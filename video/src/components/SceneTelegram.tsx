import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

export const SceneTelegram: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const fadeIn = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  const fadeOut = interpolate(frame, [330, 360], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  // Staggered reveals
  const phoneSpring = spring({ frame, fps, config: { damping: 14 } });
  const voiceNoteSpring = spring({ frame: frame - 20, fps, config: { damping: 12 } });
  const botCardSpring = spring({ frame: frame - 65, fps, config: { damping: 12 } });
  const executionSpring = spring({ frame: frame - 120, fps, config: { damping: 12 } });

  // Progress animation
  const progressPercent = Math.min(100, Math.floor(interpolate(frame, [120, 250], [0, 100], { extrapolateRight: 'clamp' })));
  const isFinished = frame >= 250;

  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        backgroundColor: '#090d16',
        opacity: fadeIn * fadeOut,
        fontFamily: 'system-ui, -apple-system, sans-serif',
        position: 'relative',
        padding: '0 80px',
        gap: 60,
      }}
    >
      {/* Left Column: Headlines & Explanations */}
      <div style={{ flex: 1, maxWidth: 640 }}>
        <div
          style={{
            display: 'inline-block',
            padding: '6px 18px',
            borderRadius: 20,
            backgroundColor: 'rgba(16, 185, 129, 0.12)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            color: '#10b981',
            fontSize: 16,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '1px',
            marginBottom: 16,
          }}
        >
          Mobile Freedom
        </div>
        <h2
          style={{
            fontSize: 52,
            fontWeight: 800,
            color: '#ffffff',
            margin: '0 0 16px 0',
            letterSpacing: '-1.5px',
            lineHeight: 1.15,
          }}
        >
          Remote Agentic Control From Your Phone
        </h2>
        <p
          style={{
            fontSize: 22,
            color: '#94a3b8',
            margin: '0 0 32px 0',
            lineHeight: 1.45,
          }}
        >
          Leave your desk. Send Romanian voice notes on Telegram — Relay translates via Whisper on your PC and orchestrates subagent teams automatically.
        </p>

        {/* Feature Highlights */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {[
            {
              icon: '👥',
              title: '/team <task> or "Echipă: ..."',
              desc: 'Dispatches multi-agent swarms (/teamwork-preview)',
            },
            {
              icon: '🎯',
              title: '/goal <task> or "Goal: ..."',
              desc: 'Autonomous long-running goal execution',
            },
            {
              icon: '🛑',
              title: 'Autopilot Countdown & Cancel',
              desc: '3-second safety window with [🛑 Cancel Task] button',
            },
          ].map((item, i) => (
            <div
              key={i}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 16,
                padding: '12px 18px',
                backgroundColor: 'rgba(30, 41, 59, 0.5)',
                borderRadius: 12,
                border: '1px solid rgba(255,255,255,0.06)',
              }}
            >
              <span style={{ fontSize: 28 }}>{item.icon}</span>
              <div>
                <div style={{ color: '#ffffff', fontWeight: 700, fontSize: 17 }}>
                  {item.title}
                </div>
                <div style={{ color: '#94a3b8', fontSize: 14 }}>{item.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Right Column: Phone Mockup */}
      <div
        style={{
          width: 440,
          height: 780,
          backgroundColor: '#0f172a',
          borderRadius: 44,
          border: '12px solid #1e293b',
          boxShadow: '0 25px 70px rgba(0,0,0,0.8)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          transform: `scale(${phoneSpring})`,
          position: 'relative',
        }}
      >
        {/* Phone Notch & Header */}
        <div
          style={{
            height: 64,
            backgroundColor: '#1e293b',
            display: 'flex',
            alignItems: 'center',
            padding: '0 20px',
            gap: 12,
            borderBottom: '1px solid #334155',
          }}
        >
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              backgroundColor: '#0284c7',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#ffffff',
              fontWeight: 800,
              fontSize: 18,
            }}
          >
            R
          </div>
          <div>
            <div style={{ color: '#ffffff', fontWeight: 700, fontSize: 16 }}>
              Relay Remote Bot
            </div>
            <div style={{ color: '#38bdf8', fontSize: 12, fontWeight: 500 }}>
              ● connected to Desktop
            </div>
          </div>
        </div>

        {/* Chat Area */}
        <div
          style={{
            flex: 1,
            padding: '20px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            background: 'linear-gradient(180deg, #0b1120 0%, #030712 100%)',
          }}
        >
          {/* User Voice Message */}
          <div
            style={{
              alignSelf: 'flex-end',
              maxWidth: 320,
              backgroundColor: '#0284c7',
              borderRadius: '18px 18px 4px 18px',
              padding: '12px 16px',
              color: '#ffffff',
              transform: `scale(${voiceNoteSpring})`,
              boxShadow: '0 4px 15px rgba(2, 132, 199, 0.35)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div
                style={{
                  width: 32,
                  height: 32,
                  borderRadius: '50%',
                  backgroundColor: 'rgba(255,255,255,0.2)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 16,
                }}
              >
                ▶
              </div>
              {/* Fake Audio Waveform */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 3, flex: 1 }}>
                {[14, 24, 18, 30, 22, 16, 28, 20, 12, 26, 18].map((h, idx) => (
                  <div
                    key={idx}
                    style={{
                      width: 4,
                      height: h,
                      backgroundColor: 'rgba(255,255,255,0.85)',
                      borderRadius: 2,
                    }}
                  />
                ))}
              </div>
              <span style={{ fontSize: 12, opacity: 0.9 }}>0:06</span>
            </div>
            <div style={{ fontSize: 12, marginTop: 6, fontStyle: 'italic', opacity: 0.95 }}>
              "Echipă: optimizează baza de date și adaugă indexuri"
            </div>
          </div>

          {/* Bot Live Card */}
          <div
            style={{
              alignSelf: 'flex-start',
              maxWidth: 340,
              backgroundColor: '#1e293b',
              borderRadius: '18px 18px 18px 4px',
              padding: '16px',
              color: '#f8fafc',
              border: '1px solid #334155',
              transform: `scale(${botCardSpring})`,
              boxShadow: '0 8px 25px rgba(0,0,0,0.5)',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 20 }}>👥</span>
              <strong style={{ fontSize: 15, color: '#38bdf8' }}>
                Teamwork Mode Detected
              </strong>
            </div>

            <div
              style={{
                fontSize: 13,
                color: '#cbd5e1',
                backgroundColor: '#0f172a',
                padding: '8px 10px',
                borderRadius: 8,
                fontFamily: 'monospace',
                marginBottom: 12,
              }}
            >
              /teamwork-preview optimize database and add indexes
            </div>

            {/* Execution status */}
            <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 12 }}>
              {isFinished ? (
                <span style={{ color: '#10b981', fontWeight: 600 }}>
                  ✅ Finished: 3 tables indexed, queries 85% faster!
                </span>
              ) : (
                <span>
                  ⚡ Orchestrating subagents: {progressPercent}%
                </span>
              )}
            </div>

            {/* Cancel Button */}
            {!isFinished && (
              <div
                style={{
                  backgroundColor: 'rgba(239, 68, 68, 0.2)',
                  border: '1px solid #ef4444',
                  borderRadius: 10,
                  padding: '8px 0',
                  textAlign: 'center',
                  color: '#f87171',
                  fontWeight: 700,
                  fontSize: 13,
                  cursor: 'pointer',
                }}
              >
                🛑 Cancel Task
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
