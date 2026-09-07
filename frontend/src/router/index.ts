import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from '../stores/auth'
const InterviewChatView = () => import('../views/InterviewChatView.vue')
const InterviewHomeView = () => import('../views/InterviewHomeView.vue')
const KnowledgeAdminView = () => import('../views/KnowledgeAdminView.vue')
const LoginView = () => import('../views/LoginView.vue')
const PracticeComparisonView = () => import('../views/PracticeComparisonView.vue')
const ProfileView = () => import('../views/ProfileView.vue')
const RegisterView = () => import('../views/RegisterView.vue')
const ReportDetailView = () => import('../views/ReportDetailView.vue')
const ReportHistoryView = () => import('../views/ReportHistoryView.vue')

const router = createRouter({
  history: createWebHistory(),
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition || { top: 0 }
  },
  routes: [
    { path: '/', redirect: '/interviews' },
    { path: '/login', component: LoginView },
    { path: '/register', component: RegisterView },
    { path: '/profile', component: ProfileView, meta: { requiresAuth: true } },
    { path: '/interviews', component: InterviewHomeView, meta: { requiresAuth: true } },
    { path: '/knowledge', component: KnowledgeAdminView, meta: { requiresAuth: true, requiresAdmin: true } },
    { path: '/interviews/:id', component: InterviewChatView, meta: { requiresAuth: true } },
    { path: '/interviews/:id/report', name: 'interview-report', component: ReportDetailView, meta: { requiresAuth: true } },
    { path: '/practice/:id/comparison', name: 'practice-comparison', component: PracticeComparisonView, meta: { requiresAuth: true } },
    { path: '/reports', component: ReportHistoryView, meta: { requiresAuth: true } },
    { path: '/reports/:id', name: 'report-detail', component: ReportDetailView, meta: { requiresAuth: true } }
  ]
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.initialize()
  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return '/login'
  }
  if (to.meta.requiresAdmin && !auth.isAdmin) {
    return '/interviews'
  }
  if ((to.path === '/login' || to.path === '/register') && auth.isAuthenticated) {
    return '/interviews'
  }
})

export default router
