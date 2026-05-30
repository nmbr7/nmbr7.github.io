+++
title = "Velox Internals"
[taxonomies]
tags = ["database", "query-engine", "vectorized-execution", "cpp", "meta", "velox", "prestissimo", "gluten"]
+++


# Velox: Meta's Unified Execution Engine

**Key paper:** Pedreira, P., Erling, O., Basmanova, M., Wilfong, K., Sakka, L., Pai, K., He, W., Chattopadhyay, B. "Velox: Meta's Unified Execution Engine." PVLDB 15(12): 3372–3384, 2022. doi:10.14778/3554821.3554829.

Velox is a C++ library of reusable vectorized execution building blocks. Not a standalone database — designed to be embedded into query engines. Presto (via Prestissimo), Spark (via Apache Gluten), and PyTorch data pipelines all embed it. Core insight: one high-quality vectorized kernel shared by multiple engines beats per-engine reimplementation.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│              Query Engine (Presto / Spark / custom)          │
│          plans, optimizes, distributes, schedules            │
└───────────────────────────┬─────────────────────────────────┘
                            │  PlanNode tree (Velox IR)
┌───────────────────────────▼─────────────────────────────────┐
│                        Velox Core                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Type System │  │ Vector Engine│  │ Expression Eval  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │   Operators  │  │  Task/Driver │  │  Memory Manager  │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│  ┌──────────────────────────────────────────────────────┐    │
│  │   Connectors: HiveConnector · IcebergConnector       │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

A query enters as a `PlanNode` tree. `LocalPlanner` converts it to a pipeline DAG of `Operator` instances. A `Task` owns execution state; `Driver` threads iterate operators in a pipeline. Data flows as columnar `RowVector` batches (default `kDefaultVectorSize = 1024` rows).

---

## Type System

### TypeKind Enum (velox/type/Type.h)

Every value has a `Type` — a shared pointer to an immutable descriptor. Physical kinds:

| TypeKind | C++ backing | Width |
|---|---|---|
| `BOOLEAN` | `bool` | bit-packed in FlatVector |
| `TINYINT` | `int8_t` | 1 |
| `SMALLINT` | `int16_t` | 2 |
| `INTEGER` | `int32_t` | 4 |
| `BIGINT` | `int64_t` | 8 |
| `HUGEINT` | `int128_t` | 16 |
| `REAL` | `float` | 4 |
| `DOUBLE` | `double` | 8 |
| `VARCHAR` | `StringView` | 16 |
| `VARBINARY` | `StringView` | 16 |
| `TIMESTAMP` | `struct Timestamp` | 16 (int64 seconds + uint64 nanos ∈ [0, 1e9)) |
| `OPAQUE` | `std::shared_ptr<void>` | 16 |
| `UNKNOWN` | `UnknownValue` | 0 |
| `ARRAY`, `MAP`, `ROW`, `FUNCTION`, `INVALID` | complex / sentinel | — |

Logical types layer semantics on top of physical kinds:
- `DATE` → physical INTEGER (days since epoch)
- `INTERVAL DAY TO SECOND` → BIGINT (milliseconds)
- `INTERVAL YEAR TO MONTH` → INTEGER
- `TIME` → BIGINT
- `DECIMAL(p,s)` → BIGINT (p ≤ 18) or HUGEINT (p 19–38)

### Structural Types

```
Type
├── ScalarType<Kind>
├── RowType          — names[]: string, types[]: TypePtr
│                      field lookup: linear scan; lazy hash map only for wide rows
├── ArrayType        — elementType: TypePtr
├── MapType          — keyType: TypePtr, valueType: TypePtr
├── DecimalType      — precision: uint8, scale: uint8
│                      SHORT (p ≤ 18): physical BIGINT
│                      LONG  (p 19–38): physical HUGEINT (int128_t)
└── OpaqueType<T>    — type_index(typeid(T)), registered serializers
```

**RowType field lookup:** stores parallel `std::vector<std::string> names_` and `std::vector<TypePtr> children_`. `getChildIdx(name)` / `containsChild()` are linear by default; a lazy hash map is built only for wide rows with frequent lookups.

**DecimalType boundary:** 10^18 < 2^63 ≤ 10^19, so 18 digits is the largest fitting in int64_t. Physical representation is the raw int64_t / int128_t unscaled integer (e.g., 123.45 with scale 2 → unscaled 12345). The older wrapper classes `UnscaledShortDecimal`/`UnscaledLongDecimal`/`UnscaledInt128Decimal` were removed; helpers live in `DecimalUtil`.

**OpaqueType:** `OpaqueType::create<T>()` instantiates a type tagged with `std::type_index(typeid(T))`. On read, checks requested `type_index` before `static_pointer_cast<T>`. `registerSerialization<T>()` allows opaque payloads to cross spill/shuffle boundaries.

**TypeCoercer** (`velox/type/TypeCoercer.h`): planning-time concern. By `Task` creation, implicit casts are already materialized `CastExpr` nodes. `coercible(from, to)` is the structural predicate; dialect-specific coercers (e.g., `prestosql::typeCoercer()`) add rules like BIGINT→REAL.

