import { apiClient } from './client'

export interface User {
  id: number
  email: string
  username: string
  is_admin: boolean
  created_at: string
  last_login_at?: string | null
}

export interface AuthResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

export interface SessionResponse {
  authenticated: boolean
  access_token?: string | null
  token_type?: string | null
  expires_in?: number | null
  user?: User | null
}

export function register(data: { email: string; username: string; password: string }) {
  return apiClient.post<AuthResponse>('/auth/register', data)
}

export function login(data: { email: string; password: string }) {
  return apiClient.post<AuthResponse>('/auth/login', data)
}

export function fetchMe() {
  return apiClient.get<User>('/auth/me')
}

export function refreshSession() {
  return apiClient.post<AuthResponse>('/auth/refresh')
}

export function fetchSession() {
  return apiClient.get<SessionResponse>('/auth/session')
}

export function logoutSession() {
  return apiClient.post<void>('/auth/logout')
}

export function logoutAllSessions() {
  return apiClient.post<void>('/auth/logout-all')
}

