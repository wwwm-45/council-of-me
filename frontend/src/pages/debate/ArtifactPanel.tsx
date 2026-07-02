import { useState } from 'react';

import ArtifactCard from './ArtifactCard';
import type { ArtifactEvent, ConvergenceMapData } from './types';

const ROUNDS: Array<{ round: number; label: string }> = [
  { round: 1, label: 'R1 立场图谱' },
  { round: 2, label: 'R2 张力图谱' },
  { round: 3, label: 'R3 参与记录' },
  { round: 4, label: 'R4 共识图谱' },
];

interface ArtifactPanelProps {
  currentRound: number;
  roundArtifacts: Map<number, ArtifactEvent>;
  convergenceMap: ConvergenceMapData | null;
}

export default function ArtifactPanel({
  currentRound,
  roundArtifacts,
  convergenceMap,
}: ArtifactPanelProps) {
  const [overrides, setOverrides] = useState<Map<number, boolean>>(new Map());

  function isExpanded(round: number) {
    if (overrides.has(round)) return overrides.get(round)!;
    return round === currentRound;
  }

  function toggle(round: number) {
    setOverrides((prev) => {
      const next = new Map(prev);
      const current = prev.has(round) ? prev.get(round)! : round === currentRound;
      next.set(round, !current);
      return next;
    });
  }

  return (
    <div className="hidden lg:flex w-80 border-l border-slate-200 flex-col bg-white flex-shrink-0">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700">图谱</h3>
        {currentRound > 0 && (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-400">
            第 {currentRound} 轮
          </span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto px-1 py-2">
        {ROUNDS.map(({ round, label }) => {
          const artifact = roundArtifacts.get(round);
          if (!artifact) {
            return (
              <div
                key={round}
                className="my-3 mx-2 border border-dashed border-slate-200 rounded-lg bg-slate-50 px-3 py-3 text-center"
              >
                <p className="text-[10px] font-semibold text-slate-400">{label}</p>
                <p className="mt-1 text-[10px] text-slate-400">等待 R{round} 完成...</p>
              </div>
            );
          }

          return (
            <ArtifactCard
              key={round}
              round={round}
              artifact={artifact}
              convergenceMap={round === 4 ? convergenceMap : null}
              expanded={isExpanded(round)}
              onToggle={() => toggle(round)}
            />
          );
        })}
      </div>
    </div>
  );
}
