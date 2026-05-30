+++
title = "Clickhouse Internals"
date = 2026-05-29
[taxonomies]
tags = ["database", "clickhouse", "mergetree", "columnar", "olap", "distributed"]
+++


# ClickHouse Internals

## Overview

ClickHouse is a column-oriented OLAP DBMS built around the **MergeTree** family of
storage engines. Its design is an aggressive application of three ideas: (1) columnar
on-disk storage with per-column compression codecs, (2) a *sparse* primary index that
indexes one row per ~8192-row granule rather than every row, and (3) an LSM-like
"merge" discipline in which every `INSERT` produces an immutable sorted *part* and a
background process merge-sorts parts together. Reads are vectorized over blocks of
columns (typically 65536 rows) through a pull-based processor pipeline.

The architecture is closest in spirit to C-Store / Vertica (Stonebraker et al., VLDB
2005) and the column-store analysis in Abadi, Madden, Hachem, "Column-Stores vs.
Row-Stores: How Different Are They Really?" (SIGMOD 2008), with lightweight codecs in
the lineage of Zukowski et al., "Super-Scalar RAM-CPU Cache Compression" (ICDE 2006).
Replication uses a replicated log over ZooKeeper or the Raft-based ClickHouse Keeper.

```
INSERT ──► [in-memory block sorted by PK] ──► write immutable PART to disk
                                                   │
parts:  all_1_1_0  all_2_2_0  all_3_3_0 ...        ▼
                              SimpleMergeSelector picks a run
                                       │
                              merge-sort by PK ──► all_1_3_1 (new part)
                                       │
                              old parts marked outdated, GC'd

SELECT ─► parse ─► AST ─► QueryPlan ─► QueryPipeline (IProcessor DAG) ─► PipelineExecutor
                              │
                  MergeTree reader: PK index → granule ranges → marks → decompress .bin
```

Source layout references: `src/Storages/MergeTree/`, `src/Processors/`, `src/Columns/`,
`src/Compression/`, `src/Coordination/`.

---

## 1. MergeTree Part Structure (On-Disk Layout)

A *part* is an immutable directory. Its name encodes its lineage:

```
{partition_id}_{min_block}_{max_block}_{level}[_{mutation_version}]
   all          _   1      _    3       _   1
```

- `partition_id`: from `PARTITION BY` (e.g. `202605` for `toYYYYMM(date)`), or literal
  `all` when no partition key.
- `min_block` / `max_block`: range of monotonically increasing block numbers (from a
  ZooKeeper/Keeper counter for replicated tables, local counter otherwise). A fresh
  insert gets `N_N`; a merge of `1_1` and `2_2` yields `1_2`.
- `level`: number of merges that produced it (raw inserts are level 0).
- `mutation_version`: appended after an `ALTER ... UPDATE/DELETE` mutation.

### Files in a part

| File | Purpose |
|---|---|
| `{column}.bin` | Compressed column data (one per column, or per stream for complex types) |
| `{column}.cmrk3` / `.mrk3` | Marks: granule → byte offsets into `.bin` |
| `primary.idx` (or `primary.cidx`) | Sparse PK index: PK tuple at each granule boundary |
| `minmax_{col}.idx` | Min/max of each partition-key column for the whole part (partition pruning) |
| `partition.dat` | Serialized partition key value for the part |
| `skp_idx_{name}.idx` / `.mrk3` | Data-skipping secondary indexes |
| `checksums.txt` | Size + hash (CityHash128) of every file; integrity + replication |
| `count.txt` | Exact row count of the part (avoids reading data for `count(*)`) |
| `columns.txt` | Ordered list of columns and their types |
| `serialization.json` | Per-column serialization kind (Default vs **Sparse**) |
| `default_compression_codec.txt` | Codec to apply to columns without explicit codec |
| `metadata_version.txt` | Schema version the part was written under |

A part can be `Wide` (one `.bin` per column, the default for large parts) or `Compact`
(all columns in a single `data.bin` + `data.mrk3`, chosen for tiny parts under
`min_bytes_for_wide_part` / `min_rows_for_wide_part`, default 10 MB / 1M rows). In-memory
parts (`InMemory`) existed in older versions but are deprecated.

