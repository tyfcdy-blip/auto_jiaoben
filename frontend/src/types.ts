export interface User {
  id: string
  username: string
  display_name: string
  is_admin: boolean
  active: boolean
}
export type Role = 'project_admin' | 'developer' | 'operator' | 'viewer'
export interface Project {
  id: string
  name: string
  role: Role
}
export interface Program {
  id: string
  project_id: string
  name: string
  description: string
}
export interface Version {
  id: string
  program_id: string
  name: string
  command: string[]
  parameter_schema: Record<string, unknown>
  published: boolean
  bundle_sha256: string | null
  side_effects: boolean
  result_required: boolean
  required_capabilities: string[]
}
export interface Task {
  input_mode?: 'directory' | 'batch' | 'none'
  id: string
  project_id: string
  program_id?: string
  name: string
  version_id: string
  default_parameters: Record<string, unknown>
  node_id: string | null
  timeout_seconds: number
  resource_keys: string[]
  requires_input: boolean
  operation: string
  enabled: boolean
  business_key_fields?: string[]
  parameter_schema?: Record<string, unknown>
  config_hash: string
}
export interface Run {
  id: string
  project_id: string
  project_name?: string
  run_id?: string
  task_name: string
  task_id: string
  source: 'manual' | 'schedule' | 'event'
  state: string
  parameters: Record<string, unknown>
  result: Record<string, unknown> | null
  exit_code: number | null
  observation: string | null
  error: string | null
  artifact_state: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  node_id: string | null
  attempt_id: string | null
  waiting_reason_code?: string
  waiting_reason?: string
  original_source?: 'manual' | 'schedule' | 'event'
  schedule_triggered_at?: string
  schedule_event_id?: string
  schedule_deduplicated?: boolean
  artifacts?: {
    id: string
    name: string
    sha256: string
    size: number
    created_at: string
    run_id: string
  }[]
}
export interface Batch {
  id: string
  project_id?: string
  label: string
  ready: boolean
  state?: string
  files?: { name: string; size?: number; sha256?: string }[]
  created_at?: string
}
export interface Node {
  id: string
  name: string
  project_id: string
  capabilities: string[]
  slots: number
  last_seen: string | null
  online: boolean
  enabled: boolean
  maintenance: boolean
  isolation: string
  config_revision?: number
  applied_revision?: number
  config_status?: string
  project_input_roots?: string[]
  dispatch_status?: string
  dispatch_detail?: string
  capacity_status?: string
  active_runs?: number
  available_slots?: number
}
export interface Caller {
  id: string
  project_id?: string
  name: string
  task_ids: string[]
  active: boolean
  allow_input_upload?: boolean
}
export interface Schedule {
  id: string
  project_id: string
  task_id: string
  name: string
  cron: string
  timezone: string
  parameters: Record<string, unknown>
  input_batch_id: string | null
  input_label?: string | null
  misfire_policy: 'skip' | 'once'
  enabled: boolean
  next_fire?: string | null
  last_error?: string | null
  revision: number
  created_at?: string
}
export interface Alert {
  id: string
  project_id: string
  run_id: string | null
  node_id: string | null
  kind: string
  severity: 'critical' | 'warning' | 'info'
  message: string
  status: string
  delivery_state: string
  created_at: string
}
export interface ChannelAlertPolicy {
  channel_id: string
  enabled_kinds: string[]
  legacy_all: boolean
  revision: number
}
export interface TaskResultNotificationConfig {
  task_id: string
  channel_id: string | null
  enabled: boolean
  revision: number
}
export interface TaskNotificationRule {
  id: string
  project_id: string
  name: string
  notification_enabled: boolean
  channel_name: string | null
}
export interface ResultNotification {
  id: string
  project_id: string
  task_id: string
  run_id: string
  channel_id: string
  channel_name: string
  content: string
  delivery_state: string
  attempts: number
  last_error: string | null
  created_at: string
}
export interface AlertDelivery {
  id: string
  alert_id: string
  project_id: string
  channel_id: string
  channel_name: string
  state: string
  attempts: number
  last_error: string | null
  created_at: string
}
export interface Channel {
  id: string
  project_id?: string
  name: string
  enabled: boolean
}
export interface Audit {
  id: string
  actor: string
  action: string
  resource_id: string | null
  project_id: string | null
  created_at: string
  note: string | null
}
export interface Member {
  user_id: string
  username?: string
  display_name?: string
  role: Role
}
export interface Page<T> {
  items: T[]
  total: number
}
export interface Overview {
  total_tasks: number
  active_runs: number
  queued_runs: number
  failed_runs: number
  online_nodes: number
  open_alerts: number
  recent_runs: Run[]
}
