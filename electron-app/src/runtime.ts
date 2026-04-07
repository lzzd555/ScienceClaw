import * as fs from 'fs';
import * as path from 'path';

const CONFIG_FILE = 'app-config.json';
const ENV_FILE = '.env';

export interface RuntimePaths {
  installRootDir: string;
  resourceDir: string;
  configFilePath: string;
  envFilePath: string;
}

interface ResolveRuntimePathsOptions {
  isPackaged: boolean;
  execPath: string;
  resourcesPath: string;
  currentDir: string;
}

interface BuildBackendEnvOptions {
  homeDir: string;
  resourceDir: string;
  extraEnv?: Record<string, string>;
}

export function resolveRuntimePaths(options: ResolveRuntimePathsOptions): RuntimePaths {
  const devRootDir = path.resolve(options.currentDir, '..', '..');
  const resourceDir = options.isPackaged ? options.resourcesPath : devRootDir;
  const installRootDir = options.isPackaged ? path.dirname(options.execPath) : devRootDir;

  return {
    installRootDir,
    resourceDir,
    configFilePath: path.join(installRootDir, CONFIG_FILE),
    envFilePath: path.join(installRootDir, ENV_FILE),
  };
}

export function parseEnvContent(content: string): Record<string, string> {
  const entries: Record<string, string> = {};

  for (const rawLine of content.split(/\r?\n/u)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }

    const separatorIndex = line.indexOf('=');
    if (separatorIndex <= 0) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    if (!key) {
      continue;
    }

    let value = line.slice(separatorIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }

    entries[key] = value;
  }

  return entries;
}

export function loadEnvFile(filePath: string): Record<string, string> {
  if (!fs.existsSync(filePath)) {
    return {};
  }

  return parseEnvContent(fs.readFileSync(filePath, 'utf-8'));
}

export function buildBackendEnv(options: BuildBackendEnvOptions): Record<string, string> {
  const pythonDir = path.join(options.resourceDir, 'python');
  const sitePackages = path.join(pythonDir, 'Lib', 'site-packages');
  const playwrightBrowsers = path.join(
    sitePackages,
    'playwright',
    'driver',
    'package',
    '.local-browsers'
  );
  const frontendDist = path.join(options.resourceDir, 'frontend-dist');

  return {
    STORAGE_BACKEND: 'local',
    RPA_CLAW_HOME: options.homeDir,
    WORKSPACE_DIR: path.join(options.homeDir, 'workspace'),
    EXTERNAL_SKILLS_DIR: path.join(options.homeDir, 'external_skills'),
    LOCAL_DATA_DIR: path.join(options.homeDir, 'data'),
    BUILTIN_SKILLS_DIR: path.join(options.resourceDir, 'builtin_skills'),
    BACKEND_PORT: '12001',
    TASK_SERVICE_PORT: '12002',
    PYTHONHOME: pythonDir,
    PYTHONPATH: sitePackages,
    PLAYWRIGHT_BROWSERS_PATH: playwrightBrowsers,
    ENVIRONMENT: 'production',
    LOG_LEVEL: 'INFO',
    FRONTEND_DIST_DIR: frontendDist,
    ...options.extraEnv,
  };
}
