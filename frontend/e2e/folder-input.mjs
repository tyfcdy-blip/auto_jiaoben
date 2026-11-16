import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import { resolve, dirname, relative, isAbsolute } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'
import { chromium } from 'playwright'

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const root = resolve(frontend, '..')
const settings = JSON.parse(
  await readFile(
    resolve(
      process.env.WORKBENCH_E2E_SESSION ||
        resolve(root, 'runtime/folder-input-session.local.json'),
    ),
    'utf8',
  ),
)
const acceptancePath = resolve(
  process.env.WORKBENCH_E2E_ACCEPTANCE ||
    resolve(root, 'runtime/folder-acceptance/native-02/acceptance.json'),
)
const accepted = JSON.parse(await readFile(acceptancePath, 'utf8'))
const base = settings.url.replace(/\/$/, '')
assert.equal(new URL(base).hostname, '127.0.0.1', '仅允许本机隔离实例')
assert(
  settings.schema?.startsWith('integration_') ||
    settings.schema === 'docker-isolated-workbench-dev',
  '需要明确的隔离测试实例',
)
assert.equal(
  accepted.url.replace(/\/$/, ''),
  base,
  '验收项目与登录实例必须一致',
)
assert(
  accepted.project_name.startsWith('文件夹输入验收-') &&
    accepted.failed === 0 &&
    accepted.tasks['csv-merge'],
  '需要已通过的合成目录项目',
)
const inputRelative = relative(
  resolve(root, 'runtime/folder-acceptance'),
  resolve(accepted.input_dir),
)
assert(
  inputRelative &&
    !inputRelative.startsWith('..') &&
    !isAbsolute(inputRelative),
  '只使用工作区合成目录',
)

const output = resolve(
  frontend,
  'e2e-folder-results',
  new Date().toISOString().replace(/[:.]/g, '-') +
    '-' +
    randomUUID().slice(0, 8),
)
await mkdir(output, { recursive: true })
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
page.setDefaultTimeout(12000)
const checks = [],
  errors = [],
  warnings = [],
  apiErrors = [],
  requests = []
const stamp = randomUUID().slice(0, 8)
let token = '',
  runId = '',
  scheduleId = '',
  task
let failure = null
const safe = (message) => {
  let value = String(message)
  for (const secret of [
    settings.password,
    settings.username,
    settings.database_url,
    token,
  ]) {
    if (secret) value = value.replaceAll(secret, '[REDACTED]')
  }
  return value
}
page.on('pageerror', (error) => errors.push(safe(error.message)))
page.on('console', (message) => {
  if (message.type() === 'error') errors.push(safe(message.text()))
  if (message.type() === 'warning') warnings.push(safe(message.text()))
})
page.on('request', (request) => {
  const url = new URL(request.url())
  if (url.pathname.includes('/api/v1/') && request.method() !== 'GET')
    requests.push({ path: url.pathname, method: request.method() })
})
page.on('response', (response) => {
  if (response.url().includes('/api/v1/') && response.status() >= 400)
    apiErrors.push({
      path: new URL(response.url()).pathname,
      status: response.status(),
    })
})
await context.route('**/*', (route) =>
  new URL(route.request().url()).origin === new URL(base).origin
    ? route.continue()
    : route.abort(),
)

async function api(path, options = {}) {
  const response = await context.request.fetch(`${base}/api/v1${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}` },
    timeout: 12000,
  })
  assert(
    response.ok(),
    `隔离验收接口失败：${response.status()} ${path.split('?')[0]}`,
  )
  return response.json()
}
async function nav(label) {
  await page
    .getByRole('navigation')
    .getByRole('link', { name: label, exact: true })
    .click()
}
async function screenshot(name) {
  await page.screenshot({
    path: resolve(output, name),
    fullPage: true,
    animations: 'disabled',
    mask: [
      page.locator('input[type=password]'),
      page.getByLabel('账号', { exact: true }),
      page.locator('[aria-label="一次性显示凭据"]'),
      page.getByText(settings.username, { exact: true }),
    ],
  })
}
async function check(name, operation) {
  await operation()
  checks.push(name)
  console.log(`PASS ${name}`)
}
async function closeDialog() {
  const modal = page.getByRole('dialog')
  await modal.getByRole('button', { name: '关闭对话框', exact: true }).click()
  await modal.waitFor({ state: 'hidden' })
}

