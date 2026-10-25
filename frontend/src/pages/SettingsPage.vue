<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import { call, type Settings, type Preferences } from '../services/api'
import { useCompanion } from '../composables/useCompanion'

const { connected, busy, error, run, notify } = useCompanion()
const values = ref<Preferences | null>(null)
const guestIds = ref('')
const loading = ref(false)
const previewBusy = ref(false)
let previewAudio: HTMLAudioElement | null = null
onUnmounted(() => previewAudio?.pause())
async function preview(style: string) {
  previewBusy.value = true
  try {
    const source = await call<string>('voice_preview', style)
    previewAudio?.pause()
    previewAudio = new Audio(source)
    await previewAudio.play()
  } catch (e) { error.value = (e as Error).message }
  finally { previewBusy.value = false }
}
const features = [
  ['proactive', '主动找你', '有值得分享的内容时，主动联系你'],
  ['friend_proactive', '主动联系好友', '按照已有节奏和个人偏好开展交流'],
  ['web_search', '联网查资料', '在需要时使用官方搜索与正文抓取'],
  ['memory_enabled', '记住相处的事', '按联系人整理与召回长期记忆'],
  ['digital_activities', '自主阅读与日常', '积累阅读笔记和自己的作品'],
  ['autonomous_drawing', '随心画画', '有灵感时创作几何简笔画'],
  ['autonomous_profile', '自主签名', '按已有长期节奏决定是否换签名'],
  ['owner_public_sharing', '分享主人趣事', '只选不涉及隐私的趣事与图片，其他好友私聊不公开'],
  ['autonomous_posts', '自主发动态', '有新内容时，按原额度与节奏发布'],
  ['qzone', '空间互动', '好友空间上午、晚间各一次；自己空间的评论每半小时检查，好友评论仍需审批'],
  ['image_understanding', '看懂图片', '理解私聊中的图片与表情包'],
  ['voice_understanding', '听懂语音', '通过本地转写理解收到的语音'],
  ['voice_replies', '用语音回答你', '仅主人私聊；游戏模式暂停，合成失败时改发文字'],
  ['singing_enabled', '点歌与曲库', '已收录直接发歌曲语音；新歌需歌名和歌手，后台准备并保存'],
  ['startup_welcome', '开场欢迎语', '台词待定，暂不自动播报；确定内容后再加入'],
  ['send_memes', '发送表情', '聊天时按语境选择已有表情'],
]
async function load() {
  if (!connected.value) return
  loading.value = true
  try { const data = await call<Settings>('get_settings'); values.value = data.values; guestIds.value = data.values.guest_ids.join(', ') }
  catch (e) { error.value = (e as Error).message }
  finally { loading.value = false }
}
async function save() {
  if (!values.value) return
  const data = { ...values.value, guest_ids: guestIds.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) }
  const result = await run<{ restart_required: boolean; identity_changed: boolean }>('save_settings', [data], '')
  if (result) {
    await load()
    notify(result.identity_changed ? '账号已变更，在线动作已停用。核对后重启机器人，再明确恢复聊天。' : result.restart_required ? '设置已保存，账号或模型修改需到模型连接页重启机器人。' : '设置已保存，后续处理使用新偏好。')
  }
}
watch(connected, value => { if (value) void load() }, { immediate: true })
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">MAKE IT YOURS</span><h1>按喜欢的方式相处。</h1><p>保留熟悉的记忆，调整接下来的日常。</p></div><button class="button quiet" :disabled="!connected || busy || loading" @click="load">重新加载</button></div>
  <form v-if="values" @submit.prevent="save">
    <section class="card form-card"><div class="form-section-heading"><span class="section-number">01</span><div><h2>名字与性格</h2><p>人设修改在后续生成时生效，不会清除聊天记忆。</p></div></div><div class="form-grid"><label>她的名字<input v-model="values.name" maxlength="40" required /></label><label>对你的称呼<input v-model="values.owner_title" maxlength="40" required /></label><label class="full-width">性格与说话方式<textarea v-model="values.persona" rows="6" maxlength="4000" /><span class="input-hint">可以描述语气、习惯，以及你希望她如何与你相处。</span></label></div></section>
    <section class="card form-card"><div class="form-section-heading"><span class="section-number">02</span><div><h2>聊天与日常</h2><p>这些开关控制是否参与，已有频率与发送边界保持原规则。</p></div></div><div class="features-grid"><div v-for="[key, label, hint] in features" :key="key" class="feature-row"><div><strong>{{ label }}</strong><p>{{ hint }}</p></div><ToggleSwitch :model-value="!!values[key]" :label="label" :disabled="key === 'startup_welcome'" @update:model-value="values![key] = $event" /></div></div></section>
    <section class="card form-card"><div class="form-section-heading"><span class="section-number">03</span><div><h2>费用与额度</h2><p>模型与联网工具共享日常预算；验收按既有独立预算记账。</p></div></div><div class="form-grid"><label>日常上限（元 / 天）<input v-model.number="values.daily_cny" type="number" min="0.01" max="10000" step="0.01" required /></label><label>总月上限（元 / 月）<input v-model.number="values.monthly_cny" type="number" min="0.01" max="10000" step="0.01" required /></label></div></section>
    <section class="card form-card"><div class="form-section-heading"><span class="section-number">04</span><div><h2>账号与好友</h2><p>修改主号或小号会关闭在线动作，核对后需重启并明确恢复。</p></div></div><div class="form-grid"><label>你的主号 QQ<input v-model="values.owner_qq" inputmode="numeric" pattern="[1-9][0-9]{4,19}" required /></label><label>大肥鱼的小号 QQ<input v-model="values.bot_qq" inputmode="numeric" pattern="[1-9][0-9]{4,19}" required /></label><label>好友范围<select v-model="values.guest_mode"><option value="allowlist">仅已登记的测试好友</option><option value="open">开放好友申请和好友私聊</option></select></label><label>测试好友 QQ<input v-model="guestIds" placeholder="多个账号用逗号分隔，最多 10 位" /></label></div></section>
    <section class="card form-card"><div class="form-section-heading"><span class="section-number">05</span><div><h2>大肥鱼的声音</h2><p>当前采用已确认的 C 版中文参考引导，音色来源仍为候选 4；活泼版已停用。长篇、链接和画画回复优先保留文字。</p></div></div><div class="form-grid"><label>什么时候发语音<select v-model="values.voice_mode"><option value="adaptive">大肥鱼根据语境选择，适合说的话偶尔用语音</option><option value="requested">仅我明确要求时</option><option value="always">主人私聊优先用语音</option></select></label><label>当前音色<input value="日常音色 4 · 中文引导 C" readonly /><span class="input-hint">活泼版已停用，保留原始参考文件。</span></label><div class="full-width"><button class="button quiet" type="button" :disabled="previewBusy" @click="preview('everyday')">试听日常 4</button><p class="input-hint">语音最多额外等待 12 秒，超时先回文字。启动时与 QQ 登录并行准备，声音就绪后持续常驻；停止大肥鱼或开启游戏模式时释放。欢迎语仍待定。</p></div></div></section>
    <div class="save-bar"><span class="small muted">只保存设置，不会发送消息或调用收费 API。</span><button class="button primary" :disabled="busy || loading" type="submit">{{ busy ? '正在保存…' : '保存偏好设置' }}</button></div>
  </form>
  <section v-else class="card empty-state"><h2>{{ loading ? '正在读取本机设置…' : '等待连接本机控制台' }}</h2><p>请通过桌面入口打开，设置会从现有安装中读取。</p></section>
</template>
