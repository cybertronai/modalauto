import './styles.css';
import * as THREE from 'three';

const root = document.querySelector('#app');
root.innerHTML = `
  <main class="stage">
    <div class="hud">
      <div class="phase" id="turnHud">
        <div id="phaseName">Loading policy</div>
        <div id="phaseStep">-- / --</div>
      </div>
    </div>
    <canvas id="scene" aria-label="Hide-and-seek fort-building animation"></canvas>
  </main>
`;

const canvas = document.querySelector('#scene');
const phaseName = document.querySelector('#phaseName');
const phaseStep = document.querySelector('#phaseStep');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.08;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xd8dde0);
scene.fog = new THREE.Fog(0xd8dde0, 24, 46);

const camera = new THREE.PerspectiveCamera(39, 16 / 9, 0.1, 100);
camera.position.set(5.8, 7.2, 7.4);
camera.lookAt(0.15, 0, 0.1);

const hemi = new THREE.HemisphereLight(0xffffff, 0xb1bcc3, 2.15);
scene.add(hemi);

const sun = new THREE.DirectionalLight(0xffffff, 4.8);
sun.position.set(-6.5, 12, 8.5);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
sun.shadow.camera.left = -14;
sun.shadow.camera.right = 14;
sun.shadow.camera.top = 14;
sun.shadow.camera.bottom = -14;
scene.add(sun);

const coolFill = new THREE.DirectionalLight(0xa7d9ff, 0.8);
coolFill.position.set(7, 5, -7);
scene.add(coolFill);

const materials = {
  floor: new THREE.MeshStandardMaterial({ color: 0xcfd4d7, roughness: 0.46, metalness: 0.02 }),
  grid: new THREE.LineBasicMaterial({ color: 0x68737b, transparent: true, opacity: 0.32 }),
  wall: new THREE.MeshStandardMaterial({ color: 0xe7ecef, roughness: 0.55 }),
  city: new THREE.MeshStandardMaterial({ color: 0xd9dee1, roughness: 0.64 }),
  box: new THREE.MeshStandardMaterial({ color: 0xf2bf13, roughness: 0.45 }),
  ramp: new THREE.MeshStandardMaterial({ color: 0xd69d00, roughness: 0.52 }),
  blue: new THREE.MeshPhysicalMaterial({
    color: 0x32b9ec,
    emissive: 0x0d7fa4,
    emissiveIntensity: 0.2,
    roughness: 0.22,
    clearcoat: 0.6
  }),
  red: new THREE.MeshPhysicalMaterial({
    color: 0xff6248,
    emissive: 0xc82018,
    emissiveIntensity: 0.22,
    roughness: 0.22,
    clearcoat: 0.6
  }),
  eye: new THREE.MeshBasicMaterial({ color: 0xffffff }),
  blueGlow: new THREE.MeshBasicMaterial({ color: 0x6bd9ff, transparent: true, opacity: 0.18, depthWrite: false }),
  redGlow: new THREE.MeshBasicMaterial({ color: 0xff8a72, transparent: true, opacity: 0.2, depthWrite: false }),
  blueSight: new THREE.MeshBasicMaterial({
    color: 0x5fd8f6,
    transparent: true,
    opacity: 0.26,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.NormalBlending
  }),
  redSight: new THREE.MeshBasicMaterial({
    color: 0xff4f5a,
    transparent: true,
    opacity: 0.28,
    depthWrite: false,
    side: THREE.DoubleSide,
    blending: THREE.NormalBlending
  }),
  fog: new THREE.MeshBasicMaterial({
    color: 0x6f7d88,
    transparent: true,
    opacity: 0.08,
    depthWrite: false,
    side: THREE.DoubleSide
  }),
  lock: new THREE.MeshBasicMaterial({ color: 0xf7fbff, transparent: true, opacity: 0.92 }),
  logo: new THREE.LineBasicMaterial({ color: 0xa7b0b6, transparent: true, opacity: 0.36 })
};

