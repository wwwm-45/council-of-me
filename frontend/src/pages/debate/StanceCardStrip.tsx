import { useEffect, useMemo, useRef, useState } from 'react';

import type { AgentOrderEntry } from '../../api/client';
import {
  type AgentEvolutionDto,
  type AgentShiftType,
  getVisual,
  SHIFT_BADGE_CLASS,
  SHIFT_LABEL,
} from './types';

interface StanceCardStripProps {
  agentOrder: AgentOrderEntry[];
  agentEvolutions: Map<string, AgentEvolutionDto[]>;
}

function isVisibleShift(shiftType: AgentShiftType): boolean {
  return shiftType === 'revision' || shiftType === 'reversal';
}

function latestEvolution(evolutions: AgentEvolutionDto[] | undefined): AgentEvolutionDto | null {
  if (!evolutions?.length) return null;
  return evolutions[evolutions.length - 1];
}

function ShiftBadge({ shiftType }: { shiftType: AgentShiftType }) {
  const label = SHIFT_LABEL[shiftType];
  if (!label || !isVisibleShift(shiftType)) return null;

  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${SHIFT_BADGE_CLASS[shiftType]}`}>
      {label}
    </span>
  );
}

function StancePopover({
  agent,
  evolutions,
}: {
  agent: AgentOrderEntry;
  evolutions: AgentEvolutionDto[];
}) {
  const visual = getVisual(agent.agentId);
  const current = latestEvolution(evolutions);
  const history = evolutions.slice(0, -1).filter((entry) => isVisibleShift(entry.shift_type));
  const showR1Anchor = current?.r1_position && current.r1_position !== current.current_position;

  return (
    <div
      data-testid={`stance-popover-${agent.agentId}`}
      className="absolute bottom-full left-1/2 z-30 mb-3 w-80 -translate-x-1/2 rounded-lg border border-slate-200 bg-white p-3 text-left shadow-xl"
    >
      <div className="mb-3 flex items-start gap-2">
        <span
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm ${visual.bg} ${visual.text}`}
          aria-hidden="true"
        >
          {visual.emoji}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold text-slate-900">{agent.agentName}</p>
            {current ? <ShiftBadge shiftType={current.shift_type} /> : null}
          </div>
          {current?.emotional_state ? (
            <p className="mt-0.5 text-xs text-slate-500">状态：{current.emotional_state}</p>
          ) : null}
        </div>
      </div>

      {showR1Anchor || history.length ? (
        <div className="mb-3 space-y-2 border-b border-slate-100 pb-3">
          {showR1Anchor ? (
            <div className="flex gap-2 text-xs text-slate-400">
              <span aria-hidden="true">⊘</span>
              <p className="line-through">{current.r1_position}</p>
            </div>
          ) : null}
          {history.map((entry, index) => (
            <div key={`${entry.agent_id}-${index}-${entry.current_position}`} className="flex gap-2 text-xs text-slate-400">
              <span aria-hidden="true">⊘</span>
              <p className="line-through">{entry.current_position}</p>
            </div>
          ))}
        </div>
      ) : null}

      <div className="space-y-2">
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">当前立场</p>
          <p className="text-sm leading-5 text-slate-800">{current?.current_position ?? '等待 R1...'}</p>
        </div>
        {current?.shift_trigger ? (
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">转变触发</p>
            <p className="text-xs leading-5 text-slate-600">{current.shift_trigger}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function StanceCardStrip({ agentOrder, agentEvolutions }: StanceCardStripProps) {
  const [openAgentId, setOpenAgentId] = useState<string | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const columnCount = useMemo(() => Math.min(Math.max(agentOrder.length, 1), 5), [agentOrder.length]);

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (!stripRef.current?.contains(event.target as Node)) {
        setOpenAgentId(null);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpenAgentId(null);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  if (!agentOrder.length) return null;

  return (
    <div ref={stripRef} className="hidden border-t border-slate-200 bg-white lg:block">
      <div className="grid gap-2 px-4 py-3" style={{ gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }}>
        {agentOrder.map((agent) => {
          const visual = getVisual(agent.agentId);
          const evolutions = agentEvolutions.get(agent.agentId) ?? [];
          const current = latestEvolution(evolutions);
          const isOpen = openAgentId === agent.agentId;

          return (
            <div key={agent.agentId} className="relative min-w-0">
              <button
                type="button"
                aria-label={`查看 ${agent.agentName} 的立场变化`}
                className="flex h-20 w-full min-w-0 items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-left transition hover:border-slate-300 hover:bg-white focus:outline-none focus:ring-2 focus:ring-slate-300"
                onClick={() => setOpenAgentId((previous) => (previous === agent.agentId ? null : agent.agentId))}
              >
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm ${visual.bg} ${visual.text}`}
                  aria-hidden="true"
                >
                  {visual.emoji}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-xs font-semibold text-slate-700">{agent.agentName}</span>
                    {current ? <ShiftBadge shiftType={current.shift_type} /> : null}
                  </span>
                  <span
                    className="mt-1 block overflow-hidden text-xs leading-4 text-slate-600"
                    style={{
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                    }}
                  >
                    {current?.current_position ?? '等待 R1...'}
                  </span>
                </span>
              </button>

              {isOpen ? <StancePopover agent={agent} evolutions={evolutions} /> : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
