+++
title = "Cache Algorithms"
[taxonomies]
tags = ["cache", "eviction", "admission", "prefetch", "lru", "arc", "lirs", "tinylfu", "s3fifo", "sieve", "lrb", "lhd", "mrc", "buffer-pool"]
+++


# Cache Eviction, Admission, and Prefetching — Expert Reference

A comprehensive, depth-first reference for the cache replacement literature spanning
**1966 → 2025**. The goal is not breadth-only coverage but accurate, citation-grade
mechanism descriptions: data-structure layouts, invariants, pseudocode, complexity,
and where each algorithm is actually deployed.

The document is structured as:

```
0.  Mental model and decision framework
1.  Theoretical foundations (Belady, Mattson, Denning)
2.  Classical eviction (FIFO, LRU, CLOCK, SLRU, LFU)
3.  Recency-frequency hybrids (LRU-K, 2Q, MQ, ARC, CAR/CART, LIRS, CLOCK-Pro)
4.  TinyLFU family (TinyLFU, W-TinyLFU, Caffeine internals)
5.  Modern lazy-promotion family (FIFO-Reinsertion, S3-FIFO, SIEVE, QD-LP)
6.  Cost/size-aware (GreedyDual, GDSF, LFUDA, Hyperbolic, LHD)
7.  Flash-aware (CFLRU and family)
8.  Admission policies (Bloom doorkeeper, AdaptSize, FlashShield)
9.  Learned / ML caching (LeCaR, Cacheus, LRB, HALP, GL-Cache, 3L-Cache)
10. Buffer-manager-specific (WATT, PostgreSQL clock-sweep, MGLRU)
11. LLM KV-cache eviction (H2O, StreamingLLM, PagedAttention/vLLM)
12. Prefetching (sequential, stride, AMP, Mithril, Pythia)
13. Simulators (libCacheSim, Caffeine simulator, PyMimircache)
14. Standard traces and benchmarks
15. Evaluation methodology (MRC construction, SHARDS, Counter Stacks)
16. Decision matrix and recommendations
17. References
```

---

## 0. Mental model and decision framework

Every cache replacement policy is a heuristic approximation of Belady's
**MIN** (evict the item whose next reuse is furthest in the future). The
state-of-the-art research is essentially a 60-year history of attempts to
estimate "next reuse time" under different workload characteristics.

### Four orthogonal axes

A useful taxonomy decomposes any modern policy into four design axes:

```
                  ┌───── Signal ─────┐  ┌── Granularity ──┐  ┌── Action ──┐  ┌── Adaptivity ──┐
recency  ──┐      │ recency / age    │  │ object          │  │ promote    │  │ static         │
frequency ──┼──►  │ frequency        │  │ block / page    │  │ demote     │  │ workload-tuned │
size     ──┤      │ size             │  │ group / class   │  │ admit      │  │ online-learned │
cost     ──┤      │ cost             │  │ region / shard  │  │ evict      │  │ ML-mixture     │
ML feats ──┘      │ derived features │  └─────────────────┘  └────────────┘  └────────────────┘
                  └──────────────────┘
```

Most "new" algorithms are simply a particular point in this 4-D space.
For example:

- **LRU** = {recency, object, evict-LRU, static}
- **ARC** = {recency+freq, object, evict, workload-tuned}
- **W-TinyLFU** = {recency+freq sketch, object, admit+evict, hill-climbed}
- **S3-FIFO** = {ghost-confirmed freq, object, demote-fast+lazy-promote, static}
- **LHD** = {hit-density features, object, evict, online}
- **LRB** = {many features, object, evict, GBDT-learned}
- **GL-Cache** = {features, group, evict-batch, ML-ranked}

### Two design patterns from the 2023+ literature

