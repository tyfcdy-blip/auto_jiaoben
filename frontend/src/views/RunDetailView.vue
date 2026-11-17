<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { request, downloadArtifact } from '../api'
import { useAction, useResource, formatTime } from '../composables'
import { session } from '../session'
import type { Run } from '../types'
import Feedback from '../components/Feedback.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Modal from '../components/Modal.vue'
const route = useRoute(),
  runId = computed(() => String(route.params.id))
const run = useResource<Run>(() => `/runs/${encodeURIComponent(runId.value)}`)
// 详情可通过直接链接进入，操作权限必须归属于该运行，不能沿用导航选中的项目。
const runProjectRole = computed(
  () =>
    session.projects.find(
      (project) => project.id === run.result.value?.project_id,
    )?.role,
)
const canOperateRun = computed(
  () =>
    Boolean(session.user?.is_admin) ||
    ['project_admin', 'developer', 'operator'].includes(
      runProjectRole.value || '',
    ),
)
const canReconcileRun = computed(
  () =>
    Boolean(session.user?.is_admin) || runProjectRole.value === 'project_admin',
)
const logs = useResource<{
  entries: { sequence: number; stream: string; text: string }[]
  truncated: boolean
}>(() => `/runs/${encodeURIComponent(runId.value)}/logs`)
const action = useAction(),
  tab = ref('logs'),
  cancelOpen = ref(false),
  reconcileOpen = ref(false),
  note = ref(''),
  outcome = ref('failed')
