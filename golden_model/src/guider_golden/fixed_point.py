"""Fixed-point model of the phase-correlation pipeline (validation stage 2).

This is the bit-accurate *spec* for the FPGA datapath. It reproduces the float
golden model (`phase_correlation.estimate_shift`) on identical inputs to within
quantization tolerance, and is in turn the reference the HDL FFT is cosim'd
against (M4).

Datapath model
--------------
- 12-bit input samples (Hann window folded in as a fixed-point coefficient).
- 2-D FFT / IFFT as separable radix-2 DIT passes in **block floating point**:
  every component (re, im) is a signed `mant_bits` integer sharing one block
  exponent for the whole 2-D array; after each butterfly stage the block is
  rescaled (round + saturate) to keep the largest magnitude inside the mantissa.
  This mirrors the Xilinx FFT IP in BFP mode.
- Twiddle factors quantized to `twiddle_bits` (signed, `twiddle_bits-1` frac).
- Cross-power conj(F)*G in fixed point, then phase-only normalization modelled
  as a CORDIC: exact angle of the quantized bin -> quantized to `cordic_bits`
  -> unit vector quantized to `unit_bits` fractional. Phase-only discards
  magnitude, so the BFP exponent collapses here.
- Peak: integer argmax (exact, no FPGA cost).
- Subpixel: 3-point parabolic in float -- runs on the PS, zero FPGA impact, so
  it reuses the float helper unchanged.

`estimate_shift` keeps the float model's signature, plus an optional `cfg`.
"""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

from .phase_correlation import hann2d, _parabolic_offset


@dataclass(frozen=True)
class FixedConfig:
    """Bit widths of the fixed-point datapath (FPGA dimensioning knobs)."""
    input_bits: int = 12      # signed sample width after ADC/conditioning
    window_bits: int = 12     # fractional bits of the Hann coefficients
    mant_bits: int = 18       # FFT mantissa per component (DSP48: 18)
    twiddle_bits: int = 16    # signed twiddle width (frac = twiddle_bits-1)
    cordic_bits: int = 16     # phase-angle resolution of the normalizer
    unit_bits: int = 15       # fractional bits of the unit-vector cos/sin
    rounding: str = "convergent"  # "convergent" (round-half-even) | "truncate"


def _round_shift(x: np.ndarray, sh: int, mode: str) -> np.ndarray:
    """Arithmetic right shift of int64 `x` by `sh`, with rounding.

    `convergent` = round half to even (Xilinx convergent rounding);
    `truncate`   = round toward zero.
    """
    if sh <= 0:
        return x << (-sh)
    if mode == "truncate":
        return np.where(x >= 0, x >> sh, -((-x) >> sh))
    q = x >> sh                      # arithmetic floor shift (numpy, signed)
    r = x - (q << sh)                # remainder in [0, 2**sh)
    half = 1 << (sh - 1)
    up = (r > half) | ((r == half) & ((q & 1) == 1))
    return q + up.astype(np.int64)


def _bfp_rescale(re: np.ndarray, im: np.ndarray, cfg: FixedConfig):
    """Shift the block so max(|re|,|im|) fits in `mant_bits`; return added exp."""
    limit = (1 << (cfg.mant_bits - 1)) - 1
    mx = int(max(np.abs(re).max(initial=0), np.abs(im).max(initial=0)))
    if mx <= limit:
        return re, im, 0
    sh = mx.bit_length() - (cfg.mant_bits - 1)
    re = _round_shift(re, sh, cfg.rounding)
    im = _round_shift(im, sh, cfg.rounding)
    # rounding can overflow by 1 LSB at the top of the range -> saturate
    np.clip(re, -limit - 1, limit, out=re)
    np.clip(im, -limit - 1, limit, out=im)
    return re, im, sh


def _bit_reverse(n: int) -> np.ndarray:
    bits = n.bit_length() - 1
    idx = np.arange(n)
    rev = np.zeros(n, dtype=np.intp)
    for b in range(bits):
        rev |= ((idx >> b) & 1) << (bits - 1 - b)
    return rev


def _twiddles(half: int, m: int, inverse: bool, cfg: FixedConfig):
    sign = 1.0 if inverse else -1.0
    k = np.arange(half)
    ang = sign * 2.0 * np.pi * k / m
    scale = 1 << (cfg.twiddle_bits - 1)
    tw_re = np.round(np.cos(ang) * scale).astype(np.int64)
    tw_im = np.round(np.sin(ang) * scale).astype(np.int64)
    return tw_re, tw_im


