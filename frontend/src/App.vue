<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from './components/AppIcon.vue'
import WindowTitleBar from './components/WindowTitleBar.vue'
import StartupScreen from './components/StartupScreen.vue'
import OverviewPage from './pages/OverviewPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import ConnectionsPage from './pages/ConnectionsPage.vue'
import SongbookPage from './pages/SongbookPage.vue'
import GalleryPage from './pages/GalleryPage.vue'
import CreativePage from './pages/CreativePage.vue'
import CalendarPage from './pages/CalendarPage.vue'
import PetPage from './pages/PetPage.vue'
import { useCompanion } from './composables/useCompanion'
import { call } from './services/api'

const page = ref('home')
const diaryDate = ref('')
const opening = ref(true)
const entered = ref(false)
const bootStarting = ref(true)
const bootError = ref('')
watch(page, value => {
  document.querySelector('.main-area')?.scrollTo(0, 0)
  if (value === 'home' && !entered.value) opening.value = true
}, { flush: 'post' })
const nav = [{ id: 'home', label: '总览', icon: 'home', en: 'OVERVIEW' }, { id: 'creative', label: '共同经历', icon: 'gallery', en: 'OUR SHARED SPACE' }, { id: 'calendar', label: '大肥鱼日历', icon: 'calendar', en: 'DAYS TO REMEMBER' }, { id: 'pet', label: '桌宠', icon: 'pet', en: 'ON YOUR DESKTOP' }, { id: 'songbook', label: '曲库', icon: 'music', en: 'SONGBOOK' }, { id: 'gallery', label: '画册', icon: 'gallery', en: 'GALLERY' }, { id: 'settings', label: '偏好设置', icon: 'settings', en: 'PREFERENCES' }, { id: 'connection', label: '模型连接', icon: 'connection', en: 'CONNECTIONS' }]
const { status, connected, busy, error, notice, connect, refresh } = useCompanion()
const startupReady = computed(() => !bootStarting.value && !bootError.value && !!status.value?.startup_ready && ['ready', 'paused'].includes(status.value.phase))
async function startBackend() {
  bootStarting.value = true; bootError.value = ''
  try {
    if (!connected.value) await connect()
    if (!connected.value) throw new Error('请从桌面“大肥鱼控制台”打开。')
    await call('control', 'start')
    await refresh()
  } catch (e) { bootError.value = (e as Error).message }
  finally { bootStarting.value = false }
}
function retryStartup() { if (!bootStarting.value) { opening.value = true; void startBackend() } }
function startupSettings() { page.value = 'settings'; opening.value = false }
async function openLogin() {
  try { await call('open_login_page') }
  catch (e) { bootError.value = (e as Error).message }
}
let timer: ReturnType<typeof setInterval>
let welcomeAudio: HTMLAudioElement | null = null
let welcomeSource = ''
let welcomeEnabled = false
let welcomeLoaded: Promise<void> | null = null
let stopWelcome: (() => void) | null = null
const welcomePending = ref(false)
async function prepareWelcome() {
  try {
    welcomeEnabled = (await call<{ enabled: boolean }>('get_startup_welcome')).enabled
    if (welcomeEnabled && !welcomeSource) {
      welcomeSource = await call<string>('voice_preview', 'welcome')
      welcomeAudio = new Audio(welcomeSource)
      welcomeAudio.preload = 'auto'
      welcomeAudio.load()
    }
  } catch { welcomeEnabled = false }
}
async function playWelcome() {
  try {
    if (!welcomeSource) welcomeSource = await call<string>('voice_preview', 'welcome')
    welcomeAudio?.pause()
    if (!welcomeAudio) welcomeAudio = new Audio(welcomeSource)
    welcomeAudio.currentTime = 0
    const player = welcomeAudio
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => { player.pause(); cleanup(); resolve() }, 14000)
      const cleanup = () => { clearTimeout(timer); player.onended = null; player.onerror = null; stopWelcome = null }
      stopWelcome = () => { player.pause(); cleanup(); resolve() }
      player.onended = () => { cleanup(); resolve() }
      player.onerror = () => { cleanup(); reject(new Error('欢迎语暂未播放')) }
      player.play().catch(e => { cleanup(); reject(e) })
    })
    welcomePending.value = false
  } catch { welcomePending.value = true }
}
async function welcomeAtFrame(finish: () => void) {
  try {
    await welcomeLoaded
    if (opening.value && welcomeEnabled) await playWelcome()
  } finally { finish() }
}
async function finishOpening(welcome: boolean) {
  if (!entered.value && !startupReady.value) return
  if (!entered.value) page.value = 'home'
  entered.value = true
  opening.value = false
  // The welcome cue has already played with the final animation frame.
}
watch(opening, value => {
  stopWelcome?.(); welcomeAudio?.pause()
  if (value) { welcomePending.value = false; welcomeLoaded = prepareWelcome() }
})
onMounted(async () => {
  await startBackend()
  welcomeLoaded = prepareWelcome()
  timer = setInterval(() => { if (connected.value && !busy.value) { if (!document.hidden) void refresh(); void call<string | null>('pop_navigation').then(target => { if (target?.startsWith('diary')) { diaryDate.value = target.slice(6); page.value = 'calendar' } else if (target) page.value = target }).catch(() => {}) } }, 2000)
})
onUnmounted(() => { clearInterval(timer); stopWelcome?.(); welcomeAudio?.pause() })
</script>
<template>
  <div class="app-shell">
    <WindowTitleBar />
    <StartupScreen v-if="opening" :startup="!entered" :ready="startupReady" :starting="bootStarting" :error="bootError || error" :status="status" @done="finishOpening" @welcome="welcomeAtFrame" @retry="retryStartup" @settings="startupSettings" @login="openLogin" />
    <div :inert="opening">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark">𓆝</div><div><strong>大肥鱼</strong><span>一点陪伴，一点日常</span></div></div>
      <div class="nav-label">你的控制台</div>
      <nav aria-label="主导航"><button v-for="item in nav" :key="item.id" :class="{ active: page === item.id }" @click="page = item.id"><AppIcon :name="item.icon" /><span>{{ item.label }}</span><span v-if="page === item.id" class="nav-dot" /></button></nav>
      <div class="sidebar-bottom"><button class="opening-replay" @click="opening = true">重播开场 <span>↗</span></button><div class="local-badge"><span class="dot" />本机运行</div><p>把复杂留在后台，<br />把日常留给大肥鱼。</p><span class="version">{{ status?.version || 'V5.17' }} · 天蓝桌面</span></div>
    </aside>
    <main class="main-area">
      <header class="topbar"><span>{{ nav.find(n => n.id === page)?.en }}</span><div><span class="small muted">独立桌面控制台</span><span class="top-divider" /><span class="small"><span class="dot" :class="{ amber: !status?.connected }" />{{ status?.connected ? 'QQ 已连接' : status?.phase === 'stopped' ? '后台已停止' : connected ? '正在检查连接' : '连接控制台中' }}</span></div></header>
      <div class="page-content">
        <div v-if="!entered && !opening" class="alert warning"><span>修改设置后，继续检查聊天连接。</span><button class="text-button" :disabled="busy || bootStarting" @click="retryStartup">返回启动加载</button></div>
        <button v-if="welcomePending" class="button quiet" @click="playWelcome">播放大肥鱼的欢迎语 ♫</button>
        <div v-if="entered && ['unavailable', 'singing'].includes(status?.voice?.state || '')" class="alert warning" role="status">{{ status?.voice.message }}</div>
        <div v-if="error" class="alert error" role="alert"><span>{{ error }}</span><button class="icon-button" aria-label="关闭提示" @click="error = ''"><AppIcon name="close" :size="16" /></button></div>
        <OverviewPage v-if="page === 'home' && entered" @navigate="page = $event" />
        <CreativePage v-else-if="page === 'creative'" />
        <CalendarPage v-else-if="page === 'calendar'" :initial-date="diaryDate" @navigate="page = $event" />
        <PetPage v-else-if="page === 'pet'" />
        <SettingsPage v-else-if="page === 'settings'" />
        <SongbookPage v-else-if="page === 'songbook'" />
        <GalleryPage v-else-if="page === 'gallery'" />
        <ConnectionsPage v-else-if="page === 'connection'" />
      </div>
      <footer class="app-footer"><span><AppIcon name="shield" :size="14" />设置保存在这台电脑</span><span>关闭窗口后，大肥鱼仍可在后台运行</span></footer>
    </main>
    <div v-if="notice" class="toast" role="status"><AppIcon name="check" :size="18" />{{ notice }}</div>
    </div>
  </div>
</template>