const active = computed(
  () =>
    run.result.value &&
    [
      'waiting_input',
      'queued',
      'leased',
      'running',
      'cancel_requested',
    ].includes(run.result.value.state),
)
const queueExpiredWithoutStart = computed(() => {
  const current = run.result.value
  return (
    current?.state === 'failed' &&
    current.error === '排队超时，未启动程序' &&
    !current.started_at &&
    !current.attempt_id
  )
})
const notStarted = computed(() => {
  const current = run.result.value
  return Boolean(
    current &&
    !current.started_at &&
    !current.attempt_id &&
    (['queued', 'waiting_input'].includes(current.state) ||
      queueExpiredWithoutStart.value),
  )
})
let timer: ReturnType<typeof setInterval> | undefined
async function refresh() {
  await Promise.all([run.refresh(), logs.refresh()])
}
onMounted(() => {
  timer = setInterval(() => {
    if (
      !document.hidden &&
      (active.value ||
        run.result.value?.file_status?.save_state === 'pending') &&
      !run.loading.value &&
      !logs.loading.value &&
      !action.pending.value
    )
      void refresh()
  }, 5000)
})
onUnmounted(() => clearInterval(timer))
async function retrySave() {
  await action.perform(async () => {
    await request(`/runs/${runId.value}/file-save/retry`, { method: 'POST' })
    await run.refresh()
  }, '已请求重新保存结果文件，不会重新运行脚本。')
}
async function cancel() {
  await action.perform(async () => {
    await request(`/runs/${runId.value}/cancel`, { method: 'POST' })
    cancelOpen.value = false
    await refresh()
  }, '停止请求已提交，请等待执行器确认')
}
async function reconcile() {
  await action.perform(async () => {
    await request(`/runs/${runId.value}/reconcile`, {
      method: 'POST',
      body: { outcome: outcome.value, note: note.value.trim() },
    })
    reconcileOpen.value = false
    await refresh()
  }, '核对结论已记录')
}
async function download(artifact: { id: string; name: string }) {
  await action.perform(
    () =>
      downloadArtifact(
        `/runs/${runId.value}/artifacts/${artifact.id}`,
        artifact.name,
      ),
    '产物下载已开始',
  )
}
</script>
<template>
  <RouterLink to="/runs" class="back-link">← 运行中心</RouterLink>
  <header class="page-header">
    <div>
      <p class="eyebrow">EXECUTION DETAIL</p>
      <h1>{{ run.result.value?.task_name || '运行详情' }}</h1>
      <p class="mono">{{ runId }}</p>
    </div>
    <div class="toolbar">
      <button class="button" :disabled="run.loading.value" @click="refresh">
        刷新
      </button>
      <button
        v-if="
          canOperateRun &&
          active &&
          run.result.value?.state !== 'cancel_requested'
        "
        class="button danger"
        @click="cancelOpen = true"
      >
        请求停止
      </button>
      <button
        v-if="canReconcileRun && run.result.value?.state === 'unknown'"
        class="button"
        @click="reconcileOpen = true"
      >
        记录核对结论
      </button>
    </div>
  </header>
  <Feedback
    :error="run.error.value || action.error.value"
    :success="action.success.value"
    :loading="run.loading.value && !run.result.value"
  />
  <template v-if="run.result.value">
    <div
      v-if="queueExpiredWithoutStart"
      class="feedback warning section-gap"
      role="status"
    >
      <strong>本次任务没有在执行电脑上运行。</strong>
      等待派发超时后已结束，这不是脚本执行报错。请先处理派发或电脑接单条件，
      再从任务管理发起新的运行；此历史记录不会自动恢复。
    </div>
    <section class="panel">
      <dl class="details-grid">
        <div>
          <dt>所属项目</dt>
          <dd>
            {{ run.result.value.project_name || run.result.value.project_id }}
          </dd>
        </div>
        <div>
          <dt>触发方式</dt>
          <dd><StatusBadge :value="run.result.value.source" /></dd>
        </div>
        <div>
          <dt>运行状态</dt>
          <dd><StatusBadge :value="run.result.value.state" /></dd>
        </div>
        <div>
          <dt>进程退出码</dt>
          <dd class="mono">
            {{ run.result.value.exit_code ?? '尚无退出证据' }}
          </dd>
        </div>
        <div>
          <dt>归档状态</dt>
          <dd>
            <StatusBadge :value="run.result.value.artifact_state || 'none'" />
          </dd>
        </div>
        <div>
          <dt>创建时间</dt>
          <dd class="numeric">{{ formatTime(run.result.value.created_at) }}</dd>
        </div>
        <div>
          <dt>开始时间</dt>
          <dd class="numeric">{{ formatTime(run.result.value.started_at) }}</dd>
        </div>
        <div>
          <dt>结束时间</dt>
          <dd class="numeric">
            {{ formatTime(run.result.value.finished_at) }}
          </dd>
        </div>
        <div>
          <dt>执行节点</dt>
          <dd class="mono">{{ run.result.value.node_id || '尚未分配' }}</dd>
        </div>
        <div>
          <dt>执行尝试</dt>
          <dd class="mono">{{ run.result.value.attempt_id || '尚未启动' }}</dd>
        </div>
        <div>
          <dt>进程观测</dt>
          <dd>{{ run.result.value.observation || '暂无' }}</dd>
        </div>
      </dl>
    </section>
    <div v-if="run.result.value.error" class="section-gap">
      <Feedback :error="run.result.value.error" />
    </div>
    <section
      v-if="run.result.value.file_status?.enabled"
      class="panel content-padding section-gap"
      aria-label="结果交付状态"
    >
      <h2>结果保存到执行电脑</h2>
      <p>
        程序执行、平台归档和本机另存分别记录。另存失败不代表脚本需要重新运行。
      </p>
      <p v-if="run.result.value.file_status.save_state === 'not_requested'">
        本次未要求另存到执行电脑。
      </p>
      <p
        v-else-if="run.result.value.file_status.save_state === 'pending'"
        role="status"
      >
        {{
          notStarted
            ? '等待程序启动并生成结果文件。'
            : '等待执行电脑回报结果保存状态。'
        }}
      </p>
      <p
        v-else-if="run.result.value.file_status.save_state === 'succeeded'"
        role="status"
      >
        已保存到执行电脑，文件清单见下方。
      </p>
      <Feedback
        v-else
        :error="
          run.result.value.file_status.save_error ||
          '结果另存失败，请检查电脑目录权限和磁盘空间。'
        "
      />
      <p v-if="run.result.value.file_status.output_dir" class="mono">
        保存父目录：{{ run.result.value.file_status.output_dir }}
      </p>
      <ul v-if="run.result.value.file_status.saved_files.length">
        <li
          v-for="file in run.result.value.file_status.saved_files"
          :key="file.path"
        >
          {{ file.name }} · {{ file.path }}
        </li>
      </ul>
      <button
        v-if="
          run.result.value.file_status.save_state === 'failed' &&
          canOperateRun &&
          run.result.value.attempt_id &&
          !active
        "
        class="button small"
        :disabled="action.pending.value"
        @click="retrySave"
      >
        仅重试另存结果
      </button>
      <p class="muted">
        钉钉发送状态请在消息记录中核对；文件保存成功不代表消息已送达。
      </p>
      <RouterLink class="text-link" to="/notification-records">
        查看消息记录 →
      </RouterLink>
    </section>
    <div
      v-if="
        run.result.value.state === 'queued' && run.result.value.waiting_reason
      "
      class="feedback warning section-gap"
    >
      <strong>暂未开始：</strong>
      {{ run.result.value.waiting_reason }}
    </div>
    <div
      v-if="run.result.value.state === 'unknown'"
      class="feedback warning section-gap"
    >
      实际业务结果尚不确定。请核对外部系统和执行节点，不能直接认为失败或从头重跑。
    </div>
    <section class="panel section-gap">
      <div class="detail-tabs">
        <button :class="{ active: tab === 'logs' }" @click="tab = 'logs'">
          执行日志
        </button>
        <button
          :class="{ active: tab === 'parameters' }"
          @click="tab = 'parameters'"
        >
          本次参数
        </button>
        <button :class="{ active: tab === 'result' }" @click="tab = 'result'">
          业务结果
        </button>
      </div>
      <div v-if="tab === 'logs'">
        <Feedback :error="logs.error.value" />
        <div v-if="logs.result.value?.truncated" class="feedback warning">
          当前日志为有界片段，部分历史内容未在此展示。
        </div>
        <div class="log-panel" aria-label="执行日志">
          <div
            v-for="entry in logs.result.value?.entries"
            :key="entry.sequence"
            class="log-line"
            :class="entry.stream"
          >
            <span class="log-number">{{ entry.sequence }}</span>
            {{ entry.text }}
          </div>
          <span v-if="!logs.result.value?.entries.length">
            {{
              logs.loading.value
                ? '正在读取日志…'
                : '暂无日志，程序启动并输出后会在这里显示。'
            }}
          </span>
        </div>
      </div>
      <div v-else-if="tab === 'parameters'" class="content-padding">
        <pre class="code-panel">{{
          JSON.stringify(run.result.value.parameters, null, 2)
        }}</pre>
      </div>
      <div v-else class="content-padding">
        <div v-if="run.result.value.result" class="business-result">
          <StatusBadge
            :value="String(run.result.value.result.status || 'unknown')"
          />
          <p>{{ run.result.value.result.summary || '程序未提供文字摘要。' }}</p>
          <details>
            <summary>查看原始结果记录</summary>
            <pre class="code-panel">{{
              JSON.stringify(run.result.value.result, null, 2)
            }}</pre>
          </details>
        </div>
        <p v-else class="muted">
          尚未收到结构化业务结果。最终状态仍需结合退出证据及该程序的接入约定。
        </p>
        <div v-if="run.result.value.artifacts?.length" class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>结果文件</th>
                <th>大小</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="artifact in run.result.value.artifacts"
                :key="artifact.id"
              >
                <td>{{ artifact.name }}</td>
                <td>{{ artifact.size }} 字节</td>
                <td>
                  <button
                    class="button small"
                    :disabled="action.pending.value"
                    @click="download(artifact)"
                  >
                    下载
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="form-caption">
          {{
            notStarted
              ? '程序尚未启动，没有生成结果文件。'
              : active
                ? '尚无可下载文件，请等待执行电脑生成并回传结果。'
                : '本次运行没有已归档的可下载文件，请结合归档状态、业务结果和日志核对。'
          }}
        </p>
      </div>
    </section>
    <p v-if="active" class="form-caption section-gap">
      页面可见时每 5 秒更新运行状态和日志。
    </p>
  </template>
  <Modal v-if="cancelOpen" title="请求停止本次运行" @close="cancelOpen = false">
    <p>执行器将按停止策略处理。停止进程不会撤销已经提交到外部系统的数据。</p>
    <Feedback :error="action.error.value" />
    <div class="form-actions">
      <button
        class="button"
        :disabled="action.pending.value"
        @click="cancelOpen = false"
      >
        返回
      </button>
      <button
        class="button danger"
        :disabled="action.pending.value"
        @click="cancel"
      >
        确认请求停止
      </button>
    </div>
  </Modal>
  <Modal
    v-if="reconcileOpen"
    title="记录人工核对结论"
    @close="reconcileOpen = false"
  >
    <form @submit.prevent="reconcile">
      <p class="form-caption">
        仅在核实实际执行及外部系统结果后操作。此记录是人工核对结论，不会伪造进程退出证据。
      </p>
      <Feedback :error="action.error.value" />
      <label>
        核对结果
        <select v-model="outcome">
          <option value="failed">已确认失败</option>
          <option value="succeeded">已确认成功</option>
          <option value="canceled">已确认取消</option>
        </select>
      </label>
      <label>
        核对依据
        <textarea
          v-model="note"
          rows="5"
          required
          minlength="5"
          maxlength="2000"
          placeholder="说明核查了哪些记录、实际结果及后续处理"
        ></textarea>
      </label>
      <div class="form-actions">
        <button class="button primary" :disabled="action.pending.value">
          保存核对结论
        </button>
      </div>
    </form>
  </Modal>
</template>
