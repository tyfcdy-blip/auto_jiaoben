<script setup lang="ts">
import { ref, watch } from 'vue'
import { session, canOperate, projectName } from '../session'
import { request } from '../api'
import { useAction, useList, formatTime } from '../composables'
import type { Alert } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
const alerts = useList<Alert>('/alerts'),
  action = useAction()
const selected = ref<Alert | null>(null),
  alertAction = ref<'ack' | 'close'>('ack'),
  note = ref('')
watch(
  () => session.projectId,
  () => (selected.value = null),
)
function selectAlert(alert: Alert, mode: 'ack' | 'close') {
  selected.value = alert
  alertAction.value = mode
  note.value = ''
}
async function acknowledge() {
  await action.perform(
    async () => {
      await request(`/alerts/${selected.value!.id}/${alertAction.value}`, {
        method: 'POST',
        body: { note: note.value.trim() },
      })
      selected.value = null
      note.value = ''
      await alerts.refresh()
    },
    alertAction.value === 'close'
      ? '告警已关闭，处理记录已保留'
      : '告警已确认，确认不代表故障已恢复',
  )
}
const kindLabels: Record<string, string> = {
  run_failed: '执行失败',
  node_offline: '节点离线',
  run_unknown: '结果待核对',
  schedule_delay: '调度延迟',
  schedule_delayed: '调度延迟',
  schedule_failed: '定时计划失败',
  schedule_misfire: '定时计划错过',
  partial: '部分完成',
  start_rejected: '启动或受理失败',
  recovery: '故障已恢复',
  timeout: '执行超时',
  startup_failed: '启动失败',
  failed: '执行失败',
  unknown: '结果待核对',
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">OPERATIONS / ALERTS</p>
      <h1>告警中心</h1>
      <p>处理执行与调度异常；发送情况请到消息记录核对。</p>
    </div>
    <button
      class="button"
      :disabled="alerts.loading.value"
      @click="alerts.refresh"
    >
      刷新告警
    </button>
  </header>
  <Feedback
    :error="alerts.error.value || action.error.value"
    :success="action.success.value"
    :loading="alerts.loading.value"
  />
  <section class="panel">
    <div v-if="alerts.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>异常</th>
            <th>等级</th>
            <th>处理状态</th>
            <th>消息投递</th>
            <th>发生时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="alert in alerts.result.value.items" :key="alert.id">
            <td>
              <strong>{{ kindLabels[alert.kind] || alert.kind }}</strong>
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(alert.project_id) }}
              </small>
              <span class="subline">{{ alert.message }}</span>
              <RouterLink
                v-if="alert.run_id"
                class="text-link subline"
                :to="`/runs/${alert.run_id}`"
              >
                查看关联运行 →
              </RouterLink>
            </td>
            <td><StatusBadge :value="alert.severity || 'warning'" /></td>
            <td><StatusBadge :value="alert.status" /></td>
            <td><StatusBadge :value="alert.delivery_state" /></td>
            <td class="numeric">{{ formatTime(alert.created_at) }}</td>
            <td>
              <div class="row-actions">
                <button
                  v-if="canOperate && alert.status === 'open'"
                  class="button small"
                  @click="selectAlert(alert, 'ack')"
                >
                  确认告警
                </button>
                <button
                  v-if="
                    canOperate &&
                    ['open', 'acknowledged', 'recovered'].includes(alert.status)
                  "
                  class="button small"
                  @click="selectAlert(alert, 'close')"
                >
                  关闭告警
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!alerts.loading.value && !alerts.error.value"
      title="当前没有告警记录"
      description="执行失败、失联和调度异常会在这里留下处理记录。"
    />
    <Pagination
      v-if="alerts.result.value"
      v-model:offset="alerts.offset.value"
      :total="alerts.result.value.total"
    />
  </section>
  <Modal
    v-if="selected"
    :title="alertAction === 'close' ? '关闭告警' : '确认告警'"
    :busy="action.pending.value"
    @close="selected = null"
  >
    <form @submit.prevent="acknowledge">
      <p>{{ selected.message }}</p>
      <p v-if="alertAction === 'close'" class="form-caption">
        关闭表示本次告警处理结束，不会停止关联任务，也不代表撤销外部业务操作。
      </p>
      <Feedback :error="action.error.value" />
      <label>
        {{ alertAction === 'close' ? '关闭说明' : '处理说明' }}
        <textarea
          v-model="note"
          rows="4"
          required
          maxlength="2000"
          :placeholder="
            alertAction === 'close'
              ? '说明关闭依据及最终处理结论'
              : '说明由谁跟进，以及接下来的核查措施'
          "
        ></textarea>
      </label>
      <div class="form-actions">
        <button class="button primary" :disabled="action.pending.value">
          {{ alertAction === 'close' ? '关闭并记录' : '确认并记录' }}
        </button>
      </div>
    </form>
  </Modal>
</template>
