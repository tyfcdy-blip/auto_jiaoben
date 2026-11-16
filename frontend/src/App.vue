<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import LoginView from './components/LoginView.vue'
import Modal from './components/Modal.vue'
import Feedback from './components/Feedback.vue'
import EmptyState from './components/EmptyState.vue'
import WorkbenchIcon from './components/WorkbenchIcon.vue'
import {
  session,
  clearSession,
  logout,
  refreshProjects,
  canManage,
} from './session'
import { request } from './api'
import { useAction } from './composables'
import packageInfo from '../package.json'
const menuOpen = ref(false),
  projectModal = ref(false),
  projectName = ref('')
const route = useRoute()
const viewKey = computed(() =>
  route.path.startsWith('/runs')
    ? 'global-runs'
    : session.user?.is_admin && route.path === '/nodes'
      ? 'global-nodes'
      : session.projectId,
)
const { pending, error, perform } = useAction()
const navigation = computed(() => [
  {
    label: '日常工作',
    entries: [
      { path: '/', label: '工作台总览', icon: 'overview' },
      { path: '/programs', label: '程序库', icon: 'program' },
      { path: '/tasks', label: '任务管理', icon: 'task' },
      { path: '/schedules', label: '定时计划', icon: 'clock' },
      { path: '/runs', label: '运行中心', icon: 'run' },
    ],
  },
  {
    label: '交付与消息',
    entries: [
      { path: '/batches', label: '输入文件', icon: 'batch' },
      { path: '/alerts', label: '告警中心', icon: 'alert' },
      { path: '/notification-records', label: '消息记录', icon: 'batch' },
    ],
  },
  {
    label: '管理设置',
    entries: [
      { path: '/nodes', label: '执行电脑', icon: 'node' },
      { path: '/callers', label: '事件接入', icon: 'event' },
      { path: '/notification-channels', label: '机器人连接', icon: 'flow' },
      { path: '/notification-rules', label: '通知规则', icon: 'task' },
      ...(session.user?.is_admin || canManage.value
        ? [
            { path: '/people', label: '账号与权限', icon: 'people' },
            { path: '/audit', label: '操作审计', icon: 'audit' },
          ]
        : []),
    ],
  },
])
const currentPageName = computed(() => {
  if (route.path.startsWith('/runs/')) return '运行详情'
  return (
    navigation.value
      .flatMap((group) => group.entries)
      .find((entry) => entry.path === route.path)?.label || '工作台'
  )
})
const scopeName = computed(() => {
  if (route.path.startsWith('/runs/')) return '单次运行记录'
  if (route.path === '/runs') {
    const projectId =
      typeof route.query.project_id === 'string' ? route.query.project_id : ''
    return projectId
      ? session.projects.find((project) => project.id === projectId)?.name ||
          '已筛选项目'
      : '所有可访问项目'
  }
  if (session.user?.is_admin && route.path === '/nodes') return '全部执行电脑'
  if (!session.projectId && session.projects.length) return '所有可访问项目'
  return (
    session.projects.find((project) => project.id === session.projectId)
      ?.name || '未选择项目'
  )
})
function expire() {
  clearSession()
  session.notice = '登录状态已失效，请重新登录。'
}
onMounted(() => window.addEventListener('workbench-session-expired', expire))
onUnmounted(() =>
  window.removeEventListener('workbench-session-expired', expire),
)
async function createProject() {
  await perform(async () => {
    await request('/projects', {
      method: 'POST',
      body: { name: projectName.value.trim() },
    })
    await refreshProjects()
    projectModal.value = false
    projectName.value = ''
  })
}
</script>
<template>
  <LoginView v-if="!session.user" />
  <div v-else class="app-shell">
    <button
      v-if="menuOpen"
      class="nav-backdrop"
      aria-label="收起导航"
      @click="menuOpen = false"
    ></button>
    <aside class="sidebar" :class="{ open: menuOpen }">
      <RouterLink class="brand" to="/">
        <span class="brand-icon"><WorkbenchIcon name="flow" /></span>
        <span>
          自动化工作台
          <small>让自动化有序运行</small>
        </span>
      </RouterLink>
      <div class="project-switch">
        <label for="project-select">查看范围</label>
        <select id="project-select" v-model="session.projectId">
          <option v-if="!session.projects.length" value="">尚未加入项目</option>
          <option v-else value="">所有项目</option>
          <option
            v-for="project in session.projects"
            :key="project.id"
            :value="project.id"
          >
            {{ project.name }}
          </option>
        </select>
        <button
          v-if="session.user.is_admin"
          class="sidebar-link"
          @click="projectModal = true"
        >
          ＋ 创建项目
        </button>
      </div>
      <nav aria-label="主导航">
        <div v-for="group in navigation" :key="group.label" class="nav-group">
          <p class="nav-group-label">{{ group.label }}</p>
          <RouterLink
            v-for="entry in group.entries"
            :key="entry.path"
            :to="entry.path"
            @click="menuOpen = false"
          >
            <WorkbenchIcon :name="entry.icon" />
            {{ entry.label }}
          </RouterLink>
        </div>
      </nav>
      <div class="sidebar-bottom">
        <div class="avatar">
          {{
            session.user.display_name?.slice(0, 1) ||
            session.user.username.slice(0, 1)
          }}
        </div>
        <div>
          <strong>
            {{ session.user.display_name || session.user.username }}
          </strong>
          <small>{{ session.user.is_admin ? '平台管理员' : '项目成员' }}</small>
        </div>
        <button class="logout" aria-label="退出登录" @click="perform(logout)">
          退出
        </button>
      </div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <button
          class="icon-button menu-toggle"
          aria-label="展开导航"
          :aria-expanded="menuOpen"
          @click="menuOpen = !menuOpen"
        >
          <WorkbenchIcon name="menu" />
        </button>
        <div class="topbar-context">
          <span>当前视图</span>
          <strong>{{ currentPageName }}</strong>
        </div>
        <div class="topbar-scope">
          <span>工作范围</span>
          <strong>{{ scopeName }}</strong>
        </div>
        <span class="topbar-right">
          <span class="environment-badge">开发验证环境</span>
          <span class="version-label">v{{ packageInfo.version }}</span>
        </span>
      </header>
      <main class="main-content">
        <Feedback :error="error" />
        <p
          v-if="!session.projectId && session.projects.length"
          class="scope-notice"
        >
          正在汇总查看所有有权访问的项目。创建和修改项目内配置时，请先选择具体项目。
        </p>
        <RouterView
          v-if="
            session.projects.length &&
            (session.projectId ||
              (route.path !== '/people' &&
                (route.path !== '/audit' || session.user.is_admin)))
          "
          :key="viewKey"
        />
        <EmptyState
          v-else-if="!session.projects.length"
          title="先建立一个项目"
          description="项目用于组织程序、任务、执行节点和成员权限。"
        >
          <button
            v-if="session.user.is_admin"
            class="button primary"
            @click="projectModal = true"
          >
            创建项目
          </button>
          <p v-else>请联系管理员将你加入项目。</p>
        </EmptyState>
        <EmptyState
          v-else
          title="请选择具体项目"
          description="这项管理操作按项目授权，请先在左侧选择要维护的项目。"
        />
      </main>
    </div>
    <Modal v-if="projectModal" title="创建项目" @close="projectModal = false">
      <form @submit.prevent="createProject">
        <Feedback :error="error" />
        <label>
          项目名称
          <input v-model="projectName" required maxlength="100" />
        </label>
        <div class="form-actions">
          <button class="button primary" :disabled="pending">
            {{ pending ? '创建中…' : '创建项目' }}
          </button>
        </div>
      </form>
    </Modal>
  </div>
</template>
