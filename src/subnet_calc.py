import asyncio
from typing import Tuple, List, Dict
from constants import BLOCK_SECONDS, INTERVAL_SECONDS, REQUIRED_BLOCKS_RATIO
from bittensor import Balance, AsyncSubtensor
from apy import calculate_interval_blocks, calculate_apy

async def calculate_hotkey_subnet_apy(
    subtensor: AsyncSubtensor,
    netuid: int,
    hotkey: str,
    interval: str,
    block: int,
    progress,
    batch_size: int = 100
) -> Tuple[float, int]:
    """
    Asynchronously calculate the Annual Percentage Yield (APY) for a hotkey based on subnet dividends.

    This function queries subnet epoch events, fetches dividends and stake data concurrently using
    AsyncSubtensor, compounds yields over the specified interval, and computes the annualized yield.

    Args:
        subtensor (AsyncSubtensor): AsyncSubtensor instance for querying the Bittensor blockchain.
        netuid (int): Netuid of the subnet for APY calculation.
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
    if netuid == 0:
        raise Exception('For root network use calculate_hotkey_root_apy() instead')
    
    subnet = await subtensor.subnet(netuid, block)
    tempo = subnet.tempo
    epoch = subnet.last_step
    progress.console.print(f"current block: {block}, last epoch block: {epoch}")

    interval_blocks = calculate_interval_blocks(tempo, interval)
    actual_interval_seconds = interval_blocks * BLOCK_SECONDS

    events: List[Dict] = []
    period = tempo + 1
    while epoch >= block-interval_blocks:
        events.append({"block": epoch, "netuid": netuid, "tempo": tempo})
        epoch -= period

    # Create divs task
    divsTask = progress.add_task(f"[cyan]Fetching divs for {hotkey}", total=len(events))

    # Create divs query
    async def query_divs_with_progress(block: int, params: List) -> int:
        result = await subtensor.query_subtensor("AlphaDividendsPerSubnet", block=block, params=params)
        progress.update(divsTask, advance=1)
        return result.value
    
    # Prepare dividend tasks
    root_div_tasks = [
        lambda event=event: query_divs_with_progress(event["block"], [event["netuid"], hotkey])
        for event in events
    ]

    # Fetch dividends in batches
    divs: List[int] = []
    for i in range(0, len(root_div_tasks), batch_size):
        batch = root_div_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch])
        batch_results = [result if not isinstance(result, Exception) else -1 for result in batch_results]
        divs.extend(batch_results)


    # Create stake task
    stakeTask = progress.add_task(f"[cyan]Fetching stakes for {hotkey}", total=len(events))

    # Create stake query
    async def query_stake_with_progress(block: int, params: List) -> int:
        result = await subtensor.query_subtensor("TotalHotkeyAlpha", block=block, params=params)
        progress.update(stakeTask, advance=1)
        return result.value
    
    # Prepare stake tasks
    stake_tasks = [
        lambda event=event: query_stake_with_progress(event["block"], [hotkey, netuid])
        for event in events
    ]   

    # Fetch stakes in batches
    stakes: List[int] = []
    for i in range(0, len(stake_tasks), batch_size):
        batch = stake_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch])
        batch_results = [result if not isinstance(result, Exception) else -1 for result in batch_results]
        stakes.extend(batch_results)

    divs_sum = 0
    yield_product = 1
    skipped = 0

    for event_index, _ in enumerate(events, 0):
        subnet_div = divs[event_index]
        stake = stakes[event_index]

        # No dividends has no effect on the yield product
        if subnet_div == 0:
            continue

        # Such cases mean that the query failed or zero stake,
        # which leads to division by zero in the yield calculation.
        if subnet_div == -1 or stake == -1 or stake == 0:
            skipped += 1
            continue

        epoch_yield = subnet_div/stake
        divs_sum += subnet_div
        yield_product *= (1+epoch_yield)

    if skipped > 0:
        progress.console.print(f"[yellow]Skipped {skipped} events due to query failures or zero stake.[/yellow]")
        if len(events) - skipped < REQUIRED_BLOCKS_RATIO * len(events):
            progress.console.print(f"[yellow]Coverage is less than: {REQUIRED_BLOCKS_RATIO * 100:.6f}% can lead to inaccurate results.[/yellow]")

    period_yield = yield_product - 1
    compounding_periods = INTERVAL_SECONDS["year"]/actual_interval_seconds

    progress.console.print(f"{interval} percentage yield: {period_yield*100:.6f}%")
    progress.console.print(f"{interval} dividends: {Balance(divs_sum).tao:.6f}τ")

    apy = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"apy: {apy:.6f}%")
    return apy, divs_sum
