/** Pure-CSS deep-space backdrop used when WebGL is unavailable (and in jsdom tests). */
export default function CssFallbackBackground() {
  return (
    <div
      aria-hidden
      className="absolute inset-0 overflow-hidden"
      style={{ background: 'radial-gradient(ellipse at 50% 42%, #0d0d2b 0%, #0a0a1a 70%)' }}
    >
      <div
        className="absolute left-1/2 top-[42%] -translate-x-1/2 -translate-y-1/2 rounded-full"
        style={{
          width: 'min(62vw, 540px)',
          height: 'min(26vw, 220px)',
          border: '1px solid rgba(0, 229, 255, 0.35)',
          boxShadow:
            '0 0 60px rgba(0, 229, 255, 0.25), inset 0 0 48px rgba(124, 58, 237, 0.18)',
        }}
      />
    </div>
  );
}
