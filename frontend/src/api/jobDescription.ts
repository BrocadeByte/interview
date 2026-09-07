import { apiClient } from './client'

export interface ParsedJobDescription {
  target_position?: string
  seniority?: string
  must_have_skills?: string[]
  nice_to_have_skills?: string[]
  responsibilities?: string[]
  interview_focus?: string[]
  risk_points?: string[]
  [key: string]: unknown
}

export interface JobDescription {
  id: number
  title: string
  company_name?: string | null
  raw_text?: string
  target_position?: string
  parsed?: ParsedJobDescription
  parsed_json?: ParsedJobDescription
  is_active?: boolean
  created_at?: string
  updated_at?: string
}

export interface ParseJobDescriptionInput {
  raw_text: string
  title: string
  company_name?: string
}

export function parseJobDescription(data: ParseJobDescriptionInput) {
  return apiClient.post<JobDescription>('/job-descriptions/parse', data)
}

export function fetchJobDescriptions() {
  return apiClient.get<JobDescription[]>('/job-descriptions')
}

export function fetchJobDescription(id: number) {
  return apiClient.get<JobDescription>(`/job-descriptions/${id}`)
}

export function activateJobDescription(id: number) {
  return apiClient.post<JobDescription>(`/job-descriptions/${id}/activate`)
}
