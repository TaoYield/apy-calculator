from bittensor import Balance
from bittensor.core.chain_data import decode_account_id
from bittensor.utils import U64_MAX

async def query_subtensor(subtensor, name, block, params=[]):
    res = await subtensor.query_subtensor(name=name, params=params, block=block)
    return getattr(res, "value", None)

async def get_children(subtensor, hotkey, netuid, block):
    resp = await query_subtensor(subtensor, "ChildKeys", block, [hotkey, netuid]) or []
    return [(float(p) / float(U64_MAX), decode_account_id(ch[0])) for p, ch in resp]

async def get_parents(subtensor, hotkey, netuid, block):
    resp = await query_subtensor(subtensor, "ParentKeys", block, [hotkey, netuid]) or []
    return [(float(p) / float(U64_MAX), decode_account_id(ph[0])) for p, ph in resp]

async def get_stake_for_hotkey_on_subnet(subtensor, hotkey, netuid, block):
    raw = await query_subtensor(subtensor, "TotalHotkeyAlpha", block, [hotkey, netuid])
    return Balance.from_rao(raw).tao if raw else 0

async def get_divs_for_hotkey_on_subnet(subtensor, hotkey, netuid, block):
    raw = await query_subtensor(subtensor, "AlphaDividendsPerSubnet", block, [netuid, hotkey])
    return Balance.from_rao(raw).tao if raw else 0

async def get_total_stake(subtensor, hotkey, block=None):
    resp = await subtensor.query_subtensor(name='TotalHotkeyAlpha', params=[hotkey, 0], block=block)
    val = getattr(resp, "value", 0)
    return Balance.from_rao(val).tao

async def get_tao_weight(subtensor, block):
    resp = await subtensor.query_subtensor(name="TaoWeight", block=block, params=[])
    raw = getattr(resp, "value", 0)
    return raw / (2**64 - 1)

async def get_childkey_take(subtensor, hotkey, netuid, block):
    r = await subtensor.query_subtensor(name='ChildkeyTake', params=[hotkey, netuid], block=block)
    return getattr(r, "value", 0)

async def calc_inherited_on_subnet(subtensor, stake, netuid, parents, children, block):
    alpha_to_children = sum(stake * frac for frac, _ in children)

    alpha_from_parents = 0
    for parent in parents:
        frac, parent_hotkey = parent[0], parent[1]
        parent_stake = await get_stake_for_hotkey_on_subnet(subtensor, parent_hotkey, netuid, block)
        alpha_from_parents += frac * parent_stake
    
    return stake - int(alpha_to_children) + int(alpha_from_parents)