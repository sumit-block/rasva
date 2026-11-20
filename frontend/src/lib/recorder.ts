/**
 * Records the microphone straight to 16-bit mono WAV in the browser.
 *
 * WAV (not MediaRecorder's WebM/MP4) keeps the reference lossless and lets the
 * Mac analyse it without FFmpeg. Browser voice processing is turned off so the
 * recording is the voice as it is. Needs a secure page: this Mac (localhost)
 * or the paired HTTPS address.
 */
export interface Recording {
  stop: () => Promise<Blob>;
  cancel: () => void;
}

const MAX_SECONDS = 60;

export async function startRecording(
  onLevel: (rms: number, seconds: number) => void,
  onLimit: () => void,
): Promise<Recording> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Recording needs a secure connection. Open Rasva on this Mac, or pair this device.");
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
  });
  const ctx = new AudioContext();
  const source = ctx.createMediaStreamSource(stream);
  // ScriptProcessorNode is deprecated but works everywhere (incl. Safari) without
  // shipping a separate worklet file; a minute of audio is well within its limits.
  const node = ctx.createScriptProcessor(4096, 1, 1);
  const chunks: Float32Array[] = [];
  let samples = 0;
  let stopped = false;
  let limitSent = false;

  node.onaudioprocess = (e) => {
    if (stopped) return;
    const data = new Float32Array(e.inputBuffer.getChannelData(0));
    chunks.push(data);
    samples += data.length;
    let sum = 0;
    for (const v of data) sum += v * v;
    onLevel(Math.sqrt(sum / data.length), samples / ctx.sampleRate);
    if (samples / ctx.sampleRate >= MAX_SECONDS && !limitSent) {
      limitSent = true;
      onLimit();
    }
  };
  source.connect(node);
  node.connect(ctx.destination);

  const teardown = () => {
    stopped = true;
    node.disconnect();
    source.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    void ctx.close();
  };

  return {
    stop: async () => {
      teardown();
      return encodeWav(chunks, samples, ctx.sampleRate);
    },
    cancel: teardown,
  };
}

function encodeWav(chunks: Float32Array[], length: number, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + length * 2);
  const view = new DataView(buffer);
  const write = (offset: number, text: string) => [...text].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks) {
    for (const v of chunk) {
      const s = Math.max(-1, Math.min(1, v));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}
