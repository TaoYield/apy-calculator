import bittensor

from rich.progress import Progress, TimeElapsedColumn, SpinnerColumn
from rich.panel import Panel

from constants import DIVIDERS
from get_hotkey_data import get_hotkey_data
from get_blocks_to_fetch import get_blocks_to_fetch
from utils.env import parse_env_data
from utils.print import print_results

[node, hotkey, blocks_to_fetch_count] = parse_env_data()

subtensor = bittensor.Subtensor(node)

current_block = subtensor.get_current_block()

blocks = get_blocks_to_fetch(current_block, blocks_to_fetch_count)

yield_sum = 0
emission_sum = 0
blocks_count = 0
invalid_blocks = 0
results = []

with Progress(
    SpinnerColumn(), *Progress.get_default_columns(), TimeElapsedColumn()
) as progress:
    task = progress.add_task("[cyan]Fetching data", total=len(blocks))

    progress.console.print(
        Panel(f"Hotkey: [b][i][magenta]{hotkey}[/magenta][/i][/b]", width=60)
    )

    for block in blocks:
        blocks_count += 1
        progress.console.print(
            f"Processing block [blue]{block}[/blue] [{blocks_count}/{len(blocks)}]"
        )

        data = get_hotkey_data(subtensor, hotkey, block)
        if data is not None:
            hotkey_yield, hotkey_emission = data
            yield_sum += hotkey_yield
            emission_sum += hotkey_emission
        else:
            invalid_blocks += 1
            progress.console.print("\tNo data found for the hotkey in this block")

        progress.advance(task)

        # Check if we've reached the required block count. If so, calculate APY for the given period.
        for number_of_blocks_required_for_period, divider in list(DIVIDERS.items()):
            if blocks_count == number_of_blocks_required_for_period:
                # If a given validator wasn't active for at least 80% of the block intervals in a given period, we skip the calculations for that period.
                valid_blocks = blocks_count - invalid_blocks
                if valid_blocks / number_of_blocks_required_for_period < 0.8:
                    results.append([None, None])
                    continue

                daily_yield = yield_sum / divider

                # Compounding occurs once per day, thus 365 days in a year.
                compounding_periods = 365
                apy = ((1 + daily_yield) ** compounding_periods - 1) * 100

                results.append([apy, emission_sum])

                break

print_results(results)
