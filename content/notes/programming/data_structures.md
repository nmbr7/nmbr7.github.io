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

```
┌───┬───┬───┬───┬───┬───┬───┬───┐
│ 0 │ 1 │ 2 │ 3 │ 4 │ 5 │ 6 │ 7 │
└───┴───┴───┴───┴───┴───┴───┴───┘
      ↑               ↑
    read            write
```

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

```
         ┌─────────────────────┐
         │  [50]  buffer:[]    │
         └──────────┬──────────┘
                    │
    ┌───────────────┴───────────────┐
    │                               │
┌───┴────────────┐    ┌─────────────┴────┐
│[20,30]         │    │[70,90]           │
│buffer:[ins:25] │    │buffer:[del:85]   │
└────────────────┘    └──────────────────┘
```

**Key properties:**
- O(log N / B) amortized I/O for insertions (vs O(log N) for B-tree)
- Cascading pushdown of buffered operations
- Write-optimized while maintaining read efficiency

---

## 3. **Skip List with Tower Optimization**

**Used in:** Redis sorted sets, LevelDB/RocksDB memtables, HFT order books

Probabilistic alternative to balanced trees with better cache behavior.

```
Level 3: HEAD ────────────────────────────────────> 50 ────────────> NIL
Level 2: HEAD ──────────> 20 ─────────────────────> 50 ────────────> NIL
Level 1: HEAD ──────────> 20 ──────────> 35 ──────> 50 ──> 60 ─────> NIL
Level 0: HEAD ─> 10 ─> 20 ─> 25 ─> 35 ─> 50 ─> 60 ─> 75 ─> 80 ─> NIL
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

```
Root (bitmap: 10100100)
 │
 ├─[bit 2]─→ Leaf: {key1: val1}
 │
 ├─[bit 5]─→ SubNode (bitmap: 00100010)
 │              ├─[bit 1]─→ Leaf: {key2: val2}
 │              └─[bit 5]─→ Collision: [{key3: val3}, {key4: val4}]
 │
 └─[bit 7]─→ Leaf: {key5: val5}
```

**Key properties:**
- 32 or 64-way branching at each level
- Population count (`POPCNT`) for index calculation
- Copy-on-write with path copying (only log₃₂(N) nodes)

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

```
┌─────────────────────────────────────┐
│           Judy Population Types     │
├─────────────────────────────────────┤
│ Null        : Empty subtree         │
│ Linear      : ≤2 keys inline        │
│ Bitmap      : Dense range, 256 bits │
│ Uncompressed: Sparse 256-way branch │
└─────────────────────────────────────┘
```

**Key properties:**
- Adapts structure based on data density
- No pointer overhead for dense regions
- Cache-optimized 64-byte nodes

---

## 7. **Cuckoo Hash Table**

**Used in:** DPDK, network packet processing, HFT symbol lookup

Guaranteed O(1) worst-case lookups with multiple hash functions.

```
Table 1 (h1):        Table 2 (h2):
┌───┬───────┐        ┌───┬───────┐
│ 0 │       │        │ 0 │       │
│ 1 │ key_A │◄───┐   │ 1 │ key_C │
│ 2 │       │    │   │ 2 │ key_A │──── kicked here
│ 3 │ key_B │    │   │ 3 │       │
└───┴───────┘    │   └───┴───────┘
                 │
Insert key_A: h1(A)=1, occupied by B
              kick B to h2(B), etc.
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

```
Universe U = 16 (keys 0-15)

         [0-15] summary
          /    \
     [0-7]     [8-15]
      / \        / \
   [0-3] [4-7] [8-11] [12-15]
    /\    /\    /\     /\
  0  2  4  6  8 10   12 14
```

**Y-Fast Trie (practical variant):**
- Top structure: X-Fast trie on representative elements
- Bottom structure: Balanced BSTs with O(log log U) elements each
- O(log log U) time, O(N) space

---

## 9. **Rope (for Strings/Buffers)**

**Used in:** Xi Editor, Visual Studio Code, Zed editor, large text processing

Binary tree of string chunks for efficient editing of massive texts.

