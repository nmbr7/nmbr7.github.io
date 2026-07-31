+++
title = "Kafka Internals"
date = 2026-02-05
[taxonomies]
tags = ["kafka", "log-structured", "replication", "exactly-once", "kraft", "streaming", "partitioning"]
+++


# Apache Kafka Internals: Expert-Level Deep Dive

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Log-Structured Storage](#2-log-structured-storage)
3. [Partitions and Segments](#3-partitions-and-segments)
4. [Replication Protocol](#4-replication-protocol)
5. [Producer Internals](#5-producer-internals)
6. [Consumer Internals](#6-consumer-internals)
7. [Exactly-Once Semantics](#7-exactly-once-semantics-eos)
8. [Coordination (ZooKeeper/KRaft)](#8-coordination-zookeeper-kraft)
9. [Performance Optimizations](#9-performance-optimizations)
10. [Guarantees Deep Dive](#10-guarantees-deep-dive)

---

## 1. Architecture Overview

Kafka's design choice is unusual among messaging systems: brokers do almost no work interpreting messages. A broker is essentially a dumb, high-throughput append-only file server — it doesn't parse message content, doesn't track per-consumer state beyond offsets, and doesn't push data to consumers. All the "smart" behavior (batching, partitioning, retries, rebalancing) lives in the client libraries. This is why Kafka scales differently from something like RabbitMQ: broker CPU and memory stay flat as consumer count grows, because the broker just serves byte ranges from disk.

### 1.1 Core Components

A cluster is a set of brokers, each independently owning some subset of partitions as leader and holding replicas of others as follower. There's no shared storage — each broker's partition data lives on its own local disks. Coordination (who's leader, who's in the cluster, topic configs) is handled by a separate control plane: historically ZooKeeper, now KRaft's own Raft-based quorum (see §8). Producers and consumers talk directly to the broker that leads the partition they care about — they discover this via metadata requests, not through the coordinator.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         KAFKA CLUSTER                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │  Broker 0   │  │  Broker 1   │  │  Broker 2   │  │  Broker 3   │    │
│  │  (Leader)   │  │  (Follower) │  │  (Leader)   │  │  (Follower) │    │
│  │             │  │             │  │             │  │             │    │
│  │ ┌─────────┐ │  │ ┌─────────┐ │  │ ┌─────────┐ │  │ ┌─────────┐ │    │
│  │ │Topic A  │ │  │ │Topic A  │ │  │ │Topic B  │ │  │ │Topic B  │ │    │
│  │ │Part 0   │ │  │ │Part 0   │ │  │ │Part 0   │ │  │ │Part 0   │ │    │
│  │ │(leader) │ │  │ │(replica)│ │  │ │(leader) │ │  │ │(replica)│ │    │
│  │ └─────────┘ │  │ └─────────┘ │  │ └─────────┘ │  │ └─────────┘ │    │
│  │ ┌─────────┐ │  │ ┌─────────┐ │  │ ┌─────────┐ │  │ ┌─────────┐ │    │
│  │ │Topic A  │ │  │ │Topic A  │ │  │ │Topic A  │ │  │ │Topic A  │ │    │
│  │ │Part 1   │ │  │ │Part 1   │ │  │ │Part 2   │ │  │ │Part 2   │ │    │
│  │ │(replica)│ │  │ │(leader) │ │  │ │(replica)│ │  │ │(leader) │ │    │
│  │ └─────────┘ │  │ └─────────┘ │  │ └─────────┘ │  │ └─────────┘ │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
│         │                │                │                │            │
│         └────────────────┴────────────────┴────────────────┘            │
│                                   │                                      │
│                    ┌──────────────┴──────────────┐                      │
│                    │     Controller (KRaft)      │                      │
│                    │  or ZooKeeper Ensemble      │                      │
│                    │  • Leader election          │                      │
│                    │  • Cluster metadata         │                      │
│                    │  • Partition assignment     │                      │
│                    └─────────────────────────────┘                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────┐                                          ┌─────────────┐
│  Producers  │ ─────────────────────────────────────── │  Consumers  │
│             │          (publish/subscribe)            │             │
└─────────────┘                                          └─────────────┘
```

### 1.2 Key Abstractions

The partition, not the topic, is Kafka's real unit of everything: parallelism, ordering, and replication all apply per-partition. A topic is just a name grouping partitions together — Kafka gives you total order *within* a partition and explicitly no order guarantee across partitions (see §10.2). This is the fundamental trade-off client code has to design around: put related events in the same partition (same key) to get ordering, at the cost of limiting parallelism for that key to one consumer at a time.

| Concept | Description |
|---------|-------------|
| **Topic** | Named feed of messages, logical grouping |
| **Partition** | Ordered, immutable sequence of records |
| **Offset** | Unique sequential ID within partition |
| **Segment** | Physical file storing partition data |
| **Replica** | Copy of partition for fault tolerance |
| **Leader** | Replica handling all reads/writes |
| **ISR** | In-Sync Replicas - caught up with leader |

---

## 2. Log-Structured Storage

### 2.1 The Commit Log

Kafka's core abstraction is an **append-only commit log**: a partition is nothing more than a sequence of records identified by position (offset), and the only mutation allowed is appending to the end. This one restriction is what makes everything else in the system simple — no in-place updates means no locking for readers, no MVCC, no compaction-on-write. A consumer reading offset 500 while a producer appends offset 10,000 never observes a torn or half-written record, because writers only ever add past the current end.

```
Partition as a Commit Log:
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  Offset:  0    1    2    3    4    5    6    7    8    9    10   ...   │
│          ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐      │
│  Records:│ R0 │ R1 │ R2 │ R3 │ R4 │ R5 │ R6 │ R7 │ R8 │ R9 │R10 │ ... │
│          └────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘      │
│                                                               ▲          │
│                                                               │          │
│  Writes always append here ──────────────────────────────────┘          │
│  (newest offset)                                                        │
│                                                                          │
│  Properties:                                                             │
│  • Append-only (immutable once written)                                 │
│  • Ordered by offset                                                    │
│  • Sequential I/O for writes                                            │
│  • Offsets are monotonically increasing                                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Why Log-Structured?

Traditional message brokers (and OLTP databases) do random I/O: each message lives at some address, gets marked delivered/deleted in place, and readers jump around the file. Disk seeks dominate cost there — a 7200 RPM HDD does ~100-200 random IOPS but 100-200 MB/s sequential. Kafka sidesteps this entirely by never seeking on the write path: producers only append, so every write is a sequential extend of the current segment file. Consumers mostly read sequentially too, since most consumption is near the tail (see page cache discussion in §9.2). The append-only log turns a messaging system's I/O pattern into the one pattern spinning disks (and SSDs, though less dramatically) are best at.

```
Traditional Database (Random I/O):
────────────────────────────────────
Disk: [  ][xx][  ][xx][  ][xx][  ][xx]
           │       │       │       │
      update   update  update  update
      (seek)   (seek)  (seek)  (seek)

Throughput: ~100-200 random IOPS on HDD

Kafka Log-Structured (Sequential I/O):
─────────────────────────────────────
Disk: [R0][R1][R2][R3][R4][R5][R6][R7]──► append
                                          │
                               single sequential write

Throughput: ~100-200 MB/s sequential on HDD
            ~500+ MB/s on SSD

Performance Gain: 1000x+ improvement
```

### 2.3 Record Format (v2, Kafka 0.11+)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    RECORD BATCH FORMAT                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Record Batch Header (61 bytes fixed):                                  │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ baseOffset (8)      │ First offset in batch                      │   │
│  │ batchLength (4)     │ Total bytes including header               │   │
│  │ partitionLeaderEpoch (4) │ Leader epoch for fencing             │   │
│  │ magic (1)           │ Version (2 for current)                    │   │
│  │ crc (4)             │ CRC32C of remaining data                   │   │
│  │ attributes (2)      │ Compression, timestamp type, transactional │   │
│  │ lastOffsetDelta (4) │ Offset of last record in batch            │   │
│  │ baseTimestamp (8)   │ Timestamp of first record                  │   │
│  │ maxTimestamp (8)    │ Max timestamp in batch                     │   │
│  │ producerId (8)      │ Producer ID (for idempotence)             │   │
│  │ producerEpoch (2)   │ Producer epoch (for fencing)              │   │
│  │ baseSequence (4)    │ First sequence number in batch            │   │
│  │ recordCount (4)     │ Number of records                          │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Records (variable length, varint encoded):                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ length (varint)       │ Record length                           │   │
│  │ attributes (1)        │ Unused currently                        │   │
│  │ timestampDelta (varint) │ Offset from baseTimestamp            │   │
│  │ offsetDelta (varint)  │ Offset from baseOffset                  │   │
│  │ keyLength (varint)    │ Key length (-1 if null)                │   │
│  │ key (bytes)           │ Key data                                │   │
│  │ valueLength (varint)  │ Value length (-1 if null)              │   │
│  │ value (bytes)         │ Value data                              │   │
│  │ headersCount (varint) │ Number of headers                       │   │
│  │ headers[]             │ Array of key-value headers             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Attributes Bit Field

```
Attributes (2 bytes):
┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐
│ 15 │ 14 │ 13 │ 12 │ 11 │ 10 │  9 │  8 │  7 │  6 │  5 │  4 │  3 │  2 │  1 │  0 │
└────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘
  │                        │    │    │    │    │    │    │    │    └────┴────┴────┘
  │                        │    │    │    │    │    │    │    │         │
  │                        │    │    │    │    │    │    │    │    Compression:
  │                        │    │    │    │    │    │    │    │    0=none, 1=gzip
  │                        │    │    │    │    │    │    │    │    2=snappy, 3=lz4
  │                        │    │    │    │    │    │    │    │    4=zstd
  │                        │    │    │    │    │    │    │    │
  │                        │    │    │    │    │    │    │    └─ Timestamp type
  │                        │    │    │    │    │    │    │       0=CreateTime
  │                        │    │    │    │    │    │    │       1=LogAppendTime
  │                        │    │    │    │    │    │    │
  │                        │    │    │    │    │    │    └─ Transactional
  │                        │    │    │    │    │    │
  │                        │    │    │    │    │    └─ Control batch
  │                        │    │    │    │    │
  │                        │    │    │    │    └─ Has delete horizon
  │                        │    │    │    │
  └────────────────────────┴────┴────┴────┴─ Unused
```

---

## 3. Partitions and Segments

A partition's commit log isn't one giant file — it's split into **segments**, each a bounded-size chunk with its own log file plus two sidecar index files. Segmenting solves two problems an unbounded single file would create: retention/compaction can drop or rewrite whole segment files instead of rewriting a multi-GB log in place, and each segment gets its own small index that fits comfortably in memory instead of one index scaling with the entire partition's history.

### 3.1 Partition Directory Structure

Each partition is a directory on disk; each segment within it contributes three files sharing a common filename — the base offset of the first record in that segment, zero-padded to 20 digits. This makes the file listing itself a sorted index: to find which segment holds offset 70000, list the directory and binary-search filenames for the largest one ≤ 70000.

```
/var/kafka-logs/
├── my-topic-0/                          # Partition 0
│   ├── 00000000000000000000.log         # Segment file (messages)
│   ├── 00000000000000000000.index       # Offset index
│   ├── 00000000000000000000.timeindex   # Time index
│   ├── 00000000000054321000.log         # Next segment (starts at offset 54321000)
│   ├── 00000000000054321000.index
│   ├── 00000000000054321000.timeindex
│   ├── 00000000000108642000.log         # Active segment
│   ├── 00000000000108642000.index
│   ├── 00000000000108642000.timeindex
│   ├── leader-epoch-checkpoint          # Leader epoch history
│   └── partition.metadata               # Partition metadata
├── my-topic-1/                          # Partition 1
│   └── ...
└── __consumer_offsets-0/                # Internal topic for consumer offsets
    └── ...
```

### 3.2 Segment File Structure

The `.log` file is just record batches concatenated back to back — no separate per-batch index inside the file itself, and no framing beyond each batch's own `batchLength` field (§2.3). Reading it means seeking to a byte offset (found via the `.index` file, §3.3) and then reading batch headers sequentially. A segment stops accepting new writes and "rolls" to a new active segment based on whichever limit hits first: size, age, or the index filling up — rolling on size keeps segments manageable for retention sweeps; rolling on time (`log.roll.ms`) bounds how stale the oldest data in an otherwise-low-traffic segment can get, which matters for time-based retention.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SEGMENT FILE (.log)                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  File: 00000000000054321000.log                                         │
│  (filename = base offset of first record in segment)                    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ Record Batch 1 (offset 54321000-54321050)                       │   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ Batch Header │ Record │ Record │ ... │ Record              │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ Record Batch 2 (offset 54321051-54321100)                       │   │
│  │ ┌─────────────────────────────────────────────────────────────┐ │   │
│  │ │ Batch Header │ Record │ Record │ ... │ Record              │ │   │
│  │ └─────────────────────────────────────────────────────────────┘ │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ Record Batch 3 ...                                              │   │
│  │ ...                                                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Segment rolls when:                                                     │
│  • Size exceeds log.segment.bytes (default 1GB)                         │
│  • Time exceeds log.roll.ms/hours (default 7 days)                      │
│  • Index file is full                                                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Offset Index Structure

The index deliberately does **not** map every offset to a position — that would make the index roughly as large as the data itself and defeat the purpose (it needs to be small enough to `mmap` and binary-search cheaply). Instead Kafka trades a bit of read cost for a lot of space savings: it records a position every `log.index.interval.bytes` (4KB default), so the index is ~0.4% the size of the log it covers. A lookup binary-searches this sparse index to the nearest entry *at or before* the target offset, seeks there, then linearly scans forward — at most 4KB of sequential reading, which is cheap since it's sequential and usually page-cache-resident. If the index file is missing or corrupted (e.g. after an unclean shutdown), the broker rebuilds it by scanning the corresponding `.log` file from scratch on startup — this is why very large segments can slow down broker restart after a crash.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    OFFSET INDEX (.index)                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Purpose: Map offset → physical file position                           │
│  Structure: Array of 8-byte entries                                     │
│                                                                          │
│  ┌────────────────────┬────────────────────┐                           │
│  │ Relative Offset(4) │ Physical Position(4)│                           │
│  ├────────────────────┼────────────────────┤                           │
│  │        0           │         0          │  offset 54321000 → pos 0  │
│  │       50           │       4096         │  offset 54321050 → pos 4K │
│  │      100           │       8192         │  offset 54321100 → pos 8K │
│  │      150           │      12288         │  offset 54321150 → pos 12K│
│  │      ...           │       ...          │                           │
│  └────────────────────┴────────────────────┘                           │
│                                                                          │
│  Index is SPARSE (not every offset):                                    │
│  • Entry added every log.index.interval.bytes (default 4KB)             │
│  • Lookup: binary search → nearest entry → sequential scan             │
│                                                                          │
│  Offset Lookup Algorithm:                                               │
│  1. Binary search in index for largest offset ≤ target                  │
│  2. Seek to physical position                                           │
│  3. Sequential scan to find exact offset                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.4 Time Index Structure

The time index exists because "seek to the message from 10 minutes ago" is common (consumer restarting with `auto.offset.reset` variants, or an app calling `offsetsForTimes()`) but offsets alone can't answer it — offsets are just sequence numbers, unrelated to wall-clock time. Rather than scanning every record's timestamp, Kafka maintains this second sparse index, populated at the same interval cadence as the offset index. A time-based seek is a two-stage binary search: first across segments (each segment's max timestamp is known), then within the target segment's time index — landing on an offset, which is then resolved to a byte position via the *offset* index. Three structures, three binary searches, chained.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TIME INDEX (.timeindex)                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Purpose: Map timestamp → offset (for time-based seeks)                 │
│  Structure: Array of 12-byte entries                                    │
│                                                                          │
│  ┌────────────────────┬────────────────────┐                           │
│  │   Timestamp (8)    │ Relative Offset(4) │                           │
│  ├────────────────────┼────────────────────┤                           │
│  │  1770216833000     │         0          │                           │
│  │  1770216834000     │        50          │                           │
│  │  1770216835000     │       100          │                           │
│  │      ...           │       ...          │                           │
│  └────────────────────┴────────────────────┘                           │
│                                                                          │
│  Time-based Seek Algorithm:                                             │
│  1. Binary search segments by max timestamp                             │
│  2. Binary search time index for offset                                 │
│  3. Use offset index to find position                                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 3.5 Log Retention and Compaction

Retention (policy 1/2) and compaction (policy 3) solve different problems and are often confused. Retention answers "how long do I keep an event stream" — old segments are deleted wholesale once every record in them is past the retention window, which is why deletion is cheap (unlink a file, no rewriting). Compaction answers a different question: "what's the latest state per key" — think of a topic as a changelog for a KV store, where you don't care about every historical update, only the current value. The **compaction (cleaner) thread** runs periodically per-partition, rewriting segments to keep only the last record per key. Deleting a key entirely is done by writing a **tombstone** — a record with that key and a `null` value; the tombstone itself is kept for `delete.retention.ms` (24h default) so that consumers who haven't yet caught up still see the delete, then it too is dropped on the next compaction pass. Compaction is I/O-throttled (`log.cleaner.io.max.bytes.per.second`) because it's a background rewrite competing with foreground produce/fetch traffic for disk bandwidth — uncapped, it would starve latency-sensitive reads.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    RETENTION POLICIES                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Policy 1: Time-based (cleanup.policy=delete)                           │
│  ─────────────────────────────────────────────                          │
│  log.retention.hours=168 (7 days default)                               │
│                                                                          │
│  Time ───────────────────────────────────────────────────────────────►  │
│       │     7 days ago      │           │        now        │           │
│       ▼                     ▼           ▼                   ▼           │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│  │ Segment │ │ Segment │ │ Segment │ │ Segment │ │ Segment │          │
│  │  (old)  │ │  (old)  │ │         │ │         │ │ (active)│          │
│  └────┬────┘ └────┬────┘ └─────────┘ └─────────┘ └─────────┘          │
│       │           │                                                     │
│       └───────────┴──► DELETE (older than retention)                   │
│                                                                          │
│  Policy 2: Size-based                                                    │
│  ────────────────────                                                   │
│  log.retention.bytes=10737418240 (10GB per partition)                   │
│                                                                          │
│  Policy 3: Compaction (cleanup.policy=compact)                          │
│  ─────────────────────────────────────────────                          │
│  Keep only latest value per key                                         │
│                                                                          │
│  Before compaction:                                                      │
│  Key: A  B  A  C  B  A  D  C  A                                         │
│  Val: 1  2  3  4  5  6  7  8  9                                         │
│                                                                          │
│  After compaction:                                                       │
│  Key: B  D  C  A                                                        │
│  Val: 5  7  8  9  (latest value for each key)                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Replication Protocol

Kafka replicates by having followers **pull** from the leader (fetch-based), not the leader pushing to followers — the same fetch RPC a consumer uses. This is a deliberate simplification: one code path handles both consumer reads and follower replication, and it means a slow follower can't be forced to accept data faster than it can process, since it controls its own fetch rate. The cost is that replication lag is bounded by fetch interval rather than being push-immediate.

### 4.1 ISR (In-Sync Replicas)

The **ISR** is the safety mechanism that makes `acks=all` mean something concrete: it's the subset of replicas that are caught up closely enough to the leader that losing the leader wouldn't lose their data. A replica falls out of the ISR when its fetch requests fall behind by more than `replica.lag.time.max.ms` — note this is time-based, not offset-based, so a replica that fetches slowly but steadily doesn't get evicted, only one that stops fetching entirely (e.g. GC pause, network partition, disk saturation). The **High Watermark** is the offset boundary between "durable" and "not yet durable": it only advances once *every* ISR member has replicated up to it, and only records below the HW are ever returned to consumers — this is what stops a consumer from reading data that could vanish if the leader dies before replication completes.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    IN-SYNC REPLICAS (ISR)                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Topic: orders, Partition: 0, Replication Factor: 3                     │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         LEADER (Broker 0)                        │   │
│  │  Log: [0][1][2][3][4][5][6][7][8][9]                            │   │
│  │                                      ▲                           │   │
│  │                                      │ LEO (Log End Offset) = 10 │   │
│  │                                      │ HW (High Watermark) = 8   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│         │                    │                                          │
│         │ replicate          │ replicate                                │
│         ▼                    ▼                                          │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐           │
│  │ FOLLOWER     │     │ FOLLOWER     │     │ FOLLOWER     │           │
│  │ (Broker 1)   │     │ (Broker 2)   │     │ (Broker 3)   │           │
│  │ IN ISR ✓     │     │ IN ISR ✓     │     │ NOT IN ISR ✗ │           │
│  │              │     │              │     │              │           │
│  │ [0][1]...[8] │     │ [0][1]...[8] │     │ [0][1][2][3] │           │
│  │ LEO=9        │     │ LEO=9        │     │ LEO=4        │           │
│  │ (1 behind)   │     │ (1 behind)   │     │ (too far!)   │           │
│  └──────────────┘     └──────────────┘     └──────────────┘           │
│                                                                          │
│  ISR = {Broker 0, Broker 1, Broker 2}                                   │
│                                                                          │
│  Replica removed from ISR when:                                         │
│  • replica.lag.time.max.ms exceeded (default 30s)                       │
│  • Hasn't fetched from leader within timeout                            │
│                                                                          │
│  High Watermark (HW):                                                    │
│  • Offset up to which ALL ISR replicas have replicated                  │
│  • Only records below HW are visible to consumers                       │
│  • HW = min(LEO of all ISR replicas)                                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Replication Flow

Note there's no separate "ack" message from follower to leader — a follower's *next* FetchRequest, by asking for offset N+1, implicitly proves it has offset N. This piggybacking avoids a whole extra RPC per replicated batch. The leader is the only party that computes the HW (as `min(LEO)` across ISR members) and it propagates that value back to followers inside FetchResponses, so followers' local HW is always slightly stale relative to the leader's — this is fine because followers don't serve consumer reads by default (`replica.selector` / KIP-392 changes this for rack-aware reads, but the HW propagation delay applies regardless).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    REPLICATION FLOW                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Step 1: Producer sends to Leader                                       │
│  ─────────────────────────────────                                      │
│  Producer ──► Leader: ProduceRequest(records)                           │
│  Leader appends to local log                                            │
│  Leader LEO: 9 → 10                                                     │
│                                                                          │
│  Step 2: Followers fetch from Leader                                    │
│  ────────────────────────────────────                                   │
│  Follower 1 ──► Leader: FetchRequest(offset=9)                          │
│  Follower 2 ──► Leader: FetchRequest(offset=9)                          │
│                                                                          │
│  Leader responds with new records                                       │
│  Followers append to local log                                          │
│  Followers LEO: 9 → 10                                                  │
│                                                                          │
│  Step 3: Followers acknowledge                                          │
│  ─────────────────────────────────                                      │
│  Next FetchRequest from follower implicitly acknowledges                │
│  Leader tracks each replica's LEO                                       │
│                                                                          │
│  Step 4: Leader advances High Watermark                                 │
│  ──────────────────────────────────────                                 │
│  When all ISR replicas reach offset 10:                                 │
│  HW: 8 → 10                                                             │
│  Leader includes new HW in FetchResponse                                │
│  Followers update their local HW                                        │
│                                                                          │
│  Step 5: Producer receives acknowledgment                               │
│  ─────────────────────────────────────────                              │
│  If acks=all: Leader waits for ISR before responding                    │
│  Producer ◄── Leader: ProduceResponse(success)                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Leader Election

The diagram's note that "records 99-100 may be LOST" only happens because those records were acknowledged to the producer *before* they were fully replicated — which only occurs under `acks=1` or `acks=0`. With `acks=all` and `min.insync.replicas=2`, the leader would not have ACKed the produce until at least one follower also had it, so the new leader (elected from the ISR) is guaranteed to hold every acknowledged record — nothing acknowledged is ever lost, only unacknowledged in-flight writes are at risk. `unclean.leader.election.enable=false` (the safe default since Kafka 0.11) additionally forbids electing a replica that fell *out* of the ISR, even if it's the only surviving replica — the alternative (`true`) trades availability for durability: the partition would rather go offline than silently accept data loss.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    LEADER ELECTION                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Scenario: Leader (Broker 0) fails                                      │
│                                                                          │
│  Before Failure:                                                         │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐            │
│  │ Broker 0       │  │ Broker 1       │  │ Broker 2       │            │
│  │ LEADER ★       │  │ Follower       │  │ Follower       │            │
│  │ LEO=100, HW=98 │  │ LEO=99         │  │ LEO=98         │            │
│  │ ISR={0,1,2}    │  │                │  │                │            │
│  └───────┬────────┘  └────────────────┘  └────────────────┘            │
│          │                                                              │
│          ✗ FAILURE                                                      │
│                                                                          │
│  Election Process:                                                       │
│  ─────────────────                                                      │
│  1. Controller detects broker failure (ZK session or heartbeat)         │
│  2. Controller selects new leader from ISR                              │
│     • Prefers replica with highest LEO                                  │
│     • If unclean.leader.election.enable=false, must be in ISR           │
│  3. Controller updates cluster metadata                                 │
│  4. New leader truncates log to HW if needed                            │
│                                                                          │
│  After Election:                                                         │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐            │
│  │ Broker 0       │  │ Broker 1       │  │ Broker 2       │            │
│  │ DOWN ✗         │  │ LEADER ★       │  │ Follower       │            │
│  │                │  │ LEO=99         │  │ LEO=98         │            │
│  │                │  │ HW=98          │  │ (catching up)  │            │
│  │                │  │ ISR={1,2}      │  │                │            │
│  └────────────────┘  └────────────────┘  └────────────────┘            │
│                                                                          │
│  Note: Records 99-100 on old leader may be LOST                         │
│  (they were not committed - below HW)                                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.4 Leader Epoch

Leader epoch replaces an older, broken mechanism (pre-KIP-101, "high watermark based truncation") that could silently cause replica divergence. The fencing logic itself: every record batch is tagged with the leader epoch active when it was written (§2.3's `partitionLeaderEpoch` field). When a follower reconnects after a partition or restart, instead of blindly truncating to its last known HW, it asks the current leader "where did epoch N end?" via an `OffsetsForLeaderEpoch` request. The leader answers with the offset where that epoch's writes stopped (i.e., where the *next* epoch began) — the follower then truncates its own log to that exact point before resuming fetches. Because epoch numbers only increase and each one has one well-defined leader, this makes divergence detection precise: two logs with the same epoch at the same offset are guaranteed to hold the same data, so there's no need to compare byte contents, only epoch/offset pairs. Producers get the same protection — a produce request carrying a stale epoch is rejected outright, which is what prevents a partitioned-away "zombie" former leader from continuing to accept writes after a new leader has taken over.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    LEADER EPOCH (Fencing)                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Problem: Split-brain, stale leaders, log divergence                    │
│  Solution: Leader Epoch - monotonically increasing number               │
│                                                                          │
│  leader-epoch-checkpoint file:                                          │
│  ┌────────────────────────────────────┐                                │
│  │ Epoch │ Start Offset               │                                │
│  ├───────┼────────────────────────────┤                                │
│  │   0   │     0                      │ (initial leader)               │
│  │   1   │   500                      │ (new leader at offset 500)     │
│  │   2   │  1200                      │ (another election)             │
│  │   3   │  1850                      │ (current leader)               │
│  └───────┴────────────────────────────┘                                │
│                                                                          │
│  Use Cases:                                                              │
│  ───────────                                                            │
│  1. Follower rejoining after partition                                  │
│     • Follower asks: "What's the end offset for epoch N?"               │
│     • Leader responds: "Epoch N ended at offset X"                      │
│     • Follower truncates if needed                                      │
│                                                                          │
│  2. Preventing stale produce                                            │
│     • ProduceRequest includes partition leader epoch                    │
│     • Broker rejects if epoch is stale                                  │
│                                                                          │
│  3. Log reconciliation                                                  │
│     • On leader change, followers truncate divergent suffix             │
│     • Ensures all replicas converge to same log                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Producer Internals

### 5.1 Producer Architecture

The producer's whole design centers on decoupling the caller's `send()` from the network — `send()` returns almost immediately after handing the record to the in-memory accumulator, while a dedicated Sender thread does the actual I/O asynchronously. This is what lets a single producer instance sustain high throughput without the calling application thread blocking on network round trips. The partitioner decides *which* partition a record lands in: with an explicit key, it hashes the key (murmur2) so all records with the same key always land on the same partition, preserving per-key order; without a key, newer Kafka versions use a **sticky** partitioner that batches several keyless records onto one partition before rotating, rather than round-robining every single record — this produces fuller batches (better compression, fewer requests) at a small cost to even distribution.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PRODUCER ARCHITECTURE                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                      KafkaProducer                               │   │
│  │                                                                   │   │
│  │  ┌─────────────┐    ┌─────────────────────────────────────────┐ │   │
│  │  │ Interceptors│───►│           Serializers                    │ │   │
│  │  └─────────────┘    │  Key Serializer │ Value Serializer      │ │   │
│  │                     └────────────────────────────┬────────────┘ │   │
│  │                                                  │               │   │
│  │                                                  ▼               │   │
│  │                     ┌─────────────────────────────────────────┐ │   │
│  │                     │           Partitioner                    │ │   │
│  │                     │  • DefaultPartitioner (murmur2 hash)    │ │   │
│  │                     │  • RoundRobinPartitioner                 │ │   │
│  │                     │  • Custom partitioner                    │ │   │
│  │                     └────────────────────────────┬────────────┘ │   │
│  │                                                  │               │   │
│  │                                                  ▼               │   │
│  │  ┌───────────────────────────────────────────────────────────┐  │   │
│  │  │              Record Accumulator (Buffer)                   │  │   │
│  │  │  ┌─────────────────────────────────────────────────────┐  │  │   │
│  │  │  │ Topic-Partition Batches                              │  │  │   │
│  │  │  │                                                      │  │  │   │
│  │  │  │  topic-0: [Batch 1 ████████] [Batch 2 ███░░░░░]     │  │  │   │
│  │  │  │  topic-1: [Batch 1 █████░░░]                        │  │  │   │
│  │  │  │  topic-2: [Batch 1 ██████████] [Batch 2 █░░░░░░░]   │  │  │   │
│  │  │  │                                                      │  │  │   │
│  │  │  │  buffer.memory = 32MB (total)                       │  │  │   │
│  │  │  │  batch.size = 16KB (per batch)                      │  │  │   │
│  │  │  └─────────────────────────────────────────────────────┘  │  │   │
│  │  └───────────────────────────────────────────────────────────┘  │   │
│  │                                   │                              │   │
│  └───────────────────────────────────┼──────────────────────────────┘   │
│                                      │                                   │
│                                      ▼                                   │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                      Sender Thread                                 │  │
│  │  • Drains batches from accumulator                                │  │
│  │  • Groups by broker (NetworkClient)                               │  │
│  │  • Sends ProduceRequests                                          │  │
│  │  • Handles retries                                                │  │
│  │                                                                    │  │
│  │  linger.ms = 0 (send immediately) or N (wait to batch)           │  │
│  │  max.in.flight.requests.per.connection = 5                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Batching and Compression

`linger.ms` is a deliberate latency-for-throughput trade: it tells the sender to wait a little (even when there's data ready to send) so more records can accumulate into the same batch, amortizing the fixed per-request overhead (TCP/TLS framing, broker-side request processing) over more payload. Compression compounds this benefit because it operates on the whole batch, not per-record — batching first means compression sees more redundancy to exploit (repeated keys, similar JSON structure, etc.), so bigger batches compress proportionally better, not just linearly. Codec choice is a CPU/ratio trade: `lz4` and `snappy` are cheap enough to enable by default with negligible producer/consumer CPU cost; `zstd` gets meaningfully better ratios for a moderate CPU premium and is generally the best default on modern hardware; `gzip` compresses best but its CPU cost on both ends makes it a poor fit for high-throughput topics.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BATCHING AND COMPRESSION                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Without Batching:                                                       │
│  ─────────────────                                                      │
│  Record 1 ──► Network ──► Broker (1 request)                            │
│  Record 2 ──► Network ──► Broker (1 request)                            │
│  Record 3 ──► Network ──► Broker (1 request)                            │
│  Total: 3 network round trips                                           │
│                                                                          │
│  With Batching (linger.ms=5, batch.size=16KB):                          │
│  ───────────────────────────────────────────                            │
│  Record 1 ─┐                                                            │
│  Record 2 ─┼──► Batch ──► Network ──► Broker (1 request)               │
│  Record 3 ─┘                                                            │
│  Total: 1 network round trip                                            │
│                                                                          │
│  Compression (applied to whole batch):                                  │
│  ─────────────────────────────────────                                  │
│  ┌─────────────────────────────────────────┐                           │
│  │ Uncompressed Batch: 16KB                │                           │
│  │ ┌─────────────────────────────────────┐ │                           │
│  │ │ R1 │ R2 │ R3 │ R4 │ R5 │ ... │ Rn  │ │                           │
│  │ └─────────────────────────────────────┘ │                           │
│  └────────────────────┬────────────────────┘                           │
│                       │ compress (lz4/snappy/zstd/gzip)                │
│                       ▼                                                 │
│  ┌─────────────────────────────────────────┐                           │
│  │ Compressed Batch: ~4KB (4:1 typical)    │                           │
│  │ ┌─────────────────────────────────────┐ │                           │
│  │ │ ████████████░░░░░░░░░░░░░░░░░░░░░░░ │ │                           │
│  │ └─────────────────────────────────────┘ │                           │
│  └─────────────────────────────────────────┘                           │
│                                                                          │
│  Compression Comparison:                                                 │
│  ┌──────────┬───────────────┬────────────────┬──────────────┐          │
│  │ Codec    │ Ratio         │ CPU (compress) │ CPU (decomp) │          │
│  ├──────────┼───────────────┼────────────────┼──────────────┤          │
│  │ none     │ 1.0x          │ -              │ -            │          │
│  │ snappy   │ ~2x           │ Low            │ Very Low     │          │
│  │ lz4      │ ~2.5x         │ Low            │ Very Low     │          │
│  │ zstd     │ ~3.5x         │ Medium         │ Low          │          │
│  │ gzip     │ ~4x           │ High           │ Medium       │          │
│  └──────────┴───────────────┴────────────────┴──────────────┘          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.3 Acknowledgment Modes

`acks` controls how many parties must have the record before the producer considers the write successful — it's the single biggest lever for the durability/latency trade-off in Kafka. `acks=0` and `acks=1` share a subtle danger: both can silently lose data on leader failure, but for different reasons — `acks=0` because the producer never even confirms the leader received it (a dropped packet is indistinguishable from success), and `acks=1` because the leader ACKs before replication happens, so a leader crash immediately after ACKing loses that record along with the leader. Only `acks=all` ties the producer's success signal to the same ISR-replication guarantee that protects consumers (§4.1's High Watermark) — which is why `acks=all` is required for exactly-once semantics (§7) to mean anything.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ACKNOWLEDGMENT MODES (acks)                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  acks=0 (Fire and Forget)                                               │
│  ─────────────────────────                                              │
│  Producer ──► Leader: Send records                                      │
│  Producer: Don't wait for response                                      │
│                                                                          │
│  • Fastest (no round-trip wait)                                         │
│  • No delivery guarantee                                                │
│  • Records may be lost                                                  │
│                                                                          │
│  acks=1 (Leader Only)                                                   │
│  ─────────────────────                                                  │
│  Producer ──► Leader: Send records                                      │
│  Leader: Append to local log                                            │
│  Leader ──► Producer: ACK                                               │
│                                                                          │
│  • Moderate latency                                                     │
│  • Records may be lost if leader fails before replication               │
│                                                                          │
│  acks=all/-1 (All ISR)                                                  │
│  ─────────────────────                                                  │
│  Producer ──► Leader: Send records                                      │
│  Leader: Append to local log                                            │
│  Leader: Wait for all ISR to replicate                                  │
│  Followers ──► Leader: Fetch and ACK                                    │
│  Leader ──► Producer: ACK                                               │
│                                                                          │
│  • Highest latency                                                      │
│  • Strongest durability (survives n-1 broker failures)                  │
│  • Requires min.insync.replicas for true durability                     │
│                                                                          │
│  Timeline Comparison:                                                    │
│  ────────────────────                                                   │
│  acks=0:   [send]►                           ~0.5ms                     │
│  acks=1:   [send]──[leader write]──[ack]►    ~2ms                       │
│  acks=all: [send]──[leader]──[replicate]──[ack]►  ~5-10ms              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.4 Idempotent Producer

Retries are necessary for reliability (a producer that gives up on the first network blip loses data) but naive retries reintroduce duplicates — the classic "did my write succeed or did just the ACK get lost" ambiguity. Kafka solves this the same way TCP solves reordering/duplication: sequence numbers, scoped per-`(producer_id, partition)` pair. The broker only accepts a batch if its sequence number is exactly `expected + 1`; a lower sequence means "I've already applied this, it's a retry" (silently deduped, ACK returned as if freshly written) and a higher sequence means a gap — something is badly wrong (a lost batch that will never be retried, or a bug), so the broker fails hard rather than silently accepting an ordering violation. This is why idempotence requires `max.in.flight.requests.per.connection ≤ 5` in older Kafka: with more in-flight, a retried (out-of-order) batch could land after a later batch already succeeded, and the broker can't always reorder-detect past that window. Idempotence gives exactly-once *per partition, per producer session* — it does not by itself give cross-partition atomicity, which is what full transactions (§7) add on top.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    IDEMPOTENT PRODUCER                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Problem: Network failures can cause duplicate messages                 │
│  ──────────────────────────────────────────────────                     │
│  Producer ──► Broker: Send record                                       │
│  Broker: Append to log ✓                                                │
│  Broker ──► Producer: ACK (but network fails)                           │
│  Producer: Timeout, retry                                               │
│  Producer ──► Broker: Send same record again                            │
│  Broker: Append AGAIN (DUPLICATE!)                                      │
│                                                                          │
│  Solution: Producer ID + Sequence Numbers                               │
│  ─────────────────────────────────────────                              │
│  enable.idempotence=true (default in Kafka 3.0+)                        │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         BROKER                                   │   │
│  │                                                                   │   │
│  │  Per-Partition State:                                            │   │
│  │  ┌──────────────────────────────────────────────────────────┐   │   │
│  │  │ Producer ID │ Last Sequence │ Last 5 Batches (for retry) │   │   │
│  │  ├─────────────┼───────────────┼────────────────────────────┤   │   │
│  │  │  PID: 1000  │    Seq: 42    │ [38, 39, 40, 41, 42]       │   │   │
│  │  │  PID: 1001  │    Seq: 17    │ [13, 14, 15, 16, 17]       │   │   │
│  │  └──────────────────────────────────────────────────────────┘   │   │
│  │                                                                   │   │
│  │  Deduplication Logic:                                            │   │
│  │  if (incoming.seq == expected.seq):                              │   │
│  │      append to log, increment expected seq                       │   │
│  │  elif (incoming.seq < expected.seq):                             │   │
│  │      return DuplicateSequenceException (dedupe!)                 │   │
│  │  elif (incoming.seq > expected.seq):                             │   │
│  │      return OutOfOrderSequenceException (fatal)                  │   │
│  │                                                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Guarantees:                                                             │
│  • Exactly-once semantics within a partition                            │
│  • Requires max.in.flight.requests.per.connection ≤ 5                   │
│  • Automatic with Kafka 3.0+ producers                                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Consumer Internals

### 6.1 Consumer Group Architecture

A consumer group is Kafka's scaling mechanism for consumption: partitions are the unit of parallelism, and the group coordinator ensures each partition is owned by exactly one consumer in the group at a time, so work is spread out without any two consumers processing the same partition concurrently. This has a hard ceiling — parallelism within a group can't exceed partition count, which is why partition count is a capacity-planning decision made up front (repartitioning later is possible but doesn't preserve key-to-partition mapping, breaking ordering guarantees for existing keys).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CONSUMER GROUP                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Topic: orders (6 partitions)                                           │
│  Consumer Group: order-processors                                        │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                         KAFKA CLUSTER                            │   │
│  │                                                                   │   │
│  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐              │   │
│  │  │ P0  │ │ P1  │ │ P2  │ │ P3  │ │ P4  │ │ P5  │              │   │
│  │  └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘              │   │
│  │     │       │       │       │       │       │                   │   │
│  └─────┼───────┼───────┼───────┼───────┼───────┼───────────────────┘   │
│        │       │       │       │       │       │                        │
│        └───┬───┘       └───┬───┘       └───┬───┘                        │
│            │               │               │                            │
│            ▼               ▼               ▼                            │
│     ┌──────────┐    ┌──────────┐    ┌──────────┐                       │
│     │Consumer 1│    │Consumer 2│    │Consumer 3│                       │
│     │ P0, P1   │    │ P2, P3   │    │ P4, P5   │                       │
│     └──────────┘    └──────────┘    └──────────┘                       │
│                                                                          │
│  Rules:                                                                  │
│  • Each partition assigned to exactly ONE consumer in group             │
│  • Each consumer can have multiple partitions                           │
│  • Max parallelism = number of partitions                               │
│  • If consumers > partitions, some consumers are idle                   │
│                                                                          │
│  Rebalance Triggers:                                                     │
│  • Consumer joins group                                                 │
│  • Consumer leaves (graceful or crash)                                  │
│  • Partitions added to topic                                            │
│  • Consumer heartbeat timeout                                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Group Coordinator and Rebalancing

The protocol below is the classic **eager** rebalance: on any membership change, *every* consumer in the group revokes *all* its partitions and the whole group re-joins from scratch, even consumers whose assignment wouldn't otherwise change. This "stop-the-world" behavior means a single consumer joining or leaving pauses the entire group's consumption during rebalancing — painful for large groups or frequent scaling events.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    GROUP COORDINATION PROTOCOL                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Step 1: Find Coordinator                                               │
│  ─────────────────────────                                              │
│  Consumer ──► Any Broker: FindCoordinatorRequest(group_id)              │
│  Broker ──► Consumer: coordinator_id = hash(group_id) % num_partitions  │
│                       of __consumer_offsets topic                       │
│                                                                          │
│  Step 2: Join Group                                                     │
│  ─────────────────────                                                  │
│  Consumer ──► Coordinator: JoinGroupRequest                             │
│    {                                                                    │
│      group_id: "order-processors",                                      │
│      member_id: "",  // empty on first join                             │
│      protocol_type: "consumer",                                         │
│      protocols: [                                                       │
│        { name: "range", metadata: subscribed_topics },                  │
│        { name: "roundrobin", metadata: subscribed_topics }              │
│      ]                                                                  │
│    }                                                                    │
│                                                                          │
│  Coordinator waits for all consumers (rebalance timeout)                │
│  Coordinator selects leader (first consumer to join)                    │
│                                                                          │
│  Coordinator ──► All Consumers: JoinGroupResponse                       │
│    {                                                                    │
│      generation_id: 5,                                                  │
│      leader: "consumer-1-uuid",                                         │
│      member_id: "consumer-X-uuid",                                      │
│      members: [...] // only leader gets full list                       │
│    }                                                                    │
│                                                                          │
│  Step 3: Sync Group (Leader assigns partitions)                         │
│  ──────────────────────────────────────────────                         │
│  Leader Consumer: Run partition assignment algorithm                    │
│  Leader ──► Coordinator: SyncGroupRequest                               │
│    {                                                                    │
│      group_id: "order-processors",                                      │
│      generation_id: 5,                                                  │
│      assignments: {                                                     │
│        "consumer-1-uuid": [P0, P1],                                     │
│        "consumer-2-uuid": [P2, P3],                                     │
│        "consumer-3-uuid": [P4, P5]                                      │
│      }                                                                  │
│    }                                                                    │
│                                                                          │
│  Other Consumers ──► Coordinator: SyncGroupRequest (empty assignments)  │
│                                                                          │
│  Coordinator ──► All Consumers: SyncGroupResponse                       │
│    { assignment: [assigned_partitions] }                                │
│                                                                          │
│  Step 4: Heartbeating                                                   │
│  ─────────────────────                                                  │
│  Consumer ──► Coordinator: HeartbeatRequest (every heartbeat.interval)  │
│  Coordinator ──► Consumer: HeartbeatResponse                            │
│    { error_code: REBALANCE_IN_PROGRESS } // triggers rejoin             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Incremental cooperative rebalancing** (KIP-429, `CooperativeStickyAssignor`, the modern default) fixes the stop-the-world problem. Instead of every consumer revoking everything, the group runs the JoinGroup/SyncGroup exchange the same way, but the assignor computes a *diff* from the previous assignment: consumers only give up the specific partitions that need to move to a different consumer, and keep processing every partition they already own and still own after the rebalance. This can take two rebalance rounds instead of one (partitions that must move are revoked in round one, reassigned in round two — a deliberate safety measure so a partition is never owned by two consumers simultaneously), but for the common case of "one more consumer joined an 8-consumer group," most consumers see zero interruption instead of all 8 pausing.

### 6.3 Offset Management

Storing consumer offsets *as a Kafka topic* rather than in an external store (early Kafka used ZooKeeper for this) is a self-hosting trick: it reuses the same replication/durability machinery the rest of the system already has, so offset commits get the same fault-tolerance as message data for free. Compaction (§3.5) keeps this topic small despite constant commits — only the latest offset per `(group, topic, partition)` key survives, so a group committing every second for a year still only occupies one compacted record per partition, not one per commit. The commit-mode choice is really a latency/safety trade at the consumer level mirroring `acks` at the producer level: auto-commit is "fire and forget" (can reprocess on crash, since the commit may lag actual processing), sync commit blocks until durable (safest, slowest), async commit doesn't block but needs explicit error handling since a failed async commit is otherwise silent.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    OFFSET MANAGEMENT                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  __consumer_offsets Topic (Internal):                                   │
│  ────────────────────────────────────                                   │
│  • 50 partitions by default                                             │
│  • Compacted (keeps latest offset per key)                              │
│  • Key: (group_id, topic, partition)                                    │
│  • Value: (offset, metadata, timestamp)                                 │
│                                                                          │
│  Offset Storage Format:                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ Key: ["order-processors", "orders", 0]                          │   │
│  │ Value: {                                                        │   │
│  │   offset: 12345,                                                │   │
│  │   leader_epoch: 5,                                              │   │
│  │   metadata: "consumer-1",                                       │   │
│  │   commit_timestamp: 1770216833000                               │   │
│  │ }                                                               │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Commit Modes:                                                           │
│  ─────────────                                                          │
│                                                                          │
│  1. Auto-commit (enable.auto.commit=true)                               │
│     • Commits every auto.commit.interval.ms (5000ms default)            │
│     • At-least-once semantics (may reprocess on crash)                  │
│                                                                          │
│  2. Manual Sync Commit                                                  │
│     consumer.commitSync()  // blocks until committed                    │
│     • Stronger guarantee                                                │
│     • Higher latency                                                    │
│                                                                          │
│  3. Manual Async Commit                                                 │
│     consumer.commitAsync(callback)  // non-blocking                     │
│     • Lower latency                                                     │
│     • Need callback for error handling                                  │
│                                                                          │
│  Offset Reset Policies (auto.offset.reset):                             │
│  ──────────────────────────────────────────                             │
│  • earliest: Start from beginning (offset 0)                            │
│  • latest: Start from end (new messages only)                           │
│  • none: Throw exception if no committed offset                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.4 Fetch Protocol

The `min_bytes`/`max_wait_ms` pair is Kafka's version of Nagle's algorithm applied to consumption: rather than the broker responding the instant *any* data exists (wasteful for low-traffic topics — constant tiny responses) or the consumer polling on a fixed timer (wastes round trips when idle, adds latency when busy), the broker holds the request open until either enough bytes accumulate or the wait time expires, whichever comes first. This is what makes long-polling cheap: an idle consumer isn't hammering the broker, but a burst of new messages is still delivered as soon as `min_bytes` is satisfied, not after some fixed poll interval. `isolation_level` on the fetch request is where the read-side half of transactional semantics (§7.3) plugs in — it tells the broker whether to include or filter out records still inside an open, uncommitted transaction.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    FETCH PROTOCOL                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Consumer ──► Broker: FetchRequest                                      │
│  {                                                                      │
│    max_wait_ms: 500,        // max time to wait for data               │
│    min_bytes: 1,            // min data to return (triggers wait)      │
│    max_bytes: 52428800,     // 50MB max response                       │
│    isolation_level: READ_COMMITTED,                                     │
│    topics: [                                                            │
│      {                                                                  │
│        topic: "orders",                                                 │
│        partitions: [                                                    │
│          { partition: 0, fetch_offset: 1000, max_bytes: 1048576 },     │
│          { partition: 1, fetch_offset: 2000, max_bytes: 1048576 }      │
│        ]                                                                │
│      }                                                                  │
│    ]                                                                    │
│  }                                                                      │
│                                                                          │
│  Broker ──► Consumer: FetchResponse                                     │
│  {                                                                      │
│    topics: [                                                            │
│      {                                                                  │
│        topic: "orders",                                                 │
│        partitions: [                                                    │
│          {                                                              │
│            partition: 0,                                                │
│            error_code: 0,                                               │
│            high_watermark: 1500,     // latest committed offset        │
│            last_stable_offset: 1450, // for transactions               │
│            records: [RecordBatch, RecordBatch, ...]                    │
│          }                                                              │
│        ]                                                                │
│      }                                                                  │
│    ]                                                                    │
│  }                                                                      │
│                                                                          │
│  Long Polling:                                                          │
│  ─────────────                                                          │
│  • If no data available, broker waits up to max_wait_ms                 │
│  • Returns immediately if min_bytes of data available                   │
│  • Reduces polling overhead for low-volume topics                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Exactly-Once Semantics (EOS)

Idempotence (§5.4) gives exactly-once *within a single partition*. Real stream-processing pipelines need more: atomically writing to multiple output partitions, and atomically committing consumer offsets alongside those writes, so that "process a record and produce results" behaves as one indivisible unit even across broker/partition boundaries. That's what the transaction protocol adds — it's essentially two-phase commit, with a dedicated coordinator playing the role of transaction manager and control records (markers) in each data partition playing the role of the participant's durable vote.

### 7.1 Transaction Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TRANSACTION ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Components:                                                             │
│  ───────────                                                            │
│  • Transaction Coordinator (broker hosting __transaction_state)         │
│  • __transaction_state topic (stores transaction metadata)              │
│  • Producer with transactional.id                                       │
│  • Control records (commit/abort markers in partitions)                 │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     PRODUCER                                     │   │
│  │  transactional.id = "order-processor-1"                         │   │
│  │  producer.id = 1000 (assigned by coordinator)                   │   │
│  │  epoch = 5 (incremented on restart)                             │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
│                             │                                           │
│           ┌─────────────────┴─────────────────┐                        │
│           ▼                                   ▼                         │
│  ┌─────────────────────┐          ┌─────────────────────────────────┐  │
│  │   Transaction       │          │        DATA PARTITIONS          │  │
│  │   Coordinator       │          │                                  │  │
│  │                     │          │  orders-0: [R][R][R][C]         │  │
│  │  __transaction_state│          │  orders-1: [R][R][C]            │  │
│  │  ┌────────────────┐ │          │  orders-2: [R][R][R][R][C]      │  │
│  │  │ txn-id: meta   │ │          │                                  │  │
│  │  │ PID: 1000      │ │          │  [R] = Record (uncommitted)     │  │
│  │  │ epoch: 5       │ │          │  [C] = Commit marker            │  │
│  │  │ state: ONGOING │ │          │  [A] = Abort marker             │  │
│  │  │ partitions: [] │ │          │                                  │  │
│  │  └────────────────┘ │          └─────────────────────────────────┘  │
│  └─────────────────────┘                                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Transaction Protocol

Two-phase commit is needed here for the same reason it's needed in any distributed transaction: partitions might be spread across different brokers, and there's no way to atomically write to all of them in one network call. The **prepare** phase (5a) durably records the *intent* to commit before touching any data partition — if the coordinator crashes right after writing PREPARE_COMMIT, it can safely resume and finish sending markers on recovery, because the intent survived. If it crashes *before* prepare is durable, the transaction is simply aborted on recovery — the ambiguity window is closed by making the coordinator's own state the source of truth, not the data partitions'.

Two failure modes worth naming: **transaction timeout** (`transaction.timeout.ms`, 60s default) aborts a transaction that's been open too long without an `EndTxnRequest` — this protects against a stalled producer holding partitions in a permanent PREPARE/ONGOING limbo, which would otherwise block `READ_COMMITTED` consumers indefinitely (their LSO can't advance past an open transaction, §7.3). **Zombie fencing** handles a producer that hangs (GC pause, network partition) and gets restarted elsewhere with the same `transactional.id` — `InitProducerIdRequest` bumps the producer epoch, and any subsequent request from the *old* (zombie) instance carrying the stale epoch is rejected, preventing it from completing a transaction after a newer instance has already taken over. Both mechanisms exist because a transactional producer, unlike an idempotent-only one, can leave partitions in a genuinely blocked state if it disappears mid-transaction — plain idempotence has no such "open" state to get stuck in.

Transactions are not free: the two-phase handshake adds coordinator round trips before data is visible, `READ_COMMITTED` consumers must buffer past in-flight transactions rather than delivering immediately, and control records themselves consume offset space. Production guidance is generally to batch more work per transaction (fewer, larger transactions) rather than wrapping every single record, to amortize this overhead — end-to-end latency overhead is commonly cited in the 20-30% range for `acks=all` + transactions versus `acks=all` alone.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TRANSACTION PROTOCOL                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Phase 1: Initialize                                                    │
│  ───────────────────                                                    │
│  Producer ──► Coordinator: InitProducerIdRequest(transactional_id)      │
│  Coordinator:                                                           │
│    - Find or create transaction state                                   │
│    - Assign/return PID                                                  │
│    - Increment epoch (fences old producers)                             │
│  Coordinator ──► Producer: InitProducerIdResponse(pid, epoch)           │
│                                                                          │
│  Phase 2: Begin Transaction                                             │
│  ──────────────────────────                                             │
│  producer.beginTransaction()  // client-side only, no RPC              │
│                                                                          │
│  Phase 3: Add Partitions                                                │
│  ───────────────────────                                                │
│  Producer ──► Coordinator: AddPartitionsToTxnRequest(partitions)        │
│  Coordinator:                                                           │
│    - Record partitions in transaction state                             │
│    - State: Empty → Ongoing                                             │
│                                                                          │
│  Phase 4: Produce Records                                               │
│  ────────────────────────                                               │
│  Producer ──► Partition Leaders: ProduceRequest (with PID, epoch)       │
│  Leaders:                                                               │
│    - Validate PID/epoch                                                 │
│    - Append records (visible only with READ_UNCOMMITTED)                │
│                                                                          │
│  Phase 5: Commit (Two-Phase)                                            │
│  ───────────────────────────                                            │
│  5a. Prepare:                                                           │
│  Producer ──► Coordinator: EndTxnRequest(COMMIT)                        │
│  Coordinator:                                                           │
│    - Write PREPARE_COMMIT to __transaction_state                        │
│                                                                          │
│  5b. Commit:                                                            │
│  Coordinator ──► All Partition Leaders: WriteTxnMarkersRequest(COMMIT)  │
│  Leaders:                                                               │
│    - Append COMMIT control record to partition                          │
│    - Records now visible to READ_COMMITTED consumers                    │
│                                                                          │
│  5c. Complete:                                                          │
│  Coordinator:                                                           │
│    - Write COMPLETE_COMMIT to __transaction_state                       │
│  Coordinator ──► Producer: EndTxnResponse(success)                      │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Consumer Isolation Levels

`READ_COMMITTED` doesn't filter records after the fact — the consumer can't simply "skip" TX2's records because it doesn't know at read time whether TX2 will commit or abort. Instead the **Last Stable Offset (LSO)** acts as a hard ceiling: it's pinned at the start of the oldest *still-open* transaction, so a `READ_COMMITTED` consumer literally cannot read past that point until the transaction resolves (commit or abort) and the LSO advances. This is why a stuck transaction (see §7.2's timeout discussion) is dangerous — it doesn't corrupt data, but it can stall every `READ_COMMITTED` consumer on that partition indefinitely, since the LSO is stuck too. Once resolved, aborted records are skipped entirely (never delivered) while committed ones are delivered in their original offset order — transactions don't reorder anything, they only gate visibility.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ISOLATION LEVELS                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Partition Log with Transactions:                                       │
│                                                                          │
│  Offset:    0    1    2    3    4    5    6    7    8    9   10        │
│            ┌────┬────┬────┬────┬────┬────┬────┬────┬────┬────┬────┐    │
│  Records:  │ R  │ R  │ R  │ R  │ C  │ R  │ R  │ A  │ R  │ R  │    │    │
│            │TX1 │TX1 │TX2 │TX1 │TX1 │TX2 │TX2 │TX2 │TX3 │TX3 │    │    │
│            └────┴────┴────┴────┴────┴────┴────┴────┴────┴────┴────┘    │
│                              │                   │                      │
│                           COMMIT              ABORT                     │
│                            TX1                 TX2                      │
│                                                                          │
│  READ_UNCOMMITTED (isolation.level):                                    │
│  ─────────────────────────────────                                      │
│  Sees: R, R, R, R, C, R, R, A, R, R                                    │
│  • All records visible immediately                                      │
│  • Includes uncommitted and aborted                                     │
│  • Lowest latency                                                       │
│                                                                          │
│  READ_COMMITTED (isolation.level):                                      │
│  ───────────────────────────────                                        │
│  Sees: R(TX1), R(TX1), R(TX1)  [offsets 0,1,3 - TX1 committed]         │
│  Skips: R(TX2), R(TX2)         [TX2 aborted]                           │
│  Waits: R(TX3), R(TX3)         [TX3 ongoing - at LSO]                  │
│                                                                          │
│  Last Stable Offset (LSO):                                              │
│  ─────────────────────────                                              │
│  • Offset of first record in an ongoing transaction                     │
│  • READ_COMMITTED consumers can only read up to LSO                     │
│  • Prevents reading uncommitted data                                    │
│                                                                          │
│  Log with LSO:                                                          │
│  Offset:    0    1    2    3    4    5    6    7    8                   │
│            ┌────┬────┬────┬────┬────┬────┬────┬────┬────┐              │
│            │ R  │ R  │ C  │ R  │ R  │ R  │ R  │ R  │    │              │
│            │TX1 │TX1 │TX1 │TX2 │TX2 │TX3 │TX3 │TX2 │    │              │
│            └────┴────┴────┴────┴────┴────┴────┴────┴────┘              │
│                        │         │                                      │
│                       HW=8      LSO=3 (TX2 ongoing)                     │
│                                                                          │
│  READ_COMMITTED can read up to offset 2 (LSO-1)                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.4 Exactly-Once Stream Processing

The critical detail here is `sendOffsetsToTransaction()` — without it, output-write and offset-commit would be two independent operations, and a crash between them creates either data loss (offset committed, output never produced) or duplication (output produced, offset not committed, so reprocessing on restart repeats the transform). By folding the consumer offset commit *into the same transaction* as the output records, both become part of one atomic unit: on abort, neither the output records nor the offset advance are visible, so a restart replays the input from the same place and produces the same output again — visible only once it's actually committed. This is the pattern Kafka Streams uses internally for its own exactly-once guarantee, rather than something applications typically hand-roll.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    EXACTLY-ONCE STREAM PROCESSING                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Pattern: Consume-Transform-Produce in Single Transaction               │
│                                                                          │
│  ┌─────────────┐                              ┌─────────────┐           │
│  │  Input      │                              │  Output     │           │
│  │  Topic      │                              │  Topic      │           │
│  │  (orders)   │                              │ (processed) │           │
│  └──────┬──────┘                              └──────▲──────┘           │
│         │                                           │                   │
│         │ consume                            produce│                   │
│         ▼                                           │                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    STREAM PROCESSOR                              │   │
│  │                                                                   │   │
│  │  while (true) {                                                  │   │
│  │    producer.beginTransaction();                                  │   │
│  │                                                                   │   │
│  │    records = consumer.poll();                                    │   │
│  │    for (record : records) {                                      │   │
│  │      result = transform(record);                                 │   │
│  │      producer.send(outputTopic, result);                         │   │
│  │    }                                                             │   │
│  │                                                                   │   │
│  │    // Atomically commit:                                         │   │
│  │    // 1. Output records                                          │   │
│  │    // 2. Consumer offsets                                        │   │
│  │    producer.sendOffsetsToTransaction(offsets, consumerGroupId);  │   │
│  │    producer.commitTransaction();                                 │   │
│  │  }                                                               │   │
│  │                                                                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Guarantees:                                                             │
│  • Output records and offset commit are atomic                          │
│  • On failure/restart: transaction aborted, offsets not committed       │
│  • Consumer resumes from last committed offset                          │
│  • No duplicates, no data loss                                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Coordination (ZooKeeper/KRaft)

Someone has to answer "who's the leader of partition N," "which brokers are alive," and "what's this topic's config" — and that someone needs to be consistent even when brokers disagree or fail concurrently. Kafka has used two different systems for this control-plane role: an external ZooKeeper ensemble (original design), and, since KIP-500, an internal Raft-based quorum (KRaft) that removes the ZooKeeper dependency entirely.

### 8.1 ZooKeeper Mode (Legacy)

ZooKeeper's ephemeral znodes are what makes controller election and broker liveness work: an ephemeral node only exists while the creating session is alive, so `/controller` disappearing (session timeout, e.g. from a GC pause or crash) automatically signals every watching broker to race for controller again — no explicit failure detector needed, ZK's session mechanism *is* the failure detector. The structural problem this diagram hints at ("metadata in two places") is that ZK holds the metadata but brokers cache it locally and must be pushed updates — on a large cluster with many partitions, this push fan-out is what made controller failover slow (a new controller has to read the *entire* metadata tree from ZK and then push full state to every broker before the cluster is consistent again).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ZOOKEEPER COORDINATION                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ZooKeeper ZNode Structure:                                             │
│                                                                          │
│  /kafka                                                                  │
│  ├── /brokers                                                           │
│  │   ├── /ids                                                           │
│  │   │   ├── /0  {"host":"broker0","port":9092,...}                    │
│  │   │   ├── /1  {"host":"broker1","port":9092,...}                    │
│  │   │   └── /2  {"host":"broker2","port":9092,...}                    │
│  │   ├── /topics                                                        │
│  │   │   ├── /orders                                                    │
│  │   │   │   └── /partitions                                           │
│  │   │   │       ├── /0  {"leader":0,"isr":[0,1,2]}                    │
│  │   │   │       └── /1  {"leader":1,"isr":[1,2,0]}                    │
│  │   │   └── /users                                                     │
│  │   └── /seqid                                                         │
│  ├── /controller  {"brokerid":0}  (current controller)                  │
│  ├── /controller_epoch  5                                               │
│  ├── /admin                                                             │
│  │   ├── /delete_topics                                                 │
│  │   └── /reassign_partitions                                           │
│  └── /config                                                            │
│      ├── /topics                                                        │
│      ├── /brokers                                                       │
│      └── /clients                                                       │
│                                                                          │
│  Controller Election:                                                    │
│  ────────────────────                                                   │
│  • Brokers race to create /controller ephemeral node                    │
│  • Winner becomes controller                                            │
│  • ZK session expiry triggers re-election                               │
│                                                                          │
│  Problems with ZooKeeper:                                                │
│  ─────────────────────────                                              │
│  • Separate system to operate                                           │
│  • Metadata in two places (ZK + brokers)                                │
│  • Controller failover is slow                                          │
│  • Scalability limits (~200K partitions)                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.2 KRaft Mode (New)

KRaft replaces ZooKeeper's external, general-purpose coordination service with a purpose-built Raft implementation (see [Distributed Consensus](@/notes/distributed/distributed_consensus.md) for the Raft algorithm itself — leader election via randomized timeouts, log replication via majority-quorum acknowledgment, and the term/epoch mechanism that prevents split-brain). The key design move is treating cluster metadata *as a Kafka-style replicated log* — the `__cluster_metadata` topic is itself Raft-replicated among controller nodes, so KRaft reuses Kafka's own log/segment/replication machinery (§2-§4) for its control plane instead of depending on an entirely separate system with its own operational model, quorum size, and failure modes. Concretely: the controller quorum elects a leader via Raft's term-based voting exactly as described in the consensus doc, that leader is the only one who appends new metadata records, and followers replicate the metadata log the same way partition followers replicate data (§4.2) — fetch-based, with a High-Watermark-like commit index. Brokers (which may or may not also be controllers) then consume this log to build their local metadata cache, rather than having metadata pushed to them by an external ZK watch mechanism. Because leader failover is now "elect a new Raft leader over a log that's already locally replicated" instead of "reconstruct entire cluster state from ZK and push it out," failover drops from ZK's tens-of-seconds-to-minutes to single-digit seconds.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    KRAFT CONSENSUS                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Architecture:                                                           │
│  ─────────────                                                          │
│  • No ZooKeeper dependency                                              │
│  • Metadata stored in internal __cluster_metadata topic                 │
│  • Raft-based consensus for controller election                         │
│  • Controllers can be dedicated or combined with brokers                │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                     CONTROLLER QUORUM                            │   │
│  │                                                                   │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │   │
│  │  │ Controller  │  │ Controller  │  │ Controller  │             │   │
│  │  │   (Leader)  │  │ (Follower)  │  │ (Follower)  │             │   │
│  │  │   node 0    │  │   node 1    │  │   node 2    │             │   │
│  │  │             │  │             │  │             │             │   │
│  │  │ __cluster_  │  │ __cluster_  │  │ __cluster_  │             │   │
│  │  │  metadata   │  │  metadata   │  │  metadata   │             │   │
│  │  │  (leader)   │  │  (replica)  │  │  (replica)  │             │   │
│  │  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │   │
│  │         │                │                │                     │   │
│  │         └────────────────┼────────────────┘                     │   │
│  │                          │                                       │   │
│  │                    Raft Consensus                                │   │
│  │                          │                                       │   │
│  └──────────────────────────┼──────────────────────────────────────┘   │
│                             │                                           │
│           ┌─────────────────┴─────────────────┐                        │
│           │          Metadata Updates         │                        │
│           ▼                   ▼               ▼                        │
│     ┌──────────┐       ┌──────────┐    ┌──────────┐                   │
│     │ Broker 0 │       │ Broker 1 │    │ Broker 2 │                   │
│     │          │       │          │    │          │                   │
│     │ Metadata │       │ Metadata │    │ Metadata │                   │
│     │  Cache   │       │  Cache   │    │  Cache   │                   │
│     └──────────┘       └──────────┘    └──────────┘                   │
│                                                                          │
│  Benefits:                                                               │
│  • Simpler operations (no ZK)                                           │
│  • Faster controller failover (~seconds vs ~minutes)                    │
│  • Millions of partitions supported                                     │
│  • Single source of truth for metadata                                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.3 Metadata Records

These records are the actual payload of the `__cluster_metadata` log — every cluster-state change (a broker registering, a partition's leader changing, a config update) becomes one record appended to this Raft-replicated log, and a broker's in-memory metadata cache is just the result of replaying every record from the beginning (or from the latest snapshot). This event-sourced design is why snapshots exist: replaying millions of individual records on every broker restart would be slow, so the controller periodically compacts the full state into a snapshot, after which brokers only need the snapshot plus whatever log records came after it — the same segment-plus-incremental-index philosophy applied to metadata rather than message data.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    METADATA RECORD TYPES                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  __cluster_metadata topic contains:                                      │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ RegisterBrokerRecord                                             │   │
│  │ {                                                                │   │
│  │   brokerId: 0,                                                   │   │
│  │   incarnationId: uuid,                                           │   │
│  │   endpoints: [{host, port, securityProtocol}],                   │   │
│  │   rack: "us-east-1a"                                             │   │
│  │ }                                                                │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ TopicRecord                                                      │   │
│  │ { topicId: uuid, name: "orders" }                                │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ PartitionRecord                                                  │   │
│  │ {                                                                │   │
│  │   topicId: uuid,                                                 │   │
│  │   partitionId: 0,                                                │   │
│  │   replicas: [0, 1, 2],                                           │   │
│  │   isr: [0, 1, 2],                                                │   │
│  │   leader: 0,                                                     │   │
│  │   leaderEpoch: 5                                                 │   │
│  │ }                                                                │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ PartitionChangeRecord                                            │   │
│  │ { topicId: uuid, partitionId: 0, leader: 1, isr: [1, 2] }       │   │
│  ├─────────────────────────────────────────────────────────────────┤   │
│  │ ConfigRecord                                                     │   │
│  │ { resourceType: TOPIC, resourceName: "orders",                  │   │
│  │   name: "retention.ms", value: "604800000" }                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Snapshot: Periodic checkpoint of full metadata state                   │
│  Log: Incremental changes since last snapshot                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Performance Optimizations

Kafka's throughput comes less from any single clever algorithm and more from refusing to do unnecessary work at every layer — avoiding copies, avoiding user-space buffering, avoiding small requests. The three techniques below compound: each independently removes overhead, and together they're why a Kafka broker can saturate network bandwidth serving reads while barely touching CPU.

### 9.1 Zero-Copy Transfer

Serving a consumer fetch is fundamentally "take bytes already sitting in the OS page cache and put them on a socket" — the naive path does that via `read()` into a user-space buffer then `write()` back out, which copies the same bytes twice more than necessary and crosses the kernel/user boundary twice. `sendfile()`/`transferTo()` lets the kernel DMA data straight from page cache to the NIC without ever staging it in the broker's (JVM) address space, cutting both copies and context switches. The catch: **this optimization is unavailable when TLS/SSL is enabled.** Encryption has to happen in user space (the kernel can't encrypt on the fly during a `sendfile()` transfer), which forces data back through the traditional read/encrypt/write path — a well-known operational gotcha where enabling inter-broker or client-broker encryption measurably increases broker CPU usage for read-heavy workloads, independent of the encryption algorithm's own cost.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ZERO-COPY (sendfile)                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Traditional Copy (4 copies, 4 context switches):                       │
│  ─────────────────────────────────────────────────                      │
│                                                                          │
│  ┌──────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────┐      │
│  │ Disk │───►│ Kernel Buffer│───►│ User Buffer  │───►│ Socket   │      │
│  └──────┘    └──────────────┘    └──────────────┘    │ Buffer   │      │
│     1. read()   (copy 1)            (copy 2)         └────┬─────┘      │
│     ◄─────►                                               │            │
│   ctx switch                                              │            │
│                           2. write()                      ▼            │
│                           ◄──────►                   ┌──────────┐      │
│                         ctx switch                   │ Network  │      │
│                                       (copy 3)       │ Buffer   │      │
│                                                      └────┬─────┘      │
│                                                           │ (copy 4)  │
│                                                           ▼            │
│                                                      ┌──────────┐      │
│                                                      │   NIC    │      │
│                                                      └──────────┘      │
│                                                                          │
│  Zero-Copy with sendfile() (0 CPU copies, 2 context switches):          │
│  ─────────────────────────────────────────────────────────────          │
│                                                                          │
│  ┌──────┐    ┌──────────────┐                        ┌──────────┐      │
│  │ Disk │───►│ Kernel Buffer│───────────────────────►│   NIC    │      │
│  └──────┘    └──────────────┘                        └──────────┘      │
│                    │                                       ▲            │
│                    │         DMA copy (no CPU)            │            │
│                    └───────────────────────────────────────┘            │
│                                                                          │
│  sendfile() syscall:                                                    │
│  • Direct transfer from page cache to network                           │
│  • No user-space copies                                                 │
│  • Kafka uses Java's FileChannel.transferTo()                           │
│                                                                          │
│  Impact: ~60% reduction in CPU usage for serving reads                  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Page Cache Utilization

Most storage engines build their own in-process cache (a buffer pool) to avoid re-reading disk — Kafka deliberately does not, and instead keeps its JVM heap small and lets the OS page cache do that job. This works because Kafka's access pattern is exactly what the OS cache is already good at: sequential writes and mostly-recent reads (consumers reading near the tail), so the kernel's LRU-ish eviction naturally keeps hot data resident without Kafka having to implement its own cache-replacement policy. Two concrete wins from this choice: no GC pauses caused by a giant heap full of cached message bytes (a small heap means short, predictable GC pauses), and the cache **survives broker restarts** — a JVM-level cache would be cold on every restart, but page cache is a kernel structure that persists across the Kafka process (though not the machine) restarting, so a rolling restart doesn't spike disk I/O.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PAGE CACHE STRATEGY                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Kafka leverages OS page cache instead of managing its own:             │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                        MEMORY                                    │   │
│  │                                                                   │   │
│  │  ┌─────────────────────────────────────────────────────────┐    │   │
│  │  │                    JVM HEAP                              │    │   │
│  │  │  • Minimal (~6GB typically)                             │    │   │
│  │  │  • Metadata, network buffers                            │    │   │
│  │  │  • NOT for message storage                              │    │   │
│  │  └─────────────────────────────────────────────────────────┘    │   │
│  │                                                                   │   │
│  │  ┌─────────────────────────────────────────────────────────┐    │   │
│  │  │                 OS PAGE CACHE                            │    │   │
│  │  │  • Majority of RAM                                       │    │   │
│  │  │  • Caches segment files                                  │    │   │
│  │  │  • OS manages eviction                                   │    │   │
│  │  │                                                          │    │   │
│  │  │  ┌──────────────────────────────────────────────────┐   │    │   │
│  │  │  │ topic-0/0000000000.log  [████████████████████░░] │   │    │   │
│  │  │  │ topic-0/0000100000.log  [████████████░░░░░░░░░░] │   │    │   │
│  │  │  │ topic-1/0000000000.log  [██████░░░░░░░░░░░░░░░░] │   │    │   │
│  │  │  │ (most recently written/read pages stay hot)       │   │    │   │
│  │  │  └──────────────────────────────────────────────────┘   │    │   │
│  │  └─────────────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Benefits:                                                               │
│  • No GC pressure from message data                                     │
│  • Warm cache survives broker restart                                   │
│  • OS optimizes cache based on access patterns                          │
│  • Sequential writes = predictable caching                              │
│                                                                          │
│  Read Patterns:                                                          │
│  • Tail reads (current): Almost always from page cache                  │
│  • Historical reads: May hit disk                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.3 Batching Everywhere

Batching at one layer only helps if the layers above and below don't undo it — a producer that batches nicely but talks to a broker that fsyncs per-record, or a broker that batches writes but feeds a consumer that fetches one message at a time, only gets a fraction of the possible win. Kafka batches at every hop specifically so the "unit of work" stays large end to end: producer batches become the unit written by the broker, become the unit fetched by followers, become the unit fetched by consumers — the same record batch format (§2.3) flows through mostly unchanged, which also means no re-serialization at each hop. The latency/throughput knobs at the bottom of this section aren't independent settings — they're the same trade-off (bigger batches, more waiting, less overhead per byte) applied consistently across producer, broker, and consumer configuration simultaneously.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BATCHING AT EVERY LAYER                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. Producer Batching                                                   │
│  ─────────────────────                                                  │
│  • Records accumulated in memory (batch.size, linger.ms)                │
│  • Compression applied per batch                                        │
│  • Single network request per batch                                     │
│                                                                          │
│  2. Broker Write Batching                                               │
│  ──────────────────────────                                             │
│  • Multiple batches written in single append                            │
│  • fsync batched (linger.ms at broker level)                            │
│                                                                          │
│  3. Replication Batching                                                │
│  ───────────────────────                                                │
│  • Followers fetch multiple batches per request                         │
│  • replica.fetch.max.bytes controls fetch size                          │
│                                                                          │
│  4. Consumer Fetch Batching                                             │
│  ───────────────────────────                                            │
│  • fetch.min.bytes, fetch.max.wait.ms                                   │
│  • Returns multiple batches per request                                 │
│                                                                          │
│  Latency vs Throughput Tradeoff:                                        │
│  ────────────────────────────────                                       │
│                                                                          │
│  Low Latency Config:          High Throughput Config:                   │
│  linger.ms=0                  linger.ms=100                             │
│  batch.size=16KB              batch.size=1MB                            │
│  acks=1                       acks=all                                  │
│  compression=none             compression=lz4                           │
│                                                                          │
│  Latency: ~2ms                Latency: ~100ms                           │
│  Throughput: ~50K msg/s       Throughput: ~500K msg/s                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Guarantees Deep Dive

Everything in §4-§7 is mechanism; this section is the payoff — how those mechanisms compose into the guarantees an application actually depends on, and which config knobs to turn to get the guarantee you need.

### 10.1 Durability Guarantees

`min.insync.replicas` is the piece that turns `acks=all` from "wait for the ISR, whatever size it happens to be" into an actual floor: without it, if the ISR shrinks to just the leader (every follower has fallen behind), `acks=all` degrades to `acks=1`'s guarantee while still charging `acks=all`'s latency — a false sense of safety. Setting `min.insync.replicas=2` on a replication-factor-3 topic makes that degradation impossible: the leader refuses to accept writes at all (`NotEnoughReplicasException`) rather than silently accepting them with weaker durability than the caller expects. This is the standard production recommendation — RF=3, `min.insync.replicas=2`, `acks=all` — because it tolerates one broker failure with zero durability loss and zero silent guarantee downgrade.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    DURABILITY GUARANTEES                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Configuration Combinations:                                             │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐│
│  │ acks  │ min.insync │ Durability  │ Survives           │ Latency   ││
│  │       │ .replicas  │             │                    │           ││
│  ├───────┼────────────┼─────────────┼────────────────────┼───────────┤│
│  │   0   │    N/A     │ None        │ Nothing            │ Lowest    ││
│  │   1   │    N/A     │ Leader only │ Follower failure   │ Low       ││
│  │  all  │     1      │ Leader only │ Follower failure   │ Medium    ││
│  │  all  │     2      │ Strong      │ 1 broker failure   │ High      ││
│  │  all  │     3      │ Strongest   │ 2 broker failures  │ Highest   ││
│  └────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  acks=all + min.insync.replicas=2 (recommended for durability):        │
│                                                                          │
│  Scenario: 3 replicas, min.insync.replicas=2                           │
│                                                                          │
│  Normal Operation:                                                       │
│  ┌────────┐  ┌────────┐  ┌────────┐                                    │
│  │Leader  │  │Follower│  │Follower│  ISR = {0, 1, 2}                   │
│  │   0    │  │   1    │  │   2    │  Produce succeeds                  │
│  └────────┘  └────────┘  └────────┘                                    │
│                                                                          │
│  One Follower Down:                                                     │
│  ┌────────┐  ┌────────┐  ┌────────┐                                    │
│  │Leader  │  │Follower│  │  DOWN  │  ISR = {0, 1}                      │
│  │   0    │  │   1    │  │   ✗    │  Produce succeeds (ISR ≥ 2)       │
│  └────────┘  └────────┘  └────────┘                                    │
│                                                                          │
│  Two Followers Down:                                                    │
│  ┌────────┐  ┌────────┐  ┌────────┐                                    │
│  │Leader  │  │  DOWN  │  │  DOWN  │  ISR = {0}                         │
│  │   0    │  │   ✗    │  │   ✗    │  Produce FAILS (ISR < 2)          │
│  └────────┘  └────────┘  └────────┘  NotEnoughReplicasException        │
│                                                                          │
│  This ensures: Acknowledged data exists on ≥2 brokers                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Ordering Guarantees

Total order within a partition falls directly out of the log being a single append-only sequence with one writer (the leader) at a time — there's no concurrency to reorder, so order is trivially preserved. The lack of cross-partition order isn't a limitation Kafka failed to solve, it's the price of partition-level parallelism: if you need global order, you need one partition, which caps throughput at what a single partition (and single consumer) can handle. Idempotence's ordering guarantee is a narrower, specific fix — it only protects against the producer's *own* retries reordering its *own* batches; it does nothing for ordering across producers or across partitions, which is why "idempotent" and "ordered" are easy to conflate but aren't the same guarantee.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ORDERING GUARANTEES                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Within a Partition: TOTAL ORDER                                        │
│  ───────────────────────────────                                        │
│                                                                          │
│  Producer sends: A, B, C, D                                             │
│  Partition stores: [A][B][C][D]                                         │
│  Consumer receives: A, B, C, D  ✓ (guaranteed)                          │
│                                                                          │
│  Across Partitions: NO ORDER GUARANTEE                                  │
│  ─────────────────────────────────────                                  │
│                                                                          │
│  Producer sends to P0: A, C                                             │
│  Producer sends to P1: B, D                                             │
│                                                                          │
│  Consumer might receive: A, B, C, D  or  B, A, D, C  or  A, B, D, C    │
│  (order between partitions is non-deterministic)                        │
│                                                                          │
│  Idempotent Producer Ordering:                                          │
│  ─────────────────────────────                                          │
│                                                                          │
│  Without idempotence (max.in.flight > 1):                               │
│  Batch 1 ─────► [network fail, retry]                                   │
│  Batch 2 ─────► [succeeds first]                                        │
│                                                                          │
│  Result: Batch 2 before Batch 1 (OUT OF ORDER!)                         │
│                                                                          │
│  With idempotence:                                                      │
│  • Broker tracks sequence numbers                                       │
│  • Out-of-order batches are rejected                                    │
│  • Producer retries maintain order                                      │
│                                                                          │
│  Transactional Ordering:                                                │
│  ───────────────────────                                                │
│  • Atomic writes to multiple partitions                                 │
│  • All-or-nothing visibility                                            │
│  • But no cross-partition ordering guarantee                            │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.3 Delivery Semantics Summary

These three semantics aren't three different features — they're the same producer/consumer machinery with different pieces enabled, layered strictly: at-most-once needs nothing extra (it's what you get by doing the minimum), at-least-once needs retries plus commit-after-process ordering on the consumer side, exactly-once needs idempotence *and* transactions *and* `read_committed` all three engaged together — dropping any one of the three degrades you back to at-least-once with duplicates possible. In practice: pick at-most-once only for genuinely disposable data (metrics samples where a gap is cheaper than the overhead of guaranteeing delivery); default to at-least-once with an idempotent consumer (e.g. upserts keyed by a natural ID) wherever possible, since it's cheaper than transactions and idempotent processing logic absorbs the occasional duplicate for free; reserve full exactly-once for cases where the *processing itself* isn't naturally idempotent (financial ledgers, counters, anything where reapplying an operation changes the result) — that's the only case where the transactional latency overhead (§7.2) is actually buying you something a simpler design couldn't.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    DELIVERY SEMANTICS                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  AT-MOST-ONCE:                                                          │
│  ─────────────                                                          │
│  Configuration:                                                          │
│  • acks=0 or acks=1 with no retries                                     │
│  • Consumer: commit before processing                                   │
│                                                                          │
│  Behavior:                                                               │
│  • Message may be lost                                                  │
│  • Message never duplicated                                             │
│  • Use case: Metrics where loss is acceptable                           │
│                                                                          │
│  AT-LEAST-ONCE (Default):                                               │
│  ─────────────────────────                                              │
│  Configuration:                                                          │
│  • acks=all, retries=MAX_INT                                            │
│  • Consumer: commit after processing                                    │
│                                                                          │
│  Behavior:                                                               │
│  • Message never lost (if acked)                                        │
│  • Message may be duplicated                                            │
│  • Use case: Most applications with idempotent consumers                │
│                                                                          │
│  EXACTLY-ONCE:                                                          │
│  ─────────────                                                          │
│  Configuration:                                                          │
│  • enable.idempotence=true                                              │
│  • transactional.id set                                                 │
│  • Consumer: isolation.level=read_committed                             │
│  • Atomic offset commit with sendOffsetsToTransaction()                 │
│                                                                          │
│  Behavior:                                                               │
│  • Message delivered exactly once                                       │
│  • Requires transactional producer + consumer                           │
│  • Higher latency                                                       │
│  • Use case: Financial transactions, stateful processing                │
│                                                                          │
│  Summary Matrix:                                                         │
│  ┌─────────────────┬─────────────┬─────────────┬──────────────────────┐│
│  │ Semantic        │ Lost?       │ Duplicated? │ Configuration        ││
│  ├─────────────────┼─────────────┼─────────────┼──────────────────────┤│
│  │ At-most-once    │ Possible    │ No          │ acks=0, no retry     ││
│  │ At-least-once   │ No          │ Possible    │ acks=all, retry      ││
│  │ Exactly-once    │ No          │ No          │ Transactions + EOS   ││
│  └─────────────────┴─────────────┴─────────────┴──────────────────────┘│
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 11. Source Code References

### Key Files in Apache Kafka

| Component | Location | What's actually there |
|-----------|----------|------------------------|
| Log (partition) | `core/src/main/scala/kafka/log/Log.scala` | Segment list management, append/read path, retention/compaction triggering — the top-level object representing one partition's on-disk log |
| Log Segment | `core/src/main/scala/kafka/log/LogSegment.scala` | Single segment's `.log`/`.index`/`.timeindex` file handles, offset lookup (§3.3), segment roll logic |
| Record Batch | `clients/src/main/java/org/apache/kafka/common/record/` | Record batch v2 encoding/decoding (§2.3), compression codec dispatch, CRC validation |
| Producer | `clients/src/main/java/org/apache/kafka/clients/producer/` | `KafkaProducer`, `RecordAccumulator` (batching, §5.1), `Sender` thread, partitioner implementations |
| Consumer | `clients/src/main/java/org/apache/kafka/clients/consumer/` | `KafkaConsumer`, fetch session management, offset commit logic (§6.3) |
| Replication | `core/src/main/scala/kafka/server/ReplicaManager.scala` | ISR tracking, HW computation, fetch request handling for both followers and consumers (§4.1-4.2) |
| Controller | `core/src/main/scala/kafka/controller/KafkaController.scala` | Legacy ZK-based controller: leader election orchestration, partition state machine (§8.1) |
| KRaft | `raft/src/main/java/org/apache/kafka/raft/` | The Raft implementation itself — leader election, log replication for `__cluster_metadata` (§8.2) |
| Transactions | `core/src/main/scala/kafka/coordinator/transaction/` | `TransactionCoordinator`, transaction state machine, marker writing (§7.2) |
| Group Coordinator | `core/src/main/scala/kafka/coordinator/group/` | JoinGroup/SyncGroup handling, rebalance protocol including cooperative rebalancing (§6.2) |

---

## 12. References

### Official Documentation
- [Apache Kafka Documentation](https://kafka.apache.org/documentation/)
- [Kafka Improvement Proposals (KIPs)](https://cwiki.apache.org/confluence/display/KAFKA/Kafka+Improvement+Proposals)

### Key KIPs
- KIP-98: Exactly Once Delivery and Transactional Messaging — introduced the transaction coordinator, idempotent producer, and `READ_COMMITTED` isolation level covered in §7
- KIP-101: Alter Replication Protocol to Use Leader Epoch rather than High Watermark for Truncation — the leader epoch fencing mechanism in §4.4, replacing an earlier HW-based truncation scheme that could silently diverge replicas
- KIP-392: Allow Consumers to Fetch from Closest Replica — rack-aware fetching, referenced in §4.2's note on follower reads
- KIP-429: Kafka Consumer Incremental Rebalance Protocol — the cooperative rebalancing behavior described in §6.2, now the default (`CooperativeStickyAssignor`)
- KIP-500 / KIP-631: Replace ZooKeeper with a Self-Managed Metadata Quorum / The Quorum-based Kafka Controller — together define KRaft (§8.2), moving cluster metadata into a Raft-replicated internal topic

### Papers
- Kreps, Narkhede, Rao, "Kafka: a Distributed Messaging System for Log Processing" (NetDB, 2011) — the original design paper: log-structured storage, consumer-pull model, and the throughput rationale behind §2.2
- Goodhope et al., "Building LinkedIn's Real-time Activity Data Pipeline" (IEEE Data Engineering Bulletin, 2012) — the production case study that motivated Kafka's design, covering operational lessons at LinkedIn's original multi-datacenter scale
- Ongaro, Ousterhout, "In Search of an Understandable Consensus Algorithm (Raft)" (USENIX ATC, 2014) — the consensus algorithm KRaft's controller quorum implements (§8.2); full treatment in [Distributed Consensus](@/notes/distributed/distributed_consensus.md)
- Kleppmann, "Designing Data-Intensive Applications" (O'Reilly, 2017), ch. 11 — situates Kafka's log-as-source-of-truth design within the broader stream-processing and event-sourcing literature; useful background for why §3.5's compaction models "topic as changelog"

### Engineering Writeups
- Confluent Engineering Blog, "Exactly-once Semantics are Possible: Here's How Kafka Does it" — implementation-level walkthrough of the transaction protocol in §7.2, from the KIP-98 authors
- LinkedIn Engineering Blog, "Kafka Ninja: Reducing Latency by 90%" and related posts on `linger.ms`/batching tuning — production tuning experience behind the throughput/latency trade-offs in §5.2 and §9.3
- Confluent, "Zero Copy: The Secret to Kafka's High Performance" — practitioner write-up on §9.1's zero-copy mechanism, including the SSL incompatibility caveat

### Source Code
- GitHub: `https://github.com/apache/kafka`

---

## See Also

- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — KRaft replaces ZooKeeper with Raft-based metadata quorum
- [RabbitMQ Internals](@/notes/distributed/rabbitmq_internals.md) — Alternative messaging system with different durability and routing trade-offs
- [WAL-Based Incremental Conversion](@/notes/database/wal_incremental_conversion.md) — CDC pipelines that feed Kafka topics from database WAL
- [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) — Testing approaches for verifying exactly-once semantics and replication correctness

---

*Document created: 2026-02-05*
*Covers: Apache Kafka 3.x architecture*
