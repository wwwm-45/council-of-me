import { useState } from 'react';
import type { ReflectionDialogueTurn, ReflectionPathId } from '../../api/client';
import PathSelector from './PathSelector';

interface Props {
  nodeLabel: string;
  selectedPath: ReflectionPathId;
  currentLayer: number;
  turns: ReflectionDialogueTurn[];
  recommendationReason: string;
  onPathSelect: (path: ReflectionPathId) => void;
  onSubmit: (content: string) => Promise<void> | void;
  onSaveInsight: (content: string) => Promise<void> | void;
  onFinishNode: () => Promise<void> | void;
  onBackToExplore: () => void;
}

export default function DialoguePanel({
  nodeLabel,
  selectedPath,
  currentLayer,
  turns,
  recommendationReason,
  onPathSelect,
  onSubmit,
  onSaveInsight,
  onFinishNode,
  onBackToExplore,
}: Props) {
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSubmit() {
    const next = draft.trim();
    if (!next || busy) return;
    setBusy(true);
    try {
      await onSubmit(next);
      setDraft('');
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveInsight() {
    const next = draft.trim();
    if (!next || busy) return;
    setBusy(true);
    try {
      await onSaveInsight(next);
    } finally {
      setBusy(false);
    }
  }

  async function handleFinish() {
    const next = draft.trim();
    if (busy) return;
    setBusy(true);
    try {
      if (next) {
        await onSaveInsight(next);
      }
      await onFinishNode();
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex h-full min-h-0 flex-col bg-transparent p-7 text-slate-100">
      <header className="mb-5">
        <p className="text-xs font-medium uppercase tracking-[0.24em] text-indigo-400">探索方向</p>
        <h3 className="mt-2 text-2xl font-semibold text-white">{nodeLabel}</h3>
        <p className="mt-1 text-sm text-slate-400">当前深度 Layer {Math.max(1, currentLayer)}</p>
      </header>

      <PathSelector
        selected={selectedPath}
        recommendationReason={recommendationReason}
        onSelect={onPathSelect}
      />

      <div className="mt-5 flex-1 space-y-3 overflow-y-auto pr-1">
        {turns.map((turn) => (
          <article key={turn.turn_id || `${turn.role}-${turn.client_turn_id}`} className={turn.role === 'assistant' ? 'pr-10' : 'pl-10'}>
            <div
              className={[
                'rounded-2xl px-4 py-3 text-sm leading-relaxed shadow-sm',
                turn.role === 'assistant'
                  ? 'border border-white/5 bg-white/5 text-slate-200'
                  : 'ml-auto border border-indigo-400/20 bg-indigo-500/15 text-indigo-50',
              ].join(' ')}
            >
              {turn.content}
            </div>
          </article>
        ))}
      </div>

      <textarea
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="写下你此刻最真实的一句话。"
        className="mt-5 min-h-28 w-full rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-indigo-400 focus:bg-white/10"
      />

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={busy || draft.trim().length === 0}
          className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          继续
        </button>
        <button
          type="button"
          onClick={handleSaveInsight}
          disabled={busy || draft.trim().length === 0}
          className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-300 transition hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          写下洞察
        </button>
        <button
          type="button"
          onClick={handleFinish}
          disabled={busy}
          className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-slate-200 disabled:cursor-not-allowed disabled:opacity-50"
        >
          先收拢这一站
        </button>
        <button
          type="button"
          onClick={onBackToExplore}
          disabled={busy}
          className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-sm text-slate-400 transition hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          回到图景
        </button>
      </div>
    </section>
  );
}
