import React from 'react';
import { interpolate, useCurrentFrame } from 'remotion';

interface OrbProps {
  size?: number;
  color?: string;
  glowColor?: string;
  pulseSpeed?: number;
}

export const Orb: React.FC<OrbProps> = ({
  size = 180,
  color = '#38bdf8',
  glowColor = 'rgba(56, 189, 248, 0.45)',
  pulseSpeed = 1,
}) => {
  const frame = useCurrentFrame();

  const pulse = Math.sin((frame * pulseSpeed * Math.PI) / 30);
  const scale = interpolate(pulse, [-1, 1], [0.94, 1.06]);
  const rotation = frame * 0.8;

  // Generate 24 orbiting particles
  const particles = Array.from({ length: 24 }).map((_, i) => {
    const angle = (i * (360 / 24) * Math.PI) / 180 + (frame * 0.03 * (i % 2 === 0 ? 1 : -1));
    const radius = (size / 2) * (0.65 + 0.25 * Math.sin(frame * 0.05 + i));
    const x = Math.cos(angle) * radius;
    const y = Math.sin(angle) * radius;
    const dotSize = 3 + (i % 3) * 1.5;
    return { x, y, dotSize, key: i };
  });

  return (
    <div
      style={{
        width: size,
        height: size,
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        transform: `scale(${scale})`,
      }}
    >
      {/* Outer ambient glow */}
      <div
        style={{
          position: 'absolute',
          width: size * 1.4,
          height: size * 1.4,
          borderRadius: '50%',
          background: `radial-gradient(circle, ${glowColor} 0%, rgba(0,0,0,0) 70%)`,
          filter: 'blur(16px)',
        }}
      />

      {/* Central orb body */}
      <div
        style={{
          width: size * 0.72,
          height: size * 0.72,
          borderRadius: '50%',
          background: `radial-gradient(circle at 35% 35%, #ffffff 0%, ${color} 45%, #0369a1 100%)`,
          boxShadow: `0 0 35px ${glowColor}, inset 0 0 15px rgba(255,255,255,0.6)`,
        }}
      />

      {/* Rotating particle ring */}
      <div
        style={{
          position: 'absolute',
          width: size,
          height: size,
          transform: `rotate(${rotation}deg)`,
        }}
      >
        {particles.map((p) => (
          <div
            key={p.key}
            style={{
              position: 'absolute',
              left: size / 2 + p.x,
              top: size / 2 + p.y,
              width: p.dotSize,
              height: p.dotSize,
              borderRadius: '50%',
              backgroundColor: '#e0f2fe',
              boxShadow: `0 0 8px #38bdf8`,
            }}
          />
        ))}
      </div>
    </div>
  );
};
