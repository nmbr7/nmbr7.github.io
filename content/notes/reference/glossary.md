+++
title = "Glossary"
date = 2026-03-26
[taxonomies]
tags = ["glossary", "reference"]
+++


# Glossary

Acronyms and technical terms used across research docs.

| Term | Definition | Covered In |
|---|---|---|
| BTB | Branch Target Buffer — hardware cache mapping instruction PCs to predicted branch targets; hierarchical (L0 µBTB / L1 / L2) | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| ACS | Access Control Services — PCIe capability ensuring peer-to-peer isolation for IOMMU groups | [VFIO Internals](@/notes/os/vfio_internals.md) |
| AES-NI | Advanced Encryption Standard New Instructions — x86 hardware-accelerated AES encryption | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| AMQP | Advanced Message Queuing Protocol — wire-level messaging protocol implemented by RabbitMQ | [RabbitMQ Internals](@/notes/distributed/rabbitmq_internals.md) |
| AQE | Adaptive Query Execution — Spark 3.0+ runtime re-optimization based on actual data statistics | [Database Systems](@/notes/database/database_systems.md) |
| ARIES | Algorithm for Recovery and Isolation Exploiting Semantics — foundational WAL recovery protocol using LSN-based redo/undo | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| ART | Adaptive Radix Tree — cache-friendly trie with variable node sizes (Node4/16/48/256), developed at TUM | [Database Systems](@/notes/database/database_systems.md) |
| AVX-512 | Advanced Vector Extensions 512-bit — x86 SIMD instruction set processing 16 floats per instruction | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| BAR | Base Address Register — PCI configuration space register defining device memory-mapped I/O regions | [VFIO Internals](@/notes/os/vfio_internals.md) |
| BFT | Byzantine Fault Tolerance — ability to reach consensus despite arbitrary (malicious) node failures | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| BMI | Bit Manipulation Instructions — x86 extension for PEXT/PDEP/BLSR and other bit-level operations | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| BNLJ | Block Nested Loop Join — join variant reading outer relation in memory-sized blocks to reduce I/O | [Join Algorithms](@/notes/database/join_algorithms.md) |
| BRIN | Block Range Index — PostgreSQL lightweight index storing min/max per physical block range | [Database Systems](@/notes/database/database_systems.md) |
| BTI | Branch Target Identification — ARM control-flow integrity mechanism marking valid indirect branch targets | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Bw-Tree | Lock-free B+ tree using delta records and CAS operations, developed for Hekaton/Azure Cosmos DB | [Data Structures](@/notes/programming/data_structures.md) |
| CAP | Consistency, Availability, Partition tolerance — Brewer/Gilbert-Lynch theorem on distributed system trade-offs | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| CAS | Compare-And-Swap — atomic instruction for lock-free programming; CMPXCHG on x86, CAS/LDXR-STXR on ARM | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| CBO | Cost-Based Optimization — query optimization using table/column statistics for plan selection | [Database Systems](@/notes/database/database_systems.md) |
| CMS | Count-Min Sketch (Cormode & Muthukrishnan 2005) — d×w counter matrix for streaming frequency estimation with O(ε⁻¹ log δ⁻¹) space | [Database Statistics](@/notes/database/database_statistics.md) |
| CDC | Change Data Capture — technique for streaming database changes as events, often via Debezium or logical replication | [WAL Incremental Conversion](@/notes/database/wal_incremental_conversion.md) |
| CDNA | Compute DNA — AMD's compute-focused GPU microarchitecture (MI-series accelerators) | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| CET | Control-flow Enforcement Technology — Intel's shadow stack + ENDBR indirect branch tracking for CFI | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| CLOG | Commit Log — PostgreSQL structure tracking transaction commit/abort status for MVCC visibility | [Arrow PostgreSQL Integration](@/notes/database/arrow_postgresql_integration.md) |
| COW | Copy-On-Write — technique where data is shared until modified; used in WiredTiger B-trees, Neon branching, btrfs | [MongoDB/WiredTiger Internals](@/notes/database/mongodb_wiredtiger_internals.md) |
| CoWoS | Chip-on-Wafer-on-Substrate — TSMC advanced packaging for multi-die integration (used in H100/A100) | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| CPL | Consistency Point LSN — Aurora's highest LSN representing a transaction-consistent boundary | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| CQE | Completion Queue Entry — io_uring kernel-to-user result structure for completed I/O operations | [io_uring Internals](@/notes/os/io_uring_internals.md) |
| CRDT | Conflict-free Replicated Data Type — data structure achieving eventual consistency without coordination | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Calcite | Apache Calcite — embeddable Java SQL parser + validator + relational-algebra optimizer (no storage/execution engine); powers Flink, Hive, Druid, Kylin, Beam, Phoenix; Begoli SIGMOD 2018 | [Calcite Internals](@/notes/database/calcite_internals.md) |
| Cascades | Top-down memoizing query-optimization framework (Graefe 1995) using rule-driven exploration of a memo of equivalence groups with branch-and-bound; basis of Calcite's VolcanoPlanner and CockroachDB's optimizer | [Calcite Internals](@/notes/database/calcite_internals.md), [CockroachDB Optimizer Rules](@/notes/database/cockroachdb_optimizer_rules.md) |
| Convention | Calcite trait identifying the execution engine / calling convention of a RelNode (NONE = logical, ENUMERABLE = built-in linq4j, BINDABLE = interpreted, JDBC/Druid/etc.); inputs must match or be bridged by a converter | [Calcite Internals](@/notes/database/calcite_internals.md) |
| RelNode | Calcite relational expression (Project/Filter/Join/Aggregate/Scan…) carrying a RelTraitSet; logical forms in rel.logical, physical forms per convention; memoized by digest in the VolcanoPlanner | [Calcite Internals](@/notes/database/calcite_internals.md) |
| RexNode | Calcite row/scalar expression (RexInputRef `$n` by ordinal, RexLiteral, RexCall, RexOver window, RexSubQuery, RexCorrelVariable); built by RexBuilder, simplified by RexSimplify | [Calcite Internals](@/notes/database/calcite_internals.md) |
| RelSubset | A RelSet (equivalence class of semantically identical RelNodes) restricted to one RelTraitSet; caches the cheapest member (`best`/`bestCost`); the unit of Calcite's Volcano memo and cost propagation | [Calcite Internals](@/notes/database/calcite_internals.md) |
| RexUnknownAs | Calcite three-valued-logic mode (FALSE/TRUE/UNKNOWN) telling RexSimplify how NULL may be folded in a given syntactic position (e.g. UNKNOWN→FALSE under WHERE) | [Calcite Internals](@/notes/database/calcite_internals.md) |
| CXL | Compute Express Link — cache-coherent interconnect for CPU-to-device memory sharing over PCIe physical layer | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| DBSP | Database Stream Processor — formal mathematical framework for incremental computation over Z-sets (Feldera) | [Database Systems](@/notes/database/database_systems.md) |
| DMA | Direct Memory Access — hardware capability for devices to read/write main memory without CPU involvement | [VFIO Internals](@/notes/os/vfio_internals.md) |
| DMB | Data Memory Barrier — ARM instruction ordering memory accesses without stalling execution | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| DecodedVector | Velox helper that normalizes any vector encoding (DICT/CONST/BIAS/SEQ) to flat base + indices for safe element access | [Velox Internals](@/notes/database/velox_internals.md) |
| DPccp | Dynamic Programming connected complement pairs — join enumeration algorithm for bushy plans | [Join Algorithms](@/notes/database/join_algorithms.md) |
| DPhyp | Dynamic Programming on hypergraphs — join enumeration supporting multi-relation predicates | [Join Algorithms](@/notes/database/join_algorithms.md), [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| DPDK | Data Plane Development Kit — userspace networking framework using VFIO for kernel-bypass packet processing (~30-40 Mpps) | [VFIO Internals](@/notes/os/vfio_internals.md) |
| DSB | Data Synchronization Barrier — ARM instruction that stalls execution until all prior memory accesses complete | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| DST | Deterministic Simulation Testing — technique running distributed systems in a single-threaded deterministic simulator | [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |
| EBR | Epoch-Based Reclamation — memory reclamation scheme for lock-free data structures using global epoch tracking | [Data Structures](@/notes/programming/data_structures.md) |
| EPT | Extended Page Tables — Intel VT-x hardware-assisted two-level address translation for VM memory | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| EVEX | Extended VEX — x86 instruction prefix encoding for AVX-512/APX supporting 32 vector registers and masking | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| FDW | Foreign Data Wrapper — PostgreSQL mechanism for querying external data sources as local tables | [Database Systems](@/notes/database/database_systems.md) |
| FLP | Fischer-Lynch-Paterson impossibility — proof that deterministic asynchronous consensus is impossible with even one crash | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| FMA | Fused Multiply-Add — single instruction computing a*b+c with one rounding; used in Tensor Cores and CPU SIMD | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| FOR | Frame-of-Reference — lightweight compression storing a per-segment base (min) and bitpacked offsets; common for dates/timestamps | [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| FSST | Fast Static Symbol Table — string compression assigning 1-byte codes to frequent substrings, allowing random access without full decompression | [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| FP8 | 8-bit floating point — low-precision format (E4M3/E5M2) for LLM training/inference on Hopper+ GPUs | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| FPW | Full Page Writes — PostgreSQL technique writing complete page images to WAL after checkpoint to prevent torn pages | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| FTL | Flash Translation Layer — SSD firmware mapping logical block addresses to physical NAND pages | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| GAA | Gate-All-Around — transistor architecture (replacing FinFET at 2nm) where gate wraps channel on all sides | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| GDSII | Graphic Data System II — standard file format for IC layout data sent to semiconductor foundries for fabrication | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| GEQO | Genetic Query Optimizer — PostgreSQL's join ordering strategy using genetic algorithms for queries with >= 12 tables | [Join Algorithms](@/notes/database/join_algorithms.md) |
| GIN | Generalized Inverted Index — PostgreSQL index for composite values (arrays, JSONB, full-text tsvector) | [Database Systems](@/notes/database/database_systems.md) |
| GiST | Generalized Search Tree — PostgreSQL extensible indexing framework for complex data types (geometric, range) | [Database Systems](@/notes/database/database_systems.md) |
| GOO | Greedy Operator Ordering — O(n^3) heuristic join ordering algorithm used as fallback for large queries | [Join Algorithms](@/notes/database/join_algorithms.md) |
| GST | Global Stabilization Time — the (unknown) point after which network timing bounds hold in partial synchrony models | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| GTID | Global Transaction Identifier — MySQL identifier simplifying replication topology management and failover | [Database Systems](@/notes/database/database_systems.md) |
| HAMT | Hash Array Mapped Trie — persistent data structure with near-O(1) operations via structural sharing (Clojure, Scala) | [Data Structures](@/notes/programming/data_structures.md) |
| HashStringAllocator | Velox arena allocator used inside hash tables and aggregation: 4-byte Header per block (kFree/kContinued/kPreviousFree flags) + CompactDoubleList free list; streaming write via newWrite/finishWrite | [Velox Internals](@/notes/database/velox_internals.md) |
| HBM | High Bandwidth Memory — stacked DRAM (HBM2/HBM3) providing >1 TB/s bandwidth for GPU/accelerator designs | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| HLC | Hybrid Logical Clock — clock combining wall-clock time with a logical counter for causal ordering (CockroachDB) | [Database Systems](@/notes/database/database_systems.md) |
| HLL | HyperLogLog (Flajolet et al. 2007) — probabilistic NDV sketch: 2^b registers, harmonic mean estimate, 1.04/√(2^b) relative error; merges via elementwise max | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| HOT | Heap-Only Tuple — PostgreSQL optimization where updated tuples stay on the same page, avoiding index updates | [Arrow PostgreSQL Integration](@/notes/database/arrow_postgresql_integration.md) |
| HTAP | Hybrid Transactional/Analytical Processing — system handling both OLTP and OLAP workloads (HyPer, TiDB) | [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| IOMMU | I/O Memory Management Unit — hardware translating device DMA addresses (IOVA) to physical addresses | [VFIO Internals](@/notes/os/vfio_internals.md) |
| IOMMUFD | IOMMU File Descriptor — newer Linux interface replacing VFIO container/group model with fd-centric API | [VFIO Internals](@/notes/os/vfio_internals.md) |
| IOTLB | I/O Translation Lookaside Buffer — IOMMU's cache for IOVA-to-PA translations; hugepages reduce miss rate dramatically | [VFIO Internals](@/notes/os/vfio_internals.md) |
| IOVA | I/O Virtual Address — the address space a device sees through the IOMMU, analogous to virtual addresses for CPUs | [VFIO Internals](@/notes/os/vfio_internals.md) |
| ILP | Instruction-Level Parallelism — overlap of independent instructions from a single thread exploited by OoO execution | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| IPC | Instructions Per Cycle — microarchitectural efficiency metric; PrediCache achieves 0.55 IPC vs 0.31 for traditional | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| IQ | Issue Queue / Reservation Stations — buffer holding dispatched µops waiting for operands before execution; unified or distributed | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| ISR | In-Sync Replicas — Kafka replicas caught up with the partition leader, eligible for leader election | [Kafka Internals](@/notes/distributed/kafka_internals.md) |
| Janino | Lightweight in-process Java source-to-bytecode compiler; Calcite uses it to compile generated linq4j Enumerable code, RexExecutor constant folding, and the metadata-handler dispatcher | [Calcite Internals](@/notes/database/calcite_internals.md) |
| JoinBridge | Velox synchronization primitive between HashBuild and HashProbe pipelines: build sets a folly::Promise with the completed HashTable; probe returns a folly::SemiFuture from isBlocked() until build completes | [Velox Internals](@/notes/database/velox_internals.md) |
| JIT | Just-In-Time compilation — compiling code at runtime; used by HyPer (LLVM), Umbra (asmJIT), PostgreSQL 11+ | [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| JOB | Join Order Benchmark (Leis et al. VLDB 2015) — 113 IMDB queries, 3–16-way joins; standard benchmark for cardinality estimation accuracy (Q-error) | [Database Statistics](@/notes/database/database_statistics.md) |
| KPTI | Kernel Page Table Isolation — Meltdown mitigation separating user/kernel page tables; adds ~5-30% overhead on syscall-heavy workloads | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| KLL | KLL Sketch (Karnin-Lang-Liberty 2016) — near-optimal mergeable quantile sketch; O(ε⁻¹ log log 1/δ) space; better than GK sketch for distributed merge | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| KMV | K-Minimum Values sketch — maintains k smallest hash values; NDV ≈ (k-1)/max(kth_smallest); merges by union + take k smallest | [Database Statistics](@/notes/database/database_statistics.md) |
| KRaft | Kafka Raft — Kafka's built-in Raft-based consensus replacing ZooKeeper for metadata management | [Kafka Internals](@/notes/distributed/kafka_internals.md) |
| kTLS | Kernel TLS — Linux kernel offload of TLS encryption/decryption for socket I/O, reducing context switches | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| LDAR | Load-Acquire Register — ARM instruction providing acquire semantics (no subsequent access reordered before it) | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| LDAPR | Load-Acquire RCpc Register — ARM weaker acquire load (ARMv8.3-RCPC) matching C++ memory_order_consume-like behavior | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| LIPAH | Logical-ID Pointer Augmented Hinting — buffer manager using fat pointers (PID + hint address); limited to 32-bit PIDs | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| LL/SC | Load-Linked/Store-Conditional — ARM/RISC-V atomic primitive pair (LDXR/STXR, LR/SC) for lock-free operations | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| LMUL | Length Multiplier — RISC-V Vector extension register grouping factor controlling effective vector length | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| LQ | Load Queue — per-core buffer tracking all in-flight loads for STLF lookup and memory ordering violation detection | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| LPS | Log Processing Service — AlloyDB component that receives WAL and materializes data blocks asynchronously | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| LSE | Large System Extensions — ARMv8.1 atomic instructions (CAS, LDADD, SWP) replacing LL/SC for better scalability | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Lattice | Calcite OLAP construct modeling a star/snowflake schema as a virtual fact-table join; dimensions and measures define candidate aggregate "tiles" auto-selected via the HRU (Harinarayan SIGMOD 1996) cube-lattice greedy algorithm for materialized-view acceleration | [Calcite Internals](@/notes/database/calcite_internals.md) |
| linq4j | Calcite's Java port of .NET LINQ — Enumerable/Enumerator data model plus an expression-tree AST (org.apache.calcite.linq4j.tree) that the Enumerable convention emits and Janino compiles | [Calcite Internals](@/notes/database/calcite_internals.md) |
| LSM | Log-Structured Merge tree — write-optimized structure converting random writes to sequential via leveled compaction | [LSM Trees](@/notes/database/lsm_trees.md) |
| LSN | Log Sequence Number — monotonically increasing identifier for WAL records, used for recovery and page versioning | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| MergeTree | ClickHouse's core storage engine family: each INSERT writes an immutable PK-sorted part; background merges fold parts together (LSM-like) | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| Granule | ClickHouse unit of index addressing — default 8192 rows (capped by index_granularity_bytes); the smallest data block the sparse index can select | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| Mark | ClickHouse mark (.mrk3/.cmrk3) — 24-byte record mapping a granule to (offset_in_compressed_file, offset_in_decompressed_block, rows_in_granule) | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| Sparse primary index | Index storing the PK tuple only at each granule boundary (primary.idx) — lossy zone-map-style pruning, not per-row | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| DoubleDelta | Codec storing second-order differences (delta-of-deltas) with Gorilla varint framing; near-free for fixed-stride sequences like timestamps | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| T64 | ClickHouse codec transposing 64 integers into bit-planes after range subtraction, storing only the needed planes; for low-range/low-cardinality ints | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| Gorilla | XOR-based float compression encoding leading/trailing zero runs of consecutive-value XORs (Pelkonen VLDB 2015); a ClickHouse codec | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| Volnitsky | Bigram-hash substring search algorithm (Boyer-Moore-Horspool variant) used in ClickHouse string/LIKE matching | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| NuRaft | C++ Raft consensus library underpinning ClickHouse Keeper, the ZooKeeper-compatible coordination service | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| Projection | ClickHouse alternate physical layout stored inside each part (different sort order and/or pre-aggregation), auto-maintained through merges | [ClickHouse Internals](@/notes/database/clickhouse_internals.md) |
| MESIF | Modified/Exclusive/Shared/Invalid/Forward — Intel's extension of MESI with a Forward state for peer-to-peer cache supply | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| MESI | Modified/Exclusive/Shared/Invalid — CPU cache coherence protocol tracking cache line states across cores | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| MCV | Most Common Values — per-column list of (value, frequency) pairs stored in pg_statistic stakind=1; used for exact selectivity on high-frequency values | [Database Statistics](@/notes/database/database_statistics.md) |
| NDV | Number of Distinct Values — column statistic driving join selectivity (1/max(NDV_R, NDV_S)); estimated via HLL or Haas-Stokes sampler | [Database Statistics](@/notes/database/database_statistics.md) |
| MLP | Memory-Level Parallelism — number of simultaneous outstanding cache misses a core can sustain; bounded by ROB size and MSHR count | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| MOESI | Modified/Owned/Exclusive/Shared/Invalid — AMD's extension of MESI with Owned state for dirty-line sharing without writeback | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| MPKI | Misses Per Kilo-Instructions — branch or cache miss rate metric; TAGE achieves <3% branch MPKI on SPEC CPU 2006 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| MSHR | Miss Status Holding Register — tracks outstanding cache misses and coalesces accesses to the same line; count ≈ MLP | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| MMA | Matrix Multiply-Accumulate — Tensor Core operation computing D = A * B + C on small matrix tiles | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| MMIO | Memory-Mapped I/O — mapping device registers into CPU address space for direct read/write access | [VFIO Internals](@/notes/os/vfio_internals.md) |
| Morsel | A small chunk of a source operator's input handed to a worker thread; unit of morsel-driven parallelism and work stealing | [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| MPSM | Massively Parallel Sort-Merge — NUMA-aware join algorithm with local sort + parallel merge across nodes | [Join Algorithms](@/notes/database/join_algorithms.md) |
| MSI-X | Message Signaled Interrupts Extended — PCIe interrupt delivery via memory writes, supporting per-queue interrupt vectors | [VFIO Internals](@/notes/os/vfio_internals.md) |
| MTE | Memory Tagging Extension — ARM hardware feature for detecting memory safety bugs (use-after-free, buffer overflow) | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| MVCC | Multi-Version Concurrency Control — concurrency scheme where readers see snapshots and writers create new versions | [Database Systems](@/notes/database/database_systems.md), [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| NoC | Network-on-Chip — on-die interconnect (ring/mesh/torus) routing traffic between cores, caches, and memory controllers | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md), [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| NLJ | Nested Loop Join — simplest join algorithm scanning inner relation for each outer tuple; O(\|R\| * B(S)) I/O | [Join Algorithms](@/notes/database/join_algorithms.md) |
| NUMA | Non-Uniform Memory Access — multi-socket architecture where memory access latency depends on which socket owns the memory | [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| NVIC | Nested Vectored Interrupt Controller — ARM Cortex-M interrupt controller with priority-based preemption | [Timer Interrupts STM32](@/notes/os/timer_interrupts_stm32.md) |
| NVLink | NVIDIA proprietary high-bandwidth GPU-to-GPU interconnect (NVLink5: 1.8 TB/s bidirectional) | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| OCC | Optimistic Concurrency Control — transaction scheme allowing concurrent execution, validating at commit time | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| OID | Object Identifier — PostgreSQL's internal numeric identifier for database objects (types, relations, functions) | [Arrow PostgreSQL Integration](@/notes/database/arrow_postgresql_integration.md) |
| OLAP | Online Analytical Processing — workload pattern of complex read-heavy aggregation queries (DuckDB, ClickHouse) | [Database Systems](@/notes/database/database_systems.md) |
| OLTP | Online Transaction Processing — workload pattern of high-throughput short read-write transactions (PostgreSQL, MySQL) | [Database Systems](@/notes/database/database_systems.md) |
| OoO | Out-of-Order execution — CPU technique issuing instructions in data-dependency order rather than program order to hide latency | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| PAC | Pointer Authentication Code — ARM cryptographic signature embedded in pointer unused bits for control-flow integrity | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| PACELC | Partition-Availability-Consistency / Else Latency-Consistency — extension of CAP capturing normal-operation trade-offs | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| PASID | Process Address Space ID — IOMMU feature enabling per-process DMA address translation for shared virtual addressing | [VFIO Internals](@/notes/os/vfio_internals.md) |
| PAX | Partition Attributes Across — hybrid row/column page layout storing columns within each page (Umbra) | [Database Systems](@/notes/database/database_systems.md) |
| PBFT | Practical Byzantine Fault Tolerance — first practical BFT protocol tolerating f Byzantine faults with 3f+1 replicas | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| PEBS | Precise Event-Based Sampling — Intel hardware profiling capturing exact instruction pointer on performance counter overflow | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| PRF | Physical Register File — centralized storage for all in-flight register values; separate INT and FP files sized at ROB + arch_regs | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| PG | Protection Group — Aurora's 10 GB storage segment replicated 6 ways across 3 AZs | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| PID | Page ID — logical identifier for a database page, translated to a buffer frame address by the buffer manager | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| Pipeline Breaker | Operator that must fully consume its input before producing output (hash-join build, aggregate, sort); materializes into pipeline-local state and acts as the source of a downstream pipeline | [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| PITR | Point-In-Time Recovery — restoring a database to any past moment by replaying WAL to a target LSN/timestamp | [Database Systems](@/notes/database/database_systems.md) |
| PLL | Phase-Locked Loop — clock generation circuit multiplying a reference crystal frequency for the system clock | [Timer Interrupts STM32](@/notes/os/timer_interrupts_stm32.md) |
| PMU | Performance Monitoring Unit — hardware counters (cycles, cache misses, branch mispredictions) for CPU profiling | [Cycle Counters & Energy](@/notes/hardware/cycle_counters_and_energy.md) |
| RBPEX | Resilient Buffer Pool Extension — local SSD cache in Azure SQL Hyperscale surviving process restarts | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| RowContainer | Velox row-major slab storing group keys and accumulators: fields ordered as (normalized key, null bits, fixed 8-byte slots, variable-width section, accumulators, probed flag) | [Velox Internals](@/notes/database/velox_internals.md) |
| Q-error | Cardinality estimation accuracy metric: max(est/actual, actual/est) ≥ 1; Q-error=1 is perfect; JOB benchmark shows PostgreSQL p95 ≈ 12× | [Database Statistics](@/notes/database/database_statistics.md) |
| RAS | Return Address Stack — hardware stack that speculatively captures call targets to predict return addresses | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| RAT | Register Alias Table — maps architectural register names to physical register IDs during OoO rename stage | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| RCU | Read-Copy-Update — Linux kernel synchronization allowing lock-free reads with deferred reclamation of old data | [Data Structures](@/notes/programming/data_structures.md) |
| RDMA | Remote Direct Memory Access — network hardware reading/writing remote memory without CPU involvement (~10 us latency) | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| RDTSC | Read Time-Stamp Counter — x86 instruction reading the 64-bit cycle counter; RDTSCP variant serializes prior instructions | [Cycle Counters & Energy](@/notes/hardware/cycle_counters_and_energy.md) |
| ROB | Reorder Buffer — circular buffer holding all in-flight µops; enables in-order retirement and precise exception handling | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| RLE | Run-Length Encoding — compression encoding consecutive identical values as (value, count) pairs | [Database Systems](@/notes/database/database_systems.md) |
| RMI | Recursive Model Index — learned index structure using a hierarchy of ML models to predict key positions | [LSM Trees](@/notes/database/lsm_trees.md) |
| RTL | Register Transfer Level — hardware description abstraction (Verilog/VHDL) defining logic in terms of registers and operations | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| RUM | Read, Update, Memory conjecture — states you can optimize at most two of read/write/space overhead in an index | [LSM Trees](@/notes/database/lsm_trees.md) |
| RVWMO | RISC-V Weak Memory Ordering — RISC-V's relaxed memory model preserving only data dependencies and same-address ordering | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| RVV | RISC-V Vector extension — scalable vector ISA with LMUL register grouping and vector-length agnostic (VLA) programming | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| SQ | Store Queue — buffer holding committed stores until they drain to the L1D cache; used for STLF and memory ordering | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| SCL | Segment Complete LSN — per-Protection-Group completeness tracker in Aurora's storage layer | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| seccomp | Secure Computing Mode — Linux syscall filtering mechanism using BPF programs for sandboxing (used in Neon WAL redo) | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Selection Vector | Array of indices into a vector selecting surviving rows after a filter; threaded downstream so filtered data is not compacted until materialization (DuckDB vectorized engine) | [DuckDB Internals](@/notes/database/duckdb_internals.md) |
| SelectivityVector | Velox bitmask (uint64_t words, 64 rows/word) of active rows passed between operators and into expression eval; applyToSelected() iterates via __builtin_ctzll | [Velox Internals](@/notes/database/velox_internals.md) |
| SharedArbitrator | Velox MemoryArbitrator implementation for global fair memory sharing across queries; 3-pass reclaim: free capacity → spill largest → abort victim | [Velox Internals](@/notes/database/velox_internals.md) |
| StringView | Velox 16-byte string representation: [size:4][inline:12] for ≤12 chars, or [size:4][prefix:4][ptr:8] for longer; enables fail-fast comparison and zero-copy substr | [Velox Internals](@/notes/database/velox_internals.md) |
| SFU | Special Function Unit — GPU hardware computing transcendentals (sin, cos, rsqrt, log) at reduced throughput | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| SIMT | Single Instruction, Multiple Thread — GPU execution model where warps of 32 threads execute in lockstep | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| SM | Streaming Multiprocessor — fundamental GPU compute unit containing CUDA cores, Tensor Cores, register file, and shared memory | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| SME | Scalable Matrix Extension — ARM extension for matrix operations using a 2D tile register (ZA) for GEMM acceleration | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| SMJ | Sort-Merge Join — join algorithm sorting both relations then merging; optimal when inputs are pre-sorted | [Join Algorithms](@/notes/database/join_algorithms.md) |
| SMMU | System Memory Management Unit — ARM's IOMMU implementation (SMMUv3) for DMA address translation and device isolation | [VFIO Internals](@/notes/os/vfio_internals.md) |
| STLF | Store-to-Load Forwarding — hardware mechanism supplying load data directly from the store queue, bypassing cache (~4-5 cycles) | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| SPDK | Storage Performance Development Kit — userspace NVMe driver framework using VFIO for millions of IOPS per core | [VFIO Internals](@/notes/os/vfio_internals.md) |
| SPSC | Single Producer Single Consumer — lock-free queue variant with one writer and one reader thread | [Data Structures](@/notes/programming/data_structures.md) |
| SQE | Submission Queue Entry — io_uring user-to-kernel I/O request structure (opcode, fd, buffer, offset) | [io_uring Internals](@/notes/os/io_uring_internals.md) |
| SQPOLL | Submission Queue Polling — io_uring mode where a kernel thread polls the SQ, eliminating syscalls entirely | [io_uring Internals](@/notes/os/io_uring_internals.md) |
| SR-IOV | Single Root I/O Virtualization — PCIe spec creating lightweight virtual functions from one physical device | [VFIO Internals](@/notes/os/vfio_internals.md) |
| SSI | Serializable Snapshot Isolation — PostgreSQL's true serializable isolation via predicate locking and conflict detection | [Database Systems](@/notes/database/database_systems.md) |
| SSTable | Sorted String Table — immutable, sorted on-disk file in LSM trees containing key-value pairs with index/bloom filter | [LSM Trees](@/notes/database/lsm_trees.md) |
| STLR | Store-Release Register — ARM instruction providing release semantics (no preceding access reordered after it) | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| SVA | Shared Virtual Addressing — IOMMU feature letting devices use the same virtual addresses as the CPU process | [VFIO Internals](@/notes/os/vfio_internals.md) |
| SVE | Scalable Vector Extension — ARM vector ISA with hardware-defined vector length (128-2048 bits) for portable SIMD | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| TAGE | Tagged Geometric History Length Branch Predictor — state-of-the-art predictor using multiple tagged components indexed by geometric history lengths (Seznec 2006) | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| THP | Transparent Huge Pages — Linux kernel feature automatically promoting 4KB page allocations to 2MB pages to reduce TLB pressure | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| TF32 | TensorFloat-32 — NVIDIA 19-bit format (8-bit exponent, 10-bit mantissa) for Tensor Core GEMM on Ampere+ | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| TLB | Translation Lookaside Buffer — CPU/IOMMU cache for virtual-to-physical address translations | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| TOAST | The Oversized-Attribute Storage Technique — PostgreSQL mechanism compressing/storing large field values out-of-line | [Database Systems](@/notes/database/database_systems.md) |
| TrueTime | Google's globally-synchronized clock API returning bounded time intervals using GPS + atomic clocks (Spanner) | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| TSC | Time Stamp Counter — x86 hardware counter incrementing at a fixed reference frequency, read via RDTSC/RDTSCP | [Cycle Counters & Energy](@/notes/hardware/cycle_counters_and_energy.md) |
| TSO | Total Store Order — x86 memory model where only Store-Load reordering is permitted; most lock-free code "just works" | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| TSX | Transactional Synchronization Extensions — Intel hardware transactional memory (XBEGIN/XEND), deprecated due to security issues | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| UCIe | Universal Chiplet Interconnect Express — open standard for die-to-die communication in chiplet-based designs | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| UIO | Userspace I/O — early Linux framework for userspace device drivers; no DMA isolation (predecessor to VFIO) | [VFIO Internals](@/notes/os/vfio_internals.md) |
| userfaultfd | User Fault File Descriptor — Linux syscall letting userspace handle page faults (used for live migration, lazy restore) | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Velox | Meta's open-source C++ vectorized execution engine library — embeds into Presto (Prestissimo), Spark (Gluten), and other engines to share one high-quality vectorized kernel | [Velox Internals](@/notes/database/velox_internals.md) |
| VectorEncoding | Velox encoding taxonomy for BaseVector subclasses: FLAT, CONSTANT, DICTIONARY, BIASED, SEQUENCE, LAZY, ROW, MAP, ARRAY | [Velox Internals](@/notes/database/velox_internals.md) |
| VectorLoader | Velox callback object wrapped by LazyVector; called to decode a column on first access (late materialization) | [Velox Internals](@/notes/database/velox_internals.md) |
| VIPT | Virtually Indexed Physically Tagged — I-cache design using virtual bits for set index (fast) and physical tag for correctness (no aliasing if index bits lie within page offset) | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| VCL | Volume Complete LSN — Aurora's highest LSN for which all prior log records reached all storage quorum nodes | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| VDL | Volume Durable LSN — Aurora's effective recovery point: highest CPL <= VCL | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| VFIO | Virtual Function I/O — Linux kernel framework for safe userspace device drivers using IOMMU DMA isolation | [VFIO Internals](@/notes/os/vfio_internals.md) |
| VLA | Vector-Length Agnostic — programming model where code adapts to hardware vector width at runtime (ARM SVE, RISC-V RVV) | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| VR | Viewstamped Replication — consensus protocol by Oki/Liskov using views and viewstamps, equivalent to Multi-Paxos | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| VT-d | Virtualization Technology for Directed I/O — Intel's IOMMU implementation for DMA remapping and device isolation | [VFIO Internals](@/notes/os/vfio_internals.md) |
| WAL | Write-Ahead Log — durability mechanism requiring all changes to be logged before being written to data files | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| WATT | Write-Aware Timestamp Tracking — eviction policy tracking write timestamps for better page replacement decisions | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| WCOJ | Worst-Case Optimal Join — join algorithm (e.g., LeapfrogTrieJoin) matching the AGM bound for cyclic queries | [Join Algorithms](@/notes/database/join_algorithms.md) |
| WiredTiger | MongoDB's default B-tree storage engine using copy-on-write, MVCC, and hazard pointers for concurrency | [MongoDB/WiredTiger Internals](@/notes/database/mongodb_wiredtiger_internals.md) |
| XDP | eXpress Data Path — Linux eBPF-based programmable network processing at the NIC driver level before kernel stack | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Z-set | Generalized multiset with integer weights (positive=insert, negative=delete) — core data model of DBSP/Feldera | [Database Systems](@/notes/database/database_systems.md) |
| Ztso | RISC-V TSO extension — provides Total Store Order semantics for x86 binary translation compatibility | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| AMS | AMS Sketch (Alon-Matias-Szegedy 1999) — randomized sketch estimating second frequency moment F₂ = Σfᵢ²; basis for join size estimation | [Database Statistics](@/notes/database/database_statistics.md) |
| ACORN | Approximate search framework supporting predicate-agnostic filtered ANN by expanding beam width to compensate for filtered nodes in HNSW graph | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| ADC | Asymmetric Distance Computation — ANN technique precomputing query-to-codebook distances into lookup table; O(M) distance vs O(d) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| ANN | Approximate Nearest Neighbor — find vector within (1+ε) × optimal distance; trades recall for speed; graph/IVF/quantization methods | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| BEIR | Benchmark for heterogeneous zero-shot IR evaluation — 18 datasets (web/bio/legal/sci); reveals generalization gap of dense models vs BM25 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| BKD-tree | Disk-friendly k-d tree variant used in Lucene for numeric and geo range queries; leaf blocks of 512–1024 points | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| BM25 | Best Match 25 — probabilistic term-weighting ranking function (Robertson et al. 1994); de-facto standard for keyword search | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| BMW | Block-Max WAND — extends WAND with per-block max scores for finer-grained postings skipping (Ding & Suel SIGIR 2011) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| CAGRA | CUDA ANNS GRAph-based — NVIDIA GPU-native graph ANN algorithm; 33–77× faster than CPU HNSW for batch search | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| ColBERT | Contextualized Late Interaction over BERT — per-token embeddings + MaxSim aggregation; stronger quality than bi-encoder, more storage | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| DiskANN | Microsoft disk-resident ANN system using Vamana graph; 1B vectors on 64GB RAM + NVMe; >95% recall@1 at <5ms (NeurIPS 2019) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| DPR | Dense Passage Retrieval — bi-encoder dense retrieval (Karpukhin et al. EMNLP 2020); trained with in-batch + BM25 hard negatives | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| HNSW | Hierarchical Navigable Small World — multi-layer proximity graph for ANN; O(ef × log n) search; dominant algorithm on ann-benchmarks | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| IVF | Inverted File Index — k-means partition ANN; scan only nprobe nearest centroid lists; base of FAISS IVFPQ | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| LSH | Locality Sensitive Hashing — hash collision probability proportional to similarity; random projections for L2, SimHash for cosine | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| MaxScore | Early termination algorithm splitting postings into essential/non-essential lists; rank-safe top-K (Turtle & Flood 1995) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| MIPS | Maximum Inner Product Search — variant of ANN for inner product similarity; used in recommendation and dense retrieval | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| MRL | Matryoshka Representation Learning — embeddings meaningful at all prefix lengths [8..2048]; truncate at inference (Kusupati NeurIPS 2022) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| MTEB | Massive Text Embedding Benchmark — 56 tasks across 8 categories; standard leaderboard for sentence/passage embedding models | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| PQ | Product Quantization — split d-dim vector into M subspaces of d/M dims each, quantize independently; M bytes per vector (Jégou 2011) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| PLAID | Performance-optimized Late Interaction Driver — centroid interaction pre-filter for ColBERT; 45× faster on CPU (CIKM 2022) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| RaBitQ | Rotation + 1-bit quantization — apply random rotation before binary quantization; tight theoretical error bound (Gao SIGMOD 2024) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| RRF | Reciprocal Rank Fusion — score = Σ 1/(k + rank_r); parameter-free fusion of multiple ranked lists (Cormack SIGIR 2009) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| ScaNN | Scalable Nearest Neighbor — Google ANN library using anisotropic quantization; 2× faster than competitors on ann-benchmarks (ICML 2020) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| SPLADE | Sparse Lexical and Expansion — BERT MLM head → 30K sparse vector with term expansion + weighting; served via inverted index (SIGIR 2021) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| WAND | Weak AND — pivot-based postings skip algorithm for top-K; rank-safe, 10–25× faster than DAAT (Broder et al. CIKM 2003) | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| ACE | AXI Coherency Extensions — ARM extension adding snoop channels (AC/CR/CD) to AXI for cache-coherent masters; ACE-Lite for non-cached coherent agents (DMA, accelerators) | [Interconnects](@/notes/hardware/interconnects.md) |
| AIB | Advanced Interface Bus — Intel-originated open chiplet D2D standard (1024 wires/channel); used in EMIB-based Sapphire Rapids/Ponte Vecchio; largely subsumed by UCIe Advanced | [Interconnects](@/notes/hardware/interconnects.md) |
| AXI | Advanced eXtensible Interface — Arm AMBA bus standard; AXI4 has 5 independent channels (AW/W/B/AR/R); AXI5 adds atomics and unique-ID interleave | [Interconnects](@/notes/hardware/interconnects.md) |
| BoW | Bunch of Wires — OCP/OIF chiplet D2D parallel-wire standard targeting < 2 mm; up to 16 GT/s/wire; largely subsumed by UCIe | [Interconnects](@/notes/hardware/interconnects.md) |
| CHI | Coherent Hub Interface — Arm AMBA packet-based mesh fabric; scales to 256-core server chips (Neoverse N2/V2 CMN-700); supports snoopy + directory coherence | [Interconnects](@/notes/hardware/interconnects.md) |
| CPO | Co-Packaged Optics — placing optical engines directly on switch ASIC substrate to eliminate PCB trace loss at 1.6T+; Broadcom Tomahawk 5/6, NVIDIA Quantum-X Photonics | [Interconnects](@/notes/hardware/interconnects.md) |
| CQ | Completion Queue — RDMA structure where NIC writes a CQE per completed Work Request; polled or interrupt-driven | [Interconnects](@/notes/hardware/interconnects.md) |
| DCB | Data Center Bridging — IEEE 802.1 extensions (PFC + ETS + QCN + DCBX) enabling lossless Ethernet for RoCE/FCoE | [Interconnects](@/notes/hardware/interconnects.md) |
| DCBX | Data Center Bridging Exchange — LLDP-based protocol exchanging DCB capabilities/config between switch and endpoint | [Interconnects](@/notes/hardware/interconnects.md) |
| DCQCN | Datacenter QCN — RoCEv2 congestion control combining switch ECN marking, CNP feedback, and rate adjustment at endpoint (Zhu SIGCOMM 2015) | [Interconnects](@/notes/hardware/interconnects.md) |
| DCT | Dynamic Connected Transport — InfiniBand QP type using shared pool of QPs dynamically retargeted per peer; required for 10k+ rank scale | [Interconnects](@/notes/hardware/interconnects.md) |
| DCTCP | Datacenter TCP (Alizadeh SIGCOMM 2010) — TCP variant using ECN with fractional marking + α-smoothing for low-latency DC | [Interconnects](@/notes/hardware/interconnects.md) |
| ECMP | Equal-Cost Multi-Path — routing technique distributing flows across multiple equal-cost paths via hash of packet fields; suffers hash collision under skew | [Interconnects](@/notes/hardware/interconnects.md) |
| ETS | Enhanced Transmission Selection (IEEE 802.1Qaz) — DCB feature for proportional bandwidth allocation across 8 traffic class groups | [Interconnects](@/notes/hardware/interconnects.md) |
| FCP | Fibre Channel Protocol — SCSI-over-FC mapping; the original SAN protocol; largely replaced by NVMe-oF/FC for new deployments | [Interconnects](@/notes/hardware/interconnects.md) |
| FEC | Forward Error Correction — channel coding (RS(528,514), RS(544,514), KR4) used in 25/50/100+ GbE to recover from bit errors; mandatory above 50G PAM4 | [Interconnects](@/notes/hardware/interconnects.md) |
| GFAM | Global Fabric Attached Memory — CXL 3.0+ pooled coherent memory accessible by any host in a CXL fabric; sub-µs latency at TB scale | [Interconnects](@/notes/hardware/interconnects.md) |
| GMI | Global Memory Interconnect — AMD on-package coherent interconnect linking CCDs to the IOD on EPYC; GMI3 at 36 GT/s | [Interconnects](@/notes/hardware/interconnects.md) |
| HDM-DB | Host-managed Device Memory — Device-managed coherence (CXL 3.0+) where device tracks host caches and issues back-invalidations; enables fabric-attached coherent memory pools >1 TB | [Interconnects](@/notes/hardware/interconnects.md) |
| HPCC | High Precision Congestion Control (Li SIGCOMM 2019) — in-band-telemetry-based CC for RDMA; per-hop queue + utilization embedded in packets | [Interconnects](@/notes/hardware/interconnects.md) |
| IBA | InfiniBand Architecture — IBTA's full layered spec; covers physical, link, network, transport, and management layers | [Interconnects](@/notes/hardware/interconnects.md) |
| ICI | Inter-Chip Interconnect — Google's TPU pod fabric; 3D torus with OCS reconfiguration in v4+ (Jouppi et al. ISCA 2023) | [Interconnects](@/notes/hardware/interconnects.md) |
| IDE | Integrity and Data Encryption — CXL link-layer AES-GCM encryption per FLIT; selectable per virtual channel | [Interconnects](@/notes/hardware/interconnects.md) |
| IFIS | Infinity Fabric Inter-Socket — AMD inter-socket coherent interconnect (xGMI variant); 32 GT/s at Zen 4 | [Interconnects](@/notes/hardware/interconnects.md) |
| IFOP | Infinity Fabric On-Package — AMD on-package coherent link between CCD and IOD; 32-36 GT/s at Zen 4/5 | [Interconnects](@/notes/hardware/interconnects.md) |
| MACsec | IEEE 802.1AE — L2 line-rate AES-128/256-GCM encryption between Ethernet hops; standard on enterprise/DC NICs | [Interconnects](@/notes/hardware/interconnects.md) |
| MR | Memory Region — RDMA registered+pinned+IOMMU-mapped buffer; has lkey (local) and rkey (remote) tokens; expensive to register (10s of ms per GB) | [Interconnects](@/notes/hardware/interconnects.md) |
| MTU | Maximum Transmission Unit — largest L2 frame supported; default 1500B Ethernet; "jumbo" 9000B common in DC; matters for PFC headroom + RoCE | [Interconnects](@/notes/hardware/interconnects.md) |
| MZM | Mach-Zehnder Modulator — silicon-photonics modulator that splits light into two arms, applies electrical phase shift on one, recombines; output amplitude = cos²(Δφ/2) | [Interconnects](@/notes/hardware/interconnects.md) |
| NCCL | NVIDIA Collective Communications Library — GPU-native AllReduce/AllGather/Broadcast library; uses NVLink/IB/RoCE; supports NVLS in-network reduction | [Interconnects](@/notes/hardware/interconnects.md) |
| NeuronLink | AWS Trainium proprietary interconnect; NeuronLink-v3 at ~12 Tbps aggregate per chip on Trainium2 | [Interconnects](@/notes/hardware/interconnects.md) |
| NIXL | NVIDIA Inference Transfer Library (2024-2025) — disaggregated KV-cache transport for LLM serving; integrates Dynamo/vLLM | [Interconnects](@/notes/hardware/interconnects.md) |
| NPIV | N_Port ID Virtualization — Fibre Channel feature letting multiple virtual ports share one HBA; required for VM passthrough on FC SANs | [Interconnects](@/notes/hardware/interconnects.md) |
| NRZ | Non-Return-to-Zero — binary signaling (1 bit/symbol); used in PCIe 1-5, Ethernet up to 25 Gbaud; superseded by PAM4 above 50 Gbaud | [Interconnects](@/notes/hardware/interconnects.md) |
| NVL72 | NVIDIA NVLink 72 — rack-scale architecture with 72 B200 GPUs in single coherent NVLink domain; 9 NVSwitch trays, 130 TB/s aggregate, copper backplane | [Interconnects](@/notes/hardware/interconnects.md) |
| NVLS | NVLink Sharp — in-switch reduction on NVSwitch 3.0+; halves AllReduce bandwidth requirement vs ring | [Interconnects](@/notes/hardware/interconnects.md) |
| NVMe-oF | NVMe over Fabrics — NVMe wire protocol over RDMA (RoCE/IB), TCP, or FC; replaces iSCSI/FC for SSD-class storage networking | [Interconnects](@/notes/hardware/interconnects.md) |
| OCS | Optical Circuit Switch — switch routing entirely in optical domain (MEMS mirrors or AWG); slow reconfig (ms), but very high BW/power efficiency once configured | [Interconnects](@/notes/hardware/interconnects.md) |
| ODP | On-Demand Paging — RDMA NIC feature replacing MR page-pinning with on-the-fly page faults via PCIe ATS+PRI; ~5-10 µs fault penalty | [Interconnects](@/notes/hardware/interconnects.md) |
| OFI | OpenFabrics Interfaces — libfabric API and provider framework (verbs/EFA/psm3/cxi/tcp); alternative to UCX, preferred by AWS/Cray/Intel stacks | [Interconnects](@/notes/hardware/interconnects.md) |
| OpenHBI | OCP High Bandwidth Interface — chiplet D2D spec targeting HBM-class memory interconnect; largely overlapped by HBM PHY and UCIe | [Interconnects](@/notes/hardware/interconnects.md) |
| PAM4 | Pulse Amplitude Modulation 4-level — 2 bits/symbol signaling; doubles baud-rate vs NRZ at cost of lower SNR; standard for 50G+ per-lane Ethernet/PCIe 6+ | [Interconnects](@/notes/hardware/interconnects.md) |
| PFC | Priority-based Flow Control (IEEE 802.1Qbb) — pause only one of 8 traffic classes per port; required for lossless Ethernet (RoCEv2, FCoE) | [Interconnects](@/notes/hardware/interconnects.md) |
| QCN | Quantized Congestion Notification (IEEE 802.1Qau) — DCB explicit-feedback CC; largely superseded by ECN-based protocols | [Interconnects](@/notes/hardware/interconnects.md) |
| QP | Queue Pair — RDMA endpoint pair (send queue + receive queue); types: RC, UC, UD, XRC, DCT | [Interconnects](@/notes/hardware/interconnects.md) |
| RNR | Receiver Not Ready — RDMA NAK indicating receiver had no posted RECV when SEND arrived; triggers sender backoff + retry | [Interconnects](@/notes/hardware/interconnects.md) |
| RoCE | RDMA over Converged Ethernet — verbs over Ethernet (v1 L2-only, dead) or UDP/IP (v2, port 4791, dominant) | [Interconnects](@/notes/hardware/interconnects.md) |
| RoCEv2 | RoCE version 2 — RDMA verbs encapsulated in UDP/IP; routable; requires lossless fabric (PFC) + ECN-based CC (DCQCN); UDP port 4791 | [Interconnects](@/notes/hardware/interconnects.md) |
| SerDes | Serializer/Deserializer — high-speed parallel-to-serial signaling IP; the fundamental scaling unit (per-lane signaling) of all modern interconnects | [Interconnects](@/notes/hardware/interconnects.md) |
| SHARP | Scalable Hierarchical Aggregation and Reduction Protocol — Mellanox in-switch reduction for IB; halves AllReduce bandwidth requirement | [Interconnects](@/notes/hardware/interconnects.md) |
| TDISP | TEE Device Interface Security Protocol — PCIe spec (adopted by CXL) for attesting confidential devices; required for confidential CXL/PCIe accelerator workloads | [Interconnects](@/notes/hardware/interconnects.md) |
| TileLink | Open RISC-V coherent chip protocol (UC Berkeley); three tiers TL-UL/TL-UH/TL-C; used in SiFive/BOOM/Chipyard | [Interconnects](@/notes/hardware/interconnects.md) |
| UALink | Ultra Accelerator Link — 2024 open consortium (AMD/Broadcom/Cisco/Google/Intel/Meta/MS/HPE); coherent NVLink alternative; targets 1024-GPU domains via Ethernet PHY + custom protocol | [Interconnects](@/notes/hardware/interconnects.md) |
| UEC | Ultra Ethernet Consortium — 2023-2025 Linux Foundation project; UEC 1.0 spec (Jun 2025) defines RUD/RUDI transport with packet spraying + modern CC for AI on commodity Ethernet | [Interconnects](@/notes/hardware/interconnects.md) |
| UPI | Ultra Path Interconnect — Intel inter-socket/inter-die coherent fabric (MESIF protocol); 10.4 GT/s (SKL) → 24 GT/s (GNR) | [Interconnects](@/notes/hardware/interconnects.md) |
| WR | Work Request — RDMA element posted to a QP's send or receive queue describing an I/O (opcode, sg_list, remote_addr/rkey, etc.) | [Interconnects](@/notes/hardware/interconnects.md) |
| xGMI | Inter-Socket Global Memory Interconnect — AMD coherent link between EPYC sockets (and between MI300 GPUs); 32 GT/s at gen4-5 | [Interconnects](@/notes/hardware/interconnects.md) |
| ZR | Coherent optical pluggable family — 400ZR/800ZR for metro distances (80-120 km unamplified) using DP-16QAM with integrated DSP | [Interconnects](@/notes/hardware/interconnects.md) |

## Cache Eviction, Admission & Prefetching

| Term | Definition | Detailed In |
|---|---|---|
| ARC | Adaptive Replacement Cache — self-tuning O(1) policy with T1 (recency) + T2 (frequency) lists and B1/B2 ghost lists; adaptive partition point p updated on ghost hits; Megiddo & Modha FAST 2003; used in ZFS, DB2 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| CLOCK-Pro | Approximation of LIRS using three clock hands (hot/cold/test); scan-resistant without explicit ghost lists; lower memory overhead than ARC; Jiang et al. ATC 2005; used in NetBSD VM | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| GDSF | Greedy Dual Size Frequency — priority-queue eviction using key = (freq/size) + clock-inflation L; scan-resistant, size-aware; web proxy caching; Cherkasova HPL 1998; used in Squid | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| GL-Cache | Group-level Learned Cache — ML model ranks groups of objects for batch eviction rather than individual objects; amortises inference cost; Yang et al. FAST 2023 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| LeCaR | Learning Cache Replacement — online RL mixture of LRU + LFU experts via multiplicative-weights update; regret-minimising; Vietri et al. HotStorage 2018 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| LHD | Least Hit Density — evicts object with lowest estimated hits-per-byte-per-time-unit; sampled from random candidates; class-based histogram; Beckmann & Sanfilippo NSDI 2018 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| LIRS | Low Inter-reference Recency Set — recency stack with HIR (high IRR) / LIR (low IRR) classification; promotes repeatedly-hit items; scan-resistant; Jiang & Zhang SIGMETRICS 2002; used in H2 DB, Caffeine (historical) | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| LRB | Learning Relaxed Belady — GBDT model trained offline on request traces to predict next reuse time; approximates Belady's OPT; Song et al. NSDI 2020; deployed in Apache Traffic Server research | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| LRU-K | LRU variant tracking K most-recent access timestamps per object; evicts object with oldest K-th reference; eliminates one-hit wonders; O'Neil et al. SIGMOD 1993 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| MGLRU | Multi-Generational LRU — Linux 6.1+ page reclaim using hardware-assisted generation counters (PG_referenced + page table young bits); replaces clock-sweep for anonymous + file pages; Kuo LKML 2022 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| MRC | Miss Ratio Curve — function mapping cache size → miss ratio; computed via reuse-distance analysis (exact) or SHARDS sampling (approximate); essential for cache sizing decisions | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| QD-LP | Quick-Demotion Large-Protection — small FIFO filter for one-hit-wonder demotion (QD) + large LRU main region with lazy promotion (LP); Yang et al. HotOS 2023 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| S3-FIFO | Small/Slow/Sliding FIFO — three FIFO queues: S (10% capacity) + M (90%) + G (ghost); frequency bit in S promotes to M on second access; simple, scan-resistant, low metadata overhead; Yang et al. SOSP 2023 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| SHARDS | Spatially Hashed Approximate Reuse Distance Sampling — O(1) amortised MRC construction via consistent hashing on object keys; 1% sampling rate with <1% miss ratio error; Waldspurger et al. FAST 2015 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| SIEVE | SIEVE eviction — single FIFO queue + hand pointer; visited bit cleared on first eviction pass (lazy demotion); simpler than LRU, competitive hit rate on CDN workloads; Zhang et al. NSDI 2024 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| SLRU | Segmented LRU — two LRU segments: probationary (new entries) + protected (second-hit promoted); objects demoted from protected → probationary on eviction pressure; used as main cache in W-TinyLFU | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| TinyLFU | Tiny LFU admission filter — 4-bit Count-Min Sketch frequency estimator + doorkeeper Bloom filter; admits new object only if frequency ≥ eviction candidate; reset-based aging; Einziger et al. 2017 | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
| W-TinyLFU | Window-TinyLFU — 1% window LRU + 99% SLRU main cache + TinyLFU admission gate + hill-climbing window-size tuner; production standard; used in Caffeine, Cassandra, Kafka, Solr, HBase, Neo4j | [Cache Algorithms](@/notes/database/cache_algorithms.md) |
