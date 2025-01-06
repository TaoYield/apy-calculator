import bittensor

from bittensor.utils import u16_normalized_float
from constants import BLOCK_INTERVAL


def get_hotkey_data(
    subtensor: bittensor.Subtensor,
    hotkey: str,
    block: int,
    should_use_stake_from_end_block: bool,
):
    untouchable_emission = subtensor.query_subtensor(
        "PendingdHotkeyEmissionUntouchable", block, params=[hotkey]
    ).value
    raw_emission = subtensor.query_subtensor(
        "PendingdHotkeyEmission", block, params=[hotkey]
    ).value

    emission = raw_emission - untouchable_emission

    # It is important to get the stake at the end of the interval for the realtime calculations.
    start_block = block - BLOCK_INTERVAL
    stake = subtensor.query_subtensor(
        "TotalHotkeyStake",
        block if should_use_stake_from_end_block else start_block,
        params=[hotkey],
    ).value

    # We're not interested in hotkeys with less than 4k TAO staked.
    if stake / 1e9 < 4000:
        return None

    take = u16_normalized_float(
        subtensor.query_subtensor("Delegates", block, params=[hotkey]).value
    )

    untouchable_emission_start_block = subtensor.query_subtensor(
        "PendingdHotkeyEmissionUntouchable", start_block, params=[hotkey]
    ).value
    raw_emission_start_block = subtensor.query_subtensor(
        "PendingdHotkeyEmission", start_block, params=[hotkey]
    ).value

    emission_start_block = raw_emission_start_block - untouchable_emission_start_block

    emission_before_take = emission - emission_start_block

    last_drain_block = int(
        subtensor.query_subtensor(
            "LastHotkeyEmissionDrain", block, params=[hotkey]
        ).value
    )

    # If the last drain block is within the interval, we need to correct the emission.
    if last_drain_block <= block and last_drain_block > start_block:
        block_before_drain = last_drain_block - 1

        untouchable_emission_before_drain = subtensor.query_subtensor(
            "PendingdHotkeyEmissionUntouchable", block_before_drain, params=[hotkey]
        ).value
        raw_emission_before_drain = subtensor.query_subtensor(
            "PendingdHotkeyEmission", block_before_drain, params=[hotkey]
        ).value

        emission_before_drain = (
            raw_emission_before_drain - untouchable_emission_before_drain
        )

        emission_before_take = emission_before_drain - emission_start_block + emission

    # We're getting rid of everything after the decimal point.
    emission_after_take = int(emission_before_take * (1 - take))

    hotkey_yield = emission_after_take / stake

    return [hotkey_yield, emission_after_take]
