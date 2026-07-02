import { useEffect, useRef } from 'react';

interface OrbitParticlesProps {
  color?: string;
}

interface OrbitParticle {
  angle: number;
  radius: number;
  speed: number;
  size: number;
  opacity: number;
  waveAmplitude: number;
  waveSpeed: number;
  waveOffset: number;
  zOffset: number;
}

/**
 * Ambient canvas of orbiting luminous particles behind the active inner voice.
 * Colour is animated towards `color` so switching voices cross-fades the halo.
 * Inert when the 2D context is unavailable (e.g. jsdom in tests).
 */
export default function OrbitParticles({ color = '#00e5ff' }: OrbitParticlesProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const colorRef = useRef<string>(color);

  useEffect(() => {
    colorRef.current = color;
  }, [color]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;

    let width = 0;
    let height = 0;
    let animationFrameId = 0;
    let time = 0;

    const particles: OrbitParticle[] = [];
    const particleCount = 200;

    const hexToRgb = (hex: string) => {
      let r = 0,
        g = 229,
        b = 255;
      if (hex.startsWith('#')) {
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
        if (result) {
          r = parseInt(result[1], 16);
          g = parseInt(result[2], 16);
          b = parseInt(result[3], 16);
        }
      } else if (hex.startsWith('rgb')) {
        const match = hex.match(/\d+/g);
        if (match && match.length >= 3) {
          r = parseInt(match[0]);
          g = parseInt(match[1]);
          b = parseInt(match[2]);
        }
      }
      return { r, g, b };
    };

    const currentColor = hexToRgb(colorRef.current);

    const initParticles = () => {
      particles.length = 0;
      const baseRadius = Math.min(width, height) * 0.45;

      for (let i = 0; i < particleCount; i++) {
        const isCore = Math.random() > 0.7;
        particles.push({
          angle: Math.random() * Math.PI * 2,
          radius: isCore ? Math.random() * (baseRadius * 0.5) : Math.random() * baseRadius * 1.2,
          speed: (Math.random() * 0.003 + 0.001) * (Math.random() > 0.5 ? 1 : -1),
          size: Math.random() * 1.5 + 0.5,
          opacity: Math.random() * 0.5 + 0.1,
          waveAmplitude: Math.random() * 15 + 5,
          waveSpeed: Math.random() * 0.015 + 0.005,
          waveOffset: Math.random() * Math.PI * 2,
          zOffset: Math.random() * 200 - 100,
        });
      }
    };

    const handleResize = () => {
      const parent = canvas.parentElement;
      if (parent) {
        const newWidth = parent.clientWidth;
        const newHeight = parent.clientHeight;
        if (width === newWidth && height === newHeight) return; // Prevent infinite reset loop

        width = newWidth;
        height = newHeight;
        const dpr = window.devicePixelRatio || 1;
        canvas.width = width * dpr;
        canvas.height = height * dpr;
        ctx.scale(dpr, dpr);
        canvas.style.width = width + 'px';
        canvas.style.height = height + 'px';
        initParticles();
      }
    };

    const resizeObserver = new ResizeObserver(() => {
      handleResize();
    });
    if (canvas.parentElement) {
      resizeObserver.observe(canvas.parentElement);
    }

    handleResize();

    const render = () => {
      time += 1;
      animationFrameId = requestAnimationFrame(render);

      const targetRgb = hexToRgb(colorRef.current);
      currentColor.r += (targetRgb.r - currentColor.r) * 0.08;
      currentColor.g += (targetRgb.g - currentColor.g) * 0.08;
      currentColor.b += (targetRgb.b - currentColor.b) * 0.08;

      const rgbString = `${Math.round(currentColor.r)}, ${Math.round(currentColor.g)}, ${Math.round(currentColor.b)}`;

      ctx.globalCompositeOperation = 'source-over';
      ctx.clearRect(0, 0, width, height);

      ctx.globalCompositeOperation = 'lighter';

      const cx = width / 2;
      const cy = height / 2;

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.angle += p.speed;

        const currentRadius = p.radius + Math.sin(time * p.waveSpeed + p.waveOffset) * p.waveAmplitude;

        // Elliptical orbit with some tilt
        const x = cx + Math.cos(p.angle) * currentRadius;
        const y = cy + Math.sin(p.angle) * currentRadius * 0.4 + Math.sin(time * 0.02 + p.angle) * 15;

        const depthScale = 1 + Math.sin(p.angle + p.zOffset) * 0.4;
        const currentSize = p.size * depthScale;
        const currentOpacity = p.opacity * Math.max(0.2, depthScale);

        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.1, currentSize), 0, Math.PI * 2);

        ctx.shadowBlur = Math.max(2, currentSize * 3);
        ctx.shadowColor = `rgba(${rgbString}, ${currentOpacity})`;
        ctx.fillStyle = `rgba(${rgbString}, ${currentOpacity})`;
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      // Node connections for core
      ctx.lineWidth = 0.5;
      const coreR = Math.min(width, height) * 0.25;
      for (let i = 0; i < particles.length; i++) {
        const p1 = particles[i];
        if (p1.radius > coreR) continue;

        const x1 = cx + Math.cos(p1.angle) * (p1.radius + Math.sin(time * p1.waveSpeed + p1.waveOffset) * p1.waveAmplitude);
        const y1 =
          cy +
          Math.sin(p1.angle) * (p1.radius + Math.sin(time * p1.waveSpeed + p1.waveOffset) * p1.waveAmplitude) * 0.4 +
          Math.sin(time * 0.02 + p1.angle) * 15;

        for (let j = i + 1; j < particles.length; j++) {
          const p2 = particles[j];
          if (p2.radius > coreR) continue;

          const x2 = cx + Math.cos(p2.angle) * (p2.radius + Math.sin(time * p2.waveSpeed + p2.waveOffset) * p2.waveAmplitude);
          const y2 =
            cy +
            Math.sin(p2.angle) * (p2.radius + Math.sin(time * p2.waveSpeed + p2.waveOffset) * p2.waveAmplitude) * 0.4 +
            Math.sin(time * 0.02 + p2.angle) * 15;

          const dist = Math.hypot(x2 - x1, y2 - y1);
          if (dist < 40) {
            const alpha = (1 - dist / 40) * 0.15;
            ctx.beginPath();
            ctx.moveTo(x1, y1);
            ctx.lineTo(x2, y2);
            ctx.strokeStyle = `rgba(${rgbString}, ${alpha})`;
            ctx.stroke();
          }
        }
      }
    };

    render();

    return () => {
      resizeObserver.disconnect();
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none z-0"
      style={{ mixBlendMode: 'screen' }}
    />
  );
}
