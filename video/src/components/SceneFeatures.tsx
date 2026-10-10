import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

export const SceneFeatures: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const fadeIn = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  const fadeOut = interpolate(frame, [160, 180], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  const features = [
    {
      icon: '🎙️',
      title: 'Whisper Large-V3 on CUDA',
      desc: 'Local speech-to-text with VAD streaming. Speaks Romanian, writes English.',
      color: '#38bdf8',
    },
    {
      icon: '⚡',
      title: 'In-Place F9 Translation',
      desc: 'Detects Romanian text in active chat, replaces in <150ms via Marian fp16.',
      color: '#f59e0b',
    },
    {
      icon: '👥',
      title: 'Multi-Agent Subarms',
      desc: 'Triggers /teamwork-preview, /goal, and /plan modes straight from Telegram.',
      color: '#10b981',
    },
    {
      icon: '📸',
      title: 'Multi-Photo Albums',
      desc: 'Snap whiteboard sketches or errors from your phone into desktop context.',
      color: '#ec4899',
    },
    {
      icon: '📊',
      title: 'Live Quota & Usage Cards',
      desc: 'Track AI token usage, reset windows, and task completions remotely.',
      color: '#8b5cf6',
    },
    {
      icon: '🔒',
      title: '100% Private & Local',
      desc: 'No voice cloud subscriptions. Your code and prompts never leave your PC.',
      color: '#06b6d4',
    },
  ];

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
        opacity: fadeIn * fadeOut,
        fontFamily: 'system-ui, -apple-system, sans-serif',
        padding: '0 80px',
      }}
    >
      <div style={{ textAlign: 'center', marginBottom: 40 }}>
        <h2
          style={{
            fontSize: 50,
            fontWeight: 800,
            color: '#ffffff',
            margin: '0 0 12px 0',
            letterSpacing: '-1px',
          }}
        >
          Engineered for Heavy AI Agent Workflows
        </h2>
        <p style={{ fontSize: 22, color: '#94a3b8', margin: 0 }}>
          Everything you need to prompt faster, multitask, and stay in control.
        </p>
      </div>

      {/* Grid of 6 Cards */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gap: 24,
          maxWidth: 1200,
          width: '100%',
        }}
      >
        {features.map((feat, idx) => {
          const cardSpring = spring({
            frame: frame - idx * 8,
            fps,
            config: { damping: 12 },
          });

          return (
            <div
              key={idx}
              style={{
                backgroundColor: 'rgba(30, 41, 59, 0.65)',
                border: '1px solid rgba(255, 255, 255, 0.08)',
                borderRadius: 16,
                padding: '24px 26px',
                transform: `scale(${cardSpring})`,
                opacity: cardSpring,
                boxShadow: '0 8px 25px rgba(0,0,0,0.4)',
              }}
            >
              <div
                style={{
                  width: 52,
                  height: 52,
                  borderRadius: 12,
                  backgroundColor: `${feat.color}20`,
                  border: `1px solid ${feat.color}40`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 26,
                  marginBottom: 16,
                }}
              >
                {feat.icon}
              </div>
              <h3
                style={{
                  fontSize: 20,
                  fontWeight: 700,
                  color: '#ffffff',
                  margin: '0 0 8px 0',
                }}
              >
                {feat.title}
              </h3>
              <p
                style={{
                  fontSize: 15,
                  color: '#94a3b8',
                  margin: 0,
                  lineHeight: 1.45,
                }}
              >
                {feat.desc}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
};
