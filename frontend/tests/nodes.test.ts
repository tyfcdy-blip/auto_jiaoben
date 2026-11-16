import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import NodesView from '../src/views/NodesView.vue'
import NodeConnectionForm from '../src/components/NodeConnectionForm.vue'
import NodeMaintenanceForm from '../src/components/NodeMaintenanceForm.vue'
import Pagination from '../src/components/Pagination.vue'
import { clearSession, session } from '../src/session'
import * as connection from '../src/nodeConnection'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status })
const signed = (id: string, token: string) => ({
  id,
  token,
  config_revision: 1,
  config_hash: 'a'.repeat(64),
})
function login() {
  session.user = {
    id: 'u',
    username: 'test',
    display_name: '测试',
    is_admin: true,
    active: true,
  }
  session.projects = [
    { id: 'p1', name: '项目一', role: 'project_admin' },
    { id: 'p2', name: '项目二', role: 'project_admin' },
  ]
  session.projectId = 'p1'
}
async function fill(wrapper: ReturnType<typeof mount>) {
  const inputs = wrapper.findAll('input')
  await inputs[0]!.setValue('合成处理电脑')
  await inputs[1]!.setValue('C:\\Python312\\python.exe')
}
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  clearSession()
})

it('普通表单登记单槽，pending阻止重复，下载重试不重新签发或缓存token', async () => {
  let resolve!: (value: Response) => void
  const fetcher = vi.fn(
    (_url: string, _options?: RequestInit) =>
      new Promise<Response>((done) => {
        resolve = done
      }),
  )
  vi.stubGlobal('fetch', fetcher)
  const download = vi
    .spyOn(connection, 'downloadConnection')
    .mockImplementation(() => {
      throw new Error('模拟下载失败')
    })
  const wrapper = mount(NodeConnectionForm, { props: { projectId: 'p1' } })
  await fill(wrapper)
  expect(wrapper.text()).not.toContain('JSON 数组')
  await wrapper.find('form').trigger('submit')
  await wrapper.find('form').trigger('submit')
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(JSON.parse(fetcher.mock.calls[0]![1]!.body as string)).toEqual({
    project_id: 'p1',
    name: '合成处理电脑',
    capabilities: ['python'],
    slots: 1,
    input_roots: [],
  })
  resolve(json(signed('node-one', 'SYNTHETIC_NODE_TOKEN_ONLY_0123456789'), 201))
  await flushPromises()
  expect(wrapper.text()).toContain('等待连接')
  const button = wrapper
    .findAll('button')
    .find((item) => item.text().includes('下载连接文件'))!
  await button.trigger('click')
  expect(wrapper.text()).toContain('无需重新登记')
  download.mockImplementation(() => {})
  await button.trigger('click')
  expect(download).toHaveBeenCalledTimes(2)
  expect(download.mock.calls[1]![0].runtimes).toEqual({
    python: 'C:\\Python312\\python.exe',
  })
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
  expect(wrapper.text()).not.toContain('SYNTHETIC_NODE_TOKEN')
})