```
           ┌──────────────┐
           │ weight=22    │
           │  (left len)  │
           └──────┬───────┘
                  │
      ┌───────────┴───────────┐
      │                       │
┌─────┴─────┐           ┌─────┴─────┐
│ weight=11 │           │  "world"  │
└─────┬─────┘           └───────────┘
      │
┌─────┴─────┐     ┌───────────┐
│  "Hello " │     │ "amazing " │
└───────────┘     └───────────┘
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

**Classic Bloom Filter:**
```
Insert "cat": hash1("cat")=2, hash2("cat")=5, hash3("cat")=7
Bit array: [0 0 1 0 0 1 0 1 0 0 0 0]
                ↑       ↑   ↑
Query "dog": if ALL hash positions are 1 → "maybe"; if ANY is 0 → "definitely not"
```

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

**Properties:**
- Space: O(1/ε × log(1/δ)) for ε-approximate counts with probability 1-δ
- No deletion in basic form; Count-Min-Log variant for heavy hitters
- Conservative update: only increment if it's the minimum → reduces overcount

**Variant — Count Sketch** (Charikar et al., 2002): unbiased estimator using ±1 signs, better for skewed distributions.

---

## 16. **HyperLogLog**

**Used in:** Redis `PFCOUNT`, BigQuery `APPROX_COUNT_DISTINCT`, Presto, Druid

Cardinality estimation using O(log log N) space per register.

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

**Properties:**
- 1.6KB for ~2% standard error (p=14, 16384 registers × 6 bits)
- Mergeable: max of each register → enables distributed counting
- HyperLogLog++ (Google, 2013): bias correction + sparse representation for small cardinalities

---

## 17. **HNSW (Hierarchical Navigable Small World Graph)**

**Used in:** Qdrant, Pinecone, pgvector, FAISS, Weaviate — the dominant ANN index

Multi-layer proximity graph for approximate nearest neighbor search in high-dimensional spaces.

```
Layer 2:  A ─────────────────── D         (sparse, long-range links)
          │                     │
Layer 1:  A ──── B ──── C ──── D ──── E   (medium connectivity)
          │      │      │      │      │
Layer 0:  A ─ B ─ C ─ D ─ E ─ F ─ G ─ H  (dense, all elements)

Search: enter at top layer → greedy walk → descend → refine
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

**Wavelet Tree** — answers rank/select/access on arbitrary alphabets:
```
String: "abracadabra"  Σ = {a,b,c,d,r}
         Root: bitvector over "left half" vs "right half" of alphabet
         /          \
    "aaaaaa"      "brcdbr"
      / \           / \
    ...  ...      ...  ...
```
- O(log|Σ|) rank/select/access on strings
- Enables compressed representations of permutations, grids, graphs

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

```
Key: "Hello World!"

8-byte slices used as B+-tree keys:

B+-tree Layer 0: key = "Hello Wo"
        │
        ▼ (border node links to next layer)
B+-tree Layer 1: key = "rld!\0\0\0\0"
        │
        ▼ value
```

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

```
CAS-based mutation with indirection nodes:

    Root (INode)
        │
    ┌───┴───┐
    │ CNode │ (immutable, array of branches)
    ├───┬───┤
    │   │   │
  SNode INode INode
  (leaf)  │     │
        CNode  CNode
```

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

```
        3
       /|\
      5  8  9
     /|    |
    12 7   14
       |
       11
```

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

```
Array:   [1, 3, 2, 5, 1, 7, 3, 2]
BIT:     [1, 4, 2, 10, 1, 8, 3, 24]

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

**Key properties:**
- Space: exactly N integers (no overhead)
- Implementation: ~10 lines of code
- 2D Fenwick tree for rectangle sum queries
- Supports order-statistic queries (find k-th smallest)

---

## 26. **Persistent / Retroactive Data Structures**

**Used in:** Git (Merkle trees), Datomic, functional languages (Haskell, Clojure), undo systems

Structures that preserve all previous versions after modification.

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

```
Fingerprint f = quotient (fq) : remainder (fr)

Slot addressed by fq, stores fr in a linear-probing cluster:

