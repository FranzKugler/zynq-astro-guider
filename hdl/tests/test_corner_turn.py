"""Cosim the corner-turn: streamed output == transpose of each input frame."""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.corner_turn import CornerTurn


def _stream(n, frames):
    """Stream `frames` (each a length-n*n list) row-major; collect valid outputs.
    A trailing dummy frame flushes the last real frame's transpose."""
    dut = CornerTurn(n=n, width=32)
    samples = [v for f in frames for v in f] + [0] * (n * n)   # + flush frame
    out = []

    async def tb(ctx):
        for v in samples:
            ctx.set(dut.i_valid, 1)
            ctx.set(dut.i_data, int(v))
            await ctx.tick()
            if ctx.get(dut.o_valid):
                out.append(ctx.get(dut.o_data))
        ctx.set(dut.i_valid, 0)
        for _ in range(2):                       # drain read latency
            await ctx.tick()
            if ctx.get(dut.o_valid):
                out.append(ctx.get(dut.o_data))

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return out


def test_transpose_bit_exact():
    n = 8
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 1 << 32, n * n) for _ in range(3)]
    out = _stream(n, frames)

    expected = np.concatenate([f.reshape(n, n).T.ravel() for f in frames])
    assert np.array_equal(np.array(out, np.uint64), expected.astype(np.uint64))


def test_small_n4():
    n = 4
    frames = [np.arange(n * n), np.arange(n * n) + 100]
    out = _stream(n, frames)
    expected = np.concatenate([f.reshape(n, n).T.ravel() for f in frames])
    assert np.array_equal(out, expected)


def test_continuous_throughput():
    """Output count == (num real frames) * n*n: every frame transposed, no gaps."""
    n = 8
    frames = [np.full(n * n, k + 1) for k in range(4)]
    out = _stream(n, frames)
    assert len(out) == len(frames) * n * n
