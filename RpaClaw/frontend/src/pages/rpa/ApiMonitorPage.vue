<script setup lang="ts">
import { ref, reactive, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import {
  startSession,
  stopSession,
  analyzeSession,
  startRecording as apiStartRecording,
  stopRecording as apiStopRecording,
  listTools,
  updateTool as apiUpdateTool,
  deleteTool as apiDeleteTool,
  publishMcpToolBundle,
  type ApiMonitorSession,
  type ApiToolDefinition,
} from '@/api/apiMonitor';
import { getBackendWsUrl } from '@/utils/sandbox';
import {
  getFrameSizeFromMetadata,
  getInputSizeFromMetadata,
  mapClientPointToViewportPoint,
  type ScreencastFrameMetadata,
  type ScreencastSize,
} from '@/utils/screencastGeometry';

const router = useRouter();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const sessionId = ref<string>('');
const session = ref<ApiMonitorSession | null>(null);
const urlInput = ref('https://');
const tools = ref<ApiToolDefinition[]>([]);
const terminalLines = ref<{ html: string }[]>([]);
const isRecording = ref(false);
const isAnalyzing = ref(false);
const expandedToolId = ref<string | null>(null);
const toolEdits = reactive<Record<string, string>>({});
const loading = ref(false);
const error = ref<string | null>(null);
const publishDialogOpen = ref(false);
const overwriteDialogOpen = ref(false);
const isPublishing = ref(false);
const publishForm = reactive({
  mcpName: '',
  description: '',
});

// Screencast
let screencastWs: WebSocket | null = null;
const canvasRef = ref<HTMLCanvasElement | null>(null);
const screencastFrameSize = ref<ScreencastSize>({ width: 1280, height: 720 });
const screencastInputSize = ref<ScreencastSize>({ width: 1280, height: 720 });
let shouldReconnectScreencast = true;
let currentScreencastSessionId: string | null = null;
let lastMoveTime = 0;
const MOVE_THROTTLE = 50;

// Terminal scroll
const terminalRef = ref<HTMLDivElement | null>(null);

// ---------------------------------------------------------------------------
// Terminal log
// ---------------------------------------------------------------------------

type LogLevel = 'INFO' | 'RECV' | 'ANALYZE' | 'BUILD' | 'ERROR';

const LOG_COLORS: Record<LogLevel, string> = {
  INFO: 'text-[#57f1db]',
  RECV: 'text-[#bacac5]',
  ANALYZE: 'text-[#7ecfcf]',
  BUILD: 'text-[#57f1db]',
  ERROR: 'text-[#ffb4ab]',
};

const addLog = (level: LogLevel, message: string) => {
  const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const colorClass = LOG_COLORS[level] || 'text-[#dae2fd]';
  const escaped = message
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  terminalLines.value.push({
    html: `<span class="text-[#5a6a65]">${ts}</span> <span class="font-bold ${colorClass}">[${level}]</span> <span class="${colorClass}">${escaped}</span>`,
  });
  nextTick(() => {
    if (terminalRef.value) {
      terminalRef.value.scrollTop = terminalRef.value.scrollHeight;
    }
  });
};

const clearLogs = () => {
  terminalLines.value = [];
};

// ---------------------------------------------------------------------------
// Screencast (same pattern as RecorderPage.vue)
// ---------------------------------------------------------------------------

const getModifiers = (e: MouseEvent | KeyboardEvent | WheelEvent): number => {
  let mask = 0;
  if (e.altKey) mask |= 1;
  if (e.ctrlKey) mask |= 2;
  if (e.metaKey) mask |= 4;
  if (e.shiftKey) mask |= 8;
  return mask;
};

const drawFrame = (base64Data: string, metadata: ScreencastFrameMetadata) => {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const img = new Image();
  img.onload = () => {
    const nextFrameSize = getFrameSizeFromMetadata(metadata, {
      width: img.naturalWidth,
      height: img.naturalHeight,
    });
    const nextInputSize = getInputSizeFromMetadata(metadata, nextFrameSize);
    screencastFrameSize.value = nextFrameSize;
    screencastInputSize.value = nextInputSize;

    if (canvas.width !== nextFrameSize.width) canvas.width = nextFrameSize.width;
    if (canvas.height !== nextFrameSize.height) canvas.height = nextFrameSize.height;
    ctx.drawImage(img, 0, 0);
  };
  img.src = `data:image/jpeg;base64,${base64Data}`;
};

const focusCanvas = () => {
  canvasRef.value?.focus();
};

const sendInputEvent = (e: Event) => {
  if (!screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  const canvas = canvasRef.value;
  if (!canvas) return;

  if (e instanceof MouseEvent && !(e instanceof WheelEvent)) {
    if (e.type === 'mousemove') {
      const now = Date.now();
      if (now - lastMoveTime < MOVE_THROTTLE) return;
      lastMoveTime = now;
    }

    const rect = canvas.getBoundingClientRect();
    const point = mapClientPointToViewportPoint({
      clientX: e.clientX,
      clientY: e.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    });
    if (!point) return;
    const actionMap: Record<string, string> = {
      mousedown: 'mousePressed',
      mouseup: 'mouseReleased',
      mousemove: 'mouseMoved',
    };
    const action = actionMap[e.type];
    if (!action) return;
    const buttonMap = ['left', 'middle', 'right'];
    screencastWs.send(JSON.stringify({
      type: 'mouse',
      action,
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      button: buttonMap[e.button] || 'left',
      clickCount: e.type === 'mousedown' ? 1 : 0,
      modifiers: getModifiers(e),
    }));
  } else if (e instanceof WheelEvent) {
    const rect = canvas.getBoundingClientRect();
    const point = mapClientPointToViewportPoint({
      clientX: e.clientX,
      clientY: e.clientY,
      containerRect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
      frameSize: screencastFrameSize.value,
      inputSize: screencastInputSize.value,
    });
    if (!point) return;
    screencastWs.send(JSON.stringify({
      type: 'wheel',
      coordinateSpace: 'css-pixel',
      x: point.x,
      y: point.y,
      deltaX: e.deltaX,
      deltaY: e.deltaY,
      modifiers: getModifiers(e),
    }));
  } else if (e instanceof KeyboardEvent) {
    const action = e.type === 'keydown' ? 'keyDown' : 'keyUp';
    screencastWs.send(JSON.stringify({
      type: 'keyboard',
      action,
      key: e.key,
      code: e.code,
      text: e.type === 'keydown' && e.key.length === 1 ? e.key : '',
      modifiers: getModifiers(e),
    }));
  }
};

const disconnectScreencast = () => {
  if (!screencastWs) return;
  const ws = screencastWs;
  screencastWs = null;
  ws.onopen = null;
  ws.onmessage = null;
  ws.onerror = null;
  ws.onclose = null;
  if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
    ws.close();
  }
};

const connectScreencast = (sid: string) => {
  currentScreencastSessionId = sid;
  if (screencastWs) {
    disconnectScreencast();
  }
  const wsUrl = getBackendWsUrl(`/api-monitor/screencast/${sid}`);
  console.log('[ApiMonitorPage] Connecting screencast:', wsUrl);
  const ws = new WebSocket(wsUrl);
  screencastWs = ws;

  ws.onopen = () => {
    if (screencastWs !== ws) return;
    console.log('[ApiMonitorPage] Screencast connected');
    addLog('INFO', 'Screencast connected');
  };

  ws.onmessage = (ev) => {
    if (screencastWs !== ws) return;
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'frame') {
        drawFrame(msg.data, msg.metadata);
      } else if (msg.type === 'preview_error') {
        error.value = msg.message || 'Screencast error';
      }
    } catch (parseError) {
      console.error('[ApiMonitorPage] Screencast parse error:', parseError);
    }
  };

  ws.onclose = (ev) => {
    if (screencastWs !== ws) return;
    console.warn('[ApiMonitorPage] Screencast closed:', ev.code, ev.reason);
    screencastWs = null;
    if (!shouldReconnectScreencast) return;
    // Simple reconnect after 2s
    setTimeout(() => {
      if (shouldReconnectScreencast && currentScreencastSessionId) {
        connectScreencast(currentScreencastSessionId);
      }
    }, 2000);
  };

  ws.onerror = (ev) => {
    if (screencastWs !== ws) return;
    console.error('[ApiMonitorPage] Screencast error:', ev);
  };
};

