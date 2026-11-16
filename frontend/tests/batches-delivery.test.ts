import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import BatchesView from '../src/views/BatchesView.vue'
import Pagination from '../src/components/Pagination.vue'
import { clearSession, session } from '../src/session'
import type { Batch } from '../src/types'

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status })
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
function server(initial?: Batch) {
  const records = new Map<string, Batch>()
  if (initial) records.set(initial.id, structuredClone(initial))
  const state = {
    uploadFailure: '',
    loseUploadResponse: false,
    readyFailure: false,
    loseReadyResponse: false,
    detailFailure: false,
    createFailure: false,
  }
  const fetcher = vi.fn(async (url: string, options: RequestInit) => {
    const path = new URL(url, 'http://test.local').pathname.replace(
      '/api/v1',
      '',
    )
    if (path === '/batches' && options.method === 'GET')
      return json({
        items: [...records.values()].map(
          ({ files: _files, ...batch }) => batch,
        ),
        total: records.size,
      })
    if (path === '/batches' && options.method === 'POST') {
      if (state.createFailure) throw new Error('lost creation response')
      const input = JSON.parse(String(options.body))
      const batch = {
        id: `batch-${records.size + 1}`,
        label: input.label,
        ready: false,
        files: [],
      }
      records.set(batch.id, batch)
      return json(batch)
    }
    const id = path.split('/')[2]!,
      batch = records.get(id)!
    if (options.method === 'GET') {
      if (state.detailFailure) return json({ message: '无法读取文件清单' }, 503)
      return json(batch)
    }
    if (options.method === 'PUT') {
      const name = decodeURIComponent(path.split('/').at(-1)!)
      if (state.uploadFailure === name)
        return json({ message: `${name} 上传失败` }, 503)
      const uploaded = {
        name,
        size: (options.body as File).size,
        sha256: `hash-${name}`,
      }
      if (!batch.files!.some((file) => file.name === name))
        batch.files!.push(uploaded)
      if (state.loseUploadResponse) throw new Error('lost upload response')
      return json(uploaded)
    }
    if (path.endsWith('/ready')) {
      if (state.readyFailure) return json({ message: '暂时不能确认就绪' }, 503)
      batch.ready = true
      if (state.loseReadyResponse) throw new Error('lost ready response')
      return json(batch)
    }
    throw new Error(`Unexpected route: ${path}`)
  })
  vi.stubGlobal('fetch', fetcher)
  return { state, records, fetcher }
}
function button(text: string) {
  const result = Array.from(
    document.querySelectorAll<HTMLButtonElement>('button'),
  ).find((node) => node.textContent?.trim() === text)
  expect(result, `missing button ${text}`).toBeTruthy()
  return result!
}
function inputName(value: string) {
  const input = document.querySelector<HTMLInputElement>(
    '[aria-label="文件批次名称"]',
  )!
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
}
async function choose(...names: string[]) {
  const input = document.querySelector<HTMLInputElement>(
    '[aria-label="选择待交付文件"]',
  )!
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: names.map((name) => new File(['content'], name)),
  })
  input.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}
async function submit() {
  document
    .querySelector('form')!
    .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await flushPromises()
}
async function openNew() {
  button('＋ 提交文件').click()
  await flushPromises()
  inputName('8月账单')
}
const writes = (
  fetcher: ReturnType<typeof server>['fetcher'],
  method: string,
) => fetcher.mock.calls.filter(([, options]) => options.method === method)
const readyCalls = (fetcher: ReturnType<typeof server>['fetcher']) =>
  fetcher.mock.calls.filter(([url]) => url.endsWith('/ready'))
afterEach(() => {
  clearSession()
  vi.unstubAllGlobals()
})

