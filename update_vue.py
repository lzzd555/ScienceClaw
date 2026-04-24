import re

with open('RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue', 'r', encoding='utf-8') as f:
    content = f.read()

script_match = re.search(r'(<script setup lang="ts">.*?</script>)', content, re.DOTALL)
if not script_match:
    print("Script tag not found")
    exit(1)

script_content = script_match.group(1)

new_template = """
<template>
  <div class="flex flex-col h-screen bg-[#f8f9fb] dark:bg-[#111] text-[var(--text-primary)] overflow-hidden">
    <!-- Top control bar -->
    <header class="h-14 flex-shrink-0 bg-white dark:bg-[#1a1a1a] border-b border-gray-200 dark:border-gray-800 flex items-center px-4 gap-3 z-50">
      <button
        @click="goBack"
        class="flex items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors text-sm font-medium px-2 py-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800"
      >
        <span class="material-symbols-outlined text-lg">arrow_back</span>
        Back
      </button>

      <div class="w-px h-6 bg-gray-200 dark:bg-gray-700"></div>

      <h1 class="text-[var(--text-primary)] font-bold text-base whitespace-nowrap">API Monitor</h1>

      <div class="flex-1 flex items-center justify-center">
        <div class="w-full max-w-2xl flex items-center gap-2">
          <div class="relative flex-1 flex items-center">
            <span class="material-symbols-outlined absolute left-3 text-[var(--text-tertiary)] text-sm">language</span>
            <input
              v-model="urlInput"
              class="w-full bg-gray-50 dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 rounded-lg py-1.5 pl-9 pr-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/50 transition-shadow font-mono"
              placeholder="Enter URL to monitor..."
              type="text"
              spellcheck="false"
              @keyup.enter="handleStartSession"
            />
          </div>
          <button
            @click="handleStartSession"
            :disabled="loading"
            class="px-4 py-1.5 bg-sky-500 text-white text-sm font-medium rounded-lg hover:bg-sky-600 transition-colors shadow-sm disabled:opacity-50 whitespace-nowrap"
          >
            Go
          </button>
        </div>
      </div>

      <div class="flex items-center gap-2">
        <button
          @click="startAnalysis"
          :disabled="!sessionId || isAnalyzing"
          class="flex items-center gap-1.5 px-3 py-1.5 bg-white dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 rounded-lg text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-gray-300 dark:hover:border-gray-600 transition-colors disabled:opacity-50"
        >
          <span class="material-symbols-outlined text-base">search_insights</span>
          Analyze
        </button>
        <button
          @click="toggleRecording"
          :disabled="!sessionId"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
          :class="isRecording
            ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800/50 hover:bg-red-100 dark:hover:bg-red-900/40'
            : 'bg-white dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-gray-300 dark:hover:border-gray-600'"
        >
          <span class="material-symbols-outlined text-base">{{ isRecording ? 'stop' : 'fiber_manual_record' }}</span>
          {{ isRecording ? 'Stop' : 'Record' }}
        </button>
        <button
          @click="openPublishDialog"
          :disabled="!sessionId || !tools.length || isPublishing"
          class="flex items-center gap-1.5 px-3 py-1.5 bg-sky-50 dark:bg-sky-900/20 border border-sky-200 dark:border-sky-800/50 rounded-lg text-sm font-medium text-sky-600 dark:text-sky-400 hover:bg-sky-100 dark:hover:bg-sky-900/40 transition-colors disabled:opacity-50 ml-1"
        >
          <span class="material-symbols-outlined text-base">save</span>
          Save as MCP Tool
        </button>
      </div>
    </header>

    <!-- Main content: left browser viewport, right terminal + tools -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left: Browser viewport -->
      <section class="flex-1 flex flex-col relative border-r border-gray-200 dark:border-gray-800 bg-gray-100 dark:bg-black">
        <div class="flex-1 relative overflow-hidden flex items-center justify-center">
          <canvas
            v-if="sessionId"
            ref="canvasRef"
            class="w-full h-full object-contain cursor-default"
            tabindex="0"
            @click="focusCanvas"
            @mousedown="sendInputEvent"
            @mouseup="sendInputEvent"
            @mousemove="sendInputEvent"
            @wheel.prevent="sendInputEvent"
            @keydown.prevent="sendInputEvent"
            @keyup.prevent="sendInputEvent"
            @contextmenu.prevent
          />
          <div
            v-else
            class="absolute inset-0 flex items-center justify-center flex-col gap-4 text-[var(--text-tertiary)]"
          >
            <span class="material-symbols-outlined text-[80px] opacity-30">language</span>
            <p class="text-sm font-medium">Enter a URL and click Go to start monitoring</p>
          </div>

          <!-- Error overlay -->
          <div v-if="error && sessionId" class="absolute top-3 right-3 max-w-xs bg-red-100 dark:bg-red-900/80 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-300 text-xs px-3 py-2 rounded-lg shadow-lg backdrop-blur-sm">
            {{ error }}
          </div>

          <!-- Live indicator -->
          <div v-if="sessionId" class="absolute bottom-10 left-1/2 -translate-x-1/2 bg-white/80 dark:bg-black/50 backdrop-blur-md border border-gray-200 dark:border-gray-700 px-3 py-1 rounded-full flex items-center gap-2 shadow-sm">
            <span class="material-symbols-outlined text-sky-500 text-sm">monitor</span>
            <span class="text-[var(--text-primary)] text-[10px] font-bold tracking-wider uppercase">Live Viewport</span>
          </div>
        </div>
        
        <!-- Status Bar inside left panel to match DOM -->
        <div class="h-8 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-[#1a1a1a] flex items-center px-4 gap-4 text-xs text-[var(--text-secondary)] flex-shrink-0">
          <div class="flex items-center gap-1.5">
            <span class="font-mono font-bold" :class="tools.length > 0 ? 'text-sky-500' : 'text-[var(--text-tertiary)]'">{{ tools.length }}</span> tools
          </div>
          <div class="w-px h-3 bg-gray-300 dark:bg-gray-700"></div>
          <div class="flex items-center gap-1.5">
            Status:
            <span :class="session?.status === 'recording' ? 'text-red-500 font-medium' : session?.status ? 'text-sky-500 font-medium' : 'text-[var(--text-tertiary)]'">
              {{ session?.status || 'idle' }}
            </span>
          </div>
          <div v-if="isRecording" class="flex items-center gap-1.5 ml-2 bg-red-50 dark:bg-red-900/20 px-2 py-0.5 rounded border border-red-100 dark:border-red-800/50">
            <div class="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></div>
            <span class="text-red-600 dark:text-red-400 font-bold text-[10px]">REC</span>
          </div>
          <div class="flex-1"></div>
          <span v-if="sessionId" class="font-mono text-[10px] text-[var(--text-tertiary)]" title="Session ID">{{ sessionId.slice(0, 8) }}</span>
        </div>
      </section>

      <!-- Right: Terminal + Tools -->
      <section class="w-[500px] flex-shrink-0 flex flex-col bg-white dark:bg-[#1a1a1a] border-l border-gray-200 dark:border-gray-800">
        <!-- Terminal log (top half) -->
        <div class="flex-1 flex flex-col min-h-0 border-b border-gray-200 dark:border-gray-800">
          <div class="h-8 flex items-center justify-between px-3 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-[#161616] shrink-0">
            <div class="flex items-center gap-2 text-xs font-mono text-[var(--text-tertiary)]">
              <div class="flex gap-1.5 mr-2">
                <div class="w-2.5 h-2.5 rounded-full bg-red-400/80 dark:bg-red-500/50"></div>
                <div class="w-2.5 h-2.5 rounded-full bg-amber-400/80 dark:bg-amber-500/50"></div>
                <div class="w-2.5 h-2.5 rounded-full bg-sky-400/80 dark:bg-sky-500/50"></div>
              </div>
              api-monitor.log
            </div>
            <button
              @click="clearLogs"
              class="text-[10px] uppercase tracking-wider text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors font-semibold"
            >
              Clear
            </button>
          </div>
          <!-- Terminal output is usually kept dark or matched with editor -->
          <div
            ref="terminalRef"
            class="flex-1 overflow-auto p-3 font-mono text-[11px] leading-relaxed bg-[#f1f3f5] dark:bg-[#0d1117] text-gray-800 dark:text-gray-300"
          >
            <div
              v-for="(line, idx) in terminalLines"
              :key="idx"
              v-html="line.html"
              class="mb-0.5 break-all"
            ></div>
            <div v-if="terminalLines.length === 0" class="text-[var(--text-tertiary)] italic">
              Waiting for activity...
            </div>
          </div>
        </div>

        <!-- Tool cards (bottom half) -->
        <div class="flex-1 flex flex-col min-h-0 bg-white dark:bg-[#1a1a1a]">
          <div class="h-10 flex items-center px-3 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-[#161616] shrink-0 gap-2">
            <span class="material-symbols-outlined text-sky-500 text-sm">build</span>
            <h3 class="text-sm font-medium text-[var(--text-primary)]">Detected Tools</h3>
            <span class="px-1.5 py-0.5 rounded-sm bg-gray-200 dark:bg-gray-800 text-[var(--text-primary)] font-mono text-[10px] leading-none ml-1">{{ tools.length }}</span>
          </div>
          <div class="flex-1 overflow-y-auto p-3 space-y-2 bg-[#f8f9fb] dark:bg-[#111]">
            <!-- Empty state -->
            <div v-if="tools.length === 0" class="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
              <span class="material-symbols-outlined text-5xl mb-3 opacity-40">build_circle</span>
              <p class="text-sm font-medium text-[var(--text-secondary)] mb-1">No tools detected yet.</p>
              <p class="text-xs">Click Analyze or Record to discover API tools.</p>
            </div>

            <!-- Tool cards -->
            <div
              v-for="tool in tools"
              :key="tool.id"
              class="bg-white dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 rounded-lg shadow-sm overflow-hidden"
            >
              <!-- Collapsed view -->
              <div
                class="flex items-center gap-2 px-3 py-2.5 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
                @click="toggleToolExpand(tool.id)"
              >
                <span
                  class="text-[10px] font-bold px-1.5 py-0.5 rounded border"
                  :class="getMethodClass(tool.method)"
                >
                  {{ tool.method }}
                </span>
                <span class="text-[11px] font-mono text-[var(--text-primary)] flex-1 truncate">{{ tool.url_pattern }}</span>
                <span class="material-symbols-outlined text-[var(--text-tertiary)] text-sm transition-transform" :class="expandedToolId === tool.id ? 'rotate-180' : ''">
                  expand_more
                </span>
              </div>

              <!-- Expanded view -->
              <div v-if="expandedToolId === tool.id" class="border-t border-gray-100 dark:border-gray-800 px-3 py-3 bg-gray-50 dark:bg-[#161616]">
                <p class="text-[11px] text-[var(--text-secondary)] mb-2 font-medium">{{ tool.description }}</p>
                <textarea
                  v-model="toolEdits[tool.id]"
                  class="w-full h-32 bg-white dark:bg-[#0d1117] border border-gray-200 dark:border-gray-700 rounded-md text-[11px] font-mono text-[var(--text-primary)] p-2 outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 resize-y transition-shadow shadow-inner"
                  spellcheck="false"
                ></textarea>
                <div class="flex justify-end gap-2 mt-3">
                  <button
                    @click="handleDeleteTool(tool.id)"
                    class="px-3 py-1.5 text-xs font-medium bg-white dark:bg-[#1e1e1e] text-red-500 border border-gray-200 dark:border-gray-700 rounded-md hover:bg-red-50 hover:border-red-200 dark:hover:bg-red-900/20 dark:hover:border-red-800 transition-colors shadow-sm"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- Modals -->
    <div
      v-if="publishDialogOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-sm px-4"
    >
      <div class="w-full max-w-md rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1e1e1e] p-5 shadow-2xl">
        <div class="flex items-center justify-between">
          <h2 class="text-base font-bold text-[var(--text-primary)]">Save as MCP Tool</h2>
          <button
            class="text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors p-1"
            @click="publishDialogOpen = false"
          >
            <span class="material-symbols-outlined text-lg block">close</span>
          </button>
        </div>
        <label class="mt-4 block text-xs font-semibold text-[var(--text-secondary)]">
          MCP Name
          <input
            v-model="publishForm.mcpName"
            class="mt-1.5 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#161616] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 transition-shadow shadow-sm"
            type="text"
            placeholder="e.g. My Website API"
          />
        </label>
        <label class="mt-4 block text-xs font-semibold text-[var(--text-secondary)]">
          Description
          <textarea
            v-model="publishForm.description"
            class="mt-1.5 h-24 w-full resize-y rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#161616] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 transition-shadow shadow-sm"
            placeholder="Describe what these tools do..."
          ></textarea>
        </label>
        <div class="mt-6 flex justify-end gap-2">
          <button
            class="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            @click="publishDialogOpen = false"
          >
            Cancel
          </button>
          <button
            class="rounded-lg bg-sky-500 px-4 py-2 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50 transition-colors shadow-sm"
            :disabled="isPublishing || !publishForm.mcpName.trim()"
            @click="submitPublish(false)"
          >
            Save
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="overwriteDialogOpen"
      class="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-sm px-4"
    >
      <div class="w-full max-w-md rounded-xl border border-red-200 dark:border-red-900/50 bg-white dark:bg-[#1e1e1e] p-5 shadow-2xl">
        <h2 class="text-base font-bold text-[var(--text-primary)] flex items-center gap-2">
          <span class="material-symbols-outlined text-amber-500">warning</span>
          Replace existing MCP tools?
        </h2>
        <p class="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
          An MCP named <span class="font-semibold text-[var(--text-primary)]">"{{ publishForm.mcpName }}"</span> already exists. Replacing it will overwrite all tools under that MCP with the current API Monitor results.
        </p>
        <div class="mt-6 flex justify-end gap-2">
          <button
            class="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            @click="overwriteDialogOpen = false"
          >
            Cancel
          </button>
          <button
            class="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50 transition-colors shadow-sm"
            :disabled="isPublishing"
            @click="submitPublish(true)"
          >
            Replace Tools
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
"""

