<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
defineProps<{ title: string; detail: string; confirmLabel: string }>()
const emit = defineEmits<{ cancel: []; confirm: [] }>()
const cancel = ref<HTMLButtonElement>()
const previous = document.activeElement as HTMLElement | null
function keys(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('cancel')
  if (e.key === 'Tab') {
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('.dialog button'))
    const next = (buttons.indexOf(document.activeElement as HTMLButtonElement) + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length
    e.preventDefault(); buttons[next]?.focus()
  }
}
onMounted(() => { cancel.value?.focus(); document.addEventListener('keydown', keys) })
onUnmounted(() => { document.removeEventListener('keydown', keys); previous?.focus() })
</script>
<template><div class="dialog-backdrop" @click.self="emit('cancel')"><section class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title"><span class="eyebrow">运行管理</span><h2 id="dialog-title">{{ title }}</h2><p>{{ detail }}</p><div class="dialog-actions"><button ref="cancel" class="button secondary" @click="emit('cancel')">取消</button><button class="button primary" @click="emit('confirm')">{{ confirmLabel }}</button></div></section></div></template>
