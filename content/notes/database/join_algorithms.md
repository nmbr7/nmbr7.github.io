+++
title = "Join Algorithms"
date = 2026-02-25
[taxonomies]
tags = ["join", "hash-join", "simd", "query-optimization", "vectorized-execution", "sort-merge", "radix-partitioning"]
+++


# Join Algorithms: Classic, Modern, and Distributed

**Date**: 2026-02-25
**Context**: Comprehensive research covering join algorithm fundamentals, state-of-the-art techniques, distributed join processing, and production system implementations. Intended as a reference for database systems engineers and researchers.

---

## Table of Contents

1. [Notation and Cost Model](#notation-and-cost-model)
2. [Classic Join Algorithms](#classic-join-algorithms)
3. [Modern and Advanced Join Algorithms](#modern-and-advanced-join-algorithms)
4. [Distributed Join Algorithms](#distributed-join-algorithms)
5. [Join Trees and Plan Shapes](#join-trees-and-plan-shapes)
6. [Multi-Way Joins and Query Optimization](#multi-way-joins-and-query-optimization)
7. [Performance Characteristics](#performance-characteristics)
8. [Correctness Concerns](#correctness-concerns)
9. [Production System Implementations](#production-system-implementations)
10. [Key Papers and References](#key-papers-and-references)

---

## Notation and Cost Model

Throughout this document, we use the following notation:

| Symbol | Meaning |
|--------|---------|
| `R`, `S` | Relations (tables) being joined |
| `\|R\|` | Cardinality (number of tuples) of R |
| `B(R)` | Number of disk pages occupied by R |
| `M` | Number of available buffer pages in memory |
| `p_R` | Tuples per page in R |
| `f` | Fan-out of an index (B-tree) |
| `h` | Height of a B-tree index |
| `HT(R)` | Number of pages for a hash table on R |

**I/O cost model**: We count the number of page I/Os (reads + writes). CPU cost is noted separately where relevant. We assume R is the smaller (build) relation and S is the larger (probe) relation unless stated otherwise.

**Memory model**: M buffer pages are available. One page can hold `p_R` tuples of R. A relation R occupies `B(R) = |R| / p_R` pages.

---

## Classic Join Algorithms

### 1. Nested Loop Join (NLJ)

The simplest join algorithm. Three variants with dramatically different performance.

#### 1a. Tuple-at-a-Time Nested Loop Join (Simple NLJ)

```
for each tuple r in R:
    for each tuple s in S:
        if theta(r, s):      // join predicate
            emit (r, s)
```

**I/O Cost**: `B(R) + |R| * B(S)`

For each tuple of R, we scan all of S. Since R has `|R|` tuples and each requires a full scan of S (`B(S)` pages), the cost is enormous.

**Example**: R = 1000 pages, S = 5000 pages, 100 tuples/page.
Cost = 1000 + (100 * 1000) * 5000 = 500,001,000 page I/Os.

**When to use**: Almost never in disk-based systems. Acceptable only when both relations fit in cache and are tiny (< few hundred tuples).

#### 1b. Block (Page) Nested Loop Join (BNLJ)

```
for each block b_R of R (using M-2 pages):
    for each page p_S of S:
        for each tuple r in b_R:
            for each tuple s in p_S:
                if theta(r, s):
                    emit (r, s)
```

**I/O Cost**: `B(R) + ceil(B(R) / (M-2)) * B(S)`

We read R in chunks of (M-2) pages, using one page for S input and one for output. For each chunk of R, we scan all of S once.

**Example**: R = 1000 pages, S = 5000 pages, M = 102 pages.
Cost = 1000 + ceil(1000/100) * 5000 = 1000 + 10 * 5000 = 51,000 page I/Os.

**Key insight**: Always put the smaller relation in the outer loop. If R fits in M-2 pages, cost reduces to `B(R) + B(S)` (one scan each).

#### 1c. Index Nested Loop Join (INLJ)

```
for each tuple r in R:
    use index on S.join_key to find matching tuples
    for each matching tuple s:
        emit (r, s)
```

**I/O Cost**: `B(R) + |R| * C_index`

Where `C_index` is the cost of one index lookup:
- **Clustered B-tree**: `h + 1` (traverse tree + read leaf page with matching tuples)
- **Unclustered B-tree**: `h + |matching tuples|` (each match may be on a different page)
- **Hash index**: `1.2` on average (one lookup + potential overflow)

**Example**: R = 1000 pages (100K tuples), S has clustered B-tree (h=3).
Cost = 1000 + 100,000 * 4 = 401,000 page I/Os.

**When optimal**: Small outer relation, selective join predicate, index on inner relation's join key. This is the standard strategy for OLTP point lookups and foreign-key traversals.

```
            Nested Loop Join Performance Summary
            =====================================

Variant     | I/O Cost                        | Best When
------------|--------------------------------|---------------------------
Simple NLJ  | B(R) + |R| * B(S)             | Never (theoretical baseline)
Block NLJ   | B(R) + ceil(B(R)/(M-2))*B(S)  | No index, small R
Index NLJ   | B(R) + |R| * C_index          | Good index on S, small R
```

### 2. Sort-Merge Join (SMJ)

Two phases: sort both relations on the join key, then merge.

#### Algorithm

```
Phase 1: Sort
    Sort R on join key -> R'
    Sort S on join key -> S'

Phase 2: Merge
    r_ptr = first tuple in R'
    s_ptr = first tuple in S'
    while r_ptr and s_ptr are valid:
        if R'.key < S'.key:
            advance r_ptr
        elif R'.key > S'.key:
            advance s_ptr
        else:  // match
            // Mark start of matching group in S
            s_mark = s_ptr
            while r_ptr.key == s_ptr.key:
                while s_ptr.key == r_ptr.key:
                    emit (r_ptr, s_ptr)
                    advance s_ptr
                advance r_ptr
                reset s_ptr to s_mark  // backtrack for duplicate keys
```

#### I/O Cost Analysis

**External sort cost per relation** (using replacement-selection or quicksort):
- Pass 0 (run generation): `2 * B(R)` I/Os (read + write all pages)
- Each merge pass: `2 * B(R)` I/Os
- Number of merge passes: `ceil(log_{M-1}(ceil(B(R)/M)))`

**Total sort cost**: `2 * B(R) * (1 + ceil(log_{M-1}(ceil(B(R)/M))))`

**Merge phase**: `B(R) + B(S)` (single scan of each sorted relation)

**Total cost**: `Sort(R) + Sort(S) + B(R) + B(S)`

If both R and S are already sorted (e.g., clustered index on join key): cost is just `B(R) + B(S)`.

**Example**: R = 1000 pages, S = 5000 pages, M = 100 pages.
Sort R: runs = ceil(1000/100) = 10 runs, 1 merge pass. Cost = 2*1000*2 = 4000
Sort S: runs = ceil(5000/100) = 50 runs, 1 merge pass. Cost = 2*5000*2 = 20000
Merge: 1000 + 5000 = 6000
Total: 30,000 page I/Os.

#### Interesting Orderings

A critical optimization: if R is already sorted on the join key (e.g., from a preceding ORDER BY, GROUP BY, or clustered index), we skip sorting R entirely. The optimizer should propagate "interesting orderings" through the plan tree, recognizing that a sort-merge join can exploit upstream sort orders.

This concept was first formalized in Selinger et al.'s System R paper (SIGMOD 1979) and remains central to all modern optimizers.

**When optimal**:
- One or both inputs already sorted on join key
- Result needs to be sorted (e.g., ORDER BY on join key)
- Very large relations where hash join would cause excessive partitioning
- Merge join is the only join that can be performed in a fully streaming manner with sorted inputs

### 3. Hash Join

The workhorse of analytical query processing. Three major variants.

#### 3a. Simple (Classic) Hash Join

```
Build Phase:
    for each tuple r in R:
        insert r into in-memory hash table HT using h(r.join_key)

Probe Phase:
    for each tuple s in S:
        probe HT using h(s.join_key)
        for each matching r in HT bucket:
            if r.join_key == s.join_key:  // resolve collisions
                emit (r, s)
```

**Requirements**: Hash table on R must fit in memory. `HT(R) <= M` pages.

**I/O Cost**: `B(R) + B(S)` (optimal -- single scan of each relation)

**CPU Cost**: `O(|R| + |S|)` expected, assuming good hash function.

**When to use**: Build side fits in memory. This is the most common scenario in OLAP.

#### 3b. Grace Hash Join (Partition-Based)

When neither relation fits in memory, partition both using the same hash function, then join matching partitions.

```
Partition Phase:
    // Partition R into M-1 buckets using hash function h1
    for each tuple r in R:
        write r to partition h1(r.join_key) % (M-1)
    // Partition S into M-1 buckets using same h1
    for each tuple s in S:
        write s to partition h1(s.join_key) % (M-1)

Probe Phase:
    for i = 0 to M-2:
        // Load R_i into memory, build hash table using h2
        build hash table on R_i using h2 (different from h1)
        // Probe with S_i
        for each tuple s in S_i:
            probe hash table using h2(s.join_key)
            emit matches
```

```
   Grace Hash Join: Partition Phase
   =================================

   R                                S
   +--------+                      +--------+
   | tuples | --h1()--> Buckets    | tuples | --h1()--> Buckets
   +--------+                      +--------+

   R_0  R_1  R_2 ... R_{M-2}      S_0  S_1  S_2 ... S_{M-2}
   [==] [==] [==]     [====]      [==] [==] [==]     [====]
     |    |    |         |          |    |    |         |
     v    v    v         v          v    v    v         v
    disk disk disk     disk       disk disk disk     disk

   Probe Phase: For each i, load R_i, build HT, probe with S_i
   =============================================================

   R_i (in memory)         S_i (streaming)
   +----------------+      +--------+
   | Hash Table h2  | <--- | tuples |
   +----------------+      +--------+
          |
          v
       matches
```

**I/O Cost**: `3 * (B(R) + B(S))`

- Read R + S for partitioning: `B(R) + B(S)`
- Write partitions: `B(R) + B(S)`
- Read partitions for probe: `B(R) + B(S)`

**Requirement**: Each partition of R must fit in memory: `B(R) / (M-1) <= M`, so `B(R) <= M * (M-1) ~= M^2`.

**Recursive partitioning**: If a partition is still too large, apply another round of partitioning with a different hash function. Cost increases to `2 * (B(R) + B(S)) * ceil(log_{M-1}(B(R)/M)) + B(R) + B(S)`.

#### 3c. Hybrid Hash Join

Optimization over Grace: keep one partition of R in memory during the partition phase, avoiding writing and re-reading it.

```
Partition Phase:
    // Partition 0 of R stays in memory as a hash table
    // Partitions 1..k are written to disk
    for each tuple r in R:
        p = h1(r.join_key) % k
        if p == 0:
            insert into in-memory hash table
        else:
            write to disk partition p

    // Partition S: partition 0 is immediately probed
    for each tuple s in S:
        p = h1(s.join_key) % k
        if p == 0:
            probe in-memory hash table, emit matches
        else:
            write to disk partition p

Remaining Partitions:
    for i = 1 to k-1:
        load R_i, build hash table, probe with S_i
```

**I/O Cost**: `3 * (B(R) + B(S)) - 2 * (M - k)` where `(M - k)` pages are used for the in-memory partition.

In the best case (R fits entirely in memory), this degenerates to the simple hash join: `B(R) + B(S)`.

**Advantage**: Smoothly adapts between in-memory and fully partitioned execution. Most production systems implement this variant.

```
    Hash Join Variant Comparison
    ============================

    Variant      | I/O Cost          | Memory Req.  | Notes
    -------------|-------------------|-------------|---------------------------
    Simple       | B(R)+B(S)         | HT(R)<=M    | Optimal when R fits
    Grace        | 3*(B(R)+B(S))     | B(R)<=M^2   | Clean partitioning
    Hybrid       | Between above     | Adaptive     | Best general-purpose
```

---

## Modern and Advanced Join Algorithms

### 4. Radix Hash Join (Cache-Conscious Hash Join)

Proposed to address the primary bottleneck of hash joins on modern hardware: **TLB and cache misses** during the build and probe phases. Random access to a large hash table causes cache thrashing.

#### Core Idea

Multi-pass radix partitioning ensures that each partition fits in the CPU cache (typically L2: 256KB-1MB). Joins within cache-resident partitions avoid TLB misses and exploit spatial locality.

```
Radix Partition Phase (multi-pass):
    // Pass 1: Partition on bits b1..bk of hash (coarse)
    // Pass 2: Partition on bits bk+1..bm (fine)
    // ... until each partition fits in L2 cache

Build + Probe Phase:
    for each partition pair (R_i, S_i):
        // Both fit in L2 cache
        build hash table on R_i
        probe with S_i
```

```
    Radix Partitioning (2-pass example)
    ====================================

    Input R:  [a b c d e f g h i j k l m n o p]
                        |
              Pass 1: partition on bits 0-1
                        |
         +------+------+------+------+
         | a e  | b f  | c g  | d h  |
         | i m  | j n  | k o  | l p  |
         +------+------+------+------+
           P0     P1     P2     P3
                        |
              Pass 2: partition on bits 2-3
                        |
    P0: [a,i] [e,m]   P1: [b,j] [f,n]   ... (16 sub-partitions)

    Each sub-partition fits in L2 cache.
    Build + probe happens entirely in cache.
```

#### Tuning Parameters

The number of radix bits is chosen based on hardware:
- **L2 cache size** `C_L2`: Partitions must satisfy `|R_i| * tuple_size < C_L2`
- **TLB entries** `T`: Number of partitions in each pass must be `<= T` (typically 64-512)
- **Number of passes**: `ceil(log_T(B(R) * page_size / C_L2))`

#### Performance

- Achieves near-bandwidth-limited performance: up to **200M tuples/sec** on modern hardware (Balkesen et al., VLDB 2013)
- **2-5x faster** than non-partitioned hash joins on large datasets
- Diminishing returns when data already fits in cache

#### Key Papers

- Shatdal, Kant, Naughton: "Cache Conscious Algorithms for Relational Query Processing", VLDB 1994
- Manegold, Boncz, Kersten: "Optimizing Main-Memory Join on Modern Hardware", TKDE 2002
- Balkesen, Alonso, Teubner, Ozsu: "Multi-Core, Main-Memory Joins: Sort vs. Hash Revisited", VLDB 2013
- Kim, Sedlar, Chhugani et al.: "Sort vs. Hash Revisited: Fast Join Implementation on Modern Multi-Core CPUs", VLDB 2009

### 5. Massively Parallel Sort-Merge (MPSM)

Designed for NUMA architectures where cross-socket memory access is expensive.

#### Core Idea

Each thread sorts its local partition independently (no cross-NUMA traffic during sort). Then, each thread merges its local sorted run against all other sorted runs.

```
Phase 1: Local Sort
    Each thread T_i sorts its local partition R_i of R

Phase 2: Parallel Merge
    Each thread T_i merges sorted R_i with all sorted S_j partitions
    No data movement across NUMA nodes during sort phase
```

**Advantage**: Avoids NUMA-remote writes entirely during the sort phase. The merge phase accesses remote data read-only, which is faster than remote writes.

**Disadvantage**: Redundant work -- each thread reads all of S. Total work is `O(n * |S|)` where n is thread count.

**Key paper**: Albutiu, Kemper, Neumann: "Massively Parallel Sort-Merge Joins in Main-Memory Multi-Core Database Systems", VLDB 2012.

### 6. Worst-Case Optimal Joins (WCOJ)

A paradigm shift from binary (pairwise) joins to multi-way joins that process all relations simultaneously.

#### The Problem with Binary Joins

Consider a triangle query: `R(A,B) JOIN S(B,C) JOIN T(A,C)`.

Binary join plans (left-deep or bushy) may produce intermediate results of size `O(|R| * |S|)` before filtering with T, even when the final result is much smaller. The AGM bound (Atserias, Grohe, Marx, 2008) proves that the maximum output size is `O(N^{3/2})` for three relations of size N, but binary plans can produce `O(N^2)` intermediate tuples.

#### The AGM Bound

For a natural join query `Q = R_1 JOIN R_2 JOIN ... JOIN R_m`, the maximum result size is bounded by:

`|Q| <= prod_{i=1}^{m} |R_i|^{x_i}`

where `x_i` are the optimal solution to the fractional edge cover LP. For the triangle query with `|R| = |S| = |T| = N`, this gives `O(N^{3/2})`.

#### Generic Join (Ngo, Porat, Re, Rudra -- PODS 2012)

The first worst-case optimal algorithm. It processes one attribute at a time:

```
GenericJoin(Q, bound_vars, assignment):
    if all variables bound:
        emit assignment
        return

    Pick next variable x
    // Intersect the domains of x across all relations containing x
    D_x = INTERSECT over all R_i containing x:
              { v : exists tuple in R_i matching assignment with x=v }

    for each v in D_x:
        GenericJoin(Q, bound_vars + {x}, assignment + {x=v})
```

**Complexity**: `O(|Q|_max * |attrs|)` where `|Q|_max` is the AGM bound. This is optimal up to the attribute count factor.

#### Leapfrog TrieJoin (Veldhuizen, ICDT 2014)

A practical, elegant implementation of WCOJ using sorted trie iterators.

```
Data Structure: Each relation stored as a sorted trie (lexicographic order)
    R(A,B): sorted on (A, B)
    S(B,C): sorted on (B, C)
    T(A,C): sorted on (A, C)

Algorithm:
    // Variable ordering: A, B, C
    // Level 0: iterate over A values
    for each A value via LeapfrogJoin on {R.A, T.A}:
        // Level 1: iterate over B values for this A
        for each B value via LeapfrogJoin on {R.B(A=a), S.B}:
            // Level 2: iterate over C values for this (A,B)
            for each C value via LeapfrogJoin on {S.C(B=b), T.C(A=a)}:
                emit (a, b, c)
```

**LeapfrogJoin on k sorted iterators**:

```
LeapfrogJoin(iter_1, ..., iter_k):
    // All iterators positioned; find common values
    Sort iterators by current value
    loop:
        if iter_1.value == iter_k.value:
            emit value       // all agree
            advance all iterators
        else:
            // iter_1 has smallest value, iter_k has largest
            // leap iter_1 forward to seek(iter_k.value)
            iter_1.seek(iter_k.value)
            rotate iterator order
            if any iterator exhausted: break
```

```
    LeapfrogJoin Example on 3 Iterators
    ====================================

    iter_A: [1, 3, 5, 7, 9, 12, 15]
    iter_B: [2, 3, 6, 9, 11, 15]
    iter_C: [1, 3, 8, 9, 14, 15]

    Step 1: Sort by current value
            iter_A=1, iter_C=1, iter_B=2
            min=1, max=2 -> not equal
            Seek iter_A to 2 -> lands on 3

    Step 2: iter_B=2, iter_A=3, iter_C=1
            Seek iter_C to 3 -> lands on 3
            iter_B=2, iter_A=3, iter_C=3
            Seek iter_B to 3 -> lands on 3
            All equal at 3! EMIT 3.

    Step 3: Advance all. iter_A=5, iter_B=6, iter_C=8
            Seek iter_A to 8 -> lands on 9
            Seek iter_B to 9 -> lands on 9
            Seek iter_C to 9 -> lands on 9
            All equal at 9! EMIT 9.

    ... continues until iterators exhausted.
```

**Complexity**: Worst-case optimal up to a log factor. `O(|Q|_AGM * log(N))` where N is the max relation size. The log factor comes from binary search in seek operations.

**Practical significance**: LogicBlox/RelationalAI uses LeapfrogTrieJoin as their core join engine. DuckDB added WCOJ support via adaptive factorization (CIDR 2025). Graph databases benefit enormously from WCOJ for cycle queries.

### 7. Adaptive Join Algorithms

Joins that adjust their strategy at runtime based on observed data characteristics.

#### Eddies (Avnur and Hellerstein, SIGMOD 2000)

Route individual tuples through a set of join operators adaptively. A central "eddy" router observes throughput and selectivity, sending tuples to operators that are currently most efficient.

```
                   +--------+
      R tuples --> | Eddy   | --> Output
      S tuples --> | Router | -->
      T tuples --> |        |
                   +--------+
                    /  |   \
                   v   v    v
                  J1   J2   J3
              (R><S)(S><T)(R><T)
```

**Key insight**: Adapts to runtime conditions (data skew, index availability, memory pressure) without re-planning.

#### SteM (STate Modules) -- Raman and Hellerstein, VLDB 2003

Extension of eddies using state modules that maintain hash tables. Each SteM can function as either build-side or probe-side of a hash join, enabling symmetric processing.

#### XJoin (Urhan and Franklin, ICDE 2000)

Designed for streaming/online environments where data arrives at unpredictable rates.

```
Phase 1 (in-memory):
    As tuples arrive from R and S:
        Insert into respective in-memory hash tables
        Probe the OTHER table's hash table for matches
        (Symmetric: each new tuple probes the other side)

Phase 2 (cleanup):
    When memory fills, flush partitions to disk
    Later, join flushed partitions using standard hash join
```

**Advantage**: Produces results incrementally as data arrives. Ideal for streaming, federated queries, and interactive systems where time-to-first-result matters.

#### Hash-Merge Join (adaptive)

Starts as a hash join. If memory is exhausted, switches to sort-merge on remaining data. Combines the speed of in-memory hashing with the graceful degradation of sort-merge.

### 8. Vectorized and SIMD Join Techniques

Modern CPUs offer SIMD (Single Instruction, Multiple Data) instructions that process 4-16 values simultaneously. Join algorithms can exploit this.

#### Vectorized Hash Table Probing

```
// Scalar probe: 1 key at a time
for each key in probe_batch:
    bucket = hash(key) % num_buckets
    scan chain at bucket for match

// Vectorized probe: process VECTOR_SIZE keys simultaneously
keys    = load_vector(probe_batch, offset, VECTOR_SIZE)
hashes  = hash_vector(keys)             // SIMD hash
buckets = modulo_vector(hashes, num_buckets)  // SIMD mod
entries = gather(hash_table, buckets)    // SIMD gather
matches = compare_vector(keys, entries)  // SIMD compare
// Process matches, handle collisions for mismatches
```

**Key techniques**:
- **SIMD gather**: Load non-contiguous memory locations into a SIMD register (AVX2 `_mm256_i32gather`)
- **SIMD compare**: Compare 8 or 16 keys simultaneously
- **Horizontal/vertical vectorization**: Process one tuple across multiple buckets (horizontal) vs. multiple tuples across their respective buckets (vertical)

#### Vectorized Partitioning (for Radix Join)

Software write-combine buffers avoid TLB misses during partitioning:

```
// Without SWCB: random writes to N partitions -> N TLB entries needed
// With SWCB: buffer a cache line of tuples per partition, flush when full

for each tuple t:
    p = radix_partition(t.key)
    swcb[p].append(t)
    if swcb[p].full():  // cache-line sized buffer
        flush swcb[p] to partition[p]  // sequential write
```

#### Performance Impact

- Vectorized hash probing: **2-4x** speedup over scalar on AVX2/AVX-512
- SIMD partitioning: **1.5-3x** speedup
- Combined effect: radix join with SIMD achieves **1-2 billion tuples/sec** on modern multi-core CPUs

**Key papers**:
- Polychroniou, Raghavan, Ross: "Rethinking SIMD Vectorization for In-Memory Databases", SIGMOD 2015
- Kersten, Leis, Kemper, Neumann, Pavlo: "Everything You Always Wanted to Know About Compiled and Vectorized Queries But Were Afraid to Ask", VLDB 2018

### 9. Sideways Information Passing and Semi-Join Reductions

Reduce the data flowing into expensive joins by pre-filtering using information from other parts of the query plan.

#### Semi-Join Reduction

Before joining R and S, compute the set of join key values that actually exist in S, and filter R to only those matching keys. This is especially valuable in distributed settings where R and S are on different nodes.

```
Step 1: Compute pi_{join_key}(S)         // project S onto join key
Step 2: R' = R SEMI-JOIN pi_{join_key}(S) // filter R
Step 3: R' JOIN S                         // join reduced R with S
```

**Cost**: Extra pass over S to extract keys + filter R. Beneficial when the semi-join significantly reduces |R|.

#### Bloom Filter Optimization

Instead of sending the exact set of join keys, build a compact Bloom filter:

```
Build Phase:
    Create Bloom filter BF from S.join_key values

Probe Phase:
    for each tuple r in R:
        if BF.might_contain(r.join_key):
            keep r (may be false positive)
        else:
            discard r (guaranteed not in S)

Join Phase:
    Join filtered R' with S
```

**Advantages**:
- Bloom filter is tiny (few KB to few MB) compared to the key set
- Can be broadcast cheaply in distributed systems
- Filters out most non-matching tuples (false positive rate < 1% with proper sizing)
- Can be pushed down into table scans (predicate pushdown)

**Runtime filter pushdown**: Systems like Velox, DataFusion, and Spark push Bloom filters into TableScan operators, enabling partition/row group pruning before data even enters the join pipeline.

### 10. Hash Table Designs for Joins

The hash table is the central data structure for hash joins. Different designs have dramatically different performance characteristics.

#### Chained (Bucket) Hash Table

```
+---+---+---+---+---+---+---+---+
| 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |  Directory (pointers)
+---+---+---+---+---+---+---+---+
  |       |           |
  v       v           v
 [a]->[d] [b]        [c]->[e]->[f]     Chains (linked lists)
```

**Pros**: Simple, handles variable-length keys, no clustering issues.
**Cons**: Pointer chasing (cache miss per chain hop), poor memory locality.

#### Open Addressing (Linear Probing)

```
+---+---+---+---+---+---+---+---+
| a |   | b |   | c | d | e |   |  Flat array
+---+---+---+---+---+---+---+---+
```

**Pros**: Cache-friendly (sequential scan on collision), no pointer overhead.
**Cons**: Clustering degrades performance at high load factors, deletion is complex, poor for n:m joins.

#### Concise Hash Table (used in HyPer/Umbra)

Two arrays: a dense tuple array and a hash directory with offsets.

```
Hash Directory:     [2|0|1|3|0|0|0|1]  (counts per bucket)
Offset Array:       [0|2|2|3|6|6|6|6]  (prefix sum)
Dense Tuple Store:  [a,d | b | c,e,f | g]
                     ^       ^
                     bucket0  bucket2
```

**Pros**: Excellent cache behavior (sequential scan of dense array), very compact.
**Cons**: Build requires two passes (count + insert).

#### Unchained Adjacency-Array Hash Table (Birler et al., DaMoN 2024, Best Paper)

Combines build-side partitioning, adjacency array layout, Bloom filters, pipelined probes, and software write-combine buffers:

```
Phase 1: Count keys per bucket
Phase 2: Prefix sum to compute offsets
Phase 3: Insert tuples into adjacency array at computed positions

Directory:  [offset_0, offset_1, ..., offset_n]
Tuples:     [bucket_0 tuples | bucket_1 tuples | ...]
Bloom:      [filter bits for rapid rejection]
```

**Performance**: Outperforms open addressing by 2x on average for relational queries and up to 20x for graph queries with heavy n:m joins.

#### Linear-Chained Hash Table (Gross et al., CIDR 2025)

Used in DuckDB for adaptive factorization. Collisions resolved with linear probing (next bucket), but entries form logical chains. Enables "3D hash join" where probe emits chain pointers instead of expanding results:

- With caching: **17.58x** speedup on factorizable queries
- Without caching: **1.25x** speedup

---

## Distributed Join Algorithms

### 11. Broadcast Join (Replication)

Send the entire smaller relation to all nodes holding partitions of the larger relation.

```
    Coordinator                Node 1          Node 2          Node 3
    +----------+              +------+         +------+         +------+
    | R (small)|  broadcast   | R    |         | R    |         | R    |
    |          | -----------> | S_1  |         | S_2  |         | S_3  |
    +----------+              |JOIN  |         |JOIN  |         |JOIN  |
                              +------+         +------+         +------+
                                |                |                |
                                v                v                v
                             Result_1         Result_2         Result_3
```

**Network cost**: `|R| * (N-1)` where N is the number of nodes. Each node receives a full copy of R.

**When optimal**: `|R| << |S|` and `|R|` fits in memory on each node. Spark's default threshold: `spark.sql.autoBroadcastJoinThreshold = 10MB`.

**Advantages**: No shuffling of S (which is large), single-pass execution, no skew issues on S side.

**Disadvantages**: Prohibitive for large R, memory pressure on all nodes, redundant storage.

### 12. Shuffle (Repartition / Hash-Partitioned) Join

Both relations are hash-partitioned on the join key and sent to the same set of nodes.

```
    Node 1 (R_1, S_1)     Node 2 (R_2, S_2)     Node 3 (R_3, S_3)
         |                      |                      |
         | hash-partition on join key (shuffle)
         v                      v                      v
    +----------+           +----------+           +----------+
    | R'_1,S'_1|           | R'_2,S'_2|           | R'_3,S'_3|
    | local    |           | local    |           | local    |
    | hash join|           | hash join|           | hash join|
    +----------+           +----------+           +----------+
```

**Network cost**: `|R| + |S|` (every tuple is sent exactly once).

**When optimal**: Both R and S are large and not co-partitioned on the join key.

**Skew problem**: If join key values are highly skewed (e.g., one value appears in 50% of tuples), one node gets overloaded. Mitigation strategies:
- **Frequency-based partitioning**: Heavy hitters detected and replicated across nodes
- **Spark AQE skew join**: Splits skewed partitions and replicates the other side. Triggered when partition size > `spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes` (default 256MB) AND partition size > 5x the median partition size.

### 13. Semi-Join Reduction in Distributed Settings

Minimize network transfer by pre-filtering.

```
    Site A (has R)                    Site B (has S)
    +------------+                    +------------+
    | R          |  1. Send BF(R)     | S          |
    |            | -----------------> |            |
    |            |                    | Filter S   |
    |            |  2. Send S'        | using BF   |
    |            | <----------------- | S' = S     |
    |            |                    | semi-join  |
    | R JOIN S'  |                    | BF(R)      |
    +------------+                    +------------+

    BF(R) = Bloom filter on R's join keys (compact, few KB)
    S' = tuples of S that pass the Bloom filter (much smaller than S)
```

**Network cost**: `|BF(R)| + |S'|` where `|S'| << |S|` if the join is selective.

**Two-phase semi-join**: Send BF(R) to filter S, then send BF(S') back to filter R, then join. Worth it when the join is very selective and network is the bottleneck.

### 14. Track Join (Parallel Partition Discovery)

An optimization for co-located data. If R and S are both range-partitioned on a related key, the join can be decomposed into independent sub-joins without any data movement:

```
If R partitioned on R.key as: [0-100], [101-200], [201-300]
   S partitioned on S.key as: [0-100], [101-200], [201-300]

Then: R[0-100] JOIN S[0-100], R[101-200] JOIN S[101-200], ...
      All independent, no shuffling needed.
```

**When applicable**: Both tables co-partitioned (same partition key, same partition boundaries). CockroachDB and TiDB exploit this when tables share partition schemes.

### 15. Distributed Sort-Merge Join

```
Phase 1: Range-partition both R and S using sampled splitters
    - Sample keys from both relations
    - Determine range boundaries
    - Shuffle tuples to appropriate nodes based on range

Phase 2: Local sort-merge join at each node
    - Each node sorts its partition of R and S
    - Performs standard merge join
```

**Network cost**: `|R| + |S|` (same as shuffle join).

**Advantage over hash shuffle**: Output is globally sorted. Useful when downstream operators need sorted data (e.g., window functions, ORDER BY).

### 16. Symmetric Hash Join (Streaming)

For streaming environments where tuples arrive asynchronously from both sides.

```
Hash Table for R: HT_R
Hash Table for S: HT_S

on_arrival(tuple t from R):
    insert t into HT_R
    probe HT_S for matches with t
    emit all matches

on_arrival(tuple t from S):
    insert t into HT_S
    probe HT_R for matches with t
    emit all matches
```

**Properties**:
- Non-blocking: produces output as soon as any match exists
- Handles out-of-order arrival
- Memory grows unboundedly (both hash tables kept)
- Used in streaming systems (Flink, RisingWave)

### 17. MapReduce-Style Joins

#### Reduce-Side Join

Both relations emit `(join_key, tagged_value)` pairs. The shuffle groups all values with the same key together. The reducer performs a local cross product.

```
Map(R):  for each (k, v) in R: emit (k, ("R", v))
Map(S):  for each (k, v) in S: emit (k, ("S", v))

Shuffle: group by key

Reduce(key, values):
    R_vals = [v for (tag, v) in values if tag == "R"]
    S_vals = [v for (tag, v) in values if tag == "S"]
    for r in R_vals:
        for s in S_vals:
            emit (key, r, s)
```

**Cost**: Full shuffle of both relations. Simple but inefficient for broadcast-suitable joins.

#### Map-Side Join

Requires one relation (R) to be small enough to load into memory on each mapper.

```
Setup: Distribute R to all map tasks (via distributed cache)

Map(S_split):
    Load R into hash table
    for each (k, v) in S_split:
        probe hash table for matches
        emit matches
```

**Cost**: Broadcast R, then single-pass over S. Equivalent to broadcast join.

#### Replicated Join (for multi-way joins in MapReduce)

For theta-joins or multi-way joins, partition the join space into a grid and replicate tuples:

```
Two relations R(A) and S(B) with theta condition: R.A + S.B < 100

Partition space into p x q grid cells.
Each R tuple sent to q cells (replicated across one dimension).
Each S tuple sent to p cells (replicated across other dimension).
Each cell independently evaluates theta condition.
```

**Key paper**: Okcan and Riedewald: "Processing Theta-Joins using MapReduce", SIGMOD 2011.

### 18. Theta Joins in Distributed Settings

Non-equi-joins (e.g., `R.A < S.B`) cannot use hash partitioning. Strategies:

- **Band join**: If `|R.A - S.B| < delta`, partition into overlapping ranges
- **1-Bucket-Theta**: Partition join matrix into roughly equal-sized regions using sampling
- **Block nested loop with partition**: Each node gets a block of R and a block of S, performs cross product with theta filter

**Cost**: Fundamentally harder than equi-joins. Lower bound is `O(|R| * |S| / N)` for N nodes when no index exists.

---

## Join Trees and Plan Shapes

The optimizer must decide the order in which to join multiple tables. The shape of the resulting join tree significantly impacts performance.

### Left-Deep Trees

```
         JOIN
        /    \
      JOIN    T4
     /    \
   JOIN    T3
  /    \
T1     T2
```

**Properties**:
- Every right child is a base relation (leaf)
- Fully pipelined: each join reads its right input and probes/looks up in the left pipeline
- `n!` possible left-deep trees for n relations (without commutativity)
- Standard for row-at-a-time (Volcano) execution with index nested loop joins
- Limited parallelism: linear chain, each operator depends on the previous

### Right-Deep Trees

```
    JOIN
   /    \
  T1   JOIN
      /    \
     T2   JOIN
          /    \
         T3    T4
```

**Properties**:
- Every left child is a base relation
- All base relations can be hashed simultaneously (build all hash tables first, then probe through the chain)
- Good for hash joins: build phase of all joins can proceed in parallel
- Requires enough memory for all hash tables simultaneously: `sum(HT(T_i))`

### Bushy Trees

```
       JOIN
      /    \
   JOIN    JOIN
  /    \  /    \
T1    T2 T3    T4
```

**Properties**:
- Interior nodes can have non-leaf children on both sides
- Strictly more general: includes left-deep and right-deep as special cases
- `(2(n-1))! / (n-1)!` possible bushy trees for n relations (Catalan number growth)
- Enables inter-operator parallelism: independent sub-trees can execute concurrently
- Harder to enumerate: DPccp/DPhyp needed for efficient search

### Comparison

```
Plan Shape  | Search Space    | Parallelism    | Memory         | Pipeline
------------|----------------|----------------|----------------|----------
Left-deep   | O(n!)          | Limited        | 1 HT at a time | Full
Right-deep  | O(n!)          | Build parallel | All HTs in mem | Full
Bushy       | O(4^n / n^1.5) | Max parallel   | Flexible       | Partial
```

Most modern optimizers (DuckDB, CockroachDB, Umbra) consider bushy plans. PostgreSQL's GEQO (Genetic Query Optimizer) kicks in at >= 12 tables because exhaustive enumeration is too expensive.

---

## Multi-Way Joins and Query Optimization

### Join Ordering Algorithms

Given n tables, find the optimal join order. This is one of the hardest problems in query optimization.

#### Dynamic Programming: DPsize (System R style)

The classic bottom-up approach from Selinger et al. (SIGMOD 1979):

```
// Build optimal plan for each subset of relations
for size = 1 to n:
    for each subset S of size 'size':
        for each way to split S into (S1, S2) where S1 JOIN S2 is valid:
            cost = Cost(best_plan(S1)) + Cost(best_plan(S2)) + JoinCost(S1, S2)
            if cost < best_plan(S):
                best_plan(S) = Plan(S1, S2)
```

**Complexity**: `O(3^n)` with pruning, `O(2^n)` space.

**Limitation**: Considers only left-deep trees in the original System R formulation. Extended to bushy trees, but enumerates many disconnected subsets wastefully.

#### DPccp (Moerkotte and Neumann, SIGMOD 2006)

**Dynamic Programming on Connected subgraph-Complement Pairs.** Only enumerates pairs of subsets `(S1, S2)` that are both connected in the join graph and connected to each other. Avoids generating cross products.

```
DPccp(JoinGraph G):
    for each vertex v in G:
        dpTable[{v}] = AccessPlan(v)

    for each connected subgraph S in G (in increasing size):
        for each connected complement C of S in G:
            // (S, C) is a csg-cmp pair
            plan = MakeJoin(dpTable[S], dpTable[C])
            if plan.cost < dpTable[S union C].cost:
                dpTable[S union C] = plan
```

**Complexity**: `O(3^n)` worst case, but much faster in practice because it skips disconnected subsets.

**Limitation**: Only handles binary (simple) join predicates, not hyperedges.

#### DPhyp (Moerkotte and Neumann, ICDE 2008)

Extends DPccp to handle **hypergraph** join predicates (predicates involving more than 2 relations, e.g., `R.a + S.b = T.c`).

Models the join graph as a hypergraph where hyperedges connect sets of relations. The algorithm enumerates connected subgraph-complement pairs on the hypergraph.

**Used by**: MySQL 8.0.23+ (hypergraph optimizer), DuckDB, Umbra/CedarDB.

#### Greedy Operator Ordering (GOO)

```
while more than one relation remains:
    pick the pair (R_i, R_j) with minimum estimated join cost
    replace R_i and R_j with their join result
```

**Complexity**: `O(n^3)` (n iterations, each scanning `O(n^2)` pairs).

**Quality**: No optimality guarantee, but surprisingly good in practice. Useful as a fallback for very large queries where DP is infeasible.

#### Genetic Algorithms (GEQO in PostgreSQL)

PostgreSQL uses a genetic algorithm when the number of joined tables exceeds `geqo_threshold` (default 12):

- Represent join orders as permutations (chromosomes)
- Crossover: combine orderings from two good plans
- Mutation: swap random table positions
- Selection: keep plans with lowest cost

**Rationale**: `12! = 479,001,600` left-deep trees; DP on all subsets has `2^12 = 4096` entries for left-deep, but bushy search is `O(3^12 ~= 500K)` -- still feasible, but PostgreSQL is conservative.

### Cardinality Estimation and Join Performance

Join ordering decisions depend critically on cardinality estimates. Errors compound multiplicatively through a plan:

```
True cardinality:  |R JOIN S| = 1000,  |R JOIN S JOIN T| = 5000
Estimated:         |R JOIN S| = 100,   |R JOIN S JOIN T| = 50
                   (10x under)          (100x under)

Impact: Optimizer chooses nested loop join expecting 50 tuples,
        actually gets 5000. 100x slower.
```

**Key insight** from Leis et al., "How Good Are Query Optimizers, Really?", VLDB 2015: On the Join Order Benchmark (JOB), PostgreSQL's estimates are off by orders of magnitude for multi-way joins. The main sources of error:
1. Independence assumption (attribute values are uncorrelated)
2. Uniform distribution assumption
3. Containment assumption for joins

**Modern approaches**:
- **Learned cardinality estimation**: Use neural networks to predict cardinalities (e.g., MSCN, DeepDB, NeuroCard)
- **Adaptive re-optimization**: If runtime cardinality diverges from estimate, re-plan mid-execution (e.g., Spark AQE)
- **Robust plan selection**: Choose plans that perform well across a range of cardinality estimates (Babcock and Chaudhuri, "Towards a Robust Query Optimizer", VLDB 2005)

### Cost Models for Joins

A cost model assigns a numeric cost to each physical operator. Typical components:

```
Cost(HashJoin(R, S)) =
    C_cpu * (|R| + |S|)           // hash + compare
  + C_io  * (B(R) + B(S))        // read cost
  + C_mem * HT(R)                 // memory for hash table
  + C_net * transfer(R, S)        // network (distributed only)
```

**Calibration challenge**: The weights `C_cpu`, `C_io`, `C_mem`, `C_net` depend on hardware. PostgreSQL uses hand-tuned constants (`seq_page_cost = 1.0`, `random_page_cost = 4.0`, `cpu_tuple_cost = 0.01`). These are notoriously hard to get right, especially on cloud hardware with variable I/O latency.

### Join Enumeration Summary

```
Algorithm    | Complexity   | Trees          | Predicates    | Used By
-------------|-------------|----------------|---------------|------------------
DPsize       | O(3^n)      | Left-deep      | Binary        | System R (1979)
DPccp        | O(3^n)*     | Bushy          | Binary        | Research
DPhyp        | O(3^n)*     | Bushy          | Hypergraph    | MySQL, DuckDB, Umbra
GOO          | O(n^3)      | Greedy         | Any           | Fallback
GEQO         | O(gen*pop)  | Left-deep      | Any           | PostgreSQL (>=12 tbls)
Linearized DP| O(n*2^n)    | Left-deep only | Binary        | Classic textbook

* Much faster in practice due to connectivity pruning
```

---

## Performance Characteristics

### CPU Cache Effects

Modern CPUs have multi-level caches with dramatically different access times:

```
Level   | Size          | Latency     | Bandwidth
--------|---------------|-------------|------------
L1      | 32-48 KB      | 1-2 ns      | ~500 GB/s
L2      | 256 KB-1 MB   | 3-5 ns      | ~200 GB/s
L3      | 8-64 MB       | 10-20 ns    | ~100 GB/s
DRAM    | GBs           | 50-100 ns   | ~50 GB/s
NVMe    | TBs           | 10-100 us   | ~5 GB/s
```

**Impact on joins**:
- Hash table larger than L3: every probe is a cache miss (~100ns). For 100M tuples, that is 10 seconds of pure cache miss latency.
- Radix partitioning reduces each partition to L2 size: probes hit in ~5ns.
- **TLB misses**: Each TLB entry covers a 4KB page. A hash table spanning 1GB uses 256K pages. The TLB (typically 1K-2K entries) thrashes constantly. Radix partitioning with TLB-aware fan-out (< TLB entries per pass) eliminates this.

### NUMA Awareness

Non-Uniform Memory Access architectures have different costs for local vs. remote memory:

```
NUMA Node 0              NUMA Node 1
+-----------+            +-----------+
| CPU 0-7   |            | CPU 8-15  |
| L3 cache  | <--QPI-->  | L3 cache  |
| Local RAM |   ~100ns   | Local RAM |
| (~50ns)   |  penalty   | (~50ns)   |
+-----------+            +-----------+
```

**Join implications**:
- Hash table built on Node 0, probed from Node 1: every probe pays QPI penalty
- **Morsel-driven**: Assign morsels to threads local to the data's NUMA node
- **MPSM approach**: Sort locally, merge across nodes (read-only remote access is faster than write)
- **Replicated hash tables**: Each NUMA node builds its own copy. Doubles memory, eliminates remote accesses during probe.

### Memory vs. Disk Trade-offs

```
Scenario              | Best Algorithm              | Key Constraint
----------------------|-----------------------------|-------------------
R fits in memory      | Simple hash join             | HT(R) <= M
R fits in L3 cache    | Hash join (no partitioning)  | HT(R) <= L3
R exceeds memory      | Grace/Hybrid hash join       | B(R) <= M^2
R >> memory, sorted   | External sort-merge          | Always works
Both huge, no index   | Grace hash join              | Recursive partition
Both huge, indexed    | Index nested loop or merge   | Depends on selectivity
```

### Skew Handling

Data skew -- where a few join key values are extremely frequent -- breaks hash join partitioning assumptions.

#### The Problem

```
Key distribution:    key=42 appears 10M times, all other keys < 100 times
Hash partitioning:   partition containing key=42 has 10M tuples
                     all other partitions have ~1000 tuples
Result:              One partition dominates, one thread/node overloaded
```

#### Solutions

**1. Heavy Hitter Detection + Special Treatment**

```
Phase 1: Sample R to find keys with frequency > threshold
Phase 2: Heavy hitter tuples -> broadcast to all partitions
          Non-heavy-hitter tuples -> normal hash partition
Phase 3: Each partition joins normally, heavy hitter tuples
          matched against all partitions of the other side
```

**2. Frequency-Based Partitioning**

Assign heavy hitters to their own partitions, potentially splitting them across multiple partitions:

```
Key 42: 10M tuples -> split into 100 sub-partitions of 100K each
         Matching S tuples replicated to all 100 sub-partitions
```

**3. Spark AQE Skew Join**

Detects skew at runtime after the shuffle:
- Partition > 256MB AND > 5x median size -> skewed
- Split the large partition into smaller chunks
- Replicate the matching partition from the other side
- Join independently

**4. Asymmetric Partitioning**

Use different partition counts for build and probe sides. Build side gets more partitions for the skewed key range.

### Parallelism Models

```
Model               | Description                                | Granularity
--------------------|--------------------------------------------|------------------
Intra-operator      | Single operator uses multiple threads       | Within one join
Inter-operator      | Different operators run simultaneously      | Pipeline stages
Pipeline            | Producer feeds consumer without materializing| Between operators
Morsel-driven       | Work divided into cache-friendly morsels    | ~10K tuples/morsel
Exchange operator   | Explicit data redistribution between threads | Volcano-style
Bushy parallelism   | Independent sub-trees execute concurrently  | Plan-level
```

**Morsel-driven parallelism** (Leis, Boncz, Kemper, Neumann -- SIGMOD 2014):

```
    Global Hash Table (lock-free build)
    +----------------------------------+
    |  Shared hash table               |
    +----------------------------------+
        ^         ^         ^
        |         |         |
    Thread 1  Thread 2  Thread 3
    Morsel_a  Morsel_b  Morsel_c   (build phase: each thread
     of R      of R      of R      inserts its morsel)

    ---- barrier ----

    Thread 1  Thread 2  Thread 3
    Morsel_x  Morsel_y  Morsel_z   (probe phase: each thread
     of S      of S      of S      probes independently)
```

**Key advantage**: Perfect load balancing. If Thread 1 finishes its morsel early, it steals the next available morsel. No static partitioning needed.

---

## Correctness Concerns

### NULL Handling Semantics

NULL values require special treatment in every join type:

```
Join Type     | NULL = NULL match? | NULL in output?
--------------|--------------------|---------------------------
INNER JOIN    | No                 | No (NULLs filtered)
LEFT OUTER    | No                 | Yes (unmatched left rows)
RIGHT OUTER   | No                 | Yes (unmatched right rows)
FULL OUTER    | No                 | Yes (both sides)
SEMI JOIN     | No                 | No (existence check only)
ANTI JOIN     | No*                | Yes (non-matching rows)
CROSS JOIN    | N/A                | Yes (all combinations)
```

*Anti-join NULL handling is particularly tricky. `R ANTI JOIN S ON R.a = S.b` should NOT return R rows where `R.a IS NULL` if S contains any NULLs, because `NULL = NULL` is UNKNOWN, not FALSE. The SQL standard says anti-join returns rows with no match, and NULL comparisons never match. But `NOT IN (subquery)` with NULLs in the subquery returns empty! This is a notorious correctness pitfall.

```
-- This returns EMPTY SET if S.b contains any NULL:
SELECT * FROM R WHERE R.a NOT IN (SELECT S.b FROM S)

-- This returns rows correctly:
SELECT * FROM R WHERE NOT EXISTS (SELECT 1 FROM S WHERE R.a = S.b)
```

**Implementation**: Hash joins must handle NULL keys specially. Typically, NULL keys are excluded from the hash table and processed separately based on join type.

### Correctness Under Concurrent Modifications

For joins in MVCC systems:
- Both sides of the join must see a consistent snapshot
- If the build side is modified during probe, results may be incorrect
- **Solution**: Snapshot isolation ensures both sides see the same committed state

For streaming joins:
- Watermarks define completeness: "all events up to time T have arrived"
- Late-arriving tuples may miss their join partners
- **Solution**: Allowed lateness windows, retraction/correction messages

### Duplicate Handling

Joins must correctly handle duplicates in both relations:

```
R: {(1, a), (1, b)}     S: {(1, x), (1, y)}

R JOIN S on key = 1 produces:
{(1, a, x), (1, a, y), (1, b, x), (1, b, y)}   -- 4 rows, not 2
```

Hash join correctness: The hash table must store ALL tuples with the same key, not just one. Build phase must handle duplicate keys by chaining or using multi-match structures.

**The diamond problem** (Birler, Kemper, Neumann -- VLDB 2024): When intermediate join results are much larger than both input and output (because of duplicates that are later eliminated by a subsequent operation), performance suffers. Diamond-hardened joins split the operator into Lookup (find matches) and Expand (materialize matches), allowing the optimizer to defer expansion.

### Overflow and Spill-to-Disk Correctness

When a hash join's build side exceeds available memory:

**Correctness requirements for spilling**:
1. **Partition alignment**: If R's partition i spills to disk, S's partition i must also be written to disk (or the in-memory probe results are incomplete for partition i)
2. **No lost tuples**: Every tuple must end up in exactly one partition
3. **Hash function consistency**: The same hash function must be used for both build and probe partitioning

**DuckDB's approach** (Kuiper et al., "Saving Private Hash Join", VLDB 2025):
- Unified buffer pool manages temporary and persistent data
- Graceful degradation: as memory pressure increases, partitions are spilled incrementally
- No abrupt transition from "in-memory mode" to "external mode"
- Correctness maintained because buffer manager handles page eviction transparently

**Common bugs**:
- Forgetting to flush the output buffer after the last partition
- Not handling the case where a single partition still exceeds memory (requires recursive partitioning)
- Hash table resize during build invalidates pointers held by concurrent probes (in parallel builds)

### Distributed Join Correctness

**Network partition handling**:
- Shuffle join: if a node becomes unreachable, its partition is lost. Recovery requires re-reading source data and re-shuffling.
- Broadcast join: if broadcast is incomplete, some nodes have partial R. Must use reliable broadcast (e.g., Spark's BitTorrent-like broadcast).

**Straggler handling**:
- One slow node delays the entire join. Spark launches speculative tasks on other nodes.
- Skew detection (AQE) can re-balance work dynamically.

**Exactly-once semantics**:
- In streaming joins, replay of input may cause duplicate output. Must deduplicate or use idempotent consumers.
- Flink checkpoints operator state (hash tables) atomically. On recovery, replay from checkpoint.

---

## Production System Implementations

### DuckDB

**Architecture**: Vectorized push-based execution, single-node, larger-than-memory support.

**Join operators**:
- **Hash Join**: Default for equi-joins. Build side selected by optimizer (smaller relation). Uses a concise hash table with linear probing. Morsel-driven parallel build, partitioned parallel probe. Standard vector size of 2048 tuples per batch.
- **Perfect Hash Join**: For small, dense integer key ranges. Direct-indexed array where `index = key - min_value`. O(1) lookup, no hash collisions. Activated when `unique_keys == build_range` and no NULLs.
- **Cross Product**: For non-equi joins or theta joins without equi-component.
- **Nested Loop Join** (Index): For inequality predicates with range indexes.
- **IE Join**: Inequality join optimization using sorted permutation arrays. Based on the Inequality Joins paper (Khayyat et al., VLDB 2015).

**Recent advances**:
- **Saving Private Hash Join** (Kuiper et al., VLDB 2025): Graceful memory degradation. Hash join spills partitions to the unified buffer pool. Performance degrades linearly with memory pressure rather than cliff-diving.
- **Data Chunk Compaction** (Qiao et al., SIGMOD 2025): Logical compaction reduces chunk count in hash join output. 1.7x speedup on tested workloads.
- **Adaptive Factorization** (Gross et al., CIDR 2025): WCOJ-style factorized joins using linear-chained hash tables. Chain pointers instead of materialized results. 17.58x speedup with caching.

### HyPer / Umbra / CedarDB

**Architecture**: Compiled (data-centric code generation), morsel-driven parallelism.

**Join implementation**:
- JIT-compiled join operators: the entire pipeline (scan -> filter -> hash build/probe -> aggregate) is compiled into a single tight loop.
- Hash table: Concise hash table (count + prefix sum + dense array). Two-pass build.
- **Diamond-hardened joins** (Birler et al., VLDB 2024): Split join into Lookup + Expand sub-operators, freely reorderable. Eliminates the diamond problem for n:m joins.
- **Unchained hash table** (Birler et al., DaMoN 2024, Best Paper): Adjacency array layout + Bloom filter + software write-combine buffers + pipelined probes. 2x over open addressing on relational, 20x on graph queries.
- **Morsel-driven parallel join**: Global hash table with lock-free build. Workers process morsels of 10K tuples. NUMA-aware morsel assignment.

### Apache Spark

**Architecture**: Distributed, JVM-based, batch and streaming.

**Join strategies** (in order of optimizer preference):
1. **Broadcast Hash Join**: Small table broadcast to all executors, in-memory hash join. Threshold: `spark.sql.autoBroadcastJoinThreshold` = 10MB default.
2. **Shuffle Hash Join**: Both sides hash-partitioned, local hash join. Used when one side fits in memory per-partition.
3. **Sort-Merge Join**: Default for large equi-joins. Both sides shuffle-sorted, then merge.
4. **Broadcast Nested Loop Join**: Fallback for non-equi joins with small table.
5. **Cartesian Product**: Last resort for non-equi joins with no small table.

**Adaptive Query Execution (AQE)**:
- Converts sort-merge to broadcast join if runtime statistics show one side is small
- Converts sort-merge to shuffle hash join if per-partition data is small
- Skew join: splits skewed partitions, replicates matching partitions from other side
- Coalesces small post-shuffle partitions

### CockroachDB

**Architecture**: Distributed SQL, Raft-based replication, range-partitioned KV store.

**Join operators**:
- **Hash Join**: Vectorized column-at-a-time execution (40x faster than row-at-a-time). Build side chosen by optimizer. Spill to disk using temp storage if memory exceeded.
- **Merge Join**: Used when both sides ordered on join key (e.g., scans on the primary key).
- **Lookup Join**: Probe an index on the right side for each left tuple. Preferred for small left side with good index on right. The optimizer explicitly models the cost of round-trips to remote ranges.
- **Inverted Index Join**: For JSONB, array, and full-text search predicates.

**Distributed execution**:
- The optimizer is "distribution-aware": it considers data locality when choosing join strategies.
- Co-located joins: if both tables are partitioned on the join key with the same ranges, no shuffling needed.
- Cross-range joins: data shuffled via DistSQL processors.

### TiDB

**Architecture**: Distributed SQL, multi-Raft, TiKV storage (RocksDB-based).

**Join operators**:
- **Hash Join**: parallel build, parallel probe. Build side chosen by smaller estimated cardinality.
- **Index Join** (Lookup): outer relation scanned, inner relation probed via index. Batched lookups to amortize RPC overhead.
- **Merge Join**: for pre-sorted inputs or when result ordering is needed.
- **Index Merge Join**: combines index scan with merge join.

**Distributed specifics**:
- Coprocessor pushdown: part of the join may be pushed to TiKV nodes (filter + partial aggregation).
- MPP mode (TiFlash): columnar storage with hash-partitioned shuffle joins for OLAP.

### Snowflake

**Architecture**: Cloud-native, separated storage (S3/Azure Blob/GCS) and compute (virtual warehouses).

**Join execution**:
- **Hash-Hash Join**: Both sides hash-partitioned, distributed to compute nodes. Default for large-large joins.
- **Broadcast Join**: Small side replicated. Chosen when one side is below threshold.
- **Merge Join**: For sorted inputs.

**Optimizations**:
- **Dynamic partition pruning**: During join, Bloom filters from build side prune micro-partitions of probe side before scanning.
- **Adaptive join decisions** (2024): Runtime-adjusted join strategies. 9.5% average query speedup.
- **Result caching**: identical subqueries (including join results) cached and reused.

### ClickHouse

**Architecture**: Columnar, vectorized, OLAP-focused, single-node and distributed.

**Join algorithms** (user-selectable or auto-chosen):
1. **Hash Join**: Default. Build side loaded into memory hash table. Fast but memory-bound.
2. **Parallel Hash Join**: Right table split among threads, each builds independent hash table. Faster for large builds, more memory.
3. **Grace Hash Join** (since v22.12): Non-memory-bound hash join. Recursive partitioning with disk spill. No sorting needed.
4. **Full Sorting Merge Join**: Both sides fully sorted, then merged. Low memory, but sorting overhead.
5. **Partial Merge Join**: Right table fully sorted + indexed on disk. Left table sorted block-by-block in memory. Minimal memory usage, slow.
6. **Direct Join**: Specialized for Dictionary/Join table engines. O(1) lookup, no hash table build.

**Auto mode**: `join_algorithm = auto` tries hash join first; if memory limit exceeded, switches to Grace hash join on-the-fly.

### Apache DataFusion

**Architecture**: Rust-based, Arrow-native, vectorized, embeddable query engine.

**Join operators**:
- **HashJoinExec**: Standard hash join on Arrow RecordBatches. Build side determined by optimizer. Supports all join types (inner, left, right, full, semi, anti).
- **SortMergeJoinExec**: For large inputs where build side exceeds memory. Sorts both sides (can spill to disk), then merges. External sort enables arbitrarily large inputs.
- **NestedLoopJoinExec**: Rewritten in 2024 for 5x speed improvement and 99% memory reduction. Used for non-equi predicates.
- **CrossJoinExec**: Full Cartesian product.
- **SymmetricHashJoinExec**: For streaming/unbounded inputs. Maintains hash tables on both sides.

**Optimizations**:
- Dynamic filter pushdown from hash join build side into probe-side TableScan (partition pruning, row group pruning).
- Top-K pushdown through joins.

### Velox (Meta)

**Architecture**: C++ execution library, Arrow-compatible, used by Presto and others.

**Join operators**:
- **HashProbe + HashBuild**: Split into two operators connected by JoinBridge. HashBuild creates hash table; last-finishing parallel builder merges all tables. HashProbe lookups with VectorHashers.
- **MergeJoin**: Assumes sorted inputs. Uses MergeJoinSource to receive right-side data through a separate pipeline.
- **NestedLoopJoin**: For non-equi and cross joins.

**Key features**:
- **Dynamic filter pushdown**: VectorHashers contain full knowledge of build-side key values. Construct in-list filters for low-cardinality keys, push down to TableScan for file/row-group pruning.
- **Parallel hash build**: Multiple threads build independent hash tables; last finisher merges.
- **Spilling**: Hash join spills to disk when memory budget exceeded.

---

## Key Papers and References

### Foundational

1. **Selinger et al.** "Access Path Selection in a Relational Database Management System", SIGMOD 1979. -- Introduced cost-based optimization, interesting orderings, left-deep dynamic programming.

2. **Shapiro, L.D.** "Join Processing in Database Systems with Large Main Memories", ACM Computing Surveys, 1986. -- Classic survey of NLJ, SMJ, hash join variants.

3. **DeWitt, Katz, Olken, Shapiro, Stonebraker, Wood.** "Implementation Techniques for Main Memory Database Systems", SIGMOD 1984. -- Early main-memory join techniques.

### Hash Joins and Partitioning

4. **Kitsuregawa, Tanaka, Moto-oka.** "Application of Hash to Database Machine and Its Architecture", New Generation Computing, 1983. -- Introduced Grace hash join.

5. **DeWitt, Gerber.** "Multiprocessor Hash-Based Join Algorithms", VLDB 1985. -- Hybrid hash join.

6. **Shatdal, Kant, Naughton.** "Cache Conscious Algorithms for Relational Query Processing", VLDB 1994. -- First paper on cache-conscious join algorithms.

7. **Manegold, Boncz, Kersten.** "Optimizing Main-Memory Join on Modern Hardware", TKDE 2002. -- Radix-cluster join, hardware-conscious tuning.

8. **Kim, Sedlar, Chhugani et al.** "Sort vs. Hash Revisited: Fast Join Implementation on Modern Multi-Core CPUs", VLDB 2009. -- Comprehensive comparison of sort-based vs. hash-based joins on modern hardware.

9. **Blanas, Li, Hellerstein, Patel.** "Design and Evaluation of Main Memory Hash Join Algorithms for Multi-Core CPUs", SIGMOD 2011. -- No-partition, shared, independent, and radix hash join comparison.

10. **Balkesen, Alonso, Teubner, Ozsu.** "Multi-Core, Main-Memory Joins: Sort vs. Hash Revisited", VLDB 2013. -- Definitive experimental comparison. Radix join achieves ~200M tuples/sec. Hardware-conscious beats hardware-oblivious.

11. **Schuh, Chen, Dittrich.** "An Experimental Comparison of Thirteen Relational Equi-Joins in Main Memory", SIGMOD 2016. -- Most comprehensive experimental comparison of join algorithms.

### Sort-Merge Joins

12. **Albutiu, Kemper, Neumann.** "Massively Parallel Sort-Merge Joins in Main Memory Multi-Core Database Systems", VLDB 2012. -- MPSM: NUMA-aware sort-merge.

### Worst-Case Optimal Joins

13. **Atserias, Grohe, Marx.** "Size Bounds and Query Plans for Relational Joins", FOCS 2008 / SICOMP 2013. -- AGM bound: tight bound on maximum join output size.

14. **Ngo, Porat, Re, Rudra.** "Worst-Case Optimal Join Algorithms", PODS 2012. -- First worst-case optimal join algorithm (NPRR).

15. **Veldhuizen, T.L.** "Leapfrog Triejoin: A Simple, Worst-Case Optimal Join Algorithm", ICDT 2014. -- Practical WCOJ using sorted tries. Finer-grained optimality than NPRR.

16. **Ngo, Re, Rudra.** "Skew Strikes Back: New Developments in the Theory of Join Algorithms", SIGMOD Record 2013. -- Survey of WCOJ theory.

17. **Gross, ten Wolde, Boncz.** "Adaptive Factorization Using Linear-Chained Hash Tables", CIDR 2025. -- WCOJ in DuckDB with adaptive factorization. 17.58x speedup with caching.

### Adaptive Joins

18. **Avnur and Hellerstein.** "Eddies: Continuously Adaptive Query Processing", SIGMOD 2000. -- Adaptive tuple routing.

19. **Urhan and Franklin.** "XJoin: A Reactively-Scheduled Pipelined Join Operator", IEEE Data Engineering Bulletin, 2000. -- Symmetric hash join for streaming.

20. **Raman and Hellerstein.** "State Modules (SteMs) for Adaptive Query Processing", VLDB 2003.

21. **Bandle, Giceva, Neumann.** "To Partition, or Not to Partition, That Is the Join Question", SIGMOD 2021. -- Adaptive partitioning decisions for hash joins.

### Vectorization and SIMD

22. **Polychroniou, Raghavan, Ross.** "Rethinking SIMD Vectorization for In-Memory Databases", SIGMOD 2015. -- SIMD-based partitioning, probing, and join processing.

23. **Kersten, Leis, Kemper, Neumann, Pavlo.** "Everything You Always Wanted to Know About Compiled and Vectorized Queries But Were Afraid to Ask", VLDB 2018. -- Compiled (HyPer) vs. vectorized (DuckDB) execution comparison.

### Parallelism

24. **Leis, Boncz, Kemper, Neumann.** "Morsel-Driven Parallelism: A NUMA-Aware Query Evaluation Framework for the Many-Core Age", SIGMOD 2014. -- Morsel-driven work-stealing, NUMA-aware scheduling.

### Join Ordering and Optimization

25. **Moerkotte and Neumann.** "Analysis of Two Existing and One New Dynamic Programming Algorithm for the Generation of Optimal Bushy Join Trees Without Cross Products" (DPccp), VLDB 2006.

26. **Moerkotte and Neumann.** "Dynamic Programming Strikes Back" (DPhyp), SIGMOD 2008 (originally appeared at ICDE 2008). -- Hypergraph-aware join enumeration.

27. **Leis, Gubichev, Mirber, Olteanu, Kemper, Neumann.** "How Good Are Query Optimizers, Really?", VLDB 2015. -- JOB benchmark, cardinality estimation failures.

### Distributed Joins

28. **Okcan and Riedewald.** "Processing Theta-Joins using MapReduce", SIGMOD 2011. -- 1-Bucket-Theta algorithm for distributed non-equi joins.

29. **Blanas, Patel, Ercegovac, Rao, Shekita, Tian.** "A Comparison of Join Algorithms for Log Processing in MapReduce", SIGMOD 2010. -- Map-side, reduce-side, and broadcast joins in MapReduce.

### Recent Advances (2024-2025)

30. **Birler, Schmidt, Fent, Neumann.** "Simple, Efficient, and Robust Hash Tables for Join Processing", DaMoN 2024 (Best Paper). -- Unchained hash tables with adjacency array layout.

31. **Birler, Kemper, Neumann.** "Robust Join Processing with Diamond Hardened Joins", VLDB 2024. -- Lookup/Expand decomposition for n:m join optimization.

32. **Kuiper, Gross, Boncz, Muhleisen.** "Saving Private Hash Join", VLDB 2025. -- Graceful memory degradation for hash joins in DuckDB.

33. **Qiao, Huang, Zhang.** "Data Chunk Compaction in Vectorized Execution", SIGMOD 2025. -- Logical compaction for hash join output chunks.

### Inequality and Theta Joins

34. **Khayyat, Ilyas, Jindal, Madden, Ouzzani, Papotti, Quiane-Ruiz, Tang, Yin.** "Lightning Fast and Space Efficient Inequality Joins", VLDB 2015 (published 2016). -- IEJoin algorithm using sorted permutation arrays. Used in DuckDB.

### Surveys

35. **Ngo, Re, Rudra.** "Worst-Case Optimal Join Algorithms", PODS 2018 (tutorial). -- Comprehensive survey of WCOJ theory and practice.

36. **Graefe, G.** "Query Evaluation Techniques for Large Databases", ACM Computing Surveys 25(2), 1993. -- Encyclopedic reference on query processing including all classic join algorithms.

---

## Summary of Recommendations

### Choosing a Join Algorithm

```
Decision Tree:
=============

Is it an equi-join?
  |
  +-- Yes
  |    |
  |    Is one side very small (< 1% of other)?
  |    +-- Yes --> Index Nested Loop Join or Broadcast (distributed)
  |    +-- No
  |         |
  |         Do both sides fit in memory?
  |         +-- Yes --> Hash Join (radix-partitioned for large data)
  |         +-- No
  |              |
  |              Is one side sorted on join key?
  |              +-- Yes --> Sort-Merge Join
  |              +-- No --> Grace/Hybrid Hash Join
  |
  +-- No (theta join)
       |
       Is the predicate a range (inequality)?
       +-- Yes --> IEJoin, band join, or sort-based
       +-- No --> Block nested loop, or partitioned theta join
```

### For Multi-Way Joins (3+ tables)

- **Star/snowflake schema**: Semi-join reduction from fact table, then hash join dimension tables
- **Cyclic queries (triangles, cliques)**: Worst-case optimal join (LeapfrogTrieJoin)
- **Chain queries**: Left-deep hash join plan usually optimal
- **Complex join graphs**: Use DPhyp for enumeration, consider bushy plans

### For Distributed Settings

- **Small-large**: Broadcast the small side
- **Large-large, equi-join**: Shuffle (hash-partitioned) join
- **Co-partitioned**: Exploit co-location, avoid shuffle
- **Skewed**: Detect heavy hitters, split + replicate
- **Streaming**: Symmetric hash join with watermarks

---

## See Also

- [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) — Morsel-driven parallelism and JIT-compiled join execution in production
- [Data Structures](@/notes/programming/data_structures.md) — Hash tables, Bloom filters, and ART indexes used in join implementations
- [Arrow Format](@/notes/database/arrow_format.md) — Columnar layout enabling SIMD-vectorized join processing
- [Database Systems Survey](@/notes/database/database_systems.md) — Production systems (DuckDB, Spark, CockroachDB) implementing these join algorithms
- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — Consensus protocols underlying distributed join coordination in CockroachDB and TiDB

---

*Last updated: 2026-02-25*
