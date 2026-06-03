"""Cross-power stage: R = conj(F) * G, then block-floating-point rescale.

Reference: guider_golden.fixed_point, cross-power + _bfp_rescale
    R_re = F_re*G_re + F_im*G_im
    R_im = F_re*G_im - F_im*G_re
    R_re, R_im, _ = _bfp_rescale(R_re, R_im, cfg)

Decomposed into per-element / per-block hardware:
  CrossMul     per element: the complex conj-multiply (exact, no rounding)
  ShiftFromMax per block:   block max magnitude -> BFP shift amount
  BfpRescale   per element: round (convergent) + saturate by that shift

The block-max reduction that feeds ShiftFromMax is streaming logic, deferred to
the top-level stream wrapper; here the shift is derived from the block max and
applied, each piece cosim'd bit-exact against the model.
"""
from amaranth import *

from .fixed import round_shift_var_expr, saturate_expr, bit_length


class CrossMul(Elaboratable):
    """Per-element conj(F)*G. Outputs are exact (full product width)."""
    def __init__(self, mant_bits: int = 18):
        self.mant_bits = mant_bits
        self.f_re = Signal(signed(mant_bits))
        self.f_im = Signal(signed(mant_bits))
        self.g_re = Signal(signed(mant_bits))
        self.g_im = Signal(signed(mant_bits))
        w = 2 * mant_bits + 1                  # product*2 + carry
        self.r_re = Signal(signed(w))
        self.r_im = Signal(signed(w))

    def elaborate(self, platform):
        m = Module()
        m.d.comb += [
            self.r_re.eq(self.f_re * self.g_re + self.f_im * self.g_im),
            self.r_im.eq(self.f_re * self.g_im - self.f_im * self.g_re),
        ]
        return m


class ShiftFromMax(Elaboratable):
    """Block max magnitude -> BFP shift = max(0, bit_length(mag) - (mant_bits-1))."""
    def __init__(self, mant_bits: int = 18, in_bits: int = 2 * 18 + 1):
        self.mant_bits = mant_bits
        self.in_bits = in_bits
        self.max_sh = in_bits - (mant_bits - 1)
        self.mag = Signal(unsigned(in_bits))
        self.sh = Signal(range(self.max_sh + 1))

    def elaborate(self, platform):
        m = Module()
        bl = bit_length(m, self.mag, self.in_bits)
        keep = self.mant_bits - 1
        m.d.comb += self.sh.eq(Mux(bl > keep, bl - keep, 0))
        return m


class BfpRescale(Elaboratable):
    """One lane: convergent round-shift by `sh`, then saturate to mant_bits."""
    def __init__(self, mant_bits: int = 18, in_bits: int = 2 * 18 + 1):
        self.mant_bits = mant_bits
        self.in_bits = in_bits
        self.max_sh = in_bits - (mant_bits - 1)
        self.value = Signal(signed(in_bits))
        self.sh = Signal(range(self.max_sh + 1))
        self.result = Signal(signed(mant_bits))

    def elaborate(self, platform):
        m = Module()
        rs = round_shift_var_expr(self.value, self.sh, self.max_sh)
        m.d.comb += self.result.eq(saturate_expr(rs, self.mant_bits))
        return m
