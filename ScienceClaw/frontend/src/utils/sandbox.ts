/**
 * Sandbox URL utilities.
 *
 * When SANDBOX_PUBLIC_URL is configured on the backend, all sandbox URLs
 * use that as the base (for split deployments). Otherwise, falls back to
 * same-host with default ports (local dev / single-machine Docker).
 */

import { apiClient } from '@/api/client';

const DEFAULT_SANDBOX_PORT = 18080;

/** Cached sandbox base URL (fetched once from backend). */
let _sandboxBaseUrl: string | null = null;
let _fetchPromise: Promise<string> | null = null;
let _storageBackend: string | null = null;

/**
 * Fetch sandbox_public_url from backend (cached).
 * Returns empty string if not configured.
 */
async function fetchSandboxPublicUrl(): Promise<string> {
  if (_sandboxBaseUrl !== null) return _sandboxBaseUrl;
  if (_fetchPromise) return _fetchPromise;

  _fetchPromise = apiClient
    .get('/client-config')
    .then((res) => {
      _sandboxBaseUrl = res.data?.sandbox_public_url || '';
      _storageBackend = res.data?.storage_backend || 'mongo';
      return _sandboxBaseUrl;
    })
    .catch(() => {
      _sandboxBaseUrl = '';
      _storageBackend = 'mongo';
      return '';
    });

  return _fetchPromise;
}

/**
 * Get sandbox base URL synchronously.
 * Uses cached value if available, otherwise falls back to same-host default.
 * Call `initSandboxConfig()` early in app startup to prime the cache.
 */
function getSandboxBaseUrlSync(): string {
  if (_sandboxBaseUrl) return _sandboxBaseUrl;
  return `${window.location.protocol}//${window.location.hostname}:${DEFAULT_SANDBOX_PORT}`;
}

/** Prime the sandbox URL cache. Call once at app startup. */
export async function initSandboxConfig(): Promise<void> {
  await fetchSandboxPublicUrl();
}

export function getSandboxBaseUrl(): string {
  return getSandboxBaseUrlSync();
}

export function getSandboxVncUrl(): string {
  return `${getSandboxBaseUrl()}/vnc/index.html?autoconnect=true&resize=scale&view_only=true`;
}

export function getRpaVncUrl(): string {
  return `${getSandboxBaseUrl()}/vnc/index.html?autoconnect=true&resize=scale&view_only=false`;
}

export function getSandboxTerminalWsUrl(): string {
  const base = getSandboxBaseUrl();
  const proto = base.startsWith('https') ? 'wss:' : 'ws:';
  // Extract host from base URL
  const url = new URL(base);
  return `${proto}//${url.host}/v1/shell/ws`;
}

export function getSandboxScreenshotUrl(): string {
  return `${getSandboxBaseUrl()}/vnc/screenshot`;
}

export type SandboxPreviewMode = 'terminal' | 'browser' | 'none';

export function isLocalMode(): boolean {
  return _storageBackend === 'local';
}

/**
 * Tools that trigger the terminal preview panel.
 * Includes both deepagents built-in tools and legacy sandbox tool names.
 */
const TERMINAL_TOOLS = new Set([
  // deepagents built-in
  'execute',
  // legacy MCP sandbox (kept for backward compatibility)
  'sandbox_execute_bash',
  'sandbox_execute_code',
  'sandbox_file_operations',
  'sandbox_str_replace_editor',
  'sandbox_get_context',
  'sandbox_get_packages',
  'sandbox_convert_to_markdown',
  'sandbox_exec',
]);

/**
 * Tools that trigger the browser preview panel.
 */
const BROWSER_TOOLS = new Set([
  'sandbox_get_browser_info',
  'sandbox_browser_screenshot',
  'sandbox_browser_execute_action',
]);

/**
 * Determine the preview mode for a given tool function name.
 * When isSandboxProxy is true, the tool executes in the sandbox via a proxy,
 * so it should always trigger the terminal preview.
 */
export function getPreviewMode(toolFunction: string, isSandboxProxy = false): SandboxPreviewMode {
  if (!toolFunction) return 'none';
  if (isSandboxProxy) return 'terminal';
  if (TERMINAL_TOOLS.has(toolFunction)) return 'terminal';
  if (BROWSER_TOOLS.has(toolFunction)) return 'browser';
  if (toolFunction.startsWith('terminal_')) return 'terminal';
  if (toolFunction.startsWith('browser_')) return 'browser';
  if (toolFunction.startsWith('sandbox_')) return 'terminal';
  return 'none';
}
