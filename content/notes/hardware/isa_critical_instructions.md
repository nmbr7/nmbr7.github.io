+++
title = "ISA Critical Instructions"
date = 2026-03-20
[taxonomies]
tags = ["x86", "arm", "risc-v", "simd", "atomics", "memory-ordering", "cache-control", "virtualization"]
+++


# Critical ISA Instructions: ARM AArch64, x86-64, and RISC-V

Comprehensive reference for performance-critical instructions across the three dominant ISAs. Covers memory ordering, SIMD/vector, cache control, atomics, bit manipulation, cryptography, branch/control flow, system instructions, floating point, string operations, transactional memory, virtualization, and performance monitoring.

**Last updated: 2026-03-20**

---

## Table of Contents

1. [Cross-ISA Fundamentals](#1-cross-isa-fundamentals)
2. [Memory Ordering and Barriers](#2-memory-ordering-and-barriers)
3. [SIMD and Vector Processing](#3-simd-and-vector-processing)
4. [Cache Control](#4-cache-control)
5. [Atomics and Lock-Free Instructions](#5-atomics-and-lock-free-instructions)
6. [Bit Manipulation](#6-bit-manipulation)
7. [Cryptography Acceleration](#7-cryptography-acceleration)
8. [Branch and Control Flow Security](#8-branch-and-control-flow-security)
9. [System and Privileged Instructions](#9-system-and-privileged-instructions)
10. [Floating Point and Precision](#10-floating-point-and-precision)
11. [String and Memory Operations](#11-string-and-memory-operations)
12. [Transactional Memory](#12-transactional-memory)
13. [Virtualization](#virtualization)
14. [Performance Monitoring](#14-performance-monitoring)
15. [Compiler Mapping: C11/C++ Atomics to ISA](#15-compiler-mapping-c11-c-atomics-to-isa)
16. [ISA Extension Discovery](#16-isa-extension-discovery)
17. [Key Papers and Resources](#17-key-papers-and-resources)

---

## 1. Cross-ISA Fundamentals

### 1.1 Encoding: Fixed vs Variable vs Compressed

```
ISA          Encoding         Instruction Width    Decode Complexity
-----------  ---------------  -------------------  ------------------
x86-64       Variable-length  1-15 bytes           High (prefix hell)
AArch64      Fixed-width      4 bytes              Low (uniform)
RISC-V       Fixed + C ext    4 bytes / 2 bytes    Low-Medium
```

**x86-64 variable-length encoding:**
- Legacy prefixes (up to 4), REX/VEX/EVEX prefix, opcode (1-3 bytes), ModR/M, SIB, displacement, immediate
- Intel APX (2024): introduces REX2 prefix for 32 GPRs, EVEX-extended legacy instructions, NDD (new data destination) 3-operand form, conditional stores (CFCMOV), suppressed flags (NF bit)
- Decode is the bottleneck: modern x86 CPUs have dedicated predecode stages, micro-op caches (uop cache / DSB on Intel, op cache on AMD), and loop stream detectors
- Macro-fusion: CMP+JCC fused into single uop on Intel/AMD (saves decode bandwidth)

**AArch64 fixed-width encoding:**
- Every instruction is exactly 32 bits -- enormous simplification for fetch/decode
- Instruction field encoding is highly regular: bits [31:25] usually identify the instruction class
- Conditional execution via condition codes on select instructions (CSEL, CSINC, CSNEG, CCMP) rather than predicated execution of ARMv7
- Register encoding is uniform (5 bits for 32 GPRs X0-X30 + SP/ZR)

**RISC-V encoding:**
- Base ISA (RV64I): 32-bit fixed-width instructions, extremely regular encoding
- C extension (Zca/Zcb/Zcf/Zcd): 16-bit compressed instructions for ~50% of common operations, reducing code size by ~25-30%
- Extensions add instructions but never change base encoding -- guaranteed backwards compatibility
- Custom instruction space (custom-0 through custom-3) reserved for domain-specific accelerators

**Impact on performance:**
- x86 decode bottleneck: Intel's uop cache (since Sandy Bridge) holds ~1536 uops, bypassing the complex decoder for hot loops. When uop cache misses, the legacy decode pipeline (4-wide on P-cores, 6-wide on Lion Cove) becomes the bottleneck
- AArch64 decode advantage: Apple M-series achieves 8-wide decode trivially due to fixed encoding. Neoverse V2 (Grace) decodes 8 instructions/cycle
- RISC-V: SiFive P870 achieves 6-wide decode; the C extension complicates alignment but is manageable compared to x86

### 1.2 Memory Model Comparison

```
                  x86-64 (TSO)           AArch64 (Weakly Ordered)    RISC-V (RVWMO)
                  ==================     ========================    =================
Store-Store       Ordered (guaranteed)   Reorderable                 Reorderable
Load-Load         Ordered (guaranteed)   Reorderable                 Reorderable
Load-Store        Ordered (guaranteed)   Reorderable                 Reorderable
Store-Load        REORDERABLE            Reorderable                 Reorderable
Dependent loads   Ordered (guaranteed)   Ordered (guaranteed*)       Ordered (RISC-V 2024+)
Atomics           Sequential consistent  Acquire/Release explicit    Acquire/Release explicit
```

*ARM has address dependency ordering but NOT control dependency ordering. The infamous "MP+dmb.sy+ctrl" litmus test shows that a branch dependent on a load does NOT order subsequent loads without an explicit ISB or DMB.

**x86-64 Total Store Order (TSO):**
- Every load acts as if it has acquire semantics
- Every store acts as if it has release semantics
- Only Store-Load reordering is allowed (and store-buffer forwarding)
- LOCK prefix or MFENCE needed only for sequential consistency (StoreLoad barrier)
- This is why most x86 lock-free code "just works" -- the hardware is doing most of the fencing for you
- Gotcha: non-temporal stores (MOVNTI, MOVNTDQ) bypass TSO and are weakly ordered -- require SFENCE

**AArch64 weakly-ordered model:**
- All four reorderings permitted unless explicitly prevented
- Load-Acquire (LDAR/LDAPR) and Store-Release (STLR) provide one-directional barriers
- ARMv8.3 adds LDAPR: load-acquire with weaker ordering than LDAR (acquire but can be reordered with earlier stores to different addresses -- "RCpc" semantics matching C++ memory_order_consume-like behavior)
- DMB/DSB/ISB for explicit barriers (see Section 2)
- Multi-copy atomicity: ARMv8 guarantees it -- a store visible to one non-originating core is visible to all

**RISC-V RVWMO (Weak Memory Ordering):**
- Extremely relaxed: essentially only preserves syntactic data dependencies and same-address ordering
- fence instruction with fine-grained bits: fence rw,rw / fence r,r / fence w,w / fence rw,w etc.
- Ztso extension: provides TSO semantics for x86 binary translation (useful for QEMU, Rosetta-like scenarios)
- Zam extension (proposed): misaligned atomics support
- RISC-V 2024 ratified "Svnapot" and strengthened dependency ordering rules

### 1.3 Register Files

```
ISA        GPRs    FP Regs       Vector Regs              Special
--------   ------  ------------  -----------------------  ------------------
x86-64     16*     16 XMM/YMM    32 ZMM (AVX-512)        FLAGS, RIP, segments
AArch64    31      32 V (128b)   32 Z (scalable SVE)      NZCV, SP, PC (not GPR)
RISC-V     32      32 F          32 V (scalable RVV)      zero (x0), pc
```

*Intel APX doubles GPRs to 32 (R16-R31) via REX2/extended EVEX. AMD has not yet adopted APX.

---

## 2. Memory Ordering and Barriers

### 2.1 x86-64 Barriers

| Instruction | Encoding | Effect | Latency (cycles) |
|-------------|----------|--------|-------------------|
| `MFENCE` | `0F AE F0` | Full fence: all loads and stores before MFENCE complete before any loads/stores after | ~33-40 (Intel), ~20 (AMD Zen4) |
| `SFENCE` | `0F AE F8` | Store fence: all stores before SFENCE visible before any stores after | ~4-6 |
| `LFENCE` | `0F AE E8` | Load fence: all loads before LFENCE complete before any loads after. Also serializes instruction stream (used for Spectre mitigation) | ~4-6 |
| `LOCK` prefix | `F0` | On atomic RMW: implicit full barrier (both acquire + release). Locks cache line in MESI exclusive state | ~18-22 (uncontended), varies with contention |

**Microarchitectural details:**

`MFENCE` drains the store buffer completely. On Intel, it also acts as a dispatch serializing instruction (no younger uops execute until MFENCE retires). On AMD Zen4+, MFENCE is lighter -- it only waits for the store buffer to drain, not full serialization.

`LFENCE` was originally specified as a load fence only, but Intel's implementation makes it dispatch-serializing (no younger uops dispatched until all older uops retire). AMD made LFENCE dispatch-serializing starting with a microcode update (needed for Spectre v1 mitigation via `lfence` after bounds check). This is controlled by MSR `C001_1029[1]` (Serializing LFENCE) on AMD.

`SFENCE` only orders stores -- specifically, it ensures WC (write-combining) and NT stores are visible. Under TSO, regular stores are already ordered, so SFENCE is only needed after `MOVNTI`/`MOVNTDQ`/`MOVNTPS` etc.

**Real-world usage:**
```c
// Linux kernel: smp_mb() on x86
// Since x86 TSO only reorders Store-Load, a LOCK'ed instruction is
// preferred over MFENCE (faster on most microarchitectures)
#define smp_mb()    asm volatile("lock; addl $0,-4(%%rsp)" ::: "memory", "cc")

// Spectre v1 mitigation in kernel array bounds check
if (index < array_size) {
    asm volatile("lfence");  // Prevent speculative load
    value = array[index];
}
```

### 2.2 AArch64 Barriers

| Instruction | Effect | Variants |
|-------------|--------|----------|
| `DMB` (Data Memory Barrier) | Orders memory accesses; does NOT stall execution | `DMB ISH` (inner shareable), `DMB OSH` (outer shareable), `DMB SY` (full system), `DMB ISHLD` (load-load inner), `DMB ISHST` (store-store inner) |
| `DSB` (Data Synchronization Barrier) | Like DMB but also stalls execution until all memory accesses complete | Same variants as DMB: `DSB ISH`, `DSB SY`, etc. |
| `ISB` (Instruction Synchronization Barrier) | Flushes pipeline, ensuring all subsequent instructions are fetched fresh. Required after modifying system registers, page tables, or self-modifying code | No variants; always full |
| `LDAR` (Load-Acquire Register) | Load with acquire semantics: no subsequent memory access (load or store) can be reordered before this load | `LDARB` (byte), `LDARH` (halfword), `LDAR` (word/doubleword) |
| `STLR` (Store-Release Register) | Store with release semantics: no preceding memory access can be reordered after this store | `STLRB`, `STLRH`, `STLR` |
| `LDAPR` (Load-Acquire RCpc) | Weaker acquire: prevents reordering of later loads/stores before this load, but allows reordering with earlier stores to different addresses | ARMv8.3-RCPC. `LDAPRB`, `LDAPRH` |

**Shareability domains:**
- **NSH** (non-shareable): only this core
- **ISH** (inner shareable): all cores in the same inner shareable domain (typically all cores in a socket)
- **OSH** (outer shareable): all cores across sockets / all bus masters
- **SY** (system): everything including peripherals, DMA engines

This matters enormously for multi-socket ARM servers (Ampere Altra, Neoverse N2/V2 based systems). Using `DMB ISH` instead of `DMB SY` avoids expensive cross-socket traffic for workloads confined to one socket.

**DMB vs DSB:**
- `DMB` orders memory accesses but allows the CPU to continue executing non-memory instructions speculatively
- `DSB` stalls the pipeline until all prior memory accesses complete. Required before `ISB`, before `WFE`/`WFI`, and before TLB maintenance (`TLBI` followed by `DSB ISH` followed by `ISB`)

**Real-world usage:**
```c
// Linux kernel: smp_mb() on AArch64
#define smp_mb()     asm volatile("dmb ish" ::: "memory")
#define smp_rmb()    asm volatile("dmb ishld" ::: "memory")
#define smp_wmb()    asm volatile("dmb ishst" ::: "memory")

// TLB invalidation sequence (ARM Architecture Reference Manual)
TLBI VAE1IS, Xt     // Invalidate TLB entry by VA, EL1, inner shareable
DSB ISH              // Wait for TLBI to complete
ISB                  // Ensure subsequent fetches see new translations

// Spin-lock release (store-release is sufficient)
STLR W0, [X1]       // Release lock -- all prior accesses visible before unlock
```

### 2.3 RISC-V Fences

| Instruction | Effect | Encoding bits |
|-------------|--------|--------------|
| `fence rw, rw` | Full fence (all prior reads/writes complete before all subsequent reads/writes) | PI=1, PO=1, PR=1, PW=1, SI=1, SO=1, SR=1, SW=1 |
| `fence r, r` | Read-read fence (load-load ordering) | PR=1, SR=1 |
| `fence w, w` | Write-write fence (store-store ordering) | PW=1, SW=1 |
| `fence rw, w` | Release fence (prior reads/writes before subsequent writes) | PR=1, PW=1, SW=1 |
| `fence r, rw` | Acquire fence (prior reads before subsequent reads/writes) | PR=1, SR=1, SW=1 |
| `fence.tso` | TSO fence (orders all except store-load; equivalent to `fence rw,rw` minus StoreLoad) | Special encoding |
| `fence.i` | Instruction fence: ensures subsequent instruction fetches see all prior stores (self-modifying code, JIT) | Separate Zifencei extension |

**RISC-V atomic ordering annotations:**
RISC-V AMO and LR/SC instructions carry `.aq` (acquire) and `.rl` (release) bits directly:
- `amoadd.w.aq` -- atomic add with acquire
- `amoadd.w.rl` -- atomic add with release
- `amoadd.w.aqrl` -- atomic add with sequential consistency
- No annotation: relaxed ordering

**The Ztso extension:**
When Ztso is implemented, the hardware provides TSO semantics for ALL memory accesses. `fence` instructions become no-ops (except `fence.i`). This is primarily useful for running x86 binaries under translation (similar to how Apple's Rosetta 2 relies on ARM's TSO mode via ACTLR_EL1.EnTSO on Apple Silicon).

**Real-world usage:**
```c
// Linux kernel: RISC-V memory barriers
#define smp_mb()     asm volatile("fence rw, rw" ::: "memory")
#define smp_rmb()    asm volatile("fence r, r" ::: "memory")
#define smp_wmb()    asm volatile("fence w, w" ::: "memory")

// Release store pattern (no dedicated load-acquire/store-release instructions
// in base RISC-V, so we use fence + store):
fence rw, w          // Release fence
sw   a0, 0(a1)      // Store

// Acquire load pattern:
lw   a0, 0(a1)      // Load
fence r, rw          // Acquire fence

// With Zacas extension (2024 ratified): Compare-and-swap
// amocas.d.aqrl rd, rs2, (rs1)  -- 64-bit CAS with full ordering
```

### 2.4 Summary Comparison: Barrier Cost

```
Operation              x86-64          AArch64              RISC-V
--------------------   ----------      ------------------   ------------------
Relaxed load           MOV (0 extra)   LDR (0 extra)       LD (0 extra)
Acquire load           MOV (free)      LDAR (~1-3 extra)   LD + fence r,rw
Release store          MOV (free)      STLR (~1-3 extra)   fence rw,w + SD
Seq-cst load           MOV (free)      LDAR (~1-3 extra)   fence rw,rw + LD + fence r,rw
Seq-cst store          MOV+MFENCE or   STLR (~1-3 extra)   fence rw,w + SD + fence rw,rw
                       LOCK;MOV (~20+)
Seq-cst RMW            LOCK CMPXCHG    LDAXR/STLXR loop    LR.aqrl/SC.aqrl
Full barrier           MFENCE (~33)    DMB ISH (~10-30)     fence rw,rw (~10-30)
```

The key insight: x86-64's strong memory model makes relaxed/acquire/release essentially free (the hardware enforces TSO), but seq-cst stores are expensive (requiring MFENCE or LOCK prefix). ARM and RISC-V make relaxed access truly relaxed (better for data structures that don't need ordering), but you pay explicitly for every ordering constraint.

---

## 3. SIMD and Vector Processing

### 3.1 x86-64: SSE through AVX-512 and APX

**Evolution:**
```
Extension   Year   Width   Key Capability
---------   ----   -----   ------------------------------------------
SSE         1999   128b    4x float, integer ops via MMX/SSE2
SSE2        2001   128b    2x double, 16x byte, full integer SIMD
SSE4.1      2007   128b    PMULLD, DPPS, BLENDVPS, PTEST, INSERTPS
SSE4.2      2008   128b    PCMPESTRI/PCMPISTRM (string processing), CRC32
AVX         2011   256b    256-bit float (VEX encoding, non-destructive 3-operand)
AVX2        2013   256b    256-bit integer (VPGATHERDD, VPERMD, VPSHUFB 256b)
AVX-512     2016   512b    64-byte vectors, 8 mask registers (k0-k7), embedded broadcast, rounding
AVX10.1     2024   256b*   AVX-512 instruction set at 256-bit minimum width, uniform across P/E cores
AVX10.2     2025   256b+   BF16/FP16 arithmetic, minmax with NaN control, YMM convert
```

*AVX10 decouples the AVX-512 instruction set from 512-bit execution width. Intel Arrow Lake E-cores support AVX10.1/256, while P-cores support AVX10.1/512. This resolves the asymmetric-SIMD problem that plagued Alder Lake.

**Key instruction categories:**

**Data movement and shuffles:**
```
VMOVDQU64 zmm1, [mem]         // Unaligned 512-bit load
VMOVDQA64 zmm1, [mem]         // Aligned 512-bit load (faults on unaligned)
VPSHUFB   ymm1, ymm2, ymm3   // Byte shuffle (LUT lookup) -- the Swiss army knife
VPERMD    ymm1, ymm2, ymm3   // Cross-lane 32-bit permute
VPERMB    zmm1, zmm2, zmm3   // Byte permute (AVX-512 VBMI)
VSHUFPS   ymm1, ymm2, ymm3   // Shuffle 32-bit floats with immediate control
VBROADCASTSD ymm1, xmm2      // Broadcast scalar to all lanes
```

**Arithmetic:**
```
VPADDD    zmm1, zmm2, zmm3   // 16x 32-bit integer add
VPMULLD   zmm1, zmm2, zmm3   // 16x 32-bit integer multiply (low 32 bits)
VPMADD52LUQ zmm1, zmm2, zmm3 // 52-bit integer FMA (IFMA) -- used for big-number crypto
VFMADD231PS zmm1, zmm2, zmm3 // 16x fused multiply-add (a*b+c) single-precision
VPDPBUSD  zmm1, zmm2, zmm3   // Dot product of uint8*int8 accumulated to int32 (VNNI)
VPDPWSSD  zmm1, zmm2, zmm3   // Dot product of int16*int16 accumulated to int32 (VNNI)
```

**Comparison and masking (AVX-512):**
```
VPCMPD    k1{k2}, zmm1, zmm2, imm8  // Compare 32-bit ints, result in mask register
VPTESTMD  k1, zmm1, zmm2            // Test packed 32-bit, set mask where AND != 0
KMOVW     k1, eax                    // Move GPR to mask register
KANDW     k1, k2, k3                 // AND mask registers
KNOTW     k1, k2                     // NOT mask register
VMOVDQU32 zmm1{k1}{z}, [mem]        // Masked load: only load lanes where k1 bits are set
                                     // {z} = zero-masking, without {z} = merge-masking
```

**Gather/Scatter:**
```
VPGATHERDD zmm1{k1}, [rax + zmm2*4]    // Gather 32-bit ints from scattered addresses
VPSCATTERDD [rax + zmm2*4]{k1}, zmm1   // Scatter 32-bit ints to scattered addresses
```
Gather performance has improved dramatically: on Zen4, VPGATHERDD is ~3-4 cycles per element (8-element gather). On Intel Sapphire Rapids, dedicated gather hardware achieves similar throughput. Scatter is slower (~5-8 cycles per element) due to write conflicts.

**String processing (SSE4.2):**
```
PCMPESTRI xmm1, xmm2, imm8   // Packed compare explicit-length strings, return index
PCMPESTRM xmm1, xmm2, imm8   // Same, return mask
PCMPISTRI xmm1, xmm2, imm8   // Packed compare implicit-length strings (null-terminated)
```
Used by glibc's `strlen`, `strcmp`, `strstr`. The `imm8` controls comparison mode: equal-any (character set membership), ranges (character ranges), equal-each (string compare), equal-ordered (substring search). Processes 16 bytes per instruction.

**Conflict detection (AVX-512CD):**
```
VPCONFLICTD zmm1, zmm2        // For each lane, set bits indicating earlier lanes with same value
```
Critical for vectorizing histograms and scatter-with-conflicts. Without this, parallel histogram updates require serial execution.

**AVX-512 frequency throttling:**
Intel CPUs (Skylake-SP through Ice Lake) reduce core frequency when executing 512-bit instructions. Three license levels:
- L0 (base): normal frequency
- L1 (AVX2 heavy): ~100-200 MHz reduction
- L2 (AVX-512 heavy): ~200-400 MHz reduction

On Sapphire Rapids+, L2 penalty is significantly reduced. On Zen4 (AMD), there is NO frequency penalty for AVX-512 (AMD implements 512-bit as two 256-bit halves in the execution units but does not throttle frequency).

**Performance considerations:**
- Transition penalties between SSE and AVX states were a major problem on Haswell-Broadwell (the "AVX-SSE transition penalty"). Solved since Skylake with VEX-encoded forms (VZEROUPPER still recommended at function boundaries)
- Memory alignment: unaligned loads/stores have near-zero penalty on modern CPUs (since Nehalem for Intel, since Zen1 for AMD), but crossing cache line boundaries (64B) or page boundaries (4KB) still costs ~3-10 extra cycles
- Port pressure: AVX-512 instructions often require specific execution ports. Intel Sapphire Rapids has 2 FMA units at 512-bit, but only 1 shuffle unit at 512-bit

### 3.2 AArch64: NEON, SVE, SVE2, SME

**NEON (Advanced SIMD):**
- Fixed 128-bit vectors in V0-V31 registers
- Operates on 8/16/32/64-bit integer and 16/32/64-bit float elements
- Key instructions:

```
LDR   Q0, [X1]              // Load 128-bit vector
LD1   {V0.4S}, [X1]         // Load 4x 32-bit single-precision
LD2   {V0.4S, V1.4S}, [X1]  // Load interleaved (de-interleave 2 streams)
LD4   {V0.4S-V3.4S}, [X1]   // Load interleaved (de-interleave 4 streams -- AoS to SoA!)
TBL   V0.16B, {V1.16B}, V2.16B  // Byte lookup table (like PSHUFB)
ADDV  S0, V1.4S             // Horizontal add across all lanes -> scalar
SADDLV D0, V1.4S            // Widening horizontal add (4x32 -> 64)
FMLA  V0.4S, V1.4S, V2.S[0]    // Fused multiply-accumulate, lane broadcast
SMULL V0.4S, V1.4H, V2.4H  // Signed widening multiply (4x16 -> 4x32)
USHR  V0.4S, V1.4S, #5     // Unsigned shift right (all lanes)
CMGT  V0.4S, V1.4S, V2.4S  // Compare greater-than, result is mask (all-ones or all-zeros)
```

**SVE (Scalable Vector Extension) / SVE2:**
- Vector length agnostic (VLA): code written once works across implementations with 128b to 2048b vectors
- Hardware implementations: Fujitsu A64FX (512b), Neoverse V1/V2 (256b/128b), Apple M4 (128b NEON only, no SVE), Graviton3/4 (256b SVE2)
- 16 predicate registers (P0-P15) for per-lane masking
- First-fault loads for safe speculative access
- Gather/scatter with full hardware support
- Loop control: `WHILELT`, `INCP`, `BRKA` for predicate-driven loop management

```
// SVE key instructions:
LD1W   {Z0.S}, P0/Z, [X1]         // Predicated load (only load where P0 is true)
ST1W   {Z0.S}, P0, [X1]           // Predicated store
ADD    Z0.S, Z1.S, Z2.S           // Vector add (scalable width)
FMLA   Z0.S, P0/M, Z1.S, Z2.S    // Predicated fused multiply-add (merging)
WHILELT P0.S, X0, X1              // Set predicate lanes where loop index < limit
INCP   X0, P0.S                   // Increment X0 by count of true predicate lanes
LD1W   {Z0.S}, P0/Z, [X1, Z2.S, UXTW #2]  // Gather load (scatter addressing)
COMPACT Z0.S, P0, Z1.S            // Compact: collect active lanes contiguously
SPLICE Z0.S, P0, Z1.S, Z2.S      // Splice two vectors using predicate as cut point
CLASTA  X0, P0, X0, Z1.S          // Conditional extract: last active element
MATCH   P0.H, P1/Z, Z0.H, Z1.H   // SVE2: find matching elements (string search!)
BDEP   Z0.S, Z1.S, Z2.S           // SVE2 bitmanip: bit deposit (like PDEP)
BEXT   Z0.S, Z1.S, Z2.S           // SVE2 bitmanip: bit extract (like PEXT)
```

**SVE VLA programming model:**
```c
// Example: VLA vector addition (works for ANY vector length)
void vec_add(float *a, float *b, float *c, int n) {
    int i = 0;
    svbool_t pg;
    while (svptest_first(svptrue_b32(), pg = svwhilelt_b32(i, n))) {
        svfloat32_t va = svld1_f32(pg, &a[i]);
        svfloat32_t vb = svld1_f32(pg, &b[i]);
        svst1_f32(pg, &c[i], svadd_f32_x(pg, va, vb));
        i += svcntw();  // Increment by hardware vector length
    }
}
```

**SME (Scalable Matrix Extension) -- ARMv9.2, 2023+:**
- Adds ZA tile register: a square matrix of (SVL/8)x(SVL/8) bytes
- Streaming SVE mode (SMSTART/SMSTOP): separate execution mode optimized for matrix ops
- Outer product instructions: `FMOPA ZA0.S, P0/M, Z0.S, Z1.S` -- rank-1 update of tile
- SME2 (2024): multi-vector instructions operating on groups of 2/4 SVE vectors, LUTI2/LUTI4 (lookup table), quantized INT8 outer products
- Graviton4 reportedly implements SME2

### 3.3 RISC-V: V Extension (RVV)

**Ratified in RISC-V V extension 1.0 (2021), widely available 2024+:**

- Similar to SVE: vector-length agnostic (VLEN is implementation-defined, 128b to 65536b)
- 32 vector registers V0-V31
- `vsetvli` / `vsetivli` / `vsetvl` instructions configure the vector unit dynamically:
  - SEW (Selected Element Width): 8/16/32/64 bits
  - LMUL (Length MULtiplier): 1/2/4/8 (or fractional 1/2, 1/4, 1/8) -- groups registers for longer vectors
  - VL (Vector Length): actual number of elements to process

```
// RVV key instructions:
vsetvli  t0, a0, e32, m4, ta, ma   // Set vector length: 32-bit elements, LMUL=4, tail-agnostic, mask-agnostic
vle32.v  v0, (a1)                   // Vector load 32-bit elements
vse32.v  v0, (a2)                   // Vector store
vadd.vv  v4, v0, v8                 // Vector add (vector + vector)
vadd.vx  v4, v0, t1                 // Vector add (vector + scalar broadcast)
vadd.vi  v4, v0, 5                  // Vector add (vector + immediate)
vfmacc.vv v0, v4, v8               // FP fused multiply-accumulate (v0 += v4 * v8)
vmseq.vv v0, v4, v8                // Set mask where equal
vluxei32.v v4, (a1), v8            // Gather: indexed load using 32-bit indices
vsuxei32.v v4, (a1), v8            // Scatter: indexed store
vslidedown.vx v4, v0, t0           // Slide elements down (cross-lane shift)
vrgather.vv v4, v0, v8             // General permute (like VPERM)
vcompress.vm v4, v0, v8            // Compress: collect masked elements (like SVE COMPACT)
vredsum.vs v0, v4, v8              // Reduction: sum all elements
vfredosum.vs v0, v4, v8            // Ordered FP reduction (deterministic)
```

**RVV LMUL register grouping:**
With LMUL=4, vector operations use groups of 4 registers (v0-v3 as one operand, v4-v7 as another). This effectively quadruples the vector length at the cost of reducing available register groups from 32 to 8.

**Implementations:**
- SiFive P870/X390: RVV 1.0, VLEN=256 (expected 2025 silicon)
- T-Head C920: RVV 1.0, VLEN=128
- SpacemiT K1/X60: RVV 1.0, VLEN=256 (BananaPi BPI-F3)
- Tenstorrent Ascalon: RVV 1.0 (expected 2025-2026)
- Ventana Veyron V2: RVV 1.0, VLEN=128 (server-class, 2024)

### 3.4 SIMD Usage in Real Systems

**Database engines (columnar scan, filtering, aggregation):**
- DuckDB: extensive use of AVX2 for columnar operations; `FilterExecutor` uses SIMD comparison + mask extraction for WHERE clauses; ALP (Adaptive Lossless floating-Point compression) uses SIMD for encode/decode
- ClickHouse: AVX2/AVX-512 for string operations (LIKE, hasToken), hash computation, aggregation kernels
- Velox (Meta): SIMD-optimized dictionary decoding, Parquet delta decoding, filter evaluation
- DataFusion: leverages Arrow's SIMD-optimized compute kernels

**String processing:**
- simdjson: parses JSON at >2 GB/s using AVX2/NEON; `VPSHUFB` for classifying characters, `VPMOVMSKB` for extracting structural character positions
- simdutf: UTF-8/UTF-16 validation and transcoding at 10+ GB/s
- Hyperscan (Intel): regex matching using AVX-512 and the `VPCMPESTRI`-family approach

**Compression:**
- LZ4/ZSTD: SIMD for match finding (hash computation), literal copy
- Apache Arrow: SIMD for dictionary encoding/decoding, run-length decoding, null bitmap operations

---

## 4. Cache Control

### 4.1 Prefetch Instructions

| ISA | Instruction | Effect | Typical Use |
|-----|-------------|--------|-------------|
| x86-64 | `PREFETCHT0` | Prefetch to L1/L2/L3 | Pointer chasing, B-tree traversal |
| x86-64 | `PREFETCHT1` | Prefetch to L2/L3 (skip L1) | Streaming data ahead of consumption |
| x86-64 | `PREFETCHT2` | Prefetch to L3 only | Far-ahead prefetch |
| x86-64 | `PREFETCHNTA` | Prefetch non-temporal (minimize cache pollution) | Streaming reads, one-pass scans |
| x86-64 | `PREFETCHW` | Prefetch for write (request exclusive ownership) | Imminent write, reduces RFO latency |
| AArch64 | `PRFM PLDL1KEEP, [addr]` | Prefetch for load, L1, keep in cache | General prefetch |
| AArch64 | `PRFM PLDL1STRM, [addr]` | Prefetch for load, L1, streaming (don't pollute) | One-pass scan |
| AArch64 | `PRFM PSTL1KEEP, [addr]` | Prefetch for store, L1, keep | Write prefetch |
| AArch64 | `PRFM PLDL2KEEP, [addr]` | Prefetch for load, L2, keep | Farther-ahead prefetch |
| RISC-V | `prefetch.r offset(rs1)` | Prefetch for read (Zicbop extension) | Read prefetch |
| RISC-V | `prefetch.w offset(rs1)` | Prefetch for write (Zicbop extension) | Write prefetch |
| RISC-V | `prefetch.i offset(rs1)` | Prefetch for instruction fetch | JIT code prefetch |

**Software prefetching in practice:**

In B-tree traversal (as in database index lookups), software prefetch is critical. When descending a B-tree, the next node to visit depends on the key comparison at the current node. By the time you know which child to visit, the memory access is on the critical path.

```c
// Prefetch-optimized B-tree lookup (used in production databases)
void btree_lookup(Node *root, Key key) {
    Node *node = root;
    while (!node->is_leaf) {
        int pos = binary_search(node->keys, node->num_keys, key);
        Node *child = node->children[pos];
        // Prefetch the child we're about to visit
        __builtin_prefetch(child, 0, 3);  // read, high temporal locality
        // Also prefetch the grandchild (speculative -- might be wrong)
        if (child->children[0])
            __builtin_prefetch(child->children[0], 0, 2);
        node = child;
    }
}
```

Group prefetching (Masstree, ART): instead of processing one lookup at a time, batch multiple lookups and issue prefetches for all of them before processing any. This fills the memory pipeline and hides latency.

**Hardware prefetcher interaction:**
Modern CPUs have aggressive hardware prefetchers (L1 stream prefetcher, L2 stride prefetcher, LLC spatial prefetcher). Software prefetch can conflict with or complement hardware prefetchers:
- Sequential scan: hardware prefetcher handles this well; software prefetch adds little value
- Pointer chasing: hardware prefetcher cannot predict; software prefetch is essential
- Random access with known pattern: software prefetch is the only option

### 4.2 Cache Line Flush and Writeback

| ISA | Instruction | Effect | Persistence Guarantee |
|-----|-------------|--------|----------------------|
| x86-64 | `CLFLUSH [addr]` | Invalidate cache line from all levels. Serializing. | Written to memory |
| x86-64 | `CLFLUSHOPT [addr]` | Like CLFLUSH but weakly ordered (can be reordered with other CLFLUSHOPTs) | Written to memory, needs SFENCE for ordering |
| x86-64 | `CLWB [addr]` | Write back cache line but retain clean copy in cache | Written to memory/persistence domain, needs SFENCE |
| x86-64 | `CLDEMOTE [addr]` | Move cache line to a lower cache level (L1->L3) | No flush, just priority demotion |
| AArch64 | `DC CIVAC, Xt` | Clean and Invalidate by VA to PoC (Point of Coherency) | Flushed to memory |
| AArch64 | `DC CVAC, Xt` | Clean by VA to PoC (writeback, retain clean copy) | Flushed to memory |
| AArch64 | `DC CVAP, Xt` | Clean by VA to PoP (Point of Persistence) -- ARMv8.2 | Flushed to persistence domain |
| AArch64 | `DC CVADP, Xt` | Clean by VA to PoDP (Point of Deep Persistence) -- ARMv8.5 | Flushed to deepest persistence domain |
| AArch64 | `DC IVAC, Xt` | Invalidate by VA (discard without writeback) -- privileged | Data loss! Privileged only |
| AArch64 | `DC ZVA, Xt` | Zero entire cache line (without read-for-ownership) | Allocates zeroed line |
| RISC-V | `cbo.clean addr` | Clean cache block (writeback) -- Zicbom extension | Flushed to next level |
| RISC-V | `cbo.flush addr` | Flush cache block (writeback + invalidate) -- Zicbom | Flushed to memory |
| RISC-V | `cbo.inval addr` | Invalidate cache block (may discard dirty data) -- Zicbom | Dangerous: may lose data |
| RISC-V | `cbo.zero addr` | Zero cache block (allocate + zero) -- Zicboz | Zeroed in cache |

**Persistent memory (PMEM) / CXL implications:**
CLWB + SFENCE is the canonical sequence for ensuring durability to persistent memory (Intel Optane DC PMEM, CXL 2.0 memory). The eADR (extended Asynchronous DRAM Refresh) feature on Intel platforms guarantees that data in the CPU write-pending queue is flushed to PMEM on power failure, making SFENCE alone sufficient (no CLWB needed). Without eADR, the sequence is:

```
store [addr], data
CLWB [addr]           // Write back to persistence domain
SFENCE                // Ensure CLWB completes
// Now data is durable
```

ARM's `DC CVAP` (Clean to Point of Persistence) is the equivalent for ARM-based persistent memory systems.

### 4.3 Non-Temporal Stores

| ISA | Instruction | Effect |
|-----|-------------|--------|
| x86-64 | `MOVNTI [mem], reg` | Store 32/64-bit integer, bypass cache (write-combining) |
| x86-64 | `MOVNTDQ [mem], xmm/ymm/zmm` | Store 128/256/512-bit vector, bypass cache |
| x86-64 | `MOVNTPS [mem], xmm/ymm/zmm` | Store packed single-precision, bypass cache |
| x86-64 | `MOVNTDQA xmm, [mem]` | Non-temporal load from WC memory (SSE4.1) |
| AArch64 | `STNP Xt, Xt2, [addr]` | Store pair, non-temporal hint (advisory, may be ignored) |
| AArch64 | `LDNP Xt, Xt2, [addr]` | Load pair, non-temporal hint |
| RISC-V | `ntstore` (proposed) | No standard NT store yet; implementation-defined via custom extensions |

**When to use non-temporal stores:**
- Writing large buffers that won't be read again soon (memset of large allocations, log writes)
- Streaming writes in ETL pipelines, checkpointing
- Avoiding cache pollution in multi-tenant / mixed workloads

**Gotchas:**
- x86 NT stores are weakly ordered (break TSO). SFENCE required before any subsequent operation that depends on the stored data being visible
- NT stores must write full cache lines (64 bytes) to be efficient; partial writes cause expensive read-modify-write on the WC buffer
- On ARM, STNP is merely a hint -- the hardware may ignore it entirely. Behavior varies significantly between implementations (Apple vs Cortex vs Neoverse)

---

## 5. Atomics and Lock-Free Instructions

### 5.1 x86-64 Atomics

**The LOCK prefix:**
When applied to a read-modify-write instruction, LOCK ensures the operation is atomic with respect to all processors. Microarchitecturally:
1. If the cache line is already in Modified/Exclusive state in L1, the LOCK is handled entirely in the cache (no bus lock) -- this is the common "cache lock" optimization since Pentium Pro
2. If the line is Shared or Invalid, the core must acquire exclusive ownership (RFO -- Request For Ownership) and hold it during the RMW
3. Split-lock (crossing cache line boundary): triggers a bus lock, which is catastrophically expensive (~100x slower). Intel Sapphire Rapids+ can generate #AC exception on split locks (controlled via MSR)

```
// Atomic instructions (all require LOCK prefix for atomicity):
LOCK XADD  [mem], reg         // Atomic fetch-and-add. Returns old value.
LOCK CMPXCHG [mem], reg       // Compare-and-exchange (CAS). Compare RAX with [mem];
                               // if equal, store reg to [mem]. Sets ZF on success.
LOCK CMPXCHG8B [mem]          // 64-bit CAS (EDX:EAX expected, ECX:EBX desired)
LOCK CMPXCHG16B [mem]         // 128-bit CAS (RDX:RAX expected, RCX:RBX desired)
                               // Critical for lock-free double-word operations
LOCK XCHG  [mem], reg         // Atomic swap. XCHG always has implicit LOCK.
LOCK BTS   [mem], imm/reg     // Atomic bit test-and-set (returns old bit value)
LOCK BTC   [mem], imm/reg     // Atomic bit test-and-complement
LOCK BTR   [mem], imm/reg     // Atomic bit test-and-reset
LOCK ADD   [mem], imm/reg     // Atomic add (no return value, cheaper than XADD)
LOCK INC   [mem]              // Atomic increment
LOCK DEC   [mem]              // Atomic decrement
```

**CMPXCHG16B (Double-Width CAS):**
Essential for lock-free data structures that need to atomically update two adjacent 64-bit words (e.g., pointer + counter to solve the ABA problem, or lock-free deque with head + tail):
```c
// Lock-free stack with ABA counter
struct Node { Node* next; };
struct TaggedPtr { Node* ptr; uint64_t tag; } __attribute__((aligned(16)));

bool cas128(TaggedPtr *target, TaggedPtr expected, TaggedPtr desired) {
    return __sync_bool_compare_and_swap((__int128*)target,
                                         *(__int128*)&expected,
                                         *(__int128*)&desired);
}
```
Note: CMPXCHG16B requires 16-byte alignment. Misaligned access triggers #GP.

### 5.2 AArch64 Atomics

**LL/SC (Load-Linked / Store-Conditional) -- ARMv8.0:**
```
// Load-exclusive / Store-exclusive (LL/SC paradigm)
LDXR   W0, [X1]          // Load-exclusive 32-bit (marks exclusive monitor)
STXR   W2, W0, [X1]      // Store-exclusive 32-bit (W2 = 0 if success, 1 if fail)
LDXP   X0, X1, [X2]      // Load-exclusive pair (128-bit atomic load)
STXP   W3, X0, X1, [X2]  // Store-exclusive pair (128-bit atomic store)

// With ordering:
LDAXR  W0, [X1]          // Load-acquire exclusive
STLXR  W2, W0, [X1]      // Store-release exclusive

// CAS loop example (pre-LSE):
retry:
    LDAXR  W0, [X1]       // Load current value (acquire)
    ADD    W3, W0, #1     // Compute new value
    STLXR  W2, W3, [X1]   // Try to store (release)
    CBNZ   W2, retry      // Retry if store-exclusive failed
```

**LSE (Large System Extensions) -- ARMv8.1, mandatory in ARMv8.2+:**
LL/SC loops have two problems at scale: (1) spurious failures under contention, (2) livelock on large systems where cache line bouncing causes perpetual exclusive-monitor loss. LSE adds single-instruction atomic RMW operations:

```
CAS    W0, W1, [X2]       // Compare-and-swap: if [X2]==W0, then [X2]=W1
CASP   X0, X1, X2, X3, [X4]  // CAS pair (128-bit CAS)
CASA   W0, W1, [X2]       // CAS with acquire
CASAL  W0, W1, [X2]       // CAS with acquire + release (seq-cst)
SWP    W0, W1, [X2]       // Atomic swap: W1 = old [X2], [X2] = W0
SWPA   W0, W1, [X2]       // Swap with acquire
LDADD  W0, W1, [X2]       // Atomic load-add: W1 = old [X2], [X2] += W0
LDADDA W0, W1, [X2]       // Load-add with acquire
LDADDAL W0, W1, [X2]      // Load-add with acquire + release
STADD  W0, [X2]           // Atomic add (no return value, like LOCK ADD)
LDCLR  W0, W1, [X2]       // Atomic load-and-clear-bits (AND NOT)
LDSET  W0, W1, [X2]       // Atomic load-and-set-bits (OR)
LDEOR  W0, W1, [X2]       // Atomic load-and-XOR
LDSMAX W0, W1, [X2]       // Atomic signed max
LDSMIN W0, W1, [X2]       // Atomic signed min
LDUMAX W0, W1, [X2]       // Atomic unsigned max
LDUMIN W0, W1, [X2]       // Atomic unsigned min
```

**LSE2 (ARMv8.4):**
- Guarantees that naturally-aligned 64-bit loads/stores within a 16-byte aligned pair are single-copy atomic
- Enables CASP and LDXP/STXP to work without strict 16-byte alignment restrictions

**FEAT_LSE128 (ARMv9.4):**
- 128-bit single-copy atomic loads/stores and CAS without needing LL/SC:
- `LDCLRP`, `LDSETP`, `SWPP` -- 128-bit atomic operations

**FEAT_LRCPC3 (ARMv8.9):**
- `LDIAPP` / `STILP`: load-acquire-pair / store-release-pair with RCpc semantics for better performance on release-consistent workloads

**Performance comparison LL/SC vs LSE:**
On Apple M1/M2, LSE atomics are ~2-3x faster than LL/SC loops under contention. On Neoverse N2/V2, the difference is even larger (~3-5x) because the LL/SC exclusive monitor is more conservative on server designs. Linux kernel and glibc detect LSE at runtime via HWCAP.

### 5.3 RISC-V Atomics

**A extension (RV64A) -- LR/SC:**
```
lr.w      rd, (rs1)        // Load-reserved word (sets reservation)
sc.w      rd, rs2, (rs1)   // Store-conditional word (rd=0 if success)
lr.d      rd, (rs1)        // Load-reserved doubleword (RV64)
sc.d      rd, rs2, (rs1)   // Store-conditional doubleword

// With ordering bits:
lr.w.aq   rd, (rs1)        // Load-reserved with acquire
sc.w.rl   rd, rs2, (rs1)   // Store-conditional with release
lr.w.aqrl rd, (rs1)        // Load-reserved sequentially consistent
```

**A extension -- AMO (Atomic Memory Operations):**
```
amoadd.w  rd, rs2, (rs1)   // Atomic fetch-and-add word
amoadd.d  rd, rs2, (rs1)   // Atomic fetch-and-add doubleword
amoswap.w rd, rs2, (rs1)   // Atomic swap
amoand.w  rd, rs2, (rs1)   // Atomic AND
amoor.w   rd, rs2, (rs1)   // Atomic OR
amoxor.w  rd, rs2, (rs1)   // Atomic XOR
amomax.w  rd, rs2, (rs1)   // Atomic signed max
amomaxu.w rd, rs2, (rs1)   // Atomic unsigned max
amomin.w  rd, rs2, (rs1)   // Atomic signed min
amominu.w rd, rs2, (rs1)   // Atomic unsigned min
```

**Zacas extension (ratified 2024):**
Adds CAS instructions that RISC-V was originally missing:
```
amocas.w  rd, rs2, (rs1)      // 32-bit compare-and-swap
amocas.d  rd, rs2, (rs1)      // 64-bit compare-and-swap
amocas.q  rd, rs2, (rs1)      // 128-bit compare-and-swap (RV64 only)
// rd holds expected value (overwritten with actual value on failure)
// rs2 holds desired value
// With .aq, .rl, .aqrl ordering
```

**Zabha extension (ratified 2024):**
Adds byte and halfword atomic operations:
```
amoadd.b  rd, rs2, (rs1)   // Atomic byte add
amoadd.h  rd, rs2, (rs1)   // Atomic halfword add
amoswap.b rd, rs2, (rs1)   // Atomic byte swap
amocas.b  rd, rs2, (rs1)   // Byte CAS (requires Zacas + Zabha)
amocas.h  rd, rs2, (rs1)   // Halfword CAS
```
These are critical for compact lock-free data structures (e.g., per-byte flags in concurrent hash tables).

### 5.4 Lock-Free Data Structure Patterns

**ABA Problem and Solutions:**
```
// x86: CMPXCHG16B for tagged pointer
struct TaggedPtr {
    void *ptr;
    uint64_t tag;  // Monotonically increasing counter
};
// CAS on the full 128-bit value -- tag prevents ABA

// ARM: CASP (128-bit CAS via LSE)
CASP X0, X1, X2, X3, [X4]  // Compare X0:X1 with [X4], store X2:X3 if equal

// RISC-V: amocas.q (128-bit CAS via Zacas)
amocas.q rd, rs2, (rs1)     // 128-bit CAS
```

**Ticket lock (simple, fair, scalable with proportional backoff):**
```
// x86: LOCK XADD for ticket acquisition
LOCK XADD [ticket_counter], 1     // Atomically get-and-increment ticket
// Spin until serving_counter == my_ticket

// ARM (LSE): LDADDAL for ticket acquisition
LDADDAL W0, W1, [X2]              // Atomically add 1, get old value with seq-cst

// RISC-V: amoadd.w.aqrl
amoadd.w.aqrl a0, a1, (a2)        // Atomically add, get old value
```

**MCS Lock (cache-line-local spinning):**
Each waiter spins on its own cache line, avoiding the thundering-herd problem of test-and-set locks. Requires CAS for enqueue and store-release for handoff.

---

## 6. Bit Manipulation

### 6.1 Population Count, Leading/Trailing Zeros

| Operation | x86-64 | AArch64 | RISC-V (Zbb) |
|-----------|--------|---------|---------------|
| Population count (# of set bits) | `POPCNT r64, r/m64` | `CNT Vn.8B, Vm.8B` (NEON) then reduce, or `FMOV` + `CNT` trick | `cpop rd, rs1` |
| Count leading zeros | `LZCNT r64, r/m64` (ABM/LZCNT) | `CLZ Xd, Xn` | `clz rd, rs1` |
| Count trailing zeros | `TZCNT r64, r/m64` (BMI1) | `RBIT Xd, Xn` then `CLZ` | `ctz rd, rs1` |
| Bit reverse | No single instruction | `RBIT Xd, Xn` | `rev8 rd, rs1` (byte-reverse only) |
| Byte reverse (endian swap) | `BSWAP r64` | `REV Xd, Xn` (64-bit), `REV16/REV32` | `rev8 rd, rs1` |
| Find first set (1-indexed) | `BSF r64, r/m64` (legacy, undefined for 0 input) | `CLZ` then subtract from 63 | `ctz` + 1 |

**x86 POPCNT gotcha:** On Sandy Bridge through Haswell, POPCNT has a false dependency on the destination register. The CPU thinks the output depends on the old value of the destination. Workaround: `xor ecx, ecx; popcnt ecx, eax` or compiler intrinsic with `-mpopcnt`.

**AArch64 POPCNT equivalent:**
AArch64 lacks a scalar POPCNT. The canonical sequence:
```
FMOV D0, X0           // Move GPR to SIMD register
CNT  V0.8B, V0.8B     // Count bits in each byte (8 results)
ADDV B0, V0.8B        // Horizontal add of 8 bytes -> total popcount
FMOV W0, S0           // Move result back to GPR
```
This is 4 instructions vs 1 on x86. Some ARM implementations (Apple M-series) may fuse this sequence.

### 6.2 Bit Extract and Deposit (BMI2 / SVE2)

| Operation | x86-64 (BMI2) | AArch64 (SVE2 bitmanip) | RISC-V (Zbe -- proposed) |
|-----------|---------------|------------------------|------------------------|
| Parallel bit extract | `PEXT r64, r64, r/m64` | `BEXT Z, Z, Z` (SVE2) | `bext` (Zbe, not yet ratified) |
| Parallel bit deposit | `PDEP r64, r64, r/m64` | `BDEP Z, Z, Z` (SVE2) | `bdep` (Zbe, not yet ratified) |

**PEXT/PDEP explained:**
```
PEXT: Extract bits from src where mask bits are 1, pack contiguously into dst
  src  = 0b_1010_1100_0011_0101
  mask = 0b_1111_0000_1111_0000
  dst  = 0b_0000_0000_1010_0011  (extracted bits packed right-to-left)

PDEP: Deposit contiguous bits from src into positions where mask bits are 1
  src  = 0b_0000_0000_1010_0011
  mask = 0b_1111_0000_1111_0000
  dst  = 0b_1010_0000_0011_0000  (deposited into mask positions)
```

**AMD Zen1/Zen2 PDEP/PEXT microcoded -- extremely slow (~18-300 cycles depending on popcount of mask).** Zen3+ implements in hardware (~3 cycles). Intel has always had fast PDEP/PEXT (~3 cycles since Haswell). This led to algorithms like "PEXT-based perfect hashing" being fast on Intel but unusable on AMD Zen1/2. Chess engines (Stockfish) notably had to maintain two code paths.

### 6.3 Other Bit Manipulation

| Operation | x86-64 | AArch64 | RISC-V |
|-----------|--------|---------|--------|
| Bit field extract | `BEXTR r64, r/m64, r64` (BMI1) | `UBFX Xd, Xn, #lsb, #width` | `Zbs: bext rd, rs1, rs2` (single bit) |
| Bit test and set | `BTS r/m, r/imm` | `UBFX` + `ORR` + `BFI` | `bset rd, rs1, rs2` (Zbs) |
| Bit test and clear | `BTR r/m, r/imm` | `UBFX` + `BIC` + `BFI` | `bclr rd, rs1, rs2` (Zbs) |
| Bit test and invert | `BTC r/m, r/imm` | `UBFX` + `EOR` + `BFI` | `binv rd, rs1, rs2` (Zbs) |
| Single-bit extract | `BT r/m, r/imm` (sets CF) | `TBNZ`/`TBZ` (branch on bit) | `bext rd, rs1, rs2` (Zbs) |
| Reset lowest set bit | `BLSR r64, r/m64` (BMI1) | `SUB` + `AND` | — |
| Extract lowest set bit | `BLSI r64, r/m64` (BMI1) | `NEG` + `AND` | — |
| Set all bits below lowest set | `BLSMSK r64, r/m64` (BMI1) | `SUB` + `EOR` | — |
| Zero high bits | `BZHI r64, r/m64, r64` (BMI2) | `UBFX` | — |
| Rotate | `ROR/ROL r, imm/cl` | `ROR Xd, Xn, #imm` / `RORV` | `ror rd, rs1, rs2` (Zbb), `rori` |
| Shift-and-add | — | `ADD Xd, Xn, Xm, LSL #n` (barrel shifter!) | `sh1add/sh2add/sh3add` (Zba) |
| OR-combine | — | `ORN Xd, Xn, Xm` (OR-NOT) | `orn rd, rs1, rs2` (Zbb) |
| AND-NOT | `ANDN r64, r64, r/m64` (BMI1) | `BIC Xd, Xn, Xm` (AND-NOT) | `andn rd, rs1, rs2` (Zbb) |
| Max/Min (integer) | — | — | `max/maxu/min/minu` (Zbb) |
| Sign/Zero extend | `MOVSX/MOVZX` | `SXTB/SXTH/SXTW/UXTB/UXTH` | `sext.b/sext.h/zext.h` (Zbb) |

**AArch64's barrel shifter advantage:**
Every ARM data-processing instruction can include a free shift/rotate on one operand:
```
ADD X0, X1, X2, LSL #3    // X0 = X1 + (X2 << 3) -- single instruction, single cycle
```
This is enormously useful for array indexing (`base + index * 8`), hash computation, and tree navigation. x86 has the LEA instruction (`lea rax, [rbx + rcx*8]`) which serves a similar purpose but only supports scales of 1, 2, 4, 8.

**RISC-V Zba (address generation):**
```
sh1add rd, rs1, rs2    // rd = (rs1 << 1) + rs2
sh2add rd, rs1, rs2    // rd = (rs1 << 2) + rs2
sh3add rd, rs1, rs2    // rd = (rs1 << 3) + rs2
add.uw rd, rs1, rs2    // rd = zero_extend(rs1[31:0]) + rs2
sh1add.uw              // rd = (zero_extend(rs1) << 1) + rs2
slli.uw rd, rs1, shamt // rd = zero_extend(rs1) << shamt
```
These map directly to array indexing with element sizes of 2, 4, 8 bytes -- critical for eliminating multi-instruction sequences in pointer arithmetic.

### 6.4 Real-World Bit Manipulation Usage

**Bitmap indexes (database systems):**
```c
// POPCNT for counting set bits in a bitmap index (e.g., null bitmap in Arrow)
uint64_t count_nulls(uint64_t *bitmap, int n_words) {
    uint64_t count = 0;
    for (int i = 0; i < n_words; i++)
        count += __builtin_popcountll(bitmap[i]);
    return count;
}

// TZCNT for iterating set bits (finding non-null positions)
while (word != 0) {
    int pos = __builtin_ctzll(word);   // TZCNT
    process(base + pos);
    word &= word - 1;                  // BLSR: clear lowest set bit
}
```

**Cuckoo hashing (using PEXT for bucket indexing):**
```c
// Hash to bucket using PEXT to extract specific bits
uint32_t bucket = _pext_u64(hash_value, bucket_mask);
```

**Chess engines (bitboard manipulation):**
Stockfish uses POPCNT, LZCNT, TZCNT, PEXT extensively for move generation on 64-bit bitboards representing the chessboard.

---

## 7. Cryptography Acceleration

### 7.1 AES Acceleration

| ISA | Instructions | Rounds per instruction | Throughput |
|-----|-------------|----------------------|------------|
| x86-64 (AES-NI) | `AESENC`, `AESENCLAST`, `AESDEC`, `AESDECLAST`, `AESKEYGENASSIST`, `AESIMC` | 1 round | ~1 cycle/round (pipelined), ~0.5 bytes/cycle per core |
| x86-64 (VAES) | `VAESENC ymm/zmm` (AVX-512 VAES) | 1 round, 2x/4x parallel | ~4-8 blocks/cycle with 512-bit |
| AArch64 (ARMv8 Crypto) | `AESE`, `AESMC` (mix columns), `AESD`, `AESIMC` | 1 round (split: sub+shift, then mix) | ~1 round per 2 instructions |
| RISC-V (Zkne/Zknd) | `aes64es`, `aes64esm`, `aes64ds`, `aes64dsm`, `aes64ks1i`, `aes64ks2` | 1 round (RV64) | Implementation-dependent |

**x86 AES-NI detail:**
```
// AES-128 encryption (10 rounds)
MOVDQU     xmm0, [plaintext]      // Load 128-bit block
PXOR       xmm0, [round_key_0]    // Initial whitening
AESENC     xmm0, [round_key_1]    // Round 1
AESENC     xmm0, [round_key_2]    // Round 2
...                                 // Rounds 3-9
AESENCLAST xmm0, [round_key_10]   // Final round (no MixColumns)
MOVDQU     [ciphertext], xmm0     // Store result
```

With VAES (AVX-512), you can process 4 AES blocks in parallel:
```
VAESENC zmm0, zmm0, zmm1          // 4 blocks x 1 round simultaneously
```
Combined with pipelining across multiple registers (processing 8-16 blocks in flight), modern x86 CPUs achieve >30 GB/s AES-256-GCM per core.

### 7.2 SHA Acceleration

| ISA | Instructions | Algorithm |
|-----|-------------|-----------|
| x86-64 (SHA-NI) | `SHA1RNDS4`, `SHA1NEXTE`, `SHA1MSG1`, `SHA1MSG2` | SHA-1 |
| x86-64 (SHA-NI) | `SHA256RNDS2`, `SHA256MSG1`, `SHA256MSG2` | SHA-256 |
| x86-64 (SHA512) | `VSHA512RNDS2`, `VSHA512MSG1`, `VSHA512MSG2` | SHA-512 (AVX10.2/AVX-512SHA, 2024+) |
| AArch64 | `SHA1C`, `SHA1H`, `SHA1M`, `SHA1P`, `SHA1SU0`, `SHA1SU1` | SHA-1 |
| AArch64 | `SHA256H`, `SHA256H2`, `SHA256SU0`, `SHA256SU1` | SHA-256 |
| AArch64 | `SHA512H`, `SHA512H2`, `SHA512SU0`, `SHA512SU1` | SHA-512 (ARMv8.2-SHA) |
| AArch64 | `SM3SS1`, `SM3TT1A/1B`, `SM3TT2A/2B`, `SM3PARTW1/2` | SM3 (Chinese standard) |
| AArch64 | `SM4E`, `SM4EKEY` | SM4 (Chinese standard) |
| RISC-V (Zknh) | `sha256sig0/1`, `sha256sum0/1` | SHA-256 |
| RISC-V (Zknh) | `sha512sig0/1`, `sha512sum0/1` | SHA-512 |

### 7.3 CRC32 Acceleration

| ISA | Instruction | Polynomial |
|-----|-------------|------------|
| x86-64 (SSE4.2) | `CRC32 r32, r/m8/16/32/64` | CRC-32C (Castagnoli, iSCSI polynomial) |
| AArch64 | `CRC32B/H/W/X` | CRC-32 (ISO 3309 polynomial) |
| AArch64 | `CRC32CB/CH/CW/CX` | CRC-32C (Castagnoli) |
| RISC-V (Zbkc) | `clmul`, `clmulh` | Carryless multiply (build CRC from this) |

**CRC32 in practice:**
- PostgreSQL uses CRC-32C for WAL record checksums (hardware-accelerated on x86 via SSE4.2)
- RocksDB uses CRC-32C for block checksums
- iSCSI, NVMe use CRC-32C for data integrity
- Ethernet uses CRC-32 (different polynomial)

RISC-V's approach with `clmul` (carryless multiplication) is more general -- you can build any CRC polynomial from it, plus it's useful for GCM (Galois/Counter Mode) in AES-GCM.

### 7.4 Wider Crypto Extensions

**AArch64 additional:**
- `FEAT_SM4`: SM4 block cipher acceleration (Chinese national standard)
- `FEAT_SHA3`: SHA3 / Keccak acceleration
- `FEAT_RNG`: hardware random number generation (`RNDR`, `RNDRRS` instructions)

**x86-64 additional:**
- `RDRAND`: hardware random number from DRNG (Digital Random Number Generator)
- `RDSEED`: true entropy from hardware noise source
- `GFNI` (Galois Field New Instructions): `GF2P8MULB`, `GF2P8AFFINEQB` -- general GF(2^8) operations, useful for Reed-Solomon, custom S-boxes
- `VPCLMULQDQ` (CLMUL + AVX-512): carryless multiply for GCM at 512-bit width

**RISC-V scalar crypto (Zkn/Zks -- ratified 2023):**
- `Zbkb`: bit manipulation for crypto (brev8, zip/unzip, pack)
- `Zbkc`: carryless multiply (clmul/clmulh)
- `Zbkx`: crossbar permutations (xperm4/xperm8)
- `Zkne`/`Zknd`: AES encrypt/decrypt
- `Zknh`: SHA-256/SHA-512
- `Zksed`: SM4 (ShangMi)
- `Zksh`: SM3 (ShangMi hash)

**RISC-V vector crypto (Zvkn/Zvks -- ratified 2024):**
- `Zvkned`: Vector AES
- `Zvknhb`: Vector SHA-256/SHA-512
- `Zvkb`: Vector bit manipulation for crypto
- `Zvkg`: Vector GHASH (for GCM)
- `Zvksed`/`Zvksh`: Vector SM4/SM3
These operate on RVV registers, enabling high-throughput parallel crypto.

---

## 8. Branch and Control Flow Security

### 8.1 Speculation Barriers

| ISA | Instruction | Purpose |
|-----|-------------|---------|
| x86-64 | `LFENCE` | Serializes instruction dispatch; prevents speculative execution past this point (Spectre v1 mitigation) |
| x86-64 | `ENDBR64/ENDBR32` | CET-IBT: marks valid indirect branch target (NOP on non-CET hardware) |
| x86-64 | `INCSSPQ` / `RDSSPQ` | CET-SS: shadow stack manipulation |
| AArch64 | `CSDB` | Conditional Speculation Dependency Barrier: ensures result of conditional is resolved before subsequent data-dependent instructions |
| AArch64 | `SB` (ARMv8.5) | Speculation Barrier: prevents speculative execution of any subsequent instructions |
| AArch64 | `BTI {c|j|jc}` | Branch Target Identification: marks valid indirect branch targets (like ENDBR) |
| AArch64 | `PACIA/PACIB/PACDA/PACDB` | Pointer Authentication Code: sign a pointer using key A/B, data key A/B |
| AArch64 | `AUTIA/AUTIB/AUTDA/AUTDB` | Authenticate (verify) a signed pointer |
| AArch64 | `BRAA/BRAB` | Branch with pointer authentication (branch + verify in one instruction) |
| AArch64 | `RETAA/RETAB` | Return with pointer authentication (return + verify) |
| RISC-V | `fence.t` (proposed) | Temporal fence: speculation barrier (part of Zifencet, under development) |
| RISC-V | `Zicfiss` (ratified 2024) | Shadow Stack: `SSPUSH`, `SSPOPCHK`, `SSAMOSWAP` for return address protection |
| RISC-V | `Zicfilp` (ratified 2024) | Landing Pad: `LPAD` instruction marks valid indirect branch targets |

### 8.2 Pointer Authentication (ARM PAC) -- Deep Dive

**How it works:**
1. PAC uses unused high bits of a 64-bit pointer (typically bits [62:49] or [62:56] depending on virtual address size configuration)
2. A cryptographic MAC (QARMA block cipher) is computed over: pointer value + context (SP or other modifier) + key (128-bit, stored in system registers)
3. The MAC is stuffed into the unused high bits -- creating a "signed" pointer
4. Before use, the pointer is authenticated: the MAC is recomputed and compared. If it matches, the high bits are restored. If not, the high bits are corrupted, causing a fault on dereference

```
// Function prologue (sign return address)
PACIASP            // Sign LR using key A, context = SP
STP X29, X30, [SP, #-16]!

// Function epilogue (verify return address)
LDP X29, X30, [SP], #16
AUTIASP            // Verify LR; faults if corrupted
RET

// Signing arbitrary pointers:
PACIA X0, X1       // Sign X0 using key A, context = X1
AUTIA X0, X1       // Verify X0 using key A, context = X1
// If verification fails, X0 gets bits [62:top] set to error pattern
```

**Performance:**
- PACIA/AUTIA: 1-3 cycles on Apple M-series (dedicated QARMA unit)
- On Neoverse V2: ~5 cycles
- Used by default in Apple's iOS/macOS toolchains, Android (starting with Pixel 6), Linux kernel (CONFIG_ARM64_PTR_AUTH)

**Limitations:**
- PAC is probabilistic: with only ~7-15 bits of MAC, brute-force is feasible in ~2^7 to 2^15 attempts
- PAC can be bypassed if attacker can observe signing oracles (sign arbitrary pointers)
- PACGA (Generic Authentication) provides 32-bit MAC for non-pointer data authentication

### 8.3 x86 CET (Control-flow Enforcement Technology)

**CET-IBT (Indirect Branch Tracking):**
- Every valid indirect branch target must begin with `ENDBR64` (or `ENDBR32`)
- If an indirect branch lands on a non-ENDBR instruction, a #CP exception is raised
- `ENDBR64` is a 4-byte NOP (`F3 0F 1E FA`) on non-CET hardware -- zero overhead for backwards compatibility
- Enabled in Linux kernel since 6.2, GCC/Clang support via `-fcf-protection=branch`

**CET-SS (Shadow Stack):**
- Hardware-managed shadow stack stores return addresses
- On CALL: return address pushed to both regular stack and shadow stack
- On RET: return addresses compared; mismatch triggers #CP
- Shadow stack pages have special page table bit (dirty bit = 0, writable = 1 -- a combination impossible for normal pages)
- `INCSSPQ`/`RDSSPQ` for runtime manipulation (setjmp/longjmp, signal handling)

```
// CET shadow stack manipulation for signal handling
RDSSPQ rax          // Read shadow stack pointer
INCSSPQ rax         // Advance shadow stack pointer (skip N entries)
// Kernel uses SAVEPREVSSP/RSTORSSP for context switches
```

### 8.4 RISC-V CFI (Zicfiss + Zicfilp)

**Zicfilp (Landing Pad):**
- `LPAD imm20`: marks valid indirect branch target with a 20-bit label
- On indirect branch, if the target is not `LPAD`, a software-check exception is raised
- The 20-bit label can be used for coarse-grained CFI (matching call sites to targets)

**Zicfiss (Shadow Stack):**
```
SSPUSH ra           // Push return address to shadow stack (in function prologue)
SSPOPCHK ra         // Pop from shadow stack and check against ra (in function epilogue)
SSAMOSWAP.W/D       // Atomic swap on shadow stack (for setjmp/longjmp)
```

---

## 9. System and Privileged Instructions

### 9.1 Timestamp and Cycle Counters

| ISA | Instruction | What it reads | Serialization | Resolution |
|-----|-------------|---------------|---------------|------------|
| x86-64 | `RDTSC` | TSC (Time Stamp Counter) | NOT serialized -- can be reordered | Reference clock (~constant rate on modern CPUs with invariant TSC) |
| x86-64 | `RDTSCP` | TSC + core ID (into ECX) | Waits for prior instructions to complete, but does NOT prevent later instructions from executing before it | Reference clock |
| x86-64 | `LFENCE; RDTSC` | TSC | Fully serialized before the read | Reference clock |
| x86-64 | `RDTSC; LFENCE` | TSC | Fully serialized after the read | Reference clock |
| x86-64 | `RDPMC` | Performance counter (ECX selects which) | Not serialized | Hardware event counts |
| AArch64 | `MRS X0, CNTVCT_EL0` | Virtual counter (architectural timer) | Not serialized (use ISB before for serialization) | Timer frequency (typically ~1 GHz on server, ~24 MHz on mobile) |
| AArch64 | `MRS X0, PMCCNTR_EL0` | Cycle counter (if EL0 access enabled) | Not serialized | Core clock cycles |
| RISC-V | `rdcycle` | Cycle counter (CSR `cycle`) | Not serialized | Core clock cycles |
| RISC-V | `rdtime` | Wall clock time (CSR `time`) | Not serialized | Platform timer frequency |
| RISC-V | `rdinstret` | Instructions retired (CSR `instret`) | Not serialized | Instruction count |

**Precise benchmarking on x86:**
```c
// Recommended pattern for precise timing:
uint64_t start, end;

// Serialize and read start time
asm volatile("lfence\n\t"
             "rdtsc\n\t"
             "shl $32, %%rdx\n\t"
             "or %%rdx, %%rax"
             : "=a"(start) :: "rdx", "memory");

// Code under test
do_work();

// Read end time and serialize
asm volatile("rdtscp\n\t"
             "shl $32, %%rdx\n\t"
             "or %%rdx, %%rax\n\t"
             "lfence"
             : "=a"(end) :: "rcx", "rdx", "memory");

uint64_t cycles = end - start;
```

**TSC invariance:**
Modern Intel/AMD CPUs have an "invariant TSC" (CPUID.80000007H:EDX[8]) that runs at a constant rate regardless of frequency scaling or C-states. This is critical for `clock_gettime(CLOCK_MONOTONIC)` -- the Linux kernel VDSO uses RDTSC directly for fast timekeeping.

**ARM timer considerations:**
- `CNTVCT_EL0` reads the generic timer, which runs at a fixed frequency (CNTFRQ_EL0, typically 1 GHz on servers, 24.576 MHz on Apple Silicon)
- For cycle-accurate measurement, `PMCCNTR_EL0` is needed, but it requires kernel to enable EL0 access (PMUSERENR_EL0)
- Apple M-series exposes cycle counter via `MRS X0, S3_2_C15_C1_0` (implementation-defined)

### 9.2 CPU Feature Detection

**x86-64 CPUID:**
```
// CPUID: EAX = leaf, ECX = subleaf
// Results in EAX, EBX, ECX, EDX
MOV EAX, 7         // Leaf 7: structured extended features
MOV ECX, 0         // Sub-leaf 0
CPUID
// EBX bit 5: AVX2
// EBX bit 16: AVX-512F
// ECX bit 1: AVX-512 VBMI
// EDX bit 22: AMX-BF16
// etc.

// Leaf 0x80000007: Advanced Power Management
CPUID
// EDX bit 8: Invariant TSC
```

Key leaves: 0 (vendor), 1 (features), 7 (extended features), 0xD (XSAVE state), 0x80000001 (AMD features), 0x80000008 (address sizes).

**AArch64 HWCAP (Linux):**
ARM does not have a CPUID equivalent accessible from user space. Feature detection happens via:
1. Kernel exposes `AT_HWCAP` / `AT_HWCAP2` auxiliary vector entries
2. User space reads via `getauxval(AT_HWCAP)` or reads `/proc/cpuinfo`
3. `MRS` reads of `ID_AA64*` registers are trapped and emulated by the kernel (since Linux 4.11, `MRS_emulate` framework)

```c
#include <sys/auxv.h>
unsigned long hwcap = getauxval(AT_HWCAP);
if (hwcap & HWCAP_ATOMICS)  // LSE atomics
if (hwcap & HWCAP_SVE)      // SVE
if (hwcap & HWCAP_SVE2)     // SVE2
if (hwcap & HWCAP_SHA512)   // SHA-512 acceleration
if (hwcap & HWCAP_PACA)     // Pointer Authentication (address key A)
```

**RISC-V extension discovery:**
- `misa` CSR: bitmask of base ISA extensions (M, A, F, D, C, etc.) -- but only readable at M-mode
- Linux kernel exposes extensions via:
  - `/proc/cpuinfo` `isa:` field (e.g., `rv64imafdc_zba_zbb_zbs_zicbom`)
  - `RISCV_HWPROBE` syscall (since Linux 6.4): structured feature query
  - `AT_HWCAP` auxiliary vector (limited, being expanded)

```c
// RISC-V HWPROBE system call
#include <asm/hwprobe.h>
struct riscv_hwprobe pairs[] = {
    { .key = RISCV_HWPROBE_KEY_IMA_EXT_0 },
};
syscall(__NR_riscv_hwprobe, pairs, 1, 0, NULL, 0);
if (pairs[0].value & RISCV_HWPROBE_IMA_V)  // Vector extension
    use_rvv_code_path();
```

### 9.3 System Register Access

| ISA | Read | Write | Use Case |
|-----|------|-------|----------|
| x86-64 | `RDMSR` | `WRMSR` | Model-Specific Registers (privileged) |
| x86-64 | `XGETBV` | `XSETBV` | Extended Control Registers (XSAVE state) |
| AArch64 | `MRS Xt, <sysreg>` | `MSR <sysreg>, Xt` | System registers (EL-dependent) |
| RISC-V | `CSRR rd, csr` | `CSRW csr, rs1` | Control/Status Registers |
| RISC-V | `CSRRS rd, csr, rs1` | — | CSR read-and-set bits (atomic) |
| RISC-V | `CSRRC rd, csr, rs1` | — | CSR read-and-clear bits (atomic) |

### 9.4 Wait and Power Management

| ISA | Instruction | Effect | Use Case |
|-----|-------------|--------|----------|
| x86-64 | `PAUSE` | Hint to processor: in a spin-wait loop. Reduces power and avoids memory-order violation pipeline flush | Spin locks, busy-waiting |
| x86-64 | `MONITOR/MWAIT` | Wait for cache line modification (privileged on most configs) | Idle loops in kernel |
| x86-64 | `UMONITOR/UMWAIT/TPAUSE` | User-space monitor/wait (Waitpkg, 2019+) | User-space spin-wait with power awareness |
| AArch64 | `WFE` | Wait For Event: sleep until event register is set (by SEV from another core, or exclusive monitor clear) | Spin locks (sleep instead of burn) |
| AArch64 | `WFET` (ARMv8.7) | WFE with timeout | Bounded spin-wait |
| AArch64 | `WFI` | Wait For Interrupt: sleep until interrupt | Idle loop |
| AArch64 | `SEV` | Send Event: wake all cores from WFE | Lock release notification |
| AArch64 | `SEVL` | Send Event Local: set event register for this core only | Prime the event register before WFE loop |
| AArch64 | `YIELD` | Hint: relinquish execution resources (like PAUSE) | Spin loops on SMT cores |
| RISC-V | `WFI` | Wait For Interrupt | Idle loop (privileged) |
| RISC-V | `PAUSE` (Zihintpause) | Hint: spin-wait loop, like x86 PAUSE | Spin locks |

**WFE-based spinlock (ARM) -- dramatically better than busy-wait:**
```
// Lock acquisition with WFE
acquire:
    LDAXR  W0, [X1]      // Load-acquire exclusive
    CBNZ   W0, wait       // If locked, go to wait
    STXR   W2, W3, [X1]  // Try to acquire
    CBNZ   W2, acquire    // Retry if store-exclusive failed
    RET

wait:
    WFE                   // Sleep until event (lock release)
    B      acquire        // Re-check lock

// Lock release with SEV
release:
    STLR   WZR, [X1]     // Store-release zero (unlock)
    SEV                   // Wake waiting cores
    RET
```

This is how Linux kernel's `arch_spin_lock` works on ARM64 (with ticket lock or queued spinlock variants). WFE reduces power consumption and cross-core interference dramatically compared to a tight LDXR/STXR polling loop.

**x86 UMWAIT (user-space wait):**
```c
// User-space monitor/wait (Intel Tremont+, Sapphire Rapids+)
_umonitor(addr);                    // Set up address to monitor
uint8_t r = _umwait(0, deadline);   // Wait until *addr changes or deadline
// State 0 = C0.2 (deeper sleep), State 1 = C0.1 (lighter sleep)
```
Useful for user-space event loops, DPDK poll-mode drivers with power awareness, and lock-free queue consumers.

---

## 10. Floating Point and Precision

### 10.1 Fused Multiply-Add (FMA)

| ISA | Instructions | Precision | Forms |
|-----|-------------|-----------|-------|
| x86-64 (FMA3) | `VFMADD132PS/PD`, `VFMADD213PS/PD`, `VFMADD231PS/PD` | float/double | 132: a=a*c+b, 213: a=b*a+c, 231: a=b*c+a |
| x86-64 (FMA3) | `VFMSUB`, `VFNMADD`, `VFNMSUB`, `VFMADDSUB`, `VFMSUBADD` | float/double | Subtract, negate, interleave add/sub variants |
| AArch64 | `FMADD Dd, Dn, Dm, Da` | float/double | d = a + n*m |
| AArch64 | `FMLA Vd.4S, Vn.4S, Vm.4S` | NEON vector | Fused multiply-accumulate |
| AArch64 (SVE) | `FMLA Zd.S, Pg/M, Zn.S, Zm.S` | SVE vector | Predicated FMA |
| RISC-V (F/D ext) | `fmadd.s/d fd, fs1, fs2, fs3` | float/double | fd = fs1*fs2 + fs3 |
| RISC-V (F/D ext) | `fmsub.s/d`, `fnmadd.s/d`, `fnmsub.s/d` | float/double | Subtract/negate variants |
| RISC-V (V ext) | `vfmacc.vv vd, vs1, vs2` | vector float | vd[i] += vs1[i] * vs2[i] |

**Why FMA matters:**
- Single rounding: `a*b+c` is computed with a single rounding at the end, rather than rounding after multiply then after add. This provides higher precision.
- 2x throughput: FMA counts as both a multiply and an add for the same execution port. Critical for dense linear algebra (GEMM), where peak FLOPS = 2 * frequency * FMA_units * vector_width.
- Kahan summation alternative: FMA enables error-free transformations like `TwoProductFMA(a, b)` that compute both the product and the rounding error in the product.

**Performance:**
- Intel Sapphire Rapids: 2x FMA units, each 512-bit wide = 2 * 16 FP32 = 32 FMA/cycle = 64 FLOP/cycle per core
- Apple M4: 4x NEON FMA units, each 128-bit = 4 * 4 FP32 = 16 FMA/cycle = 32 FLOP/cycle per core
- Neoverse V2: 2x SVE FMA units, each 128-bit (SVE VL=128) = 2 * 4 FP32 = 8 FMA/cycle per core

### 10.2 Half-Precision and BFloat16

| ISA | Extension | Instructions | Use Case |
|-----|-----------|-------------|----------|
| x86-64 | F16C (2012) | `VCVTPH2PS`, `VCVTPS2PH` | Convert FP16 <-> FP32 (no arithmetic in FP16) |
| x86-64 | AVX-512 FP16 (Sapphire Rapids) | `VADDPH`, `VMULPH`, `VFMADD231PH zmm` | Native FP16 arithmetic at 512-bit width |
| x86-64 | AMX-BF16 | `TDPBF16PS` | BF16 matrix multiply in AMX tiles |
| x86-64 | AVX10.2 (2025) | `VADDNEPBF16`, `VMULNEPBF16`, `VDPPHPS` | Native BF16 arithmetic, FP16 dot product |
| AArch64 | FEAT_FP16 (ARMv8.2) | `FADD Hd, Hn, Hm` (scalar), `FADD Vd.8H, ...` (vector) | Native FP16 arithmetic |
| AArch64 | FEAT_BF16 (ARMv8.6) | `BFMMLA Vd.4S, Vn.8H, Vm.8H` | BF16 matrix multiply-accumulate to FP32 |
| AArch64 (SME) | `BFMOPA ZA.S, Pn/M, Zm.H, Zn.H` | BF16 outer product into FP32 tile | ML training |
| RISC-V | Zfh (ratified) | `fadd.h`, `fmul.h`, `fmadd.h` | Scalar FP16 arithmetic |
| RISC-V | Zfbfmin | `fcvt.bf16.s`, `fcvt.s.bf16` | BF16 <-> FP32 conversion |
| RISC-V | Zvfh | `vfadd.vv` with SEW=16 | Vector FP16 arithmetic |
| RISC-V | Zvfbfmin | Vector BF16 conversions | Vector BF16 <-> FP32 |

**FP16 vs BF16 formats:**
```
IEEE FP16:  1 sign + 5 exponent + 10 mantissa  (range: +-65504, precision: ~3.3 decimal digits)
BFloat16:   1 sign + 8 exponent + 7 mantissa   (range: same as FP32, precision: ~2.4 decimal digits)
```
BF16 has the same dynamic range as FP32 (same 8-bit exponent), making it better for ML training where gradients can vary widely. FP16 has better precision but narrower range (often needs loss scaling).

### 10.3 Rounding Modes and Denormal Handling

**IEEE 754 rounding modes:**
All three ISAs support the four IEEE 754 rounding modes:
- Round to Nearest Even (RNE) -- default
- Round toward +Infinity (RU)
- Round toward -Infinity (RD)
- Round toward Zero (RZ)
- (Plus: Round to Nearest, ties to Away -- some ISAs support this as a 5th mode)

| ISA | Rounding mode control | Denormal behavior |
|-----|----------------------|-------------------|
| x86-64 | MXCSR register bits [14:13] for SSE/AVX; x87 FPU control word bits [11:10] | MXCSR.DAZ (Denormals Are Zero) and MXCSR.FTZ (Flush To Zero) for performance |
| AArch64 | FPCR bits [23:22] (RMode) | FPCR.FZ (Flush to Zero), FPCR.FZ16 for FP16 |
| RISC-V | `frm` CSR (3-bit field), plus per-instruction `rm` field | No DAZ/FTZ in spec; implementation may support via custom CSR |

**AVX-512 per-instruction rounding:**
AVX-512 allows embedded rounding in the instruction encoding itself:
```
VADDPS zmm0{rz-sae}, zmm1, zmm2   // Add with round-toward-zero + suppress-all-exceptions
VADDPS zmm0{rn-sae}, zmm1, zmm2   // Add with round-to-nearest-even
```
This eliminates the need to modify MXCSR between different rounding mode requirements -- invaluable for interval arithmetic, correctly-rounded math libraries, and financial computations.

**Denormal (subnormal) performance trap:**
Denormal numbers (very small numbers below the normal range) cause massive performance penalties on many implementations:
- Intel: denormals on input or output can cause ~100-150 cycle penalties per operation (microcode assist)
- AMD Zen4: input denormals are handled in hardware for most operations (no penalty), but output denormals still trigger assists
- ARM: implementation-dependent; Apple M-series handles denormals at speed; some Cortex implementations flush to zero by default
- Setting DAZ+FTZ (x86) or FZ (ARM) eliminates the penalty but slightly violates IEEE 754

**Real-world impact:**
- Database engines: usually set FTZ+DAZ early in process startup to avoid random performance cliffs
- Audio processing: denormals are notorious for causing CPU spikes in silence (values slowly decay toward denormal range in IIR filters)
- ML training: BF16/FP16 denormals rarely matter because values that small are effectively zero for gradients

---

## 11. String and Memory Operations

### 11.1 x86-64 REP String Operations

```
REP MOVSB              // Copy RCX bytes from [RSI] to [RDI]
REP MOVSQ              // Copy RCX quadwords from [RSI] to [RDI]
REP STOSB              // Fill RCX bytes at [RDI] with AL
REP STOSQ              // Fill RCX quadwords at [RDI] with RAX
REP CMPSB              // Compare RCX bytes at [RSI] with [RDI]
REPE SCASB             // Scan RCX bytes at [RDI] for AL (strlen-like)
```

**ERMS (Enhanced REP MOVSB/STOSB) -- Ivy Bridge+:**
Intel optimized the microcode for REP MOVSB/STOSB to match or beat manual SIMD copy loops for large copies. The hardware:
1. Determines optimal strategy based on copy size and alignment
2. Uses 256-bit or 512-bit internal moves
3. Handles the startup/cleanup overhead for non-aligned heads/tails
4. Benefits from store-buffer coalescing and WC (write-combining) for large copies

**FSRM (Fast Short REP MOVSB) -- Ice Lake+:**
Optimizes REP MOVSB for small copies (< 128 bytes) that were previously slower than explicit MOV sequences. Before FSRM, the startup overhead of REP MOVSB was ~15-20 cycles, making it slower than 2-3 MOV instructions for small copies.

**Performance characteristics:**
```
Copy size       Best approach (Intel Ice Lake+)
---------       ----------------------------
1-16 bytes      Two overlapping MOV (load+store)
16-128 bytes    REP MOVSB (FSRM) or 2-4 VMOVDQU
128-256 KB      REP MOVSB (ERMS, cache-friendly)
256 KB+         VMOVNTDQ (non-temporal, bypass cache)
```

glibc's `memcpy` on x86-64 checks ERMS/FSRM capability at startup and selects the appropriate implementation. For large copies, it uses either REP MOVSB or SIMD loops with non-temporal stores depending on size threshold (~= L3 cache size / 2).

### 11.2 AArch64 Load/Store Pair and Multi-Register

```
LDP  X0, X1, [X2]        // Load pair: two 64-bit loads from consecutive addresses (single instruction)
STP  X0, X1, [X2]        // Store pair: two 64-bit stores
LDP  Q0, Q1, [X2]        // Load pair: two 128-bit SIMD loads (256 bits total!)
STP  Q0, Q1, [X2]        // Store pair: two 128-bit SIMD stores

LDNP X0, X1, [X2]        // Non-temporal load pair
STNP X0, X1, [X2]        // Non-temporal store pair

// Post-index (auto-increment):
LDP  X0, X1, [X2], #16   // Load pair, then X2 += 16

// Pre-index:
LDP  X0, X1, [X2, #16]!  // X2 += 16, then load pair from [X2]

// ARMv8.7 FEAT_LS64: 64-byte single-copy atomic load/store
LD64B  X0, [X1]           // Load 64 bytes (whole cache line) atomically
ST64B  X0, [X1]           // Store 64 bytes atomically
ST64BV X0, Xs, [Xn]       // Store 64 bytes, return status
```

**FEAT_MOPS (Memory Operations) -- ARMv8.8:**
Hardware-accelerated memcpy/memset/memmove instructions:
```
// Memory copy (3-instruction sequence: prologue, main, epilogue)
CPYFP  [Xd]!, [Xs]!, Xn!    // Copy Forward: Prologue (setup)
CPYFM  [Xd]!, [Xs]!, Xn!    // Copy Forward: Main (bulk copy)
CPYFE  [Xd]!, [Xs]!, Xn!    // Copy Forward: Epilogue (cleanup)

// Memory set
SETP   [Xd]!, Xn!, Xv        // Set: Prologue
SETM   [Xd]!, Xn!, Xv        // Set: Main
SETE   [Xd]!, Xn!, Xv        // Set: Epilogue
```
The 3-instruction design allows the CPU to be interrupted and resumed (unlike x86 REP which is interruptible at iteration granularity). Each variant (P/M/E) handles the respective phase, and the hardware determines the optimal implementation. This is the ARM equivalent of ERMS.

### 11.3 RISC-V Memory Operations

RISC-V has no hardware-accelerated memcpy equivalent in the base ISA. Copy loops use:
```
// Simple doubleword copy loop
loop:
    ld   a3, 0(a1)
    sd   a3, 0(a0)
    addi a1, a1, 8
    addi a0, a0, 8
    addi a2, a2, -8
    bnez a2, loop

// With V extension (RVV): vectorized memcpy
memcpy_rvv:
    vsetvli t0, a2, e8, m8, ta, ma   // Set up for byte-width, LMUL=8
    vle8.v  v0, (a1)                  // Vector load
    vse8.v  v0, (a0)                  // Vector store
    add     a1, a1, t0
    add     a0, a0, t0
    sub     a2, a2, t0
    bnez    a2, memcpy_rvv
```

The `Zicboz` extension adds `cbo.zero` which can zero entire cache lines without a read-for-ownership -- equivalent to ARM's DC ZVA, useful for memset(0) and page zeroing.

---

## 12. Transactional Memory

### 12.1 x86 TSX (Transactional Synchronization Extensions)

**History and current status:**
TSX was introduced in Haswell (2013), disabled due to bugs, re-enabled in Broadwell/Skylake, then progressively disabled again due to security vulnerabilities (TAA -- TSX Asynchronous Abort, a side-channel attack). As of 2025:
- **HLE (Hardware Lock Elision):** deprecated and removed in all current Intel CPUs
- **RTM (Restricted Transactional Memory):** disabled by default via microcode on most consumer CPUs; available on some Xeon SKUs (Sapphire Rapids HBM, some Ice Lake Server) with security mitigations
- **Intel has announced TSX deprecation** in future architectures

**Instructions (for reference, as production systems may still encounter them):**
```
XBEGIN  label          // Start transaction; if abort, jump to label (fallback path)
XEND                   // Commit transaction (all memory modifications atomically visible)
XABORT  imm8           // Explicitly abort transaction with reason code
XTEST                  // Test if inside a transaction (ZF=0 if inside)

// Usage pattern:
retry:
    XBEGIN  fallback    // Start transaction
    // ... critical section (reads/writes are speculative) ...
    XEND                // Commit
    JMP     done

fallback:
    // Transaction aborted -- reason in EAX
    // EAX bit 0: explicit XABORT
    // EAX bit 1: may succeed on retry
    // EAX bit 2: conflict with another thread
    // EAX bit 3: buffer overflow (too many cache lines)
    // EAX bit 4: debug breakpoint hit
    // EAX bit 5: nested transaction abort
    // Acquire lock and execute critical section traditionally
    LOCK_ACQUIRE
    // ... critical section ...
    LOCK_RELEASE

done:
```

**How it worked microarchitecturally:**
1. On XBEGIN: checkpoint register state, enter transactional mode
2. All loads/stores tracked in L1D cache (read set and write set)
3. Write set: modified lines held in L1D with "transactional" bit; not made visible to other cores
4. Conflict: if another core reads a line in our write set or writes a line in our read set, the transaction aborts
5. Capacity limit: read set bounded by L1D (32 KB), write set bounded by L1D associativity (typically 8-12 cache lines usable)
6. On XEND: all writes atomically made visible (clear transactional bits)
7. On abort: discard all transactional writes, restore register checkpoint, jump to fallback

**Why systems used it (and what replaced it):**
- Optimistic lock elision for read-heavy data structures (hash tables, skip lists)
- MySQL InnoDB used TSX for optimistic locking in buffer pool management
- HotSpot JVM used TSX for lock elision
- Replacement: most systems moved to optimistic validation protocols, RCU, or more fine-grained locking

### 12.2 ARM TME (Transactional Memory Extension)

**Status: specified but NOT widely implemented. No production silicon as of 2025.**

```
TSTART   Xt       // Start transaction; Xt = 0 on success, != 0 on abort (with reason)
TCOMMIT           // Commit transaction
TCANCEL  #imm     // Explicitly cancel with reason
TTEST              // Test transactional state (Xt = depth, 0 if not in transaction)
```

ARM TME supports nesting (up to implementation-defined depth). The design is similar to x86 RTM but with ARM's relaxed memory model, the interaction between transactions and barriers is more nuanced.

### 12.3 RISC-V Transactional Memory

No ratified transactional memory extension. The Ztm proposal exists in draft form. RISC-V's approach has been to focus on getting the atomic extensions (Zacas, Zabha) right before adding hardware TM.

### 12.4 Assessment of Hardware Transactional Memory

HTM has largely failed to achieve widespread adoption:
- **Capacity aborts:** read/write sets limited by cache sizes; any context switch, interrupt, or TLB miss can abort
- **Security concerns:** transactional side channels (TSX-based Spectre variants, TAA attack)
- **Performance unpredictability:** fast path is very fast, but abort rate under contention makes worst-case terrible
- **Software alternatives are good enough:** RCU, optimistic locking with validation, lock-free data structures, epoch-based reclamation

**Key paper:** "Transactional Memory: Architectural Support for Lock-Free Data Structures" -- Herlihy & Moss, ISCA 1993 (the original proposal). "Why HTM Is Not The Answer" -- various industry retrospectives.

---

## 13. Virtualization

### 13.1 x86-64 VMX (Intel VT-x) / SVM (AMD-V)

**Intel VMX instructions:**
```
VMXON  [mem]           // Enable VMX operation (enter VMX root mode)
VMXOFF                 // Disable VMX operation
VMCLEAR [mem]          // Clear VMCS (Virtual Machine Control Structure)
VMPTRLD [mem]          // Load pointer to current VMCS
VMREAD  reg, reg       // Read field from current VMCS
VMWRITE reg, reg       // Write field to current VMCS
VMLAUNCH               // Launch VM (first entry -- VMCS must be in "clear" then "launched" state)
VMRESUME               // Resume VM (subsequent entries)
VMCALL                 // Guest -> Host hypercall (causes VM exit)
INVEPT                 // Invalidate EPT (Extended Page Table) translations
INVVPID                // Invalidate VPID (Virtual Processor ID) translations
```

**AMD SVM instructions:**
```
VMRUN  [mem]           // Run guest (VMCB address in RAX)
VMSAVE [mem]           // Save additional host state
VMLOAD [mem]           // Load additional host state
VMMCALL                // Guest -> Host hypercall
CLGI                   // Clear Global Interrupt Flag (disable interrupts including NMI)
STGI                   // Set Global Interrupt Flag
INVLPGA                // Invalidate TLB entry for specific ASID
```

**VM Exit cost:**
The cost of a VM exit (transition from guest to hypervisor) has decreased over generations but remains significant:
```
Generation          Approx VM exit + re-entry cost
-----------         ----------------------------
Pentium 4 (VT-x v1)   ~4000 cycles
Nehalem               ~1000 cycles
Skylake               ~500-700 cycles
Sapphire Rapids       ~400-500 cycles
AMD Zen4              ~400-500 cycles
```

**EPT/NPT (Extended/Nested Page Tables):**
- Hardware-assisted two-level page table walk: GVA -> GPA -> HPA
- Eliminates need for shadow page tables (which required VM exit on every guest page table modification)
- EPT violation (page fault in second-level translation) causes VM exit
- TDP (Two-Dimensional Paging) on AMD = NPT (Nested Page Tables)

### 13.2 AArch64 Virtualization (EL2)

ARM's virtualization is built into the exception level model:
```
EL0: User mode (applications)
EL1: OS kernel
EL2: Hypervisor
EL3: Secure monitor (TrustZone)
```

**Key instructions:**
```
HVC #imm    // Hypervisor Call: EL1 -> EL2 trap (like x86 VMCALL)
SMC #imm    // Secure Monitor Call: EL1/EL2 -> EL3
ERET        // Exception Return: EL2 -> EL1 (return to guest), or EL1 -> EL0
AT S12E1R, Xt  // Address Translation: Stage 1+2 EL1 read (hypervisor translates guest address)
TLBI ALLE1  // TLB Invalidate: All EL1 entries (hypervisor clears guest TLB)
```

**ARMv8.1 VHE (Virtualization Host Extensions):**
- Allows the hypervisor to run at EL2 with EL1-like register naming
- Eliminates the need for the hypervisor to save/restore its own state on every VM exit
- KVM on ARM uses VHE: the host kernel runs at EL2, guests run at EL1
- Reduces VM exit cost by ~30-50%

**Stage-2 translation (IPA -> PA):**
ARM's stage-2 translation (controlled by `VTTBR_EL2`) is the equivalent of Intel EPT. The hypervisor sets up a second-level page table that maps the guest's Intermediate Physical Addresses (IPAs) to real Physical Addresses.

### 13.3 RISC-V H Extension (Hypervisor)

**Ratified in 2024, gaining implementation support:**

```
// New privilege modes:
// HS-mode (Hypervisor Supervisor): replaces S-mode when H extension is present
// VS-mode (Virtual Supervisor): guest OS runs here
// VU-mode (Virtual User): guest user processes run here

// Key CSRs:
hstatus    // Hypervisor status
hedeleg    // Hypervisor exception delegation
hideleg    // Hypervisor interrupt delegation
hgatp      // Guest address translation pointer (like x86 EPTP, ARM VTTBR_EL2)
htval      // Hypervisor trap value (guest physical address on fault)
htinst     // Hypervisor trap instruction (trapped instruction encoding)
vsstatus   // Virtual supervisor status
vsatp      // Virtual supervisor address translation

// Instructions:
HFENCE.VVMA   // Hypervisor fence for guest VA mappings
HFENCE.GVMA   // Hypervisor fence for guest PA mappings
HLV.D rd, (rs1)  // Hypervisor Load: load from guest address space while in HS-mode
HSV.D rs2, (rs1)  // Hypervisor Store: store to guest address space
HINVAL.VVMA   // Invalidate guest VA translations
HINVAL.GVMA   // Invalidate guest PA translations
```

**Two-stage address translation (same concept as EPT/Stage-2):**
```
Guest VA --[VS-mode page table]--> Guest PA --[hgatp page table]--> Host PA
```

**Implementations:**
- QEMU full system emulation supports H extension
- Xvisor and KVM support RISC-V H extension
- T-Head C920 implements H extension
- Expected in SiFive P870 and Ventana Veyron V2

---

## 14. Performance Monitoring

### 14.1 x86-64 Performance Monitoring

**RDPMC (Read Performance Monitoring Counter):**
```
MOV ECX, 0              // Select counter 0 (IA32_PMC0)
RDPMC                   // Read counter into EDX:EAX
// ECX bit 30: select fixed counter (0 = instructions retired, 1 = cycles, 2 = ref cycles)
// ECX bit 31: select any-thread mode (on HT cores)
```

**Programming performance counters (privileged):**
```
// Write IA32_PERFEVTSELx MSR to configure what counter N measures
WRMSR(IA32_PERFEVTSEL0, event_select | umask | usr | os | enable)

// Common events:
// Instructions retired:    Event 0xC0, Umask 0x00
// LLC misses:             Event 0x2E, Umask 0x41 (Intel)
// Branch mispredictions:  Event 0xC5, Umask 0x00
// L1D cache misses:       Event 0x51 (load), 0xD1 (all)
```

**PEBS (Processor Event-Based Sampling) -- Intel:**
- Hardware writes sample records directly to a memory buffer when counter overflows
- Each record contains: RIP, registers, data address (for memory events), latency, TLB info
- Eliminates interrupt overhead of traditional sampling (no PMI for every sample)
- Used by `perf record -e cpu/event=0xd1,umask=0x20/pp` (the `pp` means "precise event")

**IBS (Instruction-Based Sampling) -- AMD:**
- AMD's equivalent of PEBS, but samples every Nth retired instruction
- IBS Fetch: samples instruction fetch events (L1I miss, ITLB miss, fetch latency)
- IBS Op: samples micro-op execution events (data cache miss, latency, address)
- Provides complete microarchitectural attribution per sampled instruction

**LBR (Last Branch Record) -- Intel:**
```
// IA32_LBR_x_FROM_IP, IA32_LBR_x_TO_IP, IA32_LBR_x_INFO
// Ring buffer of last N branches (16-32 entries depending on generation)
// Each entry: from-address, to-address, cycle count, misprediction flag
// Used for AutoFDO (feedback-directed optimization without instrumentation)
```

**Intel PT (Processor Trace):**
- Hardware traces all control flow (branches taken/not-taken) with minimal overhead (~5%)
- Output is a compressed packet stream decoded offline
- Records: TNT (taken/not-taken) packets, TIP (target IP) packets, timing packets
- Used for: precise code coverage, security monitoring, reverse debugging (rr, UDB)

### 14.2 AArch64 PMU (Performance Monitor Unit)

```
// Enable PMU cycle counter at EL0
MSR PMCR_EL0, #1             // Enable PMU
MSR PMCNTENSET_EL0, #(1<<31) // Enable cycle counter (PMCCNTR_EL0)
MSR PMUSERENR_EL0, #1        // Allow EL0 access

// Read cycle counter
MRS X0, PMCCNTR_EL0          // Read cycle count

// Configure event counter 0
MSR PMEVTYPER0_EL0, #0x08    // Event 0x08 = Instructions retired (Architecturally defined)
MSR PMCNTENSET_EL0, #1       // Enable counter 0
MRS X0, PMEVCNTR0_EL0        // Read counter 0
```

**ARM Statistical Profiling Extension (SPE) -- ARMv8.2:**
- Hardware sampling: the PMU periodically samples operations and writes detailed records to a memory buffer
- Each record includes: PC, virtual address, physical address, data source (L1/L2/LLC/DRAM), latency, event type
- Similar to Intel PEBS but designed as a separate extension rather than building on counter overflow
- Used by Linux `perf` via `arm_spe_0` PMU driver

**ARM BRBE (Branch Record Buffer Extension) -- ARMv9.2:**
- ARM's equivalent of Intel LBR
- Ring buffer of recent branches with from/to addresses and cycle counts
- Enables AutoFDO-style optimization on ARM

### 14.3 RISC-V Performance Monitoring (Zihpm)

```
// Hardware Performance Monitor CSRs:
rdcycle     rd        // Read cycle counter (CSR 0xC00)
rdtime      rd        // Read wall-clock time (CSR 0xC01)
rdinstret   rd        // Read instructions retired (CSR 0xC02)
csrr        rd, hpmcounter3   // Read hardware performance counter 3 (CSR 0xC03)
// ... up to hpmcounter31 (29 configurable counters)

// Configuration (M-mode only):
csrw mhpmevent3, event_code   // Configure what counter 3 measures
csrw mcounteren, mask          // Enable counters for S-mode access
csrw scounteren, mask          // Enable counters for U-mode access
```

**Sscofpmf extension (Counter Overflow and Privilege Mode Filtering):**
- Enables counter overflow interrupts (for sampling)
- Enables filtering counters by privilege mode (user/supervisor/machine)
- Critical for building `perf`-like profiling infrastructure

The RISC-V PMU ecosystem is still maturing. SiFive's P870 implements Zihpm with implementation-specific events. The standard event space is being defined by the RISC-V performance monitoring TG.

### 14.4 Performance Counter Usage Patterns

**Top-down microarchitecture analysis (Intel TMA):**
```
Retiring     = uops_retired.retire_slots / (4 * cpu_clk_unhalted.thread)
Bad_Spec     = (uops_issued.any - uops_retired.retire_slots + recovery_bubbles) / (4 * cycles)
FE_Bound     = idq_uops_not_delivered.core / (4 * cycles)
BE_Bound     = 1 - (Retiring + Bad_Spec + FE_Bound)
```
This decomposes pipeline stalls into: frontend bound (fetch/decode limited), backend bound (execution/memory limited), bad speculation (mispredictions), and retiring (doing useful work). Available as `perf stat --topdown` on Linux.

---

## 15. Compiler Mapping: C11/C++ Atomics to ISA {#15-compiler-mapping}

### 15.1 How `std::atomic<T>` Maps to Instructions

| C++ Operation | x86-64 | AArch64 | RISC-V |
|--------------|--------|---------|--------|
| `load(relaxed)` | `MOV` | `LDR` | `ld` |
| `load(acquire)` | `MOV` (free under TSO) | `LDAR` | `ld` + `fence r,rw` |
| `load(seq_cst)` | `MOV` (free under TSO) | `LDAR` | `fence rw,rw` + `ld` + `fence r,rw` |
| `store(relaxed)` | `MOV` | `STR` | `sd` |
| `store(release)` | `MOV` (free under TSO) | `STLR` | `fence rw,w` + `sd` |
| `store(seq_cst)` | `MOV` + `MFENCE` (or `XCHG`) | `STLR` | `fence rw,w` + `sd` + `fence rw,rw` |
| `fetch_add(relaxed)` | `LOCK XADD` | `LDADD` (LSE) or LDXR/ADD/STXR loop | `amoadd.d` |
| `fetch_add(acq_rel)` | `LOCK XADD` | `LDADDAL` (LSE) | `amoadd.d.aqrl` |
| `fetch_add(seq_cst)` | `LOCK XADD` | `LDADDAL` (LSE) | `amoadd.d.aqrl` |
| `compare_exchange(relaxed)` | `LOCK CMPXCHG` | `CAS` (LSE) or LDXR/STXR loop | `amocas.d` (Zacas) or LR/SC loop |
| `compare_exchange(seq_cst)` | `LOCK CMPXCHG` | `CASAL` (LSE) | `amocas.d.aqrl` or LR.aq/SC.rl loop |
| `thread_fence(acquire)` | compiler fence only (NOP) | `DMB ISHLD` | `fence r,rw` |
| `thread_fence(release)` | compiler fence only (NOP) | `DMB ISH` (*) | `fence rw,w` |
| `thread_fence(seq_cst)` | `MFENCE` (or `LOCK; ADD`) | `DMB ISH` | `fence rw,rw` |

(*) On AArch64, `atomic_thread_fence(release)` emits `DMB ISH` (full barrier) rather than the seemingly-sufficient `DMB ISHST`. This is because a release fence must prevent store-store AND load-store reorderings, and `DMB ISHST` only prevents store-store. The C++ standard requires that a release fence orders prior loads too (loads must not be reordered past the fence into the critical section being released).

### 15.2 x86 seq_cst Store: MFENCE vs XCHG

Two strategies for seq_cst store on x86:
1. `MOV [mem], reg` + `MFENCE` -- GCC default
2. `XCHG [mem], reg` -- Clang/LLVM default (XCHG has implicit LOCK)

XCHG is generally faster because MFENCE is an expensive serializing instruction (~33 cycles on Intel) while XCHG with LOCK is a regular atomic RMW (~18-22 cycles). However, XCHG reads the old value from memory (even though seq_cst store discards it), which can cause extra cache traffic if the line is Modified in another core.

### 15.3 Memory Model Implications for Software Design

**Lock-free queue on x86 vs ARM:**
```c
// Producer (x86 -- release store is free!)
buffer[tail] = item;           // Just a MOV
tail_index.store(new_tail, std::memory_order_release);  // Just a MOV (no fence!)

// Consumer
int t = tail_index.load(std::memory_order_acquire);      // Just a MOV
item = buffer[t];              // Just a MOV

// On ARM, the same code emits:
// Producer: STR (data) + STLR (tail)    -- STLR prevents prior stores from being reordered past
// Consumer: LDAR (tail) + LDR (data)     -- LDAR prevents subsequent loads from being reordered before
```

The key point: x86-64 code that works correctly under TSO may be missing necessary fences for ARM/RISC-V. The C++ memory model forces you to specify ordering explicitly, and the compiler inserts the right instructions for each ISA. But hand-written assembly or inline asm must account for the difference.

---

## 16. ISA Extension Discovery {#16-isa-extension-discovery}

### 16.1 Runtime Feature Detection Patterns

**x86-64: CPUID-based dispatch (used by glibc, OpenSSL, etc.):**
```c
#include <cpuid.h>

void detect_features(void) {
    uint32_t eax, ebx, ecx, edx;

    // Check leaf 7, sub-leaf 0 for extended features
    __cpuid_count(7, 0, eax, ebx, ecx, edx);

    bool has_avx2     = (ebx >> 5) & 1;
    bool has_avx512f  = (ebx >> 16) & 1;
    bool has_sha_ni   = (ebx >> 29) & 1;
    bool has_avx512bw = (ebx >> 30) & 1;

    // Check leaf 7, sub-leaf 1 for newer features
    __cpuid_count(7, 1, eax, ebx, ecx, edx);
    bool has_avx_vnni    = (eax >> 4) & 1;
    bool has_avx512_bf16 = (eax >> 5) & 1;
    bool has_avx10       = (edx >> 19) & 1;

    // Must also check OS support via XGETBV (XSAVE state)
    uint64_t xcr0 = _xgetbv(0);
    bool os_supports_avx  = (xcr0 & 0x6) == 0x6;       // XMM + YMM state
    bool os_supports_avx512 = (xcr0 & 0xE6) == 0xE6;   // XMM + YMM + ZMM + opmask state
}
```

**AArch64: HWCAP-based dispatch:**
```c
#include <sys/auxv.h>

void detect_features(void) {
    unsigned long hwcap  = getauxval(AT_HWCAP);
    unsigned long hwcap2 = getauxval(AT_HWCAP2);

    bool has_lse     = hwcap & HWCAP_ATOMICS;
    bool has_sve     = hwcap & HWCAP_SVE;
    bool has_sve2    = hwcap & HWCAP_SVE2;
    bool has_sha512  = hwcap & HWCAP_SHA512;
    bool has_crc32   = hwcap & HWCAP_CRC32;
    bool has_paca    = hwcap & HWCAP_PACA;      // Pointer Auth (key A)
    bool has_bf16    = hwcap2 & HWCAP2_BF16;
    bool has_sme     = hwcap2 & HWCAP2_SME;
    bool has_sme2    = hwcap2 & HWCAP2_SME2;
    bool has_mops    = hwcap2 & HWCAP2_MOPS;    // Memory operations (memcpy accel)

    // SVE vector length query
    if (has_sve) {
        uint64_t vl;
        asm volatile("rdvl %0, #1" : "=r"(vl));
        // vl = vector length in bytes (16 for 128b, 32 for 256b, 64 for 512b)
    }
}
```

**RISC-V: multi-method detection:**
```c
#include <sys/hwprobe.h>

void detect_features(void) {
    struct riscv_hwprobe pairs[] = {
        { .key = RISCV_HWPROBE_KEY_IMA_EXT_0 },
        { .key = RISCV_HWPROBE_KEY_CPUPERF_0 },
    };
    long ret = syscall(__NR_riscv_hwprobe, pairs, 2, 0, NULL, 0);

    if (pairs[0].value & RISCV_HWPROBE_IMA_V)
        printf("Vector extension available\n");
    if (pairs[0].value & RISCV_HWPROBE_IMA_ZBA)
        printf("Zba (address generation) available\n");
    if (pairs[0].value & RISCV_HWPROBE_IMA_ZBB)
        printf("Zbb (basic bit manipulation) available\n");
    if (pairs[0].value & RISCV_HWPROBE_IMA_ZBS)
        printf("Zbs (single-bit operations) available\n");

    // Performance info (misaligned access speed)
    if (pairs[1].value & RISCV_HWPROBE_MISALIGNED_FAST)
        printf("Fast misaligned access\n");
}
```

### 16.2 Multi-Versioning and IFUNC

**GCC/Clang function multi-versioning:**
```c
// x86 target_clones (generates multiple versions, dispatched at load time)
__attribute__((target_clones("avx512f", "avx2", "sse4.2", "default")))
void process_data(float *data, int n) {
    for (int i = 0; i < n; i++)
        data[i] = data[i] * 2.0f + 1.0f;
}

// ARM target_version (AArch64 function multi-versioning, GCC 14+/Clang 17+)
__attribute__((target_version("sve2")))
void compute(float *a, float *b, int n) { /* SVE2 version */ }

__attribute__((target_version("sve")))
void compute(float *a, float *b, int n) { /* SVE version */ }

__attribute__((target_version("default")))
void compute(float *a, float *b, int n) { /* NEON fallback */ }
```

**GNU IFUNC (Indirect Function):**
```c
// Resolver function called once at load time
static void *memcpy_resolver(void) {
    if (__builtin_cpu_supports("avx512f"))
        return memcpy_avx512;
    else if (__builtin_cpu_supports("avx2"))
        return memcpy_avx2;
    else
        return memcpy_generic;
}

void *memcpy(void *, const void *, size_t)
    __attribute__((ifunc("memcpy_resolver")));
```

glibc uses IFUNC extensively: `memcpy`, `strcmp`, `strlen`, `memset`, and many other functions have AVX2/AVX-512/SSE4.2 variants selected at runtime.

---

## 17. Key Papers and Resources {#17-key-papers-and-resources}

### Architecture Manuals (Primary Sources)
1. Intel 64 and IA-32 Architectures Software Developer's Manual (SDM), Volumes 1-4, Intel Corporation, 2024+
2. ARM Architecture Reference Manual for A-profile Architecture (ARM DDI 0487), ARM Ltd, 2024+
3. The RISC-V Instruction Set Manual, Volume I: User-Level ISA / Volume II: Privileged Architecture, RISC-V International, 2024
4. Intel Architecture Instruction Set Extensions Programming Reference (covers APX, AVX10, AMX updates)

### Memory Models and Ordering
5. "A Tutorial Introduction to the ARM and POWER Relaxed Memory Models" -- Maranget, Sarkar, Sewell, 2012. Essential reading for understanding weak memory models.
6. "x86-TSO: A Rigorous and Usable Programmer's Model for x86 Multiprocessors" -- Sewell et al., CACM 2010. The formal x86 memory model.
7. "Frightening Small Children and Disconcerting Grown-ups: Concurrency in the Linux Kernel" -- Alglave et al., ASPLOS 2018. How the Linux kernel memory model maps to hardware.
8. "RVWMO: The RISC-V Weak Memory Ordering Model" -- Chapter 17 of the RISC-V ISA specification, with formal litmus test semantics.
9. "Repairing Sequential Consistency in C/C++11" -- Lahav et al., PLDI 2017. Fixes to the C++ memory model.

### SIMD and Vectorization
10. "Auto-Vectorization in LLVM" -- Nuzman & Henderson, various LLVM developer meetings
11. "Rethinking SIMD Vectorization for In-Memory Databases" -- Polychroniou et al., SIGMOD 2015. Foundational work on SIMD for database operations.
12. "Everything You Always Wanted to Know About Compiled and Vectorized Queries But Were Afraid to Ask" -- Kersten et al., VLDB 2018.
13. "The Scalable Vector Extension for AArch64" -- Stephens et al., IEEE Micro 2017.
14. "Arm Scalable Matrix Extension (SME)" -- ARM white paper, 2021.

### Atomics and Lock-Free Programming
15. "The Art of Multiprocessor Programming" -- Herlihy & Shavit, 2008 (revised 2020). The textbook for lock-free data structures.
16. "Simple, Fast, and Practical Non-Blocking and Blocking Concurrent Queue Algorithms" -- Michael & Scott, PODC 1996. The Michael-Scott queue.
17. "Algorithms for Scalable Synchronization on Shared-Memory Multiprocessors" -- Mellor-Crummey & Scott, TOCS 1991. MCS lock and related.
18. "Large System Extensions for ARMv8-A" -- ARM white paper, 2016.

### Performance and Microarchitecture
19. "A Guide to Vectorization with Intel C++ Compilers" -- Intel technical documentation
20. Agner Fog's instruction tables and optimization guides, Technical University of Denmark. The gold standard for x86 instruction latency/throughput data.
21. "Computer Architecture: A Quantitative Approach" -- Hennessy & Patterson, 6th edition, 2017. The RISC-V edition.
22. "Performance Analysis and Tuning on Modern CPUs" -- Denis Bakhvalov, 2020.
23. uops.info -- comprehensive x86 instruction latency/throughput database with measured data.

### Security and Control Flow
24. "Spectre Attacks: Exploiting Speculative Execution" -- Kocher et al., S&P 2019.
25. "Meltdown: Reading Kernel Memory from User Space" -- Lipp et al., USENIX Security 2018.
26. "ARM Pointer Authentication" -- Qualcomm white paper, 2017.
27. "Control-flow Enforcement Technology Specification" -- Intel, 2020.

### Virtualization
28. "Hardware and Software Support for Virtualization" -- Bugnion, Nieh, Tsafrir, Morgan & Claypool, 2017.
29. "KVM/ARM: The Design and Implementation of the Linux ARM Hypervisor" -- Dall & Nieh, ASPLOS 2014.
30. "The RISC-V Hypervisor Extension" -- ratification documentation, RISC-V International, 2024.

### Cryptography
31. "Intel AES-NI Performance Testing" -- Gueron & Lindell, 2010.
32. "ARMv8 Cryptographic Extension" -- ARM white paper.

### Recent Extensions (2024-2025)
33. Intel APX Architecture Specification -- Intel, 2023-2024.
34. ARM Architecture 2024 Extensions (FEAT_LSE128, FEAT_LRCPC3, FEAT_MOPS, FEAT_SME2) -- ARM Ltd.
35. RISC-V Ratified Extensions 2024: Zacas, Zabha, Zicfiss, Zicfilp, Zvk* vector crypto -- RISC-V International.

---

## Appendix A: Quick Reference -- Instruction Equivalence Table

```
Operation                    x86-64              AArch64              RISC-V
========================     ==================  ===================  ==================
Atomic CAS (64-bit)          LOCK CMPXCHG        CAS / LDXR+STXR     amocas.d / LR+SC
Atomic CAS (128-bit)         LOCK CMPXCHG16B     CASP / LDXP+STXP    amocas.q
Atomic fetch-add             LOCK XADD           LDADD                amoadd.d
Atomic swap                  XCHG (implicit LOCK) SWP                 amoswap.d
Full memory barrier          MFENCE              DMB ISH              fence rw,rw
Load-acquire                 MOV (implicit)      LDAR                 ld + fence r,rw
Store-release                MOV (implicit)       STLR                fence rw,w + sd
Population count             POPCNT              CNT (NEON) + ADDV    cpop
Count leading zeros          LZCNT               CLZ                  clz
Count trailing zeros         TZCNT               RBIT + CLZ           ctz
Byte swap                    BSWAP               REV                  rev8
Bit field extract            BEXTR               UBFX                 (Zbs: bext)
Parallel bit extract         PEXT (BMI2)         BEXT (SVE2)          (Zbe: bext)
Parallel bit deposit         PDEP (BMI2)         BDEP (SVE2)          (Zbe: bdep)
AES round                    AESENC              AESE + AESMC         aes64esm
SHA-256 round                SHA256RNDS2         SHA256H              sha256sig0/sum0
CRC-32C                      CRC32 (SSE4.2)      CRC32C*              clmul-based
Prefetch (read)              PREFETCHT0          PRFM PLDL1KEEP       prefetch.r
Cache line writeback         CLWB                DC CVAC              cbo.clean
Non-temporal store           MOVNTI              STNP (hint only)     (no standard)
Cycle counter read           RDTSC/RDTSCP        MRS CNTVCT_EL0       rdcycle
Spin-wait hint               PAUSE               WFE (or YIELD)       pause (Zihintpause)
Speculation barrier          LFENCE              SB / CSDB            (fence.t proposed)
Indirect branch guard        ENDBR64 (CET)       BTI (FEAT_BTI)       LPAD (Zicfilp)
Return address signing       (no equivalent)     PACIA/AUTIA (PAC)    SSPUSH/SSPOPCHK
Pointer sign                 (no equivalent)     PACIA Xd, Xn         (no equivalent)
Shadow stack push            INCSSPQ (CET-SS)    (PAC replaces need)  SSPUSH (Zicfiss)
Hardware txn begin           XBEGIN (TSX*)       TSTART (TME*)        (none ratified)
VM entry                     VMLAUNCH (VMX)      ERET to EL1 (EL2)    (H ext: mret/sret)
Hypercall                    VMCALL              HVC #imm             ecall
Gather load (SIMD)           VPGATHERDD          LD1 (SVE gather)     vluxei32.v
Scatter store (SIMD)         VPSCATTERDD         ST1 (SVE scatter)    vsuxei32.v
SIMD compare -> mask         VPCMPD k1,zmm,zmm   CMP (SVE -> pred)   vmseq.vv (-> v0)
FMA (scalar)                 VFMADD231SD         FMADD Dd,Dn,Dm,Da   fmadd.d
FMA (vector)                 VFMADD231PS zmm     FMLA Zd.S,...(SVE)   vfmacc.vv
Memcpy acceleration          REP MOVSB (ERMS)    CPYFP/M/E (MOPS)    (RVV loop)
Zero cache line              (CLFLUSH + write)   DC ZVA               cbo.zero
```

(*) TSX effectively deprecated. ARM TME not yet in production silicon.

---

## Appendix B: Microarchitectural Latency Cheat Sheet (Approximate, 2024 CPUs)

```
Operation                    Intel SPR   AMD Zen4   Apple M4   Neoverse V2   SiFive P870*
========================     =========   ========   ========   ===========   ===========
Integer ADD/SUB              1 cy        1 cy       1 cy       1 cy          ~1 cy
Integer MUL (64-bit)         3 cy        3 cy       3 cy       3 cy          ~3-4 cy
Integer DIV (64-bit)         ~35-90 cy   ~11-18 cy  ~7 cy      ~7-12 cy      ~20+ cy
FP ADD (double)              4 cy        3 cy       3 cy       2 cy          ~4-5 cy
FP MUL (double)              4 cy        3 cy       3 cy       3 cy          ~4-5 cy
FP FMA (double)              4 cy        4 cy       4 cy       4 cy          ~4-5 cy
FP DIV (double)              13-14 cy    13 cy      ~7 cy      ~7-10 cy      ~15+ cy
L1D hit                      5 cy        4 cy       3 cy       4 cy          ~4-5 cy
L2 hit                       ~12 cy      ~12 cy     ~6 cy      ~10 cy        ~10+ cy
L3 hit                       ~40-50 cy   ~30-40 cy  ~16 cy     ~30-40 cy     ~30+ cy
DRAM access                  ~150+ cy    ~100+ cy   ~100+ cy   ~100+ cy      ~100+ cy
LOCK CMPXCHG (uncontended)   ~18-22 cy   ~15-18 cy  N/A        N/A           N/A
LDAR (ARM acquire load)      N/A         N/A        ~1-3 cy    ~1-3 cy       N/A
LR/SC (uncontended)          N/A         N/A        N/A        N/A           ~10-15 cy*
MFENCE                       ~33 cy      ~20 cy     N/A        N/A           N/A
DMB ISH                      N/A         N/A        ~5-10 cy   ~10-20 cy     N/A
fence rw,rw                  N/A         N/A        N/A        N/A           ~10-20 cy*
Branch mispredict            ~14 cy      ~11-13 cy  ~12-14 cy  ~11-13 cy     ~10+ cy*
RDTSC                        ~20 cy      ~10 cy     N/A        N/A           N/A
POPCNT                       1-3 cy      1 cy       N/A(vec)   N/A(vec)      ~1 cy(Zbb)
AES round                    1 cy(4lat)  1 cy(4lat) ~1 cy      ~2 cy         TBD
```

*SiFive P870 numbers are estimated/projected from available documentation.

---

*Document covers ISA state as of early 2025. Extensions in development (e.g., ARM CCA Realms, RISC-V Ztm, Intel APX full deployment) may change the landscape. Always verify against current architecture reference manuals.*

---

## See Also

- [Cycle Counters and Energy](@/notes/hardware/cycle_counters_and_energy.md) — RDTSC/CNTVCT usage and per-cycle energy costs for instructions covered here
- [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) — GPU SIMT execution model and Tensor Core instructions complement CPU ISA coverage
- [VFIO Internals](@/notes/os/vfio_internals.md) — IOMMU and virtualization instructions (VMX, ARM EL2, RISC-V H-ext) in production use
- [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) — Syscalls leveraging hardware features (rseq, pkeys, io_uring) that map to ISA instructions
- [Join Algorithms](@/notes/database/join_algorithms.md) — SIMD-vectorized hash joins that exploit the vector instructions documented here
