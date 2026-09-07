<script setup lang="ts">
import { ElButton, ElMessage, ElResult, ElSkeleton, ElTag } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-result.css'
import 'element-plus/theme-chalk/el-skeleton.css'
import 'element-plus/theme-chalk/el-tag.css'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getApiErrorMessage } from '../api/client'
import {
  createPracticeFromReport,
  fetchPracticeComparison,
  startPractice,
  startPracticeRetest,
  type PracticeComparison
} from '../api/practice'
import { rememberInterview } from '../api/interview'

const route = useRoute()
const router = useRouter()
const comparison = ref<PracticeComparison | null>(null)
const loading = ref(true)
const loadError = ref('')
const continuing = ref(false)
const openingPractice = ref(false)
const startingRetest = ref(false)

const practiceId = computed(() => Number(route.params.id))
const isPending = computed(() => !comparison.value?.after)
const comparisonState = computed(() => {
  const data = comparison.value
  if (!data) return { tag: '', title: '', description: '', type: 'info' as const }
  if (data.status === 'not_started') {
    return {
      tag: '未开始',
      title: '专项练习尚未开始',
      description: '先完成针对该短板的专项练习，之后才能进入同类能力点再测。',
      type: 'info' as const
    }
  }
  if (data.status === 'practicing') {
    return {
      tag: '训练中',
      title: '专项练习进行中',
      description: '继续完成当前专项练习；练习结束后，本页会开放再测入口。',
      type: 'warning' as const
    }
  }
  if (data.status === 'ready_for_retest') {
    return {
      tag: '待再测',
      title: '专项练习已完成',
      description: '开始同类能力点再测，完成后系统会在这里生成前后对比。',
      type: 'warning' as const
    }
  }
  if (!data.after) {
    return {
      tag: '结果异常',
      title: '再测结果暂不可用',
      description: '练习状态已完成，但没有读取到再测结果。请重新加载；若问题持续，请稍后重试。',
      type: 'danger' as const
    }
  }
  return {
    tag: '再测已完成',
    title: '',
    description: data.summary || '再测结果已生成，请结合下方明细继续巩固。',
    type: 'success' as const
  }
})
const scoreDelta = computed(() => comparison.value?.delta.score || 0)
const scoreDeltaLabel = computed(() => scoreDelta.value > 0 ? `+${scoreDelta.value}` : String(scoreDelta.value))
const structureRows = computed(() => {
  if (!comparison.value?.after) return []
  const before = comparison.value.before.answer_structure
  const after = comparison.value.after.answer_structure
  return Array.from({ length: Math.max(before.length, after.length) }, (_, index) => ({
    before: before[index] || '',
    after: after[index] || ''
  }))
})
const subScoreRows = computed(() => {
  if (!comparison.value?.after) return []
  const before = comparison.value.before.sub_scores
  const after = comparison.value.after.sub_scores
  return Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).map((dimension) => ({
    dimension,
    before: before[dimension] ?? null,
    after: after[dimension] ?? null,
    delta: before[dimension] == null || after[dimension] == null
      ? null
      : after[dimension] - before[dimension]
  }))
})

async function load() {
  if (!Number.isInteger(practiceId.value) || practiceId.value <= 0) {
    loadError.value = '练习编号无效'
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    comparison.value = (await fetchPracticeComparison(practiceId.value)).data
  } catch (error) {
    loadError.value = getApiErrorMessage(error, '加载前后对比失败')
  } finally {
    loading.value = false
  }
}

async function continueNextWeakness() {
  const data = comparison.value
  if (!data) return
  if (!data.next_weakness || !data.source_report_id) {
    await router.push(data.source_report_id ? `/reports/${data.source_report_id}#weaknesses` : '/reports')
    return
  }

  continuing.value = true
  try {
    const { data: practice } = await createPracticeFromReport({
      report_id: data.source_report_id,
      weakness_key: data.next_weakness.key,
      weakness_title: data.next_weakness.title
    })
    if (practice.practice_session_id) {
      await router.push(`/interviews/${practice.practice_session_id}`)
    } else {
      ElMessage.success('下一个短板练习已创建')
      await router.push(`/reports/${data.source_report_id}#weaknesses`)
    }
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '创建下一个短板练习失败'))
  } finally {
    continuing.value = false
  }
}

