<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import DiaryArt from './DiaryArt.vue'
import { call } from '../services/api'
import { paperSound, type DiaryArtChoice, type DiaryAsset, type DiaryRoom } from '../services/diary'
import { useCompanion } from '../composables/useCompanion'

const props = defineProps<{ month: string; initialDate?: string }>()
const emit = defineEmits<{ 'update:month': [month: string]; navigate: [page: string] }>()
const { connected, notify } = useCompanion()
const room = ref<DiaryRoom | null>(null), selected = ref(''), query = ref(''), filter = ref('all')
const opened = ref(false), loading = ref(false), error = ref(''), saving = ref(false), writing = ref(false)
const requestedDay = ref('')
const fontSize = ref(16), sound = ref(false), pageKey = ref(0)
const mediaSource = ref(''), mediaKind = ref(''), mediaTitle = ref(''), mediaBusy = ref(false)
const audio = ref<HTMLAudioElement | null>(null)
const customArt = ref(''), artPanel = ref(false), artSaving = ref(false), artChoices = ref<DiaryArtChoice[]>([])
const customCover = ref(''), coverPanel = ref(false)
const artPresets = [{ theme: 'quiet', title: '安静的日常' }, { theme: 'song', title: '听一首歌' }, { theme: 'reading', title: '翻翻书页' }, { theme: 'rice', title: '一碗白米饭' }, { theme: 'night', title: '晚安时分' }, { theme: 'memory', title: '留住这一刻' }]
const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date())
let serial = 0, mediaSerial = 0, timer: ReturnType<typeof setInterval>, searchTimer: ReturnType<typeof setTimeout>
let artSerial = 0
let coverSerial = 0
let focusAfterTurn = false
const pages = computed(() => (room.value?.pages || []).filter(p => filter.value === 'all' || (filter.value === 'bookmarks' ? p.bookmarked : p.memorial)))
const index = computed(() => pages.value.findIndex(p => p.date === selected.value))
const page = computed(() => pages.value[index.value])
const paragraphs = computed(() => page.value?.text.split(/\n+/).filter(Boolean) || [])
const count = computed(() => room.value?.pages.filter(p => p.authored).length || 0)
const monthLabel = computed(() => `${props.month.slice(0, 4)} 年 ${Number(props.month.slice(5))} 月`)
const dateLabel = (date: string) => `${Number(date.slice(5, 7))} 月 ${Number(date.slice(8))} 日`
const canSound = computed(() => sound.value && room.value?.quiet === false)

