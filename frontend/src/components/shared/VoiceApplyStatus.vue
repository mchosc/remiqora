<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ProgressBar from './ProgressBar.vue'
import { formatClock, voiceRemainingSec } from '../../composables/voicePace'

const props = defineProps<{
  phase?: string
  voiceName?: string
  startedAt?: number
  durationSec?: number | null
}>()

const { t, locale } = useI18n()
const now = ref(Date.now())
const timer = window.setInterval(() => {
  now.value = Date.now()
}, 1000)
onUnmounted(() => window.clearInterval(timer))

const phaseLine = computed(() => {
  const phase = props.phase
  if (phase === 'waiting') return t('voiceClone.applyingWait')
  if (phase === 'separating') return t('voiceClone.applyingSeparate')
  if (phase === 'preparing') return t('voiceClone.applyingPrepare')
  if (phase === 'converting') return t('voiceClone.applyingConvert')
  if (phase === 'mixing') return t('voiceClone.applyingMix')
  return props.voiceName
    ? t('voiceClone.applyingNamed', { name: props.voiceName })
    : t('voiceClone.applying')
})

const elapsedSec = computed(() => {
  if (!props.startedAt) return 0
  return Math.max(0, (now.value - props.startedAt) / 1000)
})

const leftSec = computed(() => voiceRemainingSec(elapsedSec.value, props.durationSec))

const finishLine = computed(() => {
  if (leftSec.value == null) return ''
  if (leftSec.value <= 0) return t('voiceClone.stillWorking')
  const when = new Date(now.value + leftSec.value * 1000)
  const time = when.toLocaleTimeString(locale.value === 'ru' ? 'ru-RU' : 'en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
  return t('voiceClone.finishesAt', { time, left: formatClock(leftSec.value) })
})
</script>

<template>
  <div class="space-y-1">
    <ProgressBar :value="null" />
    <p v-if="voiceName && phase" class="text-xs text-text">{{ t('voiceClone.applyingNamed', { name: voiceName }) }}</p>
    <p class="text-xs text-text-dim">{{ phaseLine }}</p>
    <p v-if="startedAt" class="text-xs text-text-dim">{{ t('voiceClone.elapsed', { time: formatClock(elapsedSec) }) }}</p>
    <p v-if="finishLine" class="text-xs text-text-dim">{{ finishLine }}</p>
  </div>
</template>
