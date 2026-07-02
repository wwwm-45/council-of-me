const FRAGMENT_PATTERN = /[^,，、；;:：。！？?!]+(?:[,，、；;:：。！？?!]+|$)/gu;
const SENTENCE_ENDING_CHARS = ['。', '！', '？', '!', '?', '；', ';'];
const COMPLETE_SENTENCE_PATTERN = /[^。！？!?；;]+[。！？!?；;]+/gu;

export function normalizeSubtitleContent(content: string): string {
  return content.replace(/\s+/g, ' ').trim();
}

function sliceTrailingWindow(
  content: string,
  maxLineChars: number,
  maxLines: number,
): string[] {
  const maxChars = maxLineChars * maxLines;
  const tail = normalizeSubtitleContent(content).slice(-maxChars);

  if (!tail) {
    return [];
  }

  if (maxLines === 1 || tail.length <= maxLineChars) {
    return [tail];
  }

  const splitAt = Math.max(1, tail.length - maxLineChars);

  return [tail.slice(0, splitAt).trim(), tail.slice(splitAt).trim()].filter(Boolean);
}

export function splitSubtitleFragments(content: string): string[] {
  const normalized = normalizeSubtitleContent(content);
  if (!normalized) {
    return [];
  }

  return (normalized.match(FRAGMENT_PATTERN) ?? [])
    .map((fragment) => fragment.trim())
    .filter(Boolean);
}

export function buildSubtitleLines(
  content: string,
  options: { maxLineChars?: number; maxLines?: number } = {},
): string[] {
  const maxLineChars = options.maxLineChars ?? 18;
  const maxLines = options.maxLines ?? 2;
  const normalized = normalizeSubtitleContent(content);
  const fragments = splitSubtitleFragments(normalized);

  if (fragments.length === 0) {
    return [];
  }

  const tail = fragments.slice(-maxLines);
  if (tail.every((fragment) => fragment.length <= maxLineChars)) {
    return tail;
  }

  return sliceTrailingWindow(normalized, maxLineChars, maxLines);
}

export function getCompletedSubtitleSentences(content: string): string[] {
  const normalized = normalizeSubtitleContent(content);
  if (!normalized) {
    return [];
  }

  return (normalized.match(COMPLETE_SENTENCE_PATTERN) ?? [])
    .map((sentence) => sentence.trim())
    .filter(Boolean);
}

export function getCompletedSubtitleSentence(content: string): string {
  return getCompletedSubtitleSentences(content).at(-1) ?? '';
}

export function getSubtitleDisplayDuration(
  content: string,
  options: {
    baseMs?: number;
    perCharMs?: number;
    minMs?: number;
    maxMs?: number;
  } = {},
): number {
  const normalized = normalizeSubtitleContent(content);
  if (!normalized) {
    return 0;
  }

  const baseMs = options.baseMs ?? 900;
  const perCharMs = options.perCharMs ?? 65;
  const minMs = options.minMs ?? 1500;
  const maxMs = options.maxMs ?? 3400;
  const duration = baseMs + normalized.length * perCharMs;

  return Math.min(maxMs, Math.max(minMs, duration));
}

export function getSubtitleTailOnComplete(content: string): string {
  const normalized = normalizeSubtitleContent(content);
  if (!normalized) {
    return '';
  }

  const lastSentenceIndex = SENTENCE_ENDING_CHARS.reduce(
    (maxIndex, char) => Math.max(maxIndex, normalized.lastIndexOf(char)),
    -1,
  );

  if (lastSentenceIndex === -1) {
    return normalized;
  }

  if (lastSentenceIndex === normalized.length - 1) {
    return '';
  }

  return normalized.slice(lastSentenceIndex + 1).trim();
}
