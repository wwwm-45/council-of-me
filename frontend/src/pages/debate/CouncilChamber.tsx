import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';

import type { AgentEvolutionData, AgentOrderEntry } from '../../api/client';
import { usePrefersReducedMotion } from '../../hooks/usePrefersReducedMotion';
import { isWebGLAvailable } from '../../lib/webgl';
import { PHASE_LABEL, type ArtifactEvent, type ChatMsg, type ConvergenceMapData } from './types';

interface CouncilChamberProps {
  agents: AgentOrderEntry[];
  userDisplayName?: string | null;
  speakingAgentId: string | null;
  currentRound: number;
  currentPhase: string;
  paused: boolean;
  streaming: boolean;
  hasMessages: boolean;
  latestContent: string;
  subtitleAnchorId?: string | null;
  subtitleAnchorName?: string | null;
  replyTo: string | null;
  onUserSeatClick: () => void;
  onPause: () => void;
  onResume: () => void;
  exchangeProgress: { seq: number; min: number; max: number } | null;
  messages?: ChatMsg[];
  roundArtifacts?: Map<number, ArtifactEvent>;
  convergenceMap?: ConvergenceMapData | null;
  agentEvolutions?: Map<string, AgentEvolutionData[]>;
}

interface SeatPosition {
  id: string;
  name: string;
  isUser: boolean;
  index: number;
  x: number;
  y: number;
  /** Screen-space anchor a little above the orb where the speech bubble hangs. */
  bubbleX: number;
  bubbleY: number;
}

/** Neon agent palette — shared between the WebGL scene and the DOM overlay. */
const AGENT_COLORS = ['#00e5ff', '#7c3aed', '#10b981', '#f59e0b', '#ec4899'];
const AGENT_COLOR_HEX = [0x00e5ff, 0x7c3aed, 0x10b981, 0xf59e0b, 0xec4899];

/** World-space layout shared by the scene and the projected DOM overlay. */
const RING_RADIUS = 10;
const AGENT_Y = 0.5;
/** Matches the reference shell's speechGroup.position.y so bubbles sit above orbs. */
const BUBBLE_Y = AGENT_Y + 2.3;
const CAMERA_POSITION = new THREE.Vector3(0, 15, 25);

function cleanUserDisplayName(value: string | null | undefined): string {
  return (value ?? '').trim().slice(0, 40);
}

/**
 * Projects the world-space ring layout into screen-space percentages so the
 * DOM overlay (labels, speech bubble, hit areas) sits on top of the matching
 * glowing 3D orbs. Uses a static camera that mirrors the scene's base camera.
 */
function computeSeats(
  agents: AgentOrderEntry[],
  userDisplayName: string | null | undefined,
  aspect: number,
): SeatPosition[] {
  const camera = new THREE.PerspectiveCamera(45, aspect > 0 ? aspect : 16 / 9, 0.1, 100);
  camera.position.copy(CAMERA_POSITION);
  camera.lookAt(0, 0, 0);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();

  const project = (x: number, y: number, z: number) => {
    const v = new THREE.Vector3(x, y, z).project(camera);
    return { x: (v.x * 0.5 + 0.5) * 100, y: (-v.y * 0.5 + 0.5) * 100 };
  };

  const seats: SeatPosition[] = [];
  const count = Math.max(agents.length, 1);

  agents.forEach((agent, index) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * index) / count;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const screen = project(cos * RING_RADIUS, AGENT_Y, sin * RING_RADIUS);
    const bubble = project(cos * RING_RADIUS, BUBBLE_Y, sin * RING_RADIUS);
    seats.push({
      id: agent.agentId,
      name: agent.agentName,
      isUser: false,
      index,
      x: screen.x,
      y: screen.y,
      bubbleX: bubble.x,
      bubbleY: bubble.y,
    });
  });

  const userScreen = project(0, AGENT_Y, RING_RADIUS);
  const userBubble = project(0, BUBBLE_Y, RING_RADIUS);
  seats.push({
    id: '__user__',
    name: cleanUserDisplayName(userDisplayName) || '我',
    isUser: true,
    index: agents.length,
    x: userScreen.x,
    y: userScreen.y,
    bubbleX: userBubble.x,
    bubbleY: userBubble.y,
  });

  return seats;
}

type ReplyTargetTo = { type: 'agent'; index: number } | { type: 'user' } | { type: 'center' };

interface ChamberSceneApi {
  setSpeaking(index: number | null): void;
  setReplyTarget(to: ReplyTargetTo | null): void;
  setPhase(phase: 'mapping' | 'default'): void;
  setReducedMotion(value: boolean): void;
  resize(width: number, height: number): void;
  dispose(): void;
}

interface AgentObject {
  group: THREE.Group;
  mat: THREE.MeshStandardMaterial;
  sprite: THREE.Sprite;
  coreMesh: THREE.Mesh;
  shellMesh: THREE.Mesh;
  trail: THREE.Group;
  ripple: THREE.Mesh;
  angle: number;
  baseColor: number;
  currentScale: number;
}

function makeRadialSpriteTexture(innerStop: number, color: string): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext('2d')!;
  const grad = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, '#ffffff');
  grad.addColorStop(innerStop, color);
  grad.addColorStop(1, 'transparent');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

/**
 * Builds the immersive Council Chamber scene — a faithful port of the design
 * shell in 辩论界面front.zip (public/council-chamber.html). Returns an
 * imperative handle the React layer drives from live debate state.
 */
