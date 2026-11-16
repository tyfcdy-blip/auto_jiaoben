import { enableAutoUnmount } from '@vue/test-utils'
import { afterEach } from 'vitest'
enableAutoUnmount(afterEach)
export function cleanup() {
  document.body.innerHTML = ''
  localStorage.clear()
  sessionStorage.clear()
}
