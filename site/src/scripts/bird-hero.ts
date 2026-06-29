/* Mynah hero — the living mark.
 *
 * Storyboard (loops): a blue mynah rests in the background → it eases to red over 0.3s as a
 * centered red sound wave rises in the foreground, held together for ~2s (recording) → the
 * wave fades out (staying red) while the bird eases to yellow over 0.3s (transcribing) → the
 * bird eases back to blue over 0.3s as the terminal types the words it "heard" → a ~1.5s
 * pause, then again.
 *
 * The bird is a point cloud sampled at runtime from the (wave-less) bird mark in WebGL; the
 * sound wave is drawn on a 2D canvas layered in front. Bird colour moves in explicit 0.3s
 * eased steps, never a continuous drift. Degrades to a static bird image for
 * prefers-reduced-motion / no-WebGL.
 */
import * as THREE from 'three';

type PhaseName = 'idle' | 'rec' | 'trans' | 'type' | 'hold';
type Phase = { name: PhaseName; label: string; chip: 'idle' | 'rec' | 'trans'; dur: number };

const CSS = { idle: '#5b7bf0', rec: '#ff4d5e', trans: '#ffb44d' };
const COLORS = {
  idle: new THREE.Color(CSS.idle),
  rec: new THREE.Color(CSS.rec),
  trans: new THREE.Color(CSS.trans),
};

const PHASES: Phase[] = [
  { name: 'idle', label: 'Idle', chip: 'idle', dur: 1350 },
  { name: 'rec', label: 'Recording…', chip: 'rec', dur: 3000 }, // red bird + wave, held ~3s
  { name: 'trans', label: 'Transcribing…', chip: 'trans', dur: 1050 },
  { name: 'type', label: 'Done', chip: 'idle', dur: 1700 },
  { name: 'hold', label: 'Done', chip: 'idle', dur: 1500 }, // the ~1.5s pause before looping
];
const PHRASE = 'add a dark-mode toggle to the settings panel';