const world = new THREE.Group();
scene.add(world);
const approximateStatic = [];
const simStatic = new THREE.Group();
world.add(simStatic);

function shadow(mesh) {
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function roundedShape(w, d, r) {
  const x = -w / 2;
  const z = -d / 2;
  const s = new THREE.Shape();
  s.moveTo(x + r, z);
  s.lineTo(x + w - r, z);
  s.quadraticCurveTo(x + w, z, x + w, z + r);
  s.lineTo(x + w, z + d - r);
  s.quadraticCurveTo(x + w, z + d, x + w - r, z + d);
  s.lineTo(x + r, z + d);
  s.quadraticCurveTo(x, z + d, x, z + d - r);
  s.lineTo(x, z + r);
  s.quadraticCurveTo(x, z, x + r, z);
  return s;
}

function block(w, h, d, material, r = 0.02) {
  const geo = new THREE.ExtrudeGeometry(roundedShape(w, d, r), {
    depth: h,
    bevelEnabled: true,
    bevelSize: r,
    bevelThickness: r,
    bevelSegments: 2
  });
  geo.rotateX(Math.PI / 2);
  geo.translate(0, h / 2, 0);
  return shadow(new THREE.Mesh(geo, material));
}

function addWall(x, z, w, d, h = 0.88) {
  const mesh = block(w, h, d, materials.wall, 0.018);
  mesh.position.set(x, 0, z);
  world.add(mesh);
  approximateStatic.push(mesh);
  return mesh;
}

function addCityBlock(x, z, w, d, h) {
  const mesh = block(w, h, d, materials.city, 0.025);
  mesh.position.set(x, 0, z);
  world.add(mesh);
  approximateStatic.push(mesh);
}

function makeAgent(material, glowMaterial) {
  const group = new THREE.Group();
  const aura = new THREE.Mesh(new THREE.CircleGeometry(0.47, 48), glowMaterial.clone());
  aura.rotation.x = -Math.PI / 2;
  aura.position.y = 0.025;

  const body = shadow(new THREE.Mesh(new THREE.CapsuleGeometry(0.17, 0.38, 8, 16), material));
  body.position.y = 0.36;

  const head = shadow(new THREE.Mesh(new THREE.SphereGeometry(0.29, 36, 22), material));
  head.scale.set(1.08, 0.92, 1.04);
  head.position.y = 0.8;

  const face = new THREE.Group();
  const eyeA = new THREE.Mesh(new THREE.SphereGeometry(0.06, 18, 12), materials.eye);
  const eyeB = eyeA.clone();
  eyeA.scale.set(1.18, 0.72, 0.45);
  eyeB.scale.copy(eyeA.scale);
  eyeA.position.set(-0.105, 0, 0);
  eyeB.position.set(0.105, 0, 0);
  face.position.set(0, 0.86, 0.27);
  face.add(eyeA, eyeB);

  const armGeo = new THREE.CapsuleGeometry(0.05, 0.25, 6, 10);
  const armA = shadow(new THREE.Mesh(armGeo, material));
  const armB = shadow(new THREE.Mesh(armGeo, material));
  armA.position.set(-0.21, 0.38, 0.02);
  armB.position.set(0.21, 0.38, 0.02);
  armA.rotation.z = 0.45;
  armB.rotation.z = -0.45;

  const ringGeo = new THREE.TorusGeometry(0.28, 0.022, 10, 52);
  const ringA = new THREE.Mesh(ringGeo, material);
  const ringB = new THREE.Mesh(ringGeo, material);
  ringA.position.y = 0.15;
  ringB.position.y = 0.27;
  ringA.rotation.x = Math.PI / 2;
  ringB.rotation.x = Math.PI / 2;

  group.add(aura, body, armA, armB, ringA, ringB, head, face);
  group.userData = { aura, head, armA, armB, ringA, ringB, face };
  return group;
}

function makeSightFan(material, radius = 2.7, fov = Math.PI * 0.38) {
  const shape = new THREE.Shape();
  shape.moveTo(0, 0);
  for (let i = 0; i <= 28; i++) {
    const a = -fov / 2 + (i / 28) * fov;
    shape.lineTo(Math.sin(a) * radius, Math.cos(a) * radius);
  }
  shape.lineTo(0, 0);
  const mesh = new THREE.Mesh(new THREE.ShapeGeometry(shape), material.clone());
  mesh.rotation.x = Math.PI / 2;
  mesh.position.y = 0.032;
  mesh.renderOrder = 4;
  mesh.userData.baseOpacity = material.opacity;
  return mesh;
}

function makeLockStraps() {
  const straps = new THREE.Group();
  const strapA = new THREE.Mesh(new THREE.BoxGeometry(0.055, 0.04, 1.18), materials.lock.clone());
  const strapB = strapA.clone();
  strapA.rotation.y = Math.PI / 4;
  strapB.rotation.y = -Math.PI / 4;
  strapA.position.y = 0.965;
  strapB.position.y = 0.968;
  straps.add(strapA, strapB);
  straps.userData.baseOpacity = 0.92;
  return straps;
}

function makeLockIcon() {
  const iconCanvas = document.createElement('canvas');
  iconCanvas.width = 96;
  iconCanvas.height = 96;
  const ctx = iconCanvas.getContext('2d');
  ctx.clearRect(0, 0, 96, 96);
  ctx.fillStyle = 'rgba(54,64,72,0.78)';
  ctx.beginPath();
  ctx.roundRect(14, 14, 68, 68, 18);
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,0.98)';
  ctx.fillStyle = 'rgba(255,255,255,0.98)';
  ctx.lineWidth = 8;
  ctx.lineCap = 'round';
  ctx.beginPath();
  ctx.arc(48, 43, 17, Math.PI, 0, false);
  ctx.stroke();
  ctx.beginPath();
  ctx.roundRect(28, 41, 40, 34, 6);
  ctx.fill();
  const texture = new THREE.CanvasTexture(iconCanvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: texture,
    transparent: true,
    opacity: 0,
    depthTest: true,
    depthWrite: false
  }));
  sprite.scale.set(0.52, 0.52, 0.52);
  sprite.userData.baseOpacity = 0.98;
  return sprite;
}

