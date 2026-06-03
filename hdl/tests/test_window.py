"""Cosim WindowMul bit-exact against the fixed-point golden model."""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.window import WindowMul
from guider_golden.fixed_point import _round_shift


def test_window_bit_exact():
    sample_bits, window_bits = 12, 12
    dut = WindowMul(sample_bits=sample_bits, coef_bits=window_bits + 1,
                    shift=window_bits)

    rng = np.random.default_rng(0)
    n = 4000
    samples = rng.integers(-(1 << (sample_bits - 1)), 1 << (sample_bits - 1), n)
    coefs = rng.integers(0, (1 << window_bits) + 1, n)   # hann*2^12 range
    refs = _round_shift(samples.astype(np.int64) * coefs.astype(np.int64),
                        window_bits, "convergent")

    bad = []

    async def tb(ctx):
        for s, c, ref in zip(samples, coefs, refs):
            ctx.set(dut.sample, int(s))
            ctx.set(dut.coef, int(c))
            got = ctx.get(dut.result)
            if got != int(ref):
                bad.append((int(s), int(c), got, int(ref)))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    assert not bad, f"{len(bad)} mismatches, first: {bad[:5]}"
