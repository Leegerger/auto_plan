# Local demand-change companions

These scripts define 12 versioned companions for the near/far increase/decrease cases at 10, 20 and 40 Recipes. V1 remains immutable. The companion changes only the source/reference demand-list representation to the public replay's model/week order. Business quantities, Plan/baseline witnesses, execution, coverage, scope, full 33-coordinate reference, actions, fixed gates and five repeats including the first remain unchanged.

Lane #9 established the old direct changed-demand recipe's behavioral failure, then passed three focused fixture/public preparation checks in 129.17 seconds. Static implementation preceded those runtime checks, so this does not claim a completed red-before-code TDD loop. A separate HTTP408/proxy5xx classification regression failed five cases before the narrow fix and passed all five afterward in 0.54 seconds. Frozen generation completed after those checks. Public preparation uses explicit memory persistence adapters in these tests; no Oracle, deployed timing or five-editor pass is claimed. Logs and exact chronology remain outside the repo in the local V2 handoff.

The original direct changed-demand import/adoption recipe failed for r20-near-decrease. The captured preview has capacity-valid and complete evaluation, 13 confirmation-pending errors, 40 unallocated output and 520 pending/gap. Its exact-source preview is retained outside the repo at `/tmp/auto-plan-spec-23/acceptance-packaged38-prepare-local-source-probe-v2/exact-source-preview.json`, SHA256 `f85caaab3dc9b544ea1766522191af7273d2e22898ca4775d0a2386bc53e05dc`. The other 11 direct recipes have the same static cross-week confirmation diagnosis; they have not been relabeled as observed runtime failures.

## Frozen generation

After the coordinator grants a runtime window, generate into a new directory:

```bash
PYTHONPATH=src:. /tmp/auto-plan-spec-23/venv13/bin/python scripts/acceptance/local_v2.py freeze --v1 docs/acceptance/frozen --output docs/acceptance/local-v2/frozen
```

The generator pins the immutable V1 manifest SHA256 `33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4`. It emits IDs `r10-near-increase-v2`, `r10-near-decrease-v2`, `r10-far-increase-v2`, `r10-far-decrease-v2` and the same four names for r20 and r40. Every entry maps its exact predecessor manifest/source/reference/action/import hashes. Its `import` is explicitly the matching original-demand loose import. Its `demand_replacement` freezes the complete same-day 600-row request. No old-ID-only alias is supported.

## Public preparation

Use a fresh explicitly disposable target at the exact frozen clock. The non-secret config must name the actual V2 scenario, HTTPS origin, application prefix, disposable target and reproducible environment. Credentials enter as one `accounts` JSON object through private stdin from the authorized in-memory provider. They must never enter files, argv or logs.

```bash
PYTHONPATH=src:. /tmp/auto-plan-spec-23/venv13/bin/python scripts/acceptance/local_v2.py prepare --config /tmp/public-configs/r20-near-decrease-v2-empty.json --fixtures docs/acceptance/local-v2/frozen --output /tmp/public-prepared/r20-near-decrease-v2-new
```

The preparer uses authenticated public APIs only. It imports the matching loose source with all 600 demands at 700 pieces, then previews and adopts the complete exact original Plan. It checks the actual immutable adoption and its full coverage. It then previews and applies a whole-file demand replacement for 2026-10-12. The replacement contains all 600 original pairs, with only M00 at 660 or 740 in the chosen near/far week. A same-day one-row -40 file would replace the initial contribution and is invalid. A later-day delta would need a different declared clock/source.

All three mutations retain their original UUID, complete public command, acknowledgment and original-UUID verification. Unknown replies reconcile that original UUID. Both initial and replacement demand files, their ordinals/IDs/versions and the original initialization lineage remain intact. Complete current source and unchanged original adoption are read again after replacement and checked with the existing frozen-source checker. The resulting unrepaired Plan may have the expected confirmation-pending evaluation; the original adopted baseline was established before the demand change. No confirmations or repaired adopted baseline are introduced.

Preparation passes only after those assertions. Failure preserves its phase, original operations and observed business artifacts. `run-config.json` uses the actual live tokens, explicit V2 scenario and absolute `fixtures` path. Every official timed run must still record its own current tokens/source/provenance and all five repeats. The existing Node runner accepts this explicit fixture manifest without source edits:

```bash
node frontend/tests/acceptance/run.mjs --config /tmp/public-prepared/r20-near-decrease-v2-new/run-config.json --output /tmp/local-v2-solo-new --mode solo --operations local
```

That command also consumes authorized credentials through private stdin. It requires the coordinator's runtime window, passed public preparation and complete runtime environment. It is a future command, not execution evidence.

The historical follow-up queue/driver is pinned to V1 and only has rolling-successor support. It must gain explicit local-successor mappings before it can schedule these companions. Do not swap an ID or borrow a rolling capability. Five-editor scheduling/current-run proof remains internal work. The three initial-import-under-load slots remain pending the user's interpretation decision; this local companion does not resolve or reclassify them.

## Focused checks

```bash
PYTHONPATH=src:. /tmp/auto-plan-spec-23/venv13/bin/python -m pytest -q scripts/acceptance/test_local_v2.py
```

The tests exercise generated public fixture artifacts and authentic HTTP initialization, exact adoption, whole-file demand replacement, original-UUID verification, immutable file history, complete source equality and public reference evaluation. A lost demand-write reply is injected at the HTTP caller boundary after the real public route commits, then reconciled through its original public verification route. The explicit memory persistence adapter makes no Oracle concurrency or performance claim.
