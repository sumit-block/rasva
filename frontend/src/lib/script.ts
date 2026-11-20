/** Mirrors the backend's Devanagari check so the UI can explain problems before submitting. */
export function hasDevanagari(text: string): boolean {
  return /[ऀ-ॿ꣠-ꣿ]/.test(text);
}
