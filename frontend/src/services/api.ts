export interface BudgetItem {
  id: string; label: string; amount: number; calls: number; unconfirmed: number; usage_estimate: number; unconfirmed_calls: number;
}
export interface Budget {
  today: number; validation: number; month: number; day: string;
  breakdown: BudgetItem[]; unconfirmed: number; validation_calls: number;
  usage_estimate: number; month_usage_estimate: number; month_unconfirmed: number;
  validation_usage_estimate: number; validation_unconfirmed: number;
  active_reserve: number; month_active_reserve: number; validation_active_reserve: number; unconfirmed_calls: number;
  billing_sync: { synced_at?: number; error?: string; peak_rate_enabled?: boolean; cny_per_unit?: number; relay_today?: number; adjustment: number };
  periods: { id: string; label: string; amount: number }[];
}
export interface Status {
  version: string; name: string; phase: string; connected: boolean;
  startup_ready: boolean;
  voice: { state: string; ready: boolean; message: string; seconds?: number; since?: number };
  paused: boolean; live: boolean; game_mode: boolean;
  game_mode_auto: boolean;
  game_mode_status: { monitor: { error?: string; override?: boolean; games?: string[] }; release: { denied?: number[] } };
  services: Record<string, boolean>; daily_cny: number; monthly_cny: number;
  model_route: string; model: string; official_model: string;
  budget: Budget | null;
  health_age: number | null; messages_today: number | null;
  qr_available: boolean; login_scanned: boolean; budget_exhausted: boolean;
}
export interface Preferences {
  revision: number; name: string; owner_title: string; persona: string;
  owner_qq: string; bot_qq: string; model: string; guest_mode: string;
  guest_ids: string[]; daily_cny: number; monthly_cny: number;
  [key: string]: string | number | boolean | string[];
}
export interface Connection { base_url: string; model: string }
export interface Song {
  id: string; title: string; artist: string; state: string; progress: string;
  created: number; updated: number; duration: number; bytes: number; master_bytes?: number; error: string; source_url: string;
}
export interface Drawing {
  id: string; scope: string; title: string; caption: string; created: number; bytes: number; thumbnail: string;
}
export interface Gallery {
  drawings: Drawing[]; total: number; filtered: number; public_count: number; private_count: number;
  bytes: number; page: number; pages: number;
}
export interface Settings {
  values: Preferences; key_saved: boolean; relay_key_saved: boolean;
  relay: Connection; active_relay: Connection; route: string;
}
interface Reply<T> { ok: boolean; data: T; message?: string }
type NativeBridge = Record<string, (...args: unknown[]) => Promise<Reply<unknown>>>
declare global { interface Window { pywebview?: { api: NativeBridge } } }

// All native calls live here; pages stay independent of the window framework.
export async function call<T>(method: string, ...args: unknown[]): Promise<T> {
  const api = window.pywebview?.api
  if (!api) throw new Error('请通过桌面“大肥鱼控制台”打开。普通浏览器仅能查看界面。')
  const reply = await api[method](...args) as Reply<T>
  if (!reply.ok) throw new Error(reply.message || '操作未完成，请稍后重试。')
  return reply.data
}
export function nativeReady(): Promise<boolean> {
  if (window.pywebview?.api) return Promise.resolve(true)
  return new Promise(resolve => {
    const timer = window.setTimeout(() => resolve(false), 5000)
    window.addEventListener('pywebviewready', () => { clearTimeout(timer); resolve(true) }, { once: true })
  })
}