export function initBirdHero() {
  const birdCanvas = document.getElementById('bird') as HTMLCanvasElement | null;
  const waveCanvas = document.getElementById('wave') as HTMLCanvasElement | null;
  const stage = birdCanvas?.parentElement as HTMLElement | null;
  const fallback = document.querySelector('.bird-fallback') as HTMLElement | null;
  const key = document.getElementById('key');
  const chip = document.getElementById('chip');
  const chipDot = document.getElementById('chipdot');
  const chipTxt = document.getElementById('chiptxt');
  const typedEl = document.getElementById('typed');
  const cursor = document.getElementById('cursor');
  if (!birdCanvas || !waveCanvas || !stage) return;

  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const iconUrl = `${import.meta.env.BASE_URL}brand/mynah-bird.png`;

  // Warm the editor's mono font up front so the first typed sentence never flashes in a
  // fallback face (the "grey text for a moment" before the real text).
  try {
    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts;
    fonts?.load('400 14px "JetBrains Mono"');
    fonts?.load('500 14px "JetBrains Mono"');
  } catch {
    /* FontFaceSet unsupported — ignore */
  }

  function setChip(label: string, colorKey: keyof typeof CSS) {
    const c = CSS[colorKey];
    if (chipTxt) chipTxt.textContent = label;
    if (chipDot) {
      chipDot.style.background = c;
      chipDot.style.boxShadow = `0 0 10px ${c}`;
    }
    if (chip) {
      chip.style.borderColor = c + '88';
      chip.style.color = c;
    }
  }

  function bailToStatic() {
    if (fallback) fallback.hidden = false;
    birdCanvas!.style.display = 'none';
    waveCanvas!.style.display = 'none';
    if (typedEl) typedEl.textContent = PHRASE;
    if (cursor) (cursor as HTMLElement).style.background = CSS.idle;
    setChip('Idle', 'idle');
  }

  if (reduce) {
    bailToStatic();
    return;
  }

  // ---- WebGL bird (background) ----
  let renderer: THREE.WebGLRenderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas: birdCanvas, antialias: true, alpha: true });
  } catch {
    bailToStatic();
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100);
  camera.position.z = 7.2;
  const group = new THREE.Group();
  scene.add(group);

  // ---- sound wave (foreground 2D canvas) ----
  const wctx = waveCanvas.getContext('2d')!;
  let WW = 0;
  let WH = 0;

  function resize() {
    const w = stage!.clientWidth;
    const h = stage!.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    WW = w;
    WH = h;
    waveCanvas!.width = w * dpr;
    waveCanvas!.height = h * dpr;
    wctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resize();
  window.addEventListener('resize', resize);

  // soft round sprite for the glow points
  function makeSprite(): THREE.Texture {
    const s = 64;
    const c = document.createElement('canvas');
    c.width = c.height = s;
    const g = c.getContext('2d')!;
    const grd = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
    grd.addColorStop(0, 'rgba(255,255,255,1)');
    grd.addColorStop(0.35, 'rgba(255,255,255,0.85)');
    grd.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grd;
    g.fillRect(0, 0, s, s);
    return new THREE.CanvasTexture(c);
  }
  const sprite = makeSprite();

  // pointer parallax (gentle)
  const pointer = { x: 0, y: 0 };
  window.addEventListener('pointermove', (e) => {
    const r = stage!.getBoundingClientRect();
    pointer.x = ((e.clientX - r.left) / r.width - 0.5) * 2;
    pointer.y = ((e.clientY - r.top) / r.height - 0.5) * 2;
  });

  let points: THREE.Points | null = null;
  let base: Float32Array;
  let pos: Float32Array;
  let seed: Float32Array;
  let count = 0;

  function buildFromImage(img: HTMLImageElement) {
    const W = 150;
    const H = 150;
    const oc = document.createElement('canvas');
    oc.width = W;
    oc.height = H;
    const octx = oc.getContext('2d')!;
    octx.drawImage(img, 0, 0, W, H);
    const data = octx.getImageData(0, 0, W, H).data;

    const px: number[] = [];
    const py: number[] = [];
    for (let y = 0; y < H; y++) {
      for (let x = 0; x < W; x++) {
        const i = (y * W + x) * 4;
        if (data[i + 3] < 120) continue;
        if ((data[i] + data[i + 1] + data[i + 2]) / 3 > 232) continue; // drop white tile bg
        if (Math.random() > 0.62) continue; // light dither so it isn't a strict grid
        px.push((x / W - 0.5) * 5.4);
        py.push(-(y / H - 0.5) * 5.4);
      }
    }

    count = px.length;
    base = new Float32Array(count * 3);
    pos = new Float32Array(count * 3);
    seed = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      base[i * 3] = px[i];
      base[i * 3 + 1] = py[i];
      base[i * 3 + 2] = (Math.random() - 0.5) * 0.25;
      pos[i * 3] = base[i * 3];
      pos[i * 3 + 1] = base[i * 3 + 1];
      pos[i * 3 + 2] = base[i * 3 + 2];
      seed[i] = Math.random() * Math.PI * 2;
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.074,
      map: sprite,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      color: COLORS.idle.clone(),
    });
    points = new THREE.Points(geo, mat);
    group.add(points);
    start();
  }

  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.onload = () => buildFromImage(img);
  img.onerror = bailToStatic;
  img.src = iconUrl;

  // ---- lifecycle ----
  // The whole animation is a pure function of elapsed time: phase, bird colour, wave level and
  // typed text are all derived from `tIn` within the current phase. No event-based state — so it
  // can't drift, and it can be frozen at any moment via window.__mynahFreeze (ms into the loop)
  // for deterministic screenshots.
  const COL_DUR = 300; // 0.3s colour animations
  const LOOP = PHASES.reduce((a, p) => a + p.dur, 0);
  const TYPE_DUR = PHASES.find((p) => p.name === 'type')!.dur;
  const loopStart = performance.now();
  const curColor = COLORS.idle.clone();

  const smooth = (k: number) => {
    k = Math.min(Math.max(k, 0), 1);
    return k * k * (3 - 2 * k);
  };

  function setBirdColor(name: PhaseName, tIn: number) {
    const e = smooth(tIn / COL_DUR);
    if (name === 'rec') curColor.copy(COLORS.idle).lerp(COLORS.rec, e); // blue -> red
    else if (name === 'trans') curColor.copy(COLORS.rec).lerp(COLORS.trans, e); // red -> yellow
    else if (name === 'type') curColor.copy(COLORS.trans).lerp(COLORS.idle, e); // yellow -> blue
    else curColor.copy(COLORS.idle); // idle / hold → blue
  }

  function waveAt(name: PhaseName, tIn: number) {
    if (name === 'rec') return Math.min(tIn / COL_DUR, 1); // rises over 0.3s, then full
    if (name === 'trans') return Math.max(0, 1 - tIn / COL_DUR); // fades over 0.3s, then gone
    return 0;
  }

  function textAt(name: PhaseName, tIn: number) {
    if (name === 'type') {
      const k = Math.min(tIn / (TYPE_DUR * 0.92), 1);
      return PHRASE.slice(0, Math.floor(k * PHRASE.length));
    }
    if (name === 'hold') return PHRASE;
    return ''; // idle / rec / trans
  }

  function start() {
    // Only burn frames while the hero is on screen (and the tab is visible).
    let onScreen = true;
    const sync = () => renderer.setAnimationLoop(onScreen && !document.hidden ? frame : null);
    if ('IntersectionObserver' in window) {
      new IntersectionObserver((e) => {
        onScreen = e.some((x) => x.isIntersecting);
        sync();
      }).observe(stage!);
    }
    document.addEventListener('visibilitychange', sync);
    sync();
  }

  // a centered, glowing sound wave that fades in/out with `level` (0..1)
  function drawWave(now: number, level: number) {
    wctx.clearRect(0, 0, WW, WH);
    if (level <= 0.002) return;
    const y0 = WH * 0.5;
    const amp = WH * 0.3 * level;
    // the wave is always the recording red (brightened toward white so it reads in the
    // foreground) — it never follows the bird to yellow.
    const r = Math.round(COLORS.rec.r * 255 * 0.45 + 255 * 0.55);
    const g = Math.round(COLORS.rec.g * 255 * 0.45 + 255 * 0.55);
    const b = Math.round(COLORS.rec.b * 255 * 0.45 + 255 * 0.55);

    const trace = (lw: number, a: number) => {
      wctx.beginPath();
      for (let x = 0; x <= WW; x += 2) {
        const nx = x / WW;
        const env = Math.sin(nx * Math.PI); // fade to nothing at both ends → reads as centered
        const w = Math.sin(nx * 16 - now * 0.008) * 0.6 + Math.sin(nx * 7 - now * 0.0045) * 0.4;
        const y = y0 - env * w * amp;
        x === 0 ? wctx.moveTo(x, y) : wctx.lineTo(x, y);
      }
      wctx.strokeStyle = `rgba(${r},${g},${b},${a * level})`;
      wctx.lineWidth = lw;
      wctx.lineJoin = 'round';
      wctx.lineCap = 'round';
      wctx.stroke();
    };

    wctx.shadowColor = `rgba(${r},${g},${b},${0.9 * level})`;
    wctx.shadowBlur = 26;
    trace(4, 0.95); // glowing core
    wctx.shadowBlur = 0;
    trace(1.4, 0.95); // crisp centre line
  }

  function frame() {
    // motion (breathe / wave scroll) uses a continuous clock so it never resets at the loop seam
    const anim = performance.now();
    // phase/state uses elapsed-in-loop, or a frozen value for deterministic screenshots
    const freeze = (window as unknown as { __mynahFreeze?: number }).__mynahFreeze;
    const elapsed = typeof freeze === 'number' ? freeze : anim - loopStart;
    let t = ((elapsed % LOOP) + LOOP) % LOOP;
    let phase: Phase = PHASES[PHASES.length - 1];
    for (const p of PHASES) {
      if (t < p.dur) {
        phase = p;
        break;
      }
      t -= p.dur;
    }
    const tIn = t;

    setChip(phase.label, phase.chip);
    key?.classList.toggle('pressed', phase.name === 'rec');
    setBirdColor(phase.name, tIn);

    // bird: hold its shape, just breathe
    if (points) {
      (points.material as THREE.PointsMaterial).color.copy(curColor);
      const breathe = 1 + Math.sin(anim * 0.0011) * 0.01;
      for (let i = 0; i < count; i++) {
        pos[i * 3] = base[i * 3] * breathe;
        pos[i * 3 + 1] = base[i * 3 + 1] * breathe + Math.sin(anim * 0.0011 + seed[i]) * 0.011;
        pos[i * 3 + 2] = base[i * 3 + 2] + Math.cos(anim * 0.0009 + seed[i]) * 0.04;
      }
      (points.geometry.attributes.position as THREE.BufferAttribute).needsUpdate = true;
    }

    drawWave(anim, waveAt(phase.name, tIn));

    // calm parallax + a gentle bob
    group.rotation.y += (pointer.x * 0.16 - group.rotation.y) * 0.04;
    group.rotation.x += (-pointer.y * 0.09 - group.rotation.x) * 0.04;
    group.position.y = Math.sin(anim * 0.001) * 0.1;

    // terminal text
    if (typedEl) {
      const tx = textAt(phase.name, tIn);
      if (typedEl.textContent !== tx) typedEl.textContent = tx;
      if (cursor) {
        (cursor as HTMLElement).style.background =
          phase.name === 'idle' ? CSS.trans : '#' + curColor.getHexString();
      }
    }

    renderer.render(scene, camera);
  }
}
