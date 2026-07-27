+++
title = "Optimization Techniques"
date = 2026-07-14
[taxonomies]
tags = ["compilers", "optimization", "vectorization", "scheduling", "llvm", "gcc", "ir"]
+++


# Compiler Optimization Techniques: Deep Dive

Mechanism-level reference for the mid-level and backend optimization passes: redundancy
elimination, vectorization, strength reduction, scalarization, instruction scheduling, loop
unrolling, and peephole rewriting. Each technique covers the *algorithm* (not just what it
does), pseudocode, before/after IR, and how LLVM/GCC actually implement it.

This is the companion to [`compilers_and_jit.md`](@/notes/programming/compilers_and_jit.md), which covers the
overall pipeline, SSA construction, instruction selection, register allocation, and JIT.
Passes already treated in depth there (DCE, GVN, alias analysis, SROA, register allocation,
modulo-scheduling overview) are cross-referenced, not duplicated.

**Pass placement (LLVM).** All operate on SSA-form IR (LLVM IR / GCC GIMPLE), running late
in the optimization pipeline after inlining and scalar cleanup, before the backend — except
the machine-level peephole/scheduling passes, which run on MachineIR post-instruction-selection.

---

## 1. Redundancy Elimination — CSE, Value Numbering

The oldest optimization family: don't compute the same thing twice.

### 1.1 Available-expressions dataflow (classical global CSE)

An expression `x op y` is **available** at point `p` if on *every* path from entry to `p` it
has been computed and neither `x` nor `y` has been redefined since. Forward, all-paths
(intersection) dataflow:

```
AVAIL_in[B]  = ∩ over preds P of AVAIL_out[P]      (AVAIL_in[entry] = ∅)
AVAIL_out[B] = GEN[B] ∪ (AVAIL_in[B] − KILL[B])
GEN[B]  = expressions computed in B and not killed afterward in B
KILL[B] = expressions using a variable assigned in B
```

Global CSE replaces an available expression's recomputation with the saved value. **Local
CSE** is the single-block version — a linear walk maintaining a table of already-computed
expressions, no dataflow needed. (Aho, Lam, Sethi, Ullman, *Compilers: Principles,
Techniques, and Tools*, 2nd ed., 2006, §9.2.)

### 1.2 Lexical CSE vs value-based (value numbering)

- **Lexical / syntactic CSE**: two expressions are "the same" iff *textually* identical (same
  operator, same operand *names*). Available-expressions is lexical. It misses `a=b+c; d=c+b`
  unless commutativity-normalized, and misses equal-*value* operands with different names.
- **Value numbering (VN)**: assign each computed value a **value number**; two expressions are
  equivalent iff their operator + operands' value numbers match. Catches copies and constant
  equalities lexical CSE misses.

Lineage:
- **Local VN** — Cocke & Schwartz, *Programming Languages and Their Compilers*, Courant
  Institute NYU, 1970 (hash-based numbering within a block).
- **Global dataflow framework** — Kildall, "A Unified Approach to Global Program
  Optimization," POPL 1973 (lattice dataflow, precise value-graph propagation; expensive).
- **Congruence-based GVN** — Alpern, Wegman, Zadeck, "Detecting Equality of Variables in
  Programs," POPL 1988: partition SSA values into **congruence classes** by optimistic
  Hopcroft-style partition refinement; does not fully interpret control flow, so it is
  efficient but less precise than Kildall.
- Rosen, Wegman, Zadeck, "Global Value Numbers and Redundant Computations," POPL 1988 — ties
  GVN to redundancy elimination on SSA. Simpson, "Value-Driven Redundancy Elimination," PhD
  thesis Rice 1996 — the RPO/hash-based GVN + GVN-PRE model LLVM's classic GVN follows.

### 1.3 LLVM `EarlyCSE` — scoped, dominator-tree

`EarlyCSE` (`-early-cse`; `-early-cse-memssa` variant) is a fast cleanup CSE, *not* full GVN.
It performs a dominator-tree walk over a **`ScopedHashTable`**: entering a dom-tree node pushes
a scope, leaving pops it, so entries are visible exactly within the subtree they dominate.

- **`AvailableValues`** — key `SimpleValue` (an `Instruction*` hashed by opcode + operands,
  with commutative operands normalized and min/max/select canonicalized), value = canonical
  `Value*`. Hit ⇒ RAUW + delete; miss ⇒ insert.
- **`AvailableLoads`** — key = pointer operand; value = `LoadValue{definingInst, generation,
  isAtomic, matchingType}`.
- **`AvailableCalls`** — read-only calls.
- **Generation counter** (`CurrentGeneration`) — a monotonic integer bumped on every
  instruction that may write memory. A load/call is reusable only if its stored generation
  equals the current one (no intervening write). With MemorySSA (§4.2),
  `isSameMemGeneration()` calls `getClobberingMemoryAccess()` for a *precise* clobber check
  instead of the coarse counter.
- **DSE** — tracks `LastStore`; a store to a location matched by a later store (same
  type/atomicity, no read between) is removed.

```
EarlyCSE(node):                 # visit in dominator-tree pre-order
  enter_scope(AvailableValues, AvailableLoads, AvailableCalls)
  for I in node.block:
     if isa<Load>(I):
        lv = AvailableLoads[ptr(I)]
        if lv and lv.generation == CurGen and types match:
           RAUW(I, lv.definingInst); erase(I); continue
        AvailableLoads[ptr(I)] = {I, CurGen}
     elif I is store:
        if LastStore to same loc, no read since: erase(LastStore)   # DSE
        CurGen++; LastStore = I; AvailableLoads[ptr(I)] = {value(I), CurGen}
     elif mayWriteMemory(I): CurGen++
     else:                                     # pure scalar op
        sv = SimpleValue(I)
        if AvailableValues[sv] exists: RAUW(I, that); erase(I)
        else AvailableValues[sv] = I
  for child in domtree.children(node): EarlyCSE(child)
  exit_scope()                                 # pops entries added in this subtree
```

Before / after (commutative-normalized):

```llvm
%a = add i32 %x, %y
%b = add i32 %y, %x    ; normalized to same key as %a
%c = mul i32 %a, %b
```
→
```llvm
%a = add i32 %x, %y
%c = mul i32 %a, %a
```

### 1.4 GVN vs NewGVN (global, value-based)

| | EarlyCSE | GVN | NewGVN |
|---|---|---|---|
| Scope | Local, dom-tree scoped | Global (function) | Global (function) |
| Equivalence | Syntactic (normalized) | Value-based (hash VN) | Value-based (congruence classes) |
| Memory / loads | Generation counter or MemorySSA | memdep/MemorySSA, load PRE | MemorySSA |
| Precision / cost | Cheapest; cleanup | Medium; does PRE | Highest; optimistic, costlier |
| Basis | Kildall-ish scoped hash | Simpson 1996 | Alpern-Wegman-Zadeck 1988 + SCCP |

`GVN` (`-gvn`) is Simpson-style hash VN over the whole function plus load elimination / load
PRE (§4.3) plus redundant-expression PRE, doing the removal inline. `NewGVN` (`-newgvn`)
reimplements Alpern-Wegman-Zadeck congruence classes combined with SCCP (an *optimistic*
partition refinement that discovers equalities across loops and is unreachable-code-aware),
generally more precise than classic GVN. See [`compilers_and_jit.md`](@/notes/programming/compilers_and_jit.md)
§2 for GVN's MemorySSA integration and SCCP.

> **DCE / ADCE** — see [`compilers_and_jit.md`](@/notes/programming/compilers_and_jit.md) §2. DCE marks reachable
> side-effecting instructions and sweeps the rest; ADCE inverts (assume all dead, mark live
> backward from side effects). Not repeated here.

---

## 2. Vectorization — SLP, Load/Store, Dependence Testing

**Two orthogonal axes.** *Loop vectorization* exploits loop-level parallelism: iterations
`i..i+VF-1` execute as one vector iteration (vector factor VF). *SLP* exploits data-level
parallelism *within a basic block*: independent isomorphic scalar statements packed
side-by-side into one SIMD op — no loop required. They are complementary; LLVM runs both. A
third pass, LoadStoreVectorizer, is a narrow memory-only coalescer used mainly on GPUs.

LLVM passes `LoopVectorizePass` and `SLPVectorizerPass` (in `llvm/lib/Transforms/Vectorize/`)
run late, before the backend. GCC's equivalents are in `tree-vect-*` (loop + basic-block/SLP,
all SLP-based since GCC 14).

