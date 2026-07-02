/* eslint-disable react-hooks/purity -- particle/node start positions, colours and timings are
   intentionally randomized once at mount; the non-determinism is the desired visual effect
   (same rationale as the predecessor ParticleBackground). */
/* eslint-disable @typescript-eslint/no-explicit-any -- R3F material/shader props are loosely
   typed in this vendored homepage scene; kept as-authored to stay drop-in (see 22.zip). */
import { useRef, useMemo } from 'react';
import { Canvas, useFrame } from '@react-three/fiber';
import { Stars, Billboard } from '@react-three/drei';
import { EffectComposer, Bloom, Vignette } from '@react-three/postprocessing';
import * as THREE from 'three';

// Cosmic scale parameters
const RADIUS_X = 15;
const RADIUS_Z = 7.5;
const NUM_NODES = 5;

// High-resolution, ultra-soft optical flare texture
const createSoftParticle = () => {
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 256;
    const ctx = canvas.getContext('2d');
    if (ctx) {
        const gradient = ctx.createRadialGradient(128, 128, 0, 128, 128, 128);
        gradient.addColorStop(0, 'rgba(255,255,255,1)');
        gradient.addColorStop(0.05, 'rgba(255,255,255,0.8)');
        gradient.addColorStop(0.2, 'rgba(255,255,255,0.2)');
        gradient.addColorStop(0.5, 'rgba(255,255,255,0.05)');
        gradient.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, 256, 256);
    }
    return new THREE.CanvasTexture(canvas);
};

const particleTexture = createSoftParticle();

// Implements a subtle smooth mouse parallax for premium feel
function CameraRig() {
    useFrame((state) => {
        const t = state.clock.elapsedTime;
        // Cinematic smooth drag
        const targetX = (state.pointer.x * 2.0) + Math.sin(t * 0.1) * 0.3;
        const targetY = (state.pointer.y * 1.5) + 4.0 + Math.cos(t * 0.15) * 0.2;

        state.camera.position.x += (targetX - state.camera.position.x) * 0.05;
        state.camera.position.y += (targetY - state.camera.position.y) * 0.05;
        state.camera.lookAt(0, 0, -1);
    });
    return null;
}

// Multi-layered, highly dynamic particle tracks mapping the neural structure

function ConvergingMaterial({ size, map, vertexColors, opacity, blending, depthWrite }: any) {
    const matRef = useRef<THREE.PointsMaterial>(null);
    useFrame((state) => {
        if (matRef.current?.userData?.shader) {
            matRef.current.userData.shader.uniforms.uTime.value = state.clock.elapsedTime;
        }
    });

    return (
        <pointsMaterial
            ref={matRef}
            size={size}
            map={map}
            vertexColors={vertexColors}
            transparent
            opacity={opacity}
            blending={blending}
            depthWrite={depthWrite}
            onBeforeCompile={(shader) => {
                shader.uniforms.uTime = { value: 0 };
                shader.vertexShader = `
                    attribute vec3 aRandomPos;
                    attribute float aRandomTime;
                    uniform float uTime;
                    varying float vEase;
                    varying float vPacketTime;
                ` + shader.vertexShader;

                shader.vertexShader = shader.vertexShader.replace(
                    `#include <begin_vertex>`,
                    `
                    float duration = 4.0 + aRandomTime * 4.0;
                    float progress = clamp((uTime - aRandomTime * 2.5) / duration, 0.0, 1.0);
                    // stream of consciousness ease
                    float ease = progress < 0.5
                        ? 8.0 * progress * progress * progress * progress
                        : 1.0 - pow(-2.0 * progress + 2.0, 4.0) / 2.0;
                    vEase = ease;
                    vPacketTime = uTime; // Custom param for possible packets

                    float angleOffset = (1.0 - ease) * 12.0 * (aRandomTime > 0.5 ? 1.0 : -1.0);
                    vec3 startLocal = aRandomPos;
                    float sX = startLocal.x * cos(angleOffset) - startLocal.z * sin(angleOffset);
                    float sZ = startLocal.x * sin(angleOffset) + startLocal.z * cos(angleOffset);
                    startLocal.x = sX;
                    startLocal.z = sZ;

                    vec3 transformed = mix(startLocal, position, ease);
                    `
                );

                shader.vertexShader = shader.vertexShader.replace(
                    `#include <size_vertex>`,
                    `
                    #include <size_vertex>
                    gl_PointSize *= min(pow(ease, 3.0) * 3.0, 1.0);
                    `
                );

                shader.fragmentShader = `
                    varying float vEase;
                    varying float vPacketTime;
                ` + shader.fragmentShader;

                shader.fragmentShader = shader.fragmentShader.replace(
                    `#include <premultiplied_alpha_fragment>`,
                    `
                    #include <premultiplied_alpha_fragment>
                    float opacityFade = smoothstep(0.0, 0.4, vEase);
                    gl_FragColor.a *= opacityFade;
                    `
                );

                matRef.current!.userData.shader = shader;
            }}
        />
    )
}

