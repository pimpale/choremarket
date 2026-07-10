# ChoreMarket mechanism laboratory

This directory is isolated from the FastAPI/React application. It compares
direct mechanisms on a finite report domain where roommate `i` reports
`(v_i, c_i)`:

- `v_i`: value of the chore being completed, whoever performs it;
- `c_i`: gross effort cost if `i` performs it.

Gross utility is `0` for no chore, `v_i` when someone else performs, and
`v_i-c_i` when `i` performs. A positive transfer means money received. Exact
budget balance therefore means transfers sum to zero.

## Mechanisms

All mechanisms implement the same small `run(profile) -> Lottery` interface.

1. **First best** chooses the lowest-cost performer and acts iff
   `sum(v) >= min(c)`. It has no incentive claims.
2. **Integrated VCG** applies the Clarke pivot rule to `{none, performer 0,
   ..., performer n-1}`. It is efficient and jointly DSIC but generally runs a
   deficit.
3. **Sequential mechanisms** first run reverse Vickrey procurement and then use:
   - the repository threshold `sum(v) >= winning bid`;
   - equal-share majority at the second-price cost;
   - a minimax-regret lottery over fixed-charge quota/sponsor demand branches,
     optimized separately for each possible Vickrey price and robust to every
     compatible winning effort cost.
4. **Random-sink VCG** chooses a sink uniformly before reports, omits both type
   dimensions of the sink, makes it ineligible to perform, runs reduced-economy
   VCG, and gives the sink the residual. Every fixed-sink branch is DSIC and
   exactly balanced, so the lottery is universally DSIC.
5. **FaltingsFair** uses the same random excluded-agent allocation and Equation
   (2) of Guo, Naroditskiy, Conitzer, Greenwald, and Jennings (WINE 2011). Its
   symmetric expected charges are balanced at every report profile and DSIC in
   expectation. See [the primary paper](https://www.cs.cmu.edu/~conitzer/budgetbalanceWINE11.pdf).
6. **Full integrated LP** jointly optimizes allocation probabilities and
   probability-weighted outcome-contingent transfers. It solves three variants:
   - joint DSIC-in-expectation + conditional exact BB;
   - add conditional ex-post IR;
   - add IR and progressive non-performer contributions.

For progressivity, if two non-performers are in the same reported profile and
one reports strictly higher WTP, that agent's payment must be weakly higher.

The LP checks every joint `(v,c) -> (v_hat,c_hat)` deviation, not only one-field
deviations. It sets transfers to zero for no chore and uses explicit adjacent
permutation constraints to make the solution anonymous. The primary objective
minimizes maximum regret; average welfare is an exact second-pass tie-breaker;
total absolute transfer mass is a third-pass numerical regularizer.

### Transfer bound

Probability-weighted transfers `z=x*t` keep the model linear. The constraints
`|z| <= M*x` prevent a zero-probability outcome from carrying phantom payments,
which requires a finite conditional-transfer bound `M`. The run reports whether
the cap is reached and supports cap-sensitivity solves. On the `n=3` grid,
changing `M` from 180 to 1440 changes neither welfare objective to solver
precision, although non-unique conditional payments reach the cap.

## Data

`synthetic.py` reproduces realistic, correlated household profiles. Cost
anchors are the anonymized observed winning asks already documented in the
repository (dishes, trash, kitchen, vacuum, sink, microwave, bathrooms). WTP
anchors encode the household's stated replacement/cleanliness values. Persistent
roommate cleanliness and reluctance traits plus chore shocks create correlation.

Every run writes `synthetic_bid_wtp.csv` with raw dollar WTP/bids and the
quantized reports used by the solved finite-grid LP. The RNG seed is fixed and
configurable.

## Run

```bash
uv sync --extra laboratory --extra dev

# Main exhaustive n=3 grid: 9 types and 729 profiles
uv run --extra laboratory python -m laboratory.run_experiment \
  --participants 3 --values 0 15 30 --costs 0 15 30 \
  --cap-sensitivity 180 720 1440

# Tractable n=4 grid: 4 types and 256 profiles
uv run --extra laboratory python -m laboratory.run_experiment \
  --participants 4 --values 0 30 --costs 0 30

uv run --extra laboratory --extra dev pytest -q
```

## Checked-in results

`results/n3` and `results/n4` contain exhaustive profile-level CSVs, mechanism
audits, synthetic data/results, LP diagnostics, and three PNG figures each.

Main exhaustive-grid welfare results:

| Mechanism | n=3 ratio | n=3 worst regret | n=4 ratio | n=4 worst regret |
|---|---:|---:|---:|---:|
| First best / integrated VCG | 1.000 | 0.000 | 1.000 | 0.000 |
| Repository sequential threshold | 1.000 | 0.000 | 1.000 | 0.000 |
| Sequential equal-share majority | 0.934 | 30.000 | 0.859 | 60.000 |
| Sequential optimized demand LP | 0.823 | 24.545 | 0.893 | 30.000 |
| Random sink / FaltingsFair | 0.916 | 15.000 | 0.970 | 7.500 |
| Integrated LP: base | 0.998 | 0.299 | 1.000 | 0.000 |
| Integrated LP: ex-post IR | 0.871 | 12.000 | 0.990 | 15.000 |
| Integrated LP: IR + progressive | 0.871 | 12.000 | 0.990 | 15.000 |

The repository threshold has no truthful-report allocation loss because it is
literally the first-best inequality. That does **not** make the composed rule
jointly truthful: the exhaustive audit finds profitable WTP and cost deviations.
The demand-stage majority/posted-price versions expose the actual welfare cost
of sequential decomposition. On `n=3`, the optimized demand lottery lowers
worst-case regret relative to majority (24.545 versus 30) by accepting lower
average welfare; its objective is minimax regret, not average welfare. The
unrestricted base LP recovers substantially
more grid welfare than random sink, but adding ex-post IR is costly on the
finer `n=3` grid; progressivity adds essentially no further worst-case cost.
