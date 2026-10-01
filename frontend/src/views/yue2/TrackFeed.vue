<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { computed, ref, watch } from 'vue'
import { useYue2Store } from '../../stores/yue2'
import { useDateFilterSort } from '../../composables/useDateFilterSort'
import FilterSortBar from '../../components/shared/FilterSortBar.vue'
import TrackCard from './TrackCard.vue'
import { useTrackView } from '../../composables/useTrackView'
import { formatTrackCodes } from '../../api/tracks'
import { useTrackFavoritesStore } from '../../stores/trackFavorites'

const store = useYue2Store()
const { t } = useI18n()
const { view, setView } = useTrackView()
const favorites = useTrackFavoritesStore()
const favoritesOnly = ref(false)

const { sortOrder, dateFrom, dateTo, activePreset, isFiltered, filteredSorted, applyPreset, reset } = useDateFilterSort(() => store.jobs)
const visibleJobs = computed(() => filteredSorted.value.filter((job) => !favoritesOnly.value || job.dbId != null && favorites.isFavorite(job.dbId)))
function resetFilters() { favoritesOnly.value = false; reset() }
watch(() => store.jobs.map((job) => job.dbId).join(','), () => { void favorites.refresh() }, { immediate: true })
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <h2 class="text-lg font-semibold text-text">{{ t('feed.yourTracks') }}</h2>
      <div class="flex rounded-lg border border-border p-0.5 text-xs">
        <button type="button" class="rounded-md px-2 py-1" :class="view === 'list' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('list')">{{ t('feed.viewList') }}</button>
        <button type="button" class="rounded-md px-2 py-1" :class="view === 'cards' ? 'bg-panel-2 text-text' : 'text-text-dim'" @click="setView('cards')">{{ t('feed.viewCards') }}</button>
      </div>
    </div>
    <p v-if="favorites.loading" class="text-xs text-text-dim" role="status">{{ t('trackFavorites.loading') }}</p>
    <p v-if="favorites.loadError" class="text-sm text-status-failed" role="alert">{{ t('trackFavorites.loadFailed') }} <button type="button" class="text-accent1 underline" @click="favorites.refresh()">{{ t('trackFavorites.retry') }}</button></p>

    <FilterSortBar
      v-if="store.jobs.length > 0"
      v-model:sort-order="sortOrder"
      v-model:date-from="dateFrom"
      v-model:date-to="dateTo"
      v-model:favorites-only="favoritesOnly"
      :favorites-loading="favorites.loading && !favorites.loaded"
      :is-filtered="isFiltered || favoritesOnly"
      :visible-count="visibleJobs.length"
      :total-count="store.jobs.length"
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
      <TrackCard v-for="job in visibleJobs" :key="job.id" :job="job" :number="formatTrackCodes([job.shortId])" :view="view" />
    </div>
  </div>
</template>