Yang et al. (HotOS '23) crystallised two patterns that explain why the
modern lazy-promotion family (S3-FIFO, SIEVE, QD-LP) outperforms LRU:

- **Lazy Promotion (LP)**: do *not* promote objects on every hit. Only
  promote at eviction time. LRU's main inefficiency is that it pays a
  pointer-swap per hit, polluting front-of-list with one-hit wonders.
- **Quick Demotion (QD)**: most objects in skewed (Zipf-like) workloads are
  one-hit wonders. A small front-of-cache filter (FIFO with ghost entries)
  evicts them before they consume scarce capacity in the main region.

Modern algorithms are "FIFO + LP + QD" assemblies. They are simpler and
*faster* than LRU while having lower miss ratios.

### Workload taxonomy

| Class            | Pattern                                  | Best-fit policies                       |
|------------------|------------------------------------------|------------------------------------------|
| Zipfian (web)    | few hot keys, long tail of one-hit       | S3-FIFO, SIEVE, W-TinyLFU                |
| LRU-friendly     | recent past = best predictor             | LRU, CLOCK                               |
| LFU-friendly     | stable hot set                           | LFU, W-TinyLFU                           |
| Scan             | one-pass over much-larger-than-cache     | LIRS, ARC, CLOCK-Pro, MQ                 |
| Loop             | sequential reuse > cache                 | LIRS, MRU/anti-LRU at the right size     |
| Churn            | replacement signal weak (rolling)        | Cacheus, CR-LFU, LeCaR (mixed expert)    |
| Variable-size    | mix of small and large objects           | GDSF, AdaptSize, LHD, hyperbolic         |
| LLM KV-cache     | attention sparsity, sliding window       | H2O, StreamingLLM, PagedAttention        |

---

## 1. Theoretical foundations

### 1.1 Working set (Denning 1968)

**Paper**: Peter J. Denning, "The Working Set Model for Program Behavior",
CACM 11(5), 1968. (ACM Best Paper for Systems.)

The **working set** `W(t, τ)` is the set of pages referenced in the time
window `[t-τ, t]`. Denning proved that under a working-set replacement
policy a multiprogrammed memory cannot thrash and produces throughput
within 5–10% of optimal. The working-set principle remains the conceptual
ground truth for *why* recency-based caching works at all: the locality
principle says programs concentrate references over short windows.

### 1.2 Belady's OPT / MIN (1966)

**Paper**: L. A. Belady, "A Study of Replacement Algorithms for a
Virtual-Storage Computer", IBM Systems Journal 5(2), 1966.

```
OPT:  on miss, evict the page whose next reference is furthest in the future.
```

OPT is *optimal* (minimum number of misses on any reference string) and
infeasible online (requires future knowledge). It is the upper-bound oracle
in every empirical evaluation. Belady & Palermo 1974 proved equivalence
with Mattson's OPT (different formulations, same algorithm).

**Belady's anomaly**: FIFO can have *more* misses when given *more* cache.
Stack algorithms (LRU, OPT, LFU under certain assumptions) are immune
because the cache at size `N` is always a subset of the cache at size `N+1`
(the *inclusion property*).

### 1.3 BeladySize (variable-object-size optimal)

When objects have variable sizes (CDN/web caching), Belady is **not
optimal** anymore. The problem becomes NP-hard. Berger et al. (SIGMETRICS
2018, "Practical Bounds on Optimal Caching with Variable Object Sizes")
gave a practical bound. **OFMA** (Offline Min for variable sizes) — also
called "OPT-Size" — uses an LP relaxation. Used as oracle in CDN papers
(LRB, AdaptSize). Tooling: `dasebe/optimalwebcaching` on GitHub.

### 1.4 Mattson stack distance (1970)

**Paper**: R. L. Mattson, J. Gecsei, D. R. Slutz, I. L. Traiger,
"Evaluation Techniques for Storage Hierarchies", IBM Systems Journal 9(2),
1970.

**Stack distance** `d(r)` of a reference `r` is the number of *distinct*
items accessed since the previous reference to the same item (∞ on cold
miss). The **miss-ratio curve (MRC)** is `MR(c) = Pr[d ≥ c]`, plotted
against cache size `c`. For LRU specifically, `MR(c) = #(refs with
d ≥ c) / total`.

**Complexity**: naive stack simulation is O(N·M) time, O(M) space for N
references over M unique items. Modern approximations (SHARDS, Counter
Stacks) reduce this to sub-linear.

**Stack (inclusion) property**: a policy has the stack property iff for
every reference string and every cache size `c`, the cache contents at
size `c` are a subset of those at size `c+1`. LRU, LFU (with ties broken
consistently), and OPT satisfy it; FIFO and CLOCK do not.

---

## 2. Classical eviction

### 2.1 FIFO

**Mechanism**: a singly-linked queue of resident items. Insert at tail,
evict from head. Hits do nothing.

```
struct fifo {
    list<Item> queue;
    hashmap<Key, list::iterator> index;
};
op get(k):  return index.contains(k) ? index[k]->value : MISS
op put(k, v):
    if |queue| == capacity:  evict_head();
    queue.push_back(Item{k,v});
    index[k] = queue.tail();
```

- **Time**: O(1) per op.
- **Space**: O(c) pointers + values.
- **Property**: not a stack algorithm (suffers Belady's anomaly). Provides
  Quick Demotion intrinsically (oldest leaves first).
- **Surprise (Yang OSDI '20)**: on real Twitter cache traces, FIFO had
  lower miss ratio than LRU on a meaningful fraction of clusters. Plain
  FIFO is the foundation of the entire QD-LP family.
- **Production**: surprisingly common as last-resort policy; basis of S3-FIFO,
  SIEVE, etc.

### 2.2 LRU (Least Recently Used)

**Mechanism**: doubly-linked list + hash map. Hit ⇒ move-to-front. Miss
⇒ insert-at-front, drop-from-tail.

```
on hit(k):       move node(k) to head
on miss(k):
    if size == cap:  evict tail
    insert k at head
```

- **Time**: O(1) per op (assuming hash table).
- **Space**: O(c) plus 2 pointers per entry.
- **Properties**: stack algorithm (no Belady anomaly), recency-only, no
  scan resistance (single sequential scan flushes the cache).
- **Production**: textbook default. Pure LRU has been replaced by ARC,
  W-TinyLFU, or S3-FIFO in most modern caches due to scan vulnerability
  and lock contention (every hit takes a write lock).

### 2.3 CLOCK (second-chance)

**Paper**: F. J. Corbató, "A Paging Experiment with the Multics System",
MIT Project MAC Memo M-384, 1968.

**Mechanism**: a circular array of resident frames, each with a 1-bit
*reference* bit. A "hand" rotates clockwise. To find a victim, scan
forward: if ref-bit=1 clear it and advance; if ref-bit=0 evict. Hit just
sets ref-bit to 1.

```
hand = 0
on hit(k):       frame[idx(k)].ref = 1
on miss(k):
    while frames[hand].ref == 1:
        frames[hand].ref = 0
        hand = (hand + 1) % cap
    evict frames[hand]; install k at frames[hand]; hand = (hand+1) % cap
```

- **Time**: amortised O(1).
- **Space**: 1 ref-bit per frame.
- **Property**: approximates LRU with much lower overhead — no list
  pointer manipulation, no lock per hit. The dominant kernel
  page-replacement algorithm for decades. Foundation of CAR, CLOCK-Pro,
  PostgreSQL clock-sweep, etc.

### 2.4 SLRU — Segmented LRU

**Paper**: Karedla, Love, Wherry, "Caching Strategies to Improve Disk
System Performance", IEEE Computer 27(3), 1994.

**Mechanism**: two LRU lists — **probationary** and **protected** — with
a configurable split (often 20%/80%).

```
Probationary (newcomers and demoted)        Protected (proven hot)
[ tail -------------------- head ]    [ tail -------------------- head ]
                       │                                      │
new misses go to    head of prob              hit in prob promotes to head of
                                              protected (may demote prot tail
                                              back to prob head)
```

- **Time**: O(1).
- **Space**: O(c) plus list pointers.
- **Property**: scan-resistant — single pass through cold data leaves
  protected segment intact. On test workloads SLRU averaged 71.4% hit ratio
  vs LRU 67% / LFU 62% / FIFO 55.8% (Karedla et al. 1994). Used as the
  "main" region in W-TinyLFU.

### 2.5 LFU and LFU-Aging

**Mechanism**: each item carries an access counter. Evict minimum counter.
Two implementations:

- **Counter + min-heap**: O(log c) ops.
- **Frequency bucket lists** (Ketan Shah variant): O(1) — each frequency
  is a doubly-linked list of items at that frequency; a separate
  doubly-linked list of frequency nodes is kept sorted.

**Cache pollution problem**: an item with high historical count that is
no longer popular is impossible to evict. **LFU-Aging**: periodically
halve all counters, *or* use **LFUDA** (LFU with Dynamic Aging,
Arlitt, Cherkasova, Dilley, Friedrich, Jin, "Evaluating content
management techniques for web proxy caches", Performance Eval Review
2000):

```
on insert/reaccess:  counter = cache_age + freq
on eviction:         cache_age = max(cache_age, evicted.counter)
```

Cache age is monotone non-decreasing and bounded below by the minimum
counter, ensuring stale highly-counted items eventually age out. LFUDA is
a common baseline for byte hit ratio in CDN literature.

---

## 3. Recency–frequency hybrids

This section is the historical core of cache replacement research
(1993–2005). All algorithms here combine some form of recency tracking
with frequency tracking.

### 3.1 LRU-K (O'Neil, O'Neil, Weikum, SIGMOD 1993)

**Mechanism**: track the timestamps of the **last K accesses** to each
page. Predict next inter-arrival time by extrapolating from the Kth most
recent access (i.e. backward time window of size K). Evict the page with
the *oldest Kth-to-last reference*.

- LRU-1 ≡ LRU.
- LRU-2 is the practical choice — covers "second chance" semantics
  without the cost of K-deep history.

**Data structure**: per-page LRU-K queue plus retained-information
queue for non-resident pages (so a newly-promoted page can leverage
older access history). Includes a *correlation period* — references
within a short interval are treated as one access, to handle
back-to-back accesses by a single transaction.

- **Time**: O(log c) (priority queue keyed on Kth-last timestamp).
- **Space**: O(c · K) timestamps.
- **Property**: explicit recency *and* frequency: a page with two
  recent references gets a tighter inter-arrival estimate than one
  with one recent reference. Strongly outperforms LRU on database OLTP
  workloads. Drawbacks: log-time ops, parameter K and correlation
  period need tuning.

### 3.2 2Q (Johnson & Shasha, VLDB 1994)

Designed as an explicit O(1) approximation of LRU-2.

**Mechanism**: three structures:
- **A1in**: FIFO of recent first-time admits (size `Kin`, typically 25% of cache).
- **Am**: LRU of frequent pages (size = `cap - Kin`).
- **A1out**: FIFO of *ghost* (key-only) entries recently kicked out of
  A1in (size `Kout`, typically 50% of cache).

```
on hit(k):
    if k in Am:    move-to-MRU in Am
    elif k in A1in: do nothing                 # stay in FIFO order
    elif k in A1out:
        # ghost hit — second sighting, promote
        install k in Am MRU; drop k from A1out

on miss(k):
    if k in A1out:                              # not in cache but ghost-known
        reclaim victim; install k as MRU of Am; remove ghost
    else:
        if |A1in| == Kin:  victim = head(A1in); push victim.key into A1out
        else if cache full: victim = LRU of Am
        push k into A1in; remember victim
```

- **Time**: O(1) all operations.
- **Space**: `cap + Kout` keys (`Kout` are ghost-only, no data).
- **Property**: first-time arrivals do *not* enter Am — pure
  scan-resistance. A ghost-confirmed second access is required for hot
  promotion. Foundation pattern for ARC, S3-FIFO, etc.
- **Suggested tuning** (Johnson & Shasha): Kin = 25% of cache, Kout =
  50% (of value-bearing entries).
- **Production**: PostgreSQL pgvector recent revisions, several CDN
  prototypes, default in some JVM cache libraries.

### 3.3 MQ — Multi-Queue (Zhou, Philbin, Li, USENIX ATC 2001)

Designed specifically for **second-level** (storage-array) buffer caches,
where the access pattern is the miss stream from a first-level cache —
much less locality, longer reuse distances.

**Mechanism**: M LRU queues `Q0, Q1, ..., Q_{M-1}`, each with an
**expiration time**. An item in Q*i* whose access frequency reaches
`2^(i+1)` is promoted to Q*i+1*; if it sits in a queue past expiration it
is demoted to Q*i-1*. A separate **ghost** queue `Q_out` retains keys
and frequency for evicted items (so re-arrivals can resume their freq).

- **Time**: O(1) per op.
- **Space**: O(c + |Q_out|).
- **Property**: explicitly designed for long inter-reference times, so a
  page idle for a long stretch isn't immediately treated as "cold".
  Outperformed LRU, LRU-2, 2Q, MRU, ARC on second-level traces in the
  original paper (across all 7 alternatives).

### 3.4 ARC — Adaptive Replacement Cache (Megiddo & Modha, FAST 2003)

The seminal modern algorithm. IBM patent encumbrance forced ZFS to
implement it (then patent expired ~2020).

**Mechanism**: four LRU lists, total size 2c:

```
       L1 (recency)               L2 (frequency)
[ T1  | B1 ]               [ B2 |  T2  ]
  ↑ resident (T1+T2 = c)         ↑ ghost (B1+B2 ≤ c)

T1: items seen once recently      (resident)
T2: items seen ≥ 2 times          (resident)
B1: ghosts evicted from T1        (key only)
B2: ghosts evicted from T2        (key only)
p : target size for T1, adapts online (0 ≤ p ≤ c)
```

Adaptive parameter `p` is the *target* size of T1. Hits in B1 (a
ghost-confirmed recency item proved useful) increase `p`; hits in B2
decrease `p`.

```
on access(k):
    Case I  (T1 ∪ T2 hit):  move k to MRU of T2          # promote
    Case II (B1 ghost hit): p = min(p + δ1, c)            # favour recency
                            REPLACE; move k to MRU of T2
    Case III (B2 ghost hit): p = max(p - δ2, 0)           # favour frequency
                            REPLACE; move k to MRU of T2
    Case IV (true miss):
        if |L1| == c:    evict from T1 or B1 ; REPLACE
        else if |L1|+|L2| ≥ c: evict from B2; REPLACE
        install k in MRU of T1

REPLACE():
    if (|T1| ≥ 1 and (|T1| > p or (k ∈ B2 and |T1| == p))):
        demote LRU of T1 to MRU of B1; evict its data
    else:
        demote LRU of T2 to MRU of B2; evict its data

δ1 = max(|B2|/|B1|, 1)
δ2 = max(|B1|/|B2|, 1)
```

- **Time**: O(1) — every operation is a constant number of list moves.
- **Space**: 2c keys (half ghost), c values.
- **Properties**: self-tuning (no parameters), scan-resistant, no Belady
  anomaly. ARC dominates LRU on essentially every storage benchmark and
  by an order of magnitude on adversarial workloads.
- **Production**: ZFS (the ARC namesake), PostgreSQL has experimented
  with ARC, IBM DB2 patented the algorithm (US 6,996,676), many storage
  systems.

### 3.5 CAR and CART (Bansal & Modha, FAST 2004)

CAR addresses ARC's main weakness — every hit takes a global lock to
move an item to the MRU position.

**CAR (Clock with Adaptive Replacement)**: replaces ARC's T1 and T2 LRU
lists with two **CLOCK lists**, retaining B1 and B2 ghost LRU lists for
adaptation. Same `p` adaptation rule as ARC.

```
T1 = CLOCK list of recency items (1-bit ref per slot)
T2 = CLOCK list of frequency items (1-bit ref per slot)
B1 = LRU ghost (recency)
B2 = LRU ghost (frequency)

on hit:  just set ref-bit (no list movement, no lock)
on eviction: rotate hand on the appropriate clock until ref=0
```

**CART (CAR with Temporal filtering)**: addresses a CAR pathology where
short-burst correlated references (one transaction touching a page twice
in 100ms) wrongly promote pages to T2. Adds a temporal filter — an item
must be referenced *after a sufficient temporal gap* to migrate from T1
to T2. Achieved by a *long-term* bit alongside the *reference* bit.

- **Time**: O(1) amortised per op, **no lock per hit** — major scaling win.
- **Production**: heavily cited in storage research, less in mainstream
  use because by ~2010 the dominant alternative had become TinyLFU.

### 3.6 LIRS — Low Inter-Reference recency Set (Jiang & Zhang, SIGMETRICS 2002)

**Insight**: recency alone is a poor predictor; the **inter-reference
recency (IRR)** — number of *other distinct items* accessed between
two consecutive accesses to the same item — is a better one.

**Definitions**:
- **R (recency)** of an item: distinct items accessed since its last
  reference (current "distance" from MRU).
- **IRR**: R measured at the *previous* access.

**Block types**:
- **LIR (Low IRR)**: kept resident.
- **HIR (High IRR)**: may be resident or non-resident (key-only).

**Data structures**:
- **Stack S**: variable-size LRU-like stack containing *all* LIR blocks
  plus HIR blocks with R less than the maximum LIR recency. (Used for
  *recency comparison*; non-resident HIR ghosts also live here.)
- **List Q**: FIFO of *resident HIR* blocks (size = small budget,
  typically 1% of cache).

**Invariants**:
- Cache = `Llirs` resident LIR + `Lhirs` resident HIR blocks, where
  `Llirs + Lhirs = c` and `Lhirs` is small (e.g. 1% of c).
- The "bottom" of S is always a LIR block. When this changes due to S
  pruning, the algorithm performs a **stack pruning** — remove
  non-LIR blocks from the bottom of S until a LIR is exposed.

```
on access(k):
    case k is LIR:
        move k to top of S; if k was at bottom, prune S
    case k is HIR and in S:                 # ghost hit OR resident HIR in S
        change k to LIR
        move LIR at bottom of S to HIR (push to MRU of Q); prune S
        if k was resident HIR (in Q): remove from Q
        else: bring k into memory from disk
    case k is HIR and not in S:             # new or long-gone
        if cache full: evict LRU of Q (resident HIR)
        bring k into memory, add to Q tail, add to top of S as HIR
```

- **Time**: O(1) amortised (stack pruning is amortised constant).
- **Space**: cache + ghost entries in S.
- **Properties**: strong scan resistance (a one-pass scan is filtered by
  the HIR/Q region), loop resistance superior to ARC on loop sizes
  *just larger than cache* — LIRS's hallmark. Original paper showed LIRS
  beats LRU by 5–60% on multi-process traces.
- **Production**: H2 database, Apache Cassandra (historically), Caffeine
  simulator's reference policy, MySQL InnoDB midpoint-LRU borrows
  ideas from LIRS.
- **LIRS2** (Zhong, Tao, Jiang 2021): handles cases where LIRS's stack
  pruning loses important history — adds a configurable history
  retention region.
- **DLIRS** (Li, Wei, 2018, SYSTOR): adds ARC-style dynamic partitioning
  between cold/hot — performs close to whichever of LIRS/ARC is the
  winner on a given workload.

### 3.7 CLOCK-Pro (Jiang, Chen, Zhang, USENIX ATC 2005)

CLOCK-Pro is to LIRS what CAR is to ARC: a clock-based, lock-friendly
approximation of LIRS.

**Three hands** on a single circular list:

```
HAND_HOT   — finds hot pages, ages them down if not recently referenced
HAND_COLD  — finds cold pages to evict
HAND_TEST  — sweeps non-resident cold ghosts past their test period
```

Block states:
- **hot** (= LIR equivalent): always resident.
- **cold** (= HIR resident or non-resident).
- "**test period**": a cold page is on probation; if it is re-accessed
  *during* this period, it is promoted to hot (which forces a hot page
  somewhere in the ring to demote to cold).

```
on access(k):
    if resident:    ref-bit ← 1
    else:           # miss
        if k is cold-ghost in ring:
            promote: turn k hot; pick a hot to demote
        else:
            install k as cold-resident, ref-bit=0
        run HAND_COLD until a cold ref=0 is found, evict
        adjust HAND_HOT and HAND_TEST as needed
```

- **Time**: amortised O(1).
- **Space**: hot+cold resident + cold-ghost = roughly 2c slots.
- **Property**: same LIR/HIR scan and loop resistance as LIRS, but with
  CLOCK's hit-path simplicity. Adopted in the **NetBSD VM** and was the
  basis of Linux kernel patches in the late 2000s (FBSD followed similar
  ideas). The Linux mainline approach evolved into multi-generational
  LRU instead (see §10.3).

### 3.8 FRD — Filtering buffer cache (Park, Yeom, MSST 2017)

**Mechanism**: a "filter" of recently-seen items (small FIFO of ghosts) +
two LRU lists for filtered (one-touch) and re-referenced pages. The
filter prevents one-hit wonders from polluting the cache. Conceptually
similar to 2Q but uses a hit-frequency criterion and adapts the filter
size based on observed filter recall. Cited as a baseline in modern
papers (Cacheus, LeCaR).

---

## 4. TinyLFU family

### 4.1 TinyLFU (Einziger, Friedman, Manes, ACM ToS 13(4), 2017)

A pure **admission policy** plugged in front of any eviction policy
(usually SLRU). Decides at miss time whether the incoming object should
*replace* the current eviction victim.

**Data structures**:
- **Count-Min Sketch (CMS)** with 4-bit counters and W rows × D
  counters per row (default 4 hash functions).
- **Doorkeeper**: a plain Bloom filter that absorbs the first
  occurrence of each item. Only items past the doorkeeper increment the
  CMS. (Halves CMS update cost since 80%+ of web objects are one-hit.)
- **Aging mechanism**: when the total sample size reaches `W` (sampling
  window), halve all CMS counters and reset the doorkeeper. This is the
  *fresh-CMS* trick that bounds memory while letting frequencies decay.

```
estimate_freq(k):
    f_main = min over rows of CMS[row][hash_row(k)]
    f_door = 1 if k in doorkeeper else 0
    return f_main + f_door

on access(k):
    if k in doorkeeper:           CMS_increment(k)
    else:                         doorkeeper.add(k)
    sample_count += 1
    if sample_count == W:         CMS *= 0.5; doorkeeper.reset()

admit(new, victim):
    return estimate_freq(new) > estimate_freq(victim)
```

- **Space**: bits/object ≈ 8 (4-bit counters × ≥2 cache-size's worth of
  sketch entries to maintain accuracy). Empirically Caffeine uses 8 bytes
  per cache entry total.
- **Properties**: provably bounded error (CMS guarantee), strong
  frequency awareness even with tiny memory. Original TinyLFU + LRU
  beat ARC on web/CDN traces.
- **Production**: in front of W-TinyLFU below.

### 4.2 W-TinyLFU (window TinyLFU) — Caffeine's policy

The eviction policy in Ben Manes's **Caffeine** library (and by transitive
adoption: Cassandra, HBase, Kafka, Solr, Neo4j, Druid, Apache Beam …).

**Architecture**:

```
                              admission decision (TinyLFU)
       ┌─ Window LRU ─┐   ┌────►────┐
miss → │ ~1% capacity │ → │compare? │── admitted → Probationary segment (SLRU)
       │ LRU eviction │   │  freq   │
       └──────────────┘   └────►────┘
                          rejected
                                                     SLRU main (~99%):
                                                     - probationary (~20% of main)
                                                     - protected    (~80% of main)
```

- **Window**: small admission LRU absorbs burstiness.
- **TinyLFU admission**: an item evicted from window is admitted to
  the main region only if its CMS frequency exceeds the frequency of
  the current SLRU LRU victim.
- **Main SLRU**: protected (proven hot) + probationary (newcomers).
- **Hill climbing**: the **window:main split** is adaptive. Hit ratio
  is sampled periodically; the window grows/shrinks in the direction of
  the last beneficial step; step size decays toward convergence.

```
hit_rate_t = recorded; if Δhit > 0, repeat last move; else reverse and shrink step
```

- **Performance**: matches or beats ARC, LIRS, LFU on the standard ARC
  traces; within 99% of Belady on real-world workloads.
- **Memory**: 8 bytes/entry (4-bit CMS × ~10 counters per entry plus 1
  bit-each in doorkeeper).
- **Production**: see above; the dominant Java-ecosystem cache.

---

## 5. Modern lazy-promotion family (2023+)

The CMU PDL group, led by Juncheng Yang and Yazhuo Zhang, demolished much
of the LRU-family conventional wisdom with three closely-related papers.

### 5.1 FIFO-Reinsertion and the QD-LP design pattern (HotOS '23)

**Paper**: Yang, Qiu, Zhang, Yue, Rashmi, "FIFO can be Better than LRU:
the Power of Lazy Promotion and Quick Demotion", HotOS 2023.

**Surprising empirical finding**: on Twitter's 153 production cache
clusters, plain FIFO had the *lowest* miss ratio on a large fraction of
clusters; CLOCK (FIFO with reference bits) frequently outperformed LRU.

**Reasons**:
1. **Lazy Promotion (LP)**: LRU promotes every hit ⇒ pollutes head with
   one-hit wonders that were briefly accessed; FIFO promotes nothing,
   so no pollution.
2. **Quick Demotion (QD)**: FIFO automatically evicts oldest first; LRU
   gives every item a "second chance" through the entire stack.

**QD-LP-FIFO** prescription:
- Cache = `S` (small FIFO, ~5–10%) + `M` (large FIFO with reinsertion).
- Ghost FIFO `G` of size |M| records recently-evicted-from-M keys.
- New items: insert at S head.
- S overflow: if item was *also referenced* while in S → demote to M
  with reference bit cleared; else drop and put a ghost in G.
- M overflow: read reference bit. If 1 → clear and *reinsert at head*
  (lazy promotion). If 0 → evict; if its key is in G it gets *re-promoted*
  to M instead.

QD-LP-FIFO requires at most 1 metadata update per hit and **no lock at
all** on the hit path. Reduces miss ratios of LIRS by 1.6% and LeCaR
by 4.3% on average across CloudPhysics, MSR, FIU, Twitter traces.

### 5.2 S3-FIFO (Yang, Zhang, Yue, Rashmi, SOSP 2023)

The production-grade member of the QD-LP family.

**Three FIFO queues**:

```
┌─ S (Small) ──┐    ┌──────── M (Main) ────────┐
│  10% of c    │    │       90% of c           │
│   FIFO       │    │  FIFO + reinsertion      │
└──────────────┘    └──────────────────────────┘
                                                 
┌─ G (Ghost) ──┐
│  ≈ |M| keys, │
│  no data     │
└──────────────┘
```

**Per-object** state: 2-bit access counter (saturating, max value 3).

```
INSERT(k):
    if k.key was in G:  install k into M (head), counter=0
    else:               install k into S (head), counter=0

ACCESS hit(k):          counter = min(counter+1, 3)         # 2 bits only

EVICT (when cache full):
    while |S| > size_S:
        x = tail(S)
        if x.counter > 0:                        # earned its keep
            re-insert x into M (head); x.counter = 0
        else:                                    # one-hit-wonder
            remove x from S; add ghost(x.key) to G; drop oldest ghost if |G| > |M|
    while |M| > size_M:
        x = tail(M)
        if x.counter > 0:
            x.counter -= 1; reinsert x at head(M)   # lazy promotion
        else:
            evict x  ; add ghost(x.key) to G; drop oldest ghost if |G| > |M|
```

- **Time**: O(1) per op; **lock-free reads** by design — the hit path is
  a single atomic counter increment.
- **Space**: 2 bits per object + ghost (key-only) entries.
- **Properties**: Quick Demotion via S; Lazy Promotion via M counter.
  No need for ghost on S — that role is filled by G.
- **Empirical**: across 6594 production traces from 14 datasets, S3-FIFO
  had the best mean miss ratio on **10/14 datasets**, and at 16 threads
  delivered **6× throughput** vs an optimised LRU.
- **Production**: implemented in CacheLib (experimental), foyer-rs (Rust),
  go-tinylfu/sieve forks, libCacheSim, multiple Python crates.

### 5.3 SIEVE (Zhang, Yang, Yue, Vigfusson, Rashmi, NSDI 2024)

**SIEVE** is the simplest of the family — *one* doubly-linked list, *one*
pointer, *one* visited bit per item. No ghost queue, no admission, no
parameters. Despite this, it beats W-TinyLFU on most web workloads.

**Data structures**:
- A doubly-linked list `L` of resident objects in **insertion order**.
- A single pointer `hand` into the list.
- 1 bit `visited` per object.

```
get(k):
    if k in cache:  k.visited = 1; return HIT
    else:           insert_and_maybe_evict(k); return MISS

insert_and_maybe_evict(k):
    if cache full:  evict()
    push k to HEAD of L with visited=0; hash[k] = node

evict():
    while hand.visited == 1:
        hand.visited = 0          # second chance
        hand = hand.prev or TAIL  # move toward older
    victim = hand
    hand = hand.prev or TAIL
    unlink victim; remove from hash
```

Key contrast with CLOCK: hand moves from TAIL → HEAD (old to new). When
an item is touched, it **stays in its current position** in L — no
move-to-front, no list mutation on hit. Only the bit changes, atomically.

- **Time**: O(1) amortised; hit path is *one atomic bit set*.
- **Space**: 1 bit + list pointers per object.
- **Properties**: provides BOTH quick demotion (newly-inserted objects
  reach the hand soon since hand sweeps low-end) AND lazy promotion (a
  popular object retains its high position until visited bit is cleared).
- **Empirical**: in 5470 web traces, SIEVE has lower miss ratio than
  LRU/CLOCK/W-TinyLFU on more than 45% of traces; reduces FIFO miss
  ratio by 42% on the worst 10%. At 16 threads, 2× the throughput of
  optimised LRU.
- **Limitations** (per authors): SIEVE is **not a stack algorithm**.
  Cache contents at size *N* are not strictly a subset of those at size
  *N+1* — possible Belady-style anomalies (rare in practice).
- **Production adoption** (within ~12 months of publication):
  - **Rust**: `sieve-cache` crate (jedisct1), used by various services.
  - **Go**: `opencoff/go-sieve`, `scalalang2/go-sieve-cache` — 90–300×
    faster than `hashicorp/golang-lru` on read-heavy concurrent loads.
  - **Python**, **JavaScript**, **C++** community ports.
  - Five production cache libraries adopted SIEVE within a year, each
    with <20 lines of code change.

### 5.4 QD-LP design pattern as a building block

The HotOS '23 paper explicitly positions QD-LP as a **LEGO-block design
pattern** to be layered on top of any base policy. Modern algorithms can
all be re-derived in this framework:

| Algorithm | Base | LP | QD |
|-----------|------|----|----|
| LRU       | --   | always promote to MRU | none |
| CLOCK     | FIFO | flip ref bit          | none |
| 2Q        | LRU  | hit in Am → MRU       | A1in FIFO |
| ARC       | LRU  | second access → T2    | none |
| W-TinyLFU | SLRU | promote prob→prot     | Window LRU |
| S3-FIFO   | FIFO | M counter reinsertion | S queue + G ghost |
| SIEVE     | FIFO | visited bit set on hit| hand sweeps from old end |

---

## 6. Cost- and size-aware eviction

When items have **variable cost** (latency to refetch) or **variable
size** (CDN: 100B JSON vs 100MB video), pure recency/frequency policies
are suboptimal. The classical solutions all derive from Young's
**GreedyDual** (1991).

### 6.1 GreedyDual / Landlord (Young, 1991)

**Paper**: Neal E. Young, "Online caching as cache size decreases",
SODA 1991 — and the journal versions "On-line caching as cache size
decreases" / "On-line file caching".

**Mechanism**: each item `i` has a *credit* `H(i)`. On admission:
`H(i) = cost(i)`. To evict: pick the item with **minimum H**. Subtract
the minimum H from *every* item's H (representing "rent" paid). On a
hit, reset `H(i) = cost(i)`.

Bookkeeping trick: maintain a global "rent" counter `L`. Internally store
`H'(i) = H(i) + L`. Then "subtracting min H from everyone" becomes "set
L = L + min H' (and the new minimum becomes 0 by definition)". O(log c)
with a priority queue keyed on `H'`.

```
on miss(k):
    while size_used + size(k) > C:
        v = argmin_i H'(i)
        L = H'(v); evict v
    H'(k) = cost(k) + L; admit k
on hit(k):
    H'(k) = cost(k) + L            # full credit restored
```

- **Time**: O(log c) per op.
- **Property**: provably **k/(k − h + 1)-competitive** for weighted
  caching, matching LRU's optimal bound. Generalises LRU (cost = 1) and
  FIFO (when cost is set on admission only).

### 6.2 GD-Size (Cao & Irani, 1997)

`H(i) = cost(i) / size(i)`. Optimises *byte hit ratio* — important for
proxy/CDN economics.

### 6.3 GDSF — Greedy-Dual-Size-Frequency (Cherkasova, HP TR 1998)

**Formula**:
```
H(i) = freq(i) · cost(i) / size(i)  +  L
```

Where `L` is the global aging counter as in GreedyDual. `freq(i)` is the
running access count of `i`. On every access, the formula is recomputed
with the new freq.

- **Property**: tri-balanced among popularity, retrieval cost, and size.
  Long-time champion among web-proxy strategies and the basis of many CDN
  policies. Outperforms LRU/LFU/GDS on both object hit ratio and byte
  hit ratio on web traces.
- **Production**: Squid web proxy (configurable), RIPQ on Facebook's
  flash-photo CDN uses GDSF as one of its first-class policies.

### 6.4 Hyperbolic Caching (Blankstein, Sen, Freedman, USENIX ATC 2017)

**Insight**: traditional priority-queue caches couple the
*priority function* to the *data structure*. Hyperbolic decouples them.
Eviction uses **random sampling** (like Redis): sample `n` items,
compute each item's *current* priority, evict the worst.

**Priority function**:
```
priority(i) = n_i / (t − t_i^enter)
```
where `n_i` is the access count and `t_i^enter` is the time `i` entered
the cache. The denominator grows with age, so the per-second access
rate is the priority. **Hyperbolic** because the cumulative hit rate
curve as a function of time spent in cache forms a hyperbola.

Easy to extend:
- Add cost: `priority = n_i · c_i / (t − t_i^enter)`.
- Add staleness: `priority *= (1 − e^{−(t−t_i^stale)/τ})`.
- Use any application-specific feature.

- **Time**: O(n) per eviction where `n` is sample size (small, e.g. 5-20).
- **Property**: no global lock, no priority-queue maintenance, trivially
  extensible to arbitrary cost/value functions. Inspired by Redis's
  approximate-LRU random-sampling design.
- **Production**: implemented in Redis variants, several academic CDNs,
  Postgres-compatible caching plugins.

### 6.5 LHD — Least Hit Density (Beckmann, Chen, Cidon, NSDI 2018)

A statistically rigorous reformulation. Object's value:

```
hit_density(i) = Pr[next access happens within remaining cache lifetime]
                 / expected size · lifetime
```

LHD computes this from conditional probabilities derived from **observed
reuse-time distribution** binned by class (`class` = object features
like size, type, frequency bucket).

**Mechanism**:
- Maintain a histogram of reuse times per class.
- For each resident object, compute the conditional probability of
  next-access-before-eviction given its current age and class.
- Sample `n` items at eviction, evict minimum `hit_density`.

Adaptation: the histograms decay over time; classes auto-discover.

- **Time**: O(n) eviction via sampling.
- **Space**: O(|classes| · |reuse-time-bins|), tiny.
- **Properties**: provably optimises hit density (rate of hits per byte
  of cache occupied); handles variable sizes natively; adapts online.
- **Empirical**: in the original paper LHD reduces tail latency and
  improves hit ratio over LRU/Hyperbolic/LRU-K on CDN and key-value
  traces.

---

## 7. Flash-aware eviction

NAND flash and 3D XPoint have **asymmetric** read/write costs and limited
endurance. Replacement policies that ignore dirty-page write-back cost
under-perform.

### 7.1 CFLRU — Clean-First LRU (Park, Jung, Kang, Kim, Lee, CASES 2006)

**Mechanism**: split LRU into a **working region** (front W%) and a
**clean-first region** (back). When evicting, prefer to drop **clean**
pages from the clean-first region first (a clean drop has no write
cost). Only if no clean page exists in the clean-first region, fall
back to LRU eviction.

```
LRU list:  [ MRU ────── working ────── │ ── clean-first ── LRU ]
evict():
    scan clean-first region from LRU toward MRU
    if a clean page found → evict it
    else → evict LRU
```

- **Result** (paper): 28.4% reduction in swap replacement cost vs LRU.
- **Variants**:
  - **LRU-WSR** (Write-Sequence-Reordering): a single LRU + a "cold"
    flag on dirty pages so they get a second chance.
  - **CCF-LRU**: also flags cold-clean pages.
  - **FAB**: flash-aware buffering at block granularity.
- **Production**: filesystem/page-cache patches for flash SSDs, embedded
  Linux configurations.

---

## 8. Admission policies

Admission policies precede eviction in the cache decision pipeline: at
miss time, "should this object even be cached?" Important because:
- One-hit wonders waste capacity.
- Very large objects can evict many smaller useful objects.
- Some objects have low refetch cost (cheap to redownload).

### 8.1 Bloom-filter / 2-bit doorkeeper admission

The simplest production admission: a Bloom filter records what has been
*seen* before. On miss, admit only if the key is already in the BF (i.e.
this is its *second* sighting). Periodically reset the BF.

- Used in Facebook **CacheLib** (default for AC engine), TinyLFU.
- Filters > 80% of one-hit wonders for typical Zipfian web workloads.

### 8.2 TinyLFU admission (see §4.1)

Compare incoming-object frequency estimate vs eviction victim's. Admit
only if the new item is *more frequent* than what would be evicted.

### 8.3 AdaptSize (Berger, Sitaraman, Harchol-Balter, NSDI 2017)

**Problem**: in CDN HOCs (Hot Object Caches), small objects far
outnumber large ones, but large objects can dominate total bytes. A pure
LRU lets a single 100MB object evict 10,000 small files. The classic
solution is **size-threshold admission**: admit only if `size ≤ T`. But
the optimal `T` varies with workload and must be adapted online.

**Mechanism**:
- Continuously fit a **Markov-chain model** of the cache as a function
  of `T`, parameterised by observed size and popularity distributions
  from recent traffic.
- Use the model to compute predicted OHR for many `T` values.
- Hill-climb `T` toward optimum every reconfiguration period.

```
every Δt seconds:
    snapshot recent request size and popularity dist
    for T in candidate set:
        OHR_pred[T] = run Markov chain on model
    T = argmax OHR_pred
```

- **Empirical**: 30–48% higher OHR than Nginx, 47–91% higher than
  Varnish on a large CDN trace.
- **Production**: deployed at Akamai (per paper).

### 8.4 Lightweight Robust Size Aware (Einziger, Friedman, Kassner, ACM ToS 2022)

A simpler, parameter-free alternative to AdaptSize: probabilistic
admission weighted by `1/size` (large objects less likely to enter).
Combined with TinyLFU for popularity. Robust to changing size
distributions without explicit model fitting.

### 8.5 FlashShield / SSD-aware admission

In disk-backed flash caches, write amplification destroys SSD lifespan.
**Admission policies** for flash:
- **Probabilistic gate**: admit at rate `p` to throttle writes.
- **Frequency gate**: admit only if seen ≥ 2 times in the last window.
- **Size gate**: admit only objects in a sweet-spot size range
  (very small or very large skip flash).
- **Production**: CacheLib's small-object cache uses doorkeeper +
  size + probabilistic admission tiers.

---

## 9. Learned / ML-based caching

The last decade has seen ML-derived policies replace hand-tuned
heuristics, especially for CDN workloads.

### 9.1 LeCaR (Vietri, Rodriguez, Cheng, Mukherjee, Ge, Lyons, Liu, Narasimhan,
HotStorage 2018)

**Reinforcement-learning meta-policy** mixing two base experts: **LRU**
and **LFU**.

- Maintains two weights `(w_LRU, w_LFU)` over the experts.
- On every cache miss, *sample* an expert with probability proportional
  to its weight. Use that expert's victim.
- Track *regret*: if the evicted item later comes back as a miss, the
  expert that chose it gets penalised.
- Update weights via the **multiplicative weights / Hedge** algorithm.

```
w_LRU = 0.5; w_LFU = 0.5
on miss:
    sample expert e ∈ {LRU, LFU} with prob proportional to (w_LRU, w_LFU)
    victim = e.pick()
    push victim into ghost FIFO (key + which expert evicted it)
on later miss of a ghost:
    blame expert e_blamed; w_{e_blamed} *= exp(-η · regret)
    renormalise
```

- **Empirical**: outperforms ARC by up to 18× on small caches relative
  to working set.
- **Variant**: **OLeCaR** extends to N experts (ARC, LIRS, LFU, …).

### 9.2 Cacheus (Rodriguez, Yusuf, Lyons, Paz, Rangaswami, Liu, Zhao,
Narasimhan, FAST 2021)