### `.bin` data file format

A `.bin` file is a concatenation of independently-decompressable **compressed blocks**.
Each block header is 25 bytes (not 16 — the checksum is 128-bit):

| Field | Size | Encoding |
|---|---|---|
| checksum | 16 B | CityHash128 of (method byte + sizes + compressed payload) |
| method | 1 B | codec id: `0x82`=LZ4, `0x90`=ZSTD, `0x02`=none |
| compressed_size | 4 B | little-endian; includes the 9-byte (method+sizes) header |
| uncompressed_size | 4 B | little-endian, size after decompression |
| compressed_data | var | the codec payload |

A block holds roughly `min_compress_block_size` (65536) to `max_compress_block_size`
(1 MiB) bytes of uncompressed column data, and a block boundary is always forced at a
granule boundary so that any granule can be decompressed without crossing into an
unrelated block. The 16-byte checksum lets ClickHouse verify each block on read
independently of any part-level checksum.

### Mark files (`.mrk3` / `.cmrk3`)

Marks map a granule index to where its data begins. With **adaptive granularity** each
mark is 24 bytes:

| Field | Size | Meaning |
|---|---|---|
| offset_in_compressed_file | 8 B | byte offset of the compressed block in `.bin` |
| offset_in_decompressed_block | 8 B | byte offset of the granule inside that block |
| rows_in_granule | 8 B | how many rows this granule contains |

Older non-adaptive marks (`.mrk`) were 16 bytes (no `rows_in_granule`, fixed 8192).
`.cmrk3` is the *compressed* mark file (marks themselves are LZ4-compressed) used by
default since v22.x. To read granule *g* of a column: take `marks[g]`, seek to
`offset_in_compressed_file`, decompress that block, skip `offset_in_decompressed_block`
bytes, read `rows_in_granule` values.

### Granules

The default granule is 8192 rows (`index_granularity`). With **adaptive granularity**
(`index_granularity_bytes`, default 10 MiB) ClickHouse caps a granule so its
uncompressed footprint stays under ~10 MiB; for very wide rows a granule may hold far
fewer than 8192 rows. This keeps the I/O cost of "read one granule" roughly constant
regardless of row width — important because the index addresses granules, not rows.

---

## 2. Sparse Primary Index Deep Dive

The PK index is *sparse*: `primary.idx` stores the PK tuple of the **first row of each
granule** only. For a part of `R` rows and granule size `g`, the index has `ceil(R/g)`
entries — for 1 billion rows and `g=8192` that is ~122k entries, small enough to keep
in memory (loaded once, cached in the mark/index cache).

```
primary.idx (PK = (toDate(ts), user_id)):
  entry 0 → granule 0 first row → (2026-05-01, 42)
  entry 1 → granule 1 first row → (2026-05-01, 991)
  entry 2 → granule 2 first row → (2026-05-02, 17)
  ...

WHERE user_id = 991 AND date = '2026-05-01'
  binary search entries → matching granule range [1, 2)
  read marks[1] → seek .bin → decompress → scan 8192 rows
```

### Lookup algorithm

```
function selectGranules(condition, index, marks):
    # index entries are sorted ascending by the composite PK (lexicographic)
    ranges = []
    # KeyCondition turns the WHERE clause into a hyperrectangle test in PK space
    for each contiguous run of granules g where
        KeyCondition.mayBeTrueInRange(index[g], index[g+1]) is true:
            ranges.append( MarkRange(g, g_end) )
    # binary search is used when the condition is a prefix-bounded range
    return ranges        # then read only marks/.bin for these ranges
```

`KeyCondition` (`src/Storages/MergeTree/KeyCondition.h`) evaluates whether a granule's
`[low, high)` PK interval *can* satisfy the predicate. Because the index is sparse it is
*lossy*: a granule is read whenever it *might* contain a match, then rows are filtered
exactly during scan. This is the same "zone-map" idea as PostgreSQL BRIN, applied at
granule granularity.

