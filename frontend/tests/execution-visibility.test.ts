import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, expect, it, vi } from 'vitest'
import RunsView from '../src/views/RunsView.vue'
import NodesView from '../src/views/NodesView.vue'
import RunDetailView from '../src/views/RunDetailView.vue'
import { clearSession, session } from '../src/session'

const mounted: VueWrapper[] = []
const json = (value: unknown) => new Response(JSON.stringify(value))
const queued = {
  id: 'r1',
  project_id: 'p1',
  task_id: 't1',
  task_name: '测试任务',
  source: 'manual',
  state: 'queued',
  created_at: '2026-11-17T03:00:00Z',
  waiting_reason_code: 'dispatch_paused',
  waiting_reason: '平台派发已暂停',
  started_at: null,
  attempt_id: null,
  parameters: {},
  artifacts: [],
}
function login() {
  session.user = {
    id: 'u1',
    username: 'test',
    display_name: '测试',
    is_admin: true,
    active: true,
  }
  session.projectId = 'p1'
  session.projects = [{ id: 'p1', name: '测试项目', role: 'project_admin' }]
}
async function show(
  component: typeof RunsView | typeof NodesView | typeof RunDetailView,
  path = '/runs',
) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component }],
  })
  await router.push(path)
  await router.isReady()
  const wrapper = mount(component, { global: { plugins: [router] } })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.useRealTimers()
  clearSession()
})

it('运行中心突出平台暂停且不提供虚假的开启按钮', async () => {
  login()
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      json({ items: url.includes('/runs?') ? [queued] : [], total: 1 }),
    ),
  )
  const wrapper = await show(RunsView)
  expect(wrapper.get('[role="status"]').text()).toContain(
    '平台派发已暂停，排队任务尚未执行',
  )
  expect(wrapper.get('[role="status"]').text()).toContain('先核对待执行任务')
  expect(
    wrapper
      .findAll('button')
      .some((button) => /开启派发|恢复派发/.test(button.text())),
  ).toBe(false)
})

it.each([
  ['运行中心', RunsView, '/runs?', '/runs'],
  ['执行电脑', NodesView, '/nodes?', '/nodes'],
] as const)(
  '%s仅在可见和请求空闲时自动刷新，卸载清理',
  async (_name, component, endpoint, path) => {
    login()
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    let hidden = false
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
    let release: (() => void) | undefined
    let hold = false
    const fetcher = vi.fn(async (url: string) => {
      if (hold && url.includes(endpoint))
        await new Promise<void>((resolve) => {
          release = resolve
        })
      return json({ items: [], total: 0 })
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = await show(component, path)
    const count = () =>
      fetcher.mock.calls.filter(([url]) => url.includes(endpoint)).length
    const choicesBefore = fetcher.mock.calls.filter(([url]) =>
      url.includes('/tasks?'),
    ).length
    const initial = count()
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(count()).toBe(initial + 1)
    hidden = true
    await vi.advanceTimersByTimeAsync(5000)
    expect(count()).toBe(initial + 1)
    hidden = false
    hold = true
    await vi.advanceTimersByTimeAsync(5000)
    expect(count()).toBe(initial + 2)
    await vi.advanceTimersByTimeAsync(10000)
    expect(count()).toBe(initial + 2)
    release?.()
    await flushPromises()
    wrapper.unmount()
    mounted.splice(mounted.indexOf(wrapper), 1)
    await vi.advanceTimersByTimeAsync(10000)
    expect(count()).toBe(initial + 2)
    expect(
      fetcher.mock.calls.filter(([url]) => url.includes('/tasks?')).length,
    ).toBe(choicesBefore)
  },
)

it.each([false, true])(
  '排队失败只在没有启动证据时说明未执行：started=%s',
  async (started) => {
    login()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        json(
          url.endsWith('/logs')
            ? { entries: [], truncated: false }
            : {
                ...queued,
                state: 'failed',
                error: '排队超时，未启动程序',
                started_at: started ? '2026-11-17T03:01:00Z' : null,
                attempt_id: started ? 'a1' : null,
              },
        ),
      ),
    )
    const wrapper = await show(RunDetailView, '/runs/r1')
    expect(wrapper.text().includes('本次任务没有在执行电脑上运行')).toBe(
      !started,
    )
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '业务结果')!
      .trigger('click')
    expect(wrapper.text()).toContain(
      started
        ? '本次运行没有已归档的可下载文件'
        : '程序尚未启动，没有生成结果文件',
    )
  },
)

