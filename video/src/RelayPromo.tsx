import React from 'react';
import { Sequence } from 'remotion';
import { SceneIntro } from './components/SceneIntro';
import { SceneDesktop } from './components/SceneDesktop';
import { SceneTelegram } from './components/SceneTelegram';
import { SceneFeatures } from './components/SceneFeatures';
import { SceneOutro } from './components/SceneOutro';

export const RelayPromo: React.FC = () => {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        backgroundColor: '#090d16',
        color: '#ffffff',
      }}
    >
      {/* 0s - 6s: Intro & Hook */}
      <Sequence from={0} durationInFrames={180}>
        <SceneIntro />
      </Sequence>

      {/* 6s - 17s: Desktop F9 In-Place Translation */}
      <Sequence from={180} durationInFrames={330}>
        <SceneDesktop />
      </Sequence>

      {/* 17s - 29s: Telegram Mobile Remote & Subagents */}
      <Sequence from={510} durationInFrames={360}>
        <SceneTelegram />
      </Sequence>

      {/* 29s - 35s: Feature Highlights Grid */}
      <Sequence from={870} durationInFrames={180}>
        <SceneFeatures />
      </Sequence>

      {/* 35s - 40s: Outro & Call to Action */}
      <Sequence from={1050} durationInFrames={150}>
        <SceneOutro />
      </Sequence>
    </div>
  );
};
