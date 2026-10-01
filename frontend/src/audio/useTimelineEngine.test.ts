import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as audioPlayback from '../composables/audioPlayback'
import { useTimelineEngine } from '../composables/useTimelineEngine'
import { defaultChannelSettings, defaultMasterSettings } from './mixerEngine'
import type { TimelineProject } from './timelineTypes'
import { TestAudioBuffer, TestAudioContext } from './testWebAudio'

beforeEach(() => {
  TestAudioContext.instances = []
  vi.stubGlobal('AudioContext', TestAudioContext)
  vi.stubGlobal('AudioBuffer', TestAudioBuffer)
  vi.spyOn(audioPlayback, 'getSharedAudioCtx').mockReturnValue(new AudioContext())
})

afterEach(() => vi.unstubAllGlobals())

function project(laneCount = 1): TimelineProject {
  return {
    version: 1, bpm: 120, pxPerSecond: 100, snapEnabled: true,
    master: defaultMasterSettings(),
    lanes: Array.from({ length: laneCount }, (_, index) => ({
      id: String(index), name: `Lane ${index}`, settings: defaultChannelSettings(),
      clips: [{ id: `clip-${index}`, sourceUrl: '/audio.wav', sourceLabel: 'Audio', timelineStart: 0, trimStart: 0, trimEnd: 4 }],
    })),
  }
}

function buffers(): Map<string, AudioBuffer> {
  return new Map([['/audio.wav', new AudioBuffer({ numberOfChannels: 1, length: 32000, sampleRate: 8000 })]])
}

it('stops and disconnects old sources before rebuilding the lane graph', async () => {
  const engine = useTimelineEngine()
  await engine.play(project(), buffers(), 0, () => {})
  const context = TestAudioContext.instances[0]
  const oldNodes = [...context.nodes]
  const oldSource = oldNodes.find((node) => node.kind === 'buffer-source')

  engine.ensureGraph(2)

  expect(oldSource?.stopCalls).toHaveLength(1)
  expect(oldSource?.onended).toBeNull()
  expect(oldNodes.filter((node) => node.kind !== 'destination').every((node) => node.disconnectCalls > 0)).toBe(true)
  engine.teardown()
})

it('invalidates an awaited resume when its graph is replaced', async () => {
  const engine = useTimelineEngine()
  const context = TestAudioContext.instances[0]
  let resolveResume = (): void => {}
  context.resumeResult = new Promise<void>((resolve) => { resolveResume = resolve })
  const pending = engine.play(project(), buffers(), 0, () => {})

  engine.ensureGraph(2)
  resolveResume()
  await pending

  expect(context.nodes.filter((node) => node.kind === 'buffer-source')).toHaveLength(0)
  engine.teardown()
})

it('schedules replacement playback on all rebuilt lanes from the caller playhead', async () => {
  const engine = useTimelineEngine()
  const clipBuffers = buffers()
  const onEnded = vi.fn()
  await engine.play(project(), clipBuffers, 0, onEnded)
  await engine.play(project(2), clipBuffers, 1.5, onEnded)
  const context = TestAudioContext.instances[0]
  const sources = context.nodes.filter((node) => node.kind === 'buffer-source')

  expect(sources[0]?.connections.size).toBe(0)
  expect(sources[0]?.stopCalls).toHaveLength(1)
  expect(sources.slice(1).map((node) => node.startCalls[0]?.slice(1))).toEqual([[1.5, 2.5], [1.5, 2.5]])
  expect(sources.slice(1).every((node) => node.connections.size === 1)).toBe(true)
  sources.slice(1).find((node) => node.onended)?.onended?.()
  expect(onEnded).toHaveBeenCalledTimes(1)
  engine.teardown()
})
