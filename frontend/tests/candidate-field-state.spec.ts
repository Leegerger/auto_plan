import { test, expect, type Page } from '@playwright/test'

const headers = { Origin: `https://localhost:${process.env.AUTO_PLAN_BROWSER_PORT ?? '8804'}`, 'X-Auto-Plan-Request': '1' }

async function shared(page:Page) {
  return (await page.request.get('/planning/api/plans/current')).json()
}

async function save(page:Page, operations:unknown[]) {
  const head = await shared(page)
  const response = await page.request.post('/planning/api/plans/save', { headers, data: {
    base_revision:head.revision,input_version:head.input_version,latest_adoption:head.latest_adoption,
    operation_id:crypto.randomUUID(),pending:true,operations } })
  expect(response.ok(), await response.text()).toBeTruthy()
}

async function calculate(page:Page, model:string) {
  await page.reload()
  await page.getByRole('navigation', {name:'主要导航'}).getByRole('link', {name:'排程',exact:true}).click()
  await page.getByRole('link', {name:'计算任务',exact:true}).click()
  await page.getByLabel('计算范围起日', {exact:true}).fill('2026-10-15')
  await page.getByLabel('计算范围终日', {exact:true}).fill('2026-10-15')
  await page.getByLabel('计算线体', {exact:true}).selectOption(['L'])
  await page.getByLabel('计算机种', {exact:true}).selectOption([model])
  await page.getByLabel('计算时限', {exact:true}).fill('10')
  const [response] = await Promise.all([
    page.waitForResponse(response=>response.url().endsWith('/api/jobs') && response.request().method()==='POST'),
    page.getByRole('button', {name:'提交计算任务',exact:true}).click(),
  ])
  const accepted = await response.json()
  const job = page.getByRole('listitem').filter({hasText:accepted.job_id})
  await expect(job.getByRole('button', {name:'查看候选内容',exact:true})).toBeVisible({timeout:20000})
  await job.getByRole('button', {name:'查看候选内容',exact:true}).click()
}

test('real candidates explain unknown origins and original remaining jobs before draft application', async ({page}) => {
  test.skip(process.env.AUTO_PLAN_BROWSER_CANDIDATE_STATES_FIXTURE !== '1', 'Requires candidate field-state fixture')
  test.setTimeout(60000)
  await page.goto('/planning/login')
  await page.getByRole('textbox', {name:'公司账号'}).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码', {exact:true}).fill('company-secret')
  await page.getByRole('button', {name:'登录',exact:true}).click()
  await expect(page.getByRole('button', {name:'退出登录',exact:true})).toBeVisible()
  let head = await shared(page)
  await calculate(page, 'M')
  const evidence = page.getByRole('region', {name:'候选现场依据',exact:true})
  await expect(evidence).toContainText('首次切机尚未计入')
  await page.getByRole('button', {name:'预览完整候选',exact:true}).click()
  await expect(page.getByText('预览完成，尚未共享', {exact:true})).toBeVisible()
  await page.getByRole('button', {name:'查看预览',exact:true}).click()
  await expect(page.getByRole('button', {name:'正式采用',exact:true})).toBeDisabled()
  await page.getByRole('tabpanel', {name:'变更预览'}).getByText('保存为待处理草稿', {exact:true}).click()
  await page.getByRole('button', {name:'共享保存',exact:true}).click()
  await expect(page.getByText(/已共享保存修订/)).toBeVisible()
  head = await shared(page)
  expect(head.pending).toBe(true)
  expect(head.latest_adoption).toBe(0)
  const rows = head.plan.segments.map((row:{id:string}) => row.id)
  await save(page, [{kind:'patch_plan',remove_segments:rows}, {kind:'confirm_line_states',rows:[{
    line_code:'L',effective_at:'2026-10-12T08:00:00+08:00',state:'unfinished_switch',recipe_code:null,
    unfinished_switch:{job_id:'actual-native-r-s',started_at:'2026-10-11T20:00:00+08:00',from_recipe:'R',to_recipe:'S',remaining_hours:'4',standard_hours:'12',completed_hours:'8'},source:'交班记录'}]}])
  await calculate(page, 'P')
  await expect(evidence).toContainText('原作业 actual-native-r-s')
  await expect(evidence).toContainText('现场剩余 4 小时')
  await expect(evidence).toContainText('现场已完成 8 小时')
  await expect(evidence).toContainText('候选预留 4.0000 小时')
  await expect(evidence).toContainText('连续')
  await expect(evidence).toContainText('目标 Recipe 尚未就绪')
  await page.getByRole('button', {name:'预览完整候选',exact:true}).click()
  await expect(page.getByText('预览完成，尚未共享', {exact:true})).toBeVisible()
  await page.getByRole('button', {name:'查看预览',exact:true}).click()
  await expect(page.getByRole('region', {name:'正式采用所需现场依据'})).toContainText('连续')
  await expect(page.getByRole('button', {name:'正式采用',exact:true})).toBeDisabled()
  await page.getByRole('tabpanel', {name:'变更预览'}).getByText('保存为待处理草稿', {exact:true}).click()
  await page.getByRole('button', {name:'共享保存',exact:true}).click()
  await expect(page.getByText(/已共享保存修订/)).toBeVisible()
  head = await shared(page)
  expect(head.plan.switches).toHaveLength(1)
  expect(head.plan.switches[0]).toMatchObject({id:'actual-native-r-s',from_recipe:'R',to_recipe:'S',allocations:[{hours:'4'}]})
  expect(head.evaluation.switch_count).toBe(1)
  expect(head.latest_adoption).toBe(0)
})
