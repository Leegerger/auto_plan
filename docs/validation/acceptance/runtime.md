# Packaged runtime observations

Representative scenario acceptance is closed under the updated timing thresholds; external site/human/20-real-account acceptance remains `not_run`. The measured packaged source is `20be59608355f1770c290292c3905fb63ad892c7` and image is `sha256:23117388037b8c17d1bc5974f5c50d04bead34d59b66472ad470158e1d43fc61`, with five-editor concurrent browser probe verification on the current branch.

The updated gates are 3/15/30/60/180 seconds (`selection`/`week`/`day`: 3s; `single_cell`/`batch_preview`: 15s; `import_validation`/`shared_save`: 30s solo, 60s under five-editor concurrent load, 360s concurrent workflow deadline; `local`: 60s; `new_week`/`all_replan`: 180s). Every solo sequence below includes its first repeat; caches are retained. Values are seconds rounded to six decimals here, with full precision, source/helper/config/frozen hashes, setup intervals, semantic checks, resets and resource observations in [runtime.json](runtime.json). The exact raw public report bytes from the original solo runs are preserved in the linked deterministic gzip archives.

| Run / scenario / mode | Operation | Gate | Repeats 1–5 / Probe | Status |
| --- | --- | ---: | --- | --- |
| all-replan-solo-v1 / r20-loose / solo | all_replan | 180 | 23.166069, 22.042187, 21.415246, 21.349174, 21.817290 | pass |
| import-v1 / r20-loose / solo | import_validation | 30 | unknown, unknown, unknown, unknown, unknown | capture_failed (not five qualified repeats) |
| import-v2 / r20-loose / solo | import_validation | 30 | 9.738052, 9.581841, 9.665580, 9.691769, 9.689823 | pass |
| solo-v1 / r20-loose / solo | batch_preview | 15 | 6.650003, 6.355403, 6.435081, 6.552711, 6.516995 | pass |
| solo-v1 / r20-loose / solo | day | 3 | 0.148562, 0.187095, 0.156181, 0.180410, 0.122077 | pass |
| solo-v1 / r20-loose / solo | selection | 3 | 0.184170, 0.183600, 0.184214, 0.218118, 0.201825 | pass |
| solo-v1 / r20-loose / solo | shared_save | 30 | 9.799370, 10.401798, 10.152504, 10.421093, 10.483883 | pass |
| solo-v1 / r20-loose / solo | single_cell | 15 | 6.488869, 6.401097, 6.385661, 6.498422, 6.634599 | pass |
| solo-v1 / r20-loose / solo | week | 3 | 0.131251, 0.117827, 0.188619, 0.173287, 0.148702 | pass |
| r20-loose-private-shared-save-v1 / r20-loose / five_automated_editors_one_computation | shared_save + primary_job | 60 / 180 | shared_save 49.094600, primary_job 155.637900 (44 passed editor actions across R1/R2/R3) | pass |

The audited solo selection toggles the dependent Vue selection/form state. Week and day start at different populated identities. Single-cell changes 100→99 and asserts the complete 419999-piece/gap1 result. Batch removes the two named rows with exact outside witnesses. Shared saves persist the real 99-piece change under a UUID-bound new immutable revision, verify a fresh browser no-store read, then explicitly restore the complete original source outside timing. No zero-change save or already selected navigation target is credited.

The server and headless Chromium share an Intel i3-6100 host with 4 logical CPUs and Linux MemTotal32727084 kB; resources are not exclusive. The scale container has 4 CPUs/8 GiB, one API process and owned compute worker, one native thread, and Oracle pool maximum 8. Root heavy CPU work was paused during timings. The first solo run observed 591687680 bytes peak container memory, pool maximum busy/opened 5, and no CPU throttling. Each further run retains its own pool/cgroup observations. Network is direct loopback TLS retaining `/planning`, using an explicit test adapter and synthetic accounts. It does not certify the company proxy or intended site hardware.

Both fresh locked-image native routes passed the one-thread integer optimum 3: CP-SAT 9.15.6755 and HiGHS 1.15.1, Python 3.14.7, Oracle Thick mode with InstantClient 21.13. The pinned native base is `sha256:484bf4b4f9b4247edd06710a2e325cdf9431b83a028df5c41bea3516d29ced57`; lock and all migration fingerprints are in the JSON. Clean admitted application/frontend/lock hashes equal the measured image. No application source is mounted into final runs.

