# Electron 安装目录配置设计

## 背景

当前 `electron-app` 在打包后有两处与安装目录脱钩：

- 运行时环境变量完全由 `src/process-manager.ts` 内置生成，无法在安装后通过安装目录中的文件追加配置。
- 设置向导写出的 `app-config.json` 存放在 `%AppData%/RpaClaw`，卸载应用后通常会残留。

用户希望：

- 在安装目录下放置 `.env` 文件，为 Electron 拉起的后端和 task-service 追加环境变量。
- 将设置向导生成的 `app-config.json` 放到安装目录下，便于随安装目录一起清理。

## 目标

- 打包模式下，从安装目录根加载 `.env`，用其覆盖默认环境变量并附加自定义变量。
- 打包模式下，将 `app-config.json` 读写位置改为安装目录根。
- 开发模式保持可用，继续从 `electron-app` 项目目录解析相关文件。

## 方案

### 路径分层

新增运行时路径解析逻辑，区分两个目录概念：

- `resourceDir`
  - 打包模式：`process.resourcesPath`
  - 开发模式：`electron-app` 项目根目录
  - 用于查找内置资源：`python`、`backend`、`task-service`、`builtin_skills`、`frontend-dist`
- `installRootDir`
  - 打包模式：`path.dirname(process.execPath)`
  - 开发模式：`electron-app` 项目根目录
  - 用于放置用户可编辑文件：`.env`、`app-config.json`

### 环境变量加载

将环境组装拆为两层：

1. 先生成当前内置默认值，保持现有启动行为。
2. 再读取 `installRootDir/.env`，解析为键值对后覆盖默认值并附加额外变量。

解析规则保持最小可用：

- 忽略空行和 `#` 注释
- 支持 `KEY=value`
- 支持单引号和双引号包裹的值
- 缺失 `.env` 时返回空对象，不报错

不引入额外 npm 依赖，避免仅为此改动扩大打包面。

### 配置文件位置

`ConfigManager` 改为使用 `installRootDir/app-config.json`：

- 首次运行判断基于安装目录下该文件是否存在
- 设置向导保存配置时写入该文件
- 正常启动时也从该文件读取

`homeDir` 下已有的 `config.json` 继续保留，作为业务目录初始化的一部分，不与 `app-config.json` 合并。

## 风险与处理

- 如果用户将应用安装到只读位置，安装目录根的 `app-config.json` 和 `.env` 可能不可写。
- 本次改动不做隐式回退到 `%AppData%`，避免路径再次分叉；若写入失败，沿用现有错误抛出路径，让向导或设置修改显式失败。

## 测试策略

使用 Node 内置测试覆盖纯逻辑：

- 打包模式下路径解析结果
- `.env` 解析行为
- 默认环境变量被安装目录 `.env` 覆盖的行为

再执行 TypeScript 构建，确认 Electron 主进程代码可编译。