### Composite keys

Multiple PK columns form one tuple sorted lexicographically. Pruning is only effective
on a **prefix**: with `ORDER BY (a, b, c)`, a predicate on `a` prunes well, on `b` alone
prunes poorly (every `a`-group repeats the full `b` range). Rule of thumb: put
low-cardinality, frequently-filtered columns first. ClickHouse blog "How ClickHouse Keeps
Data in Order to Enable Fast Queries" covers this in detail.

### Cost: index scan vs full scan

- **Full scan**: read every granule of every column referenced → bounded by total
  compressed column bytes.
- **Index scan**: read only granules in the selected `MarkRange`s. For a selective
  prefix predicate this can be orders of magnitude less I/O. The minimum unit read is one
  granule (~8192 rows), so a single-row point lookup still decompresses a whole granule —
  ClickHouse is not a point-lookup store.

`ORDER BY` and `PRIMARY KEY` may differ: `PRIMARY KEY` can be a *prefix* of `ORDER BY`,
letting the on-disk sort be finer than the indexed prefix (smaller index, same sort).

---

## 3. Data-Skipping Indexes

Secondary indexes summarize blocks of `GRANULARITY N` granules and let the planner skip
reading them. They are stored as `skp_idx_{name}.idx` + `.mrk3` inside each part and are
consulted *after* PK pruning, *before* reading column data.

| Type | Stores per block | Helps when |
|---|---|---|
| `minmax` | (min, max) of the expression | column correlated with PK / monotone-ish |
| `set(max_rows)` | up to `max_rows` distinct values (drops to "all" if exceeded) | low-cardinality column, equality/IN |
| `bloom_filter(p)` | classic Bloom filter over values, false-positive rate `p` | high-cardinality equality / IN |
| `ngrambf_v1(n, size, hashes, seed)` | Bloom filter over n-grams | substring `LIKE '%x%'`, `hasToken`-style |
| `tokenbf_v1(size, hashes, seed)` | Bloom filter over whitespace/punct tokens | word search in text |

```
DDL:  INDEX idx_url url TYPE bloom_filter(0.01) GRANULARITY 4
                                                   │
each index granule summarizes 4 data granules (4×8192 = 32768 rows).
WHERE url = 'x' → test bloom filter per index-granule → skip blocks that
                  definitely lack 'x'; read the rest and filter exactly.
```

**When they help**: the indexed column must be *physically clustered* enough that whole
blocks can be excluded. A `minmax` on a column uncorrelated with the sort order is
useless (every block spans the full range). Bloom filters help equality on
high-cardinality columns but add write + storage cost and only ever *exclude* blocks —
they never accelerate a scan that must read the block anyway. `EXPLAIN indexes = 1`
reports granules dropped per index, the only reliable way to confirm an index earns its
keep.

---

## 4. Compression and Codec Internals

ClickHouse separates **codecs** (per-column, semantic, applied first) from the **generic
compressor** (LZ4 or ZSTD, applied last). The pipeline is: raw values → codec chain →
generic compression → compressed block in `.bin`. Codecs are declared per column:
`CODEC(Delta, ZSTD(3))`.

| Codec | Mechanism | Best for |
|---|---|---|
| `LZ4` (default) | byte-level LZ77 with 64 KB window; very fast | general columns; default whole-file codec |
| `ZSTD(level)` | entropy-coded LZ with larger window | cold/archival data, better ratio, slower |
| `Delta(n)` | store `v[i]-v[i-1]` over width-`n` ints | timestamps, monotone counters |
| `DoubleDelta` | second-order delta + varint, Gorilla-style | near-constant-stride sequences |
| `Gorilla` | XOR consecutive floats, encode leading/trailing zero runs | slowly-changing gauges/metrics |
| `T64` | transpose 64 values into bit-planes, strip unused high bits | low-range / low-cardinality ints |
| `FPC`, `ZSTD_QAT`, `GCD` | specialized (floats / hardware-offload / common divisor) | niche |

### Delta and DoubleDelta

