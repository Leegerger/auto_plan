import { test, expect, type Page } from '@playwright/test'

const jobId='11111111-1111-4111-8111-111111111111'
const candidateId='22222222-2222-4222-8222-222222222222'
const payloadSha='a'.repeat(64)
const job={job_id:jobId,actor:'SHARED_EDITOR',fifo:1,source_revision:1,input_version:1,latest_adoption:0,state:'queued',stop_requested_at:null,stop_reason:null,progress:null,result:null,created_at:'2026-10-14T00:00:00+00:00',started_at:null,ended_at:null,candidates:[]}

async function login(page:Page,account='SHARED_EDITOR') {
  await page.goto('/planning/login')
  await page.getByRole('textbox',{name:'公司账号'}).fill(account)
  await page.getByLabel('公司密码',{exact:true}).fill('company-secret')
  await page.getByRole('button',{name:'登录',exact:true}).click()
  await expect(page.getByRole('button',{name:'退出登录',exact:true})).toBeVisible()
}

test('lost submission is verified with its original UUID after reload and never resubmitted',async({page})=>{
  test.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE!=='1','Requires explicit frozen shared-plan fixture')
  let submissions:Record<string,unknown>[]=[]
  const verifications:string[]=[]
  await page.route(/\/api\/jobs(?:\/.*)?$/,async route=>{
    const path=new URL(route.request().url()).pathname
    if(path.endsWith('/verify')) {
      const key=route.request().postDataJSON().submission_id
      verifications.push(key)
      await route.fulfill({json:{status:'ACCEPTED',submission_id:key,job_id:jobId}})
    } else if(route.request().method()==='POST') {
      submissions.push(route.request().postDataJSON())
      await route.abort('failed')
    } else if(path.endsWith('/jobs')) await route.fulfill({json:{jobs:submissions.length ? [job] : [],queue_capacity:8,worker_limit:2}})
    else await route.fulfill({json:job})
  })
  await login(page)
  const sharedHead=await(await page.request.get('/planning/api/plans/current')).json()
  await page.getByRole('link',{name:'共享计划',exact:true}).click()
  const panel=page.getByRole('region',{name:'计算任务'})
  await expect(panel).toBeVisible()
  await page.getByLabel('计算范围起日',{exact:true}).fill('2026-10-15')
  await page.getByLabel('计算范围终日',{exact:true}).fill('2026-10-16')
  await page.getByLabel('计算线体',{exact:true}).selectOption(['L'])
  await page.getByRole('button',{name:'提交计算任务',exact:true}).click()
  await expect(panel).toContainText('前次计算提交结果未知')
  await expect(page.getByRole('button',{name:'提交计算任务',exact:true})).toBeDisabled()
  expect(submissions).toHaveLength(1)
  expect(submissions[0]).toMatchObject({base_revision:sharedHead.revision,input_version:sharedHead.input_version,latest_adoption:sharedHead.latest_adoption,request:{scope:{start_date:'2026-10-15',end_date:'2026-10-16',line_codes:['L'],model_codes:[]},mode:'local',strategy:'hybrid',time_limit_seconds:20,max_candidates:1,native_threads:1}})
  expect(submissions[0]!.submission_id).toMatch(/^[0-9a-f-]{36}$/)
  await page.reload()
  await expect(page.getByRole('button',{name:'核实计算提交',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'核实计算提交',exact:true}).click()
  await expect(panel).toContainText('已核实计算任务已接收')
  expect(verifications).toEqual([submissions[0]!.submission_id])
  expect(submissions).toHaveLength(1)
  await expect(panel).toContainText(jobId)
  await expect(panel).toContainText('排队中')
  await page.getByRole('link',{name:'工作台',exact:true}).click()
  await page.getByRole('button',{name:'退出登录',exact:true}).click()
  expect(submissions).toHaveLength(1)
})

