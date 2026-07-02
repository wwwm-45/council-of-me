import type { ProtectiveIntent } from '../../api/client';

interface Props {
  intents: ProtectiveIntent[];
}

/** Color palette for agent intent cards — warm, non-confrontational tones. */
const CARD_COLORS = [
  { bg: 'bg-orange-50', border: 'border-orange-100', badge: 'bg-orange-100 text-orange-700' },
  { bg: 'bg-teal-50', border: 'border-teal-100', badge: 'bg-teal-100 text-teal-700' },
  { bg: 'bg-violet-50', border: 'border-violet-100', badge: 'bg-violet-100 text-violet-700' },
  { bg: 'bg-rose-50', border: 'border-rose-100', badge: 'bg-rose-100 text-rose-700' },
  { bg: 'bg-lime-50', border: 'border-lime-100', badge: 'bg-lime-100 text-lime-700' },
];

export default function ProtectiveIntents({ intents }: Props) {
  if (!intents.length) return null;

  return (
    <section className="mb-6">
      <h3 className="text-sm font-medium text-slate-500 mb-1">每个声音的保护意图</h3>
      <p className="text-[11px] text-slate-400 mb-3">
        这些声音不是在互相对抗，而是各自在守护你内心不同的需要
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {intents.map((pi, idx) => {
          const color = CARD_COLORS[idx % CARD_COLORS.length];
          return (
            <div
              key={pi.agent_id || idx}
              className={`${color.bg} rounded-xl border ${color.border} p-4`}
            >
              {/* Agent name */}
              <p className="text-sm font-semibold text-slate-700 mb-2">
                {pi.agent_name}
              </p>

              {/* Intent */}
              <p className="text-sm text-slate-600 leading-relaxed mb-2">
                {pi.intent}
              </p>

              {/* What it protects */}
              {pi.what_it_protects && (
                <p className="text-xs text-slate-500 italic mb-2">
                  {pi.what_it_protects}
                </p>
              )}

              {/* Underlying value badge */}
              {pi.underlying_value && (
                <span
                  className={`inline-block text-[10px] px-2 py-0.5 rounded-full ${color.badge}`}
                >
                  {pi.underlying_value}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
