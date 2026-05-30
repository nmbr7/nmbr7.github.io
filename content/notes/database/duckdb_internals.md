+++
title = "Duckdb Internals"
date = 2026-05-29
[taxonomies]
tags = ["database", "duckdb", "columnar", "vectorized", "olap"]
+++


# DuckDB Internals

## Overview

DuckDB is an in-process analytical (OLAP) database, sometimes called "SQLite for analytics." It runs embedded in the host process (no client/server), stores a database in a single file, and is built around a **vectorized, push-based, morsel-driven** execution engine over a **columnar, block-compressed** storage format. The architecture descends from the MonetDB lineage: vectorized execution from MonetDB/X100 (Boncz, Zukowski, Nes, "MonetDB/X100: Hyper-Pipelining Query Execution", CIDR 2005) and morsel-driven parallelism from HyPer/Umbra (Leis et al., "Morsel-Driven Parallelism", SIGMOD 2014; Neumann & Freitag, "Umbra: A Disk-Based System with In-Memory Performance", CIDR 2020).

The foundational system paper is Raasveldt & Mühleisen, "DuckDB: an Embeddable Analytical Database" (SIGMOD 2019 demo). The result-serialization work that motivated the column-at-a-time client protocol is Raasveldt & Mühleisen, "Don't Hold My Data Hostage – A Case for Client Protocol Redesign" (VLDB 2017).

```
   SQL ──▶ Parser ──▶ Binder ──▶ Logical Plan ──▶ Optimizer (DPhyp, pushdown,
                                                              filter injection)
                                                        │
                                                        ▼
   Result ◀── Vectorized Engine ◀── Physical Plan ──▶ Scheduler (morsels, tasks)
                    │                                        │
                    ▼                                        ▼
            Buffer Manager ◀────────────────────▶ Single-File Storage (blocks)
```

---

## 1. Storage Format

DuckDB stores a database as a single file divided into fixed-size **blocks**. Source: `src/storage/`, esp. `single_file_block_manager.cpp`, `block.cpp`, `storage_info.cpp`. The format is described in Mark Raasveldt's storage blog posts (2022) and the official storage docs.

### 1.1 File and block layout

The file begins with three headers, each occupying one "header block" region (4096 bytes each), followed by data blocks.

```
 offset 0           4096            8192            12288        12288 + N*262144
 ┌───────────────┬───────────────┬───────────────┬───────────────────────────┐
 │ Main Header   │ Database      │ Database      │  Data blocks (262144 each) │
 │ (magic+ver)   │ Header  H0    │ Header  H1    │  blk0 blk1 blk2 ...        │
 └───────────────┴───────────────┴───────────────┴───────────────────────────┘
```

**Main header** (`MainHeader`):

| Field            | Size      | Encoding                                  |
|------------------|-----------|-------------------------------------------|
| checksum         | 8 bytes   | uint64 xxHash of header body              |
| magic            | 4 bytes   | ASCII `"DUCK"`                            |
| version_number   | 8 bytes   | uint64 storage format version             |
| flags[4]         | 32 bytes  | 4 × uint64 reserved/feature flags         |
| (library version / source id strings follow in later formats) |

**Database header** (`DatabaseHeader`, two copies H0/H1, alternated for crash safety):

| Field               | Size    | Meaning                                          |
|---------------------|---------|--------------------------------------------------|
| checksum            | 8 bytes | xxHash of header body                            |
| iteration           | 8 bytes | uint64 monotonic write counter                   |
| meta_block          | 8 bytes | block id of metadata catalog root                |
| free_list           | 8 bytes | block id of free-list block chain                |
| block_count         | 8 bytes | total blocks allocated in file                   |
| block_alloc_size    | 8 bytes | the block size used (default 262144)             |
| vector_size         | 8 bytes | STANDARD_VECTOR_SIZE persisted (2048)            |

**Safe writes via header alternation.** A checkpoint writes a *new* `DatabaseHeader` into whichever of H0/H1 is *not* currently active, then fsyncs, then the reader on next open picks the header with the **highest valid `iteration`** whose checksum verifies. A torn write to one header leaves the other intact — this is the ping-pong / shadow-paging discipline that gives atomic checkpoint commit without a separate double-write buffer.

