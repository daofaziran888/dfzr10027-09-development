import { expect, test } from '@playwright/test'

type Evaluation = {
  verification_scope: number
  delivered_count: number
  pending_count: number
  excluded_count: number
  usable: boolean
  members: { user_id: string; '判定结果': string; '入选原因': string }[]
  pending_members: { user_id: string; '判定结果': string }[]
  excluded_members: { user_id: string; '判定结果': string }[]
  report: { member_count: number; region_distribution: Record<string, number>; total_ltv: number }
}

const API = '/api/cohort_extraction'

async function fetchJson<T>(page: import('@playwright/test').Page, path: string): Promise<T> {
  const response = await page.request.get(path)
  expect(response.ok()).toBeTruthy()
  return (await response.json()) as T
}

test('促销分群提取：排除、待判、交付与两轮重放全链路', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  page.on('pageerror', (e) => browserErrors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
  })

  await page.goto('/cohort_extraction')
  await expect(page.getByTestId('stat-scope')).toHaveText('3')
  await expect(page.getByTestId('stat-delivered')).toHaveText('1')
  await expect(page.getByTestId('stat-pending')).toHaveText('1')
  await expect(page.getByTestId('stat-excluded')).toHaveText('1')
  await expect(page.getByTestId('stat-usable')).toHaveText('有')

  // 三名候选：一名明确排除、一名待判、一名交付且给出可解释的入选原因
  await expect(page.locator('[data-testid="member-table"] tbody tr[data-testid^="member-"]')).toHaveCount(1)
  await expect(page.getByTestId('member-E10003')).toContainText('未命中排除标签')
  await expect(page.getByTestId('excluded-E10001')).toContainText('明确排除')
  await expect(page.getByTestId('pending-E10002')).toContainText('待判')
  await expect(page.getByTestId('pending-E10002')).toContainText('缺少用于排除判定的标签')

  // 第一轮提取
  await page.getByTestId('extract').click()
  const runRows = page.locator('[data-testid="run-table"] tbody tr[data-testid^="run-"]')
  await expect(runRows).toHaveCount(1)
  await expect(runRows.nth(0)).toContainText('1500')

  // 补齐待判顾客标签后再提交第二轮
  await page.getByTestId('backfill-input-E10002').fill('高价值')
  await page.getByTestId('backfill-E10002').click()
  await expect(page.getByTestId('stat-pending')).toHaveText('0')
  await expect(page.getByTestId('stat-delivered')).toHaveText('2')

  await page.getByTestId('extract').click()
  await expect(runRows).toHaveCount(2)
  await expect(runRows.nth(1)).toContainText('3700')

  // 两轮报表计数都能对上：第一轮 1 人 1500，第二轮 2 人 3700
  const reports = await fetchJson<{ items: Evaluation['report'][] }>(page, `${API}/reports`)
  expect(reports.items.map((r) => r.member_count)).toEqual([1, 2])
  expect(reports.items.map((r) => r.total_ltv)).toEqual([1500, 3700])

  // 重放第一轮旧名单，应与存档报表一致
  const firstRunId = (await runRows.nth(0).getAttribute('data-testid'))!.replace('run-', '')
  await page.getByTestId(`replay-${firstRunId}`).click()
  await expect(page.getByTestId('replay-panel')).toBeVisible()
  await expect(page.getByTestId('replay-panel')).toContainText('可复查')
  await expect(page.getByTestId('replay-panel')).toContainText('1500')

  // 后端也应确认重放一致
  const replay = await page.request.post(`${API}/runs/${firstRunId}/replay`)
  const replayBody = await replay.json()
  expect(replayBody.ok).toBe(true)
  expect(replayBody.entry.replayed_report.member_count).toBe(1)
  expect(replayBody.entry.replayed_report.total_ltv).toBe(1500)

  expect(browserErrors).toEqual([])
})
