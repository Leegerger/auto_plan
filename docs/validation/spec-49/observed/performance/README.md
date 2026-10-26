# Public preview performance

The unchanged three-request gate failed at 5.038 seconds and again at 5.089 seconds before the fix. Exact failures and the bounded repeat remain in `../cpu/`. Quantities were correct. These runs are failures, not timing passes.

One uninterrupted worker-thread profile identified a matrix scan of all 4,182 switches for each of 1,890 day/line cells. Commit `62b7aac` builds a request-local residual-allocation index by date and line. It preserves original order, exact fractional aggregation and invalid-draft presentation. It changes no input adapter, allocation rule, output calculation, shared cache or state authority.

Worker-profile workspace time fell from 1.630 to 0.731 seconds; self time fell from 0.851 to 0.045 seconds. Rule evaluation stayed 4.049/4.072 seconds and memory deepcopy stayed 2.715/2.631 seconds. Profiles include instrumentation overhead and are separate from the gate measurements.

The unchanged public gate then passed once, with three requests:

| Request | Wall seconds | Process CPU seconds |
| --- | ---: | ---: |
| 1 | 4.031737 | 4.019849 |
| 2 | 3.771971 | 3.767969 |
| 3 | 3.895247 | 3.882511 |

Every complete response is 12,514,156 bytes with SHA256 `33f8eec3087d9258b787e76a6f558d4db4c0dcb1878775f07ecde34c330dd1e5`, identical to all failed controls. The public literal checks still establish output 419,960, final-week gap 40, 4,200 segments, 4,182 switches, 18 relevant unknown-state conditions and unchanged outside/source data. The original gate and fixture are unchanged from `de410c6`. Focused public parity checks passed 82 cases in 50.62 seconds.

`before/` and `after/` retain exact requests/responses, source/environment, worker profiles and text reports. `gate/` retains all three responses and measurements. `helper-validation.md` records commands and the actual Oracle compatibility rerun. That rerun passed the complete state/continuity/goal/race/history composition once in 13.13 seconds, using a fresh disposable namespace.

## Reproduction sources

The exact scripts in `repro/` are instrumentation around the existing public HTTP boundary. They add no business endpoint. Run from a checkout with its own installed Python dependencies:

```sh
PYTHONPATH=src:. python docs/validation/spec-49/observed/performance/repro/profile62-preview.py --profile --output /tmp/spec49-profile
AUTO_PLAN_SCALE_TEST=1 PYTHONPATH=src:. python docs/validation/spec-49/observed/performance/repro/perf62-observe.py
```

Use a fresh profile output directory. The observer preserves the original test assertions and defers hashing, compression and writes until afterward. Profile output, gate output and their source pins are separate; never compare profiled duration directly with the five-second gate.

The retained profile script uses the same fixed synthetic login literal as existing tests. This is labeled test source, not an authentication record. Sanitized captured HTTP records exclude all authentication bodies, headers, cookies and session values. No real credential or copied credential environment is retained. This fixed-host CPU diagnostic establishes neither actual Oracle latency nor production-site performance.