it('运行详情日志请求未结束时不重复轮询，隐藏暂停且卸载清理', async () => {
  login()
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  let hidden = false
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  let holdLogs = false
  let releaseLogs: (() => void) | undefined
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/logs')) {
      if (holdLogs)
        await new Promise<void>((resolve) => {
          releaseLogs = resolve
        })
      return json({ entries: [], truncated: false })
    }
    return json({
      ...queued,
      state: 'running',
      started_at: '2026-11-17T03:01:00Z',
      attempt_id: 'a1',
    })
  })
  vi.stubGlobal('fetch', fetcher)
  const wrapper = await show(RunDetailView, '/runs/r1')
  const counts = () => ({
    logs: fetcher.mock.calls.filter(([url]) => url.endsWith('/logs')).length,
    runs: fetcher.mock.calls.filter(([url]) => !url.endsWith('/logs')).length,
  })
  expect(counts()).toEqual({ logs: 1, runs: 1 })
  holdLogs = true
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(counts()).toEqual({ logs: 2, runs: 2 })
  await vi.advanceTimersByTimeAsync(10000)
  expect(counts()).toEqual({ logs: 2, runs: 2 })
  holdLogs = false
  releaseLogs?.()
  await flushPromises()
  hidden = true
  await vi.advanceTimersByTimeAsync(5000)
  expect(counts()).toEqual({ logs: 2, runs: 2 })
  hidden = false
  await vi.advanceTimersByTimeAsync(5000)
  await flushPromises()
  expect(counts()).toEqual({ logs: 3, runs: 3 })
  wrapper.unmount()
  mounted.splice(mounted.indexOf(wrapper), 1)
  await vi.advanceTimersByTimeAsync(10000)
  expect(counts()).toEqual({ logs: 3, runs: 3 })
})

it.each([
  ['运行中心', RunsView, '/runs?', '/runs'],
  ['执行电脑', NodesView, '/nodes?', '/nodes'],
] as const)(
  '%s轮询请求失败后下周期恢复，不会卡在加载中',
  async (_name, component, endpoint, path) => {
    login()
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    let attempts = 0
    const fetcher = vi.fn(async (url: string) => {
      if (url.includes(endpoint)) {
        attempts++
        if (attempts === 2) throw new Error('测试网络故障')
      }
      return json({ items: [], total: 0 })
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = await show(component, path)
    expect(attempts).toBe(1)
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(attempts).toBe(2)
    expect(wrapper.text()).toContain('暂时无法连接工作台，请检查网络后重试。')
    await vi.advanceTimersByTimeAsync(5000)
    await flushPromises()
    expect(attempts).toBe(3)
    expect(wrapper.text()).not.toContain(
      '暂时无法连接工作台，请检查网络后重试。',
    )
    const refresh = wrapper
      .findAll('button')
      .find((button) => button.text().startsWith('刷新'))!
    expect(refresh.attributes('disabled')).toBeUndefined()
  },
)

it('电脑页面解释连接与槽位不代表已经执行', async () => {
  login()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => json({ items: [], total: 0 })),
  )
  const wrapper = await show(NodesView, '/nodes')
  expect(wrapper.text()).toContain('在线表示电脑已启用且最近收到心跳')
  expect(wrapper.text()).toContain('空闲表示还有运行名额，不代表任务已经启动')
})
