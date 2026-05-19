+++
title = "LSM Trees"
date = 2026-02-10
[taxonomies]
tags = ["lsm-tree", "compaction", "rocksdb", "write-amplification", "bloom-filter", "leveldb", "ssd"]
+++


# Log-Structured Merge (LSM) Trees: Comprehensive Research Guide

**Date**: 2026-01-27
**Context**: Research notes for building a world-class data layer with LSM-based storage
**Relevance to pg_arrow**: Understanding write-optimized structures for potential WAL-based conversion pipelines and hybrid storage engines

---

## Table of Contents

1. [Foundational Concepts](#1-foundational-concepts)
2. [State-of-the-Art Research (2015-2025)](#2-state-of-the-art-research-2015-2025)
3. [Production Systems & Techniques](#3-production-systems-techniques)
4. [Advanced Optimization Techniques](#4-advanced-optimization-techniques)
5. [Cutting-Edge Research Areas](#5-cutting-edge-research-areas)
6. [Key Papers Bibliography](#6-key-papers-bibliography)
7. [Decision Framework](#7-decision-framework)

---

## 1. Foundational Concepts

### 1.1 Historical Context

The LSM-tree was introduced by Patrick O'Neil, Edward Cheng, Dieter Gawlick, and Elizabeth O'Neil in their seminal 1996 paper "The Log-Structured Merge-Tree (LSM-Tree)" published in Acta Informatica. The motivation was clear: traditional B-tree indexes suffer from random I/O on writes, which was devastating for the mechanical hard drives of the era (and remains suboptimal even for SSDs).

**Key Insight**: By converting random writes into sequential writes through buffering and batch operations, LSM-trees can achieve orders of magnitude better write throughput than in-place update structures.

### 1.2 Core Architecture

```
                    ┌─────────────────────────────────────────────┐
                    │              WRITE PATH                      │
                    └─────────────────────────────────────────────┘
                                        │
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │            WRITE-AHEAD LOG (WAL)            │
                    │         (Durability guarantee)               │
                    └─────────────────────────────────────────────┘
                                        │
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │              MEMTABLE (C0)                   │
                    │    In-memory sorted structure (skiplist,     │
                    │    red-black tree, or B-tree)               │
                    │    Size: Typically 64MB-256MB               │
                    └─────────────────────────────────────────────┘
                                        │
                         (When full, becomes immutable)
                                        │
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │           IMMUTABLE MEMTABLE                 │
                    │    (Being flushed to disk)                   │
                    └─────────────────────────────────────────────┘
                                        │
                              (Flush to Level 0)
                                        │
                    ┌───────────────────▼─────────────────────────┐
                    │                                              │
    ┌───────────────┴─────────────────────────────────────────────┴──────────────┐
    │                           PERSISTENT STORAGE                                │
    │  ┌────────────────────────────────────────────────────────────────────────┐│
    │  │  LEVEL 0 (L0): Recently flushed SSTables                              ││
    │  │  - May have overlapping key ranges                                     ││
    │  │  - Typically 4-8 files before compaction triggered                     ││
    │  │  [SST1: a-z] [SST2: b-y] [SST3: c-x] [SST4: a-m]                       ││
    │  └────────────────────────────────────────────────────────────────────────┘│
    │                              │ Compaction                                   │
    │                              ▼                                              │
    │  ┌────────────────────────────────────────────────────────────────────────┐│
    │  │  LEVEL 1 (L1): 10x size of L0                                         ││
    │  │  - Non-overlapping key ranges (sorted runs)                            ││
    │  │  [SST: a-d] [SST: e-h] [SST: i-l] [SST: m-p] [SST: q-t] [SST: u-z]     ││
    │  └────────────────────────────────────────────────────────────────────────┘│
    │                              │ Compaction                                   │
    │                              ▼                                              │
    │  ┌────────────────────────────────────────────────────────────────────────┐│
    │  │  LEVEL 2 (L2): 10x size of L1                                         ││
    │  │  - Non-overlapping, larger files                                       ││
    │  │  [SST: a-b] [SST: c-d] ... (many more files)                           ││
    │  └────────────────────────────────────────────────────────────────────────┘│
    │                              │                                              │
    │                              ▼                                              │
    │  ┌────────────────────────────────────────────────────────────────────────┐│
    │  │  LEVEL N: Largest level, contains majority of data                    ││
    │  └────────────────────────────────────────────────────────────────────────┘│
    └─────────────────────────────────────────────────────────────────────────────┘
```

### 1.3 LSM vs B-Tree: Fundamental Differences

| Aspect | B-Tree | LSM-Tree |
|--------|--------|----------|
| **Write Pattern** | In-place update (random I/O) | Append-only (sequential I/O) |
| **Read Pattern** | Single lookup path | Multiple levels to check |
| **Write Amplification** | 1x (but high I/O cost) | 10-30x (but sequential) |
| **Read Amplification** | 1x (direct path) | O(levels) without optimization |
| **Space Amplification** | ~1x (minimal) | 1.1-2x (depends on compaction) |
| **Concurrency** | Lock/latch intensive | Lock-free writes possible |
| **Recovery** | Page-level WAL | Simple replay |
| **SSD Wear** | High (random writes) | Lower (sequential) |
| **Range Scans** | Excellent (single structure) | Good (merge during scan) |

### 1.4 The Write Path in Detail

```rust
// Conceptual write path implementation
fn put(key: &[u8], value: &[u8]) -> Result<()> {
    // Step 1: Write to WAL for durability
    wal.append(WriteRecord { key, value, seq_num })?;

    // Step 2: Insert into active memtable
    // This is typically a concurrent skiplist for lock-free access
    memtable.insert(key, value, seq_num);

    // Step 3: Check if memtable is full
    if memtable.size() >= memtable_size_limit {
        // Make current memtable immutable
        let immutable = memtable.freeze();
        immutable_memtables.push(immutable);

        // Create new active memtable
        memtable = MemTable::new();

        // Trigger background flush
        background_scheduler.schedule_flush();
    }

    Ok(())
}
```

### 1.5 The Read Path in Detail

```rust
// Conceptual read path
fn get(key: &[u8]) -> Result<Option<Value>> {
    // Step 1: Check active memtable (most recent)
    if let Some(value) = memtable.get(key) {
        return Ok(handle_tombstone(value));
    }

    // Step 2: Check immutable memtables (newest first)
    for imm in immutable_memtables.iter().rev() {
        if let Some(value) = imm.get(key) {
            return Ok(handle_tombstone(value));
        }
    }

    // Step 3: Check on-disk levels (L0 first, then L1, L2, ...)
    // L0 may have overlapping files, must check all
    for sst in level0.iter().rev() {  // newest first
        if sst.may_contain(key) {  // Bloom filter check
            if let Some(value) = sst.get(key) {
                return Ok(handle_tombstone(value));
            }
        }
    }

    // L1+ have non-overlapping files, binary search to find correct file
    for level in 1..=max_level {
        let sst = levels[level].find_file_for_key(key);  // Binary search
        if sst.may_contain(key) {  // Bloom filter
            if let Some(value) = sst.get(key) {
                return Ok(handle_tombstone(value));
            }
        }
    }

    Ok(None)
}
```

### 1.6 Why LSM Trees Excel for Write-Heavy Workloads

1. **Sequential I/O Dominance**: Writes are buffered and written sequentially, which is 100-1000x faster than random I/O on HDDs and 10-100x faster on SSDs.

2. **Write Batching**: Multiple writes are accumulated and written together, amortizing I/O overhead.

3. **Reduced Write Amplification at Device Level**: While LSM has logical write amplification, the physical I/O is sequential, reducing SSD wear and improving throughput.

4. **Lock-Free Writes**: The memtable can use lock-free data structures (like concurrent skiplists), enabling high write concurrency.

5. **Simplified Durability**: WAL is append-only, making fsync efficient and recovery straightforward.

---

## 2. State-of-the-Art Research (2015-2025)

### 2.1 Compaction Strategies: The Core Tradeoff

Compaction is the heart of LSM-tree performance. The choice of strategy determines the tradeoff between write amplification, read amplification, and space amplification.

#### 2.1.1 Leveled Compaction (LevelDB/RocksDB Default)

**Mechanism**:
- Each level (except L0) maintains sorted, non-overlapping runs
- L(i+1) is ~10x larger than L(i) (configurable via `level_ratio`)
- When L(i) exceeds its size limit, pick a file and merge with overlapping files in L(i+1)

**Properties**:
```
Write Amplification: O(L * T) where L = levels, T = size ratio (typically 10)
                     Approximately 10-30x in practice
Read Amplification:  O(L) = O(log_T(N)) - logarithmic in data size
Space Amplification: ~1.1x (minimal wasted space)
```

**Pros**:
- Excellent read performance (bounded number of files to check)
- Minimal space overhead
- Good for read-heavy workloads with some writes

**Cons**:
- High write amplification
- Compaction can cause latency spikes
- Background I/O can interfere with foreground operations

**Used by**: LevelDB, RocksDB (default), Pebble

#### 2.1.2 Tiered (Size-Tiered) Compaction

**Mechanism**:
- Each level contains multiple sorted runs
- When a level has T runs, merge all T runs into one run in next level
- No size limit per level, trigger based on run count

**Properties**:
```
Write Amplification: O(L) - much lower than leveled
                     Approximately 4-8x
Read Amplification:  O(L * T) - must check more files
Space Amplification: Up to 2x (temporary during compaction)
```

**Pros**:
- Lower write amplification
- Better for write-heavy workloads
- Simpler compaction scheduling

**Cons**:
- Higher read amplification
- Higher space amplification during compaction
- Range scans are more expensive

**Used by**: Cassandra (default), ScyllaDB (original), HBase

#### 2.1.3 FIFO Compaction

**Mechanism**:
- No merging at all; simply delete oldest files when size limit reached
- Only for time-series data with TTL

**Properties**:
```
Write Amplification: 1x (no rewriting)
Read Amplification:  Very high (all files may need scanning)
Space Amplification: None beyond data itself
```

**Used by**: RocksDB (optional for time-series), InfluxDB concepts

#### 2.1.4 Universal Compaction (RocksDB)

**Mechanism**:
- Hybrid approach between leveled and tiered
- Sorted runs are placed on the same "level"
- Compaction can merge any subset of consecutive runs
- Configurable space amplification limit

**Properties**:
```
Write Amplification: Tunable between leveled and tiered
Read Amplification:  Tunable
Space Amplification: Configurable limit (e.g., max 2x)
```

**Pros**:
- Flexible tradeoff tuning
- Can optimize for specific workloads
- Good for mixed workloads

**Cons**:
- Complex tuning required
- Less predictable behavior

**Key Paper**: "Universal Compaction" - RocksDB Wiki and VLDB 2021 Dostoevsky paper

#### 2.1.5 Lazy Leveling (Dostoevsky)

**Mechanism**:
- Leveled compaction for the largest level only
- Tiered compaction for all other levels
- Hybrid approach targeting the optimal point

**Properties**:
```
Write Amplification: O(L) - similar to tiered
Read Amplification:  O(1) for point lookups (with Bloom filters)
Space Amplification: Similar to leveled
```

**Key Paper**: "Dostoevsky: Better Space-Time Trade-Offs for LSM-Tree Based Key-Value Stores via Adaptive Removal of Superfluous Merging" - Dayan & Idreos, SIGMOD 2018

### 2.2 The RUM Conjecture and Amplification Tradeoffs

The **RUM Conjecture** (Read, Update, Memory) states that you can optimize at most two of the three:
- **R**ead overhead
- **U**pdate (write) overhead
- **M**emory (space) overhead

**Visualization of the Design Space**:

```
                        Read Optimal
                             ▲
                             │
                             │  B-Tree
                             │    ●
                             │
                             │         Leveled LSM
                             │              ●
                             │
                             │                    Lazy Leveling
                             │                         ●
                             │
    Write Optimal ◄──────────┼───────────────────────────► Space Optimal
                             │
                             │                    Tiered LSM
                             │                         ●
                             │
                             │
                             │  Append-Only Log
                             │    ●
                             │
                             ▼
```

**Quantifying Amplification** (from Dostoevsky paper):

For leveled compaction with size ratio T and L levels:
- Write Amplification: `W = T * L`
- Point Read Cost: `R = L` (with Bloom filters, effectively O(1))
- Range Read Cost: `R_range = L`
- Space Amplification: `S = 1 + 1/T`

For tiered compaction:
- Write Amplification: `W = L`
- Point Read Cost: `R = L * T` (many more files to check)
- Space Amplification: `S = T` (worst case during compaction)

### 2.3 Learned Indexes and ML-Enhanced LSM

#### 2.3.1 Learned Bloom Filters

**Traditional Bloom Filter**:
- Fixed false positive rate
- Memory proportional to dataset size
- No adaptation to data distribution

**Learned Bloom Filter** (Kraska et al., NeurIPS 2018):
- Train a model to predict membership
- Use traditional Bloom filter as backup for false negatives
- Can achieve same FPR with less memory for skewed distributions

```
Traditional: FPR = (1 - e^(-kn/m))^k
Learned: FPR = P(model says yes | not in set) * P(bloom says yes | model said yes, not in set)
```

**Key Paper**: "The Case for Learned Index Structures" - Kraska et al., SIGMOD 2018

#### 2.3.2 Learned Compaction Scheduling

**Research Direction**: Use ML to predict:
- Which files to compact next (minimize total I/O)
- When to trigger compaction (avoid latency spikes)
- Optimal compaction granularity

**Key Papers**:
- "Learning Multi-dimensional Indexes" - Nathan et al., SIGMOD 2020
- "Bourbon: Learned Index Structures in Storage Engines" - Dai et al., SOSP 2020

#### 2.3.3 Learned Index Structures Within SSTables

Instead of binary search within SSTable blocks, use:
- Linear regression for uniformly distributed keys
- Piecewise linear functions for sorted keys
- Recursive Model Index (RMI) for complex distributions

**Potential Gains**: 1.5-3x faster point lookups within SSTables

### 2.4 Persistent Memory (PMem) Optimizations

#### 2.4.1 Challenges with PMem

- Byte-addressable but slower than DRAM
- Persistence requires explicit flushes (clwb, sfence)
- Write ordering constraints
- Limited write endurance compared to DRAM

#### 2.4.2 LSM Optimizations for PMem

**Approach 1: PMem as Extended Buffer Pool**
- Keep multiple memtables in PMem
- Delay flush to SSD
- Reduces write amplification

**Approach 2: Hybrid Indexing**
- Keep upper levels (L0, L1) in PMem
- Keep lower levels on SSD
- Reduces read amplification for hot data

**Approach 3: PMem-Aware WAL**
- WAL directly in PMem
- No fsync needed, just memory fence
- Dramatically faster commits

**Key Papers**:
- "SLM-DB: Single-Level Key-Value Store with Persistent Memory" - Kaiyrakhmet et al., FAST 2019
- "MatrixKV: Reducing Write Stalls and Write Amplification in LSM-tree Based KV Stores" - Yao et al., ATC 2020

### 2.5 NVMe and Modern Storage Optimizations

#### 2.5.1 Multi-Queue I/O

Modern NVMe SSDs support:
- 64K queues with 64K commands each
- Parallel I/O submission
- Interrupt coalescing

**LSM Optimizations**:
- Parallel SSTable reads during compaction
- Concurrent flush operations
- I/O prioritization between compaction and user reads

#### 2.5.2 Zone Storage (ZNS)

Zoned Namespace SSDs divide storage into sequential-write zones:
- Perfect fit for LSM-tree workloads
- Eliminates SSD garbage collection overhead
- Better write amplification at device level

**Key Papers**:
- "ZNS: Avoiding the Block Interface Tax for Flash-based SSDs" - Bjorling et al., ATC 2021
- "ZenFS: Append-only File System for LSM-based Key-Value Stores on ZNS SSDs" - Jung et al.

### 2.6 Disaggregated Storage Architectures

#### 2.6.1 Compute-Storage Separation

Modern cloud architectures separate:
- **Compute tier**: Stateless, handles memtables and caching
- **Storage tier**: Persistent, handles SSTables
- **Network**: RDMA or NVMe-oF for low latency

**Advantages**:
- Independent scaling of compute and storage
- Better resource utilization
- Simplified failover

**Challenges**:
- Network latency on the read path
- Cache coherence across compute nodes
- Compaction placement (compute or storage?)

#### 2.6.2 Disaggregated LSM Architectures

**Pattern 1: Shared Storage**
- All compute nodes read/write to shared storage
- Memtables local to compute
- SSTables in shared storage (S3, EBS, etc.)

**Pattern 2: Tiered Placement**
- Hot data (memtable, L0, L1) on local NVMe
- Cold data (L2+) on remote storage
- Background migration between tiers

**Key Papers**:
- "REMIX: Efficient Range Query for LSM-trees" - Zhong et al., FAST 2021
- "Disaggregating RocksDB" - various industry talks and implementations

---

## 3. Production Systems & Techniques

### 3.1 RocksDB (Facebook/Meta)

**Overview**:
- Fork of LevelDB with significant enhancements
- Used by: MySQL (MyRocks), TiKV, CockroachDB (via Pebble), Kafka Streams
- Written in C++, highly configurable

**Key Features**:

```cpp
// RocksDB configuration example
Options options;
options.create_if_missing = true;

// Compaction
options.compaction_style = kCompactionStyleLevel;  // or kCompactionStyleUniversal
options.level_compaction_dynamic_level_bytes = true;
options.max_bytes_for_level_base = 256 * 1024 * 1024;  // 256MB
options.max_bytes_for_level_multiplier = 10;

// Memtable
options.write_buffer_size = 64 * 1024 * 1024;  // 64MB
options.max_write_buffer_number = 4;
options.min_write_buffer_number_to_merge = 2;

// Block cache
BlockBasedTableOptions table_options;
table_options.block_cache = NewLRUCache(512 * 1024 * 1024);  // 512MB
table_options.filter_policy.reset(NewBloomFilterPolicy(10));  // 10 bits per key
options.table_factory.reset(NewBlockBasedTableFactory(table_options));

// Rate limiting
options.rate_limiter.reset(NewGenericRateLimiter(100 * 1024 * 1024));  // 100MB/s for compaction
```

**Advanced Features**:
1. **Column Families**: Logical separation within same DB, independent memtables and compaction
2. **Merge Operator**: User-defined merge logic for read-modify-write patterns
3. **Prefix Bloom Filters**: Optimize for prefix-based queries
4. **Compaction Filters**: Delete/modify entries during compaction
5. **Direct I/O**: Bypass page cache for predictable performance
6. **Rate Limiter**: Control background I/O impact

**SSTable Format**:
```
┌──────────────────────────────────────────────────────────────┐
│                        DATA BLOCKS                           │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Block 1: [KV, KV, KV, ...] (compressed)                │ │
│  │ Block 2: [KV, KV, KV, ...] (compressed)                │ │
│  │ ...                                                     │ │
│  └─────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│                      META BLOCKS                             │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ Filter Block (Bloom filter for all keys)               │ │
│  │ Properties Block (file metadata)                        │ │
│  │ Compression Dict Block (shared dictionary)              │ │
│  │ Range Deletion Block (tombstone ranges)                │ │
│  └─────────────────────────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────┤
│                      INDEX BLOCK                             │
│  [last_key_block_1, offset_1]                               │
│  [last_key_block_2, offset_2]                               │
│  ...                                                         │
├──────────────────────────────────────────────────────────────┤
│                      META INDEX                              │
│  [filter_block_handle, properties_block_handle, ...]        │
├──────────────────────────────────────────────────────────────┤
│                        FOOTER                                │
│  [meta_index_handle, index_handle, magic_number]            │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 LevelDB (Google)

**Overview**:
- Original modern LSM implementation (2011)
- Simpler than RocksDB, fewer features
- Good for understanding core concepts

**Key Differences from RocksDB**:
- Single threaded compaction
- No column families
- Simpler configuration
- No merge operators

### 3.3 WiredTiger (MongoDB)

**Overview**:
- MongoDB's default storage engine since 3.2
- Hybrid approach: not pure LSM, uses B-tree with LSM-like write optimization
- Written in C

**Key Architecture**:
```
┌─────────────────────────────────────────────────────────────┐
│                     WiredTiger Architecture                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────┐    ┌────────────────────────────────┐ │
│  │   In-Memory      │    │      On-Disk                   │ │
│  │   ┌──────────┐   │    │   ┌────────────────────────┐   │ │
│  │   │ Skip List│   │    │   │ Row Store (B+ Tree)    │   │ │
│  │   │(Updates) │───┼────┼──>│ - Page-based           │   │ │
│  │   └──────────┘   │    │   │ - In-place updates     │   │ │
│  │                  │    │   │ - BUT reconciliation   │   │ │
│  │   ┌──────────┐   │    │   │   batches writes       │   │ │
│  │   │Block Mgr │   │    │   └────────────────────────┘   │ │
│  │   │ (Cache)  │   │    │                                │ │
│  │   └──────────┘   │    │   ┌────────────────────────┐   │ │
│  │                  │    │   │ Column Store (LSM-like)│   │ │
│  │                  │    │   │ - Append-only          │   │ │
│  │                  │    │   │ - Background merge     │   │ │
│  │                  │    │   └────────────────────────┘   │ │
│  └──────────────────┘    └────────────────────────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Key Features**:
1. **Document-level Concurrency**: MVCC with hazard pointers
2. **Compression**: Snappy, zlib, zstd at page level
3. **Checkpoint-based Durability**: Periodic consistent snapshots
4. **Journal**: WAL for durability between checkpoints

### 3.4 Pebble (CockroachDB)

**Overview**:
- RocksDB-compatible LSM in Go
- Built specifically for CockroachDB's needs
- Focus on correctness and Go ecosystem integration

**Key Differences from RocksDB**:
```go
// Pebble configuration
opts := &pebble.Options{
    // Memory
    MemTableSize:                64 << 20,  // 64MB
    MemTableStopWritesThreshold: 4,

    // Compaction
    L0CompactionThreshold:     2,
    L0StopWritesThreshold:     1000,
    LBaseMaxBytes:             64 << 20,  // 64MB
    MaxConcurrentCompactions:  3,

    // Bloom filters
    Levels: []pebble.LevelOptions{
        {FilterPolicy: bloom.FilterPolicy(10)},
    },
}
```

**Notable Features**:
1. **Range Keys**: Native support for range-based metadata (used for MVCC garbage)
2. **Block Property Filters**: Skip blocks during iteration based on properties
3. **Pacing**: Adaptive compaction pacing to prevent write stalls
4. **Strict MVCC**: Better support for CockroachDB's MVCC requirements

### 3.5 ScyllaDB

**Overview**:
- C++ rewrite of Cassandra
- Shard-per-core architecture
- Custom compaction strategies

**Key Innovations**:

1. **Incremental Compaction Strategy (ICS)**:
```
Traditional Compaction:
[SST1] + [SST2] + [SST3] + [SST4] → [SST_merged]
(All files must be read and written atomically)

Incremental Compaction:
[SST1] + [SST2] → [fragment1]
[fragment1] + [SST3] → [fragment2]
[fragment2] + [SST4] → [SST_merged]
(Can pause and resume, lower space amplification)
```

2. **Shard-per-Core**: Each CPU core owns a shard, no cross-core synchronization
3. **Seastar Framework**: Async I/O throughout

### 3.6 Cloud-Native Implementations

#### 3.6.1 Amazon DynamoDB

**Architecture**:
- Request routers → Storage nodes
- Storage nodes use B-trees (not LSM) for partitions
- But uses log-structured storage for durability

**Key Insight**: DynamoDB uses B-trees because partition sizes are bounded and read latency is critical.

#### 3.6.2 Google Bigtable

**Architecture**:
- True LSM-tree based
- Tablets (partitions) stored as SSTables in GFS/Colossus
- Background compaction handled by tablet servers

**Key Features**:
- Bloom filters per SSTable
- Three compaction styles (minor, merging, major)
- Column family-based locality groups

**Key Paper**: "Bigtable: A Distributed Storage System for Structured Data" - Chang et al., OSDI 2006

#### 3.6.3 Apache Cassandra

**Architecture**:
- Pure LSM per partition
- Configurable compaction strategy per table
- Distributed across nodes using consistent hashing

**Compaction Strategies**:
```yaml
# Size-Tiered (default, write-optimized)
compaction:
  class: SizeTieredCompactionStrategy
  min_threshold: 4
  max_threshold: 32

# Leveled (read-optimized)
compaction:
  class: LeveledCompactionStrategy
  sstable_size_in_mb: 160

# Time-Window (time-series)
compaction:
  class: TimeWindowCompactionStrategy
  compaction_window_unit: DAYS
  compaction_window_size: 1
```

---

## 4. Advanced Optimization Techniques

### 4.1 Bloom Filters: Theory and Practice

#### 4.1.1 Standard Bloom Filter Math

```
Parameters:
  n = number of elements
  m = number of bits
  k = number of hash functions

Optimal k: k = (m/n) * ln(2) ≈ 0.693 * (m/n)

False Positive Rate: FPR ≈ (1 - e^(-kn/m))^k

Bits per key for target FPR:
  m/n = -1.44 * log2(FPR)

  FPR = 1%  → 9.6 bits/key
  FPR = 0.1% → 14.4 bits/key
```

#### 4.1.2 Blocked Bloom Filters

Problem: Standard Bloom filters have poor cache behavior (random memory access).

Solution: Divide into cache-line-sized blocks:
```
┌────────────────────────────────────────────────────────────┐
│                    Blocked Bloom Filter                     │
├────────────────────────────────────────────────────────────┤
│ Block 0 (64 bytes)  │ Block 1 (64 bytes)  │ Block 2 ...    │
│ [bits for keys      │ [bits for keys      │                │
│  hashing to block 0]│  hashing to block 1]│                │
└────────────────────────────────────────────────────────────┘

Lookup:
1. Hash key to determine block
2. All k probes within that single cache line
```

**Tradeoff**: Slightly higher FPR but much better cache performance.

#### 4.1.3 Ribbon Filters (RocksDB)

**Ribbon Filter** (Rocksdb Improved Bloom-like Binary fused filter):
- 30% less space than Bloom for same FPR
- Slightly slower construction
- Same query speed

**Key Paper**: "Ribbon filter: practically smaller than Bloom and Xor" - Dillinger & Walzer, 2021

### 4.2 Fence Pointers and Index Structures

**Fence Pointers**: Store the last (or first) key of each block
```
┌─────────────────────────────────────────────────────────────┐
│                     Index Block                              │
├─────────────────────────────────────────────────────────────┤
│ fence_key_1: "apple"   → block_offset_1                     │
│ fence_key_2: "banana"  → block_offset_2                     │
│ fence_key_3: "cherry"  → block_offset_3                     │
│ ...                                                          │
└─────────────────────────────────────────────────────────────┘

To find "ball":
1. Binary search fence keys: "apple" < "ball" < "banana"
2. Read block at offset_2
3. Search within block
```

**Partitioned Indexes** (RocksDB):
- Split large index blocks into partitions
- Only load needed partitions
- Reduces memory for large SSTables

### 4.3 Block Caching Strategies

#### 4.3.1 Cache Tiers

```
┌─────────────────────────────────────────────────────────────┐
│                    Caching Hierarchy                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Row Cache (optional)                     │   │
│  │   - Caches decoded key-value pairs                    │   │
│  │   - Fastest for repeated point lookups               │   │
│  │   - High memory per entry                             │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Block Cache (primary)                    │   │
│  │   - Caches decompressed data blocks                   │   │
│  │   - Typically LRU or LRU with high-priority pool     │   │
│  │   - Most important cache                              │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │           Compressed Block Cache (optional)           │   │
│  │   - Caches compressed blocks                          │   │
│  │   - More entries, but decompression cost              │   │
│  │   - Good for high memory pressure scenarios          │   │
│  └──────────────────────────────────────────────────────┘   │
│                          ↓                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                 OS Page Cache                         │   │
│  │   - Filesystem caching                                │   │
│  │   - Can be bypassed with Direct I/O                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 4.3.2 Cache Admission Policies

**Problem**: Not all blocks are equally valuable. Index and filter blocks are more valuable than data blocks accessed once.

**Solutions**:
1. **Priority-based caching**: High/medium/low priority entries
2. **Tiered caching**: Separate caches for different block types
3. **Admission filters**: Only cache blocks accessed multiple times

### 4.4 Partitioning and Sharding Strategies

#### 4.4.1 Range Partitioning

```
┌─────────────────────────────────────────────────────────────┐
│                   Range Partitioning                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Partition 1: keys [a, g)     Partition 2: keys [g, m)      │
│  ┌───────────────────┐        ┌───────────────────┐         │
│  │ Memtable          │        │ Memtable          │         │
│  │ L0 SSTables       │        │ L0 SSTables       │         │
│  │ L1 SSTables       │        │ L1 SSTables       │         │
│  │ ...               │        │ ...               │         │
│  └───────────────────┘        └───────────────────┘         │
│                                                              │
│  Partition 3: keys [m, s)     Partition 4: keys [s, z]      │
│  ┌───────────────────┐        ┌───────────────────┐         │
│  │ ...               │        │ ...               │         │
│  └───────────────────┘        └───────────────────┘         │
│                                                              │
└─────────────────────────────────────────────────────────────┘

Benefits:
- Parallel compaction across partitions
- Localized write amplification
- Better cache locality
- Easier load balancing (split/merge partitions)
```

#### 4.4.2 Hash Partitioning

- Hash key to determine partition
- Good for uniform distribution
- Poor for range queries (must scan all partitions)

#### 4.4.3 Hybrid Approaches

**TiKV/CockroachDB Approach**:
- Range partitioning at high level (regions)
- Within region, single LSM tree
- Region splits when too large

### 4.5 Compaction Scheduling and I/O Prioritization

#### 4.5.1 Write Stall Prevention

```
┌─────────────────────────────────────────────────────────────┐
│              Write Stall Triggers (RocksDB)                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Soft Limits (slowdown writes):                             │
│    - L0 files > level0_slowdown_writes_trigger (20)         │
│    - Pending compaction bytes > soft_pending_compaction     │
│                                                              │
│  Hard Limits (stop writes):                                  │
│    - L0 files > level0_stop_writes_trigger (36)             │
│    - Memtables > max_write_buffer_number (6)                │
│    - Pending compaction bytes > hard_pending_compaction     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 4.5.2 I/O Scheduling

```rust
// Conceptual I/O scheduler
enum IoPriority {
    High,    // User reads/writes
    Medium,  // L0 compaction (most urgent)
    Low,     // Lower level compaction
}

struct IoScheduler {
    rate_limiter: RateLimiter,
    queues: HashMap<IoPriority, VecDeque<IoRequest>>,
}

impl IoScheduler {
    fn submit(&mut self, req: IoRequest) {
        match req.io_type {
            IoType::UserRead | IoType::UserWrite => {
                // Execute immediately, no rate limiting
                self.execute_immediate(req);
            }
            IoType::Flush => {
                // High priority background, limited rate
                self.queues[IoPriority::Medium].push_back(req);
            }
            IoType::Compaction { level } => {
                let priority = if level == 0 {
                    IoPriority::Medium  // L0 compaction is urgent
                } else {
                    IoPriority::Low
                };
                self.queues[priority].push_back(req);
            }
        }
    }
}
```

### 4.6 MVCC and Snapshot Isolation with LSM

#### 4.6.1 Version Encoding

```
Key Format with Version:
┌────────────────────────────────────────────────────────────┐
│  user_key  │  sequence_number (descending)  │  value_type  │
│  variable  │         8 bytes                │    1 byte    │
└────────────────────────────────────────────────────────────┘

Example:
"user123" @ seq=1000 → Put("profile", "{...}")
"user123" @ seq=900  → Put("profile", "{old...}")
"user123" @ seq=800  → Delete

Reading at snapshot 950:
- Returns value from seq=900
- seq=1000 is "in the future"
- seq=800 is deleted
```

#### 4.6.2 Snapshot Implementation

```rust
struct Snapshot {
    sequence_number: u64,
}

impl DB {
    fn get_snapshot(&self) -> Snapshot {
        Snapshot {
            sequence_number: self.current_sequence.load(Ordering::SeqCst)
        }
    }

    fn get_at_snapshot(&self, key: &[u8], snapshot: &Snapshot) -> Option<Value> {
        // Only return values with seq <= snapshot.sequence_number
        // Iterate in descending sequence order, stop at first match
    }
}
```

#### 4.6.3 Garbage Collection of Old Versions

```
MVCC GC Considerations:
1. Cannot delete versions visible to any active snapshot
2. Can only GC versions older than oldest active snapshot
3. Compaction is the natural place to do GC

During Compaction:
  oldest_snapshot = min(active_snapshots)

  For each key:
    Keep: most recent version <= oldest_snapshot
    Keep: all versions > oldest_snapshot (might be needed)
    Delete: all older versions (safe to GC)
```

### 4.7 Zone Storage and Append-Only Optimizations

#### 4.7.1 Zoned Namespaces (ZNS) SSDs

```
┌─────────────────────────────────────────────────────────────┐
│                    ZNS SSD Layout                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │  Zone 0  │ │  Zone 1  │ │  Zone 2  │ │  Zone 3  │ ...   │
│  │Sequential│ │Sequential│ │Sequential│ │Sequential│       │
│  │Write Only│ │Write Only│ │Write Only│ │Write Only│       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                              │
│  Zone States:                                                │
│    Empty → Open → Full → (Reset) → Empty                    │
│                                                              │
│  Operations:                                                 │
│    - Write: Append to current write pointer                 │
│    - Read: Random read anywhere                             │
│    - Reset: Erase entire zone (only way to rewrite)         │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 4.7.2 LSM-ZNS Integration

**ZenFS (RocksDB)**:
```
Mapping Strategy:
  - Each SSTable → one or more zones
  - WAL → dedicated zone(s)
  - Zone = unit of garbage collection

Benefits:
  - No SSD garbage collection (device-level WA = 1)
  - Predictable performance
  - Better endurance

Challenges:
  - Zone size alignment
  - Compaction output must align
  - Zone selection algorithm
```

---

## 5. Cutting-Edge Research Areas

### 5.1 Computational Storage

**Concept**: Push computation (filtering, aggregation) to storage devices.

**LSM Applications**:
1. **In-storage compaction**: SSD performs compaction internally
2. **Filter pushdown**: Bloom filter checks at device level
3. **Scan filtering**: Predicate evaluation in storage

**Key Papers**:
- "SmartSSD: FPGA Accelerated Near-Storage Data Analytics" - Lee et al.
- "Biscuit: A Framework for Near-Data Processing of Big Data Workloads" - Gu et al., ISCA 2016

### 5.2 GPU-Accelerated Operations

**Opportunities**:
1. **Parallel merge sort**: GPU can merge multiple sorted runs
2. **Bloom filter construction**: Parallel hashing
3. **Compression/decompression**: GPU-accelerated codecs

**Challenges**:
- PCIe bandwidth limitations
- CPU-GPU synchronization overhead
- Not suitable for all operations

**Research Direction**: Use GPU for large compactions, CPU for small ones.

### 5.3 Adaptive/Learned Compaction

#### 5.3.1 Workload-Aware Compaction

**Observation**: Static compaction parameters are suboptimal for varying workloads.

**Approach**:
```
Monitor:
  - Write rate over time
  - Read patterns (point vs range)
  - Key distribution
  - Latency requirements

Adapt:
  - More aggressive compaction during low write periods
  - Relax compaction during write bursts
  - Prioritize compaction for hot key ranges
```

#### 5.3.2 Reinforcement Learning for Compaction

**State**: Current LSM-tree state (levels, sizes, pending compactions)
**Actions**: Which compaction to trigger, which files to include
**Reward**: Minimize latency or maximize throughput

**Key Paper**: "Lethe: A Tunable Delete-Aware LSM Engine" - Sarkar et al., SIGMOD 2020

### 5.4 Integration with Modern File Systems

#### 5.4.1 ZFS Integration

**Benefits**:
- Copy-on-write semantics align with LSM
- Built-in compression
- Snapshots for consistent backups
- ARC (Adaptive Replacement Cache)

**Considerations**:
- Double buffering (ZFS ARC + LSM block cache)
- Disable ZFS compression if LSM already compresses
- Align recordsize to SSTable block size

#### 5.4.2 Btrfs Integration

**Benefits**:
- Copy-on-write
- Transparent compression
- Subvolumes for isolation

**Considerations**:
- Fragmentation over time
- May need periodic defragmentation
- Consider disabling CoW for WAL (nodatacow)

### 5.5 Emerging Architecttic Patterns

#### 5.5.1 Function-as-a-Service (FaaS) Integration

```
┌─────────────────────────────────────────────────────────────┐
│              Serverless LSM Architecture                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  Lambda 1   │  │  Lambda 2   │  │  Lambda 3   │          │
│  │  (writes)   │  │  (reads)    │  │  (compact)  │          │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘          │
│         │                │                │                  │
│         └────────────────┼────────────────┘                  │
│                          │                                   │
│                   ┌──────▼──────┐                            │
│                   │ Shared Cache │                           │
│                   │   (Redis)    │                           │
│                   └──────┬──────┘                            │
│                          │                                   │
│                   ┌──────▼──────┐                            │
│                   │  S3 Storage  │                           │
│                   │  (SSTables)  │                           │
│                   └─────────────┘                            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

#### 5.5.2 RDMA-Enabled Distributed LSM

**Concept**: Use RDMA for:
- Remote memtable reads (bypass remote CPU)
- Remote compaction coordination
- Distributed transaction coordination

**Key Paper**: "ROART: Range-query Optimized Persistent ART" - Ma et al., FAST 2021

---

## 6. Key Papers Bibliography

### Foundational Papers

1. **O'Neil et al., "The Log-Structured Merge-Tree (LSM-Tree)"**, Acta Informatica, 1996
   - The original LSM paper, must-read for understanding core concepts

2. **Chang et al., "Bigtable: A Distributed Storage System for Structured Data"**, OSDI 2006
   - Google's seminal distributed LSM system

3. **Ghemawat & Dean, "LevelDB Implementation Notes"**, Google, 2011
   - Practical details of the first modern LSM implementation

### Compaction and Optimization

4. **Dayan & Idreos, "Dostoevsky: Better Space-Time Trade-Offs for LSM-Tree Based Key-Value Stores via Adaptive Removal of Superfluous Merging"**, SIGMOD 2018
   - Defines the amplification tradeoff space rigorously

5. **Dayan et al., "Monkey: Optimal Navigable Key-Value Store"**, SIGMOD 2017
   - Optimal Bloom filter memory allocation

6. **Luo & Carey, "LSM-based Storage Techniques: A Survey"**, VLDB Journal, 2020
   - Comprehensive survey of LSM techniques

### Modern Systems

7. **Dong et al., "RocksDB: Evolution of Development Priorities in a Key-Value Store Serving Large-scale Applications"**, TODS 2021
   - RocksDB design decisions and evolution

8. **Yao et al., "MatrixKV: Reducing Write Stalls and Write Amplification in LSM-tree Based KV Stores"**, ATC 2020
   - PMem integration with LSM

9. **Kaiyrakhmet et al., "SLM-DB: Single-Level Key-Value Store with Persistent Memory"**, FAST 2019
   - Rethinking LSM for persistent memory

### Learned Structures

10. **Kraska et al., "The Case for Learned Index Structures"**, SIGMOD 2018
    - Learned indexes foundation

11. **Dai et al., "Bourbon: Learned Index Structures in Storage Engines"**, SOSP 2020
    - Practical learned indexes in LSM context

### Disaggregation and Cloud

12. **Zhong et al., "REMIX: Efficient Range Query for LSM-trees"**, FAST 2021
    - Remote index execution in disaggregated storage

13. **Cao et al., "PolarFS: An Ultra-low Latency and Failure Resilient Distributed File System"**, VLDB 2018
    - Alibaba's disaggregated storage foundation

### Emerging Hardware

14. **Bjorling et al., "ZNS: Avoiding the Block Interface Tax for Flash-based SSDs"**, ATC 2021
    - Zoned namespaces for LSM workloads

15. **Dillinger & Walzer, "Ribbon filter: practically smaller than Bloom and Xor"**, 2021
    - Modern filter improvements

---

## 7. Decision Framework

### 7.1 When to Choose LSM Over B-Tree

**Choose LSM when**:
- Write-heavy workload (>50% writes)
- Append-heavy patterns (logs, time-series)
- Sequential write performance is critical
- Space efficiency matters (SSDs)
- Can tolerate slightly higher read latency

**Choose B-Tree when**:
- Read-heavy workload (>80% reads)
- Low latency point queries are critical
- Range scan performance is paramount
- Data size fits mostly in memory
- Simpler operational model preferred

### 7.2 Compaction Strategy Selection

```
                    Workload Analysis
                          │
                          ▼
              ┌───────────────────────┐
              │  What is your primary │
              │    optimization goal? │
              └───────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    ┌─────────┐     ┌─────────┐     ┌─────────┐
    │  Write  │     │  Read   │     │  Space  │
    │Throughput│    │ Latency │     │Efficiency│
    └────┬────┘     └────┬────┘     └────┬────┘
         │               │               │
         ▼               ▼               ▼
    ┌─────────┐     ┌─────────┐     ┌─────────┐
    │ Tiered  │     │ Leveled │     │ Leveled │
    │Compaction│    │Compaction│    │Compaction│
    └─────────┘     └─────────┘     └─────────┘
```

### 7.3 Configuration Checklist

For a production LSM deployment, tune these parameters:

```yaml
# Memory
memtable_size: 64-256 MB per memtable
max_memtables: 2-4 (write buffer count)
block_cache_size: 30-50% of available RAM

# Compaction
compaction_style: leveled or universal
level0_file_trigger: 4-8 files
level0_slowdown_trigger: 20 files
level0_stop_trigger: 36 files
max_background_compactions: 2-4 (CPU cores / 2)
target_file_size_base: 64-256 MB

# Bloom Filters
bloom_bits_per_key: 10 (1% FPR)
bloom_locality: 1 (blocked bloom)
partition_filters: true for large SSTables

# Compression
compression_per_level:
  - L0-L1: LZ4 or no compression (speed)
  - L2+: ZSTD (ratio)

# I/O
direct_io: true (bypass page cache)
rate_limiter_bytes_per_sec: based on SSD IOPS budget
```

---

## 8. Practical Implementation Considerations for pg_arrow

### 8.1 Relevance to pg_arrow Project

While pg_arrow primarily reads PostgreSQL's heap files (which use in-place update storage), understanding LSM is valuable for:

1. **WAL-based Incremental Conversion**: Your existing research on WAL-based conversion could benefit from LSM-like buffering strategies
2. **Hybrid Storage Design**: Future extensions might use LSM for write-heavy workloads
3. **Compaction Concepts**: Similar merge strategies apply to Arrow file consolidation
4. **Bloom Filters**: Useful for filtering during conversion

### 8.2 Key Takeaways

1. **Sequential I/O is king**: Any storage design should maximize sequential writes
2. **Amplification tradeoffs are fundamental**: Choose based on your workload
3. **Background work needs scheduling**: Compaction/maintenance must not block foreground
4. **Modern hardware changes assumptions**: NVMe, PMem, ZNS all shift optimal designs
5. **Bloom filters are essential**: Always include probabilistic filters

---

## See Also

- [WAL, Torn Pages, and Disk Reliability](@/notes/database/wal_torn_pages.md) — WAL durability guarantees that LSM-trees rely on for crash recovery
- [MongoDB/WiredTiger Internals](@/notes/database/mongodb_wiredtiger_internals.md) — WiredTiger uses a hybrid B-tree/LSM approach with checkpointing
- [Data Structures](@/notes/programming/data_structures.md) — Bloom filters, skip lists, and other structures used within LSM-tree implementations
- [Filesystem Design](@/notes/os/filesystem_design.md) — Block allocation, journaling, and I/O patterns that affect LSM compaction performance

---

*Last updated: 2026-01-27*
*Next review: Consider when designing any write-optimized storage component*
