from constants import BLOCK_SECONDS, INTERVAL_SECONDS

# Calculates the minimum number of blocks that is a multiple of (tempo+1) 
# and is greater than or equal to the number of blocks in the given time interval.
def calculate_interval_blocks(tempo: int, interval: str):
    interval_seconds = INTERVAL_SECONDS[interval]
    interval_blocks = interval_seconds / BLOCK_SECONDS

    # Calculation must use tempo+1
    tempo += 1

    # Calculate the number of blocks to process for the given interval
    tempo_interval_blocks = interval_blocks//tempo * tempo

    if tempo_interval_blocks < interval_blocks:
        tempo_interval_blocks += tempo

    return tempo_interval_blocks

def calculate_apy(yield_value, compound_periods):
    return ((1 + yield_value) ** compound_periods - 1) * 100