---

## Vector Memory Layout

### BaseVector

```cpp
// velox/vector/BaseVector.h
class BaseVector {
  TypePtr        type_;
  BufferPtr      nulls_;      // bit-packed: 1 = VALID (not null), 0 = null
  vector_size_t  length_;     // int32_t
  // encoding() → VectorEncoding::Simple
};
```

**Null convention (critical):** bit SET (1) = valid/not-null; bit CLEAR (0) = null. Same as Arrow validity bitmap. Absent `nulls_` buffer = no nulls at all.

### VectorEncoding::Simple (velox/vector/TypeAliases.h)

```
FLAT, CONSTANT, DICTIONARY, BIASED, SEQUENCE, ROW, MAP, ARRAY, LAZY, FUNCTION
```

### FlatVector\<T\> (velox/vector/FlatVector.h)

```
FlatVector<T> : BaseVector
├── values_        : BufferPtr   — sizeof(T) * length_ bytes (bit-packed for bool)
└── stringBuffers_ : vector<BufferPtr>   — only for VARCHAR/VARBINARY
                     holds out-of-line bytes for non-inlined strings
```

### StringView Layout (velox/type/StringView.h)

16 bytes total:

```
Short strings (size ≤ 12):
  [ size: 4 bytes ][ inline chars: 12 bytes ]

Long strings (size > 12):
  [ size: 4 bytes ][ prefix: 4 bytes ][ raw pointer: 8 bytes ]
```

- Short: entire string inlined, no heap deref.
- Long: 4-char prefix enables fail-fast comparison (short-circuit on prefix mismatch); pointer into one of `stringBuffers_`. Arrow Utf8View format uses (buffer-index, offset) instead of a raw pointer — export to pre-Arrow-15 Utf8 requires materialization (contiguous bytes); Arrow 15 Utf8View can share buffers zero-copy.
- `trim()`/`substr()` mutate only the view — zero-copy for substrings.

### DictionaryVector\<T\> (velox/vector/DictionaryVector.h)

```
DictionaryVector<T> : BaseVector
├── indices_          : BufferPtr   — int32_t per row
└── dictionaryValues_ : VectorPtr   — shared base (usually FlatVector)
```

Own optional `nulls_` adds nulls without touching the base. Built via `BaseVector::wrapInDictionary(nulls, indices, size, base)`. Layers nest: `Dict(Dict(Flat))`. The key optimization: if all inputs to an expression share the same indices buffer, evaluate only on the dictionary (unique values) then re-wrap — compresses computation for low-cardinality columns.

### ConstantVector\<T\> (velox/vector/ConstantVector.h)

Scalar form: `T value_` + `bool isNull_` (+ `stringBuffer_` for long strings). Complex form: `VectorPtr valueVector_` + `vector_size_t index_`. Never materializes — `valueAt(i)` ignores `i`. Materialization only happens if an operation explicitly calls `BaseVector::flatten()`.

### BiasVector (velox/vector/BiasVector.h)

Integer compression: `int64_t bias_` + narrow delta buffer (e.g., int16). `valueAt(i) = bias_ + delta[i]`. Used when column's value range fits in fewer bytes than declared type. Produced mostly by readers; decoded transparently by `DecodedVector`.

### SequenceVector (velox/vector/SequenceVector.h)

Run-length encoding: `sequenceValues_` (base vector of run values) + `sequenceLengths_` buffer (length per run). `valueAt(i)` binary-searches `offsets_` (cumulative run-length) to find the run. Analog of Arrow 15's Run-End-Encoding (REE); Arrow stores run-end offsets for O(log n) random access; Velox stores lengths.

### LazyVector (velox/vector/LazyVector.h)

Wraps a `VectorLoader` callback. Materialized only when `loadedVector()` is called or `load(rows, hook)`. Operators force evaluation; filters can push a `ValueHook` so the loader applies the filter during decode (late materialization). Columns never accessed are never decoded.

### SelectivityVector (velox/vector/SelectivityVector.h)

```cpp
std::vector<uint64_t> bits_;   // 64 rows per word
vector_size_t begin_, end_;    // cached range of set bits
```

- `setValid(i, bool)`, `updateBounds()` recomputes range.
- `applyToSelected(lambda)`: word-at-a-time iteration via `__builtin_ctzll`.
- `findFirst()` / `findLast()`: scan words.
- Set algebra: `deselect(other)` (AND-NOT), `select(other)` (OR), `intersect`.
- `ConjunctExpr` progressively deselects rows as conjuncts fail — no intermediate vector materialization.

### BufferPtr / Copy-on-Write (velox/buffer/Buffer.h)

`Buffer` is intrusively reference-counted (`BufferPtr = boost::intrusive_ptr<Buffer>`). `Buffer::unique()` checks refcount. Mutable access (`mutableRawValues<T>()`, `BaseVector::mutableNulls()`) reallocates-and-copies if the buffer is shared — this is the COW point. `AlignedBuffer::allocate<T>(size, pool)` allocates from `MemoryPool` with 64-byte alignment for SIMD.

### resize() behavior

