"""Integration: the assembled PL top routes each endpoint to its kernel.

Drives the PhaseCorrelatorPL endpoints (with a behavioral FFT stub) and checks
each kernel is reached and behaves as in its standalone cosim: the window path,
and the two phase-stage passes (cross-power -> max -> rescale/phase-only) end to
end vs the golden model. The FFT transform itself is xsim-verified
(sim/fft_cosim.py); the shared fft_in/out endpoint's framing is covered by
test_fft_pass (same FftPass), so here we only smoke its routing.
"""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.top import PhaseCorrelatorPL
from guider_hdl.fft_ip import FftStub
from guider_hdl.cordic_ref import CordicParams, phase_only_cordic
from guider_golden import synthetic_starfield, fourier_shift
from guider_golden.fixed_point import (
    FixedConfig, _quantize_input, _fft2d, _bfp_rescale, hann2d,
)

N = 16
MANT = 18
LIMIT = (1 << (MANT - 1)) - 1
P = CordicParams()
TOL = 8


def _top():
    return PhaseCorrelatorPL(n=N, mant_bits=MANT, input_bits=12, window_bits=12,
                             core=FftStub(n=N, input_width=MANT))


def _model_sh(mx):
    return 0 if mx <= LIMIT else int(mx).bit_length() - (MANT - 1)


def _forward_ffts(seed, shift):
    cfg = FixedConfig(unit_bits=P.unit_bits)
    ref = synthetic_starfield(shape=(N, N), n_stars=8, seed=seed)
    img = fourier_shift(ref, shift)
    pk = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / pk
    z = np.zeros_like(_quantize_input(ref, scale, None, cfg))
    F = _fft2d(_quantize_input(ref, scale, None, cfg), z, cfg, inverse=False)
    G = _fft2d(_quantize_input(img, scale, None, cfg), z.copy(), cfg, inverse=False)
    return cfg, F[0], F[1], G[0], G[1]


def test_phase_stage_through_top():
    cfg, F_re, F_im, G_re, G_im = _forward_ffts(seed=2, shift=(1.0, 2.0))
    fr, fi = F_re.ravel(), F_im.ravel()
    gr, gi = G_re.ravel(), G_im.ravel()
    n = len(fr)
    dut = _top()
    R = []
    res = {}

    async def tb(ctx):
        ctx.set(dut.xpower_r.ready, 1)
        ctx.set(dut.rescale_p.ready, 1)
        # pass 1: cross-power
        for i in range(n):
            ctx.set(dut.xpower_f.valid, 1)
            ctx.set(dut.xpower_g.valid, 1)
            ctx.set(dut.xpower_f.payload.re, int(fr[i]))
            ctx.set(dut.xpower_f.payload.im, int(fi[i]))
            ctx.set(dut.xpower_g.payload.re, int(gr[i]))
            ctx.set(dut.xpower_g.payload.im, int(gi[i]))
            first = 1 if i == 0 else 0
            last = 1 if i == n - 1 else 0
            ctx.set(dut.xpower_f.first, first)
            ctx.set(dut.xpower_g.first, first)
            ctx.set(dut.xpower_f.last, last)
            ctx.set(dut.xpower_g.last, last)
            R.append((ctx.get(dut.xpower_r.payload.re),
                      ctx.get(dut.xpower_r.payload.im)))
            await ctx.tick()
        ctx.set(dut.xpower_f.valid, 0)
        ctx.set(dut.xpower_g.valid, 0)
        res["max"] = ctx.get(dut.xpower_max)
        # pass 2: rescale + phase-only (combinational), sh from pass 1's max
        sh = _model_sh(res["max"])
        ctx.set(dut.rescale_sh, sh)
        P_out = []
        for a, b in R:
            ctx.set(dut.rescale_r.valid, 1)
            ctx.set(dut.rescale_r.payload.re, int(a))
            ctx.set(dut.rescale_r.payload.im, int(b))
            P_out.append((ctx.get(dut.rescale_p.payload.re),
                          ctx.get(dut.rescale_p.payload.im)))
        res["P"] = np.array(P_out, np.int64)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()

    raw_re = fr.astype(np.int64) * gr + fi.astype(np.int64) * gi
    raw_im = fr.astype(np.int64) * gi - fi.astype(np.int64) * gr
    R = np.array(R, np.int64)
    assert np.array_equal(R[:, 0], raw_re) and np.array_equal(R[:, 1], raw_im)
    assert res["max"] == int(max(np.abs(raw_re).max(), np.abs(raw_im).max()))

    bfp_re, bfp_im, _ = _bfp_rescale(raw_re.copy(), raw_im.copy(), cfg)
    ref_re, ref_im = phase_only_cordic(bfp_re, bfp_im, P)
    assert np.array_equal(res["P"][:, 0], ref_re)
    assert np.array_equal(res["P"][:, 1], ref_im)


def test_window_through_top():
    cfg = FixedConfig()
    frame = synthetic_starfield(shape=(N, N), n_stars=8, seed=3)
    pk = max(float(np.abs(frame).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / pk
    samples = _quantize_input(frame, scale, None, cfg).ravel()
    w_int = np.round(hann2d(frame.shape) * (1 << cfg.window_bits)).astype(np.int64)
    coefs = w_int.ravel()
    want = _quantize_input(frame, scale, w_int, cfg).ravel()

    dut = _top()
    out = []

    async def tb(ctx):
        ctx.set(dut.window_out.ready, 1)
        for i in range(len(samples)):
            ctx.set(dut.window_sample.valid, 1)
            ctx.set(dut.window_coef.valid, 1)
            ctx.set(dut.window_sample.payload, int(samples[i]))
            ctx.set(dut.window_coef.payload, int(coefs[i]))
            out.append(ctx.get(dut.window_out.payload))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    assert np.array_equal(np.array(out, np.int64), want)


def test_fft_endpoint_routing_smoke():
    """fft_in -> fft_out reaches the shared FftPass (identity stub passthrough)."""
    dut = _top()
    rng = np.random.default_rng(5)
    re = rng.integers(-(1 << (MANT - 1)), 1 << (MANT - 1), N * N).astype(np.int64)
    im = rng.integers(-(1 << (MANT - 1)), 1 << (MANT - 1), N * N).astype(np.int64)
    out = []

    async def tb(ctx):
        ctx.set(dut.fft_inverse, 0)
        ctx.set(dut.fft_out.ready, 1)
        i = 0
        c = 0
        while len(out) < N * N and c < 6000:
            if i < N * N:
                ctx.set(dut.fft_in.valid, 1)
                ctx.set(dut.fft_in.payload.re, int(re[i]))
                ctx.set(dut.fft_in.payload.im, int(im[i]))
                ctx.set(dut.fft_in.first, 1 if i == 0 else 0)
                ctx.set(dut.fft_in.last, 1 if i == N * N - 1 else 0)
            else:
                ctx.set(dut.fft_in.valid, 0)
            in_fire = ctx.get(dut.fft_in.valid) and ctx.get(dut.fft_in.ready)
            out_fire = ctx.get(dut.fft_out.valid) and ctx.get(dut.fft_out.ready)
            if out_fire:
                out.append((ctx.get(dut.fft_out.payload.re),
                            ctx.get(dut.fft_out.payload.im),
                            ctx.get(dut.fft_out.first), ctx.get(dut.fft_out.last)))
            await ctx.tick()
            if in_fire:
                i += 1

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    assert len(out) == N * N
    assert np.array_equal(np.array([o[0] for o in out], np.int64), re)
    assert np.array_equal(np.array([o[1] for o in out], np.int64), im)
    assert out[0][2] == 1 and out[-1][3] == 1
