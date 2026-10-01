<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ close: [] }>()
const { t } = useI18n()
const dialog = ref<HTMLElement | null>(null)
const closeButton = ref<HTMLButtonElement | null>(null)
let opener: HTMLElement | null = null
let focusGeneration = 0

function restoreFocus() {
  if (opener?.isConnected) opener.focus()
  opener = null
}
watch(() => props.open, async (open) => {
  const generation = ++focusGeneration
  if (!open) { restoreFocus(); return }
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  if (generation === focusGeneration && props.open) closeButton.value?.focus()
}, { immediate: true, flush: 'post' })

function onKeydown(e: KeyboardEvent) {
  if (!props.open || e.defaultPrevented) return
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('close')
    return
  }
  const currentDialog = dialog.value
  if (e.key !== 'Tab' || !currentDialog) return
  const focusable = [...currentDialog.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]')].filter((element) => {
    const style = getComputedStyle(element)
    return element.tabIndex >= 0 && !element.matches(':disabled') && !element.closest('[hidden], [inert]') && style.display !== 'none' && style.visibility !== 'hidden'
  })
  const first = focusable[0]
  const last = focusable.at(-1)
  if (!first || !last) { e.preventDefault(); currentDialog.focus(); return }
  const focused = document.activeElement
  if (!currentDialog.contains(focused) || (e.shiftKey && focused === first) || (!e.shiftKey && focused === last)) {
    e.preventDefault()
    const target = e.shiftKey ? last : first
    target.focus()
  }
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  ++focusGeneration
  window.removeEventListener('keydown', onKeydown)
  if (props.open) restoreFocus()
})
</script>

<template>
  <div v-if="open" class="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:items-center" @click.self="emit('close')">
    <div ref="dialog" role="dialog" aria-modal="true" :aria-label="title" tabindex="-1" class="my-8 w-full max-w-2xl rounded-xl border border-border bg-panel p-5 shadow-2xl">
      <div class="mb-4 flex items-center justify-between">
        <h3 class="text-lg font-semibold text-text">{{ title }}</h3>
        <button ref="closeButton" type="button" class="rounded-full p-1 text-text-dim hover:bg-panel-2 hover:text-text" :aria-label="t('common.close')" @click="emit('close')">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </div>
      <div class="max-h-[70vh] space-y-3 overflow-y-auto text-sm leading-relaxed text-text-dim">
        <slot />
      </div>
    </div>
  </div>
</template>
