<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useResource, formatTime } from '../composables'
import { session } from '../session'
import type { Node, Page, Run, Task } from '../types'
import { NODE_FILES_CAPABILITY } from '../nodeFiles'
import Feedback from '../components/Feedback.vue'
import StatusBadge from '../components/StatusBadge.vue'
import EmptyState from '../components/EmptyState.vue'
import NodeOutputForm from '../components/NodeOutputForm.vue'

interface NodeDetail extends Node {
  projects: { project_id: string; name: string; enabled: boolean }[]
}
const route = useRoute()
const router = useRouter()
const nodeId = computed(() => String(route.params.id || ''))
const node = useResource<NodeDetail>(
  () => `/nodes/${encodeURIComponent(nodeId.value)}`,
)
const projectId = ref('')
const outputOpen = ref(false)
const projects = computed(() =>
  (node.result.value?.projects || []).filter((project) => project.enabled),
)
const canCreate = computed(() =>
  Boolean(
    projectId.value &&
    (session.user?.is_admin ||
      ['project_admin', 'developer'].includes(
        session.projects.find((project) => project.id === projectId.value)
          ?.role || '',
      )),
  ),
)
const tasks = useResource<Page<Task>>(() =>
  node.result.value
    ? `/tasks?node_id=${encodeURIComponent(nodeId.value)}${projectId.value ? `&project_id=${encodeURIComponent(projectId.value)}` : ''}&limit=50`
    : null,
)
const runs = useResource<Page<Run>>(() =>
  node.result.value
    ? `/runs?node_id=${encodeURIComponent(nodeId.value)}${projectId.value ? `&project_id=${encodeURIComponent(projectId.value)}` : ''}&limit=20`
    : null,
)
watch(
  () => nodeId.value,
  () => {
    projectId.value = ''
    outputOpen.value = false
  },
)
watch(projects, (choices) => {
  if (!choices.some((project) => project.project_id === projectId.value))
    projectId.value = choices.some(
      (project) => project.project_id === session.projectId,
    )
      ? session.projectId
      : choices[0]?.project_id || ''
})
async function refresh() {
  await Promise.all([node.refresh(), tasks.refresh(), runs.refresh()])
}
function openTasks(create = false) {
  if (projectId.value) session.projectId = projectId.value
  void router.push({
    path: '/tasks',
    query: { node_id: nodeId.value, ...(create ? { create: '1' } : {}) },
  })
}
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => {
    if (
      !document.hidden &&
      !node.loading.value &&
      !tasks.loading.value &&
      !runs.loading.value &&
      !outputOpen.value
    )
      void refresh()
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <RouterLink class="text-link" to="/nodes">← 执行电脑</RouterLink>
  <header class="page-header">
    <div>
      <p class="eyebrow">执行电脑工作区</p>
      <h1>{{ node.result.value?.name || '电脑详情' }}</h1>
      <p>
        输入和结果路径属于这台电脑。服务器通过执行器传递任务，无需建立共享文件夹。
      </p>
    </div>
    <button
      class="button"
      :disabled="
        node.loading.value || tasks.loading.value || runs.loading.value
      "
      @click="refresh"
    >
      刷新状态
    </button>
  </header>
  <Feedback :error="node.error.value" :loading="node.loading.value" />
  <template v-if="node.result.value">
    <section class="panel content-padding">
      <dl class="node-status-grid">
        <div>
          <dt>连接状态</dt>
          <dd>
            <StatusBadge
              :value="node.result.value.online ? 'online' : 'offline'"
            />
          </dd>
        </div>
        <div>
          <dt>执行器状态</dt>
          <dd>
            <StatusBadge
              :value="node.result.value.dispatch_status || 'unknown'"
            />
          </dd>
          <small>
            {{ node.result.value.dispatch_detail || '等待电脑报告接单状态' }}
          </small>
        </div>
        <div>
          <dt>正在执行</dt>
          <dd>
            {{ node.result.value.active_runs || 0 }} /
            {{ node.result.value.slots }} 个名额
          </dd>
        </div>
        <div>
          <dt>最近心跳</dt>
          <dd>{{ formatTime(node.result.value.last_seen) }}</dd>
        </div>
        <div>
          <dt>配置同步</dt>
          <dd>
            <StatusBadge
              :value="node.result.value.config_status || 'legacy_unmanaged'"
            />
          </dd>
        </div>
      </dl>
      <p class="muted">
        在线不代表已运行。下方运行记录可查看排队原因、是否启动、日志及结果；页面可见时每
        5 秒更新。
      </p>
      <p
        v-if="!node.result.value.capabilities.includes(NODE_FILES_CAPABILITY)"
        class="feedback warning"
      >
        该电脑尚未报告新文件能力。升级执行器后才能从网页选择它的文件和结果目录。
      </p>
    </section>
    <section class="panel content-padding section-gap">
      <div class="toolbar">
        <label>
          使用项目
          <select v-model="projectId" aria-label="电脑任务所属项目">
            <option value="" disabled>请选择有权使用的项目</option>
            <option
              v-for="project in projects"
              :key="project.project_id"
              :value="project.project_id"
            >
              {{ project.name }}
            </option>
          </select>
        </label>
        <button
          class="button primary"
          :disabled="!canCreate"
          @click="openTasks(true)"
        >
          在这台电脑上创建任务
        </button>
        <button class="button" @click="openTasks()">管理此电脑的任务</button>
      </div>
      <h2>已配置任务</h2>
      <Feedback :error="tasks.error.value" :loading="tasks.loading.value" />
      <ul v-if="tasks.result.value?.items.length" class="node-task-list">
        <li v-for="task in tasks.result.value.items" :key="task.id">
          <strong>{{ task.name }}</strong>
          <StatusBadge :value="task.enabled ? 'enabled' : 'disabled'" />
          <RouterLink
            class="text-link"
            :to="{
              path: '/runs',
              query: { task_id: task.id, project_id: task.project_id },
            }"
          >
            查看运行记录 →
          </RouterLink>
        </li>
      </ul>
      <EmptyState
        v-else-if="!tasks.loading.value"
        title="还没有绑定这台电脑的任务"
        description="选择项目后创建任务，程序就会固定交给这台电脑执行。"
      />
      <p
        v-if="tasks.result.value && tasks.result.value.total > 50"
        class="muted"
      >
        这里只列出前 50 个任务，请进入任务管理查看全部。
      </p>
    </section>
    <section class="panel content-padding section-gap">
      <h2>这台电脑的最近运行</h2>
      <Feedback :error="runs.error.value" :loading="runs.loading.value" />
      <div v-if="runs.result.value?.items.length" class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>执行状态</th>
              <th>启动时间</th>
              <th>结果另存</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="run in runs.result.value.items" :key="run.id">
              <td>{{ run.task_name }}</td>
              <td>
                <StatusBadge :value="run.state" />
                <small class="subline">{{ run.waiting_reason }}</small>
              </td>
              <td>
                {{ run.started_at ? formatTime(run.started_at) : '尚未启动' }}
              </td>
              <td>
                {{
                  run.file_status?.save_state === 'succeeded'
                    ? '已保存'
                    : run.file_status?.save_state === 'failed'
                      ? '另存失败'
                      : run.file_status?.save_state === 'pending'
                        ? '等待保存'
                        : '未要求另存'
                }}
              </td>
              <td>
                <RouterLink class="text-link" :to="`/runs/${run.id}`">
                  日志与结果 →
                </RouterLink>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState
        v-else-if="!runs.loading.value"
        title="暂无运行记录"
        description="创建任务并运行后，执行进度会显示在这里。"
      />
      <p v-if="runs.result.value && runs.result.value.total > 20" class="muted">
        显示最近 20 次运行，更多记录请在运行中心查询。
      </p>
    </section>
    <div
      v-if="
        session.user?.is_admin &&
        node.result.value.capabilities.includes(NODE_FILES_CAPABILITY)
      "
      class="section-gap"
    >
      <button class="button" @click="outputOpen = !outputOpen">
        {{ outputOpen ? '收起' : '维护' }}结果保存目录授权
      </button>
      <NodeOutputForm
        v-if="outputOpen && projectId"
        :node="node.result.value"
        :project-id="projectId"
        :projects="
          projects.map((project) => ({
            id: project.project_id,
            name: project.name,
          }))
        "
      />
    </div>
  </template>
</template>

<style scoped>
.node-status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 20px;
  margin: 0;
}
.node-status-grid dt {
  color: var(--text-muted, #64748b);
  margin-bottom: 10px;
}
.node-status-grid dd {
  margin: 0;
}
.node-task-list {
  list-style: none;
  padding: 0;
}
.node-task-list li {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border, #dce4ef);
}
</style>
