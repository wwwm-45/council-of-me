import { useEffect, useMemo, useState } from 'react';
import { Outlet, Link, useLocation } from 'react-router-dom';
import { getLlmModels, selectLlmModel, type LlmModelInfo } from '../api/client';
import { setSession } from '../store/session';

export default function Layout() {
  const location = useLocation();
  // These routes render their own full-screen dark shells (including their
  // loading/transition states), so Layout must drop the light header + gradient
  // to avoid the old UI flashing behind them during navigation.
  const immersiveRoutes = ['/elicitation', '/closure', '/portrait', '/debate', '/synthesis'];
  const isImmersiveRoute = immersiveRoutes.includes(location.pathname);
  const [models, setModels] = useState<LlmModelInfo[]>([]);
  const [currentModel, setCurrentModel] = useState('');
  const [changing, setChanging] = useState(false);

  useEffect(() => {
    let canceled = false;
    getLlmModels()
      .then((data) => {
        if (canceled) return;
        setModels(data.models || []);
        setCurrentModel(data.current_model || '');
        setSession({ llmModel: data.current_model || null });
      })
      .catch(console.error);
    return () => { canceled = true; };
  }, []);

  const groupedModels = useMemo(() => {
    const grouped: Record<string, LlmModelInfo[]> = {};
    for (const m of models) {
      if (!grouped[m.provider]) grouped[m.provider] = [];
      grouped[m.provider].push(m);
    }
    return grouped;
  }, [models]);

  async function onModelChange(nextModel: string) {
    if (!nextModel || nextModel === currentModel) return;
    setChanging(true);
    try {
      const r = await selectLlmModel(nextModel);
      setCurrentModel(r.current_model);
      setSession({ llmModel: r.current_model });
    } catch (e) {
      console.error(e);
    } finally {
      setChanging(false);
    }
  }

  return (
    <div className={isImmersiveRoute ? 'min-h-screen bg-black' : 'min-h-screen bg-gradient-to-br from-slate-50 to-blue-50'}>
      {!isImmersiveRoute && (
      <header className="border-b border-slate-200 bg-white/70 backdrop-blur sticky top-0 z-30">
        <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-lg font-semibold text-slate-800 tracking-tight">Council of Me</h1>
            <Link
              to="/history"
              className={`text-xs transition ${
                location.pathname === '/history' ? 'text-blue-600 font-medium' : 'text-slate-400 hover:text-slate-600'
              }`}
            >
              历史
            </Link>
          </div>
          <div className="flex items-center gap-2">
            {models.length > 0 && (
              <select
                value={currentModel}
                onChange={(e) => onModelChange(e.target.value)}
                disabled={changing}
                className="text-xs border border-slate-300 rounded-md bg-white px-2 py-1 text-slate-700"
                title="LLM model"
              >
                {Object.entries(groupedModels).map(([provider, list]) => (
                  <optgroup key={provider} label={provider.toUpperCase()}>
                    {list.map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.label}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            )}
            <span className="text-xs text-slate-400">{changing ? '切换中...' : '内心对话空间'}</span>
          </div>
        </div>
      </header>
      )}
      <main className={isImmersiveRoute ? 'min-h-screen' : undefined}>
        <Outlet />
      </main>
    </div>
  );
}
