/** Exact declared input-name states. This does not qualify activity or writes. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'

const compiled = new WeakMap()
// Unlike contracts.canonical, this preserves exact decoded scalar strings.
// Arrays retain their order; object keys sort; input_version alone is omitted.
export const INPUT_CONTENT_DIGEST_ALGORITHM = 'decoded-input-json-v1'

function ordered(value) {
  if (Array.isArray(value)) return value.map(ordered)
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])]))
  return value
}

function content(inputs) {
  const value = structuredClone(inputs)
  delete value.input_version
  return ordered(value)
}

export function inputContentDigest(inputs) {
  return createHash('sha256').update(JSON.stringify(content(inputs))).digest('hex')
}

function jsonDigest(value) {
  return createHash('sha256').update(JSON.stringify(ordered(value))).digest('hex')
}

function compile(declaration, initialInputs) {
  assert.equal(declaration.schema_version, 1, 'Unknown input companion schema.')
  assert.equal(declaration.kind, 'maintained_input_names', 'Unknown input companion kind.')
  assert.equal(declaration.digest_algorithm, INPUT_CONTENT_DIGEST_ALGORITHM, 'Unknown input companion digest algorithm.')
  assert.equal(declaration.source_inputs_sha256, inputContentDigest(initialInputs), 'Companion predecessor inputs differ.')
  assert.equal(declaration.targets.length, 4, 'Four existing name-edit targets are required.')
  assert.deepEqual(declaration.targets.map(target => target.editor).sort(), [1, 2, 3, 4], 'Editor assignments must be distinct.')
  assert.equal(new Set(declaration.targets.map(target => `${target.kind}:${target.code}`)).size, 4, 'Name-edit targets repeat.')
  for (const target of declaration.targets) {
    assert.ok(['models', 'lines'].includes(target.kind), 'Only existing model/line names are declared.')
    const rows = initialInputs.masters[target.kind].filter(row => row.code === target.code)
    assert.equal(rows.length, 1, 'A declared existing master is missing or duplicated.')
    assert.equal(target.names.length, 2, 'Each actor needs exactly two declared names.')
    assert.ok(target.names.every(name => typeof name === 'string' && name.length && !/[\t\r\n]/.test(name)), 'Declared names must be nonempty table cells.')
    assert.notEqual(target.names[0], target.names[1], 'A declared name edit must change content.')
    assert.equal(target.names[0], rows[0].name, 'Declared original name differs from the predecessor.')
  }
  assert.equal(declaration.states.length, 16, 'All sixteen complete input states must be frozen.')
  const states = new Map(declaration.states.map(state => [state.id, state]))
  assert.equal(states.size, 16, 'Declared state identities repeat.')
  const byDigest = new Map()
  for (let mask = 0; mask < 16; mask++) {
    const state = states.get(String(mask))
    assert.ok(state, 'A declared input-name state is missing.')
    const expected = structuredClone(initialInputs)
    for (const [index, target] of declaration.targets.entries()) expected.masters[target.kind].find(row => row.code === target.code).name = target.names[(mask >> index) & 1]
    assert.deepEqual(content(state.inputs), content(expected), 'A companion changed fields outside its declared names.')
    const digest = inputContentDigest(state.inputs)
    assert.equal(state.inputs_sha256, digest, 'Complete declared input-state digest differs.')
    assert.ok(!byDigest.has(digest), 'Declared name states must have different complete contents.')
    byDigest.set(digest, { id: state.id, inputs: structuredClone(state.inputs), inputs_sha256: digest })
  }
  return { initialInputs, byDigest }
}

export function resolveInputCompanion(declaration, initialInputs, actualInputs, options = {}) {
  let value = compiled.get(declaration)
  if (!value || value.initialInputs !== initialInputs) {
    value = compile(declaration, initialInputs)
    compiled.set(declaration, value)
  }
  assert.ok(Number.isInteger(actualInputs.input_version) && actualInputs.input_version >= initialInputs.input_version, 'Immutable input version moved backwards.')
  const state = value.byDigest.get(inputContentDigest(actualInputs))
  assert.ok(state, 'Actual complete inputs are outside the frozen companion states.')
  if (actualInputs.input_version === initialInputs.input_version) assert.equal(state.id, '0', 'An immutable input version changed contents.')
  if (options.history) {
    assert.ok(Array.isArray(options.history), 'Input history must contain complete transitions.')
    let previous = initialInputs
    const proofs = []
    for (const event of options.history) {
      assert.deepEqual(ordered(event.before), ordered(previous), 'Input history omitted or reordered a complete immutable snapshot.')
      const accepted = resolveInputCompanion(declaration, initialInputs, event.after, { transition: event })
      proofs.push(accepted.transition)
      previous = event.after
    }
    assert.deepEqual(ordered(previous), ordered(actualInputs), 'Input history does not reach the accepted immutable snapshot.')
    return { ...state, history: proofs }
  }
  if (options.transition) return { ...state, transition: changedInput(declaration, initialInputs, actualInputs, state, options.transition) }
  return state
}

function changedInput(declaration, initialInputs, actualInputs, state, attempt) {
  if (attempt.resolution) {
    const resolution = attempt.resolution
    assert.ok([null, 200].includes(attempt.response_status), 'A known rejection cannot become a reconciled input commit.')
    assert.equal(resolution.kind, 'immutable_input_version', 'Unknown input resolution proof.')
    assert.equal(resolution.result, 'committed', 'An unresolved or obsolete input request cannot be a committed edit.')
    assert.equal(resolution.head_status, 200, 'Reconciliation needs an actual successful public head read.')
    assert.equal(resolution.version_status, 200, 'Reconciliation needs an actual successful immutable input read.')
    assert.ok(resolution.head_input_version > attempt.command.base_version, 'The original optimistic input base is still current.')
    for (const key of ['head_artifact', 'version_artifact']) assert.ok(resolution[key]?.path && resolution[key]?.sha256, 'Reconciliation needs complete retained public read bodies.')
  } else assert.equal(attempt.response_status, 200, 'A failed input request cannot be a committed edit.')
  const before = resolveInputCompanion(declaration, initialInputs, attempt.before)
  const target = declaration.targets.find(row => row.editor === attempt.editor)
  assert.ok(target, 'The input editor has no declared master assignment.')
  assert.deepEqual(Object.keys(attempt.command).sort(), ['base_version', 'changes'], 'Input edits use their own public version contract.')
  assert.equal(attempt.command.base_version, attempt.before.input_version, 'Submitted input base differs from its complete snapshot.')
  assert.equal(actualInputs.input_version, attempt.before.input_version + 1, 'A genuine input commit must advance exactly one version.')
  assert.notEqual(before.inputs_sha256, state.inputs_sha256, 'An unchanged input is not a modified editor commit.')
  assert.deepEqual(Object.keys(attempt.command.changes), [target.kind], 'The input request changed another category.')

  const original = attempt.before.masters[target.kind].find(row => row.code === target.code)
  const changed = actualInputs.masters[target.kind].find(row => row.code === target.code)
  assert.notEqual(original.name, changed.name, 'The assigned existing master name did not change.')
  const expected = structuredClone(attempt.before)
  expected.masters[target.kind].find(row => row.code === target.code).name = changed.name
  expected.input_version = actualInputs.input_version
  assert.deepEqual(ordered(actualInputs), ordered(expected), 'A committed input edit changed fields outside its actor-owned name.')

  const rows = attempt.command.changes[target.kind]
  assert.ok(Array.isArray(rows) && rows.length, 'The name-edit request has no rows.')
  assert.equal(new Set(rows.map(row => row.code)).size, rows.length, 'Submitted master rows repeat.')
  assert.ok(rows.some(row => row.code === target.code), 'The assigned changed master was not submitted.')
  for (const row of rows) {
    const wanted = expected.masters[target.kind].find(item => item.code === row.code)
    assert.ok(wanted, 'A name-edit request added a master.')
    assert.deepEqual(ordered(row), ordered(wanted), 'Submitted master contents differ from the complete accepted state.')
  }
  return { kind: 'modified_input_name', editor: attempt.editor,
    acknowledgment: attempt.resolution ? 'reconciled_public_immutable_version' : 'original_http_200',
    before_version: attempt.before.input_version, input_version: actualInputs.input_version,
    before_state_id: before.id, state_id: state.id,
    digest_algorithm: INPUT_CONTENT_DIGEST_ALGORITHM,
    before_inputs_sha256: before.inputs_sha256, inputs_sha256: state.inputs_sha256,
    command_decoded_json_sha256: jsonDigest(attempt.command),
    response_decoded_json_sha256: jsonDigest(actualInputs) }
}
