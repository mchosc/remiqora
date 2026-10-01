// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createApp, h, nextTick, type App } from 'vue'
import * as api from '../../api/videos'
import * as tracks from '../../api/tracks'
import { parseVideoProject, type VideoProject, type VideoShotDraft } from '../../api/contracts'
import { useVideoWorkspace } from './useVideoWorkspace'
import { videoProjectFixture, videoTrack, videoReadinessFixture } from './videoFixtures'

vi.mock('../../api/tracks', async (original) => ({ ...await original<typeof import('../../api/tracks')>(), listTracks: vi.fn() }))
vi.mock('../../api/videos', async (original) => ({ ...await original<typeof import('../../api/videos')>(), listVideoProjects: vi.fn(), getVideoProject: vi.fn(), listVideos: vi.fn(), otherWorkBusy: vi.fn(), videoReadiness: vi.fn(), createVideoProject: vi.fn(), updateVideoProject: vi.fn(), analyzeVideoProject: vi.fn(), previewVideoProject: vi.fn(), renderVideoProject: vi.fn(), cancelVideoProject: vi.fn(), duplicateVideoProject: vi.fn(), uploadVideoReference: vi.fn(), approveVideoVariant: vi.fn(), resumeVideoProject: vi.fn(), exportVideoProject: vi.fn() }))
let app: App | undefined
let project: VideoProject
beforeEach(() => {
  vi.useFakeTimers()
  project = videoProjectFixture()
  vi.mocked(tracks.listTracks).mockResolvedValue([videoTrack])
  vi.mocked(api.listVideos).mockResolvedValue({ videos: [] })
  vi.mocked(api.otherWorkBusy).mockResolvedValue(false)
  vi.mocked(api.videoReadiness).mockResolvedValue(videoReadinessFixture)
  vi.mocked(api.listVideoProjects).mockImplementation(async () => ({ projects: [project] }))
  vi.mocked(api.getVideoProject).mockImplementation(async () => project)
  vi.mocked(api.updateVideoProject).mockImplementation(async (_id, body) => { project = parseVideoProject({ ...project, ...body, revision: project.revision + 1, shots: body.shots ?? project.shots }); return project })
  vi.mocked(api.cancelVideoProject).mockImplementation(async () => ({ ...project, revision: project.revision + 1, job: project.job ? { ...project.job, status: 'cancelled' } : null }))
})
afterEach(() => { app?.unmount(); app = undefined; document.body.replaceChildren(); sessionStorage.clear(); vi.clearAllMocks(); vi.useRealTimers() })
async function flush() { for (let i = 0; i < 12; i++) await nextTick() }
async function mount() {
  let workspace: ReturnType<typeof useVideoWorkspace> | undefined
  app = createApp({ setup() { workspace = useVideoWorkspace(); return () => h('div') } })
  app.mount(document.body.appendChild(document.createElement('div')))
  await flush()
  if (!workspace) throw new Error('Workspace did not mount')
  return workspace
}
function currentDraft(workspace: ReturnType<typeof useVideoWorkspace>) {
  if (!workspace.draft.value) throw new Error('Missing draft')
  return workspace.draft.value
}
it('cancels an active job without trying to save or discarding a recovered draft', async () => {
  project.job = { id: 'd'.repeat(32), status: 'running', operation: 'preview' }
  sessionStorage.setItem(`remiqora:video-draft:${project.id}`, JSON.stringify({ revision: 1, direction: 'Keep my recovered edit', shots: project.shots?.map(({ variants: _variants, approved_variant_id: _approval, ...shot }) => shot) }))
  const workspace = await mount()
  expect(workspace.dirty.value).toBe(true)
  await workspace.cancel()
  expect(api.cancelVideoProject).toHaveBeenCalledWith(project.id, expect.any(AbortSignal))
  expect(api.updateVideoProject).not.toHaveBeenCalled()
  expect(workspace.project.value?.job?.status).toBe('cancelled')
  expect(currentDraft(workspace).direction).toBe('Keep my recovered edit')
  expect(workspace.dirty.value).toBe(true)
  expect(JSON.parse(sessionStorage.getItem(`remiqora:video-draft:${project.id}`) || '{}')).toMatchObject({ revision: 2, direction: 'Keep my recovered edit' })
})
it('selects an existing shot after undo removes the selected newly added shot', async () => {
  const workspace = await mount()
  workspace.addShot()
  expect(currentDraft(workspace).shots.some((shot) => shot.id === workspace.selectedShotId.value)).toBe(true)
  workspace.undo()
  expect(workspace.selectedShot.value?.id).toBe('b'.repeat(32))
})
it('blocks undo and new shot selection while a job is active', async () => {
  const workspace = await mount()
  workspace.addShot()
  const before: VideoShotDraft[] = currentDraft(workspace).shots.map((shot) => ({ ...shot }))
  const selected = workspace.selectedShotId.value
  workspace.project.value = { ...project, job: { id: 'd'.repeat(32), status: 'running', operation: 'preview' } }
  workspace.undo()
  workspace.addShot()
  expect(currentDraft(workspace).shots).toEqual(before)
  expect(workspace.selectedShotId.value).toBe(selected)
})
it('owns project selection until its response arrives and blocks old-project edits', async () => {
  const workspace = await mount()
  const second = videoProjectFixture('e'.repeat(32))
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.getVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.selectProject(second.id)
  await flush()
  expect(workspace.readOnly.value).toBe(true)
  workspace.addShot()
  expect(currentDraft(workspace).shots).toHaveLength(2)
  release(second)
  await pending
  expect(workspace.project.value?.id).toBe(second.id)
  expect(workspace.readOnly.value).toBe(false)
})
it('repairs selected shot and checked preview identities after a re-analysis replaces unlocked shots', async () => {
  const workspace = await mount()
  const replacement: VideoProject = { ...project, revision: 2, shots: [{ id: 'f'.repeat(32), start_sec: 0, seconds: 4, prompt: 'New suggested scene' }] }
  vi.mocked(api.analyzeVideoProject).mockResolvedValue(replacement)
  await workspace.analyze()
  expect(workspace.selectedShot.value?.id).toBe('f'.repeat(32))
  expect(workspace.selectedPreviewIds.value).toEqual(['f'.repeat(32)])
  expect(workspace.step.value).toBe('storyboard')
})
it('saves the full selected direction, shot, timing and export settings before rendering', async () => {
  const workspace = await mount()
  const draft = currentDraft(workspace)
  draft.name = 'Reviewed video'
  draft.direction = 'A recurring subject in warm light'
  draft.seed = 42
  draft.settings = { engine_pack: 'ltx25', width: 1280, height: 704, stage1_steps: 50, stage2_steps: 1, cfg_scale: 4.5, negative_prompt: 'blur' }
  draft.export_settings = { aspect: 'square', quality: 'high', include_overlays: true }
  const first = draft.shots[0]
  if (!first) throw new Error('Missing shot')
  first.seed = 123
  first.reference_id = 'f'.repeat(32)
  first.reference_strength = .25
  first.locked = true
  draft.markers = [{ id: '1'.repeat(32), kind: 'manual', time_sec: 4, label: 'Drop', confidence: 1 }]
  draft.overlays = [{ id: '2'.repeat(32), kind: 'lyric', text: 'Reviewed timed line', start_sec: 1, end_sec: 3, position: 'top', font_size: 48, color: '#ff0000' }]
  vi.mocked(api.renderVideoProject).mockImplementation(async () => project)
  await workspace.render()
  expect(api.updateVideoProject).toHaveBeenCalledWith(project.id, expect.objectContaining({ revision: 1, name: draft.name, direction: draft.direction, seed: 42, settings: draft.settings, export_settings: draft.export_settings, markers: draft.markers, overlays: draft.overlays, shots: expect.arrayContaining([expect.objectContaining({ seed: 123, reference_id: 'f'.repeat(32), reference_strength: .25, locked: true })]) }), expect.any(AbortSignal))
  expect(api.renderVideoProject).toHaveBeenCalledWith(project.id, { revision: 2, reuse_completed: true }, expect.any(AbortSignal))
  expect(workspace.step.value).toBe('export')
  expect(workspace.dirty.value).toBe(false)
})
it('keeps unsaved changes and blocks analysis when the save fails', async () => {
  const workspace = await mount()
  currentDraft(workspace).direction = 'Recover this scene'
  vi.mocked(api.updateVideoProject).mockRejectedValue(new Error('fixture save failed'))
  await workspace.analyze()
  expect(api.analyzeVideoProject).not.toHaveBeenCalled()
  expect(workspace.dirty.value).toBe(true)
  expect(currentDraft(workspace).direction).toBe('Recover this scene')
  expect(workspace.saveError.value).toBe('fixture save failed')
  expect(workspace.acting.value).toBe(false)
})
it('discards a malformed browser draft while retaining the saved storyboard', async () => {
  sessionStorage.setItem(`remiqora:video-draft:${project.id}`, JSON.stringify({ revision: 1, seed: 'not a number', shots: [] }))
  const workspace = await mount()
  expect(workspace.dirty.value).toBe(false)
  expect(currentDraft(workspace).shots).toHaveLength(2)
})
it('recovers a partial browser draft with saved shots and explicit backend defaults', async () => {
  sessionStorage.setItem(`remiqora:video-draft:${project.id}`, JSON.stringify({ revision: 1, name: 'Recovered name', settings: { cfg_scale: 4 }, export_settings: { quality: 'high' } }))
  const workspace = await mount()
  expect(workspace.dirty.value).toBe(true)
  expect(currentDraft(workspace).name).toBe('Recovered name')
  expect(currentDraft(workspace).shots).toHaveLength(2)
  expect(currentDraft(workspace).settings).toMatchObject({ engine_pack: 'ltx23', width: 704, height: 448, stage1_steps: 30, stage2_steps: 3, cfg_scale: 4 })
  expect(currentDraft(workspace).export_settings).toEqual({ aspect: 'landscape', quality: 'high', include_overlays: true })
})
it('selects a recoverable draft shot when the first saved shot was removed locally', async () => {
  const second = project.shots?.[1]
  if (!second) throw new Error('Missing saved shot')
  const { variants: _variants, approved_variant_id: _approval, ...shot } = second
  sessionStorage.setItem(`remiqora:video-draft:${project.id}`, JSON.stringify({ revision: 1, shots: [shot] }))
  const workspace = await mount()
  expect(workspace.selectedShot.value?.id).toBe(second.id)
  expect(workspace.selectedPreviewIds.value).toEqual([second.id])
})
it('does not activate a completed duplicate response after teardown', async () => {
  const workspace = await mount()
  const duplicate = videoProjectFixture('e'.repeat(32))
  let release: (row: VideoProject) => void = () => { throw new Error('Not initialized') }
  vi.mocked(api.duplicateVideoProject).mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  const pending = workspace.duplicate()
  await flush()
  app?.unmount(); app = undefined
  release(duplicate)
  await pending
  expect(workspace.project.value?.id).toBe(project.id)
  expect(vi.getTimerCount()).toBe(0)
})
it('uploads only supported reference files and preserves the reviewed revision', async () => {
  const workspace = await mount()
  await workspace.upload(new File(['text'], 'lyrics.txt', { type: 'text/plain' }))
  expect(api.uploadVideoReference).not.toHaveBeenCalled()
  expect(workspace.error.value).toBe('invalid_reference')
  vi.mocked(api.uploadVideoReference).mockResolvedValue({ ...project, revision: 2 })
  const image = new File(['fixture image'], 'cover.png', { type: 'image/png' })
  await workspace.upload(image)
  expect(api.uploadVideoReference).toHaveBeenCalledWith(project.id, 1, image, expect.any(AbortSignal))
  expect(workspace.project.value?.revision).toBe(2)
})
