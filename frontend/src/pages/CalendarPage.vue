<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AppIcon from '../components/AppIcon.vue'
import { call } from '../services/api'
import { useCompanion } from '../composables/useCompanion'

interface Event { id: string; kind: string; title: string; text: string; created: number; date: string; speaker?: string; authored?: boolean; project_id?: string; project_title?: string; link_id?: string }
interface Calendar { month: string; timezone: string; days: Record<string, number>; events: Event[] }
const emit = defineEmits<{ navigate: [page: string] }>()
const { connected } = useCompanion()
const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(new Date())
const month = ref(today.slice(0, 7)), selected = ref(today), query = ref(''), filter = ref('all')
const data = ref<Calendar>({ month: month.value, timezone: 'Asia/Shanghai', days: {}, events: [] })
const busy = ref(false), error = ref('')
const kinds: Record<string, string> = { diary: '相处日记', episode: '相处片段', project: '共同项目', note: '感受与想法', link: '收藏片段', asset: '留下作品' }
let serial = 0, searchTimer: ReturnType<typeof setTimeout>, refreshTimer: ReturnType<typeof setInterval>
const visible = computed(() => data.value.events.filter(e => filter.value === 'all' || (filter.value === 'diary' ? e.kind === 'diary' : e.kind !== 'diary')))
const days = computed(() => {
  const [year, number] = month.value.split('-').map(Number)
  const offset = (new Date(year, number - 1, 1).getDay() + 6) % 7
  const length = new Date(year, number, 0).getDate()
  return Array.from({ length: Math.ceil((offset + length) / 7) * 7 }, (_, index) => {
    const day = index - offset + 1
    const date = `${month.value}-${String(day).padStart(2, '0')}`
    return day > 0 && day <= length ? { day, date, count: visible.value.filter(e => e.date === date).length } : null
  })
})
const entries = computed(() => visible.value.filter(e => !selected.value || e.date === selected.value))
const dateTitle = computed(() => selected.value ? `${selected.value.replaceAll('-', ' / ')} 的记录` : '这个月留下的记录')
async function load() {
  if (!connected.value) return
  const request = ++serial; busy.value = true
  try {
    const result = await call<Calendar>('get_experience_calendar', month.value, query.value)
    if (request === serial) { data.value = result; error.value = '' }
  } catch (e) { if (request === serial) error.value = (e as Error).message }
  finally { if (request === serial) busy.value = false }
}
function turn(delta: number) {
  const [year, number] = month.value.split('-').map(Number)
  const date = new Date(year, number - 1 + delta, 1)
  month.value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`
}
async function openProject(entry: Event) {
  try { await call('change_creative_project', 'activate', { id: entry.project_id }); emit('navigate', 'creative') }
  catch (e) { error.value = (e as Error).message }
}
async function openLink(entry: Event) {
  try { await call('open_experience_link', entry.project_id, entry.link_id) }
  catch (e) { error.value = (e as Error).message }
}
watch(month, () => { if (!/^\d{4}-\d{2}$/.test(month.value)) return; selected.value = month.value === today.slice(0, 7) ? today : ''; void load() })
watch(query, () => { selected.value = ''; clearTimeout(searchTimer); searchTimer = setTimeout(() => void load(), 250) })
watch(connected, value => { if (value) void load() })
onMounted(() => { void load(); refreshTimer = setInterval(() => { if (!document.hidden && !busy.value) void load() }, 15000) })
onUnmounted(() => { ++serial; clearTimeout(searchTimer); clearInterval(refreshTimer) })
</script>

<template>
  <div class="page-heading"><div><span class="eyebrow">DAYS TO REMEMBER</span><h1>那些一起留下的日子。</h1><p>翻翻她的相处日记，也看看你们做过、聊过和想继续的事。</p></div><button class="button quiet" :disabled="busy" @click="month = today.slice(0, 7); selected = today; load()">回到今天</button></div>
  <div v-if="error" class="alert error" role="alert">{{ error }}</div>
  <div class="calendar-tools"><label>找一段回忆<input v-model="query" maxlength="200" type="search" placeholder="搜这个月的歌曲、感受或片段" /></label><label>想看什么<select v-model="filter"><option value="all">日记与全部经历</option><option value="diary">只看相处日记</option><option value="experience">只看共同经历</option></select></label></div>
  <div class="calendar-layout">
    <section class="card month-card"><div class="month-heading"><button class="icon-button" aria-label="上个月" @click="turn(-1)">‹</button><input v-model="month" type="month" aria-label="选择月份" /><button class="icon-button" aria-label="下个月" @click="turn(1)">›</button></div><div class="month-grid"><span v-for="day in ['一', '二', '三', '四', '五', '六', '日']" :key="day" class="weekday">{{ day }}</span><template v-for="(day, index) in days" :key="index"><button v-if="day" class="day" :class="{ selected: selected === day.date, today: today === day.date }" :aria-label="`${day.date}，${day.count}条记录`" :aria-pressed="selected === day.date" @click="selected = day.date"><span>{{ day.day }}</span><small v-if="day.count">{{ day.count }}条</small></button><span v-else /></template></div><button class="text-button all-month" @click="selected = ''">查看整月 · {{ visible.length }} 条记录</button><p class="calendar-hint">按北京时间归档。没有记录的日子留白。</p></section>
    <section class="calendar-records" :aria-busy="busy"><div class="record-heading"><h2>{{ dateTitle }}</h2><span class="muted">{{ entries.length }} 条</span></div><div v-if="busy && !data.events.length" class="card empty">正在翻开日历……</div><div v-else-if="!entries.length" class="card empty"><AppIcon name="calendar" :size="34" /><h2>这一天，暂时没有留下记录。</h2><p>可以换一天，或在共同经历里保存一个片段和感受。</p></div><article v-for="entry in entries" :key="entry.id" class="card calendar-entry"><div class="entry-meta"><span>{{ kinds[entry.kind] }}</span><time>{{ entry.date }} · {{ new Date(entry.created * 1000).toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit' }) }}</time></div><h2>{{ entry.title }}</h2><span v-if="entry.speaker" class="speaker">{{ entry.speaker }}留下的</span><p>{{ entry.text }}</p><small v-if="entry.kind === 'diary' || entry.kind === 'episode'" class="muted">{{ entry.authored ? '依据当天真实相处写下的日记，包含她自己的主观感受。' : '来自已保存的聊天原话摘记。' }}</small><div v-if="entry.project_id" class="entry-actions"><button class="text-button" @click="openProject(entry)">回到《{{ entry.project_title }}》 ↗</button><button v-if="entry.link_id" class="text-button" @click="openLink(entry)">打开收藏链接 ↗</button></div></article></section>
  </div>
</template>

<style scoped>
.calendar-tools{display:grid;grid-template-columns:1fr 200px;gap:18px;margin-bottom:24px}.calendar-tools label{display:grid;gap:8px;color:var(--muted);font-size:12px}.calendar-tools select,.month-heading input{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fff;color:#324b6a}.calendar-layout{display:grid;grid-template-columns:minmax(300px,390px) minmax(0,1fr);gap:25px;align-items:start}.month-card{padding:22px;position:sticky;top:20px}.month-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:22px}.month-heading input{max-width:180px}.month-heading button{font-size:25px}.month-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}.weekday{text-align:center;color:var(--muted);font-size:12px;padding-bottom:10px}.day{display:flex;min-height:57px;align-items:center;justify-content:center;flex-direction:column;gap:5px;border:1px solid transparent;border-radius:10px;background:#f6f9ff;color:#324b6a}.day small{font-size:9px;color:var(--muted)}.day.today{border-color:#a9bef9}.day.selected{background:#e1ebff;border-color:#6b8ee5}.day.selected span{font-weight:700}.all-month{margin-top:20px}.calendar-hint{font-size:11px;color:var(--muted);margin-top:14px}.record-heading{display:flex;justify-content:space-between;align-items:center;padding:8px 2px 18px}.record-heading h2{font-size:17px}.calendar-records{display:grid;gap:15px}.calendar-entry{padding:24px}.entry-meta{display:flex;justify-content:space-between;gap:10px;color:var(--muted);font-size:10px;margin-bottom:14px}.entry-meta>span{background:#edf3ff;border-radius:5px;padding:4px 7px;color:#5b7cc9}.calendar-entry h2{font-size:17px;overflow-wrap:anywhere}.calendar-entry p{line-height:1.95;white-space:pre-wrap;overflow-wrap:anywhere;margin:12px 0}.speaker{display:block;color:var(--muted);font-size:11px;margin-top:9px}.entry-actions{display:flex;flex-wrap:wrap;gap:18px;margin-top:18px}.empty{text-align:center;padding:45px 24px;color:var(--muted)}.empty h2{margin:18px 0 8px;font-size:16px}.empty p{font-size:12px}@media(max-width:1080px){.calendar-layout{grid-template-columns:1fr}.month-card{position:static;max-width:500px;width:100%}.calendar-tools{grid-template-columns:1fr}}
</style>
