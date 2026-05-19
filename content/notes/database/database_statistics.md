+++
title = "Database Statistics"
date = 2026-05-20
[taxonomies]
tags = ["statistics", "query-optimizer", "cardinality-estimation", "histograms", "sampling"]
+++


# Database Statistics Collection & Estimation Techniques

Expert reference for how databases collect, store, and use statistics for query optimization. Covers sampling algorithms, histogram construction, NDV estimation, multi-column stats, learned cardinality, and system-specific implementations.

---

## 1. Why Statistics Matter

The query optimizer is a cost-based search over plan space. Cost = f(cardinality, I/O model, CPU model). Bad cardinality → wrong join order → 100–10000× slower plans.

Statistics feed:
- **Selectivity estimation**: fraction of rows surviving a predicate
- **Cardinality estimation**: output row count of each operator
- **Cost model**: bytes read, pages fetched, CPU cycles
- **Join ordering**: DPhyp/DPccp need accurate row counts to prune search space

Cardinality error compounds through joins. If each of 5 join inputs is off by 2×, final error can be 32×. Leis et al. (VLDB 2015) showed PostgreSQL frequently exceeds 1000× Q-error on 3+ table joins.

**Q-error** (Moerkotte et al. 2009):
```
Q-error(est, actual) = max(est/actual, actual/est)  ≥ 1
```
Q-error = 1 is perfect. Most papers target Q-error < 2 for 90th percentile.

---

## 2. What Gets Collected

### Table-Level
- Row count (n)
- Dead tuple count (PostgreSQL: n_dead_tup)
- Page count, average tuple width

### Column-Level
- **NDV** (number of distinct values): drives join selectivity
- **Null fraction**: rows where col IS NULL
- **MCV** (most common values): list of (value, frequency) pairs
- **Histogram**: distribution of non-MCV values
- **Correlation**: physical sort order correlation with logical sort (-1 to 1)
- **Average width** in bytes

### Multi-Column
- Functional dependencies between column sets
- MCV lists over column combinations
- Joint histograms (grid or STHoles)

### Index Statistics
- Per-level page counts
- Fill factor, leaf page count
- Per-column histogram for index columns

---

## 3. Sampling Algorithms

### Bernoulli Sampling
Each row included independently with probability p. Produces expected pN rows. Variance = p(1-p)N. Good statistical properties, poor cache behavior (random row access).

### System Sampling
Selects full pages with probability p. All rows on selected pages included. Better I/O locality, worse statistical properties (row correlation within page). PostgreSQL ANALYZE uses this for heap tables.

### Reservoir Sampling — Vitter Algorithm R (1985)
For stream of unknown length N, maintain reservoir of size k:

```c
// Algorithm R — O(N) time, O(k) space
for (i = 0; i < k; i++) reservoir[i] = stream[i];
for (i = k; i < N; i++) {
    j = rand() % (i + 1);
    if (j < k) reservoir[j] = stream[i];
}
```

Each element has exactly k/N probability of inclusion. Unbiased.

**Algorithm Z** (Vitter 1985): Skip-based optimization. Computes gap between accepted samples using the negative hypergeometric distribution. Reduces comparisons from O(N) to O(k(1 + log(N/k))). PostgreSQL ANALYZE uses Algorithm Z.

### Block-Level Sampling
Sample at block/page granularity, then scan all rows on sampled blocks. Used by PostgreSQL for large tables where row-level sampling would require full heap scan anyway. Switches strategy based on table size relative to target sample.

### PostgreSQL ANALYZE Internals

```
sample_size = min(300 × default_statistics_target, 30000)  # default target = 100
```

1. Estimate table size from pg_class.relpages
2. If table fits in sample, scan entire table
3. Otherwise: block sampling — randomly select pages, read all tuples
4. From raw sample, compute:
   - Null fraction: nulls / total
   - MCV: sort by frequency, take top `statistics_target` values
   - Histogram: from non-MCV values, partition into `statistics_target` equi-depth buckets
   - NDV: Haas-Stokes estimator (see §5)
   - Correlation: Spearman rank correlation between physical and logical order

`default_statistics_target` (default 100) controls bucket count and MCV list length. Range: 1–10000. Higher = more accurate, slower ANALYZE, larger pg_statistic rows.

### Sample Size Selection

For NDV estimation error ε with probability δ, required sample size:
```
n ≈ (NDV / ε²) × ln(1/δ)
```

For selectivity estimation of predicate with selectivity s, error ε:
```
n ≈ 1 / (s × ε²)    (rare predicates need larger samples)
```

Practical compromise: fixed sample sizes (PostgreSQL 300×target, SQL Server 20K–8M adaptive).

---

## 4. Histogram Types

### Equi-Width Histograms
Domain [min, max] split into B equal-width buckets. Each bucket stores row count. Simple construction, poor for skewed data. Rarely used in production databases.

```
bucket_width = (max - min) / B
bucket_idx(v) = floor((v - min) / bucket_width)
```

### Equi-Depth (Equi-Height) Histograms
Buckets contain equal number of rows. Better accuracy for skewed distributions.

Construction from sorted sample of size S:
```
rows_per_bucket = S / B
boundary[i] = sample[i × rows_per_bucket]
```

Each bucket stores: `(lower_bound, upper_bound, row_count, distinct_count, null_count)`.

