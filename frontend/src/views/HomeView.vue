<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useOrchestratorStore } from '../stores/orchestrator'
import { MODEL_LABELS, useModelSwitch } from '../composables/useModelSwitch'
import * as projectsApi from '../api/projects'
import type { ProjectSummary } from '../api/projects'
import type { ModelId } from '../types'
import VoiceSelect from '../components/shared/VoiceSelect.vue'

const orchestrator = useOrchestratorStore()
const { selectModel } = useModelSwitch()
const { t } = useI18n()

const MODEL_IDS: ModelId[] = ['ace_step', 'yue2']
const DESCRIPTION_KEYS: Record<ModelId, string> = {
  ace_step: 'home.descAceStep',
  yue2: 'home.descYue2',
}

const recentProjects = ref<ProjectSummary[]>([])

async function loadProjects() {
  try {
    const list = await projectsApi.listProjects()
    recentProjects.value = list
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime())
      .slice(0, 3)
  } catch {
    // Ignore errors for now
  }
}

onMounted(() => {
  loadProjects()
})

async function onPick(id: ModelId) {
  try {
    await selectModel(id)
  } catch {
    // handled via orchestrator.switchError, shown in the header.
  }
}
</script>

<template>
  <div class="mx-auto max-w-3xl py-10 text-center">
    <h1 class="text-2xl font-semibold text-text">{{ t('home.title') }}</h1>
    <p class="mt-2 text-sm text-text-dim">{{ t('home.subtitle') }}</p>

    <VoiceSelect link class="mx-auto mt-8 max-w-md" />

    <div class="mt-8 grid gap-4 sm:grid-cols-2">
      <button
        v-for="id in MODEL_IDS"
        :key="id"
        type="button"
        class="group relative overflow-hidden rounded-xl border border-border bg-panel p-6 text-left transition-colors hover:border-accent1/60"
        @click="onPick(id)"
      >
        <div class="text-lg font-semibold text-text">{{ MODEL_LABELS[id] }}</div>
        <p class="mt-2 text-sm text-text-dim">{{ t(DESCRIPTION_KEYS[id]) }}</p>
        <span class="mt-4 inline-block text-xs font-medium text-accent1 transition-transform group-hover:translate-x-1">
          {{ orchestrator.statuses[id]?.status === 'running' ? t('home.open') : t('home.start') }} →
        </span>
      </button>
    </div>

    <!-- Recent projects -->
    <div v-if="recentProjects.length > 0" class="mt-16 text-left">
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-lg font-semibold text-text">{{ t('home.recentProjects') }}</h2>
        <RouterLink to="/editor" class="text-sm text-accent1 hover:underline">{{ t('home.allProjects') }}</RouterLink>
      </div>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <RouterLink
          v-for="proj in recentProjects"
          :key="proj.id"
          :to="`/editor/${proj.id}`"
          class="flex flex-col justify-between rounded-xl border border-border bg-panel-2 p-4 transition-colors hover:border-accent1/60"
        >
          <div class="truncate text-sm font-medium text-text">{{ proj.name }}</div>
          <div class="mt-2 text-[10px] text-text-dim">{{ new Date(proj.updated_at).toLocaleDateString() }}</div>
        </RouterLink>
      </div>
    </div>
    
    <footer class="mt-20 pt-8 border-t border-border/60 text-center">
      <p class="text-xs text-text-dim">{{ t('home.footer') }}</p>
    </footer>
  </div>
</template>
