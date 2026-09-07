import 'element-plus/theme-chalk/base.css'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { ACCESS_TOKEN_KEY, SESSION_EXPIRED_EVENT, TOKEN_REFRESHED_EVENT } from './api/client'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles.css'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
const auth = useAuthStore(pinia)
window.addEventListener(TOKEN_REFRESHED_EVENT, ((event: CustomEvent<string>) => {
  auth.syncAccessToken(event.detail)
}) as EventListener)
window.addEventListener(SESSION_EXPIRED_EVENT, () => {
  auth.clearSession()
  if (router.currentRoute.value.meta.requiresAuth) router.push('/login')
})
window.addEventListener('storage', (event) => {
  if (event.key !== ACCESS_TOKEN_KEY) return
  if (event.newValue) auth.syncAccessToken(event.newValue)
  else {
    auth.clearSession()
    if (router.currentRoute.value.meta.requiresAuth) router.push('/login')
  }
})

app.use(router).mount('#app')
