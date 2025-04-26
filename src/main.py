import sys
import asyncio
from rich.progress import Progress, TimeElapsedColumn, SpinnerColumn
from rich.panel import Panel

from utils.print import print_results
from utils.env import parse_env_data
from constants import INTERVAL_SECONDS
from subnet_calc import calculate_hotkey_subnet_apy
from root_calc import calculate_hotkey_root_apy
from bittensor import AsyncSubtensor

VALID_INTERVALS = set(INTERVAL_SECONDS.keys())

def parse_args():
    """Parse and validate command line arguments."""
    if len(sys.argv) < 4:
        print("Usage: python main.py <netuid> <hotkey> <interval> [block]")
        print("  <netuid> - netuid index (0 is root)")
        print("  <hotkey> - delegate hotkey in ss58 format")
        print("  <interval> - one of: " + ", ".join(f'"{x}"' for x in VALID_INTERVALS))
        print("  [block] - optional block number to calculate APY from")
        print("Example: python main.py 37 5CsvRJXuR955WojnGMdok1hbhffZyB4N5ocrv82f3p5A2zVp 24h")
        sys.exit(1)

    try:
        netuid = int(sys.argv[1])
        hotkey = sys.argv[2]
        interval = sys.argv[3]
        block = None if len(sys.argv) <= 4 else int(sys.argv[4])

        if interval not in VALID_INTERVALS:
            print(f"Error: Invalid interval '{interval}'. Must be one of: {', '.join(VALID_INTERVALS)}")
            sys.exit(1)

        return netuid, hotkey, interval, block
    except ValueError as e:
        print(f"Error: Invalid argument format - {str(e)}")
        sys.exit(1)

async def main():
    # Parse command line arguments
    netuid, hotkey, interval, block = parse_args()

    # Get node URL from environment
    [node_url, batch_size, use_inherited_filter, no_filters] = parse_env_data()

    async with AsyncSubtensor(node_url) as subtensor:
        if block is None:
            block = await subtensor.block

        with Progress(
            SpinnerColumn(), *Progress.get_default_columns(), TimeElapsedColumn()
        ) as progress:
            if use_inherited_filter:
                progress.console.print(f"\n[yellow]WARNING: Inherited filter is used, this option could take more time. [/yellow]")
            if batch_size > 100:
                progress.console.print(f"\n[yellow]WARNING: Batch size: {batch_size}, this may cause event loop to be hanging. [/yellow]")
            progress.console.print(
                Panel(f"Hotkey: [b][i][magenta]{hotkey}[/magenta][/i][/b]", width=60)
            )

            try:
                if netuid > 0:
                    # Calculate subnet APY
                    progress.console.print(f"\nCalculating APY for subnet {netuid}")
                    apy, divs = await calculate_hotkey_subnet_apy(subtensor, netuid, hotkey, interval, block, progress, batch_size, use_inherited_filter, no_filters)
                    results = [[apy, divs]]
                else:
                    # Calculate root network APY
                    progress.console.print("\nCalculating root network APY")
                    apy, divs = await calculate_hotkey_root_apy(subtensor, hotkey, interval, block, progress, batch_size, no_filters)
                    results = [[apy, divs]]
                    
            except Exception as e:
                progress.console.print(f"Error calculating APY: {str(e)}")
                sys.exit(1)
    
        print_results(results, netuid, hotkey)

# Run the main function
if __name__ == "__main__":
    asyncio.run(main())