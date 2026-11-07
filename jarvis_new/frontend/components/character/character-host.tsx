'use client';

import { useEffect } from 'react';
import { startCharacterStore, stopCharacterStore } from '@/lib/character/store';

export function CharacterHost() {
  useEffect(() => {
    startCharacterStore();
    return stopCharacterStore;
  }, []);
  return null;
}
