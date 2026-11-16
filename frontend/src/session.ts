import { computed, reactive } from 'vue'
import { request, setToken, loadChoices } from './api'
import type { Project, User } from './types'
export const session = reactive({
  user: null as User | null,
  projects: [] as Project[],
  projectId: '',
  notice: '',
})
export const currentProject = computed(() =>
  session.projects.find((project) => project.id === session.projectId),
)
export const canDevelop = computed(() =>
  Boolean(
    session.projectId &&
    (session.user?.is_admin ||
      ['project_admin', 'developer'].includes(
        currentProject.value?.role || '',
      )),
  ),
)
export const canOperate = computed(() =>
  Boolean(
    session.projectId &&
    (session.user?.is_admin ||
      ['project_admin', 'developer', 'operator'].includes(
        currentProject.value?.role || '',
      )),
  ),
)
export const canManage = computed(() =>
  Boolean(
    session.projectId &&
    (session.user?.is_admin || currentProject.value?.role === 'project_admin'),
  ),
)
export function projectName(projectId?: string) {
  return (
    session.projects.find((project) => project.id === projectId)?.name ||
    '未知项目'
  )
}
export async function refreshProjects() {
  const result = await loadChoices<Project>('/projects')
  session.projects = result.items
  if (
    (!session.user && !session.projectId) ||
    (session.projectId &&
      !result.items.some((project) => project.id === session.projectId))
  )
    session.projectId = result.items[0]?.id || ''
}
export async function login(username: string, password: string) {
  const result = await request<{ token: string; user: User }>('/auth/login', {
    method: 'POST',
    body: { username, password },
  })
  setToken(result.token)
  try {
    await refreshProjects()
    session.user = result.user
    session.notice = ''
  } catch (error) {
    clearSession()
    throw error
  }
}
export function clearSession() {
  setToken('')
  session.user = null
  session.projects = []
  session.projectId = ''
}
export async function logout() {
  try {
    await request('/auth/logout', { method: 'POST' })
  } finally {
    clearSession()
  }
}
