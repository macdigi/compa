import { requestMIDI, pairDevices, type MIDIDeviceInfo } from "./midi.ts";
import { requestAudioInput } from "./audio.ts";
import { applyDeviceTheme, detectDeviceTheme } from "./theme.ts";
import { createDevicePicker } from "./components/DevicePicker.ts";
import { createScope } from "./components/Scope.ts";
import { createSP404Control } from "./screens/SP404Control.ts";

const MANTRAS = [
  "compa is here. plug something in.",
  "your gear, your move.",
  "one cable, one companion.",
  "knobs over menus.",
  "the box does more.",
];

async function main() {
  const app = document.querySelector<HTMLDivElement>("#app");
  if (!app) return;

  app.innerHTML = "";
  app.appendChild(buildHeader("Compa", ""));
  app.appendChild(buildContent("Detecting devices…"));
  app.appendChild(buildMantra());

  const midi = await requestMIDI();

  if (midi.kind === "unsupported") {
    if (midi.reason === "ios") {
      // iOS scope-only path
      const audio = await requestAudioInput();
      replaceContent(
        app,
        createDevicePicker({
          devices: [],
          iosDetected: true,
          onPick: () => {},
          onScopeOnly: () => {
            replaceContent(app, buildScopeOnly(audio.kind === "ok" ? audio.analyser : null));
          },
        })
      );
      return;
    }
    replaceContent(
      app,
      buildContent(
        midi.reason === "safari"
          ? "Safari doesn't support Web MIDI. Open this page in Chrome or Edge to control your gear."
          : "Web MIDI isn't available in this browser. Try Chrome, Edge, or Firefox on desktop."
      )
    );
    return;
  }

  const devices = pairDevices(midi.access);
  midi.access.onstatechange = () => {
    location.reload();
  };

  if (devices.length === 0) {
    replaceContent(
      app,
      createDevicePicker({
        devices: [],
        iosDetected: false,
        onPick: () => {},
        onScopeOnly: () => {},
      })
    );
    return;
  }

  // Auto-pick if there's only one MIDI device, else show picker
  if (devices.length === 1) {
    await launchControl(app, devices[0]);
  } else {
    replaceContent(
      app,
      createDevicePicker({
        devices,
        iosDetected: false,
        onPick: (d) => launchControl(app, d),
        onScopeOnly: () => {},
      })
    );
  }
}

async function launchControl(app: HTMLElement, device: MIDIDeviceInfo) {
  applyDeviceTheme(detectDeviceTheme(device.name));
  app.innerHTML = "";
  app.appendChild(buildHeader("Compa", `${device.name} · connected`));
  app.appendChild(buildContent("Requesting audio access for the scope…"));
  app.appendChild(buildMantra());

  const audio = await requestAudioInput();
  const analyser = audio.kind === "ok" ? audio.analyser : null;

  const screen = createSP404Control({ device, analyser });
  replaceContent(app, screen);
}

function replaceContent(app: HTMLElement, content: HTMLElement) {
  const existing = app.querySelector(".layout, .picker, .stub, .scope-only");
  if (existing) {
    existing.replaceWith(content);
  } else {
    app.appendChild(content);
  }
}

function buildHeader(title: string, deviceLabel: string): HTMLElement {
  const header = document.createElement("header");
  header.className = "header";
  const logo = document.createElement("div");
  logo.className = "logo";
  logo.textContent = title;
  const device = document.createElement("div");
  device.className = "device";
  device.textContent = deviceLabel;
  header.append(logo, device);
  return header;
}

function buildContent(text: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "stub";
  wrap.style.flex = "1";
  wrap.style.display = "flex";
  wrap.style.alignItems = "center";
  wrap.style.justifyContent = "center";
  wrap.style.color = "var(--text-dim)";
  wrap.style.fontFamily = "var(--font-mono)";
  wrap.style.fontSize = "13px";
  wrap.textContent = text;
  return wrap;
}

function buildScopeOnly(analyser: AnalyserNode | null): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "scope-only";
  wrap.style.padding = "24px";
  wrap.style.flex = "1";
  const panel = document.createElement("div");
  panel.className = "panel";
  const lbl = document.createElement("div");
  lbl.className = "section-label";
  lbl.textContent = "Scope (iOS read-only mode)";
  panel.append(lbl, createScope(analyser));
  wrap.appendChild(panel);
  return wrap;
}

function buildMantra(): HTMLElement {
  const m = document.createElement("div");
  m.className = "mantra";
  m.textContent = MANTRAS[Math.floor(Math.random() * MANTRAS.length)];
  return m;
}

main();
