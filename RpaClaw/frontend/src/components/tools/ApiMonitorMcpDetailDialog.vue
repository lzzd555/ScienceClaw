<template>
  <Dialog :open="open" @update:open="handleOpenChange">
    <DialogContent class="flex w-[calc(100vw-16px)] max-w-6xl flex-col max-h-[94vh] overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] p-0 shadow-2xl dark:border-white/10 dark:bg-[#101115]">
      <DialogHeader class="border-b border-slate-200 bg-white/80 px-6 py-5 dark:border-white/10 dark:bg-white/[0.05]">
        <DialogTitle class="flex items-center gap-3 text-xl font-black text-[var(--text-primary)]">
          <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-teal-100 text-teal-700 dark:bg-teal-400/15 dark:text-teal-200">
            <Server :size="20" />
          </div>
          {{ detail?.server.name || server?.name || t('API Monitor MCP') }}
        </DialogTitle>
        <DialogDescription class="mt-1 text-sm text-[var(--text-tertiary)]">
          {{ detail?.server.description || t('API Monitor MCP description') }}
        </DialogDescription>
      </DialogHeader>

      <div class="flex-1 overflow-y-auto min-h-0 p-6">
        <div v-if="loading" class="flex min-h-[360px] items-center justify-center">
          <div class="inline-flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm font-semibold text-[var(--text-secondary)] shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <Loader2 class="animate-spin" :size="18" />
            {{ t('Loading API Monitor MCP detail...') }}
          </div>
        </div>

        <div v-else-if="detail" class="space-y-6">
          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="space-y-3">
              <div>
                <div class="text-xs font-black uppercase tracking-[0.14em] text-teal-600 dark:text-teal-300">{{ t('MCP Overview') }}</div>
                <h3 class="mt-2 text-2xl font-black text-[var(--text-primary)]">{{ detail.server.name }}</h3>
                <p class="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">{{ detail.server.description || t('No description') }}</p>
              </div>
              <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <div class="detail-chip">
                  <span class="detail-chip-label">{{ t('Base URL') }}</span>
                  <span class="font-mono text-xs text-[var(--text-primary)]">{{ detail.server.endpoint_config?.url || '-' }}</span>
                </div>
                <div class="detail-chip">
                  <span class="detail-chip-label">{{ t('Enabled') }}</span>
                  <span :class="detail.server.enabled ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300'">
                    {{ detail.server.enabled ? t('Enabled') : t('Disabled') }}
                  </span>
                </div>
                <div class="detail-chip">
                  <span class="detail-chip-label">{{ t('Default enabled') }}</span>
                  <span :class="detail.server.default_enabled ? 'text-emerald-700 dark:text-emerald-300' : 'text-slate-600 dark:text-slate-300'">
                    {{ detail.server.default_enabled ? t('Yes') : t('No') }}
                  </span>
                </div>
                <div class="detail-chip">
                  <span class="detail-chip-label">{{ t('Tool count') }}</span>
                  <span class="text-[var(--text-primary)]">{{ detail.tools.length }}</span>
                </div>
                <div class="detail-chip">
                  <span class="detail-chip-label">{{ t('Authentication') }}</span>
                  <span :class="detail.server.api_monitor_auth?.credential_id ? 'text-emerald-700 dark:text-emerald-300' : 'text-amber-700 dark:text-amber-300'">
                    {{ apiMonitorAuthStatusLabel() }}
                  </span>
                </div>
              </div>
            </div>
          </section>

          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <div class="flex items-center gap-2">
                  <ShieldCheck :size="18" class="text-teal-600 dark:text-teal-300" />
                  <h3 class="text-base font-black text-[var(--text-primary)]">{{ t('External MCP Access') }}</h3>
                  <span
                    class="rounded-full px-2.5 py-1 text-[11px] font-bold"
                    :class="externalAccess?.enabled ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300' : 'bg-slate-100 text-slate-600 dark:bg-white/10 dark:text-slate-300'"
                  >
                    {{ externalAccess?.enabled ? t('Enabled') : t('Disabled') }}
                  </span>
                </div>
                <p class="mt-1 text-sm text-[var(--text-tertiary)]">
                  {{ formatCallerAuthRequirement(externalAccess?.caller_auth_requirements) }}
                </p>
              </div>
              <div class="flex flex-wrap gap-2">
                <button
                  v-if="!externalAccess?.enabled"
                  class="inline-flex items-center gap-1.5 rounded-xl bg-teal-600 px-4 py-2 text-xs font-bold text-white shadow-sm transition disabled:cursor-not-allowed disabled:opacity-60"
                  :disabled="externalAccessBusy === 'enable'"
                  @click="enableExternalAccess"
                >
                  <Loader2 v-if="externalAccessBusy === 'enable'" class="animate-spin" :size="14" />
                  <Power v-else :size="14" />
                  {{ t('Enable external access') }}
                </button>
                <button
                  v-else
                  class="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-[var(--text-secondary)] shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.08]"
                  :disabled="externalAccessBusy === 'disable'"
                  @click="disableExternalAccess"
                >
                  <Loader2 v-if="externalAccessBusy === 'disable'" class="animate-spin" :size="14" />
                  <Power v-else :size="14" />
                  {{ t('Disable external access') }}
                </button>
                <button
                  class="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-[var(--text-secondary)] shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.08]"
                  :disabled="!externalAccess?.url"
                  @click="copyExternalText(externalAccess?.url || '', 'External MCP URL copied')"
                >
                  <Copy :size="14" />
                  {{ t('Copy URL') }}
                </button>
              </div>
            </div>

            <div class="grid gap-3">
              <div class="detail-chip">
                <span class="detail-chip-label">{{ t('MCP URL') }}</span>
                <span class="break-all font-mono text-xs text-[var(--text-primary)]">{{ externalAccess?.url || '-' }}</span>
              </div>
            </div>
          </section>

          <!-- Token Flow Summary -->
          <section
            v-if="detail.server.api_monitor_auth?.token_flows?.length"
            class="rounded-3xl border border-sky-200 bg-sky-50/30 p-5 shadow-sm dark:border-sky-800/40 dark:bg-sky-950/10"
          >
            <div class="mb-3 flex items-center gap-2">
              <div class="flex h-5 w-5 items-center justify-center rounded-full bg-sky-500 text-[10px] font-bold text-white">
                <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              </div>
              <h3 class="text-base font-black text-[var(--text-primary)]">{{ t('Dynamic Token Flows') }}</h3>
              <span class="rounded-full bg-sky-100 px-2 py-0.5 text-[10px] font-bold text-sky-700 dark:bg-sky-900/40 dark:text-sky-300">
                {{ detail.server.api_monitor_auth.token_flows.length }}
              </span>
            </div>
            <div class="space-y-2">
              <div
                v-for="flow in detail.server.api_monitor_auth.token_flows"
                :key="flow.id"
                class="rounded-xl border border-slate-200 bg-white px-3 py-2.5 dark:border-white/10 dark:bg-white/[0.04]"
              >
                <div class="flex items-center gap-2">
                  <span class="text-sm font-bold text-[var(--text-primary)]">{{ flow.name }}</span>
                  <span
                    class="rounded-md px-1.5 py-0.5 text-[10px] font-bold"
                    :class="{
                      'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400': flow.confidence === 'high',
                      'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400': flow.confidence === 'medium',
                      'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400': flow.confidence === 'low',
                      'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400': flow.confidence === 'manual',
                    }"
                  >{{ flow.confidence }}</span>
                  <span v-if="flow.source" class="rounded-md px-1.5 py-0.5 text-[10px] font-bold bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400">
                    {{ flow.source }}
                  </span>
                </div>
                <div v-if="flow.summary" class="mt-1 text-xs text-[var(--text-tertiary)]">
                  <div>{{ t('Source') }}: {{ flow.summary.producer }}</div>
                  <div v-for="cs in flow.summary.consumers" :key="cs">{{ t('Inject to') }}: {{ cs }}</div>
                  <div v-if="flow.summary.sample_count && flow.summary.sample_count > 1" class="mt-1 text-[11px] opacity-70">
                    {{ t('Samples: {count}', { count: flow.summary.sample_count }) }}
                  </div>
                </div>
                <div v-else-if="flow.producer" class="mt-1 text-xs text-[var(--text-tertiary)]">
                  <div>{{ flow.producer.request?.method }} {{ flow.producer.request?.url }}</div>
                </div>
              </div>
            </div>
          </section>

          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="mb-5 flex items-center justify-between gap-3">
              <div>
                <h3 class="text-base font-black text-[var(--text-primary)]">{{ t('Captured tools') }}</h3>
                <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ t('API Monitor tool detail hint') }}</p>
              </div>
              <span class="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-[var(--text-secondary)] dark:bg-white/10">
                {{ t('tool count summary', { count: detail.tools.length }) }}
              </span>
            </div>

            <div v-if="detail.tools.length === 0" class="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-[var(--text-tertiary)] dark:border-white/10">
              {{ t('No API Monitor tools yet') }}
            </div>

            <div v-else class="space-y-4">
              <article
                v-for="tool in detail.tools"
                :key="tool.id"
                class="overflow-hidden rounded-2xl border border-slate-200 bg-slate-50/80 dark:border-white/10 dark:bg-white/[0.03]"
              >
                <button class="flex w-full items-center gap-4 px-5 py-4 text-left transition hover:bg-white/70 dark:hover:bg-white/[0.05]" @click="toggleExpanded(tool.id)">
                  <div class="min-w-0 flex-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="rounded-md bg-blue-100 px-2 py-1 text-[10px] font-black uppercase tracking-[0.12em] text-blue-700 dark:bg-blue-500/15 dark:text-blue-200">{{ tool.method }}</span>
                      <h4 class="truncate text-sm font-black text-[var(--text-primary)]">{{ tool.name }}</h4>
                      <span class="rounded-full px-2.5 py-1 text-[11px] font-bold" :class="statusClass(tool.validation_status)">
                        {{ formatValidationStatus(tool.validation_status) }}
                      </span>
                    </div>
                    <p class="mt-2 truncate font-mono text-xs text-[var(--text-tertiary)]">{{ tool.url }}</p>
                  </div>
                  <ChevronDown class="shrink-0 text-[var(--text-tertiary)] transition" :class="expandedToolIds.has(tool.id) ? 'rotate-180' : ''" :size="18" />
                </button>

                <div v-if="expandedToolIds.has(tool.id)" class="border-t border-slate-200 bg-white/80 px-5 py-6 dark:border-white/10 dark:bg-[#111317]">
                  <div class="grid gap-8 xl:grid-cols-2">
                    <!-- Left Column: Edit Tool -->
                    <div class="flex flex-col gap-4">
                      <div class="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-white/10">
                        <h5 class="text-sm font-black text-[var(--text-primary)] flex items-center gap-2">
                          <Wrench :size="16" class="text-teal-600 dark:text-teal-400" />
                          {{ t('Edit Tool') }}
                        </h5>
                        <button
                          class="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-br from-[#8930b0] to-[#004be2] px-4 py-1.5 text-xs font-bold text-white shadow-md transition disabled:cursor-not-allowed disabled:opacity-60"
                          :disabled="toolStates[tool.id]?.saving"
                          @click="saveTool(tool.id)"
                        >
                          <Loader2 v-if="toolStates[tool.id]?.saving" class="animate-spin" :size="14" />
                          <Save v-else :size="14" />
                          {{ toolStates[tool.id]?.saving ? t('Saving...') : t('Save Tool') }}
                        </button>
                      </div>
                      <div class="grid gap-4 sm:grid-cols-2">
                        <label class="field">
                          <span>{{ t('Tool name') }}</span>
                          <input
                            :value="toolStates[tool.id]?.name ?? ''"
                            class="tools-input"
                            @input="updateToolField(tool.id, 'name', ($event.target as HTMLInputElement).value)"
                          />
                        </label>
                        <label class="field">
                          <span>{{ t('Tool description') }}</span>
                          <input
                            :value="toolStates[tool.id]?.description ?? ''"
                            class="tools-input"
                            @input="updateToolField(tool.id, 'description', ($event.target as HTMLInputElement).value)"
                          />
                        </label>
                      </div>
                      <label class="field flex-1 min-h-0">
                        <span class="flex items-center justify-between">
                          {{ t('YAML definition') }}
                          <span v-if="toolStates[tool.id]?.isDirty" class="rounded-md bg-amber-50 px-1.5 py-0.5 text-[10px] font-black text-amber-600 dark:bg-amber-500/10 dark:text-amber-400">
                            {{ t('Unsaved changes') }}
                          </span>
                        </span>
                        <textarea
                          :value="toolStates[tool.id]?.yamlDefinition ?? ''"
                          class="tools-input min-h-[280px] h-full resize-y font-mono text-[11px] bg-slate-50/50 dark:bg-black/20"
                          spellcheck="false"
                          @input="updateToolYaml(tool.id, ($event.target as HTMLTextAreaElement).value)"
                        ></textarea>
                      </label>
                      <div v-if="tool.validation_errors?.length" class="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-400/20 dark:bg-amber-500/10">
                        <div class="text-xs font-black text-amber-800 dark:text-amber-200">{{ t('Validation errors') }}</div>
                        <ul class="mt-1.5 space-y-1 text-xs text-amber-700 dark:text-amber-100">
                          <li v-for="(error, index) in tool.validation_errors" :key="`${tool.id}-error-${index}`">• {{ error }}</li>
                        </ul>
                      </div>
                    </div>

                    <!-- Right Column: Test Tool -->
                    <div class="flex flex-col gap-4">
                      <div class="flex items-center justify-between border-b border-slate-200 pb-3 dark:border-white/10">
                        <h5 class="text-sm font-black text-[var(--text-primary)] flex items-center gap-2">
                          <Terminal :size="16" class="text-sky-600 dark:text-sky-400" />
                          {{ t('Test Tool') }}
                        </h5>
                        <button
                          class="inline-flex items-center gap-1.5 rounded-xl border border-sky-200 dark:border-sky-800/50 bg-sky-50 dark:bg-sky-900/20 px-4 py-1.5 text-xs font-bold text-sky-700 dark:text-sky-300 shadow-sm transition hover:bg-sky-100 dark:hover:bg-sky-900/40 disabled:cursor-not-allowed disabled:opacity-60"
                          :disabled="toolStates[tool.id]?.testing || toolStates[tool.id]?.isDirty"
                          @click="testTool(tool.id)"
                        >
                          <Loader2 v-if="toolStates[tool.id]?.testing" class="animate-spin" :size="14" />
                          <Play v-else :size="14" />
                          {{ toolStates[tool.id]?.testing ? t('Testing...') : t('Run Test') }}
                        </button>
                      </div>
                      
                      <p v-if="toolStates[tool.id]?.isDirty" class="text-xs text-amber-600 dark:text-amber-400 font-medium">
                        {{ t('API Monitor draft save before test hint') }}
                      </p>

                      <label class="field">
                        <span>{{ t('Test arguments') }}</span>
                        <textarea
                          :value="toolStates[tool.id]?.testArgumentsText ?? '{}'"
                          class="tools-input h-[140px] resize-y font-mono text-[11px] bg-slate-50/50 dark:bg-black/20"
                          spellcheck="false"
                          @input="updateTestArguments(tool.id, ($event.target as HTMLTextAreaElement).value)"
                        ></textarea>
                      </label>
                      
                      <div class="field flex-1 min-h-[200px] flex flex-col mt-2">
                        <span class="flex items-center justify-between">
                          {{ t('Test result') }}
                          <span v-if="toolStates[tool.id]?.testResult" class="rounded-md px-2 py-0.5 text-[10px] font-black uppercase tracking-wider" :class="(toolStates[tool.id]?.testResult as any).success ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300' : 'bg-red-100 text-red-700 dark:bg-red-500/20 dark:text-red-300'">
                            {{ (toolStates[tool.id]?.testResult as any).success ? t('Success') : t('Failed') }}
                          </span>
                        </span>
                        <div class="relative flex-1 rounded-xl border border-slate-200 bg-white dark:border-white/10 dark:bg-black/20 overflow-hidden shadow-inner">
                          <div v-if="!toolStates[tool.id]?.testResult && !toolStates[tool.id]?.testing" class="absolute inset-0 flex flex-col items-center justify-center text-slate-400 dark:text-slate-500 gap-2">
                            <Play :size="32" class="opacity-20" />
                            <span class="text-xs font-medium">{{ t('Click "Run Test" to see results') }}</span>
                          </div>
                          <div v-else-if="toolStates[tool.id]?.testing" class="absolute inset-0 flex flex-col items-center justify-center text-sky-500 gap-3">
                            <Loader2 class="animate-spin" :size="28" />
                            <span class="text-xs font-bold">{{ t('Waiting for response...') }}</span>
                          </div>
                          <pre v-else class="absolute inset-0 p-4 overflow-auto font-mono text-[11px] text-[var(--text-secondary)]"><code>{{ prettyJson(toolStates[tool.id]?.testResult ?? {}) }}</code></pre>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </article>
            </div>
          </section>
        </div>
      </div>
    </DialogContent>
  </Dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { ChevronDown, Copy, Loader2, Play, Power, Save, Server, ShieldCheck, Terminal, Wrench } from 'lucide-vue-next';
