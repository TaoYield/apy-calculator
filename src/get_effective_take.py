import bittensor
from bittensor.utils import u64_normalized_float, u16_normalized_float
from rich.progress import Progress, TaskID


def get_validating_emission(
    subtensor: bittensor.Subtensor, hotkey_uid: int, netuid: int, block: int
):
    emission = subtensor.query_subtensor("Emission", block, [netuid])
    dividends = subtensor.query_subtensor("Dividends", block, [netuid])
    incentives = subtensor.query_subtensor("Incentive", block, [netuid])

    emission_sum = sum(e.value for e in emission)

    dividends_incentives_sum = sum(d.value for d in dividends) + sum(
        i.value for i in incentives
    )

    dividend = dividends[hotkey_uid].value / dividends_incentives_sum

    validating_emission = dividend * emission_sum

    tempo = subtensor.get_subnet_hyperparameters(netuid, block).tempo

    adjusted_validating_emission = (validating_emission / tempo) * 360

    return adjusted_validating_emission


def get_stake_for_hotkey_on_subnet(
    subtensor: bittensor.Subtensor, hotkey: str, netuid: int, block: int
):
    initial_stake = subtensor.query_subtensor(
        "TotalHotkeyStake", block, params=[hotkey]
    ).value
    stake_to_children = 0
    stake_from_parents = 0

    parents = subtensor.query_subtensor("ParentKeys", block, params=[hotkey, netuid])
    children = subtensor.query_subtensor("ChildKeys", block, params=[hotkey, netuid])

    for raw_proportion, _ in children:
        proportion = u64_normalized_float(str(raw_proportion))

        stake_proportion_to_child = initial_stake * proportion
        stake_to_children += stake_proportion_to_child

    for raw_proportion, raw_parent_hotkey in parents:
        proportion = u64_normalized_float(str(raw_proportion))
        parent_hotkey = str(raw_parent_hotkey)

        parent_stake = subtensor.query_subtensor(
            "TotalHotkeyStake", block, params=[parent_hotkey]
        ).value

        stake_proportion_from_parent = parent_stake * proportion
        stake_from_parents += stake_proportion_from_parent

    finalized_stake = initial_stake - stake_to_children + stake_from_parents

    max_stake = subtensor.query_subtensor(
        "NetworkMaxStake", block, params=[netuid]
    ).value

    return min(finalized_stake, max_stake)


def get_parent_keys_validating_emission(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    netuid: int,
    validating_emission: float,
    block: int,
):
    total_validating_emission = 0

    total_hotkey_stake = get_stake_for_hotkey_on_subnet(
        subtensor, hotkey, netuid, block
    )

    if total_hotkey_stake == 0:
        return total_validating_emission

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

        parent_validating_emission = proportion_from_parent * validating_emission

        total_validating_emission += parent_validating_emission

    return total_validating_emission


def get_child_keys_validating_emission_and_fees(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    netuid: int,
    block: int,
):
    total_validating_emission = 0
    fees = 0

    parent_stake = subtensor.query_subtensor(
        "TotalHotkeyStake", block, params=[hotkey]
    ).value
    child_keys = subtensor.query_subtensor("ChildKeys", block, params=[hotkey, netuid])
    for raw_proportion, raw_child_hotkey in child_keys:
        proportion = u64_normalized_float(str(raw_proportion))
        child_hotkey = str(raw_child_hotkey)

        child_hotkey_uid = subtensor.get_uid_for_hotkey_on_subnet(
            child_hotkey, netuid, block
        )

        if child_hotkey_uid is None:
            continue

        total_child_stake = get_stake_for_hotkey_on_subnet(
            subtensor, child_hotkey, netuid, block
        )
        if total_child_stake == 0:
            continue

        stake_from_parent = parent_stake * proportion
        proportion_from_parent = stake_from_parent / total_child_stake

        # ---- Validating Emission ----
        child_validating_emission = get_validating_emission(
            subtensor, child_hotkey_uid, netuid, block
        )

        child_validating_emission_take = (
            proportion_from_parent * child_validating_emission
        )
        total_validating_emission += child_validating_emission_take

        # ---- Fees ----
        child_key_take = u16_normalized_float(
            subtensor.query_subtensor(
                "ChildkeyTake", block, params=[child_hotkey, netuid]
            ).value
        )

        fee = proportion_from_parent * child_validating_emission * child_key_take
        fees += fee

    return total_validating_emission, fees


def get_effective_take(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    netuids: list[int],
    block: int,
    progress: Progress,
    task: TaskID,
):
    total_validating_emission = 0
    total_parent_keys_validating_emission = 0
    total_child_keys_validating_emission = 0
    total_child_keys_fees = 0

    take = u16_normalized_float(
        subtensor.query_subtensor("Delegates", block, params=[hotkey]).value
    )

    for netuid in netuids:
        hotkey_uid = subtensor.get_uid_for_hotkey_on_subnet(hotkey, netuid, block)

        if hotkey_uid:
            validating_emission = get_validating_emission(
                subtensor, hotkey_uid, netuid, block
            )
            total_validating_emission += validating_emission

            parent_keys_validating_emission = get_parent_keys_validating_emission(
                subtensor, hotkey, netuid, validating_emission, block
            )
            total_parent_keys_validating_emission += parent_keys_validating_emission

        child_keys_validating_emission, child_keys_fees = (
            get_child_keys_validating_emission_and_fees(
                subtensor, hotkey, netuid, block
            )
        )
        total_child_keys_validating_emission += child_keys_validating_emission
        total_child_keys_fees += child_keys_fees

        progress.advance(task)

    denominator = (
        total_validating_emission
        - total_parent_keys_validating_emission
        + total_child_keys_validating_emission
    )
    if denominator == 0:
        effective_take = 0
    else:
        effective_take = (
            take
            * (
                total_validating_emission
                - total_parent_keys_validating_emission
                + total_child_keys_validating_emission
                - total_child_keys_fees
            )
            + total_child_keys_fees
        ) / denominator

        # Get rid of negative zero
        effective_take = abs(effective_take) if effective_take == 0 else effective_take

    return float(effective_take)
