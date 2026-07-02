import type { TensionEvidence } from '../../api/client';

interface Props {
  poleAEvidence: TensionEvidence[];
  poleBEvidence: TensionEvidence[];
  poleALabel: string;
  poleBLabel: string;
}

function EvidenceCard({ ev, colorScheme }: { ev: TensionEvidence; colorScheme: 'amber' | 'sky' }) {
  const bg = colorScheme === 'amber' ? 'bg-amber-25 border-amber-100' : 'bg-sky-25 border-sky-100';
  const badge = colorScheme === 'amber' ? 'bg-amber-100 text-amber-700' : 'bg-sky-100 text-sky-700';

  return (
    <div className={`rounded-lg border p-3 ${bg}`}>
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${badge}`}>
          {ev.agent_name}
        </span>
        <span className="text-[10px] text-slate-400">
          R{ev.round_number}
        </span>
      </div>
      <p className="text-xs text-slate-600 leading-relaxed">
        {ev.content}
      </p>
    </div>
  );
}

export default function TensionExcerpts({ poleAEvidence, poleBEvidence, poleALabel, poleBLabel }: Props) {
  const hasA = poleAEvidence.length > 0;
  const hasB = poleBEvidence.length > 0;

  if (!hasA && !hasB) {
    return (
      <p className="text-xs text-slate-400 text-center py-3">
        暂无相关辩论片段
      </p>
    );
  }

  return (
    <div className="mt-3 pt-3 border-t border-slate-100 animate-slide-up">
      <p className="text-[10px] text-slate-400 mb-2 text-center">辩论原文</p>
      <div className="grid grid-cols-2 gap-3">
        {/* Pole A evidence */}
        <div className="space-y-2">
          <p className="text-[10px] font-medium text-amber-600">{poleALabel}</p>
          {hasA ? (
            poleAEvidence.map((ev, i) => (
              <EvidenceCard key={ev.statement_id || i} ev={ev} colorScheme="amber" />
            ))
          ) : (
            <p className="text-[10px] text-slate-300 italic">无证据</p>
          )}
        </div>

        {/* Pole B evidence */}
        <div className="space-y-2">
          <p className="text-[10px] font-medium text-sky-600">{poleBLabel}</p>
          {hasB ? (
            poleBEvidence.map((ev, i) => (
              <EvidenceCard key={ev.statement_id || i} ev={ev} colorScheme="sky" />
            ))
          ) : (
            <p className="text-[10px] text-slate-300 italic">无证据</p>
          )}
        </div>
      </div>
    </div>
  );
}
