import type { Page } from './types'
let sessionToken = ''
export function apiEndpoint(path: string, base = import.meta.env.BASE_URL) {
  return `${(base || '/').replace(/\/$/, '')}/api/v1${path}`
}
export function setToken(token: string) {
  sessionToken = token
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = '',
    public requestId = '',
  ) {
    super(message)
  }
}
async function authenticatedResponse(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<Response> {
  const headers = new Headers({ Accept: 'application/json' })
  if (sessionToken) headers.set('Authorization', `Bearer ${sessionToken}`)
  const raw = options.body instanceof Blob
  if (options.body !== undefined)
    headers.set(
      'Content-Type',
      raw
        ? (options.body as Blob).type || 'application/octet-stream'
        : 'application/json',
    )
  let response: Response
  try {
    response = await fetch(apiEndpoint(path), {
      method: options.method || 'GET',
      headers,
      body:
        options.body === undefined
          ? undefined
          : raw
            ? (options.body as Blob)
            : JSON.stringify(options.body),
      signal: options.signal,
      cache: 'no-store',
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError')
      throw error
    throw new ApiError(0, '暂时无法连接工作台，请检查网络后重试。')
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    if (response.status === 401 && path !== '/auth/login')
      window.dispatchEvent(new Event('workbench-session-expired'))
    const fallback =
      response.status === 403
        ? '你没有执行此操作的权限，请联系项目管理员。'
        : response.status === 401
          ? '登录状态已失效，请重新登录。'
          : '操作未完成，请稍后重试。'
    throw new ApiError(
      response.status,
      typeof payload.message === 'string' ? payload.message : fallback,
      payload.code,
      payload.request_id,
    )
  }
  return response
}
export async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const response = await authenticatedResponse(path, options)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}
export async function downloadArtifact(path: string, filename: string) {
  const response = await authenticatedResponse(path)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.hidden = true
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export async function loadChoices<T>(
  path: string,
  signal?: AbortSignal,
): Promise<Page<T>> {
  const endpoint = new URL(path, 'http://local.invalid')
  const items: T[] = []
  let total = 0
  do {
    endpoint.searchParams.set('limit', '200')
    endpoint.searchParams.set('offset', String(items.length))
    const page = await request<Page<T>>(endpoint.pathname + endpoint.search, {
      signal,
    })
    if (page.total > 10000)
      throw new Error('当前可选资源过多，请联系管理员缩小项目范围。')
    total = page.total
    items.push(...page.items)
    if (!page.items.length && items.length < total)
      throw new Error('资源列表在读取时发生变化，请刷新后重试。')
  } while (items.length < total)
  return { items, total: items.length }
}
export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '操作未完成，请稍后重试。'
}
export function parseObject(
  value: string,
  label = '参数',
): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error(`${label}必须是有效的 JSON。`)
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
    throw new Error(`${label}必须是 JSON 对象。`)
  return parsed as Record<string, unknown>
}
export function parseStrings(value: string, label: string): string[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error(`${label}必须是有效的 JSON 数组。`)
  }
  if (
    !Array.isArray(parsed) ||
    parsed.some((entry) => typeof entry !== 'string' || !entry.trim())
  )
    throw new Error(`${label}必须是非空字符串组成的数组。`)
  return parsed
}
