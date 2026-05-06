<script setup lang="ts">
import { ArrowLeft, Globe, BarChart2, Disc, Square, Save, Wrench, ChevronDown, MonitorPlay, X, AlertTriangle, Terminal, Loader2 } from 'lucide-vue-next';
import { ref, reactive, onMounted, onBeforeUnmount, nextTick, computed } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import {
  startSession,
  stopSession,
  analyzeSession,
  startRecording as apiStartRecording,
  stopRecording as apiStopRecording,
  listTools,
  listGenerationCandidates,
  retryGenerationCandidate,
  updateTool as apiUpdateTool,
  deleteTool as apiDeleteTool,
  publishMcpToolBundle,
  updateToolSelection as apiUpdateToolSelection,
  getAuthProfile,
  getTokenFlowProfile,
  type ApiMonitorSession,
  type ApiToolDefinition,
  type ApiToolGenerationCandidate,
  type ApiMonitorAuthConfig,
  type ApiMonitorAuthProfile,
  type ApiMonitorManualTokenFlow,
  type TokenFlowProfile,
  type TokenFlowSelection,
} from '@/api/apiMonitor';
import { listCredentials, type Credential } from '@/api/credential';
import { API_MONITOR_CREDENTIAL_TYPE_OPTIONS, normalizeApiMonitorAuth } from '@/utils/apiMonitorAuth';
import { getBackendWsUrl } from '@/utils/sandbox';
import { showErrorToast, showSuccessToast } from '@/utils/toast';
import {
  ANALYSIS_MODES,
  canStartAnalysis,
  getAnalysisMode,
  modeRequiresInstruction,
  type AnalysisModeKey,
} from '@/utils/apiMonitorAnalysisModes';
import {
  getFrameSizeFromMetadata,
  getInputSizeFromMetadata,
  mapClientPointToViewportPoint,
  type ScreencastFrameMetadata,
  type ScreencastSize,
} from '@/utils/screencastGeometry';
import {
  buildScreencastReconnectMessage,
  getScreencastReconnectDelayMs,
  getScreencastReconnectNoticeDelayMs,
  isTerminalScreencastClose,
  shouldShowScreencastReconnectNotice,
} from '@/utils/screencastReconnect';
import { shouldForwardScreencastKeyboardEvent } from '@/utils/screencastInput';

const router = useRouter();
const { t } = useI18n();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const sessionId = ref<string>('');
const session = ref<ApiMonitorSession | null>(null);
const urlInput = ref('https://');
const tools = ref<ApiToolDefinition[]>([]);
const generationCandidates = ref<ApiToolGenerationCandidate[]>([]);
let generationRefreshTimer: ReturnType<typeof window.setInterval> | null = null;
const visibleGenerationCandidates = computed(() =>
  generationCandidates.value.filter((candidate) => candidate.status !== 'generated' || !candidate.tool_id),
);
const hasActiveGenerationCandidates = computed(() =>
  generationCandidates.value.some((candidate) => ['pending', 'running', 'stale'].includes(candidate.status)),
);
const detectedItemCount = computed(() => tools.value.length + visibleGenerationCandidates.value.length);
const adoptedTools = computed(() => tools.value.filter((tool) => tool.selected));
const notAdoptedTools = computed(() => tools.value.filter((tool) => !tool.selected));
const adoptedToolCount = computed(() => adoptedTools.value.length);
const toolGroups = computed(() => [
  { key: 'adopted', title: '采用', items: adoptedTools.value },
  { key: 'not-adopted', title: '不采用', items: notAdoptedTools.value },
]);
const terminalLines = ref<{ html: string }[]>([]);
const isRecording = ref(false);
const isAnalyzing = ref(false);
const analysisModes = ANALYSIS_MODES;
const analysisMode = ref<AnalysisModeKey>('free');
const analysisInstruction = ref('');
const analysisMenuOpen = ref(false);
const analysisMenuAnchor = ref<HTMLElement | null>(null);
const selectedAnalysisMode = computed(() => getAnalysisMode(analysisMode.value));
const showAnalysisInstruction = computed(() => modeRequiresInstruction(analysisMode.value));
const canRunAnalysis = computed(() => canStartAnalysis({
  hasSession: Boolean(sessionId.value),
  isAnalyzing: isAnalyzing.value,
  mode: analysisMode.value,
  instruction: analysisInstruction.value,
}));

const analysisMenuStyle = computed(() => {
  const anchor = analysisMenuAnchor.value;
  if (!anchor) return {};
  const rect = anchor.getBoundingClientRect();
  return {
    position: 'fixed' as const,
    top: `${rect.bottom + 8}px`,
    left: `${rect.left}px`,
    zIndex: 9999,
  };
});

const selectAnalysisMode = (mode: AnalysisModeKey) => {
  analysisMode.value = mode;
  analysisMenuOpen.value = false;
};

const handleClickOutsideMenu = (e: MouseEvent) => {
  if (!analysisMenuOpen.value) return;
  const anchor = analysisMenuAnchor.value;
  if (anchor && !anchor.contains(e.target as Node)) {
    analysisMenuOpen.value = false;
  }
};
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
const authProfile = ref<ApiMonitorAuthProfile | null>(null);
const tokenFlowProfile = ref<TokenFlowProfile[]>([]);
const tokenFlowSelections = ref<Record<string, boolean>>({});
const tokenFlowDrafts = reactive<Record<string, string>>({});
const tokenFlowDraftErrors = reactive<Record<string, string>>({});
const manualTokenFlowJson = ref('');
const manualTokenFlowJsonError = ref('');
const isLoadingAuthProfile = ref(false);
const publishCredentials = ref<Credential[]>([]);
const publishAuth = reactive<ApiMonitorAuthConfig>({
  credential_type: 'placeholder',
  credential_id: '',
  login_url: '',
});

