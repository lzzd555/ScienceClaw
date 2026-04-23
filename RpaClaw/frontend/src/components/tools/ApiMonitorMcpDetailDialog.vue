<template>
  <Dialog :open="open" @update:open="handleOpenChange">
    <DialogContent class="w-[calc(100vw-16px)] max-w-6xl max-h-[94vh] overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] p-0 shadow-2xl dark:border-white/10 dark:bg-[#101115]">
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

      <div class="max-h-[calc(94vh-88px)] overflow-y-auto p-6">
        <div v-if="loading" class="flex min-h-[360px] items-center justify-center">
          <div class="inline-flex items-center gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4 text-sm font-semibold text-[var(--text-secondary)] shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <Loader2 class="animate-spin" :size="18" />
            {{ t('Loading API Monitor MCP detail...') }}
          </div>
        </div>

        <div v-else-if="detail" class="space-y-6">
          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="flex flex-col gap-5 lg:flex-row lg:items-start lg:justify-between">
              <div class="space-y-3">
                <div>
                  <div class="text-xs font-black uppercase tracking-[0.14em] text-teal-600 dark:text-teal-300">{{ t('MCP Overview') }}</div>
                  <h3 class="mt-2 text-2xl font-black text-[var(--text-primary)]">{{ detail.server.name }}</h3>
                  <p class="mt-2 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">{{ detail.server.description || t('No description') }}</p>
                </div>
                <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
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
                </div>
              </div>

              <button
                class="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-[#0f8f88] to-[#0b6ee6] px-4 py-2.5 text-sm font-bold text-white shadow-lg transition disabled:cursor-not-allowed disabled:opacity-60"
                :disabled="savingConfig"
                @click="saveSharedConfig"
              >
                <Loader2 v-if="savingConfig" class="animate-spin" :size="16" />
                {{ savingConfig ? t('Saving...') : t('Save shared config') }}
              </button>
            </div>
          </section>

          <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
            <div class="mb-5 flex items-center gap-3">
              <ShieldCheck :size="18" class="text-violet-600 dark:text-violet-300" />
              <div>
                <h3 class="text-base font-black text-[var(--text-primary)]">{{ t('Shared authentication') }}</h3>
                <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ t('Shared API Monitor auth hint') }}</p>
              </div>
            </div>

            <div class="grid gap-4 lg:grid-cols-2">
              <label class="field">
                <span>{{ t('Shared headers JSON') }}</span>
                <textarea v-model="configForm.headersText" rows="6" class="tools-input min-h-[140px] resize-y font-mono text-xs" spellcheck="false"></textarea>
              </label>
              <label class="field">
                <span>{{ t('Shared query JSON') }}</span>
                <textarea v-model="configForm.queryText" rows="6" class="tools-input min-h-[140px] resize-y font-mono text-xs" spellcheck="false"></textarea>
              </label>
              <label class="field">
                <span>{{ t('Credential headers JSON') }}</span>
                <textarea v-model="configForm.credentialHeadersText" rows="6" class="tools-input min-h-[140px] resize-y font-mono text-xs" spellcheck="false"></textarea>
              </label>
              <label class="field">
                <span>{{ t('Timeout (ms)') }}</span>
                <input v-model.number="configForm.timeoutMs" type="number" min="1" class="tools-input font-mono" />
              </label>
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

                <div v-if="expandedToolIds.has(tool.id)" class="border-t border-slate-200 bg-white/80 px-5 py-5 dark:border-white/10 dark:bg-[#111317]">
                  <div class="grid gap-4 lg:grid-cols-2">
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
                    <label class="field lg:col-span-2">
                      <span>{{ t('YAML definition') }}</span>
                      <textarea
                        :value="toolStates[tool.id]?.yamlDefinition ?? ''"
                        rows="14"
                        class="tools-input min-h-[320px] resize-y font-mono text-xs"
                        spellcheck="false"
                        @input="updateToolYaml(tool.id, ($event.target as HTMLTextAreaElement).value)"
                      ></textarea>
                    </label>
                  </div>

                  <div v-if="tool.validation_errors?.length" class="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 dark:border-amber-400/20 dark:bg-amber-500/10">
                    <div class="text-sm font-black text-amber-800 dark:text-amber-200">{{ t('Validation errors') }}</div>
                    <ul class="mt-2 space-y-1 text-xs text-amber-700 dark:text-amber-100">
                      <li v-for="(error, index) in tool.validation_errors" :key="`${tool.id}-error-${index}`">• {{ error }}</li>
                    </ul>
                  </div>

                  <div class="mt-4 flex flex-wrap items-center gap-3">
                    <button
                      class="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10"
                      :disabled="toolStates[tool.id]?.saving"
                      @click="saveTool(tool.id)"
                    >
                      <Loader2 v-if="toolStates[tool.id]?.saving" class="animate-spin" :size="15" />
                      {{ toolStates[tool.id]?.saving ? t('Saving...') : t('Save tool') }}
                    </button>
                    <button
                      class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-[#8930b0] to-[#004be2] px-4 py-2 text-sm font-bold text-white shadow-lg transition disabled:cursor-not-allowed disabled:opacity-60"
                      :disabled="toolStates[tool.id]?.testing"
                      @click="testTool(tool.id)"
                    >
                      <Loader2 v-if="toolStates[tool.id]?.testing" class="animate-spin" :size="15" />
                      {{ toolStates[tool.id]?.testing ? t('Testing...') : t('Test tool') }}
                    </button>
                  </div>

                  <div class="mt-5 grid gap-4 xl:grid-cols-2">
                    <div class="preview-card">
                      <div class="preview-title">{{ t('Input schema') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(tool.input_schema || {}) }}</code></pre>
                    </div>
                    <div class="preview-card">
                      <div class="preview-title">{{ t('Sample arguments') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(toolStates[tool.id]?.sampleArguments ?? {}) }}</code></pre>
                    </div>
                    <div class="preview-card">
                      <div class="preview-title">{{ t('Path mapping') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(tool.path_mapping || {}) }}</code></pre>
                    </div>
                    <div class="preview-card">
                      <div class="preview-title">{{ t('Query mapping') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(tool.query_mapping || {}) }}</code></pre>
                    </div>
                    <div class="preview-card">
                      <div class="preview-title">{{ t('Body mapping') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(tool.body_mapping || {}) }}</code></pre>
                    </div>
                    <div class="preview-card">
                      <div class="preview-title">{{ t('Header mapping') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(tool.header_mapping || {}) }}</code></pre>
                    </div>
                    <div class="preview-card xl:col-span-2">
                      <div class="preview-title">{{ t('Response schema') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(tool.response_schema || {}) }}</code></pre>
                    </div>
                    <div class="preview-card xl:col-span-2">
                      <div class="preview-title">{{ t('Test result') }}</div>
                      <pre class="preview-code"><code>{{ prettyJson(toolStates[tool.id]?.testResult ?? {}) }}</code></pre>
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
import { ChevronDown, Loader2, Server, ShieldCheck } from 'lucide-vue-next';
import {
  getApiMonitorMcpDetail,
  testApiMonitorMcpTool,
  updateApiMonitorMcpConfig,
  updateApiMonitorMcpTool,
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
import { showErrorToast, showSuccessToast } from '@/utils/toast';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

type ToolState = {
  name: string;
  description: string;
  yamlDefinition: string;
  sampleArguments: unknown;
  testResult: unknown;
  saving: boolean;
  testing: boolean;
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
const savingConfig = ref(false);
const detail = ref<ApiMonitorMcpDetail | null>(null);
const expandedToolIds = ref<Set<string>>(new Set());
const toolStates = reactive<Record<string, ToolState>>({});
const configForm = reactive({
  headersText: '{}',
  queryText: '{}',
  credentialHeadersText: '{}',
  timeoutMs: 20000,
});

function handleOpenChange(value: boolean) {
  emit('update:open', value);
}

function safeJsonStringify(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return '{}';
  }
}

function resetToolStates() {
  Object.keys(toolStates).forEach((key) => {
    delete toolStates[key];
  });
}

function applyToolState(tool: ApiMonitorMcpToolDetail) {
  toolStates[tool.id] = {
    name: tool.name || '',
    description: tool.description || '',
    yamlDefinition: tool.yaml_definition || '',
    sampleArguments: buildSampleArguments(tool.input_schema as Record<string, unknown>),
    testResult: toolStates[tool.id]?.testResult ?? null,
    saving: false,
    testing: false,
  };
}

function applyDetail(nextDetail: ApiMonitorMcpDetail) {
  detail.value = nextDetail;
  configForm.headersText = safeJsonStringify(nextDetail.server.endpoint_config?.headers || {});
  configForm.queryText = safeJsonStringify(nextDetail.server.credential_binding?.query || {});
  configForm.credentialHeadersText = safeJsonStringify(nextDetail.server.credential_binding?.headers || {});
  configForm.timeoutMs = nextDetail.server.endpoint_config?.timeout_ms || 20000;
  resetToolStates();
  nextDetail.tools.forEach((tool) => applyToolState(tool));
  expandedToolIds.value = new Set(nextDetail.tools.length > 0 ? [nextDetail.tools[0].id] : []);
}

async function loadDetail() {
  if (!props.open || !props.server?.server_key) return;
  loading.value = true;
  try {
    const nextDetail = await getApiMonitorMcpDetail(props.server.server_key);
    applyDetail(nextDetail);
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to load API Monitor MCP detail'));
  } finally {
    loading.value = false;
  }
}

function parseJsonObject(text: string, fieldLabel: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(fieldLabel);
    }
    return parsed as Record<string, unknown>;
  } catch {
    throw new Error(t('JSON field invalid', { field: fieldLabel }));
  }
}

