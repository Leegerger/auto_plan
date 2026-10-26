# Ticket 62 preview performance fix

Fix commit `62b7aac5e8688a127e8365b5e93f625e08930702`; final clean helper HEAD `aa55447ce83437a5eb49439a523f824607617ac1` on `ticket/62-preview-performance`. Worktree `/tmp/auto-plan-spec49/wt-62-perf`. Owner `aa55447ce83437a5eb49439a523f824607617ac1` is merged. That final owner merge has identical content to the Oracle-tested helper pin `99a79f5afd25ba91cd390e804e10206a0e506907`. Business source tree `5a24fa53c385525fd9bf1d4affbab26159a47666`.

The day matrix scanned all 4,182 switches for every one of 1,890 cells, including this dense fixture's entirely ordinary switches. One request-local index now groups residual allocations by date and line. It preserves original switch order, zero entries, exact same-day Fraction sums and invalid draft presentation. No adapter, rule, evaluator, cache, ownership or weekly-state algorithm changed.

## Evidence and commands

The original five-second gate had failed twice on the unchanged source, at 5.0384 and 5.0895 seconds. The bounded three-request control was 4.8787 / 4.6133 / 5.0895 seconds. Those failures remain in the owner's durable evidence and were not reclassified as passes.

The actual FastAPI sync endpoint was profiled once before and once after the change, on its worker thread without periodic dumps. Profile overhead is excluded from the gate. The first probe setup stopped before any request because FastAPI includes its routes lazily; the off-repo instrumentation was corrected before profiling.

```sh
PYTHONPATH=src:. /root/codes/vibe_coding/auto_plan_2/.venv/bin/python \
  /tmp/auto-plan-spec49/profile62-preview.py --profile \
  --output /tmp/auto-plan-spec49/perf62-worker-before-actual
```

The after command uses output `perf62-worker-after`. Both include exactly one worker profile and return the identical full public response. Workspace cumulative time fell from 1.630 to 0.731 seconds; its own body fell from .851 to .045 seconds. Rule evaluation stayed 4.049 / 4.072 seconds. Memory-adapter deepcopy remained 2.715 / 2.631 seconds and was not edited. JSON rendering was .225 seconds before. The confirmed gain is in production matrix assembly.

Before the production edit, the new public matrix parity test passed in 2.21 seconds. It checks literal 1/3+2/3=1 Thursday hour and 3 Friday hours, another actual line/job, ordinary-switch exclusion, duplicate-order invalid drafts, unchanged actual records and no shared-source write. The performance regression itself is the existing red gate.

```sh
PYTHONPATH=src:. /root/codes/vibe_coding/auto_plan_2/.venv/bin/python -m pytest -q \
  tests/test_workspace_matrix.py tests/test_unfinished_switches.py \
  tests/test_switch_continuity.py tests/test_demand_trials.py \
  tests/test_demand_continuation.py
```

Result: **82 passed in 50.62 seconds**. Log `perf62-public-regressions.log`.

```sh
AUTO_PLAN_SCALE_TEST=1 PYTHONPATH=src:. \
  /root/codes/vibe_coding/auto_plan_2/.venv/bin/python \
  /tmp/auto-plan-spec49/perf62-observe.py
```

The observer invokes the unchanged `tests/test_evaluator_scale.py`. Its only path adjustment writes new evidence without overwriting the failures. Hashing/compression/file writes occur after all timed assertions. Result: **1 passed in 13.40 seconds** with the existing imported-anyio pytest warning. Exact three public requests:

| Request | Wall seconds | Process CPU seconds |
| --- | ---: | ---: |
| 1 | 4.0317366384 | 4.0198494740 |
| 2 | 3.7719710246 | 3.7679693300 |
| 3 | 3.8952467889 | 3.8825111690 |

Every body is HTTP 200, 12,514,156 bytes, SHA256 `33f8eec3087d9258b787e76a6f558d4db4c0dcb1878775f07ecde34c330dd1e5`, exactly matching the original failed controls. Independent output 419960, gap40, 18 relevant unknown-state conditions, 4200 segments, 4182 switches and outside/source equality remain correct. The original gate and scale fixture are byte-identical to `de410c6`.

Actual Oracle compatibility was rerun after merging the owner's source:

```sh
AUTO_PLAN_ORACLE62_EVIDENCE=/tmp/auto-plan-spec49/oracle62-after-perf \
  /root/codes/vibe_coding/auto_plan_2/.venv/bin/python \
  /tmp/auto-plan-spec49/run-oracle.py /tmp/auto-plan-spec49/wt-62-perf \
  -xq tests/test_oracle_spec49_acceptance.py
```

Result: **1 passed in 13.13 seconds**. The same 834/1 read-only boundary, 834+166 goal, exact continuity, atomic save, same-base 200/409 race, idempotent retry/verification, full adoption, factual pending state, current-yield restoration, immutable history, viewer 403 and fresh app/store reopen all passed. Fresh disposable `AP_TEST_266C07` was dropped; canonical business tables were not modified.

## Artifact handoff

- `/tmp/auto-plan-spec49/perf62-gate-after/`: all three exact compressed responses, request, samples, report with source/test hashes and manifest.
- `/tmp/auto-plan-spec49/perf62-worker-before-actual/` and `perf62-worker-after/`: uninterrupted worker profiles, text cumulative/internal/caller reports, environment/source pins, exact response and request.
- `/tmp/auto-plan-spec49/oracle62-after-perf/AP_TEST_266C07/`: sanitized complete public composition bodies, literal expectations, source/environment, cleanup, report, sanitizer and manifest. Authentication bodies/headers/connection values are excluded; recursive scan has zero findings.
- Logs are `perf62-worker-before-actual.log`, `perf62-worker-after.log`, `perf62-public-regressions.log`, `perf62-gate-after.log`, and `perf62-oracle-composite.log`.

No owned profiling, pytest, Oracle wrapper, worker or browser process remains. No dependency links were created. Worktree is clean and all quiet windows were released. Root owns the final whole-Python run after integration; owner owns final browser and durable packaging. No claim of deployed SLA, Oracle performance, customer-sample acceptance or container restart follows from this CPU diagnostic.

The exact retained harness sources are `/tmp/auto-plan-spec49/profile62-preview.py` and `/tmp/auto-plan-spec49/perf62-observe.py`; hashes are in the gate report. The profile harness has the existing synthetic company-secret login literal, without any actual credential or copied environment value. Retain it as clearly labeled test source, separately from sanitized HTTP records, which exclude all authentication bodies and headers.
