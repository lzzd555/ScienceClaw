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

  it('uses a long timeout when stopping recording because tool generation can be slow', async () => {
    const { apiClient } = await import('@/api/client')
    const { API_MONITOR_STOP_RECORDING_TIMEOUT_MS, stopRecording } = await import('./apiMonitor')
    vi.mocked(apiClient.post).mockResolvedValue({ data: { tools: [] } })

    await stopRecording('session-1')

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/record/stop',
      undefined,
      { timeout: API_MONITOR_STOP_RECORDING_TIMEOUT_MS },
    )
    expect(API_MONITOR_STOP_RECORDING_TIMEOUT_MS).toBeGreaterThanOrEqual(10 * 60 * 1000)
  })
})

describe('apiMonitor generation candidates', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  it('lists generation candidates', async () => {
    const { apiClient } = await import('@/api/client')
    const candidate = {
      id: 'candidate-1',
      session_id: 'session-1',
      dedup_key: 'GET /api/orders',
      method: 'GET',
      url_pattern: '/api/orders',
      source_call_ids: ['call-1'],
      sample_call_ids: ['call-1'],
      status: 'running' as const,
      tool_id: null,
      error: '',
      retry_after: null,
      attempts: 0,
      capture_dom_context: {},
      capture_page_url: 'https://example.com/app',
      capture_title: 'Orders',
      capture_dom_digest: 'digest-1',
      created_at: '2026-04-30T00:00:00',
      updated_at: '2026-04-30T00:00:00',
    }
    vi.mocked(apiClient.get).mockResolvedValue({ data: { candidates: [candidate] } })
    const { listGenerationCandidates } = await import('./apiMonitor')

    await expect(listGenerationCandidates('session-1')).resolves.toEqual([candidate])
    expect(apiClient.get).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/generation-candidates',
    )
  })

  it('retries generation candidates', async () => {
    const { apiClient } = await import('@/api/client')
    vi.mocked(apiClient.post).mockResolvedValue({ data: { candidate: { id: 'candidate-1' } } })
    const { retryGenerationCandidate } = await import('./apiMonitor')

    await expect(retryGenerationCandidate('session-1', 'candidate-1')).resolves.toEqual({
      id: 'candidate-1',
    })
    expect(apiClient.post).toHaveBeenCalledWith(
      '/api-monitor/session/session-1/generation-candidates/candidate-1/retry',
    )
  })
})
