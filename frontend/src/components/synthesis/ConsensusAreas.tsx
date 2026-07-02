import type { ConsensusArea } from '../../api/client';

interface Props {
  areas: ConsensusArea[];
}

export default function ConsensusAreas({ areas }: Props) {
  if (!areas.length) return null;

  return (
    <section className="mb-6">
      <h3 className="text-sm font-medium text-slate-500 mb-3">共识区域</h3>
      <div className="space-y-3">
        {areas.map((area) => (
          <div
            key={area.area_id}
            className="bg-emerald-50 rounded-xl border border-emerald-100 p-4"
          >
            <div className="flex items-start gap-2">
              <span className="text-emerald-500 mt-0.5 flex-shrink-0">&#10003;</span>
              <div className="flex-1">
                <p className="text-sm text-slate-700 font-medium leading-relaxed">
                  {area.description}
                </p>

                {/* Supporting agents */}
                {area.supporting_agents.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-2">
                    {area.supporting_agents.map((agent) => (
                      <span
                        key={agent}
                        className="text-[10px] bg-emerald-100 text-emerald-700 px-1.5 py-0.5 rounded-full"
                      >
                        {agent}
                      </span>
                    ))}
                  </div>
                )}

                {/* Evidence quotes */}
                {area.evidence.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {area.evidence.map((ev, idx) => (
                      <p
                        key={idx}
                        className="text-[11px] text-slate-500 italic pl-2 border-l-2 border-emerald-200"
                      >
                        {ev}
                      </p>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