`Delta(n)`: first value stored raw, subsequent stored as `v[i]-v[i-1]` in the same
width. Turns a monotone counter into a stream of small (often constant) values that LZ4
crushes. `DoubleDelta` stores the delta-of-deltas; for a fixed-stride series (timestamps
every 15 s) the second-order differences are zero and encode to a single bit each. The
varint framing follows the Gorilla paper.

```
DoubleDelta encode (per Pelkonen et al., VLDB 2015 timestamp scheme):
  write v[0] raw; write d1 = v[1]-v[0] raw
  for i >= 2:
    dod = (v[i]-v[i-1]) - (v[i-1]-v[i-2])
    if dod == 0:                 emit '0'
    elif dod in [-63,64]:        emit '10'  + 7  bits
    elif dod in [-255,256]:      emit '110' + 9  bits
    elif dod in [-2047,2048]:    emit '1110'+ 12 bits
    else:                        emit '1111'+ 32/64 bits
```

### Gorilla (float XOR)

From Pelkonen et al., "Gorilla: A Fast, Scalable, In-Memory Time Series Database"
(VLDB 2015). XOR each float with the previous; if equal, emit one `0` bit. Otherwise
emit `1` plus a control bit choosing whether to reuse the previous (leading-zeros,
trailing-zeros) window or store a fresh one, then the meaningful XOR bits between.
Excellent for gauges that change slowly; poor for high-entropy floats.

```
Gorilla value encode:
  xor = bits(v[i]) ^ bits(v[i-1])
  if xor == 0: emit '0'
  else:
    lz = clz(xor); tz = ctz(xor)
    if can reuse prev (lz,tz) window: emit '10' + meaningful_bits
    else: emit '11' + lz(5b) + len(6b) + meaningful_bits
```

### T64

Takes 64 consecutive integers, computes the active value range, subtracts the min, then
**transposes** the 64 values × `b` significant bits into `b` 64-bit bit-planes — storing
only as many planes as the range needs. A column of values in `[0,15]` needs 4 planes
instead of 64 full ints. The transposed bit-planes are then LZ4/ZSTD-compressed. This is
ClickHouse's analog of the bit-packing in Zukowski et al. (ICDE 2006).

### Codec chaining & ZSTD dictionaries

`CODEC(Delta, ZSTD(3))` runs Delta then ZSTD; `CODEC(DoubleDelta, LZ4)` is common for
timestamps. Order matters: the semantic codec must precede the generic compressor.
`LowCardinality(T)` is not a codec but a *type wrapper* — it dictionary-encodes the
column (a shared dictionary of distinct values + an index column), which is the most
impactful "compression" for low-cardinality strings and composes with the codecs above.

---

## 5. Merge Process

### SimpleMergeSelector

Background merges keep part count bounded (too many parts slows reads and risks the
"too many parts" error). `SimpleMergeSelector` (`src/Storages/MergeTree/`) scans the
sorted list of active parts and scores every contiguous *range* of adjacent parts,
preferring ranges that are balanced in size, not too large in total, and weighting in
part age so small parts left behind get merged eventually. The high-level scoring:

```
for each contiguous range [i..j] of active parts:
    sum   = Σ size(parts)
    max   = max size(parts);  cnt = j-i+1
    if sum > max_total_size_to_merge: skip          # don't build giant parts
    # prefer ranges where no single part dominates and that aren't too few/many parts
    score = sum / (max + ε)                          # ~cnt when balanced
    # heuristic boost for ranges containing old small parts (base_for_age)
    score *= ageBoost(min_age_in_range)
pick range with best score; if best score good enough, schedule a merge
```

The effect approximates a tiered LSM compaction: many small parts repeatedly fold into
fewer larger parts, with merged-part size bounded by `max_bytes_to_merge_at_max_space_in_pool`.

### Merge steps

```
1. select range of parts (SimpleMergeSelector)
2. open a sorted reader over each part (each is internally PK-sorted)
3. k-way merge by PK using a heap (MergingSortedAlgorithm)
   - engine-specific "merge algorithm" transforms the stream (see below)
4. stream merged rows through column writers → new part directory (temp name)
5. write primary.idx, marks, skip indexes, checksums.txt, count.txt
6. atomically rename temp → final part; mark source parts Outdated
7. source parts removed after old_parts_lifetime once no query references them
```

