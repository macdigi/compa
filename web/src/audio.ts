export type AudioSupport =
  | { kind: "ok"; ctx: AudioContext; analyser: AnalyserNode; stream: MediaStream }
  | { kind: "denied"; reason: string };

export async function requestAudioInput(deviceId?: string): Promise<AudioSupport> {
  try {
    const constraints: MediaStreamConstraints = {
      audio: {
        deviceId: deviceId ? { exact: deviceId } : undefined,
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    };
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    const ctx = new AudioContext({ latencyHint: "interactive" });
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.0;
    source.connect(analyser);
    return { kind: "ok", ctx, analyser, stream };
  } catch (err) {
    return { kind: "denied", reason: err instanceof Error ? err.message : String(err) };
  }
}

export async function listAudioInputs(): Promise<MediaDeviceInfo[]> {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter((d) => d.kind === "audioinput");
  } catch {
    return [];
  }
}
