import type { PatternResult } from '../../api/client';

interface Props {
  patterns: PatternResult[];
}

const PATTERN_ICONS: Record<string, string> = {
  recurring_value_conflict: '&#x26A1;',   // lightning
  persistent_protective_value: '&#x1F6E1;', // shield
  convergence_trend: '&#x1F4C8;',          // chart
};

const PATTERN_COLORS: Record<string, string> = {
  recurring_value_conflict: 'bg-amber-50 border-amber-100 text-amber-700',
  persistent_protective_value: 'bg-teal-50 border-teal-100 text-teal-700',
  convergence_trend: 'bg-blue-50 border-blue-100 text-blue-700',
};

export default function PatternInsights({ patterns }: Props) {
  if (!patterns.length) return null;

  return (
    <section className="mb-6">
      <h3 className="text-sm font-medium text-slate-500 mb-3">跨会话洞察</h3>
      <div className="flex flex-wrap gap-2">
        {patterns.map((p, i) => {
          const color = PATTERN_COLORS[p.pattern_type] || 'bg-slate-50 border-slate-100 text-slate-700';
          const icon = PATTERN_ICONS[p.pattern_type] || '';

          return (
            <div key={i} className={`rounded-lg border px-3 py-2 ${color}`}>
              <div className="flex items-center gap-1.5">
                {icon && <span dangerouslySetInnerHTML={{ __html: icon }} className="text-sm" />}
                <span className="text-xs font-medium">{p.description}</span>
              </div>
              <p className="text-[10px] opacity-70 mt-0.5">
                出现 {p.occurrence_count} 次
              </p>
            </div>
          );
        })}
      </div>
    </section>
  );
}
