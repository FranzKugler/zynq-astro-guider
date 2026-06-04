"""Shared fixed-point primitives, bit-matched to guider_golden.fixed_point.

These reproduce the model's arithmetic conventions in hardware so the datapath
cosims bit-exact. Keep any change here in lockstep with the Python model.
"""
from amaranth import *


def round_shift_expr(value, shift: int):
    """Arithmetic right shift of signed `value` by constant `shift`, convergent.

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


def round_shift_var_expr(value, sh, max_sh: int):
    """Like round_shift_expr but `sh` is a runtime Value in [0, max_sh].

    Same convergent rounding. Used by BFP rescale, where the block exponent
    (hence the shift) is only known at run time.
    """
    pow_sh = Const(1, unsigned(max_sh + 1)) << sh      # 2**sh
    mask = pow_sh - 1                                  # low sh bits
    half = pow_sh >> 1                                 # 2**(sh-1), 0 for sh==0
    q = value >> sh                                    # arithmetic floor
    r = value & mask                                   # nonneg remainder
    up = (r > half) | ((r == half) & q[0])
    return Mux(sh == 0, value, q + up)


def abs_expr(value):
    """Unsigned magnitude of a signed Value."""
    return Mux(value < 0, -value, value)


def saturate_expr(value, bits: int):
    """Clamp signed `value` to a signed `bits`-wide range (== model's np.clip)."""
    hi = (1 << (bits - 1)) - 1
    lo = -(1 << (bits - 1))
    return Mux(value > hi, hi, Mux(value < lo, lo, value))


def bit_length(m, mag, width: int):
    """Number of significant bits of unsigned `mag` (0 -> 0). Combinational.

    Priority encoder: the highest set bit wins (later comb writes override).
    """
    bl = Signal(range(width + 1))
    m.d.comb += bl.eq(0)
    for i in range(width):
        with m.If(mag[i]):
            m.d.comb += bl.eq(i + 1)
    return bl
