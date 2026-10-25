<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { call } from '../services/api'
import { useCompanion } from '../composables/useCompanion'

const { connected, error } = useCompanion()
const maximized = ref(false)
let timer: ReturnType<typeof setTimeout>
let pendingSize: [number, number] | null = null
let sendingSize = false
async function action(name: string) {
  if (!connected.value) return
  try { const state = await call<{ maximized: boolean }>('window_action', name); maximized.value = !!state.maximized }
  catch (e) { error.value = (e as Error).message }
}
function onResize() {
  clearTimeout(timer)
  timer = setTimeout(() => { if (connected.value) void action('state') }, 120)
}
async function flushSize() {
  if (sendingSize) return
  sendingSize = true
  try {
    while (pendingSize) { const size = pendingSize; pendingSize = null; await call('resize_window', ...size) }
  } catch (e) { error.value = (e as Error).message; pendingSize = null }
  finally { sendingSize = false }
}
function beginResize(event: PointerEvent) {
  if (!connected.value || maximized.value || event.button !== 0) return
  const handle = event.currentTarget as HTMLElement
  const start = { x: event.screenX, y: event.screenY, width: window.innerWidth, height: window.innerHeight }
  handle.setPointerCapture(event.pointerId)
  const move = (e: PointerEvent) => {
    pendingSize = [start.width + e.screenX - start.x, start.height + e.screenY - start.y]
    void flushSize()
  }
  const end = () => {
    handle.removeEventListener('pointermove', move)
    handle.removeEventListener('pointerup', end)
    handle.removeEventListener('pointercancel', end)
  }
  handle.addEventListener('pointermove', move)
  handle.addEventListener('pointerup', end)
  handle.addEventListener('pointercancel', end)
}
onMounted(() => window.addEventListener('resize', onResize))
onUnmounted(() => { window.removeEventListener('resize', onResize); clearTimeout(timer) })
</script>
<template>
  <header class="window-titlebar">
    <div class="window-drag-area pywebview-drag-region" @dblclick="action('maximize')"><span class="pywebview-drag-region">大肥鱼控制台</span></div>
    <div class="window-controls">
      <button aria-label="最小化窗口" title="最小化" :disabled="!connected" @click="action('minimize')"><AppIcon name="minimize" :size="15" /></button>
      <button :aria-label="maximized ? '还原窗口' : '最大化窗口'" :title="maximized ? '还原' : '最大化'" :disabled="!connected" @click="action('maximize')"><AppIcon :name="maximized ? 'restore' : 'maximize'" :size="14" /></button>
      <button class="window-close" aria-label="关闭控制台窗口" title="关闭窗口，后台继续运行" :disabled="!connected" @click="action('close')"><AppIcon name="close" :size="17" /></button>
    </div>
  </header>
  <div v-if="!maximized" class="resize-grip" aria-hidden="true" title="拖动调整窗口大小" @pointerdown.prevent="beginResize" />
</template>