Slots:  [fr_0 | fr_1 | fr_2 | fr_3 | fr_4 | ...]
Meta:    occ   occ    cont   run    occ    ...
         is_occupied  is_continuation  is_shifted

Cluster: consecutive remainders belonging to same or adjacent quotients
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

```
Standard doubly-linked: each node stores prev AND next (16 bytes on 64-bit)

XOR list: each node stores prev ⊕ next (8 bytes)
  A         B         C         D
  [0⊕B]    [A⊕C]    [B⊕D]    [C⊕0]

Traversal from A→B:  npx(B) ⊕ addr(A) = (A⊕C) ⊕ A = C  ✓
```

**Related compressed structures:**
- **Unrolled linked list**: multiple elements per node for cache efficiency
- **Hashed array tree**: O(√N) wasted space, O(1) indexed access
- **Cache-oblivious B-tree** (Bender et al., 2000): optimal I/O for any cache/block size without knowing hardware parameters

---

## 30. **Dancing Links (DLX)**

**Used in:** Sudoku solvers, pentomino tiling, exact cover problems, SAT preprocessing

Knuth's technique for efficient backtracking on sparse binary matrices via circular doubly-linked lists.

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

```
Keys: {"cat", "dog", "fish", "bird"}  →  MPHF  →  {0, 1, 2, 3}

No collisions, no empty slots, no probing — just one function evaluation.

Construction (CHD / RecSplit):
  1. Find hash functions that map keys to graph/hypergraph
  2. Solve assignment via peeling/splitting
  3. Store compact representation (~2-3 bits/key)
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

```
Adjacency list (naive): each vertex has a Vec<Edge> → pointer per vertex, overhead per vec

CSR: two flat arrays

Vertex:    0     1     2     3     4
          │     │     │     │     │
offsets:  [0,    2,    5,    5,    7,   8]   ← cumulative edge count
edges:    [1, 3, 0, 2, 4, _, 1, 3, 2]       ← destination vertices (packed)

Neighbors of vertex 1: edges[offsets[1]..offsets[2]] = edges[2..5] = [0, 2, 4]
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

```
Standard trie: each node has up to |Σ| child pointers → wasteful for sparse tries

Double-array trie (Aoe, 1989):
  BASE[s] + c = t        ← transition from state s via char c lands at state t
  CHECK[t] = s           ← verify that t's parent is indeed s

Arrays:
Index: [ 0   1   2   3   4   5   6   7   8   9  ...]
BASE:  [ 1   3   _   5   _   7   _   _   _   _  ...]
CHECK: [-1   0   _   0   _   1   _   _   _   _  ...]

Lookup "ab" (a=0, b=1):
  state=0: BASE[0]+0=1, CHECK[1]=0 ✓ → state=1
  state=1: BASE[1]+1=4, but CHECK[4]≠1 → not found
```

**Key properties:**
- Two integers per state (vs. |Σ| pointers in naive trie)
- O(1) per character lookup (array indexing, no pointer chasing)
- Cache-friendly: sequential array access patterns
- Dynamic variant (CEDAR, Yoshinaga & Kitsuregawa, 2014): supports insert/delete
- HAT-trie (Askitis & Sinha, 2007): hybrid hash + trie, faster than std::unordered_map for strings

---

## 46. **Sketch Structures (Theta Sketch / KLL Sketch)**

**Used in:** Apache Datasketches, Druid, Snowflake, BigQuery, Presto

Sub-linear space summaries enabling set operations and quantile estimation at scale.

```
Theta Sketch (set operations):

Concept: keep all hash values below threshold θ

Stream A:  h(a1)=0.1, h(a2)=0.7, h(a3)=0.3, h(a4)=0.9  (θ=0.5 → keep {0.1, 0.3})
Stream B:  h(b1)=0.2, h(b2)=0.4, h(b3)=0.8              (θ=0.5 → keep {0.2, 0.4})

Union:     {0.1, 0.2, 0.3, 0.4}, θ=0.5 → |estimate| = 4/0.5 = 8
Intersect: {}, θ=0.5 → |estimate| = 0
Difference: sketch(A) - sketch(B) via inclusion-exclusion
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
