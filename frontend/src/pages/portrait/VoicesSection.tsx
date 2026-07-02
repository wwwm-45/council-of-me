import { useEffect, useState, type FocusEvent } from 'react';

import type { InnerVoice, Tension } from './types';

interface VoicesSectionProps {
  voices: InnerVoice[];
  tensions: Tension[];
  onVoicesChange: (voices: InnerVoice[]) => Promise<void> | void;
}

const intensityWidth = (value: number) => `${Math.min(Math.max(value, 0), 1) * 100}%`;
const EMPTY_VOICE: InnerVoice = { name: '', core_concern: '', protective_intent: '', intensity: 0.5 };

function normalizeVoice(voice: InnerVoice): InnerVoice {
  return {
    name: voice.name.trim(),
    core_concern: voice.core_concern.trim(),
    protective_intent: voice.protective_intent.trim(),
    intensity: voice.intensity,
  };
}

function voicesEqual(left: InnerVoice[], right: InnerVoice[]) {
  if (left.length !== right.length) {
    return false;
  }

  return left.every((voice, index) => {
    const other = right[index];
    return (
      voice.name === other.name &&
      voice.core_concern === other.core_concern &&
      voice.protective_intent === other.protective_intent &&
      voice.intensity === other.intensity
    );
  });
}

function hasMeaningfulVoiceContent(voice: InnerVoice | null): voice is InnerVoice {
  if (!voice) {
    return false;
  }

  const normalized = normalizeVoice(voice);
  return Boolean(normalized.name || normalized.core_concern || normalized.protective_intent);
}

