import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import CrisisModal from '../components/CrisisModal';
import {
  debateEarlyTerminationDecision,
  debateFollowupResponse,
  debatePause,
  debateResume,
  debugSkipDebate,
  streamDebateRound,
  type AgentEvolutionData,
  type AgentOrderEntry,
  type ConvergenceHighEvent,
  type DebatePhaseStatus,
  type EarlyTerminationDecision,
  type FollowupQuestionsEvent,
  type FollowupResponseItem,
} from '../api/client';
import { getSession as getStore, setSession } from '../store/session';
import { readStageCache, writeStageCache } from '../store/stageCache';
import RoundTable from './debate/RoundTable';
import FollowupCard from './debate/FollowupCard';
import {
  getCompletedSubtitleSentence,
  getCompletedSubtitleSentences,
  getSubtitleDisplayDuration,
  getSubtitleTailOnComplete,
} from './debate/subtitle';
import type { ArtifactEvent, ChatMsg, ConvergenceMapData } from './debate/types';

const MAX_PENDING_SUBTITLE_CUES = 2;

/** A subtitle line tagged with the voice that produced it, so a cue can keep
 *  showing on the right seat even after that voice has stopped speaking. */
interface SubtitleCue {
  agentId: string;
  agentName: string;
  content: string;
}

function getPhaseStatusText(status: DebatePhaseStatus | null): string | null {
  if (!status) {
    return null;
  }

  if (status.type === 'phase_evaluating') {
    return '正在评估本轮';
  }
  if (status.type === 'artifact_start') {
    return '正在生成本轮小结';
  }
  return '本轮小结已更新';
}

interface DebatePageCache {
  messages: ChatMsg[];
  roundMeta: Array<[number, string]>;
  agentOrder: AgentOrderEntry[];
  currentRound: number;
  currentPhase: string;
  started: boolean;
  awaitingNext: boolean;
  done: boolean;
  paused: boolean;
  exchangeProgress: { seq: number; min: number; max: number } | null;
  expectedExchanges: Array<[number, [number, number]]>;
  roundArtifacts: Array<[number, ArtifactEvent]>;
  agentEvolutions: Array<[string, AgentEvolutionData[]]>;
  convergenceMap: ConvergenceMapData | null;
  phaseStatus: DebatePhaseStatus | null;
  earlyTerminationOffer: ConvergenceHighEvent | null;
  skippedR4Phases: string[];
}

type CrisisResource = {
  name: string;
  phone: string;
  description: string;
};

type RequestFailure = {
  crisis?: boolean;
  resources?: CrisisResource[];
  detail?: unknown;
};

function getRequestFailure(error: unknown): RequestFailure {
  if (typeof error !== 'object' || error === null) {
    return {};
  }
  return error as RequestFailure;
}

