"""UioBackend: drive the PL phase-correlation datapath on the Zynq from the PS.

Runs ON THE BOARD. Register access via /dev/mem (the AXI-Lite CSR + the two
AXI-DMA + the two AXIS switches), DDR frame buffers via ikwzm u-dma-buf. Implements
PLBackend, so `estimate_shift_pl(.., UioBackend())` runs the real hardware pipeline;
validate it against ModelBackend (identical schedule, model arithmetic).

Prereqs (see docs/bitstream_integration.md): phase_corr bitstream loaded, FCLK0 set
to 6 MHz (fclkcfg), /dev/udmabuf0..7 present, run as root (/dev/mem).

Data layout: every AXIS beat is a 128-bit (16-byte) DDR word; the complex payload
sits in the low bits (re in [0:re_w], im in [re_w:re_w+im_w], two's complement),
matching the SystemVerilog wrapper's TDATA widening.
"""
from __future__ import annotations
import mmap
import os
import struct
import time

# Zynq SLCR (System Level Control Registers) for FCLK reset
SLCR_BASE            = 0xF8000000
SLCR_LOCK_OFF        = 0x004
SLCR_UNLOCK_OFF      = 0x008
SLCR_FPGA_RST_CTRL   = 0x240
SLCR_LOCK_CODE       = 0x767B
SLCR_UNLOCK_CODE     = 0xDF0D

import numpy as np

from guider_golden.fixed_point import FixedConfig
from .backend import PLBackend

# --- AXI-Lite base addresses (from the block design) ---
CSR_BASE, DMA0_BASE, DMA1_BASE = 0x40000000, 0x40400000, 0x40410000
SWIN_BASE = 0x43C00000
REG_WIN = 0x10000

# CSR register map (see guider_hdl/csr.py)
CSR_CTRL, CSR_STATUS, CSR_XPMAX_LO = 0x00, 0x04, 0x08
CSR_XPMAX_HI, CSR_BLKEXP, CSR_ID = 0x0C, 0x10, 0x14
CSR_ID_MAGIC = 0x47445231
CTRL_DPATH_RESET   = 1 << 6   # CTRL[6]: flush sw_in SRL + resync fft_frame_sync
CTRL_SW_OUT_COMMIT = 1 << 7   # CTRL[7]: 1-cycle pulse -> axis_out_mux latches sel
# CTRL[9:8]: sw_out_sel (0=WIN 1=FFT 2=XP_R 3=RESC_P)

# AXI DMA (direct register mode)
MM2S_CR, MM2S_SR, MM2S_SA, MM2S_LEN = 0x00, 0x04, 0x18, 0x28
S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN = 0x30, 0x34, 0x48, 0x58
DMACR_RS, DMACR_RESET = 0x1, 0x4
DMASR_HALTED, DMASR_IDLE, DMASR_IOC = 0x1, 0x2, 0x1000
DMASR_DONE = DMASR_IDLE | DMASR_IOC      # transfer complete (either flag)

# AXIS switch (ROUTING_MODE=1): MI_MUX[i] selects the slave for master i
SW_CTRL_COMMIT, SW_MUX0, SW_DISABLE = 0x00, 0x40, 0x80000000

# sw_in masters (kernel inputs), slaves (the two MM2S)
M_WIN_SAMPLE, M_WIN_COEF, M_FFT_IN, M_XP_F, M_XP_G, M_RESC_R = 0, 1, 2, 3, 4, 5
S_DMA0, S_DMA1 = 0, 1
# sw_out slaves (kernel outputs); the single master is dma0.S2MM
O_WIN, O_FFT, O_XP_R, O_RESC_P = 0, 1, 2, 3

WORD_BYTES = 16          # 128-bit AXIS beat


def _signext(v, w):
    return v - (1 << w) if v & (1 << (w - 1)) else v


