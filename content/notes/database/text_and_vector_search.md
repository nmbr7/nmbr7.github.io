+++
title = "Text And Vector Search"
date = 2026-05-20
[taxonomies]
tags = ["full-text-search", "vector-search", "ann", "inverted-index", "elasticsearch", "embeddings"]
+++


# Text Search and Vector / Similarity Search

Expert reference for full-text and similarity search engines: classical inverted index plumbing, modern ANN graphs/quantizers, learned sparse retrieval, neural hybrid pipelines, GPU-accelerated indexes, and the production systems built around them. State of the art through 2025.

---

## 1. Exact / Full-Text Search

### 1.1 Inverted Index — Anatomy

```
Term dictionary (FST / B-tree / BlockTree)
        │ term -> (df, posting offset, ...)
        ▼
Posting list  ─►  [ docId, tf, [pos1, pos2, ...] ]   per document
        │
        ├── compressed in blocks of 128 (Lucene), 64, 256 ...
        ├── each block has a max docID + max impact (BMW)
        └── skip list / forward index for random access
```

Files in Lucene 9.x:
- `.tip` — terms index (FST in RAM, prefix → on-disk block)
- `.tim` — terms dictionary (`BlockTreeTermsWriter`, blocks of 25–48 entries; "floor blocks" subdivide oversize blocks)
- `.doc`, `.pos`, `.pay` — postings, positions, payloads (PFor compressed)
- `.nvd/.nvm` — norms (length norms for BM25)
- `.dvd/.dvm` — doc values (columnar, per-doc, for sort/filter/aggregations)
- `.kdd/.kdi/.kdm` — BKD trees for numeric / point fields
- `.vec/.vem/.vex` — HNSW vector index (Lucene 9+)

**Terms Dictionary (Lucene BlockTree + FST).** Lucene stores the terms dictionary as a BlockTree: on-disk blocks of 25–48 terms each, with an in-memory FST (Finite State Transducer) mapping valid prefixes to on-disk block offsets. The FST is built using Mihov & Maurel's direct minimal acyclic transducer construction over sorted terms. RAM footprint ~38–52% smaller than a plain trie; fuzzy queries ~22% faster than pre-4.0 approach (Mike McCandless, Lucene 4.0).

### 1.2 Postings-list Compression

| Codec | Idea | Decode speed | Ratio | Notes |
|---|---|---|---|---|
| VByte / Varint | 1 byte per 7 bits | medium | medium | Old default, branchy; still competitive on short lists |
| Group Varint (Google) | 4 ints packed, 1-byte length prefix per group | fast | medium | SIMD friendly; used in Google's production systems |
| Simple9 / Simple16 | 4-bit selector + 28 data bits per word | fast | medium | Daniel Lemire's domain; simple encode/decode |
| PFor / PForDelta (Zukowski 2006) | Block of 128 ints, k bits each + exception list | very fast | good | Chosen so ~90% fit in k bits; SIMD-amenable |
| FastPFOR / OptPFD | Cost-based k selection | very fast | very good | Lemire & Boytsov, SP&E 2015 |
| SIMD-BP128 | 128-int blocks, 16 SIMD lanes | fastest sequential | medium-good | Lemire; baseline for "fast" bench numbers |
| QMX | Quantities, Multipliers, Extractor | fast | very good | Trotman 2014; beats SIMD-BP128 on some distributions |
| Elias-Fano (Vigna) | Optimal for monotone sequences | medium-fast (with skipping) | best for dense | O(1) random access with select structure |
| PEF — Partitioned Elias-Fano | Adaptive partitioning of EF | medium-fast | best overall | Ottaviano & Venturini, SIGIR 2014 |
| Clustered EF | Cluster lists by reference list | medium | best | Pibiri & Venturini, TOIS 2017 |
| Roaring Bitmaps | Two-level (16-bit) chunks: array / bitmap / RLE | very fast | very good | Lemire et al., SP&E 2016; standard in Lucene/Druid/Pinot |

**Empirical ranking** (Pibiri & Venturini, "Techniques for Inverted Index Compression," CSUR 2021): PEF ≈ SIMD-BP128 in throughput; PEF wins compression on Gov2/ClueWeb; SIMD-BP128 wins decoding throughput on long lists; on short lists, Simple-family or VByte often beats SIMD codecs because of fixed-block overhead. QMX performs well on medium-length lists with skewed distributions. Clustered EF achieves the best space on highly clustered corpora.

### 1.3 Skip Lists, Block-Max Indexes

A posting list is partitioned into blocks of 128 docIDs (Lucene `Lucene90PostingsFormat`). Each block stores:

```
[ blockMaxDocID, blockMaxImpact, packed_bits_for_docs, packed_bits_for_freqs ]
```

The `blockMaxImpact` is a precomputed score upper bound for any document in the block — the foundation of Block-Max WAND. Skip data allows jumping over entire blocks when the block's max impact cannot beat the current threshold θ.

### 1.4 Query Processing — DAAT, TAAT, WAND, MaxScore, BMW, VBMW, JASS

**TAAT (Term-At-A-Time, Buckley & Lewit 1985).** Iterate one posting list to completion, accumulating scores per docID into a hash/array of accumulators. Random-access friendly but needs O(N) accumulators; falls out of favor on web-scale.

**DAAT (Document-At-A-Time).** Maintain one cursor per term, advance cursors in lockstep to the smallest current docID, score it. Needs only top-k heap; cache friendly; default in Lucene/Indri.

**MaxScore (Turtle & Flood — IPM 1995).** Each term has an upper-bound impact `ub_t`. Sort terms by `ub_t`. Split into "essential" terms (whose collective `ub_t` exceeds θ, current k-th score) and "non-essential" terms. A document scores only if it appears in at least one essential list, so we can skip large portions of low-impact lists. As θ rises, more terms become non-essential ⇒ more skipping. More aggressive than WAND for skewed score distributions.

**WAND (Broder, Carmel, Herscovici, Soffer, Zien — CIKM 2003).** "Weak AND." Sort cursors by current docID. The pivot is the first cursor whose cumulative `ub` exceeds θ. If all cursors before pivot point at the pivot's doc, score it; else advance the lagging cursor to ≥ pivot doc.

```
Algorithm WAND:
  maintain sorted list of (current_docID, term) pairs
  pivot = first docID where cumulative UB scores exceed threshold θ
  advance all terms behind pivot to ≥ pivot
  fully evaluate only docs that survive pivot selection
```

Faster than MaxScore for short queries; slower for long ones. Provably rank-safe (returns the same top-K as exhaustive DAAT). Production: 10–25 ms on typical web collections.

**Block-Max WAND / BMW (Ding & Suel — SIGIR 2011).** WAND with block-level max impacts. After choosing a pivot, check the sum of *block* maxes (not list maxes) at that docID. If it can't beat θ, skip the entire block. ~2× speedup over WAND on TREC GOV2.

**Block-Max MaxScore (Mallia & Ottaviano — SIGIR 2017).** Same block-max trick applied to MaxScore. Often beats BMW on long queries.

**VBMW — Variable Block-Max WAND (Mallia, Ottaviano, Porciani, Tonellotto — SIGIR 2017).** Variable-size blocks chosen by dynamic programming to minimize bound looseness. Several × over BMW. Implemented in PISA and Tantivy.

**LazyBM (Mallia, Khattab, Suel, Tonellotto — SIGIR 2020).** Defers block-max checks; combines with PISA's data-aware scheduling. Reduces redundant upper-bound checks in BMW.

**JASS / SAAT — Score-At-A-Time (Lin & Trotman 2015).** Posting lists pre-sorted by impact (quantized score). Visit highest-impact postings first; can stop at any time. Anytime ranking, robust to long queries. Deployed in systems with aggressive latency SLAs.

**Production status:** Lucene/Elasticsearch use BMW + MaxScore. PISA (research) and Tantivy implement BMW/VBMW. Vespa uses WAND with rank thresholds.

### 1.5 BM25 / TF-IDF / BM25+ / BM25F

**TF-IDF** (Sparck Jones 1972, Robertson & Spärck Jones 1976):
```
TF-IDF(t,d) = tf(t,d) × log(N / df(t))
```

**BM25 (Robertson, Walker, Jones — TREC 1994):**

```
score(d, q) = Σ_{t ∈ q} IDF(t) · (tf_{t,d} · (k1+1)) / (tf_{t,d} + k1 · (1 - b + b · |d|/avgdl))

IDF(t) = ln( (N - df_t + 0.5) / (df_t + 0.5) + 1 )
```

- `k1 ∈ [1.2, 2.0]` controls TF saturation (Lucene default 1.2).
- `b ∈ [0, 1]` controls length normalization (Lucene default 0.75).
- TF saturates: doubling tf does not double the score.

**BM25+ (Lv & Zhai — CIKM 2011).** Adds a lower-bound `δ` (≈ 1.0) to the saturation term so that any positive `tf` always scores strictly higher than `tf=0`. Fixes the "long-document under-scoring" pathology.

