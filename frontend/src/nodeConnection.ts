import { DIRECTORY_CAPABILITY, inputRoots } from './directoryInput'
export interface NodeConnection {
  format: 'workbench-connection-v1'
  node_id: string
  name: string
  base_url: string
  token: string
  runtimes: Record<string, string>
  capabilities: string[]
  input_roots?: string[]
  config_revision: number
  config_hash: string
}

export function connectionOptions(
  address: string,
  alias: string,
  executable: string,
  extra: string,
  rootsText = '',
) {
  let url: URL
  try {
    url = new URL(address.trim())
  } catch {
    throw new Error('请输入完整的工作台地址。')
  }
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new Error('工作台地址必须是没有账号和查询参数的 HTTP(S) 地址。')
  if (
    url.protocol !== 'https:' &&
    !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  )
    throw new Error('远程电脑必须通过 HTTPS 连接工作台；HTTP 仅用于本机测试。')
  if (/[%\\\s]/.test(url.pathname) || url.pathname.includes('//'))
    throw new Error('工作台应用路径不合法。')
  const runtimeAlias = alias.trim(),
    path = executable.trim()
  if (!/^[a-zA-Z0-9_-]{1,80}$/.test(runtimeAlias))
    throw new Error('运行时名称只能包含字母、数字、短横线和下划线。')
  if (
    !(/^[a-zA-Z]:[\\/]/.test(path) || path.startsWith('/')) ||
    /[\r\n\0]/.test(path)
  )
    throw new Error('请填写执行电脑上运行程序的完整绝对路径。')
  const roots = inputRoots(rootsText)
  const capabilities = [
    ...new Set([
      runtimeAlias,
      ...extra.split(/[,，\s]+/).filter(Boolean),
      ...(roots.length ? [DIRECTORY_CAPABILITY] : []),
    ]),
  ]
  if (
    capabilities.length > 30 ||
    capabilities.some((value) => value.length > 100)
  )
    throw new Error('运行环境与额外能力合计最多 30 项，每项不超过 100 个字符。')
  return {
    base_url: url.href.replace(/\/$/, ''),
    runtimes: { [runtimeAlias]: path },
    capabilities,
    ...(roots.length ? { input_roots: roots } : {}),
  }
}

export function downloadConnection(connection: NodeConnection) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(connection, null, 2)], {
      type: 'application/json',
    }),
  )
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `workbench-connection-${connection.node_id}.json`
  anchor.hidden = true
  document.body.append(anchor)
  try {
    anchor.click()
  } finally {
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
}
