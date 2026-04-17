<template>
  <div class="flex flex-col h-full w-full overflow-hidden">
    <div class="flex-shrink-0 relative overflow-hidden">
      <div class="absolute inset-0 bg-gradient-to-br from-blue-600 via-indigo-600 to-sky-700"></div>
      <div class="relative px-6 py-5">
        <div class="flex items-center justify-between gap-4">
          <div>
            <h1 class="text-xl font-bold text-white">Tools Library</h1>
            <p class="text-white/60 text-xs mt-1">
              {{ activeTab === 'external' ? `${externalTools.length} external tools` : `${mcpServers.length} MCP servers` }}
            </p>
          </div>
          <div class="flex items-center gap-3">
            <div class="rounded-xl bg-white/10 p-1 backdrop-blur-sm border border-white/10 flex items-center gap-1">
              <button class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors" :class="activeTab === 'external' ? 'bg-white text-slate-900' : 'text-white/80'" @click="activeTab = 'external'">External</button>
              <button class="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors" :class="activeTab === 'mcp' ? 'bg-white text-slate-900' : 'text-white/80'" @click="activeTab = 'mcp'">MCP</button>
            </div>
            <button v-if="activeTab === 'mcp'" @click="openCreateDialog" class="px-3 py-2 rounded-lg bg-white text-slate-900 text-sm font-medium">Add MCP</button>
            <div class="relative">
              <Search class="absolute left-3 top-1/2 -translate-y-1/2 text-white/40 size-4" />
              <input v-model="searchQuery" type="text" :placeholder="activeTab === 'external' ? 'Search tools...' : 'Search MCP servers...'" class="w-64 bg-white/10 border border-white/10 rounded-lg pl-9 pr-3 py-2 text-sm text-white placeholder-white/30 focus:outline-none">
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="flex-1 overflow-y-auto p-5 bg-[#f8f9fb] dark:bg-[#111]">
      <div v-if="activeTab === 'external'" class="max-w-[1400px] mx-auto">
        <div v-if="externalTools.length === 0 && !extLoading" class="rounded-2xl border border-dashed border-gray-300 dark:border-gray-700 bg-white dark:bg-[#1e1e1e] p-10 text-center text-sm text-[var(--text-tertiary)]">
          No external tools installed
        </div>
        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <article v-for="tool in filteredExtTools" :key="tool.name" class="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-[#1e1e1e] p-4">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0 cursor-pointer" @click="router.push(`/chat/tools/${tool.name}`)">
                <h3 class="text-sm font-semibold text-[var(--text-primary)] truncate">{{ tool.name }}</h3>
                <p class="text-xs text-[var(--text-tertiary)] mt-1">{{ tool.file }}</p>
                <p class="text-xs text-[var(--text-secondary)] mt-3 line-clamp-2">{{ tool.description || 'No description' }}</p>
              </div>
              <div class="flex items-center gap-1">
                <button @click="handleToggleBlock(tool)" class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800">
                  <EyeOff v-if="tool.blocked" :size="14" />
                  <Eye v-else :size="14" />
                </button>
                <button @click="deleteExternalTool(tool)" class="p-2 rounded-lg hover:bg-red-50 text-red-500 dark:hover:bg-red-900/20">
                  <Trash2 :size="14" />
                </button>
              </div>
            </div>
          </article>
        </div>
      </div>

      <div v-else class="max-w-[1400px] mx-auto space-y-8">
        <section>
          <div class="flex items-center justify-between mb-3">
            <div>
              <h2 class="text-sm font-semibold text-[var(--text-primary)]">Platform MCP</h2>
              <p class="text-xs text-[var(--text-tertiary)] mt-1">Read-only servers loaded from deployment config.</p>
            </div>
            <span class="text-xs text-[var(--text-tertiary)]">{{ groupedMcpServers.system.length }}</span>
          </div>
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <article v-for="server in groupedMcpServers.system" :key="server.server_key" class="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-[#1e1e1e] p-5">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <h3 class="text-sm font-semibold text-[var(--text-primary)] truncate">{{ server.name }}</h3>
                    <span class="text-[10px] px-2 py-0.5 rounded-full bg-sky-50 text-sky-600 dark:bg-sky-900/20 dark:text-sky-300">Platform</span>
                    <span class="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{{ server.transport }}</span>
                  </div>
                  <p class="text-xs text-[var(--text-secondary)] mt-2 line-clamp-2">{{ server.description || 'No description' }}</p>
                  <p class="text-xs text-[var(--text-tertiary)] mt-3">{{ formatServerEndpoint(server) }}</p>
                </div>
                <span class="text-[10px] px-2 py-0.5 rounded-full" :class="server.default_enabled ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/20 dark:text-emerald-300' : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'">
                  {{ server.default_enabled ? 'Default on' : 'Default off' }}
                </span>
              </div>
              <div class="mt-4 flex items-center gap-2">
                <button class="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-xs" @click="runTest(server)">Test</button>
                <button class="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-xs" @click="openToolsDialog(server)">Tools</button>
              </div>
            </article>
          </div>
        </section>

        <section>
          <div class="flex items-center justify-between mb-3">
            <div>
              <h2 class="text-sm font-semibold text-[var(--text-primary)]">My MCP</h2>
              <p class="text-xs text-[var(--text-tertiary)] mt-1">Private MCP servers for your account.</p>
            </div>
            <span class="text-xs text-[var(--text-tertiary)]">{{ groupedMcpServers.user.length }}</span>
          </div>
          <div v-if="groupedMcpServers.user.length === 0" class="rounded-2xl border border-dashed border-gray-300 dark:border-gray-700 bg-white dark:bg-[#1e1e1e] p-6 text-sm text-[var(--text-tertiary)]">
            No private MCP servers yet.
          </div>
          <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <article v-for="server in groupedMcpServers.user" :key="server.server_key" class="rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-[#1e1e1e] p-5">
              <div class="flex items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <h3 class="text-sm font-semibold text-[var(--text-primary)] truncate">{{ server.name }}</h3>
                    <span class="text-[10px] px-2 py-0.5 rounded-full bg-violet-50 text-violet-600 dark:bg-violet-900/20 dark:text-violet-300">Private</span>
                    <span class="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300">{{ server.transport }}</span>
                  </div>
                  <p class="text-xs text-[var(--text-secondary)] mt-2 line-clamp-2">{{ server.description || 'No description' }}</p>
                  <p class="text-xs text-[var(--text-tertiary)] mt-3">{{ formatServerEndpoint(server) }}</p>
                </div>
                <span class="text-[10px] px-2 py-0.5 rounded-full" :class="server.enabled ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-900/20 dark:text-emerald-300' : 'bg-amber-50 text-amber-600 dark:bg-amber-900/20 dark:text-amber-300'">
                  {{ server.enabled ? 'Enabled' : 'Disabled' }}
                </span>
              </div>
              <div class="mt-4 flex items-center flex-wrap gap-2">
                <button class="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-xs" @click="openEditDialog(server)">Edit</button>
                <button class="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-xs" @click="runTest(server)">Test</button>
                <button class="px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 text-xs" @click="openToolsDialog(server)">Tools</button>
                <button class="px-3 py-1.5 rounded-lg border border-red-200 text-red-600 dark:border-red-800 dark:text-red-300 text-xs" @click="deletePrivateServer(server)">Delete</button>
              </div>
            </article>
          </div>
        </section>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="formOpen" class="fixed inset-0 z-[9999] flex items-center justify-center px-4">
        <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="closeFormDialog"></div>
        <div class="relative z-10 w-full max-w-2xl rounded-2xl bg-white dark:bg-[#1f1f1f] shadow-2xl border border-gray-200 dark:border-gray-800">
          <div class="p-6 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-4">
            <div>
              <h3 class="text-lg font-semibold text-[var(--text-primary)]">{{ editingServer ? 'Edit MCP server' : 'Add MCP server' }}</h3>
              <p class="text-sm text-[var(--text-tertiary)] mt-1">Private MCP only. `stdio` works only in local mode.</p>
            </div>
            <button class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800" @click="closeFormDialog"><X :size="16" /></button>
          </div>
          <div class="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
            <label class="flex flex-col gap-2">
              <span class="text-xs font-medium text-[var(--text-secondary)]">Name</span>
              <input v-model="form.name" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm" />
            </label>
            <label class="flex flex-col gap-2">
              <span class="text-xs font-medium text-[var(--text-secondary)]">Transport</span>
              <select v-model="form.transport" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm">
                <option value="streamable_http">Streamable HTTP</option>
                <option value="sse">SSE</option>
                <option value="stdio">stdio</option>
              </select>
            </label>
            <label class="md:col-span-2 flex flex-col gap-2">
              <span class="text-xs font-medium text-[var(--text-secondary)]">Description</span>
              <textarea v-model="form.description" rows="3" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm resize-none"></textarea>
            </label>
            <template v-if="form.transport !== 'stdio'">
              <label class="md:col-span-2 flex flex-col gap-2">
                <span class="text-xs font-medium text-[var(--text-secondary)]">Endpoint URL</span>
                <input v-model="form.url" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm" />
              </label>
              <label class="md:col-span-2 flex flex-col gap-2">
                <span class="text-xs font-medium text-[var(--text-secondary)]">HTTP Headers</span>
                <textarea
                  v-model="form.headersText"
                  rows="4"
                  class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm resize-none font-mono"
                  placeholder="Authorization: Bearer {{ api_key }}&#10;X-Api-Key: ..."
                ></textarea>
                <span class="text-[11px] text-[var(--text-tertiary)]">One header per line. Values may include credential placeholders.</span>
              </label>
            </template>
            <template v-else>
              <label class="flex flex-col gap-2">
                <span class="text-xs font-medium text-[var(--text-secondary)]">Command</span>
                <input v-model="form.command" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm" />
              </label>
              <label class="flex flex-col gap-2">
                <span class="text-xs font-medium text-[var(--text-secondary)]">Working Directory</span>
                <input v-model="form.cwd" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm" />
              </label>
              <label class="md:col-span-2 flex flex-col gap-2">
                <span class="text-xs font-medium text-[var(--text-secondary)]">Arguments</span>
                <textarea v-model="form.argsText" rows="3" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm resize-none" placeholder="One argument per line"></textarea>
              </label>
            </template>
            <label class="flex flex-col gap-2">
              <span class="text-xs font-medium text-[var(--text-secondary)]">Timeout (ms)</span>
              <input v-model.number="form.timeoutMs" type="number" min="1" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm" />
            </label>
            <label class="flex flex-col gap-2">
              <span class="text-xs font-medium text-[var(--text-secondary)]">Credential</span>
              <select v-model="form.credentialId" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#141414] px-3 py-2 text-sm">
                <option value="">No credential</option>
                <option v-for="credential in credentials" :key="credential.id" :value="credential.id">{{ credential.name }} ({{ credential.username }})</option>
              </select>
            </label>
            <label class="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <input v-model="form.enabled" type="checkbox" class="rounded border-gray-300" />
              Enabled
            </label>
            <label class="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
              <input v-model="form.defaultEnabled" type="checkbox" class="rounded border-gray-300" />
              Default enabled for new sessions
            </label>
          </div>
          <div class="p-6 border-t border-gray-100 dark:border-gray-800 flex justify-end gap-2">
            <button class="px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-700 text-sm" @click="closeFormDialog">Cancel</button>
            <button class="px-4 py-2 rounded-xl bg-blue-600 text-white text-sm disabled:opacity-50" :disabled="savingForm" @click="submitForm">
              {{ savingForm ? 'Saving...' : editingServer ? 'Save changes' : 'Create MCP' }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="toolsDialogOpen" class="fixed inset-0 z-[9999] flex items-center justify-center px-4">
        <div class="absolute inset-0 bg-black/50 backdrop-blur-sm" @click="closeToolsDialog"></div>
        <div class="relative z-10 w-full max-w-3xl rounded-2xl bg-white dark:bg-[#1f1f1f] shadow-2xl border border-gray-200 dark:border-gray-800">
          <div class="p-6 border-b border-gray-100 dark:border-gray-800 flex items-center justify-between gap-4">
            <div>
              <h3 class="text-lg font-semibold text-[var(--text-primary)]">{{ selectedServer?.name || 'MCP tools' }}</h3>
              <p class="text-sm text-[var(--text-tertiary)] mt-1">{{ discoveredTools.length }} discovered tools</p>
            </div>
            <button class="p-2 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800" @click="closeToolsDialog"><X :size="16" /></button>
          </div>
          <div class="p-6 max-h-[70vh] overflow-y-auto space-y-3">
            <div v-if="discoveredTools.length === 0" class="text-sm text-[var(--text-tertiary)]">No tools returned by this MCP server.</div>
            <div v-for="tool in discoveredTools" :key="tool.name" class="rounded-xl border border-gray-200 dark:border-gray-800 p-4 bg-gray-50/80 dark:bg-[#141414]">
              <h4 class="text-sm font-semibold text-[var(--text-primary)]">{{ tool.name }}</h4>
              <p class="text-xs text-[var(--text-secondary)] mt-1">{{ tool.description || 'No description' }}</p>
              <pre class="mt-3 rounded-lg bg-white dark:bg-[#1f1f1f] border border-gray-200 dark:border-gray-800 p-3 text-xs text-[var(--text-secondary)] overflow-x-auto"><code>{{ JSON.stringify(tool.input_schema || {}, null, 2) }}</code></pre>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Search, Eye, EyeOff, Trash2, X } from 'lucide-vue-next';