Improves LeCaR with:
- **Self-tuning hyperparameters**: learning rate and discount rate
  computed from observed reuse patterns; no static tuning.
- **Mixture of four experts** tuned for four workload primitive types:
  LFU-friendly, LRU-friendly, scan, churn.
- New experts: **SR-LRU** (Scan-Resistant LRU) and **CR-LFU**
  (Churn-Resistant LFU).
- Classifies the workload's current primitive composition and
  re-weights experts continually.

Performance: more robust than LeCaR across diverse traces (CloudPhysics,
MSR, FIU, web).

### 9.3 LRB — Learning Relaxed Belady (Song, Berger, Li, Lloyd, NSDI 2020)

The first practical learned-CDN-cache paper, deployed within Apache
Traffic Server.

**Key concepts**:
- **Relaxed Belady boundary**: rather than predicting *exact* next-reuse
  time (a hard regression), predict whether an object's next access is
  beyond some boundary `D` (a much easier binary-ish decision).
- **Good decision ratio**: a new metric for how often the learner's
  eviction choice matches Belady's choice (correlates with hit ratio
  better than per-prediction accuracy).

**Mechanism**:
- Per-object features: object size, access frequency, time since each
  of the last *N* accesses (the "delta" vector), edge-server-specific
  derived features.
- **Gradient-boosted decision tree (LightGBM)** trained offline on a
  trace, then continually fine-tuned online.
