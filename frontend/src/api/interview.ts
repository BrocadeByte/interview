import { apiClient, authenticatedFetch } from './client'

export type InterviewMode = 'training' | 'mock'
export type InterviewType = 'hr' | 'project_deep_dive' | 'technical_basics' | 'system_design' | 'mixed'
export type InterviewPurpose = 'full_interview' | 'weakness_practice' | 'retest'

export interface InterviewMessage {
  id: number
  role: 'assistant' | 'user'
  content: string
  request_id?: string | null
  question_index: number
  is_followup: number
  followup_index: number
  created_at: string
}

export interface InterviewSession {
  id: number
  target_position: string
  difficulty: 'easy' | 'medium' | 'hard'
  status: 'preparing' | 'active' | 'finished'
  current_question_index: number
  current_dimension?: string | null
  current_plan_focus?: string | null
  total_question_count: number
  mode: InterviewMode
  interview_type: InterviewType
  session_purpose?: InterviewPurpose
  practice_id?: number | null
  source_practice_id?: number | null
  source_report_id?: number | null
  source_weakness_key?: string | null
  created_at: string
  updated_at: string
  messages: InterviewMessage[]
}

export interface InterviewScore {
  id: number
  session_id: number
  question_index: number
  question: string
  answer: string
  dimension: string
  score: number
  sub_scores: Record<string, number>
  reason: string
  weaknesses: string[]
  suggestions: string[]
  is_fallback: boolean
  fallback_reason?: string | null
  created_at: string
}

export interface InterviewCreateInput {
  target_position: string
  difficulty: 'easy' | 'medium' | 'hard'
  mode: InterviewMode
  interview_type: InterviewType
  resume_id?: number
  job_description_id?: number
}

const sessionCache = new Map<number, InterviewSession>()
const sessionPreferencePrefix = 'interview-session-preferences:'

type InterviewPreferences = Pick<InterviewSession, 'mode' | 'interview_type'>

function readInterviewPreferences(id: number): Partial<InterviewPreferences> {
  if (typeof window === 'undefined') return {}
  try {
    const value = window.localStorage.getItem(`${sessionPreferencePrefix}${id}`)
    return value ? JSON.parse(value) as Partial<InterviewPreferences> : {}
  } catch {
    return {}
  }
}

function writeInterviewPreferences(session: InterviewSession) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(`${sessionPreferencePrefix}${session.id}`, JSON.stringify({
      mode: session.mode,
      interview_type: session.interview_type
    }))
  } catch {
    // Storage can be unavailable in privacy mode; the in-memory cache still covers navigation.
  }
}

export function normalizeInterviewSession(
  session: Omit<InterviewSession, 'mode' | 'interview_type'> & Partial<InterviewPreferences>,
  fallback: Partial<InterviewPreferences> = {}
): InterviewSession {
  const stored = readInterviewPreferences(session.id)
  return {
    ...session,
    mode: session.mode || fallback.mode || stored.mode || 'training',
    interview_type: session.interview_type || fallback.interview_type || stored.interview_type || 'mixed'
  }
}

export function rememberInterview(session: InterviewSession) {
  sessionCache.set(session.id, session)
  writeInterviewPreferences(session)
}

export function takeRememberedInterview(id: number) {
  const session = sessionCache.get(id) || null
  sessionCache.delete(id)
  return session
}

export function warmupInterview(targetPosition: string) {
  return apiClient.post<void>('/interviews/warmup', { target_position: targetPosition })
}

export async function createInterview(data: InterviewCreateInput) {
  const response = await apiClient.post<InterviewSession>('/interviews', data)
  const session = normalizeInterviewSession(response.data, data)
  writeInterviewPreferences(session)
  return { ...response, data: session }
}

export async function fetchInterviews() {
  const response = await apiClient.get<InterviewSession[]>('/interviews')
  return { ...response, data: response.data.map((session) => normalizeInterviewSession(session)) }
}

export async function fetchInterview(id: number) {
  const response = await apiClient.get<InterviewSession>(`/interviews/${id}`)
  return { ...response, data: normalizeInterviewSession(response.data) }
}

export function fetchInterviewScores(id: number) {
  return apiClient.get<InterviewScore[]>(`/interviews/${id}/scores`)
}

export function startInterview(id: number) {
  return apiClient.post<InterviewSession>(`/interviews/${id}/start`)
}

export function answerInterview(id: number, answer: string, requestId: string) {
  return apiClient.post<InterviewSession>(`/interviews/${id}/answer`, {
    answer,
    request_id: requestId
  })
}

export function finishInterview(id: number) {
  return apiClient.post<InterviewSession>(`/interviews/${id}/finish`)
}

export type InterviewStreamEvent =
  | { event: 'status'; data: { phase: string } }
  | { event: 'delta'; data: { content: string } }
  | { event: 'text_done'; data: { content: string } }
  | { event: 'complete'; data: InterviewSession }
  | { event: 'error'; data: { message: string } }

export function streamInterviewStart(
  id: number,
  onEvent: (event: InterviewStreamEvent) => void,
  signal?: AbortSignal
) {
  return consumeInterviewStream(`/api/interviews/${id}/start/stream`, undefined, onEvent, signal)
}
export async function streamAnswer(
  id: number,
  answer: string,
  requestId: string,
  onEvent: (event: InterviewStreamEvent) => void,
  signal?: AbortSignal
) {
  return consumeInterviewStream(
    `/api/interviews/${id}/answer/stream`,
    { answer, request_id: requestId },
    onEvent,
    signal
  )
}

async function consumeInterviewStream(
  url: string,
  body: object | undefined,
  onEvent: (event: InterviewStreamEvent) => void,
  signal?: AbortSignal
) {
  // delta 只传输已提交的权威文本；text_done 和 complete 确认全文与最终会话快照。
  const response = await authenticatedFetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream'
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal
  })

  if (!response.ok) {
    let message = `Request failed with status ${response.status}`
    try {
      const payload = await response.json()
      if (typeof payload.detail === 'string') message = payload.detail
    } catch {
      // Keep the status-based message for non-JSON responses.
    }
    throw new Error(message)
  }
  if (!response.body) throw new Error('Streaming response body is unavailable')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const parsed = parseSseFrame(frame)
      if (parsed?.event === 'error') throw new Error(parsed.data.message)
      if (parsed) onEvent(parsed)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
}

function parseSseFrame(frame: string): InterviewStreamEvent | null {
  // 支持注释心跳与多行 data，避免网络分片边界影响上层业务事件。
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (dataLines.length === 0) return null
  const parsed = { event, data: JSON.parse(dataLines.join('\n')) } as InterviewStreamEvent
  if (parsed.event === 'complete') parsed.data = normalizeInterviewSession(parsed.data)
  return parsed
}
