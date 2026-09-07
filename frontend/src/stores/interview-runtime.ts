import { reactive } from 'vue'
import { defineStore } from 'pinia'

import {
  streamAnswer,
  streamInterviewStart,
  type InterviewMessage,
  type InterviewSession
} from '../api/interview'
import { getApiErrorMessage } from '../api/client'
import { createRequestId } from '../utils/request-id'

interface RetryableAnswer {
  answer: string
  requestId: string
}

export interface InterviewRuntimeState {
  session: InterviewSession | null
  answer: string
  loading: boolean
  starting: boolean
  streamingAssistantMessage: InterviewMessage | null
  streamPhase: string
  streamTextDone: boolean
  pendingUserMessage: InterviewMessage | null
  retryableAnswer: RetryableAnswer | null
  error: string
  completionVersion: number
}

export interface InterviewRuntimeResult {
  data: InterviewSession | null
  error: string
}

const pendingAnswerPrefix = 'interview-pending-answer:'

function pendingAnswerKey(sessionId: number) {
  return `${pendingAnswerPrefix}${sessionId}`
}

function readPendingAnswer(sessionId: number): RetryableAnswer | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(pendingAnswerKey(sessionId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<RetryableAnswer>
    if (!parsed.answer?.trim() || !parsed.requestId) return null
    return { answer: parsed.answer, requestId: parsed.requestId }
  } catch {
    return null
  }
}

function writePendingAnswer(sessionId: number, pending: RetryableAnswer) {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.setItem(pendingAnswerKey(sessionId), JSON.stringify(pending))
  } catch {
    // The in-memory runtime still preserves application-internal navigation.
  }
}

function clearPendingAnswer(sessionId: number) {
  if (typeof window === 'undefined') return
  try {
    window.sessionStorage.removeItem(pendingAnswerKey(sessionId))
  } catch {
    // Storage can be unavailable in privacy mode.
  }
}

function runtimeError(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : getApiErrorMessage(error, fallback)
}