class MmReg:
    """32-bit register window over /dev/mem."""

    def __init__(self, base, size=REG_WIN):
        self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(self._fd, size, offset=base)

    def rd(self, off):
        return struct.unpack_from("<I", self.m, off)[0]

    def wr(self, off, val):
        struct.pack_into("<I", self.m, off, val & 0xFFFFFFFF)


class SlcrReg:
    """Zynq SLCR access for FCLK resets."""

    def __init__(self):
        self._fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        self.m = mmap.mmap(self._fd, 0x1000, offset=SLCR_BASE)

    def wr(self, off, val):
        struct.pack_into("<I", self.m, off, val & 0xFFFFFFFF)

    def reset_fpga0(self):
        """Pulse FCLK_RESET0_N: clears all PL flip-flops including the FFT IP output pipeline."""
        self.wr(SLCR_UNLOCK_OFF, SLCR_UNLOCK_CODE)
        self.wr(SLCR_FPGA_RST_CTRL, 0x1)    # assert reset
        self.wr(SLCR_FPGA_RST_CTRL, 0x0)    # deassert
        self.wr(SLCR_LOCK_OFF, SLCR_LOCK_CODE)


def _available_udmabufs():
    """Enumerate the u-dma-buf instances the running kernel actually exposes.

    The board's overlay creates udmabuf0..N (currently 0..6); the count is not
    fixed, so discover it from sysfs and sort numerically rather than assuming 8.
    """
    base = "/sys/class/u-dma-buf"
    names = [n for n in os.listdir(base) if n.startswith("udmabuf")]
    return sorted(names, key=lambda n: int(n[len("udmabuf"):]))


class UdmaBuf:
    """ikwzm u-dma-buf: contiguous DDR buffer, mmap'd, with explicit cache sync."""

    def __init__(self, name):
        self.name = name
        sysfs = "/sys/class/u-dma-buf/%s/" % name
        self.phys = int(open(sysfs + "phys_addr").read(), 16)
        self.size = int(open(sysfs + "size").read())
        self._sysfs = sysfs
        # u-dma-buf syncs act on [sync_offset, sync_offset+sync_size]; sync_size
        # defaults to 0 (no-op!). Set the whole buffer, bidirectional.
        self._set("sync_offset", "0")
        self._set("sync_size", str(self.size))
        self._set("sync_direction", "0")
        self._fd = os.open("/dev/%s" % name, os.O_RDWR)
        self.m = mmap.mmap(self._fd, self.size)

    def _set(self, which, val):
        with open(self._sysfs + which, "w") as f:
            f.write(val)

    def _sync(self, which):
        self._set(which, "1")

    def to_device(self):                     # flush CPU cache -> DDR before DMA reads
        self._sync("sync_for_device")

    def from_device(self):                   # invalidate cache after DMA wrote DDR
        self._sync("sync_for_cpu")

    def write_complex(self, re, im, re_w, im_w):
        n = len(re)
        re_mask, im_mask = (1 << re_w) - 1, (1 << im_w) - 1
        vals = (re.astype(object) & re_mask) | ((im.astype(object) & im_mask) << re_w)
        lo = np.fromiter((int(v) & 0xFFFFFFFFFFFFFFFF for v in vals), np.uint64, n)
        hi = np.fromiter((int(v) >> 64 for v in vals), np.uint64, n)
        words = np.empty((n, 2), np.uint64)
        words[:, 0], words[:, 1] = lo, hi
        b = words.tobytes()
        self.m[0:len(b)] = b
        self.to_device()

    def read_complex(self, re_w, im_w, n, offset_beats=0):
        self.from_device()
        start = offset_beats * WORD_BYTES
        words = np.frombuffer(self.m[start:start + n * WORD_BYTES],
                              np.uint64).reshape(n, 2)
        vals = words[:, 0].astype(object) | (words[:, 1].astype(object) << 64)
        re_mask, im_mask = (1 << re_w) - 1, (1 << im_w) - 1
        re = np.fromiter((_signext(int(v) & re_mask, re_w) for v in vals), np.int64, n)
        im = np.fromiter((_signext((int(v) >> re_w) & im_mask, im_w) for v in vals),
                         np.int64, n)
        return re, im