- On eviction: sample ~64 candidates, score each with the model, evict
  highest predicted next-access time.

- **Empirical**: 4–25% WAN-traffic reduction over a production CDN
  cache on 6 traces; outperforms LeCaR, LRU-K, AdaptSize.
- **Overhead**: in-prototype CPU overhead ~10%; can be deployed on
  today's CDN servers.

### 9.4 HALP — Heuristic-Aided Learned Preference (Song, Berger, Lloyd, NSDI 2023)

Deployed in **YouTube CDN DRAM tier** since early 2022.

**Architecture**:
- A heuristic baseline (e.g. SLRU) computes a candidate set of K eviction
  victims.
- A small neural network ranks these K via a *preference-learning*
  objective (which-pair-is-better, not absolute regression).
- Feedback is automated: when a victim is later requested, the model
  gets a corrective gradient on the pair.

- **Empirical**: 9.1% reduction in peak byte-miss, only 1.8% CPU
  overhead.
- **Production**: YouTube CDN.

### 9.5 GL-Cache — Group-Level learning (Yang, Mao, Yue, Rashmi, FAST 2023)

**Insight**: object-level learning (LRB, HALP) has prohibitive overhead
on millions of objects. GL-Cache *clusters* similar objects into groups
and learns at the group level — orders-of-magnitude cheaper.

