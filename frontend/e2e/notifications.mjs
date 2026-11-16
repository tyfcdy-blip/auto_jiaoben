import assert from 'node:assert/strict'
import { readFile, mkdir } from 'node:fs/promises'
import { execFileSync } from 'node:child_process'
import { resolve } from 'node:path'
import { chromium } from 'playwright'

const sessionFile = resolve(
  process.env.WORKBENCH_E2E_SESSION ||
    '../runtime/integration/session.local.json',
)
const settings = JSON.parse(await readFile(sessionFile, 'utf8'))
assert.equal(new URL(settings.url).hostname, '127.0.0.1')
const root = settings.url.replace(/\/$/, '')
const api = `${root}/api/v1`
const stamp = Date.now().toString(36)
const projectName = `通知验收-${stamp}`
const taskName = `摘要任务-${stamp}`
const channelName = `合成连接-${stamp}`

async function call(path, method = 'GET', body, token) {
  const response = await fetch(`${api}${path}`, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(body instanceof Uint8Array
        ? {}
        : { 'Content-Type': 'application/json' }),
    },
    body:
      body === undefined
        ? undefined
        : body instanceof Uint8Array
          ? body
          : JSON.stringify(body),
  })
  assert(response.ok, `${method} ${path}: ${response.status}`)
  return response.status === 204 ? null : response.json()
}

const login = await call('/auth/login', 'POST', {
  username: settings.username,
  password: settings.password,
})
const token = login.token
const project = await call('/projects', 'POST', { name: projectName }, token)
const program = await call(
  '/programs',
  'POST',
  {
    project_id: project.id,
    name: `合成程序-${stamp}`,
  },
  token,
)
const version = await call(
  `/programs/${program.id}/versions`,
  'POST',
  {
    name: '1.0.0',
    command: ['python', 'main.py'],
    parameter_schema: {
      type: 'object',
      properties: {},
      additionalProperties: false,
    },
    result_required: true,
  },
  token,
)
await mkdir(resolve('e2e-results'), { recursive: true })
const bundle = resolve('e2e-results/notification-program.zip')
execFileSync(resolve('../.venv/Scripts/python.exe'), [
  '-c',
  'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w"); z.write(sys.argv[2],"main.py"); z.close()',
  bundle,
  resolve('e2e/fixtures/main.py'),
])
await call(
  `/versions/${version.id}/bundle`,
  'POST',
  new Uint8Array(await readFile(bundle)),
  token,
)
await call(`/versions/${version.id}/publish`, 'POST', undefined, token)
const task = await call(
  '/tasks',
  'POST',
  {
    project_id: project.id,
    name: taskName,
    version_id: version.id,
  },
  token,
)

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined,
})
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  })
  page.setDefaultTimeout(12000)
  await page.goto(root)
  await page.getByLabel('账号', { exact: true }).fill(settings.username)
  await page.getByLabel('密码', { exact: true }).fill(settings.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await page.getByLabel('查看范围').selectOption({ label: projectName })
  await page
    .getByRole('navigation')
    .getByRole('link', { name: '机器人连接' })
    .click()
  assert.equal(
    await page.getByRole('heading', { name: '任务成功消息' }).count(),
    0,
  )
  await page.getByRole('button', { name: '新增连接' }).click()
  let dialog = page.getByRole('dialog', { name: '新增钉钉通知连接' })
  await dialog.getByLabel('连接名称').fill(channelName)
  await dialog
    .getByLabel('机器人 Webhook')
    .fill('https://oapi.dingtalk.com/robot/send?access_token=SYNTHETIC-ONLY')
  await dialog.getByRole('button', { name: '保存连接' }).click()
  await dialog.waitFor({ state: 'hidden' })
  await page.getByText(channelName, { exact: true }).waitFor()
  const channels = await call(
    `/channels?project_id=${project.id}`,
    'GET',
    undefined,
    token,
  )
  const channel = channels.items.find((item) => item.name === channelName)
  assert(channel && !channel.enabled, '测试连接必须保持停用')
  await page.getByRole('button', { name: '发送测试消息' }).click()
  await page.getByText('钉钉接口已接收测试消息，请到对应群核对。').waitFor()
  await page.getByText('测试编号：', { exact: false }).waitFor()
  const audits = await call(
    `/audit?project_id=${project.id}`,
    'GET',
    undefined,
    token,
  )
  assert(
    audits.items.some(
      (item) =>
        item.action === 'test_channel_result' &&
        item.resource_id === channel.id &&
        item.note.includes('reason=accepted'),
    ),
  )
  await page
    .getByRole('navigation')
    .getByRole('link', { name: '通知规则' })
    .click()
  await page.getByRole('heading', { name: '任务成功消息' }).waitFor()
  await page.getByRole('button', { name: '选择异常类型' }).click()
  dialog = page.getByRole('dialog', { name: `告警通知规则 · ${channelName}` })
  await dialog.locator('input[value="schedule_delayed"]').check()
  await dialog.getByRole('button', { name: '保存规则' }).click()
  await dialog.waitFor({ state: 'hidden' })
  const policy = await call(
    `/channels/${channel.id}/policy`,
    'GET',
    undefined,
    token,
  )
  assert(policy.enabled_kinds.includes('schedule_delayed'))

  await page.getByRole('button', { name: '配置接收群' }).click()
  dialog = page.getByRole('dialog', { name: `任务成功消息 · ${taskName}` })
  await dialog.getByRole('checkbox', { name: '成功后发送脚本消息' }).check()
  await dialog.getByLabel('接收连接').selectOption(channel.id)
  await dialog.getByRole('button', { name: '保存通知设置' }).click()
  await dialog.waitFor({ state: 'hidden' })
  const resultConfig = await call(
    `/tasks/${task.id}/result-notification`,
    'GET',
    undefined,
    token,
  )
  assert.equal(resultConfig.channel_id, channel.id)
  assert.equal(resultConfig.enabled, true)
  assert.equal(
    (await call(`/channels?project_id=${project.id}`, 'GET', undefined, token))
      .items[0].enabled,
    false,
  )
  await page
    .getByRole('navigation')
    .getByRole('link', { name: '消息记录' })
    .click()
  await page.getByRole('heading', { name: '消息记录' }).waitFor()
  await page
    .getByRole('navigation')
    .getByRole('link', { name: '告警中心' })
    .click()
  await page.getByRole('heading', { name: '告警中心' }).waitFor()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '展开导航' }).click()
  await page
    .getByRole('navigation')
    .getByRole('link', { name: '通知规则' })
    .click()
  await page.getByRole('heading', { name: '通知规则' }).waitFor()
  const pageWidth = await page.evaluate(
    () => document.documentElement.scrollWidth,
  )
  assert(pageWidth <= 390, '窄屏页面不应出现横向滚动')
  console.log(
    'PASS 机器人连接测试消息、通知规则、消息记录和告警中心分区；连接保持停用、网络发送已模拟',
  )
} finally {
  await browser.close()
}
