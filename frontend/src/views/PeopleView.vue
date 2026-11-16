<script setup lang="ts">
import { ref } from 'vue'
import { session, canManage } from '../session'
import { request, loadChoices } from '../api'
import { useAction, useResource } from '../composables'
import type { User, Member, Page, Role } from '../types'
import Feedback from '../components/Feedback.vue'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import StatusBadge from '../components/StatusBadge.vue'
const users = useResource<Page<User>>(
    () => (session.user?.is_admin ? '/users' : null),
    loadChoices<User>,
  ),
  members = useResource<Page<Member>>(
    () => `/projects/${session.projectId}/members`,
    loadChoices<Member>,
  ),
  action = useAction()
const userOpen = ref(false),
  memberOpen = ref(false),
  username = ref(''),
  displayName = ref(''),
  password = ref(''),
  isAdmin = ref(false),
  userId = ref(''),
  role = ref<Role>('viewer')
const roleLabels: Record<Role, string> = {
  project_admin: '项目管理员',
  developer: '开发者',
  operator: '运行人员',
  viewer: '查看者',
}
function closeUser() {
  userOpen.value = false
  password.value = ''
}
async function createUser() {
  await action.perform(async () => {
    await request('/users', {
      method: 'POST',
      body: {
        username: username.value.trim(),
        display_name: displayName.value.trim(),
        password: password.value,
        is_admin: isAdmin.value,
      },
    })
    closeUser()
    username.value = ''
    displayName.value = ''
    isAdmin.value = false
    await users.refresh()
  }, '用户已创建')
  password.value = ''
}
async function saveMember() {
  await action.perform(async () => {
    await request(`/projects/${session.projectId}/members`, {
      method: 'POST',
      body: { user_id: userId.value.trim(), role: role.value },
    })
    memberOpen.value = false
    await members.refresh()
  }, '成员权限已保存')
}
async function toggle(user: User) {
  await action.perform(
    async () => {
      await request(`/users/${user.id}`, {
        method: 'PATCH',
        body: { active: !user.active },
      })
      await users.refresh()
    },
    user.active ? '账号已停用' : '账号已启用',
  )
}
function editMember(member: Member) {
  userId.value = member.user_id
  role.value = member.role
  memberOpen.value = true
}
function addMember() {
  userId.value = ''
  role.value = 'viewer'
  memberOpen.value = true
}
</script>
<template>
  <header class="page-header">
    <div>
      <p class="eyebrow">PEOPLE & ACCESS</p>
      <h1>账号与权限</h1>
      <p>人员账号与自动化身份分开管理，权限限定到项目资源。</p>
    </div>
    <button v-if="canManage" class="button primary" @click="addMember">
      ＋ 添加项目成员
    </button>
  </header>
  <Feedback
    :error="members.error.value || action.error.value"
    :success="action.success.value"
    :loading="members.loading.value"
  />
  <section class="panel">
    <header class="panel-heading"><h2>当前项目成员</h2></header>
    <div v-if="members.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>成员</th>
            <th>角色</th>
            <th>账号编号</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="member in members.result.value.items"
            :key="member.user_id"
          >
            <td class="strong">
              {{ member.display_name || member.username || member.user_id }}
            </td>
            <td>{{ roleLabels[member.role] }}</td>
            <td class="mono">{{ member.user_id }}</td>
            <td class="align-right">
              <button
                v-if="canManage"
                class="button small"
                @click="editMember(member)"
              >
                调整角色
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <EmptyState
      v-else-if="!members.loading.value && !members.error.value"
      title="暂无项目成员记录"
      description="添加成员并授予适合其职责的角色。"
    />
  </section>
  <section v-if="session.user?.is_admin" class="panel section-gap">
    <header class="panel-heading">
      <div>
        <h2>平台账号</h2>
        <p>停用账号前需交接任务负责人和通知责任。</p>
      </div>
      <button class="button" @click="userOpen = true">创建用户</button>
    </header>
    <Feedback :error="users.error.value" :loading="users.loading.value" />
    <div v-if="users.result.value?.items.length" class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>姓名 / 登录账号</th>
            <th>平台角色</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users.result.value.items" :key="user.id">
            <td class="strong">
              {{ user.display_name }}
              <small class="subline">{{ user.username }}</small>
            </td>
            <td>{{ user.is_admin ? '平台管理员' : '普通用户' }}</td>
            <td>
              <StatusBadge :value="user.active ? 'enabled' : 'disabled'" />
            </td>
            <td>
              <button
                class="button small"
                :disabled="action.pending.value || user.id === session.user?.id"
                @click="toggle(user)"
              >
                {{ user.active ? '停用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <Modal
    v-if="userOpen"
    title="创建平台用户"
    :busy="action.pending.value"
    @close="closeUser"
  >
    <form @submit.prevent="createUser">
      <Feedback :error="action.error.value" />
      <label>
        登录账号
        <input v-model="username" required maxlength="100" autocomplete="off" />
      </label>
      <label>
        显示姓名
        <input v-model="displayName" required maxlength="100" />
      </label>
      <label>
        初始密码
        <input
          v-model="password"
          type="password"
          required
          minlength="12"
          maxlength="256"
          autocomplete="new-password"
        />
        <small>至少 12 个字符，通过获授权渠道交给本人。</small>
      </label>
      <label class="checkbox">
        <input v-model="isAdmin" type="checkbox" />
        授予平台管理员权限
      </label>
      <div class="form-actions">
        <button class="button primary" :disabled="action.pending.value">
          创建用户
        </button>
      </div>
    </form>
  </Modal>
  <Modal v-if="memberOpen" title="配置项目成员" @close="memberOpen = false">
    <form @submit.prevent="saveMember">
      <Feedback :error="action.error.value" />
      <label v-if="users.result.value?.items.length">
        账号
        <select v-model="userId" required aria-label="账号">
          <option value="" disabled>请选择已有账号</option>
          <option
            v-for="user in users.result.value.items"
            :key="user.id"
            :value="user.id"
          >
            {{ user.display_name }}（{{ user.username }}）
          </option>
        </select>
      </label>
      <label v-else>
        账号编号
        <input
          v-model="userId"
          required
          placeholder="由平台管理员提供已有账号编号"
        />
      </label>
      <label>
        项目角色
        <select v-model="role">
          <option
            v-for="(label, value) in roleLabels"
            :key="value"
            :value="value"
          >
            {{ label }}
          </option>
        </select>
      </label>
      <p class="form-caption">
        查看者仅查看获授权内容；运行人员可启动任务；开发者可维护程序；项目管理员管理资源授权和发布。
      </p>
      <div class="form-actions">
        <button class="button primary" :disabled="action.pending.value">
          保存成员权限
        </button>
      </div>
    </form>
  </Modal>
</template>
