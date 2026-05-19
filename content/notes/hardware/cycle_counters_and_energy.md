+++
title = "Cycle Counters And Energy"
date = 2026-03-26
[taxonomies]
tags = ["rdtsc", "performance-counters", "energy", "benchmarking", "x86", "arm", "risc-v"]
+++


# Cycle Counters, Timing, and Per-Cycle Energy

## Reading the Cycle Counter

### x86-64: `RDTSC` / `RDTSCP`

```c
uint64_t cycles;
unsigned lo, hi;
asm volatile("rdtscp" : "=a"(lo), "=d"(hi) :: "rcx");
cycles = ((uint64_t)hi << 32) | lo;
```

- `RDTSCP` is preferred — it serializes (waits for prior instructions to complete), giving more accurate measurements
- `RDTSC` alone can be reordered by the CPU; wrap with `LFENCE` if using it
- Common benchmarking pattern: `LFENCE; RDTSC` before, `RDTSCP; LFENCE` after
- TSC frequency ≠ core frequency on modern CPUs (invariant TSC runs at fixed reference frequency). Use `CPUID` leaf `0x15` to get the ratio, or calibrate against a known clock

### AArch64: `CNTVCT_EL0` / `PMCCNTR_EL0`

```c
uint64_t val;
asm volatile("mrs %0, cntvct_el0" : "=r"(val));   // generic timer
asm volatile("mrs %0, pmccntr_el0" : "=r"(val));   // cycle counter (needs kernel enable)
```

- `CNTVCT_EL0` reads the generic timer (fixed frequency, not true cycles)
- `PMCCNTR_EL0` gives actual CPU cycles but requires `PMUSERENR_EL0.EN = 1` — usually disabled by default
- On Linux 6.0+: `echo 1 > /proc/sys/kernel/perf_user_access` to enable userspace PMU access

### RISC-V: `rdcycle` / `rdtime`

```c
uint64_t cycles;
asm volatile("rdcycle %0" : "=r"(cycles));
```

- `rdcycle` is being deprecated in newer privilege specs (ratified 2024) due to side-channel concerns
- Some implementations trap it to M-mode or return zero
- `rdtime` (wall-clock) remains available

### macOS Apple Silicon

```c
#include <mach/mach_time.h>

uint64_t start = mach_absolute_time();
// ... work ...
uint64_t end = mach_absolute_time();
uint64_t elapsed = end - start;
```

- Reads `CNTVCT_EL0` directly — the fixed-frequency timer
- On Apple Silicon, `numer/denom` is `1/1`, counter ticks at **24 MHz** (~41.67 ns resolution)
- These are **not** actual CPU cycles
- Apple **blocks** userspace access to `PMCCNTR_EL0` — reading it will `SIGILL`
- True cycle counts require private APIs (`kperf`/`CPMU`) or kernel-level access
- Projects that use private PMU access: `dougallj/applecpu`, AsahiLinux `m1n1`

## Counter Granularity Comparison

| Counter | Granularity | Measures |
|---|---|---|
| x86 `RDTSC` | ~0.3 ns (1 cycle) | Reference cycles |
| ARM `CNTVCT_EL0` | 10–52 ns | Wall time |
| ARM `PMCCNTR_EL0` | ~0.3 ns (1 cycle) | Core cycles |
| RISC-V `rdcycle` | ~1 cycle | Core cycles |
| RISC-V `rdtime` | ~100 ns | Wall time |
| macOS `mach_absolute_time` | ~42 ns | Wall time |

Typical timer frequencies by platform:
- Apple Silicon: 24 MHz (~42 ns)
- Qualcomm Snapdragon: 19.2 MHz (~52 ns)
- ARM Juno/FVP: 100 MHz (10 ns)
- AWS Graviton: 25 MHz (40 ns)
- RISC-V (most): 10 MHz (100 ns)

## Cost of Reading the Counter

The counter ticking at core frequency does **not** mean you "spend" those cycles. The counter is a **separate hardware register** that increments independently — a dedicated flip-flop chain wired to the clock tree. Reading it is like glancing at a wall clock.

### Instruction Latencies

