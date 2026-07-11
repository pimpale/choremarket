# ChoreMarket mechanism laboratory

This directory is isolated from the FastAPI/React application. It compares
mechanisms on a finite report domain where roommate `i` reports `(v_i, c_i)`:

- `v_i`: value of the chore being completed, whoever performs it;
- `c_i`: gross effort cost if `i` performs it.

If roommate `k` performs, gross welfare is

```text
sum_i(v_i) - c_k
```

Payments do not enter this expression. They are internal transfers: a payment
to the performer is another roommate's contribution, so an exactly balanced
cost split cancels when utilities are summed. The effort cost still appears
once through the performer's `v_k-c_k` gross utility.

The laboratory uses positive numbers for transfers received and negative
numbers for payments made.

## The cases

Every case implements the same `run(profile) -> Lottery` interface.

### 1. EqualSplit + FirstBest

Choose the lowest-cost performer `k` and do the chore iff
`sum(v) >= c_k`. If funded, everyone contributes `c_k/n` and the performer
receives `c_k`. This makes the benchmark's cost split explicit and exactly
balanced. It is an allocation/payment benchmark, not an incentive-compatible
mechanism: reported cost directly determines both assignment and payment.

### 2. EqualSplit + Vickrey + Majority Vote

The lowest cost report wins the reverse auction and the second-lowest cost is
the Vickrey price `p`. Everyone, including the performer, contributes `p/n`;
the performer receives `p`. The chore is funded when a strict majority reports
`v_i >= p/n`.

### 3. EqualSplit + Vickrey + FaltingsFair

Supply is the same reverse Vickrey auction and `p/n` split. FaltingsFair is a
**demand-stage** mechanism, not an integrated supply/demand sink:

- one demand report is excluded uniformly at random;
- demand agent `i` has fixed-price net value `v_i-p/n`;
- the remaining reports choose the efficient fund/no-fund decision;
- Equation (2) of Guo et al. symmetrizes the reduced-market VCG charges and
  rebates, avoiding a lumpy residual claimant.

