#!/usr/bin/env python3
"""On-board smoke test: run cross-power passes for all given resolutions through
the PL via DMA and check against the golden model. Validates the full DMA ->
AXIS switch -> kernel -> switch -> DMA datapath on hardware.

Run as root on the board (needs /dev/mem):

    sudo PYTHONPATH=target/src:golden_model/src python3 target/onboard_crosspower.py [N ...]

Default: N = 64 128 256 (all resolutions in one session).

Cross-power is exact in HW (no rounding), so R must be BIT-EXACT to conj(F)*G.
"""
import sys
import numpy as np

from guider_golden.fixed_point import FixedConfig
from guider_target import UioBackend


def run_one(be, n):
    cfg = be.cfg
    rng = np.random.default_rng(0)
    lim = 1 << (cfg.mant_bits - 1)
    shape = (n, n)
    f_re = rng.integers(-lim, lim, shape).astype(np.int64)
    f_im = rng.integers(-lim, lim, shape).astype(np.int64)
    g_re = rng.integers(-lim, lim, shape).astype(np.int64)
    g_im = rng.integers(-lim, lim, shape).astype(np.int64)

    print("N=%d: running cross-power pass (%dx%d = %d beats)..." % (n, n, n, n * n))
    r_re, r_im, block_max = be.cross_power(f_re, f_im, g_re, g_im)

    exp_re = f_re * g_re + f_im * g_im
    exp_im = f_re * g_im - f_im * g_re
    exp_max = int(max(np.abs(exp_re).max(), np.abs(exp_im).max()))

    ok_re = np.array_equal(r_re, exp_re)
    ok_im = np.array_equal(r_im, exp_im)
    ok_max = (block_max == exp_max)
    print("  R_re bit-exact: %s" % ok_re)
    print("  R_im bit-exact: %s" % ok_im)
    print("  block_max: hw=%d model=%d match=%s" % (block_max, exp_max, ok_max))
    if not (ok_re and ok_im):
        bad = np.argwhere(r_re != exp_re)[:3]
        for (y, x) in bad:
            print("    mismatch [%d,%d]: hw=%d model=%d" % (y, x, r_re[y, x], exp_re[y, x]))
    result = "PASS" if (ok_re and ok_im and ok_max) else "FAIL"
    print("  RESULT: %s" % result)
    return result == "PASS"


def main():
    sizes = [int(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else [64, 128, 256]

    # cross_power uses only buf[0..2]; 3 bufs avoids the CMA limit for udmabuf7.
    be = UioBackend(bufs=["udmabuf0", "udmabuf1", "udmabuf2"])
    print("CSR ID OK")

    results = {}
    for n in sizes:
        results[n] = run_one(be, n)

    print()
    print("=== SUMMARY ===")
    all_pass = True
    for n, ok in results.items():
        print("  N=%-4d  %s" % (n, "PASS" if ok else "FAIL"))
        all_pass = all_pass and ok
    print("OVERALL: %s" % ("PASS" if all_pass else "FAIL"))


if __name__ == "__main__":
    main()
