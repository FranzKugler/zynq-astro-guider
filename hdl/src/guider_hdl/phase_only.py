"""Phase-only normalization in hardware: R -> R/|R| via a two-pass CORDIC.

Combinational unrolled CORDIC, bit-matched to `cordic_ref.phase_only_cordic`
(shared angle table + seed, identical decisions and arithmetic-floor shifts).
Pipelining the two passes is a later synthesis concern.
"""
from amaranth import *

from .cordic_ref import CordicParams, atan_table, rotation_seed


class PhaseOnly(Elaboratable):
    def __init__(self, p: CordicParams = CordicParams()):
        self.p = p
        self.re_in = Signal(signed(p.mant_bits))
        self.im_in = Signal(signed(p.mant_bits))
        self.re_out = Signal(signed(p.unit_bits + 2))   # holds +-2**unit_bits
        self.im_out = Signal(signed(p.unit_bits + 2))

    def elaborate(self, platform):
        p = self.p
        m = Module()
        at = atan_table(p)
        seed = rotation_seed(p)
        half = 1 << (p.w_angle - 1)
        quart = 1 << (p.w_angle - 2)

        xy_w = p.mant_bits + p.gv + 4        # vectoring datapath
        z_w = p.w_angle + 3                  # angle accumulator
        r_w = p.unit_bits + p.gr + 4         # rotating datapath

        # ---- vectoring: extract the angle of (re_in, im_in) ----
        re_g = Signal(signed(xy_w))
        im_g = Signal(signed(xy_w))
        m.d.comb += [re_g.eq(self.re_in << p.gv), im_g.eq(self.im_in << p.gv)]

        x = [Signal(signed(xy_w), name=f"vx{i}") for i in range(p.n_iter + 1)]
        y = [Signal(signed(xy_w), name=f"vy{i}") for i in range(p.n_iter + 1)]
        z = [Signal(signed(z_w), name=f"vz{i}") for i in range(p.n_iter + 1)]

        with m.If(self.re_in < 0):           # pre-rotate into [-90, 90]
            with m.If(self.im_in >= 0):
                m.d.comb += [x[0].eq(im_g), y[0].eq(-re_g), z[0].eq(quart)]
            with m.Else():
                m.d.comb += [x[0].eq(-im_g), y[0].eq(re_g), z[0].eq(-quart)]
        with m.Else():
            m.d.comb += [x[0].eq(re_g), y[0].eq(im_g), z[0].eq(0)]

        for i in range(p.n_iter):
            dx, dy = x[i] >> i, y[i] >> i
            with m.If(y[i] >= 0):
                m.d.comb += [x[i + 1].eq(x[i] + dy), y[i + 1].eq(y[i] - dx),
                             z[i + 1].eq(z[i] + at[i])]
            with m.Else():
                m.d.comb += [x[i + 1].eq(x[i] - dy), y[i + 1].eq(y[i] + dx),
                             z[i + 1].eq(z[i] - at[i])]

        phi = Signal(signed(p.w_angle))      # wrap(z) == low w_angle bits, signed
        m.d.comb += phi.eq(z[p.n_iter][:p.w_angle].as_signed())

        # ---- rotating: spin gain-compensated seed by phi ----
        xr = [Signal(signed(r_w), name=f"rx{i}") for i in range(p.n_iter + 1)]
        yr = [Signal(signed(r_w), name=f"ry{i}") for i in range(p.n_iter + 1)]
        zr = [Signal(signed(z_w), name=f"rz{i}") for i in range(p.n_iter + 1)]

        with m.If(phi > quart):
            m.d.comb += [xr[0].eq(-seed), yr[0].eq(0), zr[0].eq(phi - half)]
        with m.Elif(phi < -quart):
            m.d.comb += [xr[0].eq(-seed), yr[0].eq(0), zr[0].eq(phi + half)]
        with m.Else():
            m.d.comb += [xr[0].eq(seed), yr[0].eq(0), zr[0].eq(phi)]

        for i in range(p.n_iter):
            dx, dy = xr[i] >> i, yr[i] >> i
            with m.If(zr[i] < 0):
                m.d.comb += [xr[i + 1].eq(xr[i] + dy), yr[i + 1].eq(yr[i] - dx),
                             zr[i + 1].eq(zr[i] + at[i])]
            with m.Else():
                m.d.comb += [xr[i + 1].eq(xr[i] - dy), yr[i + 1].eq(yr[i] + dx),
                             zr[i + 1].eq(zr[i] - at[i])]

        rh = 1 << (p.gr - 1)                 # round-half-up dropping guard bits
        re_raw = (xr[p.n_iter] + rh) >> p.gr
        im_raw = (yr[p.n_iter] + rh) >> p.gr
        with m.If((self.re_in == 0) & (self.im_in == 0)):
            m.d.comb += [self.re_out.eq(0), self.im_out.eq(0)]
        with m.Else():
            m.d.comb += [self.re_out.eq(re_raw), self.im_out.eq(im_raw)]
        return m
