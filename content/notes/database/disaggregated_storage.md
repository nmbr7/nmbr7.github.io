+++
title = "Disaggregated Storage"
date = 2026-02-11
[taxonomies]
tags = ["disaggregated-storage", "aurora", "neon", "rdma", "cloud-native", "snowflake", "compute-storage-separation"]
+++


# Disaggregated Storage Systems

A comprehensive reference on disaggregated storage architectures in modern database systems: separating compute from storage to enable elastic scaling, cost efficiency, and operational simplicity in the cloud.

---

## 1. Foundations

### 1.1 What Disaggregation Means

Disaggregated storage separates a database into **independently scalable tiers** — typically compute and storage — connected by a network instead of a local bus. Compute nodes become stateless (or nearly so), and durable state lives in a shared, elastic storage service.

```
Traditional (Monolithic):          Disaggregated:

  ┌──────────────────┐              ┌──────────┐  ┌──────────┐
  │     Compute      │              │ Compute  │  │ Compute  │  ... (elastic)
  │  ┌────────────┐  │              │ (stateless│  │ (stateless│
  │  │ Buffer Pool│  │              │  + cache) │  │  + cache) │
  │  ├────────────┤  │              └─────┬─────┘  └─────┬─────┘
  │  │   WAL      │  │                    │              │
  │  ├────────────┤  │                    └──────┬───────┘
  │  │ Data Files │  │                           │ Network
  │  └────────────┘  │              ┌────────────┴────────────┐
  │   Local Disk     │              │   Shared Storage Layer   │
  └──────────────────┘              │ (elastic, durable, shared)│
                                    └──────────────────────────┘
```

The key insight: in cloud environments, **storage and compute have different scaling curves**. A read-heavy analytics query needs more CPU but the same storage. A data archival workload needs more storage but minimal CPU. Coupling them wastes resources.

### 1.2 Historical Evolution

```
Era         Architecture        Example              Characteristic
──────────  ──────────────────  ────────────────────  ─────────────────────
1980s       Shared-Nothing      Teradata, Gamma      Each node owns its data
                                                     partition. Scale by
                                                     adding nodes.

1990s       Shared-Disk         Oracle RAC            All nodes access shared
                                                     SAN/NAS. Complex cache
                                                     coherence (Global Lock
                                                     Manager).

2000s       Shared-Nothing      Google Bigtable,      Scale-out for web.
            (2nd gen)           Cassandra, HBase      Partition + replicate.

2010s       Disaggregated       Aurora (2015),        Separate compute from
                                Snowflake (2014),     storage. Network is the
                                Socrates (2019)       new bus.

2020s       Multi-tier          Aurora DSQL,          Disaggregate further:
            Disaggregation      GaussDB, Neon,        compute, log, memory,
                                PolarDB-MP            and storage as separate
                                                     tiers.
```

### 1.3 Three Architecture Models

```
┌─────────────────────────────────────────────────────────────────┐
│                    Shared-Nothing                                │
│                                                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                        │
│  │Node 1   │  │Node 2   │  │Node 3   │  Each node owns         │
│  │Compute  │  │Compute  │  │Compute  │  its data partition.     │
│  │Storage  │  │Storage  │  │Storage  │  No shared state.        │
│  │(part 1) │  │(part 2) │  │(part 3) │  Resharding = pain.      │
│  └─────────┘  └─────────┘  └─────────┘                        │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     Shared-Disk                                  │
│                                                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                        │
│  │Compute 1│  │Compute 2│  │Compute 3│  All nodes see           │
│  └────┬────┘  └────┬────┘  └────┬────┘  entire dataset.        │
│       └────────────┼────────────┘       Global lock manager.    │
│            ┌───────┴───────┐            Cache coherence is       │
│            │  Shared SAN   │            complex.                 │
│            └───────────────┘                                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                   Disaggregated                                  │
│                                                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                        │
│  │Compute 1│  │Compute 2│  │Compute 3│  Compute scales          │
│  │(+cache) │  │(+cache) │  │(+cache) │  independently.          │
│  └────┬────┘  └────┬────┘  └────┬────┘  Storage scales          │
│       └────────────┼────────────┘       independently.          │
│            ┌───────┴───────┐            "Smart" storage does     │
│            │ Smart Storage │            more than just store.    │
│            │  (log apply,  │                                    │
│            │  page serve)  │                                    │
│            └───────────────┘                                    │
└─────────────────────────────────────────────────────────────────┘
```

**Key difference from shared-disk:** in disaggregated systems, the storage layer is "smart" — it understands database semantics (log records, pages, transactions) rather than just providing raw block I/O. This is what enables Aurora's "the log is the database."

### 1.4 Why Cloud Economics Drove Disaggregation

| Factor | Monolithic | Disaggregated |
|--------|-----------|---------------|
| Scale compute | Must scale storage too | Scale compute alone |
| Scale storage | Must scale compute too | Scale storage alone |
| Idle cost | Pay for provisioned resources | Pay per use (serverless) |
| Failure blast radius | Node failure = compute + data loss | Compute failure = restart elsewhere |
| Replication | Full-node replication (wasteful) | Storage-level replication (efficient) |
| Read replicas | Full copy + replay lag | Share storage, near-zero lag |
| Provisioning time | Minutes (allocate disk) | Seconds (attach to storage) |

---

## 2. Disaggregated OLTP Architectures

### 2.1 Taxonomy

The transactional.blog taxonomy (based on analysis of Aurora, Socrates, Taurus, PolarDB, Neon) identifies three architectural patterns for disaggregated OLTP:

```
Pattern 1: Partitioned WAL (Aurora)
  - WAL distributed across storage node quorums
  - Each storage partition independently applies log
  - Complex LSN tracking (VCL, CPL, SCL)

Pattern 2: Centralized Log Service (Socrates, Neon)
  - Dedicated log service receives and durably stores WAL
  - Log forwarded to page servers for page materialization
  - Cleaner separation of durability from page serving

Pattern 3: Distributed Filesystem (PolarDB/PolarFS)
  - POSIX-like filesystem over RDMA network
  - Minimal database modification — storage looks "local"
  - Trades software complexity for hardware investment
```

---

## 3. Amazon Aurora

### 3.1 Architecture Overview

Aurora (Verbitski et al., SIGMOD 2017) is the canonical disaggregated OLTP database. Its fundamental insight: **the log is the database**.