**Block size**: `DEFAULT_BLOCK_ALLOC_SIZE = 262144` (256 KiB). Each block on disk is `block_alloc_size`; the usable payload is `block_size = block_alloc_size − sizeof(checksum)` (8 bytes), with a per-block xxHash64 checksum stored in the block header for torn-page / bit-rot detection.

**Free list.** Freed blocks are recorded in a free-list block chain referenced by `free_list`. On allocation the manager pops a free block id; on truncate the trailing free blocks are released and `block_count` shrinks. This is a classic free-space map, not an LSM-style append log.

**Metadata blocks.** The catalog (schemas, tables, view definitions) and per-table storage metadata (row group pointers, column segment trees, statistics) are serialized into **metadata blocks**, a separate allocation that packs many small metadata entries into 256 KiB blocks via `MetadataManager` to avoid wasting a full block per tiny object.

### 1.2 Row groups and column segments

A table is stored as a list of **row groups**, each holding up to `ROW_GROUP_SIZE = 122880` rows (= 60 × 2048 vectors). Each row group stores one **ColumnData** per column; a column's data is a tree of **column segments**, each segment ≈ one block's worth of compressed values plus per-segment statistics (min, max, null count, distinct estimate).

```
Table
 ├─ RowGroup[0]  (rows 0 .. 122879)
 │    ├─ Column "id"   : Segment(min,max,...) → block 17
 │    ├─ Column "name" : Segment(...)         → block 18..20 (overflow for strings)
 │    └─ row versions / validity
 ├─ RowGroup[1]  (rows 122880 ..)
 └─ ...
```

Row-group-level and segment-level statistics drive **zone-map skipping**: a scan with predicate `WHERE x > 100` skips any segment whose `max <= 100`.

### 1.3 Compression

Compression is chosen **per column segment** by a two-phase scheme (no sampling — DuckDB scans the whole segment, which usually fits in cache): an **Analyze** pass asks every applicable compression function to estimate the final compressed size for that segment, then the cheapest wins, then a **Compress** pass writes it. Each function exposes `init_analyze / analyze / final_analyze / init_compression / compress / compress_finalize` and a matching scan/decompress path (`src/storage/compression/`).

| Scheme        | Applies to        | Mechanism                                                                 |
|---------------|-------------------|---------------------------------------------------------------------------|
| Constant      | any               | all values identical → store one value                                    |
| Uncompressed  | any               | flat array, fallback                                                       |
| RLE           | runs              | (value, run_length) pairs                                                 |
| Bitpacking    | integers          | pack to `ceil(log2(max−min+1))` bits per value, grouped per 1024 values   |
| FOR           | dates/timestamps  | frame = min; bitpack `value − min`                                        |
| Dictionary    | low-cardinality strings | dictionary of distinct strings + bitpacked codes                    |
| FSST          | high-card strings | Fast Static Symbol Table: 1–8 byte symbol codes for frequent substrings (Boncz, Neumann, Leis, "FSST", VLDB 2020) |
| Chimp / Patas | floats            | XOR successive doubles, encode leading/trailing zero runs (Liakos et al., "Chimp", VLDB 2022); Patas is a faster DuckDB variant |
| ALP           | floats            | Adaptive Lossless floating-Point; decompose to int+exponent when decimal-representable (Afroozeh, Kuffo, Boncz, "ALP", SIGMOD 2024) |

Bitpacking, FOR, and dictionary are the workhorses for integer/string analytics; ALP largely supersedes Chimp/Patas for real-valued data in modern DuckDB.

### 1.4 WAL and recovery

Between checkpoints, transactions append to a **write-ahead log** (`src/storage/write_ahead_log.cpp`). WAL entries are typed records: `INSERT_TUPLE`, `DELETE_TUPLE`, `UPDATE_TUPLE`, DDL (`CREATE_TABLE`, `ALTER`), and `CHECKPOINT`. On commit the WAL is flushed/fsynced (durability); recovery replays the WAL from the last checkpoint marker. A **checkpoint** materializes WAL contents into compressed column segments in the main file, writes a fresh alternating `DatabaseHeader`, then truncates the WAL. The single-file format thus provides ACID via WAL (durability/atomicity of small txns) + header ping-pong (atomic checkpoint).

