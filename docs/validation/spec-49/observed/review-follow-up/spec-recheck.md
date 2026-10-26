Spec recheck of `6c479bf...e159767da9d4dcd0c23c340816fa9f5ec6f72d64`: the P2 finding is resolved. No remaining or new spec finding identified in this fix.

`frontend/src/components/DemandAttention.vue:40` now gates computed quantities on `computation_complete` and a non-null value. Lines 79–84 apply it to all six selected-summary amounts, including explicit unknown states for pending material, pending shipping and late coverage. Known weekly demand and the Cycle Time maintenance link remain available. This satisfies §5: "计算不完整时不能将未知数量展示为确定的零值。"

The new browser case in `frontend/tests/demand-attention.spec.ts:13` checks the actual public incomplete-source fixture, all six visible unknown labels, absence of false zero labels, retained selection/forms and the maintenance return. Parent reports this case and existing incomplete-late coverage passed together, 2 tests in 10.2 seconds; I inspected the assertions and did not rerun them.

The shared request helpers preserve the trial/comparison payload fields and invalidation fingerprint, with trial mode/line and comparison on-time mode still explicit at each caller. No backend rule or persistence change is present. Final broader browser results and derived acceptance evidence remain the parent's verification work.