import { useRouter } from 'vue-router';
import { getTools, blockTool, deleteTool as apiDeleteTool } from '../api/agent';
import type { ExternalToolItem } from '../types/response';
import { listCredentials, type Credential } from '../api/credential';
import {
  createMcpServer,
  deleteMcpServer,
  discoverMcpTools,
  listMcpServers,
  testMcpServer,
  updateMcpServer,
  type McpServerItem,
  type McpToolDiscoveryItem,
} from '../api/mcp';
import { showErrorToast, showSuccessToast } from '../utils/toast';
import { groupMcpServers, parseHttpHeaderText, stringifyHttpHeaders } from '../utils/mcpUi';

const router = useRouter();
const activeTab = ref<'external' | 'mcp'>('external');
const searchQuery = ref('');

const externalTools = ref<ExternalToolItem[]>([]);
const extLoading = ref(false);

const mcpServers = ref<McpServerItem[]>([]);
const credentials = ref<Credential[]>([]);
const formOpen = ref(false);
const editingServer = ref<McpServerItem | null>(null);
const savingForm = ref(false);
const toolsDialogOpen = ref(false);
const selectedServer = ref<McpServerItem | null>(null);
const discoveredTools = ref<McpToolDiscoveryItem[]>([]);

const form = reactive({
  name: '',
  description: '',
  transport: 'streamable_http' as 'stdio' | 'streamable_http' | 'sse',
  enabled: true,
  defaultEnabled: false,
  url: '',
  headersText: '',
  command: '',
  cwd: '',
  argsText: '',
  timeoutMs: 20000,
  credentialId: '',
});