---

## 2. Buffer Manager

`src/storage/buffer_manager.cpp`, `standard_buffer_manager.cpp`.

The buffer pool manages fixed-size **blocks** as **BufferHandle** frames. A block is referenced by a `BlockHandle`; pinning (`Pin()`) loads it into memory and increments a pin count, returning a `BufferHandle` whose lifetime keeps the page resident. Unpinned blocks become eviction candidates. Dirty blocks (modified, e.g. during a checkpoint write) are tracked and flushed to the block manager.

**Replacement policy.** DuckDB uses a **clock / second-chance** approximation of LRU implemented as a queue with a reference bit (`EvictionQueue`). On eviction the sweep skips pinned blocks and gives recently-touched blocks a second chance, which avoids the per-access list surgery of strict LRU under high concurrency.

**Out-of-core execution.** When the buffer pool is full, *temporary* buffers (operator state, not persistent table data) are spilled to a temp directory as `.tmp` files via the same `BlockManager` abstraction. Operators are written to be spill-aware:

- **External hash join**: the build-side hash table is partitioned by hash; if memory is exceeded, partitions are spilled to disk and the join runs partition-by-partition (Grace hash join), re-reading probe rows per partition.
- **External sort**: runs are sorted in memory, spilled as sorted runs, then k-way merged.
- **External aggregation**: partitioned hash table; overflowing partitions spill and are finalized in a later pass.

**Memory pressure.** A global `memory_limit` (default ~80% RAM) is enforced; allocations request reservation from the buffer manager, which evicts unpinned blocks or triggers operator spilling when the limit would be exceeded. Spilling is graceful degradation, not OOM failure.

---

## 3. Vectorized Execution Engine

`src/execution/`, `src/common/types/vector.cpp`. Vectorized execution amortizes per-tuple interpreter overhead across `STANDARD_VECTOR_SIZE = 2048` values processed per call (MonetDB/X100, CIDR 2005).

### 3.1 Vector representations

A `Vector` is a typed container of up to 2048 values with a **physical type** plus a `VectorType`:

```
Vector { type, VectorType, data ptr, validity_mask (bitset), aux (string heap / child) }
```

| VectorType  | Layout                                                              | Use                          |
|-------------|--------------------------------------------------------------------|------------------------------|
| FLAT        | contiguous array of N values + validity bitmask                    | default materialized form    |
| CONSTANT    | single value, logically broadcast to all N rows                    | scalar / literal             |
| DICTIONARY  | a child Vector (dict) + a **selection vector** of indices into it  | output of filters, dict scans|
| SEQUENCE    | (start, increment): value[i] = start + i·increment                 | rowids, `range()`            |

CONSTANT and SEQUENCE store O(1) data for N rows; DICTIONARY shares one physical child across many logical positions — the basis for **zero-copy** transforms.

### 3.2 Selection vectors

Filters do not compact data; they produce a **SelectionVector** — an array of indices (`sel_t`) into the underlying vector picking surviving rows. Operators thread the selection vector downstream, so a filtered vector of 2048 rows down to 30 survivors costs no memcpy until materialization forces a flatten. This is the vectorized analogue of late materialization.

```
data:  [a b c d e f g h ...]   (2048 flat)
sel :  [1 3 6]                 → logical view = [b d g]   (3 rows)
```

### 3.3 Push-based pipelines

DuckDB uses a **push** model (not Volcano pull). A query plan is split into **pipelines**; each pipeline is a chain `Source → Operator* → Sink`. The source produces chunks and *pushes* them through operators into the sink:

```
class Source   { GetData(chunk)  -> produces DataChunk }   // scan, read-group
class Operator { Execute(in,out) -> transforms chunk   }   // filter, project
class Sink      { Sink(chunk); Combine(); Finalize();  }   // HT build, agg, sort
```

A **pipeline breaker** is an operator that must consume its entire input before producing output — it is a `Sink`. The hash-join build side, the aggregate, and the sort are breakers: they materialize into **pipeline-local sink state** during `Sink`, merge thread-local states in `Combine`, finish in `Finalize`, and then act as the **Source** of a *downstream* pipeline. This is how a join splits into a build pipeline (breaker) and a probe pipeline.

