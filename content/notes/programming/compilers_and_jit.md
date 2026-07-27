+++
title = "Compilers And JIT"
date = 2026-05-20
[taxonomies]
tags = ["compilers", "jit", "optimization", "codegen", "llvm", "ir"]
+++


# Compilers and JIT Compilation: Expert Reference

Expert-level reference covering compiler architecture, optimization theory, JIT compilation, high-performance backends, and the state of the art through 2026. Sufficient depth to implement a production compiler or JIT.

---

## 1. Compiler Architecture & Pipeline

### Three-Phase Design

```
Source Code
    │
    ▼
┌─────────────┐
│   Frontend  │  Lexing, parsing, semantic analysis, type checking
│             │  → HIR (high-level IR, close to source)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Optimizer  │  Platform-independent optimizations on IR
│             │  HIR → MIR → multiple optimization passes
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Backend   │  Instruction selection, register allocation,
│             │  scheduling → machine code / object files
└─────────────┘
```

Lattner & Adve (CGO 2004) codified this three-phase design for LLVM. The key insight: the IR is a first-class language, enabling a shared optimizer to serve many frontends and backends. LLVM IR exists in three isomorphic forms: in-memory (`Value*` graph), textual (`.ll`), and binary bitcode (`.bc`).

### Multi-Tier IR

Most production compilers use multiple IR levels, progressively lowering toward machine semantics:

- **HIR** (high-level): preserves source-level structure — loops, expressions, named variables, type information. GCC uses GENERIC/GIMPLE, Rust uses HIR/THIR/MIR, Swift uses SIL (Swift Intermediate Language), HotSpot C2 uses its own typed HIR.
- **MIR** (mid-level): SSA-based, control-flow graph over basic blocks. LLVM IR, GCC GIMPLE, Rust MIR. Most analyses and optimizations live here.
- **LIR** (low-level / machine IR): virtual registers, platform-specific operations, no SSA. LLVM's MachineIR, GCC RTL. Pre-regalloc → post-regalloc transformations happen here.

### Control Flow Graph (CFG)

A CFG is a directed graph where nodes are **basic blocks** (maximal straight-line sequences — single entry, single exit except for final terminator) and edges are branches. Properties:
- Entry block, exit block(s)
- Predecessor/successor relationships
- Each block ends with a **terminator**: branch, switch, return, unreachable

**Dominators**: Block A *dominates* B if every path from entry to B passes through A. The **dominator tree** is the tree of immediate dominators — critical for SSA construction and many optimizations. Computed efficiently via Lengauer-Tarjan O(n α(n)) or Cooper et al. simple iterative O(n²) in practice.

**Post-dominators**: reverse direction. Used for control-dependence analysis.

**Natural loops**: A back-edge is an edge from B → A where A dominates B. The natural loop of a back-edge is all nodes that can reach B without going through A (plus A). The loop header (A) is where all paths from outside must enter.

### SSA Form

Static Single Assignment (SSA) ensures each variable is defined exactly once. Benefits: enables efficient dataflow analysis, many optimizations become linear-time.

Construction (Cytron et al. 1991, TOPLAS):
1. Compute dominance frontiers: DF(x) = {y | ∃ predecessor p of y s.t. x dominates p but x does not strictly dominate y}
2. Insert φ-functions at dominance frontiers for each variable definition
3. Rename variables using DFS over dominator tree

Braun et al. (CC 2013) "Simple and Efficient Construction of SSA Form" — online algorithm that builds SSA during IR construction without a separate pass, by maintaining current definition map per block with lazy φ-insertion. Used in Go compiler, libFirm, Cranelift.

**Phi nodes** (`φ(v1, v2, ...)`) select a value from predecessors based on which predecessor was executed. Example:

```
if (cond) { x = 1; } else { x = 2; }
// use x

; In SSA:
entry:
    br cond, if_true, if_false
if_true:
    x1 = 1
    br merge
if_false:
    x2 = 2
    br merge
merge:
    x3 = φ(x1, x2)   ; φ from if_true → x1, if_false → x2
```

**SSA destruction** (phi elimination) for code generation: insert parallel copies along edges, then sequentialize (break swap cycles with temporaries). Sreedhar et al. 1999 — SSA elimination algorithm handling critical edges.

**Typed vs. untyped IRs**: LLVM IR is typed (every `Value` has a `Type`). LLVM 15+ introduced **opaque pointers** (`ptr` instead of `i32*`) — reduces pointer type casts, simplifies IR, matches how modern CPUs work.

---

## 2. Core Optimization Passes

### Dead Code Elimination (DCE)
Mark all reachable side-effecting instructions; sweep unreachable. SSA makes DCE trivial: an instruction is dead if its result has no uses (`use_empty()`). Aggressive DCE (ADCE) inverts: assume all dead, mark live instructions backward from side effects.

### Constant Folding / Propagation
Evaluate constant expressions at compile time. **IEEE 754 hazards**:
- `NaN` propagation: `NaN + 0 == NaN` but `NaN == NaN` is false
- Signed zero: `0.0 + (-0.0) == 0.0` (not `-0.0`)
- Rounding mode sensitivity: folding `a + b + c` ≠ `(a + b) + c` in general
- **fast-math** flags (`nsz`, `nnan`, `ninf`, `reassoc`) explicitly relax these constraints in LLVM

### Global Value Numbering (GVN)
Assigns the same value number to computations that always produce the same result. **Hash-based GVN** (Click 1995, PLDI): expressions hashed by opcode + operand value numbers. **LLVM NewGVN** uses Rosen/Wegman/Zadeck 1988 sparse formulation extended with MemorySSA for loads. Eliminates redundant computations across the entire function.

### LICM (Loop-Invariant Code Motion)
Hoist computations whose operands don't change inside a loop to the pre-header. Requires alias analysis to hoist loads safely.