| Instruction | Latency |
|---|---|
| x86 `RDTSC` | ~20-35 cycles |
| x86 `RDTSCP` | ~35 cycles (serializing) |
| x86 `LFENCE; RDTSC` pair | ~40 cycles total |
| ARM `CNTVCT_EL0` (Apple Silicon) | ~3-5 cycles |
| ARM `CNTVCT_EL0` (Cortex-A) | ~40-80 cycles (may trap to EL1) |
| ARM `PMCCNTR_EL0` (enabled) | ~3-10 cycles |
| ARM `PMCCNTR_EL0` (trapped to kernel) | ~1000+ cycles |
| RISC-V `rdcycle` (hardware) | ~1 cycle |
| RISC-V `rdcycle` (trapped to M-mode) | ~100+ cycles |

A full measurement pair (before + after) on x86:

```
LFENCE          ;  ~5 cycles
RDTSC           ; ~20 cycles
... work ...
RDTSCP          ; ~35 cycles
LFENCE          ;  ~5 cycles
                ; ~65 cycles overhead total
```

For 10,000 cycles of work, that's **0.65%** overhead. For 50 cycles of work, the overhead **dominates** — use statistical methods (millions of iterations, take the median).

## How the Counter is Powered

The counter is just 64 flip-flops with carry logic — a few thousand transistors out of billions. It's wired to the clock tree that already drives the entire core.

```
Clock signal ──┬── Core pipeline (fetch/decode/execute/...)
               ├── L1/L2 caches
               ├── TSC counter (just ~64 flip-flops)
               └── everything else on the die
```

The counter runs **continuously in hardware**, independent of instruction execution. It increments every cycle regardless of what the CPU is doing — including stalls, cache misses, halt states, or when no instructions execute.

```
Cycle   Counter    CPU doing
─────   ───────    ─────────
  0       0        add r1, r2
  1       1        mul r3, r4
  2       2        load [mem]
  3       3        load [mem]  (cache miss, stalled)
  4       4        load [mem]  (still stalled)
  5       5        RDTSC  ← read "5" here
  6       6        ... work ...
  7       7        ... work ...
  8       8        RDTSCP ← read "8" here → delta = 3
```

### TSC Behavior in Power States

| State | TSC behavior |
|---|---|
| Running | Ticks at reference frequency |
| C1 (halt) | Still ticks (invariant TSC) |
| C3/C6 (deep sleep) | Still ticks — maintained by separate always-on clock domain |
| Package off | Stops |

Modern invariant TSC (Intel since Nehalem, AMD since Bulldozer) runs off a **separate small oscillator** in an always-on power domain, drawing microwatts.

## Per-Cycle Energy: CPU

A core at ~3.5 GHz drawing ~5W (typical single-core active power):

```
5 W / 3,500,000,000 cycles/sec = ~1.4 nanojoules per cycle
```

That's an average. Individual cycles vary:

| What's happening | Approximate energy/cycle |
|---|---|
| Core power-gated (C6) | ~0 (leakage only, picojoules) |
| Halted (`HLT`/`WFE`) | ~0.1-0.3 nJ (clock tree toggling) |
| Simple ALU (add/shift) | ~0.5-1 nJ |
| Branch + decode heavy | ~1-2 nJ |
| L1 cache hit | ~1-2 nJ |
| L2 cache hit | ~3-5 nJ |
| AVX-512 FMA (all lanes) | ~5-10 nJ |
| L3 / DRAM access | ~10-50 nJ (spread across stall cycles) |

### Where Energy Goes in a Single Cycle

```
Clock distribution  ~30-40%   (toggling every flip-flop's clock input)
Datapath switching  ~20-30%   (actual computation, depends on operands)
Leakage             ~20-30%   (static, happens even if nothing toggles)
I/O + memory ctrl   ~10%
```

The clock tree is the dominant cost. Even a "do nothing" stall cycle toggles billions of clock inputs. **Clock gating** — selectively shutting off the clock to unused units — is the #1 power optimization in modern chips.

A single flip-flop toggle costs about **1-10 femtojoules** on modern process nodes (5nm). The TSC counter with 64 flip-flops costs roughly **~0.5 picojoules per cycle**.

## Per-Cycle Energy: GPU

GPUs have massive parallelism with a completely different power profile.