test('candidate selection enters the shared action queue as an original-source reference before explicit save',async({page})=>{
  test.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE!=='1','Requires explicit frozen shared-plan fixture')
  await login(page)
  const head=await(await page.request.get('/planning/api/plans/current')).json()
  const evaluated=await(await page.request.post('/planning/api/plans/preview',{headers:{Origin:`https://localhost:${process.env.AUTO_PLAN_BROWSER_PORT ?? '8774'}`,'X-Auto-Plan-Request':'1'},data:{base_revision:head.revision,input_version:head.input_version,latest_adoption:head.latest_adoption,operations:[]}})).json()
  const candidateSegment={...head.plan.segments[0],id:'candidate-a',quantity:300}
  const candidate={job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha,validity:{state:'requires_recheck',current_input_version:head.input_version},candidate:{id:'solver-local',source_revision:0,input_version:head.input_version,latest_adoption:head.latest_adoption,plan:{...head.plan,segments:[candidateSegment]},operation:{kind:'patch_plan',segments:[candidateSegment],remove_segments:['old-segment']},evaluation:evaluated.evaluation,objective:['0','1'],stage_bounds:[{stage:'gap',neighborhood:'local',status:'time_limit',objective:['0'],lower_bound:['0'],global_proof:false,detail:'局部阶段界限'}],eligibility:'pending_confirmation',tradeoff:'需求缺口优先，保留待确认项'},proposal:{base_revision:0,input_version:head.input_version,latest_adoption:head.latest_adoption,operations:[{kind:'apply_candidate',job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha}]}}
  let candidateViews=0,conflict=false
  const planCommands:{path:string;body:Record<string,unknown>}[]=[]
  await page.route(/\/api\/jobs(?:\/.*)?$/,async route=>{
    if(route.request().url().includes('/candidates/')){candidateViews++;await route.fulfill({json:candidate})}
    else await route.fulfill({json:{jobs:[{...job,state:'completed',candidates:[{candidate_id:candidateId,payload_sha:payloadSha,expired:false}],result:{status:'time_limit',elapsed_seconds:20,global_optimal:false,stage_bounds:[],issues:[]}}],queue_capacity:8,worker_limit:2}})
  })
  await page.route(/\/api\/plans\/(preview|save|adopt)$/,async route=>{
    const path=new URL(route.request().url()).pathname
    const body=route.request().postDataJSON()
    planCommands.push({path,body})
    if(path.endsWith('/preview')) {
      if(conflict)await route.fulfill({status:409,json:{detail:'候选原始输入已变化，请按原输入重新计算',conflicts:[]}})
      else await route.fulfill({json:{...evaluated,plan:{...head.plan,segments:[candidateSegment]},changes:[{date:'2026-10-15',line_code:'L',model_code:'M',field:'投入数量',before:200,after:300}]}})
    } else await route.fulfill({json:{status:'COMMITTED',revision:head.revision+1,latest_adoption:head.latest_adoption}})
  })
  await page.getByRole('link',{name:'共享计划',exact:true}).click()
  const panel=page.getByRole('region',{name:'计算任务'})
  await page.getByRole('button',{name:'查看候选内容',exact:true}).click()
  await expect(panel).toContainText('需求缺口优先，保留待确认项')
  await expect(panel).toContainText('待业务确认')
  await expect(panel).toContainText('局部阶段界限')
  await page.getByLabel('选择候选变更 segments:candidate-a',{exact:true}).check()
  await page.getByRole('button',{name:'预览所选候选变更',exact:true}).click()
  await expect(page.getByText('预览完成，尚未共享',{exact:true})).toBeVisible()
  expect(planCommands).toHaveLength(1)
  expect(planCommands[0]!.body).toEqual({base_revision:0,input_version:head.input_version,latest_adoption:head.latest_adoption,operations:[{kind:'apply_candidate',job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha,selection:['segments:candidate-a'],action_id:expect.any(String)}]})
  await expect(page.getByRole('region',{name:'本机完整动作队列'})).toContainText('计算候选')
  await expect(page.getByRole('button',{name:'提交计算任务',exact:true})).toBeDisabled()
  await expect(panel).toContainText('请先共享保存或处理本机动作')
  await page.getByRole('button',{name:'共享保存',exact:true}).click()
  await expect(page.getByText(/已共享保存修订/)).toBeVisible()
  expect(planCommands.filter(row=>row.path.endsWith('/adopt'))).toHaveLength(0)
  expect(planCommands.find(row=>row.path.endsWith('/save'))!.body).toMatchObject({base_revision:0,operations:[{kind:'apply_candidate',selection:['segments:candidate-a']}]})
  await page.getByRole('button',{name:'预览完整候选',exact:true}).click()
  expect(planCommands.at(-1)!.body.operations).toEqual([{kind:'apply_candidate',job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha,action_id:expect.any(String)}])
  conflict=true
  await page.getByRole('button',{name:'预览本机修改',exact:true}).click()
  await expect(page.getByText('候选原始输入已变化，请按原输入重新计算',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'按最新资料重新预览',exact:true}).click()
  expect(planCommands.at(-1)!.body.base_revision).toBe(0)
  expect(planCommands.at(-1)!.body.input_version).toBe(head.input_version)
  expect(candidateViews).toBe(1)
})

