import axios from 'axios'
import type { InternalAxiosRequestConfig } from 'axios'


export const ACCESS_TOKEN_KEY = 'access_token'
export const TOKEN_REFRESHED_EVENT = 'auth:token-refreshed'
export const SESSION_EXPIRED_EVENT = 'auth:session-expired'

interface RefreshResponse {
  access_token: string
}

type RetryableRequest = InternalAxiosRequestConfig & { _retry?: boolean }

let refreshPromise: Promise<string> | null = null


export function getApiErrorMessage(error: unknown, fallback = '请求失败') {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map((item) => item.msg || item.message || String(item)).join('；')
  }
  return fallback
}

export const apiClient = axios.create({
  baseURL: '/api',
  withCredentials: true
})

const refreshClient = axios.create({
  baseURL: '/api',
  withCredentials: true
})

export function getAccessToken() {
  return localStorage.getItem(ACCESS_TOKEN_KEY) || ''
}

export function setAccessToken(token: string | null) {
  if (token) localStorage.setItem(ACCESS_TOKEN_KEY, token)
  else localStorage.removeItem(ACCESS_TOKEN_KEY)
}

apiClient.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (!axios.isAxiosError(error) || error.response?.status !== 401 || !error.config) {
      return Promise.reject(error)
    }

    const request = error.config as RetryableRequest
    if (request._retry || isRefreshExcludedEndpoint(request.url)) {
      return Promise.reject(error)
    }

    request._retry = true
    try {
      const token = await refreshAccessToken()
      request.headers.Authorization = `Bearer ${token}`
      return apiClient(request)
    } catch {
      return Promise.reject(error)
    }
  }
)

export function refreshAccessToken(): Promise<string> {
  if (!refreshPromise) {
    refreshPromise = refreshClient
      .post<RefreshResponse>('/auth/refresh')
      .then(({ data }) => {
        setAccessToken(data.access_token)
        dispatchAuthEvent(TOKEN_REFRESHED_EVENT, data.access_token)
        return data.access_token
      })
      .catch((error) => {
        // 只有服务端明确拒绝 Refresh Token 时才退出；断网或 5xx 保留会话以便稍后重试。
        if (axios.isAxiosError(error) && error.response?.status === 401) {
          setAccessToken(null)
          dispatchAuthEvent(SESSION_EXPIRED_EVENT)
        }
        throw error
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

export async function authenticatedFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const send = (token: string) => {
    const headers = new Headers(init.headers)
    if (token) headers.set('Authorization', `Bearer ${token}`)
    return fetch(input, { ...init, headers, credentials: 'include' })
  }

  let response = await send(getAccessToken())
  if (response.status === 401 && !init.signal?.aborted) {
    const token = await refreshAccessToken()
    response = await send(token)
  }
  return response
}

function isRefreshExcludedEndpoint(url?: string) {
  return /^\/auth\/(login|register|refresh|session|logout)$/.test(url || '')
}

function dispatchAuthEvent(name: string, detail?: string) {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(name, { detail }))
}
