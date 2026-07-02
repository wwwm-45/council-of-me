import { useEffect, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'motion/react';

import { confirmPortrait, getPortrait, updatePortrait } from '../../api/client';
import { getSession as getStore, setSession } from '../../store/session';
import { readStageCache, writeStageCache } from '../../store/stageCache';
import CouncilSection from './CouncilSection';
import DilemmaSection from './DilemmaSection';
import EmotionSection from './EmotionSection';
import QuoteBlock from './QuoteBlock';
import VoicesSection from './VoicesSection';
import OrbitParticles from './OrbitParticles';
import type {
  InnerVoice,
  Portrait,
  PortraitCouncilPreview,
  PortraitDecisionFocus,
  PortraitDisplayLayer,
  PortraitDisplayVoice,
} from './types';

interface PortraitPageCache {
  portrait: Portrait | null;
  selectedLevel: string;
}

const VOICE_PALETTE = [
  { neon: '#00e5ff' }, // cyan
  { neon: '#7c3aed' }, // violet
  { neon: '#10b981' }, // emerald
  { neon: '#f59e0b' }, // amber
  { neon: '#ec4899' }, // pink
];

const LEVEL_LABELS: Record<string, string> = {
  L1: '聚焦梳理',
  L2: '多方拉扯',
  L3: '深层议会',
};

// Replaces lucide-react's <Loader2/> (not a dependency here) with a Tailwind
// `animate-spin` SVG so the immersive page keeps its loading affordance.
function Spinner({ size = 18, className = '' }: { size?: number; className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

const ParticleBackground = () => {
  // Random field is generated once on mount (client-only SPA), so a lazy
  // initializer is preferable to a setState-in-effect.
  const [particles] = useState(() =>
    Array.from({ length: 40 }).map((_, i) => {
      const colors = ['bg-cyan-300', 'bg-violet-400', 'bg-emerald-400', 'bg-pink-400', 'bg-white'];
      return {
        id: i,
        x: Math.random() * 100,
        y: Math.random() * 100,
        size: Math.random() * 3 + 1,
        delay: Math.random() * 12,
        duration: 10 + Math.random() * 15,
        color: colors[Math.floor(Math.random() * colors.length)],
      };
    }),
  );
  const [stars] = useState(() =>
    Array.from({ length: 120 }).map((_, i) => ({
      id: i,
      x: Math.random() * 100,
      y: Math.random() * 100,
      size: Math.random() * 2 + 0.5,
      delay: Math.random() * 4,
      opacity: Math.random() * 0.4 + 0.1,
    })),
  );

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none z-0">
      {/* Starry Sky Layer */}
      <div className="absolute inset-0 mix-blend-screen opacity-70">
        {stars.map((s) => (
          <div
            key={`star-${s.id}`}
            className="absolute rounded-full bg-white animate-twinkle"
            style={
              {
                left: `${s.x}%`,
                top: `${s.y}%`,
                width: `${s.size}px`,
                height: `${s.size}px`,
                animationDelay: `${s.delay}s`,
                '--twinkle-base-opacity': s.opacity,
              } as CSSProperties
            }
          />
        ))}
      </div>

      {/* Floating Particles Layer */}
      <div className="absolute inset-0 mix-blend-screen opacity-50">
        {particles.map((p) => (
          <div
            key={`particle-${p.id}`}
            className={`absolute rounded-full ${p.color} opacity-0 animate-float-particle`}
            style={{
              left: `${p.x}%`,
              top: `${p.y}%`,
              width: `${p.size}px`,
              height: `${p.size}px`,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.duration}s`,
              boxShadow: `0 0 ${p.size * 2.5}px currentColor`,
            }}
          />
        ))}
      </div>
    </div>
  );
};

const LevelSelector = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => (
  <div className="grid grid-cols-3 gap-2 relative">
    <div className="absolute -inset-1 bg-gradient-to-r from-cyan-500/0 via-cyan-500/5 to-cyan-500/0 blur-xl pointer-events-none" />
    {['L1', 'L2', 'L3'].map((lx) => {
      const active = value === lx;
      return (
        <button
          key={lx}
          type="button"
          onClick={() => onChange(lx)}
          className={`relative flex flex-col items-center justify-center p-2.5 rounded-xl border backdrop-blur-xl transition-all duration-500 overflow-hidden group ${
            active
              ? 'border-cyan-400/50 bg-cyan-950/40 text-cyan-50 shadow-[0_0_20px_rgba(0,229,255,0.2),0_1px_2px_rgba(255,255,255,0.1)_inset] scale-[1.02]'
              : 'border-white/10 bg-[#05080e]/40 text-white/60 hover:border-white/20 hover:bg-white/[0.05]'
          }`}
        >
          {active && <div className="absolute inset-0 bg-gradient-to-t from-cyan-400/10 to-transparent pointer-events-none" />}
          <div className="text-xs font-bold tracking-widest relative z-10">{lx}</div>
          <div
            className={`text-[8px] uppercase tracking-[0.1em] mt-1 transition-colors duration-300 relative z-10 ${
              active ? 'text-cyan-200/80' : 'text-white/40 group-hover:text-white/60'
            }`}
          >
            {LEVEL_LABELS[lx]}
          </div>
        </button>
      );
    })}
  </div>
);

const InteractiveDecisionFocus = ({ focus }: { focus: PortraitDecisionFocus | null }) => {
  if (!focus) return null;
  return (
    <div className="flex-1 flex flex-col rounded-3xl border border-white/[0.08] bg-[#05080e]/60 p-4 lg:p-5 backdrop-blur-xl shadow-[0_8px_32px_rgba(0,0,0,0.4),0_1px_2px_rgba(255,255,255,0.05)_inset] relative group min-h-0 overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-px w-full bg-gradient-to-r from-transparent via-white/10 to-transparent pointer-events-none" />
      <div className="text-[10px] lg:text-[11px] uppercase tracking-[0.18em] text-white/40 mb-2.5 shrink-0">Decision Focus</div>
      <h2 className="text-sm lg:text-base font-medium text-white mb-3 line-clamp-3 shrink-0 leading-snug">{focus.summary}</h2>

      <div className="flex flex-col gap-2 flex-1 min-h-0 overflow-y-auto custom-scrollbar pr-1">
        {[focus.option_a, focus.option_b].map((opt, i) => (
          <div
            key={i}
            className="shrink-0 rounded-xl bg-white/[0.03] border border-white/[0.05] p-3 flex flex-col transition-all hover:bg-white/[0.05]"
          >
            <h3 className="text-cyan-200 text-xs lg:text-sm font-medium shrink-0">{opt.label}</h3>
            {opt.description && <p className="text-[11px] lg:text-xs text-white/60 leading-relaxed mt-1">{opt.description}</p>}
          </div>
        ))}
      </div>

      {focus.why_hard && (
        <div className="mt-2.5 p-2.5 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.04] text-cyan-100/70 text-[11px] lg:text-xs leading-relaxed border-l-2 border-l-cyan-400 shrink-0">
          {focus.why_hard}
        </div>
      )}
    </div>
  );
};

const InteractiveMeaningLayers = ({ layers }: { layers: PortraitDisplayLayer[] }) => {
  const [activeLayer, setActiveLayer] = useState(0);
  if (!layers?.length) return null;

  return (
    <div className="shrink-0 lg:flex-1 flex flex-col rounded-3xl border border-white/[0.08] bg-[#05080e]/60 p-4 lg:p-4 backdrop-blur-xl shadow-[0_8px_32px_rgba(0,0,0,0.4),0_1px_2px_rgba(255,255,255,0.05)_inset] min-h-0 overflow-hidden">
      <div className="text-[10px] lg:text-[11px] uppercase tracking-[0.18em] text-white/40 mb-2.5 shrink-0">Meaning Layers</div>
      <div className="flex flex-col gap-2 flex-1 overflow-y-auto custom-scrollbar pr-1">
        {layers.map((l, i) => {
          const isActive = activeLayer === i;
          return (
            <div
              key={i}
              onClick={() => setActiveLayer(i)}
              className={`shrink-0 rounded-xl border transition-all duration-300 cursor-pointer overflow-hidden ${
                isActive ? 'bg-white/[0.05] border-white/20' : 'bg-white/[0.01] border-white/5 hover:bg-white/[0.03]'
              }`}
            >
              <div className="px-3 py-2.5 text-xs lg:text-sm font-medium flex items-center justify-between text-white/90">
                {l.title}
                <span className={`text-xs transition-transform duration-300 transform ${isActive ? 'rotate-180 text-cyan-300' : 'text-white/30'}`}>
                  ↓
                </span>
              </div>
              <AnimatePresence>
                {isActive && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="px-3 text-[11px] lg:text-xs text-white/60 space-y-2 pb-3"
                  >
                    <p className="leading-relaxed pt-1 border-t border-white/10">{l.description}</p>
                    {l.evidence && <p className="italic text-white/40 pl-2 border-l border-white/10">"{l.evidence}"</p>}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          );
        })}
      </div>
    </div>
  );
};

const InteractiveVoicesSection = ({ voices }: { voices: PortraitDisplayVoice[] }) => {
  const [activeIdx, setActiveIdx] = useState(0);

  if (!voices?.length) return null;
  const safeIdx = Math.min(activeIdx, voices.length - 1);
  const activeVoice = voices[safeIdx];
  const colorObj = VOICE_PALETTE[safeIdx % VOICE_PALETTE.length];

  return (
    <div className="h-full flex flex-col rounded-3xl border border-white/[0.08] bg-[#05080e]/60 backdrop-blur-xl shadow-[0_8px_32px_rgba(0,0,0,0.4),0_1px_2px_rgba(255,255,255,0.05)_inset] overflow-hidden relative group">
      <div className="absolute inset-0 bg-gradient-to-br from-white/[0.03] to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none" />

      <div className="text-[10px] lg:text-[11px] uppercase tracking-[0.18em] text-white/40 mt-6 lg:mt-8 mb-2 text-center shrink-0 z-10 relative">
        Inner Voices
      </div>

      <div className="flex-1 p-2 lg:p-6 w-full flex flex-col items-center justify-center text-center relative min-h-0 pb-4">
        <div className="absolute inset-0 pointer-events-none -m-4 lg:-m-12 opacity-80 mix-blend-screen scale-110 lg:scale-125 z-0">
          <OrbitParticles color={colorObj.neon} />
        </div>

        {/* Circular Layout representing "Internal Council" */}
        <div className="relative w-[280px] h-[280px] md:w-[380px] md:h-[380px] lg:w-[460px] lg:h-[460px] shrink-0 z-10 mt-6 md:mt-8 lg:mt-10 mb-8">
          {/* Center Active Voice details */}
          <AnimatePresence mode="wait">
            <motion.div
              key={safeIdx}
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              transition={{ duration: 0.4 }}
              className="absolute inset-0 flex justify-center items-center z-10 pointer-events-none"
            >
              <motion.div
                animate={{ y: [-4, 4, -4] }}
                transition={{ duration: 6, repeat: Infinity, ease: 'easeInOut' }}
                className="flex flex-col items-center justify-center w-full h-full p-2 lg:p-4 relative"
              >
                <div className="absolute inset-0 rounded-full border border-white/[0.04] bg-[#05080e]/95 backdrop-blur-xl shadow-[0_0_40px_rgba(0,0,0,0.8)] -z-10" />

                <motion.div
                  animate={{ scale: [1, 1.05, 1], rotate: [-5, 5, -5] }}
                  transition={{ duration: 6, repeat: Infinity, ease: 'easeInOut' }}
                  className="relative w-16 h-16 md:w-20 md:h-20 lg:w-24 lg:h-24 mb-3 md:mb-4 lg:mb-5 shrink-0 flex items-center justify-center"
                >
                  {/* Outer pulsing halo */}
                  <motion.div
                    animate={{ scale: [1, 1.3, 1], opacity: [0.15, 0.4, 0.15] }}
                    transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
                    className="absolute w-[200%] h-[200%] rounded-full mix-blend-screen pointer-events-none"
                    style={{ background: `radial-gradient(circle, ${colorObj.neon} 0%, transparent 60%)` }}
                  />
                  {/* Inner strong glow */}
                  <div className="absolute w-[120%] h-[120%] rounded-full blur-md opacity-80 mix-blend-screen" style={{ backgroundColor: colorObj.neon }} />
                  {/* Soft Core */}
                  <div className="absolute w-[60%] h-[60%] rounded-full bg-white opacity-90 blur-[3px]" style={{ boxShadow: `0 0 25px 8px ${colorObj.neon}` }} />
                  {/* Bright Center Dot */}
                  <div className="absolute w-[30%] h-[30%] bg-white rounded-full shadow-[0_0_15px_#fff]" />
                  {/* Surface detail / rotation */}
                  <motion.div
                    animate={{ rotate: 360 }}
                    transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
                    className="absolute w-[50%] h-[50%] rounded-full mix-blend-overlay opacity-50 border-[2px] border-black border-dashed"
                  />
                </motion.div>
                <h3
                  className="text-2xl md:text-3xl lg:text-4xl font-bold mb-1.5 md:mb-2 font-sans tracking-wide shrink-0"
                  style={{ color: colorObj.neon, textShadow: `0 0 25px ${colorObj.neon}80` }}
                >
                  {activeVoice.name}
                </h3>

                <div className="flex items-center gap-2 mb-2 lg:mb-4 shrink-0">
                  <div className="h-1.5 lg:h-2 w-24 md:w-32 lg:w-40 bg-white/10 rounded-full overflow-hidden shadow-inner">
                    <motion.div
                      key={`progress-${safeIdx}`}
                      initial={{ width: 0 }}
                      animate={{ width: `${activeVoice.intensity * 100}%` }}
                      transition={{ duration: 0.8, delay: 0.2 }}
                      className="h-full rounded-full"
                      style={{ backgroundColor: colorObj.neon, boxShadow: `0 0 10px ${colorObj.neon}` }}
                    />
                  </div>
                  <span className="font-mono text-[10px] md:text-[11px] lg:text-sm text-white/50">{Math.round(activeVoice.intensity * 100)}%</span>
                </div>

                <div className="space-y-1.5 md:space-y-2 lg:space-y-3 w-[240px] md:max-w-[280px] lg:max-w-[320px] shrink-0">
                  <div className="p-1.5 md:p-2 lg:p-3 rounded-xl bg-white/[0.03] border border-white/[0.06] text-left backdrop-blur-md relative overflow-hidden shadow-[0_4px_15px_rgba(0,0,0,0.2)]">
                    <div className="absolute left-0 top-0 bottom-0 w-0.5 lg:w-1 opacity-70" style={{ backgroundColor: colorObj.neon }} />
                    <span className="text-[9px] md:text-[10px] lg:text-xs uppercase tracking-widest text-white/40 block mb-[3px]">Concern</span>
                    <span className="text-white/90 text-[11px] md:text-xs lg:text-[14px] leading-snug block line-clamp-2">{activeVoice.concern}</span>
                  </div>
                  <div className="p-1.5 md:p-2 lg:p-3 rounded-xl bg-white/[0.03] border border-white/[0.06] text-left backdrop-blur-md relative overflow-hidden shadow-[0_4px_15px_rgba(0,0,0,0.2)]">
                    <div className="absolute left-0 top-0 bottom-0 w-0.5 lg:w-1 opacity-70" style={{ backgroundColor: colorObj.neon }} />
                    <span className="text-[9px] md:text-[10px] lg:text-xs uppercase tracking-widest text-white/40 block mb-[3px]">Intent</span>
                    <span className="text-white/90 text-[11px] md:text-xs lg:text-[14px] leading-snug block line-clamp-2">{activeVoice.protective_intent}</span>
                  </div>
                </div>
              </motion.div>
            </motion.div>
          </AnimatePresence>

          {/* Orbiting Voice Buttons */}
          {voices.map((v, i) => {
            const c = VOICE_PALETTE[i % VOICE_PALETTE.length];
            const isActive = i === safeIdx;
            const angle = (i / voices.length) * Math.PI * 2 - Math.PI / 2; // start from top

            const left = 50 + Math.cos(angle) * 44; // Keep orbit inside box bounds
            const top = 50 + Math.sin(angle) * 44;

            return (
              <motion.button
                key={i}
                type="button"
                aria-label={v.name}
                animate={{ y: [-3, 3, -3] }}
                transition={{ duration: 4 + i * 0.5, repeat: Infinity, ease: 'easeInOut' }}
                onClick={() => setActiveIdx(i)}
                className={`absolute z-20 h-10 w-10 md:h-12 md:w-12 lg:h-14 lg:w-14 -ml-5 -mt-5 md:-ml-6 md:-mt-6 lg:-ml-7 lg:-mt-7 rounded-full flex items-center justify-center transition-all duration-500 cursor-pointer ${
                  isActive ? 'scale-110' : 'scale-90 opacity-70 hover:opacity-100 hover:scale-100'
                }`}
                style={{
                  left: `${left}%`,
                  top: `${top}%`,
                }}
              >
                <div className="relative w-full h-full flex items-center justify-center pointer-events-none">
                  {/* Active Halo Expansion */}
                  {isActive && (
                    <motion.div
                      layoutId="orbitHalo"
                      className="absolute w-[160%] h-[160%] rounded-full mix-blend-screen"
                      style={{ background: `radial-gradient(circle, ${c.neon} 0%, transparent 60%)`, opacity: 0.3 }}
                    />
                  )}
                  {/* Core Glow */}
                  <div className="absolute w-[45%] h-[45%] md:w-[40%] md:h-[40%] rounded-full blur-[2px]" style={{ backgroundColor: c.neon, opacity: isActive ? 0.9 : 0.4 }} />
                  {/* Star Core */}
                  <div className="absolute w-[20%] h-[20%] md:w-[15%] md:h-[15%] bg-white rounded-full shadow-[0_0_10px_2px_rgba(255,255,255,0.8)] blur-[0.5px]" style={{ opacity: isActive ? 1 : 0.6 }} />
                  {/* Ambient Area Radiance */}
                  <div className="absolute inset-[-10px] rounded-full mix-blend-screen" style={{ background: `radial-gradient(circle at center, ${c.neon} 0%, transparent 70%)`, opacity: isActive ? 0.8 : 0.3 }} />
                </div>
              </motion.button>
            );
          })}

          {/* Visual connecting rings */}
          <div className="absolute inset-4 rounded-full border border-white/[0.06] z-0" />
          <div className="absolute inset-[-10px] lg:inset-[-16px] rounded-full border border-dashed border-white/[0.08] z-0 animate-spin" style={{ animationDuration: '40s' }} />
        </div>

        {/* Render the selected evidence string below */}
        <div className="h-16 lg:h-20 mt-2 lg:mt-4 w-full px-4 flex items-center justify-center shrink-0 z-10">
          {activeVoice.evidence && (
            <AnimatePresence mode="wait">
              <motion.div
                key={`evi-${safeIdx}`}
                initial={{ opacity: 0, y: 5 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -5 }}
                className="italic text-white/40 text-[10px] md:text-[11px] lg:text-xs leading-relaxed text-center"
              >
                "{activeVoice.evidence}"
              </motion.div>
            </AnimatePresence>
          )}
        </div>
      </div>
    </div>
  );
};

const InteractiveCouncilPreview = ({
  preview,
  selectedLevel,
  onLevelChange,
}: {
  preview: PortraitCouncilPreview | null;
  selectedLevel: string;
  onLevelChange: (l: string) => void;
}) => {
  if (!preview) return null;
  return (
    <div className="flex-1 flex flex-col rounded-3xl border border-white/[0.08] bg-[#05080e]/60 p-4 lg:p-4 backdrop-blur-xl shadow-[0_8px_32px_rgba(0,0,0,0.4),0_1px_2px_rgba(255,255,255,0.05)_inset] min-h-0 overflow-hidden">
      <div className="text-[10px] lg:text-[11px] uppercase tracking-[0.18em] text-cyan-200/65 mb-2.5 shrink-0">Council Preview</div>
      <p className="text-xs lg:text-[13px] text-white/80 leading-snug mb-3 shrink-0">{preview.summary}</p>

      <div className="flex-1 overflow-y-auto custom-scrollbar pr-1.5 space-y-2">
        {preview.roles.map((r, i) => {
          const isInner = r.source === 'inner_voice';
          const colorObj = isInner && VOICE_PALETTE[i % VOICE_PALETTE.length] ? VOICE_PALETTE[i % VOICE_PALETTE.length] : null;

          return (
            <div
              key={i}
              className="rounded-xl border border-white/[0.04] bg-white/[0.02] p-2.5 lg:p-3 border-l-2 transition-colors hover:bg-white/[0.04]"
              style={{ borderLeftColor: colorObj ? colorObj.neon : 'rgba(255,255,255,0.1)' }}
            >
              <div className="flex justify-between items-start mb-1">
                <span className="text-[11px] lg:text-xs font-semibold text-white/90">{r.display_name}</span>
                <span className="text-[9px] px-1.5 py-0.5 rounded-sm bg-white/5 text-white/50">{isInner ? 'Voice' : 'Supp'}</span>
              </div>
              <p className="text-[10px] lg:text-[11px] text-white/60 leading-snug">
                <span style={{ color: colorObj ? colorObj.neon : undefined }} className="opacity-80 mr-1">
                  {isInner ? '承接:' : '补充:'}
                </span>
                {r.represents}
              </p>
            </div>
          );
        })}
      </div>

      <div className="shrink-0 mt-3.5 pt-3.5 border-t border-white/[0.05]">
        <LevelSelector value={selectedLevel} onChange={onLevelChange} />
      </div>
    </div>
  );
};

const ActionButton = ({ isConfirming, onConfirm }: { isConfirming: boolean; onConfirm: () => void }) => (
  <button
    onClick={onConfirm}
    disabled={isConfirming}
    className="mt-4 shrink-0 w-full flex items-center justify-center rounded-2xl border border-cyan-200/30 bg-cyan-300/15 py-3.5 tracking-[0.2em] text-cyan-50 backdrop-blur transition-all duration-300 hover:bg-cyan-300/25 hover:shadow-[0_0_22px_rgba(0,229,255,.35)] disabled:opacity-50 relative overflow-hidden group"
  >
    <div className="absolute inset-0 bg-gradient-to-r from-cyan-400/0 via-cyan-400/10 to-cyan-400/0 opacity-0 group-hover:opacity-100 transition-opacity duration-700 pointer-events-none" />
    <div className="relative flex items-center text-sm lg:text-base font-medium">
      {isConfirming ? (
        <>
          <Spinner className="mr-2" size={18} />
          准备中...
        </>
      ) : (
        '确认画像，进入议会'
      )}
    </div>
  </button>
);

export default function PortraitPage() {
  const navigate = useNavigate();
  const sessionId = getStore().sessionId;
  const cached = readStageCache<PortraitPageCache>(sessionId, 'portrait');
  const [portrait, setPortrait] = useState<Portrait | null>(() => cached?.portrait ?? null);
  const [loading, setLoading] = useState(() => Boolean(sessionId) && !cached?.portrait);
  const [error, setError] = useState<string | null>(null);
  const [selectedLevel, setSelectedLevel] = useState(() => cached?.selectedLevel ?? 'L2');
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!sessionId) {
      navigate('/');
      return;
    }
    if (portrait) {
      return;
    }

    let cancelled = false;
    getPortrait(sessionId)
      .then((data) => {
        if (cancelled) {
          return;
        }
        setPortrait(data);
        setSelectedLevel(data.complexity.level);
        setSession({ status: 'portrait_pending', conflictProfile: { core_dilemma: data.core_dilemma } });
      })
      .catch((requestError) => {
        console.error(requestError);
        if (!cancelled) {
          setError('画像加载失败');
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId, navigate, portrait]);

  useEffect(() => {
    if (!sessionId) return;
    writeStageCache<PortraitPageCache>(sessionId, 'portrait', {
      portrait,
      selectedLevel,
    });
  }, [portrait, selectedLevel, sessionId]);

  async function handleDilemmaChange(coreDilemma: string) {
    if (!sessionId) {
      return;
    }
    const updated = await updatePortrait(sessionId, { core_dilemma: coreDilemma });
    setPortrait(updated);
    setSelectedLevel(updated.complexity.level);
  }

  async function handleVoicesChange(innerVoices: InnerVoice[]) {
    if (!sessionId) {
      return;
    }
    const updated = await updatePortrait(sessionId, { inner_voices: innerVoices, debate_level: selectedLevel });
    setPortrait(updated);
    setSelectedLevel(updated.complexity.level);
  }

  async function handleLevelChange(level: string) {
    if (!sessionId) {
      return;
    }
    setSelectedLevel(level);
    const updated = await updatePortrait(sessionId, { debate_level: level });
    setPortrait(updated);
    setSelectedLevel(updated.complexity.level);
  }

  async function handleConfirm() {
    if (!sessionId) {
      return;
    }
    setConfirming(true);
    setError(null);
    try {
      await confirmPortrait(sessionId, selectedLevel);
      setSession({ status: 'debating' });
      navigate('/debate');
    } catch (requestError) {
      console.error(requestError);
      setError('确认画像失败，请重试。');
      setConfirming(false);
    }
  }

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#000205]">
        <div className="flex items-center gap-3 rounded-full border border-white/12 bg-[#05080e]/70 px-6 py-3 backdrop-blur shadow-[0_10px_40px_rgba(0,0,0,.45)]">
          <Spinner className="text-cyan-400" size={18} />
          <span className="text-sm font-medium text-cyan-50">正在整理你的内心画像...</span>
        </div>
      </div>
    );
  }

  if (error && !portrait) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#000205] px-6 text-center">
        <p className="text-sm text-rose-300">{error || '暂时无法加载画像。'}</p>
      </div>
    );
  }

  if (!portrait) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#000205] px-6 text-center">
        <p className="text-sm text-white/60">暂时无法加载画像。</p>
      </div>
    );
  }

  const display = portrait.display;

  // ─── Legacy fallback (no `display`): keep the original in-flow sections. ───
  if (!display) {
    const quoteAfterDilemma = portrait.quote_placements.find((item) => item.after_section === 'dilemma');
    const quoteAfterVoices = portrait.quote_placements.find((item) => item.after_section === 'voices');

    return (
      <div className="relative mx-auto max-w-5xl px-4 py-8 sm:px-6">
        <div className="absolute inset-x-0 top-0 -z-10 h-72 rounded-[3rem] bg-[radial-gradient(circle_at_top,_rgba(251,191,36,0.18),_transparent_55%),radial-gradient(circle_at_right,_rgba(244,114,182,0.12),_transparent_38%),linear-gradient(180deg,_rgba(255,255,255,0.96),_rgba(248,250,252,0.92))]" />

        <header className="mx-auto max-w-3xl text-center">
          <p className="text-[11px] uppercase tracking-[0.28em] text-slate-400">Portrait</p>
          <h1 className="mt-4 text-4xl font-light tracking-tight text-slate-900 sm:text-5xl">你的内心画像</h1>
          <p className="mt-4 text-sm leading-7 text-slate-500">
            这里承接第一阶段对话里已经浮现出的真实张力。你可以微调表述，也可以直接带着这幅画像进入议会。
          </p>
        </header>

        <div className="mt-10 space-y-6">
          <DilemmaSection
            coreDilemma={portrait.core_dilemma}
            dilemmaLayers={portrait.dilemma_layers}
            onDilemmaChange={handleDilemmaChange}
          />

          {quoteAfterDilemma ? <QuoteBlock quote={quoteAfterDilemma.quote} sourceEmotion={quoteAfterDilemma.source_emotion} /> : null}

          <VoicesSection voices={portrait.inner_voices} tensions={portrait.core_tensions} onVoicesChange={handleVoicesChange} />

          <EmotionSection emotions={portrait.emotion_map} />

          {quoteAfterVoices ? <QuoteBlock quote={quoteAfterVoices.quote} sourceEmotion={quoteAfterVoices.source_emotion} /> : null}

          <CouncilSection
            complexity={portrait.complexity}
            assignments={portrait.agent_assignments}
            selectedLevel={selectedLevel}
            onLevelChange={handleLevelChange}
          />

          <div className="pb-4">
            <button
              onClick={handleConfirm}
              disabled={confirming}
              className="w-full rounded-[1.75rem] bg-slate-900 px-6 py-4 text-base text-white shadow-[0_20px_40px_-24px_rgba(15,23,42,0.85)] transition hover:bg-slate-800 disabled:opacity-60"
            >
              {confirming ? '正在准备辩论...' : '确认画像，开始辩论'}
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ─── Immersive display (the live path): full-screen council chamber. ───
  return (
    <div className="fixed inset-0 z-50 w-full bg-[#000205] overflow-y-auto lg:overflow-hidden text-slate-200 font-sans flex flex-col animate-fade-in">
      <ParticleBackground />
      {/* Background Glows */}
      <div
        className="absolute inset-0 pointer-events-none opacity-80"
        style={{
          background: `
            radial-gradient(circle at 50% 30%, rgba(14,165,233,.10), transparent 50%),
            radial-gradient(circle at 80% 60%, rgba(124,58,237,.06), transparent 40%),
            radial-gradient(circle at 20% 80%, rgba(16,185,129,.03), transparent 40%)
          `,
        }}
      />

      {/* Header */}
      <div className="px-5 py-4 lg:px-8 xl:px-10 lg:py-6 flex flex-col lg:flex-row lg:items-center justify-between relative z-10 shrink-0 gap-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.3em] text-cyan-200/50 font-semibold drop-shadow-[0_0_12px_rgba(0,229,255,0.3)] mb-1">
            Portrait
          </div>
          <h1 className="text-3xl lg:text-4xl wordmark drop-shadow-[0_4px_24px_rgba(0,0,0,0.5)]">你的内心画像</h1>
        </div>
        <div className="flex items-center gap-4 lg:justify-end">
          <div className="hidden lg:block w-12 h-[1px] bg-white/10" />
          <p className="text-white/40 text-[10px] lg:text-[11px] max-w-[240px] font-light leading-relaxed lg:text-right tracking-wider">
            <span className="text-white/60 font-medium">第一阶段：真实张力</span>
            <br />
            检视画像，将其带入更深层的探讨
          </p>
        </div>
      </div>

      {/* Main Grid */}
      <div className="flex-1 px-4 md:px-6 lg:px-8 xl:px-10 pb-4 lg:pb-6 xl:pb-8 relative z-10 min-h-0 flex flex-col lg:grid lg:grid-cols-12 gap-4 lg:gap-5 xl:gap-6 overflow-y-auto lg:overflow-hidden custom-scrollbar">
        {/* LEFT COLUMN */}
        <div className="col-span-12 lg:col-span-4 xl:col-span-3 flex flex-col gap-4 lg:gap-5 xl:gap-6 h-[750px] lg:h-full lg:min-h-0">
          <InteractiveDecisionFocus focus={display.decision_focus} />
          <InteractiveMeaningLayers layers={display.layers} />
        </div>

        {/* CENTER COLUMN */}
        <div className="col-span-12 lg:col-span-4 xl:col-span-6 flex flex-col h-[650px] lg:h-full lg:min-h-0">
          <InteractiveVoicesSection voices={display.voices} />
        </div>

        {/* RIGHT COLUMN */}
        <div className="col-span-12 lg:col-span-4 xl:col-span-3 flex flex-col h-[600px] lg:h-full lg:min-h-0">
          <InteractiveCouncilPreview preview={display.council_preview} selectedLevel={selectedLevel} onLevelChange={handleLevelChange} />
          {error && (
            <div className="mt-3 p-3 rounded-xl border border-red-500/30 bg-red-500/10 text-red-100 text-xs text-center shrink-0">{error}</div>
          )}
          <ActionButton isConfirming={confirming} onConfirm={handleConfirm} />
        </div>
      </div>
    </div>
  );
}
