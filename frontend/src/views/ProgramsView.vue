<script setup lang="ts">
import { ref, watch } from 'vue'
import { session, canDevelop, canManage, projectName } from '../session'
import { downloadArtifact, request } from '../api'
import { useAction, useList, useResource } from '../composables'
import type { Program, Version, Page } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import StatusBadge from '../components/StatusBadge.vue'
import Pagination from '../components/Pagination.vue'
import ProgramImport from '../components/ProgramImport.vue'
const programs = useList<Program>('/programs')
const selected = ref<Program | null>(null)
const importing = ref(false)
const importProgram = ref<Program | null>(null)
const importDraft = ref<Version | null>(null)
const versions = useResource<Page<Version>>(
  () => (selected.value ? `/programs/${selected.value.id}/versions` : null),
  // 当前版本接口一次返回200条，不支持分页；不让通用分页器重复加载同一页。
)
const action = useAction()
watch(
  () => session.projectId,
  () => {
    selected.value = null
    importing.value = false
    action.error.value = ''
    action.success.value = ''
  },
)
function openImport(
  program: Program | null = null,
  draft: Version | null = null,
) {
  importProgram.value = program
  importDraft.value = draft
  importing.value = true
}
async function saved(program: Program) {
  importing.value = false
  selected.value = program
  await Promise.all([programs.refresh(), versions.refresh()])
  action.success.value = '程序包已保存。已发布的版本可以用于创建任务。'
}
async function refresh() {
  await Promise.all([programs.refresh(), versions.refresh()])
}
function closeImport() {
  importing.value = false
  void refresh()
}
async function publish(version: Version) {
  const programId = selected.value?.id
  await action.perform(async () => {
    await request(`/versions/${version.id}/publish`, { method: 'POST' })
    if (selected.value?.id === programId) await versions.refresh()
  }, '版本已发布并封存')
}
async function download(version: Version) {
  const programName = (selected.value?.name || 'program').replace(
    /[\\/:*?"<>|]/g,
    '-',
  )
  const versionName = version.name.replace(/[\\/:*?"<>|]/g, '-')
  await action.perform(
    () =>
      downloadArtifact(
        `/versions/${version.id}/bundle`,
        `${programName}-${versionName}.zip`,
      ),
    '程序包已开始下载',
  )
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">团队的自动化能力</p>
      <h1>程序库</h1>
      <p>接入一次，按需运行。程序更新与历史记录都在这里。</p>
    </div>
    <button v-if="canDevelop" class="button primary" @click="openImport()">
      接入程序
    </button>
  </header>
  <Feedback
    :error="programs.error.value || action.error.value"
    :success="action.success.value"
    :loading="programs.loading.value"
  />
  <div class="row-actions section-gap">
    <button class="button subtle small" @click="refresh">刷新</button>
  </div>
  <section class="panel">
    <div v-if="programs.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>程序名称</th>
            <th>用途说明</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="program in programs.result.value.items" :key="program.id">
            <td>
              <button class="text-link strong" @click="selected = program">
                {{ program.name }}
              </button>
              <small v-if="!session.projectId" class="subline project-label">
                {{ projectName(program.project_id) }}
              </small>
            </td>
            <td>{{ program.description || '暂无' }}</td>
            <td class="align-right">
              <button
                v-if="!session.projectId"
                class="button subtle small"
                @click="session.projectId = program.project_id"
              >
                进入项目
              </button>
              <button class="button subtle small" @click="selected = program">
                查看与更新 →
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!programs.loading.value && !programs.error.value"
      title="把第一个程序交给工作台"
      description="准备好 ZIP 程序包，选择入口，即可一次完成接入。"
    />
    <Pagination
      v-if="programs.result.value"
      v-model:offset="programs.offset.value"
      :total="programs.result.value.total"
      :loading="programs.loading.value"
    />
  </section>
  <section v-if="selected" class="panel section-gap">
    <header class="panel-heading">
      <div>
        <h2>
          {{ selected.name }}
          <span class="muted">/ 版本</span>
        </h2>
        <p>每次更新保留一个独立版本，已有任务继续使用原版本。</p>
      </div>
      <button v-if="canDevelop" class="button" @click="openImport(selected)">
        更新程序
      </button>
    </header>
    <Feedback :error="versions.error.value" :loading="versions.loading.value" />
    <div v-if="versions.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>版本</th>
            <th>状态</th>
            <th>启动入口</th>
            <th>程序包</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="version in versions.result.value.items" :key="version.id">
            <td class="strong">
              {{ version.name }}
              <small v-if="version.side_effects" class="subline warning-text">
                含外部写入
              </small>
            </td>
            <td>
              <StatusBadge :value="version.published ? 'published' : 'draft'" />
            </td>
            <td class="mono">{{ version.command.join(' ') }}</td>
            <td>
              <span
                v-if="version.bundle_sha256"
                class="mono"
                :title="version.bundle_sha256"
              >
                {{ version.bundle_sha256.slice(0, 12) }}…
              </span>
              <span v-else class="muted">尚未上传</span>
            </td>
            <td>
              <div class="row-actions">
                <button
                  v-if="version.bundle_sha256"
                  class="button subtle small"
                  :disabled="action.pending.value"
                  @click="download(version)"
                >
                  下载程序包
                </button>
                <button
                  v-if="!version.published && canDevelop"
                  class="button small"
                  :disabled="action.pending.value"
                  @click="openImport(selected, version)"
                >
                  继续接入
                </button>
                <button
                  v-if="!version.published && canManage"
                  class="button primary small"
                  :disabled="!version.bundle_sha256 || action.pending.value"
                  @click="publish(version)"
                >
                  发布
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!versions.loading.value"
      title="尚无版本"
      description="点击更新程序，选择程序包完成接入。"
    />
  </section>
  <ProgramImport
    v-if="importing"
    :program="importProgram"
    :draft="importDraft"
    @close="closeImport"
    @saved="saved"
  />
</template>