```
Pipeline 1 (build):  Scan(B) ─▶ HashJoin.Sink  (builds HT)   [breaker]
Pipeline 2 (probe):  Scan(A) ─▶ HashJoin.Operator(probe HT) ─▶ Aggregate.Sink
```

### 3.4 Key physical operators

**Hash join** (`physical_hash_join.cpp`). Build side fills a `JoinHashTable`: rows are serialized into a row-layout (fixed-width columns inline, variable-width into a string heap) in partitioned blocks; each entry carries a hash and a chain pointer. The HT is a pointer array; collisions chain. A **Bloom filter** (perfect-hash / small filter) is built over build-side keys and pushed to the probe scan so non-matching probe rows are discarded early (and the filter can be injected as a runtime predicate at the scan — see §5).

```
JoinHashTable entry (row layout):
 ┌──────────┬──────────────┬───────────────┬─────────────┐
 │ hash (8) │ next_ptr (8) │ key cols ...  │ payload ... │
 └──────────┴──────────────┴───────────────┴─────────────┘
```

**Aggregate** (`physical_hashaggregate.cpp`). Grouped aggregation uses a **partitioned** hash table: the group key is hashed, the high bits select one of P radix partitions, each with its own hash table. Thread-local partitioned HTs are merged partition-wise in `Combine` (no global lock), and partitions can be finalized/spilled independently. Aggregate states (sum, count, sketches) live inline in the HT row.

**Sort** (`physical_order.cpp`). DuckDB sorts on a **normalized key** (radix-comparable encoding of all ORDER BY columns into a single comparable byte string, NULLs and DESC handled by bit-flips), then runs an **LSD radix sort** on the key within each thread, spills sorted runs if needed, and **k-way merges** runs. Normalized keys make comparison a memcmp, eliminating per-column branchy comparators.

---

## 4. Morsel-Driven Parallelism

`src/parallel/`, esp. `executor.cpp`, `pipeline_executor.cpp`, `task_scheduler.cpp`. Model from Leis et al., SIGMOD 2014.

A **morsel** is a small chunk of the source's input range (e.g. a contiguous slice of a row group / a set of vectors). The source operator hands out morsels on demand. The executor runs a fixed pool of worker threads; each thread repeatedly **pulls a task**, where a task is "run this pipeline on the next morsel."

```
Source range:  [────────── row groups ──────────]
                 m0   m1   m2   m3   m4   m5  ...      (morsels)
Threads:        T0   T1   T2   T0   T1   T2  ...      (dynamic assignment)
```

**Pipeline parallelism.** A pipeline is run by N parallel `PipelineExecutor` instances — one per thread. Each instance owns **thread-local operator state and a thread-local sink state**, but they **share read-only state** such as the join's already-built hash table. Each thread independently grabs morsels, pushes them through its operator chain, and accumulates into its local sink state; `Combine` then merges local states. Because morsels are handed out dynamically, a thread that finishes its work simply requests the next morsel — this is **work stealing** at morsel granularity, giving automatic load balancing across skew without static partitioning.

**Tasks and finish tasks.** The scheduler distinguishes regular pipeline tasks from a **finish task** that runs once after all morsels of a pipeline are consumed (it triggers `Combine`/`Finalize` and readies the dependent pipeline). 

**Dependencies.** Pipelines form a DAG via the `MetaPipeline`: a breaker's build pipeline must `Finalize` before its probe pipeline starts. The executor schedules pipelines respecting these edges; independent pipelines (e.g. two build sides of different joins) run concurrently. Scheduling order is a topological order of the pipeline dependency graph, with all leaves runnable immediately.

---

## 5. Query Optimizer

`src/optimizer/`. Cost-based, operating on the logical plan after binding.

**Join ordering — DPhyp.** DuckDB enumerates join orders with **DPhyp** (Moerkotte & Neumann, "Analysis of Two Existing and One New Dynamic Programming Algorithm for the Generation of Optimal Bushy Join Trees", VLDB 2006). DPhyp models the query as a **hypergraph** (hyperedges capture non-inner / multi-relation predicates) and enumerates **connected subgraph complement pairs (csg-cmp-pairs)** via `EmitCsg`/`EnumerateCmp`, building optimal **bushy** trees by dynamic programming over connected subsets. It avoids generating cross products unless forced, and its complexity tracks the number of connected subgraphs rather than `n!`. For very large join graphs DuckDB falls back to a greedy / IKKBZ-style heuristic.

