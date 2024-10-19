import math

from rich.console import Console
from rich.table import Table


def format_float(value: float) -> str:
    """
    This removes all decimals after the 2nd one, i.e., 12.345 -> 12.34
    """
    return f"{math.floor(value * 100) / 100:.2f}"


def print_results(results: list[float | None, float | None]):
    table = Table()

    table.add_column("Period", justify="right", style="blue")
    table.add_column("APY", justify="center", style="magenta")
    table.add_column("Yield", justify="right", style="green")

    periods = ["1 hour", "24 hours", "7 days", "30 days"]

    for i in range(len(results)):
        [apy, emission] = results[i]

        formatted_apy = (
            "N/A"
            if apy == None
            else (f"{format_float(apy)}%" if apy >= 0.01 else "<0.01%")
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

    console = Console()

    if len(results) > 0:
        console.print(table)
    else:
        console.print("[i]No emissions found for this hotkey...[/i]")
