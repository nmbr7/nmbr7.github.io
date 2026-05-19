+++
title = "Superscalar OOO CPU"
date = 2026-05-20
[taxonomies]
tags = ["cpu", "microarchitecture", "superscalar", "ooo", "branch-prediction", "pipeline"]
+++


# Superscalar Out-of-Order CPU Microarchitecture

Expert reference for designing and building production-grade superscalar OoO processors. Covers pipeline internals, branch prediction, memory subsystem, modern microarch case studies (Apple M1–M5, AMD Zen 4/5, Intel Golden Cove/Lion Cove, Qualcomm Oryon, ARM Cortex-X925, IBM POWER10, RISC-V), workloads by deployment domain (cloud, serverless, HPC, supercomputers), and a practical design guide.

---

## Table of Contents

1. [Pipeline Overview](#1-pipeline-overview)
2. [Frontend: Fetch, Branch Prediction, Decode](#2-frontend)
3. [Rename, Allocation, ROB](#3-rename-allocation-rob)
4. [Issue Queue / Reservation Stations](#4-issue-queue-reservation-stations)
5. [Execution Units](#5-execution-units)
6. [Memory Subsystem](#6-memory-subsystem)
7. [Speculative Execution & Security](#7-speculative-execution-security)
8. [Production Case Studies 2020–2025](#8-production-case-studies-2020-2025)
9. [Workloads by Deployment Domain](#9-workloads-by-deployment-domain)
10. [NoC and Cache Coherence](#10-noc-and-cache-coherence)
11. [Power and Frequency](#11-power-and-frequency)
12. [Building a Production-Grade Superscalar](#12-building-a-production-grade-superscalar)
13. [Key References](#13-key-references)

---

## 1. Pipeline Overview

A modern superscalar OoO processor executes instructions out of program order to hide latency and exploit instruction-level parallelism (ILP). The pipeline has three logical phases: **in-order frontend** (fetch, decode, rename), **out-of-order engine** (issue, execute), and **in-order backend** (commit/retire).

```
In-Order Frontend
┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐   ┌────────┐
│  Fetch   │──▶│ Predecode │──▶│  Decode  │──▶│  Rename  │──▶│ Alloc  │
│ (BPred + │   │  (µop     │   │ (µop     │   │  (RAT +  │   │ (ROB + │
│  I-cache)│   │  split)   │   │  crack)  │   │  PRF)    │   │  IQ)   │
└──────────┘   └───────────┘   └──────────┘   └──────────┘   └────────┘
                                                                    │
                                        Out-of-Order Engine         │
                              ┌─────────────────────────────────────┘
                              ▼
                    ┌──────────────────┐
                    │   Issue Queue    │──▶ Wakeup/Select
                    │ (Reservation     │
                    │  Stations)       │
                    └──────────────────┘
                              │
              ┌───────────────┼───────────────────┐
              ▼               ▼                   ▼
         ┌─────────┐   ┌───────────┐      ┌───────────┐
         │  INT    │   │   FP/     │      │  Load /   │
         │  ALU    │   │   SIMD    │      │  Store    │
         │  Units  │   │  Units    │      │  Units    │
         └─────────┘   └───────────┘      └───────────┘
              │               │                   │
              └───────────────┴───────────────────┘
                              │ Writeback
                              ▼
                    ┌──────────────────┐
                    │   ROB Retire     │ (in-order commit)
                    └──────────────────┘
```

### Pipeline Width Comparison

| Microarch | Fetch | Decode | Rename | Issue | Retire | ROB | PRF-INT | PRF-FP |
|-----------|------:|-------:|-------:|------:|-------:|----:|--------:|-------:|
| Apple M1 Firestorm | 8 | 8 | 8 | 8 | 8 | ~350 | ~360 | ~384 |
| Apple M2 Avalanche | 9 | 9 | 9 | 9 | 8 | ~370 | ~390 | ~400 |
| Apple M3 Everest | 9 | 9 | 9 | 9 | 8 | ~380 | ~410 | ~420 |
| Apple M4 (2024) | 10 | 10 | 10 | 10 | 10 | ~400+ | ~450 | ~450 |
| AMD Zen 4 | 8 | 4+4 | 6 | 6 | 6 | 256 | 192 | 192 |
| AMD Zen 5 | 8 | 8 | 8 | 8 | 8 | 320 | 224 | 320 |
| Intel Golden Cove | 6 | 6 | 6 | 6 | 6 | 512 | 280 | 332 |
| Intel Lion Cove (2024) | 8 | 8 | 8 | 8 | 8 | 576 | ~320 | ~384 |
| ARM Cortex-X4 | 8 | 8 | 8 | 8 | 8 | 320 | 224 | 256 |
| ARM Cortex-X925 (2024) | 10 | 10 | 10 | 10 | 10 | ~350 | ~256 | ~288 |
| IBM POWER10 | 8 | 8 | 8 | 8 | 8 | 256 | 256 | 256 |
| Qualcomm Oryon (2024) | 10 | 10 | 10 | 10 | 10 | ~350 | ~300 | ~300 |

ROB size is the primary driver of memory-level parallelism (MLP): to tolerate 200-cycle DRAM latency, the ROB must be large enough to hold 200 cycles × retire-width in-flight instructions. Intel's 576-entry ROB (Lion Cove) is the current x86 maximum.

---

## 2. Frontend

### 2.1 Branch Prediction

Branch misprediction costs 15–25 cycles in modern pipelines. The frontend spends a disproportionate fraction of silicon on prediction.

**TAGE (Tagged Geometric History Branch Predictor)**  
Seznec & Michaud, JILP 2006. Uses N components (typically 4–8), each indexed by XOR of PC and a geometric-length slice of global branch history (history lengths form a geometric series: 2, 4, 8, 16, 32, …, 256+). Each entry holds a 3-bit counter (prediction), a partial tag (collision detection), and a 2-bit usefulness counter `u`.

Prediction logic:
1. All components hit → provider = longest matching history component; altpred = second-longest.
2. No match → base bimodal predictor.
3. On mispredict: allocate new entry in a longer-history component (only if `u=0` to avoid displacing useful entries). Periodically reset all `u` bits (prevents staleness).

TAGE dominates on correlated branches (loops, switch dispatch). Achieves <3% MPKI on SPEC CPU 2006.

**TAGE-SC-L** (Seznec, MICRO 2011):  
Adds Statistical Corrector (SC) to catch predictor bias that TAGE misses (e.g., branches that are globally 80% taken but locally periodic), and a Loop Predictor for counted loops. Loop predictor stores iteration count; after a sufficient training period, overrides TAGE with 100% confidence.

**ITTAGE** (Indirect Target TAGE, Seznec 2014):  
Same tagged geometric structure, but each entry stores a predicted target address rather than a taken/not-taken counter. Handles virtual dispatch, computed jumps, switch tables. Needs more bits per entry; smaller capacity than TAGE.

**BTB Hierarchy:**

| Level | Entries | Latency | Notes |
|-------|--------:|--------:|-------|
| L0/µBTB | 64–256 | 1 cycle | Immediate loop redirect, overlaps fetch |
| L1 BTB | 1K–4K | 2–3 cycles | Most taken branches |
| L2 BTB | 6K–16K | 4–6 cycles | Call/return, indirect targets |
| RAS | 32–64 | 0 cycles | Return address stack, speculative push/pop |

Real BTB sizes:
- AMD Zen 4: 1024-entry L1, 6144-entry L2, 512-entry RAS
- AMD Zen 5: enlarged BTB, improved ITTAGE
- Intel Golden Cove: ~12K BTB, TAGE 12 components, ITTAGE 16 components
- Apple M1–M4: undisclosed but estimated 200-entry L0 BTB, deep multi-component TAGE — eliminates most branch bubbles empirically
- Qualcomm Oryon: reportedly deepest history depth in industry (2024)
- ARM Cortex-X925: enlarged BTB vs X4, loop predictor added

**Return Address Stack (RAS):**  
Speculative push on CALL, pop on RET. On mispredict recovery, restore snapshot of RAS taken at the checkpoint. 64-entry RAS covers most real programs' call depth.

**Fetch-Directed Prefetching:**  
Use branch predictor output (predicted next PC) to prefetch I-cache lines 4–8 cycles ahead. Decouples I-cache miss from fetch stall for sequential streams.

### 2.2 Instruction Cache

**VIPT (Virtually Indexed Physically Tagged):**  
Index bits derived from virtual address (fast, no TLB needed), tags from physical address (correct, no aliasing). Aliasing-free when index bits fall entirely within the page offset (bits [11:0] for 4KB pages): L1I ≤ 32KB with 64B lines uses bits [11:6] as index — safe. L1I > 32KB with direct-map uses higher index bits and can alias. Solution: restrict to set-associative with degree ≥ (cache_size / page_size).

**µop Cache (Decoded ICache):**  
Stores pre-decoded µops, bypasses fetch + full decode for hot code. Critical for x86 because decode is expensive (variable-length CISC). Apple does not publicly disclose but likely has equivalent (non-x86 ARM is cheaper to decode, so µop cache less critical but still reduces power).

| Microarch | µop cache entries | Bandwidth |
|-----------|------------------:|----------:|
| AMD Zen 4 | 4096 | 6 µops/cycle |
| AMD Zen 5 | 4096+ | 8 µops/cycle |
| Intel Golden Cove | 4096 | 6 µops/cycle |
| Intel Lion Cove | ~6000 | 8 µops/cycle |

### 2.3 Decode

**x86 Decode Complexity:**  
Variable-length instructions (1–15 bytes). Length finding is the hard part — requires scanning for prefix bytes, ModRM, SIB, displacement, immediate. Predecoder marks instruction boundaries; main decoder cracks into µops.

**µop Cracking:**  
- Simple instructions: 1 µop (ADD, MOV, LEA)
- Memory operand: 2 µops (load + ALU, or AGU + store)
- Complex (ENTER, CPUID, RDRAND): 10–100+ µops via Microcode ROM (MSROM)
- String ops (REP MOVS): sequenced from MSROM

**Macro-Fusion:**  
Adjacent instruction pairs fused into single µop at decode:
- x86: CMP/TEST + Jcc → 1 µop (eliminates a compare µop)
- ARM: ADD/SUB + B.cond → 1 µop

**Move Elimination:**  
Register-to-register moves (MOV rax, rbx) handled in rename by writing an alias in the RAT rather than dispatching an execution µop. Zero latency, no execution port consumed. ARM: most MOV instructions eliminated this way.

---

## 3. Rename, Allocation, ROB

### 3.1 Register Renaming

Goal: eliminate false dependencies (WAR, WAW hazards) while preserving RAW (true data) dependencies.

**RAT (Register Alias Table):**  
Maps each architectural register (e.g., 16 GPRs on x86-64) to a physical register number. Updated at rename. Lookup: source regs → physical source tags; destination reg → allocate new physical reg from free list.

**RRAT (Retirement RAT):**  
Committed state of RAT (updated at retire). Used for fast recovery: on mispredict, copy RRAT → RAT and restore free list by releasing all physical registers allocated since the checkpoint. Requires one RAT snapshot per speculative branch (checkpointing) for fast recovery, or ROB walk for slow recovery.

**Physical Register File (PRF):**  
Centralized storage for all in-flight values. PRF-INT and PRF-FP are separate (different access patterns, different operand widths). PRF must be large enough to hold: all architectural registers + all in-flight instructions that haven't retired + rename slots.

Typical sizing: ROB_size + arch_reg_count + rename_width × pipeline_depth ≈ PRF size.

**Free List:**  
FIFO of available physical register IDs. Allocate at rename, free at retire (after freeing the old physical reg that the instruction overwrites).

### 3.2 ROB (Reorder Buffer)

Circular buffer indexed by ROB tail (alloc) and ROB head (retire). Each entry contains:

```
struct ROBEntry {
    uint64_t  pc;           // instruction PC for precise exceptions
    uint8_t   phys_dest;    // new physical destination register
    uint8_t   old_phys;     // previous physical reg (freed at commit)
    bool      completed;    // execution finished
    bool      store;        // is this a store (needs SQ drain at commit)
    bool      load;         // is this a load (for replay tracking)
    bool      branch;       // is this a branch
    bool      exception;    // fault pending
    uint8_t   exception_code;
};
```

**Retirement:** Instructions retire in-order from ROB head. On completion (completed=true at head), free old_phys, advance head. Exceptions trigger precise replay from faulting PC.

**ROB Sizing Rationale:**  
To find MLP = N outstanding cache misses while executing at IPC=W, need ROB ≥ N × latency_per_miss_in_cycles × W / N = latency × W. For 200-cycle DRAM, 8-wide retire: ROB ≥ 200 × 8 / 10 (assuming 10% memory instructions) ≈ 160 minimum. In practice, larger is always better — 500+ ROB lets the CPU overlap many independent DRAM accesses.

### 3.3 Branch Checkpointing

At every predicted branch, snapshot the entire RAT (fast copy of ~16–32 mappings). On mispredict detected at execute: restore RAT snapshot, free all physical regs allocated after the branch, flush ROB after the branch, redirect fetch to correct PC. Latency: ~15–20 cycles mispredict penalty. Without checkpointing, recovery requires ROB walk from head to misspeculated branch — slower but less area.

---

## 4. Issue Queue / Reservation Stations

### 4.1 Unified vs. Distributed

**Unified (Tomasulo-style):**  
All µops from all execution classes wait in one pool. Every cycle, scan entire IQ for ready µops. Advantages: maximum flexibility, naturally age-ordered. Disadvantage: O(N) wakeup broadcast + O(N log N) select logic at high N; hard to pipeline at >4GHz with >64 entries.

**Distributed (clustered):**  
Separate issue queues per execution class (INT, FP, MEM). Each cluster has its own wakeup/select. Advantages: smaller per-cluster CAM, shorter critical path. Disadvantage: steering at dispatch must predict execution class; imbalanced clusters reduce utilization.

Modern practice: distributed with small clusters. Zen 4 has separate INT/FP/AGU queues; Intel Golden Cove has separate integer/FP/memory schedulers.

### 4.2 Wakeup and Select

**Wakeup:**  
When an instruction completes (or speculatively, 1 cycle before it will complete), broadcast its destination physical register tag on the wakeup bus. All entries in the IQ compare their source tags against the broadcast. Matching entries decrement their "sources remaining" count; when count = 0, mark as ready.

**Select:**  
Among all ready µops, pick the highest-priority ones (one per execution port per cycle). Priority: oldest instruction wins (age-ordered select, implemented as priority encoder with age bits). Instruction is selected, issued to register read stage, then executes.

**Critical Path:**  
Wakeup → select → register read → ALU execute must complete in one clock cycle. This is the most timing-critical path in the core and determines maximum frequency. Typical: 128–256 entry unified IQ limits clock to ~3.5–4.0 GHz at 5nm; smaller distributed 32–64 entry clusters can hit 4.5–5.5 GHz.

### 4.3 Speculative Wakeup and Replay

L1D hit latency is known (e.g., 4 cycles). On load issue, speculatively wake up all dependent instructions to arrive at execute exactly when the load data should arrive. If the load hits L1D: data arrives, dependents execute correctly. If the load misses L1D: data arrives late; kill the speculatively woken dependents, replay them when data is ready.

**Replay cost:** ~10–15 cycles for an L1 miss that triggers replay. Frequent misses → replay storm → effective IPC collapse on memory-bound workloads.

---

## 5. Execution Units

### 5.1 Execution Port Layout (Typical P-Core)

| Port | Unit | Latency | Reciprocal Throughput |
|------|------|--------:|-----------------------|
| INT ALU ×4 | ADD/SUB/AND/OR/XOR/shift/compare | 1 cycle | 4/cycle |
| INT ALU ×2 | LEA 3-component, bit manipulation (POPCNT/BMI2) | 1–3 cycles | 2/cycle |
| INT MUL ×2 | IMUL 64-bit | 3 cycles | 2/cycle |
| INT DIV ×1 | IDIV (variable, SRT algorithm) | 20–90 cycles | 1/N cycles |
| FP/SIMD ×2–4 | FMA 128/256/512-bit | 4–5 cycles | 2–4/cycle |
| AGU LD ×2 | Load address generation + L1D access | 4 cycles | 2/cycle |
| AGU ST ×1 | Store address + data write to SQ | 1+1 cycles | 1/cycle |
| Branch ×1–2 | Conditional branch resolution | 1 cycle | 1–2/cycle |

### 5.2 FP/SIMD Throughput Comparison

| Microarch | FP units | SIMD width | DP FLOP/cycle | SP FLOP/cycle |
|-----------|----------|-----------|-------------:|-------------:|
| Apple M4 (2024) | 4 FMA | 128-bit NEON | 8 | 16 |
| AMD Zen 4 | 2 FMA | 256-bit (AVX-512) | 16 | 32 |
| AMD Zen 5 | 2 FMA | 256-bit unified | 16 | 32 |
| Intel Golden Cove | 2 FMA | 512-bit (AVX-512) | 32 | 64 |
| Intel Lion Cove (2024) | 2 FMA | 512-bit (AVX-512) | 32 | 64 |
| ARM Cortex-X925 | 4 FMA | 128-bit SVE | 8 | 16 |
| Qualcomm Oryon | 4 FMA | 128-bit NEON | 8 | 16 |
| IBM POWER10 | 4 FMA + MMA | 128-bit VSX + 4×4 accum | 32+ (MMA) | 64+ |

Note: Zen 5 avoids AVX-512 frequency throttle issue (Intel CPUs drop 200–400 MHz under heavy AVX-512); Zen 5 executes 512-bit operations as two fused 256-bit ops internally, no frequency penalty.

### 5.3 Divider Design

Division uses iterative algorithms (SRT — Sweeney, Robertson, Tocher). SRT-4 produces 2 bits/cycle; a 64-bit divider takes ~30 cycles. Pipelined only partially — most cores allow one divide per cluster per latency. Avoid division in hot paths; replace with multiplication by reciprocal when divisor is loop-invariant.

---

## 6. Memory Subsystem

### 6.1 Load Queue and Store Queue

**Load Queue (LQ):**  
~100–192 entries. Tracks every in-flight load: virtual address, physical address (after TLB), data (when available), ROB index. Used for: (1) store-to-load forwarding lookup, (2) memory ordering violation detection (load-load/load-store ordering), (3) replay on cache miss.

**Store Queue (SQ):**  
~60–100 entries. Holds stores from issue until commit. At commit, stores drain to L1D (or store buffer → L1D). Fields: address, data, byte-enable mask, ROB index.

**CAM (Content-Addressable Memory):**  
LQ and SQ use CAM for address lookup (compare load address against all SQ entries simultaneously). CAM is area/power expensive — limits practical LQ/SQ sizes.

### 6.2 Store-to-Load Forwarding (STLF)

When a load issues: CAM-search SQ for any matching store (same address, sufficient data width). If match: forward data directly from SQ, 4–5 cycle latency (vs 4-cycle L1 hit — similar). If partial overlap or size mismatch: wait for store to commit to memory, then re-issue load (~20+ cycles extra).

STLF hit rate critical for code with local stack frames (push/pop patterns, local variable stores followed by loads). Compilers exploit this — stack-allocated return values forward perfectly.

### 6.3 Memory Disambiguation

**Conservative:** Stall load until all older stores have known addresses (no STLF unless address computed). Safe, loses parallelism.

**Speculative load issue:** Issue load assuming no conflict with older unknown-address stores. On load completion, re-check SQ for forwarding. On conflict: replay the load (memory order violation).

**Store Sets (Chrysos & Emer, ISCA 1998):**  
Hardware learns which (load PC, store PC) pairs historically conflict. Maintains SSIT (Store Set ID Table) and LFST (Last Fetched Store Table). Loads predicted to conflict with specific stores stall until those stores compute addresses. Eliminates most replays at cost of some unnecessary stalls.

### 6.4 Cache Hierarchy

```
                    ┌─────────────┐
         P-core     │  L1I Cache  │  64KB–192KB, VIPT, 4-cycle
                    │  L1D Cache  │  32KB–128KB, VIPT, 4-cycle
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
         Per-core   │  L2 Cache   │  512KB–16MB, PIPT, 12–15 cycle
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
         Shared     │  L3 Cache   │  8MB–128MB, PIPT, 40–60 cycle
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │    DRAM     │  DDR5/LPDDR5X/HBM3, 80–200 cycle
                    └─────────────┘
```

**Per-Microarch Numbers:**

| Microarch | L1I | L1D | L2 | L3 |
|-----------|-----|-----|----|----|
| Apple M1 P-core | 192KB | 128KB | 12MB (cluster, 4P shared) | — |
| Apple M2 P-core | 192KB | 128KB | 16MB (cluster, 4P shared) | — |
| Apple M3 P-core | 192KB | 128KB | 16MB | — |
| Apple M4 P-core | 192KB | 128KB | 16MB | — |
| AMD Zen 4 | 32KB | 32KB | 1MB | 32MB/CCD |
| AMD Zen 4 + 3D V-Cache | 32KB | 32KB | 1MB | 96–128MB stacked |
| AMD Zen 5 | 32KB | 48KB | 1MB | 32MB/CCD |
| Intel Golden Cove | 32KB | 48KB | 2MB | 3MB/core |
| Intel Lion Cove | 64KB | 48KB | 2.5MB | 3MB/core |
| ARM Cortex-X925 | 64KB | 64KB | 1.5MB | shared (varies) |
| Qualcomm Oryon | 192KB | 96KB | 12MB (cluster) | — |
| IBM POWER10 | 32KB | 32KB | 2MB | 8MB/core + 32MB eDRAM |

Apple's extremely large L2 (12–16MB shared) is a key architectural differentiator — most working sets fit, nearly eliminating L3 latency from the critical path.

**MSHR (Miss Status Holding Registers):**  
Track outstanding cache misses, coalesce multiple accesses to same line. MLP = number of simultaneously outstanding misses ≈ MSHR count. L1: 16–32 MSHRs, L2: 32–64, L3: 64–256. Critical for memory-bandwidth-bound workloads.

### 6.5 Prefetchers

| Prefetcher | Mechanism | Best For |
|------------|-----------|----------|
| Stride | Detect constant stride between successive loads from same PC | DAXPY, matrix row traversal |
| Stream | Sequential cache line prefetch ahead of access stream | Memcpy, linear scan |
| GHB (Nesbit & Smith, HPCA 2004) | Global history buffer correlates load PC histories | Indirect pointer patterns |
| SMS (Srinath et al., HPCA 2007) | Spatial footprint prediction: which lines in a region get touched | Struct-of-arrays, tiled access |
| AMPM | Aggressive/conservative dual stream + memory pressure feedback | Mixed patterns |
| Bingo | PC + address offset signature → spatial footprint | Irregularly-strided spatial |
| Pythia (Bera et al., MICRO 2021) | Reinforcement learning per-PC policy, online training | Complex/irregular patterns |

Modern CPUs tier prefetchers: simple stride prefetcher in L1, complex SMS/Bingo/RL-based in L2/LLC hardware. Apple M-series aggressive prefetcher is effective even on pointer-chasing (uses large stride at cache-line granularity with lookahead).

### 6.6 TLB Hierarchy

```
ITLB:  64–192 entries, fully associative, 1 cycle hit
DTLB:  64–96 entries, fully associative, 1 cycle hit
L2TLB: 1K–8K entries, 4–8 way set-assoc, 8–12 cycle hit
PWC:   Page Walker Cache — caches PML4/PDPT/PD intermediate table entries
       Eliminates 1–3 of 4 memory accesses during page walk on miss
```

**Huge Pages:** 2MB and 1GB pages (x86), 2MB and 1GB (ARM). Single DTLB entry covers 2MB instead of 4KB → 512× coverage improvement. Critical for large heap applications (JVM, databases). OS transparent huge page (THP) daemon or `madvise(MADV_HUGEPAGE)`.

**AMD Coalesced TLBs (Zen 4+):** Single DTLB entry can cover 2MB range when 512 consecutive 4KB pages are physically contiguous. No software change needed; hardware detects the pattern.

**TLB Shootdown:** When a page mapping changes, must invalidate TLB entries on all cores. x86: software IPI + INVLPG on each core. ARM: broadcast TLBI instruction. AMD Zen 4 enterprise: hardware page table walker with reduced shootdown cost. High cost in VM environments with frequent `mmap`/`munmap`.

### 6.7 Memory Ordering Models

| ISA | Model | Key Property |
|-----|-------|-------------|
| x86-64 | TSO (Total Store Order) | Loads can pass older stores to different addresses; all other orders preserved |
| AArch64 | Weakly Ordered (+ ARMv8.4-A FEAT_LRCPC3) | All reorders allowed; barriers (DMB/DSB) + acquire/release (LDAR/STLR/LDAPR) |
| RISC-V | RVWMO (+ Ztso optional) | Weakly ordered with explicit FENCE; annotations per instruction |
| Apple Silicon | ARM architecture, but hardware is TSO-like | Empirically observed strong ordering; no extra barriers needed for most patterns |

---

## 7. Speculative Execution & Security

### 7.1 Speculative Execution Mechanisms

- **Branch speculation:** Execute past predicted branches; rollback on mispredict.
- **Load speculation:** Issue loads before all older store addresses are known; re-check on completion.
- **Memory-level speculation:** Reorder loads past stores to different addresses (TSO permits this).
- **Value prediction:** Speculate on load values (rare in production; Gabbay & Mendelson 1997). Not deployed in current mainstream CPUs due to complexity.

### 7.2 Spectre/Meltdown Family (2018–2024)

| Variant | CVE | Mechanism | Mitigation | Typical Perf Cost |
|---------|-----|-----------|------------|:-----------------:|
| Spectre v1 | CVE-2017-5753 | Bounds-check bypass, array OOB | `array_index_mask_nospec`, `lfence` barriers | 5–15% |
| Spectre v2 | CVE-2017-5715 | Indirect branch target injection (BTB poisoning) | retpoline / eIBRS + IBPB | 5–30% |
| Meltdown | CVE-2017-5754 | Rogue data cache load (kernel VA access) | KPTI (kernel page table isolation) | 5–30% on syscall-heavy |
| Spectre v4 | CVE-2018-3639 | Speculative store bypass | SSBD (prctl or MSR) | 2–8% |
| MDS/RIDL/Fallout | CVE-2018-12130 | Microarchitectural data sampling via fill buffers | MDS_CLEAR (VERW on context switch) | 2–10% |
| L1TF | CVE-2018-3620 | L1 terminal fault via EPT/SMM | L1D flush on VM entry + VMX changes | 5–15% on VM switch |
| TAA | CVE-2019-11135 | TSX Asynchronous Abort | Disable TSX (microcode) or VERW | 0–3% |
| Downfall (GDS) | CVE-2022-40982 | Gather Data Sampling via AVX gather | GFDS microcode update | 10–50% on gather-heavy |
| Inception | CVE-2023-20569 | Phantom speculation on AMD Zen | Microcode + IBPB | varies |
| Spectre BHI/BHB | CVE-2022-0001 | Branch History Injection bypasses eIBRS | eIBRS+IBPB or SW BHI_DIS_S | 5–20% |

**Hardware mitigations:** Intel Ice Lake+ has hardware IBRS (near-zero cost), M3/M4 have hardware mitigations for most variants. Older microarchitectures (Skylake, Broadwell) carry heavier software mitigation overhead.

---

## 8. Production Case Studies 2020–2025

### 8.1 Apple Silicon: M1 → M5

**Apple M1 (Firestorm P-core, TSMC N5, 2020):**  
8-wide decode, ROB ~350, PRF-INT ~360, PRF-FP ~384. Industry-leading at launch — nearly double the instruction window of competing designs. LPDDR4X 68 GB/s. 192KB L1I, 128KB L1D, 12MB shared L2 (4P-core cluster). Eliminated x86 instruction decode complexity (ARM), redirecting transistors to execution resources.

**M2 (Avalanche, TSMC N5P, 2022):**  
9-wide decode, ROB ~370. LPDDR5 100 GB/s. 16MB shared L2. Improved branch predictor. ~18% IPC gain over M1.

**M3 (Everest, TSMC N3B, 2023):**  
9-wide, ROB ~380. First 3nm. Added hardware ray tracing and mesh shading on GPU die. Improved prefetcher. Branch predictor trained on larger history. ~20% perf/watt gain over M2.

**M4 (TSMC N3E, 2024):**  
10-wide decode, ROB ~400+. 120 GB/s LPDDR5X. 16MB shared L2. Second-gen 3nm process (N3E, higher yield than N3B). Introduced in iPad Pro (May 2024), MacBook Pro/Air/Mac mini (late 2024).  
- M4 Pro: 14-core (10P+4E), 24–120GB unified memory, 273 GB/s bandwidth, 48MB system-level cache  
- M4 Max: 16-core CPU (12P+4E), 546 GB/s bandwidth, up to 128GB  
- M4 Ultra: dual-M4-Max connected via UltraFusion (die-to-die), 1092 GB/s, 192GB

**M5 (2025, TSMC N2 or N3P, expected):**  
Not yet released as of knowledge cutoff. Expected: 10–12 wide decode, TSMC N2 (2nm-class GAA) if timeline holds, further enlarged branch predictor and ROB, LPDDR5X+ at 140–160 GB/s. Likely 15–25% IPC gain over M4. ARM v9.4A features (SVE2, TME, RME).

**Apple architectural insight:**  
Massive shared L2 + unified memory = no NUMA. Single memory controller eliminates cross-domain latency. Very large ROB exploits long DRAM latency to find MLP aggressively. Trades clock frequency (3.2–4.0 GHz) for width and instruction window depth.

### 8.2 Qualcomm Oryon (Snapdragon X Elite, 2024)

Custom ARM v8.7A implementation (not a Cortex license), designed by Nuvia (acquired by Qualcomm 2021). 10-wide decode, ROB ~350, 192KB L1I, 96KB L1D, 12MB shared L2 per 4-core cluster. 4×128b NEON FMA units. LPDDR5X at 135 GB/s. Reportedly best branch predictor history depth in shipping silicon (2024). First Windows-on-ARM CPU achieving broad x86 parity (Prism x86 emulation layer). Laptop-focused power envelope (15–25W).

### 8.3 AMD Zen 4 / Zen 5 (2022–2024)

**Zen 4 (Raphael/Genoa, TSMC N5, 2022):**  
4+4 decode (split integer+FP pipelines), 6-wide rename, 256-entry ROB, 4096 µop cache. DDR5/LPDDR5. 3D V-Cache option: 64–128MB stacked L3 using hybrid bonding (direct Cu-to-Cu). Massive gaming workload improvements (pointer-chasing from large L3). CCD (compute chiplet, 5nm) + IOD (I/O die, 6nm) chiplet design via Infinity Fabric.

**Zen 5 (Granite Ridge/Strix Point, TSMC N4P, 2024):**  
8-wide decode (eliminated the 4+4 split), 8-wide rename, 320 ROB. FP throughput doubled: 2×256b AVX-512 fused → 512b effective, no frequency penalty. Improved ITTAGE for indirect branch prediction. Better L2 prefetcher with context-aware distance. Strix Point: Zen 5 + RDNA 3.5 iGPU + XDNA 2 NPU on single die (4 TOPS → 50 TOPS NPU). Desktop variant adds 3D V-Cache option (up to 128MB L3).

### 8.4 Intel Golden Cove / Lion Cove (2021–2024)

**Golden Cove (Alder Lake/Raptor Lake, Intel 7 / TSMC N5, 2021–2022):**  
512-entry ROB (largest x86 at launch), 6-wide decode, 4096 µop cache. Full AVX-512 (2×512b FMA). 12K BTB, TAGE with 12 components. Hybrid design: Golden Cove P-cores + Gracemont E-cores (in-order, dense, efficient).

**Lion Cove (Lunar Lake, TSMC N3B, 2024):**  
576-entry ROB (new x86 record), 8-wide decode, enlarged µop cache, new FP execution tile. Eliminated HyperThreading: doubled physical per-core resources instead (full 8-wide without SMT contention). 2×512b AVX-512 FMA + AMX-FP16. L1I 64KB, L1D 48KB, L2 2.5MB. On-package LPDDR5X (8GB stacked on SoC package): 68 GB/s at extremely low latency, laptop-optimized.

**Arrow Lake (Core Ultra 200, 2024):**  
Lion Cove P-core + Skymont E-core on separate tiles. Disaggregated multi-tile design: CPU tile (TSMC N3) + GPU tile + SoC tile + IO tile (Intel 6). No HyperThreading on P-cores. Launched with firmware issues; subsequent microcode updates significantly improved performance. Strong contender post-patches for desktop workloads.

### 8.5 ARM Cortex-X925 (ARMv9.2A, 2024)

10-wide decode (vs 8-wide in X4), ROB ~350, 64KB L1I + 64KB L1D (doubled vs X4). 4× FMLA units, SVE2 capable (512-bit or variable-length), loop predictor added. Used in:
- Snapdragon 8 Gen 4 (Qualcomm, pairing with Oryon-variant small cores)
- Samsung Exynos 2500 (4nm)
- MediaTek Dimensity 9400: all-X925 big cores (4+4 X925 without traditional mid-tier), aggressive high-perf configuration targeting Apple M4 competition in mobile.

### 8.6 IBM POWER10 (Samsung 7nm, 2021)

SMT8 per physical core (8 hardware threads share execution resources), 12–15 cores per chip. Matrix Math Assist (MMA): 4×4 int8 / bfloat16 outer-product accumulators, enabling matrix multiply without GPUs. OpenCAPI / OMI (Open Memory Interface) for attached DDR5 DIMMs. 64KB L1I, 2MB L2, 8MB L3 per core, optional 32MB Centaur eDRAM L4. Enterprise RAS: chipkill ECC, redundant execution paths, hot-plug. AI inference on POWER10 competitive with GPU for batch-size-1 enterprise workloads.

### 8.7 RISC-V High-Performance (2024–2025)

**SiFive P870 (2025 samples):**  
Claimed 13-wide OoO issue, targeting TSMC 3nm. If realized, would be widest RISC-V core in production. RVV 1.0, RISC-V B/M/A extensions.

**Ventana Veyron V2:**  
16-core OoO chiplet, 12-wide issue, RVV 1.0, cloud-targeting (Marvell OCTEON 10 DPU integration). Targets Neoverse V2 class performance.

**XiangShan Kunminghu (2024):**  
Most advanced open-source OoO RISC-V. 6-wide decode, 256-entry ROB, TAGE-SC-L branch predictor, SMS prefetcher, full RVV 1.0. Perf target: ~80–85% of Apple M3 per GHz on SPEC CPU 2006. RTL open on GitHub (Chisel). Xu et al., MICRO 2022.

**Esperanto ET-SoC-1:**  
1088 RISC-V cores on single die (ML inference, not general OoO), shows RISC-V scaling to extreme core counts.

---

## 9. Workloads by Deployment Domain

### 9.1 Cloud / Server

**JIT-heavy workloads (JVM, V8, CLR):**
- iTLB pressure: JIT-compiled code footprint = 10–100s MB → frequent iTLB misses. Solution: huge pages for JIT code region (`mmap` with `MAP_HUGETLB`, jemalloc huge-page support).
- Branch predictor pollution: polymorphic virtual dispatch generates ITTAGE entries for each call-site target combination. Megamorphic call sites (>3 targets) collapse prediction → ~20-cycle mispredict every call. JIT uses inline caches to reduce megamorphism.
- µop cache misuse: large hot code → µop cache evicts → full decode on every pass. JIT compilers keep hot methods small to fit in µop cache.

**Vectorized database engines (DuckDB, ClickHouse, Velox):**
- AVX-512 critical for filter evaluation, hash aggregation, sort.
- L3 bandwidth-bound on full-table scans: need >100 GB/s effective bandwidth → prefer AMD (32MB/CCD L3) or 3D V-Cache (96MB L3).
- Prefetcher effectiveness: sequential column scan → stream prefetcher covers it. Hash probe on small hash table (fits L2/L3): no prefetch needed. Large hash table (DRAM-size): software prefetch (`_mm_prefetch`) in loop body, typically 16–32 entries ahead.
- SIMD gather/scatter: Intel Downfall GDS mitigation cost matters if AVX gather is used heavily.

**Virtualization overhead:**
- VM entry/exit: ~1000–3000 cycles per exit (VMCS load/store, TLB flush with non-global VMID)
- KPTI: adds ~2 cycles to every syscall path (page-table switch), ~50–200 cycles for syscall-intensive workloads
- Posted interrupts: APIC interrupt delivery without VM exit → amortizes interrupt cost for high-frequency I/O (NVMe, DPDK)
- Hardware-assisted IOTLB: VT-d Process Address Space ID (PASID) allows shared IOMMU context between VM and host

**NUMA effects:**
- Cross-socket memory access: 2–3× latency (100ns local DDR5 → 300ns cross-socket via Infinity Fabric or UPI)
- Thread migration: OS scheduler must keep threads on NUMA node where their memory was allocated
- Tools: `numactl --cpunodebind=0 --membind=0`, `taskset`, `libnuma`, `hwloc`

**SMT utilization:**
- SMT doubles logical cores. Throughput gain: 20–40% for throughput workloads, near 0% for latency-sensitive single-threaded.
- Shared resources under SMT: L1/L2 caches (partition by usage), branch predictor (shared, pollution between threads), issue queue (shared, reduced per-thread bandwidth).
- Intel removed HT on Lion Cove P-cores: on a single thread, full physical resources available → higher single-thread performance.

### 9.2 Containers / Serverless

**Cold start penalty:**
- Branch predictor: state from previous tenant in BTB/TAGE → indirect branch mispredictions for first ~10K instructions of new function
- L1/L2 cache: cold → 50–200 cycle miss per unique cache line in working set
- Combined: first invocation 10–100× slower than warm invocation for small serverless functions
- Mitigation: profile-guided BTB warming (prefetch predicted branches), keep functions warm via periodic pings

**Context switch cost:**
- Save/restore FP/SIMD state: 256 bytes (AVX) or 1024 bytes (AVX-512 + AMX) per context
- Linux lazy FPU restore: skip restore until FPU first used by new process (saves 50–200ns when FPU not used)
- TLB flush on address space switch: full flush or ASID/PCID tagging (x86 PCID, ARM ASID) to avoid full TLB flush

**Container isolation overhead:**
- cgroup v2 CPU accounting: ~1–5% overhead from scheduler hooks
- seccomp BPF filter: ~1–3% on syscall-heavy workloads (JIT the filter via eBPF helps)
- Network namespace: adds one veth pair hop, ~1–5µs RTT overhead within node

### 9.3 Desktop / Gaming

**Single-thread IPC dominance:**
- Game engines: extremely branch-heavy, irregular control flow (AI, physics, scene graph traversal), pointer-chasing (entity component system)
- Branch predictor quality > raw throughput for interactive latency
- Apple M4 and Qualcomm Oryon competitive with Intel/AMD for game-style workloads despite lower frequency

**Prefetcher effectiveness:**
- Pointer chasing (linked list, tree traversal): defeats stride and stream prefetchers. Software prefetch (`__builtin_prefetch`) on known traversal patterns.
- SMS prefetcher helps for struct-heavy access patterns where spatial locality exists within cache regions
- Large L3 (AMD 3D V-Cache) = fewer DRAM accesses → more effective than raw prefetch for gaming

**iGPU bandwidth contention:**
- Apple M4: 120 GB/s LPDDR5X shared between P-core cluster, E-cores, and GPU. GPU uses 30–60 GB/s during 3D workload → CPU gets 60–90 GB/s effective, rarely a bottleneck
- AMD Strix Point: 51.2 GB/s DDR5 total, RDNA 3.5 iGPU uses significant fraction → CPU memory throughput constrained under GPU load

### 9.4 HPC

**Memory bandwidth (STREAM benchmark):**

| Platform | Memory Type | STREAM Triad (GB/s) |
|----------|-------------|--------------------:|
| AMD EPYC Genoa (Zen 4, 12-channel DDR5) | DDR5-4800 | ~460 |
| Intel Sapphire Rapids (8-channel DDR5) | DDR5-4800 | ~300 |
| Apple M4 Max | LPDDR5X | ~350 (2×channels) |
| NVIDIA Grace CPU (72 Neoverse V2 + HBM3) | HBM3 | ~500 |
| AMD MI300A (CPU+GPU, HBM3) | HBM3 | ~3200 |
| Fugaku (A64FX) | HBM2 | ~1024/socket |

**Roofline model:**  
Most HPC kernels are bandwidth-bound, not compute-bound. DGEMM: compute-bound (high arithmetic intensity). STREAM: bandwidth-bound (AI = 1 FLOP/8 bytes). Sparse matrix-vector (SpMV): bandwidth-bound, low AI.

**AVX-512 frequency throttling (Intel):**  
Heavy AVX-512 → frequency drops 200–400 MHz. Critical for mixed workloads: ensure AVX-512 heavy kernels run on dedicated cores or accept frequency impact. Zen 5 avoids this (256-bit internal FMA, no throttle).

**Cache blocking:**  
Tile matrix operations so A, B, C submatrices fit in L2/L3. DGEMM: block size ~256×256 for 16MB L2. FFT: radix-split to fit working set in L2. Polyhedral transformation (Pluto, LLVM Polly) automates tiling for affine loop nests.

**NUMA for HPC:**  
hwloc topology detection → bind threads to cores on same NUMA node as memory allocation → eliminate cross-socket traffic. `likwid-pin` for explicit affinity. `OMP_PROC_BIND=close` + `OMP_PLACES=cores`.

### 9.5 Supercomputers (Top500, 2024)

**Frontier → El Capitan (HPE Cray, AMD):**
- Frontier (#1 HPL 2022–2023): AMD MI250X GPU + EPYC Trento CPU, Slingshot-11 network. 1.1 ExaFLOP/s HPL.
- El Capitan (#1 HPL 2024): AMD MI300A APU — Zen 4 CPU + CDNA3 GPU + 128GB HBM3 on single package. No PCIe CPU-GPU bottleneck. ~2 ExaFLOP/s HPL. CPU memory coherent with GPU — eliminates `cudaMemcpy` overhead.

**Fugaku (ARM A64FX, Fujitsu/RIKEN, 2020–):**
- 48 cores A64FX per node, HBM2 directly attached to CPU die (not DDR), ~1 TB/s bisection bandwidth per node
- Tofu-D interconnect: 6D torus with dynamic routing, 23 Gbps per link
- SVE 512-bit (variable-length SIMD), 4× 512b FMA units per core
- Designed for simulation workloads: weather modeling, molecular dynamics, material science

**Aurora (Argonne, Intel Ponte Vecchio + Xeon SPR):**
- Intel Xe-HPC (Ponte Vecchio): tiled GPU with compute, cache, HBM, and Rambo cache tiles bonded via EMIB
- Sapphire Rapids CPUs as host; Intel Fabric (derived from Omni-Path) for network
- Challenged by software stack maturity at launch

**Network Fabrics:**

| Fabric | Bandwidth | Topology | Latency |
|--------|----------:|----------|--------:|
| Slingshot-11 (HPE) | 200 Gb/s | Dragonfly+ | ~0.5µs MPI |
| Infiniband NDR | 400 Gb/s | Fat-tree | ~0.3µs MPI |
| Cray Aries | 16×96 Gb/s | Dragonfly | ~1µs MPI |
| HPE Cray EX Slingshot-12 | 400 Gb/s | Dragonfly+ | ~0.4µs |

**Near-Memory Compute:**
- NVIDIA Grace Hopper: Grace CPU (Neoverse V2, 72 cores) + Hopper GPU, connected via NVLink-C2C at 900 GB/s, 96GB HBM3 on GPU die + 480GB LPDDR5X on CPU, coherent memory
- Samsung UPMEM: PIM (Processing-In-Memory) DIMMs with 16 cores per DRAM die, eliminates data movement for embarrassingly parallel workloads
- CXL Type-3: memory expander devices with compute (FPGA or simple processor) — emerging for in-memory analytics

---

## 10. NoC and Cache Coherence

### 10.1 Interconnect Topologies

```
Ring (≤16 cores):
  C0 — C1 — C2 — C3 — ... — C15 — (back to C0)
  Bisection BW: 2× ring link width
  Pros: simple, low latency for neighboring nodes
  Cons: diameter grows with N, congestion at high core counts

2D Mesh (16–128 cores):
  C0 — C1 — C2 — C3
  |    |    |    |
  C4 — C5 — C6 — C7
  |    |    |    |
  ...
  Bisection BW: 4 × (N/2) links along cut
  Pros: scales well, used in Sapphire Rapids (60 tiles), Neoverse N2, Graviton 3
  Cons: corner-to-corner latency O(√N)

Torus:
  Mesh + wrap-around links → maximum hop count halved
  Used in custom HPC ASICs, Fujitsu A64FX, IBM Blue Gene
```

### 10.2 Cache Coherence Protocols

**MESI states:**
- **M** (Modified): dirty, exclusive. Cache has only copy, memory stale.
- **E** (Exclusive): clean, exclusive. Cache has only copy, memory up-to-date.
- **S** (Shared): clean, possibly shared with other caches.
- **I** (Invalid): not present.

**MESIF (Intel):**  
Adds **F** (Forward) state: one of the Shared copies is designated forwarder. When another cache requests the line, the Forward cache supplies it directly (peer-to-peer) without DRAM access. Reduces LLC bandwidth pressure.

**MOESI (AMD):**  
Adds **O** (Owned) state: dirty line that is shared. Owner supplies data to requesters without first writing back to memory. Reduces write-back traffic in producer-consumer patterns.

**Directory-Based Coherence:**  
Scales beyond snoopy broadcast. Each cache line has a directory entry listing which caches have copies. On miss, directory sends targeted invalidations/supplies to only relevant caches. Intel: inclusive L3 acts as snoop filter (if not in L3, not in any L1/L2). AMD: sparse directory per CCD + Infinity Fabric.

**Inclusive vs. Non-Inclusive L3:**
- Inclusive: L3 holds superset of L1/L2 content → acts as snoop filter → simpler protocol. Downside: 30–50% of L3 capacity "wasted" on lines that are live in L1/L2.
- Non-inclusive/exclusive: more effective L3 capacity, but requires explicit snoop filter structure.

### 10.3 CXL (Compute Express Link) — 2024 Status

- **CXL 1.1 / 2.0** (PCIe 5.0 physical): CXL.cache (device caches host memory), CXL.mem (host accesses device memory), CXL.io (standard PCIe). Additional latency: ~100–200ns over DDR5.
- **CXL 2.0** (2022): memory pooling via CXL switch, hot-plug memory.
- **CXL 3.0** (2023 spec): multi-level fabric, peer-to-peer coherence between endpoints, back-invalidation (device can push invalidations to host).
- **Production 2024**: Samsung CXL DRAM DIMMs (CMM-D), Micron CZ120, SK Hynix. Cloud providers (AWS, Google, Meta) deploying CXL for memory tiering — put cold memory on CXL devices at lower $/GB, reduce DDR5 DIMM count.

---

## 11. Power and Frequency

### 11.1 Power Model

```
P_total = P_dynamic + P_static

P_dynamic = α × C × V² × f
  α: activity factor (fraction of transistors switching per cycle, ~0.1–0.3)
  C: total gate capacitance
  V: supply voltage
  f: frequency

P_static (leakage) = I_leak × V
  Grows with temperature, node scaling (worse at smaller nodes)
  At 3nm: leakage ≈ 15–30% of total power
```

**Voltage-frequency (VF) curve:** Frequency increases ~linearly with voltage. Power increases quadratically. At 5nm, typical core: 1.0V → 3.5 GHz, 1.2V → 5.0 GHz, 1.3V → 5.5 GHz (rough estimates; varies by design and binning).

### 11.2 Boost Algorithms

**Intel Turbo Boost / Thermal Velocity Boost (TVB):**  
Monitor per-core temperature (DTS), package TDP, PL1/PL2 power limits. Boost frequency when headroom exists. TVB adds frequency bonus at lower temperatures.

**AMD Precision Boost 2:**  
Continuous per-core boost using telemetry (current draw, temperature, PPT/TDC/EDC limits). Finer granularity than Intel — adjusts per-millisecond. PBO (Precision Boost Overdrive) allows exceeding TDP limits with appropriate cooling.

**Apple Adaptive Boost:**  
Undisclosed algorithm. M4 Max P-cores run at 4.4 GHz sustained; boost behavior tied to thermal headroom of fanless/active cooling. M4 MacBook Pro (fan) sustains higher frequencies than M4 iPad Pro (fanless).

### 11.3 Dark Silicon

Esmaeilzadeh et al. (ISCA 2011): at 22nm, only 21% of a chip can be powered at maximum frequency within a fixed power budget; fraction worsens with scaling. At 5nm, estimated >50% of chip must be power-gated at any instant. Implication: can't run all functional units simultaneously at maximum frequency.

**Design responses:**
- Heterogeneous cores: ARM big.LITTLE / DynamIQ, Intel P+E, Apple P+E (2–4 P-cores + 4–8 E-cores). E-cores: 20–40% area, 15–20% power of P-cores, 60–70% IPC.
- Specialized accelerators: NPU (Apple 38 TOPS Neural Engine on M4), ISP, media encode/decode engines, ray-tracing unit. These are far more efficient (TOPS/W) than general-purpose SIMD for their specific workloads.

### 11.4 Clock Distribution

**Clock Tree Synthesis (CTS):**  
Goal: deliver rising edge simultaneously to all flip-flops in a clock domain within <10ps skew at 4 GHz (~2.5ns period = 0.4% skew budget). H-tree: hierarchical binary tree, equal wire lengths; ideal but ignores placement. Modern: H-tree skeleton + active deskew (delay elements) + mesh (distribute load at leaf level).

**Clock gating:**  
Insert enable-controlled clock gates at register file banks, FP units, cache banks. When inactive: clock off, dynamic power savings 20–40%.

**Clock Domain Crossing (CDC):**  
Core runs at one frequency (e.g., 4.0 GHz), uncore/mesh at another (e.g., 1.8 GHz). Data crossing domains uses async FIFO (typically 4–8 entries), two-flop synchronizer for control signals. Metastability failure rate ≈ 1/MTBF = f_s × f_d × exp(-t_resolve / τ); must be <10⁻¹⁵ events/second.

---

## 12. Building a Production-Grade Superscalar

### 12.1 RTL Language Selection

| Language | Paradigm | Pros | Cons | Best For |
|----------|----------|------|------|----------|
| SystemVerilog | Declarative HDL | Industry standard, full EDA tool support, IEEE standardized | Verbose, many footguns, no native metaprogramming | Production tapeout, IP licensing |
| Chisel (Scala DSL) | Functional HDL | Parameterizable generators, metaprogramming, FIRRTL IR | JVM toolchain overhead, Scala learning curve | Research (BOOM, Rocket, XiangShan) |
| SpinalHDL (Scala DSL) | Object-oriented HDL | More features than Chisel, native data types, clock domain types | Smaller community, less documentation | Fast prototyping, clean designs |
| Bluespec BSV | Rule-based HDL | Formal rule semantics, automatic scheduling, correctness by construction | Academic, limited commercial EDA support | Correctness-critical blocks (coherence, TLB) |
| Amaranth (Python) | Python DSL | Rapid iteration, good for hobbyists | Immature ecosystem, slow synthesis | Small blocks, learning |

### 12.2 Open-Source Reference Designs

**BOOM (Berkeley Out-of-Order Machine):**  
Chisel, full OoO RISC-V, supports multiple configurations (small/medium/large). Celio et al. 2017. Used in research papers. Active maintenance under CHIPS Alliance. Branch predictor: TAGE-SC-L implementation available. Good starting point for understanding full OoO design; code is complex but well-documented.

**CVA6 / Ariane:**  
SystemVerilog, 6-stage in-order-ish OoO (limited window), taped out in GF22nm. OpenHW Group CORE-V family. Supports Linux boot. Simpler than BOOM — good for understanding the interface between core and memory system without full OoO complexity.

**NaxRiscv:**  
SpinalHDL, modern OoO design, very clean and readable codebase. Active development 2023–2025. Best code quality among OSS designs for learning actual OoO microarchitecture implementation. Branch predictor: GSHARE + BTB, ITTAGE in progress.

**XiangShan (Yanqi Lake / Kunminghu):**  
Chisel, most advanced open-source OoO RISC-V. 6-wide decode, TAGE-SC-L, SMS prefetcher, full RVV 1.0. Closest to commercial performance. Xu et al., MICRO 2022. GitHub: `OpenXiangShan/XiangShan`. Requires significant compute for simulation.

**Rocket Chip:**  
Chisel, in-order, 5-stage, modular. Best starting point for learning pipeline discipline, cache interface, TLB, and Linux boot before adding OoO complexity. Includes TileLink interconnect, which scales to multi-core.

### 12.3 Simulation and Verification Toolchain

```
Layer          Tool                   Purpose
─────────────────────────────────────────────────────────────────
ISA golden     Spike (RISC-V)         Fast functional reference model for co-simulation
RTL sim (OSS)  Verilator              Cycle-accurate C++ model, 10–100× faster than event-driven
RTL sim (comm) Synopsys VCS / Cadence Xcelium  Industry standard; needed for complex UVM testbenches
Full system    QEMU + DPI cosim       Device models + CPU RTL via VPI/DPI bridge
Arch-level     gem5 (Binkert 2011)    Configurable OoO model, branch predictors, caches; good for design space exploration before RTL
Formal (OSS)   SymbiYosys + Yosys    Bounded model checking, property checking with SVA/PSL
Formal (comm)  JasperGold / VC Formal Full formal verification, equivalence checking
Coverage       gcov/lcov (sw), SystemVerilog functional coverage (hw)
```

**Co-simulation pattern (Spike + Verilator):**  
Each cycle, check that RTL core's register state matches Spike. Divergence → log differing instruction + state → root cause. This catches RTL bugs that don't manifest as crashes.

### 12.4 Physical Design Flow (Open-Source Path)

```
Step            OSS Tool              Commercial Equivalent
──────────────────────────────────────────────────────────────
RTL → Netlist   Yosys + ABC           Synopsys Design Compiler / Cadence Genus
Floorplan       OpenROAD              Cadence Innovus / Synopsys ICC2
Place & Route   OpenROAD (TritonRoute) Innovus / ICC2
STA             OpenSTA               Synopsys PrimeTime
DRC/LVS         KLayout + Magic       Mentor Calibre
GDS streaming   KLayout               Cadence Virtuoso
PDK (free)      SKY130 (130nm)        TSMC N3/N5 (commercial)
               GF180MCU (180nm)
```

**Critical path from RTL to GDS:**
1. Synthesis: map RTL → standard cell library gates; optimize for speed/area/power; generate gate-level netlist
2. Floorplan: place IO pads, macros (SRAM, PLL), define power grid (VDD/VSS stripes)
3. Place: legal placement of all standard cells in rows
4. CTS (Clock Tree Synthesis): build clock distribution network, meet skew targets
5. Route: connect all nets, meet DRC rules (min width, min spacing, via rules)
6. STA: verify all setup/hold timing constraints across all corners (PVT: process/voltage/temperature)
7. Signoff: DRC/LVS clean, antenna check, IR drop analysis
8. GDS tape-out: submit to foundry

### 12.5 FPGA Prototyping

| Platform | LUT Count | Suitable For | Cost/hr |
|----------|----------:|-------------|--------:|
| AWS F1 (UltraScale+) | 1.3M LUTs | IP prototyping, software bring-up | $1–5 |
| Xilinx VU9P | 2.5M LUTs | Moderate OoO core @ 50–100 MHz | $2000–3000 (purchase) |
| Xilinx VU19P | 9M LUTs | Large SoC or multi-core | $10000+ |
| Intel Stratix 10 | 2.7M LUTs | Alternative FPGA vendor | similar |

Multi-FPGA: complex SoCs require multiple FPGAs (separate FPGA for CPU, memory controller, peripherals) connected via SerDes links. Cadence Protium, Synopsys ZeBu: commercial emulation platforms, 10–100× faster than FPGA simulation.

### 12.6 Tape-Out Paths

| Program | Die Area | Cost | PDK | Turn-Around |
|---------|----------|-----:|-----|-------------|
| Tiny Tapeout | 160×100 µm | $100–300 | SKY130 130nm | Biannual |
| eFabless Chipignite | 10mm² | ~$10K | TSMC / SKY130 | ~6 months |
| MOSIS (educational) | varies | academic rates | various | varies |
| Europractice | 5–25mm² | €5K–30K | TSMC N28/N22, GF22 | 1–2 years |
| IMEC industrial | full reticle | $1M+ | TSMC N3/N2 | 2–3 years |

### 12.7 Practical Build Order

```
Step  What                         Why / Validation Gate
────────────────────────────────────────────────────────────────
1.    In-order 5-stage RISC-V      Establish pipeline discipline, hazard handling, TLB,
      (Rocket-derived or NaxRiscv  L1 cache with MSHR. Boot Linux on QEMU+Verilator.
      in-order variant)

2.    Add physical register file   Understand precise exceptions, RRAT, free list.
      + ROB (keep in-order issue)  Validate co-sim against Spike.

3.    Add issue queue + wakeup/    Most complex part. Start: unified 32-entry, 1 exec port.
      select (single port)        Expand to distributed 64-entry per cluster, then 4 ports.

4.    Add L1D/L2 cache MSHR       Single-core cache coherence, store queue, STLF.
      + store queue               Run memory ordering torture tests (litmus tests).

5.    Add branch predictor         Start: gshare 8K entries. Then TAGE 4 components.
                                   Measure MPKI on SPEC CPU traces.

6.    Add hardware prefetcher      Stride prefetcher first. Measure DRAM BW utilization.

7.    Multi-core + MESI snoopy     2–4 cores, ring interconnect, snoopy coherence.
      coherence                   Run concurrent litmus tests, coherence stress tests.

8.    FPGA synthesis + Linux boot  Full validation: compile kernel, run benchmarks,
                                   achieve coremark/dhrystone targets.

9.    Performance profiling        Tune ROB/PRF/IQ sizing via gem5 design space exploration.
      + tuning                    Target specific SPEC CPU workloads.
```

---

## 13. Key References

1. Tomasulo, R.M. (1967). "An efficient algorithm for exploiting multiple arithmetic units." *IBM Journal of Research and Development* 11(1): 25–33.

2. Smith, J.E., Pleszkun, A.R. (1988). "Implementing Precise Interrupts in Pipelined Processors." *IEEE Transactions on Computers* 37(5): 562–573.

3. Yeh, T.-Y., Patt, Y.N. (1992). "Alternative Implementations of Two-Level Adaptive Branch Prediction." *ISCA* 19: 124–134.

4. Sohi, G.S., Vajapeyam, S. (1987). "Tradeoffs in Instruction Format Design for Horizontal Architectures." *ASPLOS* II: 15–25.

5. Kessler, R.E. (1999). "The Alpha 21264 Microprocessor." *IEEE Micro* 19(2): 24–36.

6. Tendler, J.M. et al. (2002). "POWER4 System Microarchitecture." *IBM Journal of Research and Development* 46(1): 5–25.

7. Chrysos, G.Z., Emer, J.S. (1998). "Memory Dependence Prediction Using Store Sets." *ISCA* 25: 142–153.

8. Nesbit, K.J., Smith, J.E. (2004). "Data Cache Prefetching Using a Global History Buffer." *HPCA*: 96–105.

9. Seznec, A., Michaud, P. (2006). "A Case for (Partially) Tagged GEometric History Length Branch Prediction." *JILP* 8: 2–23.

10. Srinath, S. et al. (2007). "Feedback Directed Prefetching: Improving the Performance and Bandwidth-Efficiency of Hardware Prefetchers." *HPCA*.

11. Binkert, N. et al. (2011). "The gem5 Simulator." *ACM SIGARCH Computer Architecture News* 39(2): 1–7.

12. Esmaeilzadeh, H. et al. (2011). "Dark Silicon and the End of Multicore Scaling." *ISCA* 38: 365–376.

13. Seznec, A. (2011). "A New Case for the TAGE Branch Predictor." *MICRO* 44: 117–127. (TAGE-SC-L)

14. Lam, M.S., Wilson, R.P. (1992). "Limits of Control Flow on Parallelism." *ISCA* 19: 46–57.

15. Ferdman, M. et al. (2012). "Clearing the Clouds: A Study of Emerging Scale-Out Workloads on Modern Hardware." *ASPLOS* XVII: 37–48.

16. Gochman, S. et al. (2003). "The Intel Pentium M Processor: Microarchitecture and Performance." *Intel Technology Journal* 7(2).

17. Celio, C. et al. (2017). "The Berkeley Out-of-Order Machine (BOOM): An Industry-Competitive, Synthesizable, Parameterized RISC-V Processor." *UCB/EECS-2017-2*.

18. Bera, R. et al. (2021). "Pythia: A Customizable Hardware Prefetching Framework Using Online Reinforcement Learning." *MICRO* 54: 1121–1137.

19. Xu, Y. et al. (2022). "Towards Developing High Performance RISC-V Processors Using Agile Methodology." *MICRO* 55: 1–13. (XiangShan)

20. Seznec, A. (2014). "TAGE-SC-L Branch Predictors Again." *5th JILP Workshop on Computer Architecture Competitions (JWAC-5)*.

21. Kim, J. et al. (2019). "Revisiting Virtual Memory Translation for Hardware Prefetchers." *ISCA* 46: 840–852.

22. Gabbay, F., Mendelson, A. (1997). "Speculative Execution Based on Value Prediction." *Technion Tech Report CS0974*.

23. Fog, A. (continuously updated). "Microarchitecture of Intel, AMD and VIA CPUs." *agner.org/optimize/microarchitecture.pdf*. (Definitive source for x86 µarch tables)

24. AMD. (2024). "Software Optimization Guide for AMD EPYC 9004 Series Processors (Zen 4)." AMD Publication 57647.

25. Intel. (2024). "Intel Core Ultra (Series 2) — Microarchitecture Overview (Lion Cove)." Intel Architecture Disclosure.

---

*Last updated: 2026-04-23*
