#!/usr/bin/env python3
"""M5 final on-board validation against the datapath-reset bitstream.

With CTRL.dpath_reset flushing the AXIS switches per frame, the stale-beat prefix
is gone and the FFT frames deterministically, so the plain UioBackend should run
the whole pipeline correctly -- no alignment harness needed.

Run as root on the board:
    sudo PYTHONPATH=target/src:golden_model/src python3 target/onboard_validate.py
"""
import sys
import numpy as np

from guider_golden import synthetic_starfield, fourier_shift
from guider_target import estimate_shift_pl, ModelBackend, UioBackend

N = 256


def check_bitexact_kernels(hw, sw):
    """window + cross_power are deterministic integer kernels -> must be bit-exact."""
    rng = np.random.default_rng(7)
    cfg = hw.cfg
    ok = True
    # window
    lim_s = 1 << (cfg.input_bits - 1); lim_c = 1 << cfg.window_bits
    s = rng.integers(-lim_s, lim_s, (N, N)).astype(np.int64)
    c = rng.integers(0, lim_c + 1, (N, N)).astype(np.int64)
    be = np.array_equal(hw.window(s, c), sw.window(s, c))
    print("  window bit-exact     :", be); ok &= be
    # cross_power
    lim = 1 << (cfg.mant_bits - 1)
    fr, fi, gr, gi = (rng.integers(-lim, lim, (N, N)).astype(np.int64) for _ in range(4))
    hr, hi, hb = hw.cross_power(fr, fi, gr, gi)
    sr, si, sb = sw.cross_power(fr, fi, gr, gi)
    be = np.array_equal(hr, sr) and np.array_equal(hi, si) and hb == sb
    print("  cross_power bit-exact:", be, "(block_max hw=%d sw=%d)" % (hb, sb)); ok &= be
    return ok


def main():
    hw = UioBackend()
    sw = ModelBackend(hw.cfg)
    print("UioBackend ready, CSR ID OK")

    print("Deterministic-kernel bit-exactness:")
    kok = check_bitexact_kernels(hw, sw)

    print("Full pipeline (UioBackend vs ModelBackend):")
    cases = [(3.0, -5.0), (1.5, -2.0), (0.0, 7.0), (-4.5, 2.5)]
    pok = True
    for dy, dx in cases:
        ref = synthetic_starfield((N, N), n_stars=60, seed=11)
        img = fourier_shift(ref, (dy, dx))
        hdy, hdx, hpk, _ = estimate_shift_pl(ref, img, hw)
        sdy, sdx, spk, _ = estimate_shift_pl(ref, img, sw)
        ey, ex = abs(hdy - sdy), abs(hdx - sdx)
        good = ey < 0.05 and ex < 0.05
        pok &= good
        print("  shift=(%5.1f,%5.1f): hw=(%7.3f,%7.3f) sw=(%7.3f,%7.3f) err=(%.4f,%.4f) %s"
              % (dy, dx, hdy, hdx, sdy, sdx, ey, ex, "PASS" if good else "FAIL"))

    print("\nKernels:", "PASS" if kok else "FAIL", " Pipeline:", "PASS" if pok else "FAIL")
    print("OVERALL:", "PASS" if (kok and pok) else "FAIL")
    return 0 if (kok and pok) else 1


if __name__ == "__main__":
    sys.exit(main())
