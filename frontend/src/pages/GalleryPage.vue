<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { call, type Drawing, type Gallery } from '../services/api'
import { useCompanion } from '../composables/useCompanion'

const { connected, notify } = useCompanion()
const gallery = ref<Gallery | null>(null)
const query = ref('')
const scope = ref('all')
const page = ref(1)
const busy = ref(false)
const error = ref('')
const selected = ref<Drawing | null>(null)
const source = ref('')
const previewBusy = ref(false)
const pendingDelete = ref<Drawing | null>(null)
let searchTimer: ReturnType<typeof setTimeout>
let serial = 0
let previewSerial = 0
let previousFocus: HTMLElement | null = null
function size(bytes: number) { return `${(bytes / 1024 / 1024).toFixed(1)} MB` }
function date(stamp: number) { return new Date(stamp * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) }
async function load() {
  if (!connected.value) return
  const request = ++serial
  busy.value = true
  try {
    const result = await call<Gallery>('get_drawings', query.value, scope.value, page.value)
    if (request === serial) { gallery.value = result; error.value = '' }
  } catch (e) { if (request === serial) error.value = (e as Error).message }
  finally { if (request === serial) busy.value = false }
}
watch([query, scope], () => { page.value = 1; clearTimeout(searchTimer); searchTimer = setTimeout(() => void load(), 250) })
async function turn(next: number) { page.value = next; await load() }
async function preview(drawing: Drawing) {
  previousFocus = document.activeElement as HTMLElement | null
  selected.value = drawing; source.value = ''; previewBusy.value = true
  await nextTick()
  document.querySelector<HTMLButtonElement>('.art-close')?.focus()
  const request = ++previewSerial
  try { const result = await call<string>('drawing_preview', drawing.id, drawing.scope); if (request === previewSerial) source.value = result }
  catch (e) { notify((e as Error).message); if (request === previewSerial) selected.value = null }
  finally { if (request === previewSerial) previewBusy.value = false }
}
function close() { ++previewSerial; selected.value = null; source.value = ''; previewBusy.value = false; previousFocus?.focus() }
function keys(event: KeyboardEvent) {
  if (pendingDelete.value || !selected.value) return
  if (event.key === 'Escape') { event.preventDefault(); close() }
  if (event.key === 'Tab') {
    const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('.art-preview button'))
    const next = (buttons.indexOf(document.activeElement as HTMLButtonElement) + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length
    event.preventDefault(); buttons[next]?.focus()
  }
}
async function save(drawing: Drawing) {
  try { if ((await call<{ saved: boolean }>('save_drawing', drawing.id, drawing.scope)).saved) notify('作品已保存到你选择的位置。') }
  catch (e) { notify((e as Error).message) }
}
async function remove() {
  const drawing = pendingDelete.value
  if (!drawing) return
  pendingDelete.value = null
  try {
    await call('delete_drawing', drawing.id, drawing.scope)
    if (selected.value?.id === drawing.id) close()
    notify('已从本地画册删除。')
    await load()
  } catch (e) { notify((e as Error).message) }
}
onMounted(() => { void load(); document.addEventListener('keydown', keys) })
onUnmounted(() => { ++serial; ++previewSerial; clearTimeout(searchTimer); document.removeEventListener('keydown', keys) })
</script>

