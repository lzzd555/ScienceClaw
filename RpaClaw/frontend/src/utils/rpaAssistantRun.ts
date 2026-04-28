import type { RpaAgentMessageStatus } from './rpaAgentProgress';

export type RpaAssistantRunItemKind = 'plan' | 'action' | 'output' | 'trace' | 'diagnostic';
export type RpaAssistantRoundStatus = 'executing' | 'done' | 'error';

export interface RpaAssistantRunItem {
  id: string;
  kind: RpaAssistantRunItemKind;
  title: string;
  detail?: string;
  code?: string;
  traceId?: string;
  showCode?: boolean;
}

export interface RpaAssistantRound {
  id: string;
  index: number;
  status: RpaAssistantRoundStatus;
  items: RpaAssistantRunItem[];
}

export interface RpaAssistantRun {
  status: RpaAgentMessageStatus;
  time: string;
  traceCount: number;
  summary: string;
  error: string;
  diagnostics: string[];
  rounds: RpaAssistantRound[];
}

const cloneRun = (run: RpaAssistantRun): RpaAssistantRun => ({
  ...run,
  diagnostics: [...run.diagnostics],
  rounds: run.rounds.map((round) => ({
    ...round,
    items: round.items.map((item) => ({ ...item })),
  })),
});

const outputToText = (value: unknown): string => {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'object' && !Array.isArray(value)) {
    const labelMap: Record<string, string> = {
      project_name: '项目名',
      star_count: 'Star 数',
      fork_count: 'Fork 数',
      title: '标题',
      url: '链接',
    };
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length && entries.every(([, entryValue]) => (
      entryValue === null || ['string', 'number', 'boolean'].includes(typeof entryValue)
    ))) {
      return entries
        .map(([key, entryValue]) => `${labelMap[key] || key}: ${entryValue ?? ''}`)
        .join('\n');
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const normalizePlanDetail = (text: string): string => {
  const value = text.trim();
  if (!value) return '正在把你的目标拆成可录制的浏览器操作。';
  if (/planning one trace-first recording command/i.test(value)) {
    return '正在把你的目标拆成可录制的浏览器操作。';
  }
  if (/repairing with current page state/i.test(value)) {
    return '上一次操作未完成，正在根据当前页面重新尝试。';
  }
  if (/planning command/i.test(value)) {
    return '正在准备下一步页面操作。';
  }
  return value;
};

const normalizeDoneSummary = (message: string): string => {
  if (!message || /task completed/i.test(message)) return '任务完成';
  return message;
};

export const formatRpaAssistantDiagnostics = (diagnostics: any[] = []): string[] => diagnostics
  .map((item: any) => {
    const code = String(item?.code || '').trim();
    const message = String(item?.message || '').trim();
    const rawError = item?.raw?.result?.error ? String(item.raw.result.error).trim() : '';
    const body = message && rawError && rawError !== message ? `${message}: ${rawError}` : (message || rawError);
    if (!body) return '';
    return code ? `${code}: ${body}` : body;
  })
  .filter(Boolean);

export const createRpaAssistantRun = (time: string): RpaAssistantRun => ({
  status: 'executing',
  time,
  traceCount: 0,
  summary: '',
  error: '',
  diagnostics: [],
  rounds: [],
});

const makeItemId = (round: RpaAssistantRound, kind: RpaAssistantRunItemKind) => (
  `round-${round.index}-${kind}-${round.items.length + 1}`
);

const startRound = (run: RpaAssistantRun): RpaAssistantRound => {
  const round: RpaAssistantRound = {
    id: `round-${run.rounds.length + 1}`,
    index: run.rounds.length + 1,
    status: 'executing',
    items: [],
  };
  run.rounds.push(round);
  return round;
};

const getActiveRound = (run: RpaAssistantRun): RpaAssistantRound => {
  const last = run.rounds[run.rounds.length - 1];
  if (!last || last.status !== 'executing') return startRound(run);
  return last;
};

const addItem = (
  run: RpaAssistantRun,
  kind: RpaAssistantRunItemKind,
  title: string,
  detail = '',
  extras: Partial<RpaAssistantRunItem> = {},
) => {
  const round = getActiveRound(run);
  round.items.push({
    id: makeItemId(round, kind),
    kind,
    title,
    detail,
    ...extras,
  });
  return round;
};

export const applyRpaAssistantRunEvent = (
  currentRun: RpaAssistantRun,
  eventType: string,
  data: any = {},
): RpaAssistantRun => {
  const run = cloneRun(currentRun);

  switch (eventType) {
    case 'message_chunk': {
      addItem(run, 'plan', '正在理解你的目标', normalizePlanDetail(String(data.text || '')));
      break;
    }
    case 'agent_thought': {
      addItem(run, 'plan', '正在理解你的目标', normalizePlanDetail(String(data.text || '')));
      break;
    }
    case 'agent_action': {
      addItem(run, 'action', '正在操作页面', data.description || '执行浏览器操作', {
        code: data.code || '',
        showCode: false,
      });
      break;
    }
    case 'agent_step_done': {
      const round = addItem(run, 'output', data.success === false ? '操作反馈' : '获取到的结果', outputToText(data.output));
      round.status = data.success === false ? 'error' : 'done';
      break;
    }
    case 'trace_added': {
      const traceId = String(data.trace_id || data.trace?.trace_id || '').trim();
      addItem(run, 'trace', '已记录为技能步骤', data.description || data.trace?.description || traceId, { traceId });
      break;
    }
    case 'result': {
      const round = addItem(run, 'output', data.success === false ? '操作反馈' : '获取到的结果', outputToText(data.output || data.error));
      round.status = data.success === false ? 'error' : 'done';
      break;
    }
    case 'agent_done': {
      run.status = 'done';
      run.summary = normalizeDoneSummary(data.message || '');
      run.traceCount = Number(data.trace_count ?? data.total_steps ?? run.traceCount ?? 0);
      break;
    }
    case 'agent_aborted': {
      run.status = 'error';
      run.error = data.reason || 'Agent 已停止';
      run.diagnostics = formatRpaAssistantDiagnostics(data.diagnostics || []);
      const round = addItem(run, 'diagnostic', '执行停止', run.error);
      round.status = 'error';
      break;
    }
    case 'error': {
      run.status = 'error';
      run.error = data.message || '未知错误';
      const round = addItem(run, 'diagnostic', '执行错误', run.error);
      round.status = 'error';
      break;
    }
    default:
      break;
  }

  return run;
};
