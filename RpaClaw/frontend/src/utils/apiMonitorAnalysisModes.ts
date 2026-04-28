export type AnalysisModeKey = 'free' | 'safe_directed' | 'directed'
export type AnalysisRiskLevel = 'low' | 'guarded' | 'user_controlled'

export interface AnalysisModeOption {
  key: AnalysisModeKey
  label: string
  description: string
  requiresInstruction: boolean
  riskLevel: AnalysisRiskLevel
}

export const ANALYSIS_MODES: AnalysisModeOption[] = [
  {
    key: 'free',
    label: '自由分析',
    description: '自动扫描并探测页面上的安全交互元素。',
    requiresInstruction: false,
    riskLevel: 'low',
  },
  {
    key: 'safe_directed',
    label: '安全分析',
    description: '根据你的目标执行安全操作，跳过高风险业务动作。',
    requiresInstruction: true,
    riskLevel: 'guarded',
  },
  {
    key: 'directed',
    label: '定向分析',
    description: '根据你的目标执行操作，业务风险由你自行把控。',
    requiresInstruction: true,
    riskLevel: 'user_controlled',
  },
]

export function getAnalysisMode(modeKey: string): AnalysisModeOption {
  return ANALYSIS_MODES.find((mode) => mode.key === modeKey) || ANALYSIS_MODES[0]
}

export function modeRequiresInstruction(modeKey: string): boolean {
  return getAnalysisMode(modeKey).requiresInstruction
}

export function canStartAnalysis(input: {
  hasSession: boolean
  isAnalyzing: boolean
  mode: string
  instruction: string
}): boolean {
  if (!input.hasSession || input.isAnalyzing) return false
  if (!modeRequiresInstruction(input.mode)) return true
  return input.instruction.trim().length > 0
}
