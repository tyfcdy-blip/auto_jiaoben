<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  canDevelop,
  canManage,
  canOperate,
  projectName,
  session,
} from '../session'
import { request, parseObject, loadChoices } from '../api'
import { projectPath, useAction, useResource, formatTime } from '../composables'
import type {
  Task,
  Program,
  Version,
  Node,
  Page,
  Batch,
  Schedule,
} from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
import DirectoryFields from '../components/DirectoryFields.vue'
import ParameterFields from '../components/ParameterFields.vue'
import { useBatchDelivery } from '../batchDelivery'
import {
  DIRECTORY_CAPABILITY,
  directorySchema,
  directoryTask,
  directoryParameters,
  splitDirectoryParameters,
} from '../directoryInput'
let scope = 0
onScopeDispose(() => {
  scope++
})
const router = useRouter(),
  taskOffset = ref(0),
  taskSearch = ref(''),
  taskResource = useResource<Page<Task>>(() => {
    if (!session.projects.length) return null
    const path = projectPath('/tasks', taskOffset.value)
    return taskSearch.value.trim()
      ? `${path}&q=${encodeURIComponent(taskSearch.value.trim())}`
      : path
  }),
  tasks = { ...taskResource, offset: taskOffset },
  action = useAction()
const createOpen = ref(false),
  editingTask = ref<Task | null>(null),
  deleteTaskCandidate = ref<Task | null>(null),
  runTask = ref<Task | null>(null),
  scheduleTask = ref<Task | null>(null),
  editingSchedule = ref<Schedule | null>(null),
  deleteScheduleCandidate = ref<Schedule | null>(null),
  scheduleFormOpen = ref(false),
  taskSchedules = ref<Schedule[]>([]),
  programs = ref<Program[]>([]),
  programId = ref(''),
  nodes = ref<Node[]>([]),
  batches = ref<Batch[]>([])
const name = ref(''),
  versionId = ref(''),
  parameters = ref('{}'),
  nodeId = ref(''),
  timeout = ref(300),
  resources = ref(''),
  requiresInput = ref(false),
  operation = ref('run'),
  businessFields = ref('')
const preferredVersionId = ref('')
const runParameters = ref('{}'),
  batchId = ref(''),
  runInputMode = ref<'none' | 'existing' | 'upload'>('none'),
  eventId = ref('')
const scheduleName = ref(''),
  scheduleParameters = ref('{}'),
  scheduleInputMode = ref('none'),
  scheduleInputLabel = ref(''),
  scheduleBatchId = ref(''),
  scheduleMisfire = ref<'skip' | 'once'>('skip'),
  schedulePattern = ref('daily'),
  scheduleTime = ref('08:00'),
  scheduleWeekday = ref('1'),
  scheduleDay = ref(1),
  scheduleAdvancedCron = ref('0 8 * * *'),
  scheduleInputDir = ref(''),
  scheduleBatchKey = ref('')
const inputDir = ref(''),
  batchKey = ref(''),
  runInputDir = ref(''),
  runBatchKey = ref('')
