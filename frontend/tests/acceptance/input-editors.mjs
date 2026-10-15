/** Real Inputs.vue gestures. Persistence/authentication remain public boundaries. */
import assert from 'node:assert/strict'
import { expect } from '@playwright/test'
import { performance } from 'node:perf_hooks'
import { resolveInputCompanion } from './input-load.mjs'

const pendingResponse = (page, predicate) => {
  const pending = page.waitForResponse(predicate, { timeout: 180000 })
  pending.catch(() => {})
  return pending
}

export async function inputEditorLoop({ page, editor, declaration, initialInputs, origin, basePath,
  stopped, captureBody, firstPreview, firstCommit, onAttempt, onCommit }) {
  const target = declaration.targets.find(row => row.editor === editor)
  assert.ok(target, 'The browser editor has no declared name assignment.')
  const url = path => origin + basePath + '/api' + path
  let before, first = true, firstCommitted = false, sequence = 0

  async function load(navigate = false) {
    const pending = pendingResponse(page, response => response.url() === url('/inputs') && response.request().method() === 'GET')
    if (navigate) await page.goto(origin + basePath + '/inputs', { timeout: 180000 })
    else await page.getByRole('button', { name: '重新读取资料', exact: true }).click()
    const received = await pending
    assert.equal(received.status(), 200, 'The input editor could not reload public inputs.')
    before = await received.json()
    resolveInputCompanion(declaration, initialInputs, before)
    await expect(page.getByText(`当前资料版本 ${before.input_version}。当月及后四个月的缺失月度参数自动复制上月值，已有值保持独立。`, { exact: true })).toBeVisible()
    await page.getByLabel('资料类别', { exact: true }).selectOption(target.kind)
  }

  async function submit(phase, button, started) {
    const endpoint = url('/inputs/' + phase)
    const requestPending = page.waitForRequest(request => request.url() === endpoint && request.method() === 'POST', { timeout: 180000 })
    requestPending.catch(() => {})
    const responsePending = pendingResponse(page, response => response.url() === endpoint && response.request().method() === 'POST')
    const entry = { kind: 'input_name_attempt', editor, sequence: ++sequence, phase,
      started_ms: started, before_version: before.input_version, response_status: null, status: 'unknown' }
    await page.getByRole('button', { name: button, exact: true }).click()
    const request = await requestPending
    const rawCommand = request.postDataBuffer()
    assert.ok(rawCommand, 'The input UI did not submit a complete JSON body.')
    entry.command = JSON.parse(rawCommand)
    entry.request_artifact = captureBody(rawCommand)
    entry.request_started_epoch_ms = request.timing().startTime
    try {
      const received = await responsePending
      const bytes = await received.body()
      entry.response_status = received.status()
      entry.response_artifact = captureBody(bytes)
      entry.response = JSON.parse(bytes)
      entry.response_finished_ms = performance.now()
      entry.request_timing = request.timing()
      return entry
    } catch (error) {
      entry.finished_ms = performance.now()
      entry.failure = String(error.message).split('\n')[0].slice(0, 300)
      entry.before = before
      onAttempt(entry)
      throw error
    }
  }

  await load(true)
  while (!stopped()) {
    const started = performance.now()
    const rows = page.getByRole('table', { name: target.kind === 'models' ? '机种' : '线体', exact: true }).locator('tbody tr')
    const index = await rows.evaluateAll((elements, code) => elements.findIndex(row =>
      [...row.querySelectorAll('input')].some(input => input.getAttribute('aria-label')?.startsWith('编码 ') && input.value === code)), target.code)
    assert.ok(index >= 0, 'The assigned existing master is absent from the displayed input table.')
    await expect(page.getByLabel(`编码 第${index + 1}行`, { exact: true })).toHaveValue(target.code)
    const original = before.masters[target.kind].find(row => row.code === target.code)
    const nextName = target.names[original.name === target.names[0] ? 1 : 0]
    await page.getByLabel(`名称 第${index + 1}行`, { exact: true }).fill(nextName)
    const preview = await submit('preview', '预览表格变更', started)
    if (preview.response_status === 409) {
      preview.status = 'conflict'; preview.finished_ms = performance.now(); onAttempt(preview)
      await load(); continue
    }
    assert.equal(preview.response_status, 200, 'The real input preview request failed.')
    assert.ok(preview.response.valid, 'The declared name-only input preview was not valid.')
    assert.equal(preview.response.base_version, before.input_version)
    preview.status = 'preview'; preview.finished_ms = performance.now(); onAttempt(preview)
    await expect(page.getByText('预览通过，尚未提交', { exact: true })).toBeVisible()
    if (first) { await firstPreview(); first = false }
    if (stopped()) break
    const applied = await submit('apply', '确认提交整批', started)
    if (applied.response_status === 409) {
      applied.status = 'conflict'; applied.finished_ms = performance.now(); onAttempt(applied)
      await expect(page.getByRole('button', { name: '确认提交整批', exact: true })).toBeDisabled()
      await load(); continue
    }
    assert.equal(applied.response_status, 200, 'The real input apply request failed.')
    const after = applied.response
    const resolved = resolveInputCompanion(declaration, initialInputs, after,
      { transition: { editor, before, command: applied.command, response_status: applied.response_status } })
    await expect(page.getByText(`已提交资料版本 ${after.input_version}`, { exact: true })).toBeVisible()
    await expect(page.getByLabel(`名称 第${index + 1}行`, { exact: true })).toHaveValue(nextName)
    applied.status = 'pass'; applied.commit = resolved.transition
    applied.before = before; applied.after = after; applied.finished_ms = performance.now()
    onAttempt(applied); onCommit(applied)
    before = after
    if (!firstCommitted) { firstCommitted = true; await firstCommit() }
  }
}
