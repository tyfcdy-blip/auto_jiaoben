<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { parseObject, request } from '../api'
import {
  formatTime,
  projectPath,
  useAction,
  useChoices,
  useResource,
} from '../composables'
import { canOperate, projectName, session } from '../session'
import type { Page, Run, Schedule, Task } from '../types'
import EmptyState from '../components/EmptyState.vue'
import DirectoryFields from '../components/DirectoryFields.vue'
import Feedback from '../components/Feedback.vue'
import Modal from '../components/Modal.vue'
import Pagination from '../components/Pagination.vue'
import StatusBadge from '../components/StatusBadge.vue'
import {
  directoryParameters,
  directoryTask,
  splitDirectoryParameters,
} from '../directoryInput'

const scheduleOffset = ref(0)
const scheduleSearch = ref('')
const scheduleResource = useResource<Page<Schedule>>(() => {
  if (!session.projects.length) return null
  const path = projectPath('/schedules', scheduleOffset.value)
  return scheduleSearch.value.trim()
    ? `${path}&q=${encodeURIComponent(scheduleSearch.value.trim())}`
    : path
})
const schedules = { ...scheduleResource, offset: scheduleOffset }
const tasks = useChoices<Task>('/tasks')
const action = useAction()
const selectedSchedule = ref<Schedule | null>(null)
const editingSchedule = ref<Schedule | null>(null)
const deleteCandidate = ref<Schedule | null>(null)
const editName = ref('')
const editCron = ref('')
const editParameters = ref('{}')
const editInputDir = ref('')
const editBatchKey = ref('')
const editInputBatchId = ref('')
const editInputLabel = ref('')
const editMisfire = ref<'skip' | 'once'>('skip')
const runOffset = ref(0)
const runEndpoint = computed(() => {
  const schedule = selectedSchedule.value
  if (!schedule) return null
  const query = new URLSearchParams({
    project_id: schedule.project_id,
    schedule_id: schedule.id,
    source: 'schedule',
    limit: '50',
    offset: String(runOffset.value),
  })
  return `/runs?${query}`
})
const runs = useResource<Page<Run>>(() => runEndpoint.value)

watch(
  () => session.projectId,
  () => {
    selectedSchedule.value = null
    runOffset.value = 0
  },
)
watch(scheduleSearch, () => {
  scheduleOffset.value = 0
})

function taskName(taskId: string) {
  return (
    tasks.result.value?.items.find((task) => task.id === taskId)?.name || taskId
  )
}

function taskFor(taskId: string) {
  return tasks.result.value?.items.find((task) => task.id === taskId) || null
}

function showRuns(schedule: Schedule) {
  selectedSchedule.value = schedule
  runOffset.value = 0
}

async function toggle(schedule: Schedule) {
  const enabling = !schedule.enabled
  await action.perform(
    async () => {
      const updated = await request<Schedule>(`/schedules/${schedule.id}`, {
        method: 'PATCH',
        body: { enabled: enabling },
      })
      Object.assign(schedule, updated)
      if (selectedSchedule.value?.id === schedule.id)
        selectedSchedule.value = schedule
    },
    enabling ? '计划已启用' : '计划已暂停，不影响已经受理的运行',
  )
}

function prepareEdit(schedule: Schedule) {
  const task = taskFor(schedule.task_id)
  const split = splitDirectoryParameters(schedule.parameters || {})
  editingSchedule.value = schedule
  editName.value = schedule.name
  editCron.value = schedule.cron
  editParameters.value = JSON.stringify(
    task && directoryTask(task) ? split.business : schedule.parameters || {},
    null,
    2,
  )
  editInputDir.value = split.inputDir
  editBatchKey.value = split.batchKey
  editInputBatchId.value = schedule.input_batch_id || ''
  editInputLabel.value = schedule.input_label || ''
  editMisfire.value = schedule.misfire_policy
}

async function saveEdit() {
  const schedule = editingSchedule.value!
  const task = taskFor(schedule.task_id)
  await action.perform(async () => {
    const values = parseObject(editParameters.value)
    const updated = await request<Schedule>(`/schedules/${schedule.id}`, {
      method: 'PUT',
      body: {
        base_revision: schedule.revision,
        name: editName.value.trim(),
        cron: editCron.value.trim(),
        timezone: schedule.timezone,
        parameters:
          task && directoryTask(task)
            ? directoryParameters(
                values,
                editInputDir.value,
                editBatchKey.value,
                true,
              )
            : values,
        input_batch_id:
          task && directoryTask(task)
            ? null
            : editInputBatchId.value.trim() || null,
        input_label:
          task && directoryTask(task)
            ? null
            : editInputLabel.value.trim() || null,
        misfire_policy: editMisfire.value,
      },
    })
    Object.assign(schedule, updated)
    if (selectedSchedule.value?.id === schedule.id)
      selectedSchedule.value = schedule
    editingSchedule.value = null
  }, '定时计划已更新，下次触发时间已重新计算')
}

