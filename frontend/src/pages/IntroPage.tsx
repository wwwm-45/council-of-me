import { motion } from 'motion/react';
import { useNavigate } from 'react-router-dom';

/**
 * Intro landing foreground — the visual is reproduced from 22.zip/src/App.tsx.
 * It renders inside HomeShell, which owns the persistent three.js background,
 * so "Enter the Council" navigates to the setup screen (/start) without
 * re-mounting the scene or replaying the particle convergence.
 */
export default function IntroPage() {
  const nav = useNavigate();

  return (
    <div
      className="relative z-10 flex flex-col items-center justify-between h-full pt-16 pb-0 pointer-events-none font-sans"
      style={{ fontFamily: '"Inter", ui-sans-serif, system-ui, sans-serif' }}
    >

      <motion.div
        initial={{ opacity: 0, filter: 'brightness(0.5)' }}
        animate={{ opacity: 1, filter: 'brightness(1)' }}
        transition={{ duration: 3, ease: 'easeOut', delay: 0.5 }}
        className="flex flex-col items-center mt-20"
      >
        <h1 className="relative text-4xl md:text-5xl lg:text-6xl font-light tracking-[0.5em] font-serif mb-4 text-center select-none">
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-gray-300 via-white to-gray-300 relative z-10">
            COUNCIL OF ME
          </span>
          <span className="absolute left-1/2 -translate-x-1/2 top-0 w-full text-center text-[#8ab4f8] blur-[16px] opacity-50 z-0 pointer-events-none">
            COUNCIL OF ME
          </span>
          <span className="absolute left-1/2 -translate-x-1/2 top-0 w-full text-center text-white blur-[4px] opacity-30 z-0 pointer-events-none">
            COUNCIL OF ME
          </span>
        </h1>

        <div className="flex items-center gap-4 mb-2">
          <div className="w-12 h-[1px] bg-gradient-to-r from-transparent to-white/30"></div>
          <div className="relative w-1.5 h-1.5 rotate-45 border border-white/40">
             <div className="absolute inset-0 bg-white/50 shadow-[0_0_8px_rgba(138,180,248,0.8)] blur-[1px] scale-150"></div>
          </div>
          <div className="w-12 h-[1px] bg-gradient-to-l from-transparent to-white/30"></div>
        </div>

        <p className="text-[10px] md:text-xs tracking-[0.4em] text-[#9ba3af] font-light uppercase text-center mt-6 opacity-80">
          Explore the many perspectives within
        </p>
      </motion.div>

      <motion.button
        type="button"
        onClick={() => nav('/start')}
        initial={{ opacity: 0, filter: 'brightness(0.2)' }}
        animate={{ opacity: 1, filter: 'brightness(1)' }}
        transition={{ duration: 4, ease: 'easeOut', delay: 1.5 }}
        className="pointer-events-auto flex flex-col items-center group focus:outline-none p-8 mb-0"
      >

        {/* Advanced Symbol */}
        <div className="relative mb-8 flex items-center justify-center opacity-80 group-hover:opacity-100 transition-opacity duration-1000">
           {/* Outer subtle glow ring */}
           <div className="absolute w-14 h-14 rounded-full border border-[#8ab4f8]/20 blur-[2px] transition-all duration-1000 group-hover:scale-110 group-hover:border-[#8ab4f8]/40"></div>

           {/* Inner precise rings */}
           <div className="absolute w-14 h-14 rounded-full border border-white/10 group-hover:border-white/30 shadow-[inset_0_0_10px_rgba(138,180,248,0.1)] transition-all duration-1000 group-hover:scale-110"></div>

           <div className="absolute w-8 h-8 rotate-45 border border-white/20 transition-all duration-1000 ease-out group-hover:rotate-[135deg]"></div>

           {/* Center glowing core */}
           <div className="relative w-2 h-2 bg-white rotate-45 shadow-[0_0_8px_#ffffff,0_0_20px_#8ab4f8] transition-all duration-1000 group-hover:shadow-[0_0_12px_#ffffff,0_0_30px_#8ab4f8] group-hover:scale-110"></div>
        </div>

        {/* Text with Premium Feel */}
        <div className="relative">
          <span className="text-sm md:text-base tracking-[0.4em] font-light text-transparent bg-clip-text bg-gradient-to-r from-[#e2e8f0]/80 via-white to-[#e2e8f0]/80 uppercase">
            Enter the Council
          </span>
          {/* Subtle Text Glow Behind */}
          <span className="absolute inset-0 text-sm md:text-base tracking-[0.4em] font-light text-[#8ab4f8] uppercase blur-[6px] mix-blend-screen opacity-0 group-hover:opacity-60 transition-opacity duration-1000 pointer-events-none">
            Enter the Council
          </span>
          {/* Underline separator */}
          <div className="absolute -bottom-4 left-1/2 -translate-x-1/2 w-4 h-[1px] bg-gradient-to-r from-transparent via-[#8ab4f8]/60 to-transparent transition-all duration-1000 ease-out shadow-[0_0_8px_rgba(138,180,248,0.6)] group-hover:w-full group-hover:via-white/70"></div>
        </div>

      </motion.button>

    </div>
  );
}
