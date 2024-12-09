import math

from rich.console import Console
from rich.table import Table


def format_float(value: float, decimals: int = 2, floor: bool = True) -> str:
    """
    Formats a float value with specified number of decimal places.
    Args:
        value: The float value to format
        decimals: Number of decimal places to keep (default: 2)
        floor: If True, uses math.floor for rounding, otherwise uses regular rounding (default: True)
    """
    if floor:
        return f"{math.floor(value * (10 ** decimals)) / (10 ** decimals):.{decimals}f}"
    return f"{round(value, decimals):.{decimals}f}"


def print_results(results: list[float | None, float | None], effective_take: float):
    table = Table(caption_style="white i")

    table.add_column("Period", justify="right", style="blue")
    table.add_column("APY", justify="center", style="magenta")
    table.add_column("Yield", justify="right", style="green")

    periods = ["1 hour", "24 hours", "7 days", "30 days"]

    for i in range(len(results)):
        [apy, emission] = results[i]

        formatted_apy = (
            "N/A"
            if apy == None
            else (f"{format_float(apy, 2)}%" if apy >= 0.01 else "<0.01%")
        )

        formatted_period_emission = (
            "N/A"
            if emission == None
            else (
                f"{format_float(emission / 1e9)}𝞃"
                if emission / 1e9 >= 0.01
                else "<0.01𝞃"
            )
        )

        table.add_row(
            periods[i],
            formatted_apy,
            formatted_period_emission,
        )

    table.caption = f"Effective Take Rate: [yellow]{format_float(effective_take * 100, 1, floor=False)}%[/yellow]"

    console = Console()

    console.print("\n")

    if len(results) > 0:
        console.print(table)
    else:
        console.print("[i]No emissions found for this hotkey...[/i]")
