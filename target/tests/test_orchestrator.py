"""The PS orchestrator reproduces the golden fixed-point model.

Routing every pass through ModelBackend, estimate_shift_pl must equal
guider_golden.fixed_point.estimate_shift bit-exact -- that certifies the
sequencing (DDR buffers, transposed reads, block-max -> BFP-shift handoff, peak)
is correct. It must also recover known shifts.
"""
import numpy as np
import pytest

from guider_target import estimate_shift_pl, ModelBackend, shift_from_max
from guider_golden import (
    synthetic_starfield, fourier_shift, estimate_shift_fixed,
)
from guider_golden.fixed_point import FixedConfig, _bfp_rescale


@pytest.mark.parametrize("shift", [(3.0, -5.0), (1.5, -2.0)])
@pytest.mark.parametrize("window", [False, True])
def test_orchestrator_matches_model_bit_exact(shift, window):
    ref = synthetic_starfield(shape=(32, 32), n_stars=14, seed=1)
    img = fourier_shift(ref, shift)
    cfg = FixedConfig()

    pdy, pdx, ppk, pcorr = estimate_shift_pl(ref, img, ModelBackend(cfg),
                                             window=window)
    mdy, mdx, mpk, mcorr = estimate_shift_fixed(ref, img, window=window, cfg=cfg)

    assert np.array_equal(pcorr, mcorr)          # identical correlation surface
    assert pdy == mdy and pdx == mdx and ppk == mpk
    # recovers ground truth
    assert abs(pdy - shift[0]) < 0.3
    assert abs(pdx - shift[1]) < 0.3


def test_no_subpixel_is_integer_shift():
    ref = synthetic_starfield(shape=(16, 16), n_stars=8, seed=2)
    img = fourier_shift(ref, (2.0, -3.0))
    pdy, pdx, *_ = estimate_shift_pl(ref, img, ModelBackend(), subpixel=False)
    assert pdy == round(pdy) and pdx == round(pdx)
    mdy, mdx, *_ = estimate_shift_fixed(ref, img, subpixel=False)
    assert pdy == mdy and pdx == mdx


def test_shift_from_max_matches_bfp_rescale():
    """The PS shift handoff equals the model's internal BFP rescale shift."""
    cfg = FixedConfig()
    rng = np.random.default_rng(3)
    for _ in range(50):
        scale = rng.integers(0, 1 << (2 * cfg.mant_bits))
        re = rng.integers(-scale - 1, scale + 1, 64).astype(np.int64)
        im = rng.integers(-scale - 1, scale + 1, 64).astype(np.int64)
        mx = int(max(np.abs(re).max(initial=0), np.abs(im).max(initial=0)))
        _, _, sh = _bfp_rescale(re.copy(), im.copy(), cfg)
        assert shift_from_max(mx, cfg) == sh
