<template>
  <Popover v-model:open="open">
    <PopoverTrigger as-child>
      <div
        class="flex h-8 max-w-full cursor-pointer items-center gap-2 rounded-full border border-[var(--border-light)] bg-[var(--background-white-main)] px-3 py-1 transition-colors hover:border-[var(--border-main)]"
        :title="tooltip"
      >
        <Wrench :size="14" class="shrink-0 text-[var(--text-secondary)]" />
        <span class="truncate text-xs font-medium text-[var(--text-secondary)]">{{ label }}</span>
        <span class="rounded-full bg-blue-50 px-1.5 py-0.5 text-[10px] font-bold text-blue-600 dark:bg-blue-950/30 dark:text-blue-300">
          {{ t('enabled MCP count', { count: effectiveCount }) }}
        </span>
      </div>
    </PopoverTrigger>
    <PopoverContent class="w-[360px] overflow-hidden rounded-xl border border-[var(--border-light)] bg-[var(--background-white-main)] p-0 shadow-xl" align="start" :side-offset="8">
      <div class="border-b border-[var(--border-light)] bg-[var(--background-gray-main)] px-3 py-2">
        <div class="text-xs font-semibold text-[var(--text-primary)]">{{ title || label }}</div>
        <div class="mt-0.5 text-[11px] leading-4 text-[var(--text-tertiary)]">{{ description }}</div>
      </div>
      <div class="max-h-[360px] overflow-y-auto p-1.5">
        <div v-if="loading" class="flex flex-col items-center gap-2 px-3 py-6 text-center text-xs text-[var(--text-tertiary)]">
          <div class="h-4 w-4 animate-spin rounded-full border-2 border-[var(--border-main)] border-t-[var(--text-primary)]"></div>
          {{ t('Loading MCP servers...') }}
        </div>
        <div v-else-if="servers.length === 0" class="px-3 py-6 text-center text-xs text-[var(--text-tertiary)]">
          {{ t('No MCP servers available for this session.') }}
        </div>
        <div v-else class="flex flex-col gap-0.5">
          <div
            v-for="server in servers"
            :key="server.server_key"
            class="flex items-center gap-2.5 rounded-lg border border-transparent px-3 py-2 transition-all hover:border-[var(--border-light)] hover:bg-[var(--fill-tsp-gray-main)]"
          >
            <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--border-light)] bg-[var(--background-gray-main)] text-[var(--text-secondary)] shadow-sm">
              <Wrench :size="14" />
            </div>
            <div class="min-w-0 flex-1">
              <div class="flex min-w-0 items-center gap-1.5">
                <span class="truncate text-sm font-medium text-[var(--text-primary)]">{{ server.name }}</span>
                <span class="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-medium text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {{ server.scope === 'system' ? t('Platform') : t('Private') }}
                </span>
              </div>
              <div class="mt-0.5 truncate text-[10px] text-[var(--text-tertiary)]">
                {{ isEffective(server) ? t('Currently enabled') : t('Currently disabled') }}
              </div>
            </div>
            <select
              :value="selectedMode(server)"
              class="shrink-0 rounded-lg border border-[var(--border-light)] bg-[var(--background-white-main)] px-2 py-1.5 text-[11px] text-[var(--text-secondary)]"
              @change="emit('update-mode', server.server_key, ($event.target as HTMLSelectElement).value as McpSessionMode)"
            >
              <option value="inherit">{{ t('Follow default') }}</option>
              <option value="enabled">{{ t('Enable for this session') }}</option>
              <option value="disabled">{{ t('Disable for this session') }}</option>
            </select>
          </div>
        </div>
      </div>
    </PopoverContent>
  </Popover>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { Wrench } from 'lucide-vue-next';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import type { McpServerItem, McpSessionMode } from '../api/mcp';

type SelectableMcpServer = McpServerItem & {
  session_mode?: McpSessionMode;
};

const props = defineProps<{
  label: string;
  title?: string;
  description: string;
  tooltip: string;
  servers: SelectableMcpServer[];
  loading?: boolean;
  modes?: Record<string, McpSessionMode>;
}>();

const emit = defineEmits<{
  (e: 'open'): void;
  (e: 'update-mode', serverKey: string, mode: McpSessionMode): void;
}>();

const { t } = useI18n();
const open = ref(false);

watch(open, (value) => {
  if (value) emit('open');
});

const selectedMode = (server: SelectableMcpServer): McpSessionMode => (
  props.modes?.[server.server_key] || server.session_mode || 'inherit'
);

const isEffective = (server: SelectableMcpServer) => {
  const mode = selectedMode(server);
  return server.enabled && (mode === 'enabled' || (mode === 'inherit' && server.default_enabled));
};

const effectiveCount = computed(() => props.servers.filter((server) => isEffective(server)).length);
</script>
