Rechecked `git diff 6c479bf...e159767` at committed fix `e159767da9d4dcd0c23c340816fa9f5ec6f72d64` in the isolated review-fix worktree. Read the complete new helper and selected-demand summary. The backend source tree remains `5a24fa53c385525fd9bf1d4affbab26159a47666`.

Both Standards findings are resolved.

- `frontend/src/components/DemandAttention.vue:40` now gates computed amounts on `computation_complete`. Lines 79-84 apply it to all six computed summary quantities, while line 78 retains known weekly demand and the parameter links remain available. This satisfies `docs/plan-preview.md:24` and `docs/frontend-design.md:77`. The new browser case at `frontend/tests/demand-attention.spec.ts:13` checks an actual missing-Cycle-Time public response, all six unknown labels, maintenance navigation and retained local intent.
- `frontend/src/demandRequests.ts:6` and `:19` centralize common request fields and the actor/version/time fingerprint. Trial and comparison call sites keep their mode and line selection explicit. The reported Duplicated Code judgement call is resolved without adding unused options.

No remaining or new documented-standard violations or actionable baseline smells found in this fix. Recheck used committed source and the supplied verification results; no additional probes or test runs were performed.
