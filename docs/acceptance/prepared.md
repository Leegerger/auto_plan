# Prepared acceptance assets

These synthetic inputs and independent references are frozen for implementation checks. They have no user confirmation. Passing an adapter check or an automated browser run does not establish final business acceptance.

`frozen/manifest.json` lists 37 scenarios. Every pointer names a canonical UTF-8 JSON document compressed with deterministic gzip. Its SHA-256 is the hash of the uncompressed canonical bytes. Each scenario contains a complete source snapshot, separate reference snapshot, action, public first-import payload, explicit scope, logical input/revision/adoption tokens, exact totals and all 33 objective coordinates. The source, reference and action are separate assets even where their content is equal. The adopted baseline remains separate from the reference used to judge quality.

| Family | Recipe counts | Independent reference |
| --- | --- | --- |
| Loose and all replan | 10, 20, 40 | 420,000 on-time pieces; switch counts 418, 418, 4,182 |
| Key Recipe on one or two lines | 10, 20, 40 | Same coverage; the 10-Recipe one-line case consolidates R00 and has 209 switches |
| Exact capacity boundary | 10, 20, 40 | 420,210 / 420,210 / 422,310 pieces, zero gaps |
| Directed cross-day switch | 10, 20, 40 | One two-hour return split across adjacent dates; counted once |
| Near and far increase/decrease | 10, 20, 40 | Four separate changes of 40 pieces; near cost 120, far cost 40 |
| Actual rolling new week | 10, 20, 40 | 420,000 on-time pieces; 420 / 420 / 4,200 switches; new-week cost zero |
| Scoped insufficient capacity | 20 | Final-week on-time and final gaps of 40, output 420,210; a correctness case |
| Combined A, partial and confirmed | Small companion | Cross-line actual plus remainder, 931/1000 input-month yield, CT 0.5, Holidays, partial material, late shipping and positive/zero locks |
| Combined B, ban and confirmed | Small companion | Holiday opening, two separate ceilings on one output date, WIP once, executed lock and a zero-input-share material ban |

All scale families use 18 active lines, 40 active models, 105 production dates and all 15 dated demand weeks. October 2026 through February 2027 parameters are explicit. Ordinary capabilities allow three through six lines, apart from the named key Recipe. Positive switches take one hour ascending by Recipe code and two descending. Initial Recipe states are known. The normal window is 2026-10-12 through 2027-01-24, at Beijing Monday 08:00. The rolling window starts 2026-10-19 and ends 2027-01-31.

The arithmetic does not call the product evaluator or solver. Every model normally contributes 100 pieces per day and 700 per week. For a line with `m` Recipes, `p` model segments, `A = sum(m-1)` and `B = count(m>1)`, fresh-window switches equal `105A+104B`; hours equal `105A+208B`. The 10-Recipe placement splits each Recipe across adjacent allowed lines, so all 18 lines remain active.

Boundary maintenance is the integer `24 - production hours - switch hours`. For fixed Recipe membership and switch allocation with integer production budget `P`, ceiling capacity permits at most `100P+m-1` pieces. The witness adds `m-1` pieces to the first Recipe and allocates each Recipe `(Q-1+1/m)/100` hours. For R40/L00, the quantities 102/100/100 use exact hours 76/75, 149/150 and 149/150. This proves only the stated fixed-membership bound. Broad replan references are feasible upper bounds, not global optima.

Each local scenario changes only M00 on one named Sunday and its demand week. The near Sunday is 2026-10-25; the far Sunday is 2026-12-06. Both signs preserve zero gaps and unnecessary output. The whole-plan weekly absolute change rate is 1/700. The rolling source retains the original fourteen dated weeks and their IDs/order/switches. Its baseline stops at 2027-01-24; it does not declare a zero baseline for the new week. Earlier demand history remains explicit and excluded from the current fifteen demand weeks.

Combined A's 1,072 physical input pieces produce two independently rounded portions of 500. Ownership is 643/429 input pieces. A 428 material allowance covers 399 of 400 later-week output pieces. Totals are expected 1,000, on time 899, late 100, gap/pending 1. Allowing 429 raises on-time coverage to 900 and removes the gap. Combined B's three WIP pieces produce two output pieces once. One executed physical input produces two separately rounded CT portions, both on Tuesday. The banned variant totals four expected, two on time, one late and one pending/gap. Removing the ban raises on-time coverage to three.

Generate a new directory and compare its hashes before measurement:

```bash
python scripts/acceptance/prepare.py --output /tmp/acceptance-freeze-new
PYTHONPATH=src:. python scripts/acceptance/validate.py --scenario all --output /tmp/acceptance-adapter-new
PYTHONPATH=src:. python -m pytest -q scripts/acceptance/test_prepared.py scripts/acceptance/test_compare.py
```

The validator uses public business APIs with explicit authentication/persistence adapters. It checks every full objective numerically as rational values. It writes every observed response and a report, including failures. Its seconds are preparation diagnostics and do not claim the 1/3/5/30/120-second acceptance gates. Existing evaluator diagnostic fixtures are unchanged.

A combined full-scale distribution with confirmations, Holidays, facts and locks still needs a representative independently confirmed reference. The small companions provide exact rule arithmetic, not that missing scale or timing observation.