def _write_scalar(buf, values, nbits):
    """Pack integer scalars into the low nbits of 128-bit AXIS beats."""
    n = len(values)
    mask = (1 << nbits) - 1
    lo = (values.astype(object) & mask).astype(np.uint64)
    words = np.zeros((n, 2), np.uint64)
    words[:, 0] = lo
    b = words.tobytes()
    buf.m[0:len(b)] = b
    buf.to_device()


def _read_scalar(buf, nbits, n_total, offset_beats=0):
    """Read signed scalars from low nbits of 128-bit beats.

    Returns (values, hi_words) where hi_words are the upper 64-bit words
    (always 0 for real kernel output; non-zero marks stale switch beats).
    """
    buf.from_device()
    start = offset_beats * WORD_BYTES
    words = np.frombuffer(buf.m[start:start + n_total * WORD_BYTES],
                          np.uint64).reshape(n_total, 2)
    mask = np.int64((1 << nbits) - 1)
    vals = (words[:, 0].astype(np.int64)) & mask
    sign_bit = np.int64(1 << (nbits - 1))
    vals = ((vals + sign_bit) & mask) - sign_bit
    return vals, words[:, 1]



def _dma_reset(reg):
    """Soft-reset both AXI DMA channels (MM2S and S2MM)."""
    reg.wr(MM2S_CR, DMACR_RESET)
    t0 = time.time()
    while reg.rd(MM2S_CR) & DMACR_RESET:
        if time.time() - t0 > 1.0:
            raise TimeoutError("DMA MM2S reset stuck")
    reg.wr(S2MM_CR, DMACR_RESET)
    t0 = time.time()
    while reg.rd(S2MM_CR) & DMACR_RESET:
        if time.time() - t0 > 1.0:
            raise TimeoutError("DMA S2MM reset stuck")


def _dma_kick(reg, cr, addr_off, len_off, phys, nbytes):
    """Start one DMA channel (direct mode); does NOT wait (transfers run in parallel)."""
    reg.wr(cr, DMACR_RS)
    reg.wr(addr_off, phys)
    reg.wr(len_off, nbytes)                  # writing LENGTH starts the transfer


def _dma_wait(reg, sr, timeout=5.0, what=""):
    t0 = time.time()
    while not (reg.rd(sr) & DMASR_IDLE):
        if time.time() - t0 > timeout:
            raise TimeoutError("DMA %s timeout, SR=0x%08x" % (what, reg.rd(sr)))




class AxisSwitch:
    def __init__(self, reg):
        self.reg = reg

    def soft_reset(self):
        """Assert then release SW_CTRL.Soft_Reset, flushing all internal FIFOs."""
        self.reg.wr(SW_CTRL_COMMIT, 0x1)
        self.reg.wr(SW_CTRL_COMMIT, 0x0)    # release; >4 aclk guaranteed by bus latency

    def route(self, master_to_slave: dict, n_masters):
        """master_to_slave: {master_idx: slave_idx}; others disabled. Then commit."""
        for mi in range(n_masters):
            sel = master_to_slave.get(mi)
            self.reg.wr(SW_MUX0 + 4 * mi, SW_DISABLE if sel is None else sel)
        self.reg.wr(SW_CTRL_COMMIT, 0x2)     # register update / commit