it('登记成功后列表刷新失败，仍保留下载并可刷新，不会再创建节点', async () => {
  login()
  let created = false
  const fetcher = vi.fn((_url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      created = true
      return Promise.resolve(
        json(signed('n1', 'SYNTHETIC_NODE_TOKEN_ONLY_0123456789'), 201),
      )
    }
    return Promise.resolve(
      created
        ? json({ message: '合成列表失败' }, 503)
        : json({ items: [], total: 0 }),
    )
  })
  vi.stubGlobal('fetch', fetcher)
  const download = vi
    .spyOn(connection, 'downloadConnection')
    .mockImplementation(() => {})
  const wrapper = mount(NodesView, {
    global: { stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
  await flushPromises()
  await wrapper
    .findAll('button')
    .find((button) => button.text().includes('接入电脑'))!
    .trigger('click')
  await fill(wrapper)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('合成列表失败')
  await wrapper
    .findAll('button')
    .find((button) => button.text().includes('下载连接文件'))!
    .trigger('click')
  await wrapper
    .findAll('button')
    .find((button) => button.text() === '刷新')!
    .trigger('click')
  await flushPromises()
  expect(download).toHaveBeenCalledTimes(1)
  expect(
    fetcher.mock.calls.filter((call) => call[1]?.method === 'POST'),
  ).toHaveLength(1)
})

it('切项目后销毁旧签发界面，晚到token不在新项目显示或下载', async () => {
  login()
  let resolve!: (value: Response) => void
  const fetcher = vi.fn((_url: string, options?: RequestInit) =>
    options?.method === 'POST'
      ? new Promise<Response>((done) => {
          resolve = done
        })
      : Promise.resolve(json({ items: [], total: 0 })),
  )
  vi.stubGlobal('fetch', fetcher)
  const download = vi
    .spyOn(connection, 'downloadConnection')
    .mockImplementation(() => {})
  const wrapper = mount(NodesView, {
    global: { stubs: { Modal: { template: '<div><slot /></div>' } } },
  })
  await flushPromises()
  await wrapper
    .findAll('button')
    .find((button) => button.text().includes('接入电脑'))!
    .trigger('click')
  await fill(wrapper)
  await wrapper.find('form').trigger('submit')
  session.projectId = 'p2'
  await flushPromises()
  resolve(
    json(signed('old-node', 'SYNTHETIC_OLD_PROJECT_TOKEN_0123456789'), 201),
  )
  await flushPromises()
  expect(wrapper.find('form').exists()).toBe(false)
  expect(
    wrapper
      .findAll('button')
      .some((button) => button.text() === '下载连接文件'),
  ).toBe(false)
  expect(session.notice).toContain('原项目')
  expect(download).not.toHaveBeenCalled()
  await wrapper
    .findAll('button')
    .find((button) => button.text().includes('接入电脑'))!
    .trigger('click')
  expect(wrapper.find('input').element.value).toBe('')
})

it('网络受理结果不明时阻止盲目重复登记', async () => {
  const fetcher = vi.fn(() => Promise.reject(new TypeError('offline')))
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mount(NodeConnectionForm, { props: { projectId: 'p1' } })
  await fill(wrapper)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  expect(wrapper.text()).toContain('勿直接重复登记')
  await wrapper.find('form').trigger('submit')
  expect(fetcher).toHaveBeenCalledTimes(1)
})

it.each(['broken-json', 'missing-token', 'invalid-token'])(
  '201响应异常 %s 时结果不明，禁止重复登记或下载无效凭据',
  async (kind) => {
    const body =
      kind === 'broken-json'
        ? '{invalid'
        : JSON.stringify(
            kind === 'missing-token' ? { id: 'n1' } : { id: 'n1', token: 123 },
          )
    const fetcher = vi.fn(() =>
      Promise.resolve(new Response(body, { status: 201 })),
    )
    vi.stubGlobal('fetch', fetcher)
    const download = vi
      .spyOn(connection, 'downloadConnection')
      .mockImplementation(() => {})
    const wrapper = mount(NodeConnectionForm, { props: { projectId: 'p1' } })
    await fill(wrapper)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('勿直接重复登记')
    expect(wrapper.emitted('registered')).toBeUndefined()
    await wrapper.find('form').trigger('submit')
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(
      wrapper
        .findAll('button')
        .some((button) => button.text().includes('下载连接文件')),
    ).toBe(false)
    expect(download).not.toHaveBeenCalled()
  },
)

it('运行时选择与高级能力无需JSON，并保留应用子路径', () => {
  expect(
    connection.connectionOptions(
      'https://example.invalid/automation/',
      'python-nine',
      'C:\\Env\\python.exe',
      'nine-billing, 财务，nine-billing',
    ),
  ).toEqual({
    base_url: 'https://example.invalid/automation',
    runtimes: { 'python-nine': 'C:\\Env\\python.exe' },
    capabilities: ['python-nine', 'nine-billing', '财务'],
  })
  expect(() =>
    connection.connectionOptions(
      'http://remote.invalid',
      'python',
      '/usr/bin/python3',
      '',
    ),
  ).toThrow('HTTPS')
  expect(() =>
    connection.connectionOptions(
      'https://example.invalid',
      'python',
      'python.exe',
      '',
    ),
  ).toThrow('绝对路径')
  expect(() =>
    connection.connectionOptions(
      'https://example.invalid',
      'bad alias',
      '/usr/bin/python3',
      '',
    ),
  ).toThrow('运行时名称')
})

it('选择Node.js时登记对应能力，非法远端HTTP不发出创建请求', async () => {
  const fetcher = vi.fn(() =>
    Promise.resolve(
      json(signed('n2', 'SYNTHETIC_NODE_TOKEN_ONLY_0123456789'), 201),
    ),
  )
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mount(NodeConnectionForm, { props: { projectId: 'p1' } })
  await fill(wrapper)
  await wrapper.find('select').setValue('node')
  const inputs = wrapper.findAll('input')
  await inputs[1]!.setValue('C:\\Program Files\\nodejs\\node.exe')
  await inputs[2]!.setValue('http://remote.invalid')
  await wrapper.find('form').trigger('submit')
  expect(fetcher).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('HTTPS')
  await inputs[2]!.setValue('https://remote.invalid/automation')
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  expect(wrapper.emitted('registered')).toHaveLength(1)
})

it('实际下载工具生成可导入的JSON文件并释放浏览器临时地址', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  try {
    let captured!: Blob
    vi.stubGlobal(
      'URL',
      class extends URL {
        static createObjectURL(blob: Blob) {
          captured = blob
          return 'blob:synthetic-connection'
        }
        static revokeObjectURL = vi.fn()
      },
    )
    let filename = ''
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      filename = this.download
    })
    const expected: connection.NodeConnection = {
      format: 'workbench-connection-v1',
      node_id: 'synthetic-node',
      name: '合成电脑',
      base_url: 'https://example.invalid',
      token: 'SYNTHETIC_NODE_TOKEN_ONLY_0123456789',
      runtimes: { python: 'C:\\Python312\\python.exe' },
      capabilities: ['python'],
      config_revision: 1,
      config_hash: 'a'.repeat(64),
    }
    connection.downloadConnection(expected)
    expect(filename).toBe('workbench-connection-synthetic-node.json')
    const text = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = reject
      reader.readAsText(captured)
    })
    expect(JSON.parse(text)).toEqual(expected)
    expect(document.querySelector('a[download]')).toBeNull()
    vi.runAllTimers()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(
      'blob:synthetic-connection',
    )
  } finally {
    vi.useRealTimers()
  }
})

