<script setup lang="ts">
defineProps<{ title?: string; description?: string }>()
</script>
<template>
  <div class="empty">
    <div class="empty-mark" aria-hidden="true">空</div>
    <h3>{{ title || '暂无记录' }}</h3>
    <p>{{ description || '完成登记后，相关记录将在这里显示。' }}</p>
    <slot />
  </div>
</template>
