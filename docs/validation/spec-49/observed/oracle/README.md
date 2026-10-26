# Ticket62 actual Oracle composite validation

Test-only commit `e898fa99eb3c9ff669bd523bc1d59d19d9e0940c` on `ticket/62-oracle-acceptance`, worktree `/tmp/auto-plan-spec49/wt-62-oracle`. Owner clock commit `d7b1b61cd65c454ca304f61968bc126dfe915bf7` is an ancestor. Final helper HEAD `4556e84b5ecc165b42a7d3f7b4d2c1c268cb070c` also includes owner UI commit `4e63650b440e89093ce6bb983f319040e4118668`. This final merge changes frontend/browser files only; `src` and the tested Oracle helper are identical to the green run. No setup rerun was needed for that merge. Business source tree `b212af499ea159a4f8f99d9dbe183ae5ca2bc429`. Only repository change is `tests/test_oracle_spec49_acceptance.py`. Test SHA256 `50e4485a69e40a979a199b304f54df90b261ed3a0ff58e49062f24d517e6b226`.

## Result and reproducible command

Final complete actual PLAN_TEST run: **1 passed in 12.13s**, including public initialization, all composition checks, fresh stores/application and namespace teardown. Source has CURRENT-only `production_date` clock metadata from owner62. First complete run on prior de56d03 passed in 15.59s; final rerun adds explicit switch new0/carried1/count1 and total1050, plus stale-source409 after current-yield maintenance. Authoring first had a test collection syntax error, then an initialization-preview schema422 because the apply-only operation_id had been sent to preview; both test harness errors were fixed. No business defect or production source change was needed.

```sh
/root/codes/vibe_coding/auto_plan_2/.venv/bin/python \
  /tmp/auto-plan-spec49/run-oracle.py \
  /tmp/auto-plan-spec49/wt-62-oracle -xq \
  tests/test_oracle_spec49_acceptance.py \
  > /tmp/auto-plan-spec49/ticket62-oracle-final.log 2>&1
```

The wrapper safely loads existing PLAN_TEST environment and holds `/tmp/auto-plan-spec49/oracle.lock`. No credential values are printed. Test uses OracleAccessStore, OracleInputStore, OraclePlanStore and OracleDemandStore with existing synthetic company authentication and T24ADMIN/T33EDITOR/T33VIEWER. Business tables live only in fresh namespace `AP_TEST_B3D3FE`; canonical business tables are not modified. Synthetic access/session rows retain the existing canonical test-account behavior.

## Independent literals and public sequence

Frozen 2026-10-14 10:00 Beijing, current production week 2026-10-12, target date Thu 2026-10-15. Public paired initialization imports3lines/3models and 45 explicit weekly demand rows, zero WIP/netbalance and execution coverage. P demand1000, N demand50, M0. CT0 and both model/line yields100%; R/S daily capacities1000. Outside C/N Friday production50 is imported through public initialization and then locked exactly50 through the same queued first save.

Queued state registers actual L R→S started2026-10-11 20:00, observedWednesday08:30, standard12/completed8/remaining4; B readyS and C readyR Monday. These observations are not persisted by the read-only trials.

- Read-only L835 returns834 input and qualified remainder1. L1000 returns834 input and remaining qualified goal166, empty unallocated-input list, exact original job/ref/endpoints and4h residual only. Head is unchanged after both. Capacity is valid; missing continuity prevents adoption422.
- Exact server-returned continuity basis is confirmed through the ordinary queue. Preview is executable and still read-only. One pending save atomically commits state/audit, outside lock, goal and continuity/audit. Pending istrue/latest_adoption0 and goal166.
- Two separate same-source B continuations each compute166 input/166 on-time/goal0 and keep the immutable original goal fields. Concurrent save gives exactly200 and409. Winner retry and verify return its original committed result. Exactly two accepted goal actions and P quantities834+166 remain; no duplicate B production or switch.
- Full current-source preview is executable, target P attributed/on-time1000, total output1050 including outsideN50. L weekly new0/carried1/switch hours4 and overall count1. Adoption succeeds200. Protected actual L record is still literally the observed unfinished4h, not projected completion. Partial revision2 remains immutable.
- Publicly remove target plan, change current P model-yield to50, and confirm old source preview409. Public history restore uses the current yield: P attributed/on-time500, goal500 and total550; complete adopted plan and outside lock are restored, current protected state remains unchanged. Original adoption1 and partial revision2 bodies remain literal.
- New Oracle store and application instances reopen the exact final current record and adoption. Final pointers revision6/input3/adoption1. Viewer can read exact current record and receives403 for preview/save/adopt mutations.

## Sanitized evidence

Final artifact subset for owner durable packaging:

`/tmp/auto-plan-spec49/oracle62-evidence/AP_TEST_B3D3FE/`

It contains exact initialization/trial/queue/race/verify/adoption/maintenance/restore/reopen public bodies, independent expected.json, environment.json, timings.json, report.json, cleanup.json, sanitizer.json, passing pytest.log and manifest.sha256. Auth request/response bodies, headers, cookies, credentials, DSN and environment values are excluded. Recursive JSON scan found no blocked auth/connection keys or synthetic login secret. Committed test source is referenced by commit/hash, not copied into the public artifact subset. Earlier authoring artifacts in sibling namespaces are not the final evidence subset.

Timings are diagnostic correctness-run observations, without a quiet CPU window while root's full Python suite ran. No new SLA or performance gate is claimed. Examples: readonly835 .2572s; target1000 .2098s; atomic pending save .1468s; racing winner/loser .1352/.1917s; full preview .0924s; adoption .2458s; fresh current read .1209s wall. Process CPU includes concurrent thread work and excludes Oracle server CPU. This is a small3line composition, not representative18line/4200row performance.

## Cleanup and limits

Namespace context exited successfully and dropped disposable tables; cleanup.json records it. No owned Oracle runner, pytest, application worker or browser process remains. No dependency links were created. Worktree is clean. No business source, frontend, tracker, remote or user-owned documentation changed.

Fresh application/store reopen occurred in the same test process, not a container restart. Retry/verify and same-base race are real public operations; this composite does not add commit fault injection. Full Python/browser/representative evidence belongs to the owner/root acceptance lanes. Deployment and sanitized customer samples remain separate under40/22.
