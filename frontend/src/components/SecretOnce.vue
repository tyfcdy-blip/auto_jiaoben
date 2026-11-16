<script setup lang="ts">
import { ref } from 'vue'
defineProps<{ token: string; identity: string }>()
const copied = ref(false),
  copyError = ref('')
async function copy(token: string) {
  try {
    await navigator.clipboard.writeText(token)
    copied.value = true
    copyError.value = ''
  } catch {
    copyError.value = '无法访问剪贴板，请手动选择并复制凭据。'
  }
}
</script>
<template>
  <section class="secret-panel">
    <strong>凭据仅在创建时显示一次</strong>
    <p class="form-caption">
      请交给获授权的配置人员妥善保存。关闭后无法再次读取，不要发送到群聊或提交到代码仓库。
    </p>
    <div class="mono">身份编号：{{ identity }}</div>
    <code aria-label="一次性显示凭据">{{ token }}</code>
    <button class="button small" @click="copy(token)">
      {{ copied ? '已复制' : '复制凭据' }}
    </button>
    <p v-if="copyError" role="alert">{{ copyError }}</p>
  </section>
</template>
