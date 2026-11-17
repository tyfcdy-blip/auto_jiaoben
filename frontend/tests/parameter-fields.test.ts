import { defineComponent, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ParameterFields from '../src/components/ParameterFields.vue'

const schema = {
  type: 'object',
  required: ['period', 'count'],
  additionalProperties: false,
  properties: {
    period: { type: 'string', title: '账期', default: '2026-09' },
    count: { type: 'integer', title: '数量' },
    dry_run: { type: 'boolean', title: '试运行' },
    level: { type: 'integer', title: '等级', enum: [1, 2] },
    only_true: { type: 'boolean', title: '仅允许开启', enum: [true] },
    mixed: { title: '混合枚举', enum: [1, '1'] },
    shops: { type: 'array', title: '店铺', items: { type: 'string' } },
    input_dir: { type: 'string', title: '普通同名参数' },
    nested: { type: 'object', title: '复杂配置' },
  },
}

describe('普通参数表单', () => {
  it('空字符串选项有清晰标签且保持真实参数类型', async () => {
    const wrapper = mount(ParameterFields, {
      props: {
        modelValue: '{}',
        schema: {
          type: 'object',
          properties: {
            url: { type: 'string', enum: ['', 'https://example.com/'] },
          },
        },
      },
    })
    expect(wrapper.text()).toContain('留空（空字符串）')
    await wrapper.get('select').setValue('0')
    expect(
      JSON.parse(String(wrapper.emitted('update:modelValue')?.at(-1)?.[0])),
    ).toEqual({ url: '' })
  })
  it('应用默认值并保持数字、布尔、枚举和数组的 JSON 类型', async () => {
    const Host = defineComponent({
      components: { ParameterFields },
      setup() {
        const value = ref('{}')
        return { value, schema }
      },
      template: '<ParameterFields v-model="value" :schema="schema" />',
    })
    const wrapper = mount(Host)
    await wrapper.vm.$nextTick()
    expect(
      JSON.parse((wrapper.vm as unknown as { value: string }).value),
    ).toEqual({ period: '2026-09' })
    await wrapper.get('[aria-label="数量"]').setValue('4')
    await wrapper.get('[aria-label="试运行"]').setValue('false')
    await wrapper.get('[aria-label="等级"]').setValue('1')
    await wrapper.get('[aria-label="仅允许开启"]').setValue('0')
    await wrapper.get('[aria-label="混合枚举"]').setValue('1')
    await wrapper.get('[aria-label="店铺"]').setValue('A, B')
    await wrapper.get('[aria-label="普通同名参数"]').setValue('business-value')
    expect(
      JSON.parse((wrapper.vm as unknown as { value: string }).value),
    ).toEqual({
      period: '2026-09',
      count: 4,
      dry_run: false,
      level: 2,
      only_true: true,
      mixed: '1',
      shops: ['A', 'B'],
      input_dir: 'business-value',
    })
    expect(
      wrapper.get('[aria-label="仅允许开启"]').findAll('option'),
    ).toHaveLength(2)
    expect(wrapper.text()).toContain('高级 JSON · 包含复杂参数')
  })

  it('高级 JSON 可以保留嵌套对象', async () => {
    const wrapper = mount(ParameterFields, {
      props: { modelValue: '{}', schema },
    })
    await wrapper.get('textarea').setValue('{"nested":{"mode":"safe"}}')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toBe(
      '{"nested":{"mode":"safe"}}',
    )
  })
})

describe('日期取值方式', () => {
  const dateSchema = {
    type: 'object',
    properties: {
      report_date: { type: 'string', title: '报表日期', format: 'date' },
      period: { type: 'string', title: '账期' },
    },
  }

  function mountForm(
    modelValue = '{}',
    fieldSchema: Record<string, unknown> = dateSchema,
    scheduled = true,
  ) {
    return mount(
      defineComponent({
        components: { ParameterFields },
        setup() {
          return { value: ref(modelValue), fieldSchema, scheduled }
        },
        template:
          '<ParameterFields v-model="value" :schema="fieldSchema" :scheduled="scheduled" />',
      }),
    )
  }

  it('定时表单切换日期变量写入完整标记，并清除切回固定值后的旧标记', async () => {
    const wrapper = mountForm(
      '{"report_date":"2026-11-17","period":"固定账期"}',
    )
    const mode = wrapper.get('[aria-label="报表日期的取值方式"]')
    expect(wrapper.get('[aria-label="报表日期"]').attributes('type')).toBe(
      'date',
    )
    await mode.setValue('${scheduled_date}')
    expect(JSON.parse(wrapper.vm.value)).toEqual({
      report_date: '${scheduled_date}',
      period: '固定账期',
    })
    expect(wrapper.find('[aria-label="报表日期"]').exists()).toBe(false)
    expect(mode.find('option[value="${previous_month}"]').exists()).toBe(false)
    await mode.setValue('fixed')
    expect(JSON.parse(wrapper.vm.value).report_date).toBe('')
    await wrapper.get('[aria-label="报表日期"]').setValue('2026-12-01')
    expect(JSON.parse(wrapper.vm.value).report_date).toBe('2026-12-01')
  })

  it('普通字符串可选上一月，完整日期的旧月份配置保留回显并提示修正', async () => {
    const wrapper = mountForm('{"report_date":"${previous_month}"}')
    const dateMode = wrapper.get('[aria-label="报表日期的取值方式"]')
    expect((dateMode.element as HTMLSelectElement).value).toBe(
      '${previous_month}',
    )
    expect(
      dateMode.get('option[value="${previous_month}"]').attributes('disabled'),
    ).toBeDefined()
    expect(wrapper.text()).toContain('此参数要求完整日期')
    expect(JSON.parse(wrapper.vm.value).report_date).toBe('${previous_month}')
    await wrapper
      .get('[aria-label="账期的取值方式"]')
      .setValue('${previous_month}')
    expect(JSON.parse(wrapper.vm.value).period).toBe('${previous_month}')
    await dateMode.setValue('${scheduled_date}')
    expect(JSON.parse(wrapper.vm.value).report_date).toBe('${scheduled_date}')
    expect(dateMode.find('option[value="${previous_month}"]').exists()).toBe(
      false,
    )
    expect(wrapper.text()).not.toContain('此参数要求完整日期')
  })

  it.each(['${scheduled_date}', '${previous_month}'])(
    '已有变量 %s 回显为对应选项',
    (token) => {
      const wrapper = mountForm(JSON.stringify({ period: token }))
      expect(
        (
          wrapper.get('[aria-label="账期的取值方式"]')
            .element as HTMLSelectElement
        ).value,
      ).toBe(token)
      expect(wrapper.find('[aria-label="账期"]').exists()).toBe(false)
    },
  )

  it('默认变量可回显，切回固定值后不被默认值重新覆盖', async () => {
    const wrapper = mountForm('{}', {
      properties: {
        period: { type: 'string', title: '账期', default: '${previous_month}' },
      },
    })
    await wrapper.vm.$nextTick()
    expect(JSON.parse(wrapper.vm.value)).toEqual({
      period: '${previous_month}',
    })
    await wrapper.get('[aria-label="账期的取值方式"]').setValue('fixed')
    await wrapper.vm.$nextTick()
    expect(JSON.parse(wrapper.vm.value)).toEqual({ period: '' })
    expect(wrapper.get('[aria-label="账期"]').attributes('type')).toBe('text')
  })

  it('手动表单只展示固定日期，普通字符串不根据名称推测日期', async () => {
    const wrapper = mountForm('{"report_date":"2026-11-17"}', dateSchema, false)
    expect(wrapper.find('[aria-label="报表日期的取值方式"]').exists()).toBe(
      false,
    )
    expect(wrapper.get('[aria-label="报表日期"]').attributes('type')).toBe(
      'date',
    )
    expect(wrapper.get('[aria-label="账期"]').attributes('type')).toBe('text')
    expect(wrapper.text()).not.toContain('计划当天')
    await wrapper.get('[aria-label="报表日期"]').setValue('2026-12-02')
    expect(JSON.parse(wrapper.vm.value).report_date).toBe('2026-12-02')
  })

  it('定时模式保留枚举、数字、布尔和文件字段行为', async () => {
    const wrapper = mountForm('{}', {
      properties: {
        category: { type: 'string', enum: ['A', 'B'] },
        count: { type: 'integer' },
        enabled: { type: 'boolean' },
        file: { type: 'string', 'x-workbench-file': 'file' },
      },
    })
    expect(wrapper.findAll('[aria-label$="的取值方式"]')).toHaveLength(0)
    await wrapper.get('[aria-label="category"]').setValue('1')
    await wrapper.get('[aria-label="count"]').setValue('3')
    await wrapper.get('[aria-label="enabled"]').setValue('false')
    await wrapper.get('[aria-label="file"]').setValue('C:\\报表.xlsx')
    expect(JSON.parse(wrapper.vm.value)).toEqual({
      category: 'B',
      count: 3,
      enabled: false,
      file: 'C:\\报表.xlsx',
    })
  })

  it('保留省略字段和目录输入字段规则，原始 JSON 仍可编辑', async () => {
    const wrapper = mount(ParameterFields, {
      props: {
        modelValue: '{}',
        scheduled: true,
        omitKeys: ['period'],
        schema: { ...schema, 'x-workbench-input': 'directory' },
      },
    })
    expect(wrapper.find('[aria-label="账期的取值方式"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="普通同名参数的取值方式"]').exists()).toBe(
      false,
    )
    await wrapper.get('textarea').setValue('{"nested":{"value":1}}')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toBe(
      '{"nested":{"value":1}}',
    )
  })
})
