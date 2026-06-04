"""PS-side orchestration of the PL phase-correlation pass schedule.

`estimate_shift_pl` mirrors guider_golden.fixed_point.estimate_shift but routes
every compute pass through a `PLBackend` (the PL kernels via DMA), keeping the
PS-only work -- input scaling/quantization, the block-max -> BFP-shift handoff,
and the peak argmax + parabolic subpixel -- on the host. With the ModelBackend it
reproduces the fixed-point model bit-exact, which is the verification that the
*sequencing* (DDR buffers, transposed reads, sh handoff) is correct; swapping in
the on-board backend then runs the same schedule on real hardware.

Same convention as the golden model: (dy, dx) is the displacement of `img`
relative to `ref`.
"""
from __future__ import annotations

import numpy as np

from guider_golden.fixed_point import FixedConfig, _quantize_input, hann2d
from guider_golden.phase_correlation import _parabolic_offset

from .backend import PLBackend, shift_from_max


def _fft2(backend: PLBackend, re: np.ndarray, im: np.ndarray, inverse: bool):
    """2-D transform as two 1-D passes with a transpose (a strided DMA read) between.

    Matches guider_golden.fixed_point._fft2d: rows -> .T -> cols -> .T. Each
    `.T.copy()` models the corner-turn being realised by a column-major DMA read
    into a fresh DDR buffer (no on-chip transpose at whole-field sizes).
    """
    re, im = backend.fft_pass(re, im, inverse)          # row pass
    re, im = re.T.copy(), im.T.copy()                   # transposed DMA read
    re, im = backend.fft_pass(re, im, inverse)          # column pass
    return re.T.copy(), im.T.copy()


def estimate_shift_pl(ref, img, backend: PLBackend, *,
                      window: bool = True, subpixel: bool = True):
    """Fixed-point (dy, dx, peak_value, correlation_surface) via the PL datapath."""
    cfg: FixedConfig = backend.cfg
    ref = np.asarray(ref, dtype=np.float64)
    img = np.asarray(img, dtype=np.float64)
    ny, nx = ref.shape
    if (ny & (ny - 1)) or (nx & (nx - 1)):
        raise ValueError("phase-correlation FFT requires power-of-two dimensions")

    # --- PS: one ADC range maps the larger field max to full scale ---
    peak_in = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / peak_in
    ref_q = _quantize_input(ref, scale, None, cfg)      # signed input_bits, no window
    img_q = _quantize_input(img, scale, None, cfg)

    # --- PL: window pass (Hann coefficients streamed alongside the samples) ---
    if window:
        w_int = np.round(hann2d(ref.shape) * (1 << cfg.window_bits)).astype(np.int64)
        ref_q = backend.window(ref_q, w_int)
        img_q = backend.window(img_q, w_int)

    # --- PL: forward FFT2 of each frame (real input, imag tied to zero) ---
    z = np.zeros_like(ref_q)
    F_re, F_im = _fft2(backend, ref_q, z, inverse=False)
    G_re, G_im = _fft2(backend, img_q, z.copy(), inverse=False)

    # --- PL pass 1 + PS shift + PL pass 2: cross-power, BFP, phase-only ---
    R_re, R_im, block_max = backend.cross_power(F_re, F_im, G_re, G_im)
    sh = shift_from_max(block_max, cfg)
    P_re, P_im = backend.rescale_phase(R_re, R_im, sh)

    # --- PL: inverse FFT2 -> correlation surface (real part) ---
    corr_re, _corr_im = _fft2(backend, P_re, P_im, inverse=True)
    corr = corr_re.astype(np.float64)

    # --- PS: peak (argmax) + parabolic subpixel (zero FPGA cost) ---
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
