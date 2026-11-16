import fs from 'node:fs/promises'
import path from 'node:path'
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool'

const [input, outputDirectory] = process.argv.slice(2)
if (!input || !outputDirectory) throw new Error('需要输入XLSX和输出目录')
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input))
const overview = await workbook.inspect({
  kind: 'sheet,formula,region',
  range: 'A1:D5',
  maxChars: 5000,
  tableMaxRows: 8,
  tableMaxCols: 6,
})
await fs.mkdir(outputDirectory, { recursive: true })
await fs.writeFile(path.join(outputDirectory, 'inspect.ndjson'), overview.ndjson, 'utf8')
const names = workbook.worksheets.items.map((sheet) => sheet.name)
for (let index = 0; index < names.length; index += 1) {
  const preview = await workbook.render({ sheetName: names[index], range: 'A1:D5', scale: 2, format: 'png' })
  await fs.writeFile(path.join(outputDirectory, `sheet-${index + 1}.png`), new Uint8Array(await preview.arrayBuffer()))
}
console.log(JSON.stringify({ sheets: names, previews: names.length }))
