/** Own the opt-in functional probe through the shared phase guardian. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync,readFileSync } from 'node:fs'
import { resolve,dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tmpdir } from 'node:os'
import { guardFunctionalWorker } from './functional-worker.mjs'
const directory=dirname(fileURLToPath(import.meta.url)),root=resolve(directory,'../../..')
const enabled=process.env.AUTO_PLAN_INITIAL_IMPORT_LOAD_BROWSER==='1'
test('first import uses actual stale upload/retry with four real input editors and complete transient computation',
  {skip:!enabled&&'Set AUTO_PLAN_INITIAL_IMPORT_LOAD_BROWSER=1.',timeout:365000},async()=>{
    const output=mkdtempSync(resolve(tmpdir(),'auto-plan-initial-import-browser-'))
    process.stdout.write('Evidence '+output+'\n')
    const result=await guardFunctionalWorker({script:resolve(directory,'initial-import-browser-worker.mjs'),output,cwd:root,
      sourceFiles:[fileURLToPath(import.meta.url),resolve(directory,'initial-import-browser-support.mjs')]})
    assert.equal(result.signal,null);assert.equal(result.code,0,'Actual functional workflow failed; evidence '+output)
    const report=JSON.parse(readFileSync(resolve(output,'report.json')))
    assert.equal(report.status,'functional_pass');assert.equal(report.acceptance,'not_run');assert.deepEqual(report.repeats,[])
  })