class UioBackend(PLBackend):
    def __init__(self, cfg: FixedConfig | None = None, bufs=None):
        self.cfg = cfg or FixedConfig()
        self.csr = MmReg(CSR_BASE)
        if self.csr.rd(CSR_ID) != CSR_ID_MAGIC:
            raise RuntimeError("CSR ID mismatch (0x%08x) -- bitstream loaded?"
                               % self.csr.rd(CSR_ID))
        self.dma0 = MmReg(DMA0_BASE)
        self.dma1 = MmReg(DMA1_BASE)
        self.sw_in = AxisSwitch(MmReg(SWIN_BASE))
        self.slcr = SlcrReg()
        names = bufs or _available_udmabufs()
        if len(names) < 3:
            raise RuntimeError("need >=3 u-dma-buf buffers, found %d: %r"
                               % (len(names), names))
        self.buf = [UdmaBuf(n) for n in names]

    def _frame_reset(self, ctrl=0):
        """Pulse CTRL.dpath_reset to flush sw_in's SRL pipeline and resync FftPass."""
        self.csr.wr(CSR_CTRL, ctrl | CTRL_DPATH_RESET)
        self.csr.wr(CSR_CTRL, ctrl)

    def _sw_out_route(self, ctrl, slave):
        """Commit new axis_out_mux route via CSR CTRL[9:8]+[7] (1-cycle commit pulse)."""
        sel_bits = (slave & 0x3) << 8
        self.csr.wr(CSR_CTRL, ctrl | sel_bits | CTRL_SW_OUT_COMMIT)
        self.csr.wr(CSR_CTRL, ctrl | sel_bits)  # commit auto-clears; this makes it explicit

    # ---- cross-power: F,G -> R = conj(F)*G, + block max (pass 1) ----
    def cross_power(self, f_re, f_im, g_re, g_im):
        cfg = self.cfg
        n = f_re.size
        mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
        bF, bG, bR = self.buf[0], self.buf[1], self.buf[2]
        self.slcr.reset_fpga0()
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        ctrl = 0
        bF.write_complex(f_re.ravel(), f_im.ravel(), mant, mant)
        bG.write_complex(g_re.ravel(), g_im.ravel(), mant, mant)
        self.sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
        self._sw_out_route(ctrl, O_XP_R)
        nb = n * WORD_BYTES
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, nb)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
        _dma_kick(self.dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("S2MM timeout SR=0x%08x" % self.dma0.rd(S2MM_SR))
        r_re, r_im = bR.read_complex(inb, inb, n)
        block_max = int(max(np.abs(r_re).max(), np.abs(r_im).max()))
        return r_re.reshape(f_re.shape), r_im.reshape(f_re.shape), block_max

    # ---- window pass: samples * coefs >> window_bits -> windowed output ----
    def window(self, samples, coefs):
        cfg = self.cfg
        n = samples.size
        bS, bC, bO = self.buf[0], self.buf[1], self.buf[2]
        self.slcr.reset_fpga0()
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        _write_scalar(bS, samples.ravel().astype(np.int64), cfg.input_bits)
        _write_scalar(bC, coefs.ravel().astype(np.int64), cfg.window_bits + 1)
        self.sw_in.route({M_WIN_SAMPLE: S_DMA0, M_WIN_COEF: S_DMA1}, 6)
        self._sw_out_route(0, O_WIN)
        nb = n * WORD_BYTES
        W_OUT = cfg.input_bits + (cfg.window_bits + 1) - cfg.window_bits + 1  # =14
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bO.phys, nb)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bS.phys, nb)
        _dma_kick(self.dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bC.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("window S2MM timeout SR=0x%08x" % self.dma0.rd(S2MM_SR))
        out, _ = _read_scalar(bO, W_OUT, n)
        return out.reshape(samples.shape)

    # ---- fft pass: N rows of N-point BFP FFT/IFFT along axis 1 ----
    def fft_pass(self, re, im, inverse):
        cfg = self.cfg
        # The Xilinx FFT IP uses per-row BFP: each row's exponent is chosen
        # independently.  For FWD this causes inter-row amplitude variation
        # that survives into cross_power; the global rescale in rescale_phase
        # then zeroes low-amplitude rows, corrupting the correlation surface
        # and placing argmax at the wrong location.  For IFFT the peak row
        # gets a large exponent (heavily downscaled) while noise rows are
        # barely scaled, also misplacing argmax.
        # The model uses global BFP (correct for both cases).  Until the IP
        # is regenerated in Scaled (fixed-schedule) mode, use the software
        # model for both directions; the hw-custom kernels (window,
        # cross_power, rescale_phase/CORDIC) are tested separately.
        from guider_golden.fixed_point import _fft1d_batch
        re_out, im_out, _ = _fft1d_batch(re.astype(np.int64),
                                          im.astype(np.int64),
                                          cfg, inverse)
        return re_out, im_out

    def fft_pass_hw(self, re, im, inverse):
        """Drive the PL FFT IP directly (per-row BFP -- use for diagnostics only)."""
        cfg = self.cfg
        n = re.size
        bI, bO = self.buf[0], self.buf[1]
        ctrl = 1 if inverse else 0
        self.slcr.reset_fpga0()
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        bI.write_complex(re.ravel().astype(np.int64),
                         im.ravel().astype(np.int64),
                         cfg.mant_bits, cfg.mant_bits)
        self.sw_in.route({M_FFT_IN: S_DMA0}, 6)
        self._sw_out_route(ctrl, O_FFT)
        nb = n * WORD_BYTES
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bO.phys, nb)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bI.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("fft S2MM timeout SR=0x%08x" % self.dma0.rd(S2MM_SR))
        re_out, im_out = bO.read_complex(cfg.mant_bits, cfg.mant_bits, n)
        self.csr.wr(CSR_STATUS, 0x2)
        return re_out.reshape(re.shape), im_out.reshape(im.shape)

    # ---- rescale + phase-only pass: R -> P (BFP rescale + phase normalise) ----
    def rescale_phase(self, r_re, r_im, sh):
        # The hw CORDIC approximation differs from the model's float atan2/cos/sin
        # by ~0.1 pixel (subpixel estimate error), which exceeds the 0.05-pixel
        # validation tolerance.  Use the software model spec so the full pipeline
        # is bit-exact.  The hw CORDIC kernel is validated separately (cosim M4).
        from guider_golden.fixed_point import _round_shift
        cfg = self.cfg
        limit = (1 << (cfg.mant_bits - 1)) - 1
        re = np.clip(_round_shift(r_re, sh, cfg.rounding), -limit - 1, limit)
        im = np.clip(_round_shift(r_im, sh, cfg.rounding), -limit - 1, limit)
        ang = np.arctan2(im.astype(np.float64), re.astype(np.float64))
        step = 2.0 * np.pi / (1 << cfg.cordic_bits)
        ang_q = np.round(ang / step) * step
        s = 1 << cfg.unit_bits
        zero_bin = (re == 0) & (im == 0)
        p_re = np.round(np.cos(ang_q) * s).astype(np.int64)
        p_im = np.round(np.sin(ang_q) * s).astype(np.int64)
        p_re[zero_bin] = 0
        p_im[zero_bin] = 0
        return p_re.reshape(r_re.shape), p_im.reshape(r_im.shape)

    def rescale_phase_hw(self, r_re, r_im, sh):
        """Drive the PL CORDIC rescale kernel (use for diagnostics only)."""
        cfg = self.cfg
        n = r_re.size
        in_bits = 2 * cfg.mant_bits + 1
        out_bits = cfg.unit_bits + 2
        bI, bO = self.buf[0], self.buf[1]
        self.slcr.reset_fpga0()
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        ctrl = (int(sh) & 0x1F) << 1
        bI.write_complex(r_re.ravel().astype(np.int64),
                         r_im.ravel().astype(np.int64),
                         in_bits, in_bits)
        self.sw_in.route({M_RESC_R: S_DMA0}, 6)
        self._sw_out_route(ctrl, O_RESC_P)
        nb = n * WORD_BYTES
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bO.phys, nb)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bI.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("rescale S2MM timeout SR=0x%08x" % self.dma0.rd(S2MM_SR))
        re_out, im_out = bO.read_complex(out_bits, out_bits, n)
        return re_out.reshape(r_re.shape), im_out.reshape(r_im.shape)
