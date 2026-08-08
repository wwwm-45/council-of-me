import { useState, useRef, useEffect, useCallback, type SVGProps } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion } from 'motion/react';
import { editLastElicitationMessage, finishElicitation, postElicitation, streamElicitation, getSession as getSessionApi, type PortraitQuality } from '../api/client';
import { getSession as getStore, setSession } from '../store/session';
import { readStageCache, writeStageCache } from '../store/stageCache';
import CrisisModal from '../components/CrisisModal';
import { buildCouncilOpeningMessage } from '../content/interview';

const NOISE_PATTERN = `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noiseFilter'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noiseFilter)'/%3E%3C/svg%3E")`;

const PARTICLES = Array.from({ length: 56 }).map((_, id) => ({
  id,
  left: `${Math.random() * 100}%`,
  top: `${Math.random() * 120}%`,
  size: Math.random() * 3 + 1,
  duration: Math.random() * 28 + 38,
  delay: Math.random() * -60,
  opacity: Math.random() * 0.28 + 0.08,
  moveX: (Math.random() - 0.5) * 140,
  moveY: (Math.random() * -360) - 100,
  blur: Math.random() * 4 + 1,
}));

function CheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function PenLineIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function SendIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="m22 2-7 20-4-9-9-4Z" />
      <path d="M22 2 11 13" />
    </svg>
  );
}

function SparklesIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="m12 3 1.9 5.7L20 11l-6.1 2.3L12 21l-1.9-7.7L4 11l6.1-2.3Z" />
      <path d="M5 3v4" />
      <path d="M3 5h4" />
      <path d="M19 17v4" />
      <path d="M17 19h4" />
    </svg>
  );
}

function XIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface CrisisResource {
  name: string;
  phone: string;
  description: string;
}

interface ElicitationDepthEvaluation {
  depth_score?: number;
  depth_layer?: number;
  recommended_action?: string;
}

interface ElicitationSessionData {
  status?: string;
  display_name?: string;
  elicitation_history?: {
    conversation_history?: Message[];
    depth_evaluations?: ElicitationDepthEvaluation[];
  } | null;
}

interface ElicitationPageCache {
  messages: Message[];
  input: string;
  depthScore: number;
  depthLayer: number;
  closingHint: boolean;
  profileReady: boolean;
  qualityWarning: PortraitQuality | null;
}

function cleanDisplayName(value: string | null | undefined): string {
  return (value ?? '').trim().slice(0, 40);
}

function buildOpeningMessages(displayName?: string | null): Message[] {
  return [
    {
      role: 'assistant',
      content: buildCouncilOpeningMessage(displayName),
    },
  ];
}

interface RequestFailure {
  crisis?: boolean;
  resources?: CrisisResource[];
}

type ElicitationResponse = Awaited<ReturnType<typeof postElicitation>>;

function getRequestFailure(error: unknown): RequestFailure {
  if (typeof error !== 'object' || error === null) {
    return {};
  }
  return error as RequestFailure;
}

