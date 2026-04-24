<template>
  <Dialog :open="open" @update:open="handleOpenChange">
    <DialogContent class="w-[calc(100vw-16px)] max-w-3xl max-h-[94vh] overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] p-0 shadow-2xl dark:border-white/10 dark:bg-[#101115]">
      <DialogHeader class="border-b border-slate-200 bg-white/80 px-6 py-5 dark:border-white/10 dark:bg-white/[0.05]">
        <DialogTitle class="flex items-center gap-3 text-xl font-black text-[var(--text-primary)]">
          <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-teal-100 text-teal-700 dark:bg-teal-400/15 dark:text-teal-200">
            <Pencil :size="20" />
          </div>
          {{ t('Edit API Monitor MCP') }}
        </DialogTitle>
        <DialogDescription class="mt-1 text-sm text-[var(--text-tertiary)]">
          {{ t('Edit the name, description, and authentication for this API Monitor MCP.') }}
        </DialogDescription>
      </DialogHeader>

      <div class="max-h-[calc(94vh-88px)] overflow-y-auto p-6 space-y-6">
        <!-- Basic info -->
        <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
          <h3 class="mb-4 text-sm font-black uppercase tracking-[0.1em] text-teal-600 dark:text-teal-300">{{ t('Basic info') }}</h3>
          <div class="grid gap-4 lg:grid-cols-2">
            <label class="field">
              <span>{{ t('Name') }}</span>
              <input v-model="form.name" class="tools-input" />
            </label>
            <label class="field">
              <span>{{ t('Description') }}</span>
              <input v-model="form.description" class="tools-input" />
            </label>
          </div>
        </section>

        <!-- Authentication -->
        <section class="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
          <div class="mb-4 flex items-center gap-3">
            <ShieldCheck :size="18" class="text-violet-600 dark:text-violet-300" />
            <div>
              <h3 class="text-sm font-black uppercase tracking-[0.1em] text-violet-600 dark:text-violet-300">{{ t('Authentication & Headers') }}</h3>
              <p class="mt-1 text-xs text-[var(--text-tertiary)]">{{ t('HTTP headers credential hint') }}</p>
            </div>
          </div>

          <label class="field mb-4">
            <span>{{ t('HTTP Headers') }}</span>
            <textarea
              v-model="form.headersText"
              rows="5"
              class="tools-input resize-y font-mono"
              :placeholder="t('HTTP headers with credentials placeholder')"
              spellcheck="false"
            ></textarea>
          </label>

          <!-- Credential bindings -->
          <div class="mb-4">
            <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h5 class="text-sm font-black text-[var(--text-primary)]">{{ t('Credential Bindings') }}</h5>
                <p class="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">{{ t('Credential bindings hint') }}</p>
              </div>
              <button class="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10" @click="addCredentialBinding">
                <Plus :size="14" />
                {{ t('Add credential binding') }}
              </button>
            </div>

            <div class="mt-4 space-y-3">
              <div
                v-for="(binding, index) in form.credentialBindings"
                :key="index"
                class="grid grid-cols-1 gap-3 rounded-2xl border border-slate-200 bg-white p-3 dark:border-white/10 dark:bg-[#101115] md:grid-cols-[1fr_1.4fr_auto]"
              >
                <label class="field">
                  <span>{{ t('Alias') }}</span>
                  <input v-model="binding.alias" class="tools-input" :placeholder="t('credential')" />
                </label>
                <label class="field">
                  <span>{{ t('Credential') }}</span>
                  <select v-model="binding.credentialId" class="tools-input">
                    <option value="">{{ t('No credential') }}</option>
                    <option v-for="credential in credentials" :key="credential.id" :value="credential.id">
                      {{ credential.name }} ({{ credential.username || credential.domain || credential.id }})
                    </option>
                  </select>
                </label>
                <button class="self-end rounded-xl border border-red-200 px-3 py-2 text-xs font-bold text-red-600 transition hover:bg-red-50 dark:border-red-400/20 dark:text-red-300 dark:hover:bg-red-500/10" @click="removeCredentialBinding(index)">
                  {{ t('Remove') }}
                </button>
              </div>
            </div>
          </div>

          <label class="field mb-4">
            <span>{{ t('Query Parameters') }}</span>
            <textarea
              v-model="form.queryText"
              rows="3"
              class="tools-input resize-y font-mono"
              :placeholder="t('Query params with credentials placeholder')"
              spellcheck="false"
            ></textarea>
            <small>{{ t('Query params are appended to the MCP endpoint URL at runtime. Prefer headers for authentication when possible.') }}</small>
          </label>

          <label class="field">
            <span>{{ t('Timeout (ms)') }}</span>
            <input v-model.number="form.timeoutMs" type="number" min="1" class="tools-input font-mono" />
          </label>
        </section>
      </div>

      <!-- Footer -->
      <div class="flex justify-end gap-3 border-t border-slate-200 bg-white px-6 py-4 dark:border-white/10 dark:bg-white/[0.055]">
        <button
          class="rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/10"
          @click="handleOpenChange(false)"
        >
          {{ t('Cancel') }}
        </button>
        <button
          class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-[#0f8f88] to-[#0b6ee6] px-5 py-2 text-sm font-bold text-white shadow-lg transition hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-50 disabled:cursor-not-allowed"
          :disabled="saving"
          @click="save"
        >
          <Loader2 v-if="saving" class="animate-spin" :size="16" />
          {{ saving ? t('Saving...') : t('Save') }}
        </button>
      </div>
    </DialogContent>
  </Dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { Loader2, Pencil, Plus, ShieldCheck } from 'lucide-vue-next';
import { listCredentials, type Credential } from '@/api/credential';
import { updateApiMonitorMcpConfig, type McpServerItem } from '@/api/mcp';
import {
  splitCredentialTemplateMap,
  parseHttpHeaderText,
  parseKeyValueTemplateText,
  stringifyHttpHeaders,
  stringifyKeyValueTemplateMap,
} from '@/utils/mcpUi';
import { showErrorToast, showSuccessToast } from '@/utils/toast';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

const props = defineProps<{
  open: boolean;
  server: McpServerItem | null;
}>();

const emit = defineEmits<{
  (event: 'update:open', value: boolean): void;
  (event: 'server-updated', server: McpServerItem): void;
}>();

const { t } = useI18n();
const saving = ref(false);
const credentials = ref<Credential[]>([]);

const form = reactive({
  name: '',
  description: '',
  headersText: '',
  credentialBindings: [{ alias: 'credential', credentialId: '' }] as { alias: string; credentialId: string }[],
  queryText: '',
  timeoutMs: 20000,
});

function populateFromServer(server: McpServerItem) {
  form.name = server.name || '';
  form.description = server.description || '';

  const endpointConfig = isPlainObject(server.endpoint_config) ? server.endpoint_config : {};
  const binding = server.credential_binding || { credential_id: '', credentials: [], headers: {}, env: {}, query: {} };

  // Merge static headers (endpoint_config.headers) and credential headers (binding.headers)
  const allHeaders: Record<string, string> = {};
  if (isPlainObject(endpointConfig.headers)) {
    Object.assign(allHeaders, endpointConfig.headers as Record<string, string>);
  }
  if (isPlainObject(binding.headers)) {
    Object.assign(allHeaders, binding.headers as Record<string, string>);
  }
  form.headersText = stringifyHttpHeaders(allHeaders);

  // Credential bindings
  const bindings = (binding.credentials || [])
    .map((item: { alias?: string; credential_id?: string }) => ({ alias: item.alias || '', credentialId: item.credential_id || '' }))
    .filter((item: { alias: string; credentialId: string }) => item.alias || item.credentialId);
  if (bindings.length === 0 && binding.credential_id) {
    bindings.push({ alias: 'credential', credentialId: binding.credential_id });
  }
  form.credentialBindings = bindings.length > 0 ? bindings : [{ alias: 'credential', credentialId: '' }];

  form.queryText = stringifyKeyValueTemplateMap(isPlainObject(binding.query) ? binding.query as Record<string, string> : {});
  form.timeoutMs = (endpointConfig.timeout_ms as number) || 20000;
}

function handleOpenChange(value: boolean) {
  emit('update:open', value);
}

function addCredentialBinding() {
  form.credentialBindings.push({ alias: '', credentialId: '' });
}

function removeCredentialBinding(index: number) {
  form.credentialBindings.splice(index, 1);
  if (form.credentialBindings.length === 0) {
    form.credentialBindings.push({ alias: 'credential', credentialId: '' });
  }
}

async function save() {
  if (!props.server?.server_key) return;
  saving.value = true;
  try {
    const headerSplit = splitCredentialTemplateMap(parseHttpHeaderText(form.headersText));
    const result = await updateApiMonitorMcpConfig(props.server.server_key, {
      name: form.name,
      description: form.description,
      endpoint_config: {
        headers: headerSplit.staticValues,
        timeout_ms: form.timeoutMs,
      },
      credential_binding: {
        credential_id: '',
        credentials: form.credentialBindings
          .map((item) => ({ alias: item.alias.trim(), credential_id: item.credentialId.trim() }))
          .filter((item) => item.alias && item.credential_id),
        headers: headerSplit.credentialValues,
        env: {},
        query: parseKeyValueTemplateText(form.queryText),
      },
    });
    emit('server-updated', result.server);
    showSuccessToast(t('API Monitor MCP updated'));
    handleOpenChange(false);
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to save API Monitor config'));
  } finally {
    saving.value = false;
  }
}

watch(
  () => [props.open, props.server?.server_key],
  async ([open]) => {
    if (open && props.server) {
      populateFromServer(props.server);
      try {
        credentials.value = await listCredentials();
      } catch {
        credentials.value = [];
      }
    }
  },
  { immediate: true },
);
</script>

<style scoped>
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

.dark .tools-input {
  border-color: rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.05);
}
</style>
