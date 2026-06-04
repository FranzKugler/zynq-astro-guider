"""ModelBackend: PLBackend implemented with the golden fixed-point primitives.

This is the verification stand-in for the PL hardware. Each pass uses the exact
arithmetic the corresponding HDL kernel is cosim'd against, so
`estimate_shift_pl(.., ModelBackend(cfg))` reproduces
`guider_golden.fixed_point.estimate_shift` bit-exact -- any divergence is an
orchestration (sequencing/transpose/shift-handoff) bug, not arithmetic noise.

It deliberately uses the model's *float* phase-only (atan2/cos/sin), not the
CORDIC: the CORDIC approximation is the HDL's concern (already cosim'd in
guider_hdl), and keeping it out here makes the orchestration test bit-exact to
the model rather than tolerance-based.
"""
from __future__ import annotations

import numpy as np

from guider_golden.fixed_point import (
    FixedConfig, _fft1d_batch, _round_shift,
)

from .backend import PLBackend


class ModelBackend(PLBackend):
    def __init__(self, cfg: FixedConfig | None = None):
        self.cfg = cfg or FixedConfig()

    def window(self, samples, coefs):
        return _round_shift(samples.astype(np.int64) * coefs.astype(np.int64),
                            self.cfg.window_bits, self.cfg.rounding)

    def fft_pass(self, re, im, inverse):
        re, im, _exp = _fft1d_batch(np.asarray(re, np.int64),
                                    np.asarray(im, np.int64), self.cfg, inverse)
        return re, im

    def cross_power(self, f_re, f_im, g_re, g_im):
        f_re = f_re.astype(np.int64); f_im = f_im.astype(np.int64)
        g_re = g_re.astype(np.int64); g_im = g_im.astype(np.int64)
        r_re = f_re * g_re + f_im * g_im
        r_im = f_re * g_im - f_im * g_re
        block_max = int(max(np.abs(r_re).max(initial=0),
                            np.abs(r_im).max(initial=0)))
        return r_re, r_im, block_max

    def rescale_phase(self, r_re, r_im, sh):
        cfg = self.cfg
        limit = (1 << (cfg.mant_bits - 1)) - 1
        re = np.clip(_round_shift(r_re, sh, cfg.rounding), -limit - 1, limit)
        im = np.clip(_round_shift(r_im, sh, cfg.rounding), -limit - 1, limit)
        # phase-only normalization (float model spec; HDL uses the CORDIC)
        ang = np.arctan2(im.astype(np.float64), re.astype(np.float64))
        step = 2.0 * np.pi / (1 << cfg.cordic_bits)
        ang_q = np.round(ang / step) * step
        s = 1 << cfg.unit_bits
        zero_bin = (re == 0) & (im == 0)
        p_re = np.round(np.cos(ang_q) * s).astype(np.int64)
        p_im = np.round(np.sin(ang_q) * s).astype(np.int64)
        p_re[zero_bin] = 0
        p_im[zero_bin] = 0
        return p_re, p_im