function MeshNetworkRing() {
    const groupRef = useRef<THREE.Group>(null);
    const dustRef = useRef<THREE.Group>(null);
    const tracksRef = useRef<THREE.Group>(null);

    const { diffuseLayers, trackLayers, packetLayers } = useMemo(() => {
        const cCore = new THREE.Color('#ffffff');
        const cBlue = new THREE.Color('#38bdf8');
        const cPurple = new THREE.Color('#c084fc');
        const cGold = new THREE.Color('#fbbf24');

        // 1. Ambient Volumetric Dust (Split into layers for parallax rotation)
        const dLayers = Array(3).fill(0).map(() => ({ pos: [] as number[], col: [] as number[], rPos: [] as number[], rTime: [] as number[] }));
        for(let i=0; i<25000; i++) {
            const angle = Math.random() * Math.PI * 2;
            const spread = (Math.random() + Math.random() + Math.random() - 1.5) / 1.5;
            let localR = RADIUS_Z + spread * 4.5;
            if (Math.abs(spread) < 0.2) localR += (Math.random() > 0.5 ? 1 : -1) * (0.5 + Math.random()); // Keep deep core slightly airy

            const rX = localR * (RADIUS_X / RADIUS_Z);
            const rZ = localR;
            const tScale = Math.max(0.05, 1.0 - Math.abs(spread)*1.2);
            const y = (Math.random() - 0.5) * 1.8 * tScale;

            const layerIdx = Math.floor(Math.random() * 3);
            dLayers[layerIdx].pos.push(Math.cos(angle) * rX, y, Math.sin(angle) * rZ);

            // Random start configuration
            const startR = 40 + Math.random() * 40;
            const startY = (Math.random() - 0.5) * 40;
            const startAngle = Math.random() * Math.PI * 2;
            dLayers[layerIdx].rPos.push(Math.cos(startAngle) * startR, startY, Math.sin(startAngle) * startR);
            dLayers[layerIdx].rTime.push(Math.random());

            let c = cPurple.clone();
            if (Math.random() > 0.5) c = cBlue.clone();
            c.lerp(cCore, Math.random() * 0.1);

            dLayers[layerIdx].col.push(c.r, c.g, c.b);
        }

        // 2. High-fidelity Particle Tracks (Interwoven strands)
        const tLayers = Array(5).fill(0).map(() => ({ pos: [] as number[], col: [] as number[], rPos: [] as number[], rTime: [] as number[] }));
        const pLayers = Array(5).fill(0).map(() => ({ pos: [] as number[], col: [] as number[], rPos: [] as number[], rTime: [] as number[] })); // Fast moving data packets

        const strandCount = 400;
        for (let s_i = 0; s_i < strandCount; s_i++) {
            const layerIdx = s_i % 5;
            const isMainTrack = Math.random() > 0.4;
            const radiusOffset = isMainTrack ? (Math.random() - 0.5) * 0.8 : (Math.random() - 0.5) * 5.0;
            const baseRadZ = RADIUS_Z + radiusOffset;

            const cableWaveFreq = Math.floor(Math.random() * 10) + 1;
            const cableWaveAmpR = Math.random() * 0.8;
            const cableWaveAmpY = Math.random() * 0.4;
            const cablePhase = Math.random() * Math.PI * 2;

            let primaryColor = cBlue.clone();
            if (Math.random() > 0.5) primaryColor = cPurple.clone();
            if (Math.random() > 0.85) primaryColor = cGold.clone();

            const pointsInStrand = isMainTrack ? 750 : 300;

            const strandStartTime = Math.random();
            const startR = 50 + Math.random() * 60;
            const startY = (Math.random() - 0.5) * 80;
            const startAngle = Math.random() * Math.PI * 2;
            const strandStartPosX = Math.cos(startAngle) * startR;
            const strandStartPosZ = Math.sin(startAngle) * startR;

            for (let p_i = 0; p_i < pointsInStrand; p_i++) {
                const percent = p_i / pointsInStrand;
                const angle = percent * Math.PI * 2 + (Math.random() * 0.005);

                // Micro-jitter to feel like physical dust, not sterile math
                const jitterR = (Math.random() - 0.5) * 0.04;
                const jitterY = (Math.random() - 0.5) * 0.04;

                const cZ = baseRadZ + Math.sin(angle * cableWaveFreq + cablePhase) * cableWaveAmpR + jitterR;
                const cX = cZ * (RADIUS_X / RADIUS_Z);
                const cY = Math.cos(angle * cableWaveFreq + cablePhase) * cableWaveAmpY + jitterY;

                tLayers[layerIdx].pos.push(Math.cos(angle) * cX, cY, Math.sin(angle) * cZ);

                const spreadX = (Math.random() - 0.5) * 12;
                const spreadY = (Math.random() - 0.5) * 12;
                const spreadZ = (Math.random() - 0.5) * 12;
                const rPX = strandStartPosX + spreadX;
                const rPY = startY + spreadY;
                const rPZ = strandStartPosZ + spreadZ;
                tLayers[layerIdx].rPos.push(rPX, rPY, rPZ);
                tLayers[layerIdx].rTime.push(strandStartTime + Math.random() * 0.3);

                const c = primaryColor.clone();
                if (Math.random() > 0.95) c.lerp(cCore, 0.9);

                tLayers[layerIdx].col.push(c.r, c.g, c.b);

                // Bright data packets with motion blur tails
                if (Math.random() > 0.985) {
                    pLayers[layerIdx].pos.push(Math.cos(angle) * cX, cY, Math.sin(angle) * cZ);
                    pLayers[layerIdx].col.push(1, 1, 1);
                    pLayers[layerIdx].rPos.push(rPX, rPY, rPZ);
                    pLayers[layerIdx].rTime.push(strandStartTime);

                    // Generate trailing tail
                    for(let tail=1; tail<=12; tail++) {
                        const tAng = percent * Math.PI * 2 - (tail * 0.002);
                        const tZ = baseRadZ + Math.sin(tAng * cableWaveFreq + cablePhase) * cableWaveAmpR + jitterR;
                        const tX = tZ * (RADIUS_X / RADIUS_Z);
                        const tY = Math.cos(tAng * cableWaveFreq + cablePhase) * cableWaveAmpY + jitterY;

                        pLayers[layerIdx].pos.push(Math.cos(tAng) * tX, tY, Math.sin(tAng) * tZ);
                        const fade = Math.pow(1 - (tail/12), 2);
                        pLayers[layerIdx].col.push(c.r * fade, c.g * fade, c.b * fade);

                        pLayers[layerIdx].rPos.push(rPX, rPY, rPZ);
                        pLayers[layerIdx].rTime.push(strandStartTime + tail*0.01);
                    }
                }
            }
        }

        return {
            diffuseLayers: dLayers.map(l => ({ pos: new Float32Array(l.pos), col: new Float32Array(l.col), rPos: new Float32Array(l.rPos), rTime: new Float32Array(l.rTime) })),
            trackLayers: tLayers.map(l => ({ pos: new Float32Array(l.pos), col: new Float32Array(l.col), rPos: new Float32Array(l.rPos), rTime: new Float32Array(l.rTime) })),
            packetLayers: pLayers.map(l => ({ pos: new Float32Array(l.pos), col: new Float32Array(l.col), rPos: new Float32Array(l.rPos), rTime: new Float32Array(l.rTime) }))
        }
    }, []);

    // Fix: Unify the rotation of the main tracks to match the nodes so they don't tear apart over time
    useFrame((state) => {
        const t = state.clock.elapsedTime;
        if (groupRef.current) {
            groupRef.current.position.y = Math.sin(t * 0.1) * 0.1;
            groupRef.current.rotation.y = t * 0.005; // Lock main tracks to node rotation speed
        }

        if (dustRef.current) {
            dustRef.current.children.forEach((layer, i) => {
                layer.rotation.y = t * (0.002 + i * 0.001);
                layer.position.y = Math.sin(t * 0.1 + i) * 0.05;
            });
        }

        if (tracksRef.current) {
            tracksRef.current.children.forEach((layerGroup, i) => {
                const packets = layerGroup.children[1] as THREE.Points;
                if (packets && packets.material) {
                    (packets.material as THREE.PointsMaterial).opacity = (0.3 + 0.4 * Math.sin(t * 4 + i * 2));
                }
            });
        }
    });

    return (
        <group ref={groupRef}>
            <group ref={dustRef}>
                {diffuseLayers.map((layer, i) => (
                    <points key={`dust-${i}`}>
                        <bufferGeometry>
                            <bufferAttribute attach="attributes-position" args={[layer.pos, 3]} />
                            <bufferAttribute attach="attributes-color" args={[layer.col, 3]} />
                            <bufferAttribute attach="attributes-aRandomPos" args={[layer.rPos, 3]} />
                            <bufferAttribute attach="attributes-aRandomTime" args={[layer.rTime, 1]} />
                        </bufferGeometry>
                        <ConvergingMaterial size={0.7 + i*0.2} map={particleTexture} vertexColors transparent opacity={0.03} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </points>
                ))}
            </group>

            <group ref={tracksRef}>
                {trackLayers.map((layer, i) => (
                    <group key={`track-group-${i}`}>
                        <points>
                            <bufferGeometry>
                                <bufferAttribute attach="attributes-position" args={[layer.pos, 3]} />
                                <bufferAttribute attach="attributes-color" args={[layer.col, 3]} />
                                <bufferAttribute attach="attributes-aRandomPos" args={[layer.rPos, 3]} />
                                <bufferAttribute attach="attributes-aRandomTime" args={[layer.rTime, 1]} />
                            </bufferGeometry>
                            <ConvergingMaterial size={0.11} map={particleTexture} vertexColors transparent opacity={0.5} blending={THREE.AdditiveBlending} depthWrite={false} />
                        </points>
                        <points>
                            <bufferGeometry>
                                <bufferAttribute attach="attributes-position" args={[packetLayers[i].pos, 3]} />
                                <bufferAttribute attach="attributes-color" args={[packetLayers[i].col, 3]} />
                                <bufferAttribute attach="attributes-aRandomPos" args={[packetLayers[i].rPos, 3]} />
                                <bufferAttribute attach="attributes-aRandomTime" args={[packetLayers[i].rTime, 1]} />
                            </bufferGeometry>
                            <ConvergingMaterial size={0.5} map={particleTexture} vertexColors transparent opacity={0.7} blending={THREE.AdditiveBlending} depthWrite={false} />
                        </points>
                    </group>
                ))}
            </group>
        </group>
    );
}

