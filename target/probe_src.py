#!/usr/bin/env python3
"""Find where stale beat 7276303945 comes from."""
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

def run_and_read(f_data, g_data, prefix=1):
    bF.write_complex(f_data, f_data * 0, mant, mant)   # im=0 for simplicity
    bG.write_complex(g_data, g_data * 0, mant, mant)
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
    bR.from_device()
    re_mask = (1 << inb) - 1
    results = []
    for i in range(min(total, 4)):
        off = i * WORD_BYTES
        lo, hi = struct.unpack_from('<QQ', bR.m, off)
        v = lo | (hi << 64)
        re_val = int(v & re_mask)
        if re_val & (1 << (inb-1)): re_val -= (1 << inb)
        results.append(re_val)
    return results

zeros = np.zeros(N*N, dtype=np.int64)
ones_f = np.ones(N*N, dtype=np.int64)
twos_f = np.full(N*N, 2, dtype=np.int64)

# Test 1: run1 = zeros x zeros -> R=0, check stale in run2
print("=== Test: run1=zeros, run2=ones ===")
r1 = run_and_read(zeros, zeros, prefix=1)
print("Run1 (zeros): bR[0..3] =", r1)
r2 = run_and_read(ones_f, ones_f, prefix=1)
print("Run2 (ones):  bR[0..3] =", r2)
print("Run2 stale [0] =", r2[0], "(expected 0 if stale comes from zeros run1)")

print()
# Test 2: run1 = ones x ones -> R = N*N elements of value 1 (re_only since im=0: 1*1+0*0=1)
# Actually: im=0 so R_re = f_re * g_re + 0*0 = f_re * g_re = 1*1 = 1
# First element of bF = 1, bG = 1 -> R[0] = 1
# Stale from re-arm: should be 1 * 1 = 1
print("=== Test: run1=ones, run2=zeros ===")
r1 = run_and_read(ones_f, ones_f, prefix=1)
print("Run1 (ones): bR[0..3] =", r1)  # should be [1, 1, 1, 1]
r2 = run_and_read(zeros, zeros, prefix=1)
print("Run2 (zeros):  bR[0..3] =", r2)
print("Run2 stale [0] =", r2[0], "(expected 1 if stale=F_ones*G_ones, 0 if stale cleared)")

print()
# Test 3: run1 = twos x twos -> R[0] = 2*2 = 4, stale should be 4
print("=== Test: run1=twos, run2=zeros ===")
r1 = run_and_read(twos_f, twos_f, prefix=1)
print("Run1 (twos): bR[0..3] =", r1)  # should be [4, 4, 4, 4]
r2 = run_and_read(zeros, zeros, prefix=1)
print("Run2 (zeros):  bR[0..3] =", r2)
print("Run2 stale [0] =", r2[0], "(expected 4 if stale=F_twos*G_twos)")

print()
# Test 4: run1 = value K in first position, zeros elsewhere
K = 100
k_f = np.zeros(N*N, dtype=np.int64); k_f[0] = K
print("=== Test: run1=[K=100,0,0,...] x ones, run2=zeros ===")
r1 = run_and_read(k_f, ones_f, prefix=1)
print("Run1 ([K,0..]):  bR[0..3] =", r1)  # R[0] = 100*1=100
r2 = run_and_read(zeros, zeros, prefix=1)
print("Run2 (zeros): bR[0..3] =", r2)
print("Run2 stale [0] =", r2[0], "(expected 100 if stale=F[0]*G[0]=K*1=100)")