// ---------------------------------------------------------------------------
// Session lifecycle
// ---------------------------------------------------------------------------

const goBack = () => {
  router.push('/chat/tools');
};

const handleStartSession = async () => {
  const url = urlInput.value.trim();
  if (!url || url === 'https://') return;

  loading.value = true;
  error.value = null;
  try {
    addLog('INFO', `Starting session: ${url}`);
    const s = await startSession(url);
    sessionId.value = s.id;
    session.value = s;
    addLog('INFO', `Session created: ${s.id}`);
    connectScreencast(s.id);
    // Load any existing tools
    const existingTools = await listTools(s.id);
    tools.value = existingTools;
    if (existingTools.length > 0) {
      addLog('INFO', `Loaded ${existingTools.length} existing tools`);
    }
  } catch (err: any) {
    console.error('Failed to start API monitor session:', err);
    error.value = err.response?.data?.detail || 'Failed to start session';
    addLog('ERROR', `Failed to start session: ${error.value}`);
  } finally {
    loading.value = false;
  }
};

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------

const startAnalysis = async () => {
  if (!sessionId.value) return;
  isAnalyzing.value = true;
  addLog('INFO', 'Starting analysis...');

  const cleanup = analyzeSession(sessionId.value, (evt) => {
    let data: any;
    try { data = typeof evt.data === 'string' ? JSON.parse(evt.data) : evt.data; } catch { data = evt.data; }
    switch (evt.event) {
      case 'analysis_started':
        addLog('INFO', `Analyzing: ${data.url}`);
        break;
      case 'progress':
        if (data.step === 'scanning') {
          addLog('INFO', data.message);
        } else if (data.step === 'classifying') {
          addLog('ANALYZE', data.message);
        } else if (data.step === 'probing') {
          addLog('ANALYZE', `${data.message} (${data.current}/${data.total})`);
        } else if (data.step === 'generating') {
          addLog('BUILD', data.message);
        } else {
          addLog('INFO', data.message || 'Processing...');
        }
        break;
      case 'elements_found':
        addLog('INFO', `Found ${data.count} interactive elements`);
        break;
      case 'elements_classified':
        addLog('ANALYZE', `Classified: ${data.safe} safe, ${data.skipped} skipped`);
        break;
      case 'calls_captured':
        addLog('RECV', `Captured ${data.calls} API calls from element ${data.element_index}`);
        break;
      case 'analysis_complete':
        addLog('INFO', `Analysis complete: ${data.tools_generated} tools, ${data.total_calls} calls`);
        isAnalyzing.value = false;
        // Refresh tools from session
        listTools(sessionId.value).then((t) => { tools.value = t; }).catch(() => {});
        cleanup();
        break;
      case 'analysis_error':
        addLog('ERROR', data.error);
        isAnalyzing.value = false;
        cleanup();
        break;
    }
  });
};

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------

