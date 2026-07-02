import { useId, useState, type FocusEvent, type KeyboardEvent, type ReactNode } from 'react';
import type { LandscapeDisplayModel, NodeType } from './types';

export interface LandscapeNodeState {
  selected: boolean;
  viewed: boolean;
  explored: boolean;
  traceIndex: number | null;
}

const EMPTY_STATE: LandscapeNodeState = {
  selected: false,
  viewed: false,
  explored: false,
  traceIndex: null,
};

const OUTCOME_COLORS = {
  consensus: '#34d399',
  productive: '#60a5fa',
  irreducible: '#f59e0b',
} as const;

const OUTCOME_LABELS = {
  consensus: '共识',
  productive: '有益张力',
  irreducible: '核心分歧',
} as const;

function InteractiveNode({
  id,
  type,
  ariaLabel,
  x,
  y,
  radius,
  focusColor,
  state = EMPTY_STATE,
  delay,
  onActivate,
  children,
}: {
  id: string;
  type: NodeType;
  ariaLabel: string;
  x: number;
  y: number;
  radius: number;
  focusColor: string;
  state?: LandscapeNodeState;
  delay: number;
  onActivate?: () => void;
  children: ReactNode;
}) {
  const statusId = `landscape-node-status-${useId().replace(/:/g, '')}`;
  const [focused, setFocused] = useState(false);
  const status = [
    state.selected ? '已选中' : '未选中',
    state.viewed ? '已查看' : '未查看',
    state.explored ? '已探索' : '未探索',
    state.traceIndex ? `探索轨迹第 ${state.traceIndex} 个` : null,
  ].filter(Boolean).join('，');

  function handleKeyDown(event: KeyboardEvent<SVGGElement>) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    onActivate?.();
  }

  function handleFocus(event: FocusEvent<SVGGElement>) {
    if (event.currentTarget === event.target) setFocused(true);
  }

  function handleBlur(event: FocusEvent<SVGGElement>) {
    if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
  }

  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={ariaLabel}
      aria-pressed={state.selected}
      aria-describedby={statusId}
      data-node-id={id}
      data-node-type={type}
      data-x={x}
      data-y={y}
      data-selected={state.selected}
      data-viewed={state.viewed}
      data-explored={state.explored}
      data-trace-index={state.traceIndex ?? undefined}
      data-focus-visible={focused}
      className="cursor-pointer animate-bloom"
      style={{ animation: `bloom 0.5s ease-out ${delay}s both` }}
      onClick={onActivate}
      onKeyDown={handleKeyDown}
      onFocus={handleFocus}
      onBlur={handleBlur}
    >
      {children}
      {focused ? (
        <circle
          data-focus-ring="true"
          cx={x}
          cy={y}
          r={radius + 12}
          fill="none"
          stroke={focusColor}
          strokeWidth={3}
          strokeDasharray="6 4"
          pointerEvents="none"
        />
      ) : null}
      <desc id={statusId}>{status}</desc>
    </g>
  );
}

function StateIndicators({
  x,
  y,
  radius,
  color,
  state = EMPTY_STATE,
}: {
  x: number;
  y: number;
  radius: number;
  color: string;
  state?: LandscapeNodeState;
}) {
  const active = state.selected || state.viewed || state.explored;
  return (
    active ? (
      <circle
        cx={x}
        cy={y}
        r={radius + (state.selected ? 9 : 5)}
        fill={state.explored ? `${color}24` : 'transparent'}
        stroke={state.selected ? color : `${color}88`}
        strokeWidth={state.selected ? 2 : 1}
        strokeDasharray={state.selected ? '4 6' : undefined}
        className={state.selected ? 'animate-[drift_10s_linear_infinite]' : undefined}
        style={{ transformOrigin: `${x}px ${y}px` }}
        pointerEvents="none"
      />
    ) : null
  );
}

function TraceBadge({ x, y, radius, traceIndex }: { x: number; y: number; radius: number; traceIndex: number | null }) {
  return traceIndex ? (
    <text
      x={x + radius * 0.82}
      y={y - radius * 0.82}
      textAnchor="middle"
      dominantBaseline="middle"
      fill="#e0e7ff"
      fontSize={13}
      fontWeight={800}
      aria-label={`探索轨迹 ${traceIndex}`}
      pointerEvents="none"
    >
      {traceIndex}
    </text>
  ) : null;
}

