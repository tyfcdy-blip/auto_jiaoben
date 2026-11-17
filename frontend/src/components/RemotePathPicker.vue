<script setup lang="ts">
import { onScopeDispose, ref, watch } from 'vue'
import { errorMessage, request } from '../api'
import type { FileEntry, FileListing } from '../nodeFiles'
import Feedback from './Feedback.vue'

const props = defineProps<{
  nodeId: string
  projectId: string
  roots: string[]
  purpose: 'input' | 'output'
  kind: 'file' | 'files' | 'directory'
}>()
const emit = defineEmits<{ select: [paths: string[]]; close: [] }>()
const path = ref('')
const entries = ref<FileEntry[]>([])
const selected = ref<string[]>([])
const history = ref<string[]>([])
const busy = ref(false)
const error = ref('')
const state = ref('')
const truncated = ref(false)
const offset = ref(0)
const nextOffset = ref<number | null>(null)
let generation = 0
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
function stop() {
  generation++
  controller?.abort()
  clearTimeout(timer)
}
async function browse(directory: string, pageOffset = 0) {
  stop()
  const current = generation
  controller = new AbortController()
  const signal = controller.signal
  path.value = directory
  offset.value = pageOffset
  entries.value = []
  error.value = ''
  state.value = 'pending'
  busy.value = true
  truncated.value = false
  nextOffset.value = null
  const deadline = Date.now() + 65000
  const endpoint = `/nodes/${encodeURIComponent(props.nodeId)}/file-requests`
  async function poll(id: string) {
    if (current !== generation) return
    try {
      const result = await request<FileListing>(
        `${endpoint}/${encodeURIComponent(id)}`,
        { signal },
      )
      if (current !== generation) return
      state.value = result.state
      if (result.state === 'succeeded') {
        entries.value = result.entries
        truncated.value = result.truncated
        nextOffset.value = result.next_offset ?? null
        busy.value = false
      } else if (result.state === 'failed' || result.state === 'expired') {
        error.value =
          result.error ||
          (result.state === 'expired'
            ? '电脑未及时响应，请检查连接后重试。'
            : '目录读取失败，请检查访问权限。')
        busy.value = false
      } else if (Date.now() >= deadline) {
        error.value = '等待电脑响应超时，请检查执行器是否运行。'
        busy.value = false
      } else timer = setTimeout(() => void poll(id), 1000)
    } catch (failure) {
      if (current !== generation) return
      error.value = errorMessage(failure)
      busy.value = false
    }
  }
  try {
    const created = await request<FileListing>(endpoint, {
      method: 'POST',
      signal,
      body: {
        project_id: props.projectId,
        purpose: props.purpose,
        path: directory,
        limit: 200,
        offset: pageOffset,
      },
    })
    if (current === generation) await poll(created.id)
  } catch (failure) {
    if (current !== generation) return
    error.value = errorMessage(failure)
    busy.value = false
  }
}
function enter(directory: string) {
  history.value.push(path.value)
  void browse(directory)
}
function back() {
  const parent = history.value.pop()
  if (parent) void browse(parent)
}
function changeRoot(event: Event) {
  history.value = []
  void browse((event.target as HTMLSelectElement).value)
}
function choose(entry: FileEntry) {
  if (props.kind === 'files') {
    if (!selected.value.includes(entry.path) && selected.value.length >= 100)
      return
    selected.value = selected.value.includes(entry.path)
      ? selected.value.filter((value) => value !== entry.path)
      : [...selected.value, entry.path]
  } else emit('select', [entry.path])
}
watch(
  () => [props.nodeId, props.projectId, props.purpose, props.roots] as const,
  () => {
    stop()
    history.value = []
    selected.value = []
    entries.value = []
    path.value = ''
    busy.value = false
    error.value = ''
    state.value = ''
    if (props.roots[0]) void browse(props.roots[0])
  },
  { immediate: true },
)
onScopeDispose(stop)
</script>

