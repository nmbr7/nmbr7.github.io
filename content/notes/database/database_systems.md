+++
title = "Database Systems"
date = 2026-02-10
[taxonomies]
tags = ["survey", "postgresql", "duckdb", "cockroachdb", "clickhouse", "neon", "tigerbeetle"]
+++


# Database & Data Systems Research

A comprehensive technical reference covering architecture, key techniques, and differentiators for modern database systems across OLTP, OLAP, streaming, vector, geospatial, and edge categories.

---

## Table of Contents

1. [Umbra](#1-umbra)
2. [PostgreSQL](#2-postgresql)
3. [MySQL](#3-mysql)
4. [CockroachDB](#cockroachdb)
5. [DuckDB](#5-duckdb)
6. [Feldera](#6-feldera)
7. [ClickHouse](#7-clickhouse)
8. [RisingWave](#8-risingwave)
9. [Apache Comet](#9-apache-comet)
10. [Apache Spark](#10-apache-spark)
11. [ParadeDB](#11-paradedb)
12. [Neon](#12-neon)
13. [Turso](#13-turso)
14. [Apache Iceberg & Vendors](#14-apache-iceberg-vendors)
15. [Neo4j](#15-neo4j)
16. [Apache Sedona (SedonaDB)](#16-apache-sedona-sedonadb)
17. [Distributed PostgreSQL](#17-distributed-postgresql)
18. [Qdrant](#18-qdrant)
19. [TigerBeetle](#19-tigerbeetle)

---

## 1. Umbra

**Category:** Research RDBMS (OLAP-focused with HTAP capabilities)
**Origin:** TU Munich, Chair of Database Systems (Prof. Thomas Neumann)
**Predecessor:** HyPer (in-memory HTAP system, acquired by Tableau/Salesforce)

### Overview

Umbra is a disk-based relational database that achieves in-memory-class performance. It is the spiritual successor to HyPer, designed to remove HyPer's limitation of requiring all data to fit in RAM while retaining its breakthrough query processing speed.

### Architecture

- **Disk-based but memory-speed:** Uses a buffer manager engineered so overhead is near-zero when data fits in memory; degrades gracefully when data exceeds RAM.
- **Compiled query execution:** Queries compiled to native code, not interpreted via Volcano-style iterators.
- **Morsel-driven parallelism:** Work divided into small cache-friendly chunks processed by worker threads.
- **Pointer-free on-disk structures:** All persistent data uses offsets (not raw pointers) so pages can be freely relocated.

### Key Techniques

#### Variable-Size Buffer Management (LeanStore heritage)
- **Variable-size pages** (64 KB to several MB) instead of traditional fixed 8/16 KB pages; reduces per-tuple overhead and suits analytical scans.
- **Pointer swizzling:** On-disk page IDs replaced with in-memory pointers when resident. Page access becomes a direct pointer dereference (no hash-table lookup in buffer pool).
- **Optimistic latching:** Version-counter-based reads instead of heavy read-write locks. Readers check a version counter before/after; if changed, retry. Dramatically reduces synchronization overhead.
- **Hot/Cooling/Frozen eviction:** Pages transition through states for eviction decisions, avoiding overhead of a global LRU list.

#### Adaptive Multi-Tier Query Compilation ("Flying Start")
Unlike HyPer (which used LLVM directly, causing compilation latency), Umbra uses a custom lightweight IR:
- **Tier 0 - Interpretation/Bytecode:** Near-zero compilation overhead for very short queries.
- **Tier 1 - Fast compilation (Umbra backend):** Custom lightweight compiler generates machine code in microseconds to low milliseconds. Basic register allocation and instruction selection, skips expensive optimizations.
- **Tier 2 - Optimized compilation (LLVM):** Background LLVM compilation for long-running queries; hot-swaps running query to optimized code mid-execution.

#### Data-Centric Code Generation (Push-Based)
- Tight loops where data is pushed from producers to consumers.
- Tuple data kept in CPU registers as long as possible, minimizing materialization.
- Pipeline breakers (hash joins, sorts) define boundaries of compiled code fragments.

#### Morsel-Driven Parallelism
- **Morsels:** Input divided into ~10,000-tuple chunks. Workers process one morsel at a time.
- **Work-stealing scheduler:** Central dispatcher assigns morsels; threads grab next available when done.
- **NUMA-awareness:** Scheduler assigns morsels to threads local to the data's NUMA node.
- **Elasticity:** Dynamic parallelism adjustment; threads can be reassigned mid-query for responsive multi-query execution.

### Storage Engine
- **Columnar/PAX layout** within pages for compression and scan performance.
- **Lightweight compression:** Dictionary encoding, RLE, frame-of-reference encoding at memory bandwidth speeds.
- **Adaptive Radix Tree (ART)** as primary index structure (developed at TUM). Cache-friendly, adapts node sizes to key distribution, excellent for both point lookups and range scans.

### Transaction Support
- MVCC with snapshot isolation for readers.
- Write-ahead logging (WAL) for durability and crash recovery.

### Performance
- TPC-H SF=100 (100 GB): single-digit seconds total for all 22 queries on modern hardware.
- Sub-millisecond compilation for simple queries.
- Buffer manager overhead when data fits in memory: < 5%.
- Near-linear scaling across many cores.

### Key Papers
- Neumann (VLDB 2011): "Efficiently Compiling Efficient Query Plans for Modern Hardware"
- Leis et al. (SIGMOD 2014): "Morsel-Driven Parallelism"
- Kersten, Leis, Neumann (VLDB 2021): "Tidy Tuples and Flying Start"
- Neumann, Freitag (CIDR 2020): "Umbra: A Disk-Based System with In-Memory Performance"
- Leis et al. (ICDE 2013): "The Adaptive Radix Tree: ARTful Indexing for Main-Memory Databases"

---

## 2. PostgreSQL

**Category:** General-purpose ORDBMS (OLTP + analytical capabilities)
**License:** PostgreSQL License (permissive, similar to MIT/BSD)
**Written in:** C

### Overview

PostgreSQL is the world's most advanced open-source relational database. It combines SQL compliance, extensibility, and robustness, serving as the foundation for dozens of derivative systems (Neon, Citus, Aurora PostgreSQL, AlloyDB, etc.).

### Architecture

- **Process-per-connection model:** Each client connection gets a dedicated backend process (forked from the postmaster). Shared memory (shared buffers) provides inter-process communication.
- **Shared buffer pool:** Configurable shared memory area for caching disk pages; uses a clock-sweep eviction algorithm.
- **WAL (Write-Ahead Logging):** All modifications written to WAL before data files. Supports crash recovery, point-in-time recovery (PITR), and streaming replication.
- **MVCC (Multi-Version Concurrency Control):** Each transaction sees a snapshot; old row versions (dead tuples) accumulate until VACUUM reclaims them.

### Key Techniques

#### Storage Engine (Heap-based)
- **Heap tables:** Rows stored in pages (8 KB default), no inherent ordering. Updates create new row versions in-place or on a different page.
- **TOAST (The Oversized-Attribute Storage Technique):** Large field values compressed and/or stored out-of-line in a separate TOAST table.
- **Visibility maps and free space maps:** Track which pages are all-visible (for index-only scans) and which have free space (for inserts).
- **Tablespaces:** Map logical storage to physical directories.

#### MVCC Implementation
- **Tuple headers contain xmin/xmax:** Transaction IDs marking when a tuple was created/deleted.
- **Snapshot isolation:** Read transactions see a consistent snapshot; no read locks needed.
- **Serializable Snapshot Isolation (SSI):** PostgreSQL implements true serializable isolation via predicate locking and conflict detection (Cahill et al., 2008).
- **VACUUM:** Background process reclaims dead tuples. Autovacuum handles this automatically. Can be a source of contention on write-heavy workloads ("VACUUM bloat" problem).

#### Indexing
- **B-tree:** Default index type, highly optimized with Lehman-Yao concurrent B-tree algorithm. Supports deduplication (PG 13+).
- **GiST (Generalized Search Tree):** Extensible indexing framework for complex data types (geometric, full-text, range types).
- **GIN (Generalized Inverted Index):** For composite values (arrays, JSONB, full-text search tsvector).
- **BRIN (Block Range Index):** Lightweight index storing min/max per range of physical blocks; excellent for naturally ordered data (timestamps, sequential IDs).
- **SP-GiST:** Space-partitioned GiST for data with non-balanced decomposition (phone numbers, IP addresses).
- **Hash indexes:** Made crash-safe in PG 10+.

#### Query Processing
- **Cost-based optimizer:** Uses statistics (histograms, MCV lists, correlation) collected by ANALYZE. Considers sequential vs. random I/O costs, CPU costs, and parallelism.
- **Parallel query execution (PG 9.6+):** Parallel sequential scans, hash joins, merge joins, aggregations. Gather/Gather Merge nodes collect results from parallel workers.
- **JIT compilation (PG 11+):** Uses LLVM to JIT-compile expressions, tuple deforming, and qualifying conditions for complex queries.
- **Partitioning:** Declarative partitioning (range, list, hash) with partition pruning at both plan time and execution time.

#### Extensibility
- **Custom types, operators, functions, index methods, and procedural languages** (PL/pgSQL, PL/Python, PL/Rust, etc.).
- **Foreign Data Wrappers (FDW):** Query external data sources (other databases, files, APIs) as if they were local tables.
- **Extensions:** Rich ecosystem (PostGIS, pg_stat_statements, pgvector, Citus, TimescaleDB, etc.).
- **Logical replication:** Publish/subscribe model for selective table replication.

#### Replication & HA
- **Streaming replication:** WAL records streamed to standby servers for physical replication. Synchronous or asynchronous.
- **Logical replication (PG 10+):** Row-level changes published/subscribed at table granularity.
- **Patroni, pg_auto_failover, Stolon:** External HA management tools.

### Strengths
- Extreme extensibility and standards compliance.
- Rich type system (JSONB, arrays, ranges, geometric types, custom types).
- Strong correctness guarantees (serializable isolation, robust MVCC).
- Massive ecosystem of extensions and derivative products.

### Weaknesses
- Process-per-connection model limits connection scalability (mitigated by pgBouncer/PgCat).
- VACUUM overhead for write-heavy workloads.
- No built-in columnar storage (requires extensions like Citus columnar or pg_analytics).
- Single-node by default; horizontal scaling requires external solutions.

---

## 3. MySQL

**Category:** General-purpose RDBMS (OLTP-focused)
**License:** GPL v2 (with commercial license from Oracle)
**Written in:** C, C++

### Overview

MySQL is the world's most popular open-source relational database, powering much of the web. With the InnoDB storage engine (default since MySQL 5.5), it provides ACID-compliant transactional storage.

### Architecture

- **Thread-per-connection model:** Unlike PostgreSQL's process model, MySQL uses threads within a single process.
- **Pluggable storage engines:** InnoDB (default, transactional), MyISAM (legacy, non-transactional), Memory, NDB (Cluster), etc. The query optimizer sits above the storage engine layer.
- **SQL layer:** Parser, optimizer, and executor are decoupled from storage.

### Key Techniques

#### InnoDB Storage Engine
- **Clustered index (B+ tree):** Data is physically stored in primary key order. Secondary indexes store the primary key as a pointer (not a page/row locator), enabling consistent lookups after page splits.
- **Buffer pool:** Large shared memory area caching data and index pages. Uses a modified LRU with a young/old sublist to prevent full-table scans from flushing hot pages.
- **Redo log (WAL):** Circular redo log files for crash recovery. Write-ahead logging ensures durability.
- **Undo log:** Stored in a separate undo tablespace; used for MVCC rollback segments and consistent reads.
- **MVCC:** Read views use undo log to reconstruct old row versions. Does not suffer from PostgreSQL's VACUUM problem since undo is purged separately.
- **Change buffer:** Buffers secondary index changes for non-unique indexes, merging them later during reads or background operations. Reduces random I/O.
- **Adaptive hash index:** InnoDB automatically builds an in-memory hash index on top of B-tree pages that are frequently accessed, providing O(1) lookups.
- **Doublewrite buffer:** Writes pages to a doublewrite area before their actual locations, protecting against partial page writes during crashes.

#### Replication
- **Binlog-based replication:** Statement-based (SBR), row-based (RBR), or mixed. The binary log records all data changes.
- **GTID (Global Transaction Identifiers):** Simplifies replication topology management and failover.
- **Group Replication:** Multi-primary or single-primary mode using Paxos-based consensus for automatic failover and conflict detection.
- **InnoDB Cluster:** Integrated HA solution combining Group Replication, MySQL Shell, and MySQL Router.
- **Semi-synchronous replication:** Primary waits for at least one replica to acknowledge WAL receipt before committing.

#### Query Processing
- **Cost-based optimizer** with histograms (MySQL 8.0+), index statistics, and join optimizations.
- **Hash joins (MySQL 8.0.18+):** Previously only nested-loop joins were supported.
- **Window functions, CTEs, lateral derived tables (MySQL 8.0+).**
- **Parallel query** is limited compared to PostgreSQL; read-ahead and adaptive scanning provide some parallelism.

### MySQL Variants
- **Percona Server:** Drop-in replacement with performance improvements (thread pool, compression, enhanced InnoDB).
- **MariaDB:** Fork with additional storage engines (Aria, ColumnStore for analytics, Spider for sharding), and divergent feature development.

### Strengths
- Excellent read-heavy OLTP performance.
- Mature replication ecosystem.
- Clustered index design avoids secondary lookups for PK queries.
- Change buffer and adaptive hash index reduce I/O.

### Weaknesses
- Historically weaker SQL compliance than PostgreSQL (improving in MySQL 8.0+).
- Limited extensibility compared to PostgreSQL.
- Single storage engine boundary (query layer cannot optimize across engines).
- DDL operations historically blocking (Online DDL improved in 8.0 but still has limitations).

---

## 4. CockroachDB

**Category:** Distributed SQL (NewSQL), strongly consistent
**License:** BSL 1.1 (transitions to Apache 2.0 after 3 years)
**Written in:** Go

### Overview

CockroachDB is a distributed SQL database inspired by Google Spanner. It provides serializable transactions, horizontal scalability, and automatic geo-replication while maintaining PostgreSQL wire protocol compatibility.

### Architecture

- **Shared-nothing, distributed:** Data distributed across nodes using range-based sharding. Each range (default ~512 MB) is a unit of replication.
- **Layered architecture:**
  - **SQL layer:** PostgreSQL-compatible parser, cost-based optimizer, distributed execution engine.
  - **Transaction layer:** Distributed transaction coordination using a hybrid logical clock (HLC) for ordering.
  - **Distribution layer:** Range-based partitioning, lease-based routing, automatic rebalancing.
  - **Replication layer:** Raft consensus protocol for each range's replicas.
  - **Storage layer:** Pebble (custom LSM-tree key-value store, successor to RocksDB).

### Key Techniques

#### Distributed Transactions
- **Serializable isolation by default:** Uses a combination of MVCC timestamps and a transaction write pipeline.
- **Transaction record:** A transaction is committed by writing a transaction record to a range; parallel writes can proceed before the commit point.
- **Parallel commits:** Optimization that allows the client to be notified of commit while the transaction record is still being written, reducing latency.
- **Hybrid Logical Clocks (HLC):** Combines physical wall-clock time with a logical counter to provide causal ordering without requiring GPS/atomic clocks (unlike Spanner's TrueTime). Trade-off: read restart mechanism needed when clock skew is detected.
- **Read refreshes:** If a transaction's read timestamp becomes stale, CockroachDB attempts to refresh (re-validate) reads at a newer timestamp instead of aborting.
- **Closed timestamps:** Each range publishes a closed timestamp guaranteeing no future writes below it; enables consistent follower reads.

#### Raft Consensus
- **Per-range Raft groups:** Each range (typically 3 or 5 replicas) runs an independent Raft consensus group.
- **Leaseholder:** One replica per range holds the lease and serves all reads/writes. Leases are Raft-based.
- **Range splits and merges:** Automatic, based on data size and load. Metadata ranges track the mapping.

#### Storage: Pebble
- **LSM-tree (Log-Structured Merge-tree):** Write-optimized with compaction. Replaced RocksDB with Pebble (written in Go) for tighter integration and better performance control.
- **MVCC encoding:** Keys are encoded with timestamps for multi-version storage. Garbage collection of old versions is range-based.

#### Geo-Distribution
- **Geo-partitioning:** Pin data to specific regions (e.g., EU user data stays in EU).
- **Global tables:** Replicate reference data everywhere for low-latency reads.
- **Follower reads:** Allow stale (but bounded-staleness) reads from the nearest replica.
- **Multi-region abstractions:** SURVIVE ZONE FAILURE vs. SURVIVE REGION FAILURE configurations.

### Performance Characteristics
- Optimized for geo-distributed OLTP workloads.
- Higher latency per-transaction than single-node databases due to consensus overhead.
- Horizontal write scalability through range-based sharding.
- Not optimized for heavy analytical workloads (no columnar storage).

---

## 5. DuckDB

**Category:** In-process OLAP database (embedded analytical)
**License:** MIT
**Written in:** C++

### Overview

DuckDB is an in-process analytical database, often described as "SQLite for analytics." It runs embedded within applications (no separate server), providing fast columnar analytical query processing.

### Architecture

- **In-process / embedded:** Runs within the host process (Python, R, Java, Node.js, etc.). No client-server protocol overhead.
- **Single-file database:** Stores the entire database in a single file (or operates in-memory).
- **Vectorized execution engine:** Processes data in vectors/batches (typically 2048 values) rather than tuple-at-a-time.

### Key Techniques

#### Vectorized Execution (Push-Based)
- **Column-oriented batch processing:** Operators work on vectors of column values. Tight loops over arrays exploit CPU caches, SIMD, and branch prediction.
- **Pipeline-based execution:** Push-based model where source operators push data through pipelines. Pipelines break at materialization points (hash tables, sorts).
- **Morsel-driven parallelism:** Similar to HyPer/Umbra; work divided into morsels for multi-core scaling.

#### Storage
- **Columnar storage format:** Data stored in row groups, with each column stored contiguously. Supports lightweight compression (dictionary, RLE, bitpacking, frame-of-reference, Chimp/Patas for floating point).
- **Buffer manager with spilling:** Can operate on datasets larger than memory by spilling to disk.
- **Persistent storage:** Single-file format with ACID transactions and WAL.
- **Efficient Parquet/CSV/JSON reader:** Direct querying of external files without explicit loading. Parquet reader uses predicate pushdown, column projection, and row group skipping.

#### Query Optimizer
- **Cost-based optimizer** with cardinality estimation, join ordering (dynamic programming for small queries, greedy for large).
- **Filter pushdown, projection pushdown, common subexpression elimination.**
- **Adaptive/dynamic filter generation:** Bloom filters pushed between operators.
- **Parallel execution:** Automatic parallelization of scans, joins, aggregations across cores.

#### Zero-Copy Integration
- **Apache Arrow integration:** Zero-copy data transfer with Arrow-compatible systems.
- **Direct Pandas/Polars integration:** Can query Pandas DataFrames and Polars LazyFrames without copying data.
- **Extensions:** httpfs (query files over HTTP/S3), spatial, json, postgres_scanner, sqlite_scanner, iceberg, delta, etc.

### Strengths
- No server overhead; ideal for data science, local analytics, ETL.
- Excellent single-node analytical performance (competitive with dedicated OLAP systems).
- Rich format support (Parquet, CSV, JSON, Arrow, Iceberg, Delta Lake).
- Minimal dependencies, easy deployment.
- Full SQL support including window functions, CTEs, GROUPING SETS, PIVOT.

### Use Cases
- Interactive data analysis in notebooks (Python, R, Julia).
- Embedded analytics in applications.
- ETL/data transformation pipelines.
- Local-first analytical processing before loading to a warehouse.

---

## 6. Feldera

**Category:** Incremental Compute Engine / Streaming SQL
**License:** Open-source
**Written in:** Rust

### Overview

Feldera is an incremental compute engine based on the **DBSP (Database Stream Processor)** theory, developed by the same researchers who created the formal mathematical framework for incremental view maintenance. It processes data changes incrementally rather than re-computing results from scratch.

### Core Theory: DBSP

- **DBSP (Database Stream Processor)** is a formal mathematical framework (Budiu et al., VLDB 2023) that provides a unified theory for incremental computation over both streaming and batch data.
- **Z-sets:** The core data model. A Z-set is a generalized multiset where elements have integer weights (positive = insertion, negative = deletion). This elegantly captures both additions and removals in a single algebraic structure.
- **Lifting:** Any computation over Z-sets can be "lifted" to operate incrementally over streams of Z-sets (changes). This is the key insight: any query that works on complete datasets can be automatically transformed into an incremental version that processes only changes.
- **Bilinearity of joins:** Joins are decomposed into components that process changes from each side independently, enabling efficient incremental join maintenance.

### Architecture

- **SQL-first interface:** Write standard SQL queries (including complex joins, aggregations, window functions, and nested subqueries). Feldera compiles them into incremental dataflow circuits.
- **Incremental dataflow runtime:** The Rust-based runtime executes the compiled circuits, processing only the delta (change) at each step.
- **Input connectors:** Kafka, Debezium (CDC), HTTP, database connectors for ingesting change streams.
- **Output connectors:** Push incremental results to downstream systems.

### Key Techniques

- **Automatic incrementalization:** Converts any SQL query into an incremental pipeline without manual optimization. The DBSP theory guarantees correctness.
- **Nested incremental computation:** Handles complex SQL features (GROUP BY, HAVING, nested subqueries, DISTINCT, window functions) incrementally through recursive application of the Z-set algebra.
- **Consistent snapshots:** Maintains consistency across the entire pipeline even with multiple input sources changing simultaneously.
- **Backpressure and flow control:** Runtime handles varying input rates gracefully.

### Differentiators
- Unlike traditional streaming systems (Flink, Spark Streaming) that require manual state management and watermarking, Feldera handles this automatically via DBSP theory.
- Unlike materialized view systems that do limited incremental maintenance, Feldera incrementalizes arbitrary SQL.
- Strong correctness guarantees grounded in formal theory.

---

## 7. ClickHouse

**Category:** Column-oriented OLAP database
**License:** Apache 2.0
**Origin:** Yandex (now independent company ClickHouse Inc.)
**Written in:** C++

### Overview

ClickHouse is a high-performance column-oriented database for online analytical processing (OLAP). Known for extreme query speed on large datasets, particularly for aggregation-heavy workloads.

### Architecture

- **Column-oriented storage:** Data stored by column, enabling efficient compression and vectorized processing.
- **Shared-nothing distributed:** Cluster of shards, each containing one or more replicas. Data distributed via sharding keys.
- **MergeTree engine family:** The core storage engine; data is organized into parts that are periodically merged (similar to LSM-tree concepts).

### Key Techniques

#### MergeTree Storage Engine
- **Parts and merges:** Inserts create new data parts (sorted by primary key). Background merges combine parts, applying deduplication (ReplacingMergeTree), aggregation (AggregatingMergeTree), or TTL expiration (TTL rules).
- **Sparse primary index:** Rather than indexing every row, stores primary key values at configurable granularity (default 8192 rows). Enables fast range lookups with minimal memory overhead.
- **Data skipping indexes:** Additional lightweight indexes on columns (minmax, set, bloom_filter, ngrambf_v1, tokenbf_v1) that skip granules not matching the query predicate.
- **Partitioning:** Data partitioned by a configurable expression (typically time-based). Partition pruning eliminates irrelevant partitions at query time.

#### Compression
- **Per-column compression:** LZ4 (default, fast), ZSTD (higher ratio), DoubleDelta (timestamps/sequences), Gorilla (floating point), T64 (integer encoding).
- **Codecs can be chained:** e.g., Delta + ZSTD for timestamps achieves very high compression ratios.
- **Specialized encodings:** Automatic detection and use of dictionary encoding, run-length encoding within the column storage.

#### Vectorized Execution
- **Column-at-a-time processing:** Operations process entire column vectors, exploiting SIMD instructions and CPU cache efficiency.
- **Code generation (limited):** Some dynamic specialization for expression evaluation.

#### Distributed Query Processing
- **Distributed tables:** Virtual tables that fan out queries to shards and merge results.
- **Replicated tables (ReplicatedMergeTree):** Use ZooKeeper/ClickHouse Keeper (Raft-based) for coordination. Replicas are eventually consistent; each replica independently downloads and merges parts.
- **Parallel replicas:** For a single query, spread work across replicas of the same shard for faster processing.

#### Materialized Views
- **Incremental materialized views:** Trigger on INSERT; transform and aggregate incoming data in real-time. Chain multiple views for multi-stage ETL.
- **Projections:** Pre-sorted/pre-aggregated copies of data within the same table; optimizer automatically selects the best projection.

### Specialized Table Engines
- **AggregatingMergeTree:** Pre-aggregates data during merges using partial aggregate states (e.g., quantile digests, HLL for uniqCombined).
- **CollapsingMergeTree / VersionedCollapsingMergeTree:** Handles mutable data via sign columns.
- **MaterializedMySQL / MaterializedPostgreSQL:** Real-time CDC replication from MySQL/PostgreSQL.

### Performance Characteristics
- Can process billions of rows per second for simple aggregations on a single node.
- Excellent compression ratios (often 10-20x on real-world data).
- High ingest throughput for append-heavy workloads.
- Not designed for point-lookup OLTP; best for analytical/aggregation queries.

---

## 8. RisingWave

**Category:** Streaming Database (stream processing + SQL)
**License:** Apache 2.0
**Written in:** Rust

### Overview

RisingWave is a cloud-native streaming database that processes event streams using SQL, maintaining **materialized views** that are incrementally updated as new data arrives. It is PostgreSQL wire-compatible.

### Architecture

- **Separation of storage and compute:**
  - **Compute nodes:** Stateless stream processing workers that execute streaming operators (joins, aggregations, filters).
  - **Compactor nodes:** Handle LSM-tree compaction for the shared storage layer.
  - **Meta node:** Manages cluster metadata, scheduling, and DDL operations.
  - **Object storage backend:** Shared state stored in S3-compatible object storage (or local disk for development). This is the "Hummock" storage engine.

- **Hummock (LSM-tree on shared storage):**
  - An LSM-tree-based key-value store designed for cloud object storage.
  - SSTables stored in S3; uses local SSD as a tiered cache.
  - Enables elastic scaling since compute nodes are stateless (all state is in shared storage).
  - Supports MVCC for consistent snapshots across streaming operators.

### Key Techniques

#### Streaming Materialized Views
- **Incrementally maintained materialized views:** SQL queries defined as `CREATE MATERIALIZED VIEW` are compiled into streaming dataflow plans. As source data changes, the views are updated incrementally.
- **Consistency:** Provides **snapshot consistency** across materialized views via barrier-based checkpointing (similar to Flink's Chandy-Lamport checkpoints).
- **Complex SQL support:** Joins (including temporal joins), aggregations, window functions, subqueries, UNION, INTERSECT, EXCEPT -- all maintained incrementally.

#### Source & Sink Connectors
- **Sources:** Kafka, Pulsar, Kinesis, CDC (Debezium format, direct MySQL/PostgreSQL CDC), S3 file sources.
- **Sinks:** Kafka, JDBC databases, Iceberg, Delta Lake, Elasticsearch, Redis, etc.
- **Direct PostgreSQL CDC:** Built-in connector to replicate PostgreSQL tables as streaming sources.

#### PostgreSQL Compatibility
- **Wire-compatible:** Connect with any PostgreSQL client (psql, JDBC, etc.).
- **SQL syntax:** Supports most PostgreSQL SQL syntax.
- **Serving queries:** Materialized views can be queried directly with point lookups and range scans, providing a serving layer.

### How It Differs from Flink / ksqlDB
- **vs. Flink:** RisingWave is a full database (stores state, serves queries); Flink is a stream processing framework (requires external state stores for serving). RisingWave uses SQL natively; Flink uses Flink SQL on top of a Java/Scala API.
- **vs. ksqlDB:** RisingWave supports complex multi-way joins and subqueries; ksqlDB has limited SQL capabilities. RisingWave scales compute and storage independently; ksqlDB is tightly coupled to Kafka.

---

## 9. Apache Comet

**Category:** Native query acceleration for Apache Spark
**License:** Apache 2.0
**Written in:** Rust (native engine), Scala/Java (Spark integration)

### Overview

Apache Comet (incubating) is a **native vectorized query execution engine** that accelerates Apache Spark workloads. It is a plugin for Spark that replaces Spark's JVM-based physical operators with native (Rust-based) operators built on **Apache DataFusion** and **Apache Arrow**.

### Architecture

- **Spark plugin model:** Comet integrates as a Spark plugin that intercepts the physical plan and replaces eligible operators with native equivalents.
- **Two execution modes:**
  - **Columnar Shuffle:** Replaces Spark's row-based shuffle with an Arrow-based columnar shuffle, reducing serialization/deserialization overhead.
  - **Native Execution:** Replaces entire physical operators (scans, filters, projections, aggregations, joins, sorts) with DataFusion-based native operators.
- **Apache Arrow as the in-memory format:** All data exchanged between native operators uses Arrow columnar format, enabling zero-copy processing.

### Key Techniques

- **DataFusion execution engine:** Leverages the Rust-based DataFusion query engine for native operator execution. DataFusion provides vectorized processing, SIMD-optimized kernels, and memory-efficient execution.
- **Transparent fallback:** If an operator cannot be executed natively (unsupported expression, data type, etc.), Comet transparently falls back to Spark's JVM execution.
- **Parquet-native reads:** Native Parquet reader that bypasses Spark's Java-based reader, providing faster column decoding, predicate pushdown, and lazy materialization.
- **Memory management:** Uses Arrow's memory allocator with configurable limits; integrates with Spark's memory management framework.

### Performance
- 2-8x speedup on common Spark workloads (TPC-H, TPC-DS benchmarks).
- Largest gains on CPU-bound operations (expression evaluation, hashing, sorting) where native code vastly outperforms JVM.
- Reduced memory pressure from efficient Arrow-based columnar representation.

### Differentiators
- **Drop-in acceleration:** Requires minimal configuration changes to existing Spark jobs.
- **Preserves Spark semantics:** Results are bit-for-bit identical to Spark's JVM execution (aim is full compatibility).
- **Part of the Arrow ecosystem:** Benefits from the broader Arrow/DataFusion community's optimizations.

---

## 10. Apache Spark

**Category:** Unified analytics engine (batch + stream processing)
**License:** Apache 2.0
**Written in:** Scala, Java, Python

### Overview

Apache Spark is the dominant open-source engine for large-scale data processing, supporting batch processing, stream processing (Structured Streaming), machine learning (MLlib), and graph computation (GraphX).

### Architecture

- **Driver-executor model:** The driver program orchestrates the computation; executors run on cluster worker nodes.
- **Cluster managers:** Standalone, YARN, Mesos, Kubernetes.
- **RDD (Resilient Distributed Datasets):** The foundational abstraction -- immutable, partitioned collections with lineage for fault tolerance. Rarely used directly now; DataFrame/Dataset APIs are preferred.
- **Catalyst optimizer:** Rule-based and cost-based optimizer for Spark SQL/DataFrames.
- **Tungsten execution engine:** Off-heap memory management and whole-stage code generation.

### Key Techniques

#### Catalyst Optimizer
- **Rule-based optimization:** Predicate pushdown, constant folding, column pruning, boolean simplification.
- **Cost-based optimization (CBO):** Join reordering based on table/column statistics.
- **Adaptive Query Execution (AQE, Spark 3.0+):** Re-optimizes query plans at runtime based on actual data statistics:
  - Coalesces post-shuffle partitions to avoid small partitions.
  - Converts sort-merge joins to broadcast hash joins if one side is small.
  - Handles skewed joins by splitting large partitions.

#### Tungsten Execution Engine
- **Whole-stage code generation:** Compiles entire pipeline stages into a single Java method, avoiding virtual function call overhead of the Volcano iterator model.
- **Off-heap memory management:** Uses `sun.misc.Unsafe` for direct memory access, avoiding JVM garbage collection overhead.
- **Cache-aware computation:** Custom data structures optimized for CPU cache locality.

#### Structured Streaming
- **Micro-batch processing (default):** Processes incoming data in small batches with exactly-once semantics.
- **Continuous processing (experimental):** Sub-millisecond latency mode with at-least-once semantics.
- **Watermarking:** Handles late data by tracking event-time progress and expiring old state.
- **Stateful operations:** Arbitrary stateful processing via `mapGroupsWithState` and `flatMapGroupsWithState`.

#### Storage & Format Integration
- **Native support for Parquet, ORC, JSON, CSV, Avro, Delta Lake.**
- **Data source V2 API:** Extensible connector framework for reading/writing external data sources.
- **Delta Lake, Iceberg, Hudi:** Open table formats for ACID transactions on data lakes.

#### Spark Connect (3.4+)
- **Thin client architecture:** Decouples Spark client from the server. Clients communicate via gRPC, enabling language-agnostic access and better resource isolation.

### Performance Characteristics
- Designed for distributed, large-scale workloads (TB to PB).
- In-memory computation significantly faster than MapReduce.
- AQE dramatically improves performance on skewed data and dynamic workloads.
- JVM overhead compared to native engines (hence projects like Comet, Photon, Gluten).

---

## 11. ParadeDB

**Category:** Postgres for Search and Analytics
**License:** AGPL v3 (extensions), PostgreSQL License (some components)
**Written in:** Rust (extensions), C (PostgreSQL core)

### Overview

ParadeDB extends PostgreSQL with high-performance full-text search and analytical capabilities through custom extensions, aiming to replace the need for Elasticsearch and dedicated analytical databases alongside Postgres.

### Key Extensions

#### pg_search (Full-Text Search)
- **Built on Tantivy:** A Rust-based full-text search engine library (analogous to Apache Lucene but in Rust).
- **BM25 index type:** Creates a Tantivy-based inverted index within PostgreSQL. Supports BM25 scoring, fuzzy matching, phrase queries, regex, term boosting, and faceted search.
- **Real-time indexing:** Index is updated transactionally with PostgreSQL's MVCC. Inserts/updates/deletes are immediately reflected in search results.
- **Hybrid search:** Combine full-text search with PostgreSQL's native filtering (WHERE clauses, joins) in a single query.
- **Significantly faster than PostgreSQL's built-in `tsvector`/GIN full-text search** for complex search queries.

#### pg_analytics (Columnar Storage & Analytics)
- **Columnar table access method:** Stores PostgreSQL tables in a columnar format (based on Apache Arrow/Parquet internally).
- **DuckDB integration:** Uses DuckDB's execution engine for analytical queries on columnar tables. When a query hits columnar tables, ParadeDB routes execution through DuckDB for vectorized columnar processing.
- **Transparent to SQL:** Standard PostgreSQL SQL works on columnar tables; the optimizer automatically uses the columnar/DuckDB path when beneficial.
- **Compression:** Columnar storage provides significant compression (dictionary, RLE, etc.).

#### pg_lakehouse (Data Lake Integration)
- **Query external data:** Read Parquet, CSV, and Iceberg/Delta Lake tables stored in S3/GCS/Azure Blob directly from PostgreSQL.
- **Federated queries:** Join external lakehouse data with local PostgreSQL tables.

### Architecture
- **PostgreSQL extension model:** All capabilities are implemented as PostgreSQL extensions using the extension APIs (custom index types, custom table access methods, hooks).
- **No fork required:** Works with standard PostgreSQL (though ParadeDB also ships a pre-configured Docker image).

### Differentiators
- **Single system for OLTP + search + analytics:** Eliminates the need for a separate Elasticsearch cluster and ETL pipeline.
- **Transactional consistency:** Search indexes and columnar tables are transactionally consistent with OLTP data.
- **PostgreSQL ecosystem compatibility:** All existing PostgreSQL tools, drivers, and extensions work.

---

## 12. Neon

**Category:** Serverless PostgreSQL
**License:** Apache 2.0
**Written in:** Rust (storage layer), C (PostgreSQL compute)

### Overview

Neon is a serverless PostgreSQL platform that separates storage and compute, enabling features like autoscaling, instant branching, and scale-to-zero. It runs unmodified PostgreSQL as the compute layer.

### Architecture

The architecture separates the traditional PostgreSQL monolith into three components:

#### Compute Nodes
- **Unmodified PostgreSQL:** Standard PostgreSQL instances that handle SQL processing. Modified only at the storage interface level (custom `smgr` storage manager that fetches pages from the pageserver instead of local disk).
- **Stateless:** Compute nodes have no local persistent storage. They can be started, stopped, or scaled independently.
- **Autoscaling:** Compute scales from 0 to N vCPUs based on load. Scale-to-zero means you pay nothing when idle.

#### Pageserver
- **Stores the database pages:** The pageserver holds the actual data, organized as a **log-structured storage** of page versions.
- **Page reconstruction:** When compute requests a page at a specific LSN (Log Sequence Number), the pageserver reconstructs it by replaying WAL records on top of a base page image.
- **Layer files:** Data organized into delta layers (WAL records) and image layers (full page snapshots). Layers are periodically compacted.
- **LSM-like compaction:** Delta layers are merged and compacted into image layers over time, similar to LSM-tree compaction.
- **Multi-tenant:** A single pageserver can serve multiple tenants/databases.

#### Safekeepers
- **WAL durability:** Safekeepers form a small Paxos-based consensus group (typically 3 nodes) that durably stores the WAL stream before it's processed by the pageserver.
- **Decouples compute from storage:** Compute writes WAL to safekeepers (fast, durable), which then asynchronously feed the pageserver.
- **Acts as a write-ahead log service:** Guarantees no data loss even if the pageserver lags.

### Key Techniques

#### Copy-on-Write Branching
- **Instant database branching:** Create a branch (copy) of the entire database in milliseconds. The branch shares storage with the parent via copy-on-write semantics.
- **Use cases:** Development/test environments, point-in-time recovery, preview deployments, schema migration testing.
- **Storage efficient:** Branches only consume space for the pages that differ from the parent.

#### Time Travel
- **Point-in-time queries:** Access the database state at any past LSN (within retention period).
- **Implemented via the page version history** maintained by the pageserver.

#### Bottomless Storage
- **S3/object storage tier:** Cold data is offloaded to S3, with the pageserver acting as a cache for hot data.
- **Effectively unlimited storage:** Database size is not limited by local disk.

### How It Differs from Standard Postgres
- Standard Postgres: monolithic, storage and compute tightly coupled, no branching, manual scaling.
- Neon: disaggregated storage, stateless compute, instant branching, autoscaling, scale-to-zero.

---

## 13. Turso

**Category:** Edge Database
**License:** MIT (libSQL)
**Built on:** libSQL (open-source fork of SQLite)

### Overview

Turso is an edge-native database built on **libSQL**, an open-contribution fork of SQLite. It brings SQLite's simplicity and performance to multi-tenant, globally distributed applications.

### Architecture

#### libSQL: SQLite Fork
- **Open contribution model:** Unlike SQLite (which accepts no external contributions), libSQL accepts community contributions while maintaining compatibility.
- **Key extensions over SQLite:**
  - **Server mode (sqld):** HTTP/WebSocket server for remote access. SQLite is traditionally embedded-only; libSQL adds a server daemon.
  - **Replication protocol:** Built-in WAL-based replication for distributing data.
  - **ALTER TABLE enhancements:** Improved ALTER TABLE support beyond SQLite's limitations.
  - **Native vector search:** Built-in vector similarity search (ANN) using DiskANN-inspired algorithms, without external extensions.
  - **Virtual WAL (vWAL):** Pluggable WAL backends enabling custom storage (e.g., object storage).
  - **WASM-based user-defined functions:** Extend libSQL with WebAssembly functions.

#### Turso Platform Architecture
- **Primary database:** Single writer in a chosen region.
- **Edge replicas:** Read replicas distributed globally at edge locations (leveraging Fly.io's infrastructure or other platforms).
- **Embedded replicas:** A libSQL database embedded directly in the application process that syncs with the remote primary. Provides local-speed reads with eventual consistency.
  - Application reads from the local embedded replica (microsecond latency).
  - Writes go to the primary (network latency) and are replicated back.
  - Sync can be manual or automatic.

### Key Techniques

- **Multi-tenancy with database-per-tenant:** Designed for SaaS applications where each tenant gets their own database (SQLite scales down perfectly to tiny databases).
- **Branching:** Similar to Neon, create database branches for development/testing.
- **Point-in-time recovery:** WAL-based recovery to any point within retention.
- **Groups:** Databases in a group share a physical placement and replicate together.

### Differentiators
- **SQLite compatibility:** Drop-in replacement for SQLite in many use cases. Existing SQLite tools and libraries work.
- **Edge-native:** Sub-millisecond reads from embedded replicas or nearby edge nodes.
- **Lightweight:** Individual databases can be kilobytes to gigabytes; ideal for per-user or per-tenant databases.
- **Cost-efficient at scale:** The SQLite foundation means minimal overhead per database.

### Use Cases
- Multi-tenant SaaS (database per customer).
- Mobile/edge applications with offline-first capabilities.
- Local-first applications with cloud sync.
- Serverless functions needing fast database access.

---

## 14. Apache Iceberg & Vendors

**Category:** Open Table Format for Data Lakehouses
**License:** Apache 2.0

### Overview

Apache Iceberg is an **open table format** for huge analytic datasets. It sits between the query engine and the storage layer (files on S3, HDFS, etc.), providing a metadata layer that enables ACID transactions, schema evolution, time travel, and efficient query planning on data lakes.

### Architecture: Metadata Layers

Iceberg's architecture is a hierarchy of metadata files:

```
Catalog (pointer to current metadata)
  -> Metadata File (JSON)
    -> Snapshot (represents table state at a point in time)
      -> Manifest List (Avro file listing all manifests in a snapshot)
        -> Manifest Files (Avro files listing data files with per-file statistics)
          -> Data Files (Parquet, ORC, or Avro)
```

#### Catalog
- Points to the current metadata file location. Implementations: Hive Metastore, AWS Glue, Nessie (Git-like catalog), REST Catalog, JDBC Catalog.

#### Metadata File
- JSON file containing: table schema, partition spec, sort order, current snapshot ID, snapshot history, properties.

#### Snapshots
- Each write operation (INSERT, DELETE, UPDATE, MERGE) creates a new snapshot. Snapshots are immutable and form a linked list (history).
- **Snapshot isolation:** Readers see a consistent snapshot; writers create new snapshots atomically.

#### Manifest List
- An Avro file listing all manifest files in a snapshot, with summary statistics (partition value ranges, file counts).

#### Manifest Files
- Avro files listing individual data files with:
  - File path, format, record count.
  - Column-level statistics (min, max, null count, distinct count, per-column bounds).
  - Partition tuple values.
  - Used for **partition pruning** and **min/max filtering** at the planning stage.

### Key Techniques

#### Hidden Partitioning
- Partition transforms (year, month, day, hour, bucket, truncate) are applied automatically. Users write queries using the source column; Iceberg maps to the correct partition.
- No need to maintain partition columns in the schema or reference them in queries.

#### Partition Evolution
- Partition layout can be changed (e.g., daily -> hourly) without rewriting existing data. Old data retains its old partitioning; new data uses the new layout. Queries transparently handle both.

#### Schema Evolution
- Add, drop, rename, reorder columns, widen types -- all without rewriting data files. Iceberg uses unique column IDs (not names or positions) for reliable evolution.

#### Time Travel
- Query the table as of any historical snapshot: `SELECT * FROM table FOR SYSTEM_TIME AS OF '2024-01-01'`.
- **Rollback:** Revert the table to a previous snapshot.

#### Row-Level Deletes
- **Copy-on-write:** Rewrite data files excluding deleted rows (better for bulk deletes).
- **Merge-on-read:** Write delete files (positional or equality) that are applied at read time (better for frequent small deletes). Background compaction later merges them.

### Vendor Ecosystem

| Vendor | Integration |
|---|---|
| **Databricks** | Acquired Tabular (Iceberg creators' company). UniForm enables Delta Lake tables to be read as Iceberg. |
| **Snowflake** | Iceberg Tables: Snowflake-managed tables stored in Iceberg format on customer's object storage. External Iceberg catalogs supported. |
| **AWS** | Athena, EMR, Glue natively support Iceberg. Glue Data Catalog serves as an Iceberg catalog. |
| **Dremio** | Deep Iceberg integration; Arctic catalog (Nessie-based) provides Git-like table management. |
| **StarRocks** | Native Iceberg reader for fast analytics on lakehouse data. |
| **Trino/Presto** | First-class Iceberg connector for query federation. |
| **Spark** | The primary development platform; Iceberg's Spark integration is the most mature. |
| **Flink** | Streaming writes to Iceberg tables (append, upsert). |

### Project Nessie (Git-like Catalog)
- Provides Git-like branching and tagging for Iceberg tables.
- Create branches for experimentation, merge table changes, audit history.
- Used by Dremio Arctic and standalone deployments.

---

## 15. Neo4j

**Category:** Graph Database (Property Graph)
**License:** GPL v3 (Community), Commercial (Enterprise)
**Written in:** Java, Scala, Kotlin

### Overview

Neo4j is the leading property graph database, designed for storing and querying highly connected data. It uses the **Cypher** query language and a native graph storage engine optimized for traversals.

### Architecture

- **Native graph storage:** Data is stored as nodes, relationships, and properties using a **pointer-chasing** storage model. Each node directly points to its first relationship; each relationship points to the next relationship for both its start and end nodes (a doubly-linked adjacency list). This provides **index-free adjacency** -- traversals follow physical pointers rather than performing index lookups.
- **Record-based storage:** Fixed-size records for nodes (15 bytes), relationships (34 bytes), properties (stored as linked lists of property records). The fixed size enables direct ID-to-offset calculations.

### Key Techniques

#### Index-Free Adjacency
- The defining characteristic of Neo4j's storage. Each node physically stores a pointer to its relationship chain. Traversing a relationship is a constant-time operation (O(1) per hop) regardless of total graph size.
- This contrasts with **relational graph queries** that require index lookups or joins per hop, making multi-hop traversals exponentially expensive.

#### Cypher Query Language
- **Declarative pattern matching:** `MATCH (a:Person)-[:KNOWS]->(b:Person) WHERE a.name = 'Alice' RETURN b`.
- Supports variable-length path patterns, shortest path algorithms, graph projections, subqueries, and aggregations.
- **GQL (Graph Query Language):** ISO standard based heavily on Cypher; Neo4j is adopting GQL.

#### Transaction Processing
- Full ACID transactions with read-committed isolation.
- Write-ahead logging for durability.
- Cluster deployment uses Raft consensus for leader election and data replication.

#### Indexing
- **B-tree and range indexes** on node/relationship properties.
- **Full-text indexes** (Lucene-based) for text search.
- **Point indexes** for spatial data (2D/3D).
- **Token lookup indexes** for label/type scanning.
- **Vector indexes** for similarity search (Neo4j 5.x+).

#### Graph Data Science (GDS) Library
- In-database graph algorithms: PageRank, community detection (Louvain, Label Propagation), pathfinding (Dijkstra, A*), centrality measures, node similarity, graph embeddings (Node2Vec, GraphSAGE).
- **Graph projections:** Create in-memory analytical graph projections from the database for algorithm execution.
- **Machine learning integration:** Export embeddings and features for ML pipelines; native link prediction and node classification.

#### Clustering & Scalability
- **Causal clustering (Enterprise):** Multi-node clusters with a primary (leader) and secondaries. Raft-based consensus for writes; read replicas for scaling reads.
- **Fabric (multi-database):** Federate queries across multiple Neo4j databases.
- **Sharding:** Neo4j 5 introduced capabilities for distributing graph data across servers, though native sharding of graphs remains challenging.

### Use Cases
- Knowledge graphs, fraud detection, recommendation engines, network/IT operations, identity and access management, supply chain.

---

## 16. Apache Sedona (SedonaDB)

**Category:** Geospatial Analytics Engine
**License:** Apache 2.0
**Written in:** Scala, Java, Python

### Overview

Apache Sedona is a cluster computing system for processing large-scale spatial/geospatial data. It extends Apache Spark, Apache Flink, and Snowflake with spatial capabilities, providing spatial SQL, spatial indexing, and spatial analytics.

### Architecture

- **Runs on existing compute engines:** Sedona is a library/extension for Spark, Flink, and Snowflake -- not a standalone database. It adds spatial types, functions, and indexes to these platforms.
- **Spatial RDD (Spark):** Distributes spatial objects (points, polygons, lines) across Spark partitions with spatial awareness.
- **Spatial SQL:** Registers spatial functions and types in the Spark/Flink SQL catalog, enabling standard SQL queries with spatial operations.

### Key Techniques

#### Spatial Data Types
- **Geometry types:** Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon, GeometryCollection (OGC Simple Features compliant).
- **Raster types:** GeoTiff, ArcGrid, and other raster formats for satellite/aerial imagery processing.

#### Spatial Indexing
- **R-tree index:** Hierarchical spatial index for efficient spatial range queries and spatial joins. Built locally on each partition.
- **Quad-tree index:** Alternative spatial index dividing space into quadrants recursively.
- **KDB-tree index:** A variant optimized for high-dimensional data and balanced partitioning.
- **Spatial partitioning:** Data is partitioned using spatial-aware strategies (Hilbert curve, KDB-tree, Voronoi, R-tree) to co-locate nearby spatial objects on the same partition.

#### Spatial Operations
- **Spatial joins:** Efficient distributed spatial joins using partition-level indexes. Supports: intersects, contains, within, touches, crosses, overlaps, nearest neighbor.
- **Spatial range queries:** Find all geometries within a given bounding box or distance.
- **Spatial aggregations:** Convex hull, union, envelope.
- **Distance functions:** ST_Distance, ST_HausdorffDistance, kNN queries.
- **Spatial predicates:** Full set of DE-9IM (Dimensionally Extended 9-Intersection Model) predicates.

#### Raster Processing
- **Out-of-DB raster:** Handles large raster datasets stored externally.
- **Map algebra:** Raster calculations, band math, zonal statistics.
- **Raster-vector operations:** Extract vector features from rasters, rasterize vectors.

### Integration
- **Spark SQL / DataFrames:** All spatial operations available through SQL or the DataFrame API.
- **Flink:** Streaming spatial analytics on Flink.
- **Snowflake:** Sedona functions available as Snowflake UDFs.
- **Visualization:** Integration with Apache Zeppelin, Jupyter, Kepler.gl, deck.gl.
- **Format support:** Shapefile, GeoJSON, WKT/WKB, GeoParquet, GeoTiff.

### Use Cases
- Large-scale spatial joins (e.g., matching GPS traces to road networks).
- Satellite/aerial imagery processing.
- Urban planning and transportation analytics.
- Environmental monitoring and climate analysis.
- Location intelligence and POI analytics.

---

## 17. Distributed PostgreSQL

**Category:** Approaches to horizontally scaling PostgreSQL

### Overview

PostgreSQL is single-node by default. The distributed PostgreSQL landscape offers multiple approaches to scale horizontally, each with distinct trade-offs. This section surveys the major solutions.

### Approach 1: Extension-Based Sharding

#### Citus (Microsoft)
- **Architecture:** PostgreSQL extension that adds distributed query processing. A coordinator node distributes queries to worker nodes.
- **Sharding:** Hash-based or range-based distribution of tables across workers. Reference tables are replicated to all workers.
- **Distributed SQL:** Supports distributed transactions (2PC), distributed JOINs, distributed subqueries. Co-located joins (joining tables sharded on the same key) are most efficient.
- **Real-time analytics:** Rollup aggregation, columnar storage engine (Citus Columnar).
- **Multi-tenant optimization:** Row-based tenant isolation using a distribution column (e.g., tenant_id).
- **Trade-offs:** Requires choosing a distribution key. Cross-shard queries and non-distributed-key JOINs are slower. Still built on PostgreSQL internals.

### Approach 2: Postgres-Compatible NewSQL

#### YugabyteDB
- **Architecture:** Google Spanner-inspired. Distributed document store (DocDB) based on RocksDB with Raft consensus, with a PostgreSQL-compatible SQL layer on top.
- **Storage:** LSM-tree (RocksDB) per tablet. Data sharded into tablets with automatic splitting.
- **Consensus:** Raft per tablet for replication and leader election.
- **SQL compatibility:** Reuses PostgreSQL's query layer (parser, analyzer, parts of planner/executor) via a forked PostgreSQL process. High compatibility but not 100%.
- **Distributed transactions:** Hybrid clock-based MVCC with distributed deadlock detection.
- **Geo-distribution:** Tablespace-based geo-partitioning, follower reads, read replicas.
- **Trade-offs:** Latency overhead from distributed transactions. LSM-tree compaction overhead. Complex operational model.

#### CockroachDB
- See [Section 4](#4-cockroachdb) for full details.
- Uses PostgreSQL wire protocol but not PostgreSQL code. Custom Go-based SQL layer.

### Approach 3: HA & Read Scaling (Not True Distribution)

#### Patroni
- **Architecture:** HA template using etcd/ZooKeeper/Consul for leader election. Manages streaming replication with automatic failover.
- **Not distributed:** Single primary for writes, standbys for reads. No distributed transactions or sharding.
- **Use case:** When you need HA and read scaling but don't need horizontal write scaling.

#### pgpool-II
- **Connection pooling + load balancing:** Distributes read queries across replicas. Optional query-level write/read splitting.
- **Watchdog:** Provides HA for pgpool itself.
- **Parallel query (limited):** Can split large queries across nodes, but limited and rarely used.

#### PgBouncer / PgCat
- **Connection poolers:** Lightweight proxies that multiplex application connections to PostgreSQL, solving the process-per-connection scalability problem.
- **PgCat** adds sharding-aware routing on top of connection pooling.

### Approach 4: Foreign Data Wrappers (FDW)

- **postgres_fdw:** Query remote PostgreSQL servers as if they were local tables.
- **Manual sharding:** Application or middleware manually routes queries to specific shards. FDW enables cross-shard queries.
- **Trade-offs:** No distributed transactions. Query planning is limited (predicate pushdown only for simple cases). Manual setup and management.

### Approach 5: Middleware/Proxy-Based Sharding

#### Vitess (originally for MySQL, PostgreSQL support emerging)
- Sharding proxy that sits between application and database.
- Handles routing, sharding logic, and schema migrations.

### Comparison Matrix

| Solution | Sharding | Distributed Txns | PG Compatibility | Complexity |
|---|---|---|---|---|
| **Citus** | Hash/Range (extension) | Yes (2PC) | High (native PG) | Medium |
| **YugabyteDB** | Automatic (tablets) | Yes (Raft + HLC) | High (forked PG) | High |
| **CockroachDB** | Automatic (ranges) | Yes (Raft + HLC) | Medium (wire-compat) | High |
| **Patroni** | None (HA only) | N/A (single primary) | 100% (native PG) | Low |
| **FDW** | Manual | No | High | Low-Medium |

### Trade-offs Summary
- **Citus:** Best when you have a natural distribution key (multi-tenant, time-series). Least invasive since it's a PG extension.
- **YugabyteDB/CockroachDB:** Best for geo-distribution and automatic sharding. Higher latency per transaction. Operational complexity.
- **Patroni + Read Replicas:** Simplest. Works when write volume fits on a single node and you only need to scale reads.
- **FDW:** Useful for federated queries across databases but not a true scaling solution.

---

## 18. Qdrant

**Category:** Vector Similarity Search Engine
**License:** Apache 2.0
**Written in:** Rust

### Overview

Qdrant is a vector database and similarity search engine designed for AI/ML applications. It stores high-dimensional vectors with associated payloads and provides fast approximate nearest neighbor (ANN) search with advanced filtering capabilities.

### Architecture

- **Collections:** Top-level organizational unit (analogous to a table). Each collection has a configured vector size, distance metric, and indexing parameters.
- **Points:** Individual records containing a vector (or multiple named vectors), a unique ID, and an optional JSON payload.
- **Segments:** Internal storage units within a collection. Points are distributed across segments. Background optimization merges and rebalances segments.
- **Sharding:** Collections can be sharded across nodes for horizontal scaling.
- **Replication:** Configurable replication factor per collection for fault tolerance.

### Key Techniques

#### HNSW (Hierarchical Navigable Small World) Index
- **Primary vector index:** HNSW builds a multi-layer graph where higher layers contain fewer nodes (long-range connections) and lower layers have more nodes (local connections).
- **Search:** Start at the top layer, greedily navigate to the nearest neighbor, then drop to the next layer and repeat. Provides O(log n) search complexity with high recall.
- **Parameters:** `m` (max connections per node), `ef_construct` (search width during construction), `ef` (search width during query). Trade-off between recall, speed, and memory.
- **On-disk HNSW:** Graph structure can be memory-mapped for datasets larger than RAM.

#### Filtering
- **Payload filtering with ANN search:** Qdrant's distinguishing feature. Apply arbitrary filters on payload fields (equality, range, geo, keyword, nested) simultaneously with vector search.
- **Filterable HNSW:** Instead of post-filtering (which can miss results), Qdrant uses a custom HNSW traversal that respects filters during graph navigation. This avoids the "filter-then-search" or "search-then-filter" problems.
- **Payload indexes:** Keyword, integer, float, geo, datetime, text, boolean, UUID indexes on payload fields for fast filtering.

#### Quantization
- **Scalar quantization:** Compress 32-bit floats to 8-bit integers. 4x memory reduction with small recall loss.
- **Product quantization (PQ):** Divide vectors into sub-vectors, quantize each independently. Higher compression ratios.
- **Binary quantization:** Extreme compression for certain embedding types (e.g., Cohere, OpenAI embeddings). 32x memory reduction.
- **Rescoring:** Quantized search finds candidates; original vectors used for rescoring to maintain recall.

#### Multi-Vector and Sparse Vectors
- **Named vectors:** A single point can have multiple vector representations (e.g., text embedding + image embedding).
- **Sparse vectors:** Support for sparse vector representations (BM25-style term weights, SPLADE embeddings) enabling hybrid search.
- **Multivector:** Store multiple vectors per point (e.g., ColBERT-style late interaction).

### API & Features
- **REST and gRPC APIs.** Client libraries for Python, JavaScript, Rust, Go, Java, C#.
- **Batch operations:** Bulk upload, batch search (search multiple queries in one request).
- **Recommendation API:** Find points similar to positive examples and dissimilar to negative examples.
- **Discovery API:** Explore the vector space with constraints.
- **Snapshot & backup:** Point-in-time snapshots for backup/restore.

### Deployment
- **Single node:** Embedded or standalone.
- **Distributed mode:** Automatic sharding and replication with Raft-based consensus for cluster coordination.
- **Qdrant Cloud:** Managed offering.

### Use Cases
- Semantic search, RAG (Retrieval-Augmented Generation) for LLMs, recommendation systems, image similarity search, anomaly detection, deduplication.

---

## 19. TigerBeetle

**Category:** Financial Transactions Database
**License:** Apache 2.0
**Written in:** Zig

### Overview

TigerBeetle is a purpose-built database for financial accounting and transactions. It is designed for extreme correctness, safety, and performance in processing double-entry bookkeeping operations. Rather than being a general-purpose database, it is a domain-specific system for financial ledgers.

### Architecture

- **Fixed schema:** TigerBeetle has a hardcoded schema: **accounts** and **transfers** (debits/credits). No user-defined tables or schemas. This radical constraint enables extreme optimization.
- **Cluster of replicas:** Typically 3 or 6 replicas forming a consensus group.
- **Deterministic state machine:** The entire database is a deterministic state machine replicated via consensus. Given the same inputs, all replicas produce identical outputs.

### Key Techniques

#### Consensus: Viewstamped Replication (VR)
- Uses **Viewstamped Replication** (VR) protocol, a predecessor to Raft with similar guarantees but different implementation details.
- **Strict serializability:** All operations are strictly ordered across the cluster.
- **No leader ambiguity:** Clear view-change protocol ensures exactly one primary at a time.

#### Storage: LSM-Inspired with Direct I/O
- **Fixed-size records:** Accounts (128 bytes) and transfers (128 bytes) stored in fixed-size slots. No variable-length fields.
- **LSM-tree inspired:** Write-ahead log + compacted storage. But highly optimized for the fixed schema.
- **Direct I/O:** Bypasses the operating system's page cache entirely. TigerBeetle manages its own memory and I/O, eliminating double-buffering and ensuring predictable performance.
- **No filesystem dependency:** Uses raw block devices or files opened with O_DIRECT. No reliance on filesystem consistency semantics.

#### io_uring
- **Linux io_uring for asynchronous I/O:** All disk I/O uses io_uring for batched, asynchronous submission and completion. This provides very high I/O throughput with minimal syscall overhead.
- **On macOS:** Falls back to kqueue-based async I/O.

#### Deterministic Simulation Testing (DST)
- **The VOPR (Viewstamped Operation Replication Protocol simulator):** A comprehensive simulation testing framework that deterministically replays all possible failure scenarios.
- **Simulates:** Disk failures, network partitions, message reordering, message duplication, message loss, clock skew, process crashes at any point.
- **Deterministic:** Given a seed, the entire simulation is reproducible. This makes bugs reproducible and enables systematic exploration of the failure space.
- **Runs millions of test cases** covering rare edge cases that would be nearly impossible to hit in traditional testing.

#### Why Zig?
- **No hidden control flow:** Zig has no hidden allocations, no hidden function calls, no exceptions. Every operation is explicit and visible in the code.
- **Comptime (compile-time execution):** Zig's comptime enables powerful metaprogramming without runtime overhead. Used for generating optimized data structures and state machine code.
- **No garbage collector:** Predictable latency with no GC pauses.
- **C interop:** Seamless integration with OS APIs (io_uring, direct I/O).
- **Safety without overhead:** Runtime safety checks (bounds checking, overflow detection) in debug mode; can be disabled in release for maximum performance.

### Safety & Correctness Guarantees
- **Strict serializability** across the cluster.
- **Double-entry accounting invariants** enforced at the database level: every transfer debits one account and credits another. Balances cannot go negative (configurable). No partial transfers.
- **Idempotency:** All operations are idempotent (safe to retry).
- **No data loss:** Designed to never lose acknowledged writes, even under byzantine storage conditions (bit rot detection via checksums).

### Performance Characteristics
- **1 million+ transfers per second** on a single cluster.
- **Sub-millisecond latency** for individual transfers.
- **Batched operations:** Client submits batches of up to 8190 transfers per request. The database processes the entire batch atomically.
- **Fixed memory footprint:** Memory usage is deterministic and bounded at startup.

### Data Model

```
Account:
  - id (128-bit)
  - debits_pending, debits_posted
  - credits_pending, credits_posted
  - user_data (128-bit, 64-bit, 32-bit fields for application use)
  - ledger (uint32), code (uint16)
  - flags (linked transfers, balance checks)

Transfer:
  - id (128-bit)
  - debit_account_id, credit_account_id
  - amount (uint128)
  - pending_id (for two-phase transfers)
  - user_data fields
  - ledger, code
  - flags (linked, pending, void, post, balancing)
```

### Two-Phase Transfers
- **Pending transfers:** Reserve (hold) funds without completing the transfer.
- **Post/void:** Later post (complete) or void (cancel) the pending transfer.
- **Timeout:** Pending transfers can have a timeout; automatically voided if not posted.
- **Use case:** Payment authorization/capture, escrow, reservations.

### Use Cases
- Financial ledgers, payment processing, digital wallets, marketplace payments, trading systems, prepaid/postpaid billing, loyalty points, in-game currencies.

---

## Summary Comparison Matrix

| System | Category | Storage Model | Distribution | Language | Key Differentiator |
|---|---|---|---|---|---|
| **Umbra** | Research OLAP | Columnar/PAX + Buffer Mgr | Single-node | C++ | Adaptive compilation + memory-speed disk-based |
| **PostgreSQL** | General ORDBMS | Heap (row) + MVCC | Single-node + replicas | C | Extensibility + correctness |
| **MySQL** | General RDBMS | Clustered B-tree (InnoDB) | Replication + Group Rep | C/C++ | Read-heavy OLTP + mature replication |
| **CockroachDB** | Distributed SQL | LSM-tree (Pebble) + Raft | Range-sharded | Go | Geo-distributed serializable transactions |
| **DuckDB** | Embedded OLAP | Columnar | Single-node (embedded) | C++ | SQLite for analytics |
| **Feldera** | Incremental Compute | In-memory (Z-sets) | Single-node (scalable) | Rust | DBSP theory: automatic incrementalization |
| **ClickHouse** | Column OLAP | MergeTree family | Sharded + replicated | C++ | Extreme aggregation speed |
| **RisingWave** | Streaming DB | LSM on object storage | Distributed | Rust | Streaming MVs with PG compatibility |
| **Apache Comet** | Spark Accelerator | Arrow columnar | Distributed (Spark) | Rust/Scala | Native Spark acceleration via DataFusion |
| **Apache Spark** | Unified Analytics | Various (pluggable) | Distributed | Scala/Java | Unified batch + stream + ML |
| **ParadeDB** | PG Search+Analytics | Tantivy + Columnar | Single-node (PG) | Rust | Search + analytics in Postgres |
| **Neon** | Serverless Postgres | Log-structured pages | Separated compute/storage | Rust/C | Instant branching + autoscaling |
| **Turso** | Edge Database | SQLite (libSQL) | Edge-replicated | C/Rust | Embedded replicas at the edge |
| **Apache Iceberg** | Table Format | Parquet/ORC files | N/A (format) | Java | Schema evolution + hidden partitioning |
| **Neo4j** | Graph Database | Native graph (pointers) | Causal clustering | Java | Index-free adjacency |
| **Apache Sedona** | Geospatial Engine | Depends on engine | Distributed (Spark/Flink) | Scala/Java | Spatial indexing + spatial SQL |
| **Distributed PG** | Scaling Postgres | Various | Various | Various | Multiple approaches to scale PG |
| **Qdrant** | Vector Database | HNSW + segments | Sharded + replicated | Rust | Filtered ANN search |
| **TigerBeetle** | Financial Txn DB | LSM + Direct I/O | VR consensus | Zig | Deterministic simulation + financial safety |

---

## See Also

- [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) — Deep dive into the Umbra lineage surveyed briefly here
- [Disaggregated Storage](@/notes/database/disaggregated_storage.md) — Expands on Aurora, Neon, Snowflake, and other cloud-native architectures listed above
- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — Consensus protocols (Raft, Paxos, VR) underpinning CockroachDB, TiDB, and TigerBeetle
- [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) — Testing approach used by FoundationDB and TigerBeetle surveyed here
- [LSM Trees](@/notes/database/lsm_trees.md) — Storage engine architecture used by CockroachDB, RocksDB, ClickHouse, and TigerBeetle
