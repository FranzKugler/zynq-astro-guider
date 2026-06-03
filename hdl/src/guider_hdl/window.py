"""Hann-window multiplier: signed sample x unsigned coefficient, rounded.

Reference: guider_golden.fixed_point._quantize_input, window step
    windowed = _round_shift(sample * coef, window_bits)
where `sample` is a signed input_bits ADC word and `coef = round(hann * 2**window_bits)`.
"""
from amaranth import *

from .fixed import round_shift_expr


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
