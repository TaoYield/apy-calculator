# Average block time in seconds.
BLOCK_SECONDS = 12

# Time intervals for APY calculations
INTERVAL_SECONDS = {
    # Since almost all subnets epochs are 360 blocks.
    "1h": 72 * 60,
    "24h": 60 * 60 * 24,
    "7d": 60 * 60 * 24 * 7,
    "30d": 60 * 60 * 24 * 30,
    "year": 60 * 60 * 24 * 365,
}

REQUIRED_BLOCKS_RATIO = 0.9
# --- Root basket (runtime spec 441) ---

# Shortest measured span we will annualize (~10 min at 12s blocks). Below this the per-block
# rate is dominated by sampling noise and the compounding exponent explodes.
BASKET_MIN_SPAN_BLOCKS = 50

# Cap on basket samples per window. BasketDepositedTao is cumulative, so income between two
# samples is captured in full by their delta -- sampling on a cadence loses nothing and keeps a
# 30d window (~216k blocks) from issuing tens of thousands of historical state reads.
BASKET_MAX_SAMPLES = 200

# Root stake floor (TAO) below which a sample is treated as noise.
ROOT_MIN_STAKE_TAO = 4000

RAO_PER_TAO = 10**9
