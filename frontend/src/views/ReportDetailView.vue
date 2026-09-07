<script setup lang="ts">
import { ElButton, ElEmpty, ElMessage, ElTag, vLoading } from 'element-plus'
import 'element-plus/theme-chalk/el-button.css'
import 'element-plus/theme-chalk/el-empty.css'
import 'element-plus/theme-chalk/el-loading.css'
import 'element-plus/theme-chalk/el-message.css'
import 'element-plus/theme-chalk/el-tag.css'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { trackAnalyticsEvent } from '../api/analytics'
import { getApiErrorMessage } from '../api/client'
import { fetchInterviewScores, type InterviewScore } from '../api/interview'
import {
  createPracticeFromQuestionReview,
  createPracticeFromReport,
  type QuestionPracticeMode
} from '../api/practice'
import {
  fetchInterviewQuestionReviews,
  fetchInterviewReport,
  fetchReport,
  fetchReportQuestionReviews,
  type InterviewReport,
  type QuestionReview
} from '../api/report'

const route = useRoute()
const router = useRouter()
const report = ref<InterviewReport | null>(null)
const questionReviews = ref<QuestionReview[]>([])
const loading = ref(false)
const reviewFallback = ref(false)
const reviewUnavailable = ref(false)
const practiceLoadingKey = ref('')

const reportId = computed(() => Number(route.params.id))
const sessionId = computed(() => Number(route.params.id))
const isSessionReport = computed(() => route.name === 'interview-report')

function formatScore(value: number) {
  return Number.isFinite(value) ? value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '') : '-'
}

function purposeLabel(purpose: string | null) {
  const labels: Record<string, string> = {
    answer: '回答评分',
    scoring: '评分',
    report: '报告',
  }
  return labels[purpose || ''] || purpose || '知识检索'
}

function uniqueText(items: string[]) {
  return [...new Set(items.map((item) => item.trim()).filter(Boolean))]
}

function toFallbackReviews(scores: InterviewScore[]) {
  const latestByQuestion = new Map<number, InterviewScore>()
  for (const score of scores) {
    const previous = latestByQuestion.get(score.question_index)
    if (!previous || score.id > previous.id) latestByQuestion.set(score.question_index, score)
  }
  return [...latestByQuestion.values()]
    .map<QuestionReview>((score) => ({
      id: score.id,
      session_id: score.session_id,
      score_id: score.id,
      question_index: score.question_index,
      dimension: score.dimension,
      question: score.question,
      answer: score.answer,
      score: score.score,
      sub_scores: score.sub_scores,
      deduction_reasons: uniqueText([score.reason, ...score.weaknesses]),
      suggested_structure: uniqueText(score.suggestions),
      sample_answer: '',
      weaknesses: uniqueText(score.weaknesses),
      weakness_key: `question_${score.question_index}`,
      created_at: score.created_at
    }))
    .sort((left, right) => left.question_index - right.question_index)
}

function reviewWeakness(review: QuestionReview) {
  return review.weaknesses[0] || review.deduction_reasons[0] || `${review.dimension || '本题能力'}待提升`
}

function reviewWeaknessKey(review: QuestionReview) {
  return review.weakness_key || review.practice_seed?.weakness_key || `question_${review.question_index}`
}

function reportWeaknessKey(index: number) {
  return `report_weakness_${index + 1}`
}

function practiceKey(scope: string, index: number) {
  return `${scope}:${index}`
}

function trackReviewExpanded(event: Event, review: QuestionReview) {
  const currentReport = report.value
  if (!(event.currentTarget instanceof HTMLDetailsElement) || !event.currentTarget.open) return
  if (!currentReport?.is_final || currentReport.id == null) return
  void trackAnalyticsEvent({
    event_name: 'question_review_expanded',
    report_id: currentReport.id,
    question_review_id: review.id
  }).catch(() => undefined)
}

