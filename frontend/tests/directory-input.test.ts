import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ProgramImport from '../src/components/ProgramImport.vue'
import TasksView from '../src/views/TasksView.vue'
import NodeConnectionForm from '../src/components/NodeConnectionForm.vue'
import { clearSession, session } from '../src/session'
import {
  DIRECTORY_CAPABILITY,
  directoryParameters,
  directoryPath,
  inputSchema,
} from '../src/directoryInput'
import * as connections from '../src/nodeConnection'
import type { Task, Version } from '../src/types'

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 })
const schema = inputSchema(
  {
    type: 'object',
    properties: { period: { type: 'string' } },
    required: ['period'],
    additionalProperties: false,
  },
  true,
)
const directoryVersion = {
  id: 'v-directory',
  program_id: 'program',
  name: '文件夹版',
  command: ['python', 'main.py'],
  parameter_schema: schema,
  published: true,
  bundle_sha256: 'hash',
  required_capabilities: [DIRECTORY_CAPABILITY],
  side_effects: false,
  result_required: true,
} satisfies Version
const task = {
  id: 'directory-task',
  project_id: 'p',
  version_id: 'v-directory',
  resource_keys: [],
  name: '目录任务',
  input_mode: 'directory',
  node_id: 'node',
  default_parameters: {
    input_dir: 'D:\\账单',
    batch_key: 'first',
    period: '2026-09',
  },
  enabled: true,
  config_hash: 'a'.repeat(64),
  requires_input: false,
  timeout_seconds: 300,
  operation: 'run',
} satisfies Task
function login() {
  session.user = {
    id: 'u',
    username: 'admin',
    display_name: '管理员',
    is_admin: true,
    active: true,
  }
  session.projectId = 'p'
  session.projects = [{ id: 'p', name: '项目', role: 'project_admin' }]
}
function set(selector: string, value: string) {
  const field = document.querySelector<
    HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
  >(selector)!
  expect(field, selector).toBeTruthy()
  field.value = value
  field.dispatchEvent(
    new Event(field instanceof HTMLSelectElement ? 'change' : 'input', {
      bubbles: true,
    }),
  )
}
function click(text: string) {
  const button = Array.from(document.querySelectorAll('button')).find(
    (element) => element.textContent?.trim() === text,
  )!
  expect(button, text).toBeTruthy()
  button.click()
}
async function submit() {
  document
    .querySelector('form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flushPromises()
}
async function mountTasks(entries: Task[] = []) {
  login()
  const fetcher = vi.fn((url: string, options: RequestInit) => {
    if (options.method === 'POST')
      return Promise.resolve(
        json(
          url.endsWith('/runs') ? { run_id: 'run' } : { id: 'created-task' },
        ),
      )
    const items = url.includes('/tasks?')
      ? entries
      : url.includes('/programs?')
        ? [{ id: 'program', name: '程序' }]
        : url.includes('/versions')
          ? [
              directoryVersion,
              {
                ...directoryVersion,
                id: 'v-normal',
                name: '常规版',
                parameter_schema: {},
              },
            ]
          : url.includes('/nodes?')
            ? [
                {
                  id: 'node',
                  name: '执行电脑',
                  capabilities: [DIRECTORY_CAPABILITY],
                },
                {
                  id: 'ordinary-node',
                  name: '普通电脑',
                  capabilities: ['python'],
                },
              ]
            : []
    return Promise.resolve(json({ items, total: items.length }))
  })
  vi.stubGlobal('fetch', fetcher)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: TasksView },
      { path: '/runs/:id', component: { template: '<p>结果</p>' } },
    ],
  })
  await router.push('/')
  const wrapper = mount(TasksView, {
    attachTo: document.body,
    global: { plugins: [router] },
  })
  await flushPromises()
  return { fetcher, wrapper, router }
}
afterEach(() => {
  clearSession()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('目录输入路径与参数契约', () => {
  it.each([
    'D:\\账单',
    'C:/data/reports',
    '/srv/billing',
    '\\\\server\\share\\billing',
    '//server/share/billing',
    'C:/Data Reports/month',
  ])('保留执行电脑绝对路径 %s', (path) =>
    expect(directoryPath(path)).toBe(path),
  )
  it.each([
    '',
    'relative/path',
    'C:relative',
    '/safe/../escape',
    'D:\\data\\${scheduled_date}',
    '\\\\?\\C:\\data',
    '//?/C:/data',
    'https://host/data',
    '/bad\npath',
    '/data/./month',
    'D:\\data\\.\\month',
    '/data:stream',
    'D:/data/file:stream',
    '//single',
    '\\\\single',
    '//./pipe/name',
    '/posix\\mixed',
    '/data\x7fmonth',
    '/data<month',
    '/data>month',
    '/data"month',
    '/data|month',
    '/data?month',
    '/data*month',
    ' /data',
    '/data ',
    '/' + 'a'.repeat(2000),
  ])('坏目录 %s 不能形成可提交参数', (path) =>
    expect(() => directoryParameters({}, path, 'batch')).toThrow(),
  )
  it('路径长度上限与后端一致，语法检查不代替执行电脑权限检查', () => {
    const path = '/' + 'a'.repeat(1999)
    expect(directoryPath(path)).toBe(path)
  })
  it('目录参数保留其他业务字段，计划只接受完整批次日期表达式', () => {
    expect(
      directoryParameters(
        { period: '2026-09', keep: 2 },
        '/data',
        '${scheduled_date}',
        true,
      ),
    ).toEqual({
      period: '2026-09',
      keep: 2,
      input_dir: '/data',
      batch_key: '${scheduled_date}',
    })
    expect(() =>
      directoryParameters({}, '/data', 'prefix-${scheduled_date}', true),
    ).toThrow('不能拼接')
    expect(() => directoryParameters({}, '/data', '${scheduled_date}')).toThrow(
      '定时计划',
    )
    expect(() => directoryParameters({}, '/data', ' '.repeat(2))).toThrow(
      '批次编号',
    )
  })
  it('目录schema注入不修改原对象，取消模式保留原业务字段与required', () => {
    const source = {
      type: 'object',
      properties: { input_dir: { type: 'number' }, amount: { type: 'number' } },
      required: ['input_dir', 'amount'],
    }
    const snapshot = structuredClone(source)
    const enabled = inputSchema(source, true)
    expect(enabled).toMatchObject({
      'x-workbench-input': 'directory',
      additionalProperties: false,
      properties: {
        input_dir: { type: 'string', minLength: 1, maxLength: 2000 },
        batch_key: { type: 'string', minLength: 1, maxLength: 180 },
      },
    })
    expect(enabled.required).toEqual(['input_dir', 'amount', 'batch_key'])
    expect(source).toEqual(snapshot)
    expect(inputSchema(source, false)).toEqual(snapshot)
  })
})

describe('程序、任务和运行目录表单', () => {
  it('目录模式仅显示有目录能力的电脑，切回常规保留全部选择', async () => {
    await mountTasks()
    click('＋ 创建任务')
    await flushPromises()
    const nodes = document.querySelector<HTMLSelectElement>(
      '[aria-label="执行电脑"]',
    )!
    expect(Array.from(nodes.options).map((option) => option.value)).toEqual([
      '',
      'node',
    ])
    expect(nodes.required).toBe(true)
    const version = document.querySelector('option[value="v-normal"]')!
      .parentElement as HTMLSelectElement
    version.value = 'v-normal'
    version.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()
    expect(Array.from(nodes.options).map((option) => option.value)).toEqual([
      '',
      'node',
      'ordinary-node',
    ])
    nodes.value = 'ordinary-node'
    nodes.dispatchEvent(new Event('change', { bubbles: true }))
    version.value = 'v-directory'
    version.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()
    expect(nodes.value).toBe('')
    expect(nodes.required).toBe(true)
  })
  it.each([true, false])(
    '程序选择目录后保存模式 %s，不丢业务定义',
    async (enabled) => {
      login()
      const fetcher = vi.fn((url: string, options: RequestInit) =>
        Promise.resolve(
          json(
            url.includes('/program-bundles/scan?')
              ? {
                  decision: 'pass',
                  coverage: 'full',
                  summary: { errors: 0, high: 0, warnings: 0 },
                  findings: [],
                }
              : url.endsWith('/versions')
                ? { ...directoryVersion, published: false }
                : url.includes('/bundle?')
                  ? directoryVersion
                  : { id: 'program', project_id: 'p', name: '程序' },
          ),
        ),
      )
      vi.stubGlobal('fetch', fetcher)
      mount(ProgramImport, {
        props: {
          program: {
            id: 'program',
            project_id: 'p',
            name: '程序',
            description: '',
          },
        },
        attachTo: document.body,
      })
      set('[aria-label="程序输入方式"]', 'directory')
      await flushPromises()
      set(
        'textarea.mono',
        JSON.stringify({
          type: 'object',
          properties: {
            amount: { type: 'number' },
            input_dir: { type: 'number' },
          },
          required: ['amount', 'input_dir'],
          additionalProperties: false,
        }),
      )
      if (!enabled) {
        set('[aria-label="程序输入方式"]', 'parameters')
        await flushPromises()
      }
      const file = document.querySelector<HTMLInputElement>('input[type=file]')!
      Object.defineProperty(file, 'files', {
        value: [new File(['zip'], 'test.zip')],
      })
      file.dispatchEvent(new Event('change', { bubbles: true }))
      await submit()
      const body = JSON.parse(
        String(
          fetcher.mock.calls.find(([url]) => url.endsWith('/versions'))![1]
            .body,
        ),
      )
      expect(body.parameter_schema.properties.amount).toEqual({
        type: 'number',
      })
      expect(body.parameter_schema['x-workbench-input']).toBe(
        enabled ? 'directory' : undefined,
      )
      expect(body.parameter_schema.properties.input_dir.type).toBe(
        enabled ? 'string' : 'number',
      )
      expect(body.required_capabilities.includes(DIRECTORY_CAPABILITY)).toBe(
        enabled,
      )
      expect(body.required_capabilities).toContain('python')
    },
  )
  it('已有目录草稿读取标记，模式随已保存版本锁定', async () => {
    login()
    mount(ProgramImport, {
      props: {
        program: {
          id: 'program',
          project_id: 'p',
          name: '程序',
          description: '',
        },
        draft: { ...directoryVersion, published: false },
      },
      attachTo: document.body,
    })
    const mode = document.querySelector<HTMLSelectElement>(
      '[aria-label="程序输入方式"]',
    )!
    expect(mode.value).toBe('directory')
    expect(mode.closest('fieldset')!.disabled).toBe(true)
  })
  it('目录任务必须选定电脑，合并普通目录字段、自定义参数和批次去重', async () => {
    const { fetcher } = await mountTasks()
    click('＋ 创建任务')
    await flushPromises()
    set('form input', '本机目录任务')
    set('[aria-label="输入文件夹"]', 'D:\\账单')
    set('[aria-label="目录批次编号"]', 'new-batch')
    set('form textarea', '{"period":"2026-09","custom":4}')
    await submit()
    expect(
      fetcher.mock.calls.filter(([, options]) => options.method === 'POST'),
    ).toHaveLength(0)
    expect(document.body.textContent).toContain('固定执行电脑')
    set('[aria-label="执行电脑"]', 'node')
    await submit()
    const body = JSON.parse(
      String(
        fetcher.mock.calls.find(
          ([url, options]) =>
            url.endsWith('/tasks') && options.method === 'POST',
        )![1].body,
      ),
    )
    expect(body).toMatchObject({
      node_id: 'node',
      requires_input: false,
      business_key_fields: ['batch_key'],
      default_parameters: {
        input_dir: 'D:\\账单',
        batch_key: 'new-batch',
        period: '2026-09',
        custom: 4,
      },
    })
    expect(body).not.toHaveProperty('input_batch_id')
  })
  it('切回常规版本不强制目录电脑、不注入目录参数且保留自定义参数', async () => {
    const { fetcher } = await mountTasks()
    click('＋ 创建任务')
    await flushPromises()
    set('form input', '常规任务')
    set('form textarea', '{"custom":"keep"}')
    const version = document.querySelector('option[value="v-normal"]')!
      .parentElement as HTMLSelectElement
    version.value = 'v-normal'
    version.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()
    expect(document.querySelector('[aria-label="输入文件夹"]')).toBeNull()
    await submit()
    const body = JSON.parse(
      String(
        fetcher.mock.calls.find(([, options]) => options.method === 'POST')![1]
          .body,
      ),
    )
    expect(body.default_parameters).toEqual({ custom: 'keep' })
    expect(body.node_id).toBeNull()
    expect(body.business_key_fields).toEqual([])
  })
  it('目录运行用普通字段合并参数，不读或发送工作台文件批次', async () => {
    const { fetcher, router } = await mountTasks([task])
    click('立即运行')
    await flushPromises()
    expect(
      (document.querySelector('[aria-label="输入文件夹"]') as HTMLInputElement)
        .value,
    ).toBe('D:\\账单')
    expect(document.querySelector('form select')).toBeNull()
    set('[aria-label="输入文件夹"]', 'relative')
    await submit()
    expect(
      fetcher.mock.calls.filter(([, options]) => options.method === 'POST'),
    ).toHaveLength(0)
    set('[aria-label="输入文件夹"]', '/data/billing')
    set('[aria-label="目录批次编号"]', 'manual-2')
    set('form textarea', '{"period":"2026-08","custom":5}')
    await submit()
    const body = JSON.parse(
      String(
        fetcher.mock.calls.find(
          ([url, options]) =>
            url.endsWith('/runs') && options.method === 'POST',
        )![1].body,
      ),
    )
    expect(body.parameters).toEqual({
      period: '2026-08',
      custom: 5,
      input_dir: '/data/billing',
      batch_key: 'manual-2',
    })
    expect(body).not.toHaveProperty('input_batch_id')
    expect(fetcher.mock.calls.some(([url]) => url.includes('/batches'))).toBe(
      false,
    )
    expect(router.currentRoute.value.path).toBe('/runs/run')
  })
  it('旧任务不因同名业务字段被误识别为目录任务', async () => {
    const legacy = { ...task, input_mode: undefined, node_id: null }
    const { fetcher } = await mountTasks([legacy])
    click('立即运行')
    await flushPromises()
    expect(document.querySelector('[aria-label="输入文件夹"]')).toBeNull()
    expect(document.querySelector('form select')).not.toBeNull()
    await submit()
    const body = JSON.parse(
      String(
        fetcher.mock.calls.find(
          ([url, options]) =>
            url.endsWith('/runs') && options.method === 'POST',
        )![1].body,
      ),
    )
    expect(body.parameters).toEqual(legacy.default_parameters)
    expect(body.input_batch_id).toBeNull()
  })
})

describe('目录定时计划与电脑读取范围', () => {
  it('目录任务缺少绑定电脑时计划不能提交', async () => {
    const { fetcher } = await mountTasks([{ ...task, node_id: null }])
    click('定时运行')
    await flushPromises()
    click('＋ 添加定时计划')
    await flushPromises()
    set('form input', '不能受理')
    await submit()
    expect(
      fetcher.mock.calls.some(([, options]) => options.method === 'POST'),
    ).toBe(false)
    expect(document.body.textContent).toContain('先绑定一台执行电脑')
  })
  it('非法根目录在节点登记前拦截，不下载连接文件', async () => {
    login()
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    mount(NodeConnectionForm, {
      props: { projectId: 'p' },
      attachTo: document.body,
    })
    set('input', '测试电脑')
    const fields = document.querySelectorAll<HTMLInputElement>('input')
    fields[1]!.value = 'C:\\Python\\python.exe'
    fields[1]!.dispatchEvent(new Event('input', { bubbles: true }))
    set('[aria-label="允许读取的文件夹根目录"]', '../outside')
    await submit()
    expect(fetcher).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('完整文件夹路径')
  })
  it('目录计划隐藏批次来源，保留自定义参数，使用完整批次日期模板', async () => {
    const { fetcher } = await mountTasks([task])
    click('定时运行')
    await flushPromises()
    click('＋ 添加定时计划')
    await flushPromises()
    expect(
      document.querySelector<HTMLInputElement>('[aria-label="目录批次编号"]')
        ?.value,
    ).toBe('${scheduled_date}')
    set('form input', '每日目录')
    set('form textarea', '{"period":"${previous_month}","keep":8}')
    expect(document.querySelector('[aria-label="定时输入来源"]')).toBeNull()
    set('[aria-label="目录批次编号"]', 'prefix-${scheduled_date}')
    await submit()
    expect(
      fetcher.mock.calls.filter(([, options]) => options.method === 'POST'),
    ).toHaveLength(0)
    set('[aria-label="目录批次编号"]', '${scheduled_date}')
    await submit()
    const body = JSON.parse(
      String(
        fetcher.mock.calls.find(([, options]) => options.method === 'POST')![1]
          .body,
      ),
    )
    expect(body.parameters).toEqual({
      period: '${previous_month}',
      keep: 8,
      input_dir: 'D:\\账单',
      batch_key: '${scheduled_date}',
    })
    expect(body).not.toHaveProperty('input_batch_id')
    expect(body).not.toHaveProperty('input_label')
    expect(body.enabled).toBe(false)
  })
  it('电脑根目录同时登记到工作台并写入连接文件', async () => {
    login()
    const fetcher = vi.fn((_url: string, _options: RequestInit) =>
      Promise.resolve(
        json({
          id: 'node',
          token: 'SYNTHETIC_CONNECTION_TOKEN_0123456789',
          config_revision: 1,
          config_hash: 'a'.repeat(64),
        }),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const download = vi
      .spyOn(connections, 'downloadConnection')
      .mockImplementation(() => {})
    mount(NodeConnectionForm, {
      props: { projectId: 'p' },
      attachTo: document.body,
    })
    set('input', '测试电脑')
    const fields = document.querySelectorAll<HTMLInputElement>('input')
    fields[1]!.value = 'C:\\Python\\python.exe'
    fields[1]!.dispatchEvent(new Event('input', { bubbles: true }))
    set(
      '[aria-label="允许读取的文件夹根目录"]',
      'D:\\账单\nD:\\账单\n\\\\server\\share',
    )
    await submit()
    const body = JSON.parse(String(fetcher.mock.calls[0]![1]!.body))
    expect(body.input_roots).toEqual(['D:\\账单', '\\\\server\\share'])
    expect(body.capabilities).toContain(DIRECTORY_CAPABILITY)
    click('2. 下载连接文件')
    expect(download.mock.calls[0]![0].input_roots).toEqual([
      'D:\\账单',
      '\\\\server\\share',
    ])
    expect(
      connections.connectionOptions(
        'https://example.invalid',
        'python',
        '/usr/bin/python',
        '',
      ),
    ).not.toHaveProperty('input_roots')
  })
})
