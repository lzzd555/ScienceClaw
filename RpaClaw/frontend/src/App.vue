<template>
  <div class="app-shell flex h-screen min-h-0 flex-col bg-[var(--background-white-main)]">
    <DesktopTitleBar v-if="showDesktopTitleBar" />
    <div class="app-shell__viewport flex min-h-0 flex-1 flex-col">
      <router-view />
    </div>
    <Toast />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue';
import DesktopTitleBar from './components/DesktopTitleBar.vue';
import Toast from './components/ui/Toast.vue';
import { useTheme } from './composables/useTheme';
import { hasDesktopWindowControls } from './utils/desktopWindow';

const { initTheme } = useTheme();
const showDesktopTitleBar = hasDesktopWindowControls();

onMounted(() => {
  initTheme();
});
</script>
