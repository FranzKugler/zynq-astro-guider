"""Fourier-Mellin style rotation estimation.

Idea: |FFT| is translation-invariant, so rotation/scale survive in the
magnitude spectrum. Warping the magnitude to polar coordinates turns a
rotation into a shift along the angle axis, which we then recover with the
same phase-correlation machinery.

Note: the magnitude spectrum of a real image is point-symmetric, so rotation
is only determined modulo 180 deg. On sparse star fields the estimate is
inherently noisy -- this is a quantification tool, not a precision corrector.
"""
from __future__ import annotations
import numpy as np
from skimage.transform import warp_polar
from skimage.registration import phase_cross_correlation
from .phase_correlation import hann2d


def _log_magnitude(img: np.ndarray) -> np.ndarray:
    w = hann2d(img.shape)                       # reduce spectral leakage
    F = np.fft.fftshift(np.fft.fft2(img * w))
    return np.log1p(np.abs(F))


def estimate_rotation(ref: np.ndarray, img: np.ndarray, upsample: int = 20):
    """Estimate rotation (degrees) of `img` relative to `ref`.

    Returns (angle_deg, phase_corr_error). angle folded into (-90, 90].
    """
    radius = min(ref.shape) // 2
    wp_ref = warp_polar(_log_magnitude(ref), radius=radius, output_shape=(360, radius))
    wp_img = warp_polar(_log_magnitude(img), radius=radius, output_shape=(360, radius))
    shift, error, _ = phase_cross_correlation(wp_ref, wp_img, upsample_factor=upsample)
    angle = shift[0]                            # 360 rows over 360 deg -> 1 row = 1 deg
    angle = ((angle + 90.0) % 180.0) - 90.0
    return float(angle), float(error)
