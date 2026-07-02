import { lazy, Suspense, useMemo } from 'react';
import { isWebGLAvailable } from '../../lib/webgl';
import { usePrefersReducedMotion } from '../../hooks/usePrefersReducedMotion';
import CssFallbackBackground from './CssFallbackBackground';

const Scene = lazy(() =>
  import('./Scene').then((m) => ({ default: m.Scene })),
);

/**
 * Immersive deep-space homepage background (three.js Scene), shared by the intro
 * landing and the setup screen. Gates the heavy WebGL scene behind a WebGL +
 * reduced-motion check, falling back to a pure-CSS backdrop otherwise.
 */
export default function SceneBackground() {
  const reducedMotion = usePrefersReducedMotion();
  const webgl = useMemo(() => isWebGLAvailable(), []);

  return (
    <div className="absolute inset-0 z-0">
      {webgl && !reducedMotion ? (
        // While the heavy three.js chunk loads, show a plain deep-space backdrop
        // that matches the scene's dark base. We deliberately do NOT reuse
        // CssFallbackBackground here: its decorative cyan ring would pop in and
        // out for a single frame as the canvas mounts — the "weird component"
        // flashing by on open. A ring-less gradient is indistinguishable from
        // the scene's background, so the load is seamless.
        <Suspense fallback={<SceneLoadingBackdrop />}>
          <Scene />
        </Suspense>
      ) : (
        <CssFallbackBackground />
      )}
    </div>
  );
}

/** Neutral deep-space placeholder shown while the lazy three.js Scene chunk loads. */
function SceneLoadingBackdrop() {
  return (
    <div
      aria-hidden
      className="absolute inset-0"
      style={{ background: 'radial-gradient(ellipse at 50% 42%, #0d0d2b 0%, #0a0a1a 70%)' }}
    />
  );
}
