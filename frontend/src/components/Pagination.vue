<script setup lang="ts">
defineProps<{ offset: number; total: number; loading?: boolean }>()
const emit = defineEmits<{ 'update:offset': [value: number] }>()
</script>
<template>
  <footer class="pagination">
    <span>
      共 {{ total }} 条
      <span v-if="total">
        · 当前 {{ offset + 1 }} 至 {{ Math.min(offset + 50, total) }}
      </span>
    </span>
    <div>
      <button
        class="button subtle small"
        :disabled="offset === 0 || loading"
        @click="emit('update:offset', Math.max(0, offset - 50))"
      >
        上一页
      </button>
      <button
        class="button subtle small"
        :disabled="offset + 50 >= total || loading"
        @click="emit('update:offset', offset + 50)"
      >
        下一页
      </button>
    </div>
  </footer>
</template>
