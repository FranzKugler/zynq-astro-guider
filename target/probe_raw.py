#!/usr/bin/env python3
"""Print raw 128-bit beats to find where 7276303945 comes from."""
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

def print_beats(buf, name, n=8):
    buf.from_device()
    print("%s raw beats:" % name)
    for i in range(n):
        lo, hi = struct.unpack_from('<QQ', buf.m, i * WORD_BYTES)
        re = lo & ((1<<inb)-1)
        if re & (1<<(inb-1)): re -= (1<<inb)
        print("  [%d] lo=0x%016x hi=0x%016x re_37=%d" % (i, lo, hi, re))

def run(seed, prefix=0):
    rng = np.random.default_rng(seed)
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
        if time.time() - t0 > 10: raise TimeoutError("timeout")
    # Print bF and bG around N=64 boundary
    print_beats(bF, "bF", 4)
    print_beats(bG, "bG", 4)
    print_beats(bR, "bR (before zeros run)", 4)
    
    # bF[N*N] is the beat JUST PAST the transfer (re-arm starts here if address increments)
    print("bF at offset N*N = %d beats:" % (N*N))
    off = N*N*WORD_BYTES
    for i in range(3):
        lo, hi = struct.unpack_from('<QQ', bF.m, off + i*WORD_BYTES)
        print("  bF[%d] lo=0x%016x hi=0x%016x" % (N*N+i, lo, hi))
    print("bG at offset N*N:")
    for i in range(3):
        lo, hi = struct.unpack_from('<QQ', bG.m, off + i*WORD_BYTES)
        print("  bG[%d] lo=0x%016x hi=0x%016x" % (N*N+i, lo, hi))

# Fresh run to prime state
zeros = np.zeros(N*N, dtype=np.int64)
bF.write_complex(zeros, zeros, mant, mant)
bG.write_complex(zeros, zeros, mant, mant)
sw_in.route({M_XP_F: S_DMA0, M_XP_G: S_DMA1}, 6)
sw_out.route({0: O_XP_R}, 1)
nb = N * N * WORD_BYTES
_dma_reset(dma0); _dma_reset(dma1)
_dma_kick(dma0, S2MM_CR, S2MM_DA, S2MM_LEN, bR.phys, (N*N)*WORD_BYTES)
_dma_kick(dma0, MM2S_CR, MM2S_SA, MM2S_LEN, bF.phys, nb)
_dma_kick(dma1, MM2S_CR, MM2S_SA, MM2S_LEN, bG.phys, nb)
t0 = time.time()
while not (dma0.rd(S2MM_SR) & DMASR_IOC):
    if time.time() - t0 > 10: raise TimeoutError()
print("=== Prime run (zeros) done ===")
print()

print("=== Main probe: run seed=99 then check raw data ===")
run(99, prefix=0)