const filteredExtTools = computed(() => {
  if (!searchQuery.value) return externalTools.value;
  return externalTools.value.filter((tool) => tool.name.toLowerCase().includes(searchQuery.value.toLowerCase()));
});

const filteredMcpServers = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return mcpServers.value;
  return mcpServers.value.filter((server) =>
    [server.name, server.description, server.transport, server.scope, server.server_key]
      .some((value) => value?.toLowerCase().includes(query)),
  );
});

const groupedMcpServers = computed(() => groupMcpServers(filteredMcpServers.value));

const resetForm = () => {
  form.name = '';
  form.description = '';
  form.transport = 'streamable_http';
  form.enabled = true;
  form.defaultEnabled = false;
  form.url = '';
  form.headersText = '';
  form.command = '';
  form.cwd = '';
  form.argsText = '';
  form.timeoutMs = 20000;
  form.credentialId = '';
};

const applyServerToForm = (server: McpServerItem) => {
  const endpoint = server.endpoint_config || {};
  form.name = server.name;
  form.description = server.description || '';
  form.transport = server.transport;
  form.enabled = server.enabled;
  form.defaultEnabled = server.default_enabled;
  form.url = endpoint.url || '';
  form.headersText = stringifyHttpHeaders(endpoint.headers);
  form.command = endpoint.command || '';
  form.cwd = endpoint.cwd || '';
  form.argsText = (endpoint.args || []).join('\n');
  form.timeoutMs = endpoint.timeout_ms || 20000;
  form.credentialId = server.credential_binding?.credential_id || '';
};

