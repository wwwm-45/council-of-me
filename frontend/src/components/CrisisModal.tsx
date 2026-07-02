interface Props {
  resources: { name: string; phone: string; description: string }[];
  onClose: () => void;
}

export default function CrisisModal({ resources, onClose }: Props) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-white rounded-2xl shadow-xl max-w-md w-full mx-4 p-6">
        <h2 className="text-lg font-bold text-red-600 mb-3">需要即时支持</h2>
        <p className="text-sm text-slate-600 mb-4">
          我们注意到你的输入可能包含紧急求助信号。请优先联系以下专业支持：
        </p>
        <ul className="space-y-3 mb-5">
          {(resources || []).map((r, i) => (
            <li key={i} className="bg-red-50 rounded-lg p-3">
              <p className="font-semibold text-red-700">{r.name}</p>
              <p className="text-red-600 text-lg font-mono">{r.phone}</p>
              <p className="text-xs text-red-500">{r.description}</p>
            </li>
          ))}
        </ul>
        <button onClick={onClose} className="w-full py-2 rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200 text-sm">
          我已知晓，返回
        </button>
      </div>
    </div>
  );
}
