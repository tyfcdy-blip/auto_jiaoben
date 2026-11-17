<script setup lang="ts">
import { computed, watch } from 'vue'

type FieldSchema = {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  items?: { type?: string }
  minimum?: number
  maximum?: number
  format?: string
  'x-workbench-file'?: string
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    schema?: Record<string, unknown> | null
    label?: string
    scheduled?: boolean
    omitKeys?: string[]
  }>(),
  { schema: null, label: '业务参数', scheduled: false, omitKeys: () => [] },
)
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const reserved = new Set(['input_dir', 'batch_key'])
const usesDirectoryInput = computed(
  () => props.schema?.['x-workbench-input'] === 'directory',
)
const required = computed(
  () =>
    new Set(
      Array.isArray(props.schema?.required)
        ? (props.schema!.required as string[])
        : [],
    ),
)
const fields = computed(() => {
  const properties = props.schema?.properties
  if (
    !properties ||
    typeof properties !== 'object' ||
    Array.isArray(properties)
  )
    return []
  return Object.entries(properties as Record<string, FieldSchema>)
    .filter(([key, value]) => {
      if (
        props.omitKeys.includes(key) ||
        (usesDirectoryInput.value && reserved.has(key)) ||
        !value ||
        typeof value !== 'object'
      )
        return false
      if (Array.isArray(value.enum))
        return value.enum.every(
          (choice) =>
            choice === null ||
            ['string', 'number', 'boolean'].includes(typeof choice),
        )
      return (
        ['string', 'number', 'integer', 'boolean'].includes(value.type || '') ||
        (value.type === 'array' && value.items?.type === 'string')
      )
    })
    .map(([key, definition]) => ({ key, definition }))
})
const hasComplexFields = computed(() => {
  const properties = props.schema?.properties
  if (
    !properties ||
    typeof properties !== 'object' ||
    Array.isArray(properties)
  )
    return false
  return (
    Object.keys(properties as Record<string, unknown>).filter(
      (key) =>
        !props.omitKeys.includes(key) &&
        !(usesDirectoryInput.value && reserved.has(key)),
    ).length > fields.value.length
  )
})
const parsed = computed<Record<string, unknown> | null>(() => {
  try {
    const value = JSON.parse(props.modelValue)
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value
      : null
  } catch {
    return null
  }
})
watch(
  [() => props.schema, () => props.modelValue],
  () => {
    if (!parsed.value) return
    const next = { ...parsed.value }
    let changed = false
    for (const field of fields.value) {
      if (
        next[field.key] === undefined &&
        field.definition.default !== undefined
      ) {
        next[field.key] = field.definition.default
        changed = true
      }
    }
    if (changed) emit('update:modelValue', JSON.stringify(next, null, 2))
  },
  { immediate: true },
)

function current(field: { key: string; definition: FieldSchema }) {
  const value = parsed.value?.[field.key]
  return value === undefined ? field.definition.default : value
}
function supportsDateMode(field: { key: string; definition: FieldSchema }) {
  return (
    props.scheduled &&
    field.definition.type === 'string' &&
    !field.definition.enum &&
    !field.definition['x-workbench-file']
  )
}
function dateMode(field: { key: string; definition: FieldSchema }) {
  const value = current(field)
  return value === '${scheduled_date}' || value === '${previous_month}'
    ? value
    : 'fixed'
}
function updateDateMode(
  field: { key: string; definition: FieldSchema },
  event: Event,
) {
  const value = (event.target as HTMLSelectElement).value
  // 显式留空，避免默认日期变量在切回固定值后被默认值逻辑重新填入。
  replace(field.key, value === 'fixed' ? '' : value)
}
function textValue(field: { key: string; definition: FieldSchema }) {
  const value = current(field)
  if (field.definition.enum) {
    const index = field.definition.enum.findIndex((choice) =>
      Object.is(choice, value),
    )
    return index < 0 ? '' : String(index)
  }
  if (Array.isArray(value)) return value.join(', ')
  return value === undefined || value === null ? '' : String(value)
}
function replace(key: string, value: unknown, remove = false) {
  const next = { ...(parsed.value || {}) }
  if (remove) delete next[key]
  else next[key] = value
  emit('update:modelValue', JSON.stringify(next, null, 2))
}
function update(field: { key: string; definition: FieldSchema }, event: Event) {
  const target = event.target as HTMLInputElement | HTMLSelectElement
  const value = target.value
  if (value === '') {
    if (required.value.has(field.key) && field.definition.type === 'string')
      return replace(field.key, '')
    return replace(field.key, undefined, true)
  }
  if (field.definition.enum)
    return replace(field.key, field.definition.enum[Number(value)])
  if (field.definition.type === 'number' || field.definition.type === 'integer')
    return replace(field.key, Number(value))
  if (field.definition.type === 'boolean')
    return replace(field.key, value === 'true')
  if (field.definition.type === 'array')
    return replace(
      field.key,
      value
        .split(',')
        .map((item) => item.trim())
        .filter(Boolean),
    )
  replace(field.key, value)
}
function updateRaw(event: Event) {
  emit('update:modelValue', (event.target as HTMLTextAreaElement).value)
}
</script>

