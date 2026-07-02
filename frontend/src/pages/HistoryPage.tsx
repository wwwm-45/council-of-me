import { useState, useEffect } from 'react';
import { listSyntheses, getSynthesisDetail, compareSyntheses, detectPatterns } from '../api/client';
import type { SynthesisCard as CardData, SynthesisResponse, ComparisonResult, PatternResult } from '../api/client';
import SynthesisCardComponent from '../components/history/SynthesisCard';
import ComparisonView from '../components/history/ComparisonView';
import PatternInsights from '../components/history/PatternInsights';
import TensionVisualization from '../components/synthesis/TensionVisualization';
import ConsensusAreas from '../components/synthesis/ConsensusAreas';
import ProtectiveIntents from '../components/synthesis/ProtectiveIntents';

// Placeholder user ID (matches backend anonymous session pattern)
const USER_ID = '00000000-0000-0000-0000-000000000000';

export default function HistoryPage() {
  const [cards, setCards] = useState<CardData[]>([]);
  const [patterns, setPatterns] = useState<PatternResult[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SynthesisResponse | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      listSyntheses(USER_ID).catch(() => []),
      detectPatterns(USER_ID).catch(() => []),
    ]).then(([c, p]) => {
      setCards(c);
      setPatterns(p);
    }).finally(() => setLoading(false));
  }, []);

  function handleCardClick(id: string) {
    if (compareMode) {
      setCompareIds((prev) => {
        if (prev.includes(id)) return prev.filter((x) => x !== id);
        if (prev.length >= 2) return [prev[1], id];
        return [...prev, id];
      });
      return;
    }
    setSelectedId(id);
    setDetail(null);
    getSynthesisDetail(USER_ID, id)
      .then((d) => setDetail(d))
      .catch(console.error);
  }

  function handleCompare() {
    if (compareIds.length === 2) {
      compareSyntheses(USER_ID, compareIds[0], compareIds[1])
        .then(setComparison)
        .catch(console.error);
    }
  }

  if (loading) {
    return <div className="flex items-center justify-center min-h-[50vh] text-slate-400">加载中...</div>;
  }

  if (cards.length === 0) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-12 text-center">
        <p className="text-slate-400 text-sm">暂无历史综合记录</p>
        <p className="text-[10px] text-slate-300 mt-2">完成辩论并关闭会话后，综合结果将保存在此处</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 animate-fade-in">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-slate-800">综合历史</h2>
        <button
          onClick={() => { setCompareMode(!compareMode); setCompareIds([]); setComparison(null); }}
          className={`text-xs px-3 py-1.5 rounded-lg transition ${
            compareMode ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
          }`}
        >
          {compareMode ? '退出对比' : '对比模式'}
        </button>
      </div>

      {/* Patterns */}
      <PatternInsights patterns={patterns} />

      <div className="flex gap-6">
        {/* Left: Timeline */}
        <div className="w-64 flex-shrink-0 space-y-2">
          {cards.map((c) => (
            <SynthesisCardComponent
              key={c.synthesis_id}
              card={c}
              selected={compareMode ? compareIds.includes(c.synthesis_id) : selectedId === c.synthesis_id}
              onClick={() => handleCardClick(c.synthesis_id)}
            />
          ))}
        </div>

        {/* Right: Detail or Comparison */}
        <div className="flex-1 min-w-0">
          {compareMode ? (
            <>
              {compareIds.length < 2 && (
                <p className="text-sm text-slate-400 text-center py-8">选择两个综合记录进行对比</p>
              )}
              {compareIds.length === 2 && !comparison && (
                <div className="text-center py-8">
                  <button
                    onClick={handleCompare}
                    className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700"
                  >
                    开始对比
                  </button>
                </div>
              )}
              {comparison && <ComparisonView comparison={comparison} />}
            </>
          ) : detail ? (
            <div className="space-y-4">
              <div className="bg-white rounded-2xl border border-slate-100 shadow-sm p-5">
                <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-line">{detail.narrative}</p>
              </div>
              {detail.core_tensions?.length > 0 && <TensionVisualization tensions={detail.core_tensions} />}
              {detail.consensus_areas?.length > 0 && <ConsensusAreas areas={detail.consensus_areas} />}
              {detail.protective_intents?.length > 0 && <ProtectiveIntents intents={detail.protective_intents} />}
            </div>
          ) : (
            <p className="text-sm text-slate-400 text-center py-8">选择一个综合记录查看详情</p>
          )}
        </div>
      </div>
    </div>
  );
}
