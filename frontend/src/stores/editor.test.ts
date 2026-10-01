// @vitest-environment happy-dom
import { beforeEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useEditorStore } from './editor'

beforeEach(() => { setActivePinia(createPinia()) })

it('restores a validated undo snapshot while retaining the current zoom', () => {
  const store = useEditorStore()
  store.snapshot()
  store.addLane()
  store.setZoom(90)
  store.undo()
  expect(store.project.lanes).toHaveLength(2)
  expect(store.project.pxPerSecond).toBe(90)
  expect(store.project.master).toBeDefined()
})

it('keeps the current project when an undo snapshot is corrupt', () => {
  const store = useEditorStore()
  const original = store.project
  store.history = ['{"lanes":"invalid"}']
  expect(() => store.restoreHistory(0)).not.toThrow()
  expect(store.project).toBe(original)
  expect(store.error).toBeTruthy()
})
