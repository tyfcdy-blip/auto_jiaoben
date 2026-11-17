<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loadChoices } from '../api'
import { useResource, formatTime } from '../composables'
import { session } from '../session'
import type { Page, Run, Task } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'

const route = useRoute()
const router = useRouter()
const queryValue = (key: string) =>
  typeof route.query[key] === 'string' ? String(route.query[key]) : ''
const projectId = ref(queryValue('project_id'))
const taskId = ref(queryValue('task_id'))
const source = ref(queryValue('source'))
const state = ref(queryValue('state'))
const stateGroup = ref(queryValue('state_group'))
const offset = ref(0)
const stateFilter = computed({
  get: () => (stateGroup.value ? `group:${stateGroup.value}` : state.value),
  set: (value: string) => {
    if (value.startsWith('group:')) {
      state.value = ''
      stateGroup.value = value.slice(6)
    } else {
      state.value = value
      stateGroup.value = ''
    }
  },
})

const taskChoices = useResource<Page<Task>>(
  () => (session.user ? '/tasks?limit=200&offset=0' : null),
  (path, signal) => loadChoices<Task>(path, signal),
)
const visibleTasks = computed(() =>
  (taskChoices.result.value?.items || []).filter(
    (task) => !projectId.value || task.project_id === projectId.value,
  ),
)
watch(projectId, () => {
  if (!visibleTasks.value.some((task) => task.id === taskId.value))
    taskId.value = ''
})
const endpoint = computed(() => {
  const query = new URLSearchParams({
    limit: '50',
    offset: String(offset.value),
  })
  if (projectId.value) query.set('project_id', projectId.value)
  if (taskId.value) query.set('task_id', taskId.value)
  if (source.value) query.set('source', source.value)
  if (state.value) query.set('state', state.value)
  if (stateGroup.value) query.set('state_group', stateGroup.value)
  return `/runs?${query}`
})
const runs = useResource<Page<Run>>(() => endpoint.value)
const dispatchPaused = computed(() =>
  (runs.result.value?.items || []).some(
    (run) =>
      run.state === 'queued' && run.waiting_reason_code === 'dispatch_paused',
  ),
)
let refreshTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  refreshTimer = setInterval(() => {
    if (!document.hidden && !runs.loading.value) void runs.refresh()
  }, 5000)
})
onUnmounted(() => clearInterval(refreshTimer))
watch(
  () => route.query,
  () => {
    projectId.value = queryValue('project_id')
    taskId.value = queryValue('task_id')
    source.value = queryValue('source')
    state.value = queryValue('state')
    stateGroup.value = queryValue('state_group')
    offset.value = 0
  },
  { deep: true },
)
watch([projectId, taskId, source, state, stateGroup], () => {
  offset.value = 0
  const query: Record<string, string> = {}
  if (projectId.value) query.project_id = projectId.value
  if (taskId.value) query.task_id = taskId.value
  if (source.value) query.source = source.value
  if (state.value) query.state = state.value
  if (stateGroup.value) query.state_group = stateGroup.value
  void router.replace({ query })
})
function clearFilters() {
  projectId.value = ''
  taskId.value = ''
  source.value = ''
  state.value = ''
  stateGroup.value = ''
}
</script>

