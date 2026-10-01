'use client';

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { SuitRegion, SuitSnapshot } from '@/lib/suit-diagnostics';
import { createSuitGeometry } from './suit-geometry';

type SuitSceneProps = {
  snapshot: SuitSnapshot;
  selected: SuitRegion;
  onSelect: (region: SuitRegion) => void;
  view: 'front' | 'three-quarter' | 'back';
  paused?: boolean;
};

/** A demand-rendered inspection scene. No timer or perpetual render loop is used. */
export default function SuitScene(props: SuitSceneProps) {
  const mount = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const syncScene = useRef<(() => void) | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const host = mount.current;
    if (!host) return;
    const cleanups: (() => void)[] = [];
    let dead = false;
    let frame = 0;
    let inView = true;
    let contextLost = false;
    let width = 0;
    let height = 0;
    let density = 0;
    let frameCount = 0;
    let lastView = latest.current.view;
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const documentRoot = document.documentElement;
    const visible = () =>
      !dead &&
      !contextLost &&
      inView &&
      !document.hidden &&
      !documentRoot.classList.contains('hud-hidden') &&
      width > 0 &&
      height > 0;
    const stop = () => {
      if (frame) cancelAnimationFrame(frame);
      frame = 0;
      host.dataset.suitRunning = 'false';
    };
    const release = () => {
      dead = true;
      stop();
      syncScene.current = null;
      for (let i = cleanups.length - 1; i >= 0; i--) {
        try {
          cleanups[i]();
        } catch {
          // Finish releasing the remaining resources after a lost GL context.
        }
      }
      cleanups.length = 0;
    };

    try {
      const renderer = new THREE.WebGLRenderer({
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance',
      });
      const canvas = renderer.domElement;
      cleanups.push(() => {
        renderer.dispose();
        renderer.forceContextLoss();
        canvas.remove();
      });
      renderer.setClearColor(0x000000, 0);
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.25;
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      // Armor and lights are static: orbiting reuses the same detailed shadow map.
      renderer.shadowMap.autoUpdate = false;
      renderer.shadowMap.needsUpdate = true;
      canvas.dataset.suitCanvas = 'true';
      canvas.tabIndex = 0;
      canvas.setAttribute('role', 'img');
      canvas.setAttribute(
        'aria-label',
        'Interactive three-dimensional armor. Drag or use arrow keys to orbit, scroll or use plus and minus to zoom, and click armor to select a region.'
      );
      canvas.style.cssText =
        'display:block;width:100%;height:100%;touch-action:none;cursor:grab;outline-offset:-3px';
      host.appendChild(canvas);
      host.dataset.suitRenderer = 'webgl';
      host.dataset.suitFrames = '0';
      setUnavailable(false);

      const scene = new THREE.Scene();
      const suit = createSuitGeometry();
      scene.add(suit.root);
      cleanups.push(() => suit.dispose());
      const floorY = suit.bounds.min.y - 0.018;
      const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
      const controls = new OrbitControls(camera, canvas);
      cleanups.push(() => controls.dispose());
      controls.target.copy(suit.center);
      controls.enablePan = false;
      controls.enableDamping = !motion.matches;
      controls.dampingFactor = 0.13;
      controls.rotateSpeed = 0.72;
      controls.zoomSpeed = 0.7;
      controls.minPolarAngle = 0.58;
      controls.maxPolarAngle = Math.PI * 0.63;
      controls.autoRotate = false;

      // Locally generated studio reflections; no texture files or network requests.
      const room = new RoomEnvironment();
      const pmrem = new THREE.PMREMGenerator(renderer);
      try {
        const environment = pmrem.fromScene(room, 0.04);
        scene.environment = environment.texture;
        scene.environmentIntensity = 0.95;
        cleanups.push(() => environment.dispose());
      } finally {
        room.dispose();
        pmrem.dispose();
      }
      const key = new THREE.DirectionalLight('#fff0d5', 4.2);
      key.position.set(-4.5, 10, 7);
      key.target.position.copy(suit.center);
      key.castShadow = true;
      key.shadow.mapSize.set(2048, 2048);
      Object.assign(key.shadow.camera, {
        left: -5,
        right: 5,
        top: 5,
        bottom: -5,
        near: 0.5,
        far: 30,
      });
      key.shadow.normalBias = 0.025;
      key.shadow.bias = -0.00015;
      scene.add(key, key.target);
      cleanups.push(() => key.shadow.dispose());
      const fill = new THREE.DirectionalLight('#79dfff', 2.1);
      fill.position.set(5, 5.5, 4);
      const rim = new THREE.DirectionalLight('#ff9862', 4);
      rim.position.set(1.2, 8.5, -5.5);
      scene.add(fill, rim, new THREE.HemisphereLight('#b8dbff', '#151321', 0.65));

      const floorGeometry = new THREE.CircleGeometry(2.75, 96);
      const floorMaterial = new THREE.MeshStandardMaterial({
        color: '#071723',
        metalness: 0.3,
        roughness: 0.72,
        transparent: true,
        opacity: 0.72,
        depthWrite: false,
      });
      const floor = new THREE.Mesh(floorGeometry, floorMaterial);
      floor.rotation.x = -Math.PI / 2;
      floor.position.y = floorY;
      floor.receiveShadow = true;
      scene.add(floor);
      cleanups.push(() => {
        floorGeometry.dispose();
        floorMaterial.dispose();
      });
      const grid = new THREE.PolarGridHelper(2.78, 12, 4, 96, '#3a9aad', '#164051');
      grid.position.y = floorY + 0.003;
      const gridMaterials = Array.isArray(grid.material) ? grid.material : [grid.material];
      for (const material of gridMaterials) {
        material.transparent = true;
        material.opacity = 0.48;
        material.depthWrite = false;
      }
      scene.add(grid);
      cleanups.push(() => {
        grid.geometry.dispose();
        for (const material of gridMaterials) material.dispose();
      });
      const contactCanvas = document.createElement('canvas');
      contactCanvas.width = contactCanvas.height = 256;
      const ink = contactCanvas.getContext('2d');
      if (ink) {
        const gradient = ink.createRadialGradient(128, 128, 0, 128, 128, 128);
        gradient.addColorStop(0, 'rgba(0,0,0,.7)');
        gradient.addColorStop(0.45, 'rgba(0,0,0,.36)');
        gradient.addColorStop(1, 'rgba(0,0,0,0)');
        ink.fillStyle = gradient;
        ink.fillRect(0, 0, 256, 256);
        const texture = new THREE.CanvasTexture(contactCanvas);
        const geometry = new THREE.PlaneGeometry(3.5, 2.8);
        const material = new THREE.MeshBasicMaterial({
          map: texture,
          transparent: true,
          depthWrite: false,
          toneMapped: false,
        });
        const contact = new THREE.Mesh(geometry, material);
        contact.rotation.x = -Math.PI / 2;
        contact.position.set(0, floorY + 0.004, 0.12);
        scene.add(contact);
        cleanups.push(() => {
          texture.dispose();
          geometry.dispose();
          material.dispose();
          contactCanvas.width = contactCanvas.height = 1;
        });
      }

      function schedule() {
        if (!visible() || frame) return;
        host!.dataset.suitRunning = 'true';
        frame = requestAnimationFrame(draw);
      }
      function draw(timestamp: number) {
        frame = 0;
        if (!visible()) {
          host!.dataset.suitRunning = 'false';
          return;
        }
        advanceCamera(timestamp);
        // Only changing controls or a bounded preset move schedules another frame.
        controls.update();
        if (cameraMove) schedule();
        renderer.render(scene, camera);
        host!.dataset.suitFrames = String(++frameCount);
        host!.dataset.suitRunning = frame ? 'true' : 'false';
      }
      controls.addEventListener('change', schedule);
      cleanups.push(() => controls.removeEventListener('change', schedule));

      const offset = new THREE.Vector3();
      const spherical = new THREE.Spherical();
      let fitDistance = 15;
      let cameraMove: {
        start: number;
        theta: number;
        phi: number;
        radius: number;
        toTheta: number;
        toPhi: number;
        toRadius: number;
      } | null = null;
      function advanceCamera(timestamp: number) {
        if (!cameraMove) return;
        const move = cameraMove;
        const p = Math.min(1, Math.max(0, (timestamp - move.start) / 300));
        const eased = p * p * p * (p * (p * 6 - 15) + 10);
        spherical.set(
          THREE.MathUtils.lerp(move.radius, move.toRadius, eased),
          THREE.MathUtils.lerp(move.phi, move.toPhi, eased),
          THREE.MathUtils.lerp(move.theta, move.toTheta, eased)
        );
        camera.position.copy(controls.target).add(offset.setFromSpherical(spherical));
        if (p === 1) cameraMove = null;
      }
      const cancelCameraMove = () => {
        cameraMove = null;
      };
      controls.addEventListener('start', cancelCameraMove);
      cleanups.push(() => controls.removeEventListener('start', cancelCameraMove));
      function fit(preset: boolean, animate = false) {
        const vertical = THREE.MathUtils.degToRad(camera.fov / 2);
        const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
        fitDistance =
          Math.max(
            (suit.size.y * 0.5) / Math.tan(vertical),
            (Math.max(suit.size.x, suit.size.z) * 0.5) / Math.tan(horizontal)
          ) * 1.13;
        camera.far = Math.max(100, fitDistance * 5);
        camera.updateProjectionMatrix();
        controls.minDistance = fitDistance * 0.72;
        controls.maxDistance = fitDistance * 2.1;
        cameraMove = null;
        if (preset) {
          // Flush residual drag damping once before taking the preset's start pose.
          const damping = controls.enableDamping;
          controls.enableDamping = false;
          controls.update();
          controls.enableDamping = damping;
          const yaw =
            latest.current.view === 'front' ? 0 : latest.current.view === 'back' ? Math.PI : 0.48;
          if (animate && visible() && !latest.current.paused && !motion.matches) {
            spherical.setFromVector3(offset.copy(camera.position).sub(controls.target));
            cameraMove = {
              start: performance.now(),
              theta: spherical.theta,
              phi: spherical.phi,
              radius: spherical.radius,
              toTheta:
                spherical.theta +
                Math.atan2(Math.sin(yaw - spherical.theta), Math.cos(yaw - spherical.theta)),
              toPhi: Math.atan2(1, 0.075),
              toRadius: fitDistance * Math.sqrt(1 + 0.075 * 0.075),
            };
            schedule();
            return;
          }
          offset.set(Math.sin(yaw) * fitDistance, fitDistance * 0.075, Math.cos(yaw) * fitDistance);
        } else {
          offset.copy(camera.position).sub(controls.target);
          if (offset.lengthSq() < 0.001) offset.set(0.46, 0.075, 0.88);
          offset.normalize().multiplyScalar(fitDistance);
        }
        camera.position.copy(controls.target).add(offset);
        controls.update();
      }
      let firstSize = true;
      const resize = () => {
        // Dialog entrance transforms change visual bounds without a layout resize.
        // Backing pixels must use the untransformed viewport; raycasting below
        // deliberately uses the transformed visual bounds for pointer coordinates.
        const nextWidth = Math.max(0, host.clientWidth);
        const nextHeight = Math.max(0, host.clientHeight);
        const nextDensity = window.devicePixelRatio || 1;
        if (nextWidth === width && nextHeight === height && nextDensity === density) return;
        width = nextWidth;
        height = nextHeight;
        density = nextDensity;
        if (!width || !height) {
          stop();
          return;
        }
        // Preserve native density, including fractional DPR and monitor changes.
        renderer.setPixelRatio(density);
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        if (cameraMove) advanceCamera(cameraMove.start + 300);
        fit(firstSize);
        firstSize = false;
        schedule();
      };
      const synchronize = () => {
        suit.update(latest.current.snapshot, latest.current.selected);
        controls.enabled = !latest.current.paused && visible();
        controls.enableDamping = !latest.current.paused && !motion.matches;
        if (lastView !== latest.current.view) {
          lastView = latest.current.view;
          fit(true, true);
        }
        if (latest.current.paused && cameraMove) advanceCamera(cameraMove.start + 300);
        schedule();
      };
      syncScene.current = synchronize;
      const visibilityChanged = () => {
        controls.enabled = !latest.current.paused && visible();
        if (visible()) schedule();
        else stop();
      };
      const motionChanged = () => {
        if (motion.matches && cameraMove) advanceCamera(cameraMove.start + 300);
        controls.enableDamping = !latest.current.paused && !motion.matches;
        schedule();
      };
      const observer = new ResizeObserver(resize);
      observer.observe(host);
      cleanups.push(() => observer.disconnect());
      const intersection = new IntersectionObserver(([entry]) => {
        inView = entry.isIntersecting;
        visibilityChanged();
      });
      intersection.observe(host);
      cleanups.push(() => intersection.disconnect());
      const mutation = new MutationObserver(visibilityChanged);
      mutation.observe(documentRoot, { attributes: true, attributeFilter: ['class'] });
      cleanups.push(() => mutation.disconnect());
      document.addEventListener('visibilitychange', visibilityChanged);
      window.addEventListener('resize', resize);
      motion.addEventListener('change', motionChanged);
      cleanups.push(() => {
        document.removeEventListener('visibilitychange', visibilityChanged);
        window.removeEventListener('resize', resize);
        motion.removeEventListener('change', motionChanged);
      });
      let densityQuery: MediaQueryList | null = null;
      const densityChanged = () => {
        densityQuery?.removeEventListener('change', densityChanged);
        densityQuery = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
        densityQuery.addEventListener('change', densityChanged);
        resize();
      };
      cleanups.push(() => densityQuery?.removeEventListener('change', densityChanged));

      const raycaster = new THREE.Raycaster();
      const pointer = new THREE.Vector2();
      let downX = 0;
      let downY = 0;
      let downId = -1;
      const pointerDown = (event: PointerEvent) => {
        if (latest.current.paused) return;
        downX = event.clientX;
        downY = event.clientY;
        downId = event.pointerId;
        canvas.style.cursor = 'grabbing';
      };
      const pointerUp = (event: PointerEvent) => {
        canvas.style.cursor = 'grab';
        if (event.pointerId !== downId || latest.current.paused) return;
        downId = -1;
        if (Math.hypot(event.clientX - downX, event.clientY - downY) > 5) return;
        const rect = canvas.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        pointer.set(
          ((event.clientX - rect.left) / rect.width) * 2 - 1,
          1 - ((event.clientY - rect.top) / rect.height) * 2
        );
        raycaster.setFromCamera(pointer, camera);
        const hit = raycaster.intersectObjects(suit.pickable, false)[0];
        if (hit) latest.current.onSelect(hit.object.userData.suitRegion as SuitRegion);
      };
      const pointerCancel = () => {
        downId = -1;
        canvas.style.cursor = 'grab';
      };
      const keyDown = (event: KeyboardEvent) => {
        if (latest.current.paused || !visible()) return;
        if (
          !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', '_'].includes(
            event.key
          )
        )
          return;
        event.preventDefault();
        event.stopPropagation();
        cancelCameraMove();
        offset.copy(camera.position).sub(controls.target);
        spherical.setFromVector3(offset);
        if (event.key === 'ArrowLeft') spherical.theta -= 0.12;
        if (event.key === 'ArrowRight') spherical.theta += 0.12;
        if (event.key === 'ArrowUp') spherical.phi -= 0.1;
        if (event.key === 'ArrowDown') spherical.phi += 0.1;
        if (event.key === '+' || event.key === '=') spherical.radius *= 0.92;
        if (event.key === '-' || event.key === '_') spherical.radius *= 1.08;
        spherical.phi = THREE.MathUtils.clamp(
          spherical.phi,
          controls.minPolarAngle,
          controls.maxPolarAngle
        );
        spherical.radius = THREE.MathUtils.clamp(
          spherical.radius,
          controls.minDistance,
          controls.maxDistance
        );
        camera.position.copy(controls.target).add(offset.setFromSpherical(spherical));
        controls.update();
        schedule();
      };
      canvas.addEventListener('pointerdown', pointerDown);
      canvas.addEventListener('pointerup', pointerUp);
      canvas.addEventListener('pointercancel', pointerCancel);
      canvas.addEventListener('keydown', keyDown);
      cleanups.push(() => {
        canvas.removeEventListener('pointerdown', pointerDown);
        canvas.removeEventListener('pointerup', pointerUp);
        canvas.removeEventListener('pointercancel', pointerCancel);
        canvas.removeEventListener('keydown', keyDown);
      });
      const lost = (event: Event) => {
        event.preventDefault();
        contextLost = true;
        host.dataset.suitRenderer = 'unavailable';
        stop();
        setUnavailable(true);
      };
      const restored = () => {
        // Render-target reflections are generated GPU data. Recreate them, shadows
        // and controls together instead of sampling an emptied PMREM texture.
        setAttempt((value) => value + 1);
      };
      canvas.addEventListener('webglcontextlost', lost);
      canvas.addEventListener('webglcontextrestored', restored);
      cleanups.push(() => {
        canvas.removeEventListener('webglcontextlost', lost);
        canvas.removeEventListener('webglcontextrestored', restored);
      });
      densityChanged();
      synchronize();
    } catch {
      release();
      host.dataset.suitRenderer = 'unavailable';
      setUnavailable(true);
    }
    return release;
  }, [attempt]);

  useEffect(() => {
    syncScene.current?.();
  }, [props.snapshot, props.selected, props.view, props.paused]);

  return (
    <div
      ref={mount}
      data-suit-renderer="initializing"
      data-suit-running="false"
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      {unavailable && (
        <div
          role="status"
          style={{
            position: 'absolute',
            inset: 0,
            display: 'grid',
            placeContent: 'center',
            gap: 16,
            padding: 32,
            textAlign: 'center',
            background: '#08121cee',
            color: '#a5c3ce',
            zIndex: 1,
          }}
        >
          <p>
            3D rendering is unavailable. Region diagnostics and simulated telemetry remain
            available.
          </p>
          <button
            type="button"
            onClick={() => setAttempt((value) => value + 1)}
            style={{
              border: '1px solid #438399',
              borderRadius: 6,
              padding: '8px 12px',
              color: '#b4edff',
              background: '#102836',
              cursor: 'pointer',
            }}
          >
            Retry 3D renderer
          </button>
        </div>
      )}
    </div>
  );
}
