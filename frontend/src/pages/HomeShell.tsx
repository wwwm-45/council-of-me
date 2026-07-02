import { Outlet } from 'react-router-dom';
import SceneBackground from '../components/welcome/SceneBackground';

/**
 * Persistent shell for the intro landing (/) and the setup screen (/start).
 * The three.js SceneBackground is mounted ONCE here and shared across both
 * child routes, so navigating between them never re-mounts the canvas nor
 * replays the particle convergence — only the foreground (<Outlet/>) swaps.
 */
export default function HomeShell() {
  return (
    <div className="relative h-screen w-full overflow-hidden bg-[#050811] text-white">
      <SceneBackground />
      <Outlet />
    </div>
  );
}