### Loop Transformations
- **Unrolling**: replicate loop body N times, reduce branch overhead, expose instruction-level parallelism. Requires trip count or remainder loop.
- **Vectorization**: auto-vectorize by identifying SIMD-parallelizable loops. LLVM's LoopVectorize pass uses **dependence analysis** (direction vectors, distance vectors from Banerjee test, Omega test for exact dependence). **Polyhedral model** (Feautrier 1991, Bondhugula PLDI 2008 — Pluto algorithm): represent loop nests as integer polyhedra, find affine transformations maximizing parallelism and locality.
- **Interchange**, **tiling** (cache blocking), **fusion**, **fission**: classic loop transformations; fully automated in polyhedral frameworks (Polly LLVM extension, MLIR affine dialect).

### Function Inlining
Replace a call site with the callee's body. Critical for performance: eliminates call overhead, enables cross-procedure optimization.

**Cost model**: LLVM inliner assigns cost based on IR instruction count weighted by: branch factor, call penalties, argument simplification bonuses (if constant arg eliminates branches). Threshold varies by optimization level and hotness. **Call graph SCC order**: process SCCs bottom-up to inline callees before callers (avoids exponential blowup on mutual recursion).

**Devirtualization**: replace virtual dispatch with direct call when receiver type is known. Requires Class Hierarchy Analysis (CHA) or exact type propagation. **Speculative devirtualization** (HotSpot C2): inline most common target, guard with type check + deopt on mismatch.

### Alias Analysis
Determines whether two memory accesses may refer to the same location.

- **Andersen's analysis** (1994): flow-insensitive, context-insensitive, O(n³) worst case. Inclusion-based constraint solving; most precise flow-insensitive.
- **Steensgaard's analysis** (1996, POPL): almost-linear O(n α(n)). Unification-based — less precise but far faster on large programs.
- **TBAA (Type-Based Alias Analysis)**: uses strict aliasing rules (C/C++ UB: int* and float* can't alias) + explicit metadata tags. LLVM annotates all memory ops with `!tbaa` metadata.
- **Points-to sets**: abstract heap locations as allocation sites; CFL-reachability formulation (Reps 1997).

### Escape Analysis & Scalar Replacement
Determine if an object's lifetime is bounded to the allocating function (doesn't escape). Non-escaping objects can be **stack-allocated** or **scalar-replaced** (fields promoted to individual SSA values). LLVM's **SROA** (Scalar Replacement of Aggregates) promotes `alloca`s with only GEP+load/store uses to SSA values, enabling downstream DCE and GVN. HotSpot C2 eliminates heap allocations for non-escaping objects. Wuerthinger et al. (VEE 2010) — escape analysis for Java JITs.

