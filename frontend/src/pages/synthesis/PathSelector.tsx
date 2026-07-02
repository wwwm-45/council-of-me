import type { ReflectionPathId } from './types';

const PATH_OPTIONS: Array<{
  id: ReflectionPathId;
  title: string;
  description: string;
}> = [
  {
    id: 'emotional',
    title: '情绪停留',
    description: '先待在感觉里，看它想说什么。',
  },
  {
    id: 'assumption',
    title: '假设松动',
    description: '拆开当下最难松手的前提。',
  },
  {
    id: 'protective',
    title: '保护意图',
    description: '看见这个反应在替你守护什么。',
  },
  {
    id: 'action',
    title: '行动试探',
    description: '把发现带向一个很小的下一步。',
  },
];

interface Props {
  selected: ReflectionPathId;
  recommendationReason?: string;
  onSelect: (path: ReflectionPathId) => void;
}

export default function PathSelector({ selected, recommendationReason, onSelect }: Props) {
  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-[0.24em] text-slate-400">探索路径</p>
        {recommendationReason ? (
          <p className="text-xs text-indigo-300">{recommendationReason}</p>
        ) : null}
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        {PATH_OPTIONS.map((option) => {
          const active = option.id === selected;
          return (
            <button
              key={option.id}
              type="button"
              onClick={() => onSelect(option.id)}
              className={[
                'rounded-2xl border px-4 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400',
                active
                  ? 'border-indigo-400 bg-indigo-500/20 text-indigo-100 shadow-[0_0_18px_rgba(99,102,241,0.16)]'
                  : 'border-white/10 bg-white/5 text-slate-300 hover:border-white/20 hover:bg-white/10 hover:text-white',
              ].join(' ')}
            >
              <p className="text-sm font-semibold">{option.title}</p>
              <p className="mt-1 text-xs leading-relaxed text-inherit/80">{option.description}</p>
            </button>
          );
        })}
      </div>
    </section>
  );
}
