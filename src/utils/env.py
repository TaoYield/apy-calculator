import os

OTF_ARCHIVE_NODE = "wss://archive.chain.opentensor.ai:443"

def parse_env_data():
    node = os.getenv("NODE") or OTF_ARCHIVE_NODE
    batch_size = os.getenv("BATCH_SIZE") or 100
    use_inherited_filter = os.getenv("INHERITED") or False

    return [node, int(batch_size), bool(use_inherited_filter)]
