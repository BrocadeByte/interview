<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AppShell from './components/layout/AppShell.vue'

const route = useRoute()
const isAuthPage = computed(() => route.path === '/login' || route.path === '/register')
</script>

<template>
  <router-view v-if="isAuthPage" />
  <AppShell v-else>
    <router-view />
  </AppShell>
</template>
