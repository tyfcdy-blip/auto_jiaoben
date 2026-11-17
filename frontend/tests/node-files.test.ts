import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import RemotePathPicker from '../src/components/RemotePathPicker.vue'
import TaskFileFields from '../src/components/TaskFileFields.vue'
import TasksView from '../src/views/TasksView.vue'
import NodeDetailView from '../src/views/NodeDetailView.vue'
import RunDetailView from '../src/views/RunDetailView.vue'
import { clearSession, session } from '../src/session'
import { fileFields } from '../src/nodeFiles'

const mounted: VueWrapper[] = []
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status })
const node = {
  id: 'n1',
  name: '本机测试',
  project_id: 'p1',
  capabilities: ['node-files-v1'],
  slots: 1,
  last_seen: '2026-11-17T00:00:00Z',
  online: true,
  enabled: true,
  maintenance: false,
  isolation: 'development',
  dispatch_status: 'dispatch_paused',
  dispatch_detail: '平台派发已暂停',
  active_runs: 0,
  config_status: 'applied',
  projects: [{ project_id: 'p1', name: '测试项目', enabled: true }],
}
const configuration = {
  revision: 1,
  applied_revision: 1,
  apply_status: 'applied',
  output_roots: ['D:\\结果'],
  allowed_input_roots: ['D:\\输入'],
  allowed_output_roots: ['D:\\结果'],
  grants: [{ project_id: 'p1', output_roots: ['D:\\结果'] }],
}
const schema = {
  type: 'object',
  properties: {
    source_files: {
      type: 'array',
      items: { type: 'string' },
      title: '待合并表格',
      'x-workbench-file': 'files',
    },
    label: { type: 'string', title: '报表名称' },
  },
  required: ['source_files'],
}
function login() {
  session.user = {
    id: 'u1',
    username: 'tester',
    display_name: '测试',
    is_admin: true,
    active: true,
  }
  session.projectId = 'p1'
  session.projects = [{ id: 'p1', name: '测试项目', role: 'project_admin' }]
}
function picker(
  kind: 'file' | 'files' | 'directory' = 'files',
  roots = ['D:\\输入'],
) {
  const wrapper = mount(RemotePathPicker, {
    props: { nodeId: 'n1', projectId: 'p1', roots, purpose: 'input', kind },
  })
  mounted.push(wrapper)
  return wrapper
}
async function show(
  component: typeof TasksView | typeof NodeDetailView | typeof RunDetailView,
  path: string,
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/nodes/:id', component: NodeDetailView },
      { path: '/tasks', component: TasksView },
      { path: '/runs/:id', component: RunDetailView },
      { path: '/:pathMatch(.*)*', component: { template: '<p>下一页</p>' } },
    ],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, {
    attachTo: document.body,
    global: { plugins: [router] },
  })
  mounted.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  clearSession()
  document.body.innerHTML = ''
})

it('只识别明确标记的文件字段，普通参数不变', () => {
  expect(fileFields(schema)).toEqual([
    { key: 'source_files', title: '待合并表格', kind: 'files' },
  ])
  expect(
    fileFields({
      properties: { malformed: null, other: { 'x-workbench-file': 'script' } },
    }),
  ).toEqual([])
})

it('通过执行器请求目录并多选文件，显示空目录且无需共享盘', async () => {
  const fetcher = vi.fn(async (_url: string, options: RequestInit) =>
    json(
      options.method === 'POST'
        ? { id: 'req1' }
        : {
            id: 'req1',
            state: 'succeeded',
            entries: [
              { name: 'a.xlsx', path: 'D:\\输入\\a.xlsx', kind: 'file' },
              { name: 'b.xlsx', path: 'D:\\输入\\b.xlsx', kind: 'file' },
            ],
            truncated: false,
          },
    ),
  )
  vi.stubGlobal('fetch', fetcher)
  const wrapper = picker()
  await flushPromises()
  expect(JSON.parse(fetcher.mock.calls[0]![1].body as string)).toMatchObject({
    project_id: 'p1',
    purpose: 'input',
    path: 'D:\\输入',
    offset: 0,
    limit: 200,
  })
  await wrapper.findAll('input[type="checkbox"]')[0]!.setValue(true)
  await wrapper.findAll('input[type="checkbox"]')[1]!.setValue(true)
  await wrapper
    .findAll('button')
    .find((button) => button.text().includes('使用选中的'))!
    .trigger('click')
  expect(wrapper.emitted('select')?.[0]).toEqual([
    ['D:\\输入\\a.xlsx', 'D:\\输入\\b.xlsx'],
  ])
  expect(wrapper.text()).toContain('无需共享文件夹')
})