<template>
  <section class="remote-picker" aria-label="执行电脑文件选择器">
    <header class="toolbar">
      <strong>
        从执行电脑选择{{ kind === 'directory' ? '文件夹' : '文件' }}
      </strong>
      <button class="button small" type="button" @click="emit('close')">
        关闭选择器
      </button>
    </header>
    <p class="muted">
      这里只查看该电脑授权目录，不读取服务器磁盘，也无需共享文件夹。
    </p>
    <label>
      授权根目录
      <select :value="history[0] || path" @change="changeRoot">
        <option v-for="root in roots" :key="root" :value="root">
          {{ root }}
        </option>
      </select>
    </label>
    <p v-if="!roots.length" role="status">
      此项目尚未获准访问目录，请联系管理员配置。
    </p>
    <div class="toolbar">
      <button
        class="button small"
        type="button"
        :disabled="!history.length || busy"
        @click="back"
      >
        上级目录
      </button>
      <span class="path-label">{{ path }}</span>
      <button
        class="button small"
        type="button"
        :disabled="!path || busy"
        @click="browse(path, offset)"
      >
        刷新目录
      </button>
    </div>
    <Feedback :error="error" />
    <p v-if="busy" role="status">正在等待执行电脑读取目录…请保持执行器在线。</p>
    <p v-else-if="state === 'succeeded' && !entries.length" role="status">
      这个目录没有可显示的文件或子目录。
    </p>
    <ul v-else class="file-entries">
      <li v-for="entry in entries" :key="entry.path">
        <button
          v-if="entry.kind === 'directory'"
          class="button small"
          type="button"
          @click="enter(entry.path)"
        >
          打开文件夹 · {{ entry.name }}
        </button>
        <label v-else-if="kind === 'files'" class="checkbox">
          <input
            type="checkbox"
            :checked="selected.includes(entry.path)"
            :disabled="!selected.includes(entry.path) && selected.length >= 100"
            @change="choose(entry)"
          />
          {{ entry.name }}
        </label>
        <button
          v-else-if="kind === 'file'"
          class="button small"
          type="button"
          @click="choose(entry)"
        >
          {{ entry.name }}
        </button>
        <span v-else>{{ entry.name }}</span>
      </li>
    </ul>
    <div v-if="offset > 0 || truncated" class="toolbar">
      <button
        class="button small"
        type="button"
        :disabled="busy || offset === 0"
        @click="browse(path, Math.max(0, offset - 200))"
      >
        上一页文件
      </button>
      <button
        class="button small"
        type="button"
        :disabled="busy || nextOffset === null"
        @click="browse(path, nextOffset!)"
      >
        下一页文件
      </button>
      <small v-if="truncated && nextOffset === null">
        目录内容较多，当前只显示前 200 项，请进入更具体的子目录。
      </small>
    </div>
    <button
      v-if="kind === 'directory'"
      class="button primary small"
      type="button"
      :disabled="busy || state !== 'succeeded'"
      @click="emit('select', [path])"
    >
      选择当前文件夹
    </button>
    <button
      v-if="kind === 'files'"
      class="button primary small"
      type="button"
      :disabled="busy || !selected.length"
      @click="emit('select', selected)"
    >
      使用选中的 {{ selected.length }} 个文件
    </button>
    <p v-if="kind === 'files'" class="muted">
      每个参数最多选择 100 个文件，当前已选 {{ selected.length }} 个。
    </p>
  </section>
</template>

<style scoped>
.remote-picker {
  border: 1px solid var(--border, #dce4ef);
  border-radius: 14px;
  padding: 16px;
  margin: 12px 0;
}
.file-entries {
  list-style: none;
  padding: 0;
  max-height: 280px;
  overflow: auto;
}
.file-entries li {
  padding: 5px 0;
}
.path-label {
  overflow-wrap: anywhere;
  min-width: 0;
  flex: 1;
}
</style>