test('stop and original-input rerun retain an unknown key until verification acknowledges void',async({page})=>{
  test.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE!=='1','Requires explicit frozen shared-plan fixture')
  await page.clock.install()
  let current={...job,state:'running',progress:{stage:'候选检查',detail:'正在检查保护范围'},candidates:[{candidate_id:candidateId,payload_sha:payloadSha,expired:true}]}
  let stopped=0,metadataReads=0,verifyCalls=0,candidateViews=0
  const reruns:{submission_id:string}[]=[]
  await page.route(/\/api\/jobs(?:\/.*)?$/,async route=>{
    const path=new URL(route.request().url()).pathname
    if(path.endsWith('/stop')) {
      stopped++;current={...current,state:'stopped'}
      await route.fulfill({json:{job_id:jobId,state:'stopped',stop_requested_at:'2026-10-14T01:00:00+00:00'}})
    } else if(path.endsWith('/rerun')) {
      const command=route.request().postDataJSON();reruns.push(command)
      if(reruns.length===1)await route.abort('failed')
      else await route.fulfill({json:{status:'ACCEPTED',submission_id:command.submission_id,job_id:'33333333-3333-4333-8333-333333333333'}})
    } else if(path.endsWith('/verify')) {
      verifyCalls++
      const key=route.request().postDataJSON().submission_id
      expect(key).toBe(reruns[0]!.submission_id)
      if(verifyCalls===1)await route.fulfill({status:503,json:{detail:'仍无法判定提交结果',state:'unknown'}})
      else await route.fulfill({json:{status:'ABORTED_VOID',submission_id:key}})
    } else if(path.includes('/candidates/')) {candidateViews++;await route.abort('failed')}
    else {metadataReads++;await route.fulfill({json:{jobs:[current],queue_capacity:8,worker_limit:2}})}
  })
  await login(page)
  await page.getByRole('link',{name:'共享计划',exact:true}).click()
  const panel=page.getByRole('region',{name:'计算任务'})
  await expect(panel).toContainText('正在检查保护范围')
  await expect(page.getByRole('button',{name:'查看候选内容',exact:true})).toBeDisabled()
  await expect(page.getByRole('button',{name:'停止任务',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'停止任务',exact:true}).click()
  await expect(panel).toContainText('停止请求已接收')
  expect(stopped).toBe(1)
  await expect(panel).toContainText('已停止')
  const readsBefore=metadataReads
  current={...current,state:'interrupted'}
  await page.clock.runFor(2100)
  await expect(panel).toContainText('已中断')
  expect(metadataReads).toBeGreaterThan(readsBefore)
  expect(candidateViews).toBe(0)
  await page.getByRole('button',{name:'按原输入重新计算',exact:true}).click()
  await expect(panel).toContainText('前次计算提交结果未知')
  await page.reload()
  await page.getByRole('button',{name:'核实计算提交',exact:true}).click()
  await expect(panel).toContainText('仍无法判定提交结果')
  await expect(page.getByRole('button',{name:'按原输入重新计算',exact:true})).toBeDisabled()
  await page.getByRole('button',{name:'核实计算提交',exact:true}).click()
  await expect(panel).toContainText('已核实前次计算提交作废')
  expect(reruns).toHaveLength(1)
  await page.getByRole('button',{name:'按原输入重新计算',exact:true}).click()
  await expect(panel).toContainText('计算任务已接收')
  expect(reruns).toHaveLength(2)
  expect(Object.keys(reruns[1]!)).toEqual(['submission_id'])
  expect(reruns[1]!.submission_id).not.toBe(reruns[0]!.submission_id)
})

test('retained candidates show source validity separately from execution, eligibility and proof',async({page})=>{
  test.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE!=='1','Requires explicit frozen shared-plan fixture')
  await login(page)
  const head=await(await page.request.get('/planning/api/plans/current')).json()
  let planWrites=0
  const candidate={job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha,validity:{state:'view_only',current_input_version:head.input_version},candidate:{id:'solver-local',source_revision:1,input_version:0,latest_adoption:0,plan:head.plan,operation:{kind:'patch_plan',segments:head.plan.segments},evaluation:head.evaluation,objective:['0'],stage_bounds:[{stage:'gap',neighborhood:'one-line',status:'optimal',objective:['0'],lower_bound:['0'],global_proof:false,detail:'单线范围证明'}],eligibility:'executable',tradeoff:'原输入下可执行'},proposal:{base_revision:1,input_version:0,latest_adoption:0,operations:[{kind:'apply_candidate',job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha}]}}
  await page.route(/\/api\/jobs(?:\/.*)?$/,async route=>{
    if(route.request().url().includes('/candidates/'))await route.fulfill({json:candidate})
    else await route.fulfill({json:{jobs:[{...job,state:'completed',candidates:[{candidate_id:candidateId,payload_sha:payloadSha,expired:false}],result:{status:'time_limit',solve_status:'time_limit',elapsed_seconds:20,global_optimal:false,stage_bounds:[],issues:[]}}],queue_capacity:8,worker_limit:2}})
  })
  await page.route(/\/api\/plans\/(preview|save|adopt)$/,async route=>{planWrites++;await route.abort('failed')})
  await page.getByRole('link',{name:'共享计划',exact:true}).click()
  const panel=page.getByRole('region',{name:'计算任务'})
  await expect(panel).toContainText('执行状态 已完成')
  await expect(panel).toContainText('达到计算时限')
  await expect(panel).toContainText('未证明全局最优')
  await page.getByRole('button',{name:'查看候选内容',exact:true}).click()
  await expect(panel).toContainText('完整输入已变化，候选仅可查看')
  await expect(panel).toContainText('原始检查 可执行候选')
  await expect(panel).toContainText('单线范围证明')
  await expect(page.getByRole('button',{name:'预览完整候选',exact:true})).toBeDisabled()
  expect(planWrites).toBe(0)
  await page.screenshot({path:'/tmp/auto-plan-spec-23/jobs-ui.png',fullPage:true})
})

