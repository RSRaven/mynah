/* Mynah hero — self-playing loop: idle → recording → transcribing → typing.
   Drives the waveform canvas, the F9 key, the state chip and the editor typing. */

(() => {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const canvas = document.getElementById("wave");
  const ctx = canvas.getContext("2d");
  const key = document.getElementById("key");
  const chip = document.getElementById("chip");
  const chipDot = document.getElementById("chipdot");
  const chipTxt = document.getElementById("chiptxt");
  const typedEl = document.getElementById("typed");
  const cursor = document.getElementById("cursor");

  const COLORS = {
    idle:  [59, 167, 255],
    rec:   [255, 77, 94],
    trans: [255, 180, 77],
  };

  // lifecycle timeline (ms)
  const PHASES = [
    { name: "idle",  label: "Idle",          color: "idle",  dur: 1600 },
    { name: "rec",   label: "Recording…",    color: "rec",   dur: 2600 },
    { name: "trans", label: "Transcribing…", color: "trans", dur: 1500 },
    { name: "type",  label: "Done",          color: "idle",  dur: 2600 },
  ];
  const PHRASE = "add a dark-mode toggle to the settings panel";

  // HiDPI canvas sizing
  let W = 0, H = 0, DPR = 1;
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 2);
    const r = canvas.getBoundingClientRect();
    W = r.width; H = r.height;
    canvas.width = W * DPR; canvas.height = H * DPR;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  resize();
  window.addEventListener("resize", resize);

  // smooth color (lerp toward target each frame)
  let cur = COLORS.idle.slice();
  function lerpColor(target, k) {
    for (let i = 0; i < 3; i++) cur[i] += (target[i] - cur[i]) * k;
  }
  const rgb = (c, a = 1) => `rgba(${c[0]|0},${c[1]|0},${c[2]|0},${a})`;

  // a smooth pseudo-speech envelope across x
  function speechAmp(x, t) {
    const nx = x / W;
    const env = Math.sin(nx * Math.PI);                 // fade at the edges
    const s =
      Math.sin(nx * 13 + t * 0.009) * 0.5 +
      Math.sin(nx * 27 - t * 0.013) * 0.3 +
      Math.sin(nx * 51 + t * 0.021) * 0.2;
    const burst = 0.6 + 0.4 * Math.sin(t * 0.006 + nx * 4); // syllable-like swells
    return env * s * burst;
  }

  let phaseIdx = 0, phaseStart = performance.now(), startTime = phaseStart;

  function setPhase(i) {
    phaseIdx = i;
    const p = PHASES[i];
    phaseStart = performance.now();
    chipTxt.textContent = p.label;
    const col = COLORS[p.color];
    chipDot.style.background = rgb(col);
    chipDot.style.boxShadow = `0 0 10px ${rgb(col)}`;
    chip.style.borderColor = rgb(col, 0.5);
    chip.style.color = rgb(col, 0.95);
    key.classList.toggle("pressed", p.name === "rec");
    if (p.name === "idle") typedEl.textContent = "";   // reset for next loop
  }
  setPhase(0);

  function draw(now) {
    const p = PHASES[phaseIdx];
    const tInPhase = now - phaseStart;
    if (tInPhase > p.dur) setPhase((phaseIdx + 1) % PHASES.length);

    const phase = PHASES[phaseIdx];
    lerpColor(COLORS[phase.color], 0.08);

    // amplitude target per phase
    let ampTarget = 6;                                  // idle baseline
    if (phase.name === "rec") ampTarget = H * 0.34;
    else if (phase.name === "trans") {
      const k = Math.min((now - phaseStart) / phase.dur, 1);
      ampTarget = (H * 0.34) * (1 - k) + 4 * k;         // decay to flat
    } else if (phase.name === "type") ampTarget = 5;

    ctx.clearRect(0, 0, W, H);
    const y0 = H / 2;

    // build the path
    ctx.beginPath();
    const step = 2;
    for (let x = 0; x <= W; x += step) {
      let y;
      if (phase.name === "rec" || phase.name === "trans") {
        y = y0 - speechAmp(x, now) * ampTarget;
      } else {
        y = y0 - Math.sin(x * 0.025 + now * 0.0022) * ampTarget; // gentle idle/typing
      }
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }

    // soft fill under the line
    const grad = ctx.createLinearGradient(0, 0, 0, H);
    grad.addColorStop(0, rgb(cur, 0.18));
    grad.addColorStop(1, rgb(cur, 0));
    ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();

    // re-stroke the line itself with glow
    ctx.beginPath();
    for (let x = 0; x <= W; x += step) {
      let y;
      if (phase.name === "rec" || phase.name === "trans") {
        y = y0 - speechAmp(x, now) * ampTarget;
      } else {
        y = y0 - Math.sin(x * 0.025 + now * 0.0022) * ampTarget;
      }
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.strokeStyle = rgb(cur);
    ctx.lineWidth = 2.5;
    ctx.lineJoin = "round";
    ctx.shadowColor = rgb(cur, 0.9);
    ctx.shadowBlur = 16;
    ctx.stroke();
    ctx.shadowBlur = 0;

    // typing during the "type" phase
    if (phase.name === "type") {
      const k = Math.min((now - phaseStart) / (phase.dur * 0.8), 1);
      const n = Math.floor(k * PHRASE.length);
      typedEl.textContent = PHRASE.slice(0, n);
      cursor.style.background = rgb(cur);
    } else if (phase.name === "idle") {
      cursor.style.background = rgb(COLORS.trans);
    }

    requestAnimationFrame(draw);
  }

  if (reduce) {
    // static: draw one idle frame + show the finished sentence
    ctx.clearRect(0, 0, W, H);
    ctx.beginPath();
    for (let x = 0; x <= W; x += 2) ctx.lineTo(x, H/2 - Math.sin(x*0.03)*8);
    ctx.strokeStyle = rgb(COLORS.idle); ctx.lineWidth = 2.5; ctx.stroke();
    typedEl.textContent = PHRASE;
    chipTxt.textContent = "Idle";
  } else {
    requestAnimationFrame(draw);
  }

  // scroll-reveal + pipeline light-up
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add("in");
      if (e.target.querySelector(".node")) {
        const nodes = e.target.querySelectorAll(".node");
        nodes.forEach((n, i) => setTimeout(() => n.classList.add("lit"), 250 + i * 320));
      }
      io.unobserve(e.target);
    });
  }, { threshold: 0.2 });
  document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
})();
