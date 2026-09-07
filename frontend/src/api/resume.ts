import { apiClient } from './client'
import type { Profile } from './profile'

export type ResumeStatus = 'pending' | 'parsed' | 'failed'

export interface ParsedResume {
  projects?: unknown[]
  skills?: string[]
  experience_summary?: string
  [key: string]: unknown
}

export interface ResumeVersion {
  id: number
  source_type: 'upload' | 'paste'
  file_name?: string | null
  file_type?: string | null
  raw_text?: string
  status: ResumeStatus
  error_message?: string | null
  is_active?: boolean
  profile_patch?: Partial<Profile>
  parsed?: ParsedResume
  created_at?: string
  updated_at?: string
}

export interface PasteResumeInput {
  title: string
  content: string
}

export class ResumeParsingFailedError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResumeParsingFailedError'
  }
}

export class ResumeParsingTimeoutError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ResumeParsingTimeoutError'
  }
}

export function pasteResume(data: PasteResumeInput) {
  return apiClient.post<ResumeVersion>('/resumes/paste', data, { timeout: 15_000 })
}

export function uploadResume(file: File, title = file.name) {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('title', title)
  return apiClient.post<ResumeVersion>('/resumes/upload', formData, { timeout: 30_000 })
}

export function fetchResumes() {
  return apiClient.get<ResumeVersion[]>('/resumes')
}

export function fetchResume(id: number, timeoutMs = 10_000) {
  return apiClient.get<ResumeVersion>(`/resumes/${id}`, { timeout: timeoutMs })
}

export async function waitForResumeParsing(id: number, timeoutMs = 135_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    await delay(Math.min(800, Math.max(0, deadline - Date.now())))
    const remainingMs = deadline - Date.now()
    if (remainingMs <= 0) break
    const { data } = await fetchResume(id, Math.min(10_000, remainingMs))
    if (data.status === 'pending') continue
    if (data.status === 'failed') {
      throw new ResumeParsingFailedError(data.error_message || 'AI 简历解析失败，请检查简历内容后重试')
    }
    return data
  }
  throw new ResumeParsingTimeoutError('AI 简历解析超时，任务仍未完成，请稍后重试')
}

export function activateResume(id: number) {
  return apiClient.post<ResumeVersion>(`/resumes/${id}/activate`)
}

export function applyResumeToProfile(id: number) {
  return apiClient.post<Profile>(`/resumes/${id}/apply-profile`)
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds))
}
