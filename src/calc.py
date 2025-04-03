from bittensor import Balance
from constants import BLOCK_SECONDS, INTERVAL_SECONDS

# Calculates the minimum number of blocks that is a multiple of (tempo+1) 
# and is greater than or equal to the number of blocks in the given time interval.
def calculate_interval_blocks(tempo: int, interval: str):
    interval_seconds = INTERVAL_SECONDS[interval]
    interval_blocks = interval_seconds / BLOCK_SECONDS

    # Calculation must use tempo+1
    tempo += 1

    # Calculate the number of blocks to process for the given interval
    tempo_interval_blocks = interval_blocks//tempo * tempo

    if tempo_interval_blocks < interval_blocks:
        tempo_interval_blocks += tempo

    return tempo_interval_blocks

def calculate_apy(yield_value, compound_periods):
    return ((1 + yield_value) ** compound_periods - 1) * 100

def calculate_hotkey_subnet_apy(subtensor, netuid: int, hotkey: str, interval: str, block: int, progress, task):
    if netuid == 0:
        raise Exception('For root network use calculate_hotkey_root_apy() instead')
    
    subnet = subtensor.subnet(netuid, block)
    tempo = subnet.tempo
    epoch_block = subnet.last_step
    progress.console.print(f"current block: {block}, last epoch block: {epoch_block}")

    interval_blocks = calculate_interval_blocks(tempo, interval)
    actual_interval_seconds = interval_blocks * BLOCK_SECONDS

    # Calculate total number of blocks to process
    total_blocks = (epoch_block - (block - interval_blocks)) // (tempo + 1) + 1
    progress.update(task, total=total_blocks)

    divs_sum = 0
    yield_product = 1
    blocks_processed = 0
    while epoch_block >= block-interval_blocks:
        subnet_div = subtensor.query_subtensor("AlphaDividendsPerSubnet", epoch_block, params=[netuid, hotkey]).value
        if (subnet_div == 0):
            progress.console.print(f"zero dividends on epoch block {epoch_block}, skipping...")
            epoch_block -= tempo+1
            blocks_processed += 1
            progress.update(task, completed=blocks_processed)
            continue

        stake = subtensor.query_subtensor("TotalHotkeyAlpha", epoch_block, params=[hotkey, netuid]).value
        if (stake == 0):
            progress.console.print(f"zero stake on epoch block {epoch_block}, skipping...")
            epoch_block -= tempo+1
            blocks_processed += 1
            progress.update(task, completed=blocks_processed)
            continue

        epoch_yield = subnet_div/stake
        divs_sum += subnet_div
        yield_product *= (1+epoch_yield)

        progress.console.print(f"on epoch block: {epoch_block}, "
              f"dividends: {Balance(subnet_div).tao:.6f}τ, "
              f"stake: {Balance(stake).tao:.6f}τ, "
              f"percentage yield: {epoch_yield*100:.6f}%")
        epoch_block -= tempo+1
        blocks_processed += 1
        progress.update(task, completed=blocks_processed)

    period_yield = yield_product - 1
    compounding_periods = INTERVAL_SECONDS["year"]/actual_interval_seconds

    progress.console.print(f"{interval} percentage yield: {period_yield*100:.6f}%")
    progress.console.print(f"{interval} dividends: {Balance(divs_sum).tao:.6f}τ")

    apy = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"apy: {apy:.6f}%")
    return apy, divs_sum

def calculate_hotkey_root_apy(subtensor, hotkey: str, interval: str, block: int, progress, task):
    # Determine a fixed actual interval period in blocks
    interval_seconds = INTERVAL_SECONDS[interval]
    # Use the fixed period as the number of blocks (rounding down)
    actual_interval_blocks = int(interval_seconds / BLOCK_SECONDS)
    actual_interval_seconds = actual_interval_blocks * BLOCK_SECONDS
    start_block = block - actual_interval_blocks

    # Get information for all subnets at the current block
    subnets = subtensor.get_all_subnets_info(block)

    # Gather all epoch events for each subnet within the actual interval.
    # For each subnet, the last epoch event is determined by:
    #     last_epoch_block = block - subnet.blocks_since_epoch
    # and subsequent epochs occur every (tempo+1) blocks.
    events = []
    for subnet in subnets:
        netuid = subnet.netuid
        tempo = subnet.tempo
        period = tempo + 1
        last_epoch_block = block - subnet.blocks_since_epoch
        epoch = last_epoch_block
        while epoch >= start_block:
            events.append({
                "block": epoch,
                "netuid": netuid,
                "tempo": tempo,
            })
            epoch -= period

    # Sort events in ascending order (oldest first) so that compounding is done in time order.
    events.sort(key=lambda x: x["block"])

    # Total number of events for progress reporting
    total_events = len(events)
    progress.update(task, total=total_events)

    yield_product = 1.0
    total_root_divs = 0
    events_processed = 0

    # Group events by their block number. This way, if two or more subnets have an epoch
    # change in the same block, we sum all their yields.
    grouped_events = {}
    for event in events:
        grouped_events.setdefault(event["block"], []).append(event)
    sorted_blocks = sorted(grouped_events.keys())

    for current_block in sorted_blocks:
        block_events = grouped_events[current_block]
        block_yield_factor = 1.0

        # Process all events (epoch changes) that occurred in this block.
        for event in block_events:
            netuid = event["netuid"]

            # Query the dividends and stake at this epoch event for the given subnet.
            root_div = subtensor.query_subtensor("TaoDividendsPerSubnet", current_block, params=[netuid, hotkey]).value
            if root_div == 0:
                progress.console.print(
                    f"zero dividends on epoch block {current_block} for subnet {netuid}, skipping..."
                )
                events_processed += 1
                progress.update(task, completed=events_processed)
                continue

            stake = subtensor.query_subtensor("TotalHotkeyAlpha", current_block, params=[hotkey, 0]).value
            if stake == 0:
                progress.console.print(
                    f"zero stake on epoch block {current_block} for subnet {netuid}, skipping..."
                )
                events_processed += 1
                progress.update(task, completed=events_processed)
                continue

            # Compute the yield for this epoch event
            epoch_yield = root_div / stake
            total_root_divs += root_div
            block_yield_factor *= (1 + epoch_yield)

            progress.console.print(
                f"At epoch block {current_block}, subnet {netuid}: "
                f"root dividends: {Balance(root_div).tao:.6f}τ, "
                f"stake: {Balance(stake).tao:.6f}τ, "
                f"percentage yield: {epoch_yield*100:.6f}%"
            )
            events_processed += 1
            progress.update(task, completed=events_processed)

        # Compound the yield from all events that occurred in this block.
        yield_product *= block_yield_factor

    period_yield = yield_product - 1
    progress.console.print(f"Total {interval} percentage yield: {period_yield*100:.6f}%")
    progress.console.print(f"Total {interval} dividends: {Balance(total_root_divs).tao:.6f}τ")

    # Determine the number of compounding periods per year based on the actual interval seconds.
    compounding_periods = INTERVAL_SECONDS["year"] / actual_interval_seconds
    apy = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"APY: {apy:.6f}%")
    return apy, total_root_divs

