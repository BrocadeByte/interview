<script setup lang="ts">
import { ArrowRight, Lock, Message } from '@element-plus/icons-vue'
import { ElButton, ElForm, ElFormItem, ElIcon, ElInput, ElMessage } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-input.css'
import 'element-plus/theme-chalk/el-message.css'
import { reactive, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const form = reactive({ email: '', password: '' })

async function submit() {
  loading.value = true
  try {
    await auth.login(form.email, form.password)
    router.push('/interviews')
  } catch {
    ElMessage.error('登录失败，请检查邮箱和密码')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-showcase">
      <div class="auth-brand">
        <span class="brand-mark brand-mark-large">AI</span>
        <strong>智面 AI</strong>
      </div>
      <div class="auth-pitch">
        <span class="eyebrow eyebrow-light">面向技术求职者的岗位训练</span>
        <h1>用简历和 JD，<br />定制你的面试训练。</h1>
        <p>围绕目标技术岗位定制问题与追问，通过多维评估和复盘找到下一步提升方向。</p>
      </div>
      <div class="auth-proof">
        <div><strong>岗位定制</strong><span>围绕技术求职目标出题</span></div>
        <div><strong>多维评估</strong><span>能力表现清晰量化</span></div>
        <div><strong>行动建议</strong><span>每轮训练都有提升方向</span></div>
      </div>
    </section>

    <section class="auth-form-area">
      <div class="auth-panel">
        <div class="auth-heading">
          <span class="eyebrow">欢迎回来</span>
          <h2>登录智面 AI</h2>
          <p>使用你的账号继续岗位定制训练</p>
        </div>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="邮箱">
            <el-input v-model="form.email" type="email" placeholder="you@example.com" :prefix-icon="Message" size="large" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password placeholder="请输入密码" :prefix-icon="Lock" size="large" />
          </el-form-item>
          <el-button type="primary" native-type="submit" :loading="loading" class="full-button" size="large">
            登录<el-icon class="el-icon--right"><ArrowRight /></el-icon>
          </el-button>
        </el-form>
        <div class="auth-switch">还没有账号？<RouterLink to="/register">创建账号</RouterLink></div>
      </div>
    </section>
  </main>
</template>