function PointsOrbit({ color, secondaryColor, scale }: { color: string, secondaryColor: string, scale: number }) {
    const pRef = useRef<THREE.Points>(null);
    const count = 45;

    const { pos, col } = useMemo(() => {
        const p = new Float32Array(count * 3);
        const c = new Float32Array(count * 3);
        const c1 = new THREE.Color(color);
        const c2 = new THREE.Color(secondaryColor);

        for(let i=0; i<count; i++) {
            const r = 1.2 + Math.random() * 1.8;
            const theta = Math.random() * Math.PI * 2;
            const phi = Math.acos(Math.random() * 2 - 1);
            p[i*3] = r * Math.sin(phi) * Math.cos(theta);
            p[i*3+1] = r * Math.sin(phi) * Math.sin(theta);
            p[i*3+2] = r * Math.cos(phi);

            // Mix original color with secondary for organic variation
            const mixed = c1.clone().lerp(c2, Math.random() * 0.8 + 0.1);

            c[i*3] = mixed.r;
            c[i*3+1] = mixed.g;
            c[i*3+2] = mixed.b;
        }
        return { pos: p, col: c };
    }, [color, secondaryColor]);

    const accumulator = useRef(0);

    useFrame((state, delta) => {
        const t = state.clock.elapsedTime;
        if(pRef.current) {
            const timeScale = 1 + Math.max(0, 1 - t/4) * 20;
            accumulator.current += delta * timeScale;
            const a = accumulator.current;

            pRef.current.rotation.y = a * 0.1;
            pRef.current.rotation.x = a * 0.05;
            pRef.current.rotation.z = a * 0.08;

            const progress = Math.min(t / 3, 1);
            ((pRef.current as any).material as THREE.PointsMaterial).opacity = 0.3 * Math.pow(progress, 2);
        }
    });

    return (
        <points ref={pRef}>
            <bufferGeometry>
                <bufferAttribute attach="attributes-position" args={[pos, 3]} />
                <bufferAttribute attach="attributes-color" args={[col, 3]} />
            </bufferGeometry>
            <pointsMaterial map={particleTexture} vertexColors size={0.15 * scale} transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} />
        </points>
    );
}

