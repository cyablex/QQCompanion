<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import WhaleArt from '../components/WhaleArt.vue'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import BudgetDetails from '../components/BudgetDetails.vue'
import { useCompanion } from '../composables/useCompanion'
import { call } from '../services/api'

defineEmits<{ navigate: [page: string] }>()
const { status, connected, busy, refresh, run, notify } = useCompanion()
const stopDialog = ref(false)
const budgetExpanded = ref(false)
const qr = ref<string | null>(null)
const phases: Record<string, [string, string]> = {
  ready: ['大肥鱼，在线中。', '连接已经准备好。去 QQ 和她聊聊，或者让她自在地过会儿日常。'],
  stopped: ['休息一下，随时再见。', '后台已停止。想聊天时，点击启动，让大肥鱼回来。'],
  paused: ['在线，暂时安静一会儿。', '聊天与自主活动已暂停，QQ 连接保留。准备好后，随时恢复。'],
  waiting_login: ['见面前，还差一步。', '需要你用手机确认小号登录。完成后会自动检查聊天连接。'],
  wrong_account: ['登录的账号需要检查。', '当前 QQ 与设置中的小号不一致，请先核对账号。'],
  session_unavailable: ['连接还在恢复中。', 'QQ 会话尚未恢复，后台正在检查。暂时不能确认可以聊天。'],
  recovery_failed: ['连接需要照看一下。', '自动恢复已达到本小时上限，请在原登录管理页检查手机验证。'],
  offline_development: ['暂时留在离线模式。', '当前保留离线开发设置，需要按原流程确认后才能恢复登录。'],
  status_unavailable: ['正在确认连接状态。', '尚未收到有效的连接心跳，暂时不能确认在线。'],
}
const copy = computed(() => phases[status.value?.phase || ''] || ['今天，也一起慢慢来。', '正在连接本机控制服务，稍候就能看到大肥鱼的状态。'])
const running = computed(() => !!status.value && ['bot', 'qq', 'login', 'watchdog'].some(n => status.value?.services[n]))
const allRunning = computed(() => !!status.value && ['bot', 'qq', 'login', 'watchdog'].every(n => status.value?.services[n]))
const progress = computed(() => Math.min(100, (status.value?.budget?.today || 0) / (status.value?.daily_cny || 3) * 100))
watch(() => status.value?.qr_available ? status.value?.health_age : null, async () => {
  qr.value = status.value?.qr_available ? await call<string | null>('get_qr').catch(() => null) : null
})
async function control(action: string) {
  stopDialog.value = false
  const messages: Record<string, string> = { start: '启动请求已提交，正在等待真实连接就绪。', pause: '聊天与自主活动已暂停。', resume: '聊天与自主活动已恢复。', stop: '运行服务已停止，控制台可以继续使用。' }
  await run('control', [action], messages[action])
}
async function game(value: boolean) {
  const result = await run<{ warning: boolean }>('set_game_mode', [value], value ? '游戏模式已开启，文字聊天继续。' : '游戏模式已关闭，语音恢复按需处理。')
  if (result?.warning) notify('游戏模式已保存，但部分语音进程未能关闭，请核对任务管理器。')
}
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">DAFEIYU COMPANION</span><h1>一点日常，都在这里。</h1><p>照看运行，也给陪伴留一点空间。</p></div><button class="button quiet" :disabled="!connected || busy" @click="refresh"><AppIcon name="refresh" :size="16" />刷新状态</button></div>
  <section class="hero-card"><div class="hero-copy"><span class="status-pill" :class="{ online: status?.phase === 'ready' }"><span class="dot" :class="{ amber: status?.phase !== 'ready' }" />{{ status?.phase === 'ready' ? '一切就绪' : status?.phase === 'stopped' ? '已停止' : status?.phase === 'paused' ? '已暂停' : '连接检查中' }}</span><h2>{{ copy[0] }}</h2><p>{{ copy[1] }}</p><div class="hero-actions"><button class="button primary" :disabled="!connected || busy || allRunning" @click="control('start')"><AppIcon name="play" :size="17" />{{ allRunning ? '后台已启动' : '启动大肥鱼' }}</button><button class="button secondary" :disabled="!connected || busy || !running" @click="control(status?.paused || !status?.live ? 'resume' : 'pause')"><AppIcon :name="status?.paused || !status?.live ? 'play' : 'pause'" :size="17" />{{ status?.paused || !status?.live ? '恢复聊天' : '暂停聊天' }}</button></div></div><WhaleArt /><span class="hero-caption">SLOW DAYS, GOOD COMPANY.</span></section>
  <div v-if="status?.budget_exhausted" class="alert warning">今日日常预算已用完，新的付费调用将暂停。北京时间次日重置；QQ 连接保留。</div>
  <section v-if="status?.phase === 'waiting_login'" class="card login-card"><div><span class="eyebrow">QQ LOGIN</span><h2>{{ status.login_scanned ? '已扫码，请在手机确认' : '扫码，让大肥鱼上线' }}</h2><p>请使用大肥鱼的小号在手机 QQ 确认。二维码会随登录状态更新。</p></div><img v-if="qr" :src="qr" alt="大肥鱼 QQ 登录二维码" width="180" height="180" /><p v-else class="muted">等待二维码准备完成…</p></section>
  <div class="overview-grid">
    <section class="card game-card"><div class="card-title"><div class="icon-tile"><AppIcon name="game" :size="23" /></div><ToggleSwitch :model-value="status?.game_mode || false" label="游戏模式" :disabled="!connected || busy" @update:model-value="game" /></div><h2>给游戏腾点空间。</h2><p>开启游戏模式，暂停本地语音识别与合成推理。文字聊天照常继续。</p><div class="card-foot"><span class="dot" :class="{ gray: !status?.game_mode }" />{{ status?.game_mode ? '游戏模式已开启' : '语音按需处理' }}</div></section>
    <section class="card budget-card">
      <div class="section-top"><span class="eyebrow">TODAY’S COST ESTIMATE</span><span class="subtle-badge">模型 + 联网工具</span></div>
      <div class="money">{{ status?.budget ? `¥${status.budget.usage_estimate.toFixed(4)}` : '—' }}<span>今日费用</span></div>
      <p v-if="status?.budget?.active_reserve" class="tiny muted">调用进行中，临时留出 ¥{{ status.budget.active_reserve.toFixed(4) }}</p>
      <div class="budget-meta"><span>用于限额判断的合计 <b>{{ status?.budget ? `¥${status.budget.today.toFixed(4)}` : '—' }}</b></span><span>日限额 ¥{{ status?.daily_cny ?? '—' }}</span></div>
      <div class="progress-track"><div :style="{ width: progress + '%' }" /></div>
      <div class="budget-meta"><span>本月费用估算 <b>{{ status?.budget ? `¥${status.budget.month_usage_estimate.toFixed(4)}` : '—' }}</b></span><span>月限额 ¥{{ status?.monthly_cny ?? '—' }}</span></div>
      <p v-if="status?.budget?.unconfirmed_calls" class="tiny muted">{{ status.budget.unconfirmed_calls }} 次结果不明的记录已释放额度，不再长期占用。</p>
      <p class="tiny muted">{{ status?.budget?.billing_sync.synced_at ? '中转按平台账单校准，官方按用量估算。' : '按已知用量估算，等待中转账单同步。' }}验收只计入月限额。</p>
      <button class="budget-details-toggle" type="button" :aria-expanded="budgetExpanded" aria-controls="budget-details" :disabled="!status?.budget" @click="budgetExpanded = !budgetExpanded"><span>{{ budgetExpanded ? '收起详情' : '查看详情' }}<AppIcon name="arrow" :size="14" /></span><small>按用途查看</small></button>
      <BudgetDetails v-if="status?.budget" :budget="status.budget" :expanded="budgetExpanded" />
    </section>
  </div>
  <section class="card connection-card"><div class="section-top"><h2>运行一览</h2><span class="small muted">窗口可随时关闭</span></div><div class="connection-grid"><div><span class="field-caption">QQ 消息连接</span><strong><span class="dot" :class="{ amber: !status?.connected }" />{{ status?.connected ? '已连接' : '尚未就绪' }}</strong></div><div><span class="field-caption">日常模型</span><strong class="model-label">{{ status?.model || '—' }}</strong><span class="tiny muted">{{ status?.model_route === 'relay' ? '中转优先 · 明确拦截时转官方' : '官方连接' }}</span></div><div><span class="field-caption">后台服务</span><strong>{{ status ? Object.values(status.services).filter(Boolean).length : '—' }} <span class="muted normal">/ {{ status ? Object.keys(status.services).length : 0 }} 项运行</span></strong></div></div><div class="runtime-footer"><button class="text-button" @click="$emit('navigate', 'settings')">管理偏好设置<AppIcon name="arrow" :size="16" /></button><button class="text-button danger" :disabled="!connected || busy || !running" @click="stopDialog = true"><AppIcon name="stop" :size="14" />停止运行</button></div></section>
  <ConfirmDialog v-if="stopDialog" title="让大肥鱼休息一下？" detail="将停止本项目的 QQ 小号、聊天和守护服务，并关闭本地语音推理。控制台保留，之后可以重新启动。" confirm-label="停止运行" @cancel="stopDialog = false" @confirm="control('stop')" />
</template>
