/** Test-only primitive consumption markers. Complete bodies remain in network artifacts. */
export function installInputConsumptionTrace(target, { basePath, pageIdentity }) {
  const events = [], originalFetch = target.fetch, clock = target.performance
  let sequence = 0, nativeSequence = 0, lastNative = null, preview = null, ready = null
  const emit = (phase, fields = {}) => events.push({ phase, at_ms: clock.now(),
    clock_domain: 'browser.performance.now:' + pageIdentity, ...fields })
  const nativeClick = event => {
    const button = event.target.closest?.('button'), label = button?.textContent.trim()
    const action = { '预览表格变更': 'preview', '确认提交整批': 'apply', '重新读取资料': 'reload' }[label]
    if (!action) return
    lastNative = { native_sequence: ++nativeSequence, action }
    emit('native_click', { ...lastNative, disabled: button.disabled, review_request_id: preview?.id ?? null })
  }
  target.document?.addEventListener('click', nativeClick, true)
  const observer = target.MutationObserver && target.document ? new target.MutationObserver(() => {
    if (!preview || ready === preview.id) return
    const valid = [...target.document.querySelectorAll('.input-preview p')].some(row => row.textContent.trim() === '预览通过，尚未提交')
    const button = [...target.document.querySelectorAll('button')].find(row => row.textContent.trim() === '确认提交整批')
    if (valid && button && !button.disabled) {
      ready = preview.id
      emit('preview_dom_ready', { request_id: preview.id, base_version: preview.base_version, apply_enabled: true })
    }
  }) : null
  observer?.observe(target.document, { subtree: true, childList: true, attributes: true })
  target.fetch = function (...args) {
    let path
    try { path = new URL(typeof args[0] === 'string' ? args[0] : args[0].url, target.location.href).pathname }
    catch { return originalFetch.apply(this, args) }
    if (![basePath + '/api/inputs', basePath + '/api/inputs/preview', basePath + '/api/inputs/apply'].includes(path)) return originalFetch.apply(this, args)
    const method = args[1]?.method ?? args[0]?.method ?? 'GET', current = ++sequence
    const native = lastNative
    if (path.endsWith('/preview')) preview = null
    emit('fetch_started', { client_sequence: current, method, path, native_sequence: native?.native_sequence ?? null })
    let pending
    try { pending = originalFetch.apply(this, args) }
    catch (error) { emit('fetch_failed', { client_sequence: current, error_type: error.name }); throw error }
    return pending.then(response => {
      const id = response.headers.get('x-auto-plan-test-input'), originalJson = response.json
      emit('fetch_received', { client_sequence: current, request_id: id, status: response.status })
      response.json = function (...jsonArgs) {
        emit('json_started', { client_sequence: current, request_id: id })
        let parsed
        try { parsed = originalJson.apply(this, jsonArgs) }
        catch (error) { emit('json_failed', { client_sequence: current, request_id: id, error_type: error.name }); throw error }
        return parsed.then(value => {
          const base = Number.isInteger(value?.base_version) ? value.base_version : null
          emit('json_completed', { client_sequence: current, request_id: id,
            base_version: base, input_version: Number.isInteger(value?.input_version) ? value.input_version : null })
          if (path.endsWith('/preview') && value?.valid === true) preview = { id, base_version: base }
          return value
        }, error => { emit('json_failed', { client_sequence: current, request_id: id, error_type: error.name }); throw error })
      }
      return response
    }, error => { emit('fetch_failed', { client_sequence: current, error_type: error.name }); throw error })
  }
  const resourceObserver = target.PerformanceObserver ? new target.PerformanceObserver(list => {
    for (const entry of list.getEntries()) if (entry.name.includes(basePath + '/api/inputs')) emit('resource_finished', {
      resource_url: entry.name, start_ms: entry.startTime, response_end_ms: entry.responseEnd,
      encoded_bytes: entry.encodedBodySize, decoded_bytes: entry.decodedBodySize })
  }) : null
  resourceObserver?.observe({ type: 'resource', buffered: true })
  const api = { events, stop() { target.fetch = originalFetch; observer?.disconnect(); resourceObserver?.disconnect(); target.document?.removeEventListener('click', nativeClick, true) } }
  target.__inputConsumptionTrace = api
  return api
}