**Cardinality estimation.** Per Leis et al., "How Good Are Query Optimizers, Really?" (VLDB 2015), cardinality estimation is the dominant source of plan error. DuckDB uses per-column statistics — **min, max, NDV (distinct count, via HyperLogLog), null count**, and (for base tables) zone maps — and estimates join selectivity from NDV (the classic `|R⋈S| ≈ |R||S| / max(NDV_R, NDV_S)`). It also samples base tables to seed statistics. These feed the DPhyp cost model (cost ≈ sum of intermediate cardinalities).

**Rewrite rules.** Filter pushdown (push predicates below joins/projections, down to the scan as zone-map filters), projection pushdown (read only referenced columns), predicate simplification / constant folding, common-subexpression elimination, expression rewriting (e.g. `x IN (...)` → mark join / hash set), and unnesting of correlated subqueries (apply → join decorrelation).

**Dynamic Bloom-filter / runtime-filter injection.** After the build side of a hash join is materialized, DuckDB can inject a Bloom filter (or a min/max range filter) derived from the build keys as a **runtime predicate** on the probe-side scan. This prunes probe row groups/segments via zone maps and discards rows before the probe — a sideways-information-passing optimization that bridges the two pipelines.

**Adaptive ordering.** DuckDB performs some **runtime adaptivity**: the order in which conjunctive scan filters are applied is reordered by observed selectivity, and join build/probe sides can be swapped based on observed (vs estimated) cardinalities, correcting estimation errors during execution.

---

## 6. Parquet Integration

`extension/parquet/`. Parquet is read via late-bound table functions, with pushdown driven by Parquet's own metadata.

- **Row group skipping**: each Parquet row group carries column statistics (min/max/null_count) in its footer; DuckDB evaluates table filters against these and skips entire row groups whose stats cannot satisfy the predicate.
- **Column projection**: only the column chunks for referenced columns are fetched (the columnar layout means unreferenced columns are never read from disk/object store).
- **Predicate pushdown to page level**: with a Parquet **page index** (column index / offset index), DuckDB prunes individual data pages within a row group using per-page min/max, narrowing reads further than row-group granularity.
- **Late materialization**: DuckDB reads filter columns first, evaluates the predicate to produce a selection vector, and only then materializes the remaining projected columns for surviving rows — avoiding decompression of values that get filtered out. This dovetails with §3.2's selection vectors.

Over `httpfs`, these combine to issue **ranged HTTP GETs** for just the needed column-chunk byte ranges of surviving row groups, making remote Parquet scans I/O-frugal.

---

## 7. MVCC and Transactions

`src/transaction/`. DuckDB implements **snapshot isolation** with in-memory multi-versioning, in the HyPer style (Neumann, Mühlbauer, Kemper, "Fast Serializable Multi-Version Concurrency Control for Main-Memory Database Systems", SIGMOD 2015).

**Version storage.** Tables are updated **in place**; the *previous* versions of modified tuples are kept in per-transaction **undo buffers** forming a **version chain**. Each transaction has a `transaction_id` (above a high-water mark while running) and, on commit, a monotonically increasing `commit_id` (timestamp). A row stores the id of the writer; readers compare against their snapshot to decide whether to see the current value or walk the undo chain to the version valid as of their start timestamp.

```
current tuple (in column store)  ──▶  UndoBuffer entry (prev value, txn ts)
                                          └─▶ older entry ...
Reader with snapshot T sees newest version with commit_id ≤ T,
else follows the chain.
```

**Isolation.** Snapshot isolation: each transaction reads a consistent snapshot fixed at start; writes are buffered and made visible atomically at commit by stamping `commit_id`. Write-write conflicts (two txns updating the same row) are detected and the later committer aborts. Inserts/deletes are tracked similarly via version info on row groups (`RowVersionManager`).