const loadData = async () => {
  extLoading.value = true;
  try {
    const [tools, servers, creds] = await Promise.all([
      getTools(),
      listMcpServers(),
      listCredentials().catch(() => []),
    ]);
    externalTools.value = tools;
    mcpServers.value = servers;
    credentials.value = creds;
  } catch (error) {
    console.error(error);
    showErrorToast('Failed to load tools');
  } finally {
    extLoading.value = false;
  }
};

onMounted(loadData);

const formatServerEndpoint = (server: McpServerItem) => {
  if (server.transport === 'stdio') return server.endpoint_config.command || 'stdio';
  return server.endpoint_config.url || 'No endpoint';
};

const handleToggleBlock = async (tool: ExternalToolItem) => {
  try {
    await blockTool(tool.name, !tool.blocked);
    tool.blocked = !tool.blocked;
  } catch (error) {
    console.error(error);
    showErrorToast('Failed to update tool visibility');
  }
};

const deleteExternalTool = async (tool: ExternalToolItem) => {
  if (!window.confirm(`Delete "${tool.name}"?`)) return;
  try {
    await apiDeleteTool(tool.name);
    externalTools.value = externalTools.value.filter((item) => item.name !== tool.name);
    showSuccessToast('External tool deleted');
  } catch (error) {
    console.error(error);
    showErrorToast('Failed to delete external tool');
  }
};

