import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import RunsView from '../src/views/RunsView.vue'
import { clearSession, session } from '../src/session'

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 })

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

describe('全局运行中心', () => {
  it('默认查询所有授权项目并显示项目与触发方式，支持组合筛选', async () => {
    session.user = {
      id: 'u',
      username: 'viewer',
      display_name: '查看者',
      is_admin: false,
      active: true,
    }
    session.projectId = 'p1'
    session.projects = [
      { id: 'p1', name: '项目一', role: 'viewer' },
      { id: 'p2', name: '项目二', role: 'viewer' },
    ]
    const fetcher = vi.fn((url: string) =>
      Promise.resolve(
        json(
          url.includes('/tasks?')
            ? {
                items: [
                  { id: 't1', project_id: 'p1', name: '任务一' },
                  { id: 't2', project_id: 'p2', name: '任务二' },
                ],
                total: 2,
              }
            : {
                items: [
                  {
                    id: 'r',
                    project_id: 'p2',
                    project_name: '项目二',
                    task_id: 't2',
                    task_name: '任务二',
                    source: 'schedule',
                    state: 'succeeded',
                    parameters: {},
                    result: {},
                    created_at: '2026-09-18T08:00:00Z',
                  },
                ],
                total: 1,
              },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/runs', component: RunsView },
        { path: '/runs/:id', component: { template: '<p>详情</p>' } },
      ],
    })
    await router.push('/runs')
    const wrapper = mount(RunsView, { global: { plugins: [router] } })
    await flushPromises()
    const initialRunUrl = fetcher.mock.calls
      .map(([url]) => String(url))
      .find((url) => url.includes('/runs?'))!
    expect(initialRunUrl).not.toContain('project_id=')
    expect(wrapper.text()).toContain('项目二')
    expect(wrapper.text()).toContain('定时触发')

    await wrapper.get('[aria-label="按项目筛选"]').setValue('p2')
    await wrapper.get('[aria-label="按任务筛选"]').setValue('t2')
    await wrapper.get('[aria-label="按触发方式筛选"]').setValue('schedule')
    await wrapper.get('[aria-label="按状态筛选"]').setValue('succeeded')
    await flushPromises()
    const filtered = fetcher.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.includes('/runs?'))
      .at(-1)!
    expect(filtered).toContain('project_id=p2')
    expect(filtered).toContain('task_id=t2')
    expect(filtered).toContain('source=schedule')
    expect(filtered).toContain('state=succeeded')
    const states = wrapper
      .get<HTMLSelectElement>('[aria-label="按状态筛选"]')
      .findAll('option')
      .map((option) => option.attributes('value'))
    expect(states).toEqual(
      expect.arrayContaining([
        'group:active',
        'group:queued',
        'group:failed',
        'leased',
        'cancel_requested',
        'partial',
      ]),
    )
  })

  it('从总览状态组入口按相同口径筛选运行', async () => {
    session.user = {
      id: 'u',
      username: 'viewer',
      display_name: '查看者',
      is_admin: false,
      active: true,
    }
    session.projectId = 'p1'
    session.projects = [{ id: 'p1', name: '项目一', role: 'viewer' }]
    const fetcher = vi.fn((url: string) =>
      Promise.resolve(
        json(
          url.includes('/tasks?')
            ? { items: [], total: 0 }
            : { items: [], total: 0 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/runs', component: RunsView }],
    })
    await router.push('/runs?project_id=p1&state_group=failed')
    const wrapper = mount(RunsView, { global: { plugins: [router] } })
    await flushPromises()
    const runUrl = fetcher.mock.calls
      .map(([url]) => String(url))
      .find((url) => url.includes('/runs?'))!
    expect(runUrl).toContain('project_id=p1')
    expect(runUrl).toContain('state_group=failed')
    expect(
      wrapper.get<HTMLSelectElement>('[aria-label="按状态筛选"]').element.value,
    ).toBe('group:failed')
  })

  it('从任务运行记录入口带入项目和任务筛选', async () => {
    session.user = {
      id: 'u',
      username: 'viewer',
      display_name: '查看者',
      is_admin: false,
      active: true,
    }
    session.projectId = 'p1'
    session.projects = [{ id: 'p1', name: '项目一', role: 'viewer' }]
    const fetcher = vi.fn((url: string) =>
      Promise.resolve(
        json(
          url.includes('/tasks?')
            ? {
                items: [{ id: 't1', project_id: 'p1', name: '任务一' }],
                total: 1,
              }
            : { items: [], total: 0 },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/runs', component: RunsView }],
    })
    await router.push('/runs?project_id=p1&task_id=t1')
    const wrapper = mount(RunsView, { global: { plugins: [router] } })
    await flushPromises()
    const runUrl = fetcher.mock.calls
      .map(([url]) => String(url))
      .find((url) => url.includes('/runs?'))!
    expect(runUrl).toContain('project_id=p1')
    expect(runUrl).toContain('task_id=t1')
    await router.push('/runs')
    await flushPromises()
    expect(
      wrapper.get<HTMLSelectElement>('[aria-label="按项目筛选"]').element.value,
    ).toBe('')
    expect(
      wrapper.get<HTMLSelectElement>('[aria-label="按任务筛选"]').element.value,
    ).toBe('')
    const clearedUrl = fetcher.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.includes('/runs?'))
      .at(-1)!
    expect(clearedUrl).not.toContain('project_id=')
    expect(clearedUrl).not.toContain('task_id=')
    router.back()
    await flushPromises()
    expect(
      wrapper.get<HTMLSelectElement>('[aria-label="按项目筛选"]').element.value,
    ).toBe('p1')
    expect(
      wrapper.get<HTMLSelectElement>('[aria-label="按任务筛选"]').element.value,
    ).toBe('t1')
  })
})