const toggleRecording = async () => {
  if (!sessionId.value) return;

  if (isRecording.value) {
    try {
      addLog('INFO', 'Stopping recording...');
      const newTools = await apiStopRecording(sessionId.value);
      isRecording.value = false;
      addLog('INFO', `Recording stopped. ${newTools.length} tools generated.`);
      // Refresh tools list
      tools.value = await listTools(sessionId.value);
    } catch (err: any) {
      addLog('ERROR', `Failed to stop recording: ${err.message}`);
    }
  } else {
    try {
      addLog('INFO', 'Starting recording...');
      await apiStartRecording(sessionId.value);
      isRecording.value = true;
      addLog('INFO', 'Recording started. Interact with the browser to capture API calls.');
    } catch (err: any) {
      addLog('ERROR', `Failed to start recording: ${err.message}`);
    }
  }
};

// ---------------------------------------------------------------------------
// Tool management
// ---------------------------------------------------------------------------

const toggleToolExpand = (toolId: string) => {
  if (expandedToolId.value === toolId) {
    expandedToolId.value = null;
  } else {
    expandedToolId.value = toolId;
    if (!toolEdits[toolId]) {
      const tool = tools.value.find((t) => t.id === toolId);
      if (tool) {
        toolEdits[toolId] = tool.yaml_definition;
      }
    }
  }
};

