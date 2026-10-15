# Audited runner preparation

The action audit found that the earlier week measurement could select the already selected week 15. The earlier shared-save action saved an unchanged 100-piece row. Those observations remain preserved with their original reports and cannot establish distinct week navigation or persistence of a modified plan. The earlier single-cell five failures at 7.347, 7.318, 7.585, 7.541 and 7.269 seconds remain failures against the one-second gate. This runner correction does not recalculate them.

The corrected navigation regression first failed with week 15 where week 1 was required, then passed after the helper established distinct week and populated day transitions. Its selection assertions also require the dependent selected summary and quantity form to change. The frontend build passed after adding the helper's TypeScript declarations.

Five Node contract checks passed. They reject changed manual action identities/scopes, wrong cleared rows or outside witnesses, incorrect dated coverage, scalar-only preservation and failures after a timed pass. Six Python checks passed, including a real public standard-workbook roundtrip with 4,200 segments and unchanged shared state. That roundtrip first caught missing required ISO week labels in the workbook builder. A second failure showed that the standard import inserts switch order slots; the final assertion checks the exact uploaded source orders and the complete prepared production sequence, while allowing those certificate slots.

The public memory-adapter checks are preparation evidence. They exclude Oracle, packaged-container timing, company authentication, real accounts, five humans and confirmed business references. The revised five-editor schedule and candidate/job cleanup still require their coordinated external observations. Missing prerequisites remain `not_run`. The manifest, independent reference vectors and 1/3/5/30/120-second gates are unchanged.

The revised edited-save browser flow is not yet verified. Its first memory-adapter attempt retained five setup failures with null measured durations and completed undo/full-source cleanup. The result-summary locator also matched the adopted-history output. A second attempt retained the same setup failure and was stopped by the integration owner before all five measurements; its annotated report marks interrupted/unstarted slots `not_run` and preserves the raw report separately. Neither attempt submitted a save. The final locator binds the preview directly to the shared view and has static checks only. The integration owner must verify the real modified save, immutable UUID/revision binding, fresh read and complete source restoration on the final image before crediting any save result.

The report hashes the runner, each comparison/navigation/workbook helper, the run configuration and frozen assets separately. It retains Node-observed timing and adds a separate browser timestamp after useful-result checks and two animation frames. The browser timestamp is diagnostic and does not replace the gate duration or establish a driver-overhead correction.

Run the focused checks with the project's installed dependencies:

```bash
node frontend/tests/acceptance/contracts.test.mjs
PYTHONPATH=src:. python -m pytest -q scripts/acceptance/test_assertions.py scripts/acceptance/test_compare.py
cd frontend
AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE=1 PYTHONPATH=/absolute/worktree/src:/absolute/worktree npm run test:e2e -- tests/acceptance/navigation.spec.ts
```

The browser command also needs the configured Playwright browser cache and Python environment. The acceptance adapter must import the tested worktree through its explicit `PYTHONPATH`; a shared editable environment can otherwise resolve an older package. Detailed red/green logs and the full failed observations are retained in the implementation handoff outside the repository.
