# Fixed-point datapath — bit-width choices

Model: `guider_golden.fixed_point` (validation stage 2). Knobs live in
`FixedConfig`; this note records why the defaults are what they are and what the
FFT-IP dimensioning (M4) should inherit.

## Defaults
| field          | default | what it sets                                   |
|----------------|---------|------------------------------------------------|
| `input_bits`   | 12      | signed sample width (ADC / conditioning)       |
| `window_bits`  | 12      | fractional bits of the Hann coefficients       |
| `mant_bits`    | 18      | FFT mantissa per component (BFP)               |
| `twiddle_bits` | 16      | signed twiddle (frac = `twiddle_bits-1`)       |
| `cordic_bits`  | 16      | phase-angle resolution of the normalizer       |
| `unit_bits`    | 15      | fractional bits of the unit-vector cos/sin     |

FFT/IFFT run in **block floating point**: each re/im component is a signed
`mant_bits` integer sharing one block exponent for the whole 2-D array, rescaled
(round + saturate) after every radix-2 stage. This mirrors the Xilinx FFT IP in
BFP mode, so `mant_bits` maps to the IP output width and `twiddle_bits` to its
phase-factor width.

## Evidence (sweep)
Error = |Δdy| + |Δdx| of the fixed-point estimate vs. the float golden model,
mean / max over 5 shifts × 4 seeds, 64×64 fields, phase-only, `window=False`.

```
mant_bits   10    12    14    16    18    20    22
  mean    .064  .057  .048  .038  .028  .020  .011
  max     .224  .187  .161  .146  .105  .070  .048

twiddle_bits 8    10    12    14    16    18      -> flat: mean ~.028 throughout
cordic/unit  converged by ~8 bits; <0.006 px change from 3-bit to 16-bit angle
```

## Reading
- **`mant_bits` is the only knob that moves accuracy** (and peak SNR): roughly
  halves the error per ~6 bits. 18 (DSP48-native) gives ~0.03 px mean / ~0.1 px
  max vs. float — comfortably inside the 0.2 px budget, with headroom. 12 still
  clears 0.2 px; below that the peak softens. Keep 18.
- **`twiddle_bits` is flat from 8 up** at N=64 — phase-correlation cares about
  the coherent sum, not per-twiddle precision. 16 is generous and free (native);
  do **not** trim on this data — twiddle error grows with transform length, so
  re-sweep at the real frame size before shrinking.
- **`cordic_bits` / `unit_bits` converge by ~8 bits** — phase-only is robust to
  angle quantization. 16/15 is deliberate headroom and fits a signed 16-bit
  word. `unit_bits` only scales the IFFT input magnitude (peak value), not the
  argmax/subpixel.

## Caveats for M4
- Numbers are from 64×64 synthetic fields. Real guide frames are larger; the
  large-DC-bin pressure on BFP and the twiddle-error growth both scale with N —
  re-run the sweep at the target FFT size before freezing the IP config.
- This model is the *spec*; HDL cosim against it uses its own tolerance (the
  Xilinx IP's internal schedule is not reproduced bit-for-bit here).