async function removeSchedule() {
  const schedule = deleteCandidate.value!
  await action.perform(async () => {
    await request(`/schedules/${schedule.id}`, { method: 'DELETE' })
    if (selectedSchedule.value?.id === schedule.id)
      selectedSchedule.value = null
    deleteCandidate.value = null
    await schedules.refresh()
  }, '定时计划已删除，历史运行仍保留在运行中心')
}
</script>

<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">SCHEDULES</p>
      <h1>定时计划</h1>
      <p>查看计划状态、下次触发和实际运行记录；计划仍从对应任务中添加。</p>
    </div>
    <RouterLink to="/tasks" class="button primary">
      前往任务管理添加计划
    </RouterLink>
  </header>
  <Feedback
    :error="schedules.error.value || tasks.error.value || action.error.value"
    :success="action.success.value"
    :loading="schedules.loading.value"
  />
  <section class="panel">
    <div class="panel-heading compact-heading">
      <div>
        <h2>计划配置</h2>
        <p>按名称查询，编辑时间、参数和启停状态。</p>
      </div>
      <input
        v-model="scheduleSearch"
        class="compact-search"
        type="search"
        aria-label="查询定时计划"
        placeholder="查询计划名称"
      />
    </div>
    <div v-if="schedules.result.value?.items.length" class="table-scroll">
      <table class="responsive-table schedule-table">
        <thead>
          <tr>
            <th>计划 / 任务</th>
            <th>时间规则</th>
            <th>下次触发</th>
            <th>最近调度情况</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="schedule in schedules.result.value.items"
            :key="schedule.id"
          >
            <td data-label="计划 / 任务">
              <strong>{{ schedule.name }}</strong>
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(schedule.project_id) }}
              </small>
              <small class="subline">{{ taskName(schedule.task_id) }}</small>
            </td>
            <td data-label="时间规则">
              <span class="mono">{{ schedule.cron }}</span>
              <small class="subline">{{ schedule.timezone }}</small>
              <small class="subline">
                {{
                  schedule.misfire_policy === 'once'
                    ? '错过时补最近一次'
                    : '错过时跳过'
                }}
              </small>
            </td>
            <td class="numeric" data-label="下次触发">
              {{ formatTime(schedule.next_fire) }}
            </td>
            <td data-label="最近调度情况">
              <span v-if="schedule.last_error" class="schedule-error">
                {{ schedule.last_error }}
              </span>
              <span v-else class="muted">暂无调度异常</span>
            </td>
            <td data-label="状态">
              <StatusBadge :value="schedule.enabled ? 'enabled' : 'disabled'" />
            </td>
            <td data-label="操作">
              <div class="row-actions">
                <button
                  v-if="!session.projectId"
                  class="button small"
                  type="button"
                  @click="session.projectId = schedule.project_id"
                >
                  进入项目
                </button>
                <button
                  class="button small"
                  type="button"
                  @click="showRuns(schedule)"
                >
                  运行记录
                </button>
                <button
                  v-if="canOperate"
                  class="button small"
                  type="button"
                  :disabled="action.pending.value"
                  @click="prepareEdit(schedule)"
                >
                  编辑
                </button>
                <button
                  v-if="canOperate"
                  class="button small"
                  type="button"
                  :disabled="action.pending.value"
                  @click="toggle(schedule)"
                >
                  {{ schedule.enabled ? '暂停' : '启用' }}
                </button>
                <button
                  v-if="canOperate"
                  class="button danger small"
                  type="button"
                  :disabled="action.pending.value"
                  @click="deleteCandidate = schedule"
                >
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!schedules.loading.value && !schedules.error.value"
      :title="
        session.projectId
          ? '当前项目还没有定时计划'
          : '所有项目都还没有定时计划'
      "
      description="到任务管理选择任务，再点击“定时运行”添加计划。"
    />
    <Pagination
      v-if="schedules.result.value"
      v-model:offset="schedules.offset.value"
      :total="schedules.result.value.total"
      :loading="schedules.loading.value"
    />
  </section>

  <section v-if="selectedSchedule" class="panel section-gap">
    <div class="panel-heading">
      <div>
        <h2>{{ selectedSchedule.name }} · 运行记录</h2>
        <p>仅显示由这个定时计划实际触发的运行。</p>
      </div>
      <button
        class="button small"
        :disabled="runs.loading.value"
        @click="runs.refresh"
      >
        刷新
      </button>
    </div>
    <Feedback :error="runs.error.value" :loading="runs.loading.value" />
    <div v-if="runs.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>运行编号</th>
            <th>触发结果</th>
            <th>状态</th>
            <th>计划受理时间</th>
            <th>结束时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="run in runs.result.value.items"
            :key="run.schedule_event_id || run.id"
          >
            <td class="mono">{{ run.id }}</td>
            <td>
              <StatusBadge :value="run.source" />
              <small v-if="run.schedule_deduplicated" class="subline muted">
                已关联到此前{{
                  run.original_source === 'manual'
                    ? '手动'
                    : run.original_source === 'schedule'
                      ? '定时'
                      : '其他'
                }}运行
              </small>
            </td>
            <td><StatusBadge :value="run.state" /></td>
            <td class="numeric">
              {{ formatTime(run.schedule_triggered_at || run.created_at) }}
            </td>
            <td class="numeric">{{ formatTime(run.finished_at) }}</td>
            <td>
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
      title="这个计划还没有运行记录"
      description="只有计划到点并成功受理后才会产生记录。请同时核对启用状态和下次触发时间。"
    />
    <Pagination
      v-if="runs.result.value"
      v-model:offset="runOffset"
      :total="runs.result.value.total"
      :loading="runs.loading.value"
    />
  </section>
  <Modal
    v-if="editingSchedule"
    :title="`编辑计划 · ${editingSchedule.name}`"
    wide
    :busy="action.pending.value"
    @close="editingSchedule = null"
  >
    <form @submit.prevent="saveEdit">
      <Feedback :error="action.error.value" />
      <div class="form-grid">
        <label>
          计划名称
          <input v-model="editName" required maxlength="100" />
        </label>
        <label>
          五字段 Cron
          <input v-model="editCron" required class="mono" />
        </label>
        <label>
          错过执行时间
          <select v-model="editMisfire">
            <option value="skip">跳过错过的时间</option>
            <option value="once">仅补最近一次</option>
          </select>
        </label>
      </div>
      <DirectoryFields
        v-if="
          taskFor(editingSchedule.task_id) &&
          directoryTask(taskFor(editingSchedule.task_id)!)
        "
        v-model:input-dir="editInputDir"
        v-model:batch-key="editBatchKey"
        scheduled
        :disabled="action.pending.value"
      />
      <p
        v-if="
          taskFor(editingSchedule.task_id) &&
          directoryTask(taskFor(editingSchedule.task_id)!)
        "
        class="feedback warning"
      >
        周期计划建议使用
        <code>${scheduled_date}</code>
        作为批次编号。参数和程序版本都相同时会关联已有运行；其他参数不同则会报告业务批次冲突，均不会重复执行。
      </p>
      <div v-else class="form-grid">
        <label>
          固定输入批次编号（可选）
          <input v-model="editInputBatchId" />
        </label>
        <label>
          等待批次标签（可选）
          <input v-model="editInputLabel" />
        </label>
      </div>
      <label>
        运行参数（JSON 对象）
        <textarea
          v-model="editParameters"
          class="mono"
          rows="7"
          required
        ></textarea>
      </label>
      <p class="form-caption">保存后会从当前时间重新计算下一次触发时间。</p>
      <div class="form-actions">
        <button class="button primary" :disabled="action.pending.value">
          保存修改
        </button>
      </div>
    </form>
  </Modal>
  <Modal
    v-if="deleteCandidate"
    :title="`删除计划 · ${deleteCandidate.name}`"
    :busy="action.pending.value"
    @close="deleteCandidate = null"
  >
    <Feedback :error="action.error.value" />
    <p>删除后该计划不再触发；已有运行和操作审计仍然保留。</p>
    <div class="form-actions">
      <button class="button" type="button" @click="deleteCandidate = null">
        取消
      </button>
      <button
        class="button danger"
        type="button"
        :disabled="action.pending.value"
        @click="removeSchedule"
      >
        确认删除
      </button>
    </div>
  </Modal>
</template>

<style scoped>
.compact-search {
  width: min(300px, 100%);
  margin: 0;
}
.schedule-error {
  color: var(--warning);
  font-weight: 600;
}
@media (max-width: 640px) {
  .compact-heading {
    align-items: stretch;
  }
}
</style>
