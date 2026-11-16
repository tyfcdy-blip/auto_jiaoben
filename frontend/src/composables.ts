import { onScopeDispose, ref, shallowRef, watch } from 'vue'
import { errorMessage, request, loadChoices } from './api'
import { session } from './session'
import type { Page } from './types'
export function projectPath(path: string, offset = 0) {
  const projectQuery = session.projectId
    ? `project_id=${encodeURIComponent(session.projectId)}&`
    : ''
  return `${path}?${projectQuery}limit=50&offset=${offset}`
}
export function useResource<T>(
  path: () => string | null,
  loader?: (endpoint: string, signal: AbortSignal) => Promise<T>,
) {
  const result = shallowRef<T | null>(null),
    loading = ref(false),
    error = ref('')
  let controller: AbortController | undefined
  let generation = 0
  async function refresh() {
    const current = ++generation
    controller?.abort()
    const endpoint = path()
    if (!endpoint) {
      result.value = null
      error.value = ''
      loading.value = false
      return
    }
    controller = new AbortController()
    loading.value = true
    error.value = ''
    try {
      const response = await (loader
        ? loader(endpoint, controller.signal)
        : request<T>(endpoint, { signal: controller.signal }))
      if (current === generation) result.value = response
    } catch (failure) {
      if (
        current === generation &&
        !(failure instanceof DOMException && failure.name === 'AbortError')
      )
        error.value = errorMessage(failure)
    } finally {
      if (current === generation) loading.value = false
    }
  }
  watch(
    path,
    () => {
      result.value = null
      void refresh()
    },
    { immediate: true },
  )
  onScopeDispose(() => {
    generation++
    controller?.abort()
  })
  return { result, loading, error, refresh }
}
export function useList<T>(path: string, globalForPlatformAdmin = false) {
  const offset = ref(0)
  watch(
    () => session.projectId,
    () => {
      if (!(globalForPlatformAdmin && session.user?.is_admin)) offset.value = 0
    },
  )
  return {
    ...useResource<Page<T>>(() => {
      if (!session.projectId && !session.projects.length) return null
      if (globalForPlatformAdmin && session.user?.is_admin)
        return `${path}?limit=50&offset=${offset.value}`
      return projectPath(path, offset.value)
    }),
    offset,
  }
}
export function useChoices<T>(path: string) {
  return useResource<Page<T>>(
    () =>
      session.projectId || session.projects.length ? projectPath(path) : null,
    loadChoices<T>,
  )
}
export function useAction() {
  const pending = ref(false),
    error = ref(''),
    success = ref('')
  async function perform(action: () => Promise<void>, message = '操作已完成') {
    if (pending.value) return
    pending.value = true
    error.value = ''
    success.value = ''
    try {
      await action()
      success.value = message
    } catch (failure) {
      error.value = errorMessage(failure)
    } finally {
      pending.value = false
    }
  }
  return { pending, error, success, perform }
}
export function formatTime(value?: string | null) {
  return value
    ? new Intl.DateTimeFormat('zh-CN', {
        timeZone: 'Asia/Shanghai',
        dateStyle: 'short',
        timeStyle: 'medium',
      }).format(new Date(value))
    : '暂无'
}
