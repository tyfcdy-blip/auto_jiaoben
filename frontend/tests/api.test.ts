import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  parseObject,
  parseStrings,
  request,
  setToken,
  apiEndpoint,
  loadChoices,
} from '../src/api'
afterEach(() => {
  vi.unstubAllGlobals()
  setToken('')
})
describe('请求与参数边界', () => {
  it('资源选项读取后续分页，不静默遗漏第一屏以外的任务', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [{ id: 'a' }, { id: 'b' }], total: 3 }),
          { status: 200 },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [{ id: 'c' }], total: 3 }), {
          status: 200,
        }),
      )
    vi.stubGlobal('fetch', fetcher)
    const response = await loadChoices<{ id: string }>('/tasks?project_id=p')
    expect(response.items.map((task) => task.id)).toEqual(['a', 'b', 'c'])
    expect(fetcher.mock.calls[1][0]).toContain('offset=2')
  })
  it('根路径与Docker子路径使用一致的API前缀', () => {
    expect(apiEndpoint('/tasks', '/')).toBe('/api/v1/tasks')
    expect(apiEndpoint('/tasks', '/workbench/')).toBe('/workbench/api/v1/tasks')
    expect(apiEndpoint('/tasks', '/workbench')).toBe('/workbench/api/v1/tasks')
  })
  it('身份仅通过请求头传递，不写入浏览器存储', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ items: [] }), { status: 200 }),
      )
    vi.stubGlobal('fetch', fetcher)
    setToken('test-session')
    await request('/tasks')
    expect(
      new Headers(fetcher.mock.calls[0][1].headers).get('Authorization'),
    ).toBe('Bearer test-session')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
  it('拒绝数组和空值作为业务参数', () => {
    expect(() => parseObject('[]')).toThrow('JSON 对象')
    expect(() => parseObject('null')).toThrow('JSON 对象')
    expect(() => parseObject('{bad')).toThrow('有效的 JSON')
    expect(parseObject('{"period":"2026-08"}')).toEqual({ period: '2026-08' })
  })
  it('命令数组拒绝非字符串与空项', () => {
    expect(() => parseStrings('["python",""]', '启动入口')).toThrow()
    expect(() => parseStrings('[4]', '启动入口')).toThrow()
    expect(parseStrings('["python","main.py"]', '启动入口')).toEqual([
      'python',
      'main.py',
    ])
  })
  it('权限失败保留状态和追踪编号', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: 'forbidden',
            message: '无权访问',
            request_id: 'r1',
          }),
          { status: 403 },
        ),
      ),
    )
    await expect(request('/tasks')).rejects.toMatchObject({
      status: 403,
      requestId: 'r1',
      message: '无权访问',
    })
  })
  it('会话失效通知宿主清除身份', async () => {
    const listener = vi.fn()
    window.addEventListener('workbench-session-expired', listener)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('{}', { status: 401 })),
    )
    await expect(request('/tasks')).rejects.toBeInstanceOf(ApiError)
    expect(listener).toHaveBeenCalledOnce()
    window.removeEventListener('workbench-session-expired', listener)
  })
})
