<script setup lang="ts">
defineProps<{ error?: string; success?: string; loading?: boolean }>()
</script>
<template>
  <div v-if="error" class="feedback error" role="alert">{{ error }}</div>
  <div v-if="success" class="feedback success" role="status">{{ success }}</div>
  <div v-if="loading" class="loading" role="status">
    <span class="spinner"></span>
    正在读取…
  </div>
</template>
