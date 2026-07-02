import { useEffect, useRef, useState } from 'react';
import {
  type ChatMsg,
  type TimelineItem,
  type ArtifactEvent,
  type ConvergenceMapData,
  type RoundSummary,
  getVisual,
  PHASE_LABEL,
} from './types';
import { buildReadableTurns } from './readability';
import { groupTensionsByHorizon } from './horizon';

// ─── Sub-components ──────────────────────────────────────────────────────────

function Avatar({ agentId }: { agentId: string }) {
  const v = getVisual(agentId);
  return (
    <div
      className={`w-6 h-6 text-sm rounded-full flex items-center justify-center flex-shrink-0 ${v.bg} ring-1 ring-white shadow-sm select-none`}
    >
      {v.emoji}
    </div>
  );
}

function RoundDivider({ round, phase, exchangeInfo }: { round: number; phase: string; exchangeInfo?: string }) {
  return (
    <div className="flex items-center gap-2 my-4 px-3">
      <div className="flex-1 h-px bg-slate-200" />
      <span className="text-[10px] font-medium text-slate-400 px-2 py-0.5 rounded-full border border-slate-200 bg-white whitespace-nowrap">
        {PHASE_LABEL[phase] ?? `第 ${round} 轮`}
        {exchangeInfo && <span className="ml-1 text-slate-300">{exchangeInfo}</span>}
      </span>
      <div className="flex-1 h-px bg-slate-200" />
    </div>
  );
}

function ClosingActDivider() {
  return (
    <div className="my-5 px-3" data-testid="closing-act-divider">
      <div className="rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 to-rose-50 px-4 py-3 text-center shadow-sm">
        <p className="text-[11px] font-semibold tracking-wide text-amber-700">终章</p>
        <p className="mt-0.5 text-sm font-medium text-slate-700">最后要说的话</p>
      </div>
    </div>
  );
}

