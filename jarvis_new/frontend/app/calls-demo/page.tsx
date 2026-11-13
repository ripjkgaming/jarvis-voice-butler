import { Suspense } from 'react';
import { CallsDemo } from '@/components/calls/calls-demo';

export const metadata = { title: 'Jarvis — Calls HUD demo' };

/** Scripted mock call through every Calls HUD state.
 *  `?state=ringing|dialing|handling|escalation|hold|owner|ended` freezes one. */
export default function CallsDemoPage() {
  return (
    <Suspense fallback={null}>
      <CallsDemo />
    </Suspense>
  );
}