it('目录翻页按offset重新请求，不把结果目录误选为文件', async () => {
  let offset = 0
  const fetcher = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'POST') {
      offset = JSON.parse(options.body as string).offset
      return json({ id: `req${offset}` })
    }
    return json({
      id: `req${offset}`,
      state: 'succeeded',
      entries: [],
      truncated: offset === 0,
      next_offset: offset === 0 ? 200 : null,
    })
  })
  vi.stubGlobal('fetch', fetcher)
  const wrapper = picker('directory')
  await flushPromises()
  expect(wrapper.text()).toContain('这个目录没有可显示')
  await wrapper
    .findAll('button')
    .find((button) => button.text() === '下一页文件')!
    .trigger('click')
  await flushPromises()
  expect(offset).toBe(200)
  await wrapper
    .findAll('button')
    .find((button) => button.text() === '选择当前文件夹')!
    .trigger('click')
  expect(wrapper.emitted('select')?.[0]).toEqual([['D:\\输入']])
})

it.each(['expired', 'failed'])(
  '目录%s显示服务端原因，不能选择未验证目录',
  async (state) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, options: RequestInit) =>
        json(
          options.method === 'POST'
            ? { id: 'r' }
            : { id: 'r', state, error: '授权已撤销，请重新选择' },
        ),
      ),
    )
    const wrapper = picker('directory')
    await flushPromises()
    expect(wrapper.text()).toContain('授权已撤销')
    expect(
      wrapper
        .findAll('button')
        .find((button) => button.text() === '选择当前文件夹')!
        .attributes('disabled'),
    ).toBeDefined()
  },
)

it('目录请求被拒绝时显示权限错误，不启动轮询', async () => {
  const fetcher = vi.fn(async () =>
    json({ message: '没有访问该项目目录的权限' }, 403),
  )
  vi.stubGlobal('fetch', fetcher)
  const wrapper = picker('directory')
  await flushPromises()
  expect(wrapper.text()).toContain('没有访问该项目目录的权限')
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it('执行器一直不响应时在本地截止时间结束等待', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] })
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, options: RequestInit) =>
      json(
        options.method === 'POST'
          ? { id: 'slow' }
          : { id: 'slow', state: 'pending' },
      ),
    ),
  )
  const wrapper = picker('directory')
  await flushPromises()
  await vi.advanceTimersByTimeAsync(65000)
  expect(wrapper.text()).toContain('等待电脑响应超时')
  expect(
    wrapper
      .findAll('button')
      .find((button) => button.text() === '选择当前文件夹')!
      .attributes('disabled'),
  ).toBeDefined()
})

it('切换电脑会撤销旧请求，晚到结果不覆盖新电脑目录', async () => {
  let release: ((value: Response) => void) | undefined
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST')
      return json({ id: url.includes('n1') ? 'old' : 'new' })
    if (url.includes('/old'))
      return new Promise<Response>((resolve) => {
        release = resolve
      })
    return json({
      id: 'new',
      state: 'succeeded',
      entries: [{ name: '新电脑文件', path: 'D:\\输入\\new', kind: 'file' }],
      truncated: false,
    })
  })
  vi.stubGlobal('fetch', fetcher)
  const wrapper = picker('file')
  await flushPromises()
  const oldSignal = fetcher.mock.calls[1]![1].signal
  await wrapper.setProps({ nodeId: 'n2' })
  await flushPromises()
  expect(oldSignal?.aborted).toBe(true)
  release!(
    json({
      id: 'old',
      state: 'succeeded',
      entries: [{ name: '旧电脑文件', path: 'old', kind: 'file' }],
    }),
  )
  await flushPromises()
  expect(wrapper.text()).toContain('新电脑文件')
  expect(wrapper.text()).not.toContain('旧电脑文件')
})

it('等待执行器有明确状态；卸载后终止轮询；空授权不请求目录', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const fetcher = vi.fn(async (_url: string, options: RequestInit) =>
    json(
      options.method === 'POST' ? { id: 'r' } : { id: 'r', state: 'leased' },
    ),
  )
  vi.stubGlobal('fetch', fetcher)
  const empty = picker('file', [])
  await flushPromises()
  expect(fetcher).not.toHaveBeenCalled()
  expect(empty.text()).toContain('尚未获准访问目录')
  const wrapper = picker()
  await flushPromises()
  expect(wrapper.text()).toContain('正在等待执行电脑')
  wrapper.unmount()
  const count = fetcher.mock.calls.length
  await vi.advanceTimersByTimeAsync(10000)
  expect(fetcher).toHaveBeenCalledTimes(count)
})