**BM25L (Lv & Zhai 2011).** Length-aware variant; normalizes TF by an adjusted document length before saturation. Also addresses the long-document issue from a different angle.

**BM25F (Robertson, Zaragoza, Taylor — CIKM 2004).** Per-field `k1`, `b`, weights; compute weighted virtual TF over fields. Standard for web titles vs body vs anchor text.

**Lucene quirk:** Lucene 8+ uses `BM25Similarity` with norms quantized to 1 byte (256 distinct length buckets), enabling impact precomputation for BMW. This discretization introduces small scoring artifacts but is imperceptible in ranking quality.

### 1.6 Phrase / Positional Search

Positional indexes store, per (term, doc), a sorted list of positions. To match "`new york`":
1. Intersect docIDs of `new` and `york`.
2. For each shared doc, scan position lists; find `pos_y - pos_n = 1`.

Variants:
- **Bigram index** — index "`new york`" as a single term. Fast but inflates dictionary ~50–100×.
- **Common-grams / shingling** — index 2-grams only for stop-words (frequent terms).
- **Proximity scoring** — BM25 augmented with span scores (BM25TP, Tao & Zhai 2007); Indri implements a structured query language for proximity.

### 1.7 Boolean vs Ranked Retrieval

Boolean (`A AND B AND NOT C`) returns an unscored set; ranked retrieval (BM25, LM, neural) returns a scored top-k. Modern engines unify both: Lucene `BooleanQuery` traverses cursors via DAAT and returns BM25-ranked top-k; pure-Boolean is a `ConstantScoreQuery`. Vespa exposes both modes under the same query interface.

### 1.8 Tokenization, Stemming, n-grams, Edit-Distance Search

**Stemming (Porter, Snowball, Krovetz)** — rule-based suffix stripping. Aggressive but fast.

**Lemmatization** — dictionary-based, returns linguistic root (`is/are/was → be`). Slower but precise.

**Subword: BPE / WordPiece / SentencePiece** — used by neural models (BERT). Falling into IR pipelines for sparse expansion (SPLADE).

**Edge n-grams** — index prefixes ("autocomplete"). Standard in Elasticsearch `edge_ngram` tokenizer filter.

**Character n-grams** — typo robustness; PostgreSQL `pg_trgm` uses trigrams.

**Fuzzy / Edit-Distance Search.**

| Technique | Where it shines | Notes |
|---|---|---|
| Trie + bounded edit-distance walk | Small dictionaries, k≤3 | Direct DP; simple |
| BK-tree (Burkhard-Keller 1973) | Mid-size, metric distance | Triangle-inequality pruning; counter-intuitively often *slower* than linear scan due to cache behavior (Garbe 2012 note) |
| Levenshtein automaton (Schulz & Mihov 2002) | Lucene FuzzyQuery | DFA for "all strings within k edits of q"; `k ≤ 2` in Lucene by default |
| FST × Levenshtein automaton intersection | Lucene 4.0+ | 100× faster than pre-4.0 fuzzy; idea due to Mike McCandless |
| SymSpell (Garbe) | Fastest dictionary spell-check | Pre-compute *deletes only* from dict and query; lookup in hash map; 6 orders of magnitude faster than naive edit-distance enumeration |
| Bit-parallel NFA (Wu & Manber) | Long patterns, k > 2 | `agrep` / `tre`; works on 64-bit words |
| n-gram filtering + verification | k-NN over strings | Q-gram index, count bound theorem (Jokinen & Ukkonen 1991) |

### 1.9 Succinct & Self-Indexes for Text

Used heavily in bioinformatics, log search, and Pinot/Druid string columns.

- **Suffix array (Manber & Myers 1990).** Sorted array of suffix starting positions. O(m log n) substring search. SA-IS (Nong, Zhang, Chan 2009) builds it in O(n).
- **Compressed Suffix Array (CSA, Grossi & Vitter 2000).** O(n H_k) bits; supports locate via `Ψ` function sampling.
- **FM-index (Ferragina & Manzini — FOCS 2000).** Substring count in O(m), locate one occurrence in O(log n). Built on BWT + rank/select on the BWT bitvector. Backbone of `bwa`/`bowtie2` for sequence alignment.
- **Wavelet tree (Grossi, Gupta, Vitter 2003).** Generalized rank/select for arbitrary alphabets; underlies modern FM-indexes for protein/DNA. Supports general character access in O(log σ) time. See Navarro, *Compact Data Structures*, Cambridge 2016.
- **r-index (Gagie, Navarro, Prezza — SODA 2018).** O(r) space where `r` = number of BWT runs. Practical for highly-repetitive text (1000-genomes project, Wikipedia edit history). Implemented in `pizzachili`, `sdsl-lite`.

### 1.10 Lucene / Elasticsearch / OpenSearch Internals

```
                  ┌─────────────────── Index ────────────────────┐
                  │                                              │
       Segment N (immutable) ◄── merging ──► Segment 1 (smallest)
       (.fdt .fdx .tim .tip .doc .pos .pay .dvd .vec ...)
                  │
                  ├── Term dictionary (BlockTreeTermsWriter)
                  │     • In-RAM FST over term-prefix → file offset
                  │     • On-disk blocks, 25–48 entries; floor blocks for >max
                  │
                  ├── Postings (Lucene90PostingsFormat)
                  │     • PFor blocks of 128 docs, frequencies, positions
                  │     • Skip data (impact + max-doc per block)
                  │
                  ├── Doc values (columnar, per-doc, for sort/agg/filter)
                  │
                  ├── BKD tree (Bkd-Tree, Procopiuc 2003) for numerics
                  │
                  └── HNSW vec format (Lucene 9.0+) — per-segment HNSW graph
```

**Refresh / commit / flush.** New documents go to an in-memory `IndexWriter` buffer; `refresh` (default 1 s in ES) flushes to a new segment + opens a new `Searcher`; `flush` fsyncs translog. Segments are immutable; deletes are tombstones in a `.liv` bitset until merge.

**TieredMergePolicy.** Lucene's default since 4.0. Tier-based, picks N segments minimizing reclaimed-deletes-cost; default `maxMergedSegmentMB = 5 GB`. In Elasticsearch, force-merge to 1 segment for read-only indices.

**BKD trees.** Multidimensional KD-tree variants for points, ranges, geo. Replace numeric tries since Lucene 6.0.

**KNN / vector search.** Lucene 9 added `KnnVectorsFormat` with HNSW (codec `Lucene99HnswVectorsFormat`); per-segment graph, search merges results across segments via priority queue. Quantization: int8 (Lucene 9.7+), int4 (10.0), 1-bit BBQ (10.x).

---

## 2. Approximate Nearest Neighbor (ANN) Search

### 2.1 Curse of Dimensionality

For random points in `[0,1]^d`, all pairwise distances concentrate around the same value as `d → ∞` (Beyer, Goldstein, Ramakrishnan, Shaft — ICDT 1999). Exact KNN past `d ≈ 20` is no faster than linear scan. ANN: find x with distance ≤ (1+ε) × optimal with probability ≥ 1-δ. Accept recall < 100% for massive speedup. Distance functions: L2 (Euclidean), inner product (MIPS), cosine (= normalized IP), L1, Hamming.

### 2.2 Locality-Sensitive Hashing (LSH)

A family `H` is `(r, cr, p_1, p_2)`-sensitive if `Pr[h(x)=h(y)] ≥ p_1` when `d(x,y) ≤ r` and `≤ p_2` when `d(x,y) > cr`.

| Distance | Hash family | Reference |
|---|---|---|
| Hamming | Random bit sample | Indyk & Motwani, STOC 1998 |
| L2 | Random Gaussian projection + binning (E2LSH) | Datar, Immorlica, Indyk, Mirrokni — SoCG 2004 |
| Cosine | Random hyperplane (signed sign of `w·x`) — SimHash | Charikar, STOC 2002; Manku & Das Sarma WSDM 2007 |
| Jaccard (sets) | MinHash | Broder, SEQUENCES 1997 |
| Inner Product | Asymmetric LSH (ALSH) | Shrivastava & Li, NeurIPS 2014 |
| Multi-probe LSH | Probe nearby buckets | Lv, Josephson, Wang, Charikar, Li — VLDB 2007 |

LSH guarantees provable recall but requires many tables for high recall on real data. Displaced by graph methods for in-memory ANN on dense vectors, but MinHash/SimHash remain dominant for large-scale near-duplicate detection (spam, plagiarism, Common Crawl dedup).

### 2.3 Tree-Based ANN

- **KD-tree (Bentley 1975).** Useless past d ≈ 20 (must visit nearly every leaf).
- **Ball tree.** Splits on ball boundary, slightly better; still curse-bound.
- **Random Projection trees (Dasgupta & Freund 2008).** Project, split at median; works for intrinsic-low-dim data.
- **Annoy (Bernhardsson 2015, Spotify).** Forest of random projection trees, mmap-friendly, easy to ship a baked index, but 30–50% lower recall/QPS than HNSW. Still alive for static recommender catalogs because of its disk story. No dynamic updates.
- **FLANN (Muja & Lowe 2009).** Auto-tuned mix of KD-trees + k-means tree.