function setGroupOpacity(group, opacity) {
  group.traverse((obj) => {
    if (obj.material) obj.material.opacity = opacity;
  });
}

function samplePath(path, t) {
  const n = path.length;
  const scaled = (t % 1) * n;
  const i = Math.floor(scaled);
  const local = scaled - i;
  const a = path[i];
  const b = path[(i + 1) % n];
  const s = local * local * (3 - 2 * local);
  return {
    x: a[0] + (b[0] - a[0]) * s,
    z: a[1] + (b[1] - a[1]) * s,
    dx: b[0] - a[0],
    dz: b[1] - a[1]
  };
}

function addTileMark(x, z, size = 0.17) {
  const g = new THREE.Group();
  for (let i = 0; i < 4; i++) {
    const curve = new THREE.EllipseCurve(0, 0, size, size * 0.5, 0.15, Math.PI * 1.23, false, 0);
    const pts = curve.getPoints(22).map((p) => new THREE.Vector3(p.x, 0.018, p.y));
    const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), materials.logo);
    line.rotation.y = i * Math.PI / 2;
    g.add(line);
  }
  g.position.set(x, 0, z);
  world.add(g);
}

function makeRamp() {
  const w = 1.06;
  const d = 0.82;
  const h = 0.54;
  const vertices = new Float32Array([
    -w / 2, 0, -d / 2,
     w / 2, 0, -d / 2,
    -w / 2, 0,  d / 2,
     w / 2, 0,  d / 2,
    -w / 2, h,  d / 2,
     w / 2, h,  d / 2
  ]);
  const indices = [
    0, 2, 1, 1, 2, 3,
    2, 4, 3, 3, 4, 5,
    0, 4, 2, 0, 1, 4, 1, 5, 4,
    1, 3, 5
  ];
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(vertices, 3));
  geo.setIndex(indices);
  geo.computeVertexNormals();
  return shadow(new THREE.Mesh(geo, materials.ramp));
}

