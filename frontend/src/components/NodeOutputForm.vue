<script setup lang="ts">
import { ref, watch } from 'vue'
import { request } from '../api'
import { useAction, useResource } from '../composables'
import type { FileConfiguration } from '../nodeFiles'
import type { Node, Project } from '../types'
import Feedback from './Feedback.vue'
const props = defineProps<{
  node: Node
  projectId: string
  projects: Pick<Project, 'id' | 'name'>[]
}>()
const configuration = useResource<FileConfiguration>(
  () =>
    `/nodes/${props.node.id}/file-configuration?project_id=${encodeURIComponent(props.projectId)}`,
)
const action = useAction()
const roots = ref('')
const grants = ref<Record<string, string>>({})
watch(configuration.result, (value) => {
  if (!value) return
  roots.value = value.output_roots.join('\n')
  grants.value = Object.fromEntries(
    value.grants.map((grant) => [
      grant.project_id,
      grant.output_roots.join('\n'),
    ]),
  )
})
function lines(value: string) {
  return [
    ...new Set(
      value
        .split(/\r?\n/)
        .map((entry) => entry.trim())
        .filter(Boolean),
    ),
  ]
}
async function save() {
  const current = configuration.result.value
  if (!current) return
  await action.perform(async () => {
    await request(`/nodes/${props.node.id}/file-configuration`, {
      method: 'PUT',
      body: {
        base_revision: current.revision,
        output_roots: lines(roots.value),
        grants: Object.entries(grants.value).map(([project_id, value]) => ({
          project_id,
          output_roots: lines(value),
        })),
      },
    })
    await configuration.refresh()
  }, '结果目录授权已保存，等待执行电脑确认。')
}
</script>
<template>
  <section class="panel content-padding section-gap">
    <h2>结果保存目录授权</h2>
    <p class="muted">
      与读取目录分开管理。只允许另存程序声明的结果文件，每次运行独立保存，不覆盖已有文件。
    </p>
    <Feedback
      :error="configuration.error.value || action.error.value"
      :success="action.success.value"
      :loading="configuration.loading.value"
    />
    <p v-if="configuration.result.value">
      授权版本 {{ configuration.result.value.revision }} · 电脑已应用
      {{ configuration.result.value.applied_revision }} ·
      {{
        configuration.result.value.apply_status === 'applied'
          ? '已同步'
          : '等待同步或需检查执行器'
      }}
    </p>
    <button
      class="button small"
      type="button"
      :disabled="configuration.loading.value || action.pending.value"
      @click="configuration.refresh"
    >
      重新读取已保存配置
    </button>
    <p v-if="!node.maintenance" class="feedback warning">
      请先在执行电脑列表中进入维护状态，再调整结果目录授权。
    </p>
    <form v-if="configuration.result.value" @submit.prevent="save">
      <fieldset :disabled="action.pending.value || !node.maintenance">
        <label>
          电脑允许保存结果的根目录
          <textarea
            v-model="roots"
            rows="3"
            placeholder="一行一个执行电脑上的绝对路径，例如 D:\自动化结果"
          ></textarea>
        </label>
        <label v-for="project in projects" :key="project.id">
          {{ project.name }}可保存的目录
          <textarea
            v-model="grants[project.id]"
            rows="2"
            placeholder="一行一个，必须位于上面的根目录内；留空表示不授权"
          ></textarea>
        </label>
        <button class="button primary" type="submit">
          {{ action.pending.value ? '保存中…' : '保存结果目录授权' }}
        </button>
      </fieldset>
    </form>
  </section>
</template>