describe('一次提交输入文件', () => {
  it.each(['invalid-json', 'missing-id', 'invalid-id'])(
    '创建201响应%s时保留结果不明并阻止第二次POST',
    async (failure) => {
      administrator()
      const fetcher = vi.fn((_url: string, options: RequestInit) => {
        if (options.method === 'POST')
          return Promise.resolve(
            failure === 'invalid-json'
              ? new Response('broken-json', { status: 201 })
              : json(
                  {
                    id: failure === 'invalid-id' ? '../invalid' : undefined,
                    label: '8月账单',
                    ready: false,
                  },
                  201,
                ),
          )
        return Promise.resolve(json({ items: [], total: 0 }))
      })
      vi.stubGlobal('fetch', fetcher)
      mount(BatchesView, { attachTo: document.body })
      await flushPromises()
      await openNew()
      await choose('a.xlsx')
      await submit()
      await submit()
      expect(
        fetcher.mock.calls.filter(([, options]) => options.method === 'POST'),
      ).toHaveLength(1)
      expect(
        fetcher.mock.calls.filter(([, options]) => options.method === 'PUT'),
      ).toHaveLength(0)
      expect(document.body.textContent).toContain('创建结果尚未确认')
      expect(document.querySelector('.feedback.success')).toBeNull()
    },
  )
  it('上传已被接收但响应丢失时重发同文件，不能重复建批次或提前就绪', async () => {
    administrator()
    const { state, fetcher, records } = server()
    state.loseUploadResponse = true
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    expect(records.get('batch-1')!.files).toHaveLength(1)
    expect(readyCalls(fetcher)).toHaveLength(0)
    expect(document.querySelector('.feedback.success')).toBeNull()
    state.loseUploadResponse = false
    await submit()
    expect(writes(fetcher, 'PUT')).toHaveLength(2)
    expect(records.get('batch-1')!.files).toHaveLength(1)
    expect(document.querySelector('.feedback.success')).not.toBeNull()
  })
  it('创建后列表刷新失败不改变已成功交付的结果或诱发重复创建', async () => {
    administrator()
    const backend = server()
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, options: RequestInit) => {
        if (url.includes('/batches?') && backend.records.size)
          return Promise.resolve(json({ message: '列表刷新失败' }, 503))
        return backend.fetcher(url, options)
      }),
    )
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    await submit()
    expect(document.querySelector('.feedback.success')).not.toBeNull()
    expect(backend.records.get('batch-1')!.ready).toBe(true)
    expect(
      backend.fetcher.mock.calls.filter(
        ([url, options]) =>
          url === '/api/v1/batches' && options.method === 'POST',
      ),
    ).toHaveLength(1)
    expect(document.body.textContent).toContain('列表刷新失败')
  })
  it('旧批次迟到上传后不插入另一页或虚增总数', async () => {
    administrator()
    const backend = server({
      id: 'first',
      label: '第一页批次',
      ready: false,
      files: [],
    })
    let release!: (response: Response) => void
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, options: RequestInit) => {
        if (url.includes('/batches?'))
          return Promise.resolve(
            json({
              items: url.includes('offset=50')
                ? [{ id: 'second', label: '第二页批次', ready: false }]
                : [{ id: 'first', label: '第一页批次', ready: false }],
              total: 51,
            }),
          )
        if (options.method === 'PUT') return held
        return backend.fetcher(url, options)
      }),
    )
    const wrapper = mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    button('继续交付').click()
    await flushPromises()
    await choose('a.xlsx')
    await submit()
    expect(button('下一页').disabled).toBe(true)
    // 模拟已排入队列的分页变化，不依赖禁用按钮作为正确性的唯一保障。
    wrapper.findComponent(Pagination).vm.$emit('update:offset', 50)
    await flushPromises()
    release(json({ name: 'a.xlsx', size: 7, sha256: 'fake' }))
    await flushPromises()
    expect(wrapper.find('section.panel').text()).toContain('第二页批次')
    expect(wrapper.find('section.panel').text()).not.toContain('第一页批次')
    expect(wrapper.findComponent(Pagination).props('total')).toBe(51)
  })
  it('上传期间禁止重复提交和关闭；切换项目后旧上传不能继续确认或污染新页面', async () => {
    administrator()
    const backend = server()
    let release!: (response: Response) => void
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    const intercepted = vi.fn((url: string, options: RequestInit) =>
      options.method === 'PUT' ? held : backend.fetcher(url, options),
    )
    vi.stubGlobal('fetch', intercepted)
    const wrapper = mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    await submit()
    expect(
      intercepted.mock.calls.filter(([, options]) => options.method === 'PUT'),
    ).toHaveLength(1)
    expect(
      document.querySelector<HTMLButtonElement>('[aria-label="关闭对话框"]')!
        .disabled,
    ).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(document.querySelector('[role=dialog]')).not.toBeNull()
    backend.records.clear()
    session.projectId = 'other-project'
    await flushPromises()
    expect(document.querySelector('[role=dialog]')).toBeNull()
    const upload = intercepted.mock.calls.find(
      ([, options]) => options.method === 'PUT',
    )!
    expect(upload[1].signal!.aborted).toBe(true)
    // 故意让替身忽略 AbortSignal 并迟到返回，核对世代号仍阻断下一步。
    release(json({ name: 'a.xlsx', size: 7, sha256: 'fake' }))
    await flushPromises()
    expect(readyCalls(backend.fetcher)).toHaveLength(0)
    expect(wrapper.text()).not.toContain('8月账单')
    expect(document.querySelector('.feedback.success')).toBeNull()
  })
  it('卸载后迟到创建响应不能发起上传', async () => {
    administrator()
    const backend = server()
    let release!: (response: Response) => void
    const held = new Promise<Response>((resolve) => {
      release = resolve
    })
    const intercepted = vi.fn((url: string, options: RequestInit) =>
      options.method === 'POST' ? held : backend.fetcher(url, options),
    )
    vi.stubGlobal('fetch', intercepted)
    const wrapper = mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    wrapper.unmount()
    release(json({ id: 'late-batch', label: '8月账单', ready: false }))
    await flushPromises()
    expect(
      intercepted.mock.calls.filter(([, options]) => options.method === 'PUT'),
    ).toHaveLength(0)
    expect(document.querySelector('[role=dialog]')).toBeNull()
  })
  it('真实详情显示文件，已就绪批次只有查看入口且主表不突出 UUID', async () => {
    administrator()
    const { fetcher } = server({
      id: 'batch-secret-identifier',
      label: '已有账单',
      ready: true,
      files: [{ name: '已有.xlsx', size: 30 }],
    })
    const wrapper = mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    expect(wrapper.text()).not.toContain('batch-secret-identifier')
    button('查看详情').click()
    await flushPromises()
    expect(document.querySelector('[role=dialog]')!.textContent).toContain(
      '已有.xlsx',
    )
    expect(wrapper.text()).toContain('1 个文件')
    expect(document.querySelector('input[type=file]')).toBeNull()
    expect(writes(fetcher, 'POST')).toHaveLength(0)
    expect(
      fetcher.mock.calls.some(([url]) =>
        url.endsWith('/batches/batch-secret-identifier'),
      ),
    ).toBe(true)
  })
  it('一次提交依次创建、上传全部文件再就绪，显示最终文件数', async () => {
    administrator()
    const { fetcher, records } = server()
    const wrapper = mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx', 'b.xlsx')
    await submit()
    expect(writes(fetcher, 'POST').map(([url]) => url)).toEqual([
      '/api/v1/batches',
      '/api/v1/batches/batch-1/ready',
    ])
    expect(writes(fetcher, 'PUT')).toHaveLength(2)
    expect(records.get('batch-1')!.ready).toBe(true)
    expect(document.body.textContent).toContain(
      '文件已提交，可以用于执行任务。',
    )
    expect(wrapper.text()).toContain('2 个文件')
  })
  it('第二个文件失败不就绪；重试只补失败文件，沿用同一批次', async () => {
    administrator()
    const { state, fetcher } = server()
    state.uploadFailure = 'b.xlsx'
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx', 'b.xlsx')
    await submit()
    expect(readyCalls(fetcher)).toHaveLength(0)
    expect(document.body.textContent).toContain('上传未完成')
    expect(document.body.textContent).toContain('batch-1')
    expect(document.querySelector('.feedback.success')).toBeNull()
    state.uploadFailure = ''
    await submit()
    expect(
      fetcher.mock.calls.filter(
        ([url, options]) =>
          url === '/api/v1/batches' && options.method === 'POST',
      ),
    ).toHaveLength(1)
    expect(
      writes(fetcher, 'PUT').map(([url]) => url.split('/').at(-1)),
    ).toEqual(['a.xlsx', 'b.xlsx', 'b.xlsx'])
    expect(readyCalls(fetcher)).toHaveLength(1)
    expect(document.querySelector('.feedback.success')).not.toBeNull()
  })
  it('就绪响应丢失用详情确认成功，不额外创建或重传文件', async () => {
    administrator()
    const { state, fetcher } = server()
    state.loseReadyResponse = true
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    expect(document.querySelector('.feedback.success')).not.toBeNull()
    expect(readyCalls(fetcher)).toHaveLength(1)
    expect(writes(fetcher, 'PUT')).toHaveLength(1)
    const readyIndex = fetcher.mock.calls.findIndex(([url]) =>
      url.endsWith('/ready'),
    )
    expect(fetcher.mock.calls[readyIndex + 1]![0]).toBe(
      '/api/v1/batches/batch-1',
    )
  })
  it('就绪失败且详情未就绪绝不显示成功，重试只确认就绪', async () => {
    administrator()
    const { state, fetcher } = server()
    state.readyFailure = true
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    expect(document.querySelector('.feedback.success')).toBeNull()
    expect(document.body.textContent).toContain('暂时不能确认就绪')
    state.readyFailure = false
    await submit()
    expect(writes(fetcher, 'PUT')).toHaveLength(1)
    expect(readyCalls(fetcher)).toHaveLength(2)
    expect(document.querySelector('.feedback.success')).not.toBeNull()
  })
  it('就绪与核对均失联时保留失败，稍后读取已就绪即可恢复', async () => {
    administrator()
    const { state, fetcher } = server()
    state.loseReadyResponse = true
    state.detailFailure = true
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    expect(document.querySelector('.feedback.success')).toBeNull()
    state.detailFailure = false
    await submit()
    expect(document.querySelector('.feedback.success')).not.toBeNull()
    expect(readyCalls(fetcher)).toHaveLength(1)
    expect(writes(fetcher, 'PUT')).toHaveLength(1)
  })
  it('重新打开未齐套批次可补文件并完成，原文件仍可见', async () => {
    administrator()
    const { fetcher, records } = server({
      id: 'existing',
      label: '未完成账单',
      ready: false,
      files: [{ name: 'old.xlsx', size: 1 }],
    })
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    button('继续交付').click()
    await flushPromises()
    expect(document.body.textContent).toContain('old.xlsx')
    await choose('new.xlsx')
    await submit()
    expect(records.get('existing')!.files).toHaveLength(2)
    expect(
      fetcher.mock.calls.some(
        ([url, options]) =>
          url === '/api/v1/batches' && options.method === 'POST',
      ),
    ).toBe(false)
    expect(readyCalls(fetcher)[0]![0]).toBe('/api/v1/batches/existing/ready')
  })
  it('旧批次文件详情加载失败不能猜测齐套或提交', async () => {
    administrator()
    const { state, fetcher } = server({
      id: 'existing',
      label: '未完成账单',
      ready: false,
    })
    state.detailFailure = true
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    button('继续交付').click()
    await flushPromises()
    expect(button('继续提交文件').disabled).toBe(true)
    expect(document.querySelector('input[type=file]')).toBeNull()
    await submit()
    expect(readyCalls(fetcher)).toHaveLength(0)
  })
  it('其他操作先冻结时拒绝补传，不把本次待上传文件说成成功', async () => {
    administrator()
    const { records, fetcher } = server({
      id: 'existing',
      label: '未完成账单',
      ready: false,
      files: [{ name: 'old.xlsx' }],
    })
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    button('继续交付').click()
    await flushPromises()
    await choose('new.xlsx')
    records.get('existing')!.ready = true
    await submit()
    expect(document.body.textContent).toContain('本次待上传文件未继续交付')
    expect(document.querySelector('.feedback.success')).toBeNull()
    expect(writes(fetcher, 'PUT')).toHaveLength(0)
  })
  it('创建响应不明时不自动重复创建，引导列表核对', async () => {
    administrator()
    const { state, fetcher } = server()
    state.createFailure = true
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx')
    await submit()
    await submit()
    expect(writes(fetcher, 'POST')).toHaveLength(1)
    expect(document.body.textContent).toContain('不会自动重复创建')
    expect(document.querySelector('.feedback.success')).toBeNull()
  })
  it('重复文件和空名称在创建前拦截', async () => {
    administrator()
    const { fetcher } = server()
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    await openNew()
    await choose('a.xlsx', 'a.xlsx')
    await submit()
    expect(writes(fetcher, 'POST')).toHaveLength(0)
    await choose('a.xlsx')
    inputName(' ')
    await submit()
    expect(writes(fetcher, 'POST')).toHaveLength(0)
  })
  it('读取权限用户只能查看，不能选择文件或提交', async () => {
    administrator()
    session.user!.is_admin = false
    session.projects[0]!.role = 'viewer'
    server({
      id: 'existing',
      label: '账单',
      ready: false,
      files: [{ name: 'old.xlsx' }],
    })
    mount(BatchesView, { attachTo: document.body })
    await flushPromises()
    button('查看详情').click()
    await flushPromises()
    expect(document.body.textContent).toContain('old.xlsx')
    expect(document.querySelector('input[type=file]')).toBeNull()
    expect(document.querySelector('button[type=submit]')).toBeNull()
  })
})
