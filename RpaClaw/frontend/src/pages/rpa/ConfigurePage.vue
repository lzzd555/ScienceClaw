<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import {
  Settings,
  Code,
  Play,
  ChevronRight,
  Tag,
  ChevronDown,
  ChevronUp,
} from 'lucide-vue-next';
import { apiClient } from '@/api/client';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';

const router = useRouter();
const route = useRoute();

const sessionId = computed(() => typeof route.query.sessionId === 'string' ? route.query.sessionId : '');
const loading = ref(true);
const loadFailed = ref(false);
const error = ref<string | null>(null);

interface ParsedLocator {
  method?: string;
  role?: string;
  name?: string;
  value?: string;
  parent?: ParsedLocator;
  child?: ParsedLocator;
  base?: ParsedLocator;
  index?: number;
  locator?: ParsedLocator;
}

interface LocatorCandidate {
  kind?: string;
  score?: number;
  selected?: boolean;
  reason?: string;
  strict_match_count?: number;
  visible_match_count?: number;
  locator?: ParsedLocator | string | null;
}

interface StepValidation {
  status?: string;
  details?: string;
}

interface StepItem {
  id: string;
  action: string;
  target?: ParsedLocator | string | null;
  frame_path?: string[];
  locator_candidates?: LocatorCandidate[];
  validation?: StepValidation;
  value?: string;
  description?: string;
  label?: string;
  sensitive?: boolean;
  url?: string;
  prompt?: string;
  output_variable?: string;
  include_page_context?: boolean;
  ai_mode?: string;
  ai_result_mode?: string;
  operation_code?: string;
  operation_summary?: string;
  data_prompt?: string;
  data_value?: string;
  data_summary?: string;
  data_format?: string;
  source?: string;
  replay_mode?: string;  // "ai" | "code"
  status?: 'pending' | 'error' | 'completed';
  localOnly?: boolean;
  macro_step_index?: number;
  macro_step_type?: string;  // "locate" | "operate" | "extract"
  macro_step_desc?: string;
}

interface ParamItem {
  id: string;
  name: string;
  label: string;
  original_value: string;
  current_value: string;
  enabled: boolean;
  step_id: string;
  sensitive: boolean;
  credential_id: string;
}

interface CredentialItem {
  id: string;
  name?: string;
  username?: string;
}

const steps = ref<StepItem[]>([]);
const skillName = ref('');
const skillDescription = ref('');
const generatedScript = ref('');
const params = ref<ParamItem[]>([]);
const credentials = ref<CredentialItem[]>([]);
const promotingStepIndex = ref<number | null>(null);
const expandedStepIndex = ref<number | null>(null);
const isScriptDrawerOpen = ref(false);

const createPendingAIStep = (prompt: string, outputVariable: string): StepItem => ({
  id: `pending-ai-${Date.now()}`,
  action: 'ai_command',
  description: 'AI 命令（执行中）',
  prompt,
  output_variable: outputVariable,
  source: 'ai',
  status: 'pending',
  localOnly: true,
});

const hasAIOperation = (step: StepItem) => Boolean(step.operation_code || step.operation_summary);
const hasAIData = (step: StepItem) => Boolean(step.data_value || step.data_summary || step.output_variable);

const parseLocator = (raw: unknown): ParsedLocator | null => {
  if (!raw) return null;
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw);
    } catch {
      return { method: 'css', value: raw };
    }
  }
  return raw as ParsedLocator;
};

const shortenText = (value: string, max = 48): string => {
  if (!value) return '';
  return value.length > max ? `${value.slice(0, Math.max(0, max - 1))}…` : value;
};

const getNthBaseLocator = (locator: ParsedLocator) => locator.locator || locator.base;

const formatLocator = (raw: unknown): string => {
  const locator = parseLocator(raw);
  if (!locator) return '无定位器';
  if (locator.method === 'role') {
    return locator.name ? `role=${locator.role}[name="${locator.name}"]` : `role=${locator.role}`;
  }
  if (locator.method === 'nested') {
    return `${formatLocator(locator.parent)} >> ${formatLocator(locator.child)}`;
  }
  if (locator.method === 'nth') {
    const baseLocator = getNthBaseLocator(locator);
    const prefix = baseLocator ? `${formatLocator(baseLocator)} >> ` : '';
    return `${prefix}nth=${locator.index}`;
  }
  if (locator.method === 'css') return locator.value || 'css';
  return `${locator.method || 'locator'}:${locator.value || locator.name || ''}`;
};

const formatFramePath = (framePath?: string[]) => {
  if (!framePath?.length) return '主框架';
  return framePath.join(' -> ');
};

const VALIDATION_LABELS: Record<string, string> = {
  ok: 'Strict match',
  ambiguous: 'Ambiguous / not unique',
  fallback: 'Fallback',
  warning: 'Warning',
  broken: 'Broken',
};

const VALIDATION_CLASS_MAP: Record<string, string> = {
  ok: 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200',
  ambiguous: 'bg-amber-100 text-amber-700 ring-1 ring-amber-200',
  fallback: 'bg-amber-100 text-amber-700 ring-1 ring-amber-200',
  warning: 'bg-amber-100 text-amber-700 ring-1 ring-amber-200',
  broken: 'bg-rose-100 text-rose-700 ring-1 ring-rose-200',
};

