#!/usr/bin/env python3
"""Probe stale beats by using zeros for run2 - stale beats will be non-zero."""
import sys, time, struct
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

def do_run(seed_or_zero, prefix=0):
    if seed_or_zero == 0:
        n_ints = N * N
        zeros = np.zeros(n_ints, dtype=np.int64)
        bF.write_complex(zeros, zeros, mant, mant)
        bG.write_complex(zeros, zeros, mant, mant)
    else:
        rng = np.random.default_rng(seed_or_zero)
        f_re = rng.integers(-lim, lim, N*N).astype(np.int64)
        f_im = rng.integers(-lim, lim, N*N).astype(np.int64)
        g_re = rng.integers(-lim, lim, N*N).astype(np.int64)
        g_im = rng.integers(-lim, lim, N*N).astype(np.int64)
        bF.write_complex(f_re, f_im, mant, mant)
        bG.write_complex(g_re, g_im, mant, mant)
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
        if time.time() - t0 > 10.0: raise TimeoutError("S2MM timeout")
    elapsed = time.time() - t0
    bR.from_device()
    r_re, r_im = [], []
    re_mask = (1 << inb) - 1
    for i in range(total):
        off = i * WORD_BYTES
        lo, hi = struct.unpack_from('<QQ', bR.m, off)
        v = lo | (hi << 64)
        re_val = v & re_mask
        if re_val & (1 << (inb-1)): re_val -= (1 << inb)
        r_re.append(int(re_val))
    return r_re, elapsed

print("Run1: seed=42, prefix=0")
r1, t1 = do_run(42, 0)
print("  t=%.3fs, r_re[0..3]=%s" % (t1, r1[:4]))

print("Run2: seed=0 (ZEROS), prefix=16 -- stale beats will be non-zero")
r2, t2 = do_run(0, 16)  # zeros in bF/bG
print("  t=%.3fs" % t2)
print("  bR[0..19] raw re values (non-zero = stale, zero = valid):")
for i, v in enumerate(r2[:20]):
    marker = " <-- STALE" if v != 0 else " (zero = valid)"
    print("    [%2d] %d%s" % (i, v, marker))

# Count how many non-zero values at the start
stale_count = 0
for v in r2:
    if v != 0:
        stale_count += 1
    else:
        break
print("  Consecutive non-zero at start: %d (= stale count)" % stale_count)
