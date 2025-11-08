import asyncio
from typing import Tuple, List, Dict
from constants import BLOCK_SECONDS, INTERVAL_SECONDS, REQUIRED_BLOCKS_RATIO
from bittensor import Balance, AsyncSubtensor
from apy import calculate_apy

async def calculate_hotkey_root_apy(
    subtensor: AsyncSubtensor,
    hotkey: str,
    interval: str,
    block: int,
    progress,
    batch_size: int = 100,
    no_filters: bool = False,
) -> Tuple[float, int]:
    """
    Asynchronously calculate the Annual Percentage Yield (APY) for a hotkey based on root dividends.

    This function queries subnet epoch events, fetches dividends and stake data concurrently using
    AsyncSubtensor, compounds yields over the specified interval, and computes the annualized yield.
    Progress is tracked during data retrieval from the subtensor, not during yield computation.

    Args:
        subtensor (AsyncSubtensor): AsyncSubtensor instance for querying the Bittensor blockchain.
        hotkey (str): Hotkey identifier for APY calculation.
        interval (str): Time interval (e.g., "1h", "24h", "7d", "30d") for yield calculation.
        block (int): Ending block number for the calculation.
        progress: Rich Progress object for tracking and logging progress.
        batch_size (int): Number of tasks to process concurrently.
    Returns:
        Tuple[float, int]: (apy, total_root_divs) where:
            - apy: Annualized percentage yield as a float.
            - total_root_divs: Total dividends earned in rao (smallest unit) as an integer.
    """
    # Calculate the interval in blocks
    interval_seconds = INTERVAL_SECONDS[interval]
    actual_interval_blocks = int(interval_seconds / BLOCK_SECONDS)
    actual_interval_seconds = actual_interval_blocks * BLOCK_SECONDS
    start_block = block - actual_interval_blocks

    # Fetch subnet info asynchronously
    subnets = await subtensor.get_all_subnets_info(block=block)

    # Build list of epoch events across all subnets
    events: List[Dict] = []
    for subnet in subnets:
        netuid = subnet.netuid
        tempo = subnet.tempo
        period = tempo + 1  # Epoch period in blocks
        last_epoch_block = block - subnet.blocks_since_epoch
        epoch = last_epoch_block
        while epoch >= start_block:
            events.append({"block": epoch, "netuid": netuid, "tempo": tempo})
            epoch -= period

    # Sort events for consistent processing
    events.sort(key=lambda x: (x["block"], x["netuid"]))

    # Create root claimable task
    rootClaimableTask = progress.add_task(f"[cyan]Fetching root claimable entries for {hotkey}", total=len(events))

    # Create root claimable query
    async def query_root_claimable_with_progress(block: int, params: List) -> int:
        result = await subtensor.query_subtensor("RootClaimable", block=block, params=params)
        progress.update(rootClaimableTask, advance=1)
        return result.value
    
    # Prepare root claimable query
    root_claimable_tasks = [
        lambda event=event: query_root_claimable_with_progress(event["block"], [hotkey])
        for event in events
    ]   

    # Fetch root claimable entries in batches
    root_claimable_entries: List[int] = []
    for i in range(0, len(root_claimable_tasks), batch_size):
        batch = root_claimable_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        batch_results = [result if not isinstance(result, Exception) else -1 for result in batch_results]
        root_claimable_entries.extend(batch_results)

    # Create stake task
    stakeTask = progress.add_task(f"[cyan]Fetching stakes for {hotkey}", total=len(events))

    # Create stake query
    async def query_stake_with_progress(block: int, params: List) -> int:
        result = await subtensor.query_subtensor("TotalHotkeyAlpha", block=block, params=params)
        progress.update(stakeTask, advance=1)
        return result.value
    
    # Prepare stake tasks
    stake_tasks = [
        lambda event=event: query_stake_with_progress(event["block"], [hotkey, 0])
        for event in events
    ]   

    # Fetch stakes in batches
    stakes: List[int] = []
    for i in range(0, len(stake_tasks), batch_size):
        batch = stake_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        batch_results = [result if not isinstance(result, Exception) else -1 for result in batch_results]
        stakes.extend(batch_results)

    # Process results and compute compounded yield (no progress updates here)
    yield_product = 1.0
    total_root_divs = 0
    skipped = 0

    for event_index, event in enumerate(events, 0):
        # Extract the rao value from the structure: entry[0] is a tuple of (netuid, {'bits': value}) pairs
        if root_claimable_entries[event_index] == -1:
            root_div = -1
        else:
            entry = root_claimable_entries[event_index]
            # entry[0] is a tuple: ((netuid1, {'bits': val1}), (netuid2, {'bits': val2}), ...)
            # Find the tuple where the first element matches our netuid
            netuid = event["netuid"]
            netuid_tuple = next((item for item in entry[0] if item[0] == netuid), None)
            
            if netuid_tuple is not None:
                # netuid_tuple is (netuid, {'bits': value}), so get the dict and extract 'bits'
                root_div = netuid_tuple[1]["bits"]
            else:
                # Netuid not found in the entry
                root_div = 0
        
        stake = stakes[event_index]

        # No dividends has no effect on the yield product.
        if root_div == 0:
            continue

        # Such cases mean that the query failed or stake is zero (zero division).
        if root_div == -1 or stake == -1 or stake == 0:
            skipped += 1
            continue

        # Here we filter validators with stake less than 4k TAO.
        if not no_filters and stake < 4000:
            skipped += 1
            continue

        epoch_yield = root_div / stake
        total_root_divs += root_div
        yield_product *= (1 + epoch_yield)

    if skipped > 0:
        progress.console.print(f"[yellow]Skipped {skipped} events due to query failures or applied filters.[/yellow]")
        if len(events) - skipped < REQUIRED_BLOCKS_RATIO * len(events):
            progress.console.print(f"[yellow]Coverage is less than: {REQUIRED_BLOCKS_RATIO * 100:.6f}% can lead to inaccurate results.[/yellow]")

    # Calculate period yield and APYz
    period_yield = yield_product - 1
    progress.console.print(f"Total {interval} yield: {period_yield * 100:.6f}%")
    progress.console.print(f"Total {interval} dividends: {Balance(total_root_divs).tao:.6f}τ")

    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds
    apy = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"APY: {apy:.6f}%")

    return apy, Balance(total_root_divs).tao