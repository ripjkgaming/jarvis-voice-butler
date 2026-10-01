'use client';

import { useSyncExternalStore } from 'react';

type Geometry = { width: number; height: number; screenWidth: number };
const INITIAL: Geometry = { width: 1280, height: 720, screenWidth: 1280 };
let geometry = INITIAL;
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | undefined;
let rapidUntil = 0;

function measure() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  const screenWidth = window.screen.width;
  if (
    geometry.width !== width ||
    geometry.height !== height ||
    geometry.screenWidth !== screenWidth
  ) {
    geometry = { width, height, screenWidth };
    listeners.forEach((fn) => fn());
  }
}

function schedule() {
  clearTimeout(timer);
  if (document.hidden || !listeners.size) return;
  timer = setTimeout(
    () => {
      measure();
      schedule();
    },
    Date.now() < rapidUntil ? 150 : 1000
  );
}

function resume() {
  measure();
  schedule();
}
/** Native menu resizing sometimes omits resize events; briefly accelerate
 * the shared fallback after a command, without permanent polling cost. */
export function refreshWindowGeometry() {
  rapidUntil = Math.max(rapidUntil, Date.now() + 2000);
  resume();
}
function transition() {
  rapidUntil = Date.now() + 12000;
  resume();
}
function subscribe(fn: () => void) {
  listeners.add(fn);
  if (listeners.size === 1) {
    window.addEventListener('resize', measure);
    window.addEventListener('focus', resume);
    window.addEventListener('jarvis-school', transition);
    document.addEventListener('visibilitychange', resume);
    resume();
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size) return;
    clearTimeout(timer);
    window.removeEventListener('resize', measure);
    window.removeEventListener('focus', resume);
    window.removeEventListener('jarvis-school', transition);
    document.removeEventListener('visibilitychange', resume);
  };
}

/** Resize events plus one shared WebKitGTK backstop, fast only while the
 * shell is moving. No timer while hidden; unchanged dimensions don't render. */
export function useWindowGeometry(): Geometry {
  return useSyncExternalStore(
    subscribe,
    () => geometry,
    () => INITIAL
  );
}