const getValidationLabel = (status?: string) => {
  if (!status) return 'Unknown';
  return VALIDATION_LABELS[status] || status.replace(/_/g, ' ');
};

const getValidationClass = (status?: string) => {
  if (!status) return 'bg-gray-100 text-gray-700 ring-1 ring-gray-200';
  return VALIDATION_CLASS_MAP[status] || 'bg-gray-100 text-gray-700 ring-1 ring-gray-200';
};

const getActionLabel = (action: string) => {
  const map: Record<string, string> = {
    click: '点击',
    fill: '输入',
    press: '按键',
    select: '选择',
    navigate: '打开页面',
    goto: '打开页面',
    navigate_click: '点击后跳转',
    navigate_press: '按键后跳转',
    open_tab_click: '点击新标签',
    switch_tab: '切换标签',
    close_tab: '关闭标签',
    download_click: '点击下载',
    download: '下载',
    ai_command: 'AI 命令',
    ai_script: 'AI 脚本',
    extract_text: '提取文本',
  };
  return map[action] || action;
};

const getActionColor = (action: string) => {
  const map: Record<string, string> = {
    click: 'bg-sky-100 text-sky-700',
    fill: 'bg-emerald-100 text-emerald-700',
    press: 'bg-amber-100 text-amber-700',
    select: 'bg-fuchsia-100 text-fuchsia-700',
    navigate: 'bg-orange-100 text-orange-700',
    goto: 'bg-orange-100 text-orange-700',
    navigate_click: 'bg-indigo-100 text-indigo-700',
    navigate_press: 'bg-cyan-100 text-cyan-700',
    open_tab_click: 'bg-violet-100 text-violet-700',
    switch_tab: 'bg-slate-100 text-slate-700',
    close_tab: 'bg-rose-100 text-rose-700',
    download_click: 'bg-teal-100 text-teal-700',
    download: 'bg-teal-100 text-teal-700',
    ai_command: 'bg-purple-100 text-purple-700',
    ai_script: 'bg-purple-100 text-purple-700',
    extract_text: 'bg-lime-100 text-lime-700',
  };
  return map[action] || 'bg-gray-100 text-gray-700';
};

// Macro step type helpers
const getMacroTypeLabel = (type?: string) => {
  const map: Record<string, string> = {
    locate: '条件查找',
    operate: '页面操作',
    extract: '数据提取',
  };
  return map[type || ''] || '操作';
};

const getMacroTypeColor = (type?: string) => {
  const map: Record<string, string> = {
    locate: 'bg-blue-100 text-blue-700 ring-1 ring-blue-200',
    operate: 'bg-emerald-100 text-emerald-700 ring-1 ring-emerald-200',
    extract: 'bg-purple-100 text-purple-700 ring-1 ring-purple-200',
  };
  return map[type || ''] || 'bg-gray-100 text-gray-700 ring-1 ring-gray-200';
};

// Group steps by macro_step_index for hierarchical display
interface MacroGroup {
  index: number;           // macro step index (or -1 for ungrouped)
  type: string;            // locate | operate | extract
  description: string;     // macro step description
  steps: { step: StepItem; originalIndex: number }[];
  expanded: boolean;
}

const macroExpandedGroups = ref<Set<number>>(new Set());

const toggleMacroGroup = (groupIndex: number) => {
  if (macroExpandedGroups.value.has(groupIndex)) {
    macroExpandedGroups.value.delete(groupIndex);
  } else {
    macroExpandedGroups.value.add(groupIndex);
  }
};

const macroGroups = computed<MacroGroup[]>(() => {
  const hasMacro = steps.value.some(s => s.macro_step_index !== undefined);
  if (!hasMacro) {
    // No macro grouping — single flat group
    return [{
      index: -1,
      type: 'operate',
      description: '',
      steps: steps.value.map((step, i) => ({ step, originalIndex: i })),
      expanded: true,
    }];
  }

  const groups: MacroGroup[] = [];
  const groupMap = new Map<number, MacroGroup>();

  for (let i = 0; i < steps.value.length; i++) {
    const step = steps.value[i];
    const macroIdx = step.macro_step_index ?? -1;

    if (!groupMap.has(macroIdx)) {
      const group: MacroGroup = {
        index: macroIdx,
        type: step.macro_step_type || 'operate',
        description: step.macro_step_desc || '',
        steps: [],
        expanded: macroExpandedGroups.value.has(macroIdx),
      };
      groupMap.set(macroIdx, group);
      groups.push(group);
    }
    groupMap.get(macroIdx)!.steps.push({ step, originalIndex: i });
  }

  return groups;
});

const getValuePreview = (step: StepItem) => {
  if (!step.value) return '';
  const display = step.sensitive ? '******' : String(step.value);
  return shortenText(`值: ${display}`, 28);
};

const getFrameHint = (step: StepItem) => {
  if (!step.frame_path?.length) return '';
  return `iframe ${step.frame_path.length} 层`;
};

