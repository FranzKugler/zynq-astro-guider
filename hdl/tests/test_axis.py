"""AXIS-native adaptation: FirstGen regenerates FIRST from LAST, and through the
top it makes CrossPower's per-frame block-max reset correctly on TLAST-only input.
"""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.stream import FirstGen
from guider_hdl.csr import PhaseCorrelatorTop
from guider_hdl.fft_ip import FftStub

MANT = 18
XPMAX_LO, XPMAX_HI = 0x08, 0x0C


def test_firstgen_regenerates_first_from_last():
    dut = FirstGen(8)
    lasts = [0, 0, 1, 0, 1, 1]          # frames: [3], [2], [1]
    ready = [1, 1, 1, 0, 1, 1, 1]       # stall once mid-stream (beat 3 held)
    got_first, got_payload = [], []

    async def tb(ctx):
        k = 0
        for c in range(len(ready)):
            ctx.set(dut.int.ready, ready[c])
            if k < len(lasts):
                ctx.set(dut.ext.valid, 1)
                ctx.set(dut.ext.last, lasts[k])
                ctx.set(dut.ext.payload, k)
            fired = ctx.get(dut.ext.valid) and ctx.get(dut.ext.ready)
            if fired:
                got_first.append(ctx.get(dut.int.first))
                got_payload.append(ctx.get(dut.int.payload))
            await ctx.tick()
            if fired:
                k += 1

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    # FIRST: beat0 (reset), then the beat after each LAST
    assert got_first == [1, 0, 0, 1, 0, 1]
    assert got_payload == [0, 1, 2, 3, 4, 5]      # pass-through, no loss under stall


async def _axil_read(ctx, ax, addr):
    ctx.set(ax.araddr, addr); ctx.set(ax.arvalid, 1); ctx.set(ax.rready, 1)
    while not ctx.get(ax.arready):
        await ctx.tick()
    await ctx.tick(); ctx.set(ax.arvalid, 0)
    while not ctx.get(ax.rvalid):
        await ctx.tick()
    d = ctx.get(ax.rdata)
    await ctx.tick(); ctx.set(ax.rready, 0)
    return d


def _frame(seed, n):
    rng = np.random.default_rng(seed)
    lo, hi = -(1 << (MANT - 1)), 1 << (MANT - 1)
    return (rng.integers(lo, hi, n), rng.integers(lo, hi, n),
            rng.integers(lo, hi, n), rng.integers(lo, hi, n))


def _model_max(fr, fi, gr, gi):
    raw_re = fr.astype(np.int64) * gr + fi.astype(np.int64) * gi
    raw_im = fr.astype(np.int64) * gi - fi.astype(np.int64) * gr
    return int(max(np.abs(raw_re).max(), np.abs(raw_im).max()))


def test_block_max_resets_per_frame_through_top():
    """Two TLAST-only frames: each xpower_max is that frame's own max (FIRST reset)."""
    n = 16
    fa = _frame(1, n)
    fb = _frame(2, n)
    dut = PhaseCorrelatorTop(n=8, mant_bits=MANT, core=FftStub(n=8, input_width=MANT))
    ax = dut.s_axil
    res = {}

    async def stream(ctx, fr, fi, gr, gi):
        for k in range(n):
            ctx.set(dut.xpower_f.valid, 1); ctx.set(dut.xpower_g.valid, 1)
            ctx.set(dut.xpower_f.payload.re, int(fr[k]))
            ctx.set(dut.xpower_f.payload.im, int(fi[k]))
            ctx.set(dut.xpower_g.payload.re, int(gr[k]))
            ctx.set(dut.xpower_g.payload.im, int(gi[k]))
            last = 1 if k == n - 1 else 0
            ctx.set(dut.xpower_f.last, last); ctx.set(dut.xpower_g.last, last)
            await ctx.tick()
        ctx.set(dut.xpower_f.valid, 0); ctx.set(dut.xpower_g.valid, 0)
        ctx.set(dut.xpower_f.last, 0); ctx.set(dut.xpower_g.last, 0)
        await ctx.tick(); await ctx.tick()        # let o_max_valid latch into the CSR

    async def read_max(ctx):
        lo = await _axil_read(ctx, ax, XPMAX_LO)
        hi = await _axil_read(ctx, ax, XPMAX_HI)
        return lo | (hi << 32)

    async def tb(ctx):
        ctx.set(dut.xpower_r.ready, 1)
        await stream(ctx, *fa)
        res["a"] = await read_max(ctx)
        await stream(ctx, *fb)
        res["b"] = await read_max(ctx)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    assert res["a"] == _model_max(*fa)
    assert res["b"] == _model_max(*fb)            # not carried over from frame A
