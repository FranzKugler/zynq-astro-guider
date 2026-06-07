"""AXI4-Lite control/status register file for the PL datapath.

Wraps `top.PhaseCorrelatorPL` so the whole PL is one IP with a single AXI-Lite
slave (the PS's control plane) plus the AXIS data ports. This is the register map
`guider_target.UioBackend` will poke; keep the two in lockstep.

Register map (byte offset, 32-bit words):
  0x00 CTRL      RW  [0] fft_inverse           [5:1] rescale_sh
                     [6] dpath_reset (level: flush AXIS switches via rst_dpath +
                         resync FftPass row/o_cnt; PS pulses before each frame)
  0x04 STATUS    R   [0] xpower_done (sticky)  [1] fft_done (sticky)
                 W   write-1-to-clear those sticky bits
  0x08 XPMAX_LO  R   xpower_max[31:0]   (latched on xpower_max_valid)
  0x0c XPMAX_HI  R   xpower_max[36:32]
  0x10 BLKEXP    R   fft_blk_exp_sum[15:0] (latched on the fft frame's last beat)
  0x14 ID        R   0x47_44_52_31 ("GDR1") -- bitstream sanity check

The sticky done bits + latched values let the PS read a pass's result after its
DMA completes without racing the one-cycle status strobes. The AXI-Lite slave is
single-transaction (aw->w->b, ar->r); ample for a control plane.
"""
from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, connect, flipped

from .stream import AXIStream, FirstGen
from .top import PhaseCorrelatorPL
from .cordic_ref import CordicParams

ID_MAGIC = 0x47445231          # "GDR1"


