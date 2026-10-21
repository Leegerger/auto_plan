# Rolling preparation companions

The three explicit successors are `r10-rolling-v2`, `r20-rolling-v2` and `r40-rolling-v2`. Their immutable V1 predecessors, failed or unrun observations, business quantities, actions, full 33-coordinate reference objectives and acceptance gates remain intact. The first of five repeats still counts. These companions describe preparation before timing, not a new-week timing pass or confirmed user distribution.

V1 could not match public preparation exactly because it omitted the mandatory execution coverage/cutoff correction audit and used a different materialized demand-list order. V2 freezes that audit before execution, the explicit zero-WIP reset batch, and public model/week ordering. Sources and references retain their own original Plan IDs, dates, orders, exact hour witnesses and switch layouts. The source has 3,920 production segments; its reference has 4,200. The r10/r20 sources each retain 392 switches and their references 420. The r40 source retains 3,920 switches and its reference 4,200.

The declared audit actor is the synthetic editor `ACCEPTANCE0`. The new boundary Recipe states are explicit synthetic observations declared in each preparation asset. Expired planned work is never promoted into an execution fact. All execution facts remain empty. The 280 expired source rows, totaling 28,000 pieces, remain historical reset lineage. The remaining fourteen weeks retain their original quantities and identities.

## Generate into a new directory

```bash
PYTHONPATH=src:. python scripts/acceptance/rolling_v2.py freeze --v1 docs/acceptance/frozen --output docs/acceptance/rolling-v2/frozen
```

Generation verifies predecessor manifest SHA256 `33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4` and every consumed frozen asset. Existing output directories and V1 descendants are refused. Each successor records its predecessor manifest/source/reference/action/import hashes. Its `import` is the original prior-week loose import, not a substitute first import at the new clock. Separate assets declare the prior complete source, new dated demand file and preparation observations/history.

## Prepare in two public phases

Use an explicitly disposable target and an authenticated synthetic editor whose public account ID equals the declared audit actor. Provide credentials through private stdin from the authorized in-memory provider. Never put credentials or cookies in files, argv or reports. The non-secret config names the explicit successor scenario, HTTPS origin, application prefix, disposable target and reproducible environment.

At the initial controlled clock, run:

```bash
PYTHONPATH=src:. python scripts/acceptance/rolling_v2.py prepare --phase initial --config /tmp/public-configs/r20-rolling-v2.json --fixtures docs/acceptance/rolling-v2/frozen --output /tmp/public-prepared/r20-rolling-v2
```

This phase requires an empty target at `2026-10-12T08:00:00+08:00`. It previews and imports the original complete 600-row demand file, then previews and adopts the exact original Plan. Full source equality, actual adoption/coverage, immutable file rows and original UUID verification must pass before the report becomes `awaiting_clock_transition`.

Stop the owned test application and restart it against the same disposable namespace at the declared rolling clock, `2026-10-19T08:00:00+08:00`. This is an explicit test infrastructure action. The preparer contains no clock-changing endpoint. Log in again, then run:

```bash
PYTHONPATH=src:. python scripts/acceptance/rolling_v2.py prepare --phase rolling --config /tmp/public-configs/r20-rolling-v2.json --fixtures docs/acceptance/rolling-v2/frozen --output /tmp/public-prepared/r20-rolling-v2
```

The second phase verifies the same target, original Plan/adoption/input tokens and unchanged first demand file before writing. It appends the new 40-row file attributed to October 19, with 700 pieces per model for January 25. File versions belong to each attribution date, so the October 12 and October 19 files are both version 1 and remain effective. It previews and saves one atomic command containing the predeclared execution coverage/cutoff correction, weekly reset from the original adoption, and explicit synthetic boundary Recipe observations. Its action IDs, actor, reason and audit time were frozen before execution.

The full prepared snapshot, saved immutable revision, original immutable adoption, both complete demand files and reset history must match. All original operation UUIDs, commands, acknowledgments and verification responses are retained. A lost reply reconciles its original UUID. An unresolved failure preserves the journal and stops; it cannot silently restart with fresh UUIDs. No SQL, direct business seeding or adoption rewrite is used.

The final `run-config.json` records actual live tokens, the explicit successor ID and absolute fixture path. A timed run still needs a passed public preparation, full source preflight, corrected locked image, exact environment, unchanged official runner and five first-inclusive repeats. A registry successor entry must explicitly bind the released manifest/source/reference/action hashes to its immutable V1 predecessor and authorization record. V1 is never relabeled as a V2 observation.

## Functional evidence and remaining qualification

The r20 public adapter lifecycle passed complete prior-adoption, week advance, demand history, reset lineage and source assertions. All three generated companion invariants passed against their own frozen predecessors. The r10 two-phase public preparer passed after its missing-function regression and retained setup failures. Its real public demand write loses its reply at the caller seam, then resolves through the original UUID before continuing.

The historical r10 Oracle lifecycle passed on product source `61e2476ed69b2f1b48c0b030247e3e07eac7a4e4`. Separate selected r20 and r40 Oracle lifecycles each passed once on product source `3ecc1b8a058b4133f8610ed3d8282cbd54c488d5`, in 243.25 and 281.99 seconds respectively. Those durations describe functional test execution. Each used its own disposable namespace, restarted physical pools against the same namespace at the declared next clock, reconciled the original lost-reply demand UUID and verified the complete source, adoption, revision, demand files, reset history and protection. Each ended at R3/V3/A1 with both phase pools and the control pool closed and no remaining namespace objects.

Both new Oracle source evaluations retained 392000 expected/on-time pieces and 28000 gap; their reference evaluations retained 420000 expected/on-time and zero gap. All four complete responses, including all 600 weekly rows and 33 objective coordinates, were byte-identical to the independently retained public default-evaluation proofs. The existing frozen manifest and all V1/V2 assets remained unchanged.

`scripts/acceptance/test_oracle_rolling_v2.py` now collects explicit `r10`, `r20` and `r40` cases. Without the Oracle opt-in it skips all three. Select each intended case explicitly when using the authorized in-memory configuration provider; retain its original output and stop on a failure. Constrained-image native new-week candidates, the fixed five-repeat timing matrix, five-editor load and user confirmation remain unqualified.
