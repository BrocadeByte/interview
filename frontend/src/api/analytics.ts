import { apiClient } from './client'


export type ClientAnalyticsEvent =
  | {
      event_name: 'profile_applied'
      resume_id?: number
      job_description_id?: number
    }
  | {
      event_name: 'question_review_expanded'
      report_id: number
      question_review_id: number
    }

export function trackAnalyticsEvent(event: ClientAnalyticsEvent) {
  return apiClient.post<void>('/analytics/events', {
    ...event,
    client_event_id: crypto.randomUUID()
  })
}