test('an administrator can stop a known job without business-view reads',async({page})=>{
  const businessReads:string[]=[]
  const stops:string[]=[]
  await page.route('**/api/auth/login',route=>route.fulfill({json:{user_id:'ADMIN',roles:['administrator']}}))
  await page.route('**/api/admin/authorizations',route=>route.fulfill({json:[]}))
  await page.route(/\/api\/(jobs|plans|inputs)(?:\/.*)?$/,async route=>{
    const path=new URL(route.request().url()).pathname
    if(route.request().method()==='POST' && path.endsWith('/stop')) {
      stops.push(path)
      await route.fulfill({json:{job_id:jobId,state:'running',stop_requested_at:'2026-10-14T01:00:00+00:00'}})
    } else {businessReads.push(path);await route.abort('failed')}
  })
  await login(page,'ADMIN')
  await page.getByRole('link',{name:'账号授权',exact:true}).click()
  const panel=page.getByRole('region',{name:'管理员停止已知任务'})
  await expect(panel).toBeVisible()
  await page.getByLabel('已知任务编号',{exact:true}).fill(jobId)
  await page.getByRole('button',{name:'停止指定任务',exact:true}).click()
  await expect(panel).toContainText('停止请求已接收')
  expect(stops).toEqual([`/planning/api/jobs/${jobId}/stop`])
  expect(businessReads).toEqual([])
  await expect(page.getByRole('link',{name:'共享计划',exact:true})).toHaveCount(0)
})

test('a viewer can inspect a retained candidate after worker failure with no editing commands',async({page})=>{
  test.skip(process.env.AUTO_PLAN_BROWSER_TRANSACTIONS_FIXTURE!=='1','Requires explicit frozen shared-plan fixture')
  await page.route('**/api/auth/login',async route=>{
    const response=await route.fetch()
    await route.fulfill({response,json:{...await response.json(),roles:['viewer']}})
  })
  await login(page)
  const head=await(await page.request.get('/planning/api/plans/current')).json()
  const candidate={job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha,validity:{state:'requires_recheck',current_input_version:head.input_version},candidate:{id:'solver-local',source_revision:head.revision,input_version:head.input_version,latest_adoption:head.latest_adoption,plan:head.plan,operation:{kind:'patch_plan',segments:head.plan.segments},evaluation:head.evaluation,objective:['0'],stage_bounds:[],eligibility:'executable',tradeoff:'已完整发布的候选'},proposal:{base_revision:head.revision,input_version:head.input_version,latest_adoption:head.latest_adoption,operations:[{kind:'apply_candidate',job_id:jobId,candidate_id:candidateId,payload_sha:payloadSha}]}}
  const mutations:string[]=[]
  await page.route(/\/api\/jobs(?:\/.*)?$/,async route=>{
    if(route.request().method()==='POST'){mutations.push(route.request().url());await route.abort('failed')}
    else if(route.request().url().includes('/candidates/'))await route.fulfill({json:candidate})
    else await route.fulfill({json:{jobs:[{...job,actor:'SECOND_EDITOR',state:'failed',candidates:[{candidate_id:candidateId,payload_sha:payloadSha,expired:false}],result:{reason:'worker_exited_without_result'}}],queue_capacity:8,worker_limit:2}})
  })
  await page.getByRole('link',{name:'共享计划',exact:true}).click()
  const panel=page.getByRole('region',{name:'计算任务'})
  await expect(panel).toContainText('执行状态 失败')
  await expect(panel).toContainText('计算进程已退出，未返回完整结果')
  await page.getByRole('button',{name:'查看候选内容',exact:true}).click()
  await expect(panel).toContainText('已完整发布的候选')
  await expect(panel.getByRole('checkbox').first()).toBeDisabled()
  await expect(page.getByRole('button',{name:'提交计算任务',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'按原输入重新计算',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'停止任务',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'预览完整候选',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'共享保存',exact:true})).toHaveCount(0)
  expect(mutations).toEqual([])
})