it('离线、旧执行器、写目录未同步均不提供可点的浏览入口', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => json({ ...configuration, apply_status: 'pending' })),
  )
  const wrapper = mount(TaskFileFields, {
    props: {
      node: { ...node, online: false },
      projectId: 'p1',
      schema,
      modelValue: { inputs: {}, output_dir: null },
    },
  })
  mounted.push(wrapper)
  await flushPromises()
  expect(wrapper.text()).toContain('电脑离线')
  expect(
    wrapper
      .findAll('button')
      .filter((button) => button.text().startsWith('选择'))
      .every((button) => button.attributes('disabled') !== undefined),
  ).toBe(true)
  await wrapper.setProps({ node })
  expect(
    wrapper
      .findAll('button')
      .find((button) => button.text() === '选择结果保存文件夹')!
      .attributes('disabled'),
  ).toBeDefined()
  await wrapper.setProps({ node: { ...node, capabilities: ['python'] } })
  expect(wrapper.text()).toContain('先升级并启动执行器')
  expect(wrapper.findAll('button')).toHaveLength(0)
})

it('从电脑创建任务，选中文件和输出一次性提交且必填文件参数合入默认值', async () => {
  login()
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') return json({ id: 'created' })
    if (url.includes('/file-configuration')) return json(configuration)
    const items = url.includes('/programs?')
      ? [{ id: 'program', name: '测试程序' }]
      : url.includes('/versions')
        ? [{ id: 'v1', published: true, name: '1.0', parameter_schema: schema }]
        : url.includes('/nodes?')
          ? [node]
          : []
    return json({ items, total: items.length })
  })
  vi.stubGlobal('fetch', fetcher)
  const { wrapper } = await show(TasksView, '/tasks?node_id=n1&create=1')
  const fields = wrapper.getComponent(TaskFileFields)
  expect(fields.props('node')?.id).toBe('n1')
  fields.vm.$emit('update:modelValue', {
    inputs: { source_files: ['D:\\输入\\账单.xlsx'] },
    output_dir: 'D:\\结果',
  })
  await flushPromises()
  const taskName = document.querySelector<HTMLInputElement>(
    'form input[maxlength="100"]',
  )!
  taskName.value = '文件测试'
  taskName.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
  document
    .querySelector('form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flushPromises()
  const saved = fetcher.mock.calls.find(
    ([url, options]) => url.endsWith('/tasks') && options.method === 'POST',
  )!
  expect(saved).toBeTruthy()
  const body = JSON.parse(saved[1].body as string)
  expect(body).toMatchObject({
    name: '文件测试',
    node_id: 'n1',
    default_parameters: { source_files: ['D:\\输入\\账单.xlsx'] },
    file_options: {
      inputs: { source_files: ['D:\\输入\\账单.xlsx'] },
      output_dir: 'D:\\结果',
    },
  })
  expect(
    fetcher.mock.calls.some(
      ([url, options]) =>
        url.endsWith('/file-options') && options.method === 'PUT',
    ),
  ).toBe(false)
})

it('电脑工作区展示心跳/派发状态，只查询该电脑任务与运行', async () => {
  login()
  const fetcher = vi.fn(async (url: string) =>
    json(url.endsWith('/nodes/n1') ? node : { items: [], total: 0 }),
  )
  vi.stubGlobal('fetch', fetcher)
  const { wrapper, router } = await show(NodeDetailView, '/nodes/n1')
  expect(wrapper.text()).toContain('平台派发已暂停')
  expect(wrapper.text()).toContain('最近心跳')
  expect(
    fetcher.mock.calls.some(([url]) => url.includes('/runs?node_id=n1')),
  ).toBe(true)
  const navigate = vi.spyOn(router, 'push').mockResolvedValue(undefined)
  await wrapper
    .findAll('button')
    .find((button) => button.text() === '在这台电脑上创建任务')!
    .trigger('click')
  await flushPromises()
  expect(navigate).toHaveBeenCalledWith({
    path: '/tasks',
    query: { node_id: 'n1', create: '1' },
  })
})

