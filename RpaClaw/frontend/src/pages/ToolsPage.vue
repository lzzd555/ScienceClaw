<template>
  <div class="tools-library flex h-full w-full flex-col overflow-hidden bg-[#f5f7fb] text-[var(--text-primary)] dark:bg-[#101115]">
    <header class="relative flex-shrink-0 overflow-hidden">
      <div class="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.22),transparent_32%),linear-gradient(115deg,#7838c8_0%,#1857d2_52%,#0f8db8_100%)]"></div>
      <div class="absolute -right-16 -top-20 h-52 w-52 rounded-full bg-white/10 blur-3xl"></div>
      <div class="relative px-5 py-5 sm:px-7">
        <div class="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
          <div class="flex items-center gap-3">
            <div class="flex h-10 w-10 items-center justify-center rounded-2xl border border-white/20 bg-white/15 text-white shadow-lg backdrop-blur">
              <Wrench :size="20" />
            </div>
            <div>
              <h1 class="text-2xl font-bold tracking-tight text-white">{{ t('Tools Library') }}</h1>
              <p class="mt-1 text-sm text-white/70">{{ activeSummary }}</p>
            </div>
          </div>

          <div class="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div class="inline-flex rounded-full border border-white/15 bg-white/10 p-1 shadow-inner backdrop-blur">
              <button
                class="rounded-full px-4 py-2 text-sm font-semibold transition-all"
                :class="activeTab === 'external' ? 'bg-white text-slate-950 shadow-sm' : 'text-white/75 hover:text-white'"
                @click="activeTab = 'external'"
              >
                {{ t('Custom Code Tools') }}
              </button>
              <button
                class="rounded-full px-4 py-2 text-sm font-semibold transition-all"
                :class="activeTab === 'mcp' ? 'bg-white text-slate-950 shadow-sm' : 'text-white/75 hover:text-white'"
                @click="activeTab = 'mcp'"
              >
                {{ t('MCP Tools') }}
              </button>
            </div>
            <div class="relative w-full sm:w-[340px] xl:w-[400px]">
              <Search class="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-white/60" />
              <input
                v-model="searchQuery"
                type="text"
                :placeholder="searchPlaceholder"
                class="w-full rounded-full border border-white/20 bg-slate-950/20 py-2 pl-10 pr-4 text-sm text-white caret-white placeholder:text-white/55 shadow-[inset_0_1px_4px_rgba(0,0,0,0.16)] outline-none backdrop-blur transition focus:border-white/45 focus:bg-slate-950/25 focus:ring-2 focus:ring-white/25"
              >
            </div>
          </div>
        </div>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto px-5 py-6 sm:px-7">
      <div v-if="activeTab === 'external'" class="mx-auto max-w-[1400px]">
        <div v-if="externalTools.length === 0 && !extLoading" class="rounded-3xl border border-dashed border-slate-300 bg-white/80 p-12 text-center text-sm text-[var(--text-tertiary)] shadow-sm dark:border-white/10 dark:bg-white/[0.04]">
          <Code2 class="mx-auto mb-3 text-[var(--text-tertiary)]" :size="30" />
          {{ t('No external tools installed') }}
        </div>
        <div v-else class="grid grid-cols-1 gap-5 md:grid-cols-2 2xl:grid-cols-3">
          <article
            v-for="tool in filteredExtTools"
            :key="tool.name"
            class="group relative overflow-hidden rounded-3xl border border-slate-200/80 bg-white p-5 shadow-sm transition duration-300 hover:-translate-y-1 hover:shadow-[0_18px_42px_rgba(15,23,42,0.08)] dark:border-white/10 dark:bg-white/[0.055]"
          >
            <div class="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-[#8930b0] via-[#004be2] to-[#0f8db8] opacity-0 transition group-hover:opacity-100"></div>
            <div class="flex items-start justify-between gap-4">
              <button class="min-w-0 flex-1 text-left" @click="router.push(`/chat/tools/${tool.name}`)">
                <div class="flex items-start gap-4">
                  <div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-100 to-sky-100 text-lg font-black text-violet-700 dark:from-violet-500/20 dark:to-sky-500/20 dark:text-violet-200">
                    {{ getExternalToolInitial(tool) }}
                  </div>
                  <div class="min-w-0">
                    <h3 class="truncate text-base font-bold text-[var(--text-primary)]">{{ tool.name }}</h3>
                    <p class="mt-1 truncate text-xs text-[var(--text-tertiary)]">{{ tool.file }}</p>
                  </div>
                </div>
                <p class="mt-4 line-clamp-3 min-h-[3.75rem] text-sm leading-6 text-[var(--text-secondary)]">
                  {{ tool.description || t('No description') }}
                </p>
              </button>
              <div class="flex shrink-0 items-center gap-1 opacity-100 transition md:opacity-0 md:group-hover:opacity-100">
                <button class="rounded-xl p-2 text-[var(--text-tertiary)] transition hover:bg-slate-100 hover:text-[var(--text-primary)] dark:hover:bg-white/10" :title="tool.blocked ? t('Unblock tool') : t('Block tool')" @click="handleToggleBlock(tool)">
                  <EyeOff v-if="tool.blocked" :size="16" />
                  <Eye v-else :size="16" />
                </button>
                <button class="rounded-xl p-2 text-red-500 transition hover:bg-red-50 dark:hover:bg-red-500/10" :title="t('Delete tool')" @click="deleteExternalTool(tool)">
                  <Trash2 :size="16" />
                </button>
              </div>
            </div>
            <div class="mt-5 flex items-center justify-between border-t border-slate-100 pt-4 dark:border-white/10">
              <span class="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold" :class="tool.blocked ? 'bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300' : 'bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300'">
                <ShieldCheck :size="13" />
                {{ tool.blocked ? t('Blocked') : t('Available') }}
              </span>
              <button class="text-sm font-semibold text-blue-600 transition hover:text-blue-700 dark:text-blue-300" @click="router.push(`/chat/tools/${tool.name}`)">
                {{ t('Open') }}
              </button>
            </div>
          </article>
        </div>
      </div>

      <div v-else class="mx-auto max-w-6xl space-y-9">
        <section class="rounded-3xl border border-blue-200/70 bg-white/85 p-5 shadow-sm ring-1 ring-white/60 dark:border-blue-400/15 dark:bg-white/[0.055] dark:ring-white/5">
          <div class="flex gap-4">
            <div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-blue-50 text-blue-700 dark:bg-blue-400/10 dark:text-blue-200">
              <Info :size="20" />
            </div>
            <div>
              <h2 class="text-sm font-bold text-[var(--text-primary)]">{{ t('About Model Context Protocol (MCP)') }}</h2>
              <p class="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                {{ t('MCP tools description') }}
                <code class="mx-1 rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-blue-700 dark:bg-white/10 dark:text-blue-200">stdio</code>
                {{ t('MCP stdio local only hint') }}
              </p>
            </div>
          </div>
        </section>

        <section class="space-y-4">
          <div class="flex flex-col gap-3 border-b border-slate-200 pb-4 dark:border-white/10 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 class="text-xl font-black tracking-tight text-[var(--text-primary)]">{{ t('Platform MCP') }}</h2>
              <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ t('Read-only servers loaded from deployment config.') }}</p>
            </div>
            <span class="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-[var(--text-secondary)] dark:bg-white/10">{{ t('server count summary', { count: groupedMcpServers.system.length }) }}</span>
          </div>
          <div v-if="groupedMcpServers.system.length === 0" class="rounded-3xl border border-dashed border-slate-300 bg-white/80 p-8 text-sm text-[var(--text-tertiary)] dark:border-white/10 dark:bg-white/[0.04]">
            {{ t('No platform MCP servers configured.') }}
          </div>
          <div v-else class="space-y-4">
            <article v-for="server in groupedMcpServers.system" :key="server.server_key" class="mcp-card">
              <div class="mcp-card-layout">
                <div class="mcp-card-main">
                  <div class="mcp-icon bg-violet-100 text-violet-700 dark:bg-violet-400/15 dark:text-violet-200"><Server :size="22" /></div>
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2">
                      <h3 class="truncate text-base font-bold text-[var(--text-primary)]">{{ server.name }}</h3>
                      <span class="badge-blue">{{ t('Platform') }}</span>
                      <span class="badge-muted">{{ server.transport }}</span>
                    </div>
                    <p class="mt-2 line-clamp-2 text-sm leading-6 text-[var(--text-secondary)]">{{ server.description || t('No description') }}</p>
                    <p class="mt-3 flex min-w-0 items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                      <Link2 :size="14" />
                      <span class="max-w-[520px] truncate font-mono">{{ formatServerEndpointText(server) }}</span>
                    </p>
                  </div>
                </div>
                <div class="mcp-actions">
                  <span class="status-pill" :class="server.default_enabled ? 'status-on' : 'status-muted'">{{ server.default_enabled ? t('Default on') : t('Default off') }}</span>
                  <button class="action-blue" @click="runTest(server)">{{ t('Test') }}</button>
                  <button class="action-blue" @click="openToolsDialog(server)">{{ t('Tools') }}</button>
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="space-y-4">
          <div class="flex flex-col gap-3 border-b border-slate-200 pb-4 dark:border-white/10 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 class="text-xl font-black tracking-tight text-[var(--text-primary)]">{{ t('My MCP') }}</h2>
              <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ t('Private MCP servers for your account.') }}</p>
            </div>
            <div class="flex items-center gap-2">
              <span class="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-[var(--text-secondary)] dark:bg-white/10">{{ t('server count summary', { count: groupedMcpServers.user.length }) }}</span>
              <button class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-[#8930b0] to-[#004be2] px-4 py-2 text-sm font-bold text-white shadow-lg transition hover:-translate-y-0.5 active:translate-y-0" @click="openCreateDialog">
                <Plus :size="16" />
                {{ t('Add MCP') }}
              </button>
            </div>
          </div>
          <div v-if="groupedMcpServers.user.length === 0" class="rounded-3xl border border-dashed border-slate-300 bg-white/80 p-10 text-center text-sm text-[var(--text-tertiary)] dark:border-white/10 dark:bg-white/[0.04]">
            <Database class="mx-auto mb-3" :size="30" />
            {{ t('No private MCP servers yet.') }}
          </div>
          <div v-else class="space-y-4">
            <article v-for="server in groupedMcpServers.user" :key="server.server_key" class="mcp-card">
              <div class="mcp-card-layout">
                <div class="mcp-card-main">
                  <div class="mcp-icon bg-slate-100 text-[var(--text-secondary)] dark:bg-white/10 dark:text-slate-200">
                    <Terminal v-if="server.transport === 'stdio'" :size="22" />
                    <Server v-else :size="22" />
                  </div>
                  <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2">
                      <h3 class="truncate text-base font-bold text-[var(--text-primary)]">{{ server.name }}</h3>
                      <span class="badge-violet">{{ t('Private') }}</span>
                      <span class="badge-muted">{{ server.transport }}</span>
                    </div>
                    <p class="mt-2 line-clamp-2 text-sm leading-6 text-[var(--text-secondary)]">{{ server.description || t('No description') }}</p>
                    <p class="mt-3 flex min-w-0 items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                      <Link2 :size="14" />
                      <span class="max-w-[520px] truncate font-mono">{{ formatServerEndpointText(server) }}</span>
                    </p>
                  </div>
                </div>
                <div class="mcp-actions">
                  <span class="status-pill" :class="server.enabled ? 'status-on' : 'status-warn'">{{ server.enabled ? t('Enabled') : t('Disabled') }}</span>
                  <button class="action-muted" @click="openEditDialog(server)">
                    <Pencil :size="14" class="inline" />
                    {{ t('Edit') }}
                  </button>
                  <button class="action-blue" @click="runTest(server)">{{ t('Test') }}</button>
                  <button class="action-blue" @click="openToolsDialog(server)">{{ t('Tools') }}</button>
                  <button class="action-danger" @click="deletePrivateServer(server)">{{ t('Delete') }}</button>
                </div>
              </div>
            </article>
          </div>
        </section>
      </div>
    </main>

    <Teleport to="body">
      <div v-if="formOpen" class="fixed inset-0 z-[9999] flex items-center justify-center px-4 py-6">
        <div class="absolute inset-0 bg-slate-950/55 backdrop-blur-sm" @click="closeFormDialog"></div>
        <div class="relative z-10 flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] shadow-2xl dark:border-white/10 dark:bg-[#101115]">
          <div class="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-5 dark:border-white/10 dark:bg-white/[0.055]">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-violet-100 text-violet-700 dark:bg-violet-400/15 dark:text-violet-200">
                <Settings2 :size="20" />
              </div>
              <div>
                <h3 class="text-xl font-black text-[var(--text-primary)]">{{ editingServer ? t('Edit MCP server') : t('Add MCP server') }}</h3>
                <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ t('Private MCP only. stdio works only in local mode.') }}</p>
              </div>
            </div>
            <button class="rounded-xl p-2 text-[var(--text-tertiary)] transition hover:bg-slate-100 hover:text-[var(--text-primary)] dark:hover:bg-white/10" @click="closeFormDialog">
              <X :size="18" />
            </button>
          </div>

          <div class="space-y-5 overflow-y-auto p-6">
            <section class="dialog-section">
              <h4 class="dialog-title">{{ t('Basic Info') }}</h4>
              <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
                <label class="field">
                  <span>{{ t('Name') }}</span>
                  <input v-model="form.name" class="tools-input" :placeholder="t('e.g. Local Filesystem')" >
                </label>
                <label class="field">
                  <span>{{ t('Transport') }}</span>
                  <select v-model="form.transport" class="tools-input">
                    <option value="streamable_http">Streamable HTTP</option>
                    <option value="sse">SSE</option>
                    <option value="stdio">stdio</option>
                  </select>
                </label>
                <label class="field md:col-span-2">
                  <span>{{ t('Description') }}</span>
                  <textarea v-model="form.description" rows="3" class="tools-input resize-none" :placeholder="t('Describe what this MCP server provides')"></textarea>
                </label>
              </div>
            </section>

            <section class="dialog-section">
              <h4 class="dialog-title flex items-center gap-2">
                <Link2 :size="16" class="text-blue-600 dark:text-blue-300" />
                {{ t('Connection Details') }}
              </h4>
              <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
                <template v-if="form.transport !== 'stdio'">
                  <label class="field md:col-span-2">
                    <span>{{ t('Endpoint URL') }}</span>
                    <input v-model="form.url" class="tools-input font-mono" placeholder="https://mcp.example.com/mcp" >
                  </label>
                </template>
                <template v-else>
                  <label class="field">
                    <span>{{ t('Command') }}</span>
                    <input v-model="form.command" class="tools-input font-mono" placeholder="npx" >
                  </label>
                  <label class="field">
                    <span>{{ t('Working Directory') }}</span>
                    <input v-model="form.cwd" class="tools-input font-mono" >
                  </label>
                  <label class="field md:col-span-2">
                    <span>{{ t('Arguments') }}</span>
                    <textarea v-model="form.argsText" rows="3" class="tools-input resize-none font-mono" :placeholder="t('One argument per line')"></textarea>
                  </label>
                </template>
                <label class="field">
                  <span>{{ t('Timeout (ms)') }}</span>
                  <input v-model.number="form.timeoutMs" type="number" min="1" class="tools-input font-mono" >
                </label>
              </div>
            </section>

            <section class="dialog-section">
              <h4 class="dialog-title flex items-center gap-2">
                <ShieldCheck :size="16" class="text-violet-600 dark:text-violet-300" />
                {{ t('Authentication & Headers') }}
              </h4>
              <div class="space-y-5">
                <label v-if="form.transport !== 'stdio'" class="field md:col-span-2">
                  <span>{{ t('HTTP Headers') }}</span>
                  <textarea
                    v-model="form.headersText"
                    rows="5"
                    class="tools-input resize-y font-mono"
                    :placeholder="t('HTTP headers with credentials placeholder')"
                  ></textarea>
                  <small>{{ t('HTTP headers credential hint') }}</small>
                </label>

                <div class="rounded-2xl border border-slate-200 bg-slate-50/70 p-4 dark:border-white/10 dark:bg-white/[0.04]">
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
                        <input v-model="binding.alias" class="tools-input font-mono" placeholder="github" >
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

                <div class="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <label v-if="form.transport === 'stdio'" class="field md:col-span-2">
                    <span>{{ t('Environment Variables') }}</span>
                    <textarea
                      v-model="form.envText"
                      rows="4"
                      class="tools-input resize-y font-mono"
                      :placeholder="t('Environment variables with credentials placeholder')"
                    ></textarea>
                    <small>{{ t('Environment variables are only applied to local stdio MCP processes.') }}</small>
                  </label>
                  <div v-if="form.transport !== 'stdio'" class="md:col-span-2">
                    <button class="text-xs font-bold text-blue-600 transition hover:text-blue-700 dark:text-blue-300" @click="showAdvancedQuery = !showAdvancedQuery">
                      {{ showAdvancedQuery ? t('Hide advanced query params') : t('Show advanced query params') }}
                    </button>
                    <label v-if="showAdvancedQuery" class="field mt-3">
                      <span>{{ t('Advanced Query Params') }}</span>
                      <textarea
                        v-model="form.queryText"
                        rows="3"
                        class="tools-input resize-y font-mono"
                        :placeholder="t('Query params with credentials placeholder')"
                      ></textarea>
                      <small>{{ t('Query params are appended to the MCP endpoint URL at runtime. Prefer headers for authentication when possible.') }}</small>
                    </label>
                  </div>
                </div>
              </div>
            </section>

            <section class="dialog-section">
              <h4 class="dialog-title">{{ t('Options') }}</h4>
              <div class="space-y-4">
                <label class="flex cursor-pointer items-center justify-between gap-4">
                  <span>
                    <span class="block text-sm font-bold text-[var(--text-primary)]">{{ t('Enabled') }}</span>
                    <span class="block text-xs text-[var(--text-tertiary)]">{{ t('Allow agents to use this MCP server.') }}</span>
                  </span>
                  <input v-model="form.enabled" type="checkbox" class="h-5 w-5 rounded border-slate-300 text-blue-600" >
                </label>
                <div class="h-px bg-slate-100 dark:bg-white/10"></div>
                <label class="flex cursor-pointer items-center justify-between gap-4">
                  <span>
                    <span class="block text-sm font-bold text-[var(--text-primary)]">{{ t('Default enabled for new sessions') }}</span>
                    <span class="block text-xs text-[var(--text-tertiary)]">{{ t('Automatically enable this server when a new session starts.') }}</span>
                  </span>
                  <input v-model="form.defaultEnabled" type="checkbox" class="h-5 w-5 rounded border-slate-300 text-blue-600" >
                </label>
              </div>
            </section>
          </div>

          <div class="flex justify-end gap-3 border-t border-slate-200 bg-white px-6 py-4 dark:border-white/10 dark:bg-white/[0.055]">
            <button class="rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/10" @click="closeFormDialog">{{ t('Cancel') }}</button>
            <button class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-[#8930b0] to-[#004be2] px-5 py-2 text-sm font-bold text-white shadow-lg transition disabled:cursor-not-allowed disabled:opacity-60" :disabled="savingForm" @click="submitForm">
              <Loader2 v-if="savingForm" class="animate-spin" :size="16" />
              {{ savingForm ? t('Saving...') : editingServer ? t('Save changes') : t('Create MCP') }}
            </button>
          </div>
        </div>
      </div>

      <div v-if="toolsDialogOpen" class="fixed inset-0 z-[9999] flex items-center justify-center px-4 py-6">
        <div class="absolute inset-0 bg-slate-950/55 backdrop-blur-sm" @click="closeToolsDialog"></div>
        <div class="relative z-10 flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl dark:border-white/10 dark:bg-[#17181d]">
          <div class="flex items-center justify-between gap-4 border-b border-slate-200 px-6 py-5 dark:border-white/10">
            <div>
              <h3 class="text-xl font-black text-[var(--text-primary)]">{{ selectedServer?.name || t('MCP tools') }}</h3>
              <p class="mt-1 text-sm text-[var(--text-tertiary)]">{{ t('discovered tools summary', { count: discoveredTools.length }) }}</p>
            </div>
            <button class="rounded-xl p-2 text-[var(--text-tertiary)] transition hover:bg-slate-100 hover:text-[var(--text-primary)] dark:hover:bg-white/10" @click="closeToolsDialog">
              <X :size="18" />
            </button>
          </div>
          <div class="max-h-[70vh] space-y-3 overflow-y-auto p-6">
            <div v-if="discoveredTools.length === 0" class="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-[var(--text-tertiary)] dark:border-white/10">
              {{ t('No tools returned by this MCP server.') }}
            </div>
            <div v-for="tool in discoveredTools" :key="tool.name" class="rounded-2xl border border-slate-200 bg-slate-50/80 p-4 dark:border-white/10 dark:bg-white/[0.04]">
              <h4 class="text-sm font-bold text-[var(--text-primary)]">{{ tool.name }}</h4>
              <p class="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{{ tool.description || t('No description') }}</p>
              <pre class="mt-3 overflow-x-auto rounded-xl border border-slate-200 bg-white p-3 text-xs text-[var(--text-secondary)] dark:border-white/10 dark:bg-[#101115]"><code>{{ JSON.stringify(tool.input_schema || {}, null, 2) }}</code></pre>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  Code2,
  Database,
  Eye,
  EyeOff,
  Info,
  Link2,
  Loader2,
  Pencil,
  Plus,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  Terminal,
  Trash2,
  Wrench,
  X,
} from 'lucide-vue-next';
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
import {
  formatMcpServerEndpoint,
  groupMcpServers,
  parseKeyValueTemplateText,
  parseHttpHeaderText,
  splitCredentialTemplateMap,
  stringifyKeyValueTemplateMap,
  stringifyHttpHeaders,
} from '../utils/mcpUi';

