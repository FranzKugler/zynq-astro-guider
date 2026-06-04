"""Corner-turn: stream an N x N frame in row-major, read it back transposed.

This is the row/column reorder between the two 1-D FFT passes of the 2-D FFT
(the `.T` steps in guider_golden.fixed_point._fft2d). Pure permutation -- no
arithmetic -- so it is cosim'd bit-exact against a numpy transpose.

Ping-pong double buffer: frame k+1 is written while frame k is read out
transposed, sustaining one-sample-per-cycle throughput after a one-frame fill
latency. For a power-of-two N the transposed read address is just the row-major
address with its two halves swapped (col*N + row).

The payload is opaque (carry re|im concatenated); values pass through unchanged.
"""
from amaranth import *
from amaranth.lib.memory import Memory


class CornerTurn(Elaboratable):
    def __init__(self, n: int = 8, width: int = 32):
        self.logn = (n - 1).bit_length()
        assert (1 << self.logn) == n, "N must be a power of two"
        self.n = n
        self.width = width
        self.i_valid = Signal()
        self.i_data = Signal(width)
        self.o_valid = Signal()
        self.o_data = Signal(width)

    def elaborate(self, platform):
        m = Module()
        n, logn = self.n, self.logn
        depth = 2 * n * n

        m.submodules.mem = mem = Memory(shape=unsigned(self.width), depth=depth,
                                        init=[0] * depth)
        wport = mem.write_port()
        rport = mem.read_port()                 # synchronous, 1-cycle latency

        cnt = Signal(range(n * n))              # element index within a frame
        wr_buf = Signal()                       # buffer currently being written
        frame_seen = Signal()                   # a full frame has been buffered

        col = cnt[:logn]
        row = cnt[logn:2 * logn]
        rd_addr = Cat(row, col)                 # swap halves -> col*N + row

        m.d.comb += [
            wport.addr.eq(Cat(cnt, wr_buf)),
            wport.data.eq(self.i_data),
            wport.en.eq(self.i_valid),
            rport.addr.eq(Cat(rd_addr, ~wr_buf)),  # read the other buffer
            rport.en.eq(self.i_valid),
            self.o_data.eq(rport.data),
        ]
        # o_valid tracks the read through the memory's 1-cycle latency
        m.d.sync += self.o_valid.eq(frame_seen & self.i_valid)

        with m.If(self.i_valid):
            with m.If(cnt == n * n - 1):
                m.d.sync += [cnt.eq(0), wr_buf.eq(~wr_buf), frame_seen.eq(1)]
            with m.Else():
                m.d.sync += cnt.eq(cnt + 1)
        return m
