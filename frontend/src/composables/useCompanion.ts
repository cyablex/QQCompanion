import { ref } from 'vue'
import { call, nativeReady, type Status } from '../services/api'

const status = ref<Status | null>(null)
const connected = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
let noticeTimer: ReturnType<typeof setTimeout>

export function useCompanion() {
  async function refresh() {
    try { status.value = await call<Status>('get_status'); error.value = '' }
    catch (e) { error.value = (e as Error).message; status.value = null }
  }
  function notify(message: string) {
    notice.value = message; clearTimeout(noticeTimer)
    noticeTimer = setTimeout(() => notice.value = '', 6500)
  }
  async function run<T>(method: string, args: unknown[], message: string): Promise<T | undefined> {
    if (busy.value) return
    busy.value = true; error.value = ''
    try {
      const result = await call<T>(method, ...args)
      await refresh(); if (message) notify(message)
      return result
    } catch (e) { error.value = (e as Error).message }
    finally { busy.value = false }
  }
  async function connect() {
    connected.value = await nativeReady()
    if (connected.value) await refresh()
    else error.value = '当前为界面预览。请从桌面“大肥鱼控制台”打开以使用本机功能。'
  }
  return { status, connected, busy, error, notice, refresh, notify, run, connect }
}
