<script setup lang="ts">
import { computed } from 'vue'
import type { Budget } from '../services/api'

const props = defineProps<{ budget: Budget; expanded: boolean }>()
const calls = computed(() => props.budget.breakdown.reduce((n, item) => n + item.calls, 0))
function money(value: number) {
  return value > 0 && value < 0.0001 ? '< ¥0.0001' : `¥${value.toFixed(4)}`
}
function share(value: number) {
  const total = props.budget.usage_estimate
  return total ? `${Math.max(0, Math.min(100, value / total * 100))}%` : '0%'
}
</script>
<template>
  <div id="budget-details" class="budget-expander" :class="{ expanded }" :inert="!expanded" :aria-hidden="!expanded">
    <div class="budget-expander-clip">
      <section class="budget-detail-content" aria-label="今日费用明细">
        <div class="detail-heading"><h3>今天，费用用在哪里</h3><span>{{ budget.day }} · 北京时间</span></div>
        <p class="detail-count">{{ calls }} 次日常调用记录 · 按用途归类</p>
        <div v-if="budget.breakdown.length" class="expense-list">
          <div v-for="item in budget.breakdown" :key="item.id" class="expense-item">
            <div class="expense-line"><strong>{{ item.label }}</strong><span>{{ money(item.usage_estimate) }}</span></div>
            <div class="expense-bar" aria-hidden="true"><span :style="{ width: share(item.usage_estimate) }" /></div>
            <p v-if="item.id === 'reconciliation'">平台汇总与本机明细的差额，未强行分配用途</p>
            <p v-else>{{ item.calls }} 次调用 · 本机费用估算<span v-if="item.unconfirmed_calls"><br />{{ item.unconfirmed_calls }} 次结果不明，临时额度已释放</span></p>
          </div>
        </div>
        <p v-else class="expense-empty">今天还没有日常调用记录。</p>
        <dl class="expense-totals">
          <div><dt>日常费用合计</dt><dd>{{ money(budget.usage_estimate) }}</dd></div>
          <div v-if="budget.active_reserve > 0" class="pending-total"><dt>调用中，临时留出</dt><dd>{{ money(budget.active_reserve) }}</dd></div>
          <div><dt>用于限额判断的合计</dt><dd>{{ money(budget.today) }}</dd></div>
          <div v-if="budget.validation_calls"><dt>验收费用估算 <small>{{ budget.validation_calls }} 次</small></dt><dd>{{ money(budget.validation_usage_estimate) }}</dd></div>
          <div v-if="budget.validation_active_reserve > 0"><dt>验收调用中</dt><dd>{{ money(budget.validation_active_reserve) }}</dd></div>
        </dl>
        <p v-if="budget.billing_sync.relay_today != null" class="expense-note">中转平台返回：{{ money(budget.billing_sync.relay_today) }}。按 1 USD 额度＝{{ budget.billing_sync.cny_per_unit }} 元换算。最近同步 {{ new Date((budget.billing_sync.synced_at || 0) * 1000).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) }}。{{ budget.billing_sync.error ? '本次同步失败，保留上次账单并累加新调用估算。' : '约每 30 秒同步，平台入账可能稍有延迟。' }}</p>
        <dl v-if="budget.periods.length" class="expense-totals"><div v-for="item in budget.periods" :key="item.id"><dt>{{ item.label }}</dt><dd>{{ money(item.amount) }}</dd></div></dl>
        <p class="expense-note">只在调用进行中临时留额度；失败、中断、没有用量回执时释放，记录保留等待对账，不认定为免费。中转以平台汇总校准，官方按用量估算，以账单为准。同步有延迟或官方未返回用量时，费用仍可能存在偏差。验收只计入月限额，本地语音无模型 API 费用。</p>
        <p class="expense-note">官方模型：工作日 9–12 点、14–18 点为高峰，其余时段及节假日半价。{{ budget.billing_sync.peak_rate_enabled === false ? '中转当前分组未启用峰谷倍率。' : '中转以平台返回的实际扣费为准。' }}新调用保存时段与单价，旧记录保留。{{ budget.validation_calls ? '含验收的中转汇总只校准月合计，不强行分摊到日常费用。' : '' }}</p>
      </section>
    </div>
  </div>
</template>
<style scoped>
.budget-expander { display: grid; grid-template-rows: 0fr; opacity: 0; transition: grid-template-rows .3s ease, opacity .2s ease; }
.budget-expander.expanded { grid-template-rows: 1fr; opacity: 1; }
.budget-expander-clip { overflow: hidden; min-height: 0; }
.budget-detail-content { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 19px; }
.detail-heading { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 7px; }
.detail-heading h3 { margin: 0; font-size: 13px; font-weight: 650; }
.detail-heading > span, .detail-count { color: var(--muted); font-size: 10px; }
.detail-count { margin-top: 5px; }
.expense-list { display: grid; gap: 17px; padding: 20px 0; }
.expense-line { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; font-size: 12px; }
.expense-line strong { font-weight: 550; }
.expense-line > span, dd { font-variant-numeric: tabular-nums; white-space: nowrap; }
.expense-bar { height: 4px; border-radius: 4px; background: #eef3fd; overflow: hidden; margin: 8px 0 4px; }
.expense-bar span { display: block; height: 100%; min-width: 0; border-radius: inherit; background: linear-gradient(90deg, #4d6bfe, #94baff); transition: width .25s; }
.expense-item > p, .expense-note { color: var(--muted); font-size: 10px; line-height: 1.8; }
.expense-empty { padding: 20px 0; font-size: 12px; color: var(--muted); }
.expense-totals { border-top: 1px solid var(--line); margin: 0; padding: 13px 0 10px; display: grid; gap: 10px; font-size: 12px; }
.expense-totals > div { display: flex; justify-content: space-between; gap: 10px; }
.expense-totals dd { margin: 0; font-weight: 650; }
.expense-totals small { font-weight: 400; color: var(--muted); padding-left: 5px; }
.expense-totals .pending-total { color: var(--muted); font-size: 10px; }
.expense-note { padding: 10px 12px; border-radius: 9px; background: #f4f8ff; }
</style>
