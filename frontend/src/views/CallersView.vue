<script setup lang="ts">
import { ref } from 'vue'
import { session, canManage, projectName } from '../session'
import { request, apiEndpoint } from '../api'
import { useAction, useList, useChoices } from '../composables'
import type { Caller, Task } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
import SecretOnce from '../components/SecretOnce.vue'
const apiRoot = apiEndpoint('')
const apiDocs = apiRoot.replace(/\/api\/v1$/, '/api/docs')
const callers = useList<Caller>('/callers'),
  tasks = useChoices<Task>('/tasks'),
  action = useAction(),
  open = ref(false),
  name = ref(''),
  taskIds = ref<string[]>([]),
  allowInputUpload = ref(false),
  issued = ref<{ id: string; token: string } | null>(null)
function close() {
  open.value = false
  issued.value = null
  name.value = ''
  taskIds.value = []
  allowInputUpload.value = false
}
async function create() {
  await action.perform(async () => {
    issued.value = await request('/callers', {
      method: 'POST',
      body: {
        name: name.value.trim(),
        project_id: session.projectId,
        task_ids: taskIds.value,
        allow_input_upload: allowInputUpload.value,
      },
    })
    await callers.refresh()
  }, '调用方已创建')
}
async function toggle(caller: Caller) {
  await action.perform(async () => {
    await request(`/callers/${caller.id}`, {
      method: 'PATCH',
      body: { active: !caller.active },
    })
    await callers.refresh()
  }, '调用方授权状态已更新')
}
function parameterRows(task: Task) {
  const schema = task.parameter_schema || {}
  const properties = schema.properties
  if (
    !properties ||
    typeof properties !== 'object' ||
    Array.isArray(properties)
  )
    return []
  const required = new Set(
    Array.isArray(schema.required) ? (schema.required as string[]) : [],
  )
  return Object.entries(
    properties as Record<string, Record<string, unknown>>,
  ).map(([name, definition]) => ({
    name,
    type: Array.isArray(definition.enum)
      ? `可选值：${definition.enum.join(' / ')}`
      : String(definition.type || '任意'),
    required: required.has(name),
    description: String(definition.description || definition.title || ''),
  }))
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">EVENT INTEGRATIONS</p>
      <h1>事件接入</h1>
      <p>给影刀或其他系统独立凭据，只能触发明确授权的任务。</p>
    </div>
    <button v-if="canManage" class="button primary" @click="open = true">
      ＋ 创建调用方
    </button>
  </header>
  <Feedback
    :error="callers.error.value || action.error.value || tasks.error.value"
    :success="action.success.value"
    :loading="callers.loading.value"
  />
  <section class="panel">
    <div v-if="callers.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>调用方</th>
            <th>状态</th>
            <th>授权任务</th>
            <th>文件交付</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="caller in callers.result.value.items" :key="caller.id">
            <td class="strong">
              {{ caller.name }}
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(caller.project_id) }}
              </small>
              <small class="subline mono">{{ caller.id }}</small>
            </td>
            <td>
              <StatusBadge :value="caller.active ? 'enabled' : 'disabled'" />
            </td>
            <td>
              <span v-if="!caller.task_ids.length" class="muted">
                未授权任何任务
              </span>
              <span v-for="id in caller.task_ids" :key="id" class="subline">
                {{
                  tasks.result.value?.items.find((task) => task.id === id)
                    ?.name || id
                }}
              </span>
            </td>
            <td>{{ caller.allow_input_upload ? '已授权' : '未授权' }}</td>
            <td>
              <button
                v-if="canManage"
                class="button small"
                :disabled="action.pending.value"
                @click="toggle(caller)"
              >
                {{ caller.active ? '撤销访问' : '恢复访问' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!callers.loading.value && !callers.error.value"
      title="尚未配置外部调用方"
      description="为每个上游系统单独授权，避免共享人员账号或节点凭据。"
    />
    <Pagination
      v-if="callers.result.value"
      v-model:offset="callers.offset.value"
      :total="callers.result.value.total"
    />
  </section>
  <section class="panel section-gap">
    <div class="content-padding">
      <div class="integration-heading">
        <div>
          <h2>对外接口接入指南</h2>
          <p class="form-caption">
            “创建调用方”就是创建一组外部接口凭据并授权任务。接口地址由工作台统一提供，不需要为每个程序另建接口。
          </p>
        </div>
        <a class="button subtle" :href="apiDocs" target="_blank" rel="noopener">
          打开完整 API 文档
        </a>
      </div>
      <ol class="integration-steps">
        <li>
          <b>创建凭据</b>
          <span>选择允许触发的任务，只保存一次性显示的 Bearer 令牌。</span>
        </li>
        <li>
          <b>准备输入</b>
          <span>
            普通参数放入
            parameters；文件可传共享文件夹路径，或先上传为文件批次。
          </span>
        </li>
        <li>
          <b>触发任务</b>
          <span>
            POST 固定任务地址，工作台校验参数后保存运行并下发给执行电脑。
          </span>
        </li>
        <li>
          <b>取得结果</b>
          <span>
            用 run_id 查询终态，读取 result JSON，并按 artifacts
            清单下载结果文件。
          </span>
        </li>
      </ol>
      <h3>已登记任务可传参数</h3>
      <div v-if="tasks.result.value?.items.length" class="task-contracts">
        <details v-for="task in tasks.result.value.items" :key="task.id">
          <summary>
            <b>{{ task.name }}</b>
            <span class="mono">{{ task.id }}</span>
          </summary>
          <p class="muted">
            {{
              task.input_mode === 'directory'
                ? '共享文件夹输入'
                : task.requires_input
                  ? '工作台文件批次输入'
                  : '文件可选'
            }}
          </p>
          <table
            v-if="parameterRows(task).length"
            class="compact-contract-table"
          >
            <thead>
              <tr>
                <th>参数名</th>
                <th>类型</th>
                <th>必填</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="field in parameterRows(task)" :key="field.name">
                <td class="mono">{{ field.name }}</td>
                <td>{{ field.type }}</td>
                <td>{{ field.required ? '是' : '否' }}</td>
                <td>{{ field.description || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="muted">该任务没有定义普通业务参数。</p>
        </details>
      </div>
      <h3 class="section-gap">触发与查询</h3>
      <p class="form-caption">
        影刀下载完成、关闭文件后，用 HTTP
        请求发起任务。文件夹路径必须能从执行电脑访问，并已在该电脑授权。一次业务交付使用一个
        event_id，网络重试保持它不变。
      </p>
      <pre class="code-panel">
POST {{ apiRoot }}/tasks/{task_id}/runs
Authorization: Bearer &lt;调用方凭据&gt;
Content-Type: application/json

{
  "event_id": "下载批次-唯一编号",
  "parameters": {
    "input_dir": "执行电脑可访问的文件夹路径",
    "batch_key": "下载批次-唯一编号"
  }
}

响应：202 已受理；200 同一事件去重。保存响应的 run_id。

GET {{ apiRoot }}/runs/{run_id}
Authorization: Bearer &lt;同一调用方凭据&gt;

queued / waiting_input / leased / running：继续查询
succeeded：读取 result JSON、artifacts 清单
failed / partial / unknown / canceled：停止后续业务动作，按状态处理

GET {{ apiRoot }}/runs/{run_id}/artifacts/{artifact_id}
Authorization: Bearer &lt;同一调用方凭据&gt;

响应是文件字节；用清单里的 name、size、sha256 校验并保存。</pre>
      <h3>外部文件有两种传法</h3>
      <div class="file-modes">
        <article>
          <b>共享文件夹</b>
          <p>
            影刀下载目录与执行电脑互通，并且已配置允许目录时，直接把
            <code>input_dir</code>
            和唯一的
            <code>batch_key</code>
            放进 parameters。文件不经过工作台上传。
          </p>
        </article>
        <article>
          <b>工作台文件批次</b>
          <p>
            外部系统依次调用
            <code>POST /batches</code>
            、
            <code>PUT /batches/{id}/files/{name}</code>
            、
            <code>POST /batches/{id}/ready</code>
            ，再把批次 ID 作为
            <code>input_batch_id</code>
            触发任务。
          </p>
        </article>
      </div>
      <p class="form-caption">
        首次触发只确认工作台受理，不表示脚本已完成。影刀可以每隔几秒查询一次终态，再接收
        JSON
        和文件。机器人消息中的运行链接供有权限的人员登录工作台查看，不把调用方令牌放进群聊。
      </p>
    </div>
  </section>
  <Modal
    v-if="open"
    title="创建外部调用方"
    :busy="action.pending.value"
    @close="close"
  >
    <SecretOnce v-if="issued" :identity="issued.id" :token="issued.token" />
    <form v-else @submit.prevent="create">
      <Feedback :error="action.error.value" />
      <label>
        调用方名称
        <input
          v-model="name"
          required
          maxlength="100"
          placeholder="例如：影刀账单下载流程"
        />
      </label>
      <label>允许触发的任务</label>
      <div class="checkbox-list">
        <label
          v-for="task in tasks.result.value?.items"
          :key="task.id"
          class="checkbox"
        >
          <input v-model="taskIds" type="checkbox" :value="task.id" />
          {{ task.name }}
        </label>
        <span v-if="!tasks.result.value?.items.length" class="muted">
          暂无任务，请先创建任务。
        </span>
      </div>
      <label class="checkbox section-gap">
        <input v-model="allowInputUpload" type="checkbox" />
        允许该调用方登记、上传并冻结本项目输入批次
      </label>
      <p class="form-caption">
        文件交付权限默认关闭。开启后可登记、上传并冻结整个项目的输入批次，范围不受上方任务选择限制。影刀需要直接交付下载文件时，由项目管理员明确授权。
      </p>
      <div class="form-actions">
        <button
          class="button primary"
          :disabled="action.pending.value || !taskIds.length"
        >
          创建并生成凭据
        </button>
      </div>
    </form>
    <div v-if="issued" class="form-actions">
      <button class="button primary" @click="close">已妥善保存，关闭</button>
    </div>
  </Modal>
</template>
<style scoped>
.integration-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
}
.integration-heading h2 {
  margin-bottom: 6px;
}
.integration-steps {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  padding: 0;
  list-style: none;
  counter-reset: step;
}
.integration-steps li {
  display: grid;
  gap: 6px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.55);
}
.integration-steps li::before {
  counter-increment: step;
  content: counter(step);
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 8px;
  background: #dbeafe;
  color: #1d4ed8;
  font-weight: 800;
}
.integration-steps span,
.file-modes p {
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.6;
}
.task-contracts {
  display: grid;
  gap: 8px;
}
.task-contracts details {
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
}
.task-contracts summary {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  cursor: pointer;
}
.compact-contract-table {
  margin-top: 10px;
}
.file-modes {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.file-modes article {
  padding: 16px;
  border-radius: 12px;
  background: #f7f9fd;
  border: 1px solid var(--line);
}
.file-modes p {
  margin-bottom: 0;
}
@media (max-width: 800px) {
  .integration-heading {
    flex-direction: column;
  }
  .integration-steps,
  .file-modes {
    grid-template-columns: 1fr;
  }
}
</style>
