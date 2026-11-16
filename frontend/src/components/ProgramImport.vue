<script setup lang="ts">
import { computed, onScopeDispose, ref, watch } from 'vue'
import { ApiError, parseObject, parseStrings, request } from '../api'
import { useAction } from '../composables'
import { canManage, session } from '../session'
import type { Program, Version } from '../types'
import Feedback from './Feedback.vue'
import Modal from './Modal.vue'
import {
  DIRECTORY_CAPABILITY,
  directorySchema,
  inputSchema,
} from '../directoryInput'

const props = defineProps<{
  program?: Program | null
  draft?: Version | null
}>()
const emit = defineEmits<{ close: []; saved: [program: Program] }>()
const projectId = session.projectId
const action = useAction()
const name = ref(props.program?.name || '')
const description = ref(props.program?.description || '')
const runtime = ref(props.draft?.command[0] || 'python')
const entry = ref(props.draft?.command[1] || 'main.py')
const versionName = ref(
  props.draft?.name || `v${new Date().toISOString().replace(/[-:TZ.]/g, '')}`,
)
const schema = ref(
  JSON.stringify(
    props.draft?.parameter_schema || {
      type: 'object',
      properties: {},
      additionalProperties: false,
    },
    null,
    2,
  ),
)
const argumentsText = ref(JSON.stringify(props.draft?.command.slice(2) || []))
const capabilities = ref((props.draft?.required_capabilities || []).join(', '))
const inputMode = ref(
  directorySchema(props.draft?.parameter_schema) ? 'directory' : 'parameters',
)
const sideEffects = ref(props.draft?.side_effects || false)
const resultRequired = ref(props.draft?.result_required ?? true)
const file = ref<File | null>(null)
type BundleFinding = {
  severity: 'error' | 'high' | 'warning'
  code: string
  file: string
  message: string
  line?: number
}
type BundleScan = {
  decision: 'pass' | 'review' | 'blocked'
  coverage: 'full' | 'partial'
  summary: { errors: number; high: number; warnings: number }
  findings: BundleFinding[]
  findings_truncated?: boolean
}
const bundleScan = ref<BundleScan | null>(null)
const reviewedRisk = ref(false)
let scannedFile: File | null = null
const savedProgram = ref<Program | null>(props.program || null)
const savedVersion = ref<Version | null>(props.draft || null)
const uploaded = ref(Boolean(props.draft?.bundle_sha256))
const uncertain = ref(false)
const stage = ref('')
const locked = computed(() => Boolean(savedVersion.value))
const shouldPublish = ref(canManage.value && !sideEffects.value)
let active = true
const controller = new AbortController()
onScopeDispose(() => {
  active = false
  controller.abort()
})
watch(
  () => session.projectId,
  () => {
    active = false
    controller.abort()
    emit('close')
  },
)
watch(runtime, (value, previous) => {
  if (
    !locked.value &&
    ((previous === 'python' && entry.value === 'main.py') ||
      (previous === 'node' && entry.value === 'main.js'))
  )
    entry.value = value === 'node' ? 'main.js' : 'main.py'
})
function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  uploaded.value = false
  bundleScan.value = null
  reviewedRisk.value = false
  scannedFile = null
}
async function ensureBundleScan() {
  if (bundleScan.value && (uploaded.value || scannedFile === file.value))
    return bundleScan.value
  stage.value = '安全与格式检查'
  let inspected: BundleScan
  if (uploaded.value && savedVersion.value) {
    inspected = await request<BundleScan>(
      `/versions/${savedVersion.value.id}/bundle-scan`,
      { signal: controller.signal },
    )
  } else {
    const query = new URLSearchParams({
      project_id: projectId || '',
      runtime: runtime.value.trim(),
      entry: entry.value.trim(),
    })
    inspected = await request<BundleScan>(`/program-bundles/scan?${query}`, {
      method: 'POST',
      signal: controller.signal,
      body: new Blob([file.value!], { type: 'application/zip' }),
    })
  }
  if (
    !inspected ||
    !['pass', 'review', 'blocked'].includes(inspected.decision) ||
    !inspected.summary ||
    !Array.isArray(inspected.findings)
  )
    throw new Error('程序包检查响应不完整，请稍后重试。')
  bundleScan.value = inspected
  if (!uploaded.value) scannedFile = file.value
  return bundleScan.value
}
async function save() {
  if (!active || uncertain.value) return
  await action.perform(async () => {
    const payload = {
      name: versionName.value.trim(),
      command: [
        runtime.value.trim(),
        entry.value.trim(),
        ...parseStrings(argumentsText.value, '附加启动参数'),
      ],
      parameter_schema: inputSchema(
        parseObject(schema.value, '业务参数定义'),
        inputMode.value === 'directory',
      ),
      required_capabilities: [
        ...new Set([
          runtime.value.trim(),
          ...capabilities.value
            .split(',')
            .map((value) => value.trim())
            .filter(Boolean),
          ...(inputMode.value === 'directory' ? [DIRECTORY_CAPABILITY] : []),
        ]),
      ],
      side_effects: sideEffects.value,
      result_required: resultRequired.value,
      protocol_version: '1',
    }
    if (
      !name.value.trim() ||
      !payload.name ||
      !payload.command[0] ||
      !payload.command[1]
    )
      throw new Error('请填写程序名称、运行环境和入口文件。')
    if (
      !uploaded.value &&
      (!file.value ||
        !file.value.name.toLowerCase().endsWith('.zip') ||
        !file.value.size)
    )
      throw new Error('请选择包含入口文件的非空 ZIP 程序包。')
    if (file.value && file.value.size > 100 * 1024 * 1024)
      throw new Error('程序包不能超过 100 MB。')
    const options = { method: 'POST', signal: controller.signal }
    try {
      const scan = await ensureBundleScan()
      if (scan.decision === 'blocked')
        throw new Error(
          '程序包存在确定错误或禁止项，请根据检查结果修复后重新选择。',
        )
      if (scan.decision === 'review' && !reviewedRisk.value)
        throw new Error('程序包包含高风险能力，请核对检查结果并确认后继续。')
      if (!savedProgram.value) {
        stage.value = '保存程序'
        const created = await request<Program>('/programs', {
          ...options,
          body: {
            project_id: projectId,
            name: name.value.trim(),
            description: description.value.trim(),
          },
        })
        if (
          !created ||
          typeof created.id !== 'string' ||
          !created.id ||
          created.project_id !== projectId
        )
          throw new Error('程序保存响应不完整。')
        savedProgram.value = created
      }
      if (!active) return
      if (!savedVersion.value) {
        stage.value = '保存版本'
        const created = await request<Version>(
          `/programs/${savedProgram.value.id}/versions`,
          { ...options, body: payload },
        )
        if (
          !created ||
          typeof created.id !== 'string' ||
          !created.id ||
          created.program_id !== savedProgram.value.id
        )
          throw new Error('版本保存响应不完整。')
        savedVersion.value = created
      }
      if (!active) return
      if (!uploaded.value) {
        stage.value = '上传程序包'
        const stored = await request<Version>(
          `/versions/${savedVersion.value.id}/bundle?accept_review=${reviewedRisk.value}`,
          {
            ...options,
            body: new Blob([file.value!], { type: 'application/zip' }),
          },
        )
        if (
          !stored ||
          stored.id !== savedVersion.value.id ||
          !stored.bundle_sha256
        )
          throw new Error('程序包保存结果尚不能确认，请继续重试。')
        savedVersion.value = stored
        uploaded.value = true
      }
      if (!active) return
      if (shouldPublish.value) {
        stage.value = '发布版本'
        const published = await request<Version>(
          `/versions/${savedVersion.value.id}/publish?accept_review=${reviewedRisk.value}`,
          options,
        )
        if (
          !published ||
          published.id !== savedVersion.value.id ||
          published.published !== true
        )
          throw new Error('发布结果尚不能确认，请继续重试。')
        savedVersion.value = published
      }
      if (active) emit('saved', savedProgram.value)
    } catch (failure) {
      // 创建响应丢失时无法安全猜测对象编号。停止自动创建，让用户从已保存记录继续。
      if (
        !(
          failure instanceof ApiError &&
          failure.status >= 400 &&
          failure.status < 500
        ) &&
        ['保存程序', '保存版本'].includes(stage.value)
      ) {
        uncertain.value = true
        throw new Error(
          '保存结果尚不能确认。请关闭窗口并刷新程序列表，核对已保存记录后继续接入，避免重复创建。',
        )
      }
      throw failure
    }
  }, '')
}
</script>
<template>
  <Modal
    :title="draft ? '继续接入' : program ? '更新程序' : '接入程序'"
    wide
    :busy="action.pending.value"
    @close="emit('close')"
  >
    <form @submit.prevent="save">
      <p class="muted">
        选好程序包，工作台会依次保存版本、上传并校验内容。更新程序时保留历史版本，已有任务不受影响。
      </p>
      <Feedback :error="action.error.value" />
      <fieldset
        :disabled="action.pending.value || Boolean(savedProgram)"
        class="import-fields"
        v-if="!program"
      >
        <label>
          程序名称
          <input
            v-model="name"
            required
            maxlength="100"
            placeholder="例如：月度账单合并"
          />
        </label>
        <label>
          用途说明
          <textarea
            v-model="description"
            rows="2"
            maxlength="2000"
            placeholder="这个程序帮助团队完成什么工作？"
          ></textarea>
        </label>
      </fieldset>
      <fieldset
        :disabled="action.pending.value || locked"
        class="import-fields"
      >
        <div class="form-grid">
          <label>
            输入方式
            <select v-model="inputMode" aria-label="程序输入方式">
              <option value="parameters">常规参数 / 工作台文件批次</option>
              <option value="directory">执行电脑上的文件夹</option>
            </select>
            <small v-if="inputMode === 'directory'">
              任务会填写文件夹和批次编号，并固定执行电脑。其他业务参数可在高级设置中保留。
            </small>
          </label>
          <label>
            运行环境
            <select v-model="runtime" required>
              <option value="python">Python</option>
              <option value="node">Node.js</option>
              <option
                v-if="!['python', 'node'].includes(runtime)"
                :value="runtime"
              >
                {{ runtime }}
              </option>
            </select>
          </label>
          <label>
            入口文件
            <input
              v-model="entry"
              required
              maxlength="500"
              placeholder="main.py"
            />
            <small>相对于 ZIP 根目录，例如 scripts/main.py。</small>
          </label>
        </div>
      </fieldset>
      <label v-if="!uploaded" class="upload-area">
        ZIP 程序包
        <input
          type="file"
          accept=".zip,application/zip"
          :disabled="action.pending.value || uncertain"
          @change="chooseFile"
        />
        <small>
          {{
            file
              ? `${file.name} · ${(file.size / 1024).toFixed(1)} KB`
              : '将入口脚本和依赖文件一起打包；不包含密码、缓存或虚拟环境。'
          }}
        </small>
      </label>
      <p v-else class="feedback success">
        程序包已保存，接下来可以发布，无需重新上传。
      </p>
      <section
        v-if="bundleScan"
        class="scan-result"
        :class="`scan-${bundleScan.decision}`"
        aria-live="polite"
      >
        <strong>
          {{
            bundleScan.decision === 'pass'
              ? '程序包检查通过'
              : bundleScan.decision === 'blocked'
                ? '程序包已阻断'
                : '程序包需要人工复核'
          }}
        </strong>
        <span>
          {{ bundleScan.summary.errors }} 个错误 ·
          {{ bundleScan.summary.high }} 个高风险项
          {{
            bundleScan.coverage === 'partial' ? ' · 部分内容无法静态检查' : ''
          }}
          {{ bundleScan.findings_truncated ? ' · 检测明细已截断' : '' }}
        </span>
        <ul v-if="bundleScan.findings.length">
          <li
            v-for="finding in bundleScan.findings"
            :key="`${finding.code}-${finding.file}-${finding.line}`"
          >
            <b>{{ finding.severity === 'error' ? '错误' : '高风险' }}</b>
            {{ finding.file }}
            <template v-if="finding.line">:{{ finding.line }}</template>
            — {{ finding.message }}
          </li>
        </ul>
        <label
          v-if="bundleScan.decision === 'review'"
          class="checkbox review-check"
        >
          <input v-model="reviewedRisk" type="checkbox" />
          我已核对这些能力是该程序正常运行所必需，并接受继续接入
        </label>
        <small>
          静态检查用于发现已知语法和高风险模式，不能证明业务逻辑完全正确。
        </small>
      </section>
      <details class="advanced-options">
        <summary>高级设置 · 版本与参数</summary>
        <fieldset
          :disabled="action.pending.value || locked"
          class="import-fields"
        >
          <label>
            版本名称
            <input v-model="versionName" required maxlength="100" />
            <small>已自动生成，也可以在首次保存前填写团队约定的版本号。</small>
          </label>
          <label>
            运行时别名
            <input
              v-model="runtime"
              required
              pattern="[a-zA-Z][a-zA-Z0-9_\-]{0,39}"
            />
            <small>其他语言可填写执行电脑上配置的运行时别名。</small>
          </label>
          <label>
            业务参数定义（JSON Schema）
            <textarea
              v-model="schema"
              class="mono"
              rows="6"
              required
            ></textarea>
          </label>
          <label>
            附加启动参数（JSON 数组）
            <input v-model="argumentsText" class="mono" required />
          </label>
          <label>
            额外环境要求（逗号分隔）
            <input v-model="capabilities" placeholder="例如：excel" />
          </label>
          <label class="checkbox">
            <input v-model="resultRequired" type="checkbox" />
            要求程序提供结构化结果
          </label>
        </fieldset>
      </details>
      <label class="checkbox">
        <input
          v-model="sideEffects"
          type="checkbox"
          :disabled="action.pending.value || locked"
          @change="shouldPublish = false"
        />
        此程序会向外部系统写入数据
      </label>
      <p v-if="sideEffects" class="feedback warning">
        当前开发环境尚不允许发布外部写入程序，可以先保存为草稿。
      </p>
      <label v-if="canManage" class="checkbox">
        <input
          v-model="shouldPublish"
          type="checkbox"
          :disabled="action.pending.value || sideEffects"
        />
        校验通过后发布，允许创建任务
      </label>
      <p v-else class="muted">上传后保存为草稿，由项目管理员发布。</p>
      <p v-if="action.pending.value" role="status">
        {{ stage }}… 请保持窗口打开。
      </p>
      <p
        v-else-if="savedVersion && action.error.value && !uncertain"
        class="muted"
      >
        已保存的步骤会保留。修正问题后点击继续，不会重复登记版本。
      </p>
      <div class="form-actions">
        <button
          class="button primary"
          :disabled="action.pending.value || uncertain"
        >
          {{
            action.pending.value
              ? `${stage}…`
              : savedVersion
                ? '继续完成'
                : shouldPublish
                  ? '上传并发布'
                  : '上传并保存草稿'
          }}
        </button>
      </div>
    </form>
  </Modal>
</template>
<style scoped>
.import-fields {
  border: 0;
  padding: 0;
  margin: 0;
  min-width: 0;
}
.upload-area {
  padding: 20px;
  border: 1px dashed var(--accent, #2563eb);
  border-radius: 12px;
  background: #eff6ff;
}
.scan-result {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  padding: 14px 16px;
  border: 1px solid #bfdbfe;
  border-radius: 12px;
  background: #eff6ff;
}
.scan-result > span,
.scan-result small {
  color: var(--muted, #64748b);
}
.scan-result ul {
  margin: 2px 0;
  padding-left: 20px;
}
.scan-blocked {
  border-color: #fecaca;
  background: #fef2f2;
}
.scan-review {
  border-color: #fde68a;
  background: #fffbeb;
}
.review-check {
  margin-top: 2px;
}
</style>