**Mechanism**:
- Partition cache into groups (e.g. by size bucket, content type,
  insertion-time epoch). Each group is itself a FIFO.
- Features (per group): write rate, miss rate, request rate, mean object
  size, last-access age.
- Train a simple regressor (linear or shallow XGBoost) **once per day**
  on a sampled trace.
- Inference: rank all groups by predicted utility; evict from lowest
  group(s); reuse a single inference for many evictions.

- **Empirical**: vs the best learned cache (LRB), GL-Cache is **64%
  higher throughput**, **3% higher mean hit ratio**, **13% higher P90**.
- Training: 10–50 ms of one CPU core per day.

### 9.6 3L-Cache — Low-overhead Learning (Zhou et al., FAST 2025)

The most recent advance. Object-level learning at **almost group-level
cost**.

**Three innovations**:
1. **Sampling**: record at most one object per request as training data
   (constant per-request overhead).
2. **Bidirectional eviction sampling**: at eviction, sample candidates
   biased toward *unpopular* objects (since most evictions hit the
   tail).
3. **Adaptive retraining frequency**: detect drift via a low-cost
   metric; retrain only when needed.

- **Empirical**: 60.9% lower CPU than HALP, 94.9% lower than LRB, while
  achieving the lowest miss ratio among learned policies on benchmark
  traces.
- **Affiliation**: Beijing University of Technology + Microsoft Research.

---

## 10. Buffer-manager-specific designs

Buffer pools differ from object caches in three ways:
- Fixed page size (no variable-size complication).
- Throughput is dominated by lock contention on hot pages.
- Persistence: dirty pages must be written before eviction, adding
  cost asymmetry.

### 10.1 PostgreSQL clock-sweep + buffer ring

PostgreSQL's `shared_buffers` uses CLOCK with a 3-bit (formerly 2-bit)
**usage_count** per buffer:

```
on access(buf):    buf.usage_count = min(buf.usage_count + 1, 5)
on sweep(buf):     if pinned: skip
                   else if buf.usage_count > 0: buf.usage_count--; advance
                   else: this is the victim

nextVictimBuffer  — global rotating index
```

**Buffer ring** strategy for bulk operations (sequential scans,
COPY, VACUUM): use a small private ring of 256KB–16MB so a single
large scan does not evict the entire shared cache. Detected
automatically when accessing > 1/4 of shared_buffers.

- **Property**: pure CLOCK with no ghost queue or admission filter.
  Recognised in the PG community as suboptimal for mixed OLTP/scan
  workloads — a recurring proposal is to adopt CAR/CART or W-TinyLFU.

### 10.2 WATT — Write-Aware Timestamp Tracking (Vöhringer & Leis, VLDB 2023)

**Paper**: Demian E. Vöhringer, Viktor Leis, "Write-Aware Timestamp
Tracking: Effective and Efficient Page Replacement for Modern Hardware",
PVLDB 16(11), 2023.

Designed for the **LeanStore** buffer manager (NVMe SSD storage engine).
Addresses the gap that classical replacement algorithms (CLOCK, second
chance) provide poor hit ratios for modern NVMe workloads, while
sophisticated policies (LRU-K, ARC, LIRS) have CPU overhead too high
for in-memory hot paths.

**Mechanism**:
- **Per-page timestamp log**: rather than store a single last-access
  time, WATT keeps the last K (default 4–8) reference timestamps per
  page in a small ring buffer **inserted cyclically** — no shifting,
  no list manipulation on hit. Per-hit cost ≈ one timestamp store.
- **Page value function**: combines recency (last timestamp) and
  frequency (interval between recent timestamps) into a scalar
  "page value". The exact form is a weighted geometric average of
  recent inter-arrival times.
- **Sample-based eviction**: sample ~8 random page descriptors, compute
  page value for each, evict the minimum.
- **Write-awareness**: dirty pages' page values get a penalty so they
  are eviction targets later (saving a write-back).

- **Empirical**: matches LRU-K hit ratio at CLOCK-level CPU cost; up to
  2× throughput vs CLOCK on TPC-C and YCSB in the LeanStore paper's
  follow-up.
