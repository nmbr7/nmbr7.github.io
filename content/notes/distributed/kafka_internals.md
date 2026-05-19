+++
title = "Kafka Internals"
date = 2026-02-10
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

### 1.1 Core Components

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

Kafka's core abstraction is an **append-only commit log**:

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

### 3.1 Partition Directory Structure

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

### 4.1 ISR (In-Sync Replicas)

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

### 6.3 Offset Management

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

### 8.1 ZooKeeper Mode (Legacy)

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

### 9.1 Zero-Copy Transfer

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

### 10.1 Durability Guarantees

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

| Component | Location |
|-----------|----------|
| Log (partition) | `core/src/main/scala/kafka/log/Log.scala` |
| Log Segment | `core/src/main/scala/kafka/log/LogSegment.scala` |
| Record Batch | `clients/src/main/java/org/apache/kafka/common/record/` |
| Producer | `clients/src/main/java/org/apache/kafka/clients/producer/` |
| Consumer | `clients/src/main/java/org/apache/kafka/clients/consumer/` |
| Replication | `core/src/main/scala/kafka/server/ReplicaManager.scala` |
| Controller | `core/src/main/scala/kafka/controller/KafkaController.scala` |
| KRaft | `raft/src/main/java/org/apache/kafka/raft/` |
| Transactions | `core/src/main/scala/kafka/coordinator/transaction/` |
| Group Coordinator | `core/src/main/scala/kafka/coordinator/group/` |

---

## 12. References

### Official Documentation
- [Apache Kafka Documentation](https://kafka.apache.org/documentation/)
- [Kafka Improvement Proposals (KIPs)](https://cwiki.apache.org/confluence/display/KAFKA/Kafka+Improvement+Proposals)

### Key KIPs
- KIP-98: Exactly Once Delivery and Transactional Messaging
- KIP-392: Allow Consumers to Fetch from Closest Replica
- KIP-500: Replace ZooKeeper with Self-Managed Metadata Quorum
- KIP-631: The Quorum-based Kafka Controller

### Papers
- "Kafka: a Distributed Messaging System for Log Processing" (LinkedIn, 2011)
- "Building LinkedIn's Real-time Activity Data Pipeline" (IEEE, 2012)

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
