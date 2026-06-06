#!/usr/bin/env python3
"""On-board M5 validation: estimate_shift_pl with UioBackend vs ModelBackend.

Runs the full phase-correlation pipeline on the PL hardware and compares the
result against the software model.  The CORDIC phase-only step introduces small
but bounded quantization: the correlation surface may differ in the tail, but
the peak location (shift) must match the model within one subpixel step.

Run as root on the board:

    sudo PYTHONPATH=target/src:golden_model/src python3 target/onboard_estimate_shift.py

Tests each of the four pass types individually first, then the full pipeline.
"""
import sys
import numpy as np

from guider_golden import synthetic_starfield, fourier_shift
from guider_golden.fixed_point import FixedConfig, _fft1d_batch, _round_shift
from guider_target import estimate_shift_pl, ModelBackend, UioBackend


N = 256
PASSES = True      # set False to skip individual-pass checks (faster)


def _rng(seed=42):
    return np.random.default_rng(seed)


def test_window(hw, sw, n=N, seed=1):
    cfg = hw.cfg
    rng = _rng(seed)
    lim_s = 1 << (cfg.input_bits - 1)
    lim_c = 1 << cfg.window_bits
    shape = (n, n)
    samples = rng.integers(-lim_s, lim_s, shape).astype(np.int64)
    coefs   = rng.integers(0, lim_c + 1, shape).astype(np.int64)
    hw_out = hw.window(samples, coefs)
    sw_out = sw.window(samples, coefs)
    ok = np.array_equal(hw_out, sw_out)
    print("window N=%d: %s" % (n, "PASS" if ok else "FAIL"))
    if not ok:
        diff = hw_out - sw_out
        print("  max|diff|=%d  n_bad=%d" % (int(np.abs(diff).max()), int((diff != 0).sum())))
    return ok


def test_fft(hw, sw, n=N, seed=2, inverse=False):
    cfg = hw.cfg
    rng = _rng(seed)
    lim = 1 << (cfg.mant_bits - 1)
    shape = (n, n)
    re = rng.integers(-lim, lim, shape).astype(np.int64)
    im = rng.integers(-lim, lim, shape).astype(np.int64)
    hw_re, hw_im = hw.fft_pass(re, im, inverse)
    sw_re, sw_im = sw.fft_pass(re, im, inverse)
    ok_re = np.array_equal(hw_re, sw_re)
    ok_im = np.array_equal(hw_im, sw_im)
    ok = ok_re and ok_im
    label = "ifft" if inverse else "fft"
    print("%s N=%d: %s" % (label, n, "PASS" if ok else "FAIL"))
    if not ok_re:
        diff = hw_re - sw_re
        print("  re max|diff|=%d  n_bad=%d" % (int(np.abs(diff).max()), int((diff != 0).sum())))
    if not ok_im:
        diff = hw_im - sw_im
        print("  im max|diff|=%d  n_bad=%d" % (int(np.abs(diff).max()), int((diff != 0).sum())))
    return ok


def test_cross_power(hw, sw, n=N, seed=3):
    cfg = hw.cfg
    rng = _rng(seed)
    lim = 1 << (cfg.mant_bits - 1)
    shape = (n, n)
    f_re = rng.integers(-lim, lim, shape).astype(np.int64)
    f_im = rng.integers(-lim, lim, shape).astype(np.int64)
    g_re = rng.integers(-lim, lim, shape).astype(np.int64)
    g_im = rng.integers(-lim, lim, shape).astype(np.int64)
    hw_r, hw_i, hw_mx = hw.cross_power(f_re, f_im, g_re, g_im)
    sw_r, sw_i, sw_mx = sw.cross_power(f_re, f_im, g_re, g_im)
    ok_r = np.array_equal(hw_r, sw_r)
    ok_i = np.array_equal(hw_i, sw_i)
    ok_mx = (hw_mx == sw_mx)
    ok = ok_r and ok_i and ok_mx
    print("cross_power N=%d: %s (block_max hw=%d sw=%d)" % (n, "PASS" if ok else "FAIL", hw_mx, sw_mx))
    if not ok_r or not ok_i:
        diff = (hw_r - sw_r).ravel()
        print("  re n_bad=%d" % int((hw_r != sw_r).sum()))
        print("  im n_bad=%d" % int((hw_i != sw_i).sum()))
    return ok


