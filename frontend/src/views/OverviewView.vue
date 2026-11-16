<script setup lang="ts">
import { session, canDevelop, projectName } from '../session'
import { useResource, formatTime } from '../composables'
import type { Overview } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import WorkbenchIcon from '../components/WorkbenchIcon.vue'
const { result, loading, error, refresh } = useResource<Overview>(() =>
  session.projectId
    ? `/overview?project_id=${encodeURIComponent(session.projectId)}`
    : '/overview',
)
const runsPath = (stateGroup?: string) => {
  const query = new URLSearchParams()
  if (session.projectId) query.set('project_id', session.projectId)
  if (stateGroup) query.set('state_group', stateGroup)
  return query.size ? `/runs?${query}` : '/runs'
}
const cards: {
  key: keyof Omit<Overview, 'recent_runs'>
  label: string
  hint: string
  icon: string
  path: string
  stateGroup?: string
  tone?: string
}[] = [
  {
    key: 'active_runs',
    label: '正在运行',
    hint: '查看执行进展',
    icon: 'run',
    path: '/runs',
    stateGroup: 'active',
  },
  {
    key: 'queued_runs',
    label: '等待执行',
    hint: '查看排队任务',
    icon: 'clock',
    path: '/runs',
    stateGroup: 'queued',
  },
  {
    key: 'failed_runs',
    label: '失败运行',
    hint: '前往运行中心核查',
    tone: 'danger',
    icon: 'task',
    path: '/runs',
    stateGroup: 'failed',
  },
  {
    key: 'open_alerts',
    label: '待处理告警',
    hint: '等待负责人处理',
    tone: 'warning',
    icon: 'alert',
    path: '/alerts',
  },
]
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">今天的自动化工作</p>
      <h1>工作台总览</h1>
      <p>程序、调度与执行进展，在这里连接起来。</p>
    </div>
    <button class="button" :disabled="loading" @click="refresh">
      <WorkbenchIcon name="refresh" />
      刷新状态
    </button>
  </header>
  <Feedback :error="error" :loading="loading" />
  <div class="metrics">
    <RouterLink
      v-for="card in cards"
      :key="card.key"
      :to="card.path === '/runs' ? runsPath(card.stateGroup) : card.path"
      class="metric"
      :class="card.tone"
    >
      <div class="metric-label">
        <span>{{ card.label }}</span>
        <WorkbenchIcon :name="card.icon" />
      </div>
      <strong>{{ result?.[card.key] ?? '暂无' }}</strong>
      <small>
        {{ card.hint }}
        <span aria-hidden="true">↗</span>
      </small>
    </RouterLink>
  </div>
  <div class="capacity-strip">
    <div>
      <span class="capacity-icon"><WorkbenchIcon name="node" /></span>
      <span>
        <strong>{{ result?.online_nodes ?? '暂无' }}</strong>
        个在线节点
      </span>
      <span class="capacity-divider"></span>
      <span>
        <strong>{{ result?.total_tasks ?? '暂无' }}</strong>
        个已登记任务
      </span>
    </div>
    <RouterLink to="/nodes" class="text-link">
      管理执行电脑
      <span aria-hidden="true">→</span>
    </RouterLink>
  </div>
  <section class="overview-start" aria-label="开始使用工作台">
    <div class="start-heading">
      <div>
        <span class="eyebrow">从一个程序开始</span>
        <h2>把自动化交给工作台</h2>
        <p>接入已有脚本，安排执行，让每次运行都有记录。</p>
      </div>
      <WorkbenchIcon name="flow" />
    </div>
    <div class="quick-actions">
      <RouterLink to="/programs" class="quick-action">
        <span class="action-icon blue"><WorkbenchIcon name="program" /></span>
        <span>
          <strong>{{ canDevelop ? '接入程序' : '查看程序' }}</strong>
          <small>上传程序包，保存可执行版本</small>
        </span>
        <WorkbenchIcon name="arrow" />
      </RouterLink>
      <RouterLink to="/tasks" class="quick-action">
        <span class="action-icon mint"><WorkbenchIcon name="task" /></span>
        <span>
          <strong>{{ canDevelop ? '创建任务' : '查看任务' }}</strong>
          <small>选择程序、参数与执行方式</small>
        </span>
        <WorkbenchIcon name="arrow" />
      </RouterLink>
      <RouterLink :to="runsPath()" class="quick-action">
        <span class="action-icon amber"><WorkbenchIcon name="run" /></span>
        <span>
          <strong>查看运行</strong>
          <small>跟踪状态、结果与执行日志</small>
        </span>
        <WorkbenchIcon name="arrow" />
      </RouterLink>
    </div>
  </section>
  <section class="panel">
    <header class="panel-heading">
      <div>
        <h2>最近执行</h2>
        <p>业务结果、进程状态与异常均以运行记录为准。</p>
      </div>
      <RouterLink :to="runsPath()" class="text-link">
        全部运行
        <span aria-hidden="true">→</span>
      </RouterLink>
    </header>
    <div v-if="result?.recent_runs.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务</th>
            <th>运行状态</th>
            <th>创建时间</th>
            <th><span class="visually-hidden">查看运行</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="run in result.recent_runs" :key="run.id">
            <td>
              <RouterLink :to="`/runs/${run.id}`" class="text-link">
                {{ run.task_name || '未命名任务' }}
              </RouterLink>
              <span v-if="!session.projectId" class="subline project-label">
                {{ projectName(run.project_id) }}
              </span>
              <span class="subline mono run-reference" :title="run.id">
                运行 {{ run.id.slice(0, 8) }}
              </span>
            </td>
            <td><StatusBadge :value="run.state" /></td>
            <td class="numeric">{{ formatTime(run.created_at) }}</td>
            <td class="align-right">
              <RouterLink :to="`/runs/${run.id}`" class="text-link">
                查看详情 →
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!loading && !error"
      title="还没有运行记录"
      description="先登记程序并创建任务，每次执行会自动留在这里。"
    >
      <RouterLink class="button" to="/programs">
        {{ canDevelop ? '接入第一个程序' : '查看已登记程序' }}
      </RouterLink>
    </EmptyState>
  </section>
  <div class="overview-note">
    <span class="note-marker">i</span>
    <p>
      机器在线不等于任务成功。对于失联或结果不确定的任务，请先核对实际处理情况，再决定后续操作。
    </p>
  </div>
</template>