function clearMedia() { ++mediaSerial; audio.value?.pause(); mediaSource.value = ''; mediaKind.value = ''; mediaBusy.value = false }
async function load() {
  if (!connected.value) return
  const request = ++serial; loading.value = true
  try {
    const value = await call<DiaryRoom>('get_diary_book', props.month, query.value)
    if (request !== serial) return
    if (room.value && (room.value.scope !== value.scope || room.value.generation !== value.generation)) { clearMedia(); selected.value = ''; requestedDay.value = ''; opened.value = false }
    room.value = value; error.value = ''
    if (!pages.value.some(p => p.date === selected.value)) selected.value = requestedDay.value || pages.value[0]?.date || ''
  } catch (e) { if (request === serial) error.value = (e as Error).message }
  finally { if (request === serial) loading.value = false }
}
function choose(day: string) { const wasOpen = opened.value; clearMedia(); requestedDay.value = ''; selected.value = day; opened.value = true; focusAfterTurn = true; ++pageKey.value; paperSound(canSound.value); void nextTick(() => { document.querySelector<HTMLElement>('.journal-reading')?.scrollIntoView({ block: 'start', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); if (!wasOpen) focusPage() }) }
function focusPage() { if (focusAfterTurn) { document.querySelector<HTMLElement>('.journal-sheet')?.focus({ preventScroll: true }); focusAfterTurn = false } }
function goDate(day: string) { clearMedia(); requestedDay.value = day; selected.value = day; opened.value = true; if (props.month !== day.slice(0, 7)) emit('update:month', day.slice(0, 7)); else void load() }
defineExpose({ goDate })
function turn(delta: number) { const next = pages.value[index.value + delta]; if (next) choose(next.date) }
function firstOpen() { if (page.value) choose(page.value.date); else { opened.value = true; paperSound(canSound.value) } }
async function bookmark() {
  if (!page.value || saving.value) return
  saving.value = true
  const day = page.value.date, enabled = !page.value.bookmarked
  try { await call('bookmark_diary', day, enabled); paperSound(canSound.value, true); await load() }
  catch (e) { notify((e as Error).message) }
  finally { saving.value = false }
}
async function writeToday() {
  if (!room.value || writing.value) return
  writing.value = true
  try {
    const result = await call<{ accepted: boolean }>('request_diary_write', today, room.value.scope, room.value.generation)
    notify(result.accepted ? '已请她看看今天的记录。' : '她正在写，稍等一会儿。')
    if (props.month !== today.slice(0, 7)) emit('update:month', today.slice(0, 7))
    await load()
  } catch (e) { notify((e as Error).message) }
  finally { writing.value = false }
}
async function preview(asset: DiaryAsset) {
  if (!page.value) return
  clearMedia(); const request = ++mediaSerial, day = page.value.date
  mediaBusy.value = true; mediaTitle.value = asset.title; mediaKind.value = asset.kind
  try { const value = await call<string>('diary_asset_preview', day, asset.project_id, asset.kind, asset.id); if (request === mediaSerial) { mediaSource.value = value; if (asset.kind === 'song') { await nextTick(); await audio.value?.play().catch(() => {}) } } }
  catch (e) { if (request === mediaSerial) { notify((e as Error).message); clearMedia() } }
  finally { if (request === mediaSerial) mediaBusy.value = false }
}
async function project(id: string) { try { await call('change_creative_project', 'activate', { id }); emit('navigate', 'creative') } catch (e) { notify((e as Error).message) } }
async function loadPageArt() {
  const request = ++artSerial; customArt.value = ''; artPanel.value = false
  if (!page.value || page.value.illustration?.kind !== 'drawing') return
  try { const value = await call<string>('diary_page_art', page.value.date); if (request === artSerial) customArt.value = value }
  catch { if (request === artSerial) customArt.value = '' }
}
async function openArtPanel() {
  artPanel.value = !artPanel.value
  if (!artPanel.value) return
  try { artChoices.value = await call<DiaryArtChoice[]>('diary_art_choices') } catch (e) { notify((e as Error).message) }
}
async function chooseArt(selection: DiaryArtChoice) {
  if (!page.value || artSaving.value) return
  artSaving.value = true
  try { await call('choose_diary_art', page.value.date, selection); await load(); artPanel.value = false; paperSound(canSound.value, true) }
  catch (e) { notify((e as Error).message) }
  finally { artSaving.value = false }
}
async function openCoverPanel() {
  coverPanel.value = !coverPanel.value
  if (coverPanel.value) { try { artChoices.value = await call<DiaryArtChoice[]>('diary_art_choices') } catch (e) { notify((e as Error).message) } }
}
async function chooseCover(selection: DiaryArtChoice) {
  if (artSaving.value) return
  artSaving.value = true
  try { await call('choose_diary_cover', props.month, selection); await load(); coverPanel.value = false; paperSound(canSound.value, true) }
  catch (e) { notify((e as Error).message) }
  finally { artSaving.value = false }
}
async function loadCoverArt() {
  const request = ++coverSerial; customCover.value = ''; coverPanel.value = false
  if (room.value?.cover?.kind !== 'drawing') return
  try { const value = await call<string>('diary_cover_art', props.month); if (request === coverSerial) customCover.value = value } catch { if (request === coverSerial) customCover.value = '' }
}
function keys(e: KeyboardEvent) {
  if (!opened.value || /INPUT|TEXTAREA|SELECT|BUTTON|AUDIO/.test((e.target as HTMLElement).tagName)) return
  if (e.key === 'ArrowLeft') { e.preventDefault(); turn(-1) }
  if (e.key === 'ArrowRight') { e.preventDefault(); turn(1) }
}
function jumpInitial() {
  if (!props.initialDate) return
  opened.value = true; selected.value = props.initialDate; requestedDay.value = props.initialDate
  if (props.month !== props.initialDate.slice(0, 7)) emit('update:month', props.initialDate.slice(0, 7))
}
watch(() => props.month, () => { clearMedia(); if (!requestedDay.value.startsWith(props.month)) requestedDay.value = ''; selected.value = props.initialDate?.startsWith(props.month) ? props.initialDate : requestedDay.value; void load() })
watch(() => props.initialDate, () => { jumpInitial(); void load() })
watch(query, () => { requestedDay.value = ''; clearTimeout(searchTimer); searchTimer = setTimeout(() => void load(), 250) })
watch(filter, () => { requestedDay.value = ''; if (!pages.value.some(p => p.date === selected.value)) selected.value = pages.value[0]?.date || ''; clearMedia() })
watch(() => [page.value?.date, JSON.stringify(page.value?.illustration), room.value?.scope, room.value?.generation], () => void loadPageArt())
watch(() => [props.month, JSON.stringify(room.value?.cover), room.value?.scope, room.value?.generation], () => void loadCoverArt())
watch(connected, value => { if (value) void load() })
onMounted(() => { jumpInitial(); void load(); timer = setInterval(() => { if (!document.hidden && !loading.value) void load() }, 8000) })
onUnmounted(() => { ++serial; ++artSerial; ++coverSerial; clearInterval(timer); clearTimeout(searchTimer); clearMedia() })
</script>

<template>
  <section class="whale-journal" @keydown="keys">
    <div v-if="error" class="alert error" role="alert">{{ error }}</div>
    <div class="journal-toolbar">
      <label class="month-picker"><span class="sr-only">日记月份</span><select :value="month" @change="emit('update:month', ($event.target as HTMLSelectElement).value)"><option v-for="m in room?.months || [month]" :key="m" :value="m">{{ m.replace('-', ' / ') }}</option></select></label>
      <div class="journal-tools-right"><button v-if="!opened" class="text-button" :aria-expanded="coverPanel" @click="openCoverPanel">本月封面</button><button class="text-button" :disabled="writing || room?.activity.phase === 'writing'" @click="writeToday"><AppIcon name="pen" :size="14" />{{ room?.activity.phase === 'writing' ? '她正在写日记…' : '请她写今天' }}</button><button class="text-button" :aria-pressed="sound" :disabled="room?.quiet" @click="sound = !sound">{{ room?.quiet ? '安静阅读' : sound ? '轻音效：开' : '轻音效：关' }}</button></div>
    </div>
    <p v-if="room?.activity.phase === 'writing'" class="writing-note" role="status">她正在把 {{ room.activity.day }} 的相处写下来，写好后再来看。</p>
    <p v-else-if="['failed', 'interrupted'].includes(room?.activity.phase || '')" class="writing-note">这次还没写好，已经保存的日记仍在这里。</p>
    <p v-else-if="room?.activity.phase === 'skipped'" class="writing-note">今天暂时没有新的日记，已有内容可以继续翻看。</p>

    <section v-if="!opened && coverPanel" class="art-choice-panel" aria-label="选择本月封面"><button :disabled="artSaving" @click="chooseCover({ kind: 'auto' })">写日记的封面</button><button v-for="item in artPresets" :key="item.theme" :disabled="artSaving" @click="chooseCover({ kind: 'preset', theme: item.theme })">{{ item.title }}</button><button v-for="item in artChoices" :key="item.scope! + item.id" :disabled="artSaving" @click="chooseCover({ kind: 'drawing', id: item.id, scope: item.scope })">{{ item.title }}</button></section>
    <div v-if="!opened" class="journal-cover">
      <DiaryArt :theme="room?.cover?.theme || 'cover'" :override="customCover" :class="{ 'cover-vignette': !!room?.cover?.kind }" label="蓝发鲸鱼娘大肥鱼的日记封面" />
      <div class="cover-copy"><span class="cover-kicker">DAFEIYU · PRIVATE JOURNAL</span><h2>鲸鱼日记</h2><p class="cover-subtitle">把一点日常，慢慢写成我们。</p><span class="cover-month">{{ monthLabel }}</span><button class="open-book" @click="firstOpen">翻开日记 <span>↗</span></button></div>
      <span class="cover-foot">{{ count ? `这个月，写下了 ${count} 天` : '日子慢慢过，故事慢慢写。' }}</span>
      <span class="cover-ribbon" aria-hidden="true" />
    </div>

    <div v-else class="journal-open">
      <aside class="journal-index">
        <button class="book-back" @click="opened = false; clearMedia(); paperSound(canSound)">‹ 合上日记本</button>
        <div class="index-heading"><span>CONTENTS</span><h2>{{ monthLabel }}</h2></div>
        <label class="journal-search"><AppIcon name="search" :size="15" /><input v-model="query" maxlength="200" placeholder="找一句话、一首歌…" aria-label="搜索日记与经历" /></label>
        <div class="index-filters"><button :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button><button :class="{ active: filter === 'bookmarks' }" @click="filter = 'bookmarks'">书签</button><button :class="{ active: filter === 'memorials' }" @click="filter = 'memorials'">纪念</button></div>
        <nav aria-label="日记目录"><button v-for="item in pages" :key="item.date" class="index-entry" :class="{ selected: selected === item.date }" :aria-current="selected === item.date ? 'page' : undefined" @click="choose(item.date)"><span>{{ dateLabel(item.date) }}<AppIcon v-if="item.bookmarked" name="bookmark" :size="12" /></span><strong>{{ item.title }}</strong><small>{{ item.authored ? '她的日记' : '共同经历' }}{{ item.memorial ? ' · 有纪念' : '' }}</small></button></nav>
        <p v-if="!pages.length" class="index-empty">{{ loading ? '正在翻目录…' : filter === 'bookmarks' ? '喜欢的一页，可以夹个书签。' : '这个月的故事还在慢慢攒。' }}</p>
      </aside>

      <div class="journal-reading">
        <div class="reading-tools"><span>{{ page ? `${index + 1} / ${pages.length}` : '留一页空白，等新的故事。' }}</span><div><button class="font-button" aria-label="缩小字号" :disabled="fontSize <= 14" @click="fontSize--">A−</button><button class="font-button" aria-label="放大字号" :disabled="fontSize >= 22" @click="fontSize++">A＋</button><button class="bookmark-button" :class="{ saved: page?.bookmarked }" :disabled="!page || saving" :aria-pressed="page?.bookmarked" @click="bookmark"><AppIcon name="bookmark" :size="16" />{{ page?.bookmarked ? '已夹书签' : '夹个书签' }}</button></div></div>
        <Transition name="paper" mode="out-in" @after-enter="focusPage">
          <article v-if="page" :key="`${page.date}:${pageKey}`" class="journal-sheet" tabindex="-1" :style="{ '--journal-font': `${fontSize}px` }">
            <header class="sheet-heading"><span class="sheet-date">{{ page.date.replaceAll('-', ' . ') }}</span><span class="little-star" aria-hidden="true">✧</span><h2>{{ page.title }}</h2><p>{{ page.authored ? '大肥鱼，写给自己的这一页。' : '一起留下的片段，放在这一天。' }}</p></header>
            <DiaryArt :theme="page.theme" :override="customArt" :label="customArt ? page.illustration?.title : '大肥鱼的' + (page.theme === 'song' ? '听歌' : page.theme === 'rice' ? '白米饭' : page.theme === 'night' ? '夜晚' : '日常') + '主题插画'" class="page-illustration" />
            <div class="art-choice-toggle"><span>{{ customArt ? page.illustration?.title : '这一页的小插画' }}</span><button class="text-button" :aria-expanded="artPanel" @click="openArtPanel">{{ artPanel ? '收起选择' : '换一幅插画' }}</button></div>
            <section v-if="artPanel" class="art-choice-panel" aria-label="选择这一天的插画"><button :disabled="artSaving" @click="chooseArt({ kind: 'auto' })">跟随当天内容</button><button v-for="item in artPresets" :key="item.theme" :disabled="artSaving" @click="chooseArt({ kind: 'preset', theme: item.theme })">{{ item.title }}</button><span v-if="artChoices.length">也可以夹进画册里的作品：</span><button v-for="item in artChoices" :key="item.scope! + item.id" :disabled="artSaving" @click="chooseArt({ kind: 'drawing', id: item.id, scope: item.scope })">{{ item.title }}</button></section>
            <div v-if="page.authored" class="journal-prose"><p v-for="(paragraph, i) in paragraphs" :key="i">{{ paragraph }}</p></div>
            <div v-else class="unwritten-note"><span>这一天的长日记还没有写下。</span><p>已经发生的小事和留下的作品，先收在这里。</p></div>
            <section v-if="page.memorial" class="memorial-label"><AppIcon name="sparkle" :size="16" /><span>值得留下的这一页</span><p>{{ page.assets.length ? '一起做过的东西，也在这里。' : '桌边发生的一点小事，留着以后再看。' }}</p></section>
            <div v-if="page.assets.length" class="diary-attachments"><button v-for="asset in page.assets" :key="asset.project_id + asset.kind + asset.id" class="keepsake-card" :disabled="asset.state !== 'ready' || mediaBusy" @click="preview(asset)"><span class="card-tape" aria-hidden="true" /><AppIcon :name="asset.kind === 'song' ? 'music' : 'gallery'" :size="25" /><span><strong>{{ asset.title }}</strong><small>{{ asset.state !== 'ready' ? '作品暂不可用' : asset.kind === 'song' ? '听听这首歌' : '打开这幅画' }}</small></span><span>↗</span></button></div>
            <section v-if="page.events.length" class="day-clippings"><h3>夹在这一页的小片段</h3><div v-for="event in page.events" :key="event.id" class="day-clipping"><span class="clipping-dot" /><div><strong>{{ event.title }}</strong><p>{{ event.text.replace(/^\[桌宠场景记录，仅作相处背景\]\s*/, '') }}</p><button v-if="event.project_id" class="text-button" @click="project(event.project_id)">回到共同经历 ↗</button></div></div></section>
            <footer class="sheet-signature"><span>晚一点，再翻到这里。</span><strong>大肥鱼 <span>𓆝</span></strong></footer>
          </article>
          <article v-else key="empty" class="journal-sheet empty-page"><DiaryArt theme="quiet" /><h2>{{ query ? '这句话，暂时没有找到。' : '这一页，还留着空白。' }}</h2><p>{{ query ? '试试别的词，或者换一个月份。' : '写过的日记和一起留下的事，会慢慢放进来。' }}</p><button v-if="query" class="text-button" @click="query = ''">清除搜索</button></article>
        </Transition>
        <div v-if="mediaKind" class="diary-player"><div><strong>{{ mediaTitle }}</strong><button class="icon-button" aria-label="关闭作品预览" @click="clearMedia"><AppIcon name="close" :size="16" /></button></div><p v-if="mediaBusy">正在打开这件作品…</p><audio v-else-if="mediaKind === 'song'" ref="audio" :src="mediaSource" controls /><img v-else :src="mediaSource" :alt="mediaTitle" /></div>
        <nav class="page-navigation" aria-label="翻篇"><button :disabled="index <= 0" @click="turn(-1)">‹ 新一篇</button><span>{{ page ? dateLabel(page.date) : monthLabel }}</span><button :disabled="index < 0 || index >= pages.length - 1" @click="turn(1)">旧一篇 ›</button></nav>
      </div>
    </div>
  </section>
</template>

<style scoped>
.cover-vignette :deep(img),.cover-vignette :deep(svg){position:absolute;width:55%;height:90%;right:0;bottom:0;object-fit:contain}.cover-vignette{background:linear-gradient(135deg,#fffdf7,#f0f5f6)}
.journal-search{flex-direction:row}.journal-index .index-entry{display:block;width:100%}
.art-choice-toggle{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:-12px 0 25px;font-size:10px;color:#91a0a8}.art-choice-toggle .text-button{font-size:10px}.art-choice-panel{display:flex;flex-wrap:wrap;gap:8px;padding:14px;margin:-10px 0 25px;border:1px dashed #d9dfdc;background:#f5f7f3;border-radius:7px}.art-choice-panel button{border:1px solid #e2e5dd;background:#fffdf7;padding:7px 10px;color:#698293;font-size:11px;border-radius:5px;cursor:pointer}.art-choice-panel>span{width:100%;color:#8b9ca6;font-size:10px;margin:6px 0}
.whale-journal{--ink:#314b64;--paper:#fffdf6;--blue:#557e9d;--gold:#b3986e;color:var(--ink)}.journal-toolbar,.journal-tools-right,.reading-tools,.reading-tools>div{display:flex;align-items:center;gap:18px}.journal-toolbar{justify-content:space-between;margin:0 0 20px}.month-picker select{border:1px solid #d8e1e6;background:#fffdf8;border-radius:10px;padding:10px 14px;color:var(--ink)}.journal-tools-right{gap:20px;font-size:12px}.writing-note{padding:12px 16px;margin:0 0 18px;border-left:2px solid #99b2c5;color:#597389;background:#edf3f6;font-size:13px}.journal-cover{position:relative;min-height:440px;overflow:hidden;border-radius:14px 22px 22px 14px;background:#f9f5e9;box-shadow:0 20px 48px #344e6b12,8px 0 0 -3px #f4eedf,12px 0 0 -4px #d9e0dd;border:1px solid #e8e2d4}.journal-cover>.diary-art{position:absolute;inset:0}.cover-copy{position:relative;z-index:1;padding:56px 44px;max-width:57%;text-shadow:0 1px 10px #fff8}.cover-kicker{font-size:9px;letter-spacing:3px;color:#708696}.cover-copy h2{font:500 49px/1.4 'Noto Serif SC','Songti SC','SimSun',serif;letter-spacing:6px;margin:22px 0 14px;color:#385e7b}.cover-subtitle{font:14px/2.1 'Songti SC','SimSun',serif;letter-spacing:2px}.cover-month{display:block;font-size:12px;color:#708796;margin-top:24px}.open-book{display:flex;gap:30px;align-items:center;background:#365c78;color:#fffaf0;border:0;border-radius:7px;padding:13px 20px;margin-top:27px;font-size:13px;cursor:pointer;box-shadow:0 5px 15px #365c7818;transition:transform .18s,background .18s}.open-book:hover{background:#244c69;transform:translateY(-2px)}.cover-foot{position:absolute;bottom:21px;left:44px;font-size:10px;color:#668193}.cover-ribbon{position:absolute;top:0;right:40px;width:23px;height:66px;background:#476f8a;clip-path:polygon(0 0,100% 0,100% 100%,50% 82%,0 100%)}.journal-open{display:grid;grid-template-columns:205px minmax(0,1fr);gap:25px;align-items:start}.journal-index{padding:6px 0;position:sticky;top:14px}.book-back{font-size:12px;background:none;border:0;padding:0;color:#6d8496;cursor:pointer}.index-heading{margin:31px 0 20px}.index-heading>span{font-size:9px;letter-spacing:3px;color:#96a5ae}.index-heading h2{font:500 21px/1.7 'Songti SC','SimSun',serif;margin-top:7px}.journal-search{display:flex;align-items:center;gap:7px;background:#fffdf7;border:1px solid #e4e4dd;border-radius:8px;padding:9px}.journal-search input{border:0!important;background:transparent!important;box-shadow:none!important;padding:0!important;width:100%;font-size:11px;min-width:0}.index-filters{display:flex;gap:5px;margin:14px 0}.index-filters button{padding:5px 9px;background:none;border:0;color:#7a8b98;cursor:pointer;font-size:11px;border-radius:5px}.index-filters .active{background:#e7eff3;color:#3e6580}.journal-index nav{display:grid;gap:9px;max-height:62vh;overflow:auto;padding-right:5px}.index-entry{position:relative;text-align:left;border:1px solid transparent;background:transparent;padding:14px 12px;border-radius:8px;color:#536f83;cursor:pointer}.index-entry.selected{background:#fffdf7;border-color:#e6e1d5;box-shadow:0 3px 12px #324e6805}.index-entry>span{display:flex;justify-content:space-between;font-size:10px;color:#8297a6}.index-entry strong{display:block;font:500 14px/1.8 'Songti SC','SimSun',serif;margin:6px 0}.index-entry small{font-size:9px;color:#99a5ad}.index-empty{font-size:12px;line-height:1.9;color:#8a9ba7;padding:15px 10px}.reading-tools{justify-content:space-between;margin-bottom:13px;font-size:10px;color:#8797a4;gap:10px}.reading-tools>div{gap:10px}.font-button{border:0;background:none;color:#6d8496;cursor:pointer;font-family:Georgia,serif}.bookmark-button{display:flex;align-items:center;gap:5px;border:0;background:none;color:#7790a2;font-size:11px;cursor:pointer}.bookmark-button.saved{color:#b68d53}.journal-sheet{position:relative;padding:35px 38px 28px;min-height:650px;border:1px solid #e8e2d5;border-radius:4px 12px 12px 4px;background:linear-gradient(90deg,#e9e2d31a,transparent 18px),repeating-linear-gradient(0deg,#aa976902 0,#aa976902 1px,transparent 1px,transparent 4px),var(--paper);box-shadow:0 12px 38px #314b6410,5px 0 0 -2px #f3efe4;outline:none}.sheet-heading{position:relative;border-bottom:1px solid #e9e3d8;padding-bottom:21px}.sheet-date{font:12px Georgia,serif;letter-spacing:3px;color:#7b91a0}.little-star{float:right;color:#b8a073;font-size:22px}.sheet-heading h2{font:500 25px/1.7 'Noto Serif SC','Songti SC','SimSun',serif;letter-spacing:1px;margin:17px 0 9px;overflow-wrap:anywhere}.sheet-heading>p{font-size:10px;color:#9aa5aa;letter-spacing:1px}.page-illustration{height:185px;margin:15px 0 23px}.journal-prose{font:var(--journal-font)/2.25 'Noto Serif SC','Songti SC','SimSun',serif;letter-spacing:.35px;color:#3f5260}.journal-prose p{margin:0 0 1.4em;white-space:pre-wrap;overflow-wrap:anywhere}.unwritten-note{font:14px/2.1 'Songti SC','SimSun',serif;color:#7b8e9a;margin:18px 0 30px}.unwritten-note p{font-size:12px;margin-top:9px}.memorial-label{position:relative;border-top:1px dashed #ddd6c7;padding:21px 0 15px;margin-top:22px;color:#9f865c;display:flex;align-items:center;gap:8px;flex-wrap:wrap}.memorial-label>span{font-size:12px}.memorial-label>p{width:100%;font-size:11px;color:#8b999d;margin-top:1px}.diary-attachments{display:grid;gap:15px;margin:10px 0 26px}.keepsake-card{position:relative;display:flex;align-items:center;gap:15px;text-align:left;background:#f1f5f5;border:1px solid #e0e6e3;padding:24px 18px 18px;border-radius:3px;cursor:pointer;color:#597b90;transform:rotate(-.6deg);transition:transform .18s,box-shadow .18s}.keepsake-card:nth-child(even){transform:rotate(.7deg)}.keepsake-card:hover{transform:rotate(0);box-shadow:0 5px 13px #365c780c}.keepsake-card>span:last-child{margin-left:auto}.keepsake-card strong{display:block;font:500 14px/1.7 'Songti SC','SimSun',serif}.keepsake-card small{display:block;font-size:10px;color:#8d9fa8;margin-top:5px}.card-tape{position:absolute;top:-6px;left:37%;width:65px;height:17px;background:#d2decd88;transform:rotate(-3deg)}.day-clippings{padding-top:7px}.day-clippings h3{font-size:11px;font-weight:400;color:#96a1a6;letter-spacing:1px;margin-bottom:18px}.day-clipping{display:flex;gap:11px;margin:0 0 20px;align-items:flex-start}.clipping-dot{width:5px;height:5px;min-width:5px;background:#afc1ca;border-radius:50%;margin-top:7px}.day-clipping strong{font-size:11px;font-weight:500;color:#698293}.day-clipping p{font-size:12px;line-height:1.9;white-space:pre-wrap;overflow-wrap:anywhere;color:#7a8d99;margin:7px 0}.day-clipping .text-button{font-size:10px}.sheet-signature{margin-top:35px;padding-top:19px;border-top:1px solid #eee8dc;display:flex;align-items:center;justify-content:space-between;gap:14px}.sheet-signature>span{font-size:10px;color:#9ca7a9}.sheet-signature strong{font:16px 'Songti SC','SimSun',serif;font-weight:400;letter-spacing:2px;color:#738c9d}.sheet-signature strong span{font-size:24px}.page-navigation{display:flex;justify-content:space-between;align-items:center;margin:25px 0 3px;color:#8fa0aa;font-size:10px}.page-navigation button{background:none;border:0;color:#678397;cursor:pointer;font-size:12px;padding:7px}.empty-page{text-align:center;padding-top:60px}.empty-page .diary-art{height:220px}.empty-page h2{font:500 22px/2 'Songti SC','SimSun',serif;margin-top:20px}.empty-page>p{font-size:12px;line-height:2;color:#91a0a7;margin-top:12px}.diary-player{background:#fffdf6;border:1px solid #e5e0d4;border-radius:9px;padding:18px;margin-top:17px}.diary-player>div{display:flex;justify-content:space-between;align-items:center;font-size:12px;color:#607c8e;margin-bottom:12px}.diary-player audio{width:100%;height:38px}.diary-player img{max-width:100%;max-height:550px;display:block;margin:auto}.diary-player>p{font-size:12px;color:#8597a4}.paper-enter-active,.paper-leave-active{transition:opacity .16s,transform .16s;transform-origin:left center}.paper-enter-from{opacity:0;transform:perspective(1200px) rotateY(-4deg) translateX(7px)}.paper-leave-to{opacity:0;transform:perspective(1200px) rotateY(3deg) translateX(-5px)}button:disabled{cursor:default;opacity:.45}@media(max-width:1150px){.journal-open{grid-template-columns:170px minmax(0,1fr);gap:18px}.journal-sheet{padding:28px 25px}.cover-copy{padding-left:32px}.cover-copy h2{font-size:41px}.cover-subtitle{font-size:12px;letter-spacing:1px}}@media(max-width:850px){.journal-open{grid-template-columns:1fr}.journal-index{position:static}.index-heading{margin:20px 0 12px}.journal-index nav{display:flex;overflow-x:auto;max-height:none}.index-entry{min-width:170px}.journal-cover{min-height:400px}.cover-copy{max-width:70%;padding-top:40px}.journal-tools-right{gap:12px}.cover-copy h2{font-size:35px}.journal-toolbar{gap:12px}.journal-sheet{min-height:500px}.reading-tools>span{max-width:45%}}@media(prefers-reduced-motion:reduce){*,*::before,*::after{transition:none!important;animation:none!important;transform:none!important}}
</style>
