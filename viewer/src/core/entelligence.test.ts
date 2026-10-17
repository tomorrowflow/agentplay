import { describe, expect, it } from 'vitest';
import { researchUrl } from './entelligence';

describe('researchUrl', () => {
  it('points at the write-up and says only which surface opened it', () => {
    expect(researchUrl('welcome')).toBe(
      'https://entelligence.ai/research/self-improving-coding-agents?src=agentplay-viewer&from=welcome'
    );
  });
});
