<script setup lang="ts">
import { session, projectName } from '../session'
import { useList, formatTime } from '../composables'
import type { AlertDelivery, ResultNotification } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
const alertDeliveries = useList<AlertDelivery>('/alert-deliveries')
const resultNotifications = useList<ResultNotification>('/result-notifications')
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">NOTIFICATIONS / HISTORY</p>
      <h1>消息记录</h1>
      <p>
        告警和脚本业务消息统一留痕。连接测试成功只证明机器人当时可用；任务运行后才会生成业务消息。
      </p>
    </div>
  </header>
  <section class="panel">
    <header class="panel-heading">
      <div>
        <h2>告警投递记录</h2>
        <p>每条记录对应一个告警和一个钉钉连接，可核对发送与重试状态。</p>
      </div>
    </header>
    <Feedback
      :error="alertDeliveries.error.value"
      :loading="alertDeliveries.loading.value"
    />
    <div v-if="alertDeliveries.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>告警</th>
            <th>接收连接</th>
            <th>投递状态</th>
            <th>发生时间</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="delivery in alertDeliveries.result.value.items"
            :key="delivery.id"
          >
            <td>
              <span class="mono">{{ delivery.alert_id }}</span>
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(delivery.project_id) }}
              </small>
            </td>
            <td>{{ delivery.channel_name }}</td>
            <td>
              <StatusBadge :value="delivery.state" />
              <small v-if="delivery.attempts" class="subline muted">
                已尝试 {{ delivery.attempts }} 次
              </small>
              <small v-if="delivery.last_error" class="subline muted">
                {{ delivery.last_error }}
              </small>
            </td>
            <td class="numeric">{{ formatTime(delivery.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!alertDeliveries.loading.value && !alertDeliveries.error.value"
      title="暂无告警投递记录"
      description="产生告警且命中通知规则后，平台会在这里记录每个接收连接的投递状态。"
    />
    <Pagination
      v-if="alertDeliveries.result.value"
      v-model:offset="alertDeliveries.offset.value"
      :total="alertDeliveries.result.value.total"
    />
  </section>
  <section class="panel section-gap">
    <header class="panel-heading">
      <div>
        <h2>脚本运行通知</h2>
        <p>
          任务成功后的脚本消息或结果摘要。每条记录固定到产生时选择的机器人；以后改连接不会自动补发旧消息，也不会重跑业务脚本。
        </p>
      </div>
      <button
        class="button"
        :disabled="resultNotifications.loading.value"
        @click="resultNotifications.refresh"
      >
        刷新记录
      </button>
    </header>
    <Feedback
      :error="resultNotifications.error.value"
      :loading="resultNotifications.loading.value"
    />
    <div
      v-if="resultNotifications.result.value?.items.length"
      class="table-scroll"
    >
      <table>
        <thead>
          <tr>
            <th>任务与运行</th>
            <th>发送内容</th>
            <th>钉钉投递</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="entry in resultNotifications.result.value.items"
            :key="entry.id"
          >
            <td>
              <RouterLink class="text-link" :to="`/runs/${entry.run_id}`">
                查看运行 →
              </RouterLink>
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(entry.project_id) }}
              </small>
              <small class="subline muted mono">任务 {{ entry.task_id }}</small>
              <small class="subline muted">
                接收连接：{{ entry.channel_name }}
              </small>
            </td>
            <td>
              <details>
                <summary>查看发送内容</summary>
                <pre class="notification-content">{{ entry.content }}</pre>
              </details>
            </td>
            <td>
              <StatusBadge :value="entry.delivery_state" />
              <small v-if="entry.attempts" class="subline muted">
                已尝试 {{ entry.attempts }} 次
              </small>
              <small v-if="entry.last_error" class="subline muted">
                {{ entry.last_error }}
              </small>
            </td>
            <td class="numeric">{{ formatTime(entry.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="
        !resultNotifications.loading.value && !resultNotifications.error.value
      "
      title="暂无结果通知"
      description="在通知规则中开启任务成功消息后，发送记录会出现在这里。"
    />
    <Pagination
      v-if="resultNotifications.result.value"
      v-model:offset="resultNotifications.offset.value"
      :total="resultNotifications.result.value.total"
    />
  </section>
</template>
<style scoped>
.notification-content {
  max-width: 48ch;
  margin: 10px 0 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
</style>
