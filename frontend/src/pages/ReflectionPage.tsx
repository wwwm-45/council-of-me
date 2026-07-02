import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  getReflectionPrompts,
  postReflection,
  completeReflection,
  type ReflectionPromptsResponse,
  type QuestionResponse,
} from '../api/client';
import { getSession as getStore, setSession } from '../store/session';

export default function ReflectionPage() {
  const nav = useNavigate();
  const sid = getStore().sessionId;
  const [prompts, setPrompts] = useState<ReflectionPromptsResponse | null>(null);
  const [currentLevel, setCurrentLevel] = useState<'r1' | 'r2' | 'r3' | 'r4'>('r1');
  const [answers, setAnswers] = useState<Record<string, string>>({ r1: '', r2: '', r3: '', r4: '' });
  const [loading, setLoading] = useState(true);
  const [submitted, setSubmitted] = useState<Set<string>>(new Set());

  const levels: ('r1' | 'r2' | 'r3' | 'r4')[] = ['r1', 'r2', 'r3', 'r4'];
  const levelLabels: Record<string, string> = { r1: 'R1 描述', r2: 'R2 对话', r3: 'R3 转化', r4: 'R4 批判' };

  useEffect(() => {
    if (!sid) { nav('/'); return; }
    getReflectionPrompts(sid).then(setPrompts).catch(console.error).finally(() => setLoading(false));
  }, [nav, sid]);

  async function handleSubmit(level: 'r1' | 'r2' | 'r3' | 'r4') {
    if (!answers[level]?.trim()) return;
    try {
      const responses: QuestionResponse[] = [{
        question_id: `${level}-legacy`,
        input_type: 'text',
        value: answers[level],
      }];
      await postReflection(sid!, level.toUpperCase(), responses);
      setSubmitted((s) => new Set([...s, level]));
      const idx = levels.indexOf(level);
      if (idx < levels.length - 1) setCurrentLevel(levels[idx + 1]);
    } catch (e) { console.error(e); }
  }

  async function handleComplete() {
    setLoading(true);
    try {
      await completeReflection(sid!);
      setSession({ status: 'closing' });
      nav('/closure');
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }

  if (loading) return <div className="flex items-center justify-center min-h-[50vh] text-slate-400">加载中...</div>;

  if (!prompts) return <div className="flex items-center justify-center min-h-[50vh] text-slate-400">Loading...</div>;

  const currentPrompts = prompts[currentLevel] || [];

  return (
    <div className="max-w-3xl mx-auto px-4 py-6 sm:py-8 animate-fade-in">
      <h2 className="text-xl font-bold text-slate-800 mb-4">深度反思</h2>

      {/* Level tabs */}
      <div className="flex gap-2 mb-6">
        {levels.map((l) => (
          <button key={l} onClick={() => setCurrentLevel(l)}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition ${
              currentLevel === l ? 'bg-blue-600 text-white' : submitted.has(l) ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}>
            {levelLabels[l]}
            {submitted.has(l) && ' ✓'}
          </button>
        ))}
      </div>

      {/* Current prompt */}
      <div className="bg-white rounded-2xl shadow-sm border border-slate-100 p-6 mb-6">
        {currentPrompts.map((p, i) => (
          <div key={i} className="mb-4">
            <p className="text-sm font-semibold text-slate-800 mb-1">{p.question}</p>
            <p className="text-xs text-slate-400">{p.hint}</p>
          </div>
        ))}
        <textarea
          value={answers[currentLevel]}
          onChange={(e) => setAnswers({ ...answers, [currentLevel]: e.target.value })}
          placeholder="写下你的反思..."
          rows={4}
          className="w-full rounded-xl border border-slate-300 px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 resize-none"
        />
        <button onClick={() => handleSubmit(currentLevel)} disabled={!answers[currentLevel]?.trim() || submitted.has(currentLevel)}
          className="mt-3 w-full py-2.5 rounded-xl bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50 transition text-sm">
          {submitted.has(currentLevel) ? '已提交' : '提交反思'}
        </button>
      </div>

      <button onClick={handleComplete}
        className="w-full py-3 rounded-xl bg-slate-800 text-white font-medium hover:bg-slate-900 transition">
        完成反思，进入收束
      </button>
    </div>
  );
}
