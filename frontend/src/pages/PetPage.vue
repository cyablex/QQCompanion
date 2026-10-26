<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { call } from '../services/api'
import { useCompanion } from '../composables/useCompanion'
interface Pet { prepared: boolean; enabled: boolean; running: boolean; game_mode: boolean; memory_mib: number }
const { notify, connected } = useCompanion()
const pet = ref<Pet | null>(null), busy = ref(false), error = ref('')
let timer: ReturnType<typeof setInterval>
async function load() { if (!connected.value) return; try { pet.value = await call<Pet>('get_vpet_status'); error.value = '' } catch (e) { error.value = (e as Error).message } }
async function toggle(enabled: boolean) { busy.value = true; try { pet.value = await call<Pet>('set_vpet_enabled', enabled); notify(enabled ? pet.value.game_mode ? '游戏模式结束后会恢复桌宠。' : '桌宠正在走到桌面上。' : '桌宠已关闭。') } catch (e) { error.value = (e as Error).message } finally { busy.value = false } }
onMounted(() => { void load(); timer = setInterval(() => { if (!document.hidden && !busy.value) void load() }, 4000) })
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">ON YOUR DESKTOP</span><h1>让她待在桌面一角。</h1><p>摸摸头、轻轻戳一下，日常的互动也能接进同一个大肥鱼。</p></div></div>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <section class="card pet-card"><div class="pet-icon"><AppIcon name="pet" :size="48" /></div><div><span class="status-pill">{{ pet?.running ? '桌面陪伴中' : pet?.enabled && pet.game_mode ? '游戏模式 · 暂时休息' : '尚未开启' }}</span><h2>大肥鱼桌宠 · VPet 初版</h2><p>共用现有的人设、私聊记录与模型。连续点击主要播放动作，简短回应会留一点间隔。</p><p v-if="pet?.running" class="muted">当前桌宠进程内存：{{ pet.memory_mib }} MiB（不含聊天与语音后台）</p><p v-if="pet && !pet.prepared" class="muted">此安装还未准备桌宠环境，可运行项目中的“准备桌宠环境.cmd”。</p><button class="button" :disabled="busy || !pet || (!pet.prepared && !pet.enabled)" @click="toggle(!pet!.enabled)">{{ pet?.enabled ? '关闭桌宠' : '开启桌宠' }}</button></div></section>
  <div class="pet-details"><section class="card"><h2>现在可以怎样玩</h2><p>点击头部摸摸头，点击身体轻轻戳一下，长按头部提起移动。右键可以调整大小、打开共同经历、日历和曲库，或退出桌宠。</p><p>游戏模式会退出桌宠、释放其进程资源。若此前开启了桌宠，结束游戏模式后会恢复。</p></section><section class="card"><h2>这一阶段的形象</h2><p>当前使用 VPet 原作者的演示动作素材验证互动。蓝发女仆的完整动作素材尚未适配，桌面自由聊天、语音口型与更多动作留到后续。</p><p>互动由原有大肥鱼后台回应，QQ 私聊能读取这些真实记录。</p></section></div>
</template>
<style scoped>
.pet-card{display:flex;align-items:flex-start;gap:28px;padding:32px}.pet-icon{display:grid;place-items:center;min-width:90px;height:90px;border-radius:22px;background:#e8f0ff;color:#6483d2}.pet-card h2{margin:18px 0 12px;font-size:23px}.pet-card p{max-width:650px;color:var(--muted);margin-bottom:16px}.pet-card .button{margin-top:12px}.pet-details{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:24px}.pet-details .card{padding:28px}.pet-details h2{margin-bottom:15px}.pet-details p{font-size:13px;color:var(--muted);margin-top:12px}@media(max-width:1080px){.pet-details{grid-template-columns:1fr}.pet-card{padding:24px;gap:18px}}
</style>
