# Prerequisite validation provenance

These checks belong to the individual implementation tickets. They are separate from the final combined acceptance in this directory. Counts below identify the tested source and scope; they do not claim that every check ran again on the final integration head. All Oracle checks used disposable `AP_TEST_` business objects in actual `PLAN_TEST`. Browser fixtures used the memory adapter and do not establish Oracle persistence.

| Ticket | Final implementation source | Observed validation |
| --- | --- | --- |
| #50 | `9055be6` | 13 public API checks, eight browser checks and frontend build. Effective local preview and source invalidation. |
| #51 | `3e29732` | 12 browser checks and build. Context retention and 18-line, 40-model, 15-week workspace at both desktop sizes. |
| #52 | `92007ca` | 80 API checks, original CPU preview gate, browser checks and build. Complete 105-day workspace state. |
| #53 | `de85c9e` | Full suite at an earlier checkpoint: 453 passed, 52 skipped. Later focused 112 checks, browser/build and actual Oracle confirmation/correction/race/history checks. Source and temporal guards received additional focused checks. |
| #54 | `bdc0094` | Full suite at this feature checkpoint: 487 passed, 53 skipped. Focused 134 checks, original three-preview CPU gate, browser/build and actual Oracle remaining-job persistence/correction/history checks. |
| #55 | `ccd3ad9` | Full suite: 508 passed, 54 skipped. Focused 201 checks, original CPU gate, two continuity browser checks, build and actual Oracle atomic adoption/race/commit-fault/history/reopen proof. |
| #56 | `17c51b7` | 65 affected native/source cases have green coverage across separate runs, not a single all-green batch. State/manual group: 136 passed. Four focused actual Oracle cases passed across initial and corrected-fixture runs. New real-worker browser case and seven existing job browser cases passed; build passed. |
| #57 | `04501f0` | Public demand-attention and browser checks, build. Authoritative reasons and separate units, including pending confirmations and incomplete accounting. |
| #58 | `6890bcc` | 86 focused API checks, two trial and two continuity browser checks, build and one actual Oracle trial/goal persistence check. |
| #59 | `b2bafe9` | 80 focused API checks, one actual Oracle continuation/race/history/reopen proof. Final date-retention follow-up passed two browser checks and build. Cross-month goal accounting retains qualified output rather than prior input. |
| #60 | `de410c6` | Final 127 affected API checks, five browser checks, build, original CPU-preview gate and one actual Oracle positive comparison proof. Representative comparison timings are recorded separately below. |
| #61 | `a1ef3d9` | Final six late API checks, full normal browser flow, incomplete-accounting browser case and build passed. Earlier actual Oracle explicit-late/save/retry/verify/maintenance/adopt/history/reopen proof passed; it was not rerun at the final source pin. |

## Limits and retained failures

The #56 native batch initially had one invalid fixture using source-segment hours, then that exact corrected public case passed. Two Oracle fixtures needed explicit public field confirmation under the new adoption contract and passed after correction. One Oracle worker startup returned 503 and passed on an isolated retry; the cause was not established. A worker watchdog emitted `FileNotFoundError` after its temporary directory had been removed. This was recorded without claiming a lifecycle fix.

The full-suite counts from #53, #54 and #55 are historical checkpoints, not final whole-branch validation. Ten malformed-serializer fixture warnings were already present at baseline. The frontend build retains its existing bundle-size warning. Exact final validation and source pins are reported separately.

No check here establishes formal customer-sample or production-site acceptance. Issues #22 and #40 remain separate and open. Remote branch publication is pending explicit approval after an automatic approval rejection.

## Reproducible checks

The committed `tests/test_*.py`, `frontend/tests/*.spec.ts` and `tests/browser_server.py` retain the public input fixtures and independent expected literals. Feature tests use the same application APIs as the product. Opt-in actual Oracle tests assert `PLAN_TEST` and use the disposable namespace helper. Credentials and browser sessions are excluded from this report and retained artifacts.
