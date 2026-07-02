import type { RoundProgression } from '../../api/client';

interface Props {
  rounds: RoundProgression[];
}

/** Map agent name fragments to Tailwind fill colors. */
const AGENT_COLORS: Record<string, string> = {
  '共情': '#fda4af',     // rose-300
  'Empathic': '#fda4af',
  '理性': '#7dd3fc',     // sky-300
  'Rational': '#7dd3fc',
  '批判': '#fcd34d',     // amber-300
  'Critical': '#fcd34d',
  '创意': '#c4b5fd',     // violet-300
  'Creative': '#c4b5fd',
  '综合': '#5eead4',     // teal-300
  'Synthesizer': '#5eead4',
};

function agentColor(name: string): string {
  for (const [key, color] of Object.entries(AGENT_COLORS)) {
    if (name.includes(key)) return color;
  }
  return '#cbd5e1'; // slate-300
}

export default function DebateTimeline({ rounds }: Props) {
  if (!rounds.length) return null;

  const svgWidth = 400;
  const svgHeight = 70;
  const padding = 30;
  const lineY = 25;
  const dotRadius = 6;
  const agentDotRadius = 3;
  const step = rounds.length > 1 ? (svgWidth - 2 * padding) / (rounds.length - 1) : 0;

  return (
    <section className="mb-6">
      <h3 className="text-sm font-medium text-slate-500 mb-2">辩论进程</h3>
      <svg
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="w-full"
        role="img"
        aria-label="辩论轮次时间线"
      >
        {/* Connecting line */}
        <line
          x1={padding} y1={lineY}
          x2={svgWidth - padding} y2={lineY}
          stroke="#e2e8f0" strokeWidth={2}
        />

        {rounds.map((r, i) => {
          const cx = rounds.length === 1 ? svgWidth / 2 : padding + i * step;

          return (
            <g key={r.round}>
              {/* Round circle */}
              <circle cx={cx} cy={lineY} r={dotRadius} fill="#3b82f6" />
              <text
                x={cx} y={lineY + 1}
                textAnchor="middle" dominantBaseline="middle"
                fill="white" fontSize={7} fontWeight="bold"
              >
                {r.round}
              </text>

              {/* Agent dots below */}
              {r.agents.map((agent, j) => {
                const agentX = cx - ((r.agents.length - 1) * 7) / 2 + j * 7;
                return (
                  <circle
                    key={agent}
                    cx={agentX}
                    cy={lineY + 16}
                    r={agentDotRadius}
                    fill={agentColor(agent)}
                  />
                );
              })}

              {/* Statement count */}
              <text
                x={cx} y={lineY + 30}
                textAnchor="middle" fill="#94a3b8" fontSize={6}
              >
                {r.statement_count}条
              </text>
            </g>
          );
        })}

        {/* Arrow at end */}
        <polygon
          points={`${svgWidth - padding + 5},${lineY} ${svgWidth - padding - 2},${lineY - 4} ${svgWidth - padding - 2},${lineY + 4}`}
          fill="#e2e8f0"
        />
      </svg>
    </section>
  );
}