it('编辑任务改成自动匹配会原子清除旧文件设置，不保留失效电脑绑定', async () => {
  login()
  const task = {
    id: 't1',
    name: '原任务',
    project_id: 'p1',
    program_id: 'program',
    version_id: 'v1',
    node_id: 'n1',
    default_parameters: {},
    resource_keys: [],
    timeout_seconds: 300,
    operation: 'run',
    enabled: true,
    requires_input: false,
    config_hash: 'base-hash',
  }
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'PUT') return json(task)
    if (url.includes('/file-configuration')) return json(configuration)
    if (url.endsWith('/file-options'))
      return json({
        inputs: {},
        output_dir: 'D:\\结果',
        base_config_hash: 'base-hash',
      })
    const items = url.includes('/tasks?')
      ? [task]
      : url.includes('/nodes?')
        ? [node]
        : url.includes('/programs?')
          ? [{ id: 'program', name: '程序' }]
          : url.includes('/versions')
            ? [{ id: 'v1', name: '1.0', published: true, parameter_schema: {} }]
            : []
    return json({ items, total: items.length })
  })
  vi.stubGlobal('fetch', fetcher)
  const { wrapper } = await show(TasksView, '/tasks')
  await wrapper
    .findAll('button')
    .find((button) => button.text() === '编辑')!
    .trigger('click')
  await flushPromises()
  expect(wrapper.getComponent(TaskFileFields).props('modelValue')).toEqual({
    inputs: {},
    output_dir: 'D:\\结果',
  })
  const computer = document.querySelector<HTMLSelectElement>(
    'select[aria-label="执行电脑"]',
  )!
  computer.value = ''
  computer.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
  document
    .querySelector('form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flushPromises()
  const saved = fetcher.mock.calls.find(
    ([url, options]) => url.endsWith('/tasks/t1') && options.method === 'PUT',
  )!
  expect(JSON.parse(saved[1].body as string)).toMatchObject({
    node_id: null,
    file_options: { inputs: {}, output_dir: null },
    base_config_hash: 'base-hash',
  })
})

it('文件多选最多100项，达到上限仍可取消已选项', async () => {
  const entries = Array.from({ length: 101 }, (_, index) => ({
    name: `file-${index}.txt`,
    path: `D:\\输入\\file-${index}.txt`,
    kind: 'file',
  }))
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, options: RequestInit) =>
      json(
        options.method === 'POST'
          ? { id: 'r' }
          : { id: 'r', state: 'succeeded', entries, truncated: false },
      ),
    ),
  )
  const wrapper = picker('files')
  await flushPromises()
  const boxes = wrapper.findAll('input[type="checkbox"]')
  for (const checkbox of boxes.slice(0, 100)) await checkbox.setValue(true)
  expect(boxes[100]!.attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('当前已选 100 个')
  await boxes[0]!.setValue(false)
  expect(boxes[100]!.attributes('disabled')).toBeUndefined()
})

it('脚本成功而另存失败时，独立显示失败并只重试保存，不重跑脚本', async () => {
  login()
  const run = {
    id: 'r1',
    project_id: 'p1',
    task_id: 't1',
    task_name: '另存测试',
    attempt_id: 'attempt-1',
    state: 'succeeded',
    source: 'manual',
    started_at: '2026-11-17T00:00:00Z',
    finished_at: '2026-11-17T00:00:01Z',
    parameters: {},
    exit_code: 0,
    artifact_state: 'ready',
    file_status: {
      enabled: true,
      inputs: {},
      output_dir: 'D:\\结果',
      save_state: 'failed',
      saved_files: [],
      save_error: '磁盘空间不足',
    },
    artifacts: [],
  }
  const fetcher = vi.fn(async (url: string, options: RequestInit) =>
    json(
      options.method === 'POST'
        ? { accepted: true }
        : url.endsWith('/logs')
          ? { entries: [], truncated: false }
          : run,
    ),
  )
  vi.stubGlobal('fetch', fetcher)
  const { wrapper } = await show(RunDetailView, '/runs/r1')
  expect(wrapper.text()).toContain('磁盘空间不足')
  expect(wrapper.text()).toContain('另存失败不代表脚本需要重新运行')
  await wrapper
    .findAll('button')
    .find((button) => button.text() === '仅重试另存结果')!
    .trigger('click')
  await flushPromises()
  expect(
    fetcher.mock.calls
      .filter(([, options]) => options.method === 'POST')
      .map(([url]) => url),
  ).toEqual(['/api/v1/runs/r1/file-save/retry'])
})
