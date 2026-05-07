import type { MIDIDeviceInfo } from "../midi.ts";

type PickerOpts = {
  devices: MIDIDeviceInfo[];
  iosDetected: boolean;
  onPick: (device: MIDIDeviceInfo) => void;
  onScopeOnly: () => void;
};

export function createDevicePicker(opts: PickerOpts): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "picker";

  const title = document.createElement("h2");
  title.textContent = "Pick your gear";

  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = opts.devices.length
    ? "Connected MIDI devices below. Plug in your SP-404 or P-6 over USB if you don't see it."
    : "No MIDI devices found. Plug in your SP-404 or P-6 via USB and refresh.";

  wrap.append(title, hint);

  if (opts.iosDetected) {
    const warn = document.createElement("div");
    warn.className = "ios-warning";
    warn.innerHTML = "iPhone/iPad detected. Safari (and every iOS browser) doesn't support Web MIDI — that's an Apple restriction. You can still use the <strong>scope-only</strong> mode to monitor audio. For full control, use a Mac/PC with Chrome.";
    wrap.appendChild(warn);

    const scopeBtn = document.createElement("button");
    scopeBtn.className = "primary";
    scopeBtn.textContent = "Continue with scope-only mode";
    scopeBtn.onclick = opts.onScopeOnly;
    wrap.appendChild(scopeBtn);
    return wrap;
  }

  if (opts.devices.length) {
    const select = document.createElement("select");
    for (const d of opts.devices) {
      const option = document.createElement("option");
      option.value = d.id;
      option.textContent = `${d.name}${d.manufacturer ? ` — ${d.manufacturer}` : ""}`;
      select.appendChild(option);
    }
    const go = document.createElement("button");
    go.className = "primary";
    go.textContent = "Use this device";
    go.onclick = () => {
      const picked = opts.devices.find((d) => d.id === select.value);
      if (picked) opts.onPick(picked);
    };
    wrap.append(select, go);
  } else {
    const refresh = document.createElement("button");
    refresh.textContent = "Refresh";
    refresh.onclick = () => location.reload();
    wrap.appendChild(refresh);
  }

  return wrap;
}
