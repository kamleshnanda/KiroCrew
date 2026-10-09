import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { OPEN_MESSAGE_TYPE, openUrlOf, useFrameOpenLink } from './useFrameOpenLink'

const PR = 'https://github.com/kirodotdev/KiroCrew/pull/18518'

/** A frame stand-in whose window is a unique object: the hook compares identity only. */
function mountFrame() {
  return { contentWindow: {} as Window } as HTMLIFrameElement
}

function post(data: unknown, source: unknown) {
  const event = new MessageEvent('message', { data })
  Object.defineProperty(event, 'source', { value: source })
  window.dispatchEvent(event)
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useFrameOpenLink', () => {
  it('opens a pull request its own frame asks for, exactly once', () => {
    const frame = mountFrame()
    const open = vi.fn()
    renderHook(() => useFrameOpenLink([{ current: frame }], open))
    post({ type: OPEN_MESSAGE_TYPE, url: PR }, frame.contentWindow)
    expect(open).toHaveBeenCalledTimes(1)
    expect(open).toHaveBeenCalledWith(PR)
  })

  it('drops a URL on another host', () => {
    const frame = mountFrame()
    const open = vi.fn()
    renderHook(() => useFrameOpenLink([{ current: frame }], open))
    post({ type: OPEN_MESSAGE_TYPE, url: 'https://evil.example/kirodotdev/KiroCrew/pull/1' }, frame.contentWindow)
    post({ type: OPEN_MESSAGE_TYPE, url: 'https://github.com.evil.example/a/b/pull/1' }, frame.contentWindow)
    expect(open).not.toHaveBeenCalled()
  })

  it('drops a github.com URL that is not a pull request page', () => {
    const frame = mountFrame()
    const open = vi.fn()
    renderHook(() => useFrameOpenLink([{ current: frame }], open))
    for (const url of [
      'https://github.com/kirodotdev/KiroCrew',
      'https://github.com/kirodotdev/KiroCrew/issues/1',
      'https://github.com/kirodotdev/KiroCrew/pull/1/files',
      `${PR}?x=1`,
      `${PR}#top`,
      'http://github.com/kirodotdev/KiroCrew/pull/1',
      'javascript:alert(1)//https://github.com/a/b/pull/1',
    ]) {
      post({ type: OPEN_MESSAGE_TYPE, url }, frame.contentWindow)
    }
    expect(open).not.toHaveBeenCalled()
  })

  it('ignores a valid message from a window that is not its frame', () => {
    const mine = mountFrame()
    const other = mountFrame()
    const open = vi.fn()
    renderHook(() => useFrameOpenLink([{ current: mine }], open))
    post({ type: OPEN_MESSAGE_TYPE, url: PR }, other.contentWindow)
    post({ type: OPEN_MESSAGE_TYPE, url: PR }, window)
    post({ type: OPEN_MESSAGE_TYPE, url: PR }, null)
    expect(open).not.toHaveBeenCalled()
  })

  it('opens through window.open with noopener and noreferrer by default', () => {
    const frame = mountFrame()
    const spy = vi.spyOn(window, 'open').mockImplementation(() => null)
    renderHook(() => useFrameOpenLink([{ current: frame }]))
    post({ type: OPEN_MESSAGE_TYPE, url: PR }, frame.contentWindow)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith(PR, '_blank', 'noopener,noreferrer')
    spy.mockRestore()
  })

  it('reads only the open type, never the act reply', () => {
    expect(openUrlOf({ type: 'kirocrew-dashboard:act', url: PR })).toBeNull()
    expect(openUrlOf({ type: OPEN_MESSAGE_TYPE, url: PR })).toBe(PR)
    expect(openUrlOf({ type: OPEN_MESSAGE_TYPE, url: `${PR}/` })).toBe(`${PR}/`)
  })
})
