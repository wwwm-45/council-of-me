import type { ComparisonResult } from '../../api/client';

interface Props {
  comparison: ComparisonResult;
}

export default function ComparisonView({ comparison }: Props) {
  const { synthesis_a: a, synthesis_b: b, shared_tension_themes, shared_value_conflicts, convergence_delta } = comparison;

  return (
    <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5 animate-fade-in">
      <h3 className="text-sm font-medium text-slate-700 mb-4 text-center">综合对比</h3>

      {/* Header: two dilemmas */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="text-center">
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
            a.type === 'CONSENSUS_MAP' ? 'bg-emerald-100 text-emerald-700' : 'bg-violet-100 text-violet-700'
          }`}>
            {a.type === 'CONSENSUS_MAP' ? '共识' : '多声'}
          </span>
          <p className="text-xs text-slate-600 mt-1 truncate">{a.dilemma || '会话A'}</p>
        </div>
        <div className="text-center">
          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
            b.type === 'CONSENSUS_MAP' ? 'bg-emerald-100 text-emerald-700' : 'bg-violet-100 text-violet-700'
          }`}>
            {b.type === 'CONSENSUS_MAP' ? '共识' : '多声'}
          </span>
          <p className="text-xs text-slate-600 mt-1 truncate">{b.dilemma || '会话B'}</p>
        </div>
      </div>

      {/* Shared tensions */}
      {shared_tension_themes.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-slate-500 mb-2">共同张力主题</p>
          <div className="space-y-2">
            {shared_tension_themes.map((st, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className="bg-amber-50 px-2 py-1 rounded border border-amber-100 text-amber-700 truncate flex-1">
                  {st.tension_a}
                </span>
                <span className="text-slate-300">&harr;</span>
                <span className="bg-sky-50 px-2 py-1 rounded border border-sky-100 text-sky-700 truncate flex-1">
                  {st.tension_b}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Shared value conflicts */}
      {shared_value_conflicts.length > 0 && (
        <div className="mb-4">
          <p className="text-xs font-medium text-slate-500 mb-2">共同价值冲突</p>
          <div className="flex flex-wrap gap-1.5">
            {shared_value_conflicts.map((vc) => (
              <span key={vc} className="text-[10px] bg-rose-50 text-rose-600 px-2 py-0.5 rounded-full border border-rose-100">
                {vc}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Convergence delta */}
      <div className="text-center">
        <p className="text-xs text-slate-400 mb-1">收敛度变化</p>
        <span className={`text-lg font-semibold ${
          convergence_delta > 0 ? 'text-emerald-600' : convergence_delta < 0 ? 'text-rose-600' : 'text-slate-400'
        }`}>
          {convergence_delta > 0 ? '+' : ''}{Math.round(convergence_delta * 100)}%
        </span>
      </div>

      {/* Empty state */}
      {shared_tension_themes.length === 0 && shared_value_conflicts.length === 0 && (
        <p className="text-xs text-slate-400 text-center py-4">
          两次综合之间未发现明显的共同主题
        </p>
      )}
    </div>
  );
}
