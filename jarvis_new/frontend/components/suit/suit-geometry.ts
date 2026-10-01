import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { SUIT_REGIONS, type SuitRegion, type SuitSnapshot } from '@/lib/suit-diagnostics';

type Contour = readonly (readonly [number, number])[];
const RED = '#a91d30';
const GOLD = '#d5ad60';
const LIMIT = (value: number) =>
  Number.isFinite(value) ? THREE.MathUtils.clamp(value, 0, 100) / 100 : 0;

/** Original procedural armor. Everything is modeled in meters, facing +Z.
 * GPU resources are tracked at construction, including selection/wear overlays. */
export function createSuitGeometry() {
  const root = new THREE.Group();
  root.name = 'Aegis articulated armor';
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const pickable: THREE.Mesh[] = [];
  const outlines: { region: SuitRegion; line: THREE.LineSegments }[] = [];
  const wear: { region: SuitRegion; line: THREE.LineSegments }[] = [];
  const tint: { region: SuitRegion; material: THREE.MeshStandardMaterial; base: THREE.Color }[] =
    [];
  const regions = {} as Record<SuitRegion, THREE.Group>;
  const red = {} as Record<SuitRegion, THREE.MeshStandardMaterial>;
  const gold = {} as Record<SuitRegion, THREE.MeshStandardMaterial>;
  const scars = {} as Record<SuitRegion, THREE.LineBasicMaterial>;
  const material = <T extends THREE.Material>(m: T): T => {
    materials.add(m);
    return m;
  };
  const geometry = <T extends THREE.BufferGeometry>(g: T): T => {
    geometries.add(g);
    return g;
  };
  const steel = material(
    new THREE.MeshStandardMaterial({ color: '#17222b', metalness: 0.83, roughness: 0.32 })
  );
  const graphite = material(
    new THREE.MeshStandardMaterial({ color: '#080e16', metalness: 0.38, roughness: 0.55 })
  );
  const silver = material(
    new THREE.MeshStandardMaterial({ color: '#a5b3bd', metalness: 0.9, roughness: 0.23 })
  );
  const light = material(
    new THREE.MeshStandardMaterial({
      color: '#d6fcff',
      emissive: '#5fe3ff',
      emissiveIntensity: 3,
      metalness: 0.15,
      roughness: 0.18,
    })
  );
  const selectedInk = material(
    new THREE.LineBasicMaterial({
      color: '#7ef0ff',
      transparent: true,
      opacity: 0.48,
      depthWrite: false,
    })
  );
  const burned = new THREE.Color('#24202a');
  const alarm = new THREE.Color('#ff6b2b');
  for (const { id } of SUIT_REGIONS) {
    const group = new THREE.Group();
    group.name = id;
    group.userData.suitRegion = id;
    root.add(group);
    regions[id] = group;
    red[id] = material(
      new THREE.MeshStandardMaterial({ color: RED, metalness: 0.72, roughness: 0.26 })
    );
    gold[id] = material(
      new THREE.MeshStandardMaterial({ color: GOLD, metalness: 0.78, roughness: 0.3 })
    );
    scars[id] = material(
      new THREE.LineBasicMaterial({
        color: '#ffb278',
        transparent: true,
        opacity: 0,
        depthWrite: false,
      })
    );
    tint.push({ region: id, material: red[id], base: red[id].color.clone() });
    tint.push({ region: id, material: gold[id], base: gold[id].color.clone() });
  }

  const mesh = (
    region: SuitRegion,
    g: THREE.BufferGeometry,
    m: THREE.Material,
    x: number,
    y: number,
    z: number,
    parent: THREE.Object3D = regions[region]
  ) => {
    const object = new THREE.Mesh(geometry(g), m);
    object.position.set(x, y, z);
    object.castShadow = true;
    object.receiveShadow = true;
    object.userData.suitRegion = region;
    parent.add(object);
    pickable.push(object);
    return object;
  };
  const ball = (
    r: SuitRegion,
    radius: number,
    x: number,
    y: number,
    z: number,
    m: THREE.Material = steel
  ) => mesh(r, new THREE.SphereGeometry(radius, 28, 18), m, x, y, z);
  const box = (
    r: SuitRegion,
    w: number,
    h: number,
    d: number,
    x: number,
    y: number,
    z: number,
    m: THREE.Material,
    radius = 0.06
  ) => mesh(r, new RoundedBoxGeometry(w, h, d, 3, radius), m, x, y, z);
  const cylinder = (
    r: SuitRegion,
    top: number,
    bottom: number,
    h: number,
    x: number,
    y: number,
    z: number,
    m: THREE.Material
  ) => mesh(r, new THREE.CylinderGeometry(top, bottom, h, 32), m, x, y, z);
  const ring = (
    r: SuitRegion,
    radius: number,
    tube: number,
    x: number,
    y: number,
    z: number,
    m: THREE.Material
  ) => mesh(r, new THREE.TorusGeometry(radius, tube, 12, 64), m, x, y, z);

  const plate = (
    r: SuitRegion,
    points: Contour,
    x: number,
    y: number,
    z: number,
    m: THREE.Material = red[r],
    depth = 0.11,
    holes: Contour[] = []
  ) => {
    const shape = new THREE.Shape(points.map(([a, b]) => new THREE.Vector2(a, b)));
    for (const hole of holes)
      shape.holes.push(new THREE.Path(hole.map(([a, b]) => new THREE.Vector2(a, b))));
    const g = new THREE.ExtrudeGeometry(shape, {
      depth,
      steps: 1,
      bevelEnabled: true,
      bevelSegments: 3,
      bevelSize: 0.035,
      bevelThickness: 0.035,
      curveSegments: 16,
    });
    const object = mesh(r, g, m, x, y, z);
    const outline = new THREE.LineSegments(geometry(new THREE.EdgesGeometry(g, 35)), selectedInk);
    outline.visible = false;
    object.add(outline);
    outlines.push({ region: r, line: outline });
    // Fixed hairline fractures become visible with damage; no runtime textures.
    if (points.length > 5 && holes.length === 0) {
      const bounds = new THREE.Box3().setFromBufferAttribute(
        g.getAttribute('position') as THREE.BufferAttribute
      );
      const w = bounds.max.x - bounds.min.x;
      const h = bounds.max.y - bounds.min.y;
      const cx = (bounds.min.x + bounds.max.x) / 2;
      const cy = (bounds.min.y + bounds.max.y) / 2;
      const vertices: number[] = [];
      for (let i = 0; i < 4; i++) {
        const sx = cx + (i % 2 ? 0.18 : -0.19) * w;
        const sy = cy + (i < 2 ? 0.18 : -0.19) * h;
        vertices.push(
          sx,
          sy,
          depth + 0.037,
          sx + 0.09 * w,
          sy - 0.065 * h,
          depth + 0.037,
          sx + 0.09 * w,
          sy - 0.065 * h,
          depth + 0.037,
          sx + 0.05 * w,
          sy - 0.19 * h,
          depth + 0.037
        );
      }
      const damage = new THREE.LineSegments(
        geometry(
          new THREE.BufferGeometry().setAttribute(
            'position',
            new THREE.Float32BufferAttribute(vertices, 3)
          )
        ),
        scars[r]
      );
      damage.visible = false;
      object.add(damage);
      wear.push({ region: r, line: damage });
    }
    return object;
  };

  // Neck and torso: separate pectoral plates, rib overlap and dorsal hardware.
  const chest = regions.chest;
  chest.position.y = 5.25;
  const body = mesh(
    'chest',
    new THREE.CapsuleGeometry(0.73, 0.78, 8, 28),
    graphite,
    0,
    -0.12,
    -0.03
  );
  body.scale.set(1.25, 1, 0.7);
  cylinder('chest', 0.25, 0.34, 0.42, 0, 1.08, 0, steel);
  for (let i = 0; i < 3; i++) cylinder('chest', 0.27, 0.27, 0.035, 0, 0.97 + i * 0.11, 0, silver);
  const breast: Contour = [
    [-0.97, 0.59],
    [-0.67, 0.82],
    [-0.12, 0.66],
    [-0.08, 0.16],
    [-0.34, -0.1],
    [-0.8, 0.03],
    [-1.02, 0.31],
  ];
  plate('chest', breast, 0, 0, 0.43);
  plate('chest', breast.map(([x, y]) => [-x, y] as const).reverse(), 0, 0, 0.43);
  plate(
    'chest',
    [
      [-0.84, -0.05],
      [-0.46, -0.24],
      [0, -0.38],
      [0.46, -0.24],
      [0.84, -0.05],
      [0.63, -0.6],
      [0.34, -0.81],
      [-0.34, -0.81],
      [-0.63, -0.6],
    ],
    0,
    0,
    0.35
  );
  for (const side of [-1, 1]) {
    const clavicle = plate(
      'chest',
      [
        [-0.39, 0.09],
        [0.32, 0.15],
        [0.4, 0.03],
        [0.08, -0.06],
        [-0.33, -0.04],
      ],
      side * 0.63,
      0.72,
      0.5,
      gold.chest,
      0.07
    );
    clavicle.rotation.z = side * -0.09;
    for (let i = 0; i < 3; i++) {
      const rib = box(
        'chest',
        0.32,
        0.14,
        0.42,
        side * 0.75,
        -0.23 - i * 0.19,
        0.02,
        gold.chest,
        0.035
      );
      rib.rotation.z = side * -0.18;
    }
  }
  for (let i = 0; i < 3; i++)
    plate(
      'chest',
      [
        [-0.49, 0.12],
        [0.49, 0.12],
        [0.4, -0.12],
        [0.17, -0.19],
        [-0.17, -0.19],
        [-0.4, -0.12],
      ],
      0,
      -0.95 - i * 0.27,
      0.21,
      i === 1 ? gold.chest : red.chest,
      0.1
    );
  box('chest', 1.2, 0.3, 0.66, 0, -1.55, -0.02, graphite);
  plate(
    'chest',
    [
      [-0.6, 0.12],
      [-0.18, 0.22],
      [0.18, 0.22],
      [0.6, 0.12],
      [0.46, -0.18],
      [0.2, -0.32],
      [-0.2, -0.32],
      [-0.46, -0.18],
    ],
    0,
    -1.59,
    0.3,
    gold.chest,
    0.12
  );
  const dorsal = plate(
    'chest',
    [
      [-0.8, 0.6],
      [-0.46, 0.78],
      [0.46, 0.78],
      [0.8, 0.6],
      [0.69, -0.5],
      [0.35, -0.77],
      [-0.35, -0.77],
      [-0.69, -0.5],
    ],
    0,
    -0.04,
    -0.48
  );
  dorsal.rotation.y = Math.PI;
  for (const side of [-1, 1]) {
    const exhaust = box('chest', 0.21, 0.74, 0.16, side * 0.5, 0.16, -0.71, steel, 0.025);
    exhaust.rotation.z = side * -0.12;
    for (let i = 0; i < 5; i++)
      box('chest', 0.23, 0.032, 0.04, side * 0.5, -0.13 + i * 0.14, -0.81, silver, 0.01);
  }

  // Helmet: sculpted red shell, pierced gold faceplate and inset optical slits.
  regions.head.position.set(0, 7.08, 0);
  const shell = ball('head', 0.52, 0, 0, -0.085, red.head);
  shell.scale.set(0.95, 1.28, 1.02);
  const mask: Contour = [
    [-0.31, 0.56],
    [0.31, 0.56],
    [0.46, 0.3],
    [0.4, -0.25],
    [0.25, -0.55],
    [-0.25, -0.55],
    [-0.4, -0.25],
    [-0.46, 0.3],
  ];
  const eyeL: Contour = [
    [-0.34, 0.14],
    [-0.065, 0.075],
    [-0.09, -0.012],
    [-0.31, 0.025],
  ];
  const eyeR = eyeL.map(([x, y]) => [-x, y] as const).reverse();
  plate('head', mask, 0, 0, 0.35, gold.head, 0.1, [eyeL, eyeR]);
  for (const eye of [eyeL, eyeR]) plate('head', eye, 0, 0, 0.43, light, 0.018);
  plate(
    'head',
    [
      [-0.065, 0.12],
      [0.065, 0.12],
      [0.105, -0.21],
      [0, -0.27],
      [-0.105, -0.21],
    ],
    0,
    0,
    0.49,
    gold.head,
    0.035
  );
  box('head', 0.3, 0.055, 0.025, 0, -0.34, 0.49, graphite, 0.012);
  plate(
    'head',
    [
      [-0.17, 0.58],
      [0.17, 0.58],
      [0.12, 0.26],
      [-0.12, 0.26],
    ],
    0,
    0,
    0.4,
    red.head,
    0.07
  );
  for (const side of [-1, 1]) {
    const ear = cylinder('head', 0.2, 0.2, 0.08, side * 0.5, -0.015, -0.04, gold.head);
    ear.rotation.z = Math.PI / 2;
    const temple = box('head', 0.13, 0.41, 0.27, side * 0.46, 0.25, -0.07, red.head, 0.055);
    temple.rotation.z = side * 0.08;
    plate(
      'head',
      [
        [-0.085, 0.17],
        [0.08, 0.13],
        [0.12, -0.2],
        [0, -0.32],
        [-0.12, -0.19],
      ],
      side * 0.37,
      -0.32,
      0.28,
      red.head,
      0.08
    );
  }

  // Arms sit slightly away from the torso so all segmented plates read in orbit.
  for (const side of [-1, 1]) {
    const r: SuitRegion = side > 0 ? 'leftArm' : 'rightArm';
    regions[r].position.set(side * 1.23, 5.98, -0.025);
    regions[r].rotation.z = side * 0.085;
    const shoulder = ball(r, 0.43, 0, -0.03, 0, red[r]);
    shoulder.scale.set(1.18, 0.88, 1.16);
    plate(
      r,
      [
        [-0.45, 0.13],
        [-0.26, 0.39],
        [0.27, 0.36],
        [0.48, 0.1],
        [0.4, -0.27],
        [-0.26, -0.31],
      ],
      0,
      0,
      0.29,
      red[r],
      0.18
    );
    plate(
      r,
      [
        [-0.43, 0.15],
        [-0.25, 0.36],
        [0.26, 0.33],
        [0.4, 0.18],
        [0.15, 0.2],
        [-0.22, 0.22],
      ],
      0,
      0.035,
      0.49,
      gold[r],
      0.05
    );
    cylinder(r, 0.26, 0.22, 0.81, 0, -0.73, 0, graphite);
    plate(
      r,
      [
        [-0.24, 0.43],
        [0.24, 0.43],
        [0.3, 0.17],
        [0.2, -0.38],
        [0, -0.46],
        [-0.22, -0.33],
        [-0.28, 0.2],
      ],
      0,
      -0.72,
      0.21
    );
    box(r, 0.12, 0.7, 0.27, side * 0.25, -0.71, 0, gold[r], 0.035);
    ball(r, 0.25, 0, -1.25, 0.015, steel);
    ring(r, 0.17, 0.045, 0, -1.25, 0.25, gold[r]);
    cylinder(r, 0.24, 0.19, 0.92, 0, -1.82, 0.065, graphite);
    plate(
      r,
      [
        [-0.28, 0.46],
        [0.28, 0.46],
        [0.34, 0.21],
        [0.24, -0.35],
        [0.12, -0.48],
        [-0.19, -0.4],
        [-0.32, 0.14],
      ],
      0,
      -1.84,
      0.24,
      red[r],
      0.17
    );
    plate(
      r,
      [
        [-0.09, 0.33],
        [0.09, 0.36],
        [0.13, -0.29],
        [0, -0.39],
        [-0.1, -0.31],
      ],
      side * 0.21,
      -1.81,
      0.42,
      gold[r],
      0.045
    );
    box(r, 0.46, 0.12, 0.48, 0, -2.33, 0.08, silver, 0.035);
    box(r, 0.44, 0.38, 0.32, 0, -2.58, 0.12, red[r], 0.09);
    for (let i = 0; i < 4; i++)
      box(
        r,
        0.092,
        0.26 + (i === 1 || i === 2 ? 0.035 : 0),
        0.19,
        -0.15 + i * 0.1,
        -2.85,
        0.15,
        red[r],
        0.038
      );
    const thumb = box(r, 0.13, 0.3, 0.19, side * -0.27, -2.64, 0.16, gold[r], 0.04);
    thumb.rotation.z = side * -0.28;
    ring(r, 0.095, 0.025, 0, -2.57, -0.055, silver);
    const palm = mesh(r, new THREE.CircleGeometry(0.073, 32), light, 0, -2.57, -0.08);
    palm.rotation.y = Math.PI;
    // Dorsal gauntlet and upper-arm plating remain modeled in the rear view.
    const rear = plate(
      r,
      [
        [-0.22, 0.38],
        [0.22, 0.38],
        [0.24, -0.28],
        [0, -0.43],
        [-0.24, -0.28],
      ],
      0,
      -1.85,
      -0.17,
      gold[r],
      0.1
    );
    rear.rotation.y = Math.PI;
  }

  // Articulated legs: pelvis pivots, cuisses, raised knee shields and broad boots.
  for (const side of [-1, 1]) {
    const r: SuitRegion = side > 0 ? 'leftLeg' : 'rightLeg';
    regions[r].position.set(side * 0.56, 3.53, 0);
    regions[r].rotation.z = side * 0.025;
    ball(r, 0.34, 0, 0, -0.04, steel);
    cylinder(r, 0.31, 0.25, 1.3, 0, -0.66, -0.025, graphite);
    plate(
      r,
      [
        [-0.32, 0.58],
        [0.28, 0.65],
        [0.4, 0.28],
        [0.3, -0.55],
        [0.12, -0.74],
        [-0.25, -0.64],
        [-0.39, 0.17],
      ],
      0,
      -0.56,
      0.25,
      red[r],
      0.16
    );
    plate(
      r,
      [
        [-0.09, 0.56],
        [0.12, 0.5],
        [0.17, -0.42],
        [0.035, -0.63],
        [-0.12, -0.41],
      ],
      side * 0.23,
      -0.54,
      0.43,
      gold[r],
      0.055
    );
    ball(r, 0.245, 0, -1.43, -0.02, steel);
    plate(
      r,
      [
        [-0.26, 0.2],
        [0, 0.32],
        [0.28, 0.18],
        [0.3, -0.07],
        [0, -0.29],
        [-0.28, -0.08],
      ],
      0,
      -1.44,
      0.31,
      gold[r],
      0.13
    );
    plate(
      r,
      [
        [-0.2, 0.14],
        [0, 0.24],
        [0.22, 0.12],
        [0.18, -0.12],
        [0, -0.21],
        [-0.19, -0.1],
      ],
      0,
      -1.44,
      0.47,
      red[r],
      0.09
    );
    cylinder(r, 0.24, 0.19, 1.28, 0, -2.21, -0.04, graphite);
    plate(
      r,
      [
        [-0.28, 0.63],
        [0.28, 0.63],
        [0.34, 0.37],
        [0.2, -0.64],
        [0, -0.77],
        [-0.23, -0.62],
        [-0.34, 0.28],
      ],
      0,
      -2.21,
      0.2,
      red[r],
      0.19
    );
    plate(
      r,
      [
        [-0.18, 0.52],
        [0.18, 0.52],
        [0.13, 0.34],
        [-0.13, 0.34],
      ],
      0,
      -2.2,
      0.41,
      gold[r],
      0.055
    );
    const calf = box(r, 0.28, 0.96, 0.31, side * 0.22, -2.21, -0.06, gold[r], 0.075);
    calf.rotation.z = side * -0.045;
    box(r, 0.59, 0.38, 1.02, 0, -3.07, 0.24, red[r], 0.12);
    box(r, 0.64, 0.11, 1.06, 0, -3.28, 0.24, graphite, 0.04);
    plate(
      r,
      [
        [-0.24, 0.08],
        [0.24, 0.08],
        [0.28, -0.07],
        [-0.28, -0.07],
      ],
      0,
      -3.08,
      0.76,
      gold[r],
      0.045
    );
    const rear = plate(
      r,
      [
        [-0.24, 0.59],
        [0.24, 0.59],
        [0.27, -0.36],
        [0.11, -0.63],
        [-0.2, -0.56],
      ],
      0,
      -0.57,
      -0.27,
      red[r],
      0.12
    );
    rear.rotation.y = Math.PI;
  }

  // Separate pickable reactor assembly and a procedural, texture-free light halo.
  regions.reactor.position.set(0, 5.47, 0.7);
  const socket = cylinder('reactor', 0.35, 0.35, 0.08, 0, 0, 0.035, steel);
  socket.rotation.x = Math.PI / 2;
  ring('reactor', 0.32, 0.047, 0, 0, 0.08, gold.reactor);
  ring('reactor', 0.248, 0.026, 0, 0, 0.105, silver);
  const reactorLight = material(
    new THREE.MeshStandardMaterial({
      color: '#c6faff',
      emissive: '#5fe3ff',
      emissiveIntensity: 3,
      roughness: 0.18,
      metalness: 0.2,
    })
  );
  mesh('reactor', new THREE.CircleGeometry(0.194, 64), reactorLight, 0, 0, 0.13);
  for (let i = 0; i < 8; i++) {
    const a = (i * Math.PI) / 4;
    const spoke = box(
      'reactor',
      0.026,
      0.088,
      0.028,
      Math.sin(a) * 0.252,
      Math.cos(a) * 0.252,
      0.135,
      steel,
      0.008
    );
    spoke.rotation.z = -a;
  }
  const haloMaterial = material(
    new THREE.ShaderMaterial({
      uniforms: { strength: { value: 0.55 }, glowColor: { value: new THREE.Color('#54dfff') } },
      vertexShader:
        'varying vec2 haloUv; void main(){haloUv=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
      fragmentShader:
        'varying vec2 haloUv; uniform vec3 glowColor; uniform float strength; void main(){float r=length(haloUv-.5)*2.;float a=pow(max(0.,1.-r),3.)*strength;gl_FragColor=vec4(glowColor,a);}',
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
  );
  const halo = mesh('reactor', new THREE.PlaneGeometry(1.24, 1.24), haloMaterial, 0, 0, 0.15);
  halo.castShadow = false;
  // Halo is decorative: clicks should hit the actual underlying reactor hardware.
  pickable.splice(pickable.indexOf(halo), 1);

  const reactorSelection = mesh(
    'reactor',
    new THREE.RingGeometry(0.381, 0.394, 96),
    material(
      new THREE.MeshBasicMaterial({
        color: '#7ef0ff',
        transparent: true,
        opacity: 0.85,
        depthWrite: false,
        toneMapped: false,
      })
    ),
    0,
    0,
    0.11
  );
  reactorSelection.visible = false;
  reactorSelection.castShadow = false;
  pickable.splice(pickable.indexOf(reactorSelection), 1);

  root.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(root);
  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  return {
    root,
    pickable,
    bounds,
    center,
    size,
    update(snapshot: SuitSnapshot, selected: SuitRegion) {
      for (const item of tint) {
        const damage = LIMIT(snapshot.damage[item.region]);
        item.material.color.copy(item.base).lerp(burned, damage * 0.76);
        item.material.roughness = 0.26 + damage * 0.5;
        item.material.emissive.copy(alarm);
        item.material.emissiveIntensity = Math.max(0, damage - 0.4) * 0.15;
      }
      for (const { id } of SUIT_REGIONS) scars[id].opacity = LIMIT(snapshot.damage[id]) * 0.86;
      for (const item of wear) item.line.visible = LIMIT(snapshot.damage[item.region]) > 0.04;
      for (const item of outlines) item.line.visible = item.region === selected;
      reactorSelection.visible = selected === 'reactor';
      const charge = LIMIT(snapshot.reactor) * (1 - LIMIT(snapshot.damage.reactor) * 0.8);
      reactorLight.emissiveIntensity = 0.25 + charge * 3;
      reactorLight.emissive.set(charge < 0.28 ? '#ff883e' : '#5fe3ff');
      haloMaterial.uniforms.strength.value = 0.15 + charge * 0.6;
      haloMaterial.uniforms.glowColor.value.set(charge < 0.28 ? '#ff883e' : '#54dfff');
      light.emissiveIntensity = 0.8 + charge * 2.2;
    },
    dispose() {
      for (const g of geometries) g.dispose();
      for (const m of materials) m.dispose();
      geometries.clear();
      materials.clear();
      root.clear();
      pickable.length = 0;
    },
  };
}
