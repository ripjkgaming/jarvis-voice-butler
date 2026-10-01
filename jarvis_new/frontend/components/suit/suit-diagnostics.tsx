'use client';

import { type CSSProperties, type KeyboardEvent, useEffect, useRef, useState } from 'react';
import { SUIT_PRESETS, SUIT_REGIONS, type SuitRegion } from '@/lib/suit-diagnostics';
import s from './suit-diagnostics.module.css';
import SuitScene from './suit-scene';

type Preset = keyof typeof SUIT_PRESETS;
type View = 'front' | 'three-quarter' | 'back';
const PRESETS: Preset[] = ['nominal', 'postFlight', 'critical'];
const VIEWS: { id: View; label: string }[] = [
  { id: 'front', label: 'Front' },
  { id: 'three-quarter', label: '3/4 view' },
  { id: 'back', label: 'Back' },
];
const SEGMENTS = Array.from({ length: 24 }, (_, i) => i);
const REGION_NOTES: Record<SuitRegion, string> = {
  head: 'Sensor array / targeting optics',
  chest: 'Torso structure / armor plating',
  leftArm: 'Left repulsor / actuator assembly',
  rightArm: 'Right repulsor / actuator assembly',
  leftLeg: 'Left stabilizer / propulsion assembly',
  rightLeg: 'Right stabilizer / propulsion assembly',
  reactor: 'Arc containment / power distribution',
};
const bounded = (value: number) => Math.max(0, Math.min(100, Math.round(value)));
const condition = (damage: number) =>
  damage >= 70
    ? 'Critical'
    : damage >= 35
      ? 'Compromised'
      : damage >= 15
        ? 'Wear detected'
        : 'Nominal';
const tone = (damage: number) => (damage >= 70 ? 'critical' : damage >= 35 ? 'warn' : 'normal');