Grows `length_`; if `nulls_` exists and new size exceeds capacity: reallocates (rounds up to whole uint64_t words via `bits::nbytes`), on unique buffer sets newly added bits to valid (1), on shared buffer allocates fresh copy (COW). FlatVector also resizes `values_`.

---

## Memory Management

### MemoryPool Hierarchy (velox/common/memory/MemoryPool.h)

```
MemoryManager (singleton)
└── Root pool         kAggregate  enforces maxCapacity_   [per query, QueryCtx]
    └── Task pool     kAggregate
        └── Node pool kAggregate
            └── Op pool kLeaf   ← only kind that allocates
```

Only `kLeaf` pools allocate; `kAggregate` pools sum children and forbid direct allocation. Only the query root pool enforces the per-query memory limit.

**Quantized reservation** (avoids per-allocation root traffic): leaf pools keep `usedReservationBytes_` (actual) vs `reservationBytes_` (reserved from root). Growth rounds up:
- < 16 MB: +1 MB steps
- 16–64 MB: +4 MB steps
- ≥ 64 MB: +8 MB steps

The slack between reserved and used absorbs small allocations without hitting the arbitrator.

### MemoryAllocator Implementations (velox/common/memory/MemoryAllocator.h)

**MallocAllocator:** forwards to `std::malloc`/`free`. Simple; fragmentation-prone; no RSS cap.

**MmapAllocator:** manages physical pages via `mmap` for explicit RSS control. Nine `SizeClass` objects for pages from 4 KB to 1 MB. Per SizeClass bitmaps:
- `pageAllocated_` — allocated pages
- `pageMapped_` — mapped (not returned to OS)
- `mappedFreeLookup_` — coarse bitmap (1 bit summarizes 512 bits of `pageAllocated_`) for fast free-page search

`SizeMix` (built by `allocationSize()`) names which classes and counts to satisfy a request. Three paths:
1. Small (< `maxMallocBytes`) → `std::malloc`
2. Medium (≤ 1 MB) → SizeClass
3. Large (> 1 MB) → direct contiguous `mmap`

Freed class pages stay mapped (lazy reclaim); unmapped only when total mapped pages hit system limit. Contiguous `mmap` regions can be `MADV_HUGEPAGE`.

### HashStringAllocator / Arena (velox/common/memory/HashStringAllocator.h)

Arena over `memory::Allocation` runs (min 16 KB). Used heavily inside hash tables and aggregation to avoid per-element `malloc`.

**Block layout:**
```
[ Header: 4 bytes ][ data: N bytes ]
```

Header packs size with three flag bits:
- `kFree` — block on free list
- `kContinued` — block is one segment of a non-contiguous multi-part allocation (last 8 bytes = continuation pointer)
- `kPreviousFree` — immediately preceding block is free (enables backward coalescing)

Free blocks form a circular doubly-linked list (`CompactDoubleList`) using 6-byte pointers stored in the block's first 12 data bytes; block size duplicated in the last 4 bytes for backward merge.

Streaming write API: `newWrite()` / `extendWrite()` / `finishWrite()` via `ByteOutputStream`. Read: `prepareRead()` via `ByteInputStream`. `StlAllocator` adapts STL containers onto it.

**Common accumulators built on it:**
- `SingleValueAccumulator` (min/max/arbitrary string): one allocation rewritten via `extendWrite()`.
- `ValueList` (array_agg/map_agg): chain of blocks (linked-list segments) appended via `finishWrite()`; fixed part holds head/tail positions.

### MemoryArbitrator Implementations (velox/common/memory/MemoryArbitrator.h)

**NoopArbitrator:** no reclamation; growth fails immediately on cap.

**SharedArbitrator** (`velox/common/memory/SharedArbitrator.h`): global fair sharing across queries. Serializes arbitration (one request at a time for consistent snapshot).

**Reclaim / reclaimable-task selection algorithm** — on `growPool(requestor, bytes)`:

1. **Local arbitration first** (if requestor would exceed its own `maxCapacity_`): reclaim from the requestor's own pools — shrink free capacity (fast, no spill), then invoke spillable operators' `reclaim()`.

2. **Global arbitration** over candidate pools, three sorted passes:
   - Pass 1: sort by **free capacity** (largest first); shrink unused reservation — no spill, fast.
   - Pass 2: if still short, sort by **memory usage** (largest first); invoke `reclaim()` (disk spill / TableWriter flush). Skip candidates whose reclaimable used capacity is below threshold (avoid tiny-yield churn).
   - Pass 3: if still short, sort by **memory capacity**; `handleOOM()` aborts the candidate with the largest capacity as victim.

**Reclaim safety:** arbitrator calls `Task::requestPause()` before reclaiming. Operators expose `nonReclaimableSection_` (set by default inside Driver loop; cleared at safe points before `maybeReserve`). Arbitrator skips operators inside non-reclaimable sections.

**Spillable operators** that override `Operator::reclaim()`: `OrderBy`, `HashBuild`, `HashAggregation`, `RowNumber`, `TopNRowNumber`, `MarkDistinct`, `Window`, `TableWriter`.

### Spilling (velox/exec/Spill*)

