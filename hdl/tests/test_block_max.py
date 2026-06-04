"""Cosim the streaming block-max reducer (BFP first pass)."""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.cross_power import BlockMax
from guider_golden import synthetic_starfield, fourier_shift
from guider_golden.fixed_point import (
    FixedConfig, _quantize_input, _fft2d, _bfp_rescale,
)

IN_BITS = 2 * 18 + 1


def _run(blocks):
    """Stream `blocks` (each a list of (re, im)) through one DUT; return o_max
    sampled after each block's last element."""
    dut = BlockMax(in_bits=IN_BITS)
    out = []

    async def tb(ctx):
        for blk in blocks:
            for k, (a, b) in enumerate(blk):
                ctx.set(dut.i_valid, 1)
                ctx.set(dut.i_first, 1 if k == 0 else 0)
                ctx.set(dut.i_re, int(a))
                ctx.set(dut.i_im, int(b))
                await ctx.tick()
            out.append(ctx.get(dut.o_max))
        ctx.set(dut.i_valid, 0)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return out


def _np_max(blk):
    a = np.array(blk, np.int64)
    return int(np.maximum(np.abs(a[:, 0]), np.abs(a[:, 1])).max())


def test_block_max_bit_exact():
    rng = np.random.default_rng(0)
    lim = 1 << (IN_BITS - 1)
    blocks = [list(zip(rng.integers(-lim, lim, n), rng.integers(-lim, lim, n)))
              for n in (1, 7, 64, 256, 100)]
    got = _run(blocks)
    for o, blk in zip(got, blocks):
        assert o == _np_max(blk)


def test_resets_between_blocks():
    """i_first must drop the previous block's max."""
    big = [(1 << 30, -(1 << 31)), (5, 5)]        # max ~2**31
    small = [(3, -4), (-7, 2), (0, 1)]           # max 7
    got = _run([big, small])
    assert got[0] == _np_max(big)
    assert got[1] == _np_max(small) == 7


def test_block_max_on_real_cross_power():
    cfg = FixedConfig()
    ref = synthetic_starfield(shape=(16, 16), n_stars=8, seed=1)
    img = fourier_shift(ref, (2.0, -1.0))
    pk = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / pk
    rr = _quantize_input(ref, scale, None, cfg)
    ii = _quantize_input(img, scale, None, cfg)
    z = np.zeros_like(rr)
    F_re, F_im, _ = _fft2d(rr, z, cfg, inverse=False)
    G_re, G_im, _ = _fft2d(ii, z.copy(), cfg, inverse=False)
    raw_re = (F_re * G_re + F_im * G_im).ravel()
    raw_im = (F_re * G_im - F_im * G_re).ravel()

    got = _run([list(zip(raw_re, raw_im))])[0]
    assert got == int(max(np.abs(raw_re).max(), np.abs(raw_im).max()))