const getSelectedCandidate = (step: StepItem): LocatorCandidate | null => {
  const candidates = step.locator_candidates || [];
  return candidates.find((candidate) => candidate.selected) || candidates[0] || null;
};

const formatCandidateMatchText = (candidate: LocatorCandidate): string => {
  const strictCount = candidate.strict_match_count;
  const visibleCount = candidate.visible_match_count;

  if (typeof strictCount === 'number' && strictCount > 0) {
    return strictCount === 1 ? 'strict match' : `${strictCount} strict matches`;
  }

  if (typeof visibleCount === 'number') {
    const plural = visibleCount === 1 ? '' : 'es';
    return `${visibleCount} visible match${plural}`;
  }

  if (typeof strictCount === 'number') {
    const plural = strictCount === 1 ? '' : 'es';
    return `${strictCount} strict match${plural}`;
  }

  return '';
};

const getCandidateSummary = (step: StepItem) => {
  const candidates = step.locator_candidates || [];
  const total = candidates.length;
  if (!total) return '';
  const selected = getSelectedCandidate(step);
  if (!selected) return `${total} candidate${total === 1 ? '' : 's'}`;

  const summary: string[] = [];
  if (selected.kind) summary.push(`Current ${selected.kind}`);
  const matchText = formatCandidateMatchText(selected);
  if (matchText) summary.push(matchText);
  summary.push(`${total} candidate${total === 1 ? '' : 's'}`);

  return summary.join(' · ');
};

const getStepTitle = (step: StepItem) => {
  if (step.description) return step.description;
  return `${getActionLabel(step.action)} ${formatLocator(step.target || step.label || '')}`;
};

const getStepLocatorSummary = (step: StepItem) => shortenText(formatLocator(step.target || step.label || ''), 72);

const toggleStep = (index: number) => {
  expandedStepIndex.value = expandedStepIndex.value === index ? null : index;
};

const promoteLocator = async (stepIndex: number, candidateIndex: number) => {
  if (!sessionId.value || promotingStepIndex.value !== null) return;
  promotingStepIndex.value = stepIndex;
  error.value = null;
  try {
    await apiClient.post(`/rpa/session/${sessionId.value}/step/${stepIndex}/locator`, {
      candidate_index: candidateIndex,
    });
    await loadSession();
    expandedStepIndex.value = stepIndex;
  } catch (err: any) {
    error.value = `切换定位器失败: ${err.response?.data?.detail || err.message}`;
  } finally {
    promotingStepIndex.value = null;
  }
};

const toggleReplayMode = async (stepIndex: number, mode: string) => {
  if (!sessionId.value) return;
  error.value = null;
  const step = steps.value[stepIndex];
  try {
    // Handle ai_command ↔ ai_script conversion
    if (step.action === 'ai_command' && mode === 'code') {
      // ai_command → ai_script: convert AI result data into fixed script
      const scriptValue = (step as any).operation_code
        || (step as any).data_value
        || `# ${step.description || (step as any).prompt || ''}`;
      step.action = 'ai_script';
      (step as any).value = scriptValue;
      (step as any).source = 'ai';
      step.replay_mode = 'code';
      // Remove ai_command-specific fields
      delete (step as any).ai_mode;
      delete (step as any).ai_result_mode;
      delete (step as any).data_prompt;
      delete (step as any).data_value;
      delete (step as any).data_summary;
      delete (step as any).data_context_mode;
      delete (step as any).operation_code;
      delete (step as any).operation_summary;
      await apiClient.patch(`/rpa/session/${sessionId.value}/step/${stepIndex}`, step);
      return;
    }
    if (step.action === 'ai_script' && mode === 'ai') {
      // ai_script → ai_command: convert script description into AI command
      const hasData = Boolean((step as any).output_variable || (step as any).data_value);
      step.action = 'ai_command';
      (step as any).ai_mode = hasData ? 'data' : 'execute';
      (step as any).ai_result_mode = hasData ? 'data_only' : 'operation_only';
      (step as any).data_prompt = (step as any).prompt || step.description;
      (step as any).data_context_mode = 'page';
      step.replay_mode = 'ai';
      // Remove ai_script-specific fields
      delete (step as any).value;
      await apiClient.patch(`/rpa/session/${sessionId.value}/step/${stepIndex}`, step);
      return;
    }
    await apiClient.patch(`/rpa/session/${sessionId.value}/step/${stepIndex}/replay-mode`, {
      replay_mode: mode,
    });
    steps.value[stepIndex].replay_mode = mode;
  } catch (err: any) {
    error.value = `切换回放模式失败: ${err.response?.data?.detail || err.message}`;
  }
};

const loadCredentials = async () => {
  try {
    const resp = await apiClient.get('/credentials');
    credentials.value = resp.data.credentials || [];
  } catch {
    // Credentials are optional for this page.
  }
};

