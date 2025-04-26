def has_enough_stake(root_stake, alpha_stake, inh_root_stake, inh_alpha_stake, tao_weight):
    if alpha_stake < 10:
        return False

    combined_stake = root_stake * tao_weight + alpha_stake
    inh_combined_stake = inh_root_stake * tao_weight + inh_alpha_stake
    
    if combined_stake < 4000 and inh_combined_stake < 4000:
        return False
    
    return True
