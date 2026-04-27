export type RpaTimelineStatusTone = 'success' | 'danger' | 'warning' | 'active' | 'neutral';

export interface RpaTimelineStatus {
  label: string;
  tone: RpaTimelineStatusTone;
}

export interface RpaStepTimelineItem {
  id: string;
  indexLabel: string;
  actionLabel: string;
  title: string;
  summary: string;
  summaryLabel: string;
  summaryValue: string;
  status: RpaTimelineStatus;
  isAi: boolean;
  isFailed: boolean;
  technical: {
    locator: string;
    frame: string;
    validation: string;
    url: string;
    candidateCount: number;
  };
}

const ACTION_LABELS: Record<string, string> = {
  ai_operation: 'AI 操作',
  check: '勾选',
  click: '点击',
  data_capture: '提取',
  dataflow_fill: '填充',
  fill: '输入',
  goto: '导航',
  input: '输入',
  manual_action: '操作',
  navigate: '导航',
  navigate_click: '点击后跳转',
  navigate_press: '按键后跳转',
  navigation: '导航',
  open_tab_click: '点击新标签',
  press: '按键',
  record: '操作',
  select: '选择',
  switch_tab: '切换标签',
  close_tab: '关闭标签',
  download: '下载',
  download_click: '点击下载',
  trace: '操作',
  type: '输入',
};

const VALIDATION_STATUS: Record<string, RpaTimelineStatus> = {
  accepted: { label: '已确认', tone: 'success' },
  broken: { label: '待修复', tone: 'danger' },
  exact: { label: '已确认', tone: 'success' },
  failed: { label: '待修复', tone: 'danger' },
  ok: { label: '已确认', tone: 'success' },
  strict: { label: '已确认', tone: 'success' },
  unique: { label: '已确认', tone: 'success' },
  warning: { label: '需关注', tone: 'warning' },
};

export function formatRpaActionLabel(action?: string): string {
  if (!action) return '操作';
  const normalized = action.toLowerCase();
  return ACTION_LABELS[normalized] || action.replace(/_/g, ' ');
}

const stringifyLocator = (raw: unknown): string => {
  if (!raw) return '';
  if (typeof raw === 'string') return raw;
  if (typeof raw !== 'object') return String(raw);

  const locator = raw as Record<string, any>;

  if (locator.method === 'nested' || (locator.parent && locator.child)) {
    return `${stringifyLocator(locator.parent)} >> ${stringifyLocator(locator.child)}`;
  }

  if (locator.method === 'nth' || locator.type === 'nth') {
    const baseLocator = locator.locator || locator.base;
    const prefix = baseLocator ? `${stringifyLocator(baseLocator)} >> ` : '';
    return locator.method === 'nth'
      ? `${prefix}nth=${locator.index ?? 0}`
      : `${prefix}nth(${locator.index ?? 0})`;
  }

  if (locator.playwright_locator) return String(locator.playwright_locator);
  if (locator.selector) return String(locator.selector);
  if (locator.method === 'role') return `role=${locator.role || 'element'}${locator.name ? `[name="${locator.name}"]` : ''}`;
  if (locator.method === 'css') return String(locator.value || locator.selector || 'css');
  if (locator.method === 'text') return `text:"${locator.value || locator.text || locator.name || ''}"`;
  if (locator.method === 'label') return `label:"${locator.value || locator.text || locator.name || ''}"`;
  if (locator.method) return `${locator.method}:${locator.value || locator.name || ''}`;
  if (locator.type === 'role') return `role=${locator.role || 'element'}${locator.name ? `[name="${locator.name}"]` : ''}`;
  if (locator.type === 'text') return `text="${locator.text || locator.name || ''}"`;
  if (locator.type === 'label') return `label="${locator.text || locator.name || ''}"`;
  if (locator.type === 'placeholder') return `placeholder="${locator.text || locator.name || ''}"`;
  if (locator.type === 'css') return String(locator.selector || '');
  if (locator.locator) return stringifyLocator(locator.locator);

  try {
    return JSON.stringify(locator);
  } catch {
    return String(locator);
  }
};

