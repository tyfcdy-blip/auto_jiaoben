<script setup lang="ts">
import { ref } from 'vue'
import { login, session } from '../session'
import { useAction } from '../composables'
import Feedback from './Feedback.vue'
import WorkbenchIcon from './WorkbenchIcon.vue'
const username = ref(''),
  password = ref('')
const { pending, error, perform } = useAction()
async function submit() {
  await perform(async () => {
    await login(username.value.trim(), password.value)
  })
  password.value = ''
}
</script>
<template>
  <main class="login-page">
    <section class="login-intro">
      <div class="login-brand">
        <span class="brand-icon"><WorkbenchIcon name="flow" /></span>
        <span>自动化工作台</span>
      </div>
      <span class="eyebrow">连接程序，让工作有序发生</span>
      <h1>
        把重复的工作，
        <br />
        交给有序的自动化。
      </h1>
      <p>
        从已有脚本到日常调度，
        <br />
        让每次执行有记录，让异常及时被看见。
      </p>
      <div class="login-flow" aria-hidden="true">
        <span>
          <WorkbenchIcon name="program" />
          接入程序
        </span>
        <i></i>
        <span>
          <WorkbenchIcon name="clock" />
          按需调度
        </span>
        <i></i>
        <span>
          <WorkbenchIcon name="run" />
          跟踪结果
        </span>
      </div>
      <footer>
        <span class="environment-badge">开发验证环境</span>
        <span>团队协作，从统一的工作空间开始</span>
      </footer>
    </section>
    <section class="login-form-wrap">
      <form class="login-form" @submit.prevent="submit">
        <div class="login-welcome-icon"><WorkbenchIcon name="shield" /></div>
        <span class="eyebrow">欢迎回来</span>
        <h2>登录工作台</h2>
        <p class="muted">使用管理员为你创建的账号。</p>
        <Feedback :error="error || session.notice" />
        <label>
          账号
          <input
            v-model="username"
            name="username"
            autocomplete="username"
            required
            maxlength="100"
            autofocus
          />
        </label>
        <label>
          密码
          <input
            v-model="password"
            type="password"
            name="password"
            autocomplete="current-password"
            required
            maxlength="256"
          />
        </label>
        <button class="button primary full" :disabled="pending">
          {{ pending ? '正在登录…' : '登录' }}
          <span aria-hidden="true">→</span>
        </button>
        <p class="login-note">
          登录信息仅在本次页面会话中保留。刷新后需要重新登录。
        </p>
      </form>
    </section>
  </main>
</template>
