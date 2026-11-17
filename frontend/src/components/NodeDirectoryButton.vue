<script setup lang="ts">
import { ref, watch } from 'vue'
import type { Node } from '../types'
import { useResource } from '../composables'
import { NODE_FILES_CAPABILITY, type FileConfiguration } from '../nodeFiles'
import RemotePathPicker from './RemotePathPicker.vue'
import Feedback from './Feedback.vue'
const props = defineProps<{
  node: Node | null
  projectId: string
  disabled?: boolean
}>()
const emit = defineEmits<{ select: [path: string] }>()
const open = ref(false)
function select(paths: string[]) {
  emit('select', paths[0] || '')
  open.value = false
}
const configuration = useResource<FileConfiguration>(() =>
  open.value && props.node
    ? `/nodes/${props.node.id}/file-configuration?project_id=${encodeURIComponent(props.projectId)}`
    : null,
)
watch(
  () => [props.node?.id, props.projectId],
  () => {
    open.value = false
  },
)
</script>
<template>
  <div v-if="node?.capabilities.includes(NODE_FILES_CAPABILITY)">
    <button
      class="button small"
      type="button"
      :disabled="disabled || !node.online"
      @click="open = !open"
    >
      从执行电脑选择输入文件夹
    </button>
    <p v-if="!node.online" class="muted">电脑离线，暂时无法读取目录。</p>
    <Feedback
      v-if="open"
      :error="configuration.error.value"
      :loading="configuration.loading.value"
    />
    <RemotePathPicker
      v-if="open && node.online && configuration.result.value"
      :node-id="node.id"
      :project-id="projectId"
      purpose="input"
      kind="directory"
      :roots="configuration.result.value.allowed_input_roots"
      @select="select"
      @close="open = false"
    />
  </div>
</template>
