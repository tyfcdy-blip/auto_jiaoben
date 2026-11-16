<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { errorMessage, request } from '../api'
import { session } from '../session'
import type { Node } from '../types'
import Feedback from './Feedback.vue'
import StatusBadge from './StatusBadge.vue'

interface Grant {
  project_id: string
  project_name: string
  enabled: boolean
  input_roots: string[]
}
interface Configuration {
  node: Node
  desired_revision: number
  applied_revision: number
  config_status: string
  config_error: string
  input_roots: string[]
  root_status: { path: string; status: string; checked_at?: number }[]
  grants: Grant[]
}
interface GrantDraft {
  project_id: string
  project_name: string
  enabled: boolean
  roots: string
  owner: boolean
}

const props = defineProps<{ node: Node }>()
const emit = defineEmits<{ busy: [value: boolean]; saved: []; close: [] }>()
const configuration = ref<Configuration | null>(null)
const name = ref('')
const slots = ref(1)
const roots = ref('')
const grants = ref<GrantDraft[]>([])
const loading = ref(true)
const pending = ref(false)
const error = ref('')

const enabledProjects = computed(
  () => grants.value.filter((grant) => grant.enabled).length,
)
function lines(value: string) {
  return [
    ...new Set(
      value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ]
}
function hydrate(value: Configuration) {
  configuration.value = value
  name.value = value.node.name
  slots.value = value.node.slots
  roots.value = value.input_roots.join('\n')
  const existing = new Map(
    value.grants.map((grant) => [grant.project_id, grant]),
  )
  const projects = new Map(
    session.projects.map((project) => [project.id, project.name]),
  )
  value.grants.forEach((grant) =>
    projects.set(grant.project_id, grant.project_name),
  )
  grants.value = [...projects]
    .map(([projectId, projectName]) => {
      const grant = existing.get(projectId)
      return {
        project_id: projectId,
        project_name: projectName,
        enabled: projectId === value.node.project_id || Boolean(grant?.enabled),
        roots: grant?.input_roots.join('\n') || '',
        owner: projectId === value.node.project_id,
      }
    })
    .sort((left, right) => Number(right.owner) - Number(left.owner))
}
async function load() {
  loading.value = true
  error.value = ''
  try {
    hydrate(
      await request<Configuration>(`/nodes/${props.node.id}/configuration`),
    )
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    loading.value = false
  }
}
async function save() {
  if (pending.value || !configuration.value) return
  pending.value = true
  emit('busy', true)
  error.value = ''
  try {
    const result = await request<Configuration>(
      `/nodes/${props.node.id}/configuration`,
      {
        method: 'PUT',
        body: {
          base_revision: configuration.value.desired_revision,
          name: name.value.trim(),
          slots: slots.value,
          input_roots: lines(roots.value),
          grants: grants.value.map((grant) => ({
            project_id: grant.project_id,
            enabled: grant.enabled,
            input_roots: lines(grant.roots),
          })),
        },
      },
    )
    hydrate(result)
    emit('saved')
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    pending.value = false
    emit('busy', false)
  }
}
onMounted(load)
</script>

<template>
  <Feedback :error="error" :loading="loading" />
  <div v-if="!node.maintenance" class="maintenance-gate">
    <strong>请先让电脑进入维护状态</strong>
    <p>维护状态会停止接收新任务，确保目录和项目权限调整时没有任务正在启动。</p>
  </div>
  <form v-else-if="configuration" @submit.prevent="save">
    <div class="configuration-summary">
      <div>
        <small>配置同步</small>
        <StatusBadge :value="configuration.config_status" />
      </div>
      <div>
        <small>期望版本</small>
        <strong>v{{ configuration.desired_revision }}</strong>
      </div>
      <div>
        <small>电脑已应用</small>
        <strong>v{{ configuration.applied_revision }}</strong>
      </div>
      <p v-if="configuration.config_error">{{ configuration.config_error }}</p>
    </div>

    <div class="form-grid section-gap">
      <label>
        电脑名称
        <input v-model="name" required maxlength="100" :disabled="pending" />
      </label>
      <label>
        同时运行任务数
        <input
          v-model.number="slots"
          type="number"
          min="1"
          :max="enabledProjects > 1 ? 1 : 16"
          required
          :disabled="pending"
        />
        <small>
          {{
            enabledProjects > 1
              ? '跨项目共享电脑首版固定为 1。'
              : '这是整台电脑的总容量。'
          }}
        </small>
      </label>
    </div>
    <label>
      电脑允许读取的根目录
      <textarea
        v-model="roots"
        rows="4"
        :disabled="pending"
        placeholder="一行一个，例如 D:\业务文件 或 \\文件服务器\共享目录"
      ></textarea>
      <small>
        这里只保存路径，不保存共享账号和密码。网络共享凭据应配置在执行器运行的
        Windows 账号中。
      </small>
    </label>

    <div class="grant-heading">
      <div>
        <h3>可使用这台电脑的项目</h3>
        <p>
          {{ enabledProjects }} 个项目启用；每个项目只能读取自己获准的子目录。
        </p>
      </div>
    </div>
    <section class="grant-list">
      <article
        v-for="grant in grants"
        :key="grant.project_id"
        class="grant-row"
        :class="{ active: grant.enabled }"
      >
        <label class="checkbox grant-toggle">
          <input
            v-model="grant.enabled"
            type="checkbox"
            :disabled="pending || grant.owner"
          />
          <span>
            <strong>{{ grant.project_name }}</strong>
            <small>
              {{
                grant.owner
                  ? '登记所属项目，必须保留'
                  : grant.enabled
                    ? '已授权'
                    : '未授权'
              }}
            </small>
          </span>
        </label>
        <label v-if="grant.enabled">
          本项目可读取目录
          <textarea
            v-model="grant.roots"
            rows="2"
            :disabled="pending"
            placeholder="一行一个，必须位于电脑根目录内"
          ></textarea>
        </label>
      </article>
    </section>
    <div class="form-actions">
      <button
        type="button"
        class="button"
        :disabled="pending"
        @click="emit('close')"
      >
        取消
      </button>
      <button class="button primary" :disabled="pending || !name.trim()">
        {{ pending ? '正在保存…' : '保存全部配置' }}
      </button>
    </div>
  </form>
</template>

<style scoped>
.maintenance-gate {
  padding: 22px;
  border: 1px solid #ecd39d;
  border-radius: 12px;
  background: #fff9eb;
}
.maintenance-gate p,
.grant-heading p {
  margin: 6px 0 0;
  color: var(--muted);
  font-size: 12px;
}
.configuration-summary {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: linear-gradient(135deg, #f2fbf8, #f5f8ff);
}
.configuration-summary > div {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 7px;
}
.configuration-summary small {
  color: var(--muted);
}
.configuration-summary p {
  grid-column: 1 / -1;
  margin: 0;
  color: var(--danger);
}
.grant-heading {
  display: flex;
  justify-content: space-between;
  margin: 28px 0 12px;
}
.grant-heading h3 {
  margin: 0;
}
.grant-list {
  display: grid;
  gap: 10px;
}
.grant-row {
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: #fafbfd;
}
.grant-row.active {
  border-color: #b8dcd3;
  background: #f5fbf9;
}
.grant-toggle {
  margin-bottom: 0;
}
.grant-toggle span {
  display: flex;
  flex-direction: column;
}
.grant-toggle small {
  color: var(--muted);
}
.grant-row > label:last-child {
  margin: 14px 0 0 23px;
}
@media (max-width: 640px) {
  .configuration-summary {
    grid-template-columns: 1fr;
  }
  .configuration-summary p {
    grid-column: auto;
  }
}
</style>
