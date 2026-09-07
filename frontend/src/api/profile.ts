import { apiClient } from './client'

export interface Profile {
  id?: number
  user_id?: number
  age?: number | null
  education?: string | null
  major?: string | null
  experience_years?: number | null
  target_position?: string | null
  target_city?: string | null
  expected_salary?: string | null
  skills?: string | null
  projects?: string | null
  self_evaluation?: string | null
}

export interface AutoProfileInput {
  resume_id?: number
  job_description_id?: number
  target_position?: string
}

export interface AutoProfileDraft {
  profile_patch: Partial<Profile>
  completeness: number
  auto_summary?: string | null
  warnings: string[]
}

export function fetchProfile() {
  return apiClient.get<Profile>('/profile/me')
}

export function updateProfile(data: Profile) {
  return apiClient.put<Profile>('/profile/me', data)
}

export function autoGenerateProfile(data: AutoProfileInput) {
  return apiClient.post<AutoProfileDraft>('/profile/auto-generate', data)
}