async function beginRetest() {
  if (!comparison.value || startingRetest.value) return
  startingRetest.value = true
  try {
    const { data } = await startPracticeRetest(practiceId.value)
    rememberInterview(data)
    await router.push({ path: `/interviews/${data.id}`, query: { practiceId: String(practiceId.value) } })
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '启动再测失败'))
  } finally {
    startingRetest.value = false
  }
}

async function beginOrContinuePractice() {
  if (!comparison.value || openingPractice.value) return
  openingPractice.value = true
  try {
    const { data } = await startPractice(practiceId.value)
    rememberInterview(data)
    await router.push(`/interviews/${data.id}`)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '打开专项练习失败'))
  } finally {
    openingPractice.value = false
  }
}

function sessionRoute(sessionId: number) {
  return `/interviews/${sessionId}`
}

onMounted(load)
</script>

<template>
  <main class="app-page comparison-page">
    <section class="content comparison-content">
      <header class="page-heading comparison-heading">
        <div>
          <span class="eyebrow">训练进度</span>
          <h1>训练前后对比</h1>
          <p>对照原始表现与再测结果，确认已经改善的部分和下一步训练重点。</p>
        </div>
        <el-button @click="router.push('/reports')">返回训练报告</el-button>
      </header>

      <section v-if="loading" class="comparison-loading" aria-live="polite">
        <el-skeleton :rows="8" animated />
      </section>

      <el-result
        v-else-if="loadError"
        icon="error"
        title="对比结果加载失败"
        :sub-title="loadError"
      >
        <template #extra>
          <el-button type="primary" @click="load">重新加载</el-button>
          <el-button @click="router.push('/reports')">返回报告</el-button>
        </template>
      </el-result>

      <template v-else-if="comparison">
        <section class="comparison-score-card" :class="{ pending: isPending }">
          <div class="comparison-score-copy">
            <el-tag :type="comparisonState.type" effect="light">
              {{ comparisonState.tag }}
            </el-tag>
            <h2>{{ comparison.weakness_title || '专项训练结果' }}</h2>
            <p>{{ comparisonState.description }}</p>
          </div>
          <div class="score-comparison" aria-label="训练前后得分">
            <button type="button" @click="router.push(sessionRoute(comparison.before.session_id))">
              <small>原始得分</small>
              <strong>{{ comparison.before.score }}</strong>
              <span>会话 #{{ comparison.before.session_id }}</span>
            </button>
            <span class="score-arrow" aria-hidden="true">→</span>
            <button v-if="comparison.after" type="button" @click="router.push(sessionRoute(comparison.after.session_id))">
              <small>再测得分</small>
              <strong>{{ comparison.after.score }}</strong>
              <span>会话 #{{ comparison.after.session_id }}</span>
            </button>
            <div v-else class="pending-score">
              <small>再测得分</small>
              <strong>--</strong>
              <span>完成后生成</span>
            </div>
            <div v-if="comparison.after" class="score-delta" :class="{ improved: scoreDelta > 0, declined: scoreDelta < 0 }">
              <small>分数变化</small>
              <strong>{{ scoreDeltaLabel }}</strong>
            </div>
          </div>
        </section>

        <el-result
          v-if="isPending"
          icon="info"
          :title="comparisonState.title"
          :sub-title="comparisonState.description"
          class="pending-retest-panel"
        >
          <template #extra>
            <el-button
              v-if="comparison.status === 'not_started' || comparison.status === 'practicing'"
              type="primary"
              :loading="openingPractice"
              @click="beginOrContinuePractice"
            >
              {{ comparison.status === 'not_started' ? '开始专项练习' : '继续专项练习' }}
            </el-button>
            <el-button
              v-else-if="comparison.status === 'ready_for_retest'"
              type="primary"
              :loading="startingRetest"
              @click="beginRetest"
            >
              开始再测
            </el-button>
            <el-button v-else type="primary" @click="load">重新加载</el-button>
            <el-button @click="router.push('/reports')">查看训练报告</el-button>
          </template>
        </el-result>

        <template v-else-if="comparison.after">
          <section class="comparison-section">
            <div class="report-section-title"><span>01</span><h2>短板变化</h2></div>
            <div class="weakness-comparison-grid">
              <article class="weakness-change-card resolved">
                <h3>已缓解短板</h3>
                <ul v-if="comparison.delta.resolved_weaknesses.length">
                  <li v-for="item in comparison.delta.resolved_weaknesses" :key="item">{{ item }}</li>
                </ul>
                <p v-else>暂未识别到明确缓解的短板，建议结合回答结构继续复盘。</p>
              </article>
              <article class="weakness-change-card remaining">
                <h3>仍需改进</h3>
                <ul v-if="comparison.delta.remaining_weaknesses.length">
                  <li v-for="item in comparison.delta.remaining_weaknesses" :key="item">{{ item }}</li>
                </ul>
                <p v-else>本轮目标短板已明显改善，可以进入下一个能力点。</p>
              </article>
            </div>
          </section>

          <section v-if="subScoreRows.length" class="comparison-section">
            <div class="report-section-title"><span>02</span><h2>子分变化</h2></div>
            <div class="sub-score-table" role="table" aria-label="训练前后子分变化">
              <div class="sub-score-row sub-score-head" role="row">
                <span role="columnheader">评分维度</span><span role="columnheader">原始</span><span role="columnheader">再测</span><span role="columnheader">变化</span>
              </div>
              <div v-for="item in subScoreRows" :key="item.dimension" class="sub-score-row" role="row">
                <strong role="cell">{{ item.dimension }}</strong>
                <span role="cell">{{ item.before ?? '--' }}</span>
                <span role="cell">{{ item.after ?? '--' }}</span>
                <b role="cell" :class="{ positive: item.delta != null && item.delta > 0, negative: item.delta != null && item.delta < 0 }">
                  {{ item.delta == null ? '--' : item.delta > 0 ? `+${item.delta}` : item.delta }}
                </b>
              </div>
            </div>
          </section>

          <section class="comparison-section answer-comparison-section">
            <div class="report-section-title"><span>{{ subScoreRows.length ? '03' : '02' }}</span><h2>回答结构对比</h2></div>
            <div class="answer-comparison-grid">
              <article>
                <header><span>训练前</span><button type="button" @click="router.push(sessionRoute(comparison.before.session_id))">会话 #{{ comparison.before.session_id }}</button></header>
                <p>{{ comparison.before.answer || '未记录可展示的原回答。' }}</p>
              </article>
              <article class="answer-after">
                <header><span>再测后</span><button type="button" @click="router.push(sessionRoute(comparison.after.session_id))">会话 #{{ comparison.after.session_id }}</button></header>
                <p>{{ comparison.after.answer || '未记录可展示的再测回答。' }}</p>
              </article>
            </div>

            <div v-if="structureRows.length" class="structure-comparison" role="table" aria-label="回答结构前后对比">
              <div class="structure-row structure-head" role="row">
                <span role="columnheader">原回答结构</span><span role="columnheader">再测回答结构</span>
              </div>
              <div v-for="(item, index) in structureRows" :key="index" class="structure-row" role="row">
                <p role="cell"><b>{{ index + 1 }}</b><span>{{ item.before || '该步骤未体现' }}</span></p>
                <p role="cell"><b>{{ index + 1 }}</b><span>{{ item.after || '该步骤未体现' }}</span></p>
              </div>
            </div>
            <p v-else class="comparison-empty-hint">本次结果暂未返回结构化步骤，请先结合两版回答正文进行对照。</p>
          </section>

          <footer class="comparison-actions">
            <div>
              <strong>下一步：{{ comparison.next_weakness?.title || '继续处理报告中的下一个短板' }}</strong>
              <span>保持一次只练一个能力点，更容易观察真实提升。</span>
            </div>
            <el-button type="primary" size="large" :loading="continuing" @click="continueNextWeakness">继续练下一个短板</el-button>
          </footer>
        </template>
      </template>
    </section>
  </main>
</template>
