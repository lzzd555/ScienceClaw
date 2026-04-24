import re

with open('RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update imports
icons_pattern = r"import \{([^}]+)\} from 'lucide-vue-next';"
def add_icons(match):
    icons = match.group(1)
    for icon in ['Save', 'Play', 'Wrench', 'Terminal']:
        if icon not in icons:
            icons += f", {icon}"
    return f"import {{{icons}}} from 'lucide-vue-next';"

content = re.sub(icons_pattern, add_icons, content, count=1)

# 2. Find the expanded view part and replace it
# It starts at: <div v-if="expandedToolIds.has(tool.id)"
# and ends at: </article>
start_str = '                <div v-if="expandedToolIds.has(tool.id)"'
end_str = '              </article>'

start_idx = content.find(start_str)
end_idx = content.find(end_str, start_idx)

if start_idx == -1 or end_idx == -1:
    print("Error finding template boundaries")
    exit(1)

new_expanded_view = """                <div v-if="expandedToolIds.has(tool.id)" class="border-t border-slate-200 bg-white/80 px-5 py-6 dark:border-white/10 dark:bg-[#111317]">
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
"""

new_content = content[:start_idx] + new_expanded_view + content[end_idx:]

with open('RpaClaw/frontend/src/components/tools/ApiMonitorMcpDetailDialog.vue', 'w', encoding='utf-8') as f:
    f.write(new_content)
