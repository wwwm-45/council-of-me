import { X } from 'lucide-react';
import { useId } from 'react';
import type { PropsWithChildren } from 'react';

import type { ImmersivePanelMode } from './types';

const MODE_LABELS = {
  explore: '深海漫游',
  dialogue: '多面会话',
  trace: '反思轨迹',
} as const;

interface ImmersiveDrawerProps {
  mode: Exclude<ImmersivePanelMode, 'closed'>;
  title: string;
  onClose: () => void;
}

export function ImmersiveDrawer({
  mode,
  title,
  onClose,
  children,
}: PropsWithChildren<ImmersiveDrawerProps>) {
  const titleId = useId();

  return (
    <aside
      aria-labelledby={titleId}
      className="pointer-events-auto absolute bottom-6 right-6 top-6 z-30 flex w-[420px] max-w-[calc(100%-3rem)] flex-col overflow-hidden rounded-[32px] border border-white/10 bg-slate-950/80 shadow-2xl backdrop-blur-2xl"
    >
      <header className="flex shrink-0 items-center justify-between border-b border-white/5 bg-white/5 px-6 py-4">
        <div>
          <p className="text-[9px] font-bold uppercase tracking-[0.2em] text-indigo-400">
            沉浸探索状态
          </p>
          <h2 className="mt-1 text-sm font-semibold text-white">{MODE_LABELS[mode]}</h2>
          <p id={titleId} className="sr-only">{title}</p>
        </div>
        <button
          type="button"
          aria-label="关闭探索面板"
          onClick={onClose}
          className="grid h-8 w-8 place-items-center rounded-full border border-white/10 bg-white/5 text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
        >
          <X aria-hidden="true" className="h-4 w-4" />
        </button>
      </header>
      <div
        data-testid="immersive-drawer-scroll"
        className="min-h-0 flex-1 overflow-y-auto"
      >
        {children}
      </div>
    </aside>
  );
}
