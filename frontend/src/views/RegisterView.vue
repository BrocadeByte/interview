<script setup lang="ts">
import { ArrowRight, Lock, Message, User } from '@element-plus/icons-vue'
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
const form = reactive({ email: '', username: '', password: '' })

async function submit() {
  loading.value = true
  try {
    await auth.register(form.email, form.username, form.password)
    router.push('/profile')
  } catch {
    ElMessage.error('注册失败，邮箱可能已被使用')
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
        <span class="eyebrow eyebrow-light">你的求职训练搭档</span>
        <h1>三分钟建立求职画像，<br />开始岗位定制训练。</h1>
        <p>记录你的技术能力、项目经历和目标岗位，让每轮训练都更贴合真实求职准备。</p>
      </div>
      <div class="auth-proof">
        <div><strong>岗位定制</strong><span>围绕目标职位出题</span></div>
        <div><strong>进度留存</strong><span>持续记录训练轨迹</span></div>
        <div><strong>隐私保护</strong><span>个人资料安全管理</span></div>
      </div>
    </section>

    <section class="auth-form-area">
      <div class="auth-panel">
        <div class="auth-heading">
          <span class="eyebrow">开始训练</span>
          <h2>创建求职者账号</h2>
          <p>完善个人画像，开始第一场岗位定制训练</p>
        </div>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="邮箱">
            <el-input v-model="form.email" type="email" placeholder="you@example.com" :prefix-icon="Message" size="large" />
          </el-form-item>
          <el-form-item label="用户名">
            <el-input v-model="form.username" placeholder="请输入用户名" :prefix-icon="User" size="large" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password placeholder="至少 6 位" :prefix-icon="Lock" size="large" />
          </el-form-item>
          <el-button type="primary" native-type="submit" :loading="loading" class="full-button" size="large">
            创建账号<el-icon class="el-icon--right"><ArrowRight /></el-icon>
          </el-button>
        </el-form>
        <div class="auth-switch">已经有账号？<RouterLink to="/login">返回登录</RouterLink></div>
      </div>
    </section>
  </main>
</template>
