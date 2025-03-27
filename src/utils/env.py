import os

OTF_ARCHIVE_NODE = "wss://archive.chain.opentensor.ai:443"

def parse_env_data():
    node = os.getenv("NODE") or OTF_ARCHIVE_NODE

    return [node]