Merges run on the background pool (`background_pool_size`, default 16) with a separate
pool for large merges; selection respects available pool space so a giant merge cannot
starve small ones.

### Engine-specific merge transforms

| Engine | What the merge does |
|---|---|
| `MergeTree` | plain k-way sort merge, no row collapsing |
| `ReplacingMergeTree` | among rows with equal sort key, keep the one with max `version` (or last) |
| `AggregatingMergeTree` | combine `AggregateFunction` *states* of equal-key rows via the function's `merge` |
| `SummingMergeTree` | sum numeric columns for equal-key rows |
| `CollapsingMergeTree` | cancel a `Sign=-1` row against a preceding `Sign=+1` row |
| `VersionedCollapsingMergeTree` | same, but uses an explicit version column to order pairs |

Crucially these guarantees are **eventual**: collapsing/replacing only happens *during a
merge*, so queries must use `FINAL`, `argMax`, or `-If`/`-Merge` combinators to get fully
reconciled results before merges complete.

### TTL merges

`TTL` expressions (`TTL ts + INTERVAL 30 DAY`) are evaluated during merges: expired rows
are dropped, or moved (`TO DISK`/`TO VOLUME`), or aggregated (`GROUP BY ... SET`). Column
TTLs reset a column to its default past the TTL. A dedicated TTL-merge schedule ensures
expiry happens even without size-driven merges.

### Aggregate state binary format

`AggregatingMergeTree` stores `AggregateFunction(func, types)` columns as the function's
serialized *intermediate state* (e.g. `uniqExact` → a hash set; `avg` → (sum, count);
`quantileTDigest` → a t-digest). The merge calls the function's `merge(state_a, state_b)`
and `serialize`; readers must use `-Merge` combinators (`avgMerge`, `uniqMerge`) to
finalize. This is what powers incrementally-maintained rollups via materialized views.

---

## 6. Vectorized Execution (Column Block Model)

ClickHouse processes data in **Chunks**: a `Chunk` is a vector of `IColumn` pointers plus
a row count (`src/Processors/Chunk.h`). Default block size is 65536 rows
(`max_block_size`). Operating on whole columns amortizes virtual dispatch and exposes
SIMD — the core argument of the column-store literature.

### IColumn hierarchy

```
IColumn
 ├─ ColumnVector<T>     contiguous PODArray<T> (UInt8..Float64, dates)
 ├─ ColumnString        chars: PODArray<UInt8> + offsets: PODArray<UInt64>
 ├─ ColumnFixedString   fixed-width contiguous bytes
 ├─ ColumnArray         data: IColumn + offsets: PODArray<UInt64>
 ├─ ColumnNullable      nested: IColumn + null_map: ColumnVector<UInt8>
 ├─ ColumnLowCardinality dictionary: IColumn + indexes: IColumn
 ├─ ColumnConst         single value + size (avoids materializing constants)
 └─ ColumnTuple / ColumnMap / ColumnSparse ...
```

`ColumnSparse` stores only non-default values plus their positions — chosen per part via
`serialization.json` when a column is mostly default (e.g. mostly-zero metrics), and
arithmetic on it stays sparse.

### Function dispatch

`IFunction::executeImpl(arguments, result_type, input_rows_count)` takes input columns
and returns a result column. The hot loop typically does:

```
ColumnPtr executeImpl(cols, n):
    a = cols[0]; b = cols[1]
    if isColumnConst(b): return executeConstRHS(a, constval(b), n)  # specialize
    res = ColumnVector<R>::create(n)
    for i in 0..n:                       # auto-vectorized by the compiler
        res[i] = op(a[i], b[i])          # AVX2/AVX-512 on the contiguous arrays
    return res
```

Specialized template instantiations per numeric type give the autovectorizer tight,
branch-free loops.

### SIMD usage

