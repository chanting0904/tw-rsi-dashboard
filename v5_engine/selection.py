"""Rank the single strategy universe without placing trades."""
import pandas as pd
from .security_status import can_trade


def select_universe(ctx, positions, day):
    # Pending dividend rights do not occupy an old-stock retention slot.
    pos={c:p for c,p in positions.items() if p['shares']-p.get('locked_shares',0)>0}
    wk = day
    cap_ok, targetA = ctx.cap_ok, ctx.targetA
    r20s = ctx.r20s
    cfg = ctx.config
    MAX_HOLD_A = cfg.max_hold_a
    capw = cap_ok.loc[wk]
    def cap_pass(c):
        return c not in cap_ok.columns or bool(capw[c])

    want = {}
    tA = targetA.loc[wk]
    codesA = [c for c in tA[tA].index.tolist() if cap_pass(c) and can_trade(ctx, c, wk)]
    rank_scores = getattr(ctx, 'a_rank_scores', None)
    if rank_scores is None:
        rank_scores = r20s.rolling(cfg.a_rank_days, min_periods=cfg.a_rank_days).mean()
    scores = rank_scores.loc[wk]
    scoredA = sorted(codesA, key=lambda c: float(scores[c]) if pd.notna(scores[c]) else -1, reverse=True)
    ranks = {c: i + 1 for i, c in enumerate(scoredA)}
    # Eligibility and capital filters take precedence over retention. Existing
    # A names through rank 20 occupy the same 15 slots; vacancies accept top 15.
    retained = [c for c in scoredA[:cfg.a_retention_rank]
                if c in pos and pos[c]['pool'] == 'A'][:MAX_HOLD_A]
    want["A"] = retained + [c for c in scoredA[:MAX_HOLD_A] if c not in retained][:MAX_HOLD_A-len(retained)]
    want["A"].sort(key=ranks.__getitem__)
    # Keep the original entry ranks for position sizing.
    want_rank = {c: ranks[c] for c in want["A"]}
    want["B"] = []
    return want, want_rank, set(want["A"]), 1.0, 0.0