const router = useRouter();
const { t } = useI18n();

const activeTab = ref<'external' | 'mcp'>('external');
const searchQuery = ref('');
const externalTools = ref<ExternalToolItem[]>([]);
const extLoading = ref(false);
const mcpServers = ref<McpServerItem[]>([]);
const credentials = ref<Credential[]>([]);
const formOpen = ref(false);
const editingServer = ref<McpServerItem | null>(null);
const savingForm = ref(false);
const showAdvancedQuery = ref(false);
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
  credentialBindings: [{ alias: 'credential', credentialId: '' }],
  envText: '',
  queryText: '',
});

const filteredExtTools = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return externalTools.value;
  return externalTools.value.filter((tool) =>
    [tool.name, tool.file, tool.description].some((value) => value?.toLowerCase().includes(query)),
  );
});

const filteredMcpServers = computed(() => {
  const query = searchQuery.value.trim().toLowerCase();
  if (!query) return mcpServers.value;
  return mcpServers.value.filter((server) =>
    [server.name, server.description, server.transport, server.scope, server.server_key, formatMcpServerEndpoint(server)]
      .some((value) => value?.toLowerCase().includes(query)),
  );
});

const groupedMcpServers = computed(() => groupMcpServers(filteredMcpServers.value));
const activeSummary = computed(() => (
  activeTab.value === 'external'
    ? t('external tools count summary', { count: externalTools.value.length })
    : t('MCP servers count summary', { count: mcpServers.value.length })
));
const searchPlaceholder = computed(() => (
  activeTab.value === 'external' ? t('Search tools...') : t('Search MCP servers...')
));

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
  form.credentialBindings = [{ alias: 'credential', credentialId: '' }];
  form.envText = '';
  form.queryText = '';
  showAdvancedQuery.value = false;
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
  const binding = server.credential_binding || {
    credential_id: '',
    credentials: [],
    headers: {},
    env: {},
    query: {},
  };
  const bindings = (binding.credentials || [])
    .map((item) => ({ alias: item.alias || '', credentialId: item.credential_id || '' }))
    .filter((item) => item.alias || item.credentialId);
  if (bindings.length === 0 && binding.credential_id) {
    bindings.push({ alias: 'credential', credentialId: binding.credential_id });
  }
  form.credentialBindings = bindings.length > 0 ? bindings : [{ alias: 'credential', credentialId: '' }];
  form.headersText = stringifyHttpHeaders({ ...(endpoint.headers || {}), ...(binding.headers || {}) });
  form.envText = stringifyKeyValueTemplateMap({ ...(endpoint.env || {}), ...(binding.env || {}) });
  form.queryText = stringifyKeyValueTemplateMap(binding.query);
  showAdvancedQuery.value = Boolean(form.queryText.trim());
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
    showErrorToast(t('Failed to load tools'));
  } finally {
    extLoading.value = false;
  }
};

