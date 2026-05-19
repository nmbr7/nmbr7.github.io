+++
title = "Papers"
date = 2026-03-26
[taxonomies]
tags = ["papers", "reference", "index"]
+++


# Papers Index

Deduplicated index of papers referenced across research docs. Sorted alphabetically by first author within each section.

---

## Database Internals & Query Processing

| Paper | Venue | Cited In |
|---|---|---|
| Abadi, Madden, Hachem, "The Design and Implementation of Modern Column-Oriented Database Systems" | Foundations and Trends in Databases, 2013 | [Arrow Format](@/notes/database/arrow_format.md), [Arrow PG Integration](@/notes/database/arrow_postgresql_integration.md) |
| Athanassoulis, Kester et al., "Designing Access Methods: The RUM Conjecture" | EDBT 2016 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Berenson et al., "A Critique of ANSI SQL Isolation Levels" | SIGMOD 1995 | [MongoDB & WiredTiger](@/notes/database/mongodb_wiredtiger_internals.md) |
| Bottcher, Leis, Neumann, Kemper, "Scalable Garbage Collection for In-Memory MVCC Systems" | VLDB 2019 | [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Budiu et al., "DBSP" (Database Stream Processor framework) | VLDB 2023 | [Database Systems](@/notes/database/database_systems.md) |
| Cahill et al., Serializable Snapshot Isolation | 2008 | [Database Systems](@/notes/database/database_systems.md) |
| Diaconu et al., "Hekaton: SQL Server's Memory-Optimized OLTP Engine" | SIGMOD 2013 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Freitag, Kemper, Neumann, "Memory-Optimized Multi-Version Concurrency Control for Disk-Based Database Systems" | VLDB 2022 | [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Graefe, "Modern B-Tree Techniques" | Foundations and Trends in Databases, 2011 | [MongoDB & WiredTiger](@/notes/database/mongodb_wiredtiger_internals.md), [Data Structures](@/notes/programming/data_structures.md) |
| Graefe, "Query Evaluation Techniques for Large Databases" | ACM Computing Surveys, 1993 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Gray, Putzolu, "The Five-Minute Rule for Trading Memory for Disc Accesses" | SIGMOD 1987 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Gray, Reuter, "Transaction Processing: Concepts and Techniques" | Morgan Kaufmann, 1993 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Hellerstein, Stonebraker, Hamilton, "Architecture of a Database System" | Foundations and Trends in Databases, 2007 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Kersten, Leis, Neumann, "Tidy Tuples and Flying Start: Fast Compilation and Fast Execution of Relational Queries in Umbra" | VLDB Journal 2021 | [Database Systems](@/notes/database/database_systems.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Kersten, Leis, Kemper, Neumann, Pavlo, Boncz, "Everything You Always Wanted to Know About Compiled and Vectorized Queries But Were Afraid to Ask" | VLDB 2018 | [Arrow PG Integration](@/notes/database/arrow_postgresql_integration.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md), [Join Algorithms](@/notes/database/join_algorithms.md), [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Leis, Kemper, Neumann, "The Adaptive Radix Tree: ARTful Indexing for Main-Memory Databases" | ICDE 2013 | [Database Systems](@/notes/database/database_systems.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md), [Data Structures](@/notes/programming/data_structures.md) |
| Leis, Gubichev, Mirber, Olteanu, Kemper, Neumann, "How Good Are Query Optimizers, Really?" | VLDB 2015 | [Join Algorithms](@/notes/database/join_algorithms.md), [Database Statistics](@/notes/database/database_statistics.md) |
| Mohan, Haderle, Lindsay, Pirahesh, Schwarz, "ARIES: A Transaction Recovery Method Supporting Fine-Granularity Locking" | ACM TODS, 1992 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Muhlbauer, Rodiger, Kemper, Neumann, "Fast Serializable Multi-Version Concurrency Control for Main-Memory Database Systems" | SIGMOD 2015 | [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Neumann, "Efficiently Compiling Efficient Query Plans for Modern Hardware" | VLDB 2011 | [Arrow Format](@/notes/database/arrow_format.md), [Database Systems](@/notes/database/database_systems.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Neumann, Freitag, "Umbra: A Disk-Based System with In-Memory Performance" | CIDR 2020 | [Database Systems](@/notes/database/database_systems.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Selinger et al., "Access Path Selection in a Relational Database Management System" | SIGMOD 1979 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Stonebraker, "The Design of POSTGRES Storage System" | 1987 | [WAL Incremental Conversion](@/notes/database/wal_incremental_conversion.md) |

## Join Algorithms

| Paper | Venue | Cited In |
|---|---|---|
| Albutiu, Kemper, Neumann, "Massively Parallel Sort-Merge Joins in Main Memory Multi-Core Database Systems" | VLDB 2012 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Atserias, Grohe, Marx, "Size Bounds and Query Plans for Relational Joins" (AGM bound) | FOCS 2008 / SICOMP 2013 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Avnur, Hellerstein, "Eddies: Continuously Adaptive Query Processing" | SIGMOD 2000 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Balkesen, Alonso, Teubner, Ozsu, "Multi-Core, Main-Memory Joins: Sort vs. Hash Revisited" | VLDB 2013 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Bandle, Giceva, Neumann, "To Partition, or Not to Partition, That Is the Join Question" | SIGMOD 2021 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Birler, Kemper, Neumann, "Robust Join Processing with Diamond Hardened Joins" | VLDB 2024 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Birler, Schmidt, Fent, Neumann, "Simple, Efficient, and Robust Hash Tables for Join Processing" | DaMoN 2024 (Best Paper) | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Blanas, Li, Hellerstein, Patel, "Design and Evaluation of Main Memory Hash Join Algorithms for Multi-Core CPUs" | SIGMOD 2011 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Blanas, Patel, Ercegovac, Rao, Shekita, Tian, "A Comparison of Join Algorithms for Log Processing in MapReduce" | SIGMOD 2010 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Boncz et al., "MonetDB/X100: Hyper-Pipelining Query Execution" | CIDR 2005 | [Arrow Format](@/notes/database/arrow_format.md), [Arrow PG Integration](@/notes/database/arrow_postgresql_integration.md) |
| DeWitt, Gerber, "Multiprocessor Hash-Based Join Algorithms" (Hybrid hash join) | VLDB 1985 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| DeWitt, Katz, Olken, Shapiro, Stonebraker, Wood, "Implementation Techniques for Main Memory Database Systems" | SIGMOD 1984 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Gross, ten Wolde, Boncz, "Adaptive Factorization Using Linear-Chained Hash Tables" | CIDR 2025 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Khayyat et al., "Lightning Fast and Space Efficient Inequality Joins" (IEJoin) | VLDB 2015 (pub. 2016) | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Kim, Sedlar, Chhugani et al., "Sort vs. Hash Revisited: Fast Join Implementation on Modern Multi-Core CPUs" | VLDB 2009 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Kitsuregawa, Tanaka, Moto-oka, "Application of Hash to Database Machine and Its Architecture" (Grace hash join) | New Generation Computing, 1983 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Kuiper, Gross, Boncz, Muhleisen, "Saving Private Hash Join" | VLDB 2025 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Leis, Boncz, Kemper, Neumann, "Morsel-Driven Parallelism: A NUMA-Aware Query Evaluation Framework" | SIGMOD 2014 | [Database Systems](@/notes/database/database_systems.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md), [Join Algorithms](@/notes/database/join_algorithms.md) |
| Manegold, Boncz, Kersten, "Optimizing Main-Memory Join on Modern Hardware" | TKDE 2002 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Moerkotte, Neumann, "Analysis of Two Existing and One New Dynamic Programming Algorithm" (DPccp) | VLDB 2006 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Moerkotte, Neumann, "Dynamic Programming Strikes Back" (DPhyp) | SIGMOD 2008 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Ngo, Porat, Re, Rudra, "Worst-Case Optimal Join Algorithms" | PODS 2012 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Ngo, Re, Rudra, "Skew Strikes Back: New Developments in the Theory of Join Algorithms" | SIGMOD Record 2013 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Okcan, Riedewald, "Processing Theta-Joins using MapReduce" | SIGMOD 2011 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Polychroniou, Raghavan, Ross, "Rethinking SIMD Vectorization for In-Memory Databases" | SIGMOD 2015 | [Join Algorithms](@/notes/database/join_algorithms.md), [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md), [Data Structures](@/notes/programming/data_structures.md) |
| Qiao, Huang, Zhang, "Data Chunk Compaction in Vectorized Execution" | SIGMOD 2025 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Raman, Hellerstein, "State Modules (SteMs) for Adaptive Query Processing" | VLDB 2003 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Schuh, Chen, Dittrich, "An Experimental Comparison of Thirteen Relational Equi-Joins in Main Memory" | SIGMOD 2016 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Shapiro, "Join Processing in Database Systems with Large Main Memories" | ACM Computing Surveys, 1986 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Shatdal, Kant, Naughton, "Cache Conscious Algorithms for Relational Query Processing" | VLDB 1994 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Urhan, Franklin, "XJoin: A Reactively-Scheduled Pipelined Join Operator" | IEEE Data Engineering Bulletin, 2000 | [Join Algorithms](@/notes/database/join_algorithms.md) |
| Veldhuizen, "Leapfrog Triejoin: A Simple, Worst-Case Optimal Join Algorithm" | ICDT 2014 | [Join Algorithms](@/notes/database/join_algorithms.md) |

## Buffer Management & Storage Engines

| Paper | Venue | Cited In |
|---|---|---|
| Chang et al., "Resource-Adaptive Query Execution with Paged Memory Management" (LIPAH) | CIDR 2025 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| Johnson et al., "Shore-MT: A Scalable Storage Manager for the Multicore Era" | EDBT 2009 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| Leis, Haubenschild, Neumann, "LeanStore: In-Memory Data Management Beyond Main Memory" | ICDE 2018 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md), [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) |
| Leis et al., "Optimistic Lock Coupling" | IEEE Data Eng. Bull. 2019 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| Leis et al., "Virtual-Memory Assisted Buffer Management" (vmcache) | SIGMOD 2023 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| Vohringer, Leis, "Write-Aware Timestamp Tracking" (WATT eviction) | VLDB 2023 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |
| Zinsmeister, Nguyen, Leis, Neumann, "Predictive Translation: High-Performance Buffer Management Without the Trade-Offs" | SIGMOD 2026 | [Buffer Management](@/notes/database/buffer_management_predictive_translation.md) |

## LSM Trees & Write-Optimized Storage

| Paper | Venue | Cited In |
|---|---|---|
| Balmau, Didona et al., "SILK: Preventing Latency Spikes in Log-Structured Merge Key-Value Stores" | USENIX ATC 2019 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Bjorling et al., "ZNS: Avoiding the Block Interface Tax for Flash-based SSDs" | USENIX ATC 2021 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Chang et al., "Bigtable: A Distributed Storage System for Structured Data" | OSDI 2006 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Dai et al., "Bourbon: Learned Index Structures in Storage Engines" | SOSP 2020 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Dayan, Idreos, "Dostoevsky: Better Space-Time Trade-Offs for LSM-Tree Based Key-Value Stores" | SIGMOD 2018 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Dayan et al., "Monkey: Optimal Navigable Key-Value Store" | SIGMOD 2017 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Dillinger, Walzer, "Ribbon Filter: Practically Smaller Than Bloom and Xor" | 2021 | [LSM Trees](@/notes/database/lsm_trees.md), [Data Structures](@/notes/programming/data_structures.md) |
| Dong et al., "RocksDB: Evolution of Development Priorities in a Key-Value Store" | TODS 2021 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Kaiyrakhmet et al., "SLM-DB: Single-Level Key-Value Store with Persistent Memory" | FAST 2019 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Lu, Pillai et al., "WiscKey: Separating Keys from Values in SSD-Conscious Storage" | FAST 2016 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md), [LSM Trees](@/notes/database/lsm_trees.md) |
| Luo, Carey, "LSM-based Storage Techniques: A Survey" | VLDB Journal 2020 | [LSM Trees](@/notes/database/lsm_trees.md), [Data Structures](@/notes/programming/data_structures.md) |
| O'Neil, Cheng, Gawlick, O'Neil, "The Log-Structured Merge-Tree (LSM-Tree)" | Acta Informatica, 1996 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md), [LSM Trees](@/notes/database/lsm_trees.md), [MongoDB & WiredTiger](@/notes/database/mongodb_wiredtiger_internals.md) |
| Yao et al., "MatrixKV: Reducing Write Stalls and Write Amplification in LSM-tree Based KV Stores" | USENIX ATC 2020 | [LSM Trees](@/notes/database/lsm_trees.md) |
| Zhong et al., "REMIX: Efficient Range Query for LSM-trees" | FAST 2021 | [LSM Trees](@/notes/database/lsm_trees.md) |

## Disaggregated Storage & Cloud Databases

| Paper | Venue | Cited In |
|---|---|---|
| Antonopoulos et al., "Socrates: The New SQL Server in the Cloud" | SIGMOD 2019 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Armbrust et al., "Delta Lake: High-Performance ACID Table Storage over Cloud Object Stores" | VLDB 2020 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Bacon et al., "Spanner: Becoming a SQL System" | SIGMOD 2017 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md), [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Cao et al., "PolarFS: An Ultra-low Latency and Failure Resilient Distributed File System" | VLDB 2018 | [LSM Trees](@/notes/database/lsm_trees.md), [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Corbett et al., "Spanner: Google's Globally-Distributed Database" | OSDI 2012 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md), [Disaggregated Storage](@/notes/database/disaggregated_storage.md), [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Dageville et al., "The Snowflake Elastic Data Warehouse" | SIGMOD 2016 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Depoutovitch et al., "Taurus Database: How to Be Fast, Available, and Frugal in the Cloud" | SIGMOD 2020 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Dong, Zhang et al., "Cloud-Native Databases: A Survey" | IEEE TKDE 2024 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| GaussDB team, "GaussDB: A Cloud-Native Multi-Primary Database with Compute-Memory-Storage Disaggregation" | VLDB 2024 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Ghemawat, Gobioff, Leung, "The Google File System" | SOSP 2003 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Pang, Wang, "Understanding the Performance Implications of the Design Principles in Storage-Disaggregated Databases" (OpenAurora) | SIGMOD 2024 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| PolarDB-MP team, "PolarDB-MP: A Multi-Primary Cloud-Native Database via Disaggregated Shared Memory" | SIGMOD Companion 2024 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| PolarDB team, "From Scale-Up to Scale-Out: PolarDB's Journey to Achieving 2 Billion tpmC" | VLDB 2025 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Verbitski et al., "Amazon Aurora: Design Considerations for High Throughput Cloud-Native Relational Databases" | SIGMOD 2017 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md), [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Verbitski et al., "Amazon Aurora: On Avoiding Distributed Consensus for I/Os, Commits, and Membership Changes" | SIGMOD 2018 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Vuppalapati et al., "Building An Elastic Query Engine on Disaggregated Storage" | NSDI 2020 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Wang, Zhang, "Disaggregated Database Systems" (Tutorial) | SIGMOD 2023 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Wang et al., "Cache Coherence Over Disaggregated Memory" (SELCC) | VLDB 2025 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Weisgut et al., "CXL Memory Performance for In-Memory Data Processing" | VLDB 2025 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |
| Yu et al., "Disaggregation: A New Architecture for Cloud Databases" | VLDB 2025 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md) |

## Distributed Consensus & Replication

| Paper | Venue | Cited In |
|---|---|---|
| Abadi, "Consistency Tradeoffs in Modern Distributed Database System Design" | Computer (IEEE), 2012 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Ailijiang, Charapko, Demirbas, Mitra, "WPaxos: Wide Area Network Flexible Consensus" | IEEE TPDS 2020 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Baudet et al., "State Machine Replication in the Libra Blockchain" | The Libra Association, 2019 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Buchman, Kwon, Milosevic, "The Latest Gossip on BFT Consensus" | arXiv 2018 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Castro, Liskov, "Practical Byzantine Fault Tolerance" | OSDI 1999 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Castro, Liskov, "Practical Byzantine Fault Tolerance and Proactive Recovery" | TOCS 2002 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Chandra, Griesemer, Redstone, "Paxos Made Live: An Engineering Perspective" | PODC 2007 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Chandra, Toueg, "Unreliable Failure Detectors for Reliable Distributed Systems" | JACM 1996 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Danezis, Kokoris-Kogias, Sonnino, Spiegelman, "Narwhal and Tusk: A DAG-based Mempool and Efficient BFT Consensus" | EuroSys 2022 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Dwork, Lynch, Stockmeyer, "Consensus in the Presence of Partial Synchrony" | JACM 1988 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Fischer, Lynch, Paterson, "Impossibility of Distributed Consensus with One Faulty Process" (FLP) | JACM 1985 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md), [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |
| Gafni, Lamport, "Disk Paxos" | Distributed Computing, 2003 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Gilbert, Lynch, "Brewer's Conjecture and the Feasibility of Consistent, Available, Partition-Tolerant Web Services" | SIGACT News 2002 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Howard, Malkhi, Spiegelman, "Flexible Paxos: Quorum Intersection Revisited" | OPODIS 2016 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Howard, Mortier, "Paxos vs Raft: Have We Reached Consensus on Distributed Consensus?" | HotCloud 2020 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Huang et al., "TiDB: A Raft-based HTAP Database" | VLDB 2020 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md), [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Hunt, Konar, Junqueira, Reed, "ZooKeeper: Wait-free Coordination for Internet-scale Systems" | USENIX ATC 2010 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Junqueira, Reed, Serafini, "Zab: High-performance Broadcast for Primary-backup Systems" | DSN 2011 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Kreps, Narkhede, Rao, "Kafka: A Distributed Messaging System for Log Processing" | NetDB Workshop, 2011 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md), [Kafka Internals](@/notes/distributed/kafka_internals.md) |
| Lamport, "Fast Paxos" | Distributed Computing, 2006 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Lamport, "Generalized Consensus and Paxos" | MSR Technical Report, 2005 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Lamport, "Paxos Made Simple" | SIGACT News, 2001 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Lamport, "The Part-Time Parliament" | TOCS 1998 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Lamport, "Time, Clocks, and the Ordering of Events in a Distributed System" | CACM 1978 | [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |
| Lamport, Massa, "Cheap Paxos" | DSN 2004 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Liskov, Cowling, "Viewstamped Replication Revisited" | MIT-CSAIL-TR-2012-021, 2012 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Mao, Junqueira, Marzullo, "Mencius: Building Efficient Replicated State Machines for WANs" | OSDI 2008 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Moraru, Andersen, Kaminsky, "There Is More Consensus in Egalitarian Parliaments" (EPaxos) | SOSP 2013 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Oki, Liskov, "Viewstamped Replication: A New Primary Copy Method" | PODC 1988 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Ongaro, Ousterhout, "In Search of an Understandable Consensus Algorithm" (Raft) | USENIX ATC 2014 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Santos, Schiper, "Optimizing Paxos with Batching and Pipelining" | Theoretical Computer Science, 2012 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Shapiro, Preguica, Baquero, Zawirski, "Conflict-free Replicated Data Types" | SSS 2011 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md), [Data Structures](@/notes/programming/data_structures.md) |
| Taft et al., "CockroachDB: The Resilient Geo-Distributed SQL Database" | SIGMOD 2020 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md), [CockroachDB Optimizer Rules](@/notes/database/cockroachdb_optimizer_rules.md) |
| Van Renesse, Altinbuken, "Paxos Made Moderately Complex" | ACM Computing Surveys, 2015 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Yin, Malkhi, Reiter, Gueta, Abraham, "HotStuff: BFT Consensus with Linearity and Responsiveness" | PODC 2019 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md) |
| Zhou et al., "FoundationDB: A Distributed Unbundled Transactional Key Value Store" | SIGMOD 2021 | [Disaggregated Storage](@/notes/database/disaggregated_storage.md), [Distributed Consensus](@/notes/distributed/distributed_consensus.md), [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |

## Deterministic Simulation Testing

| Paper | Venue | Cited In |
|---|---|---|
| Alpern, Schneider, "Defining Liveness" | Information Processing Letters, 1985 | [Distributed Consensus](@/notes/distributed/distributed_consensus.md), [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |
| Chen, Groce, Zhang, Wong, Fern, Eide, Regehr, "Taming Compiler Fuzzers" (Swarm testing) | PLDI 2013 | [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |
| Newcombe, Rath, Zhang, Metz, Kelley, "How Amazon Web Services Uses Formal Methods" | CACM 2015 | [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) |

## Storage Reliability & Crash Consistency

| Paper | Venue | Cited In |
|---|---|---|
| Alagappan et al., "Protocol-Aware Recovery for Consensus-Based Storage" | FAST 2018 (Best Paper) | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| He, Kannan et al., "The Unwritten Contract of Solid State Drives" | EuroSys 2017 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Michael, "Hazard Pointers: Safe Memory Reclamation for Lock-Free Objects" | IEEE TPDS 2004 | [MongoDB & WiredTiger](@/notes/database/mongodb_wiredtiger_internals.md), [Data Structures](@/notes/programming/data_structures.md) |
| Pillai et al., "All File Systems Are Not Created Equal: On the Complexity of Crafting Crash-Consistent Applications" | OSDI 2014 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Rebello et al., "Can Applications Recover from fsync Failures?" | USENIX ATC 2020 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |
| Schroeder, Merchant et al., "Flash Reliability in Production: The Expected and the Unexpected" | FAST 2016 | [WAL & Torn Pages](@/notes/database/wal_torn_pages.md) |

## File Systems

| Paper | Venue | Cited In |
|---|---|---|
| Boyd-Wickizer et al., "An Analysis of Linux Scalability to Many Cores" | OSDI 2010 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Ganger, Patt, "Soft Updates: A Solution to the Metadata Update Problem in File Systems" | 1994 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Harter et al., "All File Systems Are Not Created Equal" | 2011 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Kadekodi et al., "SplitFS: Reducing Software Overhead in File Systems for Persistent Memory" | SOSP 2019 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Lee et al., "F2FS: A New File System for Flash Storage" | 2015 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| McKusick et al., "A Fast File System for UNIX" | 1984 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| McVoy, Kleiman, "Extent-like Performance from a UNIX File System" | 1991 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Prabhakaran et al., "Analysis and Evolution of Journaling File Systems" | 2005 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Rosenblum, Ousterhout, "The Design and Implementation of a Log-Structured File System" | 1992 | [Filesystem Design](@/notes/os/filesystem_design.md) |
| Xu, Swanson, "NOVA: A Log-structured File System for Hybrid Volatile/Non-volatile Main Memories" | 2016 | [Filesystem Design](@/notes/os/filesystem_design.md) |

## Virtualization & Device Passthrough

| Paper | Venue | Cited In |
|---|---|---|
| Amit et al., "vIOMMU: Efficient IOMMU Emulation" | USENIX ATC 2011 | [VFIO Internals](@/notes/os/vfio_internals.md) |
| Ben-Yehuda et al., "The Turtles Project: Design and Implementation of Nested Virtualization" | OSDI 2010 | [VFIO Internals](@/notes/os/vfio_internals.md) |
| Bugnion, Nieh, Tsafrir, "Hardware and Software Support for Virtualization" | Morgan & Claypool, 2017 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Dall, Nieh, "KVM/ARM: The Design and Implementation of the Linux ARM Hypervisor" | ASPLOS 2014 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Markuze et al., "True IOMMU Protection from DMA Attacks: When Copy is Faster than Zero Copy" | ASPLOS 2016 | [VFIO Internals](@/notes/os/vfio_internals.md) |
| Neugebauer et al., "Understanding PCIe Performance for End Host Networking" | SIGCOMM 2018 | [VFIO Internals](@/notes/os/vfio_internals.md) |
| Tian et al., "A Full GPU Virtualization Solution with Mediated Pass-Through" (GVT-g) | USENIX ATC 2014 | [VFIO Internals](@/notes/os/vfio_internals.md) |

## GPU, TPU & Accelerator Architecture

| Paper | Venue | Cited In |
|---|---|---|
| Abts et al., "Think Fast: A Tensor Streaming Processor for Accelerating Deep Learning Workloads" (Groq TSP) | ISCA 2020 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Ajayi et al., "OpenROAD: Toward a Self-Driving, Open-Source Digital Layout Implementation Tool Chain" | GOMAC 2019 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Chen et al., "Eyeriss: An Energy-Efficient Reconfigurable Accelerator for Deep CNNs" | JSSC 2017 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Chen et al., "Eyeriss v2: A Flexible Accelerator for Emerging Deep Neural Networks" | JETCAS 2019 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Dao et al., "FlashAttention: Fast and Memory-Efficient Exact Attention" | NeurIPS 2022 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Dao, "FlashAttention-2: Faster Attention with Better Parallelism and Work Partitioning" | ICLR 2024 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Darvish Rouhani et al., "Microscaling Data Formats for Deep Learning" (OCP MX spec) | arXiv 2023 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Genc et al., "Gemmini: Enabling Systematic Deep-Learning Architecture Evaluation via Full-Stack Integration" | DAC 2021 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Gustafson, Yonemoto, "Beating Floating Point at its Own Game: Posit Arithmetic" | Supercomputing Frontiers, 2017 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Jia et al., "Dissecting the NVIDIA Volta GPU Architecture via Microbenchmarking" | arXiv 2018 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Jia et al., "Dissecting the NVidia Turing T4 GPU via Microbenchmarking" | arXiv 2019 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Jouppi et al., "In-Datacenter Performance Analysis of a Tensor Processing Unit" (TPU v1) | ISCA 2017 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Jouppi et al., "A Domain-Specific Supercomputer for Training Deep Neural Networks" (TPU v2/v3) | Comm. ACM 2020 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Jouppi et al., "TPU v4: An Optically Reconfigurable Supercomputer for Machine Learning" | ISCA 2023 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Kung, "Why Systolic Architectures?" | IEEE Computer, 1982 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Kwon et al., "Efficient Memory Management for Large Language Model Serving with PagedAttention" (vLLM) | SOSP 2023 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Kwon et al., "MAERI: Enabling Flexible Dataflow Mapping over DNN Accelerators" | ASPLOS 2018 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Leviathan et al., "Fast Inference from Transformers via Speculative Decoding" | ICML 2023 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Lindholm et al., "NVIDIA Tesla: A Unified Graphics and Computing Architecture" | IEEE Micro, 2008 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Micikevicius et al., "Mixed Precision Training" | ICLR 2018 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Naffziger et al., "AMD Chiplet Architecture for High-Performance Server and Desktop Products" | ISSCC 2020 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Norrie et al., "The Design Process for Google's Training Chips: TPUv2 and TPUv3" | IEEE Micro, 2021 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Noune et al., "8-bit Numerical Formats for Deep Neural Networks" | NeurIPS 2022 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Parashar et al., "Timeloop: A Systematic Approach to DNN Accelerator Evaluation" | ISPASS 2019 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Pope et al., "Efficiently Scaling Transformer Inference" | MLSys 2023 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Rogers et al., "Cache-Conscious Wavefront Scheduling" (CCWS) | MICRO 2012 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Shazeer, "Fast Transformer Decoding: One Write-Head is All You Need" (Multi-Query Attention) | arXiv 2019 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Sze et al., "Efficient Processing of Deep Neural Networks: A Tutorial and Survey" | Proceedings of the IEEE, 2017 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |

## ISA, Memory Models & Microarchitecture

| Paper | Venue | Cited In |
|---|---|---|
| Alglave et al., "Frightening Small Children and Disconcerting Grown-ups: Concurrency in the Linux Kernel" | ASPLOS 2018 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Herlihy, Shavit, "The Art of Multiprocessor Programming" | 2008 (revised 2020) | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Kocher et al., "Spectre Attacks: Exploiting Speculative Execution" | S&P 2019 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Lahav et al., "Repairing Sequential Consistency in C/C++11" | PLDI 2017 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Lipp et al., "Meltdown: Reading Kernel Memory from User Space" | USENIX Security 2018 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Luo, Wen, "GCoM: A Detailed GPU Core Model for Accurate Analytical Modeling of Modern GPUs" | ISPASS 2019 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Maranget, Sarkar, Sewell, "A Tutorial Introduction to the ARM and POWER Relaxed Memory Models" | 2012 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Mellor-Crummey, Scott, "Algorithms for Scalable Synchronization on Shared-Memory Multiprocessors" (MCS lock) | TOCS 1991 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Michael, Scott, "Simple, Fast, and Practical Non-Blocking and Blocking Concurrent Queue Algorithms" | PODC 1996 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Narasiman et al., "Improving GPU Performance via Large Warps and Two-Level Warp Scheduling" | MICRO 2011 | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |
| Sewell et al., "x86-TSO: A Rigorous and Usable Programmer's Model for x86 Multiprocessors" | CACM 2010 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Stephens et al., "The Scalable Vector Extension for AArch64" | IEEE Micro, 2017 | [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |

## Data Structures & Algorithms

| Paper | Venue | Cited In |
|---|---|---|
| Almeida et al., "Delta State Replicated Data Types" | J. Parallel & Distributed Computing, 2018 | [Data Structures](@/notes/programming/data_structures.md) |
| Bender, Hu, "Packed Memory Arrays" | FOCS 2007 | [Data Structures](@/notes/programming/data_structures.md) |
| Cormode, Muthukrishnan, "An Improved Data Stream Summary: The Count-Min Sketch" | 2005 | [Data Structures](@/notes/programming/data_structures.md) |
| Ding et al., "ALEX: An Updatable Adaptive Learned Index" | SIGMOD 2020 | [Data Structures](@/notes/programming/data_structures.md) |
| Esposito et al., "RecSplit: Minimal Perfect Hashing via Recursive Splitting" | ALENEX 2020 | [Data Structures](@/notes/programming/data_structures.md) |
| Fan et al., "Cuckoo Filter: Practically Better Than Bloom" | CoNEXT 2014 | [Data Structures](@/notes/programming/data_structures.md) |
| Ferragina, Manzini, "Opportunistic Data Structures with Applications" (FM-Index) | FOCS 2000 | [Data Structures](@/notes/programming/data_structures.md) |
| Ferragina, Vinciguerra, "PGM-Index: A Fully-Dynamic Compressed Learned Index" | VLDB 2020 | [Data Structures](@/notes/programming/data_structures.md) |
| Hendler et al., "Flat Combining and the Synchronization-Parallelism Tradeoff" | SPAA 2010 | [Data Structures](@/notes/programming/data_structures.md) |
| Karnin, Lang, Liberty, "Optimal Streaming Quantile Sketches" (KLL) | VLDB 2016 | [Data Structures](@/notes/programming/data_structures.md) |
| Kraska et al., "The Case for Learned Index Structures" | SIGMOD 2018 | [LSM Trees](@/notes/database/lsm_trees.md), [Data Structures](@/notes/programming/data_structures.md) |
| Kulkarni et al., "Logical Physical Clocks and Consistent Snapshots" (HLC) | OPODIS 2014 | [Data Structures](@/notes/programming/data_structures.md) |
| Lemire et al., "Roaring Bitmaps: Implementation of an Optimized Software Library" | Software: Practice & Experience, 2018 | [Data Structures](@/notes/programming/data_structures.md) |
| Levandoski et al., "The Bw-Tree: A B-tree for New Hardware Platforms" | ICDE 2013 | [Data Structures](@/notes/programming/data_structures.md) |
| Malkov, Yashunin, "Efficient and Robust Approximate Nearest Neighbor Search Using HNSW Graphs" | TPAMI 2020 | [Data Structures](@/notes/programming/data_structures.md) |
| Mao et al., "Cache Craftiness for Fast Multicore Key-Value Storage" (Masstree) | EuroSys 2012 | [Data Structures](@/notes/programming/data_structures.md) |
| Masson et al., "DDSketch: A Fast and Fully-Mergeable Quantile Sketch" | VLDB 2019 | [Data Structures](@/notes/programming/data_structures.md) |
| Megiddo, Modha, "ARC: A Self-Tuning, Low Overhead Replacement Cache" | FAST 2003 | [Data Structures](@/notes/programming/data_structures.md) |
| Prokopec et al., "Concurrent Tries with Efficient Non-Blocking Snapshots" (CTrie) | PPoPP 2012 | [Data Structures](@/notes/programming/data_structures.md) |
| Sleator, Tarjan, "Self-Adjusting Binary Search Trees" (Splay) | JACM 1985 | [Data Structures](@/notes/programming/data_structures.md) |
| Stoica et al., "Chord: A Scalable Peer-to-peer Lookup Protocol" | IEEE/ACM TON 2003 | [Data Structures](@/notes/programming/data_structures.md) |
| Subramanya et al., "DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node" | NeurIPS 2019 | [Data Structures](@/notes/programming/data_structures.md) |
| Wongkham et al., "Are Updatable Learned Indexes Ready?" | VLDB 2022 | [Data Structures](@/notes/programming/data_structures.md) |

## Superscalar & CPU Microarchitecture

| Paper | Venue | Cited In |
|---|---|---|
| AMD, "Software Optimization Guide for AMD EPYC 9004 Series Processors (Zen 4)" | AMD Publication 57647, 2024 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Bera, Subramanian, Singh, Mutlu, "Pythia: A Customizable Hardware Prefetching Framework Using Online Reinforcement Learning" | MICRO 2021 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Binkert et al., "The gem5 Simulator" | ACM SIGARCH Computer Architecture News 39(2), 2011 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Celio, Patterson, Asanovic, "The Berkeley Out-of-Order Machine (BOOM): An Industry-Competitive, Synthesizable, Parameterized RISC-V Processor" | UCB/EECS-2017-2, 2017 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Chrysos, Emer, "Memory Dependence Prediction Using Store Sets" | ISCA 1998 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Esmaeilzadeh, Blem, St.Amant, Sankaralingam, Burger, "Dark Silicon and the End of Multicore Scaling" | ISCA 2011 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Ferdman et al., "Clearing the Clouds: A Study of Emerging Scale-Out Workloads on Modern Hardware" | ASPLOS 2012 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Fog, A., "Microarchitecture of Intel, AMD and VIA CPUs" | agner.org/optimize (continuously updated) | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Gabbay, Mendelson, "Speculative Execution Based on Value Prediction" | Technion Tech Report CS0974, 1997 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Gochman et al., "The Intel Pentium M Processor: Microarchitecture and Performance" | Intel Technology Journal 7(2), 2003 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Intel, "Intel Core Ultra (Series 2) — Microarchitecture Overview (Lion Cove)" | Intel Architecture Disclosure, 2024 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Kessler, "The Alpha 21264 Microprocessor" | IEEE Micro 19(2), 1999 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Kim, Jeong, Chang, Suh, "Revisiting Virtual Memory Translation for Hardware Prefetchers" | ISCA 2019 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Lam, Wilson, "Limits of Control Flow on Parallelism" | ISCA 1992 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Nesbit, Smith, "Data Cache Prefetching Using a Global History Buffer" | HPCA 2004 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Seznec, "A New Case for the TAGE Branch Predictor" (TAGE-SC-L) | MICRO 2011 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Seznec, "TAGE-SC-L Branch Predictors Again" (ITTAGE) | 5th JILP Workshop on Computer Architecture Competitions (JWAC-5), 2014 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Seznec, Michaud, "A Case for (Partially) Tagged GEometric History Length Branch Prediction" | JILP 8, 2006 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Smith, Pleszkun, "Implementing Precise Interrupts in Pipelined Processors" | IEEE Transactions on Computers 37(5), 1988 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Sohi, Vajapeyam, "Tradeoffs in Instruction Format Design for Horizontal Architectures" | ASPLOS 1987 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Srinath, Mutlu, Kim, Patt, "Feedback Directed Prefetching: Improving the Performance and Bandwidth-Efficiency of Hardware Prefetchers" | HPCA 2007 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Tendler, Dodson, Fields, Le, Sinharoy, "POWER4 System Microarchitecture" | IBM Journal of Research and Development 46(1), 2002 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Tomasulo, "An Efficient Algorithm for Exploiting Multiple Arithmetic Units" | IBM Journal of Research and Development 11(1), 1967 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Xu et al., "Towards Developing High Performance RISC-V Processors Using Agile Methodology" (XiangShan) | MICRO 2022 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |
| Yeh, Patt, "Alternative Implementations of Two-Level Adaptive Branch Prediction" | ISCA 1992 | [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) |

## Text Search & Information Retrieval

| Paper | Venue | Cited In |
|---|---|---|
| Broder, Carmel, Herscovici, Soffer, Zien, "Efficient Query Evaluation Using a Two-Level Retrieval Process" (WAND) | CIKM 2003 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Burkhard, Keller, "Some Approaches to Best-Match File Searching" (BK-trees) | CACM 1973 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Chambi, Lemire, Kaser, Godin, "Better Bitmap Performance with Roaring Bitmaps" | Software: Practice & Experience 2016 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Ding, Suel, "Faster Top-k Document Retrieval Using Block-Max Indexes" (BMW) | SIGIR 2011 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Ferragina, Manzini, "Opportunistic Data Structures with Applications" (FM-index) | FOCS 2000 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Lemire, Boytsov, "Decoding Billions of Integers per Second Through Vectorization" (SIMD-BP128) | Software: Practice & Experience 2015 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Ottaviano, Venturini, "Partitioned Elias-Fano Indexes" | SIGIR 2014 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Robertson, Walker, Jones, Hancock-Beaulieu, Gatford, "Okapi at TREC-3" (BM25) | TREC 1994 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Robertson, Zaragoza, "The Probabilistic Relevance Framework: BM25 and Beyond" | FnTIR 2009 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Schulz, Mihov, "Fast String Correction with Levenshtein-Automata" | IJDAR 2002 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Turtle, Flood, "Query Evaluation: Strategies and Optimizations" (MaxScore) | IPM 1995 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |

## Approximate Nearest Neighbor Search

| Paper | Venue | Cited In |
|---|---|---|
| Douze, Guzhva, Deng, Johnson et al., "The FAISS Library" | arXiv 2024 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Fu, Cai, Du, "Fast Approximate Nearest Neighbor Search With the Navigating Spreading-out Graph" (NSG) | VLDB 2019 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Gao, Long, "RaBitQ: Quantizing High-Dimensional Vectors with a Theoretical Error Bound for ANN Search" | SIGMOD 2024 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Guo, Sun, Lindgren, Geng, Simcha, Chern, Kumar, "Accelerating Large-Scale Inference with Anisotropic Vector Quantization" (ScaNN) | ICML 2020 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Indyk, Motwani, "Approximate Nearest Neighbors: Towards Removing the Curse of Dimensionality" | STOC 1998 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Jégou, Douze, Schmid, "Product Quantization for Nearest Neighbor Search" | IEEE TPAMI 2011 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Johnson, Douze, Jégou, "Billion-scale Similarity Search with GPUs" (FAISS) | arXiv 2017 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Malkov, Yashunin, "Efficient and Robust Approximate Nearest Neighbor Search Using Hierarchical Navigable Small World Graphs" (HNSW) | IEEE TPAMI 2018 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Ootomo, Ozaki, Itasaka, Yokota, Tanaka, "CAGRA: Highly Parallel Graph Construction and ANN Search for GPUs" | arXiv 2308.15136 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Patel, Kraft, Guestrin, Zaharia, "ACORN: Performant and Predicate-Agnostic Search Over Vector Embeddings and Structured Data" | SIGMOD 2024 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Singh, Jaiswal et al., "FreshDiskANN: A Fast and Accurate Graph-Based ANN Index for Streaming Similarity Search" | arXiv 2021 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Subramanya, Devvrit, Kakde, Krishnaswamy, Shrivastava, "DiskANN: Fast Accurate Billion-point Nearest Neighbor Search on a Single Node" | NeurIPS 2019 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |

## Neural & Learned Retrieval

| Paper | Venue | Cited In |
|---|---|---|
| Cormack, Clarke, Buettcher, "Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods" (RRF) | SIGIR 2009 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Formal, Piwowarski, Clinchant, "SPLADE: Sparse Lexical and Expansion Model for First Stage Retrieval" | SIGIR 2021 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Karpukhin, Oğuz, Min, Lewis et al., "Dense Passage Retrieval for Open-Domain Question Answering" (DPR) | EMNLP 2020 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Khattab, Zaharia, "ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT" | SIGIR 2020 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Kusupati, Bhatt, Rege et al., "Matryoshka Representation Learning" (MRL) | NeurIPS 2022 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Muennighoff, Tazi, Magne, Reimers, "MTEB: Massive Text Embedding Benchmark" | EACL 2023 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Radford, Kim, Hallacy et al., "Learning Transferable Visual Models From Natural Language Supervision" (CLIP) | ICML 2021 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Santhanam, Khattab, Saad-Falcon, Potts, Zaharia, "ColBERTv2: Effective and Efficient Retrieval via Lightweight Late Interaction" | NAACL 2022 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Santhanam, Khattab, Potts, Zaharia, "PLAID: An Efficient Engine for Late Interaction Retrieval" | CIKM 2022 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Thakur, Reimers, Rücklé, Srivastava, Gurevych, "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of IR Models" | NeurIPS 2021 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |
| Xiong, Xiong, Li et al., "Approximate Nearest Neighbor Negative Contrastive Estimation for Dense Text Retrieval" (ANCE) | ICLR 2021 | [Text & Vector Search](@/notes/programming/text_and_vector_search.md) |

## Statistics, Cardinality Estimation & Query Optimization

| Paper | Venue | Cited In |
|---|---|---|
| Alon, Matias, Szegedy, "The Space Complexity of Approximating the Frequency Moments" | J. Comput. Syst. Sci. 1999 | [Database Statistics](@/notes/database/database_statistics.md) |
| Bruno, Chaudhuri, Gravano, "STHoles: A Multidimensional Workload-Aware Histogram" | SIGMOD 2001 | [Database Statistics](@/notes/database/database_statistics.md) |
| Cormode, Muthukrishnan, "An Improved Data Stream Summary: The Count-Min Sketch and its Applications" | J. Algorithms 2005 | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| Dunning, Ertl, "Computing Extremely Accurate Quantiles Using t-Digests" | arXiv 2019 | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| Estan, Naughton, "End-Biased Samples for Join Cardinality Estimation" | SIGMOD 2006 | [Database Statistics](@/notes/database/database_statistics.md) |
| Flajolet, Martin, "Probabilistic Counting Algorithms for Data Base Applications" | J. Comput. Syst. Sci. 1985 | [Database Statistics](@/notes/database/database_statistics.md) |
| Flajolet, Fusy, Gandouet, Meunier, "HyperLogLog: the Analysis of a Near-Optimal Cardinality Estimation Algorithm" | DMTCS 2007 | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| Haas, Naughton, Seshadri, Stokes, "Sampling-Based Estimation of the Number of Distinct Values of an Attribute" | VLDB 1995 | [Database Statistics](@/notes/database/database_statistics.md) |
| Han, Wu, Wu, Zhu, Pfadler, Qin, Li, Pfadler, "Cardinality Estimation in DBMS: A Comprehensive Benchmark Evaluation" | VLDB 2021 | [Database Statistics](@/notes/database/database_statistics.md) |
| Heule, Nunkesser, Hall, "HyperLogLog in Practice: Algorithmic Engineering of a State of the Art Cardinality Estimation Algorithm" | EDBT 2013 | [Database Statistics](@/notes/database/database_statistics.md) |
| Hilprecht, Schmidt, Kulessa, Molina, Kersting, Binnig, "DeepDB: Learn from Data, not from Queries!" | VLDB 2020 | [Database Statistics](@/notes/database/database_statistics.md) |
| Ioannidis, Kang, "Randomized Algorithms for Optimizing Large Join Queries" | SIGMOD 1990 | [Database Statistics](@/notes/database/database_statistics.md) |
| Ioannidis, Kang, "Optimal Histograms for Limiting Worst-Case Error Propagation in the Size of Join Results" | TODS 1993 | [Database Statistics](@/notes/database/database_statistics.md) |
| Karnin, Lang, Liberty, "Optimal Quantile Approximation in Streams" | FOCS 2016 | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| Kipf, Kipf, Radke, Leis, Boncz, Kemper, "Learned Cardinalities: Estimating Correlated Joins with Deep Learning" | CIDR 2019 | [Database Statistics](@/notes/database/database_statistics.md) |
| Li, Quoc, Ho, Gubichev, Kemper, Abo Khamis, Olteanu, Schleich, "Wander Join: Online Aggregation via Random Walks" | SIGMOD 2016 | [Database Statistics](@/notes/database/database_statistics.md) |
| Marcus, Negi, Mao, Zhang, Alizadeh, Kraska, Papaemmanouil, Tatbul, "Bao: Making Learned Query Optimization Practical" | SIGMOD 2021 | [Database Statistics](@/notes/database/database_statistics.md) |
| Masson, Rim, Lee, "DDSketch: A Fast and Fully-Mergeable Quantile Sketch with Relative-Error Guarantees" | VLDB 2019 | [Database Statistics](@/notes/database/database_statistics.md), [Data Structures](@/notes/programming/data_structures.md) |
| Metwally, Agrawal, El Abbadi, "Efficient Computation of Frequent and Top-k Elements in Data Streams" | ICDT 2005 | [Database Statistics](@/notes/database/database_statistics.md) |
| Misra, Gries, "Finding Repeated Elements" | Science of Computer Programming 1982 | [Database Statistics](@/notes/database/database_statistics.md) |
| Moerkotte, Steinbrunn, Moerkotte, "Analysis of Two Existing and One New Dynamic Programming Algorithm for the Generation of Optimal Bushy Join Trees without Cross Products" (Q-error) | VLDB 2006 | [Database Statistics](@/notes/database/database_statistics.md), [Join Algorithms](@/notes/database/join_algorithms.md) |
| Poosala, Ioannidis, Haas, Shekita, "Improved Histograms for Selectivity Estimation of Range Predicates" | SIGMOD 1996 | [Database Statistics](@/notes/database/database_statistics.md) |
| Vitter, "Random Sampling with a Reservoir" | ACM Trans. Math. Software 1985 | [Database Statistics](@/notes/database/database_statistics.md) |
| Vitter, Wang, Iyer, "Data Cube Approximation and Histograms via Wavelets" | VLDB 1998 | [Database Statistics](@/notes/database/database_statistics.md) |
| Yang, Liang, Kamsetty, Wu, Guestrin, Stoica, Krishnan, Abbeel, "NeuroCard: One Cardinality Estimator for All Tables" | NeurIPS 2020 | [Database Statistics](@/notes/database/database_statistics.md) |

## Datacenter Networking & RDMA

| Paper | Venue | Cited In |
|---|---|---|
| Addanki, Apostolaki, Ghobadi, Schmid, Vanbever, "PowerTCP: Pushing the Performance Limits of Datacenter Networks" | NSDI 2022 | [Interconnects](@/notes/hardware/interconnects.md) |
| Alizadeh, Greenberg, Maltz, Padhye, Patel, Prabhakar, Sengupta, Sridharan, "Data Center TCP (DCTCP)" | SIGCOMM 2010 | [Interconnects](@/notes/hardware/interconnects.md) |
| Bansal, Khan, Goyal et al., "Meta's RoCE Networks: Building, Operating, and Lessons Learned" | SIGCOMM 2023 | [Interconnects](@/notes/hardware/interconnects.md) |
| Dragojević, Narayanan, Hodson, Castro, "FaRM: Fast Remote Memory" | NSDI 2014 | [Interconnects](@/notes/hardware/interconnects.md) |
| Gibson, Hartl, Wlodarczyk, Vahdat, Mogul, Goldberg, Sjödin, Sosa, Yang, Singh, "Aquila: A Unified, Low-Latency Fabric for Datacenter Networks" | NSDI 2022 | [Interconnects](@/notes/hardware/interconnects.md) |
| Guo, Wu, Deng, Liu, Haridas, Liu, Xu, Yu, Xiang, Wang, Yu, Zhang, Zhang, Padhye, Lipshteyn, "RDMA over Commodity Ethernet at Scale" | SIGCOMM 2016 | [Interconnects](@/notes/hardware/interconnects.md) |
| Kumar, Dukkipati, Jouppi, Lam, Madhavan, Mittal, Mittal, Wassel, Wetherall, Wu, Yang, Zats, "Swift: Delay is Simple and Effective for Congestion Control in the Datacenter" | SIGCOMM 2020 | [Interconnects](@/notes/hardware/interconnects.md) |
| Li, Miao, Liu, Zhou, Sridharan, Kumar, Bao, Zhou, Yang, Tewari, "HPCC: High Precision Congestion Control" | SIGCOMM 2019 | [Interconnects](@/notes/hardware/interconnects.md) |
| Mellette, McGuinness, Roy, Forencich, Papen, Snoeren, Porter, "RotorNet: A Scalable, Low-Complexity, Optical Datacenter Network" | SIGCOMM 2017 | [Interconnects](@/notes/hardware/interconnects.md) |
| Mittal, Lam, Dukkipati, Blem, Wassel, Ghobadi, Vahdat, Wang, Wetherall, Zats, "TIMELY: RTT-based Congestion Control for the Datacenter" | SIGCOMM 2015 | [Interconnects](@/notes/hardware/interconnects.md) |
| Mittal, Shpiner, Panda, Zahavi, Krishnamurthy, Ratnasamy, Shenker, "Revisiting Network Support for RDMA" (IRN) | SIGCOMM 2018 | [Interconnects](@/notes/hardware/interconnects.md) |
| Olteanu, Agache, Voinescu, Raiciu, "An Edge-Queued Datagram Service for All Datacenter Traffic" (EQDS) | NSDI 2022 | [Interconnects](@/notes/hardware/interconnects.md) |
| Poutievski, Mashayekhi, Ong, Singhvi, Tariq, Tariq, Vahdat, Wanderer, "Jupiter Evolving: Transforming Google's Datacenter Network via Optical Circuit Switches and Software-Defined Networking" | SIGCOMM 2022 | [Interconnects](@/notes/hardware/interconnects.md) |
| Singh, Ong, Agarwal, Anderson, Armistead, Bannon, Boving, Desai, Felderman, Germano, Kanagala, Provost, Simmons, Tanda, Wanderer, Hölzle, Stuart, Vahdat, "Jupiter Rising: A Decade of Clos Topologies and Centralized Control in Google's Datacenter Network" | SIGCOMM 2015 | [Interconnects](@/notes/hardware/interconnects.md) |
| Stephens, Akella, Swift, "Loom: Flexible and Efficient NIC Packet Scheduling" (Annulus follow-on) | SIGCOMM 2019 | [Interconnects](@/notes/hardware/interconnects.md) |
| Zhu, Eran, Firestone, Guo, Lipshteyn, Liron, Padhye, Raindel, Yahia, Zhang, "Congestion Control for Large-Scale RDMA Deployments" (DCQCN) | SIGCOMM 2015 | [Interconnects](@/notes/hardware/interconnects.md) |

## HPC Fabrics & Optical Networks

| Paper | Venue | Cited In |
|---|---|---|
| Ajima, Inoue, Hiramoto, Takagi, Shimizu, "The Tofu Interconnect D" (Fugaku) | HotI 2018 | [Interconnects](@/notes/hardware/interconnects.md) |
| Alverson, Roweth, Kaplan, "The Gemini System Interconnect" (Cray XE/XK) | HotI 2010 | [Interconnects](@/notes/hardware/interconnects.md) |
| Ballani, Costa, Behrendt, Cletheroe, Haller, Jozwik, Karinou, Lange, Shi, Thomsen, Williams, "Sirius: A Flat Datacenter Network with Nanosecond Optical Switching" | SIGCOMM 2020 | [Interconnects](@/notes/hardware/interconnects.md) |
| De Sensi, Di Girolamo, McMahon, Roweth, Hoefler, "An In-Depth Analysis of the Slingshot Interconnect" | SC 2020 | [Interconnects](@/notes/hardware/interconnects.md) |
| Faanes, Bataineh, Roweth, Court, Froese, Alverson, Johnson, Kopnick, Higgins, Reinhard, "Cray Cascade: A Scalable HPC System Based on a Dragonfly Network" (Aries) | SC 2012 | [Interconnects](@/notes/hardware/interconnects.md) |
| Khani, Ghobadi, Alizadeh, Zhu, Glick, Bergman, Vahdat, Klenk, Ebrahimi, "SiP-ML: High-Bandwidth Optical Network Interconnects for Machine Learning Training" | SIGCOMM 2021 | [Interconnects](@/notes/hardware/interconnects.md) |
| Liu, Theogarajan, Pinheiro, Vahdat, "Apollo: A Sequencing-Based Approach to Reconfigurable Optical Networks" | SIGCOMM 2021 | [Interconnects](@/notes/hardware/interconnects.md) |
| Mellette, Das, Guo, McGuinness, Snoeren, Porter, Papen, "Expanding Across Time to Deliver Bandwidth Efficiency and Low Latency" (Opera) | NSDI 2020 | [Interconnects](@/notes/hardware/interconnects.md) |
| Shaw, Adams, Azaria, Bank, Batson, Bell, Bergdorf et al., "Anton 2: Raising the Bar for Performance and Programmability in a Special-Purpose Molecular Dynamics Supercomputer" | SC 2014 | [Interconnects](@/notes/hardware/interconnects.md) |

## Standards & Specifications

| Spec | Year | Cited In |
|---|---|---|
| ARM, "AMBA AXI and ACE Protocol Specification" Issue G | 2021 | [Interconnects](@/notes/hardware/interconnects.md) |
| ARM, "AMBA CHI Architecture Specification" Issue F | 2023 | [Interconnects](@/notes/hardware/interconnects.md) |
| CXL Consortium, "Compute Express Link 3.2 Specification" | Dec 2024 | [Interconnects](@/notes/hardware/interconnects.md) |
| IEEE 802.1Qbb, "Priority-based Flow Control" | 2011 | [Interconnects](@/notes/hardware/interconnects.md) |
| IEEE 802.3df-2024, "200/400/800 Gb/s Ethernet" | 2024 | [Interconnects](@/notes/hardware/interconnects.md) |
| IEEE 802.3dj (draft), "1.6 Tb/s Ethernet" | 2026 (project) | [Interconnects](@/notes/hardware/interconnects.md) |
| InfiniBand Trade Association, "InfiniBand Architecture Specification 1.7 (Vol 1)" | 2023 | [Interconnects](@/notes/hardware/interconnects.md) |
| NVMe Express, "NVMe over Fabrics Specification 1.1a" | 2023 | [Interconnects](@/notes/hardware/interconnects.md) |
| PCI-SIG, "PCI Express Base Specification Revision 7.0" | 2025 | [Interconnects](@/notes/hardware/interconnects.md), [PCIe Internals](@/notes/hardware/pcie_internals.md) |
| UCIe Consortium, "Universal Chiplet Interconnect Express 2.1 Specification" | Aug 2025 | [Interconnects](@/notes/hardware/interconnects.md) |
| Ultra Ethernet Consortium, "Ultra Ethernet Specification 1.0" | Jun 2025 | [Interconnects](@/notes/hardware/interconnects.md) |

## Textbooks & Reference Works

| Paper | Venue | Cited In |
|---|---|---|
| Dally, Towles, "Principles and Practices of Interconnection Networks" | Textbook (Morgan Kaufmann 2003) | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md), [Interconnects](@/notes/hardware/interconnects.md) |
| Duato, Yalamanchili, Ni, "Interconnection Networks: An Engineering Approach" | Textbook (Morgan Kaufmann 2003) | [Interconnects](@/notes/hardware/interconnects.md) |
| Gregg, "BPF Performance Tools" | Addison-Wesley, 2019 | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Gregg, "Systems Performance" (2nd ed.) | Addison-Wesley, 2020 | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Hennessy, Patterson, "Computer Architecture: A Quantitative Approach" (6th ed.) | Textbook | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md), [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) |
| Kerrisk, "The Linux Programming Interface" | No Starch Press, 2010 | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Love, "Linux Kernel Development" (3rd ed.) | Addison-Wesley, 2010 | [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) |
| Navarro, "Succinct Data Structures" | Cambridge University Press, 2016 | [Data Structures](@/notes/programming/data_structures.md) |
| Weste, Harris, "CMOS VLSI Design: A Circuits and Systems Perspective" | Textbook | [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) |

---

*Last updated: 2026-05-01*
