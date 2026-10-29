<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { call } from '../services/api'
import { useCompanion } from '../composables/useCompanion'
interface Asset { id: string; kind: string; scope: string; title: string; subtitle?: string; state: string }
interface Entry { id: string; text: string; author: string; created: number }
interface Link { id: string; title: string; url: string; note: string; created: number }
interface Project { id: string; title: string; kind: string; stage: string; next_step: string; updated: number; entries: Entry[]; assets: Asset[]; links: Link[] }
interface Room { projects: Project[]; assets: Asset[]; active_id: string }
interface Version { id: string; label: string; state: string; progress: string; pitch_mode: string; shift: number }
const { connected, notify } = useCompanion()
const room = ref<Room>({ projects: [], assets: [], active_id: '' })
const selected = ref(''), title = ref(''), kind = ref('song'), note = ref(''), stage = ref('idea'), nextStep = ref('')
const project = computed(() => room.value.projects.find(p => p.id === selected.value))
const search = ref(''), linkTitle = ref(''), linkUrl = ref(''), linkNote = ref('')
const projects = computed(() => room.value.projects.filter(p => !search.value.trim() || [p.title, p.next_step, ...p.entries.map(e => e.text), ...p.assets.map(a => a.title), ...(p.links || []).map(l => `${l.title} ${l.note}`)].join('\n').toLocaleLowerCase().includes(search.value.trim().toLocaleLowerCase())))
const busy = ref(false), error = ref(''), creating = ref(false), dirty = ref(false)
const assetChoice = ref(''), songChoice = ref(''), versions = ref<Version[]>([])
const mode = ref('preserve'), shift = ref(0), voiceLabel = ref('跟随当前音色')
const audio = ref<HTMLAudioElement | null>(null), audioSource = ref(''), imageSource = ref('')
let timer: ReturnType<typeof setInterval>, playback = 0
const stages: Record<string, string> = { idea: '选题', preview: '试听与草图', adjust: '调整中', finished: '成品' }
const kinds: Record<string, string> = { song: '一起做首歌', comic: '一起画一组', reading: '一起看与聊', other: '其他小计划' }
const date = (stamp: number) => new Date(stamp * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
const key = (a: Asset) => `${a.kind}|${a.scope}|${a.id}`
async function load() {
  if (!connected.value) return
  try {
    room.value = await call<Room>('get_creative_projects')
    if (!selected.value || !room.value.projects.some(p => p.id === selected.value)) selected.value = room.value.active_id || room.value.projects[0]?.id || ''
    if (!dirty.value && project.value) { stage.value = project.value.stage; nextStep.value = project.value.next_step }
    if (songChoice.value) versions.value = await call<Version[]>('get_song_previews', songChoice.value)
  } catch (e) { error.value = (e as Error).message }
}
async function perform(work: () => Promise<void>) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await work() } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function change(action: string, data: Record<string, unknown>) {
  const saved = await call<Project>('change_creative_project', action, data)
  selected.value = saved.id; await load(); return saved
}
function saveLink() { void perform(async () => {
  await change('link', { id: selected.value, title: linkTitle.value, url: linkUrl.value, note: linkNote.value })
  linkTitle.value = ''; linkUrl.value = ''; linkNote.value = ''; notify('片段和感受留好了。')
}) }
function openLink(link: Link) { void perform(async () => { await call('open_experience_link', selected.value, link.id) }) }
function create() { void perform(async () => {
  await change('create', { title: title.value, kind: kind.value }); title.value = ''; creating.value = false
  dirty.value = false; stage.value = 'idea'; nextStep.value = ''; note.value = ''; songChoice.value = ''; versions.value = []
  linkTitle.value = ''; linkUrl.value = ''; linkNote.value = ''
}) }
function select(p: Project) { void perform(async () => {
  audio.value?.pause(); audioSource.value = ''; imageSource.value = ''; playback++
  selected.value = p.id; stage.value = p.stage; nextStep.value = p.next_step; dirty.value = false; note.value = ''
  linkTitle.value = ''; linkUrl.value = ''; linkNote.value = ''
  songChoice.value = p.assets.find(a => a.kind === 'song')?.id || ''; versions.value = []
  await change('activate', { id: p.id })
}) }
function saveStep() { void perform(async () => {
  await change('update', { id: selected.value, stage: stage.value, next_step: nextStep.value }); dirty.value = false
  notify('下一步留好了，下次可以接着做。')
}) }
function saveNote() { void perform(async () => { await change('note', { id: selected.value, text: note.value }); note.value = '' }) }
function attach() { void perform(async () => {
  const a = room.value.assets.find(a => key(a) === assetChoice.value)
  if (!a) return
  await change('attach', { id: selected.value, kind: a.kind, asset_id: a.id, scope: a.scope })
  if (a.kind === 'song') { songChoice.value = a.id; await load() }
  assetChoice.value = ''
}) }
function listen(a: Asset, version = '') { const serial = ++playback; void perform(async () => {
  audio.value?.pause(); imageSource.value = ''
  const source = await call<string>(version ? 'song_version_preview' : 'song_preview', a.id, ...(version ? [version] : []))
  if (serial !== playback) return
  audioSource.value = source; await nextTick(); await audio.value?.play().catch(() => {})
}) }
function view(a: Asset) { void perform(async () => {
  audio.value?.pause(); audioSource.value = ''; playback++
  imageSource.value = await call<string>('drawing_preview', a.id, a.scope)
}) }
function makePreview() { void perform(async () => {
  await call('create_song_preview', songChoice.value, mode.value, Number(shift.value))
  await change('update', { id: selected.value, stage: 'adjust', next_step: '等试听做好，比较前半段低声区和后半段，记录想保留或继续调整的地方。' })
  dirty.value = false; await load(); notify('开始制作独立试听，原成品会保留。')
}) }
function adopt(v: Version) { void perform(async () => {
  await call('adopt_song_preview', songChoice.value, v.id)
  await change('update', { id: selected.value, stage: 'finished', next_step: '' })
  dirty.value = false; await load(); notify('已采用为曲库成品，旧版仍可试听。')
}) }
function listenVersion(v: Version) { const a = room.value.assets.find(a => a.kind === 'song' && a.id === songChoice.value); if (a) listen(a, v.id) }
async function voiceSettings() { const s = await call<{ label: string; mode: string; shift: number }>('get_singing_settings'); voiceLabel.value = s.label; return s }
function saveVoice(follow = false) { void perform(async () => { await call('save_singing_settings', mode.value, Number(shift.value), follow); await voiceSettings(); notify('以后新制作的歌曲会使用这套设置。') }) }
function chooseVoice() { void perform(async () => { await call('choose_singing_reference'); await voiceSettings() }) }
onMounted(async () => {
  await load(); songChoice.value = project.value?.assets.find(a => a.kind === 'song')?.id || ''
  try { const s = await voiceSettings(); mode.value = s.mode; shift.value = s.shift } catch { /* The project can be used without a voice reference. */ }
  await load(); timer = setInterval(() => { if (!document.hidden && !busy.value) void load() }, 4000)
})
onUnmounted(() => { clearInterval(timer); playback++; audio.value?.pause() })
</script>

