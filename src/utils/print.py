import math

from rich.console import Console
from rich.table import Table


def format_float(value: float, decimals: int, floor: bool = True) -> str:
    """
    Formats a float value with specified number of decimal places.
    Args:
        value: The float value to format
        decimals: Number of decimal places to keep (default: 2)
        floor: If True, uses math.floor for rounding, otherwise uses regular rounding (default: True)
    """
    if floor:
        return f"{value:.{decimals}f}"
    return f"{value:.{decimals}f}"


def print_results(results: list[list[float | None, float | None]], netuid: int, hotkey: str):
    if not results or not results[0]:
        console = Console()
        console.print("[i]No data found for this hotkey...[/i]")
        return

    [apy, divs] = results[0]
    
    table = Table(caption_style="white i")
    table.add_column("Metric", justify="right", style="blue")
    table.add_column("Value", justify="right", style="magenta")

    # Format subnet
    subnet = "Root Network" if netuid == 0 else f"Subnet {netuid}"

    # Format APY
    formatted_apy = (
        "N/A"
        if apy is None
        else (f"{format_float(apy, 2)}%" if apy >= 0.01 else "<0.01%")
    )

    # Format dividends
    formatted_divs = (
        "N/A"
        if divs is None
        else (
            f"{format_float(divs, 6)}𝞃"
            if divs >= 0.000001
            else "<0.000001𝞃"
        )
    )

    table.add_row("Subnet", subnet)
    table.add_row("Hotkey", hotkey)
    table.add_row("APY", formatted_apy)
    table.add_row("Dividends", formatted_divs)

    console = Console()
    console.print("\n")
    console.print(table)