- Arithmetic / comparison kernels compile to AVX2/AVX-512 over `PODArray`.
- String search uses the **Volnitsky algorithm** (a bigram-hash variant of
  Boyer-Moore-Horspool) for substring/`LIKE`, with SIMD-accelerated scanning of the
  haystack.
- `position`, `hasToken`, `multiSearchAny` have dedicated SIMD paths.

### Expression DAG & short-circuit

`ExpressionActions` (`src/Interpreters/ActionsDAG`) compiles a scalar expression into a
DAG of `IFunction` nodes; common subexpressions are computed once and reused. ClickHouse
also has an **LLVM JIT** that fuses chains of arithmetic functions into a single compiled
kernel when `compile_expressions` is enabled, cutting per-column dispatch (data-centric
codegen in the spirit of Neumann, VLDB 2011). Lazy/short-circuit evaluation
(`short_circuit_function_evaluation`) skips evaluating expensive arguments on rows masked
out by `if`/`and`/`or`, important for nullable and `throwIf`-style functions.

---

## 7. Query Pipeline (IProcessor Model)

Execution is a **pull-based dataflow graph** of `IProcessor` nodes connected by ports
(`src/Processors/`). Each processor declares `InputPort`s and `OutputPort`s and a
`prepare()`/`work()` protocol: `prepare()` inspects port states and returns a status
(`NeedData`, `PortFull`, `Ready`, `Async`, `Finished`); `work()` does the actual CPU
work when `Ready`.

```
   ┌──────────┐   ┌────────────────┐   ┌───────────────┐   ┌──────────┐
   │  Source  │──►│ FilterTransform│──►│ExpressionTrans │──►│ ... Sink │
   │(MergeTree│   │  (WHERE)       │   │ (SELECT exprs) │   └──────────┘
   │  reader) │   └────────────────┘   └───────────────┘
   └──────────┘
        ▲   resize/merge ports fan-in/out for parallel lanes
        │
   ┌────┴─────┬──────────┐
 stream0    stream1    stream2     (one lane per thread; granule ranges split)
```

Node families:

- **ISource**: `MergeTreeSource` (reads granule ranges), `RemoteSource` (reads from a
  shard over native protocol), `NullSource`, generators.
- **ISink / IOutputFormat**: `MergeTreeSink` (writes parts), result-format writers.
- **ITransform**: `FilterTransform`, `ExpressionTransform`, `AggregatingTransform`,
  `MergingAggregatedTransform`, `SortingTransform`, `MergeSortingTransform`,
  `LimitTransform`, `JoiningTransform`, plus `ResizeProcessor` to repartition lanes.

### Execution

`QueryPipelineBuilder` builds the DAG from the `QueryPlan`; `PipelineExecutor` runs it.
The executor treats `work()`-ready processors as tasks in a graph, dispatching them
across `max_threads` worker threads, expanding parallel lanes where the plan allows
(e.g. many `MergeTreeSource`s feeding a parallel `AggregatingTransform`, then a single
`MergingAggregatedTransform`). It is a *push of readiness, pull of data* model — no
thread blocks on I/O if other processors are runnable.

### Async I/O & prefetch

For object-store / remote disks, `ReadBufferFromRemoteFS` issues **asynchronous
prefetch** for the next granules' compressed blocks while the current granule is being
decompressed and filtered, overlapping network latency with CPU. Local reads use the
page cache plus ClickHouse's own mark cache and uncompressed-block cache.

---

## 8. Distributed Query Execution

The `Distributed` table engine is a *view* over a `cluster` of shards (each shard a
local `MergeTree`-family table, possibly replicated). It does no storage of its own.

```
        client
          │ SELECT ... FROM distributed_t GROUP BY k
          ▼
     ┌─────────┐   initiator rewrites query for shards (push down WHERE,
     │initiator│   partial GROUP BY), then fans out the remote query
     └─────────┘
       │     │     │
       ▼     ▼     ▼        per-shard: local read + partial aggregation
   shard0  shard1  shard2
       │     │     │        RemoteBlockInputStream / RemoteSource pulls
       └─────┴─────┘        partial blocks back over the native protocol
          │
     initiator MergingAggregatedTransform → final result
```

