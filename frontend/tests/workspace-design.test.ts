import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, onMounted, onUnmounted } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createMemoryHistory } from 'vue-router'
import App from '../src/App.vue'
import { router as applicationRouter } from '../src/router'
import OverviewView from '../src/views/OverviewView.vue'
import { clearSession, session } from '../src/session'
import type { Role } from '../src/types'

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})
function member(role: Role) {
  session.user = {
    id: 'u',
    username: 'reader',
    display_name: '测试成员',
    is_admin: false,
    active: true,
  }
  session.projects = [{ id: 'p', name: '真实项目', role }]
  session.projectId = 'p'
}
async function router() {
  const instance = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/:pathMatch(.*)*',
        component: { template: '<div>业务页面</div>' },
      },
    ],
  })
  await instance.push('/')
  await instance.isReady()
  return instance
}
describe('明亮工作空间与真实总览', () => {
  it('总览保留真实数字、运行链接，并把完整编号降为可查询的次要信息', async () => {
    member('developer')
    const id = '16ae102e-0671-4e8d-8846-1e86e448a333'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            active_runs: 2,
            queued_runs: 3,
            failed_runs: 4,
            open_alerts: 5,
            online_nodes: 6,
            total_tasks: 7,
            recent_runs: [
              {
                id,
                task_name: '合并报表',
                state: 'running',
                created_at: '2026-09-17T01:00:00Z',
              },
            ],
          }),
        ),
      ),
    )
    const wrapper = mount(OverviewView, {
      global: { plugins: [await router()] },
    })
    await flushPromises()
    expect(
      wrapper.findAll('.metric strong').map((metric) => metric.text()),
    ).toEqual(['2', '3', '4', '5'])
    expect(wrapper.get('.capacity-strip').text()).toContain('6 个在线节点')
    expect(
      wrapper.findAll('.quick-action').map((link) => link.attributes('href')),
    ).toEqual(['/programs', '/tasks', '/runs?project_id=p'])
    expect(
      wrapper.findAll('.metric').map((link) => link.attributes('href')),
    ).toEqual([
      '/runs?project_id=p&state_group=active',
      '/runs?project_id=p&state_group=queued',
      '/runs?project_id=p&state_group=failed',
      '/alerts',
    ])
    expect(wrapper.get('.run-reference').text()).toBe('运行 16ae102e')
    expect(wrapper.get('.run-reference').attributes('title')).toBe(id)
    expect(wrapper.find(`a[href="/runs/${id}"]`).text()).toBe('合并报表')
  })
  it('接口失败时不伪造零值和运行记录，查看者只看到浏览入口', async () => {
    member('viewer')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ message: '暂时无法读取总览' }), {
          status: 503,
        }),
      ),
    )
    const wrapper = mount(OverviewView, {
      global: { plugins: [await router()] },
    })
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('暂时无法读取总览')
    expect(
      wrapper.findAll('.metric strong').map((metric) => metric.text()),
    ).toEqual(['暂无', '暂无', '暂无', '暂无'])
    expect(
      wrapper.findAll('.quick-action strong').map((label) => label.text()),
    ).toEqual(['查看程序', '查看任务', '查看运行'])
    expect(wrapper.find('tbody').exists()).toBe(false)
  })
  it('导航分组保留项目权限边界，项目切换与小屏收起导航均可用', async () => {
    member('viewer')
    session.projects.push({
      id: 'managed',
      name: '管理项目',
      role: 'project_admin',
    })
    const wrapper = mount(App, { global: { plugins: [await router()] } })
    expect(
      wrapper.findAll('.nav-group-label').map((group) => group.text()),
    ).toEqual(['日常工作', '交付与消息', '管理设置'])
    expect(
      wrapper
        .findAll('.nav-group')[1]
        .findAll('a')
        .map((link) => link.text()),
    ).toEqual(['输入文件', '告警中心', '消息记录'])
    expect(wrapper.find('a[href="/people"]').exists()).toBe(false)
    expect(wrapper.find('a[href="/schedules"]').text()).toBe('定时计划')
    const scheduleRoute = applicationRouter
      .getRoutes()
      .find((route) => route.path === '/schedules')
    expect(scheduleRoute?.redirect).toBeUndefined()
    expect(scheduleRoute?.components?.default).toBeTruthy()
    expect(wrapper.get('.environment-badge').text()).toBe('开发验证环境')
    await wrapper.get('#project-select').setValue('managed')
    expect(session.projectId).toBe('managed')
    expect(wrapper.find('a[href="/people"]').exists()).toBe(true)
    await wrapper.get('[aria-label="展开导航"]').trigger('click')
    expect(wrapper.get('.sidebar').classes()).toContain('open')
    await wrapper.get('a[href="/programs"]').trigger('click')
    expect(wrapper.get('.sidebar').classes()).not.toContain('open')
  })
  it('平台管理员切换项目时保持全局执行电脑页面，不销毁维护上下文', async () => {
    member('project_admin')
    session.user!.is_admin = true
    session.projects.push({
      id: 'other',
      name: '其他项目',
      role: 'project_admin',
    })
    let mounted = 0
    let unmounted = 0
    const nodePage = defineComponent({
      setup() {
        onMounted(() => mounted++)
        onUnmounted(() => unmounted++)
        return () => '全局电脑页'
      },
    })
    const instance = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/nodes', component: nodePage }],
    })
    await instance.push('/nodes')
    await instance.isReady()
    const wrapper = mount(App, { global: { plugins: [instance] } })
    expect(mounted).toBe(1)
    await wrapper.get('#project-select').setValue('other')
    await flushPromises()
    expect(mounted).toBe(1)
    expect(unmounted).toBe(0)
  })
  it('普通成员切换当前项目时也不会销毁全局运行中心', async () => {
    member('viewer')
    session.projects.push({ id: 'other', name: '其他项目', role: 'viewer' })
    let mounted = 0
    let unmounted = 0
    const runPage = defineComponent({
      setup() {
        onMounted(() => mounted++)
        onUnmounted(() => unmounted++)
        return () => '全局运行中心'
      },
    })
    const instance = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/runs', component: runPage }],
    })
    await instance.push('/runs')
    await instance.isReady()
    const wrapper = mount(App, { global: { plugins: [instance] } })
    expect(mounted).toBe(1)
    expect(wrapper.get('.topbar-scope strong').text()).toBe('所有可访问项目')
    await instance.push('/runs?project_id=p')
    await flushPromises()
    expect(wrapper.get('.topbar-scope strong').text()).toBe('真实项目')
    await wrapper.get('#project-select').setValue('other')
    await flushPromises()
    expect(mounted).toBe(1)
    expect(unmounted).toBe(0)
  })
})
