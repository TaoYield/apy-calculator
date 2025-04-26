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
    batch_size: int = 100
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

    # Create divs task
    divsTask = progress.add_task(f"[cyan]Fetching divs for {hotkey}", total=len(events))

    # Create divs query
    async def query_divs_with_progress(block: int, params: List) -> int:
        result = await subtensor.query_subtensor("TaoDividendsPerSubnet", block=block, params=params)
        progress.update(divsTask, advance=1)
        return result.value
    
    # Prepare dividend tasks
    root_div_tasks = [
        lambda event=event: query_divs_with_progress(event["block"], [event["netuid"], hotkey])
        for event in events
    ]

    # Fetch dividends in batches
    root_divs: List[int] = []
    for i in range(0, len(root_div_tasks), batch_size):
        batch = root_div_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch], return_exceptions=True)
        batch_results = [result if not isinstance(result, Exception) else -1 for result in batch_results]
        root_divs.extend(batch_results)
        
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

    for event_index, _ in enumerate(events, 0):
        root_div = root_divs[event_index]
        stake = stakes[event_index]

        # No dividends has no effect on the yield product.
        if root_div == 0:
            continue

        # Such cases mean that the query failed or zero stake,
        # which leads to division by zero in the yield calculation.
        # also here we filter validators with stake less than 4k TAO.
        if root_div == -1 or stake == -1 or stake < 4000:
            skipped += 1
            continue

        epoch_yield = root_div / stake
        total_root_divs += root_div
        yield_product *= (1 + epoch_yield)

    if skipped > 0:
        progress.console.print(f"[yellow]Skipped {skipped} events due to query failures or zero stake.[/yellow]")
        if len(events) - skipped < REQUIRED_BLOCKS_RATIO * len(events):
            progress.console.print(f"[yellow]Coverage is less than: {REQUIRED_BLOCKS_RATIO * 100:.6f}% can lead to inaccurate results.[/yellow]")

    # Calculate period yield and APYz
    period_yield = yield_product - 1
    progress.console.print(f"Total {interval} yield: {period_yield * 100:.6f}%")
    progress.console.print(f"Total {interval} dividends: {Balance(total_root_divs).tao:.6f}τ")

    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds
    apy = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"APY: {apy:.6f}%")

    return apy, total_root_divs