export const theme = {
  bg: "rgb(10, 10, 14)",
  bgPanel: "rgb(28, 28, 38)",
  bgLighter: "rgb(38, 38, 50)",
  bgInput: "rgb(22, 22, 32)",
  border: "rgb(55, 55, 68)",
  borderLight: "rgb(70, 70, 85)",
  text: "rgb(210, 210, 218)",
  textDim: "rgb(120, 120, 135)",
  textBright: "rgb(248, 248, 252)",
  green: "rgb(50, 195, 70)",
  red: "rgb(210, 55, 55)",
  yellow: "rgb(210, 195, 40)",
  blue: "rgb(70, 140, 230)",
  waveformColor: "rgb(80, 180, 240)",
  waveformMarker: "rgb(235, 65, 65)",
} as const;

export const deviceThemes = {
  compa: { accent: "rgb(235, 120, 30)", accentBright: "rgb(255, 155, 50)", accentDim: "rgb(160, 80, 18)" },
  sp404: { accent: "rgb(235, 120, 30)", accentBright: "rgb(255, 155, 50)", accentDim: "rgb(160, 80, 18)" },
  p6:    { accent: "rgb(255, 230, 0)",  accentBright: "rgb(255, 245, 80)", accentDim: "rgb(180, 160, 0)" },
  force: { accent: "rgb(220, 50, 50)",  accentBright: "rgb(255, 80, 80)",  accentDim: "rgb(140, 30, 30)" },
} as const;

export type DeviceTheme = keyof typeof deviceThemes;

export function applyDeviceTheme(name: DeviceTheme) {
  const t = deviceThemes[name];
  const root = document.documentElement.style;
  root.setProperty("--accent", t.accent);
  root.setProperty("--accent-bright", t.accentBright);
  root.setProperty("--accent-dim", t.accentDim);
}

export function detectDeviceTheme(deviceName: string): DeviceTheme {
  const n = deviceName.toLowerCase();
  if (n.includes("sp-404") || n.includes("sp404")) return "sp404";
  if (n.includes("p-6") || n.includes("roland p6")) return "p6";
  if (n.includes("force")) return "force";
  return "compa";
}