**Spill file format:** `VectorStreamGroup` serializes `RowVector` batches (same format as shuffle pages — encoding-preserving). `SpillFileList` writes; `SpillPartition::createReader()` / `createOrderedReader()` restore. Classes: `Spiller`, `SpillState`, `SpillPartition`, `SpillFile`/`SpillFileList`, `SpillWriter`/`SpillReader`.

---

## Task / Driver / Pipeline Execution

### PlanNode → Pipeline

`LocalPlanner::plan()` walks the `PlanNode` tree bottom-up, emitting pipelines. A **pipeline** is a linear chain of operators with no repartition. Pipeline breaks occur at `LocalExchangeNode`, `MergeExchangeNode`, and `HashJoinNode` (build side becomes a separate pipeline).

```
PlanNode tree:            → Pipeline 0:
  AggregationNode            [TableScanOp] → [FilterOp] → [AggregationOp]
    └─ FilterNode
         └─ TableScanNode
```

### Task

`Task` owns:
- PlanNode tree + `QueryCtx`
- `DriverFactory` per pipeline
- `std::vector<std::shared_ptr<Driver>>` — one per pipeline × parallelism degree
- Split queue: `BlockingQueue<Split>` per source operator
- Synchronization: `std::mutex`, `std::condition_variable` for driver state transitions

### Driver::runInternal() Loop (velox/exec/Driver.cpp)

```
while (true) {
  check for blocked operators → return ContinueFuture if any blocked
  
  op = findNextReady();
  output = op->getOutput();
  if (output) { nextOp->addInput(output) }
  
  if (op->isFinished()) { propagate noMoreInput downstream }
  if (shouldYield()) { re-enqueue driver, yield thread }
}
```

Blocking I/O uses `folly::SemiFuture` (alias `ContinueFuture`): blocked operator returns future from `isBlocked()`, driver yields thread back to `folly::Executor` thread pool, driver re-queued when future completes. No thread parking.

`BlockingReason` values: `kWaitForConsumer`, `kWaitForSplit`, `kWaitForExchange`, `kWaitForJoinBuild`, `kWaitForMemory`, `kYield`.

### Operator Interface

```cpp
class Operator {
  virtual void       addInput(RowVectorPtr input)   = 0;
  virtual RowVectorPtr getOutput()                  = 0;
  virtual bool       needsInput() const             = 0;
  virtual void       noMoreInput()                  = 0;
  virtual bool       isFinished()                   = 0;
  virtual BlockingReason isBlocked(ContinueFuture*) = 0;
};
```

### FilterProject Fusion (velox/exec/FilterProject.cpp)

`FilterNode` directly feeding `ProjectNode` collapses into one `FilterProject` operator with a single `ExprSet` containing both filter predicate and projection expressions. Filter evaluates first → `SelectivityVector` active rows → passed directly to projection ExprSet. No intermediate `RowVector` materialized between filter and project. `hasFilter_` bool selects the path.

### TableScanOperator + LazyVector

Scan produces columns as `LazyVector`s wrapping a `VectorLoader`. Downstream evaluation forces materialization via `loadedVector()`. Filters and `ValueHook`s can be pushed so the loader decodes+filters in one pass. Columns projected away or pruned are never loaded.

---

## Expression Evaluation

### ExprSet Compilation (velox/expr/ExprCompiler.cpp)

Converts `core::ITypedExpr` tree to executable `exec::Expr` DAG:

1. **Type mapping**: `FieldAccessTypedExpr`→`FieldReference`, `ConstantTypedExpr`→`ConstantExpr`, `CallTypedExpr`→special form / `VectorFunction` / `SimpleFunction` (resolution order: special form name → VectorFunction signature → SimpleFunction).
2. **Flattening**: adjacent AND/OR merge into n-ary `ConjunctExpr`; associative calls like `concat(a,concat(b,c))` → `concat(a,b,c)`.
3. **CSE detection**: identical deterministic subexpressions become a shared `Expr` (`isMultiplyReferenced_`), evaluated once, results cached.
4. **Constant folding**: deterministic subexpressions with no column dependency evaluated at compile time → `ConstantExpr`.
5. `computeMetadata()` populates `distinctFields_`, `multiplyReferencedFields_`, `propagatesNulls_`, `deterministic_`, `hasConditionals_`.

### Evaluation Pipeline per Expr::eval

```
CSE cache check
→ evalEncodings()        (peeling: strip shared dict/const wrappers)
→ evalWithNulls()        (remove null-input rows for null-propagating exprs)
→ evalAll() / evalSpecialForm()
→ finalize              (restore nulls, re-wrap peeled encoding, cache if CSE)
```

Fast path: `evalFlatNoNulls()` when inputs are flat/constant non-null and `supportsFlatNoNullsFastPath() == true`.

### Encoding Peeling Algorithm (velox/expr/Expr.cpp, PeeledEncoding)

Strips common wrappers before calling scalar functions:

1. All inputs `ConstantVector` → evaluate on 1 row, wrap result as constant.
2. Inputs share the **same** `DictionaryVector` indices/base → evaluate only on dictionary (unique) values, re-wrap with original indices. Big win for low-cardinality columns.
3. `Expr::evalWithMemo()` memoizes per-base-dictionary results across batches reusing the same base vector (`baseOfDictionaryRepeats_`), re-wrapping with new indices only.

