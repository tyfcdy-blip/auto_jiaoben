'use strict'
// 只统计平台提供的输入清单，不读取清单之外的文件，不使用第三方依赖。
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

async function summarize(context, output) {
  const root = context.parameters.input_dir
  if (!path.isAbsolute(root) || !fs.statSync(root).isDirectory() || !context.parameters.batch_key)
    throw new Error('input_dir 必须是存在的绝对目录，batch_key 不能为空')
  const canonicalRoot = fs.realpathSync(root)
  const extensions = {}
  let bytes = 0
  for (const entry of context.input_manifest) {
    if (!path.isAbsolute(entry.path) || fs.lstatSync(entry.path).isSymbolicLink())
      throw new Error('输入文件路径不合法')
    const relative = path.relative(canonicalRoot, fs.realpathSync(entry.path))
    if (!relative || relative === '..' || relative.startsWith('..' + path.sep) || path.isAbsolute(relative))
      throw new Error('输入文件超出指定目录')
    const digest = crypto.createHash('sha256')
    let size = 0
    for await (const chunk of fs.createReadStream(entry.path)) {
      digest.update(chunk)
      size += chunk.length
    }
    if (digest.digest('hex') !== entry.sha256) throw new Error('文件校验值不一致')
    const extension = path.extname(entry.name).toLowerCase() || '(无扩展名)'
    extensions[extension] = (extensions[extension] || 0) + 1
    bytes += size
    console.log(`processed_file=${entry.name} bytes=${size}`)
  }
  const summary = { batch_key: context.parameters.batch_key, files: context.input_manifest.length, bytes, extensions }
  fs.writeFileSync(path.join(output, 'directory-summary.json.tmp'), JSON.stringify(summary, null, 2), { flag: 'wx' })
  fs.renameSync(path.join(output, 'directory-summary.json.tmp'), path.join(output, 'directory-summary.json'))
  return { status: 'success', summary: `已统计 ${summary.files} 个文件，共 ${bytes} 字节`, metrics: summary,
    artifacts: [{ name: 'directory-summary.json', required: true }] }
}

async function main() {
  const index = process.argv.indexOf('--context-file')
  if (index < 0 || !process.argv[index + 1]) throw new Error('缺少 --context-file')
  const context = JSON.parse(fs.readFileSync(process.argv[index + 1], 'utf8'))
  const output = context.output_dir
  if (!path.isAbsolute(output) || !fs.statSync(output).isDirectory()) throw new Error('平台输出目录无效')
  const relativeOutput = path.relative(fs.realpathSync(context.parameters.input_dir), fs.realpathSync(output))
  if (!relativeOutput || (!relativeOutput.startsWith('..' + path.sep) && relativeOutput !== '..' && !path.isAbsolute(relativeOutput)))
    throw new Error('输出目录不能位于只读输入目录内')
  let result
  try { result = await summarize(context, output) }
  catch (error) {
    result = { status: 'failed', summary: `目录统计失败：${error.message}`, artifacts: [] }
    console.error(result.summary)
    process.exitCode = 2
  }
  Object.assign(result, { protocol_version: '1', run_id: context.run_id, attempt_id: context.attempt_id })
  fs.writeFileSync(path.join(output, 'result.json.tmp'), JSON.stringify(result, null, 2))
  fs.renameSync(path.join(output, 'result.json.tmp'), path.join(output, 'result.json'))
}
main().catch((error) => { console.error(error.message); process.exitCode = 2 })
