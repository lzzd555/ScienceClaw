export const ALLOWED_EXTERNAL_PROTOCOLS = new Set(['http:', 'https:']);

export function normalizeExternalUrl(input: string): string | null {
  try {
    const parsed = new URL(input);
    if (!ALLOWED_EXTERNAL_PROTOCOLS.has(parsed.protocol)) {
      return null;
    }

    return parsed.toString();
  } catch {
    return null;
  }
}
