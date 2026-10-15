# Evaluator CPU diagnostic

Issue #39 removes repeated demand sorting, attribution scans and full output
evaluation during Recipe boundary comparisons. `rules.evaluate` and transaction
scope comparisons use the same Recipe event walk. Each evaluation builds its own
input, demand-week, source-week, weekly-result and confirmation-batch indexes.
There is no cache shared across versions. Candidate, save and adoption validation
still evaluates the complete frozen snapshot.

The shared preview already returns JSON-mode values. Its HTTP handler now renders
those values directly, avoiding a second recursive conversion by FastAPI. It
preserves the complete response, exact values, status and JSON media type.

## Fixed example and results

The fixture has 18 lines, 40 models and 105 production dates, from 2026-10-12
through 2027-01-24. Each model has 100 input pieces each day, unit yields, CT zero
and demand of 700 pieces in each of 15 weeks. All parameters are present. There
are no Holidays, WIP, locks or confirmation batches. Execution coverage confirms
blank facts as zero at the frozen starting Monday 08:00 Beijing time.

The explicit ordered plan has 4,200 production segments and 4,182 zero-duration
switches. Each segment uses one hour with full-day capacity of 2,400 pieces.
Expected output and on-time coverage are both 420,000 pieces, with no gap.

The shared preview changes all 40 models on the final date from 100 to 99 pieces,
retaining their hours. The hand-derived output and on-time coverage are 419,960
pieces. Each model has a one-piece gap only in the final week, totaling 40 pieces.
The fixture SHA-256 is
`69f83b724605421093648a73e949c5e868444a7319d96cac4f45dc7377790837`.

Measurements use Python 3.14.2 on an Intel Core i3-6100, with four logical CPUs in
the process affinity. FastAPI is 0.143.1, Starlette 1.8.0, Pydantic 2.14.1 and HTTPX
0.28.1. Resources were shared; cgroup limits were unavailable. Each timed request
includes request validation, complete authoritative evaluation and the response
body. Application startup and synthetic login happen before the first request.

| Public request | Baseline first / repeats | Final first / repeats |
| --- | --- | --- |
| Diagnostic `/plans/evaluate` | 1.911 / 2.104 / 1.838 s | 2.719 / 2.789 / 2.641 s |
| Shared `/plans/preview` | 13.331 / 12.461 / 14.340 s | 3.317 / 3.424 / 3.538 s |

The baseline is frozen commit `f1d035e`. Final source file hashes, all environment
metadata, process CPU times and intermediate measurements are retained in
[the measurement record](validation/evaluator-performance-39.json), alongside
[the before/after profiles](validation/evaluator-performance-39-profiles.txt). The final
source was measured before its implementation commit, so its file hashes identify
the measured code. The slower diagnostic-only measurements remain in the record;
the samples do not establish a uniform improvement for every request.

Canonical JSON is byte-for-byte equal to the baseline for both complete public
responses, including array order and all exact values. Diagnostic response size
is 7,539,791 bytes; shared preview size is 9,950,393 bytes. Their canonical hashes
are respectively:

```text
facc0f739dc86899aa56c28b724c6cdbca01e677d5c9f9b6cbea6672f2efeded
818f88c696c764fd3f53a86dbd3dfe87670ca68ce184858ffdfa24d268c17d31
```

The first indexing/state-walk implementation still produced shared preview times
of 4.971 / 5.118 / 5.031 seconds. Its profile showed a second JSON conversion of
the complete response. Removing that conversion brought all final samples below
the existing five-second budget. No acceptance threshold was changed.

## Repeating the diagnostic

From a development checkout with its dependencies installed:

```bash
PYTHONPATH=src:. .venv/bin/python scripts/measure_evaluator.py --output /tmp/auto-plan-measurement
AUTO_PLAN_SCALE_TEST=1 PYTHONPATH=src:. .venv/bin/python -m pytest -q tests/test_evaluator_scale.py
```

The output directory must be new. The script saves the exact fixture, canonical
compressed responses, first-plus-two-repeat measurements, environment metadata,
source hashes and a separately profiled shared request. Profiling overhead is
excluded from the request timings. The opt-in regression checks the public
business API against the five-second CPU budget and independently known totals.
Existing public API cases check fractional CT, Holidays, partial quotas, bans,
facts, WIP, window tails and transaction conflicts.

These are CPU diagnostics using in-memory persistence and company-authentication
adapters. They do not measure Oracle round trips, real network/proxy traffic,
browser rendering, deployed resource limits, five-editor contention, native solver
quality or final acceptance. The specification's 1/3/5-second, 30-second and
two-minute thresholds remain unchanged. TestClient emits the existing
Starlette/HTTPX deprecation warning.
