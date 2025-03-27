from bittensor import Balance
from constants import BLOCK_SECONDS, INTERVAL_SECONDS

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
    subnets = subtensor.get_all_subnets_info(block)
    total_root_divs = 0
    total_root_yield = 0
    total_interval_blocks = 0

    # Calculate total number of blocks to process across all subnets
    total_blocks = 0
    for subnet in subnets:
        tempo = subnet.tempo
        interval_blocks = calculate_interval_blocks(tempo, interval)
        epoch_block = block - subnet.blocks_since_epoch
        total_blocks += (epoch_block - (block - interval_blocks)) // (tempo + 1) + 1

    progress.update(task, total=total_blocks)
    blocks_processed = 0

    for subnet in subnets:
        progress.console.print(f"\nprocessing subnet {subnet.netuid}")

        netuid = subnet.netuid
        tempo = subnet.tempo
        epoch_block = block - subnet.blocks_since_epoch
        progress.console.print(f"current block: {block}, last epoch block: {epoch_block}")

        interval_blocks = calculate_interval_blocks(tempo, interval)
        total_interval_blocks += interval_blocks

        divs_sum = 0
        yield_product = 1
        while epoch_block >= block-interval_blocks:
            root_div = subtensor.query_subtensor("TaoDividendsPerSubnet", epoch_block, params=[netuid, hotkey]).value
            if (root_div == 0):
                progress.console.print(f"zero dividends on epoch block {epoch_block}, skipping...")
                epoch_block -= tempo+1
                blocks_processed += 1
                progress.update(task, completed=blocks_processed)
                continue

            stake = subtensor.query_subtensor("TotalHotkeyAlpha", epoch_block, params=[hotkey, 0]).value
            if (stake == 0):
                progress.console.print(f"zero stake on epoch block {epoch_block}, skipping...")
                epoch_block -= tempo+1
                blocks_processed += 1
                progress.update(task, completed=blocks_processed)
                continue

            epoch_yield = root_div/stake
            divs_sum += root_div
            yield_product *= (1+epoch_yield)

            progress.console.print(f"on epoch block: {epoch_block}, "
                  f"root dividends: {Balance(root_div).tao:.6f}τ, "
                  f"stake: {Balance(stake).tao:.6f}τ, "
                  f"percentage yield: {epoch_yield*100:.6f}%")
            epoch_block -= tempo+1
            blocks_processed += 1
            progress.update(task, completed=blocks_processed)

        period_yield = yield_product - 1
        progress.console.print(f"{interval} percentage yield: {period_yield*100:.6f}%")
        progress.console.print(f"{interval} dividends: {Balance(divs_sum).tao:.6f}τ")

        total_root_yield += period_yield
        total_root_divs += divs_sum

    progress.console.print(f"total {interval} percentage yield: {total_root_yield*100:.6f}%")
    progress.console.print(f"total {interval} dividends: {Balance(total_root_divs).tao:.6f}τ")

    avg_interval_seconds = total_interval_blocks/len(subnets) * BLOCK_SECONDS
    period_yield = total_root_yield
    compounding_periods = INTERVAL_SECONDS["year"]/avg_interval_seconds
    apy = calculate_apy(period_yield, compounding_periods)
    progress.console.print(f"apy: {apy:.6f}%")
    return apy, total_root_divs