function buildChamberScene(
  canvas: HTMLCanvasElement,
  options: { agentColors: number[]; agentCount: number; reducedMotion: boolean },
): ChamberSceneApi {
  const { agentColors, agentCount } = options;
  let reducedMotion = options.reducedMotion;

  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  let width = canvas.clientWidth || window.innerWidth;
  let height = canvas.clientHeight || window.innerHeight;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x000205);
  scene.fog = new THREE.FogExp2(0x000205, 0.02);

  const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
  camera.position.copy(CAMERA_POSITION);
  camera.lookAt(0, 0, 0);

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance' });
  renderer.setPixelRatio(dpr);
  renderer.setSize(width, height, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  // Softened from 1.0 — the orbs were reading as too bright.
  renderer.toneMappingExposure = 0.85;

  const composer = new EffectComposer(renderer);
  composer.setSize(width, height);
  composer.addPass(new RenderPass(scene, camera));
  // Bloom strength softened from 0.7 to dial back the glow intensity.
  const bloomPass = new UnrealBloomPass(new THREE.Vector2(width, height), 0.45, 0.5, 0.1);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());

  const disposables: { dispose: () => void }[] = [];
  const track = <T extends { dispose: () => void }>(obj: T): T => {
    disposables.push(obj);
    return obj;
  };

  // ── Deep-space twinkling starfield ───────────────────────────────────────
  const bgCount = 1500;
  const bgGeom = track(new THREE.BufferGeometry());
  const bgPos = new Float32Array(bgCount * 3);
  const bgColors = new Float32Array(bgCount * 3);
  const bgBaseColors = new Float32Array(bgCount * 3);
  const bgPhases = new Float32Array(bgCount);
  const bgSpeeds = new Float32Array(bgCount);
  const colorOpts = [
    new THREE.Color(0xfff5e6),
    new THREE.Color(0xffebcd),
    new THREE.Color(0xdce8ff),
    new THREE.Color(0xefccff),
  ];
  for (let i = 0; i < bgCount; i += 1) {
    bgPos[i * 3] = (Math.random() - 0.5) * 160;
    bgPos[i * 3 + 1] = (Math.random() - 0.5) * 160;
    bgPos[i * 3 + 2] = (Math.random() - 0.5) * 160;
    const c = colorOpts[Math.floor(Math.random() * colorOpts.length)];
    bgBaseColors[i * 3] = c.r;
    bgBaseColors[i * 3 + 1] = c.g;
    bgBaseColors[i * 3 + 2] = c.b;
    bgColors[i * 3] = c.r;
    bgColors[i * 3 + 1] = c.g;
    bgColors[i * 3 + 2] = c.b;
    bgPhases[i] = Math.random() * Math.PI * 2;
    bgSpeeds[i] = 0.5 + Math.random() * 1.5;
  }
  bgGeom.setAttribute('position', new THREE.BufferAttribute(bgPos, 3));
  bgGeom.setAttribute('color', new THREE.BufferAttribute(bgColors, 3));
  const bgMat = track(new THREE.PointsMaterial({
    size: 0.25,
    vertexColors: true,
    transparent: true,
    opacity: 0.8,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  }));
  const bgParticles = new THREE.Points(bgGeom, bgMat);
  scene.add(bgParticles);

  // ── Nebula glow plate ────────────────────────────────────────────────────
  const nebulaCanvas = document.createElement('canvas');
  nebulaCanvas.width = 256;
  nebulaCanvas.height = 256;
  const nCtx = nebulaCanvas.getContext('2d')!;
  const nGrad = nCtx.createRadialGradient(128, 128, 0, 128, 128, 128);
  nGrad.addColorStop(0, 'rgba(30, 40, 80, 0.2)');
  nGrad.addColorStop(1, 'transparent');
  nCtx.fillStyle = nGrad;
  nCtx.fillRect(0, 0, 256, 256);
  const nebulaTex = track(new THREE.CanvasTexture(nebulaCanvas));
  const nebulaMat = track(new THREE.MeshBasicMaterial({ map: nebulaTex, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
  const nebulaMesh = new THREE.Mesh(track(new THREE.PlaneGeometry(100, 100)), nebulaMat);
  nebulaMesh.position.y = -5;
  nebulaMesh.rotation.x = -Math.PI / 2;
  scene.add(nebulaMesh);

  // ── Astrolabe ring platform ──────────────────────────────────────────────
  const ringGroup = new THREE.Group();
  scene.add(ringGroup);
  const scalableRingGroup = new THREE.Group();
  ringGroup.add(scalableRingGroup);

  const ringRadii = [RING_RADIUS * 0.8, RING_RADIUS, RING_RADIUS * 1.05];
  const segmentedArcs: { mesh: THREE.Mesh; speed: number }[] = [];
  ringRadii.forEach((r, idx) => {
    const bGeom = track(new THREE.TorusGeometry(r, idx === 1 ? 0.04 : 0.015, 16, 128));
    const bMat = track(new THREE.MeshBasicMaterial({
      color: idx === 1 ? 0x88aaff : 0x446699,
      transparent: true,
      opacity: idx === 1 ? 0.6 : 0.3,
      blending: THREE.AdditiveBlending,
    }));
    const bMesh = new THREE.Mesh(bGeom, bMat);
    bMesh.rotation.x = Math.PI / 2;
    scalableRingGroup.add(bMesh);

    if (idx === 1 || idx === 2) {
      const arcCount = idx === 1 ? 3 : 5;
      for (let a = 0; a < arcCount; a += 1) {
        const arcGeom = track(new THREE.TorusGeometry(r + (idx === 1 ? 0.12 : -0.08), 0.01, 8, 32, Math.PI * 0.4));
        const arcMat = track(new THREE.MeshBasicMaterial({ color: 0x00e5ff, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending }));
        const arcMesh = new THREE.Mesh(arcGeom, arcMat);
        arcMesh.rotation.x = Math.PI / 2;
        arcMesh.rotation.z = ((Math.PI * 2) / arcCount) * a;
        scalableRingGroup.add(arcMesh);
        segmentedArcs.push({ mesh: arcMesh, speed: idx === 1 ? 0.2 : -0.15 });
      }
    }
  });

  const pillarsCount = 12;
  const pillarGeom = track(new THREE.CylinderGeometry(0.015, 0.015, 0.4, 8));
  const pillarMat = track(new THREE.MeshBasicMaterial({ color: 0x88aaff, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending }));
  for (let i = 0; i < pillarsCount; i += 1) {
    const pMesh = new THREE.Mesh(pillarGeom, pillarMat);
    const a = (i / pillarsCount) * Math.PI * 2;
    pMesh.position.set(Math.cos(a) * RING_RADIUS * 1.05, -0.2, Math.sin(a) * RING_RADIUS * 1.05);
    scalableRingGroup.add(pMesh);
  }

  const dotsCount = 180;
  const dotsPos = new Float32Array(dotsCount * 3);
  for (let i = 0; i < dotsCount; i += 1) {
    const a = (i / dotsCount) * Math.PI * 2;
    dotsPos[i * 3] = Math.cos(a) * (RING_RADIUS * 0.92);
    dotsPos[i * 3 + 1] = 0;
    dotsPos[i * 3 + 2] = Math.sin(a) * (RING_RADIUS * 0.92);
  }
  const dotsGeom = track(new THREE.BufferGeometry());
  dotsGeom.setAttribute('position', new THREE.BufferAttribute(dotsPos, 3));
  const dotsMat = track(new THREE.PointsMaterial({ size: 0.06, color: 0x00e5ff, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending }));
  const dotsMesh = new THREE.Points(dotsGeom, dotsMat);
  scalableRingGroup.add(dotsMesh);

  const plateMesh = new THREE.Mesh(
    track(new THREE.CylinderGeometry(RING_RADIUS, RING_RADIUS, 0.05, 64)),
    track(new THREE.MeshBasicMaterial({ color: 0x0a1020, transparent: true, opacity: 0.7, depthWrite: false })),
  );
  plateMesh.position.y = -0.1;
  scalableRingGroup.add(plateMesh);

  const gridHelper = new THREE.PolarGridHelper(RING_RADIUS, 16, 8, 64, 0x224466, 0x112233);
  gridHelper.position.y = -0.05;
  const gridMaterial = gridHelper.material as THREE.Material;
  gridMaterial.transparent = true;
  gridMaterial.opacity = 0.15;
  gridMaterial.blending = THREE.AdditiveBlending;
  scalableRingGroup.add(gridHelper);
  track(gridHelper.geometry);
  track(gridMaterial);

  const auraMesh = new THREE.Mesh(
    track(new THREE.CylinderGeometry(RING_RADIUS * 0.95, RING_RADIUS * 0.95, 0.1, 64)),
    track(new THREE.MeshBasicMaterial({ color: 0x335588, transparent: true, opacity: 0.15, blending: THREE.AdditiveBlending, depthWrite: false })),
  );
  scalableRingGroup.add(auraMesh);

  // ── Center convergence graph (mapping phase) ─────────────────────────────
  const centerGraphGroup = new THREE.Group();
  centerGraphGroup.scale.setScalar(0);
  ringGroup.add(centerGraphGroup);
  const graphCoreMesh = new THREE.Mesh(
    track(new THREE.IcosahedronGeometry(0.8, 1)),
    track(new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending })),
  );
  centerGraphGroup.add(graphCoreMesh);
  const graphGeom = track(new THREE.IcosahedronGeometry(1.8, 2));
  const graphMesh = new THREE.Mesh(graphGeom, track(new THREE.MeshBasicMaterial({ color: 0x00e5ff, wireframe: true, transparent: true, opacity: 0.2, blending: THREE.AdditiveBlending })));
  centerGraphGroup.add(graphMesh);
  const graphNodes = new THREE.Points(graphGeom, track(new THREE.PointsMaterial({ size: 0.08, color: 0x88aaff, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending })));
  centerGraphGroup.add(graphNodes);

  // ── Agents ───────────────────────────────────────────────────────────────
  const agents: AgentObject[] = [];
  const agentBaseGeom = track(new THREE.SphereGeometry(0.5, 32, 32));
  const rippleGeom = track(new THREE.RingGeometry(0.6, 0.7, 64));

  for (let i = 0; i < agentCount; i += 1) {
    const angle = (i / Math.max(agentCount, 1)) * Math.PI * 2 - Math.PI / 2;
    const color = agentColors[i] ?? AGENT_COLOR_HEX[i % AGENT_COLOR_HEX.length];
    const colorHex = '#' + new THREE.Color(color).getHexString();

    const agentGroup = new THREE.Group();
    agentGroup.position.set(Math.cos(angle) * RING_RADIUS, AGENT_Y, Math.sin(angle) * RING_RADIUS);
    ringGroup.add(agentGroup);

    const coreMesh = new THREE.Mesh(
      track(new THREE.IcosahedronGeometry(0.25, 0)),
      track(new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending })),
    );
    agentGroup.add(coreMesh);

    const tether = new THREE.Mesh(
      track(new THREE.CylinderGeometry(0.01, 0.05, 1.0, 8)),
      track(new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending })),
    );
    tether.position.y = -0.5;
    agentGroup.add(tether);

    const mat = track(new THREE.MeshStandardMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.3,
      transparent: true,
      opacity: 0.6,
      roughness: 0.1,
      metalness: 0.5,
    }));
    agentGroup.add(new THREE.Mesh(agentBaseGeom, mat));

    const shellMesh = new THREE.Mesh(
      track(new THREE.IcosahedronGeometry(0.55, 2)),
      track(new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.15, blending: THREE.AdditiveBlending })),
    );
    agentGroup.add(shellMesh);

    const spriteMat = track(new THREE.SpriteMaterial({ map: track(makeRadialSpriteTexture(0.15, colorHex)), blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.8 }));
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(3.5, 3.5, 3.5);
    agentGroup.add(sprite);

    const trailGroup = new THREE.Group();
    const orbitRing = new THREE.Mesh(
      track(new THREE.TorusGeometry(0.85, 0.008, 8, 64)),
      track(new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending })),
    );
    orbitRing.rotation.x = Math.PI / 2;
    trailGroup.add(orbitRing);

    const satelliteCount = 40;
    const satellitePositions = new Float32Array(satelliteCount * 3);
    for (let j = 0; j < satelliteCount; j += 1) {
      const ta = (j / satelliteCount) * Math.PI * 2 + Math.random() * 0.2;
      satellitePositions[j * 3] = Math.cos(ta) * 0.85;
      satellitePositions[j * 3 + 1] = (Math.random() - 0.5) * 0.05;
      satellitePositions[j * 3 + 2] = Math.sin(ta) * 0.85;
    }
    const satelliteGeom = track(new THREE.BufferGeometry());
    satelliteGeom.setAttribute('position', new THREE.BufferAttribute(satellitePositions, 3));
    const satellites = new THREE.Points(satelliteGeom, track(new THREE.PointsMaterial({ size: 0.08, color: 0xffffff, blending: THREE.AdditiveBlending, transparent: true, opacity: 0.8, depthWrite: false })));
    trailGroup.add(satellites);
    trailGroup.rotation.x = (Math.random() - 0.5) * 0.6;
    trailGroup.rotation.z = (Math.random() - 0.5) * 0.6;
    agentGroup.add(trailGroup);

    const ripple = new THREE.Mesh(
      rippleGeom,
      track(new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide })),
    );
    ripple.rotation.x = Math.PI / 2;
    agentGroup.add(ripple);

    agents.push({ group: agentGroup, mat, sprite, coreMesh, shellMesh, trail: trailGroup, ripple, angle, baseColor: color, currentScale: 1 });
  }

  // ── User node (front-center diamond) ─────────────────────────────────────
  const userGroup = new THREE.Group();
  userGroup.position.set(0, AGENT_Y, RING_RADIUS);
  ringGroup.add(userGroup);
  // Self ("我") node — the glow halo was reading as over-bright versus the
  // agent orbs, so the emissive core, wireframe shell and radial sprite are all
  // dialled back here (and in the render loop below).
  const userMat = track(new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x88ccff, emissiveIntensity: 0.45, roughness: 0.1, metalness: 0.8 }));
  const userMesh = new THREE.Mesh(track(new THREE.OctahedronGeometry(0.35, 0)), userMat);
  userGroup.add(userMesh);
  const userOuterMesh = new THREE.Mesh(
    track(new THREE.OctahedronGeometry(0.6, 0)),
    track(new THREE.MeshBasicMaterial({ color: 0x00e5ff, wireframe: true, transparent: true, opacity: 0.32, blending: THREE.AdditiveBlending })),
  );
  userGroup.add(userOuterMesh);
  const uSpriteMat = track(new THREE.SpriteMaterial({ map: track(makeRadialSpriteTexture(0.3, 'rgba(255,255,255,0.28)')), blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.4 }));
  const uSprite = new THREE.Sprite(uSpriteMat);
  uSprite.scale.set(2.6, 2.6, 2.6);
  userGroup.add(uSprite);

  scene.add(new THREE.AmbientLight(0xffffff, 0.2));

  // ── Energy beam (reply connection) ───────────────────────────────────────
  const lineMat = track(new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, blending: THREE.AdditiveBlending, opacity: 0 }));
  let activeLineMesh: THREE.Line | null = null;
  let activeLineGeom: THREE.BufferGeometry | null = null;

  // ── State + render loop ──────────────────────────────────────────────────
  let speakingIndex: number | null = null;
  let replyTarget: ReplyTargetTo | null = null;
  let phase: 'mapping' | 'default' = 'default';
  let phaseTransition = 0;
  let disposed = false;
  let paused = false;
  let running = !reducedMotion;
  let rafId = 0;
  let lastTime = performance.now() / 1000;
  let elapsed = 0;

  // Pointer parallax — eased toward the cursor and applied to the background
  // layers only, so the foreground ring stays aligned with the DOM overlay.
  let targetMouseX = 0;
  let targetMouseY = 0;
  let mouseX = 0;
  let mouseY = 0;
  const onMouseMove = (event: MouseEvent) => {
    targetMouseX = (event.clientX / window.innerWidth) * 2 - 1;
    targetMouseY = -(event.clientY / window.innerHeight) * 2 + 1;
  };
  window.addEventListener('mousemove', onMouseMove);

  const onVisChange = () => {
    paused = document.hidden;
    if (!paused) lastTime = performance.now() / 1000;
  };
  document.addEventListener('visibilitychange', onVisChange);

  function renderFrame() {
    const now = performance.now() / 1000;
    const dt = Math.min(now - lastTime, 0.1);
    lastTime = now;
    elapsed += dt;
    const time = elapsed;

    if (!reducedMotion) {
      mouseX += (targetMouseX - mouseX) * dt * 2;
      mouseY += (targetMouseY - mouseY) * dt * 2;
      bgParticles.position.x = mouseX * 5;
      bgParticles.position.y = mouseY * 3;
      nebulaMesh.position.x = mouseX * 3;
      nebulaMesh.position.z = -mouseY * 3;
      bgParticles.rotation.y += dt * 0.015;
      bgParticles.rotation.x += dt * 0.005;
      const colorArr = bgParticles.geometry.attributes.color.array as Float32Array;
      for (let i = 0; i < bgCount; i += 1) {
        bgPhases[i] += dt * bgSpeeds[i];
        const brightness = 0.1 + Math.max(0, Math.sin(bgPhases[i])) * 0.9;
        colorArr[i * 3] = bgBaseColors[i * 3] * brightness;
        colorArr[i * 3 + 1] = bgBaseColors[i * 3 + 1] * brightness;
        colorArr[i * 3 + 2] = bgBaseColors[i * 3 + 2] * brightness;
      }
      bgParticles.geometry.attributes.color.needsUpdate = true;
    }

    const targetPhaseTransition = phase === 'mapping' ? 1 : 0;
    phaseTransition += (targetPhaseTransition - phaseTransition) * dt * 2;
    const currentRadius = THREE.MathUtils.lerp(RING_RADIUS, RING_RADIUS * 0.6, phaseTransition);
    scalableRingGroup.scale.setScalar(currentRadius / RING_RADIUS);

    if (!reducedMotion) {
      segmentedArcs.forEach((arc) => {
        arc.mesh.rotation.z -= dt * arc.speed;
      });
      dotsMesh.rotation.y += dt * 0.1;
      centerGraphGroup.rotation.y += dt * 0.2;
      centerGraphGroup.rotation.z += dt * 0.1;
      graphCoreMesh.rotation.y -= dt * 0.3;
    }
    centerGraphGroup.scale.setScalar(THREE.MathUtils.lerp(0.001, 1.0, phaseTransition));

    userGroup.position.z = currentRadius;

    if (activeLineMesh) {
      scene.remove(activeLineMesh);
      if (activeLineGeom) {
        activeLineGeom.dispose();
        const idx = disposables.indexOf(activeLineGeom);
        if (idx > -1) disposables.splice(idx, 1);
      }
      activeLineMesh = null;
    }

    if (speakingIndex !== null && replyTarget !== null && agents[speakingIndex]) {
      const fromPos = agents[speakingIndex].group.position.clone();
      const toPos = new THREE.Vector3();
      if (replyTarget.type === 'center') toPos.set(0, 0, 0);
      else if (replyTarget.type === 'user') toPos.copy(userGroup.position);
      else if (agents[replyTarget.index]) toPos.copy(agents[replyTarget.index].group.position);

      const points: THREE.Vector3[] = [];
      const numPoints = 30;
      const arcHeight = fromPos.distanceTo(toPos) * 0.15;
      for (let i = 0; i <= numPoints; i += 1) {
        const t = i / numPoints;
        const p = new THREE.Vector3().lerpVectors(fromPos, toPos, t);
        p.y += Math.sin(t * Math.PI) * arcHeight;
        if (!reducedMotion) {
          p.x += Math.sin(t * 10 - time * 5) * 0.1;
          p.z += Math.cos(t * 10 - time * 5) * 0.1;
        }
        points.push(p);
      }
      activeLineGeom = track(new THREE.BufferGeometry().setFromPoints(points));
      activeLineMesh = new THREE.Line(activeLineGeom, lineMat);
      lineMat.color.setHex(agents[speakingIndex].baseColor);
      lineMat.opacity = reducedMotion ? 0.7 : (Math.sin(time * 15) * 0.3 + 0.7) * 0.8;
      scene.add(activeLineMesh);
    }

    agents.forEach((ag, idx) => {
      ag.group.position.set(Math.cos(ag.angle) * currentRadius, AGENT_Y, Math.sin(ag.angle) * currentRadius);

      const isSpeaking = idx === speakingIndex;
      const targetScale = isSpeaking ? 1.3 : 1.0;
      ag.currentScale = reducedMotion ? targetScale : ag.currentScale + (targetScale - ag.currentScale) * dt * 6;
      ag.group.scale.setScalar(ag.currentScale);

      if (!reducedMotion) {
        ag.coreMesh.rotation.y -= dt * 2.0;
        ag.shellMesh.rotation.y += dt * 0.5;
        ag.shellMesh.rotation.z += dt * 0.2;
        ag.trail.rotation.y += dt * 1.5;
        ag.trail.rotation.z += dt * 0.5;
      }

      const breathe = reducedMotion ? 0.5 : Math.sin(time * 2 + idx) * 0.5 + 0.5;
      ag.mat.emissiveIntensity = isSpeaking ? 1.0 + breathe * 0.3 : 0.3 + breathe * 0.15;
      ag.sprite.material.opacity = isSpeaking ? 0.75 : 0.3 + breathe * 0.15;

      if (isSpeaking && !reducedMotion) {
        const ripplePhase = (time * 1.5) % 1.5;
        ag.ripple.scale.setScalar(1.0 + ripplePhase * 2);
        (ag.ripple.material as THREE.Material).opacity = Math.max(0, 1.0 - ripplePhase / 1.5);
      } else {
        (ag.ripple.material as THREE.Material).opacity = 0;
      }
    });

    userGroup.position.y = AGENT_Y + (reducedMotion ? 0 : Math.sin(time) * 0.2);
    if (!reducedMotion) {
      userMesh.rotation.y += dt;
      userOuterMesh.rotation.y -= dt * 0.5;
      userOuterMesh.rotation.z += dt * 0.2;
    }
    const userIsTarget = replyTarget?.type === 'user';
    userMat.emissiveIntensity = reducedMotion
      ? (userIsTarget ? 0.65 : 0.4)
      : userIsTarget
        ? 0.65 + Math.sin(time * 5) * 0.3
        : 0.4 + Math.sin(time * 2) * 0.12;

    composer.render();
  }

  function loop() {
    if (disposed) return;
    rafId = window.requestAnimationFrame(loop);
    if (paused) return;
    renderFrame();
  }

  function requestRender() {
    if (!running && !disposed) renderFrame();
  }

  if (running) loop();
  else renderFrame();

  return {
    setSpeaking(index) {
      speakingIndex = index === null || index < 0 ? null : index;
      requestRender();
    },
    setReplyTarget(to) {
      replyTarget = to;
      requestRender();
    },
    setPhase(next) {
      phase = next;
      requestRender();
    },
    setReducedMotion(value) {
      reducedMotion = value;
      if (value) {
        running = false;
        if (rafId) window.cancelAnimationFrame(rafId);
        rafId = 0;
      } else if (!running) {
        running = true;
        lastTime = performance.now() / 1000;
        loop();
      }
      renderFrame();
    },
    resize(nextWidth, nextHeight) {
      if (nextWidth <= 0 || nextHeight <= 0) return;
      width = nextWidth;
      height = nextHeight;
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      composer.setSize(width, height);
      bloomPass.setSize(width, height);
      requestRender();
    },
    dispose() {
      disposed = true;
      if (rafId) window.cancelAnimationFrame(rafId);
      window.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('visibilitychange', onVisChange);
      disposables.forEach((d) => d.dispose());
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((m) => m.dispose());
        else if (material) material.dispose();
      });
      renderer.dispose();
      composer.dispose();
    },
  };
}