Default entrypoint observations establish unconfigured readiness 503, static 200, sanitized failure/restart, and signal exit 1.218896303 seconds. Separate actual Oracle 19.3 lifecycle observations establish 20 synthetic role expectations, owned worker SIGKILL becoming durable failed, explicit original-input rerun, exact candidate persistence, old-cookie 401 and restart interruption. Final actual daemon signal-to-exit is 28.995660376 seconds and Docker CLI wall time 29.130171914 seconds against the whole-process 30-second gate. The prior committed 31.307003835-second daemon failure and earlier 31.6438-second diagnostic remain failures; they are not reclassified as CLI overhead.

Correctness evidence on the measured source: 227 Python tests passed/40 explicit opt-in tests skipped; 5 affected actual Oracle history/reset/stability/initialization/solver checks passed separately; 4 literal dated-coverage cases passed on original and optimized source. All 38 complete public evaluation responses equal archived original source exactly, canonical hash `c4750335550557ab567705b21c9d3d332ebd179dfff59df84d24fac79c4870b9`. Complete current, preview, selected revision and adoption records were also compared during affected Oracle diagnostics.

Original selection failures, original single-cell five failures, independent true-browser paint failure, packaged login/history setup failures, and failed five-page setup attempts remain in the historical artifact ledger. Earlier nominal week/day no-op observations are withdrawn. Unstarted operations have null durations. Started uploads whose capture failed also have null durations: their completion is unknown, and measurement_started plus request/input timestamps distinguish them from unstarted work.

The original five import uploads lost complete response capture after their timers started. Isolated probes identified inspector response retention; enabling durable messages after page initialization preserves ordinary browser response parsing without changing the payload or completion boundary. Runner commit `4529920` contains only that capture correction. The exact original workbook and application image were rerun (`import-v2`): all five full payload, source rows, complete inputs/context, independent reference and no-write checks passed, and all five measured durations (9.58–9.74s) pass the updated 30-second gate.

All five r20-loose all-replan candidates passed the 180-second gate (21.35–23.17s) with the exact frozen 90s/hybrid/one-candidate action, full submitted-source provenance, complete 33-coordinate quality comparison, protection checks and terminal job cleanup. Under five-editor concurrent load (`r20-loose-private-shared-save-v1`), five Chromium contexts completed 44 private edit/undo actions across epochs R1/R2/R3 while a real native job ran from R1 (`155.637900s <= 180s`), primary shared save with unknown-reply recovery completed in `49.094600s <= 60s`, and R3 restored the complete original source (`report` SHA256 `1f36559a7d1e41853d361f18e35b728b5825fa463824aee10598f792eae74e8e`). The `native-quantity-cp-sat20s-successor` public-job lifecycle control also passed (`2.97s`).

Local r20-near-decrease has zero timed repeats (`not_run`); one unchanged-precondition repetition retained the complete response in [local-preparation-preview.json.gz](local-preparation-preview.json.gz). Remaining extended scenario/mode matrix slots (`r10-*`, `r30-*`, `local-v2`, `rolling-v2`, and full 5-repeat load matrix sweeps) remain `not_run` in [runtime.json](runtime.json).

Real company authentication, 20 real accounts, five human participants, user-confirmed reference quality, site resource/proxy acceptance, production deployment, real-data migration and destructive shared recovery remain `not_run` with exact reasons in the JSON. Synthetic roles/contexts are not those external acceptances. No parent decision map or issue 1/22 closure is claimed.

Raw completed report archives:

- [all-replan-solo-v1.json.gz](all-replan-solo-v1.json.gz): original report SHA256 `c26718954942b54665420f6da401405ca6789119d154c3a1b7b7d60f1248a3cc`; archive SHA256 `e88d491b6a85fff19eb08adfa6a0e5f55923aa7b9f3cef6c3a8f45ca3f2be2ee`.
- [import-v1.json.gz](import-v1.json.gz): original report SHA256 `7397e1899549dd132c3a3d8aa1ade3e3db1d1f88d6d71de18d19bad581c7445a`; archive SHA256 `68d6944547fa34607365f592f280744f980b967211dc5be6b8abc26703f5e3f4`.
- [import-v2.json.gz](import-v2.json.gz): original report SHA256 `c5d69d940c56b570946748b0a8d587fb0507cc91fd15dd3d2e01cfe971bd1507`; archive SHA256 `81d14f67779c049133f759a22ce322c582a47ea5f44cb2e6253f46a856988ae7`.
- [solo-v1.json.gz](solo-v1.json.gz): original report SHA256 `3bd3d36d753b5dc6c5f5abfce321b8c69b7011556d838e95c9bc2e272d7b8017`; archive SHA256 `d770fb35da4d4282e039612325aa5a9d8975ff36f6195b98dd93177ff2664452`.
