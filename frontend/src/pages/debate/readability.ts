import type { ChatMsg, SignificantTurn } from './types';

export type ReadableTurn = ChatMsg & {
  emphasis: 'primary' | 'normal' | 'muted';
};

export function buildReadableTurns(
  messages: ChatMsg[],
  artifact: { low_trust?: boolean; significant_turns?: SignificantTurn[] },
): ReadableTurn[] {
  const significantIds = new Set(
    (artifact.significant_turns ?? []).map((item) => item.statement_id),
  );

  return messages.map((message) => ({
    ...message,
    emphasis: significantIds.has(message.id)
      ? 'primary'
      : artifact.low_trust
        ? 'muted'
        : 'normal',
  }));
}
