import asyncio
from typing import Tuple, List, Dict
from constants import BLOCK_SECONDS, INTERVAL_SECONDS, REQUIRED_BLOCKS_RATIO
from bittensor import Balance, AsyncSubtensor
from apy import calculate_interval_blocks, calculate_apy
from filter import has_enough_stake
from helpers import calc_inherited_on_subnet, get_children, get_divs_for_hotkey_on_subnet, get_parents, get_stake_for_hotkey_on_subnet, get_tao_weight

async def calculate_hotkey_subnet_apy(
    subtensor: AsyncSubtensor,
    netuid: int,
    hotkey: str,
    interval: str,
    block: int,
    progress,
    batch_size: int = 100,
    use_inherited_filer: bool = False,
    no_filters: bool = False,
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
    data_task = progress.add_task(f"[cyan]Fetching data for {hotkey}", total=len(events))

    # Create blockchain query
    async def query_data_with_progress(block: int, hotkey: str, netuid: int) -> dict:
        # Main part
        tao_weight = await get_tao_weight(subtensor, block)
        subnet_stake = await get_stake_for_hotkey_on_subnet(subtensor, hotkey, netuid, block)
        root_stake = await get_stake_for_hotkey_on_subnet(subtensor, hotkey, 0, block)
        subnet_div = await get_divs_for_hotkey_on_subnet(subtensor, hotkey, netuid, block)
        
        # Inherited part
        inh_root_stake = 0
        inh_subnet_stake = 0
        if use_inherited_filer:
            parents = await get_parents(subtensor, hotkey, netuid, block)
            children = await get_children(subtensor, hotkey, netuid, block)
            inh_root_stake = await calc_inherited_on_subnet(subtensor, root_stake, 0, parents, children, block)
            inh_subnet_stake = await calc_inherited_on_subnet(subtensor, subnet_stake, netuid, parents, children, block)

        progress.update(data_task, advance=1)

        return {
            "block": block,
            "tao_weight": tao_weight,
            "subnet_div": subnet_div,
            "root_stake": root_stake,
            "subnet_stake": subnet_stake,
            "inh_root_stake": inh_root_stake,
            "inh_subnet_stake": inh_subnet_stake,
        }
    
    # Prepare stake tasks
    data_tasks = [
        lambda event=event: query_data_with_progress(event["block"], hotkey, netuid)
        for event in events
    ]

    # Fetch data in batches
    results: List[int] = []
    for i in range(0, len(data_tasks), batch_size):
        batch = data_tasks[i:i + batch_size]
        batch_results = await asyncio.gather(*[task() for task in batch])
        batch_results = [result if not isinstance(result, Exception) else -1 for result in batch_results]
        results.extend(batch_results)

    
    divs_sum = 0
    yield_product = 1
    skipped = 0

    for event_index, _ in enumerate(events, 0):
        data = results[event_index]

        # Such cases mean that the query failed.
        if data == -1:
            skipped += 1
            continue

        root_stake, subnet_stake = data["root_stake"], data["subnet_stake"]
        inh_root_stake, inh_subnet_stake = data["inh_root_stake"], data["inh_subnet_stake"]
        tao_weight = data["tao_weight"]
        subnet_div = data["subnet_div"]

        # No dividends has no effect on the yield product
        if subnet_div == 0:
            continue

        # Zero stake is skipped (zero division)
        if subnet_stake == 0:
            skipped += 1
            continue

        # Apply filter.
        if not no_filters and not has_enough_stake(root_stake, subnet_stake, inh_root_stake, inh_subnet_stake, tao_weight):
            skipped += 1
            continue

        epoch_yield = subnet_div/subnet_stake
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