const versionOptions = useResource<Page<Version>>(
  () => (programId.value ? `/programs/${programId.value}/versions` : null),
  loadChoices<Version>,
)
const versions = computed(() =>
  (versionOptions.result.value?.items || [])
    .filter((version) => version.published)
    .map((version) => ({
      ...version,
      programName:
        programs.value.find((program) => program.id === programId.value)
          ?.name || '',
    })),
)
const isDirectory = computed(() =>
  directorySchema(
    versions.value.find((version) => version.id === versionId.value)
      ?.parameter_schema,
  ),
)
const selectedParameterSchema = computed(
  () =>
    versions.value.find((version) => version.id === versionId.value)
      ?.parameter_schema || null,
)
const runDelivery = useBatchDelivery(() => {})
const selectableNodes = computed(() =>
  isDirectory.value
    ? nodes.value.filter((node) =>
        node.capabilities?.includes(DIRECTORY_CAPABILITY),
      )
    : nodes.value,
)
const scheduleCron = computed(() => {
  const [hour, minute] = scheduleTime.value.split(':').map(Number)
  return schedulePattern.value === 'daily'
    ? `${minute} ${hour} * * *`
    : schedulePattern.value === 'weekly'
      ? `${minute} ${hour} * * ${scheduleWeekday.value}`
      : schedulePattern.value === 'monthly'
        ? `${minute} ${hour} ${scheduleDay.value} * *`
        : scheduleAdvancedCron.value.trim()
})
watch(isDirectory, (enabled) => {
  if (enabled) {
    requiresInput.value = false
    if (!selectableNodes.value.some((node) => node.id === nodeId.value))
      nodeId.value = ''
  }
})
watch(programId, () => {
  versionId.value = ''
})
watch(runInputMode, (mode) => {
  if (mode === 'upload') void runDelivery.open()
  else if (runDelivery.opened.value && !runDelivery.pending.value)
    runDelivery.close()
})
watch(taskSearch, () => {
  taskOffset.value = 0
})
watch(versions, (choices) => {
  versionId.value = choices.some(
    (version) => version.id === preferredVersionId.value,
  )
    ? preferredVersionId.value
    : choices[0]?.id || ''
  preferredVersionId.value = ''
})
watch(
  () => session.projectId,
  () => {
    scope++
    createOpen.value = false
    runTask.value = null
    scheduleTask.value = null
    scheduleFormOpen.value = false
    editingTask.value = null
    deleteTaskCandidate.value = null
    editingSchedule.value = null
    deleteScheduleCandidate.value = null
    programId.value = ''
    nodeId.value = ''
    inputDir.value = ''
    batchKey.value = ''
    runInputDir.value = ''
    runBatchKey.value = ''
    parameters.value = '{}'
  },
)
async function prepareCreate() {
  const current = scope
  await action.perform(async () => {
    const [programResult, nodeResult] = await Promise.all([
      loadChoices<Program>(projectPath('/programs')),
      loadChoices<Node>(projectPath('/nodes')),
    ])
    if (current !== scope) return
    programs.value = programResult.items
    editingTask.value = null
    preferredVersionId.value = ''
    programId.value = programResult.items[0]?.id || ''
    nodes.value = nodeResult.items
    name.value = ''
    parameters.value = '{}'
    nodeId.value = ''
    timeout.value = 300
    resources.value = ''
    requiresInput.value = false
    operation.value = 'run'
    businessFields.value = ''
    createOpen.value = true
  }, '')
}
async function prepareEdit(task: Task) {
  const current = scope
  await action.perform(async () => {
    const [programResult, nodeResult] = await Promise.all([
      loadChoices<Program>(projectPath('/programs')),
      loadChoices<Node>(projectPath('/nodes')),
    ])
    if (current !== scope) return
    programs.value = programResult.items
    nodes.value = nodeResult.items
    editingTask.value = task
    preferredVersionId.value = task.version_id
    programId.value = task.program_id || ''
    versionId.value = task.version_id
    name.value = task.name
    const split = splitDirectoryParameters(task.default_parameters || {})
    inputDir.value = split.inputDir
    batchKey.value = split.batchKey
    parameters.value = JSON.stringify(
      directoryTask(task) ? split.business : task.default_parameters || {},
      null,
      2,
    )
    nodeId.value = task.node_id || ''
    timeout.value = task.timeout_seconds
    resources.value = (task.resource_keys || []).join(',')
    requiresInput.value = task.requires_input
    operation.value = task.operation
    businessFields.value = (task.business_key_fields || [])
      .filter((field) => field !== 'batch_key')
      .join(',')
    createOpen.value = true
  }, '')
}
async function saveTask() {
  const current = scope
  await action.perform(
    async () => {
      if (
        isDirectory.value &&
        !selectableNodes.value.some((node) => node.id === nodeId.value)
      )
        throw new Error('文件夹输入必须选择一台支持目录读取的固定执行电脑。')
      const defaults = parseObject(parameters.value)
      const body = {
        project_id: session.projectId,
        name: name.value.trim(),
        version_id: versionId.value,
        default_parameters: isDirectory.value
          ? directoryParameters(defaults, inputDir.value, batchKey.value)
          : defaults,
        node_id: nodeId.value || null,
        timeout_seconds: timeout.value,
        resource_keys: resources.value
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean),
        business_key_fields: [
          ...new Set([
            ...businessFields.value
              .split(',')
              .map((value) => value.trim())
              .filter(Boolean),
            ...(isDirectory.value ? ['batch_key'] : []),
          ]),
        ],
        requires_input: isDirectory.value ? false : requiresInput.value,
        operation: operation.value.trim(),
      }
      if (editingTask.value) {
        delete (body as { project_id?: string }).project_id
        Object.assign(body, {
          base_config_hash: editingTask.value.config_hash,
        })
        await request(`/tasks/${editingTask.value.id}`, {
          method: 'PUT',
          body,
        })
      } else {
        await request('/tasks', { method: 'POST', body })
      }
      if (current !== scope) return
      createOpen.value = false
      editingTask.value = null
      name.value = ''
      await tasks.refresh()
    },
    editingTask.value ? '任务配置已更新' : '任务已创建',
  )
}
function closeTaskForm() {
  createOpen.value = false
  editingTask.value = null
}
async function deleteTask() {
  const task = deleteTaskCandidate.value!
  await action.perform(async () => {
    await request(`/tasks/${task.id}`, { method: 'DELETE' })
    deleteTaskCandidate.value = null
    await tasks.refresh()
  }, '任务已删除')
}
async function toggle(task: Task) {
  await action.perform(
    async () => {
      await request(`/tasks/${task.id}`, {
        method: 'PATCH',
        body: { enabled: !task.enabled },
      })
      await tasks.refresh()
    },
    task.enabled ? '任务已停用' : '任务已启用',
  )
}
async function prepareRun(task: Task) {
  const current = scope
  await action.perform(async () => {
    const response = directoryTask(task)
      ? { items: [] }
      : await loadChoices<Batch>(projectPath('/batches'))
    if (current !== scope) return
    batches.value = response.items
    runTask.value = task
    const defaults = task.default_parameters || {}
    const split = splitDirectoryParameters(defaults)
    runInputDir.value = split.inputDir
    runBatchKey.value = split.batchKey
    runParameters.value = JSON.stringify(
      directoryTask(task) ? split.business : defaults,
      null,
      2,
    )
    batchId.value = ''
    runInputMode.value = task.requires_input ? 'existing' : 'none'
    eventId.value = crypto.randomUUID()
  }, '')
}
async function startRun() {
  const current = scope
  await action.perform(async () => {
    const task = runTask.value!
    const selectedInputMode = runInputMode.value
    const selectedBatch = batchId.value || null
    const selectedEventId = eventId.value
    const selectedInputDir = runInputDir.value
    const selectedBatchKey = runBatchKey.value
    const values = parseObject(runParameters.value)
    if (directoryTask(task) && !task.node_id)
      throw new Error('这个目录任务尚未绑定执行电脑，请先修正任务配置。')
    if (
      !directoryTask(task) &&
      task.requires_input &&
      selectedInputMode === 'none'
    )
      throw new Error('这个任务需要输入文件，请选择已有批次或直接上传文件。')
    let selectedBatchId = selectedBatch
    if (!directoryTask(task) && selectedInputMode === 'upload') {
      await runDelivery.submit()
      if (!runDelivery.batch.value?.ready)
        throw new Error(
          runDelivery.error.value || '文件尚未完成交付，请检查后重试。',
        )
      selectedBatchId = runDelivery.batch.value.id
    }
    if (
      !directoryTask(task) &&
      selectedInputMode === 'existing' &&
      !selectedBatchId
    )
      throw new Error('请选择一个已就绪的输入批次。')
    const response = await request<{ run_id: string }>(
      `/tasks/${runTask.value!.id}/runs`,
      {
        method: 'POST',
        body: {
          event_id: selectedEventId,
          ...(directoryTask(task)
            ? {}
            : {
                input_batch_id:
                  selectedInputMode === 'none' ? null : selectedBatchId,
              }),
          parameters: directoryTask(task)
            ? directoryParameters(values, selectedInputDir, selectedBatchKey)
            : values,
        },
      },
    )
    if (current !== scope) return
    runDelivery.close()
    runTask.value = null
    await router.push(`/runs/${response.run_id}`)
  }, '任务已受理')
}
function chooseRunFiles(event: Event) {
  const input = event.target as HTMLInputElement
  runDelivery.choose(Array.from(input.files || []))
  input.value = ''
}
function dropRunFiles(event: DragEvent) {
  runDelivery.choose(Array.from(event.dataTransfer?.files || []))
}
function closeRun() {
  if (action.pending.value || runDelivery.pending.value) return
  runDelivery.close()
  runTask.value = null
}
async function prepareSchedules(task: Task) {
  const current = scope
  await action.perform(async () => {
    const [scheduleResult, batchResult] = await Promise.all([
      loadChoices<Schedule>(
        `/schedules?task_id=${encodeURIComponent(task.id)}`,
      ),
      directoryTask(task)
        ? Promise.resolve({ items: [], total: 0 })
        : loadChoices<Batch>(projectPath('/batches')),
    ])
    if (current !== scope) return
    scheduleTask.value = task
    taskSchedules.value = scheduleResult.items
    batches.value = batchResult.items
    scheduleFormOpen.value = false
  }, '')
}
function prepareScheduleCreate() {
  const task = scheduleTask.value!
  const defaults = task.default_parameters || {}
  const split = splitDirectoryParameters(defaults)
  scheduleName.value = `${task.name}定时运行`
  scheduleInputDir.value = split.inputDir
  scheduleBatchKey.value = '${scheduled_date}'
  scheduleParameters.value = JSON.stringify(
    directoryTask(task) ? split.business : defaults,
    null,
    2,
  )
  scheduleInputMode.value = 'none'
  scheduleInputLabel.value = ''
  scheduleBatchId.value = ''
  scheduleMisfire.value = 'once'
  editingSchedule.value = null
  scheduleFormOpen.value = true
}
function prepareScheduleEdit(schedule: Schedule) {
  const task = scheduleTask.value!
  const split = splitDirectoryParameters(schedule.parameters || {})
  scheduleName.value = schedule.name
  scheduleInputDir.value = split.inputDir
  scheduleBatchKey.value = split.batchKey
  scheduleParameters.value = JSON.stringify(
    directoryTask(task) ? split.business : schedule.parameters || {},
    null,
    2,
  )
  scheduleInputMode.value = schedule.input_batch_id
    ? 'fixed'
    : schedule.input_label
      ? 'label'
      : 'none'
  scheduleInputLabel.value = schedule.input_label || ''
  scheduleBatchId.value = schedule.input_batch_id || ''
  scheduleMisfire.value = schedule.misfire_policy
  const [minute, hour, day, month, weekday] = schedule.cron.split(' ')
  scheduleTime.value = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
  if (day === '*' && month === '*' && weekday === '*') {
    schedulePattern.value = 'daily'
  } else if (day === '*' && month === '*' && /^\d$/.test(weekday)) {
    schedulePattern.value = 'weekly'
    scheduleWeekday.value = weekday
  } else if (/^\d+$/.test(day) && month === '*' && weekday === '*') {
    schedulePattern.value = 'monthly'
    scheduleDay.value = Number(day)
  } else {
    schedulePattern.value = 'advanced'
    scheduleAdvancedCron.value = schedule.cron
  }
  editingSchedule.value = schedule
  scheduleFormOpen.value = true
}
async function saveSchedule() {
  const current = scope
  await action.perform(
    async () => {
      const task = scheduleTask.value!
      if (directoryTask(task) && !task.node_id)
        throw new Error('目录任务必须先绑定一台执行电脑。')
      const values = parseObject(scheduleParameters.value)
      const body = {
        ...(editingSchedule.value ? {} : { task_id: task.id, enabled: false }),
        name: scheduleName.value.trim(),
        cron: scheduleCron.value,
        timezone: 'Asia/Shanghai',
        parameters: directoryTask(task)
          ? directoryParameters(
              values,
              scheduleInputDir.value,
              scheduleBatchKey.value,
              true,
            )
          : values,
        ...(directoryTask(task)
          ? {}
          : {
              input_batch_id:
                scheduleInputMode.value === 'fixed'
                  ? scheduleBatchId.value || null
                  : null,
              input_label:
                scheduleInputMode.value === 'label'
                  ? scheduleInputLabel.value.trim() || null
                  : null,
            }),
        misfire_policy: scheduleMisfire.value,
      }
      if (editingSchedule.value)
        Object.assign(body, {
          base_revision: editingSchedule.value.revision,
        })
      await request(
        editingSchedule.value
          ? `/schedules/${editingSchedule.value.id}`
          : '/schedules',
        { method: editingSchedule.value ? 'PUT' : 'POST', body },
      )
      if (current !== scope) return
      const result = await loadChoices<Schedule>(
        `/schedules?task_id=${encodeURIComponent(task.id)}`,
      )
      if (current !== scope) return
      taskSchedules.value = result.items
      scheduleFormOpen.value = false
      editingSchedule.value = null
    },
    editingSchedule.value
      ? '定时计划已更新'
      : '定时计划已保存，请检查首次执行时间后启用',
  )
}
async function toggleSchedule(schedule: Schedule) {
  await action.perform(
    async () => {
      const updated = await request<Schedule>(`/schedules/${schedule.id}`, {
        method: 'PATCH',
        body: { enabled: !schedule.enabled },
      })
      Object.assign(schedule, updated)
    },
    schedule.enabled ? '计划已暂停' : '计划已启用',
  )
}
async function deleteSchedule() {
  const schedule = deleteScheduleCandidate.value!
  await action.perform(async () => {
    await request(`/schedules/${schedule.id}`, { method: 'DELETE' })
    deleteScheduleCandidate.value = null
    taskSchedules.value = taskSchedules.value.filter(
      (item) => item.id !== schedule.id,
    )
  }, '定时计划已删除，历史运行仍保留在运行中心')
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">让程序按你的安排工作</p>
      <h1>任务管理</h1>
      <p>选择程序和执行电脑，创建可手动、定时或由事件触发的任务。</p>
    </div>
    <button
      v-if="canDevelop"
      class="button primary"
      :disabled="action.pending.value"
      @click="prepareCreate"
    >
      ＋ 创建任务
    </button>
  </header>
  <Feedback
    :error="tasks.error.value || action.error.value"
    :success="action.success.value"
    :loading="tasks.loading.value"
  />
  <section class="panel">
    <div class="panel-heading compact-heading">
      <div>
        <h2>任务配置</h2>
        <p>按名称查询，维护任务配置和运行入口。</p>
      </div>
      <input
        v-model="taskSearch"
        class="compact-search"
        type="search"
        aria-label="查询任务"
        placeholder="查询任务名称"
      />
    </div>
    <div v-if="tasks.result.value?.items.length" class="table-scroll">
      <table class="task-table responsive-table">
        <thead>
          <tr>
            <th>任务</th>
            <th>状态</th>
            <th>操作模式</th>
            <th>执行上限</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="task in tasks.result.value.items" :key="task.id">
            <td class="strong" data-label="任务">
              {{ task.name }}
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(task.project_id) }}
              </small>
              <small class="subline muted">
                {{
                  directoryTask(task)
                    ? '读取执行电脑上的文件夹'
                    : task.requires_input
                      ? '需要输入文件'
                      : '无需输入文件'
                }}
              </small>
            </td>
            <td data-label="状态">
              <StatusBadge :value="task.enabled ? 'enabled' : 'disabled'" />
            </td>
            <td class="mono" data-label="操作模式">{{ task.operation }}</td>
            <td data-label="执行上限">{{ task.timeout_seconds }} 秒</td>
            <td data-label="操作">
              <div class="row-actions">
                <button
                  v-if="!session.projectId"
                  class="button small"
                  @click="session.projectId = task.project_id"
                >
                  进入项目
                </button>
                <button
                  v-if="canOperate"
                  class="button primary small"
                  :disabled="!task.enabled || action.pending.value"
                  @click="prepareRun(task)"
                >
                  立即运行
                </button>
                <button
                  v-if="canOperate"
                  class="button small"
                  :disabled="action.pending.value"
                  @click="prepareSchedules(task)"
                >
                  定时运行
                </button>
                <RouterLink
                  class="button small"
                  :to="{
                    path: '/runs',
                    query: { project_id: task.project_id, task_id: task.id },
                  }"
                >
                  运行记录
                </RouterLink>
                <button
                  v-if="canDevelop"
                  class="button small"
                  :disabled="action.pending.value"
                  @click="prepareEdit(task)"
                >
                  编辑
                </button>
                <RouterLink
                  v-if="canManage"
                  class="button small"
                  to="/notification-rules"
                >
                  通知规则
                </RouterLink>
                <button
                  v-if="canDevelop"
                  class="button small"
                  :disabled="action.pending.value"
                  @click="toggle(task)"
                >
                  {{ task.enabled ? '停用' : '启用' }}
                </button>
                <button
                  v-if="canDevelop"
                  class="button danger small"
                  :disabled="action.pending.value"
                  @click="deleteTaskCandidate = task"
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
      v-else-if="!tasks.loading.value && !tasks.error.value"
      title="尚未创建任务"
      description="先发布一个程序版本，再配置任务及其业务参数。"
    />
    <Pagination
      v-if="tasks.result.value"
      v-model:offset="tasks.offset.value"
      :total="tasks.result.value.total"
    />
  </section>
  <Modal
    v-if="createOpen"
    :title="editingTask ? `编辑任务 · ${editingTask.name}` : '创建任务'"
    wide
    :busy="action.pending.value"
    @close="closeTaskForm"
  >
    <form @submit.prevent="saveTask">
      <Feedback :error="action.error.value" />
      <Feedback
        :error="versionOptions.error.value"
        :loading="versionOptions.loading.value"
      />
      <div class="form-grid">
        <label>
          任务名称
          <input v-model="name" required maxlength="100" />
        </label>
        <label>
          程序
          <select v-model="programId" required>
            <option value="" disabled>请选择程序</option>
            <option
              v-for="program in programs"
              :key="program.id"
              :value="program.id"
            >
              {{ program.name }}
            </option>
          </select>
        </label>
        <label>
          执行电脑
          <select
            v-model="nodeId"
            aria-label="执行电脑"
            :required="isDirectory"
          >
            <option value="">
              {{ isDirectory ? '请选择固定执行电脑' : '自动匹配可用节点' }}
            </option>
            <option
              v-for="node in selectableNodes"
              :key="node.id"
              :value="node.id"
            >
              {{ node.name }}
            </option>
          </select>
          <small v-if="isDirectory && !selectableNodes.length">
            暂无支持目录输入的电脑，请先在执行电脑配置允许读取的文件夹根目录。
          </small>
        </label>
      </div>
      <p v-if="versionId" class="muted">
        使用版本：{{
          versions.find((version) => version.id === versionId)?.name
        }}。默认选择可用版本，任务创建后固定使用。
      </p>
      <DirectoryFields
        v-if="isDirectory"
        v-model:input-dir="inputDir"
        v-model:batch-key="batchKey"
        :disabled="action.pending.value"
      />
      <ParameterFields
        v-model="parameters"
        :schema="selectedParameterSchema"
        :label="isDirectory ? '其他默认业务参数' : '默认业务参数'"
      />
      <details class="advanced-options">
        <summary>高级设置 · 版本与执行规则</summary>
        <div class="form-grid">
          <label>
            已发布版本
            <select v-model="versionId" required>
              <option value="" disabled>请选择程序版本</option>
              <option
                v-for="version in versions"
                :key="version.id"
                :value="version.id"
              >
                {{ version.programName }} / {{ version.name }}
              </option>
            </select>
          </label>
          <label>
            最长执行时间（秒）
            <input
              v-model.number="timeout"
              type="number"
              min="1"
              max="86400"
              required
            />
          </label>
          <label>
            操作模式
            <input v-model="operation" required maxlength="100" />
          </label>
          <label>
            互斥资源（逗号分隔）
            <input v-model="resources" placeholder="例如：billing-upload" />
          </label>

          <label>
            业务去重参数（逗号分隔，可选）
            <input
              v-model="businessFields"
              placeholder="例如：period,shop_id"
            />
            <small>
              无文件任务按这些参数区分业务批次。不填时仅按请求编号去重；有输入文件的任务默认按批次去重。
            </small>
          </label>
        </div>
      </details>
      <label v-if="!isDirectory" class="checkbox">
        <input v-model="requiresInput" type="checkbox" />
        运行前需要输入文件
      </label>
      <p v-if="!versions.length" class="feedback warning">
        暂无已发布版本，请先到程序管理中上传程序包并发布。
      </p>
      <div class="form-actions">
        <button
          class="button primary"
          :disabled="action.pending.value || !versions.length"
        >
          {{ editingTask ? '保存修改' : '创建任务' }}
        </button>
      </div>
    </form>
  </Modal>
  <Modal
    v-if="scheduleTask"
    :title="`定时运行 · ${scheduleTask.name}`"
    wide
    :busy="action.pending.value"
    @close="scheduleTask = null"
  >
    <Feedback :error="action.error.value" :success="action.success.value" />
    <template v-if="!scheduleFormOpen">
      <div class="modal-section-heading">
        <div>
          <h3>已配置的计划</h3>
          <p class="muted">
            一个任务可以添加多个时间计划，每个计划保存自己的运行参数。
          </p>
        </div>
        <button
          v-if="canOperate"
          class="button primary small"
          type="button"
          @click="prepareScheduleCreate"
        >
          ＋ 添加定时计划
        </button>
      </div>
      <div v-if="taskSchedules.length" class="table-scroll schedule-list">
        <table>
          <thead>
            <tr>
              <th>计划</th>
              <th>时间</th>
              <th>下次运行</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="schedule in taskSchedules" :key="schedule.id">
              <td class="strong">{{ schedule.name }}</td>
              <td>
                <span class="mono">{{ schedule.cron }}</span>
                <small class="subline">{{ schedule.timezone }}</small>
              </td>
              <td class="numeric">{{ formatTime(schedule.next_fire) }}</td>
              <td>
                <StatusBadge
                  :value="schedule.enabled ? 'enabled' : 'disabled'"
                />
              </td>
              <td>
                <div class="row-actions">
                  <button
                    v-if="canOperate"
                    class="button small"
                    :disabled="action.pending.value"
                    @click="prepareScheduleEdit(schedule)"
                  >
                    编辑
                  </button>
                  <button
                    v-if="canOperate"
                    class="button small"
                    :disabled="action.pending.value"
                    @click="toggleSchedule(schedule)"
                  >
                    {{ schedule.enabled ? '暂停' : '启用' }}
                  </button>
                  <button
                    v-if="canOperate"
                    class="button danger small"
                    :disabled="action.pending.value"
                    @click="deleteScheduleCandidate = schedule"
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
        v-else
        title="尚未添加定时计划"
        description="点击“添加定时计划”，设置运行时间和本次计划使用的参数。"
      />
    </template>
    <form v-else @submit.prevent="saveSchedule">
      <div class="form-grid">
        <label>
          计划名称
          <input v-model="scheduleName" required maxlength="100" />
        </label>
        <label>
          运行频率
          <select v-model="schedulePattern">
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
            <option value="advanced">自定义 Cron</option>
          </select>
        </label>
        <label v-if="schedulePattern !== 'advanced'">
          北京时间
          <input v-model="scheduleTime" type="time" required />
        </label>
        <label v-if="schedulePattern === 'weekly'">
          星期
          <select v-model="scheduleWeekday">
            <option value="1">星期一</option>
            <option value="2">星期二</option>
            <option value="3">星期三</option>
            <option value="4">星期四</option>
            <option value="5">星期五</option>
            <option value="6">星期六</option>
            <option value="0">星期日</option>
          </select>
        </label>
        <label v-if="schedulePattern === 'monthly'">
          每月日期
          <input
            v-model.number="scheduleDay"
            type="number"
            min="1"
            max="31"
            required
          />
        </label>
        <label v-if="schedulePattern === 'advanced'">
          五字段 Cron
          <input v-model="scheduleAdvancedCron" required class="mono" />
        </label>
        <label>
          错过执行时间
          <select v-model="scheduleMisfire">
            <option value="skip">跳过错过的时间</option>
            <option value="once">仅补最近一次</option>
          </select>
        </label>
        <label v-if="!directoryTask(scheduleTask)">
          输入来源
          <select v-model="scheduleInputMode" aria-label="定时输入来源">
            <option value="none">不绑定输入</option>
            <option value="fixed">固定输入批次</option>
            <option value="label">按标签等待批次</option>
          </select>
        </label>
        <label
          v-if="!directoryTask(scheduleTask) && scheduleInputMode === 'fixed'"
        >
          固定输入批次
          <select v-model="scheduleBatchId" required>
            <option value="" disabled>请选择批次</option>
            <option v-for="batch in batches" :key="batch.id" :value="batch.id">
              {{ batch.label }}
            </option>
          </select>
        </label>
        <label
          v-if="!directoryTask(scheduleTask) && scheduleInputMode === 'label'"
        >
          等待的批次标签
          <input
            v-model="scheduleInputLabel"
            required
            maxlength="100"
            placeholder="${scheduled_date}"
          />
        </label>
      </div>
      <DirectoryFields
        v-if="directoryTask(scheduleTask)"
        v-model:input-dir="scheduleInputDir"
        v-model:batch-key="scheduleBatchKey"
        scheduled
        :disabled="action.pending.value"
      />
      <p v-if="directoryTask(scheduleTask)" class="feedback warning">
        批次编号用于防止同一批数据重复执行。周期计划建议使用
        <code>${scheduled_date}</code>
        ；参数和程序版本都相同时会关联已有运行；其他参数不同则会报告业务批次冲突，均不会重复执行。
      </p>
      <ParameterFields
        v-model="scheduleParameters"
        :schema="scheduleTask.parameter_schema"
        label="本计划的运行参数"
        scheduled
      />
      <p class="muted">
        <small>
          时间变量按计划触发时间计算：
          <code>${scheduled_date}</code>
          表示计划日期，
          <code>${previous_month}</code>
          表示上个月。变量必须作为一个完整的字符串值使用。
        </small>
      </p>
      <p class="form-caption">
        实际规则：
        <code>{{ scheduleCron }}</code>
        · Asia/Shanghai。新计划先保存为关闭状态。
      </p>
      <div class="form-actions split-actions">
        <button class="button" type="button" @click="scheduleFormOpen = false">
          返回计划列表
        </button>
        <button class="button primary" :disabled="action.pending.value">
          {{ editingSchedule ? '保存计划修改' : '保存定时计划' }}
        </button>
      </div>
    </form>
  </Modal>
  <Modal
    v-if="deleteTaskCandidate"
    :title="`删除任务 · ${deleteTaskCandidate.name}`"
    :busy="action.pending.value"
    @close="deleteTaskCandidate = null"
  >
    <Feedback :error="action.error.value" />
    <p>
      删除后不能恢复。已有运行记录、定时计划或事件调用方引用的任务不会被删除，系统会提示先处理依赖。
    </p>
    <div class="form-actions">
      <button class="button" type="button" @click="deleteTaskCandidate = null">
        取消
      </button>
      <button
        class="button danger"
        type="button"
        :disabled="action.pending.value"
        @click="deleteTask"
      >
        确认删除
      </button>
    </div>
  </Modal>
  <Modal
    v-if="deleteScheduleCandidate"
    :title="`删除计划 · ${deleteScheduleCandidate.name}`"
    :busy="action.pending.value"
    @close="deleteScheduleCandidate = null"
  >
    <Feedback :error="action.error.value" />
    <p>删除后该计划不再触发；已经产生或关联的运行记录仍保留在运行中心。</p>
    <div class="form-actions">
      <button
        class="button"
        type="button"
        @click="deleteScheduleCandidate = null"
      >
        取消
      </button>
      <button
        class="button danger"
        type="button"
        :disabled="action.pending.value"
        @click="deleteSchedule"
      >
        确认删除
      </button>
    </div>
  </Modal>
  <Modal
    v-if="runTask"
    :title="`运行 · ${runTask.name}`"
    :busy="action.pending.value || runDelivery.pending.value"
    @close="closeRun"
  >
    <form @submit.prevent="startRun">
      <Feedback :error="action.error.value" />
      <Feedback
        :error="runDelivery.error.value"
        :success="runDelivery.success.value"
      />
      <fieldset
        class="run-fields"
        :disabled="action.pending.value || runDelivery.pending.value"
      >
        <DirectoryFields
          v-if="directoryTask(runTask)"
          v-model:input-dir="runInputDir"
          v-model:batch-key="runBatchKey"
          :disabled="action.pending.value"
        />
        <template v-else>
          <label>
            输入文件
            <select v-model="runInputMode" aria-label="本次输入方式">
              <option value="none" :disabled="runTask.requires_input">
                不使用文件
              </option>
              <option value="upload">直接拖入或选择文件</option>
              <option value="existing">选择已有文件批次</option>
            </select>
          </label>
          <label v-if="runInputMode === 'existing'">
            已有输入批次
            <select v-model="batchId" required>
              <option value="" disabled>请选择已就绪批次</option>
              <option
                v-for="batch in batches"
                :key="batch.id"
                :value="batch.id"
                :disabled="!batch.ready"
              >
                {{ batch.label }}{{ batch.ready ? '' : '（尚未就绪）' }}
              </option>
            </select>
          </label>
          <section v-if="runInputMode === 'upload'" class="run-upload">
            <label>
              本次文件名称
              <input
                v-model="runDelivery.label.value"
                required
                maxlength="100"
                placeholder="例如：2026年9月账单"
              />
            </label>
            <label
              class="run-drop-zone"
              @dragover.prevent
              @drop.prevent="dropRunFiles"
            >
              <strong>拖文件到这里</strong>
              <span>或点击选择，可一次选择多个文件</span>
              <input
                type="file"
                multiple
                aria-label="直接上传运行文件"
                @change="chooseRunFiles"
              />
            </label>
            <ul v-if="runDelivery.files.value.length" class="run-file-list">
              <li
                v-for="(entry, index) in runDelivery.files.value"
                :key="entry.file.name"
              >
                <span>{{ entry.file.name }} · {{ entry.file.size }} 字节</span>
                <button
                  v-if="entry.state === 'pending'"
                  type="button"
                  class="text-link"
                  @click="runDelivery.remove(index)"
                >
                  移除
                </button>
                <span v-else class="muted">{{ entry.state }}</span>
              </li>
            </ul>
            <p v-if="runDelivery.stage.value" role="status">
              {{ runDelivery.stage.value }}
            </p>
          </section>
        </template>
        <ParameterFields
          v-model="runParameters"
          :schema="runTask.parameter_schema"
          :label="directoryTask(runTask) ? '其他业务参数' : '本次业务参数'"
        />
      </fieldset>
      <p class="muted">
        提交后进入运行队列。结果请在运行详情中查看；网络重试会沿用同一请求标识。
      </p>
      <div class="form-actions">
        <button
          class="button primary"
          :disabled="action.pending.value || runDelivery.pending.value"
        >
          {{
            action.pending.value || runDelivery.pending.value
              ? '提交中…'
              : '确认运行'
          }}
        </button>
      </div>
    </form>
  </Modal>