function makeLowBarrier() {
  return block(1.65, 0.38, 0.24, materials.wall, 0.022);
}

const floorSize = 10.8;
const floor = shadow(new THREE.Mesh(new THREE.BoxGeometry(floorSize, 0.15, floorSize), materials.floor));
floor.position.y = -0.075;
world.add(floor);
approximateStatic.push(floor);

const fogWash = new THREE.Mesh(new THREE.PlaneGeometry(floorSize * 0.98, floorSize * 0.98), materials.fog);
fogWash.rotation.x = -Math.PI / 2;
fogWash.position.y = 0.018;
fogWash.renderOrder = 2;
world.add(fogWash);

for (let i = -floorSize / 2; i <= floorSize / 2 + 0.001; i += 0.58) {
  const vertical = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(i, 0.012, -floorSize / 2),
    new THREE.Vector3(i, 0.012, floorSize / 2)
  ]);
  const horizontal = new THREE.BufferGeometry().setFromPoints([
    new THREE.Vector3(-floorSize / 2, 0.012, i),
    new THREE.Vector3(floorSize / 2, 0.012, i)
  ]);
  world.add(new THREE.Line(vertical, materials.grid), new THREE.Line(horizontal, materials.grid));
}

for (const [x, z] of [[-4.0, -3.0], [-2.3, 2.6], [0.9, -1.2], [2.3, 2.9], [3.8, -3.1], [-0.4, 4.1], [-4.5, 1.2]]) {
  addTileMark(x, z);
}

addWall(0, -5.55, 11.25, 0.34);
addWall(0, 5.55, 11.25, 0.34);
addWall(-5.55, 0, 0.34, 11.25);
addWall(5.55, 0, 0.34, 11.25);

addWall(1.35, -2.0, 0.28, 4.7, 0.76);
addWall(3.65, 0.9, 4.65, 0.28, 0.76);
addWall(0.25, 1.0, 0.28, 1.35, 0.76);
addWall(0.95, -0.15, 0.28, 1.25, 0.76);

for (let ring = 0; ring < 2; ring++) {
  for (let x = -7.2; x <= 7.2; x += 0.78) {
    for (const z of [-6.65 - ring * 0.65, 6.65 + ring * 0.65]) {
      if (Math.abs(x) > 5.75 || Math.abs(x) < 5.2) addCityBlock(x, z, 0.6, 0.58, 0.24 + ((x * 17 + z * 9) % 7) * 0.055);
    }
  }
  for (let z = -6.2; z <= 6.2; z += 0.78) {
    for (const x of [-6.65 - ring * 0.65, 6.65 + ring * 0.65]) {
      if (Math.abs(z) > 5.75 || Math.abs(z) < 5.2) addCityBlock(x, z, 0.58, 0.6, 0.25 + ((z * 13 + x * 5) % 8) * 0.05);
    }
  }
}

const ramp = makeRamp();
ramp.position.set(-4.15, 0.02, 2.05);
ramp.rotation.y = -0.25;
world.add(ramp);

const boxA = block(0.92, 0.92, 0.92, materials.box, 0.035);
const boxB = block(0.92, 0.92, 0.92, materials.box, 0.035);
const boxC = block(0.88, 0.88, 0.88, materials.box, 0.035);
const rampDefense = makeRamp();
const movableBarrier = makeLowBarrier();
world.add(boxA, boxB, boxC, rampDefense, movableBarrier);

const lockIconA = makeLockIcon();
const lockIconB = makeLockIcon();
world.add(lockIconA, lockIconB);

