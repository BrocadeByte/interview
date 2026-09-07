import { apiClient } from './client'

export interface KnowledgeDocument {
  id: number
  title: string
  category: string
  target_position: string
  content: string
  metadata: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface KnowledgeDocumentPayload {
  title: string
  category: string
  target_position: string
  content: string
  metadata: Record<string, unknown>
}

export interface KnowledgeIngestionTask {
  id: number
  document_id: number | null
  status: string
  stage: string
  title: string
  category: string
  target_position: string
  file_name: string
  file_type: string
  file_size: number
  attempts: number
  max_attempts: number
  error: string | null
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
}

export function fetchKnowledgeDocuments() { return apiClient.get<KnowledgeDocument[]>('/knowledge/documents') }
export function createKnowledgeDocument(data: KnowledgeDocumentPayload) { return apiClient.post<KnowledgeDocument>('/knowledge/documents', data) }
export function updateKnowledgeDocument(id: number, data: KnowledgeDocumentPayload) { return apiClient.put<KnowledgeDocument>(`/knowledge/documents/${id}`, data) }
export function deleteKnowledgeDocument(id: number) { return apiClient.delete(`/knowledge/documents/${id}`) }

export function uploadKnowledgeFile(data: { file: File; title?: string; category: string; target_position?: string }) {
  const form = new FormData()
  form.append('file', data.file)
  if (data.title?.trim()) form.append('title', data.title.trim())
  form.append('category', data.category)
  if (data.target_position?.trim()) form.append('target_position', data.target_position.trim())
  return apiClient.post<KnowledgeIngestionTask>('/knowledge/files/async', form)
}

export function fetchIngestionTasks() { return apiClient.get<KnowledgeIngestionTask[]>('/knowledge/ingestion-tasks') }
export function fetchIngestionTask(id: number) { return apiClient.get<KnowledgeIngestionTask>(`/knowledge/ingestion-tasks/${id}`) }
export function retryIngestionTask(id: number) { return apiClient.post<KnowledgeIngestionTask>(`/knowledge/ingestion-tasks/${id}/retry`) }
export function reexecuteIngestionTask(id: number) { return apiClient.post<KnowledgeIngestionTask>(`/knowledge/ingestion-tasks/${id}/reexecute`) }
