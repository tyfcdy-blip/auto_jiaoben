<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Node } from '../types'
import { useResource } from '../composables'
import {
  fileFields,
  NODE_FILES_CAPABILITY,
  type FileConfiguration,
  type FileOptions,
} from '../nodeFiles'
import RemotePathPicker from './RemotePathPicker.vue'
import Feedback from './Feedback.vue'

const props = defineProps<{
  modelValue: FileOptions
  node: Node | null
  projectId: string
  schema?: Record<string, unknown> | null
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: FileOptions] }>()
const supported = computed(() =>
  Boolean(props.node?.capabilities.includes(NODE_FILES_CAPABILITY)),
)
const fields = computed(() => fileFields(props.schema))
const picker = ref<{
  key: string
  kind: 'file' | 'files' | 'directory'
  purpose: 'input' | 'output'
} | null>(null)
const configuration = useResource<FileConfiguration>(() =>
  supported.value && props.node && props.projectId
    ? `/nodes/${encodeURIComponent(props.node.id)}/file-configuration?project_id=${encodeURIComponent(props.projectId)}`
    : null,
)
const writable = computed(
  () => configuration.result.value?.apply_status === 'applied',
)
watch(
  () => [props.node?.id, props.projectId, props.schema],
  () => {
    picker.value = null
  },
)
function select(paths: string[]) {
  const target = picker.value
  if (!target) return
  emit(
    'update:modelValue',
    target.purpose === 'output'
      ? { ...props.modelValue, output_dir: paths[0] || null }
      : {
          ...props.modelValue,
          inputs: {
            ...props.modelValue.inputs,
            [target.key]: target.kind === 'files' ? paths : paths[0] || '',
          },
        },
  )
  picker.value = null
}
function clear(key: string) {
  const inputs = { ...props.modelValue.inputs }
  delete inputs[key]
  emit('update:modelValue', { ...props.modelValue, inputs })
}
function valueLabel(key: string) {
  const value = props.modelValue.inputs[key]
  return Array.isArray(value) ? value.join('\n') : value || '未选择'
}
</script>

<template>
  <section class="file-options section-gap">
    <h3>执行电脑上的输入与结果</h3>
    <p v-if="!node" class="muted">
      先选择固定执行电脑，再选择它的文件和结果保存位置。
    </p>
    <p v-else-if="!supported" class="muted">
      这台电脑尚未报告文件选择能力，请先升级并启动执行器。原有任务配置仍可使用。
    </p>
    <template v-else>
      <Feedback
        :error="configuration.error.value"
        :loading="configuration.loading.value"
      />
      <button
        type="button"
        class="button small"
        :disabled="disabled || configuration.loading.value"
        @click="configuration.refresh"
      >
        刷新目录授权
      </button>
      <p v-if="!node.online" role="status">
        电脑离线，暂时无法浏览目录；已保存的文件设置仍保留。
      </p>
      <div v-for="field in fields" :key="field.key" class="file-field">
        <strong>{{ field.title }}</strong>
        <pre>{{ valueLabel(field.key) }}</pre>
        <button
          type="button"
          class="button small"
          :disabled="
            disabled ||
            !node.online ||
            !configuration.result.value?.allowed_input_roots.length
          "
          @click="
            picker = { key: field.key, kind: field.kind, purpose: 'input' }
          "
        >
          选择{{
            field.kind === 'directory'
              ? '文件夹'
              : field.kind === 'files'
                ? '多个文件'
                : '文件'
          }}
        </button>
        <button
          v-if="modelValue.inputs[field.key]"
          type="button"
          class="button small"
          :disabled="disabled"
          @click="clear(field.key)"
        >
          清除选择
        </button>
      </div>
      <p
        v-if="
          fields.length &&
          !configuration.loading.value &&
          !configuration.result.value?.allowed_input_roots.length
        "
        class="muted"
      >
        本项目还没有可读取的授权目录，请在电脑维护中配置。
      </p>
      <div class="file-field">
        <strong>结果另存到执行电脑（可选）</strong>
        <pre>{{ modelValue.output_dir || '仅回传工作台，不另存' }}</pre>
        <p class="muted">
          只保存程序声明的结果文件，每次运行自动创建独立子文件夹。这里选择保存位置，不改变程序业务参数。
        </p>
        <button
          type="button"
          class="button small"
          :disabled="
            disabled ||
            !node.online ||
            !writable ||
            !configuration.result.value?.allowed_output_roots.length
          "
          @click="picker = { key: '', kind: 'directory', purpose: 'output' }"
        >
          选择结果保存文件夹
        </button>
        <button
          v-if="modelValue.output_dir"
          type="button"
          class="button small"
          :disabled="disabled"
          @click="
            emit('update:modelValue', { ...modelValue, output_dir: null })
          "
        >
          取消另存
        </button>
        <p
          v-if="
            !configuration.loading.value &&
            !configuration.result.value?.allowed_output_roots.length
          "
          class="muted"
        >
          尚未授权结果保存目录，请平台管理员在电脑详情中单独设置。
        </p>
        <p v-else-if="!writable" class="muted">
          结果目录授权尚未同步到电脑，请等待执行器确认后重试。
        </p>
      </div>
      <RemotePathPicker
        v-if="picker && configuration.result.value && node.online"
        :key="`${node.id}:${picker.purpose}:${picker.key}`"
        :node-id="node.id"
        :project-id="projectId"
        :purpose="picker.purpose"
        :kind="picker.kind"
        :roots="
          picker.purpose === 'input'
            ? configuration.result.value.allowed_input_roots
            : configuration.result.value.allowed_output_roots
        "
        @select="select"
        @close="picker = null"
      />
    </template>
  </section>
</template>

<style scoped>
.file-field {
  padding: 12px 0;
  border-bottom: 1px solid var(--border, #dce4ef);
}
.file-field pre {
  font: inherit;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 8px 0;
  color: var(--text-muted, #64748b);
}
.file-field .button + .button {
  margin-left: 8px;
}
</style>
