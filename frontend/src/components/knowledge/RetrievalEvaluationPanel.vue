<script setup lang="ts">
import {
  ElAlert,
  ElButton,
  ElCheckbox,
  ElCollapse,
  ElCollapseItem,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElSelect,
  ElTag
} from 'element-plus'
import 'element-plus/theme-chalk/el-alert.css'
import 'element-plus/theme-chalk/el-checkbox.css'
import 'element-plus/theme-chalk/el-collapse.css'
import 'element-plus/theme-chalk/el-empty.css'
import 'element-plus/theme-chalk/el-form.css'
import 'element-plus/theme-chalk/el-input-number.css'
import { computed, ref } from 'vue'

import { getApiErrorMessage } from '../../api/client'
import {
  evaluateKnowledgeRetrieval,
  type KnowledgeDocument,
  type RetrievalEvaluationCasePayload,
  type RetrievalEvaluationResult,
  type RetrievalMetricScores
} from '../../api/knowledge'

interface EvaluationDraft extends RetrievalEvaluationCasePayload {
  uid: number
}

const props = defineProps<{ documents: KnowledgeDocument[] }>()

let nextUid = 1
const running = ref(false)
const topK = ref(5)
const result = ref<RetrievalEvaluationResult | null>(null)
const cases = ref<EvaluationDraft[]>([newDraft()])

const categoryOptions = computed(() => Array.from(new Set(props.documents.map((item) => item.category))).sort())
const metricDefinitions: Array<{ key: keyof RetrievalMetricScores; label: string; help: string }> = [
  { key: 'context_precision', label: '语义准确率', help: '高排名内容中，对标准答案真正有用的内容是否靠前' },
  { key: 'context_recall', label: '语义召回率', help: '标准答案中的信息是否能被检索上下文支撑' },
  { key: 'id_context_precision', label: '文档准确率', help: '检索到的文档中，人工标注相关文档的占比' },
  { key: 'id_context_recall', label: '文档召回率', help: '人工标注的相关文档中，被检索到的占比' },
  { key: 'hit_rate', label: 'Hit@K', help: 'Top K 中是否至少命中一篇相关文档' },
  { key: 'mrr', label: 'MRR', help: '第一篇相关文档越靠前，得分越高' }
]
const availableSummaryMetrics = computed(() => metricDefinitions.filter((metric) => result.value?.metrics[metric.key] != null))
const failedMetricCount = computed(() => result.value?.cases.reduce((total, item) => total + Object.keys(item.metric_errors).length, 0) || 0)

function newDraft(): EvaluationDraft {
  return {
    uid: nextUid++,
    case_id: '',
    query: '',
    reference: '',
    reference_document_ids: [],
    target_position: 'general',
    categories: [],
    include_general: true
  }
}

function addCase() {
  if (cases.value.length >= 20) return ElMessage.warning('一次最多评测 20 条样本')
  cases.value.push(newDraft())
}

function removeCase(uid: number) {
  if (cases.value.length === 1) return ElMessage.warning('至少保留一条评测样本')
  cases.value = cases.value.filter((item) => item.uid !== uid)
}

async function runEvaluation() {
  const invalid = cases.value.find((item) => !item.query.trim() || (!item.reference?.trim() && !item.reference_document_ids.length))
  if (invalid) {
    ElMessage.warning('每条样本需填写问题，并至少提供标准答案或相关文档')
    return
  }

  running.value = true
  try {
    const payload = cases.value.map(({ uid, ...item }, index) => ({
      ...item,
      case_id: item.case_id?.trim() || `case-${index + 1}`,
      query: item.query.trim(),
      reference: item.reference?.trim() || undefined,
      target_position: item.target_position?.trim() || undefined
    }))
    result.value = (await evaluateKnowledgeRetrieval({ cases: payload, top_k: topK.value })).data
    ElMessage.success('检索评测已完成')
  } catch (error) {
    ElMessage.error(getApiErrorMessage(error, '检索评测失败'))
  } finally {
    running.value = false
  }
}

function formatScore(score: number | null) {
  return score == null ? '—' : `${Math.round(score * 100)}%`
}

function scoreLevel(score: number | null) {
  if (score == null) return 'unknown'
  if (score >= 0.8) return 'good'
  if (score >= 0.6) return 'fair'
  return 'poor'
}

function scoreLabel(score: number | null) {
  return ({ good: '良好', fair: '可用', poor: '待优化', unknown: '未评测' } as const)[scoreLevel(score)]
}

function documentLabel(id: number) {
  const document = props.documents.find((item) => item.id === id)
  return document ? `#${id} ${document.title}` : `#${id}`
}

function metricErrorText(errors: Record<string, string>) {
  return Object.entries(errors).map(([name, message]) => `${name}: ${message}`).join('；')
}
</script>

