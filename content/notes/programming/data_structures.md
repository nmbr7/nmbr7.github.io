+++
title = "Data Structures"
date = 2026-02-10
[taxonomies]
tags = ["data-structures", "lock-free", "bloom-filter", "crdt", "skip-list", "concurrent", "cache-friendly"]
+++


# Data Structures for High-Performance Systems

These are specialized data structures critical in production systems where nanoseconds and reliability matter — from databases and kernels to HFT and networking.

---

## 1. **Lock-Free Ring Buffer (SPSC/MPMC)**

**Used in:** HFT systems, Linux kernel, LMAX Disruptor

A circular buffer that enables wait-free communication between threads without locks.

**The problem it solves.** A lock-based queue serializes producer and consumer on a mutex — every push/pop pays a lock round-trip and risks priority inversion. For a single-producer/single-consumer (SPSC) pair the two never actually need to touch the *same* variable at the same time: the producer owns `tail`, the consumer owns `head`. Publishing a slot then reduces to one release-store, and no lock is needed at all.

**The core idea — head/tail indices that only ever advance.** Indices are *monotonic* counters (never reset); the slot is `index & (N-1)`. Because they only grow, `tail - head` is the exact count of queued items with no ambiguity between full and empty.

```
Monotonic indices (N=8, mask=7). Buffer physically 8 slots; indices count forever.

  tail=10, head=6  →  count = tail-head = 4 items queued
  slot(10) = 10 & 7 = 2 ← next write lands here
  slot(6)  = 6  & 7 = 6 ← next read comes from here

  ┌───┬───┬───┬───┬───┬───┬───┬───┐
  │ 8 │ 9 │ w │   │   │   │ r │ 7 │   (numbers = index stored, w=write pos, r=read pos)
  └───┴───┴───┴───┴───┴───┴───┴───┘
    0   1   2   3   4   5   6   7   ← physical slot
  FULL  when tail-head == N   EMPTY when tail-head == 0   (no wasted sentinel slot)
```

**Worked push/pop trace:**
```
push(x): tail=10, head=6. Check 10-6=4 < 8 ✓ not full.
         buffer[10 & 7] = x               ← write into slot 2
         tail.store(11, release)          ← publish; consumer's acquire-load sees x
pop():   head=6, tail=11 (acquire). 11-6=5 > 0 ✓ not empty.
         x = buffer[6 & 7]                ← read slot 6
         head.store(7, release)           ← frees the slot for producer
```
The `release` on `tail.store` pairs with the consumer's `acquire` on `tail.load`: it guarantees the *data write* into the slot is visible before the index bump that advertises it. Same pairing on `head` frees slots safely.

**Key properties:**
- Cache-line padding to prevent false sharing
- Memory barriers for ordering guarantees
- Power-of-2 sizing for fast modulo (bitwise AND)

```cpp
// Simplified SPSC queue
template<typename T, size_t N>
class SPSCQueue {
    alignas(64) std::atomic<size_t> head_{0};
    alignas(64) std::atomic<size_t> tail_{0};
    T buffer_[N];  // N must be power of 2

    bool push(const T& item) {
        size_t tail = tail_.load(std::memory_order_relaxed);
        if (tail - head_.load(std::memory_order_acquire) >= N)
            return false;
        buffer_[tail & (N-1)] = item;
        tail_.store(tail + 1, std::memory_order_release);
        return true;
    }
};
```

---

## 2. **B+ Tree with Fractal Tree Indexing (Bε-tree)**

**Used in:** TokuDB, BetrFS, FoundationDB

Buffers updates at internal nodes, converting random I/O to sequential I/O.

**The problem it solves.** A B-tree insert dirties one leaf → one random write (~1 I/O per insert). At scale that's the bottleneck. The Bε-tree trades a little of each node's fanout budget for a *message buffer*: an insert only writes a message into the root's buffer (already in memory, flushed sequentially), and the message *drifts down* toward its leaf in large batches. Cost is amortized across every message that shares the flush.

**The core idea — the ε knob.** Each internal node of block size B splits its space: `B^ε` for pivot keys (fanout) and `B - B^ε` for the buffer. ε ∈ (0,1] slides between B-tree (ε=1, no buffer) and buffered-repository/LSM-like (ε→0, huge buffers).

```
Node = [ pivots: B^ε keys ] [ buffer: B−B^ε pending messages ]

Insert(k=25): append message {ins,25} to ROOT buffer only. 1 sequential write. Done.

         ┌─────────────────────────────────┐
         │ pivots:[50]  buffer:[ins25,del12]│  ← root, in memory
         └──────────────┬──────────────────┘
                        │ buffer fills → FLUSH: route each message to the
                        │ child whose key-range covers it, append there
    ┌───────────────────┴───────────────────┐
┌───┴─────────────────┐        ┌─────────────┴────────┐
│ pivots:[20,30]      │        │ pivots:[70,90]       │
│ buffer:[ins25]      │        │ buffer:[del85]       │  ← messages sink one level
└─────────────────────┘        └──────────────────────┘
```

**Worked flush cascade.** When a node's buffer overflows, pick the child receiving the most messages and move that whole batch down in one I/O:
```
root buffer full → flush all messages destined for child[20,30] together (say 40 msgs)
  → 1 write moves 40 inserts one level down  → amortized 1/40 of an I/O each
  → repeat down the tree; each message crosses log_{B^ε} N levels total
Amortized insert cost = O( log_{B^ε} N / (B−B^ε) ) ≈ O( (log_B N)/(ε B^{1−ε}) ) I/Os
  → with ε=1/2: O( (log N)/√B ) — √B times cheaper than a B-tree's O(log_B N)
```
**Reads** still pay O(log_{B^ε} N), but must also check the buffers along the root-to-leaf path for pending messages that haven't reached the leaf yet — that's the read/write tradeoff ε tunes.

**Key properties:**
- O(log_{B^ε} N / B^{1−ε}) amortized I/O for insertions (√B faster than B-tree at ε=½)
- Cascading pushdown: messages flushed in big batches, cost amortized across the batch
- Write-optimized while keeping reads at B-tree-like O(log N) (+ buffer scan on the path)
- Used in TokuDB/PerconaFT and BetrFS; deletes and upserts are also just messages

---

## 3. **Skip List with Tower Optimization**

**Used in:** Redis sorted sets, LevelDB/RocksDB memtables, HFT order books

Probabilistic alternative to balanced trees with better cache behavior.

**The problem it solves.** A sorted linked list has O(N) search. A balanced tree fixes that but needs rotations (tricky to make lock-free, and cache-hostile). A skip list adds *express lanes*: extra forward pointers that let search skip over many nodes, giving O(log N) expected search with no rotations — just local pointer splices, which makes concurrent (even lock-free) variants easy.

**The core idea — geometric tower heights.** Each inserted node flips a fair coin to decide its height: present at level 0 always, promoted to level i+1 with probability p=½. So ~½ the nodes reach level 1, ~¼ reach level 2, etc. Level i has ~N/2ⁱ nodes → the top lane has O(1) nodes and O(log N) levels.

```
Level 3: HEAD ────────────────────────────────────> 50 ────────────> NIL
Level 2: HEAD ──────────> 20 ─────────────────────> 50 ────────────> NIL
Level 1: HEAD ──────────> 20 ──────────> 35 ──────> 50 ──> 60 ─────> NIL
Level 0: HEAD ─> 10 ─> 20 ─> 25 ─> 35 ─> 50 ─> 60 ─> 75 ─> 80 ─> NIL
         (50 flipped heads 3×→height 4; 20,35,60 →height 2; 10,25,75,80→height 1)
```

**Worked search — find 60.** Start top-left, move right while next ≤ target, else drop a level:
```
L3: HEAD→50 (50<60, advance to 50); 50→NIL (NIL>60) → drop to L2 at node 50
L2: 50→NIL → drop to L1 at node 50
L1: 50→60  (60≤60, advance to 60) → found on the way down; 60 is the answer
Visited 3 nodes instead of 6 — the express lanes skipped 10,20,25,35.
```
**Worked insert — insert 55 (coin flips heads once → height 2):**
```
Search for 55, remembering the last node visited at EACH level (the "update" vector):
  update[L1]=50, update[L0]=50.
Splice new node between 50 and 60 at levels 0 and 1:
  node55.forward[0]=50.forward[0](=60); 50.forward[0]=node55   (level 0)
  node55.forward[1]=50.forward[1](=60); 50.forward[1]=node55   (level 1)
Purely local pointer updates → no rebalancing, trivially made lock-free with CAS.
```

**HFT optimization - Order book with price-time priority:**
```cpp
struct OrderBookLevel {
    Price price;
    // Orders stored in arrival order for FIFO matching
    intrusive_list<Order> orders;
    // Skip list links
    OrderBookLevel* forward[MAX_LEVEL];
};
```

---

## 4. **HAMT (Hash Array Mapped Trie)**

**Used in:** Clojure persistent collections, Scala immutable maps, IPFS

Provides near-O(1) operations with structural sharing for immutability.

**The problem it solves.** An immutable hash map needs cheap copy-on-write updates. A flat array-backed table would copy the whole array on every insert (O(N)). A HAMT is a 32-way trie *keyed by slices of the hash*, so an update only copies the ~log₃₂(N) nodes on the path from root to the changed leaf — every other subtree is shared by pointer with the previous version.

**The core idea — consume the hash 5 bits at a time.** A 32-bit (or 64-bit) hash is chopped into 5-bit chunks (2⁵=32). Chunk at depth d selects the child at that level. To avoid storing 32 mostly-null child slots per node, each node holds a **32-bit bitmap** of which children exist plus a *dense* array of only the present children. The slot's array index is `popcount(bitmap & (bit-1))`.

```
Insert key with hash = ...0b_00111_00010  (read 5 bits per level, right to left)
depth 0 chunk = 00010 = 2   → look at bit 2 of root bitmap
depth 1 chunk = 00111 = 7   → look at bit 7 of the child's bitmap

Root (bitmap: 10100100)   bits set = {2,5,7} → dense array has 3 entries
 │        popcount(bitmap & (1<<2 −1)) = 0 → entry[0]
 ├─[bit 2]─→ Leaf: {key1: val1}
 ├─[bit 5]─→ SubNode (bitmap: 00100010)   ← hash collision on first 5 bits → recurse
 │              ├─[bit 1]─→ Leaf: {key2: val2}
 │              └─[bit 5]─→ Collision: [{key3,val3},{key4,val4}]  ← full hash equal
 └─[bit 7]─→ Leaf: {key5: val5}
```

**Worked lookup — key1 (hash chunk₀ = 2):**
```
Root bitmap = 10100100. Is bit 2 set?  bitmap & (1<<2) ≠ 0 ✓
Dense index = popcount(bitmap & ((1<<2)−1)) = popcount(00000000) = 0
→ array[0] is a Leaf holding key1 → compare full key → return val1.
```

**Worked immutable insert — add key6, path root→SubNode(bit5)→new leaf:**
```
Copy only: new SubNode (with key6 spliced into its dense array + bitmap bit set),
           new Root (its entry[1] repointed to the new SubNode).
Everything else — the bit-2 leaf, the bit-7 leaf, key2's leaf — SHARED by pointer.
Cost: O(log₃₂ N) node copies (~1–2 levels typical), old version stays fully valid.
```

**Key properties:**
- 32 or 64-way branching at each level → shallow tree (log₃₂ N ≈ ⌈N-digits/1.5⌉)
- Population count (`POPCNT`) turns the sparse bitmap into a dense-array index in 1 instruction
- Copy-on-write with path copying (only log₃₂(N) nodes) → cheap persistent versions
- Collisions past hash exhaustion fall back to a small collision list at the leaf

```cpp
// Index calculation using popcount
int sparseIndex(uint32_t bitmap, int bitPosition) {
    return __builtin_popcount(bitmap & ((1 << bitPosition) - 1));
}
```

---

## 5. **LSM Tree (Log-Structured Merge Tree)**

**Used in:** LevelDB, RocksDB, Cassandra, InfluxDB

Write-optimized structure that batches writes in memory and flushes to sorted disk levels.

```
Memory:    ┌──────────────┐
           │  MemTable    │  (Red-black tree / Skip list)
           │  (Mutable)   │
           └──────┬───────┘
                  │ Flush
Disk:      ┌──────▼───────┐
Level 0:   │ SST │ SST    │  (Overlapping ranges)
           └──────┬───────┘
                  │ Compaction
           ┌──────▼───────┐
Level 1:   │   SSTable    │  (Sorted, non-overlapping)
           └──────┬───────┘
                  │
Level N:   │ Larger SSTables │ (10x size ratio per level)
```

**The core idea — never update in place; make reads reconcile.** A write just appends to the in-memory MemTable (+ a WAL record for durability). No disk seek, no read-modify-write. When the MemTable fills it's flushed as an immutable sorted SSTable. The cost is pushed to *reads*, which must check newest-to-oldest until they find the key.

**Worked write path:**
```
put(k,v):  1. append {k,v} to WAL (sequential disk write, crash-safe)
           2. insert into MemTable (skip list / RB tree, sorted)
           MemTable full → seal it, start a new one, flush sealed one to L0 SSTable
delete(k): write a TOMBSTONE marker {k, ⊥} — deletes are just writes too
```
**Worked read path (newest wins):**
```
get(k):  1. check active MemTable        → hit? return (newest possible)
         2. check immutable MemTables
         3. L0 SSTables newest→oldest     (L0 ranges OVERLAP → may check several)
         4. L1..LN: one SSTable per level (non-overlapping → binary search key range)
   At each SSTable, consult its Bloom filter FIRST: "definitely not here" → skip the
   disk read entirely. This is why every SSTable ships a Bloom filter.
   First match wins (could be a tombstone → key is "deleted").
```
**Worked compaction (leveled).** L(i) is ~10× L(i+1). When Lᵢ overfills, pick an SSTable and merge it into the overlapping Lᵢ₊₁ tables:
```
L0: [a–f]* [c–k]*  (overlapping)      merge-sort a chosen table with
L1: [a–d][e–h][i–m]                    the L1 tables it overlaps →
  → produces new non-overlapping L1 tables; drop tombstoned/overwritten keys here
Write amplification: each key rewritten ~once per level it passes = O(levels × ratio).
Tiered compaction instead stacks same-size tables and merges them in bulk → less
write amp, more read amp (more tables to check). This is the core LSM tradeoff dial.
```

**Key properties:**
- Write amplification: O(level_count × size_ratio)
- Bloom filters per SSTable to avoid unnecessary reads
- Leveled vs. tiered compaction strategies

### LSM Alternatives

LSM trees are excellent for **write-heavy workloads**, but they're not universally optimal. The "best" structure depends entirely on your workload characteristics.

| Structure    | Best For                  | Write  | Read   | Space  |
| ------------ | ------------------------- | ------ | ------ | ------ |
| **LSM Tree** | Write-heavy, sequential   | ⭐⭐⭐ | ⭐⭐   | ⭐⭐   |
| **B+ Tree**  | Read-heavy, range queries | ⭐⭐   | ⭐⭐⭐ | ⭐⭐⭐ |
| **Bw-Tree**  | High concurrency          | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐   |
| **FASTER**   | Point lookups, hybrid     | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐   |

**Bw-Tree** (Lock-Free B+ Tree) — Used by: Microsoft SQL Server Hekaton, Azure Cosmos DB
- Lock-free operations, better for multi-core CPUs
- Combines benefits of LSM and B+ tree
- Complex implementation

**FASTER** (Microsoft Research) — Hybrid log + hash index approach
```
┌─────────────────┐
│   Hash Index    │  ← In-memory, point lookups
├─────────────────┤
│  Hybrid Log     │  ← Spans memory + storage
└─────────────────┘
```
- Extremely fast point operations, handles larger-than-memory data
- Limited range query support

**WiscKey** (Key-Value Separation) — Optimization on top of LSM
```
LSM Tree:     [key, pointer] → small, fast compaction
Value Log:    [actual values] → sequential writes
```
- Reduces write amplification significantly, faster compaction
- Range scans become random reads

**PebblesDB** (Fragmented LSM) — Guards to reduce write amplification

**Learned Indexes** (Research) — Uses ML to predict key positions; potentially massive space savings, still experimental for writes

**Decision Framework:**
```
                    ┌─────────────────┐
                    │  Workload Type? │
                    └────────┬────────┘
           ┌─────────────────┼─────────────────┐
           ▼                 ▼                 ▼
      Write-Heavy       Balanced         Read-Heavy
           │                │                 │
           ▼                ▼                 ▼
       LSM Tree         Bw-Tree           B+ Tree
       (RocksDB)     (SQL Server)      (PostgreSQL)
```

---

## 6. **Judy Array**

**Used in:** Linux kernel routing tables, high-performance dictionaries

A 256-ary digital tree with aggressive compression techniques.

**The problem it solves.** A plain 256-ary radix tree indexes a key one byte at a time — 4 levels for a 32-bit key, 8 for 64-bit. Fast, but each node is a 256-slot pointer array (2 KB), almost all NULL. Judy keeps the O(bytes) lookup depth but makes each node's *size adapt to how many children it actually has*, so sparse and dense regions both stay compact and cache-resident.

**The core idea — pick a node encoding per population.** Walking the key byte-by-byte, at each level Judy stores the set of present child-bytes in whichever of these forms is smallest:

```
┌─────────────────────────────────────────────────────────────────┐
│ Judy node encodings (chosen by how many children a node has)     │
├─────────────────────────────────────────────────────────────────┤
│ Null        : 0 children      → nothing stored                   │
│ Linear      : ≤ ~25 children  → sorted list of present bytes     │
│                                 [0x03,0x41,0x9f] + values inline  │
│ Bitmap      : dense range     → 256-bit present-mask (32 bytes)   │
│                                 + popcount to index a value array │
│ Uncompressed: near-full       → direct 256-way pointer array     │
└─────────────────────────────────────────────────────────────────┘
```

**Worked lookup — find key `0x41 42 00 07` (bytes 0x41,0x42,0x00,0x07):**
```
Level 0 (byte 0x41): root is a Linear node holding bytes [0x10, 0x41, 0xC3].
        Binary-search the sorted list → 0x41 at index 1 → follow child ptr[1].
Level 1 (byte 0x42): child is a Bitmap node. Test bit 0x42 of the 256-bit mask.
        Bit set ✓. Index into value array = popcount(mask & ((1<<0x42)-1)) = 5
        → 6th slot → follow child ptr.
Level 2 (byte 0x00): Uncompressed node (dense) → direct index [0x00] → child ptr.
Level 3 (byte 0x07): Linear leaf node → find 0x07 → return stored value. FOUND.
```
The key trick at Bitmap nodes is the same **popcount rank** used in Swiss tables and succinct structures: the 256-bit mask says *which* bytes exist, and `popcount` of the mask below the target bit gives its position in a densely-packed value array — no 256-slot array needed.

**Why it compresses further — the two Judy flavors:**
- **JudyL** (integer→value map) and **Judy1** (bit-set) share this machinery.
- **Level compression:** long runs of single-child nodes are collapsed (path compression, like a radix tree).
- **Width compression:** the Linear/Bitmap/Uncompressed choice above shrinks each level.

