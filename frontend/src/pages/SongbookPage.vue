<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, type ComponentPublicInstance } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { call, type Song } from '../services/api'
import { useCompanion } from '../composables/useCompanion'

const { connected, notify } = useCompanion()
const songs = ref<Song[]>([])
const query = ref('')
const loading = ref(false)
const error = ref('')
const deleting = ref(false)
const pendingDelete = ref<Song | null>(null)
const previewing = ref('')
const playing = ref('')
const preview = ref('')
const audio = ref<HTMLAudioElement>()
function setAudio(el: Element | ComponentPublicInstance | null) { audio.value = el instanceof HTMLAudioElement ? el : undefined }
let timer: ReturnType<typeof setInterval>
const ready = computed(() => songs.value.filter(s => s.state === 'ready'))
const preparing = computed(() => songs.value.filter(s => ['queued', 'processing'].includes(s.state)))
const totalBytes = computed(() => ready.value.reduce((sum, s) => sum + s.bytes + (s.master_bytes || 0), 0))
const visible = computed(() => songs.value.filter(s => `${s.title} ${s.artist}`.toLowerCase().includes(query.value.trim().toLowerCase())))
function size(bytes: number) { return bytes ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : '—' }
function duration(seconds: number) { return seconds ? `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}` : '—' }
function state(song: Song) { return song.state === 'ready' ? '已收录' : song.state === 'failed' ? '未完成' : song.progress || '等待准备' }
async function load() {
  if (!connected.value || loading.value) return
  loading.value = true
  try { songs.value = (await call<{ songs: Song[] }>('get_songs')).songs; error.value = '' }
  catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
async function listen(song: Song) {
  previewing.value = song.id
  try {
    audio.value?.pause()
    const source = await call<string>('song_preview', song.id)
    preview.value = source; playing.value = song.id
    await nextTick()
    await audio.value?.play().catch(() => {})
  } catch (e) { notify((e as Error).message) }
  finally { previewing.value = '' }
}
async function remove() {
  const song = pendingDelete.value
  if (!song) return
  deleting.value = true
  try {
    await call('delete_song', song.id)
    if (playing.value === song.id) { audio.value?.pause(); playing.value = ''; preview.value = '' }
    pendingDelete.value = null
    notify('已从曲库移除，并取消尚未发送的点歌。')
    await load()
  } catch (e) { notify((e as Error).message) }
  finally { deleting.value = false }
}
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden) void load() }, 3000) })
onUnmounted(() => { clearInterval(timer); audio.value?.pause() })
</script>