# We also need to fix the terminal colors to look good in both light and dark mode
script_content_fixed = script_content.replace(
"""const LOG_COLORS: Record<LogLevel, string> = {
  INFO: 'text-[#57f1db]',
  RECV: 'text-[#bacac5]',
  ANALYZE: 'text-[#7ecfcf]',
  BUILD: 'text-[#57f1db]',
  ERROR: 'text-[#ffb4ab]',
};""",
"""const LOG_COLORS: Record<LogLevel, string> = {
  INFO: 'text-sky-600 dark:text-sky-400',
  RECV: 'text-gray-500 dark:text-gray-400',
  ANALYZE: 'text-teal-600 dark:text-teal-400',
  BUILD: 'text-sky-600 dark:text-sky-400',
  ERROR: 'text-red-500 dark:text-red-400',
};"""
).replace(
"""const colorClass = LOG_COLORS[level] || 'text-[#dae2fd]';
  const escaped = message
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  terminalLines.value.push({
    html: `<span class="text-[#5a6a65]">${ts}</span> <span class="font-bold ${colorClass}">[${level}]</span> <span class="${colorClass}">${escaped}</span>`,
  });""",
"""const colorClass = LOG_COLORS[level] || 'text-[var(--text-primary)]';
  const escaped = message
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  terminalLines.value.push({
    html: `<span class="text-[var(--text-tertiary)]">${ts}</span> <span class="font-bold ${colorClass}">[${level}]</span> <span class="${colorClass}">${escaped}</span>`,
  });"""
).replace(
"""const methodColors: Record<string, string> = {
  GET: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  POST: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  PUT: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  DELETE: 'bg-red-500/20 text-red-400 border-red-500/30',
  PATCH: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
};

const getMethodClass = (method: string) => methodColors[method.toUpperCase()] || 'bg-gray-500/20 text-gray-400 border-gray-500/30';""",
"""const methodColors: Record<string, string> = {
  GET: 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-200 dark:border-emerald-500/30',
  POST: 'bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-500/30',
  PUT: 'bg-amber-50 dark:bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-200 dark:border-amber-500/30',
  DELETE: 'bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-400 border-red-200 dark:border-red-500/30',
  PATCH: 'bg-purple-50 dark:bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-200 dark:border-purple-500/30',
};

const getMethodClass = (method: string) => methodColors[method.toUpperCase()] || 'bg-gray-100 dark:bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-200 dark:border-gray-500/30';"""
)

with open('RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue', 'w', encoding='utf-8') as f:
    f.write(script_content_fixed + "\n\n" + new_template + "\n")
