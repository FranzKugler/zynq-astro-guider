"""Cosim the two DDR-streaming phase-stage kernels against the golden model.

Pass 1 (CrossPower) and pass 2 (RescalePhase) together reproduce the model's
cross-power -> BFP rescale -> phase-only, bit-exact except the CORDIC (vs float
atan2/cos/sin), which is bit-exact to cordic_ref and within a few LSB of the
model. The block max from pass 1 feeds the BFP shift of pass 2, exactly as the
PS will latch it between the two DMA passes.
"""
import numpy as np
from amaranth.sim import Simulator

from guider_hdl.phase_stage import CrossPower, RescalePhase
from guider_hdl.cordic_ref import CordicParams, phase_only_cordic
from guider_golden import synthetic_starfield, fourier_shift
from guider_golden.fixed_point import (
    FixedConfig, _quantize_input, _fft2d, _bfp_rescale,
)

MANT = 18
IN_BITS = 2 * MANT + 1
LIMIT = (1 << (MANT - 1)) - 1
P = CordicParams()
TOL = 8                       # max abs LSB error, CORDIC vs float model


def _model_sh(mx):
    return 0 if mx <= LIMIT else int(mx).bit_length() - (MANT - 1)


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


def _cross_power(fre, fim, gre, gim):
    """Drive CrossPower full-rate; return the R stream and the frame block max."""
    dut = CrossPower(mant_bits=MANT)
    n = len(fre)
    r = []
    res = {}

    async def tb(ctx):
        ctx.set(dut.r.ready, 1)
        for i in range(n):
            ctx.set(dut.f.valid, 1)
            ctx.set(dut.g.valid, 1)
            ctx.set(dut.f.payload.re, int(fre[i]))
            ctx.set(dut.f.payload.im, int(fim[i]))
            ctx.set(dut.g.payload.re, int(gre[i]))
            ctx.set(dut.g.payload.im, int(gim[i]))
            first = 1 if i == 0 else 0
            last = 1 if i == n - 1 else 0
            ctx.set(dut.f.first, first)
            ctx.set(dut.g.first, first)
            ctx.set(dut.f.last, last)
            ctx.set(dut.g.last, last)
            r.append((ctx.get(dut.r.payload.re), ctx.get(dut.r.payload.im)))
            await ctx.tick()
        ctx.set(dut.f.valid, 0)
        ctx.set(dut.g.valid, 0)
        ctx.set(dut.f.last, 0)
        ctx.set(dut.g.last, 0)
        res["max"] = ctx.get(dut.o_max)            # final after the last edge
        res["ov"] = ctx.get(dut.o_max_valid)       # pulses one cycle past last

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return np.array(r, np.int64), res


def _rescale_phase(rre, rim, sh):
    """Drive RescalePhase (pure combinational); return the P stream."""
    dut = RescalePhase(mant_bits=MANT, p=P)
    out = []

    async def tb(ctx):
        ctx.set(dut.p.ready, 1)
        ctx.set(dut.sh, int(sh))
        for a, b in zip(rre, rim):
            ctx.set(dut.r.valid, 1)
            ctx.set(dut.r.payload.re, int(a))
            ctx.set(dut.r.payload.im, int(b))
            out.append((ctx.get(dut.p.payload.re), ctx.get(dut.p.payload.im)))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    return np.array(out, np.int64)


def _forward_ffts(seed, shift):
    cfg = FixedConfig(unit_bits=P.unit_bits)
    ref = synthetic_starfield(shape=(16, 16), n_stars=8, seed=seed)
    img = fourier_shift(ref, shift)
    pk = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / pk
    rr = _quantize_input(ref, scale, None, cfg)
    ii = _quantize_input(img, scale, None, cfg)
    z = np.zeros_like(rr)
    F_re, F_im, _ = _fft2d(rr, z, cfg, inverse=False)
    G_re, G_im, _ = _fft2d(ii, z.copy(), cfg, inverse=False)
    return cfg, F_re, F_im, G_re, G_im


def test_cross_power_stream_bit_exact():
    """Pass 1: streamed R == model conj(F)*G; block max == model BFP max/shift."""
    _, F_re, F_im, G_re, G_im = _forward_ffts(seed=1, shift=(2.0, -1.0))
    fr, fi = F_re.ravel(), F_im.ravel()
    gr, gi = G_re.ravel(), G_im.ravel()

    R, res = _cross_power(fr, fi, gr, gi)
    raw_re = fr.astype(np.int64) * gr + fi.astype(np.int64) * gi
    raw_im = fr.astype(np.int64) * gi - fi.astype(np.int64) * gr
    assert np.array_equal(R[:, 0], raw_re)
    assert np.array_equal(R[:, 1], raw_im)

    mx = int(max(np.abs(raw_re).max(), np.abs(raw_im).max()))
    assert res["max"] == mx
    assert res["ov"] == 1
    _, _, sh = _bfp_rescale(raw_re.copy(), raw_im.copy(),
                            FixedConfig(mant_bits=MANT))
    assert _model_sh(res["max"]) == sh


def test_phase_stage_end_to_end():
    """Pass 1 -> shift -> pass 2 reproduces the model phase-only output."""
    cfg, F_re, F_im, G_re, G_im = _forward_ffts(seed=2, shift=(1.0, 2.0))
    fr, fi = F_re.ravel(), F_im.ravel()
    gr, gi = G_re.ravel(), G_im.ravel()

    R, res = _cross_power(fr, fi, gr, gi)
    sh = _model_sh(res["max"])
    P_hw = _rescale_phase(R[:, 0], R[:, 1], sh)

    # model: BFP-rescale the raw cross-power, then phase-only
    raw_re = fr.astype(np.int64) * gr + fi.astype(np.int64) * gi
    raw_im = fr.astype(np.int64) * gi - fi.astype(np.int64) * gr
    bfp_re, bfp_im, _ = _bfp_rescale(raw_re.copy(), raw_im.copy(), cfg)

    # bit-exact to the CORDIC reference fed the same BFP-rescaled bins
    ref_re, ref_im = phase_only_cordic(bfp_re, bfp_im, P)
    assert np.array_equal(P_hw[:, 0], ref_re)
    assert np.array_equal(P_hw[:, 1], ref_im)

    # and within a few LSB of the float model
    mre, mim = _model_phase_only(bfp_re, bfp_im, cfg)
    assert np.abs(P_hw[:, 0] - mre).max() <= TOL
    assert np.abs(P_hw[:, 1] - mim).max() <= TOL
