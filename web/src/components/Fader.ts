type FaderOpts = {
  label?: string;
  orientation?: "vertical" | "horizontal";
  value?: number;
  min?: number;
  max?: number;
  centered?: boolean;
  onChange: (value: number) => void;
};

export function createFader(opts: FaderOpts): HTMLElement {
  const min = opts.min ?? 0;
  const max = opts.max ?? 127;
  const orientation = opts.orientation ?? "vertical";
  let value = opts.value ?? (opts.centered ? (min + max) / 2 : min);

  const wrap = document.createElement("div");
  wrap.className = `fader ${orientation === "horizontal" ? "horizontal" : ""}`.trim();

  const track = document.createElement("div");
  track.className = "fader-track";

  const fill = document.createElement("div");
  fill.className = "fader-fill";

  const thumb = document.createElement("div");
  thumb.className = "fader-thumb";

  track.append(fill, thumb);

  const update = () => {
    const norm = (value - min) / (max - min);
    if (orientation === "vertical") {
      fill.style.height = `${norm * 100}%`;
      thumb.style.bottom = `${norm * 100}%`;
    } else {
      fill.style.width = `${norm * 100}%`;
      thumb.style.left = `${norm * 100}%`;
    }
  };

  const setFromPointer = (e: PointerEvent) => {
    const rect = track.getBoundingClientRect();
    let norm: number;
    if (orientation === "vertical") {
      norm = 1 - (e.clientY - rect.top) / rect.height;
    } else {
      norm = (e.clientX - rect.left) / rect.width;
    }
    norm = Math.max(0, Math.min(1, norm));
    const newVal = min + norm * (max - min);
    if (newVal !== value) {
      value = newVal;
      update();
      opts.onChange(value);
    }
  };

  let dragging = false;
  track.addEventListener("pointerdown", (e) => {
    dragging = true;
    track.setPointerCapture(e.pointerId);
    setFromPointer(e);
    e.preventDefault();
  });
  track.addEventListener("pointermove", (e) => {
    if (dragging) setFromPointer(e);
  });
  track.addEventListener("pointerup", (e) => {
    dragging = false;
    try { track.releasePointerCapture(e.pointerId); } catch {}
  });
  track.addEventListener("dblclick", () => {
    if (opts.centered) {
      value = (min + max) / 2;
      update();
      opts.onChange(value);
    }
  });

  wrap.appendChild(track);
  if (opts.label) {
    const lbl = document.createElement("div");
    lbl.className = "fader-label";
    lbl.textContent = opts.label;
    wrap.appendChild(lbl);
  }

  update();
  return wrap;
}
