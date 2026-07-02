import type { SynthesisResponse } from '../../api/client';
import FeelingSelector from './FeelingSelector';
import { AGENT_COLORS, AGENT_NAMES, SHIFT_ICONS } from './types';
import type { FeelingId, NodeType, ReflectionExplorationView } from './types';

interface Props {
  nodeId: string;
  nodeType: NodeType;
  data: SynthesisResponse;
  onClose?: () => void;
  reflectionMode?: boolean;
  reflectionExploration?: ReflectionExplorationView | null;
  onFeelingsChange?: (feelings: FeelingId[]) => void;
  onEnterDialogue?: () => void;
}

function collectEchoCards(
  nodeId: string,
  nodeType: NodeType,
  data: SynthesisResponse,
  reflectionExploration: ReflectionExplorationView | null | undefined,
) {
  const synthesisCards: Array<{ title: string; body: string }> = [];

  if (nodeType === 'tension') {
    const tension = data.core_tensions.find((item) => item.tension_id === nodeId);
    for (const evidence of [
      ...(tension?.pole_a.evidence_statements ?? []),
      ...(tension?.pole_b.evidence_statements ?? []),
    ].slice(0, 4)) {
      synthesisCards.push({
        title: `${evidence.agent_name} · R${evidence.round_number}`,
        body: evidence.content,
      });
    }
  }

  if (nodeType === 'voice') {
    const voice = data.voice_positions.find((item) => item.agent_id === nodeId);
    const evolution = data.agent_evolutions?.find((item) => item.agent_id === nodeId);
    const protector = data.protective_intents?.find((item) => item.agent_id === nodeId);
    const concessions = data.concessions?.filter((item) => item.agent_id === nodeId) ?? [];

    if (voice?.core_stance) {
      synthesisCards.push({
        title: `${voice.agent_name} · 核心立场`,
        body: voice.core_stance,
      });
    }
    if (evolution) {
      synthesisCards.push({
        title: '立场演化',
        body: `R1：${evolution.r1_position}\n现在：${evolution.current_position}`,
      });
    }
    if (protector?.intent) {
      synthesisCards.push({
        title: '保护意图',
        body: protector.intent,
      });
    }
    for (const concession of concessions.slice(0, 2)) {
      synthesisCards.push({
        title: '让步',
        body: concession.concession,
      });
    }
  }

  if (nodeType === 'consensus' && nodeId.startsWith('consensus-')) {
    const index = Number.parseInt(nodeId.split('-')[1] ?? '', 10);
    const area = data.consensus_areas?.[index];
    for (const evidence of area?.evidence?.slice(0, 3) ?? []) {
      synthesisCards.push({
        title: '共识证据',
        body: evidence,
      });
    }
  }

  if (nodeType === 'productive' && nodeId.startsWith('productive-')) {
    const index = Number.parseInt(nodeId.split('-')[1] ?? '', 10);
    const productive = data.productive_tensions?.[index];
    if (productive?.description) {
      synthesisCards.push({
        title: '生产性张力',
        body: productive.description,
      });
    }
    if (productive?.understanding) {
      synthesisCards.push({
        title: '互相理解',
        body: productive.understanding,
      });
    }
  }

  if (nodeType === 'irreducible' && nodeId.startsWith('irreducible-')) {
    const index = Number.parseInt(nodeId.split('-')[1] ?? '', 10);
    const difference = data.irreducible_differences?.[index];
    if (difference?.description) {
      synthesisCards.push({
        title: '分歧描述',
        body: difference.description,
      });
    }
    if (difference?.why_irreducible) {
      synthesisCards.push({
        title: '为何难以调和',
        body: difference.why_irreducible,
      });
    }
  }

  if (nodeType === 'center') {
    if (data.key_insight) {
      synthesisCards.push({
        title: '核心洞察',
        body: data.key_insight,
      });
    }
    for (const moment of data.highlight_moments?.slice(0, 2) ?? []) {
      synthesisCards.push({
        title: '关键时刻',
        body: moment,
      });
    }
  }

  if (!reflectionExploration) {
    return synthesisCards;
  }
  const deduped = new Map<string, { title: string; body: string }>();

  for (const card of synthesisCards) {
    const key = `${card.title}-${card.body}`;
    if (!deduped.has(key)) {
      deduped.set(key, card);
    }
  }

  for (const turn of reflectionExploration.dialogue ?? []) {
    for (const card of turn.echo_cards ?? []) {
      const key = card.card_id || `${card.title}-${card.body}`;
      if (!key || deduped.has(key)) continue;
      deduped.set(key, { title: card.title, body: card.body });
    }
  }

  return Array.from(deduped.values());
}

