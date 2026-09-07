import { apiClient } from './client'
import { normalizeInterviewSession, type InterviewSession } from './interview'

export type QuestionPracticeMode = 'repeat_question' | 'similar_question'
export type PracticeStatus = 'not_started' | 'practicing' | 'ready_for_retest' | 'completed'

export interface PracticeCreationResponse {
  id: number
  status: PracticeStatus
  practice_session_id?: number | null
  retest_session_id?: number | null
}

export interface QuestionPracticeInput {
  report_id: number
  question_review_id: number
  score_id: number
  question_index: number
  weakness_key: string
  weakness_title: string
  practice_mode: QuestionPracticeMode
}

export interface ReportPracticeInput {
  report_id: number
  weakness_key: string
  weakness_title: string
}

export interface PracticeListItem extends PracticeCreationResponse {
  source_report_id: number
  source_session_id: number
  source_question_review_id?: number | null
  weakness_key: string
  weakness_title: string
  target_dimension?: string | null
  before_score?: number | null
  after_score?: number | null
  created_at?: string
  updated_at?: string
}

export interface PracticeComparisonSession {
  session_id: number
  score: number
  weaknesses: string[]
  answer: string
  answer_structure: string[]
  sub_scores: Record<string, number>
}

export interface PracticeComparisonDelta {
  score: number
  resolved_weaknesses: string[]
  remaining_weaknesses: string[]
  sub_scores: Record<string, number>
}

export interface PracticeNextWeakness {
  key: string
  title: string
}

export interface PracticeComparison {
  practice_id: number
  status: PracticeStatus
  source_report_id?: number | null
  practice_session_id?: number | null
  retest_session_id?: number | null
  weakness_title?: string | null
  before: PracticeComparisonSession
  after: PracticeComparisonSession | null
  delta: PracticeComparisonDelta
  summary: string
  next_weakness?: PracticeNextWeakness | null
}

export function createPracticeFromQuestionReview(data: QuestionPracticeInput) {
  return apiClient.post<PracticeCreationResponse>('/practice/from-question-review', data)
}

export function createPracticeFromReport(data: ReportPracticeInput) {
  return apiClient.post<PracticeCreationResponse>('/practice/from-report', data)
}

export function fetchPractices() {
  return apiClient.get<PracticeListItem[]>('/practice')
}

export async function startPractice(id: number) {
  const response = await apiClient.post<InterviewSession>(`/practice/${id}/start`)
  return { ...response, data: normalizeInterviewSession(response.data) }
}

export async function startPracticeRetest(id: number) {
  const response = await apiClient.post<InterviewSession>(`/practice/${id}/start-retest`)
  return { ...response, data: normalizeInterviewSession(response.data) }
}

export async function fetchPracticeComparison(id: number) {
  const response = await apiClient.get<PracticeComparison>(`/practice/${id}/comparison`)
  return { ...response, data: normalizePracticeComparison(response.data, id) }
}

function normalizePracticeComparison(data: PracticeComparison, practiceId: number): PracticeComparison {
  const before = normalizeComparisonSession(data.before)
  const after = data.after ? normalizeComparisonSession(data.after) : null
  const fallbackDelta = after ? after.score - before.score : 0
  return {
    ...data,
    practice_id: Number(data.practice_id || practiceId),
    status: after ? 'completed' : data.status || 'ready_for_retest',
    before,
    after,
    delta: {
      score: finiteNumber(data.delta?.score, fallbackDelta),
      resolved_weaknesses: stringList(data.delta?.resolved_weaknesses),
      remaining_weaknesses: stringList(data.delta?.remaining_weaknesses),
      sub_scores: numberRecord(data.delta?.sub_scores)
    },
    summary: data.summary || ''
  }
}

function normalizeComparisonSession(data: PracticeComparisonSession): PracticeComparisonSession {
  return {
    ...data,
    session_id: Number(data.session_id),
    score: finiteNumber(data.score),
    weaknesses: stringList(data.weaknesses),
    answer: data.answer || '',
    answer_structure: stringList(data.answer_structure),
    sub_scores: numberRecord(data.sub_scores)
  }
}

function stringList(value: unknown) {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
    : []
}

function numberRecord(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, score]) => [key, Number(score)] as const)
      .filter(([, score]) => Number.isFinite(score))
  )
}

function finiteNumber(value: unknown, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}
