import React from 'react';
import { Composition } from 'remotion';
import { RelayPromo } from './RelayPromo';

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="RelayPromo"
        component={RelayPromo}
        durationInFrames={1200}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