Selectivity for point predicate `col = v`:
- Find bucket containing v
- `sel = 1 / distinct_count_in_bucket` (uniformity assumption within bucket)

Selectivity for range `a ≤ col ≤ b`:
- Sum partial/full buckets spanning [a, b]
- Partial bucket: linear interpolation within bucket bounds

**PostgreSQL histogram**: equi-depth, stores only upper bounds (lower = previous bucket's upper). Null values excluded. MCVs excluded before histogram construction — histogram represents non-MCV distribution.

**CockroachDB histogram**: equi-depth, up to 200 buckets, stores `(upper_bound, eq_rows, range_rows, distinct_range_rows)` per bucket.

### V-Optimal Histograms — Ioannidis & Kang (VLDB 1988)
Minimize total variance of approximation error. Each bucket minimizes:
```
V = Σ_buckets Σ_{v in bucket} (freq(v) - avg_freq_in_bucket)²
```
Optimal via dynamic programming O(N²B) for N distinct values and B buckets. Better than equi-depth for heavy-tailed distributions. Impractical for large N without approximation.

### MaxDiff Histograms — Poosala et al. (VLDB 1996)
Place bucket boundaries where adjacent frequency differences are largest (max "diff"). Captures sudden frequency changes well. Used as baseline in many benchmark papers.

For sorted distinct values v₁..vₙ with frequencies f₁..fₙ:
```
diff(i) = |f_i - f_{i-1}|  or  |f_i × v_i - f_{i-1} × v_{i-1}|  (area-based)
```
Select top B-1 diff positions as bucket boundaries.

### Wavelet Histograms — Vitter et al. (VLDB 1998)
Apply Haar wavelet transform to frequency distribution. Retain top-k coefficients by magnitude (largest impact on reconstruction). Coefficient thresholding = automatic error-minimizing histogram. Supports point queries and prefix-sum range queries efficiently.

```
Haar transform of [f₁, f₂, f₃, f₄]:
L1: [(f₁+f₂)/2, (f₃+f₄)/2, (f₁-f₂)/2, (f₃-f₄)/2]
L2: [(f₁+f₂+f₃+f₄)/4, (f₁+f₂-f₃-f₄)/4, ...]
```

Theoretical optimality for L2 error with k coefficients. Rarely implemented in production due to complexity.

### Compressed Histogram
MCVs stored separately; histogram covers remaining non-MCV values. PostgreSQL uses this. Ensures heavy hitters get exact frequency, long tail gets histogram approximation.

---

## 5. Distinct Value (NDV) Estimation

### Naive Counting
Exact NDV from full scan. O(N) time, O(NDV) space. Impractical for large tables.

### Good-Turing Estimator
From sample of size n containing d distinct values, f₁ = singletons (values seen once):
```
estimated_new_distinct_in_unseen = f₁ / n
```
Assumes unseen portion has similar singleton density.

### Haas-Stokes Estimator (1995) — PostgreSQL
Used by PostgreSQL. Given sample of size n from population of size N:
- d = distinct values in sample
- f₁ = values appearing exactly once in sample
- f₂ = values appearing exactly twice in sample

```
D̂ = d / (1 - (f₁/n) × (1 - n/N))

# Correction for large f₁:
if f₁ > 0:
    D̂ = d - f₁ × (1 - n/N) × ln(1 - n/N) × ... [full formula in pg source: analyze.c:5200]
```

In PostgreSQL source: `estimate_ndistinct()` in `src/backend/statistics/analyze.c`.

### Probabilistic Counting — Flajolet & Martin (1985)
Hash each element to bit string. Record position of lowest set bit (= log₂(1/hash)). Max position seen ≈ log₂(NDV). Multiple hash functions, take median of estimates.

Estimation:
```
NDV ≈ 2^(max_trailing_zeros) / φ   where φ ≈ 0.77351
```

Error ≈ 78% with single hash. Reduce with multiple parallel counters.

### HyperLogLog — Flajolet et al. (2007)
State of the art for streaming NDV. Uses 2^b registers (b = 4..16), each storing max leading zeros seen:

```c
uint8_t M[1 << b];  // b=14 → 16384 registers, ~12KB

for each element x:
    h = hash64(x)
    j = h >> (64 - b)          // first b bits = register index
    w = h & ((1 << (64-b))-1)  // remaining bits
    M[j] = max(M[j], leading_zeros(w) + 1)

// Harmonic mean estimate:
Z = 1.0 / Σ_j (2^(-M[j]))
E = α_m × m² × Z              // α_m ≈ 0.7213 for large m
```

Relative error: 1.04 / √(2^b). At b=14: ≈ 0.8% error with 12KB memory.

Bias corrections for small range (linear counting) and large range (large-range correction). Merge property: `union(HLL1, HLL2)` = elementwise max of registers. CockroachDB uses HLL for NDV.

### HyperLogLog++ — Heule et al. (2013, Google)
Improvements over HLL:
1. 64-bit hashes (vs 32-bit) eliminates large-cardinality saturation
2. Bias correction via empirical lookup table for small-cardinality range
3. Sparse representation for low-cardinality sets (dense only after threshold)

Used in BigQuery, Redshift, Spark.

### KMV (k-Minimum Values) Sketches
Maintain the k smallest hash values seen. NDV estimate:
```
NDV ≈ (k-1) / max(k_th_smallest_hash)   [normalized to [0,1]]
```

Error: 1/√(k-2). Merges by union of hash sets + take k smallest. Used in some OLAP systems.

---

## 6. Most Common Values (MCV)

### Misra-Gries Heavy Hitters (1982)
Find all items with frequency > n/k using only k counters. Stream algorithm:
```
if item in counters: counters[item]++
elif len(counters) < k: counters[item] = 1
else: decrement all counters by 1, remove zeros
```

Guarantees: any item with frequency > n/k is in output. Over-estimates by at most n/k.

### Count-Min Sketch — Cormode & Muthukrishnan (2005)
d × w matrix of counters, d independent hash functions mapping to [0,w):

```c
// Update:
for i in range(d): CMS[i][h_i(x)] += 1

// Query frequency of x:
return min(CMS[i][h_i(x)] for i in range(d))
```

Error bound: point query ≤ true_freq + ε×N with probability 1-δ where d=ln(1/δ), w=e/ε.

Space: O(ε⁻¹ log δ⁻¹). Merges by elementwise addition.

### Space Saving — Metwally et al. (2005)
Always maintain exactly k (element, count, error) triples. On new element:
- If in table: increment count
- Else: replace minimum-count element, new count = old_min + 1, error = old_min

Exact frequencies for any element with true frequency > N/(k+1). Better than Misra-Gries in practice.

### PostgreSQL MCV Storage
`pg_statistic` per-column row. Five "slots" (stakind 1..5):

| stakind | content |
|---------|---------|
| 1 | MCV: stavalues = array of values, stanumbers = frequencies |
| 2 | Histogram: stavalues = bucket boundaries (equi-depth), stanumbers = NULL |
| 3 | Correlation: stanumbers[1] = Spearman correlation coeff |
| 4 | MCV of array elements (for array columns) |
| 5 | Range histogram (for range types) |

staop = operator used for comparison (e.g., `<` OID for ordering).

Selectivity combining MCV + histogram:
```
if v in MCV_list:
    return MCV_frequency[v]
else:
    histogram_fraction = (1 - Σ MCV_frequencies) / histogram_total_rows
    return histogram_fraction × histogram_selectivity(v)
```

---

## 7. Multi-Column Statistics

### Independence Assumption Failure
Without multi-column stats, optimizer assumes independence:
```
sel(A=a AND B=b) = sel(A=a) × sel(B=b)
```

For correlated columns (city, country), this massively underestimates selectivity. For mutually exclusive values, overestimates.

### Grid Histograms
2D grid over (col_A, col_B) domain. Each cell stores row count. O(B²) cells for B buckets per dimension. Curse of dimensionality limits to 2-3 columns.

### STHoles — Bruno et al. (SIGMOD 2001)
Adaptive query-feedback histogram. Holes (sub-rectangles) carved into existing buckets based on observed query results. Converges to accurate distribution after enough queries. Space-efficient for sparse distributions.

```
STHoles structure:
Rectangle [a1,b1]×[a2,b2] with count C
  └── Hole1 [a1',b1']×[a2',b2'] with count C1
  └── Hole2 [a1'',b1'']×[a2'',b2''] with count C2
```

### Functional Dependencies — PostgreSQL 10+

`CREATE STATISTICS s ON (city, country) FROM t;`

Stores degree of dependency: fraction of (city, country) pairs where knowing city determines country.

```
degree(A→B) = 1 - NDV(A,B) / NDV(A)   ∈ [0, 1]
```

At query time: if predicate on A and B, and strong A→B dependency exists:
```
sel(A=a AND B=b) ≈ sel(A=a)   # instead of sel(A=a) × sel(B=b)
```

PostgreSQL stores up to 32 FD coefficients per statistics object.

### MCV Lists — PostgreSQL 12+

`CREATE STATISTICS s (mcv) ON (city, country) FROM t;`

Stores top-k (city, country) value pairs with joint frequencies. Direct lookup for common combinations. Fallback to independence for non-MCV combinations.

### PostgreSQL Extended Statistics — Summary

| Type | Keyword | What it captures |
|------|---------|-----------------|
| FD | DEPENDENCIES | A functionally determines B |
| MCV | MCV | Joint most-common value frequencies |
| NDV | NDISTINCT | Correlated distinct counts |

`CREATE STATISTICS name (dependencies, mcv, ndistinct) ON col1, col2 FROM tbl;`

Stats collected by ANALYZE when `CREATE STATISTICS` exists. Planner checks `pg_statistic_ext_data` at selectivity estimation time.

### CockroachDB Multi-Column Stats
Automatically collects stats on prefix columns of each index. For index on (a, b, c), collects stats for (a), (a,b), (a,b,c). Used in selectivity propagation when multi-column predicates match index prefix.

---

## 8. Auto-Statistics and Refresh

### PostgreSQL autovacuum ANALYZE
Trigger condition:
```
n_mod_since_analyze > autovacuum_analyze_threshold + autovacuum_analyze_scale_factor × reltuples
# defaults: threshold=50, scale_factor=0.2 → trigger after 20% + 50 rows changed
```

autovacuum worker runs ANALYZE asynchronously. Manual `ANALYZE` forces immediate collection.

Stale detection: `pg_stat_user_tables.last_analyze` + `n_mod_since_analyze`. No automatic staleness detection per-column.

### SQL Server Auto-Update Statistics
Pre-2014 (compatibility ≤ 70): trigger at 20% row changes.

SQL Server 2014+ (trace flag 2371, default for compat ≥ 130):
```
threshold = sqrt(1000 × table_row_count)    # dynamic threshold
```
For 1M row table: threshold ≈ 31,623 rows (3.2%) instead of 200,000 (20%). Better for large tables.

Async auto-update (default): plan compiled with old stats, update queued. Next execution uses new stats. `AUTO_UPDATE_STATISTICS_ASYNC = ON`.

Filtered statistics: `CREATE STATISTICS s ON t(col) WHERE status = 'active'` — only triggers update when filtered subset changes.

### CockroachDB Auto-Stats
Worker goroutine checks tables periodically. Refresh condition:
```
rows_changed / total_rows > 0.2   OR   rows_changed > 500
```
Probabilistic trigger: avoids thundering herd on large clusters. Adaptive throttling: backs off if cluster load high. Stats refresh runs as low-priority background job.

**Statistics Forecasting**: When ≥3 historical collections exist, fits linear regression on (timestamp, row_count) pairs. Extrapolates current row count for cardinality estimation when stats are slightly stale. Prevents plan regression during gradual table growth.

### Oracle DBMS_STATS
`GATHER_AUTO` (default): Oracle tracks column usage via `SYS.COL_USAGE$`. Only collects stats on columns that appear in WHERE/GROUP BY/ORDER BY. Saves work on wide tables.

`GATHER_STALE`: Re-collects when >10% of rows changed (tracked via monitoring).

Auto sample size: `DBMS_STATS.AUTO_SAMPLE_SIZE` — Oracle adaptively chooses sample size per column based on NDV. NDV-heavy columns get larger samples.

Online stats during bulk load:
```sql
INSERT /*+ GATHER_OPTIMIZER_STATISTICS */ INTO t SELECT ...
```
Collects stats in single pass during INSERT, no separate ANALYZE needed.

Histogram types in Oracle:
- **Frequency**: ≤254 distinct values, exact frequency per value
- **Top-Frequency**: >254 distinct values but top-N covers >99% of data
- **Height-Balanced**: equi-depth for skewed large-cardinality columns  
- **Hybrid**: top-N MCVs exact + height-balanced for remainder

### MySQL InnoDB Persistent Statistics
`innodb_stats_persistent = ON` (default): stats survive restart, stored in `mysql.innodb_table_stats` / `mysql.innodb_index_stats`.

`innodb_stats_sample_pages` (default 20): pages sampled per index for NDV estimation. Higher = more accurate, slower `ANALYZE TABLE`.

`innodb_stats_method`:
- `nulls_equal`: NULLs count as one group (default)
- `nulls_unequal`: each NULL counts as distinct
- `nulls_ignored`: NULLs excluded from NDV

Index dive vs statistics: for range predicates, MySQL performs "index dives" (B-tree probes) to count records in range when `eq_range_index_dive_limit` not exceeded. More accurate than histogram for selective ranges.

---

## 9. Sketch-Based Techniques

### Count-Min Sketch (Applications Beyond MCV)
Frequency estimation for any value in O(1) amortized. Used for:
- Join cardinality: `Σ_v min(CMS_R[v], CMS_S[v])` ≈ join output size
- Correlation detection: compare `P(A=a,B=b)` from joint CMS vs product of marginals
- Heavy hitter detection as basis for MCV

### AMS Sketch — Alon, Matias, Szegedy (1996)
Estimate second frequency moment F₂ = Σ fᵢ²:
```
F₂ ≈ (1/k) Σ_{i=1}^k (Σ_{j=1}^n h_i(a_j))²
```
where h_i : domain → {-1, +1} are 4-wise independent hash functions. F₂ captures data skew; F₂ / n = expected self-join size.

### KLL Sketch — Karnin, Lang, Liberty (2016, VLDB 2016)
Near-optimal mergeable quantile sketch. O(ε⁻¹ log log 1/δ) space for ε-approximate quantiles.

Construction: maintain multiple compactors at increasing levels. Each compactor holds b elements; when full, keeps every other element, pushes rest up.

Better than Greenwald-Khanna (2001) for mergeability. Used in Apache DataSketches (Yahoo), Apache Spark 3.0+ for approximate quantiles.

### T-Digest — Dunning & Ertl (2019)
Cluster-based quantile approximation. Clusters (centroids) near median are dense; clusters near extremes are sparse. Better tail accuracy than KLL for extreme quantiles (p99.9+).

```
mean(cluster) = Σ xi / count(cluster)
weight(cluster) = count(cluster)
# New point x absorbed by nearest cluster if within weight limit
```

Used in Elasticsearch percentile aggregations, Prometheus histograms.

### DDSketch — Masson et al. (2019)
Relative-error quantile sketch. Each bucket covers values in relative range [γ^i, γ^{i+1}). Bucket index:
```
i = ceil(log(x) / log(γ))
```
Guarantees relative error ≤ α for all quantiles. Mergeable. Deterministic (no randomness). Used in Datadog metrics.

### Mergeable Summaries for Distributed Stats
HLL union: `HLL(A∪B)[j] = max(HLL(A)[j], HLL(B)[j])` — exact.  
Count-Min union: `CMS(A∪B)[i][j] = CMS(A)[i][j] + CMS(B)[i][j]` — exact.  
KLL/T-Digest merge: approximately correct (see original papers).

Greenplum/Redshift distributed ANALYZE: each segment node computes local sketch, coordinator merges. For histograms: bucket boundaries agreed upfront, counts summed. For NDV: HLL merge.

---

## 10. Learned Cardinality Estimation

### Why Traditional Methods Fail
- Independence assumption breaks for correlated columns
- Histograms can't capture multi-table correlations
- Each join output feeds next join: errors multiply
- JOB benchmark: PostgreSQL median Q-error = 5.6×, 95th = 1000× on complex joins

### MSCN — Kipf et al. (CIDR 2019)
Multi-Set Convolutional Network. First learned cardinality model.

Input features: bitmask of tables, bitmask of joins, predicate features (column, operator, value normalized). Set-encoding via element-wise mean-pooling → fully connected layers → cardinality estimate.

Trained on 10K–100K queries with true cardinalities as labels. Generalizes to unseen predicates within known schema. Fails on distribution shift (new value ranges).

### NeuroCard — Yang et al. (NeurIPS 2020)
Autoregressive density estimation over join results. Learn P(A=a, B=b, C=c, ...) as product of conditionals:
```
P(A,B,C) = P(A) × P(B|A) × P(C|A,B)
```

Model: MADE (Masked Autoencoder for Distribution Estimation) or Transformer. Each variable factored by column + table membership indicator (handles NULLs from outer joins).

Cardinality estimate:
```
|σ_{pred}(R⋈S)| = |R⋈S| × Σ_{(a,b) satisfying pred} P(A=a, B=b)
```

10–100× better than PostgreSQL on JOB. Inference: Monte Carlo sampling from model. Slow if not batched.

### DeepDB — Hilprecht et al. (VLDB 2020)
Sum-Product Network (SPN) learned from data. Decomposes joint distribution into products of marginals when columns are statistically independent:

```
If X ⊥ Y:  P(X, Y) = P(X) × P(Y)   [product node]
Else:       P(X, Y) = Σ_i w_i × P_i(X, Y)  [sum node = mixture]
```

SPNs: exact inference in linear time in circuit size. Learns structure via recursive independence testing (RDC test). Better than NeuroCard for single-table; worse for complex multi-join.

### BayesCard — Wu & Cai (2021)
Bayesian network structure learning over table join columns. Learns P(C1, C2, ..., Ck) as DAG of conditional distributions. Inference via variable elimination.

Advantage: interpretable, fast updates (add observations), calibrated uncertainty estimates.

### FLAT — Zhu et al. (VLDB 2021)
Factorized Lightweight and Accurate cardinality estimator. Combines factorization decomposition with SPN-like structure. Learns across multiple tables using foreign key join semantics. Better accuracy-speed tradeoff than NeuroCard for OLAP workloads.

### Bao — Marcus et al. (VLDB 2021)
Bandit-based plan selection, not pure cardinality estimation. Uses existing cardinality estimates, learns which plan hints work well for which query families. Thompson sampling over hint sets. Avoids worst plans without requiring accurate cardinality.

### ALECE — Han et al. (SIGMOD 2023)
Attention-based learned cardinality. Transformer-based model treating query predicates as attention tokens over column statistics. Handles distribution shift better via lightweight fine-tuning.

### Hybrid Approaches
Common production pattern:
```
if query is simple (single table, few predicates):
    use traditional histogram + MCV
else if learned model confidence high:
    use learned estimate
else:
    fall back to histogram + cap at reasonable bound
```

No production database has fully replaced histograms with learned models as of 2026. PostgreSQL has research extensions (pg_plan_advsr for hint feedback, pg_learn for ML cardinality).

### Online Retraining
Distribution shift detection: compare recent query true cardinalities vs model predictions. If rolling Q-error > threshold, trigger retrain. Retrain on recent query log sample. Avoid catastrophic forgetting with replay buffer.

---

## 11. Join Cardinality Estimation

### Attribute Value Independence (AVI)
Assumes join columns independent across tables:
```
|R⋈S on R.a=S.b| = |R| × |S| / max(NDV(R.a), NDV(S.b))
```

Underestimates when key-foreign-key joins have skewed keys. Overestimates when join columns correlated with filter predicates.

### End-Biased Sampling — Estan & Naughton (SIGMOD 2006)
Sample join results directly: for each tuple in R, probe S using the join key. Estimate:
```
|R⋈S| = (1/p) × |sampled matches|
```

Avoids AVI assumption. Expensive: requires index on S. Used for initial statistics, not per-query.

### Wander Join — Li et al. (SIGMOD 2016)
Random walk over join graph for cardinality estimation. Start at random tuple in any relation, randomly follow join edges to adjacent relations. Each completed path = one sample from join result. Estimate:

```
|J| ≈ N / (Σ paths × (1/|start_rel|))
```

Handles acyclic and cyclic joins. Unbiased. O(k) per sample for k-way join. Practical for ≤ 5 tables.

### Bound-Based Methods
Upper bound via containment assumption (each value in smaller table matches something in larger):
```
|R⋈S| ≤ min(|R| × max_freq(S.key), |S| × max_freq(R.key))
```

Used as sanity check to cap runaway estimates.

---

## 12. System Deep Dives

### PostgreSQL — Full Statistics Pipeline

**Schema**: `pg_statistic` (one row per column), `pg_statistic_ext` (multi-column), `pg_statistic_ext_data`.

Key functions in source:
- `src/backend/commands/analyze.c`: main ANALYZE orchestration, heap sampling
- `src/backend/statistics/statistics.c`: extended statistics collection
- `src/backend/utils/adt/selfuncs.c`: selectivity estimation functions
- `src/backend/statistics/analyze.c`: `estimate_ndistinct()`

Selectivity functions by operator family:
- `eqsel()`: equality predicate selectivity — uses MCV lookup then histogram
- `scalarltsel()`, `scalargtsel()`: range predicate — histogram bucket summation
- `neqsel()`: inequality — 1 - eqsel
- `matchingsel()`: LIKE/regex — heuristics (0.005 for anchored, 0.0001 for arbitrary)
- `arraycontsel()`: array @> operator

Range histograms (stakind=4 for pg_range types): stores boundaries as range values, enables range overlap selectivity.

### SQL Server — Statistics Blob

Statistics stored in sys.stats, data in STATBLOB binary column.

Contents:
- Header: row count, modification counter, sample fraction
- Density vector: 1/NDV for each prefix of index columns (for multi-column stats)
- Histogram: up to 200 RANGE_HI_KEY rows, each with (RANGE_HI_KEY, EQ_ROWS, RANGE_ROWS, DISTINCT_RANGE_ROWS, AVG_RANGE_ROWS)

`DBCC SHOW_STATISTICS ('table', 'stat_name') WITH HISTOGRAM` — displays histogram.

Incremental statistics (2014+): per-partition stats stored separately, global stats synthesized by merging. `CREATE STATISTICS ... WITH INCREMENTAL = ON`. Only modified partitions need refresh.

Filtered statistics: selectivity for filtered index only accounts for matching rows. Optimizer uses filtered stats when query predicate matches filter definition.

### Oracle — Column Usage Tracking

`SYS.COL_USAGE$` tracks which columns appear in WHERE clauses (equality, range, like, null checks). DBMS_STATS only collects histograms for used columns by default.

Histogram decision: if NDV ≤ 254, use Frequency histogram (exact). If NDV > 254 but top-N values cover >99%, use Top-Frequency (like MCV + small tail). Otherwise Height-Balanced or Hybrid.

Adaptive Cursor Sharing: if statistics cause a bad plan, Oracle detects via execution feedback (actual vs estimated rows), marks cursor bind-sensitive, re-optimizes with extended cardinality info. Relates to Adaptive Query Optimization (AQO) in PostgreSQL research.

### Snowflake — Micro-Partition Statistics

No explicit ANALYZE. Statistics collected during data load.

Each micro-partition (≈16MB compressed, 50–500MB uncompressed) stores metadata:
- Per-column min/max values
- Per-column NDV approximation (HLL)  
- Null count
- Overlap with other micro-partitions (for clustering)

Query planning uses micro-partition metadata for partition pruning and cardinality estimation. Optimizer knows which micro-partitions to skip for range predicates.

Automatic clustering: for frequently queried columns, Snowflake auto-clusters data to reduce micro-partition overlap, improving both data skipping and statistics accuracy.

### ClickHouse — Granule-Level Statistics

Traditional ClickHouse (pre-2023): only table-level stats (row count, compressed size). No column histograms. Relies on primary key index for range filtering.

ClickHouse 23.11+: column statistics as optional index feature:
```sql
ALTER TABLE t ADD STATISTICS col TYPE tdigest, uniq;
```
Types: `tdigest` (quantiles), `uniq` (HLL-based NDV), `count_min` (frequency), `minmax`.

Collected via `ANALYZE TABLE t UPDATE STATISTICS (col)`.

Mark-level statistics: each mark (8192 rows) has min/max in sparse index. `PREWHERE` clause evaluated cheaply to skip marks.

### DuckDB — Statistics Collection

Statistics collected during query execution (adaptive/online):
- Collects base table stats during first full scan
- Propagates cardinality through operators during query optimization
- Uses HLL for NDV, samples for histograms
- `PRAGMA enable_progress_bar` + `EXPLAIN ANALYZE` shows actual vs estimated

`duckdb_statistics('table', 'column')` system function — returns min, max, approx_distinct.

Pushes statistics through expression rewrites: if after predicate pushdown column range is [a,b], histograms trimmed to that range before further selectivity estimation.

---

## 13. Incremental and Online Statistics

### Problem
Full ANALYZE is expensive: requires scan of large fraction of table. For tables with 10B rows, even 1% sample = 100M rows.

### Reservoir Sampling Over Streams
Maintain reservoir of size k, update on INSERT/DELETE:
- INSERT: add to reservoir with probability k/current_n (reservoir sampling)
- DELETE: if deleted row in reservoir, replace with random non-reservoir row (requires tracking)

Deletions are hard — no efficient algorithm to maintain reservoir under arbitrary deletions without knowing total count.

### DDSketch / T-Digest for Streaming Histograms
Both support `add(value)` in O(1). No re-scan needed. Suitable for maintaining running distribution.

Challenge: value deletions. Requires separate "delete" sketch (subtract from positive sketch). Works for Count-Min (subtraction is exact), less clean for T-Digest.

### KLL Sketch Streaming
KLL supports streaming additions naturally. No explicit support for deletions. Used in Spark Streaming stats, Flink window statistics.

### PostgreSQL: No Incremental — Invalidate + Re-Scan
PostgreSQL does not incrementally update statistics. autovacuum detects n_mod_since_analyze > threshold, then runs full ANALYZE on the table. Simple but expensive for large tables.

### SQL Server Incremental on Partitioned Tables
Partitioned tables: per-partition statistics + global rollup. Only changed partitions need re-ANALYZE. Useful for append-only partition schemes (time-series data: new partition per day/month).

### CockroachDB Change Tracking
CRDB tracks row mutations per table range. When change threshold exceeded, stats refresh job queued. Refresh does full sampling scan (no incremental). Forecasting (§7) bridges gap between refreshes.

---

## 14. Distributed Statistics Collection

### Parallel ANALYZE
Each worker node scans its local partitions, computes local reservoir samples. Coordinator:
1. Merges reservoir samples (union + re-sample to target size)
2. Computes global histogram from merged sample
3. For NDV: merges per-node HLL sketches (elementwise max)
4. For MCVs: union local MCVs, sum frequencies, sort by frequency, take top-k

Greenplum: `ANALYZE` fans out to all segment nodes in parallel. Coordinator holds final stats.

### Hive / Spark CBO Statistics
```sql
ANALYZE TABLE t COMPUTE STATISTICS;           -- table-level
ANALYZE TABLE t COMPUTE STATISTICS FOR COLUMNS col1, col2;  -- column stats
```

Spark 2.2+ Cost-Based Optimizer uses these for join reordering (Adaptive Query Execution in Spark 3.0+ uses runtime stats for adaptive join strategies regardless).

Hive stores stats in Metastore (MySQL/PostgreSQL backing store). Column stats: NDV, null count, max/min, avg length. No histogram by default (histograms added in Hive 3.0 via HLL-based approx).

### Trino / Presto
Statistics sourced from Hive Metastore connector, or via connector-specific API (`ConnectorTableStatistics`, `ConnectorColumnStatistics`).

`ANALYZE` statement: `ANALYZE tablename WITH (partitions = ARRAY[...])` — delegates to connector.

For Iceberg tables: statistics stored in table metadata files (iceberg-metadata.json), includes NDV (HLL), null count, min/max per column per snapshot.

### Apache Iceberg Table Statistics
Column statistics in manifest files: per-column min/max, null count, nan count (for floats), lower/upper bounds as bytes.

Puffin files (Iceberg spec v2): blob storage for large statistics objects (HLL sketches, theta sketches). NDV from theta sketch (Apache DataSketches / KMV variant).

### Google Spanner Statistics
Spanner maintains `SPANNER_SYS.TABLE_STATISTICS` and `SPANNER_SYS.COLUMN_STATISTICS`. Auto-refresh. Collection via distributed sampling using Spanner's own read infrastructure. Stats used by Spanner query optimizer for join ordering.

---

## 15. Benchmarks and Accuracy Evaluation

### IMDB / JOB Benchmark — Leis et al. (VLDB 2015)
Internet Movie Database: 21 tables, up to 113 foreign keys. 113 benchmark queries (JOB = Join Order Benchmark), 3–16 way joins.

Findings:
- PostgreSQL: geometric mean Q-error ≈ 5.6× per join input; worst case >1000×
- Commercial systems (SQL Server, DB2, Oracle): similar or worse on complex joins
- Even perfect cardinalities don't always produce optimal plans (cost model errors)
- Bad join orders cause 100–1000× slowdowns on complex queries

Q-error distribution on JOB:
```
System          | p50  | p95   | max
PostgreSQL 9.4  | 1.5× | 58×   | 7500×
PostgreSQL 12   | 1.3× | 12×   | 960×
Commercial DBs  | 1.2× | 30×   | 3000×
NeuroCard       | 1.1× | 3.0×  | 50×
```

### STATS Benchmark — Han et al. (SIGMOD 2021)
8 tables from Stack Overflow, 146 queries, up to 8-way joins. Evaluates learned models in distribution shift settings.

### CE Benchmark Components
- Base table filters: single-column and multi-column predicates
- Join cardinalities: 2-way through N-way
- Correlation tests: correlated column groups
- Skew tests: Zipfian-distributed join keys

### Error Amplification Through Join Trees

For left-deep chain R1⋈R2⋈R3⋈R4, if each single-join cardinality has Q-error = q:
```
max Q-error(final) ≤ q^(n-1)   # in worst case
```

For q=2, n=4: max error = 8×. In practice, errors partially cancel, but tail errors remain large.

---

## 16. Key Papers Reference

| Paper | Venue | Topic |
|-------|-------|-------|
| Vitter 1985, "Random Sampling with a Reservoir" | ACM Trans. Math. Software | Reservoir sampling Algorithm R, Z |
| Ioannidis & Kang 1988, "Optimal Histograms for Limiting Worst-Case Error" | VLDB | V-optimal histograms |
| Haas et al. 1995, "Sampling-Based Estimation of the Number of Distinct Values" | VLDB | Haas-Stokes NDV estimator (PostgreSQL) |
| Flajolet & Martin 1985, "Probabilistic Counting Algorithms" | J. Comput. Syst. Sci. | Probabilistic counting for NDV |
| Misra & Gries 1982, "Finding Repeated Elements" | Sci. Comput. Program. | Heavy hitter streaming algorithm |
| Poosala et al. 1996, "Improved Histograms for Selectivity Estimation" | SIGMOD | MaxDiff, comparative study of histograms |
| Bruno, Chaudhuri & Gravano 2001, "STHoles: A Multidimensional Workload-Aware Histogram" | SIGMOD | Adaptive multi-column histograms |
| Vitter, Wang & Iyer 1998, "Data Cube Approximation and Histograms via Wavelets" | VLDB | Wavelet histograms |
| Cormode & Muthukrishnan 2005, "An Improved Data Stream Summary: Count-Min Sketch" | J. Algorithms | Count-Min Sketch |
| Metwally, Agrawal & El Abbadi 2005, "Efficient Computation of Frequent and Top-k Elements" | ICDT | Space Saving algorithm |
| Flajolet et al. 2007, "HyperLogLog: Analysis of Near-Optimal Cardinality Estimation" | DMTCS | HyperLogLog algorithm |
| Heule, Nunkesser & Hall 2013, "HyperLogLog in Practice" | EDBT | HyperLogLog++ (Google) |
| Greenwald & Khanna 2001, "Space-Efficient Online Computation of Quantile Summaries" | SIGMOD | GK quantile sketch |
| Karnin, Lang & Liberty 2016, "Optimal Quantile Approximation in Streams" | FOCS | KLL sketch |
| Dunning & Ertl 2019, "Computing Extremely Accurate Quantiles Using t-Digests" | arXiv | T-Digest |
| Masson, Rim & Lee 2019, "DDSketch: A Fast and Fully-Mergeable Quantile Sketch" | VLDB | DDSketch |
| Alon, Matias & Szegedy 1999, "The Space Complexity of Approximating the Frequency Moments" | J. Comput. Syst. Sci. | AMS sketch, F2 moment |
| Estan & Naughton 2006, "End-Biased Samples for Join Cardinality Estimation" | SIGMOD | Join sample estimation |
| Leis et al. 2015, "How Good Are Query Optimizers?" | VLDB | JOB benchmark, Q-error analysis |
| Kipf et al. 2019, "Learned Cardinalities: Estimating Correlated Joins with Deep Learning" | CIDR | MSCN, first learned cardinality model |
| Yang et al. 2020, "NeuroCard: One Cardinality Estimator for All Tables" | NeurIPS | Autoregressive join cardinality |
| Hilprecht et al. 2020, "DeepDB: Learn from Data, not from Queries" | VLDB | SPN-based learned stats |
| Marcus et al. 2021, "Bao: Making Learned Query Optimization Practical" | SIGMOD | Bandit hint selection |
| Li et al. 2016, "Wander Join: Online Aggregation via Random Walks" | SIGMOD | Random walk join cardinality |
| Han et al. 2021, "Cardinality Estimation in DBMS: A Comprehensive Benchmark" | VLDB | STATS benchmark |
| Moerkotte et al. 2009, "Preventing Bad Plans by Bounding the Impact of Cardinality Estimation Errors" | VLDB | Q-error metric definition |

---

## ASCII: Statistics Pipeline

```
Table Data
    │
    ▼ (reservoir sampling, block sampling)
Sample S ──── size: min(300×target, 30000) rows
    │
    ├──► Null count ──────────────────────────► null_frac
    │
    ├──► Sort by value
    │       │
    │       ├──► Top-k by frequency ──────────► MCV list (values + frequencies)
    │       │
    │       └──► Remaining values
    │               │
    │               └──► Equi-depth partition ► Histogram (B buckets)
    │
    ├──► Haas-Stokes estimator ───────────────► NDV (ndistinct)
    │
    └──► Spearman rank correlation ───────────► correlation

Multi-column stats (CREATE STATISTICS):
    ├──► RDC independence test ───────────────► FD coefficients
    ├──► Joint MCV computation ───────────────► MCV list over (col1, col2)
    └──► Joint HLL ────────────────────────────► NDV for column group

                    ┌──────────────────────────┐
Stored in:          │ pg_statistic             │
                    │  stakind 1: MCV          │
                    │  stakind 2: histogram    │
                    │  stakind 3: correlation  │
                    └──────────────────────────┘
```

## ASCII: HyperLogLog Register Update

```
Element x
    │
    ▼
 hash64(x) = 0b[b₆₃...b₀]
    │
    ├── first b bits ─────────► register index j  (j < 2^b)
    │
    └── remaining 64-b bits ──► count leading zeros + 1 = ρ(w)

M[j] = max(M[j], ρ(w))

Estimate:
    Z = 1.0 / Σ_j (2^(-M[j]))      (harmonic mean)
    E = α_m × m² × Z
    where α_m ≈ 0.7213/(1 + 1.079/m), m = 2^b

Error ≈ 1.04/√m   (e.g., m=16384 → 0.81% error)
```
