"""Cosim the phase-only CORDIC.

Two levels:
  1. Amaranth PhaseOnly == cordic_ref.phase_only_cordic   (BIT-EXACT)
  2. cordic_ref.phase_only_cordic ~= float golden model    (TOLERANCE, few LSB)
"""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.phase_only import PhaseOnly
from guider_hdl.cordic_ref import CordicParams, phase_only_cordic
from guider_golden import synthetic_starfield, fourier_shift
from guider_golden.fixed_point import (
    FixedConfig, _quantize_input, _fft2d, _bfp_rescale,
)

P = CordicParams()
TOL = 8                       # max abs LSB error, fixed-point CORDIC vs model


def _model_phase_only(re, im, cfg):
    ang = np.arctan2(im.astype(float), re.astype(float))
    step = 2 * np.pi / (1 << cfg.cordic_bits)
    aq = np.round(ang / step) * step
    s = 1 << cfg.unit_bits
    o_re = np.round(np.cos(aq) * s).astype(np.int64)
    o_im = np.round(np.sin(aq) * s).astype(np.int64)
    zb = (re == 0) & (im == 0)
    o_re[zb] = 0
    o_im[zb] = 0
    return o_re, o_im


def _hw(re, im):
    dut = PhaseOnly(P)
    out_re, out_im = [], []

    async def tb(ctx):
        for a, b in zip(re, im):
            ctx.set(dut.re_in, int(a))
            ctx.set(dut.im_in, int(b))
            out_re.append(ctx.get(dut.re_out))
            out_im.append(ctx.get(dut.im_out))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    return np.array(out_re, np.int64), np.array(out_im, np.int64)


def test_hw_matches_cordic_ref_bit_exact():
    lim = 1 << (P.mant_bits - 1)
    rng = np.random.default_rng(0)
    re = np.concatenate([rng.integers(-lim, lim, 1500),
                         rng.integers(-8, 8, 300),          # tiny vectors
                         np.array([0, 1, -1, lim - 1, -lim])])
    im = np.concatenate([rng.integers(-lim, lim, 1500),
                         rng.integers(-8, 8, 300),
                         np.array([0, 0, 0, 0, lim - 1])])
    hw_re, hw_im = _hw(re, im)
    ref_re, ref_im = phase_only_cordic(re, im, P)
    assert np.array_equal(hw_re, ref_re)
    assert np.array_equal(hw_im, ref_im)


def test_cordic_ref_matches_model_within_tol():
    rng = np.random.default_rng(1)
    lim = 1 << (P.mant_bits - 1)
    re = rng.integers(-lim, lim, 20000)
    im = rng.integers(-lim, lim, 20000)
    cfg = FixedConfig(cordic_bits=16, unit_bits=P.unit_bits)
    cre, cim = phase_only_cordic(re, im, P)
    mre, mim = _model_phase_only(re, im, cfg)
    assert np.abs(cre - mre).max() <= TOL
    assert np.abs(cim - mim).max() <= TOL


def test_zero_bin():
    hw_re, hw_im = _hw([0], [0])
    assert hw_re[0] == 0 and hw_im[0] == 0


def test_on_real_cross_power():
    """Feed real BFP-rescaled cross-power; HW bit-exact to ref, ref ~= model."""
    cfg = FixedConfig(unit_bits=P.unit_bits)
    ref = synthetic_starfield(shape=(16, 16), n_stars=8, seed=2)
    img = fourier_shift(ref, (1.0, 2.0))
    pk = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / pk
    rr = _quantize_input(ref, scale, None, cfg)
    ii = _quantize_input(img, scale, None, cfg)
    z = np.zeros_like(rr)
    F_re, F_im, _ = _fft2d(rr, z, cfg, inverse=False)
    G_re, G_im, _ = _fft2d(ii, z.copy(), cfg, inverse=False)
    R_re = F_re * G_re + F_im * G_im
    R_im = F_re * G_im - F_im * G_re
    R_re, R_im, _ = _bfp_rescale(R_re, R_im, cfg)

    hw_re, hw_im = _hw(R_re.ravel(), R_im.ravel())
    ref_re, ref_im = phase_only_cordic(R_re.ravel(), R_im.ravel(), P)
    assert np.array_equal(hw_re, ref_re) and np.array_equal(hw_im, ref_im)

    mre, mim = _model_phase_only(R_re.ravel(), R_im.ravel(), cfg)
    assert np.abs(hw_re - mre).max() <= TOL
    assert np.abs(hw_im - mim).max() <= TOL