- **Production**: LeanStore (open-source); related ideas appear in
  Umbra and CedarDB.

### 10.3 Linux MGLRU — Multi-Generational LRU

Merged in Linux 6.1 (late 2022) as an alternative to the traditional
active/inactive lists.

**Mechanism**:
- Pages are tagged with an integer **generation**. New pages get the
  current generation; an "aging" scan promotes recently-accessed pages
  to younger generations.
- Eviction always proceeds from the **oldest generation**.
- Uses **hardware accessed bits** in page tables aggressively (walked
  via `pte_young()`), avoiding per-page software bookkeeping.
- Generations are demand-managed: aging is triggered when there is
  reclaim pressure, not on every access.

- **Empirical** (Google internal data): 40% reduction in `kswapd` CPU,
  85% reduction in low-memory kills, 18% improvement in rendering
  latency.
- **Production**: default on Android (since 2022), Amazon Linux 2023,
  ChromeOS, modern kernels.

### 10.4 Predictive Translation (LeanStore lineage, SIGMOD 2026)

Covered separately in
[`database/buffer_management_predictive_translation.md`](@/notes/database/buffer_management_predictive_translation.md).
Uses a hashed indirection table with optimistic latching to keep the hot
path lock-free at the cost of probabilistic translation misses, in the
spirit of pointer swizzling but with hardware-prefetch-friendly access
patterns.

---

## 11. LLM KV-cache eviction (2023–2025)

A new and fast-moving subfield. As LLM context windows grew to
128k–1M tokens, the **per-token key/value tensors** of the attention
cache (KV cache) dominate GPU memory. KV-cache eviction is critical for
long-context inference and batched serving.

### 11.1 PagedAttention / vLLM (Kwon et al., SOSP 2023)

Not eviction per se, but the **block-wise memory management** that makes
eviction tractable. KV-cache is stored in fixed-size **blocks** (e.g.
16 tokens × num_heads × head_dim), addressed through a per-sequence
**block table** — borrowed directly from OS virtual memory paging.
Enables sharing prefixes across requests, copy-on-write for sampling, and
swapping blocks to host memory when GPU memory is exhausted.

### 11.2 H2O — Heavy-Hitter Oracle (Zhang et al., NeurIPS 2023)

**Insight**: attention weights are extremely sparse — for most tokens, a
few "heavy hitter" past tokens dominate the dot product.

**Mechanism**:
- Maintain per-token **cumulative normalised attention scores** updated
  on every decoding step.
- Cache contains:
  - A fixed budget of *recent* tokens (sliding window).
  - A fixed budget of *high-score* tokens (heavy hitters).
- On every decoding step, evict the token with lowest cumulative score
  outside the recent window.

- **Empirical**: with 20% KV-cache retained, throughput up to **29×**
  vs DeepSpeed Zero-Inference / HF Accelerate, **3×** vs FlexGen on
  OPT-6.7B and OPT-30B; latency reduced 1.9× at constant batch.
- **Theoretical**: cast as a dynamic submodular maximisation problem
  with approximation guarantees.

### 11.3 StreamingLLM (Xiao, Tian, Chen, Han, ICLR 2024)

**Surprising observation**: attention sinks — the very first few tokens
(BOS, system prompt prefix) accumulate disproportionately high attention
weights regardless of content, because they are the only tokens visible
to every later position.

**Mechanism**: maintain the first ~4 "sink" tokens + a sliding window of
the last K tokens; evict everything in the middle. Achieves stable
generation for *millions* of tokens with constant memory.

### 11.4 Later directions (2024–2025)

- **PagedEviction**: block-wise pruning that integrates with vLLM's
  paged structure.
- **In-context KV-Cache Eviction via Attention-Gate**: differentiable
  eviction module trained jointly with the LLM.
- **FastGen**: heads-aware eviction — different attention heads have
  different sparsity patterns.
- **SnapKV / PyramidKV / RazorAttention**: layer-wise budget allocation
  reflecting that deeper layers have more diffuse attention.

---

## 12. Prefetching

Prefetching attacks the *compulsory miss* (first reference) part of the
3C miss taxonomy (compulsory, capacity, conflict). Modern caches combine
prefetching with eviction.

### 12.1 Sequential / OBL / one-block-lookahead

The classical baseline: on a miss for block `b`, also fetch `b+1`. Used
in disk read-ahead (Linux `readahead()`). **Asynchronous OBL** issues
the prefetch out of the critical path.

### 12.2 Stride prefetching

Detect constant stride patterns (block `b`, `b+s`, `b+2s`, …) and
extrapolate. Common in CPU hardware prefetchers (L1/L2). Storage
analogue: detect `s`-spaced disk-block sequences.

### 12.3 Adaptive Multi-stream Prefetching (Gill & Bathen, FAST 2007)

**AMP** addresses two failure modes of fixed-degree sequential prefetch
in **shared caches** (with multiple concurrent streams):
- **Cache pollution**: prefetched data evicts more useful data.
- **Prefetch wastage**: prefetched data evicted before use.

AMP is in the **Adaptive Asynchronous (AA)** class — per-stream
prefetch depth `p` and trigger distance `g` adapt online based on
observed cache hit/miss feedback. Proved near-optimal for steady
sequential streams under any cache size.

- **Production**: IBM storage controllers, multiple SAN products.

### 12.4 Mithril — Mining sporadic associations (Yang, Karimi, Sæmundsson,
Wildani, Vigfusson, ACM SoCC 2017)

