<script setup lang="ts">
defineProps<{ name: string }>()
const paths: Record<string, string> = {
  overview: 'M3 10 12 3l9 7M5 9v12h5v-7h4v7h5V9',
  program: 'm12 3 9 5v8l-9 5-9-5V8l9-5Zm0 9 9-4M12 12 3 8m9 4v9M7.5 5.5l9 5',
  task: 'M9 5h12M9 12h12M9 19h12M3 5l1 1 2-2M3 12l1 1 2-2M3 19l1 1 2-2',
  clock: 'M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  run: 'm9 7 8 5-8 5V7ZM21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
  node: 'M4 4h16v6H4V4Zm0 10h16v6H4v-6ZM7 7h.01M7 17h.01M11 7h6M11 17h6',
  batch: 'M8 3h8l4 4v14H8V3Zm8 0v5h4M4 7v14M11 12h6M11 16h6',
  event:
    'M9 8 6 5 3 8m3-3v11a3 3 0 0 0 3 3h3M15 16l3 3 3-3m-3 3V8a3 3 0 0 0-3-3h-3',
  alert: 'M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9ZM10 21h4',
  people:
    'M15 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-4-4M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm4-4a4 4 0 0 1 0 8',
  audit: 'M5 3h10l4 4v14H5V3Zm9 0v5h5M8 12h8M8 16h5',
  arrow: 'M4 12h16m-6-6 6 6-6 6',
  refresh:
    'M20 7v5h-5M4 17v-5h5M5.6 7a7 7 0 0 1 11.7-2L20 8M4 16l2.7 3A7 7 0 0 0 18.4 17',
  menu: 'M4 6h16M4 12h16M4 18h16',
  flow: 'M3 4h6v6H3V4Zm12 10h6v6h-6v-6ZM9 7h6a3 3 0 0 1 3 3v4M6 10v7h9',
  shield: 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6',
}
</script>
<template>
  <svg
    class="workbench-icon"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    <path :d="paths[name] || paths.overview" />
  </svg>
</template>