function updateToolField(toolId: string, field: 'name' | 'description', value: string) {
  const state = toolStates[toolId];
  if (!state) return;
  state[field] = value;
  state.yamlDefinition = syncYamlTopLevelField(state.yamlDefinition, field, value);
}

function updateToolYaml(toolId: string, value: string) {
  const state = toolStates[toolId];
  if (!state) return;
  state.yamlDefinition = value;
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

async function saveSharedConfig() {
  if (!detail.value?.server.server_key) return;
  savingConfig.value = true;
  try {
    const result = await updateApiMonitorMcpConfig(detail.value.server.server_key, {
      endpoint_config: {
        headers: parseJsonObject(configForm.headersText, t('Shared headers JSON')),
        timeout_ms: configForm.timeoutMs,
      },
      credential_binding: {
        query: parseJsonObject(configForm.queryText, t('Shared query JSON')),
        headers: parseJsonObject(configForm.credentialHeadersText, t('Credential headers JSON')),
      },
    });
    detail.value.server = result.server;
    emit('server-updated', result.server);
    showSuccessToast(t('API Monitor shared config saved'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to save API Monitor config'));
  } finally {
    savingConfig.value = false;
  }
}

async function saveTool(toolId: string) {
  if (!detail.value?.server.server_key) return;
  const state = toolStates[toolId];
  if (!state) return;
  state.saving = true;
  try {
    const updated = await updateApiMonitorMcpTool(detail.value.server.server_key, toolId, {
      yaml_definition: state.yamlDefinition,
    });
    const index = detail.value.tools.findIndex((tool) => tool.id === toolId);
    if (index >= 0) {
      detail.value.tools[index] = updated;
      applyToolState(updated);
      toolStates[toolId].testResult = state.testResult;
    }
    showSuccessToast(t('API Monitor tool saved'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to save API Monitor tool'));
  } finally {
    state.saving = false;
  }
}

async function testTool(toolId: string) {
  if (!detail.value?.server.server_key) return;
  const state = toolStates[toolId];
  const tool = detail.value.tools.find((item) => item.id === toolId);
  if (!state || !tool) return;
  state.testing = true;
  try {
    const result = await testApiMonitorMcpTool(detail.value.server.server_key, toolId, {
      arguments: buildSampleArguments(tool.input_schema as Record<string, unknown>) as Record<string, unknown>,
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
    detail.value = null;
    expandedToolIds.value = new Set();
    resetToolStates();
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
