import numpy as np
import pytest
from guider_golden import (synthetic_starfield, fourier_shift, rotate_field,
                           estimate_shift, estimate_rotation)


def test_small_rotation_keeps_translation_usable():
    ref = synthetic_starfield(seed=3)
    img = fourier_shift(rotate_field(ref, 0.1), (2.0, -1.0))
    dy, dx, _peak, _ = estimate_shift(ref, img, window=True)
    # at 0.1 deg the translation should still be recovered well below 1 px
    assert np.hypot(dy - 2.0, dx + 1.0) < 0.5


@pytest.mark.parametrize("ang", [3.0, -5.0, 8.0])
def test_fourier_mellin_recovers_rotation(ang):
    ref = synthetic_starfield(seed=4)
    # add a translation too: rotation estimate must be translation-invariant
    img = fourier_shift(rotate_field(ref, ang), (4.0, 7.0))
    est, _err = estimate_rotation(ref, img)
    assert abs(est - ang) < 1.0
