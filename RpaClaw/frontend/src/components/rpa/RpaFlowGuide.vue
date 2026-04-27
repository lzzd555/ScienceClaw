<script setup lang="ts">
import { computed } from 'vue';
import {
  Check,
  ChevronRight,
  FolderOpen,
  Home,
  Play,
  Radio,
  RotateCcw,
  Save,
  Settings,
} from 'lucide-vue-next';
import {
  buildRpaFlowSteps,
  getRpaFlowMetaChips,
  type RpaFlowGuideState,
  type RpaFlowStepId,
  type RpaTestState,
} from '@/utils/rpaFlowGuide';

interface SecondaryAction {
  id: string;
  label: string;
  disabled?: boolean;
  tone?: 'default' | 'accent';
}

const props = withDefaults(defineProps<{
  currentStep: RpaFlowStepId;
  sessionId?: string | null;
  recordedStepCount?: number;
  diagnosticCount?: number;
  isRecording?: boolean;
  recordingTime?: string;
  testState?: RpaTestState;
  skillName?: string;
  statusMessage?: string;
  primaryLabel?: string;
  primaryDisabled?: boolean;
  secondaryActions?: SecondaryAction[];
}>(), {
  sessionId: null,
  recordedStepCount: 0,
  diagnosticCount: 0,
  isRecording: false,
  recordingTime: '',
  testState: 'idle',
  skillName: '',
  statusMessage: '',
  primaryLabel: '',
  primaryDisabled: false,
  secondaryActions: () => [],
});

const emit = defineEmits<{
  'go-record': [];
  'go-configure': [];
  'go-test': [];
  'primary-action': [];
  'secondary-action': [id: string];
  home: [];
  skills: [];
}>();

const state = computed<RpaFlowGuideState>(() => ({
  currentStep: props.currentStep,
  sessionId: props.sessionId,
  recordedStepCount: props.recordedStepCount,
  diagnosticCount: props.diagnosticCount,
  isRecording: props.isRecording,
  recordingTime: props.recordingTime,
  testState: props.testState,
  skillName: props.skillName,
}));

const steps = computed(() => buildRpaFlowSteps(state.value));
const chips = computed(() => getRpaFlowMetaChips(state.value));

const clickStep = (id: RpaFlowStepId, disabled: boolean) => {
  if (disabled || id === props.currentStep) return;
  if (id === 'record') emit('go-record');
  if (id === 'configure') emit('go-configure');
  if (id === 'test') emit('go-test');
};

const stepIcon = (id: RpaFlowStepId) => {
  if (id === 'record') return Radio;
  if (id === 'configure') return Settings;
  return Play;
};
</script>

