/**
 * Minimal session state management (React context-free, just a module-level store).
 * Pages import { session, setSession } and use local state + this as fallback.
 */
export interface SessionState {
  sessionId: string | null;
  status: string;
  framingPreference: string | null;
  conflictProfile: Record<string, unknown> | null;
  identityCards: Record<string, unknown>[] | null;
  debateLevel: string | null;
  llmModel: string | null;
  userDisplayName: string | null;
}

const STORAGE_KEY = 'council-of-me.session';

export const EMPTY_STATE: SessionState = {
  sessionId: null,
  status: 'init',
  framingPreference: null,
  conflictProfile: null,
  identityCards: null,
  debateLevel: null,
  llmModel: null,
  userDisplayName: null,
};

function readPersistedState(): SessionState {
  if (typeof window === 'undefined') {
    return { ...EMPTY_STATE };
  }

  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { ...EMPTY_STATE };
    }

    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return { ...EMPTY_STATE };
    }

    return { ...EMPTY_STATE, ...(parsed as Partial<SessionState>) };
  } catch {
    return { ...EMPTY_STATE };
  }
}

function persistState(): void {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(_state));
  } catch {
    // Ignore persistence errors and keep the in-memory state usable.
  }
}

let _state: SessionState = readPersistedState();

const _listeners: Set<() => void> = new Set();

export function getSession(): SessionState {
  return _state;
}

export function setSession(partial: Partial<SessionState>): void {
  _state = { ..._state, ...partial };
  persistState();
  _listeners.forEach((fn) => fn());
}

export function resetSession(): void {
  _state = readPersistedState();
  _listeners.forEach((fn) => fn());
}

export function subscribe(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}
