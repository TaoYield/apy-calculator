import os

from constants import BLOCKS_TO_FETCH_COUNT

OTF_ARCHIVE_NODE = "wss://archive.chain.opentensor.ai:443"
TAO5_HOTKEY = "5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp"
DEFAULT_PERIOD = "24h"


def parse_env_data():
    node = os.getenv("NODE") or OTF_ARCHIVE_NODE
    hotkey = os.getenv("HOTKEY") or TAO5_HOTKEY
    period = os.getenv("PERIOD") or DEFAULT_PERIOD

    blocks_to_fetch_count = BLOCKS_TO_FETCH_COUNT.get(period)

    if not blocks_to_fetch_count:
        valid_periods = ", ".join(BLOCKS_TO_FETCH_COUNT.keys())
        raise ValueError(
            f"Invalid period: '{period}'. Period must be one of: {valid_periods}"
        )

    return [node, hotkey, blocks_to_fetch_count]
