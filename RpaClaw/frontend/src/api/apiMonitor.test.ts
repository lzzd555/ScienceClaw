import { describe, expect, it, vi, beforeEach } from 'vitest'

const createSSEConnection = vi.fn()

vi.mock('@/api/client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  createSSEConnection: (...args: unknown[]) => createSSEConnection(...args),
}))

describe('apiMonitor analyzeSession', () => {
  beforeEach(() => {
    createSSEConnection.mockReset()
    createSSEConnection.mockResolvedValue(vi.fn())
  })

  it('sends free mode payload by default', async () => {
    const { analyzeSession } = await import('./apiMonitor')

    analyzeSession('session-1', vi.fn())
    await Promise.resolve()

    expect(createSSEConnection).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/analyze',
      { method: 'POST', body: { mode: 'free', instruction: '' } },
      expect.any(Object),
    )
  })

  it('sends selected analysis mode and instruction', async () => {
    const { analyzeSession } = await import('./apiMonitor')

    analyzeSession('session-1', vi.fn(), {
      mode: 'safe_directed',
      instruction: '搜索订单 123',
    })
    await Promise.resolve()

    expect(createSSEConnection).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/analyze',
      { method: 'POST', body: { mode: 'safe_directed', instruction: '搜索订单 123' } },
      expect.any(Object),
    )
  })
})
