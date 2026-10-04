/**
 *
 * @module pcm-resampler
 * Responsibilities: Convert streaming audio samples into signed PCM16 at the recognition service's target rate.
 * Implementation: Preserve fractional sample area across worklet blocks, average each target sample interval, then clip and quantize.
 * Related Modules: speech-worklet.js uses the resampler during microphone capture; digital-human-check.js uses it for generated test audio.
 *
 * Declaration Index:
 * - PCM16Resampler:
 *   Streaming area-average resampling: preserve phase across AudioWorklet blocks.
 * - PCM16Resampler.constructor:
 *   Set the sample-rate ratio and retain partial output-sample accumulation.
 * - PCM16Resampler.process:
 *   Downsample across block boundaries and quantize clipped samples to signed PCM16.
 *
 * Variable Index:
 * None
 *
 */
/** Streaming area-average resampling: preserve phase across AudioWorklet blocks. */
export class PCM16Resampler {
  /** Set the sample-rate ratio and retain partial output-sample accumulation. */ constructor(inputRate, outputRate = 16000) {
    if (inputRate < outputRate) throw new Error("Input sample rate must be at least 16 kHz.");
    this.ratio = inputRate / outputRate;
    this.weight = 0;
    this.sum = 0;
  }

  /** Downsample across block boundaries and quantize clipped samples to signed PCM16. */ process(samples) {
    const output = [];
    for (const sample of samples) {
      let remaining = 1;
      while (remaining > 1e-9) {
        const width = Math.min(remaining, this.ratio - this.weight);
        this.sum += sample * width;
        this.weight += width;
        remaining -= width;
        if (this.weight >= this.ratio - 1e-9) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          output.push(Math.round(value < 0 ? value * 32768 : value * 32767));
          this.weight = this.sum = 0;
        }
      }
    }
    return output;
  }
}
