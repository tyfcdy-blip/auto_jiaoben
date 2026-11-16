<script setup lang="ts">
import { ref, watch } from 'vue'
import { session, canManage, projectName } from '../session'
import { ApiError, errorMessage, loadChoices, request } from '../api'
import { useAction, useResource, projectPath } from '../composables'
import type {
  Channel,
  ChannelAlertPolicy,
  Page,
  TaskNotificationRule,
  TaskResultNotificationConfig,
} from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
const props = withDefaults(defineProps<{ section?: 'channels' | 'rules' }>(), {
  section: 'channels',
})
function sectionList<T>(path: string, section: string | string[]) {
  const offset = ref(0)
  watch(
    () => session.projectId,
    () => (offset.value = 0),
  )
  const sections = Array.isArray(section) ? section : [section]
  return {
    ...useResource<Page<T>>(() =>
      sections.includes(props.section) &&
      (session.projectId || session.projects.length)
        ? projectPath(path, offset.value)
        : null,
    ),
    offset,
  }
}
const channels = sectionList<Channel>('/channels', ['channels', 'rules'])
const tasks = sectionList<TaskNotificationRule>(
  '/task-notification-rules',
  'rules',
)
const action = useAction()
const testMessageId = ref('')
const channelOpen = ref(false),
  channelName = ref(''),
  webhook = ref(''),
  secret = ref('')
const policyChannel = ref<Channel | null>(null),
  policy = ref<ChannelAlertPolicy | null>(null)
const selectedKinds = ref<string[]>([]),
  policyLoading = ref(false),
  policyError = ref('')
const resultTask = ref<TaskNotificationRule | null>(null),
  resultConfig = ref<TaskResultNotificationConfig | null>(null)
const resultChannels = ref<Channel[]>([]),
  resultEnabled = ref(false),
  resultChannelId = ref('')
let policyRequest = 0,
  resultRequest = 0
