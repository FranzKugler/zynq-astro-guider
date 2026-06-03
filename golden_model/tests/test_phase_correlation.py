import numpy as np
import pytest
from guider_golden import synthetic_starfield, fourier_shift, estimate_shift


@pytest.mark.parametrize("shift", [
    (0.0, 0.0), (3.0, -5.0), (10.0, -12.0),   # integer
    (1.5, 2.25), (-4.3, 0.7),                 # subpixel
])
def test_recovers_known_shift(shift):
    ref = synthetic_starfield(seed=1)
    img = fourier_shift(ref, shift)
    dy, dx, _peak, _corr = estimate_shift(ref, img, window=False)
    assert abs(dy - shift[0]) < 0.2
    assert abs(dx - shift[1]) < 0.2


def test_peak_significant():
    ref = synthetic_starfield(seed=2)
    img = fourier_shift(ref, (2.0, 3.0))
    *_, peak, corr = estimate_shift(ref, img, window=False)
    assert peak > 5.0 * np.median(np.abs(corr))