function SpeechBubble({
  seat,
  agentName,
  content,
}: {
  seat: SeatPosition;
  agentName: string;
  content: string;
}) {
  const color = seat.isUser ? '#88ccff' : AGENT_COLORS[seat.index % AGENT_COLORS.length] ?? '#88ccff';

  return (
    <div data-testid="council-chamber-speech-bubble">
      <div
        data-testid="roundtable-speech-bubble"
        className="bubble-direction-down pointer-events-none absolute z-30 w-[320px] max-w-[78vw] border bg-[#05080e]/85 px-4 py-3 text-slate-100 shadow-2xl backdrop-blur-sm"
        style={{
          left: `${seat.bubbleX}%`,
          top: `${seat.bubbleY}%`,
          transform: 'translate(-50%, -100%)',
          borderColor: color,
          boxShadow: `0 4px 20px rgba(0,0,0,0.5), 0 0 15px ${color}33`,
          clipPath: 'polygon(0 0, 100% 0, 100% calc(100% - 14px), calc(100% - 14px) 100%, 0 100%)',
        }}
      >
        <span
          data-testid="roundtable-speech-bubble-label"
          className="mb-2 block border-b border-white/15 pb-2 font-mono text-[11px] font-bold uppercase tracking-[0.18em]"
          style={{ color }}
        >
          {agentName}
          {!seat.isUser && <span className="opacity-70"> · 发言中</span>}
        </span>
        <p data-testid="roundtable-speech-bubble-body" className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">
          {content}
        </p>
        <span
          data-testid="roundtable-speech-bubble-direction"
          className="bubble-direction-down absolute left-1/2 bottom-[-0.35rem] h-3 w-3 rotate-45 border bg-[#05080e]"
          style={{
            borderColor: color,
            transform: 'translate(-50%, -50%) rotate(45deg)',
          }}
        />
      </div>
    </div>
  );
}

