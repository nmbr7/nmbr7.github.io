+++
title = "WAL Torn Pages"
date = 2026-02-10
[taxonomies]
tags = ["wal", "crash-recovery", "torn-pages", "nvme", "checksums", "durability", "postgresql"]
+++


# Write-Ahead Logging, Torn Pages, and Disk Reliability

**Date**: 2026-01-28
**Context**: Deep dive into how database systems ensure durability and consistency in the face of crashes and disk failures. Essential knowledge for understanding PostgreSQL's Full Page Writes (FPW) and why modern systems may handle this differently.

---

## Table of Contents

1. [Theoretical Foundations](#1-theoretical-foundations)
2. [The Torn Page Problem](#2-the-torn-page-problem)
3. [PostgreSQL's Full Page Writes](#3-postgresql-s-full-page-writes-fpw)
4. [Alternative Approaches by System](#4-alternative-approaches-by-system)
5. [Modern Hardware and Atomic Writes](#5-modern-hardware-and-atomic-writes)
6. [Checksums and Detection vs Prevention](#6-checksums-and-detection-vs-prevention)
7. [Why Some Systems Don't Need FPW](#7-why-some-systems-don-t-need-fpw)
8. [Trade-off Analysis](#8-trade-off-analysis)
9. [State-of-the-Art Research](#9-state-of-the-art-research)
10. [Practical Recommendations](#10-practical-recommendations)

---

## 1. Theoretical Foundations

### 1.1 The ACID Properties and Durability

The "D" in ACID (Durability) guarantees that once a transaction commits, its effects survive any subsequent failure. This seemingly simple guarantee is remarkably difficult to implement correctly.

**The fundamental tension:**
- We want high performance (batch writes, buffering)
- We need correctness (no data loss, no corruption)
- Hardware fails in complex, sometimes partial ways

### 1.2 Write-Ahead Logging (WAL) - The Core Principle

**Definition**: Before any modification to the database is written to disk, a log record describing the change must first be written to stable storage.

**The WAL Protocol (Mohan et al., ARIES, 1992):**

```
WRITE-AHEAD LOGGING RULE:
  Before a database page is written to disk, all log records
  pertaining to changes on that page must be flushed to the log.

FORCE-LOG-AT-COMMIT RULE:
  Before a transaction commits, all its log records must be
  flushed to stable storage.
```

**Why this works:**
1. If system crashes before commit, log doesn't contain commit record -> rollback
2. If system crashes after commit, log contains all changes -> redo
3. Log is append-only, sequential writes -> fast
4. Database pages can be written lazily -> performance

### 1.3 The Recovery Model

**Three types of recovery actions:**
- **REDO**: Re-apply committed changes that may not have reached disk
- **UNDO**: Roll back uncommitted changes that may have reached disk
- **ANALYSIS**: Determine what needs REDO and UNDO

**Key insight**: WAL separates the "durability" problem (did the change survive?) from the "consistency" problem (is the database in a valid state?).

### 1.4 Log Sequence Numbers (LSN)

Every modification generates a monotonically increasing LSN:

```
LSN:  1    2    3    4    5    6    7    8    ...
      |    |    |    |    |    |    |    |
      v    v    v    v    v    v    v    v
Log: [T1  [T1  [T2  [T1  [T2  [T1  [T2  [T2
      B]   W]   B]   W]   W]   C]   W]   C]

B = Begin, W = Write, C = Commit
```

Each database page stores its `PageLSN` - the LSN of the last log record that modified it. During recovery:
- If `PageLSN >= LogRecordLSN`, the change is already on the page (skip)
- If `PageLSN < LogRecordLSN`, the change needs to be redone (apply)

---

## 2. The Torn Page Problem

### 2.1 What is a Torn Page?

A **torn page** (also called a **partial write** or **fractured page**) occurs when a system crash happens during a write operation, leaving a page in a partially updated state.

**Scenario:**
```
Database Page (8KB) being written to disk:

Time T0: Original page on disk
         [Header | Row1 | Row2 | Row3 | Row4 | Free Space]

Time T1: Application modifies Row2, Row3
         Buffer: [Header' | Row1 | Row2' | Row3' | Row4 | Free Space]

Time T2: OS starts writing page to disk
         Disk:   [Header' | Row1 | Row2' | <POWER FAILURE>

Time T3: After recovery
         Disk:   [Header' | Row1 | Row2' | Row3 | Row4 | Free Space]
                                          ^^^^^
                                          OLD DATA!
```

**The problem:** The page header says "I contain modifications from LSN X" but part of the page still has old data from LSN Y.

### 2.2 Why Does This Happen?

**Physical reality of disk writes:**

1. **Magnetic HDDs (512-byte sectors):**
   - An 8KB page = 16 sectors
   - Each sector write is atomic (mostly)
   - But 16 sectors are NOT written atomically
   - Disk may reorder sector writes within the page

2. **SSDs (4KB pages internally):**
   - An 8KB database page = 2 SSD pages
   - Each 4KB SSD page write is atomic
   - But 2 pages are NOT written atomically
   - Flash Translation Layer (FTL) adds complexity

3. **The filesystem lies:**
   - OS buffers writes
   - Filesystem journaling has its own atomicity guarantees
   - `fsync()` behavior varies by filesystem

### 2.3 The Corruption Scenarios

**Scenario 1: Header updated, data not**
```
Page after crash:
  LSN = 100 (new)
  Data = old (LSN 50)

Recovery thinks: "Page is at LSN 100, no redo needed"
Reality: Page is corrupt, missing changes from LSN 51-100
```

**Scenario 2: Data updated, header not**
```
Page after crash:
  LSN = 50 (old)
  Data = mixed old/new

Recovery thinks: "Page is at LSN 50, redo from 51"
Reality: Redo applies changes to partially updated page -> more corruption
```

**Scenario 3: Checksum invalidated**
```
Page after crash:
  Checksum = old
  Data = mixed old/new

At least we can detect this! (if checksums are enabled)
```

### 2.4 Why WAL Alone Doesn't Solve This

**Common misconception:** "WAL guarantees recovery, so torn pages aren't a problem."

**Reality:** WAL guarantees you can **redo** changes, but redo assumes pages are in a **valid prior state**. A torn page is neither the old state nor the new state - it's an invalid hybrid.

```
Scenario:
1. Page at LSN 50
2. Transaction modifies page, logged at LSN 51
3. Crash during page write
4. Page on disk is torn (part LSN 50, part LSN 51)
5. Recovery reads page, sees PageLSN = 51
6. Recovery skips LSN 51 (thinks it's already applied)
7. Page remains corrupt FOREVER
```

---

## 3. PostgreSQL's Full Page Writes (FPW)

### 3.1 The Solution: Full Page Images

PostgreSQL's approach: After a checkpoint, the **first** time a page is modified, write a **complete copy** of the page to WAL before writing the modification.

```
WAL Record with FPW:
+------------------+
| XLogRecord Header|
+------------------+
| Full Page Image  | <- Complete 8KB page copy
| (8KB)            |
+------------------+
| Modification Data| <- The actual change
+------------------+
```

### 3.2 How FPW Works

**Checkpoint cycle:**

```
Checkpoint at LSN 1000
  |
  v
Page A first modified at LSN 1001 -> FPW (full image of page A)
Page A modified at LSN 1002 -> NO FPW (already have image since checkpoint)
Page A modified at LSN 1003 -> NO FPW
  |
  v
Checkpoint at LSN 2000
  |
  v
Page A first modified at LSN 2001 -> FPW (new full image)
```

**Recovery with FPW:**

```
1. Crash during page write
2. Page on disk is torn
3. Recovery reads WAL from last checkpoint
4. Finds FPW record for the page
5. Overwrites entire page with the full image from WAL
6. Continues recovery from that point
7. Page is now in a KNOWN GOOD state
```

### 3.3 PostgreSQL Implementation Details

**Key structures (from `xlog.h`):**

```c
/* Full-page image header */
typedef struct XLogRecordBlockImageHeader
{
    uint16      length;         /* Length of image data */
    uint16      hole_offset;    /* Offset of hole */
    uint8       bimg_info;      /* Flags */
} XLogRecordBlockImageHeader;

/* Flags */
#define BKPIMAGE_HAS_HOLE       0x01  /* Page has hole (sparse) */
#define BKPIMAGE_IS_COMPRESSED  0x02  /* Image is compressed */
#define BKPIMAGE_APPLY          0x04  /* Apply image during recovery */
```

**The FPW decision logic (simplified from `XLogInsert`):**

```c
static bool
XLogCheckBufferNeedsBackup(Buffer buffer)
{
    /*
     * If the page has been modified since the last checkpoint,
     * we don't need a full-page image.
     */
    if (PageGetLSN(page) >= RedoRecPtr)
        return false;  /* Page already modified since checkpoint */

    return true;  /* Need full-page image */
}
```

### 3.4 Configuration Options

```sql
-- Main switch (on by default, DO NOT DISABLE IN PRODUCTION)
full_page_writes = on

-- Compression to reduce WAL size (PostgreSQL 9.5+)
wal_compression = on

-- Checksum validation
data_checksums = on  -- Set at initdb time
```

### 3.5 The Performance Cost

**WAL amplification:**

```
Without FPW:
  UPDATE single row -> ~100 bytes of WAL

With FPW (first modification after checkpoint):
  UPDATE single row -> ~8KB of WAL (8000% increase!)
```

**Real-world impact:**
- WAL volume increases 2-10x with FPW
- More disk I/O for WAL writes
- Longer replication lag
- More archival storage needed
- Recovery takes longer (more data to process)

**Benchmark example:**
```
Workload: OLTP with random single-row updates

Without FPW: 50,000 TPS, 100 MB/s WAL
With FPW:    45,000 TPS, 800 MB/s WAL

Cost: 10% TPS reduction, 8x WAL volume
```

### 3.6 Optimizations

**1. Hole punching:**
If page has contiguous free space, don't write it:
```
Page: [Header|Data|Data|    FREE SPACE    ]
FPW:  [Header|Data|Data] <- Only non-empty part
```

**2. Compression (wal_compression = on):**
Compress full page images before writing to WAL.
Typical compression ratio: 2-4x for text-heavy pages.

**3. Checkpoint tuning:**
More frequent checkpoints = more FPW, but faster recovery.
Less frequent checkpoints = less FPW, but longer recovery.

---

## 4. Alternative Approaches by System

### 4.1 MySQL/InnoDB: Doublewrite Buffer

**Concept:** Write pages to a special "doublewrite buffer" area first, then to their final location.

```
Modification Flow:
1. Modify page in buffer pool
2. Write page to doublewrite buffer (sequential write)
3. fsync() doublewrite buffer
4. Write page to actual tablespace location
5. (No immediate fsync - can batch)

Recovery:
1. Read doublewrite buffer
2. For each page in doublewrite:
   - Read corresponding page from tablespace
   - If tablespace page is corrupt, copy from doublewrite
   - If both OK, use tablespace page (more recent)
```

**Implementation:**
```
Doublewrite buffer: 2MB (128 pages of 16KB)
Location: ibdata1 (system tablespace) or dedicated file

Two regions:
- First write: pages 0-63
- Second write: pages 64-127
(Alternating to ensure at least one complete copy)
```

**Pros:**
- WAL stays small (no full page images in redo log)
- Sequential writes for doublewrite (fast on HDDs)

**Cons:**
- Extra write I/O (write amplification = 2x for dirty pages)
- Latency for each page write
- Complexity in the I/O path

**MySQL 8.0 improvements:**
- Parallel doublewrite threads
- Dedicated doublewrite files
- Can be disabled for atomic-write-capable storage

### 4.2 SQL Server: Torn Page Detection + Shadow Paging

**Option 1: Torn Page Detection**
```
Each 512-byte sector gets a 2-bit "torn bit" pattern:
- Before write: Set pattern A
- After write: Should be pattern A in all sectors
- If mixed patterns: Page is torn

Detection only - does not prevent, only detects.
Requires restore from backup if torn page found.
```

**Option 2: Page Checksums**
```sql
ALTER DATABASE MyDB SET PAGE_VERIFY CHECKSUM
```

**Option 3: Shadow Pages (older method)**
- Write new page to new location
- Update pointer atomically
- Old page remains as backup

### 4.3 Oracle: Block Change Tracking + Flashback

**Approach:**
- Use O_DIRECT + Direct I/O to bypass filesystem
- Rely on storage-level atomic writes
- Redo log compression
- Block change tracking for incremental backups

**Key difference:** Oracle often runs on enterprise storage with battery-backed write caches, where atomic writes are guaranteed at the storage layer.

### 4.4 SQLite: Rollback Journal + WAL Mode

**Rollback Journal Mode (default):**
```
1. Before modifying page, copy original to journal
2. Modify page in place
3. On commit, delete journal

Recovery:
- If journal exists, roll back by restoring pages from journal
- Journal acts as "undo" log
```

**WAL Mode (modern SQLite):**
```
1. Write changes to WAL file (append-only)
2. Pages in main database are read-only during transactions
3. Checkpoint merges WAL back to main database

Recovery:
- Replay WAL from last checkpoint
- Main database always has consistent point-in-time state
```

**Torn page handling:**
- SQLite uses 4KB pages (matches typical filesystem block)
- Relies on filesystem atomic write guarantees for 4KB
- Checksums detect corruption
- WAL mode naturally avoids in-place updates

### 4.5 RocksDB: No In-Place Updates (LSM-Tree)

**Key insight:** LSM-trees never modify data in place.

```
Write path:
1. Write to WAL (append-only)
2. Insert to MemTable (in-memory)
3. When MemTable full, flush to SST file (new file, never modified)

SST files are immutable - written once, then only deleted
No partial writes possible - file is either complete or doesn't exist
```

**Torn page protection: NOT NEEDED**

Why? Because:
1. WAL is append-only (torn page = truncate to valid prefix)
2. SST files are immutable (atomic at file creation level)
3. Manifest file tracks which SST files are valid
4. Recovery: Read manifest, use only complete SST files

**The MANIFEST:**
```
MANIFEST-000001:
  Version 1: SST files [001, 002, 003]
  Version 2: SST files [001, 002, 003, 004]  <- compaction
  Version 3: SST files [001, 005]            <- cleanup

Recovery reads MANIFEST to know current valid state
```

### 4.6 DuckDB: Copy-on-Write + Checksums

**Architecture:**
- In-memory first, spill to disk as needed
- Copy-on-write for persistent storage
- No in-place modifications to data pages

```
Write path:
1. Create new version of modified block
2. Write new block to new location
3. Update metadata atomically
4. Old block available for reads until switch
```

**Torn page handling:**
- Never overwrite existing data
- Atomic metadata switches
- Checksums detect any corruption
- Simpler model for analytical workloads

### 4.7 FoundationDB: Copy-on-Write B-Trees

**Approach:** Similar to DuckDB but for distributed OLTP.

```
Write path:
1. Copy path from leaf to root
2. Write new pages (don't modify old)
3. Atomic pointer swing at root
4. Old pages garbage collected

Result: Always crash-consistent without torn page concerns
```

### 4.8 CockroachDB: Leverages RocksDB

CockroachDB uses RocksDB (now Pebble) as its storage layer:
- Inherits LSM-tree's immutability
- Adds distributed consensus (Raft) on top
- WAL + SST files, no torn page problem at storage layer

### 4.9 TigerBeetle: Protocol-Aware Recovery with Dual WAL

TigerBeetle is a financial accounting database that takes a radically different approach to durability. Rather than treating storage faults and consensus as separate problems, TigerBeetle integrates them into a unified system that can self-heal from disk corruption using cluster-wide redundancy.

**Design Philosophy:**

TigerBeetle treats disks as "near-Byzantine" - expecting not just crashes but:
- Latent sector errors (0.031% of SSDs, 1.4% of enterprise HDDs annually)
- Silent data corruption (bit rot)
- Misdirected I/O (firmware writes to wrong sector: 0.023% SSDs, 0.466% HDDs annually)
- Gray failures (disk becomes extremely slow without error codes)

**The Dual WAL Architecture:**

TigerBeetle maintains TWO write-ahead logs:

```
Primary WAL:   [Header1|Data1] [Header2|Data2] [Header3|Data3] ...
                   |              |              |
                   v              v              v
Secondary WAL: [Header1]      [Header2]      [Header3]      ...
               (headers only, lightweight copy)
```

**Why two WALs?**

This solves a critical problem that most databases get wrong:

```
Traditional database with single WAL:

WAL: [Record1] [Record2] [Record3] [CORRUPTED] [Record5] [Record6]
                              ^
                         bit rot here

Traditional recovery: "Corruption found, truncate from Record3 forward"
Result: Records 4, 5, 6 LOST even though they were committed!

TigerBeetle with dual WAL:

Primary WAL:   [H1|D1] [H2|D2] [H3|D3] [CORRUPTED] [H5|D5] [H6|D6]
Secondary WAL: [H1]    [H2]    [H3]    [H4]        [H5]    [H6]
                                        ^
                              Header intact in secondary!

TigerBeetle recovery:
1. Detect corruption at position 4 in primary
2. Check secondary WAL - header H4 is intact
3. Know that Record 4 was committed (header exists)
4. Request Record 4's data from replica cluster
5. ALL records preserved!
```

**Key insight:** The dual WAL lets TigerBeetle distinguish between:
- **Corruption at end of log** (torn write from power loss) -> safe to truncate
- **Corruption in middle of log** (bit rot) -> DO NOT truncate, repair from cluster

**Hash-Chaining:**

Every prepare message (unit of WAL entry) includes a checksum pointer to the previous entry:

```
Prepare N: {
    sequence: N,
    checksum: 0xABCD1234,           // My checksum
    parent_checksum: 0x5678EFGH,    // Previous prepare's checksum
    data: [...],
}
```

This creates a cryptographic chain from the beginning of history:
- If any single bit is corrupted, the chain breaks
- Recovery can identify exactly which record is corrupted
- Backups can verify their entire history matches the primary
- Protects against misdirected I/O (data written to wrong location)

**Direct I/O by Design:**

TigerBeetle uses `O_DIRECT` exclusively and NEVER uses buffered I/O.

**Why?** The 2018 paper "Can Applications Recover from fsync Failures?" (Pillai et al.) showed that:

```
Buffered I/O fsync failure scenario:

1. Application writes data to page cache
2. fsync() called
3. Disk returns EIO error (bad sector)
4. Kernel marks page as CLEAN (not dirty)
5. Application retries fsync()
6. Kernel says "nothing to write, page is clean"
7. Application thinks data is durable
8. DATA IS LOST - never actually written!

Both PostgreSQL and MySQL had this bug.
```

TigerBeetle's solution: bypass the page cache entirely.

```
Direct I/O requirements:
- All buffers aligned to 4KB sector boundaries
- All I/O sizes multiples of sector size
- User-space buffer management (no kernel page cache)
- Explicit control over durability
```

**io_uring Integration:**

TigerBeetle is built around Linux's io_uring for zero-syscall I/O:

```
Traditional I/O path:
  Application -> syscall -> kernel -> driver -> disk
  (context switch per operation)

io_uring path:
  Application -> shared ring buffer <- kernel polls
  (no context switches, kernel processes batches)
```

Benefits:
- Unified API for storage AND network I/O
- Batch submission of multiple operations
- Completion notification without syscalls
- Natural fit for TigerBeetle's static memory model

Cross-platform: Uses IOCP on Windows, kqueue on macOS.

**Protocol-Aware Recovery (PAR):**

Based on the FAST'18 Best Paper "Protocol-Aware Recovery for Consensus-Based Storage" (Alagappan et al.):

Traditional replicated systems treat storage and consensus as separate:
```
Node A: [Storage Engine] <-- crashes independently
Node B: [Storage Engine] <-- crashes independently
Node C: [Storage Engine] <-- crashes independently
        [Consensus Layer] <-- assumes perfect storage below
```

TigerBeetle integrates them:
```
Cluster: [Storage + Consensus integrated]
         - Storage faults communicated to consensus
         - Consensus redundancy used to repair storage
         - Single corrupted node healed from cluster
```

**Recovery scenarios:**

```
Scenario: Node A has corrupted WAL entry at position 100

Traditional Raft/Paxos:
  - Node A restarts
  - Finds corruption
  - ??? (undefined behavior, often data loss or unavailability)

TigerBeetle PAR:
  1. Node A detects corruption via checksum/hash-chain
  2. Node A sends repair request to cluster
  3. Nodes B, C provide the correct data for position 100
  4. Node A repairs its local storage
  5. Cluster continues operating
  6. No data loss, no unavailability
```

**NACK Protocol for View Changes:**

During leader election, a critical question: "What was actually committed?"

```
View change scenario:
  - Old leader had entries [1, 2, 3, 4, 5]
  - Entry 5 might have been partially replicated
  - New leader needs to know: "Was 5 committed?"

TigerBeetle's NACK:
  - Replicas positively state: "I NEVER accepted entry 5"
  - If 4/6 replicas NACK entry 5, it was never committed
  - Safe to discard entry 5 without data loss
  - Even works if entry 5 is corrupted on some nodes
```

**Immutable Data Model:**

All data in TigerBeetle is immutable:
- Accounts have balances, transfers are recorded
- No UPDATE operations in the traditional sense
- Transfers append to the log, balances computed
- Corruption detection is straightforward (data should never change)

**Superblock Protection:**

The superblock (root of the data structure) is stored 4 times:

```
Disk layout:
[Superblock Copy 0][Superblock Copy 1][Superblock Copy 2][Superblock Copy 3]
     |                   |                  |                  |
     +------- Each copy has sequence number + checksums -------+

Write protocol:
1. Write new superblock to next copy position
2. Read back and verify
3. Advance copy pointer

Recovery:
- Read all 4 copies
- Use highest valid sequence number
- Can survive up to 3 corrupted copies
```

**LSM-Tree for Derived State:**

While the WAL is the source of truth, derived state uses an LSM-tree:

```
Prepare messages -> WAL (source of truth)
                 -> LSM tree (derived state, for fast lookups)

LSM blocks:
- 0.5 MiB each
- Checksummed and hash-chained
- Referenced by address + checksum (not just address)
- Immutable once written
- Corruption repaired from replicas
```

**Testing: The VOPR Simulator:**

TigerBeetle uses a 1024-core fuzzing simulator called VOPR:

```
VOPR fault injection:
- Corrupt random bits (cosmic rays)
- Replace file chunks (misdirected writes)
- Lose writes entirely
- Inject latency (gray failures)
- All at double-digit percentages simultaneously!

Test criteria:
- No committed data lost
- No availability loss (unless majority corrupted)
- Cluster self-heals when faults are minority
```

**Jepsen Analysis Results (2025):**

Kyle Kingsbury's Jepsen tested TigerBeetle 0.16.11:

```
Findings:
- "Exceptional resilience to disk corruption"
- Recovered from bitflips on minority nodes
- Grid zone tolerated loss of all but one copy
- Safely halted (rather than corrupting) on majority corruption

Issues found and fixed:
- Superblock padding corruption caused crash (fixed in 0.16.26)
- WAL head vulnerability in helical fault patterns (documented limitation)
- Node recovery procedure was undocumented (fixed in 0.16.43)
```

**Performance Characteristics:**

```
Batching amortizes costs:
- 100 transfers in batch: ~50ms latency
- 10,000 transfers in batch: ~20ms latency (LOWER!)

Why? Little's Law - larger batches amortize:
- fsync cost
- Consensus round-trip
- Network overhead

TigerBeetle throughput:
- 175k-245k transfers/second (Linux, tuned)
- Up to 1M+ transfers/second with large batches
```

**Trade-offs:**

| Aspect | TigerBeetle Approach | Trade-off |
|--------|---------------------|-----------|
| Flexibility | Financial accounting only | Cannot be general-purpose DB |
| Transactions | Non-interactive only | No multi-round-trip transactions |
| Updates | Append-only model | No traditional UPDATE |
| Language | Zig | Smaller ecosystem than C/Rust |
| Deployment | 3 or 6 replicas required | Cannot run single-node |
| Page cache | Bypassed entirely | Must manage own buffers |

**Why This Matters:**

TigerBeetle demonstrates that the traditional separation of concerns (storage vs consensus) can be a liability. By integrating them, TigerBeetle achieves:

1. **Self-healing storage**: Local disk corruption repaired from cluster
2. **Stronger durability**: No silent data loss from bit rot
3. **Clear failure semantics**: Either data is safe, or cluster halts (no corruption)
4. **Simpler recovery**: No complex WAL truncation heuristics

This represents a new paradigm for database design, applying 2018 research that most production databases still haven't adopted.

### 4.10 Summary Table

| System | Torn Page Strategy | In-Place Updates | WAL Amplification | Key Innovation |
|--------|-------------------|------------------|-------------------|----------------|
| PostgreSQL | Full Page Writes | Yes | High | Simple, well-tested |
| MySQL/InnoDB | Doublewrite Buffer | Yes | Low (separate I/O) | Sequential doublewrite |
| SQL Server | Detection/Checksums | Yes | Low | Torn bit patterns |
| Oracle | Storage-level atomics | Yes | Low | Enterprise storage |
| SQLite | Journal/WAL modes | Partial | Medium | Simplicity |
| RocksDB | Immutable SSTs | No | Low | LSM immutability |
| DuckDB | Copy-on-Write | No | Low | Analytics focus |
| FoundationDB | COW B-Trees | No | Low | Distributed COW |
| **TigerBeetle** | **Dual WAL + PAR** | **No (append-only)** | **Low** | **Cluster-based repair** |

**TigerBeetle's Unique Position:**

TigerBeetle is the only system in this comparison that:
1. Uses **two WALs** to distinguish torn writes from bit rot
2. Leverages **Protocol-Aware Recovery** to repair local storage from cluster redundancy
3. Treats storage faults as a **first-class concern** in the consensus protocol
4. Uses **Direct I/O exclusively** to avoid fsync() failure bugs
5. **Hash-chains all data** for cryptographic integrity verification

---

## 5. Modern Hardware and Atomic Writes

### 5.1 The Hardware Evolution

**Historical assumptions:**
- 512-byte sector = atomic write unit
- Database pages >> sector size
- No guarantees above sector level

**Modern reality:**
- NVMe SSDs with 4KB atomic writes
- Some SSDs support 16KB atomic writes
- Intel Optane with byte-addressable atomicity
- Cloud storage with API-level atomicity

### 5.2 Storage Atomic Write Guarantees

**Linux O_ATOMIC (proposed, not standard):**
```c
// Hypothetical API for atomic writes
int fd = open("datafile", O_RDWR | O_ATOMIC);
// All writes up to atomic_max_bytes are atomic
pwrite(fd, page, PAGE_SIZE, offset);
```

**NVMe Atomic Write Unit:**
```
AWUN (Atomic Write Unit Normal): Guaranteed atomic write size
AWUPF (Atomic Write Unit Power Fail): Atomic size during power fail

Modern NVMe typically: AWUN = 4KB, AWUPF = 4KB
Some enterprise: AWUN = 16KB+
```

**Checking your drive (Linux):**
```bash
# Check NVMe atomic write capabilities
nvme id-ns /dev/nvme0n1 -H | grep -i atomic
```

### 5.3 MySQL's Atomic Write Support

MySQL 8.0 can detect and use atomic writes:

```ini
# my.cnf
innodb_use_native_aio = ON
innodb_doublewrite = ON  # Auto-disabled if atomic writes detected

# MySQL auto-detects:
# - Fusion-io directFS
# - Atomic write capable SSDs
# - Some cloud storage
```

**Log message:**
```
[Note] InnoDB: Atomic write detected, disabling doublewrite buffer
```

### 5.4 PostgreSQL and Atomic Writes

**Current state (as of PG 16):**
- No automatic detection of atomic writes
- `full_page_writes = off` is the only option (dangerous!)
- Community discussion ongoing

**Potential future:**
```c
// Hypothetical PostgreSQL addition
bool
XLogNeedFPW(Buffer buffer)
{
    if (io_supports_atomic_write(PageSize))
        return false;  // Storage guarantees atomicity

    return PageGetLSN(page) < RedoRecPtr;
}
```

### 5.5 Cloud Storage Atomicity

**AWS EBS:**
- io2 Block Express: 64KB atomic writes
- gp3: 16KB atomic writes (with some caveats)
- Varies by instance type and configuration

**Google Cloud Persistent Disk:**
- SSD: 16KB atomic writes
- Local SSD: 4KB atomic writes

**Azure Premium SSD:**
- 4KB atomic writes guaranteed

**Implication:** Cloud databases can potentially disable FPW/doublewrite on appropriate storage, but often don't due to complexity and risk.

---

## 6. Checksums and Detection vs Prevention

### 6.1 Detection vs Prevention Strategies

**Prevention strategies:**
- Full Page Writes (prevent by having recovery data)
- Doublewrite Buffer (prevent by having backup copy)
- Copy-on-Write (prevent by never overwriting)
- Atomic writes (prevent at hardware level)

**Detection strategies:**
- Checksums (detect corruption, cannot fix)
- Torn bits (detect partial writes)
- Magic numbers (detect gross corruption)

### 6.2 PostgreSQL Data Checksums

**Enable at cluster creation:**
```bash
initdb --data-checksums -D /var/lib/postgresql/data
# Or
pg_checksums --enable -D /var/lib/postgresql/data  # PG 12+, requires stop
```

**How it works:**
```c
/* From src/include/storage/checksum.h */

/* CRC-32C of page content (excluding pd_checksum field itself) */
uint16
pg_checksum_page(char *page, BlockNumber blkno)
{
    uint32 crc = pg_crc32c(0, page + offset, size);

    /* Include block number to detect wrong-page-at-wrong-location */
    crc = pg_crc32c(crc, &blkno, sizeof(blkno));

    return (uint16) crc;  /* Truncated to 16 bits */
}
```

**Checksum location:**
```
Page Header:
+---------------+
| pd_lsn        | 8 bytes
| pd_checksum   | 2 bytes <- Checksum stored here
| pd_flags      | 2 bytes
| pd_lower      | 2 bytes
| pd_upper      | 2 bytes
| pd_special    | 2 bytes
| pd_pagesize   | 2 bytes
| pd_version    | 2 bytes
+---------------+
```

### 6.3 When Checksums Alone Are Insufficient

**Scenario: Silent corruption without FPW**

```
1. Checkpoint at LSN 100
2. Page modified at LSN 101 (no FPW because full_page_writes = off)
3. Crash during write at LSN 101
4. Page on disk: torn, checksum invalid
5. Recovery reads page -> checksum fails
6. PANIC: Database cannot recover!

PostgreSQL has no way to reconstruct the page without FPW.
```

**Detection without recourse = data loss**

### 6.4 The Checksum + FPW Synergy

```
With both checksums AND full_page_writes:

1. Checkpoint at LSN 100
2. Page modified at LSN 101, FPW written
3. Crash during write at LSN 101
4. Page on disk: torn, checksum invalid
5. Recovery reads page -> checksum fails
6. Recovery finds FPW at LSN 101
7. Restore page from FPW
8. Verify checksum now passes
9. Recovery continues successfully
```

### 6.5 MySQL Checksums

```sql
-- InnoDB checksum algorithm
SET GLOBAL innodb_checksum_algorithm = 'crc32';

-- Options: crc32, strict_crc32, innodb, strict_innodb, none
```

**InnoDB stores checksums at:**
- Beginning of page: old-style checksum
- End of page: CRC-32C

### 6.6 Checksum Overhead

**PostgreSQL:**
- Read overhead: ~1-2% CPU for checksum verification
- Write overhead: ~1-2% CPU for checksum calculation
- Generally recommended: Always enable

**Benchmark:**
```
pgbench -c 10 -T 60

Without checksums: 15,234 TPS
With checksums:    14,998 TPS

Overhead: ~1.5%
```

---

## 7. Why Some Systems Don't Need FPW

### 7.1 LSM-Tree Based Systems

**Key property:** Append-only writes, immutable files

```
RocksDB/LevelDB write path:
1. Append to WAL (sequential, atomic at record level)
2. Insert to MemTable (memory)
3. Flush MemTable to new SST file (new file = atomic creation)
4. Update MANIFEST (atomic file operation)

No step involves modifying an existing data file!
```

**Why it works:**
- SST files written sequentially, then made read-only
- If crash during SST write, file is incomplete -> discard
- MANIFEST only updated after SST fully written
- Recovery: Read MANIFEST, ignore incomplete SST files

### 7.2 Copy-on-Write Systems

**Examples:** ZFS, btrfs, DuckDB, FoundationDB

```
COW write path:
1. Read block at location A
2. Modify in memory
3. Write modified block to NEW location B
4. Update parent pointer atomically
5. Old block at A is now free

At no point is existing data overwritten!
```

**Why it works:**
- Crash before pointer update -> old block still valid
- Crash after pointer update -> new block valid
- No partial states possible

### 7.3 Append-Only Systems

**Examples:** Event sourcing, immutable logs, Kafka

```
Append-only log:
Record 1 | Record 2 | Record 3 | Record 4 | <EOF>
                                          ^
                              Crash here = truncate to Record 3
```

**Recovery:**
- Scan from start or last checkpoint
- Validate each record (checksum/magic)
- Truncate at first invalid record
- All preceding records are valid

### 7.4 In-Memory Systems with Checkpointing

**Examples:** Redis, VoltDB, MemSQL/SingleStore

```
In-memory operation:
- All data in memory
- Periodic snapshots to disk
- Snapshot is atomic (COW or fork-based)

Write path:
1. Modify in memory (atomic at operation level)
2. Append to AOF/WAL
3. Periodic snapshot

Recovery:
1. Load last snapshot
2. Replay WAL since snapshot
```

**Why FPW unnecessary:**
- Memory operations are atomic
- Snapshots use COW (no torn pages)
- WAL is append-only

### 7.5 Systems with Storage-Level Atomicity

**Examples:** Systems on NVMe with large atomic write units

If hardware guarantees atomic 16KB writes and database uses 8KB pages:
- Every page write is atomic
- No torn pages possible
- FPW is pure overhead

**But:** Few systems actually detect and leverage this today.

---

## 8. Trade-off Analysis

### 8.1 Write Amplification Comparison

```
WRITE AMPLIFICATION FACTORS:

PostgreSQL (B-tree, FPW on):
  User write: 100 bytes
  Page modification: 8KB page touch
  WAL: 100 bytes + 8KB FPW (first after checkpoint) = 8.1KB
  Data file: 8KB
  Total write amplification: 80x-160x first write, ~1.2x subsequent

MySQL/InnoDB (B-tree, doublewrite):
  User write: 100 bytes
  Page modification: 16KB page touch
  WAL: ~100 bytes
  Doublewrite: 16KB
  Data file: 16KB
  Total write amplification: ~320x first write (but sequential!)

RocksDB (LSM-tree):
  User write: 100 bytes
  WAL: ~100 bytes
  MemTable: memory only
  Flush: amortized across many writes
  Compaction: additional amplification (10-30x total)
  Total write amplification: 10-30x (but all sequential)

DuckDB (COW, analytics):
  User write: typically bulk
  New blocks: proportional to modified data
  No in-place modification
  Total write amplification: ~1-2x for bulk writes
```

### 8.2 Recovery Time Comparison

```
RECOVERY TIME (for 100GB database, 1GB WAL):

PostgreSQL:
- Scan 1GB WAL
- Apply FPW images (potentially many GBs of page images)
- Redo remaining changes
- ~5-15 minutes typical

MySQL/InnoDB:
- Check doublewrite buffer
- Repair any torn pages
- Apply redo log
- ~2-5 minutes typical

RocksDB:
- Read MANIFEST
- Verify SST files
- Replay WAL (small, since SSTs capture most data)
- ~30 seconds to 2 minutes typical

COW systems (ZFS, btrfs):
- No recovery needed
- Just mount last consistent state
- ~seconds
```

### 8.3 Space Overhead Comparison

```
SPACE OVERHEAD:

PostgreSQL FPW:
- WAL size increase: 2-10x
- Additional archive storage
- Longer replication lag

MySQL doublewrite:
- Fixed 2MB buffer
- No WAL size increase
- Minimal space overhead

LSM-tree systems:
- Space amplification from levels: 10-20x
- No torn page overhead per se

COW systems:
- Space for old versions until garbage collected
- Typically 10-30% overhead
```

### 8.4 Complexity Comparison

```
IMPLEMENTATION COMPLEXITY:

PostgreSQL FPW:
  + Simple concept
  + Well-tested
  - Performance overhead
  - WAL management complexity

MySQL doublewrite:
  + Separate from redo log
  + Small fixed overhead
  - Extra I/O path
  - Sequential write dependency

LSM-tree (no torn page handling):
  + Naturally atomic
  - Compaction complexity
  - Read amplification

COW:
  + Elegant
  + Snapshot capability
  - Garbage collection
  - Space management
```

### 8.5 Decision Matrix

| Workload | Recommended Approach | Why |
|----------|---------------------|-----|
| OLTP, random writes | FPW or Doublewrite | Must handle torn pages |
| Analytics, bulk writes | COW or LSM | Less critical, bulk atomic |
| Write-heavy, logs | LSM-tree | Append-only natural fit |
| Read-heavy | B-tree + FPW | Read performance priority |
| Mixed | Depends on ratio | Profile and decide |
| NVMe with atomics | Consider disabling FPW | Hardware handles it |

---

## 9. State-of-the-Art Research

### 9.1 Key Academic Papers

**Foundational:**

1. **"ARIES: A Transaction Recovery Method Supporting Fine-Granularity Locking"**
   - Authors: C. Mohan, D. Haderle, B. Lindsay, H. Pirahesh, P. Schwarz
   - Venue: ACM TODS, 1992
   - Contribution: Definitive WAL-based recovery algorithm
   - Key insight: LSN-based redo/undo with physiological logging

2. **"The Log-Structured Merge-Tree (LSM-Tree)"**
   - Authors: Patrick O'Neil, Edward Cheng, Dieter Gawlick, Elizabeth O'Neil
   - Venue: Acta Informatica, 1996
   - Contribution: Alternative to B-trees that avoids in-place updates
   - Relevance: No torn page problem by design

3. **"The Five-Minute Rule for Trading Memory for Disc Accesses"**
   - Authors: Jim Gray, Gianfranco Putzolu
   - Venue: SIGMOD, 1987
   - Contribution: Economic analysis of buffer management
   - Relevance: Foundation for checkpoint interval decisions

**Modern Research:**

4. **"Understanding the Reliability of Solid State Drives"**
   - Authors: Bianca Schroeder, Arif Merchant, et al.
   - Venue: FAST, 2016 (Google study)
   - Key finding: SSD failure modes differ from HDDs
   - Relevance: Modern assumptions about atomic writes

5. **"Designing Access Methods: The RUM Conjecture"**
   - Authors: Manos Athanassoulis, Michael Kester, et al.
   - Venue: EDBT, 2016
   - Key insight: Read/Update/Memory tradeoffs are fundamental
   - Relevance: FPW is an update overhead

6. **"SILK: Preventing Latency Spikes in Log-Structured Merge Key-Value Stores"**
   - Authors: Oana Balmau, Diego Didona, et al.
   - Venue: USENIX ATC, 2019
   - Contribution: LSM optimizations for tail latency
   - Relevance: Making LSM more practical for OLTP

7. **"WiscKey: Separating Keys from Values in SSD-Conscious Storage"**
   - Authors: Lanyue Lu, Thanumalayan S. Pillai, et al.
   - Venue: FAST, 2016
   - Contribution: Value separation for LSM trees
   - Relevance: Reduces write amplification dramatically

8. **"The Unwritten Contract of Solid State Drives"**
   - Authors: Jun He, Sudarsun Kannan, et al.
   - Venue: EuroSys, 2017
   - Key finding: SSD atomic write guarantees are complex
   - Relevance: Don't blindly trust hardware atomicity claims

9. **"Protocol-Aware Recovery for Consensus-Based Storage"** (Best Paper)
   - Authors: Ramnatthan Alagappan, Aishwarya Ganesan, Eric Lee, Aws Albarghouthi, Vijay Chidambaram, Andrea C. Arpaci-Dusseau, Remzi H. Arpaci-Dusseau
   - Venue: FAST, 2018
   - Contribution: Integrating storage fault handling into consensus protocols
   - Key insight: Traditional databases conflate bit rot with crashes, losing committed data
   - Impact: Foundation for TigerBeetle's storage design
   - URL: https://www.usenix.org/conference/fast18/presentation/alagappan

10. **"Can Applications Recover from fsync Failures?"**
    - Authors: Anthony Rebello, Yuvraj Patel, Ramnatthan Alagappan, Andrea C. Arpaci-Dusseau, Remzi H. Arpaci-Dusseau
    - Venue: USENIX ATC, 2020
    - Key finding: After fsync() EIO error, kernel marks pages clean; retry doesn't work
    - Impact: Explains why TigerBeetle uses Direct I/O exclusively
    - Relevance: PostgreSQL and MySQL both had (and may still have) related bugs

### 9.2 Emerging Techniques

**1. Persistent Memory (PMem):**
```
Intel Optane / CXL Memory:
- Byte-addressable persistent storage
- Sub-microsecond latency
- Atomic 8-byte writes guaranteed

Implications:
- Fine-grained logging possible
- No torn page problem at 8-byte level
- New recovery algorithms needed
```

**Research:**
- "RECIPE: Converting Concurrent DRAM Indexes to Persistent-Memory Indexes" (SOSP 2019)
- "Zen: A High-Throughput Log-Free OLTP Engine" (VLDB 2021)

**2. Asynchronous I/O + io_uring:**
```
Linux io_uring:
- Kernel-bypass I/O submission
- Batching and polling modes
- IORING_OP_WRITE_FIXED for atomic writes
```

**Implication:** Efficient batched fsync enables more frequent durability points.

**3. Learned Checkpointing:**
```
Use ML to predict:
- When to checkpoint (minimize recovery time vs performance)
- Which pages to prioritize
- Optimal FPW compression settings
```

**Research:** Emerging area, no definitive papers yet.

**4. Hardware Acceleration:**
```
Computational storage:
- Offload checksum calculation to SSD controller
- Transparent compression at storage layer
- Hardware-assisted atomic writes
```

**Products:** Samsung SmartSSD, ScaleFlux, Pliops

### 9.3 Industry Innovations

**MySQL 8.0 Doublewrite Buffer v2:**
- Parallel doublewrite threads
- Dedicated files instead of ibdata1
- Better scalability

**PostgreSQL 15+ WAL Improvements:**
- Improved FPW compression
- WAL record packing
- Parallel recovery

**Amazon Aurora:**
- Storage-level replication
- Log-only replication to replicas
- 6-way quorum for durability
- Avoids traditional torn page handling

**Google Spanner:**
- Truetime for global consistency
- Paxos-based replication
- Storage layer handles atomicity

**TigerBeetle:**
- Dual WAL architecture distinguishes torn writes from bit rot
- Protocol-Aware Recovery heals local storage from cluster
- Direct I/O + io_uring for correct durability semantics
- Hash-chained, immutable data model
- Written in Zig with static memory allocation
- Represents a new paradigm: consensus-integrated storage recovery
- Open source: https://github.com/tigerbeetle/tigerbeetle

### 9.4 Future Directions

**1. Hardware atomicity becoming standard:**
- NVMe 2.0 specification includes atomic write semantics
- Cloud providers offering larger atomic write guarantees
- May eventually make FPW/doublewrite obsolete

**2. Unified storage engines:**
- Bridging B-tree and LSM approaches
- Bw-tree (Microsoft), Silt, etc.
- Trying to get benefits of both

**3. Serverless databases:**
- Disaggregated storage and compute
- Storage layer handles durability
- Compute nodes are stateless

---

## 10. Practical Recommendations

### 10.1 For pg_arrow Project

Since pg_arrow reads PostgreSQL data files directly, understanding FPW is crucial:

**Reading a stopped PostgreSQL database:**
- Pages on disk are consistent (checkpoint completed before stop)
- No torn page concerns for readers
- Check `data_checksums` to validate pages

**Reading from a crashed database:**
- Some pages may be torn
- WAL replay necessary for consistency
- Either run PostgreSQL recovery first, or implement WAL replay

**Code implications:**
```rust
fn validate_page(page: &[u8], block_num: u32) -> Result<(), Error> {
    // 1. Check page header magic
    let header = PageHeaderData::parse(page)?;

    // 2. Verify checksum if enabled
    if data_checksums_enabled {
        let expected = header.pd_checksum;
        let actual = pg_checksum_page(page, block_num);
        if expected != actual {
            return Err(Error::ChecksumMismatch {
                block: block_num,
                expected,
                actual,
            });
        }
    }

    // 3. Validate LSN is reasonable
    if header.pd_lsn > current_redo_ptr {
        // Page may be from future (clock skew) or corrupted
        warn!("Page LSN ahead of redo pointer");
    }

    Ok(())
}
```

### 10.2 For PostgreSQL Users/DBAs

**Always enable:**
```sql
-- postgresql.conf
full_page_writes = on     -- DO NOT DISABLE
data_checksums = on       -- Enable at initdb

-- For performance with FPW:
wal_compression = on      -- Reduces WAL size
checkpoint_timeout = 15min -- Longer = fewer FPW
max_wal_size = 4GB        -- Allow more between checkpoints
```

**Monitoring:**
```sql
-- Check FPW frequency
SELECT pg_stat_wal.* FROM pg_stat_wal;

-- Estimate FPW overhead
SELECT
    wal_bytes,
    wal_fpi  -- Full page images written
FROM pg_stat_wal;

-- Check checksum status
SHOW data_checksums;
SELECT pg_control_checkpoint();
```

### 10.3 For Database System Designers

**Questions to answer:**

1. **Does your system do in-place updates?**
   - Yes -> Need torn page protection
   - No (LSM/COW) -> Probably don't need it

2. **What is your page size vs hardware atomic unit?**
   - Page size <= atomic unit -> No protection needed
   - Page size > atomic unit -> Need protection

3. **Can you guarantee storage atomicity?**
   - Enterprise storage with BBU -> Maybe skip
   - Consumer SSDs -> Don't trust blindly
   - Cloud storage -> Check provider docs

4. **What is your recovery time target?**
   - Seconds -> COW or LSM preferred
   - Minutes acceptable -> FPW is fine
   - Long recovery OK -> Can be more aggressive

5. **What is your write amplification budget?**
   - Minimal -> LSM or COW
   - Some overhead OK -> B-tree + FPW
   - Sequential is fine -> Doublewrite buffer

### 10.4 Testing Your Understanding

**Thought experiments:**

1. *"If I disable FPW and my system crashes during a page write, what happens on recovery?"*
   - Answer: PostgreSQL tries to redo changes to a torn page, potentially propagating corruption or failing with checksum errors.

2. *"Why can RocksDB skip torn page protection?"*
   - Answer: It never modifies data in place. WAL is append-only (truncate on corruption), SST files are immutable (discard incomplete files).

3. *"If my NVMe supports 16KB atomic writes and PostgreSQL uses 8KB pages, can I safely disable FPW?"*
   - Answer: Theoretically yes, but PostgreSQL has no mechanism to verify or leverage this. Dangerous without kernel/storage stack verification.

4. *"Why does the doublewrite buffer use sequential writes?"*
   - Answer: Sequential writes are faster, especially on HDDs. The doublewrite buffer is flushed before random data file writes.

---

## 11. Glossary

| Term | Definition |
|------|------------|
| **Torn Page** | A page partially written due to crash, containing mix of old and new data |
| **FPW** | Full Page Write - PostgreSQL's torn page protection mechanism |
| **WAL** | Write-Ahead Log - sequential log of all changes for durability |
| **LSN** | Log Sequence Number - monotonically increasing identifier for log position |
| **Checkpoint** | Point at which all dirty buffers are flushed to disk |
| **REDO** | Process of re-applying committed changes during recovery |
| **UNDO** | Process of rolling back uncommitted changes during recovery |
| **COW** | Copy-on-Write - never modify in place, always write to new location |
| **LSM** | Log-Structured Merge-tree - write-optimized structure with immutable files |
| **Doublewrite** | InnoDB's torn page protection via redundant writes |
| **AWUN** | Atomic Write Unit Normal - NVMe guaranteed atomic write size |
| **BBU** | Battery-Backed Unit - hardware ensuring cache durability |
| **PAR** | Protocol-Aware Recovery - integrating storage faults into consensus |
| **Dual WAL** | TigerBeetle's approach using two logs to distinguish torn writes from bit rot |
| **Hash-chain** | Cryptographic linking where each record includes checksum of previous |
| **Direct I/O** | Bypassing OS page cache with O_DIRECT flag |
| **io_uring** | Linux kernel interface for efficient async I/O without syscalls |
| **Bit rot** | Gradual corruption of data on storage media over time |
| **Gray failure** | Partial failure where component is slow but doesn't report errors |
| **NACK** | Negative acknowledgment - explicitly stating "I never received this" |

---

## 12. Bibliography

### Core Papers

1. Mohan, C. et al. "ARIES: A Transaction Recovery Method Supporting Fine-Granularity Locking and Partial Rollbacks Using Write-Ahead Logging." ACM TODS 17(1), 1992.

2. O'Neil, P. et al. "The Log-Structured Merge-Tree (LSM-Tree)." Acta Informatica 33(4), 1996.

3. Gray, J. and Reuter, A. "Transaction Processing: Concepts and Techniques." Morgan Kaufmann, 1993.

4. Hellerstein, J., Stonebraker, M., Hamilton, J. "Architecture of a Database System." Foundations and Trends in Databases 1(2), 2007.

### System-Specific

5. PostgreSQL Global Development Group. "PostgreSQL Documentation: Write-Ahead Logging." https://www.postgresql.org/docs/current/wal.html

6. Schwartz, B. et al. "High Performance MySQL." O'Reilly, 4th edition, 2021. (Chapters on InnoDB)

7. RocksDB Team. "RocksDB Wiki: Write-Ahead-Log." https://github.com/facebook/rocksdb/wiki

### Storage Research

8. Schroeder, B. et al. "Flash Reliability in Production: The Expected and the Unexpected." FAST 2016.

9. He, J. et al. "The Unwritten Contract of Solid State Drives." EuroSys 2017.

10. Lu, L. et al. "WiscKey: Separating Keys from Values in SSD-conscious Storage." FAST 2016.

### Modern Systems

11. Verbitski, A. et al. "Amazon Aurora: Design Considerations for High Throughput Cloud-Native Relational Databases." SIGMOD 2017.

12. Corbett, J. et al. "Spanner: Google's Globally Distributed Database." OSDI 2012.

13. Diaconu, C. et al. "Hekaton: SQL Server's Memory-Optimized OLTP Engine." SIGMOD 2013.

### Protocol-Aware Recovery and Storage Faults

14. Alagappan, R. et al. "Protocol-Aware Recovery for Consensus-Based Storage." FAST 2018. (Best Paper)

15. Rebello, A. et al. "Can Applications Recover from fsync Failures?" USENIX ATC 2020.

16. Pillai, T. et al. "All File Systems Are Not Created Equal: On the Complexity of Crafting Crash-Consistent Applications." OSDI 2014.

### TigerBeetle-Specific

17. TigerBeetle Team. "TigerBeetle Architecture Documentation." https://github.com/tigerbeetle/tigerbeetle/blob/main/docs/ARCHITECTURE.md

18. TigerBeetle Team. "TigerBeetle Safety Documentation." https://docs.tigerbeetle.com/concepts/safety/

19. Kingsbury, K. "Jepsen: TigerBeetle 0.16.11." https://jepsen.io/analyses/tigerbeetle-0.16.11 (2025)

---

## See Also

- [WAL-Based Incremental Conversion](@/notes/database/wal_incremental_conversion.md) — Builds on WAL fundamentals for CDC and incremental Arrow conversion
- [LSM Trees](@/notes/database/lsm_trees.md) — Write-optimized storage that uses WAL for durability before memtable flush
- [Filesystem Design](@/notes/os/filesystem_design.md) — Journaling, crash consistency, and the storage layer WAL writes depend on
- [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) — Testing methodologies for verifying crash recovery correctness (TigerBeetle, FoundationDB)
- [Disaggregated Storage](@/notes/database/disaggregated_storage.md) — Aurora, Neon, and others that redesign WAL propagation for distributed storage

---

*Last updated: 2026-01-28*
