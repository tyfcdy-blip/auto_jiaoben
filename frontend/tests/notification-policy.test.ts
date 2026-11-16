import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import NotificationSettingsView from '../src/views/NotificationSettingsView.vue'
import NotificationRecordsView from '../src/views/NotificationRecordsView.vue'
import { clearSession, session } from '../src/session'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status })

function administrator() {
  session.user = {
    id: 'u',
    username: 'admin',
    display_name: '管理员',
    is_admin: false,
    active: true,
  }
  session.projectId = 'p'
  session.projects = [{ id: 'p', name: '测试项目', role: 'project_admin' }]
}

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

describe('钉钉通知设置', () => {
  it('按连接选择异常类型，保存时提交读取到的修订版本', async () => {
    administrator()
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (url.endsWith('/channels/c/policy'))
        return Promise.resolve(
          json({
            channel_id: 'c',
            enabled_kinds: ['unknown', 'failed'],
            legacy_all: false,
            revision: options.method === 'PUT' ? 5 : 4,
          }),
        )
      if (url.includes('/channels?'))
        return Promise.resolve(
          json({
            items: [
              { id: 'c', project_id: 'p', name: '测试群', enabled: true },
            ],
            total: 1,
          }),
        )
      if (url.includes('/alerts?'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 'a',
                project_id: 'p',
                kind: 'unknown',
                severity: 'critical',
                message: '结果待核对',
                status: 'open',
                delivery_state: 'sent',
                created_at: '2026-09-19T00:00:00Z',
              },
            ],
            total: 1,
          }),
        )
      return Promise.resolve(json({ items: [], total: 0 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'rules' },
      attachTo: document.body,
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '选择异常类型')!
      .trigger('click')
    await flushPromises()
    expect(document.body.textContent).toContain('紧急')
    const dialog = document.querySelector('[role="dialog"]')!
    const failed = dialog.querySelector<HTMLInputElement>(
      'input[value="failed"]',
    )!
    failed.checked = false
    failed.dispatchEvent(new Event('change', { bubbles: true }))
    await flushPromises()
    const partial = dialog.querySelector<HTMLInputElement>(
      'input[value="partial"]',
    )!
    partial.checked = true
    partial.dispatchEvent(new Event('change', { bubbles: true }))
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const saved = fetcher.mock.calls.find(
      ([url, options]) =>
        url.endsWith('/channels/c/policy') && options.method === 'PUT',
    )!
    expect(JSON.parse(String(saved[1].body))).toEqual({
      enabled_kinds: ['unknown', 'partial'],
      base_revision: 4,
    })
    expect(fetcher.mock.calls.some(([url]) => url.includes('/alerts?'))).toBe(
      false,
    )
    expect(wrapper.text()).toContain('告警通知规则已保存')
  })

  it('所有项目下分别显示告警和结果投递，不出现跨项目配置入口', async () => {
    administrator()
    session.projectId = ''
    const fetcher = vi.fn((url: string) => {
      if (url.includes('/result-notifications?'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 'n',
                project_id: 'p',
                task_id: 't',
                run_id: 'r',
                channel_id: 'c',
                channel_name: '测试群',
                content: '结果：测试完成',
                delivery_state: 'sent',
                attempts: 1,
                last_error: null,
                created_at: '2026-09-19T00:00:00Z',
              },
            ],
            total: 1,
          }),
        )
      if (url.includes('/alert-deliveries?'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 'd',
                alert_id: 'a',
                project_id: 'p',
                channel_id: 'c',
                channel_name: '测试群',
                state: 'sent',
                attempts: 1,
                last_error: null,
                created_at: '2026-09-19T00:00:00Z',
              },
            ],
            total: 1,
          }),
        )
      return Promise.resolve(json({ items: [], total: 0 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(NotificationRecordsView, {
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('测试群')
    expect(wrapper.text()).toContain('任务 t')
    expect(wrapper.text()).toContain('测试项目')
    expect(wrapper.text()).not.toContain('选择异常类型')
    expect(fetcher.mock.calls.some(([url]) => url.includes('/channels?'))).toBe(
      false,
    )
    expect(
      fetcher.mock.calls
        .map(([url]) => url)
        .filter(
          (url) =>
            url.includes('/alert-deliveries?') ||
            url.includes('/result-notifications?'),
        )
        .every((url) => !url.includes('project_id=')),
    ).toBe(true)
  })

  it('通知规则遇到修订冲突时刷新表单，不覆盖他人的修改', async () => {
    administrator()
    let revision = 1
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (url.endsWith('/channels/c/policy')) {
        if (options.method === 'PUT') {
          revision = 2
          return Promise.resolve(
            json({ message: '通知规则已被其他人修改' }, 409),
          )
        }
        return Promise.resolve(
          json({
            channel_id: 'c',
            enabled_kinds: revision === 1 ? ['unknown'] : ['partial'],
            legacy_all: false,
            revision,
          }),
        )
      }
      if (url.includes('/channels?'))
        return Promise.resolve(
          json({
            items: [
              { id: 'c', project_id: 'p', name: '测试群', enabled: true },
            ],
            total: 1,
          }),
        )
      return Promise.resolve(json({ items: [], total: 0 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'rules' },
      attachTo: document.body,
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '选择异常类型')!
      .trigger('click')
    await flushPromises()
    const dialog = document.querySelector('[role="dialog"]')!
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(dialog.textContent).toContain('已刷新为最新配置')
    expect(
      dialog.querySelector<HTMLInputElement>('input[value="partial"]')?.checked,
    ).toBe(true)
    expect(
      dialog.querySelector<HTMLInputElement>('input[value="unknown"]')?.checked,
    ).toBe(false)
    expect(
      fetcher.mock.calls.filter(([, options]) => options.method === 'PUT'),
    ).toHaveLength(1)
  })

  it('任务成功结果通知只能由项目管理员选择同项目连接并保存', async () => {
    administrator()
    const task = {
      id: 't',
      project_id: 'p',
      name: '测试任务',
      version_id: 'v',
      default_parameters: {},
      node_id: null,
      timeout_seconds: 300,
      resource_keys: [],
      requires_input: false,
      operation: 'run',
      enabled: true,
      config_hash: 'a'.repeat(64),
    }
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (url.endsWith('/tasks/t/result-notification'))
        return Promise.resolve(
          json({
            task_id: 't',
            channel_id: options.method === 'PUT' ? 'c' : null,
            enabled: options.method === 'PUT',
            revision: options.method === 'PUT' ? 1 : 0,
          }),
        )
      if (url.includes('/channels?'))
        return Promise.resolve(
          json({
            items: [
              { id: 'c', project_id: 'p', name: '测试群', enabled: true },
            ],
            total: 1,
          }),
        )
      return Promise.resolve(json({ items: [task], total: 1 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(NotificationSettingsView, {
      props: { section: 'rules' },
      attachTo: document.body,
      global: { stubs: { RouterLink: true } },
    })
    await flushPromises()
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '配置接收群')!
      .trigger('click')
    await flushPromises()
    const dialog = document.querySelector('[role="dialog"]')!
    const enabled = dialog.querySelector<HTMLInputElement>(
      'input[type="checkbox"]',
    )!
    enabled.checked = true
    enabled.dispatchEvent(new Event('change', { bubbles: true }))
    const channel = dialog.querySelector<HTMLSelectElement>('select')!
    channel.value = 'c'
    channel.dispatchEvent(new Event('change', { bubbles: true }))
    dialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const saved = fetcher.mock.calls.find(
      ([url, options]) =>
        url.endsWith('/tasks/t/result-notification') &&
        options.method === 'PUT',
    )!
    expect(JSON.parse(String(saved[1].body))).toEqual({
      channel_id: 'c',
      enabled: true,
      base_revision: 0,
    })
    expect(wrapper.text()).toContain('任务运行通知规则已保存')
  })
})
