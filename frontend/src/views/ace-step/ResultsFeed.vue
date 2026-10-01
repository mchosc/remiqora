<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAceStepStore } from '../../stores/aceStep'
import { useDateFilterSort } from '../../composables/useDateFilterSort'
import FilterSortBar from '../../components/shared/FilterSortBar.vue'
import JobCard from './JobCard.vue'
import { useTrackView } from '../../composables/useTrackView'
import { formatTrackCodes } from '../../api/tracks'
import { useTrackFavoritesStore } from '../../stores/trackFavorites'

const store = useAceStepStore()
const { t } = useI18n()
const { view, setView } = useTrackView()
const hasActive = computed(() => store.activeJobs.length > 0)
const cancelError = ref('')
const cancelling = ref(false)
const favorites = useTrackFavoritesStore()
const favoritesOnly = ref(false)

const { sortOrder, dateFrom, dateTo, activePreset, isFiltered, filteredSorted, applyPreset, reset } = useDateFilterSort(() => store.jobs)
const visibleJobs = computed(() => filteredSorted.value.filter((job) => !favoritesOnly.value || job.dbIds.some(favorites.isFavorite)))
function visibleIds(ids: number[]): number[] { return favoritesOnly.value ? ids.filter(favorites.isFavorite) : ids }
const visibleCount = computed(() => visibleJobs.value.reduce((count, job) => count + (job.dbIds.length ? visibleIds(job.dbIds).length : job.audioUrls.length || 1), 0))
const totalCount = computed(() => store.jobs.reduce((count, job) => count + (job.dbIds.length || job.audioUrls.length || 1), 0))
function resetFilters() { favoritesOnly.value = false; reset() }
watch(() => store.jobs.flatMap((job) => job.dbIds).join(','), () => { void favorites.refresh() }, { immediate: true })

async function stopAll() {
  if (cancelling.value) return
  cancelError.value = ''
  cancelling.value = true
  try { await store.cancelAll() }
  catch (error) { cancelError.value = error instanceof Error ? error.message : t('storeErrors.unknownError') }
  finally { cancelling.value = false }
}
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-lg font-semibold text-text">{{ t('feed.yourTracks') }}</h2>
      <div class="flex items-center gap-2">
        <div class="flex rounded-lg border border-border p-0.5 text-xs">
          <button type="button" class="rounded-md px-2 py-1" :class="view === 'list' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('list')">{{ t('feed.viewList') }}</button>
          <button type="button" class="rounded-md px-2 py-1" :class="view === 'cards' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('cards')">{{ t('feed.viewCards') }}</button>
        </div>
        <button v-if="hasActive" type="button" class="rounded-lg border border-status-failed/40 px-3 py-1.5 text-xs text-status-failed hover:bg-status-failed/10 disabled:opacity-50" :disabled="cancelling" @click="stopAll">
          {{ t('feed.stopAll') }}
        </button>
      </div>
    </div>
    <p v-if="cancelError" class="text-sm text-status-failed" role="alert">{{ cancelError }}</p>
    <p v-if="favorites.loading" class="text-xs text-text-dim" role="status">{{ t('trackFavorites.loading') }}</p>
    <p v-if="favorites.loadError" class="text-sm text-status-failed" role="alert">{{ t('trackFavorites.loadFailed') }} <button type="button" class="text-accent1 underline" @click="favorites.refresh()">{{ t('trackFavorites.retry') }}</button></p>

    <div v-if="store.historyError" class="space-y-2 rounded-lg bg-status-failed/10 p-3 text-sm text-status-failed" role="alert">
      <p>{{ store.historyError }}</p>
      <button type="button" class="text-accent1 hover:underline" @click="store.loadHistory()">{{ t('aceJob.retryLoad') }}</button>
    </div>

    <FilterSortBar
      v-if="store.jobs.length > 0"
      v-model:sort-order="sortOrder"
      v-model:date-from="dateFrom"
      v-model:date-to="dateTo"
      v-model:favorites-only="favoritesOnly"
      :favorites-loading="favorites.loading && !favorites.loaded"
      :is-filtered="isFiltered || favoritesOnly"
      :visible-count="visibleCount"
      :total-count="totalCount"
      :active-preset="activePreset"
      @preset="applyPreset"
      @reset="resetFilters"
    />

    <!-- Skeleton loaders while history is loading -->
    <template v-if="!store.historyLoaded">
      <div v-for="i in 3" :key="'skel-' + i" class="animate-pulse rounded-xl border border-border bg-panel p-4 space-y-3">
        <div class="flex items-center justify-between">
          <div class="h-4 w-1/3 rounded bg-panel-2"></div>
          <div class="h-5 w-16 rounded-full bg-panel-2"></div>
        </div>
        <div class="h-3 w-2/3 rounded bg-panel-2"></div>
        <div class="h-10 w-full rounded-lg bg-panel-2"></div>
      </div>
    </template>

    <p v-else-if="store.jobs.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-dim">{{ t('feed.emptyHint') }}</p>
    <p v-else-if="visibleJobs.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-text-dim">{{ t(favoritesOnly ? 'trackFavorites.noneMatching' : 'feed.noneInPeriod') }}</p>
    <div :class="view === 'list' ? 'divide-y divide-border overflow-hidden rounded-xl border border-border' : 'space-y-3'">
      <JobCard v-for="job in visibleJobs" :key="job.id" :job="job" :number="formatTrackCodes(favoritesOnly ? visibleIds(job.dbIds).map(favorites.trackCode) : job.shortIds)" :view="view" :visible-track-ids="visibleIds(job.dbIds)" :favorites-only="favoritesOnly" />
    </div>
  </div>
</template>