const KEYWORD_MAP: Record<string, string> = {
  邮箱: 'email', 邮件: 'email', email: 'email', 'e-mail': 'email',
  密码: 'password', password: 'password', pwd: 'password',
  用户名: 'username', 用户: 'username', username: 'username', user: 'username',
  账号: 'account', account: 'account',
  手机: 'phone', 电话: 'phone', phone: 'phone', tel: 'phone', mobile: 'phone',
  验证码: 'captcha', captcha: 'captcha', code: 'code',
  搜索: 'search', search: 'search',
  地址: 'address', address: 'address', url: 'url',
  姓名: 'name', name: 'name',
};

function deriveParamName(loc: ParsedLocator | null, sensitive: boolean): string {
  if (!loc) return sensitive ? 'password' : '';
  if (sensitive) return 'password';

  const candidates: string[] = [];
  if (loc.name) candidates.push(loc.name);
  if (loc.value && loc.method !== 'css') candidates.push(loc.value);
  if (loc.role) candidates.push(loc.role);

  for (const text of candidates) {
    const lower = text.toLowerCase().trim();
    for (const [keyword, paramName] of Object.entries(KEYWORD_MAP)) {
      if (lower.includes(keyword)) return paramName;
    }

    const ascii = lower
      .replace(/[^a-z0-9_]/g, '_')
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '');
    if (ascii && ascii.length >= 2 && ascii.length <= 30 && /^[a-z]/.test(ascii)) {
      return ascii;
    }
  }
  return '';
}

const loadSession = async () => {
  if (!sessionId.value) {
    error.value = '缺少 sessionId 参数';
    loadFailed.value = true;
    loading.value = false;
    return;
  }

  try {
    const resp = await apiClient.get(`/rpa/session/${sessionId.value}`);
    const session = resp.data.session;
    steps.value = (session.steps || []) as StepItem[];
    loadFailed.value = false;
    error.value = null;

    const usedNames = new Set<string>();
    params.value = steps.value
      .filter((step) => step.action === 'fill' || step.action === 'select')
      .map((step, index) => {
        let label = `参数${index + 1}`;
        let semanticName = '';

        try {
          const loc = parseLocator(step.target);
          if (loc?.name) label = loc.name;
          else if (loc?.value) label = loc.value;
          semanticName = deriveParamName(loc, !!step.sensitive);
        } catch {
          // Fall back to generated defaults.
        }

        let name = semanticName || `param_${index}`;
        if (usedNames.has(name)) {
          let suffix = 2;
          while (usedNames.has(`${name}_${suffix}`)) suffix++;
          name = `${name}_${suffix}`;
        }
        usedNames.add(name);

        return {
          id: `param_${index}`,
          name,
          label,
          original_value: step.value || '',
          current_value: step.value || '',
          enabled: true,
          step_id: step.id,
          sensitive: !!step.sensitive,
          credential_id: '',
        };
      });

    const navStep = steps.value.find((step) => !!step.url);
    if (navStep?.url) {
      try {
        const url = new URL(navStep.url);
        skillName.value = `${url.hostname} 自动化`;
      } catch {
        skillName.value = '录制技能';
      }
    } else {
      skillName.value = '录制技能';
    }
    skillDescription.value = `自动执行 ${steps.value.length} 个录制步骤`;
  } catch (err: any) {
    error.value = `加载会话失败: ${err.response?.data?.detail || err.message}`;
    if (!steps.value.length) loadFailed.value = true;
  } finally {
    loading.value = false;
  }
};

const buildParamMap = () => {
  const paramMap: Record<string, any> = {};
  params.value
    .filter((param) => param.enabled)
    .forEach((param) => {
      paramMap[param.name] = {
        original_value: param.original_value,
        sensitive: param.sensitive || false,
        credential_id: param.credential_id || '',
      };
    });
  return paramMap;
};

const generateScript = async () => {
  try {
    error.value = null;
    const resp = await apiClient.post(`/rpa/session/${sessionId.value}/generate`, {
      params: buildParamMap(),
    });
    generatedScript.value = resp.data.script || '';
    isScriptDrawerOpen.value = true;
  } catch (err: any) {
    isScriptDrawerOpen.value = false;
    generatedScript.value = '';
    error.value = `生成脚本失败: ${err.response?.data?.detail || err.message}`;
  }
};

const goToTest = () => {
  router.push({
    path: '/rpa/test',
    query: {
      sessionId: sessionId.value,
      skillName: skillName.value,
      skillDescription: skillDescription.value,
      params: JSON.stringify(buildParamMap()),
    },
  });
};

onMounted(() => {
  loadSession();
  loadCredentials();
});
</script>

