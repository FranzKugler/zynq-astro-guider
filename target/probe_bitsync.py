#!/usr/bin/env python3
"""Read bR[0] from run1 and compare to stale in run2.
Fresh bitstream assumed already loaded."""
import sys, time, struct
sys.path.insert(0, 'target/src')
sys.path.insert(0, 'golden_model/src')
import numpy as np
from guider_golden.fixed_point import FixedConfig
from guider_target.uio_backend import (
    UdmaBuf, MmReg, AxisSwitch, _dma_reset, _dma_kick,
    CSR_BASE, DMA0_BASE, DMA1_BASE, SWIN_BASE, SWOUT_BASE,
    MM2S_CR, MM2S_SA, MM2S_LEN, S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN,
    DMASR_IOC, M_XP_F, M_XP_G, S_DMA0, S_DMA1, O_XP_R, WORD_BYTES,
    CSR_ID, CSR_ID_MAGIC, MM2S_SR,
)
cfg = FixedConfig()
csr = MmReg(CSR_BASE)
assert csr.rd(CSR_ID) == CSR_ID_MAGIC, "no bitstream"
dma0 = MmReg(DMA0_BASE)
dma1 = MmReg(DMA1_BASE)
sw_in = AxisSwitch(MmReg(SWIN_BASE))
sw_out = AxisSwitch(MmReg(SWOUT_BASE))
bF, bG, bR = UdmaBuf("udmabuf0"), UdmaBuf("udmabuf1"), UdmaBuf("udmabuf2")

N = 64
mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
lim = 1 << (mant - 1)

def run(f_re, f_im, g_re, g_im, prefix=0):
    bF.write_complex(f_re, f_im, mant, mant)
    bG.write_complex(g_re, g_im, mant, mant)
    sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
    sw_out.route({0: O_XP_R}, 1)
    nb = N*N*WORD_BYTES
    _dma_reset(dma0); _dma_reset(dma1)
    _dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, (N*N+prefix)*WORD_BYTES)
    _dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
    _dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
    t0 = time.time()
    while not (dma0.rd(S2MM_SR) & DMASR_IOC):
        if time.time()-t0 > 10: raise TimeoutError()

rng = np.random.default_rng(42)
f_re = rng.integers(-lim, lim, N*N).astype(np.int64)
f_im = rng.integers(-lim, lim, N*N).astype(np.int64)
g_re = rng.integers(-lim, lim, N*N).astype(np.int64)
g_im = rng.integers(-lim, lim, N*N).astype(np.int64)

print("=== run1: seed42, prefix=0 ===")
run(f_re, f_im, g_re, g_im, prefix=0)
bR.from_device()
lo0, hi0 = struct.unpack_from('<QQ', bR.m, 0)
re0 = lo0 & ((1<<inb)-1)
if re0 & (1<<(inb-1)): re0 -= (1<<inb)
lo1, hi1 = struct.unpack_from('<QQ', bR.m, WORD_BYTES)
re1 = lo1 & ((1<<inb)-1)
if re1 & (1<<(inb-1)): re1 -= (1<<inb)
print("bR[0].re =", re0)
print("bR[1].re =", re1)
exp0 = int(f_re[0]*g_re[0]+f_im[0]*g_im[0])
exp1 = int(f_re[1]*g_re[1]+f_im[1]*g_im[1])
print("expected[0] =", exp0, "match:", re0==exp0)
print("expected[1] =", exp1, "match:", re1==exp1)
print()

print("=== run2: zeros, prefix=16 ===")
z = np.zeros(N*N, dtype=np.int64)
run(z, z, z, z, prefix=16)
bR.from_device()
vals = []
for i in range(4):
    lo, hi = struct.unpack_from('<QQ', bR.m, i*WORD_BYTES)
    re = lo & ((1<<inb)-1)
    if re & (1<<(inb-1)): re -= (1<<inb)
    vals.append(re)
print("bR[0..3] =", vals)
nonz = [(i, vals[i]) for i in range(len(vals)) if vals[i] != 0]
print("non-zero:", nonz)
print("stale == bR[0] from run1?", vals[0] == re0)
