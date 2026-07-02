import { useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  Menu,
  Sparkles,
  X,
} from 'lucide-react';
import type { SynthesisResponse } from '../../api/client';
import { AGENT_COLORS } from './types';

interface DataSidebarProps {
  data: SynthesisResponse;
  immersiveActive?: boolean;
  onFinish: () => void;
}

const FALLBACK_COLOR = '#94a3b8';

export default function DataSidebar({ data, immersiveActive = false, onFinish }: DataSidebarProps) {
  const [open, setOpen] = useState(false);
  const synthesisLabel = data.synthesis_type === 'CONSENSUS_MAP' ? '共识星图' : '多声部全景';
  const hasOutcomes =
    data.consensus_areas.length > 0 ||
    data.productive_tensions.length > 0 ||
    data.irreducible_differences.length > 0;

  return (
    <>
      <div
        className={`pointer-events-none absolute right-8 top-8 z-20 flex animate-pulse-slow items-center gap-2.5 rounded-full border border-indigo-400/30 bg-indigo-950/40 px-5 py-2.5 text-indigo-100 backdrop-blur-md transition-all duration-700 ${
          immersiveActive ? '-translate-y-4 scale-95 opacity-0' : 'translate-y-0 scale-100 opacity-100'
        }`}
      >
        <Sparkles aria-hidden="true" className="h-5 w-5 text-indigo-300" />
        <span className="text-[13px] font-bold tracking-[0.2em] drop-shadow-md">点击星辰进入探索</span>
      </div>

      <button
        type="button"
        aria-label="展开综合图景数据"
        onClick={() => setOpen(true)}
        className={`absolute left-6 top-6 z-20 rounded-full border border-white/10 bg-white/5 p-3.5 text-slate-200 backdrop-blur-md transition-all hover:bg-white/10 hover:text-white ${
          open ? 'pointer-events-none scale-90 opacity-0' : 'pointer-events-auto'
        }`}
      >
        <Menu aria-hidden="true" className="h-5 w-5" />
      </button>

      <aside
        aria-label="综合图景参考数据"
        className={`absolute bottom-0 left-0 top-0 z-30 flex w-[420px] max-w-[100vw] flex-col border-r border-white/10 bg-slate-950/80 text-slate-200 shadow-2xl backdrop-blur-3xl transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex shrink-0 items-start justify-between border-b border-white/10 p-6 pb-4">
          <div className="flex-1 pr-4">
            <div className="mb-3 flex items-center gap-2">
              <span className="rounded-full border border-indigo-500/20 bg-indigo-500/10 px-3 py-1.5 text-[10px] font-bold uppercase tracking-[0.2em] text-indigo-400">
                {synthesisLabel}
              </span>
            </div>
            <h2 className="mb-4 text-xl font-bold leading-tight text-white">{data.dilemma_text}</h2>
            <dl className="grid grid-cols-3 gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-400">
              <div className="flex flex-col gap-1">
                <dt className="opacity-70">收敛度</dt>
                <dd data-meta="convergence" className="text-sm text-emerald-400">
                  {Math.round(data.meta.convergence_score * 100)}%
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="opacity-70">冲突强度</dt>
                <dd data-meta="conflict" className="text-sm text-amber-400">
                  {Math.round(data.meta.value_conflict_intensity * 100)}%
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="opacity-70">对谈轮次</dt>
                <dd data-meta="rounds" className="text-sm text-white">
                  {data.meta.debate_rounds}
                </dd>
              </div>
            </dl>
          </div>
          <button
            type="button"
            aria-label="收起综合图景数据"
            onClick={() => setOpen(false)}
            className="mt-1 rounded-full bg-white/5 p-2 text-slate-400 transition-colors hover:bg-white/10 hover:text-white"
          >
            <X aria-hidden="true" className="h-5 w-5" />
          </button>
        </div>

        <div className="custom-scrollbar min-h-0 flex-1 space-y-8 overflow-y-auto px-6 py-6">
          <div className="rounded-[20px] border border-indigo-500/20 bg-indigo-900/30 p-5">
            {data.key_insight ? (
              <div className="mb-3 text-sm font-medium leading-relaxed text-indigo-200">{data.key_insight}</div>
            ) : null}
            <p className="whitespace-pre-line text-xs leading-relaxed text-slate-400">{data.narrative}</p>
          </div>

          {data.voice_positions.length > 0 ? (
            <section className="space-y-4">
              <h3 className="pl-2 text-xs font-bold uppercase tracking-widest text-slate-400">参与声部</h3>
              {data.voice_positions.map((voice) => {
                const color = AGENT_COLORS[voice.agent_id] ?? FALLBACK_COLOR;
                const evolution = data.agent_evolutions.find((item) => item.agent_id === voice.agent_id);
                return (
                  <article
                    key={voice.agent_id}
                    className="relative overflow-hidden rounded-[20px] border border-white/10 bg-white/5 p-4"
                  >
                    <div className="mb-2 flex items-center gap-2">
                      <span className="h-3 w-3 rounded-full" style={{ backgroundColor: color }} />
                      <span className="text-sm font-bold tracking-wide text-white">{voice.agent_name}</span>
                    </div>
                    <p className="mb-3 text-xs font-medium leading-relaxed text-slate-300">{voice.core_stance}</p>
                    {evolution ? (
                      <div className="rounded-xl border border-white/5 bg-black/30 p-2.5 text-[10px]">
                        <div className="mb-1.5 flex items-center gap-1.5 text-slate-400 opacity-80">
                          <span className="line-through">{evolution.r1_position}</span>
                          <ArrowRight aria-hidden="true" className="h-2.5 w-2.5 shrink-0" />
                        </div>
                        <div className="font-medium text-slate-200">{evolution.current_position}</div>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </section>
          ) : null}

          {data.core_tensions.length > 0 ? (
            <section className="space-y-3">
              <h3 className="flex items-center gap-2 pl-2 text-xs font-bold uppercase tracking-widest text-slate-400">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-500" /> 核心张力
              </h3>
              {data.core_tensions.map((tension) => (
                <article
                  key={tension.tension_id}
                  className="rounded-[20px] border border-white/10 bg-gradient-to-br from-white/5 to-transparent p-4"
                >
                  <div className="mb-3 flex items-center justify-between">
                    <h4 className="text-sm font-bold text-white">{tension.name}</h4>
                    <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-[9px] font-bold text-amber-300">
                      强度 {Math.round(tension.intensity * 100)}%
                    </span>
                  </div>
                  <div className="space-y-3">
                    <div className="rounded-xl border-l-2 border-amber-500/50 bg-black/20 p-3">
                      <div className="mb-1 text-[10px] font-bold text-amber-200">{tension.pole_a.label}</div>
                      <div className="text-xs text-slate-300">{tension.pole_a.stance}</div>
                    </div>
                    <div className="rounded-xl border-l-2 border-sky-500/50 bg-black/20 p-3">
                      <div className="mb-1 text-[10px] font-bold text-sky-200">{tension.pole_b.label}</div>
                      <div className="text-xs text-slate-300">{tension.pole_b.stance}</div>
                    </div>
                  </div>
                </article>
              ))}
            </section>
          ) : null}

          {hasOutcomes ? (
            <section className="space-y-4 pt-2">
              <h3 className="pl-2 text-xs font-bold uppercase tracking-widest text-slate-400">对谈沉淀</h3>

              {data.consensus_areas.length > 0 ? (
                <div className="rounded-[20px] border border-emerald-500/20 bg-emerald-900/20 p-4">
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-emerald-400">
                    <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5" /> 共识
                  </div>
                  <div className="space-y-2">
                    {data.consensus_areas.map((item) => (
                      <p
                        key={item.area_id}
                        className="rounded-xl bg-emerald-500/10 p-2.5 text-xs leading-relaxed text-emerald-100/90"
                      >
                        {item.description}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}

              {data.productive_tensions.length > 0 ? (
                <div className="rounded-[20px] border border-sky-500/20 bg-sky-900/20 p-4">
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-sky-400">
                    <Sparkles aria-hidden="true" className="h-3.5 w-3.5" /> 有益张力
                  </div>
                  <div className="space-y-2">
                    {data.productive_tensions.map((item, index) => (
                      <p
                        key={index}
                        className="rounded-xl bg-sky-500/10 p-2.5 text-xs leading-relaxed text-sky-100/90"
                      >
                        {item.description}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}

              {data.irreducible_differences.length > 0 ? (
                <div className="rounded-[20px] border border-amber-500/20 bg-amber-900/20 p-4">
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-amber-400">
                    <AlertCircle aria-hidden="true" className="h-3.5 w-3.5" /> 保留的分歧
                  </div>
                  <div className="space-y-2">
                    {data.irreducible_differences.map((item, index) => (
                      <p
                        key={index}
                        className="rounded-xl bg-amber-500/10 p-2.5 text-xs leading-relaxed text-amber-100/90"
                      >
                        {item.description}
                      </p>
                    ))}
                  </div>
                </div>
              ) : null}
            </section>
          ) : null}
        </div>

        <div className="shrink-0 border-t border-white/10 p-5">
          <button
            type="button"
            onClick={onFinish}
            className="flex w-full items-center justify-between rounded-2xl border border-indigo-300/20 bg-indigo-500/20 px-4 py-3.5 text-sm font-semibold text-indigo-50 transition-colors hover:bg-indigo-500/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
          >
            <span>完成并进入收束</span>
            <ArrowRight aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
      </aside>
    </>
  );
}
