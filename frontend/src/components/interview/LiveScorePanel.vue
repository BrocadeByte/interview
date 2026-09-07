<script setup lang="ts">
import { Check, RefreshRight, TrendCharts } from '@element-plus/icons-vue'
import { ElButton, ElIcon } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-icon.css'
import { computed, ref, watch } from 'vue'

import type { InterviewScore } from '../../api/interview'

const props = defineProps<{
  scores: InterviewScore[]
  loading?: boolean
  error?: boolean
}>()

defineEmits<{
  retry: []
}>()

const selectedScoreId = ref<number | null>(null)

const latestScore = computed(() => props.scores[props.scores.length - 1] || null)
const selectedScore = computed(
  () => props.scores.find((item) => item.id === selectedScoreId.value) || latestScore.value
)
const averageScore = computed(() => {
  if (props.scores.length === 0) return 0
  return Math.round(props.scores.reduce((total, item) => total + item.score, 0) / props.scores.length)
})
const scoreTone = computed(() => {
  const value = selectedScore.value?.score || 0
  if (value >= 85) return 'excellent'
  if (value >= 70) return 'good'
  return 'improving'
})
const feedbackTitle = computed(() => {
  const value = selectedScore.value?.score || 0
  if (value >= 85) return '这一轮表现出色'
  if (value >= 70) return '方向正确，继续保持'
  return '已经完成，下一轮会更好'
})
const primarySuggestion = computed(
  () => selectedScore.value?.suggestions.find((item) => item.trim()) || '继续用具体情境、行动和结果组织回答。'
)

watch(
  () => latestScore.value?.id,
  (id) => {
    if (id) selectedScoreId.value = id
  },
  { immediate: true }
)
</script>

<template>
  <aside class="live-score-panel" aria-live="polite" aria-label="实时评分">
    <div class="live-score-header">
      <div>
        <span>实时评分</span>
        <strong>每次作答后更新</strong>
      </div>
      <el-icon aria-hidden="true"><TrendCharts /></el-icon>
    </div>

    <div v-if="loading && scores.length === 0" class="score-panel-state">
      <span class="thinking-pulse" aria-hidden="true"></span>
      正在同步本场评分...
    </div>

    <div v-else-if="error && scores.length === 0" class="score-panel-state score-panel-error">
      <span>评分暂未同步</span>
      <el-button text type="primary" @click="$emit('retry')">
        <el-icon aria-hidden="true"><RefreshRight /></el-icon>重试
      </el-button>
    </div>

    <div v-else-if="scores.length === 0" class="score-panel-empty">
      <strong>完成第一轮回答后</strong>
      <span>这里会显示本题得分和改进建议</span>
    </div>

    <template v-else-if="selectedScore">
      <div class="score-overview" :class="scoreTone">
        <div class="score-ring">
          <strong>{{ selectedScore.score }}</strong>
          <span>分</span>
        </div>
        <div class="score-summary">
          <span>第 {{ selectedScore.question_index }} 题 · {{ selectedScore.dimension }}</span>
          <strong>{{ feedbackTitle }}</strong>
          <small>本场平均 {{ averageScore }} 分 · 已评 {{ scores.length }} 轮</small>
        </div>
      </div>

      <div class="score-feedback">
        <div class="score-feedback-title">
          <el-icon><Check /></el-icon>
          <strong>本轮反馈</strong>
        </div>
        <p>{{ selectedScore.reason || '已完成本轮回答，评分结果已记录。' }}</p>
      </div>

      <div class="score-next-step">
        <span>下一轮可以这样提升</span>
        <p>{{ primarySuggestion }}</p>
      </div>

      <div class="score-history" role="group" aria-label="选择评分轮次">
        <button
          v-for="(item, index) in scores"
          :key="item.id"
          type="button"
          :class="{ active: item.id === selectedScore.id }"
          :aria-label="`查看第 ${index + 1} 轮评分，${item.score} 分`"
          :aria-pressed="item.id === selectedScore.id"
          @click="selectedScoreId = item.id"
        >
          <span>{{ index + 1 }}</span>
          <strong>{{ item.score }}</strong>
        </button>
      </div>
    </template>
  </aside>
</template>
