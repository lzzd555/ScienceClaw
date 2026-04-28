export interface ActivitySandboxTerminalToolItem {
  id: string;
  type: string;
  tool?: {
    tool_call_id?: string;
    function?: string;
    name?: string;
    status?: string;
    args?: Record<string, any> | null;
    content?: any;
    tool_meta?: {
      sandbox?: boolean;
    };
  };
}

export interface ActivitySandboxExecEntry {
  toolName: string;
  command: string;
  output?: string;
  status: string;
}

const TERMINAL_TOOLS = new Set([
  'execute',
  'sandbox_execute_bash',
  'sandbox_execute_code',
  'sandbox_file_operations',
  'sandbox_str_replace_editor',
  'sandbox_get_context',
  'sandbox_get_packages',
  'sandbox_convert_to_markdown',
  'sandbox_exec',
]);

function isTerminalTool(item: ActivitySandboxTerminalToolItem): boolean {
  const fn = item.tool?.function || item.tool?.name || '';
  if (!fn) return false;
  if (item.tool?.tool_meta?.sandbox) return true;
  if (TERMINAL_TOOLS.has(fn)) return true;
  if (fn.startsWith('terminal_') || fn.startsWith('sandbox_')) return true;
  return false;
}

function extractCommand(item: ActivitySandboxTerminalToolItem): string {
  const args = item.tool?.args;
  if (!args || typeof args !== 'object') return '';
  return args.command || args.code || args.script || args.path || args.file || args.url || args.action || '';
}

function extractOutput(item: ActivitySandboxTerminalToolItem): string {
  const content = item.tool?.content;
  if (!content) return '';
  if (typeof content === 'string') {
    try {
      const parsed = JSON.parse(content);
      return parsed.stdout || parsed.output || parsed.text || content;
    } catch {
      return content;
    }
  }
  if (typeof content === 'object') {
    return content.stdout || content.output || content.text || JSON.stringify(content);
  }
  return String(content);
}

export function buildActivitySandboxTerminalHistory(
  items: ActivitySandboxTerminalToolItem[] = [],
): ActivitySandboxExecEntry[] {
  const history: ActivitySandboxExecEntry[] = [];
  const seenEntries = new Set<string>();

  for (const item of items) {
    if (item.type !== 'tool' || !item.tool) continue;

    const fn = item.tool.function || item.tool.name || '';
    if (!fn || !isTerminalTool(item)) {
      continue;
    }

    const callId = item.tool.tool_call_id || item.id;
    const command = extractCommand(item);

    if (item.tool.status === 'calling') {
      const callingKey = `${callId}:calling`;
      if (seenEntries.has(callingKey)) continue;
      seenEntries.add(callingKey);
      history.push({ toolName: fn, command, status: 'calling' });
      continue;
    }

    if (item.tool.status === 'called') {
      const callingKey = `${callId}:calling`;
      const calledKey = `${callId}:called`;
      if (!seenEntries.has(callingKey)) {
        seenEntries.add(callingKey);
        history.push({ toolName: fn, command, status: 'calling' });
      }
      if (seenEntries.has(calledKey)) continue;
      seenEntries.add(calledKey);
      history.push({
        toolName: fn,
        command,
        output: extractOutput(item),
        status: 'called',
      });
    }
  }

  return history;
}