The Faltings side transfers sum to zero and the equal split finances the
performer. See [Guo et al., WINE 2011](https://www.cs.cmu.edu/~conitzer/budgetbalanceWINE11.pdf).

FaltingsFair is truthful in expectation for its fixed-price demand subproblem.
The composition is not jointly DSIC: a selected performer can manipulate WTP
to trigger the Vickrey rent that exists only when the chore is funded. The
exhaustive audit reports both WTP-only and cost-only deviations.

### 4. LP Demand + Vickrey

Reverse Vickrey again chooses the performer and price. For each possible price,
a full finite-domain demand LP jointly optimizes funding probabilities and
outcome-contingent demand transfers. It enforces:

- DSIC in expectation against every WTP misreport with the price held fixed;
- demand transfers summing to `-p` when funded and zero when not funded;
- balanced side transfers even in a no-fund branch;
- minimax welfare regret over every winning cost compatible with the observed
  second price, with average welfare as a tie-breaker.

There is no posted-price, quota, sponsor, nonnegative-contribution, or universal
truthfulness restriction. FaltingsFair is feasible inside this demand LP's
constraint set. `demand_lp_solutions.csv` records the per-price LP diagnostics
(and, with `--audit`, certifies demand-stage WTP IC and price recovery).

The experiment runs the *raw-bid* deployment variant of this composition:
performer selection and the Vickrey price use unquantized cost bids, so no
supply-side welfare is lost to a report grid. WTP reports are quantized
internally and the demand lottery is precomputed on a `--demand-price-step`
price grid (default 2.5); the exact-vs-rounded price gap is charged equally in
the funded branch, keeping budget balance and supply DSIC exact while demand
incentives stay within `step/(2n)` of exact. Two WTP resolutions are compared:

- coarse demand: `0, 15, 30, 45`;
- fine demand: `0, 7.5, 15, 22.5, 30, 37.5, 45`.

This keeps the supply side fixed while showing how much demand-side
performance is lost to WTP discretization. The per-price demand LPs are
independent and solve in parallel across worker processes (spawned, not
forked — forked children inherit the parent's multithreaded HiGHS state
broken). A fully grid-bid variant (`UnrestrictedDemandSequential`) remains
available in `sequential.py`; on grid profiles the two coincide.

When composed with supply, the Vickrey payment `p` is added to the performer's
transfer. That funding-contingent rent is deliberately excluded from the fixed
price demand IC problem, then included in the full chore-utility exploitability
audit. This isolates the strategic cost created by sequential composition.

### 5. LP Integrated Supply/Demand

The full LP jointly chooses probabilities over `{no chore, roommate 0 performs,
..., roommate n-1 performs}` and each roommate's expected transfer per report
profile. Outcome-contingent transfers are reconstructed after the solve by
spreading each expected transfer over the performing outcomes in proportion to
their probabilities — a lossless reduction, since IC, regret, and welfare only
depend on expected transfers, and the reconstruction preserves:

- DSIC in expectation against every joint `(v,c) -> (v_hat,c_hat)` report;
- exact budget balance for every report profile and realized outcome;
- zero transfers when no chore is done.

On large domains the IC constraints are also generated lazily: grid-adjacent
misreports seed the model and cutting rounds add only the rows a candidate
solution violates (a few hundred out of tens of thousands in practice), with a
final full sweep guaranteeing every misreport gains at most `ic_tolerance`.

Worst-case welfare regret and average welfare are combined into one scalarized
objective, `regret - 1e-4 * average_welfare`, replacing the former two-pass
lexicographic solve; the regret reported can exceed the true minimax value by
at most `1e-4` times the domain's welfare range. Small grids keep a final pass
that minimizes absolute expected transfers to select a numerically tame payment
rule; fine grids skip that payment-only pass.

No ex-post, interim, or ex-ante IR constraint is included. True ex-ante utility
under the uniform exhaustive-grid prior is reported as an audit statistic. With
the symmetric prior, anonymity, exact BB, and nonnegative average welfare, every
agent has the same nonnegative ex-ante utility, making an explicit ex-ante IR
constraint redundant here. Profile-wise utilities can still be negative.

### Transfer bound

Reconstructed transfer mass satisfies `|z| <= M*x`, which prevents a
zero-probability outcome from carrying phantom transfers; in the reduced LP
this appears as the aggregated bound `|t| <= M*(1-x_null)` on expected
transfers, so a finite conditional-transfer bound `M` is still required. The
solution reports whether the bound is active. Previous cap checks on the `n=3`
grid found identical welfare objectives from `M=180` through `M=1440`.

## Exact symmetry reduction

The integrated LP is solved on anonymous profile orbits rather than all labeled
profiles. For `n=4` and the default 16 types, this reduces 65,536 labeled
profiles to

```text
C(16 + 4 - 1, 4) = 3,876
```

canonical multisets. Duplicate IC constraints are removed, stabilizer
equalities handle agents with identical types, and the solved mechanism is
expanded back to all 65,536 profiles for exhaustive verification.

## Data and plots

`synthetic.py` generates reproducible correlated household profiles. Effort
cost anchors come from the anonymized observed winning asks already documented
in the repository; WTP anchors encode stated replacement/cleanliness values.
Each output dataset contains raw dollar WTP/bids plus the quantized reports used
by the finite-grid mechanism. The default integrated/supply grid is now
`0, 15, 30, 45`, which reduces the former top-coding at 30 without making the
joint type domain unmanageably fine.

Synthetic allocations are chosen using the quantized reports but welfare and
the first-best benchmark are evaluated on the original continuous types. Thus
the calibrated welfare ratios include rounding and top-coding loss.

The old synthetic boxplot was flat whenever at least 75% of samples had zero
regret. It has been replaced by `synthetic_regret_tail.png`, which shows mean
regret, the frequency of nonzero loss, and worst loss.
`synthetic_welfare_ratio.png` gives the headline welfare comparison using only
the 500 household-calibrated scenarios. `synthetic_rounding_loss.png` isolates
the allocation loss from the coarse grid and the fine-WTP grid; the underlying
profile and summary data are written to matching CSV files.

`uniform_prior_regret_cdf.png` is the prior-free complement: true types drawn
uniformly with each coordinate capped at the top rounding boundary (top grid
level plus half a step, so top-coding of an unbounded tail cannot dominate)
and scored against the continuous first best. Unlike the exhaustive CDF —
which audits mechanisms on their own report domain and so contains no rounding
loss by construction — every curve here includes each mechanism's quantization
exposure under its deployment report interface. The same mechanism list runs
in every comparison: raw-bid and formula mechanisms consume the continuous
types directly, tabular mechanisms quantize at the door via the
`QuantizedReports` wrapper, and the raw equal-split first best anchors zero
regret. Profile and summary data are in `uniform_prior_regret.csv` and
`uniform_prior_regret_summary.csv`.

The exhaustive IC/BB/IR and demand-LP audits are diagnostic and skipped by
default; pass `--audit` to run them and emit `mechanism_audits.csv` plus the
audit columns of `demand_lp_solutions.csv`. Each run also writes `timings.csv`
and prints a chronological phase-timing report (LP model build vs solve vs IC
cutting rounds, per-mechanism audits, evaluation and plotting) to direct
optimization effort.

## Run

```bash
uv sync --extra laboratory --extra dev

# n=3 default: 16 types, 4,096 labeled profiles / 816 anonymous LP orbits
uv run --extra laboratory python -m laboratory.run_experiment \
  --participants 3

# n=4 default: 16 types, 65,536 labeled profiles / 3,876 anonymous LP orbits
uv run --extra laboratory python -m laboratory.run_experiment \
  --participants 4

# Override either resolution when needed
uv run --extra laboratory python -m laboratory.run_experiment \
  --participants 3 \
  --values 0 15 30 45 --costs 0 15 30 45 \
  --demand-fine-values 0 7.5 15 22.5 30 37.5 45

uv run --extra laboratory --extra dev pytest -q
```

The single-solve integrated LP now defaults to HiGHS interior point with
crossover disabled (`run_crossover=off`), which parallelizes and skips the
often-dominant crossover cleanup; the interior optimum also avoids the
degenerate-vertex transfer branches simplex solutions produced. Multi-pass
solves (the demand LPs and the small-grid regularization pass) keep the HiGHS
default, because tie constraints leave the feasible set without an interior,
where pure IPM fails. Override globally with `CHOREMARKET_LP_METHOD`
(`simplex`, `pdlp`, ...) or re-enable crossover with
`CHOREMARKET_LP_CROSSOVER=on`; configure the thread cap with
`CHOREMARKET_LP_THREADS` (default 8).

## Checked-in results

Result directories contain profile-level welfare CSVs, exhaustive mechanism
audits, synthetic bid/WTP data, LP diagnostics, and six figures. The current
broader-grid `n=3` exhaustive results are:

| Mechanism | welfare ratio | worst regret |
|---|---:|---:|
| EqualSplit + FirstBest | 1.000 | 0.000 |
| EqualSplit + Vickrey + Majority | 0.958 | 45.000 |
| EqualSplit + Vickrey + FaltingsFair | 0.959 | 20.000 |
| LP Demand + Vickrey, coarse WTP | 0.997 | 10.000 |
| LP Demand + Vickrey, fine WTP | 0.997 | 11.250 |
| LP Integrated Supply/Demand | 0.984 | 2.350 |

The fine demand LP optimizes on a larger report domain, so it need not improve
every statistic restricted to the coarse-grid subset. Its calibrated raw-data
welfare is slightly higher than the coarse version (87.92% versus 87.84% of
continuous first best), and its maximum composed-mechanism deviation gain falls
from 30.0 to 27.5. Both demand LPs pass their exact fixed-price WTP-IC audits;
the integrated LP's joint-report deviation gain remains numerical zero.

On the calibrated `n=3` sample, the coarse grid oracle achieves 87.93% of
continuous first-best welfare and the fine-WTP grid oracle achieves 88.18%.
The small difference shows that remaining rounding loss is driven mainly by
the shared supply-cost grid rather than demand WTP resolution.
