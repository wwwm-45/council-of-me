import type { SynthesisCard as CardData } from '../../api/client';

interface Props {
  card: CardData;
  selected?: boolean;
  onClick?: () => void;
}

export default function SynthesisCard({ card, selected, onClick }: Props) {
  const date = card.created_at
    ? new Date(card.created_at).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
    : '';

  const typeLabel = card.synthesis_type === 'CONSENSUS_MAP' ? '共识' : '多声';
  const typeBg = card.synthesis_type === 'CONSENSUS_MAP'
    ? 'bg-emerald-100 text-emerald-700'
    : 'bg-violet-100 text-violet-700';

  return (
    <div
      onClick={onClick}
      className={`rounded-xl border p-3 cursor-pointer transition-all ${
        selected
          ? 'border-blue-400 bg-blue-50 shadow-sm'
          : 'border-slate-100 bg-white hover:border-slate-200'
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[10px] text-slate-400">{date}</span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${typeBg}`}>
          {typeLabel}
        </span>
      </div>

      <p className="text-sm text-slate-700 font-medium truncate mb-2">
        {card.core_dilemma || '(未命名困境)'}
      </p>

      <div className="flex items-center gap-3 text-[10px] text-slate-400">
        {card.tension_count > 0 && (
          <span>{card.tension_count} 个张力</span>
        )}
        {card.debate_rounds && (
          <span>{card.debate_rounds} 轮</span>
        )}
        {card.convergence_score != null && (
          <div className="flex items-center gap-1">
            <span>收敛</span>
            <div className="w-12 h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-400 rounded-full"
                style={{ width: `${Math.round(card.convergence_score * 100)}%` }}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