### 2.4 Graph-Based ANN

State of the art for in-memory ANN on dense vectors.

**NSW — Navigable Small World (Malkov, Ponomarenko, Logvinov, Krylov — Information Systems 2014).** Build by greedy insert: connect new node to its `M` nearest neighbors in the partial graph. Search by greedy walk from random entry. "Small world" = short hop diameter. Log-scale complexity empirically.

**HNSW — Hierarchical NSW (Malkov & Yashunin — IEEE TPAMI 2018, arXiv 1603.09320).** Multi-layer NSW. Each node's max layer drawn from `Geom(1/ln(M))`. Search descends layers from the top, performing greedy + best-first with priority queue at each layer. Logarithmic expected hops.

```
        layer 2:       o─────o────o            (very few nodes, long edges)
                       │           │
        layer 1:    o──o──o──o─────o──o        (fewer nodes, longer edges)
                    │  │  │  │     │  │
        layer 0:  o─o──o──o──o──o──o──o─o─o    (all nodes, short edges)
                              ↑
                     greedy descent + ef beam search at each layer

Insert node v:
  l = floor(-ln(uniform(0,1)) × mL)   # layer assignment, mL = 1/ln(M)
  for layer = max_layer down to l+1:
    greedy search, keep ef=1 candidate
  for layer = l down to 0:
    find ef candidates via greedy search
    select M neighbors using heuristic (prefer diverse directions)
    add bidirectional links
```

Hyperparameters:
- `M` — max connections per node (default 16). Memory: `M * 4 bytes * N` for int32 IDs at layer 0, plus extra layers.
- `efConstruction` — beam width during build (default 200). Higher = better recall, slower build.
- `ef` (or `efSearch`) — beam width at query (default = k). Tune up for higher recall.
- "Heuristic neighbor selection" — diverse pruning (avoid cluster of co-located neighbors): for candidate c, add to neighbor list only if no existing neighbor w is closer to c than the new node. One of HNSW's key advantages over vanilla NSW.

Space: O(n × M × layers) ≈ O(n × M). For M=16, d=128 float32: ~100–200 bytes/vector overhead for graph structure.

**NSG — Navigating Spreading-out Graph (Fu, Xiang, Wang, Cai — VLDB 2019).** Approximation of MRNG (Monotonic Relative Neighborhood Graph) with a single navigating node. Lower memory, slightly faster than HNSW for sequential queries; very low out-degree (avg ~3–5 vs HNSW ~M); deployed at Taobao for billion-scale.

**Vamana / DiskANN (Subramanya, Devvrit, Kadekodi, Krishnaswamy, Simhadri — NeurIPS 2019).** Single-layer graph (no hierarchy) with α-RNG pruning (α ≥ 1; α > 1 increases shortcut edges). Designed so the graph fits on SSD with only PQ-compressed vectors in RAM. Beam search reads neighbors from SSD.

```
DiskANN search loop:
  candidate-set ← {entry node}
  while frontier not empty:
      best B candidates ← extract B closest to query (in RAM, via PQ distances)
      issue B parallel SSD reads for those nodes' adjacency lists + full vectors
      for each fetched neighbor: compute exact distance, push into priority queue
  return top-k
```

5–10× more points per node vs HNSW for billion-scale. SIFT1B at 95% recall@1, <3 ms mean latency on 16-core box with 64 GB RAM + NVMe SSD.

**FreshDiskANN (Singh et al. — arXiv 2105.09613).** Three-tier: long-term DiskANN on SSD + short-term in-memory delta + tombstone bitset. Periodic background merge. Deployed in Bing.

**IP-DiskANN (Krishnaswamy et al. — arXiv 2502.13826, 2025).** First *true in-place* update protocol for graph indexes — no batch consolidation. Each insert/delete completes locally with bounded graph-quality degradation.

**SPTAG / SPANN (Chen, Zhang, Yu et al. — NeurIPS 2021).** Microsoft's IVF-disk hybrid: in-RAM SPTAG over centroids; per-centroid posting list of full vectors on SSD. 2× DiskANN on billion-scale at 90% recall. Deployed at Bing.

**SPFresh (Xu et al. — SOSP 2023).** SPANN + LIRE (Lightweight Incremental REbalance) protocol for online vector inserts/deletes; ~1% DRAM and <10% cores vs full rebuild for 1% daily updates.

**CAGRA — CUDA ANNS Graph (Ootomo et al. — arXiv 2308.15136 / NVIDIA cuVS, 2024).** Fixed-degree flat graph built and searched on GPU. Uses Tensor Cores indirectly via batched distance kernels. 12× faster build, 4.7× faster online search than CPU HNSW; integrated into Faiss 1.10+, Milvus, Weaviate, OpenSearch 3.0.

**FilteredVamana / Stitched Vamana (Gollapudi, Karia et al. — Microsoft, WWW 2023).** "Filtered-DiskANN": graph edges aware of label sets; supports streaming inserts under label filters.

**ACORN (Patel, Kraft, Guestrin, Zaharia — Stanford, SIGMOD 2024).** Predicate-agnostic filtered ANN: extends HNSW with edge-list expansion at query time, denser graph during build, two-hop neighbor expansion. 2–1000× higher throughput at fixed recall vs pre/post-filter or per-predicate indexes. Integrated in Elasticsearch 9.1.

**SeRF — Segment Graph for Range-Filtered ANN (Zuo, Qiao, Zhou, Li, Deng — SIGMOD 2024).** Compresses `n` HNSW indexes for arbitrary range-filter queries into a single segment graph of the same size as one index; 2D version handles general ranges in `O(n log n)`.

### 2.5 Quantization

| Method | Bits/dim | Loss | Notes |
|---|---|---|---|
| Float32 | 32 | exact | baseline |
| Float16 / BF16 | 16 | tiny | bit-packed memory; FP16 needs care for L2 sums |
| Int8 (Scalar Quant) | 8 | small | per-vector min/max; FAISS, Lucene, Qdrant default |
| PQ (Jégou, Douze, Schmid — TPAMI 2011) | typically 8/M (e.g. 64/8 = 8 bits per subspace × M subvectors) | medium | Splits vector into M subvectors, k-means each into 256 centroids; lookup via 256×M codebook table |
| OPQ — Optimized PQ (Ge, He, Ke, Sun — CVPR 2013) | same as PQ | smaller | Pre-rotates with learned R to align with PQ's separable structure |
| AQ — Additive Quant (Babenko & Lempitsky CVPR 2014) | M·log K | smaller than PQ at same bits | Sum of M codewords from M codebooks; non-orthogonal subspaces |
| RQ — Residual Quant (Chen et al. 2010) | M·log K | small | Quantize, subtract centroid, repeat on residual |
| LSQ / TQ (Martinez, Hoos, Little — CVPR 2018) | M·log K | smallest | More expensive encode |
| Scalar Binary | 1 | large | Sign-only; 32× compression |
| RaBitQ (Gao & Long — SIGMOD 2024) | 1 (extended versions: 4–8) | small with theoretical bound | Random rotation + sign quantization with provable distance error bound; 32× compression with quality near SQ8 |
| Extended RaBitQ (Gao & Long — SIGMOD 2025) | 1.5–8 | provably near-optimal | Asymptotically optimal Euclidean quantization |
| BBQ — Better Binary Quant (Elastic, 2024–25) | 1 + 14 corrective bytes | small | RaBitQ-derived; production in Elasticsearch 8.16+ as `bbq_hnsw` |
| 8-bit Rotational Quant (Weaviate, 2025) | 8 + rotation | very small | Hadamard rotation + SQ8 |
| TurboQuant (2025) | 1–4 | small | Successor in the RaBitQ family |
| Anisotropic Vector Quantization — ScaNN (Guo, Sun, Lindgren, Geng, Simcha, Chern, Kumar — ICML 2020) | 4–8 (4-bit PQ) | smallest for MIPS | Loss penalizes parallel residual component; SOTA for inner-product search |

**Distance computation modes** (PQ):
- **SDC — Symmetric Distance Computation.** Both query and DB compressed; precompute `K×K` distance table per subspace once.
- **ADC — Asymmetric Distance Computation.** Query stays full-precision; precompute per-query `M×K` lookup table; sum `M` lookups per database point. Always preferred at recall.

### 2.6 IVF — Inverted File Index

Coarse quantizer (k-means with `nlist` centroids) partitions space; each vector lands in one centroid's posting list. Search probes the `nprobe` closest centroids.

| Variant | Posting payload | Memory | Recall |
|---|---|---|---|
| IVF-Flat | Full vectors | `4d N` bytes | exact within probed clusters |
| IVF-PQ (Jégou et al. 2011) | PQ codes | `M N` bytes (M = bytes/vec) | tunable; standard for billion-scale |
| IVF-PQ + ADC (FAISS `IndexIVFPQ`) | PQ codes | as above | the canonical recipe |
| IVF-PQFastScan (Andre, Kerboua-Benlarbi — ICDE 2015 / FAISS 2018) | 4-bit PQ rearranged for AVX2 | half size | ≥ 10× faster decode via SIMD shuffle |
| IVF-OPQ | OPQ codes | as PQ | tighter |
| IVF-RaBitQ | 1-bit per dim | ~`d/8 N` | near-int8 recall in 32× memory |

