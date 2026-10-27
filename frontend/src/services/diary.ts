export interface DiaryEvent { id: string; kind: string; title: string; text: string; date: string; created: number; project_id?: string; project_title?: string; link_id?: string; speaker?: string }
export interface DiaryAsset { id: string; kind: string; title: string; scope: string; state: string; project_id: string }
export interface DiaryArtChoice { kind: string; id?: string; scope?: string; title?: string; theme?: string }
export interface DiaryPage { date: string; title: string; text: string; authored: boolean; theme: string; illustration?: DiaryArtChoice; bookmarked: boolean; memorial: boolean; events: DiaryEvent[]; assets: DiaryAsset[] }
export interface DiaryRoom { month: string; cover?: DiaryArtChoice; months: string[]; pages: DiaryPage[]; bookmark_dates: string[]; quiet: boolean; scope: string; generation: number; activity: { phase?: string; day?: string; title?: string; job_id?: string } }

let context: AudioContext | null = null
export function paperSound(enabled: boolean, bookmark = false) {
  if (!enabled) return
  try {
    context ??= new AudioContext()
    void context.resume()
    const gain = context.createGain(), oscillator = context.createOscillator(), at = context.currentTime
    gain.gain.setValueAtTime(.012, at); gain.gain.exponentialRampToValueAtTime(.0001, at + .09)
    oscillator.type = 'sine'; oscillator.frequency.setValueAtTime(bookmark ? 760 : 270, at)
    oscillator.frequency.exponentialRampToValueAtTime(bookmark ? 1100 : 150, at + .09)
    oscillator.connect(gain); gain.connect(context.destination); oscillator.start(at); oscillator.stop(at + .1)
    oscillator.onended = () => { oscillator.disconnect(); gain.disconnect() }
  } catch { /* Reading also works when the audio device is unavailable. */ }
}