```
                        ┌──────────────┐
                        │   Primary    │
                        │  (MySQL/PG)  │
                        └──────┬───────┘
                               │ Redo log records only
                               │ (no dirty pages shipped)
            ┌──────────────────┼──────────────────┐
            │                  │                   │
       ┌────┴────┐       ┌────┴────┐        ┌────┴────┐
       │  AZ 1   │       │  AZ 2   │        │  AZ 3   │
       │┌───┐┌───┐│      │┌───┐┌───┐│       │┌───┐┌───┐│
       ││ S ││ S ││      ││ S ││ S ││       ││ S ││ S ││
       │└───┘└───┘│      │└───┘└───┘│       │└───┘└───┘│
       └──────────┘      └──────────┘       └──────────┘
           6 copies across 3 AZs
           Write quorum: 4/6
           Read quorum: 3/6
```

### 3.2 Key Design Decisions

**Only ship redo logs, not pages.** Traditional MySQL/PostgreSQL ships dirty pages, double-write buffers, and checkpoint data to disk. Aurora ships only the WAL redo log records to storage. The storage layer applies redo to materialize pages.

**Write amplification comparison:**

```
Traditional MySQL (mirrored):
  Redo log      ──→ EBS (write 1)
  Binlog        ──→ EBS (write 2)
  Data pages    ──→ EBS (write 3)
  Double-write  ──→ EBS (write 4)
  FRM metadata  ──→ EBS (write 5)
  × 2 (mirror)  = ~10 writes per mutation

Aurora:
  Redo log      ──→ Storage (write 1, 6 copies)
  Storage applies redo to pages internally
  = 1 logical write (6 physical for durability)
```

The network I/O reduction is dramatic: Aurora sends ~6× fewer bytes across the network than a mirrored MySQL deployment.

**Protection Groups (PGs):** The database volume is partitioned into 10GB segments. Each segment is replicated 6 ways into a **Protection Group** spread across 3 AZs (2 copies per AZ).

### 3.3 Quorum Model

```
Quorum parameters:
  V = 6   (total copies)
  Vw = 4  (write quorum)
  Vr = 3  (read quorum)

  Vw + Vr > V  (ensures read sees latest write)
  Vw > V/2     (ensures no conflicting writes)

Tolerates:
  - Loss of entire AZ (2 nodes) + 1 more: still read (3 left ≥ Vr)
  - Loss of entire AZ (2 nodes): still write (4 left ≥ Vw)
```

### 3.4 Log Sequence Numbers

Aurora uses a hierarchy of LSNs to track consistency:

```
VCL (Volume Complete LSN):
  Highest LSN for which ALL prior log records have been received
  by ALL storage nodes in the quorum.

CPL (Consistency Point LSN):
  Highest LSN that represents a transaction-consistent boundary
  (mini-transaction completion). Recovery truncates to the highest
  CPL ≤ VCL.

VDL (Volume Durable LSN):
  Highest CPL ≤ VCL. This is the effective recovery point.

SCL (Segment Complete LSN):
  Per-segment version of VCL. Tracks completeness per PG.
```

### 3.5 Crash Recovery

Aurora's recovery is fundamentally different from traditional databases. Traditional PostgreSQL/MySQL replays WAL from the last checkpoint — recovery time depends on checkpoint interval. Aurora's storage nodes **continuously apply redo in the background**, so recovery is just determining the VDL via a quorum read.

```
Traditional recovery:
  1. Find last checkpoint on disk
  2. Replay WAL from checkpoint to end (~minutes for large DBs)
  3. Undo uncommitted transactions

Aurora recovery:
  1. Read SCLs from all storage nodes (quorum read)
  2. Compute VCL from SCLs
  3. Determine VDL = highest CPL ≤ VCL
  4. Truncate any log records above VDL
  5. Done (~10 seconds, regardless of DB size)
```

No redo replay needed — storage already has the pages. Recovery time is constant regardless of database size.

### 3.6 Gossip-Based Repair

When a storage node misses log records (e.g., after a transient failure), Aurora uses a **peer-to-peer gossip protocol** within each Protection Group to fill gaps:

```
Storage node detects gap:
  "I have SCL=150, but peer reports SCL=200"

Gossip repair:
  1. Node identifies missing log records (LSN 151-200)
  2. Requests records from peers in the same PG
  3. Peers send missing records directly (peer-to-peer)
  4. Node applies records and advances its SCL

Benefit: No load on compute for repairs.
         10 GB segment repair in ~10 seconds.
```

This reduces the probability of needing a full segment rebuild, keeping the write quorum available even during partial failures.

### 3.7 Read Replicas

Aurora read replicas share the **same storage layer** — no data copy needed. The primary ships a stream of log records to replicas for cache invalidation. Replica lag is typically <20ms (vs. seconds for traditional MySQL replication).

```
Primary:  buffer pool → apply mutations → ship redo to storage
                                        → ship redo to replicas

Replica:  buffer pool → receive redo → invalidate/update cached pages
                                     → read pages from shared storage
```

### 3.8 Aurora DSQL (2024)

Amazon's latest disaggregated database, announced at re:Invent 2024, GA mid-2025. Fully disaggregated into independent components:

- **Query processor**: parses/plans/executes SQL
- **Adjudicator**: resolves transaction conflicts (OCC)
- **Journal**: durable WAL service
- **Crossbar**: routes reads to storage

Multi-region strong consistency with active-active writes. Claims 4× faster than comparable distributed SQL. Designed for 99.99% (single-region) and 99.999% (multi-region) availability.

---

## 4. Microsoft Socrates (Azure SQL Hyperscale)

### 4.1 Architecture

Socrates (Antonopoulos et al., SIGMOD 2019) separates Azure SQL into **four tiers**:

```
┌───────────────────────────────────────────────────────┐
│ Tier 1: Compute                                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
│  │ Primary  │  │Secondary │  │Secondary │  ...        │
│  │(read/write│  │(read-only│  │(read-only│             │
│  │  +RBPEX) │  │  +RBPEX) │  │  +RBPEX) │             │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘            │
│        │              │              │                  │
├────────┼──────────────┼──────────────┼──────────────────┤
│ Tier 2: Log Service                                     │
│  ┌─────┴──────────────┴──────────────┴─────┐           │
│  │  Landing Zone (Azure Premium Storage)    │           │
│  │  XLOG process (routes WAL)               │           │
│  │  ──→ Page Servers                        │           │
│  │  ──→ Long-term log storage (XStore)      │           │
│  └─────────────────────┬───────────────────┘           │
│                        │                                │
├────────────────────────┼────────────────────────────────┤
│ Tier 3: Page Servers                                    │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐          │
│  │Page Server│  │Page Server│  │Page Server│           │
│  │(partition │  │(partition │  │(partition │           │
│  │ of data)  │  │ of data)  │  │ of data)  │           │
│  │  +RBPEX   │  │  +RBPEX   │  │  +RBPEX   │           │
│  └───────────┘  └───────────┘  └───────────┘           │
│                                                         │
├─────────────────────────────────────────────────────────┤
│ Tier 4: XStore (Azure Blob Storage)                     │
│  Long-term durable storage for log + data snapshots     │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Key Components

**RBPEX (Resilient Buffer Pool Extension):** A local SSD cache on both compute nodes and page servers. Acts as a warm cache that survives process restarts (but not machine failures). Eliminates cold-start penalty after failover. Based on Hekaton (SQL Server's in-memory engine) for recovery.

**Landing Zone:** WAL records land in Azure Premium Storage (low latency, high durability). The XLOG process reads from the landing zone and fans out to page servers and long-term storage.

**Page Servers:** Each page server owns a partition of the database. It applies WAL records to reconstruct pages. When a compute node needs a page not in its buffer pool, it fetches from the page server.

**XStore:** Azure Blob Storage for long-term log and snapshot retention. Cheapest tier, used for disaster recovery and time travel.

### 4.3 Key Differences from Aurora

| Dimension | Aurora | Socrates |
|-----------|--------|----------|
| WAL distribution | Quorum to storage nodes | Landing zone → fan-out |
| Page serving | Storage nodes materialize pages | Dedicated page servers |
| Local cache | Buffer pool only | RBPEX (survives restart) |
| Log durability | Quorum across AZs | Azure Premium Storage |
| Long-term storage | Integrated in storage nodes | Separate XStore tier |

---

## 5. Neon (Serverless Postgres)

### 5.1 Architecture

Neon separates PostgreSQL into stateless compute and durable storage, connected by WAL streaming. Written in Rust (storage layer).

```
┌──────────────────────────────────────────────────────┐
│                  Compute Layer                        │
│  ┌───────────────┐  ┌───────────────┐               │
│  │ PostgreSQL    │  │ PostgreSQL    │  ... (elastic)  │
│  │ (unmodified)  │  │ (read replica)│               │
│  │ + neon_smgr   │  │ + neon_smgr   │               │
│  └───────┬───────┘  └───────┬───────┘               │
│          │ WAL               │ page reads             │
├──────────┼───────────────────┼────────────────────────┤
│          │ Storage Layer     │                        │
│  ┌───────┴───────┐  ┌───────┴──────┐                │
│  │  Safekeepers  │  │  Pageserver  │                │
│  │ (WAL durability│  │ (page mater- │                │
│  │  via Paxos)   │  │  ialization)  │                │
│  └───────┬───────┘  └───────┬──────┘                │
│          │                   │                        │
│  ┌───────┴───────────────────┴──────┐                │
│  │      Object Storage (S3)         │                │
│  │  (immutable page history +       │                │
│  │   WAL archive)                   │                │
│  └──────────────────────────────────┘                │
└──────────────────────────────────────────────────────┘
```

### 5.2 Components

**Safekeepers (WAL durability):**
- Receive WAL from compute via streaming replication protocol
- A transaction commits when a **quorum of safekeepers** acknowledges (Paxos)
- Hold WAL until the pageserver has processed and uploaded to S3
- Provide durability independent of compute lifetime

**Pageserver (page materialization):**
- Consumes WAL from safekeepers
- Reconstructs data pages by replaying WAL on base page images
- Serves page requests from compute (at a specific LSN)
- Periodically creates layer files and uploads to S3
- Never blocks transaction commits — materialization is asynchronous

**Object Storage (S3):**
- Stores immutable page history and WAL archives
- Queries do NOT read directly from S3 — the pageserver caches hot pages
- Provides effectively infinite storage

### 5.3 Read and Write Paths

```
Write path:
  1. Compute applies changes to shared buffers (in memory)
  2. WAL streamed to safekeepers
  3. Commit acknowledged once Paxos quorum reached
  4. Pageserver asynchronously materializes pages
  5. Layer files uploaded to S3

Read path (cache hit):
  1. Compute reads from local buffer pool → done

Read path (cache miss):
  1. Compute requests page at LSN from pageserver (via neon_smgr)
  2. Pageserver checks cache → if miss, reconstruct from
     base image + WAL replay up to requested LSN
  3. Return page to compute
```

### 5.4 Pageserver Storage Internals

The pageserver stores data in **layer files** organized in an LSM-like structure:

```
Layer types:
  ImageLayer:  Snapshot of ALL keys in a range at ONE specific LSN
               (equivalent to a base image / checkpoint)

  DeltaLayer:  Collection of WAL records/page images in a range of LSNs
               for a range of keys (equivalent to incremental changes)

Layer organization (LSM-like):
  L0: DeltaLayers covering full key range, narrow LSN range
      (incoming WAL gets written here first)

  L1: After compaction — DeltaLayers covering narrow key range,
      full LSN range (reshuffled for locality)

  Image layers created periodically for each key range
  (avoids long reconstruction chains)
```

**GetPage@LSN reconstruction:**

```
Request: GetPage(page_id=42, lsn=500)

1. Search layer map for page 42:
   - Find latest ImageLayer for page 42 at or before LSN 500
     → Found: ImageLayer at LSN 300 (base image)
   - Find all DeltaLayers between LSN 300 and LSN 500
     → Found: Delta at LSN 350, Delta at LSN 480

2. Reconstruct:
   base_page = ImageLayer.get(page_42, LSN=300)
   page = apply(Delta@350, base_page)
   page = apply(Delta@480, page)
   return page  // page as of LSN 500
```

**WAL redo process (security):** each tenant gets a **separate WAL redo process** that runs in a seccomp sandbox. The process cannot access the filesystem or network — it communicates with the pageserver only through a pipe. This isolates tenant data and limits the blast radius of bugs in PostgreSQL's WAL replay code.

**Compaction:** L0 → L1 compaction reshuffles data so each L1 file covers the full LSN range but only part of the key space. Compaction also materializes page images (collapsing delta chains) and drops obsolete page versions beyond the retention window.

### 5.5 Branching

Neon's killer feature: **instant database branching** via copy-on-write.

```
Timeline (main):  LSN 100 ──→ LSN 200 ──→ LSN 300 ──→ ...
                                 │
                                 │ branch (instant, zero-copy)
                                 ▼
Timeline (dev):                  LSN 200 ──→ LSN 201 ──→ ...
                                 (new writes diverge,
                                  old pages shared via COW)
