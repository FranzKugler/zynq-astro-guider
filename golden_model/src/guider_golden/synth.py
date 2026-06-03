"""Synthetic star fields and exact Fourier shifts for testing the pipeline."""
from __future__ import annotations
import numpy as np


def synthetic_starfield(shape: tuple[int, int] = (256, 256), n_stars: int = 40,
                        fwhm: float = 2.5, background: float = 10.0,
                        noise: float = 1.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ny, nx = shape
    sigma = fwhm / 2.3548
    ys, xs = np.mgrid[0:ny, 0:nx].astype(np.float64)
    img = np.full(shape, background, dtype=np.float64)
    for _ in range(n_stars):
        cy, cx = rng.uniform(0, ny), rng.uniform(0, nx)
        amp = rng.uniform(50.0, 1000.0)
        img += amp * np.exp(-(((ys - cy) ** 2 + (xs - cx) ** 2) / (2 * sigma ** 2)))
    if noise > 0:
        img += rng.normal(0.0, noise, shape)
    return img


def fourier_shift(img: np.ndarray, shift: tuple[float, float]) -> np.ndarray:
    """Shift `img` by (+sy, +sx) with subpixel accuracy (periodic / wrap)."""
    sy, sx = shift
    ny, nx = img.shape
    ky = np.fft.fftfreq(ny)[:, None]
    kx = np.fft.fftfreq(nx)[None, :]
    F = np.fft.fft2(img) * np.exp(-2j * np.pi * (ky * sy + kx * sx))
    return np.fft.ifft2(F).real
