import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { closeSession, downloadReport, previewReport } from '../api/client';
import { getSession as getStore, setSession } from '../store/session';
import SceneBackground from '../components/welcome/SceneBackground';

type DownloadState = 'idle' | 'loading' | 'error';

export default function ClosurePage() {
  const nav = useNavigate();
  const sid = getStore().sessionId;
  const [done, setDone] = useState(() => getStore().status === 'closed');
  const [downloadState, setDownloadState] = useState<DownloadState>('idle');
  const closingRef = useRef(false);

  useEffect(() => { if (!sid) nav('/'); }, [nav, sid]);

  // The synthesis landscape leads straight here: archive and close the session,
  // then show the completion screen. No emotion self-assessment or review step.
  useEffect(() => {
    if (!sid || done || closingRef.current) return;
    closingRef.current = true;
    closeSession(sid)
      .then(() => setSession({ status: 'closed' }))
      .catch((e) => console.error(e))
      .finally(() => setDone(true));
  }, [sid, done]);

  async function onDownload() {
    if (!sid) return;
    setDownloadState('loading');
    try {
      await downloadReport(sid);
      setDownloadState('idle');
    } catch (e) {
      console.error(e);
      setDownloadState('error');
    }
  }

  async function onPreview() {
    if (!sid) return;
    try {
      await previewReport(sid);
    } catch (e) {
      console.error(e);
    }
  }

  const downloadLabel =
    downloadState === 'loading' ? '生成报告中…' : downloadState === 'error' ? '生成失败，重试' : '下载报告';

  return (
    <div className="relative min-h-screen w-full overflow-hidden bg-[#050811] text-white flex items-center justify-center">
      <SceneBackground />
      <div className="relative z-10 w-full max-w-lg px-4 animate-fade-in">
        {!done ? (
          <div className="flex flex-col items-center justify-center text-center">
            <div className="mb-4 h-16 w-16 animate-pulse rounded-full bg-white/10" />
            <p className="text-sm text-white/60">正在为这次对话收尾…</p>
          </div>
        ) : (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-8 text-center shadow-2xl backdrop-blur-xl">
            <div className="mb-4 text-4xl">🌿</div>
            <h2 className="mb-2 text-xl font-semibold">会话已完成</h2>
            <p className="mb-6 text-sm text-white/70">感谢你的探索。你的内心声音值得被聆听。照顾好自己。</p>
            <button
              onClick={onDownload}
              disabled={downloadState === 'loading'}
              className="mb-3 w-full rounded-xl bg-[#cc785c] py-3 font-medium text-white transition hover:bg-[#d97757] disabled:opacity-60"
            >
              {downloadLabel}
            </button>
            <button
              onClick={onPreview}
              className="mb-3 w-full rounded-xl border border-white/15 py-3 font-medium text-white/80 transition hover:bg-white/5"
            >
              预览报告
            </button>
            <button
              onClick={() => { setSession({ sessionId: null, status: 'init' }); nav('/'); }}
              className="w-full rounded-xl border border-white/15 py-3 font-medium text-white/80 transition hover:bg-white/5"
            >
              开始新会话
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