// Screencast
let screencastWs: WebSocket | null = null;
const canvasRef = ref<HTMLCanvasElement | null>(null);
const screencastFrameSize = ref<ScreencastSize>({ width: 1280, height: 720 });
const screencastInputSize = ref<ScreencastSize>({ width: 1280, height: 720 });
let shouldReconnectScreencast = true;
let screencastReconnectTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectNoticeTimer: ReturnType<typeof setTimeout> | null = null;
let screencastReconnectAttempts = 0;
let screencastReconnectStartedAt = 0;
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
    if (!shouldForwardScreencastKeyboardEvent(e)) return;
    e.preventDefault();
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

const handlePaste = (e: ClipboardEvent) => {
  if (!screencastWs || screencastWs.readyState !== WebSocket.OPEN) return;
  const text = e.clipboardData?.getData('text');
  if (!text) return;
  screencastWs.send(JSON.stringify({ type: 'paste', text }));
};

const clearScreencastReconnectTimer = () => {
  if (screencastReconnectTimer !== null) {
    clearTimeout(screencastReconnectTimer);
    screencastReconnectTimer = null;
  }
};

const clearScreencastReconnectNoticeTimer = () => {
  if (screencastReconnectNoticeTimer !== null) {
    clearTimeout(screencastReconnectNoticeTimer);
    screencastReconnectNoticeTimer = null;
  }
};

const hasPendingScreencastReconnect = () => (
  screencastReconnectTimer !== null ||
  (screencastWs !== null && screencastWs.readyState !== WebSocket.OPEN)
);

const disconnectScreencast = () => {
  clearScreencastReconnectTimer();
  clearScreencastReconnectNoticeTimer();
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

const scheduleScreencastReconnect = (sid: string) => {
  if (!shouldReconnectScreencast || screencastReconnectTimer !== null) return;

  screencastReconnectAttempts += 1;
  if (screencastReconnectStartedAt <= 0) {
    screencastReconnectStartedAt = Date.now();
  }

  const delay = getScreencastReconnectDelayMs(screencastReconnectAttempts);
  const message = buildScreencastReconnectMessage('录制', delay);
  const noticeDelay = getScreencastReconnectNoticeDelayMs({
    outageStartedAtMs: screencastReconnectStartedAt,
    nowMs: Date.now(),
  });

  clearScreencastReconnectNoticeTimer();
  screencastReconnectNoticeTimer = setTimeout(() => {
    screencastReconnectNoticeTimer = null;
    if (shouldShowScreencastReconnectNotice({
      shouldReconnect: shouldReconnectScreencast,
      hasPendingReconnect: hasPendingScreencastReconnect(),
    })) {
      error.value = message;
      addLog('INFO', message);
    }
  }, noticeDelay);

  screencastReconnectTimer = setTimeout(() => {
    screencastReconnectTimer = null;
    if (!shouldReconnectScreencast) return;
    connectScreencast(sid);
  }, delay);
};

const connectScreencast = (sid: string) => {
  clearScreencastReconnectTimer();
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
    screencastReconnectAttempts = 0;
    screencastReconnectStartedAt = 0;
    clearScreencastReconnectNoticeTimer();
    if (error.value?.includes('画面流暂时中断')) {
      error.value = null;
    }
    addLog('INFO', '屏幕录制已连接');
  };

  ws.onmessage = (ev) => {
    if (screencastWs !== ws) return;
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'frame') {
        drawFrame(msg.data, msg.metadata);
      } else if (msg.type === 'monitor_log') {
        const level = msg.level === 'ERROR' ? 'ERROR' : 'RECV';
        addLog(level, msg.message);
      } else if (msg.type === 'preview_error') {
        if (!hasPendingScreencastReconnect()) {
          error.value = msg.message || 'Screencast error';
        }
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
    if (isTerminalScreencastClose(ev.code)) {
      error.value = ev.reason || '录制画面流连接失败';
      return;
    }
    scheduleScreencastReconnect(sid);
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
    addLog('INFO', `正在启动会话: ${url}`);
    const s = await startSession(url);
    sessionId.value = s.id;
    session.value = s;
    addLog('INFO', `已创建会话: ${s.id}`);
    connectScreencast(s.id);
    // Load any existing tools
    const existingTools = await listTools(s.id);
    tools.value = existingTools;
    generationCandidates.value = await listGenerationCandidates(s.id);
    if (existingTools.length > 0) {
      addLog('INFO', `已加载 ${existingTools.length} 个现有工具`);
    }
  } catch (err: any) {
    console.error('Failed to start API monitor session:', err);
    error.value = err.response?.data?.detail || '启动会话失败';
    addLog('ERROR', `启动会话失败: ${error.value}`);
  } finally {
    loading.value = false;
  }
};

// ---------------------------------------------------------------------------
// Analysis
// ---------------------------------------------------------------------------