const hiderA = makeAgent(materials.blue, materials.blueGlow);
const hiderB = makeAgent(materials.blue, materials.blueGlow);
const seekerA = makeAgent(materials.red, materials.redGlow);
const seekerB = makeAgent(materials.red, materials.redGlow);
world.add(hiderA, hiderB, seekerA, seekerB);

hiderA.userData.vision = makeSightFan(materials.blueSight, 2.25, Math.PI * 0.36);
hiderB.userData.vision = makeSightFan(materials.blueSight, 2.25, Math.PI * 0.36);
seekerA.userData.vision = makeSightFan(materials.redSight, 3.05, Math.PI * 0.32);
seekerB.userData.vision = makeSightFan(materials.redSight, 3.05, Math.PI * 0.32);
hiderA.add(hiderA.userData.vision);
hiderB.add(hiderB.userData.vision);
seekerA.add(seekerA.userData.vision);
seekerB.add(seekerB.userData.vision);

const hiderPathA = [[3.75, -3.15], [2.9, -2.7], [2.85, -1.35], [3.75, -1.25]];
const hiderPathB = [[2.45, -4.05], [1.65, -3.55], [1.9, -2.4], [2.8, -2.25]];
const seekerPathA = [[0.55, 3.85], [0.85, 3.35], [1.25, 2.95], [0.55, 3.85]];
const seekerPathB = [[2.15, 4.15], [2.45, 3.52], [2.9, 3.15], [2.15, 4.15]];

const clock = new THREE.Clock();
const replay = {
  data: null,
  status: 'loading'
};
const replayAgents = [hiderA, hiderB, seekerA, seekerB];
const replayBoxes = [boxA, boxB, boxC];
const replayRamps = [rampDefense, ramp];
boxC.visible = false;

fetch('/rollouts/hide_and_seek_quadrant_seed0.json')
  .then((res) => {
    if (!res.ok) throw new Error(`rollout HTTP ${res.status}`);
    return res.json();
  })
  .then((data) => {
    replay.data = data;
    replay.status = 'ready';
    buildSimStatic(data.model);
  })
  .catch((err) => {
    replay.status = 'failed';
    console.error('Failed to load rollout replay', err);
  });

function phaseBlend(t, start, end) {
  return THREE.MathUtils.smoothstep(t, start, end);
}

function setAgent(agent, path, t, active, seed, lookTarget = null) {
  const p = samplePath(path, t);
  setAgentPose(agent, p.x, p.z, active, seed, lookTarget, p.dx, p.dz);
}

function setAgentPose(agent, x, z, active, seed, lookTarget = null, dx = 0, dz = 1) {
  agent.position.set(x, 0.02 + Math.sin(clock.elapsedTime * 5 + seed) * 0.018, z);
  let gazeAngle = 0;
  if (lookTarget) {
    const worldGaze = Math.atan2(lookTarget.x - x, lookTarget.z - z);
    agent.rotation.y = worldGaze;
  } else {
    const worldGaze = Math.atan2(dx, dz);
    agent.rotation.y = worldGaze;
  }
  agent.userData.face.position.x = Math.sin(gazeAngle) * 0.25;
  agent.userData.face.position.z = Math.cos(gazeAngle) * 0.25;
  agent.userData.face.rotation.y = gazeAngle;
  const pulse = active ? 1 : 0.35;
  agent.userData.aura.material.opacity = (agent.userData.aura.material.color.getHex() === 0x6bd9ff ? 0.16 : 0.18) * pulse;
  if (agent.userData.vision) {
    agent.userData.vision.rotation.x = Math.PI / 2;
    agent.userData.vision.rotation.z = gazeAngle;
    agent.userData.vision.material.opacity = agent.userData.vision.userData.baseOpacity * (active ? 1 : 0.32);
    agent.userData.vision.scale.set(1, 1 + Math.sin(clock.elapsedTime * 2.4 + seed) * 0.035, 1);
  }
  agent.userData.aura.scale.setScalar(1 + Math.sin(clock.elapsedTime * 3 + seed) * 0.07);
  agent.userData.head.scale.y = 0.92 + Math.sin(clock.elapsedTime * 4.6 + seed) * 0.03;
  const shove = lookTarget ? 0.72 : 0;
  agent.userData.armA.rotation.x = shove + Math.sin(clock.elapsedTime * 5.2 + seed) * 0.3 * pulse;
  agent.userData.armB.rotation.x = shove - Math.sin(clock.elapsedTime * 5.2 + seed) * 0.3 * pulse;
  agent.userData.ringA.rotation.z = clock.elapsedTime * 1.3 + seed;
  agent.userData.ringB.rotation.z = -clock.elapsedTime * 1.1 + seed;
}

