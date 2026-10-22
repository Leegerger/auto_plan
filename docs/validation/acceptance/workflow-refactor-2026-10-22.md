# Acceptance workflow refactor validation

The user approved architecture candidate 1 against `6766a03c544af63522d0daf2b429ee3df966e953`. This change concentrates browser plan operations in stateless, named workflow functions. It migrates the runner's manual edits, private drafts, shared saves, computation submission, source review and candidate display. Maintained-input actors and initialization retain their own preparation flows.

The runner still owns scenarios, original UUIDs, first-input clocks, gates, complete business comparisons, evidence and restoration. Product code and frozen fixtures are unchanged. The historical candidate evaluation request expands only retained legacy uniform line yields through the existing compatibility function. Its exact transmitted bytes, original historical snapshot and implementation provenance are recorded separately.

## Checks

| Check | Result |
| --- | --- |
| Frontend typecheck and production build | Passed. Vite retains its existing large-chunk warning. |
| Default Playwright suite | 15 passed, 35 skipped. |
| Transaction-fixture navigation and shared workflow | 6 passed. After strengthening undo completion, the 5 workflow tests passed again. |
| Complete Node acceptance suite | 130 passed, 5 skipped, 0 failed or cancelled. |
| Default Python suite | 423 passed, 51 skipped, 1 failed. The failed case passed when rerun alone. |
| Observation error lifetime | All 7 tests passed through the same public interface used by the full private-save probe. |
| Four maintained-input actors | Passed. Separate output/preview-pin/retry and native computation source-pin probes also passed. |
| Five private editors, preview and undo | Small and full 4,200-segment cases passed. The full case preserves 600 weekly results. |
| Five private editors with a native job and shared save | Small adapter case passed, including a lost successful reply, original UUID verification, R1/R2/R3, public candidate evaluation and complete independent comparisons. |
| Candidate evaluation preparation | Legacy expansion and current-format pass-through passed; mixed-format input was rejected. |

The final Node command was `node --test --test-concurrency=1 frontend/tests/acceptance/*.test.mjs`. The final small native shared-save command enabled `AUTO_PLAN_PRIVATE_DRAFT_EDITORS_BROWSER=1` and `AUTO_PLAN_PRIVATE_DRAFT_SHARED_SAVE_PROBE=1`. Its report is `/tmp/auto-plan-private-draft-editors-suogkB/run/report.json`; shared-save completion was 15.5545 seconds, candidate public evaluation returned 200, and restoration reached R3. The full preview/undo report is `/tmp/auto-plan-private-draft-editors-fLtBL1/run/report.json`.

These local probes use the memory adapter. They do not establish Oracle persistence, the repeated external load matrix, production timings or human acceptance. Opt-in cases skipped by default suites remain separate qualifications.

## Checks that did not pass

The documented default Python command `env -u AUTO_PLAN_TEST_ORACLE PYTHONPATH=src:. .venv/bin/python -m pytest -q` finished with 423 passed, 51 skipped and 1 failed in 339.10 seconds. `tests/test_browser_job_freeze.py::test_job_freezes_the_reviewed_version_while_a_concurrent_public_writer_waits_for_admission` observed a stopped job instead of a completed candidate. This unchanged test submits a native job with a 10-second limit. A single rerun after the other probes finished passed in 6.76 seconds. That rerun does not change the failed full-suite result.

Two full 4,200-segment native shared-save overlap runs failed before the required post-save running-job assertion. The original native job reached its unchanged 90-second deadline and became interrupted with `stop_deadline`. Both runs restored the original complete plan to R3 and cleaned up their owned resources. Reports remain at:

- `/tmp/auto-plan-private-draft-editors-cbM5RT/run/report.json`
- `/tmp/auto-plan-private-draft-editors-rYchot/run/report.json`

Later setup and selection changes passed the focused workflow and small native probes, but the full native overlap case has not been requalified. No gate, fixture or solver deadline was relaxed. A passing full preview/undo case does not close this native-job limitation.

The broader Python command `env -u AUTO_PLAN_TEST_ORACLE PYTHONPATH=src:. .venv/bin/python -m pytest -q tests scripts/acceptance` finished with 479 passed, 55 skipped, 10 failed and 30 setup errors. All failures and errors were under `scripts/acceptance/`, in the frozen workbook, local-v2, rolling-v2, protected-source, private shared-epoch and initial-import preparation checks. Some older preparation paths still submit historical uniform line yields directly to strict current input endpoints and receive 422 or an invalid preview.

A representative initial-import setup error was reproduced unchanged in a disposable checkout of `6766a03`: `scripts/acceptance/test_initial_import_load.py::test_real_workbook_accepts_its_pin_when_other_authorized_editors_advance_inputs` fails at line 108 because `/plans/evaluate` returns 422. That confirms this particular error predates the refactor; it is not a baseline reproduction of every failed case. The older preparation paths were not changed by this task.

## Standards

The independent standards review finished with 0 unresolved documented-standard violations and 0 unresolved smell findings. It checked the final observation error-lifetime interface as well as the workflow module and its callers.

## Spec

The independent spec review finished with 0 unresolved findings against the approved scope. Review fixes made undo wait for restored visible output, retained the exact candidate evaluation request bytes, and moved primary-error retention into the publicly tested observation interface. The runner retains business and cleanup policy.

Review totals are Standards 0 and Spec 0. The test limitations above remain separate from those code-review results. Temporary report paths identify local evidence from this session; they are not archived acceptance packages.
