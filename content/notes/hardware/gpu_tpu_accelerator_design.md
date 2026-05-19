+++
title = "GPU TPU Accelerator Design"
date = 2026-03-04
[taxonomies]
tags = ["gpu", "tpu", "tensor-core", "simt", "systolic-array", "vlsi", "chiplet", "memory-hierarchy"]
+++


# GPU, TPU, and LLM Accelerator Chip Design

A comprehensive deep dive into GPU microarchitecture, TPU/AI accelerator design, numerical formats, interconnects, and the complete chip design flow from RTL to silicon — aimed at someone who wants to design and build a world-class AI accelerator.

---

## Table of Contents

1. [GPU Architecture Fundamentals](#1-gpu-architecture-fundamentals)
2. [GPU Microarchitecture Evolution](#2-gpu-microarchitecture-evolution)
3. [Memory Hierarchy Deep Dive](#3-memory-hierarchy-deep-dive)
4. [Google TPU Architecture](#4-google-tpu-architecture)
5. [Production AI Accelerators](#5-production-ai-accelerators)
6. [Systolic Arrays and Dataflow Architectures](#6-systolic-arrays-and-dataflow-architectures)
7. [Numerical Formats for AI Hardware](#7-numerical-formats-for-ai-hardware)
8. [LLM-Specific Hardware Design](#8-llm-specific-hardware-design)
9. [On-Chip Interconnect and NoC Design](#9-on-chip-interconnect-and-noc-design)
10. [Chip-to-Chip Interconnects and Packaging](#10-chip-to-chip-interconnects-and-packaging)
11. [VLSI Design Flow: RTL to GDSII](#11-vlsi-design-flow-rtl-to-gdsii)
12. [Process Technology and Transistor Design](#12-process-technology-and-transistor-design)
13. [Memory Design for Accelerators](#13-memory-design-for-accelerators)
14. [Power Delivery and Thermal Design](#14-power-delivery-and-thermal-design)
15. [Verification, Testing, and Tapeout](#15-verification-testing-and-tapeout)
16. [Open-Source Chip Design](#16-open-source-chip-design)
17. [Practical Path to Building Your Own Chip](#17-practical-path-to-building-your-own-chip)
18. [Performance Analysis: Roofline Model](#18-performance-analysis-roofline-model)
19. [Key Papers and References](#19-key-papers-and-references)

---

## 1. GPU Architecture Fundamentals

### 1.1 SIMT Execution Model

GPUs use **Single Instruction, Multiple Thread (SIMT)**, distinct from CPU-style SIMD. In SIMD, a single instruction operates on a vector register (e.g., AVX-512 processes 16 floats). In SIMT, each thread has its own registers and program counter (logically), but threads are grouped into **warps** (NVIDIA, 32 threads) or **wavefronts** (AMD, 32 or 64 threads) that execute the same instruction in lockstep.

Key differences from SIMD:
- **Per-thread addressing**: Each thread computes its own memory address (scatter/gather native)
- **Branch divergence**: Threads in a warp can take different paths (at a performance cost)
- **Per-thread register state**: Each thread has its own architectural registers
- **Hardware-managed scheduling**: Thousands of threads scheduled by hardware, not software

```
SIMD (CPU):                         SIMT (GPU):
┌──────────────────────┐            ┌──────────────────────┐
│ Single instruction   │            │ Single instruction   │
│ operates on ONE      │            │ issued to 32 threads │
│ wide vector register │            │ each with OWN regs   │
│                      │            │                      │
│ VADD zmm0, zmm1, zmm2│           │ FADD R1, R2, R3      │
│ [16 floats in one reg]│           │ [32 threads execute] │
└──────────────────────┘            └──────────────────────┘
```

### 1.2 Streaming Multiprocessor (SM) Architecture

The SM (NVIDIA) or Compute Unit (AMD) is the fundamental building block. A modern SM (Hopper) contains:

```
┌─────────────────────────────────────────────────────────┐
│                    Streaming Multiprocessor (SM)         │
│                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │Warp Sched 0 │  │Warp Sched 1 │  │Warp Sched 2 │ ... │
│  │Dispatch Unit│  │Dispatch Unit│  │Dispatch Unit│     │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘     │
│         │                │                │             │
│  ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐     │
│  │ FP32 Units  │  │ FP32 Units  │  │ FP32 Units  │     │
│  │ INT32 Units │  │ INT32 Units │  │ INT32 Units │     │
│  │ FP64 Units  │  │ FP64 Units  │  │ FP64 Units  │     │
│  │ Tensor Cores│  │ Tensor Cores│  │ Tensor Cores│     │
│  │ LD/ST Units │  │ LD/ST Units │  │ LD/ST Units │     │
│  │ SFU         │  │ SFU         │  │ SFU         │     │
│  └─────────────┘  └─────────────┘  └─────────────┘     │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │              Register File (256 KB)              │    │
│  └─────────────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────────────┐    │
│  │           Shared Memory / L1 Cache (228 KB)      │    │
│  └─────────────────────────────────────────────────┘    │
│  ┌──────────────────┐  ┌──────────────────┐             │
│  │   Tex/L1 Cache   │  │  Constant Cache  │             │
│  └──────────────────┘  └──────────────────┘             │
└─────────────────────────────────────────────────────────┘
```

**SM partitioning** (since Volta): Each SM is divided into 4 **processing blocks** (sub-cores), each with:
- 1 warp scheduler + 1 dispatch unit
- 16 FP32 cores, 16 INT32 cores, 8 FP64 cores (varies by arch)
- 1 Tensor Core (4th-gen in Hopper)
- 4 LD/ST units, 4 SFU (Special Function Units: sin, cos, rsqrt, etc.)
- Portion of the register file

### 1.3 Register File Design

The register file is the **largest on-chip storage** — 256 KB per SM in Hopper (more than L1 cache). Each thread gets a set of 32-bit registers (up to 255 per thread). Design:

- **Banked architecture**: Divided into banks (typically 4-8) to allow multiple simultaneous accesses
- **Operand collectors**: Buffer register values before they reach ALUs. Collects operands across multiple cycles from different banks to avoid bank conflicts
- **No renaming**: Unlike CPUs, GPU register files use physical = architectural registers (compiler allocates)
- **Occupancy tradeoff**: More registers per thread = fewer warps fit in SM = less latency hiding

```
Register file per SM (Hopper):
- 65,536 × 32-bit registers = 256 KB
- At 255 regs/thread: floor(65536/255) × 32 = ~8 warps (256 threads)
- At 32 regs/thread: floor(65536/32) × 32 = ~64 warps (2048 threads)

Occupancy = active warps / max warps = latency hiding ability
```

### 1.4 Warp Scheduling

Hardware warp schedulers pick which ready warp executes each cycle. Policies:

| Policy | Description | Best For |
|--------|-------------|----------|
| **Round-Robin (RR)** | Cycle through warps in order | Uniform workloads |
| **Greedy-Then-Oldest (GTO)** | Run same warp until stall, then pick oldest ready | Cache locality |
| **Two-Level** | Divide warps into active/pending groups, RR within active | Reduce cache thrashing |
| **CCWS** | Cache-conscious warp scheduling — limit active warps to fit in cache | Memory-intensive |
| **Loose Round-Robin (LRR)** | RR but skip stalled warps | General purpose |

Modern GPUs (Volta+) use a variant of **GTO with aging** to prevent starvation.

### 1.5 Branch Divergence

When threads in a warp take different branch paths:

**Pre-Volta (SIMT stack)**:
```
if (threadIdx.x < 16) {  // Divergent branch
    A();                   // Threads 0-15 execute, 16-31 masked
} else {
    B();                   // Threads 16-31 execute, 0-15 masked
}
// Reconvergence point — all threads active again
```
Uses a **reconvergence stack** — push the reconvergence PC and active mask, execute each path with the appropriate mask, pop at reconvergence. Nested divergence nests stack entries.

**Volta+ (Independent Thread Scheduling)**:
Each thread has its own program counter and call stack. Threads can diverge and reconverge at arbitrary points. Enables:
- **Warp-level synchronization primitives** (`__syncwarp()`)
- Threads can wait for each other within a warp
- Fine-grained interleaving of divergent paths

### 1.6 Thread Block Scheduling

The **GigaThread Engine** (NVIDIA) / **Command Processor** (AMD) distributes thread blocks (workgroups) to SMs:

1. Check SM resource availability: registers, shared memory, thread slots, block slots
2. Assign block to SM that has sufficient resources
3. SM's internal warp schedulers manage constituent warps
4. Block retires when all warps complete; resources freed

**Persistent kernels**: Launch exactly as many blocks as SMs can hold, loop internally — avoids block launch overhead for iterative algorithms.

### 1.7 Scoreboarding and Dependency Tracking

GPUs use a **scoreboard** per warp to track outstanding operations:
- When an instruction issues, destination register is marked pending
- Subsequent instructions reading that register stall until result is written back
- **No out-of-order execution within a warp** — instructions issue in order but complete out of order
- Warp scheduler switches to another ready warp instead of stalling the pipeline

This is fundamentally different from CPU OoO: GPUs hide latency through **thread-level parallelism** rather than instruction-level parallelism.

---

## 2. GPU Microarchitecture Evolution

### 2.1 NVIDIA Architecture Generations

| Gen | Year | Node | Key Innovation | SM Count (top) | Tensor Cores |
|-----|------|------|---------------|----------------|-------------|
| **Tesla** (G80) | 2006 | 90nm | Unified shaders, CUDA | 16 (128 cores) | — |
| **Fermi** (GF100) | 2010 | 40nm | L1/L2 cache, ECC, 64-bit | 16 (512 cores) | — |
| **Kepler** (GK110) | 2012 | 28nm | Dynamic parallelism, Hyper-Q, shuffle | 15 (2880 cores) | — |
| **Maxwell** (GM200) | 2014 | 28nm | Energy efficiency, shared mem redesign | 24 (3072 cores) | — |
| **Pascal** (GP100) | 2016 | 16nm | HBM2, NVLink, unified memory | 56 (3584 cores) | — |
| **Volta** (GV100) | 2017 | 12nm | **Tensor Cores (1st gen)**, independent thread sched | 80 (5120 cores) | 640 |
| **Turing** (TU102) | 2018 | 12nm | RT cores, INT8/INT4 tensor, async copy | 72 (4608 cores) | 576 |
| **Ampere** (GA100) | 2020 | 7nm | TF32, sparsity, 3rd-gen NVLink | 108 (6912 cores) | 432 |
| **Hopper** (GH100) | 2022 | 4nm | **Transformer Engine**, FP8, DPX, TMA | 132 (16896 cores) | 528 |
| **Blackwell** (GB100) | 2024 | 4nm | 2 dies, 5th-gen Tensor, FP4, NVLink5 | 2×(?) | ~1000+ |

### 2.2 Tensor Core Design (Deep Dive)

Tensor Cores perform **matrix multiply-accumulate (MMA)** on small tiles:

```
D = A × B + C

Where:
  A is m×k, B is k×n, C and D are m×n

1st gen (Volta):  4×4×4 FP16 → FP32 accumulate
2nd gen (Turing): + INT8 (8×8×16), INT4 (8×8×32), INT1 (8×8×128)
3rd gen (Ampere): + TF32 (unmasked FP32 input), BF16, FP64 tensor
                  + Structured sparsity (2:4) → 2× throughput
4th gen (Hopper): + FP8 (E4M3, E5M2), 256×256 via warp groups
                  + Transformer Engine: automatic FP8↔FP16 scaling
5th gen (Blackwell): + FP4, second-gen Transformer Engine
                     + 2× Hopper performance
```

**How a Tensor Core works internally**:
```
Cycle 1: Load A fragment (4×4 FP16) from register file
Cycle 2: Load B fragment (4×4 FP16) from register file
Cycle 3-6: Multiply-accumulate pipeline
  - 4×4 dot products using fused multiply-add trees
  - FP16 multipliers feed into FP32 adder tree
  - Result accumulated into FP32 register
Cycle 7: Write D fragment back to register file

Throughput: 1 MMA per cycle per Tensor Core
  = 4×4×4×2 = 128 FP16 FLOPS/cycle (Volta)
  = With 640 TCs at 1.53 GHz = 125 TFLOPS FP16 (V100)
```

**Structured Sparsity (Ampere+)**:
Hardware enforces **2:4 sparsity** — in every group of 4 values, exactly 2 are zero. A compressed format stores only the 2 non-zero values + a 2-bit index. Tensor Core skips zero multiplications → 2× throughput.

```
Dense:    [1.5, 0, 0.3, 0]  → Sparse: [1.5, 0.3] + index [0,2]
Hardware: Only does 2 multiplies instead of 4
```

### 2.3 Hopper Architecture Specifics

Key innovations relevant to accelerator design:

**Tensor Memory Accelerator (TMA)**: Dedicated hardware unit that handles async multi-dimensional tensor copies between global memory and shared memory. Eliminates address calculation overhead from the SM.

```
// Without TMA: each thread calculates addresses, issues loads
// With TMA: single TMA descriptor handles entire tile copy
tma.load_async [shared_ptr], [tma_desc], {coord0, coord1};
```

**Thread Block Clusters**: New hierarchy level — group of thread blocks that can cooperate:
```
Grid → Cluster → Thread Block → Warp → Thread
                 (new level)
```
Clusters enable distributed shared memory — thread blocks in a cluster can directly access each other's shared memory.

**DPX Instructions**: Hardware acceleration for dynamic programming (Smith-Waterman, Needleman-Wunsch, Viterbi) — 7× speedup over Ampere.

**Warp Group MMA**: 4 warps (128 threads) cooperate on a single large MMA operation, enabling 256×256 matrix tiles.

### 2.4 AMD GPU Architecture

**GCN (Graphics Core Next, 2012-2019)**:
- 64-wide wavefronts
- 4 SIMD16 units per CU, each executing 1/4 of a wavefront per cycle
- 64 KB LDS (Local Data Share) per CU
- Scalar ALU + scalar register file per CU (for uniform operations)

**CDNA (Compute DNA, MI100/MI200/MI250X/MI300X)**:
- Dedicated compute architecture (no graphics pipeline)
- MI300X: 304 CUs, 192 GB HBM3, 5.3 TB/s bandwidth
- Matrix cores: MFMA instructions (Matrix Fused Multiply-Add)
  - FP16: 32×32×8 per cycle per CU
  - BF16, FP8, INT8 supported
- **Chiplet design**: MI300X = 8 XCD (Accelerator Complex Dies) + 4 IODs
- Infinity Fabric for die-to-die communication

**RDNA (2019+)**:
- 32-wide wavefronts (wave32) — better for divergent graphics workloads
- Dual-issue: can issue 2 wave32 instructions per cycle (effectively wave64)
- Dedicated AI accelerators in RDNA 3+

### 2.5 Intel GPU Architecture (Xe/Arc)

- **Xe-HPC (Ponte Vecchio)**: Data center GPU for AI/HPC
  - 128 Xe-cores, each with 8 vector + 8 matrix engines
  - XMX (Xe Matrix Extensions) — systolic array per Xe-core
  - 128 GB HBM2e, 3.2 TB/s
  - 47 tiles connected via EMIB and Foveros packaging
  - Supports INT8, BF16, FP32, TF32

---

## 3. Memory Hierarchy Deep Dive

### 3.1 Complete Memory Hierarchy

```
┌─────────────────────────────────────────────────┐
│ Registers (256 KB/SM) — 0 cycle latency         │  ~20 TB/s (estimate)
├─────────────────────────────────────────────────┤
│ Shared Memory (228 KB/SM) — ~20-30 cycle        │  ~19 TB/s aggregate
├─────────────────────────────────────────────────┤
│ L1 Data Cache (part of 228KB) — ~30 cycle       │  ~same as shared mem
├─────────────────────────────────────────────────┤
│ L2 Cache (50 MB, Hopper) — ~200 cycle           │  ~12 TB/s
├─────────────────────────────────────────────────┤
│ HBM3 (80 GB, Hopper) — ~400-600 cycle           │  3.35 TB/s
├─────────────────────────────────────────────────┤
│ PCIe/NVLink (system memory) — 1000+ cycle       │  64-900 GB/s
└─────────────────────────────────────────────────┘
```

### 3.2 HBM (High Bandwidth Memory)

HBM stacks DRAM dies vertically on a silicon interposer next to the GPU die:

```
           ┌─────────┐ ┌─────────┐
           │ DRAM Die │ │ DRAM Die │  ← 4-12 DRAM dies stacked
           │ DRAM Die │ │ DRAM Die │
           │ DRAM Die │ │ DRAM Die │
           │ Logic Die│ │ Logic Die│  ← Base logic die with PHY
           └────┬─────┘ └────┬─────┘
     ───────────┴─────────────┴──────────────
     │          Silicon Interposer            │  ← Passive Si with wiring
     │  ┌─────────────────────────────────┐   │
     │  │          GPU Die                │   │
     │  └─────────────────────────────────┘   │
     ─────────────────────────────────────────
     │           Package Substrate            │
     ─────────────────────────────────────────
```

| Spec | HBM2 | HBM2e | HBM3 | HBM3e |
|------|-------|-------|------|-------|
| **Pin bandwidth** | 2 Gbps | 3.6 Gbps | 6.4 Gbps | 9.6 Gbps |
| **Bus width/stack** | 1024-bit | 1024-bit | 1024-bit | 1024-bit |
| **Stacks (typical)** | 4 | 4-6 | 4-6 | 6-8 |
| **Channels/stack** | 8 | 8 | 16 | 16 |
| **Capacity/stack** | 8 GB | 16 GB | 16 GB | 24 GB |
| **BW/stack** | 256 GB/s | 460 GB/s | 819 GB/s | 1.2 TB/s |
| **Total (6 stacks)** | — | 2.76 TB/s | 4.9 TB/s | 7.2 TB/s |

**Why HBM matters for AI**: A 70B parameter model in FP16 = 140 GB. At batch=1 decoding, each token reads all parameters once. To generate 100 tokens/s, need 140 GB × 100 = 14 TB/s — this is why memory bandwidth is the bottleneck.

### 3.3 Memory Coalescing

When a warp issues a load, 32 threads each request an address. The **coalescing unit** merges these into minimum memory transactions:

```
Coalesced access (ideal):
  Thread 0 → addr 0x1000    ┐
  Thread 1 → addr 0x1004    │ → 1 × 128-byte transaction
  Thread 2 → addr 0x1008    │   (all within one cache line)
  ...                        │
  Thread 31 → addr 0x107C   ┘

Strided access (worst case):
  Thread 0 → addr 0x1000    → 1 × 128-byte transaction
  Thread 1 → addr 0x1200    → 1 × 128-byte transaction  (different lines)
  Thread 2 → addr 0x1400    → 1 × 128-byte transaction
  ...
  Thread 31 → ???           → 32 × 128-byte transactions = 32× bandwidth waste
```

Hardware: The LD/ST unit contains an **address crossbar** that sorts thread addresses, groups them by cache line, and issues the minimum number of memory transactions (1 to 32 per warp instruction).

### 3.4 Shared Memory Bank Conflicts

Shared memory is divided into **32 banks** (one per thread in a warp), each 4 bytes wide. Accesses to different banks proceed in parallel; two threads accessing the same bank cause a **bank conflict** (serialized).

```
No conflict (each thread hits different bank):
  Thread 0 → Bank 0
  Thread 1 → Bank 1
  ...
  Thread 31 → Bank 31

2-way bank conflict (2 threads hit bank 0):
  Thread 0 → Bank 0  ┐ serialized
  Thread 16 → Bank 0 ┘
  Thread 1 → Bank 1    (parallel with others)
  ...

Broadcast (all threads read SAME address in one bank):
  → No conflict! Hardware broadcasts the value
```

**Design implication**: If you're designing shared memory for your accelerator, you need either:
1. Enough banks to match your SIMT width (32 banks for 32-wide)
2. Multiported memory (expensive in area)
3. Banking + arbitration + replay for conflicts

### 3.5 L2 Cache and Memory Partitions

The L2 cache is **sliced** across memory partitions:

```
┌──────────────────────────────────────────────┐
│                    GPC 0-7                    │
│  [SM][SM][SM]...[SM][SM][SM]...[SM][SM][SM]  │
└──────────────────┬───────────────────────────┘
                   │ Crossbar / NoC
┌──────┬──────┬──────┬──────┬──────┬──────┐
│L2 P0 │L2 P1 │L2 P2 │L2 P3 │...  │L2 Pn │
│+ MC0 │+ MC1 │+ MC2 │+ MC3 │     │+ MCn │
└──┬───┘──┬───┘──┬───┘──┬───┘     └──┬───┘
   │      │      │      │            │
  HBM0  HBM1   HBM2   HBM3   ...  HBMn
```

**Partition camping**: If all SMs access addresses that hash to the same L2 partition, that partition becomes a bottleneck. Address interleaving (XOR-based hashing) distributes accesses across partitions.

Hopper L2: 50 MB total, ~12 TB/s aggregate bandwidth, 64-byte sectors.

### 3.6 Memory Controller and Scheduling

Each memory controller manages one HBM channel. Key scheduling policies:

- **FR-FCFS (First-Ready, First-Come-First-Served)**: Prioritize row-buffer hits (already open page), then oldest request. Maximizes DRAM bandwidth but can starve some requestors.
- **BLISS (Blacklisting scheduler)**: Identifies and throttles threads that cause interference
- **ATLAS**: Attentive scheduling — prioritizes threads that have used least memory service

**Row buffer**: Each DRAM bank has a ~8 KB row buffer. Opening a new row costs ~30 ns, but reading from an already-open row costs ~10 ns. Row hit rate critically affects effective bandwidth.

---

## 4. Google TPU Architecture

### 4.1 TPU v1 (2015) — Inference Only

The paper that started it all: "In-Datacenter Performance Analysis of a Tensor Processing Unit" (Jouppi et al., ISCA 2017).

```
┌─────────────────────────────────────────────┐
│                   TPU v1                     │
│                                             │
│  ┌──────────────────────────────────────┐   │
│  │    Matrix Multiply Unit (MXU)        │   │
│  │    256 × 256 systolic array          │   │
│  │    8-bit multiply, 32-bit accumulate │   │
│  │    = 65,536 MACs/cycle               │   │
│  │    @ 700 MHz = 92 TOPS (INT8)       │   │
│  └──────────────────────────────────────┘   │
│                                             │
│  ┌──────────────┐  ┌──────────────────┐     │
│  │ Unified      │  │ Activation       │     │
│  │ Buffer       │  │ Pipeline         │     │
│  │ 24 MB SRAM   │  │ (ReLU, sigmoid,  │     │
│  │ (weights +   │  │  tanh, etc.)     │     │
│  │  activations)│  │                  │     │
│  └──────────────┘  └──────────────────┘     │
│                                             │
│  ┌──────────────┐  ┌──────────────────┐     │
│  │ Weight FIFO  │  │ Accumulators     │     │
│  │ (256×256×8b  │  │ (4096 × 256 ×   │     │
│  │  = 64 KB)    │  │  32-bit = 4 MB)  │     │
│  └──────────────┘  └──────────────────┘     │
│                                             │
│  PCIe Gen3 x16 connection to host CPU       │
│  28nm process, ~75W TDP                     │
└─────────────────────────────────────────────┘
```

**Design philosophy**: No caches, no branch predictors, no OoO execution. Deterministic, CISC-style instructions that each take many cycles. The MXU does ~92 TOPS while consuming far less power than a GPU because it eliminates general-purpose overhead.

### 4.2 TPU v2/v3 — Training Support

TPU v2 added:
- **bfloat16 support** (Google invented this format for TPUs)
- **HBM** for high-bandwidth parameter storage
- **128×128 MXU** (bfloat16 multiply, FP32 accumulate)
- **Vector Processing Unit (VPU)** for element-wise operations
- **Scalar Processing Unit** for control flow
- **Inter-Chip Interconnect (ICI)** — 2D torus topology for multi-chip training

TPU v3:
- Liquid cooling (first Google liquid-cooled chip)
- 2× FP16 FLOPS over v2 (~123 TFLOPS BF16)
- Larger HBM (32 GB per chip)

### 4.3 TPU v4 (2021)

```
┌───────────────────────────────────────────────┐
│                    TPU v4                      │
│                                               │
│  ┌──────────────┐  ┌──────────────────────┐   │
│  │ 2× MXU       │  │ Vector Processing    │   │
│  │ 128×128 each │  │ Unit (VPU)           │   │
│  │ BF16→FP32    │  │ - SIMD vector ops    │   │
│  │ 275 TFLOPS   │  │ - Activation funcs   │   │
│  │ (BF16)       │  │ - Normalization      │   │
│  └──────────────┘  └──────────────────────┘   │
│                                               │
│  ┌──────────────┐  ┌──────────────────────┐   │
│  │ SparseCore   │  │ Scalar Unit          │   │
│  │ (sparse      │  │ (control flow,       │   │
│  │  embedding   │  │  address gen)        │   │
│  │  lookups)    │  │                      │   │
│  └──────────────┘  └──────────────────────┘   │
│                                               │
│  32 GB HBM2e, 1.2 TB/s bandwidth             │
│  ICI: 6 links × 50 GB/s = 300 GB/s bidirec.  │
│  7nm process                                  │
└───────────────────────────────────────────────┘
```

**TPU v4 Pod** (4096 chips):
- 3D torus topology (4×4×4 cubes connected into larger 3D mesh)
- **Optical Circuit Switches (OCS)** reconfigure the torus topology dynamically
- 1.1 EFLOPS (BF16) per pod
- **SparseCore**: Dedicated hardware for embedding table lookups (critical for recommendation models)

### 4.4 TPU v5e / v5p / v6 (Trillium)

**TPU v5e** (cost-optimized):
- Targets inference and small-medium training
- Same BF16 TFLOPS as v4 but cheaper per chip

**TPU v5p** (performance):
- 2× BF16 FLOPS over v4 (~459 TFLOPS)
- Larger HBM (95 GB HBM2e per chip, 2.76 TB/s)
- Improved ICI bandwidth

**TPU v6 (Trillium, 2024)**:
- 4.7× compute improvement over v5e
- 256-chip pod configurations
- Improved energy efficiency
- Support for FP8 types

### 4.5 Systolic Array Operation (MXU Details)

The MXU is a 2D systolic array. Data flows through in a pipelined fashion:

```
Weight-Stationary Dataflow:
  - Weights are pre-loaded into each PE (Processing Element)
  - Activations flow left-to-right
  - Partial sums flow top-to-bottom

           Input Activations →
           a0  a1  a2  a3
           ↓   ↓   ↓   ↓
     ┌────┬────┬────┬────┐
w0 → │ PE │ PE │ PE │ PE │ → (partial sums out)
     ├────┼────┼────┼────┤
w1 → │ PE │ PE │ PE │ PE │
     ├────┼────┼────┼────┤
w2 → │ PE │ PE │ PE │ PE │
     ├────┼────┼────┼────┤
w3 → │ PE │ PE │ PE │ PE │
     └────┴────┴────┴────┘
           ↓   ↓   ↓   ↓
         Output partial sums

Each PE: accumulator += weight × input_activation
After N cycles, bottom row outputs contain C[i][j] = Σ A[i][k] × B[k][j]
```

**Pipeline timing** for a 128×128 array:
- Cycle 0: a[0][0] enters PE[0][0]
- Cycle 1: a[0][0] reaches PE[0][1], a[0][1] enters PE[0][0], a[1][0] enters PE[1][0]
- Cycle 127: first element reaches PE[0][127]
- Cycle 254 (= 2N-2): last output available at PE[127][127]
- **Latency**: 2N-2 cycles to fill + drain the array
- **Throughput**: After pipeline fills, one N×N result matrix per N cycles

**Handling non-square matrices**: Tile the computation. For M×K × K×N:
- Tile into 128×128 chunks
- Each tile goes through the MXU
- Accumulate partial results in the accumulator buffer
- Total MXU invocations: ceil(M/128) × ceil(N/128) × ceil(K/128)

---

## 5. Production AI Accelerators

### 5.1 Cerebras WSE-3 (Wafer-Scale Engine)

The most radical approach: **one chip = one entire wafer**.

```
┌─────────────────────────────────────┐
│          WSE-3 (2024)               │
│                                     │
│  Die size: 46,225 mm² (full wafer)  │
│  Transistors: 4 trillion            │
│  Cores: 900,000                     │
│  SRAM: 44 GB on-chip               │
│  Memory BW: 21 PB/s (on-chip SRAM) │
│  Interconnect: 214 Pb/s on-wafer   │
│  FP16: ~125 PFLOPS                 │
│  Process: TSMC 5nm                  │
│  TDP: ~23 kW (liquid cooled)       │
└─────────────────────────────────────┘
```

**Key innovations**:
- **No HBM**: All memory is SRAM on the wafer → 21 PB/s bandwidth (1000× GPU)
- **Weight streaming**: Model stored off-chip (in MemoryX boxes). Weights streamed in layer-by-layer through 1.2 TB/s of external I/O. Works because neural nets are sequential by layer.
- **No caches**: All SRAM is software-managed scratchpad
- **Swarm programming model**: Thousands of cores work cooperatively
- **Yield management**: Redundant cores and routing to handle defective regions

**When it shines**: Memory-bandwidth-bound workloads (large LLM inference, sparse models).

### 5.2 Graphcore IPU (Intelligence Processing Unit)

```
IPU Mk2 (Colossus GC200):
  - 1,472 independent processing tiles
  - Each tile: 1 core with 6 hardware threads + 624 KB SRAM
  - Total SRAM: ~900 MB per chip
  - FP16: 250 TFLOPS
  - BSP (Bulk Synchronous Parallel) execution model

Execution model:
  Phase 1: COMPUTE — all tiles compute independently
  Phase 2: EXCHANGE — all-to-all communication via exchange fabric
  Phase 3: SYNC — barrier synchronization
  Repeat.
```

**BSP model**: Simplifies programming because you don't worry about race conditions during compute phases. Communication is explicit and synchronized. Good for sparse and irregular workloads.

### 5.3 Groq TSP (Tensor Streaming Processor)

```
┌─────────────────────────────────────────┐
│              Groq TSP                    │
│                                         │
│  Architecture: Deterministic dataflow   │
│  - NO caches (everything compiler-      │
│    scheduled)                           │
│  - NO branch prediction                 │
│  - NO runtime scheduling               │
│  - 230 MB SRAM per chip                │
│  - Time-deterministic: same input =    │
│    exact same cycle count every time    │
│                                         │
│  Functional slices:                     │
│  ┌─────┬─────┬─────┬─────┬─────┐       │
│  │ MXM │ MXM │ VXM │ SXM │ MEM │       │
│  │     │     │     │     │     │       │
│  │Mat  │Mat  │Vec  │Scal │Mem  │       │
│  │Mult │Mult │Op   │Op   │     │       │
│  └─────┴─────┴─────┴─────┴─────┘       │
│  Data streams between slices via        │
│  compiler-scheduled superlanes          │
│                                         │
│  FP8: ~750 TOPS, INT8: ~750 TOPS      │
│  14nm process, ~300W                    │
└─────────────────────────────────────────┘
```

**Philosophy**: Move all scheduling complexity from hardware to the compiler. Zero overhead at runtime → extremely high utilization and predictable latency. Perfect for inference where the computation graph is known at compile time.

### 5.4 Tenstorrent (Jim Keller's Architecture)

```
Wormhole Architecture:
  - Tensix cores: RISC-V control + dedicated matrix/vector engines
  - Each Tensix core:
    ┌──────────────────────────────┐
    │ 5× RISC-V cores (control)   │
    │ 1× FPU (matrix engine)      │
    │ 1× SFPU (vector engine)     │
    │ 1.5 MB SRAM                 │
    └──────────────────────────────┘
  - 80 Tensix cores per chip
  - 2D mesh NoC with Ethernet-based chip-to-chip

Blackhole (latest):
  - More Tensix cores, higher clocks
  - Chiplet-ready architecture
  - Open-source software stack
```

**Philosophy**: Commodity-like scalability. Each chip is relatively simple; scale by tiling many chips together with Ethernet fabric. Open ecosystem vs. NVIDIA's closed stack.

### 5.5 Etched Sohu — Transformer-Specific ASIC

```
┌─────────────────────────────────────┐
│           Etched Sohu (2024)        │
│                                     │
│  Approach: Hardcode the entire      │
│  transformer architecture into      │
│  silicon. No programmability for    │
│  other architectures.              │
│                                     │
│  - Attention, GEMM, LayerNorm,     │
│    Softmax, RoPE all in hardware   │
│  - 144 GB HBM3e                    │
│  - Claim: 500K tok/s (Llama 70B)   │
│  - TSMC 4nm                        │
│  - 8× H100 inference throughput    │
│    (claimed)                        │
│                                     │
│  Risk: If transformer architecture  │
│  changes significantly, chip is     │
│  obsolete. Betting on transformers  │
│  being durable.                     │
└─────────────────────────────────────┘
```

### 5.6 Other Notable Accelerators

| Chip | Company | Key Differentiator |
|------|---------|-------------------|
| **Trainium2** | AWS | NeuronCores with 32-wide SIMD, custom interconnect (Trn2 UltraServer: 64 chips) |
| **MTIA v2** | Meta | Internal inference chip, INT8-optimized for recommendation |
| **Gaudi 3** | Intel/Habana | 64 TPC (Tensor Proc. Cores) + 8 MME (Matrix Math Engines), RDMA mesh |
| **SN40L** | SambaNova | Reconfigurable dataflow, 3-tier memory (SRAM/on-chip/HBM), runs 5T param models |
| **Cloud AI 100** | Qualcomm | Hexagon-derived cores, 16-core NSP, power-efficient inference |
| **Ascend 910B** | Huawei | Da Vinci architecture, 3D Cube Core (matrix engine), used in Chinese AI training |

---

## 6. Systolic Arrays and Dataflow Architectures

### 6.1 Dataflow Taxonomy

The critical question in accelerator design: **how do you move data**?

```
┌─────────────────────────────────────────────────────────┐
│                    DATAFLOW TAXONOMY                     │
│                                                         │
│  Weight Stationary (WS):                                │
│    - Weights stay in PEs, activations stream through    │
│    - Minimizes weight reads (largest tensor)            │
│    - Used in: TPU, many systolic arrays                 │
│                                                         │
│  Output Stationary (OS):                                │
│    - Outputs stay in PEs, weights+activations stream    │
│    - Minimizes partial sum reads/writes                 │
│    - Used in: ShiDianNao                                │
│                                                         │
│  Row Stationary (RS):                                   │
│    - Each PE computes one row of the output             │
│    - Maximizes data reuse at all levels                 │
│    - Used in: Eyeriss                                   │
│                                                         │
│  No Local Reuse (NLR):                                  │
│    - All data from global buffer every time             │
│    - Simplest but most bandwidth-hungry                 │
│    - Used in: some simple designs                       │
│                                                         │
│  Key metric: Energy per MAC operation                   │
│    MAC itself:        ~1 pJ (FP16)                     │
│    RF access:         ~1 pJ                            │
│    Scratchpad access: ~5 pJ                            │
│    DRAM access:       ~200 pJ                          │
│                                                         │
│  → Moving data costs 200× more energy than computing!   │
│  → Dataflow choice is THE critical design decision      │
└─────────────────────────────────────────────────────────┘
```

### 6.2 Eyeriss: Row Stationary Dataflow

Eyeriss (MIT, Chen et al., ISCA 2016) introduced the row stationary dataflow:

```
                  Global Buffer (108 KB SRAM)
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │ PE Array  │ │ PE Array  │ │ PE Array  │   12×14 = 168 PEs
     │ (14×12)  │ │          │ │          │
     │          │ │          │ │          │
     │ Each PE: │ │          │ │          │
     │ - RF     │ │          │ │          │
     │   (512B) │ │          │ │          │
     │ - MAC    │ │          │ │          │
     │ - Ctrl   │ │          │ │          │
     └──────────┘ └──────────┘ └──────────┘

Row Stationary mapping:
- 1D convolution row (filter row × ifmap row) maps to one PE
- PE does all multiplies+accumulates for that row pair
- Adjacent PEs pass partial sums horizontally
- Diagonal PEs share ifmap data
→ Maximizes reuse of all data types (weights, ifmaps, psums)
```

**Eyeriss v2** extended this with a hierarchical mesh NoC for flexible data delivery to support varied layer shapes.

### 6.3 NVDLA (Open Source Reference)

NVIDIA Deep Learning Accelerator — open-source Verilog for an inference accelerator:

```
┌────────────────────────────────────────────┐
│                  NVDLA                      │
│                                            │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │Convolution│  │ SDP      │  │ PDP     │  │
│  │ Engine    │  │(Single   │  │(Pooling │  │
│  │(MAC array)│  │ Data     │  │ Engine) │  │
│  │           │  │ Proc:    │  │         │  │
│  │16×16 MACs │  │ BN,ReLU, │  │ Max/Avg │  │
│  │           │  │ EltWise) │  │         │  │
│  └──────────┘  └──────────┘  └─────────┘  │
│                                            │
│  ┌──────────┐  ┌──────────┐  ┌─────────┐  │
│  │ CDP      │  │ RUBIK    │  │ Bridge  │  │
│  │(Channel  │  │(Data     │  │ DMA     │  │
│  │ Data     │  │ Reshape  │  │         │  │
│  │ Proc:    │  │ Engine)  │  │ AXI     │  │
│  │ LUT-     │  │          │  │ Master/ │  │
│  │ based)   │  │          │  │ Slave   │  │
│  └──────────┘  └──────────┘  └─────────┘  │
└────────────────────────────────────────────┘
```

Good starting point for learning accelerator RTL design. Available on GitHub.

### 6.4 Design Space Exploration: Timeloop + Accelergy

**Timeloop** (NVIDIA/MIT) models the performance of different dataflow mappings:
- Input: Architecture description + workload (layer shapes) + mapping constraints
- Output: Latency, energy, utilization for every possible mapping
- Explores the vast space of loop orderings, tiling, and spatial mapping

**Accelergy** estimates area and energy for each hardware component:
- Parameterized models for SRAMs, MACs, NoC routers, etc.
- Feed into Timeloop for end-to-end analysis

**Typical exploration workflow**:
1. Define your architecture (array size, memory hierarchy, NoC)
2. Define target workload (ResNet, GPT, etc.)
3. Run Timeloop to find optimal mapping
4. Compare energy/area/performance across design points
5. Iterate on architecture

### 6.5 CGRA (Coarse-Grained Reconfigurable Array)

Between ASIC (fixed) and FPGA (fine-grained):

```
┌──────┬──────┬──────┬──────┐
│ PE   │ PE   │ PE   │ PE   │  Each PE: ALU + small RF + routing
├──────┼──────┼──────┼──────┤
│ PE   │ PE   │ PE   │ PE   │  Reconfigurable interconnect
├──────┼──────┼──────┼──────┤  between PEs
│ PE   │ PE   │ PE   │ PE   │
├──────┼──────┼──────┼──────┤  Config loaded at compile time
│ PE   │ PE   │ PE   │ PE   │  (not per-cycle like FPGA)
└──────┴──────┴──────┴──────┘

Examples: SambaNova RDU, Intel Plasticine (Stanford), ADRES
```

---

## 7. Numerical Formats for AI Hardware

### 7.1 Format Comparison

```
FP32 (IEEE 754):   [1 sign][8 exp][23 mantissa]  = 32 bits
                    Range: ±3.4×10³⁸, Precision: ~7 decimal digits

TF32 (NVIDIA):     [1 sign][8 exp][10 mantissa]  = 19 bits
                    Same range as FP32, precision of FP16
                    Used in Tensor Cores: FP32 input → TF32 multiply → FP32 accumulate

BF16 (Google):     [1 sign][8 exp][7 mantissa]   = 16 bits
                    Same range as FP32 (8-bit exponent)
                    Precision: ~2 decimal digits
                    Training-friendly: range matters more than precision

FP16 (IEEE 754):   [1 sign][5 exp][10 mantissa]  = 16 bits
                    Range: ±65504, Precision: ~3 decimal digits
                    Limited range causes overflow in training

FP8 E4M3:          [1 sign][4 exp][3 mantissa]   = 8 bits
                    Range: ±448, used for forward pass (more precision)

FP8 E5M2:          [1 sign][5 exp][2 mantissa]   = 8 bits
                    Range: ±57344, used for backward pass (more range)

FP4 E2M1:          [1 sign][2 exp][1 mantissa]   = 4 bits
                    Range: ±6, Blackwell tensor cores
```

### 7.2 Hardware Implications

Multiplier area/energy scales roughly quadratically with mantissa bits:

| Format | Multiplier Size (relative) | Accumulator | Typical Usage |
|--------|---------------------------|-------------|---------------|
| FP32 | 1.0× (baseline) | FP32 | CPU training |
| BF16 | ~0.12× | FP32 | TPU/GPU training |
| FP16 | ~0.16× | FP32 | GPU training (Volta+) |
| TF32 | ~0.15× | FP32 | Ampere+ training |
| FP8 | ~0.03× | FP32 or FP16 | Hopper+ training/inference |
| INT8 | ~0.04× | INT32 | Inference quantized |
| INT4 | ~0.01× | INT32 | Inference aggressive quant |

**Why this matters**: For a 128×128 MXU:
- FP32: 128×128 = 16,384 FP32 multipliers → huge area + power
- BF16: Same 16K multipliers but each ~8× smaller → fits in same area with 8× more MACs
- FP8: Another ~4× reduction → enables 4× larger arrays or 4× less power

### 7.3 Microscaling (MX) Formats

OCP (Open Compute Project) standard for block-scaled formats:

```
Traditional per-tensor quantization:
  tensor_quant = round(tensor / scale)     # one scale for entire tensor
  Problem: outliers force large scale → wastes precision for normal values

Block floating point / Microscaling:
  Split tensor into blocks of K elements (K = 32 typical)
  Each block shares ONE exponent (scale factor)
  Each element stores only mantissa + sign

  ┌─────────────────────────────────────────┐
  │ Shared exponent (8-bit)                 │
  │ ┌─────┬─────┬─────┬─────┬─────┬─────┐  │
  │ │ e0  │ e1  │ e2  │ ... │ e31 │     │  │  Elements: sign + mantissa only
  │ └─────┴─────┴─────┴─────┴─────┴─────┘  │
  └─────────────────────────────────────────┘

MX4:  8-bit shared exp + 32 × 4-bit elements  = effective 4.25 bits/element
MX6:  8-bit shared exp + 32 × 6-bit elements  = effective 6.25 bits/element
MX9:  8-bit shared exp + 32 × 9-bit elements  = effective 9.25 bits/element
```

**Hardware for MX**: The shared exponent is applied as a shift to the accumulator, not per-element multiply. This means the multiplier only needs to handle the mantissa bits — significant area/power savings.

### 7.4 Mixed-Precision Training

Standard recipe (Micikevicius et al., ICLR 2018):

```
┌─────────────────────────────────────────────┐
│           Mixed-Precision Training           │
│                                             │
│  Master weights: FP32 (stored in memory)    │
│         │                                   │
│         ▼ cast to FP16/BF16                │
│  Forward pass: FP16/BF16 compute            │
│         │                                   │
│         ▼                                   │
│  Loss computation: FP32                      │
│         │                                   │
│         ▼ × loss_scale (e.g., 1024)         │
│  Backward pass: FP16/BF16 compute           │
│         │                                   │
│         ▼ ÷ loss_scale                      │
│  Gradient update: FP32 (master weights)      │
│                                             │
│  Loss scaling prevents gradient underflow    │
│  in FP16 (values too small to represent)     │
└─────────────────────────────────────────────┘
```

**Hopper Transformer Engine**: Automates FP8 training:
- Per-tensor dynamic scaling based on statistics from previous iteration
- Delayed scaling: use max absolute value from step N to set scale for step N+1
- Hardware tracks statistics (max values) automatically
- Format selection: E4M3 for forward, E5M2 for backward

### 7.5 Stochastic Rounding

Instead of round-to-nearest-even:
```
Round-to-nearest:  3.7 → 4, 3.2 → 3 (deterministic)

Stochastic:        3.7 → 4 with 70% probability, 3 with 30% probability
                   3.2 → 4 with 20% probability, 3 with 80% probability

Implementation: add a random number ∈ [0, 1) × ulp before truncating
  val_rounded = floor(val + random() × ulp)

Why it helps: In expectation, E[round(x)] = x
  Regular rounding has systematic bias that accumulates
  Stochastic rounding is unbiased → helps convergence in low precision
```

Hardware cost: Needs a PRNG (Pseudo-Random Number Generator) per PE — LFSR is cheapest (~20 gates).

---

## 8. LLM-Specific Hardware Design

### 8.1 The LLM Inference Problem

```
Transformer inference has TWO distinct phases:

PREFILL (processing the prompt):
  - All tokens processed in parallel (like training)
  - Compute-bound: large matrix multiplications
  - Arithmetic intensity: ~O(d_model) FLOPS per byte
  - GPU utilization: HIGH (80-95%)

DECODE (generating tokens one at a time):
  - Each token depends on ALL previous tokens (autoregressive)
  - Batch size = 1 per sequence for the "new" token
  - Memory-bound: read entire model weights for 1 token
  - Arithmetic intensity: ~O(1) — just 2 FLOPS per weight byte read
  - GPU utilization: LOW (1-5% compute, bottlenecked on memory BW)

The decode phase is THE bottleneck for LLM inference.
```

### 8.2 Arithmetic Intensity Analysis

For a transformer layer with dimension d=4096 (e.g., Llama 7B):

```
Key operation: Y = X × W where W is (4096 × 4096)

Prefill (batch B tokens):
  FLOPS = 2 × B × 4096 × 4096 = 33.5M × B FLOPS
  Bytes = 4096 × 4096 × 2 (BF16 weights) + B × 4096 × 2 (input) ≈ 33.5MB (weights dominate)
  Arithmetic Intensity = (33.5M × B) / 33.5M = B FLOPS/byte
  For B=2048: AI = 2048 → compute-bound ✓

Decode (B=1):
  FLOPS = 2 × 1 × 4096 × 4096 = 33.5M FLOPS
  Bytes = 33.5 MB (same weights read)
  Arithmetic Intensity = 33.5M / 33.5M = 1 FLOP/byte
  → Memory-bandwidth-bound ✗

H100 has:
  - 990 TFLOPS BF16 Tensor Core
  - 3.35 TB/s HBM3 bandwidth
  - Compute/BW ratio = 990T / 3.35T = 295 FLOPS/byte

  → Need AI > 295 to be compute-bound
  → Decode at AI=1 uses only 1/295 = 0.34% of compute!
```

### 8.3 KV Cache Management

Each generated token needs attention over ALL previous tokens. The Key-Value cache stores precomputed K and V matrices:

```
KV Cache size per token per layer:
  2 (K + V) × d_model × 2 bytes (BF16) = 2 × 4096 × 2 = 16 KB

For Llama 70B (80 layers), 4K context:
  80 layers × 4096 tokens × 8192 dim × 2 (K+V) × 2 bytes = 10.7 GB per sequence

For batch=64:
  64 × 10.7 GB = 686 GB — exceeds H100's 80 GB HBM!
```

**Hardware solutions**:
- **Paged attention**: KV cache stored in non-contiguous pages (vLLM approach), needs hardware page table support
- **Grouped Query Attention (GQA)**: Reduce KV cache by sharing across attention heads (8× reduction in Llama 2)
- **Multi-Query Attention (MQA)**: Single K,V shared across all heads
- **Dedicated KV cache SRAM**: On-chip SRAM for "hot" KV entries, spill to HBM
- **Compression**: Quantize KV cache to INT4/INT8 (lose some quality)

### 8.4 Attention Hardware

FlashAttention (Dao et al.) is currently a software algorithm, but hardware implications:

```
Standard Attention:
  S = Q × K^T          (N×N matrix — O(N²) memory)
  P = softmax(S)
  O = P × V

  Problem: N×N attention matrix doesn't fit in SRAM for large N

FlashAttention (tiled):
  For each tile of Q (block size B_r):
    For each tile of K, V (block size B_c):
      Compute local S_ij = Q_i × K_j^T
      Compute local softmax (with running max/sum correction)
      Update output O_i
    End
  End

  Never materializes the full N×N matrix — stays in SRAM

Hardware FlashAttention accelerator would need:
  1. Tile-sized SRAM buffers for Q, K, V tiles
  2. Matrix multiply unit (same MXU as GEMM)
  3. Softmax unit: exp, running max, running sum, divide
  4. Online softmax accumulator (log-sum-exp correction)
  5. Output accumulator with rescaling
```

**Dedicated Softmax hardware**: The softmax operation (exp of each element, sum, divide) is expensive. Hardware approaches:
- Look-up table (LUT) for exp function
- Piecewise linear approximation
- Log-domain computation (avoid exp/divide entirely)
- Streaming implementation with running max correction

### 8.5 Hardware for Speculative Decoding

Speculative decoding uses a small "draft" model to predict several tokens, then the large model verifies them in one batch:

```
Traditional: Large model generates 1 token at a time
  [Large] → tok1 → [Large] → tok2 → [Large] → tok3  (3 serial steps)

Speculative: Draft model generates guesses, large model verifies in batch
  [Draft] → tok1,tok2,tok3 (fast, 3 steps)
  [Large] → verify all 3 at once (1 step, batch=3)
  If all correct: 3 tokens for cost of ~1 large inference

Hardware support:
  - Dual compute paths: small model path + large model path
  - Dedicated draft model engines (could be much smaller MXU)
  - Fast verification logic (compare token probabilities)
  - Speculative buffer for draft tokens
```

### 8.6 MoE (Mixture of Experts) Hardware Challenges

MoE models (e.g., Mixtral 8×7B, GPT-4 rumored MoE) route each token to a subset of expert FFN blocks:

```
┌─────────────┐
│   Router    │  ← Learned gating network
│  (softmax)  │     selects top-K experts per token
└──────┬──────┘
  ┌────┴────┐
  ▼         ▼
┌────┐   ┌────┐   ┌────┐   ┌────┐
│Exp0│   │Exp1│   │Exp2│   │Exp7│   ← 8 expert FFN blocks
└────┘   └────┘   └────┘   └────┘     (only 2 active per token)

Hardware challenges:
1. LOAD IMBALANCE: Some experts get many tokens, others few
   → Underutilizes compute if mapped statically
   → Need dynamic load-balanced dispatch

2. ALL-TO-ALL COMMUNICATION: Tokens must be routed to correct expert
   → In multi-chip: each chip holds some experts
   → Expert parallelism needs all-to-all shuffle
   → Interconnect bandwidth is bottleneck

3. MEMORY: All 8 experts' weights in memory even if only 2 active
   → More total parameters but same compute as dense
   → Memory-capacity-bound, not compute-bound

Hardware solutions:
  - Expert buffer: Prefetch next layer's expert weights
  - Dynamic routing NoC: On-chip network for token-to-expert dispatch
  - Capacitated routing: Hardware enforces max tokens per expert
  - Expert caching: Keep hot experts in fast memory, cold in HBM
```

---

## 9. On-Chip Interconnect and NoC Design

### 9.1 Topology Options

```
Mesh (most common for AI accelerators):          Ring:
  ┌──┐   ┌──┐   ┌──┐   ┌──┐                    ┌──┐ → ┌──┐
  │PE│───│PE│───│PE│───│PE│                    │PE│   │PE│
  └──┘   └──┘   └──┘   └──┘                    ↑  └──┘   └──┘ ↓
    │      │      │      │                     ┌──┐         ┌──┐
  ┌──┐   ┌──┐   ┌──┐   ┌──┐                  │PE│         │PE│
  │PE│───│PE│───│PE│───│PE│                    └──┘ ← ┌──┘ ← └──┘
  └──┘   └──┘   └──┘   └──┘

Pros: Regular, scalable           Pros: Simple, low area
Cons: Diameter = 2(√N-1)         Cons: Latency = O(N)

Torus (mesh with wraparound):     Crossbar:
  ┌──┐───┌──┐───┌──┐─┐            ┌──┐
  │PE│   │PE│   │PE│ │          ──│PE│──┬──┬──┬──
  └──┘   └──┘   └──┘ │            └──┘  │  │  │
    │      │      │   │            ┌──┐  │  │  │
  ┌──┐   ┌──┐   ┌──┐ │          ──│PE│──┼──┼──┼──
  │PE│   │PE│   │PE│ │            └──┘  │  │  │
  └──┘   └──┘   └──┘ │
  └──────────────────┘           Pros: O(1) latency
                                 Cons: O(N²) area — doesn't scale
Pros: Halves diameter
Cons: Long wires at edges
```

### 9.2 Router Microarchitecture

```
┌──────────────────────────────────────────────┐
│              NoC Router (5-port mesh)          │
│                                              │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐     │
│  │Input │  │Input │  │Input │  │Input │     │
│  │Buffer│  │Buffer│  │Buffer│  │Buffer│ ... │
│  │ (N)  │  │ (S)  │  │ (E)  │  │ (W)  │     │
│  └──┬───┘  └──┬───┘  └──┬───┘  └──┬───┘     │
│     │         │         │         │          │
│  ┌──▼─────────▼─────────▼─────────▼──┐       │
│  │         Route Computation          │       │
│  │    (XY routing: compare coords)    │       │
│  └──────────────┬─────────────────────┘       │
│                 │                             │
│  ┌──────────────▼─────────────────────┐       │
│  │        VC Allocation                │       │
│  │   (assign virtual channel)          │       │
│  └──────────────┬─────────────────────┘       │
│                 │                             │
│  ┌──────────────▼─────────────────────┐       │
│  │      Switch Allocation              │       │
│  │  (arbitrate for crossbar port)      │       │
│  └──────────────┬─────────────────────┘       │
│                 │                             │
│  ┌──────────────▼─────────────────────┐       │
│  │         5×5 Crossbar                │       │
│  │    (flit traversal)                 │       │
│  └────────────────────────────────────┘       │
│                                              │
│  Pipeline: RC → VA → SA → ST (4 cycles/hop)  │
│  Can reduce to 1-2 cycles with speculation    │
└──────────────────────────────────────────────┘
```

**Flow control**:
- **Wormhole**: Packet divided into flits. Header flit reserves path, data flits follow, tail releases. Low buffer requirements.
- **Virtual Channels (VCs)**: Multiple logical channels share one physical link. Prevents deadlock and improves throughput.
- **Credit-based**: Downstream sends credit when buffer freed. Upstream only sends if credits available. No buffer overflow.

### 9.3 NoC for Systolic Arrays

The NoC feeding a systolic array has specific requirements:
- **Streaming pattern**: Weights flow one direction, activations another
- **Bandwidth matching**: Must sustain one element per PE per cycle
- **Multicast**: Same weight/activation may go to multiple PEs

```
Data delivery for 128×128 systolic array:

Option A: Direct wiring (no NoC)
  - 128 input ports for activations (left edge)
  - 128 input ports for weights (top edge)
  - 128 output ports for results (bottom edge)
  - Simple but inflexible, works for fixed dataflow

Option B: Shared bus with arbitration
  - Lower area but becomes bottleneck at scale

Option C: Hierarchical mesh
  - Global NoC moves tiles from memory to local buffers
  - Local distribution network feeds PEs
  - Most practical for large arrays
```

### 9.4 AXI Protocol (For Accelerator Interfaces)

Most accelerators use AXI (Advanced eXtensible Interface) for memory access:

```
AXI4 channels:
  Write: AW (address) + W (data) + B (response)
  Read:  AR (address) + R (data)

Key features for accelerators:
  - Burst transfers: Up to 256 beats per transaction
  - Outstanding transactions: Multiple in-flight (pipelining)
  - Data width: 32 to 1024 bits
  - QoS: Priority levels for latency-sensitive vs. bulk

AXI-Stream (for streaming data between accelerator blocks):
  - No address channel — just data flowing
  - TVALID/TREADY handshake
  - TLAST marks end of packet
  - Simplest interface for PE-to-PE data flow
```

---

## 10. Chip-to-Chip Interconnects and Packaging

### 10.1 NVLink Evolution

| Gen | Year | BW per link | Links per GPU | Total BW | Signaling |
|-----|------|-------------|--------------|----------|-----------|
| NVLink 1.0 | 2016 | 40 GB/s | 4 | 160 GB/s | NRZ |
| NVLink 2.0 | 2017 | 50 GB/s | 6 | 300 GB/s | NRZ |
| NVLink 3.0 | 2020 | 50 GB/s | 12 | 600 GB/s | NRZ |
| NVLink 4.0 | 2022 | 50 GB/s | 18 | 900 GB/s | PAM4 |
| NVLink 5.0 | 2024 | 100 GB/s | 18 | 1800 GB/s | PAM4 |

**NVSwitch**: Crossbar switch chip connecting 8 GPUs with full bisection bandwidth. Each NVSwitch has 64 NVLink ports. DGX H100 uses 4 NVSwitch chips to connect 8 H100s.

**NVLink C2C (Chip-to-Chip)**: Die-to-die link in Blackwell — connects the two GPU dies in GB200 at 10 TB/s with 5× energy efficiency of NVLink 4.0.

### 10.2 Advanced Packaging

```
2.5D Integration (CoWoS - Chip on Wafer on Substrate):
  ┌─────────┐  ┌─────────┐  ┌─────────┐
  │  GPU    │  │   HBM   │  │   HBM   │
  │  Die    │  │  Stack  │  │  Stack  │
  └────┬────┘  └────┬────┘  └────┬────┘
  ─────┴──────────────┴──────────┴───────  ← Silicon interposer
  ─────────────────────────────────────── ← Organic substrate

  H100: GPU die + 6× HBM3 stacks on CoWoS interposer
  Interposer: passive silicon with wiring layers
  Micro-bump pitch: ~40 μm

3D Integration (Foveros):
  ┌─────────────────┐
  │  Compute die    │  ← Active logic (top)
  ├─────────────────┤
  │  Base die       │  ← I/O, memory ctrl (bottom)
  └─────────────────┘
  Through-Silicon Vias (TSVs) connect dies
  Bump pitch: ~36 μm (Foveros) → ~9 μm (Foveros Direct)

EMIB (Embedded Multi-die Interconnect Bridge):
  Instead of full interposer, embed small silicon bridges
  in the package substrate only where dies meet
  Used in Intel Ponte Vecchio

Chiplet Architecture (MI300X):
  ┌────┐ ┌────┐ ┌────┐ ┌────┐
  │XCD0│ │XCD1│ │XCD2│ │XCD3│  ← 8 compute chiplets (5nm)
  ├────┤ ├────┤ ├────┤ ├────┤
  │XCD4│ │XCD5│ │XCD6│ │XCD7│
  └──┬─┘ └──┬─┘ └──┬─┘ └──┬─┘
  ───┴──────┴──────┴──────┴────  ← Active interposer
  ┌────┐ ┌────┐ ┌────┐ ┌────┐
  │IOD0│ │IOD1│ │IOD2│ │IOD3│  ← 4 I/O dies (6nm)
  └────┘ └────┘ └────┘ └────┘    with HBM PHY + IF links
```

### 10.3 UCIe (Universal Chiplet Interconnect Express)

Open standard for die-to-die interconnect:

```
UCIe Specification:
  Standard package:  4 GB/s per lane, 16 lanes per module
  Advanced package:  16-32 GB/s per lane (shorter reach)

  Protocol layers:
    - Raw (PHY): electrical signaling
    - Streaming: no protocol overhead
    - CXL: memory coherency across chiplets
    - PCIe: standard I/O

  Enables mixing chiplets from different vendors/processes
  E.g., 3nm compute chiplet + 5nm I/O chiplet + 7nm memory controller
```

### 10.4 Optical Interconnects

The future for chip-to-chip at scale:

```
Co-Packaged Optics (CPO):
  - Optical transceiver integrated into the package (not a pluggable module)
  - Eliminates SerDes power for electrical-to-optical conversion
  - Bandwidth: 12.8-51.2 Tbps per package

Silicon Photonics:
  - Optical components (waveguides, modulators, detectors) built on silicon
  - Compatible with CMOS fabrication
  - Intel, GlobalFoundries, TSMC all have platforms

TPU v4 uses Optical Circuit Switches (OCS):
  - MEMS-based mirror array reconfigures optical paths
  - Can rewire the 3D torus topology in milliseconds
  - Enables flexible job placement across 4096 chips
```

---

## 11. VLSI Design Flow: RTL to GDSII

### 11.1 Complete Flow Overview

```
┌─────────────────────┐
│  1. Specification    │  ← Architecture definition, ISA, interfaces
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  2. RTL Design       │  ← SystemVerilog / Chisel / SpinalHDL
│     (Behavioral)     │     Write hardware description
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  3. Functional       │  ← UVM testbenches, formal verification
│     Verification     │     constrained random, coverage-driven
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  4. Logic Synthesis  │  ← Synopsys Design Compiler / Cadence Genus
│     RTL → Gates      │     Map to standard cells, optimize
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  5. Floor Planning   │  ← Macro placement, power grid, I/O rings
│                      │     Define die size and aspect ratio
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  6. Place & Route    │  ← Cadence Innovus / Synopsys ICC2
│     (Physical)       │     Cell placement, clock tree, routing
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  7. Sign-off         │  ← STA, power analysis, IR drop,
│     Verification     │     signal integrity, DRC, LVS
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  8. GDSII Tapeout    │  ← Send to foundry (TSMC, Samsung, Intel)
│                      │     Mask generation, fabrication (~3 months)
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  9. Packaging &      │  ← Wire bonding or flip-chip
│     Testing          │     Wafer test, package test, burn-in
└─────────────────────┘
```

### 11.2 RTL Design Languages

| Language | Description | Used By |
|----------|-------------|---------|
| **SystemVerilog** | Industry standard, most EDA tool support | Everyone |
| **Chisel** | Scala-embedded HDL, parameterized generators | RISC-V (Rocket, BOOM), academic |
| **SpinalHDL** | Scala-embedded, better type safety than Chisel | VexRiscv, NaxRiscv |
| **Amaranth** | Python-embedded HDL (formerly nMigen) | Open-source community |
| **CIRCT** | MLIR-based hardware compiler infrastructure | LLVM/Google |
| **Bluespec** | Rule-based HDL, high-level scheduling | Academic, some industry |

For an accelerator, SystemVerilog is the safest bet for EDA tool compatibility. Chisel is excellent for parameterized designs (e.g., configurable array sizes).

### 11.3 Logic Synthesis Deep Dive

Synthesis transforms RTL into a gate-level netlist optimized for timing, area, and power:

```
Input:  always_ff @(posedge clk)
          result <= a * b + c;      // behavioral multiply-accumulate

Synthesis steps:
  1. Elaboration: Build syntax tree, resolve parameters
  2. GTECH mapping: Convert to generic gates (AND, OR, MUX, FLOP)
  3. Technology mapping: Map to foundry standard cells
     - Multiplier → chosen architecture (Booth, Wallace tree, etc.)
     - Adder → chosen architecture (Kogge-Stone, Brent-Kung, etc.)
  4. Optimization passes:
     - Timing: Fix setup violations (resize cells, add buffers)
     - Area: Share logic, remove redundancy
     - Power: Clock gating insertion, operand isolation

Output: Netlist of standard cells with timing constraints met

Key constraints file (SDC):
  create_clock -period 1.0 [get_ports clk]   # 1 GHz target
  set_input_delay 0.2 -clock clk [all_inputs]
  set_output_delay 0.2 -clock clk [all_outputs]
  set_max_area 0                               # minimize area
```

### 11.4 Physical Design

**Floor planning**:
```
┌───────────────────────────────────────────┐
│  I/O Ring (pad cells)                      │
│  ┌─────────────────────────────────────┐   │
│  │ ┌─────┐ ┌──────────┐ ┌──────────┐  │   │
│  │ │SRAM │ │          │ │  SRAM    │  │   │
│  │ │Macro│ │  Std Cell│ │  Macro   │  │   │
│  │ │     │ │  Region  │ │          │  │   │
│  │ └─────┘ │ (Place & │ └──────────┘  │   │
│  │         │  Route)  │              │   │
│  │ ┌─────┐ │          │ ┌──────────┐  │   │
│  │ │PLL  │ │          │ │  HBM PHY │  │   │
│  │ │     │ │          │ │          │  │   │
│  │ └─────┘ └──────────┘ └──────────┘  │   │
│  │                                     │   │
│  │  Power Grid (VDD/VSS mesh)          │   │
│  └─────────────────────────────────────┘   │
└───────────────────────────────────────────┘
```

**Clock Tree Synthesis (CTS)**:
The clock must reach every flip-flop at the same time (low skew):
- Build balanced H-tree or mesh clock distribution
- Insert buffers to drive large loads
- Target skew: < 50 ps for 1 GHz
- Target jitter: < 20 ps

**Timing closure**: The hardest part of physical design — iterating on placement, sizing, and routing until all timing paths meet setup and hold constraints across all PVT (Process, Voltage, Temperature) corners.

---

## 12. Process Technology and Transistor Design

### 12.1 FinFET and Beyond

```
Planar MOSFET (>22nm):          FinFET (22nm-3nm):
         Gate                          Gate wraps 3 sides
          │                            of vertical fin
  ┌───────┴───────┐             ┌──────┴──────┐
  │               │             │    ┌───┐    │
──┤   Channel     ├──           │    │Fin│    │
  │               │             │    │   │    │
  └───────────────┘             └────┴───┴────┘
Source         Drain            Better electrostatic control
                                → less leakage, better scaling

GAA/Nanosheet (2nm and below):
         Gate wraps ALL 4 sides
         of stacked nanosheets
      ┌──────────────┐
      │  ┌────────┐  │
      │  │ Sheet  │  │
      │  ├────────┤  │
      │  │ Sheet  │  │
      │  ├────────┤  │
      │  │ Sheet  │  │
      │  └────────┘  │
      └──────────────┘
  Even better control, more drive current
  per footprint (stack more sheets)
```

### 12.2 Process Node Comparison

| Node | Foundry | Transistor | Density | AI Chips Using It |
|------|---------|-----------|---------|-------------------|
| **7nm** | TSMC N7 | FinFET | ~91 MTr/mm² | A100, MI250X |
| **5nm** | TSMC N5 | FinFET | ~173 MTr/mm² | M2, Dimensity 9000 |
| **4nm** | TSMC N4 | FinFET | ~166 MTr/mm² | H100, Blackwell, A17 Pro |
| **3nm** | TSMC N3E | FinFET | ~208 MTr/mm² | M3 Pro/Max/Ultra, A18 |
| **3nm** | Samsung 3GAE | **GAA** | ~170 MTr/mm² | (Exynos 2400) |
| **2nm** | TSMC N2 | **GAA** | ~300 MTr/mm² | Expected 2025+ |
| **20A** | Intel | **RibbonFET** (GAA) | ~200 MTr/mm² | Expected 2025 |
| **18A** | Intel | GAA + **BSPDN** | ~250 MTr/mm² | External foundry available |

**Back-Side Power Delivery Network (BSPDN)**: Intel 18A and TSMC N2P deliver power from the back of the wafer instead of through front-side metal layers. Frees up routing tracks → better signal routing → higher performance and density.

### 12.3 Cost of Fabrication

Rough estimates (2024):

| Node | Mask Cost | Design Cost | Per-Wafer Cost | Die Cost (100mm²) |
|------|-----------|-------------|----------------|-------------------|
| 28nm | ~$2M | ~$30M | ~$3K | ~$30 |
| 7nm | ~$15M | ~$150M | ~$10K | ~$100 |
| 5nm | ~$25M | ~$300M | ~$17K | ~$170 |
| 3nm | ~$50M | ~$500M+ | ~$20K+ | ~$200+ |

For a startup: 28nm or 12nm is realistic. Sub-7nm requires $500M+ investment or significant funding.

---

## 13. Memory Design for Accelerators

### 13.1 SRAM Design

```
Standard 6T SRAM Cell:
         WL (Word Line)
          │         │
    BL ──┤M1   M2├── BL_bar
          │    ×    │
         ┌┤M3  M4├┐
   VDD ──┤        ├── VDD
         └┤M5  M6├┘
          │       │
   VSS ───┘       └─── VSS

  Read: Assert WL, sense voltage difference on BL/BL_bar
  Write: Drive BL/BL_bar to desired value, assert WL

  6T cell size (TSMC 7nm): ~0.027 μm²
  Typical read latency: 0.5-1 ns (small SRAM), 1-3 ns (large SRAM)
```

**SRAM for accelerators**:
- Register file: Multi-port SRAM (2R1W to 8R4W). Expensive in area — each port adds transistors.
- Scratchpad/shared memory: Single-port or dual-port, banked for parallelism.
- Typical sizes: 32 KB - 4 MB per compute unit
- Compiler-generated SRAMs: Foundries provide memory compilers that generate SRAM blocks of any aspect ratio/size

**SRAM vs Register File area**:
```
1 KB register file (8R4W, 32-bit words): ~0.05 mm² at 7nm
1 KB SRAM (single-port):                ~0.002 mm² at 7nm
→ Register files are ~25× larger per bit than SRAM!
→ This is why GPU register files dominate die area
```

### 13.2 Scratchpad vs Cache

```
Cache (hardware-managed):
  + Transparent to software
  + Handles irregular access patterns
  - Tag storage overhead (~5-10% area)
  - Conflict misses waste bandwidth
  - Non-deterministic latency
  - Cache coherence complexity

Scratchpad (software-managed):
  + No tag overhead → more usable storage
  + Deterministic latency (always hits)
  + Software controls exactly what's stored
  + No coherence needed (private per core)
  - Programmer/compiler must manage data movement
  - DMA engines needed for async data transfers

For AI accelerators: Scratchpad wins decisively
  - Access patterns are regular and predictable
  - Compiler can perfectly schedule data movement
  - No wasted area on tags/coherence
  - Used in: TPU, Cerebras, Groq, most custom ASICs
```

### 13.3 HBM PHY Design

The HBM physical interface (PHY) is a critical design challenge:

```
HBM3 PHY requirements:
  - 1024 data pins per stack (128 per channel × 8 channels)
  - Pin speed: 6.4 Gbps
  - Very short reach (mm-scale on interposer)
  - Low power per bit (< 3 pJ/bit)
  - Per-pin training/calibration

  PHY components:
  ┌─────────────────────────────────────┐
  │  TX driver (push-pull or CML)       │
  │  RX receiver (sense amplifier)      │
  │  DLL/PLL for clock generation       │
  │  Per-lane deskew and calibration    │
  │  Command/address interface          │
  │  DRAM initialization state machine  │
  │  BIST (Built-In Self-Test)          │
  └─────────────────────────────────────┘

  You can license HBM PHY IP from:
  - Synopsys (DesignWare HBM PHY)
  - Cadence (Denali HBM Controller + PHY)
  - Rambus
  Cost: $5-20M for license + royalties
```

---

## 14. Power Delivery and Thermal Design

### 14.1 Power Breakdown of a Typical AI Chip

```
H100 power breakdown (estimated ~700W TDP):

  Tensor Cores + FP units:   ~35%  (245W)  ← Switching power in MACs
  Register files:            ~15%  (105W)  ← High port count, large
  SRAM (shared mem + L2):    ~12%  (84W)   ← Leakage-dominated at 4nm
  Memory controllers + PHY:  ~15%  (105W)  ← SerDes, HBM PHY
  NoC / Interconnect:        ~8%   (56W)   ← Crossbar, routers
  Clock distribution:        ~8%   (56W)   ← Clock tree buffers
  I/O (PCIe, NVLink):        ~5%   (35W)   ← SerDes transceivers
  Other (control, misc):     ~2%   (14W)

Power equation:
  P_dynamic = α × C × V² × f
    α = activity factor (0.1-0.3 typical)
    C = total capacitance
    V = supply voltage (~0.75V at 4nm)
    f = clock frequency (~1.5-2 GHz)

  P_leakage = I_leak × V
    Grows exponentially with temperature and smaller nodes
    At 3nm: leakage can be 30-50% of total power!
```

### 14.2 Power Management Techniques

```
Clock Gating:
  - Gate the clock to inactive units
  - Most effective technique (saves α × C × V² × f)
  - Synthesis tools auto-insert clock gating for enable-guarded registers
  - Coarse-grained: gate entire blocks (e.g., unused Tensor Cores)
  - Fine-grained: gate individual register banks

Power Gating:
  - Cut VDD to sleeping blocks using header/footer switches
  - Eliminates leakage (important at advanced nodes)
  - Requires retention registers for state preservation
  - Wake-up latency: ~100-1000 ns (must charge up local decaps)

DVFS (Dynamic Voltage-Frequency Scaling):
  - Reduce voltage + frequency together for power savings
  - P ∝ V²f → halving V+f gives 8× power reduction
  - Requires on-chip voltage regulators (LDO or buck converter)
  - Typical GPU: 0.6V-1.1V range, 500 MHz-2 GHz

  For AI accelerators:
  - Prefill phase: max voltage, max frequency (compute-bound)
  - Decode phase: reduce voltage, reduce frequency (memory-bound anyway)
  - Save 30-50% power during decode with minimal performance impact
```

### 14.3 Cooling

```
                   AI Chip Cooling Solutions

Air Cooling:        Liquid Cooling:       Immersion:
  ┌─────────┐         ┌─────────┐          ┌─────────┐
  │ Heatsink│         │Cold Plate│          │         │
  │ + Fans  │         │ (water)  │          │ Dielectr│
  │         │         │         │          │ Fluid    │
  └────┬────┘         └────┬────┘          │ (entire │
       │                   │               │ server  │
  Max ~400W            Max ~1000W          │ submerge│
  Typical data         Used in H100        │ d)      │
  center GPU           DGX systems         └─────────┘
                                           Max ~1500W+
                                           Most efficient
                                           TPU v3+ uses direct liquid
```

---

## 15. Verification, Testing, and Tapeout

### 15.1 Verification Landscape

Verification typically consumes **60-70% of total design effort**:

```
                    Verification Methods

Coverage:           Cost:           Completeness:
  ▲                   ▲                   ▲
  │ Formal            │                   │ Formal
  │ Verification      │                   │
  │                   │ Emulation         │
  │ Emulation         │ ($10M+)          │ Emulation
  │                   │                   │
  │ Simulation        │ Formal           │ Simulation
  │ (UVM)             │                   │
  │                   │ FPGA Proto       │ FPGA Proto
  │ FPGA              │                   │
  │ Prototyping       │ Simulation       │
  └──────────────     └─────────────     └──────────────
```

**UVM (Universal Verification Methodology)**:
```
┌──────────────────────────────────────┐
│          UVM Testbench               │
│                                      │
│  ┌─────────┐  ┌───────────────────┐  │
│  │Sequencer│→ │    Driver          │  │
│  │(stimulus│  │ (drives DUT pins)  │──┼──→ DUT
│  │ gen)    │  └───────────────────┘  │
│  └─────────┘                         │
│               ┌───────────────────┐  │
│               │   Monitor         │←─┼──  DUT outputs
│               │ (samples DUT)     │  │
│               └────────┬──────────┘  │
│                        │             │
│               ┌────────▼──────────┐  │
│               │   Scoreboard      │  │
│               │ (check expected   │  │
│               │  vs actual)       │  │
│               └───────────────────┘  │
│                                      │
│  ┌─────────────────────────────────┐ │
│  │ Coverage Collector              │ │
│  │ (functional + code coverage)    │ │
│  └─────────────────────────────────┘ │
└──────────────────────────────────────┘
```

**Formal verification**: Mathematically prove properties hold for ALL possible inputs (not just tested ones). Essential for:
- Protocol compliance (AXI, cache coherence)
- Deadlock freedom in NoC
- Arithmetic correctness (multiplier, FPU)

### 15.2 DFT (Design for Testability)

```
Scan Chain (for post-silicon manufacturing test):

  Normal mode:  D → FF → Q        (functional operation)
  Scan mode:    SI → FF → SO       (shift test patterns through chain)

  ┌──────┐    ┌──────┐    ┌──────┐    ┌──────┐
  │ FF 1 │───→│ FF 2 │───→│ FF 3 │───→│ FF N │───→ Scan Out
  └──────┘    └──────┘    └──────┘    └──────┘
  Scan In ───→

  Process:
  1. Shift test pattern in (scan mode)
  2. Apply one clock in normal mode (capture)
  3. Shift results out (scan mode)
  4. Compare with expected results (detect manufacturing defects)

Memory BIST (Built-In Self-Test):
  - On-chip state machine that tests every SRAM bit
  - March test patterns: March C- (march through 0s and 1s)
  - Detects stuck-at faults, address decoder faults, coupling faults
  - Repair: redundant rows/columns activated for faulty cells
```

### 15.3 FPGA Prototyping

Before tapeout, verify the full design on FPGAs:

```
FPGA Prototyping Platforms:

  Synopsys HAPS-100: Up to 4× VU19P FPGAs (~160M ASIC gates)
  Cadence Protium X2: Similar scale, automated partitioning
  Custom: Multiple Xilinx Alveo U280 or Intel Stratix 10

  Typical speed: 10-50 MHz (vs 1+ GHz in silicon)
  Good for: Software development, system validation, boot tests

  For an AI accelerator:
  - Implement a single PE or small 4×4 array on one FPGA
  - Validate dataflow, memory interface, control logic
  - Run actual inference workloads at reduced speed
  - Catch bugs that simulation would take years to find
```

---

## 16. Open-Source Chip Design

### 16.1 Open-Source RTL-to-GDSII Flow

```
┌─────────────────────────────────────────────────┐
│              OpenLane 2 / OpenROAD Flow          │
│                                                 │
│  RTL ──→ Yosys (synthesis)                      │
│      ──→ OpenSTA (timing analysis)              │
│      ──→ OpenROAD:                              │
│           - RePlAce (global placement)          │
│           - TritonCTS (clock tree synthesis)     │
│           - FastRoute + TritonRoute (routing)   │
│           - OpenRCX (parasitic extraction)       │
│      ──→ Magic (DRC, LVS)                       │
│      ──→ KLayout (GDS viewing, DRC)             │
│      ──→ GDSII output                           │
│                                                 │
│  Supported PDKs:                                │
│  - SkyWater 130nm (SKY130) — Google-sponsored   │
│  - GlobalFoundries 180nm (GF180MCU)             │
│  - IHP 130nm (SG13G2) — with SiGe BiCMOS       │
│  - ASAP7 (academic predictive 7nm)              │
└─────────────────────────────────────────────────┘
```

### 16.2 Open-Source Accelerator Projects

| Project | Description | Language |
|---------|-------------|----------|
| **NVDLA** | NVIDIA inference accelerator | SystemVerilog |
| **Gemmini** | Berkeley systolic array generator (RISC-V) | Chisel |
| **VTA** | Apache TVM versatile tensor accelerator | Chisel/Verilog |
| **Ara** | ETH Zürich RISC-V vector processor | SystemVerilog |
| **OpenPiton** | Princeton many-core research framework | Verilog |
| **PULP** | ETH Zürich parallel ultra-low-power cluster | SystemVerilog |
| **CFU Playground** | Google ML accelerator + RISC-V on FPGA | Verilog/Amaranth |
| **HammerBlade** | UW/Bespoke Silicon Group manycore | SystemVerilog |

**Gemmini** is particularly relevant — it's a parameterized systolic array generator that plugs into the Rocket RISC-V core via RoCC (Rocket Custom Coprocessor) interface. You can configure array size, dataflow, memory hierarchy, and generate RTL.

### 16.3 Free Tape-Out Programs

- **Efabless / Google chipIgnite**: Free tape-out on SkyWater 130nm. Submit open-source design → get fabricated chips. Multiple shuttle runs per year.
- **Tiny Tapeout**: Educational program, very small designs on shared die.
- **MUSE program** (IEEE SSCS): Subsidized tapeout for students.

---

## 17. Practical Path to Building Your Own Chip

### 17.1 Recommended Learning Path

```
Phase 1: FUNDAMENTALS (3-6 months)
  ├── Learn SystemVerilog (or Chisel)
  ├── Implement basic components: ALU, register file, SRAM controller
  ├── Build a simple RISC-V CPU (from scratch or modify VexRiscv)
  ├── Learn UVM basics for verification
  └── Tool: Verilator (free simulator), GTKWave (waveform viewer)

Phase 2: ACCELERATOR DESIGN (3-6 months)
  ├── Design a small systolic array (8×8, INT8)
  ├── Add scratchpad memory and DMA engine
  ├── Implement weight stationary dataflow
  ├── Connect to RISC-V host via AXI/RoCC
  ├── Run a small neural network (MNIST MLP)
  └── Tool: Gemmini (study and modify), Timeloop (explore design space)

Phase 3: FPGA PROTOTYPING (3-6 months)
  ├── Synthesize your design for Xilinx FPGA (Vivado)
  ├── Target a board: Alveo U250/U280 or Zynq UltraScale+
  ├── Validate with real workloads at reduced clock
  ├── Profile: utilization, bandwidth, throughput
  ├── Iterate on design (this is where most bugs are found)
  └── Tool: Vivado, Vitis HLS for comparison

Phase 4: ASIC TAPEOUT (6-12 months)
  ├── Port design to ASIC flow (OpenLane or commercial)
  ├── Target SkyWater 130nm (free) or GF 12nm (funded)
  ├── Physical design: floor plan, power grid, timing closure
  ├── Verification: DRC, LVS, STA sign-off
  ├── Submit to Efabless shuttle (or commercial foundry)
  └── Tool: OpenLane, OpenROAD, Magic, KLayout

Phase 5: SCALING (ongoing)
  ├── Move to advanced node (7nm+) — requires $50M+ or VC funding
  ├── Add HBM integration (license PHY IP)
  ├── Multi-die / chiplet architecture
  ├── Build compiler stack (MLIR/TVM/XLA-based)
  └── This is where it becomes a company, not a project
```

### 17.2 Cost Estimates

```
Hobby/Academic (SkyWater 130nm):
  Tools: Free (open-source)
  Fabrication: Free (Efabless shuttle)
  FPGA board: $200-$3000
  Total: $500-$5000

Startup MVP (GF 22nm or TSMC 28nm):
  EDA tools: $500K-$2M/year (Synopsys/Cadence/Siemens)
  Design team: 5-10 engineers × $200K = $1-2M/year
  IP licenses: $1-3M (PHY, memory compiler, bus fabric)
  Fabrication: $2-5M (mask set + wafers)
  Total: $5-15M for first chip

Competitive AI Chip (TSMC 5nm/4nm):
  EDA tools: $5-10M/year
  Design team: 50-200 engineers
  IP licenses: $10-30M
  Fabrication: $25-50M (mask set alone)
  Packaging (CoWoS + HBM): $20-50M setup
  Total: $100-500M for first chip
  (This is why Cerebras, Groq, Tenstorrent raised $100M+)
```

### 17.3 Critical Decisions for Your Chip

```
1. WHAT WORKLOAD?
   - Training only → need HBM, high compute, all-reduce support
   - Inference only → can use SRAM-heavy design, lower power
   - Transformer-specific → can hardcode attention/softmax
   - General ML → need programmability (GPU-like or CGRA)

2. WHAT PRECISION?
   - FP8/BF16 minimum for training
   - INT8/INT4 sufficient for inference
   - Consider MX formats for best efficiency

3. WHAT SCALE?
   - Single chip inference → simpler, focus on memory BW
   - Multi-chip training → need high-BW interconnect (expensive!)

4. PROGRAMMABILITY vs EFFICIENCY?
   - Fully programmable (GPU-like): Max flexibility, lower efficiency
   - Configurable (CGRA): Good balance
   - Fixed-function (TPU-like): Highest efficiency, limited scope
   - Hardcoded (Etched-like): Max efficiency, one architecture only

5. MEMORY ARCHITECTURE?
   - HBM: High bandwidth, expensive, needs interposer
   - LPDDR5: Cheaper, lower bandwidth, simpler packaging
   - SRAM-only (Cerebras/Groq): Massive on-chip, limited capacity
   - Hybrid: SRAM scratchpad + HBM backing store (most common)
```

---

## 18. Performance Analysis: Roofline Model

### 18.1 Roofline for AI Accelerators

```
Performance           Compute Ceiling
(FLOPS)              ─────────────────────────────────
   │                /
   │               / ← Memory BW ceiling (slope = BW)
   │              /
   │             /
   │            /
   │           /
   │          /  Ridge point = Peak FLOPS / Peak BW
   │         /   (operational intensity where compute = memory)
   │        /
   │       /
   │      /
   │     /
   │    /
   │   /
   └──/──────────────────────────────────
      Arithmetic Intensity (FLOPS/Byte)

H100 Roofline:
  Peak BF16 Tensor: 990 TFLOPS
  Peak HBM BW: 3.35 TB/s
  Ridge point: 990T / 3.35T = 295 FLOPS/byte

  Operation         | AI (FLOPS/byte) | Bound
  ─────────────────────────────────────────────
  GEMM (large batch)| 1000+           | Compute
  GEMM (batch=1)    | 1-2             | Memory
  Attention (long)  | 100+            | Compute
  Softmax           | ~5              | Memory
  LayerNorm         | ~10             | Memory
  Activation (ReLU) | ~0.25           | Memory
  Embedding lookup  | ~0.5            | Memory
```

### 18.2 Designing for the Right Ridge Point

If your chip targets LLM inference (decode phase), the workload has AI ≈ 1-2 FLOPS/byte. A chip with a ridge point of 295 (like H100) wastes 99% of compute during decode.

**Better design for inference**:
```
Option A: More bandwidth, less compute
  - SRAM-heavy design (like Groq): 230 MB SRAM at 80 TB/s
  - Ridge point: 750 TOPS / 80 TB/s = ~9 FLOPS/byte
  - Much better utilization at AI=1-2

Option B: Larger batch sizes (increase AI)
  - Continuous batching: batch many sequences together
  - If batch=128: AI = 128 → still memory-bound on H100
  - If batch=256: AI = 256 → close to ridge point

Option C: Quantize (increase effective bandwidth)
  - INT4 model: 4× less bytes to read per parameter
  - Effective AI increases 4×: from 1 to 4 FLOPS/byte
  - Need INT4 compute units (cheap in area)
```

### 18.3 Sizing Your Chip

Example: Design a chip for Llama 70B inference at 100 tokens/s per user

```
Model: 70B params × 2 bytes (BF16) = 140 GB
Decode: Must read all 140 GB per token
Target: 100 tokens/s × 140 GB/token = 14 TB/s memory bandwidth needed

Option 1: HBM approach
  HBM3e at 1.2 TB/s per stack → need 12 stacks → 3-4 chips
  Cost: expensive, high power

Option 2: SRAM approach (Groq-like)
  44 GB SRAM at 21 PB/s → bandwidth not an issue
  But 44 GB < 140 GB → need weight streaming from external memory
  With 1.2 TB/s external I/O: 140 GB / 1.2 TB/s = 117 ms per token = ~8.5 tok/s
  Need 12 WSE chips to hit 100 tok/s (layer-parallel)

Option 3: INT4 quantization
  35 GB model, need 3.5 TB/s
  3 stacks HBM3e = 3.6 TB/s → fits on ONE chip!
  Minimal quality loss with good quantization (GPTQ, AWQ)

→ Quantization is the single most impactful hardware/software co-design choice
```

---

## 19. Key Papers and References

### GPU Architecture
1. Lindholm et al., "NVIDIA Tesla: A Unified Graphics and Computing Architecture," IEEE Micro, 2008
2. Jia et al., "Dissecting the NVIDIA Volta GPU Architecture via Microbenchmarking," arXiv, 2018
3. Jia et al., "Dissecting the NVidia Turing T4 GPU via Microbenchmarking," arXiv, 2019
4. Luo & Wen, "GCoM: A Detailed GPU Core Model for Accurate Analytical Modeling of Modern GPUs," ISPASS, 2019
5. Rogers et al., "Cache-Conscious Wavefront Scheduling," MICRO, 2012 (CCWS)
6. Narasiman et al., "Improving GPU Performance via Large Warps and Two-Level Warp Scheduling," MICRO, 2011

### TPU and Accelerators
7. Jouppi et al., "In-Datacenter Performance Analysis of a Tensor Processing Unit," ISCA, 2017 (TPU v1)
8. Jouppi et al., "A Domain-Specific Supercomputer for Training Deep Neural Networks," Comm. ACM, 2020 (TPU v2/v3)
9. Jouppi et al., "TPU v4: An Optically Reconfigurable Supercomputer for Machine Learning," ISCA, 2023
10. Lie, "Cerebras Architecture Deep Dive: First Look Inside the WSE," Hot Chips, 2019
11. Abts et al., "Think Fast: A Tensor Streaming Processor for Accelerating Deep Learning Workloads," ISCA, 2020 (Groq TSP)
12. Knowles, "GraphCore," Hot Chips, 2017 (IPU)

### Dataflow and Systolic Arrays
13. Kung, "Why Systolic Architectures?," IEEE Computer, 1982 (foundational systolic array paper)
14. Chen et al., "Eyeriss: An Energy-Efficient Reconfigurable Accelerator for Deep CNNs," JSSC, 2017
15. Chen et al., "Eyeriss v2: A Flexible Accelerator for Emerging Deep Neural Networks," JETCAS, 2019
16. Parashar et al., "Timeloop: A Systematic Approach to DNN Accelerator Evaluation," ISPASS, 2019
17. Kwon et al., "MAERI: Enabling Flexible Dataflow Mapping over DNN Accelerators via Reconfigurable Interconnects," ASPLOS, 2018
18. Sze et al., "Efficient Processing of Deep Neural Networks: A Tutorial and Survey," Proceedings of the IEEE, 2017

### Numerical Formats
19. Micikevicius et al., "Mixed Precision Training," ICLR, 2018
20. Darvish Rouhani et al., "Microscaling Data Formats for Deep Learning," arXiv, 2023 (OCP MX spec)
21. Noune et al., "8-bit Numerical Formats for Deep Neural Networks," NeurIPS, 2022
22. Gustafson & Yonemoto, "Beating Floating Point at its Own Game: Posit Arithmetic," Supercomputing Frontiers, 2017

### LLM Hardware
23. Dao et al., "FlashAttention: Fast and Memory-Efficient Exact Attention," NeurIPS, 2022
24. Dao, "FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning," ICLR, 2024
25. Leviathan et al., "Fast Inference from Transformers via Speculative Decoding," ICML, 2023
26. Shazeer, "Fast Transformer Decoding: One Write-Head is All You Need," arXiv, 2019 (Multi-Query Attention)
27. Pope et al., "Efficiently Scaling Transformer Inference," MLSys, 2023 (Google inference optimization)
28. Kwon et al., "Efficient Memory Management for Large Language Model Serving with PagedAttention," SOSP, 2023 (vLLM)

### Chip Design and VLSI
29. Weste & Harris, "CMOS VLSI Design: A Circuits and Systems Perspective" (textbook, definitive reference)
30. Rabaey et al., "Digital Integrated Circuits: A Design Perspective" (textbook)
31. Hennessy & Patterson, "Computer Architecture: A Quantitative Approach" (6th ed.)
32. Ajayi et al., "OpenROAD: Toward a Self-Driving, Open-Source Digital Layout Implementation Tool Chain," GOMAC, 2019
33. Genc et al., "Gemmini: Enabling Systematic Deep-Learning Architecture Evaluation via Full-Stack Integration," DAC, 2021

### Interconnect and Packaging
34. Dally & Towles, "Principles and Practices of Interconnection Networks" (textbook)
35. Norrie et al., "The Design Process for Google's Training Chips: TPUv2 and TPUv3," IEEE Micro, 2021 (ICI details)
36. Naffziger et al., "AMD Chiplet Architecture for High-Performance Server and Desktop Products," ISSCC, 2020

### Industry Deep Dives
37. NVIDIA CUDA Programming Guide (comprehensive GPU architecture reference)
38. Hot Chips conference proceedings (annual, best source for new chip architectures)
39. ISSCC (International Solid-State Circuits Conference) proceedings
40. IEEE Micro "Top Picks from Architecture Conferences" (annual survey of best papers)

---

*Last note: The single most important insight for building a competitive AI chip is this — the bottleneck is NOT compute, it's data movement. Every design decision should minimize bytes moved per useful FLOP. This is why dataflow architecture, memory hierarchy design, and numerical format choice matter more than raw FLOPS count.*

---

## See Also

- [Cycle Counters and Energy](@/notes/hardware/cycle_counters_and_energy.md) — Per-cycle energy analysis for CPU vs GPU, Tensor Core efficiency, and FLOPS/watt comparison
- [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) — SIMD/vector instructions (AVX-512, SVE, RVV) and numerical formats that feed accelerator pipelines
- [VFIO Internals](@/notes/os/vfio_internals.md) — Device passthrough for GPU/accelerator access from VMs and userspace (DPDK, SPDK, QEMU)
- [Data Structures](@/notes/programming/data_structures.md) — SIMD-vectorized structures and cache-aware layouts relevant to accelerator programming
