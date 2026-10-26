# Ticket62 public preview diagnosis

Owner source5ef91af, new isolated wt-62-perf / ticket/62-preview-performance. Read-only preparation while ticket50's final desktop quiet run is active; no business mutations or new measurements before explicit CPU release.

Existing exact red-capable feedback loop is unchanged AUTO_PLAN_SCALE_TEST=1 tests/test_evaluator_scale.py. Root's two observed failures are5.0384257436s and5.0894767195s; bounded repeat has4.878736/4.613330/5.089477s wall and4.844654/4.608479/5.070291s process CPU. Every responseHTTP200,12,514,156bytes and SHA25633f8eec3087d9258b787e76a6f558d4db4c0dcb1878775f07ecde34c330dd1e5. Domain literals/output419960/gap40/18unknownstateconditions remain correct. These existing user/root-provided executed red logs establish the actual failure without another blind rerun. The fastest equivalent diagnosis loop minimizes three repetitions to one full4200-row request; the full fixture stays unchanged because that is the reported threshold workload.

Three ranked falsifiable hypotheses shared before profiling:

1. workspace_view.py's day residual list scans all4182switches for each1890day cell, about7.9million predicate checks even when residual set is empty. Prediction: one request-local derived allocation index removes that time while preserving complete bytes, order and exact sums, including invalid drafts. This runs with actual Oracle too; it is not an adapter shortcut.
2. _line_weeks repeatedly scans switch/event lists for270line/week rows. Prediction: it appears independently prominent in uninterrupted worker profile; if it is minor it stays untouched.
3. MemoryPlanStore/MemoryInputStore clones dominate. Prediction: deepcopy callers dominate service time and actualOracle ownership differs. Adapter-only savings are not production gains and no adapter edit will be proposed.

Probe /tmp/auto-plan-spec49/profile62-preview.py instruments the already-created FastAPI sync route's actual worker call, not the outer TestClient thread. One cProfile interval covers the whole endpoint, service and JSON rendering, with no periodic dump inside active frames. Profiling artifacts/JSON hashing/compression happen after the request. Assertions retain literal evaluation, unchanged switches, outside rows and shared source, plus full response SHA. Profile overhead is never compared to5s. A later unprofiled loop and the original unchanged three-request gate must validate a measured fix.

## Confirmed diagnosis and one-variable result

After the explicit desktop CPU release, the first external instrumentation attempt stopped before issuing any request because FastAPI0.143 includes routes lazily. Resolving its effective included route contexts corrected only the off-repo probe. One uninterrupted profile then ran the full sync endpoint on its actual worker, including service and JSON rendering. It returned the exact original response hash and all independent literals.

Before profile: endpoint10.670s; workspace_view1.630s cumulative/.851s self; _line_weeks.489s; JSON rendering.225s. Memory deepcopies2.715s are separately labeled. No periodic profile snapshots fragmented parent/child totals.

One production change indexes only residual switch allocations by(date,line), in original switch order, and aggregates each switch's exact Fraction hours for each date. Ordinary switches are ignored once. The day matrix then reads the existing entry. No evaluator, protection, weekly event walk, adapter, cache, ownership or rule change was made.

The added public parity case passed before the fix. It independently checks1/3+2/3=1 hour on Thursday and3 hours on Friday, another actual job on another line, ordinary return-switch exclusion, duplicate-order invalid drafts still visible, actual state equality and read-only source. After the edit all82 affected matrix/unfinished/continuity/trial/continuation public tests passed in50.62s.

The unchanged original three-request5s gate passed once after this change, in13.40s overall. Requests were4.0317366/3.7719710/3.8952468s wall and4.0198495/3.7679693/3.8825112s process CPU. All three bodies are12,514,156bytes with the same original SHA25633f8eec3087d9258b787e76a6f558d4db4c0dcb1878775f07ecde34c330dd1e5. No threshold or fixture edits, blind repeated search for a passing gate, or timing-artifact work inside the timed request.

Matching after profile: endpoint9.695s, workspace_view.731s cumulative/.045s self; _line_weeks.464s and rule evaluation4.049→4.072s are effectively unchanged. This isolates about.9s of profile-visible application work, not an adapter clone optimization. Profiles are instrumentation observations, not gate/SLA measurements.

Fix62b7aac; final helper99a79f5 merges owner212266e. Actual PLAN_TEST composite reran on this merged new backend,1 passed in13.13s, with all literal qualified-output, atomic race/retry/adoption/current-yield/history/viewer/reopen assertions preserved and disposable namespace AP_TEST_266C07 dropped. No owned process or dependency link remains. All CPU windows released.
