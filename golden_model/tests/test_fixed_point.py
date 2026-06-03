import numpy as np
import pytest

from guider_golden import synthetic_starfield, fourier_shift
from guider_golden import estimate_shift as estimate_shift_float
from guider_golden.fixed_point import estimate_shift as estimate_shift_fixed
from guider_golden.fixed_point import FixedConfig

SHAPE = (64, 64)


def _field(seed=1):
    return synthetic_starfield(shape=SHAPE, n_stars=25, seed=seed)


@pytest.mark.parametrize("shift", [
    (0.0, 0.0), (3.0, -5.0), (7.0, 4.0),   # integer
    (1.5, 2.25), (-4.3, 0.7),              # subpixel
])
def test_recovers_known_shift(shift):
    ref = _field(seed=1)
    img = fourier_shift(ref, shift)
    dy, dx, _peak, _corr = estimate_shift_fixed(ref, img, window=False)
    assert abs(dy - shift[0]) < 0.3
    assert abs(dx - shift[1]) < 0.3


@pytest.mark.parametrize("shift", [(3.0, -5.0), (1.5, 2.25), (-4.3, 0.7)])
def test_agrees_with_float_model(shift):
    """Stage 2 reproduces stage 1 on identical inputs (within quantization)."""
    ref = _field(seed=3)
    img = fourier_shift(ref, shift)
    fdy, fdx, *_ = estimate_shift_float(ref, img, window=False)
    qdy, qdx, *_ = estimate_shift_fixed(ref, img, window=False)
    assert abs(qdy - fdy) < 0.25
    assert abs(qdx - fdx) < 0.25


def test_peak_significant():
    ref = _field(seed=2)
    img = fourier_shift(ref, (2.0, 3.0))
    *_, peak, corr = estimate_shift_fixed(ref, img, window=False)
    assert peak > 5.0 * np.median(np.abs(corr))


def test_bit_width_degradation():
    """More mantissa bits -> not worse; coarse config stays bounded."""
    ref = _field(seed=5)
    shift = (6.0, -3.0)
    img = fourier_shift(ref, shift)

    def err(mant_bits):
        cfg = FixedConfig(mant_bits=mant_bits)
        dy, dx, *_ = estimate_shift_fixed(ref, img, window=False, cfg=cfg)
        return abs(dy - shift[0]) + abs(dx - shift[1])

    fine = err(20)
    coarse = err(12)
    assert fine < 0.3
    assert coarse < 1.5            # degrades but does not blow up
    assert fine <= coarse + 0.2    # more bits never meaningfully worse


def test_requires_power_of_two():
    ref = synthetic_starfield(shape=(48, 64))
    with pytest.raises(ValueError):
        estimate_shift_fixed(ref, ref)
