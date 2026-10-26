# Final review follow-up

Both independent reviews of `491c8b5` through `6c479bf` found that selecting an incomplete demand displayed definite coverage and hid unknown pending or late amounts. Standards also identified duplicated request construction and source fingerprints in the trial and comparison panels.

Fix `e159767da9d4dcd0c23c340816fa9f5ec6f72d64` resolves these findings. All six computed selected-demand quantities now show "待完成计算" when accounting is incomplete. Known weekly demand and maintenance links remain available. Common request fields and the actor/version/time fingerprint have one helper. Each caller still specifies its mode, and only a single-line trial adds its selected line. Comparison remains explicitly on time.

The backend source tree remains `5a24fa53c385525fd9bf1d4affbab26159a47666`. No Python source, rule, API, fixture endpoint, package dependency or timing limit changed. The earlier whole Python and Oracle evidence keeps its original source pins; no repeated whole-suite or performance claim is made here.

## Independent review outcome

| Axis | Initial findings | Resolved | Remaining |
| --- | --- | --- | --- |
| Standards | 2, including the duplication judgment | 2 | 0 |
| Spec | 1 | 1 | 0 |

The reports are retained separately: [initial Standards](observed/review-follow-up/initial-standards.md), [initial Spec](observed/review-follow-up/initial-spec.md), [Standards recheck](observed/review-follow-up/standards-recheck.md) and [Spec recheck](observed/review-follow-up/spec-recheck.md). Both rechecks inspected the committed fix. They did not claim additional runtime checks.

## Public browser red and green

The new browser case uses the existing incomplete-output fixture. It contains 100 planned N input pieces and demand of 1000, with Cycle Time missing. The public head reports incomplete accounting. Before the fix, selecting this demand showed "按时覆盖 0 片" and omitted pending material, pending shipping and late coverage. The test failed on the expected unknown coverage label against unchanged integrated assets.

After the fix, the same six computed labels are visibly unknown. The browser also checks known demand of 1000, the N/week Cycle Time maintenance route, retained date and quantity forms, selected production and the checked local hours-edit action. Returning from maintenance follows the existing invalidation and re-preview workflow. Shared revision stays unchanged.

| Focused check | Result |
| --- | --- |
| New summary and existing incomplete-late case | 2 passed in 10.2 seconds |
| Existing demand attention | 5 passed in 19.7 seconds; the other fixture case skipped |
| Single-line trial | 2 passed in 16.8 seconds |
| Independent comparison | 1 passed in 12.9 seconds; two representative opt-in cases skipped |
| Saved continuation across month and line | 1 passed in 15.3 seconds |
| Explicit late and confirmation return | 1 passed in 12.8 seconds |
| Clock invalidation and retained local intent | 1 passed in 4.9 seconds |
| Vue type checking and Vite build | Passed; Vite reports 15.64 seconds. Full command wall time was not separately measured. The existing bundle advisory remains. |

The total is 13 passed and three fixture-specific skips. These functional checks preserve selected queue operations, immutable goal anchors, source invalidation, explicit late mode and comparison selection. They do not establish new performance limits. Browser tests use fresh isolated fixture servers on ports 8831 through 8837 and the fix's own built assets.

Two intermediate test-authoring failures are retained. One expected an active preview after maintenance return, although that route intentionally invalidates it. The next attempted to open the toolbar action queue while the re-preview drawer was open. The test now explicitly re-previews and closes that drawer before checking the action queue. Neither correction changed product behavior or a timeout.

## Retained evidence and reproduction

[Independent expected values](observed/review-follow-up/expected.json), [source and result metadata](observed/review-follow-up/report.json), [environment](observed/review-follow-up/environment.json), [exact regression commands and fixture flags](observed/review-follow-up/regressions.json) and [clock adapter command](observed/review-follow-up/clock-run.json) accompany the logs. Red and green public current-head bodies, selected-summary text and screenshots are in `observed/review-follow-up/red/` and `green/`. Clock response pairs and its retained-action screenshot are in `clock/`.

Run the existing Playwright configuration from `frontend` with `PYTHONPATH` pointing at this checkout's `src` and root, the repository Python interpreter selected with `UV_PYTHON`, and `AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE=1`. The regression metadata gives each feature flag and test selector. The new case and incomplete-late case additionally require `AUTO_PLAN_BROWSER_DEMAND_TRIAL_FIXTURE=1` and `AUTO_PLAN_BROWSER_DEMAND_LATE_INCOMPLETE_FIXTURE=1`. The clock case uses `tests.browser_clock_server`, the external clock-file adapter and `AUTO_PLAN_BROWSER_EXTERNAL_URL`; it adds no business endpoint.

Gzip preserves exact source bytes, with hashes in the new [compression map](observed/review-follow-up/compression.json). The original acceptance records and pins remain intact. The root manifest includes this follow-up and all earlier evidence. No authentication payload, header, cookie, session value, browser storage or trace was retained. The separate follow-up sanitizer records the final recursive audit.
