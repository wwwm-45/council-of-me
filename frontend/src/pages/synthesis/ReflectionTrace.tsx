import type { FeelingId } from './types';
import InsightInput from './InsightInput';

interface ReflectionTraceItemView {
  node_id: string;
  node_label: string;
  feelings: FeelingId[];
  insight: string | null;
}

interface Props {
  trace: {
    footprint_sentence: string;
    insights: ReflectionTraceItemView[];
    exploration_order: string[];
  };
  onInsightChange: (nodeId: string, insight: string) => void;
  onProceed: () => void;
}

export default function ReflectionTrace({ trace, onInsightChange, onProceed }: Props) {
  return (
    <section className="h-full min-h-0 bg-transparent p-7 text-slate-100">
      <div className="mb-6">
        <p className="text-xs font-medium uppercase tracking-[0.24em] text-indigo-400">轨迹回望</p>
        <h3 className="mt-2 text-2xl font-semibold text-white">收拢你的反思轨迹</h3>
      </div>

      {trace.footprint_sentence ? (
        <p className="mb-6 rounded-2xl border border-indigo-400/15 bg-indigo-500/10 px-4 py-3 text-sm leading-relaxed text-indigo-50">
          {trace.footprint_sentence}
        </p>
      ) : null}

      <div className="space-y-4">
        {trace.insights.map((item) => (
          <InsightInput
            key={item.node_id}
            nodeLabel={item.node_label}
            feelings={item.feelings}
            value={item.insight ?? ''}
            onChange={(next) => onInsightChange(item.node_id, next)}
          />
        ))}
      </div>

      <div className="mt-6 flex flex-col gap-3 border-t border-white/10 pt-5">
        <p className="text-sm text-slate-400">
          你走过了 {trace.insights.length} 个节点，现在把这些发现轻轻收好。
        </p>
        <button
          type="button"
          onClick={onProceed}
          className="w-full rounded-xl bg-white px-5 py-3 text-sm font-medium text-slate-950 transition hover:bg-slate-200"
        >
          进入情感收束
        </button>
      </div>
    </section>
  );
}
