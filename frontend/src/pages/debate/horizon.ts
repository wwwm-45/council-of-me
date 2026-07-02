/** 3-C: decision-horizon axis for R2 tensions (mirrors backend artifacts.py). */
export type Horizon = 'immediate' | 'medium' | 'long' | 'unscoped';

/** Fixed display order: soonest first, catch-all last. */
export const HORIZON_ORDER: Horizon[] = ['immediate', 'medium', 'long', 'unscoped'];

export const HORIZON_LABELS: Record<Horizon, string> = {
  immediate: '即时',
  medium: '中期',
  long: '长期',
  unscoped: '未定范围',
};

export interface HorizonGroup<T> {
  key: Horizon;
  label: string;
  tensions: T[];
}

function normalizeHorizon(value: unknown): Horizon {
  return value === 'immediate' || value === 'medium' || value === 'long' ? value : 'unscoped';
}

/**
 * Bucket tensions by decision horizon into fixed-order, non-empty groups.
 * Returns `null` when no tension carries a real (non-unscoped) horizon, so the
 * caller can fall back to a flat list instead of dumping everything under
 * "未定范围" (e.g. pre-3-C sessions whose tensions have no horizon).
 */
export function groupTensionsByHorizon<T extends { horizon?: string }>(
  tensions: T[],
): HorizonGroup<T>[] | null {
  const buckets: Record<Horizon, T[]> = {
    immediate: [],
    medium: [],
    long: [],
    unscoped: [],
  };

  for (const tension of tensions) {
    buckets[normalizeHorizon(tension.horizon)].push(tension);
  }

  const hasScoped =
    buckets.immediate.length > 0 || buckets.medium.length > 0 || buckets.long.length > 0;
  if (!hasScoped) {
    return null;
  }

  return HORIZON_ORDER.filter((key) => buckets[key].length > 0).map((key) => ({
    key,
    label: HORIZON_LABELS[key],
    tensions: buckets[key],
  }));
}
