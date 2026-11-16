<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { ApiError, downloadArtifact, errorMessage, request } from '../api'
import {
  connectionOptions,
  downloadConnection,
  type NodeConnection,
} from '../nodeConnection'
import Feedback from './Feedback.vue'

const props = defineProps<{ projectId: string }>()
const emit = defineEmits<{
  busy: [value: boolean]
  registered: []
  close: []
}>()
const name = ref(''),
  runtime = ref('python'),
  executable = ref(''),
  alias = ref('python'),
  extra = ref(''),
  roots = ref('')
const address = ref(
  window.location.origin + import.meta.env.BASE_URL.replace(/\/$/, ''),
)
const pending = ref(false),
  installerPending = ref(false),
  error = ref(''),
  uncertain = ref(false),
  issued = ref<NodeConnection | null>(null)
let alive = true
onScopeDispose(() => {
  alive = false
  issued.value = null
})
watch(runtime, (value) => {
  alias.value = value
})
async function create() {
  if (pending.value || issued.value || uncertain.value || !props.projectId)
    return
  error.value = ''
  let options: ReturnType<typeof connectionOptions>
  try {
    if (!name.value.trim()) throw new Error('请填写电脑名称。')
    options = connectionOptions(
      address.value,
      alias.value,
      executable.value,
      extra.value,
      roots.value,
    )
  } catch (failure) {
    error.value = errorMessage(failure)
    return
  }
  const projectId = props.projectId,
    computerName = name.value.trim()
  pending.value = true
  emit('busy', true)
  try {
    const result = await request<{
      id: string
      token: string
      config_revision: number
      config_hash: string
    } | null>('/nodes', {
      method: 'POST',
      body: {
        project_id: projectId,
        name: computerName,
        capabilities: options.capabilities,
        slots: 1,
        input_roots: options.input_roots || [],
      },
    })
    if (!alive || props.projectId !== projectId) return
    if (
      !result ||
      typeof result.id !== 'string' ||
      !/^[a-zA-Z0-9_-]{1,100}$/.test(result.id) ||
      typeof result.token !== 'string' ||
      !/^[a-zA-Z0-9_-]{32,4096}$/.test(result.token) ||
      !Number.isInteger(result.config_revision) ||
      result.config_revision < 1 ||
      !/^[a-f0-9]{64}$/.test(result.config_hash)
    )
      throw new Error('节点签发响应不完整')
    // 保存签发结果后才通知列表刷新；刷新失败不重复创建节点，也不丢失凭据。
    issued.value = {
      format: 'workbench-connection-v1',
      node_id: result.id,
      name: computerName,
      token: result.token,
      config_revision: result.config_revision,
      config_hash: result.config_hash,
      ...options,
    }
    emit('registered')
  } catch (failure) {
    if (!alive || props.projectId !== projectId) return
    uncertain.value = !(
      failure instanceof ApiError &&
      failure.status >= 400 &&
      failure.status < 500
    )
    error.value = uncertain.value
      ? '登记结果暂时无法确认。请关闭后刷新节点列表并核对，勿直接重复登记；凭据未收到时请停用该节点再重新接入。'
      : errorMessage(failure)
  } finally {
    if (alive) {
      pending.value = false
      emit('busy', false)
    }
  }
}
function download() {
  if (!issued.value) return
  try {
    downloadConnection(issued.value)
    error.value = ''
  } catch {
    error.value = '下载未完成，请重试下载连接文件，无需重新登记电脑。'
  }
}
async function downloadInstaller() {
  if (installerPending.value) return
  installerPending.value = true
  try {
    await downloadArtifact(
      '/agent-packages/windows',
      '自动化工作台执行电脑接入包.zip',
    )
    error.value = ''
  } catch (failure) {
    error.value = errorMessage(failure)
  } finally {
    installerPending.value = false
  }
}
</script>
<template>
  <Feedback :error="error" />
  <section v-if="issued" class="panel">
    <h3>{{ issued.name }} 已登记，等待连接</h3>
    <p>
      下载下面两个文件，在执行电脑上完成一次接入。连接文件含节点凭据，请勿发到群聊或代码仓库。
    </p>
    <ol>
      <li>下载“一键接入包”和“连接文件”。</li>
      <li>解压接入包，双击“安装并连接.cmd”，选择连接文件。</li>
      <li>看到接入完成后返回这里刷新；成功后删除连接文件。</li>
    </ol>
    <p>
      接入工具会后台启动并加入当前Windows用户的开机启动。首版需先安装Python
      3.12，当前采用开发执行模式，每次运行一个可信任务。
    </p>
    <div class="form-actions">
      <button
        class="button primary"
        :disabled="installerPending"
        @click="downloadInstaller"
      >
        {{ installerPending ? '正在准备…' : '1. 下载一键接入包' }}
      </button>
      <button class="button" @click="download">2. 下载连接文件</button>
      <button class="button" @click="emit('close')">已保存，关闭</button>
    </div>
  </section>
  <form v-else @submit.prevent="create">
    <div class="form-grid">
      <label>
        电脑名称
        <input
          v-model="name"
          required
          maxlength="100"
          placeholder="例如：财务数据处理电脑"
          :disabled="pending"
        />
      </label>
      <label>
        运行环境
        <select v-model="runtime" :disabled="pending">
          <option value="python">Python</option>
          <option value="node">Node.js</option>
        </select>
      </label>
      <label>
        执行电脑上的程序路径
        <input
          v-model="executable"
          required
          :disabled="pending"
          :placeholder="
            runtime === 'python'
              ? '例如：C:\\Python312\\python.exe'
              : '例如：C:\\Program Files\\nodejs\\node.exe'
          "
        />
        <small>
          填写执行电脑已有的程序路径。虚拟环境应选择其中的 python.exe；Linux
          也可填写 /usr/bin/python3。
        </small>
      </label>
    </div>
    <label>
      允许读取的文件夹根目录（可选）
      <textarea
        v-model="roots"
        aria-label="允许读取的文件夹根目录"
        rows="3"
        :disabled="pending"
        placeholder="一行一个完整路径，例如 D:\账单"
      ></textarea>
      <small>
        仅目录输入任务需要。实际权限和路径由执行电脑核验；不要填写共享账号或密码。
      </small>
    </label>
    <details class="advanced-options">
      <summary>高级设置</summary>
      <div class="form-grid">
        <label>
          工作台连接地址
          <input v-model="address" required :disabled="pending" />
          <small>
            默认当前地址。另一台电脑不能使用 localhost，请改为其可访问的 HTTPS
            地址。
          </small>
        </label>
        <label>
          运行时名称
          <input v-model="alias" required maxlength="80" :disabled="pending" />
          <small>应与程序入口使用的运行时一致，例如 python-nine。</small>
        </label>
        <label>
          额外能力
          <input
            v-model="extra"
            :disabled="pending"
            placeholder="例如：nine-billing，用逗号分隔"
          />
        </label>
      </div>
    </details>
    <p>每台电脑同时运行 1 个任务。这里不会安装运行环境或启动远程电脑。</p>
    <div class="form-actions">
      <button
        class="button primary"
        :disabled="pending || uncertain || !projectId"
      >
        {{ pending ? '正在登记…' : '登记并生成连接文件' }}
      </button>
    </div>
  </form>
</template>