async function startQuestionPractice(review: QuestionReview, mode: QuestionPracticeMode) {
  const currentReport = report.value
  if (!currentReport?.is_final || currentReport.id == null) return
  const loadingKey = practiceKey(mode, review.question_index)
  practiceLoadingKey.value = loadingKey
  try {
    const { data } = await createPracticeFromQuestionReview({
      report_id: currentReport.id,
      question_review_id: review.id,
      score_id: review.score_id,
      question_index: review.question_index,
      weakness_key: reviewWeaknessKey(review),
      weakness_title: reviewWeakness(review),
      practice_mode: mode
    })
    ElMessage.success(mode === 'repeat_question' ? '已创建本题重练' : '已创建同类题专项练习')
    if (data.practice_session_id) await router.push(`/interviews/${data.practice_session_id}`)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '专项练习创建失败'))
  } finally {
    practiceLoadingKey.value = ''
  }
}

async function startWeaknessPractice(weakness: string, index: number) {
  const currentReport = report.value
  if (!currentReport?.is_final || currentReport.id == null) return
  const loadingKey = practiceKey('weakness', index)
  practiceLoadingKey.value = loadingKey
  try {
    const { data } = await createPracticeFromReport({
      report_id: currentReport.id,
      weakness_key: reportWeaknessKey(index),
      weakness_title: weakness
    })
    ElMessage.success('已根据该短板创建专项练习')
    if (data.practice_session_id) await router.push(`/interviews/${data.practice_session_id}`)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '专项练习创建失败'))
  } finally {
    practiceLoadingKey.value = ''
  }
}

async function loadQuestionReviews(currentReport: InterviewReport) {
  reviewFallback.value = false
  reviewUnavailable.value = false
  try {
    const response = isSessionReport.value
      ? await fetchInterviewQuestionReviews(currentReport.session_id)
      : currentReport.id != null
        ? await fetchReportQuestionReviews(currentReport.id)
        : null
    if (response == null) throw new Error('Preview has no persisted question reviews')
    const { data } = response
    questionReviews.value = data
  } catch {
    try {
      const { data } = await fetchInterviewScores(currentReport.session_id)
      questionReviews.value = toFallbackReviews(data)
      reviewFallback.value = questionReviews.value.length > 0
      reviewUnavailable.value = questionReviews.value.length === 0
    } catch {
      questionReviews.value = []
      reviewUnavailable.value = true
    }
  }
}

