"""Phase stage as two DDR-streaming AXI-Stream kernels.

Mirrors `guider_golden.fixed_point.estimate_shift` lines: cross-power
    R = conj(F)*G,  R = _bfp_rescale(R),  R = phase_only(R)
between the forward FFTs and the inverse FFT. Global BFP needs the block max of
the whole N x N frame *before* any element can be rescaled, so it is split into
two passes over DDR (the frame does not fit in BRAM at whole-field sizes):

  pass 1  CrossPower    F,G streams -> R = conj(F)*G (exact) -> DDR
                        and track the frame block max -> BFP shift `sh`
  pass 2  RescalePhase  R stream (from DDR) + `sh` -> BFP rescale -> phase-only

CrossPower/BfpRescale are bit-exact to the model; PhaseOnly is bit-exact to
cordic_ref (~= model within a few LSB). The per-frame `sh` is ShiftFromMax of
pass 1's block max; the PS latches it between the two DMA passes.
"""
from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .stream import Stream, complex_layout
from .cross_power import CrossMul, BlockMax, BfpRescale
from .phase_only import PhaseOnly
from .cordic_ref import CordicParams


class CrossPower(wiring.Component):
    """Pass 1: join F and G, emit exact R = conj(F)*G, track the frame block max.

    Streaming join -- one R beat per (F,G) pair, both consumed together. `o_max`
    holds the running max(|R_re|,|R_im|) over the frame and is final the cycle
    `o_max_valid` pulses (one cycle after the LAST beat is accepted), ready for
    ShiftFromMax. R is the exact (2*mant_bits+1)-wide product, destined for DDR.
    """

    def __init__(self, mant_bits: int = 18):
        self.mant_bits = mant_bits
        self.in_bits = 2 * mant_bits + 1
        super().__init__({
            "f":           In(Stream(complex_layout(mant_bits))),
            "g":           In(Stream(complex_layout(mant_bits))),
            "r":           Out(Stream(complex_layout(self.in_bits))),
            "o_max":       Out(unsigned(self.in_bits)),
            "o_max_valid": Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        m.submodules.mul = mul = CrossMul(mant_bits=self.mant_bits)
        m.submodules.bmax = bmax = BlockMax(in_bits=self.in_bits)
        f, g, r = self.f, self.g, self.r

        # join handshake: fire only when both inputs and the sink are ready
        m.d.comb += [
            r.valid.eq(f.valid & g.valid),
            f.ready.eq(r.ready & g.valid),
            g.ready.eq(r.ready & f.valid),
            mul.f_re.eq(f.payload.re), mul.f_im.eq(f.payload.im),
            mul.g_re.eq(g.payload.re), mul.g_im.eq(g.payload.im),
            r.payload.re.eq(mul.r_re), r.payload.im.eq(mul.r_im),
            r.first.eq(f.first), r.last.eq(f.last),
        ]
        fire = r.valid & r.ready
        m.d.comb += [
            bmax.i_valid.eq(fire), bmax.i_first.eq(f.first),
            bmax.i_re.eq(mul.r_re), bmax.i_im.eq(mul.r_im),
            self.o_max.eq(bmax.o_max),
        ]
        m.d.sync += self.o_max_valid.eq(fire & f.last)
        return m


class RescalePhase(wiring.Component):
    """Pass 2: BFP-rescale R by the per-frame `sh`, then phase-only normalize.

    Pure combinational datapath with stream passthrough (elastic, no buffering);
    pipelining the CORDIC is a later synthesis concern. `sh` is held for the
    whole frame (the PS programs it from pass 1's block max before the DMA).
    """

    def __init__(self, mant_bits: int = 18, p: CordicParams | None = None):
        self.mant_bits = mant_bits
        self.in_bits = 2 * mant_bits + 1
        self.cordic = p or CordicParams(mant_bits=mant_bits)
        self.max_sh = self.in_bits - (mant_bits - 1)
        super().__init__({
            "r":  In(Stream(complex_layout(self.in_bits))),
            "p":  Out(Stream(complex_layout(self.cordic.unit_bits + 2))),
            "sh": In(range(self.max_sh + 1)),
        })

    def elaborate(self, platform):
        m = Module()
        m.submodules.rs_re = rs_re = BfpRescale(self.mant_bits, self.in_bits)
        m.submodules.rs_im = rs_im = BfpRescale(self.mant_bits, self.in_bits)
        m.submodules.po = po = PhaseOnly(self.cordic)
        r, p = self.r, self.p

        m.d.comb += [
            rs_re.value.eq(r.payload.re), rs_re.sh.eq(self.sh),
            rs_im.value.eq(r.payload.im), rs_im.sh.eq(self.sh),
            po.re_in.eq(rs_re.result), po.im_in.eq(rs_im.result),
            p.payload.re.eq(po.re_out), p.payload.im.eq(po.im_out),
            p.valid.eq(r.valid), r.ready.eq(p.ready),
            p.first.eq(r.first), p.last.eq(r.last),
        ]
        return m
