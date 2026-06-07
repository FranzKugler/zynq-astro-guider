#!/usr/bin/env python3
"""Probe how many stale beats are in sw_in after each N=64 cross_power call.
After a fresh bitstream, run N=64 (should pass). Then run N=64 again with a
large prefix to capture all stale + valid beats, and find where valid starts.
"""
import sys, mmap, os, struct, time
sys.path.insert(0, 'target/src')
sys.path.insert(0, 'golden_model/src')
import numpy as np
from guider_golden.fixed_point import FixedConfig
from guider_target.uio_backend import (
    MM2S_SR, S2MM_SR,
    UdmaBuf, MmReg, AxisSwitch, _dma_reset, _dma_kick,
    CSR_BASE, DMA0_BASE, DMA1_BASE, SWIN_BASE, SWOUT_BASE,
    MM2S_CR, MM2S_SA, MM2S_LEN, S2MM_CR, S2MM_DA, S2MM_LEN,
    DMASR_IOC, M_XP_F, M_XP_G, S_DMA0, S_DMA1, O_XP_R, WORD_BYTES,
    CSR_ID, CSR_ID_MAGIC, CSR_XPMAX_LO, CSR_XPMAX_HI
)

cfg = FixedConfig()
csr = MmReg(CSR_BASE)
assert csr.rd(CSR_ID) == CSR_ID_MAGIC, "CSR ID mismatch"
dma0 = MmReg(DMA0_BASE)
dma1 = MmReg(DMA1_BASE)
sw_in = AxisSwitch(MmReg(SWIN_BASE))
sw_out = AxisSwitch(MmReg(SWOUT_BASE))
bF, bG, bR = UdmaBuf("udmabuf0"), UdmaBuf("udmabuf1"), UdmaBuf("udmabuf2")

def cross_power_raw(n, prefix):
    """Run cross_power with given prefix, return ALL (prefix+n) raw re values."""
    mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
    rng = np.random.default_rng(0)
    lim = 1 << (mant - 1)
    f_re = rng.integers(-lim, lim, (n, n)).astype(np.int64)
    f_im = rng.integers(-lim, lim, (n, n)).astype(np.int64)
    g_re = rng.integers(-lim, lim, (n, n)).astype(np.int64)
    g_im = rng.integers(-lim, lim, (n, n)).astype(np.int64)
    bF.write_complex(f_re.ravel(), f_im.ravel(), mant, mant)
    bG.write_complex(g_re.ravel(), g_im.ravel(), mant, mant)
    sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
    sw_out.route({0: O_XP_R}, 1)
    nb = n * n * WORD_BYTES
    _dma_reset(dma0); _dma_reset(dma1)
    _dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, (n*n + prefix) * WORD_BYTES)
    _dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
    _dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
    t0 = time.time()
    while not (dma0.rd(S2MM_SR) & DMASR_IOC):
        if time.time() - t0 > 10.0:
            raise TimeoutError("S2MM timeout")
    # read all allocated beats from offset 0
    total = n*n + prefix
    r_re, r_im = bR.read_complex(inb, inb, total, offset_beats=0)
    exp_re = (f_re * g_re + f_im * g_im).ravel()
    return r_re, exp_re

N = 64
MAX_PREFIX = 16  # probe up to 16 stale beats

print("Run 1 (fresh state, prefix=0):")
r_re, exp = cross_power_raw(N, 0)
match = np.array_equal(r_re, exp)
print("  bit-exact:", match)
if not match:
    # find first mismatch
    first_mm = np.argwhere(r_re != exp)[0][0]
    print("  first mismatch at index:", first_mm)

print()
print("Run 2 (probe prefix=%d):" % MAX_PREFIX)
r_re, exp = cross_power_raw(N, MAX_PREFIX)
print("  exp[0] =", exp[0])
for i in range(MAX_PREFIX + 4):
    match_i = (r_re[i] == exp[0])
    print("  offset %2d: hw=%d %s" % (i, r_re[i], "<-- MATCH (stale count=%d)" % i if match_i else ""))

# also check if exp[0] anywhere
matches = np.where(r_re[:MAX_PREFIX+N] == exp[0])[0]
print("  exp[0] found at positions:", matches[:10])