export default function VoicesSection({ voices, tensions, onVoicesChange }: VoicesSectionProps) {
  const [draftVoices, setDraftVoices] = useState<InnerVoice[]>(voices);
  const [pendingVoice, setPendingVoice] = useState<InnerVoice | null>(null);

  useEffect(() => {
    setDraftVoices(voices);
  }, [voices]);

  async function persist(nextVoices: InnerVoice[]) {
    const normalized = nextVoices.map(normalizeVoice);
    setDraftVoices(normalized);

    if (voicesEqual(normalized, voices.map(normalizeVoice))) {
      return;
    }

    await onVoicesChange(normalized);
  }

  function updateVoiceAt(index: number, patch: Partial<InnerVoice>) {
    setDraftVoices((current) =>
      current.map((voice, voiceIndex) => (voiceIndex === index ? { ...voice, ...patch } : voice)),
    );
  }

  async function handlePersistedBlur(event: FocusEvent<HTMLElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }

    await persist(draftVoices);
  }

  function updatePendingVoice(patch: Partial<InnerVoice>) {
    setPendingVoice((current) => ({ ...(current ?? EMPTY_VOICE), ...patch }));
  }

  async function handlePendingBlur(event: FocusEvent<HTMLElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }

    if (!hasMeaningfulVoiceContent(pendingVoice)) {
      setPendingVoice(null);
      return;
    }

    const nextVoices = [...draftVoices, normalizeVoice(pendingVoice)];
    setPendingVoice(null);
    await persist(nextVoices);
  }

  return (
    <section className="rounded-[2rem] border border-slate-200/70 bg-white/90 p-6 shadow-[0_18px_50px_-30px_rgba(15,23,42,0.45)] backdrop-blur">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-[11px] uppercase tracking-[0.24em] text-slate-400">内在声音</p>
          <p className="mt-2 text-sm text-slate-500">这些声音来自第一阶段对话的提炼，你可以改名、补充关切或调整保护意图。</p>
        </div>
        <button
          type="button"
          aria-label="add voice"
          onClick={() => {
            if (!pendingVoice) {
              setPendingVoice({ ...EMPTY_VOICE });
            }
          }}
          className="rounded-full border border-dashed border-slate-300 px-4 py-2 text-sm text-slate-500 transition hover:border-slate-400 hover:text-slate-700"
        >
          添加声音
        </button>
      </div>

      <div className="mt-5 space-y-4">
        {draftVoices.map((voice, index) => (
          <article
            key={`persisted-${index}`}
            onBlur={handlePersistedBlur}
            className="rounded-3xl border border-slate-200/70 bg-slate-50/70 p-4"
          >
            <div className="grid gap-3 md:grid-cols-[1fr_1.4fr_1.4fr_auto]">
              <input
                value={voice.name}
                onChange={(event) => updateVoiceAt(index, { name: event.target.value })}
                placeholder="声音名称"
                className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-300 focus:outline-none"
              />
              <input
                value={voice.core_concern}
                onChange={(event) => updateVoiceAt(index, { core_concern: event.target.value })}
                placeholder="它最在意什么"
                className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-300 focus:outline-none"
              />
              <input
                value={voice.protective_intent}
                onChange={(event) => updateVoiceAt(index, { protective_intent: event.target.value })}
                placeholder="它想保护什么"
                className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-300 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => persist(draftVoices.filter((_, voiceIndex) => voiceIndex !== index))}
                className="rounded-full px-3 py-2 text-sm text-slate-400 transition hover:bg-white hover:text-rose-500"
                aria-label={`删除声音 ${voice.name || index + 1}`}
              >
                删除
              </button>
            </div>

            <div className="mt-4">
              <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
                <span>表达强度</span>
                <span>{Math.round(voice.intensity * 100)}%</span>
              </div>
              <div className="h-2 rounded-full bg-slate-200">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-sky-400 to-indigo-500"
                  style={{ width: intensityWidth(voice.intensity) }}
                />
              </div>
            </div>
          </article>
        ))}

        {pendingVoice ? (
          <article
            key="pending-voice"
            onBlur={handlePendingBlur}
            className="rounded-3xl border border-dashed border-slate-300 bg-white/80 p-4"
          >
            <div className="grid gap-3 md:grid-cols-[1fr_1.4fr_1.4fr_auto]">
              <input
                value={pendingVoice.name}
                onChange={(event) => updatePendingVoice({ name: event.target.value })}
                placeholder="声音名称"
                className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-300 focus:outline-none"
              />
              <input
                value={pendingVoice.core_concern}
                onChange={(event) => updatePendingVoice({ core_concern: event.target.value })}
                placeholder="它最在意什么"
                className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-300 focus:outline-none"
              />
              <input
                value={pendingVoice.protective_intent}
                onChange={(event) => updatePendingVoice({ protective_intent: event.target.value })}
                placeholder="它想保护什么"
                className="rounded-2xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 focus:border-slate-300 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => setPendingVoice(null)}
                className="rounded-full px-3 py-2 text-sm text-slate-400 transition hover:bg-white hover:text-rose-500"
                aria-label="discard new voice"
              >
                取消
              </button>
            </div>

            <div className="mt-4">
              <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
                <span>表达强度</span>
                <span>{Math.round(pendingVoice.intensity * 100)}%</span>
              </div>
              <div className="h-2 rounded-full bg-slate-200">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-sky-300 to-indigo-400"
                  style={{ width: intensityWidth(pendingVoice.intensity) }}
                />
              </div>
            </div>
          </article>
        ) : null}
      </div>

      {tensions.length > 0 ? (
        <div className="mt-6 space-y-3">
          <p className="text-[11px] uppercase tracking-[0.2em] text-slate-400">核心张力</p>
          {tensions.map((tension, index) => (
            <div key={`${tension.pole_a}-${tension.pole_b}-${index}`} className="rounded-2xl border border-slate-200/70 bg-white px-4 py-3">
              <div className="flex items-center justify-between gap-4 text-sm font-medium text-slate-600">
                <span>{tension.pole_a || '一端'}</span>
                <span>{tension.pole_b || '另一端'}</span>
              </div>
              <div className="mt-2 h-2 rounded-full bg-gradient-to-r from-sky-200 via-amber-200 to-rose-200" />
              <p className="mt-2 text-xs leading-5 text-slate-500">{tension.user_evidence}</p>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
