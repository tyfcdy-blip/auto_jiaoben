<script setup lang="ts">
defineProps<{
  inputDir: string
  batchKey: string
  scheduled?: boolean
  disabled?: boolean
}>()
const emit = defineEmits<{
  'update:inputDir': [value: string]
  'update:batchKey': [value: string]
}>()
</script>
<template>
  <div class="form-grid">
    <label>
      输入文件夹
      <input
        :value="inputDir"
        aria-label="输入文件夹"
        required
        maxlength="2000"
        :disabled="disabled"
        placeholder="例如：D:\账单\本次下载"
        @input="
          emit('update:inputDir', ($event.target as HTMLInputElement).value)
        "
      />
      <small>
        这是执行电脑上的路径，不是服务器路径，无需与服务器共享文件夹。先完成全部下载，再触发任务；目录必须已获授权，运行期间保持文件内容不变。
      </small>
    </label>
    <label>
      批次编号
      <input
        :value="batchKey"
        aria-label="目录批次编号"
        required
        maxlength="180"
        :disabled="disabled"
        :placeholder="
          scheduled
            ? '固定编号或 ${scheduled_date}'
            : '例如：2026-09-17-第一次下载'
        "
        @input="
          emit('update:batchKey', ($event.target as HTMLInputElement).value)
        "
      />
      <small v-if="scheduled">
        可用完整的 ${scheduled_date} 或
        ${previous_month}；文件夹路径保持固定，不拼日期路径。
      </small>
      <small v-else>每次新交付使用新的编号，同一批文件重试沿用原编号。</small>
    </label>
  </div>
</template>
