import { Fragment } from 'react';

interface BlurInTextProps {
  text: string;
  splitBy?: 'word' | 'char';
  className?: string;
  /** Seconds before the first segment animates. */
  baseDelay?: number;
  /** Seconds added per segment index. */
  stepDelay?: number;
}

export default function BlurInText({
  text,
  splitBy = 'word',
  className,
  baseDelay = 0,
  stepDelay = 0.08,
}: BlurInTextProps) {
  const segments = splitBy === 'char' ? Array.from(text) : text.split(' ');

  return (
    <span className={className} aria-label={text}>
      {segments.map((seg, i) => (
        <Fragment key={`${seg}-${i}`}>
          <span
            aria-hidden
            className="blur-in-segment"
            style={{ animationDelay: `${baseDelay + i * stepDelay}s` }}
          >
            {seg}
          </span>
          {/* Inter-word space lives BETWEEN the inline-block spans; a trailing
              space inside an inline-block collapses to zero width. */}
          {splitBy === 'word' && i < segments.length - 1 ? ' ' : ''}
        </Fragment>
      ))}
    </span>
  );
}