export const useInterviewRuntimeStore = defineStore('interview-runtime', () => {
  const sessions = reactive<Record<number, InterviewRuntimeState>>({})
  const startTasks = new Map<number, Promise<InterviewRuntimeResult>>()
  const answerTasks = new Map<number, Promise<InterviewRuntimeResult>>()

  function forSession(sessionId: number) {
    if (!sessions[sessionId]) {
      const retryableAnswer = readPendingAnswer(sessionId)
      sessions[sessionId] = {
        session: null,
        answer: retryableAnswer?.answer || '',
        loading: false,
        starting: false,
        streamingAssistantMessage: null,
        streamPhase: '',
        streamTextDone: false,
        pendingUserMessage: null,
        retryableAnswer,
        error: '',
        completionVersion: 0
      }
    }
    return sessions[sessionId]
  }

  function setSession(sessionId: number, session: InterviewSession) {
    const runtime = forSession(sessionId)
    runtime.session = session
    const retryable = runtime.retryableAnswer
    if (retryable && session.messages.some((message) => message.request_id === retryable.requestId)) {
      runtime.retryableAnswer = null
      if (runtime.answer === retryable.answer) runtime.answer = ''
      clearPendingAnswer(sessionId)
    }
  }

  function consumeError(sessionId: number) {
    const runtime = forSession(sessionId)
    const message = runtime.error
    runtime.error = ''
    return message
  }

  function startFirstQuestion(sessionId: number): Promise<InterviewRuntimeResult> {
    const existing = startTasks.get(sessionId)
    if (existing) return existing

    const task = runFirstQuestion(sessionId).finally(() => startTasks.delete(sessionId))
    startTasks.set(sessionId, task)
    return task
  }

  async function runFirstQuestion(sessionId: number): Promise<InterviewRuntimeResult> {
    const runtime = forSession(sessionId)
    runtime.starting = true
    runtime.streamTextDone = false
    runtime.error = ''
    runtime.streamingAssistantMessage = {
      id: -Date.now(),
      role: 'assistant',
      content: '',
      question_index: 1,
      is_followup: 0,
      followup_index: 0,
      created_at: new Date().toISOString()
    }
    let completed: InterviewSession | null = null

    try {
      await streamInterviewStart(sessionId, (event) => {
        if (event.event === 'status') {
          runtime.streamPhase = event.data.phase
        } else if (event.event === 'delta' && runtime.streamingAssistantMessage) {
          runtime.streamingAssistantMessage.content += event.data.content
        } else if (event.event === 'text_done') {
          if (runtime.streamingAssistantMessage) {
            runtime.streamingAssistantMessage.content = event.data.content
          }
          runtime.streamTextDone = true
        } else if (event.event === 'complete') {
          completed = event.data
          setSession(sessionId, event.data)
          runtime.streamingAssistantMessage = null
          runtime.completionVersion += 1
        }
      })
      if (!completed) throw new Error('首题生成完成事件缺失，请重试')
      return { data: completed, error: '' }
    } catch (error) {
      runtime.streamingAssistantMessage = null
      runtime.error = runtimeError(error, '生成第一道问题失败')
      return { data: null, error: runtime.error }
    } finally {
      runtime.starting = false
      runtime.streamPhase = ''
      runtime.streamTextDone = false
    }
  }

  function submitAnswer(sessionId: number): Promise<InterviewRuntimeResult> {
    const existing = answerTasks.get(sessionId)
    if (existing) return existing

    const runtime = forSession(sessionId)
    const submittedAnswer = runtime.answer.trim()
    if (!submittedAnswer || runtime.loading) {
      return Promise.resolve({ data: null, error: '' })
    }

    const task = runAnswer(sessionId, submittedAnswer).finally(() => answerTasks.delete(sessionId))
    answerTasks.set(sessionId, task)
    return task
  }

  async function runAnswer(
    sessionId: number,
    submittedAnswer: string
  ): Promise<InterviewRuntimeResult> {
    const runtime = forSession(sessionId)
    runtime.loading = true
    runtime.streamTextDone = false
    runtime.error = ''
    const requestId = runtime.retryableAnswer?.answer === submittedAnswer
      ? runtime.retryableAnswer.requestId
      : createRequestId()
    const pending = { answer: submittedAnswer, requestId }
    runtime.retryableAnswer = pending
    writePendingAnswer(sessionId, pending)

    runtime.pendingUserMessage = {
      id: -Date.now(),
      role: 'user',
      content: submittedAnswer,
      request_id: requestId,
      question_index: runtime.session?.current_question_index || 1,
      is_followup: latestAssistant(runtime.session)?.is_followup || 0,
      followup_index: latestAssistant(runtime.session)?.followup_index || 0,
      created_at: new Date().toISOString()
    }
    runtime.streamingAssistantMessage = {
      id: -(Date.now() + 1),
      role: 'assistant',
      content: '',
      question_index: runtime.session?.current_question_index || 1,
      is_followup: 0,
      followup_index: 0,
      created_at: new Date().toISOString()
    }
    runtime.answer = ''
    let completed: InterviewSession | null = null

    try {
      await streamAnswer(sessionId, submittedAnswer, requestId, (event) => {
        if (event.event === 'status') {
          runtime.streamPhase = event.data.phase
        } else if (event.event === 'delta' && runtime.streamingAssistantMessage) {
          runtime.streamingAssistantMessage.content += event.data.content
        } else if (event.event === 'text_done') {
          if (runtime.streamingAssistantMessage) {
            runtime.streamingAssistantMessage.content = event.data.content
          }
          runtime.streamTextDone = true
        } else if (event.event === 'complete') {
          completed = event.data
          setSession(sessionId, event.data)
          runtime.pendingUserMessage = null
          runtime.streamingAssistantMessage = null
          runtime.retryableAnswer = null
          clearPendingAnswer(sessionId)
          runtime.completionVersion += 1
        }
      })
      if (!completed) throw new Error('回答处理完成事件缺失，请重试')
      return { data: completed, error: '' }
    } catch (error) {
      runtime.answer = submittedAnswer
      runtime.pendingUserMessage = null
      runtime.streamingAssistantMessage = null
      runtime.error = runtimeError(error, '提交回答失败')
      return { data: null, error: runtime.error }
    } finally {
      runtime.loading = false
      runtime.streamPhase = ''
      runtime.streamTextDone = false
    }
  }

  return {
    sessions,
    forSession,
    setSession,
    consumeError,
    startFirstQuestion,
    submitAnswer
  }
})

function latestAssistant(session: InterviewSession | null) {
  const messages = session?.messages || []
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') return messages[index]
  }
  return null
}
