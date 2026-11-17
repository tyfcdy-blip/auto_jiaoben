export const NODE_FILES_CAPABILITY = 'node-files-v1'

export interface FileOptions {
  inputs: Record<string, string | string[]>
  output_dir: string | null
}
export interface FileConfiguration {
  revision: number
  applied_revision: number
  apply_status: string
  output_roots: string[]
  allowed_input_roots: string[]
  allowed_output_roots: string[]
  grants: { project_id: string; output_roots: string[] }[]
}
export interface FileEntry {
  name: string
  path: string
  kind: 'file' | 'directory'
  size: number | null
}
export interface FileListing {
  id: string
  state: 'pending' | 'leased' | 'succeeded' | 'failed' | 'expired'
  expires_at: number
  path: string
  entries: FileEntry[]
  truncated: boolean
  error: string
  next_offset?: number | null
}
export function fileFields(
  schema?: Record<string, unknown> | null,
): { key: string; title: string; kind: 'file' | 'files' | 'directory' }[] {
  const properties = schema?.properties
  if (
    !properties ||
    typeof properties !== 'object' ||
    Array.isArray(properties)
  )
    return []
  return Object.entries(properties).flatMap(([key, definition]) => {
    if (!definition || typeof definition !== 'object') return []
    const field = definition as Record<string, unknown>
    const kind = field['x-workbench-file']
    if (kind !== 'file' && kind !== 'files' && kind !== 'directory') return []
    return [
      { key, title: typeof field.title === 'string' ? field.title : key, kind },
    ]
  })
}
export function emptyFileOptions(): FileOptions {
  return { inputs: {}, output_dir: null }
}
