"""Phase-only cross-correlation for whole-field guiding-error estimation.

Float reference implementation = the GOLDEN MODEL. The later fixed-point
model and the FPGA FFT are validated bit-for-bit against this on identical
inputs.

Convention
----------
estimate_shift(ref, img) -> (dy, dx, peak, corr) where (dy, dx) is the
displacement of `img` relative to `ref`, i.e.
    img(y, x) ~= ref(y - dy, x - dx)
so feeding a frame shifted by (+dy, +dx) returns (+dy, +dx).
"""
from __future__ import annotations
import numpy as np

EPS = 1e-12


def hann2d(shape: tuple[int, int]) -> np.ndarray:
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1]))


def cross_power_spectrum(ref: np.ndarray, img: np.ndarray,
                         phase_only: bool = True) -> np.ndarray:
    """conj(FFT(ref)) * FFT(img), optionally phase-only normalized.

    conj(F)*G puts the correlation peak at +shift (see module docstring).
    """
    F = np.fft.fft2(ref)
    G = np.fft.fft2(img)
    R = np.conj(F) * G
    if phase_only:
        R = R / (np.abs(R) + EPS)
    return R


def _parabolic_offset(c_m1: float, c_0: float, c_p1: float) -> float:
    """3-point parabolic peak interpolation; offset in [-0.5, 0.5]."""
    denom = c_m1 - 2.0 * c_0 + c_p1
    if abs(denom) < EPS:
        return 0.0
    return 0.5 * (c_m1 - c_p1) / denom


def estimate_shift(ref: np.ndarray, img: np.ndarray, *,
                   window: bool = True, phase_only: bool = True,
                   subpixel: bool = True):
    """Estimate (dy, dx, peak_value, correlation_surface).

    window     : apply a 2-D Hann window first (needed for non-periodic
                 frames; disable for already-periodic synthetic data).
    phase_only : phase-only correlation (sharper, brightness-robust peak).
    subpixel   : 3-point parabolic refinement of the integer peak.
    """
    ref = np.asarray(ref, dtype=np.float64)
    img = np.asarray(img, dtype=np.float64)
    if window:
        w = hann2d(ref.shape)
        ref = ref * w
        img = img * w

    corr = np.fft.ifft2(cross_power_spectrum(ref, img, phase_only)).real

    ny, nx = corr.shape
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