export default function NodeDetailPanel({
  nodeId,
  nodeType,
  data,
  onClose,
  reflectionMode = false,
  reflectionExploration = null,
  onFeelingsChange,
  onEnterDialogue,
}: Props) {
  const echoCards = collectEchoCards(nodeId, nodeType, data, reflectionExploration);
  const selectedFeelings = reflectionExploration?.feelings ?? [];

  return (
    <div
      className="h-full min-h-0 animate-fade-in bg-transparent p-7 text-slate-100"
    >
      {onClose ? (
        <button
          type="button"
          onClick={onClose}
          className="float-right text-lg leading-none text-slate-400 transition hover:text-white"
        >
          &times;
        </button>
      ) : null}
      {nodeType === 'center' && <CenterDetail data={data} />}
      {nodeType === 'tension' && <TensionDetail tensionId={nodeId} data={data} />}
      {nodeType === 'voice' && <VoiceDetail agentId={nodeId} data={data} />}
      {(nodeType === 'consensus' || nodeType === 'irreducible' || nodeType === 'productive') && (
        <OuterDetail nodeId={nodeId} data={data} />
      )}
      {reflectionMode ? (
        <section className="mt-6 space-y-5 border-t border-white/10 pt-5">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.24em] text-slate-400">辩论回声</p>
            {echoCards.length > 0 ? (
              <div className="mt-3 space-y-2">
                {echoCards.map((echo) => (
                  <article key={`${echo.title}-${echo.body}`} className="rounded-2xl border border-white/5 bg-white/5 px-3 py-3">
                    {echo.title ? <p className="text-xs font-medium text-slate-400">{echo.title}</p> : null}
                    <p className="mt-1 text-sm leading-relaxed text-slate-200">{echo.body}</p>
                  </article>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-400">先从你对这个节点的感受开始，后续相关辩论片段会在这里浮现。</p>
            )}
          </div>

          {onFeelingsChange ? (
            <FeelingSelector selected={selectedFeelings} onChange={onFeelingsChange} />
          ) : null}

          {selectedFeelings.length > 0 && onEnterDialogue ? (
            <div className="rounded-2xl border border-indigo-400/20 bg-indigo-500/10 px-4 py-4">
              <p className="text-sm text-indigo-100">你已经捕捉到感觉了。要继续留在这个节点里，再往里问一层吗？</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {onEnterDialogue ? (
                  <button
                    type="button"
                    onClick={onEnterDialogue}
                    className="rounded-xl bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-700"
                  >
                    继续深挖
                  </button>
                ) : null}
                {onClose ? (
                  <button
                    type="button"
                    onClick={onClose}
                    className="rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-300 transition hover:bg-white/10 hover:text-white"
                  >
                    回到图景
                  </button>
                ) : null}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function CenterDetail({ data }: { data: SynthesisResponse }) {
  return (
    <div>
      <h3 className="mb-3 text-xl font-bold text-white">{data.dilemma_text || '你的议题'}</h3>
      {data.key_insight && (
        <div className="mb-3 rounded-2xl border border-white/5 bg-white/5 p-4">
          <p className="mb-1 text-xs text-indigo-300">核心洞察</p>
          <p className="text-sm leading-relaxed text-slate-200">{data.key_insight}</p>
        </div>
      )}
      <p className="whitespace-pre-line text-sm leading-relaxed text-slate-300">{data.narrative}</p>
    </div>
  );
}

function TensionDetail({ tensionId, data }: { tensionId: string; data: SynthesisResponse }) {
  const tension = data.core_tensions.find(t => t.tension_id === tensionId);
  if (!tension) return <p className="text-sm text-slate-400">未找到该张力</p>;
  return (
    <div>
      <h3 className="mb-1 text-xl font-bold text-white">{tension.name}</h3>
      <div className="flex items-center gap-2 mb-4">
        <span className="text-xs text-slate-500">强度</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/10">
          <div className="h-full rounded-full bg-amber-400" style={{ width: `${tension.intensity * 100}%` }} />
        </div>
        <span className="text-xs text-slate-500">{(tension.intensity * 100).toFixed(0)}%</span>
      </div>
      <div className="mb-3 rounded-2xl border border-amber-400/15 bg-amber-400/10 p-3">
        <p className="mb-1 text-xs font-semibold text-amber-300">{tension.pole_a.label}</p>
        <p className="mb-1 text-sm text-slate-200">{tension.pole_a.stance}</p>
        <div className="flex gap-1 flex-wrap">
          {tension.pole_a.agents.map(a => (
            <span key={a} className="text-[10px] px-1.5 py-0.5 rounded-full text-white"
              style={{ backgroundColor: AGENT_COLORS[a] || '#94a3b8' }}>{AGENT_NAMES[a] || a}</span>
          ))}
        </div>
      </div>
      <div className="mb-3 rounded-2xl border border-sky-400/15 bg-sky-400/10 p-3">
        <p className="mb-1 text-xs font-semibold text-sky-300">{tension.pole_b.label}</p>
        <p className="mb-1 text-sm text-slate-200">{tension.pole_b.stance}</p>
        <div className="flex gap-1 flex-wrap">
          {tension.pole_b.agents.map(a => (
            <span key={a} className="text-[10px] px-1.5 py-0.5 rounded-full text-white"
              style={{ backgroundColor: AGENT_COLORS[a] || '#94a3b8' }}>{AGENT_NAMES[a] || a}</span>
          ))}
        </div>
      </div>
      {[...(tension.pole_a.evidence_statements || []), ...(tension.pole_b.evidence_statements || [])].length > 0 && (
        <div className="mt-3">
          <p className="mb-2 text-xs text-slate-400">辩论原文</p>
          {[...(tension.pole_a.evidence_statements || []), ...(tension.pole_b.evidence_statements || [])].slice(0, 4).map((ev, i) => (
            <blockquote key={i} className="mb-2 border-l-2 border-white/15 pl-3 text-xs text-slate-300 italic">
              "{ev.content.length > 80 ? ev.content.slice(0, 80) + '…' : ev.content}"
              <span className="mt-0.5 block text-[10px] text-slate-500 not-italic">— {ev.agent_name} (R{ev.round_number})</span>
            </blockquote>
          ))}
        </div>
      )}
    </div>
  );
}

function VoiceDetail({ agentId, data }: { agentId: string; data: SynthesisResponse }) {
  const evo = data.agent_evolutions?.find(e => e.agent_id === agentId);
  const intent = data.protective_intents?.find(p => p.agent_id === agentId);
  const concessions = data.concessions?.filter(c => c.agent_id === agentId) || [];
  const name = AGENT_NAMES[agentId] || agentId;
  const color = AGENT_COLORS[agentId] || '#94a3b8';
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <div className="w-8 h-8 rounded-full" style={{ backgroundColor: color }} />
        <h3 className="text-base font-bold text-white">{name}</h3>
      </div>
      {evo && (
        <div className="mb-4 rounded-2xl border border-white/5 bg-white/5 p-3">
          <p className="mb-2 text-xs text-slate-400">立场演化</p>
          <div className="flex items-center gap-2">
            <div className="flex-1">
              <p className="text-xs text-slate-400">R1</p>
              <p className="text-sm text-slate-200">{evo.r1_position}</p>
            </div>
            <span className="text-lg" style={{ color }}>{SHIFT_ICONS[evo.shift_type] || '→'}</span>
            <div className="flex-1">
              <p className="text-xs text-slate-400">最终</p>
              <p className="text-sm text-slate-200">{evo.current_position}</p>
            </div>
          </div>
          {evo.shift_trigger && (
            <p className="text-xs text-slate-500 mt-2"><span className="font-medium">触发松动：</span>{evo.shift_trigger}</p>
          )}
          <p className="text-xs text-slate-500 mt-1"><span className="font-medium">情绪状态：</span>{evo.emotional_state}</p>
        </div>
      )}
      {intent && (
        <div className="mb-4 p-3 rounded-xl" style={{ backgroundColor: color + '15' }}>
          <p className="text-xs text-slate-500 mb-1">保护意图</p>
          <p className="mb-1 text-sm text-slate-200">{intent.intent}</p>
          <p className="text-xs text-slate-500 italic">守护：{intent.what_it_protects}</p>
        </div>
      )}
      {concessions.length > 0 && (
        <div className="mb-3">
          <p className="text-xs text-slate-500 mb-1">让步</p>
          {concessions.map((c, i) => (
            <blockquote key={i} className="mb-2 border-l-2 border-white/15 pl-3 text-xs text-slate-300 italic">
              {c.concession}
            </blockquote>
          ))}
        </div>
      )}
    </div>
  );
}

function OuterDetail({ nodeId, data }: { nodeId: string; data: SynthesisResponse }) {
  if (nodeId.startsWith('consensus-')) {
    const idx = parseInt(nodeId.split('-')[1], 10);
    const area = data.consensus_areas?.[idx];
    if (!area) return null;
    return (
      <div>
        <h3 className="mb-2 text-base font-bold text-emerald-300">共识</h3>
        <p className="mb-2 text-sm text-slate-200">{area.description}</p>
        <div className="flex gap-1 flex-wrap mb-2">
          {area.supporting_agents.map(a => (
            <span key={a} className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-100 text-emerald-700">
              {AGENT_NAMES[a] || a}
            </span>
          ))}
        </div>
        {area.evidence?.length > 0 && (
          <div>
            {area.evidence.slice(0, 3).map((e, i) => (
              <blockquote key={i} className="mb-1 border-l-2 border-emerald-400/30 pl-3 text-xs text-slate-300 italic">{e}</blockquote>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (nodeId.startsWith('irreducible-')) {
    const idx = parseInt(nodeId.split('-')[1], 10);
    const diff = data.irreducible_differences?.[idx];
    if (!diff) return null;
    return (
      <div>
        <h3 className="mb-2 text-base font-bold text-amber-300">不可调和分歧</h3>
        <p className="mb-2 text-sm text-slate-200">{diff.description}</p>
        <p className="text-xs text-slate-400 italic">原因：{diff.why_irreducible}</p>
      </div>
    );
  }
  if (nodeId.startsWith('productive-')) {
    const idx = parseInt(nodeId.split('-')[1], 10);
    const pt = data.productive_tensions?.[idx];
    if (!pt) return null;
    return (
      <div>
        <h3 className="mb-2 text-base font-bold text-emerald-300">生产性张力</h3>
        <p className="mb-2 text-sm text-slate-200">{pt.description}</p>
        <p className="text-xs text-slate-400 italic">互相理解：{pt.understanding}</p>
      </div>
    );
  }
  return null;
}
