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

    def read_complex(self, re_w, im_w, n):
        self.from_device()
        words = np.frombuffer(self.m[0:n * WORD_BYTES], np.uint64).reshape(n, 2)
        vals = words[:, 0].astype(object) | (words[:, 1].astype(object) << 64)
        re_mask, im_mask = (1 << re_w) - 1, (1 << im_w) - 1
        re = np.fromiter((_signext(int(v) & re_mask, re_w) for v in vals), np.int64, n)
        im = np.fromiter((_signext((int(v) >> re_w) & im_mask, im_w) for v in vals),
                         np.int64, n)
        return re, im


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

    # ---- cross-power: F,G -> R = conj(F)*G, + block max (pass 1) ----
    def cross_power(self, f_re, f_im, g_re, g_im):
        cfg = self.cfg
        n = f_re.size
        mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
        bF, bG, bR = self.buf[0], self.buf[1], self.buf[2]
        bF.write_complex(f_re.ravel(), f_im.ravel(), mant, mant)
        bG.write_complex(g_re.ravel(), g_im.ravel(), mant, mant)
        self.sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
        self.sw_out.route({0: O_XP_R}, 1)
        nb = n * WORD_BYTES
        # the cross-power kernel joins F and G, so both MM2S must run together;
        # kick S2MM (R) + both MM2S, THEN wait for all three.
        _dma_reset(self.dma0); _dma_reset(self.dma1)
        _dma_kick(self.dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, nb)
        _dma_kick(self.dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
        _dma_kick(self.dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
        chans = [(self.dma0, S2MM_SR, "dma0.S2MM(R)"),
                 (self.dma0, MM2S_SR, "dma0.MM2S(F)"),
                 (self.dma1, MM2S_SR, "dma1.MM2S(G)")]
        t0 = time.time()
        while not all(reg.rd(sr) & DMASR_IDLE for reg, sr, _ in chans):
            if time.time() - t0 > 10.0:
                st = "  ".join("%s=0x%08x" % (nm, reg.rd(sr)) for reg, sr, nm in chans)
                raise TimeoutError("DMA stall: " + st)
        r_re, r_im = bR.read_complex(inb, inb, n)
        block_max = self.csr.rd(CSR_XPMAX_LO) | (self.csr.rd(CSR_XPMAX_HI) << 32)
        return r_re.reshape(f_re.shape), r_im.reshape(f_re.shape), int(block_max)

    # ---- the remaining passes follow the same pattern; TODO after cross-power
    # proves the DMA datapath on hardware ----
    def window(self, samples, coefs):
        raise NotImplementedError("window pass: TODO after cross_power validated")

    def fft_pass(self, re, im, inverse):
        raise NotImplementedError("fft pass: TODO after cross_power validated")

    def rescale_phase(self, r_re, r_im, sh):
        raise NotImplementedError("rescale/phase pass: TODO after cross_power validated")
