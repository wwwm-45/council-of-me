import type { FeelingId } from './types';
import { FEELING_LABELS } from './types';

interface Props {
  selected: FeelingId[];
  onChange: (feelings: FeelingId[]) => void;
}

const FEELING_OPTIONS = Object.entries(FEELING_LABELS) as Array<[FeelingId, string]>;

export default function FeelingSelector({ selected, onChange }: Props) {
  function toggleFeeling(feeling: FeelingId) {
    const next = selected.includes(feeling)
      ? selected.filter((item) => item !== feeling)
      : [...selected, feeling];
    onChange(next);
  }

  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs font-medium uppercase tracking-[0.24em] text-slate-400">感受标记</p>
        <p className="mt-1 text-sm text-slate-300">先记录你对这个节点的第一反应，再决定要不要继续往里走。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {FEELING_OPTIONS.map(([feeling, label]) => {
          const active = selected.includes(feeling);
          return (
            <button
              key={feeling}
              type="button"
              aria-pressed={active}
              onClick={() => toggleFeeling(feeling)}
              className={[
                'rounded-full border px-3 py-2 text-sm transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400',
                active
                  ? 'border-indigo-400 bg-indigo-500/20 text-indigo-100 shadow-[0_0_16px_rgba(99,102,241,0.2)]'
                  : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:bg-white/10 hover:text-white',
              ].join(' ')}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
