"""Bit-accurate fixed-point CORDIC reference for phase-only normalization.

The golden model's phase-only step uses float atan2/cos/sin (a behavioral spec);
a real CORDIC cannot reproduce that bit-for-bit. So this module is the spec the
*hardware* is held to: the Amaranth `PhaseOnly` block is cosim'd BIT-EXACT
against `phase_only_cordic` here, and `phase_only_cordic` is in turn checked
against the float model within tolerance (~few LSB on the unit vector).

Algorithm: vectoring CORDIC extracts the angle of (re, im); a rotating CORDIC
spins a gain-compensated seed by that angle to regenerate unit*(cos, sin).
Guard bits (gv/gr) below the LSB keep the iterative shifts from bleeding
precision; results are round-half-up back to `unit_bits`.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class CordicParams:
    mant_bits: int = 18    # signed input width (cross-power post BFP rescale)
    unit_bits: int = 15    # output unit-vector scale S = 2**unit_bits
    n_iter: int = 18       # CORDIC iterations (both passes)
    w_angle: int = 24      # internal angle width (full circle = 2**w_angle)
    gv: int = 14          # vectoring guard bits (sized so tiny |R|~1 bins, whose
                          # angle the float model still resolves exactly, stay
                          # within a few LSB; see test tolerance)
    gr: int = 6            # rotating-pass guard bits


def atan_table(p: CordicParams) -> list[int]:
    full = 1 << p.w_angle
    return [round(math.atan(2.0 ** -i) / (2 * math.pi) * full)
            for i in range(p.n_iter)]


def rotation_seed(p: CordicParams) -> int:
    """Gain-compensated seed: rotating S/K by phi yields magnitude S."""
    k = 1.0
    for i in range(p.n_iter):
        k *= math.sqrt(1 + 2.0 ** (-2 * i))
    return round((1 << p.unit_bits) / k * (1 << p.gr))


def phase_only_cordic(re, im, p: CordicParams = CordicParams()):
    """(re, im) -> unit*(cos phi, sin phi); zero input -> (0, 0)."""
    re = np.asarray(re, np.int64)
    im = np.asarray(im, np.int64)
    at = atan_table(p)
    seed = rotation_seed(p)
    full, half, quart = 1 << p.w_angle, 1 << (p.w_angle - 1), 1 << (p.w_angle - 2)
    rh = 1 << (p.gr - 1)

    out_re = np.zeros(re.shape, np.int64)
    out_im = np.zeros(re.shape, np.int64)
    for idx in np.ndindex(re.shape):
        x = int(re[idx]) << p.gv
        y = int(im[idx]) << p.gv
        if x == 0 and y == 0:
            continue
        z = 0
        if x < 0:                       # pre-rotate into [-90, 90] (need x>=0)
            if y >= 0:
                x, y, z = y, -x, quart
            else:
                x, y, z = -y, x, -quart
        for i in range(p.n_iter):       # vectoring: drive y -> 0, z -> angle
            dx, dy = x >> i, y >> i
            if y >= 0:
                x, y, z = x + dy, y - dx, z + at[i]
            else:
                x, y, z = x - dy, y + dx, z - at[i]
        phi = z & (full - 1)
        if phi >= half:
            phi -= full

        xr, yr, zr = seed, 0, phi
        if zr > quart:                  # pre-rotate seed into [-90, 90]
            xr, zr = -xr, zr - half
        elif zr < -quart:
            xr, zr = -xr, zr + half
        for i in range(p.n_iter):       # rotating: drive zr -> 0
            dx, dy = xr >> i, yr >> i
            if zr < 0:
                xr, yr, zr = xr + dy, yr - dx, zr + at[i]
            else:
                xr, yr, zr = xr - dy, yr + dx, zr - at[i]
        out_re[idx] = (xr + rh) >> p.gr
        out_im[idx] = (yr + rh) >> p.gr
    return out_re, out_im