const alertKinds = [
  { kind: 'unknown', label: '结果待核对', severity: 'critical' },
  { kind: 'partial', label: '部分完成', severity: 'critical' },
  { kind: 'schedule_misfire', label: '定时计划错过', severity: 'critical' },
  { kind: 'schedule_failed', label: '定时计划失败', severity: 'critical' },
  { kind: 'failed', label: '执行失败', severity: 'warning' },
  { kind: 'start_rejected', label: '启动或受理失败', severity: 'warning' },
  { kind: 'node_offline', label: '执行电脑离线', severity: 'warning' },
  { kind: 'schedule_delayed', label: '调度延迟', severity: 'warning' },
] as const
watch([() => session.projectId, () => props.section], () => {
  testMessageId.value = ''
  closePolicy()
  closeResultConfig()
  closeChannel()
})
function closeResultConfig() {
  resultRequest++
  resultTask.value = null
  resultConfig.value = null
  resultChannels.value = []
  resultEnabled.value = false
  resultChannelId.value = ''
}
async function openResultConfig(task: TaskNotificationRule) {
  const current = ++resultRequest
  const currentProject = session.projectId
  await action.perform(async () => {
    const [config, choices] = await Promise.all([
      request<TaskResultNotificationConfig>(
        `/tasks/${task.id}/result-notification`,
      ),
      loadChoices<Channel>(
        `/channels?project_id=${encodeURIComponent(task.project_id)}`,
      ),
    ])
    if (
      current !== resultRequest ||
      currentProject !== session.projectId ||
      task.project_id !== currentProject
    )
      return
    resultTask.value = task
    resultConfig.value = config
    resultEnabled.value = config.enabled
    resultChannelId.value = config.channel_id || ''
    resultChannels.value = choices.items
  }, '')
}
async function saveResultConfig() {
  if (!resultTask.value || !resultConfig.value) return
  const task = resultTask.value
  await action.perform(async () => {
    if (resultEnabled.value && !resultChannelId.value)
      throw new Error('启用脚本消息前，请选择一个钉钉连接。')
    try {
      await request(`/tasks/${task.id}/result-notification`, {
        method: 'PUT',
        body: {
          enabled: resultEnabled.value,
          channel_id: resultChannelId.value || null,
          base_revision: resultConfig.value!.revision,
        },
      })
      closeResultConfig()
      await tasks.refresh()
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        const current = await request<TaskResultNotificationConfig>(
          `/tasks/${task.id}/result-notification`,
        )
        resultConfig.value = current
        resultEnabled.value = current.enabled
        resultChannelId.value = current.channel_id || ''
        throw new Error('通知规则已被其他人修改，已刷新最新配置，请重新确认。')
      }
      throw failure
    }
  }, '任务运行通知规则已保存')
}
function closePolicy() {
  policyRequest++
  policyChannel.value = null
  policy.value = null
  policyLoading.value = false
  policyError.value = ''
  selectedKinds.value = []
}
async function loadPolicy(channel: Channel) {
  const current = ++policyRequest
  policyLoading.value = true
  policyError.value = ''
  try {
    const loaded = await request<ChannelAlertPolicy>(
      `/channels/${channel.id}/policy`,
    )
    if (current !== policyRequest) return
    policy.value = loaded
    selectedKinds.value = [...loaded.enabled_kinds]
  } catch (failure) {
    if (current === policyRequest) policyError.value = errorMessage(failure)
  } finally {
    if (current === policyRequest) policyLoading.value = false
  }
}
async function openPolicy(channel: Channel) {
  policyChannel.value = channel
  policy.value = null
  selectedKinds.value = []
  await loadPolicy(channel)
}
async function savePolicy() {
  if (!policyChannel.value || !policy.value) return
  const channel = policyChannel.value
  await action.perform(async () => {
    try {
      await request<ChannelAlertPolicy>(`/channels/${channel.id}/policy`, {
        method: 'PUT',
        body: {
          enabled_kinds: alertKinds
            .map(({ kind }) => kind)
            .filter((kind) => selectedKinds.value.includes(kind)),
          base_revision: policy.value!.revision,
        },
      })
      closePolicy()
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 409) {
        await loadPolicy(channel)
        throw new Error(
          '通知规则已被其他人修改，已刷新为最新配置，请重新确认后保存。',
        )
      }
      throw failure
    }
  }, '告警通知规则已保存')
}
function closeChannel() {
  channelOpen.value = false
  webhook.value = ''
  secret.value = ''
}
async function createChannel() {
  await action.perform(async () => {
    await request('/channels', {
      method: 'POST',
      body: {
        project_id: session.projectId,
        name: channelName.value.trim(),
        webhook_url: webhook.value.trim(),
        signing_secret: secret.value.trim() || null,
      },
    })
    closeChannel()
    channelName.value = ''
    await channels.refresh()
  }, '通知连接已登记')
  secret.value = ''
}
async function toggle(channel: Channel) {
  await action.perform(async () => {
    await request(`/channels/${channel.id}`, {
      method: 'PATCH',
      body: { enabled: !channel.enabled },
    })
    await channels.refresh()
  }, '通知连接状态已更新')
}
async function testChannel(channel: Channel) {
  const projectId = session.projectId
  const section = props.section
  testMessageId.value = ''
  await action.perform(async () => {
    const result = await request<{ sent: true; test_id: string }>(
      `/channels/${channel.id}/test-message`,
      { method: 'POST' },
    )
    if (session.projectId === projectId && props.section === section)
      testMessageId.value = result.test_id
  }, '钉钉接口已接收测试消息，请到对应群核对。')
  if (session.projectId !== projectId || props.section !== section) {
    action.success.value = ''
    action.error.value = ''
    testMessageId.value = ''
  }
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">NOTIFICATIONS / SETTINGS</p>
      <h1>{{ section === 'channels' ? '机器人连接' : '通知规则' }}</h1>
      <p>
        {{
          section === 'channels'
            ? '集中管理钉钉机器人；Webhook 和签名密钥不会交给业务脚本。'
            : '决定系统异常类型和任务成功后发给哪个群；消息内容由平台模板封装。'
        }}
      </p>
    </div>
  </header>
  <section class="panel">
    <header class="panel-heading">
      <div>
        <h2>{{ section === 'channels' ? '钉钉机器人' : '系统告警规则' }}</h2>
        <p>
          {{
            section === 'channels'
              ? '连接初始停用；可先发送一条测试消息核对群与加签，测试不受启停状态限制。'
              : '按机器人选择需要接收的异常类型及等级；故障恢复发给原接收群。'
          }}
        </p>
      </div>
      <button
        v-if="section === 'channels' && canManage && session.projectId"
        class="button"
        @click="channelOpen = true"
      >
        新增连接
      </button>
    </header>
    <Feedback
      :error="channels.error.value || action.error.value"
      :success="action.success.value"
      :loading="channels.loading.value"
    />
    <p v-if="action.success.value && testMessageId" class="muted">
      测试编号：{{ testMessageId }}
    </p>
    <div v-if="channels.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>连接名称</th>
            <th>状态</th>
            <th v-if="section === 'channels'">凭据</th>
            <th v-if="section === 'rules'">异常类型</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="channel in channels.result.value.items" :key="channel.id">
            <td class="strong">
              {{ channel.name }}
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(channel.project_id) }}
              </small>
            </td>
            <td>
              <StatusBadge :value="channel.enabled ? 'enabled' : 'disabled'" />
            </td>
            <td v-if="section === 'channels'" class="muted">
              受控保存，不提供回读
            </td>
            <td v-if="section === 'rules'">
              <button
                v-if="
                  canManage && (session.projectId || session.user?.is_admin)
                "
                class="button small"
                :disabled="action.pending.value"
                @click="openPolicy(channel)"
              >
                选择异常类型
              </button>
              <span v-else class="muted">按项目授权维护</span>
            </td>
            <td class="align-right">
              <button
                v-if="section === 'channels' && canManage && session.projectId"
                class="button small"
                :disabled="action.pending.value"
                @click="testChannel(channel)"
              >
                发送测试消息
              </button>
              <button
                v-if="section === 'channels' && canManage && session.projectId"
                class="button small"
                :disabled="action.pending.value"
                @click="toggle(channel)"
              >
                {{ channel.enabled ? '停用' : '启用' }}
              </button>
              <RouterLink
                v-if="section === 'channels'"
                class="text-link"
                to="/notification-rules"
              >
                配置通知规则 →
              </RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!channels.loading.value && !channels.error.value"
      title="尚未配置通知连接"
      description="先在机器人连接页登记获授权的钉钉群；业务脚本无需保存机器人凭据。"
    />
    <Pagination
      v-if="channels.result.value"
      v-model:offset="channels.offset.value"
      :total="channels.result.value.total"
    />
  </section>
  <section v-if="section === 'rules'" class="panel section-gap">
    <header class="panel-heading">
      <div>
        <h2>任务成功消息</h2>
        <p>
          按任务启用并选接收群。脚本写入 result.json 的
          notification_text；未提供时发送 summary。每次成功运行最多一条。
        </p>
      </div>
    </header>
    <Feedback
      :error="tasks.error.value || action.error.value"
      :success="action.success.value"
      :loading="tasks.loading.value"
    />
    <div v-if="tasks.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>任务</th>
            <th>消息条件</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="task in tasks.result.value.items" :key="task.id">
            <td class="strong">
              {{ task.name }}
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(task.project_id) }}
              </small>
            </td>
            <td>
              <StatusBadge
                :value="task.notification_enabled ? 'enabled' : 'disabled'"
              />
              <small class="subline muted">
                {{
                  task.notification_enabled
                    ? `接收连接：${task.channel_name || '未指定'}`
                    : '运行成功且结果为 success 时可发送'
                }}
              </small>
            </td>
            <td>
              <button
                v-if="canManage && session.projectId"
                class="button small"
                :disabled="action.pending.value"
                @click="openResultConfig(task)"
              >
                配置接收群
              </button>
              <span v-else class="muted">选择项目后可配置</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!tasks.loading.value && !tasks.error.value"
      title="暂无任务"
      description="先创建任务，再决定其成功消息是否发到钉钉群。"
    />
    <Pagination
      v-if="tasks.result.value"
      v-model:offset="tasks.offset.value"
      :total="tasks.result.value.total"
    />
  </section>
  <Modal
    v-if="section === 'rules' && policyChannel"
    :title="`告警通知规则 · ${policyChannel.name}`"
    :busy="action.pending.value"
    @close="closePolicy"
  >
    <form @submit.prevent="savePolicy">
      <Feedback
        :error="policyError || action.error.value"
        :loading="policyLoading"
      />
      <template v-if="policy">
        <p class="form-caption">
          仅勾选此连接需要接收的异常。恢复消息会发送给收到原故障的连接。
        </p>
        <p v-if="policy.legacy_all" class="feedback warning">
          旧连接目前接收全部异常；保存后会按下面所选类型过滤。
        </p>
        <div class="policy-grid">
          <label
            v-for="item in alertKinds"
            :key="item.kind"
            class="policy-option"
          >
            <input v-model="selectedKinds" type="checkbox" :value="item.kind" />
            <span>{{ item.label }}</span>
            <StatusBadge :value="item.severity" />
          </label>
        </div>
        <p class="form-caption">
          全部不选表示此连接不接收故障告警；任务结果通知仍单独配置。
        </p>
        <div class="form-actions">
          <button class="button primary" :disabled="action.pending.value">
            保存规则
          </button>
        </div>
      </template>
    </form>
  </Modal>
  <Modal
    v-if="section === 'channels' && channelOpen"
    title="新增钉钉通知连接"
    :busy="action.pending.value"
    @close="closeChannel"
  >
    <form @submit.prevent="createChannel">
      <Feedback :error="action.error.value" />
      <label>
        连接名称
        <input v-model="channelName" required maxlength="100" />
      </label>
      <label>
        机器人 Webhook
        <input
          v-model="webhook"
          type="url"
          required
          autocomplete="off"
          placeholder="由钉钉管理员提供的连接"
        />
      </label>
      <label>
        签名密钥（如需）
        <input v-model="secret" type="password" autocomplete="new-password" />
      </label>
      <p class="form-caption">
        仅填写获授权的通知群连接。启用后，平台会向该连接投递任务告警。
      </p>
      <div class="form-actions">
        <button class="button primary" :disabled="action.pending.value">
          保存连接
        </button>
      </div>
    </form>
  </Modal>
  <Modal
    v-if="section === 'rules' && resultTask"
    :title="`任务成功消息 · ${resultTask.name}`"
    :busy="action.pending.value"
    @close="closeResultConfig"
  >
    <form @submit.prevent="saveResultConfig">
      <Feedback :error="action.error.value" />
      <p class="form-caption">
        脚本只声明消息文本，不持有机器人凭据。平台在运行确认成功时按这里的规则入队，未填写
        notification_text 时使用
        summary。模板包含任务名和运行编号；失败运行由系统告警规则处理。
      </p>
      <label class="result-toggle">
        <input v-model="resultEnabled" type="checkbox" />
        成功后发送脚本消息
      </label>
      <label>
        接收连接
        <select v-model="resultChannelId" :required="resultEnabled">
          <option value="">请选择钉钉连接</option>
          <option
            v-for="channel in resultChannels"
            :key="channel.id"
            :value="channel.id"
          >
            {{ channel.name }}{{ channel.enabled ? '' : '（已停用）' }}
          </option>
        </select>
      </label>
      <p v-if="!resultChannels.length" class="form-caption">
        请先到机器人连接页新增并启用连接。
      </p>
      <p
        v-else-if="
          resultEnabled &&
          resultChannels.find((channel) => channel.id === resultChannelId)
            ?.enabled === false
        "
        class="feedback warning"
      >
        当前连接已停用，消息会记录为停用，不会在启用后补发。
      </p>
      <div class="form-actions">
        <button
          class="button primary"
          :disabled="action.pending.value || !resultConfig"
        >
          保存通知设置
        </button>
      </div>
    </form>
  </Modal>
</template>
<style scoped>
.policy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.policy-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  margin: 0;
  border: 1px solid var(--line);
  border-radius: 10px;
}
.policy-option input {
  width: auto;
  margin: 0;
}
.policy-option .badge {
  margin-left: auto;
}
@media (max-width: 640px) {
  .policy-grid {
    grid-template-columns: 1fr;
  }
}
</style>
