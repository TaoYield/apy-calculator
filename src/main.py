import sys
import bittensor
from rich.progress import Progress, TimeElapsedColumn, SpinnerColumn
from rich.panel import Panel

from utils.print import print_results
from utils.env import parse_env_data
from calc import (
    calculate_hotkey_subnet_apy,
    calculate_hotkey_root_apy,
)
from constants import INTERVAL_SECONDS

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

def main():
    # Parse command line arguments
    netuid, hotkey, interval, block = parse_args()

    # Get node URL from environment
    [node_url] = parse_env_data()
    subtensor = bittensor.Subtensor(node_url)
    
    if block is None:
        block = subtensor.block

    with Progress(
        SpinnerColumn(), *Progress.get_default_columns(), TimeElapsedColumn()
    ) as progress:
        progress.console.print(
            Panel(f"Hotkey: [b][i][magenta]{hotkey}[/magenta][/i][/b]", width=60)
        )

        try:
            if netuid > 0:
                # Calculate subnet APY
                progress.console.print(f"\nCalculating APY for subnet {netuid}")
                task = progress.add_task(f"[cyan]Processing subnet {netuid}", total=100)
                apy, divs = calculate_hotkey_subnet_apy(subtensor, netuid, hotkey, interval, block, progress, task)
                results = [[apy, divs]]
            else:
                # Calculate root network APY
                progress.console.print("\nCalculating root network APY")
                task = progress.add_task("[cyan]Processing root network", total=100)
                apy, divs = calculate_hotkey_root_apy(subtensor, hotkey, interval, block, progress, task)
                results = [[apy, divs]]
                
        except Exception as e:
            progress.console.print(f"Error calculating APY: {str(e)}")
            sys.exit(1)
    
    print_results(results, netuid, hotkey)

if __name__ == "__main__":
    main()
