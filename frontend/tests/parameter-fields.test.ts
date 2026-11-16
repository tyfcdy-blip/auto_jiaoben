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
