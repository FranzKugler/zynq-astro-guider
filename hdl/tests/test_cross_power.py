"""Cosim the cross-power stage bit-exact against the fixed-point golden model."""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.cross_power import CrossMul, ShiftFromMax, BfpRescale
from guider_golden import synthetic_starfield, fourier_shift
from guider_golden.fixed_point import (
    FixedConfig, _quantize_input, _fft2d, _bfp_rescale, _round_shift,
)

MANT = 18
IN_BITS = 2 * MANT + 1
LIMIT = (1 << (MANT - 1)) - 1            # 131071


def _drive(dut, inputs, outs):
    """Run `dut` over a list of input dicts; return list of output dicts."""
    got = []

    async def tb(ctx):
        for vec in inputs:
            for name, val in vec.items():
                ctx.set(getattr(dut, name), int(val))
            got.append({o: ctx.get(getattr(dut, o)) for o in outs})

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    return got


def _model_sh(mx):
    return 0 if mx <= LIMIT else mx.bit_length() - (MANT - 1)


def _model_rescale_elem(v, sh):
    r = int(_round_shift(np.array([v], np.int64), sh, "convergent")[0])
    return int(np.clip(r, -LIMIT - 1, LIMIT))


def test_cross_mul_bit_exact():
    dut = CrossMul(mant_bits=MANT)
    rng = np.random.default_rng(0)
    n = 3000
    lo, hi = -(1 << (MANT - 1)), 1 << (MANT - 1)
    f_re, f_im = rng.integers(lo, hi, n), rng.integers(lo, hi, n)
    g_re, g_im = rng.integers(lo, hi, n), rng.integers(lo, hi, n)
    out = _drive(dut, [dict(f_re=a, f_im=b, g_re=c, g_im=d)
                       for a, b, c, d in zip(f_re, f_im, g_re, g_im)],
                 ("r_re", "r_im"))
    ref_re = f_re.astype(np.int64) * g_re + f_im.astype(np.int64) * g_im
    ref_im = f_re.astype(np.int64) * g_im - f_im.astype(np.int64) * g_re
    for o, rr, ri in zip(out, ref_re, ref_im):
        assert o["r_re"] == rr and o["r_im"] == ri


def test_shift_from_max_bit_exact():
    dut = ShiftFromMax(mant_bits=MANT, in_bits=IN_BITS)
    rng = np.random.default_rng(1)
    mags = np.concatenate([
        rng.integers(0, 1 << IN_BITS, 2000),
        np.array([0, 1, LIMIT, LIMIT + 1, (1 << IN_BITS) - 1]),  # edges
    ])
    out = _drive(dut, [dict(mag=int(mx)) for mx in mags], ("sh",))
    for o, mx in zip(out, mags):
        assert o["sh"] == _model_sh(int(mx))


def test_bfp_rescale_bit_exact():
    dut = BfpRescale(mant_bits=MANT, in_bits=IN_BITS)
    rng = np.random.default_rng(2)
    n = 3000
    vals = rng.integers(-(1 << (IN_BITS - 1)), 1 << (IN_BITS - 1), n)
    shs = rng.integers(0, dut.max_sh + 1, n)
    out = _drive(dut, [dict(value=int(v), sh=int(s))
                       for v, s in zip(vals, shs)], ("result",))
    for o, v, s in zip(out, vals, shs):
        assert o["result"] == _model_rescale_elem(int(v), int(s))


def test_cross_power_end_to_end():
    """mul + shift-from-max + rescale reproduce the model on real FFT data."""
    cfg = FixedConfig(mant_bits=MANT)
    ref = synthetic_starfield(shape=(16, 16), n_stars=8, seed=1)
    img = fourier_shift(ref, (2.0, -1.0))
    peak_in = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / peak_in
    ref_re = _quantize_input(ref, scale, None, cfg)
    img_re = _quantize_input(img, scale, None, cfg)
    z = np.zeros_like(ref_re)
    F_re, F_im, _ = _fft2d(ref_re, z, cfg, inverse=False)
    G_re, G_im, _ = _fft2d(img_re, z.copy(), cfg, inverse=False)

    raw_re = F_re * G_re + F_im * G_im
    raw_im = F_re * G_im - F_im * G_re
    exp_re, exp_im, _ = _bfp_rescale(raw_re.copy(), raw_im.copy(), cfg)

    fr, fi = F_re.ravel(), F_im.ravel()
    gr, gi = G_re.ravel(), G_im.ravel()

    # 1) complex multiply in HW == raw products
    mul = _drive(CrossMul(mant_bits=MANT),
                 [dict(f_re=a, f_im=b, g_re=c, g_im=d)
                  for a, b, c, d in zip(fr, fi, gr, gi)], ("r_re", "r_im"))
    hw_raw_re = np.array([o["r_re"] for o in mul], np.int64)
    hw_raw_im = np.array([o["r_im"] for o in mul], np.int64)
    assert np.array_equal(hw_raw_re, raw_re.ravel())
    assert np.array_equal(hw_raw_im, raw_im.ravel())

    # 2) block max (streaming reducer, taken in Python) -> shift in HW
    mx = int(max(np.abs(hw_raw_re).max(), np.abs(hw_raw_im).max()))
    sh = _drive(ShiftFromMax(mant_bits=MANT, in_bits=IN_BITS),
                [dict(mag=mx)], ("sh",))[0]["sh"]

    # 3) per-lane rescale in HW == model's BFP-rescaled cross-power
    resc = BfpRescale(mant_bits=MANT, in_bits=IN_BITS)
    hw_re = _drive(resc, [dict(value=int(v), sh=sh) for v in hw_raw_re],
                   ("result",))
    hw_im = _drive(resc, [dict(value=int(v), sh=sh) for v in hw_raw_im],
                   ("result",))
    assert np.array_equal([o["result"] for o in hw_re], exp_re.ravel())
    assert np.array_equal([o["result"] for o in hw_im], exp_im.ravel())