### 2.1 SLP (Superword-Level Parallelism)

**Origin.** Larsen & Amarasinghe, "Exploiting Superword Level Parallelism with Multimedia
Instruction Sets," PLDI 2000 (SIGPLAN Notices 35(5):145–156). Insight: short fixed-width SIMD
("superword") units are better targeted by finding *isomorphic, independent* scalar statements
anywhere in a basic block and packing them, than by loop analysis. A loop is handled by
**unrolling it first**, turning loop-level parallelism into intra-block SLP — so SLP subsumes
loop vectorization for unrolled straight-line code and additionally catches parallelism that
has no loop at all (hand-unrolled code, struct-field arithmetic).

Definitions:
- A **pack** is an n-tuple of independent, isomorphic statements `⟨s1,…,sn⟩`, replaced by one
  SIMD op. A **PackSet** is a set of packs.
- Two statements are **isomorphic** if they contain the same operation in the same order.
- **Adjacent memory references**: `a[i]` and `a[i+1]` reference contiguous memory — the seed.

The four-phase algorithm:

**Phase 1 — seed from adjacent references.** Scan for pairs of memory accesses to adjacent
addresses; each independent, alignment-consistent adjacent pair (both loads, or both stores)
seeds a 2-element pack.

```
find_adj_refs(BB):
  P = {}
  for each pair (s1, s2) of memory refs in BB:
    if adjacent(s1, s2) and stmts_can_pack(P, s1, s2, align):
      P += {(s1, s2)}
  return P
```
`stmts_can_pack` checks: isomorphic, independent, s1 not already the left element of another
pack and s2 not already the right, alignment consistent.

**Phase 2 — extend the PackSet** along data-dependence edges in *both* directions:

```
extend_packlist(P):
  do:
    Pprev = P
    for each pack p in P:
      P = follow_use_defs(P, p)     // grow toward producers (defs of p's operands)
      P = follow_def_uses(P, p)     // grow toward consumers (uses of p's results)
  while P != Pprev
  return P
```

**Phase 3 — combine packs into groups.** Merge packs sharing an endpoint: if `p1=⟨s1..sn⟩`
and `p2=⟨sn..sm⟩` (last of p1 == first of p2), combine to `⟨s1..sm⟩`. The paper's example:
`(1,4)` and `(4,7)` combine to `(1,4,7)`, building packs of width matching the SIMD register.

**Phase 4 — schedule / codegen.** Emit SIMD ops in dependence order (topological sort). Where
a scalar value feeds a packed op, insert a **pack** (`insertelement`); where a packed result
feeds scalar code, insert an **unpack** (`extractelement`). A dependence cycle among packs
forces a pack back to scalars.

**Alignment** is propagated (`a[i]` alignment = base alignment + offset mod vector width);
packs must be alignment-consistent. **Cost model** is *local, greedy*: keep a pack only if the
SIMD op's benefit exceeds the pack/unpack overhead at scalar↔vector boundaries — a known
limitation later addressed by goSLP.

Before (hand-unrolled loop body) → after:

```
a[0]=b[0]+c[0]; a[1]=b[1]+c[1];        vb = vload b[0..3]
a[2]=b[2]+c[2]; a[3]=b[3]+c[3]    →    vc = vload c[0..3]
                                       va = vadd vb, vc
                                       vstore a[0..3], va
```

**Loop-Aware SLP** — Rosen, Nuzman, Zaks, "Loop-Aware SLP in GCC," GCC Developers' Summit
2007. Combines loop and SLP vectorization in one framework (unroll-and-pack while keeping loop
context). LLVM's `SLPVectorizer.cpp` header explicitly names this as its inspiration.

#### LLVM `SLPVectorizer` — bottom-up (`BoUpSLP`)

`llvm/lib/Transforms/Vectorize/SLPVectorizer.cpp`, driver class `BoUpSLP`. On by default at
`-O2/-O3` (`-fno-slp-vectorize` to disable). **Bottom-up** = start from a seed and build a
**vectorization tree** upward through use-def chains toward operands/producers (contrast
Larsen, which seeds at memory and grows both ways).

- **Seeds**: primarily *consecutive stores* (`collectSeedStores` groups by underlying object;
  consecutive proven via SCEV). Also horizontal *reductions* (`HorizontalReduction` matches
  `a+b+c+d` → vector partial-sum + horizontal reduce), GEP indices, PHI nodes.
