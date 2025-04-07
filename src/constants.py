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