<template>
  <section class="parameter-editor">
    <header v-if="fields.length" class="parameter-heading">
      <strong>{{ label }}</strong>
      <span>按程序定义填写，带 * 的项目必填。</span>
    </header>
    <div v-if="fields.length" class="parameter-grid">
      <label v-for="field in fields" :key="field.key">
        {{ field.definition.title || field.key }}
        <template v-if="required.has(field.key)">*</template>
        <select
          v-if="supportsDateMode(field)"
          :value="dateMode(field)"
          :aria-label="`${field.definition.title || field.key}的取值方式`"
          @change="updateDateMode(field, $event)"
        >
          <option value="fixed">固定值</option>
          <option value="${scheduled_date}">计划当天（YYYY-MM-DD）</option>
          <option
            v-if="
              field.definition.format !== 'date' ||
              dateMode(field) === '${previous_month}'
            "
            value="${previous_month}"
            :disabled="field.definition.format === 'date'"
          >
            {{
              field.definition.format === 'date'
                ? '上一月（旧配置，需要修改）'
                : '上一月（YYYY-MM）'
            }}
          </option>
        </select>
        <select
          v-if="field.definition.enum || field.definition.type === 'boolean'"
          :value="textValue(field)"
          :required="required.has(field.key)"
          :aria-label="field.definition.title || field.key"
          @change="update(field, $event)"
        >
          <option value="" :disabled="required.has(field.key)">
            {{ required.has(field.key) ? '请选择' : '不填写' }}
          </option>
          <template v-if="field.definition.enum">
            <option
              v-for="(choice, index) in field.definition.enum"
              :key="index"
              :value="String(index)"
            >
              {{
                choice === null
                  ? '空值'
                  : choice === ''
                    ? '留空（空字符串）'
                    : choice
              }}
            </option>
          </template>
          <template v-else-if="field.definition.type === 'boolean'">
            <option value="true">是</option>
            <option value="false">否</option>
          </template>
        </select>
        <input
          v-else-if="!supportsDateMode(field) || dateMode(field) === 'fixed'"
          :value="textValue(field)"
          :type="
            ['number', 'integer'].includes(field.definition.type || '')
              ? 'number'
              : field.definition.type === 'string' &&
                  field.definition.format === 'date'
                ? 'date'
                : 'text'
          "
          :step="
            field.definition.type === 'integer'
              ? 1
              : field.definition.type === 'number'
                ? 'any'
                : undefined
          "
          :min="field.definition.minimum"
          :max="field.definition.maximum"
          :required="required.has(field.key)"
          :aria-label="field.definition.title || field.key"
          :placeholder="
            field.definition.type === 'array' ? '多个值用逗号分隔' : ''
          "
          @input="update(field, $event)"
        />
        <small
          v-else-if="
            field.definition.format === 'date' &&
            dateMode(field) === '${previous_month}'
          "
          class="feedback warning"
        >
          旧配置只生成月份，此参数要求完整日期，请改选固定值或计划当天。
        </small>
        <small v-else>按每次计划运行的日期自动填写。</small>
        <small v-if="field.definition.description">
          {{ field.definition.description }}
        </small>
        <small v-else class="mono">参数名：{{ field.key }}</small>
      </label>
    </div>
    <p v-else class="muted">这个程序没有定义需要填写的普通业务参数。</p>
    <details class="advanced-options">
      <summary>
        高级 JSON{{ hasComplexFields ? ' · 包含复杂参数' : '' }}
      </summary>
      <label>
        {{ label }}（JSON 对象）
        <textarea
          class="mono"
          rows="6"
          required
          :value="modelValue"
          @input="updateRaw"
        ></textarea>
      </label>
      <small v-if="!parsed" class="feedback warning">
        JSON 格式尚未完成，保存时会阻止提交。
      </small>
    </details>
  </section>
</template>

<style scoped>
.parameter-editor {
  display: grid;
  gap: 12px;
}
.parameter-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.parameter-heading span {
  color: var(--muted);
  font-size: 0.82rem;
}
.parameter-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
@media (max-width: 680px) {
  .parameter-grid {
    grid-template-columns: 1fr;
  }
  .parameter-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
