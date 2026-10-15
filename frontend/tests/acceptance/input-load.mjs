/** Exact declared input-name states. This does not qualify activity or writes. */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'

const compiled = new WeakMap()

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

function compile(declaration, initialInputs) {
  assert.equal(declaration.schema_version, 1, 'Unknown input companion schema.')
  assert.equal(declaration.kind, 'maintained_input_names', 'Unknown input companion kind.')
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

export function resolveInputCompanion(declaration, initialInputs, actualInputs) {
  let value = compiled.get(declaration)
  if (!value || value.initialInputs !== initialInputs) {
    value = compile(declaration, initialInputs)
    compiled.set(declaration, value)
  }
  assert.ok(Number.isInteger(actualInputs.input_version) && actualInputs.input_version >= initialInputs.input_version, 'Immutable input version moved backwards.')
  const state = value.byDigest.get(inputContentDigest(actualInputs))
  assert.ok(state, 'Actual complete inputs are outside the frozen companion states.')
  if (actualInputs.input_version === initialInputs.input_version) assert.equal(state.id, '0', 'An immutable input version changed contents.')
  return state
}