**Fallback / give-up:** peeling abandoned when wrappers are incompatible (different indices buffers, mixed encodings), nulls in wrapper can't be cleanly composed, or peeling wouldn't reduce row count. `PeeledEncoding::peel()` returns bool success; on failure, `DecodedVector` produces flat base + indices and proceeds row-wise on SelectivityVector.

### ConjunctExpr Short-Circuit + Adaptive Reorder (velox/expr/ConjunctExpr.cpp)

- AND: evaluates conjuncts left-to-right; after each, rows where conjunct returned false (or null per three-valued logic) are **deselected** from the active `SelectivityVector`. Next conjunct only runs on survivors.
- OR: dual — deselect true rows.
- **Error suppression**: for rows already decided (AND: returned false; OR: returned true), exceptions from subsequent conjuncts are suppressed — correct SQL short-circuit semantics.
- **Adaptive reordering**: per-conjunct selectivity + wall time tracked each batch; conjuncts reordered to run cheapest/most-selective first (`selectivity_` updated dynamically).

### ErrorVector / Per-Row Exception Capture

Under `TRY`, `EvalCtx::throwOnError_` = false. `EvalCtx::setError(row, exceptionPtr)` records into an `EvalErrors` vector (indexed by row). Rows with errors excluded downstream; `TRY` sets those output rows to null. Without TRY, first error rethrows.

### LambdaExpr (velox/expr/LambdaExpr.cpp)

For `transform`/`filter`/`reduce`/`array_sort`. Produces a `FunctionVector` whose `Callable` captures outer context: captured (non-argument) fields bound into a row of captures; lambda formal arguments supplied per-invocation by the higher-order function. HOF builds a flattened element `RowVector` + `SelectivityVector` over all elements, calls the captured `ExprSet` once — vectorized across all elements in one batch call, not per-element.

### VectorFunction vs SimpleFunction

**SimpleFunction (template-based):**
```cpp
template<typename TExec>
struct MyFunc {
  VELOX_DEFINE_FUNCTION_TYPES(TExec);
  bool call(int64_t& result, const int64_t& a, const int64_t& b) {
    result = a + b;
    return true;  // non-null
  }
};
VELOX_REGISTER_FUNCTION(MyFunc, myFunc);
```
Velox generates vectorized loop, null handling, encoding normalization via template metaprogramming.

**VectorFunction:** direct vector access for functions needing it (e.g., `cardinality`, `map_keys`):
```cpp
class MyVectorFunc : public exec::VectorFunction {
  void apply(const SelectivityVector& rows,
             std::vector<VectorPtr>& args,
             const TypePtr& outputType,
             EvalCtx& context,
             VectorPtr& result) const override;
};
```

### Codegen (LLVM / xsimd)

Optional expression codegen (`velox/experimental/codegen`): after expression compilation, a codegen pass emits LLVM IR for hot inner loops (arithmetic chains, cast chains), replacing the interpreted DAG walk with native machine code for the batch.

**xsimd** (header-only SIMD abstraction): used in hash table probing, string comparison, null checking. Explicit AVX2/AVX-512 paths in `Hashtable.cpp`, `VectorHasher.cpp`.

---

## Aggregation

### Operators

- `HashAggregation` — arbitrary cardinality GROUP BY
- `StreamingAggregation` — pre-sorted input, single pass

### Aggregate Interface (velox/exec/Aggregate.h)

```cpp
class Aggregate {
  // Placement-construct accumulators for new groups
  virtual void initializeNewGroups(char** groups,
                                   folly::Range<const int32_t*> indices) = 0;
  // Partial/single-stage ingest
  virtual void addRawInput(char** groups, const SelectivityVector& rows,
                           const std::vector<VectorPtr>& args, bool mayPushdown) = 0;
  // Final-stage merge
  virtual void addIntermediateResults(char** groups, const SelectivityVector& rows,
                                      const std::vector<VectorPtr>& args, bool mayPushdown) = 0;
  // Output
  virtual void extractValues(char** groups, int32_t numGroups, VectorPtr* result) = 0;
  // Serialize intermediate state
  virtual void extractAccumulators(char** groups, int32_t numGroups, VectorPtr* result) = 0;
  // Fixed bytes per group in RowContainer
  virtual int32_t accumulatorFixedWidthSize() const = 0;
};
```

### Hash Table Layout (velox/exec/HashTable.h)

**128-byte buckets, 16 slots each:**
```
[ 16 tag bytes (7-bit hash tags, 1 per slot) ]
[ 16 × 6-byte row pointers → RowContainer ]
[ 16 bytes unused padding ]
= 128 bytes total
```

**SIMD probe:** all 16 tags loaded into one 128-bit register; `_mm_cmpeq_epi8` (SSE2) compares against broadcast probe tag — 16 slots checked in 1 instruction → 16-bit match mask. Matching slots' 6-byte pointers followed into `RowContainer` for full key comparison. Linear probing across buckets on no-match.

