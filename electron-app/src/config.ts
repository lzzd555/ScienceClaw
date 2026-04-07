import { app } from 'electron';
import * as fs from 'fs';
import * as path from 'path';
import { AppConfig } from './types';
import { resolveRuntimePaths } from './runtime';

const APP_NAME = 'RpaClaw';

export class ConfigManager {
  private configPath: string;
  private config: AppConfig | null = null;

  constructor() {
    const runtimePaths = resolveRuntimePaths({
      isPackaged: app.isPackaged,
      execPath: process.execPath,
      resourcesPath: process.resourcesPath,
      currentDir: __dirname,
    });
    this.configPath = runtimePaths.configFilePath;
  }

  /**
   * Check if this is the first run (config file doesn't exist)
   */
  isFirstRun(): boolean {
    return !fs.existsSync(this.configPath);
  }

  /**
   * Load configuration from disk
   */
  load(): AppConfig | null {
    if (!fs.existsSync(this.configPath)) {
      return null;
    }
    try {
      const data = fs.readFileSync(this.configPath, 'utf-8');
      this.config = JSON.parse(data);
      return this.config;
    } catch (error) {
      console.error('Failed to load config:', error);
      return null;
    }
  }

  /**
   * Save configuration to disk
   */
  save(config: AppConfig): void {
    try {
      fs.writeFileSync(this.configPath, JSON.stringify(config, null, 2), 'utf-8');
      this.config = config;
    } catch (error) {
      console.error('Failed to save config:', error);
      throw error;
    }
  }

  /**
   * Get current configuration
   */
  get(): AppConfig | null {
    return this.config;
  }

  /**
   * Get default home directory
   */
  getDefaultHomeDir(): string {
    return path.join(app.getPath('home'), APP_NAME);
  }

  /**
   * Initialize home directory structure
   */
  initializeHomeDir(homeDir: string): void {
    const dirs = [
      homeDir,
      path.join(homeDir, 'workspace'),
      path.join(homeDir, 'external_skills'),
      path.join(homeDir, 'data'),
      path.join(homeDir, 'data', 'sessions'),
      path.join(homeDir, 'data', 'users'),
      path.join(homeDir, 'data', 'tasks'),
      path.join(homeDir, 'logs'),
    ];

    for (const dir of dirs) {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    }

    // Create default config.json in home directory
    const configPath = path.join(homeDir, 'config.json');
    if (!fs.existsSync(configPath)) {
      const defaultConfig = {
        backend_port: 12001,
        task_service_port: 12002,
        log_level: 'INFO',
      };
      fs.writeFileSync(configPath, JSON.stringify(defaultConfig, null, 2), 'utf-8');
    }
  }

  /**
   * Validate home directory (writable and has space)
   */
  validateHomeDir(homeDir: string): { valid: boolean; error?: string } {
    try {
      // Check if parent directory exists
      const parentDir = path.dirname(homeDir);
      if (!fs.existsSync(parentDir)) {
        return { valid: false, error: 'Parent directory does not exist' };
      }

      // Check if writable
      const testFile = path.join(parentDir, '.rpaclaw-test');
      fs.writeFileSync(testFile, 'test');
      fs.unlinkSync(testFile);

      return { valid: true };
    } catch (error) {
      return { valid: false, error: `Not writable: ${error}` };
    }
  }
}