<template>
  <div class="page-heading"><div><span class="eyebrow">OUR SHARED SPACE</span><h1>昨天没做完的，今天继续。</h1><p>把作品、一起看的片段、感受和下一步，留在同一个地方。</p></div><button class="button" :disabled="!connected || busy" @click="creating = !creating">新建项目</button></div>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <form v-if="creating" class="card create-form" @submit.prevent="create"><label>想一起做什么<input v-model="title" required maxlength="80" placeholder="比如：把 Time Machine 唱成喜欢的样子" /></label><label>这次的方向<select v-model="kind"><option v-for="(label, id) in kinds" :key="id" :value="id">{{ label }}</option></select></label><button class="button" :disabled="busy || !title.trim()">开始这件小事</button></form>
  <div class="creative-layout">
    <aside class="card project-list"><span class="field-caption">我们的共同经历</span><input v-model="search" type="search" class="project-search" aria-label="搜索共同经历" placeholder="找歌曲、片段或感受" /><p v-if="!room.projects.length" class="muted">第一件小事，从这里开始。</p><p v-else-if="!projects.length" class="muted">没有找到相关记录，换个词试试。</p><button v-for="p in projects" :key="p.id" class="project-item" :class="{ active: p.id === selected }" :disabled="busy" @click="select(p)"><span>{{ kinds[p.kind] }}</span><strong>{{ p.title }}</strong><small>{{ stages[p.stage] }} · {{ p.id === room.active_id ? '聊天正在接续' : '点击翻看' }}</small></button></aside>
    <div v-if="project" class="project-detail">
      <section class="card project-heading"><div><span class="eyebrow">{{ kinds[project.kind] }}</span><h2>{{ project.title }}</h2><p class="muted">上次留下：{{ date(project.updated) }}</p></div><span class="status-pill">{{ stages[project.stage] }}</span></section>
      <section class="card"><h2>从这里接着做</h2><p class="muted">留下具体的一步，她在下次私聊时也能接上。</p><div class="step-editor"><label>做到哪里了<select v-model="stage" @change="dirty = true"><option v-for="(label, id) in stages" :key="id" :value="id">{{ label }}</option></select></label><label>下一步<textarea v-model="nextStep" rows="3" maxlength="500" placeholder="比如：先听前30秒，比较低声区是否更自然。" @input="dirty = true" /></label><button class="button quiet" :disabled="busy" @click="saveStep">留好下一步</button></div></section>
      <section class="card"><h2>一起留下的作品</h2><p class="muted">关联曲库或画册里的真实作品，随时回来听、回来翻。</p><div class="attach-row"><select v-model="assetChoice" aria-label="选择已有作品"><option value="">选一件已有作品</option><option v-for="a in room.assets" :key="key(a)" :value="key(a)">{{ a.kind === 'song' ? '♫' : '▧' }} {{ a.title }} {{ a.subtitle ? `· ${a.subtitle.slice(0, 28)}` : '' }}</option></select><button class="button quiet" :disabled="busy || !assetChoice" @click="attach">放进项目</button></div><p v-if="!project.assets.length" class="muted">可以先在 QQ 点首歌或请她画张图，再放进来。</p><div v-for="a in project.assets" :key="key(a)" class="project-asset"><div><strong>{{ a.title }}</strong><small>{{ a.state === 'ready' ? '作品已就绪' : a.state === 'unavailable' ? '原作品已不可用，记录仍保留' : '正在准备' }}</small></div><button class="text-button" :disabled="a.state !== 'ready' || busy" @click="a.kind === 'song' ? listen(a) : view(a)">{{ a.kind === 'song' ? '试听成品' : '查看画面' }}</button></div><audio v-if="audioSource" ref="audio" :src="audioSource" controls class="project-player" /><img v-if="imageSource" :src="imageSource" alt="项目中的画作" class="project-image" /></section>
      <section v-if="project.kind === 'song'" class="card"><h2>试一试，再决定</h2><p class="muted">每次调整生成独立试听，喜欢了再采用为成品。默认保留原曲音高。参考音域仅在高音仍处于模型范围内时统一升八度；额外移调会让人声与伴奏一起改变调性。</p><label>这次调整哪首<select v-model="songChoice" @change="load"><option value="">选择项目中的歌曲</option><option v-for="a in project.assets.filter(a => a.kind === 'song')" :key="a.id" :value="a.id">{{ a.title }}</option></select></label><div class="voice-line"><span>目标音色：{{ voiceLabel }}</span><button class="text-button" :disabled="busy" @click="chooseVoice">更换参考音色</button><button class="text-button" :disabled="busy" @click="saveVoice(true)">跟随说话音色</button></div><div class="voice-controls"><label>音域处理<select v-model="mode"><option value="reference_octave">整曲参考音域</option><option value="preserve">保留原曲旋律与音域（推荐）</option></select></label><label>额外移调：{{ shift }} 半音<input v-model.number="shift" type="range" min="-12" max="12" step="1" /></label></div><div class="preview-actions"><button class="button" :disabled="busy || !songChoice" @click="makePreview">制作一版试听</button><button class="text-button" :disabled="busy" @click="saveVoice(false)">也用于以后的新歌</button></div><div v-for="v in versions" :key="v.id" class="preview-version"><div><strong>{{ v.label }}</strong><small>{{ v.state === 'ready' ? `${v.pitch_mode === 'reference_octave' ? '整曲参考音域' : '原音域'} · 移调 ${v.shift}` : v.progress || '正在准备' }}</small></div><div><button class="text-button" :disabled="busy || v.state !== 'ready'" @click="listenVersion(v)">试听</button><button class="text-button" :disabled="busy || v.state !== 'ready'" @click="adopt(v)">采用为成品</button></div></div></section>
      <section class="card"><h2>一起看的片段</h2><p class="muted">留一个视频或网页链接，再写下当时的感受。保存链接后可在浏览器回看。</p><form @submit.prevent="saveLink"><input v-model="linkTitle" required maxlength="120" placeholder="给这个片段起个名字" aria-label="片段标题" /><input v-model="linkUrl" required type="url" maxlength="2000" placeholder="https://www.bilibili.com/video/…" aria-label="视频或网页链接" /><textarea v-model="linkNote" rows="2" maxlength="1500" placeholder="看到哪里了？哪一幕想下次接着聊？" aria-label="片段感受" /><button class="button quiet" :disabled="busy || !linkTitle.trim() || !linkUrl.trim()">收藏这个片段</button></form><article v-for="link in [...(project.links || [])].reverse()" :key="link.id" class="creative-note"><div><strong>{{ link.title }}</strong><time>{{ date(link.created) }}</time></div><p v-if="link.note">{{ link.note }}</p><button class="text-button" :disabled="busy" @click="openLink(link)">打开原链接 ↗</button></article></section>
      <section class="card"><h2>当时的感受，也留在这里</h2><p class="muted">写下听感，或在私聊中请她记下一个具体想法。</p><form @submit.prevent="saveNote"><textarea v-model="note" rows="3" maxlength="1500" placeholder="刚刚最喜欢哪一段？下一版想变成什么样？" /><button class="button quiet" :disabled="busy || !note.trim()">留在这里</button></form><article v-for="entry in [...project.entries].reverse()" :key="entry.id" class="creative-note"><div><strong>{{ entry.author === 'companion' ? '私聊中留的想法' : '你留下的' }}</strong><time>{{ date(entry.created) }}</time></div><p>{{ entry.text }}</p></article></section>
    </div>
    <section v-else class="card creative-empty"><AppIcon name="gallery" :size="42" /><h2>先选一件想一起做的事。</h2><p>一首歌、一组小画，或者一个还没想完的点子。</p><button class="button quiet" :disabled="!connected" @click="creating = true">留下第一个项目</button></section>
  </div>
