import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { getSynthesis, streamSynthesis } from '../api/client';
import type { SynthesisResponse } from '../api/client';
import { getSession as getStore, setSession } from '../store/session';
import MetaScoreBar from '../components/synthesis/MetaScoreBar';
import TensionVisualization from '../components/synthesis/TensionVisualization';
import TensionMap from '../components/synthesis/TensionMap';
import ConsensusAreas from '../components/synthesis/ConsensusAreas';
import ProtectiveIntents from '../components/synthesis/ProtectiveIntents';
import DebateTimeline from '../components/synthesis/DebateTimeline';
import SynthesisLoadingUI from '../components/synthesis/SynthesisLoadingUI';

export default function SynthesisPage() {
  const nav = useNavigate();
  const sid = getStore().sessionId;
  const [data, setData] = useState<SynthesisResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeStages, setActiveStages] = useState<Set<string>>(new Set());
  const [completedStages, setCompletedStages] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!sid) { nav('/'); return; }
    const controller = new AbortController();

    streamSynthesis(sid, {
      onStageStart: (info) => {
        setActiveStages(prev => new Set(prev).add(info.stage));
      },
      onStageEnd: (stage) => {
        setActiveStages(prev => { const s = new Set(prev); s.delete(stage); return s; });
        setCompletedStages(prev => new Set(prev).add(stage));
      },
      onComplete: (result) => {
        setData(result);
        setLoading(false);
      },
      onCached: (result) => {
        setData(result);
        setLoading(false);
      },
      onError: () => {
        // Fallback to REST
        getSynthesis(sid).then(setData).catch(console.error).finally(() => setLoading(false));
      },
    }, controller.signal).catch(() => {
      // SSE fetch itself failed — fallback to REST
      if (!controller.signal.aborted) {
        getSynthesis(sid).then(setData).catch(console.error).finally(() => setLoading(false));
      }
    });

    return () => controller.abort();
  }, [nav, sid]);

  function goToReflection() {
    setSession({ status: 'reflecting' });
    nav('/reflection');
  }

  if (loading || !data) return <SynthesisLoadingUI activeStages={activeStages} completedStages={completedStages} />;

  const typeLabel = data.synthesis_type === 'CONSENSUS_MAP' ? '共识地图' : '多声音全景';

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 sm:py-8 animate-fade-in">
      {/* Header */}
      <div className="mb-5">
        <h2 className="text-xl font-bold text-slate-800 mb-1">综合图景</h2>
        <div className="flex items-center gap-2">
          <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
            {typeLabel}
          </span>
          {data.meta?.debate_rounds && (
            <span className="text-[11px] text-slate-400">
              {data.meta.debate_rounds} 轮辩论
            </span>
          )}
        </div>
      </div>

      {/* Meta score bars */}
      {data.meta && <MetaScoreBar meta={data.meta} />}

      {/* Narrative */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 mb-6">
        <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-line">{data.narrative}</p>
      </div>

      {/* Core tensions (cards) */}
      {data.core_tensions?.length > 0 && (
        <TensionVisualization tensions={data.core_tensions} />
      )}

      {/* Tension relationship map (SVG) */}
      {data.core_tensions?.length > 0 && (
        <TensionMap tensions={data.core_tensions} />
      )}

      {/* Consensus areas (only for CONSENSUS_MAP) */}
      {data.consensus_areas?.length > 0 && (
        <ConsensusAreas areas={data.consensus_areas} />
      )}

      {/* Protective intents (IFS) */}
      {data.protective_intents?.length > 0 && (
        <ProtectiveIntents intents={data.protective_intents} />
      )}

      {/* Voice positions (collapsed if protective intents already shown) */}
      {data.voice_positions.length > 0 && !data.protective_intents?.length && (
        <section className="mb-6">
          <h3 className="text-sm font-medium text-slate-500 mb-3">各声音立场</h3>
          <div className="space-y-3">
            {data.voice_positions.map((vp, i) => (
              <div key={i} className="bg-white rounded-xl border border-slate-100 p-4">
                <p className="font-semibold text-sm text-slate-800 mb-1">{vp.agent_name}</p>
                <p className="text-sm text-slate-600">{vp.core_stance}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Debate timeline */}
      {data.meta?.round_progression?.length ? (
        <DebateTimeline rounds={data.meta.round_progression} />
      ) : null}

      {/* Action */}
      <button onClick={goToReflection}
        className="w-full py-3 rounded-xl bg-blue-600 text-white font-medium hover:bg-blue-700 transition">
        进入深度反思
      </button>
    </div>
  );
}