function SeatNode({
  seat,
  isSpeaking,
  onAgentClick,
  onUserSeatClick,
}: {
  seat: SeatPosition;
  isSpeaking: boolean;
  onAgentClick: (agentId: string) => void;
  onUserSeatClick: () => void;
}) {
  const color = AGENT_COLORS[seat.index % AGENT_COLORS.length] ?? '#88ccff';

  const commonProps = {
    className: 'group absolute z-20 flex flex-col items-center gap-1',
    style: {
      left: `${seat.x}%`,
      top: `${seat.y}%`,
      transform: 'translate(-50%, -50%)',
    },
  };

  if (seat.isUser) {
    return (
      <button type="button" {...commonProps} aria-label="User seat" onClick={onUserSeatClick} title={seat.name}>
        <span className="h-12 w-12 rounded-full border border-blue-300/30 transition-colors group-hover:border-blue-200/60 group-hover:bg-blue-300/10" />
        <span className="text-[10px] font-medium leading-tight text-blue-100">{seat.name}</span>
      </button>
    );
  }

  return (
    <button
      type="button"
      {...commonProps}
      aria-label={`${seat.name} seat`}
      title={seat.name}
      onClick={() => onAgentClick(seat.id)}
    >
      <span
        className={`h-14 w-14 rounded-full border border-transparent transition-all ${
          isSpeaking ? 'animate-speaking' : 'group-hover:border-white/30 group-hover:bg-white/5'
        }`}
        style={isSpeaking ? { boxShadow: `0 0 26px ${color}` } : undefined}
      />
      <span
        className="max-w-[88px] text-center font-mono text-[9px] font-medium uppercase leading-tight tracking-[0.12em] text-white/35 transition-colors group-hover:text-white/75"
        style={{ color: isSpeaking ? color : undefined }}
      >
        {seat.name}
      </span>
    </button>
  );
}

