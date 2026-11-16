<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { session, canOperate, projectName } from '../session'
import { useAction, useList, formatTime } from '../composables'
import { useBatchDelivery } from '../batchDelivery'
import { request } from '../api'
import type { Batch } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'

const batches = useList<Batch>('/batches')
const action = useAction()
const deleteCandidate = ref<Batch | null>(null)
const details = ref<Record<string, Batch>>({})
watch(
  () => `${session.user?.id || ''}:${session.projectId}`,
  () => {
    details.value = {}
  },
  { flush: 'sync' },
)
const delivery = useBatchDelivery((batch, created) => {
  details.value = { ...details.value, [batch.id]: batch }
  const page = batches.result.value
  if (page) {
    batches.result.value = {
      ...page,
      items: page.items.map((entry) => (entry.id === batch.id ? batch : entry)),
    }
  }
  // 总数和分页归服务端所有；列表刷新失败不改变已经确认的交付结果。
  if (created || batch.ready) void batches.refresh()
})
const fileRows = computed(() => {
  const remote = delivery.batch.value?.files || []
  if (delivery.batch.value?.ready)
    return remote.map((file) => ({
      ...file,
      status: '已交付',
      removeIndex: -1,
    }))
  const local = delivery.files.value
  return [
    ...remote
      .filter((file) => !local.some((entry) => entry.file.name === file.name))
      .map((file) => ({ ...file, status: '已上传', removeIndex: -1 })),
    ...local.map((entry, index) => ({
      name: entry.file.name,
      size: entry.file.size,
      removeIndex: entry.state === 'pending' ? index : -1,
      status: {
        pending: '待上传',
        uploading: '上传中',
        uploaded: '已上传',
        failed: '上传未完成',
      }[entry.state],
    })),
  ]
})
function choose(event: Event) {
  const input = event.target as HTMLInputElement
  delivery.choose(Array.from(input.files || []))
  input.value = ''
}
function close() {
  if (delivery.pending.value) return
  delivery.close()
  void batches.refresh()
}
async function deleteEmptyBatch() {
  const batch = deleteCandidate.value!
  await action.perform(async () => {
    await request(`/batches/${batch.id}`, { method: 'DELETE' })
    deleteCandidate.value = null
    await batches.refresh()
  }, '空文件批次已删除')
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">INPUT FILES</p>
      <h1>输入文件</h1>
      <p>选择本次所需的全部文件，提交后即可供任务使用。</p>
    </div>
    <div class="row-actions">
      <button
        class="button"
        :disabled="delivery.pending.value || batches.loading.value"
        @click="batches.refresh"
      >
        刷新列表
      </button>
      <button
        v-if="canOperate"
        class="button primary"
        :disabled="delivery.pending.value"
        @click="delivery.open()"
      >
        ＋ 提交文件
      </button>
    </div>
  </header>
  <Feedback
    :error="batches.error.value || action.error.value"
    :success="action.success.value"
    :loading="batches.loading.value"
  />
  <section class="panel">
    <div v-if="batches.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>文件</th>
            <th>交付状态</th>
            <th>创建时间</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in batches.result.value.items" :key="batch.id">
            <td>
              <button
                class="text-link strong"
                :disabled="delivery.pending.value"
                @click="delivery.open(batch)"
              >
                {{ batch.label }}
              </button>
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(batch.project_id) }}
              </small>
            </td>
            <td>
              <span v-if="details[batch.id]?.files">
                {{ details[batch.id]?.files?.length }} 个文件
              </span>
              <button
                v-else
                class="text-link"
                :disabled="delivery.pending.value"
                @click="delivery.open(batch)"
              >
                查看文件
              </button>
            </td>
            <td>
              <StatusBadge :value="batch.ready ? 'ready' : 'uploading'" />
            </td>
            <td class="numeric">{{ formatTime(batch.created_at) }}</td>
            <td class="align-right">
              <button
                class="button small"
                :disabled="delivery.pending.value"
                @click="delivery.open(batch)"
              >
                {{ batch.ready || !canOperate ? '查看详情' : '继续交付' }}
              </button>
              <button
                v-if="canOperate && !batch.ready"
                class="button danger small"
                :disabled="delivery.pending.value || action.pending.value"
                @click="deleteCandidate = batch"
              >
                删除空批次
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!batches.loading.value && !batches.error.value"
      title="尚未提交输入文件"
      description="为本次文件起一个名称，选择全部文件后一次提交。"
    />
    <Pagination
      v-if="batches.result.value"
      v-model:offset="batches.offset.value"
      :total="batches.result.value.total"
      :loading="delivery.pending.value || batches.loading.value"
    />
  </section>
  <Modal
    v-if="delivery.opened.value"
    :title="
      delivery.batch.value?.ready
        ? '文件详情'
        : delivery.batch.value
          ? '继续交付文件'
          : '提交输入文件'
    "
    :busy="delivery.pending.value"
    wide
    @close="close"
  >
    <form @submit.prevent="delivery.submit">
      <Feedback
        :error="delivery.error.value"
        :success="delivery.success.value"
      />
      <label>
        文件批次名称
        <input
          v-model="delivery.label.value"
          aria-label="文件批次名称"
          required
          maxlength="100"
          :disabled="
            delivery.pending.value ||
            !!delivery.batch.value ||
            delivery.creationUncertain.value ||
            !canOperate
          "
          placeholder="例如：2026年8月账单"
        />
      </label>
      <p v-if="delivery.batch.value?.ready" class="muted">
        文件已就绪，可以用于任务。需要修改或补充时，请新建文件批次。
      </p>
      <p v-else class="muted">
        请一次选齐所需文件。点击提交后将确认交付，文件不能再修改；等待这些文件的任务可能开始执行。
      </p>
      <div
        v-if="
          canOperate &&
          !delivery.batch.value?.ready &&
          delivery.detailLoaded.value &&
          !delivery.creationUncertain.value
        "
        class="form-actions"
      >
        <label
          class="button file-button"
          :class="{ disabled: delivery.pending.value }"
        >
          选择文件
          <input
            type="file"
            aria-label="选择待交付文件"
            multiple
            :disabled="delivery.pending.value"
            @change="choose"
          />
        </label>
        <span class="muted">{{ fileRows.length }} 个文件</span>
      </div>
      <p v-if="delivery.stage.value" role="status" aria-live="polite">
        {{ delivery.stage.value }}
        <template v-if="delivery.files.value.length">
          · 本次已上传 {{ delivery.submittedCount.value }}/{{
            delivery.files.value.length
          }}
          个文件
        </template>
      </p>
      <div v-if="fileRows.length" class="table-scroll">
        <table aria-label="文件交付清单">
          <thead>
            <tr>
              <th>文件名</th>
              <th>大小</th>
              <th>状态</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="file in fileRows" :key="file.name">
              <td>{{ file.name }}</td>
              <td>
                {{ file.size === undefined ? '暂无' : `${file.size} 字节` }}
              </td>
              <td>{{ file.status }}</td>
              <td>
                <button
                  v-if="file.removeIndex >= 0"
                  type="button"
                  class="text-link"
                  :disabled="delivery.pending.value"
                  :aria-label="`移除 ${file.name}`"
                  @click="delivery.remove(file.removeIndex)"
                >
                  移除
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="delivery.detailLoaded.value" class="muted">
        尚未选择或上传文件。
      </p>
      <p
        v-if="
          delivery.batch.value &&
          !delivery.batch.value.ready &&
          delivery.error.value
        "
        class="muted"
      >
        已保留这个文件批次及已上传文件。重试会继续当前交付，不会重新创建。
      </p>
      <details v-if="delivery.batch.value" class="section-gap">
        <summary>批次标识</summary>
        <p class="mono">{{ delivery.batch.value.id }}</p>
      </details>
      <div class="form-actions">
        <button
          v-if="delivery.batch.value && !delivery.detailLoaded.value"
          type="button"
          class="button"
          :disabled="delivery.pending.value"
          @click="delivery.reload"
        >
          重新读取文件
        </button>
        <button
          type="button"
          class="button"
          :disabled="delivery.pending.value"
          @click="close"
        >
          {{ delivery.batch.value?.ready ? '完成' : '关闭' }}
        </button>
        <button
          v-if="
            canOperate &&
            !delivery.batch.value?.ready &&
            !delivery.creationUncertain.value
          "
          class="button primary"
          :disabled="
            delivery.pending.value ||
            !delivery.detailLoaded.value ||
            !fileRows.length
          "
        >
          {{
            delivery.pending.value
              ? '正在提交…'
              : delivery.batch.value
                ? '继续提交文件'
                : '提交文件'
          }}
        </button>
      </div>
    </form>
  </Modal>
  <Modal
    v-if="deleteCandidate"
    :title="`删除文件批次 · ${deleteCandidate.label}`"
    :busy="action.pending.value"
    @close="deleteCandidate = null"
  >
    <Feedback :error="action.error.value" />
    <p>只有尚未上传任何文件、也未被运行或计划引用的待交付批次可以删除。</p>
    <div class="form-actions">
      <button class="button" type="button" @click="deleteCandidate = null">
        取消
      </button>
      <button
        class="button danger"
        type="button"
        :disabled="action.pending.value"
        @click="deleteEmptyBatch"
      >
        确认删除
      </button>
    </div>
  </Modal>
</template>
