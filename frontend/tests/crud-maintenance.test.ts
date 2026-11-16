import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TasksView from '../src/views/TasksView.vue'
import SchedulesView from '../src/views/SchedulesView.vue'
import { clearSession, session } from '../src/session'

const json = (value: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(value), { status })

function developer() {
  session.user = {
    id: 'u',
    username: 'developer',
    display_name: '开发者',
    is_admin: false,
    active: true,
  }
  session.projectId = 'p'
  session.projects = [{ id: 'p', name: '项目', role: 'developer' }]
}

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

describe('任务与计划配置维护', () => {
  it('任务可以编辑和受控删除，更新请求不修改所属项目', async () => {
    developer()
    const task = {
      id: 't',
      project_id: 'p',
      program_id: 'program',
      name: '旧任务名',
      version_id: 'version',
      default_parameters: { period: 'old' },
      node_id: null,
      timeout_seconds: 300,
      resource_keys: [],
      business_key_fields: ['period'],
      requires_input: false,
      operation: 'run',
      enabled: true,
      input_mode: 'none',
      config_hash: 'a'.repeat(64),
    }
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (options.method === 'PUT')
        return Promise.resolve(json({ ...task, name: '新任务名' }))
      if (options.method === 'DELETE') return Promise.resolve(json(null, 204))
      if (url.includes('/programs/program/versions'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 'version',
                program_id: 'program',
                name: 'v1',
                published: true,
                parameter_schema: {
                  type: 'object',
                  properties: { period: { type: 'string' } },
                },
              },
            ],
            total: 1,
          }),
        )
      if (url.includes('/programs?'))
        return Promise.resolve(
          json({ items: [{ id: 'program', name: '程序' }], total: 1 }),
        )
      if (url.includes('/nodes?'))
        return Promise.resolve(json({ items: [], total: 0 }))
      return Promise.resolve(json({ items: [task], total: 1 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/tasks', component: TasksView },
        { path: '/runs', component: { template: '<p>运行</p>' } },
      ],
    })
    await router.push('/tasks')
    const wrapper = mount(TasksView, {
      attachTo: document.body,
      global: { plugins: [router] },
    })
    await flushPromises()
    await wrapper.get('[aria-label="查询任务"]').setValue('旧任务')
    await flushPromises()
    expect(
      fetcher.mock.calls
        .map(([url]) => String(url))
        .some(
          (url) =>
            url.includes('/tasks?') &&
            url.includes('q=%E6%97%A7%E4%BB%BB%E5%8A%A1'),
        ),
    ).toBe(true)
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '编辑')!
      .trigger('click')
    await flushPromises()
    const editDialog = document.querySelector('[role=dialog]')!
    const name = editDialog.querySelector<HTMLInputElement>('input')!
    name.value = '新任务名'
    name.dispatchEvent(new Event('input', { bubbles: true }))
    editDialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const update = fetcher.mock.calls.find(
      ([, options]) => options.method === 'PUT',
    )!
    expect(update[0]).toBe('/api/v1/tasks/t')
    const updateBody = JSON.parse(String(update[1].body))
    expect(updateBody.name).toBe('新任务名')
    expect(updateBody).not.toHaveProperty('project_id')
    expect(updateBody.base_config_hash).toBe('a'.repeat(64))

    await wrapper
      .findAll('button')
      .find((button) => button.text() === '删除')!
      .trigger('click')
    await flushPromises()
    const deleteDialog = document.querySelector('[role=dialog]')!
    ;[...deleteDialog.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('确认删除'))!
      .click()
    await flushPromises()
    expect(
      fetcher.mock.calls.some(
        ([url, options]) =>
          url === '/api/v1/tasks/t' && options.method === 'DELETE',
      ),
    ).toBe(true)
  })

  it('计划页可以编辑和删除计划', async () => {
    developer()
    const schedule = {
      id: 's',
      project_id: 'p',
      task_id: 't',
      name: '旧计划',
      cron: '0 8 * * *',
      timezone: 'Asia/Shanghai',
      parameters: { period: 'old' },
      input_batch_id: null,
      input_label: null,
      misfire_policy: 'skip',
      enabled: false,
      next_fire: '2026-09-19T00:00:00Z',
      revision: 1,
    }
    const fetcher = vi.fn((url: string, options: RequestInit) => {
      if (options.method === 'PUT')
        return Promise.resolve(
          json({ ...schedule, name: '新计划', cron: '30 9 * * *' }),
        )
      if (options.method === 'DELETE') return Promise.resolve(json(null, 204))
      if (url.includes('/tasks?'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 't',
                name: '任务',
                project_id: 'p',
                input_mode: 'none',
                default_parameters: {},
              },
            ],
            total: 1,
          }),
        )
      if (url.includes('/runs?'))
        return Promise.resolve(json({ items: [], total: 0 }))
      return Promise.resolve(json({ items: [schedule], total: 1 }))
    })
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/schedules', component: SchedulesView },
        { path: '/tasks', component: { template: '<p>任务</p>' } },
        { path: '/runs/:id', component: { template: '<p>运行</p>' } },
      ],
    })
    await router.push('/schedules')
    const wrapper = mount(SchedulesView, {
      attachTo: document.body,
      global: { plugins: [router] },
    })
    await flushPromises()
    await wrapper.get('[aria-label="查询定时计划"]').setValue('旧计划')
    await flushPromises()
    expect(
      fetcher.mock.calls
        .map(([url]) => String(url))
        .some(
          (url) =>
            url.includes('/schedules?') &&
            url.includes('q=%E6%97%A7%E8%AE%A1%E5%88%92'),
        ),
    ).toBe(true)
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '编辑')!
      .trigger('click')
    await flushPromises()
    const editDialog = document.querySelector('[role=dialog]')!
    const inputs = editDialog.querySelectorAll<HTMLInputElement>('input')
    inputs[0]!.value = '新计划'
    inputs[0]!.dispatchEvent(new Event('input', { bubbles: true }))
    inputs[1]!.value = '30 9 * * *'
    inputs[1]!.dispatchEvent(new Event('input', { bubbles: true }))
    editDialog
      .querySelector('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await flushPromises()
    const update = fetcher.mock.calls.find(
      ([, options]) => options.method === 'PUT',
    )!
    expect(update[0]).toBe('/api/v1/schedules/s')
    expect(JSON.parse(String(update[1].body))).toMatchObject({
      base_revision: 1,
      name: '新计划',
      cron: '30 9 * * *',
    })

    await wrapper
      .findAll('button')
      .find((button) => button.text() === '删除')!
      .trigger('click')
    await flushPromises()
    const deleteDialog = document.querySelector('[role=dialog]')!
    ;[...deleteDialog.querySelectorAll('button')]
      .find((button) => button.textContent?.includes('确认删除'))!
      .click()
    await flushPromises()
    expect(
      fetcher.mock.calls.some(
        ([url, options]) =>
          url === '/api/v1/schedules/s' && options.method === 'DELETE',
      ),
    ).toBe(true)
  })
})
