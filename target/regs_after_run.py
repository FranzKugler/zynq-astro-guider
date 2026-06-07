#!/usr/bin/env python3
"""After N=64 run, dump DMA status registers and first 8 bR beats raw."""
import sys, time
sys.path.insert(0, 'target/src')
sys.path.insert(0, 'golden_model/src')
import numpy as np
from guider_golden.fixed_point import FixedConfig
from guider_target.uio_backend import (
    UdmaBuf, MmReg, AxisSwitch, _dma_reset, _dma_kick,
    CSR_BASE, DMA0_BASE, DMA1_BASE, SWIN_BASE, SWOUT_BASE,
    MM2S_CR, MM2S_SR, MM2S_SA, MM2S_LEN,
    S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN,
    DMASR_IOC, DMASR_HALTED, DMASR_IDLE, DMACR_RS,
    M_XP_F, M_XP_G, S_DMA0, S_DMA1, O_XP_R, WORD_BYTES,
    CSR_ID, CSR_ID_MAGIC,
)

cfg = FixedConfig()
csr = MmReg(CSR_BASE)
assert csr.rd(CSR_ID) == CSR_ID_MAGIC
dma0 = MmReg(DMA0_BASE)
dma1 = MmReg(DMA1_BASE)
sw_in = AxisSwitch(MmReg(SWIN_BASE))
sw_out = AxisSwitch(MmReg(SWOUT_BASE))
bF, bG, bR = UdmaBuf("udmabuf0"), UdmaBuf("udmabuf1"), UdmaBuf("udmabuf2")

N = 64
mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
lim = 1 << (mant - 1)
rng = np.random.default_rng(0)
f_re = rng.integers(-lim, lim, (N,N)).astype(np.int64)
f_im = rng.integers(-lim, lim, (N,N)).astype(np.int64)
g_re = rng.integers(-lim, lim, (N,N)).astype(np.int64)
g_im = rng.integers(-lim, lim, (N,N)).astype(np.int64)

bF.write_complex(f_re.ravel(), f_im.ravel(), mant, mant)
bG.write_complex(g_re.ravel(), g_im.ravel(), mant, mant)
sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
sw_out.route({0: O_XP_R}, 1)
nb = N * N * WORD_BYTES
_dma_reset(dma0); _dma_reset(dma1)
_dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, nb)
_dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
_dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
t0 = time.time()
while not (dma0.rd(S2MM_SR) & DMASR_IOC):
    if time.time() - t0 > 10.0: raise TimeoutError("S2MM timeout")

# Immediately read register state
t_ioc = time.time() - t0
print("=== DMA state immediately after S2MM IOC (t=%.3f s) ===" % t_ioc)
def dump_dma(name, reg):
    mm2s_cr = reg.rd(MM2S_CR); mm2s_sr = reg.rd(MM2S_SR)
    s2mm_cr = reg.rd(S2MM_CR); s2mm_sr = reg.rd(S2MM_SR)
    print("%s MM2S CR=0x%08x SR=0x%08x (HALTED=%d IDLE=%d IOC=%d)" % (
        name, mm2s_cr, mm2s_sr,
        (mm2s_sr>>0)&1, (mm2s_sr>>1)&1, (mm2s_sr>>12)&1))
    print("%s S2MM CR=0x%08x SR=0x%08x (HALTED=%d IDLE=%d IOC=%d)" % (
        name, s2mm_cr, s2mm_sr,
        (s2mm_sr>>0)&1, (s2mm_sr>>1)&1, (s2mm_sr>>12)&1))
dump_dma("dma0", dma0)
dump_dma("dma1", dma1)

# Wait a bit to let re-arm settle, then check again
time.sleep(0.01)
print("\n=== After 10ms sleep ===")
dump_dma("dma0", dma0)
dump_dma("dma1", dma1)

time.sleep(0.1)
print("\n=== After 100ms sleep ===")
dump_dma("dma0", dma0)
dump_dma("dma1", dma1)

time.sleep(1.0)
print("\n=== After 1s sleep ===")
dump_dma("dma0", dma0)
dump_dma("dma1", dma1)

# Read first 8 bR beats raw (before any reset)
bR.from_device()
import struct
print("\n=== First 8 bR beats (raw uint64 pairs) ===")
for i in range(8):
    start = i * WORD_BYTES
    lo, hi = struct.unpack_from('<QQ', bR.m, start)
    # re field = signext(lo & 0x1FFFFFFFFF, 37)
    re_raw = lo & 0x1FFFFFFFFF
    if re_raw & (1<<36): re_raw -= (1<<37)
    print("  bR[%d]: lo=0x%016x hi=0x%016x re=%d" % (i, lo, hi, re_raw))