const saveToolEdit = async (toolId: string) => {
  if (!sessionId.value) return;
  const yaml = toolEdits[toolId];
  if (yaml === undefined) return;
  const current = tools.value.find((t) => t.id === toolId);
  if (current?.yaml_definition === yaml) return;
  const updated = await apiUpdateTool(sessionId.value, toolId, yaml);
  const idx = tools.value.findIndex((t) => t.id === toolId);
  if (idx >= 0) {
    tools.value[idx] = updated;
  }
};

const flushToolEdits = async () => {
  const editedToolIds = Object.keys(toolEdits);
  for (const toolId of editedToolIds) {
    await saveToolEdit(toolId);
  }
};

const handleDeleteTool = async (toolId: string) => {
  if (!sessionId.value) return;
  try {
    addLog('INFO', `Deleting tool: ${toolId}`);
    await apiDeleteTool(sessionId.value, toolId);
    tools.value = tools.value.filter((t) => t.id !== toolId);
    delete toolEdits[toolId];
    if (expandedToolId.value === toolId) {
      expandedToolId.value = null;
    }
    addLog('INFO', `Tool deleted`);
  } catch (err: any) {
    addLog('ERROR', `Failed to delete tool: ${err.message}`);
  }
};

// ---------------------------------------------------------------------------
// Publish as MCP
// ---------------------------------------------------------------------------

const getDefaultMcpName = () => {
  const target = session.value?.target_url || urlInput.value;
  try {
    const host = new URL(target).hostname;
    return host ? `${host} API MCP` : 'API Monitor MCP';
  } catch {
    return 'API Monitor MCP';
  }
};

const openPublishDialog = () => {
  if (!sessionId.value || !tools.value.length) return;
  publishForm.mcpName = publishForm.mcpName || getDefaultMcpName();
  publishForm.description = publishForm.description || session.value?.target_url || urlInput.value || '';
  publishDialogOpen.value = true;
};

const submitPublish = async (confirmOverwrite = false) => {
  if (!sessionId.value || !tools.value.length || !publishForm.mcpName.trim()) return;
  isPublishing.value = true;
  try {
    addLog('INFO', 'Publishing MCP tools...');
    await flushToolEdits();
    const result = await publishMcpToolBundle(sessionId.value, {
      mcp_name: publishForm.mcpName.trim(),
      description: publishForm.description.trim(),
      confirm_overwrite: confirmOverwrite,
    });
    publishDialogOpen.value = false;
    overwriteDialogOpen.value = false;
    addLog('INFO', `Saved MCP "${publishForm.mcpName}" with ${result.tool_count} tools`);
  } catch (err: any) {
    if (err?.response?.status === 409 && err?.response?.data?.needs_confirmation) {
      overwriteDialogOpen.value = true;
      addLog('INFO', 'Existing MCP found. Waiting for overwrite confirmation.');
      return;
    }
    addLog('ERROR', `Failed to save MCP: ${err.message}`);
  } finally {
    isPublishing.value = false;
  }
};

// ---------------------------------------------------------------------------
// Method badge colors
// ---------------------------------------------------------------------------

const methodColors: Record<string, string> = {
  GET: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  POST: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  PUT: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  DELETE: 'bg-red-500/20 text-red-400 border-red-500/30',
  PATCH: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
};

const getMethodClass = (method: string) => methodColors[method.toUpperCase()] || 'bg-gray-500/20 text-gray-400 border-gray-500/30';

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  addLog('INFO', 'API Monitor ready. Enter a URL and click Go to start.');
});

onBeforeUnmount(() => {
  shouldReconnectScreencast = false;
  disconnectScreencast();
  if (sessionId.value) {
    stopSession(sessionId.value).catch(() => {});
  }
});
</script>

