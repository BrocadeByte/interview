import { defineStore } from 'pinia'

import {
  fetchMe,
  fetchSession,
  login,
  logoutAllSessions,
  logoutSession,
  register,
  type User
} from '../api/auth'
import { getAccessToken, setAccessToken } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    token: getAccessToken(),
    initialized: false
  }),
  getters: {
    isAuthenticated: (state) => Boolean(state.token),
    isAdmin: (state) => Boolean(state.user?.is_admin)
  },
  actions: {
    async login(email: string, password: string) {
      const { data } = await login({ email, password })
      this.setSession(data.access_token, data.user)
    },
    async register(email: string, username: string, password: string) {
      const { data } = await register({ email, username, password })
      this.setSession(data.access_token, data.user)
    },
    async loadMe() {
      if (!this.token) return
      const { data } = await fetchMe()
      this.user = data
    },
    async initialize() {
      if (this.initialized) return
      try {
        const { data } = await fetchSession()
        if (data.authenticated && data.access_token && data.user) {
          this.setSession(data.access_token, data.user)
        } else {
          this.clearSession()
        }
      } catch {
        this.clearSession()
      } finally {
        this.initialized = true
      }
    },
    setSession(token: string, user: User) {
      this.token = token
      this.user = user
      setAccessToken(token)
    },
    syncAccessToken(token: string) {
      this.token = token
      setAccessToken(token)
    },
    clearSession() {
      this.token = ''
      this.user = null
      setAccessToken(null)
    },
    async logout() {
      try {
        await logoutSession()
      } catch {
        // 本地会话仍需清理，避免网络故障把用户困在已退出的页面。
      } finally {
        this.clearSession()
      }
    },
    async logoutAll() {
      try {
        await logoutAllSessions()
      } catch {
        // 与单设备退出保持一致，本地状态始终立即失效。
      } finally {
        this.clearSession()
      }
    }
  }
})