`nprobe` tuning: too low → recall drops sharply; too high → linear-scan-bad. Typical: `nprobe = 16–64` for `nlist = 4096` on 1M vectors; scale `nlist ≈ 4·sqrt(N)`.

### 2.7 ScaNN (Google)

Two-level + anisotropic-PQ:

```
[ Tree partitioning (nlist clusters) ] → [ AH — Asymmetric Hashing (4-bit PQ) ] → [ Re-rank top-r with full vectors ]
```

The `Anisotropic` loss biases quantization to lose less along the direction of likely query inner-products. Wins ann-benchmarks for years.

**SOAR (Sun et al., NeurIPS 2023).** ScaNN extension with redundant orthogonal cluster assignments: each vector goes into multiple partitions whose centroid residuals are orthogonal, improving the `nprobe → recall` curve.

### 2.8 FAISS (Meta)

| Index name | Composition |
|---|---|
| `IndexFlatL2` / `IndexFlatIP` | Brute-force exact |
| `IndexIVFFlat` | IVF + full vectors |
| `IndexIVFPQ` | IVF + PQ |
| `IndexIVFScalarQuantizer` | IVF + SQ |
| `IndexHNSWFlat` | HNSW |
| `IndexHNSWPQ` | HNSW with PQ at leaves |
| `IndexNSG` | NSG |
| `IndexBinaryFlat`/`IndexBinaryHash` | Binary vectors (Hamming) |
| `IndexPreTransform(OPQMatrix, …)` | Rotate before quantization |

GPU FAISS provides `GpuIndexFlat`, `GpuIndexIVFFlat`, `GpuIndexIVFPQ`. Faiss 1.10+ supports `cuVS` backends for IVF-PQ, IVF-Flat, Flat, CAGRA — build on GPU, deploy on CPU.

### 2.9 Filtering during ANN