export function CenterNode({
  x,
  y,
  dilemma,
  insight,
  delay,
  onClick,
  state = EMPTY_STATE,
}: {
  x: number;
  y: number;
  dilemma: string;
  insight: string;
  delay: number;
  onClick?: () => void;
  state?: LandscapeNodeState;
}) {
  return (
    <InteractiveNode
      id="center"
      type="center"
      ariaLabel={`核心议题：${dilemma}`}
      x={x}
      y={y}
      radius={100}
      focusColor="#ffffff"
      state={state}
      delay={delay}
      onActivate={onClick}
    >
      <g
        className={state.selected ? 'scale-105 drop-shadow-[0_0_25px_rgba(99,102,241,0.8)]' : 'scale-100'}
        style={{ transformOrigin: `${x}px ${y}px` }}
      >
        <circle cx={x} cy={y} r={140} fill="rgba(99,102,241,0.2)" filter="blur(30px)" className="animate-pulse-slow" pointerEvents="none" />
        <StateIndicators x={x} y={y} radius={100} color="#818cf8" state={state} />
        <circle cx={x} cy={y} r={100} fill="rgba(30,27,75,0.65)" stroke="rgba(99,102,241,0.6)" strokeWidth={1.5} />
        <circle cx={x} cy={y} r={90} fill="rgba(99,102,241,0.15)" filter="blur(15px)" pointerEvents="none" />
        <foreignObject x={x - 82} y={y - 82} width={164} height={164} pointerEvents="none">
          <div className="flex h-full w-full flex-col items-center justify-center px-3 text-center">
            <div className="mb-2 text-[12px] font-bold uppercase tracking-[0.2em] text-indigo-300">核心探索</div>
            <span className="line-clamp-3 overflow-hidden text-[16px] font-extrabold leading-snug text-white">{dilemma}</span>
            {insight ? <span className="mt-2 line-clamp-2 overflow-hidden text-[10px] leading-snug text-slate-300">{insight}</span> : null}
          </div>
        </foreignObject>
        <TraceBadge x={x} y={y} radius={100} traceIndex={state.traceIndex} />
      </g>
    </InteractiveNode>
  );
}

export function TensionNode({
  x,
  y,
  tension,
  delay,
  onClick,
  state = EMPTY_STATE,
}: {
  x: number;
  y: number;
  tension: LandscapeDisplayModel['tensions'][number];
  delay: number;
  onClick?: () => void;
  state?: LandscapeNodeState;
}) {
  const radius = 65;
  return (
    <InteractiveNode
      id={tension.id}
      type="tension"
      ariaLabel={`张力：${tension.label}`}
      x={x}
      y={y}
      radius={radius}
      focusColor="#ffffff"
      state={state}
      delay={delay}
      onActivate={onClick}
    >
      <g className={state.selected ? 'scale-110' : 'animate-float scale-100'} style={{ transformOrigin: `${x}px ${y}px` }}>
        <circle cx={x} cy={y} r={radius + 20} fill="rgba(139,92,246,0.15)" filter="blur(20px)" className="animate-pulse-slow" pointerEvents="none" />
        <StateIndicators x={x} y={y} radius={radius} color="#a5b4fc" state={state} />
        <circle cx={x} cy={y} r={radius} fill="rgba(30,27,75,0.55)" stroke="rgba(165,180,252,0.55)" strokeWidth={1.5} />
        <circle cx={x} cy={y} r={radius - 10} fill="rgba(165,180,252,0.15)" filter="blur(10px)" pointerEvents="none" />
        <foreignObject x={x - 60} y={y - 60} width={120} height={120} pointerEvents="none">
          <div className="flex h-full w-full flex-col items-center justify-center gap-1 px-2 text-center">
            <div className="text-[10px] font-bold uppercase tracking-widest text-indigo-300/80">张力焦点</div>
            <span className="line-clamp-2 overflow-hidden text-[14px] font-extrabold leading-snug text-white">{tension.label}</span>
          </div>
        </foreignObject>
        <TraceBadge x={x} y={y} radius={radius} traceIndex={state.traceIndex} />
      </g>
    </InteractiveNode>
  );
}

export function VoiceNode({
  x,
  y,
  voice,
  delay,
  onClick,
  showLabel = true,
  state = EMPTY_STATE,
}: {
  x: number;
  y: number;
  voice: LandscapeDisplayModel['voices'][number];
  delay: number;
  onClick?: () => void;
  showLabel?: boolean;
  state?: LandscapeNodeState;
}) {
  const radius = 50;
  return (
    <InteractiveNode
      id={voice.id}
      type="voice"
      ariaLabel={`声音：${voice.label}`}
      x={x}
      y={y}
      radius={radius}
      focusColor={voice.color}
      state={state}
      delay={delay}
      onActivate={onClick}
    >
      <g
        className={state.selected ? 'scale-110' : 'animate-float scale-100'}
        style={{ transformOrigin: `${x}px ${y}px`, filter: state.selected ? `drop-shadow(0 0 20px ${voice.color})` : undefined }}
      >
        <circle cx={x} cy={y} r={radius + 15} fill={voice.color} opacity={0.1} filter="blur(15px)" className="animate-pulse-slow" pointerEvents="none" />
        <StateIndicators x={x} y={y} radius={radius} color={voice.color} state={state} />
        <circle cx={x} cy={y} r={radius} fill="rgba(15,23,42,0.45)" stroke={voice.color} strokeWidth={1.5} />
        <circle cx={x} cy={y} r={radius - 10} fill={voice.color} opacity={0.2} filter="blur(8px)" pointerEvents="none" />
        <foreignObject x={x - 45} y={y - 45} width={90} height={90} pointerEvents="none">
          <div className="flex h-full w-full items-center justify-center px-1 text-center">
            <span className="max-w-full overflow-hidden text-ellipsis whitespace-nowrap text-[14px] font-extrabold" style={{ color: voice.color }}>
              {showLabel ? voice.label : voice.label.slice(0, 2)}
            </span>
          </div>
        </foreignObject>
        {voice.shiftIcon ? (
          <foreignObject x={x + radius - 16} y={y - radius - 4} width={24} height={24} pointerEvents="none">
            <div className="flex h-full w-full items-center justify-center rounded-full border border-white/20 bg-white/10 text-[12px] text-white">{voice.shiftIcon}</div>
          </foreignObject>
        ) : null}
        <TraceBadge x={x} y={y} radius={radius} traceIndex={state.traceIndex} />
      </g>
    </InteractiveNode>
  );
}

