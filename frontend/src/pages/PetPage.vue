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
  <div class="page-heading"><div><span class="eyebrow">ON YOUR DESKTOP</span><h1>让她待在桌面一角。</h1><p>摸摸头，聊几句，让她唱首歌。桌面与 QQ 都能接着同一段相处。</p></div></div>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <section class="card pet-card"><div class="pet-icon"><AppIcon name="pet" :size="48" /></div><div><span class="status-pill">{{ pet?.running ? '桌面陪伴中' : pet?.enabled && pet.game_mode ? '游戏模式 · 暂时休息' : '尚未开启' }}</span><h2>大肥鱼桌宠 · 桌边对话</h2><p>共用现有的人设、私聊记录与模型。右键选择“和大肥鱼说话”即可输入文字，也能点歌和交代已支持的事情。</p><p v-if="pet?.running" class="muted">当前桌宠进程内存：{{ pet.memory_mib }} MiB（不含聊天与语音后台）</p><p v-if="pet && !pet.prepared" class="muted">此安装还未准备桌宠环境，可运行项目中的“准备桌宠环境.cmd”。</p><button class="button" :disabled="busy || !pet || (!pet.prepared && !pet.enabled)" @click="toggle(!pet!.enabled)">{{ pet?.enabled ? '关闭桌宠' : '开启桌宠' }}</button></div></section>
  <div class="pet-details"><section class="card"><h2>现在可以怎样玩</h2><p>点击头部摸摸头，点击身体轻轻戳一下，长按头部提起移动。右键菜单的大小滑杆可连续调整50%—200%，以升级前的大小为100%，重开也会记住。</p><p>右键“喂大白饭”会放下一碗可拖的饭。拿起来，她会亮着眼睛慢慢追；追到后抱住饭碗，继续按住可一起拖走，松手就开吃。提前放下她也会追到再吃；“收起米饭”可结束互动。</p><p>本机已接入同一套蓝发女仆连续透明视频，包含呼吸、哼歌、张望、读写、吃饭和触摸反馈；常态动作播完再换，也可右键选择。安静陪伴暂停走动与自动播放；主动喂饭后会回到原来的陪伴设置。右键“潜水收起”会留下可拖的小头像，点一下恢复；也可完全隐藏。支持减少动效，游戏模式关闭进程。</p></section><section class="card"><h2>同一段相处</h2><p>桌面与 QQ 共用聊天、记忆和真实任务。摸头、提起和喂饭只作本地互动，不自动进入聊天、日记或共同记录；陪伴姿态不会冒充已经读完文章或画好作品。</p><p>透明视频与静态预览使用同一套角色画法，减少动效时保留静态画面；缺少视频素材或组件时沿用已有角色。真正写日记时显示写字，写好可从提示打开当天的日记。语音、歌曲、图片、GIF仍可在聊天窗口播放或查看；麦克风输入、语音口型和完整逐帧专属动作集仍待补充。</p></section></div>
</template>
<style scoped>
.pet-card{display:flex;align-items:flex-start;gap:28px;padding:32px}.pet-icon{display:grid;place-items:center;min-width:90px;height:90px;border-radius:22px;background:#e8f0ff;color:#6483d2}.pet-card h2{margin:18px 0 12px;font-size:23px}.pet-card p{max-width:650px;color:var(--muted);margin-bottom:16px}.pet-card .button{margin-top:12px}.pet-details{display:grid;grid-template-columns:1fr 1fr;gap:22px;margin-top:24px}.pet-details .card{padding:28px}.pet-details h2{margin-bottom:15px}.pet-details p{font-size:13px;color:var(--muted);margin-top:12px}@media(max-width:1080px){.pet-details{grid-template-columns:1fr}.pet-card{padding:24px;gap:18px}}
</style>
