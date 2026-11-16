import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { afterEach, describe, it, expect, vi } from 'vitest'
import RunDetailView from '../src/views/RunDetailView.vue'
import SecretOnce from '../src/components/SecretOnce.vue'
import Modal from '../src/components/Modal.vue'
import { clearSession, session } from '../src/session'
import { useResource } from '../src/composables'
import type { Role } from '../src/types'
const json = (value: unknown) =>
  new Response(JSON.stringify(value), { status: 200 })
afterEach(() => {
  vi.unstubAllGlobals()
  clearSession()
})
describe('v0.2 执行观察', () => {
  it.each([
    {
      role: 'viewer',
      selectedRole: 'project_admin',
      admin: false,
      operate: false,
      reconcile: false,
    },
    {
      role: 'operator',
      selectedRole: 'viewer',
      admin: false,
      operate: true,
      reconcile: false,
    },
    {
      role: 'developer',
      selectedRole: 'viewer',
      admin: false,
      operate: true,
      reconcile: false,
    },
    {
      role: 'project_admin',
      selectedRole: 'viewer',
      admin: false,
      operate: true,
      reconcile: true,
    },
    {
      role: null,
      selectedRole: 'project_admin',
      admin: false,
      operate: false,
      reconcile: false,
    },
    {
      role: null,
      selectedRole: 'viewer',
      admin: true,
      operate: true,
      reconcile: true,
    },
  ])(
    '按运行项目角色 $role 与平台管理员 $admin 显示操作，不沿用选中项目 $selectedRole',
    async ({ role, selectedRole, admin, operate, reconcile }) => {
      session.user = {
        id: 'u',
        username: 'user',
        display_name: '测试人员',
        is_admin: admin,
        active: true,
      }
      session.projectId = 'selected-project'
      session.projects = [
        {
          id: 'selected-project',
          name: '导航项目',
          role: selectedRole as Role,
        },
      ]
      if (role)
        session.projects.push({
          id: 'run-project',
          name: '运行项目',
          role: role as Role,
        })
      let state = 'running'
      vi.stubGlobal(
        'fetch',
        vi.fn((url: string) =>
          Promise.resolve(
            json(
              url.endsWith('/logs')
                ? { entries: [], truncated: false }
                : {
                    id: 'r',
                    project_id: 'run-project',
                    task_name: '其他项目的运行',
                    state,
                    parameters: {},
                    created_at: '2026-09-16T10:00:00Z',
                    result: null,
                  },
            ),
          ),
        ),
      )
      const router = createRouter({
        history: createMemoryHistory(),
        routes: [{ path: '/runs/:id', component: RunDetailView }],
      })
      await router.push('/runs/r')
      await router.isReady()
      const wrapper = mount(RunDetailView, { global: { plugins: [router] } })
      await flushPromises()
      const hasButton = (label: string) =>
        wrapper.findAll('button').some((button) => button.text() === label)
      expect(hasButton('请求停止')).toBe(operate)
      expect(hasButton('记录核对结论')).toBe(false)
      state = 'unknown'
      await wrapper
        .findAll('button')
        .find((button) => button.text() === '刷新')!
        .trigger('click')
      await flushPromises()
      expect(hasButton('请求停止')).toBe(false)
      expect(hasButton('记录核对结论')).toBe(reconcile)
      wrapper.unmount()
    },
  )
  it('签发凭据期间不能通过关闭或Escape丢失一次性返回值', async () => {
    const wrapper = mount(Modal, {
      props: { title: '签发凭据', busy: true },
      attachTo: document.body,
    })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeUndefined()
    expect(
      (document.querySelector('[aria-label="关闭对话框"]') as HTMLButtonElement)
        .disabled,
    ).toBe(true)
    await wrapper.setProps({ busy: false })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
  it('日志HTML安全显示，未知结果要求核对而不是重跑', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    const fetcher = vi.fn((url: string) =>
      Promise.resolve(
        json(
          url.endsWith('/logs')
            ? {
                entries: [
                  {
                    sequence: 1,
                    stream: 'stderr',
                    text: '<script>alert(1)</script>',
                  },
                ],
                truncated: true,
              }
            : {
                id: 'r',
                task_name: '测试任务',
                state: 'unknown',
                parameters: {},
                created_at: '2026-09-16T10:00:00Z',
                artifact_state: 'incomplete',
                result: null,
              },
        ),
      ),
    )
    vi.stubGlobal('fetch', fetcher)
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/runs/:id', component: RunDetailView }],
    })
    await router.push('/runs/r')
    await router.isReady()
    const wrapper = mount(RunDetailView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('实际业务结果尚不确定')
    expect(wrapper.text()).toContain('<script>alert(1)</script>')
    expect(wrapper.find('script').exists()).toBe(false)
    expect(wrapper.text()).toContain('部分历史内容未在此展示')
    expect(wrapper.text()).not.toContain('立即重跑')
  })
  it('排队任务直接显示无法接单的具体原因', async () => {
    session.user = {
      id: 'u',
      username: 'admin',
      display_name: '管理员',
      is_admin: true,
      active: true,
    }
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          json(
            url.endsWith('/logs')
              ? { entries: [], truncated: false }
              : {
                  id: 'queued-run',
                  project_id: 'project',
                  task_name: '文件合并',
                  state: 'queued',
                  waiting_reason_code: 'config_pending',
                  waiting_reason:
                    '执行电脑“本机”执行器版本过旧，不能同步平台配置（电脑 v0，平台 v2）',
                  parameters: {},
                  created_at: '2026-09-18T08:00:00Z',
                  result: null,
                },
          ),
        ),
      ),
    )
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/runs/:id', component: RunDetailView }],
    })
    await router.push('/runs/queued-run')
    await router.isReady()
    const wrapper = mount(RunDetailView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('暂未开始')
    expect(wrapper.text()).toContain('电脑 v0，平台 v2')
  })
  it('一次显示凭据不写入浏览器持久存储', () => {
    const wrapper = mount(SecretOnce, {
      props: { identity: 'node1', token: 'temporary-node-token' },
    })
    expect(wrapper.text()).toContain('凭据仅在创建时显示一次')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    wrapper.unmount()
    expect(document.body.textContent).not.toContain('temporary-node-token')
  })
  it('晚到的旧请求不能覆盖新的资源', async () => {
    let resolveFirst!: (value: Response) => void
    const fetcher = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveFirst = resolve
          }),
      )
      .mockResolvedValueOnce(json({ name: '最新' }))
    vi.stubGlobal('fetch', fetcher)
    const component = defineComponent({
      props: { path: { type: String, required: true } },
      setup(props) {
        return useResource<{ name: string }>(() => props.path)
      },
      template: '<div>{{result?.name}}</div>',
    })
    const wrapper = mount(component, { props: { path: '/first' } })
    await wrapper.setProps({ path: '/second' })
    await flushPromises()
    expect(wrapper.text()).toBe('最新')
    resolveFirst(json({ name: '过期' }))
    await flushPromises()
    expect(wrapper.text()).toBe('最新')
  })
})