</template>

<style scoped>
.project-search{width:100%;margin:16px 0 10px;font-size:11px;padding:10px}.creative-note strong,.creative-note p{overflow-wrap:anywhere}
.creative-layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:22px;align-items:start}.project-list{padding:22px 14px;position:sticky;top:20px}.project-list>.field-caption{padding:0 10px}.project-item{display:flex;flex-direction:column;gap:8px;width:100%;margin-top:12px;padding:16px;border:1px solid transparent;border-radius:12px;background:transparent;text-align:left;cursor:pointer;color:#324b6a}.project-item.active{background:#edf3ff;border-color:#d3e0fb}.project-item span,.project-item small{font-size:11px;color:var(--muted)}.project-item strong{font-size:14px;line-height:1.6}.project-detail{display:grid;gap:20px}.project-detail>.card{padding:26px}.project-detail h2{font-size:18px;margin-top:0}.project-detail p.muted{font-size:12px;line-height:1.8;margin-bottom:20px}.project-heading{display:flex;align-items:center;justify-content:space-between;gap:18px}.project-heading h2{font-size:24px;line-height:1.5;margin:8px 0}.project-heading p{margin-bottom:0!important}.create-form{display:grid;grid-template-columns:2fr 1fr auto;align-items:end;gap:16px;margin-bottom:24px;padding:22px}.creative-layout label,.create-form label{display:grid;gap:9px;font-size:12px;color:#53677f}.creative-layout select,.create-form select{width:100%;border:1px solid var(--line);border-radius:9px;padding:11px;background:#fff;color:#324b6a;box-sizing:border-box;font:inherit}.step-editor{display:grid;gap:16px}.step-editor .button{justify-self:start}.attach-row{display:flex;gap:12px;margin:18px 0}.attach-row select{flex:1;min-width:0}.project-asset,.preview-version{display:flex;justify-content:space-between;align-items:center;gap:15px;padding:16px 0;border-top:1px solid var(--line)}.project-asset strong,.preview-version strong{display:block;font-size:14px}.project-asset small,.preview-version small{display:block;font-size:11px;color:var(--muted);margin-top:7px}.project-player{width:100%;margin-top:18px}.project-image{display:block;max-width:100%;max-height:460px;object-fit:contain;margin:20px auto 0;border-radius:12px}.voice-line{display:flex;flex-wrap:wrap;align-items:center;gap:14px;font-size:12px;margin:22px 0;color:var(--muted)}.voice-controls{display:grid;grid-template-columns:1fr 1fr;gap:20px}.voice-controls input{accent-color:#6485da;padding:0}.preview-actions{display:flex;align-items:center;gap:18px;margin:20px 0}.preview-version>div:last-child{display:flex;gap:16px}.creative-note{padding:20px 0 0;margin-top:20px;border-top:1px solid var(--line)}.creative-note>div{display:flex;justify-content:space-between;gap:15px;font-size:12px}.creative-note time{color:var(--muted);font-size:10px}.creative-note p{white-space:pre-wrap;line-height:1.9;color:#53677f;font-size:13px}.project-detail form{display:grid;gap:14px}.project-detail form .button{justify-self:start}.creative-empty{text-align:center;padding:65px 25px;color:var(--muted)}.creative-empty h2{font-size:19px;color:#324b6a}.creative-empty p{font-size:13px;margin-bottom:25px}@media(max-width:1080px){.creative-layout{grid-template-columns:185px minmax(0,1fr);gap:16px}.project-detail>.card{padding:20px}.create-form,.voice-controls{grid-template-columns:1fr}.voice-line{gap:10px}}
</style>
