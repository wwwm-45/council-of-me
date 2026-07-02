import { useState } from 'react';
import { groupTensionsByHorizon } from './horizon';
import { type ArtifactEvent, type ConvergenceMapData, type RoundSummary, getVisual } from './types';

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

// ─── Artifact Card ──────────────────────────────────────────────────────────

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

type ArtifactCardBaseProps = {
  round: number;
  artifact: ArtifactEvent;
  convergenceMap: ConvergenceMapData | null;
};

type ArtifactCardProps = ArtifactCardBaseProps & (
  | { expanded: boolean; onToggle: () => void }
  | { expanded?: undefined; onToggle?: undefined }
);

function ArtifactCard(props: ArtifactCardProps) {
  const { round, artifact, convergenceMap } = props;
  const controlledExpanded = props.expanded;
  const onToggle = props.onToggle;
  const summary = artifact.data.summary;
  // R4 convergence map and summary-bearing artifacts are expanded by default.
  const [internalExpanded, setInternalExpanded] = useState(round === 4 || Boolean(summary));
  const expanded = controlledExpanded ?? internalExpanded;
  const handleToggle = onToggle ?? (() => setInternalExpanded((value) => !value));

  const borderColor = ROUND_BORDER_COLOR[round] ?? 'border-l-slate-300';
  const label = ROUND_LABEL[round] ?? `R${round} 摘要`;

  return (
    <div
      className={`my-3 mx-2 border border-slate-200 ${borderColor} border-l-[3px] rounded-lg bg-white shadow-sm overflow-hidden`}
    >
      {/* Collapsed header */}
      <button
        onClick={handleToggle}
        aria-expanded={expanded}
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
export function ConvergenceMapSystemCard({ data }: { data: ConvergenceMapData }) {
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

export default ArtifactCard;
