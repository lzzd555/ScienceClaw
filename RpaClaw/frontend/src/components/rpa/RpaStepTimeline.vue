<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronUp,
  Loader2,
  Radio,
  Trash2,
  Wand2,
} from 'lucide-vue-next';
import {
  buildRpaStepTimelineItem,
  formatRpaStepLocator,
  type RpaTimelineStatusTone,
} from '@/utils/rpaStepTimeline';

const props = withDefaults(defineProps<{
  steps: any[];
  title?: string;
  mode?: 'record' | 'configure' | 'test';
  isRecording?: boolean;
  autoScroll?: boolean;
  activeIndex?: number | null;
  failedStepIndex?: number | null;
  failedStepError?: string;
  failedStepCandidates?: any[];
  triedCandidateIndices?: Set<number>;
  retryingWithCandidate?: boolean;
  promotingStepIndex?: number | null;
  diagnosticsCount?: number;
  diagnosticsMessage?: string;
  showDelete?: boolean;
  showCandidates?: boolean;
  showHeader?: boolean;
  emptyMessage?: string;
}>(), {
  title: '录制步骤',
  mode: 'record',
  isRecording: false,
  autoScroll: false,
  activeIndex: null,
  failedStepIndex: null,
  failedStepError: '',
  failedStepCandidates: () => [],
  triedCandidateIndices: () => new Set<number>(),
  retryingWithCandidate: false,
  promotingStepIndex: null,
  diagnosticsCount: 0,
  diagnosticsMessage: '',
  showDelete: false,
  showCandidates: false,
  showHeader: true,
  emptyMessage: '当前没有录制步骤。',
});

const emit = defineEmits<{
  'delete-step': [payload: { step: any; index: number }];
  'promote-locator': [payload: { step: any; stepIndex: number; candidateIndex: number }];
  'retry-candidate': [candidateIndex: number];
}>();

const scrollerRef = ref<HTMLElement | null>(null);
const expandedIndex = ref<number | null>(null);

const items = computed(() => props.steps.map((step, index) => buildRpaStepTimelineItem({
  step,
  index,
  failedStepIndex: props.failedStepIndex,
  activeIndex: props.activeIndex,
})));

const latestIndex = computed(() => (
  props.activeIndex ?? (props.steps.length > 0 ? props.steps.length - 1 : null)
));

const statusClass = (tone: RpaTimelineStatusTone) => {
  if (tone === 'success') return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200';
  if (tone === 'danger') return 'bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-200';
  if (tone === 'warning') return 'bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-200';
  if (tone === 'active') return 'bg-violet-50 text-[#831bd7] dark:bg-violet-950/30 dark:text-violet-200';
  return 'bg-[#edeef0] text-gray-600 dark:bg-white/10 dark:text-gray-300';
};

const actionClass = (isAi: boolean, isExpanded: boolean, isFailed: boolean) => {
  if (isFailed) return 'bg-rose-100 text-rose-700 dark:bg-rose-950/40 dark:text-rose-200';
  if (isExpanded) return 'bg-gradient-to-r from-[#841cd8] to-[#ac0189] text-white';
  if (isAi) return 'bg-violet-50 text-[#831bd7] dark:bg-violet-950/30 dark:text-violet-200';
  return 'bg-[#edeef0] text-gray-600 dark:bg-white/10 dark:text-gray-300';
};

const canDeleteStep = (step: any, index: number) => (
  props.showDelete && index > 0 && step?.deletable !== false
);

const toggleStep = (index: number) => {
  expandedIndex.value = expandedIndex.value === index ? null : index;
};

const scrollToIndex = async (index: number | null) => {
  if (index === null || index < 0) return;
  await nextTick();
  const scroller = scrollerRef.value;
  const target = scroller?.querySelector<HTMLElement>(`[data-step-index="${index}"]`);
  target?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
};

watch(
  () => props.steps.length,
  () => {
    if (!props.autoScroll) return;
    scrollToIndex(latestIndex.value);
  },
);

