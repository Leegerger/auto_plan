import { test, expect } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

test('a new production day expires previews and quotes while retaining local intent', async ({ page }) => {
  const clockFile=process.env.AUTO_PLAN_BROWSER_CLOCK_FILE
  test.skip(!clockFile, 'Requires the external clock-file browser adapter')
  const headers={Origin:process.env.AUTO_PLAN_BROWSER_EXTERNAL_URL!, 'X-Auto-Plan-Request':'1'}
  await writeFile(clockFile!,'2026-10-14T07:59:00+08:00')
  await page.goto('/planning/login')
  await page.getByRole('textbox',{name:'公司账号'}).fill('SHARED_EDITOR')
  await page.getByLabel('公司密码',{exact:true}).fill('company-secret')
  await page.getByRole('button',{name:'登录',exact:true}).click()
  await expect(page.getByRole('heading',{name:'工作台',exact:true})).toBeVisible()
  const saved=await page.request.post('/planning/api/plans/save',{headers,data:{
    base_revision:0,input_version:1,latest_adoption:0,operation_id:crypto.randomUUID(),pending:true,
    operations:[{kind:'replace_plan',plan:{initial_states:[{line_code:'L',recipe_code:'R'}],
      segments:[{id:'future-s',date:'2026-10-14',line_code:'L',order:0,model_code:'P',quantity:100,hours:'3'}]}},
      {kind:'confirm_day_capacity',observed_at:'2026-10-14T07:59:00+08:00',
        confirmations:[{line_code:'L',current_recipe:'S',remaining_hours:'1/60'}]}]}})
  expect(saved.ok(),await saved.text()).toBe(true)
  await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:'排程',exact:true}).click()
  await page.getByRole('button',{name:'展开待处理需求',exact:true}).click()
  await page.getByRole('button',{name:'选择需求 M 2026-10-12',exact:true}).click()
  await page.getByRole('button',{name:'按需补排',exact:true}).click()
  await page.getByLabel('补排投入起日',{exact:true}).fill('2026-10-15')
  await page.getByLabel('补排投入终日',{exact:true}).fill('2026-10-15')
  await page.getByLabel('补排线体',{exact:true}).selectOption('L')
  await page.getByLabel('补排调整范围起日',{exact:true}).fill('2026-10-15')
  await page.getByLabel('补排调整范围终日',{exact:true}).fill('2026-10-15')
  await page.getByLabel('补排调整范围线体',{exact:true}).selectOption(['L'])
  await page.getByLabel('补排合格产出目标',{exact:true}).fill('100')
  await page.getByRole('button',{name:'试算按时覆盖',exact:true}).click()
  await expect(page.getByRole('region',{name:'单线补排试算结果',exact:true})).toContainText('建议投入 100 片投入')
  await page.getByRole('button',{name:'选用完整安排并预览',exact:true}).click()
  await expect(page.getByText('预览完成，尚未共享',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:'按需补排',exact:true}).click()
  await page.getByLabel('补排合格产出目标',{exact:true}).fill('50')
  await page.getByRole('button',{name:'试算按时覆盖',exact:true}).click()
  await expect(page.getByRole('region',{name:'单线补排试算结果',exact:true})).toContainText('建议投入 50 片投入')
  await page.getByRole('button',{name:'比较授权线体',exact:true}).click()
  await expect(page.getByRole('region',{name:'独立线体补排比较',exact:true})).toBeVisible()
  const before=await (await page.request.get('/planning/api/plans/current')).json()

  await writeFile(clockFile!,'2026-10-14T07:59:30+08:00')
  const sameDay=page.waitForResponse(response=>response.url().endsWith('/api/plans/current') && response.request().method()==='GET')
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
  expect((await (await sameDay).json()).time.production_date).toBe('2026-10-13')
  await expect(page.getByRole('region',{name:'计划状态'})).toContainText('本机预览')
  await expect(page.getByRole('region',{name:'单线补排试算结果',exact:true})).toBeVisible()
  await expect(page.getByRole('region',{name:'独立线体补排比较',exact:true})).toBeVisible()

  await writeFile(clockFile!,'2026-10-14T08:01:00+08:00')
  const refreshed=page.waitForResponse(response=>response.url().endsWith('/api/plans/current') && response.request().method()==='GET')
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')))
  const after=await (await refreshed).json()
  for(const key of ['revision','input_version','latest_adoption','context','plan'])expect(after[key]).toEqual(before[key])
  expect(after.time.window_start).toBe(before.time.window_start)
  expect(after.time.production_date).toBe('2026-10-14')
  await expect(page.getByRole('region',{name:'计划状态'})).toContainText('生产日已变化：2026-10-13 → 2026-10-14')
  await expect(page.getByRole('region',{name:'计划状态'})).toContainText('待重新预览')
  await expect(page.getByRole('region',{name:'单线补排试算结果',exact:true})).toHaveCount(0)
  await expect(page.getByRole('region',{name:'独立线体补排比较',exact:true})).toHaveCount(0)
  await expect(page.getByLabel('补排合格产出目标',{exact:true})).toHaveValue('50')
  await expect(page.getByLabel('补排投入起日',{exact:true})).toHaveValue('2026-10-15')
  await expect(page.getByLabel('补排投入终日',{exact:true})).toHaveValue('2026-10-15')
  await expect(page.getByLabel('补排线体',{exact:true})).toHaveValue('L')
  await expect(page.getByLabel('补排调整范围起日',{exact:true})).toHaveValue('2026-10-15')
  await expect(page.getByLabel('补排调整范围终日',{exact:true})).toHaveValue('2026-10-15')
  await page.getByRole('button',{name:/本机临时稿/}).click()
  const actions=page.getByRole('region',{name:'本机完整动作队列',exact:true})
  await expect(actions.getByRole('checkbox')).toHaveCount(1)
  await expect(actions.getByRole('checkbox')).toBeChecked()
  if(process.env.AUTO_PLAN_SPEC49_EVIDENCE){
    const evidence=join(process.env.AUTO_PLAN_SPEC49_EVIDENCE,'clock')
    await mkdir(evidence,{recursive:true})
    await writeFile(join(evidence,'responses.json'),JSON.stringify({before,after},null,2)+'\n')
    await page.screenshot({path:join(evidence,'retained-local-action.png')})
  }
})
