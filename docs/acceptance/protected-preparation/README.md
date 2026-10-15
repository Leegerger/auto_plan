# Protected source preparation

`scripts/acceptance/protected.py` prepares the four existing frozen scenarios
`combined-a-confirmed`, `combined-a-partial`, `combined-b-confirmed` and
`combined-b-ban` through public APIs. It pins the immutable V1 manifest SHA256
`33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4`.
It does not change the frozen source, reference, actions or timing gates.

The target must be a fresh, explicitly disposable adapter or `AP_TEST_*` Oracle
namespace at the exact frozen clock. The preparer imports the complete original
source and demand file, replaces the Plan with its exact frozen witness, applies
all positive and zero locks, then adopts that witness. Confirmed scenarios can
establish their baseline directly.

The partial scenario temporarily changes only the existing `partial-material`
confirmation quota from 428 to 429. The ban scenario temporarily revokes only the
existing `whole-week-ban` confirmation. These declared preparation changes allow
the original protected witness to establish an executable adopted baseline.
After adoption the preparer restores the exact original confirmation through the
maintained-input API. The final source may retain its intended pending output or
forbidden condition. No execution fact, WIP, demand or reference is fabricated.

The final check compares the complete frozen source, unchanged immutable
adoption, original input revision and complete original demand-file history.
Every original demand row, including zeros, retains its deterministic file/row
identity and ordinal. `state.json`, `adoption.json`, `demand-files.json` and the
operation journal retain those observations outside timing.

Each Plan mutation retains its original UUID before submission and verifies the
actual committed result through that UUID. A lost reply reconciles that original
UUID; an unresolved result stops preparation. Confirmation edits use maintained
inputs' optimistic-version protocol, which has no Plan UUID. An unknown input
write stops with its original command preserved for reconciliation.

Use a new output directory and a non-secret config naming the actual scenario,
HTTPS origin, application prefix, disposable target and environment. Credentials
enter as an `accounts` JSON object through private stdin from the authorized
in-memory provider, never files, command-line arguments or logs:

```bash
PYTHONPATH=src:. python scripts/acceptance/protected.py --config /tmp/public-configs/combined-a-partial-empty.json --output /tmp/public-prepared/combined-a-partial-new
```

`run-config.json` records the actual prepared tokens and frozen fixture path.
Timed acceptance still needs its own full source assertions, current tokens,
complete five repeats and current-run actor/load evidence. Preparation is not a
timed pass and does not replace the pending-result contract for negative cases.

The focused public API suite passed all four scenarios in 2.60 seconds after
retained feature/adapter/recipe failures. It exercises explicit memory stores,
full source/history assertions and an actually committed adoption reply lost at
the public caller boundary. It makes no Oracle, browser, concurrency or SLA claim:

```bash
PYTHONPATH=src:. python -m pytest -q scripts/acceptance/test_protected.py
```