const openCreateDialog = () => {
  editingServer.value = null;
  resetForm();
  formOpen.value = true;
};

const openEditDialog = (server: McpServerItem) => {
  editingServer.value = server;
  applyServerToForm(server);
  formOpen.value = true;
};

const closeFormDialog = () => {
  formOpen.value = false;
  editingServer.value = null;
  resetForm();
};

const buildPayload = () => ({
  name: form.name.trim(),
  description: form.description.trim(),
  transport: form.transport,
  enabled: form.enabled,
  default_enabled: form.defaultEnabled,
  endpoint_config: form.transport === 'stdio'
    ? {
        command: form.command.trim(),
        cwd: form.cwd.trim(),
        args: form.argsText.split('\n').map((value) => value.trim()).filter(Boolean),
        timeout_ms: form.timeoutMs,
      }
    : {
        url: form.url.trim(),
        headers: parseHttpHeaderText(form.headersText),
        timeout_ms: form.timeoutMs,
      },
  credential_binding: {
    credential_id: form.credentialId,
    headers: {},
    env: {},
    query: {},
  },
  tool_policy: {
    allowed_tools: [],
    blocked_tools: [],
  },
});

const submitForm = async () => {
  if (!form.name.trim()) {
    showErrorToast('MCP server name is required');
    return;
  }
  if (form.transport === 'stdio' && !form.command.trim()) {
    showErrorToast('Command is required for stdio MCP');
    return;
  }
  if (form.transport !== 'stdio' && !form.url.trim()) {
    showErrorToast('Endpoint URL is required');
    return;
  }

  savingForm.value = true;
  try {
    const payload = buildPayload();
    if (editingServer.value) {
      await updateMcpServer(editingServer.value.id, payload);
      showSuccessToast('MCP server updated');
    } else {
      await createMcpServer(payload);
      showSuccessToast('MCP server created');
    }
    closeFormDialog();
    mcpServers.value = await listMcpServers();
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || 'Failed to save MCP server');
  } finally {
    savingForm.value = false;
  }
};

const runTest = async (server: McpServerItem) => {
  try {
    const result = await testMcpServer(server.server_key);
    showSuccessToast(`${server.name}: ${result.tool_count} tools reachable`);
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || `Failed to test ${server.name}`);
  }
};

const openToolsDialog = async (server: McpServerItem) => {
  try {
    const result = await discoverMcpTools(server.server_key);
    selectedServer.value = server;
    discoveredTools.value = result.tools;
    toolsDialogOpen.value = true;
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || `Failed to discover tools for ${server.name}`);
  }
};

const closeToolsDialog = () => {
  toolsDialogOpen.value = false;
  selectedServer.value = null;
  discoveredTools.value = [];
};

const deletePrivateServer = async (server: McpServerItem) => {
  if (!window.confirm(`Delete "${server.name}"?`)) return;
  try {
    await deleteMcpServer(server.id);
    mcpServers.value = await listMcpServers();
    showSuccessToast('MCP server deleted');
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || 'Failed to delete MCP server');
  }
};
</script>
