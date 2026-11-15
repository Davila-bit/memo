// Audio exists only in browser memory, then in the POST body sent to our API.
export interface VoiceRecording {
  finish(): string;
  cancel(): void;
}

export async function startVoiceRecording(): Promise<VoiceRecording> {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
    throw new Error("This browser can't record a question. You can still type it.");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 }, video: false });
  let context: AudioContext | undefined;
  try {
    context = new AudioContext();
    await context.resume();
    const sampleRate = context.sampleRate;
    const source = context.createMediaStreamSource(stream);
    // A short, in-memory capture. A silent output keeps processing active without microphone echo.
    const processor = context.createScriptProcessor(4096, 1, 1);
    const mute = context.createGain();
    mute.gain.value = 0;
    const chunks: Float32Array[] = [];
    let samples = 0;
    let stopped = false;
    processor.onaudioprocess = (event) => {
      if (stopped || samples >= sampleRate * 8) return;
      const chunk = event.inputBuffer.getChannelData(0).slice(0, sampleRate * 8 - samples);
      chunks.push(chunk);
      samples += chunk.length;
    };
    source.connect(processor);
    processor.connect(mute);
    mute.connect(context.destination);

    function release() {
      stopped = true;
      processor.onaudioprocess = null;
      source.disconnect();
      processor.disconnect();
      mute.disconnect();
      stream.getTracks().forEach((track) => track.stop());
      void context?.close().catch(() => {});
    }
    return {
      cancel() { release(); chunks.length = 0; },
      finish() {
        release();
        const pcm = new Float32Array(samples);
        let offset = 0;
        for (const chunk of chunks) { pcm.set(chunk, offset); offset += chunk.length; }
        chunks.length = 0;
        return wavBase64(pcm, sampleRate);
      },
    };
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    void context?.close().catch(() => {});
    throw error;
  }
}

function wavBase64(input: Float32Array, sampleRate: number): string {
  const ratio = sampleRate / 16000;
  const length = Math.floor(input.length / ratio);
  const bytes = new Uint8Array(44 + length * 2);
  const view = new DataView(bytes.buffer);
  const text = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) bytes[offset + i] = value.charCodeAt(i);
  };
  text(0, "RIFF"); view.setUint32(4, bytes.length - 8, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.max(start + 1, Math.floor((i + 1) * ratio));
    let total = 0;
    for (let j = start; j < end && j < input.length; j++) total += input[j];
    const sample = Math.max(-1, Math.min(1, total / (end - start)));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}
