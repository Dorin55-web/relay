import React from 'react';
import { interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

export const SceneDesktop: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const fadeIn = interpolate(frame, [0, 20], [0, 1], { extrapolateRight: 'clamp' });
  const fadeOut = interpolate(frame, [300, 330], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });

  // Key press animation around frame 80
  const isKeyPressed = frame >= 80 && frame <= 100;
  const keySpring = spring({
    frame: frame - 80,
    fps,
    config: { damping: 10, mass: 0.5 },
  });

  // Text state: before frame 88 is Romanian, after is translated English
  const isTranslated = frame >= 88;
  const originalText = "Echipă: construiește un microserviciu cu autentificare JWT";
  const translatedText = "create a microservice with JWT authentication";

  // Typing effect for Romanian before frame 75
  const charsShown = Math.min(
    originalText.length,
    Math.floor(interpolate(frame, [15, 75], [0, originalText.length], { extrapolateRight: 'clamp' }))
  );
  const currentRomanianText = originalText.slice(0, charsShown);

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
        position: 'relative',
        padding: '0 80px',
      }}
    >
      {/* Top Header */}
      <div style={{ textAlign: 'center', marginBottom: 35 }}>
        <div
          style={{
            display: 'inline-block',
            padding: '6px 18px',
            borderRadius: 20,
            backgroundColor: 'rgba(56, 189, 248, 0.12)',
            border: '1px solid rgba(56, 189, 248, 0.3)',
            color: '#38bdf8',
            fontSize: 16,
            fontWeight: 700,
            textTransform: 'uppercase',
            letterSpacing: '1px',
            marginBottom: 12,
          }}
        >
          Desktop Intelligence
        </div>
        <h2
          style={{
            fontSize: 48,
            fontWeight: 800,
            color: '#ffffff',
            margin: '0 0 10px 0',
            letterSpacing: '-1px',
          }}
        >
          In-Place F9 Translation in &lt;150ms
        </h2>
        <p style={{ fontSize: 22, color: '#94a3b8', margin: 0 }}>
          Type naturally in Romanian directly in your IDE. Press F9 to translate in place on local GPU.
        </p>
      </div>

      {/* Mockup IDE / Chat Window */}
      <div
        style={{
          width: 1040,
          backgroundColor: '#111827',
          borderRadius: 16,
          border: '1px solid #1f2937',
          boxShadow: '0 25px 60px rgba(0, 0, 0, 0.65)',
          overflow: 'hidden',
        }}
      >
        {/* Window Chrome Titlebar */}
        <div
          style={{
            height: 48,
            backgroundColor: '#0f172a',
            borderBottom: '1px solid #1e293b',
            display: 'flex',
            alignItems: 'center',
            padding: '0 18px',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#ef4444' }} />
            <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#f59e0b' }} />
            <div style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#10b981' }} />
          </div>
          <div style={{ color: '#64748b', fontSize: 14, fontWeight: 600 }}>
            AntiGravity / Cursor / VSCode — Active Workspace
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div
              style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                backgroundColor: isTranslated ? '#10b981' : '#38bdf8',
                boxShadow: isTranslated ? '0 0 8px #10b981' : '0 0 8px #38bdf8',
              }}
            />
            <span style={{ color: '#94a3b8', fontSize: 13, fontWeight: 600 }}>
              {isTranslated ? 'CUDA Ready' : 'Relay Active'}
            </span>
          </div>
        </div>

        {/* Window Body */}
        <div style={{ padding: '36px 40px' }}>
          {/* Agent History simulation */}
          <div
            style={{
              padding: '16px 20px',
              backgroundColor: '#1e293b',
              borderRadius: 10,
              color: '#94a3b8',
              fontSize: 16,
              marginBottom: 28,
              borderLeft: '4px solid #38bdf8',
            }}
          >
            <span style={{ color: '#38bdf8', fontWeight: 700 }}>AI Agent:</span> Ready for your next instruction.
            What should we implement?
          </div>

          {/* Active Input Box Container */}
          <div style={{ position: 'relative' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 8,
              }}
            >
              <span style={{ color: '#64748b', fontSize: 14, fontWeight: 600 }}>Message input</span>
              {/* Hotkey Badge */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                  transform: isKeyPressed ? `scale(${1 - keySpring * 0.1})` : 'scale(1)',
                }}
              >
                <span style={{ color: '#94a3b8', fontSize: 14 }}>Trigger:</span>
                <div
                  style={{
                    padding: '4px 14px',
                    borderRadius: 8,
                    backgroundColor: isKeyPressed ? '#0284c7' : '#1e293b',
                    border: isKeyPressed ? '2px solid #38bdf8' : '1px solid #334155',
                    color: isKeyPressed ? '#ffffff' : '#38bdf8',
                    fontSize: 14,
                    fontWeight: 800,
                    boxShadow: isKeyPressed ? '0 0 15px rgba(56, 189, 248, 0.8)' : 'none',
                    transition: 'all 0.1s ease',
                  }}
                >
                  F9
                </div>
              </div>
            </div>

            {/* Input Box */}
            <div
              style={{
                minHeight: 80,
                backgroundColor: '#0a0f1d',
                borderRadius: 12,
                border: isTranslated ? '1.5px solid #10b981' : '1.5px solid #38bdf8',
                padding: '18px 22px',
                fontSize: 20,
                color: '#f8fafc',
                fontFamily: "'JetBrains Mono', Consolas, monospace",
                display: 'flex',
                alignItems: 'center',
                boxShadow: isTranslated
                  ? '0 0 25px rgba(16, 185, 129, 0.25)'
                  : '0 0 25px rgba(56, 189, 248, 0.15)',
              }}
            >
              <span>{isTranslated ? translatedText : currentRomanianText}</span>
              <span
                style={{
                  display: 'inline-block',
                  width: 3,
                  height: 24,
                  backgroundColor: '#38bdf8',
                  marginLeft: 4,
                  opacity: Math.sin(frame * 0.2) > 0 ? 1 : 0,
                }}
              />
            </div>
          </div>

          {/* Stats Bar */}
          <div
            style={{
              display: 'flex',
              gap: 28,
              marginTop: 22,
              paddingTop: 18,
              borderTop: '1px solid #1e293b',
            }}
          >
            <div style={{ color: '#64748b', fontSize: 14 }}>
              ⚡ Latency: <strong style={{ color: '#10b981' }}>118ms</strong>
            </div>
            <div style={{ color: '#64748b', fontSize: 14 }}>
              🧠 Model: <strong style={{ color: '#e2e8f0' }}>Opus-MT (Marian) CUDA fp16</strong>
            </div>
            <div style={{ color: '#64748b', fontSize: 14 }}>
              🛡️ Safety: <strong style={{ color: '#e2e8f0' }}>No Auto-Enter (Review first)</strong>
            </div>
            <div style={{ color: '#64748b', fontSize: 14 }}>
              🎙️ Fallback: <strong style={{ color: '#38bdf8' }}>Empty box → Voice Dictation</strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