function ReplyLine({ fromSeat, toSeat }: { fromSeat: SeatPosition; toSeat: SeatPosition }) {
  return (
    <line
      data-testid="council-chamber-reply-line"
      data-from={fromSeat.id}
      data-to={toSeat.id}
      x1={`${fromSeat.x}%`}
      y1={`${fromSeat.y}%`}
      x2={`${toSeat.x}%`}
      y2={`${toSeat.y}%`}
      stroke="transparent"
      strokeWidth="0"
    />
  );
}

function getCenterTitle(currentRound: number, currentPhase: string): string {
  const isR4SubPhase = ['r4_reflection', 'r4_mapping', 'r4_final'].includes(currentPhase);
  if (currentPhase === 'r4_final') {
    return '终章 · 最后要说的话';
  }
  if (isR4SubPhase) {
    return `第 4 轮 · ${PHASE_LABEL[currentPhase] ?? currentPhase}`;
  }
  if (currentRound > 0) {
    return `第 ${currentRound} 轮`;
  }
  return '议会厅';
}

export default function CouncilChamber({
  agents,
  userDisplayName,
  speakingAgentId,
  currentRound,
  currentPhase,
  paused,
  streaming,
  hasMessages,
  latestContent,
  subtitleAnchorId,
  subtitleAnchorName,
  replyTo,
  onUserSeatClick,
  onPause,
  onResume,
  exchangeProgress,
  messages = [],
  agentEvolutions,
}: CouncilChamberProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const sceneApiRef = useRef<ChamberSceneApi | null>(null);
  const reducedMotion = usePrefersReducedMotion();
  const reducedMotionRef = useRef(reducedMotion);
  const [aspect, setAspect] = useState(16 / 9);
  const webglAvailable = useMemo(() => isWebGLAvailable(), []);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  const seats = useMemo(() => computeSeats(agents, userDisplayName, aspect), [agents, userDisplayName, aspect]);
  const agentsKey = useMemo(() => agents.map((agent) => agent.agentId).join('|'), [agents]);

  const speakingSeat = seats.find((seat) => seat.id === speakingAgentId);
  const replyToSeat = replyTo ? seats.find((seat) => seat.id === replyTo) : null;
  const speakingAgent = speakingAgentId
    ? agents.find((entry) => entry.agentId === speakingAgentId)
    : undefined;
  const anchoredSeat =
    subtitleAnchorId !== null && subtitleAnchorId !== undefined
      ? seats.find((seat) => seat.id === subtitleAnchorId) ?? null
      : null;
  // Anchor wins over the live speaker so the displayed subtitle cue (which now
  // outlives the speaker hand-off) always sits on the orb of whoever actually
  // said it — the previous voice's last line lingers on their seat while the
  // next voice's orb lights up.
  const bubbleSeat = anchoredSeat ?? speakingSeat ?? null;
  const bubbleAgentName =
    subtitleAnchorName ?? anchoredSeat?.name ?? speakingAgent?.agentName ?? speakingSeat?.name ?? null;
  const selectedAgent = selectedAgentId
    ? agents.find((agent) => agent.agentId === selectedAgentId)
    : null;
  const centerTitle = getCenterTitle(currentRound, currentPhase);
  const phaseLabel = currentPhase ? (PHASE_LABEL[currentPhase] ?? currentPhase) : '';
  const hasClosingAct = messages.some((message) => message.closingAct);

  function getEvolution(agentId: string): AgentEvolutionData | undefined {
    return agentEvolutions?.get(agentId)?.at(-1);
  }

  // Selected-agent stance comparison: original (R1) vs. current, shown in the panel.
  const selectedEvolution = selectedAgent ? getEvolution(selectedAgent.agentId) : undefined;
  const selectedLivePosition =
    selectedAgent && selectedAgent.agentId === speakingAgentId && latestContent ? latestContent : null;
  const selectedCurrentPosition =
    selectedLivePosition || selectedEvolution?.current_position || phaseLabel || '等待这个声音发言。';
  const selectedOriginalPosition = selectedEvolution?.r1_position;
  const showSelectedOriginal =
    !!selectedOriginalPosition && selectedOriginalPosition !== selectedEvolution?.current_position;

  // Build the WebGL scene once per agent roster; live state is pushed imperatively.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !webglAvailable) {
      return undefined;
    }
    const api = buildChamberScene(canvas, {
      agentColors: agents.map((_, index) => AGENT_COLOR_HEX[index % AGENT_COLOR_HEX.length]),
      agentCount: agents.length,
      reducedMotion: reducedMotionRef.current,
    });
    sceneApiRef.current = api;

    return () => {
      api.dispose();
      sceneApiRef.current = null;
    };
  }, [agentsKey, agents, webglAvailable]);

  // Push live debate state into the scene (runs after the build effect on mount).
  useEffect(() => {
    const api = sceneApiRef.current;
    if (!api) return;
    const speakingIndex = speakingAgentId ? agents.findIndex((agent) => agent.agentId === speakingAgentId) : -1;
    api.setSpeaking(speakingIndex >= 0 ? speakingIndex : null);

    let target: ReplyTargetTo | null = null;
    if (replyTo) {
      const replyIndex = agents.findIndex((agent) => agent.agentId === replyTo);
      if (replyIndex >= 0) target = { type: 'agent', index: replyIndex };
      else if (replyTo === '__user__') target = { type: 'user' };
    }
    api.setReplyTarget(target);
    api.setPhase(currentPhase === 'r4_mapping' ? 'mapping' : 'default');
  }, [speakingAgentId, replyTo, currentPhase, agents]);

  useEffect(() => {
    reducedMotionRef.current = reducedMotion;
    sceneApiRef.current?.setReducedMotion(reducedMotion);
  }, [reducedMotion]);

  // Keep the projected DOM overlay and the renderer in sync with the viewport.
  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const update = () => {
      const rect = element.getBoundingClientRect();
      const width = rect.width || window.innerWidth;
      const height = rect.height || window.innerHeight;
      if (width > 0 && height > 0) setAspect(width / height);
      sceneApiRef.current?.resize(width, height);
    };
    update();
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  return (
    <div
      ref={containerRef}
      data-testid="council-chamber"
      className="fixed inset-0 z-50 overflow-hidden bg-[#000205] text-white"
    >
      <canvas
        ref={canvasRef}
        data-testid="council-chamber-canvas"
        className="absolute inset-0 h-full w-full"
        aria-hidden="true"
      />

      {!webglAvailable && (
        <>
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(14,165,233,0.18),transparent_42%),radial-gradient(circle_at_30%_15%,rgba(124,58,237,0.16),transparent_32%)]" />
          <div className="absolute inset-[18%] rounded-full border border-cyan-200/15 bg-slate-950/40 shadow-[inset_0_0_40px_rgba(125,211,252,0.08)]" />
        </>
      )}

      <div
        className="pointer-events-none absolute left-1/2 top-6 z-20 flex max-w-[260px] -translate-x-1/2 flex-col items-center gap-1 text-center"
      >
        <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-100">{centerTitle}</span>
        {phaseLabel && currentPhase !== 'r4_final' && (
          <span className="text-[10px] text-cyan-200/65">{phaseLabel}</span>
        )}

        {streaming && !paused && !speakingAgentId && currentRound > 0 && (
          <div className="mt-1 flex flex-col items-center gap-1">
            <div className="flex items-center gap-1">
              {[0, 150, 300].map((delay) => (
                <span
                  key={delay}
                  className="h-1.5 w-1.5 animate-pulse rounded-full bg-cyan-300"
                  style={{ animationDelay: `${delay}ms` }}
                />
              ))}
            </div>
            <span className="text-[9px] text-cyan-100/70">
              {hasMessages ? '等待下一位发言...' : '首轮发言生成中...'}
            </span>
          </div>
        )}

        {currentPhase === 'r4_mapping' && (
          <span className="mt-1 text-[9px] font-medium text-teal-200">正在映射共识...</span>
        )}

        {exchangeProgress && currentPhase !== 'r4_mapping' && (
          <span className="text-[9px] text-cyan-100/55">
            {exchangeProgress.seq}/{exchangeProgress.min}-{exchangeProgress.max}
          </span>
        )}

        {streaming && (
          <button
            type="button"
            onClick={paused ? onResume : onPause}
            className={`pointer-events-auto mt-1 rounded-full border px-2.5 py-1 text-[10px] transition ${
              paused
                ? 'border-emerald-300/40 bg-emerald-400/15 text-emerald-100 hover:bg-emerald-400/25'
                : 'border-white/10 bg-white/5 text-white/70 hover:bg-white/10'
            }`}
          >
            {paused ? '继续' : '暂停'}
          </button>
        )}
      </div>

      <svg
        className="pointer-events-none absolute inset-0 z-10 h-full w-full"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
      >
        {speakingSeat && replyToSeat && <ReplyLine fromSeat={replyToSeat} toSeat={speakingSeat} />}
      </svg>

      {seats.map((seat) => (
        <SeatNode
          key={seat.id}
          seat={seat}
          isSpeaking={seat.id === speakingAgentId}
          onAgentClick={setSelectedAgentId}
          onUserSeatClick={onUserSeatClick}
        />
      ))}

      {hasClosingAct && (
        <div
          data-testid="closing-act-divider"
          className="absolute left-1/2 top-[18%] z-30 -translate-x-1/2 rounded-full border border-amber-200/35 bg-amber-300/10 px-4 py-2 text-xs font-semibold tracking-[0.18em] text-amber-50 backdrop-blur"
        >
          终章
        </div>
      )}

      {bubbleSeat && bubbleAgentName && latestContent && (
        <SpeechBubble seat={bubbleSeat} agentName={bubbleAgentName} content={latestContent} />
      )}

      <div
        data-testid="council-chamber-history-panel"
        className={`absolute left-0 top-[100px] z-40 flex max-h-[calc(100vh-120px)] w-80 flex-col rounded-r-[8px] border border-l-0 border-white/15 bg-[#05080e]/85 text-white shadow-[4px_4px_20px_rgba(0,0,0,0.5)] backdrop-blur transition-transform duration-300 ${
          historyOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <button
          type="button"
          aria-label="对话历史"
          onClick={() => setHistoryOpen((value) => !value)}
          className="absolute right-[-36px] top-5 flex h-[100px] w-9 items-center justify-center rounded-r-[8px] border border-l-0 border-white/15 bg-[#05080e]/85 text-[12px] tracking-[0.3em] text-white/70 transition hover:bg-[#0f141e]/95 hover:text-white"
          style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
        >
          历史
        </button>
        <div className="border-b border-white/15 px-5 py-4 text-base font-bold tracking-[0.18em] text-white/90">
          对话记录
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length > 0 ? messages.map((message) => {
            const agentIndex = agents.findIndex((agent) => agent.agentId === message.agentId);
            const color = message.isUserTurn || message.speakerType === 'user'
              ? '#60a5fa'
              : AGENT_COLORS[Math.max(agentIndex, 0) % AGENT_COLORS.length];
            return (
              <div key={message.id} className="text-[13px] leading-5">
                <div className="mb-1 font-mono text-[10px] uppercase tracking-[0.16em] text-white/40">
                  第 {message.round} 轮
                </div>
                <div className="mb-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.12em]" style={{ color }}>
                  {message.agentName}
                </div>
                <div className="rounded-[4px] border-l-2 bg-white/[0.03] px-3 py-2 text-white/80" style={{ borderColor: color }}>
                  {message.content || (message.streaming ? '正在接收…' : '')}
                </div>
              </div>
            );
          }) : agents.map((agent, index) => (
            <div key={agent.agentId} className="border-l-2 border-white/10 bg-white/[0.03] px-3 py-2 text-xs">
              <div className="mb-1 font-mono font-semibold uppercase tracking-[0.16em]" style={{ color: AGENT_COLORS[index % AGENT_COLORS.length] }}>
                {agent.agentName}
              </div>
              <div className="text-white/65">
                {agent.agentId === speakingAgentId && latestContent
                  ? latestContent
                  : '已接入实时辩论。'}
              </div>
            </div>
          ))}
        </div>
      </div>

      {!historyOpen && messages.length > 0 && (
        <div className="pointer-events-none absolute left-10 top-[130px] z-30 animate-pulse rounded-full border border-white/20 bg-[#05080e]/60 px-4 py-2 text-[12px] tracking-[0.16em] text-white/70 backdrop-blur">
          点击展开对话历史
        </div>
      )}

      {selectedAgent && (
        <aside className="absolute right-5 top-[50px] z-40 max-h-[calc(100vh-100px)] w-[360px] overflow-y-auto rounded-[8px] border border-white/15 bg-[#05080e]/85 p-5 text-sm text-white shadow-[-4px_4px_20px_rgba(0,0,0,0.5)] backdrop-blur">
          <div className="mb-3 flex items-center justify-between border-b border-white/10 pb-2">
            <h3 className="font-mono text-xs font-semibold uppercase tracking-[0.18em] text-white/80">
              {selectedAgent.agentName}
            </h3>
            <button
              type="button"
              aria-label="Close agent details"
              onClick={() => setSelectedAgentId(null)}
              className="text-lg leading-none text-white/50 transition hover:text-white"
            >
              ×
            </button>
          </div>
          <div className="space-y-3">
            <section>
              <div className="mb-1 text-[11px] tracking-[0.18em] text-white/40">
                立场变化
              </div>
              {showSelectedOriginal && (
                <div className="mb-2 flex gap-2 text-xs leading-5 text-white/35">
                  <span aria-hidden="true">⊘</span>
                  <p className="line-through">{selectedOriginalPosition}</p>
                </div>
              )}
              <p className="rounded-[4px] border-l-2 border-cyan-300/60 bg-white/[0.04] p-3 text-xs leading-5 text-white/80">
                {selectedCurrentPosition}
              </p>
            </section>
            <section>
              <div className="mb-1 text-[11px] tracking-[0.18em] text-white/40">关联</div>
              <p className="rounded-[4px] border-l-2 border-white/20 bg-white/[0.04] p-3 text-xs leading-5 text-white/65">
                {replyTo === selectedAgent.agentId
                  ? '正在接收当前发言者的直接回应。'
                  : '正在旁听本轮议会。'}
              </p>
            </section>
          </div>
        </aside>
      )}

      {!selectedAgent && (
        <div className="pointer-events-none absolute bottom-5 right-5 z-30 animate-pulse rounded-full border border-white/20 bg-[#05080e]/60 px-4 py-2 text-[12px] tracking-[0.16em] text-white/70 backdrop-blur">
          点击星体查看详情
        </div>
      )}

    </div>
  );
}
