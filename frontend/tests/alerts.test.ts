import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, it, expect, vi } from 'vitest'
import AlertsView from '../src/views/AlertsView.vue'
import NotificationSettingsView from '../src/views/NotificationSettingsView.vue'
import { clearSession, session } from '../src/session'
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 })
afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})
describe('v0.4 告警与通知', () => {
  it('已恢复告警可由运行人员说明关闭，已关闭后不再展示操作', async () => {
    session.user = {
      id: 'u',
      username: 'operator',
      display_name: '运行人员',
      is_admin: false,
      active: true,
    }
    session.projectId = 'p'
    session.projects = [{ id: 'p', name: '项目', role: 'operator' }]
    let status = 'recovered'
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (options.method === 'POST') {
        status = 'closed'
        return Promise.resolve(json({ id: 'a', status }))
      }
      return Promise.resolve(
        json(
          url.includes('/alerts?')
            ? {
                items: [
                  {
                    id: 'a',
                    kind: 'node_offline',
                    message: '节点已恢复',
                    status,
                    delivery_state: 'sent',
                    created_at: '2026-09-16T10:00:00Z',
                  },
                ],
                total: 1,
              }
            : { items: [], total: 0 },
        ),
      )
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(AlertsView, {
      attachTo: document.body,
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('已恢复')
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '关闭告警')!
      .trigger('click')
    await flushPromises()
    const textarea = document.querySelector('textarea')!
    textarea.value = '已核对节点恢复及相关任务状态'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    document
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const submission = fetcher.mock.calls.find(
      ([, options]) => options.method === 'POST',
    )!
    expect(submission[0]).toBe('/api/v1/alerts/a/close')
    expect(JSON.parse(String(submission[1].body))).toEqual({
      note: '已核对节点恢复及相关任务状态',
    })
    expect(wrapper.text()).toContain('已关闭')
    expect(
      wrapper.findAll('button').some((button) => button.text() === '关闭告警'),
    ).toBe(false)
  })
  it('消息已发送不显示为故障已处理', async () => {
    session.projectId = 'p'
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          json(
            url.includes('/alerts?')
              ? {
                  items: [
                    {
                      id: 'a',
                      kind: 'run_failed',
                      message: '任务执行失败',
                      status: 'open',
                      delivery_state: 'sent',
                      created_at: '2026-09-16T10:00:00Z',
                    },
                  ],
                  total: 1,
                }
              : { items: [], total: 0 },
          ),
        ),
      ),
    )
    const wrapper = mount(AlertsView, {
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('待处理')
    expect(wrapper.text()).toContain('已发送')
    expect(wrapper.text()).not.toContain('已恢复')
  })
  it('恢复通知使用中文类型名称', async () => {
    session.projectId = 'p'
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          json(
            url.includes('/alerts?')
              ? {
                  items: [
                    {
                      id: 'recovery',
                      kind: 'recovery',
                      message: '定时计划已恢复',
                      status: 'recovered',
                      delivery_state: 'sent',
                      created_at: '2026-09-18T10:00:00Z',
                    },
                  ],
                  total: 1,
                }
              : { items: [], total: 0 },
          ),
        ),
      ),
    )
    const wrapper = mount(AlertsView, {
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('故障已恢复')
  })
  it('通知凭据只用于创建请求，关闭后不残留且不调用发送接口', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    session.projectId = 'p'
    const fetcher = vi.fn((_url: string, options: RequestInit) =>
      Promise.resolve(
        json(
          options.method === 'POST'
            ? { id: 'c', name: '测试连接', enabled: false }
            : { items: [], total: 0 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'channels' },
      attachTo: document.body,
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '新增连接')!
      .trigger('click')
    await flushPromises()
    const form = document.querySelector('form')!
    const fields = form.querySelectorAll('input')
    for (const [index, value] of [
      '测试连接',
      'https://example.invalid/robot',
      'test-secret',
    ].entries()) {
      fields[index]!.value = value
      fields[index]!.dispatchEvent(new Event('input', { bubbles: true }))
    }
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const posts = fetcher.mock.calls.filter(
      ([, options]) => options.method === 'POST',
    )
    expect(posts).toHaveLength(1)
    expect(posts[0]![0]).toBe('/api/v1/channels')
    expect(JSON.parse(String(posts[0]![1].body)).signing_secret).toBe(
      'test-secret',
    )
    expect(document.body.textContent).not.toContain('test-secret')
    expect(localStorage.length).toBe(0)
    expect(document.querySelector('input[type=password]')).toBeNull()
  })
  it('机器人连接切换失败时在连接页面显示错误', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    session.projectId = 'p'
    session.projects = [{ id: 'p', name: '测试项目', role: 'project_admin' }]
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, options: RequestInit) =>
        Promise.resolve(
          options.method === 'PATCH'
            ? new Response(JSON.stringify({ message: '连接状态更新失败' }), {
                status: 503,
              })
            : json(
                url.includes('/channels?')
                  ? {
                      items: [
                        {
                          id: 'c',
                          project_id: 'p',
                          name: '测试群',
                          enabled: false,
                        },
                      ],
                      total: 1,
                    }
                  : { items: [], total: 0 },
              ),
        ),
      ),
    )
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'channels' },
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '启用')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('连接状态更新失败')
    expect(wrapper.text()).toContain('未启用')
    expect(wrapper.text()).not.toContain('任务成功消息')
  })
  it('停用的机器人可手动发送一次测试消息，并显示钉钉接收结果', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    session.projectId = 'p'
    session.projects = [{ id: 'p', name: '隔离项目', role: 'project_admin' }]
    let finishRequest: ((response: Response) => void) | undefined
    const fetcher = vi.fn((url: string, options: RequestInit) =>
      options.method === 'POST' && url.endsWith('/test-message')
        ? new Promise<Response>((resolve) => {
            finishRequest = resolve
          })
        : Promise.resolve(
            json({
              items: [
                {
                  id: 'c',
                  project_id: 'p',
                  name: '隔离测试群',
                  enabled: false,
                },
              ],
              total: 1,
            }),
          ),
    )
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'channels' },
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    const testButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '发送测试消息')!
    await testButton.trigger('click')
    await testButton.trigger('click')
    expect(testButton.attributes('disabled')).toBeDefined()
    expect(
      fetcher.mock.calls.filter(([url]) => url.endsWith('/test-message')),
    ).toHaveLength(1)
    finishRequest!(json({ sent: true, test_id: 'probe-123' }))
    await flushPromises()
    expect(wrapper.text()).toContain('钉钉接口已接收测试消息')
    expect(wrapper.text()).toContain('probe-123')
    expect(wrapper.text()).toContain('未启用')
    session.projectId = ''
    await flushPromises()
    expect(wrapper.text()).not.toContain('probe-123')
  })
  it('钉钉拒绝测试消息时显示可操作的错误，且只读人员看不到发送入口', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    session.projectId = 'p'
    session.projects = [{ id: 'p', name: '隔离项目', role: 'project_admin' }]
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, options: RequestInit) =>
        Promise.resolve(
          options.method === 'POST' && url.endsWith('/test-message')
            ? new Response(
                JSON.stringify({
                  message: '钉钉拒绝测试消息（错误码 310000）',
                }),
                { status: 502 },
              )
            : json({
                items: [
                  {
                    id: 'c',
                    project_id: 'p',
                    name: '隔离测试群',
                    enabled: false,
                  },
                ],
                total: 1,
              }),
        ),
      ),
    )
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'channels' },
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '发送测试消息')!
      .trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('错误码 310000')
    expect(wrapper.text()).not.toContain('测试编号：')
    session.user = {
      id: 'v',
      username: 'viewer',
      display_name: '只读',
      is_admin: false,
      active: true,
    }
    session.projects = [{ id: 'p', name: '隔离项目', role: 'viewer' }]
    await flushPromises()
    expect(
      wrapper
        .findAll('button')
        .some((button) => button.text() === '发送测试消息'),
    ).toBe(false)
  })
  it('测试请求未结束就切换项目时不展示旧项目的结果', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    session.projectId = 'p'
    session.projects = [{ id: 'p', name: '隔离项目', role: 'project_admin' }]
    let finishRequest: ((response: Response) => void) | undefined
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, options: RequestInit) =>
        options.method === 'POST' && url.endsWith('/test-message')
          ? new Promise<Response>((resolve) => {
              finishRequest = resolve
            })
          : Promise.resolve(
              json({
                items: [
                  {
                    id: 'c',
                    project_id: 'p',
                    name: '隔离测试群',
                    enabled: false,
                  },
                ],
                total: 1,
              }),
            ),
      ),
    )
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'channels' },
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '发送测试消息')!
      .trigger('click')
    session.projectId = ''
    await flushPromises()
    finishRequest!(json({ sent: true, test_id: 'old-project-probe' }))
    await flushPromises()
    expect(wrapper.text()).not.toContain('old-project-probe')
    expect(wrapper.text()).not.toContain('钉钉接口已接收测试消息')
  })
})
