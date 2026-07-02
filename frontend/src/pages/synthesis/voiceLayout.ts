type SimilarityMatrix = Record<string, Record<string, number>> | undefined;

function similarity(matrix: SimilarityMatrix, a: string, b: string): number {
  if (!matrix) return 0;
  return matrix[a]?.[b] ?? matrix[b]?.[a] ?? 0;
}

/**
 * Greedy nearest-neighbour ordering so the most similar voices sit adjacent on
 * the ring. Falls back to the original order when no usable matrix is present.
 */
export function orderVoicesBySimilarity(
  agentIds: string[],
  matrix: SimilarityMatrix,
): string[] {
  if (agentIds.length <= 2 || !matrix || Object.keys(matrix).length === 0) {
    return [...agentIds];
  }

  const remaining = new Set(agentIds);
  const ordered: string[] = [];
  let current = agentIds[0];
  ordered.push(current);
  remaining.delete(current);

  while (remaining.size > 0) {
    let best: string | null = null;
    let bestScore = -Infinity;
    for (const candidate of remaining) {
      const score = similarity(matrix, current, candidate);
      if (score > bestScore) {
        bestScore = score;
        best = candidate;
      }
    }
    const next = best ?? (remaining.values().next().value as string);
    ordered.push(next);
    remaining.delete(next);
    current = next;
  }

  return ordered;
}
