<template>
  <div
    class="h-[36px] flex items-center px-3 w-full bg-[var(--background-gray-main)] border-b border-[var(--border-main)] rounded-t-[12px] shadow-[inset_0px_1px_0px_0px_#FFFFFF] dark:shadow-[inset_0px_1px_0px_0px_#FFFFFF30]">
    <div class="flex-1 flex items-center justify-center">
      <div class="max-w-[250px] truncate text-[var(--text-tertiary)] text-sm font-medium text-center">
        MCP Tool
      </div>
    </div>
  </div>
  <div class="flex-1 min-h-0 w-full overflow-y-auto">
    <div class="flex-1 min-h-0 max-w-[640px] mx-auto">
      <div class="flex flex-col overflow-auto h-full px-4 py-3">
        <div class="py-3 pt-0">
          <div class="text-[var(--text-primary)] text-sm font-medium mb-2">
            {{ t('Tool') }}: {{ displayName }}
          </div>
          
          <div v-if="toolContent.args && Object.keys(toolContent.args).length > 0" class="mb-4">
            <div class="text-[var(--text-primary)] text-sm font-medium mb-2">{{ t('Arguments') }}:</div>
            <pre class="bg-[var(--fill-tsp-gray-main)] rounded-lg p-3 text-xs text-[var(--text-secondary)] overflow-x-auto"><code>{{ JSON.stringify(toolContent.args, null, 2) }}</code></pre>
          </div>

          <div v-if="requestPreviewText" class="mb-4">
            <div class="text-[var(--text-primary)] text-sm font-medium mb-2">{{ t('API request preview') }}:</div>
            <pre class="bg-[var(--fill-tsp-gray-main)] rounded-lg p-3 text-xs text-[var(--text-secondary)] whitespace-pre-wrap overflow-x-auto"><code>{{ requestPreviewText }}</code></pre>
          </div>
          
          <div v-if="toolContent.content?.result" class="mb-4">
            <div class="text-[var(--text-primary)] text-sm font-medium mb-2">{{ t('Result') }}:</div>
            <div class="bg-[var(--fill-tsp-gray-main)] rounded-lg p-3 text-sm text-[var(--text-secondary)] whitespace-pre-wrap">
              {{ toolContent.content.result }}
            </div>
          </div>
          
          <div v-else class="text-[var(--text-tertiary)] text-sm">
            {{ toolContent.status === 'calling' ? t('Tool is executing...') : t('Waiting for result...') }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ToolContent } from '@/types/message';
import type { ApiMonitorRequestPreview } from '@/utils/mcpUi';
import { formatApiMonitorRequestPreview, formatMcpToolDisplayName } from '@/utils/mcpUi';

const { t } = useI18n()

const props = defineProps<{
  sessionId: string;
  toolContent: ToolContent;
  live: boolean;
}>();

const displayName = computed(() => formatMcpToolDisplayName({
  functionName: props.toolContent.function,
  fallbackName: props.toolContent.name,
  meta: props.toolContent.tool_meta,
}));

const requestPreview = computed<ApiMonitorRequestPreview | null>(() => {
  const content = props.toolContent.content;
  if (!content || typeof content !== 'object') {
    return null;
  }

  const preview = (content as { request_preview?: unknown }).request_preview;
  if (!preview || typeof preview !== 'object' || Array.isArray(preview)) {
    return null;
  }

  return preview as ApiMonitorRequestPreview;
});

const requestPreviewText = computed(() => {
  if (!requestPreview.value) {
    return '';
  }

  return formatApiMonitorRequestPreview(requestPreview.value);
});
</script> 
