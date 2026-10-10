import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { Orb } from './Orb';

export const SceneOutro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const fadeIn = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  const scaleSpring = spring({
    frame,
    fps,
    config: { damping: 14, mass: 0.8 },
  });

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#090d16',
        opacity: fadeIn,
        fontFamily: 'system-ui, -apple-system, sans-serif',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          transform: `scale(${scaleSpring})`,
          marginBottom: 30,
        }}
      >
        <Orb size={180} color="#38bdf8" glowColor="rgba(56, 189, 248, 0.6)" />
      </div>

      <h1
        style={{
          fontSize: 76,
          fontWeight: 900,
          color: '#ffffff',
          margin: '0 0 12px 0',
          letterSpacing: '-2px',
          background: 'linear-gradient(135deg, #ffffff 40%, #7dd3fc 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          textShadow: '0 0 40px rgba(56,189,248,0.35)',
        }}
      >
        RELAY
      </h1>

      <p
        style={{
          fontSize: 30,
          fontWeight: 600,
          color: '#cbd5e1',
          margin: '0 0 40px 0',
        }}
      >
        Your Desktop AI. In Your Pocket.
      </p>

      {/* GitHub Repo Pill */}
      <div
        style={{
          padding: '16px 36px',
          borderRadius: 36,
          backgroundColor: 'rgba(30, 41, 59, 0.85)',
          border: '1.5px solid rgba(56, 189, 248, 0.4)',
          color: '#ffffff',
          fontSize: 24,
          fontWeight: 700,
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          boxShadow: '0 8px 30px rgba(2, 132, 199, 0.3)',
        }}
      >
        <span>⭐</span>
        <span style={{ color: '#38bdf8' }}>github.com/Dorin55-web/relay</span>
      </div>

      <div
        style={{
          marginTop: 24,
          color: '#64748b',
          fontSize: 16,
          fontWeight: 500,
        }}
      >
        Built with Whisper · Marian · PySide6 · Telegram Remote
      </div>
    </div>
  );
};
