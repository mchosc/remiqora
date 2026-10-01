<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAceStepStore } from '../../stores/aceStep'
import * as api from '../../api/aceStep'
import type { GenerateMusicRequest } from '../../api/aceStep'
import { useLoraRegistry } from '../../composables/useLoraRegistry'
import ChipGroup from '../../components/shared/ChipGroup.vue'
import CollapsibleDetails from '../../components/shared/CollapsibleDetails.vue'
import HelpModal from '../../components/shared/HelpModal.vue'
import TagInput from '../../components/shared/TagInput.vue'
import VoiceSelect from '../../components/shared/VoiceSelect.vue'
import { ApiError } from '../../api/http'
import { voiceErrorText, VoiceApplyError } from '../../api/voices'
import { estimateTrackDuration } from '../../composables/estimateDuration'
import { parseAcePresets, finiteNumber, type AcePreset } from '../../composables/generationPresets'

const store = useAceStepStore()
const props = withDefaults(defineProps<{ generationAvailable?: boolean | null }>(), { generationAvailable: true })
const { t, tm } = useI18n()
const { loras, add: addLora, remove: removeLora } = useLoraRegistry()

type Mode = 'simple' | 'custom'
type TaskType = 'cover' | 'repaint' | 'extract' | 'lego' | 'complete' | 'voice_replacement'

const presets = ref<AcePreset[]>([])
const selectedPresetName = ref('')
const newPresetName = ref('')
const showPresetInput = ref(false)

function loadPresets() {
  try {
    const raw = localStorage.getItem('acestep_presets')
    if (raw) {
      const value: unknown = JSON.parse(raw)
      presets.value = parseAcePresets(value)
    }
  } catch {}
}

function saveCurrentPreset() {
  const name = newPresetName.value.trim()
  if (!name) return
  const preset: AcePreset = {
    name,
    mode: mode.value,
    simpleQuery: simpleQuery.value,
    customPrompt: customPrompt.value,
    instrumental: instrumental.value,
    customLyrics: customLyrics.value,
    duration: duration.value,
    durationAuto: durationAuto.value,
    audioFormat: audioFormat.value,
    bpm: bpm.value,
    keyScale: keyScale.value,
    timeSignature: timeSignature.value,
    vocalLanguage: vocalLanguage.value,
    inferenceSteps: inferenceSteps.value,
    guidanceScale: guidanceScale.value,
    selectedModel: selectedModel.value,
  }
  const idx = presets.value.findIndex((p) => p.name === preset.name)
  if (idx !== -1) presets.value[idx] = preset
  else presets.value.push(preset)
  localStorage.setItem('acestep_presets', JSON.stringify(presets.value))
  selectedPresetName.value = name
  newPresetName.value = ''
  showPresetInput.value = false
}

function applyPreset(name: string) {
  const p = presets.value.find((x) => x.name === name)
  if (!p) return
  mode.value = p.mode || 'simple'
  simpleQuery.value = p.simpleQuery || ''
  customPrompt.value = p.customPrompt || ''
  instrumental.value = !!p.instrumental
  customLyrics.value = p.customLyrics || ''
  if (p.duration) {
    duration.value = p.duration
    durationTouched.value = true
  }
  durationAuto.value = p.durationAuto === true
  if (p.audioFormat) audioFormat.value = p.audioFormat
  bpm.value = p.bpm ?? null
  keyScale.value = p.keyScale || ''
  timeSignature.value = p.timeSignature || ''
  vocalLanguage.value = p.vocalLanguage || ''
  inferenceSteps.value = p.inferenceSteps ?? null
  guidanceScale.value = p.guidanceScale ?? null
  if (p.selectedModel) selectedModel.value = p.selectedModel
}

function deletePreset(name: string) {
  presets.value = presets.value.filter((p) => p.name !== name)
  localStorage.setItem('acestep_presets', JSON.stringify(presets.value))
  if (selectedPresetName.value === name) selectedPresetName.value = ''
}

onMounted(loadPresets)

watch(
  () => store.pendingParamsInsert,
  (params) => {
    if (!params) return
    if (typeof params.prompt === 'string' && params.prompt) {
      mode.value = 'custom'
      customPrompt.value = params.prompt
    } else if (typeof params.sample_query === 'string' || typeof params.query === 'string') {
      mode.value = 'simple'
      simpleQuery.value = typeof params.sample_query === 'string' ? params.sample_query : (typeof params.query === 'string' ? params.query : '')
    }
    if (typeof params.lyrics === 'string') customLyrics.value = params.lyrics
    if (typeof params.instrumental === 'boolean') instrumental.value = params.instrumental
    if (finiteNumber(params.audio_duration)) {
      if (params.audio_duration > 0) {
        durationTouched.value = true
        duration.value = Math.min(300, Math.max(10, params.audio_duration))
        durationAuto.value = false
      } else durationAuto.value = true
    } else if (finiteNumber(params.duration)) {
      durationTouched.value = true
      duration.value = Math.min(300, Math.max(10, params.duration))
      durationAuto.value = false
    }
    if (params.audio_format === 'mp3' || params.audio_format === 'wav' || params.audio_format === 'flac') audioFormat.value = params.audio_format
    if (finiteNumber(params.bpm)) bpm.value = params.bpm
    if (typeof params.key_scale === 'string') keyScale.value = params.key_scale
    if (typeof params.time_signature === 'string') timeSignature.value = params.time_signature
    if (typeof params.vocal_language === 'string') vocalLanguage.value = params.vocal_language
    if (finiteNumber(params.inference_steps)) inferenceSteps.value = params.inference_steps
    if (finiteNumber(params.guidance_scale)) guidanceScale.value = params.guidance_scale
    if (finiteNumber(params.seed)) seedValue.value = params.seed
    if (typeof params.model === 'string') selectedModel.value = params.model
    store.clearPendingParamsInsert()
  },
)

