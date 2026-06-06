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

import numpy as np

from guider_golden.fixed_point import FixedConfig
from .backend import PLBackend

# --- AXI-Lite base addresses (from the block design) ---
CSR_BASE, DMA0_BASE, DMA1_BASE = 0x40000000, 0x40400000, 0x40410000
SWIN_BASE, SWOUT_BASE = 0x43C00000, 0x43C10000
REG_WIN = 0x10000

# CSR register map (see guider_hdl/csr.py)
CSR_CTRL, CSR_STATUS, CSR_XPMAX_LO = 0x00, 0x04, 0x08
CSR_XPMAX_HI, CSR_BLKEXP, CSR_ID = 0x0C, 0x10, 0x14
CSR_ID_MAGIC = 0x47445231

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


def _raw_hi(buf, n_total, offset_beats=0):
    """Return (lo_words, hi_words) of raw 64-bit halves for stale detection."""
    buf.from_device()
    start = offset_beats * WORD_BYTES
    words = np.frombuffer(buf.m[start:start + n_total * WORD_BYTES],
                          np.uint64).reshape(n_total, 2)
    return words[:, 0], words[:, 1]


def _stale_skip(hi_words, overhead):
    """Return offset past stale prefix beats (upper 64 bits non-zero = stale).

    Real kernel output always has TDATA[127:W] = 0 (zeroed by AXI wrapper),
    so hi_words == 0 for valid beats.  Stale beats from the switch SRL init
    state have non-zero upper bits.  Fallback: if no stale detected (off==0)
    just use offset 0 (no prefix to skip).
    """
    off = 0
    while off < overhead and hi_words[off] != 0:
        off += 1
    return off


