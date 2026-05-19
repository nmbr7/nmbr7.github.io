+++
title = "Mongodb Wiredtiger Internals"
date = 2026-02-10
[taxonomies]
tags = ["wiredtiger", "mvcc", "btree", "checkpointing", "replication", "mongodb", "sharding"]
+++


# MongoDB and WiredTiger Database Internals: Expert Guide

## Table of Contents

1. [Overview and Architecture](#1-overview-and-architecture)
2. [WiredTiger Storage Engine Deep Dive](#2-wiredtiger-storage-engine-deep-dive)
3. [MVCC Implementation](#3-mvcc-implementation)
4. [Transaction System](#4-transaction-system)
5. [Journaling and Write-Ahead Logging](#5-journaling-and-write-ahead-logging)
6. [Checkpoint System](#6-checkpoint-system)
7. [Concurrency Control](#7-concurrency-control)
8. [Cache and Memory Management](#8-cache-and-memory-management)
9. [Replication Internals](#9-replication-internals)
10. [Compression and Block Management](#10-compression-and-block-management)
11. [Query Execution](#11-query-execution)
12. [Sharding Architecture](#12-sharding-architecture)
13. [Comparisons with Other Systems](#13-comparisons-with-other-systems)
14. [References](#14-references)

---

## 1. Overview and Architecture

### 1.1 MongoDB Architecture Layers

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLIENT APPLICATIONS                              │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           MONGOS (Router)                                │
│  - Query routing          - Chunk migration        - Config management   │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
┌───────────────────────┐ ┌───────────────────────┐ ┌───────────────────────┐
│      MONGOD           │ │      MONGOD           │ │      MONGOD           │
│   (Shard/Replica)     │ │   (Shard/Replica)     │ │   (Shard/Replica)     │
├───────────────────────┤ ├───────────────────────┤ ├───────────────────────┤
│  Query Engine         │ │  Query Engine         │ │  Query Engine         │
│  ├─ Parser            │ │  ├─ Parser            │ │  ├─ Parser            │
│  ├─ Optimizer         │ │  ├─ Optimizer         │ │  ├─ Optimizer         │
│  └─ Executor          │ │  └─ Executor          │ │  └─ Executor          │
├───────────────────────┤ ├───────────────────────┤ ├───────────────────────┤
│  Storage API          │ │  Storage API          │ │  Storage API          │
│  (KVEngine interface) │ │  (KVEngine interface) │ │  (KVEngine interface) │
├───────────────────────┤ ├───────────────────────┤ ├───────────────────────┤
│  WiredTiger Engine    │ │  WiredTiger Engine    │ │  WiredTiger Engine    │
│  ├─ B-Tree            │ │  ├─ B-Tree            │ │  ├─ B-Tree            │
│  ├─ Cache             │ │  ├─ Cache             │ │  ├─ Cache             │
│  ├─ Journal           │ │  ├─ Journal           │ │  ├─ Journal           │
│  └─ Checkpoint        │ │  └─ Checkpoint        │ │  └─ Checkpoint        │
└───────────────────────┘ └───────────────────────┘ └───────────────────────┘
```

### 1.2 Storage Engine Abstraction

MongoDB uses a pluggable storage engine architecture:

```cpp
// From src/mongo/db/storage/kv/kv_engine.h
class KVEngine {
public:
    // Create a new RecordStore (collection)
    virtual Status createRecordStore(OperationContext* opCtx,
                                     StringData ns,
                                     StringData ident,
                                     const CollectionOptions& options) = 0;

    // Create a new SortedDataInterface (index)
    virtual Status createSortedDataInterface(OperationContext* opCtx,
                                             StringData ident,
                                             const IndexDescriptor* desc) = 0;

    // Checkpoint and recovery
    virtual void checkpoint() = 0;
    virtual Status recoverToStableTimestamp(Timestamp stableTimestamp) = 0;

    // Timestamp management
    virtual void setStableTimestamp(Timestamp stableTimestamp) = 0;
    virtual void setOldestTimestamp(Timestamp oldestTimestamp) = 0;
};
```

---

## 2. WiredTiger Storage Engine Deep Dive

### 2.1 Core Architecture

WiredTiger is a high-performance, embedded database engine acquired by MongoDB in 2014. It became the default storage engine in MongoDB 3.2.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      WIREDTIGER ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                        API LAYER                                 │    │
│  │  WT_CONNECTION, WT_SESSION, WT_CURSOR                           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                    │                                     │
│  ┌─────────────────────────────────┼─────────────────────────────────┐  │
│  │                         BTREE LAYER                               │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐ │  │
│  │  │ Row Store     │  │ Column Store  │  │ LSM Trees             │ │  │
│  │  │ (default)     │  │ (append-only) │  │ (write-optimized)     │ │  │
│  │  └───────────────┘  └───────────────┘  └───────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                     │
│  ┌─────────────────────────────────┼─────────────────────────────────┐  │
│  │                        CACHE LAYER                                │  │
│  │  ┌─────────────────────────────────────────────────────────────┐ │  │
│  │  │  In-Memory Pages  │  Eviction  │  Hazard Pointers          │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                    │                                     │
│  ┌──────────────────┬──────────────┴───────────────┬─────────────────┐  │
│  │   BLOCK MGR      │      TRANSACTION MGR         │    LOG (WAL)    │  │
│  │  ┌────────────┐  │  ┌─────────────────────────┐ │  ┌───────────┐  │  │
│  │  │ Allocation │  │  │ MVCC │ Snapshots │ Ckpt │ │  │ Journal   │  │  │
│  │  │ Compression│  │  └─────────────────────────┘ │  │ Recovery  │  │  │
│  │  └────────────┘  │                              │  └───────────┘  │  │
│  └──────────────────┴──────────────────────────────┴─────────────────┘  │
│                                    │                                     │
│                                    ▼                                     │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                         FILE SYSTEM                               │  │
│  │   *.wt (data)  │  WiredTiger.wt (metadata)  │  journal/*         │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 B-Tree Implementation

WiredTiger uses a **copy-on-write (COW) B-tree** design, fundamentally different from traditional in-place update B-trees.

#### 2.2.1 Page Types

```c
// From src/include/btmem.h
#define WT_PAGE_INVALID      0  // Invalid page type
#define WT_PAGE_BLOCK_MANAGER 1 // Block-manager metadata
#define WT_PAGE_COL_FIX      2  // Column-store fixed-length leaf
#define WT_PAGE_COL_INT      3  // Column-store internal page
#define WT_PAGE_COL_VAR      4  // Column-store variable-length leaf
#define WT_PAGE_OVFL         5  // Overflow page
#define WT_PAGE_ROW_INT      6  // Row-store internal page
#define WT_PAGE_ROW_LEAF     7  // Row-store leaf page (most common)
```

#### 2.2.2 Page Structure (In-Memory)

```c
// Simplified from src/include/btmem.h
struct __wt_page {
    /* Page type and flags */
    uint8_t type;                    // WT_PAGE_ROW_LEAF, etc.
    uint8_t flags;

    /* Memory accounting */
    size_t memory_footprint;         // Total memory for this page

    /* Page content (union based on type) */
    union {
        /* Row-store leaf page */
        struct {
            WT_ROW *row;             // Array of key/value pairs
            uint32_t entries;        // Number of entries
        } row_leaf;

        /* Row-store internal page */
        struct {
            WT_REF *intl;            // Array of child references
            uint32_t entries;
        } row_int;

        /* Column-store */
        struct {
            WT_COL *col;
            uint64_t recno;          // Starting record number
        } col_leaf;
    } u;

    /* Parent reference */
    WT_REF *parent_ref;              // How parent references this page

    /* Modification tracking */
    WT_PAGE_MODIFY *modify;          // Non-NULL if page is dirty

    /* Read generation for LRU */
    uint64_t read_gen;               // Used for eviction decisions
};
```

#### 2.2.3 Row Entry Structure

```c
// Each row in a leaf page
struct __wt_row {
    void *key;                       // Key data (prefix-compressed)
    // Value is accessed through WT_ROW_VALUE macro
    // For MVCC, value points to WT_UPDATE chain
};

// Internal page child reference
struct __wt_ref {
    WT_PAGE *page;                   // Pointer to child (if in memory)
    void *addr;                      // On-disk address (if not in memory)
    uint8_t state;                   // WT_REF_DISK, WT_REF_MEM, etc.
    WT_PAGE_DELETED *page_del;       // Fast-truncate information
};

// Reference states
#define WT_REF_DISK     0            // Page is on disk
#define WT_REF_DELETED  1            // Page is deleted
#define WT_REF_LOCKED   2            // Page is being read/evicted
#define WT_REF_MEM      3            // Page is in memory
#define WT_REF_SPLIT    4            // Page has been split
```

#### 2.2.4 Copy-on-Write Mechanism

```
Traditional B-tree (in-place update):
┌─────────┐                    ┌─────────┐
│ Page A  │  ──update──►      │ Page A' │  (same location, modified)
│ v=100   │                    │ v=200   │
└─────────┘                    └─────────┘

WiredTiger COW B-tree:
┌─────────┐                    ┌─────────┐
│ Page A  │  ──update──►      │ Page A  │  (original, immutable)
│ v=100   │                    │ v=100   │
└─────────┘                    └─────────┘
                                    │
                               ┌────┴────┐
                               │ Page A' │  (new copy at new location)
                               │ v=200   │
                               └─────────┘

Benefits:
- No torn pages (atomic at page level)
- Easy snapshots (just keep old page)
- Readers never block writers
- Crash recovery is simpler
```

#### 2.2.5 Page Splits

```
Before split (leaf page too full):
┌──────────────────────────────────────────┐
│              Parent Page                  │
│  [... key_A → ref_A, key_B → ref_B ...]  │
└────────────────────┬─────────────────────┘
                     │
              ┌──────┴──────┐
              │  Leaf Page  │
              │ (OVERFLOW!) │
              │ k1,k2,k3... │
              └─────────────┘

After split:
┌──────────────────────────────────────────────────────┐
│                    Parent Page                        │
│  [...key_A→ref_A, key_split→ref_new, key_B→ref_B...] │
└────────────────────┬────────────────┬────────────────┘
                     │                │
              ┌──────┴──────┐  ┌──────┴──────┐
              │  Leaf Left  │  │ Leaf Right  │
              │   k1, k2    │  │  k3, k4...  │
              └─────────────┘  └─────────────┘

Split Algorithm:
1. Allocate new page
2. Copy half the entries to new page
3. Update parent to add new child reference
4. If parent overflows, recursively split parent
5. Mark old page as obsolete (will be reclaimed at checkpoint)
```

### 2.3 Prefix Compression

WiredTiger uses prefix compression for keys to reduce memory and disk usage:

```
Keys: "user:1000", "user:1001", "user:1002", "user:1003"

Without prefix compression:
[user:1000][user:1001][user:1002][user:1003] = 40 bytes

With prefix compression:
[user:100][0|1][1|1][2|1][3|1]
    ^       ^    ^    ^    ^
    |       |    |    |    |
  prefix  suffix (1 byte each showing delta from prefix)
= ~15 bytes

Implementation:
- First key stored in full
- Subsequent keys store:
  - Prefix length (bytes shared with previous key)
  - Suffix (remaining bytes)
```

### 2.4 Hazard Pointers

Hazard pointers enable lock-free concurrent access to pages:

```c
// From src/include/session.h
struct __wt_session_impl {
    WT_HAZARD *hazard;           // Array of hazard pointers
    uint32_t hazard_inuse;       // Number of active hazard pointers
    uint32_t hazard_size;        // Array capacity
};

struct __wt_hazard {
    WT_REF *ref;                 // Protected page reference
    // Additional debug info in debug builds
};
```

**Algorithm:**

```
Thread A (reader):                    Thread B (evictor):
─────────────────                    ──────────────────
1. Want to access page P
2. hp[slot] = P
3. memory_barrier()
4. if (P still valid):
   - Read page P safely              1. Want to evict page P
   ...                               2. Check all hazard pointers
5. hp[slot] = NULL                   3. if (P in any hazard pointer):
                                        - Skip P, try another page
                                     4. else:
                                        - Safe to evict P
```

**Why this works:**
- Writer (evictor) always checks hazard pointers before freeing
- Reader always sets hazard pointer before dereferencing
- Memory barrier ensures proper ordering
- No locks needed - wait-free for readers

---

## 3. MVCC Implementation

### 3.1 Update Chain Architecture

WiredTiger implements MVCC through update chains - linked lists of versions attached to each key:

```
Document key: "user:1000"

In-memory structure:
┌─────────────────────────────────────────────────────────────────────────┐
│                           WT_ROW                                         │
│  key_ptr ──────► "user:1000"                                            │
│  value_ptr ─────┐                                                        │
└─────────────────┼────────────────────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│      WT_UPDATE          │     │      WT_UPDATE          │
│  ┌───────────────────┐  │     │  ┌───────────────────┐  │
│  │ next ─────────────┼──┼────►│  │ next ─────────────┼──┼───► ...
│  │ txnid: 2500       │  │     │  │ txnid: 2450       │  │
│  │ start_ts: (10, 5) │  │     │  │ start_ts: (10, 2) │  │
│  │ durable_ts:(10,5) │  │     │  │ durable_ts:(10,2) │  │
│  │ type: STANDARD    │  │     │  │ type: STANDARD    │  │
│  │ size: 128         │  │     │  │ size: 128         │  │
│  │ data: {x:300,...} │  │     │  │ data: {x:200,...} │  │
│  └───────────────────┘  │     │  └───────────────────┘  │
└─────────────────────────┘     └─────────────────────────┘
        (newest)                        (older)
```

### 3.2 Update Structure

```c
// From src/include/btmem.h
struct __wt_update {
    volatile uint64_t txnid;     // Transaction that created this update

    wt_timestamp_t start_ts;     // Start timestamp
    wt_timestamp_t durable_ts;   // Durable timestamp (for replication)

    WT_UPDATE *next;             // Next older version

    uint32_t size;               // Data size
    uint8_t type;                // Update type
    uint8_t flags;

    // Data follows immediately after structure
    uint8_t data[];
};

// Update types
#define WT_UPDATE_STANDARD   0   // Normal value
#define WT_UPDATE_DELETED    1   // Tombstone (delete marker)
#define WT_UPDATE_RESERVE    2   // Reserved for future update
#define WT_UPDATE_MODIFY     3   // Delta modification
```

### 3.3 Visibility Rules

```c
// Simplified visibility check from src/txn/txn.c
static inline bool
__wt_txn_upd_visible(WT_SESSION_IMPL *session, WT_UPDATE *upd)
{
    WT_TXN *txn = session->txn;

    // 1. Updates from our own transaction are always visible
    if (upd->txnid == txn->id)
        return true;

    // 2. Check transaction ID visibility
    if (!__wt_txn_visible_id(session, upd->txnid))
        return false;

    // 3. Check timestamp visibility (if using timestamps)
    if (txn->read_timestamp != WT_TS_NONE) {
        if (upd->start_ts > txn->read_timestamp)
            return false;
    }

    return true;
}

// Transaction ID visibility
static inline bool
__wt_txn_visible_id(WT_SESSION_IMPL *session, uint64_t id)
{
    WT_TXN *txn = session->txn;

    // Committed before our snapshot started
    if (id < txn->snap_min)
        return true;

    // Started after our snapshot
    if (id > txn->snap_max)
        return false;

    // In our snapshot's list of concurrent transactions
    if (__wt_txn_id_in_snapshot(txn, id))
        return false;  // Not visible (concurrent)

    return true;  // Committed during our snapshot creation
}
```

### 3.4 Snapshot Isolation

```
Transaction Timeline:
────────────────────────────────────────────────────────────────────────►
time
     t1         t2         t3         t4         t5         t6
     │          │          │          │          │          │
     │          │          │          │          │          │
    TX1       TX2        TX1        TX3        TX2        TX3
   begin     begin     commit     begin      commit    commit

Snapshots:
─────────────────────────────────────────────────────────────────────────
TX1 snapshot at t1: { snap_min=1, snap_max=1, active=[] }
TX2 snapshot at t2: { snap_min=1, snap_max=2, active=[1] }
TX3 snapshot at t4: { snap_min=2, snap_max=3, active=[2] }

Visibility Matrix:
                TX1 sees    TX2 sees    TX3 sees
TX1's writes      Yes         No*         Yes
TX2's writes      No          Yes         No*
TX3's writes      No          No          Yes

* Not visible because transaction was concurrent at snapshot time
```

### 3.5 Timestamps in MongoDB

MongoDB uses hybrid logical timestamps for causal consistency:

```c
// Timestamp structure
typedef struct {
    uint32_t seconds;    // Seconds since Unix epoch
    uint32_t increment;  // Tie-breaker within same second
} wt_timestamp_t;

// Timestamp ordering
// (10, 5) < (10, 6) < (11, 0) < (11, 1)
```

**Timestamp Types:**

| Timestamp | Purpose |
|-----------|---------|
| `commit_timestamp` | When transaction logically committed |
| `durable_timestamp` | When transaction became durable on majority |
| `read_timestamp` | Point-in-time for snapshot reads |
| `oldest_timestamp` | Earliest timestamp any reader might need |
| `stable_timestamp` | Latest timestamp safe for checkpointing |

```
Timeline with Timestamps:
────────────────────────────────────────────────────────────────────────►
time (seconds.increment)

     (10,0)     (10,1)     (10,2)     (11,0)     (11,1)
        │          │          │          │          │
        │          │          │          │          │
     oldest    stable      read     commit    durable
    timestamp  timestamp  timestamp timestamp timestamp
        │          │          │          │          │
        ▼          ▼          ▼          ▼          ▼
    ┌──────────────────────────────────────────────────┐
    │  Data visible for   │        │   New data       │
    │  historical reads   │ ckpt   │   (not yet       │
    │                     │ safe   │   durable)       │
    └──────────────────────────────────────────────────┘
```

---

## 4. Transaction System

### 4.1 Transaction Lifecycle

```c
// Transaction states
typedef enum {
    WT_TXN_NONE,        // No transaction
    WT_TXN_RUNNING,     // Transaction in progress
    WT_TXN_COMMIT,      // Committing
    WT_TXN_ERROR,       // Error state
    WT_TXN_ROLLBACK     // Rolling back
} WT_TXN_STATE;

// Transaction structure (simplified)
struct __wt_txn {
    uint64_t id;                    // Transaction ID
    WT_TXN_STATE state;

    // Snapshot
    uint64_t snap_min;              // Oldest active txn at snapshot
    uint64_t snap_max;              // Newest txn at snapshot
    uint64_t *snapshot;             // Array of active txn IDs
    uint32_t snapshot_count;

    // Timestamps
    wt_timestamp_t read_timestamp;
    wt_timestamp_t commit_timestamp;
    wt_timestamp_t durable_timestamp;

    // Modifications tracking
    WT_TXN_OP *mod;                 // Array of operations
    size_t mod_count;

    // Isolation level
    uint32_t isolation;
};
```

### 4.2 Transaction Operations

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     TRANSACTION LIFECYCLE                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. BEGIN TRANSACTION                                                    │
│     ├─ Allocate transaction ID                                          │
│     ├─ Take snapshot of active transactions                             │
│     ├─ Set read_timestamp (if specified)                                │
│     └─ State = WT_TXN_RUNNING                                           │
│                                                                          │
│  2. OPERATIONS (INSERT/UPDATE/DELETE)                                    │
│     ├─ Create WT_UPDATE with txnid                                      │
│     ├─ Link into update chain (atomic CAS)                              │
│     ├─ Record operation in txn->mod array                               │
│     └─ Write to journal (group commit buffer)                           │
│                                                                          │
│  3. COMMIT                                                               │
│     ├─ Set commit_timestamp                                             │
│     ├─ Validate no write-write conflicts                                │
│     ├─ Make updates visible (set durable_timestamp)                     │
│     ├─ Wait for journal sync (durability)                               │
│     ├─ Remove txnid from global active list                             │
│     └─ State = WT_TXN_NONE                                              │
│                                                                          │
│  4. ROLLBACK (on error or explicit)                                      │
│     ├─ Walk txn->mod array                                              │
│     ├─ Mark each WT_UPDATE as aborted                                   │
│     ├─ Remove txnid from active list                                    │
│     └─ State = WT_TXN_NONE                                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.3 Write-Write Conflict Detection

```c
// When a transaction tries to update a key
int __wt_txn_modify_check(WT_SESSION_IMPL *session, WT_UPDATE *upd)
{
    WT_TXN *txn = session->txn;

    // Walk the update chain
    for (; upd != NULL; upd = upd->next) {
        // Skip our own updates
        if (upd->txnid == txn->id)
            continue;

        // Check if another transaction modified this key
        // after our transaction started
        if (upd->txnid > txn->snap_min &&
            __wt_txn_id_in_snapshot(txn, upd->txnid)) {
            // Concurrent modification - conflict!
            return WT_ROLLBACK;
        }

        // Check timestamp conflicts
        if (txn->read_timestamp != WT_TS_NONE &&
            upd->start_ts > txn->read_timestamp) {
            return WT_ROLLBACK;
        }
    }

    return 0;
}
```

### 4.4 Multi-Document Transactions (MongoDB 4.0+)

```
┌─────────────────────────────────────────────────────────────────────────┐
│              MULTI-DOCUMENT TRANSACTION                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  // Client code                                                          │
│  session.startTransaction({                                              │
│      readConcern: { level: "snapshot" },                                │
│      writeConcern: { w: "majority" }                                    │
│  });                                                                     │
│                                                                          │
│  try {                                                                   │
│      // All operations use same snapshot                                │
│      db.accounts.updateOne({ _id: "A" }, { $inc: { balance: -100 }});  │
│      db.accounts.updateOne({ _id: "B" }, { $inc: { balance: +100 }});  │
│                                                                          │
│      session.commitTransaction();                                        │
│  } catch (error) {                                                       │
│      session.abortTransaction();                                         │
│      throw error;                                                        │
│  }                                                                       │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Internal Implementation:                                                │
│                                                                          │
│  1. Transaction coordinator (for cross-shard)                           │
│     └─ Uses two-phase commit protocol                                   │
│                                                                          │
│  2. Oplog entries written atomically                                    │
│     └─ Single oplog entry with all operations                           │
│     └─ prevOpTime chain links related entries                           │
│                                                                          │
│  3. WiredTiger transaction spans all operations                         │
│     └─ Single prepare/commit at WiredTiger level                        │
│                                                                          │
│  4. Two-phase commit for distributed transactions                       │
│     └─ Prepare phase: all shards prepare                                │
│     └─ Commit phase: coordinator commits all                            │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Journaling and Write-Ahead Logging

### 5.1 Journal Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      JOURNAL ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Write Path:                                                             │
│                                                                          │
│  ┌──────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐ │
│  │  Client  │───►│   Server    │───►│  Log Slot   │───►│  Journal    │ │
│  │  Write   │    │  Operation  │    │   Buffer    │    │   Files     │ │
│  └──────────┘    └─────────────┘    └─────────────┘    └─────────────┘ │
│                                            │                  │          │
│                                            │                  ▼          │
│                                      ┌─────┴─────┐    ┌─────────────┐   │
│                                      │   Group   │    │   Disk      │   │
│                                      │  Commit   │───►│   fsync     │   │
│                                      └───────────┘    └─────────────┘   │
│                                                                          │
│  Journal Directory Structure:                                            │
│  dbpath/                                                                 │
│  └── journal/                                                           │
│      ├── WiredTigerLog.0000000001    (100MB each, circular)            │
│      ├── WiredTigerLog.0000000002                                       │
│      └── WiredTigerPreplog.0000000001  (prepared transactions)          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Log Record Format

```c
// From src/include/log.h
struct __wt_log_record {
    uint32_t len;           // Record length (including header)
    uint32_t checksum;      // CRC32C of record

    uint16_t flags;         // Compression, etc.
    uint8_t  unused[2];
    uint32_t mem_len;       // Uncompressed length (if compressed)

    // Followed by log record data:
    // - Record type
    // - File ID (which btree)
    // - Key/value data
};

// Log record types
#define WT_LOGREC_CHECKPOINT   0   // Checkpoint record
#define WT_LOGREC_COMMIT       1   // Transaction commit
#define WT_LOGREC_FILE_SYNC    2   // File sync
#define WT_LOGREC_MESSAGE      3   // Diagnostic message
#define WT_LOGREC_SYSTEM       4   // System record
```

### 5.3 Log Operations

```c
// Log operation types (within a transaction)
#define WT_LOGOP_COL_MODIFY    0   // Column-store modify
#define WT_LOGOP_COL_PUT       1   // Column-store insert/update
#define WT_LOGOP_COL_REMOVE    2   // Column-store delete
#define WT_LOGOP_COL_TRUNCATE  3   // Column-store truncate
#define WT_LOGOP_ROW_MODIFY    4   // Row-store modify (delta)
#define WT_LOGOP_ROW_PUT       5   // Row-store insert/update
#define WT_LOGOP_ROW_REMOVE    6   // Row-store delete
#define WT_LOGOP_ROW_TRUNCATE  7   // Row-store truncate
#define WT_LOGOP_TXN_TIMESTAMP 8   // Transaction timestamp

// Example log entry for insert
// [header][WT_LOGOP_ROW_PUT][file_id][key_size][key][value_size][value]
```

### 5.4 Group Commit

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        GROUP COMMIT                                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Without group commit (sequential fsyncs):                              │
│  ─────────────────────────────────────────                              │
│  TX1: write ──► fsync ──► ack                                           │
│                               TX2: write ──► fsync ──► ack              │
│                                                   TX3: write ──► fsync ─│
│                                                                          │
│  Time: ═══════════════════════════════════════════════════════►         │
│        [   slow: 3 fsyncs, ~30ms total   ]                              │
│                                                                          │
│  With group commit (batched fsyncs):                                    │
│  ───────────────────────────────────                                    │
│  TX1: write ─┐                                                          │
│  TX2: write ─┼──► buffer ──► single fsync ──► ack all                  │
│  TX3: write ─┘                                                          │
│                                                                          │
│  Time: ═══════════════════►                                             │
│        [ fast: 1 fsync, ~10ms ]                                         │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Implementation:                                                         │
│                                                                          │
│  1. Log Slot System                                                     │
│     ┌─────────────────────────────────────────────────────────────┐    │
│     │  Slot 0 (active)  │  Slot 1 (syncing)  │  Slot 2 (done)    │    │
│     │  TX4, TX5, TX6    │  TX1, TX2, TX3     │  (empty)          │    │
│     │  accumulating     │  waiting for sync  │                    │    │
│     └─────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  2. Slot State Machine                                                  │
│     SLOT_BUFFERING ──► SLOT_PENDING ──► SLOT_DONE                      │
│         │                    │              │                           │
│     (collecting          (fsync in       (all TXs                       │
│      log records)        progress)       notified)                      │
│                                                                          │
│  3. Configuration                                                       │
│     - journal_commit_interval: max wait time (default 100ms)            │
│     - journal_commit_size: max buffer size before flush                 │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 5.5 fsync Strategies

```c
// MongoDB journal sync modes
enum {
    JOURNAL_SYNC_NONE,       // No sync (fastest, risk of data loss)
    JOURNAL_SYNC_BUFFERED,   // OS buffer (default, 100ms commit interval)
    JOURNAL_SYNC_DIRECT,     // Direct I/O (bypass OS cache)
};

// WiredTiger log sync options
// From wiredtiger.h
#define WT_LOG_SYNC      0x1  // Sync after each commit
#define WT_LOG_FLUSH     0x2  // Flush after each commit
#define WT_LOG_FSYNC     0x4  // fsync after each commit (most durable)
```

---

## 6. Checkpoint System

### 6.1 Checkpoint Overview

The checkpoint creates a consistent point-in-time snapshot of all data:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CHECKPOINT ARCHITECTURE                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Relationship between Journal and Checkpoint:                           │
│                                                                          │
│  Time ───────────────────────────────────────────────────────────────►  │
│                                                                          │
│       │         │                  │                  │                 │
│       ▼         ▼                  ▼                  ▼                 │
│    ┌─────┐   ┌─────┐            ┌─────┐           ┌─────┐             │
│    │ CP1 │   │ CP2 │            │ CP3 │           │ CP4 │             │
│    └──┬──┘   └──┬──┘            └──┬──┘           └──┬──┘             │
│       │         │                  │                  │                 │
│       ├─────────┼──────────────────┼──────────────────┤                 │
│       │         │  Journal entries │                  │                 │
│       │         │  (replay on      │                  │                 │
│       │         │   recovery)      │                  │                 │
│       │         │                  │                  │                 │
│    ───┴─────────┴──────────────────┴──────────────────┴───              │
│                                                                          │
│  Recovery:                                                               │
│  1. Load most recent complete checkpoint (CP3)                          │
│  2. Replay journal entries after CP3                                    │
│  3. Discard incomplete transactions                                     │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Checkpoint Process Detail

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CHECKPOINT PHASES                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Phase 1: PREPARE                                                        │
│  ─────────────────                                                       │
│  • Acquire checkpoint lock (prevents concurrent checkpoints)             │
│  • Record current transaction state:                                     │
│    - snapshot_min: 2420  (oldest active txn)                            │
│    - snapshot_max: 2420  (newest txn)                                   │
│    - snapshot_count: 0   (concurrent active txns)                       │
│  • Record timestamps:                                                    │
│    - oldest_timestamp: point before which data can be discarded         │
│    - stable_timestamp: safe point for checkpoint                        │
│  • Block new transactions from using timestamps before stable           │
│                                                                          │
│  Phase 2: GATHER                                                         │
│  ────────────────                                                        │
│  • Walk all open B-trees                                                │
│  • Identify dirty pages:                                                │
│    - page->modify->write_gen > btree->base_write_gen                    │
│  • Build list of pages to reconcile                                     │
│                                                                          │
│  Phase 3: RECONCILE                                                      │
│  ──────────────────                                                      │
│  • For each dirty page:                                                 │
│    - Convert in-memory format to on-disk format                         │
│    - Apply MVCC visibility (filter invisible versions)                  │
│    - Apply compression                                                  │
│    - Calculate checksums                                                │
│                                                                          │
│  Phase 4: WRITE                                                          │
│  ──────────────                                                          │
│  • Allocate new disk blocks (copy-on-write)                             │
│  • Write reconciled pages                                               │
│  • Update parent pages with new child addresses                         │
│  • Write updated metadata                                               │
│                                                                          │
│  Phase 5: SYNC                                                           │
│  ─────────────                                                           │
│  • fsync all data files                                                 │
│  • Write checkpoint record to WiredTiger.wt                             │
│  • fsync metadata file                                                  │
│  • Write checkpoint complete marker                                     │
│                                                                          │
│  Phase 6: CLEANUP                                                        │
│  ────────────────                                                        │
│  • Update base_write_gen (your log shows: 154764)                       │
│  • Mark old blocks as free for reuse                                    │
│  • Remove old log files (if no longer needed for recovery)              │
│  • Release checkpoint lock                                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.3 Write Generation Tracking

```c
// From src/include/btree.h
struct __wt_btree {
    uint64_t base_write_gen;      // Write generation at last checkpoint
    // ...
};

// From src/include/btmem.h
struct __wt_page_modify {
    uint64_t write_gen;           // Incremented on each modification
    uint64_t update_txn;          // Largest txn ID in page
    // ...
};

// Dirty check: is this page modified since last checkpoint?
#define __wt_page_is_modified(page) \
    ((page)->modify != NULL && \
     (page)->modify->write_gen > (page)->modify->base_write_gen)
```

**Your log analysis:**
```
base write gen: 154764
```
This means WiredTiger has processed 154,764 page modification generations. Any page with `write_gen > 154764` is dirty and needs to be written at the next checkpoint.

### 6.4 Reconciliation (Page Conversion)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PAGE RECONCILIATION                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  In-Memory Page:                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  WT_ROW array                                                    │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │ key1 → UPDATE(v=300,ts=10) → UPDATE(v=200,ts=8) → ...     │ │   │
│  │  │ key2 → UPDATE(v=500,ts=9)  → DELETED(ts=7) → ...          │ │   │
│  │  │ key3 → UPDATE(v=100,ts=11) (newest, not visible at ckpt)  │ │   │
│  │  └────────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                    │                                     │
│                        Reconcile at stable_timestamp=9                   │
│                                    │                                     │
│                                    ▼                                     │
│  On-Disk Page (after reconciliation):                                   │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Page Header: checksum, flags, etc.                              │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │ key1: v=200 (ts=8 visible, ts=10 too new)                  │ │   │
│  │  │ key2: (deleted at ts=7, omitted)                           │ │   │
│  │  │ key3: (ts=11 > stable_timestamp, kept in memory only)      │ │   │
│  │  └────────────────────────────────────────────────────────────┘ │   │
│  │  Compressed with snappy/zstd                                     │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Concurrency Control

### 7.1 Ticket-Based Admission Control

```c
// From src/mongo/db/storage/wiredtiger/wiredtiger_kv_engine.cpp
class WiredTigerTicketHolder {
private:
    Semaphore _readTickets;    // Default: 128
    Semaphore _writeTickets;   // Default: 128

public:
    void acquireRead() {
        _readTickets.acquire();
    }

    void acquireWrite() {
        _writeTickets.acquire();
    }
};

// Why tickets matter:
// - Too many concurrent operations overwhelm WiredTiger cache
// - Tickets prevent cache thrashing
// - Provides backpressure to clients
```

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    TICKET SYSTEM                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Read Tickets (128 default)          Write Tickets (128 default)        │
│  ┌───────────────────────┐          ┌───────────────────────┐          │
│  │ ████████████████░░░░░ │          │ ██████████░░░░░░░░░░░ │          │
│  │   110 in use / 128    │          │    80 in use / 128    │          │
│  └───────────────────────┘          └───────────────────────┘          │
│                                                                          │
│  When tickets exhausted:                                                │
│  - New operations block                                                 │
│  - serverStatus shows queued operations                                 │
│  - Indicates system under heavy load                                    │
│                                                                          │
│  Tuning considerations:                                                  │
│  - Increase for high-concurrency workloads                              │
│  - Decrease if seeing cache pressure                                    │
│  - Monitor wiredTiger.concurrentTransactions metrics                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Lock Hierarchy

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MONGODB LOCK HIERARCHY                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Level 1: Global Lock                                                   │
│  ────────────────────                                                   │
│  • Protects global state                                                │
│  • Usually held in MODE_IX (intent exclusive)                           │
│                                                                          │
│  Level 2: Database Lock                                                 │
│  ──────────────────────                                                 │
│  • Per-database lock                                                    │
│  • Intent locks for normal operations                                   │
│  • Exclusive for DDL (createCollection, dropDatabase)                   │
│                                                                          │
│  Level 3: Collection Lock                                               │
│  ────────────────────────                                               │
│  • Per-collection lock                                                  │
│  • MODE_IS for reads, MODE_IX for writes                                │
│  • MODE_X for collection-level operations                               │
│                                                                          │
│  Level 4: Document Lock (WiredTiger)                                    │
│  ───────────────────────────────────                                    │
│  • No explicit document locks                                           │
│  • MVCC handles concurrent access                                       │
│  • Write-write conflicts detected at commit                             │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Lock Modes:                                                             │
│                                                                          │
│  MODE_IS  │ Intent Shared    │ "I intend to read something below"      │
│  MODE_IX  │ Intent Exclusive │ "I intend to write something below"     │
│  MODE_S   │ Shared           │ "I'm reading the whole resource"        │
│  MODE_X   │ Exclusive        │ "I'm modifying the whole resource"      │
│                                                                          │
│  Compatibility Matrix:                                                   │
│           │ IS   IX   S    X                                            │
│      ─────┼─────────────────                                            │
│       IS  │  ✓    ✓    ✓    ✗                                           │
│       IX  │  ✓    ✓    ✗    ✗                                           │
│       S   │  ✓    ✗    ✓    ✗                                           │
│       X   │  ✗    ✗    ✗    ✗                                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Document-Level Concurrency

```
┌─────────────────────────────────────────────────────────────────────────┐
│              DOCUMENT-LEVEL CONCURRENCY (WiredTiger)                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Scenario: Two transactions updating same document                      │
│                                                                          │
│  Document: { _id: 1, balance: 1000 }                                    │
│                                                                          │
│  TX1                              TX2                                   │
│  ────                             ────                                  │
│  begin()                                                                │
│  snapshot: [balance=1000]                                               │
│                                   begin()                               │
│                                   snapshot: [balance=1000]              │
│  update(_id:1, balance=900)                                             │
│  ┌─────────────────────────┐                                            │
│  │ UPDATE(txn=TX1,v=900)   │──┐                                        │
│  │          │              │  │                                         │
│  │ UPDATE(txn=0,v=1000)    │  │                                         │
│  └─────────────────────────┘  │                                         │
│                               │  update(_id:1, balance=800)             │
│                               │  ┌─────────────────────────┐            │
│                               │  │ Check update chain...   │            │
│                               │  │ TX1 modified after my   │            │
│                               │  │ snapshot started!       │            │
│                               │  │                         │            │
│                               │  │ → WT_ROLLBACK          │            │
│                               │  └─────────────────────────┘            │
│  commit()                                                               │
│                                   [MongoDB retries TX2 automatically]   │
│                                   begin()                               │
│                                   snapshot: [balance=900]               │
│                                   update(_id:1, balance=700)            │
│                                   commit()                              │
│                                                                          │
│  Final: { _id: 1, balance: 700 }                                        │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.4 Optimistic vs Pessimistic Concurrency

```
┌─────────────────────────────────────────────────────────────────────────┐
│            OPTIMISTIC CONCURRENCY (WiredTiger Approach)                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Characteristics:                                                        │
│  • No locks held during transaction                                     │
│  • Conflicts detected at commit time                                    │
│  • Good for low-conflict workloads                                      │
│  • Requires retry logic for conflicts                                   │
│                                                                          │
│  Algorithm:                                                              │
│  1. Begin: Take snapshot, no locks                                      │
│  2. Read: Use snapshot isolation                                        │
│  3. Write: Create update in chain (no lock)                             │
│  4. Commit: Validate no conflicts, make visible                         │
│  5. Conflict: Rollback and retry                                        │
│                                                                          │
│  Pros:                           Cons:                                  │
│  ✓ High concurrency              ✗ Wasted work on conflict             │
│  ✓ No deadlocks                  ✗ May retry many times                │
│  ✓ Readers never block           ✗ Not ideal for hotspots              │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│           PESSIMISTIC CONCURRENCY (Traditional Approach)                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Characteristics:                                                        │
│  • Locks acquired upfront                                               │
│  • No conflicts at commit (already prevented)                           │
│  • Good for high-conflict workloads                                     │
│  • Deadlock detection needed                                            │
│                                                                          │
│  Algorithm:                                                              │
│  1. Begin: Acquire locks on resources                                   │
│  2. Read: Hold shared lock                                              │
│  3. Write: Hold exclusive lock                                          │
│  4. Commit: Release locks                                               │
│  5. Deadlock: Abort one transaction                                     │
│                                                                          │
│  Pros:                           Cons:                                  │
│  ✓ No wasted work                ✗ Lower concurrency                   │
│  ✓ Predictable performance       ✗ Potential deadlocks                 │
│  ✓ Good for hotspots             ✗ Readers may block                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Cache and Memory Management

### 8.1 WiredTiger Cache Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MEMORY ARCHITECTURE                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  System Memory (e.g., 64GB)                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                                                                  │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │           WiredTiger Cache (default: 50% - 1GB)            │ │   │
│  │  │                     ~31GB                                   │ │   │
│  │  │  ┌──────────────────────────────────────────────────────┐  │ │   │
│  │  │  │  Clean Pages     │ Dirty Pages    │  Internal/Ovfl  │  │ │   │
│  │  │  │  (can evict)     │ (need write)   │   Pages         │  │ │   │
│  │  │  │     ~20GB        │    ~8GB        │     ~3GB        │  │ │   │
│  │  │  └──────────────────────────────────────────────────────┘  │ │   │
│  │  │                                                             │ │   │
│  │  │  • Uncompressed data (faster access)                       │ │   │
│  │  │  • MVCC update chains                                       │ │   │
│  │  │  • Index pages                                              │ │   │
│  │  └────────────────────────────────────────────────────────────┘ │   │
│  │                                                                  │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │              OS Filesystem Cache                            │ │   │
│  │  │                   ~25GB                                     │ │   │
│  │  │                                                             │ │   │
│  │  │  • Compressed on-disk blocks                                │ │   │
│  │  │  • Managed by OS (LRU)                                      │ │   │
│  │  │  • Read-ahead prefetching                                   │ │   │
│  │  └────────────────────────────────────────────────────────────┘ │   │
│  │                                                                  │   │
│  │  ┌────────────────────────────────────────────────────────────┐ │   │
│  │  │              Other (MongoDB, connections, etc.)             │ │   │
│  │  │                    ~8GB                                     │ │   │
│  │  └────────────────────────────────────────────────────────────┘ │   │
│  │                                                                  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.2 Cache Configuration

```javascript
// MongoDB cache configuration (in mongod.conf)
storage:
  wiredTiger:
    engineConfig:
      cacheSizeGB: 31        // Explicit size
      // OR leave blank for default: max(50% RAM - 1GB, 256MB)

// WiredTiger configuration string
wiredtiger_open(home, NULL,
    "cache_size=31G,"
    "eviction_target=80,"       // Start eviction at 80%
    "eviction_trigger=95,"      // Aggressive eviction at 95%
    "eviction_dirty_target=5,"  // Target for dirty pages
    "eviction_dirty_trigger=20" // Block writes at 20% dirty
);
```

### 8.3 Eviction System

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    EVICTION SYSTEM                                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Cache Pressure Levels:                                                 │
│                                                                          │
│  0%    ──────────────────────────────────────────────────────── 100%   │
│  │                                                                │     │
│  │  [  Normal Operation  ][ Eviction ][ Aggressive ][  BLOCK  ]  │     │
│  │                       80%         95%                         │     │
│  │                        ▲           ▲                          │     │
│  │                        │           │                          │     │
│  │              eviction_target  eviction_trigger                │     │
│  │                                                                │     │
│                                                                          │
│  Eviction Threads:                                                      │
│  • 1 eviction server thread (coordinator)                               │
│  • 4 eviction worker threads (configurable)                             │
│  • Application threads can also evict (under pressure)                  │
│                                                                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Eviction Algorithm (LRU-based):                                        │
│                                                                          │
│  1. Score each page:                                                    │
│     score = base_score                                                  │
│           + (current_time - page.read_gen) * LRU_WEIGHT                 │
│           + (page.is_clean ? CLEAN_BONUS : 0)                           │
│           + (page.type == LEAF ? LEAF_BONUS : 0)                        │
│           - (page.entries * SIZE_PENALTY)                               │
│                                                                          │
│  2. Select candidates:                                                  │
│     • Walk btree looking for evictable pages                            │
│     • Add to eviction queue (sorted by score)                           │
│                                                                          │
│  3. Evict pages:                                                        │
│     • Clean pages: Just discard                                         │
│     • Dirty pages: Reconcile → Write → Discard                          │
│                                                                          │
│  4. Hazard pointer check:                                               │
│     • Before freeing, scan all sessions' hazard pointers                │
│     • If page is hazarded, skip (try another)                           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.4 Page Read Path

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    PAGE READ PATH                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Query: find({ _id: 12345 })                                            │
│                                                                          │
│  Step 1: Check if page in cache                                         │
│  ──────────────────────────────                                         │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │  WT_REF for target page                                           │ │
│  │  ┌────────────────────────────────────────────────────────────┐  │ │
│  │  │ state: WT_REF_MEM  ────► Page in cache (fast path)         │  │ │
│  │  │ state: WT_REF_DISK ────► Need to read from disk            │  │ │
│  │  └────────────────────────────────────────────────────────────┘  │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  Step 2: Set hazard pointer (if in cache)                               │
│  ─────────────────────────────────────────                              │
│  session->hazard[slot] = page_ref;                                      │
│  memory_barrier();                                                       │
│  // Page now protected from eviction                                    │
│                                                                          │
│  Step 3: Read from disk (if not in cache)                               │
│  ─────────────────────────────────────────                              │
│  ┌───────────────────────────────────────────────────────────────────┐ │
│  │  1. CAS: WT_REF_DISK → WT_REF_LOCKED (claim the read)            │ │
│  │  2. Allocate memory for page                                      │ │
│  │  3. Read compressed block from disk                               │ │
│  │  4. Decompress (snappy/zstd/zlib)                                │ │
│  │  5. Build in-memory page structure                                │ │
│  │  6. Set state: WT_REF_MEM                                        │ │
│  │  7. Set hazard pointer                                            │ │
│  └───────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  Step 4: Access data                                                    │
│  ───────────────────                                                    │
│  • Binary search within page for key                                    │
│  • Walk update chain for correct version (MVCC)                         │
│  • Return value to caller                                               │
│                                                                          │
│  Step 5: Clear hazard pointer                                           │
│  ────────────────────────────                                           │
│  session->hazard[slot] = NULL;                                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Replication Internals

### 9.1 Replica Set Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    REPLICA SET ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                         ┌───────────────┐                               │
│                         │    PRIMARY    │                               │
│                         │  (read/write) │                               │
│                         └───────┬───────┘                               │
│                                 │                                        │
│                    ┌────────────┼────────────┐                          │
│                    │  Oplog Replication      │                          │
│                    │  (async by default)     │                          │
│                    │                         │                          │
│              ┌─────▼─────┐            ┌──────▼────┐                     │
│              │ SECONDARY │            │ SECONDARY │                     │
│              │  (read)   │            │  (read)   │                     │
│              └───────────┘            └───────────┘                     │
│                                                                          │
│  Election:                                                               │
│  • Raft-like consensus protocol                                         │
│  • Members vote based on priority and oplog position                    │
│  • Primary must maintain majority connection                            │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.2 Oplog Structure

```javascript
// Oplog collection: local.oplog.rs (capped collection)
// Each entry represents one operation

// Insert operation
{
    "ts": Timestamp(1770216833, 1),     // Hybrid timestamp
    "t": NumberLong(15),                 // Election term
    "h": NumberLong(-4731091939167146612), // Operation hash
    "v": 2,                              // Oplog version
    "op": "i",                           // Operation type
    "ns": "mydb.users",                  // Namespace
    "ui": UUID("abc123..."),             // Collection UUID
    "wall": ISODate("2026-02-04T14:53:53.314Z"),
    "o": {                               // Document
        "_id": ObjectId("..."),
        "name": "John",
        "email": "john@example.com"
    }
}

// Update operation
{
    "ts": Timestamp(1770216834, 1),
    "op": "u",                           // Update
    "ns": "mydb.users",
    "o2": { "_id": ObjectId("...") },    // Query
    "o": {                               // Update
        "$v": 2,                         // Update format version
        "diff": {                        // Delta format (v2)
            "u": { "email": "newemail@example.com" }
        }
    }
}

// Delete operation
{
    "ts": Timestamp(1770216835, 1),
    "op": "d",                           // Delete
    "ns": "mydb.users",
    "o": { "_id": ObjectId("...") }      // Document identifier
}

// No-op (used for heartbeats, elections)
{
    "ts": Timestamp(1770216836, 1),
    "op": "n",                           // No-op
    "ns": "",
    "o": { "msg": "periodic noop" }
}
```

### 9.3 Replication State Machine

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    REPLICATION STATE MACHINE                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Member States:                                                          │
│                                                                          │
│  ┌─────────────┐     election     ┌─────────────┐                      │
│  │   STARTUP   │─────────────────►│   PRIMARY   │                      │
│  │             │                   │             │                      │
│  └──────┬──────┘                   └──────┬──────┘                      │
│         │                                 │                              │
│         │ initial sync                    │ step down                   │
│         │                                 │                              │
│         ▼                                 ▼                              │
│  ┌─────────────┐     election     ┌─────────────┐                      │
│  │  SECONDARY  │◄────────────────►│  SECONDARY  │                      │
│  │             │                   │             │                      │
│  └──────┬──────┘                   └─────────────┘                      │
│         │                                                                │
│         │ disconnect                                                    │
│         ▼                                                                │
│  ┌─────────────┐                   ┌─────────────┐                      │
│  │  RECOVERING │──────────────────►│   ARBITER   │ (vote only)         │
│  │             │    sync complete  │             │                      │
│  └─────────────┘                   └─────────────┘                      │
│                                                                          │
│  State Transitions:                                                      │
│  • STARTUP → SECONDARY: After initial sync                              │
│  • SECONDARY → PRIMARY: Won election                                    │
│  • PRIMARY → SECONDARY: Lost election or stepped down                   │
│  • SECONDARY → RECOVERING: Fell too far behind oplog                    │
│  • RECOVERING → SECONDARY: Caught up via initial sync                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.4 Write Concern and Read Concern

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    WRITE CONCERN LEVELS                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  w: 0  (Unacknowledged)                                                 │
│  ────────────────────────                                               │
│  Client ──write──► Primary ──► (no ack)                                 │
│  • Fire and forget                                                      │
│  • No durability guarantee                                              │
│                                                                          │
│  w: 1  (Acknowledged - Default)                                         │
│  ──────────────────────────────                                         │
│  Client ──write──► Primary ──ack──► Client                              │
│  • Written to primary's memory                                          │
│  • May be lost if primary crashes before sync                           │
│                                                                          │
│  w: 1, j: true  (Journaled)                                             │
│  ──────────────────────────                                             │
│  Client ──write──► Primary ──journal sync──► ack                        │
│  • Written to journal on primary                                        │
│  • Survives primary crash                                               │
│                                                                          │
│  w: "majority"  (Majority Acknowledged)                                 │
│  ───────────────────────────────────────                                │
│  Client ──write──► Primary ──replicate──► Secondaries                  │
│                              │                                          │
│                    wait for majority ack                                │
│                              │                                          │
│                    ◄────────ack──────────                               │
│  • Survives failover                                                    │
│  • Strongest durability guarantee                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                    READ CONCERN LEVELS                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  "local" (Default)                                                      │
│  ─────────────────                                                      │
│  • Returns most recent data on queried node                             │
│  • May return data that could be rolled back                            │
│                                                                          │
│  "available"                                                            │
│  ────────────                                                           │
│  • Like "local" but for sharded clusters                                │
│  • May return orphaned documents during migrations                      │
│                                                                          │
│  "majority"                                                             │
│  ──────────                                                             │
│  • Returns data acknowledged by majority                                │
│  • Data will not be rolled back                                         │
│  • Requires WiredTiger storage engine                                   │
│                                                                          │
│  "linearizable"                                                         │
│  ───────────────                                                        │
│  • Strongest consistency                                                │
│  • Reads reflect all successful majority writes                         │
│  • Only on primary, may block                                           │
│                                                                          │
│  "snapshot"                                                             │
│  ──────────                                                             │
│  • Point-in-time snapshot                                               │
│  • Used with multi-document transactions                                │
│  • Provides repeatable reads within transaction                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 9.5 Majority Commit Point

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MAJORITY COMMIT POINT                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Timeline:                                                              │
│                                                                          │
│  Oplog   │ ts(1,1) │ ts(2,1) │ ts(3,1) │ ts(4,1) │ ts(5,1) │          │
│  ────────┴─────────┴─────────┴─────────┴─────────┴─────────┴───────    │
│                                                                          │
│  Primary:    [=====applied=====][===applied===][==applied==]           │
│              ts(1,1)           ts(3,1)        ts(5,1)                   │
│                                                                          │
│  Sec 1:      [=====applied=====][===applied===]                        │
│              ts(1,1)           ts(3,1)                                  │
│                                                                          │
│  Sec 2:      [=====applied=====]                                       │
│              ts(1,1)                                                     │
│                                                                          │
│  Majority Commit Point: ts(1,1)                                         │
│  (highest timestamp replicated to majority)                             │
│                                                                          │
│  After Sec 2 catches up to ts(3,1):                                     │
│  Majority Commit Point: ts(3,1)                                         │
│                                                                          │
│  Uses:                                                                   │
│  • readConcern "majority" reads up to this point                        │
│  • Checkpointing uses stable timestamp (≤ majority commit point)        │
│  • Rollback: never roll back beyond majority commit point               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 10. Compression and Block Management

### 10.1 Compression Options

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    COMPRESSION COMPARISON                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Algorithm │ Ratio │ Compress Speed │ Decompress │ CPU Usage │ Default │
│  ──────────┼───────┼────────────────┼────────────┼───────────┼─────────│
│  none      │  1.0x │ N/A            │ N/A        │ None      │         │
│  snappy    │  1.5x │ Fast           │ Very fast  │ Low       │ ✓       │
│  zlib      │  2.5x │ Slow           │ Medium     │ High      │         │
│  zstd      │  2.8x │ Medium         │ Fast       │ Medium    │ ✓ (4.2+)│
│                                                                          │
│  Configuration:                                                          │
│  db.createCollection("mycoll", {                                        │
│      storageEngine: {                                                   │
│          wiredTiger: {                                                  │
│              configString: "block_compressor=zstd"                      │
│          }                                                              │
│      }                                                                  │
│  });                                                                    │
│                                                                          │
│  Compression happens at:                                                │
│  • Page level (when reconciling/writing to disk)                        │
│  • NOT in memory (pages stored uncompressed in cache)                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.2 Block Manager

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BLOCK MANAGER                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  File Structure:                                                         │
│                                                                          │
│  collection-0--123456789.wt                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Block 0: File header                                            │   │
│  │  Block 1: Root page                                              │   │
│  │  Block 2: Internal page                                          │   │
│  │  Block 3: Leaf page                                              │   │
│  │  Block 4: (free - available for allocation)                      │   │
│  │  Block 5: Leaf page                                              │   │
│  │  Block 6: Overflow page                                          │   │
│  │  ...                                                             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Block Allocation:                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Free List (avail list)                                          │   │
│  │  ┌─────────────────────────────────────────────────────────────┐│   │
│  │  │  offset: 4096, size: 4096  (block 4)                        ││   │
│  │  │  offset: 32768, size: 8192 (blocks 8-9)                     ││   │
│  │  │  ...                                                         ││   │
│  │  └─────────────────────────────────────────────────────────────┘│   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Allocation Strategy:                                                    │
│  1. Check free list for suitable block                                  │
│  2. If found, reuse                                                     │
│  3. If not, extend file                                                 │
│                                                                          │
│  Compaction:                                                             │
│  • WiredTiger does NOT automatically compact                            │
│  • Use compact() command to reclaim space                               │
│  • Rewrites file to remove fragmentation                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 10.3 On-Disk Page Format

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    ON-DISK PAGE FORMAT                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Disk Block:                                                             │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  ┌───────────────────────────────────────────────────────────┐  │   │
│  │  │                    Block Header (28 bytes)                 │  │   │
│  │  │  ┌─────────────────────────────────────────────────────┐  │  │   │
│  │  │  │ disk_size    │ checksum     │ flags                 │  │  │   │
│  │  │  │ (4 bytes)    │ (4 bytes)    │ (4 bytes)             │  │  │   │
│  │  │  ├─────────────────────────────────────────────────────┤  │  │   │
│  │  │  │ unused       │ version      │ page type             │  │  │   │
│  │  │  └─────────────────────────────────────────────────────┘  │  │   │
│  │  └───────────────────────────────────────────────────────────┘  │   │
│  │                                                                  │   │
│  │  ┌───────────────────────────────────────────────────────────┐  │   │
│  │  │              Compressed Page Data                          │  │   │
│  │  │  ┌─────────────────────────────────────────────────────┐  │  │   │
│  │  │  │ Page Header                                          │  │  │   │
│  │  │  │  - recno (for column store)                         │  │  │   │
│  │  │  │  - entries count                                     │  │  │   │
│  │  │  │  - timestamp info                                    │  │  │   │
│  │  │  ├─────────────────────────────────────────────────────┤  │  │   │
│  │  │  │ Cell Data (prefix-compressed keys + values)          │  │  │   │
│  │  │  │  [cell0][cell1][cell2]...[cellN]                    │  │  │   │
│  │  │  └─────────────────────────────────────────────────────┘  │  │   │
│  │  └───────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Cell Format:                                                            │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │  [descriptor][prefix_len][suffix_len][suffix][value_len][value] │    │
│  │       1B        var         var       var       var       var   │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 11. Query Execution

### 11.1 Query Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    QUERY EXECUTION PIPELINE                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Query: db.users.find({ age: { $gt: 25 } }).sort({ name: 1 }).limit(10) │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  1. PARSE                                                        │   │
│  │     └─► Convert query to internal representation (BSON → AST)   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  2. ANALYZE                                                      │   │
│  │     └─► Identify available indexes                               │   │
│  │     └─► Check for cached plan                                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  3. PLAN (if not cached)                                         │   │
│  │     └─► Generate candidate plans                                 │   │
│  │         • Plan A: Collection scan + sort                         │   │
│  │         • Plan B: Index scan on {age: 1} + fetch + sort          │   │
│  │         • Plan C: Index scan on {name: 1} + filter (if exists)   │   │
│  │     └─► Race candidate plans (first batch)                       │   │
│  │     └─► Select winning plan                                      │   │
│  │     └─► Cache winning plan                                       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  4. EXECUTE                                                      │   │
│  │     └─► Execute winning plan stage by stage                      │   │
│  │         ┌────────────┐                                           │   │
│  │         │ IXSCAN     │ Index scan on {age: 1}                    │   │
│  │         │ age > 25   │                                           │   │
│  │         └─────┬──────┘                                           │   │
│  │               │                                                  │   │
│  │               ▼                                                  │   │
│  │         ┌────────────┐                                           │   │
│  │         │   FETCH    │ Fetch full documents                      │   │
│  │         └─────┬──────┘                                           │   │
│  │               │                                                  │   │
│  │               ▼                                                  │   │
│  │         ┌────────────┐                                           │   │
│  │         │    SORT    │ Sort by name                              │   │
│  │         └─────┬──────┘                                           │   │
│  │               │                                                  │   │
│  │               ▼                                                  │   │
│  │         ┌────────────┐                                           │   │
│  │         │   LIMIT    │ Return first 10                           │   │
│  │         └────────────┘                                           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 11.2 Plan Cache

```javascript
// View cached plans
db.users.getPlanCache().list()

// Output:
[
    {
        "queryHash": "7B6A9D3C",
        "planCacheKey": "8E2F1A5D",
        "isActive": true,
        "works": 156,           // Cost estimate
        "cachedPlan": {
            "stage": "FETCH",
            "inputStage": {
                "stage": "IXSCAN",
                "keyPattern": { "age": 1 },
                "indexName": "age_1"
            }
        },
        "timeOfCreation": ISODate("2026-02-04T10:00:00Z"),
        "createdFromQuery": {
            "query": { "age": { "$gt": 25 } },
            "sort": { "name": 1 },
            "projection": {}
        }
    }
]

// Plan cache eviction triggers:
// • Index added/dropped
// • Collection stats change significantly
// • Server restart
// • Manual clear: db.users.getPlanCache().clear()
```

### 11.3 Index Types

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    INDEX TYPES                                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  B-Tree (default)                                                       │
│  ─────────────────                                                      │
│  db.coll.createIndex({ field: 1 })                                      │
│  • Standard ordered index                                               │
│  • Range queries, equality, sorting                                     │
│  • WiredTiger B-tree implementation                                     │
│                                                                          │
│  Compound                                                                │
│  ─────────                                                              │
│  db.coll.createIndex({ a: 1, b: -1, c: 1 })                             │
│  • Multiple fields                                                      │
│  • Order matters (prefix queries)                                       │
│  • Can satisfy sort on indexed fields                                   │
│                                                                          │
│  Multikey                                                                │
│  ─────────                                                              │
│  db.coll.createIndex({ tags: 1 })  // tags is an array                  │
│  • Automatically created for array fields                               │
│  • One index entry per array element                                    │
│  • Restrictions on compound multikey                                    │
│                                                                          │
│  Text                                                                    │
│  ─────                                                                  │
│  db.coll.createIndex({ content: "text" })                               │
│  • Full-text search                                                     │
│  • Stemming, stop words                                                 │
│  • One text index per collection                                        │
│                                                                          │
│  Hashed                                                                  │
│  ───────                                                                │
│  db.coll.createIndex({ field: "hashed" })                               │
│  • Hash of field value                                                  │
│  • Used for sharding (even distribution)                                │
│  • Equality queries only (no range)                                     │
│                                                                          │
│  Geospatial                                                              │
│  ───────────                                                            │
│  db.coll.createIndex({ location: "2dsphere" })                          │
│  • Geographic queries                                                   │
│  • GeoJSON support                                                      │
│  • $near, $geoWithin, etc.                                              │
│                                                                          │
│  Wildcard                                                                │
│  ─────────                                                              │
│  db.coll.createIndex({ "$**": 1 })                                      │
│  • Index all fields                                                     │
│  • Good for dynamic schemas                                             │
│  • Higher storage overhead                                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 12. Sharding Architecture

### 12.1 Sharded Cluster Components

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SHARDED CLUSTER ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                         ┌───────────────────┐                           │
│                         │      CLIENT       │                           │
│                         └─────────┬─────────┘                           │
│                                   │                                      │
│                    ┌──────────────┼──────────────┐                      │
│                    ▼              ▼              ▼                       │
│             ┌──────────┐  ┌──────────┐  ┌──────────┐                   │
│             │  mongos  │  │  mongos  │  │  mongos  │  (Query Routers)  │
│             └────┬─────┘  └────┬─────┘  └────┬─────┘                   │
│                  │             │             │                          │
│    ┌─────────────┴─────────────┴─────────────┴─────────────┐           │
│    │                                                        │           │
│    │           Config Server Replica Set                    │           │
│    │    ┌──────────┐ ┌──────────┐ ┌──────────┐            │           │
│    │    │ Primary  │ │Secondary │ │Secondary │            │           │
│    │    │ (config) │ │ (config) │ │ (config) │            │           │
│    │    └──────────┘ └──────────┘ └──────────┘            │           │
│    │    • Chunk metadata                                   │           │
│    │    • Shard catalog                                    │           │
│    │    • Cluster-wide locks                               │           │
│    └────────────────────────────────────────────────────────┘           │
│                                                                          │
│    ┌────────────────────────────────────────────────────────┐           │
│    │                        SHARDS                          │           │
│    │                                                        │           │
│    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │           │
│    │  │   Shard 1   │  │   Shard 2   │  │   Shard 3   │   │           │
│    │  │ (replica    │  │ (replica    │  │ (replica    │   │           │
│    │  │  set)       │  │  set)       │  │  set)       │   │           │
│    │  │             │  │             │  │             │   │           │
│    │  │ Chunks:     │  │ Chunks:     │  │ Chunks:     │   │           │
│    │  │ [A-H]       │  │ [I-P]       │  │ [Q-Z]       │   │           │
│    │  └─────────────┘  └─────────────┘  └─────────────┘   │           │
│    │                                                        │           │
│    └────────────────────────────────────────────────────────┘           │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 12.2 Shard Keys and Chunks

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SHARD KEY AND CHUNKS                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Shard Key: { user_id: 1 }                                              │
│                                                                          │
│  Chunk Distribution:                                                     │
│                                                                          │
│  Shard Key Range:   [MinKey]────────────────────────────────[MaxKey]   │
│                                                                          │
│  Chunks:            │ Chunk1 │ Chunk2 │ Chunk3 │ Chunk4 │ Chunk5 │      │
│                     [MinKey,  [1000,   [2000,   [3000,   [4000,          │
│                      1000)    2000)    3000)    4000)    MaxKey)         │
│                                                                          │
│  Shard Assignment:  │ Shard1 │ Shard2 │ Shard1 │ Shard3 │ Shard2 │      │
│                                                                          │
│  Chunk Splitting:                                                        │
│  • Occurs when chunk exceeds chunkSize (default 128MB)                  │
│  • Split point chosen at median                                         │
│  • Creates two smaller chunks                                           │
│                                                                          │
│  Chunk Migration:                                                        │
│  • Balancer moves chunks between shards                                 │
│  • Goal: even distribution across shards                                │
│  • Can cause temporary performance impact                               │
│                                                                          │
│  Shard Key Selection Guidelines:                                        │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Good:                          Bad:                             │   │
│  │  • High cardinality             • Low cardinality (few values)  │   │
│  │  • Even distribution            • Monotonically increasing      │   │
│  │  • Query isolation              • Hotspots                       │   │
│  │  • { user_id: 1 }               • { status: 1 }                 │   │
│  │  • { tenant_id: 1, _id: 1 }     • { created_at: 1 }            │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 12.3 Query Routing

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    QUERY ROUTING                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Shard Key: { user_id: 1 }                                              │
│                                                                          │
│  Targeted Query (includes shard key):                                   │
│  ──────────────────────────────────────                                 │
│  db.orders.find({ user_id: 12345 })                                     │
│                                                                          │
│  mongos ──► config server (lookup chunk for user_id=12345)              │
│         ──► route to specific shard (Shard2)                            │
│         ◄── return results from Shard2 only                             │
│                                                                          │
│  Scatter-Gather Query (no shard key):                                   │
│  ─────────────────────────────────────                                  │
│  db.orders.find({ status: "pending" })                                  │
│                                                                          │
│  mongos ──► broadcast to ALL shards                                     │
│         ◄── gather results from all shards                              │
│         ──► merge and return                                            │
│                                                                          │
│  Performance Comparison:                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Query Type      │ Shards Hit │ Performance │ Example           │   │
│  │  ────────────────┼────────────┼─────────────┼─────────────────── │   │
│  │  Targeted        │ 1          │ Fast        │ {user_id: X}      │   │
│  │  Scatter-Gather  │ All        │ Slow        │ {status: "..."}   │   │
│  │  Broadcast       │ All        │ Slowest     │ unsharded coll    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 13. Comparisons with Other Systems

### 13.1 MongoDB vs PostgreSQL

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MONGODB vs POSTGRESQL                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Aspect              │ MongoDB/WiredTiger    │ PostgreSQL               │
│  ────────────────────┼───────────────────────┼─────────────────────────│
│  Storage Model       │ Copy-on-write B-tree  │ Heap + B-tree indexes   │
│                      │ (clustered by _id)    │ (heap is unordered)     │
│                      │                       │                          │
│  MVCC Location       │ In-memory update      │ In-place with           │
│                      │ chains                │ xmin/xmax in tuples     │
│                      │                       │                          │
│  Vacuum Needed       │ No (COW reclaims)     │ Yes (dead tuple         │
│                      │                       │ cleanup required)        │
│                      │                       │                          │
│  Page Size           │ Variable (4KB-512KB)  │ Fixed 8KB               │
│                      │                       │                          │
│  Checkpoints         │ Full tree reconcile   │ Dirty buffer flush      │
│                      │ to new locations      │ in place                 │
│                      │                       │                          │
│  WAL Format          │ Logical (key-value    │ Physical (page          │
│                      │ operations)           │ images + deltas)         │
│                      │                       │                          │
│  Compression         │ Per-page (snappy,     │ Per-table (lz4,         │
│                      │ zstd, zlib)           │ pglz, zstd)             │
│                      │                       │                          │
│  Replication         │ Logical (oplog)       │ Physical (WAL           │
│                      │                       │ streaming) + Logical     │
│                      │                       │                          │
│  Lock Granularity    │ Document + intent     │ Row + advisory +        │
│                      │ (collection level)    │ predicate locks         │
│                      │                       │                          │
│  Index Types         │ B-tree, hash, text,   │ B-tree, hash, GiST,     │
│                      │ geospatial, wildcard  │ SP-GiST, GIN, BRIN      │
│                      │                       │                          │
│  Transactions        │ Multi-doc (4.0+)      │ Full ACID (always)      │
│                      │ Snapshot isolation    │ Multiple levels         │
│                      │                       │                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 13.2 MongoDB vs MySQL InnoDB

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    MONGODB vs MYSQL INNODB                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Aspect              │ MongoDB/WiredTiger    │ MySQL/InnoDB             │
│  ────────────────────┼───────────────────────┼─────────────────────────│
│  B-tree Update       │ Copy-on-write         │ In-place update         │
│                      │                       │                          │
│  MVCC Storage        │ Update chains in      │ Undo log (rollback      │
│                      │ memory                │ segment)                 │
│                      │                       │                          │
│  Buffer Pool         │ WiredTiger cache      │ InnoDB buffer pool      │
│                      │ (uncompressed pages)  │ (compressed +           │
│                      │                       │ uncompressed)            │
│                      │                       │                          │
│  Doublewrite         │ Not needed (COW)      │ Required (for torn      │
│                      │                       │ page protection)         │
│                      │                       │                          │
│  Change Buffer       │ Not applicable        │ Buffers secondary       │
│                      │                       │ index changes            │
│                      │                       │                          │
│  Clustering          │ By _id (always)       │ By primary key          │
│                      │                       │ (configurable)           │
│                      │                       │                          │
│  Page Size           │ Variable              │ 16KB default            │
│                      │                       │                          │
│  Redo Log            │ Journal (operations)  │ Redo log (pages)        │
│                      │                       │                          │
│  Purge               │ Not needed            │ Purge thread cleans     │
│                      │                       │ old MVCC versions       │
│                      │                       │                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 14. References

### 14.1 Official Documentation

- [MongoDB Manual](https://www.mongodb.com/docs/manual/)
- [WiredTiger Documentation](http://source.wiredtiger.com/)
- [MongoDB University](https://university.mongodb.com/)

### 14.2 Source Code

- MongoDB: `https://github.com/mongodb/mongo`
  - Storage: `src/mongo/db/storage/`
  - WiredTiger integration: `src/mongo/db/storage/wiredtiger/`
  - Replication: `src/mongo/db/repl/`
  - Query: `src/mongo/db/query/`

- WiredTiger: `https://github.com/wiredtiger/wiredtiger`
  - B-tree: `src/btree/`
  - Cache: `src/cache/`
  - Eviction: `src/evict/`
  - Transaction: `src/txn/`
  - Log/Journal: `src/log/`
  - Block manager: `src/block/`

### 14.3 Key Source Files

| Component | Location |
|-----------|----------|
| WiredTiger B-tree | `wiredtiger/src/btree/bt_*.c` |
| Page structure | `wiredtiger/src/include/btmem.h` |
| Checkpoint | `wiredtiger/src/txn/txn_ckpt.c` |
| MVCC/Transactions | `wiredtiger/src/txn/txn.c` |
| Visibility | `wiredtiger/src/txn/txn_visibility.c` |
| Journal | `wiredtiger/src/log/log.c` |
| Eviction | `wiredtiger/src/evict/evict_lru.c` |
| Cache | `wiredtiger/src/cache/cache_*.c` |
| Block manager | `wiredtiger/src/block/block_*.c` |
| MongoDB Storage API | `mongo/src/mongo/db/storage/storage_engine.h` |
| WiredTiger KV Engine | `mongo/src/mongo/db/storage/wiredtiger/wiredtiger_kv_engine.cpp` |
| Oplog | `mongo/src/mongo/db/repl/oplog.cpp` |
| Replication | `mongo/src/mongo/db/repl/replication_coordinator_impl.cpp` |

### 14.4 Academic Papers

1. **WiredTiger Design**
   - Graefe, G. "Modern B-Tree Techniques" - Foundations and Trends in Databases, 2011

2. **MVCC and Concurrency**
   - Berenson et al. "A Critique of ANSI SQL Isolation Levels" - SIGMOD 1995

3. **Log-Structured Storage**
   - O'Neil et al. "The Log-Structured Merge-Tree (LSM-Tree)" - Acta Informatica, 1996

4. **Hazard Pointers**
   - Michael, M. "Hazard Pointers: Safe Memory Reclamation for Lock-Free Objects" - IEEE TPDS, 2004

### 14.5 Conference Talks

- MongoDB World presentations on storage internals
- WiredTiger team talks at database conferences
- MongoDB Engineering Blog: `https://www.mongodb.com/blog/channel/engineering`

---

## Appendix A: Log Message Reference

### A.1 Checkpoint Log (User's Original Query)

```json
{
    "t": {"$date": "2026-02-04T14:53:53.314+00:00"},
    "s": "I",
    "c": "WTCHKPT",
    "id": 22430,
    "ctx": "Checkpointer",
    "msg": "WiredTiger message",
    "attr": {
        "message": {
            "ts_sec": 1770216833,
            "ts_usec": 314115,
            "thread": "1:0xffffa64febc0",
            "session_name": "WT_SESSION.checkpoint",
            "category": "WT_VERB_CHECKPOINT_PROGRESS",
            "category_id": 6,
            "verbose_level": "DEBUG_1",
            "verbose_level_id": 1,
            "msg": "saving checkpoint snapshot min: 2420, snapshot max: 2420 snapshot count: 0, oldest timestamp: (0, 0) , meta checkpoint timestamp: (0, 0) base write gen: 154764"
        }
    }
}
```

**Field Analysis:**

| Field | Value | Meaning |
|-------|-------|---------|
| `c: "WTCHKPT"` | Component | WiredTiger Checkpoint subsystem |
| `ctx: "Checkpointer"` | Context | Background checkpointer thread |
| `id: 22430` | Log ID | Checkpoint progress message |
| `snapshot min: 2420` | Txn ID | Oldest transaction in checkpoint snapshot |
| `snapshot max: 2420` | Txn ID | Newest transaction in checkpoint snapshot |
| `snapshot count: 0` | Count | No concurrent active transactions |
| `oldest timestamp: (0, 0)` | HLC | No oldest timestamp set (not using timestamps) |
| `meta checkpoint timestamp: (0, 0)` | HLC | Metadata checkpoint timestamp |
| `base write gen: 154764` | Counter | Page modification generation boundary |

---

## See Also

- [LSM Trees](@/notes/database/lsm_trees.md) — WiredTiger's B-tree storage contrasts with LSM-based engines; compaction and write amplification trade-offs apply
- [WAL, Torn Pages, and Disk Reliability](@/notes/database/wal_torn_pages.md) — WiredTiger's journaling and checkpoint system addresses the same durability problems
- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — MongoDB replica sets use a Raft-like protocol covered in depth here
- [Buffer Management and Predictive Translation](@/notes/database/buffer_management_predictive_translation.md) — WiredTiger's cache/eviction is a buffer management problem addressed by this research

---

*Document created: 2026-02-04*
*Last updated: 2026-02-04*