<template>
  <div class="min-h-screen bg-[#f5f6f7] text-gray-900">
    <header class="sticky top-0 z-30 border-b border-gray-200 bg-white/90 backdrop-blur-xl">
      <div class="mx-auto flex max-w-[1440px] items-center gap-4 px-4 py-4 sm:px-6 lg:px-8">
        <div class="flex min-w-0 items-center gap-3">
          <div class="flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-[#831bd7] to-[#ac0089] text-white shadow-lg shadow-[#831bd7]/20">
            <Settings :size="20" />
          </div>
          <div class="min-w-0">
            <h1 class="truncate text-lg font-extrabold tracking-tight sm:text-xl">配置技能</h1>
          </div>
        </div>

        <div class="ml-auto flex items-center gap-2">
          <button
            type="button"
            @click="generateScript"
            class="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50"
          >
            <Code :size="16" />
            预览脚本
          </button>
          <button
            type="button"
            @click="goToTest"
            class="inline-flex items-center gap-2 rounded-full bg-gradient-to-r from-[#831bd7] to-[#ac0089] px-5 py-2 text-sm font-bold text-white shadow-lg shadow-[#831bd7]/20 transition-opacity hover:opacity-95"
          >
            <Play :size="16" />
            开始测试
            <ChevronRight :size="16" />
          </button>
        </div>
      </div>
    </header>

    <div v-if="loading" class="flex h-64 items-center justify-center">
      <p class="text-sm text-gray-500">加载中...</p>
    </div>

    <div v-else-if="loadFailed" class="flex h-64 items-center justify-center px-6">
      <div class="rounded-2xl border border-rose-200 bg-white px-6 py-5 text-sm text-rose-600 shadow-sm">
        {{ error || '页面加载失败' }}
      </div>
    </div>

    <main v-else class="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
      <div v-if="error" class="mb-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        {{ error }}
      </div>

      <div class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section class="space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 class="text-xl font-extrabold tracking-tight">录制步骤</h2>
              <p class="text-sm text-gray-500">默认显示摘要信息，点击任一步骤可展开查看详细定位诊断。</p>
            </div>
            <div class="rounded-full bg-white px-4 py-1.5 text-xs font-bold text-[#831bd7] shadow-sm ring-1 ring-[#831bd7]/10">
              共 {{ steps.length }} 步
            </div>
          </div>

          <div class="space-y-3">
            <!-- Macro-grouped step rendering -->
            <template v-for="(group, gIdx) in macroGroups" :key="gIdx">
              <!-- Macro group header (only shown when there are multiple groups) -->
              <div
                v-if="macroGroups.length > 1 && group.steps.length > 0"
                class="cursor-pointer rounded-2xl border bg-gray-50 px-4 py-3 transition-all"
                :class="group.expanded ? 'border-gray-300 shadow-sm' : 'border-gray-200 hover:border-gray-300'"
                @click="toggleMacroGroup(group.index)"
              >
                <div class="flex items-center gap-3">
                  <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl text-xs font-extrabold bg-white text-gray-600 shadow-sm">
                    {{ String(gIdx + 1).padStart(2, '0') }}
                  </div>
                  <span
                    class="rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide"
                    :class="getMacroTypeColor(group.type)"
                  >
                    {{ getMacroTypeLabel(group.type) }}
                  </span>
                  <span class="flex-1 text-sm font-semibold text-gray-800 truncate">{{ group.description }}</span>
                  <span class="text-[10px] text-gray-400">{{ group.steps.length }} 个操作</span>
                  <component :is="group.expanded ? ChevronUp : ChevronDown" :size="16" class="text-gray-400" />
                </div>
              </div>

              <!-- Steps inside the macro group -->
              <template v-if="macroGroups.length <= 1 || group.expanded">
                <article
                  v-for="{ step, originalIndex: idx } in group.steps"
                  :key="step.id"
                  class="overflow-hidden rounded-3xl border bg-white shadow-sm transition-all"
                  :class="{
                    'border-[#831bd7]/30 shadow-lg shadow-[#831bd7]/10': expandedStepIndex === idx,
                    'border-gray-200': expandedStepIndex !== idx,
                    'ml-4 border-l-2': macroGroups.length > 1,
                    'border-l-blue-300': macroGroups.length > 1 && group.type === 'locate',
                    'border-l-emerald-300': macroGroups.length > 1 && group.type === 'operate',
                    'border-l-purple-300': macroGroups.length > 1 && group.type === 'extract',
                  }"
                >
                  <div
                    class="cursor-pointer px-4 py-4 sm:px-5"
                    @click="toggleStep(idx)"
                  >
                    <div class="flex items-start gap-4">
                      <div
                        class="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl text-xs font-extrabold"
                        :class="expandedStepIndex === idx ? 'bg-[#831bd7] text-white' : 'bg-gray-100 text-gray-500'"
                      >
                        {{ String(idx + 1).padStart(2, '0') }}
                      </div>

                      <div class="min-w-0 flex-1">
                        <div class="flex flex-wrap items-center gap-2">
                          <span
                            class="rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide"
                            :class="getActionColor(step.action)"
                          >
                            {{ getActionLabel(step.action) }}
                          </span>
                          <span
                            v-if="step.status === 'pending' || step.status === 'error'"
                            class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                            :class="step.status === 'pending' ? 'bg-amber-100 text-amber-700' : 'bg-rose-100 text-rose-700'"
                          >
                            {{ step.status === 'pending' ? '执行中' : '失败' }}
                          </span>
                          <span
                            v-if="step.validation?.status"
                            class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                            :class="getValidationClass(step.validation.status)"
                          >
                            {{ getValidationLabel(step.validation.status) }}
                          </span>
                          <span
                            v-if="getFrameHint(step)"
                            class="rounded-full bg-violet-50 px-2.5 py-1 text-[10px] font-semibold text-violet-700 ring-1 ring-violet-100"
                          >
                            {{ getFrameHint(step) }}
                          </span>
                          <span
                            v-if="step.replay_mode === 'ai'"
                            class="rounded-full bg-gradient-to-r from-purple-500 to-pink-500 px-2.5 py-1 text-[10px] font-bold text-white"
                          >
                            AI 动态
                          </span>
                        </div>

                        <h3 class="mt-2 text-sm font-bold text-gray-900 sm:text-[15px]">
                          {{ getStepTitle(step) }}
                        </h3>

                        <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-gray-500">
                          <span class="min-w-0 max-w-full truncate font-mono text-gray-600">
                            {{ getStepLocatorSummary(step) }}
                          </span>
                          <span v-if="getValuePreview(step)">{{ getValuePreview(step) }}</span>
                          <span v-if="getCandidateSummary(step)">{{ getCandidateSummary(step) }}</span>
                        </div>
                      </div>

                      <button
                        type="button"
                        class="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl border border-gray-200 bg-white text-gray-500 transition-colors hover:bg-gray-50"
                        @click.stop="toggleStep(idx)"
                      >
                        <ChevronUp v-if="expandedStepIndex === idx" :size="18" />
                        <ChevronDown v-else :size="18" />
                      </button>
                    </div>
                  </div>

                  <div
                    v-if="expandedStepIndex === idx"
                    class="border-t border-gray-100 bg-[#faf7fd] px-4 py-4 sm:px-5"
                    @click.stop
                  >
                    <div class="grid gap-3 rounded-2xl bg-white p-4 ring-1 ring-[#831bd7]/10">
                      <div class="grid gap-2 text-sm text-gray-600">
                        <!-- AI Command specific fields -->
                        <template v-if="step.action === 'ai_command'">
                          <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                            <span class="text-xs font-bold uppercase tracking-wide text-gray-400">回放模式</span>
                            <div class="flex items-center gap-1">
                              <button
                                type="button"
                                class="rounded-l-lg px-3 py-1.5 text-xs font-semibold transition-colors"
                                :class="step.replay_mode === 'code' ? 'bg-purple-700 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'"
                                @click.stop="toggleReplayMode(idx, 'code')"
                              >固定代码</button>
                              <button
                                type="button"
                                class="rounded-r-lg px-3 py-1.5 text-xs font-semibold transition-colors"
                                :class="step.replay_mode !== 'code' ? 'bg-gradient-to-r from-purple-600 to-pink-500 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'"
                                @click.stop="toggleReplayMode(idx, 'ai')"
                              >AI 动态</button>
                              <span class="ml-2 text-[10px] text-gray-400">{{ step.replay_mode === 'code' ? '直接执行录制期生成的 Playwright 代码' : '运行时由 AI 根据当前页面状态生成操作' }}</span>
                            </div>
                          </div>
                          <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                            <span class="text-xs font-bold uppercase tracking-wide text-gray-400">提示词</span>
                            <span class="break-all text-xs text-gray-700 whitespace-pre-wrap">{{ (step as any).prompt || step.description }}</span>
                          </div>
                          <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                            <span class="text-xs font-bold uppercase tracking-wide text-gray-400">结果类型</span>
                            <div class="flex flex-wrap gap-2 text-xs">
                              <span v-if="hasAIOperation(step)" class="rounded bg-orange-100 px-1.5 py-0.5 font-semibold text-orange-700">操作</span>
                              <span v-if="hasAIData(step)" class="rounded bg-blue-100 px-1.5 py-0.5 font-semibold text-blue-700">数据</span>
                              <span v-if="!hasAIOperation(step) && !hasAIData(step)" class="text-gray-500">无显式结果</span>
                            </div>
                          </div>
                          <div v-if="step.output_variable" class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                            <span class="text-xs font-bold uppercase tracking-wide text-gray-400">输出变量</span>
                            <span class="break-all font-mono text-xs text-purple-700">{{ step.output_variable }}</span>
                          </div>
                          <div v-if="hasAIOperation(step)" class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                            <span class="text-xs font-bold uppercase tracking-wide text-gray-400">操作结果</span>
                        <div class="rounded-lg bg-orange-50 p-2">
                          <p v-if="step.operation_summary" class="break-all text-xs text-gray-700 whitespace-pre-wrap">{{ step.operation_summary }}</p>
                          <pre v-if="step.operation_code" class="mt-1 break-all text-xs text-gray-700 whitespace-pre-wrap font-mono">{{ step.operation_code }}</pre>
                        </div>
                      </div>
                      <div v-if="hasAIData(step)" class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                        <span class="text-xs font-bold uppercase tracking-wide text-gray-400">数据结果</span>
                        <div class="rounded-lg bg-blue-50 p-2">
                          <p v-if="step.data_prompt" class="text-[11px] text-blue-700">提取指令: {{ step.data_prompt }}</p>
                          <p v-if="step.data_summary" class="mt-1 break-all text-xs text-gray-700 whitespace-pre-wrap">{{ step.data_summary }}</p>
                          <pre v-if="step.data_format === 'json' && step.data_value" class="mt-1 break-all text-xs text-gray-700 whitespace-pre-wrap font-mono">{{ step.data_value }}</pre>
                          <span v-else-if="step.data_value" class="mt-1 block break-all text-xs text-gray-700 whitespace-pre-wrap">{{ step.data_value }}</span>
                        </div>
                      </div>
                    </template>
                    <!-- Regular step fields (hidden for ai_command) -->
                    <template v-if="step.action !== 'ai_command'">
                    <!-- Replay mode toggle for AI-sourced steps -->
                    <div v-if="step.source === 'ai' && ['click', 'fill', 'press', 'extract_text', 'navigate_click'].includes(step.action)" class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                      <span class="text-xs font-bold uppercase tracking-wide text-gray-400">回放模式</span>
                      <div class="flex items-center gap-1">
                        <button
                          type="button"
                          class="rounded-l-lg px-3 py-1.5 text-xs font-semibold transition-colors"
                          :class="step.replay_mode !== 'ai' ? 'bg-purple-700 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'"
                          @click.stop="toggleReplayMode(idx, 'code')"
                        >固定代码</button>
                        <button
                          type="button"
                          class="rounded-r-lg px-3 py-1.5 text-xs font-semibold transition-colors"
                          :class="step.replay_mode === 'ai' ? 'bg-gradient-to-r from-purple-600 to-pink-500 text-white' : 'bg-gray-100 text-gray-500 hover:bg-gray-200'"
                          @click.stop="toggleReplayMode(idx, 'ai')"
                        >AI 动态</button>
                        <span class="ml-2 text-[10px] text-gray-400">{{ step.replay_mode === 'ai' ? '运行时由 AI 判断执行' : '使用录制时的固定选择器' }}</span>
                      </div>
                    </div>
                    <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                      <span class="text-xs font-bold uppercase tracking-wide text-gray-400">主定位器</span>
                      <span class="break-all font-mono text-xs text-gray-700">{{ formatLocator(step.target) }}</span>
                    </div>
                    <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                      <span class="text-xs font-bold uppercase tracking-wide text-gray-400">框架层级</span>
                      <span class="break-all font-mono text-xs text-gray-700">{{ formatFramePath(step.frame_path) }}</span>
                    </div>
                    <div class="grid gap-1 sm:grid-cols-[92px_minmax(0,1fr)]">
                      <span class="text-xs font-bold uppercase tracking-wide text-gray-400">校验结果</span>
                      <div class="flex flex-wrap items-center gap-2">
                        <span
                          v-if="step.validation?.status"
                          class="rounded-full px-2.5 py-1 text-[10px] font-semibold"
                          :class="getValidationClass(step.validation.status)"
                        >
                          {{ getValidationLabel(step.validation.status) }}
                        </span>
                        <span class="text-xs text-gray-600">{{ step.validation?.details || '无额外说明' }}</span>
                      </div>
                    </div>
                    </template>
                  </div>
                  <div v-if="step.locator_candidates?.length" class="space-y-2">
                    <div class="flex items-center justify-between">
                      <p class="text-sm font-bold text-gray-900">候选定位器</p>
                      <p class="text-xs text-gray-400">只在当前展开步骤中显示完整列表</p>
                    </div>

                    <div class="space-y-2">
                      <div
                        v-for="(candidate, candidateIndex) in step.locator_candidates"
                        :key="`${step.id}-${candidateIndex}`"
                        class="flex flex-col gap-2 rounded-2xl border px-3 py-3 md:flex-row md:items-start md:justify-between md:gap-4"
                        :class="candidate.selected ? 'border-[#831bd7]/30 bg-[#fbf7ff]' : 'border-gray-200 bg-white'"
                      >
                        <div class="min-w-0 flex-1">
                          <div class="flex flex-wrap items-center gap-2 text-[11px]">
                            <span class="rounded-full bg-gray-100 px-2 py-0.5 font-semibold uppercase tracking-wide text-gray-600">
                              {{ candidate.kind || 'locator' }}
                            </span>
                            <span class="text-gray-400">分数 {{ candidate.score ?? '-' }}</span>
                            <span class="text-gray-400">严格 {{ candidate.strict_match_count ?? '-' }}</span>
                            <span
                              v-if="candidate.selected"
                              class="rounded-full bg-[#831bd7] px-2 py-0.5 font-semibold text-white"
                            >
                              当前使用
                            </span>
                          </div>
                          <p class="mt-1 break-all font-mono text-xs text-gray-700">{{ formatLocator(candidate.locator) }}</p>
                          <p v-if="candidate.reason" class="mt-1 text-[11px] text-gray-500">{{ candidate.reason }}</p>
                        </div>

                        <button
                          type="button"
                          class="shrink-0 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors"
                          :class="candidate.selected ? 'cursor-default border-gray-200 text-gray-400' : 'border-[#831bd7]/25 text-[#831bd7] hover:bg-[#831bd7]/5'"
                          :disabled="candidate.selected || promotingStepIndex === idx"
                          @click.stop="promoteLocator(idx, candidateIndex)"
                        >
                          {{ promotingStepIndex === idx ? '切换中...' : (candidate.selected ? '当前使用' : '使用此定位器') }}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </article>
              </template>
            </template>

            <div v-if="steps.length === 0" class="rounded-3xl border border-dashed border-gray-300 bg-white px-6 py-12 text-center text-sm text-gray-400">
              当前没有可配置的录制步骤。
            </div>
          </div>
        </section>

        <aside class="space-y-4 xl:sticky xl:top-24 xl:self-start">
          <section class="rounded-3xl border border-gray-200 bg-white p-5 shadow-sm">
            <div class="flex items-center gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#f4eaff] text-[#831bd7]">
                <Settings :size="18" />
              </div>
              <div>
                <h2 class="text-base font-extrabold">技能信息</h2>
              </div>
            </div>

            <div class="mt-4 space-y-4">
              <div class="space-y-1.5">
                <label class="text-xs font-semibold text-gray-500">技能名称</label>
                <input
                  v-model="skillName"
                  class="w-full rounded-2xl border border-gray-200 bg-[#fafafa] px-3 py-2.5 text-sm outline-none transition-colors focus:border-[#831bd7] focus:bg-white"
                />
              </div>
              <div class="space-y-1.5">
                <label class="text-xs font-semibold text-gray-500">描述</label>
                <textarea
                  v-model="skillDescription"
                  rows="3"
                  class="w-full resize-none rounded-2xl border border-gray-200 bg-[#fafafa] px-3 py-2.5 text-sm outline-none transition-colors focus:border-[#831bd7] focus:bg-white"
                />
              </div>
            </div>
          </section>

          <section class="rounded-3xl border border-gray-200 bg-white p-5 shadow-sm">
            <div class="flex items-start gap-3">
              <div class="flex h-10 w-10 items-center justify-center rounded-2xl bg-[#f4eaff] text-[#831bd7]">
                <Tag :size="18" />
              </div>
              <div class="min-w-0">
                <h2 class="text-base font-extrabold">可配置参数</h2>
              </div>
            </div>

            <div v-if="params.length > 0" class="mt-4 max-h-[calc(100vh-22rem)] space-y-3 overflow-y-auto pr-1">
              <div
                v-for="param in params"
                :key="param.id"
                class="rounded-2xl border border-gray-200 bg-[#fafafa] p-3"
              >
                <div class="flex items-center gap-3">
                  <input
                    v-model="param.enabled"
                    type="checkbox"
                    class="h-4 w-4 rounded border-gray-300 accent-[#831bd7]"
                  />
                  <input
                    v-model="param.name"
                    class="min-w-0 flex-1 border-0 bg-transparent text-sm font-semibold text-gray-800 outline-none"
                    placeholder="参数名"
                  />
                  <span
                    class="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                    :class="param.sensitive ? 'bg-fuchsia-100 text-fuchsia-700' : 'bg-slate-100 text-slate-700'"
                  >
                    {{ param.sensitive ? '敏感' : '普通' }}
                  </span>
                </div>

                <p class="mt-2 text-[11px] text-gray-500">{{ param.label }}</p>

                <div class="mt-3">
                  <select
                    v-if="param.sensitive"
                    v-model="param.credential_id"
                    class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition-colors focus:border-[#831bd7]"
                  >
                    <option value="">选择凭据...</option>
                    <option
                      v-for="cred in credentials"
                      :key="cred.id"
                      :value="cred.id"
                    >
                      {{ cred.name || '未命名凭据' }}{{ cred.username ? ` (${cred.username})` : '' }}
                    </option>
                  </select>
                  <input
                    v-else
                    v-model="param.current_value"
                    class="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm text-gray-700 outline-none transition-colors focus:border-[#831bd7]"
                    placeholder="默认值"
                  />
                </div>
              </div>
            </div>

            <div v-else class="mt-4 rounded-2xl border border-dashed border-gray-200 bg-[#fafafa] px-4 py-6 text-center text-sm text-gray-400">
              当前没有可参数化的输入步骤。
            </div>
          </section>
        </aside>
      </div>
    </main>

    <Dialog :open="isScriptDrawerOpen" @update:open="(open: boolean) => { isScriptDrawerOpen = open }">
      <DialogContent
        class="left-auto right-0 top-0 h-screen w-[min(760px,100vw)] max-h-none max-w-none translate-x-0 translate-y-0 overflow-hidden rounded-none border-l border-gray-200 bg-[#0f1115] p-0"
      >
        <div class="flex h-full flex-col">
          <DialogHeader class="border-b border-white/10 px-6 py-4 text-left">
            <DialogTitle class="flex items-center gap-2 text-base font-bold text-white">
              <Code :size="18" />
              脚本预览
            </DialogTitle>
          </DialogHeader>

          <div class="flex-1 overflow-auto px-6 py-5">
            <pre class="min-h-full overflow-x-auto rounded-2xl bg-black/30 p-4 text-xs leading-6 text-emerald-300"><code>{{ generatedScript }}</code></pre>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  </div>
</template>
