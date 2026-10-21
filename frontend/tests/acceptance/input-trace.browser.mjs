/** Opt-in SMALL native Inputs.vue protocol control; never full-load acceptance. */
import { chromium, expect } from '@playwright/test'
import assert from 'node:assert/strict'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'
import { performance } from 'node:perf_hooks'
import { setTimeout as delay } from 'node:timers/promises'
import { installInputConsumptionTrace } from './input-consumption-trace.mjs'
import { joinInputTransition } from './input-trace-join.mjs'
assert.equal(process.env.AUTO_PLAN_INPUT_TRACE_BROWSER, '1')
const config = JSON.parse(readFileSync(process.argv[2])), output = resolve(config.output)
const accounts = JSON.parse(readFileSync(0, 'utf8')).accounts
assert.equal(accounts.length, 2)
const sha = bytes => createHash('sha256').update(bytes).digest('hex')
const report = { kind: 'small two-actor protocol/consumption control; no SLA', scenario: config.scenario,
  acceptance: 'not_run', repeats: [], status: 'fail', source_closure: 'unconfirmed', requests: [], joins: [], node_clock: 'node.performance.now:pid=' + process.pid }
let browser
const persist = () => writeFileSync(resolve(output, 'report.json'), JSON.stringify(report, null, 2))
async function capture(reply) {
  const timing = { body_started_ms: performance.now() }, bytes = await reply.body()
  timing.body_completed_ms = performance.now()
  const request = reply.request().postDataBuffer(), id = reply.headers()['x-auto-plan-test-input']
  assert.ok(id && request)
  for (const [name, value] of [['request', request], ['response', bytes]]) writeFileSync(resolve(output, id + '-' + name + '.json'), value)
  timing.capture_completed_ms = performance.now()
  const value = { id, path: new URL(reply.url()).pathname, status: reply.status(), request_bytes: request,
    command: JSON.parse(request), response: JSON.parse(bytes), request_sha256: sha(request), response_sha256: sha(bytes), timing }
  timing.parse_completed_ms = performance.now()
  report.requests.push({ ...value, request_bytes: undefined }); persist()
  return value
}
async function action(page, label) {
  const pending = page.waitForResponse(reply => reply.request().method() === 'POST' && reply.url().endsWith(label === '预览表格变更' ? '/inputs/preview' : '/inputs/apply'), { timeout: 15000 })
  await page.getByRole('button', { name: label, exact: true }).click()
  return capture(await pending)
}
async function preview(page, index, name, expected) {
  await page.getByLabel('资料类别', { exact: true }).selectOption('lines')
  await page.getByLabel(`名称 第${index + 1}行`, { exact: true }).fill(name)
  const value = await action(page, '预览表格变更')
  assert.equal(value.status, 200); assert.equal(value.response.valid, true)
  assert.deepEqual(value.response.snapshot, expected)
  await expect(page.getByText('预览通过，尚未提交', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '确认提交整批', exact: true })).toBeEnabled()
  return value
}
try {
  browser = await chromium.launch({ headless: true })
  const pages = []
  for (let index = 0; index < 2; index++) {
    const context = await browser.newContext({ ignoreHTTPSErrors: true })
    await context.addInitScript({ content: '(' + installInputConsumptionTrace.toString() + ')(window,' + JSON.stringify({ basePath: '/planning', pageIdentity: 'small-actor-' + index }) + ')' })
    const page = await context.newPage(); pages.push(page)
    await page.goto(config.origin + '/planning/login', { timeout: 15000 })
    await page.getByRole('textbox', { name: '公司账号', exact: true }).fill(accounts[index].username)
    await page.getByLabel('公司密码', { exact: true }).fill(accounts[index].password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeVisible()
    await page.goto(config.origin + '/planning/inputs', { timeout: 15000 })
    await expect(page.getByText('输入资料维护', { exact: true })).toBeVisible()
    await expect(page.getByText('当前资料版本 1。当月及后四个月的缺失月度参数自动复制上月值，已有值保持独立。', { exact: true })).toBeVisible()
  }
  const one = structuredClone(config.inputs), a = structuredClone(one), b = structuredClone(one)
  a.masters.lines[0].name = 'A changed'; b.masters.lines[1].name = 'B changed'
  const pendingA = await preview(pages[0], 0, 'A changed', a)
  const pendingB = await preview(pages[1], 1, 'B changed', b)
  const appliedB = await action(pages[1], '确认提交整批'); b.input_version = 2
  assert.equal(appliedB.status, 200); assert.deepEqual(appliedB.response, b)
  await expect(pages[1].getByText('已提交资料版本 2', { exact: true })).toBeVisible()
  const staleA = await action(pages[0], '确认提交整批'); assert.equal(staleA.status, 409)
  await expect(pages[0].getByRole('button', { name: '确认提交整批', exact: true })).toBeDisabled()
  const reloaded = pages[0].waitForResponse(reply => reply.request().method() === 'GET' && reply.url().endsWith('/api/inputs'))
  await pages[0].getByRole('button', { name: '重新读取资料', exact: true }).click()
  assert.deepEqual(await (await reloaded).json(), b)
  await expect(pages[0].getByText('当前资料版本 2。当月及后四个月的缺失月度参数自动复制上月值，已有值保持独立。', { exact: true })).toBeVisible()
  const both = structuredClone(b); both.masters.lines[0].name = 'A changed'
  const freshA = await preview(pages[0], 0, 'A changed', both)
  const appliedA = await action(pages[0], '确认提交整批'); both.input_version = 3
  assert.equal(appliedA.status, 200); assert.deepEqual(appliedA.response, both)
  await expect(pages[0].getByText('已提交资料版本 3', { exact: true })).toBeVisible()
  const allEvents = await Promise.all(pages.map(page => page.evaluate(() => window.__inputConsumptionTrace.events)))
  writeFileSync(resolve(output, 'browser-events.json'), JSON.stringify(allEvents, null, 2))
  let server = [], until = performance.now() + 5000
  while (performance.now() < until) {
    const text = readFileSync(config.server_trace, 'utf8')
    server = text.split('\n').slice(0, -1).filter(Boolean).map(line => JSON.parse(line))
    if ([pendingA,pendingB,appliedB,staleA,freshA,appliedA].every(row => server.some(item => item.id === row.id))) break
    await delay(20)
  }
  for (const [events, pre, applied] of [[allEvents[0],pendingA,staleA],[allEvents[0],freshA,appliedA],[allEvents[1],pendingB,appliedB]]) {
    report.joins.push(joinInputTransition({ events, preview: pre, apply: applied, server }))
  }
  const get = async path => { const r = await pages[0].request.get(config.origin + '/planning/api' + path); assert.equal(r.status(), 200); return r.json() }
  assert.deepEqual(await get('/inputs?version=1'), one); assert.deepEqual(await get('/inputs?version=2'), b)
  assert.deepEqual(await get('/inputs?version=3'), both)
  const head = await get('/plans/current'); assert.equal(head.revision, 0); assert.equal(head.latest_adoption, 0)
  assert.deepEqual((await get('/demands/files')).files, [])
  report.status = 'pass'; report.source_closure = 'confirmed declared V3; immutable V1/V2 preserved; R/A0'
  report.restoration = 'not performed: declared disposable protocol ends at V3; resource cleanup is separate'
} catch (error) {
  report.failure = { name: error.name, message: error.message.split('\n')[0].slice(0, 240) }
  throw error
} finally {
  if (browser) await browser.close()
  persist()
}
