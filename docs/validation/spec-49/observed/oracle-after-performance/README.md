# Actual Oracle composition after the performance fix

The complete public composition passed again after the residual-index change: one case in 13.13 seconds, production source tree `5a24fa53c385525fd9bf1d4affbab26159a47666`. Test source is `tests/test_oracle_spec49_acceptance.py`; helper pin is `99a79f5`, containing fix `62b7aac` and the owner's clock source.

The independent expectations and public sequence are unchanged from [the first composite report](../oracle/README.md): actual remaining four hours permits 834 input, the retained goal needs 166, exact continuity and state/lock/goal commit atomically, two competing same-base continuations yield HTTP 200/409, original retry/verify agrees, full preview/adoption succeeds, actual pending facts remain pending, current 50% yield history restore produces 500 output and goal balance 500, raw history stays literal, viewers cannot mutate, and fresh store/application instances reopen the exact result.

The namespace `AP_TEST_266C07` was fully dropped. `report.json.gz`, `environment.json.gz`, `expected.json.gz`, `cleanup.json.gz`, complete public bodies and their source manifest are retained here. `compression.json` explains filename/byte mappings. This is actual PLAN_TEST correctness evidence, not a quiet latency SLA, a container restart or production acceptance. Authentication records and connection values are excluded.
