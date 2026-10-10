import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';
import { Orb } from './Orb';

export const SceneIntro: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const titleSpring = spring({
    frame,
    fps,
    config: { damping: 14, mass: 0.8 },
  });

  const subtitleSpring = spring({
    frame: frame - 15,
    fps,
    config: { damping: 14 },
  });

  const badgesSpring = spring({
    frame: frame - 30,
    fps,
    config: { damping: 14 },
  });

  const orbSpring = spring({
    frame: frame - 10,
    fps,
    config: { damping: 12, mass: 0.9 },
  });

  const opacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: 'clamp' });
  const fadeOut = interpolate(frame, [150, 180], [1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
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
        opacity: opacity * fadeOut,
        fontFamily: 'system-ui, -apple-system, sans-serif',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Background ambient radial gradients */}
      <div
        style={{
          position: 'absolute',
          width: 800,
          height: 800,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(14,165,233,0.18) 0%, rgba(99,102,241,0.05) 50%, transparent 80%)',
          filter: 'blur(60px)',
          top: '15%',
        }}
      />

      {/* Pulsing Central Orb */}
      <div
        style={{
          transform: `scale(${orbSpring})`,
          marginBottom: 35,
        }}
      >
        <Orb size={170} color="#38bdf8" glowColor="rgba(56, 189, 248, 0.55)" />
      </div>

      {/* Main App Title */}
      <h1
        style={{
          fontSize: 84,
          fontWeight: 900,
          letterSpacing: '-2px',
          color: '#ffffff',
          margin: 0,
          transform: `translateY(${interpolate(titleSpring, [0, 1], [40, 0])}px)`,
          opacity: titleSpring,
          background: 'linear-gradient(135deg, #ffffff 40%, #7dd3fc 100%)',
          WebkitBackgroundClip: 'text',
          WebkitTextFillColor: 'transparent',
          textShadow: '0 0 40px rgba(56,189,248,0.3)',
        }}
      >
        RELAY
      </h1>

      {/* Subtitle */}
      <p
        style={{
          fontSize: 32,
          fontWeight: 500,
          color: '#94a3b8',
          margin: '18px 0 35px 0',
          maxWidth: 850,
          textAlign: 'center',
          lineHeight: 1.35,
          transform: `translateY(${interpolate(subtitleSpring, [0, 1], [30, 0])}px)`,
          opacity: subtitleSpring,
        }}
      >
        Voice Superpowers & Remote Mobile Autopilot for Desktop AI Coding
      </p>

      {/* Feature Pills */}
      <div
        style={{
          display: 'flex',
          gap: 16,
          transform: `translateY(${interpolate(badgesSpring, [0, 1], [25, 0])}px)`,
          opacity: badgesSpring,
        }}
      >
        {[
          { icon: '⚡', text: 'In-Place F9 Translation (<150ms)' },
          { icon: '🎙️', text: 'Faster-Whisper on Local CUDA' },
          { icon: '📱', text: 'Telegram Remote & Subagents' },
          { icon: '🔒', text: '100% Private & Local' },
        ].map((badge, idx) => (
          <div
            key={idx}
            style={{
              padding: '10px 22px',
              borderRadius: 30,
              backgroundColor: 'rgba(30, 41, 59, 0.75)',
              border: '1px solid rgba(56, 189, 248, 0.25)',
              color: '#e2e8f0',
              fontSize: 18,
              fontWeight: 600,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              boxShadow: '0 4px 15px rgba(0,0,0,0.3)',
            }}
          >
            <span>{badge.icon}</span>
            <span>{badge.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
};
