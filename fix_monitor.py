import re

with open('RpaClaw/frontend/src/pages/rpa/ApiMonitorPage.vue', 'r', encoding='utf-8') as f:
    content = f.read()

# Modify the script to include the lucide icons
script_pattern = r'(<script setup lang="ts">)'
icons_import = """\\1
import { ArrowLeft, Globe, BarChart2, Disc, Square, Save, Wrench, ChevronDown, MonitorPlay, X, AlertTriangle, Workflow, LayoutDashboard, Package, Settings, BookOpen, Terminal } from 'lucide-vue-next';"""

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

# Modify the template
template_start = content.find('<template>')
new_template = """<template>
  <div class="flex h-full w-full bg-[#f8f9fb] dark:bg-[#111] text-[var(--text-primary)] overflow-hidden api-monitor-teal">
    <!-- BEGIN: SideNavBar -->
    <nav class="w-[240px] h-full bg-white dark:bg-[#1a1a1a] border-r border-gray-200 dark:border-gray-800 flex flex-col flex-shrink-0 z-50 relative">
      <!-- Header -->
      <div class="h-14 flex items-center px-4 border-b border-gray-200 dark:border-gray-800 shrink-0">
        <div class="flex items-center gap-3">
          <div class="w-7 h-7 rounded bg-sky-500/20 flex items-center justify-center text-sky-500">
            <Workflow :size="18" />
          </div>
          <div>
            <h1 class="text-sm font-semibold text-[var(--text-primary)] tracking-tight leading-tight">MCP 管理器</h1>
            <p class="text-[10px] text-[var(--text-tertiary)] leading-tight">工程精度</p>
          </div>
        </div>
      </div>
      <!-- Main Navigation -->
      <div class="flex-1 py-3 overflow-y-auto">
        <ul class="space-y-1 px-2">
          <li>
            <a class="flex items-center gap-3 px-3 py-2 rounded-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors group cursor-not-allowed opacity-60">
              <LayoutDashboard :size="16" class="group-hover:text-sky-500 transition-colors" />
              <span class="text-xs font-medium">仪表盘</span>
            </a>
          </li>
          <li>
            <a class="flex items-center gap-3 px-3 py-2 rounded-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors group cursor-not-allowed opacity-60">
              <Package :size="16" class="group-hover:text-sky-500 transition-colors" />
              <span class="text-xs font-medium">工具清单</span>
            </a>
          </li>
          <li>
            <!-- Active State -->
            <a class="flex items-center gap-3 px-3 py-2 rounded-md bg-sky-50 dark:bg-sky-900/20 text-sky-600 dark:text-sky-400 font-semibold relative after:absolute after:left-0 after:top-1/2 after:-translate-y-1/2 after:h-4 after:w-1 after:bg-sky-500 after:rounded-r-full">
              <Disc :size="16" />
              <span class="text-xs">API 录制</span>
            </a>
          </li>
          <li>
            <a class="flex items-center gap-3 px-3 py-2 rounded-md text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors group cursor-not-allowed opacity-60">
              <Settings :size="16" class="group-hover:text-sky-500 transition-colors" />
              <span class="text-xs font-medium">设置</span>
            </a>
          </li>
        </ul>
      </div>
      <!-- Footer Navigation -->
      <div class="p-3 border-t border-gray-200 dark:border-gray-800 shrink-0">
        <ul class="space-y-1">
          <li>
            <a class="flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors cursor-not-allowed opacity-60">
              <BookOpen :size="14" />
              文档
            </a>
          </li>
          <li>
            <a class="flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors cursor-not-allowed opacity-60">
              <Terminal :size="14" />
              系统日志
            </a>
          </li>
        </ul>
      </div>
    </nav>
    <!-- END: SideNavBar -->

    <!-- BEGIN: Main Application Area -->
    <main class="flex-1 flex flex-col h-full overflow-hidden bg-white dark:bg-[#1a1a1a] relative z-0">
      <!-- Top control bar -->
      <header class="h-14 flex-shrink-0 bg-white dark:bg-[#1a1a1a] border-b border-gray-200 dark:border-gray-800 flex items-center px-4 gap-3 z-50">
        <button
          @click="goBack"
          class="flex items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors text-sm font-medium px-2 py-1.5 rounded-md hover:bg-gray-100 dark:hover:bg-gray-800"
        >
          <ArrowLeft :size="18" />
          返回
        </button>

        <div class="w-px h-6 bg-gray-200 dark:bg-gray-700"></div>

        <h1 class="text-[var(--text-primary)] font-bold text-base whitespace-nowrap">API 监控</h1>

        <div class="flex-1 flex items-center justify-center">
          <div class="w-full max-w-2xl flex items-center gap-2">
            <div class="relative flex-1 flex items-center">
              <Globe :size="14" class="absolute left-3 text-[var(--text-tertiary)]" />
              <input
                v-model="urlInput"
                class="w-full bg-gray-50 dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 rounded-lg py-1.5 pl-9 pr-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] focus:outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/50 transition-shadow font-mono"
                placeholder="输入 URL 进行监控..."
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
            <BarChart2 :size="16" />
            分析
          </button>
          <button
            @click="toggleRecording"
            :disabled="!sessionId"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
            :class="isRecording
              ? 'bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 border border-red-200 dark:border-red-800/50 hover:bg-red-100 dark:hover:bg-red-900/40'
              : 'bg-white dark:bg-[#1e1e1e] border border-gray-200 dark:border-gray-700 text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-gray-300 dark:hover:border-gray-600'"
          >
            <component :is="isRecording ? Square : Disc" :size="16" />
            {{ isRecording ? '停止' : '录制' }}
          </button>
          <button
            @click="openPublishDialog"
            :disabled="!sessionId || !tools.length || isPublishing"
            class="flex items-center gap-1.5 px-3 py-1.5 bg-sky-50 dark:bg-sky-900/20 border border-sky-200 dark:border-sky-800/50 rounded-lg text-sm font-medium text-sky-600 dark:text-sky-400 hover:bg-sky-100 dark:hover:bg-sky-900/40 transition-colors disabled:opacity-50 ml-1"
          >
            <Save :size="16" />
            保存为 MCP 工具
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
              <Globe :size="80" class="opacity-30" />
              <p class="text-sm font-medium">输入 URL 并点击 Go 以开始监控</p>
            </div>

            <!-- Error overlay -->
            <div v-if="error && sessionId" class="absolute top-3 right-3 max-w-xs bg-red-100 dark:bg-red-900/80 border border-red-200 dark:border-red-800 text-red-600 dark:text-red-300 text-xs px-3 py-2 rounded-lg shadow-lg backdrop-blur-sm">
              {{ error }}
            </div>

            <!-- Live indicator -->
            <div v-if="sessionId" class="absolute bottom-10 left-1/2 -translate-x-1/2 bg-white/80 dark:bg-black/50 backdrop-blur-md border border-gray-200 dark:border-gray-700 px-3 py-1 rounded-full flex items-center gap-2 shadow-sm">
              <MonitorPlay :size="14" class="text-sky-500" />
              <span class="text-[var(--text-primary)] text-[10px] font-bold tracking-wider uppercase">实时视图</span>
            </div>
          </div>
          
          <!-- Status Bar inside left panel to match DOM -->
          <div class="h-8 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-[#1a1a1a] flex items-center px-4 gap-4 text-xs text-[var(--text-secondary)] flex-shrink-0">
            <div class="flex items-center gap-1.5">
              <span class="font-mono font-bold" :class="tools.length > 0 ? 'text-sky-500' : 'text-[var(--text-tertiary)]'">{{ tools.length }}</span> 个工具
            </div>
            <div class="w-px h-3 bg-gray-300 dark:bg-gray-700"></div>
            <div class="flex items-center gap-1.5">
              状态:
              <span :class="session?.status === 'recording' ? 'text-red-500 font-medium' : session?.status ? 'text-sky-500 font-medium' : 'text-[var(--text-tertiary)]'">
                {{ session?.status === 'recording' ? '录制中' : session?.status === 'active' ? '活动' : '空闲' }}
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
                清除
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
                等待活动...
              </div>
            </div>
          </div>

          <!-- Tool cards (bottom half) -->
          <div class="flex-1 flex flex-col min-h-0 bg-white dark:bg-[#1a1a1a]">
            <div class="h-10 flex items-center px-3 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-[#161616] shrink-0 gap-2">
              <Wrench :size="14" class="text-sky-500" />
              <h3 class="text-sm font-medium text-[var(--text-primary)]">检测到的工具</h3>
              <span class="px-1.5 py-0.5 rounded-sm bg-gray-200 dark:bg-gray-800 text-[var(--text-primary)] font-mono text-[10px] leading-none ml-1">{{ tools.length }}</span>
            </div>
            <div class="flex-1 overflow-y-auto p-3 space-y-2 bg-[#f8f9fb] dark:bg-[#111]">
              <!-- Empty state -->
              <div v-if="tools.length === 0" class="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
                <Wrench :size="48" class="mb-3 opacity-40" />
                <p class="text-sm font-medium text-[var(--text-secondary)] mb-1">尚未检测到工具。</p>
                <p class="text-xs">点击“分析”或“录制”以发现 API 工具。</p>
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
                  <ChevronDown :size="16" class="text-[var(--text-tertiary)] transition-transform" :class="expandedToolId === tool.id ? 'rotate-180' : ''" />
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
                      删除
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>

    <!-- Modals -->
    <div
      v-if="publishDialogOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-sm px-4"
    >
      <div class="w-full max-w-md rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1e1e1e] p-5 shadow-2xl">
        <div class="flex items-center justify-between">
          <h2 class="text-base font-bold text-[var(--text-primary)]">保存为 MCP 工具</h2>
          <button
            class="text-[var(--text-tertiary)] hover:text-[var(--text-primary)] transition-colors p-1"
            @click="publishDialogOpen = false"
          >
            <X :size="18" />
          </button>
        </div>
        <label class="mt-4 block text-xs font-semibold text-[var(--text-secondary)]">
          MCP 名称
          <input
            v-model="publishForm.mcpName"
            class="mt-1.5 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#161616] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 transition-shadow shadow-sm"
            type="text"
            placeholder="例如: 我的网站 API"
          />
        </label>
        <label class="mt-4 block text-xs font-semibold text-[var(--text-secondary)]">
          描述
          <textarea
            v-model="publishForm.description"
            class="mt-1.5 h-24 w-full resize-y rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#161616] px-3 py-2 text-sm text-[var(--text-primary)] outline-none focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 transition-shadow shadow-sm"
            placeholder="描述这些工具的功能..."
          ></textarea>
        </label>
        <div class="mt-6 flex justify-end gap-2">
          <button
            class="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            @click="publishDialogOpen = false"
          >
            取消
          </button>
          <button
            class="rounded-lg bg-sky-500 px-4 py-2 text-sm font-medium text-white hover:bg-sky-600 disabled:opacity-50 transition-colors shadow-sm"
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
      class="fixed inset-0 z-[110] flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-sm px-4"
    >
      <div class="w-full max-w-md rounded-xl border border-red-200 dark:border-red-900/50 bg-white dark:bg-[#1e1e1e] p-5 shadow-2xl">
        <h2 class="text-base font-bold text-[var(--text-primary)] flex items-center gap-2">
          <AlertTriangle :size="18" class="text-amber-500" />
          替换现有的 MCP 工具？
        </h2>
        <p class="mt-3 text-sm leading-relaxed text-[var(--text-secondary)]">
          名为 <span class="font-semibold text-[var(--text-primary)]">"{{ publishForm.mcpName }}"</span> 的 MCP 已存在。替换它将使用当前的 API 监控结果覆盖该 MCP 下的所有工具。
        </p>
        <div class="mt-6 flex justify-end gap-2">
          <button
            class="rounded-lg border border-gray-200 dark:border-gray-700 px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors"
            @click="overwriteDialogOpen = false"
          >
            取消
          </button>
          <button
            class="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50 transition-colors shadow-sm"
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
