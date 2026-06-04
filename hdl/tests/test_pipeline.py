"""End-to-end: the HW-block pipeline reproduces the model and recovers shifts.

Bit-exact to the fixed-point model everywhere except phase-only (CORDIC vs float
atan2); the test confirms that divergence does not move the shift estimate.
"""
import numpy as np
import pytest

from guider_hdl.cosim import estimate_shift_hw
from guider_golden import synthetic_starfield, fourier_shift
from guider_golden import estimate_shift_fixed
from guider_golden.fixed_point import FixedConfig

SHAPE = (32, 32)


def _field(seed=1):
    return synthetic_starfield(shape=SHAPE, n_stars=14, seed=seed)


@pytest.mark.parametrize("shift", [(3.0, -5.0), (1.5, -2.0)])
def test_recovers_shift_and_tracks_model(shift):
    ref = _field(seed=1)
    img = fourier_shift(ref, shift)
    cfg = FixedConfig()

    hdy, hdx, *_ = estimate_shift_hw(ref, img, window=False, cfg=cfg)
    mdy, mdx, *_ = estimate_shift_fixed(ref, img, window=False, cfg=cfg)

    # recovers ground truth
    assert abs(hdy - shift[0]) < 0.3
    assert abs(hdx - shift[1]) < 0.3
    # tracks the model (only phase-only CORDIC differs)
    assert abs(hdy - mdy) < 0.15
    assert abs(hdx - mdx) < 0.15


def test_windowed_path_exercises_windowmul():
    """window=True routes both frames through WindowMul; still recovers shift."""
    ref = _field(seed=4)
    shift = (2.0, 3.0)
    img = fourier_shift(ref, shift)
    cfg = FixedConfig()

    hdy, hdx, peak, corr = estimate_shift_hw(ref, img, window=True, cfg=cfg)
    mdy, mdx, *_ = estimate_shift_fixed(ref, img, window=True, cfg=cfg)

    assert abs(hdy - shift[0]) < 0.4
    assert abs(hdx - shift[1]) < 0.4
    assert abs(hdy - mdy) < 0.2
    assert abs(hdx - mdx) < 0.2
    assert peak > 5.0 * np.median(np.abs(corr))
