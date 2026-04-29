export function shouldForwardScreencastKeyboardEvent(e: KeyboardEvent): boolean {
  const isPasteShortcut = (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v';
  return !isPasteShortcut;
}
