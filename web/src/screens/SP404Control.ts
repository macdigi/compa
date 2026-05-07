import type { MIDIDeviceInfo } from "../midi.ts";
import { sendCC } from "../midi.ts";
import { createKnob } from "../components/Knob.ts";
import { createFader } from "../components/Fader.ts";
import { createScope } from "../components/Scope.ts";
import { createUpgradeCard } from "../components/UpgradeCard.ts";
import { BUS12_FX } from "../sp404_data.ts";

const CC_FX_SELECT = 83;
const CC_CTRL = [16, 17, 18];
const CC_VOLUME = 7;

export function createSP404Control(opts: {
  device: MIDIDeviceInfo;
  analyser: AnalyserNode | null;
}): HTMLElement {
  const root = document.createElement("div");
  root.className = "layout";

  const main = document.createElement("div");
  main.style.display = "flex";
  main.style.flexDirection = "column";
  main.style.gap = "14px";

  // Scope panel
  const scopePanel = document.createElement("div");
  scopePanel.className = "panel";
  const scopeLabel = document.createElement("div");
  scopeLabel.className = "section-label";
  scopeLabel.textContent = "Scope";
  scopePanel.append(scopeLabel, createScope(opts.analyser));

  // Decks
  const decks = document.createElement("div");
  decks.style.display = "grid";
  decks.style.gridTemplateColumns = "1fr 1fr";
  decks.style.gap = "14px";
  decks.append(deck("Deck A · Bus 1", 1, opts.device), deck("Deck B · Bus 2", 2, opts.device));

  // Crossfader panel
  const xfPanel = document.createElement("div");
  xfPanel.className = "panel";
  xfPanel.style.textAlign = "center";
  const xfLabel = document.createElement("div");
  xfLabel.className = "section-label";
  xfLabel.textContent = "Crossfader (Deck A ⟷ Deck B)";
  const xf = createFader({
    label: "",
    orientation: "horizontal",
    min: 0,
    max: 127,
    value: 64,
    centered: true,
    onChange: (v) => {
      const norm = v / 127;
      const aVol = Math.round((1 - norm) * 127);
      const bVol = Math.round(norm * 127);
      sendCC(opts.device.output, 1, CC_VOLUME, aVol);
      sendCC(opts.device.output, 2, CC_VOLUME, bVol);
    },
  });
  xfPanel.append(xfLabel, xf);

  main.append(scopePanel, decks, xfPanel);

  // Sidebar
  const aside = document.createElement("div");
  aside.style.display = "flex";
  aside.style.flexDirection = "column";
  aside.style.gap = "14px";
  aside.appendChild(createUpgradeCard());

  root.append(main, aside);
  return root;
}

function deck(title: string, channel: 1 | 2, device: MIDIDeviceInfo): HTMLElement {
  const panel = document.createElement("div");
  panel.className = "panel";

  const label = document.createElement("div");
  label.className = "section-label";
  label.textContent = title;
  panel.appendChild(label);

  // FX select row
  const fxRow = document.createElement("div");
  fxRow.className = "fx-row";
  const fxLabel = document.createElement("label");
  fxLabel.textContent = "FX";
  const fxSelect = document.createElement("select");
  for (const [val, name] of Object.entries(BUS12_FX)) {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = name;
    fxSelect.appendChild(opt);
  }
  fxSelect.onchange = () => {
    sendCC(device.output, channel, CC_FX_SELECT, parseInt(fxSelect.value, 10));
  };
  fxRow.append(fxLabel, fxSelect);
  panel.appendChild(fxRow);

  // Three CTRL knobs
  const knobRow = document.createElement("div");
  knobRow.style.display = "flex";
  knobRow.style.gap = "16px";
  knobRow.style.justifyContent = "center";
  knobRow.style.padding = "12px 0 4px 0";

  for (let i = 0; i < 3; i++) {
    knobRow.appendChild(
      createKnob({
        label: `Ctrl ${i + 1}`,
        min: 0,
        max: 127,
        value: 64,
        onChange: (v) => sendCC(device.output, channel, CC_CTRL[i], v),
      })
    );
  }
  panel.appendChild(knobRow);

  // Volume fader
  const faderRow = document.createElement("div");
  faderRow.style.display = "flex";
  faderRow.style.justifyContent = "center";
  faderRow.style.padding = "8px 0";
  faderRow.appendChild(
    createFader({
      label: "Volume",
      orientation: "vertical",
      min: 0,
      max: 127,
      value: 100,
      onChange: (v) => sendCC(device.output, channel, CC_VOLUME, v),
    })
  );
  panel.appendChild(faderRow);

  return panel;
}