### Planning

The initiator splits the query into an **initiator part** and a **remote part**. The
remote part (sent verbatim as SQL plus a "stage" hint) runs on each shard up to
`WithMergeableState` — i.e. it does the local scan, filter, and a *partial* aggregation,
returning aggregate states rather than final values. The initiator then merges.

### Data exchange & two-level aggregation

Inter-shard transport is the **native protocol** (length-prefixed columnar blocks) over
the TCP port. Aggregation uses a **two-level** hash table when the group count is large:
groups are bucketed by a hash prefix (256 buckets) so the merge step on the initiator can
proceed bucket-by-bucket in parallel and shards can stream buckets independently. This is
the classic partition-then-merge scheme that bounds initiator memory.

### Parallel replicas

`parallel_replicas` lets a *single* shard's replicas cooperate on one query: the
coordinator hands out disjoint **granule (mark) ranges** to each replica as a work queue
(`allow_experimental_parallel_reading_from_replicas`), so replicas scan different parts of
the same data in parallel and stream partial results back. This turns replicas (normally
just for HA) into read-scaling units, task-stealing style, with the coordinator balancing
ranges dynamically rather than statically splitting by `parallel_replicas_count`.

---

## 9. ReplicatedMergeTree and ClickHouse Keeper

`ReplicatedMergeTree` replicates via a **shared replicated log** in ZooKeeper or
ClickHouse Keeper. There is no leader for writes in the Raft sense — any replica accepts
inserts and appends log entries; replicas converge by replaying the log.

### Keeper paths

```
/clickhouse/tables/{shard}/{table}/
   ├─ log/                  sequence of replication tasks (log-0000000001, ...)
   ├─ replicas/{replica}/
   │     ├─ log_pointer     last log entry this replica has copied to its queue
   │     ├─ queue/          this replica's pending tasks
   │     ├─ parts/          parts this replica currently has
   │     └─ is_active       ephemeral node = replica is alive
   ├─ block_numbers/        per-partition block-number allocator
   ├─ blocks/               insert dedup hashes (idempotent retries)
   ├─ mutations/            ALTER UPDATE/DELETE definitions
   └─ columns / metadata    current schema + version
```

### Log entry types

| Type | Meaning |
|---|---|
| `GET_PART` | fetch a part another replica produced (e.g. a fresh insert) |
| `MERGE_PARTS` | merge a named set of source parts into a result part |
| `MUTATE_PART` | apply a mutation (UPDATE/DELETE/materialize) to a part |
| `DROP_RANGE` / `DROP_PART` | remove parts in a block range (partition drop, dedup) |
| `ALTER_METADATA` | apply a schema change |

### Replica lifecycle

```
loop:
  if /log has entries beyond my log_pointer:
      copy them into my queue/ ; advance log_pointer
  pick an executable queue task (deps satisfied, parts present):
      GET_PART   → download part from a replica that has it (interserver HTTP)
      MERGE_PARTS→ run the merge locally (deterministic: all replicas get same bytes)
      MUTATE_PART→ apply mutation locally
  on success: register result in replicas/{me}/parts ; remove queue node
```

Merges are *assigned via the log*, not run independently, so every replica produces
byte-identical merged parts (deterministic merge). A replica can instead `GET_PART` the
merged result if it falls behind. Inserts are deduplicated by a hash stored under
`blocks/`, making retries idempotent.

### Part fetch protocol

`GET_PART` downloads the part's files (`*.bin`, `*.mrk3`, `primary.idx`, `checksums.txt`,
...) from a healthy replica over the **interserver HTTP port** (default 9009), validating
each file against `checksums.txt`. With `zero_copy_replication` on shared object storage,
only metadata is fetched and the data blobs are referenced in place.

### ClickHouse Keeper

