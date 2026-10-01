import { acceptHMRUpdate, defineStore } from 'pinia'
import * as api from '../api/yue2'
import type { CotMode, GenerateOptions } from '../api/yue2'
import * as tracksApi from '../api/tracks'
import { applyStatus, applyVoiceAndWait, getActiveVoiceId, voiceNameFor, VoiceApplyError, waitForVoiceApply } from '../api/voices'
import type { ApplyStatus } from '../api/voices'
import { forgetVoiceClock, rememberVoiceClock, rememberVoiceUsed, voiceClock, voiceUsed } from './voiceClock'
import { clearVoiceWatch, markVoiceWatch, noteVoice, voiceWatchActive } from './voiceWatch'
import type { JobStatus } from '../types'
import { i18n } from '../i18n'
import { createPollingLoop, type PollContext, type PollingLoop } from '../composables/polling'
import type { JsonObject, VoiceJobProgress } from '../api/contracts'

const t = i18n.global.t

const HEALTH_MS = 5000
const healthLoops = new WeakMap<object, PollingLoop>()
type HistoryEdit = { kind: 'renamed'; title: string } | { kind: 'deleted' }
// Only edits completed while this request is pending need reconciliation.
// Keep them outside persisted/reactive state and discard them with the load.
const pendingHistoryEdits = new WeakMap<object, Map<number, HistoryEdit>>()

export interface Yue2Job {
  id: string
  status: JobStatus
  createdAt: number
  title: string
  style: string
  lyrics: string
  cot: CotMode
  precision: 'q8_0' | 'q4_0'
  seed: number
  error?: string
  audioUrl?: string
  abcPlan?: string | null
  durationSec?: number | null
  wallSec?: number | null
  savedFilename?: string | null
  saveError?: string | null
  dbId?: number | null
  shortId?: number | null
  finalized: boolean
  voiceApply?: 'running' | 'done' | 'failed'
  voicePhase?: string
  voiceId?: string
  voiceName?: string
  voiceStartedAt?: number
  voiceProgress?: VoiceJobProgress | null
  voiceError?: string
  voiceErrorCode?: string
  params?: JsonObject
}