def _fft1d_batch(re: np.ndarray, im: np.ndarray, cfg: FixedConfig,
                 inverse: bool):
    """Batched radix-2 DIT FFT along axis 1, BFP-rescaled after each stage.

    Rescaling over the whole batch == global block float for the 2-D pass.
    Returns (re, im, total_exp_shift).
    """
    batch, n = re.shape
    rev = _bit_reverse(n)
    re = re[:, rev].copy()
    im = im[:, rev].copy()
    tb = cfg.twiddle_bits - 1
    total = 0
    m = 2
    while m <= n:
        half = m >> 1
        tw_re, tw_im = _twiddles(half, m, inverse, cfg)
        re3 = re.reshape(batch, n // m, m)
        im3 = im.reshape(batch, n // m, m)
        a_re, a_im = re3[:, :, :half], im3[:, :, :half]
        b_re, b_im = re3[:, :, half:], im3[:, :, half:]
        t_re = _round_shift(b_re * tw_re - b_im * tw_im, tb, cfg.rounding)
        t_im = _round_shift(b_re * tw_im + b_im * tw_re, tb, cfg.rounding)
        re = np.concatenate([a_re + t_re, a_re - t_re], axis=2).reshape(batch, n)
        im = np.concatenate([a_im + t_im, a_im - t_im], axis=2).reshape(batch, n)
        re, im, sh = _bfp_rescale(re, im, cfg)
        total += sh
        m <<= 1
    return re, im, total


def _fft2d(re: np.ndarray, im: np.ndarray, cfg: FixedConfig, inverse: bool):
    exp = 0
    re, im, d = _fft1d_batch(re, im, cfg, inverse); exp += d      # rows
    re, im = re.T.copy(), im.T.copy()
    re, im, d = _fft1d_batch(re, im, cfg, inverse); exp += d      # cols
    re, im = re.T.copy(), im.T.copy()
    return re, im, exp


def _quantize_input(x: np.ndarray, scale: float, w_int: np.ndarray | None,
                    cfg: FixedConfig) -> np.ndarray:
    """Float field -> signed `input_bits` samples, optional Hann fold-in."""
    limit = (1 << (cfg.input_bits - 1)) - 1
    xi = np.clip(np.round(x * scale), -limit - 1, limit).astype(np.int64)
    if w_int is not None:
        xi = _round_shift(xi * w_int, cfg.window_bits, cfg.rounding)
    return xi


def estimate_shift(ref: np.ndarray, img: np.ndarray, *,
                   window: bool = True, phase_only: bool = True,
                   subpixel: bool = True, cfg: FixedConfig = FixedConfig()):
    """Fixed-point estimate of (dy, dx, peak_value, correlation_surface).

    Same convention as the float golden model: (dy, dx) is the displacement of
    `img` relative to `ref`. `cfg` selects the datapath bit widths.
    """
    ref = np.asarray(ref, dtype=np.float64)
    img = np.asarray(img, dtype=np.float64)
    ny, nx = ref.shape
    if (ny & (ny - 1)) or (nx & (nx - 1)):
        raise ValueError("fixed-point FFT requires power-of-two dimensions")

    # shared input scale: map the larger field max to full-scale (one ADC range)
    peak_in = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / peak_in
    w_int = None
    if window:
        w_int = np.round(hann2d(ref.shape) * (1 << cfg.window_bits)).astype(np.int64)

    ref_re = _quantize_input(ref, scale, w_int, cfg)
    img_re = _quantize_input(img, scale, w_int, cfg)
    zeros = np.zeros_like(ref_re)

    F_re, F_im, _ = _fft2d(ref_re, zeros, cfg, inverse=False)
    G_re, G_im, _ = _fft2d(img_re, zeros.copy(), cfg, inverse=False)

    # cross-power R = conj(F) * G
    R_re = F_re * G_re + F_im * G_im
    R_im = F_re * G_im - F_im * G_re
    R_re, R_im, _ = _bfp_rescale(R_re, R_im, cfg)

    if phase_only:
        ang = np.arctan2(R_im.astype(np.float64), R_re.astype(np.float64))
        step = 2.0 * np.pi / (1 << cfg.cordic_bits)
        ang_q = np.round(ang / step) * step          # CORDIC angle quantization
        S = 1 << cfg.unit_bits
        zero_bin = (R_re == 0) & (R_im == 0)
        R_re = np.round(np.cos(ang_q) * S).astype(np.int64)
        R_im = np.round(np.sin(ang_q) * S).astype(np.int64)
        R_re[zero_bin] = 0
        R_im[zero_bin] = 0

    corr_re, _corr_im, _ = _fft2d(R_re, R_im, cfg, inverse=True)
    corr = corr_re.astype(np.float64)             # exponent irrelevant: peak is
                                                  # argmax + a scale-free ratio

    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak_val = float(corr[py, px])

    sub_dy = sub_dx = 0.0
    if subpixel:
        sub_dy = _parabolic_offset(corr[(py - 1) % ny, px], corr[py, px],
                                   corr[(py + 1) % ny, px])
        sub_dx = _parabolic_offset(corr[py, (px - 1) % nx], corr[py, px],
                                   corr[py, (px + 1) % nx])

    dy = (((py + ny // 2) % ny) - ny // 2) + sub_dy
    dx = (((px + nx // 2) % nx) - nx // 2) + sub_dx
    return dy, dx, peak_val, corr