<template>
  <div class="flex flex-col h-screen bg-[#0b1326] overflow-hidden text-[#dae2fd]">
    <!-- Top control bar -->
    <header class="h-14 flex-shrink-0 bg-[#0b1326] border-b border-[#3c4a46] flex items-center px-4 gap-3 z-50">
      <button
        @click="goBack"
        class="flex items-center gap-1 text-[#bacac5] hover:text-[#57f1db] transition-colors text-sm font-medium"
      >
        <span class="material-symbols-outlined text-lg">arrow_back</span>
        Back
      </button>

      <div class="w-px h-6 bg-[#3c4a46]"></div>

      <h1 class="text-[#dae2fd] font-bold text-base whitespace-nowrap">API Monitor</h1>

      <div class="flex-1 flex items-center gap-2 ml-2">
        <div class="flex-1 max-w-xl flex items-center bg-[#0f1d30] border border-[#3c4a46] rounded-lg overflow-hidden focus-within:border-[#57f1db]/50 transition-colors">
          <span class="material-symbols-outlined text-[#5a6a65] text-base ml-2">language</span>
          <input
            v-model="urlInput"
            class="flex-1 bg-transparent text-sm text-[#dae2fd] px-2 py-1.5 outline-none placeholder:text-[#5a6a65]"
            placeholder="Enter URL to monitor..."
            type="text"
            spellcheck="false"
            @keyup.enter="handleStartSession"
          />
        </div>
        <button
          @click="handleStartSession"
          :disabled="loading"
          class="px-3 py-1.5 bg-[#57f1db]/20 text-[#57f1db] border border-[#57f1db]/30 rounded-lg text-sm font-medium hover:bg-[#57f1db]/30 transition-colors disabled:opacity-50 whitespace-nowrap"
        >
          Go
        </button>
      </div>

      <div class="flex items-center gap-2">
        <button
          @click="startAnalysis"
          :disabled="!sessionId || isAnalyzing"
          class="px-3 py-1.5 bg-[#57f1db]/20 text-[#57f1db] border border-[#57f1db]/30 rounded-lg text-sm font-medium hover:bg-[#57f1db]/30 transition-colors disabled:opacity-50 whitespace-nowrap flex items-center gap-1"
        >
          <span class="material-symbols-outlined text-base">search_insights</span>
          Analyze
        </button>
        <button
          @click="toggleRecording"
          :disabled="!sessionId"
          class="px-3 py-1.5 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 whitespace-nowrap flex items-center gap-1"
          :class="isRecording
            ? 'bg-[#ffb4ab]/20 text-[#ffb4ab] border border-[#ffb4ab]/30 hover:bg-[#ffb4ab]/30'
            : 'bg-[#57f1db]/20 text-[#57f1db] border border-[#57f1db]/30 hover:bg-[#57f1db]/30'"
        >
          <span class="material-symbols-outlined text-base">{{ isRecording ? 'stop' : 'fiber_manual_record' }}</span>
          {{ isRecording ? 'Stop' : 'Record' }}
        </button>
        <button
          @click="openPublishDialog"
          :disabled="!sessionId || !tools.length || isPublishing"
          class="px-3 py-1.5 bg-[#57f1db]/20 text-[#57f1db] border border-[#57f1db]/30 rounded-lg text-sm font-medium hover:bg-[#57f1db]/30 transition-colors disabled:opacity-50 whitespace-nowrap flex items-center gap-1"
        >
          <span class="material-symbols-outlined text-base">save</span>
          Save as MCP Tool
        </button>
      </div>
    </header>

    <!-- Main content: left 50% browser viewport, right 50% terminal + tools -->
    <div class="flex-1 flex overflow-hidden">
      <!-- Left: Browser viewport -->
      <div class="w-1/2 border-r border-[#3c4a46] flex flex-col">
        <div class="flex-1 bg-black relative overflow-hidden">
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
            class="absolute inset-0 flex items-center justify-center flex-col gap-4 text-[#5a6a65]"
          >
            <span class="material-symbols-outlined text-[80px] opacity-20">language</span>
            <p class="text-sm font-medium">Enter a URL and click Go to start monitoring</p>
          </div>

          <!-- Error overlay -->
          <div v-if="error && sessionId" class="absolute top-3 right-3 max-w-xs bg-[#ffb4ab]/90 text-[#0b1326] text-xs px-3 py-2 rounded-lg shadow-lg">
            {{ error }}
          </div>

          <!-- Live indicator -->
          <div v-if="sessionId" class="absolute bottom-3 left-1/2 -translate-x-1/2 bg-white/10 backdrop-blur-md border border-white/20 px-3 py-1 rounded-full flex items-center gap-2">
            <span class="material-symbols-outlined text-[#57f1db] text-sm">monitor</span>
            <span class="text-white text-[10px] font-bold tracking-wider uppercase">Live Viewport</span>
          </div>
        </div>
      </div>

      <!-- Right: Terminal + Tools -->
      <div class="w-1/2 flex flex-col overflow-hidden">
        <!-- Terminal log (top half) -->
        <div class="h-1/2 flex flex-col border-b border-[#3c4a46]">
          <!-- macOS-style title bar -->
          <div class="h-8 bg-[#0f1d30] flex items-center px-3 flex-shrink-0 border-b border-[#3c4a46]">
            <div class="flex gap-1.5">
              <div class="w-2.5 h-2.5 rounded-full bg-[#ffb4ab]"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-[#f5d76e]"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-[#57f1db]"></div>
            </div>
            <span class="ml-3 text-[10px] text-[#5a6a65] font-mono">api-monitor.log</span>
            <div class="flex-1"></div>
            <button
              @click="clearLogs"
              class="text-[10px] text-[#5a6a65] hover:text-[#bacac5] transition-colors"
            >
              Clear
            </button>
          </div>
          <!-- Scrollable log area -->
          <div
            ref="terminalRef"
            class="flex-1 overflow-y-auto bg-[#0b1326] p-3 space-y-0.5"
          >
            <div
              v-for="(line, idx) in terminalLines"
              :key="idx"
              class="font-mono text-[11px] leading-relaxed"
              v-html="line.html"
            ></div>
            <div v-if="terminalLines.length === 0" class="text-[#5a6a65] text-xs font-mono italic">
              Waiting for activity...
            </div>
          </div>
        </div>

        <!-- Tool cards (bottom half) -->
        <div class="h-1/2 flex flex-col overflow-hidden">
          <div class="h-8 bg-[#0f1d30] flex items-center px-3 flex-shrink-0 border-b border-[#3c4a46]">
            <span class="material-symbols-outlined text-[#57f1db] text-sm mr-1">build</span>
            <span class="text-[10px] text-[#bacac5] font-medium">Detected Tools</span>
            <span class="ml-2 text-[10px] text-[#57f1db] font-bold bg-[#57f1db]/10 px-1.5 py-0.5 rounded">{{ tools.length }}</span>
          </div>
          <div class="flex-1 overflow-y-auto bg-[#0b1326] p-3 space-y-2">
            <!-- Empty state -->
            <div v-if="tools.length === 0" class="h-full flex items-center justify-center">
              <div class="text-center text-[#5a6a65]">
                <span class="material-symbols-outlined text-4xl opacity-30 mb-2 block">build_circle</span>
                <p class="text-xs">No tools detected yet.</p>
                <p class="text-[10px] mt-1">Click Analyze or Record to discover API tools.</p>
              </div>
            </div>

            <!-- Tool cards -->
            <div
              v-for="tool in tools"
              :key="tool.id"
              class="bg-[#0f1d30] border border-[#3c4a46] rounded-lg overflow-hidden"
            >
              <!-- Collapsed view -->
              <div
                class="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-[#0f1d30]/80 transition-colors"
                @click="toggleToolExpand(tool.id)"
              >
                <span
                  class="text-[10px] font-bold px-1.5 py-0.5 rounded border"
                  :class="getMethodClass(tool.method)"
                >
                  {{ tool.method }}
                </span>
                <span class="text-[11px] font-mono text-[#dae2fd] flex-1 truncate">{{ tool.url_pattern }}</span>
                <span class="material-symbols-outlined text-[#5a6a65] text-sm transition-transform" :class="expandedToolId === tool.id ? 'rotate-180' : ''">
                  expand_more
                </span>
              </div>

              <!-- Expanded view -->
              <div v-if="expandedToolId === tool.id" class="border-t border-[#3c4a46] px-3 py-2">
                <p class="text-[10px] text-[#bacac5] mb-2">{{ tool.description }}</p>
                <textarea
                  v-model="toolEdits[tool.id]"
                  class="w-full h-32 bg-[#0b1326] border border-[#3c4a46] rounded text-[10px] font-mono text-[#dae2fd] p-2 outline-none focus:border-[#57f1db]/50 resize-y"
                  spellcheck="false"
                ></textarea>
                <div class="flex justify-end gap-2 mt-2">
                  <button
                    @click="handleDeleteTool(tool.id)"
                    class="px-2 py-1 text-[10px] bg-[#ffb4ab]/20 text-[#ffb4ab] border border-[#ffb4ab]/30 rounded hover:bg-[#ffb4ab]/30 transition-colors"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Status bar -->
    <footer class="h-7 flex-shrink-0 bg-[#0f1d30] border-t border-[#3c4a46] flex items-center px-4 gap-4 text-[10px] text-[#5a6a65]">
      <span>
        <span class="text-[#57f1db] font-bold">{{ tools.length }}</span> tools
      </span>
      <div class="w-px h-3 bg-[#3c4a46]"></div>
      <span>
        Status:
        <span :class="session?.status === 'recording' ? 'text-[#ffb4ab]' : session?.status ? 'text-[#57f1db]' : 'text-[#5a6a65]'">
          {{ session?.status || 'idle' }}
        </span>
      </span>
      <div v-if="isRecording" class="flex items-center gap-1">
        <div class="w-1.5 h-1.5 rounded-full bg-[#ffb4ab] animate-pulse"></div>
        <span class="text-[#ffb4ab] font-bold">REC</span>
      </div>
      <div class="flex-1"></div>
      <span v-if="sessionId" class="font-mono">{{ sessionId.slice(0, 8) }}...</span>
    </footer>

    <div
      v-if="publishDialogOpen"
      class="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 px-4"
    >
      <div class="w-full max-w-md rounded-xl border border-[#3c4a46] bg-[#0f1d30] p-5 shadow-2xl">
        <div class="flex items-center justify-between">
          <h2 class="text-base font-bold text-[#dae2fd]">Save as MCP Tool</h2>
          <button
            class="text-[#5a6a65] hover:text-[#dae2fd]"
            @click="publishDialogOpen = false"
          >
            <span class="material-symbols-outlined text-lg">close</span>
          </button>
        </div>
        <label class="mt-4 block text-xs font-semibold text-[#bacac5]">
          MCP Name
          <input
            v-model="publishForm.mcpName"
            class="mt-1 w-full rounded-lg border border-[#3c4a46] bg-[#0b1326] px-3 py-2 text-sm text-[#dae2fd] outline-none focus:border-[#57f1db]/60"
            type="text"
          />
        </label>
        <label class="mt-3 block text-xs font-semibold text-[#bacac5]">
          Description
          <textarea
            v-model="publishForm.description"
            class="mt-1 h-20 w-full resize-none rounded-lg border border-[#3c4a46] bg-[#0b1326] px-3 py-2 text-sm text-[#dae2fd] outline-none focus:border-[#57f1db]/60"
          ></textarea>
        </label>
        <div class="mt-5 flex justify-end gap-2">
          <button
            class="rounded-lg border border-[#3c4a46] px-3 py-2 text-sm text-[#bacac5] hover:bg-white/5"
            @click="publishDialogOpen = false"
          >
            Cancel
          </button>
          <button
            class="rounded-lg border border-[#57f1db]/30 bg-[#57f1db]/20 px-3 py-2 text-sm font-semibold text-[#57f1db] hover:bg-[#57f1db]/30 disabled:opacity-50"
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
      class="fixed inset-0 z-[110] flex items-center justify-center bg-black/70 px-4"
    >
      <div class="w-full max-w-md rounded-xl border border-[#ffb4ab]/30 bg-[#0f1d30] p-5 shadow-2xl">
        <h2 class="text-base font-bold text-[#dae2fd]">Replace existing MCP tools?</h2>
        <p class="mt-3 text-sm leading-6 text-[#bacac5]">
          An MCP named "{{ publishForm.mcpName }}" already exists. Replacing it will overwrite all tools under that MCP with the current API Monitor results.
        </p>
        <div class="mt-5 flex justify-end gap-2">
          <button
            class="rounded-lg border border-[#3c4a46] px-3 py-2 text-sm text-[#bacac5] hover:bg-white/5"
            @click="overwriteDialogOpen = false"
          >
            Cancel
          </button>
          <button
            class="rounded-lg border border-[#ffb4ab]/30 bg-[#ffb4ab]/20 px-3 py-2 text-sm font-semibold text-[#ffb4ab] hover:bg-[#ffb4ab]/30 disabled:opacity-50"
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
