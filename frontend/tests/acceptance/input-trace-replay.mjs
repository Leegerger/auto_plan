/** Read-only offline replay. Large body verification never runs in worker cleanup. */
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createHash } from 'node:crypto'
import { collectInputJoins } from './input-trace-collector.mjs'
const sha = bytes => createHash('sha256').update(bytes).digest('hex')

export function replayInputTrace(report, read = readFileSync) {
  const trace = report.input_trace
  const checked = artifact => {
    assert.ok(artifact?.path && artifact.sha256, 'A separately retained artifact binding is required.')
    const bytes = read(artifact.path)
    assert.equal(sha(bytes), artifact.sha256, 'Retained artifact differs from the worker capture hash.')
    return bytes
  }
  const config = JSON.parse(checked(trace?.config)), browserBytes = checked(trace.browser_export)
  const index = JSON.parse(checked(trace.attempts)), serverBytes = checked(trace.server_ledger)
  const readArtifact = artifact => checked({ ...artifact, path: resolve(report.fixtures, artifact.path) })
  const attempts = index.map(row => ({ ...row,
    ...(row.request_artifact ? { command: JSON.parse(readArtifact(row.request_artifact)) } : {}),
    ...(row.response_artifact ? { response: JSON.parse(readArtifact(row.response_artifact)) } : {}) }))
  const result = collectInputJoins({ attempts, browserBytes, browserSha256: trace.browser_export.sha256,
    serverBytes, origin: config.origin, readArtifact })
  return { ...result, acceptance: 'not_run', repeats: [],
    captured_config_sha256: trace.config.sha256,
    source_closure: trace.source_closure ?? 'See original workflow report; protocol replay establishes no source closure.',
    input_acknowledgments: trace.input_acknowledgments ?? 'See original workflow report; no new acknowledgment is inferred.',
    restoration: trace.restoration ?? 'See original workflow report; replay performs no restoration.' }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const output = resolve(process.argv[2]), report = JSON.parse(readFileSync(resolve(output, 'report.json')))
  const result = replayInputTrace(report)
  writeFileSync(resolve(output, 'input-trace-replay.json'), JSON.stringify(result, null, 2))
  process.stdout.write(JSON.stringify({ status: result.status, joins: result.joins.length, acceptance: result.acceptance }) + '\n')
  if (result.status !== 'complete') process.exitCode = 1
}
