import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile, unlink } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { randomUUID } from 'node:crypto'
import { chromium } from 'playwright'

const configPath = resolve(
  process.env.WORKBENCH_E2E_SESSION ||
    '../runtime/integration/session.local.json',
)
const settings = JSON.parse(await readFile(configPath, 'utf8'))
const sanitize = (value) =>
  String(value)
    .replaceAll(settings.password, '[REDACTED]')
    .replaceAll(settings.database_url || '__not_present__', '[REDACTED]')
assert(
  new URL(settings.url).hostname === '127.0.0.1',
  '此冒烟脚本仅允许本地隔离后端',
)
const output = resolve('e2e-results')
await mkdir(output, { recursive: true })
const bundle = resolve(output, 'smoke-program.zip')
execFileSync(
  resolve('../.venv/Scripts/python.exe'),
  [
    '-c',
    'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.write(sys.argv[2],"main.py"); z.close()',
    bundle,
    resolve('e2e/fixtures/main.py'),
  ],
  { stdio: 'pipe' },
)
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
})
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
  locale: 'zh-CN',
  timezoneId: 'Asia/Shanghai',
})
const page = await context.newPage()
const errors = [],
  warnings = [],
  failures = [],
  checks = []
let nodeToken = ''
const nodeTokenFile = resolve(output, 'node-token.local.txt')
let importedConfig = ''
let importedToken = ''
page.on('pageerror', (error) => errors.push(error.message))
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(message.text())
  if (message.type() === 'warning') warnings.push(message.text())
})
page.on('response', (response) => {
  if (response.url().includes('/api/v1/') && response.status() >= 400)
    failures.push({
      path: new URL(response.url()).pathname,
      status: response.status(),
    })
})
page.setDefaultTimeout(12000)
const stamp = Date.now().toString().slice(-8)
const names = {
  project: `UI验收-${stamp}`,
  program: `无副作用程序-${stamp}`,
  task: `浏览器任务-${stamp}`,
  batch: `合成输入-${stamp}`,
  schedule: `每日测试-${stamp}`,
  node: `UI测试节点-${stamp}`,
  caller: `UI测试调用方-${stamp}`,
  user: `ui_${stamp}`,
}
async function check(name, action) {
  await action()
  checks.push(name)
  console.log(`PASS ${name}`)
}
async function screenshot(name) {
  await page.evaluate(() => window.scrollTo(0, 0))
  const masks = [page.locator('[aria-label="一次性显示凭据"]')]
  const sensitiveInputs = page.locator('input[type=password],input[type=url]')
  for (let index = 0; index < (await sensitiveInputs.count()); index++) {
    if (await sensitiveInputs.nth(index).inputValue())
      masks.push(sensitiveInputs.nth(index))
  }
  await page.screenshot({
    path: resolve(output, name),
    fullPage: true,
    animations: 'disabled',
    mask: masks,
  })
}
async function nav(label) {
  await page
    .getByRole('navigation')
    .getByRole('link', { name: label, exact: true })
    .click()
}
async function dialog(title) {
  const target = page.getByRole('dialog', { name: title, exact: true })
  await target.waitFor()
  return target
}
try {
  await check('登录页面无真实运行假数据', async () => {
    await page.goto(settings.url)
    await page.getByRole('heading', { name: '登录工作台' }).waitFor()
    await screenshot('01-login-desktop.png')
  })
  await check('真实账号登录与内存会话', async () => {
    await page.getByLabel('账号', { exact: true }).fill(settings.username)
    await page.getByLabel('密码', { exact: true }).fill(settings.password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page
      .getByRole('link', { name: '自动化工作台 让自动化有序运行' })
      .waitFor()
    assert.equal(
      await page.evaluate(() => localStorage.length + sessionStorage.length),
      0,
    )
  })
  await check('真实创建项目与项目切换', async () => {
    await page.getByRole('button', { name: '＋ 创建项目', exact: true }).click()
    const modal = await dialog('创建项目')
    await modal.getByLabel('项目名称').fill(names.project)
    await modal.getByRole('button', { name: '创建项目', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByLabel('查看范围').selectOption({ label: names.project })
    await page.getByRole('heading', { name: '工作台总览' }).waitFor()
    await page.getByRole('heading', { name: '还没有运行记录' }).waitFor()
    await screenshot('02-overview-desktop.png')
  })
  await check('一个表单完成程序接入、ZIP上传与发布', async () => {
    await nav('程序库')
    await page.getByRole('button', { name: '接入程序', exact: true }).click()
    const modal = await dialog('接入程序')
    await modal.getByLabel('程序名称', { exact: true }).fill(names.program)
    await modal
      .getByLabel('用途说明', { exact: true })
      .fill('合成测试程序，不调用任何外部业务系统。')
    await modal.locator('input[type=file]').setInputFiles(bundle)
    await modal.locator('summary').click()
    await modal.getByLabel('版本名称').fill('1.0.0')
    await screenshot('07-program-import-desktop.png')
    await modal.getByRole('button', { name: '上传并发布', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByText('已发布', { exact: true }).waitFor()
  })
  await check('历史版本可追溯且不再要求分步上传发布', async () => {
    await page.getByRole('button', { name: names.program, exact: true }).click()
    await page.getByRole('cell', { name: '1.0.0', exact: true }).waitFor()
    assert.equal(
      await page.getByRole('button', { name: '发布', exact: true }).count(),
      0,
    )
    await page.getByRole('button', { name: '更新程序', exact: true }).waitFor()
    await screenshot('08-programs-desktop.png')
  })
  await check('任务绑定真实已发布版本', async () => {
    await nav('任务管理')
    await page.getByRole('button', { name: '＋ 创建任务' }).click()
    const modal = await dialog('创建任务')
    await modal.getByLabel('任务名称').fill(names.task)
    await modal.getByText('使用版本：', { exact: false }).waitFor()
    await modal.getByRole('button', { name: '创建任务', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page
      .getByRole('cell')
      .filter({ hasText: names.task })
      .first()
      .waitFor()
  })
  await check('一次多选上传、齐套就绪与真实文件详情', async () => {
    await nav('输入文件')
    await page.getByRole('button', { name: '＋ 提交文件' }).click()
    const modal = await dialog('提交输入文件')
    await modal.getByLabel('文件批次名称', { exact: true }).fill(names.batch)
    await modal.locator('input[type=file]').setInputFiles([
      {
        name: 'synthetic.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from('label,value\nsynthetic,1\n'),
      },
      {
        name: 'details.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from('label,value\nexample,2\n'),
      },
    ])
    await modal.getByRole('button', { name: '提交文件', exact: true }).click()
    const detail = await dialog('文件详情')
    await detail.getByText('synthetic.csv', { exact: true }).waitFor()
    await detail.getByText('details.csv', { exact: true }).waitFor()
    await screenshot('09-input-files-desktop.png')
    await detail.getByRole('button', { name: '完成', exact: true }).click()
    await page.getByRole('button', { name: names.batch, exact: true }).click()
    await page
      .getByRole('dialog')
      .getByText('synthetic.csv', { exact: true })
      .waitFor()
    await page
      .getByRole('dialog')
      .getByRole('button', { name: '完成', exact: true })
      .click()
  })
  await check('事件带参受理及排队取消', async () => {
    await nav('任务管理')
    await page.getByRole('button', { name: '立即运行', exact: true }).click()
    let modal = await dialog(`运行 · ${names.task}`)
    await modal.getByRole('button', { name: '确认运行', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByRole('heading', { name: names.task, exact: true }).waitFor()
    await page.getByText('排队中', { exact: true }).waitFor()
    await page.getByRole('button', { name: '请求停止', exact: true }).click()
    modal = await dialog('请求停止本次运行')
    await modal
      .getByRole('button', { name: '确认请求停止', exact: true })
      .click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByText('已取消', { exact: true }).waitFor()
    await screenshot('03-run-detail-desktop.png')
  })
  await check('定时计划默认关闭、明确启停', async () => {
    await nav('任务管理')
    const taskRow = page
      .locator('tbody tr')
      .filter({ hasText: names.task })
      .first()
    await taskRow.getByRole('button', { name: '定时运行', exact: true }).click()
    const modal = await dialog(`定时运行 · ${names.task}`)
    await modal
      .getByRole('button', { name: '＋ 添加定时计划', exact: true })
      .click()
    await modal.getByLabel('计划名称').fill(names.schedule)
    await modal.getByLabel('定时输入来源').selectOption('label')
    await modal.getByLabel('等待的批次标签').fill('${previous_month}')
    await modal
      .getByRole('button', { name: '保存定时计划', exact: true })
      .click()
    await modal.getByText('未启用', { exact: true }).waitFor()
    await modal.getByRole('button', { name: '启用', exact: true }).click()
    await modal.getByText('已启用', { exact: true }).waitFor()
    await modal.getByRole('button', { name: '暂停', exact: true }).click()
    await modal.getByText('未启用', { exact: true }).waitFor()
    await modal.getByRole('button', { name: '关闭对话框', exact: true }).click()
  })
  await check('电脑连接文件下载、真实导入与维护管理', async () => {
    await nav('执行电脑')
    await page.getByRole('button', { name: '＋ 接入电脑', exact: true }).click()
    const modal = await dialog('接入执行电脑')
    await modal.getByLabel('电脑名称', { exact: true }).fill(names.node)
    await modal
      .getByLabel('执行电脑上的程序路径')
      .fill(resolve('../.venv/Scripts/python.exe'))
    await screenshot('10-node-connect-desktop.png')
    await modal.getByRole('button', { name: '登记并生成连接文件' }).click()
    const downloading = page.waitForEvent('download')
    await modal
      .getByRole('button', { name: '2. 下载连接文件', exact: true })
      .click()
    const downloaded = await downloading
    const profileFile = resolve(output, `connection-${stamp}.local.json`)
    await downloaded.saveAs(profileFile)
    const profile = JSON.parse(await readFile(profileFile, 'utf8'))
    assert.equal(profile.format, 'workbench-connection-v1')
    assert(profile.token.length > 20)
    nodeToken = profile.token
    assert(!(await page.locator('body').innerText()).includes(nodeToken))
    const directory = resolve(output, `connection-${stamp}`)
    importedConfig = resolve(directory, 'agent.json')
    importedToken = resolve(directory, 'node-token.txt')
    execFileSync(
      resolve('../.venv/Scripts/python.exe'),
      [
        '-m',
        'agent',
        '--import-connection',
        profileFile,
        '--connection-dir',
        directory,
      ],
      { cwd: resolve('..'), stdio: 'pipe', timeout: 30000 },
    )
    await unlink(profileFile)
    await modal.getByRole('button', { name: '已保存，关闭' }).click()
    const nodeRow = page
      .locator('tbody tr')
      .filter({ hasText: names.node })
      .first()
    await nodeRow.getByRole('button', { name: '进入维护' }).click()
    await nodeRow.getByText('维护中，不接新任务', { exact: true }).waitFor()
    await nodeRow.getByRole('button', { name: '结束维护' }).click()
    await nodeRow.getByText('允许接单', { exact: true }).waitFor()
  })
  await check('真实子进程成功、日志与鉴权产物下载', async () => {
    const agentConfig = importedConfig
    await nav('任务管理')
    await page.getByRole('button', { name: '立即运行', exact: true }).click()
    const modal = await dialog(`运行 · ${names.task}`)
    await modal.getByRole('button', { name: '确认运行', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByRole('heading', { name: names.task, exact: true }).waitFor()
    execFileSync(
      resolve('../.venv/Scripts/python.exe'),
      ['-m', 'agent', '--config', agentConfig, '--once'],
      { cwd: resolve('..'), stdio: 'pipe', timeout: 30000 },
    )
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await page.getByText('已成功', { exact: true }).waitFor()
    await page
      .getByLabel('执行日志')
      .getByText('浏览器联调程序完成', { exact: false })
      .waitFor()
    await page.getByRole('button', { name: '业务结果', exact: true }).click()
    const downloading = page.waitForEvent('download')
    await page.getByRole('button', { name: '下载', exact: true }).click()
    const artifact = await downloading
    const file = resolve(output, 'downloaded-report.csv')
    await artifact.saveAs(file)
    assert((await readFile(file, 'utf8')).includes('synthetic,1'))
    await screenshot('05-success-artifact-desktop.png')
  })
  await check('调用方限任务授权与凭据生命周期', async () => {
    await nav('事件接入')
    await page.getByRole('button', { name: '＋ 创建调用方' }).click()
    const modal = await dialog('创建外部调用方')
    await modal.getByLabel('调用方名称').fill(names.caller)
    await modal.getByLabel(names.task, { exact: true }).check()
    await modal.getByLabel('允许该调用方登记、上传并冻结本项目输入批次').check()
    await modal.getByRole('button', { name: '创建并生成凭据' }).click()
    await modal.getByText('凭据仅在创建时显示一次', { exact: true }).waitFor()
    await modal.getByRole('button', { name: '已妥善保存，关闭' }).click()
    await page.getByRole('button', { name: '撤销访问', exact: true }).click()
    await page.getByText('未启用', { exact: true }).waitFor()
    await page.getByRole('cell', { name: '已授权', exact: true }).waitFor()
  })
  await check('真实脚本失败告警、人工确认及关闭', async () => {
    const failureTask = `失败验收-${stamp}`
    await nav('任务管理')
    await page.getByRole('button', { name: '＋ 创建任务' }).click()
    let modal = await dialog('创建任务')
    await modal.getByLabel('任务名称').fill(failureTask)
    await modal.locator('summary').click()
    await modal
      .getByLabel('已发布版本')
      .selectOption({ label: `${names.program} / 1.0.0` })
    await modal.getByLabel('操作模式').fill('simulate_failure')
    await modal.getByRole('button', { name: '创建任务', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page
      .getByRole('row')
      .filter({ hasText: failureTask })
      .getByRole('button', { name: '立即运行', exact: true })
      .click()
    modal = await dialog(`运行 · ${failureTask}`)
    await modal.getByRole('button', { name: '确认运行', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page
      .getByRole('heading', { name: failureTask, exact: true })
      .waitFor()
    execFileSync(
      resolve('../.venv/Scripts/python.exe'),
      ['-m', 'agent', '--config', importedConfig, '--once'],
      { cwd: resolve('..'), stdio: 'pipe', timeout: 30000 },
    )
    nodeToken = ''
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    await page.getByText('失败', { exact: true }).waitFor()
    await nav('告警中心')
    await page.getByRole('button', { name: '确认告警', exact: true }).click()
    modal = await dialog('确认告警')
    await modal
      .getByLabel('处理说明')
      .fill('已确认是合成程序主动失败，无外部副作用')
    await modal.getByRole('button', { name: '确认并记录' }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByText('已确认', { exact: true }).waitFor()
    await page.getByRole('button', { name: '关闭告警', exact: true }).click()
    modal = await dialog('关闭告警')
    await modal
      .getByLabel('关闭说明')
      .fill('验收已核对退出证据及日志，关闭本次合成告警')
    await modal.getByRole('button', { name: '关闭并记录' }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByText('已关闭', { exact: true }).waitFor()
    await screenshot('06-alert-closed-desktop.png')
  })
  await check('真实用户创建与项目角色授予', async () => {
    await nav('账号与权限')
    await page.getByRole('button', { name: '创建用户', exact: true }).click()
    let modal = await dialog('创建平台用户')
    await modal.getByLabel('登录账号').fill(names.user)
    await modal.getByLabel('显示姓名').fill('UI验收查看者')
    await modal.getByLabel('初始密码').fill(`${randomUUID()}A9!`)
    await modal.getByRole('button', { name: '创建用户', exact: true }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByRole('button', { name: '＋ 添加项目成员' }).click()
    modal = await dialog('配置项目成员')
    await modal
      .getByLabel('账号', { exact: true })
      .selectOption({ label: `UI验收查看者（${names.user}）` })
    await modal.getByLabel('项目角色').selectOption('viewer')
    await modal.getByRole('button', { name: '保存成员权限' }).click()
    await modal.waitFor({ state: 'hidden' })
    await page.getByText('成员权限已保存', { exact: true }).waitFor()
  })
  await check('审计与告警读取、不创建真实通知连接', async () => {
    await nav('操作审计')
    await page.locator('tbody tr').first().waitFor()
    await nav('告警中心')
    await page.getByRole('heading', { name: '告警中心', exact: true }).waitFor()
    await page
      .getByRole('heading', { name: '钉钉通知连接', exact: true })
      .waitFor()
  })
  await check('小屏页面无横向溢出、导航可达', async () => {
    await page.setViewportSize({ width: 390, height: 844 })
    await page.getByRole('button', { name: '展开导航' }).click()
    await nav('任务管理')
    await page.getByRole('heading', { name: '任务管理', exact: true }).waitFor()
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    )
    const mobileRun = page
      .getByRole('button', { name: '立即运行', exact: true })
      .first()
    const bounds = await mobileRun.boundingBox()
    assert(
      bounds && bounds.x >= 0 && bounds.x + bounds.width <= 390,
      '手机宽度内应能直接操作任务',
    )
    await mobileRun.click()
    await page.getByRole('dialog').waitFor()
    await page.getByRole('button', { name: '关闭对话框', exact: true }).click()
    await screenshot('04-tasks-mobile.png')
    for (const [navigation, heading, screenshotName] of [
      ['定时计划', '定时计划', '11-schedules-mobile.png'],
      ['运行中心', '运行中心', '12-runs-mobile.png'],
    ]) {
      await page.getByRole('button', { name: '展开导航' }).click()
      await nav(navigation)
      await page.getByRole('heading', { name: heading, exact: true }).waitFor()
      await page
        .locator('.loading')
        .first()
        .waitFor({ state: 'hidden' })
        .catch(() => {})
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
        `${navigation}在手机宽度内不应出现页面级横向滚动`,
      )
      await screenshot(screenshotName)
    }
  })
  await check('刷新清除内存身份并返回登录', async () => {
    await page.reload()
    await page.getByRole('heading', { name: '登录工作台' }).waitFor()
    assert.equal(
      await page.evaluate(() => localStorage.length + sessionStorage.length),
      0,
    )
  })
  assert.deepEqual(errors, [], '浏览器控制台应无错误')
  assert.deepEqual(warnings, [], '浏览器控制台应无警告')
  assert.deepEqual(failures, [], '正常业务路径应无API错误')
  await writeFile(
    resolve(output, 'summary.json'),
    JSON.stringify(
      {
        date: new Date().toISOString(),
        browser: browser.version(),
        schema: settings.schema,
        checks,
        passed: checks.length,
        failed: 0,
        console_errors: errors,
        console_warnings: warnings,
        api_errors: failures,
        scope: '真实浏览器 + 本地隔离 PostgreSQL 后端；无真实外部系统调用',
      },
      null,
      2,
    ),
    'utf8',
  )
  console.log(`E2E PASS ${checks.length} checks; no browser or API errors`)
} catch (error) {
  await screenshot('failure.png').catch(() => {})
  await writeFile(
    resolve(output, 'failure-summary.json'),
    JSON.stringify(
      {
        checks,
        error: sanitize(error),
        console_errors: errors,
        api_errors: failures,
      },
      null,
      2,
    ),
    'utf8',
  )
  console.error(sanitize(error))
  process.exitCode = 1
} finally {
  await unlink(nodeTokenFile).catch(() => {})
  if (importedToken) await unlink(importedToken).catch(() => {})
  await context.close()
  await browser.close()
}