import { parse as parseYaml } from 'yaml';
import {
  disableApiMonitorExternalAccess,
  enableApiMonitorExternalAccess,
  getApiMonitorMcpDetail,
  testApiMonitorMcpTool,
  updateApiMonitorMcpTool,
  type ApiMonitorExternalAccessState,
  type ApiMonitorMcpDetail,
  type ApiMonitorMcpToolDetail,
  type McpServerItem,
} from '@/api/mcp';
import {
  buildSampleArguments,
  formatValidationStatus,
  prettyJson,
  syncYamlTopLevelField,
} from '@/utils/apiMonitorMcp';
import { formatApiMonitorAuthStatus } from '@/utils/apiMonitorAuth';
import {
  formatCallerAuthRequirement,
} from '@/utils/apiMonitorExternalAccess';
import { showErrorToast, showSuccessToast } from '@/utils/toast';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type ToolState = {
  name: string;
  description: string;
  yamlDefinition: string;
  savedYamlDefinition: string;
  previewInputSchema: Record<string, unknown>;
  savedInputSchema: Record<string, unknown>;
  sampleArguments: unknown;
  testArgumentsText: string;
  testResult: unknown;
  saving: boolean;
  testing: boolean;
  isDirty: boolean;
};

const props = defineProps<{
  open: boolean;
  server: McpServerItem | null;
}>();

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void;
  (event: 'server-updated', server: McpServerItem): void;
}>();