it('登记后可下载一键接入包且不会重复签发节点', async () => {
  let created = false
  const fetcher = vi.fn((_url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      created = true
      return Promise.resolve(
        json(signed('n-package', 'SYNTHETIC_NODE_TOKEN_ONLY_0123456789'), 201),
      )
    }
    return Promise.resolve(
      new Response('synthetic-zip', {
        status: created ? 200 : 401,
        headers: { 'Content-Type': 'application/zip' },
      }),
    )
  })
  vi.stubGlobal('fetch', fetcher)
  const createObjectURL = vi.fn(() => 'blob:synthetic-agent-package')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal(
    'URL',
    class extends URL {
      static createObjectURL = createObjectURL
      static revokeObjectURL = revokeObjectURL
    },
  )
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  const wrapper = mount(NodeConnectionForm, { props: { projectId: 'p1' } })
  await fill(wrapper)
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  await wrapper
    .findAll('button')
    .find((button) => button.text().includes('一键接入包'))!
    .trigger('click')
  await flushPromises()
  expect(
    fetcher.mock.calls.filter((call) => call[1]?.method === 'POST'),
  ).toHaveLength(1)
  expect(
    fetcher.mock.calls.some((call) =>
      String(call[0]).endsWith('/agent-packages/windows'),
    ),
  ).toBe(true)
  await vi.waitFor(() => {
    expect(createObjectURL).toHaveBeenCalledTimes(1)
  })
})