| Strategy | Cost model | Best when |
|---|---|---|
| Pre-filter then exact | O(filtered_set_size) | High selectivity (small filtered set) |
| Post-filter (search top-k', filter to k) | Recall collapses if filter is selective | Low selectivity |
| Per-predicate index (per-tenant HNSW) | Memory O(N·tenants) | Small tenant fan-out |
| In-graph filtering (HNSW with filter at expand) | Practical default; degrades on selective predicates | Medium selectivity |
| Filtered-DiskANN | Edges constrained by label set | Static label sets |
| ACORN (Stanford, SIGMOD 24) | Graph designed for arbitrary predicates | Production hybrid search |
| SeRF (SIGMOD 24) | Segment graph for range filters | Range / numeric filters |
| Curator (arXiv 2401.07119) | Shared clustering tree, per-tenant subtrees | Multi-tenant SaaS |

### 2.10 Recall / QPS / Memory Tradeoffs (typical, dim=768, 1M vectors)

| Index | Memory | Build time | QPS @ recall=0.95 | Notes |
|---|---|---|---|---|
| Flat | 3.0 GB | 0 | 50 (k=10) | exact |
| HNSW(M=16, ef=200) | 3.5 GB | 5 min | 4000 | gold standard small |
| HNSW + SQ8 | 0.8 GB | 5 min | 5000 | minor recall loss |
| HNSW + BBQ | 0.16 GB + corrective bytes | 5 min | 8000 | rerank with float |
| IVF-PQ (nlist=4096, m=64) | 100 MB | 10 min | 6000 | larger drop at recall>0.95 |
| DiskANN | 200 MB RAM, 4 GB SSD | 1 hr | 1500 | scales to 1B; SSD-bound |
| CAGRA (GPU) | 4 GB GPU | 30 s | 100 000+ | small batches |

Numbers are illustrative — see `ann-benchmarks.com` and `big-ann-benchmarks` for real curves.

---

## 3. Sparse & Hybrid Search

### 3.1 Learned Sparse Retrieval (LSR)

Goal: keep BM25's inverted-index efficiency but learn term weights and term expansions from data.

- **DeepCT (Dai & Callan, SIGIR 2019).** BERT predicts a context-aware TF for each term.
- **DeepImpact (Mallia, Khattab, Suel, Tonellotto, SIGIR 2021).** Learns impact scores; fully replaces BM25.
- **uniCOIL (Lin & Ma, arXiv 2106.14807).** Single weight per token; trained with MSMARCO; serves on a vanilla inverted index.
- **SPLADE (Formal, Piwowarski, Clinchant — SIGIR 2021; SPLADE v2 — arXiv 2109.10086, 2021; SPLADE-v3 — arXiv 2403.06789, 2024).** Uses BERT MLM head logits passed through `log(1 + ReLU(x))` and pooled (max in v2). Adds FLOPS regularizer for sparsity. Achieves dense-quality on BEIR while serving over an inverted index.

```
SPLADE(d)[v] = Σ_t log(1 + ReLU(BERT_MLM(d)_t[v]))   for each vocab term v
Result: 30K-dim sparse vector, ~100–300 nonzeros per document
```

- **TILDE / TILDEv2 (Zhuang & Zuccon, SIGIR 2021).** Document-side expansion, query-side BoW; very fast at query time.
- **CSPLADE (Amazon, AACL 2025).** SPLADE on causal LM; better effectiveness from decoder-only models.
- **SPLATE (2024).** Adapts frozen ColBERT token embeddings to sparse vocabulary space via learned MLM adapter. Enables ColBERT-quality retrieval on CPU via standard inverted index.

**Why LSR matters in production:** fits the existing Lucene/Tantivy/PISA ecosystem; can be served with WAND/BMW; predictable latency tail; explainable (term-by-term attribution).

### 3.2 ColBERT — Late Interaction

**ColBERT (Khattab & Zaharia — SIGIR 2020).** Encode query and document into per-token vectors. Score is `MaxSim`:

```
s(q, d) = Σ_i max_j ⟨q_i , d_j⟩
```

Captures token-level interaction without crossing the bi-encoder boundary. ~10× more storage than dense (per-token vectors) but much higher accuracy.

**ColBERTv2 (Santhanam, Khattab, Saad-Falcon, Potts, Zaharia — NAACL 2022).** Centroid-based compression: cluster all token embeddings into K centroids; store `(centroidID, residual)` per token, residual quantized to 1–2 bits.

**PLAID (Santhanam, Khattab, Potts, Zaharia — CIKM 2022).** Centroid pruning: invert as `centroid → passages`; prune low-scoring passages via centroid scores; rerank with full MaxSim. 7× GPU / 45× CPU speedup vs ColBERTv2. Tens of ms latency for 140 M passages.

**ColPali (Faysse et al. — arXiv 2407.01449, 2024).** ColBERT-style late interaction over PaliGemma vision-language tokens; SOTA on document-image retrieval (ViDoRe). Supported in Elasticsearch 9, Vespa, Qdrant.

**PyLate (2024).** Flexible training/retrieval library for late-interaction models.

### 3.3 Hybrid Fusion

**Score fusion (linear combination):** `s = α·s_dense + (1−α)·s_sparse`. Requires score normalization (z-score, min-max). Brittle when distributions shift.

**RRF — Reciprocal Rank Fusion (Cormack, Clarke, Buettcher — SIGIR 2009):**

```
RRF(d) = Σ_r 1 / (k + rank_r(d))           with k typically = 60
```

Rank-based, distribution-free, zero-tuning baseline. Standard in OpenSearch, Elastic, Azure AI Search, Milvus, Weaviate, Vespa, pgvector via `pgvectorscale`/`pgvector-rrf`. Works regardless of how many retrievers (dense + BM25 + SPLADE + ColPali + …).

**Convex / weighted RRF, normalized RRF.** Tuned per-corpus.

**ConvexCombination, BordaCount, CombSUM, CombMNZ.** Older but still occasionally optimal on specific domains.

### 3.4 Reranking

| Stage | Architecture | Latency | Quality |
|---|---|---|---|
| First-stage (10⁹ → 1000) | Sparse / dense / hybrid | ~ms | medium |
| Second-stage (1000 → 100) | Cross-encoder MiniLM/BERT | 10–100 ms/100 docs | high |
| Late-stage (100 → top-k) | Listwise (RankT5, RankGPT, ListT5, RankZephyr) | 100–1000 ms | highest |

**MonoT5 (Nogueira, Jiang, Pradeep, Lin — Findings of EMNLP 2020).** T5 generates token "true"/"false"; relevance = `P(true)`.

**DuoT5 (Pradeep, Nogueira, Lin — arXiv 2101.05667).** Pairwise: T5 says whether `d_i` is more relevant than `d_j`. Run on top-50 from MonoT5.

**RankT5 (Zhuang, Qin, Jagerman, Hui, Ma, Wang, Bendersky — SIGIR 2023).** Ranking losses (softmax / pairwise / listwise) instead of language-modeling.

**ListT5 (Yoon et al. — ACL 2024).** Listwise tournament-sort over Fusion-in-Decoder T5; SOTA on BEIR subsets.

**RankGPT / RankLLaMA / RankZephyr.** Listwise prompting of LLMs; RankGPT (Sun et al. EMNLP 2023) showed GPT-4 in zero-shot beats supervised cross-encoders on BEIR.

---

## 4. Semantic / Neural Search (Dense Bi-Encoders)

### 4.1 Foundational Models

- **DSSM (Huang et al. CIKM 2013).** First neural retrieval (Microsoft).
- **DPR — Dense Passage Retrieval (Karpukhin, Oguz, Min, Lewis, Wu, Edunov, Chen, Yih — EMNLP 2020).** BERT-base bi-encoder + in-batch negatives + hard-negatives mining. Dethroned BM25 on Open-Domain QA. Score = dot product of [CLS] representations.
- **ANCE (Xiong et al., ICLR 2021).** Approximate nearest-neighbor hard negatives drawn periodically from the index during training.
- **Contriever (Izacard et al., 2021).** Unsupervised contrastive on Wikipedia spans.
- **SimCSE (Gao, Yao, Chen — EMNLP 2021).** Dropout twice → positive pair; cornerstone of modern contrastive sentence encoders.
- **GTR / Sentence-T5 (Ni et al., 2021).** T5-encoder bi-encoders.
- **E5 (Wang et al. — Microsoft, arXiv 2212.03533).** Two-stage: weakly-supervised on CCPairs, then fine-tuned with hard negatives. e5-mistral-7b is a top MTEB model.
- **BGE — BAAI General Embeddings (BGE-large-en, BGE-M3).** BGE-M3 (Chen et al., 2024) does dense + sparse + multi-vector all from one model, multilingual.
- **GTE — General Text Embeddings (Alibaba).** Compact (gte-large-en-v1.5) and very strong.
- **Nomic-embed-text-v1 / v2 (Nussbaum et al., 2024).** First fully reproducible long-context (8 K) open-weights embedder; training data + code released. v2 introduces sparse MoE.
- **NV-Embed (NVIDIA, 2024 / 2025).** Llama-derived, MTEB SOTA.
- **Gemini Embedding 2 / Gemini-Embedding (Google, 2025).** Multimodal text/image/audio/PDF/video at the embedding level.
- **Arctic-Embed (Snowflake, 2024).** Strong long-context retrieval.
- **Cohere Embed v3 / v4.** Strong proprietary multilingual.
- **OpenAI text-embedding-3-small / large.** Production-tier; supports MRL truncation.

### 4.2 Training Recipes

```
1. Two-stage:
   stage 1  weakly supervised, billions of (q, d⁺) pairs (web, Reddit, QnA)
   stage 2  fine-tune on labeled triples (q, d⁺, hard d⁻) with InfoNCE
2. Loss: InfoNCE / contrastive softmax
        L = -log [ exp(s(q,d⁺)/τ) / Σ_d exp(s(q,d)/τ) ]
3. Hard-negatives mining
   • BM25 hard negatives (top BM25 hits that are NOT relevant)
   • ANCE: refresh negatives from current model's nearest neighbors
   • SimANS / NV-Retriever: positive-aware negative selection
4. In-batch negatives + cross-batch (MoCo) for huge effective batch sizes
5. Distillation from cross-encoder (RocketQA, RocketQAv2, Margin-MSE)
```

### 4.3 Matryoshka Representation Learning (MRL)

**Kusupati, Bhatt, Rege, Wallingford, Sinha, Ramanujan, Howard-Snyder, Chen, Kakade, Jain, Farhadi — NeurIPS 2022 (arXiv 2205.13147).** Train so that each prefix `[d_1 ... d_{d_i}]` is by itself a usable embedding. Each prefix nests like a Russian doll. At inference, truncate to whatever dimension the budget allows — no retraining. Used by OpenAI text-embedding-3, Cohere v3+, Gemini Embedding, BGE-M3 partly. Up to 14× smaller embedding for the same accuracy.

### 4.4 MTEB / MMTEB

**MTEB (Muennighoff, Tazi, Magne, Reimers — EACL 2023).** ~50 tasks across classification, clustering, reranking, retrieval, STS, summarization, pair classification, bitext mining; in 2024–2025 expanded to **MMTEB** (over 500 tasks, 1000+ languages, including instruction-following and long-document retrieval, OpenReview 2025).

Top of MTEB English Retrieval (as of mid-2025):
- Gemini Embedding (Google)
- NV-Embed (NVIDIA)
- e5-mistral-7b-instruct
- BGE-M3 (multilingual)
- gte-Qwen2-7B-instruct
- Stella, Linq, BGE-en-icl, Jasper
- mxbai-embed-large-v1 (MixedBread)

The leaderboard moves weekly; treat any specific ranking as ephemeral.

### 4.5 Multi-Vector

ColBERT family (above) is the canonical multi-vector approach. **ColPali / ColQwen / ColGemma** extend to vision tokens.

### 4.6 Multi-Modal Embeddings

- **CLIP (Radford et al. — Learning Transferable Visual Models From Natural Language Supervision, ICML 2021).** Image-text contrastive bi-encoder; zero-shot ImageNet classification by similarity.
- **SigLIP (Zhai, Mustafa, Kolesnikov, Beyer — ICCV 2023).** Pairwise sigmoid loss instead of softmax; trains larger batches without needing global softmax.
- **SigLIP 2 (Tschannen et al. — Google, arXiv 2502.14786, Feb 2025).** Adds captioning, self-distillation, masked prediction; strong multilingual.
- **MobileCLIP / MobileCLIP2 (Apple, CVPR 2024 / TMLR 2025).** Edge-deployable.
- **EVA-CLIP, OpenCLIP** (LAION).
- **BLIP-2, LLaVA, InternVL.** Generative VLMs with retrievable image features.
- **Cross-modal retrieval pipelines** typically: encode image → vector, encode text → vector in same space, ANN.

---

## 5. Long Context, Late Chunking, Contextual Retrieval

**Chunking** is still a primary lever for RAG quality.

| Strategy | Pros | Cons |
|---|---|---|
| Fixed-size (e.g. 512 tokens, 50 overlap) | trivial, predictable | breaks context |
| Recursive / heading-aware (LangChain `RecursiveCharacterTextSplitter`) | respects document structure | requires parsing |
| Semantic chunking (boundary at cosine drop) | better recall (~9% vs fixed) | needs embedder, slower |
| LLM-based / agentic chunking | best for complex docs | costly per-doc |
| Long RAG (use whole sections / pages) | preserves context | bigger context window needed |

**Late chunking (Günther et al. — Jina AI, arXiv 2409.04701, 2024).** Run the long-context encoder over the whole document first, *then* pool to chunks. The chunk vectors get cross-chunk context for free. No retraining required.

**Contextual Retrieval (Anthropic, Sept 2024).** Prepend an LLM-generated chunk-specific summary to each chunk before embedding (and BM25-indexing). +35% retrieval accuracy at top-20. Combined with hybrid search + reranking, claims −67% retrieval failure rate. Scales via Claude prompt-caching at ~$1/M chunks.

**Hierarchical retrieval / RAPTOR (Sarthi et al. — Stanford 2024).** Cluster chunks, summarize cluster, recurse → tree of summaries; route query to right level.

---

## 6. Production Vector Search Systems

### 6.1 Architecture Taxonomy

```
                  ┌──────────────────────────────────────────────────┐
                  │ Vector DB Architectural Patterns                  │
                  │                                                    │
  Standalone:     │  Client → [Index + Storage] → Result             │
                  │  (pgvector, FAISS, Qdrant standalone)             │
                  │                                                    │
  Distributed:    │  Client → Proxy → [QueryNode × N] ← [DataNode]  │
                  │                        ↑                          │
                  │               [ObjectStorage + WAL]               │
                  │  (Milvus, Elasticsearch, Weaviate distributed)    │
                  │                                                    │
  Serverless:     │  Client → Router → [Compute Pods] + [S3 Index]  │
                  │  (Pinecone serverless, Turbopuffer)               │
                  └──────────────────────────────────────────────────┘
```

### 6.2 Elasticsearch / OpenSearch

- Lucene-backed; KNN via per-segment HNSW from Lucene 9.
- BBQ binary quantization GA in 8.18 / 9.0 (2025); claims 5× faster than OpenSearch + FAISS at same recall.
- ACORN-derived filtered KNN in 9.1.
- ColPali / ColBERT support, JinaAI rerank module.
- OpenSearch 3.0 ships GPU CAGRA via cuVS as preview.

### 6.3 Milvus 2.x (Zilliz)

```
[ Proxy ] → [ Coordinator (Root/Query/Data/Index) ] → [ Workers (Query, Data, Index nodes) ]
                                  │
                                  ▼
                          [ Object storage: S3 / MinIO ]
                          [ WAL: Pulsar / Kafka ]
                          [ Metadata: etcd ]
```

- Disaggregated compute/storage, cloud-native.
- **Growing segments** in memory (no index, brute-force search) → **Sealed segments** with Knowhere index on object store.
- **Knowhere** is the algorithmic engine: wraps FAISS, hnswlib, DiskANN, CAGRA, ScaNN-style; per-segment build.
- 2024+: Cardinal engine, GPU CAGRA, multi-vector hybrid, JSON filtering, Inverted index for keyword (Tantivy-based).
- Scale: single Milvus cluster handles 10B+ vectors with horizontal scaling of query nodes.

### 6.4 Qdrant

- Pure Rust; SIMD; custom Gridstore engine.
- HNSW with filter-during-traversal (no pre/post split); payload-aware HNSW groups matching-payload nodes during construction.
- Scalar + binary + product quantization, async indexing, rescoring from full precision.
- On-disk HNSW: mmap-backed for large datasets.
- 2025: GPU HNSW indexing, inline storage embeds quantized vectors in graph nodes, 1.5/2-bit quant, asymmetric quant, sparse vectors, multivector (ColBERT).

### 6.5 Weaviate

- Go implementation. HNSW + BM25 (Okapi BM25 via inverted index).
- **Module system**: text2vec-openai, text2vec-cohere, multi2vec-clip, etc. Auto-embedding at index time.
- **Hybrid search**: BM25 + vector with α weighting or RRF. Single API.
- Multi-tenancy: per-tenant HNSW shards (50K+ tenants supported); ACORN integration.
- 8-bit Rotational Quantization (RaBitQ-based) 2025 release; cuVS CAGRA build path.

### 6.6 Pinecone

- Original closed-source serverless vector DB.
- **Pod-based**: provisioned compute pods (s1/p1/p2); in-memory HNSW per pod. Fixed capacity.
- **Serverless (2024 GA)**: separates write, read, storage. Slabs (immutable indexed files) on S3-like. DiskANN-style streaming access. 10–100× cheaper for intermittent workloads. Higher latency (10–50ms vs 1–5ms pods).

### 6.7 Vespa

- Yahoo's open-source engine (now Vespa.ai). One engine for tensors, BM25, attributes, ANN (HNSW).
- **Tensor framework**: arbitrary tensor expressions in ranking; runs ML models in rank profiles (XGBoost, ONNX, lightGBM, custom). True multi-vector ranking (ColBERT, ColPali) with first-class tensor MaxSim.
- **Phased ranking** (first-phase, second-phase, global-phase) with per-phase budget.
- **Streaming search** (visit-only mode with no index, used at Yahoo Mail).
- **Online HNSW**: index built online as data is ingested; no separate offline build phase.

### 6.8 pgvector

- PostgreSQL extension. v0.5 added HNSW; v0.7 added halfvec (FP16), bit, sparsevec; v0.8 (2024) optimization for filtering.
- IVFFlat (needs initial training data) and HNSW indexes.
- Limitations: 2000-dimension hard limit for indexed columns; HNSW build single-writer in older versions (0.6+ adds parallel build); VACUUM expensive for HNSW; filtered queries can return < k rows when filter selectivity is low.
- **pgvectorscale (Timescale)** — extension wrapping DiskANN-style algorithms, StreamingDiskANN; SBQ binary quantization.
- **paradedb / pg_search** — Lucene/Tantivy-style BM25 inside Postgres.

### 6.9 Others

| System | Index | Unique Feature |
|---|---|---|
| Chroma | HNSW (hnswlib) | Embedded, Python-native, RAG tooling; rewriting in Rust |
| LanceDB | DiskANN (Lance columnar format) | Serverless, columnar storage, multimodal; Lance format inspired by Arrow |
| Marqo | HNSW + BM25 | End-to-end embedding + search |
| Redis VSS | HNSW / Flat | In-memory, Redis modules ecosystem |
| MongoDB Atlas | HNSW (mongot) | Co-located with document store |
| Turbopuffer | S3-resident index | Serverless, $0.33/GB/mo, BM25+vector |
| Vald (Yahoo Japan) | NGT graph | K8s-native |
| Quickwit | Tantivy-based | Rust, log/observability search, S3-backed |
| Tantivy | Custom Lucene-alike | Rust library; ~2–6× Lucene at single-node bench; backbone of Quickwit, ParadeDB |

---

## 7. GPU-Accelerated Vector Search

### 7.1 Why GPU

Brute-force IP / L2 = batched matmul = the GPU's natural workload. Tensor Cores push >1 PFLOP/s on H100. For *small batches* (single-query latency), graph algorithms give better latency than BLAS due to PCIe + launch overhead.

### 7.2 FAISS GPU

- `GpuIndexFlat`: blocked matrix-matrix; near peak HBM bandwidth.
- `GpuIndexIVFFlat`/`GpuIndexIVFPQ`: bigger-than-HBM via sharding; multi-GPU via `IndexShards`.
- 2024: optional cuVS backend for IVF-PQ, IVF-Flat, Flat, CAGRA — 2× faster than legacy GPU FAISS for many workloads, builds in seconds.
- Batch throughput: 1M+ QPS for IVFPQ with batch_size=1024 on A100. Use case: offline batch embedding indexing, large-scale recall benchmark generation.

### 7.3 cuVS / RAFT (NVIDIA)

cuVS = "CUDA Vector Search," the productized successor to RAFT's ANN module. Provides:
- CAGRA (graph)
- GPU IVF-Flat / IVF-PQ
- Brute force (Flat)
- DiskANN (CPU search via cuVS-built graph)
- HNSW (CPU search; cuVS can build then export)

Adopted by Milvus, Weaviate (v1.25+), OpenSearch, Elasticsearch, Faiss.

### 7.4 CAGRA Algorithm

1. Build initial NN graph on GPU via batched k-NN.
2. Reverse-edge pruning to enforce fixed degree.
3. Search: parallel multi-start beam search; threads cooperate within a query; Tensor Cores for distance batches.
4. Tightly bound register usage so each warp processes a query.

```
For each query (warp-level):
  initialize beam from random/seed nodes
  while not converged:
    for each frontier node in parallel: load neighbors, compute distances (FP16/FP32)
    update visited bitmap
  emit top-k
```

Performance (H100, 100M float32 / d=768): build in seconds, search at hundreds of thousands QPS at recall@10 ≥ 95%. Construction: 2.2–27× faster than HNSW; search (large batch): 33–77× faster than HNSW in 90–95% recall range. Single-query: use CPU HNSW.

| Scenario | Recommendation |
|---|---|
| Offline index build, large corpus | CAGRA / GPU FAISS IVF build → export to CPU |
| Online single-query serving | CPU HNSW (DiskANN for large) |
| High-throughput batch serving (LLM retrieval, recs) | GPU FAISS IVFPQ or CAGRA |
| Memory-constrained billion-scale | DiskANN on NVMe |

---

## 8. State of the Art and 2024–2025 Highlights

| Theme | Notable work | Where |
|---|---|---|
| Quantization | RaBitQ (Gao & Long 2024), Extended RaBitQ (2025), BBQ Elastic | SIGMOD'24/'25, blog |
| Filtered ANN | Filtered-DiskANN, ACORN, SeRF, UNIFY, Compass | WWW'23, SIGMOD'24, arXiv |
| Streaming/fresh | FreshDiskANN, SPFresh (SOSP 2023), IP-DiskANN (2025), Cosmos DB DiskANN | SOSP, arXiv |
| Multi-tenant | Curator (Jin et al. 2024), Weaviate native MT | arXiv 2401.07119 |
| GPU | CAGRA + cuVS, GPU FAISS rewrite | NVIDIA blog, Faiss 1.10 |
| Multi-vector | ColBERTv2 + PLAID, ColPali/ColQwen, RAGatouille | NAACL 22, CIKM 22, ECCV 24 |
| Late chunking | Jina late chunking (arXiv 2409.04701) | 2024 |
| Contextual RAG | Anthropic Contextual Retrieval 2024 | Anthropic blog |
| Sparse | SPLADE-v3 (March 2024), CSPLADE (2025) | arXiv |
| Embeddings | Gemini Embedding 2 (multimodal), NV-Embed v2, BGE-M3, Nomic v2, Arctic-Embed-L 2.0 | 2024–25 |
| Long-context | nomic-embed-text-v1 (8K), bge-m3 (8K), Voyage 32K | 2024 |
| Learned indexes for ANN | LMI, RMI for partitioning | active research |
| RAG architectures | RAPTOR, GraphRAG (Microsoft 2024), HyDE, Self-RAG, Corrective RAG, agentic chunking | 2024–25 |

---

## 9. Evaluation

### 9.1 Metrics

| Metric | Definition | Use |
|---|---|---|
| Recall@K | fraction of true relevants retrieved in top-K | retrieval / ANN |
| Precision@K | fraction of top-K that are relevant | classification flavor |
| MRR (Mean Reciprocal Rank) | mean of `1/rank` of first relevant | factoid QA |
| MAP (Mean Average Precision) | mean over queries of AP | TREC ad hoc |
| NDCG@K (Normalized Discounted Cumulative Gain) | graded relevance, log-discounted | web search standard |
| nDCG-binary | NDCG with 0/1 grades | BEIR default |
| Spearman / Pearson | for STS tasks | embedding semantic similarity |
| Hit-rate / Success@K | did any relevant make top-K | recommender |
| QPS / latency p50/p95/p99 | system metric | production |

### 9.2 Benchmarks

- **BEIR (Thakur, Reimers, Rücklé, Srivastava, Gurevych — NeurIPS Datasets 2021).** 18 zero-shot retrieval datasets across domains (TREC-COVID, NQ, FiQA, SciFact, ArguAna, …). Standard for evaluating dense retrievers out of distribution. Key finding: BM25 strong baseline on many domains.
- **MTEB / MMTEB.** Embedding tasks at scale.
- **MS MARCO** (Microsoft, 2016). ~8.8M passage corpus; the de-facto training/eval set for retrieval.
- **TREC Deep Learning Tracks (2019–present).** Annual TREC tracks built on MS MARCO.
- **LoCoMo, NarrativeQA, LongBench** — long-document retrieval/QA.
- **ann-benchmarks.com** — recall vs QPS curves for in-memory ANN indexes; the canonical chart for "is your library competitive?"
- **Big-ANN-Benchmarks** (Simhadri et al., NeurIPS 2021 / NeurIPS 2023 competition). Billion-scale and practical variants: filtered, sparse, OOD, streaming. NeurIPS'23 had 26 entries across 4 tracks.

### 9.3 Latency Anatomy (web-scale RAG, target 500 ms wall clock)

```
Query embedding             5–15 ms (small dense model on GPU)
ANN search (top 200)        5–30 ms (HNSW or DiskANN single shard)
BM25 / SPLADE search        2–10 ms (PISA / Tantivy / Lucene)
Fusion + dedupe             <1 ms
Cross-encoder rerank top 50 30–80 ms (MiniLM)
LLM generation             100–300 ms (TTFT) + streaming
```

---

## 10. Decision Cheat Sheet

| Need | First choice |
|---|---|
| Few-million in-memory dense vectors, low latency | HNSW (Lucene / hnswlib / Qdrant) |
| Billions of vectors, single node, SSD | DiskANN (or SPANN) + PQ |
| Billions of vectors, GPU available | cuVS CAGRA + IVF-PQ |
| Cost is dominant, recall ≥ 0.95 acceptable | IVF-PQ or BBQ + reranking |
| Filtered search with arbitrary predicates | ACORN or filter-during-HNSW + post-rerank |
| Range filters on numeric attributes | SeRF / UNIFY |
| Multi-tenant SaaS (millions of tenants) | Per-tenant shards (Weaviate) or Curator |
| Streaming inserts/deletes | FreshDiskANN / SPFresh / IP-DiskANN |
| Need rerank on top of vector | Cross-encoder (MiniLM/MonoT5) → ListT5/RankZephyr if budget allows |
| RAG end-to-end | Hybrid (BM25 + dense), RRF, contextual chunking, cross-encoder rerank |
| Lucene-incompatible greenfield in Rust | Tantivy + Qdrant (or LanceDB if columnar AI lakehouse) |
| Postgres-only stack, modest scale | pgvector (HNSW) + pgvectorscale + paradedb for BM25 |

---

## 11. Pitfalls & Production Lessons

1. **Inner product vs cosine vs L2.** Normalize once at ingest. Mixing kernels across stages silently halves recall.
2. **Score fusion vs rank fusion.** Score normalization is brittle across distributions; RRF "just works." Migrate to convex combo only after measuring.
3. **Filtering selectivity.** Pre-filter is fastest when the filtered set is small enough for exact scan; HNSW with naive post-filter collapses recall when filter is selective.
4. **HNSW + deletes.** Without a custom delete primitive, deletes mark tombstones; recall stays fine but disk grows. Periodic full rebuild or use FreshDiskANN/SPFresh-style updaters.
5. **`ef`, `nprobe`, `k_oversample` tuning.** Always plot recall-vs-latency curves on your data. ann-benchmarks defaults are not your defaults.
6. **PQ on small dimensions.** PQ works best when `d ≥ 64`. For d=32 vectors, just use scalar quant or flat.
7. **Small-batch GPU.** GPU shines at >32 queries/batch. For interactive single-query, CPU HNSW often ties or wins.
8. **Cold-cache latency.** mmap'd HNSW from SSD on first query can be 50× slower than warm. Pre-warm or pin into RAM.
9. **Embedder drift.** When you upgrade your embedding model, the *whole* index must be rebuilt. Plan storage + downtime.
10. **Token-level multi-vector storage.** ColBERT × 100 tokens × 128 dim × 2 bytes = 25 KB/passage. Plan accordingly; PLAID compression is mandatory at scale.
11. **Lucene merge storms.** Bulk inserts → many tiny segments → merge thrashing; tune `index.merge.scheduler.max_thread_count`, batch by ingest pipeline.
12. **BBQ / RaBitQ recall floor.** Always pair binary quantization with re-rank against full-precision vectors for the final top-k (oversample 5–10×).
13. **BK-tree cache behavior.** BK-trees have poor cache locality for large dictionaries — linear scan often beats them in practice for vocabularies < 1M. Profile before choosing.
14. **SPLADE/learned-sparse index size.** SPLADE generates ~100–300 nonzeros per document in a 30K vocabulary; a 10M doc corpus → ~3B nonzero entries. IDF-pruning during indexing (drop terms with weight < ε) can reduce this 5–10×.

---

## Key References

### Inverted Index, BM25, Top-k Retrieval

1. Ferragina, Manzini, "Opportunistic data structures with applications" — FOCS 2000 (FM-index).
2. Robertson, Walker, Jones, "Okapi at TREC-3" — TREC 1994 (BM25).
3. Lv, Zhai, "Lower-bounding term frequency normalization" — CIKM 2011 (BM25+, BM25L).
4. Robertson, Zaragoza, Taylor, "Simple BM25 extension to multiple weighted fields" — CIKM 2004 (BM25F).
5. Broder, Carmel, Herscovici, Soffer, Zien, "Efficient query evaluation using a two-level retrieval process" — CIKM 2003 (WAND).
6. Turtle, Flood, "Query evaluation: Strategies and optimizations" — IPM 1995 (MaxScore).
7. Ding, Suel, "Faster top-k document retrieval using block-max indexes" — SIGIR 2011 (BMW).
8. Mallia, Ottaviano, Porciani, Tonellotto, "Faster blockmax WAND with variable-sized blocks" — SIGIR 2017 (VBMW).
9. Mallia, Khattab, Suel, Tonellotto, "Faster and more robust top-k retrieval" — SIGIR 2020 (LazyBM).
10. Lin, Trotman, "Anytime Ranking for Impact-Ordered Indexes" — ICTIR 2015 (JASS/SAAT).
11. Ottaviano, Venturini, "Partitioned Elias-Fano indexes" — SIGIR 2014.
12. Pibiri, Venturini, "Clustered Elias-Fano indexes" — TOIS 2017.
13. Pibiri, Venturini, "Techniques for Inverted Index Compression" — ACM Computing Surveys 2021.
14. Lemire, Boytsov, "Decoding billions of integers per second through vectorization" — SP&E 2015.
15. Chambi, Lemire, Kaser, Godin, "Better bitmap performance with Roaring bitmaps" — SP&E 2016 / arXiv 1402.6407.
16. Trotman, "Compressed Integer Lists" — SPIRE 2014 (QMX).
17. Schulz, Mihov, "Fast string correction with Levenshtein automata" — IJDAR 2002.
18. Garbe, "1000× Faster Spelling Correction" — SymSpell, blog/code 2012–.
19. Navarro, "Compact Data Structures: A Practical Approach" — Cambridge, 2016.
20. Gagie, Navarro, Prezza, "Optimal-Time Text Indexing in BWT-runs Bounded Space" — SODA 2018 (r-index).
21. Grossi, Gupta, Vitter, "High-Order Entropy-Compressed Text Indexes" — SODA 2003 (wavelet tree).

### Graph ANN

22. Malkov, Ponomarenko, Logvinov, Krylov, "Approximate nearest neighbor algorithm based on navigable small world graphs" — Information Systems 2014.
23. Malkov, Yashunin, "Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs" — TPAMI 2018 / arXiv 1603.09320.
24. Fu, Xiang, Wang, Cai, "Fast Approximate Nearest Neighbor Search With The Navigating Spreading-out Graph" — VLDB 2019 (NSG).
25. Subramanya, Devvrit, Kadekodi, Krishnaswamy, Simhadri, "DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node" — NeurIPS 2019.
26. Singh, Subramanya, Krishnaswamy, Simhadri, "FreshDiskANN" — arXiv 2105.09613.
27. Krishnaswamy, Sankar, Singh, Simhadri, "In-Place Updates of a Graph Index for Streaming ANN Search" — arXiv 2502.13826, 2025 (IP-DiskANN).
28. Gollapudi et al., "Filtered-DiskANN: Graph Algorithms for ANN Search with Filters" — WWW 2023.
29. Chen, Zhang, Yu, Shao, Liu, Chen, Zhao, Wang, Yang, "SPANN: Highly-efficient Billion-scale ANN Search" — NeurIPS 2021.
30. Xu, Liang, Lou, Zhang, Wang, Cui, Yang, Chen, "SPFresh: Incremental In-Place Update for Billion-Scale Vector Search" — SOSP 2023.
31. Patel, Kraft, Guestrin, Zaharia, "ACORN: Performant and Predicate-Agnostic Search Over Vector Embeddings and Structured Data" — SIGMOD 2024 / arXiv 2403.04871.
32. Zuo, Qiao, Zhou, Li, Deng, "SeRF: Segment Graph for Range-Filtering ANN Search" — SIGMOD 2024.
33. Jin, Liu, Zhang, Krishnaswamy, "Curator: Efficient Indexing for Multi-Tenant Vector Databases" — arXiv 2401.07119, 2024.
34. Ootomo, Naruse, Nolet, Wang, Feng, Zhu, "CAGRA: Highly Parallel Graph Construction and ANN Search for GPUs" — arXiv 2308.15136 / NVIDIA cuVS, 2024.
35. Simhadri et al., "Results of the Big ANN: NeurIPS'23 competition" — arXiv 2409.17424.

### LSH / Tree

36. Indyk, Motwani, "Approximate Nearest Neighbors: Towards Removing the Curse of Dimensionality" — STOC 1998.
37. Datar, Immorlica, Indyk, Mirrokni, "Locality-Sensitive Hashing Scheme Based on p-Stable Distributions" — SoCG 2004 (E2LSH).
38. Charikar, "Similarity Estimation Techniques from Rounding Algorithms" — STOC 2002 (SimHash).
39. Broder, "On the resemblance and containment of documents" — SEQUENCES 1997 (MinHash).
40. Lv, Josephson, Wang, Charikar, Li, "Multi-probe LSH" — VLDB 2007.
41. Bernhardsson, "Annoy: Approximate Nearest Neighbors Oh Yeah" — open source, Spotify, 2015–.

### Quantization

42. Jégou, Douze, Schmid, "Product Quantization for Nearest Neighbor Search" — TPAMI 2011.
43. Ge, He, Ke, Sun, "Optimized Product Quantization" — CVPR 2013.
44. Babenko, Lempitsky, "Additive Quantization for Extreme Vector Compression" — CVPR 2014.
45. Guo, Sun, Lindgren, Geng, Simcha, Chern, Kumar, "Accelerating Large-Scale Inference with Anisotropic Vector Quantization" — ICML 2020 (ScaNN).
46. Sun, Yang, Wu, Guo, Liu, Mei, "SOAR: Improved Indexing for Approximate Nearest Neighbor Search" — NeurIPS 2023.
47. Gao, Long, "RaBitQ: Quantizing High-Dimensional Vectors with a Theoretical Error Bound for ANN Search" — SIGMOD 2024.
48. Gao, Long, "Practical and Asymptotically Optimal Quantization of High-Dimensional Vectors in Euclidean Space for ANN Search" — SIGMOD 2025 (Extended RaBitQ).
49. Andre, Kerboua-Benlarbi, Pellerin, "Cache-Friendly Architectures for Inverted-Index-Based Search" — ICDE 2015 (PQFastScan).
50. Douze, Sablayrolles, Jégou, "Link and code: Fast Indexing with Graphs and Compact Regression Codes" — CVPR 2018.
51. Johnson, Douze, Jégou, "Billion-scale similarity search with GPUs (Faiss)" — IEEE TBD 2021 / arXiv 1702.08734.
52. Douze et al., "The Faiss Library" — arXiv 2401.08281, 2024.

### Sparse / Hybrid / Reranking

53. Karpukhin, Oguz, Min, Lewis, Wu, Edunov, Chen, Yih, "Dense Passage Retrieval for Open-Domain QA" — EMNLP 2020.
54. Khattab, Zaharia, "ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT" — SIGIR 2020.
55. Santhanam, Khattab, Saad-Falcon, Potts, Zaharia, "ColBERTv2: Effective and Efficient Retrieval via Lightweight Late Interaction" — NAACL 2022.
56. Santhanam, Khattab, Potts, Zaharia, "PLAID: An Efficient Engine for Late Interaction Retrieval" — CIKM 2022.
57. Faysse et al., "ColPali: Efficient Document Retrieval with Vision Language Models" — arXiv 2407.01449, 2024.
58. Formal, Piwowarski, Clinchant, "SPLADE: Sparse Lexical and Expansion Model for First Stage Ranking" — SIGIR 2021.
59. Formal et al., "SPLADE v2: Sparse Lexical and Expansion Model" — arXiv 2109.10086.
60. Lassance et al., "SPLADE-v3" — arXiv 2403.06789, 2024.
61. Lin, Ma, "uniCOIL: Few-Lessons Learned from Unifying Sparse and Dense Retrieval" — arXiv 2106.14807.
62. Mallia, Khattab, Suel, Tonellotto, "DeepImpact: Learning Passage Impacts for Inverted Indexes" — SIGIR 2021.
63. Cormack, Clarke, Buettcher, "Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods" — SIGIR 2009.
64. Nogueira, Jiang, Pradeep, Lin, "Document Ranking with a Pretrained Sequence-to-Sequence Model" — Findings of EMNLP 2020 (MonoT5).
65. Pradeep, Nogueira, Lin, "The Expando-Mono-Duo Design Pattern for Text Ranking with Pretrained Sequence-to-Sequence Models" — arXiv 2101.05667 (DuoT5).
66. Zhuang et al., "RankT5: Fine-Tuning T5 for Text Ranking with Ranking Losses" — SIGIR 2023.
67. Yoon, Choi, Lee, Yoon, Seo, "ListT5: Listwise Reranking with Fusion-in-Decoder Improves Zero-shot Retrieval" — ACL 2024.
68. Sun et al., "Is ChatGPT Good at Search? Investigating Large Language Models as Re-Ranking Agents" — EMNLP 2023 (RankGPT).

### Embeddings / Multimodal

69. Reimers, Gurevych, "Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks" — EMNLP 2019.
70. Gao, Yao, Chen, "SimCSE: Simple Contrastive Learning of Sentence Embeddings" — EMNLP 2021.
71. Wang, Yang, Huang, Yang, Majumder, Wei, "Text Embeddings by Weakly-Supervised Contrastive Pre-training" — arXiv 2212.03533 (E5).
72. Xiao, Liu, Zhang, Muennighoff, "C-Pack: Packaged Resources To Advance General Chinese Embedding" — 2023 (BGE).
73. Chen, Xiao, Zhang, Lian, Liu, "BGE-M3: Multi-Linguality, Multi-Functionality, Multi-Granularity" — 2024.
74. Nussbaum, Morris, Duderstadt, Mulyar, "Nomic Embed: Training a Reproducible Long Context Text Embedder" — 2024.
75. Lee et al., "NV-Embed" — NVIDIA, arXiv 2405.17428.
76. Kusupati, Bhatt, Rege, Wallingford, Sinha, Ramanujan, Howard-Snyder, Chen, Kakade, Jain, Farhadi, "Matryoshka Representation Learning" — NeurIPS 2022 / arXiv 2205.13147.
77. Radford et al., "Learning Transferable Visual Models From Natural Language Supervision" — ICML 2021 (CLIP).
78. Zhai, Mustafa, Kolesnikov, Beyer, "Sigmoid Loss for Language Image Pre-Training" — ICCV 2023 (SigLIP).
79. Tschannen et al., "SigLIP 2: Multilingual Vision-Language Encoders" — Google, arXiv 2502.14786, 2025.
80. Muennighoff, Tazi, Magne, Reimers, "MTEB: Massive Text Embedding Benchmark" — EACL 2023.
81. Enevoldsen et al., "MMTEB: Massive Multilingual Text Embedding Benchmark" — OpenReview 2025.
82. Thakur, Reimers, Rücklé, Srivastava, Gurevych, "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models" — NeurIPS Datasets 2021.

### RAG / Long Context / Chunking

83. Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" — NeurIPS 2020.
84. Sarthi et al., "RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval" — Stanford 2024.
85. Edge, Trinh, Cheng, Bradley, Chao, Mody, Truitt, Larson, "GraphRAG: From Local to Global" — Microsoft, arXiv 2404.16130.
86. Günther, Mohr, Williams, Wang, Sturua, Akram, Han, "Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models" — arXiv 2409.04701, 2024.
87. Anthropic, "Introducing Contextual Retrieval" — Anthropic blog / Cookbook, Sept 2024.
88. Asai et al., "Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection" — ICLR 2024.

### Succinct Indexes

89. Ferragina, Manzini, "Indexing Compressed Text" — JACM 2005 (FM-index full version).
90. Grossi, Vitter, "Compressed Suffix Arrays and Suffix Trees with Applications to Text Indexing and String Matching" — STOC 2000 (CSA).
91. Nong, Zhang, Chan, "Linear Suffix Array Construction by Almost Pure Induced-Sorting" — DCC 2009 (SA-IS).

---

*Last updated: 2026-05-01*