const { t } = useI18n();

const loading = ref(false);
const detail = ref<ApiMonitorMcpDetail | null>(null);
const expandedToolIds = ref<Set<string>>(new Set());
const toolStates = reactive<Record<string, ToolState>>({});
const activeLoadToken = ref(0);
const externalAccess = ref<ApiMonitorExternalAccessState | null>(null);
const externalAccessBusy = ref<'enable' | 'disable' | ''>('');

function handleOpenChange(value: boolean) {
  emit('update:open', value);
}

function resetToolStates() {
  Object.keys(toolStates).forEach((key) => {
    delete toolStates[key];
  });
}

function clearDetailState() {
  detail.value = null;
  externalAccess.value = null;
  expandedToolIds.value = new Set();
  resetToolStates();
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function apiMonitorAuthStatusLabel() {
  const status = formatApiMonitorAuthStatus(detail.value?.server.api_monitor_auth);
  return status === 'configured' ? t('Configured') : t('No credential');
}

function parseYamlDraft(yamlText: string): { name?: string; description?: string; parameters?: Record<string, unknown> } | null {
  try {
    const parsed = parseYaml(yamlText);
    if (!isPlainObject(parsed)) {
      return null;
    }
    return {
      name: typeof parsed.name === 'string' ? parsed.name : undefined,
      description: typeof parsed.description === 'string' ? parsed.description : undefined,
      parameters: isPlainObject(parsed.parameters) ? parsed.parameters : undefined,
    };
  } catch {
    return null;
  }
}

function safeJsonStringify(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return '{}';
  }
}

