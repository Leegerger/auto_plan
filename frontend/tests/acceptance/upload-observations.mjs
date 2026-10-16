/** Response-bound TEST-only transport attestation. No inferred byte proof. */
import assert from 'node:assert/strict'
export function attestUpload(observation, expected) {
  assert.ok(observation && expected.id, 'No actual response-bound receipt proof.')
  assert.equal(observation.instrumentation, 'test-only passive ASGI receive')
  assert.equal(observation.id, expected.id, 'Receipt belongs to another response.')
  assert.equal(observation.method, 'POST')
  const url=new URL(expected.url)
  assert.equal(observation.path, url.pathname)
  assert.deepEqual(observation.query, Object.fromEntries(url.searchParams))
  assert.equal(observation.status, expected.status)
  assert.equal(observation.response_complete, true)
  assert.equal(observation.complete, true, 'Native stream receipt remains incomplete.')
  assert.equal(observation.length, expected.length)
  assert.equal(observation.sha256, expected.sha256)
  return {...observation, provider:'test-only server-observed received bytes'}
}

export async function readUploadObservation(path, id, options={}) {
  const {readFileSync}=await import('node:fs')
  const {setTimeout:delay}=await import('node:timers/promises')
  assert.ok(typeof id==='string' && id, 'No actual response nonce; receipt remains unconfirmed.')
  const deadline=Date.now()+(options.timeout_ms??1000)
  do {
    let raw=''
    try{raw=readFileSync(path,'utf8')}catch(error){if(error.code!=='ENOENT')throw error}
    // The writer appends after sending the actual response. Ignore an in-flight
    // final line until its newline arrives; never reinterpret a partial record.
    const matches=raw.split('\n').slice(0,-1).filter(line=>line && JSON.parse(line).id===id)
    assert.ok(matches.length<=1,'Repeated response-bound upload observations.')
    if(matches.length===1)return {observation:JSON.parse(matches[0]),raw:matches[0]}
    if(Date.now()>=deadline)break
    await delay(Math.min(options.interval_ms??10,Math.max(1,deadline-Date.now())))
  }while(Date.now()<=deadline)
  throw new Error('No complete response-bound server receipt before deadline; transport remains unconfirmed.')
}
