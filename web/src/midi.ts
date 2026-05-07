export type MIDIDeviceInfo = {
  id: string;
  name: string;
  manufacturer: string;
  input: MIDIInput;
  output: MIDIOutput;
};

export type MIDISupport =
  | { kind: "ok"; access: MIDIAccess }
  | { kind: "unsupported"; reason: string };

export async function requestMIDI(): Promise<MIDISupport> {
  const nav = navigator as Navigator & {
    requestMIDIAccess?: (opts?: { sysex?: boolean }) => Promise<MIDIAccess>;
  };
  if (typeof nav.requestMIDIAccess !== "function") {
    const ua = navigator.userAgent.toLowerCase();
    const isIOS = /ipad|iphone|ipod/.test(ua) || (/macintosh/.test(ua) && "ontouchend" in document);
    const isSafari = /^((?!chrome|android).)*safari/i.test(ua);
    if (isIOS) return { kind: "unsupported", reason: "ios" };
    if (isSafari) return { kind: "unsupported", reason: "safari" };
    return { kind: "unsupported", reason: "browser" };
  }
  try {
    const access = await nav.requestMIDIAccess({ sysex: true });
    return { kind: "ok", access };
  } catch (err) {
    return { kind: "unsupported", reason: `denied: ${err instanceof Error ? err.message : String(err)}` };
  }
}

export function pairDevices(access: MIDIAccess): MIDIDeviceInfo[] {
  const inputs = Array.from(access.inputs.values());
  const outputs = Array.from(access.outputs.values());
  const devices: MIDIDeviceInfo[] = [];

  for (const input of inputs) {
    const match = outputs.find(
      (o) => o.name === input.name && o.manufacturer === input.manufacturer
    );
    if (match) {
      devices.push({
        id: `${input.id}|${match.id}`,
        name: input.name ?? "Unknown",
        manufacturer: input.manufacturer ?? "",
        input,
        output: match,
      });
    }
  }
  return devices;
}

export function sendCC(output: MIDIOutput, channel: number, cc: number, value: number) {
  const ch = Math.max(0, Math.min(15, channel - 1));
  const v = Math.max(0, Math.min(127, Math.round(value)));
  output.send([0xb0 | ch, cc & 0x7f, v]);
}

export function sendNoteOn(output: MIDIOutput, channel: number, note: number, velocity: number) {
  const ch = Math.max(0, Math.min(15, channel - 1));
  output.send([0x90 | ch, note & 0x7f, Math.max(0, Math.min(127, velocity))]);
}

export function sendNoteOff(output: MIDIOutput, channel: number, note: number) {
  const ch = Math.max(0, Math.min(15, channel - 1));
  output.send([0x80 | ch, note & 0x7f, 0]);
}