<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">ALL EXECUTIONS</p>
      <h1>运行中心</h1>
      <p>统一查看有权访问的全部项目运行，按项目、任务和触发方式快速定位。</p>
    </div>
    <button class="button" :disabled="runs.loading.value" @click="runs.refresh">
      刷新记录
    </button>
  </header>
  <Feedback
    :error="runs.error.value || taskChoices.error.value"
    :loading="runs.loading.value"
  />
  <div v-if="dispatchPaused" class="feedback warning section-gap" role="status">
    <strong>平台派发已暂停，排队任务尚未执行。</strong>
    请管理员先核对待执行任务，再恢复平台派发；电脑在线或空闲不会解除此暂停。
  </div>
  <p class="form-caption">页面可见时每 5 秒更新运行状态，无需反复点击刷新。</p>
  <section class="filter-panel" aria-label="运行记录筛选">
    <label>
      项目
      <select v-model="projectId" aria-label="按项目筛选">
        <option value="">全部项目</option>
        <option
          v-for="project in session.projects"
          :key="project.id"
          :value="project.id"
        >
          {{ project.name }}
        </option>
      </select>
    </label>
    <label>
      任务
      <select v-model="taskId" aria-label="按任务筛选">
        <option value="">全部任务</option>
        <option v-for="task in visibleTasks" :key="task.id" :value="task.id">
          {{ task.name }}
        </option>
      </select>
    </label>
    <label>
      触发方式
      <select v-model="source" aria-label="按触发方式筛选">
        <option value="">全部方式</option>
        <option value="manual">手动触发</option>
        <option value="schedule">定时触发</option>
        <option value="event">事件触发</option>
      </select>
    </label>
    <label>
      状态
      <select v-model="stateFilter" aria-label="按状态筛选">
        <option value="">全部状态</option>
        <optgroup label="常用状态组">
          <option value="group:active">执行中（全部）</option>
          <option value="group:queued">等待中（全部）</option>
          <option value="group:failed">异常结果（全部）</option>
        </optgroup>
        <optgroup label="精确状态">
          <option value="waiting_input">等待输入</option>
          <option value="queued">排队中</option>
          <option value="leased">准备启动</option>
          <option value="running">运行中</option>
          <option value="cancel_requested">停止待确认</option>
          <option value="succeeded">已成功</option>
          <option value="failed">失败</option>
          <option value="partial">部分完成</option>
          <option value="unknown">结果待核对</option>
          <option value="canceled">已取消</option>
        </optgroup>
      </select>
    </label>
    <button
      class="button small filter-reset"
      type="button"
      @click="clearFilters"
    >
      清除筛选
    </button>
  </section>
  <section class="panel">
    <div v-if="runs.result.value?.items.length" class="table-scroll">
      <table class="responsive-table runs-table">
        <thead>
          <tr>
            <th>项目 / 任务</th>
            <th>触发方式</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>开始时间</th>
            <th>结束时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in runs.result.value.items" :key="run.id">
            <td data-label="项目 / 任务">
              <RouterLink :to="`/runs/${run.id}`" class="text-link strong">
                {{ run.task_name || run.task_id }}
              </RouterLink>
              <small class="subline">
                {{ run.project_name || run.project_id }}
              </small>
              <small class="subline mono">{{ run.id }}</small>
            </td>
            <td data-label="触发方式"><StatusBadge :value="run.source" /></td>
            <td data-label="状态">
              <StatusBadge :value="run.state" />
              <small v-if="run.waiting_reason" class="subline">
                {{ run.waiting_reason }}
              </small>
            </td>
            <td class="numeric" data-label="创建时间">
              {{ formatTime(run.created_at) }}
            </td>
            <td class="numeric" data-label="开始时间">
              {{ formatTime(run.started_at) }}
            </td>
            <td class="numeric" data-label="结束时间">
              {{ formatTime(run.finished_at) }}
            </td>
            <td data-label="操作">
              <RouterLink :to="`/runs/${run.id}`" class="text-link">
                查看详情 →
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!runs.loading.value && !runs.error.value"
      title="没有符合条件的运行记录"
      description="调整筛选条件，或从任务管理手动运行、添加定时计划。"
    />
    <Pagination
      v-if="runs.result.value"
      v-model:offset="offset"
      :total="runs.result.value.total"
      :loading="runs.loading.value"
    />
  </section>
</template>

<style scoped>
.filter-panel {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr)) auto;
  align-items: end;
  gap: 14px;
  margin-bottom: 16px;
  padding: 18px;
  background: rgba(255, 255, 255, 0.76);
  border: 1px solid rgba(255, 255, 255, 0.95);
  border-radius: var(--radius);
  box-shadow: var(--shadow-soft);
  backdrop-filter: blur(18px);
}
.filter-panel label {
  margin: 0;
}
.filter-reset {
  margin-bottom: 1px;
}
@media (max-width: 900px) {
  .filter-panel {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 560px) {
  .filter-panel {
    grid-template-columns: 1fr;
  }
}
</style>
