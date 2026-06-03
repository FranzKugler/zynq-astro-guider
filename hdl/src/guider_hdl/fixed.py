"""Shared fixed-point primitives, bit-matched to guider_golden.fixed_point.

These reproduce the model's arithmetic conventions in hardware so the datapath
cosims bit-exact. Keep any change here in lockstep with the Python model.
"""
from amaranth import *


def round_shift_expr(value, shift: int):
    """Arithmetic right shift of signed `value` by `shift`, convergent rounding.

    Round half to even == Xilinx convergent rounding == the model's
    `_round_shift(..., "convergent")`. Returns a combinational Value.
    """
    if shift <= 0:
        return value << (-shift)
    q = value >> shift            # arithmetic floor shift (signed Value)
    r = value[:shift]             # low `shift` bits = nonneg remainder
    half = 1 << (shift - 1)
    up = (r > half) | ((r == half) & q[0])
    return q + up
