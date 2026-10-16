/** Explicit full-r20 correctness successor; existing canonical payloads stay byte exact. */
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { gunzipSync } from 'node:zlib'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
export const FULL_PRIVATE_ID = 'r20-loose-private-five-context-v1'
export function writeFullPrivateFixture(root, directory) {
  const frozen = resolve(root, 'docs/acceptance/frozen'), raw = readFileSync(resolve(frozen, 'manifest.json'))
  assert.notEqual(resolve(directory), frozen, 'Canonical fixtures are read-only.')
  assert.deepEqual(readdirSync(directory), [], 'Provide a new empty owned fixture directory.')
  assert.equal(sha(raw), '33e765b2cc22a28116d0416f04cc05cd628f5239183160be32a143035d29b0f4')
  const manifest = JSON.parse(raw), original = manifest.scenarios.find(row => row.id === 'r20-loose')
  assert.equal(original.source.sha256, 'd019627a21ca1507f9145aac1575c55fa03c8be52838d0f47c85088a8703d909')
  assert.equal(original.reference.sha256, original.source.sha256)
  assert.equal(original.action.sha256, '7f524927f3aa5d0df789c60c938557530b26322addb90ad5b4c14bf8a7ae2f4c')
  const loaded = {}
  for (const key of ['source', 'reference', 'action', 'import']) {
    const compressed = readFileSync(resolve(frozen, original[key].path)), bytes = gunzipSync(compressed)
    assert.equal(sha(bytes), original[key].sha256)
    writeFileSync(resolve(directory, original[key].path), compressed)
    loaded[key] = JSON.parse(bytes)
  }
  const source = loaded.source
  assert.equal(source.plan.segments.length, 4200); assert.equal(source.plan.switches.length, 418)
  assert.equal(source.inputs.masters.models.length, 40); assert.equal(source.inputs.masters.lines.length, 18)
  assert.equal(source.inputs.business.demands.length, 600)
  const row = source.plan.segments.find(row => row.id === 'S-2027-01-24-M39')
  assert.deepEqual(row, { date: '2027-01-24', hours: '1', id: 'S-2027-01-24-M39', line_code: 'L01', model_code: 'M39', order: 5, quantity: 100 })
  writeFileSync(resolve(directory, 'predecessor-manifest.json'), raw)
  const scenario = { ...structuredClone(original), id: FULL_PRIVATE_ID,
    predecessor: { scenario: original.id, manifest: { path: 'predecessor-manifest.json', sha256: sha(raw) } } }
  const effects = [['CAP', 'L01', '2027-01-24'], ...source.inputs.masters.lines.map(line => ['CELL', '2027-01-24', line.code, 'M39']), ...source.inputs.business.demands.filter(row => row.model_code === 'M39').map(row => ['REQ', 'M39', row.week])]
  assert.equal(effects.length, 34)
  const objective = Array(30).fill('0').concat(['418', '1', '0']); objective[14] = objective[29] = '1'
  const declaration = { schema_version: 1, kind: 'private_local_drafts_v1', scenario: scenario.id,
    source_manifest_sha256: sha(raw), source_fixture_sha256: scenario.source.sha256,
    reference_sha256: scenario.reference.sha256, action_sha256: scenario.action.sha256,
    targets: Array.from({ length: 5 }, (_, editor) => ({ editor, segment_id: row.id,
      operation: { kind: 'change_quantity', quantity: 99 }, expected: { quantity: 99,
        hours_lower_exclusive: '49/50', hours_upper_inclusive: '18', weekly_count: 600, objective: [...objective], effects: structuredClone(effects),
        totals: { expected: 419999, on_time: 419999, late: 0, gap: 1, pending: 0, unallocated: 0, outside_window: 0 },
        changed_week: { model_code: 'M39', week: '2027-01-18', demand: 700, on_time: 699, gap: 1 } },
      restoration: 'undo_complete_action_to_origin' })) }
  const bytes = Buffer.from(JSON.stringify(declaration))
  writeFileSync(resolve(directory, 'editing-workload.json'), bytes)
  scenario.editing_workload = { path: 'editing-workload.json', encoding: 'json', sha256: sha(bytes) }
  writeFileSync(resolve(directory, 'manifest.json'), JSON.stringify({ ...manifest, scenarios: [scenario] }))
  return { scenario, declaration, source }
}
