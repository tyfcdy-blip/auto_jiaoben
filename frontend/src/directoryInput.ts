import type { Task } from './types'

export const DIRECTORY_CAPABILITY = 'directory-input-v1'
export function directorySchema(schema?: Record<string, unknown>): boolean {
  return schema?.['x-workbench-input'] === 'directory'
}
export function directoryTask(task?: Task | null): boolean {
  // 旧后端没有派生字段时保持原任务行为，不根据碰巧同名的参数猜测模式。
  return task?.input_mode === 'directory'
}
export function inputSchema(
  source: Record<string, unknown>,
  directory: boolean,
) {
  const schema = structuredClone(source)
  if (!directory) {
    if (directorySchema(schema)) delete schema['x-workbench-input']
    return schema
  }
  const properties = schema.properties
  if (
    properties !== undefined &&
    (!properties || typeof properties !== 'object' || Array.isArray(properties))
  )
    throw new Error('业务参数定义的 properties 必须是对象。')
  if (
    schema.required !== undefined &&
    (!Array.isArray(schema.required) ||
      schema.required.some((key) => typeof key !== 'string'))
  )
    throw new Error('业务参数定义的 required 必须是字段名称数组。')
  return {
    ...schema,
    type: 'object',
    'x-workbench-input': 'directory',
    properties: {
      ...(properties || {}),
      input_dir: { type: 'string', minLength: 1, maxLength: 2000 },
      batch_key: { type: 'string', minLength: 1, maxLength: 180 },
    },
    required: [
      ...new Set([
        ...((schema.required as string[]) || []),
        'input_dir',
        'batch_key',
      ]),
    ],
    additionalProperties: false,
  }
}
export function directoryPath(value: string): string {
  const path = value
  // 与后端跨平台词法一致；这里不探测浏览器电脑上的目录或共享权限。
  const normalized = path.replaceAll('\\', '/')
  const drive = /^[a-zA-Z]:\//.test(normalized)
  const unc =
    normalized.startsWith('//') &&
    normalized.split('/').filter(Boolean).length >= 2
  const posix =
    normalized.startsWith('/') &&
    !normalized.startsWith('//') &&
    !path.includes('\\')
  const tail = drive ? normalized.slice(2) : normalized
  if (
    !path ||
    path !== path.trim() ||
    path.length > 2000 ||
    !(drive || unc || posix) ||
    /[\x00-\x1f\x7f<>"|?*]/.test(path) ||
    tail.includes(':') ||
    /\$\{/.test(path) ||
    normalized.startsWith('//?/') ||
    normalized.startsWith('//./') ||
    normalized.split('/').some((part) => part === '.' || part === '..')
  )
    throw new Error(
      '请输入执行电脑可访问的完整文件夹路径，不使用相对路径、越界段或日期拼接。',
    )
  return path
}
export function directoryParameters(
  parameters: Record<string, unknown>,
  inputDir: string,
  batchKey: string,
  scheduled = false,
) {
  const key = batchKey.trim()
  if (!key || key.length > 180 || /[\x00-\x1f]/.test(key))
    throw new Error('请填写 1 至 180 字的批次编号。')
  if (
    key.includes('${') &&
    (!scheduled || !['${scheduled_date}', '${previous_month}'].includes(key))
  )
    throw new Error(
      '批次编号仅在定时计划中支持完整的 ${scheduled_date} 或 ${previous_month}，不能拼接表达式。',
    )
  return { ...parameters, input_dir: directoryPath(inputDir), batch_key: key }
}
export function splitDirectoryParameters(parameters: Record<string, unknown>) {
  const { input_dir, batch_key, ...business } = parameters
  return {
    inputDir: typeof input_dir === 'string' ? input_dir : '',
    batchKey: typeof batch_key === 'string' ? batch_key : '',
    business,
  }
}
export function inputRoots(text: string): string[] {
  const roots = [
    ...new Set(
      text
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ].map(directoryPath)
  if (roots.length > 30)
    throw new Error('允许读取的文件夹根目录最多填写 30 项。')
  return roots
}
