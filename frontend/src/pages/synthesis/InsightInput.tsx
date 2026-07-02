import { useEffect, useState } from 'react';
import type { FeelingId } from './types';
import { FEELING_LABELS } from './types';

interface Props {
  nodeLabel: string;
  feelings: FeelingId[];
  value: string;
  onChange: (next: string) => void;
}

export default function InsightInput({ nodeLabel, feelings, value, onChange }: Props) {
  const [draft, setDraft] = useState(value);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  return (
    <article className="rounded-[28px] border border-white/10 bg-white/5 px-5 py-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-semibold text-white">{nodeLabel}</h4>
        {feelings.map((feeling) => (
          <span
            key={feeling}
            className="rounded-full border border-indigo-400/15 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-200"
          >
            {FEELING_LABELS[feeling]}
          </span>
        ))}
      </div>
      <textarea
        value={draft}
        onChange={(event) => {
          setDraft(event.target.value);
          onChange(event.target.value);
        }}
        placeholder="把这里的发现写成一句你愿意带走的话。"
        className="mt-3 min-h-24 w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:bg-white/10"
      />
    </article>
  );
}
