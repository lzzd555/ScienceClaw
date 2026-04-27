<script setup lang="ts">
import { AlertTriangle, RotateCcw, X } from 'lucide-vue-next';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DISCARD_RECORDING_CONFIRMATION } from '@/utils/rpaFlowGuide';

defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  'update:open': [open: boolean];
  confirm: [];
}>();

const close = () => {
  emit('update:open', false);
};

const confirm = () => {
  emit('confirm');
  close();
};
</script>

<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="w-[min(520px,calc(100vw-32px))] overflow-hidden rounded-2xl border-0 bg-white p-0 shadow-[0_28px_80px_rgba(25,28,30,0.22)] dark:bg-[#272728]">
      <div class="bg-[#f8f9fb] px-5 py-4 dark:bg-[#212122]">
        <DialogHeader class="text-left">
          <div class="flex items-start gap-3 pr-8">
            <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-rose-50 text-rose-600 ring-1 ring-rose-100 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-900/60">
              <AlertTriangle :size="20" />
            </div>
            <div class="min-w-0">
              <DialogTitle class="text-base font-extrabold text-gray-950 dark:text-gray-100">
                {{ DISCARD_RECORDING_CONFIRMATION.title }}
              </DialogTitle>
              <p class="mt-1 text-xs font-semibold text-gray-500 dark:text-gray-400">
                将开启一个全新的录制会话
              </p>
            </div>
          </div>
        </DialogHeader>
      </div>

      <div class="px-5 py-5">
        <p class="text-sm leading-6 text-gray-700 dark:text-gray-300">
          {{ DISCARD_RECORDING_CONFIRMATION.message }}
        </p>
        <div class="mt-4 rounded-xl bg-[#f2f4f6] px-4 py-3 text-xs leading-5 text-gray-600 dark:bg-white/[0.06] dark:text-gray-400">
          当前录制步骤、参数配置、脚本预览和测试结果不会带入新录制。
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 bg-[#f8f9fb] px-5 py-4 dark:bg-[#212122]">
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-bold text-gray-600 transition-colors hover:bg-[#edeef0] hover:text-gray-900 dark:text-gray-300 dark:hover:bg-white/10 dark:hover:text-white"
          @click="close"
        >
          <X :size="15" />
          {{ DISCARD_RECORDING_CONFIRMATION.cancelText }}
        </button>
        <button
          type="button"
          class="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-r from-[#841cd8] to-[#ac0189] px-4 py-2 text-sm font-extrabold text-white shadow-sm shadow-[#841cd8]/20 transition-opacity hover:opacity-95"
          @click="confirm"
        >
          <RotateCcw :size="15" />
          {{ DISCARD_RECORDING_CONFIRMATION.confirmText }}
        </button>
      </div>
    </DialogContent>
  </Dialog>
</template>
