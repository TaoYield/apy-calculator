"""
CLI entry for the snapshot-mode root APY.

Usage:
    python main_snapshot.py <hotkey> [block]

Mirrors main.py's ergonomics but only for root APY (subnet APY isn't in scope
here yet). Add [block] to compute at a specific historical block instead of head.
Use --verbose to see per-subnet breakdown.

Env:
    NODE   substrate RPC url (default: OTF archive; use $SUBSTRATE_RPC_URL when
           reproducing what btguru sees)

Compare against main.py at the SAME block/hotkey to see how much of the
gap between two nearby windows was round-robin jitter.
"""
import asyncio
import os
import sys

from bittensor import AsyncSubtensor
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TimeElapsedColumn

from utils.env import parse_env_data
from utils.print import print_results
from root_calc_snapshot import calculate_hotkey_root_apy_snapshot


def parse_args():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage: python main_snapshot.py <hotkey> [block] [--verbose]")
        print("Example: python main_snapshot.py 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp")
        print("         python main_snapshot.py 5CsvRJXuR955... 8791000 --verbose")
        sys.exit(1)
    hotkey = sys.argv[1]
    block = None
    verbose = False
    for arg in sys.argv[2:]:
        if arg == "--verbose":
            verbose = True
        else:
            try:
                block = int(arg)
            except ValueError:
                print(f"Bad argument: {arg}")
                sys.exit(1)
    return hotkey, block, verbose


async def main():
    hotkey, block, verbose = parse_args()
    node_url, _batch_size, _use_inh, no_filters = parse_env_data()

    async with AsyncSubtensor(node_url) as subtensor:
        if block is None:
            block = await subtensor.block

        with Progress(SpinnerColumn(), *Progress.get_default_columns(), TimeElapsedColumn()) as progress:
            progress.console.print(
                Panel(
                    f"Hotkey: [b][i][magenta]{hotkey}[/magenta][/i][/b]\n"
                    f"Mode:   [b]snapshot[/b] (no sliding window)\n"
                    f"Block:  {block}",
                    width=80,
                )
            )
            progress.console.print("\nCalculating snapshot root APY")

            apy, total_annual_tao, contribs = await calculate_hotkey_root_apy_snapshot(
                subtensor, hotkey, block, no_filters=no_filters, verbose=verbose,
            )

        print_results([[apy, total_annual_tao]], 0, hotkey)


if __name__ == "__main__":
    asyncio.run(main())
