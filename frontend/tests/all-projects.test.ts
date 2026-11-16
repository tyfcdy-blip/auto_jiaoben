import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from '../src/App.vue'
import OverviewView from '../src/views/OverviewView.vue'
import TasksView from '../src/views/TasksView.vue'
import { canDevelop, clearSession, session } from '../src/session'

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

function setProjects() {
  session.user = {
    id: 'admin',
    username: 'admin',
    display_name: '管理员',
    is_admin: true,
    active: true,
  }
  session.projects = [
    { id: 'p1', name: '项目一', role: 'project_admin' },
    { id: 'p2', name: '项目二', role: 'project_admin' },
  ]
  session.projectId = 'p1'
}

async function router(path = '/') {
  const instance = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div>总览页</div>' } },
      { path: '/tasks', component: { template: '<div>任务页</div>' } },
      { path: '/people', component: { template: '<div>成员页</div>' } },
      { path: '/audit', component: { template: '<div>审计页</div>' } },
    ],
  })
  await instance.push(path)
  await instance.isReady()
  return instance
}

describe('所有项目汇总查看', () => {
  it('选择所有项目后保留导航与汇总页，写权限仍需具体项目', async () => {
    setProjects()
    const instance = await router()
    const wrapper = mount(App, { global: { plugins: [instance] } })
    await wrapper.get('#project-select').setValue('')
    await flushPromises()
    expect(wrapper.get('.topbar-scope strong').text()).toBe('所有可访问项目')
    expect(wrapper.get('.scope-notice').text()).toContain('选择具体项目')
    expect(wrapper.text()).toContain('总览页')
    expect(canDevelop.value).toBe(false)
    await instance.push('/people')
    await flushPromises()
    expect(wrapper.text()).toContain('这项管理操作按项目授权')
    expect(wrapper.text()).not.toContain('成员页')
  })

  it('总览的所有项目指标链接不带项目过滤条件', async () => {
    setProjects()
    session.projectId = ''
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          active_runs: 1,
          queued_runs: 2,
          failed_runs: 3,
          open_alerts: 4,
          online_nodes: 5,
          total_tasks: 6,
          recent_runs: [],
        }),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(OverviewView, {
      global: { plugins: [await router()] },
    })
    await flushPromises()
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/overview')
    expect(
      wrapper.findAll('.metric').map((card) => card.attributes('href')),
    ).toEqual([
      '/runs?state_group=active',
      '/runs?state_group=queued',
      '/runs?state_group=failed',
      '/alerts',
    ])
  })

  it('非平台管理员不能从所有项目视图进入项目审计', async () => {
    setProjects()
    session.user!.is_admin = false
    session.projectId = ''
    const wrapper = mount(App, {
      global: { plugins: [await router('/audit')] },
    })
    expect(wrapper.text()).toContain('这项管理操作按项目授权')
    expect(wrapper.text()).not.toContain('审计页')
  })

  it('任务跨项目列出归属，只能进入项目后维护', async () => {
    setProjects()
    session.projectId = ''
    const fetcher = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          items: [
            {
              id: 'task-2',
              project_id: 'p2',
              name: '合并报表',
              enabled: true,
              operation: 'run',
              timeout_seconds: 300,
              requires_input: false,
            },
          ],
          total: 1,
        }),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const wrapper = mount(TasksView, {
      global: { plugins: [await router('/tasks')] },
    })
    await flushPromises()
    expect(fetcher.mock.calls[0][0]).toContain('/tasks?limit=50&offset=0')
    expect(fetcher.mock.calls[0][0]).not.toContain('project_id=')
    expect(wrapper.text()).toContain('项目二')
    expect(
      wrapper
        .findAll('button')
        .some((button) => button.text().includes('创建任务')),
    ).toBe(false)
    await wrapper
      .findAll('button')
      .find((button) => button.text() === '进入项目')!
      .trigger('click')
    await flushPromises()
    expect(session.projectId).toBe('p2')
    expect(canDevelop.value).toBe(true)
  })
})