const startAnalysis = async () => {
  if (!canRunAnalysis.value) return;
  isAnalyzing.value = true;
  startGenerationRefresh();
  const mode = selectedAnalysisMode.value;
  const instruction = showAnalysisInstruction.value ? analysisInstruction.value.trim() : '';
  addLog('INFO', `开始${mode.label}...`);

  const cleanup = analyzeSession(sessionId.value, (evt) => {
    let data: any;
    try { data = typeof evt.data === 'string' ? JSON.parse(evt.data) : evt.data; } catch { data = evt.data; }
    switch (evt.event) {
      case 'analysis_started':
        addLog('INFO', `正在分析: ${data.url || ''}${data.mode ? ` [${data.mode}]` : ''}`);
        break;
      case 'progress':
        if (data.step === 'scanning') {
          addLog('INFO', data.message);
        } else if (data.step === 'classifying') {
          addLog('ANALYZE', data.message);
        } else if (data.step === 'probing') {
          addLog('ANALYZE', `${data.message} (${data.current}/${data.total})`);
        } else if (data.step === 'snapshot') {
          addLog('ANALYZE', data.message);
        } else if (data.step === 'planning') {
          addLog('ANALYZE', data.message);
        } else if (data.step === 'executing') {
          addLog('ANALYZE', data.message);
        } else if (data.step === 'generating') {
          addLog('BUILD', data.message);
        } else {
          addLog('INFO', data.message || '处理中...');
        }
        break;
      case 'elements_found':
        addLog('INFO', `找到 ${data.count} 个可交互元素`);
        break;
      case 'elements_classified':
        addLog('ANALYZE', `已分类: ${data.safe} 个安全, ${data.skipped} 个跳过`);
        break;
      case 'directed_plan_ready':
        addLog('ANALYZE', `操作计划已生成: ${data.action_count || 0} 个动作`);
        break;
      case 'directed_step_snapshot':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮页面观察: ${data.title || data.url || ''}`);
        break;
      case 'directed_step_planned':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮决策: ${data.summary || data.goal_status || ''}`);
        break;
      case 'directed_trace_added':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮 trace 已创建`);
        break;
      case 'directed_trace_updated':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮 trace 已更新: ${data.execution?.result || data.decision?.goal_status || ''}`);
        break;
      case 'directed_action_detail':
        addLog('BUILD', `${data.description}  →  ${data.code}`);
        break;
      case 'directed_step_executed':
        addLog('ANALYZE', `✓ 第 ${data.step || '-'} 轮已执行: ${data.code || data.description || ''}`);
        break;
      case 'directed_action_executed':
        addLog('ANALYZE', `✓ 已执行: ${data.code}`);
        break;
      case 'directed_action_skipped':
        addLog('ANALYZE', `已跳过动作: ${data.description || ''}${data.reason ? `（${data.reason}）` : ''}`);
        break;
      case 'directed_step_observed':
        addLog('ANALYZE', `第 ${data.step || '-'} 轮观察完成: 新增 ${data.new_calls || 0} 个 API 调用`);
        break;
      case 'directed_replan':
        addLog('ANALYZE', `动作失败，准备重规划: ${data.error || data.description || ''}`);
        break;
      case 'directed_done':
        addLog('ANALYZE', `定向分析停止: ${data.reason || data.goal_status || ''}`);
        break;
      case 'calls_captured':
        addLog('RECV', data.step
          ? `第 ${data.step} 轮捕获了 ${data.calls} 个 API 调用`
          : `从元素 ${data.element_index} 捕获了 ${data.calls} 个 API 调用`);
        break;
      case 'api_candidate_created':
      case 'api_candidate_updated':
      case 'api_candidate_rate_limited':
      case 'api_tool_generation_failed':
        upsertGenerationCandidate({
          id: data.candidate_id,
          session_id: sessionId.value,
          dedup_key: data.dedup_key,
          method: data.method,
          url_pattern: data.url_pattern,
          source_call_ids: [],
          sample_call_ids: [],
          status: data.status,
          tool_id: data.tool_id,
          error: data.error || '',
          retry_after: data.retry_after,
          attempts: 0,
          capture_dom_context: {},
          capture_page_url: '',
          capture_title: '',
          capture_dom_digest: '',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        });
        addLog('BUILD', `${data.method} ${data.url_pattern} ${getCandidateStatusLabel(data.status)}`);
        break;
      case 'api_tool_generated':
        if (data.tool) {
          const idx = tools.value.findIndex((tool) => tool.id === data.tool.id);
          if (idx >= 0) tools.value[idx] = data.tool;
          else tools.value.unshift(data.tool);
        }
        generationCandidates.value = generationCandidates.value.filter((item) => item.id !== data.candidate_id);
        addLog('BUILD', `工具已生成: ${data.tool?.name || data.url_pattern}`);
        break;
      case 'analysis_complete':
        addLog('INFO', `分析完成: ${data.tools_generated} 个工具, ${data.total_calls} 个调用`);
        isAnalyzing.value = false;
        refreshGenerationState().catch(() => {});
        cleanup();
        break;
      case 'analysis_error':
        addLog('ERROR', data.error);
        isAnalyzing.value = false;
        refreshGenerationState().catch(() => {});
        cleanup();
        break;
    }
  }, {
    mode: analysisMode.value,
    instruction,
  });
};

const handleRetryCandidate = async (candidate: ApiToolGenerationCandidate) => {
  if (!sessionId.value) return;
  try {
    const updated = await retryGenerationCandidate(sessionId.value, candidate.id);
    upsertGenerationCandidate(updated);
    addLog('BUILD', `已重新排队: ${candidate.method} ${candidate.url_pattern}`);
  } catch (err: any) {
    addLog('ERROR', `重试生成失败: ${err.message}`);
  }
};

// ---------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------

