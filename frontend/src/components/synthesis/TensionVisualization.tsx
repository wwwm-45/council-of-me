import { useState } from 'react';
import type { CoreTension } from '../../api/client';
import TensionExcerpts from './TensionExcerpts';

interface Props {
  tensions: CoreTension[];
}

export default function TensionVisualization({ tensions }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!tensions.length) return null;

  const hasAnyEvidence = tensions.some(
    (t) =>
      (t.pole_a.evidence_statements?.length ?? 0) > 0 ||
      (t.pole_b.evidence_statements?.length ?? 0) > 0,
  );

  function toggleExpand(id: string) {
    setExpandedId((prev) => (prev === id ? null : id));
  }

  return (
    <section className="mb-6">
      <h3 className="text-sm font-medium text-slate-500 mb-3">核心张力</h3>
      <div className="space-y-4">
        {tensions.map((t) => {
          const isExpanded = expandedId === t.tension_id;
          const poleAEvidence = t.pole_a.evidence_statements ?? [];
          const poleBEvidence = t.pole_b.evidence_statements ?? [];
          const hasEvidence = poleAEvidence.length > 0 || poleBEvidence.length > 0;

          return (
            <div
              key={t.tension_id}
              className={`bg-white rounded-2xl border border-slate-100 shadow-sm p-5 transition-all ${
                hasEvidence ? 'cursor-pointer hover:border-slate-200' : ''
              }`}
              onClick={hasEvidence ? () => toggleExpand(t.tension_id) : undefined}
            >
              {/* Tension name */}
              <p className="text-sm font-semibold text-slate-700 mb-3 text-center">
                {t.name}
              </p>

              {/* Poles */}
              <div className="flex items-stretch gap-3">
                {/* Pole A */}
                <div className="flex-1 bg-amber-50 rounded-xl p-3 border border-amber-100">
                  <p className="text-xs font-semibold text-amber-700 mb-1">
                    {t.pole_a.label}
                  </p>
                  <p className="text-xs text-slate-600 leading-relaxed">
                    {t.pole_a.stance}
                  </p>
                  {t.pole_a.agents.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {t.pole_a.agents.map((a) => (
                        <span
                          key={a}
                          className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full"
                        >
                          {a}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Intensity bar (vertical divider) */}
                <div className="flex flex-col items-center justify-center w-8">
                  <span className="text-[10px] text-slate-400 mb-1">张力</span>
                  <div className="w-1.5 flex-1 bg-slate-100 rounded-full overflow-hidden relative">
                    <div
                      className="absolute bottom-0 w-full rounded-full transition-all"
                      style={{
                        height: `${Math.round(t.intensity * 100)}%`,
                        background: `linear-gradient(to top, #f59e0b, #ef4444)`,
                      }}
                    />
                  </div>
                  <span className="text-[10px] text-slate-400 mt-1">
                    {Math.round(t.intensity * 100)}%
                  </span>
                </div>

                {/* Pole B */}
                <div className="flex-1 bg-sky-50 rounded-xl p-3 border border-sky-100">
                  <p className="text-xs font-semibold text-sky-700 mb-1">
                    {t.pole_b.label}
                  </p>
                  <p className="text-xs text-slate-600 leading-relaxed">
                    {t.pole_b.stance}
                  </p>
                  {t.pole_b.agents.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {t.pole_b.agents.map((a) => (
                        <span
                          key={a}
                          className="text-[10px] bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded-full"
                        >
                          {a}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Value conflict label */}
              {t.value_conflict && (t.value_conflict.value_a || t.value_conflict.value_b) && (
                <p className="text-[10px] text-slate-400 text-center mt-3">
                  价值维度：{t.value_conflict.value_a} ↔ {t.value_conflict.value_b}
                </p>
              )}

              {/* Click hint */}
              {hasEvidence && !isExpanded && (
                <p className="text-[10px] text-slate-300 text-center mt-2">
                  点击查看辩论原文
                </p>
              )}

              {/* Expanded excerpts */}
              {isExpanded && (
                <TensionExcerpts
                  poleAEvidence={poleAEvidence}
                  poleBEvidence={poleBEvidence}
                  poleALabel={t.pole_a.label}
                  poleBLabel={t.pole_b.label}
                />
              )}
            </div>
          );
        })}
      </div>

      {/* Global hint if no evidence */}
      {!hasAnyEvidence && (
        <p className="text-[10px] text-slate-300 text-center mt-2">
          张力分析基于LLM提取，暂无原文映射
        </p>
      )}
    </section>
  );
}
