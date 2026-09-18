export interface AppConfig {
  pageTitle: string;
  pageDescription: string;
  companyName: string;

  supportsChatInput: boolean;
  supportsVideoInput: boolean;
  supportsScreenShare: boolean;
  isPreConnectBufferEnabled: boolean;

  logo: string;
  startButtonText: string;
  accent?: string;
  logoDark?: string;
  accentDark?: string;

  audioVisualizerType?: 'bar' | 'wave' | 'grid' | 'radial' | 'aura';
  audioVisualizerColor?: `#${string}`;
  audioVisualizerColorDark?: `#${string}`;
  audioVisualizerColorShift?: number;
  audioVisualizerBarCount?: number;
  audioVisualizerGridRowCount?: number;
  audioVisualizerGridColumnCount?: number;
  audioVisualizerRadialBarCount?: number;
  audioVisualizerRadialRadius?: number;
  audioVisualizerWaveLineWidth?: number;

  // agent dispatch configuration
  agentName?: string;

  // LiveKit Cloud Sandbox configuration
  sandboxId?: string;
}

export const APP_CONFIG_DEFAULTS: AppConfig = {
  companyName: 'Jarvis',
  pageTitle: 'Jarvis',
  pageDescription: 'Offline-first voice butler for your desktop',

  supportsChatInput: true,
  supportsVideoInput: true,
  supportsScreenShare: true,
  isPreConnectBufferEnabled: true,

  logo: '/lk-logo.svg',
  accent: '#1fd5f9',
  logoDark: '/lk-logo-dark.svg',
  accentDark: '#22d3ee',
  startButtonText: 'Talk to Jarvis',

  // optional: audio visualization configuration
  // audioVisualizerType: 'bar',
  audioVisualizerColor: '#002cf2',
  audioVisualizerColorDark: '#1fd5f9',
  // audioVisualizerColorShift: 0.3,
  // audioVisualizerBarCount: 5,
  //audioVisualizerType: 'radial',
  //audioVisualizerRadialBarCount: 24,
  //audioVisualizerRadialRadius: 100,
  //audioVisualizerType: 'grid',
  //audioVisualizerGridRowCount: 25,
  //audioVisualizerGridColumnCount: 25,
  //audioVisualizerType: 'wave',
  //audioVisualizerWaveLineWidth: 3,
  // 'bar': the aura shader renders a 300-450px blob that overflows
  // its 90px session tile and spills across the HUD. The HUD's own
  // ParticleOrb is the hero visual; the session tile stays minimal.
  audioVisualizerType: 'bar',

  // agent dispatch configuration — default; the Tauri shell overrides
  // this at runtime via invoke('app_config').
  agentName: process.env.NEXT_PUBLIC_AGENT_NAME ?? 'my-agent',

  // LiveKit Cloud Sandbox configuration
  sandboxId: undefined,
};
