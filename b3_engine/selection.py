"""Rank A/B candidates and resolve channel overlaps without placing trades."""
import pandas as pd


def select_universe(ctx, positions, day):
    pos, wk = positions, day
    sw_tsm, cap_ok, targetA, targetB = ctx.sw_tsm, ctx.cap_ok, ctx.targetA, ctx.targetB
    r20s, tv20 = ctx.r20s, ctx.tv20
    cfg = ctx.config
    W_A, W_B = cfg.weight_a, cfg.weight_b
    MAX_HOLD_A, MAX_HOLD_B, DYN_A = cfg.max_hold_a, cfg.max_hold_b, cfg.dynamic_a
    on = bool(sw_tsm.get(wk, False))
    a_w, b_w = (W_A, W_B) if on else (1.0, 0.0)
    capw = cap_ok.loc[wk]
    def cap_pass(c):
        return c not in cap_ok.columns or bool(capw[c])

    want = {}
    tA = targetA.loc[wk]
    codesA = [c for c in tA[tA].index.tolist() if cap_pass(c)]
    scoredA = sorted(codesA, key=lambda c: float(r20s[c].loc[wk]) if pd.notna(r20s[c].loc[wk]) else -1, reverse=True)
    want["A"] = scoredA[:MAX_HOLD_A]
    want_rank = {c: i + 1 for i, c in enumerate(want["A"])}
    if b_w > 0:
        tB = targetB.loc[wk]
        codesB = [c for c in tB[tB].index.tolist() if cap_pass(c)]
        tvw_ = tv20.shift(1).loc[wk]
        scoredB = sorted(codesB, key=lambda c: float(tvw_[c]) if pd.notna(tvw_[c]) else -1, reverse=True)
        want["B"] = scoredB[:MAX_HOLD_B]
    else:
        want["B"] = []
    _rawA, _rawB = list(want["A"]), list(want["B"])
    _fa, _fb = [], []
    for c in _rawA:
        if c in pos and pos[c]["pool"] == "B" and c in _rawB:
            _fb.append(c)
        else:
            _fa.append(c)
    for c in _rawB:
        if c not in _fa and c not in _fb:
            _fb.append(c)
    want["A"], want["B"] = _fa, _fb
    want_set = set(_fa) | set(_fb)
    if DYN_A and on and not want["B"]:
        a_w, b_w = 1.0, 0.0

    return want, want_rank, want_set, a_w, b_w
