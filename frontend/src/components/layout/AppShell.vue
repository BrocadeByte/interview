<script setup lang="ts">
import { ChatDotRound, Collection, DataAnalysis, SwitchButton, User } from '@element-plus/icons-vue'
import { ElIcon, ElTooltip } from 'element-plus'
import 'element-plus/theme-chalk/el-icon.css'
import 'element-plus/theme-chalk/el-tooltip.css'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '../../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const navigation = computed(() => [
  { label: '开始训练', path: '/interviews', icon: ChatDotRound, visible: true },
  { label: '训练报告', path: '/reports', icon: DataAnalysis, visible: true },
  { label: '求职画像', path: '/profile', icon: User, visible: true },
  { label: '知识库', path: '/knowledge', icon: Collection, visible: auth.isAdmin }
])

const userInitial = computed(() => (auth.user?.username || auth.user?.email || 'U').slice(0, 1).toUpperCase())

function isActive(path: string) {
  if (path === '/interviews') return route.path.startsWith('/interviews')
  if (path === '/reports') return route.path.startsWith('/reports') || route.path.startsWith('/practice')
  return route.path === path || route.path.startsWith(`${path}/`)
}

async function logout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar shell-topbar">
      <button class="brand" type="button" aria-label="返回开始训练" @click="router.push('/interviews')">
        <span class="brand-mark"><el-icon><ChatDotRound /></el-icon></span>
        <span class="brand-copy">
          <strong>智面 AI</strong>
          <small>技术求职者面试训练助手</small>
        </span>
      </button>

      <nav class="primary-nav" aria-label="主导航">
        <button
          v-for="item in navigation.filter((entry) => entry.visible)"
          :key="item.path"
          class="nav-item"
          :class="{ active: isActive(item.path) }"
          type="button"
          :aria-current="isActive(item.path) ? 'page' : undefined"
          @click="router.push(item.path)"
        >
          <el-icon aria-hidden="true"><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <div class="user-menu">
        <span class="user-avatar">{{ userInitial }}</span>
        <span class="user-copy">
          <strong>{{ auth.user?.username || '求职者' }}</strong>
          <small>{{ auth.isAdmin ? '系统管理员' : '个人账号' }}</small>
        </span>
        <el-tooltip content="退出登录" placement="bottom">
          <button class="icon-button" type="button" aria-label="退出登录" @click="logout">
            <el-icon><SwitchButton /></el-icon>
          </button>
        </el-tooltip>
      </div>
    </header>

    <div class="app-content-shell"><slot /></div>
  </div>
</template>