**Hash modes:**
- `kArray` — key range small enough to index directly (no hashing)
- `kNormalizedKey` — keys packed into 64-bit normalized key for direct compare + `AdaptivePrefetch`
- `kHash` — full key comparison via RowContainer pointer follow

**Interleaving / prefetch:** multiple probe keys interleaved so multiple cache misses are in-flight simultaneously (memory-level parallelism). `ProbeState` carries per-row probe progress for the vectorized loop.

### RowContainer Row Layout (velox/exec/RowContainer.h)

Per-row layout (row-major slab):
```
1. Normalized key word (optional, kNormalizedKey mode only)
2. Null flags: 1 bit per nullable column, packed (1 = null)
3. Fixed-width data: 8 bytes per column
   - Scalars ≤ 8 bytes: inline
   - Strings/complex/> 8 bytes: pointer/StringView into variable-width section
4. Variable-width section: bytes in HashStringAllocator, referenced from fixed part
5. Accumulators (aggregation mode only)
6. Flag bits:
   - free flag (row on free list)
   - probed flag (build row matched; used for outer/anti/semi join result generation)
```

Free rows threaded on a free list using the row's own space.

### Accumulator Layouts

| Function | Layout |
|---|---|
| `min`/`max` (numeric) | inline value + valid bit in fixed-width slot |
| `min`/`max`/`arbitrary` (string) | `SingleValueAccumulator` — one HashStringAllocator alloc |
| `sum`/`count` | inline fixed-width |
| `avg` | inline (sum: double, count: int64) |
| `array_agg`/`map_agg` | `ValueList` — chain of HashStringAllocator blocks; fixed part holds head/tail positions |
| `approx_distinct` | HyperLogLog bytes in HashStringAllocator (sparse then dense), serialized to VARBINARY on extract |
| `approx_percentile` | KLL sketch bytes in HashStringAllocator |

### Partial→Final Serialization

Partial agg calls `extractAccumulators()` → intermediate `RowVector` (e.g., avg → `ROW(sum DOUBLE, count BIGINT)`, approx_distinct → VARBINARY HLL bytes). Final agg reads via `addIntermediateResults()` and merges. **Scan pushdown** for `sum`/`min`/`max`/`bitwise_and_agg`/`bitwise_or_agg`/`bool_and`/`bool_or` when reading raw columns with no transform — aggregate applied during decode via `ValueHook`.

### HashAggregation Spilling

1. Select partitions (by hash bits) holding most data that meet spill target.
2. Reuse same partition set across rounds.
3. Remove spilled rows from table; serialize via `VectorStreamGroup`.
4. On restore: sort-merge in-memory + on-disk runs keyed by grouping columns.

---

## Hash Join

Operators `HashBuild` and `HashProbe` (velox/exec/HashBuild.cpp, HashProbe.cpp), two pipelines bridged by `HashJoinBridge`.

### JoinBridge Promise/Future (velox/exec/HashJoinBridge.h)

`HashBuild` operators across drivers build partial tables; the last finisher merges them (parallel build) and sets a `folly::Promise` with the completed `HashTable` + spill partition info. `HashProbe::isBlocked()` returns the bridge's `folly::SemiFuture` (`BlockingReason::kWaitForJoinBuild`). Driver yields until build completes. Bridge also carries dynamic filter info.

### Dynamic Filter Pushdown

For join keys with few distinct build-side values, `VectorHasher` builds an in-list filter pushed to probe-side `TableScan` for file/row-group pruning and row filtering during decode.

### Join Types Handled

`INNER`, `LEFT`, `RIGHT`, `FULL OUTER`, `LEFT SEMI (filter)`, `LEFT SEMI (project)`, `RIGHT SEMI (filter)`, `RIGHT SEMI (project)`, `LEFT ANTI`, `RIGHT ANTI`, `NULL-AWARE ANTI`, counting joins.

**NULL-aware anti join:** build side tracks whether any build key is null (`hasNullKeys`). If true → every probe row filtered (NOT IN with null = false for all). If false → probe rows with no match pass through; probe row with null key fails.

### Spillable Hash Join

Build operators coordinate: all spill the same partition set (hash bits; recursive bit ranges, e.g., parent bits [29,31] → child [32,35] for 8-way recursion). Probe routes input rows belonging to spilled partitions to matching probe spill files. Replay per partition: rebuild from build spill file, re-probe from probe spill file.

### Other Join Types

**MergeJoin:** ordered inputs; merge-scan both sides, emit matches. Used when upstream sort order aligns with join keys.

**NestedLoopJoin:** cross join or non-equi predicates. `NestedLoopJoinBuild` stores right side in memory as list of `RowVector` batches. Probe iterates probe rows × build batches, applies filter expression.

---

## Shuffle / Exchange

### PartitionedOutput Operator

Terminates a pipeline sending data to another node or pipeline. Routes each row to a partition bucket by hash of partition keys. Serializes batches using **Presto page format** (column-by-column: type tag + encoding + data).

### Exchange Operator

`ExchangeOperator` (source) pulls `SerializedPage` objects from `ExchangeClient`, which holds multiple `ExchangeSource`s. `PrestoExchangeSource` issues HTTP requests returning `folly::SemiFuture`. `ExchangeOperator::isBlocked()` returns that future; driver yields until pages arrive.

