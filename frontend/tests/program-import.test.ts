import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ProgramImport from '../src/components/ProgramImport.vue'
import { ApiError, request } from '../src/api'
import { clearSession, session } from '../src/session'
vi.mock('../src/api', async (original) => ({
  ...(await original<typeof import('../src/api')>()),
  request: vi.fn(),
}))
const api = vi.mocked(request)
const program = {
  id: 'p',
  project_id: 'project',
  name: '账单合并',
  description: '',
}
const version = {
  id: 'v',
  program_id: 'p',
  name: 'v1',
  command: ['python', 'main.py'],
  parameter_schema: {
    type: 'object',
    properties: {},
    additionalProperties: false,
  },
  required_capabilities: [],
  side_effects: false,
  result_required: true,
  published: false,
  bundle_sha256: null,
}
const passScan = {
  decision: 'pass',
  coverage: 'full',
  summary: { errors: 0, high: 0, warnings: 0 },
  findings: [],
}
beforeEach(() => {
  api.mockReset()
  session.user = {
    id: 'u',
    username: 'test',
    display_name: '测试',
    active: true,
    is_admin: true,
  }
  session.projectId = 'project'
  session.projects = [{ id: 'project', name: '测试', role: 'developer' }]
})
afterEach(clearSession)
async function prepare(props = {}) {
  const wrapper = mount(ProgramImport, {
    props,
    global: { stubs: { Teleport: true } },
  })
  const field = wrapper.find('input[placeholder="例如：月度账单合并"]')
  if (field.exists()) await field.setValue('账单合并')
  const upload = wrapper.get('input[type=file]')
  Object.defineProperty(upload.element, 'files', {
    configurable: true,
    value: [new File(['synthetic'], 'bundle.zip', { type: 'application/zip' })],
  })
  await upload.trigger('change')
  return wrapper
}
function normal(path: string) {
  if (path.startsWith('/program-bundles/scan')) return passScan
  if (path === '/programs') return program
  if (path.endsWith('/versions')) return version
  if (path.includes('/bundle?')) return { ...version, bundle_sha256: 'hash' }
  return { ...version, bundle_sha256: 'hash', published: true }
}
describe('程序接入连续流程', () => {
  it.each(['broken-json', 'missing-id', 'version-missing-id'])(
    '创建响应不完整 %s 时不重复创建或继续上传',
    async (failure) => {
      api.mockImplementation(async (path) => {
        if (path.startsWith('/program-bundles/scan')) return passScan
        if (failure === 'broken-json') throw new SyntaxError('broken body')
        if (failure === 'missing-id') return {} as never
        return path === '/programs' ? program : ({} as never)
      })
      const wrapper = await prepare()
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      const count = api.mock.calls.length
      await wrapper.get('form').trigger('submit')
      await flushPromises()
      expect(api).toHaveBeenCalledTimes(count)
      expect(api.mock.calls.some((call) => call[0].endsWith('/bundle'))).toBe(
        false,
      )
      expect(wrapper.text()).toContain('核对已保存记录')
    },
  )
  it('一次提交依次保存程序、版本、程序包和发布', async () => {
    api.mockImplementation(async (path) => normal(path))
    const wrapper = await prepare()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.mock.calls.map((call) => call[0])).toEqual([
      '/program-bundles/scan?project_id=project&runtime=python&entry=main.py',
      '/programs',
      '/programs/p/versions',
      '/versions/v/bundle?accept_review=false',
      '/versions/v/publish?accept_review=false',
    ])
    expect(api.mock.calls[2]![1]?.body).toMatchObject({
      command: ['python', 'main.py'],
      required_capabilities: ['python'],
      result_required: true,
      side_effects: false,
    })
    expect(wrapper.emitted('saved')?.[0]).toEqual([program])
  })
  it('上传失败后沿用已有程序和版本，成功才发布', async () => {
    let uploads = 0
    api.mockImplementation(async (path) => {
      if (path.includes('/bundle?') && uploads++ === 0)
        throw new ApiError(0, '上传中断')
      return normal(path)
    })
    const wrapper = await prepare()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('上传中断')
    expect(api.mock.calls.some((call) => call[0].includes('/publish?'))).toBe(
      false,
    )
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(
      api.mock.calls.filter((call) => call[0] === '/programs'),
    ).toHaveLength(1)
    expect(
      api.mock.calls.filter((call) => call[0].endsWith('/versions')),
    ).toHaveLength(1)
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })
  it('发布失败保留程序包，重试不重新上传', async () => {
    let publishes = 0
    api.mockImplementation(async (path) => {
      if (path.includes('/publish?') && publishes++ === 0)
        throw new ApiError(409, '发布尚未通过')
      return normal(path)
    })
    const wrapper = await prepare()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('发布尚未通过')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(
      api.mock.calls.filter((call) => call[0].includes('/bundle?')),
    ).toHaveLength(1)
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })
  it('开发者只能接入草稿，不调用发布接口', async () => {
    session.user!.is_admin = false
    api.mockImplementation(async (path) => normal(path))
    const wrapper = await prepare()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.mock.calls.some((call) => call[0].includes('/publish?'))).toBe(
      false,
    )
    expect(wrapper.text()).toContain('由项目管理员发布')
  })
  it('创建响应丢失不能重复登记，提示核对真实列表', async () => {
    api.mockImplementation(async (path) => {
      if (path.startsWith('/program-bundles/scan')) return passScan
      throw new ApiError(0, '网络中断')
    })
    const wrapper = await prepare()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('核对已保存记录')
    expect(wrapper.emitted('saved')).toBeUndefined()
  })
  it('本地参数格式无效时不创建半成品记录', async () => {
    const wrapper = await prepare()
    await wrapper.get('textarea.mono').setValue('{bad}')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('JSON')
  })
  it('重复提交只创建一次，项目切换阻止后续上传及发布', async () => {
    let resolve!: (value: unknown) => void
    api.mockImplementation((path) => {
      if (path.startsWith('/program-bundles/scan'))
        return Promise.resolve(passScan)
      return new Promise((done) => {
        resolve = done
      })
    })
    const wrapper = await prepare()
    await wrapper.get('form').trigger('submit')
    await wrapper.get('form').trigger('submit')
    expect(api).toHaveBeenCalledTimes(2)
    session.projectId = 'other'
    await flushPromises()
    resolve(program)
    await flushPromises()
    expect(api).toHaveBeenCalledTimes(2)
    expect(wrapper.emitted('close')).toHaveLength(1)
    expect(wrapper.emitted('saved')).toBeUndefined()
  })
  it('已有草稿继续接入不会再次创建程序或版本', async () => {
    api.mockImplementation(async (path) => normal(path))
    const wrapper = await prepare({ program, draft: version })
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(api.mock.calls.map((call) => call[0])).toEqual([
      '/program-bundles/scan?project_id=project&runtime=python&entry=main.py',
      '/versions/v/bundle?accept_review=false',
      '/versions/v/publish?accept_review=false',
    ])
  })
})
