export function createScope(analyser: AnalyserNode | null): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.className = "scope";
  canvas.height = 140;

  const resize = () => {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    if (rect.width === 0) return;
    canvas.width = Math.floor(rect.width * dpr);
    canvas.height = Math.floor(140 * dpr);
    const ctx = canvas.getContext("2d");
    ctx?.scale(dpr, dpr);
  };

  let raf = 0;
  const data = analyser ? new Uint8Array(analyser.fftSize) : null;

  const draw = () => {
    raf = requestAnimationFrame(draw);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;

    ctx.fillStyle = getVar("--bg");
    ctx.fillRect(0, 0, w, h);

    ctx.strokeStyle = getVar("--border");
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();

    if (!analyser || !data) {
      ctx.fillStyle = getVar("--text-dim");
      ctx.font = '11px "JetBrains Mono", monospace';
      ctx.textAlign = "center";
      ctx.fillText("audio input not connected", w / 2, h / 2 - 8);
      ctx.fillText("(grant mic permission to enable scope)", w / 2, h / 2 + 12);
      return;
    }

    analyser.getByteTimeDomainData(data);
    ctx.lineWidth = 1.6;
    ctx.strokeStyle = getVar("--waveform");
    ctx.beginPath();
    const slice = w / data.length;
    let x = 0;
    for (let i = 0; i < data.length; i++) {
      const v = data[i] / 128.0;
      const y = (v * h) / 2;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
      x += slice;
    }
    ctx.stroke();
  };

  resize();
  window.addEventListener("resize", resize);
  draw();

  (canvas as any).stop = () => cancelAnimationFrame(raf);
  return canvas;
}

function getVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}
