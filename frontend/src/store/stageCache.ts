const PREFIX = 'council-of-me.stage.';

function keyFor(sessionId: string, stage: string): string {
  return `${PREFIX}${sessionId}.${stage}`;
}

export function readStageCache<T>(sessionId: string | null | undefined, stage: string): T | null {
  if (!sessionId || typeof window === 'undefined') {
    return null;
  }

  try {
    const raw = window.sessionStorage.getItem(keyFor(sessionId, stage));
    if (!raw) {
      return null;
    }
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function writeStageCache<T>(sessionId: string | null | undefined, stage: string, value: T): void {
  if (!sessionId || typeof window === 'undefined') {
    return;
  }

  try {
    window.sessionStorage.setItem(keyFor(sessionId, stage), JSON.stringify(value));
  } catch {
    // Keep the UI usable if storage is unavailable or full.
  }
}
