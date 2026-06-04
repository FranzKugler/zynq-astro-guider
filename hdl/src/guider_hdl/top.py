"""PL phase-correlation datapath -- the synthesizable top-level assembly.

Frames live in PS DDR3; the PL is the set of AXI-Stream compute kernels the PS
drives via AXI-DMA. This module instantiates them and exposes their stream
endpoints + control/status as the PL's external contract (the ports the Zynq
block design wires to the DMA channels). The one shared resource is the FFT IP:
a single `FftPass` endpoint is time-shared across every FFT/IFFT pass -- the PS
just streams different data through `fft_in`/`fft_out` at different times, with
`fft_inverse` selecting the direction.

PS-orchestrated pass schedule (each line = one DMA in -> compute -> DMA out;
the transpose between FFT passes is a column-major DMA read, not on-chip):

  pass                 endpoints used                         control / status
  -------------------  -------------------------------------  -------------------
  window (ref, img)    window_sample, window_coef -> window_out
  FFT rows  (x2)       fft_in -> fft_out                      fft_inverse=0
  FFT cols  (x2)       fft_in -> fft_out  (transposed read)   fft_inverse=0
  cross-power          xpower_f, xpower_g -> xpower_r         -> xpower_max(_valid)
  rescale + phase      rescale_r -> rescale_p                 rescale_sh (from max)
  IFFT rows/cols       fft_in -> fft_out  (transposed read)   fft_inverse=1
  peak                 (PS reads corr from DDR: argmax + parabolic subpixel)

Window and the FFT row pass are kept as separate DMA hops here (simplest, clear);
fusing window -> FFT to save one DDR round trip is a later optimization. Each
kernel is cosim'd against the golden model in its own test; the FFT transform is
xsim-verified (sim/fft_cosim.py) while `core` may be a behavioral FftStub for
pysim of the streaming assembly.
"""
from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, connect, flipped

from .stream import Stream, complex_layout
from .window import WindowStream
from .fft_pass import FftPass
from .phase_stage import CrossPower, RescalePhase
from .cordic_ref import CordicParams


class PhaseCorrelatorPL(wiring.Component):
    def __init__(self, n: int = 256, mant_bits: int = 18, input_bits: int = 12,
                 window_bits: int = 12, phase_width: int = 16,
                 cordic: CordicParams | None = None, core=None):
        self.n = n
        self.mant_bits = mant_bits
        cordic = cordic or CordicParams(mant_bits=mant_bits)
        # build the kernels first so the signature can use their derived widths
        self._win = WindowStream(sample_bits=input_bits, window_bits=window_bits)
        self._fft = FftPass(n=n, mant_bits=mant_bits, phase_width=phase_width,
                            core=core)
        self._cross = CrossPower(mant_bits=mant_bits)
        self._rescale = RescalePhase(mant_bits=mant_bits, p=cordic)
        xpow = self._cross.in_bits                       # 2*mant_bits + 1
        super().__init__({
            # --- window kernel ---
            "window_sample":    In(Stream(signed(input_bits))),
            "window_coef":      In(Stream(unsigned(window_bits + 1))),
            "window_out":       Out(Stream(signed(self._win.result_bits))),
            # --- shared FFT pass ---
            "fft_in":           In(Stream(complex_layout(mant_bits))),
            "fft_out":          Out(Stream(complex_layout(mant_bits))),
            "fft_inverse":      In(1),
            "fft_blk_exp_sum":  Out(unsigned(16)),
            # --- cross-power (pass 1) ---
            "xpower_f":         In(Stream(complex_layout(mant_bits))),
            "xpower_g":         In(Stream(complex_layout(mant_bits))),
            "xpower_r":         Out(Stream(complex_layout(xpow))),
            "xpower_max":       Out(unsigned(xpow)),
            "xpower_max_valid": Out(1),
            # --- rescale + phase-only (pass 2) ---
            "rescale_r":        In(Stream(complex_layout(xpow))),
            "rescale_p":        Out(Stream(complex_layout(cordic.unit_bits + 2))),
            "rescale_sh":       In(range(self._rescale.max_sh + 1)),
        })

    def elaborate(self, platform):
        m = Module()
        m.submodules.win = win = self._win
        m.submodules.fft = fft = self._fft
        m.submodules.cross = cross = self._cross
        m.submodules.rescale = rescale = self._rescale

        # forward each external endpoint to its kernel (flipped == inside view)
        connect(m, flipped(self.window_sample), win.sample)
        connect(m, flipped(self.window_coef), win.coef)
        connect(m, win.out, flipped(self.window_out))

        connect(m, flipped(self.fft_in), fft.inp)
        connect(m, fft.out, flipped(self.fft_out))

        connect(m, flipped(self.xpower_f), cross.f)
        connect(m, flipped(self.xpower_g), cross.g)
        connect(m, cross.r, flipped(self.xpower_r))

        connect(m, flipped(self.rescale_r), rescale.r)
        connect(m, rescale.p, flipped(self.rescale_p))

        m.d.comb += [
            fft.inverse.eq(self.fft_inverse),
            self.fft_blk_exp_sum.eq(fft.o_blk_exp_sum),
            rescale.sh.eq(self.rescale_sh),
            self.xpower_max.eq(cross.o_max),
            self.xpower_max_valid.eq(cross.o_max_valid),
        ]
        return m