<template>
  <div class="page-heading"><div><span class="eyebrow">SONGBOOK</span><h1>会唱的歌，慢慢攒。</h1><p>已经学会的直接唱给你听，新歌准备好后也会留在这里。</p></div><button class="button quiet" :disabled="!connected || loading" @click="load"><AppIcon name="refresh" :size="16" />刷新曲库</button></div>
  <div class="song-summary"><section class="card"><span class="field-caption">已经收录</span><strong>{{ ready.length }}<small>首</small></strong></section><section class="card"><span class="field-caption">正在准备</span><strong>{{ preparing.length }}<small>首</small></strong></section><section class="card"><span class="field-caption">成品占用</span><strong class="song-size">{{ size(totalBytes) }}</strong></section></div>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <section class="card song-list">
    <div class="song-list-top"><div><h2>大肥鱼的曲库</h2><p>点歌时告诉她歌名和歌手或版本，例如“唱一首司凤版雨爱”。</p></div><label class="song-search"><span class="sr-only">搜索歌名或歌手</span><input v-model="query" placeholder="搜索歌名、歌手或版本" /></label></div>
    <div v-if="!visible.length" class="song-empty"><AppIcon name="music" :size="36" /><h3>{{ query ? '没有找到这首歌' : '第一首歌，也会有的。' }}</h3><p>{{ query ? '换一个歌名或歌手试试。' : '在 QQ 告诉她想听什么，新学会的歌会自动收录。' }}</p></div>
    <div v-else class="song-table">
      <div class="song-row song-table-head"><span>曲目</span><span>状态</span><span>时长</span><span>大小</span><span>操作</span></div>
      <template v-for="song in visible" :key="song.id">
        <div class="song-row"><div class="song-name"><strong>{{ song.title }}</strong><span>{{ song.artist }} · 大肥鱼翻唱</span></div><span class="song-state" :class="song.state">{{ state(song) }}</span><span>{{ duration(song.duration) }}</span><span class="muted">{{ size(song.bytes + (song.master_bytes || 0)) }}</span><div class="song-actions"><button class="text-button" :disabled="song.state !== 'ready' || !!previewing" @click="listen(song)"><AppIcon name="play" :size="14" />{{ previewing === song.id ? '读取中' : '试听' }}</button><button class="text-button danger" :disabled="deleting" @click="pendingDelete = song">删除</button></div></div>
        <p v-if="['failed', 'needs_source'].includes(song.state) || (song.state === 'queued' && song.error)" class="song-error">{{ song.error || '这次没有准备完成，重新点歌后可以再试。' }}</p>
        <div v-if="playing === song.id" class="song-player"><audio :ref="setAudio" :src="preview" controls preload="metadata" /><span class="tiny muted">仅在本机试听，不发送 QQ 消息。</span></div>
      </template>
    </div>
    <div class="song-foot"><AppIcon name="clock" :size="16" /><span>新歌需要一些准备时间；成品会保留，临时人声和伴奏处理文件会清理。</span></div>
  </section>
  <ConfirmDialog v-if="pendingDelete && !deleting" title="从曲库删除这首歌？" :detail="`《${pendingDelete.title}》·${pendingDelete.artist} 的本地成品将移除，尚未发送的点歌会取消；已经发出的 QQ 消息不受影响。`" confirm-label="删除曲目" @cancel="pendingDelete = null" @confirm="remove" />
</template>

<style scoped>
.song-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin-bottom:24px}.song-summary .card{padding:22px 26px}.song-summary strong{display:block;font-size:34px;margin-top:12px;font-weight:600;letter-spacing:-1px}.song-summary small{font-size:14px;margin-left:8px;font-weight:400;color:var(--muted)}.song-summary .song-size{font-size:28px;line-height:41px}.song-list{padding:26px}.song-list-top{display:flex;justify-content:space-between;gap:20px;align-items:center;margin-bottom:22px}.song-list-top h2{margin:0 0 8px}.song-list-top p{font-size:13px;color:var(--muted);margin:0;line-height:1.7}.song-search{width:230px;flex-shrink:0}.song-search input{width:100%;box-sizing:border-box}.song-table{overflow-x:auto}.song-row{display:grid;grid-template-columns:minmax(170px,1fr) 125px 48px 66px 118px;gap:12px;align-items:center;min-width:580px;padding:20px 0;border-top:1px solid var(--line)}.song-table-head{padding:11px 0;font-size:12px;color:var(--muted)}.song-name strong{display:block;font-size:16px;margin-bottom:7px}.song-name span{font-size:12px;color:var(--muted)}.song-state{font-size:12px;color:#55738e}.song-state.ready{color:#367c60}.song-state.failed{color:#a86945}.song-actions{display:flex;gap:18px}.song-actions .text-button{font-size:13px;white-space:nowrap}.song-player{display:flex;align-items:center;gap:16px;padding:0 0 20px}.song-player audio{height:36px;max-width:100%}.song-error{margin:0 0 16px;color:#a86945;font-size:13px}.song-foot{display:flex;gap:8px;align-items:center;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:20px;margin-top:6px;line-height:1.7}.song-empty{padding:56px 20px;text-align:center;color:var(--muted)}.song-empty h3{font-weight:500;color:#233c64;margin:18px 0 10px}.song-empty p{font-size:13px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@media(max-width:1100px){.song-list-top{align-items:flex-start;flex-direction:column}.song-search{width:100%}.song-summary .card{padding:20px}.song-player{align-items:flex-start;flex-direction:column}}
</style>