### SCCP (Sparse Conditional Constant Propagation)
Wegman & Zadeck 1991 (TOPLAS). Combined constant propagation + unreachable code elimination using a lattice: ⊤ (unknown) → constant → ⊥ (overdefined). Worklist algorithm on SSA def-use chains: more precise than separate passes because it handles conditional branches (if known-false branch, don't propagate its definitions).

> **Deeper dives → [`optimization_techniques.md`](@/notes/programming/optimization_techniques.md):** CSE vs value
> numbering + `EarlyCSE` (§1); SLP / load-store vectorization + GCD/Banerjee/Omega dependence
> testing (§2); straight-line strength reduction + LSR (§3); array scalarization,
> register promotion, MemorySSA-based redundant-load / dead-store elimination (§4).

---

## 3. Instruction Selection

Map IR operations to target machine instructions. A fundamentally NP-hard problem (covering DAG with tiles); practice uses efficient heuristics.

### BURS (Bottom-Up Rewrite Systems)
Fraser & Henry BURG (1992) and Aho et al. tree pattern matching: express isel as a tree grammar. Dynamic programming over expression tree to find minimum-cost tiling. BURS generates optimal code for expression trees in O(n) after O(|grammar|) precomputation.

### LLVM SelectionDAG
LLVM's primary isel mechanism. Pipeline:
1. Build **SelectionDAG** from LLVM MIR — nodes are typed SDValues, edges are data/chain/glue dependencies
2. Target-independent DAG combines (peepholes)
3. **Legalization**: expand/promote/custom-lower illegal types and operations for target
4. Target-specific DAG combines
5. **Instruction selection**: pattern-match DAG nodes to `MachineInstr` using tblgen-generated patterns from `.td` files
6. **Scheduling**: order MachineInstrs for the basic block

SelectionDAG is powerful but slow (per-basic-block, high memory allocation). `FastISel` bypasses it for `-O0` builds: simple direct mapping, misses many opportunities, falls back to SelectionDAG for unsupported patterns.

### GlobalISel
LLVM's newer isel framework (merged 2016, production on AArch64 since ~2020). Works on **MachineIR** throughout rather than a parallel DAG. Passes: `IRTranslator` (LLVM IR → generic MIR), `Legalizer`, `RegBankSelect` (coarse register file assignment), `InstructionSelect` (pattern matching via tblgen). Benefits: operates across basic block boundaries, reuses MachineIR analysis infrastructure. Status 2026: AArch64 default at `-O0`/`-O1`; x86 migration ongoing.

---

## 4. Register Allocation

Map unlimited virtual registers to finite physical registers. Spill to stack when not enough registers.

### Graph Coloring (Chaitin-Briggs)
Chaitin 1982 (CACM): build **interference graph** — nodes are live ranges (virtual registers), edges between live ranges that are simultaneously live. K-coloring where K = number of physical registers. NP-hard in general; heuristic via:
1. **Simplification**: remove nodes with degree < K (can always be colored)
2. **Coalescing**: merge copy-related nodes (George & Appel 1996 IRC — conservative coalescing avoids making graph uncolorable)
3. **Freeze**: give up coalescing for a low-degree move-related node
4. **Spill selection**: pick a node to potentially spill (cost = use/def frequency / spill weight)
5. **Select**: assign colors during stack pop; if can't color → actual spill

**Chaitin-Briggs** improvement (1982): conservative coalescing prevents Chaitin's aggressive coalescing from introducing uncolorable nodes.

**George-Appel Iterated Register Coalescing** (TOPLAS 1996): canonical modern algorithm, handles all cases cleanly, still used in GCC.

### Linear Scan
Poletto & Sarkar 1999 (TOPLAS): sort live intervals by start point, greedily assign registers, expire intervals ending before new one starts. O(n log n). Used for fast compilation (JIT -O1, LLVM legacy).

**Wimmer & Franz 2010** (CGO): SSA-based linear scan with live interval splitting. Splits intervals at optimal split points to reduce spills. Used in HotSpot C1 and early GraalVM.

**Braun & Hack 2009** (CC): "Register Allocation for Programs in SSA Form" — exploit SSA properties to simplify interference graph (SSA interference is chordal — graph coloring is polynomial). Efficient and clean.

### Cranelift regalloc2
Ion Baraff's algorithm (2022), used in Cranelift/Wasmtime. SSA-based, handles splits cleanly, ~20% faster compilation vs. previous allocator. Also has `fastalloc` single-pass allocator for `O0` compilation speed.

### Spilling & Rematerialization
**Spilling**: insert store to stack slot before last use, load from stack before next use. **Rematerialization**: recompute a value rather than loading from stack (worthwhile for cheap computations: constants, address calculations). Spill weights = (use_count + def_count) / loop_depth_factor to prefer spilling cold values.

---

## 5. Code Generation & Scheduling

### Instruction Scheduling
Reorder instructions within a basic block to hide latencies and maximize ILP, without violating data/control dependencies.

**List scheduling**: topological sort of dependency DAG, greedily schedule ready instructions by priority (critical path, resource usage). Forward scheduling (minimize latency) vs. backward scheduling (from last instruction). Used universally in production compilers.

**Software pipelining** (Rau, MICRO 1994): overlap iterations of a loop in time. Find **initiation interval** II = max(RecMII, ResMII) where RecMII is recurrence-constrained minimum and ResMII is resource-constrained minimum. **Modulo scheduling**: schedule loop kernel with period II, fill prologue/epilogue. Dramatically improves throughput for loops (VLIW, in-order CPUs). Rarely used for OOO x86 (hardware handles dynamic scheduling).

### ABI Calling Conventions

**System V AMD64 ABI** (Linux/macOS):
- Integer args: RDI, RSI, RDX, RCX, R8, R9 (then stack)
- Float/SSE args: XMM0–XMM7 (then stack)
- Return: RAX (integer), XMM0 (float), RAX+RDX (128-bit)
- **Red zone**: 128 bytes below RSP usable by leaf functions without adjusting stack (signal safety caveat)
- Callee-saved: RBX, RBP, R12-R15

**ARM64 AAPCS** (AArch64):
- Integer args: X0–X7, float/SIMD: V0–V7
- Return: X0/X1, V0/V1
- Callee-saved: X19–X28, V8–V15 (lower 64 bits)
- No red zone

### Prologue/Epilogue & DWARF CFI
Prologue: `push rbp; mov rbp, rsp; sub rsp, N`. **Frame pointer omission** (FPO): use RBP as a general register instead; requires **DWARF CFI** (Call Frame Information) directives (`.cfi_def_cfa_offset`, `.cfi_offset`) so debuggers/unwinders can reconstruct the stack frame without RBP. LLVM emits CFI by default at `-O1+`.

### Peephole Optimization
Small local rewrites on final instruction sequence: `add r, 0 → nop`, `mul r, 2 → shl r, 1`, `mov r, r → nop`. LLVM's `PeepholeOptimizer` pass and target-specific `optimizeInstruction()` callbacks.

> **Deeper dives → [`optimization_techniques.md`](@/notes/programming/optimization_techniques.md):** list
> scheduling, trace / superblock / hyperblock scheduling, modulo scheduling (ResMII/RecMII,
> MRT, MVE), VLIW ISAs (Itanium/C6000/Hexagon), and loop unrolling (§5); peephole as term
> rewriting, superoptimization, and LLVM's four-level InstCombine/DAGCombine/MachineInstr/
> GlobalISel family (§6).

---

## 6. LLVM Internals

### New Pass Manager (NPM)
Legacy PM (2003–2021) used a single pass pipeline with poor analysis caching. **New PM** (Chandler Carruth, LLVM 9, default LLVM 14):

- **Analysis managers**: `ModuleAnalysisManager`, `CGSCCAnalysisManager`, `FunctionAnalysisManager`, `LoopAnalysisManager` — each caches analysis results, invalidates on IR changes
- **PassBuilder**: configures optimization pipeline (O0/O1/O2/O3/Os) with registered extension points
- **PreservedAnalyses**: passes declare which analyses are preserved/invalidated
- **CGSCC pipeline**: call graph SCC traversal for interprocedural passes (inliner, devirtualization)

### IR Value/User/Use Graph
All LLVM IR entities are `Value` subclasses. `User` (extends `Value`) has a list of `Use` operands. `Use` is a linked-list node pointing back to its `User` — enables O(1) def-use traversal and O(1) RAUW (Replace All Uses With). `IRBuilder<>` provides type-safe API for constructing IR instructions.

### MachineIR (MIR)
After isel, IR is lowered to `MachineFunction` → `MachineBasicBlock` → `MachineInstr` → `MachineOperand`. Operands are: virtual registers, physical registers, immediates, frame indices, global addresses, etc. Pre-regalloc: virtual registers. Post-regalloc: physical registers. Accessed via `MachineRegisterInfo`, `TargetInstrInfo`, `TargetRegisterInfo`.

### MC Layer
Machine Code layer: `MCInst` (abstract instruction), `MCStreamer` (abstract emitter — output to object file or assembly text), `MCCodeEmitter` (encode MCInst to bytes), `MCAsmBackend` (fixups, relaxation), `MCObjectFileInfo`. Supports ELF/Mach-O/COFF. `llvm-mc` assembler uses this layer directly.

### ORCv2 JIT API
On-Request Compilation v2 (2019+). Architecture:

```
ExecutionSession      — owns all JIT state, thread-safe
  └─ JITDylib         — dynamic library abstraction (symbol table, linking scope)
       └─ MaterializationUnit — lazy source of definitions

IRCompileLayer        — compiles LLVM IR → object code
ObjectLinkingLayer    — links object code
  ├─ RTDyldObjectLinkingLayer  — uses RuntimeDyld (legacy, in-process)
  └─ JITLink              — new linker (out-of-process capable, ELF/MachO/COFF)
```

`LLJIT`: eager compilation (IR → object → link on lookup). `LLLazyJIT`: lazy compilation via `IndirectStubsManager` — first call triggers compilation, subsequent calls go direct. **Out-of-process JIT**: executor process separate from compiler process via `OrcRemoteTarget` / `SimpleRemoteExecutor` — enables JIT in sandboxed environments.

### MLIR
Multi-Level IR (Lattner et al., CGO 2021). Core insight: make the IR extensible with **dialects** — namespaced type and operation sets. Progressive lowering passes transform between dialects.

Key dialects:
- `affine`: polyhedral loop nests, affine maps, structured control flow
- `linalg`: named tensor contractions (matmul, conv) with indexing maps for auto-scheduling
- `vector`: n-D vectors, reduction/broadcast/shuffle operations
- `arith`: arithmetic on scalars/vectors
- `memref`: typed memory regions with layout maps (row-major, column-major, strided)
- `gpu`: GPU kernel launch, block/thread IDs, barrier sync
- `llvm`: direct LLVM IR constructs for final lowering
- `transform` (CGO 2025): explicitly scheduled transformations as IR operations (tiling, vectorization, distribution) — deterministic optimization without heuristic passes

**Bufferization**: converts value semantics (tensors) to buffer semantics (memrefs). One-shot bufferization (MLIR 2022) handles in-place analysis to minimize copies. Used in TensorFlow/XLA, JAX, IREE, Mojo, Flang.

---

## 7. JIT Compilation Deep Dive

### Interpreter Dispatch Techniques

Ertl & Gregg (PLDI 2003) quantified dispatch overhead:

| Technique | Description | BTB pressure | Overhead |
|---|---|---|---|
| Switch dispatch | `switch(opcode) { case OP_ADD: ... }` | 1 indirect branch (all opcodes → same predictor slot) | ~100% misprediction for varied traces |
| Computed goto | GCC `&&label`, `goto *dispatch_table[op]` | N separate indirect branches | ~50% misprediction |
| Direct threading | Instruction words ARE code addresses; `goto *pc++` | N direct threaded branches | Best for interpreted execution |
| Indirect threading | Instruction words point to code vectors | Intermediate | Used in CPython pre-3.11 |
| Subroutine threading | Instructions are calls | Call overhead | Clean but slow |

CPython 3.11 specializing adaptive interpreter: inline caches embedded in bytecode stream, per-opcode specialization (e.g., `LOAD_ATTR` → `LOAD_ATTR_INSTANCE_VALUE` when shape known). Reduces dispatch + specializes hot paths without a JIT.

### Tracing JIT

HotpathVM (Gal & Franz, VEE 2006): identify hot loop back-edges, record a **trace** (linear sequence of operations executed, including across calls), compile trace to native code with **guards** at each branch.

```
Loop body execution:
  [guard: x is int] → side exit if fails
  x = x + 1
  [guard: x < 100] → side exit if fails
  → loop back
```

**Trace trees**: multiple traces from same loop header; side exits can trigger new trace recording from that point → forms a tree. TraceMonkey (Mozilla Firefox 3.5, 2009): first production trace JIT for JavaScript.

**Blacklisting**: traces that side-exit too frequently are marked "not worth tracing" — avoid compilation thrash for unoptimizable code.

**Limitations**: trace explosion for loops with many branches; poor performance on OO dispatch-heavy code. Most major runtimes (V8, SpiderMonkey) moved away from tracing to method JIT.

### Tiered Compilation

```
Interpreter (T0)
    │  hot method detection (invocation counter + back-edge counter)
    ▼
Baseline JIT (T1) — fast, unoptimized native code, collects type profiles
    │  profile threshold
    ▼
Optimizing JIT (T2) — slow compilation, speculative opts, deopt on assumption violation
```

**HotSpot JVM** (T0–T4):
- T0: interpreter (with profiling)
- T1: C1 client compiler (fast, local opts, no inlining)
- T2: C1 with full profiling
- T3: C1 with light profiling
- T4: C2 server compiler (aggressive opts, speculative inlining, escape analysis, loop vectorization)

**V8 JavaScript** (4-tier, 2023+):
- Ignition: register-based bytecode interpreter with inline caches, collects type feedback
- Sparkplug: baseline JIT — compiles Ignition bytecode 1:1 to native, no optimization, < 1ms compilation
- Maglev: SSA-based mid-tier JIT (shipped Chrome 117, 2023) — 2-4x speedup vs Sparkplug, compiles in ~20ms
- TurboFan: optimizing JIT (Sea of Nodes IR) + **Turboshaft** (new CFG-based backend, Chrome 120+, 2x faster TF compilation)

**.NET RyuJIT**:
- Tier 0: fast no-opt JIT on first call
- Tier 1: re-JIT hot methods with full optimization + dynamic PGO (enabled by default .NET 8)
- On-stack replacement (OSR) for long-running methods

**GraalVM Truffle**: language-agnostic JIT via partial evaluation (see §9).

### Deoptimization

When speculative assumptions are violated, the JIT must **deoptimize** — reconstruct interpreter state and fall back.

**HotSpot uncommon traps**: compiled code calls `Deoptimization::uncommon_trap()` on guard failure. Frame is rewritten in-place (deopt metadata maps compiled frame to interpreter frame). Deopt reasons tracked per call site; too many → recompile without that speculation.

**Safepoints**: positions where GC can safely run and deopt can be performed. All live references must be precisely known (stack maps). Two mechanisms:
- **Polling-based**: compiled code polls a flag at loop back-edges and method entries (`test [safepoint_page], eax` — page is mprotect'd to PROT_NONE to trigger SIGSEGV on GC request)
- **Signal-based**: no polling; use OS signals to interrupt threads at any point, but requires signal-safe frame unwinding

**On-Stack Replacement (OSR)** (Fink & Qian, VEE 2003): replace a running interpreted method with compiled code (or compiled → recompiled) while it's in the middle of execution — critical for long-running loops that hit the compilation threshold mid-loop. Requires mapping interpreter state (locals, stack) to compiled frame layout.

### Speculative Optimization & Inline Caches

**Type feedback**: interpreter/baseline JIT instruments call sites and property accesses to record types seen at runtime. Optimizing JIT uses this to specialize (assume `x` is always `int`).

**Inline caches (ICs)**: small stub at call site that caches last (or N) receiver types. First call: cache miss, slow path lookup + cache population. Subsequent calls: fast path check.

- **Monomorphic IC**: 1 cached type, single comparison + direct call. ~1-2 ns.
- **Polymorphic IC (PIC)**: 2-4 types, linear scan of stubs. ~3-5 ns.
- **Megamorphic IC**: too many types seen → full vtable/dictionary lookup.

**Hidden classes / Shapes / Maps**: V8 assigns each object a **Map** (hidden class) encoding the object's layout (field names + offsets). Objects with same fields in same order share a Map. Property access becomes fixed offset load once Map matches. Shape transitions form a tree. SpiderMonkey: "Shapes" (property tree). JSC: "Structures". This enables C-struct-like field access for JS objects.

**V8 Map transitions**:
```
{} → Map0
{x: 1} → Map1 (Map0 + field 'x' at offset 8)
{x: 1, y: 2} → Map2 (Map1 + field 'y' at offset 16)
```

### Profile-Guided Optimization (PGO)

**Instrumented PGO**: insert counters at branches/calls, run representative workload, use profile in recompilation. Standard in GCC/Clang (`-fprofile-generate` / `-fprofile-use`). Enables: block reordering (hot code in one cache line), inlining prioritization, branch prediction hints.

**AutoFDO** (Chen et al., CGO 2016, Google): sampling-based PGO using Linux `perf` hardware counters. No instrumentation overhead. Profile converted to IR-level feedback via `create_llvm_prof`. Used at scale in Google's production builds.

**BOLT** (Panchenko et al., CGO 2019, Facebook/Meta): post-link binary optimizer. Reads `perf` profile, reconstructs CFG from binary, reorders basic blocks for hot/cold separation, improves icache utilization. 5-15% improvement on already PGO-compiled binaries.

**CSSPGO** (LLVM 2021): context-sensitive PGO — tracks call context (call stack) in profile, enables precise per-callsite inlining decisions.

---

## 8. High-Performance JIT Backends

### asmjit
Zero-dependency C++ assembler library (Kobalicek). Full x86/x64 ISA including AVX-512. API:

```cpp
JitRuntime rt;
CodeHolder code;
code.init(rt.environment());
x86::Assembler a(&code);
a.mov(x86::eax, 1);
a.ret();
// Finalize → executable memory
```

**Dual-mapped W^X**: allocates two virtual mappings of same physical page — one RW (for writing code), one RX (for execution). Avoids mprotect syscalls between write and execute. Critical for Apple Silicon (W^X enforced by hardware on non-JIT-entitled processes). Used in Umbra database for sub-millisecond query compilation.

### MIR (Vladimir Makarov, Red Hat)
Lightweight JIT backend, pure C, no dependencies, ~20kLOC. Three-address MIR instructions, SSA construction, GVN, inlining, simple regalloc. Benchmark: **~180x faster compilation than GCC -O2**, ~70% of GCC code quality. Also has C-to-MIR frontend. Designed as a lightweight GCC JIT option, also used for JIT-in-a-library scenarios.

### QBE
14kLOC (vs. LLVM ~10MLOC). SSA-based IR, GVN, regalloc, ABI lowering for x86-64, AArch64, RISC-V. "Compiler backend in a weekend" scope. Achieves ~70% of GCC -O2 performance. Used by cproc (C11 compiler), scc (simple C compiler), several research compilers. Good reference implementation for learning backend design.

### Cranelift
Rust-based compiler backend (Mozilla, Bytecode Alliance). CLIF (Cranelift IR): SSA-based, typed, close to MIR level. Used in Wasmtime (WASM JIT) and SpiderMonkey (WASM tier).

Key design decisions:
- **regalloc2** (Ion Baraff, 2022): new register allocator, ~20% faster compilation vs. IonAlloc, supports move hints for coalescing without full coalescing logic, `fastalloc` single-pass mode for `-O0`
- Explicit **ABI lowering** as a separate pass (not mixed with isel)
- No link-time optimization (kept intentional for JIT simplicity)
- **Filetests**: test harness comparing compiled output register-by-register

### Copy-and-Patch JIT
Xu & Kjolstad (OOPSLA 2021): template-based JIT with zero IR overhead. Compile handler stubs with Clang ahead of time, leaving **holes** (patches) for operands. At JIT time: copy stencil, patch operand slots. No IR, no regalloc, no optimization — just memcpy + patch.

**CPython 3.13 JIT**: uses copy-and-patch on specializing interpreter opcodes. Compiles one "micro-op" at a time. Not an optimizing JIT — more a "no-interpreter-overhead" tier. 5-15% faster on micro-benchmarks. CPython 3.14 adds a Tier 2 optimizer between specialization and copy-and-patch.

### LuaJIT
Mike Pall's Lua 5.1 JIT. Widely considered the fastest scripting JIT ever written for its era.

- **DynASM**: meta-assembler that processes mixed C/assembly source into a C file with code generation calls — used to hand-write both interpreter and JIT backend compactly
- **Dual VM**: fast bytecode interpreter as fallback; trace JIT for hot loops
- **Trace compiler**: records traces across Lua+C FFI boundaries; SSA IR with alias analysis; platform-specific backend (x86, x64, ARM, MIPS, PPC); no GVN or global opts — purely trace-local
- **FFI**: call C functions and access C structs directly from Lua without writing C glue — eliminates GC object overhead for numeric workloads
- Performance: 5-20x CPython for numeric code, within 2x of C for many benchmarks

---

## 9. Partial Evaluation & Specialization

### Futamura Projections (Futamura 1971)
Given: `interpreter` runs `program` on `data`: `interpreter(program, data) = output`

Let `mix` be a partial evaluator (specializes a program given some inputs):

- **1st projection**: `mix(interpreter, program) = compiled_program` — specializing interpreter to program gives a compiled program
- **2nd projection**: `mix(mix, interpreter) = compiler` — specializing partial evaluator to interpreter gives a compiler
- **3rd projection**: `mix(mix, mix) = compiler_compiler` — specializing to the specializer gives a compiler generator

The first projection is practically implemented by JITs: partial evaluation of the interpreter with respect to known bytecode.

### PyPy / RPython Metatracing
PyPy (Bolz et al., ECOOP 2009): write interpreter in **RPython** (restricted Python subset), run **metatracer** on the interpreter itself. Trace records interpreter's execution trace for a given bytecode sequence → compiled native code for that bytecode sequence. Automatically derives a JIT from any RPython interpreter. Achieves 2-5x CPython for many benchmarks (worse for I/O-bound). Used for PyPy, Pypy-STM, RPython runtimes for other languages.

### GraalVM Truffle
Wuerthinger et al. (ONWARD! 2012, ACM DL 2013). **Self-optimizing AST interpreter**: AST nodes rewrite themselves based on type feedback:

```java
// Initially polymorphic add node
class AddNode extends Node {
    Object execute(Object a, Object b) {
        if (a instanceof Integer && b instanceof Integer) {
            replace(new IntAddNode());  // specialize
            return (int)a + (int)b;
        }
        // fallback...
    }
}
```

**Graal compiler** performs partial evaluation of the AST interpreter: given specialization state, constant-folds interpreter dispatch, inlines AST node execute() methods → produces tight native code. Language-agnostic: same mechanism works for JavaScript (GraalJS), Python (GraalPy), Ruby (TruffleRuby), R, WebAssembly, LLVM bitcode (via Sulong). Native Image: AOT compile Java to standalone binary using closed-world assumption.

---

## 10. WebAssembly Compilation

### WASM Semantics
- **Structured control flow**: `block`, `loop`, `if/else` constructs — no arbitrary goto. Simplifies validation and compilation. Branch targets are block depths (not addresses).
- **Linear memory**: single or multi-memory, byte-addressable, no type safety. Bounds checks required (unless Spectre guards or OS trap-based).
- **Value types**: `i32`, `i64`, `f32`, `f64`, `v128` (SIMD), `funcref`, `externref`
- **Validation**: typed stack machine; all programs type-check in a single linear pass before execution

### Compilation Tiers

**V8 Liftoff** (baseline, 2018): one-pass streaming compiler — generates code as it parses WASM, no IR. Fast startup (~10x faster than TurboFan). Each local maps to a stack slot or register; simple regalloc.

**V8 TurboFan for WASM**: same optimizing JIT as JS but typed. Lazy tier-up: Liftoff code counts hot function calls, triggers async TurboFan compilation, patches call sites when done.

**V8 speculative WASM opts** (Chrome M137, 2025): type speculation on `call_indirect` and `call_ref` — inline the most common callee with a type guard + deopt path. >50% speedup on Dart/Flutter benchmarks.

**Wasmtime/Cranelift**: Rust-based, Cranelift backend, AOT or JIT mode. Used in production (Fastly Compute, Shopify). `winch`: new baseline compiler for Wasmtime (analogous to Liftoff).

**Binaryen**: WASM-to-WASM optimizer. `wasm-opt -O3` runs DCE, inlining, GVN, type narrowing on WASM IR. Used by Emscripten, Dart, Kotlin/WASM as a post-compilation optimization step.

### WasmGC
Struct and array heap types with typed references, garbage-collected by host GC. Baseline support in all major browsers (December 2024). Enables JVM/CLR-style managed languages to target WASM without a bundled GC. Kotlin/WASM and Dart/Flutter ship WasmGC builds. V8 WasmGC speculative inlining (call_indirect with deopt) shows >50% speedup in benchmarks.

---

## 11. GC Integration with JIT

### Precise GC Roots
GC must know exactly which stack slots and registers hold live GC references at every safepoint. LLVM provides:
- `gc.statepoint` intrinsic: marks a call as a safepoint, accepts all live GC values as operands
- `gc.relocate`: after statepoint, gets the potentially-moved new address of a GC value
- **Stack map section**: emitted in `.llvm_stackmaps` ELF section; encodes for each call site: which stack offsets/registers hold references
- `RewriteStatepointsForGC` pass: inserts statepoints and relocates throughout IR

### Safepoints
JVM safepoint mechanism: compiled code checks a **safepoint polling page** at method entries and loop back-edges. GC calls `os::protect_memory()` on the page → makes page PROT_NONE → next poll triggers SIGSEGV, caught by JVM signal handler → thread suspends at safepoint. All threads must reach safepoints before GC can proceed (stop-the-world).

Signal-based safepoints (zero-cost probes unless GC active): send SIGPOLL/SIGUSR1 to threads; threads must be at async-safe points (function prologue/loop header); requires frame unwinding capability at any instruction.

### Write Barriers
Needed for generational and concurrent GC:

- **Card table** (generational): heap divided into 512-byte cards; on store to heap object, mark card as dirty (`card_table[addr >> 9] = dirty`). GC scans dirty cards for cross-generation references. ~1 additional store per heap write.
- **SATB** (snapshot-at-the-beginning, G1 GC, ZGC): concurrent marking. Before overwriting a reference, log the old value to a thread-local buffer. Ensures all objects reachable at marking start are eventually marked.
- **Incremental update** (CMS): log the new value. Requires re-scanning at the end of concurrent mark. Simpler but less precise.

### GC-Aware Register Allocation
Regalloc must ensure GC references don't reside in callee-saved registers across a safepoint without proper tracking (since GC might move the object). LLVM's statepoint mechanism forces explicit use of `gc.relocate` — any reference used after a statepoint must use the relocated value, ensuring no stale pointer use.

---

## 12. JIT for Databases & Specialized Domains

### HyPer Data-Centric Compilation
Neumann (VLDB 2011): "Efficiently Compiling Efficient Query Plans for Modern Hardware". Key insight: query compilation should produce **pipelined** code that processes tuples through multiple operators without materializing intermediate results.

**Produce/Consume model**: each operator has `produce()` and `consume(tuple)` methods. Compilation via recursive invocation:

```
HashJoin.produce():
    build_side.produce()          // fill hash table
    probe_side.produce()          // probe

Scan.produce():
    for each tuple t:
        parent.consume(t)         // push up pipeline

HashJoin.consume(t) [from probe side]:
    if probe_hash_table(t):
        parent.consume(merge(t, match))
```

This generates code where the inner loop contains the full pipeline — Scan → Filter → HashProbe — with no function call overhead. LLVM IR generated per query, compiled at O2. Throughput: 6.5 GB/s tuple processing on OLAP queries.

### Umbra Flying Start
Kersten et al. (VLDB J. 2021): two-tier compilation for sub-millisecond query latency:
- **U1** (asmjit-based): ~1ms compilation, ~50% of LLVM-optimized performance
- **U2** (LLVM-based): ~100ms compilation, full optimization

Short queries run on U1; U2 compiles asynchronously; long queries migrate to U2 via on-stack replacement. See also `database/hyper_umbra_cedardb.md`.

### Other Database JIT Approaches
- **DuckDB**: no JIT. Pre-compiled vectorized kernels in C++ with templating, `#pragma GCC optimize("O3")`, AVX-512 where available. Expression evaluation via expression trees of pre-compiled functions. Fast development cycle.
- **Apache Spark Whole-Stage CodeGen**: Janino (runtime Java source compiler) generates Java bytecode for operator pipelines at query start. ~2x faster than interpreted Volcano model.
- **Photon** (Databricks, SIGMOD 2022): C++ native vectorized engine with JNI bridge. Pre-compiled columnar kernels, no JIT. 2-12x faster than Spark for supported operators.

### ML Compiler Stack
- **XLA** (Accelerated Linear Algebra, Google/TensorFlow/JAX): HLO IR → backend-specific lowering (GPU/CPU/TPU). Auto-fusion, layout optimization, algebraic simplification. MLIR-based since XLA 2023.
- **TVM** (Chen et al., OSDI 2018): compute/schedule separation (Halide-inspired). `te.compute()` defines computation; schedules specify tiling, vectorization, parallelism. **AutoTVM** and **Ansor** auto-tune schedules via ML-guided search.
- **Halide** (Ragan-Kelley et al., PLDI 2013): image/signal processing. Separate algorithm (what to compute) from schedule (how: tiling, vectorization, parallelism, fusion). Schedule-space search finds near-optimal implementations.

### GPU Compilation
**NVPTX backend** (LLVM): target for CUDA compilation. CUDA C → Clang frontend → LLVM IR → NVPTX → PTX (assembly) → cubin (GPU binary via ptxas or NVVM). PTX is a virtual ISA — retargetable to different GPU generations by NVIDIA driver.

**CUDA compilation model**: `__global__` kernels compiled to PTX/cubin; host code compiled to x86. `nvcc` orchestrates Clang + ptxas. **SASS** = actual machine code (SM-specific). PTX provides forward compatibility.

**ROCm HIP**: AMD's CUDA-compatible API. `hipcc` → Clang with AMDGPU target (LLVM AMDGPU backend) → GCN/RDNA ISA. **AMDGPU backend** in LLVM supports both OpenCL and HIP/ROCm.

---

## 13. State of the Art (2023–2026)

### MLIR Ecosystem
- **IREE** (Intermediate Representation Execution Environment, Google): production ML deployment stack built on MLIR. Compiles TensorFlow/JAX/PyTorch ops through MLIR dialects to CPU/GPU/embedded targets. Used in Google production.
- **Mojo** (Modular): Python-compatible systems language, MLIR-native compiler stack. MLIR dialects for Mojo IR; leverages `affine`/`linalg`/`vector` for kernel generation.
- **Flang**: LLVM Fortran frontend, now MLIR-based. FIR (Fortran IR) dialect → LLVM IR.
- **circt**: Circuit IR Compilers and Tools — MLIR dialects for hardware design (RTL, scheduling, synthesis). Bridges software and hardware compiler communities.
- **Transform dialect** (CGO 2025): user-controlled, deterministic optimization scheduling. Replaces heuristic passes with explicit IR-level transformation recipes. Game changer for ML kernel authors.

### JIT Advances (2024–2026)
**CPython 3.13 JIT**: copy-and-patch on micro-ops. 5-15% improvement. **3.14 outlook**: Tier 2 optimizer (abstract interpretation + type narrowing on micro-op traces) + free-threaded mode (GIL-free with per-object locking). Target: 2-5x speedup over 3.10 by 3.14.

**YJIT (Ruby)**: Lazy Basic Block Versioning (Chevalier-Boisvert 2016, DLS) — compile one basic block at a time, specialized per type context; defer compilation until types are known at each branch. Shopify rewrote in Rust (2022), shipped Ruby 3.1+. Numbers: 15-19% faster on Shopify production workloads; 2-3x on micro-benchmarks.

**V8 Turboshaft** (Chrome 120+, 2023): replaces TurboFan's Sea-of-Nodes backend with a conventional CFG-based IR. Sea-of-Nodes had pathological compile time on certain IR patterns. Turboshaft: 2x faster TurboFan compilation, cleaner architecture.

**.NET 8 Dynamic PGO**: enabled by default since .NET 8 (2023). RyuJIT instruments Tier 0 code, collects profiles, re-JITs hot methods at Tier 1 with optimization. ~15% improvement on average workloads.

### Superoptimizers
- **Souper** (Regehr et al., 2018): LLVM IR superoptimizer. Extracts "dataflow slices" from LLVM IR, queries Z3 SMT solver for stronger rewrites. Finds non-obvious peephole rules that humans miss. ~5% improvement on benchmarks when integrated into LLVM pipeline.
- **Minotaur**: synthesis-based vectorized peephole optimizer (2023). Extends Souper to vector operations; discovers AVX-512 rewrites. Merged into LLVM as proof-of-concept optimizations.
- **Hydra** (2024): multi-target superoptimizer using Alive2 (LLVM IR correctness verifier) for soundness checking.

### WasmGC & Browser JIT
WasmGC baseline all major browsers Dec 2024. V8 adding speculative inlining of `call_indirect` through WasmGC types (Chrome M137, 2025). Kotlin/WASM and Dart/Flutter WasmGC production builds available.

### Effects & Reference Counting
**Perceus** (Reinking et al., POPL 2021): provably efficient reference counting via **reuse analysis** — if an object's refcount drops to 1 before a constructor, reuse its memory in-place. Eliminates most RC overhead for functional programs. Implemented in Koka language.

**Koka v3** (Leijen, MSR): effects system enables compiler to reason about resource lifetimes and optimize allocations. Reference counting with Perceus reuse achieves GC-like throughput without GC pauses.

---

## Key References

### Compiler Architecture
- Lattner & Adve. "LLVM: A Compilation Framework for Lifelong Program Analysis and Transformation." CGO 2004.
- Cytron, Ferrante, Rosen, Wegman, Zadeck. "Efficiently Computing Static Single Assignment Form and the Control Dependence Graph." TOPLAS 13(4), 1991.
- Braun, Buchwald, Hack, Leißa, Mallon, Zwinkau. "Simple and Efficient Construction of Static Single Assignment Form." CC 2013.
- Cooper, Harvey, Kennedy. "A Simple, Fast Dominance Algorithm." RICE TR-06-33870, 2001.

### Optimization
- Wegman & Zadeck. "Constant Propagation with Conditional Branches." TOPLAS 13(2), 1991.
- Click & Cooper. "Combining Analyses, Combining Optimizations." TOPLAS 17(2), 1995.
- Andersen. "Program Analysis and Specialization for the C Programming Language." PhD Thesis, DIKU, 1994.
- Steensgaard. "Points-to Analysis in Almost Linear Time." POPL 1996.

### Instruction Selection & Register Allocation
- Fraser, Henry, Proebsting. "BURG—Fast Optimal Instruction Selection and Tree Parsing." SIGPLAN Notices 27(4), 1992.
- Chaitin, Auslander, Chandra, Cocke, Hopkins, Markstein. "Register Allocation via Coloring." Computer Languages 6(1), 1981.
- George & Appel. "Iterated Register Coalescing." TOPLAS 18(3), 1996.
- Poletto & Sarkar. "Linear Scan Register Allocation." TOPLAS 21(5), 1999.
- Wimmer & Franz. "Linear Scan Register Allocation on SSA Form." CGO 2010.
- Braun & Hack. "Register Allocation for Programs in SSA Form." CC 2009.

### JIT & Dynamic Compilation
- Aycock. "A Brief History of Just-In-Time." ACM Computing Surveys 35(2), 2003.
- Ertl & Gregg. "The Structure and Performance of Efficient Interpreters." J. Instruction-Level Parallelism 5, 2003.
- Gal & Franz. "Incremental Dynamic Code Generation with Trace Trees." VEE 2006.
- Fink & Qian. "Design, Implementation and Evaluation of Adaptive Recompilation with On-Stack Replacement." CGO 2003.

### High-Performance Backends
- Xu & Kjolstad. "Copy-and-Patch Compilation: A fast compilation algorithm for high-level languages and bytecode." OOPSLA 2021.
- Chevalier-Boisvert & Feeley. "Simple and Effective Type Check Removal through Lazy Basic Block Versioning." ECOOP 2015.
- Bolz, Cuni, Fijalkowski, Rigo. "Tracing the Meta-Level: PyPy's Tracing JIT Compiler." ICOOOLPS 2009.
- Wuerthinger et al. "Self-Optimizing AST Interpreters." DLS 2012.

### Database JIT
- Neumann. "Efficiently Compiling Efficient Query Plans for Modern Hardware." PVLDB 4(9), 2011.
- Kersten, Leis, Kemper, Neumann. "Tidy Tuples and Flying Start: Fast Compilation and Fast Execution of Relational Queries in Umbra." VLDB J. 30(5), 2021.

### ML Compilers
- Chen, Moreau, et al. "TVM: An Automated End-to-End Optimizing Compiler for Deep Learning." OSDI 2018.
- Ragan-Kelley, Barnes, Adams, Paris, Durand, Amarasinghe. "Halide: A Language and Compiler for Optimizing Parallelism, Locality, and Recomputation in Image Processing Pipelines." PLDI 2013.
- Lattner et al. "MLIR: Scaling Compiler Infrastructure for Domain Specific Computation." CGO 2021.

### PGO & Superoptimization
- Chen, Luk, Xu. "AutoFDO: Automatic Feedback-Directed Optimization for Warehouse-Scale Applications." CGO 2016.
- Panchenko, Auler, Dawson, Ottoni. "BOLT: A Practical Binary Optimizer for Data Centers and Beyond." CGO 2019.
- Sasnauskas et al. "Souper: A Synthesizing Superoptimizer." arXiv 2018.

### GC & Memory Safety
- Reinking, Xie, de Moura, Leijen. "Perceus: Garbage Free Reference Counting with Reuse." PLDI 2021.
- Futamura. "Partial Evaluation of Computation Process — An Approach to a Compiler-Compiler." Systems, Computers, Controls 2(5), 1971.
