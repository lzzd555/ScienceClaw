export type InlineSandboxPreviewMode = 'browser' | 'none';

const BROWSER_TOOLS = new Set([
  'sandbox_get_browser_info',
  'sandbox_browser_screenshot',
  'sandbox_browser_execute_action',
]);

export interface InlineSandboxToolItem {
  type: string;
  tool?: {
    function?: string;
    name?: string;
    status?: string;
    tool_meta?: {
      sandbox?: boolean;
    };
  };
}

const isInlineBrowserPreviewTool = (toolFunction: string) => {
  if (!toolFunction) return false;
  if (BROWSER_TOOLS.has(toolFunction)) return true;
  return toolFunction.startsWith('browser_');
};

const getLatestTool = (items: InlineSandboxToolItem[] = []) => {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type === 'tool' && item.tool) {
      return item.tool;
    }
  }

  return null;
};

export const getInlineSandboxPreviewMode = (items: InlineSandboxToolItem[] = []): InlineSandboxPreviewMode => {
  const latestTool = getLatestTool(items);
  if (!latestTool) return 'none';

  const fn = latestTool.function || latestTool.name || '';
  if (isInlineBrowserPreviewTool(fn)) return 'browser';

  return 'none';
};

export const hasActiveInlineSandboxPreviewTool = (items: InlineSandboxToolItem[] = []): boolean => {
  const latestTool = getLatestTool(items);
  if (!latestTool) return false;

  const fn = latestTool.function || latestTool.name || '';
  if (!isInlineBrowserPreviewTool(fn)) return false;

  return latestTool.status === 'calling';
};