onMounted(loadData);

const getExternalToolInitial = (tool: ExternalToolItem) => tool.name.trim().charAt(0).toUpperCase() || 'T';

const formatServerEndpointText = (server: McpServerItem) => {
  const endpoint = formatMcpServerEndpoint(server);
  return endpoint === 'No endpoint' ? t('No endpoint') : endpoint;
};

const handleToggleBlock = async (tool: ExternalToolItem) => {
  try {
    await blockTool(tool.name, !tool.blocked);
    tool.blocked = !tool.blocked;
  } catch (error) {
    console.error(error);
    showErrorToast(t('Failed to update tool visibility'));
  }
};

const deleteExternalTool = async (tool: ExternalToolItem) => {
  if (!window.confirm(t('Are you sure you want to delete the tool "{name}"?', { name: tool.name }))) return;
  try {
    await apiDeleteTool(tool.name);
    externalTools.value = externalTools.value.filter((item) => item.name !== tool.name);
    showSuccessToast(t('External tool deleted'));
  } catch (error) {
    console.error(error);
    showErrorToast(t('Failed to delete external tool'));
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

const addCredentialBinding = () => {
  form.credentialBindings.push({ alias: '', credentialId: '' });
};

const removeCredentialBinding = (index: number) => {
  form.credentialBindings.splice(index, 1);
  if (form.credentialBindings.length === 0) {
    form.credentialBindings.push({ alias: 'credential', credentialId: '' });
  }
};

const buildPayload = () => {
  const headerSplit = splitCredentialTemplateMap(parseHttpHeaderText(form.headersText));
  const envSplit = splitCredentialTemplateMap(parseKeyValueTemplateText(form.envText));

  return {
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
          env: envSplit.staticValues,
          timeout_ms: form.timeoutMs,
        }
      : {
          url: form.url.trim(),
          headers: headerSplit.staticValues,
          timeout_ms: form.timeoutMs,
        },
    credential_binding: {
      credential_id: '',
      credentials: form.credentialBindings
        .map((item) => ({ alias: item.alias.trim(), credential_id: item.credentialId.trim() }))
        .filter((item) => item.alias && item.credential_id),
      headers: form.transport !== 'stdio' ? headerSplit.credentialValues : {},
      env: form.transport === 'stdio' ? envSplit.credentialValues : {},
      query: form.transport !== 'stdio' ? parseKeyValueTemplateText(form.queryText) : {},
    },
    tool_policy: {
      allowed_tools: [],
      blocked_tools: [],
    },
  };
};

const submitForm = async () => {
  if (!form.name.trim()) {
    showErrorToast(t('MCP server name is required'));
    return;
  }
  if (form.transport === 'stdio' && !form.command.trim()) {
    showErrorToast(t('Command is required for stdio MCP'));
    return;
  }
  if (form.transport !== 'stdio' && !form.url.trim()) {
    showErrorToast(t('Endpoint URL is required'));
    return;
  }

  savingForm.value = true;
  try {
    const payload = buildPayload();
    if (editingServer.value) {
      await updateMcpServer(editingServer.value.id, payload);
      showSuccessToast(t('MCP server updated'));
    } else {
      await createMcpServer(payload);
      showSuccessToast(t('MCP server created'));
    }
    closeFormDialog();
    mcpServers.value = await listMcpServers();
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to save MCP server'));
  } finally {
    savingForm.value = false;
  }
};

const runTest = async (server: McpServerItem) => {
  try {
    const result = await testMcpServer(server.server_key);
    showSuccessToast(t('MCP server test success', { name: server.name, count: result.tool_count }));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to test MCP server', { name: server.name }));
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
    showErrorToast(error?.message || t('Failed to discover MCP tools', { name: server.name }));
  }
};

const closeToolsDialog = () => {
  toolsDialogOpen.value = false;
  selectedServer.value = null;
  discoveredTools.value = [];
};

const deletePrivateServer = async (server: McpServerItem) => {
  if (!window.confirm(t('Delete MCP server confirm', { name: server.name }))) return;
  try {
    await deleteMcpServer(server.id);
    mcpServers.value = await listMcpServers();
    showSuccessToast(t('MCP server deleted'));
  } catch (error: any) {
    console.error(error);
    showErrorToast(error?.message || t('Failed to delete MCP server'));
  }
};
</script>

<style scoped>
.tools-library {
  --tools-ring: rgba(0, 129, 242, 0.28);
}

.mcp-card {
  border-radius: 1.5rem;
  border: 1px solid rgba(226, 232, 240, 0.9);
  background: white;
  padding: 1.25rem;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
  transition: box-shadow 0.2s ease, transform 0.2s ease, background-color 0.2s ease;
}

.mcp-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 16px 38px rgba(15, 23, 42, 0.07);
}

