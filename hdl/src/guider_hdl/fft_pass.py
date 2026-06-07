"""One 1-D FFT pass over an N x N frame, via the time-shared FFT IP.

The 2-D FFT is two of these with a DMA transpose between them (the column pass is
this same kernel fed column-major data), so the on-chip corner-turn is not needed
at whole-field sizes -- see hdl/README.md "Top-level (DDR-streaming)".

This kernel owns the framing the FFT IP needs:
  * config   -- load the transform direction once (held), before any data;
  * tlast    -- assert at each N-point row boundary on the input stream;
  * output   -- reframe the IP's natural-order output as an N x N frame stream
                (FIRST on the first bin, LAST on the last);
  * blk_exp  -- sum the per-row BFP block exponents (m_axis TUSER) over the frame.
                The model discards the exponent (the peak is argmax + a scale-free
                ratio), but a requantizing design can use it; exposed best-effort.

`core` is the FFT instance: the synthesizable FftIP by default, or a behavioral
FftStub for pysim of this framing logic. The transform values themselves are
verified against the real IP in Vivado xsim (sim/fft_cosim.py); pysim here only
exercises the streaming/handshake glue.
"""
from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

from .stream import Stream, complex_layout
from .fft_ip import FftIP


class FftPass(wiring.Component):
    def __init__(self, n: int = 256, mant_bits: int = 18, phase_width: int = 16,
                 core=None):
        self.n = n
        self.mant_bits = mant_bits
        self.core = core or FftIP(n=n, input_width=mant_bits,
                                  phase_width=phase_width)
        super().__init__({
            "inp":           In(Stream(complex_layout(mant_bits))),
            "out":           Out(Stream(complex_layout(mant_bits))),
            "inverse":       In(1),
            # pulse before each frame: reload the IP config (so fft_inverse can
            # change between forward and inverse frames) and resync the row/output
            # framing counters. Does NOT reset the FFT IP itself.
            "frame_sync":    In(1),
            "o_blk_exp_sum": Out(unsigned(16)),
        })

    def elaborate(self, platform):
        m = Module()
        m.submodules.core = core = self.core
        n, inp, out = self.n, self.inp, self.out

        # --- config: send the direction word once per direction change.
        # `configured` gates data; `inverse_last` tracks what direction the IP
        # was last given so we resend only when fft_inverse actually changes.
        # We do NOT reset configured inside frame_sync: the FFT IP deasserts
        # s_cfg_tready after accepting the config word and only reasserts it
        # after the full frame output.  Clearing configured while s_cfg_tready
        # is 0 causes a permanent deadlock (configured can never become 1).
        configured = Signal()
        inverse_last = Signal()
        m.d.comb += [
            core.s_cfg_tdata.eq(Cat(~self.inverse, C(0, 7))),
            core.s_cfg_tvalid.eq(~configured),
        ]
        # Direction changed since the last accepted config -> force reload.
        # Fires on the cycle inverse changes; the PS only changes inverse between
        # frames (after the previous DMA's IOC), so s_cfg_tready is 1 by then.
        with m.If(self.inverse != inverse_last):
            m.d.sync += configured.eq(0)
        # Handshake: record the direction that was accepted (last-wins in Amaranth,
        # so this overrides the direction-change clear on the same cycle).
        with m.If(core.s_cfg_tvalid & core.s_cfg_tready):
            m.d.sync += [configured.eq(1), inverse_last.eq(self.inverse)]

        # --- input: gate on configured; assert tlast every N samples (row end) ---
        row = Signal(range(n))
        s_tlast = row == n - 1
        m.d.comb += [
            core.s_re.eq(inp.payload.re), core.s_im.eq(inp.payload.im),
            core.s_tvalid.eq(inp.valid & configured),
            core.s_tlast.eq(s_tlast),
            inp.ready.eq(core.s_tready & configured),
        ]
        with m.If(inp.valid & inp.ready):
            m.d.sync += row.eq(Mux(s_tlast, 0, row + 1))

        # --- output: reframe natural-order bins as an N x N frame stream ---
        o_cnt = Signal(range(n * n))
        o_last = o_cnt == n * n - 1
        m.d.comb += [
            out.payload.re.eq(core.m_re), out.payload.im.eq(core.m_im),
            out.valid.eq(core.m_tvalid),
            core.m_tready.eq(out.ready),
            out.first.eq(o_cnt == 0),
            out.last.eq(o_last),
        ]
        # exp_sum registers the exponents of fully-completed rows; the exposed
        # sum folds in the current row's end combinationally so it is correct
        # *at* out.last (where the last row's TUSER arrives on the same beat).
        exp_sum = Signal(16)
        fire = out.valid & out.ready
        row_end = fire & core.m_tlast
        with m.If(fire):
            m.d.sync += o_cnt.eq(Mux(o_last, 0, o_cnt + 1))
            with m.If(out.first):
                m.d.sync += exp_sum.eq(0)
            with m.Elif(core.m_tlast):
                m.d.sync += exp_sum.eq(exp_sum + core.m_blk_exp)
        m.d.comb += self.o_blk_exp_sum.eq(
            exp_sum + Mux(row_end, core.m_blk_exp, 0))

        # frame_sync (PS pulse before each frame): realign the framing counters.
        # Does NOT clear `configured` -- the FFT IP deasserts s_cfg_tready after
        # accepting a config word and only reasserts after a full frame completes;
        # clearing configured while s_cfg_tready=0 creates a permanent deadlock.
        # Direction reloads are handled via the inverse_last comparison above.
        with m.If(self.frame_sync):
            m.d.sync += [row.eq(0), o_cnt.eq(0)]
        return m
