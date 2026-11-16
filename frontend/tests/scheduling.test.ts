import { flushPromises, mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import { afterEach, describe, it, expect, vi } from 'vitest'
import TasksView from '../src/views/TasksView.vue'
import { clearSession, session } from '../src/session'
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status })
function operator() {
  session.user = {
    id: 'u',
    username: 'operator',
    display_name: '运行人员',
    is_admin: false,
    active: true,
  }
  session.projectId = 'p'
  session.projects = [{ id: 'p', name: '项目', role: 'operator' }]
}
afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})
describe('v0.3 事件与定时表单', () => {
  it('创建任务默认绑定一个已发布版本，高级配置仍可选择历史版本', async () => {
    operator()
    session.user!.is_admin = true
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') return json({ id: 'task' })
      const items = url.includes('/programs?')
        ? [{ id: 'program', name: '合并报表' }]
        : url.includes('/programs/program/versions')
          ? [
              { id: 'draft', name: '草稿', published: false },
              { id: 'v2', name: '2.0', published: true },
              { id: 'v1', name: '1.0', published: true },
            ]
          : []
      return json({ items, total: items.length })
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: TasksView }],
    })
    await router.push('/')
    const wrapper = mount(TasksView, {
      attachTo: document.body,
      global: { plugins: [router] },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('创建任务'))!
      .trigger('click')
    await flushPromises()
    const form = document.querySelector('form')!
    expect(form.textContent).toContain('使用版本：2.0')
    expect(form.querySelector('details')!.open).toBe(false)
    expect(form.querySelector('option[value="draft"]')).toBeNull()
    expect(form.querySelector('option[value="v1"]')).not.toBeNull()
    const name = form.querySelector('input')!
    name.value = '每月报表'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const call = fetcher.mock.calls.find(
      ([, options]) => options.method === 'POST',
    )!
    expect(JSON.parse(String(call[1].body))).toMatchObject({
      version_id: 'v2',
      node_id: null,
      default_parameters: {},
      timeout_seconds: 300,
    })
  })
  it.each(['切换项目', '卸载页面'])(
    '%s 后异步选项返回不会重新打开创建窗口',
    async (change) => {
      operator()
      session.user!.is_admin = true
      let resolvePrograms!: (value: Response) => void
      const fetcher = vi.fn((url: string) =>
        url.includes('/programs?')
          ? new Promise<Response>((resolve) => {
              resolvePrograms = resolve
            })
          : Promise.resolve(json({ items: [], total: 0 })),
      )
      vi.stubGlobal('fetch', fetcher)
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: '/', component: TasksView }],
      })
      await router.push('/')
      const wrapper = mount(TasksView, {
        attachTo: document.body,
        global: { plugins: [router] },
      })
      await flushPromises()
      await wrapper
        .findAll('button')
        .find((button) => button.text().includes('创建任务'))!
        .trigger('click')
      if (change === '切换项目') session.projectId = 'other'
      else wrapper.unmount()
      await flushPromises()
      resolvePrograms(
        json({ items: [{ id: 'old', name: '旧项目程序' }], total: 1 }),
      )
      await flushPromises()
      expect(document.querySelector('[role="dialog"]')).toBeNull()
      expect(
        fetcher.mock.calls.some(([url]) =>
          url.includes('/programs/old/versions'),
        ),
      ).toBe(false)
    },
  )
  it('定时计划保留日期模板并以关闭状态创建', async () => {
    operator()
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (options.method === 'POST') return Promise.resolve(json({ id: 's' }))
      return Promise.resolve(
        json(
          url.includes('/tasks?')
            ? {
                items: [
                  {
                    id: 't',
                    project_id: 'p',
                    name: '每日任务',
                    enabled: true,
                    default_parameters: {},
                    requires_input: false,
                    operation: 'run',
                    timeout_seconds: 300,
                  },
                ],
                total: 1,
              }
            : { items: [], total: 0 },
        ),
      )
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: TasksView },
        { path: '/runs', component: { template: '<p>运行中心</p>' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(TasksView, {
      attachTo: document.body,
      global: { plugins: [router] },
    })
    await flushPromises()
    expect(wrapper.get('a[href="/runs?project_id=p&task_id=t"]').text()).toBe(
      '运行记录',
    )
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '定时运行')!
      .trigger('click')
    await flushPromises()
    ;(
      Array.from(document.querySelectorAll('button')).find((button) =>
        button.textContent?.includes('添加定时计划'),
      ) as HTMLButtonElement
    ).click()
    await flushPromises()
    const form = document.querySelector('form')!
    const inputs = form.querySelectorAll('input')
    inputs[0]!.value = '每天取数'
    inputs[0]!.dispatchEvent(new Event('input', { bubbles: true }))
    const textarea = form.querySelector('textarea')!
    textarea.value = '{"period":"${previous_month}"}'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const call = fetcher.mock.calls.find(
      ([url, options]) =>
        url.endsWith('/schedules') && options.method === 'POST',
    )!
    expect(JSON.parse(String(call[1].body))).toMatchObject({
      task_id: 't',
      cron: '0 8 * * *',
      timezone: 'Asia/Shanghai',
      enabled: false,
      parameters: { period: '${previous_month}' },
    })
  })
  it('任务内计划启停失败不改状态，成功后才更新', async () => {
    operator()
    let patchAttempts = 0
    const task = {
      id: 't',
      project_id: 'p',
      name: '每日任务',
      enabled: true,
      default_parameters: {},
      requires_input: false,
      operation: 'run',
      timeout_seconds: 300,
    }
    const schedule = {
      id: 's',
      task_id: 't',
      project_id: 'p',
      name: '每日八点',
      cron: '0 8 * * *',
      timezone: 'Asia/Shanghai',
      parameters: {},
      input_batch_id: null,
      misfire_policy: 'skip',
      enabled: false,
      next_fire: '2026-09-19T00:00:00Z',
    }
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (options.method === 'PATCH') {
        patchAttempts++
        return Promise.resolve(
          patchAttempts === 1
            ? json({ message: '模拟启用失败' }, 503)
            : json({ ...schedule, enabled: true }),
        )
      }
      return Promise.resolve(
        json(
          url.includes('/tasks?')
            ? { items: [task], total: 1 }
            : url.includes('/schedules?')
              ? { items: [schedule], total: 1 }
              : { items: [], total: 0 },
        ),
      )
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: TasksView },
        { path: '/runs', component: { template: '<p>运行中心</p>' } },
      ],
    })
    await router.push('/')
    const wrapper = mount(TasksView, {
      attachTo: document.body,
      global: { plugins: [router] },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '定时运行')!
      .trigger('click')
    await flushPromises()
    const enable = () =>
      Array.from(document.querySelectorAll('button')).find(
        (button) => button.textContent?.trim() === '启用',
      ) as HTMLButtonElement
    enable().click()
    await flushPromises()
    expect(document.body.textContent).toContain('模拟启用失败')
    expect(enable()).toBeTruthy()
    enable().click()
    await flushPromises()
    expect(document.body.textContent).toContain('已启用')
    expect(
      fetcher.mock.calls.filter(([, options]) => options.method === 'PATCH'),
    ).toHaveLength(2)
  })
  it('触发响应丢失后的重试保持同一事件编号', async () => {
    operator()
    let submitted = 0
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (url.endsWith('/runs') && options.method === 'POST') {
        submitted++
        return submitted === 1
          ? Promise.reject(new TypeError('network'))
          : Promise.resolve(json({ run_id: 'r' }))
      }
      return Promise.resolve(
        json(
          url.includes('/tasks?')
            ? {
                items: [
                  {
                    id: 't',
                    name: '任务',
                    default_parameters: { period: '2026-08' },
                    enabled: true,
                    operation: 'run',
                    timeout_seconds: 300,
                  },
                ],
                total: 1,
              }
            : { items: [], total: 0 },
        ),
      )
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: TasksView },
        { path: '/runs/:id', component: { template: '<p>运行详情</p>' } },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(TasksView, {
      attachTo: document.body,
      global: { plugins: [router] },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '立即运行')!
      .trigger('click')
    await flushPromises()
    const submit = () =>
      document
        .querySelector('form')!
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    submit()
    await flushPromises()
    expect(document.body.textContent).toContain('暂时无法连接工作台')
    submit()
    await flushPromises()
    const calls = fetcher.mock.calls
      .filter(
        ([url, options]) => url.endsWith('/runs') && options.method === 'POST',
      )
      .map(([, options]) => JSON.parse(String(options.body)))
    expect(calls).toHaveLength(2)
    expect(calls[0].event_id).toBe(calls[1].event_id)
    expect(calls[0].parameters).toEqual({ period: '2026-08' })
    expect(router.currentRoute.value.path).toBe('/runs/r')
  })
})