function Meter({
  label,
  value,
  note,
  accent,
}: {
  label: string;
  value: number;
  note: string;
  accent: 'cyan' | 'gold' | 'mint';
}) {
  const level = bounded(value);
  return (
    <div className={s.meter} data-accent={accent} data-low={level <= 20 || undefined}>
      <div className={s.meterTop}>
        <span>{label}</span>
        <strong>
          {level}
          <small>%</small>
        </strong>
      </div>
      <div
        className={s.segments}
        role="meter"
        aria-label={`Simulated ${label.toLowerCase()}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={level}
      >
        {SEGMENTS.map((index) => (
          <span key={index} data-on={index < (level / 100) * SEGMENTS.length || undefined} />
        ))}
      </div>
      <span className={s.meterNote}>{note}</span>
    </div>
  );
}

/** A self-contained simulation. Mounting owns every listener and local state;
 * closing restores keyboard focus and leaves no diagnostics work behind. */
export default function SuitDiagnostics({
  onClose,
  school,
  barHeight,
}: {
  onClose: () => void;
  school: boolean;
  barHeight: number;
}) {
  const [preset, setPreset] = useState<Preset>('postFlight');
  const [selected, setSelected] = useState<SuitRegion>('chest');
  const [view, setView] = useState<View>('three-quarter');
  const [paused, setPaused] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const regionButtons = useRef(new Map<SuitRegion, HTMLButtonElement>());
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const previous = document.activeElement;
    closeButton.current?.focus({ preventScroll: true });
    const keydown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
        return;
      }
      if (event.key !== 'Tab' || !dialog.current) return;
      const candidates = Array.from(
        dialog.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
        )
      ).filter((element) => element.tabIndex >= 0 && element.getClientRects().length > 0);
      const first = candidates[0];
      const last = candidates.at(-1);
      if (!first || !last) {
        event.preventDefault();
        dialog.current.focus({ preventScroll: true });
      } else if (
        event.shiftKey &&
        (document.activeElement === first || !dialog.current.contains(document.activeElement))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (document.activeElement === last || !dialog.current.contains(document.activeElement))
      ) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', keydown, true);
    return () => {
      document.removeEventListener('keydown', keydown, true);
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus({ preventScroll: true });
    };
  }, []);

  const snapshot = SUIT_PRESETS[preset].snapshot;
  const damage = bounded(snapshot.damage[selected]);
  const integrity = 100 - damage;
  const selectedLabel = SUIT_REGIONS.find((region) => region.id === selected)?.label ?? selected;
  const totalIntegrity = Math.round(
    SUIT_REGIONS.reduce((sum, region) => sum + 100 - bounded(snapshot.damage[region.id]), 0) /
      SUIT_REGIONS.length
  );
  const affected = SUIT_REGIONS.filter((region) => snapshot.damage[region.id] >= 35).length;

  const regionKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    let next = SUIT_REGIONS.findIndex((region) => region.id === selected);
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight')
      next = (next + 1) % SUIT_REGIONS.length;
    else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft')
      next = (next + SUIT_REGIONS.length - 1) % SUIT_REGIONS.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = SUIT_REGIONS.length - 1;
    else return;
    event.preventDefault();
    event.stopPropagation();
    const id = SUIT_REGIONS[next].id;
    setSelected(id);
    regionButtons.current.get(id)?.focus();
  };

  return (
    <div
      className={s.overlay}
      data-suit-diagnostics="true"
      data-school={school || undefined}
      style={{ '--suit-bar-height': `${school ? Math.max(0, barHeight) : 0}px` } as CSSProperties}
    >
      <div
        ref={dialog}
        className={s.dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="suit-diagnostics-title"
        aria-describedby="suit-simulation-description"
        tabIndex={-1}
        onKeyDown={(event) => event.stopPropagation()}
      >
        <header className={s.header}>
          <div className={s.identity}>
            <span className={s.brand}>STARK / SYSTEMS</span>
            <h1 id="suit-diagnostics-title">Suit diagnostics</h1>
          </div>
          <div className={s.simulation} aria-label="Simulation, fictional suit telemetry">
            <span />
            SIMULATION
          </div>
          <button
            type="button"
            ref={closeButton}
            className={s.close}
            onClick={onClose}
            aria-label="Close suit diagnostics"
          >
            <span aria-hidden="true">×</span>
            <span className={s.closeLabel}>Close</span>
            <kbd>ESC</kbd>
          </button>
        </header>

        <div className={s.toolbar}>
          <p id="suit-simulation-description">
            Fictional armor telemetry. Select a scenario to inspect simulated damage and reserves.
          </p>
          <div className={s.presets} role="group" aria-label="Simulation scenario">
            {PRESETS.map((id) => (
              <button
                type="button"
                key={id}
                onClick={() => setPreset(id)}
                aria-pressed={preset === id}
              >
                {SUIT_PRESETS[id].label}
              </button>
            ))}
          </div>
        </div>

        <main className={s.workspace}>
          <section className={`${s.panel} ${s.regions}`} aria-labelledby="suit-regions-title">
            <div className={s.panelHeading}>
              <h2 id="suit-regions-title">Armor integrity</h2>
              <span>01 / STRUCTURE</span>
            </div>
            <div className={s.integrityTotal}>
              <strong>
                {totalIntegrity}
                <small>%</small>
              </strong>
              <span>COMPOSITE INTEGRITY</span>
            </div>
            <div
              className={s.regionList}
              role="listbox"
              aria-label="Suit region"
              onKeyDown={regionKeys}
            >
              {SUIT_REGIONS.map((region) => {
                const loss = bounded(snapshot.damage[region.id]);
                return (
                  <button
                    type="button"
                    key={region.id}
                    ref={(node) => {
                      if (node) regionButtons.current.set(region.id, node);
                      else regionButtons.current.delete(region.id);
                    }}
                    role="option"
                    aria-selected={selected === region.id}
                    tabIndex={selected === region.id ? 0 : -1}
                    data-tone={tone(loss)}
                    data-selected={selected === region.id || undefined}
                    onClick={() => setSelected(region.id)}
                  >
                    <span className={s.regionMarker} aria-hidden="true" />
                    <span className={s.regionName}>
                      {region.label}
                      <small>{condition(loss)}</small>
                    </span>
                    <strong>
                      {100 - loss}
                      <small>%</small>
                    </strong>
                    <span className={s.regionTrack} aria-hidden="true">
                      <span style={{ transform: `scaleX(${(100 - loss) / 100})` }} />
                    </span>
                  </button>
                );
              })}
            </div>
            <p className={s.regionHint}>↑ ↓ select a region · click the model to inspect</p>
          </section>

          <section className={s.modelPanel} aria-label="Interactive suit inspection">
            <div className={s.modelHeader}>
              <span>ARMOR // 3D INSPECTION</span>
              <span>SIM · {String(SUIT_REGIONS.length).padStart(2, '0')} REGIONS</span>
            </div>
            <div className={s.scene}>
              <SuitScene
                snapshot={snapshot}
                selected={selected}
                onSelect={setSelected}
                view={view}
                paused={paused}
              />
            </div>
            <div className={s.modelCaption}>
              <span className={s.modelName}>MARK // S</span>
              <span>{paused ? 'Motion paused' : 'Drag to orbit · scroll to zoom'}</span>
            </div>
            <div className={s.cameraBar}>
              <div className={s.cameraViews} role="group" aria-label="Suit camera view">
                {VIEWS.map((item) => (
                  <button
                    type="button"
                    key={item.id}
                    onClick={() => setView(item.id)}
                    aria-pressed={view === item.id}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className={s.pause}
                onClick={() => setPaused((value) => !value)}
                aria-pressed={paused}
                aria-label={paused ? 'Resume model motion' : 'Pause model motion'}
              >
                <span aria-hidden="true">{paused ? '▶' : 'Ⅱ'}</span>
                {paused ? 'Resume' : 'Pause'}
              </button>
            </div>
          </section>

          <aside className={s.telemetry} aria-label="Simulated systems and selected region">
            <section className={s.panel} aria-labelledby="suit-reserves-title">
              <div className={s.panelHeading}>
                <h2 id="suit-reserves-title">Core systems</h2>
                <span>02 / RESERVES</span>
              </div>
              <Meter
                label="Reactor charge"
                value={snapshot.reactor}
                note="Simulated energy available"
                accent="cyan"
              />
              <Meter
                label="Fuel reserve"
                value={snapshot.fuel}
                note="Simulated propulsion reserve"
                accent="gold"
              />
              <Meter
                label="Coolant level"
                value={snapshot.coolant}
                note="Simulated cooling capacity"
                accent="mint"
              />
            </section>
            <section
              className={`${s.panel} ${s.selectedPanel}`}
              data-tone={tone(damage)}
              aria-labelledby="suit-selected-title"
            >
              <div className={s.panelHeading}>
                <h2 id="suit-selected-title">Selected region</h2>
                <span>03 / ANALYSIS</span>
              </div>
              <div className={s.selectedName}>
                <h3>{selectedLabel}</h3>
                <span>{condition(damage)}</span>
              </div>
              <p className={s.partNote}>{REGION_NOTES[selected]}</p>
              <div className={s.selectedValues}>
                <div>
                  <span>INTEGRITY</span>
                  <strong>
                    {integrity}
                    <small>%</small>
                  </strong>
                </div>
                <div>
                  <span>DAMAGE</span>
                  <strong>
                    {damage}
                    <small>%</small>
                  </strong>
                </div>
              </div>
              <p className={s.damageNote} aria-live="polite">
                {damage >= 70
                  ? 'Critical simulated damage. Restore this assembly before the next test flight.'
                  : damage >= 35
                    ? 'Simulated impact damage detected. This assembly is flagged for service.'
                    : damage >= 15
                      ? 'Minor simulated wear. This assembly remains within operational tolerance.'
                      : 'No significant simulated damage. This assembly is within nominal tolerance.'}
              </p>
            </section>
          </aside>
        </main>

        <footer className={s.footer}>
          <span>
            <i />
            LOCAL SIMULATION · NO LIVE SUIT CONNECTED
          </span>
          <span>
            {affected
              ? `${affected} ${affected === 1 ? 'REGION' : 'REGIONS'} FLAGGED FOR REVIEW`
              : 'ALL REGIONS NOMINAL'}
          </span>
        </footer>
      </div>
    </div>
  );
}
