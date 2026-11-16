import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, it, expect, vi } from 'vitest'
import TasksView from '../src/views/TasksView.vue'
import CallersView from '../src/views/CallersView.vue'
import { clearSession, session } from '../src/session'
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 })
function administrator() {
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
function set(control: HTMLInputElement | HTMLSelectElement, value: string) {
  control.value = value
  control.dispatchEvent(
    new Event(control instanceof HTMLSelectElement ? 'change' : 'input', {
      bubbles: true,
    }),
  )
}
afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})
describe('输入交付契约', () => {
  it('手动运行可按参数定义填表并直接上传多个文件后触发', async () => {
    administrator()
    let releaseReady!: () => void
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (url.includes('/tasks?'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 't',
                project_id: 'p',
                name: '账单合并',
                enabled: true,
                default_parameters: { period: '2026-08' },
                node_id: null,
                requires_input: true,
                operation: 'run',
                timeout_seconds: 300,
                resource_keys: [],
                version_id: 'v',
                config_hash: 'h',
                parameter_schema: {
                  type: 'object',
                  required: ['period'],
                  additionalProperties: false,
                  properties: {
                    period: {
                      type: 'string',
                      title: '账期',
                      description: '处理月份',
                    },
                  },
                },
              },
            ],
            total: 1,
          }),
        )
      if (url.includes('/batches?'))
        return Promise.resolve(json({ items: [], total: 0 }))
      if (url.endsWith('/batches') && options.method === 'POST')
        return Promise.resolve(
          json({
            id: 'batch-new',
            project_id: 'p',
            label: '本次账单',
            ready: false,
            files: [],
          }),
        )
      if (url.includes('/files/'))
        return Promise.resolve(
          json({
            id: 'file-1',
            name: decodeURIComponent(url.split('/').pop()!),
            size: 4,
            sha256: 'hash',
          }),
        )
      if (url.endsWith('/ready'))
        return new Promise<Response>((resolve) => {
          releaseReady = () =>
            resolve(json({ id: 'batch-new', label: '本次账单', ready: true }))
        })
      if (url.endsWith('/tasks/t/runs'))
        return Promise.resolve(json({ run_id: 'run-new' }))
      return Promise.resolve(json({ items: [], total: 0 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: TasksView },
        { path: '/runs/:id', component: { template: '<p />' } },
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
      .find((button) => button.text() === '立即运行')!
      .trigger('click')
    await flushPromises()
    set(document.querySelector('[aria-label="本次输入方式"]')!, 'upload')
    await flushPromises()
    set(
      document.querySelector<HTMLInputElement>(
        'input[placeholder="例如：2026年9月账单"]',
      )!,
      '本次账单',
    )
    set(
      document.querySelector<HTMLInputElement>('[aria-label="账期"]')!,
      '2026-09',
    )
    const upload = document.querySelector<HTMLInputElement>(
      '[aria-label="直接上传运行文件"]',
    )!
    Object.defineProperty(upload, 'files', {
      configurable: true,
      value: [new File(['one'], 'a.xlsx'), new File(['two'], 'b.xlsx')],
    })
    upload.dispatchEvent(new Event('change', { bubbles: true }))
    document
      .querySelector<HTMLFormElement>('.modal form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    expect(
      document.querySelector<HTMLFieldSetElement>('.run-fields')!.disabled,
    ).toBe(true)
    // 即使脚本在上传等待期间强制派发 change/input，触发请求也必须使用点击确认时的快照。
    set(
      document.querySelector<HTMLSelectElement>('[aria-label="本次输入方式"]')!,
      'none',
    )
    set(
      document.querySelector<HTMLInputElement>('[aria-label="账期"]')!,
      'changed-after-submit',
    )
    releaseReady()
    await flushPromises()
    const trigger = fetcher.mock.calls.find(([url]) =>
      url.endsWith('/tasks/t/runs'),
    )!
    expect(JSON.parse(String(trigger[1].body))).toMatchObject({
      input_batch_id: 'batch-new',
      parameters: { period: '2026-09' },
    })
    expect(
      fetcher.mock.calls.filter(([url]) => url.includes('/files/')),
    ).toHaveLength(2)
    expect(router.currentRoute.value.path).toBe('/runs/run-new')
  })

  it('计划按标签等待输入时清除固定批次，日期模板留给后端解析', async () => {
    administrator()
    const fetcher = vi.fn((url: string, options: RequestInit) =>
      Promise.resolve(
        json(
          options.method === 'POST'
            ? { id: 's' }
            : url.includes('/tasks?')
              ? {
                  items: [
                    {
                      id: 't',
                      project_id: 'p',
                      name: '任务',
                      enabled: true,
                      default_parameters: {},
                      node_id: null,
                      requires_input: false,
                      operation: 'run',
                      timeout_seconds: 300,
                      resource_keys: [],
                      version_id: 'v',
                    },
                  ],
                  total: 1,
                }
              : url.includes('/batches?')
                ? {
                    items: [{ id: 'b', label: '旧批次', ready: true }],
                    total: 1,
                  }
                : { items: [], total: 0 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: TasksView },
        { path: '/runs', component: { template: '<p />' } },
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
    ;(
      Array.from(document.querySelectorAll('button')).find((button) =>
        button.textContent?.includes('添加定时计划'),
      ) as HTMLButtonElement
    ).click()
    await flushPromises()
    const form = document.querySelector('form')!
    set(form.querySelector('input')!, '等待账单')
    const mode = form.querySelector<HTMLSelectElement>(
      '[aria-label="定时输入来源"]',
    )!
    set(mode, 'fixed')
    await flushPromises()
    const fixed = Array.from(form.querySelectorAll('select')).find((select) =>
      Array.from(select.options).some((option) => option.value === 'b'),
    )!
    set(fixed, 'b')
    set(mode, 'label')
    await flushPromises()
    set(
      form.querySelector<HTMLInputElement>(
        'input[placeholder="${scheduled_date}"]',
      )!,
      '${previous_month}',
    )
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const submission = fetcher.mock.calls.find(
      ([url, options]) =>
        url.endsWith('/schedules') && options.method === 'POST',
    )!
    expect(JSON.parse(String(submission[1].body))).toMatchObject({
      input_label: '${previous_month}',
      input_batch_id: null,
      enabled: false,
    })
  })
  it('调用方上传权限默认关闭，明确勾选才提交授权', async () => {
    administrator()
    const fetcher = vi.fn((url: string, options: RequestInit) =>
      Promise.resolve(
        json(
          options.method === 'POST'
            ? { id: 'caller', token: 'test-only-token' }
            : url.includes('/tasks?')
              ? {
                  items: [
                    {
                      id: 't',
                      name: '任务',
                      requires_input: false,
                      parameter_schema: {
                        type: 'object',
                        properties: {
                          period: {
                            type: 'string',
                            title: '账期',
                          },
                        },
                      },
                    },
                  ],
                  total: 1,
                }
              : { items: [], total: 0 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(CallersView, { attachTo: document.body })
    await flushPromises()
    expect(wrapper.text()).toContain('接口地址由工作台统一提供')
    expect(wrapper.text()).toContain('period')
    expect(wrapper.text()).toContain('工作台文件批次')
    expect(wrapper.get('a[href$="/api/docs"]').attributes('target')).toBe(
      '_blank',
    )
    await wrapper
      .findAll('button')
      .find((button) => button.text().includes('创建调用方'))!
      .trigger('click')
    await flushPromises()
    const form = document.querySelector('form')!
    set(form.querySelector('input:not([type=checkbox])')!, '影刀测试')
    expect(form.textContent).toContain('整个项目的输入批次')
    expect(form.textContent).toContain('范围不受上方任务选择限制')
    const checkboxes = form.querySelectorAll<HTMLInputElement>(
      'input[type=checkbox]',
    )
    expect(checkboxes[1]!.checked).toBe(false)
    for (const checkbox of checkboxes) {
      checkbox.checked = true
      checkbox.dispatchEvent(new Event('change', { bubbles: true }))
    }
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const submission = fetcher.mock.calls.find(
      ([url, options]) => url.endsWith('/callers') && options.method === 'POST',
    )!
    expect(JSON.parse(String(submission[1].body))).toMatchObject({
      task_ids: ['t'],
      allow_input_upload: true,
    })
  })
})