const mode = ref<Mode>('simple')
const simpleQuery = ref('')
const customPrompt = ref('')
const instrumental = ref(false)
const customLyrics = ref('')

const useRefAudio = ref(false)
const refAudioFile = ref<File | null>(null)
const taskType = ref<TaskType>('cover')
const selectedVoiceId = ref<string | null>(null)
const isVoiceReplacement = computed(() => useRefAudio.value && taskType.value === 'voice_replacement')
watch(() => props.generationAvailable, (available) => {
  if (available === false) { useRefAudio.value = true; taskType.value = 'voice_replacement' }
}, { immediate: true })
const repaintStart = ref<number | null>(null)
const repaintEnd = ref<number | null>(null)
const trackName = ref('vocals')
const trackClasses = ref<string[]>([])
const coverStrength = ref(1)

const duration = ref(120)
const durationAuto = ref(true)
const durationTouched = ref(false)
const batchSize = ref<1 | 2 | 4>(1)

const audioFormat = ref<'mp3' | 'wav' | 'flac'>('mp3')
const bpm = ref<number | null>(null)
const keyScale = ref('')
const timeSignature = ref('')
const vocalLanguage = ref('')
const inferenceSteps = ref<number | null>(null)
const inferenceStepsTouched = ref(false)
const guidanceScale = ref<number | null>(null)
const seedValue = ref<number | null>(null)
const selectedModel = ref('')

const selectedLoraPath = ref('')
const loraScaleVal = ref(1)
const loraStatus = ref('')
const newLoraName = ref('')
const newLoraPath = ref('')

const submitting = ref(false)
const formError = ref('')
const helpOpen = ref<null | 'style' | 'lyrics' | 'remix' | 'advanced'>(null)