**Local exchange** (same node, different threads): `LocalExchange` — lock-free multi-producer multi-consumer queue of `RowVector` pointers. No serialization.

### SerializedPage

```
SerializedPage {
  codec:            CompressionKind  (NONE, LZ4, ZSTD, SNAPPY)
  uncompressedSize: int32
  data:             byte[]           // Presto page wire format
}
```

---

## Connector / Split System

### HiveConnectorSplit (velox/connectors/hive/)

```cpp
struct HiveConnectorSplit {
  string    filePath;        // s3://bucket/path/part-00000.parquet
  FileFormat fileFormat;     // PARQUET, DWRF, ORC, TEXT, Nimble
  long      start;           // byte offset
  long      length;          // byte length
  map<string,string> partitionKeys;
  optional<TableBucketNumber> tableBucketNumber;
};
```

`Task::addSplit()` enqueues splits; `TableScanOperator` dequeues and calls `HiveDataSource::addSplit()`, which constructs the reader, applies column pruning (`outputType`), and pushes `SubfieldFilters`.

### DwioReader Interface (velox/dwio/common/Reader.h)

Abstract `Reader`/`RowReader` (`createRowReader`, `next(size, result)`). `ParquetReader` implements it: reads footer/schema, iterates row groups, builds column readers, decodes pages, wraps as `FlatVector` or `DictionaryVector` (for DICT-encoded pages). DWRF/ORC implement the same interface → HiveDataSource is format-agnostic.

### Parquet Filter Pushdown

`SubfieldFilter` types pushed to reader (`velox/type/Filter.h`):

| Filter type | Velox class |
|---|---|
| Equality | `BigintRange` (degenerate) / `BytesValues` (singleton) |
| Range | `BigintRange`, `DoubleRange`, `BytesRange` |
| In-list | `BigintValuesUsingHashTable`, `BytesValues` |
| IS NULL | `IsNull` |
| IS NOT NULL | `IsNotNull` |
| Boolean | `BoolValue` |

Evaluated against row-group min/max/null-count stats → skip whole row groups. Remaining rows filtered during page decode.

### ArrowBridge (velox/vector/arrow/Bridge.cpp)

`exportToArrow(VectorPtr, ArrowArray&, pool)` / `exportToArrow(..., ArrowSchema&)` and `importFromArrow(ArrowSchema&, ArrowArray&, pool)` over Arrow C Data Interface.

**Zero-copy cases:**
- Flat fixed-width primitives (int, float, double, bool)
- With Arrow 15: `StringView` ↔ `Utf8View`/`BinaryView`, `ArrayVector` ↔ `ListView`, `ConstantVector` ↔ Arrow constant encoding, `SequenceVector` ↔ Run-End-Encoding
- `DictionaryVector` ↔ Arrow dictionary array (indices + dictionary)
- Buffers shared with refcount transferred through C interface `release` callback

**Requires copy:**
- Pre-Arrow-15 string export to Utf8 (offset-based, needs contiguous bytes materialization)
- `BiasVector` (must decode)
- Nested encodings Arrow can't represent in one layer
- Null representation incompatibilities

---

## Prestissimo (Velox in Presto)

C++ Presto worker embedding Velox. `PrestoServer` boots on Proxygen HTTP; `TaskManager` owns one `velox::Task` per fragment.

### HTTP Endpoints (TaskResource.cpp)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/task/{taskId}` | Create/update task (plan fragment + splits + output buffer config) |
| `GET` | `/v1/task/{taskId}` | Full `TaskInfo` (metrics) |
| `GET` | `/v1/task/{taskId}/status` | Lightweight `TaskStatus` (progress) |
| `GET` | `/v1/task/{taskId}/results/{bufferId}/{token}` | Fetch shuffle output pages (long-poll) |
| `GET` | `/v1/task/{taskId}/results/{bufferId}/{token}/acknowledge` | Ack received pages; producer frees buffers |
| `DELETE` | `/v1/task/{taskId}` | Abort/cleanup |
| `DELETE` | `/v1/task/{taskId}/results/{bufferId}` | Destroy a buffer |

**PrestoExchangeSource:** HTTP **long-polling** (not streaming). Issues `GET .../results/{bufferId}/{token}`; producer holds request open until pages available or timeout, returns serialized pages with next token in headers. Consumer acknowledges token → `OutputBufferManager` releases buffers → re-issues next long-poll. Backpressure via output buffer max size.

### Plan JSON Deserialization (presto_cpp/main/types/PrestoToVeloxQueryPlan.cpp)

`@type` discriminator in JSON selects `PlanNode` class:

| Presto JSON type | Velox PlanNode |
|---|---|
| `TableScanNode` | `TableScanNode` (HiveConnector) |
| `ProjectNode` | `ProjectNode` |
| `FilterNode` | `FilterNode` |
| `AggregationNode` | `AggregationNode` (Step: PARTIAL/FINAL/INTERMEDIATE/SINGLE) |
| `JoinNode` | `HashJoinNode` |
| `RemoteSourceNode` | `ExchangeNode` / `MergeExchangeNode` |
| `OutputNode` | `PartitionedOutputNode` |
| `TopNNode` | `TopNNode` |
| `WindowNode` | `WindowNode` |