ClickHouse Keeper (`src/Coordination/`) is a drop-in ZooKeeper replacement built on the
**NuRaft** Raft library. It speaks the ZooKeeper client protocol and stores the same
znode tree, but uses Raft for linearizable consensus (leader election + replicated log +
snapshots) instead of ZAB. It is typically co-deployed with ClickHouse, lowering
operational overhead and improving write latency/throughput for the coordination
workload (many small `create`/`set`/`multi` ops). Compaction-style log + periodic
snapshot keep its on-disk state bounded.

---

## 10. Materialized Views and Projections

### Materialized Views

A ClickHouse MV is an **insert trigger**, not a maintained cache. After a block is
inserted into the *source* table, ClickHouse runs the MV's `SELECT` over **just that
block** and inserts the result into the MV's target table:

```
INSERT INTO source (block B)
      │
      ├─► write B to source's MergeTree
      └─► for each attached MV:
              result = MV.SELECT applied to B          # block-at-a-time
              INSERT result INTO MV.target_table
```

The target is usually an `AggregatingMergeTree` / `SummingMergeTree` holding partial
states, so rollups are maintained incrementally and finalized at read time with `-Merge`
combinators. The MV never re-reads history — it only ever sees newly-inserted blocks,
which is why backfills require `POPULATE` or manual inserts and why joins inside MVs only
see the freshly inserted side. The `to`-table can be shared by multiple MVs.

### Projections

A **projection** is an alternate physical representation stored *inside the same part
directory* (`projections/{name}/`), giving each part a second sort order and/or a
pre-aggregation:

```
part all_1_3_1/
  ├─ {column}.bin ...            (base data, ORDER BY a)
  └─ projections/by_b/
        ├─ {column}.bin ...      (same rows re-sorted by b, or pre-aggregated)
        └─ primary.idx
```

Because the projection lives in the part, it is always consistent with base data and is
maintained automatically through merges (no separate refresh). Two kinds:

- **Normal projection**: same rows, different `ORDER BY` — accelerates predicates on a
  non-PK column.
- **Aggregate projection**: `SELECT ... GROUP BY` stored as aggregate states — answers
  rollup queries by reading far fewer rows.

### Projection selection

During planning the optimizer checks each projection: it is usable iff it **covers all
columns the query needs** (or all needed aggregates) and is expected to read less data.
If multiple qualify, the one minimizing estimated granules read wins; otherwise the query
falls back to base data. `EXPLAIN projections = 1` shows which projection was chosen and
why. The tradeoff is write amplification and storage: every projection is rebuilt on
every merge.

---

## Key Papers & Sources

- Pelkonen, Franklin, Teller, Cavallaro, Huang, Meza, Veeraraghavan, "Gorilla: A Fast,
  Scalable, In-Memory Time Series Database," **VLDB 2015** — DoubleDelta + Gorilla codecs.
- Zukowski, Heman, Nes, Boncz, "Super-Scalar RAM-CPU Cache Compression," **ICDE 2006** —
  lightweight bit-packing / FOR codecs underlying T64.
- Abadi, Madden, Hachem, "Column-Stores vs. Row-Stores: How Different Are They Really?,"
  **SIGMOD 2008** — late materialization, vectorized columnar execution rationale.
- Stonebraker et al., "C-Store: A Column-oriented DBMS," **VLDB 2005** — architectural
  ancestor (read-optimized + writable store + merges).
- Neumann, "Efficiently Compiling Efficient Query Plans for Modern Hardware," **VLDB
  2011** — data-centric codegen, relevant to ClickHouse's LLVM expression JIT.
- Shvachko, Kuang, Radia, Chansler, "The Hadoop Distributed File System," **MSST 2010** —
  context for distributed columnar storage at scale.
- O'Neil, Cheng, Gawlick, O'Neil, "The Log-Structured Merge-Tree (LSM-Tree)," **Acta
  Informatica 1996** — the merge discipline MergeTree adapts.
- ClickHouse blog, "How ClickHouse Keeps Data in Order to Enable Fast Queries" — sparse
  primary index walkthrough.
- ClickHouse technical posts by Alexey Milovidov (architecture, codecs, Keeper).
- ClickHouse source: `src/Storages/MergeTree/`, `src/Processors/`, `src/Columns/`,
  `src/Compression/`, `src/Coordination/`.