- **Vectorization tree** of `TreeEntry` nodes. `buildTree_rec`: all lanes must be same opcode
  and schedulable together, then recurse per operand position. Node kinds: **vectorizable**
  (all lanes isomorphic → real vector op) and **gather** (non-uniform lanes materialized by
  `insertelement` build-vector — the cost leaves). `reorderInputsAccordingToOpcode` handles
  commutative-operand alignment (LLVM's look-ahead reordering).
- **Scheduling**: `BlockScheduling` / `ScheduleBundle` verifies a bundle can be legally
  scheduled contiguously before vectorizing (analogue of Larsen's Phase-4 cycle check).
- **Cost model** via `TargetTransformInfo` (`getTreeCost`): vectorizable-node cost = vector-op
  cost − replaced scalar-op costs (negative = saving); **gather** cost = build-vector cost;
  **extract** cost = `extractelement` for results still used by scalar code (`ExternalUses`);
  plus shuffle and `getSpillCost` (penalizes long vector live ranges across calls). Vectorize
  iff total < 0. Threshold `-slp-threshold`.

Before (`clang -O1`) → after SLP (seed = the two consecutive stores):

```llvm
%b0 = load double, ptr %B                %vb = load <2 x double>, ptr %B
%c0 = load double, ptr %C                %vc = load <2 x double>, ptr %C
%a0 = fadd double %b0, %c0          →    %va = fadd <2 x double> %vb, %vc
store double %a0, ptr %A                 store <2 x double> %va, ptr %A
; ... i64 1 versions of each ...
```

Since GCC 14 the basic-block (SLP) and loop vectorizer are unified — all vectorization is
SLP-based (`tree-vect-slp.cc`, `vect_build_slp_tree` builds an SLP tree of `slp_tree` nodes).

#### SLP vs loop vectorization

| | Loop vectorizer | SLP vectorizer |
|---|---|---|
| Unit | iterations of a loop | isomorphic scalar stmts in a BB |
| Requires a loop? | yes | no (straight-line code) |
| Cross-iteration deps | must prove absence (dependence analysis) | irrelevant; needs independence *within* the pack |
| Trip count | matters (remainder/tail loop) | N/A |
| Main cost risk | strided/gather memory | gather/scatter at pack boundaries (insert/extract) |
| LLVM pass | `LoopVectorizePass` | `SLPVectorizerPass` (`BoUpSLP`) |

#### Recent SLP research

- **goSLP** — Mendis & Amarasinghe, ASPLOS 2018. Replaces greedy local packing with an
  **Integer Linear Programming** formulation finding the *globally* cost-optimal packing
  across the whole function. Beats LLVM's greedy SLP on SPEC. Headline distinction: **global
  ILP** vs LLVM/Larsen **greedy local**.
- **Look-Ahead SLP** — Porpodas, Rocha, Góes, CGO 2018. For commutative ops where a greedy
  lane match fails, score deeper into the operand DAG to pick the reordering that keeps
  subtrees isomorphic. Now reflected in LLVM's operand-reordering heuristic.
- **VW-SLP** — Porpodas et al., PACT 2018. Vector width varies at *instruction* granularity
  within one tree rather than a fixed width.
- **Super-Node SLP** — Porpodas et al., CGO 2019. Handles chains mixing an operator and its
  inverse (`+`/`-`, `*`/`/`) as one commutative super-node (reassociation-aware packing).
- Earlier: **PSLP** (CGO 2015, pads non-isomorphic graphs to isomorphic) and **throttled SLP**
  (PACT 2015, partial-tree vectorization when full-tree is unprofitable).

### 2.2 Load/Store Vectorization

`llvm/lib/Transforms/Vectorize/LoadStoreVectorizer.cpp`. Coalesces *adjacent scalar* loads (or
stores) into one wider vector load/store — four `load i32` from `p, p+4, p+8, p+12` become one
`load <4 x i32>`. Unlike SLP it does **not** vectorize the *computation*: it assumes the
vector's elements are extracted and used individually. It exists chiefly because on GPUs a
single 128-bit transaction is far cheaper than four 32-bit ones (memory coalescing), and
`extractelement` from a vector register is nearly free there.

Algorithm:
1. **Pseudo-BB split** — break each block into maximal runs guaranteed to pass control to
   their successor (split at anything that could throw/not-return), so reordering memory ops
   within a pseudo-BB is safe.
2. **Equivalence classes** — bucket loads (separately, stores) by `(getUnderlyingObject(ptr),
   address space, element type size)`.
3. **Adjacency proof — `getConstantOffset`** — for two pointers in a class, prove one is a
   constant byte offset from the other, via **SCEV** and GEP decomposition (subtract the two
   pointer SCEVs, check the difference is constant). Handles nested GEPs, pointer `select`s,
   pointer arithmetic recursively up to depth 3. *This* is the key distinction from SLP's
   adjacency: an SCEV constant-offset proof on addresses, not an isomorphism check on
   computation.
4. **Greedy chain building** — sort a class by offset, greedily assemble chains with
   pairwise-known relative offsets.
5. **Three-phase chain split** — (a) **may-alias**: split where an intervening memory op may
   alias (can't reorder across a possible alias); (b) **contiguity**: split at gaps so each
   sub-chain is contiguous — optionally *gap-fill* a 1–2-element gap with masked elements if
   the target supports masked load/store (TTI); (c) **alignment/legality**: split/trim to a
   legal element count + alignment via `TargetTransformInfo`
   (`isLegalToVectorizeLoadChain`).
6. **Rewrite** — N scalar loads → one `load <N x T>` + N `extractelement`s (dually for stores).

Before → after (GPU-style):

```llvm
%v0 = load i32, ptr %p+0            %vec = load <4 x i32>, ptr %p, align 16
%v1 = load i32, ptr %p+1       →    %v0 = extractelement <4 x i32> %vec, i64 0
%v2 = load i32, ptr %p+2            %v1 = extractelement <4 x i32> %vec, i64 1
%v3 = load i32, ptr %p+3            ... i64 2, i64 3
```
On NVPTX this lowers to one `ld.global.v4.u32` (128-bit coalesced) instead of four
`ld.global.u32`. AMDGPU and NVPTX backends run this pass; the "extract is cheap" assumption
holds because GPU vector registers are addressable sub-lanes, unlike CPU SIMD where extraction
costs a shuffle.

| | LoadStoreVectorizer | SLPVectorizer |
|---|---|---|
| Vectorizes | memory access only | full computation graphs |
| Element use | assumes individual use (keeps extracts) | keeps values *in* vector form through the tree |
| Adjacency proof | SCEV constant-offset on addresses | isomorphism + consecutive-store SCEV |
| Primary target | GPUs (coalescing; cheap extract) | CPUs (SSE/AVX/NEON) |

### 2.3 Data Dependence Analysis

The legality oracle for vectorize/interchange/tile/unroll-and-jam. Canonical text: Allen &
Kennedy, *Optimizing Compilers for Modern Architectures: A Dependence-Based Approach*, Morgan
Kaufmann, 2001. Direction/distance formalism: Wolfe, *High Performance Compilers for Parallel
Computing*, 1996; Banerjee, *Dependence Analysis for Supercomputing*, Kluwer, 1988.

**Framework.** For a depth-`d` loop nest, an **iteration vector** `i=(i1,…,id)` identifies one
body execution; iterations are ordered lexicographically. A **dependence** between two
references to the same array in iterations `i` (source) and `j` (sink) exists if they touch
the same location and ≥1 is a write. Types: **flow/true** (RAW), **anti** (WAR), **output**
(WAW).

- **Distance vector** `D = j − i` (e.g. `A[i-1]` vs `A[i]` → distance 1).
- **Direction vector** abstracts each component to `<` / `=` / `>` / `*`.
- **Loop-carried at level k** iff the first non-`=` direction is at position k (and is `<`) —
  the dependence flows *across* iterations of loop k. **Loop-independent** iff all `=` (both
  accesses in the *same* iteration).

**Why it gates transformations.** Vectorizing/parallelizing loop k is legal iff there is **no
loop-carried dependence at level k** (a `<` there means iteration i+1 needs a value from i, so
they can't be one SIMD lane group). **Interchange** of k, k+1 is legal iff no dependence has
direction `(<,>)` in those positions. **Tiling** requires the fused loops be fully permutable
(all distances non-negative). The problem reduces to: does the **subscript system** have an
integer solution in bounds? For `A[a0+a1*i]` vs `A[b0+b1*j]`, a dependence exists iff
`a0+a1*i = b0+b1*j` has an in-bounds integer solution satisfying the tested direction. Three
tests trade precision for cost:

**GCD test.** The Diophantine equation `a1*i − b1*j = b0 − a0` has an integer solution **iff
`gcd(a1,b1)` divides `(b0 − a0)`**. If not → **proven independent**. If yes → inconclusive
(ignores bounds/directions). Cheap first filter.
> `for i: A[2*i] = A[2*i+1]` needs `2i = 2j+1` → `2i−2j=1`; `gcd(2,2)=2` ∤ 1 → **no
> dependence**, vectorizable (even ≠ odd).

**Banerjee test.** Banerjee, *Dependence Analysis for Supercomputing*, 1988. Bounds- and
direction-aware. Form `h(i,j) = (a0+a1*i) − (b0+b1*j)`; compute its real **min/max** over the
loop-bound box *specialized to a direction* (substitute the direction constraint, e.g. `i<j`,
into the bounds) using Banerjee's inequality (split each coefficient into positive/negative
parts). If `0 ∉ [h_min, h_max]` → **no dependence in that direction**. Else possible (real
relaxation ⇒ can report a spurious dependence when only a non-integer solution exists).
Combined with GCD in practice.
> `for i=1..100: A[i+100] = A[i]+1` needs `j−i=100` with `1≤i,j≤100`. Max of `j−i` = `99 <
> 100` → **no dependence**; vectorizable even though GCD (`gcd(1,1)=1 | 100`) could not tell.

**Omega test.** Pugh, "The Omega Test," Supercomputing '91 (expanded CACM 35(8), 1992). Exact
integer programming over **Presburger arithmetic**. The dependence + direction constraints
form a linear integer equality/inequality system; Omega decides integer feasibility via
integer-exact equality elimination (with mod-based *tightening*) and integer **Fourier–Motzkin**
projection augmented with:
- **real shadow** (FM over reals — no solution ⇒ no dependence, sound);
- **dark shadow** (shrunken projection — integer solution ⇒ dependence exists, sound);
- an exact **splinter** case-split for the gap between them.

Exact for **affine** subscripts + affine bounds (no false dependences *or* false
independences), and competitive in speed with GCD/Banerjee on real code.
> `for i: A[2*i] = A[2*i−2]` needs `i = j−1`. GCD (`gcd(2,2)=2 | 2`) and Banerjee (real
> solution) are both inconclusive; Omega proves `i=j−1` integer-feasible → **true
> loop-carried flow dependence, distance 1** (blocks vectorization).

**Production compilers.**
- **LLVM** `DependenceAnalysis.cpp` — Banerjee + GCD, classifying each subscript pair as
  **ZIV** (constants), **SIV** (single index — fast exact strong/weak-zero/weak-crossing
  sub-tests), or **MIV** (falls back to `banerjeeMIVtest`/`gcdMIVtest`). This is the
  Goff–Kennedy–Tseng "Practical Dependence Testing" (PLDI 1991) hierarchy.
  `LoopAccessAnalysis.cpp` is what the *vectorizer* consumes: SCEV-based, and where it cannot
  statically prove independence it emits **runtime alias checks** ("memchecks") guarding a
  vectorized loop version. **Polly** uses **ISL** (a full Presburger engine, the Omega
  lineage) for exact polyhedral dependence.
- **GCC** `tree-data-ref.cc` (distance/direction vectors, subscript classification), `omega.cc`
  (Pugh's Omega, used historically by Graphite/Lambda), and vectorizer runtime alias checks in
  `tree-vect-data-refs.cc`. Non-affine subscripts (`A[B[i]]`, `A[i*j]`) fall outside all three
  tests → runtime checks or bail.

---

## 3. Strength Reduction

Replace expensive ops (multiply) with cheaper ones (add/shift). Two distinct settings:
straight-line code and induction-variable loops.

### 3.1 Straight-Line Strength Reduction (SLSR)

LLVM `StraightLineStrengthReduce` (`-slsr`). Header: "reduce arithmetic redundancy in
straight-line code instead of loops." It targets code usually coming *from an unrolled loop*,
where deltas between similar expressions are the same across unrolled copies and can be reused,
turning a chain of independent multiplies into one multiply plus cheap adds.

**Candidate forms.** Expressions canonicalized into one of three, with `S` an integer
*variable* (stride), `i` a *constant* integer (index), `B` the base:
1. **Add:** `B + i * S`
2. **Mul:** `(B + i) * S`
3. **GEP:** `&B[..][i * S][..]` (array indexing with scaled offsets)

**Basis relation.** Candidate `C1` **is a basis for** `C2` when they share CandidateKind,
Base, and Stride, and `C1` **dominates** `C2` (so the basis's value is available where `C2` is
rewritten). A map `(Kind, Base, Stride) → candidates` finds a dominating basis quickly.

**The three delta rewrites** (verbatim from the LLVM header comment):

```
Index delta (same base and stride):
  S1: X = B + i  * S
  S2: Y = B + i' * S   =>   X + (i' - i) * S

Base delta (same stride and index):
  S1: X = B  + i * S
  S2: Y = B' + i * S   =>   X + (B' - B)

Stride delta (same base and index):
  S1: X = B + i * S
  S2: Y = B + i * S'   =>   X + i * (S' - S)
```

`X` is the basis's already-computed value; the rewrite replaces a full `B + i'*S` (which
contains a multiply) with `X + <delta>`. Since `(i'−i)` is a compile-time constant in the
index-delta case, `(i'−i)*S` is a strength-reduced small multiply (often a shift), and when
deltas are equal across unrolled iterations the multiply degenerates to repeated adds.

```
collect_candidates(F):
  for inst in F (dominator-tree order):
     if matches Add/Mul/GEP form:
        C = Candidate{kind, base, index (const), stride, inst}
        for B in bucket[(C.kind, C.base, C.stride)]:
           if B dominates C.inst:  C.basis = best(C.basis, B)
        bucket[(C.kind,C.base,C.stride)].push(C)
        candidates.push(C)
rewrite():
  for C in candidates (bases first):
     if C.basis:
        delta  = pick_delta_case(C, C.basis)          # index/base/stride
        newval = C.basis.value + delta_expr           # add/sub + small const mul
        RAUW(C.inst, newval)
```

Before (from unrolled loop) → after (index-delta, `%p0` chosen as basis, delta reused):

```llvm
%a0=mul %i0,%s; %p0=add %base,%a0        %p0 = ...                      ; one mul retained
%a1=mul %i1,%s; %p1=add %base,%a1   →    %d  = mul %delta_const, %s     ; (i1-i0)*S
%a2=mul %i2,%s; %p2=add %base,%a2        %p1 = add %p0, %d
                                         %p2 = add %p1, %d              ; same delta, no new mul
```

The 2024 redesign (commit `f67409c`, PR #162930) reworked candidate selection to prefer the
highest-benefit basis and added **path compression** (rewrite directly against a
further-dominating basis) to shorten dependency chains and improve ILP. GCC's analog is
`tree-ssa-strength-reduction.c` (Cooper, Eckhardt, Kennedy, "Redundancy Elimination
Revisited," PACT 2008 — same `basis + index*stride` candidate families).

### 3.2 Classic loop strength reduction (contrast)

Cocke & Kennedy, "An Algorithm for Reduction of Operator Strength," CACM 20(11):850–856, 1977;
Allen, Cocke, Kennedy, "Reduction of Operator Strength," in *Program Flow Analysis*,
Prentice-Hall, 1981. Operates on **induction variables**: a basic IV `i` is incremented by a
loop-invariant constant per iteration; a derived IV `j = a*i + b` (a, b loop-invariant) has its
per-iteration multiply `a*i` replaced by a new IV `t` initialized to `a*i0 + b` before the loop
and incremented by `a*c` each iteration. Region-based over the loop nesting; followed by
**linear-function test replacement** rewriting the exit test in terms of the new IV so the
original IV dies.

```c
for (i=0;i<n;i++) s += A[i];   // A[i] addr = &A + i*4  (a multiply/iter)
```
→
```c
p = &A;
for (i=0;i<n;i++){ s += *p; p += 4; }   // t += a*c per iteration
```

**LLVM `LoopStrengthReduce`** (`-loop-reduce`) runs late (pre-codegen) on **SCEV**: each IV is
a `SCEVAddRecExpr` `{start,+,step}<loop>`. It enumerates **Formulae** per use site
(`Formula = BaseRegs + Scale·ScaledReg + BaseImmediate`), canonicalizing the current loop's
addrec into `ScaledReg`, then solves a cost-model optimization (weights: `NumRegs`,
`AddRecCost`, `NumIVMuls`, `NumBaseAdds`, `ImmCost`, `SetupCost`, plus TTI
`isLegalAddressingMode` to fold offsets into addressing modes). GCC's analog is `ivopts`
(`tree-ssa-loop-ivopts.c`). Related: Kennedy et al., "Strength Reduction via SSAPRE," CC 1998
(SR as a variant of PRE on SSA).

---

## 4. Scalarization & Memory Optimization

Move memory traffic into registers, and eliminate provably redundant loads/dead stores. All
of §4.2–4.4 share one substrate: **MemorySSA**.

### 4.1 Array scalarization / scalar replacement of array elements

Carr & Kennedy, "Scalar Replacement in the Presence of Conditional Control Flow," *Software—
Practice & Experience* 24(1):51–77, 1994. Promotes a *reused* array element (referenced
multiple times, or across iterations) into a scalar register candidate, turning redundant
memory accesses into register accesses. Reuse is detected via **dependence analysis** (a
true/input dependence between two subscripted references with consistent subscript distance
means the second can reuse a register value); across iterations, a value produced in iteration
`i` and reused in `i+k` is carried in `k` rotating scalar temporaries initialized in a
prologue. The 1994 contribution handles **conditional control flow** inside the body by
**mapping the problem onto partial redundancy elimination** (PRE availability/anticipability
dataflow), inserting loads only where needed and avoiding speculative loads on paths that don't
need them.

```c
for (i=1;i<n;i++) A[i] = A[i-1] + B[i];   // A[i-1] reloaded each iter
```
→
```c
t = A[0];
for (i=1;i<n;i++){ t = t + B[i]; A[i] = t; }   // A[i-1] load eliminated
```

**How LLVM does it.** There is no single "scalar replacement of array elements" pass; the
effect is a combination:
1. **`mem2reg` / SROA** promotes `alloca`s to SSA via Cytron et al. dominance-frontier SSA
   construction — this handles *stack slots*, not array-element scalarization.
2. **LICM register promotion** (`LICM.cpp`, `promoteLoopAccessesToScalars`) is the true analog
   of Carr-Kennedy: find loads/stores to a **must-alias**, loop-invariant location; prove it
   safe (no aliasing clobber via MemorySSA/alias sets; dereferenceable; stores guaranteed to
   execute or the location writable so no new fault); **hoist a single load into the
   preheader**, **sink a single store into the exits**, thread the value through SSA phis.

```llvm
loop:                              preheader:
  %v = load i32, ptr %p             %v0 = load i32, ptr %p       ; single hoisted load
  %n = add i32 %v, 1          →    loop:
  store i32 %n, ptr %p              %v = phi [%v0,%preheader],[%n,%loop]
                                    %n = add i32 %v, 1           ; no load/store in loop
                                   exit:
                                    store i32 %n, ptr %p         ; single sunk store
```

**SROA vs array-element scalar replacement** (the crux):

| | **SROA** (`SROA.cpp`) | **Array-element scalar replacement** (Carr-Kennedy / LICM promotion) |
|---|---|---|
| Operates on | `alloca` (aggregates: structs; arrays with constant indices) | *any* location (heap/global/param) via loads/stores with predictable subscripts |
| Basis | splits aggregate alloca → per-field allocas, then mem2reg | dependence/availability proves reuse; threads value through SSA phis |
| Needs alias analysis? | no (alloca identity known) | yes (must prove must-alias + no clobber) |
| LLVM pass | `SROA` + `mem2reg` | `LICM` promotion (MemorySSA), `GVN` for straight-line reuse |

SROA is **allocation-based** (it knows the exact object); array-element scalar replacement is
**dependence/alias-based** (it must *prove* two subscripted references touch the same element
with no intervening write). GCC: SROA analog `tree-sra`; array-element/inter-iteration reuse
analog **predictive commoning** `tree-predcom.c` (`-ftree-predcom`), plus `tree-ssa-loop-im`.

### 4.2 MemorySSA — the substrate

LLVM `MemorySSA` (docs: llvm.org/docs/MemorySSA.html; George Burgess IV & Daniel Berlin).
Replaces the older `MemoryDependenceAnalysis`. Imposes SSA on **memory as a single virtual
value** (one partition — trading precision for near-linear speed; precise per-location
partitioning would need ~N² virtual variables). Node kinds:

- **MemoryDef** — may modify memory or impose ordering (stores, `memset`/`memcpy`, general
  calls, volatile/atomic loads, fences). Takes one operand = the memory version *before* it
  (its **defining access**, the nearest dominating def/phi) and *produces* a new version. Defs
  chain up the dominator tree.
- **MemoryUse** — reads only (plain loads, readonly calls). One operand → the def whose value
  it reads. Produces no new version.
- **MemoryPhi** — one per block where ≥2 memory versions reach it; merges reaching defs like a
  value phi.
- **liveOnEntry** — sentinel def dominating everything (memory on function entry).

```llvm
; 1 = MemoryDef(liveOnEntry)
store i32 0, ptr %p
; MemoryUse(1)
%a = load i32, ptr %q
; 2 = MemoryDef(1)
store i32 1, ptr %r
```

**The clobber walker.** `getClobberingMemoryAccess(MA)` walks *up* the def chain / through
phis and returns the nearest access that actually **clobbers** the queried location — the first
dominating def whose `MemoryLocation` may-alias the query per AA; provably-non-aliasing
intervening defs are skipped. Results cached. This is the primitive all elimination passes call.

```
getClobbering(start, Loc):
  cur = start.definingAccess
  while cur is not liveOnEntry:
     if cur is MemoryPhi: cur = resolve_phi(cur, Loc)   # nearest common clobber
     if AA.alias(memoryLoc(cur), Loc) != NoAlias: return cur
     cur = cur.definingAccess
  return liveOnEntry
```

### 4.3 Redundant load elimination & load PRE

In `GVN` and `EarlyCSE`. For a load `L` at location `X`, get its clobbering access:
- clobber is a **store** to `X` (compatible type) → **store-to-load forwarding**: RAUW `L`
  with the stored value.
- clobber is a prior **load** of `X` (no intervening write) → **load-to-load forwarding**.
- GVN also forwards `memset`→load, `memcpy`-from-constant-global→load, and partially forwards
  when a wider store covers a narrower load (shifts/truncs).

**Load PRE** (partial redundancy elimination of loads). When a load is redundant on *some*
paths but not all, GVN uses a non-local query to find a per-predecessor available value,
**PHI-translates** the load's address into each predecessor's value space, **inserts a load
into the predecessors lacking the value**, then replaces the original with a **phi**:

```llvm
if:   store i32 42, ptr %p          if:   store i32 42, ptr %p; br %merge
      br %merge                      else: %v.pre = load i32, ptr %p   ; inserted
else: br %merge                →           br %merge
merge:%v = load i32, ptr %p          merge:%v = phi [42,%if],[%v.pre,%else]  ; original removed
```

### 4.4 Dead store elimination & MemCpyOpt

**`DeadStoreElimination`** (`-dse`), MemorySSA-backed since D72700 (Jan 2020), default Sep 2020
(`fb109c4`). For each killing store `K` to `X`, walk *upward* to an earlier `MemoryDef` `D`
writing a location fully covered by `K`, verifying **no `MemoryUse` between that may read `X`**;
if so, delete `D`. Handles full/partial overwrites, dead stores at function end, and stores to
non-escaping objects.

```llvm
store i32 1, ptr %p     ; dead — overwritten below, no read between
store i32 2, ptr %p     →   store i32 2, ptr %p
```

**`MemCpyOpt`** uses MemorySSA to forward `memcpy`→`memcpy`, turn store-of-load into `memcpy`,
convert `memcpy` of a just-`memset` buffer, and do **call-slot optimization** (callee writes
directly into the final destination) — all predicated on clobber checks proving no interference.

> **Alias analysis** (Andersen/Steensgaard/TBAA/points-to) — see
> [`compilers_and_jit.md`](@/notes/programming/compilers_and_jit.md) §2. MemorySSA consumes AA results for its
> clobber walk; the two are complementary layers.

---

## 5. Instruction Scheduling & Loop Restructuring

### 5.1 The VLIW model — why the compiler owns scheduling

A **VLIW (Very Long Instruction Word)** processor issues a fixed-format word naming *multiple
independent operations*, one per functional-unit slot, all in one cycle. Unlike a superscalar
out-of-order core (see [`../hardware/superscalar_ooo_cpu.md`](@/notes/hardware/superscalar_ooo_cpu.md)),
the hardware does **no** dynamic dependency checking, renaming, or scheduling — in the pure
model, no interlocks. The *compiler* guarantees packed operations are independent and that
inter-op latencies are respected. VLIW = "software does what the OOO scheduler does, with a
whole-program view and no runtime cost."

Consequences:
- **Exposed latencies.** A load of latency L is unreadable for L cycles; the compiler fills
  them (independent ops or NOPs). Some VLIWs expose branch delay slots (TI C6000: 5).
- **No hazard interlock** (strict model) — a wrong schedule is a *correctness* bug. Real
  designs (Hexagon, later Itanium) reintroduce stop-bits/interlocks for binary portability.
- **ILP within a basic block is tiny** (~1.5–2 for control-heavy integer code — Fisher's
  motivating observation), so VLIW compilers *must* schedule across branches. That is the
  reason trace/superblock/hyperblock/modulo scheduling exist. VLIW descends from **horizontal
  microcode** (wide microinstructions each controlling many datapath fields).

### 5.2 List scheduling (the local kernel)

Every region method ends up calling list scheduling on a single-entry region.

1. **Build the data-dependence DAG** — nodes = ops; edges = RAW (latency = producer result
   latency), WAR/WAW (usually 0–1), and memory deps via alias analysis.
2. **Priorities** — **critical-path height** `h(n)` (longest latency-weighted path to a sink,
   highest first), or **mobility/slack** `ALAP(n) − ASAP(n)` (lowest first). Tie-break by
   successor count, resource scarcity, source order.
3. **Cycle-by-cycle greedy issue** against a **resource reservation table** (`cycle × FU`,
   often a per-FU pipeline FSA — LLVM `llvm-mca` and GCC's DFA scheduler both use FSA models).

```
list_schedule(DDG, resources):
  for n: compute height(n), preds_remaining(n)
  Ready = { n : preds_remaining(n) == 0 };  cycle = 0
  while Ready or InFlight:
     for n in sorted(Ready by priority):
        if resources.available(n, cycle):
           schedule n at cycle; resources.reserve(n, cycle); Ready.remove(n)
           ready_time(succ) = cycle + latency(n->succ)  for each succ
     cycle += 1
     Ready += { m : all preds scheduled and ready_time(m) <= cycle }
```

Forward (top-down) minimizes latency from entry; backward (bottom-up) schedules from the sink
(good when the tail bottlenecks). The lesson it encodes: **hoist long-latency ops (loads) as
early as dependences allow, fill their shadows with independent work.**

### 5.3 Trace scheduling (Fisher)

Fisher, "Trace Scheduling: A Technique for Global Microcode Compaction," IEEE Trans. Comput.
C-30(7):478–490, 1981 (commercialized in **Multiflow TRACE**; reference implementation is
Ellis's **Bulldog**, Yale PhD 1985). Schedule a whole **trace** — a maximal acyclic path of
blocks likely to execute together — as one big block, then repair off-trace paths.

1. **Trace selection** by profile weights: greedily grow the hottest path forward to the
   most-likely successor and backward to the most-likely predecessor, stopping at back-edges,
   scheduled blocks, or cold edges.
2. **Schedule the trace** with list scheduling, *ignoring* interior block boundaries.
3. **Bookkeeping / compensation code** for correctness on off-trace edges:
   - **Split** (branch leaves the trace): an op moved *below* a branch that the off-trace path
     needed → insert a **copy** on the off-trace edge. Moving a side-effecting op *above* an
     exit is **speculation** (needs safety).
   - **Join** (side entry merges in): ops moved *above* the join → insert copies on the
     entering edge. Trace scheduling classically restricts side entrances (Multiflow disallowed
     them). Compensation code can *increase* code size; the win is on the dominant path.

### 5.4 Superblock & hyperblock (IMPACT)

Hwu et al., "The Superblock," *J. Supercomputing* 7(1):229–248, 1993; Mahlke et al., "Effective
Compiler Support for Predicated Execution Using the Hyperblock," MICRO-25, 1992.

**Superblock** = a trace with a *single entry and no side entrances* (multiple exits allowed).
The simplification over trace scheduling: eliminate the join-repair problem by **tail
duplication** — any block in the trace with a side entry is *duplicated* so the trace copy has
only the on-trace predecessor; off-trace predecessors branch to the duplicate. Trades **code
growth** for **scheduling freedom** (scheduler handles only the simpler downward-motion-past-
exit / speculation case) and enables superblock-scope classical opts (CSE, LICM, unrolling).

**Hyperblock** = single-entry, multi-exit region formed by **if-conversion + predication**,
merging multiple control paths (both sides of a branch) into one predicated straight-line
region. `if (c) x=a; else x=b;` → `p = cmp c; (p) x=a; (!p) x=b;` — both ops issue, only the
true-predicate one commits. Removes hard-to-predict branches and exposes both paths to one
scheduler, at the cost of issue slots on the not-taken side (IMPACT heuristics exclude rarely-
taken/hazardous/resource-heavy paths).

| Region | Entries | Exits | Side entries | Off-path repair | Branches |
|---|---|---|---|---|---|
| Basic block | 1 | 1 | n/a | none | none crossed |
| Trace (Fisher) | 1 | many | allowed | split + join compensation | stay |
| Superblock (Hwu) | 1 | many | eliminated (tail dup) | split (speculation) only | stay |
| Hyperblock (Mahlke) | 1 | many | eliminated + if-converted | predicate-guarded | → predicates |

Region scheduling / treegions (Bernstein & Rodeh, "Global Instruction Scheduling for
Superscalar Machines," PLDI 1991) generalize to tree-shaped single-entry regions, avoiding
tail-duplication cost.

### 5.5 Modulo scheduling / software pipelining (Rau)

Rau, "Iterative Modulo Scheduling," MICRO-27, 1994 (HP Labs HPL-94-115); Lam, "Software
Pipelining," PLDI 1988 (modulo variable expansion, hierarchical reduction); original Rau &
Glaeser, MICRO-14, 1981. See [`compilers_and_jit.md`](@/notes/programming/compilers_and_jit.md) §5 for the brief;
the mechanism:

Overlap successive iterations so a new one starts every **II (Initiation Interval)** cycles.
Steady state = the **kernel**, preceded by a **prologue** (fills the pipeline) and followed by
an **epilogue** (drains it).

```
Sequential (4 cyc/iter):        Pipelined, II=1:
 it0: A B C D                   prologue: A0 / A1 B0 / A2 B1 C0
 it1:         A B C D           kernel :  Ai Bi-1 Ci-2 Di-3   (1 iter/cycle)
 it2:                 A B C D   epilogue: drains B,C,D of last iters
```

**MII = max(ResMII, RecMII).**
- **ResMII** (resource): per resource class, `ceil(uses_per_iter / instances)`; max over
  resources. (3 mem ops, 2 mem units → `ceil(3/2)=2`.)
- **RecMII** (recurrence): for each elementary cycle `θ` in the dependence graph, `ceil(
  latency(θ) / distance(θ))`, max over cycles. `x[i]=x[i-1]+1` with add latency 2 →
  `ceil(2/1)=2` (the loop-carried dep bottlenecks throughput at 2 cyc/iter no matter how many
  FUs). `x[i]=x[i-2]+1` → `ceil(2/2)=1`.

**Modulo Reservation Table (MRT)** — `II` rows (cycle mod II) × one column per resource. An op
at absolute cycle `t` reserves `MRT[t mod II][resource]`. **Modulo constraint**: no two ops
reserve the same (cycle-mod-II, resource) slot — guaranteeing that when all iterations overlay,
no resource is oversubscribed in steady state.

```
for II = MII, MII+1, ...:
   reset MRT; unschedule all ops
   priority = height in DDG;   budget = c * num_ops
   while some op unscheduled and budget-- > 0:
      n = highest-priority unscheduled op
      t_early = max over scheduled preds p of (time(p) + latency(p,n) - II*distance(p,n))
      find slot t in [t_early, t_early+II-1] with free MRT resources
      if found: schedule n at t
      else:                                  # forced placement (min-conflicts)
         t = t_early; unschedule MRT-conflicting ops; schedule n at t
   if all scheduled: emit prologue/kernel/epilogue; break
```

The eject-and-reschedule escapes local minima without exhaustive backtracking. **Swing Modulo
Scheduling** (Llosa et al., PACT 1996) orders nodes to reduce register pressure — it is GCC's
`-fmodulo-sched` (SMS).

**Modulo Variable Expansion & rotating registers.** With iterations overlapped, a value from
iteration `i` is still live when `i+1` produces its own version of the "same" register. Fixes:
- **MVE (Lam 1988)** — unroll the kernel `k = ceil(liverange/II)` times, rename to `k`
  physical registers in rotation. Software-only; costs code size + registers.
- **Rotating register files (Itanium)** — a base register decrements each kernel iteration so
  `r32` names a different physical register per iteration. `br.ctop`/`br.wtop` rotate the base
  and predicates, making MVE free *and* generating prologue/epilogue implicitly (rotating
  predicates gate which pipeline stages are active during fill/drain).

### 5.6 Predication & speculation

- **Full predication** (Itanium, Hexagon dot-new): nearly every op has a qualifying predicate;
  it executes but commits only if true — enables if-conversion → hyperblocks. Itanium has 64
  predicate regs set by `cmp` producing `(p, !p)`.
- **Control speculation** — hoist a load *above* its guarding branch. Itanium **speculative
  load `ld.s`** sets a **NaT (Not-a-Thing)** poison bit instead of faulting; a later `chk.s`
  branches to recovery if NaT is set. NaT propagates through computation.
- **Data speculation** — hoist a load above a maybe-aliasing store. Itanium **advanced load
  `ld.a`** records the address in the **ALAT (Advanced Load Address Table)**; a later
  `ld.c`/`chk.a` verifies no intervening store hit it, else re-executes. This does in software
  what an OOO core's memory-dependence predictor + load-store queue do in hardware.

### 5.7 Bundle formation & real ISAs

The compiler must emit a fixed-width word every cycle, so empty slots become **NOPs** (code-
size + I-cache cost — a real VLIW downside). Compression schemes:

**Itanium / IA-64 EPIC** — 128-bit **bundle** = 5-bit **template** + three 41-bit **syllables**:

```
 127                                                          5 4      0
+-------------+-------------+-------------+----------------------+------+
| slot 2 (41) | slot 1 (41) | slot 0 (41) |                      |templ |
+-------------+-------------+-------------+----------------------+------+
   template selects FU types per slot + positions of stop bits ';;'
```
Unit types M (memory), I (integer), F (float), B (branch), L+X (long `movl`, two slots). Each
syllable carries a 6-bit qualifying predicate. 128 GPRs (r32–r127 rotate), 64 predicate regs,
NaT bit per GR, ALAT, rotating registers + `br.ctop` for pipelined loops.

**TI C6000** VLIW DSP — 8 FUs in 2 datapaths (`.L/.S/.M/.D` × 2). 256-bit **fetch packet** =
eight 32-bit instructions; each instruction's **bit 0 = `p`-bit**: `p=1` = "execute in parallel
with next" (`||`), grouping into a 1–8-instruction **execute packet** (no NOP padding for serial
code):

```
 31                                   1   0
+--------------------------------------+---+
|              instruction              | p |   p=1 -> || with next
+--------------------------------------+---+
Fetch packet = [I0 I1 I2 I3 I4 I5 I6 I7]  (8 x 32b = 256b)
```
Conditional exec via 3-bit `creg` + `z` (`[A1] add ...`). **Exposed delay slots**: multiply 1,
load 4, branch 5 — the compiler fills them.

**Qualcomm Hexagon** — variable-length **packet** of 1–4 instructions delimited by 2 parse
bits per 32-bit instruction. **Dot-new predicates / new-value operands**: a predicate/register
produced earlier *in the same packet* is consumable later in it (`p0=cmp...; if (p0.new)
jump`, new-value stores `memw(...)=R.new`) — compare + dependent branch/store in one packet
with no bubble. **Hardware loops** (`loop0`/`loop1`) give zero-overhead loop branches; **HVX**
vectors issue in the same packet model.

**Multiflow TRACE** (historical) — 7/14/28 operations per instruction (256/512/1024-bit
words), built by Fisher/Colwell to commercialize trace scheduling.

**Why general-purpose VLIW lost (Itanium post-mortem):** statically scheduling around
*unpredictable* memory latency (cache misses vary at runtime) is something only dynamic OOO
handles well; NOP/template code bloat hurt I-cache; latencies baked into binaries break cross-
microarchitecture compatibility; OOO superscalar delivered ILP dynamically on legacy x86. VLIW
thrives where behavior is statically predictable and power/area matter: DSPs (C6000), modem/
vector coprocessors (Hexagon), older AMD TeraScale GPUs, ML accelerators.

### 5.8 Loop unrolling

Replicate the body `k` times (unroll factor), adjust the IV/step so `k` original iterations run
per new iteration.

**Benefits**: amortize loop overhead (one branch + counter per `k`); expose ILP (`k`
independent copies fill FU slots / hide latencies — *the* enabler for VLIW list scheduling and
for SLP §2.1); enable CSE/redundant-load-elim/strength-reduction across iterations; feed modulo
scheduling (kernel unroll for MVE) and unroll-and-jam.

**Costs**: I-cache / code-size pressure (a bloated body that no longer fits L1I can be *slower*)
and **register pressure** (`k×` live ranges → spills that erase the ILP gain — the central
tension).

Kinds:
- **Full/complete** — trip count is a small compile-time constant `N`: replace the loop with
  `N` straight-line copies, delete the branch. Only when `N` is small (code-size threshold).
- **Partial** — unroll by `k` (2/4/8) keeping a loop; needs a **remainder/epilogue loop** for
  the `N mod k` leftover.
- **Runtime** — trip count unknown: unrolled main loop + a remainder guarded by a runtime `N
  mod k` and a `N ≥ k` skip check.

```c
main_count = n & ~3;                         // n - (n % 4)
for (i=0; i<main_count; i+=4)                // unrolled x4
    { body(i); body(i+1); body(i+2); body(i+3); }
for (; i<n; i++) body(i);                    // remainder loop, <4 iters
```
(Duff's device is the classic hand-coded remainder; modern compilers emit a separate remainder
loop.)

**Factor selection** balances benefit vs register/I-cache cost: keep body ≤ a threshold; favor
factors dividing a known trip count evenly (no remainder); match issue width / FU count; unroll
≥ recurrence latency for pipelining; back off if estimated register pressure would spill.

**Unroll-and-jam** — for a nest, unroll the outer loop by `k` and **fuse ("jam")** the `k`
inner-loop copies into one, creating register reuse across the unrolled outer iterations (the
basis of BLAS microkernels / register blocking):
```c
for i step 2:
  for j:
     C[i][j]   += A[i]*B[j];
     C[i+1][j] += A[i+1]*B[j];   // B[j] loaded once, reused for both i
```
Legality requires the outer loop unrollable and the jam not to reverse a loop-carried
dependence direction between fused inner instances (checked via §2.3 dependence analysis).

**LLVM `LoopUnrollPass`** (+ `LoopUnrollAndJamPass`, `LoopFullUnrollPass`). Trip counts from
SCEV (`getSmallConstantTripCount`, `getSmallConstantTripMultiple`). Config via
`TargetTransformInfo::UnrollingPreferences` (target hook `getUnrollingPreferences()` + CLI):
`Threshold` (full-unroll budget), `PartialThreshold`, `OptSizeThreshold`, `Partial`, `Runtime`,
`Count`, `MaxCount`/`FullUnrollMaxCount`, `AllowRemainder`, `UpperBound`, `AllowPeeling`,
`PeelCount`. `analyzeLoopUnrollCost()` estimates the unrolled body (with post-unroll constant-
folding); runtime unrolling uses `UnrollRuntimeLoopRemainder()`. Knobs: `-unroll-count`,
`-unroll-threshold`, `-unroll-runtime`, `-unroll-allow-partial`, `-unroll-and-jam`. Metadata:
`llvm.loop.unroll.enable/count/full/disable`, `llvm.loop.unroll_and_jam.count` (from Clang
`#pragma unroll` / `#pragma clang loop unroll(...)`).

**GCC**: `-funroll-loops` (known/computable trip counts; implies `-fweb`,
`-frename-registers`), `-funroll-all-loops` (even uncertain counts; usually harmful),
`-fpeel-loops`, `-floop-unroll-and-jam`. Params: `--param max-unroll-times` (8),
`max-unrolled-insns` (~200), `max-completely-peel-times`, `unroll-jam-min-percent`. `#pragma
GCC unroll N`. RTL loop optimizer `loop-unroll.cc`; GIMPLE complete unrolling
`tree-ssa-loop-ivcanon.cc` (`cunroll`); software pipeliner SMS `-fmodulo-sched`.

---

## 6. Peephole Optimization

McKeeman, "Peephole Optimization," CACM 8(7):443–444, 1965 — coined the term: slide a small
**window ("peephole")** of a few consecutive instructions and replace matched patterns with
cheaper equivalents. Retargetable classic: Davidson & Fraser, "The Design and Application of a
Retargetable Peephole Optimizer," ACM TOPLAS 2(2):191–202, 1980.

### 6.1 Peephole as term rewriting; the superoptimization link

A peephole optimizer is a **term-rewriting system**: `pattern → replacement` applied over a
sliding window to a fixpoint. Concerns: **termination** (rewrites must not loop — enforce a
well-founded order or bounded rounds) and **confluence** (order-independence, usually not
guaranteed; a worklist re-examines neighbors after each rewrite).

**Superoptimization** is the search for the *optimal* short sequence:
- Massalin, "Superoptimizer: A Look at the Smallest Program," ASPLOS 1987 — exhaustive search
  over instruction sequences, verified by testing.
- Bansal & Aiken, "Automatic Generation of Peephole Superoptimizers," ASPLOS 2006 — *learn* a
  peephole rule database offline by enumerating short sequences + their optimal equivalents,
  then apply as a fast pass. Bridges superoptimization ↔ peephole.
- **Souper** (Sasnauskas et al., 2017) — an LLVM-IR superoptimizer using an SMT solver to
  discover missing InstCombine rules. **Alive2** (Lopes, Lee, Hur, Liu, Regehr, PLDI 2021)
  formally verifies InstCombine transforms (catches wrong undef/poison/overflow-flag handling).

### 6.2 Classic transformations

- **Algebraic identities**: `x*1→x`, `x+0→x`, `x&x→x`, `x^x→0`, `x*0→0`, `(x<<a)<<b→x<<(a+b)`.
- **Strength reduction**: `x*2→x<<1`, unsigned `x/2→x>>1`, `x%2^k→x&(2^k-1)`, `mul by const →
  shift/add chains`.
- **Redundant pairs**: `store r,[m]; load [m]→r` ⇒ drop the load; `mov a,b; mov b,a` ⇒ drop the
  second; push/pop cancellation.
- **Null/identity ops**: `add r,0`, `mov r,r`, `or r,0` ⇒ delete.
- **Machine constant folding**: `mov r,2; add r,3 → mov r,5`. Window-local **dead store
  elimination**.
- **Branch optimization**: jump-to-jump chaining (`jmp L1; L1: jmp L2` → target `L2`);
  branch inversion (`jz L1; jmp L2; L1:` → `jnz L2`); unreachable-code/dead-label removal;
  branch-to-next-instruction deletion.
- **Cross-basic-block peephole**: apply rules across a fall-through / single-successor edge
  (fold a compare in one block with a branch in the next), respecting CFG edges + liveness.

### 6.3 LLVM's four-level peephole family

**(a) InstCombine — the canonical IR-level peephole.** `llvm/lib/Transforms/InstCombine/`.
Worklist-driven algebraic simplification on LLVM IR. `visitXxx` per opcode (`visitAdd`,
`visitICmp`, …) returns a replacement or edits in place; changed instrs re-queue their users.
Rules written declaratively via the **`PatternMatch`** DSL (`m_Add(m_Value(A),
m_ConstantInt(C))`, `m_OneUse`, `m_c_Add` commutative). **`SimplifyDemandedBits` + `KnownBits`**
give bit-level reasoning to drop redundant masks. Shares rules with **`InstructionSimplify`**
(the return-an-existing-value subset). Picks *canonical forms* (constants on the RHS, `sub x,C →
add x,-C`) so later passes see uniform IR — a known correctness minefield (undef/poison/nsw/nuw),
hence Alive2.

**(b) DAGCombine — SelectionDAG peephole.** `DAGCombiner.cpp`, run before *and* after type/op
legalization (`visitADD`, `visitLoad`, …), folding target-independent + target-specific DAG
patterns (`TargetLowering::PerformDAGCombine`), forming addressing modes, combining loads/stores.

**(c) MachineInstr peephole — `PeepholeOptimizer`.** `llvm/lib/CodeGen/PeepholeOptimizer.cpp`,
on MachineIR post-ISel/pre-RA: copy propagation (`foldRedundantCopy`), `optimizeCompareInstr`
(drop a redundant `cmp` when a prior op set the flags), `optimizeExtInstr`, `optimizeSelect`,
address-mode folds — via `TargetInstrInfo` hooks. Neighbors: **`MachineCombiner`**
(reassociation/FMA contraction gated by a machine-model *critical-path latency* check),
`MachineCSE`, `MachineSink`.

**(d) GlobalISel Combiner.** `GlobalISel/Combiner.cpp` + `CombinerHelper`, rules in TableGen
(`GICombineRule`), applied on generic MIR — the modern declarative retargetable peephole layer
replacing hand-written SelectionDAG combines for GlobalISel targets.

**Jump threading** — distinct from window-local branch fixups. `JumpThreading.cpp` is a
*global* SSA pass: when a value into a conditional branch is known on a specific predecessor
edge (via `LazyValueInfo`/correlated conditions), it redirects that predecessor directly to the
resolved successor (duplicating the block if needed). Correlated-branch threading lives here;
trivial branch fixups + tail merging live in peephole / `BranchFolder.cpp`.

### 6.4 GCC peephole

- **`define_peephole2`** in the target `.md` — RTL pattern → replacement, matched by the
  `peephole2` pass (post-RA, can allocate scratch regs). Legacy `define_peephole` runs pre-RA.
- **`combine` pass** (`combine.cc`) — GCC's InstCombine analog: combines 2–4 RTL insns into one
  by substitution + `simplify_rtx`, guided by insn costs and `recog`. Most GCC RTL strength-
  reduction/identity folding happens here, alongside `fwprop` and the declarative **`match.pd`**
  GIMPLE pattern DSL (used by `tree-ssa-forwprop` / `gimple-match` — GCC's PatternMatch analog).

> **Register allocation** — see [`compilers_and_jit.md`](@/notes/programming/compilers_and_jit.md) §4
> (Chaitin-Briggs graph coloring, iterated register coalescing, linear scan, regalloc2,
> spilling/rematerialization). Not repeated here.

---

## Key References

### Redundancy elimination
- Cocke, Schwartz. *Programming Languages and Their Compilers.* Courant Institute NYU, 1970.
- Kildall. "A Unified Approach to Global Program Optimization." POPL 1973.
- Alpern, Wegman, Zadeck. "Detecting Equality of Variables in Programs." POPL 1988.
- Rosen, Wegman, Zadeck. "Global Value Numbers and Redundant Computations." POPL 1988.
- Simpson. "Value-Driven Redundancy Elimination." PhD thesis, Rice University, 1996.
- VanDrunen, Hosking. "Value-Based Partial Redundancy Elimination." CC 2004.

### Vectorization & dependence analysis
- Larsen, Amarasinghe. "Exploiting Superword Level Parallelism with Multimedia Instruction Sets." PLDI 2000.
- Rosen, Nuzman, Zaks. "Loop-Aware SLP in GCC." GCC Developers' Summit 2007.
- Mendis, Amarasinghe. "goSLP: Globally Optimized Superword Level Parallelism Framework." ASPLOS 2018.
- Porpodas, Rocha, Góes. "Look-Ahead SLP: Auto-vectorization in the Presence of Commutative Operations." CGO 2018.
- Porpodas, Rocha, Góes. "VW-SLP: Auto-vectorization with Adaptive Vector Width." PACT 2018.
- Porpodas et al. "Super-Node SLP." CGO 2019. / "PSLP: Padded SLP Auto-vectorization." CGO 2015.
- Banerjee. *Dependence Analysis for Supercomputing.* Kluwer, 1988.
- Pugh. "The Omega Test: A Fast and Practical Integer Programming Algorithm for Dependence Analysis." Supercomputing '91; CACM 35(8), 1992.
- Goff, Kennedy, Tseng. "Practical Dependence Testing." PLDI 1991.
- Allen, Kennedy. *Optimizing Compilers for Modern Architectures.* Morgan Kaufmann, 2001.
- Verdoolaege. "isl: An Integer Set Library for the Polyhedral Model." ICMS 2010.

### Strength reduction & scalar/memory optimization
- Cocke, Kennedy. "An Algorithm for Reduction of Operator Strength." CACM 20(11), 1977.
- Allen, Cocke, Kennedy. "Reduction of Operator Strength." In *Program Flow Analysis*, Prentice-Hall, 1981.
- Kennedy, Chow, Dahl, Liu, Lo, Streich. "Strength Reduction via SSAPRE." CC 1998.
- Cooper, Eckhardt, Kennedy. "Redundancy Elimination Revisited." PACT 2008.
- Carr, Kennedy. "Scalar Replacement in the Presence of Conditional Control Flow." Software—Practice & Experience 24(1):51–77, 1994.
- Cytron, Ferrante, Rosen, Wegman, Zadeck. "Efficiently Computing SSA Form and the Control Dependence Graph." TOPLAS 13(4), 1991.
- LLVM Project. "MemorySSA." (George Burgess IV, Daniel Berlin.)

### Instruction scheduling, VLIW & loop unrolling
- Fisher. "Trace Scheduling: A Technique for Global Microcode Compaction." IEEE Trans. Comput. C-30(7):478–490, 1981.
- Ellis. *Bulldog: A Compiler for VLIW Architectures.* PhD thesis, Yale, 1985 (MIT Press, 1986).
- Colwell, Nix, O'Donnell, Papworth, Rodman. "A VLIW Architecture for a Trace Scheduling Compiler" (Multiflow TRACE). ASPLOS II, 1987.
- Bernstein, Rodeh. "Global Instruction Scheduling for Superscalar Machines." PLDI 1991.
- Hwu et al. "The Superblock: An Effective Technique for VLIW and Superscalar Compilation." J. Supercomputing 7(1):229–248, 1993.
- Mahlke, Lin, Chen, Hank, Bringmann. "Effective Compiler Support for Predicated Execution Using the Hyperblock." MICRO-25, 1992.
- Rau, Glaeser. "Some Scheduling Techniques and an Easily Schedulable Horizontal Architecture." MICRO-14, 1981.
- Lam. "Software Pipelining: An Effective Scheduling Technique for VLIW Machines." PLDI 1988.
- Rau. "Iterative Modulo Scheduling: An Algorithm for Software Pipelining Loops." MICRO-27, 1994 (HPL-94-115).
- Llosa, González, Ayguadé, Valero. "Swing Modulo Scheduling: A Lifetime-Sensitive Approach." PACT 1996.
- Fisher, Faraboschi, Young. *Embedded Computing: A VLIW Approach to Architecture, Compilers and Tools.* Morgan Kaufmann, 2005.

### Peephole & superoptimization
- McKeeman. "Peephole Optimization." CACM 8(7):443–444, 1965.
- Davidson, Fraser. "The Design and Application of a Retargetable Peephole Optimizer." TOPLAS 2(2), 1980.
- Massalin. "Superoptimizer: A Look at the Smallest Program." ASPLOS 1987.
- Bansal, Aiken. "Automatic Generation of Peephole Superoptimizers." ASPLOS 2006.
- Sasnauskas et al. "Souper: A Synthesizing Superoptimizer." 2017.
- Lopes, Lee, Hur, Liu, Regehr. "Alive2: Bounded Translation Validation for LLVM." PLDI 2021.

### Textbooks
- Aho, Lam, Sethi, Ullman. *Compilers: Principles, Techniques, and Tools*, 2nd ed. Addison-Wesley, 2006.
- Muchnick. *Advanced Compiler Design and Implementation.* Morgan Kaufmann, 1997.
- Cooper, Torczon. *Engineering a Compiler*, 2nd ed. Morgan Kaufmann, 2011.
- Wolfe. *High Performance Compilers for Parallel Computing.* Addison-Wesley, 1996.
