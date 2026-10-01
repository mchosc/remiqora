/** Recent singing passes take about 2.8× the song, plus a short split and mix. */

const VOICE_RTF = 2.8
const VOICE_OVERHEAD_SEC = 20

export function voiceRemainingSec(elapsedSec: number, durationSec: number | null | undefined): number | null {
  if (durationSec == null || !Number.isFinite(durationSec) || durationSec <= 0) return null
  if (!Number.isFinite(elapsedSec) || elapsedSec < 0) return null
  const total = VOICE_OVERHEAD_SEC + durationSec * VOICE_RTF
  return Math.max(0, Math.round(total - elapsedSec))
}

export function formatClock(totalSeconds: number): string {
  const rounded = Math.max(0, Math.round(totalSeconds))
  const hours = Math.floor(rounded / 3600)
  const minutes = Math.floor((rounded % 3600) / 60)
  const seconds = rounded % 60
  const body = `${minutes}:${String(seconds).padStart(2, '0')}`
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}` : body
}
