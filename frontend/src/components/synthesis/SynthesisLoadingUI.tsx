interface Props {
  activeStages: Set<string>;
  completedStages: Set<string>;
}

const STAGES = [
  { key: 'convergence', label: '分析收敛趋势' },
  { key: 'tensions',    label: '识别核心张力' },
  { key: 'intents',     label: '解读保护意图' },
  { key: 'narrative',   label: '编织综合叙述' },
];

export default function SynthesisLoadingUI({ activeStages, completedStages }: Props) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] animate-fade-in">
      <h2 className="text-lg font-bold text-slate-700 mb-6">正在生成综合图景</h2>
      <div className="flex flex-col items-start gap-0">
        {STAGES.map((s, i) => {
          const done = completedStages.has(s.key);
          const active = activeStages.has(s.key);
          const isLast = i === STAGES.length - 1;
          return (
            <div key={s.key} className="flex items-start gap-3">
              {/* Status dot + connector */}
              <div className="flex flex-col items-center">
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold transition-all duration-300 ${
                    done
                      ? 'bg-emerald-500 text-white'
                      : active
                        ? 'bg-blue-500 text-white animate-stage-pulse'
                        : 'bg-slate-200 text-slate-400'
                  }`}
                >
                  {done ? '✓' : active ? (i + 1) : (i + 1)}
                </div>
                {!isLast && (
                  <div
                    className={`w-0.5 h-6 transition-colors duration-300 ${
                      done ? 'bg-emerald-300' : 'bg-slate-200'
                    }`}
                  />
                )}
              </div>
              {/* Label */}
              <span
                className={`text-sm pt-0.5 transition-colors duration-300 ${
                  done
                    ? 'text-emerald-600 font-medium'
                    : active
                      ? 'text-blue-600 font-medium'
                      : 'text-slate-400'
                }`}
              >
                {s.label}
                {active && <span className="ml-1.5 text-blue-400 animate-pulse">…</span>}
              </span>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-slate-300 mt-6">首次综合需要 10-20 秒，再次查看将瞬间加载</p>
    </div>
  );
}