function lerpVec2(a, b, t) {
  const s = THREE.MathUtils.smoothstep(t, 0, 1);
  return { x: a.x + (b.x - a.x) * s, z: a.z + (b.z - a.z) * s };
}

function setBox(mesh, a, b, t, rotA = 0, rotB = 0) {
  const p = lerpVec2(a, b, t);
  mesh.position.set(p.x, 0, p.z);
  mesh.rotation.y = rotA + (rotB - rotA) * THREE.MathUtils.smoothstep(t, 0, 1);
  return p;
}

function setRamp(mesh, a, b, t, rotA = 0, rotB = 0) {
  const p = lerpVec2(a, b, t);
  mesh.position.set(p.x, 0.02, p.z);
  mesh.rotation.y = rotA + (rotB - rotA) * THREE.MathUtils.smoothstep(t, 0, 1);
  return p;
}

function simToScene(pos) {
  return {
    x: pos[0] - 3,
    z: pos[1] - 3,
    y: pos[2] || 0
  };
}

function materialFromRgba(rgba, fallback) {
  if (!rgba || rgba.length < 3) return fallback;
  const color = new THREE.Color(rgba[0], rgba[1], rgba[2]);
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.55,
    transparent: rgba[3] < 1,
    opacity: Math.max(0.16, rgba[3])
  });
}

function buildSimStatic(model) {
  if (!model || !Array.isArray(model.geoms)) return;
  approximateStatic.forEach((mesh) => { mesh.visible = false; });
  simStatic.clear();

  for (const geom of model.geoms) {
    if (geom.name === 'floor0') {
      const size = geom.size;
      const mesh = shadow(new THREE.Mesh(
        new THREE.BoxGeometry(size[0] * 2, 0.12, size[1] * 2),
        materials.floor
      ));
      const p = simToScene(geom.pos);
      mesh.position.set(p.x, -0.06, p.z);
      simStatic.add(mesh);
    }

    if (geom.name && geom.name.startsWith('wall')) {
      const size = geom.size;
      const isOuter = geom.rgba && geom.rgba[3] < 0.5;
      const mesh = shadow(new THREE.Mesh(
        new THREE.BoxGeometry(size[0] * 2, isOuter ? 0.95 : size[2] * 2, size[1] * 2),
        materials.wall
      ));
      const p = simToScene(geom.pos);
      mesh.position.set(p.x, isOuter ? 0.475 : p.y, p.z);
      simStatic.add(mesh);
    }
  }
}

function interpAngle(a, b, t) {
  let delta = b - a;
  while (delta > Math.PI) delta -= Math.PI * 2;
  while (delta < -Math.PI) delta += Math.PI * 2;
  return a + delta * t;
}

function interpRecord(a, b, t) {
  const pa = simToScene(a.pos);
  const pb = simToScene(b.pos);
  return {
    x: THREE.MathUtils.lerp(pa.x, pb.x, t),
    z: THREE.MathUtils.lerp(pa.z, pb.z, t),
    y: THREE.MathUtils.lerp(pa.y, pb.y, t),
    yaw: interpAngle(a.yaw || 0, b.yaw || 0, t)
  };
}

