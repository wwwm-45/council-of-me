import type { AgentAssignment, ComplexityAssessment } from './types';

interface CouncilSectionProps {
  complexity: ComplexityAssessment;
  assignments: AgentAssignment[];
  selectedLevel: string;
  onLevelChange: (level: string) => Promise<void> | void;
}

const LEVEL_LABELS: Record<string, string> = {
  L1: '聚焦梳理',
  L2: '多方拉扯',
  L3: '深层议会',
};

export default function CouncilSection({
  complexity,
  assignments,
  selectedLevel,
  onLevelChange,
}: CouncilSectionProps) {
  return (
    <section className="rounded-[2rem] border border-slate-200/70 bg-white/90 p-6 shadow-[0_18px_50px_-30px_rgba(15,23,42,0.45)] backdrop-blur">
      <p className="text-[11px] uppercase tracking-[0.24em] text-slate-400">议会组建</p>
      <p className="mt-3 rounded-3xl bg-slate-50 px-4 py-4 text-sm leading-7 text-slate-600">
        {complexity.narrative}
      </p>

      <div className="mt-5 space-y-3">
        {assignments.map((assignment) => (
          <article key={`${assignment.agent_role}-${assignment.voice_name}`} className="rounded-3xl border border-slate-200/70 bg-white px-4 py-4">
            <div className="flex items-center gap-2 text-sm text-slate-500">
              {assignment.voice_name ? <span>{assignment.voice_name}</span> : <span>补位视角</span>}
              <span>→</span>
              <span className="font-semibold text-slate-700">{assignment.display_name}</span>
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-500">{assignment.mapping_reason}</p>
          </article>
        ))}
      </div>

      <div className="mt-6">
        <p className="text-[11px] uppercase tracking-[0.2em] text-slate-400">辩论层级</p>
        <div className="mt-3 grid grid-cols-3 gap-2">
          {(['L1', 'L2', 'L3'] as const).map((level) => (
            <button
              key={level}
              onClick={() => onLevelChange(level)}
              className={`rounded-2xl border px-3 py-3 text-sm transition ${
                selectedLevel === level
                  ? 'border-slate-900 bg-slate-900 text-white'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300'
              }`}
            >
              <span className="block font-semibold">{level}</span>
              <span className="mt-1 block text-xs opacity-80">{LEVEL_LABELS[level]}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