async function load() {
  loading.value = true
  try {
    const { data } = isSessionReport.value
      ? await fetchInterviewReport(sessionId.value)
      : await fetchReport(reportId.value)
    report.value = data
    await loadQuestionReviews(data)
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '报告加载失败'))
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="app-page">
    <section class="content report-detail" v-loading="loading">
      <el-empty v-if="!loading && !report" description="报告不存在" />

      <template v-if="report">
        <header class="report-hero">
          <div>
            <span class="eyebrow">面试复盘</span>
            <h1>{{ report.is_final ? '面试复盘报告' : '面试报告预览' }}</h1>
            <p v-if="report.is_final">训练编号 #{{ report.session_id }} · 基于全部 {{ report.generated_from_score_count }} 条评分生成</p>
            <p v-else>训练编号 #{{ report.session_id }} · 基于当前 {{ report.generated_from_score_count }} 条评分的临时预览，结束面试后才会保存最终报告</p>
          </div>
          <div class="total-score">
            <span>{{ report.total_score }}</span>
            <small>综合得分</small>
          </div>
        </header>

        <section class="report-section">
          <div class="report-section-title"><span>01</span><h2>总体评价</h2></div>
          <p class="report-summary">{{ report.summary || '暂无总体评价' }}</p>
        </section>

        <section class="report-section">
          <div class="report-section-title"><span>02</span><h2>维度评分</h2></div>
          <el-empty v-if="report.dimension_scores.length === 0" description="暂无维度评分" />
          <div v-else class="dimension-score-list">
            <div v-for="item in report.dimension_scores" :key="item.dimension" class="dimension-score-item">
              <div class="dimension-score-head">
                <div>
                  <strong>{{ item.dimension }}</strong>
                  <small>题目 {{ item.question_indexes.join('、') }}</small>
                </div>
                <span>{{ item.score }}</span>
              </div>
              <div class="score-bar" :aria-label="`${item.dimension} ${item.score} 分`"><div :style="{ width: `${item.score}%` }"></div></div>
              <p>{{ item.focus }}</p>
            </div>
          </div>
        </section>

        <section class="report-section question-review-section">
          <div class="report-section-title"><span>03</span><h2>逐题复盘</h2></div>
          <p v-if="reviewFallback" class="review-notice">逐题复盘服务尚未返回完整内容，当前先展示可追溯的评分记录。</p>
          <el-empty
            v-if="questionReviews.length === 0"
            :description="reviewUnavailable ? '暂时无法加载逐题复盘' : '暂无逐题复盘'"
          />
          <div v-else class="question-review-list">
            <details
              v-for="review in questionReviews"
              :key="`${review.question_index}-${review.id}`"
              class="question-review-card"
              @toggle="trackReviewExpanded($event, review)"
            >
              <summary>
                <span class="question-review-index">第 {{ review.question_index }} 题</span>
                <span class="question-review-summary">
                  <strong>{{ review.question || '题目内容待补充' }}</strong>
                  <small>{{ review.dimension || '综合能力' }}</small>
                </span>
                <span class="question-review-score">{{ review.score }} 分</span>
                <span class="question-review-chevron" aria-hidden="true"></span>
              </summary>

              <div class="question-review-content">
                <div class="review-copy-block">
                  <h3>原回答</h3>
                  <p>{{ review.answer || '本题未记录有效回答。' }}</p>
                </div>

                <div class="question-review-grid">
                  <div class="review-copy-block review-deduction">
                    <h3>扣分原因</h3>
                    <ul v-if="review.deduction_reasons.length">
                      <li v-for="item in review.deduction_reasons" :key="item">{{ item }}</li>
                    </ul>
                    <p v-else>本题暂无明确扣分记录。</p>
                  </div>
                  <div class="review-copy-block review-structure">
                    <h3>建议结构</h3>
                    <ol v-if="review.suggested_structure.length">
                      <li v-for="item in review.suggested_structure" :key="item">{{ item }}</li>
                    </ol>
                    <p v-else>建议按“背景与目标 → 关键行动与取舍 → 结果与复盘”组织真实经历。</p>
                  </div>
                </div>

                <div class="review-copy-block review-sample">
                  <h3>示范回答</h3>
                  <p v-if="review.sample_answer">{{ review.sample_answer }}</p>
                  <p v-else class="review-placeholder">逐题示范回答待复盘服务生成；请不要直接套用报告级示例，以免引入未经你确认的经历或数据。</p>
                </div>

                <div v-if="report.is_final" class="question-practice-actions">
                  <el-button
                    :loading="practiceLoadingKey === practiceKey('repeat_question', review.question_index)"
                    :disabled="Boolean(practiceLoadingKey)"
                    @click="startQuestionPractice(review, 'repeat_question')"
                  >重练这题</el-button>
                  <el-button
                    type="primary"
                    :loading="practiceLoadingKey === practiceKey('similar_question', review.question_index)"
                    :disabled="Boolean(practiceLoadingKey)"
                    @click="startQuestionPractice(review, 'similar_question')"
                  >练同类题</el-button>
                </div>
              </div>
            </details>
          </div>
        </section>

        <section class="report-grid">
          <div class="report-section">
            <div class="report-section-title success"><span>04</span><h2>核心优势</h2></div>
            <el-empty v-if="report.strengths.length === 0" description="暂无内容" />
            <ul v-else class="report-list-text"><li v-for="item in report.strengths" :key="item">{{ item }}</li></ul>
          </div>
          <div id="weaknesses" class="report-section">
            <div class="report-section-title warning"><span>05</span><h2>待提升项</h2></div>
            <el-empty v-if="report.weaknesses.length === 0" description="暂无内容" />
            <ul v-else class="report-list-text weakness-practice-list">
              <li v-for="(item, index) in report.weaknesses" :key="item">
                <span>{{ item }}</span>
                <el-button
                  v-if="report.is_final"
                  size="small"
                  type="primary"
                  plain
                  :loading="practiceLoadingKey === practiceKey('weakness', index)"
                  :disabled="Boolean(practiceLoadingKey)"
                  @click="startWeaknessPractice(item, index)"
                >专项练习</el-button>
              </li>
            </ul>
          </div>
        </section>

        <section class="report-grid">
          <div class="report-section">
            <div class="report-section-title"><span>06</span><h2>优化建议</h2></div>
            <el-empty v-if="report.suggestions.length === 0" description="暂无内容" />
            <ul v-else class="report-list-text"><li v-for="item in report.suggestions" :key="item">{{ item }}</li></ul>
          </div>
          <div class="report-section">
            <div class="report-section-title"><span>07</span><h2>学习路线</h2></div>
            <el-empty v-if="report.learning_path.length === 0" description="暂无内容" />
            <ul v-else class="report-list-text"><li v-for="item in report.learning_path" :key="item">{{ item }}</li></ul>
          </div>
        </section>

        <section class="report-section">
          <div class="report-section-title"><span>08</span><h2>报告示例回答</h2></div>
          <p class="sample-answer">{{ report.sample_answer || '暂无示范回答' }}</p>
        </section>

        <section class="report-section">
          <div class="report-section-title"><span>09</span><h2>知识来源</h2></div>
          <el-empty v-if="report.citations.length === 0" description="该报告生成时未记录知识来源" />
          <div v-else class="citation-list">
            <article
              v-for="citation in report.citations"
              :key="`${citation.purpose}-${citation.question_index}-${citation.document_id}-${citation.index_version}-${citation.chunk_id}-${citation.reference}`"
              class="citation-item"
            >
              <div class="citation-head">
                <div>
                  <strong>{{ citation.title }}</strong>
                  <small>
                    文档 #{{ citation.document_id }} · 版本 {{ citation.index_version }} · chunk {{ citation.chunk_id }}
                    <template v-if="citation.source_page"> · 第 {{ citation.source_page }} 页</template>
                  </small>
                </div>
                <div class="citation-tags">
                  <el-tag size="small" effect="plain">{{ purposeLabel(citation.purpose) }}</el-tag>
                  <el-tag v-if="citation.question_index" size="small" effect="plain">第 {{ citation.question_index }} 题</el-tag>
                  <el-tag v-if="citation.rerank_is_fallback" size="small" type="warning" effect="plain">精排降级</el-tag>
                </div>
              </div>
              <dl class="citation-metrics">
                <div><dt>检索分</dt><dd>{{ formatScore(citation.retrieval_score) }}</dd></div>
                <div><dt>RRF</dt><dd>{{ formatScore(citation.rrf_score) }}</dd></div>
                <div><dt>精排分</dt><dd>{{ formatScore(citation.rerank_score) }}</dd></div>
                <div><dt>召回路线</dt><dd>{{ citation.retrieval_routes.join(' + ') || '-' }}</dd></div>
                <div><dt>上下文 chunks</dt><dd>{{ citation.context_chunks.map(item => item.chunk_id).join(', ') }}</dd></div>
              </dl>
              <p v-if="citation.rerank_fallback_reason" class="citation-warning">{{ citation.rerank_fallback_reason }}</p>
            </article>
          </div>
        </section>
      </template>
    </section>
  </main>
</template>
