// 仅由隔离集成测试传入临时凭据；所有操作只针对合成项目和本机 Agent。
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { chromium } from 'playwright'

const settings = JSON.parse(await readFile(process.argv[2], 'utf8'))
assert.equal(new URL(settings.url).hostname, '127.0.0.1')
const output = resolve('../runtime/verification/node-files-ui')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({
  headless: true,
  ...(process.platform === 'win32' ? { channel: 'msedge' } : {}),
})
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: 'zh-CN' })
page.setDefaultTimeout(15000)
const failures = []
page.on('pageerror', (error) => failures.push(error.message))
page.on('response', (response) => {
  if (response.url().includes('/api/v1/') && response.status() >= 400)
    failures.push(`${response.status()} ${new URL(response.url()).pathname}`)
})
try {
  await page.goto(settings.url + '/#/nodes/' + settings.node_id)
  await page.getByLabel('账号', { exact: true }).fill(settings.username)
  await page.getByLabel('密码', { exact: true }).fill(settings.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByRole('heading', { name: '隔离执行电脑', exact: true }).waitFor()
  await page.getByLabel('电脑任务所属项目').selectOption(settings.project_id)
  await page.getByRole('link', { name: '日志与结果 →' }).first().waitFor()
  await page.screenshot({ path: resolve(output, 'computer.png'), fullPage: true, animations: 'disabled' })
  await page.getByRole('button', { name: '在这台电脑上创建任务', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '创建任务', exact: true })
  await dialog.getByLabel('任务名称', { exact: true }).fill('网页创建合成文件任务')
  await dialog.getByLabel(/^程序/).selectOption(settings.program_id)
  assert.equal(await dialog.getByLabel('执行电脑', { exact: true }).inputValue(), settings.node_id)
  await dialog.getByRole('button', { name: '选择多个文件', exact: true }).click()
  const picker = page.getByRole('region', { name: '执行电脑文件选择器' })
  await picker.getByLabel('甲.txt', { exact: true }).check()
  await picker.getByLabel('乙.txt', { exact: true }).check()
  await picker.getByRole('button', { name: '使用选中的 2 个文件', exact: true }).click()
  await dialog.getByRole('button', { name: '选择结果保存文件夹', exact: true }).click()
  await picker.getByRole('button', { name: '选择当前文件夹', exact: true }).click()
  await page.screenshot({ path: resolve(output, 'task-files.png'), fullPage: true, animations: 'disabled' })
  const createdResponse = page.waitForResponse((response) => response.request().method() === 'POST' &&
    new URL(response.url()).pathname.endsWith('/api/v1/tasks'))
  await dialog.getByRole('button', { name: '创建任务', exact: true }).click()
  const response = await createdResponse
  assert.equal(response.status(), 201)
  const task = await response.json()
  const row = page.getByRole('row').filter({ hasText: '网页创建合成文件任务' })
  await row.getByRole('button', { name: '立即运行', exact: true }).click()
  const runResponse = page.waitForResponse((response) => response.request().method() === 'POST' &&
    new URL(response.url()).pathname.endsWith(`/tasks/${task.id}/runs`))
  await page.getByRole('button', { name: '确认运行', exact: true }).click()
  const accepted = await runResponse
  assert.equal(accepted.status(), 202)
  const admitted = await accepted.json()
  let detail
  const deadline = Date.now() + 25000
  while (Date.now() < deadline) {
    const result = await page.request.get(`${settings.url}/api/v1/runs/${admitted.run_id}`, {
      headers: { Authorization: settings.authorization },
    })
    assert.equal(result.status(), 200)
    detail = await result.json()
    if (!['queued', 'leased', 'running'].includes(detail.state)) break
    await new Promise((done) => setTimeout(done, 200))
  }
  assert.equal(detail.state, 'succeeded')
  assert.equal(detail.file_status.save_state, 'succeeded')
  assert.equal(detail.artifact_state, 'complete')
  // Hash导航保留当前登录状态，验证运行详情与独立保存信息。
  await page.evaluate((runId) => { location.hash = '/runs/' + runId }, admitted.run_id)
  await page.getByRole('heading', { name: '网页创建合成文件任务', exact: true }).waitFor()
  await page.getByText('程序执行、平台归档和本机另存分别记录。另存失败不代表脚本需要重新运行。', { exact: true }).waitFor()
  await page.getByText('已保存到执行电脑，文件清单见下方。', { exact: true }).waitFor()
  await page.screenshot({ path: resolve(output, 'run-results.png'), fullPage: true, animations: 'disabled' })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '页面存在横向溢出')
  assert.deepEqual(failures, [])
  await writeFile(resolve(output, 'result.json'), JSON.stringify({
    passed: true,
    checks: ['电脑详情', '创建任务预选电脑', '真实远程文件多选', '授权结果目录选择',
      '网页提交运行', '真实Agent执行', '本机保存与平台归档', '详情状态', '1440像素无横向溢出'],
    task_id: task.id, run_id: admitted.run_id,
  }, null, 2))
  process.stdout.write('浏览器完整操作验收通过，证据已保存\n')
} catch (error) {
  await page.screenshot({ path: resolve(output, 'failure.png'), fullPage: true })
  throw error
} finally {
  await browser.close()
}
