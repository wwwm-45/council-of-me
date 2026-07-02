import type { EmotionEntry } from './types';

interface EmotionSectionProps {
  emotions: EmotionEntry[];
}

const EMOTION_STYLES: Record<string, string> = {
  焦虑: 'bg-rose-50 text-rose-600 border-rose-100',
  恐惧: 'bg-rose-50 text-rose-600 border-rose-100',
  渴望: 'bg-sky-50 text-sky-600 border-sky-100',
  希望: 'bg-emerald-50 text-emerald-600 border-emerald-100',
};

export default function EmotionSection({ emotions }: EmotionSectionProps) {
  return (
    <section className="rounded-[2rem] border border-slate-200/70 bg-white/90 p-6 shadow-[0_18px_50px_-30px_rgba(15,23,42,0.45)] backdrop-blur">
      <p className="text-[11px] uppercase tracking-[0.24em] text-slate-400">情绪气候</p>
      <div className="mt-4 flex flex-wrap gap-3">
        {emotions.map((entry, index) => (
          <div
            key={`${entry.emotion}-${index}`}
            className={`rounded-full border px-4 py-2 ${EMOTION_STYLES[entry.emotion] ?? 'bg-slate-50 text-slate-600 border-slate-200'}`}
          >
            <p className="text-sm font-medium">{entry.emotion}</p>
            <p className="mt-1 text-xs text-slate-500">{entry.context || '在对话里反复出现'}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
