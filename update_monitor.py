import re

with open('RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace script to update icons and translations
script_pattern = r'(<script setup lang="ts">)'
icons_import = """\\1
import { ArrowLeft, Globe, BarChart2, Disc, Square, Save, Wrench, ChevronDown, MonitorPlay, X, AlertTriangle, Terminal } from 'lucide-vue-next';"""

content = re.sub(script_pattern, icons_import, content, count=1)

# Translate the logs in script
content = content.replace("'API Monitor ready. Enter a URL and click Go to start.'", "'API 监控已准备就绪。输入 URL 并点击 Go 开始。'")
content = content.replace("'Screencast connected'", "'屏幕录制已连接'")
content = content.replace("`Starting session: ${url}`", "`正在启动会话: ${url}`")
content = content.replace("`Session created: ${s.id}`", "`已创建会话: ${s.id}`")
content = content.replace("`Loaded ${existingTools.length} existing tools`", "`已加载 ${existingTools.length} 个现有工具`")
content = content.replace("'Failed to start session'", "'启动会话失败'")
content = content.replace("`Failed to start session: ${error.value}`", "`启动会话失败: ${error.value}`")
content = content.replace("'Starting analysis...'", "'开始分析...'")
content = content.replace("`Analyzing: ${data.url}`", "`正在分析: ${data.url}`")
content = content.replace("'Processing...'", "'处理中...'")
content = content.replace("`Found ${data.count} interactive elements`", "`找到 ${data.count} 个可交互元素`")
content = content.replace("`Classified: ${data.safe} safe, ${data.skipped} skipped`", "`已分类: ${data.safe} 个安全, ${data.skipped} 个跳过`")
content = content.replace("`Captured ${data.calls} API calls from element ${data.element_index}`", "`从元素 ${data.element_index} 捕获了 ${data.calls} 个 API 调用`")
content = content.replace("`Analysis complete: ${data.tools_generated} tools, ${data.total_calls} calls`", "`分析完成: ${data.tools_generated} 个工具, ${data.total_calls} 个调用`")
content = content.replace("'Stopping recording...'", "'正在停止录制...'")
content = content.replace("`Recording stopped. ${newTools.length} tools generated.`", "`录制已停止。生成了 ${newTools.length} 个工具。`")
content = content.replace("`Failed to stop recording: ${err.message}`", "`停止录制失败: ${err.message}`")
content = content.replace("'Starting recording...'", "'正在开始录制...'")
content = content.replace("'Recording started. Interact with the browser to capture API calls.'", "'录制已开始。请与浏览器交互以捕获 API 调用。'")
content = content.replace("`Failed to start recording: ${err.message}`", "`开始录制失败: ${err.message}`")
content = content.replace("`Deleting tool: ${toolId}`", "`正在删除工具: ${toolId}`")
content = content.replace("`Tool deleted`", "`工具已删除`")
content = content.replace("`Failed to delete tool: ${err.message}`", "`删除工具失败: ${err.message}`")
content = content.replace("'Publishing MCP tools...'", "'正在发布 MCP 工具...'")
content = content.replace("`Saved MCP \"${publishForm.mcpName}\" with ${result.tool_count} tools`", "`已保存 MCP \"${publishForm.mcpName}\"，包含 ${result.tool_count} 个工具`")
content = content.replace("'Existing MCP found. Waiting for overwrite confirmation.'", "'发现已存在的 MCP。等待覆盖确认。'")
content = content.replace("`Failed to save MCP: ${err.message}`", "`保存 MCP 失败: ${err.message}`")

# Replace the template section
template_start = content.find('<template>')
if template_start == -1:
    print("Error: Could not find <template>")
    exit(1)
    
new_template = """<template>
  <div class="api-monitor-page flex h-full w-full flex-col overflow-hidden bg-[#f5f7fb] text-[var(--text-primary)] dark:bg-[#101115] api-monitor-teal">
    <header class="relative flex-shrink-0 overflow-hidden">
      <!-- Background gradient matching ToolsPage -->
      <div class="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(255,255,255,0.22),transparent_32%),linear-gradient(115deg,#0ea5e9_0%,#0284c7_52%,#0369a1_100%)]"></div>
      <div class="absolute -right-16 -top-20 h-52 w-52 rounded-full bg-white/10 blur-3xl"></div>
      
      <div class="relative px-5 py-5 sm:px-7">
        <div class="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
          <div class="flex items-center gap-3">
            <button
              @click="goBack"
              class="flex h-10 w-10 items-center justify-center rounded-2xl border border-white/20 bg-white/15 text-white shadow-lg backdrop-blur transition hover:bg-white/25"
              title="返回"
            >
              <ArrowLeft :size="20" />
            </button>
            <div class="flex h-10 w-10 items-center justify-center rounded-2xl border border-white/20 bg-white/15 text-white shadow-lg backdrop-blur">
              <Globe :size="20" />
            </div>
            <div>
              <h1 class="text-2xl font-bold tracking-tight text-white">API 监控</h1>
              <p class="mt-1 text-sm text-white/70">实时捕获并生成 MCP 工具</p>
            </div>
          </div>

          <div class="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div class="relative w-full sm:w-[340px] xl:w-[400px]">
              <Globe class="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-white/60" />
              <input
                v-model="urlInput"
                type="text"
                placeholder="输入 URL 进行监控..."
                spellcheck="false"
                @keyup.enter="handleStartSession"
                class="w-full rounded-full border border-white/20 bg-slate-950/20 py-2 pl-10 pr-[70px] text-sm text-white caret-white placeholder:text-white/55 shadow-[inset_0_1px_4px_rgba(0,0,0,0.16)] outline-none backdrop-blur transition focus:border-white/45 focus:bg-slate-950/25 focus:ring-2 focus:ring-white/25 font-mono"
              >
              <button
                @click="handleStartSession"
                :disabled="loading"
                class="absolute right-1 top-1/2 -translate-y-1/2 rounded-full bg-white px-3 py-1 text-xs font-bold text-sky-700 shadow-sm transition hover:bg-sky-50 disabled:opacity-50"
              >
                Go
              </button>
            </div>

            <div class="flex items-center gap-2">
              <button
                @click="startAnalysis"
                :disabled="!sessionId || isAnalyzing"
                class="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-4 py-2 text-sm font-semibold text-white shadow-inner backdrop-blur transition hover:bg-white/20 disabled:opacity-50"
              >
                <BarChart2 :size="16" />
                分析
              </button>
              <button
                @click="toggleRecording"
                :disabled="!sessionId"
                class="inline-flex items-center gap-2 rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-white shadow-inner backdrop-blur transition disabled:opacity-50"
                :class="isRecording ? 'bg-red-500/80 hover:bg-red-500' : 'bg-white/10 hover:bg-white/20'"
              >
                <component :is="isRecording ? Square : Disc" :size="16" />
                {{ isRecording ? '停止' : '录制' }}
              </button>
              <button
                @click="openPublishDialog"
                :disabled="!sessionId || !tools.length || isPublishing"
                class="inline-flex items-center gap-2 rounded-full bg-white px-4 py-2 text-sm font-bold text-sky-700 shadow-lg transition hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-50 disabled:hover:translate-y-0"
              >
                <Save :size="16" />
                保存为 MCP
              </button>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- Main content: left browser viewport, right terminal + tools -->
    <div class="flex-1 flex overflow-hidden p-5 sm:px-7 pb-6 gap-5">
      <!-- Left: Browser viewport -->
      <section class="flex-1 flex flex-col relative rounded-3xl border border-slate-200/80 bg-white shadow-sm overflow-hidden dark:border-white/10 dark:bg-[#17181d]">
        <div class="flex-1 relative overflow-hidden flex items-center justify-center bg-slate-50 dark:bg-black/20">
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
            <Globe :size="60" class="opacity-30" />
            <p class="text-sm font-medium">输入 URL 并点击 Go 以开始监控</p>
          </div>

          <!-- Error overlay -->
          <div v-if="error && sessionId" class="absolute top-3 right-3 max-w-xs bg-red-100 dark:bg-red-900/80 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-300 text-xs px-3 py-2 rounded-xl shadow-lg backdrop-blur-sm">
            {{ error }}
          </div>

          <!-- Live indicator -->
          <div v-if="sessionId" class="absolute bottom-6 left-1/2 -translate-x-1/2 bg-white/90 dark:bg-[#1a1a1a]/90 backdrop-blur-md border border-slate-200 dark:border-white/10 px-4 py-1.5 rounded-full flex items-center gap-2 shadow-sm">
            <MonitorPlay :size="14" class="text-sky-500" />
            <span class="text-[var(--text-primary)] text-xs font-bold tracking-wider">实时视图</span>
          </div>
        </div>
        
        <!-- Status Bar -->
        <div class="h-10 border-t border-slate-100 dark:border-white/10 bg-white dark:bg-[#1a1a1a] flex items-center px-4 gap-4 text-xs text-[var(--text-secondary)] flex-shrink-0">
          <div class="flex items-center gap-1.5 font-medium">
            <span class="font-mono font-bold" :class="tools.length > 0 ? 'text-sky-500' : 'text-[var(--text-tertiary)]'">{{ tools.length }}</span> 个工具
          </div>
          <div class="w-px h-3 bg-slate-200 dark:bg-white/10"></div>
          <div class="flex items-center gap-1.5 font-medium">
            状态:
            <span :class="session?.status === 'recording' ? 'text-red-500 font-bold' : session?.status ? 'text-sky-500 font-bold' : 'text-[var(--text-tertiary)]'">
              {{ session?.status === 'recording' ? '录制中' : session?.status === 'analyzing' ? '分析中' : '空闲' }}
            </span>
          </div>
          <div v-if="isRecording" class="flex items-center gap-1.5 ml-2 bg-red-50 dark:bg-red-500/10 px-2 py-1 rounded-md border border-red-100 dark:border-red-500/20">
            <div class="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse"></div>
            <span class="text-red-600 dark:text-red-400 font-bold text-[10px]">REC</span>
          </div>
          <div class="flex-1"></div>
          <span v-if="sessionId" class="font-mono text-[10px] text-[var(--text-tertiary)]" title="Session ID">{{ sessionId.slice(0, 8) }}</span>
        </div>
      </section>

      <!-- Right: Terminal + Tools -->
      <section class="w-[450px] xl:w-[500px] flex-shrink-0 flex flex-col gap-5">
        <!-- Terminal log (top half) -->
        <div class="flex-1 flex flex-col min-h-0 rounded-3xl border border-slate-200/80 bg-white shadow-sm overflow-hidden dark:border-white/10 dark:bg-[#17181d]">
          <div class="h-10 flex items-center justify-between px-4 border-b border-slate-100 dark:border-white/10 bg-slate-50/50 dark:bg-white/[0.02] shrink-0">
            <div class="flex items-center gap-2 text-xs font-bold text-[var(--text-primary)]">
              <Terminal :size="14" class="text-sky-500" />
              监控日志
            </div>
            <button
              @click="clearLogs"
              class="text-[10px] font-bold tracking-wider text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors"
            >
              清除
            </button>
          </div>
          <div
            ref="terminalRef"
            class="flex-1 overflow-auto p-4 font-mono text-[11px] leading-relaxed bg-[#f8fafc] dark:bg-black/20 text-slate-800 dark:text-slate-300"
          >
            <div
              v-for="(line, idx) in terminalLines"
              :key="idx"
              v-html="line.html"
              class="mb-1 break-all"
            ></div>
            <div v-if="terminalLines.length === 0" class="text-[var(--text-tertiary)] italic">
              等待活动...
            </div>
          </div>
        </div>

        <!-- Tool cards (bottom half) -->
        <div class="flex-1 flex flex-col min-h-0 rounded-3xl border border-slate-200/80 bg-white shadow-sm overflow-hidden dark:border-white/10 dark:bg-[#17181d]">
          <div class="h-10 flex items-center px-4 border-b border-slate-100 dark:border-white/10 bg-slate-50/50 dark:bg-white/[0.02] shrink-0 gap-2">
            <Wrench :size="14" class="text-sky-500" />
            <h3 class="text-xs font-bold text-[var(--text-primary)]">检测到的工具</h3>
            <span class="px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-white/10 text-[var(--text-secondary)] font-mono text-[10px] font-bold leading-none ml-1">{{ tools.length }}</span>
          </div>
          <div class="flex-1 overflow-y-auto p-4 space-y-3 bg-white dark:bg-transparent">
            <!-- Empty state -->
            <div v-if="tools.length === 0" class="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
              <Wrench :size="40" class="mb-3 opacity-30" />
              <p class="text-sm font-medium text-[var(--text-secondary)] mb-1">尚未检测到工具</p>
              <p class="text-xs">点击“分析”或“录制”以发现 API 工具。</p>
            </div>

            <!-- Tool cards -->
            <div
              v-for="tool in tools"
              :key="tool.id"
              class="rounded-2xl border border-slate-200 bg-slate-50/80 shadow-sm overflow-hidden dark:border-white/10 dark:bg-white/[0.04]"
            >
              <!-- Collapsed view -->
              <div
                class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors"
                @click="toggleToolExpand(tool.id)"
              >
                <span
                  class="text-[10px] font-bold px-2 py-0.5 rounded-md"
                  :class="getMethodClass(tool.method)"
                >
                  {{ tool.method }}
                </span>
                <span class="text-[11px] font-mono text-[var(--text-primary)] flex-1 truncate">{{ tool.url_pattern }}</span>
                <ChevronDown :size="16" class="text-[var(--text-tertiary)] transition-transform" :class="expandedToolId === tool.id ? 'rotate-180' : ''" />
              </div>

              <!-- Expanded view -->
              <div v-if="expandedToolId === tool.id" class="border-t border-slate-100 dark:border-white/10 px-4 py-4 bg-white dark:bg-transparent">
                <p class="text-xs text-[var(--text-secondary)] mb-3 font-medium">{{ tool.description }}</p>
                <textarea
                  v-model="toolEdits[tool.id]"
                  class="w-full h-40 bg-[#f8fafc] dark:bg-black/20 border border-slate-200 dark:border-white/10 rounded-xl text-[11px] font-mono text-[var(--text-primary)] p-3 outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 resize-y transition-shadow"
                  spellcheck="false"
                ></textarea>
                <div class="flex justify-end gap-2 mt-3">
                  <button
                    @click="handleDeleteTool(tool.id)"
                    class="rounded-xl border border-red-200 px-3 py-1.5 text-xs font-bold text-red-600 transition hover:bg-red-50 dark:border-red-500/20 dark:text-red-400 dark:hover:bg-red-500/10"
                  >
                    删除
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
      class="fixed inset-0 z-[100] flex items-center justify-center px-4 py-6"
    >
      <div class="absolute inset-0 bg-slate-950/55 backdrop-blur-sm" @click="publishDialogOpen = false"></div>
      <div class="relative z-10 flex w-full max-w-md flex-col overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] shadow-2xl dark:border-white/10 dark:bg-[#101115]">
        <div class="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-5 dark:border-white/10 dark:bg-white/[0.055]">
          <div>
            <h2 class="text-xl font-black text-[var(--text-primary)]">保存为 MCP 工具</h2>
            <p class="mt-1 text-sm text-[var(--text-tertiary)]">将录制的 API 接口打包成 MCP</p>
          </div>
          <button
            class="rounded-xl p-2 text-[var(--text-tertiary)] transition hover:bg-slate-100 hover:text-[var(--text-primary)] dark:hover:bg-white/10"
            @click="publishDialogOpen = false"
          >
            <X :size="18" />
          </button>
        </div>
        <div class="space-y-4 p-6">
          <label class="flex flex-col gap-2">
            <span class="text-sm font-bold text-[var(--text-secondary)]">MCP 名称</span>
            <input
              v-model="publishForm.mcpName"
              class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5"
              type="text"
              placeholder="例如: 我的网站 API"
            />
          </label>
          <label class="flex flex-col gap-2">
            <span class="text-sm font-bold text-[var(--text-secondary)]">描述</span>
            <textarea
              v-model="publishForm.description"
              class="h-28 w-full resize-y rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5"
              placeholder="描述这些工具的功能..."
            ></textarea>
          </label>
        </div>
        <div class="flex justify-end gap-3 border-t border-slate-200 bg-white px-6 py-4 dark:border-white/10 dark:bg-white/[0.055]">
          <button
            class="rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/10"
            @click="publishDialogOpen = false"
          >
            取消
          </button>
          <button
            class="inline-flex items-center gap-2 rounded-xl bg-gradient-to-br from-[#0ea5e9] to-[#0284c7] px-5 py-2 text-sm font-bold text-white shadow-lg transition hover:-translate-y-0.5 active:translate-y-0 disabled:opacity-50 disabled:cursor-not-allowed"
            :disabled="isPublishing || !publishForm.mcpName.trim()"
            @click="submitPublish(false)"
          >
            保存
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="overwriteDialogOpen"
      class="fixed inset-0 z-[110] flex items-center justify-center px-4 py-6"
    >
      <div class="absolute inset-0 bg-slate-950/55 backdrop-blur-sm" @click="overwriteDialogOpen = false"></div>
      <div class="relative z-10 flex w-full max-w-md flex-col overflow-hidden rounded-3xl border border-amber-200 bg-[#f5f7fb] shadow-2xl dark:border-amber-900/50 dark:bg-[#101115]">
        <div class="flex items-center justify-between gap-4 border-b border-amber-200 bg-white px-6 py-5 dark:border-amber-900/50 dark:bg-white/[0.055]">
          <div class="flex items-center gap-3">
            <AlertTriangle :size="24" class="text-amber-500" />
            <h2 class="text-lg font-black text-[var(--text-primary)]">替换现有的 MCP 工具？</h2>
          </div>
        </div>
        <div class="p-6">
          <p class="text-sm leading-relaxed text-[var(--text-secondary)]">
            名为 <span class="font-bold text-[var(--text-primary)]">"{{ publishForm.mcpName }}"</span> 的 MCP 已存在。替换它将使用当前的 API 监控结果覆盖该 MCP 下的所有工具。
          </p>
        </div>
        <div class="flex justify-end gap-3 border-t border-amber-200 bg-white px-6 py-4 dark:border-amber-900/50 dark:bg-white/[0.055]">
          <button
            class="rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/10"
            @click="overwriteDialogOpen = false"
          >
            取消
          </button>
          <button
            class="inline-flex items-center gap-2 rounded-xl bg-amber-500 px-5 py-2 text-sm font-bold text-white shadow-lg transition hover:bg-amber-600 disabled:opacity-50"
            :disabled="isPublishing"
            @click="submitPublish(true)"
          >
            替换工具
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
"""

with open('RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue', 'w', encoding='utf-8') as f:
    f.write(content[:template_start] + new_template)
