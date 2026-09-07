import { apiClient } from './client'

export interface QuestionReview {
  id: number
  report_id?: number
  session_id: number
  score_id: number
  question_index: number
  dimension: string
  question: string
  answer: string
  score: number
  sub_scores: Record<string, number>
  deduction_reasons: string[]
  suggested_structure: string[]
  sample_answer: string
  weaknesses: string[]
  weakness_key?: string | null
  practice_seed?: {
    weakness_key?: string | null
    [key: string]: unknown
  } | null
  created_at?: string
}

export interface InterviewReport {
  id: number | null
  session_id: number
  total_score: number
  summary: string
  strengths: string[]
  weaknesses: string[]
  suggestions: string[]
  dimension_scores: ReportDimensionScore[]
  learning_path: string[]
  sample_answer: string
  citations: KnowledgeCitation[]
  is_final: boolean
  generated_from_score_count: number
  created_at: string
  updated_at: string
}

export interface KnowledgeContextChunkCitation {
  chunk_id: string
  chunk_index: number
  source_page: number | null
  is_primary: boolean
  context_token_count: number
  context_truncated: boolean
}

export interface KnowledgeCitation {
  reference: number
  query: string
  purpose: string | null
  question_index: number | null
  document_id: number
  title: string
  category: string
  target_position: string
  index_version: number
  chunk_id: string
  chunk_index: number
  source_page: number | null
  section_title: string | null
  retrieval_score: number
  retrieval_routes: string[]
  retrieval_scores: Record<string, number>
  retrieval_ranks: Record<string, number>
  rrf_score: number
  rerank_score: number
  rerank_provider: string | null
  rerank_model: string | null
  rerank_rank: number | null
  rerank_is_fallback: boolean
  rerank_fallback_reason: string | null
  context_chunks: KnowledgeContextChunkCitation[]
  context_token_count: number
  context_truncated: boolean
}

export interface ReportDimensionScore {
  dimension: string
  score: number
  question_indexes: number[]
  focus: string
  weaknesses: string[]
  suggestions: string[]
}

export interface InterviewReportListItem {
  id: number
  session_id: number
  target_position: string
  difficulty: string
  total_score: number
  created_at: string
  updated_at: string
}

export function fetchReports() {
  return apiClient.get<InterviewReportListItem[]>('/reports')
}

export function fetchReport(id: number) {
  return apiClient.get<InterviewReport>(`/reports/${id}`)
}

export function fetchInterviewReport(sessionId: number) {
  return apiClient.get<InterviewReport>(`/interviews/${sessionId}/report`)
}

export async function fetchReportQuestionReviews(reportId: number) {
  const response = await apiClient.get<QuestionReview[]>(`/reports/${reportId}/question-reviews`)
  return { ...response, data: normalizeQuestionReviews(response.data) }
}

export async function fetchInterviewQuestionReviews(sessionId: number) {
  const response = await apiClient.get<QuestionReview[]>(`/interviews/${sessionId}/question-reviews`)
  return { ...response, data: normalizeQuestionReviews(response.data) }
}

function normalizeQuestionReviews(items: QuestionReview[]) {
  return (Array.isArray(items) ? items : [])
    .map((item) => ({
      ...item,
      id: Number(item.id || item.score_id),
      session_id: Number(item.session_id),
      score_id: Number(item.score_id || item.id),
      question_index: Number(item.question_index),
      score: Number(item.score),
      sub_scores: item.sub_scores && typeof item.sub_scores === 'object' ? item.sub_scores : {},
      deduction_reasons: stringList(item.deduction_reasons),
      suggested_structure: stringList(item.suggested_structure),
      weaknesses: stringList(item.weaknesses),
      question: item.question || '',
      answer: item.answer || '',
      dimension: item.dimension || '',
      sample_answer: item.sample_answer || ''
    }))
    .filter((item) => Number.isFinite(item.question_index) && item.question_index > 0)
    .sort((left, right) => left.question_index - right.question_index)
}

function stringList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
    : []
}