Expressions arrive as `RowExpression` JSON → Velox `ITypedExpr`. Types via `TypeParser::toVeloxType()`.

### Presto→Velox Type Mapping

| Presto type | Velox physical |
|---|---|
| `VARCHAR`, `BIGINT`, `INTEGER`, `SMALLINT`, `TINYINT`, `DOUBLE`, `REAL`, `BOOLEAN`, `TIMESTAMP`, `DATE` | same |
| `ROW(...)` | RowType |
| `ARRAY(x)` | ArrayType |
| `MAP(k,v)` | MapType |
| `DECIMAL(p,s)` | BIGINT (p ≤ 18) / HUGEINT (p 19–38) |
| `HYPERLOGLOG` / `P4HYPERLOGLOG` | VARBINARY-backed custom type |
| `JSON` | VARCHAR-backed |
| `IPADDRESS` | HUGEINT-backed |
| `TIMESTAMP WITH TIME ZONE` | BIGINT-backed (52 bits ms UTC + 12 bits tz id) |

---

## Velox in Spark: Apache Gluten

**Apache Gluten** (now an Apache TLP) offloads Spark SQL to Velox via Substrait.

### Execution Flow

```
Spark Driver (JVM)
  Spark physical plan → Substrait plan (protobuf)
         │
         │ JNI: nativeCreateKernelWithIterator(substraitBytes)
         ▼
Velox Native (C++, loaded via JNI)
  SubstraitToVeloxPlanConverter → PlanNode tree → velox::Task
  Results returned as ColumnarBatch (Arrow C Data Interface)
         │
         ▼
Spark Executor (JVM)
  ColumnarBatch consumed by downstream Spark operators
```

### Substrait → Velox Node Mapping

| Substrait RelType | Velox PlanNode |
|---|---|
| `ReadRel` | `TableScanNode` / `ValuesNode` |
| `FilterRel` | `FilterNode` |
| `ProjectRel` | `ProjectNode` |
| `AggregateRel` | `AggregationNode` |
| `JoinRel` | `HashJoinNode` / `MergeJoinNode` |
| `SortRel` | `OrderByNode` |
| `FetchRel` | `LimitNode` / `TopNNode` |
| `ExpandRel` | `ExpandNode` |
| `ExchangeRel` | `PartitionedOutputNode` / `ExchangeNode` |

Functions map by Substrait reference → Velox function name (Spark dialect: `sparksql::` namespace).

### JNI Columnar Boundary

Batches cross JVM↔C++ as Arrow C Data Interface (`ArrowArray`/`ArrowSchema`) or Gluten `ColumnarBatch` handles (off-heap pointers). Shuffle uses native columnar shuffle writer/reader.

### Offload vs Fallback

Supported operators (scan, filter, project, hash agg, hash/sort-merge join, sort, window, union, expand, limit, shuffle) run native Velox. Unsupported operators or runtime failures fall back to JVM Spark, inserting `ColumnarToRow`/`RowToColumnar` transitions. Gluten's planner validates each operator/expression for native support before offloading; unvalidated subtrees stay on JVM.

---

## Fuzzing and Testing

`velox/expression/fuzzer/` contains an expression fuzzer:
1. Generates random `TypePtr` trees.
2. Generates random expression trees.
3. Evaluates with interpreted engine and (optionally) codegen engine.
4. Compares results row-by-row; mismatches = bugs.

Similarly: `RowContainerFuzzer`, `AggregationFuzzer`, `JoinFuzzer`.

---

## Key Papers and References

| Paper | Venue | Year |
|-------|-------|------|
| Pedreira et al., "Velox: Meta's Unified Execution Engine" | PVLDB | 2022 |
| Leis et al., "Morsel-Driven Parallelism: A NUMA-Aware Query Evaluation Framework for the Many-Core Age" | SIGMOD | 2014 |
| Kersten et al., "Everything You Always Wanted to Know About Compiled and Vectorized Queries But Were Afraid to Ask" | PVLDB | 2018 |
| Boncz, Zukowski, Nes, "MonetDB/X100: Hyper-Pipelining Query Execution" | CIDR | 2005 |
| Neumann, "Efficiently Compiling Efficient Query Plans for Modern Hardware" | PVLDB | 2011 |
| Blanas et al., "A Comparison of Join Algorithms for Log Records and Hierarchical Data in a Web Warehouse" | SIGMOD | 2010 |

**Online sources:**
- Velox docs: https://facebookincubator.github.io/velox/develop/
- Engineering at Meta — Velox: https://engineering.fb.com/2023/03/09/open-source/velox-open-source-execution-engine/
- Engineering at Meta — Arrow 15 alignment: https://engineering.fb.com/2024/02/20/developer-tools/velox-apache-arrow-15-composable-data-management/
- Presto C++ docs: https://prestodb.io/docs/current/presto-cpp.html
- Apache Gluten: https://gluten.apache.org/