function CouncilClosingCard({ agentIds }: { agentIds: string[] }) {
  return (
    <div
      className="my-4 mx-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-4 text-center shadow-sm animate-slide-up"
      data-testid="council-closing-card"
    >
      <div className="flex items-center justify-center -space-x-1.5">
        {agentIds.map((id) => (
          <div key={id} className="rounded-full ring-2 ring-white">
            <Avatar agentId={id} />
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs font-medium text-slate-600">议会落幕</p>
      <p className="mt-0.5 text-[10px] text-slate-400">{agentIds.length} 个声音都已留下最后的话</p>
    </div>
  );
}

function ReplyTag({ replyTo, messages }: { replyTo: string; messages: ChatMsg[] }) {
  const v = getVisual(replyTo);
  const name = messages.find((m) => m.agentId === replyTo)?.agentName ?? replyTo;
  return (
    <span className="text-[9px] text-slate-400 ml-8">
      ↩ {v.emoji} 回应 {name}
    </span>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="w-1 h-1 rounded-full bg-slate-400 animate-bounce"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </div>
  );
}

function RoundSummaryBanner({ summary }: { summary: RoundSummary }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 space-y-2 mt-3">
      <div>
        <p className="text-[10px] text-slate-500">本轮真正争点</p>
        <p className="text-sm text-slate-800">{summary.current_dispute}</p>
      </div>
      <div>
        <p className="text-[10px] text-slate-500">本轮推进</p>
        <p className="text-sm text-slate-800">{summary.key_change}</p>
      </div>
      <div>
        <p className="text-[10px] text-slate-500">仍未解决</p>
        <p className="text-sm text-slate-800">{summary.unresolved_issue}</p>
      </div>
    </div>
  );
}

// ─── Round border color mapping ─────────────────────────────────────────────

const ROUND_BORDER_COLOR: Record<number, string> = {
  1: 'border-l-gray-400',
  2: 'border-l-amber-400',
  3: 'border-l-rose-400',
  4: 'border-l-teal-400',
};

const ROUND_LABEL: Record<number, string> = {
  1: 'R1 立场图谱',
  2: 'R2 张力图谱',
  3: 'R3 参与记录',
  4: 'R4 共识图谱',
};

// ─── Horizon group dot colors (3-C) ──────────────────────────────────────────

const HORIZON_DOT: Record<string, string> = {
  immediate: 'bg-rose-400',
  medium: 'bg-amber-400',
  long: 'bg-sky-400',
  unscoped: 'bg-slate-300',
};

type TensionItem = {
  description?: string;
  name?: string;
  depth?: string;
  level?: string;
  horizon?: string;
  poles?: unknown[];
};

// ─── Artifact Card ──────────────────────────────────────────────────────────

function ArtifactCard({
  round,
  artifact,
  convergenceMap,
}: {
  round: number;
  artifact: ArtifactEvent;
  convergenceMap: ConvergenceMapData | null;
}) {
  const summary = artifact.data.summary;
  // R4 convergence map and summary-bearing artifacts are expanded by default.
  const [expanded, setExpanded] = useState(round === 4 || Boolean(summary));

  const borderColor = ROUND_BORDER_COLOR[round] ?? 'border-l-slate-300';
  const label = ROUND_LABEL[round] ?? `R${round} 摘要`;

  return (
    <div
      className={`my-3 mx-2 border border-slate-200 ${borderColor} border-l-[3px] rounded-lg bg-white shadow-sm overflow-hidden`}
    >
      {/* Collapsed header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-slate-50 transition-colors text-left"
      >
        <span className="text-[10px] font-semibold text-slate-500">
          {label}
        </span>
        <span className="text-[10px] text-slate-400 flex items-center gap-1">
          {!expanded && <ArtifactSummaryLine round={round} artifact={artifact} convergenceMap={convergenceMap} />}
          <svg
            className={`w-3 h-3 transition-transform ${expanded ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </span>
      </button>

      {/* Expanded content */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-slate-100">
          {summary && <RoundSummaryBanner summary={summary} />}
          {round === 1 && <PositionMapContent data={artifact.data} />}
          {round === 2 && <TensionMapContent data={artifact.data} />}
          {round === 3 && <EngagementRecordContent data={artifact.data} />}
          {round === 4 && convergenceMap && <ConvergenceMapContent data={convergenceMap} />}
          {round === 4 && !convergenceMap && <GenericArtifactContent data={artifact.data} />}
        </div>
      )}
    </div>
  );
}

/** One-line summary shown when the card is collapsed */
function ArtifactSummaryLine({
  round,
  artifact,
  convergenceMap,
}: {
  round: number;
  artifact: ArtifactEvent;
  convergenceMap: ConvergenceMapData | null;
}) {
  if (artifact.data.summary?.key_change) {
    return <span>{artifact.data.summary.key_change}</span>;
  }
  if (round === 1) {
    const positions = artifact.data.positions as Record<string, unknown>[] | undefined;
    const agentPositions = artifact.data.agent_positions as Record<string, unknown>[] | undefined;
    const count = positions?.length ?? agentPositions?.length ?? Object.keys(artifact.data).length;
    return <span>{count} 个立场</span>;
  }
  if (round === 2) {
    const tensions = artifact.data.tensions as unknown[] | undefined;
    return <span>{tensions?.length ?? '?'} 个张力</span>;
  }
  if (round === 3) {
    const shifts = artifact.data.position_shifts as unknown[] | undefined;
    return <span>{shifts?.length ?? '?'} 个变化</span>;
  }
  if (round === 4 && convergenceMap) {
    return <span>{convergenceMap.consensus.length} 共识, {convergenceMap.productive_tensions.length} 张力</span>;
  }
  return <span>查看详情</span>;
}

// ─── Artifact content renderers ─────────────────────────────────────────────

/** R1 Position Map: show each agent's stance */
function PositionMapContent({ data }: { data: Record<string, unknown> }) {
  // Support both { positions: [...] } and { agent_positions: [...] } shapes
  const positions = (data.positions ?? data.agent_positions ?? []) as Array<{
    agent_id?: string;
    agent_name?: string;
    stance?: string;
    position?: string;
    core_stance?: string;
  }>;

  if (positions.length === 0) {
    return data.summary ? null : <GenericArtifactContent data={data} />;
  }

  return (
    <ul className="mt-2 space-y-1.5">
      {positions.map((p, i) => {
        const visual = p.agent_id ? getVisual(p.agent_id) : null;
        return (
          <li key={i} className="flex items-start gap-1.5 text-[10px]">
            {visual && <span>{visual.emoji}</span>}
            <span className={`font-semibold ${visual?.text ?? 'text-slate-600'}`}>
              {p.agent_name ?? p.agent_id ?? `Agent ${i + 1}`}:
            </span>
            <span className="text-slate-600">{p.stance ?? p.position ?? p.core_stance ?? ''}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** R2 Tension Map: tensions grouped by decision horizon, with depth badges */
function TensionMapContent({ data }: { data: Record<string, unknown> }) {
  const tensions = (data.tensions ?? []) as TensionItem[];

  if (tensions.length === 0) {
    return data.summary ? null : <GenericArtifactContent data={data} />;
  }

  const depthBadge = (depth: string | undefined) => {
    const d = depth?.toLowerCase() ?? 'surface';
    const colors: Record<string, string> = {
      surface: 'bg-green-100 text-green-700',
      moderate: 'bg-amber-100 text-amber-700',
      deep: 'bg-rose-100 text-rose-700',
    };
    return colors[d] ?? 'bg-slate-100 text-slate-600';
  };

  const renderItem = (t: TensionItem, i: number) => {
    const depth = t.depth ?? t.level;
    return (
      <li key={i} className="text-[10px] text-slate-600">
        <span>{t.description ?? t.name ?? `Tension ${i + 1}`}</span>
        {depth && (
          <span className={`ml-1.5 px-1.5 py-0.5 rounded text-[8px] font-medium ${depthBadge(depth)}`}>
            {depth}
          </span>
        )}
      </li>
    );
  };

  const groups = groupTensionsByHorizon(tensions);

  // No meaningful layering (e.g. pre-3-C data) → keep the existing flat list.
  if (!groups) {
    return (
      <ol className="mt-2 space-y-1.5 list-decimal list-inside">
        {tensions.map(renderItem)}
      </ol>
    );
  }

  return (
    <div className="mt-2 space-y-2.5">
      {groups.map((group) => (
        <div key={group.key}>
          <p className="text-[9px] font-semibold text-slate-500 mb-1 flex items-center gap-1">
            <span className={`inline-block w-1.5 h-1.5 rounded-full ${HORIZON_DOT[group.key]}`} />
            {group.label}
            <span className="font-normal text-slate-400">({group.tensions.length})</span>
          </p>
          <ol className="space-y-1.5 list-decimal list-inside">
            {group.tensions.map(renderItem)}
          </ol>
        </div>
      ))}
    </div>
  );
}

/** R3 Engagement Record: show position shifts and key moments */
function EngagementRecordContent({ data }: { data: Record<string, unknown> }) {
  const pickText = (value: unknown, keys: string[]): string => {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object') return '';
    const record = value as Record<string, unknown>;
    for (const key of keys) {
      const candidate = record[key];
      if (typeof candidate === 'string' && candidate.trim()) {
        return candidate;
      }
    }
    return '';
  };

  const shifts = (data.position_shifts ?? []) as Array<{
    agent_id?: string;
    agent_name?: string;
    description?: string;
    shift?: string;
  }>;
  const moments = ((data.highlight_moments ?? data.key_moments ?? []) as unknown[])
    .map((moment) => pickText(moment, ['description', 'moment']))
    .filter(Boolean);
  const unresolved = ((data.unresolved_disagreements ?? []) as unknown[])
    .map((item) => pickText(item, ['description', 'disagreement', 'boundary']))
    .filter(Boolean);

  if (shifts.length === 0 && moments.length === 0 && unresolved.length === 0) {
    return data.summary ? null : <GenericArtifactContent data={data} />;
  }

  return (
    <div className="mt-2 space-y-2">
      {shifts.length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-slate-500 mb-1">立场变化</p>
          <ul className="space-y-1">
            {shifts.map((s, i) => {
              const visual = s.agent_id ? getVisual(s.agent_id) : null;
              return (
                <li key={i} className="text-[10px] text-slate-600 flex items-start gap-1">
                  {visual && <span>{visual.emoji}</span>}
                  <span>{s.agent_name ?? s.agent_id}: {s.description ?? s.shift ?? ''}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {moments.length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-slate-500 mb-1">关键时刻</p>
          <ul className="space-y-1">
            {moments.map((moment, i) => (
              <li key={i} className="text-[10px] text-slate-600">
                {moment}
              </li>
            ))}
          </ul>
        </div>
      )}
      {unresolved.length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-slate-500 mb-1">Unresolved Boundaries</p>
          <ul className="space-y-1">
            {unresolved.map((item, i) => (
              <li key={i} className="text-[10px] text-slate-600">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** R4 Convergence Map: consensus, tensions, differences, key insight */
function ConvergenceMapContent({ data }: { data: ConvergenceMapData }) {
  return (
    <div className="mt-2 space-y-3">
      {/* Consensus */}
      {data.consensus.length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-teal-600 mb-1">共识</p>
          <ul className="space-y-0.5">
            {data.consensus.map((c, i) => (
              <li key={i} className="text-[10px] text-slate-600 flex items-start gap-1">
                <span className="text-teal-400 mt-0.5">&#x2713;</span>
                <span>{c}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Productive tensions */}
      {data.productive_tensions.length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-amber-600 mb-1">建设性张力</p>
          <ul className="space-y-1">
            {data.productive_tensions.map((t, i) => (
              <li key={i} className="text-[10px] text-slate-600">
                <span className="font-medium">{t.description}</span>
                <span className="text-slate-400 block ml-3 text-[9px]">{t.understanding}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Irreducible differences */}
      {data.irreducible_differences.length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-rose-600 mb-1">不可消解的分歧</p>
          <ul className="space-y-1">
            {data.irreducible_differences.map((d, i) => (
              <li key={i} className="text-[10px] text-slate-600">
                <span className="font-medium">{d.description}</span>
                <span className="text-slate-400 block ml-3 text-[9px]">{d.why_irreducible}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Key insight */}
      {data.key_insight && (
        <div className="bg-teal-50 border border-teal-100 rounded-md px-2.5 py-2 mt-2">
          <p className="text-[9px] font-semibold text-teal-700 mb-0.5">核心洞察</p>
          <p className="text-[10px] text-teal-800 leading-relaxed">{data.key_insight}</p>
        </div>
      )}

      {/* Agent final positions */}
      {data.agent_final_positions && Object.keys(data.agent_final_positions).length > 0 && (
        <div>
          <p className="text-[9px] font-semibold text-slate-500 mb-1">最终立场</p>
          <ul className="space-y-0.5">
            {Object.entries(data.agent_final_positions).map(([agentId, position]) => {
              const visual = getVisual(agentId);
              return (
                <li key={agentId} className="text-[10px] text-slate-600 flex items-start gap-1">
                  <span>{visual.emoji}</span>
                  <span>{position}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Convergence map rendered as a system card in the transcript (for r4_mapping event) */
function ConvergenceMapSystemCard({ data }: { data: ConvergenceMapData }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="my-3 mx-2 border border-teal-200 border-l-[3px] border-l-teal-400 rounded-lg bg-teal-50/50 shadow-sm overflow-hidden animate-slide-up">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-teal-50 transition-colors text-left"
      >
        <span className="text-[10px] font-semibold text-teal-700">
          共识映射结果
        </span>
        <svg
          className={`w-3 h-3 text-teal-500 transition-transform ${expanded ? 'rotate-180' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {expanded && (
        <div className="px-3 pb-3 border-t border-teal-100">
          <ConvergenceMapContent data={data} />
        </div>
      )}
    </div>
  );
}

/** Fallback renderer for unstructured artifact data */
function GenericArtifactContent({ data }: { data: Record<string, unknown> }) {
  return (
    <pre className="mt-2 text-[9px] text-slate-500 whitespace-pre-wrap break-words overflow-hidden max-h-40 overflow-y-auto">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

// ─── Extended timeline type ─────────────────────────────────────────────────

type ExtendedTimelineItem =
  | TimelineItem
  | { kind: 'artifact'; round: number; artifact: ArtifactEvent }
  | { kind: 'convergence_map'; data: ConvergenceMapData }
  | { kind: 'closing_act_divider' }
  | { kind: 'council_closing'; agentIds: string[] };

const AUTO_SCROLL_BOTTOM_THRESHOLD = 48;

function isNearBottom(element: HTMLElement) {
  return (
    element.scrollHeight - element.scrollTop - element.clientHeight
    <= AUTO_SCROLL_BOTTOM_THRESHOLD
  );
}

// ─── Main component ──────────────────────────────────────────────────────────

interface DebateTranscriptProps {
  messages: ChatMsg[];
  roundMeta: Map<number, string>;
  expectedExchanges: Map<number, [number, number]>;
  streaming: boolean;
  paused: boolean;
  exchangeProgress: { seq: number; min: number; max: number } | null;
  roundArtifacts?: Map<number, ArtifactEvent>;
  convergenceMap?: ConvergenceMapData | null;
  showArtifacts?: boolean;
  pendingFollowupRound?: number | null;
}

export default function DebateTranscript({
  messages,
  roundMeta,
  expectedExchanges,
  streaming,
  paused,
  exchangeProgress,
  roundArtifacts,
  convergenceMap,
  showArtifacts = true,
  pendingFollowupRound = null,
}: DebateTranscriptProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const shouldStickToBottomRef = useRef(true);

  useEffect(() => {
    if (paused) return;
    const el = containerRef.current;
    if (el && shouldStickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, paused]);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    shouldStickToBottomRef.current = isNearBottom(el);
  }

  // Build timeline with round dividers and artifact cards
  const timeline: ExtendedTimelineItem[] = [];
  const seenRounds = new Set<number>();
  let lastRound = 0;
  let closingActOpened = false;
  for (const msg of messages) {
    // Before starting a new round, insert the artifact card for the previous round
    if (!seenRounds.has(msg.round)) {
      // Insert artifact for the previous round (if any) before new round divider
      if (showArtifacts && lastRound > 0 && roundArtifacts?.has(lastRound)) {
        const artifact = roundArtifacts.get(lastRound)!;
        // Don't duplicate convergence_map as artifact if it will be shown as system card
        if (!(lastRound === 4 && artifact.type === 'convergence_map')) {
          timeline.push({ kind: 'artifact', round: lastRound, artifact });
        }
      }

      seenRounds.add(msg.round);
      const expected = expectedExchanges.get(msg.round);
      const exchangeInfo = expected ? `${expected[0]}-${expected[1]}` : undefined;
      timeline.push({
        kind: 'divider',
        round: msg.round,
        phase: roundMeta.get(msg.round) ?? '',
        exchangeInfo,
      });
      lastRound = msg.round;
    }
    // The closing act splits off from the reflection block inside the same round 4.
    // The convergence map (分歧图) belongs just above the closing act so the arc
    // reads 反思 → 分歧图 → 终章 → 最后的话.
    if (msg.closingAct && !closingActOpened) {
      closingActOpened = true;
      if (showArtifacts && convergenceMap) {
        timeline.push({ kind: 'convergence_map', data: convergenceMap });
      }
      timeline.push({ kind: 'closing_act_divider' });
    }
    timeline.push({ kind: 'msg', msg });
  }

  // Insert artifact card for the last round (if round has ended and has no more messages coming)
  if (showArtifacts && lastRound > 0 && roundArtifacts?.has(lastRound) && !streaming) {
    const artifact = roundArtifacts.get(lastRound)!;
    if (!(lastRound === 4 && artifact.type === 'convergence_map')) {
      timeline.push({ kind: 'artifact', round: lastRound, artifact });
    }
  }

  // Insert convergence map as a system card when available. Before the closing
  // act exists (e.g. the R4 mapping phase) it sits at the end; once the closing
  // act has opened it is rendered just above the 终章 curtain instead (see loop).
  if (showArtifacts && convergenceMap && !closingActOpened) {
    timeline.push({ kind: 'convergence_map', data: convergenceMap });
  }

  // Cap the closing act with a council-closing card once all final words have landed.
  if (closingActOpened && !streaming) {
    const closingAgentIds = Array.from(
      new Set(messages.filter((m) => m.closingAct).map((m) => m.agentId)),
    );
    timeline.push({ kind: 'council_closing', agentIds: closingAgentIds });
  }

  const isWaiting = streaming && !messages.some((m) => m.streaming);

  return (
    <div ref={containerRef} onScroll={handleScroll} className="h-full overflow-y-auto px-3 py-2">
      {timeline.map((item, idx) => {
        if (item.kind === 'divider') {
          return (
            <RoundDivider
              key={`d-${item.round}`}
              round={item.round}
              phase={item.phase}
              exchangeInfo={item.exchangeInfo}
            />
          );
        }

        if (item.kind === 'closing_act_divider') {
          return <ClosingActDivider key="closing-act-divider" />;
        }

        if (item.kind === 'council_closing') {
          return <CouncilClosingCard key="council-closing" agentIds={item.agentIds} />;
        }

        if (item.kind === 'artifact') {
          return (
            <ArtifactCard
              key={`artifact-${item.round}`}
              round={item.round}
              artifact={item.artifact}
              convergenceMap={item.round === 4 ? convergenceMap ?? null : null}
            />
          );
        }

        if (item.kind === 'convergence_map') {
          return (
            <ConvergenceMapSystemCard
              key={`convergence-map-${idx}`}
              data={item.data}
            />
          );
        }

        // kind === 'msg'
        const msg = item.msg;
        if (msg.isUserTurn || msg.speakerType === 'user') {
          return (
            <div
              key={msg.id}
              data-testid="user-turn-bubble"
              className="mb-2 flex justify-end animate-slide-up"
            >
              <div className="max-w-[82%] rounded-2xl rounded-br-md border border-blue-100 bg-blue-50 px-3 py-2 text-right shadow-sm">
                <span className="text-[10px] font-semibold text-blue-700">
                  {msg.agentName || 'You'}
                </span>
                <p className="mt-0.5 text-xs leading-relaxed text-slate-800">
                  {msg.content}
                </p>
              </div>
            </div>
          );
        }

        const rawEmphasis = buildReadableTurns(
          [msg],
          roundArtifacts?.get(msg.round)?.data ?? {},
        )[0]?.emphasis ?? 'normal';
        // A round whose follow-up gate is still open isn't "finished" from the
        // user's view, so don't gray it out yet even if its artifact came back
        // low-trust — they're being asked to engage with that very round.
        const emphasis = rawEmphasis === 'muted' && msg.round === pendingFollowupRound
          ? 'normal'
          : rawEmphasis;
        const emphasisClass = emphasis === 'primary'
          ? 'rounded-xl bg-amber-50/70 ring-1 ring-amber-100 px-2 py-2'
          : emphasis === 'muted'
            ? 'opacity-55'
            : '';
        const bodyToneClass = emphasis === 'muted' ? 'text-slate-500' : 'text-slate-700';

        return (
          <div key={msg.id} className="animate-slide-up mb-2" data-emphasis={emphasis}>
            {msg.replyTo && (
              <ReplyTag replyTo={msg.replyTo} messages={messages} />
            )}
            <div className={`flex items-start gap-2 ${msg.isIntervention ? 'opacity-80' : ''} ${emphasisClass}`}>
              <Avatar agentId={msg.agentId} />
              <div className="min-w-0 flex-1">
                <span className={`text-[10px] font-semibold ${getVisual(msg.agentId).text}`}>
                  {msg.agentName}
                </span>
                {msg.interventionType && (
                  <span className="ml-1 text-[8px] px-1 py-0.5 rounded bg-slate-100 text-slate-400">
                    {msg.interventionType}
                  </span>
                )}
                <p className={`text-xs leading-relaxed mt-0.5 ${bodyToneClass} ${msg.isIntervention ? 'border-l-2 border-dashed border-slate-300 pl-2' : ''}`}>
                  {msg.content}
                  {msg.streaming && (
                    <span className="ml-0.5 inline-block w-[2px] h-[1em] bg-current align-[-0.15em] animate-pulse" />
                  )}
                </p>
              </div>
            </div>
          </div>
        );
      })}

      {isWaiting && (
        <div className="flex items-center gap-2 my-3 ml-2 animate-slide-up">
          <span className="text-[10px] text-slate-400">
            讨论中
            {exchangeProgress && (
              <span className="ml-1">
                {exchangeProgress.seq}/{exchangeProgress.min}-{exchangeProgress.max}
              </span>
            )}
          </span>
          <TypingDots />
        </div>
      )}

      <div className="h-1" />
    </div>
  );
}
