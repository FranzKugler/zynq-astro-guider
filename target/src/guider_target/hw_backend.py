"""On-board PLBackend over real AXI-DMA -- scaffold (needs board + bitstream).

Maps the pass schedule onto the Zynq's PS<->PL path for PhaseCorrelatorPL:

  DDR buffers   ikwzm **udmabuf** (/dev/udmabufN): physically-contiguous,
                cache-coherent regions the PL DMAs touch; mmap'd here as numpy
                views. One buffer per live frame (samples, F, G, R, P, corr).
  DMA           AXI-DMA / AXIS in the PL fabric, one MM2S+S2MM pair per kernel
                endpoint (window_*, fft_in/out, xpower_*, rescale_*). Programmed
                by poking the DMA control/address/length registers (simple
                register-mode transfers; a real build may use scatter-gather).
  control/status uio (/dev/uioN) mmap of the kernel control regs:
                fft_inverse, rescale_sh (write) and xpower_max / xpower_max_valid,
                fft_blk_exp_sum (read). One FFT IP, time-shared: set fft_inverse,
                stream a frame through fft_in/out, repeat per pass.

A pass = configure regs -> kick MM2S (DDR->PL) and S2MM (PL->DDR) -> wait done.
The transpose between FFT passes is an MM2S with a column-major descriptor (or a
strided copy) into a fresh udmabuf, mirroring orchestrator._fft2's `.T`.

This module intentionally stops at the register pokes: it needs the integrated
block design + bitstream (not yet built -- the salvaged BOOT.bin carries only the
PS-bring-up image) and the board. The structure is real so wiring it up later is
filling in the marked spots, verified against ModelBackend by identical results.
"""
from __future__ import annotations

import numpy as np

from guider_golden.fixed_point import FixedConfig

from .backend import PLBackend

_NEEDS_BOARD = (
    "UioBackend needs the on-board AXI-DMA + the integrated PhaseCorrelatorPL "
    "bitstream; only ModelBackend runs off the board. See module docstring."
)


class UioBackend(PLBackend):
    def __init__(self, cfg: FixedConfig | None = None, *,
                 uio: str = "/dev/uio0", udmabuf_glob: str = "/dev/udmabuf*"):
        self.cfg = cfg or FixedConfig()
        self.uio = uio
        self.udmabuf_glob = udmabuf_glob
        # TODO(board): open `uio` (mmap control/status regs) and discover the
        # udmabuf regions; cache their physical addresses for the DMA descriptors.
        raise NotImplementedError(_NEEDS_BOARD)

    # --- one pass each: DMA the frame through the kernel, return the result ---
    def window(self, samples: np.ndarray, coefs: np.ndarray) -> np.ndarray:
        # TODO(board): DMA samples + coefs into window_sample/window_coef, kick,
        # DMA window_out back. raise NotImplementedError(_NEEDS_BOARD)
        raise NotImplementedError(_NEEDS_BOARD)

    def fft_pass(self, re, im, inverse):
        # TODO(board): write fft_inverse; DMA (re,im) through fft_in -> fft_out
        # (one 1-D pass, tlast per row handled in the PL); read fft_blk_exp_sum.
        raise NotImplementedError(_NEEDS_BOARD)

    def cross_power(self, f_re, f_im, g_re, g_im):
        # TODO(board): DMA F,G through xpower_f/g -> xpower_r; read xpower_max
        # once xpower_max_valid; return (R_re, R_im, xpower_max).
        raise NotImplementedError(_NEEDS_BOARD)

    def rescale_phase(self, r_re, r_im, sh):
        # TODO(board): write rescale_sh; DMA R through rescale_r -> rescale_p.
        raise NotImplementedError(_NEEDS_BOARD)
