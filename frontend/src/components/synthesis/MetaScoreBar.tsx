import type { SynthesisMeta } from '../../api/client';

interface Props {
  meta: SynthesisMeta;
}

const BARS = [
  { key: 'convergence_score' as const, label: '收敛度', color: 'bg-emerald-400' },
  { key: 'novelty_score' as const, label: '新颖度', color: 'bg-blue-400' },
  { key: 'value_conflict_intensity' as const, label: '价值冲突', color: 'bg-amber-400' },
];

export default function MetaScoreBar({ meta }: Props) {
  return (
    <section className="mb-5">
      <div className="flex gap-4">
        {BARS.map(({ key, label, color }) => {
          const value = meta[key];
          if (value == null) return null;
          const pct = Math.round(value * 100);

          return (
            <div key={key} className="flex-1">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-slate-500">{label}</span>
                <span className="text-[10px] text-slate-400">{pct}%</span>
              </div>
              <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${color}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