function syncToolStateFromYaml(toolId: string) {
  const state = toolStates[toolId];
  if (!state) return;
  const parsedDraft = parseYamlDraft(state.yamlDefinition);
  state.isDirty = state.yamlDefinition !== state.savedYamlDefinition;
  state.name = parsedDraft?.name ?? parseYamlDraft(state.savedYamlDefinition)?.name ?? state.name;
  state.description = parsedDraft?.description ?? parseYamlDraft(state.savedYamlDefinition)?.description ?? state.description;
  state.previewInputSchema = parsedDraft?.parameters ?? state.savedInputSchema;
  state.sampleArguments = buildSampleArguments(state.previewInputSchema);
}

function clearToolTestResult(toolId: string) {
  const state = toolStates[toolId];
  if (!state) return;
  state.testResult = null;
}

function applyToolState(tool: ApiMonitorMcpToolDetail) {
  const sampleArgs = buildSampleArguments(tool.input_schema as Record<string, unknown>);
  toolStates[tool.id] = {
    name: tool.name || '',
    description: tool.description || '',
    yamlDefinition: tool.yaml_definition || '',
    savedYamlDefinition: tool.yaml_definition || '',
    previewInputSchema: (tool.input_schema as Record<string, unknown>) || {},
    savedInputSchema: (tool.input_schema as Record<string, unknown>) || {},
    sampleArguments: sampleArgs,
    testArgumentsText: safeJsonStringify(sampleArgs),
    testResult: toolStates[tool.id]?.testResult ?? null,
    saving: false,
    testing: false,
    isDirty: false,
  };
  syncToolStateFromYaml(tool.id);
}