watch(
  () => props.failedStepIndex,
  (index) => {
    if (index === null || index === undefined) return;
    expandedIndex.value = index;
    scrollToIndex(index);
  },
);
</script>

<template>
  <section class="flex min-h-0 flex-1 flex-col overflow-hidden bg-[#eff1f2] dark:bg-[#212122]">
    <div v-if="showHeader" class="shrink-0 px-3 pb-2.5 pt-4">
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0">
          <h2 class="text-base font-extrabold text-gray-950 dark:text-gray-100">{{ title }}</h2>
          <p class="mt-0.5 truncate text-[11px] font-medium text-gray-500 dark:text-gray-400">
            默认显示业务摘要，展开查看高级信息
          </p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          <span
            v-if="isRecording"
            class="inline-flex items-center gap-1 rounded-md bg-violet-50 px-2 py-1 text-[10px] font-bold text-[#831bd7] dark:bg-violet-950/30 dark:text-violet-200"
          >
            <Radio class="animate-pulse" :size="11" />
            自动跟随
          </span>
          <span class="rounded-md bg-white px-2 py-1 text-[10px] font-extrabold text-[#831bd7] dark:bg-[#272728] dark:text-violet-200">
            {{ steps.length }} 步
          </span>
        </div>
      </div>

      <div
        v-if="diagnosticsCount > 0"
        class="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-950/30 dark:text-rose-200"
      >
        <p class="flex items-center gap-1.5 font-bold">
          <AlertTriangle :size="13" />
          {{ diagnosticsCount }} 个步骤待处理
        </p>
        <p class="timeline-wrap mt-1 text-[11px] opacity-85">
          {{ diagnosticsMessage || '请先修复或删除这些步骤，再继续后续操作。' }}
        </p>
      </div>
    </div>

    <div ref="scrollerRef" class="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-1.5 pb-4">
      <article
        v-for="(item, index) in items"
        :key="item.id"
        :data-step-index="index"
        class="group relative overflow-hidden rounded-lg bg-white transition-colors dark:bg-[#272728]"
        :class="[
          item.isFailed
            ? 'ring-2 ring-rose-100 dark:ring-rose-950/50'
            : expandedIndex === index
              ? 'ring-2 ring-violet-100 dark:ring-violet-950/50'
              : 'hover:bg-[#fbfbfc] dark:hover:bg-[#303032]',
        ]"
      >
        <button
          type="button"
          class="block w-full px-2 py-2 text-left"
          @click="toggleStep(index)"
        >
          <div class="flex min-w-0 items-start gap-1.5 pr-4">
            <span
              class="mt-0.5 flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-md text-[9px] font-extrabold"
              :class="actionClass(item.isAi, expandedIndex === index, item.isFailed)"
            >
              {{ item.indexLabel }}
            </span>

            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 items-center gap-1">
                <span class="shrink-0 rounded bg-[#edeef0] px-1 py-0.5 text-[10px] font-bold leading-3 text-gray-600 dark:bg-white/10 dark:text-gray-300">
                  {{ item.actionLabel }}
                </span>
                <span
                  v-if="item.isAi"
                  class="inline-flex shrink-0 items-center gap-0.5 rounded bg-violet-50 px-1 py-0.5 text-[10px] font-bold leading-3 text-[#831bd7] dark:bg-violet-950/30 dark:text-violet-200"
                >
                  <Bot :size="10" />
                  AI
                </span>
                <span class="shrink-0 rounded px-1 py-0.5 text-[10px] font-bold leading-3" :class="statusClass(item.status.tone)">
                  {{ item.status.label }}
                </span>
              </div>

              <h3 class="timeline-title timeline-clamp mt-1 text-[13px] font-extrabold leading-[18px] text-gray-950 dark:text-gray-100">
                {{ item.title }}
              </h3>
              <p
                class="mt-0.5 flex min-w-0 items-center gap-0.5 text-[11px] leading-4 text-gray-500 dark:text-gray-400"
                :title="item.summary"
              >
                <span v-if="item.summaryLabel" class="shrink-0 font-semibold">{{ item.summaryLabel }}：</span>
                <span
                  class="min-w-0 flex-1 truncate"
                  :class="item.summaryLabel === '定位器' ? 'font-mono' : ''"
                >
                  {{ item.summaryValue }}
                </span>
              </p>
            </div>
          </div>
        </button>

        <div class="absolute right-1 top-1.5 flex items-center gap-0.5">
          <button
            v-if="canDeleteStep(steps[index], index)"
            type="button"
            class="flex h-5 w-5 items-center justify-center rounded text-gray-400 opacity-0 transition-colors hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100 dark:hover:bg-rose-950/30"
            title="删除步骤"
            @click.stop="emit('delete-step', { step: steps[index], index })"
          >
            <Trash2 :size="12" />
          </button>
          <button
            type="button"
            class="flex h-5 w-5 items-center justify-center rounded text-gray-400 transition-colors hover:bg-[#edeef0] dark:text-gray-500 dark:hover:bg-white/10"
            title="展开高级信息"
            @click.stop="toggleStep(index)"
          >
            <ChevronUp v-if="expandedIndex === index" :size="14" />
            <ChevronDown v-else :size="14" />
          </button>
        </div>

        <div
          v-if="expandedIndex === index"
          class="space-y-2 bg-[#f7f4fa] px-2.5 pb-2.5 pt-2 dark:bg-[#342f3a]"
        >
          <div class="rounded-lg bg-white p-2.5 text-xs dark:bg-[#272728]">
            <p class="mb-2 font-bold text-gray-900 dark:text-gray-100">高级信息</p>
            <dl class="space-y-2">
              <div class="grid gap-1">
                <dt class="text-[10px] font-bold uppercase text-gray-400 dark:text-gray-500">Locator</dt>
                <dd class="timeline-code font-mono text-[11px] text-gray-700 dark:text-gray-300">{{ item.technical.locator }}</dd>
              </div>
              <div class="grid gap-1">
                <dt class="text-[10px] font-bold uppercase text-gray-400 dark:text-gray-500">Frame</dt>
                <dd class="timeline-code font-mono text-[11px] text-gray-700 dark:text-gray-300">{{ item.technical.frame }}</dd>
              </div>
              <div class="grid gap-1">
                <dt class="text-[10px] font-bold uppercase text-gray-400 dark:text-gray-500">Validation</dt>
                <dd class="timeline-wrap text-[11px] text-gray-700 dark:text-gray-300">{{ item.technical.validation }}</dd>
              </div>
              <div v-if="item.technical.url" class="grid gap-1">
                <dt class="text-[10px] font-bold uppercase text-gray-400 dark:text-gray-500">URL</dt>
                <dd class="timeline-code font-mono text-[11px] text-gray-700 dark:text-gray-300">{{ item.technical.url }}</dd>
              </div>
            </dl>
          </div>

          <div
            v-if="showCandidates && steps[index]?.locator_candidates?.length && steps[index]?.configurable !== false"
            class="space-y-2 rounded-lg bg-white p-2.5 dark:bg-[#272728]"
          >
            <div class="flex items-center justify-between gap-3">
              <p class="text-xs font-extrabold text-gray-900 dark:text-gray-100">候选定位器</p>
              <span class="text-[10px] font-semibold text-gray-400 dark:text-gray-500">{{ item.technical.candidateCount }} 个</span>
            </div>
            <button
              v-for="(candidate, candidateIndex) in steps[index].locator_candidates"
              :key="`${item.id}-${candidateIndex}`"
              type="button"
              class="block w-full rounded-lg px-2.5 py-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60"
              :class="candidate.selected ? 'bg-violet-50 dark:bg-violet-950/30' : 'bg-[#f2f4f6] hover:bg-[#edeef0] dark:bg-white/5 dark:hover:bg-white/10'"
              :disabled="candidate.selected || promotingStepIndex === index"
              @click.stop="emit('promote-locator', { step: steps[index], stepIndex: index, candidateIndex })"
            >
              <div class="mb-1 flex min-w-0 items-center gap-1.5">
                <span class="shrink-0 rounded-md bg-white px-1.5 py-0.5 text-[10px] font-bold text-gray-600 dark:bg-[#272728] dark:text-gray-300">
                  {{ candidate.kind || 'locator' }}
                </span>
                <span v-if="candidate.selected" class="shrink-0 rounded-md bg-[#831bd7] px-1.5 py-0.5 text-[10px] font-bold text-white">当前使用</span>
                <span class="min-w-0 truncate text-[10px] font-semibold text-gray-400 dark:text-gray-500">score {{ candidate.score ?? '-' }}</span>
              </div>
              <p class="timeline-code font-mono text-[11px] text-gray-700 dark:text-gray-300">
                {{ formatRpaStepLocator(candidate.locator || candidate) }}
              </p>
              <p v-if="candidate.reason" class="timeline-wrap mt-1 text-[11px] text-gray-500 dark:text-gray-400">
                {{ candidate.reason }}
              </p>
            </button>
          </div>

          <div
            v-if="item.isFailed"
            class="space-y-2 rounded-lg bg-rose-50 p-2.5 text-xs text-rose-700 dark:bg-rose-950/30 dark:text-rose-200"
          >
            <p class="flex items-center gap-1.5 font-bold">
              <AlertTriangle :size="13" />
              执行失败
            </p>
            <p v-if="failedStepError" class="timeline-wrap text-[11px]">{{ failedStepError }}</p>

            <div v-if="failedStepCandidates.length" class="space-y-1.5 pt-1">
              <p class="font-bold">尝试其他定位器</p>
              <button
                v-for="(candidate, candidateIndex) in failedStepCandidates"
                :key="candidateIndex"
                type="button"
                class="block w-full rounded-lg bg-white px-2.5 py-2 text-left text-[11px] text-gray-700 transition-colors hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-[#272728] dark:text-gray-300 dark:hover:bg-rose-950/50"
                :disabled="retryingWithCandidate"
                @click.stop="emit('retry-candidate', candidateIndex)"
              >
                <div class="flex min-w-0 items-center gap-2">
                  <span class="min-w-0 flex-1 truncate font-mono">
                    {{ candidate.kind }}: {{ candidate.playwright_locator || formatRpaStepLocator(candidate.locator) }}
                  </span>
                  <span
                    v-if="triedCandidateIndices.has(candidateIndex)"
                    class="shrink-0 rounded-md bg-[#edeef0] px-1.5 py-0.5 text-[10px] font-bold text-gray-500 dark:bg-white/10"
                  >
                    已尝试
                  </span>
                </div>
              </button>
            </div>
          </div>
        </div>
      </article>

      <div
        v-if="!steps.length"
        class="flex flex-col items-center justify-center gap-3 rounded-xl bg-white py-10 text-center text-sm text-gray-400 dark:bg-[#272728] dark:text-gray-500"
      >
        <Loader2 v-if="mode === 'test'" class="animate-spin text-[#831bd7]" :size="20" />
        <Wand2 v-else class="text-[#831bd7]" :size="20" />
        <p class="px-4 text-xs font-medium">{{ emptyMessage }}</p>
      </div>

      <div
        v-if="isRecording"
        class="flex flex-col items-center justify-center gap-2 rounded-xl bg-white/60 py-5 text-center dark:bg-white/5"
      >
        <Wand2 class="animate-pulse text-[#831bd7]" :size="18" />
        <p class="text-xs font-semibold text-gray-500 dark:text-gray-400">检测新操作中...</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline-clamp {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.timeline-title {
  overflow-wrap: anywhere;
}

.timeline-code,
.timeline-wrap {
  max-width: 100%;
  overflow-wrap: anywhere;
  word-break: break-word;
}
</style>
