"""Cosim WindowMul bit-exact against the fixed-point golden model."""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.window import WindowMul, WindowStream
from guider_golden import synthetic_starfield
from guider_golden.fixed_point import (
    FixedConfig, _round_shift, _quantize_input, hann2d,
)


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


def test_window_stream_matches_model_quantize():
    """WindowStream over a real frame == the model's windowed _quantize_input."""
    cfg = FixedConfig()
    frame = synthetic_starfield(shape=(16, 16), n_stars=8, seed=3)
    pk = max(float(np.abs(frame).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / pk

    samples = _quantize_input(frame, scale, None, cfg).ravel()        # no window
    coefs = np.round(hann2d(frame.shape) * (1 << cfg.window_bits)).astype(np.int64).ravel()
    want = _quantize_input(frame, scale,
                           np.round(hann2d(frame.shape) * (1 << cfg.window_bits))
                           .astype(np.int64), cfg).ravel()

    dut = WindowStream(sample_bits=cfg.input_bits, window_bits=cfg.window_bits)
    n = len(samples)
    out = []

    async def tb(ctx):
        ctx.set(dut.out.ready, 1)
        for i in range(n):
            ctx.set(dut.sample.valid, 1)
            ctx.set(dut.coef.valid, 1)
            ctx.set(dut.sample.payload, int(samples[i]))
            ctx.set(dut.coef.payload, int(coefs[i]))
            ctx.set(dut.sample.first, 1 if i == 0 else 0)
            ctx.set(dut.sample.last, 1 if i == n - 1 else 0)
            out.append(ctx.get(dut.out.payload))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    assert np.array_equal(np.array(out, np.int64), want)
