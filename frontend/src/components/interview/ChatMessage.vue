<script setup lang="ts">
defineProps<{
  role: 'assistant' | 'user'
  content: string
  streaming?: boolean
}>()
</script>

<template>
  <div class="message" :class="role">
    <div class="message-role">{{ role === 'assistant' ? 'AI 面试官' : '我' }}</div>
    <div class="message-content">
      {{ content }}<span v-if="streaming" class="typing-cursor">|</span>
    </div>
  </div>
</template>