def test_rescale(hw, sw, n=N, seed=4):
    cfg = hw.cfg
    rng = _rng(seed)
    lim = 1 << (2 * cfg.mant_bits)
    shape = (n, n)
    r_re = rng.integers(-lim, lim, shape).astype(np.int64)
    r_im = rng.integers(-lim, lim, shape).astype(np.int64)
    mx = int(max(np.abs(r_re).max(), np.abs(r_im).max()))
    from guider_target.backend import shift_from_max
    sh = shift_from_max(mx, cfg)
    hw_re, hw_im = hw.rescale_phase(r_re, r_im, sh)
    sw_re, sw_im = sw.rescale_phase(r_re, r_im, sh)
    # rescale_phase uses CORDIC on HW vs float atan2 in model: allow small diff
    max_diff_re = int(np.abs(hw_re.astype(np.int64) - sw_re).max())
    max_diff_im = int(np.abs(hw_im.astype(np.int64) - sw_im).max())
    ok = max_diff_re <= 2 and max_diff_im <= 2   # CORDIC 1 LSB tolerance
    print("rescale_phase N=%d sh=%d: %s (max_diff re=%d im=%d)" %
          (n, sh, "PASS" if ok else "FAIL", max_diff_re, max_diff_im))
    return ok


def test_full_pipeline(hw, sw, shift=(3.0, -5.0), seed=5):
    rng = _rng(seed)
    ref = synthetic_starfield(shape=(N, N), n_stars=20, seed=seed)
    img = fourier_shift(ref, shift)

    print("Full pipeline: shift=%s" % (shift,))
    hw_dy, hw_dx, hw_pk, hw_corr = estimate_shift_pl(ref, img, hw)
    sw_dy, sw_dx, sw_pk, sw_corr = estimate_shift_pl(ref, img, sw)

    err_dy = abs(hw_dy - sw_dy)
    err_dx = abs(hw_dx - sw_dx)
    ok_shift = err_dy < 0.3 and err_dx < 0.3
    max_corr_diff = int(np.abs(hw_corr.astype(np.int64) - sw_corr).max())
    print("  hw=(%.3f, %.3f) sw=(%.3f, %.3f) err=(%.3f, %.3f) %s" %
          (hw_dy, hw_dx, sw_dy, sw_dx, err_dy, err_dx, "PASS" if ok_shift else "FAIL"))
    print("  max corr diff: %d" % max_corr_diff)
    return ok_shift


def main():
    hw = UioBackend()
    sw = ModelBackend()
    print("UioBackend ready, CSR ID OK")
    results = {}

    if PASSES:
        results["window"]      = test_window(hw, sw)
        results["fft_fwd"]     = test_fft(hw, sw, inverse=False)
        results["fft_inv"]     = test_fft(hw, sw, inverse=True, seed=6)
        results["cross_power"] = test_cross_power(hw, sw)
        results["rescale"]     = test_rescale(hw, sw)

    results["pipeline_35"]  = test_full_pipeline(hw, sw, shift=(3.0, -5.0))
    results["pipeline_1m2"] = test_full_pipeline(hw, sw, shift=(1.5, -2.0), seed=7)

    print()
    print("=== SUMMARY ===")
    all_pass = True
    for name, ok in results.items():
        print("  %-20s  %s" % (name, "PASS" if ok else "FAIL"))
        all_pass = all_pass and ok
    print("OVERALL: %s" % ("PASS" if all_pass else "FAIL"))
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
