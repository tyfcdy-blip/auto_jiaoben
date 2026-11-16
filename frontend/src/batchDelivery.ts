import { computed, onScopeDispose, ref, watch } from 'vue'
import { ApiError, errorMessage, request } from './api'
import { canOperate, session } from './session'
import type { Batch } from './types'

type BatchFile = NonNullable<Batch['files']>[number]
type DeliveryFile = {
  file: File
  state: 'pending' | 'uploading' | 'uploaded' | 'failed'
}

// 只使用现有独立接口；失败后保留原批次和文件，不把部分上传当作齐套。
export function useBatchDelivery(
  updated: (batch: Batch, created: boolean) => void,
) {
  const opened = ref(false),
    pending = ref(false),
    label = ref(''),
    batch = ref<Batch | null>(null),
    files = ref<DeliveryFile[]>([]),
    error = ref(''),
    success = ref(''),
    stage = ref(''),
    detailLoaded = ref(false),
    creationUncertain = ref(false)
  let generation = 0
  let controller: AbortController | undefined
  const scope = () => `${session.user?.id || ''}:${session.projectId}`
  const submittedCount = computed(
    () => files.value.filter((entry) => entry.state === 'uploaded').length,
  )
  function reset() {
    generation++
    controller?.abort()
    opened.value = false
    pending.value = false
    label.value = ''
    batch.value = null
    files.value = []
    error.value = ''
    success.value = ''
    stage.value = ''
    detailLoaded.value = false
    creationUncertain.value = false
  }
  watch(scope, reset, { flush: 'sync' })
  onScopeDispose(reset)
  function operation() {
    const current = ++generation
    controller?.abort()
    controller = new AbortController()
    pending.value = true
    error.value = ''
    success.value = ''
    return { current, signal: controller.signal }
  }
  function current(id: number) {
    return id === generation
  }
  function accept(value: Batch, created = false) {
    batch.value = value
    updated(value, created)
  }
  async function detail(id: string, signal: AbortSignal) {
    const value = await request<Batch>(`/batches/${id}`, { signal })
    if (
      value.id !== id ||
      !Array.isArray(value.files) ||
      typeof value.ready !== 'boolean'
    )
      throw new Error('文件清单未完整返回，请重新读取后继续。')
    return value
  }
  async function reload() {
    if (pending.value || !batch.value) return
    const id = batch.value.id,
      run = operation()
    stage.value = '正在读取文件清单'
    try {
      const value = await detail(id, run.signal)
      if (!current(run.current)) return
      accept(value)
      detailLoaded.value = true
    } catch (failure) {
      if (current(run.current)) error.value = errorMessage(failure)
    } finally {
      if (current(run.current)) {
        pending.value = false
        stage.value = ''
      }
    }
  }
  async function open(value: Batch | null = null) {
    if (pending.value) return
    reset()
    opened.value = true
    batch.value = value
    label.value = value?.label || ''
    if (value) await reload()
    else detailLoaded.value = true
  }
  function close() {
    if (!pending.value) reset()
  }
  function choose(chosen: File[]) {
    if (pending.value || batch.value?.ready || !canOperate.value) return
    error.value = ''
    success.value = ''
    const names = new Set(files.value.map((entry) => entry.file.name))
    for (const file of chosen) {
      if (!file.name || file.name.length > 180 || /[\\/]/.test(file.name)) {
        error.value = '文件名过长或包含路径，请修改文件名后选择。'
        return
      }
      if (names.has(file.name)) {
        error.value = `“${file.name}”已在本次清单中，请不要重复选择。`
        return
      }
      names.add(file.name)
    }
    files.value.push(
      ...chosen.map((file) => ({ file, state: 'pending' as const })),
    )
  }
  function remove(index: number) {
    // 已发出的上传可能已被接收，不能通过删除本地行伪装撤回文件。
    if (!pending.value && files.value[index]?.state === 'pending')
      files.value.splice(index, 1)
  }
  async function submit() {
    if (
      pending.value ||
      creationUncertain.value ||
      !canOperate.value ||
      !session.projectId
    )
      return
    if (!detailLoaded.value) {
      error.value = '请先重新读取文件清单。'
      return
    }
    if (!label.value.trim() || label.value.trim().length > 100) {
      error.value = '请填写 1 至 100 字的文件批次名称。'
      return
    }
    if (!files.value.length && !batch.value?.files?.length) {
      error.value = '请至少选择一个文件。'
      return
    }
    if (batch.value?.ready) return
    const projectId = session.projectId,
      run = operation()
    try {
      if (!batch.value) {
        stage.value = '正在创建文件批次'
        let created: Batch
        try {
          created = await request<Batch>('/batches', {
            method: 'POST',
            body: { project_id: projectId, label: label.value.trim() },
            signal: run.signal,
          })
          if (
            typeof created?.id !== 'string' ||
            !/^[a-zA-Z0-9_-]{1,100}$/.test(created.id)
          )
            throw new Error('创建响应不完整')
        } catch (failure) {
          if (
            current(run.current) &&
            !(
              failure instanceof ApiError &&
              failure.status >= 400 &&
              failure.status < 500
            )
          ) {
            creationUncertain.value = true
            throw new Error(
              '创建结果尚未确认。请关闭窗口并刷新列表，找到刚创建的文件批次后继续交付；不会自动重复创建。',
            )
          }
          throw failure
        }
        if (!current(run.current)) return
        accept({ ...created, files: [] }, true)
      } else {
        stage.value = '正在核对已交付文件'
        const value = await detail(batch.value.id, run.signal)
        if (!current(run.current)) return
        accept(value)
        if (value.ready) {
          if (files.value.some((entry) => entry.state !== 'uploaded'))
            throw new Error(
              '批次已由其他操作确认就绪，本次待上传文件未继续交付。请核对清单，缺少的文件需要新建批次。',
            )
          success.value = '文件已提交，可以用于执行任务。'
          return
        }
      }
      const id = batch.value!.id
      for (const entry of files.value) {
        if (entry.state === 'uploaded') continue
        stage.value = `正在上传 ${entry.file.name}`
        entry.state = 'uploading'
        try {
          const uploaded = await request<BatchFile>(
            `/batches/${id}/files/${encodeURIComponent(entry.file.name)}`,
            {
              method: 'PUT',
              body: entry.file,
              signal: run.signal,
            },
          )
          if (!current(run.current)) return
          entry.state = 'uploaded'
          accept({
            ...batch.value!,
            files: [
              ...(batch.value!.files || []).filter(
                (file) => file.name !== uploaded.name,
              ),
              uploaded,
            ],
          })
        } catch (failure) {
          if (current(run.current)) entry.state = 'failed'
          throw failure
        }
      }
      if (!current(run.current)) return
      if (files.value.some((entry) => entry.state !== 'uploaded'))
        throw new Error('还有文件未上传完成。')
      stage.value = '正在确认文件齐套'
      try {
        const ready = await request<Batch>(`/batches/${id}/ready`, {
          method: 'POST',
          signal: run.signal,
        })
        if (!current(run.current)) return
        if (!ready.ready) throw new Error('服务端尚未确认文件就绪。')
        accept({ ...batch.value!, ...ready, files: batch.value!.files })
      } catch (failure) {
        if (!current(run.current)) return
        // 响应丢失不等于操作失败；只有详情明确 ready 才能恢复为成功。
        const observed = await detail(id, run.signal)
        if (!current(run.current)) return
        accept(observed)
        if (!observed.ready) throw failure
      }
      if (current(run.current)) success.value = '文件已提交，可以用于执行任务。'
    } catch (failure) {
      if (current(run.current)) error.value = errorMessage(failure)
    } finally {
      if (current(run.current)) {
        pending.value = false
        stage.value = ''
      }
    }
  }
  return {
    opened,
    pending,
    label,
    batch,
    files,
    error,
    success,
    stage,
    detailLoaded,
    creationUncertain,
    submittedCount,
    open,
    close,
    choose,
    remove,
    reload,
    submit,
  }
}
