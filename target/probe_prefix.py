#!/usr/bin/env python3
"""Determine exact stale prefix by running multiple N=64 passes with large
prefix and comparing actual vs expected values at each beat position.
Assumes fresh bitstream already loaded (no prior state in switch FIFOs)."""
import sys, time
sys.path.insert(0, 'target/src')
sys.path.insert(0, 'golden_model/src')
import numpy as np
from guider_golden.fixed_point import FixedConfig
from guider_target.uio_backend import (
    UdmaBuf, MmReg, AxisSwitch, _dma_reset, _dma_kick,
    CSR_BASE, DMA0_BASE, DMA1_BASE, SWIN_BASE, SWOUT_BASE,
    MM2S_CR, MM2S_SA, MM2S_LEN, S2MM_CR, S2MM_SR, S2MM_DA, S2MM_LEN,
    DMASR_IOC, M_XP_F, M_XP_G, S_DMA0, S_DMA1, O_XP_R, WORD_BYTES,
    CSR_ID, CSR_ID_MAGIC,
)

cfg = FixedConfig()
csr = MmReg(CSR_BASE)
assert csr.rd(CSR_ID) == CSR_ID_MAGIC, "bitstream not loaded"
dma0 = MmReg(DMA0_BASE)
dma1 = MmReg(DMA1_BASE)
sw_in = AxisSwitch(MmReg(SWIN_BASE))
sw_out = AxisSwitch(MmReg(SWOUT_BASE))
bF, bG, bR = UdmaBuf("udmabuf0"), UdmaBuf("udmabuf1"), UdmaBuf("udmabuf2")

N = 64
OVERHEAD = 8   # allocate N*N + OVERHEAD beats to capture any stale prefix
mant, inb = cfg.mant_bits, 2 * cfg.mant_bits + 1
lim = 1 << (mant - 1)

def do_run(seed, label):
    rng = np.random.default_rng(seed)
    f_re = rng.integers(-lim, lim, N*N).astype(np.int64)
    f_im = rng.integers(-lim, lim, N*N).astype(np.int64)
    g_re = rng.integers(-lim, lim, N*N).astype(np.int64)
    g_im = rng.integers(-lim, lim, N*N).astype(np.int64)
    exp = f_re * g_re + f_im * g_im   # expected R_re values
    bF.write_complex(f_re, f_im, mant, mant)
    bG.write_complex(g_re, g_im, mant, mant)
    sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
    sw_out.route({0: O_XP_R}, 1)
    nb = N * N * WORD_BYTES
    _dma_reset(dma0); _dma_reset(dma1)
    _dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, (N*N + OVERHEAD) * WORD_BYTES)
    _dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
    _dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
    t0 = time.time()
    while not (dma0.rd(S2MM_SR) & DMASR_IOC):
        if time.time() - t0 > 10: raise TimeoutError()
    r_re, r_im = bR.read_complex(inb, inb, N*N + OVERHEAD, offset_beats=0)
    # Find first position where bR matches exp[0]
    matches = np.where(r_re[:OVERHEAD+5] == exp[0])[0]
    stale = int(matches[0]) if len(matches) > 0 else -1
    print("%s  exp[0]=%d  bR[0..7]=%s  stale_offset=%d" % (
        label, exp[0], list(r_re[:OVERHEAD]), stale))
    if stale >= 0:
        ok = np.array_equal(r_re[stale:stale+N*N], exp)
        print("  bit-exact at offset %d: %s" % (stale, ok))
    return stale

print("=== run1 (fresh bitstream, seed=42) ===")
s1 = do_run(42, "run1")
print()
print("=== run2 (same session, seed=99) ===")
s2 = do_run(99, "run2")
print()
print("=== run3 (same session, seed=7) ===")
s3 = do_run(7, "run3")
print()
print("Stale offsets: run1=%d run2=%d run3=%d" % (s1, s2, s3))