### NVIDIA A100 (~300W TDP, 1.4 GHz, 6912 CUDA cores, 108 SMs)

```
Whole chip:  300W / 1.4 GHz      = ~214 nJ per cycle
Per SM:      ~2.8W / 1.4 GHz     = ~2 nJ per cycle per SM
Per CUDA core: ~43 mW / 1.4 GHz  = ~31 pJ per cycle per core
```

Each CUDA core draws far less than a CPU core because it's tiny and simple — just an FMA unit, no branch predictor, no out-of-order engine, no speculation.

### CPU vs GPU Energy Comparison

| Unit | Energy/cycle | Why |
|---|---|---|
| CPU core (x86 OoO) | ~1,400 pJ | Branch predictor, ROB, complex decode, speculation |
| GPU SM (32 lanes) | ~2,000 pJ | 32 simple ALUs + shared control + register file |
| Single CUDA core | ~31 pJ | Just an FMA, amortized control |
| Tensor Core (one op) | ~50-100 pJ | 4×4 matrix FMA |
| CPU TSC counter | ~0.5 pJ | 64 flip-flops toggling |

### Why GPUs Win on FLOPS/Watt

```
CPU:                          GPU SM:
┌─────────────────────┐       ┌──────────────┐
│ Branch predictor    │       │ One fetch     │
│ OoO scheduler       │       │ One decode    │
│ Rename/ROB (256+)   │       │ One scheduler │
│ Complex decode ×4-6 │       │              │
│ Speculation engine  │       │ Shared across:│
│                     │       │ ├─ ALU 0      │
│ ALU 0   ALU 1      │       │ ├─ ALU 1      │
│ ALU 2   ALU 3      │       │ ├─ ...        │
│                     │       │ └─ ALU 31     │
│ 4 wide             │       │              │
└─────────────────────┘       └──────────────┘
~70% overhead, 30% compute   ~20% overhead, 80% compute
```

A CPU spends most energy on **control** (figuring out what to execute). A GPU spends most on **compute** (doing math). This yields ~10-50× better FLOPS/watt.

### Tensor Core Minimum Granularity

Tensor Cores are wired for fixed-size matrix operations — you cannot do a single multiply-add. The minimum is a **4×4×4** matrix FMA:

```
D = A × B + C

A = 4×4, B = 4×4, C = 4×4 (accumulator), D = 4×4 result
= 64 FMAs in one instruction
```

The hardware is a physically connected systolic array:

```
        B columns
        ↓ ↓ ↓ ↓
A row → ● ● ● ●  → partial sums accumulate
A row → ● ● ● ●  → across the array
A row → ● ● ● ●  → in one cycle
A row → ● ● ● ●  →
        + + + +
        C (accumulator)
```

At the warp level, the actual `WMMA` API exposes **16×16×16** as the minimum usable size (8192 FMAs per instruction across 32 threads). On Hopper (H100), `WGMMA` goes to **64×256×16** tiles.

### Why Matrix Sizes Matter

| Matrix dimension | Tensor Core utilization |
|---|---|
| 16×16 | ~100% |
| 17×17 | Padded to 32×32, ~28% utilized |
| 128×128 | ~100% |
| 127×127 | ~2% waste (minor) |
| 7×7 | Terrible — mostly padding |

Undersized matrices must be **zero-padded**, wasting compute. This is why ML frameworks pad hidden dimensions to multiples of 8/16/64 — `hidden_dim=1023` is measurably slower than `hidden_dim=1024`.

### Energy per FLOP

```
A100 Tensor: 312 TFLOPS BF16 at 300W → ~1 pJ per FLOP
CPU AVX-512: ~2 TFLOPS BF16 at 250W  → ~125 pJ per FLOP
```

Roughly **100× worse** energy efficiency per FLOP on a CPU. This is why ML training runs on GPUs.

---

## See Also

- [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) — RDTSC/RDTSCP, CNTVCT_EL0, and rdcycle instructions for reading cycle counters across ISAs
- [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) — GPU microarchitecture and Tensor Core design explaining the energy-per-FLOP numbers here
- [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) — perf_event_open, PEBS, and PMU access for measuring cycle counts from userspace
