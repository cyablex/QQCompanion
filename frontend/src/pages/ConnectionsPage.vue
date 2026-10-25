<script setup lang="ts">
import { ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import { call, type Settings } from '../services/api'
import { useCompanion } from '../composables/useCompanion'

const { status, connected, busy, error, run, notify } = useCompanion()
const settings = ref<Settings | null>(null)
const relay = ref({ base_url: '', model: '', relay_key: '' })
const officialKey = ref('')
const restartDialog = ref(false)
async function load() {
  if (!connected.value) return
  try { settings.value = await call<Settings>('get_settings'); relay.value = { ...settings.value.relay, relay_key: '' }; officialKey.value = '' }
  catch (e) { error.value = (e as Error).message }
}
async function saveRelay() {
  const result = await run<{ message: string }>('save_relay', [relay.value], '')
  if (result) { await load(); notify(result.message) }
}
async function saveOfficial() {
  if (!settings.value) return
  const result = await run('save_settings', [{ ...settings.value.values, api_key: officialKey.value }], '官方配置已保存，重启机器人后加载。')
  if (result) await load()
}
async function restart() {
  restartDialog.value = false
  await run('control', ['restart_bot'], '机器人正在重新加载配置，QQ 登录保留，稍后检查连接状态。')
}
watch(connected, value => { if (value) void load() }, { immediate: true })
</script>
<template>
  <div class="page-heading"><div><span class="eyebrow">STAY CONNECTED</span><h1>把连接安排妥当。</h1><p>日常使用中转，官方工具与备用连接独立保留。</p></div><button class="button quiet" :disabled="!connected || busy" @click="load">重新加载</button></div>
  <section class="card route-summary"><div class="icon-tile"><AppIcon name="connection" :size="23" /></div><div><h2>{{ status?.model_route === 'relay' ? '当前使用中转优先' : '当前使用官方连接' }}</h2><p>{{ status?.model || '等待读取当前连接' }} · 联网工具使用官方凭据</p></div><span class="subtle-badge">{{ status?.connected ? '消息通道已连接' : '消息通道待就绪' }}</span></section>
  <template v-if="settings">
    <form class="card form-card" autocomplete="off" @submit.prevent="saveRelay"><div class="form-section-heading"><span class="section-number">01</span><div><h2>中转接入资料</h2><p>地址与模型保存后需核实启用；更换 Key 后，重启机器人会加载新 Key。</p></div></div><div class="form-grid"><label class="full-width">API 地址<input v-model="relay.base_url" type="url" required maxlength="2048" /><span class="input-hint">填写 API 基础地址，例如 https://zrocode.cc/v1。</span></label><label>模型名<input v-model="relay.model" required maxlength="128" /></label><label>中转 API Key<input v-model="relay.relay_key" type="password" autocomplete="new-password" maxlength="512" :placeholder="settings.relay_key_saved ? '已保存，留空保留现有 Key' : '填写中转专用 Key'" /></label></div><div class="inline-note"><AppIcon name="shield" :size="16" /><span>Key 使用 Windows 当前用户加密保存，界面不会读回。</span></div><div class="form-actions"><span class="small muted">当前生效地址：{{ settings.active_relay.base_url || '未启用中转' }}</span><button class="button secondary" :disabled="busy" type="submit">保存接入资料</button></div></form>
    <form class="card form-card" autocomplete="off" @submit.prevent="saveOfficial"><div class="form-section-heading"><span class="section-number">02</span><div><h2>官方连接</h2><p>供官方搜索与正文抓取使用；中转明确拦截时，当前一轮转官方回答。</p></div></div><div class="form-grid"><label>官方备用模型<input v-model="settings.values.model" required maxlength="128" /></label><label>官方 API Key<input v-model="officialKey" type="password" autocomplete="new-password" maxlength="512" :placeholder="settings.key_saved ? '已保存，留空保留现有 Key' : '填写官方 Key'" /></label></div><div class="form-actions"><span class="small muted">修改后需要重新加载机器人。</span><button class="button secondary" :disabled="busy" type="submit">保存官方配置</button></div></form>
    <section class="card restart-card"><div><h2>重新加载机器人</h2><p>短暂中断聊天，保留 QQ 登录。重启会加载已保存的 Key，地址与模型仍使用已启用配置。</p></div><button class="button secondary" :disabled="busy || !status?.services.bot" @click="restartDialog = true"><AppIcon name="refresh" :size="16" />重启机器人</button></section>
  </template>
  <section v-else class="card empty-state"><p>等待读取本机连接配置…</p></section>
  <ConfirmDialog v-if="restartDialog" title="重新加载机器人？" detail="聊天会短暂中断。正在进行的请求可能已产生费用，重启不会自动重发旧消息；QQ 登录保持。" confirm-label="重启机器人" @cancel="restartDialog = false" @confirm="restart" />
</template>