def _dma_reset(reg):
    """Soft-reset the whole AXI DMA core (clears stale state incl. sticky IOC)."""
    reg.wr(MM2S_CR, DMACR_RESET)
    t0 = time.time()
    while reg.rd(MM2S_CR) & DMACR_RESET:
        if time.time() - t0 > 1.0:
            raise TimeoutError("DMA reset stuck")


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
        self.sw_out = AxisSwitch(MmReg(SWOUT_BASE))
        names = bufs or ["udmabuf%d" % i for i in range(8)]
        self.buf = [UdmaBuf(n) for n in names]

    # Constant value the AXI switch outputs during/after soft-reset (from the IP's
    # internal SRL initial state).  All stale prefix beats carry this value; the
    # first valid beat never equals it for any real cross-power input.
    _STALE_INIT = 7276303945

    # ---- cross-power: F,G -> R = conj(F)*G, + block max (pass 1) ----
    def cross_power(self, f_re, f_im, g_re, g_im):
        cfg = self.cfg
        n = f_re.size
        mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
        bF, bG, bR = self.buf[0], self.buf[1], self.buf[2]
        # Reset DMAs first to stop any re-armed MM2S, then soft-reset both switches
        # to flush their FIFOs (eliminates the stale-beat prefix from the
        # c_sg_length_width=26 re-arm and PL initialisation state).
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        self.sw_in.soft_reset(); self.sw_out.soft_reset()
        bF.write_complex(f_re.ravel(), f_im.ravel(), mant, mant)
        bG.write_complex(g_re.ravel(), g_im.ravel(), mant, mant)
        self.sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
        self.sw_out.route({0: O_XP_R}, 1)
        nb = n * WORD_BYTES
        # Over-allocate S2MM by OVERHEAD beats to absorb the variable stale prefix
        # (empirically 2 or 4 beats of _STALE_INIT from the switch).  After IOC we
        # scan past the stale prefix to find where valid data starts.
        # MM2S fires DMADecErr post-completion (RS sticky, c_sg_length_width=26);
        # that is harmless -- S2MM IOC is the true completion signal.
        OVERHEAD = 8
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys,
                  (n + OVERHEAD) * WORD_BYTES)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
        _dma_kick(self.dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("S2MM timeout SR=0x%08x" %
                                   self.dma0.rd(S2MM_SR))
        all_re, all_im = bR.read_complex(inb, inb, n + OVERHEAD, offset_beats=0)
        off = 0
        while off < OVERHEAD and int(all_re[off]) == self._STALE_INIT:
            off += 1
        r_re = all_re[off:off + n]
        r_im = all_im[off:off + n]
        block_max = self.csr.rd(CSR_XPMAX_LO) | (self.csr.rd(CSR_XPMAX_HI) << 32)
        return r_re.reshape(f_re.shape), r_im.reshape(f_re.shape), int(block_max)

    # ---- window pass: samples * coefs >> window_bits -> windowed output ----
    def window(self, samples, coefs):
        cfg = self.cfg
        n = samples.size
        bS, bC, bO = self.buf[0], self.buf[1], self.buf[2]
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        self.sw_in.soft_reset(); self.sw_out.soft_reset()
        _write_scalar(bS, samples.ravel().astype(np.int64), cfg.input_bits)
        _write_scalar(bC, coefs.ravel().astype(np.int64), cfg.window_bits + 1)
        self.sw_in.route({M_WIN_SAMPLE: S_DMA0, M_WIN_COEF: S_DMA1}, 6)
        self.sw_out.route({0: O_WIN}, 1)
        nb = n * WORD_BYTES
        W_OUT = cfg.input_bits + (cfg.window_bits + 1) - cfg.window_bits + 1  # =14
        OVERHEAD = 8
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bO.phys,
                  (n + OVERHEAD) * WORD_BYTES)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bS.phys, nb)
        _dma_kick(self.dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bC.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("window S2MM timeout SR=0x%08x" %
                                   self.dma0.rd(S2MM_SR))
        out, hi = _read_scalar(bO, W_OUT, n + OVERHEAD)
        off = _stale_skip(hi, OVERHEAD)   # upper 64 bits are 0 for real data
        return out[off:off + n].reshape(samples.shape)

    # ---- fft pass: N rows of N-point BFP FFT/IFFT along axis 1 ----
    def fft_pass(self, re, im, inverse):
        cfg = self.cfg
        n = re.size
        bI, bO = self.buf[0], self.buf[1]
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        self.sw_in.soft_reset(); self.sw_out.soft_reset()
        ctrl = (1 if inverse else 0)
        self.csr.wr(CSR_CTRL, ctrl)
        bI.write_complex(re.ravel().astype(np.int64),
                         im.ravel().astype(np.int64),
                         cfg.mant_bits, cfg.mant_bits)
        self.sw_in.route({M_FFT_IN: S_DMA0}, 6)
        self.sw_out.route({0: O_FFT}, 1)
        nb = n * WORD_BYTES
        OVERHEAD = 8
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bO.phys,
                  (n + OVERHEAD) * WORD_BYTES)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bI.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("fft S2MM timeout SR=0x%08x" %
                                   self.dma0.rd(S2MM_SR))
        re_raw, im_raw = bO.read_complex(cfg.mant_bits, cfg.mant_bits,
                                         n + OVERHEAD, offset_beats=0)
        _, hi = _raw_hi(bO, n + OVERHEAD)
        off = _stale_skip(hi, OVERHEAD)
        self.csr.wr(CSR_STATUS, 0x2)          # W1C fft_done
        return (re_raw[off:off + n].reshape(re.shape),
                im_raw[off:off + n].reshape(im.shape))

    # ---- rescale + phase-only pass: R -> P (BFP rescale + phase normalise) ----
    def rescale_phase(self, r_re, r_im, sh):
        cfg = self.cfg
        n = r_re.size
        in_bits = 2 * cfg.mant_bits + 1          # W_R / 2 = 37
        out_bits = cfg.unit_bits + 2              # W_P / 2 = 17
        bI, bO = self.buf[0], self.buf[1]
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        self.sw_in.soft_reset(); self.sw_out.soft_reset()
        self.csr.wr(CSR_CTRL, (int(sh) & 0x1F) << 1)
        bI.write_complex(r_re.ravel().astype(np.int64),
                         r_im.ravel().astype(np.int64),
                         in_bits, in_bits)
        self.sw_in.route({M_RESC_R: S_DMA0}, 6)
        self.sw_out.route({0: O_RESC_P}, 1)
        nb = n * WORD_BYTES
        OVERHEAD = 8
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bO.phys,
                  (n + OVERHEAD) * WORD_BYTES)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bI.phys, nb)
        t0 = time.time()
        while not (self.dma0.rd(S2MM_SR) & DMASR_IOC):
            if time.time() - t0 > 10.0:
                raise TimeoutError("rescale S2MM timeout SR=0x%08x" %
                                   self.dma0.rd(S2MM_SR))
        re_raw, im_raw = bO.read_complex(out_bits, out_bits, n + OVERHEAD,
                                         offset_beats=0)
        _, hi = _raw_hi(bO, n + OVERHEAD)
        off = _stale_skip(hi, OVERHEAD)
        return (re_raw[off:off + n].reshape(r_re.shape),
                im_raw[off:off + n].reshape(r_im.shape))
