<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ value: string }>()
const labels: Record<string, string> = {
  waiting_input: '等待输入',
  queued: '排队中',
  leased: '准备启动',
  running: '运行中',
  cancel_requested: '停止待确认',
  succeeded: '已成功',
  success: '已成功',
  failed: '失败',
  partial: '部分完成',
  unknown: '结果待核对',
  canceled: '已取消',
  online: '在线',
  offline: '离线',
  open: '待处理',
  acknowledged: '已确认',
  resolved: '已恢复',
  recovered: '已恢复',
  closed: '已关闭',
  pending: '待发送',
  retry: '等待重试',
  sending: '发送中',
  sent: '已发送',
  disabled: '未启用',
  enabled: '已启用',
  published: '已发布',
  draft: '未发布',
  ready: '已就绪',
  uploading: '交付中',
  complete: '完整',
  incomplete: '不完整',
  none: '无产物',
  development: '开发环境',
  os_user: '独立用户',
  applied: '已同步',
  legacy_unmanaged: '旧版执行器',
  idle: '空闲',
  busy: '忙碌',
  maintenance: '维护中',
  config_pending: '配置待同步',
  config_failed: '配置同步失败',
  agent_unsupported: '版本不兼容',
  manual: '手动触发',
  schedule: '定时触发',
  event: '事件触发',
  critical: '紧急',
  warning: '警告',
  info: '信息',
  filtered: '规则未投递',
}
const tone = computed(() =>
  [
    'failed',
    'offline',
    'unknown',
    'config_failed',
    'agent_unsupported',
    'critical',
  ].includes(props.value)
    ? 'danger'
    : [
          'partial',
          'open',
          'cancel_requested',
          'incomplete',
          'config_pending',
          'maintenance',
          'busy',
          'warning',
          'retry',
        ].includes(props.value)
      ? 'warning'
      : [
            'online',
            'succeeded',
            'success',
            'published',
            'ready',
            'sent',
            'enabled',
            'complete',
            'applied',
            'idle',
            'manual',
          ].includes(props.value)
        ? 'good'
        : ['running', 'leased', 'sending'].includes(props.value)
          ? 'active'
          : 'neutral',
)
</script>
<template>
  <span class="badge" :class="tone">
    <span class="badge-dot"></span>
    {{ labels[value] || value }}
  </span>
</template>
