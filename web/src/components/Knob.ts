type KnobOpts = {
  label: string;
  value?: number;
  min?: number;
  max?: number;
  format?: (v: number) => string;
  onChange: (value: number) => void;
};

export function createKnob(opts: KnobOpts): HTMLElement {
  const min = opts.min ?? 0;
  const max = opts.max ?? 127;
  const format = opts.format ?? ((v) => Math.round(v).toString());
  let value = opts.value ?? min;

  const wrap = document.createElement("div");
  wrap.className = "knob";

  const dial = document.createElement("canvas");
  dial.className = "knob-dial";
  dial.width = 56;
  dial.height = 56;

  const label = document.createElement("div");
  label.className = "knob-label";
  label.textContent = opts.label;

  const valueEl = document.createElement("div");
  valueEl.className = "knob-value";

  const draw = () => {
    const ctx = dial.getContext("2d");
    if (!ctx) return;
    const w = dial.width;
    const h = dial.height;
    const cx = w / 2;
    const cy = h / 2;
    const r = w / 2 - 4;
    ctx.clearRect(0, 0, w, h);

    const norm = (value - min) / (max - min);
    const startAngle = 0.75 * Math.PI;
    const endAngle = 2.25 * Math.PI;
    const valAngle = startAngle + (endAngle - startAngle) * norm;

    ctx.lineWidth = 4;
    ctx.lineCap = "round";

    ctx.strokeStyle = getCssVar("--bg-input");
    ctx.beginPath();
    ctx.arc(cx, cy, r, startAngle, endAngle);
    ctx.stroke();

    ctx.strokeStyle = getCssVar("--accent");
    ctx.beginPath();
    ctx.arc(cx, cy, r, startAngle, valAngle);
    ctx.stroke();

    ctx.fillStyle = getCssVar("--bg-lighter");
    ctx.beginPath();
    ctx.arc(cx, cy, r - 7, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = getCssVar("--accent-bright");
    ctx.lineWidth = 2;
    ctx.beginPath();
    const ix = cx + Math.cos(valAngle) * (r - 11);
    const iy = cy + Math.sin(valAngle) * (r - 11);
    const ox = cx + Math.cos(valAngle) * (r - 4);
    const oy = cy + Math.sin(valAngle) * (r - 4);
    ctx.moveTo(ix, iy);
    ctx.lineTo(ox, oy);
    ctx.stroke();

    valueEl.textContent = format(value);
  };

  let dragging = false;
  let startY = 0;
  let startVal = value;

  const onPointerDown = (e: PointerEvent) => {
    dragging = true;
    startY = e.clientY;
    startVal = value;
    dial.setPointerCapture(e.pointerId);
    e.preventDefault();
  };
  const onPointerMove = (e: PointerEvent) => {
    if (!dragging) return;
    const dy = startY - e.clientY;
    const range = max - min;
    const newVal = clamp(startVal + (dy / 140) * range, min, max);
    if (newVal !== value) {
      value = newVal;
      draw();
      opts.onChange(value);
    }
  };
  const onPointerUp = (e: PointerEvent) => {
    dragging = false;
    try { dial.releasePointerCapture(e.pointerId); } catch {}
  };
  const onWheel = (e: WheelEvent) => {
    e.preventDefault();
    const range = max - min;
    const step = range / 100;
    value = clamp(value - Math.sign(e.deltaY) * step, min, max);
    draw();
    opts.onChange(value);
  };

  dial.addEventListener("pointerdown", onPointerDown);
  dial.addEventListener("pointermove", onPointerMove);
  dial.addEventListener("pointerup", onPointerUp);
  dial.addEventListener("pointercancel", onPointerUp);
  dial.addEventListener("wheel", onWheel, { passive: false });

  wrap.append(dial, label, valueEl);
  draw();

  return wrap;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

function getCssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}
