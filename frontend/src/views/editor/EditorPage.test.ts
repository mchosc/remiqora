// @vitest-environment happy-dom
import { afterEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import EditorPage from './EditorPage.vue'
import { useEditorStore } from '../../stores/editor'
import { i18n } from '../../i18n'
import * as tracksApi from '../../api/tracks'
import { decodeStem } from '../../audio/mixerEngine'
import type { SavedTrack } from '../../api/contracts'

const engine = vi.hoisted(() => ({
  ensureGraph: vi.fn(), applySettings: vi.fn(), decodeAll: vi.fn(async () => new Map<string, AudioBuffer>()),
  play: vi.fn(async () => {}), stop: vi.fn(), teardown: vi.fn(), render: vi.fn(),
  getLaneLevel: vi.fn(() => ({ peak: 0, clipping: false, peakL: 0, peakR: 0 })),
  getMasterLevel: vi.fn(() => ({ peak: 0, clipping: false, peakL: 0, peakR: 0 })),
}))
vi.mock('../../composables/useTimelineEngine', () => ({ useTimelineEngine: () => engine }))
vi.mock('../../composables/audioPlayback', () => ({ getSharedAudioCtx: () => ({ currentTime: 0 }) }))
vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), uploadTrack: vi.fn() }))
vi.mock('../../audio/mixerEngine', async (original) => ({ ...await original<typeof import('../../audio/mixerEngine')>(), decodeStem: vi.fn() }))

let app: App | undefined
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); vi.unstubAllGlobals() })

async function mountEditor() {
  vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  const pinia = createPinia()
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/editor', component: { render: () => null } }, { path: '/editor/:id', component: EditorPage, props: true }] })
  await router.push('/editor/new')
  app = createApp({ render: () => h(RouterView) })
  app.use(pinia).use(router).use(i18n)
  const container = document.createElement('div')
  document.body.append(container)
  app.mount(container)
  await nextTick()
  await nextTick()
  const store = useEditorStore(pinia)
  return { store, router, container }
}

it('restarts playback for the first lane-count change in a fresh new editor', async () => {
  const { store } = await mountEditor()
  expect(engine.ensureGraph).toHaveBeenCalledWith(2)
  store.playing = true
  store.addLane()
  await nextTick()
  expect(engine.ensureGraph).toHaveBeenCalledWith(3)
  expect(engine.play).toHaveBeenCalledTimes(1)
})

it('ignores a dropped-audio upload that finishes after leaving the editor', async () => {
  let release: (track: SavedTrack) => void = () => { throw new Error('Not initialized') }
  const upload = new Promise<SavedTrack>((resolve) => { release = resolve })
  vi.mocked(tracksApi.uploadTrack).mockReturnValue(upload)
  const { store, router, container } = await mountEditor()
  const lane = container.querySelector('.relative.isolate')
  if (!(lane instanceof HTMLElement)) throw new Error('Missing audio drop zone')
  const transfer = new DataTransfer()
  transfer.items.add(new File(['audio'], 'song.wav', { type: 'audio/wav' }))
  const drop = new DragEvent('drop', { bubbles: true, dataTransfer: transfer })
  Object.defineProperty(drop, 'dataTransfer', { value: transfer })
  expect(drop.dataTransfer?.files.length).toBe(1)
  lane.dispatchEvent(drop)
  expect(tracksApi.uploadTrack).toHaveBeenCalledTimes(1)
  await router.push('/editor')
  store.error = 'Current session warning'
  release({ id: 1, short_id: 1, title: 'Dropped', created_at: '2026-10-01T10:00:00Z', model: 'upload', lyrics: '', seed: null, duration_ms: null, wall_ms: null, params: {}, filename: 'song.wav', audio_url: '/api/tracks/1/audio', abc_url: null, stems: null, midi: null })
  await nextTick()
  await nextTick()
  expect(decodeStem).not.toHaveBeenCalled()
  expect(store.error).toBe('Current session warning')
  expect(store.project.lanes.every((track) => track.clips.length === 0)).toBe(true)
})
