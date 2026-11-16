<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
const props = defineProps<{ title: string; wide?: boolean; busy?: boolean }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLElement>(),
  previous = document.activeElement as HTMLElement | null
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && !props.busy) emit('close')
  if (event.key !== 'Tab') return
  const controls = Array.from(
    dialog.value?.querySelectorAll<HTMLElement>(
      'button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]',
    ) || [],
  ).filter((control) => !control.hidden)
  const first = controls[0],
    last = controls.at(-1)
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}
onMounted(async () => {
  document.addEventListener('keydown', keydown)
  await nextTick()
  dialog.value
    ?.querySelector<HTMLElement>('input,select,textarea,button')
    ?.focus()
})
onUnmounted(() => {
  document.removeEventListener('keydown', keydown)
  previous?.focus()
})
</script>
<template>
  <Teleport to="body">
    <div class="modal-backdrop">
      <section
        ref="dialog"
        class="modal"
        :class="{ wide }"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
      >
        <header class="modal-header">
          <h2>{{ title }}</h2>
          <button
            class="icon-button"
            aria-label="关闭对话框"
            :disabled="busy"
            @click="emit('close')"
          >
            ×
          </button>
        </header>
        <div class="modal-body"><slot /></div>
      </section>
    </div>
  </Teleport>
</template>
