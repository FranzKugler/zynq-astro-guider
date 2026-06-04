"""End-to-end cosim integration harness (NON-SYNTHESIZABLE).

Drives the real Amaranth datapath blocks through the Amaranth simulator, in the
exact dataflow of `guider_golden.fixed_point.estimate_shift`, with the FFT/IFFT
substituted by the model's BFP FFT (the behavioral stand-in for the Xilinx FFT
IP, which pysim cannot run). `estimate_shift_hw` keeps the model's interface so
it can be compared directly against it.

This closes the chain end-to-end -- window -> FFT -> cross-power -> phase-only
-> IFFT -> peak -> subpixel -- before the Vivado/xsim FFT-IP wrapper exists.
Everything is bit-exact to the model except phase-only (CORDIC vs float atan2),
so the HW shift estimate should track the model within a small tolerance.
"""
from __future__ import annotations
import numpy as np
from amaranth.sim import Simulator

from guider_golden.fixed_point import (
    FixedConfig, _quantize_input, _fft2d, hann2d,
)
from guider_golden.phase_correlation import _parabolic_offset

from .window import WindowMul
from .cross_power import CrossMul, ShiftFromMax, BfpRescale, BlockMax
from .phase_only import PhaseOnly
from .cordic_ref import CordicParams


def _run_comb(dut, vectors, in_ports, out_ports):
    """Drive a combinational block over `vectors`; return list of output tuples."""
    res = []

    async def tb(ctx):
        for vec in vectors:
            for port, val in zip(in_ports, vec):
                ctx.set(getattr(dut, port), int(val))
            res.append(tuple(ctx.get(getattr(dut, o)) for o in out_ports))

    sim = Simulator(dut)
    sim.add_testbench(tb)
    sim.run()
    return res


def _block_max(dut, re, im):
    """Sequential streaming max over (re, im)."""
    out = {}

    async def tb(ctx):
        for k, (a, b) in enumerate(zip(re, im)):
            ctx.set(dut.i_valid, 1)
            ctx.set(dut.i_first, 1 if k == 0 else 0)
            ctx.set(dut.i_re, int(a))
            ctx.set(dut.i_im, int(b))
            await ctx.tick()
        out["m"] = ctx.get(dut.o_max)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(tb)
    sim.run()
    return out["m"]


def estimate_shift_hw(ref, img, *, window: bool = True, subpixel: bool = True,
                      cfg: FixedConfig = FixedConfig(), p: CordicParams | None = None):
    """HW-block pipeline estimate of (dy, dx, peak, corr); model's interface."""
    if p is None:
        p = CordicParams(mant_bits=cfg.mant_bits, unit_bits=cfg.unit_bits)
    ref = np.asarray(ref, np.float64)
    img = np.asarray(img, np.float64)
    ny, nx = ref.shape
    in_bits = 2 * cfg.mant_bits + 1

    # ---- input scale (sensor ADC, in Python), then Hann via WindowMul ----
    peak_in = max(float(np.abs(ref).max()), float(np.abs(img).max()), 1e-30)
    scale = ((1 << (cfg.input_bits - 1)) - 1) / peak_in
    ref_q = _quantize_input(ref, scale, None, cfg)
    img_q = _quantize_input(img, scale, None, cfg)
    if window:
        w_int = np.round(hann2d(ref.shape) * (1 << cfg.window_bits)).astype(np.int64)
        wm = WindowMul(sample_bits=cfg.input_bits, coef_bits=cfg.window_bits + 1,
                       shift=cfg.window_bits)
        wc = w_int.ravel()
        ref_q = np.array([r[0] for r in _run_comb(
            wm, list(zip(ref_q.ravel(), wc)), ["sample", "coef"], ["result"])]
        ).reshape(ref.shape)
        img_q = np.array([r[0] for r in _run_comb(
            wm, list(zip(img_q.ravel(), wc)), ["sample", "coef"], ["result"])]
        ).reshape(img.shape)

    # ---- forward FFT (model = behavioral stand-in for the IP) ----
    z = np.zeros_like(ref_q)
    F_re, F_im, _ = _fft2d(ref_q, z, cfg, inverse=False)
    G_re, G_im, _ = _fft2d(img_q, z.copy(), cfg, inverse=False)

    # ---- cross-power conj(F)*G via CrossMul ----
    cm = CrossMul(mant_bits=cfg.mant_bits)
    prod = _run_comb(cm, list(zip(F_re.ravel(), F_im.ravel(),
                                  G_re.ravel(), G_im.ravel())),
                     ["f_re", "f_im", "g_re", "g_im"], ["r_re", "r_im"])
    raw_re = np.array([r[0] for r in prod], np.int64)
    raw_im = np.array([r[1] for r in prod], np.int64)

    # ---- BFP: BlockMax -> ShiftFromMax -> BfpRescale ----
    mx = _block_max(BlockMax(in_bits=in_bits), raw_re, raw_im)
    sh = _run_comb(ShiftFromMax(mant_bits=cfg.mant_bits, in_bits=in_bits),
                   [(mx,)], ["mag"], ["sh"])[0][0]
    rsc = BfpRescale(mant_bits=cfg.mant_bits, in_bits=in_bits)
    R_re = np.array([r[0] for r in _run_comb(
        rsc, [(v, sh) for v in raw_re], ["value", "sh"], ["result"])], np.int64)
    R_im = np.array([r[0] for r in _run_comb(
        rsc, [(v, sh) for v in raw_im], ["value", "sh"], ["result"])], np.int64)

    # ---- phase-only via CORDIC ----
    po = _run_comb(PhaseOnly(p), list(zip(R_re, R_im)),
                   ["re_in", "im_in"], ["re_out", "im_out"])
    P_re = np.array([r[0] for r in po], np.int64).reshape(ref.shape)
    P_im = np.array([r[1] for r in po], np.int64).reshape(ref.shape)

    # ---- inverse FFT (model stand-in) + peak + subpixel (on the PS) ----
    corr_re, _ci, _ = _fft2d(P_re, P_im, cfg, inverse=True)
    corr = corr_re.astype(np.float64)
    py, px = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak_val = float(corr[py, px])
    sub_dy = sub_dx = 0.0
    if subpixel:
        sub_dy = _parabolic_offset(corr[(py - 1) % ny, px], corr[py, px],
                                   corr[(py + 1) % ny, px])
        sub_dx = _parabolic_offset(corr[py, (px - 1) % nx], corr[py, px],
                                   corr[py, (px + 1) % nx])
    dy = (((py + ny // 2) % ny) - ny // 2) + sub_dy
    dx = (((px + nx // 2) % nx) - nx // 2) + sub_dx
    return dy, dx, peak_val, corr
