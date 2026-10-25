<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { call, type Status } from '../services/api'

const props = defineProps<{ startup: boolean; ready: boolean; starting: boolean; error: string; status: Status | null }>()
const emit = defineEmits<{ done: [welcome: boolean]; retry: []; settings: []; login: [] }>()
const frame = ref<HTMLIFrameElement | null>(null)
const previousFocus = document.activeElement as HTMLElement | null
const atGate = ref(false)
const failed = ref(false)
let frameReady = false
let loadingRequested = !!props.error || window.matchMedia('(prefers-reduced-motion: reduce)').matches
const phase = computed(() => props.status?.phase || '')
const qr = ref<string | null>(null)
const qrUnavailable = ref(false)
const needsQr = computed(() => props.startup && atGate.value && phase.value === 'waiting_login' && !props.status?.login_scanned)
watch([() => props.status, atGate], async (_value, _old, onCleanup) => {
  let current = true
  onCleanup(() => { current = false })
  if (!needsQr.value || !props.status?.qr_available) {
    qr.value = null; qrUnavailable.value = false
    return
  }
  try {
    const image = await call<string | null>('get_qr')
    if (current) { qr.value = image; qrUnavailable.value = !image }
  } catch {
    if (current) { qr.value = null; qrUnavailable.value = true }
  }
}, { immediate: true })
const hint = computed(() => {
  if (props.error) return props.error
  if (phase.value === 'waiting_login') {
    if (props.status?.login_scanned) return '请在手机 QQ 确认登录，完成本次访问授权。'
    if (qr.value) return '请使用手机 QQ 扫码，在手机端确认本次登录。'
    return qrUnavailable.value ? '暂未取得登录码，正在重新读取；也可打开登录管理。' : '正在获取登录凭证，请稍候。'
  }
  if (phase.value === 'wrong_account') return '请在登录管理中确认使用大肥鱼的小号。'
  if (phase.value === 'offline_development') return '当前处于离线开发模式，请检查在线设置。'
  if (['recovery_failed', 'session_unavailable'].includes(phase.value)) return '请检查手机验证或登录状态，再重新检查连接。'
  if (props.status?.connected && !props.ready && ['starting', 'warming'].includes(props.status?.voice?.state || '')) return 'QQ 已连接，正在准备声音，请稍候。'
  if (props.status?.voice?.state === 'unavailable') return props.status.voice.message
  return props.ready ? '会话已就绪，正在进入。' : '正在同步后台与聊天通道，请稍候。'
})
function sendState() {
  if (!frameReady) return
  frame.value?.contentWindow?.postMessage({ type: 'dafeiyu-startup:state',
    ready: !props.startup || props.ready, starting: props.starting, error: props.error,
    phase: phase.value, scanned: !!props.status?.login_scanned, hint: hint.value,
    qr: needsQr.value ? qr.value : null,
    login: ['waiting_login', 'wrong_account', 'recovery_failed', 'session_unavailable'].includes(phase.value)
  }, '*')
}
function skip() {
  if (!props.startup) { emit('done', false); return }
  loadingRequested = true
  if (frameReady) frame.value?.contentWindow?.postMessage({ type: 'dafeiyu-startup:load' }, '*')
}
function loaded() { frameReady = true; sendState(); if (loadingRequested) skip() }
function message(event: MessageEvent) {
  if (event.source !== frame.value?.contentWindow) return
  const type = event.data?.type
  if (type === 'dafeiyu-startup:done') {
    if (!props.startup || props.ready) emit('done', true)
    else failed.value = true
  } else if (type === 'dafeiyu-startup:waiting') { atGate.value = true; sendState() }
  else if (type === 'dafeiyu-startup:skip') skip()
  else if (type === 'dafeiyu-startup:failed') failed.value = true
  else if (type === 'dafeiyu-startup:qr-error') { qr.value = null; qrUnavailable.value = true }
  else if (type === 'dafeiyu-startup:action' && ['retry', 'settings', 'login'].includes(event.data.action)) {
    if (event.data.action === 'retry') emit('retry')
    else if (event.data.action === 'settings') emit('settings')
    else emit('login')
  }
}
function key(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); skip() }
  else if (['Enter', ' '].includes(event.key) && !atGate.value) {
    if (event.target instanceof HTMLElement && event.target.closest('button')) return
    event.preventDefault()
    frame.value?.contentWindow?.postMessage({ type: 'dafeiyu-startup:key', key: event.key }, '*')
  }
}
watch([() => props.status, () => props.ready, () => props.starting, () => props.error, qr, qrUnavailable], sendState, { flush: 'post' })
watch(() => props.error, value => { if (value) skip() })
watch([failed, () => props.ready], () => { if (failed.value && (!props.startup || props.ready)) emit('done', false) })
onMounted(() => {
  window.addEventListener('message', message)
  window.addEventListener('keydown', key)
  frame.value?.focus()
})
onUnmounted(() => {
  window.removeEventListener('message', message)
  window.removeEventListener('keydown', key)
  previousFocus?.focus()
})
</script>
<template>
  <section class="startup-overlay" role="dialog" aria-modal="true" aria-label="大肥鱼开场与启动加载">
    <iframe v-show="!failed" ref="frame" class="startup-frame" :src="`./startup/index.html?gate=${startup ? '1' : '0'}`" sandbox="allow-scripts" title="大肥鱼开场与启动加载" @load="loaded" @error="failed = true" />
    <div v-if="failed" class="startup-fallback" role="status"><p>动画暂不可用，仍在检查连接。</p><p>{{ hint }}</p><div><button class="text-button" @click="emit('retry')">重新检查</button><button class="text-button" @click="emit('login')">打开登录页</button><button class="text-button" @click="emit('settings')">检查设置</button></div></div>
    <button v-if="!atGate && !failed" class="startup-skip" type="button" aria-label="跳到启动加载" aria-keyshortcuts="Escape" title="跳到启动加载 · Esc" @click="skip"><span>skip</span><svg viewBox="0 0 8 10" aria-hidden="true"><path d="M1 1 7 5 1 9Z" /></svg></button>
  </section>
</template>
<style scoped>
.startup-fallback { height:100%; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:20px; background:#e5e3df; color:#3a3936; }
.startup-fallback > div { display:flex; gap:24px; }
</style>