```

Because all page history is immutable in S3, branching just creates a new timeline pointer — no data is copied. Divergent writes go to new layer files.

### 5.6 How It Differs from Aurora

| Dimension | Aurora | Neon |
|-----------|--------|------|
| Compute engine | Modified MySQL/PostgreSQL | Unmodified PostgreSQL + smgr shim |
| WAL durability | Quorum across 6 storage nodes | Paxos across safekeepers |
| Page serving | Storage nodes | Dedicated pageserver |
| Long-term storage | Integrated | S3 (separate) |
| Branching | Cloning (full volume copy) | Instant COW branches |
| Scale-to-zero | No (always-on compute) | Yes (compute shuts down) |
| Open source | No | Yes (Rust storage layer) |

---

## 6. Snowflake

### 6.1 Architecture

Snowflake (Dageville et al., SIGMOD 2016) is a disaggregated OLAP data warehouse with three layers:

```
┌──────────────────────────────────────────────────────┐
│ Layer 1: Cloud Services                               │
│  ┌─────────────────────────────────────────────┐     │
│  │  Query optimizer, metadata, authentication,  │     │
│  │  access control, transaction management      │     │
│  └─────────────────────────────────────────────┘     │
├──────────────────────────────────────────────────────┤
│ Layer 2: Virtual Warehouses (Compute)                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │    VW 1  │  │    VW 2  │  │    VW 3  │  ...     │
│  │ (XS-4XL) │  │ (XS-4XL) │  │ (XS-4XL) │          │
│  │ +SSD cache│  │ +SSD cache│  │ +SSD cache│          │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘          │
│        └──────────────┼──────────────┘               │
│                       │                               │
├───────────────────────┼───────────────────────────────┤
│ Layer 3: Centralized Storage                          │
│  ┌────────────────────┴──────────────────────┐       │
│  │  S3 / GCS / Azure Blob Storage            │       │
│  │  Immutable micro-partitions (columnar)     │       │
│  └───────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────┘
```

### 6.2 Key Design Decisions

**Micro-partitions:** Data is stored as immutable, compressed, columnar files (50-500MB each). No in-place updates. Mutations create new micro-partitions and tombstone old ones.

**Virtual Warehouses (VWs):** MPP compute clusters that can be independently started, stopped, and resized. Multiple VWs can query the same data simultaneously without interference. Each VW has a local SSD cache for hot micro-partitions.

**No data shuffling:** because storage is shared, adding a new VW doesn't require moving data. It just starts reading from S3.

### 6.3 Time Travel and Zero-Copy Cloning

Because micro-partitions are immutable, Snowflake retains previous versions:

```
Time T1:  Table A = {partition1, partition2, partition3}
Time T2:  UPDATE → Table A = {partition1, partition2', partition3}
          (partition2 is retained for time travel)

CLONE:    Table B = clone(Table A)
          → Table B points to same partitions (zero-copy)
          → Future mutations create new partitions per table
```

Time travel allows querying data **as of** any point within a configurable retention window (default 1 day, up to 90 days on Enterprise).

### 6.4 Why Snowflake Disaggregation Works for OLAP

OLAP workloads are read-heavy with large sequential scans. The S3 bandwidth (hundreds of GB/s aggregate) matches the access pattern. Write latency to S3 (10-100ms) is acceptable because OLAP writes are infrequent batch loads, not individual transactions.

This would **not** work for OLTP where individual row writes need sub-millisecond latency — hence Aurora/Socrates use specialized storage layers rather than raw object stores.

---

## 7. PolarDB (Alibaba)

### 7.1 Architecture

PolarDB uses a shared-storage architecture connected via RDMA, with PolarFS as the distributed filesystem.

```
┌──────────────────────────────────────────────────────┐
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ Primary  │  │ Read-Only│  │ Read-Only│  ...      │
│  │(MySQL/PG)│  │ Replica  │  │ Replica  │           │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘          │
│        └──────────────┼──────────────┘               │
│                       │  25Gbps RDMA                  │
│        ┌──────────────┴──────────────┐               │
│        │         PolarFS             │               │
│        │  (User-space distributed    │               │
│        │   filesystem over RDMA)     │               │
│        │  + NVMe SSDs + Optane       │               │
│        │  + ParallelRaft consensus   │               │
│        └─────────────────────────────┘               │
└──────────────────────────────────────────────────────┘
```

### 7.2 PolarFS

PolarFS (Cao et al., VLDB 2018) is a user-space distributed filesystem designed for minimal latency:

**Key technologies:**
- **RDMA networking** (25Gbps): bypasses OS network stack
- **NVMe SSDs via SPDK**: bypasses OS storage stack
- **User-space I/O stack**: eliminates kernel transitions
- **ParallelRaft**: a consensus protocol derived from Raft that allows out-of-order I/O completion, exploiting database-level tolerance for non-sequential log application

**Performance:** PolarFS adds ~10μs overhead vs. local ext4. This is ~10× lower latency than Ceph and ~2× the throughput.

### 7.3 Key Differences from Aurora

| Dimension | Aurora | PolarDB |
|-----------|--------|---------|
| Storage abstraction | Log-based (smart storage) | Filesystem (POSIX-like) |
| Network | Standard TCP/IP | RDMA |
| Database modification | Significant (log shipping) | Minimal (FS looks local) |
| Hardware requirements | Standard cloud VMs | Dedicated RDMA + NVMe |
| Log consensus | Quorum across storage nodes | ParallelRaft in PolarFS |

### 7.4 PolarDB-MP (Multi-Primary)

PolarDB-MP (2024) extends PolarDB with multi-primary support using disaggregated shared memory:

- **Buffer fusion**: distributed cache across compute nodes via RDMA
- **Lock fusion**: distributed page + row locking
- **Transaction fusion**: per-node transaction tables with global coordination

Achieves 3× throughput with 8 primaries on non-partitioned workloads.

---

## 8. Google AlloyDB

### 8.1 Architecture

AlloyDB is Google's PostgreSQL-compatible database with disaggregated storage, built on Google's infrastructure.

```
┌──────────────────────────────────────────────────────┐
│  ┌─────────────┐  ┌─────────────┐                   │
│  │  Primary    │  │ Read Pool   │  ... (elastic)     │
│  │ (Postgres)  │  │ (Postgres)  │                    │
│  │ +buffer pool│  │ +ultra-fast  │                    │
│  │ +ultra-fast │  │   cache     │                    │
│  │   cache     │  └──────┬──────┘                    │
│  └──────┬──────┘         │                           │
│         │ WAL            │ page reads                 │
│  ┌──────┴────────────────┴──────┐                    │
│  │  Intelligent Storage Service  │                    │
│  │  ┌───────────────────────┐   │                    │
│  │  │ Log Processing Service │   │                    │
│  │  │ (applies WAL → pages)  │   │                    │
│  │  └───────────┬───────────┘   │                    │
│  │  ┌───────────┴───────────┐   │                    │
│  │  │ Block Storage          │   │                    │
│  │  │ (materialized pages)   │   │                    │
│  │  └───────────────────────┘   │                    │
│  └──────────────────────────────┘                    │
└──────────────────────────────────────────────────────┘
```

### 8.2 Key Design

**Log Processing Service (LPS):** Receives WAL from primary, processes log records, and generates materialized data blocks. Writes are acknowledged as soon as WAL is persisted to regional storage — block materialization is asynchronous (similar to Aurora).

**The storage service itself is disaggregated:** block storage (pages) scales separately from log processing (WAL application). This enables the LPS to be scaled independently based on write throughput.

**Multi-tier caching:**
1. **Buffer cache** (RAM): standard PostgreSQL shared buffers
2. **Ultra-fast cache** (local NVMe SSD): additional hot-page caching layer
3. **Intelligent storage**: always available, handles cache misses

**WAL streaming to replicas:** like Aurora, AlloyDB streams WAL records from primary to read pool instances for cache invalidation.

---

## 9. Google Spanner and Colossus

### 9.1 Spanner Architecture

Spanner (Corbett et al., OSDI 2012) is Google's globally-distributed database, running on Colossus (the successor to GFS).

```
┌──────────────────────────────────────────────────────┐
│  Zone 1 (≈ datacenter)                                │
│  ┌─────────────────────────────────────────────┐     │
│  │  Spanserver                                  │     │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐     │     │
│  │  │ Tablet  │  │ Tablet  │  │ Tablet  │     │     │
│  │  │(B-tree +│  │(B-tree +│  │(B-tree +│     │     │
│  │  │  WAL on │  │  WAL on │  │  WAL on │     │     │
│  │  │Colossus)│  │Colossus)│  │Colossus)│     │     │
│  │  │+ Paxos  │  │+ Paxos  │  │+ Paxos  │     │     │
│  │  │ group   │  │ group   │  │ group   │     │     │
│  │  └─────────┘  └─────────┘  └─────────┘     │     │
│  └─────────────────────────────────────────────┘     │
│  ┌─────────────────────────────────────────────┐     │
│  │  Colossus (distributed filesystem)           │     │
│  │  (successor to GFS, 1MB chunks, D-servers)   │     │
│  └─────────────────────────────────────────────┘     │
└──────────────────────────────────────────────────────┘
```

**Data model:** tables are divided by primary key ranges into **splits**. Each split is managed by a **Paxos group** with replicas across zones (typically 3 or 5 replicas). Each replica stores its tablet as a B-tree-like structure + WAL on Colossus.

### 9.2 TrueTime

Spanner's key innovation is **TrueTime**, an API that returns a time interval `[earliest, latest]` guaranteed to contain the true current time:

```
TrueTime API:
  TT.now()    → TTinterval [earliest, latest]
  TT.after(t) → true if t has definitely passed
  TT.before(t)→ true if t has definitely not arrived

Implementation:
  GPS receivers + atomic clocks in each datacenter
  GPS provides absolute time, atomic clocks provide stability
  Each corrects for the other's failure modes
  Typical uncertainty (ε): ~1-7ms
```

**Commit-wait protocol:** after assigning a commit timestamp `s`, the leader waits until `TT.after(s)` is true before revealing the write. This guarantees that any subsequent transaction will see this write — providing **external consistency** (linearizability) without centralized coordination.

```
Commit:
  1. Acquire locks
  2. s = TT.now().latest   (assign timestamp)
  3. Wait until TT.after(s) (commit-wait, ~2ε ≈ 2-14ms)
  4. Release locks, reveal commit
```

### 9.3 Colossus

Colossus is the successor to the Google File System (GFS, Ghemawat et al., SOSP 2003):

- **Chunk size**: 1MB (vs. 64MB in GFS) — better tail latency for small reads
- **Metadata**: distributed in a scalable Bigtable-based metadata service (vs. single master in GFS)
- **D-servers**: data servers that store chunks
- **Custodians**: background processes that manage replication, garbage collection, re-replication

Colossus serves as the common storage substrate for Spanner, Bigtable, Blobstore, and many other Google services. It is the foundational layer that makes Spanner's disaggregation possible — compute (spanservers) can fail independently from storage (Colossus).

---

## 10. FoundationDB: Unbundled Architecture

FoundationDB (Zhou et al., SIGMOD 2021) takes disaggregation to an extreme by unbundling the database into many independent roles:

```
┌──────────────────────────────────────────────────────┐
│ Control Plane (Paxos):                                │
│  Coordinators → ClusterController → recruits:         │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │Sequencer │  │ Proxies  │  │Resolvers │           │
│  │(timestamp│  │(tx proc) │  │(conflict │           │
│  │ oracle)  │  │          │  │ detect)  │           │
│  └──────────┘  └────┬─────┘  └──────────┘          │
│                      │                               │
│ Data Plane:          │                               │
│  ┌───────────────────┴──────────────────┐           │
│  │  Log Servers (WAL, f+1 replicas)     │           │
│  └───────────────────┬──────────────────┘           │
│                      │ async                         │
│  ┌───────────────────┴──────────────────┐           │
│  │  Storage Servers (materialized data)  │           │
│  └──────────────────────────────────────┘           │
└──────────────────────────────────────────────────────┘
```

**Key insight:** log servers and storage servers are separate roles. Log servers provide durability (f+1 replicas, not quorum-based). Storage servers asynchronously consume the log to materialize data. If any component fails, the ClusterController reconfigures the entire system in <5 seconds.

This is disaggregation taken further than Aurora — not just compute vs. storage, but **every database function is a separate, independently scalable service**.

---

## 11. TiDB: Range-Based Disaggregation

### 11.1 Architecture

TiDB separates SQL processing from distributed storage via TiKV.

```
┌──────────────────────────────────────────────────────┐
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │  TiDB    │  │  TiDB    │  │  TiDB    │  (SQL)   │
│  │ (compute)│  │ (compute)│  │ (compute)│           │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘          │
│        └──────────────┼──────────────┘               │
│                       │                               │
│  ┌────────────────────┴─────────────────────┐        │
│  │              TiKV (Storage)               │        │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  │        │
│  │  │ Region1 │  │ Region2 │  │ Region3 │  │        │
│  │  │(Raft grp│  │(Raft grp│  │(Raft grp│  │        │
│  │  │ 3 repls)│  │ 3 repls)│  │ 3 repls)│  │        │
│  │  └─────────┘  └─────────┘  └─────────┘  │        │
│  └──────────────────────────────────────────┘        │
│                                                       │
│  ┌──────────────────────────────────────────┐        │
│  │          TiFlash (Columnar Replicas)      │        │
│  │  Raft-learner replication from TiKV       │        │
│  │  Columnar storage for OLAP queries        │        │
│  └──────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────┘
```

**Key difference from Aurora:** TiKV uses **range-based sharding with per-range Raft groups** (Multi-Raft). Each range ("Region", ~96MB) has its own Raft group with 3 replicas. This is distributed consensus at the storage layer, not quorum I/O.

**TiFlash** adds HTAP capability: columnar replicas receive updates via Raft learner protocol, enabling real-time analytics on transactional data without ETL.

---

## 12. GaussDB (Huawei)

GaussDB (VLDB 2024) introduces **three-tier disaggregation**: compute, memory, and storage as separate layers.

```
┌──────────────────────────────────────────────────────┐
│  Compute Layer (transaction processing)               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │Compute 1 │  │Compute 2 │  │Compute 3 │ (multi-   │
│  │(primary) │  │(primary) │  │(primary) │  primary)  │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘          │
│        └──────────────┼──────────────┘               │
│                       │                               │
│  Memory Layer (global buffer pool + lock manager)     │
│  ┌────────────────────┴─────────────────────┐        │
│  │  Disaggregated shared memory (RDMA)       │        │
│  │  - Global buffer management               │        │
│  │  - Global lock management                 │        │
│  │  - Page ownership + affinity routing      │        │
│  └──────────────────────┬───────────────────┘        │
│                         │                             │
│  Storage Layer (page + log persistence)               │
│  ┌──────────────────────┴───────────────────┐        │
│  │  Distributed storage (pages + WAL)        │        │
│  └──────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────┘
```

**Key innovation:** the memory layer is a separate tier. Pages are logically partitioned and assigned ownership to compute nodes. If a query touches pages owned by another node, ownership is transferred. A smart routing layer captures data affinity to minimize page transfers.

**Recovery:** two-tier checkpoint recovery (memory + storage). Memory checkpoints + on-demand page recovery significantly reduce failover time.

---

## 13. Key Design Patterns

### 13.1 "The Log Is the Database" (Aurora Pattern)

The compute node treats the storage layer as a log endpoint. Only redo log records cross the network. The storage layer applies redo to reconstruct pages.

```
Compute:   page mutation → generate redo → ship to storage → done
Storage:   receive redo → apply to base page → serve page requests

Benefit:   Minimal network I/O (log records are tiny vs. full pages)
Trade-off: Storage must be "smart" (understand log format)
```

### 13.2 Separation of WAL Service from Page Storage

Socrates and Neon separate WAL durability from page materialization:

```
Compute → WAL Service (fast, durable)  → Page Servers (materialization)
                                        → Object Store (archive)

Benefit:   WAL service can be optimized for latency (Premium SSD, NVRAM)
           Page servers can be optimized for throughput
Trade-off: Additional tier adds complexity and potential latency
```

### 13.3 Cache Hierarchy in Disaggregated Systems

Every disaggregated system uses multi-level caching to hide storage latency:

```
Level 1: Buffer Pool (RAM, ~10-100GB)
  Hit time: nanoseconds
  Managed by database engine

Level 2: Local SSD Cache (NVMe, ~1-10TB)
  Hit time: microseconds
  Socrates: RBPEX
  AlloyDB:  Ultra-fast cache
  Snowflake: VW local SSD cache

Level 3: Remote Storage (page servers, storage nodes)
  Hit time: sub-millisecond (RDMA) to milliseconds (TCP)

Level 4: Object Store (S3/GCS/Azure Blob)
  Hit time: 10-100ms
  Used for cold data, archival, time travel
```

### 13.4 Lease-Based Coordination

Disaggregated systems use leases to manage ownership and prevent split-brain:

```
Primary compute holds lease:
  - If lease expires, primary cannot write
  - Failover candidate acquires new lease
  - Lease prevents two primaries from writing simultaneously

Storage nodes hold page leases:
  - Ensures only one page server serves a given page range
  - Prevents split-brain page serving after reconfig
```

### 13.5 Near-Data Processing (Pushdown)

Pushing computation to the storage layer reduces network transfer:

```
Without pushdown:
  Storage ──→ [full pages] ──→ Compute (filter here)
  Network cost: high

With pushdown:
  Storage (filter here) ──→ [matching rows only] ──→ Compute
  Network cost: low

Examples:
  - Aurora: storage-level page coalescing
  - PolarDB: FPGA-based filter pushdown to SSDs
  - Snowflake: micro-partition pruning before scan
  - TiKV: coprocessor pushdown for simple predicates
```

### 13.6 Cache Coherence in Disaggregated Systems

When multiple compute nodes cache pages from shared storage, maintaining coherence is a fundamental challenge. Six approaches have emerged:

```
Approach                  System           Mechanism
────────────────────────  ──────────────── ───────────────────────────────
Single-writer             Aurora           Only primary writes; replicas
                                           receive invalidations via log
                                           stream. No coherence needed.

Lease-based invalidation  Socrates         Compute holds page lease;
                                           storage pushes invalidation
                                           when page is modified.

RDMA-based                PolarDB-MP       Buffer fusion via RDMA. Direct
                                           memory reads across nodes.
                                           Distributed locking.

CXL hardware coherence    PolarDB-CXL      Cache-coherent shared memory
                                           across machines via CXL.
                                           Hardware handles coherence.

SELCC (VLDB 2025)         Research          Shared-Exclusive Latch Cache
                                           Coherence. Embeds ownership
                                           metadata in RDMA latch word.
                                           No computation on memory side.

Federated coherence       Research (2025)   Argues CXL snooping/directory
                                           will not scale. Proposes
                                           software-managed coherence
                                           domains with cross-domain
                                           protocols.
```

SELCC achieves coherence without any computation at the remote memory side — ownership metadata is embedded directly in the RDMA latch word, enabling cache management via RDMA atomic operations. This is important because disaggregated memory nodes are often compute-limited.

---

## 14. Storage Layer Technologies

### 14.1 Cloud Object Storage as Substrate

```
Service          Latency (first byte)   Throughput         Cost/GB/mo
───────────────  ─────────────────────  ─────────────────  ──────────
S3 Standard      50-200ms               Aggregate: TB/s+   ~$0.023
S3 Express       Single-digit ms        10× S3 Standard    ~$0.16
GCS Standard     50-200ms               Similar to S3      ~$0.020
Azure Blob       50-200ms               Similar to S3      ~$0.018
```

**S3 Express One Zone (2023):** a significant development — up to 10× lower latency than S3 Standard. Enables object storage as a viable tier for lower-latency workloads (e.g., Iceberg metadata, hot data in lakehouses).

### 14.2 RDMA

Remote Direct Memory Access enables ~1-5μs latency reads/writes across the network, bypassing the OS. Used by PolarDB, GaussDB, and CXL-based systems.

```
Traditional network path:        RDMA path:
  App → kernel → TCP → NIC →     App → RDMA verb → NIC → wire
  wire → NIC → TCP → kernel →    wire → NIC → App memory (DMA)
  App
  Latency: 50-500μs              Latency: 1-5μs
```

### 14.3 CXL (Compute Express Link)

CXL is a cache-coherent interconnect enabling memory disaggregation at the hardware level:

```
CXL 1.0 (2019): Cache coherence + memory semantics for accelerators
CXL 2.0 (2022): Memory pooling via CXL Switch
CXL 3.0 (2023): Multi-level switching, rack-scale memory pools
CXL 4.0 (2025): Enhanced fabric, improved latency

                 ┌────────┐    ┌────────┐
                 │ CPU 1  │    │ CPU 2  │
                 └───┬────┘    └───┬────┘
                     │  CXL        │  CXL
                 ┌───┴─────────────┴───┐
                 │     CXL Switch      │
                 └───┬─────┬─────┬─────┘
                     │     │     │
                 ┌───┴─┐ ┌┴───┐ ┌┴───┐
                 │DRAM │ │DRAM│ │DRAM│  (memory pool)
                 └─────┘ └────┘ └────┘
```

**For databases:** CXL enables shared buffer pools across compute nodes with cache-coherent memory access. PolarDB-CXL demonstrated direct buffer pool access across machines, eliminating the per-page latency of RDMA-based approaches. CXL achieves 3.8× speedup over 200G RDMA.

---

## 15. Advantages and Trade-offs

### 15.1 Advantages

| Advantage | Mechanism |
|-----------|-----------|
| **Elastic compute** | Add/remove compute nodes without touching storage |
| **Elastic storage** | Grow storage without affecting compute |
| **Cost efficiency** | Pay for compute only when used (serverless) |
| **Failure isolation** | Compute crash ≠ data loss; restart elsewhere |
| **Fast failover** | New compute attaches to existing storage in seconds |
| **Read scaling** | Add read replicas that share storage (zero-copy) |
| **Instant branching** | COW semantics on immutable storage (Neon, Snowflake) |
| **Time travel** | Retain historical versions cheaply in object store |
| **Multi-tenancy** | Share storage infrastructure across tenants |

### 15.2 Trade-offs

| Trade-off | Detail |
|-----------|--------|
| **Network latency** | Every cache miss = network round trip (μs–ms vs. ns for local SSD) |
| **Tail latency** | Network jitter causes P99/P999 spikes |
| **Complexity** | More tiers = more failure modes, harder debugging |
| **Cache efficiency** | Local SSD cache must be warm; cold starts are expensive |
| **Write amplification** | Some architectures still replicate heavily (Aurora: 6 copies) |
| **Vendor lock-in** | Most disaggregated DBs are cloud-proprietary (except Neon, TiDB) |

### 15.3 When Disaggregation Hurts

- **Write-heavy OLTP with microsecond SLAs:** network latency for every write can be unacceptable
- **Single-node workloads:** disaggregation overhead without scaling benefits
- **Latency-sensitive applications:** P99 spikes from network hops, remote page faults
- **Small datasets:** overhead of multi-tier architecture isn't justified

---

## 16. Emerging Trends

### 16.1 Serverless + Disaggregated (Scale-to-Zero)

Neon and Aurora Serverless represent the convergence: compute scales to zero, storage persists independently. User pays nothing when idle.

```
Idle:     Storage ✓ (persistent, cheap)   Compute ✗ (scaled to zero)
Active:   Storage ✓                       Compute ✓ (spun up in seconds)
```

### 16.2 Lakehouse Architecture

The lakehouse pattern applies disaggregation to analytics: open table formats (Iceberg, Delta Lake, Hudi) sit on object storage, and arbitrary compute engines (Spark, Trino, Flink, DuckDB) query the data independently.

```
Compute engines (disaggregated, interchangeable):
  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐
  │ Spark │  │ Trino │  │ Flink │  │DuckDB │
  └───┬───┘  └───┬───┘  └───┬───┘  └───┬───┘
      └──────────┼──────────┼──────────┘
                 │
  ┌──────────────┴──────────────┐
  │  Open Table Format Layer    │
  │  (Iceberg / Delta / Hudi)   │
  │  - Schema, partitioning     │
  │  - ACID transactions        │
  │  - Time travel, branching   │
  └──────────────┬──────────────┘
                 │
  ┌──────────────┴──────────────┐
  │  Object Storage (S3/GCS)    │
  │  Parquet / ORC files        │
  └─────────────────────────────┘
```

### 16.3 CXL Memory Pooling

CXL 2.0+ enables **memory disaggregation**: CPUs access a shared memory pool over the CXL fabric. This is a step beyond storage disaggregation — now even RAM is a shared, elastic resource.

Database implications:
- Shared buffer pools across compute nodes (cache coherent, no page transfer overhead)
- Elastic memory allocation (add DRAM to pool, no node restart)
- Faster recovery (buffer pool survives compute failure if memory is remote)

Research is exploding: 10 papers 2019-2022, 40 in 2023, 51 in 2024.

### 16.4 Multi-Primary Disaggregation

The next frontier: multiple read-write primaries sharing disaggregated storage. Examples:
- **Aurora DSQL**: multi-region active-active with OCC conflict resolution
- **PolarDB-MP**: multi-primary with RDMA-based buffer/lock/transaction fusion
- **GaussDB**: compute-memory-storage disaggregation with page ownership routing
- **Taurus Multi-Master**: pessimistic distributed locking, 3× throughput with 8 primaries

---

## 17. Key Papers

### 17.1 Foundational Systems Papers

1. **Verbitski et al.** - "Amazon Aurora: Design Considerations for High Throughput Cloud-Native Relational Databases", SIGMOD, 2017. The foundational Aurora paper.
2. **Verbitski et al.** - "Amazon Aurora: On Avoiding Distributed Consensus for I/Os, Commits, and Membership Changes", SIGMOD, 2018. Follow-up on Aurora's consensus avoidance.
3. **Antonopoulos et al.** - "Socrates: The New SQL Server in the Cloud", SIGMOD, 2019. Azure SQL Hyperscale architecture.
4. **Dageville et al.** - "The Snowflake Elastic Data Warehouse", SIGMOD, 2016. Snowflake's three-layer architecture.
5. **Cao et al.** - "PolarFS: An Ultra-low Latency and Failure Resilient Distributed File System for Shared Storage Cloud Database", VLDB, 2018. PolarDB's storage layer.
6. **Zhou et al.** - "FoundationDB: A Distributed Unbundled Transactional Key Value Store", SIGMOD, 2021. FoundationDB's unbundled architecture.
7. **Huang et al.** - "TiDB: A Raft-based HTAP Database", VLDB, 2020. TiDB's range-sharded disaggregated architecture.
8. **Depoutovitch et al.** - "Taurus Database: How to Be Fast, Available, and Frugal in the Cloud", SIGMOD, 2020. Huawei's disaggregated DB.
9. **Corbett et al.** - "Spanner: Google's Globally-Distributed Database", OSDI, 2012. TrueTime, Paxos-based replication, external consistency.
10. **Ghemawat, Gobioff, Leung** - "The Google File System", SOSP, 2003. GFS, predecessor to Colossus.
11. **Bacon et al.** - "Spanner: Becoming a SQL System", SIGMOD, 2017. Evolution of Spanner's query engine.

### 17.2 Multi-Primary and Advanced Disaggregation

9. **GaussDB team** - "GaussDB: A Cloud-Native Multi-Primary Database with Compute-Memory-Storage Disaggregation", VLDB, 2024.
10. **PolarDB-MP team** - "PolarDB-MP: A Multi-Primary Cloud-Native Database via Disaggregated Shared Memory", SIGMOD Companion, 2024.
11. **PolarDB team** - "From Scale-Up to Scale-Out: PolarDB's Journey to Achieving 2 Billion tpmC", VLDB, 2025.

### 17.3 Surveys and Tutorials

15. **Wang, Zhang** - "Disaggregated Database Systems" (Tutorial), SIGMOD, 2023. Purdue tutorial covering taxonomy and trade-offs.
16. **Dong, Zhang et al.** - "Cloud-Native Databases: A Survey", IEEE TKDE, 2024. Comprehensive survey of cloud-native DB architectures.
17. **Yu et al.** - "Disaggregation: A New Architecture for Cloud Databases", VLDB, 2025.

### 17.4 Benchmarks and Analysis

18. **Pang, Wang** - "Understanding the Performance Implications of the Design Principles in Storage-Disaggregated Databases" (OpenAurora), SIGMOD, 2024.

### 17.5 CXL, Memory Disaggregation, and Cache Coherence

19. **Tsinghua/ICDE team** - "A CXL-Powered Database System: Opportunities and Challenges", ICDE, 2024.
20. **Weisgut et al.** - "CXL Memory Performance for In-Memory Data Processing", VLDB, 2025.
21. **SIGMOD 2025** - "Unlocking the Potential of CXL for Disaggregated Memory in Cloud-Native Databases", SIGMOD Companion, 2025.
22. **Wang et al.** - "Cache Coherence Over Disaggregated Memory" (SELCC), VLDB, 2025. RDMA-based coherence without remote computation.
23. **Federated Coherence** - "The Dawn of Disaggregation and the Coherence Conundrum: A Call for Federated Coherence", arXiv, 2025.

### 17.6 Related Systems Papers

24. **Vuppalapati et al.** - "Building An Elastic Query Engine on Disaggregated Storage", NSDI, 2020. Snowflake's elastic compute.
25. **Armbrust et al.** - "Delta Lake: High-Performance ACID Table Storage over Cloud Object Stores", VLDB, 2020. Delta Lake.
26. **AWS re:Invent 2024** - "Amazon Aurora DSQL" (industry announcement). Multi-region disaggregated SQL.

---

## 18. System Comparison Matrix

```
System          Type   Storage Model          Compute-Storage    WAL Model          Replication
                                              Link
──────────────  ─────  ─────────────────────  ─────────────────  ─────────────────  ──────────────
Aurora          OLTP   Smart storage (log→    TCP/IP             Partitioned WAL    Quorum (4/6)
                       page materialization)                     across PGs

Socrates        OLTP   Landing zone + page    TCP/IP + local SSD Log service →     Log replication
                       servers + XStore                          page servers        to secondaries

Neon            OLTP   Safekeepers + page-    TCP/IP + S3        Safekeepers        Paxos (WAL)
                       server + S3                               (Paxos quorum)

PolarDB         OLTP   PolarFS (distributed   RDMA (25Gbps)     Shared log via     ParallelRaft
                       filesystem)                               PolarFS

AlloyDB         OLTP   Intelligent storage    Google internal    LPS processes      Regional
                       (LPS + block store)    network            WAL → blocks       replication

Spanner         OLTP   B-trees + WAL on       Google internal    Per-split Paxos    Paxos (per split,
                       Colossus (GFS v2)      network            log                cross-region)

Snowflake       OLAP   S3/GCS/Azure Blob      TCP/IP            N/A (batch loads)  Object store
                       (micro-partitions)                                           replication

TiDB/TiKV       HTAP   Range-sharded Raft     TCP/IP            Per-range Raft     Multi-Raft
                       groups                                    log                (3 replicas)

FoundationDB    OLTP   Log servers + storage  TCP/IP             f+1 log servers    f+1 (eager
                       servers (unbundled)                                          reconfiguration)

GaussDB         OLTP   Memory layer + storage  RDMA              Multi-primary      Memory + storage
                       layer (3-tier)                             WAL                checkpoints

Aurora DSQL     OLTP   Journal + crossbar     AWS internal       Journal service    Multi-region
                       (fully disaggregated)                                        active-active
```

---

## See Also

- [WAL, Torn Pages, and Disk Reliability](@/notes/database/wal_torn_pages.md) — WAL propagation is central to Aurora, Neon, and Socrates architectures
- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — Paxos and Raft protocols used by Aurora, PolarDB, Spanner, and TiKV
- [Database Systems Survey](@/notes/database/database_systems.md) — Broader survey covering many of the same systems from a different angle
- [Buffer Management and Predictive Translation](@/notes/database/buffer_management_predictive_translation.md) — Buffer pool design changes significantly in disaggregated architectures

---

*Last updated: 2026-02-11*