function replayFrame(elapsed) {
  const frames = replay.data.frames;
  const fps = 7.5;
  const scaled = (elapsed * fps) % Math.max(1, frames.length - 1);
  const i = Math.floor(scaled);
  const t = scaled - i;
  return { a: frames[i], b: frames[Math.min(i + 1, frames.length - 1)], t };
}

function updateTurnHud(frame) {
  const horizon = replay.data?.source?.env?.includes('hide_and_seek') ? 80 : Math.max(1, replay.data.frames.length - 1);
  const prepSteps = Math.round(horizon * 0.4);
  const step = frame.step || 0;
  const isBlue = step < prepSteps;
  const remaining = isBlue ? prepSteps - step : horizon - step;
  phaseName.textContent = isBlue ? 'Blue build phase' : 'Red seek phase';
  phaseStep.textContent = `${Math.max(0, remaining)} steps left`;
}

function applyReplay(elapsed) {
  const { a, b, t } = replayFrame(elapsed);
  const blueTurn = a.step < 32;
  updateTurnHud(a);
  fogWash.material.opacity = blueTurn ? 0.07 : 0.12;

  for (let i = 0; i < replayAgents.length; i++) {
    const ra = a.agents[i];
    const rb = b.agents[i] || ra;
    const agent = replayAgents[i];
    if (!ra) {
      agent.visible = false;
      continue;
    }
    agent.visible = true;
    const p = interpRecord(ra, rb, t);
    const lookTarget = {
      x: p.x + Math.sin(p.yaw),
      z: p.z + Math.cos(p.yaw)
    };
    const active = ra.team === 'hider' ? blueTurn : !blueTurn;
    setAgentPose(agent, p.x, p.z, active, i * 1.31, lookTarget);
    agent.position.y = Math.max(0.02, p.y * 0.16);
  }

  for (let i = 0; i < replayBoxes.length; i++) {
    const ra = a.boxes[i];
    const rb = b.boxes[i] || ra;
    const mesh = replayBoxes[i];
    if (!ra) {
      mesh.visible = false;
      continue;
    }
    mesh.visible = true;
    const p = interpRecord(ra, rb, t);
    mesh.position.set(p.x, 0, p.z);
    mesh.rotation.y = p.yaw;
  }

  for (let i = 0; i < replayRamps.length; i++) {
    const ra = a.ramps[i];
    const rb = b.ramps[i] || ra;
    const mesh = replayRamps[i];
    if (!ra) {
      mesh.visible = false;
      continue;
    }
    mesh.visible = true;
    const p = interpRecord(ra, rb, t);
    mesh.position.set(p.x, 0.02, p.z);
    mesh.rotation.y = p.yaw;
  }

  lockIconA.visible = false;
  lockIconB.visible = false;
  movableBarrier.visible = false;

  const camDrift = Math.sin(elapsed * 0.08) * 0.1;
  camera.position.set(4.9 + camDrift, 6.8, 6.4 - camDrift * 0.5);
  camera.lookAt(0.0, 0.05, 0.0);
}