.mcp-card-layout {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
}

.mcp-card-main {
  display: flex;
  min-width: 0;
  gap: 1rem;
}

.mcp-actions {
  display: flex;
  flex-shrink: 0;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.mcp-icon {
  display: flex;
  height: 3rem;
  width: 3rem;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  border-radius: 1rem;
}

.badge-blue,
.badge-violet,
.badge-muted {
  border-radius: 0.375rem;
  padding: 0.125rem 0.5rem;
  font-size: 10px;
  font-weight: 900;
  line-height: 1rem;
}

.badge-blue {
  background: rgba(219, 234, 254, 0.85);
  color: rgb(29, 78, 216);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.badge-violet {
  background: rgba(237, 233, 254, 0.9);
  color: rgb(109, 40, 217);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.badge-muted {
  background: rgb(241, 245, 249);
  color: var(--text-secondary);
}

.status-pill,
.action-blue,
.action-muted,
.action-danger {
  border-radius: 0.75rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 800;
  line-height: 1rem;
}

.status-on {
  background: rgba(236, 253, 245, 0.95);
  color: rgb(4, 120, 87);
}

.status-warn {
  background: rgba(255, 251, 235, 0.95);
  color: rgb(180, 83, 9);
}

.status-muted {
  background: rgb(241, 245, 249);
  color: var(--text-secondary);
}

.action-blue {
  border: 1px solid rgba(147, 197, 253, 0.75);
  color: rgb(29, 78, 216);
  transition: background-color 0.15s ease;
}

.action-blue:hover {
  background: rgb(239, 246, 255);
}

.action-muted {
  border: 1px solid rgb(226, 232, 240);
  color: var(--text-secondary);
  transition: background-color 0.15s ease;
}

.action-muted:hover {
  background: rgb(248, 250, 252);
}

.action-danger {
  border: 1px solid rgb(254, 202, 202);
  color: rgb(220, 38, 38);
  transition: background-color 0.15s ease;
}

.action-danger:hover {
  background: rgb(254, 242, 242);
}

.dialog-section {
  border-radius: 1.5rem;
  border: 1px solid rgb(226, 232, 240);
  background: white;
  padding: 1.25rem;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}

.dialog-title {
  margin-bottom: 1rem;
  color: var(--text-primary);
  font-size: 0.875rem;
  font-weight: 900;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.field > span {
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 800;
}

.field > small {
  color: var(--text-tertiary);
  font-size: 11px;
}

.tools-input {
  border-radius: 0.9rem;
  border: 1px solid rgba(148, 163, 184, 0.38);
  background: rgba(255, 255, 255, 0.92);
  color: var(--text-primary);
  font-size: 0.875rem;
  outline: none;
  padding: 0.625rem 0.8rem;
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease;
}

.tools-input:focus {
  border-color: rgba(0, 129, 242, 0.72);
  box-shadow: 0 0 0 3px var(--tools-ring);
}

@media (min-width: 1024px) {
  .mcp-card-layout {
    align-items: center;
    flex-direction: row;
    justify-content: space-between;
  }
}

.dark .mcp-card,
.dark .dialog-section {
  border-color: rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.055);
}

.dark .badge-blue {
  background: rgba(96, 165, 250, 0.1);
  color: rgb(191, 219, 254);
}

.dark .badge-violet {
  background: rgba(167, 139, 250, 0.12);
  color: rgb(221, 214, 254);
}

.dark .badge-muted,
.dark .status-muted {
  background: rgba(255, 255, 255, 0.1);
}

.dark .status-on {
  background: rgba(16, 185, 129, 0.12);
  color: rgb(110, 231, 183);
}

.dark .status-warn {
  background: rgba(245, 158, 11, 0.12);
  color: rgb(252, 211, 77);
}

.dark .action-blue {
  border-color: rgba(96, 165, 250, 0.28);
  color: rgb(191, 219, 254);
}

.dark .action-blue:hover {
  background: rgba(96, 165, 250, 0.1);
}

.dark .action-muted {
  border-color: rgba(255, 255, 255, 0.1);
}

.dark .action-muted:hover {
  background: rgba(255, 255, 255, 0.1);
}

.dark .action-danger {
  border-color: rgba(248, 113, 113, 0.28);
  color: rgb(252, 165, 165);
}

.dark .action-danger:hover {
  background: rgba(239, 68, 68, 0.1);
}

.dark .tools-input {
  border-color: rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.06);
}
</style>