export default function ElicitationPage() {
  const nav = useNavigate();
  const store = getStore();
  const sid = store.sessionId;
  const cached = readStageCache<ElicitationPageCache>(sid, 'elicitation');
  const [messages, setMessages] = useState<Message[]>(() => (
    cached?.messages ?? buildOpeningMessages(store.userDisplayName)
  ));
  const [input, setInput] = useState(() => cached?.input ?? '');
  const [loading, setLoading] = useState(false);
  const [depthScore, setDepthScore] = useState(() => cached?.depthScore ?? 0);
  const [depthLayer, setDepthLayer] = useState(() => cached?.depthLayer ?? 1);
  const [closingHint, setClosingHint] = useState(() => cached?.closingHint ?? false);
  const [profileReady, setProfileReady] = useState(() => cached?.profileReady ?? false);
  const [qualityWarning, setQualityWarning] = useState<PortraitQuality | null>(() => cached?.qualityWarning ?? null);
  const [finishing, setFinishing] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingText, setEditingText] = useState('');
  const [editingSaving, setEditingSaving] = useState(false);
  const [crisis, setCrisis] = useState<{ resources: CrisisResource[] } | null>(null);
  const [restored, setRestored] = useState(() => Boolean(cached));
  const messageListRef = useRef<HTMLElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll only the transcript pane, so the immersive stage chrome stays fixed.
  useEffect(() => {
    const scroller = messageListRef.current;
    if (!scroller) return;
    if (typeof scroller.scrollTo === 'function') {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
      return;
    }
    scroller.scrollTop = scroller.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!sid) return;
    writeStageCache<ElicitationPageCache>(sid, 'elicitation', {
      messages,
      input,
      depthScore,
      depthLayer,
      closingHint,
      profileReady,
      qualityWarning,
    });
  }, [closingHint, depthLayer, depthScore, input, messages, profileReady, qualityWarning, sid]);

  // Restore conversation history from backend on mount
  useEffect(() => {
    if (sid && ['portrait_pending', 'profile_pending', 'complexity_pending', 'identity_pending'].includes(getStore().status)) {
      nav('/portrait');
      return;
    }
    if (!sid || restored) return;
    (async () => {
      try {
        const data = await getSessionApi(sid) as ElicitationSessionData;
        const hist = data.elicitation_history?.conversation_history;
        const depthEvaluations = data.elicitation_history?.depth_evaluations;
        // Continue the portrait flow directly when Phase 1 has already converged.
        if (data.status && ['portrait_pending', 'profile_pending', 'complexity_pending', 'identity_pending'].includes(data.status)) {
          setSession({ status: data.status });
          nav('/portrait');
          return;
        }
        const displayName = cleanDisplayName(data.display_name ?? getStore().userDisplayName);
        if (displayName) {
          setSession({ userDisplayName: displayName });
        }
        if (hist && Array.isArray(hist) && hist.length > 0) {
          const restoredMsgs: Message[] = hist.map((m) => ({
            role: m.role as 'user' | 'assistant',
            content: m.content,
          }));
          setMessages([...buildOpeningMessages(displayName), ...restoredMsgs]);
        } else if (displayName) {
          setMessages(buildOpeningMessages(displayName));
        }
        if (Array.isArray(depthEvaluations) && depthEvaluations.length > 0) {
          const latest = depthEvaluations[depthEvaluations.length - 1];
          setDepthScore(typeof latest?.depth_score === 'number' ? latest.depth_score : 0);
          setDepthLayer(typeof latest?.depth_layer === 'number' ? latest.depth_layer : 1);
          setClosingHint(latest?.recommended_action === 'prepare_closing');
        }
      } catch {
        // Silently fail — first conversation has no history
      } finally {
        setRestored(true);
      }
    })();
  }, [nav, sid, restored]);

  // Auto-resize textarea
  const adjustTextarea = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 160) + 'px';
  }, []);

  useEffect(() => { adjustTextarea(); }, [input, adjustTextarea]);

  if (!sid) { nav('/'); return null; }

  function handleFinishedProfile(data: ElicitationResponse) {
    if (data.requires_quality_confirmation && data.portrait_quality) {
      setQualityWarning(data.portrait_quality);
      return;
    }
    setQualityWarning(null);
    if (!data.should_continue && data.conflict_profile_draft) {
      setSession({ conflictProfile: data.conflict_profile_draft, status: 'portrait_pending' });
      setProfileReady(true);
    }
  }

  async function send() {
    if (!input.trim() || loading || finishing || editingSaving || editingIndex !== null) return;
    const userMsg = input.trim();
    setInput('');
    setQualityWarning(null);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setMessages((m) => [...m, { role: 'user', content: userMsg }]);
    setLoading(true);
    try {
      let assistantStarted = false;
      let streamFinal: ElicitationResponse | null = null;
      const updateAssistant = (content: string) => {
        if (!assistantStarted) {
          assistantStarted = true;
          setMessages((m) => [...m, { role: 'assistant', content }]);
          return;
        }
        setMessages((m) => {
          const next = [...m];
          next[next.length - 1] = { role: 'assistant', content };
          return next;
        });
      };

      try {
        await streamElicitation(sid!, userMsg, {
          // Tokens stream as the model drafts across audit retries, and intermediate
          // drafts can be rewritten or discarded before a candidate passes. We swallow
          // onToken/onCorrection so un-passed drafts never render — the "思考中..."
          // indicator covers the wait and only the final audited response (turn_end)
          // is committed to the conversation.
          onToken: () => {},
          onCorrection: () => {},
          onTurnEnd: (data) => {
            streamFinal = data;
            updateAssistant(data.response);
          },
        });
      } catch (streamError: unknown) {
        if (getRequestFailure(streamError).crisis || assistantStarted) {
          throw streamError;
        }
        const data = await postElicitation(sid!, userMsg);
        setMessages((m) => [...m, { role: 'assistant', content: data.response }]);
        setDepthScore(data.depth?.depth_score ?? 0);
        setDepthLayer(data.depth?.depth_layer ?? 1);
        setClosingHint(data.depth?.recommended_action === 'prepare_closing');
        handleFinishedProfile(data);
        return;
      }

      if (!streamFinal) {
        throw new Error('stream ended without final payload');
      }
      const data = streamFinal as ElicitationResponse;
      setDepthScore(data.depth?.depth_score ?? 0);
      setDepthLayer(data.depth?.depth_layer ?? 1);
      setClosingHint(data.depth?.recommended_action === 'prepare_closing');
      handleFinishedProfile(data);
    } catch (error: unknown) {
      const failure = getRequestFailure(error);
      if (failure.crisis) {
        setCrisis({ resources: failure.resources ?? [] });
      } else {
        setMessages((m) => [...m, { role: 'assistant', content: '抱歉，出了点问题，请重试。' }]);
      }
    } finally {
      setLoading(false);
    }
  }

  async function finishEarly(force = false) {
    if (loading || finishing || editingSaving || editingIndex !== null) return;
    setFinishing(true);
    try {
      const data = await finishElicitation(sid!, force);
      handleFinishedProfile(data);
    } catch {
      setMessages((m) => [...m, { role: 'assistant', content: '抱歉，提前结束对话时出了点问题，请重试。' }]);
    } finally {
      setFinishing(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const displayDepthLayer = Math.min(Math.max(depthLayer, 1), 2);
  const latestUserIndex = messages.reduce((latest, message, index) => (
    message.role === 'user' ? index : latest
  ), -1);

  function startEditing(index: number, content: string) {
    setEditingIndex(index);
    setEditingText(content);
  }

  function cancelEditing() {
    setEditingIndex(null);
    setEditingText('');
  }

  async function saveEditedMessage() {
    if (editingIndex === null || !editingText.trim() || editingSaving || loading || finishing) return;
    const message = editingText.trim();
    const targetIndex = editingIndex;
    setEditingSaving(true);
    setQualityWarning(null);
    try {
      const data = await editLastElicitationMessage(sid!, message);
      setMessages((current) => {
        const next = [...current];
        next[targetIndex] = { role: 'user', content: message };
        if (next[targetIndex + 1]?.role === 'assistant') {
          next.splice(targetIndex + 1, 1);
        }
        next.splice(targetIndex + 1, 0, { role: 'assistant', content: data.response });
        return next;
      });
      setDepthScore(data.depth?.depth_score ?? 0);
      setDepthLayer(data.depth?.depth_layer ?? 1);
      setClosingHint(data.depth?.recommended_action === 'prepare_closing');
      handleFinishedProfile(data);
      cancelEditing();
    } catch {
      setMessages((current) => [...current, { role: 'assistant', content: '抱歉，修改上一条消息时出了一点问题，请重试。' }]);
    } finally {
      setEditingSaving(false);
    }
  }

  return (
    <div className="relative isolate flex h-screen w-full flex-col overflow-hidden bg-[#010103] font-sans text-white antialiased selection:bg-teal-500/30">
      {crisis && <CrisisModal resources={crisis.resources} onClose={() => setCrisis(null)} />}

      <div className="pointer-events-none absolute inset-0 -z-30 bg-[radial-gradient(circle_at_50%_0%,_#07111f_0%,_#010103_72%)]" />
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-20 overflow-hidden opacity-80 mix-blend-screen">
        {PARTICLES.map((p) => (
          <motion.div
            key={p.id}
            animate={{
              x: [0, p.moveX * 0.5, p.moveX],
              y: [0, p.moveY * 0.6, p.moveY],
              opacity: [0, p.opacity, p.opacity * 0.75, 0],
              scale: [0.5, 1, 1.2, 0.8],
            }}
            transition={{ duration: p.duration, repeat: Infinity, ease: 'linear', delay: p.delay }}
            className="absolute rounded-full bg-teal-100/80"
            style={{
              left: p.left,
              top: p.top,
              width: p.size,
              height: p.size,
              filter: `blur(${p.blur}px)`,
              boxShadow: `0 0 ${p.size * 4}px rgba(45,212,191,0.36)`,
            }}
          />
        ))}
      </div>

      <div aria-hidden="true" className="pointer-events-none absolute left-1/2 top-[36%] -z-20 flex -translate-x-1/2 -translate-y-1/2 items-center justify-center">
        <motion.div
          animate={{ scale: [1, 1.15, 1], opacity: [0.3, 0.52, 0.3] }}
          transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
          className="absolute h-[900px] w-[900px] rounded-full bg-[radial-gradient(circle,_rgba(45,212,191,0.06)_0%,_rgba(0,0,0,0)_62%)] blur-[80px]"
        />
        <motion.div
          animate={{ scale: [0.95, 1.05, 0.95], opacity: [0.4, 0.7, 0.4] }}
          transition={{ duration: 8, repeat: Infinity, ease: 'easeInOut', delay: 1 }}
          className="absolute h-[450px] w-[450px] rounded-full bg-[radial-gradient(circle,_rgba(129,140,248,0.1)_0%,_rgba(0,0,0,0)_60%)] blur-[40px]"
        />
        <motion.div
          data-testid="elicitation-celestial-core"
          animate={{ scale: [0.98, 1.05, 0.98], opacity: [0.7, 0.9, 0.7], rotate: [0, 90, 180] }}
          transition={{
            scale: { duration: 6, repeat: Infinity, ease: 'easeInOut' },
            opacity: { duration: 6, repeat: Infinity, ease: 'easeInOut' },
            rotate: { duration: 30, repeat: Infinity, ease: 'linear' },
          }}
          className="absolute h-[160px] w-[160px] rounded-full bg-[radial-gradient(circle,_rgba(255,255,255,1)_0%,_rgba(204,251,244,0.8)_15%,_rgba(45,212,191,0.3)_40%,_transparent_70%)] blur-[4px] shadow-[0_0_120px_rgba(45,212,191,0.5),_0_0_40px_rgba(255,255,255,0.6)_inset]"
        />
      </div>
      <motion.div
        aria-hidden="true"
        animate={{ opacity: [0.55, 0.95, 0.55] }}
        transition={{ duration: 8, repeat: Infinity, ease: 'easeInOut' }}
        className="pointer-events-none absolute left-1/2 top-[36%] -z-10 h-[2px] w-[140vw] -translate-x-1/2 -translate-y-1/2 rounded-full bg-gradient-to-r from-transparent via-teal-500/10 to-transparent blur-[8px]"
      />
      <div aria-hidden="true" className="pointer-events-none absolute left-[30%] top-[20%] -z-10 h-[900px] w-[900px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[radial-gradient(ellipse_at_center,_rgba(20,184,166,0.05),_rgba(15,23,42,0.01),_transparent_70%)] blur-[120px]" />
      <div aria-hidden="true" className="pointer-events-none absolute right-[6%] top-[75%] -z-10 h-[1000px] w-[1000px] -translate-y-1/2 rounded-full bg-[radial-gradient(ellipse_at_center,_rgba(124,58,237,0.05),_rgba(30,41,59,0.01),_transparent_70%)] blur-[140px]" />
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 -z-10 opacity-[0.06] mix-blend-screen" style={{ backgroundImage: NOISE_PATTERN }} />

      <header className="z-20 mx-auto flex h-24 w-full max-w-[1920px] shrink-0 items-center justify-between px-5 sm:px-8 lg:px-12">
        <div className="group py-2">
          <h1 className="relative text-[0.8rem] font-light uppercase tracking-[0.4em] text-white/80 transition-all duration-700 group-hover:text-white">
            <span className="absolute -inset-x-8 -inset-y-4 z-0 bg-teal-400/20 opacity-0 blur-xl transition-opacity duration-700 group-hover:opacity-100" />
            <span className="relative z-10 drop-shadow-[0_0_12px_rgba(255,255,255,0.2)] transition-all duration-700 group-hover:drop-shadow-[0_0_20px_rgba(45,212,191,0.6)]">
              Council of Me
            </span>
          </h1>
        </div>

        <div className="flex items-center gap-3 sm:gap-6">
          <div className="hidden items-center gap-2 rounded-full px-3 py-2 text-[0.65rem] font-light uppercase tracking-[0.25em] text-white/35 sm:flex">
            <SparklesIcon className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Views</span>
          </div>
          <div className="flex items-center gap-3 rounded-[2rem] border border-white/[0.04] border-t-white/[0.1] bg-white/[0.015] px-4 py-2.5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.08),_0_8px_32px_rgba(0,0,0,0.4)] backdrop-blur-3xl sm:px-7">
            <div className="relative flex h-2 w-2 items-center justify-center">
              <div className="absolute h-full w-full animate-ping rounded-full bg-teal-400/40" />
              <div className="h-1.5 w-1.5 rounded-full bg-teal-300 shadow-[0_0_12px_rgba(45,212,191,0.8)]" />
            </div>
            <span className="text-[0.7rem] font-light tracking-[0.15em] text-white/80 sm:text-[0.75rem]">
              内心对话空间
            </span>
          </div>
        </div>
      </header>

      <main ref={messageListRef} className="z-10 flex min-h-0 w-full flex-1 flex-col items-center overflow-y-auto px-4 pb-4 pt-2 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden sm:px-8">
        <div className="flex w-full max-w-[820px] flex-col gap-8 pb-6">
          {messages.map((m, i) => {
            const isUser = m.role === 'user';
            const isEditing = editingIndex === i;
            const canEdit = (
              isUser
              && i === latestUserIndex
              && editingIndex === null
              && !loading
              && !finishing
              && !editingSaving
              && !profileReady
            );
            return (
              <div key={i} className={`relative flex w-full items-start gap-4 ${isUser ? 'justify-end' : 'justify-start'}`}>
                {!isUser && (
                  <div className="relative mt-2 flex h-10 w-10 shrink-0 items-center justify-center">
                    <div className="absolute h-full w-full rounded-full bg-[radial-gradient(circle,_rgba(45,212,191,0.15)_0%,_transparent_70%)] blur-[8px]" />
                    <div className="absolute z-10 h-1.5 w-1.5 rounded-full bg-white shadow-[0_0_12px_rgba(45,212,191,0.9),_0_0_4px_rgba(255,255,255,1)_inset]" />
                    <motion.div animate={{ scale: [1, 1.5, 1], opacity: [0.3, 0.7, 0.3] }} transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }} className="absolute h-3 w-3 rounded-full bg-teal-300/30 blur-[2px]" />
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 15, repeat: Infinity, ease: 'linear' }} className="absolute h-6 w-6 rounded-full border border-dashed border-teal-300/30" />
                    <motion.div animate={{ rotate: -360, scale: [0.95, 1.05, 0.95] }} transition={{ rotate: { duration: 25, repeat: Infinity, ease: 'linear' }, scale: { duration: 6, repeat: Infinity, ease: 'easeInOut' } }} className="absolute h-8 w-8 rounded-full border border-teal-500/15" />
                  </div>
                )}

                <div className={`group relative max-w-[min(76%,44rem)] ${isUser ? 'order-1' : ''}`}>
                  {canEdit && (
                    <button
                      type="button"
                      aria-label="编辑上一条消息"
                      title="编辑上一条消息"
                      onClick={() => startEditing(i, m.content)}
                      className="absolute -left-11 top-3 z-20 flex h-8 w-8 items-center justify-center rounded-full border border-white/[0.08] bg-white/[0.035] text-white/55 shadow-[inset_0_1px_1px_rgba(255,255,255,0.12),_0_8px_24px_rgba(0,0,0,0.35)] backdrop-blur-2xl transition hover:border-white/[0.14] hover:text-white disabled:opacity-50"
                    >
                      <PenLineIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  )}
                  <div className={`pointer-events-none absolute -inset-6 z-0 rounded-[2.5rem] blur-[42px] transition-all duration-1000 ${
                    isUser ? 'bg-indigo-500/5 group-hover:bg-indigo-400/10' : 'bg-teal-500/5 group-hover:bg-teal-400/10'
                  }`} />
                  <div className={`relative z-10 overflow-hidden rounded-[24px] border px-5 py-4 shadow-[inset_0_1px_1px_rgba(255,255,255,0.13),_0_16px_32px_-8px_rgba(0,0,0,0.62)] backdrop-blur-3xl sm:px-7 sm:py-5 ${
                    isUser
                      ? 'rounded-tr-[4px] border-white/[0.05] bg-gradient-to-bl from-indigo-500/[0.04] to-black/[0.32]'
                      : 'rounded-tl-[4px] border-white/[0.08] bg-gradient-to-br from-white/[0.055] to-white/[0.012]'
                  }`}>
                    <div className="absolute inset-x-0 top-0 h-[1px] bg-gradient-to-r from-transparent via-white/[0.24] to-transparent mix-blend-overlay" />
                    <div className={`absolute inset-y-0 ${isUser ? 'right-0' : 'left-0'} w-[1px] bg-gradient-to-b from-white/[0.18] via-transparent to-transparent mix-blend-overlay`} />
                    <div className="absolute inset-0 z-0 opacity-[0.055] mix-blend-plus-lighter" style={{ backgroundImage: NOISE_PATTERN }} />
                    <div className={`absolute -top-8 z-0 h-36 w-36 rounded-full blur-[40px] ${isUser ? '-right-8 bg-violet-400/5' : '-left-8 bg-teal-200/10'}`} />

                    {isEditing ? (
                      <div className="relative z-10 space-y-3">
                        <textarea
                          aria-label="编辑已发送消息"
                          value={editingText}
                          onChange={(event) => setEditingText(event.target.value)}
                          rows={3}
                          className="min-w-64 w-full resize-y rounded-2xl border border-white/[0.08] bg-black/25 px-4 py-3 text-sm leading-relaxed text-white/90 outline-none placeholder:text-white/25 focus:border-teal-300/30 focus:ring-2 focus:ring-teal-300/15"
                        />
                        <div className="flex flex-wrap justify-end gap-2">
                          <button
                            type="button"
                            onClick={cancelEditing}
                            disabled={editingSaving}
                            className="flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.025] px-3 py-1.5 text-xs font-medium text-white/65 transition hover:text-white disabled:opacity-50"
                          >
                            <XIcon className="h-3.5 w-3.5" aria-hidden="true" />
                            取消修改
                          </button>
                          <button
                            type="button"
                            onClick={saveEditedMessage}
                            disabled={editingSaving || !editingText.trim()}
                            className="flex items-center gap-1.5 rounded-full border border-teal-200/20 bg-teal-300/10 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-teal-300/15 disabled:opacity-50"
                          >
                            <CheckIcon className="h-3.5 w-3.5" aria-hidden="true" />
                            保存修改
                          </button>
                        </div>
                      </div>
                    ) : (
                      <p className={`relative z-10 whitespace-pre-wrap text-[1rem] font-light leading-[1.78] tracking-[0.02em] antialiased drop-shadow-[0_2px_10px_rgba(0,0,0,0.75)] ${
                        isUser ? 'text-white/82' : 'text-white/95'
                      }`}>
                        {m.content}
                      </p>
                    )}
                  </div>
                </div>

                {isUser && (
                  <div className="relative order-2 mt-2 flex h-10 w-10 shrink-0 items-center justify-center">
                    <div className="absolute h-full w-full rounded-full bg-[radial-gradient(circle,_rgba(129,140,248,0.2)_0%,_transparent_70%)] blur-[8px]" />
                    <div className="absolute z-10 h-1.5 w-1.5 rounded-full bg-white shadow-[0_0_12px_rgba(129,140,248,0.9),_0_0_4px_rgba(255,255,255,1)_inset]" />
                    <motion.div animate={{ scale: [0.9, 1.4, 0.9], opacity: [0.2, 0.6, 0.2] }} transition={{ duration: 5, repeat: Infinity, ease: 'easeInOut', delay: 1 }} className="absolute h-3 w-3 rounded-full bg-indigo-300/30 blur-[2px]" />
                    <motion.div animate={{ rotate: 360 }} transition={{ duration: 18, repeat: Infinity, ease: 'linear' }} className="absolute h-6 w-6 rounded-full border border-dashed border-indigo-400/30" />
                    <motion.div animate={{ rotate: -360, scale: [0.95, 1.05, 0.95] }} transition={{ rotate: { duration: 25, repeat: Infinity, ease: 'linear' }, scale: { duration: 6, repeat: Infinity, ease: 'easeInOut', delay: 2 } }} className="absolute h-8 w-8 rounded-full border border-indigo-500/15" />
                  </div>
                )}
              </div>
            );
          })}
          {loading && (
            <div className="relative flex w-full items-start justify-start gap-4">
              <div className="relative mt-2 flex h-10 w-10 shrink-0 items-center justify-center">
                <div className="absolute h-full w-full rounded-full bg-[radial-gradient(circle,_rgba(45,212,191,0.15)_0%,_transparent_70%)] blur-[8px]" />
                <div className="absolute z-10 h-1.5 w-1.5 rounded-full bg-white shadow-[0_0_12px_rgba(45,212,191,0.9)]" />
                <motion.div animate={{ scale: [1, 1.5, 1], opacity: [0.3, 0.7, 0.3] }} transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }} className="absolute h-5 w-5 rounded-full border border-teal-300/30" />
              </div>
              <div className="relative overflow-hidden rounded-[24px] rounded-tl-[4px] border border-white/[0.08] bg-gradient-to-br from-white/[0.05] to-white/[0.01] px-7 py-4 text-sm font-light tracking-[0.16em] text-white/55 shadow-[inset_0_1px_1px_rgba(255,255,255,0.12),_0_16px_32px_-8px_rgba(0,0,0,0.62)] backdrop-blur-3xl">
                <div className="absolute inset-0 opacity-[0.055]" style={{ backgroundImage: NOISE_PATTERN }} />
                <span className="relative">思考中...</span>
              </div>
            </div>
          )}
        </div>
      </main>

      <footer className="z-20 w-full shrink-0 px-4 pb-6 pt-0 sm:px-8 sm:pb-8">
        {profileReady ? (
          <div className="mx-auto flex w-full max-w-[840px] flex-col items-center gap-3">
            <p className="text-xs tracking-[0.2em] text-white/35">对话阶段已完成</p>
            <button
              onClick={() => nav('/portrait')}
              className="rounded-[1.5rem] border border-white/[0.1] bg-white/[0.035] px-8 py-3 text-sm font-medium text-white shadow-[inset_0_1px_1px_rgba(255,255,255,0.18),_0_18px_42px_rgba(0,0,0,0.45)] backdrop-blur-2xl transition hover:border-teal-200/25 hover:shadow-[inset_0_1px_1px_rgba(255,255,255,0.24),_0_0_32px_rgba(45,212,191,0.18)]"
            >
              查看你的困境画像 →
            </button>
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-[840px] flex-col items-center gap-5">
            {qualityWarning && (
              <div
                role="alert"
                aria-live="polite"
                className="w-full rounded-[1.35rem] border border-amber-200/15 bg-amber-300/[0.06] px-5 py-4 text-sm text-amber-50 shadow-[inset_0_1px_1px_rgba(255,255,255,0.12),_0_18px_40px_rgba(0,0,0,0.45)] backdrop-blur-3xl"
              >
                <div className="space-y-2">
                  {qualityWarning.issues.map((issue) => (
                    <div key={`${issue.code}-${issue.message}`}>
                      <p className="font-medium text-amber-50">{issue.message}</p>
                      <p className="mt-1 text-amber-100/75">{issue.suggestion}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => setQualityWarning(null)}
                    className="rounded-full border border-amber-100/20 bg-white/[0.035] px-3 py-2 text-xs font-medium text-amber-50 transition hover:bg-white/[0.06]"
                  >
                    继续聊几句
                  </button>
                  {qualityWarning.can_force_continue !== false && (
                    <button
                      type="button"
                      onClick={() => finishEarly(true)}
                      disabled={finishing}
                      className="rounded-full border border-amber-100/20 bg-amber-300/20 px-3 py-2 text-xs font-medium text-white transition hover:bg-amber-300/25 disabled:opacity-50"
                    >
                      仍然进入画像
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="group relative w-full">
              <div className="pointer-events-none absolute -bottom-12 left-1/2 -z-10 h-24 w-full -translate-x-1/2 rounded-full bg-teal-900/10 blur-[50px] transition-all duration-700 group-focus-within:bg-teal-800/15 group-focus-within:blur-[60px]" />
              <div className="relative flex w-full flex-col overflow-hidden rounded-[2rem] border border-white/[0.04] bg-white/[0.015] shadow-[inset_0_1px_1px_rgba(255,255,255,0.12),_inset_0_-1px_1px_rgba(255,255,255,0.02),_0_32px_64px_-16px_rgba(0,0,0,0.9)] backdrop-blur-[60px] backdrop-saturate-[1.3] transition-all duration-500 group-focus-within:border-white/[0.08] group-focus-within:bg-white/[0.025]">
                <div className="absolute inset-x-0 top-0 z-20 h-[1px] bg-gradient-to-r from-transparent via-white/[0.3] to-transparent mix-blend-overlay" />
                <div className="absolute inset-0 z-0 opacity-[0.06] mix-blend-plus-lighter" style={{ backgroundImage: NOISE_PATTERN }} />
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={finishing || editingSaving || editingIndex !== null}
                  placeholder="输入你的想法...（Shift+Enter 换行）"
                  rows={1}
                  className="relative z-10 w-full resize-none overflow-hidden bg-transparent px-6 pb-5 pt-6 text-[1rem] font-light leading-[1.6] tracking-wide text-white/90 outline-none placeholder:text-white/24 selection:bg-teal-500/40 disabled:opacity-50 sm:px-8 [&::-webkit-scrollbar]:hidden"
                  style={{ minHeight: '76px', maxHeight: '160px' }}
                />
                <div className="relative z-10 flex flex-col gap-3 border-t border-white/[0.03] bg-[linear-gradient(to_bottom,rgba(0,0,0,0)_0%,rgba(0,0,0,0.2)_100%)] px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
                  <button
                    type="button"
                    onClick={() => finishEarly(false)}
                    disabled={loading || finishing || editingSaving || editingIndex !== null}
                    className="flex items-center justify-center gap-2 rounded-full px-4 py-2 text-white/45 opacity-80 transition-all duration-300 hover:bg-white/5 hover:text-white hover:opacity-100 disabled:opacity-30 sm:justify-start"
                  >
                    <XIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    <span className="text-[0.7rem] font-light tracking-[0.2em]">提前结束对话</span>
                  </button>
                  <button
                    onClick={send}
                    disabled={loading || finishing || editingSaving || editingIndex !== null || !input.trim()}
                    className="group/btn relative overflow-hidden rounded-[1.5rem] border border-white/[0.08] bg-white/[0.025] px-7 py-2.5 shadow-[inset_0_1px_1px_rgba(255,255,255,0.2),_0_8px_24px_rgba(0,0,0,0.4)] backdrop-blur-md transition-all duration-500 hover:border-white/[0.15] hover:shadow-[inset_0_1px_1px_rgba(255,255,255,0.3),_0_0_32px_rgba(45,212,191,0.2)] active:scale-[0.97] disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <div className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_center,_rgba(45,212,191,0.2),_transparent_70%)] opacity-0 transition-opacity duration-500 group-hover/btn:opacity-100" />
                    <div className="absolute inset-x-0 top-0 h-[1px] bg-gradient-to-r from-transparent via-white/[0.5] to-transparent mix-blend-overlay" />
                    <div className="flex items-center justify-center gap-2.5">
                      <span className="text-[0.7rem] font-medium tracking-[0.25em] text-white/95">发送</span>
                      <SendIcon className="h-3.5 w-3.5 text-white/80 transition-transform duration-500 group-hover/btn:translate-x-1 group-hover/btn:text-white" aria-hidden="true" />
                    </div>
                  </button>
                </div>
              </div>
            </div>

            <div className="flex w-full items-center gap-3 px-1">
              <div className="h-[1px] flex-1 bg-gradient-to-r from-transparent via-white/[0.08] to-white/[0.12]" />
              <div className="h-1.5 w-1.5 rounded-full bg-teal-400/70 shadow-[0_0_12px_rgba(45,212,191,1)]" />
              <div className="min-w-20 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
                <div
                  role="progressbar"
                  aria-label={`Depth layer ${displayDepthLayer}`}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(Math.max(depthScore * 100, 5))}
                  className="h-1.5 rounded-full transition-all duration-700 ease-out"
                  style={{
                    width: `${Math.max(depthScore * 100, 5)}%`,
                    background:
                      displayDepthLayer === 2
                        ? 'linear-gradient(90deg, rgba(45,212,191,0.65), rgba(129,140,248,0.85))'
                        : 'linear-gradient(90deg, rgba(153,246,228,0.45), rgba(45,212,191,0.85))',
                  }}
                />
              </div>
              <div className="h-[1px] flex-1 bg-gradient-to-l from-transparent via-white/[0.08] to-white/[0.12]" />
              {closingHint && (
                <span className="hidden shrink-0 animate-pulse text-xs tracking-[0.16em] text-white/40 sm:inline">
                  对话即将进入总结阶段
                </span>
              )}
            </div>
          </div>
        )}
      </footer>
    </div>
  );
}
