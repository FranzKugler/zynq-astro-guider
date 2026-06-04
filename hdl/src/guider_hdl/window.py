"""Hann-window multiplier: signed sample x unsigned coefficient, rounded.

Reference: guider_golden.fixed_point._quantize_input, window step
    windowed = _round_shift(sample * coef, window_bits)
where `sample` is a signed input_bits ADC word and `coef = round(hann * 2**window_bits)`.
"""
from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .fixed import round_shift_expr
from .stream import Stream


class WindowMul(Elaboratable):
    def __init__(self, sample_bits: int = 12, coef_bits: int = 13,
                 shift: int = 12):
        self.shift = shift
        self.sample = Signal(signed(sample_bits))
        self.coef = Signal(unsigned(coef_bits))
        # product is sample_bits+coef_bits wide; >>shift then +1 for round carry
        self.result = Signal(signed(sample_bits + coef_bits - shift + 1))

    def elaborate(self, platform):
        m = Module()
        prod = self.sample * self.coef            # signed * unsigned -> signed
        m.d.comb += self.result.eq(round_shift_expr(prod, self.shift))
        return m


class WindowStream(wiring.Component):
    """AXI-Stream Hann window: join a real sample stream with a coefficient stream.

    First kernel of the datapath. The Hann coefficients (round(hann2d * 2**window_bits),
    the model's `w_int`) are constant per session, so the PS supplies them as a
    second DMA stream replayed each frame -- no on-chip coef ROM, and bit-exact to
    the model by construction. Output is the windowed real sample feeding the FFT
    (imag side is tied to zero by the FFT-pass kernel).
    """

    def __init__(self, sample_bits: int = 12, window_bits: int = 12):
        self.shift = window_bits
        coef_bits = window_bits + 1                # unsigned hann*2^window_bits
        self.result_bits = sample_bits + coef_bits - window_bits + 1
        super().__init__({
            "sample": In(Stream(signed(sample_bits))),
            "coef":   In(Stream(unsigned(coef_bits))),
            "out":    Out(Stream(signed(self.result_bits))),
        })

    def elaborate(self, platform):
        m = Module()
        m.submodules.mul = mul = WindowMul(
            sample_bits=len(self.sample.payload),
            coef_bits=len(self.coef.payload), shift=self.shift)
        s, c, o = self.sample, self.coef, self.out
        # join: emit one windowed sample per (sample, coef) pair
        m.d.comb += [
            o.valid.eq(s.valid & c.valid),
            s.ready.eq(o.ready & c.valid),
            c.ready.eq(o.ready & s.valid),
            mul.sample.eq(s.payload), mul.coef.eq(c.payload),
            o.payload.eq(mul.result),
            o.first.eq(s.first), o.last.eq(s.last),
        ]
        return m