it('平台管理员可一次保存电脑根目录和多个项目授权', async () => {
  login()
  const node = {
    id: 'shared-node',
    project_id: 'p1',
    name: '共享电脑',
    capabilities: ['python'],
    slots: 1,
    last_seen: null,
    online: false,
    enabled: true,
    maintenance: true,
    isolation: 'development',
    config_revision: 1,
    applied_revision: 1,
    config_status: 'applied',
  }
  const configuration = {
    node,
    desired_revision: 1,
    applied_revision: 1,
    config_status: 'applied',
    config_error: '',
    input_roots: ['D:/共享'],
    root_status: [],
    grants: [
      {
        project_id: 'p1',
        project_name: '项目一',
        enabled: true,
        input_roots: ['D:/共享/项目一'],
      },
    ],
  }
  const fetcher = vi.fn((_url: string, options?: RequestInit) =>
    Promise.resolve(
      json(
        options?.method === 'PUT'
          ? { ...configuration, desired_revision: 2, config_status: 'pending' }
          : configuration,
      ),
    ),
  )
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mount(NodeMaintenanceForm, { props: { node } })
  await flushPromises()
  expect(wrapper.text()).toContain('项目二')
  const checkboxes = wrapper.findAll<HTMLInputElement>('input[type="checkbox"]')
  expect(checkboxes[0]!.attributes('disabled')).toBeDefined()
  await checkboxes[1]!.setValue(true)
  const textareas = wrapper.findAll('textarea')
  await textareas.at(-1)!.setValue('D:/共享/项目二')
  await wrapper.find('form').trigger('submit')
  await flushPromises()
  const put = fetcher.mock.calls.find(
    ([, options]) => options?.method === 'PUT',
  )!
  expect(JSON.parse(String(put[1]!.body))).toMatchObject({
    base_revision: 1,
    input_roots: ['D:/共享'],
    grants: [
      { project_id: 'p1', enabled: true, input_roots: ['D:/共享/项目一'] },
      { project_id: 'p2', enabled: true, input_roots: ['D:/共享/项目二'] },
    ],
  })
  expect(wrapper.emitted('saved')).toHaveLength(1)
})

it('平台管理员看到全局电脑清单，切换项目不改为项目过滤列表', async () => {
  login()
  const fetcher = vi.fn((_url: string) =>
    Promise.resolve(json({ items: [], total: 51 })),
  )
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mount(NodesView)
  await flushPromises()
  expect(String(fetcher.mock.calls[0]![0])).toBe(
    '/api/v1/nodes?limit=50&offset=0',
  )
  expect(wrapper.text()).toContain('全平台电脑统一维护')
  expect(wrapper.text()).toContain('接入电脑')
  wrapper.findComponent(Pagination).vm.$emit('update:offset', 50)
  await flushPromises()
  expect(String(fetcher.mock.calls.at(-1)![0])).toBe(
    '/api/v1/nodes?limit=50&offset=50',
  )
  session.projectId = 'p2'
  await flushPromises()
  expect(String(fetcher.mock.calls.at(-1)![0])).toBe(
    '/api/v1/nodes?limit=50&offset=50',
  )
})

it('非平台管理员只读取当前项目授权的电脑', async () => {
  login()
  session.user!.is_admin = false
  const fetcher = vi.fn((_url: string) =>
    Promise.resolve(json({ items: [], total: 0 })),
  )
  vi.stubGlobal('fetch', fetcher)
  const wrapper = mount(NodesView)
  await flushPromises()
  expect(String(fetcher.mock.calls[0]![0])).toBe(
    '/api/v1/nodes?project_id=p1&limit=50&offset=0',
  )
  expect(wrapper.text()).toContain('当前项目有权使用的电脑')
})

it('电脑同时显示闲忙容量和不能接单的原因', async () => {
  login()
  const node = {
    id: 'pending-node',
    project_id: 'p1',
    name: '待升级电脑',
    capabilities: ['python'],
    slots: 1,
    last_seen: '2026-09-18T08:00:00Z',
    online: true,
    enabled: true,
    maintenance: false,
    isolation: 'development',
    config_revision: 2,
    applied_revision: 0,
    config_status: 'pending',
    dispatch_status: 'config_pending',
    capacity_status: 'idle',
    dispatch_detail: '执行器版本过旧，不能同步平台配置；受管任务暂停',
    active_runs: 0,
    available_slots: 1,
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve(json({ items: [node], total: 1 }))),
  )
  const wrapper = mount(NodesView)
  await flushPromises()
  expect(wrapper.text()).toContain('配置待同步')
  expect(wrapper.text()).toContain('空闲')
  expect(wrapper.text()).toContain('运行 0/1')
  expect(wrapper.text()).toContain('受管任务暂停')
})