<template>
  <header class="rpa-flow-guide h-16 flex-shrink-0 bg-[#f8f9fb]/95 backdrop-blur-xl dark:bg-[#161618]/95">
    <div class="flex h-full items-center gap-3 px-4 sm:px-6">
      <div class="flex items-center gap-1">
        <button
          type="button"
          class="flex h-9 w-9 items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-[#edeef0] hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"
          title="返回首页"
          @click="emit('home')"
        >
          <Home :size="17" />
        </button>
        <button
          type="button"
          class="flex h-9 w-9 items-center justify-center rounded-lg text-gray-500 transition-colors hover:bg-[#edeef0] hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white"
          title="技能库"
          @click="emit('skills')"
        >
          <FolderOpen :size="17" />
        </button>
      </div>

      <div class="flex min-w-0 items-center gap-2">
        <button
          v-for="(step, index) in steps"
          :key="step.id"
          type="button"
          class="group flex h-10 min-w-0 items-center gap-2 rounded-lg px-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-55"
          :class="[
            step.status === 'active'
              ? 'bg-gradient-to-r from-[#841cd8] to-[#ac0189] text-white shadow-sm shadow-[#841cd8]/20'
              : step.status === 'completed'
                ? 'bg-white text-gray-900 dark:bg-[#272728] dark:text-gray-100'
                : step.disabled
                  ? 'text-gray-400 dark:text-gray-500'
                  : 'text-gray-600 hover:bg-white hover:text-gray-950 dark:text-gray-300 dark:hover:bg-white/10 dark:hover:text-white',
          ]"
          :title="step.destructive ? '重新录制会丢弃之前的所有工作' : (step.disabledReason || step.title)"
          :disabled="step.disabled"
          @click="clickStep(step.id, step.disabled)"
        >
          <span
            class="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-extrabold"
            :class="step.status === 'active' ? 'bg-white/20 text-white' : 'bg-[#edeef0] text-gray-600 dark:bg-white/10 dark:text-gray-300'"
          >
            <Check v-if="step.status === 'completed'" :size="13" />
            <component :is="stepIcon(step.id)" v-else :size="13" />
          </span>
          <span class="hidden min-w-0 sm:block">
            <span class="block truncate text-[13px] font-extrabold leading-tight">{{ step.title }}</span>
            <span class="block truncate text-[10px] font-semibold opacity-70">{{ step.disabledReason || step.caption }}</span>
          </span>
          <ChevronRight
            v-if="index < steps.length - 1"
            class="ml-1 hidden shrink-0 text-gray-300 lg:block"
            :size="15"
          />
        </button>
      </div>

      <div class="hidden min-w-0 flex-1 items-center gap-2 xl:flex">
        <span
          v-if="skillName"
          class="truncate rounded-md bg-[#edeef0] px-2 py-1 text-[11px] font-semibold text-gray-600 dark:bg-white/10 dark:text-gray-300"
        >
          {{ skillName }}
        </span>
        <span
          v-for="chip in chips"
          :key="chip"
          class="shrink-0 rounded-md bg-[#edeef0] px-2 py-1 text-[11px] font-semibold text-gray-600 dark:bg-white/10 dark:text-gray-300"
        >
          {{ chip }}
        </span>
        <span
          v-if="statusMessage"
          class="shrink-0 rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-200"
        >
          {{ statusMessage }}
        </span>
      </div>

      <div class="ml-auto flex shrink-0 items-center gap-2">
        <button
          v-for="action in secondaryActions"
          :key="action.id"
          type="button"
          class="hidden items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-bold transition-colors disabled:cursor-not-allowed disabled:opacity-50 sm:inline-flex"
          :class="action.tone === 'accent'
            ? 'bg-[#f0dbff] text-[#6900b3] hover:bg-[#ddb7ff] dark:bg-[#841cd8]/20 dark:text-purple-200'
            : 'bg-white text-gray-700 hover:bg-[#edeef0] dark:bg-[#272728] dark:text-gray-300 dark:hover:bg-[#444345]'"
          :disabled="action.disabled"
          @click="emit('secondary-action', action.id)"
        >
          {{ action.label }}
        </button>

        <button
          v-if="currentStep !== 'record'"
          type="button"
          class="hidden items-center gap-1.5 rounded-lg px-3 py-2 text-xs font-bold text-gray-500 transition-colors hover:bg-[#edeef0] hover:text-gray-900 dark:text-gray-400 dark:hover:bg-white/10 dark:hover:text-white lg:inline-flex"
          @click="emit('go-record')"
        >
          <RotateCcw :size="14" />
          重新录制
        </button>

        <button
          v-if="primaryLabel"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-[#841cd8] to-[#ac0189] px-4 py-2 text-xs font-extrabold text-white shadow-sm shadow-[#841cd8]/20 transition-opacity hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
          :disabled="primaryDisabled"
          @click="emit('primary-action')"
        >
          <Save v-if="currentStep === 'test' && testState === 'success'" :size="14" />
          <Play v-else :size="14" />
          {{ primaryLabel }}
        </button>
      </div>
    </div>
  </header>
</template>
