interface QuoteBlockProps {
  quote: string;
  sourceEmotion?: string;
}

export default function QuoteBlock({ quote, sourceEmotion }: QuoteBlockProps) {
  return (
    <blockquote className="rounded-3xl border border-amber-200/70 bg-gradient-to-r from-amber-50 via-white to-rose-50 px-5 py-4 shadow-sm">
      <p className="text-[11px] uppercase tracking-[0.22em] text-amber-500">你的原话</p>
      <p className="mt-2 text-base leading-7 text-slate-700">“{quote}”</p>
      {sourceEmotion ? <p className="mt-2 text-xs text-slate-400">关联情绪：{sourceEmotion}</p> : null}
    </blockquote>
  );
}
