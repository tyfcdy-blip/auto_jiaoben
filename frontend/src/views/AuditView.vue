<script setup lang="ts">
import { projectName, session } from '../session'
import { useList, formatTime } from '../composables'
import type { Audit } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Pagination from '../components/Pagination.vue'
const audit = useList<Audit>('/audit')
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">AUDIT TRAIL</p>
      <h1>操作审计</h1>
      <p>关键操作由控制服务独立记录，不以脚本日志代替人员操作证据。</p>
    </div>
    <button
      class="button"
      :disabled="audit.loading.value"
      @click="audit.refresh"
    >
      刷新记录
    </button>
  </header>
  <Feedback :error="audit.error.value" :loading="audit.loading.value" />
  <section class="panel">
    <div v-if="audit.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>时间</th>
            <th>操作者</th>
            <th>操作</th>
            <th>关联资源</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="event in audit.result.value.items" :key="event.id">
            <td class="numeric">{{ formatTime(event.created_at) }}</td>
            <td>{{ event.actor }}</td>
            <td class="mono">
              {{ event.action }}
              <small
                v-if="!session.projectId && event.project_id"
                class="subline project-label"
              >
                {{ projectName(event.project_id) }}
              </small>
            </td>
            <td class="mono compact-id">{{ event.resource_id || '暂无' }}</td>
            <td>{{ event.note || '暂无' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!audit.loading.value && !audit.error.value"
      title="暂无可见审计记录"
      description="版本发布、授权调整、任务触发等关键操作会按权限显示。"
    />
    <Pagination
      v-if="audit.result.value"
      v-model:offset="audit.offset.value"
      :total="audit.result.value.total"
    />
  </section>
</template>