export function OuterNode({
  x,
  y,
  type,
  label,
  delay,
  onClick,
  id = `${type}-${label}`,
  state = EMPTY_STATE,
}: {
  x: number;
  y: number;
  type: 'consensus' | 'irreducible' | 'productive';
  label: string;
  delay: number;
  onClick?: () => void;
  id?: string;
  state?: LandscapeNodeState;
}) {
  const color = OUTCOME_COLORS[type];
  const radius = 45;
  return (
    <InteractiveNode
      id={id}
      type={type}
      ariaLabel={`${OUTCOME_LABELS[type]}：${label}`}
      x={x}
      y={y}
      radius={radius}
      focusColor={color}
      state={state}
      delay={delay}
      onActivate={onClick}
    >
      <g
        className={state.selected ? 'scale-110' : 'animate-float scale-100'}
        style={{ transformOrigin: `${x}px ${y}px`, filter: state.selected ? `drop-shadow(0 0 20px ${color})` : undefined }}
      >
        <title>{label}</title>
        <circle cx={x} cy={y} r={radius + 15} fill={color} opacity={0.1} filter="blur(15px)" className="animate-pulse-slow" pointerEvents="none" />
        <StateIndicators x={x} y={y} radius={radius} color={color} state={state} />
        <circle
          cx={x}
          cy={y}
          r={radius}
          fill="rgba(15,23,42,0.5)"
          stroke={`${color}88`}
          strokeWidth={1.5}
          strokeDasharray={type === 'irreducible' ? '4 4' : undefined}
        />
        {type === 'productive' ? (
          <rect x={x - 7} y={y - 7} width={14} height={14} fill={color} opacity={0.45} transform={`rotate(45 ${x} ${y})`} pointerEvents="none" />
        ) : type === 'irreducible' ? (
          <polygon points={`${x},${y - 9} ${x - 8},${y + 7} ${x + 8},${y + 7}`} fill={color} opacity={0.45} pointerEvents="none" />
        ) : null}
        <circle cx={x} cy={y} r={radius - 8} fill={color} opacity={0.15} filter="blur(8px)" pointerEvents="none" />
        <foreignObject x={x - 40} y={y - 40} width={80} height={80} pointerEvents="none">
          <div className="flex h-full w-full items-center justify-center px-1 text-center">
            <span className="break-words text-[12px] font-extrabold uppercase tracking-widest" style={{ color }}>{OUTCOME_LABELS[type]}</span>
          </div>
        </foreignObject>
        <TraceBadge x={x} y={y} radius={radius} traceIndex={state.traceIndex} />
      </g>
    </InteractiveNode>
  );
}

export function LandscapeDefs() {
  return (
    <defs>
      <radialGradient id="reflectionAura" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="#6366f1" stopOpacity={0.15} />
        <stop offset="50%" stopColor="#4f46e5" stopOpacity={0.05} />
        <stop offset="100%" stopColor="transparent" stopOpacity={0} />
      </radialGradient>
      <radialGradient id="nodeGlow" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="#ffffff" stopOpacity={0.8} />
        <stop offset="100%" stopColor="#ffffff" stopOpacity={0} />
      </radialGradient>
      <filter id="softGlow" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation={8} result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
    </defs>
  );
}

export function MapLegend() {
  return (
    <div className="absolute bottom-6 flex gap-5 rounded-full border border-slate-700/50 bg-slate-900/60 px-5 py-2.5 text-xs font-medium text-slate-300 backdrop-blur-md">
      {(Object.keys(OUTCOME_LABELS) as Array<keyof typeof OUTCOME_LABELS>).map((type) => (
        <div key={type} className="flex items-center gap-2">
          <span
            className={`h-3.5 w-3.5 shrink-0 ${type === 'consensus' ? 'rounded-full' : type === 'productive' ? 'rotate-45' : ''}`}
            style={{
              backgroundColor: OUTCOME_COLORS[type],
              clipPath: type === 'irreducible' ? 'polygon(50% 0, 0 100%, 100% 100%)' : undefined,
            }}
          />
          {OUTCOME_LABELS[type]}
          {type === 'irreducible' ? <span hidden>不可调和</span> : null}
        </div>
      ))}
    </div>
  );
}
