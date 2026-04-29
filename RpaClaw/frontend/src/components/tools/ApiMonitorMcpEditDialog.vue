<template>
  <Dialog :open="open" @update:open="handleOpenChange">
    <DialogContent class="flex w-[calc(100vw-16px)] max-w-3xl flex-col max-h-[94vh] overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] p-0 shadow-2xl dark:border-white/10 dark:bg-[#101115]">
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

      <div class="flex-1 overflow-y-auto min-h-0 p-6 space-y-6">
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
              <h3 class="text-sm font-black uppercase tracking-[0.1em] text-violet-600 dark:text-violet-300">{{ t('API Monitor Authentication') }}</h3>
              <p class="mt-1 text-xs text-[var(--text-tertiary)]">{{ t('API Monitor credential auth hint') }}</p>
            </div>
          </div>

          <div class="grid gap-4 lg:grid-cols-2">
            <label class="field">
              <span>{{ t('Credential Type') }}</span>
              <select v-model="form.credentialType" class="tools-input">
                <option v-for="option in API_MONITOR_CREDENTIAL_TYPE_OPTIONS" :key="option.value" :value="option.value">
                  {{ t(option.labelKey) }}
                </option>
              </select>
              <small>{{ t('API Monitor Placeholder credential type hint') }}</small>
            </label>

            <label class="field">
              <span>{{ t('Credential') }}</span>
              <select v-model="form.credentialId" class="tools-input">
                <option value="">{{ t('No credential') }}</option>
                <option v-for="credential in credentials" :key="credential.id" :value="credential.id">
                  {{ credential.name }} ({{ credential.username || credential.domain || credential.id }})
                </option>
              </select>
              <small v-if="credentials.length === 0">{{ t('No credentials available') }}</small>
            </label>
          </div>

          <label v-if="form.credentialType === 'test'" class="field mt-4">
            <span>{{ t('Login URL') }}</span>
            <input v-model="form.loginUrl" class="tools-input font-mono" :placeholder="t('Login URL placeholder')" />
          </label>

          <label class="field mt-4">
            <span>{{ t('Timeout (ms)') }}</span>
            <input v-model.number="form.timeoutMs" type="number" min="1" class="tools-input font-mono" />
          </label>

          <label class="field mt-4">
            <span>{{ t('Token flows JSON') }}</span>
            <textarea v-model="tokenFlowsJson" class="tools-input min-h-56 font-mono text-xs" />
            <small>{{ t('Edit saved token flows JSON hint') }}</small>
            <small v-if="tokenFlowsJsonError" class="text-red-500">{{ tokenFlowsJsonError }}</small>
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
import { Loader2, Pencil, ShieldCheck } from 'lucide-vue-next';
import { listCredentials, type Credential } from '@/api/credential';
import { updateApiMonitorMcpConfig, type McpServerItem } from '@/api/mcp';
import { API_MONITOR_CREDENTIAL_TYPE_OPTIONS, normalizeApiMonitorAuth } from '@/utils/apiMonitorAuth';
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
const tokenFlowsJson = ref('[]');
const tokenFlowsJsonError = ref('');

const form = reactive({
  name: '',
  description: '',
  credentialType: 'placeholder' as 'placeholder' | 'test',
  credentialId: '',
  loginUrl: '',
  timeoutMs: 20000,
});

function populateFromServer(server: McpServerItem) {
  form.name = server.name || '';
  form.description = server.description || '';
  const endpointConfig = isPlainObject(server.endpoint_config) ? server.endpoint_config : {};
  const auth = normalizeApiMonitorAuth(server.api_monitor_auth);
  form.credentialType = auth.credential_type;
  form.credentialId = auth.credential_id;
  form.loginUrl = auth.login_url || '';
  form.timeoutMs = (endpointConfig.timeout_ms as number) || 20000;
  tokenFlowsJson.value = JSON.stringify(server.api_monitor_auth?.token_flows || [], null, 2);
  tokenFlowsJsonError.value = '';
}

function handleOpenChange(value: boolean) {
  emit('update:open', value);
}

async function save() {
  if (!props.server?.server_key) return;
  saving.value = true;
  try {
    tokenFlowsJsonError.value = '';
    let tokenFlows: unknown = [];
    try {
      tokenFlows = tokenFlowsJson.value.trim() ? JSON.parse(tokenFlowsJson.value) : [];
    } catch (error) {
      tokenFlowsJsonError.value = error instanceof Error ? error.message : t('Invalid JSON');
      return;
    }
    if (!Array.isArray(tokenFlows)) {
      tokenFlowsJsonError.value = t('Token flows JSON must be an array');
      return;
    }
    const result = await updateApiMonitorMcpConfig(props.server.server_key, {
      name: form.name,
      description: form.description,
      endpoint_config: {
        timeout_ms: form.timeoutMs,
      },
      api_monitor_auth: {
        credential_type: form.credentialType,
        credential_id: form.credentialId,
        ...(form.credentialType === 'test' && form.loginUrl ? { login_url: form.loginUrl } : {}),
        token_flows: tokenFlows as any,
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