</template>
<style scoped>
.compact-search {
  width: min(300px, 100%);
  margin: 0;
}
.modal-section-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 18px;
}
.modal-section-heading p {
  margin: 0;
}
.schedule-list {
  border: 1px solid var(--line);
  border-radius: 12px;
}
.split-actions {
  justify-content: space-between;
}
.result-toggle {
  display: flex;
  align-items: center;
  gap: 10px;
}
.result-toggle input {
  width: auto;
  margin: 0;
}
.run-upload {
  display: grid;
  gap: 12px;
}
.run-fields {
  display: grid;
  gap: 14px;
  min-width: 0;
  margin: 0;
  padding: 0;
  border: 0;
}
.run-drop-zone {
  position: relative;
  display: grid;
  place-items: center;
  min-height: 124px;
  border: 1px dashed #7aa2f7;
  border-radius: 14px;
  background: #f4f7ff;
  color: #244780;
  cursor: pointer;
  text-align: center;
}
.run-drop-zone input {
  position: absolute;
  inset: 0;
  width: 100%;
  opacity: 0;
  cursor: pointer;
}
.run-drop-zone span {
  color: var(--muted);
  font-size: 0.84rem;
}
.run-file-list {
  display: grid;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.run-file-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}
@media (max-width: 640px) {
  .compact-heading {
    align-items: stretch;
  }
  .task-table {
    min-width: 0;
    table-layout: fixed;
  }
  .task-table th:nth-child(3),
  .task-table td:nth-child(3),
  .task-table th:nth-child(4),
  .task-table td:nth-child(4) {
    display: none;
  }
  .task-table th:first-child {
    width: 42%;
  }
  .task-table th:nth-child(2) {
    width: 24%;
  }
  .task-table td,
  .task-table th {
    padding: 14px 10px;
    overflow-wrap: anywhere;
  }
  .task-table .row-actions {
    flex-direction: column;
    align-items: stretch;
  }
  .task-table .button {
    padding-inline: 8px;
  }
}
</style>