function applyDetail(nextDetail: ApiMonitorMcpDetail) {
  detail.value = nextDetail;
  externalAccess.value = nextDetail.server.external_access ?? null;
  resetToolStates();
  nextDetail.tools.forEach((tool) => applyToolState(tool));
  expandedToolIds.value = new Set(nextDetail.tools.length > 0 ? [nextDetail.tools[0].id] : []);
}

async function enableExternalAccess() {
  if (!detail.value?.server.server_key) return;
  externalAccessBusy.value = 'enable';
  try {
    const state = await enableApiMonitorExternalAccess(detail.value.server.server_key);
    externalAccess.value = state;
    showSuccessToast(t('API Monitor external access enabled'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to update API Monitor external access'));
  } finally {
    externalAccessBusy.value = '';
  }
}

async function disableExternalAccess() {
  if (!detail.value?.server.server_key) return;
  externalAccessBusy.value = 'disable';
  try {
    externalAccess.value = await disableApiMonitorExternalAccess(detail.value.server.server_key);
    showSuccessToast(t('API Monitor external access disabled'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to update API Monitor external access'));
  } finally {
    externalAccessBusy.value = '';
  }
}

async function copyExternalText(value: string, messageKey: string) {
  if (!value) return;
  await navigator.clipboard.writeText(value);
  showSuccessToast(t(messageKey));
}

async function loadDetail() {
  if (!props.open || !props.server?.server_key) return;
  const loadToken = activeLoadToken.value + 1;
  activeLoadToken.value = loadToken;
  loading.value = true;
  clearDetailState();
  try {
    const nextDetail = await getApiMonitorMcpDetail(props.server.server_key);
    if (activeLoadToken.value !== loadToken) return;
    applyDetail(nextDetail);
  } catch (error: any) {
    if (activeLoadToken.value !== loadToken) return;
    console.error(error);
    showErrorToast(error?.message || t('Failed to load API Monitor MCP detail'));
  } finally {
    if (activeLoadToken.value === loadToken) {
      loading.value = false;
    }
  }
}

function updateToolField(toolId: string, field: 'name' | 'description', value: string) {
  const state = toolStates[toolId];
  if (!state) return;
  state[field] = value;
  state.yamlDefinition = syncYamlTopLevelField(state.yamlDefinition, field, value);
  clearToolTestResult(toolId);
  syncToolStateFromYaml(toolId);
}

function updateToolYaml(toolId: string, value: string) {
  const state = toolStates[toolId];
  if (!state) return;
  state.yamlDefinition = value;
  clearToolTestResult(toolId);
  syncToolStateFromYaml(toolId);
  // Update test arguments when YAML parameters change
  const newSample = buildSampleArguments(state.previewInputSchema);
  state.sampleArguments = newSample;
  state.testArgumentsText = safeJsonStringify(newSample);
}

function updateTestArguments(toolId: string, value: string) {
  const state = toolStates[toolId];
  if (!state) return;
  state.testArgumentsText = value;
}

function toggleExpanded(toolId: string) {
  const next = new Set(expandedToolIds.value);
  if (next.has(toolId)) {
    next.delete(toolId);
  } else {
    next.add(toolId);
  }
  expandedToolIds.value = next;
}

function statusClass(status?: string | null) {
  const normalized = (status || '').trim().toLowerCase();
  if (normalized === 'valid') {
    return 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300';
  }
  if (normalized === 'invalid') {
    return 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300';
  }
  return 'bg-slate-100 text-slate-700 dark:bg-white/10 dark:text-slate-300';
}

function parseTestArguments(text: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(text.trim() || '{}');
    if (isPlainObject(parsed)) return parsed;
  } catch {
    // fall through
  }
  return {};
}

async function saveTool(toolId: string) {
  if (!detail.value?.server.server_key) return;
  const state = toolStates[toolId];
  if (!state) return;
  const preservedTestResult = state.testResult;
  state.saving = true;
  try {
    const updated = await updateApiMonitorMcpTool(detail.value.server.server_key, toolId, {
      yaml_definition: state.yamlDefinition,
    });
    const index = detail.value.tools.findIndex((tool) => tool.id === toolId);
    if (index >= 0) {
      detail.value.tools[index] = updated;
      applyToolState(updated);
      toolStates[toolId].testResult = null;
    }
    showSuccessToast(t('API Monitor tool saved'));
  } catch (error: any) {
    state.testResult = preservedTestResult;
    console.error(error);
    showErrorToast(error?.message || t('Failed to save API Monitor tool'));
  } finally {
    state.saving = false;
  }
}

async function testTool(toolId: string) {
  if (!detail.value?.server.server_key) return;
  const state = toolStates[toolId];
  if (!state) return;
  state.testing = true;
  try {
    const args = parseTestArguments(state.testArgumentsText);
    const result = await testApiMonitorMcpTool(detail.value.server.server_key, toolId, {
      arguments: args,
    });
    state.testResult = result;
    showSuccessToast(result.success ? t('API Monitor tool test succeeded') : t('API Monitor tool test finished'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to test API Monitor tool'));
  } finally {
    state.testing = false;
  }
}

watch(
  () => [props.open, props.server?.server_key],
  async ([open]) => {
    if (open) {
      await loadDetail();
      return;
    }
    activeLoadToken.value += 1;
    loading.value = false;
    clearDetailState();
  },
  { immediate: true },
);
</script>

<style scoped>
.detail-chip {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.35rem;
  border-radius: 1rem;
  border: 1px solid rgba(226, 232, 240, 0.9);
  background: rgba(248, 250, 252, 0.9);
  padding: 0.85rem 1rem;
  font-size: 0.875rem;
  font-weight: 700;
}

.detail-chip-label {
  font-size: 0.7rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-tertiary);
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
}

.field > span {
  font-size: 0.8rem;
  font-weight: 800;
  color: var(--text-secondary);
}

.tools-input {
  width: 100%;
  border-radius: 1rem;
  border: 1px solid rgba(226, 232, 240, 0.95);
  background: white;
  padding: 0.8rem 0.95rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}

.tools-input:focus {
  border-color: rgba(37, 99, 235, 0.55);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.12);
}

.preview-card {
  overflow: hidden;
  border-radius: 1rem;
  border: 1px solid rgba(226, 232, 240, 0.9);
  background: rgba(248, 250, 252, 0.85);
}

.preview-title {
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
  padding: 0.85rem 1rem;
  font-size: 0.75rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-secondary);
}

.preview-code {
  max-height: 280px;
  overflow: auto;
  padding: 1rem;
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--text-secondary);
}

.dark .detail-chip,
.dark .preview-card {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.04);
}

.dark .preview-title {
  border-color: rgba(255, 255, 255, 0.08);
}

.dark .tools-input {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.05);
}
</style>
