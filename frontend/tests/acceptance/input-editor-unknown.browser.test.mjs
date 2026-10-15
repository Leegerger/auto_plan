/** A lost apply acknowledgment must settle through public immutable inputs. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, openSync, closeSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gunzipSync, gzipSync } from 'node:zlib'
import { get as httpsGet } from 'node:https'
import { setTimeout as delay } from 'node:timers/promises'
import { inputEditorLoop } from './input-editors.mjs'
import { INPUT_CONTENT_DIGEST_ALGORITHM, inputContentDigest, resolveInputCompanion } from './input-load.mjs'
import { assertState } from './contracts.mjs'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
const enabled = process.env.AUTO_PLAN_INPUT_UNKNOWN_BROWSER === '1'
const sha = value => createHash('sha256').update(value).digest('hex')

function fixture(directory) {
  const frozen = resolve(root, 'docs/acceptance/frozen')
  const parentBytes = readFileSync(resolve(frozen, 'manifest.json'))
  const parent = JSON.parse(parentBytes), original = parent.scenarios.find(row => row.id === 'r20-loose')
  const raw = gunzipSync(readFileSync(resolve(frozen, original.source.path)))
  assert.equal(sha(raw), original.source.sha256)
  const source = JSON.parse(raw), models = ['M00', 'M02', 'M04', 'M06', 'M08']
  const lines = ['L00', 'L01', 'L02', 'L03', 'L04'], recipes = ['R00', 'R01', 'R02', 'R03', 'R04']
  source.inputs.masters.models = source.inputs.masters.models.filter(row => models.includes(row.code))
  source.inputs.masters.lines = source.inputs.masters.lines.filter(row => lines.includes(row.code))
  source.inputs.masters.recipes = source.inputs.masters.recipes.filter(row => recipes.includes(row.code))
  source.inputs.masters.capabilities = source.inputs.masters.capabilities.filter(row => recipes.includes(row.recipe_code) && lines.includes(row.line_code))
  const parameters = source.inputs.parameters
  parameters.model_yields = parameters.model_yields.filter(row => models.includes(row.model_code))
  parameters.capacities = parameters.capacities.filter(row => recipes.includes(row.recipe_code) && lines.includes(row.line_code))
  parameters.cycle_times = parameters.cycle_times.filter(row => models.includes(row.model_code))
  parameters.switch_durations = parameters.switch_durations.filter(row => recipes.includes(row.from_recipe) && recipes.includes(row.to_recipe))
  source.inputs.business.demands = source.inputs.business.demands.filter(row => models.includes(row.model_code))
  source.plan.segments = source.plan.segments.filter(row => models.includes(row.model_code))
  source.plan.switches = []; source.plan.ordinary_idle = []; source.plan.current_day_witnesses = []
  source.plan.initial_states = lines.map((line_code, index) => ({ line_code, recipe_code: recipes[index] }))
  source.baseline.plan = structuredClone(source.plan)
  for (const scope of [...source.execution.coverage, ...source.baseline.coverage]) { scope.line_codes = [...lines]; scope.model_codes = [...models] }
  assert.equal(source.plan.segments.length, 525)
  assert.ok(source.plan.segments.every(row => row.quantity === 100))
  assert.equal(source.inputs.business.demands.length, 75)
  assert.ok(source.inputs.business.demands.every(row => row.quantity === 700))
  const rawSource = Buffer.from(JSON.stringify(source))
  writeFileSync(resolve(directory, 'source.json.gz'), gzipSync(rawSource))
  const scenario = { id: 'input-editor-unknown-adapter-v1', kind: 'browser_unknown_input_contract',
    source: { path: 'source.json.gz', sha256: sha(rawSource) }, acceptance: 'not_run',
    predecessor: { manifest_sha256: sha(parentBytes), source_sha256: original.source.sha256 },
    purpose: 'Five-model lost input acknowledgment correctness fixture; no fixed-scale acceptance alias.' }
  writeFileSync(resolve(directory, 'manifest.json'), JSON.stringify({ schema_version: 1, scenarios: [scenario] }))
  const targets = models.slice(1).map((code, index) => ({ editor: index + 1, kind: 'models', code,
    names: [code, code + ' · unknown-editor' + (index + 1)] }))
  const declaration = { schema_version: 1, kind: 'maintained_input_names', digest_algorithm: INPUT_CONTENT_DIGEST_ALGORITHM,
    scenario: scenario.id, source_manifest_sha256: sha(parentBytes), source_fixture_sha256: scenario.source.sha256,
    source_inputs_sha256: inputContentDigest(source.inputs), targets,
    states: Array.from({ length: 16 }, (_, mask) => {
      const inputs = structuredClone(source.inputs)
      for (const [index, target] of targets.entries()) inputs.masters.models.find(row => row.code === target.code).name = target.names[(mask >> index) & 1]
      return { id: String(mask), inputs, inputs_sha256: inputContentDigest(inputs) }
    }) }
  writeFileSync(resolve(directory, 'companion.json'), JSON.stringify(declaration))
  return { source, scenario, declaration }
}

function healthy(url) {
  return new Promise(done => {
    const request = httpsGet(url, { rejectUnauthorized: false, timeout: 1000 }, response => { response.resume(); done(response.statusCode === 200) })
    request.on('timeout', () => request.destroy()); request.on('error', () => done(false))
  })
}

test('an actual committed input with a lost reply is recovered without blind apply or invented acknowledgment',
  { skip: !enabled && 'Set AUTO_PLAN_INPUT_UNKNOWN_BROWSER=1 for the owned HTTPS adapter.', timeout: 120000 }, async () => {
    const python = process.env.AUTO_PLAN_INPUT_EDITORS_PYTHON
    assert.ok(python, 'Provide the installed test interpreter.')
    const port = Number(process.env.AUTO_PLAN_INPUT_EDITORS_PORT ?? '18869')
    const output = mkdtempSync(resolve(tmpdir(), 'auto-plan-input-unknown-contract-'))
    const fixtures = resolve(output, 'fixtures'); mkdirSync(fixtures); mkdirSync(resolve(output, 'bodies'))
    const { source, scenario, declaration } = fixture(fixtures)
    const origin = 'https://localhost:' + port, prefix = origin + '/planning/api'
    const headers = { Origin: origin, 'X-Auto-Plan-Request': '1' }
    const env = { ...Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('AUTO_PLAN_'))), PYTHONPATH: 'src:.' }
    const log = openSync(resolve(output, 'server.log'), 'w')
    const server = spawn(python, ['-m', 'scripts.acceptance.adapter_server', '--fixtures', fixtures,
      '--scenario', scenario.id, '--port', String(port)], { cwd: root, detached: true, stdio: ['ignore', log, log], env })
    let serverExited = false, serverError, browser, stopped = false, loops = [], reconciliationTimer, previewDone
    const serverFinished = new Promise(done => {
      server.once('exit', (code, signal) => { serverExited = true; done({ code, signal }) })
      server.once('error', error => { serverError = error; serverExited = true; done({ error: error.message }) })
    })
    const attempts = [], commits = [], forwarded = [], events = []
    let hiddenCommitted, dropped, firstDone
    const droppedPromise = new Promise(done => { dropped = done })
    const allFirst = new Promise(done => { firstDone = done })
    const evidence = { kind: 'functional public browser input unknown-write contract; no timed acceptance',
      scenario: scenario.id, acceptance: 'not_run', repeats: [], attempts, commits, forwarded, events }
    const captureBody = bytes => {
      const value = Buffer.from(bytes), digest = sha(value), path = 'bodies/' + digest + '.json'
      writeFileSync(resolve(output, path), value)
      return { path, sha256: digest }
    }
    const captured = pointer => {
      const bytes = readFileSync(resolve(output, pointer.path)); assert.equal(sha(bytes), pointer.sha256)
      return JSON.parse(bytes)
    }
    try {
      const readyBy = Date.now() + 30000
      while (!serverExited && Date.now() < readyBy && !await healthy(prefix + '/ready')) await delay(100)
      assert.ok(!serverExited && await healthy(prefix + '/ready'), 'Owned adapter not ready: ' + (serverError?.message ?? output))
      browser = await chromium.launch({ headless: true })
      const pages = []
      for (let editor = 0; editor < 5; editor++) {
        const context = await browser.newContext({ ignoreHTTPSErrors: true })
        const login = await context.request.post(prefix + '/auth/login', { headers,
          data: { username: 'ACCEPTANCE' + editor, password: 'company-secret' } })
        assert.equal(login.status(), 200)
        pages.push(await context.newPage())
      }
      const get = async path => {
        try {
          const response = await pages[0].request.get(prefix + path, { headers }); assert.equal(response.status(), 200); return response.json()
        } catch (error) { throw new Error(error.message.split('\n')[0]) }
      }
      const readState = async () => {
        const head = await get('/plans/current'), inputs = await get('/inputs?version=' + head.input_version)
        const adopted = await get('/plans/adoptions/' + head.latest_adoption)
        return { head, snapshot: { time: head.time, inputs, plan: head.plan, execution: head.context.execution,
          locks: head.context.locks, reset: head.context.reset, preferences: head.context.preferences,
          baseline: { version: head.latest_adoption, plan: adopted.snapshot.plan, coverage: adopted.coverage } } }
      }
      const original = await readState()
      assert.equal(original.head.revision, 1); assert.equal(original.head.input_version, 1); assert.equal(original.head.latest_adoption, 1)
      evidence.original_state = original
      for (const [name, value] of [['evaluation', original.head.evaluation], ['plan', original.head.plan], ['snapshot', original.snapshot]]) {
        writeFileSync(resolve(output, 'initial-' + name + '.json'), JSON.stringify(value))
      }
      const preflight = spawnSync(python, [resolve(root, 'scripts/acceptance/check_result.py'), '--fixtures', fixtures,
        '--scenario', scenario.id, '--kind', 'source', '--evaluation', resolve(output, 'initial-evaluation.json'),
        '--candidate', resolve(output, 'initial-plan.json'), '--observed-source', resolve(output, 'initial-snapshot.json')],
        { cwd: root, env, encoding: 'utf8' })
      evidence.source_preflight = { status: preflight.status, stdout: preflight.stdout, stderr: preflight.stderr }
      assert.equal(preflight.status, 0, 'Complete public source differs from frozen correctness fixture: ' + preflight.stderr)
      await pages[1].route(prefix + '/inputs/apply', async route => {
        const command = route.request().postDataJSON()
        const response = await route.fetch()
        forwarded.push({ command, status: response.status() })
        if (!hiddenCommitted && response.status() === 200) {
          hiddenCommitted = { command, response: await response.json() }
          evidence.hidden_injector_truth = hiddenCommitted
          events.push({ kind: 'actual_commit_reply_dropped', epoch_ms: Date.now() })
          await route.abort('failed'); dropped(); return
        }
        await route.fulfill({ response })
      })
      let previewCount = 0
      const allPreviews = new Promise(done => { previewDone = done })
      const onCommit = entry => {
        commits.push(entry)
        if (new Set(commits.map(row => row.editor)).size === 4) { stopped = true; firstDone() }
      }
      loops = declaration.targets.map(target => inputEditorLoop({ page: pages[target.editor], editor: target.editor,
        declaration, initialInputs: source.inputs, origin, basePath: '/planning', stopped: () => stopped,
        captureBody, firstPreview: async () => { if (++previewCount === 4) previewDone(); await allPreviews },
        firstCommit: () => allFirst, onAttempt: entry => { attempts.push(entry) }, onCommit }))
      for (const pending of loops) pending.catch(() => {})
      const failed = Promise.race(loops.map(pending => pending.then(() => new Promise(() => {}))))
      await Promise.race([allFirst, failed, droppedPromise.then(() => new Promise((_, reject) => {
        reconciliationTimer = setTimeout(() => reject(new assert.AssertionError({
          message: 'The actual committed lost-reply input was not reconciled promptly through immutable public versions: ' + output,
          actual: new Set(commits.map(row => row.editor)).size, expected: 4, operator: 'strictEqual' })), 15000)
      }))])
      clearTimeout(reconciliationTimer)
      await Promise.all(loops)
      assert.ok(hiddenCommitted, 'The test must drop an actual successful commit reply.')
      const recovered = commits.find(row => row.editor === 1)
      assert.equal(recovered.response_status, null, 'A lost acknowledgment cannot be invented as HTTP200.')
      assert.equal(recovered.response_artifact, undefined)
      assert.equal(recovered.resolution.kind, 'immutable_input_version')
      assert.equal(recovered.resolution.result, 'committed')
      assert.equal(recovered.resolution.head_status, 200)
      assert.equal(recovered.resolution.version_status, 200)
      assert.deepEqual(recovered.after, hiddenCommitted.response)
      assert.deepEqual(captured(recovered.resolution.version_artifact), hiddenCommitted.response)
      assert.ok(captured(recovered.resolution.head_artifact).input_version >= recovered.after.input_version)
      assert.equal(forwarded.filter(row => JSON.stringify(row.command) === JSON.stringify(hiddenCommitted.command)).length, 1,
        'The original unknown apply must be forwarded exactly once.')
      const history = [...commits].sort((a, b) => a.after.input_version - b.after.input_version)
      const final = history.at(-1).after
      resolveInputCompanion(declaration, source.inputs, final, { history })
      assert.deepEqual([...new Set(history.map(row => row.editor))].sort(), [1, 2, 3, 4])
      const initial = await get('/inputs?version=1')
      assertState(await readState(), original, [original.snapshot.plan], { inputCompanion: declaration })
      const changes = { models: final.masters.models.map(row => ({ ...row,
        name: source.inputs.masters.models.find(item => item.code === row.code).name })) }
      const preview = await pages[0].request.post(prefix + '/inputs/preview', { headers, data: { base_version: final.input_version, changes } })
      assert.equal(preview.status(), 200); assert.equal((await preview.json()).valid, true)
      const apply = await pages[0].request.post(prefix + '/inputs/apply', { headers, data: { base_version: final.input_version, changes } })
      assert.equal(apply.status(), 200)
      const restored = await apply.json()
      assert.equal(inputContentDigest(restored), inputContentDigest(initial))
      assertState(await readState(), original, [original.snapshot.plan], { inputCompanion: declaration })
      evidence.restored_inputs = restored; evidence.status = 'pass'
    } finally {
      clearTimeout(reconciliationTimer)
      stopped = true
      previewDone?.(); firstDone()
      try {
        if (browser) await browser.close()
        await Promise.allSettled(loops)
      } finally {
        if (server.pid && !serverExited) {
          try { process.kill(-server.pid, 'SIGTERM') } catch (error) { if (error.code !== 'ESRCH') throw error }
          await Promise.race([serverFinished, delay(3000)])
          if (!serverExited) { try { process.kill(-server.pid, 'SIGKILL') } catch (error) { if (error.code !== 'ESRCH') throw error }; await serverFinished }
        }
        closeSync(log)
        evidence.owned = { controller_pid: process.pid, server_pid: server.pid, server_pgid: server.pid }
        if (serverError) evidence.setup_error = serverError.message
        writeFileSync(resolve(output, 'evidence.json'), JSON.stringify(evidence, null, 2) + '\n')
      }
    }
  })