<template>
  <div class="retrieval-evaluation-panel">
    <el-alert type="info" :closable="false" show-icon>
      <template #title>Ragas 检索质量评测</template>
      标准答案会启用 LLM 语义准确率和召回率；相关文档会启用可重复的 ID 指标。建议两种标注都填，结果更完整。
    </el-alert>

    <div class="evaluation-toolbar">
      <div>
        <strong>评测集</strong>
        <small>调用当前线上的混合检索与重排链路</small>
      </div>
      <label class="top-k-control">Top K <el-input-number v-model="topK" :min="1" :max="20" controls-position="right" /></label>
      <el-button @click="addCase">添加样本</el-button>
      <el-button type="primary" :loading="running" @click="runEvaluation">运行评测</el-button>
    </div>

    <div class="evaluation-cases">
      <article v-for="(item, index) in cases" :key="item.uid" class="evaluation-case-editor">
        <div class="evaluation-case-heading">
          <strong>样本 {{ index + 1 }}</strong>
          <el-button text type="danger" :disabled="cases.length === 1" @click="removeCase(item.uid)">删除</el-button>
        </div>
        <el-form label-position="top">
          <div class="evaluation-form-grid">
            <el-form-item label="检索问题" class="span-2">
              <el-input v-model="item.query" type="textarea" :rows="2" placeholder="例如：Java 中 HashMap 的扩容机制是什么？" />
            </el-form-item>
            <el-form-item label="标准答案（语义评测）" class="span-2">
              <el-input v-model="item.reference" type="textarea" :rows="3" placeholder="填写完整、可验证的标准答案" />
            </el-form-item>
            <el-form-item label="相关文档（ID 评测）" class="span-2">
              <el-select v-model="item.reference_document_ids" multiple filterable collapse-tags placeholder="选择应该被检索到的文档">
                <el-option v-for="document in documents" :key="document.id" :label="`#${document.id} ${document.title}`" :value="document.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="适用岗位">
              <el-input v-model="item.target_position" placeholder="general" />
            </el-form-item>
            <el-form-item label="知识分类">
              <el-select v-model="item.categories" multiple clearable collapse-tags placeholder="不选表示全部分类">
                <el-option v-for="category in categoryOptions" :key="category" :label="category" :value="category" />
              </el-select>
            </el-form-item>
          </div>
          <el-checkbox v-model="item.include_general">同时包含 general 通用知识</el-checkbox>
        </el-form>
      </article>
    </div>

    <section v-if="result" class="evaluation-results">
      <div class="evaluation-result-heading">
        <div>
          <span class="eyebrow">评测结果</span>
          <h2>{{ result.framework }} {{ result.framework_version }}</h2>
        </div>
        <small>{{ result.case_count }} 条样本 · Top {{ result.top_k }} · {{ (result.duration_ms / 1000).toFixed(1) }} 秒<span v-if="result.evaluator_model"> · {{ result.evaluator_model }}</span></small>
      </div>

      <div v-if="availableSummaryMetrics.length" class="evaluation-score-grid">
        <article v-for="metric in availableSummaryMetrics" :key="metric.key" class="evaluation-score-card" :class="scoreLevel(result.metrics[metric.key])">
          <span>{{ metric.label }}</span>
          <strong>{{ formatScore(result.metrics[metric.key]) }}</strong>
          <el-tag size="small" :type="scoreLevel(result.metrics[metric.key]) === 'good' ? 'success' : scoreLevel(result.metrics[metric.key]) === 'fair' ? 'warning' : 'danger'">{{ scoreLabel(result.metrics[metric.key]) }}</el-tag>
          <small>{{ metric.help }}</small>
        </article>
      </div>
      <el-alert v-if="failedMetricCount" type="warning" :closable="false" show-icon :title="`${failedMetricCount} 个指标计算失败，请展开样本查看原因`" />
      <p class="evaluation-score-note">分数区间仅用于快速提示，请以固定评测集在检索策略迭代前后的相对变化为准。</p>

      <el-collapse class="evaluation-case-results">
        <el-collapse-item v-for="(item, index) in result.cases" :key="item.case_id" :name="item.case_id">
          <template #title>
            <div class="case-result-title">
              <strong>{{ index + 1 }}. {{ item.query }}</strong>
              <span>{{ item.duration_ms }} ms</span>
            </div>
          </template>
          <el-alert v-if="Object.keys(item.metric_errors).length" type="warning" :closable="false" :title="metricErrorText(item.metric_errors)" />
          <div class="case-metric-list">
            <template v-for="metric in metricDefinitions" :key="metric.key">
              <span v-if="item.metrics[metric.key] != null"><b>{{ metric.label }}</b>{{ formatScore(item.metrics[metric.key]) }}</span>
            </template>
          </div>
          <p v-if="item.reference_document_ids.length" class="reference-document-list">标注文档：{{ item.reference_document_ids.map(documentLabel).join('、') }}</p>
          <el-empty v-if="!item.retrieved_contexts.length" description="未检索到上下文" />
          <div v-for="context in item.retrieved_contexts" :key="`${context.rank}-${context.chunk_id}`" class="retrieved-context-row" :class="{ relevant: context.relevant, irrelevant: context.relevant === false }">
            <div class="retrieved-context-meta">
              <strong>#{{ context.rank }} {{ context.title }}</strong>
              <span>文档 {{ context.document_id }} · chunk {{ context.chunk_index }} · 分数 {{ context.retrieval_score.toFixed(4) }}</span>
              <el-tag v-if="context.relevant != null" size="small" :type="context.relevant ? 'success' : 'info'">{{ context.relevant ? '命中标注' : '未标注' }}</el-tag>
            </div>
            <p>{{ context.content_preview }}</p>
            <small v-if="context.retrieval_routes.length">召回路径：{{ context.retrieval_routes.join(' + ') }}</small>
          </div>
        </el-collapse-item>
      </el-collapse>
    </section>
  </div>
</template>
