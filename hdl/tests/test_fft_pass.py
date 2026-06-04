"""Cosim the FftPass framing/handshake logic with the behavioral FFT stub.

The transform values are verified against the real IP in Vivado xsim
(sim/fft_cosim.py); here we check the synthesizable glue around the IP: config
loads before data, the input TLAST lands on every N-point row boundary, the
output is reframed as an N x N frame (FIRST/LAST), the per-row block exponents
are summed, and backpressure neither drops nor duplicates a beat. The stub is an
identity skid, so output payload == input payload in order.
"""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.fft_pass import FftPass
from guider_hdl.fft_ip import FftStub

N = 8
MANT = 18
BLK_EXP = 2


def _run(re, im, *, ready_pattern=None):
    """Feed one N*N frame through FftPass(core=FftStub); collect output + probes."""
    dut = FftPass(n=N, mant_bits=MANT,
                  core=FftStub(n=N, input_width=MANT, blk_exp=BLK_EXP))
    n = len(re)
    out = []
    s_tlast_at_accept = []

    async def tb(ctx):
        ctx.set(dut.inverse, 0)
        i = 0
        c = 0
        while len(out) < n and c < 4000:
            if i < n:
                ctx.set(dut.inp.valid, 1)
                ctx.set(dut.inp.payload.re, int(re[i]))
                ctx.set(dut.inp.payload.im, int(im[i]))
                ctx.set(dut.inp.first, 1 if i == 0 else 0)
                ctx.set(dut.inp.last, 1 if i == n - 1 else 0)
            else:
                ctx.set(dut.inp.valid, 0)
            rdy = 1 if ready_pattern is None else ready_pattern(c)
            ctx.set(dut.out.ready, rdy)

            in_fire = ctx.get(dut.inp.valid) and ctx.get(dut.inp.ready)
            if in_fire:
                s_tlast_at_accept.append(ctx.get(dut.core.s_tlast))
            out_fire = ctx.get(dut.out.valid) and ctx.get(dut.out.ready)
            if out_fire:
                out.append((ctx.get(dut.out.payload.re),
                            ctx.get(dut.out.payload.im),
                            ctx.get(dut.out.first), ctx.get(dut.out.last),
                            ctx.get(dut.o_blk_exp_sum)))
            await ctx.tick()
            if in_fire:
                i += 1
            c += 1

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return out, s_tlast_at_accept


def _frame(seed=0):
    rng = np.random.default_rng(seed)
    lim = 1 << (MANT - 1)
    return (rng.integers(-lim, lim, N * N).astype(np.int64),
            rng.integers(-lim, lim, N * N).astype(np.int64))


def test_passthrough_and_framing():
    re, im = _frame(0)
    out, s_tlast = _run(re, im)

    assert len(out) == N * N
    o_re = np.array([o[0] for o in out], np.int64)
    o_im = np.array([o[1] for o in out], np.int64)
    assert np.array_equal(o_re, re) and np.array_equal(o_im, im)  # identity stub

    first = [o[2] for o in out]
    last = [o[3] for o in out]
    assert first[0] == 1 and sum(first) == 1
    assert last[-1] == 1 and sum(last) == 1


def test_input_tlast_on_row_boundaries():
    re, im = _frame(1)
    _, s_tlast = _run(re, im)
    assert len(s_tlast) == N * N
    want = [1 if (k % N) == N - 1 else 0 for k in range(N * N)]
    assert s_tlast == want


def test_block_exponent_sum():
    re, im = _frame(2)
    out, _ = _run(re, im)
    # N rows, each contributing BLK_EXP from the stub's TUSER
    assert out[-1][4] == N * BLK_EXP


def test_backpressure_no_loss():
    re, im = _frame(3)
    out, s_tlast = _run(re, im, ready_pattern=lambda c: (c // 2) % 2)  # stutter
    assert len(out) == N * N
    o_re = np.array([o[0] for o in out], np.int64)
    o_im = np.array([o[1] for o in out], np.int64)
    assert np.array_equal(o_re, re) and np.array_equal(o_im, im)
    assert s_tlast == [1 if (k % N) == N - 1 else 0 for k in range(N * N)]