function animate() {
  const elapsed = clock.getElapsedTime();
  if (replay.data) {
    applyReplay(elapsed);
    renderer.render(scene, camera);
    requestAnimationFrame(animate);
    return;
  }

  const cycle = (elapsed % 12) / 12;
  const blueTurn = cycle < 0.55;
  const redT = phaseBlend(cycle, 0.55, 1.0);
  const blueT = phaseBlend(cycle, 0.0, 0.55);
  const pushA = phaseBlend(blueT, 0.1, 0.55);
  const pushB = phaseBlend(blueT, 0.22, 0.72);
  const pushRamp = phaseBlend(blueT, 0.36, 0.82);
  const pushBarrier = phaseBlend(blueT, 0.18, 0.66);
  const settle = phaseBlend(blueT, 0.76, 1.0);

  const boxAPos = setBox(boxA, { x: 3.24, z: -1.35 }, { x: 2.62, z: -0.92 }, pushA, -0.08, 0.08);
  const boxBPos = setBox(boxB, { x: 2.28, z: -4.0 }, { x: 2.05, z: -3.35 }, pushB, 0.04, -0.06);
  const rampDefensePos = setRamp(rampDefense, { x: 3.95, z: -3.0 }, { x: 3.2, z: -2.15 }, pushRamp, 1.12, 0.18);
  const barrierPos = setBox(movableBarrier, { x: 2.8, z: -2.35 }, { x: 2.08, z: -2.04 }, pushBarrier, -0.08, -0.02);
  const lockedA = phaseBlend(blueT, 0.48, 0.62);
  const lockedB = phaseBlend(blueT, 0.64, 0.78);
  setGroupOpacity(lockIconA, lockedA * 0.9);
  setGroupOpacity(lockIconB, lockedB * 0.9);
  lockIconA.visible = lockedA > 0.02;
  lockIconB.visible = lockedB > 0.02;
  lockIconA.position.set(boxAPos.x, 1.38 + Math.sin(elapsed * 3.2) * 0.025, boxAPos.z);
  lockIconB.position.set(boxBPos.x, 1.38 + Math.sin(elapsed * 3.0 + 1.2) * 0.025, boxBPos.z);
  fogWash.material.opacity = blueTurn ? 0.075 : 0.12;
  boxC.position.set(-4.05 + Math.sin(elapsed * 0.5) * 0.05, 0, 2.0 + Math.cos(elapsed * 0.4) * 0.04);
  boxC.rotation.y = Math.sin(elapsed * 0.45) * 0.04;
  ramp.rotation.y = -0.25 + Math.sin(elapsed * 0.55) * 0.035;

  const hiderContactA = pushRamp < 0.72
    ? { x: rampDefensePos.x + 0.36, z: rampDefensePos.z + 0.18 }
    : { x: boxAPos.x + 0.56, z: boxAPos.z + 0.1 };
  const hiderContactB = pushBarrier < 0.82
    ? { x: barrierPos.x + 0.48, z: barrierPos.z - 0.04 }
    : { x: boxBPos.x + 0.54, z: boxBPos.z - 0.02 };
  const hiderHideA = { x: 3.58, z: -0.88 };
  const hiderHideB = { x: 2.78, z: -2.55 };
  const hiderAPos = lerpVec2(hiderContactA, hiderHideA, settle);
  const hiderBPos = lerpVec2(hiderContactB, hiderHideB, settle);

  if (blueTurn) {
    const lookA = pushRamp < 0.72 ? rampDefensePos : boxAPos;
    const lookB = pushBarrier < 0.82 ? barrierPos : boxBPos;
    setAgentPose(hiderA, hiderAPos.x, hiderAPos.z, true, 0.1, settle < 0.85 ? lookA : { x: 3.6, z: -1.85 });
    setAgentPose(hiderB, hiderBPos.x, hiderBPos.z, true, 1.7, settle < 0.85 ? lookB : { x: 2.3, z: -2.85 });
  } else {
    setAgentPose(hiderA, hiderHideA.x, hiderHideA.z, false, 0.1, { x: 1.1, z: 3.2 });
    setAgentPose(hiderB, hiderHideB.x, hiderHideB.z, false, 1.7, { x: 1.1, z: 3.2 });
  }
  setAgent(seekerA, seekerPathA, redT * 0.82 + 0.02, !blueTurn, 2.6, { x: 2.9, z: -1.9 });
  setAgent(seekerB, seekerPathB, redT * 0.78 + 0.18, !blueTurn, 4.2, { x: 2.9, z: -1.9 });

  const camDrift = Math.sin(elapsed * 0.08) * 0.18;
  camera.position.set(5.8 + camDrift, 7.2, 7.4 - camDrift * 0.6);
  camera.lookAt(0.15, 0, 0.1);

  renderer.render(scene, camera);
  requestAnimationFrame(animate);
}

function resize() {
  const w = root.clientWidth;
  const h = root.clientHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}

window.addEventListener('resize', resize);
resize();
animate();
