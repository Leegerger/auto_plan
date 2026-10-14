import { test, expect } from '@playwright/test'

test('editor previews daily quantities and lossless hours with visible boundary issues', async ({ page }) => {
  await page.goto('/planning/login')
  await page.getByRole('textbox', { name: '公司账号' }).fill('admin')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('link', { name: '账号授权', exact: true }).click()
  await page.getByRole('textbox', { name: '授权账号' }).fill('PREVIEW_EDITOR')
  await page.getByRole('group', { name: '角色' }).getByText('业务编辑者', { exact: true }).click()
  await page.getByRole('button', { name: '保存授权' }).click()
  await expect(page.getByText('已保存 PREVIEW_EDITOR 的授权', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '退出登录' }).click()
  await page.getByRole('textbox', { name: '公司账号' }).fill('preview_editor')
  await page.getByLabel('公司密码', { exact: true }).fill('company-secret')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('link', { name: '输入资料', exact: true })).toBeVisible()
  const current = await (await page.request.get('/planning/api/inputs')).json()
  const changes = {
    recipes: [{ code: 'PREVIEW_R', name: '预览工艺', active: true }],
    lines: [{ code: 'PREVIEW_L', name: '预览线体', active: true }],
    models: [{ code: 'PREVIEW_M', name: '预览机种', active: true, recipe_code: 'PREVIEW_R' }],
    capacities: [{ month: '2026-10', recipe_code: 'PREVIEW_R', line_code: 'PREVIEW_L', pieces: 1000 }],
    line_yields: [{ month: '2026-10', percent: '100' }],
    model_yields: [{ month: '2026-10', model_code: 'PREVIEW_M', percent: '100' }],
    cycle_times: [{ model_code: 'PREVIEW_M', days: '0' }],
  }
  const applied = await page.request.post('/planning/api/inputs/apply', {
    headers: { Origin: 'https://localhost:8774', 'X-Auto-Plan-Request': '1' },
    data: { base_version: current.input_version, changes },
  })
  expect(applied.ok()).toBeTruthy()
  await page.getByRole('link', { name: '日内详情', exact: true }).click()
  await expect(page.getByRole('heading', { name: '日内详情与工时预览' })).toBeVisible()
  await page.getByLabel('详情生产日期', { exact: true }).fill('2026-10-14')
  await page.getByLabel('详情线体', { exact: true }).selectOption('PREVIEW_L')
  await page.getByRole('button', { name: '添加生产段', exact: true }).click()
  await page.getByLabel('机种 第1段', { exact: true }).selectOption('PREVIEW_M')
  await page.getByLabel('投入片数 第1段', { exact: true }).fill('2')
  await page.getByLabel('精确工时 第1段', { exact: true }).fill('0.024')
  await page.getByRole('button', { name: '预览精确工时', exact: true }).click()
  await expect(page.getByRole('table', { name: '预览问题' })).toContainText('超过精确工时上限1')
  await page.getByLabel('精确工时 第1段', { exact: true }).fill('0.024001')
  await page.getByRole('button', { name: '预览精确工时', exact: true }).click()
  await expect(page.getByText('容量与顺序通过', { exact: true })).toBeVisible()
  await expect(page.getByRole('table', { name: 'Recipe 容量依据' })).toContainText('0.0240')
  await page.getByRole('table', { name: 'Recipe 容量依据' }).getByText('查看精确值', { exact: true }).click()
  await expect(page.getByRole('table', { name: 'Recipe 容量依据' })).toContainText('24001/1000000')
  await page.getByLabel('投入片数 第1段', { exact: true }).fill('1')
  await page.getByLabel('精确工时 第1段', { exact: true }).fill('1/1000000')
  await page.getByRole('button', { name: '预览精确工时', exact: true }).click()
  await expect(page.getByRole('table', { name: 'Recipe 容量依据' })).toContainText('<0.0001')
  await page.getByRole('button', { name: '按数量生成工时依据', exact: true }).click()
  await expect(page.getByText('已生成精确工时依据，尚未保存', { exact: true })).toBeVisible()
  await page.screenshot({ path: '/tmp/auto-plan-spec-23/plan-preview-ui.png', fullPage: true })
})
