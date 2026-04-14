<template>
  <header
    class="desktop-title-bar flex h-10 items-center border-b border-[var(--border-main)] bg-[var(--background-white-main)] text-[var(--text-primary)]"
  >
    <div class="desktop-title-bar__brand flex min-w-0 items-center px-3 text-sm font-semibold">
      <span class="truncate">RpaClaw</span>
    </div>

    <div class="desktop-title-bar__drag flex h-full flex-1 items-center justify-center px-3">
      <span class="truncate text-xs text-[var(--text-secondary)]">Desktop</span>
    </div>

    <div class="desktop-title-bar__actions flex h-full items-stretch">
      <button
        type="button"
        class="desktop-title-bar__button"
        aria-label="Minimize window"
        @click="controls.minimize()"
      >
        <Minus :size="16" />
      </button>
      <button
        type="button"
        class="desktop-title-bar__button"
        :aria-label="isMaximized ? 'Restore window' : 'Maximize window'"
        @click="handleToggleMaximize"
      >
        <Copy v-if="isMaximized" :size="14" />
        <Square v-else :size="14" />
      </button>
      <button
        type="button"
        class="desktop-title-bar__button desktop-title-bar__button--close"
        aria-label="Close window"
        @click="controls.close()"
      >
        <X :size="16" />
      </button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue';
import { Copy, Minus, Square, X } from 'lucide-vue-next';
import { createDesktopWindowControls } from '@/utils/desktopWindow';

const controls = createDesktopWindowControls();
const isMaximized = ref(false);

let disposeStateListener = () => undefined;

const syncWindowState = async () => {
  isMaximized.value = await controls.isMaximized();
};

const handleToggleMaximize = async () => {
  await controls.toggleMaximize();
  await syncWindowState();
};

onMounted(async () => {
  disposeStateListener = controls.onStateChanged((state) => {
    isMaximized.value = state.maximized;
  });
  await syncWindowState();
});

onUnmounted(() => {
  disposeStateListener();
});
</script>

<style scoped>
.desktop-title-bar {
  flex-shrink: 0;
}

.desktop-title-bar__brand,
.desktop-title-bar__actions,
.desktop-title-bar__button {
  -webkit-app-region: no-drag;
}

.desktop-title-bar__drag {
  -webkit-app-region: drag;
}

.desktop-title-bar__button {
  display: inline-flex;
  width: 2.875rem;
  align-items: center;
  justify-content: center;
  border: 0;
  background: transparent;
  color: inherit;
  transition: background-color 0.15s ease;
}

.desktop-title-bar__button:hover {
  background: color-mix(in srgb, var(--background-gray-main) 75%, transparent);
}

.desktop-title-bar__button--close:hover {
  background: #e5484d;
  color: #fff;
}
</style>
