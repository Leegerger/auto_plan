/** Expected source capture only. Never pass the observed computation-source body here. */
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'
const run = promisify(execFile)

export async function captureExpectedState(rawState, { root, output, stem, python = 'python' }) {
  const digest = bytes => createHash('sha256').update(bytes).digest('hex')
  const rawBytes = JSON.stringify(rawState), rawPath = stem + '-raw-state.json'
  writeFileSync(resolve(output, rawPath), rawBytes, { flag: 'wx' })
  const provenancePath = stem + '-schema.json'
  const result = await run(python, [resolve(root, 'scripts/acceptance/materialize_source.py'), '--state', resolve(output, rawPath), '--provenance', resolve(output, provenancePath)],
    { cwd: root, encoding: 'utf8', timeout: 30000, maxBuffer: 64 * 1024 * 1024,
      env: { ...process.env, PYTHONPATH: [resolve(root, 'src'), root, process.env.PYTHONPATH].filter(Boolean).join(':') } })
  const snapshot = JSON.parse(result.stdout), typedBytes = JSON.stringify(snapshot), typedPath = stem + '-expected-snapshot.json'
  writeFileSync(resolve(output, typedPath), typedBytes, { flag: 'wx' })
  return { state: { ...rawState, snapshot }, capture: {
    kind: 'independent_read_state_with_schema_defaults',
    schema: { path: provenancePath, sha256: digest(readFileSync(resolve(output, provenancePath))), ...JSON.parse(readFileSync(resolve(output, provenancePath))) },
    raw: { path: rawPath, sha256: digest(rawBytes) },
    expected: { path: typedPath, sha256: digest(typedBytes) },
    head: { revision: rawState.head.revision, input_version: rawState.head.input_version, latest_adoption: rawState.head.latest_adoption }
  } }
}