<template>
  <div class="page-heading"><div><span class="eyebrow">LITTLE MOMENTS</span><h1>把小画，慢慢收好。</h1><p>有时是一点心情，有时只是想画。都留在这里。</p></div><button class="button quiet" :disabled="!connected || busy" @click="load"><AppIcon name="refresh" :size="16" />刷新画册</button></div>
  <div class="art-summary"><section class="card"><span class="field-caption">收好的小画</span><strong>{{ gallery?.total ?? '—' }}<small>幅</small></strong></section><section class="card"><span class="field-caption">独立创作 / 私聊作品</span><strong class="art-small">{{ gallery?.public_count ?? '—' }} <span>/</span> {{ gallery?.private_count ?? '—' }}</strong></section><section class="card"><span class="field-caption">图片占用</span><strong class="art-small">{{ size(gallery?.bytes || 0) }}</strong></section></div>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <div class="art-toolbar"><div class="art-filters" aria-label="作品分类"><button v-for="item in [{ id: 'all', label: '全部作品' }, { id: 'public', label: '独立创作' }, { id: 'private', label: '私聊作品' }]" :key="item.id" :class="{ active: scope === item.id }" @click="scope = item.id">{{ item.label }}</button></div><input v-model="query" class="art-search" aria-label="搜索作品" placeholder="搜索作品名或配文" /></div>
  <div v-if="gallery && !gallery.drawings.length" class="card art-empty"><AppIcon name="gallery" :size="38" /><h2>{{ query ? '还没有找到这幅画' : '第一幅小画，也会有的。' }}</h2><p>{{ query ? '换个关键词或分类试试。' : '告诉她想画什么，画完保存后就会出现在这里。' }}</p></div>
  <div v-else class="art-grid" :aria-busy="busy">
    <article v-for="drawing in gallery?.drawings || []" :key="drawing.scope + drawing.id" class="card art-card">
      <button class="art-cover" :aria-label="`查看《${drawing.title}》`" @click="preview(drawing)"><img :src="drawing.thumbnail" :alt="drawing.title" loading="lazy" /></button>
      <div class="art-info"><div class="art-meta"><span>{{ drawing.scope === 'public' ? '独立创作' : '私聊作品' }}</span><time>{{ date(drawing.created) }}</time></div><h2>{{ drawing.title }}</h2><p>{{ drawing.caption }}</p><div class="art-actions"><button class="text-button" @click="preview(drawing)">查看原图</button><button class="text-button" @click="save(drawing)">保存图片</button><button class="text-button danger" @click="pendingDelete = drawing">删除</button></div></div>
    </article>
  </div>
  <div v-if="gallery && gallery.pages > 1" class="art-pagination"><button class="button quiet" :disabled="page <= 1 || busy" @click="turn(page - 1)">上一页</button><span>{{ gallery.page }} / {{ gallery.pages }}</span><button class="button quiet" :disabled="page >= gallery.pages || busy" @click="turn(page + 1)">下一页</button></div>
  <p class="tiny muted art-note">私聊作品保留原来的使用范围；在这里查看和保存，不会发送或发布。</p>
  <div v-if="selected" class="art-backdrop" @click.self="close"><section class="art-preview" role="dialog" aria-modal="true" aria-label="作品预览"><button class="icon-button art-close" aria-label="关闭原图" autofocus @click="close"><AppIcon name="close" /></button><p v-if="previewBusy" class="muted">正在打开原图…</p><img v-if="source" :src="source" :alt="selected.title" /><div><h2>{{ selected.title }}</h2><p>{{ selected.caption }}</p><button class="button quiet" @click="save(selected)">保存图片</button></div></section></div>
  <ConfirmDialog v-if="pendingDelete" title="从画册删除这幅画？" :detail="`《${pendingDelete.title}》的本地图片和创作稿会删除，已经发出的 QQ 消息不受影响。`" confirm-label="删除作品" @cancel="pendingDelete = null" @confirm="remove" />
</template>

<style scoped>
.art-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-bottom:26px}.art-summary .card{padding:22px 26px}.art-summary strong{display:block;font-size:34px;font-weight:600;margin-top:12px;letter-spacing:-1px}.art-summary small{font-size:14px;color:var(--muted);font-weight:400;margin-left:8px}.art-summary .art-small{font-size:28px;line-height:41px}.art-small span{color:#b2c0d1;margin:0 8px;font-weight:300}.art-toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:20px}.art-filters{display:flex;gap:6px}.art-filters button{border:0;background:transparent;padding:9px 14px;border-radius:9px;color:var(--muted);font:inherit;font-size:13px;cursor:pointer}.art-filters button.active{background:#e4edff;color:#496ddb}.art-search{width:240px}.art-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.art-card{padding:0;overflow:hidden}.art-cover{display:block;width:100%;border:0;padding:0;background:#f7f6f0;cursor:zoom-in;aspect-ratio:3/2}.art-cover img{width:100%;height:100%;object-fit:contain;display:block}.art-info{padding:20px 22px}.art-meta{display:flex;gap:8px;justify-content:space-between;font-size:10px;color:var(--muted)}.art-meta span{color:#6a83b1}.art-info h2{font-size:17px;margin:13px 0 10px}.art-info p{color:var(--muted);font-size:13px;line-height:1.8;margin:0;min-height:47px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.art-actions{display:flex;gap:18px;margin-top:18px;padding-top:15px;border-top:1px solid var(--line)}.art-actions .text-button{font-size:12px}.art-actions .danger{margin-left:auto}.art-pagination{display:flex;align-items:center;justify-content:center;gap:20px;margin:24px 0;color:var(--muted);font-size:13px}.art-note{margin-top:22px;line-height:1.8}.art-empty{text-align:center;padding:65px 20px;color:var(--muted)}.art-empty h2{font-size:18px;color:#294064;margin-top:22px}.art-empty p{font-size:13px}.art-backdrop{position:fixed;inset:0;z-index:50;background:rgba(24,40,66,.5);display:flex;align-items:center;justify-content:center;padding:28px}.art-preview{position:relative;background:#fff;border-radius:18px;overflow:auto;max-width:1000px;max-height:90vh;width:100%;box-shadow:0 24px 100px #23344d40}.art-preview>img{display:block;width:100%;max-height:66vh;object-fit:contain;background:#f7f6f0}.art-preview>div{padding:20px 28px 26px}.art-preview h2{margin:0 0 10px}.art-preview p{font-size:14px;line-height:1.8;color:var(--muted)}.art-close{position:absolute;right:12px;top:12px;background:#fff;border-radius:50%;z-index:1}@media(min-width:1450px){.art-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:1050px){.art-toolbar{align-items:flex-start;flex-direction:column}.art-search{width:100%;box-sizing:border-box}.art-summary .card{padding:20px}.art-grid{gap:16px}.art-info{padding:16px}.art-meta{flex-direction:column;gap:5px}}
</style>
