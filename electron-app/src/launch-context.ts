const INSTALLER_LAUNCH_FLAGS = new Set(['--updated']);
const INSTALLER_STARTUP_DELAY_MS = 5000;
const DEFAULT_SERVICE_STARTUP_TIMEOUT_MS = 30000;
const INSTALLER_SERVICE_STARTUP_TIMEOUT_MS = 30000;

function normalizeArg(arg: string): string {
  return arg.trim().toLowerCase();
}

export function buildRelaunchArgs(argv: string[]): string[] {
  return argv.slice(1).filter((arg) => !INSTALLER_LAUNCH_FLAGS.has(normalizeArg(arg)));
}

export function getStartupDelayMs(argv: string[]): number {
  return argv.some((arg) => INSTALLER_LAUNCH_FLAGS.has(normalizeArg(arg)))
    ? INSTALLER_STARTUP_DELAY_MS
    : 0;
}

export function getServiceStartupTimeoutMs(argv: string[]): number {
  return argv.some((arg) => INSTALLER_LAUNCH_FLAGS.has(normalizeArg(arg)))
    ? INSTALLER_SERVICE_STARTUP_TIMEOUT_MS
    : DEFAULT_SERVICE_STARTUP_TIMEOUT_MS;
}
