import { describe, expect, it } from 'vitest';

import { shouldForwardScreencastKeyboardEvent } from './screencastInput';

function keyEvent(input: Partial<KeyboardEvent>): KeyboardEvent {
  return {
    type: input.type || 'keydown',
    key: input.key || '',
    ctrlKey: Boolean(input.ctrlKey),
    metaKey: Boolean(input.metaKey),
  } as KeyboardEvent;
}

describe('screencastInput', () => {
  it('does not forward paste shortcuts because paste uses Input.insertText', () => {
    expect(shouldForwardScreencastKeyboardEvent(keyEvent({ type: 'keydown', key: 'v', ctrlKey: true }))).toBe(false);
    expect(shouldForwardScreencastKeyboardEvent(keyEvent({ type: 'keydown', key: 'V', metaKey: true }))).toBe(false);
    expect(shouldForwardScreencastKeyboardEvent(keyEvent({ type: 'keyup', key: 'v', ctrlKey: true }))).toBe(false);
  });

  it('forwards normal keyboard shortcuts and regular text input', () => {
    expect(shouldForwardScreencastKeyboardEvent(keyEvent({ type: 'keydown', key: 'a' }))).toBe(true);
    expect(shouldForwardScreencastKeyboardEvent(keyEvent({ type: 'keydown', key: 'c', ctrlKey: true }))).toBe(true);
  });
});