// Delicate energy arcs connecting the main neural nodes
function NodeConnections() {
    const linesGroup = useRef<THREE.Group>(null);

    useFrame((state) => {
        const t = state.clock.elapsedTime;
        const progress = Math.max(0, Math.min((t - 2.0) / 4.0, 1));
        const ease = progress < 0.5 ? 8 * progress * progress * progress * progress : 1 - Math.pow(-2 * progress + 2, 4) / 2;
        if(linesGroup.current) {
            linesGroup.current.rotation.y = t * 0.005;
            linesGroup.current.position.y = Math.sin(t * 0.1) * 0.1;

            linesGroup.current.children.forEach((line) => {
                const material = (line as THREE.Line).material as THREE.LineBasicMaterial;
                if (material) {
                    material.opacity = 0.15 * ease;
                }
            });
        }
    });

    const angles = [Math.PI/2, Math.PI/2 + 0.9, Math.PI/2 + 2.2, Math.PI/2 - 2.2, Math.PI/2 - 0.9];
    const orderedIndices = [4, 0, 1, 2, 3];

    const lines = orderedIndices.map((nID, i) => {
        const nextID = orderedIndices[(i + 1) % 5];
        const a1 = angles[nID];
        const a2 = angles[nextID];

        const p1 = new THREE.Vector3(Math.cos(a1)*RADIUS_X, 0, Math.sin(a1)*RADIUS_Z);
        const p2 = new THREE.Vector3(Math.cos(a2)*RADIUS_X, 0, Math.sin(a2)*RADIUS_Z);

        const mid = p1.clone().lerp(p2, 0.5);
        mid.y -= 1.5;
        mid.x *= 0.9;
        mid.z *= 0.9;

        const curve = new THREE.QuadraticBezierCurve3(p1, mid, p2);
        const points = curve.getPoints(40);
        const lineGeo = new THREE.BufferGeometry().setFromPoints(points);

        return (
            <line key={`line-${i}`}>
                <bufferGeometry attach="geometry" {...lineGeo} />
                <lineBasicMaterial attach="material" color="#38bdf8" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} />
            </line>
        )
    });

    return <group ref={linesGroup}>{lines}</group>;
}

