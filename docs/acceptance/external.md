# External acceptance runner

The external runner uses Chromium against a selected HTTPS application. It never starts a replacement server, stores passwords/session cookies, or calls a private write endpoint. The separate `adapter_server.py` provides an explicitly synthetic local preparation target for checking the scripts.

Copy `external.example.json` to a non-secret run configuration and fill its environment fields. For an authorized disposable Oracle run, set `target.kind` to `PLAN_TEST` and record the exact `AP_TEST_` namespace, origin, retained prefix, image digest, source commit, migrations, runtime/native/browser versions, hardware, network/proxy path, actual cgroup CPU/memory allocation, workers/threads, pool maximum/observed use and peak resources. A target declaration records the operator's attestation; it does not independently prove where the server connects.

The runner requires the complete frozen source inputs, plan, execution context, baseline, scope and exact production clock. It rejects changed hashes or bindings. Use the existing injected clock at the test-infrastructure boundary. No production configuration switch or clock bypass is provided. Record any namespace/clock wrapper separately from actual product entrypoint observations.

For an empty disposable target, `prepare_external.py` validates the public first import, applies it with an operation UUID, then previews and explicitly adopts the frozen source plan. This synthetic test adoption establishes the baseline. It writes each UUID before submission and verifies the original UUID after a transport failure. It requires one authorized account through private stdin and writes a non-secret `run-config.json` with the actual tokens. It refuses existing plans, rolling history and protected companions that need additional public context actions. It never resets or drops a schema.

```bash
# Supply {"accounts":[{"username":"...","password":"..."}]} through a private stdin channel.
PYTHONPATH=src:. python scripts/acceptance/prepare_external.py --config /tmp/run.json --output /tmp/prepared-new
# Supply a fresh in-memory login payload again. No credential file is required.
PLAYWRIGHT_BROWSERS_PATH=/tmp/auto-plan-playwright PYTHONPATH=src:. node frontend/tests/acceptance/run.mjs --config /tmp/prepared-new/run-config.json --output /tmp/observed-solo-new --mode solo --operations selection,week,day,single_cell,batch_preview,shared_save
```

For the five-editor run, provide exactly five distinct authorized test accounts through stdin and use `--mode five_automated_editors_one_computation`. One of those editors owns the single computation. Four additional contexts continuously preview a one-piece reduction, preview restoration, and save the pair with zero net quantity change. Their scopes use different models/lines from the measured edit. The report states this schedule, each acknowledged revision and every action interval. It does not describe the automated contexts as humans or an independent sixth editor. This schedule avoids changing the frozen computation reference while still exercising local edits, full preview and acknowledged Oracle saves. Intersecting conflicts and real human behavior need separate observations.

Every selected operation runs five times, including the first. Gates remain 1 second for selection and single-cell feedback, 3 for week/day views, 5 for batch preview/import validation/shared save, 30 for a local candidate and 120 for new-week/all-replan candidates. The browser captures the actual click or change event. Completion requires the useful result to appear and finish rendering. A progress message never ends candidate timing. Save requires a committed acknowledgment. Candidate timing includes submission, queue/network, polling, content request and displayed output tables. A further public evaluation against the frozen context verifies executable/protection state and the complete objective, with that verification span reported separately.

Use `--operations local` with a frozen near/far local scenario. The fixed batch action clears M00 and M02 on the final date of a loose source, with independently expected output 419,800 and gap 200. Single-cell edits should use the final-date M00 segment. Both previews remain local.

Use `--operations new_week` only with a prepared rolling scenario, and `--operations all_replan` with its matching broad-scope scenario. Use `--operations import_validation` alone in solo mode on a fresh empty target with `workbook` in the configuration. Download that target's public `/api/initialization/template.xlsx`, then populate it without guessing its columns:

```bash
PYTHONPATH=src:. python scripts/acceptance/workbook.py --scenario r20-loose --template /tmp/standard.xlsx --output /tmp/frozen-source.xlsx
```

The initial-import five-editor load remains `not_run` because an empty first-import target has no shared edit state. A coordinated independent target or another agreed import scenario is required for that observation.

The report retains every repeat, first-repeat marker, click/feedback/full-render times, gate result, request spans, exact vectors, candidate/stage bounds, commit/job/candidate IDs, screenshots, hashes and environment data. It records missing external prerequisites as `not_run`. The runner exits unsuccessfully on a failed automated gate. Reference confirmation, real accounts/company authentication, twenty-account permissions and five-human business acceptance remain `not_run` regardless of synthetic results. Run the five-editor command separately from solo; both reports are required.

An optional `AUTO_PLAN_BROWSER_EXTERNAL_URL` disables the fixture web server in the existing Playwright configuration. Existing fixture tests still have their own fixture requirements. The standalone acceptance runner above supplies the complete external flow.

To check the runner locally after the frontend build, start this explicitly adapter-backed server, then select `target.kind: adapter`, port 8776, logical tokens 1/1/1, and synthetic accounts ACCEPTANCE0 through ACCEPTANCE4 using the public test password from `tests/support.py` through stdin:

```bash
PYTHONPATH=src:. python scripts/acceptance/adapter_server.py --scenario r20-loose --port 8776
```

Stop and remove disposable test infrastructure after evidence collection. Preserve failure artifacts. None of these commands authorizes production deployment, real worksite import, shared-instance restore or storage exhaustion.

A separate twenty-account role command accepts a private stdin object with exactly twenty distinct entries. Each entry has `username`, `password`, expected `roles`, and `active`. Prepare those grants on the selected disposable target first. The command checks login, current roles, business reads, editor preview, denied saves for noneditors, administrator reads and logout. `--save-probes` also performs acknowledged zero-change saves for editors and records their UUIDs/revisions. It writes account indices and role labels, never passwords, cookies or login bodies. Grant setup, immediate local revocation and company-side deactivation remain separate observations.

```bash
python scripts/acceptance/roles.py --config /tmp/run.json --output /tmp/twenty-synthetic-new
```

The local adapter server supplies MATRIX00 through MATRIX19. Roles repeat editor, viewer, administrator, editor+administrator. MATRIX18 has no grant and MATRIX19 is inactive. These are synthetic role fixtures, not twenty company accounts.