export const useYue2Store = defineStore('yue2', {
  state: () => ({
    jobs: [] as Yue2Job[],
    health: null as api.HealthResponse | null,
    healthError: false,
    historyLoaded: false,
    // Set by a TrackCard's "insert into form" action; GenerateForm watches
    // this and copies it into its own local ABC textarea state.
    pendingAbcInsert: null as string | null,
    pendingParamsInsert: null as JsonObject | null,
    _aborters: {} as Record<string, AbortController>,
    _voiceAborters: {} as Record<number, AbortController>,
    _historyGeneration: 0,
  }),
  actions: {
    requestInsertParams(params: JsonObject) {
      this.pendingParamsInsert = { ...params }
    },
    clearPendingParamsInsert() {
      this.pendingParamsInsert = null
    },
    async loadHistory() {
      const generation = ++this._historyGeneration
      const isCurrent = () => generation === this._historyGeneration
      const edits = new Map<number, HistoryEdit>()
      pendingHistoryEdits.set(this, edits)
      try {
        const tracks = await tracksApi.listTracks('yue2')
        if (!isCurrent()) return
        const savedJobs: Yue2Job[] = await Promise.all(tracks.map(async (track) => {
          const voice = await this._savedVoice(track.id, track.audio_url, isCurrent)
          const job: Yue2Job = {
            id: `saved_${track.id}`,
            status: 'done',
            createdAt: new Date(track.created_at).getTime() || Date.now(),
            title: track.title,
            style: typeof track.params.style === 'string' && track.params.style.trim() ? track.params.style : track.title,
            lyrics: track.lyrics,
            cot: track.params.cot === 'melody' || track.params.cot === 'full' ? track.params.cot : 'off',
            precision: track.params.precision === 'q4_0' ? 'q4_0' : 'q8_0',
            seed: track.seed ?? 0,
            audioUrl: voice.audioUrl || track.audio_url,
            abcPlan: track.abc_url ? '' : null,
            durationSec: track.duration_ms ? track.duration_ms / 1000 : null,
            wallSec: track.wall_ms ? track.wall_ms / 1000 : null,
            savedFilename: track.filename,
            dbId: track.id,
            shortId: track.short_id ?? null,
            finalized: true,
            params: track.params,
            voiceApply: voice.voiceApply,
            voicePhase: voice.voicePhase,
            voiceId: voice.voiceId,
            voiceName: voice.voiceName,
            voiceStartedAt: voice.voiceStartedAt,
            voiceProgress: voice.voiceProgress,
            voiceError: voice.voiceError,
            voiceErrorCode: voice.voiceErrorCode,
          }
          return job
        }))
        if (!isCurrent()) return
        const currentSavedJobs = savedJobs.flatMap(job => {
          const edit = job.dbId == null ? undefined : edits.get(job.dbId)
          if (edit?.kind === 'deleted') return []
          return [edit?.kind === 'renamed' ? { ...job, title: edit.title } : job]
        })
        // Keep any jobs still in-flight this session (not yet in the saved list).
        const inFlightIds = new Set(this.jobs.filter((j) => !j.finalized).map((j) => j.id))
        this.jobs = [...this.jobs.filter((j) => inFlightIds.has(j.id)), ...currentSavedJobs].sort((a, b) => b.createdAt - a.createdAt)
        for (const job of this.jobs) {
          if (job.voiceApply === 'running') this._followVoice(job)
        }
      } catch (cause) {
        if (isCurrent()) throw cause
      } finally {
        if (isCurrent()) {
          pendingHistoryEdits.delete(this)
          this.historyLoaded = true
        }
      }
    },
    _trackJob(trackId: number): Yue2Job | undefined {
      return this.jobs.find((job) => job.dbId === trackId)
    },
    _onVoice(trackId: number, row: ApplyStatus) {
      const live = this._trackJob(trackId)
      if (!live) return
      // The polling helper follows its terminal callback with a URL-only
      // result. Keep that measured receipt; a new active job must reset it.
      if (row.job_progress !== undefined || live.voiceProgress?.status !== row.status || row.status === 'queued' || row.status === 'running') live.voiceProgress = row.job_progress ?? null
      if (row.status === 'done') {
        if (row.audio_url) {
          if (live.audioUrl?.startsWith('blob:')) URL.revokeObjectURL(live.audioUrl)
          live.audioUrl = row.audio_url
        }
        live.voiceApply = 'done'
        live.voicePhase = ''
        if (row.voice_id) live.voiceId = row.voice_id
        if (row.voice_name) live.voiceName = row.voice_name
        rememberVoiceUsed(trackId, live.voiceId || '', live.voiceName || '')
        forgetVoiceClock(trackId)
        return
      }
      if (row.status === 'failed' || row.status === 'cancelled' || row.status === 'idle') {
        live.voiceApply = 'failed'
        live.voicePhase = ''
        live.voiceErrorCode = row.error_code || 'interrupted'
        live.voiceError = row.error || ''
        forgetVoiceClock(trackId)
        return
      }
      const note = noteVoice(trackId, row)
      live.voiceApply = 'running'
      live.voicePhase = note.voicePhase
      live.voiceId = note.voiceId
      live.voiceName = note.voiceName
      live.voiceStartedAt = note.voiceStartedAt
    },
    async _savedVoice(trackId: number, audioUrl: string, isCurrent: () => boolean): Promise<{
      voiceApply?: Yue2Job['voiceApply']
      voicePhase?: string
      voiceId?: string
      voiceName?: string
      voiceStartedAt?: number
      voiceProgress?: VoiceJobProgress | null
      voiceError?: string
      voiceErrorCode?: string
      audioUrl?: string
    }> {
      try {
        const row = await applyStatus(trackId)
        if (!isCurrent()) return {}
        if (row.status === 'running' || row.status === 'queued') {
          const note = noteVoice(trackId, row)
          return { voiceApply: 'running', ...note }
        }
        if (row.status === 'done') {
          const used = voiceUsed(trackId)
          return {
            voiceApply: 'done',
            audioUrl: row.audio_url || audioUrl,
            voiceId: row.voice_id || used?.voiceId,
            voiceName: row.voice_name || used?.voiceName,
          }
        }
        if (row.status === 'failed' || row.status === 'cancelled') {
          return { voiceApply: 'failed', voiceError: row.error, voiceErrorCode: row.error_code }
        }
      } catch {
        // A track with no voice step still lists normally.
      }
      return {}
    },
    _followVoice(job: Yue2Job) {
      if (job.dbId == null || voiceWatchActive(job.dbId)) return
      const trackId = job.dbId
      markVoiceWatch(trackId)
      const controller = new AbortController()
      this._voiceAborters[trackId] = controller
      const clock = voiceClock(trackId)
      if (clock) {
        job.voiceId = job.voiceId || clock.voiceId
        job.voiceName = job.voiceName || clock.voiceName
        job.voiceStartedAt = job.voiceStartedAt || clock.startedAt
      }
      void waitForVoiceApply(trackId, (row) => {
        if (!controller.signal.aborted) this._onVoice(trackId, row)
      }, controller.signal).then((url) => {
        if (controller.signal.aborted) return
        this._onVoice(trackId, { status: 'done', audio_url: url, error: '', error_code: '', phase: '' })
      }).catch((err) => {
        if (controller.signal.aborted) return
        this._onVoice(trackId, {
          status: 'failed',
          audio_url: '',
          error: err instanceof VoiceApplyError ? err.detail : (err instanceof Error ? err.message : String(err)),
          error_code: err instanceof VoiceApplyError ? err.code : '',
          phase: '',
        })
      }).finally(() => {
        if (this._voiceAborters[trackId] !== controller) return
        delete this._voiceAborters[trackId]
        clearVoiceWatch(trackId)
      })
    },
    async refreshHealth(context?: PollContext) {
      try {
        const health = await api.health(context?.signal)
        if (context && !context.isCurrent()) return
        this.health = health
        this.healthError = false
      } catch {
        if (!context || context.isCurrent()) this.healthError = true
      }
    },
    startBackgroundTasks() {
      let loop = healthLoops.get(this)
      if (!loop) {
        loop = createPollingLoop((context) => this.refreshHealth(context), HEALTH_MS)
        healthLoops.set(this, loop)
      }
      loop.start()
    },
    stopBackgroundTasks() {
      this._historyGeneration++
      pendingHistoryEdits.delete(this)
      healthLoops.get(this)?.stop()
      for (const [trackId, controller] of Object.entries(this._voiceAborters)) {
        controller.abort()
        delete this._voiceAborters[Number(trackId)]
        clearVoiceWatch(Number(trackId))
      }
    },
    async generateBatch(params: { lyrics: string; style: string; cot: CotMode; precision: 'q8_0' | 'q4_0'; baseSeed: number; randomSeed: boolean; batchSize: number; options: GenerateOptions }) {
      const newJobs: Yue2Job[] = []
      for (let i = 0; i < params.batchSize; i++) {
        const seed = params.randomSeed ? Math.floor(Math.random() * 2147483647) : params.baseSeed + i
        newJobs.push({
          id: `g_${Date.now()}_${i}`,
          status: 'queued',
          createdAt: Date.now(),
          title: params.style,
          style: params.style,
          lyrics: params.lyrics,
          cot: params.cot,
          precision: params.precision,
          seed,
          finalized: false,
          params: { ...params.options, cot: params.cot, precision: params.precision, style: params.style, lyrics: params.lyrics },
        })
      }
      this.jobs.unshift(...newJobs)
      for (const { id } of newJobs) {
        // Look the job back up through the reactive `jobs` array instead of
        // mutating the raw object still held in `newJobs`: Pinia/Vue only
        // tracks changes made through the reactive proxy, so mutating the
        // original (pre-unshift) reference never triggers a re-render even
        // though the same data ends up saved to the server correctly.
        const job = this.jobs.find((j) => j.id === id)
        if (job && job.status === 'queued' && !job.finalized) await this._generateOne(job, params.options)
      }
    },
    async _generateOne(job: Yue2Job, options: GenerateOptions) {
      if (job.finalized || job.status === 'cancelled') return
      job.status = 'running'
      const aborter = new AbortController()
      this._aborters[job.id] = aborter
      try {
        const started = performance.now()
        const result = await api.generateTrack(job.lyrics, job.seed, options, job.precision, aborter.signal)
        if (aborter.signal.aborted) return
        const wallMs = result.timing?.wall_ms ?? performance.now() - started
        const durationMs = result.timing?.audio_duration_ms
        if (typeof result.audio !== 'string') throw new Error(t('storeErrors.serverNoAudio'))
        const blob = api.base64AudioBlob(result.audio)
        const abcPlan = api.abcFromResult(result)
        job.audioUrl = URL.createObjectURL(blob)
        job.wallSec = wallMs / 1000
        job.durationSec = durationMs ? durationMs / 1000 : null
        job.abcPlan = abcPlan || null
        job.status = 'done'
        job.finalized = true

        try {
          const saved = await tracksApi.saveTrack(
            {
              model: 'yue2',
              title: job.title,
              lyrics: job.lyrics,
              seed: job.seed,
              duration_ms: durationMs,
              wall_ms: wallMs,
              params: job.params || { cot: job.cot, precision: job.precision },
            },
            blob,
            'wav',
            abcPlan || null,
          )
          job.savedFilename = saved.filename
          job.saveError = null
          job.dbId = saved.id
          job.shortId = saved.short_id ?? null
          const voiceId = getActiveVoiceId()
          if (voiceId) {
            const voiceName = await voiceNameFor(voiceId)
            const startedAt = Date.now()
            rememberVoiceClock(saved.id, { startedAt, voiceId, voiceName })
            const arm = this._trackJob(saved.id)
            if (arm) {
              arm.voiceApply = 'running'
              arm.voiceError = ''
              arm.voiceErrorCode = ''
              arm.voiceId = voiceId
              arm.voiceName = voiceName
              arm.voiceStartedAt = startedAt
              arm.voicePhase = ''
            }
            markVoiceWatch(saved.id)
            try {
              const voiced = await applyVoiceAndWait(voiceId, saved.id, (row) => {
                this._onVoice(saved.id, row)
              })
              this._onVoice(saved.id, { status: 'done', audio_url: voiced, error: '', error_code: '', phase: '' })
            } catch (voiceErr) {
              this._onVoice(saved.id, {
                status: 'failed',
                audio_url: '',
                error: voiceErr instanceof VoiceApplyError ? voiceErr.detail : (voiceErr instanceof Error ? voiceErr.message : String(voiceErr)),
                error_code: voiceErr instanceof VoiceApplyError ? voiceErr.code : '',
                phase: '',
              })
            } finally {
              clearVoiceWatch(saved.id)
            }
          }
        } catch (err) {
          job.savedFilename = null
          job.saveError = err instanceof Error ? err.message : String(err)
          job.dbId = null
        }
      } catch (err) {
        if (aborter.signal.aborted || (err instanceof DOMException && err.name === 'AbortError')) {
          job.status = 'cancelled'
        } else {
          job.status = 'failed'
          job.error = err instanceof Error ? err.message : String(err)
        }
        job.finalized = true
      } finally {
        delete this._aborters[job.id]
      }
    },
    cancel(jobId: string) {
      const job = this.jobs.find((row) => row.id === jobId)
      if (!job || job.finalized) return
      job.status = 'cancelled'
      job.finalized = true
      this._aborters[jobId]?.abort()
    },
    requestInsertAbc(abc: string) {
      this.pendingAbcInsert = abc
    },
    clearPendingAbcInsert() {
      this.pendingAbcInsert = null
    },
    async deleteJob(job: Yue2Job) {
      const trackId = job.dbId
      if (trackId != null) {
        await tracksApi.deleteTrack(trackId)
        pendingHistoryEdits.get(this)?.set(trackId, { kind: 'deleted' })
      }
      this.jobs = this.jobs.filter((j) => j.id !== job.id && (trackId == null || j.dbId !== trackId))
    },
    async renameJob(job: Yue2Job, title: string) {
      const trackId = job.dbId
      if (trackId == null) return
      const saved = await tracksApi.renameTrack(trackId, title)
      const edits = pendingHistoryEdits.get(this)
      if (edits?.get(trackId)?.kind !== 'deleted') edits?.set(trackId, { kind: 'renamed', title: saved.title })
      const target = this._trackJob(trackId)
      if (target) target.title = saved.title
    },
  },
})

// Without this, editing this file while the dev server is running leaves
// already-mounted components bound to a stale store instance (old action
// closures) instead of picking up the new code - looks like "it's fixed in
// a fresh tab but stuck in the one I already had open".
if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useYue2Store, import.meta.hot))
}
