import { useEffect, useState } from 'react';

import type { FollowupQuestionsEvent, FollowupResponseItem } from '../../api/client';

interface FollowupCardProps {
  offer: FollowupQuestionsEvent;
  submitting: boolean;
  onSubmit: (responses: FollowupResponseItem[]) => void;
  onSkip: () => void;
}

export default function FollowupCard({ offer, submitting, onSubmit, onSkip }: FollowupCardProps) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [remaining, setRemaining] = useState<number | null>(offer.timeout_seconds);
  const hasCountdown = remaining !== null;

  // The parent remounts this card per gate (key={offer.followup_id}), so answers
  // and countdown state initialise fresh on mount; no-timeout gates do not tick.
  useEffect(() => {
    if (offer.timeout_seconds === null) {
      return undefined;
    }
    const timer = setInterval(() => {
      setRemaining((value) => (value !== null && value > 0 ? value - 1 : 0));
    }, 1000);
    return () => clearInterval(timer);
  }, [offer.timeout_seconds]);

  const responses: FollowupResponseItem[] = offer.questions
    .map((question) => ({
      question_id: question.question_id,
      answer: (answers[question.question_id] ?? '').trim(),
    }))
    .filter((response) => response.answer.length > 0);

  const submitDisabled = submitting || responses.length === 0;

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-[#000205]/70 px-4 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-[14px] border border-white/15 bg-[#05080e]/90 p-6 text-white shadow-[0_20px_60px_rgba(0,0,0,0.6)] backdrop-blur">
        <div className="inline-flex items-center rounded-full border border-cyan-200/30 bg-cyan-300/10 px-3 py-1 text-xs font-medium tracking-[0.12em] text-cyan-100">
          议会想先问你
        </div>
        <p className="mt-4 text-sm leading-6 text-white/75">{offer.lead_in}</p>

        <div className="mt-4 space-y-4">
          {offer.questions.map((question) => (
            <div key={question.question_id}>
              <label
                htmlFor={`followup-${question.question_id}`}
                className="block text-sm font-medium text-white/85"
              >
                {question.text}
              </label>
              <textarea
                id={`followup-${question.question_id}`}
                value={answers[question.question_id] ?? ''}
                onChange={(event) =>
                  setAnswers((prev) => ({ ...prev, [question.question_id]: event.target.value }))
                }
                disabled={submitting}
                rows={2}
                className="mt-1.5 w-full resize-none rounded-[8px] border border-white/15 bg-white/[0.04] px-3 py-2 text-sm leading-5 text-white/90 outline-none transition placeholder:text-white/30 focus:border-cyan-300/50 focus:ring-2 focus:ring-cyan-300/20 disabled:opacity-50"
              />
            </div>
          ))}
        </div>

        <p className="mt-3 text-[11px] font-medium text-white/45">
          {hasCountdown
            ? remaining > 0
              ? `${remaining} 秒后自动继续辩论`
              : '正在继续辩论…'
            : '等待你的回答；也可以跳过继续辩论。'}
        </p>

        <div className="mt-5 flex flex-col gap-3 sm:flex-row">
          <button
            type="button"
            onClick={onSkip}
            disabled={submitting}
            className="flex-1 rounded-[8px] border border-white/20 bg-white/[0.04] px-4 py-3 text-sm font-medium text-white/75 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
          >
            跳过，继续辩论
          </button>
          <button
            type="button"
            onClick={() => onSubmit(responses)}
            disabled={submitDisabled}
            className="flex-1 rounded-[8px] border border-cyan-200/30 bg-cyan-300/15 px-4 py-3 text-sm font-medium text-cyan-50 transition hover:bg-cyan-300/25 disabled:cursor-not-allowed disabled:opacity-60"
          >
            提交回答
          </button>
        </div>
      </div>
    </div>
  );
}
