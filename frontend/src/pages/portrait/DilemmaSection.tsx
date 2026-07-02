import { useEffect, useState } from 'react';

import type { DilemmaLayer } from './types';

interface DilemmaSectionProps {
  coreDilemma: string;
  dilemmaLayers: DilemmaLayer[];
  onDilemmaChange: (value: string) => Promise<void> | void;
}

const DEPTH_LABELS: Record<string, string> = {
  surface: '表层',
  emotional: '情绪层',
  existential: '存在层',
};

const DEPTH_STYLES: Record<string, string> = {
  surface: 'border-l-sky-400 bg-sky-50/70',
  emotional: 'border-l-amber-400 bg-amber-50/80',
  existential: 'border-l-rose-400 bg-rose-50/80',
};

function normalizeText(value: string) {
  return value.trim().replace(/\s+/g, ' ');
}

function formatDescription(layer: DilemmaLayer) {
  const description = normalizeText(layer.description);
  const userLanguage = normalizeText(layer.user_language);

  if (!description) {
    return userLanguage;
  }

  if (!userLanguage || description !== userLanguage) {
    return description;
  }

  switch (layer.depth) {
    case 'emotional':
      return `情绪上，这首先表现为：${userLanguage}`;
    case 'existential':
      return `更深一层，这碰到的是：${userLanguage}`;
    case 'surface':
    default:
      return `表面上，这首先表现为：${userLanguage}`;
  }
}

export default function DilemmaSection({ coreDilemma, dilemmaLayers, onDilemmaChange }: DilemmaSectionProps) {
  const [draft, setDraft] = useState(coreDilemma);

  useEffect(() => {
    setDraft(coreDilemma);
  }, [coreDilemma]);

  async function handleBlur() {
    const next = draft.trim();
    if (next && next !== coreDilemma) {
      await onDilemmaChange(next);
    } else {
      setDraft(coreDilemma);
    }
  }

  return (
    <section className="rounded-[2rem] border border-slate-200/70 bg-white/90 p-6 shadow-[0_18px_50px_-30px_rgba(15,23,42,0.45)] backdrop-blur">
      <div className="mb-5">
        <p className="text-[11px] uppercase tracking-[0.24em] text-slate-400">困境全景</p>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={handleBlur}
          rows={2}
          className="mt-3 w-full resize-none border-none bg-transparent px-0 text-2xl leading-9 text-slate-800 focus:outline-none"
        />
      </div>

      <div className="grid gap-3 md:grid-cols-3">
        {dilemmaLayers.map((layer, index) => {
          const userLanguage = normalizeText(layer.user_language);

          return (
            <article
              key={`${layer.depth}-${index}`}
              className={`rounded-2xl border border-slate-200/60 border-l-4 p-4 ${DEPTH_STYLES[layer.depth] ?? DEPTH_STYLES.surface}`}
            >
              <p className="text-[11px] uppercase tracking-[0.2em] text-slate-500">
                {DEPTH_LABELS[layer.depth] ?? layer.depth}
              </p>
              <p className="mt-2 text-sm font-medium leading-6 text-slate-700">{formatDescription(layer)}</p>
              {userLanguage ? <p className="mt-3 text-xs leading-5 text-slate-500">“{userLanguage}”</p> : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}
