<script setup lang="ts">
import { ref, watch } from 'vue'
import { session, canManage } from '../session'
import { request } from '../api'
import { useAction, useList, formatTime } from '../composables'
import type { Node } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
import NodeConnectionForm from '../components/NodeConnectionForm.vue'
import NodeMaintenanceForm from '../components/NodeMaintenanceForm.vue'
const nodes = useList<Node>('/nodes', true),
  action = useAction(),
  open = ref(false),
  registering = ref(false),
  selected = ref<Node | null>(null),
  maintaining = ref(false)
function close() {
  open.value = false
  registering.value = false
  selected.value = null
  maintaining.value = false
}
watch(
  () => `${session.user?.id || ''}:${session.projectId}`,
  () => {
    if (registering.value) {
      session.notice =
        '原项目的电脑接入请求可能已提交，请返回原项目刷新核对后再接入。'
      close()
      return
    }
    open.value = false
  },
  { flush: 'sync' },
)
async function patch(
  node: Node,
  change: { enabled?: boolean; maintenance?: boolean },
) {
  await action.perform(async () => {
    await request(`/nodes/${node.id}`, { method: 'PATCH', body: change })
    await nodes.refresh()
  }, '电脑设置已更新')
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">EXECUTION NODES</p>
      <h1>执行电脑</h1>
      <p v-if="session.user?.is_admin">
        全平台电脑统一维护；项目切换不会改变电脑清单，项目授权在电脑配置中管理。
      </p>
      <p v-else>
        查看{{
          session.projectId ? '当前项目' : '所有授权项目'
        }}有权使用的电脑、在线状态和接单状态。
      </p>
    </div>
    <div class="toolbar">
      <button
        class="button"
        :disabled="nodes.loading.value"
        @click="nodes.refresh"
      >
        刷新
      </button>
      <button
        v-if="canManage"
        class="button primary"
        :disabled="!session.projectId || registering"
        @click="open = true"
      >
        ＋ 接入电脑
      </button>
    </div>
  </header>
  <Feedback
    :error="nodes.error.value || action.error.value"
    :success="action.success.value"
    :loading="nodes.loading.value"
  />
  <section class="panel">
    <div v-if="nodes.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>电脑</th>
            <th>连接状态</th>
            <th>接单状态</th>
            <th>最后联系</th>
            <th>接单管理</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="node in nodes.result.value.items" :key="node.id">
            <td class="strong">
              {{ node.name }}
              <details>
                <summary>技术详情</summary>
                <small class="subline mono">标识：{{ node.id }}</small>
                <small class="subline">
                  能力：{{ node.capabilities.join('、') || '未报告' }}
                </small>
                <small class="subline">
                  隔离方式：
                  <StatusBadge :value="node.isolation || 'development'" />
                </small>
                <small class="subline">
                  配置：
                  <StatusBadge
                    :value="node.config_status || 'legacy_unmanaged'"
                  />
                  v{{ node.applied_revision || 0 }}/{{
                    node.config_revision || 0
                  }}
                </small>
              </details>
            </td>
            <td><StatusBadge :value="node.online ? 'online' : 'offline'" /></td>
            <td>
              <StatusBadge
                :value="
                  node.dispatch_status || (node.online ? 'idle' : 'offline')
                "
              />
              <StatusBadge
                v-if="
                  node.capacity_status &&
                  node.capacity_status !== node.dispatch_status &&
                  node.online &&
                  node.enabled &&
                  !node.maintenance
                "
                :value="node.capacity_status"
              />
              <small class="subline">
                运行 {{ node.active_runs || 0 }}/{{ node.slots }} ·
                {{ node.dispatch_detail || `总容量 ${node.slots}` }}
              </small>
            </td>
            <td class="numeric">{{ formatTime(node.last_seen) }}</td>
            <td>
              <div
                v-if="session.user?.is_admin || canManage"
                class="row-actions"
              >
                <button
                  class="button small"
                  :disabled="action.pending.value"
                  @click="patch(node, { maintenance: !node.maintenance })"
                >
                  {{ node.maintenance ? '结束维护' : '进入维护' }}
                </button>
                <button
                  class="button small"
                  :disabled="action.pending.value"
                  @click="patch(node, { enabled: !node.enabled })"
                >
                  {{ node.enabled ? '停用' : '启用' }}
                </button>
                <button
                  v-if="session.user?.is_admin"
                  class="button small"
                  :disabled="action.pending.value"
                  :title="node.maintenance ? '' : '请先进入维护状态'"
                  @click="selected = node"
                >
                  维护电脑
                </button>
              </div>
              <small class="subline">
                {{
                  !node.enabled
                    ? '已停用'
                    : node.maintenance
                      ? '维护中，不接新任务'
                      : '允许接单'
                }}
              </small>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!nodes.loading.value && !nodes.error.value"
      title="尚未连接执行电脑"
      :description="
        session.user?.is_admin
          ? '尚未登记任何电脑。点击“接入电脑”，登记后即可统一维护项目授权和可操作目录。'
          : '当前项目尚未获授权使用执行电脑，请联系平台管理员。'
      "
    />
    <Pagination
      v-if="nodes.result.value"
      v-model:offset="nodes.offset.value"
      :total="nodes.result.value.total"
    />
  </section>
  <div class="overview-note">
    <span class="note-marker">i</span>
    <p>
      在线仅表示执行器可通信。开发环境不能执行有外部写入的任务；维护和停用不会自动撤销已经发生的业务操作。
    </p>
  </div>
  <Modal v-if="open" title="接入执行电脑" :busy="registering" @close="close">
    <NodeConnectionForm
      :key="session.projectId"
      :project-id="session.projectId"
      @busy="registering = $event"
      @registered="nodes.refresh"
      @close="close"
    />
  </Modal>
  <Modal
    v-if="selected"
    title="电脑与项目配置"
    wide
    :busy="maintaining"
    @close="close"
  >
    <NodeMaintenanceForm
      :node="selected"
      @busy="maintaining = $event"
      @saved="nodes.refresh"
      @close="close"
    />
  </Modal>
</template>
