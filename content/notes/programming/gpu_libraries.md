+++
title = "GPU Libraries"
date = 2026-07-10
[taxonomies]
tags = ["gpu", "cuda", "rocm", "metal", "sycl", "vulkan", "rust", "python", "optimization"]
+++


# GPU Programming Libraries: CUDA, ROCm, Metal, and the Portable Stack

The programmer-facing companion to [GPU/TPU/Accelerator Chip Design](@/notes/hardware/gpu_tpu_accelerator_design.md).
That doc covers the **silicon** — SIMT execution, Tensor-Core hardware, HBM hierarchy, the roofline
(§18). This doc covers how you actually **write, compile, tune, and ship** GPU code across NVIDIA,
AMD, and Apple, from C/C++ down to PTX/SASS up through Rust and Python, plus the cross-vendor
portability layers. Where the two overlap (roofline, arithmetic intensity, interconnect bandwidth),
this doc links rather than duplicates: see also [PCIe Internals](@/notes/hardware/pcie_internals.md) and
[Compute Interconnects](@/notes/hardware/interconnects.md) for the host↔device link layer.

Accuracy note: several performance multipliers below (wgmma vs mma peak, DSMEM speedup, Apple
tensor-op tile sizes, precision throughput ratios) are **vendor-stated or reverse-engineered**, not
independently reproduced. They are flagged inline. Version-pinned facts (library versions, M-series
bandwidth, Rust-CUDA nightly) are current as of **mid-2026** — re-verify before relying on exact
numbers.

## Table of Contents