const toggleRecording = async () => {
  if (!sessionId.value) return;

  if (isRecording.value) {
    try {
      addLog('INFO', '正在停止录制...');
      await apiStopRecording(sessionId.value);
      isRecording.value = false;
      await refreshGenerationState();
      addLog(
        'INFO',
        `录制已停止。当前 ${tools.value.length} 个工具，${visibleGenerationCandidates.value.length} 个仍在生成。`,
      );
    } catch (err: any) {
      addLog('ERROR', `停止录制失败: ${err.message}`);
    }
  } else {
    try {
      addLog('INFO', '正在开始录制...');
      await apiStartRecording(sessionId.value);
      isRecording.value = true;
      startGenerationRefresh();
      addLog('INFO', '录制已开始。请与浏览器交互以捕获 API 调用。');
    } catch (err: any) {
      addLog('ERROR', `开始录制失败: ${err.message}`);
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
    addLog('INFO', `正在删除工具: ${toolId}`);
    await apiDeleteTool(sessionId.value, toolId);
    tools.value = tools.value.filter((t) => t.id !== toolId);
    delete toolEdits[toolId];
    if (expandedToolId.value === toolId) {
      expandedToolId.value = null;
    }
    addLog('INFO', `工具已删除`);
  } catch (err: any) {
    addLog('ERROR', `删除工具失败: ${err.message}`);
  }
};

const toggleToolSelection = async (tool: ApiToolDefinition, selected: boolean) => {
  if (!sessionId.value) return;
  try {
    const updated = await apiUpdateToolSelection(sessionId.value, tool.id, selected);
    const idx = tools.value.findIndex((item) => item.id === tool.id);
    if (idx >= 0) {
      tools.value[idx] = updated;
    }
    addLog('INFO', `${selected ? '已采用' : '已取消采用'}: ${tool.name || tool.url_pattern}`);
  } catch (err: any) {
    addLog('ERROR', `更新采用状态失败: ${err.message}`);
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

const openPublishDialog = async () => {
  if (!sessionId.value || !adoptedToolCount.value) return;
  publishForm.mcpName = publishForm.mcpName || getDefaultMcpName();
  publishForm.description = publishForm.description || session.value?.target_url || urlInput.value || '';
  publishAuth.credential_type = 'placeholder';
  publishAuth.credential_id = '';
  publishAuth.login_url = '';
  publishDialogOpen.value = true;
  isLoadingAuthProfile.value = true;
  try {
    const [profile, creds] = await Promise.all([
      getAuthProfile(sessionId.value),
      listCredentials(),
    ]);
    authProfile.value = profile;
    publishCredentials.value = creds;
    publishAuth.credential_type = profile.recommended_credential_type || 'placeholder';
    // Load token flow profile separately (non-critical)
    try {
      const tfProfile = await getTokenFlowProfile(sessionId.value);
      tokenFlowProfile.value = tfProfile.flows || [];
      tokenFlowSelections.value = {};
      Object.keys(tokenFlowDrafts).forEach((key) => delete tokenFlowDrafts[key]);
      Object.keys(tokenFlowDraftErrors).forEach((key) => delete tokenFlowDraftErrors[key]);
      for (const flow of tfProfile.flows || []) {
        tokenFlowSelections.value[flow.id] = flow.enabled_by_default;
      }
      if (tokenFlowProfile.value.length > 0) {
        addLog('INFO', `检测到 ${tokenFlowProfile.value.length} 个动态 Token 流程`);
      } else {
        addLog('INFO', '未检测到动态 Token 流程（捕获的流量中未发现 token 传递模式）');
      }
    } catch (err: any) {
      tokenFlowProfile.value = [];
      addLog('ERROR', `加载 Token Flow 失败: ${err.message}`);
    }
  } catch (err: any) {
    authProfile.value = null;
    tokenFlowProfile.value = [];
    publishCredentials.value = [];
    addLog('ERROR', `加载认证配置失败: ${err.message}`);
  } finally {
    isLoadingAuthProfile.value = false;
  }
};

const parseManualTokenFlows = (): Array<Record<string, unknown>> => {
  manualTokenFlowJsonError.value = '';
  if (!manualTokenFlowJson.value.trim()) return [];
  try {
    const parsed = JSON.parse(manualTokenFlowJson.value);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch (error) {
    manualTokenFlowJsonError.value = error instanceof Error ? error.message : 'Invalid JSON';
    return [];
  }
};

const beginEditTokenFlow = (flow: TokenFlowProfile) => {
  tokenFlowDraftErrors[flow.id] = '';
  tokenFlowDrafts[flow.id] = JSON.stringify(flow.runtime_config || {}, null, 2);
};

const resetTokenFlowDraft = (flowId: string) => {
  delete tokenFlowDrafts[flowId];
  delete tokenFlowDraftErrors[flowId];
};

const parseEditedTokenFlows = (): ApiMonitorManualTokenFlow[] | null => {
  const flows: ApiMonitorManualTokenFlow[] = [];
  for (const [flowId, draft] of Object.entries(tokenFlowDrafts)) {
    tokenFlowDraftErrors[flowId] = '';
    if (!tokenFlowSelections.value[flowId]) continue;
    try {
      const parsed = JSON.parse(draft);
      flows.push(parsed as ApiMonitorManualTokenFlow);
    } catch (error) {
      tokenFlowDraftErrors[flowId] = error instanceof Error ? error.message : 'Invalid JSON';
      return null;
    }
  }
  return flows;
};

const submitPublish = async (confirmOverwrite = false) => {
  if (!sessionId.value || !adoptedToolCount.value || !publishForm.mcpName.trim()) return;
  isPublishing.value = true;
  try {
    addLog('INFO', '正在发布 MCP 工具...');
    await flushToolEdits();
    const authPayload = normalizeApiMonitorAuth(publishAuth);
    const editedFlows = parseEditedTokenFlows();
    if (editedFlows === null) {
      addLog('ERROR', 'Token Flow JSON 格式错误');
      isPublishing.value = false;
      return;
    }
    // Include enabled token flow selections
    const enabledFlows: TokenFlowSelection[] = Object.entries(tokenFlowSelections.value)
      .filter(([id, enabled]) => enabled && !(id in tokenFlowDrafts))
      .map(([id, enabled]) => ({ id, enabled }));
    if (enabledFlows.length > 0) {
      authPayload.token_flows = enabledFlows;
    }
    // Include manual token flows
    const manualFlows = parseManualTokenFlows();
    if (manualTokenFlowJsonError.value) {
      addLog('ERROR', `手动 Token Flow JSON 格式错误: ${manualTokenFlowJsonError.value}`);
      isPublishing.value = false;
      return;
    }
    if (manualFlows.length > 0) {
      authPayload.manual_token_flows = manualFlows as any;
    }
    if (editedFlows.length > 0) {
      authPayload.manual_token_flows = [
        ...((authPayload.manual_token_flows || []) as any[]),
        ...(editedFlows as any[]),
      ];
    }
    const result = await publishMcpToolBundle(sessionId.value, {
      mcp_name: publishForm.mcpName.trim(),
      description: publishForm.description.trim(),
      confirm_overwrite: confirmOverwrite,
      api_monitor_auth: authPayload,
    });
    publishDialogOpen.value = false;
    overwriteDialogOpen.value = false;
    showSuccessToast(`已保存 MCP "${publishForm.mcpName}"，包含 ${result.tool_count} 个工具`);
  } catch (err: any) {
    if (err?.response?.status === 409 && err?.response?.data?.needs_confirmation) {
      overwriteDialogOpen.value = true;
      addLog('INFO', '发现已存在的 MCP。等待覆盖确认。');
      return;
    }
    showErrorToast(`保存 MCP 失败: ${err.message}`);
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

const confidenceLabels: Record<string, string> = {
  high: '高置信',
  medium: '中置信',
  low: '低置信',
};

const getConfidenceLabelWithScore = (confidence: string, score: number) => {
  const label = confidenceLabels[confidence] || '中置信';
  return `${score} ${label}`;
};

const confidenceClasses: Record<string, string> = {
  high: 'bg-emerald-500/15 text-emerald-600 border-emerald-500/25 dark:text-emerald-300',
  medium: 'bg-amber-500/15 text-amber-600 border-amber-500/25 dark:text-amber-300',
  low: 'bg-slate-500/15 text-slate-600 border-slate-500/25 dark:text-slate-300',
};

const getConfidenceClass = (confidence: string) => confidenceClasses[confidence] || confidenceClasses.medium;

const upsertGenerationCandidate = (candidate: ApiToolGenerationCandidate) => {
  const idx = generationCandidates.value.findIndex((item) => item.id === candidate.id);
  if (idx >= 0) {
    generationCandidates.value[idx] = candidate;
  } else {
    generationCandidates.value.unshift(candidate);
  }
};

const refreshGenerationState = async () => {
  if (!sessionId.value) return;
  const [nextTools, nextCandidates] = await Promise.all([
    listTools(sessionId.value),
    listGenerationCandidates(sessionId.value),
  ]);
  tools.value = nextTools;
  generationCandidates.value = nextCandidates;
};

const startGenerationRefresh = () => {
  if (generationRefreshTimer) return;
  generationRefreshTimer = window.setInterval(() => {
    if (!isRecording.value && !isAnalyzing.value && !hasActiveGenerationCandidates.value) {
      stopGenerationRefresh();
      return;
    }
    refreshGenerationState().catch(() => {});
  }, 1000);
};

const stopGenerationRefresh = () => {
  if (!generationRefreshTimer) return;
  window.clearInterval(generationRefreshTimer);
  generationRefreshTimer = null;
};

const getCandidateStatusLabel = (status: ApiToolGenerationCandidate['status']) => {
  if (status === 'pending') return '等待生成';
  if (status === 'running') return '生成中';
  if (status === 'rate_limited') return '限流重试中';
  if (status === 'failed') return '生成失败';
  if (status === 'stale') return '等待更新';
  return '已生成';
};

const getCandidateStatusClass = (status: ApiToolGenerationCandidate['status']) => {
  if (status === 'running' || status === 'pending') return 'border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-500/30 dark:bg-sky-500/10 dark:text-sky-300';
  if (status === 'rate_limited') return 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300';
  if (status === 'failed') return 'border-red-200 bg-red-50 text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300';
  return 'border-slate-200 bg-slate-50 text-slate-600 dark:border-white/10 dark:bg-white/5 dark:text-slate-300';
};

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

onMounted(() => {
  addLog('INFO', 'API 监控已准备就绪。输入 URL 并点击 Go 开始。');
  document.addEventListener('click', handleClickOutsideMenu, true);
});

onBeforeUnmount(() => {
  document.removeEventListener('click', handleClickOutsideMenu, true);
  stopGenerationRefresh();
  shouldReconnectScreencast = false;
  disconnectScreencast();
  if (sessionId.value) {
    stopSession(sessionId.value).catch(() => {});
  }
});
</script>

<template>
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
                @click="openPublishDialog"
                :disabled="!sessionId || !adoptedToolCount || isPublishing"
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
        
        <!-- Action Toolbar -->
        <div v-if="sessionId" class="flex flex-col border-b border-slate-100 dark:border-white/10 bg-white dark:bg-[#1a1a1a] shrink-0 p-4 gap-4 z-10 relative">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-3">
              <!-- Analysis Menu -->
              <div ref="analysisMenuAnchor" class="inline-flex overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-white/10 dark:bg-white/5">
                <button
                  @click="startAnalysis"
                  :disabled="!canRunAnalysis"
                  class="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-slate-100 dark:hover:bg-white/10 disabled:opacity-50"
                >
                  <BarChart2 :size="16" class="text-indigo-500" />
                  分析
                </button>
                <button
                  type="button"
                  class="inline-flex items-center gap-1 border-l border-slate-200 dark:border-white/10 px-3 py-2 text-sm font-semibold text-[var(--text-primary)] transition hover:bg-slate-100 dark:hover:bg-white/10 disabled:opacity-50"
                  :disabled="!sessionId || isAnalyzing"
                  @click="analysisMenuOpen = !analysisMenuOpen"
                >
                  {{ selectedAnalysisMode.label }}
                  <ChevronDown :size="14" />
                </button>
              </div>
              <Teleport to="body">
                <div
                  v-if="analysisMenuOpen"
                  :style="analysisMenuStyle"
                  class="w-64 overflow-hidden rounded-2xl border border-slate-200 bg-white py-2 text-left shadow-xl dark:border-white/10 dark:bg-[#17181d]"
                >
                  <button
                    v-for="mode in analysisModes"
                    :key="mode.key"
                    type="button"
                    class="block w-full px-4 py-3 text-left transition hover:bg-slate-50 dark:hover:bg-white/[0.06]"
                    @click="selectAnalysisMode(mode.key)"
                  >
                    <span class="block text-sm font-bold text-slate-900 dark:text-white">{{ mode.label }}</span>
                    <span class="mt-1 block text-xs leading-5 text-slate-500 dark:text-slate-400">{{ mode.description }}</span>
                  </button>
                </div>
              </Teleport>

              <!-- Recording Toggle -->
              <button
                @click="toggleRecording"
                :disabled="!sessionId"
                class="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold transition disabled:opacity-50"
                :class="isRecording ? 'bg-red-500 text-white shadow-md hover:bg-red-600' : 'border border-slate-200 bg-slate-50 text-[var(--text-primary)] hover:bg-slate-100 dark:border-white/10 dark:bg-white/5 dark:hover:bg-white/10'"
              >
                <component :is="isRecording ? Square : Disc" :size="16" />
                {{ isRecording ? '停止' : '录制' }}
              </button>
            </div>
          </div>

          <!-- Large Instruction Input -->
          <div v-if="showAnalysisInstruction" class="relative">
            <input
              v-model="analysisInstruction"
              type="text"
              placeholder="请输入操作说明（例如：点击登录按钮，输入账号密码等）..."
              class="w-full rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-[var(--text-primary)] shadow-inner outline-none transition focus:border-sky-400 focus:bg-white focus:ring-4 focus:ring-sky-400/10 dark:border-white/10 dark:bg-black/20 dark:focus:bg-[#1a1a1a]"
              @keyup.enter="startAnalysis"
            />
          </div>
        </div>

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
            @keydown="sendInputEvent"
            @keyup.prevent="sendInputEvent"
            @paste.prevent="handlePaste"
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
            <span class="font-mono font-bold" :class="tools.length > 0 ? 'text-sky-500' : 'text-[var(--text-tertiary)]'">{{ adoptedToolCount }}/{{ tools.length }}</span> 个工具
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
            <span class="px-1.5 py-0.5 rounded-md bg-slate-100 dark:bg-white/10 text-[var(--text-secondary)] font-mono text-[10px] font-bold leading-none ml-1">{{ detectedItemCount }}</span>
          </div>
          <div class="flex-1 overflow-y-auto p-4 space-y-3 bg-white dark:bg-transparent">
            <!-- Empty state -->
            <div v-if="detectedItemCount === 0" class="h-full flex flex-col items-center justify-center text-[var(--text-tertiary)]">
              <Wrench :size="40" class="mb-3 opacity-30" />
              <p class="text-sm font-medium text-[var(--text-secondary)] mb-1">尚未检测到工具</p>
              <p class="text-xs">点击"分析"或"录制"以发现 API 工具。</p>
            </div>

            <!-- Generation candidate placeholders -->
            <div v-if="visibleGenerationCandidates.length" class="space-y-2">
              <div class="flex items-center justify-between px-1 text-[11px] font-bold text-[var(--text-tertiary)]">
                <span>生成中</span>
                <span>{{ visibleGenerationCandidates.length }}</span>
              </div>
              <div
                v-for="candidate in visibleGenerationCandidates"
                :key="candidate.id"
                class="rounded-2xl border border-slate-200 bg-slate-50/80 px-4 py-3 shadow-sm dark:border-white/10 dark:bg-white/[0.04]"
              >
                <div class="flex items-center gap-3">
                  <span class="text-[10px] font-bold px-2 py-0.5 rounded-md" :class="getMethodClass(candidate.method)">
                    {{ candidate.method }}
                  </span>
                  <span class="min-w-0 flex-1 truncate font-mono text-[11px] text-[var(--text-primary)]">
                    {{ candidate.url_pattern }}
                  </span>
                  <span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold" :class="getCandidateStatusClass(candidate.status)">
                    {{ getCandidateStatusLabel(candidate.status) }}
                  </span>
                </div>
                <div class="mt-2 flex items-center justify-between gap-3 text-[10px] text-[var(--text-tertiary)]">
                  <span>样本 {{ candidate.source_call_ids?.length || 0 }}</span>
                  <span v-if="candidate.retry_after">下次重试 {{ new Date(candidate.retry_after).toLocaleTimeString() }}</span>
                  <span v-else-if="candidate.error" class="truncate text-red-500">{{ candidate.error }}</span>
                  <button
                    v-if="candidate.status === 'failed' || candidate.status === 'rate_limited'"
                    class="rounded-lg border border-slate-200 px-2 py-1 font-bold text-[var(--text-secondary)] transition hover:bg-slate-100 dark:border-white/10 dark:hover:bg-white/10"
                    @click="handleRetryCandidate(candidate)"
                  >
                    重试
                  </button>
                </div>
              </div>
            </div>

            <!-- Grouped tool cards -->
            <template v-for="group in toolGroups" :key="group.key">
              <div v-if="group.items.length" class="space-y-2">
                <div class="flex items-center justify-between px-1 text-[11px] font-bold text-[var(--text-tertiary)]">
                  <span>{{ group.title }}</span>
                  <span>{{ group.items.length }}</span>
                </div>
                <div
                  v-for="tool in group.items"
                  :key="tool.id"
                  class="rounded-2xl border border-slate-200 bg-slate-50/80 shadow-sm overflow-hidden dark:border-white/10 dark:bg-white/[0.04]"
                >
                  <div
                    class="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-slate-100 dark:hover:bg-white/[0.06] transition-colors"
                    @click="toggleToolExpand(tool.id)"
                  >
                    <button
                      class="shrink-0 rounded-lg border px-2 py-1 text-[10px] font-bold transition"
                      :class="tool.selected ? 'border-emerald-400 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300' : 'border-slate-300 bg-white text-slate-500 dark:border-white/10 dark:bg-white/5 dark:text-slate-300'"
                      @click.stop="toggleToolSelection(tool, !tool.selected)"
                    >
                      {{ tool.selected ? '采用' : '不采用' }}
                    </button>
                    <span class="text-[10px] font-bold px-2 py-0.5 rounded-md" :class="getMethodClass(tool.method)">
                      {{ tool.method }}
                    </span>
                    <span class="text-[11px] font-mono text-[var(--text-primary)] flex-1 truncate">{{ tool.url_pattern }}</span>
                    <span class="shrink-0 rounded-md border px-2 py-0.5 text-[10px] font-bold" :class="getConfidenceClass(tool.confidence)">
                      {{ getConfidenceLabelWithScore(tool.confidence, tool.score) }}
                    </span>
                    <ChevronDown :size="16" class="text-[var(--text-tertiary)] transition-transform" :class="expandedToolId === tool.id ? 'rotate-180' : ''" />
                  </div>

                  <div v-if="expandedToolId === tool.id" class="border-t border-slate-100 dark:border-white/10 px-4 py-4 bg-white dark:bg-transparent">
                    <p class="text-xs text-[var(--text-secondary)] mb-2 font-medium">{{ tool.description }}</p>
                    <div v-if="tool.confidence_reasons?.length" class="mb-3 flex flex-wrap gap-1.5">
                      <span
                        v-for="reason in tool.confidence_reasons"
                        :key="reason"
                        class="rounded-md bg-slate-100 px-2 py-1 text-[10px] font-medium text-[var(--text-secondary)] dark:bg-white/10"
                      >
                        {{ reason }}
                      </span>
                    </div>
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
            </template>
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
      <div class="relative z-10 flex w-full max-w-md flex-col overflow-hidden rounded-3xl border border-slate-200 bg-[#f5f7fb] shadow-2xl dark:border-white/10 dark:bg-[#101115] max-h-[94vh]">
        <div class="flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-6 py-5 dark:border-white/10 dark:bg-white/[0.055]">
          <div>
            <h2 class="text-xl font-black text-[var(--text-primary)]">保存为 MCP 工具</h2>
            <p class="mt-1 text-sm text-[var(--text-tertiary)]">将采用的 API 接口打包成 MCP</p>
          </div>
          <button
            class="rounded-xl p-2 text-[var(--text-tertiary)] transition hover:bg-slate-100 hover:text-[var(--text-primary)] dark:hover:bg-white/10"
            @click="publishDialogOpen = false"
          >
            <X :size="18" />
          </button>
        </div>
        <div class="flex-1 overflow-y-auto min-h-0 space-y-4 p-6">
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
          <section class="rounded-2xl border border-slate-200 bg-white p-4 dark:border-white/10 dark:bg-white/[0.04]">
            <div class="mb-3 flex items-center justify-between gap-3">
              <div>
                <h3 class="text-sm font-black text-[var(--text-primary)]">{{ t('Auth Configuration') }}</h3>
                <p class="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                  {{ t('Auth profile hint for publish') }}
                </p>
              </div>
              <Loader2 v-if="isLoadingAuthProfile" class="animate-spin text-sky-500" :size="16" />
            </div>
            <div v-if="authProfile" class="mb-4 rounded-xl bg-slate-50 px-3 py-2 text-xs text-[var(--text-secondary)] dark:bg-white/5">
              {{ t('Detected {count} sensitive headers', { count: authProfile.sensitive_header_count }) }}
            </div>
            <label class="mb-3 flex flex-col gap-2">
              <span class="text-sm font-bold text-[var(--text-secondary)]">{{ t('Credential Type') }}</span>
              <select v-model="publishAuth.credential_type" class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5">
                <option v-for="option in API_MONITOR_CREDENTIAL_TYPE_OPTIONS" :key="option.value" :value="option.value">
                  {{ t(option.labelKey) }}
                </option>
              </select>
            </label>
            <label class="flex flex-col gap-2">
              <span class="text-sm font-bold text-[var(--text-secondary)]">{{ t('Credential') }}</span>
              <select v-model="publishAuth.credential_id" class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5">
                <option value="">{{ t('Skip credential') }}</option>
                <option v-for="credential in publishCredentials" :key="credential.id" :value="credential.id">
                  {{ credential.name }} ({{ credential.username || credential.domain || credential.id }})
                </option>
              </select>
            </label>
            <label v-if="publishAuth.credential_type === 'test'" class="mt-3 flex flex-col gap-2">
              <span class="text-sm font-bold text-[var(--text-secondary)]">{{ t('Login URL') }}</span>
              <input v-model="publishAuth.login_url" class="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-[var(--text-primary)] outline-none transition focus:border-sky-400 focus:ring-1 focus:ring-sky-400/30 dark:border-white/10 dark:bg-white/5 font-mono" :placeholder="t('Login URL placeholder')" />
            </label>
          </section>

          <!-- Token Flow Detection -->
          <section v-if="tokenFlowProfile.length > 0" class="rounded-2xl border border-sky-200 bg-sky-50/50 p-4 dark:border-sky-800/50 dark:bg-sky-950/20">
            <div class="mb-3 flex items-center gap-2">
              <div class="flex h-5 w-5 items-center justify-center rounded-full bg-sky-500 text-[10px] font-bold text-white">
                <svg class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" /></svg>
              </div>
              <h3 class="text-sm font-black text-[var(--text-primary)]">{{ t('Dynamic Token Flow Detected') }}</h3>
            </div>
            <p class="mb-3 text-xs text-[var(--text-tertiary)]">{{ t('Token flow detection hint') }}</p>
            <div class="space-y-2">
              <div
                v-for="flow in tokenFlowProfile"
                :key="flow.id"
                class="flex items-start gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 dark:border-white/10 dark:bg-white/[0.04]"
              >
                <input
                  type="checkbox"
                  v-model="tokenFlowSelections[flow.id]"
                  class="mt-0.5 h-4 w-4 rounded border-slate-300 text-sky-500 focus:ring-sky-500"
                />
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-bold text-[var(--text-primary)]">{{ flow.name }}</span>
                    <span
                      class="rounded-md px-1.5 py-0.5 text-[10px] font-bold"
                      :class="{
                        'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400': flow.confidence === 'high',
                        'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400': flow.confidence === 'medium',
                        'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400': flow.confidence === 'low',
                      }"
                    >{{ flow.confidence }}</span>
                  </div>
                  <div class="mt-1 text-xs text-[var(--text-tertiary)]">
                    <div>{{ t('Source') }}: {{ flow.producer_summary }}</div>
                    <div v-for="cs in flow.consumer_summaries" :key="cs">{{ t('Inject to') }}: {{ cs }}</div>
                    <div v-if="flow.sample_count && flow.sample_count > 1" class="mt-1 text-[11px] opacity-70">
                      {{ t('Samples: {count}', { count: flow.sample_count }) }}
                    </div>
                  </div>
                  <div class="mt-2 flex gap-2">
                    <button
                      type="button"
                      class="rounded-lg border border-slate-200 px-2 py-1 text-xs font-bold text-[var(--text-secondary)] transition hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/10"
                      @click="beginEditTokenFlow(flow)"
                    >
                      {{ t('Edit Token Flow') }}
                    </button>
                    <button
                      v-if="flow.id in tokenFlowDrafts"
                      type="button"
                      class="rounded-lg border border-slate-200 px-2 py-1 text-xs font-bold text-[var(--text-tertiary)] transition hover:bg-slate-50 dark:border-white/10 dark:hover:bg-white/10"
                      @click="resetTokenFlowDraft(flow.id)"
                    >
                      {{ t('Reset') }}
                    </button>
                  </div>
                  <label v-if="flow.id in tokenFlowDrafts" class="mt-2 block">
                    <textarea
                      v-model="tokenFlowDrafts[flow.id]"
                      class="h-40 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-mono text-xs"
                    />
                    <span v-if="tokenFlowDraftErrors[flow.id]" class="mt-1 block text-xs text-red-500">
                      {{ tokenFlowDraftErrors[flow.id] }}
                    </span>
                  </label>
                </div>
              </div>
            </div>

            <!-- Manual Token Flows -->
            <label class="mt-3 block">
              <span class="mb-1 block text-xs font-medium text-[var(--text-secondary)]">
                {{ t('Manual token flows JSON') }}
              </span>
              <textarea
                v-model="manualTokenFlowJson"
                class="h-32 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 font-mono text-xs"
                :placeholder="t('Paste manual token flow JSON')"
              />
              <span v-if="manualTokenFlowJsonError" class="mt-1 block text-xs text-red-500">
                {{ manualTokenFlowJsonError }}
              </span>
            </label>
          </section>
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
