import type { AppError } from '../utils/errors'

export interface Page {
  index: number
  type: 'cover' | 'content' | 'summary'
  content: string
}

export interface OutlineResponse {
  success: boolean
  outline?: string
  pages?: Page[]
  error?: AppError | string
  error_message?: string
}

export interface ProgressEvent {
  index: number
  status: 'generating' | 'done' | 'error'
  current?: number
  total?: number
  image_url?: string
  message?: string
  error?: AppError | string
  retryable?: boolean
}

export interface FinishEvent {
  success: boolean
  task_id: string
  images: string[]
  total?: number
  completed?: number
  failed?: number
  failed_indices?: number[]
  cached?: boolean
}

export interface HistoryRecord {
  id: string
  title: string
  created_at: string
  updated_at: string
  status: string
  thumbnail: string | null
  page_count: number
  task_id: string | null
}

export interface HistoryDetail {
  id: string
  title: string
  created_at: string
  updated_at: string
  outline: {
    raw: string
    pages: Page[]
  }
  images: {
    task_id: string | null
    generated: string[]
  }
  status: string
  thumbnail: string | null
}

export interface CreateHistoryParams {
  topic: string
  outline: { raw: string; pages: Page[] }
  task_id?: string
}

export interface UpdateHistoryParams {
  outline?: { raw: string; pages: Page[] }
  images?: { task_id: string | null; generated: string[] }
  status?: string
  thumbnail?: string
}

export interface Config {
  text_generation: {
    active_provider: string
    providers: Record<string, any>
  }
  image_generation: {
    active_provider: string
    providers: Record<string, any>
  }
}

export interface AntigravityCredentialState {
  connected: boolean
  email?: string | null
  project_id?: string | null
  expires_at?: number | null
  expires_in_seconds?: number | null
  can_refresh?: boolean
  needs_relogin?: boolean
  account_id?: string | null
  account_count?: number
  healthy_count?: number
  cooling_down_count?: number
  dead_count?: number
  accounts?: AntigravityAccount[]
  recovered_at?: number | null
}

export interface AntigravityAccount {
  id: string
  email?: string | null
  project_id?: string | null
  connected: boolean
  dead: boolean
  cooling_down: boolean
  blocked_until?: number | null
  block_reason?: string | null
  last_error?: string | null
  can_refresh?: boolean
  expires_at?: number | null
  expires_in_seconds?: number | null
  last_used?: number | null
  updated_at?: number | null
}

export interface AntigravityLoginStatus {
  success: boolean
  flow?: 'idle' | 'waiting' | 'provisioning' | 'connected' | 'failed' | 'cancelled'
  message?: string
  auth_url?: string | null
  error?: string | null
  connected?: boolean
  credential?: AntigravityCredentialState
}

export interface AntigravityAccountsResponse {
  success: boolean
  accounts: AntigravityAccount[]
  message?: string
}

export interface AntigravityModels {
  success: boolean
  text_models?: string[]
  image_models?: string[]
}

export interface AntigravityQuotaBucket {
  bucket_id?: string | null
  remaining_fraction?: number | null
  reset_time?: string | null
  disabled?: boolean | null
}

export interface AntigravityQuota {
  success: boolean
  quota?: {
    groups: Array<{ group_id?: string | null; buckets: AntigravityQuotaBucket[] }>
    error?: string
  }
}

export interface ContentResponse {
  success: boolean
  titles?: string[]
  copywriting?: string
  tags?: string[]
  error?: AppError | string
  error_message?: string
}
