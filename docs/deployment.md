# Deployment, upgrades and recovery

This release runs one API process, one job controller and its bounded compute children in one image, container and replica. Oracle is the only persistent store. The final base is `harbor.cminl.oa:8443/uv/instantclient:python3.14@sha256:484bf4b4f9b4247edd06710a2e325cdf9431b83a028df5c41bea3516d29ced57`. The image installs `uv.lock` with `--frozen --no-dev`, uses Python 3.14.7, includes the compiled frontend and runs as UID 10001. Build output and runtime logs go to stdout. Run no additional Uvicorn workers or replicas.

## Build and configure

Run `scripts/build-image.sh RELEASE_ID IMAGE_TAG` from a clean release checkout with Node 22 or newer and Docker available. The script installs the frontend lockfile, builds it, then builds the image. The Python layer uses the required pinned base. Standard `HTTP_PROXY` and `HTTPS_PROXY` build arguments support package download through the operator's network. They contain no application configuration. Never pass Oracle credentials or company passwords as build arguments. `.dockerignore` admits only source, migrations, lockfiles and generated frontend assets. Agent metadata, local configuration and credentials stay outside the build context.

Record the source commit, immutable image ID/digest, `runtime/release.json`, both lockfile hashes and assigned CPU/memory limits with each deployment. Use an immutable registry tag and digest. `deploy/runtime.env.example` lists non-secret settings. Provide required Oracle DSN/password, initial administrator and the mounted company CA through the runtime secret provider. Keep the runtime environment file outside the checkout and build context. Disable fake authentication for production. The application rejects fake authentication outside PLAN_TEST, and entrypoint configuration failures print a fixed message without Pydantic inputs.

An example resource allocation is 4 CPUs and 8 GiB, subject to the site's measured load. This is a configuration example, not a certified capacity. The default worker count is `max(0, floor(allocated CPUs) - 1)`, bounded by affinity and cgroup quota. Each compute child gets one native thread and no Oracle credentials or handles. API and job persistence share the single configured Oracle pool, default maximum 10. With fewer than 2 allocated CPUs the default has no compute workers. Set `AUTO_PLAN_JOB_WORKERS` only to reduce that resource-bound maximum.

After the operator has prepared runtime secrets and applied the explicit migrations, an authorized deployment can use the following command. Building and testing this release does not authorize running it against production.

```bash
docker run --name auto-plan --restart unless-stopped \
  --cpus 4 --memory 8g --pids-limit 256 --stop-timeout 30 \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --env-file /run/auto-plan/runtime.env \
  --mount type=bind,src=/run/auto-plan/company-ca.crt,dst=/run/auto-plan/company-ca.crt,readonly \
  --publish 127.0.0.1:8000:8000 IMAGE@sha256:RELEASE_DIGEST
```

The HTTPS proxy must retain `/auto_plan`, including on API, static and deep route requests. `deploy/nginx.conf.example` deliberately has no trailing slash after the upstream in `proxy_pass`, so it preserves the prefix. Set `AUTO_PLAN_PUBLIC_ORIGIN` to the exact external HTTPS origin and `AUTO_PLAN_BASE_PATH` to that retained prefix. Set `AUTO_PLAN_PROXY_TRUSTED_IPS` to the actual proxy address, never an unreviewed wildcard. Cookies are Secure, HttpOnly and SameSite=Strict. Write requests need the exact Origin and `X-Auto-Plan-Request: 1`.

## Explicit migration ledger

Startup never creates or alters schema objects. The image accepts complete schema version 5 only, with the exact shipped checksums for migrations 001 through 005 and status APPLIED or BASELINED. Missing, partial, changed, older or newer records refuse business access. Migration 000 explicitly installs `AP_SCHEMA_MIGRATION` and `AP_SCHEMA_EVENT`; it is the ledger bootstrap and is not a business schema version.

Stop the application and exclude other writers before any migration or inspected baseline. The maintainer records an Oracle recovery point/SCN or the DBA's verifiable recovery record, its timestamp, backup/recovery availability, operator, old/new image digests, release and source commit. The `--recovery-point` string is an operator record, not an automatic Oracle restore-point creation or a proof that recovery is available. Do not put passwords, DSNs or customer records in this identifier.

For a fresh installation, run migration 000 explicitly, then 001 through 005 in order. Supply the same required metadata to every action. The runtime secret environment must already be available to these commands.

```bash
python -m auto_plan.migrate --expected-user PLAN_TEST --bootstrap-ledger \
  --recovery-point DBA_RECORD --operator OPERATOR --release RELEASE --source-revision COMMIT
python -m auto_plan.migrate migrations/001_access.sql --expected-user PLAN_TEST \
  --recovery-point DBA_RECORD --operator OPERATOR --release RELEASE --source-revision COMMIT
```

