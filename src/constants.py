# Average block time in seconds.
BLOCK_TIME = 12

# We're gatheting emission data every 360 blocks, around every 72 minutes.
BLOCK_INTERVAL = 360

# Block at which the merge from the Metagraph to Pending Emissions occured.
# https://taostats.io/block/3791351
START_BLOCK = 3_791_351

# Number of blocks to fetch for each period.
BLOCKS_TO_FETCH_FOR_HOURLY_APY = 1
BLOCKS_TO_FETCH_FOR_DAILY_APY = 24 * 60 * 60 / BLOCK_TIME / BLOCK_INTERVAL
BLOCKS_TO_FETCH_FOR_WEEKLY_APY = 7 * 24 * 60 * 60 / BLOCK_TIME / BLOCK_INTERVAL
BLOCKS_TO_FETCH_FOR_MONTHLY_APY = 30 * 24 * 60 * 60 / BLOCK_TIME / BLOCK_INTERVAL

# Dividers used to calculate daily yield from the summed yield.
DIVIDERS = {
    # There are 20 intervals in a day, therefore to calculate the daily yield, we need to multiply the sum by 20.
    BLOCKS_TO_FETCH_FOR_HOURLY_APY: 1 / 20,
    BLOCKS_TO_FETCH_FOR_DAILY_APY: 1,
    BLOCKS_TO_FETCH_FOR_WEEKLY_APY: 7,
    BLOCKS_TO_FETCH_FOR_MONTHLY_APY: 30,
}

BLOCKS_TO_FETCH_COUNT = {
    "1h": BLOCKS_TO_FETCH_FOR_HOURLY_APY,
    "24h": BLOCKS_TO_FETCH_FOR_DAILY_APY,
    "7d": BLOCKS_TO_FETCH_FOR_WEEKLY_APY,
    "30d": BLOCKS_TO_FETCH_FOR_MONTHLY_APY,
}