class AXILite(wiring.Signature):
    """AXI4-Lite, declared from the manager (master) viewpoint."""

    def __init__(self, addr_width: int = 8, data_width: int = 32):
        self.addr_width = addr_width
        self.data_width = data_width
        super().__init__({
            "awaddr":  Out(addr_width), "awvalid": Out(1), "awready": In(1),
            "wdata":   Out(data_width), "wstrb": Out(data_width // 8),
            "wvalid":  Out(1), "wready": In(1),
            "bresp":   In(2), "bvalid": In(1), "bready": Out(1),
            "araddr":  Out(addr_width), "arvalid": Out(1), "arready": In(1),
            "rdata":   In(data_width), "rresp": In(2),
            "rvalid":  In(1), "rready": Out(1),
        })


class PhaseCorrelatorCsr(wiring.Component):
    """AXI-Lite slave exposing the datapath control/status (see module map)."""

    def __init__(self, sh_bits: int = 5, max_bits: int = 37, blk_bits: int = 16,
                 addr_width: int = 8):
        self.sh_bits = sh_bits
        self.max_bits = max_bits
        self.blk_bits = blk_bits
        self.addr_width = addr_width
        super().__init__({
            "s_axil":             In(AXILite(addr_width=addr_width)),
            # control outputs -> PhaseCorrelatorPL
            "o_fft_inverse":      Out(1),
            "o_rescale_sh":       Out(sh_bits),
            "o_dpath_reset":      Out(1),
            # status inputs <- PhaseCorrelatorPL
            "i_xpower_max":       In(max_bits),
            "i_xpower_max_valid": In(1),
            "i_fft_blk_exp_sum":  In(blk_bits),
            "i_fft_done":         In(1),
        })

    def elaborate(self, platform):
        m = Module()
        ax = self.s_axil

        fft_inverse = Signal()
        rescale_sh = Signal(self.sh_bits)
        dpath_reset = Signal()
        xpower_done = Signal()
        fft_done = Signal()
        xpmax = Signal(self.max_bits)
        blkexp = Signal(self.blk_bits)
        m.d.comb += [self.o_fft_inverse.eq(fft_inverse),
                     self.o_rescale_sh.eq(rescale_sh),
                     self.o_dpath_reset.eq(dpath_reset)]

        # --- status capture (one-cycle strobes -> latched value + sticky flag) ---
        with m.If(self.i_xpower_max_valid):
            m.d.sync += [xpmax.eq(self.i_xpower_max), xpower_done.eq(1)]
        with m.If(self.i_fft_done):
            m.d.sync += [blkexp.eq(self.i_fft_blk_exp_sum), fft_done.eq(1)]

        m.d.comb += [ax.bresp.eq(0), ax.rresp.eq(0)]   # always OKAY

        # --- write channel: AW -> W -> B ---
        waddr = Signal(self.addr_width)
        with m.FSM(name="wr"):
            with m.State("AW"):
                m.d.comb += ax.awready.eq(1)
                with m.If(ax.awvalid):
                    m.d.sync += waddr.eq(ax.awaddr)
                    m.next = "W"
            with m.State("W"):
                m.d.comb += ax.wready.eq(1)
                with m.If(ax.wvalid):
                    with m.Switch(waddr[2:]):
                        with m.Case(0x00 >> 2):            # CTRL
                            m.d.sync += [fft_inverse.eq(ax.wdata[0]),
                                         rescale_sh.eq(ax.wdata[1:1 + self.sh_bits]),
                                         dpath_reset.eq(ax.wdata[6])]
                        with m.Case(0x04 >> 2):            # STATUS: W1C
                            with m.If(ax.wdata[0]):
                                m.d.sync += xpower_done.eq(0)
                            with m.If(ax.wdata[1]):
                                m.d.sync += fft_done.eq(0)
                    m.next = "B"
            with m.State("B"):
                m.d.comb += ax.bvalid.eq(1)
                with m.If(ax.bready):
                    m.next = "AW"

        # --- read channel: AR -> R ---
        raddr = Signal(self.addr_width)
        rdata = Signal(32)
        with m.Switch(raddr[2:]):
            with m.Case(0x00 >> 2):
                m.d.comb += rdata.eq(Cat(fft_inverse, rescale_sh, dpath_reset))
            with m.Case(0x04 >> 2):
                m.d.comb += rdata.eq(Cat(xpower_done, fft_done))
            with m.Case(0x08 >> 2):
                m.d.comb += rdata.eq(xpmax[:32])
            with m.Case(0x0C >> 2):
                m.d.comb += rdata.eq(xpmax[32:])
            with m.Case(0x10 >> 2):
                m.d.comb += rdata.eq(blkexp)
            with m.Case(0x14 >> 2):
                m.d.comb += rdata.eq(ID_MAGIC)
        with m.FSM(name="rd"):
            with m.State("AR"):
                m.d.comb += ax.arready.eq(1)
                with m.If(ax.arvalid):
                    m.d.sync += raddr.eq(ax.araddr)
                    m.next = "R"
            with m.State("R"):
                m.d.comb += [ax.rvalid.eq(1), ax.rdata.eq(rdata)]
                with m.If(ax.rready):
                    m.next = "AR"
        return m


class PhaseCorrelatorTop(wiring.Component):
    """The packaged PL IP: AXI-Lite CSR + AXIS-native (TLAST-only) data ports.

    Wraps PhaseCorrelatorPL with the CSR and a `FirstGen` on each input stream, so
    the IP boundary speaks plain AXI-DMA AXIS (no FIRST) -- the block-design
    wrapper is then a pure rename. Output streams drop FIRST (DMA needs only LAST).
    """

    _INPUTS = ("window_sample", "window_coef", "fft_in",
               "xpower_f", "xpower_g", "rescale_r")
    _OUTPUTS = ("window_out", "fft_out", "xpower_r", "rescale_p")

    def __init__(self, n: int = 256, mant_bits: int = 18, input_bits: int = 12,
                 window_bits: int = 12, phase_width: int = 16,
                 cordic: CordicParams | None = None, core=None):
        self._pl = PhaseCorrelatorPL(n=n, mant_bits=mant_bits,
                                     input_bits=input_bits, window_bits=window_bits,
                                     phase_width=phase_width, cordic=cordic,
                                     core=core)
        self._csr = PhaseCorrelatorCsr(
            sh_bits=(self._pl._rescale.max_sh).bit_length(),
            max_bits=self._pl._cross.in_bits, blk_bits=16)
        members = self._pl.signature.members
        sig = {"s_axil": In(AXILite()),
               "o_dpath_reset": Out(1)}
        for name in self._INPUTS:
            sig[name] = In(AXIStream(members[name].signature.payload_shape))
        for name in self._OUTPUTS:
            sig[name] = Out(AXIStream(members[name].signature.payload_shape))
        super().__init__(sig)

    def elaborate(self, platform):
        m = Module()
        m.submodules.pl = pl = self._pl
        m.submodules.csr = csr = self._csr
        connect(m, flipped(self.s_axil), csr.s_axil)

        # The PS pulses CTRL.dpath_reset before each frame; the wrapper exposes it
        # to the BD, where a proc_sys_reset turns it into a SYNCHRONOUS reset of
        # the AXIS switches' data path, flushing the switch's stale-beat prefix.
        # It also drives fft_frame_sync which resets FftPass's row/o_cnt counters.
        # The FFT IP is NOT reset; direction reload is handled by inverse_last
        # comparison inside FftPass (see fft_pass.py).
        m.d.comb += self.o_dpath_reset.eq(csr.o_dpath_reset)

        members = self._pl.signature.members
        for name in self._INPUTS:                    # AXIS-native -> FIRST -> kernel
            fg = FirstGen(members[name].signature.payload_shape)
            m.submodules["fg_" + name] = fg
            connect(m, flipped(getattr(self, name)), fg.ext)
            connect(m, fg.int, getattr(pl, name))
        for name in self._OUTPUTS:                    # kernel -> AXIS-native (drop FIRST)
            ext, pls = getattr(self, name), getattr(pl, name)
            m.d.comb += [ext.valid.eq(pls.valid), pls.ready.eq(ext.ready),
                         ext.last.eq(pls.last), ext.payload.eq(pls.payload)]

        m.d.comb += [
            pl.fft_inverse.eq(csr.o_fft_inverse),
            pl.fft_frame_sync.eq(csr.o_dpath_reset),
            pl.rescale_sh.eq(csr.o_rescale_sh),
            csr.i_xpower_max.eq(pl.xpower_max),
            csr.i_xpower_max_valid.eq(pl.xpower_max_valid),
            csr.i_fft_blk_exp_sum.eq(pl.fft_blk_exp_sum),
            # fft frame done = last beat of an fft_out frame actually accepted
            csr.i_fft_done.eq(pl.fft_out.valid & pl.fft_out.last & self.fft_out.ready),
        ]
        return m