**Association-rule mining** applied to cache prefetching. Detects
**mid-frequency** correlations that classical prefetchers miss (not
sequential, not random — sporadic but reliable: "every time block A is
read, block C is read within 10 accesses").

**Mechanism**:
- Mine the access log online using a streaming variant of FP-Growth.
- Generate rules `X ⇒ Y` with support and confidence thresholds.
- On hit of `X`, prefetch all `Y` whose rule confidence exceeds a
  threshold.
- Mid-frequency targeting: filter out very-hot blocks (always cached)
  and very-cold (rules unreliable).

- **Empirical**: 55% average hit-ratio gain over LRU + Probability
  Graph on 135 block-storage traces; 36% over AMP.
- **Property**: composable — sits on top of any eviction policy.

### 12.5 Hardware learned prefetchers

- **Pythia** (Bera, Kanellopoulos, Nori, Shahroodi, Subramoney, Mutlu,
  MICRO 2021): reinforcement-learning hardware prefetcher with multiple
  program-context features (PC, address delta, page offset, page
  granularity); reward signal includes memory bandwidth utilisation.
  Implemented in Chisel HDL. ~3.4% IPC improvement over the
  best non-learned prefetcher.
- **Voyager** (Shi, Akram, Pickett, Lustig, ASPLOS 2021):
  LSTM-based memory prefetcher for irregular pointer-chasing workloads.

---

## 13. Cache simulators

Reproducible benchmarking is critical because miss ratios are
trace-dependent.

### 13.1 libCacheSim (CMU PDL, Yang et al.)

**Repository**: `cacheMon/libCacheSim`. The de-facto modern reference
simulator — used in the S3-FIFO, SIEVE, GL-Cache, 3L-Cache papers.

- **Language**: C++ with C / Python bindings.
- **Traces**: text CSV, binary `oraclegeneral` format (compact, hashed),
  Zstandard-compressed binary.
- **Policies**: LRU, FIFO, CLOCK, ARC, LIRS, 2Q, SLRU, S3-FIFO, SIEVE,
  Hyperbolic, LHD, GDSF, TinyLFU, W-TinyLFU, optimal (offline Belady),
  GL-Cache, … (50+).
- **Throughput**: ~10–50 million requests/sec on a single core.
- **Adoption**: ~100 research institutions and companies.

### 13.2 Caffeine simulator (Ben Manes)

Java module `caffeine/simulator`. Implements every major academic
policy (LRU, ARC, LIRS, LIRS2, CAR, CLOCK-Pro, W-TinyLFU, LeCaR,
Hyperbolic, …) for hit-ratio comparison. The Caffeine README has the
canonical comparison charts on the ARC, LIRS, OLTP traces.

### 13.3 PyMimircache / PyCacheSim

Pythonic exploratory tools. Older PyMimircache (Emory / CMU) is largely
superseded by libCacheSim, but its Jupyter-friendly API remains useful
for plotting.

### 13.4 webcachesim (Berger et al.)

C++ simulator from the AdaptSize / LRB papers, focused on CDN object
caching with variable sizes.

### 13.5 ChampSim

Cycle-accurate simulator from the **CRC2 (Cache Replacement Championship 2)**
ecosystem — used for hardware (CPU LLC) replacement, not software caches.
Hosts the Pythia HDL+sim reference.

---

## 14. Standard traces and benchmarks

### 14.1 SNIA IOTTA Repository — MSR Cambridge (Narayanan, FAST 2008)

http://iotta.snia.org/traces/388 — 36 enterprise-server block-IO traces
from a week in 2007. The de facto storage-cache benchmark for 15+ years.
Used in nearly every replacement paper from 2008 onward.

### 14.2 Twitter cache traces (Yang, Yue, Rashmi, OSDI 2020)

`twitter/cache-trace` on GitHub. 54 traces from production Memcached /
Twemcache clusters, anonymised. Spans Zipfian, write-heavy, TTL-heavy,
and bursty workloads. Source of the observation that "FIFO often beats
LRU" that motivated S3-FIFO / SIEVE.

### 14.3 CloudPhysics VM I/O traces (Ahmad et al., 2015)

Used in LeCaR, Cacheus, LIRS2. Heterogeneous virtual disk I/O,
moderately sized.

### 14.4 FIU SRC traces

8 server/client traces (web, mail, virtualisation) from Florida
International University. Used in LeCaR family.

### 14.5 Wikipedia CDN traces

Published with AdaptSize and LRB papers; one server's San Francisco
Wikipedia traffic. CDN-focused (variable object sizes).

### 14.6 CDN / web public traces

- **CDN1, CDN2, CDN3**: anonymised production CDN traces from large
  providers (used in S3-FIFO; >2 trillion requests total).
- **Tencent Photo cache trace** (Zhang et al., FAST 2020): photo
  service trace, byte- and request-skewed.
- **IBM ObjectStore** traces (Eytan et al., 2019): cloud-block-store
  workloads with object-size variation.
- **MemCachier**: anonymised production cache logs from MemCachier.com.
- **Twitch**: video streaming workload.
- **Twitter cluster** (above).

### 14.7 Cache benchmarking suites

- **CacheBench** (Meta CacheLib): synthetic workload generation +
  trace replay with hit-ratio, throughput, latency-percentile reports.
- **YCSB cache mode**: Zipfian, uniform, latest distributions for
  pure-rate testing.
- **ARC benchmark suite** (IBM 2003): the classic OLTP, P*, S*, ConCat,
  Merge, DS1 traces still used today.

---

## 15. Evaluation methodology

### 15.1 Miss-ratio curves (MRC)

The fundamental tool: `miss_ratio(c)` as a function of cache size `c`.
Captures *all* sizes in one chart, exposing knees and convexity.

**Properties**:
- For **stack algorithms** (LRU, OPT), MRC is monotone-decreasing.
- For non-stack algorithms (FIFO, CLOCK), the MRC may have local
  bumps (Belady's anomaly).
- The MRC under OPT is the *lower envelope* against which every other
  policy is judged.

**Naive computation**: Mattson's stack algorithm — O(N·M).

### 15.2 SHARDS (Waldspurger, Park, Garthwaite, Ahmad, FAST 2015)

**Spatially Hashed Approximate Reuse-Distance Sampling**: an approximate
MRC algorithm in *constant* space.

**Idea**: deterministically sample a subset of references by hashing the
*key* (not random per-reference): `if hash(k) < T then keep`. Because
the decision is keyed on the object, the same items are consistently
included or excluded — preserving inter-reference patterns within the
sample.

```
threshold T  — sampling intensity (e.g. T = 1% of hash range)
for each ref(k):
    if hash(k) < T:  feed to exact MRC algorithm scaled by 1/(T/MAX)

To run in constant space:  if memory pressure → lower T further;
re-scale previously-seen sample counts accordingly.
```

- **Empirical**: at 0.1% sampling rate, the approximate MRC is within
  0.02 of exact on average. Production-deployable.
- **Production**: VMware vSphere (where Waldspurger was at the time of
  publication); influences modern observability tools.

### 15.3 Counter Stacks (Wires, Ingram, Drudi, Harvey, Warfield, OSDI 2014)

A *streaming sublinear-space* MRC algorithm using **HyperLogLog**
sketches.

**Idea**: maintain a sequence of HLL counters over different lookback
intervals; the HLL union/cardinality difference yields an estimate of
stack distance distribution. Sublinear in distinct items.

- **Empirical**: a week of trace from 13 enterprise servers (≈ 1
  trillion refs) processed in 17 minutes with 80 MB RAM.
- Followed by **AET** (Hu et al., 2018) and **PARDA** for parallel MRC.

### 15.4 Beyond hit ratio

Modern evaluation considers multiple metrics:
- **Hit ratio** (object) — fraction of requests served from cache.
- **Byte hit ratio** — fraction of bytes served from cache (matters
  for bandwidth-constrained CDNs).
- **Throughput** (requests/sec/core) — exposes lock contention,
  promotion cost.
- **Tail latency** (P99, P999) — eviction policies that incur sweep
  storms cause tail blips.
- **Memory overhead** (bytes/entry of metadata).
- **CPU overhead** of training/inference (for learned caches).
- **Adaptivity** — how fast hit ratio recovers after workload shifts.

### 15.5 Pareto-frontier comparisons

Recent papers (S3-FIFO, SIEVE, GL-Cache, 3L-Cache) report on the
hit-ratio × throughput plane: simpler policies (FIFO, SIEVE) sit at high
throughput / moderate hit ratio; complex policies (LRB) at high hit ratio
/ lower throughput. The Pareto frontier moves outward each year.

---

## 16. Decision matrix and recommendations

### 16.1 By system type

| System                       | Recommended policy                       |
|------------------------------|-------------------------------------------|
| In-process Java cache        | W-TinyLFU (Caffeine)                      |
| Web/key-value cache (Memcached-style) | S3-FIFO or SIEVE                  |
| CDN with variable size       | LRB / GL-Cache / GDSF / AdaptSize         |
| Database buffer pool (OLTP)  | LRU-K / WATT / 2Q / clock-with-counters   |
| Database buffer pool (mixed) | CAR / ARC / W-TinyLFU                     |
| Block storage cache          | ARC / LIRS / CLOCK-Pro                    |
| Flash-backed cache           | CFLRU + write-aware admission             |
| Linux kernel page cache      | MGLRU                                     |
| LLM KV-cache                 | H2O + StreamingLLM (sinks) + PagedAttn    |
| Operating system VM          | MGLRU / CLOCK-Pro                         |
| L1/L2 CPU caches             | hardware LRU / pseudo-LRU / DIP / RRIP    |

### 16.2 Implementation cheat sheet

If you must implement *one* eviction policy in 2026:

```
- If you control the hit path and need scalability:        SIEVE
- If you want best hit ratio at the cost of complexity:    W-TinyLFU
- If you have ghost queues and want simplicity + headroom: S3-FIFO
- If your workload is OLTP-like (DB pages):                LRU-2 or WATT
- If sizes vary wildly (CDN):                              GDSF or LHD or LRB
```

### 16.3 Anti-patterns

- **Pure LRU under scan workloads**: one large query trashes the cache.
- **Pure LFU without aging**: stale formerly-hot keys never leave.
- **High-frequency promotion under contention**: pure LRU's
  move-to-front becomes the bottleneck; replace with CLOCK or
  ghost-confirmed promotion.
- **Mixing eviction and admission via thresholds without modelling**: 
  always-admit + LRU is often worse than always-admit + S3-FIFO at the 
  same memory.
- **Ignoring write cost**: in flash/dirty-page caches, eviction must 
  factor in write-back amplification.

### 16.4 Pitfalls when evaluating

- Trace bias: ARC-1 trace conclusions don't generalise to CDN
  workloads.
- Cold-start: report only *after* warmup window (often 10% of trace).
- Ghost size: ARC/2Q/S3-FIFO require explicit ghost memory accounting
  in fair comparison.
- Concurrent benchmark: report single-thread and 8/16/64-thread
  numbers; the gap between LRU and SIEVE *grows* with contention.

---

## 17. References

### 17.1 Foundations

- Belady, L. A. **"A Study of Replacement Algorithms for a Virtual-Storage
  Computer"**. IBM Systems Journal 5(2), 1966.
- Belady, L. A., Palermo, F. P. **"On-line measurement of paging
  behavior by the multivalued MIN algorithm"**. IBM Journal of Research
  and Development 18(1), 1974.
- Denning, P. J. **"The Working Set Model for Program Behavior"**.
  CACM 11(5), 1968.
- Mattson, Gecsei, Slutz, Traiger. **"Evaluation Techniques for Storage
  Hierarchies"**. IBM Systems Journal 9(2), 1970.
- Corbató, F. J. **"A Paging Experiment with the Multics System"**.
  MIT Project MAC Memo M-384, 1968 (CLOCK).
- Young, N. E. **"On-line caching as cache size decreases"**. SODA 1991
  (GreedyDual).

### 17.2 Classical and recency–frequency hybrids

- Karedla, Love, Wherry. **"Caching Strategies to Improve Disk System
  Performance"**. IEEE Computer 27(3), 1994 (SLRU).
- O'Neil, O'Neil, Weikum. **"The LRU-K Page Replacement Algorithm for
  Database Disk Buffering"**. SIGMOD 1993.
- Johnson, T., Shasha, D. **"2Q: A Low Overhead High Performance Buffer
  Replacement Algorithm"**. VLDB 1994.
- Zhou, Y., Philbin, J., Li, K. **"The Multi-Queue Replacement Algorithm
  for Second Level Buffer Caches"**. USENIX ATC 2001.
- Megiddo, N., Modha, D. S. **"ARC: A Self-Tuning, Low Overhead
  Replacement Cache"**. FAST 2003. (IEEE Computer 2004 follow-up.)
- Bansal, S., Modha, D. S. **"CAR: Clock with Adaptive Replacement"**.
  FAST 2004.
- Jiang, S., Zhang, X. **"LIRS: An Efficient Low Inter-Reference Recency
  Set Replacement Policy to Improve Buffer Cache Performance"**.
  SIGMETRICS 2002.
- Jiang, S., Chen, F., Zhang, X. **"CLOCK-Pro: An Effective Improvement
  of the CLOCK Replacement"**. USENIX ATC 2005.
- Li, C. **"DLIRS: Improving LIRS Cache Replacement with Dynamics"**.
  SYSTOR 2018.
- Zhong, Tao, Jiang. **"LIRS2: An Improved LIRS Replacement
  Algorithm"**. SYSTOR 2021.
- Park, Yeom. **"FRD: A Filtering-based Buffer Cache Algorithm"**.
  MSST 2017.

### 17.3 TinyLFU family

- Einziger, G., Friedman, R., Manes, B. **"TinyLFU: A Highly Efficient
  Cache Admission Policy"**. ACM Transactions on Storage 13(4), 2017
  (arXiv 1512.00727 — preliminary in PDP 2014).
- Caffeine Wiki — Efficiency and Design pages,
  https://github.com/ben-manes/caffeine/wiki

### 17.4 Modern lazy-promotion family

- Yang, Qiu, Zhang, Yue, Rashmi. **"FIFO can be Better than LRU: the
  Power of Lazy Promotion and Quick Demotion"**. HotOS 2023.
- Yang, Zhang, Qiu, Yue, Rashmi. **"FIFO Queues Are All You Need for
  Cache Eviction"** (S3-FIFO). SOSP 2023.
- Zhang, Yang, Yue, Vigfusson, Rashmi. **"SIEVE is Simpler than LRU:
  an Efficient Turn-Key Eviction Algorithm for Web Caches"**.
  NSDI 2024.
- Yang, J. (PhD dissertation). **"Designing Efficient and Scalable
  Key-value Cache Management Systems"**. CMU-CS-24-149, 2024.

### 17.5 Cost- and size-aware

- Cao, P., Irani, S. **"Cost-Aware WWW Proxy Caching Algorithms"**.
  USENIX Symp. Internet Tech. and Systems, 1997 (GreedyDual-Size).
- Cherkasova, L. **"Improving WWW Proxies Performance with
  Greedy-Dual-Size-Frequency Caching Policy"**. HP TR HPL-98-69R1,
  1998 (GDSF).
- Arlitt, Cherkasova, Dilley, Friedrich, Jin. **"Evaluating Content
  Management Techniques for Web Proxy Caches"**. Performance
  Evaluation Review 27(4), 2000 (LFUDA).
- Blankstein, A., Sen, S., Freedman, M. J. **"Hyperbolic Caching:
  Flexible Caching for Web Applications"**. USENIX ATC 2017.
- Beckmann, N., Chen, H., Cidon, A. **"LHD: Improving Cache Hit Rate by
  Maximizing Hit Density"**. NSDI 2018.

### 17.6 Flash-aware

- Park, Jung, Kang, Kim, Lee. **"CFLRU: A Replacement Algorithm for
  Flash Memory"**. CASES 2006.
- Jung, Y. et al. **"LRU-WSR: A Page Replacement Algorithm Based on
  Cold Detection for Flash Memory"**. CASES 2007 / IEEE TCE 2008.
- Li, Z. et al. **"CCF-LRU: A New Buffer Replacement Algorithm for
  Flash Memory"**. IEEE TCE 2009.

### 17.7 Admission policies

- Berger, D. S., Sitaraman, R. K., Harchol-Balter, M. **"AdaptSize:
  Orchestrating the Hot Object Memory Cache in a Content Delivery
  Network"**. NSDI 2017.
- Einziger, Friedman, Kassner. **"Lightweight Robust Size-Aware Cache
  Management"**. ACM Transactions on Storage 18(3), 2022.
- (Doorkeeper / probabilistic admission concepts diffused through TinyLFU
  and CacheLib.)

### 17.8 Learned caching

- Vietri, Rodriguez, Cheng, Mukherjee, Ge, Lyons, Liu, Narasimhan.
  **"Driving Cache Replacement with ML-based LeCaR"**. HotStorage 2018.
- Rodriguez, Yusuf, Lyons, Paz, Rangaswami, Liu, Zhao, Narasimhan.
  **"Learning Cache Replacement with CACHEUS"**. FAST 2021.
- Song, Berger, Li, Lloyd. **"Learning Relaxed Belady for Content
  Distribution Network Caching"**. NSDI 2020 (LRB).
- Song, Berger, Lloyd. **"HALP: Heuristic Aided Learned Preference
  Eviction Policy for YouTube Content Delivery Network"**. NSDI 2023.
- Yang, Mao, Yue, Rashmi. **"GL-Cache: Group-level Learning for
  Efficient and High-performance Caching"**. FAST 2023.
- Zhou et al. **"3L-Cache: Low Overhead and Precise Learning-based
  Eviction Policy for Caches"**. FAST 2025.

### 17.9 Buffer-manager-specific

- Vöhringer, D. E., Leis, V. **"Write-Aware Timestamp Tracking:
  Effective and Efficient Page Replacement for Modern Hardware"**.
  PVLDB 16(11), 2023 (WATT).
- LWN coverage of MGLRU, https://lwn.net/Articles/851184/ and
  https://lwn.net/Articles/1060967/
- PostgreSQL source: `src/backend/storage/buffer/freelist.c`,
  `bufmgr.c`.

### 17.10 LLM KV-cache

- Kwon et al. **"Efficient Memory Management for Large Language Model
  Serving with PagedAttention"** (vLLM). SOSP 2023.
- Zhang et al. **"H2O: Heavy-Hitter Oracle for Efficient Generative
  Inference of Large Language Models"**. NeurIPS 2023.
- Xiao, Tian, Chen, Han. **"Efficient Streaming Language Models with
  Attention Sinks"** (StreamingLLM). ICLR 2024.

### 17.11 Prefetching

- Gill, B. S., Bathen, L. A. D. **"AMP: Adaptive Multi-stream
  Prefetching in a Shared Cache"**. FAST 2007.
- Yang, Karimi, Sæmundsson, Wildani, Vigfusson. **"Mithril: Mining
  Sporadic Associations for Cache Prefetching"**. ACM SoCC 2017.
- Bera, Kanellopoulos, Nori, Shahroodi, Subramoney, Mutlu. **"Pythia:
  A Customizable Hardware Prefetching Framework Using Online
  Reinforcement Learning"**. MICRO 2021.
- Shi, Akram, Pickett, Lustig. **"Voyager: Combining Local and Global
  Features for Practical Learned Memory Prefetching"**. ASPLOS 2021.

### 17.12 Evaluation and tooling

- Waldspurger, Park, Garthwaite, Ahmad. **"Efficient MRC Construction
  with SHARDS"**. FAST 2015.
- Wires, Ingram, Drudi, Harvey, Warfield. **"Characterizing Storage
  Workloads with Counter Stacks"**. OSDI 2014.
- Berg, Berger, et al. **"The CacheLib Caching Engine: Design and
  Experiences at Scale"**. OSDI 2020.
- libCacheSim — https://github.com/cacheMon/libCacheSim
- Caffeine — https://github.com/ben-manes/caffeine
- AdaptSize / webcachesim — https://github.com/dasebe/AdaptSize ,
  https://github.com/dasebe/optimalwebcaching
- LRB simulator — https://github.com/sunnyszy/lrb
- S3-FIFO repo — https://github.com/Thesys-lab/sosp23-s3fifo
- SIEVE repo — https://github.com/cacheMon/NSDI24-SIEVE

### 17.13 Production traces and datasets

- SNIA IOTTA — http://iotta.snia.org/
- MSR Cambridge — http://iotta.snia.org/traces/388
- Twitter cache traces — https://github.com/twitter/cache-trace
- Yang, J., Yue, Y., Rashmi, K. V. **"A Large-scale Analysis of
  Hundreds of In-memory Key-value Cache Clusters at Twitter"**.
  OSDI 2020; ACM TOS 17(3), 2021.

### 17.14 Production systems

- Berg, Berger, McAllister, Grosof, et al. CacheLib at Meta — OSDI 2020.
- Tang, L., Huang, Q., Lloyd, W., Kumar, S., Li, K. **"RIPQ: Advanced
  Photo Caching on Flash for Facebook"**. FAST 2015.
- Caffeine adoption (Cassandra, Kafka, Solr, HBase, Neo4j) —
  https://github.com/ben-manes/caffeine/wiki

---

## Appendix A — One-page algorithm comparison table

| Algorithm | Year | Class | Hit-path | Time | Space (per entry) | Scan-res | Freq-aware | Adaptive | Production |
|-----------|------|-------|----------|------|---------------------|----------|-----------|----------|------------|
| FIFO | — | recency | nop | O(1) | 1 ptr | no | no | no | LRU's worst replacement, common fallback |
| LRU | — | recency | move-to-MRU | O(1) | 2 ptr | no | no | no | textbook default |
| CLOCK | 1968 | recency | set ref-bit | O(1) amort | 1 bit | no | no | no | OS kernels |
| SLRU | 1994 | both | move-segments | O(1) | 2 ptr + flag | yes | yes | no | W-TinyLFU's main |
| LFU | — | freq | counter++ | O(1)–O(log c) | counter | yes | yes | no | hot-set caches |
| LFUDA | 2000 | freq | counter + age | O(log c) | counter | yes | yes | no | proxy caches |
| LRU-K | 1993 | both | timestamp log | O(log c) | K timestamps | yes | yes | no | OLTP DBs |
| 2Q | 1994 | both | move within Am | O(1) | 2 ptr | yes | yes | no | Postgres pgvector etc. |
| MQ | 2001 | both | counter + queue | O(1) | ptr + count | yes | yes | yes | 2nd-level buffer |
| ARC | 2003 | both | LRU move + adapt | O(1) | 2 ptr + ghost | yes | yes | yes | ZFS, DB2 |
| CAR | 2004 | both | set ref-bit | O(1) amort | 1 bit + ghost | yes | yes | yes | research |
| CART | 2004 | both | + long-term bit | O(1) amort | 2 bits + ghost | yes | yes | yes | research |
| LIRS | 2002 | both | stack maintain | O(1) amort | flag + ghost | yes | yes | no | H2 DB, Cassandra (hist.), Caffeine sim |
| CLOCK-Pro | 2005 | both | set ref-bit | O(1) amort | flag + ghost | yes | yes | no | NetBSD VM |
| TinyLFU | 2017 | admission | CMS update | O(1) | 4-bit CMS | yes | yes | yes (sketch) | Caffeine |
| W-TinyLFU | 2017 | both | window LRU + CMS | O(1) | 8 B/entry | yes | yes | yes (hill) | Caffeine, Cassandra, Solr, Kafka |
| S3-FIFO | 2023 | both | counter++ | O(1) | 2 bits + ghost | yes | yes | no | Foyer, libCacheSim |
| SIEVE | 2024 | both | set visited bit | O(1) | 1 bit | yes | impl. | no | Rust sieve-cache, Go go-sieve |
| GDSF | 1998 | cost+size | recompute prio | O(log c) | ptr + cnt + size | yes | yes | no | Squid |
| Hyperbolic | 2017 | rate | counter + time | O(n) sample | counter + ts | yes | rate | yes (sampled) | Redis-like |
| LHD | 2018 | hit-density | hist update | O(n) sample | class id | yes | yes | yes | research |
| LeCaR | 2018 | learned mix | sample expert | O(1) | weights | yes | yes | yes | research |
| Cacheus | 2021 | learned mix | sample experts | O(1) | weights | yes | yes | yes | research |
| LRB | 2020 | GBDT | model infer | O(model) | many feats | yes | yes | yes | Apache Traffic Server |
| HALP | 2023 | NN preference | NN score | O(model · K) | feats | yes | yes | yes | YouTube CDN |
| GL-Cache | 2023 | group learn | group update | O(1) hit | group ptr | yes | yes | yes | research |
| 3L-Cache | 2025 | sampled obj-learn | bit + ts | O(1) hot | bit + ts | yes | yes | yes | research |
| CFLRU | 2006 | flash | LRU + dirty flag | O(1) | dirty bit | partial | no | no | embedded SSDs |
| MGLRU | 2022 | generations | hw access bit | O(1) | generation | yes | yes | yes | Linux 6.1+ |
| WATT | 2023 | DB buffer | timestamp ring | O(1) | K timestamps | yes | yes | yes | LeanStore |
| Clock-sweep | — | DB buffer | usage_count++ | O(1) | 3 bits | partial | partial | no | PostgreSQL |
| H2O | 2023 | LLM KV | score update | O(L) | attn score | n/a | yes | n/a | LLM serving |
| StreamingLLM | 2024 | LLM KV | sliding window | O(1) | none | n/a | n/a | n/a | LLM serving |

---

*Last updated: 2026-05-26. ~40 algorithms, ~95 references.*
