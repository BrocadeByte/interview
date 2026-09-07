<script setup lang="ts">
import { RefreshRight } from '@element-plus/icons-vue'
import { ElButton, ElEmpty, ElMessage, vLoading } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-empty.css'
import 'element-plus/theme-chalk/el-loading.css'
import 'element-plus/theme-chalk/el-message.css'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { fetchReports, type InterviewReportListItem } from '../api/report'

const router = useRouter()
const reports = ref<InterviewReportListItem[]>([])
const loading = ref(false)

function difficultyLabel(value: string) {
  const labels: Record<string, string> = { easy: '基础', medium: '标准', hard: '进阶' }
  return labels[value] || value
}

async function load() {
  loading.value = true
  try {
    const { data } = await fetchReports()
    reports.value = data
  } catch {
    ElMessage.error('报告列表加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="app-page">
    <section class="content report-history">
      <header class="section-header">
        <div>
          <span class="eyebrow">训练报告</span>
          <h1>训练报告</h1>
          <p>复盘每次岗位定制训练，持续观察能力变化与下一步提升方向。</p>
        </div>
        <el-button :icon="RefreshRight" :loading="loading" @click="load">刷新数据</el-button>
      </header>

      <el-empty v-if="!loading && reports.length === 0" description="暂无训练报告" />

      <div v-else v-loading="loading" class="report-list">
        <button v-for="report in reports" :key="report.id" type="button" class="report-item" :aria-label="`查看${report.target_position}${difficultyLabel(report.difficulty)}难度训练报告，综合得分 ${report.total_score}`" @click="router.push(`/reports/${report.id}`)">
          <div class="report-main">
            <span>{{ report.target_position }}</span>
            <small>{{ difficultyLabel(report.difficulty) }}难度 · 训练编号 #{{ report.session_id }}</small>
          </div>
          <div class="report-item-meta">
            <small>综合得分</small>
            <div class="score-badge">{{ report.total_score }}</div>
          </div>
        </button>
      </div>
    </section>
  </main>
</template>
