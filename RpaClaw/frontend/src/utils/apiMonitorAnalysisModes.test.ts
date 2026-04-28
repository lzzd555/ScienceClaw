import { describe, expect, it } from 'vitest'

import {
  ANALYSIS_MODES,
  canStartAnalysis,
  getAnalysisMode,
  modeRequiresInstruction,
} from './apiMonitorAnalysisModes'

describe('apiMonitorAnalysisModes', () => {
  it('defines the initial modes in dropdown order', () => {
    expect(ANALYSIS_MODES.map((mode) => mode.key)).toEqual(['free', 'safe_directed', 'directed'])
  })

  it('marks only directed modes as requiring instruction', () => {
    expect(modeRequiresInstruction('free')).toBe(false)
    expect(modeRequiresInstruction('safe_directed')).toBe(true)
    expect(modeRequiresInstruction('directed')).toBe(true)
  })

  it('falls back to free mode for unknown mode keys', () => {
    expect(getAnalysisMode('future_mode').key).toBe('free')
  })

  it('allows free analysis without instruction', () => {
    expect(canStartAnalysis({ hasSession: true, isAnalyzing: false, mode: 'free', instruction: '' })).toBe(true)
  })

  it('requires instruction for directed modes', () => {
    expect(canStartAnalysis({ hasSession: true, isAnalyzing: false, mode: 'directed', instruction: '   ' })).toBe(false)
    expect(canStartAnalysis({ hasSession: true, isAnalyzing: false, mode: 'directed', instruction: '删除测试订单' })).toBe(true)
  })
})