**Durability / recovery.** Commits append to the WAL and fsync (§1.4). Recovery replays WAL since the last checkpoint; checkpointing folds committed data into the columnar file and garbage-collects undo buffers whose versions are no longer visible to any active transaction. ACID in the single-file format = WAL (A, D) + snapshot MVCC (I) + checkpoint header ping-pong (atomic, durable consistency point).

---

## 8. Extensions

`src/main/extension/`. Extensions are dynamically loaded shared libraries that register capabilities through the same internal APIs the core uses, via the `ExtensionLoader` / `DatabaseInstance` registries:

- **Scalar / aggregate / table functions** — register a `CreateScalarFunctionInfo`, `AggregateFunction`, or `TableFunction` (with bind, init-global, init-local, and the vectorized `function` callback).
- **Types** — register custom `LogicalType`s and casts.
- **File systems / replacement scans** — register a `FileSystem` (e.g. `httpfs` adds `s3://`, `https://`) or a **replacement scan** so `SELECT * FROM 'file.parquet'` resolves to a table function.
- **Optimizer extensions** — register an `OptimizerExtension` callback that rewrites the logical plan.
- **Storage extensions** — register a custom catalog/transaction manager (`StorageExtension`), enabling foreign databases to appear as attached catalogs.

Notable extensions:

| Extension          | Provides                                                                    |
|--------------------|-----------------------------------------------------------------------------|
| `httpfs`           | HTTP/S3/GCS/Azure filesystems; ranged GETs for remote Parquet/CSV           |
| `iceberg`          | Read Apache Iceberg tables (manifest/snapshot resolution → Parquet scans)   |
| `delta`            | Read Delta Lake tables via the delta-kernel-rs library                      |
| `postgres_scanner` | Query live PostgreSQL over the wire as attached tables (binary COPY, pushdown of filters/projection) — the analogue of DuckDB's own Postgres scanner work |

The `postgres_scanner` and `delta`/`iceberg` extensions exemplify the storage-extension pattern: a foreign system is attached as a catalog, its tables surface as table functions, and filter/projection pushdown is forwarded to the source (Postgres `WHERE`/column lists; Iceberg/Delta row-group stats), so DuckDB's vectorized engine consumes only the needed data.

---

## Key Papers & Resources

- Raasveldt, Mühleisen, "DuckDB: an Embeddable Analytical Database", SIGMOD 2019 (demo).
- Raasveldt, Mühleisen, "Don't Hold My Data Hostage – A Case for Client Protocol Redesign", VLDB 2017.
- Boncz, Zukowski, Nes, "MonetDB/X100: Hyper-Pipelining Query Execution", CIDR 2005.
- Leis, Boncz, Kemper, Neumann, "Morsel-Driven Parallelism: A NUMA-Aware Query Evaluation Framework for the Many-Core Age", SIGMOD 2014.
- Moerkotte, Neumann, "Analysis of Two Existing and One New Dynamic Programming Algorithm for the Generation of Optimal Bushy Join Trees" (DPhyp), VLDB 2006.
- Leis, Gubichev, Mirchev, Boncz, Kemper, Neumann, "How Good Are Query Optimizers, Really?", VLDB 2015.
- Neumann, Mühlbauer, Kemper, "Fast Serializable Multi-Version Concurrency Control for Main-Memory Database Systems", SIGMOD 2015.
- Neumann, Freitag, "Umbra: A Disk-Based System with In-Memory Performance", CIDR 2020.
- Boncz, Neumann, Leis, "FSST: Fast Random Access String Compression", VLDB 2020.
- Liakos, Papakonstantinopoulou, Kotidis, "Chimp: Efficient Lossless Floating Point Compression for Time Series Databases", VLDB 2022.
- Afroozeh, Kuffo, Boncz, "ALP: Adaptive Lossless floating-Point Compression", SIGMOD 2024.
- Mark Raasveldt, "Lightweight Compression in DuckDB", duckdb.org blog, 2022-10-28.
- DuckDB source: `src/storage/` (single_file_block_manager, compression/), `src/execution/` (physical_hash_join, physical_hashaggregate, physical_order), `src/parallel/` (executor, task_scheduler), `src/optimizer/` (join_order/, filter_pushdown), `src/transaction/`.
