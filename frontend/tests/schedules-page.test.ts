import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SchedulesView from '../src/views/SchedulesView.vue'
import { clearSession, session } from '../src/session'

const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 })

function operator() {
  session.user = {
    id: 'u',
    username: 'operator',
    display_name: '运行人员',
    is_admin: false,
    active: true,
  }
  session.projectId = 'p'
  session.projects = [{ id: 'p', name: '测试项目', role: 'operator' }]
}

afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

describe('定时计划页', () => {
  it('展示计划状态，并按计划编号读取该计划的运行记录', async () => {
    operator()
    const fetcher = vi.fn((url: string) => {
      if (url.includes('/tasks?'))
        return Promise.resolve(
          json({ items: [{ id: 't', name: '表格合并' }], total: 1 }),
        )
      if (url.includes('/runs?'))
        return Promise.resolve(
          json({
            items: [
              {
                id: 'r',
                project_id: 'p',
                task_id: 't',
                task_name: '表格合并',
                source: 'schedule',
                original_source: 'manual',
                schedule_deduplicated: true,
                schedule_triggered_at: '2026-09-18T11:25:02Z',
                state: 'succeeded',
                parameters: {},
                result: {},
                created_at: '2026-09-18T09:57:00Z',
                finished_at: '2026-09-18T09:57:10Z',
              },
            ],
            total: 1,
          }),
        )
      return Promise.resolve(
        json({
          items: [
            {
              id: 's',
              project_id: 'p',
              task_id: 't',
              name: '每日合并',
              cron: '57 17 * * *',
              timezone: 'Asia/Shanghai',
              parameters: {},
              input_batch_id: null,
              misfire_policy: 'skip',
              enabled: true,
              next_fire: '2026-09-19T09:57:00Z',
              last_error: '错过计划时间，已按“跳过”策略顺延至下一次',
            },
          ],
          total: 1,
        }),
      )
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
    const wrapper = mount(SchedulesView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('每日合并')
    expect(wrapper.text()).toContain('表格合并')
    expect(wrapper.text()).toContain('错过计划时间')
    await wrapper.get('button').trigger('click')
    await flushPromises()
    const runUrl = fetcher.mock.calls
      .map(([url]) => String(url))
      .find((url) => url.includes('/runs?'))!
    expect(runUrl).toContain('schedule_id=s')
    expect(runUrl).toContain('source=schedule')
    expect(wrapper.text()).toContain('r')
    expect(wrapper.text()).toContain('已关联到此前手动运行')
    expect(wrapper.find('a[href="/runs/r"]').exists()).toBe(true)
  })
})