const TASK_TYPES = computed<{ value: TaskType; label: string }[]>(() => [
  { value: 'voice_replacement', label: t('aceGen.taskTypes.voice_replacement') },
  { value: 'cover', label: t('aceGen.taskTypes.cover') },
  { value: 'repaint', label: t('aceGen.taskTypes.repaint') },
  { value: 'extract', label: t('aceGen.taskTypes.extract') },
  { value: 'lego', label: t('aceGen.taskTypes.lego') },
  { value: 'complete', label: t('aceGen.taskTypes.complete') },
])
const TRACK_NAME_OPTIONS = ['vocals', 'drums', 'bass', 'guitar', 'piano', 'keys', 'strings', 'brass', 'woodwinds', 'synth', 'percussion', 'other']
const TIME_SIGNATURES = ['4/4', '3/4', '6/8', '2/4', '5/4', '7/8']
// Native self-names, not translated - matches constants.VALID_LANGUAGES in
// the ACE-Step API (external/ACE-Step-1.5/acestep/constants.py).
const VOCAL_LANGUAGES: { code: string; label: string }[] = [
  { code: 'en', label: 'English' },
  { code: 'ru', label: 'Русский' },
  { code: 'zh', label: '中文' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
  { code: 'es', label: 'Español' },
  { code: 'fr', label: 'Français' },
  { code: 'de', label: 'Deutsch' },
  { code: 'it', label: 'Italiano' },
  { code: 'pt', label: 'Português' },
  { code: 'nl', label: 'Nederlands' },
  { code: 'pl', label: 'Polski' },
  { code: 'uk', label: 'Українська' },
  { code: 'cs', label: 'Čeština' },
  { code: 'sk', label: 'Slovenčina' },
  { code: 'ro', label: 'Română' },
  { code: 'hu', label: 'Magyar' },
  { code: 'bg', label: 'Български' },
  { code: 'sr', label: 'Српски' },
  { code: 'hr', label: 'Hrvatski' },
  { code: 'sv', label: 'Svenska' },
  { code: 'no', label: 'Norsk' },
  { code: 'da', label: 'Dansk' },
  { code: 'fi', label: 'Suomi' },
  { code: 'is', label: 'Íslenska' },
  { code: 'el', label: 'Ελληνικά' },
  { code: 'tr', label: 'Türkçe' },
  { code: 'he', label: 'עברית' },
  { code: 'ar', label: 'العربية' },
  { code: 'fa', label: 'فارسی' },
  { code: 'ur', label: 'اردو' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'bn', label: 'বাংলা' },
  { code: 'pa', label: 'ਪੰਜਾਬੀ' },
  { code: 'ta', label: 'தமிழ்' },
  { code: 'te', label: 'తెలుగు' },
  { code: 'ne', label: 'नेपाली' },
  { code: 'sa', label: 'संस्कृतम्' },
  { code: 'th', label: 'ไทย' },
  { code: 'vi', label: 'Tiếng Việt' },
  { code: 'id', label: 'Indonesia' },
  { code: 'ms', label: 'Melayu' },
  { code: 'tl', label: 'Tagalog' },
  { code: 'sw', label: 'Kiswahili' },
  { code: 'az', label: 'Azərbaycan' },
  { code: 'lt', label: 'Lietuvių' },
  { code: 'ca', label: 'Català' },
  { code: 'ht', label: 'Kreyòl ayisyen' },
  { code: 'la', label: 'Latina' },
  { code: 'yue', label: '廣東話' },
]

const selectedModelInfo = computed(() => store.inventory?.models.find((m) => m.name === selectedModel.value))
const isTurbo = computed(() => /turbo/i.test(selectedModelInfo.value?.name || selectedModel.value || ''))
const supportedTaskTypes = computed<Set<string>>(() => {
  const list = selectedModelInfo.value?.supported_task_types
  return list && list.length ? new Set(list) : new Set(TASK_TYPES.value.map((tt) => tt.value))
})
const taskTypeOptions = computed(() => TASK_TYPES.value.map((tt) => ({ ...tt, disabled: tt.value !== 'voice_replacement' && (!props.generationAvailable || !supportedTaskTypes.value.has(tt.value)) })))

watch(
  () => store.inventory,
  (inv) => {
    if (inv && !inv.models.some((model) => model.name === selectedModel.value)) {
      selectedModel.value = inv.models.some((model) => model.name === inv.default_model) ? inv.default_model : inv.models[0]?.name ?? ''
    }
  },
  { immediate: true },
)

watch(isTurbo, (turbo) => {
  if (!inferenceStepsTouched.value) inferenceSteps.value = null // let placeholder show the right default
  if (turbo) guidanceScale.value = null
}, { immediate: true })

watch(supportedTaskTypes, (set) => {
  if (taskType.value !== 'voice_replacement' && !set.has(taskType.value)) {
    const fallback = TASK_TYPES.value.find((tt) => set.has(tt.value))
    if (fallback) taskType.value = fallback.value
  }
})

// The trainer saves each checkpoint as "<checkpoint>/adapter/{adapter_config.json,...}";
// exporting a checkpoint via the LoRA training page copies that checkpoint dir as-is, so
// the actual PEFT adapter path can end up one level deeper than the registered path.
// Probe both nestings rather than requiring the registry entry to be exactly right.
async function loadLoraWithFallback(path: string, token: number, name?: string) {
  const candidates = [path, `${path}/adapter`, `${path}/adapter/adapter`]
  let lastErr: unknown
  for (const candidate of candidates) {
    if (!formAlive || token !== loraRequest) return
    try {
      await api.loraLoad(candidate, name)
      return
    } catch (err) {
      lastErr = err
    }
  }
  throw lastErr
}

let formAlive = true
let loraRequest = 0
onBeforeUnmount(() => {
  formAlive = false
  loraRequest++
  if (loraDebounce) clearTimeout(loraDebounce)
})

let loraDebounce: ReturnType<typeof setTimeout> | null = null
watch(selectedLoraPath, async (path) => {
  const token = ++loraRequest
  loraStatus.value = ''
  try {
    if (!path) {
      await api.loraUnload()
    } else {
      const entry = loras.value.find((l) => l.path === path)
      await loadLoraWithFallback(path, token, entry?.name)
      if (!formAlive || token !== loraRequest) return
      await api.loraScale(loraScaleVal.value)
    }
  } catch (err) {
    if (formAlive && token === loraRequest) loraStatus.value = err instanceof Error ? err.message : String(err)
  }
})
watch(loraScaleVal, (scale) => {
  if (!selectedLoraPath.value) return
  if (loraDebounce) clearTimeout(loraDebounce)
  const token = loraRequest
  loraDebounce = setTimeout(async () => {
    if (!formAlive || token !== loraRequest) return
    try {
      await api.loraScale(scale)
    } catch (err) {
      if (formAlive && token === loraRequest) loraStatus.value = err instanceof Error ? err.message : String(err)
    }
  }, 300)
})

function onRefFileChange(e: Event) {
  const file = e.target instanceof HTMLInputElement ? e.target.files?.[0] ?? null : null
  refAudioFile.value = file
}

function registerNewLora() {
  addLora(newLoraName.value, newLoraPath.value)
  newLoraName.value = ''
  newLoraPath.value = ''
}

const inferenceStepsPlaceholder = computed(() => (isTurbo.value ? t('aceGen.inferenceStepsTurbo') : t('aceGen.inferenceStepsNormal')))
function clockLabel(totalSeconds: number): string {
  const rounded = Math.max(0, Math.round(totalSeconds))
  const m = Math.floor(rounded / 60)
  const s = rounded % 60
  return `${m}:${String(s).padStart(2, '0')} (${rounded}s)`
}

const durationLabel = computed(() => clockLabel(duration.value))
const fit = computed(() => estimateTrackDuration({
  lyrics: mode.value === 'custom' && !instrumental.value ? customLyrics.value : '',
  instrumental: mode.value === 'custom' && instrumental.value,
  bpm: bpm.value,
}))
const fitLabel = computed(() => clockLabel(fit.value.suggested))
const fitText = computed(() => {
  if (!durationAuto.value && fit.value.suggested > 300 && fit.value.kind === 'lyrics') {
    return t('aceGen.durationCapped', { value: fitLabel.value, max: clockLabel(300) })
  }
  if (fit.value.kind === 'instrumental') return t('aceGen.durationFitInstrumental', { value: fitLabel.value })
  if (fit.value.kind === 'lyrics') return t('aceGen.durationFit', { value: fitLabel.value })
  return t('aceGen.durationFitFull', { value: fitLabel.value })
})

watch(durationAuto, (auto, wasAuto) => {
  if (wasAuto && !auto && !durationTouched.value) duration.value = fit.value.slider
})

function useFitLength() {
  duration.value = fit.value.slider
  durationTouched.value = true
  durationAuto.value = false
}

async function submit() {
  if (submitting.value) return
  formError.value = ''
  if (isVoiceReplacement.value) {
    const file = refAudioFile.value
    const voiceId = selectedVoiceId.value
    if (!file) { formError.value = t('aceGen.selectRefFile'); return }
    if (!voiceId) { formError.value = t('aceGen.replacementSelectVoice'); return }
    submitting.value = true
    try { await store.submitVoiceReplacement(file, voiceId) }
    catch (error) {
      if (formAlive) formError.value = error instanceof VoiceApplyError
        ? voiceErrorText(error.code, error.detail)
        : error instanceof ApiError ? voiceErrorText(error.message) : error instanceof Error ? error.message : t('storeErrors.unknownError')
    }
    finally { if (formAlive) submitting.value = false }
    return
  }
  if (!props.generationAvailable) { formError.value = t('aceGen.generationUnavailable'); return }

  const req: GenerateMusicRequest = {
    audio_duration: durationAuto.value ? -1 : duration.value,
    batch_size: batchSize.value,
    audio_format: audioFormat.value,
  }
  let title = ''

  if (mode.value === 'simple') {
    if (!simpleQuery.value.trim()) {
      formError.value = t('aceGen.enterDescription')
      return
    }
    title = simpleQuery.value.trim().slice(0, 60)
    if (useRefAudio.value) {
      req.prompt = simpleQuery.value.trim()
    } else {
      req.sample_query = simpleQuery.value.trim()
      req.sample_mode = true
    }
  } else {
    if (!customPrompt.value.trim()) {
      formError.value = t('aceGen.enterStyle')
      return
    }
    title = customPrompt.value.trim().slice(0, 60)
    req.prompt = customPrompt.value.trim()
    req.lyrics = instrumental.value ? '' : customLyrics.value
  }

  if (bpm.value) req.bpm = bpm.value
  if (keyScale.value) req.key_scale = keyScale.value
  if (timeSignature.value) req.time_signature = timeSignature.value
  if (vocalLanguage.value) req.vocal_language = vocalLanguage.value
  if (inferenceSteps.value) req.inference_steps = inferenceSteps.value
  if (guidanceScale.value != null && !isTurbo.value) req.guidance_scale = guidanceScale.value
  if (seedValue.value != null) {
    req.seed = seedValue.value
    req.use_random_seed = false
  } else {
    req.use_random_seed = true
  }
  if (selectedModel.value) req.model = selectedModel.value

  let refFile: File | null = null
  if (useRefAudio.value && taskType.value !== 'voice_replacement') {
    if (!refAudioFile.value) {
      formError.value = t('aceGen.selectRefFile')
      return
    }
    refFile = refAudioFile.value
    req.task_type = taskType.value
    if (taskType.value === 'repaint') {
      if (repaintStart.value != null) req.repainting_start = repaintStart.value
      if (repaintEnd.value != null) req.repainting_end = repaintEnd.value
    }
    if (taskType.value === 'extract' || taskType.value === 'lego') req.track_name = trackName.value
    if (taskType.value === 'complete') req.track_classes = trackClasses.value
    if (taskType.value === 'cover' || taskType.value === 'repaint') req.audio_cover_strength = coverStrength.value
  }

  submitting.value = true
  try {
    await store.submit(req, refFile, title)
  } catch (err) {
    formError.value = err instanceof Error ? err.message : String(err)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="lg:sticky lg:top-20 lg:self-start">
    <div class="space-y-4 rounded-xl border border-border bg-panel p-4">
      <VoiceSelect link :label="isVoiceReplacement ? t('aceGen.replacementVoice') : undefined" :hint="isVoiceReplacement ? t('aceGen.replacementVoiceHint') : undefined" @select="selectedVoiceId = $event" />
      <button type="button" class="accent-gradient w-full rounded-lg py-2.5 text-sm font-semibold text-white disabled:opacity-50" :disabled="submitting || (isVoiceReplacement ? !refAudioFile || !selectedVoiceId : !generationAvailable)" @click="submit">
        {{ submitting ? t(isVoiceReplacement ? 'aceGen.replacementUploading' : 'aceGen.submitting') : t(isVoiceReplacement ? 'aceGen.replaceVoice' : 'aceGen.submit') }}
      </button>
      <p v-if="formError" role="alert" class="rounded-lg bg-status-failed/10 p-2 text-xs text-status-failed">{{ formError }}</p>
      <p v-if="!generationAvailable && !isVoiceReplacement" class="text-xs text-text-dim">{{ t('aceGen.generationUnavailable') }}</p>

      <template v-if="!isVoiceReplacement">
      <!-- Preset bar -->
      <div class="flex flex-wrap items-center gap-1.5 rounded-lg border border-border bg-panel-2 p-2">
        <select
          v-model="selectedPresetName"
          class="flex-1 min-w-32 rounded border border-border bg-panel px-2 py-1 text-xs text-text"
          @change="applyPreset(selectedPresetName)"
        >
          <option value="">{{ t('aceGen.presetPlaceholder') }}</option>
          <option v-for="p in presets" :key="p.name" :value="p.name">{{ p.name }}</option>
        </select>
        <button
          v-if="!showPresetInput"
          type="button"
          class="rounded border border-border px-2 py-1 text-xs text-text hover:bg-panel"
          :title="t('aceGen.savePresetTitle')"
          @click="showPresetInput = true"
        >
          {{ t('aceGen.addPreset') }}
        </button>
        <button
          v-if="selectedPresetName"
          type="button"
          class="rounded px-1.5 py-1 text-xs text-status-failed hover:bg-status-failed/10"
          :title="t('aceGen.deletePresetTitle')"
          @click="deletePreset(selectedPresetName)"
        >
          ✕
        </button>
        <div v-if="showPresetInput" class="flex w-full items-center gap-1 pt-1">
          <input
            v-model="newPresetName"
            :placeholder="t('aceGen.presetNamePlaceholder')"
            class="flex-1 rounded border border-border bg-panel px-2 py-0.5 text-xs text-text"
            @keyup.enter="saveCurrentPreset"
          />
          <button
            type="button"
            class="accent-gradient rounded px-2 py-0.5 text-xs text-white"
            @click="saveCurrentPreset"
          >
            {{ t('aceGen.save') }}
          </button>
          <button
            type="button"
            class="text-xs text-text-dim hover:text-text"
            @click="showPresetInput = false"
          >
            {{ t('aceGen.cancel') }}
          </button>
        </div>
      </div>

      <div class="flex gap-2 rounded-lg bg-panel-2 p-1 text-sm">
        <button
          type="button"
          class="flex-1 rounded-md py-1.5 transition-colors"
          :class="mode === 'simple' ? 'accent-gradient text-white' : 'text-text-dim'"
          @click="mode = 'simple'"
        >
          {{ t('aceGen.modeSimple') }}
        </button>
        <button
          type="button"
          class="flex-1 rounded-md py-1.5 transition-colors"
          :class="mode === 'custom' ? 'accent-gradient text-white' : 'text-text-dim'"
          @click="mode = 'custom'"
        >
          {{ t('aceGen.modeCustom') }}
        </button>
      </div>

      <div v-if="mode === 'simple'" class="space-y-1.5">
        <label class="text-sm font-medium text-text">{{ t('aceGen.simpleLabel') }}</label>
        <textarea v-model="simpleQuery" rows="3" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 text-sm text-text" :placeholder="t('aceGen.simplePlaceholder')"></textarea>
      </div>

      <div v-else class="space-y-3">
        <div class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-sm font-medium text-text">{{ t('aceGen.styleLabel') }}</label>
            <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'style'">{{ t('common.help') }}</button>
          </div>
          <TagInput v-model="customPrompt" :placeholder="t('aceGen.stylePlaceholder')" />
        </div>
        <label class="flex items-center gap-2 text-sm text-text-dim">
          <input v-model="instrumental" type="checkbox" class="rounded border-border" />
          {{ t('aceGen.instrumental') }}
        </label>
        <div v-if="!instrumental" class="space-y-1.5">
          <div class="flex items-center justify-between">
            <label class="text-sm font-medium text-text">{{ t('aceGen.lyricsLabel') }}</label>
            <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'lyrics'">{{ t('common.help') }}</button>
          </div>
          <textarea v-model="customLyrics" rows="6" class="w-full rounded-lg border border-border bg-panel-2 p-2.5 font-mono text-sm text-text" :placeholder="t('aceGen.lyricsPlaceholder')"></textarea>
        </div>
      </div>

      </template>
      <div class="space-y-2 rounded-lg border border-border bg-panel-2/50 p-3">
        <label class="flex items-center justify-between text-sm">
          <span class="flex items-center gap-2 font-medium text-text">
            <input v-model="useRefAudio" type="checkbox" class="rounded border-border" />
            {{ t('aceGen.refAudio') }}
          </span>
          <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'remix'">{{ t('common.help') }}</button>
        </label>
        <div v-if="useRefAudio" class="space-y-3 pt-1">
          <input type="file" :aria-label="t('aceGen.refFileLabel')" :accept="isVoiceReplacement ? '.wav,.mp3,.flac,.ogg,.opus,.m4a' : 'audio/*'" :disabled="submitting" class="block w-full text-xs text-text-dim file:mr-3 file:rounded-md file:border-0 file:accent-gradient file:px-3 file:py-1.5 file:text-white" @change="onRefFileChange" />
          <p v-if="refAudioFile" class="text-xs text-text-dim">{{ refAudioFile.name }}</p>

          <ChipGroup v-model="taskType" :options="taskTypeOptions" />
          <div v-if="isVoiceReplacement" class="space-y-2 text-sm text-text-dim">
            <p>{{ t('aceGen.replacementHint') }}</p>
            <p class="text-xs">{{ t('aceGen.replacementLimits') }}</p>
            <p v-if="!selectedVoiceId" class="text-xs text-status-queued">{{ t('aceGen.replacementSelectVoice') }}</p>
          </div>

          <div v-if="taskType === 'repaint'" class="grid grid-cols-2 gap-2">
            <div>
              <label class="text-xs text-text-dim">{{ t('aceGen.repaintStart') }}</label>
              <input v-model.number="repaintStart" type="number" min="0" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" />
            </div>
            <div>
              <label class="text-xs text-text-dim">{{ t('aceGen.repaintEnd') }}</label>
              <input v-model.number="repaintEnd" type="number" min="0" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" />
            </div>
          </div>

          <div v-if="taskType === 'extract' || taskType === 'lego'">
            <label class="text-xs text-text-dim">{{ t('aceGen.trackPart') }}</label>
            <select v-model="trackName" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option v-for="opt in TRACK_NAME_OPTIONS" :key="opt" :value="opt">{{ opt }}</option>
            </select>
          </div>

          <div v-if="taskType === 'complete'">
            <label class="mb-1 block text-xs text-text-dim">{{ t('aceGen.trackPartsToAdd') }}</label>
            <ChipGroup v-model="trackClasses" multiple :options="TRACK_NAME_OPTIONS.map((v) => ({ value: v, label: v }))" />
          </div>

          <div v-if="taskType === 'cover' || taskType === 'repaint'">
            <label class="text-xs text-text-dim">{{ t('aceGen.coverStrength', { value: coverStrength.toFixed(2) }) }}</label>
            <input v-model.number="coverStrength" type="range" min="0" max="1" step="0.05" class="w-full accent-accent1" />
          </div>
        </div>
      </div>

      <template v-if="!isVoiceReplacement">
      <div class="space-y-1">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <label class="text-xs text-text-dim">{{ durationAuto ? t('aceGen.durationAuto', { value: fitLabel }) : t('aceGen.duration', { value: durationLabel }) }}</label>
          <ChipGroup
            :model-value="durationAuto ? 'auto' : 'set'"
            :options="[
              { value: 'auto', label: t('aceGen.durationAutoChoice') },
              { value: 'set', label: t('aceGen.durationSetChoice') },
            ]"
            @update:model-value="(value) => { durationAuto = value === 'auto' }"
          />
        </div>
        <input
          v-if="!durationAuto"
          v-model.number="duration"
          type="range"
          min="10"
          max="300"
          step="5"
          class="w-full accent-accent1"
          @input="durationTouched = true"
        />
        <p class="text-xs text-text-dim">
          <template v-if="durationAuto">{{ t('aceGen.durationAutoHint') }}</template>
          <template v-else>
            {{ fitText }}
            <button
              v-if="duration !== fit.slider"
              type="button"
              class="ml-1 text-accent1 hover:underline"
              @click="useFitLength"
            >{{ t('aceGen.durationUseFit') }}</button>
          </template>
        </p>
      </div>

      <div>
        <label class="mb-1 block text-xs text-text-dim">{{ t('aceGen.variantCount') }}</label>
        <ChipGroup v-model="batchSize" :options="[{ value: 1, label: '1' }, { value: 2, label: '2' }, { value: 4, label: '4' }]" />
      </div>

      <CollapsibleDetails :summary="t('aceGen.advancedSettings')">
        <button type="button" class="text-xs text-accent1 hover:underline" @click="helpOpen = 'advanced'">{{ t('aceGen.advancedWhatMeans') }}</button>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.format') }}</label>
            <select v-model="audioFormat" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option value="mp3">mp3</option>
              <option value="wav">wav</option>
              <option value="flac">flac</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.bpm') }}</label>
            <input v-model.number="bpm" type="number" min="0" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('aceGen.bpmPlaceholder')" />
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.keyScale') }}</label>
            <input v-model="keyScale" type="text" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('aceGen.keyScalePlaceholder')" />
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.timeSignature') }}</label>
            <select v-model="timeSignature" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option value="">{{ t('aceGen.timeSignatureAuto') }}</option>
              <option v-for="ts in TIME_SIGNATURES" :key="ts" :value="ts">{{ ts }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.vocalLanguage') }}</label>
            <select v-model="vocalLanguage" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
              <option value="">{{ t('aceGen.vocalLanguageAuto') }}</option>
              <option v-for="lang in VOCAL_LANGUAGES" :key="lang.code" :value="lang.code">{{ lang.label }}</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.inferenceSteps') }}</label>
            <input
              v-model.number="inferenceSteps"
              type="number"
              min="1"
              class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text"
              :placeholder="inferenceStepsPlaceholder"
              @input="inferenceStepsTouched = true"
            />
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.guidanceScale') }}</label>
            <input
              v-model.number="guidanceScale"
              type="number"
              step="0.1"
              :disabled="isTurbo"
              class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text disabled:opacity-40"
              :placeholder="isTurbo ? t('aceGen.guidanceUnused') : t('aceGen.guidanceDefault')"
            />
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.seed') }}</label>
            <input v-model.number="seedValue" type="number" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text" :placeholder="t('aceGen.seedPlaceholder')" />
          </div>
          <div>
            <label class="text-xs text-text-dim">{{ t('aceGen.model') }}</label>
            <select v-model="selectedModel" :aria-label="t('aceGen.model')" :disabled="!store.inventory?.models.length" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text disabled:opacity-50">
              <option v-if="!store.inventory?.models.length" value="">{{ t(store.inventoryLoading ? 'aceGen.inventoryLoading' : 'aceGen.inventoryEmpty') }}</option>
              <option v-for="m in store.inventory?.models ?? []" :key="m.name" :value="m.name">{{ m.name }}</option>
            </select>
            <p v-if="store.inventoryLoading" role="status" class="mt-1 text-xs text-text-dim">{{ t('aceGen.inventoryLoading') }}</p>
            <p v-if="store.inventoryError" role="alert" class="mt-1 text-xs text-status-failed">{{ t('aceGen.inventoryUnavailable') }}</p>
            <button v-if="store.inventoryError || !store.inventory?.models.length" type="button" :disabled="store.inventoryLoading" class="mt-1 text-xs text-accent1 hover:underline disabled:opacity-50" @click="store.loadInventory()">{{ t('aceGen.inventoryRetry') }}</button>
          </div>
        </div>

        <div class="space-y-2 rounded-lg border border-border bg-panel-2/50 p-3">
          <label class="text-xs text-text-dim">{{ t('aceGen.lora') }}</label>
          <select v-model="selectedLoraPath" class="w-full rounded-lg border border-border bg-panel-2 p-2 text-sm text-text">
            <option value="">{{ t('aceGen.noLora') }}</option>
            <option v-for="l in loras" :key="l.path" :value="l.path">{{ l.name }}</option>
          </select>
          <div v-if="selectedLoraPath">
            <label class="text-xs text-text-dim">{{ t('aceGen.loraStrength', { value: loraScaleVal.toFixed(2) }) }}</label>
            <input v-model.number="loraScaleVal" type="range" min="0" max="2" step="0.05" class="w-full accent-accent1" />
          </div>
          <p v-if="loraStatus" class="text-xs text-status-failed">{{ loraStatus }}</p>
          <div class="flex items-center gap-2">
            <input v-model="newLoraName" type="text" :placeholder="t('aceGen.loraNamePlaceholder')" class="w-1/3 rounded-lg border border-border bg-panel-2 p-1.5 text-xs text-text" />
            <input v-model="newLoraPath" type="text" :placeholder="t('aceGen.loraPathPlaceholder')" class="flex-1 rounded-lg border border-border bg-panel-2 p-1.5 text-xs text-text" />
            <button type="button" class="rounded-lg bg-panel px-2 py-1.5 text-xs text-text-dim hover:text-text" @click="registerNewLora">+</button>
          </div>
          <ul v-if="loras.length" class="space-y-1">
            <li v-for="l in loras" :key="l.path" class="flex items-center justify-between text-xs text-text-dim">
              <span class="truncate">{{ l.name }}</span>
              <button type="button" class="text-status-failed hover:underline" @click="removeLora(l.path)">{{ t('aceGen.removeLora') }}</button>
            </li>
          </ul>
        </div>
      </CollapsibleDetails>
      </template>
    </div>

    <HelpModal :open="helpOpen === 'style'" :title="t('aceGen.help.style.title')" @close="helpOpen = null">
      <p>{{ t('aceGen.help.style.intro') }}</p>
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('aceGen.help.style.dimHeader') }}</th>
            <th class="py-1">{{ t('aceGen.help.style.examplesHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('aceGen.help.style.rows') as { dim: string; examples: string }[])" :key="row.dim" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.dim }}</td>
            <td class="py-1.5">{{ row.examples }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('aceGen.help.style.tipsTitle') }}</p>
      <ul class="list-disc space-y-1 pl-4">
        <li v-for="tip in (tm('aceGen.help.style.tips') as string[])" :key="tip">{{ tip }}</li>
      </ul>
      <p class="font-medium text-text">{{ t('aceGen.help.style.examplesTitle') }}</p>
      <p v-for="ex in (tm('aceGen.help.style.examples') as string[])" :key="ex" class="rounded bg-panel-2 p-2 font-mono text-xs">{{ ex }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'lyrics'" :title="t('aceGen.help.lyrics.title')" @close="helpOpen = null">
      <p>{{ t('aceGen.help.lyrics.intro') }}</p>
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('aceGen.help.lyrics.tagHeader') }}</th>
            <th class="py-1">{{ t('aceGen.help.lyrics.purposeHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('aceGen.help.lyrics.rows') as { tag: string; purpose: string }[])" :key="row.tag" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">{{ row.tag }}</td>
            <td class="py-1.5">{{ row.purpose }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('aceGen.help.lyrics.marksTitle') }}</p>
      <p>{{ t('aceGen.help.lyrics.marksIntro') }}</p>
      <table class="w-full border-collapse text-xs">
        <tbody class="align-top">
          <tr v-for="mark in (tm('aceGen.help.lyrics.marks') as { tag: string; purpose: string }[])" :key="mark.tag" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-mono text-text">{{ mark.tag }}</td>
            <td class="py-1.5">{{ mark.purpose }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('aceGen.help.lyrics.tipsTitle') }}</p>
      <ul class="list-disc space-y-1 pl-4">
        <li v-for="tip in (tm('aceGen.help.lyrics.tips') as string[])" :key="tip">{{ tip }}</li>
      </ul>
      <p class="font-medium text-text">{{ t('aceGen.help.lyrics.exampleTitle') }}</p>
      <p class="rounded bg-panel-2 p-2 font-mono text-xs whitespace-pre-line">{{ t('aceGen.help.lyrics.example') }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'remix'" :title="t('aceGen.help.remix.title')" @close="helpOpen = null">
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('aceGen.help.remix.scenarioHeader') }}</th>
            <th class="py-1">{{ t('aceGen.help.remix.whatHeader') }}</th>
            <th class="py-1">{{ t('aceGen.help.remix.tuneHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('aceGen.help.remix.rows') as { scenario: string; what: string; tune: string }[])" :key="row.scenario" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.scenario }}</td>
            <td class="py-1.5 pr-2">{{ row.what }}</td>
            <td class="py-1.5">{{ row.tune }}</td>
          </tr>
        </tbody>
      </table>
      <p class="font-medium text-text">{{ t('aceGen.help.remix.paramsTitle') }}</p>
      <ul class="list-disc space-y-1 pl-4">
        <li v-for="(p, i) in (tm('aceGen.help.remix.params') as string[])" :key="i" v-html="p"></li>
      </ul>
      <p class="font-medium text-text">{{ t('aceGen.help.remix.unavailableTitle') }}</p>
      <p class="text-xs text-text-dim">{{ t('aceGen.help.remix.unavailable') }}</p>
    </HelpModal>
    <HelpModal :open="helpOpen === 'advanced'" :title="t('aceGen.help.advanced.title')" @close="helpOpen = null">
      <table class="w-full border-collapse text-xs">
        <thead>
          <tr class="border-b border-border text-left text-text">
            <th class="py-1 pr-2">{{ t('aceGen.help.advanced.paramHeader') }}</th>
            <th class="py-1">{{ t('aceGen.help.advanced.whatHeader') }}</th>
          </tr>
        </thead>
        <tbody class="align-top">
          <tr v-for="row in (tm('aceGen.help.advanced.rows') as { param: string; what: string }[])" :key="row.param" class="border-b border-border/60">
            <td class="py-1.5 pr-2 font-medium text-text">{{ row.param }}</td>
            <td class="py-1.5">{{ row.what }}</td>
          </tr>
        </tbody>
      </table>
      <p class="text-xs text-text-dim">{{ t('aceGen.help.advanced.footer') }}</p>
    </HelpModal>
  </div>
</template>