0. [Primer: the GPU programming model and the library landscape](#0-primer-the-gpu-programming-model-and-the-library-landscape)
1. [NVIDIA — the compilation pipeline (nvcc → PTX → SASS)](#1-nvidia-the-compilation-pipeline-nvcc-ptx-sass)
2. [NVIDIA — execution and memory model](#2-nvidia-execution-and-memory-model)
3. [NVIDIA — Tensor Cores and GEMM (WMMA → wgmma → tcgen05)](#3-nvidia-tensor-cores-and-gemm-wmma-wgmma-tcgen05)
4. [NVIDIA — libraries and runtime services](#4-nvidia-libraries-and-runtime-services)
5. [NVIDIA — optimization techniques and profiling](#5-nvidia-optimization-techniques-and-profiling)
6. [AMD — ROCm and HIP](#6-amd-rocm-and-hip)
7. [AMD — libraries, matrix cores, MI300](#7-amd-libraries-matrix-cores-mi300)
8. [Apple — Metal compute](#8-apple-metal-compute)
9. [Apple — ML stack and MLX](#9-apple-ml-stack-and-mlx)
10. [Cross-vendor portability: SYCL, Vulkan, wgpu](#10-cross-vendor-portability-sycl-vulkan-wgpu)
11. [Rust on the GPU](#11-rust-on-the-gpu)
12. [Python on the GPU](#12-python-on-the-gpu)
13. [C / C++ across vendors](#13-c-c-across-vendors)
14. [Cross-cutting optimization theory](#14-cross-cutting-optimization-theory-vendor-neutral)
15. [Limitations and advantages matrix](#15-limitations-and-advantages-matrix)
16. [Key references](#16-key-references)

---

<a name="0-primer"></a>
## 0. Primer: the GPU programming model and the library landscape

A GPU is a throughput machine that hides latency with **massive parallelism rather than caches**. The
programming model everywhere is the same shape regardless of vendor:

```
HOST (CPU)                          DEVICE (GPU)
──────────                          ────────────
allocate device buffers    ──────►  global memory (HBM / unified pool)
copy inputs H2D            ──────►
launch kernel(grid, block) ──────►  thousands of threads, grouped into
                                    warps/wavefronts/SIMD-groups (lockstep lanes),
                                    grouped into blocks/workgroups/threadgroups
                                    (share on-chip scratchpad + a barrier),
                                    grouped into a grid
synchronize / wait         ◄──────  kernel completion (async by default)
copy outputs D2H           ◄──────
```

A **kernel** is a function that runs per-thread; you launch a *grid* of *blocks* of *threads*. Threads
in the same block share a fast on-chip scratchpad and can barrier-synchronize; threads across blocks
generally cannot (before Hopper clusters). The lockstep unit — the *warp* — is the granularity at
which the hardware actually issues instructions, and almost every optimization reduces to "keep the
lanes of a warp doing coherent work over coalesced memory."

**Why libraries exist.** Writing raw kernels that hit peak throughput is hard (coalescing, bank
conflicts, register pressure, tensor-core fragment layouts). So the ecosystem is layered:

- **Low-level kernel languages** — CUDA C++, HIP C++, Metal Shading Language, SYCL, compute shaders.
- **Primitive libraries** — BLAS/DNN/FFT/collectives (cuBLAS/cuDNN/NCCL; rocBLAS/MIOpen/RCCL; MPS).
- **Template kernel libraries** — CUTLASS, Composable Kernel — for those who need the last 10%.
- **Array/DSL frontends** — CuPy, Triton, JAX, MLX — "write Python (or a tile DSL), get a fast kernel."
- **Portability layers** — SYCL, Vulkan, wgpu, Kokkos — one source, many backends.

### Cross-stack concept map

The same idea has three names. This table is the Rosetta stone for the rest of the doc:

| Concept | NVIDIA CUDA | AMD ROCm/HIP | Apple Metal |
|---|---|---|---|
| Lockstep lane group | **warp = 32** | **wavefront = 64** (GCN/CDNA) / **32** (RDNA) | **SIMD-group = 32** |
| Cooperating thread group | thread block | workgroup | threadgroup |
| On-chip scratchpad | shared memory (up to 228 KB/SM) | **LDS** (~64 KB/CU, 32 banks) | threadgroup memory |
| Per-thread fast storage | registers (64K×32b/SM) | VGPRs (+ scalar **SGPRs**) | registers |
| Matrix/tensor unit | Tensor Cores | **MFMA** (CDNA) / **WMMA** (RDNA3+) | **simdgroup_matrix** / Metal 4 TensorOps |
| Dense GEMM library | cuBLAS / cuBLASLt | rocBLAS / hipBLASLt (+Tensile) | MPS / MLX |
| DNN primitives | cuDNN | MIOpen | MPSGraph |
| Collectives | NCCL | RCCL | — |
| Template kernel lib | CUTLASS (+ CuTe) | Composable Kernel | (MLX kernels) |
| Graph capture (kill launch cost) | CUDA Graphs | hipGraph | MLX lazy graph |
| Memory model | discrete VRAM (+ optional UM) | discrete / **MI300A UMA-HBM** | **UMA (LPDDR5X), zero-copy** |
| Manual memory-op sync | implicit (compiler) | explicit **`s_waitcnt`** | implicit |
| Native FP64 | yes | yes (CDNA) / weak (RDNA) | **no** |
| Profilers | Nsight Systems / Compute | rocprof / Omniperf / RGP | Xcode GPU capture / Instruments |

Read this table once; it explains why a technique that helps on one vendor (e.g. 64-wide-wavefront
reductions on AMD) needs adjusting on another (32-wide warps on NVIDIA/Apple).

---

<a name="1-nvidia-compilation"></a>
## 1. NVIDIA — the compilation pipeline (nvcc → PTX → SASS)

CUDA has the deepest, most mature stack (≈15 years). Understanding it starts with **how source becomes
machine code**, because that pipeline is the root of both its portability story and its lock-in.

### 1.1 Two host APIs

- **Runtime API** (`cudart`, `cuda_runtime.h`): high-level, implicit context/module management, the
  `<<<grid, block>>>` launch syntax, lazy context init. ~95% of application code uses this.
- **Driver API** (`libcuda.so`: `cuMalloc`, `cuModuleLoad`, `cuLaunchKernel`): explicit `CUcontext` /
  `CUmodule` / `CUfunction` handles. Needed for JIT-loading PTX/cubin at runtime, multi-context
  control, and toolkit-independent deployment. Frameworks (PyTorch, TensorFlow) bind the driver API.

The runtime API is built *on top of* the driver API; you can mix them, with gotchas around primary
context sharing (`cuDevicePrimaryCtxRetain`).

### 1.2 The nvcc pipeline

```
.cu ──nvcc──► host C++  ─────────────────────────► host compiler (gcc/clang/msvc)
          └► device code ──cicc (NVVM/LLVM)──► PTX ──ptxas──► SASS (cubin)
                                                         └──► fatbin (multi-arch bundle)
```

- **NVVM IR** — NVIDIA's LLVM-based device IR; `cicc` is the LLVM-backed device front end.
- **PTX (Parallel Thread eXecution)** — a *virtual ISA*. Forward-compatible, stable across GPU
  generations, semi-readable. **Not executed directly.**
- **ptxas** — the PTX→SASS optimizing assembler. **This is where the perf-relevant decisions happen:**
  register allocation, instruction scheduling, `-O3`, `--maxrregcount`, and `-v` (prints per-kernel
  register / shared-mem / spill usage — always read this).
- **SASS (Streaming ASSembler)** — the actual per-architecture machine ISA. Undocumented, changes
  every generation. Disassemble with `cuobjdump -sass` / `nvdisasm`.

**The JIT path is the forward-compat mechanism:** if a binary ships only PTX (or the driver finds no
matching cubin), the driver JIT-compiles PTX→SASS *at load*, caching in `~/.nv/ComputeCache`. So PTX
built for `compute_70` runs on Blackwell — at the cost of a first-run JIT.

### 1.3 Arch targeting and compute capability

`-gencode arch=compute_XX,code=sm_XX`: `compute_XX` = virtual (emit PTX), `sm_XX` = real (emit SASS).
Common idiom: embed SASS for known arches **plus** PTX of the highest virtual arch as a JIT fallback.

| Arch | CC | Example parts |
|---|---|---|
| Volta | sm_70 | V100 |
| Turing | sm_75 | T4, RTX 20xx |
| Ampere | sm_80 / sm_86 | A100 / GA10x |
| Ada | sm_89 | RTX 40xx, L40 |
| Hopper | sm_90 / **sm_90a** | H100/H200 |
| Blackwell | sm_100 / **sm_100a** / sm_120 | B100/B200 (datacenter) / consumer |

The **`a` suffix** (`sm_90a`, `sm_100a`) marks **architecture-specific** features — wgmma, tcgen05, TMA
— that have **no forward-compatible PTX**. Code using them must be recompiled per arch; there is no JIT
portability. This is the single biggest "gotcha" of programming peak-perf Hopper/Blackwell.

`__CUDA_ARCH__` gates device-side codepaths per arch. `cubin` = single-arch ELF; `fatbin` = a container
of multiple cubins + PTX.

### 1.4 Occupancy and launch bounds

- `__launch_bounds__(maxThreadsPerBlock, minBlocksPerSM)` caps ptxas register allocation so the kernel
  *fits* at the target occupancy. Without it, ptxas may allocate too many registers and silently cut
  occupancy.
- **Occupancy** = active warps / max warps per SM, limited by the min of (registers/thread,
  shared-mem/block, blocks/SM, threads/SM).
- `cudaOccupancyMaxPotentialBlockSize` (and the Nsight Compute occupancy section) find the
  occupancy-maximizing block size — **but max occupancy ≠ max performance** (see §5, Volkov).

---

<a name="2-nvidia-exec-memory"></a>
## 2. NVIDIA — execution and memory model

### 2.1 The thread hierarchy and SIMT

thread → **warp (32 threads, fixed)** → block (≤1024 threads) → [cluster, Hopper+] → grid. A warp
executes one instruction across 32 lanes (SIMT). Divergent branches within a warp **serialize** —
predication or reconvergence — at a cost proportional to the number of distinct paths taken. Sort or
bucket data to keep warps coherent.

### 2.2 Independent thread scheduling (Volta+) — a correctness trap

Pre-Volta, a warp had **one shared PC**; lanes were implicitly warp-synchronous. Volta gives **per-thread
PC and call stack**, so lanes make independent forward progress (fixing certain producer/consumer
deadlocks). The consequence for you: **you can no longer assume implicit warp-synchronous behavior.**
Old code that did warp reductions without synchronization is now **undefined behavior**. You must use the
explicit `_sync` intrinsics with masks:

```cuda
// Warp-level sum reduction (correct on Volta+). Butterfly over log2(32)=5 steps.
__inline__ __device__ int warpReduceSum(int v) {
    for (int offset = 16; offset > 0; offset >>= 1)
        v += __shfl_down_sync(0xffffffff, v, offset);  // mask MUST cover active lanes
    return v;
}
```

### 2.3 Synchronization primitives

- `__syncthreads()` — block-wide barrier (all threads in the block).
- `__syncwarp(mask)` — warp-level reconvergence barrier.
- **Cooperative Groups** (CUDA 9+) — typed group abstraction: `thread_block`,
  `thread_block_tile<32>`, `coalesced_group`, `grid_group` (grid-wide sync via
  `cudaLaunchCooperativeKernel`), `cluster_group` (Hopper). The portable replacement for raw intrinsics.
- **Warp shuffle** — `__shfl_sync`, `__shfl_down_sync`, `__shfl_xor_sync`: register-to-register exchange
  within a warp, no shared memory. XOR shuffle = butterfly reduction/scan in 5 steps.

### 2.4 Memory spaces

| Space | Scope | Approx latency | Notes |
|---|---|---|---|
| Register | thread | ~0 | 64K × 32-bit/SM; spills → local |
| Shared (SMEM) | block | ~20–30 cyc | on-chip, 32 banks × 4 B; up to **228 KB/SM** (H100/Blackwell), 164 KB (A100), opt-in via `cudaFuncAttributeMaxDynamicSharedMemorySize` |
| L1 / Tex | SM | ~30 cyc | unified with shared on Volta+ (configurable split) |
| Constant | grid (RO) | cache | 64 KB, broadcast-optimized for uniform access |
| L2 | GPU | ~200 cyc | 50 MB on H100; residency control below |
| Global / DRAM | GPU | ~400–800 cyc | HBM3/HBM3e |
| Local | thread | = global | **register-spill target — slow, avoid** |

**Coalescing** is the single biggest global-memory lever: a warp's 32 accesses falling in the same
128 B line (or 32 B sectors) → 1 transaction; strided/scattered → many.

**Bank conflicts**: SMEM has 32 banks of 4 B. A warp hitting 32 distinct banks = 1 cycle; N lanes on the
same bank = N-way conflict, serialized N×; same-address = free broadcast. Classic fixes: **pad** a
`[32][32]` tile to `[32][33]` (wastes a column), or **XOR-swizzle** the index (no waste, non-linear —
used in CUTLASS/cuBLAS and TMA layouts).

**L2 residency control** (Ampere+): `cudaAccessPolicyWindow` / stream attributes mark a global range as
*persisting* in an L2 carve-out (up to ~40 MB on H100) — wins for repeatedly-read weights/embeddings.

### 2.5 Async data movement: cp.async, TMA, clusters

- **`cp.async`** (Ampere sm_80+, `__pipeline_memcpy_async` / `cuda::memcpy_async`) copies global→shared
  **bypassing the register file** (optionally L1), freeing registers and overlapping the copy with
  compute. With `cuda::pipeline` you build multi-stage (double/triple-buffered) prefetch — the
  pre-Hopper software-pipelining primitive for GEMM/stencil.
- **TMA — Tensor Memory Accelerator** (Hopper sm_90+): a dedicated **hardware DMA engine** for bulk
  multidimensional (up to 5D) tiled copies global↔shared, driven by a `CUtensorMap` descriptor built
  once on host. A **single thread** issues the copy; hardware computes addresses, handles
  boundary/swizzle, and signals an **mbarrier** on completion. Vs `cp.async`, TMA removes *all*
  per-thread address arithmetic and register pressure and does swizzling in hardware to feed wgmma —
  essential for peak Hopper/Blackwell GEMM.
- **Thread-block clusters + Distributed Shared Memory (DSMEM)** (Hopper): a **cluster** co-schedules up
  to 16 blocks on the same GPC; their shared memories form one addressable space over a dedicated
  SM-to-SM network (bypassing L2). One block can load/store/atomic directly into a peer's SMEM —
  **~7× faster block-to-block exchange than round-tripping global memory** *(NVIDIA-stated figure)* —
  enabling effective tiles larger than a single 228 KB SMEM. Programmed via `cluster_group`.

---

<a name="3-nvidia-tensor-cores"></a>
## 3. NVIDIA — Tensor Cores and GEMM (WMMA → wgmma → tcgen05)

Tensor Cores are the reason NVIDIA dominates ML. Evolution:
Volta (FP16) → Turing (INT8/4) → Ampere (TF32, BF16, 2:4 sparsity) → Hopper (FP8 E4M3/E5M2, wgmma) →
Blackwell (FP4 E2M1, FP6, 2nd-gen Transformer Engine, tcgen05).

### 3.1 The programming levels (low → high)

- **WMMA API** (`nvcuda::wmma`, `mma.h`): warp-level `load_matrix_sync` / `mma_sync` /
  `store_matrix_sync` over opaque `fragment` types. Portable, but leaves ~30–40% on the table vs
  hand-tuned. Fixed tile shapes (16×16×16 etc.).
- **`mma.sync` PTX** (sm_70+): explicit warp-level MMA with defined fragment register layouts. Full
  control; what CUTLASS emitted pre-Hopper.
- **`wgmma.mma_async`** (Hopper sm_90a): a **warpgroup = 4 warps = 128 threads** acts as one *async* MMA
  unit. Operand B must be in SMEM; A in SMEM or registers; accumulator C in registers. Async +
  descriptor/fence semantics. **wgmma reportedly hits >96% of H100's 989 TF/s BF16 peak for N≥64, vs
  ~63% for legacy `mma.sync`** *(NVIDIA/Colfax figures — corroborate with the arXiv Hopper microbench
  paper before quoting)*.
- **`tcgen05.mma` / UMMA** (Blackwell sm_100a): 5th-gen. `D = A*B + C` at a **stated 2–4× wgmma
  throughput** (data-type-dependent), using a dedicated **Tensor Memory (TMEM)** for accumulators (off
  the register file). Native FP4 (e2m1) and FP6, mixed low-precision with FP16/FP32 accumulation, and
  **built-in block scaling** (microscaling / MXFP). Can span 2 SMs. *(Multipliers are vendor-stated;
  the low-level TMEM/descriptor layouts are `sm_100a`-specific and move fast — track CUTLASS source.)*

### 3.2 Libraries over Tensor Cores

- **cuBLAS / cuBLASLt** — cuBLASLt is the extended, tunable GEMM API with **epilogue fusion**
  (bias+GELU+scale), FP8 scaling, and algorithm/heuristic selection. Production default for dense GEMM.
- **CUTLASS** — the open-source C++ template GEMM/conv library and the *reference* for how to program
  Tensor Cores. **CuTe** (CUTLASS 3.x) is its core abstraction: a **`Layout = Shape ⊗ Stride`** algebra
  over hierarchical Tensors, unifying thread/data mapping, swizzles, TMA descriptors, and MMA atoms in
  one composable type system. If you write custom tensor-core kernels, CuTe layouts are the mental model
  to learn (Colfax Research tutorials are the best on-ramp).
- **cuDNN** — DNN primitives (conv via implicit-GEMM/Winograd/FFT, attention, norm, pooling); v8+ graph
  API for fusion.

---

<a name="4-nvidia-libraries"></a>
## 4. NVIDIA — libraries and runtime services

| Library | Role | Notes |
|---|---|---|
| **Thrust** | STL-like parallel algorithms over `device_vector` | convenience layer over CUB; backend-portable |
| **CUB** | device/block/warp primitives (`BlockScan`, `DeviceRadixSort`, `WarpReduce`) | fastest sort/scan/reduce building blocks, tuned per-arch — use when Thrust is too coarse |
| **cuSPARSE** | sparse BLAS (SpMV/SpMM/SpGEMM) | `cusparseLt` for 2:4 structured-sparsity tensor-core GEMM |
| **cuFFT** | batched 1/2/3D FFT | `cuFFTDx` = device-callable FFT you can fuse into a kernel |
| **cuRAND** | RNG (XORWOW, Philox, Sobol) | host + device APIs |
| **NCCL** | multi-GPU/node collectives | see below |

**NCCL** — the backbone of data/tensor-parallel training. The two algorithms to know:

- **Ring** — bandwidth-optimal (each rank forwards a chunk to the next, pipelined; comm volume
  `2(N-1)/N × data`), but latency scales **O(N)** → best for large messages.
- **Double-binary-tree** — **O(log N)** latency at full bandwidth → best for small/medium messages at
  large scale (NCCL 2.4+). **CollNet/NVLS** adds in-network (SHARP) reduction.

NCCL auto-selects by size/topology; `NCCL_ALGO` forces it.

**CUDA Graphs** — capture a DAG of kernels/copies/memsets once (`cudaStreamBeginCapture`), instantiate,
then **replay as one submission**. Per-kernel launch overhead is **~5–10 µs CPU-side** *(order-of-
magnitude, version-dependent)*; for many small kernels this is a large fraction of iteration time.
Graphs cut it to near-constant. Essential for LLM decode / many-small-kernel inference. PyTorch:
`torch.cuda.graph` / `make_graphed_callables`.

**Multi-tenancy and partitioning:**
- **MPS (Multi-Process Service)** — a daemon that funnels multiple processes' kernels into one context
  so they run *concurrently* (no context-switch serialization). Soft sharing; improves utilization for
  many small jobs.
- **MIG (Multi-Instance GPU, A100+)** — hardware partition into up to **7 isolated instances** with
  dedicated SMs/L2/DRAM/copy-engines. True QoS isolation for multi-tenant cloud.
- **Green contexts (CUDA 12.4+)** — carve a set of SMs into a lightweight context (`cuGreenCtxCreate`)
  for fine-grained SM allocation without full MIG isolation.

**Unified memory** — `__managed__` / `cudaMallocManaged`: one pointer valid on host and device, with
page-faulting migration on demand (Pascal+). Tune with `cudaMemPrefetchAsync` (move ahead of use) and
`cudaMemAdvise` (`ReadMostly`, `PreferredLocation`, `AccessedBy`). Grace-Hopper's cache-coherent
NVLink-C2C makes CPU/GPU sharing far cheaper (less migration).

**GPUDirect** — **RDMA**: a NIC DMAs straight to/from GPU memory (low-latency multi-node, bypassing a
host bounce buffer). **Storage (GDS, `cuFile`)**: NVMe/filesystem DMA direct to GPU memory, bypassing
the CPU for data loading. See [Compute Interconnects](@/notes/hardware/interconnects.md) for the RDMA verbs.

---

<a name="5-nvidia-optimization"></a>
## 5. NVIDIA — optimization techniques and profiling

### 5.1 Techniques ranked by typical impact

| Rank | Technique | Mechanism | Typical win |
|---|---|---|---|
| 1 | **Global coalescing** | align warp accesses to 128 B lines | up to N× on memory-bound |
| 2 | **Shared-memory tiling** | stage reused data in SMEM, cut DRAM traffic | 2–10× on GEMM/stencil |
| 3 | **Async pipelining** (cp.async / TMA + multi-stage) | overlap load with compute | hides most DRAM latency |
| 4 | **Occupancy ↔ ILP balance** (Volkov) | fewer threads, more independent work/registers per thread | ~peak ALU at low occupancy |
| 5 | **Bank-conflict removal** (pad / swizzle) | 32 distinct banks/warp | up to 32× on SMEM-bound |
| 6 | **Vectorized loads** (`float4`, `ld.global.v4`) | 128-bit transactions, fewer instrs, better MLP | 1.2–2× mem throughput |
| 7 | **Register-pressure mgmt** (`__launch_bounds__`, `--maxrregcount`) | avoid spills to local memory | prevents a perf cliff |
| 8 | **Persistent kernels + grid-stride loops** | launch = #SMs blocks, loop over data; avoids relaunch | cuts launch overhead |
| 9 | **Warp specialization** | dedicate warps to roles (TMA producer vs wgmma consumer), coordinate via mbarrier | key to Hopper warp-specialized / ping-pong GEMM |
| 10 | **`__restrict__` + `const`** | enable non-aliasing reordering + `__ldg` read-only cache | modest, free |

### 5.2 The Volkov result (the most important non-obvious idea)

Vasily Volkov, *"Better Performance at Lower Occupancy,"* GTC 2010 (and thesis, UC Berkeley
EECS-2016-143, 2016): latency is hidden by **either** thread-level parallelism (occupancy) **or**
instruction-level parallelism (independent work per thread). Computing multiple outputs per thread
(more registers, *lower* occupancy) can beat max-occupancy code. On G80: ~100% peak at 25% occupancy
with no ILP, or **8% occupancy with 3-way ILP**. Occupancy-only models mispredict throughput by up to
**1.7×**. This directly motivates register-blocked GEMM. **Do not fixate on occupancy — profile.**

Little's Law makes it concrete: `in-flight work needed = latency × throughput`. You supply that in-flight
work with threads × ILP; max threads is just one way to get there.

### 5.3 Profiling: nsys first, then ncu

- **Nsight Systems (`nsys`)** — the **macro** timeline across CPU/GPU/streams/NCCL. Finds launch gaps,
  serialization, missing compute/copy overlap. Start here.
- **Nsight Compute (`ncu`)** — the **micro**, per-kernel tool: **roofline** (compute vs memory bound),
  **warp-stall reasons** (Long Scoreboard = waiting on a global load; MIO Throttle; Barrier; Not
  Selected), occupancy, memory chart. This tells you *why* a kernel is slow.

---

<a name="6-amd-rocm-hip"></a>
## 6. AMD — ROCm and HIP

AMD's strategy is **source portability**: make CUDA code recompile with minimal changes.

### 6.1 HIP: the CUDA clone

HIP (Heterogeneous-compute Interface for Portability) is a C++ runtime + kernel language that is a
near-1:1 clone of the CUDA API: `cudaMalloc` → `hipMalloc`; `__global__`, `__shared__`, `blockIdx` all
carry over. **`hipcc`** dispatches on `HIP_PLATFORM`:

- `amd` → **HIP-Clang** (ROCm LLVM) + the **ROCclr** runtime → AMD GPUs. Stack:
  `HIP → ROCclr → HSA runtime → KFD (/dev/kfd) → amdgpu kernel driver`.
- `nvidia` → calls **nvcc**; HIP becomes a **thin header-only shim over CUDA** (macros/inline funcs), so
  there is essentially zero overhead on NVIDIA — it compiles to native CUDA.

**HIPIFY** translates CUDA→HIP:
- **hipify-perl** — regex substitution; no CUDA install needed, tolerates broken code, but shallow.
- **hipify-clang** — Clang-AST based; needs a working CUDA install (must compile the input), far more
  accurate on macros/templates/type resolution.

vs CUDA: HIP deliberately lags CUDA feature-for-feature; newest CUDA features arrive late or never, and
the NVIDIA path is only as good as the underlying CUDA.

### 6.2 Compilation and ISA

ROCm ships a fork of LLVM/Clang with the **AMDGPU backend**:

```
HIP/C++ → Clang → LLVM IR → AMDGPU backend → GCN/RDNA/CDNA ISA → code object (ELF) / .hsaco
```

Inspect with `llvm-objdump --triple=amdgcn`. **gfx targets** are AMD's `sm_XX`:

| gfx | Arch | Product |
|---|---|---|
| gfx906 | GCN5 (Vega) | MI50/60 |
| **gfx90a** | **CDNA2** | **MI200 / MI250** |
| **gfx942** | **CDNA3** | **MI300X / MI300A** |
| gfx950 | CDNA4 | MI355X |
| gfx1100/01/02 | RDNA3 | RX 7900 XTX/XT, W7900 |
| gfx1200/01 | RDNA4 | RX 9000 |

Binaries are **gfx-specific** — you build/ship per-target code objects (fat binaries mitigate, but it's
real friction vs CUDA's PTX-JIT forward-compat).

### 6.3 The wavefront gotcha and the scalar unit

- **Wavefront = 64 threads (GCN/CDNA)** vs **32 (RDNA, "wave32", can also do wave64)**. NVIDIA warps are
  always 32. **Code that assumes a fixed lane count breaks across AMD arches** — query
  `__AMDGCN_WAVEFRONT_SIZE`, never assume.
- **LDS (Local Data Share)** = AMD's shared memory: ~64 KB/CU, 32 banks (confirm exact size per gfx).
- **VGPRs vs SGPRs** — a GCN/CDNA distinctive: a separate **scalar unit**. Values uniform across a
  wavefront (loop bounds, base pointers) belong in **SGPRs**, freeing VGPRs for lane-varying data. This
  scalar/vector split is a real perf lever with **no direct CUDA analogue**. On MI300: up to 256
  VGPRs/thread, up to 104 SGPRs/workgroup.

**GCN vs CDNA vs RDNA:** GCN = original 64-wide compute; **CDNA** = datacenter Instinct fork (keeps
wave64, drops raster hardware, adds MFMA matrix cores + FP64); **RDNA** = consumer/gaming fork (wave32,
WGP work-group processors, ray tracing, RDNA3+ WMMA, weak/no FP64).

---

<a name="7-amd-libraries"></a>
## 7. AMD — libraries, matrix cores, MI300

### 7.1 The library stack (cuBLAS/cuDNN/NCCL analogues)

| ROCm | CUDA equivalent | Notes |
|---|---|---|
| **rocBLAS** | cuBLAS | uses **Tensile** (kernel-gen/autotuner) + hipBLASLt internally |
| **hipBLAS** | — | thin portability wrapper → rocBLAS (AMD) or cuBLAS (NVIDIA) |
| **hipBLASLt** | cuBLASLt | fusion-capable GEMM (epilogues, scaling); preferred high-perf path |
| **MIOpen** | **cuDNN** | DNN primitives; HIP backend uses rocBLAS/hipBLASLt |
| **rocFFT / rocSPARSE** | cuFFT / cuSPARSE | FFT / sparse |
| **RCCL** | **NCCL** | API-compatible collectives |
| **Composable Kernel (CK)** | **CUTLASS** | templated fused GEMM + **fused attention (flash-attn)** |
| rocPRIM/hipCUB, rocThrust | CUB/Thrust | primitives |
| **AITER** | — | AI Tensor Engine: optimized attention (MHA/MLA), a PyTorch attention backend |

*(Library versions as of ROCm ~7.x, mid-2026; many are consolidating into a `rocm-libraries`
super-repo — pin to a specific release when citing.)*

### 7.2 Matrix cores

- **MFMA** (Matrix Fused Multiply-Add) — **CDNA** (gfx90a/942/950). 4 matrix engines/CU. FP8/FP16/BF16/
  FP32 **and FP64 matrix** (a datacenter differentiator RDNA lacks). Instructions like
  `v_mfma_f32_16x16x16_f16`. Operates at wavefront (64-lane) granularity with AMD-specific fragment
  layouts — use the `amd_matrix_instruction_calculator` tool for exact K/M/N/cycles/register layout.
- **WMMA** (Wave Matrix Multiply-Accumulate) — **RDNA3+**. Consumer matrix op, 16×16×16 FP16/BF16
  (RDNA4 adds FP8/sparsity), no FP64.

vs NVIDIA Tensor Cores: same hardware-block-MMA idea, but AMD-specific fragment layouts, programmed via
compiler intrinsics / CK / hipBLASLt rather than a `wmma::` fragment API or `mma.sync` PTX, and CUTLASS
is more mature than CK (though CK has closed the gap for attention).

### 7.3 MI300, chiplets, unified memory

- **MI300A (APU)** — per socket: 6 XCDs (Accelerator Complex Dies) → **228 CDNA3 CUs**, 3 CCDs → **24
  Zen4 cores**, **128 GB HBM3 unified** shared CPU+GPU → true zero-copy (no PCIe transfers), same win as
  Apple UMA but at HBM bandwidth. Caveat *(Chips and Cheese)*: HBM3 + Infinity Cache is high-bandwidth
  but high-latency — great for latency-tolerant GPU, worse for latency-sensitive CPU code.
- **MI300X** — pure GPU: 8 XCDs, **192 GB HBM3, ~5.3 TB/s**.
- XCD chiplets over **Infinity Fabric**; multi-socket over **xGMI** (see
  [Interconnects](@/notes/hardware/interconnects.md)).
- **hipGraph** — CUDA-Graphs equivalent (capture/replay DAG, low launch overhead).

### 7.4 Expert tricks and profiling

- **Occupancy** = min over (VGPRs, SGPRs, LDS, workgroup slots).
- **LDS bank conflicts** — 32 banks; fix with **XOR pre-swizzling** (conflict-free, zero extra LDS) or
  padding. RGP exposes an LDS-bank-conflict counter.
- **`s_waitcnt` manual sync** — AMD has **no automatic dependency tracking** for memory ops; the
  compiler inserts `s_waitcnt vmcnt/lgkmcnt/expcnt` to stall until outstanding vector-memory / LDS+
  scalar / export counts drain. Structuring code so the compiler emits fewer (or hand-tuning them in
  ISA) is a real lever.
- **GCN memory clause** — group memory instructions so hardware issues them back-to-back and overlaps
  latency, rather than interleaving with ALU that forces early waits.
- **Profiling** — `rocprof`/`rocprofv3` (counters/traces), **Omniperf** (now "ROCm Compute Profiler":
  roofline, per-kernel bottleneck), Omnitrace (system trace), RGP (Radeon GPU Profiler).

### 7.5 Limitations

Consumer-GPU support is second-class (datacenter MI-series is the tested path; RDNA support is secondary
and shifts release-to-release); **ROCm ML on Windows is largely unsupported** (use WSL2/Linux); driver
`amdgpu`+ROCm version coupling and DKMS pain on Linux; gfx binary fragmentation; ecosystem breadth still
trails CUDA (closing fast on datacenter via upstream PyTorch/vLLM/flash-attention).

---

<a name="8-apple-metal"></a>
## 8. Apple — Metal compute

Apple has no CUDA, no cross-vendor path — **Metal is the only low-level GPU API on macOS/iOS**, and its
defining trait is the unified memory architecture.

### 8.1 Object model and MSL

`MTLDevice` (the GPU) → `MTLCommandQueue` (≈ a CUDA stream) → `MTLCommandBuffer` (a batch) →
`MTLComputeCommandEncoder` (encodes dispatches); `MTLComputePipelineState` = a compiled kernel + config
(≈ a CUDA module+function). Dispatch via `dispatchThreadgroups` or `dispatchThreads`.

**MSL (Metal Shading Language)** is **C++14-based** (closer to modern CUDA C++ than to C). Kernels are
`kernel void`; indexing via attributes:

```metal
kernel void saxpy(device const float* x   [[buffer(0)]],
                  device       float* y   [[buffer(1)]],
                  constant     float& a   [[buffer(2)]],
                  uint gid [[thread_position_in_grid]]) {  // ≈ blockIdx*blockDim + threadIdx
    y[gid] = a * x[gid] + y[gid];
}
```

Execution hierarchy: **threadgroup** ≈ block; **SIMD-group = 32 threads** (lockstep, confirmed 32-wide
across current Apple Silicon); **threadgroup memory** ≈ shared/LDS with
`threadgroup_barrier(mem_flags::mem_threadgroup)`.

### 8.2 Unified Memory Architecture (UMA) — the headline

CPU, GPU, and Neural Engine share **one physical LPDDR5X pool** over a wide on-die bus. **No discrete
VRAM, no PCIe copies** — weights aren't duplicated or transferred, so a 512 GB M3 Ultra can hold models
an 80 GB H100 cannot. `MTLStorageMode`:

- **`.shared`** — CPU+GPU coherent, **zero-copy** on Apple Silicon (the common case).
- **`.private`** — GPU-only, faster (no CPU coherence bookkeeping), needs a blit to populate.
- **`.managed`** — legacy Intel-Mac / discrete-GPU mode (explicit `didModifyRange`); irrelevant on
  Apple Silicon.
- **`.memoryless`** — tile-only transient (TBDR).

Unified bandwidth (shared CPU+GPU): **M3 Max 400 GB/s, M4 Max 546 GB/s, M3 Ultra 819 GB/s** (M2 Ultra
800). *(No M4 Ultra existed as of this research; M5 generation emerging — re-verify.)* LLM inference here
is bandwidth-bound (weight loading), so these numbers cap tok/s.

### 8.3 TBDR implications for compute

Apple GPUs are **Tile-Based Deferred Renderers**: the screen is split into tiles held in fast on-chip
tile memory, slashing DRAM bandwidth. Compute-relevant features:

- **Imageblocks** — structured 2D data in tile memory, accessible from kernel functions, single-op
  load/store.
- **Tile shaders** — fuse render + compute in one pass sharing imageblock/threadgroup memory (e.g.
  per-tile depth min/max → light culling → shading, all on-chip).
- Takeaway: the architecture rewards keeping working sets in tile/threadgroup memory and avoiding
  round-trips to unified DRAM.

### 8.4 SIMD-group functions (warp-intrinsic analogues)

`simd_shuffle`, `simd_shuffle_xor`, `simd_sum`, `simd_prefix_exclusive_sum`, `simd_broadcast`,
`simd_ballot` — reductions/scans/exchanges across the 32 lanes **without threadgroup memory or
barriers**, exactly like CUDA `__shfl_sync`.

---

<a name="9-apple-mlx"></a>
## 9. Apple — ML stack and MLX

### 9.1 The acceleration blocks (and which you can actually reach)

- **MPS / MPSGraph** — Metal Performance Shaders: hand-tuned kernels (GEMM, conv, RNN) ≈ a cuDNN+cuBLAS
  bundle. **MPSGraph** builds/optimizes/executes op DAGs and can run Core ML models. This is what
  PyTorch-MPS and many frameworks call under the hood.
- **simdgroup_matrix** — MSL's on-GPU "tensor core": cooperative 8×8 (and tiled larger) matrix multiply
  across a 32-lane SIMD-group, cutting register pressure in FP16/FP32 GEMM. **Metal 4 / M5 "neural
  accelerators"** expose new TensorOps / Metal Performance Primitives. *(Exact tile sizes/throughput per
  generation are partly reverse-engineered — Rigel arXiv 2606.12765, philipturner/metal-benchmarks —
  not fully Apple-documented.)*
- **AMX** — an **undocumented CPU-side matrix coprocessor** (one block per CPU cluster), reachable **only
  via Apple's Accelerate** (BLAS/vDSP), **not from Metal**. Specs are third-party reverse-engineered.
- **ANE (Apple Neural Engine)** — the dedicated, extremely power-efficient NPU, reachable **only through
  Core ML** — **not from Metal or MLX**. A hard ecosystem wall (Orion arXiv 2603.06728 reverse-engineers
  it for LLMs).

### 9.2 MLX

Apple's open-source NumPy/JAX-like array framework:

- **Unified-memory native** — arrays live in the shared pool; ops dispatch to CPU or GPU with **no data
  movement**. Unlike PyTorch's `.to(device)` model, an `mx.array` isn't tied to a device.
- **Lazy evaluation** — ops build a graph, materialized only at `mx.eval()`, enabling **kernel fusion**
  and reduced launch overhead (important given Metal's dispatch cost). Over-calling `mx.eval()` breaks
  fusion and adds sync points.
- **JAX-style transforms** — `grad`, `vmap`, compile. Maps to Metal kernels (Metal 4 → TensorOps).
- **mlx-lm** — LLM library on top: optimized inference, speculative decoding, KV-cache management,
  LoRA fine-tuning. The practical local-LLM entry point on Mac, alongside llama.cpp's Metal backend.

### 9.3 Ecosystem and limits

- **llama.cpp Metal backend** — mature custom-MSL quantized GEMV/attention; often the fastest GGUF path.
- **PyTorch MPS** — functional but gappy: `PYTORCH_ENABLE_MPS_FALLBACK=1` silently runs unsupported ops
  on **CPU** (a perf cliff that hides bugs); missing/partial ops (some complex ops); **FP8 tensors exist
  but can't compute**; **no FP64**; no CUDA-graph parity.
- **Limitations**: macOS/iOS only; **no hardware FP64** (MPS rejects float64; emulation is very slow —
  disqualifies much HPC); younger ecosystem; ANE closed; single-vendor.

---

<a name="10-portability"></a>
## 10. Cross-vendor portability: SYCL, Vulkan, wgpu

### 10.1 SYCL / oneAPI — single-source C++

SYCL 2020 (Khronos) is **single-source C++17**: host + device in one `.cpp`, a kernel is a lambda in
`queue.submit(...).parallel_for(...)`. Two memory models:

- **Buffer/accessor** — wrap data in `buffer`, request `accessor`s; the runtime builds a task DAG from
  accessor dependencies and schedules automatically (safer, implicit sync).
- **USM (Unified Shared Memory)** — raw pointers (`malloc_device` / `malloc_shared` / `malloc_host`),
  CUDA-like explicit `memcpy` + `depends_on` (easier CUDA porting; `malloc_shared` = page-migrated).

Execution: `nd_range` = global + local range → **work-groups** (≈ blocks) → **sub-groups** (≈ warps,
32/64 lanes, with shuffles/reductions).

Two implementations matter:

- **DPC++** (Intel's LLVM compiler, the oneAPI reference) — backends via a plugin layer: **Level Zero**
  (Intel GPU), **CUDA** (emits PTX), **HIP** (amdgcn), OpenCL, CPU. Ships **oneMKL** (BLAS/FFT/RNG),
  **oneDNN**, oneDPL; **SYCLomatic/`dpct`** migrates CUDA→SYCL (~80–90% automatic).
- **AdaptiveCpp** (formerly hipSYCL/OpenSYCL) — community/independent. Headline: a **single-pass SSCP
  compiler** (IWOCL 2023) that parses SYCL *once* into device-independent LLVM IR, then JIT-lowers to
  PTX/amdgcn/SPIR-V at runtime (vs DPC++'s multi-pass per-target). Because its CUDA/HIP path augments
  clang's CUDA/HIP frontend, you can **mix SYCL and CUDA/HIP in one source file**.

SYCL doesn't fuse across `parallel_for` by default; fusion comes from DPC++'s experimental kernel-fusion
extension or writing fused kernels by hand (oneDNN does graph-level fusion).

**Perf vs native CUDA**: recent studies put DPC++ within **~3%** of native on Polybench-class GPU
workloads *(edge study, J. Supercomputing 2024; workload-specific — not universal)*. **Limitations**:
trails on bleeding-edge features (newest tensor-core shapes, TMA/cluster launch, warp specialization),
smaller tuned-library surface than cuBLAS/CUTLASS/cuDNN, extra abstraction. Used by DOE Aurora,
ROOT/RDataFrame. Best for portable C++ HPC.

### 10.2 Vulkan compute — maximum reach, maximum boilerplate

Low-level explicit API. Compute shaders in GLSL/HLSL/Slang → **SPIR-V** (offline). Pieces:

- **VkPipeline** (compute) wraps one entry point + layout.
- **Descriptor sets** bind resources (verbose; `VK_EXT_descriptor_indexing` / descriptor buffers ease
  it); **push constants** = tiny (~128–256 B) inline uniforms, the fastest param path.
- **Buffer device address** (`VK_KHR_buffer_device_address`) — raw 64-bit GPU pointers → bindless.
- **Subgroups** (`VK_KHR_shader_subgroup*`) — warp-level ballot/shuffle/arithmetic.
- **Cooperative matrix** (`VK_KHR_cooperative_matrix`, 2023) — the **portable path to tensor cores
  outside CUDA**. Subgroup-scoped tiles are small, so naive GEMM underperforms; you must hand-tile
  through shared memory. `VK_NV_cooperative_matrix2` (Oct 2024, NVIDIA-only) adds workgroup-scope
  matrices to fix this.
- **Explicit sync** — pipeline barriers, events, semaphores (incl. timeline), fences. You own all hazard
  tracking.
- Cross-vendor: NVIDIA/AMD/Intel native; **Apple via MoltenVK** (Vulkan→Metal translation, subset).

Pros: runs on essentially every modern GPU + Apple; explicit → predictable; tensor cores via coopmat.
Cons: extreme boilerplate; manual sync/memory/descriptors; no single-source. Used by llama.cpp's Vulkan
backend, ncnn.

### 10.3 wgpu / WebGPU — write once, including the browser

The Rust `wgpu` crate implements the **WebGPU** standard; shaders in **WGSL** (also SPIR-V/GLSL). One
API over **Vulkan, Metal, DX12, GL, and browser WebGPU + WASM**. Compute via pipelines + **bind groups**
(WebGPU's descriptor-set analog). Safer/higher-level than raw Vulkan, auto sync.

**Portability tax**: WebGPU is a lowest-common-denominator API — **no tensor-core / cooperative-matrix
access yet** (subgroup/coopmat extensions in progress — verify current status), browser resource limits,
no arbitrary pointers, WGSL less expressive than CUDA/HLSL. Expect a real gap vs native for tensor-core
GEMM. ML use: Burn's `burn-wgpu`, wonnx (ONNX on wgpu), WebLLM (in-browser LLMs).

### 10.4 SPIR-V and the rest, briefly

- **SPIR-V** — Khronos binary IR; consumed by Vulkan (all shaders), OpenCL 2.1+, SYCL (Level Zero/
  OpenCL backends), rust-gpu, AdaptiveCpp. **SPIRV-Cross** transpiles SPIR-V → GLSL/HLSL/MSL (how
  MoltenVK/wgpu retarget Metal/DX). Two dialects (Vulkan "Logical" vs OpenCL "Physical" pointer models)
  aren't fully interchangeable.
- **OpenCL** — the original portable GPGPU (2009); faded due to verbose host API, separate-source string
  kernels, weak vendor optimizers, and NVIDIA deprioritizing it for CUDA. Alive as a *backend* (POCL,
  rusticl) and SPIR-V consumer.
- **Kokkos / RAJA / Alpaka** — C++ template perf-portability layers (parallel_for + memory/execution
  spaces) backing onto CUDA/HIP/SYCL/OpenMP. Kokkos & RAJA are DOE Exascale Computing Project; best for
  scientific HPC needing one source across Frontier (AMD) / Aurora (Intel) / Perlmutter (NVIDIA).
- **OpenMP target offload** — `#pragma omp target teams distribute parallel for`; lowest porting effort,
  least control. **NVHPC stdpar** — `std::for_each(std::execution::par_unseq, …)` auto-offloaded by
  nvc++ (uses managed memory); zero GPU-specific code, limited to STL-algorithm shapes.

---

<a name="11-rust-gpu"></a>
## 11. Rust on the GPU

Three real paths, plus binding crates.

- **Rust-CUDA** — `rustc_codegen_nvvm` is a rustc backend emitting NVVM IR → **PTX** (compile Rust
  *kernels* for NVIDIA); host side uses **`cust`** to load PTX and launch via the driver API. Dormant
  for ~3 years, **revived 2024–25** with CUDA 12.x support and context hardening; requires a **pinned
  nightly** (`rust-toolchain.toml`, uses rustc internals). *(Exact nightly drifts — check the repo.)*
- **rust-gpu** — compiles Rust → **SPIR-V** for Vulkan (graphics + compute); uses **SPIR-T** for
  optimization. Moved to the community Rust-GPU org (2024); ~90% of a real shader repo has been ported,
  each compiling to valid SPIR-V droppable into the original pipeline. Best for Vulkan compute in Rust.
- **CubeCL** (Burn team) — a multi-backend Rust **kernel language** targeting **CUDA, ROCm/HIP, wgpu
  (Vulkan/Metal/DX/WebGPU), Metal**. Two standout features: **comptime** (modify compiler IR at first
  compile — instruction specialization, loop unrolling, shape specialization) and **runtime
  autotuning** (micro-benchmark to pick the best config per hardware). Powers Burn's backends; its
  matmul engine **reportedly rivals cuBLAS/CUTLASS** across backends *(project's own claim — no
  independent third-party parity benchmark seen; verify)*.

**Binding/runtime crates**: `cudarc` (safe CUDA + cuBLAS/cuDNN/NCCL, used by candle), `candle`
(HuggingFace tensor lib, CUDA+Metal+CPU), `burn` (full DL framework, CubeCL-powered), `ash` (Vulkan),
`metal-rs` (Metal), `wgpu`, `ocl`.

**Why Rust-on-GPU is hard**: kernels are **no_std / no-alloc** (no heap, restricted panic); **ownership
and lifetimes don't map to GPU memory** (address spaces, manual barriers, SIMT divergence sit outside
Rust's aliasing model — the borrow checker doesn't cover cross-thread GPU races); SPIR-V logical
addressing forbids arbitrary pointer casts Rust code assumes; and tooling/maturity (pinned nightlies,
thin debuggers) trails CUDA C++. Verdict: **CubeCL** most production-ready and portable, **rust-gpu** for
Vulkan, **Rust-CUDA** for NVIDIA-native PTX.

---

<a name="12-python-gpu"></a>
## 12. Python on the GPU

The dominant on-ramp, and increasingly where custom kernels are written too ("write Python, get PTX").

| Tool | What it is | Model | When |
|---|---|---|---|
| **CuPy** | NumPy/SciPy-compatible GPU arrays; wraps cuBLAS/cuDNN/cuFFT; custom `RawKernel`/`ElementwiseKernel` | drop-in ndarray | fastest path to move NumPy code to GPU |
| **Numba CUDA** | `@cuda.jit` JITs a Python subset → PTX (LLVM) | explicit threads/blocks in Python | prototyping kernels without C++ |
| **PyCUDA** | thin driver-API bindings; compile CUDA-C strings at runtime | manual | legacy/fine control; superseded by cuda.core |
| **cuda-python / `cuda.core`** | official driver+runtime+NVRTC bindings; `cuda.core` = ergonomic layer | low-level, canonical | the sanctioned base others build on |
| **Triton (OpenAI)** | Python **tile** DSL, JIT via MLIR → PTX; `tl.dot` → tensor cores; auto coalescing/SMEM/pipelining; `@triton.autotune` | block-level, not thread-level | dominant for custom fused kernels; near-CUTLASS, far more productive |
| **torch.compile / TorchInductor** | PyTorch 2 graph compiler; **generates Triton** for GPU (default since PT 2.0) | automatic from eager code | zero-effort fusion for model code |
| **JAX + XLA** | functional array API; XLA fuses to GPU (+ **Pallas** for custom tile kernels) | whole-program `jit`/`vmap`/`pmap` | research; XLA fusion is the perf engine |
| **NVIDIA Warp** | Python kernel JIT for simulation/graphics (differentiable physics) | `@wp.kernel`, thread-per-element | robotics/sim, not general DL |
| **MLX** | Apple's array framework | unified-memory, lazy | local ML on Apple Silicon (§9) |

Rule of thumb: array-level → CuPy/JAX; productive custom fused kernel → **Triton**; last-10% peak →
CUTLASS/CuTe in C++; full driver control → cuda.core.

---

<a name="13-c-cpp"></a>
## 13. C / C++ across vendors

C++ is the canonical low-level GPU target on every vendor — everything above (Python, Rust, portability
layers) ultimately emits or wraps a C++-shaped kernel. The fundamental split is **how host and device
code relate**:

- **Single-source** (CUDA C++, HIP C++, SYCL): host and device in one translation unit, one compiler
  driver handling both. Ergonomic — you write a kernel next to its launch, share types and templates.
  This is why CUDA/HIP/SYCL feel productive.
- **Separate-shader** (Vulkan, Metal): device code is a *separate* shader language (GLSL/HLSL/WGSL/MSL)
  compiled to an IR (SPIR-V / AIR) that the host C/C++ program loads at runtime. More boilerplate
  (descriptor sets, pipeline objects, explicit sync), but decoupled — the same host code drives any
  shader, and shaders ship as portable IR.

Practical guidance for C/C++: for NVIDIA-only, CUDA C++ with CUTLASS/CuTe is the peak-perf path; for
AMD+NVIDIA source portability, HIP; for vendor-neutral single-source, SYCL (DPC++/AdaptiveCpp); for
ship-to-literally-everything including Apple and the browser, Vulkan (via MoltenVK) or wgpu, accepting
the boilerplate and portability tax.

---

<a name="14-optimization-theory"></a>
## 14. Cross-cutting optimization theory (vendor-neutral)

These principles hold regardless of vendor; the per-vendor sections above are applications of them.

**1. Roofline** (Williams, Waterman, Patterson, *CACM* 2009). `attainable FLOP/s = min(peak compute,
arithmetic_intensity × peak bandwidth)`, where **AI = FLOPs / bytes moved**. Left of the ridge point =
**memory-bound**; right = **compute-bound**. E.g. an A100 (~19.5 TF32 TFLOP/s, ~1.5–2 TB/s HBM) has a
ridge at ~10 FLOP/byte: GEMV/elementwise (AI<1) are memory-bound; large GEMM (AI in the hundreds) is
compute-bound. The math is detailed in
[GPU/TPU chip-design §18](@/notes/hardware/gpu_tpu_accelerator_design.md) — this doc uses it as the lens for
deciding whether to attack bandwidth or compute.

**2. Occupancy + latency hiding via Little's Law.** GPUs hide latency with parallelism, not caches.
`in-flight work = latency × throughput`. Supply it with threads × ILP — **not necessarily max threads**
(§5.2, Volkov: ILP hides latency at low occupancy; occupancy-only models err up to 1.7×).

**3. Kernel / operator fusion.** Merge ops to avoid round-tripping intermediates through global memory.
Elementwise chains and softmax/layernorm/attention are memory-bound → fusion cuts DRAM traffic, often
the dominant win. **FlashAttention** is the canonical example (fuses attention, tiles in SRAM). Enabled
by Triton, torch.compile, XLA, TVM, oneDNN, CK, MLX.

**4. Tiling / register blocking.** Stage tiles of operands into on-chip scratchpad (SMEM/LDS/threadgroup,
~100× faster than global), reuse across a block; hold sub-tiles in **registers** for the innermost
accumulate. This is how GEMM reaches peak — raise arithmetic intensity by maximizing on-chip reuse.
Tensor-core / cooperative-matrix GEMM *still* needs an outer shared-memory tiling loop.

**5. Autotuning + JIT specialization.** **Triton** (`@triton.autotune` sweeps block sizes / num_warps /
num_stages), **TVM + Ansor** (auto-schedule search), Kernl. JIT specializes on shapes/dtypes at first
call. The trend is away from one hand-written kernel toward a *search* over a parameterized space.

**6. Precision / quantization → throughput.** Lower precision gives roughly linear tensor-core throughput
gains and halved/quartered bandwidth+footprint. Rough tensor-core scaling: **FP16/BF16 ≈ 2× FP32(TF32);
INT8/FP8 ≈ 2× FP16; FP4 (Blackwell) higher still** *(approximate, arch-family-dependent — pull the
specific datasheet)*. BF16 keeps FP32's exponent range (training-friendly); FP8 (E4M3/E5M2) and INT8 for
inference with calibration/QAT. You trade accuracy for throughput + memory.

**7. Host↔device transfer.** **PCIe Gen4 x16 ≈ 32 GB/s, Gen5 ≈ 64 GB/s** — vs HBM ~1.5–3+ TB/s and
NVLink 4/5 ~450–900+ GB/s. Mitigate with **pinned (page-locked) host memory** (enables true DMA, ~2×
faster, async-capable), **overlap copy with compute via multiple streams / copy engines** (double-buffer
H2D/compute/D2H), and keeping data resident on-device. See
[PCIe Internals](@/notes/hardware/pcie_internals.md) and [Interconnects](@/notes/hardware/interconnects.md).

---

<a name="15-matrix"></a>
## 15. Limitations and advantages matrix

### 15.1 Vendor comparison

| Axis | NVIDIA CUDA | AMD ROCm/HIP | Apple Metal |
|---|---|---|---|
| Ecosystem maturity | ★★★★★ (~15 yr) | ★★★☆ (datacenter fast-improving) | ★★★ (young, growing) |
| Portability | NVIDIA-only | source-portable (HIP→NVIDIA too) | Apple-only |
| Peak perf ceiling | highest (wgmma/tcgen05) | high (MFMA, MI300) | mid (simdgroup_matrix) |
| OS | Linux-first, Windows | Linux-first (Windows ML weak) | macOS/iOS only |
| Language on-ramps | richest (C++/Rust/Python) | HIP C++, Python (rising) | MSL/Swift, MLX/Python |
| Openness | closed SASS/ptxas/libs (CUTLASS/CUB/NCCL open) | **open source** | closed (ANE fully closed) |
| FP64 | yes | yes (CDNA) / weak (RDNA) | **no** |
| Memory model | discrete + optional UM | discrete / **MI300A UMA-HBM** | **UMA zero-copy** |
| Killer feature | Tensor Cores + tooling (Nsight) | open + MI300 UMA-HBM + FP64 matrix | huge cheap unified memory for local LLMs |

### 15.2 Portability-layer decision matrix

| Layer | Cross-vendor | Tensor cores | Single-source | Perf vs native | Boilerplate | Best for |
|---|---|---|---|---|---|---|
| SYCL/DPC++ | NV/AMD/Intel/CPU | yes (backend) | yes (C++) | ~97% | low | portable C++ HPC / oneAPI |
| AdaptiveCpp | NV/AMD/Intel/CPU | yes | yes (single-pass) | ~90–97% | low | vendor-independent SYCL |
| Vulkan compute | all GPUs + Apple (MoltenVK) | yes (coopmat) | no | high (manual) | very high | ship-everywhere / max control |
| wgpu/WebGPU | all + browser/WASM | not yet | yes (WGSL) | portability tax | low | browser / cross-platform + safety |
| Kokkos/RAJA | via CUDA/HIP/SYCL | via backend | yes (C++) | ~native | medium | DOE/exascale scientific C++ |
| OpenCL | all (fading) | limited | no | varies | high | legacy/embedded, SPIR-V backend |

### 15.3 Rust-on-GPU decision matrix

| Project | Targets | Authoring | Maturity | Best for |
|---|---|---|---|---|
| CubeCL | CUDA/ROCm/wgpu/Metal | Rust + comptime + autotune | rising fast | portable high-perf Rust kernels |
| rust-gpu | SPIR-V → Vulkan | Rust → SPIR-V | community, active | Vulkan compute in Rust |
| Rust-CUDA | NVIDIA PTX | Rust → NVVM | revived, nightly-pinned | NVIDIA-native Rust |
| candle / burn | CUDA/Metal/wgpu/CPU | framework tensors | production | ML inference/training in Rust |

### 15.4 "If you need X, use Y" cheat-sheet

- **Absolute peak on NVIDIA** → CUDA C++ + CUTLASS/CuTe (or cuBLASLt/cuDNN if a library covers it).
- **Productive custom fused kernels** → Triton (Python) — near-peak, far less effort.
- **Run the same source on NVIDIA and AMD** → HIP (source-portable) or SYCL (single-source, +Intel).
- **Vendor-neutral C++ with near-native perf** → SYCL (DPC++/AdaptiveCpp).
- **Ship to literally every GPU incl. Apple + browser** → Vulkan (via MoltenVK) or wgpu/WebGPU.
- **Local LLM inference on a Mac** → MLX / mlx-lm or llama.cpp Metal.
- **Rust, multi-backend** → CubeCL; Rust for Vulkan compute → rust-gpu; Rust NVIDIA-native → Rust-CUDA.
- **FP64-heavy HPC** → NVIDIA or AMD CDNA (never Apple).
- **Kill many-small-kernel launch overhead** → CUDA Graphs / hipGraph / MLX lazy graph.

---

<a name="16-references"></a>
## 16. Key references

**Foundational theory**
- Williams, Waterman, Patterson. *Roofline: An Insightful Visual Performance Model for Multicore
  Architectures.* CACM 2009.
- Volkov. *Better Performance at Lower Occupancy.* GTC 2010. And thesis, UC Berkeley EECS-2016-143, 2016.
- Nickolls, Buck, Garland, Skadron. *Scalable Parallel Programming with CUDA.* ACM Queue 2008. *[Classic]*
- Tillet, Kung, Cox. *Triton: An IR and Compiler for Tiled Neural-Network Computations.* MAPL 2019.
- Kirk & Hwu. *Programming Massively Parallel Processors*, 4th ed. *[Textbook]*

**NVIDIA architecture / programming**
- NVIDIA. *Hopper Architecture In-Depth* (clusters, DSMEM, TMA) — developer blog.
- Colfax Research tutorials: WGMMA on Hopper; TMA; Blackwell tensor-memory GEMM (tcgen05).
- CUTLASS docs + CuTe layout algebra.
- Luo et al. *Dissecting the NVIDIA Hopper Architecture through Microbenchmarking.* arXiv 2501.12084 (2025).
- *Microbenchmarking NVIDIA's Blackwell Architecture.* arXiv 2512.02189 (2025).
- NVIDIA. *Massively Scale Deep Learning Training with NCCL 2.4* (double-binary-tree).
- NVIDIA / PyTorch. *Accelerating PyTorch with CUDA Graphs*; *CUTLASS Ping-Pong GEMM* (warp specialization).

**AMD ROCm**
- AMD ROCm docs: HIP compilers; AMDGPU backend (LLVM); HIPIFY; MI300 microarchitecture; MI300A APU.
- AMD. *amd_matrix_instruction_calculator* (MFMA layouts).
- Chips and Cheese. *Inside the AMD Radeon Instinct MI300A.*
- *Inter-APU Infinity Fabric analysis.* arXiv 2508.11298 (2025).

**Apple Metal**
- Apple developer: Metal overview; Tile shading (Metal 2); MLX; PyTorch on Metal; WWDC25 MLX session.
- *Rigel: Reverse-Engineering the Metal 4.1 Tensor Compute Path on the M4 Max.* arXiv 2606.12765 (2026).
- *Orion: reverse-engineering the Apple Neural Engine for LLMs.* arXiv 2603.06728 (2026).
- philipturner/metal-benchmarks.

**Portability / Rust**
- Khronos SYCL 2020 spec. AdaptiveCpp single-pass SSCP (IWOCL 2023).
- `VK_KHR_cooperative_matrix` proposal; Bolz, *Cooperative Matrix* (Vulkanised 2025).
- Rust-GPU project blogs (Rust-CUDA update 2025; "Rust on every GPU" 2025); CubeCL / Burn cross-platform
  backend.
- Kokkos/RAJA/Alpaka evaluation. arXiv 2402.08950 (2024).

*See [Papers Index](@/notes/reference/papers.md) for venue/year cross-references and
[Glossary](@/notes/reference/glossary.md) for term definitions.*
