# ChoreMarket Laboratory

This directory is for mechanism-design experiments that should not affect the
deployed web app.

Install the optional optimization stack:

```bash
uv sync --extra laboratory
```

Run the posted-price LP demo:

```bash
uv run --extra laboratory python -m laboratory.run_posted_price_lp
```

## Posted-Price LP

`posted_price_lp.py` optimizes over a finite library of deterministic
posted-price branches. Each branch fixes a cost split before reports arrive and
then applies a monotone voting/quota rule:

```text
fund iff sum_i weight_i * 1[b_i >= charge_i] >= quota
```

If the branch funds the chore, everyone pays their fixed charge. This is
truthful branch-by-branch because a pivotal participant wants to accept exactly
when their true value covers their posted charge. A lottery over branches keeps
that incentive story because the lottery weights are optimized before reports.

The current demo includes:

- equal split unanimity
- equal split majority
- equal split two-thirds supermajority
- k-sponsor unanimity branches for every sponsor subset

The LP can optimize either:

- average welfare over a finite valuation grid
- minimax regret against the efficient benchmark

For one chore with cost `C`, the efficient benchmark is:

```text
OPT(v) = max(0, sum_i v_i - C)
```

The lab intentionally ignores individual rationality; majority and sponsor
branches may charge participants who would rather the chore not happen.