function NodeItem({ i, angle, x, z, color, secondaryColor, centerColor, scaleMulti, pLightIntensity }: any) {
    const groupRef = useRef<THREE.Group>(null);

    const finalPos = useMemo(() => new THREE.Vector3(x, 0, z), [x, z]);

    const startPos = useMemo(() => {
        const outR = 60 + Math.random() * 40;
        const yOff = (i % 2 === 0 ? 1 : -1) * (30 + Math.random() * 20);
        return new THREE.Vector3(
            Math.cos(angle) * outR,
            yOff,
            Math.sin(angle) * outR
        );
    }, [angle, i]);

    const basePos = useRef(new THREE.Vector3());
    const spiralOffset = useRef(new THREE.Vector3());

    useFrame((state) => {
        const t = state.clock.elapsedTime;
        const duration = 6.0 + i * 1.2;
        const progress = Math.min(t / duration, 1);

        // Stream of consciousness ease: very slow start, rapid rush, then gentle settle. (easeInOutQuart)
        const ease = progress < 0.5
            ? 8 * progress * progress * progress * progress
            : 1 - Math.pow(-2 * progress + 2, 4) / 2;

        const pulse = Math.sin(t * 1.5 + i);

        if (groupRef.current) {
            const chaoticAngle = angle + (1 - ease) * Math.PI * 6 * (i % 2 === 0 ? 1 : -1);
            const r = 35 * (1 - ease) * (1 - ease);
            spiralOffset.current.set(
                Math.cos(chaoticAngle) * r,
                0,
                Math.sin(chaoticAngle) * r
            );

            basePos.current.lerpVectors(startPos, finalPos, ease);
            groupRef.current.position.copy(basePos.current).add(spiralOffset.current);

            groupRef.current.scale.setScalar((1 + pulse * 0.04) * Math.max(0.01, ease));

            const localRing = groupRef.current.children[1];
            if (localRing) {
                localRing.rotation.z = t * (0.02 + i*0.01) + (1 - ease) * 30;
                localRing.rotation.x = -Math.PI/2 - 0.2 * Math.sin(t * 0.5 + i) + (1 - ease) * 20;
            }
        }
    });

    return (
        <group ref={groupRef}>

                {/* 1. Multi-layered Plasma Core */}
                <group>
                    {/* Super hot true center */}
                    <mesh>
                        <sphereGeometry args={[0.05 * scaleMulti, 32, 32]} />
                        <meshBasicMaterial color={centerColor} transparent opacity={0.9} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    {/* Primary colored corona */}
                    <mesh>
                        <sphereGeometry args={[0.10 * scaleMulti, 32, 32]} />
                        <meshBasicMaterial color={color} transparent opacity={0.4} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    {/* Secondary chromatic energy shell (adds complexity/impurity) */}
                    <mesh>
                        <sphereGeometry args={[0.14 * scaleMulti, 24, 24]} />
                        <meshBasicMaterial color={secondaryColor} transparent opacity={0.2} wireframe wireframeLinewidth={1.5} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                </group>

                {/* 2. Delicate protective orbital planes */}
                <group rotation={[-Math.PI/2, 0.1, 0]}>
                    <mesh>
                        <ringGeometry args={[0.6 * scaleMulti, 0.62 * scaleMulti, 64]} />
                        <meshBasicMaterial color={color} transparent opacity={0.15} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.DoubleSide} />
                    </mesh>
                    <mesh>
                        <ringGeometry args={[0.9 * scaleMulti, 0.91 * scaleMulti, 64]} />
                        <meshBasicMaterial color={secondaryColor} transparent opacity={0.1} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.DoubleSide} />
                    </mesh>
                </group>

                {/* 3. True Anamorphic Lens Flare Billboard */}
                <Billboard>
                    {/* Hot glowing core halo */}
                    <mesh>
                        <planeGeometry args={[2.5 * scaleMulti, 2.5 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={centerColor} transparent opacity={0.8} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>

                    {/* Diffuse ambient node aura - layered for gradient */}
                    <mesh>
                        <planeGeometry args={[7 * scaleMulti, 7 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={color} transparent opacity={0.3} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    <mesh>
                        <planeGeometry args={[12 * scaleMulti, 12 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={secondaryColor} transparent opacity={0.12} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>

                    {/* Sharp Horizontal Anamorphic Flare - layered with chromatic aberration */}
                    <mesh>
                        <planeGeometry args={[22 * scaleMulti, 0.5 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={color} transparent opacity={0.15} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    {/* Outer chromatic bleed */}
                    <mesh>
                        <planeGeometry args={[28 * scaleMulti, 0.9 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={secondaryColor} transparent opacity={0.06} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    <mesh>
                        <planeGeometry args={[12 * scaleMulti, 1.4 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={centerColor} transparent opacity={0.12} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>

                    {/* Sharp Vertical Flare - with slight offset color bleed */}
                    <mesh>
                        <planeGeometry args={[0.4 * scaleMulti, 18 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={color} transparent opacity={0.1} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    <mesh>
                        <planeGeometry args={[0.6 * scaleMulti, 12 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={secondaryColor} transparent opacity={0.05} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>

                    {/* Thin Diagonal Glint Marks */}
                    <mesh rotation={[0, 0, Math.PI / 4]}>
                        <planeGeometry args={[6 * scaleMulti, 0.25 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={centerColor} transparent opacity={0.08} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                    <mesh rotation={[0, 0, -Math.PI / 4]}>
                        <planeGeometry args={[6 * scaleMulti, 0.25 * scaleMulti]} />
                        <meshBasicMaterial map={particleTexture} color={centerColor} transparent opacity={0.08} blending={THREE.AdditiveBlending} depthWrite={false} />
                    </mesh>
                </Billboard>

                {/* 4. Authentic 3D light spread to illuminate local gas */}
                <pointLight color={color} intensity={pLightIntensity} distance={18} decay={1.5} />

                {/* 5. Frenetic orbital dust mixed with both energies */}
                <PointsOrbit color={color} secondaryColor={secondaryColor} scale={scaleMulti} />
            </group>
        );
}

// Celestial Nodes with photographic anamorphic flares
function Nodes() {
    const groupRef = useRef<THREE.Group>(null);

    useFrame((state) => {
        if (groupRef.current) {
            groupRef.current.rotation.y = state.clock.elapsedTime * 0.005;
            groupRef.current.position.y = Math.sin(state.clock.elapsedTime * 0.1) * 0.1;
        }
    });

    const nodes = Array.from({ length: NUM_NODES }).map((_, i) => {
        const angles = [Math.PI/2, Math.PI/2 + 0.9, Math.PI/2 + 2.2, Math.PI/2 - 2.2, Math.PI/2 - 0.9];
        const angle = angles[i] || (i / NUM_NODES) * Math.PI * 2 + Math.PI / 2;

        const x = Math.cos(angle) * RADIUS_X;
        const z = Math.sin(angle) * RADIUS_Z;

        let color = '#38bdf8'; // Blue cyan
        let secondaryColor = '#818cf8'; // Soft purple/indigo
        let centerColor = '#ffffff';
        let scaleMulti = 1.0;
        let pLightIntensity = 1.8;

        if (i === 0) {
            color = '#ff3b3b'; // Vibrant Red
            secondaryColor = '#ffaa00'; // Bright Gold
            centerColor = '#ffedcc';
            scaleMulti = 1.5;
            pLightIntensity = 4.0;
        } else if (i === 1) {
            color = '#0033ff'; // Deep Intense Blue
            secondaryColor = '#00d4ff'; // Bright Cyan
            centerColor = '#e0eeff';
            scaleMulti = 1.35;
            pLightIntensity = 4.0;
        } else if (i === 2) {
            color = '#6600ff'; // Deep Electric Purple
            secondaryColor = '#d400ff'; // Neon Magenta/Purple
            centerColor = '#f3e0ff';
            scaleMulti = 1.35;
            pLightIntensity = 4.0;
        } else if (i === 3) {
            color = '#00ff88'; // Neon Green
            secondaryColor = '#10b981'; // Emerald/Mint
            centerColor = '#d1fae5';
            scaleMulti = 1.35;
            pLightIntensity = 4.0;
        } else {
            color = '#f97316'; // Vibrant Orange
            secondaryColor = '#fde047'; // Bright Yellow
            centerColor = '#fef08a';
            scaleMulti = 1.35;
            pLightIntensity = 4.0;
        }

        return <NodeItem key={i} i={i} angle={angle} x={x} z={z} color={color} secondaryColor={secondaryColor} centerColor={centerColor} scaleMulti={scaleMulti} pLightIntensity={pLightIntensity} />;
    });

    return <group ref={groupRef}>{nodes}</group>;
}

// Intense cosmic void godrays providing scale
function BackgroundGodRays() {
    const groupRef = useRef<THREE.Group>(null);
    const raysRef = useRef<THREE.Group>(null);

    useFrame((state) => {
        const t = state.clock.elapsedTime;
        if (groupRef.current) {
            const progress = Math.min(t / 4.0, 1);
            const ease = progress < 0.5 ? 8 * progress * progress * progress * progress : 1 - Math.pow(-2 * progress + 2, 4) / 2;
            groupRef.current.position.y = -2 + (1 - ease) * 15; // Sweep down from heaven
        }
        if (raysRef.current) {
            // Subtle organic swaying of the light shafts
            raysRef.current.children.forEach((ray, i) => {
                ray.rotation.y = t * 0.02 + i * 0.3;
                ray.rotation.z = Math.sin(t * 0.05 + i) * 0.05;
                ray.position.y = 20 + Math.sin(t * 0.1 + i) * 2;
            });
        }
    });

    const rays = Array.from({ length: 15 }).map((_, i) => {
        const color = i % 4 === 0 ? "#fbbf24" : i % 3 === 0 ? "#c084fc" : "#38bdf8";
        const w = 4 + Math.random() * 15;
        const h = 40 + Math.random() * 30;
        const opacity = 0.015 + Math.random() * 0.015;
        const rotY = Math.random() * Math.PI;
        const offsetX = (Math.random() - 0.5) * 8;
        const offsetZ = (Math.random() - 0.5) * 8;
        return (
            <mesh key={i} rotation={[0, rotY, 0]} position={[offsetX, 20, offsetZ]}>
                <planeGeometry args={[w, h]} />
                <meshBasicMaterial map={particleTexture} color={color} transparent opacity={opacity} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.DoubleSide} />
            </mesh>
        );
    });

    return (
        <group position={[0, -2, -2]} ref={groupRef}>
            <Billboard>
                <mesh>
                    <planeGeometry args={[65, 65]} />
                    <meshBasicMaterial map={particleTexture} color="#05102e" transparent opacity={0.6} blending={THREE.AdditiveBlending} depthWrite={false} />
                </mesh>
                <mesh>
                    <planeGeometry args={[35, 35]} />
                    <meshBasicMaterial map={particleTexture} color="#1e3a8a" transparent opacity={0.3} blending={THREE.AdditiveBlending} depthWrite={false} />
                </mesh>
            </Billboard>

            <group ref={raysRef}>
                {rays}
                {/* Core ambient central shaft joining the network */}
                <mesh position={[0, 20, 0]}>
                    <cylinderGeometry args={[2, 16, 50, 32, 1, true]} />
                    <meshBasicMaterial color="#ffffff" transparent opacity={0.01} blending={THREE.AdditiveBlending} depthWrite={false} side={THREE.DoubleSide} />
                </mesh>
            </group>
        </group>
    );
}

// Floating massive out-of-focus dust particles for depth parallax
function ForegroundDust() {
    const pRef = useRef<THREE.Points>(null);
    const count = 180;

    const { positions, scales } = useMemo(() => {
        const pos = new Float32Array(count * 3);
        const scl = new Float32Array(count);
        for(let i=0; i<count; i++) {
            pos[i*3] = (Math.random() - 0.5) * 20;
            pos[i*3+1] = (Math.random() - 0.5) * 15;
            pos[i*3+2] = (Math.random() - 0.5) * 5 + 6; // Very close to camera
            scl[i] = Math.random() * 0.8 + 0.4;
        }
        return { positions: pos, scales: scl };
    }, []);

        useFrame((state) => {
        const t = state.clock.elapsedTime;
        if(pRef.current) {
            pRef.current.rotation.y = t * 0.02;
            pRef.current.rotation.x = t * 0.01;
            pRef.current.position.z = Math.sin(t * 0.05) * 3.0;

            const progress = Math.min(t / 8.0, 1);
            const ease = progress < 0.5 ? 8 * progress * progress * progress * progress : 1 - Math.pow(-2 * progress + 2, 4) / 2;
            ((pRef.current as any).material as THREE.PointsMaterial).opacity = 0.02 * ease;
        }
    });

    return (
        <points ref={pRef}>
            <bufferGeometry>
                <bufferAttribute attach="attributes-position" args={[positions, 3]} />
                <bufferAttribute attach="attributes-size" args={[scales, 1]} />
            </bufferGeometry>
            <pointsMaterial map={particleTexture} size={3.5} sizeAttenuation={true} color="#38bdf8" transparent opacity={0} blending={THREE.AdditiveBlending} depthWrite={false} />
        </points>
    );
}

export function Scene() {
    return (
        <Canvas camera={{ position: [0, 4.5, 20], fov: 42 }}>
            <color attach="background" args={['#02040b']} />

            <CameraRig />

            <Stars radius={150} depth={50} count={9000} factor={6} saturation={0.8} fade speed={0.4} />

            <BackgroundGodRays />
            <MeshNetworkRing />
            <NodeConnections />
            <Nodes />
            <ForegroundDust />

            <EffectComposer>
                <Bloom luminanceThreshold={0.15} mipmapBlur intensity={1.5} radius={0.7} />
                <Vignette eskil={false} offset={0.12} darkness={1.2} />
            </EffectComposer>
        </Canvas>
    );
}
