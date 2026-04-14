<template>
  <div class="flex h-full min-h-0 overflow-hidden bg-white">
    <LeftPanel />
    <div class="relative h-full min-w-0 flex-1 py-0 pr-0">
      <div class="flex h-full bg-[var(--background-gray-main)]">
        <div class="flex flex-1 min-w-0 min-h-0">
          <router-view :key="$route.params.sessionId" />
          <FilePanel />
        </div>
      </div>
    </div>
  </div>
  <TakeOverView />
  <CustomDialog />
  <SessionFileList />
  <SettingsDialog />
  <ContextMenu />
</template>

<script setup lang="ts">
import LeftPanel from '@/components/LeftPanel.vue';
import CustomDialog from '@/components/ui/CustomDialog.vue';
import ContextMenu from '@/components/ui/ContextMenu.vue';
import TakeOverView from '@/components/TakeOverView.vue';
import SessionFileList from '@/components/SessionFileList.vue';
import FilePanel from '@/components/FilePanel.vue';
import SettingsDialog from '@/components/settings/SettingsDialog.vue';
</script>
