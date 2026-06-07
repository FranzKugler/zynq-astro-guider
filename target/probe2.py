#!/usr/bin/env python3
"""Probe stale count with different seeds so stale != valid."""
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
    DMASR_IOC, M_XP_F, M_XP_G, S_DMA0, S_DMA1, O_XP_R, WORD_BYTES,
    CSR_ID, CSR_ID_MAGIC,
)

cfg = FixedConfig()
csr = MmReg(CSR_BASE)
assert csr.rd(CSR_ID) == CSR_ID_MAGIC, "CSR ID mismatch"
dma0 = MmReg(DMA0_BASE)
dma1 = MmReg(DMA1_BASE)
sw_in = AxisSwitch(MmReg(SWIN_BASE))
sw_out = AxisSwitch(MmReg(SWOUT_BASE))
bF, bG, bR = UdmaBuf("udmabuf0"), UdmaBuf("udmabuf1"), UdmaBuf("udmabuf2")

N = 64
mant = cfg.mant_bits
inb = 2 * mant + 1

def run_cross(seed, prefix):
    lim = 1 << (mant - 1)
    rng = np.random.default_rng(seed)
    f_re = rng.integers(-lim, lim, (N,N)).astype(np.int64)
    f_im = rng.integers(-lim, lim, (N,N)).astype(np.int64)
    g_re = rng.integers(-lim, lim, (N,N)).astype(np.int64)
    g_im = rng.integers(-lim, lim, (N,N)).astype(np.int64)
    bF.write_complex(f_re.ravel(), f_im.ravel(), mant, mant)
    bG.write_complex(g_re.ravel(), g_im.ravel(), mant, mant)
    sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
    sw_out.route({0: O_XP_R}, 1)
    nb = N * N * WORD_BYTES
    total = N * N + prefix
    _dma_reset(dma0); _dma_reset(dma1)
    _dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, total * WORD_BYTES)
    _dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
    _dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
    t0 = time.time()
    while not (dma0.rd(S2MM_SR) & DMASR_IOC):
        if time.time() - t0 > 10.0: raise TimeoutError("timeout")
    r_re, r_im = bR.read_complex(inb, inb, total, offset_beats=0)
    exp_re = (f_re * g_re + f_im * g_im).ravel()
    return r_re, r_im, exp_re

# Run 1: seed=0, prefix=0  (should be fresh/pass)
r_re, r_im, exp = run_cross(0, 0)
ok = np.array_equal(r_re[:N*N], exp)
print("Run1 seed=0 prefix=0: bit-exact=%s" % ok)
if not ok:
    print("  mismatch at 0: hw=%d exp=%d" % (r_re[0], exp[0]))

# Run 2: seed=1, prefix=32  - use different seed so stale from run1 != valid
r_re2, r_im2, exp2 = run_cross(1, 32)
# exp2[0] is the first VALID beat of the run-2 result
# if there are S stale beats, exp2[0] appears at position S
print("Run2 seed=1 prefix=32: exp2[0]=%d" % exp2[0])
for i in range(min(40, len(r_re2))):
    match = (r_re2[i] == exp2[0])
    marker = " <-- MATCH stale_count=%d" % i if match else ""
    print("  [%2d] hw=%d%s" % (i, r_re2[i], marker))
    if i > 4 and match:
        break

# If stale count found, show bits around it
matches = np.where(r_re2 == exp2[0])[0]
print("exp2[0] at positions:", matches[:5].tolist())