Repeat the second command for `002_inputs.sql`, `003_plans.sql`, `004_demands.sql` and `005_jobs.sql`. Use the operator-approved expected production schema only in an authorized production maintenance window. The CLI verifies the configured and connected account before executing DDL. It records APPLYING before the first statement, the acknowledged completed statement count and APPLIED only after completion. Repeating an acknowledged exact migration is a no-op. An incomplete record or checksum mismatch refuses automatic retry.

For an existing installation without a ledger, first compare the complete installed DDL with the shipped scripts, including column types, constraints, enabled immutable triggers and indexes. Preserve the comparison as an operator artifact with the recovery record. Bootstrap only the ledger, then run `--baseline-through N` with the same metadata, where N is the actual complete installed prefix. This explicit action checks expected object validity/enabled triggers and the available input/plan/demand/job consistency checks before recording BASELINED checksums. It does not infer a baseline from table names, repair data or reexecute old DDL. Continue later additive migrations normally. Never claim a partially installed migration is a complete prefix.

Oracle DDL commits. A power/network loss can leave a completed statement beyond the last acknowledged count. On FAILED or APPLYING, keep the service stopped, inspect both the ledger and actual objects, and compare each statement with the release script. Do not delete the ledger row or blindly rerun a file. The DBA must restore the recorded recovery point or approve and document a reviewed forward repair in a new versioned migration. If ledger bootstrap itself fails, inspect its two objects before recovery; startup still refuses access without a complete compatible ledger. Preserve failure evidence and the original recovery record.

## Startup and stop

`GET PREFIX/api/health` is liveness and reports `alive`; it is not permission to write. `GET PREFIX/api/ready` returns 200 only after the schema/checksum, input/context/plan/adoption/idempotency/demand/job checks, old-session invalidation and running-job recovery succeed. Failure returns 503 with a fixed named reason and useful instructions, without exception text, SQL, documents or configuration. Static assets and the explanatory page remain reachable. Authentication remains reachable after successful session recovery; business API access stays closed, including GET routes that otherwise continue input months or extend candidate retention. The page displays the safe readiness reason.

The first SIGTERM closes admission immediately and starts cooperative worker stop. One absolute 30-second deadline covers active requests, worker/controller stop and resource cleanup. A separate watchdog starts forced finalization two seconds before that same deadline, reserving time for group reaping and container exit even when synchronous Oracle or cleanup calls block. It kills only owned worker process groups, reaps available children and exits. The next successful startup invalidates old sessions and marks still-running durable jobs interrupted. Original frozen inputs remain available for explicit rerun. No automatic rerun is inferred from a crash. The container stop timeout is 30 seconds; do not configure a shorter orchestrator grace period.

## Upgrade and rollback

Apply additive expansions before replacing the image. Keep the old image, its source/lockfiles, the schema compatibility range, recovery record and test evidence. Run readiness and the focused public API/browser checks before reopening writes. New release records must explicitly name the preceding tested image and whether it accepts the resulting schema. This is the first packaged release; no earlier production image has a certified rollback record.

If both image compatibility records and tests show that the previous image can read/write the current additive schema, stop the new container and restart that immutable previous image with the same runtime configuration. Recheck readiness and sessions. If a future contraction or data transformation breaks that compatibility, an image rollback alone is insufficient. Keep writes closed and have the DBA restore Oracle to the recorded approved point before the matching old image starts. This project does not implement a separate backup system, shared destructive restore or automatic schema downgrade.

Use the existing Data Guard/RMAN and the user-verified seven-day point-in-time recovery boundary. Permanently retained adopted plans are different from that seven-day backup window. Recovery can lose data after the selected recoverable point. The earlier one-hour recovery observation is not a universal recovery-time guarantee. A spare computer's one-hour work recovery depends on Oracle already being available and the work having been saved there. Unwritten browser data remains local.

## Storage and acceptance evidence

An administrator can request `GET PREFIX/api/admin/storage`. It reports adoption count, compressed unique adopted snapshot bytes, allocated app table/index/LOB bytes and schema tablespace quotas. The adoption warning threshold defaults to 1 GiB and is configurable. Quotas are not a promise of shared tablespace free space. Ask the DBA to monitor actual tablespace free space and backup growth too. Adopted history/audit is permanent. The 30-day nonadopted recovery and candidate-view retention policies do not authorize deleting immutable revision blobs; this release retains those physical blobs.

Run `pytest` for injected-boundary application checks and explicit `AUTO_PLAN_TEST_ORACLE=1` PLAN_TEST cases for real concurrency and controlled app-object faults. The latter invalidate this test app's sessions and write only its own AP_* or disposable AP_TEST objects. Do not run destructive instance/storage/recovery faults on the database shared with production. See the frozen acceptance assets and report under `docs/acceptance/` and `docs/validation/`. Synthetic browser contexts do not establish twenty real accounts, five humans, company revocation, user-confirmed references or final business acceptance. Every measured repeat, including the first, must meet the unchanged 1/3/5/30/120-second gates.