try {
  await check('真实登录并选择已通过原生验收的合成目录项目', async () => {
    await page.goto(base)
    await page.getByRole('heading', { name: '登录工作台' }).waitFor()
    await page.getByLabel('账号', { exact: true }).fill(settings.username)
    await page.getByLabel('密码', { exact: true }).fill(settings.password)
    const signedIn = page.waitForResponse(
      (response) =>
        response.url().endsWith('/api/v1/auth/login') &&
        response.request().method() === 'POST',
    )
    await page.getByRole('button', { name: '登录', exact: true }).click()
    const response = await signedIn
    assert.equal(response.status(), 200)
    token = (await response.json()).token
    await page.getByLabel('当前项目').selectOption(accepted.project_id)
    task = await api(`/tasks/${accepted.tasks['csv-merge']}`)
    assert.equal(task.project_id, accepted.project_id)
    assert.equal(task.input_mode, 'directory')
    assert(task.node_id)
    assert.equal(
      await page.evaluate(() => localStorage.length + sessionStorage.length),
      0,
    )
  })
  await check(
    '目录任务列表显示电脑文件夹标签，普通运行表单无需上传',
    async () => {
      await nav('任务管理')
      const row = page.getByRole('row').filter({ hasText: task.name })
      await row.getByText('读取执行电脑上的文件夹', { exact: true }).waitFor()
      await screenshot('01-directory-tasks.png')
      await row.getByRole('button', { name: '立即运行', exact: true }).click()
      const modal = page.getByRole('dialog', { name: `运行 · ${task.name}` })
      await modal.waitFor()
      assert.equal(
        await modal.getByLabel('输入文件夹', { exact: true }).inputValue(),
        task.default_parameters.input_dir,
      )
      assert.equal(await modal.locator('input[type=file],select').count(), 0)
      await modal
        .getByLabel('输入文件夹', { exact: true })
        .fill(accepted.input_dir)
      await modal
        .getByLabel('目录批次编号', { exact: true })
        .fill(`ui-${stamp}`)
      await screenshot('02-directory-manual-form.png')
    },
  )
  await check(
    '真实UI带目录和新批次受理，参数保留且没有input_batch_id',
    async () => {
      const requestPromise = page.waitForRequest(
        (request) =>
          request.url().endsWith(`/tasks/${task.id}/runs`) &&
          request.method() === 'POST',
      )
      const responsePromise = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/tasks/${task.id}/runs`) &&
          response.request().method() === 'POST',
      )
      await page
        .getByRole('dialog')
        .getByRole('button', { name: '确认运行', exact: true })
        .click()
      const request = await requestPromise,
        response = await responsePromise
      const sent = request.postDataJSON()
      assert.equal(response.status(), 202)
      assert(!Object.hasOwn(sent, 'input_batch_id'))
      assert.equal(sent.parameters.input_dir, accepted.input_dir)
      assert.equal(sent.parameters.batch_key, `ui-${stamp}`)
      const {
        input_dir: _directory,
        batch_key: _batch,
        ...business
      } = task.default_parameters
      const {
        input_dir: _submittedDirectory,
        batch_key: _submittedBatch,
        ...submittedBusiness
      } = sent.parameters
      assert.deepEqual(submittedBusiness, business)
      runId = (await response.json()).run_id
      await page.waitForURL(`**/runs/${runId}`)
      const run = await api(`/runs/${runId}`)
      assert.equal(run.parameters.batch_key, `ui-${stamp}`)
      assert.equal(run.input_batch_id, null)
    },
  )
  await check('UI取消本次未执行队列，避免留下额外待运行任务', async () => {
    await page.getByRole('button', { name: '请求停止', exact: true }).click()
    const modal = page.getByRole('dialog', { name: '请求停止本次运行' })
    await modal
      .getByRole('button', { name: '确认请求停止', exact: true })
      .click()
    await modal.waitFor({ state: 'hidden' })
    assert.equal((await api(`/runs/${runId}`)).state, 'canceled')
    await screenshot('03-directory-manual-canceled.png')
  })
  await check(
    '目录定时计划保存完整批次日期模板，保持关闭且无平台批次来源',
    async () => {
      await nav('定时计划')
      await page
        .getByRole('button', { name: '＋ 新建计划', exact: true })
        .click()
      const modal = page.getByRole('dialog', { name: '新建定时计划' })
      await modal
        .getByLabel('计划名称', { exact: true })
        .fill(`UI目录计划-${stamp}`)
      await modal.getByLabel('执行任务').selectOption(task.id)
      await modal
        .getByLabel('输入文件夹', { exact: true })
        .fill(accepted.input_dir)
      await modal
        .getByLabel('目录批次编号', { exact: true })
        .fill('${scheduled_date}')
      assert.equal(
        await modal.getByLabel('输入来源', { exact: true }).count(),
        0,
      )
      await modal.getByText('高级设置 · 其他业务参数', { exact: true }).click()
      const {
        input_dir: _directory,
        batch_key: _batch,
        ...business
      } = task.default_parameters
      await modal.locator('textarea').fill(JSON.stringify(business))
      await screenshot('04-directory-schedule-form.png')
      const savedPromise = page.waitForResponse(
        (response) =>
          response.url().endsWith('/schedules') &&
          response.request().method() === 'POST',
      )
      await modal
        .getByRole('button', { name: '保存为关闭状态', exact: true })
        .click()
      const savedResponse = await savedPromise
      assert.equal(savedResponse.status(), 201)
      const sent = savedResponse.request().postDataJSON(),
        saved = await savedResponse.json()
      scheduleId = saved.id
      assert.equal(saved.enabled, false)
      assert(
        !Object.hasOwn(sent, 'input_batch_id') &&
          !Object.hasOwn(sent, 'input_label'),
      )
      assert.deepEqual(saved.parameters, {
        ...business,
        input_dir: accepted.input_dir,
        batch_key: '${scheduled_date}',
      })
      await modal.waitFor({ state: 'hidden' })
      const row = page
        .getByRole('row')
        .filter({ hasText: `UI目录计划-${stamp}` })
      await row.getByText('执行电脑上的文件夹', { exact: true }).waitFor()
      await row.getByRole('button', { name: '启用', exact: true }).waitFor()
      await screenshot('05-directory-schedule-disabled.png')
    },
  )
  await check(
    '程序导入普通目录模式可切换且保留业务Schema，未额外登记程序',
    async () => {
      await nav('程序库')
      await page.getByRole('button', { name: '接入程序', exact: true }).click()
      const modal = page.getByRole('dialog', { name: '接入程序', exact: true })
      const before = requests.length
      await modal
        .getByLabel('程序输入方式', { exact: true })
        .selectOption('directory')
      await modal.getByText('高级设置 · 版本与参数', { exact: true }).click()
      const schemaField = modal.getByLabel('业务参数定义（JSON Schema）', {
        exact: true,
      })
      const sample = JSON.stringify({
        type: 'object',
        properties: { period: { type: 'string' } },
        required: ['period'],
        additionalProperties: false,
      })
      await schemaField.fill(sample)
      await modal
        .getByLabel('程序输入方式', { exact: true })
        .selectOption('parameters')
      assert.equal(await schemaField.inputValue(), sample)
      await modal
        .getByLabel('程序输入方式', { exact: true })
        .selectOption('directory')
      await screenshot('06-directory-program-form.png')
      await closeDialog()
      assert.equal(requests.length, before)
    },
  )
  await check(
    '电脑接入提供可选根目录普通控件，表单检查不签发或下载凭据',
    async () => {
      await nav('执行电脑')
      await page
        .getByRole('button', { name: '＋ 接入电脑', exact: true })
        .click()
      const modal = page.getByRole('dialog')
      const before = requests.length
      await modal
        .getByLabel('允许读取的文件夹根目录', { exact: true })
        .fill(accepted.input_dir)
      assert.equal(
        await modal
          .getByLabel('允许读取的文件夹根目录', { exact: true })
          .inputValue(),
        accepted.input_dir,
      )
      await modal
        .getByText('实际权限和路径由执行电脑核验；不要填写共享账号或密码。', {
          exact: false,
        })
        .waitFor()
      await screenshot('07-directory-node-roots-form.png')
      await closeDialog()
      assert.equal(requests.length, before)
    },
  )
  assert.deepEqual(errors, [], '浏览器不得有错误')
  assert.deepEqual(warnings, [], '浏览器不得有警告')
  assert.deepEqual(apiErrors, [], '正常流程不得有接口错误')
} catch (error) {
  failure = safe(error)
  await screenshot('failure.png').catch(() => {})
  console.error(failure)
  process.exitCode = 1
} finally {
  // 清理仅限本脚本新建的合成运行与计划，不启动执行器或修改上游验收任务。
  if (token) {
    try {
      if (runId) {
        const run = await api(`/runs/${runId}`)
        if (
          [
            'waiting_input',
            'queued',
            'leased',
            'running',
            'cancel_requested',
          ].includes(run.state)
        )
          await api(`/runs/${runId}/cancel`, { method: 'POST' })
      }
      if (scheduleId)
        await api(`/schedules/${scheduleId}`, {
          method: 'PATCH',
          data: { enabled: false },
        })
    } catch (error) {
      failure = failure || safe(error)
      process.exitCode = 1
    }
  }
  const summary = {
    date: new Date().toISOString(),
    browser: browser.version(),
    source_acceptance: relative(root, acceptancePath),
    project_id: accepted.project_id,
    checks,
    passed: checks.length,
    failed: failure ? 1 : 0,
    error: failure,
    console_errors: errors,
    console_warnings: warnings,
    api_errors: apiErrors,
    created_run_id: runId || null,
    created_schedule_id: scheduleId || null,
    scope:
      '真实本机浏览器与隔离后端；手动运行只验证受理后取消，目录执行结果见原生验收；计划保持关闭；程序与根目录只检查表单，不签发凭据',
  }
  await writeFile(
    resolve(output, 'summary.json'),
    JSON.stringify(summary, null, 2),
    'utf8',
  )
  await context.close()
  await browser.close()
  console.log(
    `FOLDER E2E ${failure ? 'FAIL' : 'PASS'} ${checks.length} checks; evidence=${relative(frontend, output)}`,
  )
}
