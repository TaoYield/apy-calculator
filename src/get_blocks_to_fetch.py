from constants import START_BLOCK, BLOCK_INTERVAL

MIN_VALID_BLOCK = START_BLOCK + BLOCK_INTERVAL


def get_blocks_to_fetch(current_block: int, blocks_to_fetch_count: int) -> list[int]:
    """
    This function returns a list of blocks to fetch based on the interval,
    starting from the `current_block` and going backwards up to `MIN_VALID_BLOCK` block.
    """
    blocks_to_fetch = []

    # Find the first block that is >= MIN_VALID_BLOCK and follows the BLOCK_INTERVAL pattern.
    next_block = max(
        MIN_VALID_BLOCK,
        current_block - ((current_block - START_BLOCK) % BLOCK_INTERVAL),
    )

    while (
        len(blocks_to_fetch) < blocks_to_fetch_count and next_block >= MIN_VALID_BLOCK
    ):
        blocks_to_fetch.append(next_block)
        next_block -= BLOCK_INTERVAL

    # Sort blocks in descending order (most recent first).
    return sorted(blocks_to_fetch, reverse=True)