**Key properties:**
- Adapts node encoding to local data density → no wasted pointer arrays
- No pointer overhead for dense regions; no 256-slot arrays for sparse ones
- Cache-optimized: nodes sized to ~one or two 64-byte cache lines
- Downside: notoriously complex (Judy's source is ~20k LOC of macros); ART (§10) achieves similar adaptivity far more simply and has largely superseded it

---

## 7. **Cuckoo Hash Table**

**Used in:** DPDK, network packet processing, HFT symbol lookup

Guaranteed O(1) worst-case lookups with multiple hash functions.

**The problem it solves.** Chained/linear-probing hash tables have O(1) *average* lookup but a long probe chain in the worst case — bad for HFT/networking where tail latency is what matters. Cuckoo hashing guarantees a key lives in exactly one of **2 candidate slots** (from 2 independent hashes), so *lookup touches at most 2 cache lines, always*. The cost moves to inserts, which may have to shuffle existing keys.

**The core idea — each key has two homes; displace to make room.** On insert, try h1(x). If occupied, evict the resident and put x there; the evicted key "cuckoos" into its *other* home (its h2), which may evict yet another key — a kick chain. It terminates because each key falls back to its alternate slot.

```
Table 1 (h1):        Table 2 (h2):
┌───┬───────┐        ┌───┬───────┐
│ 0 │       │        │ 0 │       │
│ 1 │ key_A │◄───┐   │ 1 │ key_C │
│ 2 │       │    │   │ 2 │ key_A │──── A also reachable here (its h2)
│ 3 │ key_B │    │   │ 3 │       │
└───┴───────┘    │   └───┴───────┘
```

**Worked insert with kick chain — insert X, h1(X)=1, h2(X)=3:**
```
1. slot T1[1] holds A → evict A, place X there.       T1[1]=X
2. A must go to its OTHER home h2(A)=2 → T2[2] empty → place A. DONE. chain length 2.
   (If T2[2] were full, evict that key to ITS alternate, and so on.)
If a chain exceeds ~log N kicks (or loops), STASH the key or trigger a full rehash
with new hash seeds. Load factor kept < 50% (plain) so chains stay short.
```
**Worked lookup — find A:**
```
Check T1[h1(A)=1] → X, not A.  Check T2[h2(A)=2] → A ✓.  At most 2 probes, period.
```

**Modern variant - Bucket Cuckoo Hashing:**
```cpp
struct Bucket {
    uint8_t fingerprints[4];  // 1-byte hash summaries
    Key keys[4];
    Value values[4];
};
// Check 4 entries per cache line!
```

---

## 8. **Van Emde Boas Tree / X-Fast/Y-Fast Trie**

**Used in:** Kernel schedulers, IP routing, real-time priority queues

Achieves O(log log U) operations where U is the universe size.

**The problem it solves.** A balanced BST does predecessor/successor in O(log N). When keys are integers from a bounded universe [0,U), a vEB tree beats that: O(log log U), independent of N. For U=2³² that's ~5 steps. Kernel schedulers and IP routers exploit exactly this — keys are small integers, and next-higher-priority / longest-prefix queries must be sub-microsecond.

**The core idea — split each key into high/low halves and recurse on √U.** A vEB over universe U stores a key x as `high = ⌊x/√U⌋` (which *cluster*) and `low = x mod √U` (position within cluster). It holds √U child vEB structures each of universe √U, plus a **summary** vEB (also universe √U) marking which clusters are non-empty. Each recursion halves the *number of bits*, so depth is log log U.

```
Universe U = 16, √U = 4. Key x → (high = x>>2, low = x&3).
  e.g. 13 = 1101 → high=3 (cluster 3), low=1 (slot 1)

         summary vEB (universe 4): which clusters non-empty?
              /    |    |    \
        cluster0 cluster1 cluster2 cluster3     ← each a vEB over universe 4
        [0-3]    [4-7]    [8-11]   [12-15]
Plus each node caches its own min and max (min is NOT stored recursively — the trick
that removes a log factor and makes successor O(1) recursive calls, not two).
```

**Worked successor(x) — find next key > 6, U=16 (high=1, low=2):**
```
6 → cluster 1, slot 2.
1. If cluster[1] has a slot > 2 (check its max): recurse into cluster[1]. 
   Say cluster[1].max = 1 (only key 5 present) < 2 → no successor inside this cluster.
2. Ask SUMMARY for the next non-empty cluster after 1: summary.successor(1) = 3.
3. Answer = cluster 3's MIN, reassembled: 3*4 + cluster[3].min.
Each step is ONE recursive call on a √U-universe structure → T(U)=T(√U)+O(1)=O(log log U).
```
The min-caching is what keeps it to one recursive call per level (not two), giving the log log U bound rather than log U.

**Y-Fast Trie (practical variant):**
- Top structure: X-Fast trie on representative elements
- Bottom structure: Balanced BSTs with O(log log U) elements each
- O(log log U) time, O(N) space

---

## 9. **Rope (for Strings/Buffers)**

**Used in:** Xi Editor, Visual Studio Code, Zed editor, large text processing

Binary tree of string chunks for efficient editing of massive texts.

**The problem it solves.** A flat `std::string` makes an insert/delete at position i cost O(N) — everything after i shifts. For a multi-megabyte editor buffer that's unusable. A rope stores the text as leaf chunks in a balanced tree; edits become O(log N) pointer surgery, and immutable ropes give O(1)-ish undo by sharing untouched subtrees.

**The core idea — internal nodes cache the length of their left subtree ("weight").** That single number turns character indexing into a tree descent: at each node, if `i < weight` go left, else go right with `i -= weight`. No character is ever counted twice.

```
           ┌──────────────┐
           │ weight=13    │  (= length of everything in the left subtree)
           └──────┬───────┘
      ┌───────────┴───────────┐
┌─────┴─────┐           ┌─────┴─────┐
│ weight=6  │           │  "world"  │  (leaf, len 5)
└─────┬─────┘           └───────────┘
┌─────┴─────┐     ┌───────────┐
│ "Hello "  │     │ "amazing" │   (leaves, len 6 and 7)
└───────────┘     └───────────┘
Full text = "Hello " + "amazing" + "world"
```

**Worked index — char at position 8:**
```
Root weight=13: 8 < 13 → go LEFT (i stays 8)
Left node weight=6: 8 ≥ 6 → go RIGHT, i = 8−6 = 2
Reach leaf "amazing": leaf[2] = 'a' (m-a-z...) → 'z'? "amazing"[2]='z'. Return.
Two node hops instead of scanning 9 chars.
```
**Worked concat & split — the two O(log N) primitives everything is built on:**
```
concat(L,R): make a new root with left=L, right=R, weight=len(L). O(1) (+ rebalance).
split(rope, k): walk to position k, cutting each node into a ≤k part and a >k part,
  reassembling two ropes by concat on the way up. O(log N).
insert(text,k) = concat(concat(split_left, new_leaf), split_right)  → O(log N).
delete(a,b)    = concat(split(rope,a).left, split(rope,b).right).
```

**Key properties:**
- O(log N) insert/delete at any position (vs O(N) for arrays)
- O(log N) concatenation
- Immutable variants enable infinite undo

```cpp
class Rope {
    struct Node {
        size_t weight;  // Left subtree length
        unique_ptr<Node> left, right;
        string leaf;    // Only for leaf nodes
    };

    char index(size_t i, Node* n) {
        if (n->leaf) return n->leaf[i];
        if (i < n->weight) return index(i, n->left.get());
        return index(i - n->weight, n->right.get());
    }
};
```

---

## 10. **Radix Tree / Adaptive Radix Tree (ART)**

**Used in:** Linux kernel (page cache, routing), TiDB, DuckDB, HyPer

Compressed trie with node sizes that adapt to key density.

**The problem it solves.** A radix tree indexes keys byte-by-byte (great: depth = key length, no rebalancing), but a fixed 256-way node wastes ~2 KB when it has 2 children. ART keeps radix-tree lookup but stores each node in one of **four sizes that grow on demand**, so memory tracks actual fanout — matching Judy's goal (§6) with far less code, which is why DuckDB/HyPer adopted it.

**The core idea — grow the node type as children are added.** A node starts as Node4; when a 5th child is inserted it's promoted to Node16, then Node48, then Node256. Each type finds a child by a different mechanism (linear/SIMD scan, indirection array, or direct index) — trading space for lookup speed as density rises.

```
Adaptive Node Types:
┌────────────────────────────────────────────────────┐
│ Node4   : 4 keys,   4 children (16 bytes)          │
│ Node16  : 16 keys,  16 children (SIMD searchable)  │
│ Node48  : 256→48 index, 48 children                │
│ Node256 : Direct 256-way lookup                    │
└────────────────────────────────────────────────────┘

                    Node16 ["ab"]
                    /      |      \
              Node4["c"]  "xyz"   Node48["de"]
                /                    \
             "def"                  "fgh"
```

**Worked lookup — key `"art"` (bytes a,r,t) with path compression:**
```
Root Node16, byte 'a' → SIMD-compare 'a' against 16 keys → child index → descend.
Child has a COMPRESSED PATH "rt" (single-child chain collapsed into the node's prefix):
  compare remaining key "rt" against stored prefix "rt" ✓ → reach leaf → value.
Path compression means the 'r' and 't' nodes (each had one child) aren't separate hops.
```
**Worked adaptive growth — inserting into a full Node4:**
```
Node4 holds keys [b,d,f,h] (4 children, full). Insert child 'c':
  → allocate Node16, copy the 4 entries + 'c' (now 5), free the Node4, swap pointer.
Later a 17th child → promote Node16→Node48 (256-byte index → 48 child ptrs);
49th → Node256 (direct index[byte]). Shrinks symmetrically on delete.
```

**Key properties:**
- Height determined by key length, not data volume
- Path compression eliminates single-child chains
- SIMD-optimized key matching in Node16

```cpp
// SIMD search in Node16
int Node16::findChild(uint8_t key) {
    __m128i cmp = _mm_cmpeq_epi8(
        _mm_set1_epi8(key),
        _mm_loadu_si128((__m128i*)keys)
    );
    int mask = _mm_movemask_epi8(cmp);
    return mask ? __builtin_ctz(mask) : -1;
}
```

---

## 11. **B+ Tree**

**Used in:** MySQL InnoDB, PostgreSQL, all major databases

Disk-optimized tree with high fanout for database indexes.

**The problem it solves.** Disk/SSD I/O is dominated by the *number* of block fetches, not bytes. A binary tree of N keys is ~log₂N deep → log₂N seeks. A B+ tree packs hundreds of keys per node (one disk page), so fanout is ~100s and depth drops to log₁₀₀N — a billion keys in 3–4 page reads. All values live in the leaves, which are linked, so range scans are sequential.

**The core idea — fat nodes + splits that grow the tree upward.** Inserts go into a leaf; when a node overflows (> ORDER−1 keys) it *splits* in half and pushes its middle key up to the parent. Splits cascade toward the root; when the root splits, the tree gains a level. This keeps every leaf at the same depth (perfectly balanced) with no rotations.

**Worked insert with a leaf split (ORDER=4, so max 3 keys/node):**
```
Leaf [10, 20, 30] is full. Insert 25:
  1. would become [10,20,25,30] — overflow.
  2. split at middle: left=[10,20], right=[25,30]
  3. copy the split key 25 UP into the parent as a separator (B+ tree copies, doesn't
     move, the key — it still lives in the right leaf for range scans)
Parent [.., 40] → [.., 25, 40] with the two new leaves as children.
If the parent now overflows, it splits too and MOVES its middle key up (internal splits
move; leaf splits copy). Root split → new root → tree height +1. Always balanced.
```
**Worked range scan `[15, 45]`** — the leaf-link payoff:
```
findLeaf(15) → leaf [10,20]. Emit ≥15 → 20. Follow leaf.next → [25,30] → emit 25,30.
→ next → [40,50] → emit 40, stop at 50 > 45. Sequential leaf walk, no re-descent.
```

```cpp
template<typename K, typename V, int ORDER = 128>
class BPlusTree {
    struct Node {
        bool is_leaf;
        int num_keys;
        K keys[ORDER - 1];

        virtual ~Node() = default;
    };

    struct InternalNode : Node {
        Node* children[ORDER];
    };

    struct LeafNode : Node {
        V values[ORDER - 1];
        LeafNode* next;  // For range scans
    };

    Node* root_ = nullptr;

    LeafNode* findLeaf(const K& key) {
        Node* current = root_;

        while (!current->is_leaf) {
            InternalNode* internal = static_cast<InternalNode*>(current);
            int i = 0;
            while (i < internal->num_keys && key >= internal->keys[i])
                i++;
            current = internal->children[i];
        }

        return static_cast<LeafNode*>(current);
    }

public:
    V* search(const K& key) {
        if (!root_) return nullptr;

        LeafNode* leaf = findLeaf(key);
        for (int i = 0; i < leaf->num_keys; i++) {
            if (leaf->keys[i] == key)
                return &leaf->values[i];
        }
        return nullptr;
    }

    // Range scan - O(log n + k) where k is result size
    std::vector<V> range(const K& start, const K& end) {
        std::vector<V> results;
        LeafNode* leaf = findLeaf(start);

        while (leaf) {
            for (int i = 0; i < leaf->num_keys; i++) {
                if (leaf->keys[i] >= start && leaf->keys[i] <= end)
                    results.push_back(leaf->values[i]);
                else if (leaf->keys[i] > end)
                    return results;
            }
            leaf = leaf->next;
        }
        return results;
    }
};
```

| Pros                               | Cons                             |
| ---------------------------------- | -------------------------------- |
| Optimal for disk I/O (high fanout) | Complex implementation           |
| Excellent range query performance  | Higher memory for small datasets |
| Leaf-level linking                 | Rebalancing overhead             |
| Predictable I/O patterns           | Not cache-optimal for in-memory  |

---

## 12. **Red-Black Tree**

**Used in:** C++ std::map/set, Java TreeMap, Linux CFS scheduler

Self-balancing BST with guaranteed O(log n) operations.

**The problem it solves.** A plain BST degrades to a linked list (O(N)) on sorted input. AVL trees stay perfectly balanced but rebalance aggressively (more rotations per insert). Red-black trees enforce a *looser* balance — good enough for O(log N) but with ≤2 rotations per insert/delete — which is why they back most standard-library maps where insert/erase are common.

**The core idea — 5 color invariants bound the height.** Nodes are red or black, with rules: (1) root is black, (2) red nodes have black children (no two reds adjacent), (3) every root-to-NULL path has the same count of black nodes ("black-height"). Rule 3 pins the shortest path; rule 2 says the longest path can insert at most one red between blacks → **longest ≤ 2× shortest**, so height ≤ 2·log₂(N+1) → O(log N). Fixups (recolor + rotate) restore these after a change.

**Worked insert fixup — Case 1 (red uncle → just recolor, no rotation):**
```
Insert z (always starts RED to preserve black-height). z's parent is RED → violates
rule 2. Look at the UNCLE (parent's sibling):

        [B:g]                    [R:g]   ← recolor grandparent RED
        /    \        →          /    \
    [R:p]  [R:u]             [B:p]  [B:u] ← parent & uncle → BLACK
      /                        /
   [R:z]                    [R:z]         ← violation pushed UP to g; repeat at g
Recoloring fixes it locally and moves the problem up ≤ log N levels. Rotations
(Cases 2/3) only needed when the uncle is BLACK — at most 2 of them, then done.
```

```cpp
enum Color { RED, BLACK };

template<typename K, typename V>
class RedBlackTree {
    struct Node {
        K key;
        V value;
        Color color;
        Node *left, *right, *parent;

        Node(K k, V v) : key(k), value(v), color(RED),
                         left(nullptr), right(nullptr), parent(nullptr) {}
    };

    Node* root_ = nullptr;

    void rotateLeft(Node* x) {
        Node* y = x->right;
        x->right = y->left;
        if (y->left) y->left->parent = x;
        y->parent = x->parent;

        if (!x->parent) root_ = y;
        else if (x == x->parent->left) x->parent->left = y;
        else x->parent->right = y;

        y->left = x;
        x->parent = y;
    }

    void fixInsert(Node* z) {
        while (z->parent && z->parent->color == RED) {
            if (z->parent == z->parent->parent->left) {
                Node* y = z->parent->parent->right;  // Uncle

                if (y && y->color == RED) {
                    // Case 1: Uncle is red
                    z->parent->color = BLACK;
                    y->color = BLACK;
                    z->parent->parent->color = RED;
                    z = z->parent->parent;
                } else {
                    if (z == z->parent->right) {
                        // Case 2: Uncle black, z is right child
                        z = z->parent;
                        rotateLeft(z);
                    }
                    // Case 3: Uncle black, z is left child
                    z->parent->color = BLACK;
                    z->parent->parent->color = RED;
                    rotateRight(z->parent->parent);
                }
            } else {
                // Mirror cases...
            }
        }
        root_->color = BLACK;
    }
};
```

| Pros                            | Cons                              |
| ------------------------------- | --------------------------------- |
| Guaranteed O(log n)             | More rotations than AVL on insert |
| Good for insert-heavy workloads | More complex than AVL             |
| Less strict balancing           | Pointer overhead                  |
| Standard library choice         | Poor cache locality               |

---

## 13. **Memory Pool / Slab Allocator**

**Used in:** Linux kernel SLAB/SLUB, nginx, game engines

Eliminates allocation overhead and fragmentation for fixed-size objects.

**The problem it solves.** General `malloc` must handle arbitrary sizes: it searches free lists, splits/coalesces blocks, and leaves external fragmentation. But kernels and servers allocate the *same* struct millions of times (inodes, sk_buffs, connection objects). A slab allocator pre-carves memory into fixed-size cells of exactly that type → allocation is popping a free-list head (a few instructions), and identical sizes mean **zero external fragmentation**.

**The core idea — a slab is a page of same-size cells threaded on a free list.** Because every cell is `sizeof(T)`, a freed cell can store its own `next` pointer in place. Allocate = unlink head; free = push onto head. No search, no coalescing.

```
Slab (one page) carved into cells; free cells form an intrusive singly-linked list:

  free_list ─→ [cell3] ─→ [cell1] ─→ [cell5] ─→ null
  ┌──────┬──────┬──────┬──────┬──────┬──────┐
  │cell0 │cell1 │cell2 │cell3 │cell4 │cell5 │   in-use: 0,2,4
  │ USED │ free │ USED │ free │ USED │ free │   (free ones chained above)
  └──────┴──────┴──────┴──────┴──────┴──────┘

allocate(): p = free_list; free_list = p->next; return p.     ~3 instructions, O(1)
free(p):    p->next = free_list; free_list = p.               O(1), cell reused hot-in-cache
```
**Why it also helps cache & concurrency:** freed objects are handed back immediately, still warm in L1 (kernel SLUB keeps per-CPU slabs to avoid cross-core locking). The bounded worst case — no search — is what makes it usable on latency-critical paths. Cost: memory is pinned per-type and not returned to the OS until the slab empties.

```cpp
template<typename T, size_t BlockSize = 4096>
class MemoryPool {
    struct Block {
        alignas(T) char data[sizeof(T)];
        Block* next;
    };

    struct Slab {
        std::array<Block, BlockSize / sizeof(Block)> blocks;
        Slab* next_slab;
    };

    Slab* slabs_ = nullptr;
    Block* free_list_ = nullptr;
    std::mutex mutex_;

public:
    T* allocate() {
        std::lock_guard<std::mutex> lock(mutex_);

        if (!free_list_) {
            Slab* slab = new Slab();
            slab->next_slab = slabs_;
            slabs_ = slab;

            for (size_t i = 0; i < slab->blocks.size() - 1; i++) {
                slab->blocks[i].next = &slab->blocks[i + 1];
            }
            slab->blocks.back().next = nullptr;
            free_list_ = &slab->blocks[0];
        }

        Block* block = free_list_;
        free_list_ = block->next;
        return reinterpret_cast<T*>(block->data);
    }

    void deallocate(T* ptr) {
        std::lock_guard<std::mutex> lock(mutex_);
        Block* block = reinterpret_cast<Block*>(ptr);
        block->next = free_list_;
        free_list_ = block;
    }
};
```

| Pros                         | Cons                              |
| ---------------------------- | --------------------------------- |
| O(1) allocation/deallocation | Only for fixed-size objects       |
| Zero fragmentation           | Memory not returned to OS         |
| Cache-friendly locality      | Per-type pool needed              |
| Predictable performance      | Memory overhead for small objects |

---

## 14. **Bloom Filter Variants (Cuckoo, Xor, Ribbon)**

**Used in:** RocksDB, PostgreSQL, network routers, distributed caches

Probabilistic set membership structures with false positives but no false negatives.

**The problem it solves.** "Is key k in this set?" over a huge set (every SSTable's keys, every URL seen) can't afford one bit per possible key or a disk probe per query. A Bloom filter answers in ~10 bits *per stored key* with a tunable false-positive rate, and — crucially — **never a false negative**: if it says "not present," you can skip the expensive lookup entirely. That asymmetry is the whole point in LSM read paths (§5) and routers.

**The core idea — k hash bits, all must be set.** Insert sets k bit positions. Query checks those same k positions: a 0 anywhere proves the key was never inserted (no false negatives); all-1s means "probably yes" (could be coincidental overlap from other keys → false positive).

**Classic Bloom Filter:**
```
Insert "cat": hash1("cat")=2, hash2("cat")=5, hash3("cat")=7 → set those bits
Bit array: [0 0 1 0 0 1 0 1 0 0 0 0]
                ↑       ↑   ↑
Query "dog": h(dog)={2,5,9}. bit 9 = 0 → "DEFINITELY NOT" (proof: dog never set bit 9)
Query "cow": h(cow)={2,5,7}. all three = 1 → "MAYBE" — but cat set them, not cow!
             → this is a false positive. No way to tell without the real lookup.
```
**Why the FP rate is tunable.** With m bits, n keys, k hashes, FP ≈ (1 − e^{−kn/m})^k, minimized at k = (m/n)·ln2 → ~0.6185^{m/n}. Rule of thumb: ~9.6 bits/key gives 1% FP, +4.8 bits/key per 10× improvement. No deletes (clearing a bit could break another key that shares it) — hence the counting/cuckoo/quotient variants below.

**Cuckoo Filter** (Fan et al., 2014) — supports deletion, better space for ε < 3%:
```
Buckets with fingerprints:
┌──────┬──────┬──────┬──────┐
│ fp_a │      │ fp_b │ fp_c │   Bucket 0
│      │ fp_d │      │      │   Bucket 1
│ fp_e │ fp_f │      │      │   Bucket 2
└──────┴──────┴──────┴──────┘
Lookup: check bucket[h1(x)] and bucket[h1(x) ⊕ h(fingerprint)]
```

**Xor Filter** (Graf & Lemire, 2020) — static, ~23% smaller than Bloom, faster queries:
- 3 hash functions map to 3 positions; XOR of stored values = fingerprint
- Build via "peeling" algorithm on random hypergraph
- 1.23 bits/key × log(1/ε), near information-theoretic optimum

**Ribbon Filter** (Dillinger & Walzer, 2021, used in RocksDB) — balanced construction/query:
- Based on solving linear systems over GF(2) via banded matrices
- Configurable space vs. build time tradeoff
- ~20% space improvement over Bloom at same FPR

**Binary Fuse Filter** (2022) — successor to Xor, even faster construction:
- 1.13 bits/key × log(1/ε), essentially optimal

---

## 15. **Count-Min Sketch**

**Used in:** Network traffic monitoring, database query optimizers (PostgreSQL), stream processing (Flink, Spark)

Probabilistic frequency estimator for streaming data. Never underestimates counts.

**The problem it solves.** Counting how often each item appears in a high-rate stream (which IPs, which query terms) with an exact hash map costs O(distinct items) memory — unbounded. Count-Min fixes memory at d×w counters regardless of stream size, answering "how many times did x appear?" with a *one-sided* error: it may over-count (from collisions) but **never under-counts**. Good enough for heavy-hitter detection and query-optimizer selectivity.

**The core idea — d independent hash rows; take the min to shed collisions.** Update bumps one counter per row. Any single counter is inflated by other items that hashed there, but different items collide in *different* rows — so the **minimum** across rows is the estimate least polluted by collisions.

```
d hash functions, w counters each:

           w counters
    ┌───┬───┬───┬───┬───┬───┬───┬───┐
h1: │ 0 │ 3 │ 0 │ 1 │ 0 │ 2 │ 0 │ 0 │
    ├───┼───┼───┼───┼───┼───┼───┼───┤
h2: │ 1 │ 0 │ 0 │ 3 │ 0 │ 0 │ 1 │ 0 │
    ├───┼───┼───┼───┼───┼───┼───┼───┤
h3: │ 0 │ 0 │ 2 │ 0 │ 0 │ 3 │ 0 │ 0 │
    └───┴───┴───┴───┴───┴───┴───┴───┘

Update(x): increment CM[i][hi(x)] for each row i
Query(x):  min over all rows of CM[i][hi(x)]
```
**Worked example.** True counts: x appeared 3×, y 5×, and y happens to collide with x in row h1 but not h2/h3:
```
After stream:
  row h1[cell shared by x,y] = 3+5 = 8   ← polluted
  row h2[x's cell]           = 3         ← clean
  row h3[x's cell]           = 3         ← clean
Query(x) = min(8,3,3) = 3 ✓ exact — the min discarded the h1 collision.
The min can only be ≥ true count (every counter includes x's real hits) → never under.
```
**Error bound.** With w = ⌈e/ε⌉ and d = ⌈ln(1/δ)⌉, the estimate exceeds the truth by more than ε·(total stream size) with probability ≤ δ. So error scales with stream mass — great for heavy hitters, weak for rare items (which is what Count Sketch's ±1 signs fix below).

**Properties:**
- Space: O(1/ε × log(1/δ)) for ε-approximate counts with probability 1-δ
- No deletion in basic form; Count-Min-Log variant for heavy hitters
- Conservative update: only increment if it's the minimum → reduces overcount

**Variant — Count Sketch** (Charikar et al., 2002): unbiased estimator using ±1 signs, better for skewed distributions.

---

## 16. **HyperLogLog**

**Used in:** Redis `PFCOUNT`, BigQuery `APPROX_COUNT_DISTINCT`, Presto, Druid

Cardinality estimation using O(log log N) space per register.

**The problem it solves.** Counting *distinct* elements (unique visitors, distinct IPs) exactly needs a set of all of them — O(N) memory. HyperLogLog estimates the count to ~2% error in **1.5 KB, flat**, for cardinalities up to billions, and merges across machines. That's why it's the engine behind `APPROX_COUNT_DISTINCT` everywhere.

**The core idea — rare bit patterns reveal cardinality.** Hash each element uniformly. The probability a hash has ≥k leading zeros is 2^{−k}. So if the *maximum* leading-zero count you've ever seen is R, you've probably observed ~2^R distinct values (you needed ~2^R draws to hit that rarity). One register is noisy → use m registers (chosen by the first p hash bits), each tracking its own max, and combine with a **harmonic mean** (tames the outliers) times a bias constant α_m.

```
Hash each element to 64-bit value:
  h(x) = 0010000...  (first 1-bit at position 4)

Split into: [register index (first p bits)] [remaining bits → count leading zeros]

Registers (m = 2^p):
┌────┬────┬────┬────┬────┬────┐
│ R0 │ R1 │ R2 │ R3 │ ...│ Rm │  each stores max(leading_zeros + 1)
│  3 │  7 │  1 │  5 │    │  4 │
└────┴────┴────┴────┴────┴────┘

Estimate = α_m × m² / Σ(2^(-R[j]))   (harmonic mean based)
```
**Worked register update — add element e, p=4 (m=16 registers):**
```
h(e) = 0110 | 000101...   first p=4 bits = 0110 = 6 → register R6.
remaining bits 000101... → leading zeros = 3 → value = 3+1 = 4.
R6 = max(R6, 4).   (only the MAX is kept → adding e again never changes anything →
                    naturally handles duplicates, which is the whole point.)
Merge two HLLs: R_merged[j] = max(R_a[j], R_b[j]) per register → distributed distinct-count.
```

**Properties:**
- 1.6KB for ~2% standard error (p=14, 16384 registers × 6 bits)
- Mergeable: max of each register → enables distributed counting
- HyperLogLog++ (Google, 2013): bias correction + sparse representation for small cardinalities

---

## 17. **HNSW (Hierarchical Navigable Small World Graph)**

**Used in:** Qdrant, Pinecone, pgvector, FAISS, Weaviate — the dominant ANN index

Multi-layer proximity graph for approximate nearest neighbor search in high-dimensional spaces.

**The problem it solves.** Exact nearest-neighbor in high dimensions is cursed — no index beats brute-force O(N·d) once d is large. HNSW gives *approximate* NN in O(log N) distance computations with high recall, which is what powers vector search (RAG, recommendations). It's the dominant ANN index because it's fast to query and incrementally insertable.

**The core idea — a navigable small-world graph with skip-list-style layers.** Layer 0 holds every vector, richly connected to near neighbors. Higher layers are exponentially sparser samples with *long-range* links (like an express lane). Search starts at the top (few, far-apart nodes), greedily hops toward the query, then descends — each layer zooms in. "Small-world" = short greedy paths; the long links prevent getting stuck in a local cluster.

```
Layer 2:  A ─────────────────── D         (sparse, long-range links)
          │                     │
Layer 1:  A ──── B ──── C ──── D ──── E   (medium connectivity)
          │      │      │      │      │
Layer 0:  A ─ B ─ C ─ D ─ E ─ F ─ G ─ H  (dense, all elements)
```

**Worked greedy search — query q, find nearest, top entry = A:**
```
L2: at A. neighbors {D}. dist(q,D) < dist(q,A) → move to D. D's neighbors no closer
    → descend to L1 at D.
L1: at D. neighbors {C,E}. dist(q,C) < dist(q,D) → move to C. C no better → descend.
L0: at C. beam search with candidate list of size ef_search: explore C's neighbors,
    keep the ef closest, expand them, until no improvement → return top-k.
Higher ef_search = wider beam = higher recall, slower. Distance calls ≈ O(log N · degree).
```

**Key properties:**
- Build: insert each element with probability p(layer) = e^(-layer / m_L)
- Search: O(log N) expected time with polylogarithmic graph degree
- Tunable recall/speed via `ef_construction` (build quality) and `ef_search` (query beam width)
- Memory: ~1KB per vector for typical configs (M=16, d=128)

**SOTA variants:**
- **DiskANN** (Microsoft, NeurIPS 2019): billion-scale on SSD, Vamana graph
- **ScaNN** (Google, ICML 2020): anisotropic quantization + reordering
- **Glass** (2023): graph-based ANN optimized for GPU
- **RaBitQ** (2024): random-bit quantization, 32× compression with high recall

---

## 18. **Learned Indexes**

**Research frontier:** Replacing traditional index structures with ML models.

**The Case for Learned Indexes** (Kraska et al., Google, 2018):
```
Traditional B-tree:          Learned Index:
┌─────────┐                  ┌──────────────────────┐
│  Root   │                  │  Linear/NN Model     │
├────┬────┤                  │  f(key) → position   │
│ L  │ R  │     →            │  + error bounds      │
├──┬─┤──┬─┤                  │  + local search      │
│..│..│..│..│                 └──────────────────────┘
```

**Key insight:** A B-tree is a model that maps keys to positions. A CDF model can do the same with less space.

**Worked lookup — the "predict then correct" mechanism:**
```
Sorted keys stored in an array. A learned index fits a model f(key) ≈ position
(the empirical CDF: what fraction of keys are ≤ this key, × N).

lookup(k=57):
  1. pos_pred = f(57) = 812         ← model predicts array index ~812 (1 evaluation)
  2. the model has a guaranteed max error ε (say ±32) measured at build time
  3. binary/exponential search ONLY the window [812−32, 812+32] → 64 slots → ~6 compares
Contrast a B-tree: ~log₁₀₀(N) pointer-chasing page reads. The model replaces the upper
tree levels with one arithmetic evaluation; ε bounds the leftover local search.
```
The whole game is making f small *and* accurate: a single linear model is tiny but has huge ε on real data, so the structures below stack many local linear segments to keep ε small.

**PGM-Index** (Ferragina & Vinciguerra, VLDB 2020):
- Piecewise linear approximation of the CDF
- Provably optimal space for a given error bound ε
- Recursive: index of index of index → O(log log N) lookup
- Fully dynamic variant (2022)

**ALEX** (Ding et al., SIGMOD 2020):
- Adaptive learned index with gapped arrays at leaves
- Handles inserts/updates/deletes natively
- Splits/merges nodes based on cost model
- Outperforms B-trees on read-heavy YCSB workloads by 2-4×

**LIPP** (Wu et al., VLDB 2021): Updatable learned index with worst-case O(log N) guarantees.

**NFL** (Wu et al., 2022): Near-optimal learned filters replacing Bloom filters.

---

## 19. **CRDTs (Conflict-Free Replicated Data Types)**

**Used in:** Redis CRDT, Riak, Automerge, Yjs (collaborative editing), Apple Notes, Figma

Data structures that guarantee eventual consistency without coordination.

**The problem it solves.** Multiple replicas take writes concurrently (offline edits, multi-region DBs, collab editors) with no locking. Naive last-write-wins loses data; manual conflict resolution is error-prone. CRDTs are typed so that *any* order of merges reaches the **same** final state — no coordination, no conflicts, guaranteed convergence.

**The core idea — merge must be a join on a lattice.** For state-based CRDTs, `merge` must be **commutative, associative, and idempotent** (a semilattice join). Those three properties mean it doesn't matter what order updates arrive, whether they're duplicated, or how they're batched — every replica that has seen the same *set* of updates computes an identical state. That's why a G-Counter merges by per-node `max` (max is comm/assoc/idempotent) and a G-Set by `union`.

```
Two types:
  CvRDT (state-based): merge(state_A, state_B) → converged state
  CmRDT (op-based):    apply(op) at all replicas → same result regardless of order

G-Counter (grow-only counter):
  Node A: [A:3, B:0, C:0]   value = max per node, sum for total
  Node B: [A:0, B:5, C:0]
  merge → [A:3, B:5, C:0]   total = 8

LWW-Register (last-writer-wins):
  {value: "hello", timestamp: 1706000001}
  {value: "world", timestamp: 1706000002}
  merge → {value: "world", timestamp: 1706000002}
```
**Worked convergence — 3 replicas, updates arrive in different orders:**
```
G-Counter, nodes A,B,C. A increments twice, B once, concurrently.
  Replica A learns:  merge([A:2,B:0],[A:0,B:1]) then C's [.. ] → [A:2,B:1,C:0]
  Replica C learns them in the OPPOSITE order → still max per node → [A:2,B:1,C:0]
  Duplicate delivery of A's state → max(2,2)=2 → idempotent, no double count.
All three converge to total = 3 regardless of order/duplication → the lattice guarantee.
```

**Key types:**
| CRDT | Description | Merge |
|------|-------------|-------|
| G-Counter | Grow-only counter | max per node |
| PN-Counter | Positive-negative counter | two G-Counters |
| G-Set | Grow-only set | union |
| OR-Set | Observed-remove set | add wins over concurrent remove |
| LWW-Register | Last-writer-wins | highest timestamp |
| RGA | Replicated growable array | causal ordering |
| Merkle-CRDT | CRDT over Merkle-DAG | DAG merge (used in IPFS) |

**SOTA research:**
- **Diamond Types** (2023): optimized list CRDT, 10-100× faster than Automerge/Yjs
- **Fugue** (2023): list CRDT with maximal non-interleaving guarantee
- **Eg-walker** (Kleppmann, 2024): replay-based CRDT with near-optimal performance

---

## 20. **Succinct Data Structures**

**Used in:** Bioinformatics (FM-index), compressed search indexes, language models

Represent data in space close to information-theoretic minimum while supporting constant-time operations.

```
Bit vector B = 10110010 (n=8, m=4 ones)

Operations:
  rank₁(B, 5) = 3    (count 1s in B[0..5])
  select₁(B, 2) = 2  (position of 2nd 1-bit)
  access(B, 3) = 1    (value at position 3)

Space: n + o(n) bits (sublinear overhead!)
```

**Key structures:**

**Wavelet Tree** — answers rank/select/access on arbitrary alphabets by recursively splitting the alphabet in half and storing *one bit per character* at each level (0 = char's symbol is in the left half of the current alphabet range, 1 = right half). Each node keeps only a bitvector; the characters are never stored explicitly.

```
String: "abracadabra"  Σ = {a,b,c,d,r}, split point m = c

Root  (alphabet {a,b,c,d,r}, left={a,b}, right={c,d,r}):
  char:   a b r a c a d a b r a
  bit:    0 0 1 0 1 0 1 0 0 1 0     ← 0 if char∈{a,b}, 1 if char∈{c,d,r}
        /                    \
  left subtree {a,b}       right subtree {c,d,r}
  (chars with bit 0:        (chars with bit 1:
   a b a a a b a)            r c d r)
  split a|b:                 split c | d,r:
  char: a b a a a b a        char: r c d r
  bit:  0 1 0 0 0 1 0        bit:  1 0 1 1   (0 if 'c', 1 if {d,r})
      /        \                 /       \
   "aaaaa"    "bb"            "c"      right {d,r}: "rdr", bit 1 0 1
```

**Worked query — `access(2)`: what is the character at index 2 (`'r'`)?**
```
String:   a b r a c a d a b r a   (index 2 = 'r')
Root bits:0 0 1 0 1 0 1 0 0 1 0
Root bit[2] = 1  → char is in right half {c,d,r}. Descend right.
  Remap position = (# of 1-bits in root[0..2], exclusive of 2) = rank₁(root,2)=0
  → this char is the 0th element of the right child's sequence "rcdr".
Right child {c,d,r}: seq "r c d r", bits 1 0 1 1.  bit[0] = 1 → char in {d,r}.
  Remap = rank₁(child,0) = 0 → 0th element of {d,r} child's sequence "rdr".
Leaf {d,r}: seq "r d r", bits 1 0 1.  bit[0] = 1 → the larger symbol → 'r'.  ✓
```
The mechanism: at each node the stored bit picks the child, and `rank` remaps the index into that child's shorter sequence — O(log|Σ|) steps, each an O(1) bitvector rank.

**Worked query — `rank_r("abracadabra", 7)` (how many `'r'` in first 7 chars):**
```
'r' ∈ right half {c,d,r} → root bit for 'r' is 1.
  rank₁(root, 7) = 2   (two 1-bits in first 7 → two chars went right)  → descend right with count 2
'r' ∈ {d,r} half → that child's bit for 'r' is 1.
  rank₁(child, 2) = ... → keep narrowing until the leaf for 'r'.
Result: count of 'r' among the first 7 chars.
```

- O(log|Σ|) rank/select/access on strings — each step is one bitvector rank (O(1) with a rank index)
- Total space ≈ n·⌈log|Σ|⌉ bits + o(n) — the bitvectors across a level sum to n bits
- Enables compressed representations of permutations, grids, graphs; core of many FM-index implementations

**FM-Index** (Ferragina & Manzini, 2000):
- Compressed full-text index based on Burrows-Wheeler Transform (BWT)
- Search any pattern of length m in O(m) time, regardless of text size
- Space: nH_k + o(n log|Σ|) bits (k-th order empirical entropy)
- Used in BWA/Bowtie for DNA alignment

**Elias-Fano Encoding** — quasi-succinct representation of sorted integers:
- Space: 2n + n⌈log(U/n)⌉ bits for n integers from universe [0, U)
- O(1) access, predecessor, successor
- Used in search engine posting lists (Lucene, Tantivy)

---

## 21. **Masstree**

**Used in:** Silo (in-memory OLTP), MICA, research KV stores

High-performance concurrent B+-tree trie hybrid for variable-length keys. From Mao et al. (EuroSys 2012).

**The problem it solves.** A B+-tree with variable-length string keys does slow byte-by-byte comparisons and stores long keys awkwardly; a pure trie is deep and pointer-chasing for long shared prefixes. Masstree gets the best of both: it treats keys as sequences of **8-byte slices**, comparing a whole slice as a single 64-bit integer (one machine compare, not a strcmp loop), and only spills to a deeper trie layer when many keys share an 8-byte prefix.

**The core idea — a trie of B+-trees keyed on 64-bit slices.** Each trie layer is a full B+-tree whose keys are 8-byte chunks of the original key, loaded as `uint64_t` (big-endian so integer order = lexicographic order). Keys that fit in ≤8 bytes never leave layer 0. Longer keys that collide on the first 8 bytes follow a border-node link to a layer-1 B+-tree keyed on bytes 8–15, and so on.

```
Key: "Hello World!"

8-byte slices used as B+-tree keys (compared as single uint64):

B+-tree Layer 0: key = "Hello Wo"  (0x48656C6C6F20576F as one 64-bit compare)
        │
        ▼ (border node links to next layer when >8 bytes share this prefix)
B+-tree Layer 1: key = "rld!\0\0\0\0"
        │
        ▼ value
```
**Worked lookup — `"Hello World!"` (12 bytes):**
```
slice0 = "Hello Wo" as uint64 → binary-search layer-0 B+-tree → border node → layer 1.
slice1 = "rld!" padded → binary-search layer-1 B+-tree → leaf → value.
Two 64-bit-compare descents instead of a 12-byte strcmp at every node.
```
**Optimistic concurrency (no read locks):** each node carries a version counter. A reader snapshots the version, reads, then re-checks it; if a writer bumped it mid-read (split/insert), the reader retries. Writers lock only the node they mutate. Readers never block → scales on many cores.

**Key properties:**
- Trie over 8-byte key slices, B+-tree at each trie layer
- Version-based optimistic concurrency (no read locks)
- Cache-line-sized nodes (15 keys per internal node)
- Prefetch-friendly: next node address known before comparison completes
- Handles variable-length keys without hashing

---

## 22. **CTrie (Concurrent Hash Trie)**

**Used in:** Scala standard library, concurrent caches, actor systems

Lock-free concurrent hash trie supporting linearizable snapshots.

**The problem it solves.** A HAMT (§4) is great for single-threaded immutable maps, but making it *concurrent* and lock-free is hard: a naive CAS on a shared child array races with other writers and can't give a consistent snapshot for iteration. CTrie adds a level of **indirection nodes (INodes)** so every mutation is a single CAS on one INode, and adds GCAS so you can take an O(1) atomic snapshot even while writers run.

**The core idea — INodes make the CNode arrays immutable.** A CNode (branch array) is never mutated in place; to change it you build a new CNode and CAS the *parent INode's* pointer from the old CNode to the new one. Because the array itself is immutable, readers holding the old pointer see a consistent frozen view — no torn reads. The INode is the single mutable, CAS-able cell.

```
    Root (INode)              INode = mutable pointer cell (the CAS target)
        │                     CNode = immutable branch array (never edited in place)
    ┌───┴───┐
    │ CNode │
    ├───┬───┤
  SNode INode INode           SNode = leaf key/value
  (leaf)  │     │
        CNode  CNode
```
**Worked lock-free insert:**
```
insert(k,v):
  1. navigate to the INode whose CNode should hold k (hash-slice like HAMT).
  2. read its CNode (immutable snapshot).
  3. build CNode' = old CNode + new SNode(k,v) (copy-on-write of that one array).
  4. CAS(inode.main, oldCNode, CNode').
     success → done, linearizable at the CAS.
     failure → another writer won; RE-READ and retry from step 2.
No locks; contention just means a retry loop, and only the contended INode is touched.
```
**GCAS snapshots (O(1) atomic):** a snapshot bumps a global generation number. Reads/writes use GCAS (generation-compare-and-set): a node tagged with an old generation is lazily copied to the new generation on first touch, so the snapshot and live tree diverge copy-on-write. Iteration over the snapshot is linearizable and never blocks writers.

**Key properties:**
- Insert/delete/lookup all lock-free via single CAS on INode
- O(1) atomic snapshots via GCAS (generational CAS)
- Automatic contraction (removes empty nodes)
- Linearizable iteration from snapshot

**Paper:** Prokopec et al., "Concurrent Tries with Efficient Non-Blocking Snapshots" (PPoPP 2012)

---

## 23. **Pairing Heap**

**Used in:** Network routing (Dijkstra), GCC `__gnu_pbds`, event simulators

Simplest heap with amortized efficiency rivaling Fibonacci heaps.

**The problem it solves.** Dijkstra/Prim want a priority queue with fast `decrease-key`. Fibonacci heaps give the best asymptotics but their bookkeeping (marks, cascading cuts, sibling lists) is slow in practice and painful to code. Pairing heaps are a **multiway heap-ordered tree** with almost no bookkeeping — just child/sibling pointers — yet match Fibonacci heaps within a log factor and beat them on real inputs.

**The core idea — everything reduces to `meld` (merge two heaps).** Meld = compare two roots, hang the larger-root tree as the leftmost child of the smaller root. O(1). `insert` = meld a singleton. `decrease-key` = cut the node's subtree out, meld it back at the root. The only real work is `delete-min`, and its analysis rides on a clever **two-pass** melding of the orphaned children.

```
        3                    delete-min removes root 3, orphaning its children: 5, 8, 9
       /|\
      5  8  9    →   children list: [5] [8] [9]   (each may head its own subtree)
     /|    |
    12 7   14
       |
       11
```
**Worked delete-min (two-pass):**
```
Pass 1 (left→right, pair up adjacent siblings and meld each pair):
  meld(5,8) → 5 is smaller → 8 becomes child of 5   → tree(5)
  9 is unpaired → stays as tree(9)
  result list: [tree(5)]  [tree(9)]
Pass 2 (right→left, fold the paired results into one):
  meld(tree(9), tree(5)) → 5 smaller → 9 hangs under 5 → new root 5.
New heap root = 5. Total work O(#children); amortized O(log N) per delete-min.
```
The two passes are what prevent a degenerate left-spine from making later operations O(N) — the single-pass ("naive") variant lacks the amortized bound.

**Key properties:**
- O(1) insert, merge, find-min
- O(log N) amortized delete-min
- O(log N) amortized decrease-key (conjectured O(1))
- Much simpler than Fibonacci heaps; often faster in practice
- Two-pass pairing: left-to-right pairing, then right-to-left merging

**Comparison:**

| Heap | find-min | insert | decrease-key | delete-min | merge |
|------|----------|--------|--------------|------------|-------|
| Binary | O(1) | O(log N) | O(log N) | O(log N) | O(N) |
| Fibonacci | O(1) | O(1)* | O(1)* | O(log N)* | O(1) |
| Pairing | O(1) | O(1) | O(log N)* | O(log N)* | O(1) |
| Strict Fibonacci | O(1) | O(1) | O(1) | O(log N) | O(1) |

*amortized

---

## 24. **R-Tree / R*-Tree**

**Used in:** PostGIS, SQLite R*-Tree, MongoDB 2dsphere, game engines

Spatial index for rectangles, polygons, and multi-dimensional data.

**The problem it solves.** A B-tree indexes one ordered dimension; "find everything overlapping this map rectangle" has no single sort order. An R-tree generalizes the B+-tree to 2D+ by indexing **minimum bounding rectangles (MBRs)**: each internal entry is a box that encloses all its children's boxes, so a window query prunes any subtree whose MBR doesn't intersect the query — usually visiting a tiny fraction of the tree.

**The core idea — hierarchically nested bounding boxes.** Leaves hold object MBRs; each parent's MBR is the union of its children's. Overlap is allowed (unlike a grid), which is what lets it stay balanced under arbitrary insertions — but too much overlap kills pruning, which is exactly what R*-tree's insert heuristics minimize.

```
         ┌───────────────────────────────────┐
         │ R1                                │
         │  ┌────────┐    ┌──────────────┐   │
         │  │ R3     │    │ R4           │   │
         │  │ ■  ■   │    │  ■    ■      │   │
         │  │   ■    │    │     ■        │   │
         │  └────────┘    └──────────────┘   │
         └───────────────────────────────────┘
         ┌───────────────────────────────────┐
         │ R2                                │
         │  ┌──────┐  ┌──────────┐          │
         │  │ R5   │  │ R6       │          │
         │  │ ■  ■ │  │  ■  ■ ■  │          │
         │  └──────┘  └──────────┘          │
         └───────────────────────────────────┘
```

**Worked window query — find objects intersecting box Q:**
```
Root has children R1, R2 (their MBRs). Test Q against each MBR:
  Q ∩ MBR(R1)? yes → recurse into R1.   Q ∩ MBR(R2)? no → PRUNE entire R2 subtree.
Inside R1: Q ∩ MBR(R3)? yes → check its leaf points. Q ∩ MBR(R4)? no → prune.
Only the R3 leaf's points are tested for exact intersection. Overlap between R3,R4 MBRs
would force checking both → why minimizing overlap (R*-tree) matters.
```
**The split problem — why R*-tree exists.** When a node overflows, you must split its entries into two new MBRs. A bad split makes big overlapping boxes that defeat pruning. Guttman's original tried to minimize total area; R*-tree jointly minimizes overlap + margin + area and *forcibly reinserts* some entries on overflow, yielding 10–30% faster queries.

**R*-Tree improvements** (Beckmann et al., 1990):
- Forced reinsert on overflow → better node utilization
- Combined optimization of overlap, margin, and area
- Consistently 10-30% better query performance than R-tree

**SOTA variants:**
- **STR-packed R-tree** (bulk-loading): Sort-Tile-Recursive for static datasets
- **Priority R-tree**: optimal worst-case I/O for window queries
- **Hilbert R-tree**: uses Hilbert curve for spatial locality

---

## 25. **Fenwick Tree (Binary Indexed Tree)**

**Used in:** Competitive programming, count inversions, range-frequency queries, BIT-based database statistics

Supports prefix sum queries and point updates in O(log N) with minimal space.

**The problem it solves.** You want both point-update *and* prefix-sum in O(log N). A plain array gives O(1) update but O(N) prefix sum; a prefix-sum array gives O(1) query but O(N) update. A Fenwick tree balances both at O(log N) in *exactly N integers* (no tree pointers) — the trick is letting index arithmetic on the *lowest set bit* define an implicit tree.

**The core idea — each index covers a range whose length is its lowest set bit.** `BIT[i]` stores the sum of a range of length `LSB(i) = i & (-i)` ending at i. To query a prefix, jump down by stripping the LSB (covers disjoint power-of-two blocks). To update, climb by adding the LSB (hits every block that contains i). Both walks touch ≤ log N indices because each step clears/adds a bit.

```
Array:   [1, 3, 2, 5, 1, 7, 3, 2]   (1-indexed)
BIT:     [1, 4, 2, 10, 1, 8, 3, 24]
         BIT[i] covers (i−LSB(i), i]. e.g. BIT[4]=1+3+2+5=11? here 10 uses given data;
         BIT[6] covers (4,6] = arr[5]+arr[6]=1+7=8. BIT[8] covers (0,8] = whole = 24.

Tree structure (parent via i + LSB(i)):
Index:    1    2    3    4    5    6    7    8
          │    │    │    │    │    │    │    │
          └──→ 2    │    │    │    │    │    │
               └────┴──→ 4    │    │    │    │
                         │    └──→ 6    │    │
                         │         └────┴──→ 8
                         └──────────────────→ 8

prefix_sum(7) = BIT[7] + BIT[6] + BIT[4]  (strip lowest set bit each step)
update(3, +5): BIT[3] += 5, BIT[4] += 5, BIT[8] += 5  (add lowest set bit)
```
**Worked prefix_sum(7) — bit walk (7 = 0b111):**
```
i=7 (0b111): add BIT[7];  i −= LSB(7)=1  → i=6
i=6 (0b110): add BIT[6];  i −= LSB(6)=2  → i=4
i=4 (0b100): add BIT[4];  i −= LSB(4)=4  → i=0 stop.
Sum of arr[1..7] = BIT[7]+BIT[6]+BIT[4], three disjoint blocks of length 1,2,4. O(log N).
```
**Worked update(3,+5) — bit walk (3 = 0b011), N=8:**
```
i=3 (0b011): BIT[3]+=5;  i += LSB(3)=1  → i=4
i=4 (0b100): BIT[4]+=5;  i += LSB(4)=4  → i=8
i=8 (0b1000):BIT[8]+=5;  i += LSB(8)=8  → i=16 > N stop.
Exactly the blocks that CONTAIN index 3 got the delta. Query & update walk are inverses.
```

**Key properties:**
- Space: exactly N integers (no overhead)
- Implementation: ~10 lines of code
- 2D Fenwick tree for rectangle sum queries
- Supports order-statistic queries (find k-th smallest)

---

## 26. **Persistent / Retroactive Data Structures**

**Used in:** Git (Merkle trees), Datomic, functional languages (Haskell, Clojure), undo systems

Structures that preserve all previous versions after modification.

**The problem it solves.** Undo history, time-travel queries (Datomic "as-of"), Git commits, and functional data all need *old versions to stay queryable* after an update. Copying the whole structure per edit is O(N) per version. Persistence techniques give a new version in O(log N) (or O(1)) extra space by **sharing everything that didn't change**.

**The core idea — two ways to keep the past cheap.** (1) **Path copying:** copy only the nodes on the root-to-change path; every off-path subtree is shared by pointer with prior versions. (2) **Fat nodes:** never copy — instead each node stores a *list of (version, value)* entries, so a field can hold different values in different versions. Path copying is simpler and cache-friendly; fat nodes use less space but need a version-search per field access.

```
Path Copying (persistent balanced BST):

Version 1:       5              Version 2:       5'
                / \                             / \
               3   7                           3   7'
              / \                              / \   \
             1   4                            1   4   8  (new)

Only nodes on root-to-change path are copied: O(log N) per update.
Shared structure: nodes 1, 3, 4 are shared across versions.
```

**Types:**
| Type | Query old | Update old | Space per op |
|------|-----------|------------|--------------|
| Partial | O(1) | No | O(log N) |
| Full | O(1) | O(1) → new version | O(log N) |
| Confluent | O(1) | Merge versions | O(log² N) |
| Retroactive | O(1) | Modify past ops | O(√N) to O(polylog N) |

**Fat Node method**: store all versions at each node, O(1) amortized space per change, O(log V) query for version V.

**Retroactive** (Demaine et al., 2007): modify operations in the *past* and propagate effects forward. Partially retroactive priority queues in O(√N log N).

---

## 27. **Quotient Filter**

**Used in:** RocksDB (experimental), SSD FTL layers, deduplication systems

Like Bloom filters but supports merging, resizing, and counting — on cache-friendly contiguous memory.

**The problem it solves.** A Bloom filter (§14) sets k *random* bits per key → k cache-line misses per query and no deletes/resize/merge. A quotient filter stores each key's fingerprint in *one contiguous array* addressed by part of the fingerprint, so queries touch one cluster (often one cache line), and — because the full fingerprint is recoverable — it supports **delete, count, resize, and merge**, which Bloom can't.

**The core idea — split the fingerprint into quotient (address) + remainder (stored).** A p-bit fingerprint splits into the top `q` bits (the **quotient** = home slot) and the low `r` bits (the **remainder**, stored in that slot). Collisions (same quotient) form a **run**; adjacent runs form a **cluster**, kept sorted via linear probing. Three metadata bits per slot — `is_occupied`, `is_continuation`, `is_shifted` — let you reconstruct which remainders belong to which quotient even after shifting.

```
Fingerprint f = quotient (fq) : remainder (fr)

Slot addressed by fq, stores fr in a linear-probing cluster:

Slots:  [fr_0 | fr_1 | fr_2 | fr_3 | fr_4 | ...]
Meta:    occ   occ    cont   run    occ    ...
         is_occupied  is_continuation  is_shifted

Cluster: consecutive remainders belonging to same or adjacent quotients
```
**Worked insert & lookup.** Fingerprint of x = quotient 3, remainder 0xA2:
```
insert(x): home slot = 3. set is_occupied[3]=1. If slot 3 free → store 0xA2 there.
  If slot 3 taken by another quotient's run → linear-probe right to the end of
  quotient-3's run, shifting later remainders right and setting is_shifted on them,
  keeping each run sorted. Store 0xA2 in the opened gap.
lookup(x): quotient=3. is_occupied[3]? no → DEFINITELY absent.
  yes → scan quotient 3's run (bounded by continuation/shifted bits) for remainder 0xA2.
  found → "probably present"; not found → absent. False positive only if a DIFFERENT
  key shares both quotient 3 AND remainder 0xA2 (prob 2^{−r}).
delete(x): find 0xA2 in the run, remove it, shift the cluster left to close the gap.
```

**Key properties:**
- Cache-friendly: single contiguous array (vs. Bloom's random access)
- Supports deletes and counting (Counting Quotient Filter)
- Mergeable: two QFs can be combined in linear time
- Resizable: double size by splitting fingerprints
- Space: ~10-25% more than Bloom, but with far more functionality

**Rank-Select Quotient Filter (RSQF)** (Pandey et al., SIGMOD 2017): uses rank-select on metadata bits for O(1) operations.

---

## 28. **Log-Structured / Append-Only B-Trees**

**Used in:** LMDB, FoundationDB Record Layer, Btrfs, ZFS, CockroachDB (Pebble)

Copy-on-write B-trees that never modify pages in place. Enables snapshots and crash safety for free.

**The problem it solves.** In-place B-tree updates risk torn pages on crash (half-written node → corrupt index) and need a WAL + careful recovery. If you *never overwrite* a page — always write a new copy — then a crash simply leaves the old tree intact, and any old root is a consistent snapshot. Durability and snapshots fall out of the write discipline itself.

**The core idea — copy the whole root-to-leaf path, publish a new root atomically.** Changing a leaf means writing a new leaf, then a new parent pointing at it, up to a new root. The old pages are untouched, so old roots remain valid read views. Committing = atomically swapping the "current root" pointer (one word / one fsync of a header). LMDB writes two root slots and alternates → torn-write-proof commit.

```
Write "X" to leaf:

Before:
  Root(v1) → [A, B] → ... → Leaf_B(v1): [old data]

After (copy path to root):
  Root(v2) → [A, B'] → ... → Leaf_B(v2): [X, old data]
  Root(v1) → [A, B]  → ... → Leaf_B(v1): [old data]   ← still valid snapshot!

Old pages reclaimed by garbage collection after no readers reference them.
```

**Key properties:**
- Crash recovery: just point to last valid root (no WAL needed for structural consistency)
- Free snapshots: any old root is a consistent read view
- Write amplification: full path copy (~4-5 pages per write at typical heights)
- LMDB: single-writer, dual-root COW B+ tree with memory-mapped I/O

**LLAMA** (Levandoski et al., VLDB 2013): Log-structured access method for Bw-tree, separates logical/physical pages.

---

## 29. **XOR-Linked List / Compressed Pointer Structures**

**Used in:** Memory-constrained embedded systems, Linux kernel `hlist`

Doubly-linked list using XOR of prev/next pointers to halve pointer overhead.

**The problem it solves.** A doubly-linked list needs two pointers per node to walk both ways (16 bytes on 64-bit). On memory-constrained targets that overhead matters. XOR trick: since you always *arrive* from one neighbor, you already know its address — so one field `prev⊕next` suffices to derive the other direction, halving pointer storage.

**The core idea — store the XOR, recover the neighbor with the address you came from.** `npx(node) = addr(prev) ⊕ addr(next)`. Endpoints XOR with 0 (NULL). Because `x⊕y⊕x = y`, if you're at B having come from A, then `next = npx(B) ⊕ A`; going backward, `prev = npx(B) ⊕ next`.

```
Standard doubly-linked: each node stores prev AND next (16 bytes on 64-bit)

XOR list: each node stores prev ⊕ next (8 bytes)
  A         B         C         D
  [0⊕B]    [A⊕C]    [B⊕D]    [C⊕0]
```
**Worked forward traversal A→D (carry the previous address):**
```
start: cur=A, prev=0.  next = npx(A)⊕prev = (0⊕B)⊕0 = B.   → visit B
       prev=A, cur=B.  next = npx(B)⊕prev = (A⊕C)⊕A = C.   → visit C
       prev=B, cur=C.  next = npx(C)⊕prev = (B⊕D)⊕B = D.   → visit D
       prev=C, cur=D.  next = npx(D)⊕prev = (C⊕0)⊕C = 0.   → end.
Backward is symmetric: start at D with prev=0, same formula yields C,B,A.
```
**Caveats:** you can't jump to an arbitrary node and walk (must know a neighbor); breaks with pointer-tagging, ASLR-relative reloc, or GC that moves objects; and it's opaque to debuggers — which is why it's a niche embedded/kernel trick, not general-purpose.

**Related compressed structures:**
- **Unrolled linked list**: multiple elements per node for cache efficiency
- **Hashed array tree**: O(√N) wasted space, O(1) indexed access
- **Cache-oblivious B-tree** (Bender et al., 2000): optimal I/O for any cache/block size without knowing hardware parameters

---

## 30. **Dancing Links (DLX)**

**Used in:** Sudoku solvers, pentomino tiling, exact cover problems, SAT preprocessing

Knuth's technique for efficient backtracking on sparse binary matrices via circular doubly-linked lists.

**The problem it solves.** Exact-cover backtracking (Sudoku, tiling, N-queens) repeatedly removes rows/columns and — on backtrack — restores them. Doing this with array marking or copying is slow and allocates. DLX represents the sparse matrix as a mesh of circular doubly-linked lists so that removing an element is 4 pointer writes and *undoing* it is 4 more — using the very pointers the removed node still holds.

**The core idea — "dancing" links: a removed node remembers how to reinsert itself.** To unlink node x: `x.left.right = x.right; x.right.left = x.left`. Crucially x's *own* left/right pointers are left untouched. So to relink: `x.left.right = x; x.right.left = x` — x still points at exactly the neighbors it belongs between. Remove and restore are perfect inverses with zero extra state → O(1), no allocation, ideal for deep backtracking.

```
Exact cover matrix (columns = constraints, rows = choices):

     1  2  3  4  5  6  7
A: [ 1  0  0  1  0  0  1 ]
B: [ 1  0  0  1  0  0  0 ]
C: [ 0  0  0  1  1  0  1 ]
D: [ 0  0  1  0  1  1  0 ]
E: [ 0  1  1  0  0  1  1 ]
F: [ 0  1  0  0  0  0  1 ]

Cover column: unlink it and all rows intersecting it
Uncover: relink in reverse order — O(1) per link!
```
**Worked search step (Algorithm X via DLX):**
```
1. choose column with fewest 1s (say col 1: rows A,B) → minimizes branching.
2. COVER col 1: remove col-1 header, and for each row in it (A,B), remove those rows'
   other columns' cells → those constraints are now "satisfied", shrinking the matrix.
3. try row A: recurse on the reduced matrix. Solution found → record A.
4. backtrack: UNCOVER col 1 in EXACT REVERSE order of the covering → matrix restored
   bit-for-bit, because every unlinked node still points at its old neighbors.
5. try row B next, etc.
```
Reverse-order uncover is mandatory: relinks must happen in the mirror sequence so each node reinserts into a neighborhood identical to when it left.

**Key properties:**
- Cover/uncover are O(1) per link via pointer relinking
- No memory allocation during search
- Explores ~10⁶ nodes/second on modern hardware
- Generalization: Algorithm C for colored/multiplicity constraints

---

# Distributed, Memory-Efficient & Compute-Efficient Structures

---

## 31. **Roaring Bitmaps**

**Used in:** Apache Lucene/Solr, Spark, ClickHouse, Druid, Pilosa, Git

Hybrid compressed bitmap that adapts container type per 16-bit chunk. Dominates every other compressed bitmap format in practice.

**The problem it solves.** A raw bitset for 32-bit IDs is 512 MB regardless of density — wasteful when sparse. Run-length formats (WAH/EWAH) compress runs but are slow for random access and set ops. Roaring splits the universe into 2¹⁶-value chunks and picks the *best representation per chunk* — sorted array (sparse), bitset (dense), or run-length (clustered) — so it's compact *and* supports fast SIMD set operations.

**The core idea — high 16 bits pick a container, low 16 bits live inside it.** A top-level sorted array of chunk-keys maps to per-chunk containers. Because each container is small (≤ 8 KB), set operations dispatch on the *pair* of container types and use a specialized, often SIMD, routine.

```
Universe of 32-bit integers: split each value into [high 16 bits : low 16 bits]

high 16 bits → selects container
low 16 bits  → stored inside container

Container types per chunk (chosen automatically):
┌──────────────────────────────────────────────────────────────────┐
│ Array Container   │ cardinality < 4096  │ sorted u16 array      │
│ Bitmap Container  │ cardinality ≥ 4096  │ 8KB fixed bitset      │
│ Run Container     │ many runs           │ [start, length] pairs │
└──────────────────────────────────────────────────────────────────┘

Example: {1, 3, 5, 100, 65536, 65537, 65538}

Chunk 0 (high=0): Array [1, 3, 5, 100]          ← sparse, use sorted array
Chunk 1 (high=1): Run [(0, 3)]                   ← 3 consecutive, use RLE
```
**Worked intersection A ∩ B — dispatch per container-type pair:**
```
Align by chunk key (merge the two top-level key arrays). For each shared chunk key:
  Array ∩ Array   → galloping/merge of two sorted u16 lists (SIMD shuffle for small)
  Bitmap ∩ Bitmap → word-wise AND of 8KB bitsets (AVX-512, 512 bits/instr) + popcount
  Array ∩ Bitmap  → for each u16 in the array, test its bit in the bitmap (1 load each)
  Run   ∩ anything→ walk runs, clip against the other container
Result container's TYPE is re-chosen from its cardinality (may downgrade bitmap→array).
`and_cardinality` counts matches WITHOUT building the result → common in analytics.
```

**Key properties:**
- AND/OR/XOR across containers via type-specific fast paths
- SIMD-accelerated: AVX2/AVX-512 intersection on bitmap containers
- ~2-100× smaller than uncompressed bitsets on real data
- ~10× faster than WAH, Concise, EWAH compressed bitmaps
- `roaring_bitmap_and_cardinality()` — counts intersection without materializing

**Roaring+Run (2016):** Added run containers; further 2-4× compression on sequential IDs.

**Roaring 64-bit:** Extends to 64-bit universe via top-level ART or B-tree keyed by high 32 bits.

---

## 32. **Swiss Table (Abseil `flat_hash_map`)**

**Used in:** Google production (Abseil), Rust `hashbrown`, Go runtime (1.24+), CockroachDB

SIMD-probed, open-addressing hash table. The modern standard for in-memory hash maps.

**The problem it solves.** Chaining hash maps (`std::unordered_map`) chase a pointer per probe → 2–3 cache misses and heap nodes per entry. Swiss tables are flat (keys inline, no nodes) and check **16 slots at once** with one SIMD compare against a byte of hash, cutting lookups to ~1 cache miss. This is the design now in Abseil, Rust `hashbrown`, and the Go 1.24 runtime.

**The core idea — a separate control byte array probed with SIMD.** Each slot has a 1-byte control: either EMPTY, DELETED, or the low 7 bits of the key's hash (a fingerprint, `h2`). The high bits (`h1`) pick the starting group. A lookup loads 16 control bytes into an SSE register, compares all 16 against the target fingerprint in one instruction, and only does full key-equality on the (usually ≤1) slots that match — most of the 16 are rejected without touching the keys at all.

```
Layout: groups of 16 slots, each with 1-byte metadata

Group (16 bytes, fits SSE register):
┌────────────────────────────────────────────────────────────────┐
│ ctrl[0] ctrl[1] ctrl[2] ... ctrl[15]                           │
│  0x4A    EMPTY   0x4A   ...  DEL                               │
└────────────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────────────┐
│ slot[0] slot[1] slot[2] ... slot[15]  (key-value pairs)        │
└────────────────────────────────────────────────────────────────┘

Lookup "key":
  h = hash(key)
  group_idx = h1(h) % num_groups
  h2_match  = h2(h)  ← 7-bit fingerprint stored in ctrl byte

  __m128i ctrl  = _mm_loadu_si128(ctrl_group);
  __m128i match = _mm_cmpeq_epi8(ctrl, _mm_set1_epi8(h2_match));
  uint16_t mask = _mm_movemask_epi8(match);
  // Each set bit → candidate slot; check full key equality
```
**Worked probe continuation — what if the group has no match and no EMPTY:**
```
1. SIMD-match fingerprint in group G. Any bit set → check those keys; hit → return.
2. No fingerprint match, but is there an EMPTY slot in G? (another SIMD compare vs EMPTY)
   EMPTY present → key is DEFINITELY absent, stop (open addressing invariant).
   No EMPTY (group full) → advance to next group (quadratic probing) and repeat step 1.
DELETED (tombstone) bytes match neither fingerprint nor EMPTY → probe continues past them;
periodic "rehash in place" reclaims tombstones when they accumulate.
```

**Key properties:**
- 1 byte metadata overhead per slot (7-bit hash + EMPTY/DELETED sentinel)
- Probe entire group (16 candidates) in single SIMD instruction
- ~1.0-1.5 cache misses per lookup on average (vs. 2-3 for chaining)
- Flat layout: all keys inline, no pointer chasing, no linked lists
- Tombstone cleanup via "rehash in place" on high deletion workloads

**Comparison (ns/op, 64-byte keys, 1M entries):**

| Operation | `std::unordered_map` | Swiss Table | Improvement |
|-----------|---------------------|-------------|-------------|
| Lookup (hit) | ~90 ns | ~25 ns | 3.6× |
| Lookup (miss) | ~80 ns | ~18 ns | 4.4× |
| Insert | ~120 ns | ~35 ns | 3.4× |

**F14 (Facebook):** Similar concept, 16-byte SIMD chunks, slightly different collision strategy. Used in Folly.

---

## 33. **Merkle Trees / Merkle-Patricia Trie**

**Used in:** Git, Bitcoin/Ethereum, S3 (anti-entropy), Cassandra (repair), Certificate Transparency, IPFS

Hash tree enabling O(log N) proof of inclusion and efficient diff/sync between replicas.

**The problem it solves.** Two questions at scale: "prove block D is in this dataset without sending me the whole dataset" and "which parts of my copy differ from yours". Hashing every block into a tree makes the **root hash a fingerprint of all data** — any change flips the root, and you can prove/locate changes in O(log N) hashes instead of O(N) data.

**The core idea — parent hash = hash of children's hashes.** Leaves hash the data; each internal node hashes the concatenation of its children's hashes. Two properties follow: (1) an **inclusion proof** is just the sibling hashes along the leaf-to-root path — recompute up and compare to the trusted root; (2) **diffing** two trees means descending only where the two roots' subtree hashes differ, so identical subtrees are skipped wholesale.

```
                    H(H01 + H23)          ← root hash: summary of all data
                   /            \
            H(H0 + H1)        H(H2 + H3)
             /      \           /      \
          H(D0)   H(D1)     H(D2)   H(D3)    ← leaf hashes
           |        |         |        |
          D0       D1        D2       D3       ← data blocks

Proof that D2 is in the tree: provide H(D3) and H(H0+H1)
Verifier computes: H(H(D2) + H(D3)) → H23, then H(H01 + H23) → compare to root ✓
```
**Worked inclusion proof — prove D2 belongs, given only trusted root:**
```
Prover sends D2 + sibling hashes along its path: [ H(D3), H01 ].  (log N = 2 hashes)
Verifier: a = H(D2)                    (hash the claimed block)
          b = H(a ‖ H(D3)) = H23       (combine with right sibling)
          r = H(H01 ‖ b)               (combine with left sibling up top)
          r == trusted_root ?  yes → D2 is authentically in the set; no → tampered.
Verifier never saw D0,D1,D3 — just 2 hashes. Forging D2 requires a hash collision.
```
**Worked anti-entropy diff (Cassandra repair):**
```
Two replicas exchange only ROOT hashes. Equal → ranges identical, done, zero data moved.
Differ → recurse: exchange the two child hashes, follow only the mismatching side, until
you reach the differing leaves → transfer just those. Cost O(#differences · log N).
```

**Merkle-Patricia Trie (Ethereum):**
```
Extension: shared prefix "abc"
    │
 Branch: 16-way (hex nibbles) + value slot
    ├─0→ ...
    ├─7→ Leaf: remaining="de", value=100
    └─f→ Leaf: remaining="00", value=200

Each node hashed → content-addressed → tamper-evident state trie
```

**Key properties:**
- Proof size: O(log N) hashes, ~1KB for billion-entry tree
- Diff between two roots: only walk where hashes diverge → O(changes) sync
- Anti-entropy: Cassandra uses Merkle trees to detect replica divergence per range
- Certificate Transparency: append-only Merkle tree with O(log N) consistency proofs

**SOTA variants:**
- **Verkle Tree** (Ethereum roadmap): vector commitments replace hashes, ~3× smaller proofs
- **Prolly Tree** (Dolt, Noms): content-defined chunking + Merkle, enables structural diff of databases
- **Authenticated Data Structures** (Miller et al.): generic framework, extends to skip lists, B-trees

---

## 34. **Consistent Hashing (Ring, Jump, Maglev)**

**Used in:** DynamoDB, Cassandra, Memcached, Nginx, Envoy, Kafka partition assignment

Distributes keys across nodes such that adding/removing a node only remaps O(K/N) keys.

**The problem it solves.** Plain `hash(key) % N` shatters on resize: change N and *almost every* key remaps → a cache/shard reshuffle that stampedes the backend. Consistent hashing arranges keys and nodes on the same ring so adding/removing one node only remaps the keys that fell in that node's arc — O(K/N) keys move, not O(K).

**The core idea — hash keys and nodes into the same space; a key belongs to the next node clockwise.** Adding node C only steals the keys between C and its predecessor from C's successor; everyone else is untouched. **Virtual nodes** (each physical node placed at many ring points) smooth out the otherwise-lumpy arc sizes and spread a departed node's load across many survivors.

```
Classic Ring (Karger et al., 1997):

         Node A (3 vnodes)
          ↙  ↑  ↖
   A₁ ●────────● A₃       Hash ring [0, 2³²)
      │          │
      │   keys   │         key hashed → walk clockwise → first node
      │          │
   B₂ ●────────● B₁
          ↗  ↓  ↘
         Node B (3 vnodes)

Add Node C: only keys between C's predecessors remapped
```
**Worked lookup + add-node:**
```
lookup(key): h = hash(key) → walk clockwise on the ring (binary search the sorted vnode
             positions) → first vnode ≥ h → its physical node owns the key.
add node C at ring position p: find the successor vnode S just clockwise of p.
             Only keys with hash in (predecessor(p), p] — currently served by S — move to C.
             Every other key's clockwise-successor is unchanged → no global reshuffle.
```
**Jump hash intuition (no ring, no memory):** the loop simulates "as buckets grow from 1→N, each time a new bucket appears a key jumps to it with probability 1/(new count)". The final bucket it lands on is deterministic per key and perfectly balanced — but only the *last* bucket can be added/removed (no arbitrary node removal), which is its one limitation.

**Jump Consistent Hash** (Lamping & Veach, Google 2014):
```cpp
// Entire implementation: 7 lines, zero memory, perfect balance
int32_t JumpConsistentHash(uint64_t key, int32_t num_buckets) {
    int64_t b = -1, j = 0;
    while (j < num_buckets) {
        b = j;
        key = key * 2862933555777941757ULL + 1;
        j = (b + 1) * (double(1LL << 31) / double((key >> 33) + 1));
    }
    return b;
}
```
- O(ln N) time, O(1) space, perfectly uniform
- Limitation: only supports appending/removing the last bucket

**Maglev Hashing** (Google, 2016):
- Builds a lookup table via permutation-based populate
- O(1) lookup after O(M × N) build (M = table size, N = nodes)
- Minimal disruption: changing one backend affects ~1/M of entries
- Used in Google's Maglev network load balancer (10Gbps+ per machine)

**Rendezvous (Highest Random Weight) Hashing:**
- For each key, score = hash(key, node_id), pick highest
- O(N) per lookup but trivially handles weights and heterogeneous nodes
- Used in: Microsoft Azure, Akamai CDN

---

## 35. **T-Digest / DDSketch (Streaming Quantiles)**

**Used in:** Elasticsearch, Prometheus, Datadog, Apache Flink, New Relic

Compact data structures for accurate quantile estimation (p50, p99, p99.9) over streaming data.

**The problem it solves.** Exact quantiles need all the data sorted — O(N) memory, impossible on a firehose of latencies. You mostly care about *tail* quantiles (p99, p99.9) with tight accuracy. T-Digest keeps a few KB of weighted **centroids** that are deliberately *tiny near the tails and fat in the middle*, so tail quantiles stay accurate while total memory is bounded and mergeable across shards.

**The core idea — cluster the data into centroids sized by a scale function.** Each centroid is (mean, count). The scale function `k(q)` allots more, smaller centroids where q→0 or q→1 (arcsin bunches them at the extremes) and lets middle centroids absorb many points. Adding a value merges it into the nearest centroid *if* that centroid's size is still under its k-budget; else it starts a new one.

```
T-Digest: variable-width centroids, precise at tails

Centroids: (mean, count) sorted by mean
┌─────┬─────┬─────┬─────┬─────┬─────┬─────┬─────┐
│ c=1 │ c=3 │ c=15│ c=40│ c=38│ c=12│ c=4 │ c=1 │
│ 0.1 │ 1.2 │ 5.0 │ 50  │ 90  │ 98  │ 99.5│99.99│
└─────┴─────┴─────┴─────┴─────┴─────┴─────┴─────┘
  ← small clusters near extremes: high accuracy at p0, p100 →
  ← large clusters in middle: less precision needed there →

Scale function controls centroid size: k(q) ~ δ/2π × arcsin(2q - 1)
→ centroids near q=0 and q=1 stay tiny → tail accuracy
```

**Worked quantile query — estimate p99 from centroids:**
```
Centroids sorted by mean, with cumulative counts. Total count = 10000. Target rank = 0.99·10000 = 9900.
Walk cumulative counts until the centroid whose range spans rank 9900:
  ...centroid at cum 9880 (mean 99.5, count 4)  → spans ranks [9880, 9884)... keep going
  centroid at cum 9900 (mean 99.7) → rank 9900 lands here.
Interpolate LINEARLY between this centroid's mean and its neighbor's, weighted by where
9900 falls within the centroid's count span → p99 ≈ 99.7.
Tail centroids have count≈1 → almost no interpolation error at p99.9/p99.99.
```

**Key properties (T-Digest):**
- ~1-5KB regardless of data volume (compression param δ ≈ 100-200)
- Mergeable: combine T-Digests from different shards/time windows
- Relative error < 1% at extreme quantiles (p99.9, p99.99)
- ~100ns per add, ~200ns per quantile query

**DDSketch** (Masson et al., 2019, used in Datadog):
```
Logarithmic bins with guaranteed relative error:

bin_index = ⌊log_γ(value)⌋   where γ = (1+α)/(1-α)

Bins:  [...| count | count | count | count |...]
          0.9-1.0  1.0-1.1  1.1-1.21  ...

Relative accuracy α guaranteed for ALL quantiles (not just tails)
```
- Fully mergeable with no accuracy loss
- Fixed memory: ~2KB for α=1% across any data range
- CollapsingLowestDense variant: bounded memory even for extreme ranges

---

## 36. **Eytzinger Layout / Cache-Oblivious Search**

**Used in:** Database index nodes, SIMD binary search, high-frequency trading

Reorganizes sorted arrays into implicit tree layouts for branch-free, prefetch-friendly search.

**The problem it solves.** Textbook binary search on a sorted array is a branch-misprediction and cache-miss machine: each step jumps to an unpredictable index (the CPU can't prefetch it) and the `<` branch mispredicts ~50% of the time. Eytzinger lays the same keys out in **breadth-first (heap) order** so the children of the current node are at trivially computable indices `2i`, `2i+1` — which the hardware *can* prefetch — and the comparison becomes branchless.

**The core idea — store the implicit binary search tree in BFS order.** Index 1 = the middle element (tree root), index 2/3 = the two quartile elements, etc. Searching walks `i = 2i + (a[i] < key)` — always visiting one of two adjacent cache-predictable children, prefetching both before the compare even resolves.

```
Sorted array:     [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

Eytzinger layout: [_, 8, 4, 12, 2, 6, 10, 14, 1, 3, 5, 7, 9, 11, 13, 15]
                   ↑  └── root at index 1
                   unused
Children of node i: left = 2i, right = 2i+1  (implicit, no pointers)

Prefetch: at depth d, we know all possible nodes at depth d+2
          → prefetch both children before comparison resolves
```

```cpp
// Branchless Eytzinger search with prefetch
int eytzinger_search(int* a, int n, int key) {
    int i = 1;
    while (i <= n) {
        __builtin_prefetch(&a[2 * i]);      // prefetch children
        __builtin_prefetch(&a[2 * i + 1]);
        i = 2 * i + (a[i] < key);           // branchless: no misprediction
    }
    // i now encodes the result via bit manipulation
    return (i >> __builtin_ffs(~i)) - 1;
}
```
**Worked search — find key=6 in the layout above `[_,8,4,12,2,6,10,14,...]`:**
```
i=1: a[1]=8.  8<6? no  → i = 2·1 + 0 = 2
i=2: a[2]=4.  4<6? yes → i = 2·2 + 1 = 5
i=5: a[5]=6.  6<6? no  → i = 2·5 + 0 = 10   (past n → loop ends)
Decode: i=10=0b1010. The trailing path of "went-left" (0) bits encodes the answer index;
the bit-manipulation `i >> ffs(~i)` strips them to recover the sorted-array position of 6.
No branch ever mispredicted; both children of each node were prefetched a step early.
```

**Key properties:**
- ~2× faster than `std::lower_bound` for N > 1K (Khuong & Morin, 2017)
- Zero branch mispredictions (converted to conditional moves)
- Prefetch two levels ahead → cache misses fully overlapped
- Works because implicit tree has perfect locality at each level

**Van Emde Boas layout** (cache-oblivious):
- Recursively split tree into top and bottom √N subtrees
- Optimal for any unknown cache line / page size
- Asymptotically best I/O: O(logₐ N) where B = block size

**FAST** (Schlegel et al., 2009): SIMD-friendly tree with k-ary nodes matching SIMD width. Compare 4-16 keys per cycle.

---

## 37. **Flat Combining**

**Used in:** Concurrent stacks, queues, priority queues, counters, skip lists

Technique that batches concurrent operations through a single combiner thread, eliminating contention.

**The problem it solves.** Under high contention, lock-free structures thrash: every thread CASes the same cache line, most retry, and the line ping-pongs between cores (each miss ~100 cycles). Flat combining flips it — instead of N threads all fighting to touch the structure, one "combiner" thread grabs the lock and applies *everyone's* pending operations while they wait. The hot cache lines stay on one core, and the batch amortizes synchronization.

**The core idea — a publication list + one rotating combiner.** Each thread posts its request into a per-thread slot (a cheap store, no contention). Threads then try to acquire a single lock: the winner becomes combiner and *scans the publication list*, executing every posted op against the (plain, sequential) data structure and writing results back into each slot. Losers spin briefly on their own slot until the combiner fills in their result.

```
Thread 1 ──→ ┌─────────────────────┐
Thread 2 ──→ │  Publication List    │──→ Combiner thread
Thread 3 ──→ │  (per-thread slots)  │    (winner of lock)
Thread 4 ──→ └─────────────────────┘    applies ALL pending ops

Timeline:
  T1: publish(push, 5)  ─┐
  T2: publish(push, 3)   ├─→ T2 wins lock, becomes combiner
  T3: publish(pop)       ─┤   T2 executes: push(5), push(3), pop → result for T3
  T4: publish(push, 7)  ─┘   All threads get results without touching shared state
```

**Key properties:**
- Eliminates CAS contention: only one thread modifies the data structure
- Sequential data structure underneath — no concurrent complexity
- Throughput scales with core count despite single combiner
- Cache benefit: combiner has hot cache lines, batch amortizes misses
- ~2-10× throughput vs. lock-free alternatives at high contention

**Paper:** Hendler et al., "Flat Combining and the Synchronization-Parallelism Tradeoff" (SPAA 2010)

**Related — RCU (Read-Copy-Update):**
- Linux kernel's dominant read-side synchronization
- Readers: zero overhead (no locks, no atomics, no barriers on x86)
- Writers: copy structure, update, wait for grace period (all readers done), free old
- Used for: routing tables, module lists, `/proc` filesystem — anything read-mostly

---

## 38. **Minimal Perfect Hash Functions (MPHF)**

**Used in:** Compiler symbol tables, static databases, search engine indexes, bioinformatics (k-mer lookup)

Maps N keys to [0, N) with zero collisions and zero wasted slots. Build once, query O(1).

**The problem it solves.** For a *static, known* key set (compiler keywords, a genomics k-mer dictionary, a search index's terms) a general hash table wastes space on empty slots and pays for collision handling. An MPHF is a precomputed function that maps exactly those N keys onto 0..N−1 with **no collisions and no gaps** — so you can back it with a dense array (`value[mphf(key)]`) at ~2–4 bits/key of index overhead and one cache miss per lookup.

**The core idea — assign each key one of k hash slots so the assignment is solvable.** The classic (BDZ/hypergraph) construction hashes each key to 3 positions in an array (a 3-uniform hypergraph edge), then "peels" the hypergraph: repeatedly remove any slot touched by exactly one key, assigning that key there. If the array is ≥1.23·N, peeling succeeds w.h.p. The stored result is a small table of 2-bit values that, combined at query time, pick which of a key's 3 slots is *its* slot.

```
Keys: {"cat", "dog", "fish", "bird"}  →  MPHF  →  {0, 1, 2, 3}

No collisions, no empty slots, no probing — just one function evaluation.
```
**Worked construction (peeling) & query:**
```
BUILD: each key → 3 slots via h0,h1,h2 into array G (size 1.23N).
  Peel: find a slot with degree 1 (only one key maps there) → that key is "assigned" there,
        remove it, decrementing its other two slots' degrees → exposes new degree-1 slots.
  Repeat until all keys peeled (reverse of that order gives a valid assignment).
  Store a 2-bit value per slot so that (g[h0(k)]+g[h1(k)]+g[h2(k)]) mod 3 selects k's slot.
QUERY(k): compute h0,h1,h2; i = (g[h0]+g[h1]+g[h2]) mod 3; return h_i(k). O(1), 3 array reads.
Adding/removing keys → must rebuild (why it's for STATIC sets).
```

**RecSplit** (Esposito et al., 2020): near-optimal at 1.56 bits/key:
- Recursively split keys into buckets until solvable
- At each level, try random bijections; store the seed that works
- Theoretically optimal: information-theoretic lower bound is ~1.44 bits/key

**PTHash** (Pibiri & Trani, 2021): ~3-4 bits/key, extremely fast queries:
- Pilot-based construction: find pilot values that avoid collisions per bucket
- ~40ns queries (single cache miss), builds in ~100ns/key
- Used in genomics for k-mer dictionaries with billions of keys

**BBHash** (Limasset et al., 2017): embarrassingly parallel construction:
- Iterative: hash all keys, keep those with unique positions, repeat on remainder
- ~3 bits/key, parallelizes trivially across cores/machines
- Scales to billions of keys in minutes

**Use case — Static function (not just bijection):**
- Store f: keys → values by building MPHF, then array[mphf(key)] = value
- Lookup: array[mphf(key)] — O(1), zero wasted space
- Not suitable if keys change; rebuild required

---

## 39. **Compressed Sparse Row (CSR) / Graph Compression**

**Used in:** GraphBLAS, Neo4j, Scipy sparse matrices, PageRank, ML adjacency tensors

The standard in-memory format for large sparse graphs. Eliminates per-edge pointer overhead.

**The problem it solves.** `Vec<Vec<u32>>` adjacency lists scatter each vertex's edges across separate heap allocations → pointer overhead, poor locality, cache misses walking neighbors. Sparse matrices have the same issue. CSR packs all edges into **one contiguous array** with a per-vertex offset index, so a vertex's neighbors are a sequential slice — perfect for streaming scans (BFS, PageRank, SpMV) and SIMD.

**The core idea — two flat arrays: offsets + packed edges.** `offsets[v]..offsets[v+1]` delimits vertex v's slice inside the `edges` array. No per-vertex object, no pointers; `offsets` has |V|+1 entries, `edges` has |E| entries.

```
Adjacency list (naive): each vertex has a Vec<Edge> → pointer per vertex, overhead per vec

CSR: two flat arrays

Vertex:    0     1     2     3     4
          │     │     │     │     │
offsets:  [0,    2,    5,    5,    7,   8]   ← cumulative edge count
edges:    [1, 3, 0, 2, 4, _, 1, 3, 2]       ← destination vertices (packed)

Neighbors of vertex 1: edges[offsets[1]..offsets[2]] = edges[2..5] = [0, 2, 4]
```
**Worked SpMV (y = A·x, the core of PageRank) — one sequential pass:**
```
for v in 0..|V|:
    sum = 0
    for e in offsets[v] .. offsets[v+1]:   ← contiguous slice, prefetcher-friendly
        sum += weight[e] * x[ edges[e] ]    ← gather x by neighbor id
    y[v] = sum
Every access to `edges`/`weight` is sequential; only x[] is gathered. |E| multiply-adds
total, zero pointer chasing → why every graph engine and sparse BLAS uses CSR.
CSC (column-major twin) is the same idea transposed, for column/in-edge traversal.
```

**Key properties:**
- Space: exactly |V|+1 + |E| integers (no pointers, no overhead)
- Sequential scan of neighbors: perfect cache locality
- ~4-8× less memory than adjacency list with `Vec<Vec<u32>>`
- Immutable; for dynamic graphs use CSR + delta log or adjacency list

**WebGraph Framework** (Boldi & Vigna, 2004):
- Compresses web graphs to ~1-3 bits/edge (vs. 32 bits uncompressed)
- Techniques: reference chains, gap coding, ζ codes, interval encoding
- BV compression of a 1B edge web graph: ~400MB (vs. ~4GB CSR)

**Graph compression SOTA:**
- **Ligra+** (Shun et al., 2015): compressed in-memory graphs with parallel processing
- **Log(Graph)** (Besta et al., 2019): logarithmic-space succinct graph representations
- **Terrace** (Pandey et al., 2021): B-tree-based dynamic graph with CSR-like scan performance

---

## 40. **SIMD-Vectorized Structures**

**Used in:** DuckDB, ClickHouse, Velox, Polars, simdjson, hyperscan

Structures and algorithms redesigned from scratch to exploit 128/256/512-bit SIMD.

**The problem it solves.** Scalar code touches one value per instruction; a modern core has 256/512-bit vector units idle unless code is *shaped* for them. Analytical engines (DuckDB, ClickHouse, Velox) reorganize hot loops — hashing, filtering, joins, JSON parsing — so a single instruction processes 8/16/32/64 values, turning branch-heavy per-row logic into branchless data-parallel sweeps. The gains (4–16×) come only when the data layout and control flow are redesigned, not from auto-vectorizing scalar code.

**The core idea — replace per-element branches with mask-and-compress.** Load a lane of values, do the operation on all lanes at once (`cmpeq`, add, etc.), turn the result into a bitmask (`movemask`), then use `pext`/`pdep` or a shuffle table to compact the surviving lanes. No per-row branch → no mispredictions.

```
Traditional: process 1 element per cycle
SIMD:        process 4/8/16/32/64 elements per cycle

Example — Vectorized hash probe (DuckDB style):

Input keys:  [k0, k1, k2, k3, k4, k5, k6, k7]  (8 × 32-bit in AVX2)

Step 1: Hash all 8 keys in parallel
  hashes = _mm256_mullo_epi32(keys, golden_ratio_vec);

Step 2: Gather 8 hash table entries simultaneously
  entries = _mm256_i32gather_epi32(table, hashes, 4);

Step 3: Compare all 8 entries with keys
  matches = _mm256_cmpeq_epi32(entries, keys);

Step 4: Extract match mask
  mask = _mm256_movemask_epi8(matches);
  // Process hits with PDEP/PEXT bit manipulation
```

**Key SIMD primitives and their uses:**

| Instruction | Use |
|-------------|-----|
| `cmpeq` | Parallel comparison (hash probes, string search) |
| `movemask` | Convert SIMD comparison to scalar bitmask |
| `shuffle/permute` | Partition, filter, gather results |
| `gather/scatter` | Indirect load/store (hash table probing) |
| `popcnt` | Count matches, cardinality |
| `lzcnt/tzcnt` | Find first match, skip empty slots |
| `pext/pdep` | Compress/expand based on bitmask (BMI2) |

**SIMD-friendly filter (Vectorized selection scan):**
```
Predicate: column > 42

col_values: [10, 55, 3, 99, 42, 67, 8, 51]  (load 8 at once)
threshold:  [42, 42, 42, 42, 42, 42, 42, 42]
cmp_result: [ 0,  1, 0,  1,  0,  1, 0,  1]  → bitmask = 0b10101010
selected_indices = [1, 3, 5, 7]               → via PEXT or lookup table
```

**Structures redesigned for SIMD:**
- **SIMD-Scan** (Polychroniou et al., 2015): vectorized hash join, 4-8× over scalar
- **SIMDified Bloom filter**: check all k hash positions with one SIMD compare
- **Vectorized sorting networks**: bitonic/merge networks for small arrays
- **SIMD-friendly B-tree nodes**: pack keys for SIMD comparison instead of binary search
- **simdjson**: parses JSON at >2GB/s via structural character classification with SIMD

---

## 41. **Distributed Hash Tables (Chord / Kademlia)**

**Used in:** BitTorrent (Kademlia), IPFS, Ethereum devp2p, Cassandra (modified), Amazon Dynamo (inspired)

Decentralized key-value lookup across N nodes with O(log N) hops and no central directory.

**The problem it solves.** In a peer-to-peer network with millions of churning nodes there's no central directory to ask "who has key K?". A DHT gives every node a *partial* routing table (O(log N) entries) such that any node can locate the key's owner in O(log N) hops — no coordinator, self-healing under joins/leaves.

**The core idea — put node IDs and keys in one identifier space; route greedily toward the target ID.** Chord uses a ring with a **finger table** of exponentially-spaced shortcuts (2⁰, 2¹, 2²… ahead), so each hop at least halves the remaining distance → log N hops. Kademlia uses **XOR distance** and k-buckets; both share the "halve the distance each hop" structure.

```
Chord Ring (Stoica et al., 2001):

Node IDs and keys mapped to same m-bit identifier space (e.g., SHA-1)

      ┌──── N1 ◄── responsible for keys in (N56, N1]
      │
 N56 ─┤         N8
      │        ╱
 N51 ─┤    N14
      │    ╱
 N42 ─┼─ N21
      │    ╲
 N38 ─┤    N32
      │
      └── N35

Finger table at node N8 (shortcuts across the ring):
  finger[1] = successor(8 + 2⁰) = N14     ← 1 hop forward
  finger[2] = successor(8 + 2¹) = N14     ← 2 hops forward
  finger[3] = successor(8 + 2²) = N14     ← 4 hops forward
  finger[4] = successor(8 + 2³) = N21     ← 8 hops forward
  finger[5] = successor(8 + 2⁴) = N32     ← 16 hops forward
  finger[6] = successor(8 + 2⁵) = N42     ← 32 hops forward

Lookup(key=30): N8 → finger[5]=N32 too far → finger[4]=N21 → N21.successor=N32
  → 2 hops (O(log N))
```

**Kademlia (Maymounkov & Mazieres, 2002):**
- Distance = XOR of node IDs (symmetric, forms ultrametric space)
- k-buckets: each node keeps k contacts per distance range
- Iterative lookup: α parallel queries per step → faster convergence
- No stabilization protocol needed (XOR topology self-heals)
- Used by BitTorrent DHT: ~25M concurrent nodes

**Key properties:**
- O(log N) routing hops, O(log N) state per node
- Churn handling: Chord needs stabilization; Kademlia uses lazy eviction
- Security: Eclipse attacks mitigated via routing table constraints, diverse lookups

---

## 42. **Epoch-Based Reclamation / Hazard Pointers**

**Used in:** Crossbeam (Rust), ConcurrentHashMap (Java), Folly (C++), Linux kernel RCU

Safe memory reclamation for lock-free structures — the "garbage collection" problem for concurrent systems.

**The problem it solves.** In a lock-free structure, thread A can be mid-read of node X exactly when thread B unlinks and `free`s X → use-after-free. You can't just free unlinked nodes. Without a GC you need a protocol that answers "is any thread still looking at this node?" cheaply. EBR and hazard pointers are the two dominant answers, trading reclamation latency against per-access cost.

**The core idea — defer frees until provably no reader holds the pointer.**
- **EBR:** readers announce an *epoch* on entry; a retired node from epoch e is freed only once every active thread has advanced past e. Cheap on the read side (one store), but a single stalled thread stops all reclamation → unbounded garbage.
- **Hazard pointers:** a reader *publishes* the exact pointer it's using; a retired node is freed only if it appears in no thread's hazard list. Bounded garbage (O(threads×K)), but every read pays a store + memory fence.

```
Problem: Thread A reads node X while Thread B unlinks and frees X → use-after-free

Epoch-Based Reclamation (Fraser, 2004):

Global epoch: 2
Thread states:
  T1: active, epoch=2    ← entered critical section at epoch 2
  T2: active, epoch=2
  T3: inactive

Retire(node): add to garbage[current_epoch]
Advance epoch: only when ALL active threads have observed current epoch

  garbage[0]: [X, Y]   ← safe to free (epoch 2, all threads past epoch 0)
  garbage[1]: [Z]       ← safe to free
  garbage[2]: [W, V]    ← NOT safe yet (threads still in epoch 2)
```

**Hazard Pointers** (Michael, 2004):
```
Each thread publishes pointers it's currently accessing:

T1 hazard list: [ptr_A, ptr_C, null, null]
T2 hazard list: [ptr_B, null, null, null]

Retire(ptr_X):
  scan all hazard lists
  if ptr_X not in any list → free immediately
  else → defer to later scan

Bounded garbage: at most O(T × K) unfreed objects (T threads, K hazards/thread)
```

**Comparison:**

| Scheme | Amortized overhead | Max garbage | Read-side cost | Reclaim latency |
|--------|-------------------|-------------|----------------|-----------------|
| Epoch-based | O(1) | Unbounded* | Near-zero | Delayed (epoch) |
| Hazard pointers | O(T) per retire | O(T × K) | 1 store + fence | Scan-based |
| RCU | O(1) | O(T × grace) | Zero (x86) | Grace period |
| Reference counting | O(1) | Zero | Atomic inc/dec | Immediate |

*Quiescent-state-based reclamation (QSBR) bounds this by requiring periodic quiescent points.

**Hyaline** (Nikolaev et al., 2020): combines benefits of epoch and hazard pointers, bounded reclamation with low overhead.

---

## 43. **Vector Clocks / Hybrid Logical Clocks**

**Used in:** Dynamo/DynamoDB, Riak, CockroachDB (HLC), Spanner (TrueTime), causal consistency protocols

Logical timestamps for ordering events in distributed systems without synchronized physical clocks.

**The problem it solves.** Wall clocks drift and skew across machines, so a physical timestamp can't reliably tell you whether event A *caused* B or they happened concurrently. Vector clocks capture **causality** exactly: from two vector timestamps you can tell if one happened-before the other or they're concurrent (a conflict needing resolution — e.g. sibling versions in Dynamo).

**The core idea — one counter per node; take the elementwise max on receive.** A node bumps its own entry per event and ships its whole vector with each message. On receipt you `max` elementwise (absorbing everything the sender had seen) then bump your own. Comparing two vectors elementwise then reveals order: strictly-less on all axes = happened-before; incomparable = concurrent.

```
Vector Clocks (Fidge/Mattern, 1988):

Each node maintains a vector of counters, one per node:

  Event at Node A:  A increments VC[A]
  Send message:     attach current VC
  Receive message:  VC[i] = max(local[i], received[i]) for all i; increment VC[self]

Node A: [1,0,0] → [2,0,0] ──msg──→ Node B: max([0,1,0],[2,0,0]) = [2,1,0] → [2,2,0]
                                                                        │
Node C: [0,0,1] ──msg──→ Node B: max([2,2,0],[0,0,1]) = [2,3,1]      │
                                                                        │
Causality: VC_a < VC_b iff ∀i: VC_a[i] ≤ VC_b[i] and ∃j: VC_a[j] < VC_b[j]
Concurrent: neither VC_a < VC_b nor VC_b < VC_a  → conflict!
```
**Worked concurrency detection — two writes to the same key:**
```
Start: everyone [0,0,0].
Client writes via A → A stamps v1 = [1,0,0].
Meanwhile client writes via B (hasn't seen A's write) → B stamps v2 = [0,1,0].
Compare v1=[1,0,0] vs v2=[0,1,0]:  v1[0]>v2[0] BUT v1[1]<v2[1] → neither ≤ the other
→ CONCURRENT → Dynamo keeps BOTH as siblings, hands both to the app to reconcile.
Contrast: if B had first read A's write, B would stamp [1,1,0] > [1,0,0] → clean overwrite.
```

**Problem:** Vector clocks grow with number of nodes (O(N) per timestamp).

**Hybrid Logical Clocks** (Kulkarni et al., 2014):
```
HLC = (physical_time, logical_counter)

  physical component: max(local_wall_clock, received_physical)
  logical component:  tiebreaker when physical times collide

Size: constant (just 2 integers), regardless of cluster size
Guarantees:
  - If e → f (causally), then HLC(e) < HLC(f)
  - HLC is always within ε of physical time (bounded drift)
  - Compatible with NTP-synchronized clocks
```

**Used in CockroachDB:** HLC provides causal ordering for MVCC timestamps with ~150ms max clock skew tolerance.

**Spanner TrueTime:** GPS + atomic clocks → bounded uncertainty interval [earliest, latest]. Wait out uncertainty before committing → external consistency (linearizability).

**Interval Tree Clocks** (Almeida et al., 2008): fork/join model, supports dynamic number of participants without ID allocation.

---

## 44. **Tournament / Loser Trees**

**Used in:** External merge sort (every database's sort operator), K-way merge in LSM compaction, priority-queue replacement

Binary tree of comparisons for efficient K-way merging. Loser tree variant avoids redundant comparisons.

**The problem it solves.** Merging K sorted runs (external sort, LSM compaction) naively picks the min of K heads each step → O(K) comparisons per output element. A binary heap cuts that to O(log K) but does ~2·log K comparisons per sift. A **loser tree** does exactly **one** comparison per tree level per output — the tightest constant for K-way merge, which matters when K is 512–1024 runs.

**The core idea — store the LOSER at each internal node; only the winner's path needs replay.** A tournament of K leaves (one per run's current head) plays off; each internal node records the *loser* of its match, and the overall winner sits above the root. After you output the winner and pull the next value from its run, only the *single path* from that leaf to the root can change — replay just those log K matches against the stored losers.

```
4-way merge of sorted runs: [2,5,8], [1,4,9], [3,6,7], [0,10,11]

Loser tree (stores loser at each internal node, winner propagates up):

            ┌───────┐
            │ W: 0  │  ← overall winner (output next)
            │ L: 1  │  ← loser stored here
            └───┬───┘
          ┌─────┴─────┐
       ┌──┴──┐     ┌──┴──┐
       │ L:2 │     │ L:3 │
       └──┬──┘     └──┬──┘
        ┌─┴─┐       ┌─┴─┐
       [0] [2]     [1] [3]   ← leaf: current head of each run
        0   2       1   3    ← values

Output 0, advance run[0] to next value (10):
  Only replay path from leaf[0] to root: 3 comparisons for 4-way merge
  (vs. 3 comparisons per element for naive tournament)
```
**Worked replay after outputting the winner 0:**
```
Winner was 0 (from run[3], value 0). Output it. run[3] advances → new leaf value 10.
Replay ONLY leaf[3]'s path to the root, playing the new value against each stored LOSER:
  level 1 node (was L:3): compare 10 vs its sibling leaf value 1 → 10 loses → store L=10 here,
                          winner 1 rises.
  root (was L:1): compare rising 1 vs stored loser... → 1 is the new overall winner.
New winner = 1. Only log₂4 = 2 comparisons touched; the other subtree never re-examined.
Loop: output 1, advance its run, replay its path, etc. → one comparison per level per item.
```

**Key properties:**
- K-way merge: O(log K) comparisons per output element
- Loser tree: winner just needs to replay against stored losers on its path
- Replacement selection: output element, replace with next from same run, replay
- Used in external sort: merge ~256-1024 runs simultaneously
- SIMD variant: compare K elements per node where K = SIMD width

**Cache-optimized multi-way merge:**
- Each run buffered in memory → sequential reads
- Loser tree fits in L1 cache for K ≤ ~256
- Doubles I/O bandwidth utilization vs. 2-way merge

---

## 45. **Compact Trie / Double-Array Trie**

**Used in:** ICU (Unicode), MeCab (Japanese NLP), IP routing (DPDK), Linux kernel `fib_trie`

Extremely memory-efficient trie encoding using two parallel arrays. O(1) per trie transition.

**The problem it solves.** A standard trie node holds up to |Σ| child pointers (|Σ| = alphabet size). For ASCII that is 256 pointers × 8 bytes = 2 KB *per node*, almost all NULL for real dictionaries. The double-array trie (Aoe, *An Efficient Digital Search Algorithm by Using a Double-Array Structure*, IEEE TSE 1989) collapses every node to **two integers** while keeping O(1) transitions.

**The core idea.** Instead of storing pointers, store two arrays indexed by *state number* (a state = a trie node = an integer id, root = state 1):

```
  BASE[s] + code(c) = t     ← where the transition from state s on char c lands
  CHECK[t]          = s      ← who t's parent is (guards against collisions)
```

A transition `s --c--> t` is *valid* iff `CHECK[BASE[s] + code(c)] == s`. That single equality check is the whole trie walk. BASE[s] is a per-state offset chosen at build time so that all of s's children land in currently-free slots.

**Worked example — build a trie for `{"ab", "ac", "b"}`.**
Let code(a)=1, code(b)=2, code(c)=3. States: 1=root, 2="a", 3="b", 4="ab", 5="ac".

```
Step 1: root=state 1. Its children are 'a' and 'b'.
        Pick BASE[1]=1 so:  child 'a' → slot 1+1=2,  child 'b' → slot 1+2=3.
        Assign state 2 to slot 2, state 3 to slot 3.  Set CHECK[2]=1, CHECK[3]=1.

Step 2: state 2 ("a") has children 'b','c'.
        Pick BASE[2]=2 so:  'b' → 2+2=4,  'c' → 2+3=5.  (slots 4,5 free ✓)
        CHECK[4]=2, CHECK[5]=2.

Step 3: state 3 ("b") is a leaf-word. state 4 ("ab"), 5 ("ac") leaf-words.
        Mark word-ends (separate bitmap, or a BASE<0 convention).

Resulting arrays (index = slot/state number):
Index:  [ 0   1   2   3   4   5 ]
BASE:   [ _   1   2   _   _   _ ]     state 1→base1, state 2→base2
CHECK:  [ _   0   1   1   2   2 ]     0 = root has no parent
                └─┬─┘ └──┬──┘
              children   children
              of root    of state 2
```

**Worked lookup — search `"ab"`:**
```
  start s=1 (root)
  'a': t = BASE[1] + code(a) = 1 + 1 = 2;  CHECK[2]==1==s ✓  → s=2
  'b': t = BASE[2] + code(b) = 2 + 2 = 4;  CHECK[4]==2==s ✓  → s=4
  end of key, state 4 marked word-end  → "ab" FOUND
```
**Worked lookup — search `"ad"` (code(d)=4):**
```
  'a': → s=2  (as above)
  'd': t = BASE[2] + 4 = 6;  slot 6 empty / CHECK[6]≠2  → NOT FOUND
```
The CHECK test is what makes it safe: two unrelated states may compute the *same* slot index, but only the one whose parent matches passes.

**Collisions & the build cost.** When adding children of state s, if any target slot `BASE[s]+code(c)` is already occupied by an unrelated state, you must pick a different BASE[s] — which means *relocating* s's existing children to a new contiguous block (and fixing their CHECK entries). This relocation is why naive construction is O(n²)-ish; production builders (and the dynamic CEDAR variant) keep a free-slot linked list to find a fitting BASE quickly.

**Key properties:**
- Two integers per state (vs. |Σ| pointers in naive trie) — ~10× smaller
- O(1) per character lookup (one add + one array read + one compare, no pointer chasing)
- Cache-friendly: BASE/CHECK are flat arrays, sequential-ish access
- Static build packs slots densely; lookup is read-only and branch-light
- Dynamic variant (CEDAR, Yoshinaga & Kitsuregawa, 2014): supports insert/delete via free-list slot management
- HAT-trie (Askitis & Sinha, 2007): hybrid hash + trie, faster than std::unordered_map for strings

---

## 46. **Sketch Structures (Theta Sketch / KLL Sketch)**

**Used in:** Apache Datasketches, Druid, Snowflake, BigQuery, Presto

Sub-linear space summaries enabling set operations and quantile estimation at scale.

**The problem it solves.** Exact distinct-count *with set operations* (unions/intersections of huge cohorts) needs the actual sets — infeasible across billions of rows and many segments. Theta sketches keep a bounded sample of hashes that supports **union/intersection/difference** with error bounds and full mergeability; KLL does the same for quantiles. Both trade a little accuracy for fixed KB-scale memory that composes across shards.

**The core idea (Theta) — keep only hashes below a threshold θ; that fraction estimates the whole.** Hash values are uniform in [0,1). If you retain all hashes < θ, you've kept ≈θ fraction of *distinct* items, so |set| ≈ (# kept)/θ. Because the retained set is defined purely by the hash and θ, two sketches **union** by keeping the min-θ and merging, and **intersect** by keeping hashes present in both — set algebra on samples.

```
Theta Sketch (set operations):

Concept: keep all hash values below threshold θ

Stream A:  h(a1)=0.1, h(a2)=0.7, h(a3)=0.3, h(a4)=0.9  (θ=0.5 → keep {0.1, 0.3})
Stream B:  h(b1)=0.2, h(b2)=0.4, h(b3)=0.8              (θ=0.5 → keep {0.2, 0.4})

Union:     {0.1, 0.2, 0.3, 0.4}, θ=0.5 → |estimate| = 4/0.5 = 8
Intersect: {}, θ=0.5 → |estimate| = 0
Difference: sketch(A) - sketch(B) via inclusion-exclusion
```

**KLL core idea — a hierarchy of compactors that randomly drop half.** Each level holds ≤k items; when full it *compacts*: sort, then keep either the even- or odd-indexed items (coin flip) and promote them to the next level with doubled weight. Randomizing which half survives makes errors cancel rather than accumulate, giving near-optimal error for the memory. Worked:
```
Level 0 compactor fills to k items → sort → flip coin → keep every other item,
  each now "weighs 2" → push to level 1. Level 1 fills → same, weight 4 → level 2...
Query(q): sweep all levels, summing weights of items ≤ target until reaching rank q·N.
Higher levels = coarser but rarer items → total space O((1/ε)·log log(1/δ)).
```

**KLL Sketch** (Karnin, Lang, Liberty, 2016) — mergeable quantile sketch:
- Space: O(1/ε × log log(1/δ)) — nearly optimal
- Supports merge across distributed nodes with no accuracy loss
- Replaced GK-sketch (2001) as the standard; used in Apache Datasketches
- Relative-error variant (ReqSketch, 2021): constant relative error at all quantiles

**Frequent Directions** (Liberty, 2013) — streaming matrix sketch:
- Maintains low-rank approximation of a matrix in O(d × ℓ) space
- Covariance matrix estimation for streaming PCA
- Generalization of Misra-Gries heavy hitters to matrix setting

---

## 47. **Segment Tree (with Lazy Propagation)**

**Used in:** Database range aggregates, computational geometry, HFT interval queries, game physics (AABB)

Supports arbitrary range queries and range updates in O(log N) per operation.

**The problem it solves.** "Sum/min/max over range [l,r]" plus "update this element" — a prefix-sum array does queries in O(1) but updates in O(N); a Fenwick tree (§25) handles sum but not min/max or *range* updates. A segment tree handles **any associative aggregate** with both range query and (with lazy propagation) *range* update in O(log N).

**The core idea — each node stores the aggregate of its subrange; any query range decomposes into O(log N) node-ranges.** The tree recursively halves [0,N). A query [l,r] is answered by combining the few "canonical" nodes whose ranges tile [l,r] exactly — at most 2 per level → O(log N) nodes.

```
Array: [1, 3, 5, 7, 2, 4, 6, 8]

Segment tree (sum):
                    [36]               range [0,7]
                   /    \
              [16]        [20]         [0,3] [4,7]
             /    \      /    \
          [4]    [12]  [6]    [14]     [0,1] [2,3] [4,5] [6,7]
         / \    / \   / \    / \
        1   3  5   7 2   4  6   8     individual elements

Query sum([2,5]): decompose into [2,3] + [4,5] = 12 + 6 = 18  (2 nodes)
```

**Lazy propagation** — batch range updates:
```
"Add 10 to all elements in [0, 5]"

Instead of updating 6 leaves: mark internal nodes with pending updates
Node [0,3]: lazy_add = 10  ← propagate only when children queried
Node [4,5]: lazy_add = 10

Query/update pushes lazy values down on demand → O(log N) amortized
```
**Worked lazy update then query — "add 10 to [0,5]", then "sum [2,5]":**
```
UPDATE add-10 to [0,5]: descend; where a node's range is FULLY inside [0,5], stamp its
  lazy_add += 10 and bump its stored sum by 10·(range size), then STOP — don't recurse.
  Node [0,3] (size 4): sum += 40, lazy_add=10.  Node [4,5] (size 2): sum += 20, lazy=10.
QUERY sum[2,5]: descend into [0,3] → must go below it → first PUSH DOWN its lazy 10 to
  children [0,1],[2,3] (add 10·size to each, set their lazy), clear parent lazy. Then read
  [2,3]=12+20=... and [4,5] node → combine. Lazy is only materialized on the paths queries touch → O(log N) amortized.
```

**Key properties:**
- Any associative operation: sum, min, max, gcd, matrix multiply
- Persistent segment tree: O(log N) per version, shares structure
- 2D segment tree: O(log² N) per query for rectangle operations
- Merge sort tree: segment tree of sorted arrays → O(log² N) count-in-range

**Fractional Cascading** (Chazelle & Guibas, 1986): accelerates successive binary searches across sorted lists from O(k log N) to O(k + log N). Used with layered range trees.

---

## 48. **Treap (Tree + Heap)**

**Used in:** Randomized BSTs, implicit treaps for arrays, persistent balanced trees, competitive programming

Balanced BST using random priorities. Simpler than red-black trees, elegant for split/merge operations.

**The problem it solves.** Red-black/AVL trees balance via intricate rotation cases that are easy to get wrong. A treap gets *expected* balance almost for free: give each key a random priority and keep the tree a heap on priorities. Random priorities → the tree is shaped like a random BST → expected O(log N) height, with dead-simple code and O(log N) `split`/`merge` that make range operations trivial.

**The core idea — a BST on keys that is simultaneously a heap on random priorities.** The key ordering makes it a search tree; the heap-on-priority constraint pins the shape. Since priorities are random and independent of insertion order, the resulting tree is distributionally identical to inserting keys in random order → balanced w.h.p. All operations reduce to `split` (cut into keys≤x and keys>x) and `merge`.

```
         (K=5, P=93)          Keys form BST (left < root < right)
          /        \          Priorities form max-heap (parent > children)
   (K=2, P=82)  (K=8, P=77)
    /     \        /     \
(K=1,P=5) (K=3,P=43) (K=7,P=61) (K=9,P=19)

Split(tree, key=6):
  → Left tree: all keys ≤ 6    Right tree: all keys > 6
  O(log N) expected, directly follows search path

Merge(left, right):
  → Combine by priority: higher priority becomes root
  O(log N) expected
```
**Worked insert — insert (K=6, random P=88) via split+merge:**
```
1. split(tree, key=6) → L = all keys <6, R = all keys >6 (follows the search path, O(log N)).
2. new node n=(6,88).  merge(L, n) then merge(result, R): at each merge, whichever root
   has HIGHER priority stays on top, recurse into the appropriate child.
   Because P=88 outranks some subtrees, n bubbles to the right depth automatically —
   no explicit rotation cases, the heap property does the balancing.
```
Equivalently, rotation-based insert: BST-insert by key, then rotate the new node up while its priority exceeds its parent's — the two formulations are identical.

**Implicit Treap** — array with O(log N) insert/delete at any position:
- No explicit keys; position determined by subtree sizes
- Split at position k: left gets first k elements, right gets rest
- Supports reverse, cyclic shift, range assignment — all O(log N)
- Alternative to rope for sequence operations

**Key properties:**
- Expected O(log N) height with random priorities
- Split + Merge = all balanced BST operations
- Persistent variant: path copy during split/merge → O(log N) per version
- Much simpler to implement than red-black or AVL trees

---

## 49. **Clock / CLOCK-Pro / ARC (Adaptive Replacement Cache)**

**Used in:** Operating system page caches, PostgreSQL buffer manager, ZFS ARC, database buffer pools

Eviction policies that adapt to workload patterns, beating LRU on mixed access patterns.

**The problem it solves.** Strict LRU has two flaws: (1) it needs a lock + list-splice on *every* access (contention), and (2) it's fooled by scans — reading a big table once evicts your whole hot working set. CLOCK approximates LRU with a cheap reference bit (no per-access list surgery); ARC goes further, *self-tuning* the split between recency and frequency so scans can't blow away frequently-used pages.

**The core idea.** CLOCK: pages sit in a ring, each with a reference bit set on access; an evicting "hand" sweeps, giving any referenced page a second chance (clear bit, skip) and evicting the first unreferenced one — amortized LRU without touching a page on hit. ARC: keep *two* lists — T1 (seen once, recency) and T2 (seen ≥twice, frequency) — plus **ghost lists** B1/B2 remembering recently-evicted keys. A hit in a ghost list is a signal that you evicted too aggressively from that side, so ARC shifts the target boundary p toward it.

```
LRU problem: a single full table scan evicts the entire working set

CLOCK (approximation of LRU):
  Circular buffer of pages, each with a "referenced" bit
  ┌───┬───┬───┬───┬───┬───┬───┬───┐
  │ 1 │ 0 │ 1 │ 1 │ 0 │ 0 │ 1 │ 0 │  ← reference bits
  └───┴───┴───┴───┴───┴───┴───┴───┘
                      ↑ hand
  Evict: advance hand; if ref=1, clear to 0 and skip; if ref=0, evict.

ARC (Megiddo & Modha, 2003):
  ┌─────────┬─────────┬──────────┬──────────┐
  │ Ghost B1│ Cache T1│ Cache T2 │ Ghost B2 │
  │(recently│(recency)│(frequency│(recently │
  │ evicted │         │ ≥2 hits) │ evicted  │
  │ from T1)│         │          │ from T2) │
  └─────────┴─────────┴──────────┴──────────┘
            ↑─── p ───↑ (adaptive partition point)

  Hit in B1 ghost → increase p (favor recency, grow T1)
  Hit in B2 ghost → decrease p (favor frequency, grow T2)
  Adapts online to workload: scan-resistant AND frequency-aware
```
**Worked CLOCK eviction sweep** (need a victim; hand at slot with ref bits `[1,0,1,...]`):
```
hand@slot0: ref=1 → clear to 0, advance.  hand@slot1: ref=0 → EVICT slot1, place new page.
A page touched since the hand last passed survives one more revolution → "second chance".
```
**Worked ARC adaptation** (why scans don't hurt):
```
A scan streams unique pages → they land in T1, get evicted to ghost B1, never re-referenced.
Your hot pages live in T2 (≥2 hits) and are NOT displaced by T1 growth beyond p.
If a real recency-heavy phase starts, hits in B1 push p up → T1 grows → adapts. No tuning.
```

**Key properties:**
- ARC: self-tuning, no parameters, scan-resistant, O(1) per operation
- Outperforms LRU by 2-20% hit rate on real workloads
- Patent-encumbered (IBM) → many systems use CLOCK-Pro or LIRS instead

**CLOCK-Pro** (Jiang et al., 2005): approximation of LIRS, three "hands":
- Separates cold and hot pages
- Scan-resistant without ghost lists (lower memory than ARC)
- Used in Linux kernel as an alternative page replacement candidate

**2Q** (Johnson & Shasha, 1994): two queues (FIFO for new, LRU for promoted) — simpler than ARC, used in PostgreSQL buffer manager.

---

## 50. **Conflict-Free Replicated Registers / Maps (δ-CRDTs)**

**Used in:** Redis Enterprise (Active-Active), Automerge 2.0, Electric SQL, Loro

Optimized CRDTs that transmit only deltas rather than full state, reducing network cost by orders of magnitude.

**The problem it solves.** Classic state-based CRDTs (§19) converge by shipping the *entire* state on every sync — fine for a counter, ruinous for a 1 GB document edited one character at a time. δ-CRDTs ship only the small **delta** produced by each operation while preserving the exact same convergence guarantees, cutting bandwidth from O(state) to O(change).

**The core idea — a delta is itself a join-irreducible fragment of the lattice.** Each operation yields a δ that is a tiny CRDT state; merging δ into the full state via the same lattice `join` (⊔) has identical effect to the full-state merge. Because join is idempotent/commutative/associative (§19), deltas can be batched, reordered, or redelivered safely — anti-entropy periodically re-ships accumulated deltas to catch any dropped in real time.

```
Classic state-based CRDT: ship entire state on every sync
  Node A sends: {counter: [A:50, B:30, C:10]}  ← 90 bytes for 3 nodes

δ-CRDT: ship only the delta since last sync
  Node A sends: {counter: [A:+3]}               ← 8 bytes

δ-group: collect deltas over interval, merge, ship batch:
  Δ = δ₁ ⊔ δ₂ ⊔ δ₃  (merge deltas, then send)
  Receiver: state' = state ⊔ Δ
```

**δ-CRDT Maps** (Almeida et al., 2018):
```
Nested map with per-field CRDTs:
{
  "user": LWW-Register("alice"),          ← last-writer-wins
  "items": OR-Set({item1, item2, item3}), ← add-wins set
  "count": PN-Counter(7),                 ← increment/decrement
  "doc":   RGA("hello world")             ← replicated list/text
}

Delta for adding item4:
  Δ = {"items": {add: item4, causal_context: [(A,15)]}}
  Size: O(1) regardless of set size
```
**Worked delta ship-and-merge:**
```
Node A adds item4 → produces δ = {items: add item4 tagged (A,15)}  (a few bytes).
A ships δ (not its whole OR-Set) to B and C.
B: state_B = state_B ⊔ δ → item4 appears, tagged with dot (A,15).
C receives δ TWICE (retry) → second ⊔ is idempotent → item4 not duplicated.
If C concurrently REMOVED item4, OR-Set add-wins + causal context decides: a remove only
  cancels adds it has SEEN; the unseen (A,15) add survives → item4 stays.
Dotted version vectors track which dots each replica absorbed → GC once all have them.
```

**Key properties:**
- Network cost: O(delta) instead of O(state) per sync
- Causal consistency via dotted version vectors (compact causal metadata)
- Anti-entropy: periodic delta shipping catches missed real-time updates
- Garbage collection: prune causal metadata when all replicas have converged

**Automerge 2.0 (Columnar CRDT):**
- Stores CRDT metadata in columnar format (compressed operation log)
- 10-100× less memory than row-based CRDT storage
- Supports text, JSON-like documents, tables

---

## 51. **Bw-Tree (Buzzword Tree)**

**Used in:** Microsoft SQL Server Hekaton, Azure Cosmos DB, Silo

Lock-free B+-tree using delta chains and logical-to-physical page mapping. Designed for modern multi-core + flash.

**The problem it solves.** Latching a B+-tree page serializes writers and causes cache-line contention on many cores; rewriting a whole page per update is bad for flash (write amplification). The Bw-tree removes latches entirely: updates are tiny **delta records** prepended to a page and published with a single CAS on a **mapping table** slot, so writers never block and pages aren't rewritten in place.

**The core idea — indirection through a mapping table + delta chains.** Nodes reference each other by logical page id (PID), not physical pointer; the mapping table translates PID→physical address. An update builds a delta, links it in front of the current page, and CAS-swaps the mapping-table entry to point at the delta. Readers walk the delta chain then the base page. When a chain grows long, a background consolidation merges deltas into a fresh page and CASes it in.

```
Mapping Table (logical → physical):
┌─────┬────────────┐
│ PID │ Phys Addr  │
├─────┼────────────┤
│  0  │ → Node A   │
│  1  │ → Δ₃→Δ₂→Δ₁→Node B │  ← delta chain
│  2  │ → Node C   │
└─────┴────────────┘

Update page 1:
  1. Create delta record Δ₄ (insert/delete)
  2. Δ₄.next = current mapping[1]
  3. CAS mapping[1] from old to Δ₄
  → Lock-free! Failed CAS = contention, just retry

Consolidation: when chain too long, merge deltas into new base page, CAS mapping
```

**Key properties:**
- No latching on pages: all updates via CAS on mapping table
- Delta chains amortize write amplification (no full page rewrite per update)
- Elastic page sizes: pages grow/shrink via split/merge deltas
- Flash-friendly: log-structured writes, sequential I/O
- Epoch-based GC for old pages and delta records

**OpenBw-Tree** (Wang et al., 2018): open-source reimplementation:
- Found Bw-tree often slower than optimistic-lock-coupled B+-tree in practice
- Delta chain traversal costs more cache misses than anticipated
- Mapping table indirection adds ~1 cache miss per access

**Lesson:** Lock-free ≠ faster. Optimistic lock coupling on B+-trees often wins due to simpler cache access patterns.

---

## 52. **Splay Tree**

**Used in:** Link-Cut trees (network flow), compression (move-to-front), memory allocators (dlmalloc), cache-like structures

Self-adjusting BST that moves accessed nodes to root via rotations. Amortized O(log N) with working-set optimality.

**The problem it solves.** Balanced trees (RB/AVL) store extra metadata and treat every key equally, even though real access is skewed (some keys are hot). A splay tree stores *no* balance metadata and instead moves every accessed node to the root, so recently/frequently used keys drift near the top — giving the **working-set property**: repeatedly touching a small hot set costs O(log(hot-set-size)), not O(log N), automatically.

**The core idea — "splay" the accessed node to the root with paired rotations.** After finding X you rotate it up, but *in pairs* chosen by X's shape relative to its grandparent: **zig-zig** (X and parent same side → rotate grandparent first, then parent), **zig-zag** (opposite sides → two opposite rotations), **zig** (X is child of root → single rotation). The zig-zig pairing is what halves the depth of everything on the access path — the amortized magic that a naive rotate-to-root lacks.

```
Access node X → splay X to root via zig-zig, zig-zag, zig rotations:

Before accessing 1:          After splaying 1 to root:
        5                           1
       / \                           \
      3   7                           3
     /                               / \
    2                               2   5
   /                                     \
  1  ← access this                        7

Working Set Property:
  If you access the same k elements repeatedly,
  amortized cost = O(log k), NOT O(log N)
  → adapts to temporal locality automatically
```

**Key properties:**
- No balance metadata stored (no colors, no heights)
- O(log N) amortized for all operations (Sleator & Tarjan, 1985)
- Static optimality conjecture: within O(1) factor of optimal static BST (unproven)
- Dynamic optimality conjecture: within O(1) of ANY BST algorithm (open for 40 years)
- Bad worst-case: single operation can be O(N), but amortized sequence is O(N log N)

**Link-Cut Trees** (Sleator & Tarjan, 1983):
- Splay trees as auxiliary trees representing paths in a forest
- O(log N) amortized: link, cut, find-root, path-aggregate
- Used in: max-flow algorithms, dynamic connectivity, network optimization

---

## 53. **Extendible Hashing / Linear Hashing**

**Used in:** Berkeley DB, early MySQL ISAM, disk-based hash indexes, embedded databases

Disk-friendly hash tables that grow gracefully without full rehashing.

**The problem it solves.** An in-memory hash table resizes by rehashing *everything* into a bigger array — O(N) stall. On disk that means re-reading and rewriting every page: unacceptable for a live database index. Extendible and linear hashing grow **one bucket at a time**, so a growth step touches a single page, giving smooth latency and no global rebuild.

**The core idea — use a growing PREFIX of the hash so splits are local.** Extendible hashing keeps a directory indexed by the top `d` bits (global depth); each bucket has a local depth ≤ d. Overflow splits just that bucket using one more hash bit and, only if its local depth was already d, doubles the *directory* (pointers, cheap) — never the data. Linear hashing drops the directory entirely: a split pointer marches through buckets, splitting them in round-robin order regardless of which overflowed, using `mod N` vs `mod 2N` to route.

```
Extendible Hashing:

Global directory (depth=2): indexes into buckets by first d bits of hash

  hash prefix → bucket
  00 → Bucket A [items with hash 00...]
  01 → Bucket B [items with hash 01...]
  10 → Bucket C [items with hash 10...]
  11 → Bucket C [items with hash 10... and 11...] (shared, local depth=1)

Bucket overflow → split ONE bucket, double directory if needed:
  Bucket C splits: 10→C', 11→C''
  Only C's entries rehashed — everything else untouched
```
**Worked bucket split (extendible).** Global depth d=2; bucket C (local depth 1) covers prefixes 10,11 and overflows:
```
1. C's local depth (1) < global depth (2)?  YES → no directory doubling needed.
   Split C into C'(prefix 10) and C''(prefix 11) using the 2nd hash bit; repoint dir[10]→C', dir[11]→C''.
   Only C's keys rehashed; A,B untouched; directory size unchanged.
2. If instead C's local depth == global depth → double the DIRECTORY first (copy pointers,
   d→d+1), THEN split. Doubling touches only cheap pointer entries, not data pages.
Lookup is always: top-d bits index the directory → one bucket page read. O(1) I/O.
```

**Linear Hashing** (Litwin, 1980):
```
Split pointer advances linearly through buckets:

Buckets:  [B0] [B1] [B2] [B3] [B4]
                      ↑ split pointer

Lookup(key):
  h = hash(key) % N       (current table size)
  if h < split_ptr:
    h = hash(key) % 2N    (use extended hash range)

Split: take bucket at split_ptr, redistribute using 2N hash
  → amortized O(1) cost, no directory, smooth growth
```

**Key properties:**
- No full rehash ever: at most one bucket reorganized per split
- Extendible: O(1) lookup (directory + 1 page I/O), directory may fit in memory
- Linear: no directory at all, slightly less uniform but simpler
- Both support ~75-90% fill factor

---

## 54. **Packed Memory Array (PMA) / Gapped Array**

**Used in:** ALEX (learned index leaves), cache-oblivious B-trees, density-based indexes

Sorted array with gaps that allows O(log² N) amortized inserts while maintaining cache-friendly sequential layout.

**The problem it solves.** A sorted array gives perfect scan locality (SIMD, prefetch) but O(N) inserts (shift everything). A tree gives O(log N) inserts but scatters data (pointer-chasing scans). A PMA keeps data in a *single sorted array with deliberate gaps*, so scans stay sequential while inserts only shift within a small window — O(log² N) amortized. This is why ALEX uses PMAs at its learned-index leaves: models predict a position, gaps absorb the insert without moving the model.

**The core idea — maintain per-level density bounds; rebalance the smallest window that's in range.** Conceptually the array is a virtual binary tree of segments. Each tree level h has a density corridor [ρ_h, τ_h] (looser near leaves, tighter near root). Insert into the local segment; if that segment's density leaves its corridor, walk *up* to the smallest enclosing window whose density is back in-bounds, and evenly redistribute (re-space) that whole window. Rare large redistributions amortize against many cheap local ones.

```
Logical sorted order: [1, 3, 5, 7, 9, 11, 13, 15]

PMA with gaps (density ~50-75%):
[ 1 | 3 | _ | 5 | 7 | _ | 9 | _ | 11 | 13 | _ | 15 | _ | _ | _ | _ ]

Insert 6:
  Find position between 5 and 7
  Local rebalance: shift within window to maintain density bounds

  [ 1 | 3 | _ | 5 | 6 | 7 | 9 | _ | 11 | 13 | _ | 15 | _ | _ | _ | _ ]
                     ↑ inserted, neighbors shifted

If local density exceeds threshold → rebalance larger window (amortized)
Density thresholds at each level: τ_h (upper) and ρ_h (lower)
```
**Worked insert with cascading rebalance — insert 6 into a nearly-full segment:**
```
1. Segment [.,5,7,.] is the leaf window. Insert 6 → [5,6,7] → density e.g. 3/3 = 1.0 > τ_leaf.
2. Out of corridor → climb to the parent window (double the span), recompute density.
   Parent [.,5,6,7,9,.,11,.] density = 6/8 = 0.75. In corridor? if ≤ τ at that level → STOP here.
3. Evenly re-space that parent window's elements across its slots (restore uniform gaps):
   [5 . 6 . 7 . 9 . 11 . ...]. Only that window moved — the rest of the array untouched.
4. If even the whole array exceeds τ_root → grow (resize ×2) and re-space globally (rare).
Amortized O(log² N): O(log N) levels × O(log N) redistribution charge per element.
```

**Key properties:**
- Sequential scan: just iterate the flat array, skip gaps (SIMD-friendly)
- Insert: O(log² N) amortized (rebalance cascades at most O(log N) levels)
- Cache-oblivious: no tuning needed for cache line size
- Predecessor/successor: O(1) — just scan forward/backward
- Space overhead: ~2× for gaps (configurable via density bounds)

**Used in ALEX:** Gapped arrays at learned index leaf nodes allow inserts without restructuring the model. Gaps absorb local inserts; when exhausted, split the node.

---

## Comparison Matrix

| # | Structure | Lookup | Insert | Space | Use Case |
|---|-----------|--------|--------|-------|----------|
| 1 | Ring Buffer | O(1) | O(1) | O(N) | IPC, streaming |
| 2 | Bε-tree | O(logₐ N) | O(logₐ N / B) | O(N) | Write-heavy DBs |
| 3 | Skip List | O(log N) | O(log N) | O(N) | Order books |
| 4 | HAMT | O(log₃₂ N) | O(log₃₂ N) | O(N) | Immutable maps |
| 5 | LSM Tree | O(log N × L) | O(1) amort | O(N) | Time-series DBs |
| 6 | Judy Array | O(key_len) | O(key_len) | < O(N) | Sparse integers |
| 7 | Cuckoo Hash | O(1) worst | O(1) amort | O(N) | Packet lookup |
| 8 | vEB Tree | O(log log U) | O(log log U) | O(U) | Priority queues |
| 9 | Rope | O(log N) | O(log N) | O(N) | Text editors |
| 10 | ART | O(k) | O(k) | Adaptive | In-memory indexes |
| 11 | B+ Tree | O(log N) | O(log N) | O(N) | Database indexes |
| 12 | Red-Black Tree | O(log N) | O(log N) | O(N) | Ordered maps |
| 13 | Memory Pool | O(1) | O(1) | O(N) | Fixed-size alloc |
| 14 | Bloom/Xor/Ribbon | O(k) | O(k) / build | bits/key | Set membership |
| 15 | Count-Min Sketch | O(d) | O(d) | O(1/ε log 1/δ) | Frequency estimation |
| 16 | HyperLogLog | — | O(1) | O(log log N) | Cardinality estimation |
| 17 | HNSW | O(log N) | O(log N) | O(N × M) | Vector ANN search |
| 18 | Learned Index | O(1)+local | O(log N) | O(N/ε) | ML-based indexing |
| 19 | CRDTs | O(1)-O(N) | O(1) | O(N × replicas) | Distributed consensus-free |
| 20 | Succinct / Wavelet | O(log Σ) | Static | n + o(n) bits | Compressed full-text |
| 21 | Masstree | O(k) | O(k) | O(N) | Concurrent KV stores |
| 22 | CTrie | O(k) | O(k) | O(N) | Lock-free concurrent maps |
| 23 | Pairing Heap | O(1) | O(1) | O(N) | Priority queues |
| 24 | R*-Tree | O(N^(1-1/d)) | O(N^(1-1/d)) | O(N) | Spatial indexing |
| 25 | Fenwick Tree | O(log N) | O(log N) | O(N) | Prefix sums |
| 26 | Persistent BST | O(log N) | O(log N) | O(log N)/op | Versioned data |
| 27 | Quotient Filter | O(1) | O(1) | ~1.2× Bloom | Mergeable set membership |
| 28 | COW B-Tree | O(log N) | O(log N) | O(N) | Snapshot-friendly DBs |
| 29 | XOR-Linked List | O(1) step | O(1) | O(N) halved ptrs | Embedded systems |
| 30 | Dancing Links | — | O(1) cover | O(N×M) | Exact cover/backtracking |
| 31 | Roaring Bitmap | O(1) | O(1) | Adaptive | Compressed set operations |
| 32 | Swiss Table | O(1) avg | O(1) avg | O(N) | General-purpose hash map |
| 33 | Merkle Tree | O(log N) proof | O(log N) | O(N) | Integrity verification |
| 34 | Consistent Hash | O(log N) | O(log N) | O(N log N) vnodes | Distributed key routing |
| 35 | T-Digest/DDSketch | O(1) quantile | O(1) | O(1/ε) | Streaming quantiles |
| 36 | Eytzinger Layout | O(log N) | Static | O(N) | Cache-optimal search |
| 37 | Flat Combining | O(1) amort | O(1) amort | O(N + T) | High-contention concurrency |
| 38 | MPHF (RecSplit) | O(1) | Static build | ~1.56 bits/key | Static key→index |
| 39 | CSR (graphs) | O(degree) | Static | O(V+E) | Sparse graph storage |
| 40 | SIMD structures | O(1)-O(log N) | varies | O(N) | Vectorized computation |
| 41 | DHT (Chord) | O(log N) hops | O(log N) | O(log N)/node | Decentralized KV |
| 42 | Epoch reclamation | — | — | O(T × retired) | Lock-free memory safety |
| 43 | Vector Clock/HLC | O(1) compare | O(1) | O(N) or O(1) | Distributed ordering |
| 44 | Loser Tree | O(log K) | O(log K) replay | O(K) | K-way merge |
| 45 | Double-Array Trie | O(k) | O(k) amort | O(states × 2) | String dictionary |
| 46 | Theta/KLL Sketch | O(1) | O(1) | O(1/ε) | Distributed set/quantile |
| 47 | Segment Tree | O(log N) | O(log N) | O(N) | Range query/update |
| 48 | Treap | O(log N)* | O(log N)* | O(N) | Split/merge BST |
| 49 | ARC/CLOCK-Pro | O(1) | O(1) | O(C) | Adaptive cache eviction |
| 50 | δ-CRDTs | O(1) | O(1) | O(N × replicas) | Efficient distributed sync |
| 51 | Bw-Tree | O(log N) | O(log N) | O(N + deltas) | Lock-free B-tree |
| 52 | Splay Tree | O(log N)* | O(log N)* | O(N) | Working-set adaptive |
| 53 | Extendible Hash | O(1) | O(1) amort | O(N) | Disk-friendly hashing |
| 54 | Packed Memory Array | O(log N) | O(log² N)* | O(N) gaps | Sorted array + inserts |

---

## Further Reading

**Classic references:**
- **"Modern B-Tree Techniques"** - Graefe (Foundations and Trends in DBs, 2011)
- **"The Adaptive Radix Tree"** - Leis et al. (ICDE 2013)
- **"Cuckoo Filter: Practically Better Than Bloom"** - Fan et al. (CoNEXT 2014)
- **"LSM-based Storage Techniques: A Survey"** - Luo & Carey (VLDB Journal 2020)
- **LMAX Disruptor** - Mechanical sympathy blog

**Probabilistic & streaming:**
- **"An Improved Data Stream Summary: The Count-Min Sketch"** - Cormode & Muthukrishnan (2005)
- **"HyperLogLog in Practice"** - Heule, Nunkesser, Hall (Google, 2013)
- **"Xor Filters: Faster and Smaller Than Bloom and Cuckoo Filters"** - Graf & Lemire (2020)
- **"Ribbon Filter: Practically Smaller Than Bloom and Xor"** - Dillinger & Walzer (2021)

**Learned & adaptive structures:**
- **"The Case for Learned Index Structures"** - Kraska et al. (SIGMOD 2018)
- **"PGM-Index: A Fully-Dynamic Compressed Learned Index"** - Ferragina & Vinciguerra (VLDB 2020)
- **"ALEX: An Updatable Adaptive Learned Index"** - Ding et al. (SIGMOD 2020)
- **"Are Updatable Learned Indexes Ready?"** - Wongkham et al. (VLDB 2022)

**Concurrent & distributed:**
- **"Cache Craftiness for Fast Multicore Key-Value Storage"** (Masstree) - Mao et al. (EuroSys 2012)
- **"Concurrent Tries with Efficient Non-Blocking Snapshots"** (CTrie) - Prokopec et al. (PPoPP 2012)
- **"A Comprehensive Study of CRDTs"** - Shapiro et al. (INRIA 2011)
- **"Fugue: A List CRDT with Maximal Non-Interleaving"** - Weidner (2023)

**Vector search & ANN:**
- **"Efficient and Robust Approximate Nearest Neighbor Search Using HNSW Graphs"** - Malkov & Yashunin (TPAMI 2020)
- **"DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node"** - Subramanya et al. (NeurIPS 2019)
- **"Accelerating Large-Scale Inference with ANN on GPUs"** (Glass) - 2023

**Succinct & compressed:**
- **"Succinct Data Structures"** - Navarro (Cambridge University Press, 2016)
- **"Opportunistic Data Structures with Applications"** (FM-Index) - Ferragina & Manzini (FOCS 2000)
- **"Wavelet Trees for All"** - Navarro (J. Discrete Algorithms, 2014)

**Memory-efficient & compressed:**
- **"Roaring Bitmaps: Implementation of an Optimized Software Library"** - Lemire et al. (Software: Practice & Experience, 2018)
- **"Swiss Tables and absl::flat_hash_map"** - Kulukundis (CppCon 2017)
- **"RecSplit: Minimal Perfect Hashing via Recursive Splitting"** - Esposito et al. (ALENEX 2020)
- **"PTHash: Revisiting FCH Minimal Perfect Hashing"** - Pibiri & Trani (SIGIR 2021)
- **"The WebGraph Framework"** - Boldi & Vigna (WWW 2004)
- **"Array Layouts for Comparison-Based Searching"** (Eytzinger) - Khuong & Morin (JEA 2017)

**Distributed & concurrent:**
- **"Chord: A Scalable Peer-to-peer Lookup Protocol"** - Stoica et al. (IEEE/ACM TON 2003)
- **"Kademlia: A Peer-to-peer Information System Based on the XOR Metric"** - Maymounkov & Mazieres (IPTPS 2002)
- **"Logical Physical Clocks and Consistent Snapshots"** (HLC) - Kulkarni et al. (OPODIS 2014)
- **"Flat Combining and the Synchronization-Parallelism Tradeoff"** - Hendler et al. (SPAA 2010)
- **"Safe Memory Reclamation for Optimistic Concurrency"** (Epoch-based) - Fraser (PhD thesis, 2004)
- **"Hazard Pointers: Safe Memory Reclamation for Lock-Free Objects"** - Michael (TPDS 2004)
- **"Delta State Replicated Data Types"** - Almeida et al. (J. Parallel & Distributed Computing, 2018)
- **"The Bw-Tree: A B-tree for New Hardware Platforms"** - Levandoski et al. (ICDE 2013)

**Compute-efficient & cache-aware:**
- **"SIMD-Scan: Ultra Fast In-Memory Table Scan Using SIMD Instructions"** - Polychroniou et al. (VLDB 2015)
- **"ARC: A Self-Tuning, Low Overhead Replacement Cache"** - Megiddo & Modha (FAST 2003)
- **"CLOCK-Pro: An Effective Improvement of the CLOCK Replacement"** - Jiang et al. (USENIX ATC 2005)
- **"Packed Memory Arrays"** - Bender & Hu (FOCS 2007)
- **"Optimal Streaming Quantile Sketches"** (KLL) - Karnin, Lang, Liberty (VLDB 2016)
- **"DDSketch: A Fast and Fully-Mergeable Quantile Sketch"** - Masson et al. (VLDB 2019)
- **"Self-Adjusting Binary Search Trees"** (Splay) - Sleator & Tarjan (JACM 1985)

---

## See Also

- [Join Algorithms](@/notes/database/join_algorithms.md) — Hash tables, Bloom filters, and ART indexes in join implementations
- [LSM Trees](@/notes/database/lsm_trees.md) — LSM-tree architecture using Bloom filters, skip lists, and merge strategies covered here
- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — CRDTs, vector clocks, and consistent hashing used in distributed consensus systems
- [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) — SIMD instructions enabling the vectorized data structures documented here
