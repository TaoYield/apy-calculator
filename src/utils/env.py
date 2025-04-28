import os

OTF_ARCHIVE_NODE = "wss://archive.chain.opentensor.ai:443"

def parse_env_data():
    node = os.getenv("NODE") or OTF_ARCHIVE_NODE
    batch_size = os.getenv("BATCH_SIZE") or 100
    use_inherited_filter = os.getenv("INHERITED", 'False').lower() in ('true', '1', 't') or False
    no_filters = os.getenv("NO_FILTERS", 'False').lower() in ('true', '1', 't')


    return [node, int(batch_size), bool(use_inherited_filter), bool(no_filters)]
