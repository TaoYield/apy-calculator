import bittensor
from bittensor.utils import u64_normalized_float, u16_normalized_float
from rich.progress import Progress, TaskID


def get_parent_keys_dividends(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    netuid: int,
    dividends: float,
    block: int,
):
    total_dividends = 0

    child_key_take = u16_normalized_float(
        subtensor.query_subtensor("ChildkeyTake", block, params=[hotkey, netuid]).value
    )

    total_hotkey_stake = subtensor.query_subtensor(
        "TotalHotkeyStake", block, params=[hotkey]
    ).value

    if total_hotkey_stake == 0:
        return total_dividends

    parent_keys = subtensor.query_subtensor(
        "ParentKeys", block, params=[hotkey, netuid]
    )
    for raw_proportion, raw_parent_hotkey in parent_keys:
        proportion = u64_normalized_float(str(raw_proportion))
        parent_hotkey = str(raw_parent_hotkey)

        parent_stake = subtensor.query_subtensor(
            "TotalHotkeyStake", block, params=[parent_hotkey]
        ).value

        stake_from_parent = parent_stake * proportion
        proportion_from_parent = stake_from_parent / total_hotkey_stake

        parent_dividends = proportion_from_parent * dividends

        child_dividends_take = child_key_take * parent_dividends

        parent_dividends_take = parent_dividends - child_dividends_take

        total_dividends += parent_dividends_take

    return total_dividends


def get_child_keys_dividends_and_fees(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    metagraph: bittensor.Subtensor.metagraph,
    netuid: int,
    block: int,
):
    total_dividends = 0
    fees = 0

    parent_stake = subtensor.query_subtensor(
        "TotalHotkeyStake", block, params=[hotkey]
    ).value
    child_keys = subtensor.query_subtensor("ChildKeys", block, params=[hotkey, netuid])
    for raw_proportion, raw_child_hotkey in child_keys:
        proportion = u64_normalized_float(str(raw_proportion))
        child_hotkey = str(raw_child_hotkey)

        try:
            child_hotkey_index = metagraph.hotkeys.index(child_hotkey)
        except ValueError:
            child_hotkey_index = None

        if child_hotkey_index is None:
            continue

        total_child_stake = subtensor.query_subtensor(
            "TotalHotkeyStake", block, params=[child_hotkey]
        ).value
        if total_child_stake == 0:
            continue

        stake_from_parent = parent_stake * proportion
        proportion_from_parent = stake_from_parent / total_child_stake

        # ---- Dividends ----
        child_dividends = metagraph.dividends[child_hotkey_index]

        child_dividends_take = proportion_from_parent * child_dividends
        total_dividends += child_dividends_take

        # ---- Fees ----
        child_key_take = u16_normalized_float(
            subtensor.query_subtensor(
                "ChildkeyTake", block, params=[child_hotkey, netuid]
            ).value
        )

        fee = proportion_from_parent * child_dividends * child_key_take
        fees += fee

    return total_dividends, fees


def get_effective_take(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    netuids: list[int],
    block: int,
    progress: Progress,
    task: TaskID,
):
    total_dividends = 0
    total_parent_keys_dividends = 0
    total_child_keys_dividends = 0
    total_child_keys_fees = 0

    take = u16_normalized_float(
        subtensor.query_subtensor("Delegates", block, params=[hotkey]).value
    )

    for netuid in netuids:
        metagraph = subtensor.metagraph(netuid, block)

        hotkey_to_index = {key: idx for idx, key in enumerate(metagraph.hotkeys)}

        if hotkey in hotkey_to_index:
            hotkey_index = hotkey_to_index[hotkey]
            dividends = metagraph.dividends[hotkey_index]
            total_dividends += dividends

            parent_keys_dividends = get_parent_keys_dividends(
                subtensor, hotkey, netuid, dividends, block
            )
            total_parent_keys_dividends += parent_keys_dividends

        child_keys_dividends, child_keys_fees = get_child_keys_dividends_and_fees(
            subtensor, hotkey, metagraph, netuid, block
        )
        total_child_keys_dividends += child_keys_dividends
        total_child_keys_fees += child_keys_fees

        progress.advance(task)

    denominator = (
        total_dividends - total_parent_keys_dividends + total_child_keys_dividends
    )
    if denominator == 0:
        effective_take = 0
    else:
        effective_take = (
            take
            * (
                total_dividends
                - total_parent_keys_dividends
                + total_child_keys_dividends
                - total_child_keys_fees
            )
            + total_child_keys_fees
        ) / denominator

        # Get rid of negative zero
        effective_take = abs(effective_take) if effective_take == 0 else effective_take

    return float(effective_take)
