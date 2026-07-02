import { useState } from 'react';
import { motion } from 'motion/react';
import { useNavigate } from 'react-router-dom';
import { createSession, debugSkip, submitConsent, submitFraming } from '../api/client';
import { setSession, EMPTY_STATE } from '../store/session';
import BlurInText from '../components/welcome/BlurInText';

const DEFAULT_FRAMING = 'inner_parts';

export default function WelcomePage() {
  const nav = useNavigate();
  const [displayName, setDisplayName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [consentOpen, setConsentOpen] = useState(false);

  async function handleStart() {
    const trimmedName = displayName.trim();
    if (!trimmedName) return;
    setError(null);
    setLoading(true);
    try {
      setSession({ ...EMPTY_STATE });
      const { session_id } = await createSession(trimmedName);
      setSession({ sessionId: session_id, status: 'created', userDisplayName: trimmedName });
      await submitConsent(session_id);
      await submitFraming(session_id, DEFAULT_FRAMING);
      setSession({ status: 'eliciting', framingPreference: DEFAULT_FRAMING, userDisplayName: trimmedName });
      nav('/elicitation');
    } catch (e) {
      console.error(e);
      setError('连接失败，请稍后再试');
    } finally {
      setLoading(false);
    }
  }

  async function handleDebugSkip() {
    setError(null);
    setLoading(true);
    try {
      setSession({ ...EMPTY_STATE });
      const result = await debugSkip();
      setSession({
        sessionId: result.session_id,
        status: result.status,
        framingPreference: result.framing_preference,
        conflictProfile: result.conflict_profile,
        debateLevel: result.debate_level,
      });
      nav('/portrait');
    } catch (e) {
      console.error(e);
      setError('连接失败，请稍后再试');
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {/* Foreground content — fades in over the persistent HomeShell scene
          (the background stars/particles are NOT re-mounted on entry). */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.8, ease: 'easeOut' }}
        className="pointer-events-none relative z-10 flex min-h-screen flex-col items-center justify-center px-4 text-slate-100"
      >
        <BlurInText
          text="COUNCIL OF ME"
          splitBy="word"
          className="wordmark text-3xl sm:text-5xl md:text-6xl text-center"
        />
        <BlurInText
          text="探索你内心的多重声音"
          splitBy="char"
          baseDelay={0.6}
          stepDelay={0.05}
          className="mt-5 text-sm sm:text-base text-slate-300/70 tracking-[0.3em] text-center"
        />

        <div className="liquid-glass pointer-events-auto mt-10 w-full max-w-md rounded-2xl p-6 sm:p-8">
          <label className="block text-left text-sm font-medium text-slate-200">
            你的称呼
            <input
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              disabled={loading}
              placeholder="比如：小雨"
              className="mt-2 w-full rounded-xl border border-cyan-400/30 bg-white/5 px-4 py-3 text-sm text-slate-100 placeholder:text-slate-400/60 outline-none transition focus:border-cyan-300/70 focus:shadow-[0_0_12px_rgba(0,229,255,0.3)] disabled:opacity-50"
            />
          </label>

          <button
            onClick={handleStart}
            disabled={loading || !displayName.trim()}
            className="liquid-glass-strong mt-5 flex w-full items-center justify-center gap-2 rounded-xl py-3 text-sm font-medium tracking-[0.2em] text-slate-100 transition hover:shadow-[0_0_20px_rgba(0,229,255,0.35)] disabled:opacity-50"
          >
            {loading ? '进入中…' : '进入议会'}
            {!loading && (
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M7 17L17 7" />
                <path d="M7 7h10v10" />
              </svg>
            )}
          </button>

          {error && (
            <p role="alert" className="mt-3 text-center text-xs text-rose-300/90">
              {error}
            </p>
          )}

          <div className="mt-4 text-center text-[11px] leading-relaxed text-slate-400/70">
            进入即表示同意：你的对话不会用于训练 AI ·{' '}
            <button
              type="button"
              onClick={() => setConsentOpen((v) => !v)}
              className="underline decoration-dotted underline-offset-2 hover:text-slate-200"
            >
              {consentOpen ? '收起' : '了解更多'}
            </button>
            {consentOpen && (
              <span className="mt-2 block text-left text-slate-400/70">
                · 你可以随时暂停、修改或退出<br />
                · 这不是心理治疗，如需专业帮助请联系心理咨询师
              </span>
            )}
          </div>
        </div>
      </motion.div>

      {/* Demoted debug entry */}
      <button
        type="button"
        onClick={handleDebugSkip}
        disabled={loading}
        className="absolute bottom-3 right-3 z-10 text-[10px] text-slate-500/50 hover:text-slate-300/70 disabled:opacity-40"
      >
        [调试] 跳过
      </button>
    </>
  );
}
