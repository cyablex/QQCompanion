<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { call } from '../services/api'
const props = defineProps<{ theme: string; label?: string; override?: string }>()
const source = ref('')
const cache = new Map<string, string>()
let serial = 0
async function load() {
  const request = ++serial, theme = props.theme
  source.value = cache.get(theme) || ''
  if (cache.has(theme)) return
  try { const value = await call<string>('diary_illustration', theme); cache.set(theme, value); if (request === serial) source.value = value } catch { source.value = '' }
}
watch(() => props.theme, () => void load())
onMounted(() => void load())
</script>
<template>
  <div class="diary-art" :class="`art-${theme}`">
    <img v-if="override || source" :src="override || source" :alt="label || '大肥鱼的日记插画'" />
    <svg v-else viewBox="0 0 520 260" role="img" :aria-label="label || '蓝色鲸鱼纹样'">
      <path d="M45 213q55-34 110 0t110 0 110 0 100 0" fill="none" stroke="#bdd4e0" stroke-width="3" />
      <path d="M103 160c-6-49 44-85 100-76 44 7 67 50 108 61 34 9 46 1 59-18-19-8-24-27-13-44 14 1 30 15 31 27 9-16 26-23 40-17-1 23-17 34-33 38-4 41-48 74-109 74-76 0-172-4-183-45Z" fill="#759fc0" />
      <path d="M106 160c63 26 124 36 204 27-82 34-171 11-204-27Z" fill="#e6f0ef" />
      <circle cx="153" cy="137" r="5" fill="#304f6e" /><path d="M162 155q12 8 22-1M226 170q8 26 33 17" fill="none" stroke="#426989" stroke-width="3" stroke-linecap="round" />
      <path d="m92 70 4-9 4 9 9 4-9 4-4 9-4-9-9-4Zm320 113 3-7 3 7 7 3-7 3-3 7-3-7-7-3Z" fill="#c4a369" />
    </svg>
  </div>
</template>
<style scoped>
.diary-art{position:relative;overflow:hidden}.diary-art img,.diary-art svg{display:block;width:100%;height:100%;object-fit:contain}.art-cover img{object-fit:cover;object-position:center top}
</style>