export default function DebatePage() {
  const nav = useNavigate();
  const store = getStore();
  const sid = store.sessionId;
  const userDisplayName = store.userDisplayName;
  const cached = readStageCache<DebatePageCache>(sid, 'debate');

  const [messages, setMessages] = useState<ChatMsg[]>(() => (
    (cached?.messages ?? []).map((message) => ({ ...message, streaming: false }))
  ));
  const [roundMeta, setRoundMeta] = useState<Map<number, string>>(() => new Map(cached?.roundMeta ?? []));
  const [agentOrder, setAgentOrder] = useState<AgentOrderEntry[]>(() => cached?.agentOrder ?? []);
  const [currentRound, setCurrentRound] = useState(() => cached?.currentRound ?? 0);
  const [currentPhase, setCurrentPhase] = useState(() => cached?.currentPhase ?? '');
  const [streaming, setStreaming] = useState(false);
  const [debugSkipping, setDebugSkipping] = useState(false);
  const [awaitingNext, setAwaitingNext] = useState(() => cached?.awaitingNext ?? false);
  const [done, setDone] = useState(() => cached?.done ?? false);
  const [started, setStarted] = useState(() => cached?.started ?? false);
  const [paused, setPaused] = useState(() => cached?.paused ?? false);
  const [exchangeProgress, setExchangeProgress] = useState<{ seq: number; min: number; max: number } | null>(() => cached?.exchangeProgress ?? null);
  const [expectedExchanges, setExpectedExchanges] = useState<Map<number, [number, number]>>(() => new Map(cached?.expectedExchanges ?? []));

  const [speakingAgentId, setSpeakingAgentId] = useState<string | null>(null);
  const [subtitleAnchor, setSubtitleAnchor] = useState<{ agentId: string; agentName: string } | null>(null);
  const [liveSubtitleContent, setLiveSubtitleContent] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);

  const [roundArtifacts, setRoundArtifacts] = useState<Map<number, ArtifactEvent>>(() => new Map(cached?.roundArtifacts ?? []));
  const [agentEvolutions, setAgentEvolutions] = useState<Map<string, AgentEvolutionData[]>>(() => new Map(cached?.agentEvolutions ?? []));
  const [convergenceMap, setConvergenceMap] = useState<ConvergenceMapData | null>(() => cached?.convergenceMap ?? null);
  const [phaseStatus, setPhaseStatus] = useState<DebatePhaseStatus | null>(() => cached?.phaseStatus ?? null);
  const [earlyTerminationOffer, setEarlyTerminationOffer] = useState<ConvergenceHighEvent | null>(() => cached?.earlyTerminationOffer ?? null);
  const [earlyTerminationSubmitting, setEarlyTerminationSubmitting] = useState(false);
  const [followupOffer, setFollowupOffer] = useState<FollowupQuestionsEvent | null>(null);
  const [followupSubmitting, setFollowupSubmitting] = useState(false);
  // True while the backend is composing this round's follow-up question (a slow
  // LLM call that lands between round_end and followup_questions). Keeps the
  // next-round button suppressed so a click can't race the pending question.
  const [followupPreparing, setFollowupPreparing] = useState(false);
  const [skippedR4Phases, setSkippedR4Phases] = useState<string[]>(() => cached?.skippedR4Phases ?? []);

  const [error, setError] = useState<string | null>(null);
  const [crisis, setCrisis] = useState<{ resources: CrisisResource[] } | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const currentRoundRef = useRef(cached?.currentRound ?? 0);
  const streamMsgIdRef = useRef<string | null>(null);
  const closingActRef = useRef(false);
  const pausedRef = useRef(cached?.paused ?? false);
  const contentBufferRef = useRef('');
  const lastSubtitleRef = useRef('');
  const subtitleQueueRef = useRef<SubtitleCue[]>([]);
  const subtitleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const processedSentenceCountRef = useRef(0);
  const agentEndedRef = useRef(false);
  // The voice currently feeding the subtitle buffer; captured per cue so a
  // line stays attributed to its speaker after the next voice has started.
  const currentSpeakerRef = useRef<{ agentId: string; agentName: string } | null>(null);

  function clearSubtitleTimer() {
    if (subtitleTimerRef.current) {
      clearTimeout(subtitleTimerRef.current);
      subtitleTimerRef.current = null;
    }
  }

  function displaySubtitleCue(cue: SubtitleCue) {
    lastSubtitleRef.current = cue.content;
    setLiveSubtitleContent(cue.content);
    // The anchor moves with the displayed cue (not the live speaker), so a
    // lingering final line keeps sitting on the seat of whoever said it.
    setSubtitleAnchor({ agentId: cue.agentId, agentName: cue.agentName });
    clearSubtitleTimer();
    subtitleTimerRef.current = setTimeout(() => {
      subtitleTimerRef.current = null;
      if (pausedRef.current) {
        return;
      }
      playQueuedSubtitleCue();
    }, getSubtitleDisplayDuration(cue.content));
  }

  function playQueuedSubtitleCue() {
    if (pausedRef.current || subtitleTimerRef.current) {
      return;
    }

    const nextCue = subtitleQueueRef.current.shift();
    if (!nextCue) {
      return;
    }

    displaySubtitleCue(nextCue);
  }

  function enqueueSubtitleCue(content: string) {
    const speaker = currentSpeakerRef.current;
    if (
      !speaker
      || !content
      || content === lastSubtitleRef.current
      || subtitleQueueRef.current.at(-1)?.content === content
    ) {
      return;
    }

    // Keep at most two unseen cues so the bubble stays close to the live transcript.
    subtitleQueueRef.current.push({ ...speaker, content });
    if (subtitleQueueRef.current.length > MAX_PENDING_SUBTITLE_CUES) {
      subtitleQueueRef.current = subtitleQueueRef.current.slice(-MAX_PENDING_SUBTITLE_CUES);
    }

    playQueuedSubtitleCue();
  }

  function syncCompletedSubtitleQueue() {
    const completedSentences = getCompletedSubtitleSentences(contentBufferRef.current);
    if (completedSentences.length <= processedSentenceCountRef.current) {
      return;
    }

    const newSentences = completedSentences.slice(processedSentenceCountRef.current);
    processedSentenceCountRef.current = completedSentences.length;
    newSentences.forEach(enqueueSubtitleCue);
  }

  function flushSubtitleTail() {
    const tail = getSubtitleTailOnComplete(contentBufferRef.current);
    if (!tail) {
      return;
    }
    enqueueSubtitleCue(tail);
  }

  /** Full reset for round boundaries — clears the visible bubble too. */
  function resetSubtitlePlayback() {
    clearSubtitleTimer();
    subtitleQueueRef.current = [];
    processedSentenceCountRef.current = 0;
    agentEndedRef.current = false;
    contentBufferRef.current = '';
    lastSubtitleRef.current = '';
    currentSpeakerRef.current = null;
    setLiveSubtitleContent('');
    setSubtitleAnchor(null);
  }

  /**
   * Hand the subtitle buffer to a new speaker without wiping the line already
   * on screen. The outgoing voice's final cue (and its running display timer)
   * stays visible until this speaker produces a cue of their own, so the last
   * sentence always gets its full reading time instead of being cut to <1s when
   * the next voice starts — the failure mode that was most visible in R4.
   */
  function prepareSpeakerSubtitle(agentId: string, agentName: string) {
    subtitleQueueRef.current = [];
    processedSentenceCountRef.current = 0;
    agentEndedRef.current = false;
    contentBufferRef.current = '';
    currentSpeakerRef.current = { agentId, agentName };
    // Intentionally leave the timer, lastSubtitleRef, liveSubtitleContent and
    // anchor untouched so the previous cue finishes its dwell time.
  }

  useEffect(() => {
    if (!sid) return;
    writeStageCache<DebatePageCache>(sid, 'debate', {
      messages,
      roundMeta: Array.from(roundMeta.entries()),
      agentOrder,
      currentRound,
      currentPhase,
      started,
      awaitingNext,
      done,
      paused,
      exchangeProgress,
      expectedExchanges: Array.from(expectedExchanges.entries()),
      roundArtifacts: Array.from(roundArtifacts.entries()),
      agentEvolutions: Array.from(agentEvolutions.entries()),
      convergenceMap,
      phaseStatus,
      earlyTerminationOffer,
      skippedR4Phases,
    });
  }, [
    agentEvolutions,
    agentOrder,
    awaitingNext,
    convergenceMap,
    currentPhase,
    currentRound,
    done,
    earlyTerminationOffer,
    exchangeProgress,
    expectedExchanges,
    messages,
    paused,
    phaseStatus,
    roundArtifacts,
    roundMeta,
    sid,
    skippedR4Phases,
    started,
  ]);

  function resolveSubtitleOnResume() {
    const completedSentence = getCompletedSubtitleSentence(contentBufferRef.current);
    const tail = getSubtitleTailOnComplete(contentBufferRef.current);

    if (agentEndedRef.current && tail) {
      return tail;
    }

    return completedSentence || tail;
  }

  function syncSubtitleOnResume() {
    const resolvedCue = resolveSubtitleOnResume();
    clearSubtitleTimer();
    subtitleQueueRef.current = [];
    processedSentenceCountRef.current = getCompletedSubtitleSentences(contentBufferRef.current).length;

    const speaker = currentSpeakerRef.current;
    if (!resolvedCue || !speaker) {
      return;
    }

    displaySubtitleCue({ ...speaker, content: resolvedCue });
  }

  function doStream() {
    if (!sid || streaming) return;

    if (!started) {
      setStarted(true);
    }

    setError(null);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setStreaming(true);
    setAwaitingNext(false);
    setFollowupPreparing(false);

    void streamDebateRound(
      sid,
      {
        onRoundStart(round, phase, order, expected) {
          // A chained phase (e.g. R3.5 after round-3 deepen) arrives in the
          // same stream after a round_end — re-hide the next-round button.
          setStreaming(true);
          setAwaitingNext(false);
          setFollowupPreparing(false);
          setCurrentRound(round);
          currentRoundRef.current = round;
          setCurrentPhase(phase);
          setPhaseStatus(null);
          setRoundMeta((prev) => new Map(prev).set(round, phase));
          closingActRef.current = false;
          setAgentOrder(order);
          setExchangeProgress(null);
          setSpeakingAgentId(null);
          setSubtitleAnchor(null);
          resetSubtitlePlayback();
          if (expected) {
            setExpectedExchanges((prev) => new Map(prev).set(round, expected));
          }
        },
        onAgentStart(agentId, agentName, round, agentReplyTo) {
          const id = `${agentId}-${round}-${Date.now()}`;
          streamMsgIdRef.current = id;
          setSpeakingAgentId(agentId);
          setPhaseStatus(null);
          prepareSpeakerSubtitle(agentId, agentName);
          setReplyTo(agentReplyTo ?? null);
          setMessages((prev) => [
            ...prev,
            {
              id,
              agentId,
              agentName,
              round,
              content: '',
              streaming: true,
              replyTo: agentReplyTo,
              closingAct: closingActRef.current,
            },
          ]);
        },
        onAgentToken(_agentId, content) {
          const id = streamMsgIdRef.current;
          if (!id) return;

          setMessages((prev) =>
            prev.map((message) => (
              message.id === id
                ? { ...message, content: message.content + content }
                : message
            )),
          );

          contentBufferRef.current += content;

          if (pausedRef.current) return;

          syncCompletedSubtitleQueue();
        },
        onAgentEnd() {
          const id = streamMsgIdRef.current;
          streamMsgIdRef.current = null;
          setSpeakingAgentId(null);
          setReplyTo(null);
          agentEndedRef.current = true;

          if (!pausedRef.current) {
            syncCompletedSubtitleQueue();
            flushSubtitleTail();
          }

          if (!id) return;

          setMessages((prev) =>
            prev.map((message) => (
              message.id === id
                ? { ...message, streaming: false }
                : message
            )),
          );
        },
        onExchangeMeta(seq, totalMin, totalMax) {
          setExchangeProgress({ seq, min: totalMin, max: totalMax });
        },
        onRoundArtifact(artifactType, data) {
          const artifactRound = currentRoundRef.current || currentRound;
          setRoundArtifacts((prev) => {
            const next = new Map(prev);
            next.set(artifactRound, { type: artifactType as ArtifactEvent['type'], data });
            return next;
          });
        },
        onAgentEvolution(evolutions) {
          setAgentEvolutions((prev) => {
            const next = new Map(prev);
            for (const evolution of evolutions) {
              const history = next.get(evolution.agent_id) ?? [];
              next.set(evolution.agent_id, [...history, evolution]);
            }
            return next;
          });
        },
        onPhaseStatus(status) {
          setPhaseStatus(status);
        },
        onR4SubPhase(subPhase, data) {
          setCurrentPhase(subPhase);
          closingActRef.current = subPhase === 'r4_final';

          if (subPhase === 'r4_mapping') {
            setConvergenceMap(data.convergence_map as unknown as ConvergenceMapData);
            setRoundArtifacts((prev) => {
              const next = new Map(prev);
              next.set(4, {
                type: 'convergence_map',
                data: data.convergence_map as Record<string, unknown>,
              });
              return next;
            });
            return;
          }

          if (
            (subPhase === 'r4_reflection' || subPhase === 'r4_final')
            && data.streamed !== true
          ) {
            const id = `${data.agent_id}-${data.round_number}-${Date.now()}`;
            setMessages((prev) => [
              ...prev,
              {
                id,
                agentId: data.agent_id as string,
                agentName: data.agent_name as string,
                round: data.round_number as number,
                content: data.content as string,
                streaming: false,
                closingAct: closingActRef.current,
              },
            ]);
          }
        },
        onRoundEnd() {
          setAwaitingNext(true);
          setPhaseStatus(null);
          setExchangeProgress(null);
          setStreaming(false);
        },
        onConvergenceHigh(payload) {
          setEarlyTerminationOffer(payload);
          setEarlyTerminationSubmitting(false);
        },
        onFollowupPreparing() {
          setFollowupPreparing(true);
        },
        onFollowupQuestions(payload) {
          setFollowupPreparing(false);
          setFollowupOffer(payload);
          setFollowupSubmitting(false);
        },
        onFollowupSkipped() {
          setFollowupPreparing(false);
        },
        onFollowupResolved() {
          setFollowupOffer(null);
          setFollowupSubmitting(false);
        },
        onRoundSkip({ phase, reason }) {
          if (reason !== 'early_termination') {
            return;
          }
          setEarlyTerminationOffer(null);
          setAwaitingNext(false);
          setFollowupPreparing(false);
          setSkippedR4Phases((prev) => (
            prev.includes(phase) ? prev : [...prev, phase]
          ));
        },
        onDebateComplete() {
          setDone(true);
          setAwaitingNext(false);
          setStreaming(false);
          setPhaseStatus(null);
          setEarlyTerminationOffer(null);
          setEarlyTerminationSubmitting(false);
          setFollowupOffer(null);
          setFollowupSubmitting(false);
          setFollowupPreparing(false);
        },
        onError(message) {
          console.error('SSE error:', message);
          setError(message);
          setStreaming(false);
          setPhaseStatus(null);
          setEarlyTerminationSubmitting(false);
          setFollowupOffer(null);
          setFollowupSubmitting(false);
          setFollowupPreparing(false);
        },
      },
      ctrl.signal,
    );
  }

  function scheduleStreamStart() {
    setTimeout(() => {
      doStream();
    }, 0);
  }

  useEffect(() => {
    if (!sid) {
      nav('/');
      return;
    }

    return () => {
      abortRef.current?.abort();
      clearSubtitleTimer();
    };
  }, [nav, sid]);

  async function handlePause() {
    pausedRef.current = true;
    clearSubtitleTimer();
    setPaused(true);
    await debatePause(sid!);
  }

  async function handleResume() {
    pausedRef.current = false;
    setPaused(false);
    syncSubtitleOnResume();
    await debateResume(sid!);
  }

  async function handleDebugSkip() {
    if (!sid) return;

    setDebugSkipping(true);
    setError(null);

    try {
      await debugSkipDebate(sid);
      setSession({ status: 'synthesizing' });
      nav('/synthesis');
    } catch (error: unknown) {
      const detail = getRequestFailure(error).detail;
      if (typeof detail === 'string') {
        setError(detail);
      } else if (detail) {
        setError(JSON.stringify(detail));
      } else {
        setError('调试跳过失败');
      }
    } finally {
      setDebugSkipping(false);
    }
  }

  async function handleEarlyTerminationDecision(decision: EarlyTerminationDecision) {
    if (!sid || !earlyTerminationOffer || earlyTerminationSubmitting) {
      return;
    }

    setEarlyTerminationSubmitting(true);
    setError(null);

    try {
      const result = await debateEarlyTerminationDecision(sid, decision);
      setEarlyTerminationOffer(null);
      setAwaitingNext(result.decision === 'continue');
    } catch (error: unknown) {
      const detail = getRequestFailure(error).detail;
      if (typeof detail === 'string') {
        setError(detail);
      } else if (detail) {
        setError(JSON.stringify(detail));
      } else {
        setError('提前结束决策提交失败');
      }
    } finally {
      setEarlyTerminationSubmitting(false);
    }
  }

  async function handleFollowupSubmit(responses: FollowupResponseItem[]) {
    if (!sid || !followupOffer || followupSubmitting) {
      return;
    }

    setFollowupSubmitting(true);
    setError(null);

    try {
      await debateFollowupResponse(sid, followupOffer.followup_id, responses);
      setFollowupOffer(null);
    } catch (error: unknown) {
      const detail = getRequestFailure(error).detail;
      if (typeof detail === 'string') {
        setError(detail);
      } else if (detail) {
        setError(JSON.stringify(detail));
      } else {
        setError('追问回答提交失败');
      }
    } finally {
      setFollowupSubmitting(false);
    }
  }

  function handleFollowupSkip() {
    void handleFollowupSubmit([]);
  }

  const r4Skipped = skippedR4Phases.length > 0;
  const phaseStatusText = getPhaseStatusText(phaseStatus);
  return (
    <div className="fixed inset-0 z-50 animate-fade-in overflow-hidden bg-[#000205] text-white">
      {crisis && <CrisisModal resources={crisis.resources} onClose={() => setCrisis(null)} />}

      <RoundTable
        agents={agentOrder}
        userDisplayName={userDisplayName}
        speakingAgentId={speakingAgentId}
        currentRound={currentRound}
        currentPhase={currentPhase}
        paused={paused}
        streaming={streaming}
        hasMessages={messages.length > 0}
        latestContent={liveSubtitleContent}
        subtitleAnchorId={subtitleAnchor?.agentId ?? null}
        subtitleAnchorName={subtitleAnchor?.agentName ?? null}
        replyTo={replyTo}
        onUserSeatClick={() => {}}
        onPause={handlePause}
        onResume={handleResume}
        exchangeProgress={exchangeProgress}
        messages={messages}
        roundArtifacts={roundArtifacts}
        convergenceMap={convergenceMap}
        agentEvolutions={agentEvolutions}
      />

      <div className="absolute bottom-2 left-1/2 z-[60] flex w-[min(92vw,620px)] -translate-x-1/2 flex-col items-center">
        {phaseStatusText && (
          <div
            role="status"
            className="mt-3 rounded-full border border-white/15 bg-[#05080e]/75 px-3 py-1.5 text-xs font-medium text-white/70 shadow-lg backdrop-blur"
          >
            {phaseStatusText}
          </div>
        )}

        {followupPreparing && !followupOffer && (
          <div
            role="status"
            className="mt-3 inline-flex items-center gap-2 rounded-full border border-white/15 bg-[#05080e]/75 px-3 py-1.5 text-xs font-medium text-white/70 shadow-lg backdrop-blur"
          >
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-300" />
            正在准备你的问题…
          </div>
        )}

        {r4Skipped && (
          <div className="mt-3 w-full rounded-[8px] border border-amber-200/35 bg-amber-300/10 px-4 py-3 text-sm text-amber-50 backdrop-blur">
            <p className="font-medium">已跳过 R4，正在直接进入综合。</p>
            <p className="mt-1 text-amber-100/75">系统已根据你的选择跳过收束轮次，接下来会直接展示综合结果。</p>
          </div>
        )}

        {error && (
          <div className="mt-3 rounded-[8px] border border-red-300/35 bg-red-500/10 px-4 py-2.5 text-center text-sm text-red-50 backdrop-blur">
            <p>{error}</p>
          </div>
        )}

        <div className="mt-4 flex flex-wrap justify-center gap-2">
          {!started && !error && (
            <button
              onClick={handleDebugSkip}
              disabled={debugSkipping || streaming}
              className="rounded-[4px] border border-dashed border-white/20 bg-[#05080e]/65 px-4 py-2.5 text-sm text-white/55 transition hover:bg-white/10 disabled:opacity-50"
            >
              {debugSkipping ? '载入中...' : '[调试] 跳过辩论'}
            </button>
          )}

          {earlyTerminationOffer ? null : !started || error || streaming ? (
            <button
              onClick={() => {
                setError(null);
                scheduleStreamStart();
              }}
              disabled={streaming || debugSkipping}
              className="rounded-[4px] border border-cyan-200/30 bg-cyan-300/15 px-6 py-2.5 text-sm font-medium text-cyan-50 transition hover:bg-cyan-300/25 disabled:opacity-50"
            >
              {streaming ? '辩论进行中...' : error ? '重试' : '开始辩论'}
            </button>
          ) : done ? (
            <button
              onClick={() => {
                setSession({ status: 'synthesizing' });
                nav('/synthesis');
              }}
              className="rounded-[4px] border border-white/20 bg-white/10 px-6 py-2.5 text-sm font-medium text-white transition hover:bg-white/15"
            >
              进入综合
            </button>
          ) : awaitingNext && !streaming && !followupOffer && !followupPreparing ? (
            <>
              <button
                onClick={scheduleStreamStart}
                className="rounded-[4px] border border-cyan-200/30 bg-cyan-300/15 px-6 py-2.5 text-sm font-medium text-cyan-50 transition hover:bg-cyan-300/25"
              >
                下一轮
              </button>
              <button
                onClick={() => {
                  setSession({ status: 'synthesizing' });
                  nav('/synthesis');
                }}
                className="rounded-[4px] border border-white/20 bg-[#05080e]/65 px-4 py-2.5 text-sm text-white/65 transition hover:bg-white/10"
              >
                跳至综合
              </button>
            </>
          ) : null}
        </div>
      </div>

      {earlyTerminationOffer && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[#000205]/70 px-4 backdrop-blur-sm">
          <div className="w-full max-w-lg rounded-[14px] border border-white/15 bg-[#05080e]/90 p-6 text-white shadow-[0_20px_60px_rgba(0,0,0,0.6)] backdrop-blur">
            <div className="inline-flex items-center rounded-full border border-emerald-200/30 bg-emerald-300/10 px-3 py-1 text-xs font-medium tracking-[0.12em] text-emerald-100">
              R3 收敛提醒
            </div>
            <h2 className="mt-4 text-xl font-semibold text-white">本轮已经明显收敛</h2>
            <p className="mt-3 text-sm leading-6 text-white/70">
              当前收敛度约为 {Math.round(earlyTerminationOffer.convergence_score * 100)}%，预计再花{' '}
              {earlyTerminationOffer.estimated_remaining_minutes ?? 5}
              {' '}分钟可以完成 R4。你可以继续进入 R4，也可以现在结束并进入综合。
            </p>
            <div className="mt-6 flex flex-col gap-3 sm:flex-row">
              <button
                type="button"
                onClick={() => void handleEarlyTerminationDecision('continue')}
                disabled={earlyTerminationSubmitting}
                className="flex-1 rounded-[8px] border border-white/20 bg-white/[0.04] px-4 py-3 text-sm font-medium text-white/75 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
              >
                继续进入 R4
              </button>
              <button
                type="button"
                onClick={() => void handleEarlyTerminationDecision('close')}
                disabled={earlyTerminationSubmitting}
                className="flex-1 rounded-[8px] border border-cyan-200/30 bg-cyan-300/15 px-4 py-3 text-sm font-medium text-cyan-50 transition hover:bg-cyan-300/25 disabled:cursor-not-allowed disabled:opacity-60"
              >
                现在结束并进入综合
              </button>
            </div>
          </div>
        </div>
      )}

      {followupOffer && (
        <FollowupCard
          key={followupOffer.followup_id}
          offer={followupOffer}
          submitting={followupSubmitting}
          onSubmit={(responses) => void handleFollowupSubmit(responses)}
          onSkip={handleFollowupSkip}
        />
      )}
    </div>
  );
}