export function formatRpaStepLocator(raw: unknown): string {
  return stringifyLocator(raw) || '无定位器';
}

export function formatRpaFramePath(framePath?: unknown): string {
  if (!Array.isArray(framePath) || framePath.length === 0) return 'Main frame';
  if (framePath.length === 1 && String(framePath[0]).toLowerCase() === 'main') return 'Main frame';
  return framePath.map((frame) => String(frame)).join(' > ');
}

export function getRpaStepStatus(params: {
  step: any;
  index: number;
  failedStepIndex?: number | null;
  activeIndex?: number | null;
}): RpaTimelineStatus {
  if (params.failedStepIndex === params.index) {
    return { label: '执行失败', tone: 'danger' };
  }

  if (params.activeIndex === params.index) {
    return { label: '最新', tone: 'active' };
  }

  const rawStatus = String(params.step?.validation?.status || params.step?.validationStatus || '').toLowerCase();
  if (rawStatus && VALIDATION_STATUS[rawStatus]) return VALIDATION_STATUS[rawStatus];
  if (params.step?.status === 'active') return { label: '进行中', tone: 'active' };
  if (params.step?.status === 'completed') return { label: '已记录', tone: 'success' };
  return { label: '已记录', tone: 'neutral' };
}

const cleanText = (value: unknown): string => (
  String(value || '').replace(/\s+/g, ' ').trim()
);

const fallbackTitle = (actionLabel: string, locator: string): string => {
  if (actionLabel === '导航') return '打开页面';
  if (actionLabel === '输入') return '输入内容';
  if (actionLabel === '点击') return locator && locator !== '无定位器' ? '点击页面元素' : '点击';
  if (actionLabel === 'AI 操作') return '执行 AI 操作';
  return `${actionLabel}页面内容`;
};

const buildCollapsedSummary = (params: {
  step: any;
  locator: string;
  valuePreview: string;
  url: string;
}): { label: string; value: string; text: string } => {
  const explicitSummary = cleanText(params.step?.summary);
  const build = (label: string, value: string) => ({
    label,
    value,
    text: label ? `${label}：${value}` : value,
  });

  if (explicitSummary) return build('', explicitSummary);
  if (params.valuePreview) return build('输入值', params.valuePreview);
  if (params.locator && params.locator !== '无定位器') return build('定位器', params.locator);
  if (params.url) return build('页面', params.url);
  return build('', '展开查看高级信息');
};

export function buildRpaStepTimelineItem(params: {
  step: any;
  index: number;
  failedStepIndex?: number | null;
  activeIndex?: number | null;
}): RpaStepTimelineItem {
  const { step, index, failedStepIndex, activeIndex } = params;
  const actionLabel = formatRpaActionLabel(step?.action || step?.label || step?.source);
  const locator = formatRpaStepLocator(step?.target || step?.locator || step?.locatorSummary);
  const title = cleanText(step?.title || step?.description || step?.label)
    || fallbackTitle(actionLabel, locator);
  const valuePreview = cleanText(step?.value);
  const validationDetails = cleanText(step?.validation?.details || step?.validationDetails);
  const url = cleanText(step?.url);
  const summary = buildCollapsedSummary({ step, locator, valuePreview, url });

  const source = String(step?.source || '').toLowerCase();
  const isAi = source === 'ai' || step?.action === 'ai_operation';

  return {
    id: String(step?.id || step?.stepId || index),
    indexLabel: String(index + 1).padStart(2, '0'),
    actionLabel,
    title,
    summary: summary.text,
    summaryLabel: summary.label,
    summaryValue: summary.value,
    status: getRpaStepStatus({ step, index, failedStepIndex, activeIndex }),
    isAi,
    isFailed: failedStepIndex === index,
    technical: {
      locator,
      frame: step?.frameSummary || formatRpaFramePath(step?.frame_path || step?.framePath),
      validation: validationDetails || step?.validation?.status || step?.validationStatus || '无额外说明',
      url,
      candidateCount: Array.isArray(step?.locator_candidates) ? step.locator_candidates.length : 0,
    },
  };
}
