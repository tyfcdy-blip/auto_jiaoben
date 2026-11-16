import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoginView from '../src/components/LoginView.vue'
import StatusBadge from '../src/components/StatusBadge.vue'
import ProgramsView from '../src/views/ProgramsView.vue'
import { clearSession, session } from '../src/session'
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
afterEach(() => {
  vi.unstubAllGlobals()
  clearSession()
})
describe('v0.1 界面', () => {
  it('登录错误显示明确提示，不留下密码', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(json({ message: '账号或密码错误' }, 401)),
    )
    const wrapper = mount(LoginView)
    await wrapper.get('input[name=username]').setValue('operator')
    await wrapper.get('input[name=password]').setValue('wrong')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role=alert]').text()).toContain('账号或密码错误')
    expect(
      (wrapper.get('input[name=password]').element as HTMLInputElement).value,
    ).toBe('')
    expect(session.user).toBeNull()
  })
  it('展示未知结果而不是成功', () => {
    const wrapper = mount(StatusBadge, { props: { value: 'unknown' } })
    expect(wrapper.text()).toBe('结果待核对')
    expect(wrapper.classes()).toContain('danger')
  })
  it('查看者没有登记程序入口且空列表不伪造记录', async () => {
    session.user = {
      id: 'u',
      username: 'reader',
      display_name: '查看者',
      is_admin: false,
      active: true,
    }
    session.projects = [{ id: 'p', name: '测试项目', role: 'viewer' }]
    session.projectId = 'p'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(json({ items: [], total: 0 })),
    )
    const wrapper = mount(ProgramsView)
    await flushPromises()
    expect(wrapper.text()).toContain('把第一个程序交给工作台')
    expect(
      wrapper
        .findAll('button')
        .some((button) => button.text().includes('登记程序')),
    ).toBe(false)
    expect(wrapper.find('tbody').exists()).toBe(false)
  })
  it('程序描述按文本呈现，不能执行HTML', async () => {
    session.projectId = 'p'
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        json({
          items: [
            {
              id: 'a',
              name: '程序',
              description: '<img src=x onerror=alert(1)>',
            },
          ],
          total: 1,
        }),
      ),
    )
    const wrapper = mount(ProgramsView)
    await flushPromises()
    expect(wrapper.text()).toContain('<img src=x onerror=alert(1)>')
    expect(wrapper.find('img').exists()).toBe(false)
  })
})
