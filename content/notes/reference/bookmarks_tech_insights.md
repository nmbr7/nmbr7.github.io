+++
title = "Bookmarks Tech Insights"
date = 2026-03-02
[taxonomies]
tags = ["survey", "case-studies", "postgresql", "kafka", "performance", "cloud-native", "rust"]
+++


# Tech Insights from Bookmarks

Curated technical bookmarks covering engineering case studies, systems techniques, data storage, and programming languages. Extracted from Chrome bookmarks (March 2026).

**Progress:** 60/200+ articles detailed | Last batch: 2026-05-26

> Legend: entries with `> **Key insights:**` blocks have been read and summarized.
> Run another batch anytime with: *"get details for 10 more articles"*

---

## Case Studies

Company engineering blogs, postmortems, architecture deep-dives.

### Database & Storage Infrastructure

- [How Uber Conquered Database Overload: From Static Rate-Limiting to Intelligent Load Management](https://www.uber.com/en-IN/blog/from-static-rate-limiting-to-intelligent-load-management/) -- Uber's evolution from static rate limiting to adaptive database load shedding

  > **Key insights:**
  > - Scale context: Docstore/Schemaless handle tens of millions of req/s across 170M+ MAU; minor overloads cascade across microservices
  > - Stateless quota-based rate-limiting failed at scale (Redis dependency, can't track thousands of partitions, broken cost model — full table scan costs same as single-row read); shifted shedding to storage layer where context is complete
  > - Concurrency (in-flight ops) chosen over QPS as primary overload signal — Little's Law `Concurrency = Throughput × Latency` maps directly to resource usage
  > - CoDel adapts queue policy: FIFO under normal load, LIFO under pressure ("newer requests still have a chance to succeed"); prevents wasted work on stale requests
  > - Cinnamon adds priority tiers (t0-t5): user-facing work protected at t1 while background jobs shed first — priority-aware on top of CoDel's priority-agnostic base
  > - PID controller treats overload as "dimmer switch" not binary reject; smooths recovery vs static thresholds that cause thundering herd
  > - Unified engine results: +80% throughput (5400 vs 3000 QPS), -70% P99 latency (1.0s vs 3.1s upserts), -93% goroutine count (10K vs 150K peak), -60% heap (1GB vs 5-6GB spikes)
  > - BYOS framework: pluggable signals (follower lag, write bytes, mem) feed unified decision loop without core rewrite
  > - Scorecard layer: per-tenant deterministic concurrency limits isolate noisy neighbors independently of system-wide shedding
  > - Regulators detect "low-fidelity" overload (large write payloads, partition hotspots, mem pressure) missed by concurrency metric alone
  > - Evolution: Phase 1 quota/Redis (failed) → Phase 2 CoDel + Scorecard → Phase 3 Cinnamon (6 priority tiers + PID controller) → Phase 4 BYOS unified engine
- [One Stone, Three Birds: Finer-Grained Encryption @ Apache Parquet](https://www.uber.com/en-IN/blog/one-stone-three-birds-finer-grained-encryption-apache-parquet/) -- Uber's column-level encryption for Parquet data at rest

  > **Key insights:**
  > - Single column-encryption mechanism solves three orthogonal needs: access control, retention/deletion, encryption-at-rest — avoids three separate systems
  > - Schema-driven: encryption metadata flows through Hive Metastore (HMS) not per-file RPC to tag store — eliminates excessive remote calls
  > - Per-column independent keys: each column encrypted with own key; access is "do you hold key K?" — permission enforcement at crypto layer, not app code
  > - Double-envelope key hierarchy: Data Encryption Keys (DEKs, per file/column) → Key Encryption Keys (KEKs, cached in Spark executors) → Master Encryption Keys (MEKs, in KMS); KMS contacted only once per MEK per executor, not per file
  > - Crypto-shredding for retention: deleting the master key turns ciphertext into garbage; no need to rewrite petabyte tables to expire one column
  > - Two algorithm modes: AES-GCM (authenticated, +5.7% write / +3.7% read with 60% columns encrypted) vs AES-GCM-CTR (metadata-only auth, 3–4.5× faster than full GCM in single-thread Java 9; integrity from Parquet row-group checksums)
  > - Key rotation modifies only the file footer (re-wrap DEKs/KEKs with new MEKs), not data pages — avoids re-encrypting column data
  > - Parquet-1817 plugin factory enables Spark/Hive/Presto/Flink compatibility without per-engine modification; encryption transparent to columnar projection, predicate pushdown, encoding, compression
  > - Auto-onboarding: tag changes propagate to ingestion pipelines; no manual table-by-table onboarding across PB-scale lake
  > - Backfilling petabytes of historical data was the hardest operational challenge; built 20× faster encryption tooling for re-encryption
  > - Mask-on-deny: users without key get null values instead of hard failure — legacy pipelines keep working
- [How Uber Indexes Streaming Data with Pull-Based Ingestion in OpenSearch](https://www.uber.com/en-IN/blog/how-uber-indexes-streaming-data-with-pull-based-ingestion-in-opensearch/) -- Pull-based streaming data indexing at Uber

  > **Key insights:**
  > - Core idea: replace OpenSearch's push-based translog with native pull from Kafka/Kinesis; cluster focuses on indexing, Kafka owns durability
  > - Each OpenSearch shard maps 1:1 to a stream partition; StreamPoller + IngestionPlugin interface handles source-specific consumer logic
  > - Blocking queue decouples consumer and processor for throughput; optional document-ID partitioning parallelizes writes
  > - IngestionEngine replaces translog with a no-op; stores `_BatchStartPointer` (min offset across active writers) with every Lucene commit for recovery
  > - Recovery: init → retrieve last `_BatchStartPointer` → rewind consumer → replay; prevents data loss and duplicate indexing on replica promotion
  > - External versioning supports out-of-order delivery: users set doc version in message; at-least-once processing + versioning = consistent views
  > - Error policies: Drop (discard + advance) or Block (retry indefinitely)
  > - Two replication modes: Segment Replication (primary ingests, replicas download via remote store — efficient but slight lag) vs All-Active (every shard ingests independently — zero lag, higher CPU)
  > - Regional clusters consume from globally replicated Kafka topics; each region holds a full copy for failover
- [Uforwarder: Uber's Scalable Kafka Consumer Proxy](https://www.infoq.com/news/2026/02/uber-uforwarder-kafka-push-proxy/) -- Push-based Kafka consumer proxy for event-driven microservices at scale

  > **Key insights:**
  > - Replaces pull-based Kafka client SDKs with gRPC push interface; centralizes offset management so application services need no Kafka library
  > - Scale: 1000+ downstream consumer services, trillions of messages/day, multiple PB/day of data
  > - Out-of-order commit tracker prevents head-of-line blocking: stuck message routes to DLQ while the commit pointer advances independently
  > - Context-aware routing via Kafka headers: infrastructure-level decisions (region, env, isolation) replace app-level filter code
  > - Auto-rebalancer reacts to CPU/memory/throughput signals continuously, redistributing partitions during traffic spikes without manual intervention
  > - DelayProcessManager enables per-partition pause/resume — selective backpressure isolates slow consumers without freezing the whole stream
  > - Eliminates bespoke delay/retry semantics in each service; one proxy implements the patterns once, all consumers inherit them
  > - Trade-off: extra gRPC hop adds latency vs direct Kafka client; justified by operational simplification at thousand-service scale
  > - Pattern: Kafka-proxy-as-platform is the natural successor to per-team Kafka client libraries when consumer count crosses ~100
- [Automating RDS Postgres to Aurora Postgres Migration (Netflix)](https://netflixtechblog.com/automating-rds-postgres-to-aurora-postgres-migration-261ca045447f) -- Netflix's automated large-scale PostgreSQL migration to Aurora

  > **Key insights:**
  > - Fleet of ~400 PostgreSQL clusters; manual migration unscalable — built fully automated self-service workflow requiring zero database credentials and zero application code changes
  > - Chose Aurora Read Replica approach over snapshot-based: continuous async replication keeps replica in sync, enabling validation while production traffic flows; trades implementation complexity for shorter downtime
  > - Data Access Layer (DAL) architecture: apps → forward proxy (mTLS) → Data Gateway (Envoy reverse proxy) → database; cutover is config change in proxy layer, not app change
  > - Quiescence: instruct users to halt app traffic, then enforce at infra layer by detaching RDS security groups + instance reboot — forcibly terminates all connections without needing DB credentials
  > - Replication lag validation subtlety: OldestReplicationSlotLag never settles at zero — oscillates 0↔64MB every ~5 min due to WAL segment rotation (archive_timeout=300s); 0 moment confirms full catch-up
  > - Lag formula: `pg_current_wal_lsn() - restart_lsn`; new WAL segment advances current position by one segment (64MB) before Aurora consumes it
  > - Cutover: promote Aurora read replica to standalone writable cluster, update Envoy Data Gateway routing config — all client connections transparently rerouted
  > - Full ecosystem parity: parameter groups, read replicas, replication slots all migrated to preserve functional equivalence
- [Stripe's DocDB: Zero-Downtime Data Movement for Trillion-Dollar Payments](https://qconsf.com/presentation/nov2025/stripes-docdb-how-zero-downtime-data-movement-powers-trillion-dollar-payment-processing) -- Stripe's document database powering zero-downtime payment processing
- [Pinterest's CDC-Powered Ingestion Slashes Database Latency from 24 Hours to 15 Minutes](https://www.infoq.com/news/2026/02/pinterest-cdc-db-ingestion/) -- Pinterest replacing batch ingestion with CDC for near-real-time data pipelines

  > **Key insights:**
  > - Old system: multiple independent batch pipelines with full-table dumps; 24+ hour latency despite only ~5% of rows changing daily; no row-level delete support
  > - New stack: Debezium/TiCDC → Kafka → Flink → Spark → Iceberg; two table types: CDC tables (append-only ledgers, sub-5-min latency) and Base tables (snapshots via Spark MERGE INTO, 15-min to 1-hour cadence)
  > - Standardized on Merge-on-Read (MoR) over Copy-on-Write: MoR writes deltas to separate files, resolves at query time — reduces write amplification and storage costs at petabyte scale
  > - Hash-based primary key bucket partitioning via Iceberg enables parallel upserts; ~100 buckets reduce per-task overhead
  > - At-least-once delivery with natural deduplication: MERGE INTO is idempotent on primary key (last-writer-wins), no explicit dedup infrastructure needed
  > - Bootstrap pipeline loads historical data initially; maintenance jobs handle compaction and snapshot expiration
  > - Config-driven onboarding supports MySQL, TiDB, KVStore; thousands of active pipelines across petabyte-scale data
  > - Results: latency 24h → 15min, compute costs slashed by processing only changed 5% of rows
- [Contributing to Debezium: Fixing Logical Replication at Scale (Zalando)](https://engineering.zalando.com/posts/2025/12/contributing-to-debezium.html) -- Zalando fixing Debezium CDC logical replication under heavy load

  > **Key insights:**
  > - Core conflict: Debezium's offset store and PostgreSQL's replication slot diverge in position tracking; connector fails with "Saved offset is before replication slot's confirmed lsn" forcing full re-syncs
  > - Root cause: Debezium 2.7.4+ hard-coded `withAutomaticFlush(false)`, disabling JDBC driver's keepalive LSN flush that Zalando depended on to prevent WAL pile-up on low-activity databases
  > - Contribution 1 (`lsn.flush.mode`, PR #6881): three modes — `manual`, `connector` (default), `connector_and_driver` (both flush, preventing WAL growth on idle tables)
  > - Contribution 2 (`offset.mismatch_strategy`, PR #6948): four strategies — `no_validation`, `trust_offset`, `trust_slot` (PostgreSQL slot authoritative), `trust_greater_lsn` (bidirectional sync using max LSN)
  > - Zalando's architecture differs: Patroni + custom Postgres Operator with ephemeral MemoryOffsetBackingStore, trusting slots as source of truth; most users trust persistent Kafka offset store instead
  > - Scale: 100+ Kubernetes clusters processing hundreds of thousands of events/second; zero detected data loss over nearly two years with billions of events processed
  > - `trust_greater_lsn` enables self-healing from slot/offset mismatches, reducing manual intervention in production
  > - Shipped in Debezium 3.4.0.Final (December 2025)
- [ClickPy at 2 Trillion rows: Scaling ingestion](https://clickhouse.com/blog/clickpy-2-trillion-rows) -- ClickHouse scaling Python package analytics to 2 trillion rows

  > **Key insights:**
  > - 2.21 trillion rows of Python package downloads from 2011+; pipeline: BigQuery → GCS → ClickPipes → staging DB → production DB
  > - ClickPipes replaced hand-rolled cron+ClickLoad: built-in retries, backoff, failure handling, and pipeline state tracking vs manual retry logic
  > - Null Engine + Materialized View pattern: ClickPipes writes to Null engine table (data doesn't persist), single MV handles schema normalization and type conversion before writing to main table
  > - Hot swap migration: cloned 14 tables + MVs to staging, ran both pipelines in parallel comparing daily row counts, then clean cutover
  > - Schema optimizations: LowCardinality strings for country/type/installer, Enum8 for CI field, Tuple nesting for file metadata, derived fields via splitByChar+arraySlice
  > - 13 separate materialized views pre-compute aggregations by different dimensions (daily, by version, by installer, by country)
  > - Historical data repair via lightweight DELETEs on multi-trillion-row tables; daily-grouped MVs auto-repopulate, non-daily MVs require drop/re-ingest/recreate cycle
  > - Discovered silent historical discrepancies between BigQuery source and ClickHouse only through systematic comparison
- [A 2.5x faster Postgres parser with Claude Code](https://multigres.com/blog/ai-parser-engineering) -- Multigres engineering a faster PostgreSQL parser

  > **Key insights:**
  > - Pure Go implementation replaces pg_query_go's cgo wrapper: eliminates cross-compilation pain, platform-specific builds, cgo runtime overhead
  > - Ports the real Postgres grammar verbatim, not a simplified variant — avoids perpetual catch-up with PG syntax updates
  > - AI excels at translation (PG source → Go yacc) but errs on invention (deparsing logic without reference); discipline of using existing artifacts matters
  > - Project state lived in markdown files (checklists, phase docs, session summaries) — Claude's own memory was insufficient for multi-week project
  > - Speedup from 1 year → 8 weeks came from expert code review catching systematic AI errors (wrong type signatures, symptom-fixing, missing edge cases) — not autonomous generation
  > - 71.2% coverage via porting PG's own regression suite (thousands of decade-spanning queries) — validates "Postgres-compatible grammar" claim
  > - Benchmarks: simple SELECT 1.6µs vs 3.1µs (2×), complex SELECT 3.2µs vs 11.0µs (3.5×), CREATE TABLE 7.7µs vs 26.4µs (3.5×); full suite 145ms vs 366ms (2.5×)
  > - Scope: 287,786 lines across 304 files ported in 8 weeks (1 engineer + Claude); previous Vitess MySQL parser took over a year with a team
  > - Mechanical work (translation, test code, AST node generation) delegated to AI; architectural work (grammar debug, design) kept with humans
  > - Lesson: "fast output means nothing if output is wrong" — every grammar rule manually compared to PG source, every test failure investigated
- [VACUUM FULL Locked Our Database for 14 Hours on Black Friday](https://medium.com/lets-code-future/vacuum-full-locked-our-database-for-14-hours-on-black-friday-33daf7959c9b) -- Production incident: Postgres VACUUM FULL during peak traffic

  > **Key insights:**
  > - Trigger: 84% dead tuples in `orders` table; engineer ran `VACUUM FULL` at 2:14 AM on Black Friday; 14-hour lockout → ~$340K lost revenue
  > - VACUUM FULL takes `ACCESS EXCLUSIVE` lock — blocks all SELECT/INSERT/UPDATE/DELETE; rewrites entire table row-by-row; ~4h on 180GB table
  > - Key difference from regular VACUUM: regular VACUUM marks dead tuples reusable without locking; VACUUM FULL rewrites, reclaims disk at OS level
  > - Cannot be cleanly cancelled (leaves partial rewrites); not transactional; `pg_cancel_backend()` ineffective
  > - Duration is roughly constant regardless of bloat ratio — the test run's 4h estimate was misleading
  > - Fix 1: tune autovacuum — 5% scale_factor instead of 20%, higher cost limits, naptime=10s
  > - Fix 2: adopt `pg_repack` — rebuilds tables/indexes without `ACCESS EXCLUSIVE`, online operation
  > - Fix 3: partition time-series data; drop old partitions instead of deleting rows
  > - Process: require CTO approval for VACUUM FULL, prohibit during peak, add bloat monitoring
  > - Design lesson: 10–20% bloat is acceptable; disk is cheaper than downtime
- [Our Database Had 500 Million Rows, Deleting 100 Million Took 6 Days](https://medium.com/@devcommando/our-database-had-500-million-rows-deleting-100-million-took-6-days-cc45a67b8c01) -- Lessons on bulk delete performance in large production databases

  > **Key insights:**
  > - MVCC overhead: PostgreSQL marks rows deleted (dead tuples) rather than removing them immediately; dead tuples consume disk and degrade scans
  > - Single DELETE: massive lock contention, WAL flood, all indexes updated per row — killed after 6h with zero rows removed
  > - Batched DELETE degradation: batch 1 took 2s, batch 100 took 23s, batch 300 took 60+s — subquery re-scans increasingly bloated table
  > - VACUUM after batches: found 5M dead tuples after partial deletion; regular VACUUM doesn't reclaim OS disk; VACUUM FULL causes outages
  > - Index maintenance multiplies I/O: `created_at` and other indexes require update per row deleted
  > - Winning approach: create new table with `PARTITION BY RANGE(created_at)`, insert only retained rows, atomic swap during maintenance window, drop old table — avoids fighting MVCC entirely
  > - Design lesson: partition by time at schema design time; `pg_partman` for automation; then DROP PARTITION takes milliseconds vs days of DELETE
- [When an Aurora PostgreSQL Major Upgrade Fails](https://medium.com/@dhiraj_db/when-an-aurora-postgresql-major-upgrade-fails-a-lesson-from-a-hidden-view-27cda5842bbe) -- Debugging a hidden view blocking Aurora PostgreSQL upgrade

  > **Key insights:**
  > - Aurora PG 15→17 in-place upgrade halted during `pg_restore` with: `ERROR: column reference 'query_id' is ambiguous`
  > - Root cause: custom monitoring view `pg_stat_activityenric` built on `pg_stat_get_activity()` using explicit PG15 column list; PG16+ expanded the function's output columns, causing `query_id` name collision
  > - The view existed across multiple databases — removing it from one DB wasn't enough; `pg_upgrade` hit identical incompatible definitions in others
  > - Diagnosis: `SELECT * FROM pg_catalog.pg_views WHERE viewname = 'pg_stat_activityenric'` in every database
  > - Fix: drop the view from every database before upgrade; recreate using PG16+-compatible column references post-upgrade
  > - Lesson: custom views on internal PostgreSQL system functions (`pg_stat_*`) require compatibility audits before major version upgrades; avoid explicit column lists tied to system function output
- [Unlocking 3x Write Performance: Cloud SQL MySQL Optimizations](https://medium.com/google-cloud/unlocking-3x-write-performance-a-deep-dive-into-cloud-sql-mysql-optimizations-69a504856170) -- Google Cloud tripling MySQL write throughput
  > *(article unavailable — fetch failed)*
- [How We Solved a Critical Race Condition in Banking Systems](https://souravkabiraj.medium.com/how-we-solved-a-critical-race-condition-in-banking-systems-623c140b796d) -- Debugging concurrency bugs in production banking

### Platform & Infrastructure

- [Debugging a FUSE deadlock in the Linux kernel (Netflix)](https://netflixtechblog.com/debugging-a-fuse-deadlock-in-the-linux-kernel-c75cd7989b6d) -- Kernel-level FUSE deadlock root cause analysis

  > **Key insights:**
  > - Netflix uses FUSE filesystems for container image layers; deadlock caused containers to hang indefinitely on file operations
  > - FUSE architecture: kernel VFS → FUSE kernel module → userspace daemon; requests queued in kernel, daemon reads /dev/fuse, processes, writes response back
  > - Deadlock scenario: FUSE daemon itself triggers a VFS operation on the same FUSE filesystem while handling a request — kernel holds inode lock waiting for daemon response, daemon blocks waiting for inode lock
  > - Debugging methodology: crash dumps, /proc/PID/stack for blocked threads, ftrace to trace kernel lock acquisition chains
  > - Root cause in specific kernel code path where page cache invalidation during FUSE writeback took inode mutex, then re-entered FUSE for metadata — circular dependency
  > - Fix required kernel patch to avoid holding inode mutex across FUSE round-trips; contributed upstream to Linux kernel
  > - Key lesson: userspace filesystem daemons must never re-enter the same filesystem they serve, or kernel must not hold locks across FUSE calls
- [Migrating Millions of Concurrent Websockets to Envoy (Slack)](https://slack.engineering/migrating-millions-of-concurrent-websockets-to-envoy/) -- Slack's WebSocket infrastructure migration to Envoy proxy

  > **Key insights:**
  > - Old setup: HAProxy across multiple AWS regions; required "hot restarts" on every backend endpoint change, complex lifecycle management
  > - Why Envoy: dynamically configured clusters/endpoints (no reloads), zone-aware routing, passive health checking, panic routing
  > - Migration strategy: parallel Envoy stack alongside HAProxy, gradual weighted DNS shift (10% -> 25% -> 50% -> 75% -> 100%) over 6 months
  > - Config managed via Chef libraries generating Envoy YAML programmatically; intentionally supported only used features initially
  > - Extracting "important" HAProxy config from accumulated tech debt was hardest part; undocumented behavioral dependencies needed replication
  > - Subtle issues: broke daily active user metrics temporarily; "load balancer behavior is complex" with no shortcut around debugging
  > - Lacked pre-migration automated tests; discovered expected behaviors through service owner consultation
  > - Result: complete HAProxy replacement with zero customer impact; subsequently exceeded previous peak load with no issues
- [How Dropbox Designed ATF: an Async Task Framework](https://dropbox.tech/infrastructure/asynchronous-task-scheduling-at-dropbox) -- Dropbox's distributed async task scheduling system

  > **Key insights:**
  > - Six components: Frontend (RPC) → Task Store (Edgestore) → Store Consumer → SQS → Controller → Executor + heartbeat status controller
  > - At-least-once execution: tasks retry until Success or FatalFailure; pull-based polling (controllers/executors long-poll) reduces coupling vs push
  > - Scale: 9000 async tasks/sec, 100+ use cases across 28 teams; 95% start within 5 s of schedule time
  > - Tasks claim exclusive "Claimed" state to prevent overlap; HSC kills executors after 3 failed heartbeats — zombie protection
  > - Per (lambda, priority) pair gets dedicated SQS queue (95 total); lambda owners control their own worker clusters and capacity
  > - Idempotence mandatory in user lambdas — framework explicitly does not solve dedup; pushes correctness burden to callback authors
  > - Exponential backoff for retriable failures; timeouts at enqueue/claim/heartbeat each trigger automatic retry independently
  > - Isolation via dedicated clusters, queues, and quotas per lambda — prevents resource contention between independent task types
  > - Edgestore (Dropbox's metadata DB) backs task state; SQS handles work distribution — clean split of state-of-truth vs work queue
- [How Spotify Built Its Data Platform To Understand 1.4 Trillion Data Points](https://blog.bytebytego.com/p/how-spotify-built-its-data-platform) -- Spotify's data platform for processing trillions of events
- [How Tailscale works](https://tailscale.com/blog/how-tailscale-works) -- Architecture of Tailscale's WireGuard-based mesh VPN

  > **Key insights:**
  > - Separation of concerns: centralized coordination server (control plane: auth, key distribution, ACL, network maps) + full mesh of WireGuard tunnels (data plane: peer-to-peer encrypted UDP)
  > - Key exchange via Noise IK over X25519; coordination server is shared drop box for WireGuard public keys — never sees plaintext traffic
  > - DERP (Detoured Encrypted Routing Protocol): custom relay over HTTP replacing TURN; relays encrypted WireGuard packets; every connection starts via DERP, upgrades to direct UDP after NAT traversal succeeds
  > - Custom DISCO protocol for NAT traversal: NaCl box authenticated UDP path probing; achieves >90% direct P2P connection rate, DERP relay rarely needed for sustained data
  > - End-to-end encryption regardless of path: DERP relays forward opaque ciphertext, never possess decryption keys (Curve25519, ChaCha20-Poly1305)
  > - ACLs defined centrally (JSON/HuJSON policy language), pushed to each node in network map; nodes enforce locally in WireGuard filter rules — cryptographically enforced (no key = no connection)
  > - MagicDNS: automatic human-readable hostnames + Let's Encrypt TLS certificates for every device in tailnet without manual cert management
  > - Hybrid topology: hub-and-spoke control (persistent connections to coordination server) + full mesh data (direct WireGuard tunnels, no central bottleneck)
- [How WebSockets Cost Recall.ai $1M on AWS](https://www.recall.ai/blog/how-websockets-cost-us-1m-on-our-aws-bill) -- Postmortem on expensive WebSocket architecture on AWS

  > **Key insights:**
  > - Meeting bots used WebSockets over localhost to transport raw video from headless Chromium to encoder — seemed reasonable for IPC but catastrophically inefficient at scale
  > - WebSocket fragmentation: Chromium fragments messages >131KB into frames; single 1080p raw frame (3.1MB) = 24 fragments with reassembly overhead
  > - WebSocket masking: spec mandates XOR masking on all client-to-server data — extra pass over every byte at 150MB/s throughput (p99 bot bandwidth)
  > - CPU profiling revealed dominance of `__memmove_avx_unaligned_erms` and `__memcpy_avx_unaligned_erms` — excessive memory copying throughout transport
  > - Evaluated alternatives: TCP/IP rejected (1500-byte MTU fragmentation + kernel-space copying); Unix domain sockets rejected (user-to-kernel transitions)
  > - Solution: custom lock-free multi-producer single-consumer ring buffer in shared memory; three pointers (write, peek, read) enabling zero-copy reads
  > - Implementation details: atomic operations for thread-safety, named semaphores for signaling, variable-sized frame support, Chromium sandbox-compatible
  > - Impact: bot CPU 4 cores → 2 cores (50% reduction) = over $1M annual AWS savings; scale context: 1TB video/second across infrastructure
- [How Okta Scaled From 12 to 1,000 Kubernetes Clusters With Argo CD](https://thenewstack.io/how-okta-scaled-from-12-to-1000-kubernetes-clusters-with-argo-cd/) -- Okta's Kubernetes fleet scaling with GitOps
- [Pinterest's Moka: Kubernetes Rewriting Rules of Big Data Processing](https://www.infoq.com/news/2026/01/pinterest-kubernetes-bigdata/) -- Pinterest migrating big data workloads to Kubernetes

  > **Key insights:**
  > - Moka = Pinterest's EKS-based unified big-data platform replacing Hadoop YARN clusters; runs Spark, Flink, Ray on Kubernetes with single control plane
  > - YuniKorn scheduler used instead of stock kube-scheduler: hierarchical queues, gang scheduling, fair sharing — restores YARN-like multi-tenancy semantics
  > - Fluent Bit + OpenTelemetry pipeline replaces YARN log aggregation; per-pod structured logging shipped to central store
  > - ARM Graviton support adds ~20% cost reduction for batch workloads vs equivalent x86 instances
  > - Karpenter for autoscaling: bin-packs jobs onto right-sized spot nodes; faster than Cluster Autoscaler's ASG-based provisioning
  > - Migration approach: dual-write to YARN and Moka, validate parity, cut over per-workload; avoided big-bang switch
  > - Container image caching critical at scale: pre-warmed Spark images on nodes eliminates pull latency during gang scheduling
  > - Lesson: Kubernetes as big-data substrate is viable but requires non-default scheduler + dedicated logging/observability stack
- [Reducing Onboarding from 48 Hours to 4: Amazon Key's Event-Driven Platform](https://www.infoq.com/news/2026/02/amazon-key-event-driven-platform/) -- Amazon Key's event-driven architecture redesign

  > **Key insights:**
  > - Migrated from synchronous REST orchestration to event-driven via single EventBridge bus shared across accounts; cross-account event routing replaces direct service calls
  > - Onboarding time: 48 hours → 4 hours (12× reduction); driven by self-service event subscriptions instead of bespoke integration code per partner
  > - CDK-based infrastructure automation: each consumer defines event filters declaratively; rules + targets + IAM provisioned in single deployment
  > - Throughput: ~2000 events/sec sustained, P90 latency ~80ms end-to-end across multi-account hops, 99.99% delivery success
  > - Schema registry enforces contract evolution; producers can't break consumers via uncoordinated payload changes
  > - DLQ + replay tooling per consumer enables independent failure recovery without affecting peer subscribers
  > - Tradeoff: debugging eventual-consistency flows harder than sync request/response; invested in distributed tracing (X-Ray) as compensation
  > - Pattern reusable: single shared bus + cross-account access + schema registry is the production blueprint for EventBridge at scale
- [How Slack Achieved Operational Excellence for Spark on Amazon EMR](https://aws.amazon.com/blogs/big-data/how-slack-achieved-operational-excellence-for-spark-on-amazon-emr-using-generative-ai/) -- Slack's Spark operational improvements on EMR
- [We Moved from AWS to Hetzner, Cut Costs 89%](https://medium.com/lets-code-future/we-ran-go-rust-postgresql-and-kubernetes-in-production-for-two-years-heres-the-catch-961ea2b9237c) -- Real-world cost comparison: AWS to bare metal

  > **Key insights:**
  > - AWS monthly: 6× t3.medium ($1200) + RDS db.t3.large ($850) + LB ($180) + data transfer ($650) + S3 ($120) + CloudWatch ($380) + NAT Gateway ($220) + misc ($600) = **$4,200/month**
  > - Hetzner monthly: 6× CAX11 equivalent ($280) + managed PG ($90) + LB ($15) + 1TB bandwidth included + 500GB storage ($25) = **$410/month** (+ Cloudflare $20) = **~$470/month**
  > - **Savings: $45,600/year (89% reduction)**; Hetzner CAX11 has dedicated CPU + NVMe vs t3.medium's shared CPU
  > - Zero-downtime migration: week 1 infra setup → week 2 DB migration (export/import + replication) → week 3 gradual DNS shift 10→50→100% → week 4 AWS shutdown
  > - Problems hit: new Hetzner IPs flagged as spam (SPF/DKIM warmup needed), 100K req/s DDoS attack (required Cloudflare), manual backup scripting, self-managed Grafana+Prometheus
  > - Lost: managed services (ElastiCache, SQS, Lambda, EventBridge), global regions (limited to DE/FI/US), auto-scaling, built-in DDoS protection, AWS support
  > - Gained: predictable billing, dedicated CPU, included bandwidth, full control
- [Migrating 40 Lambdas to Containers, AWS Bill Down 73%](https://medium.com/lets-code-future/i-migrated-40-lambdas-to-containers-aws-bill-went-down-73-6dc0c17de3fb) -- Cost and architecture tradeoffs: Lambda to containers

### Networking & Load Balancing

- [Examining Load Balancing Algorithms with Envoy](https://blog.envoyproxy.io/examining-load-balancing-algorithms-with-envoy-1be643ea121c) -- Comparison of load balancing strategies (round-robin, least-request, ring hash, Maglev)
  > *(article unavailable — SSL certificate error)*
- [High Availability Load Balancers with Maglev (Cloudflare)](https://blog.cloudflare.com/high-availability-load-balancers-with-maglev/) -- Google's Maglev consistent hashing for L4 load balancing

  > **Key insights:**
  > - Maglev scheduler: consistent hashing on 5-tuple (protocol, src IP, src port, dst IP, dst port) → same backend selected by any LB without shared state
  > - HA via statelessness: routers use BGP + ECMP hashing to distribute across multiple LB instances; all LBs apply identical Maglev hash → traffic always reaches correct backend even after LB failover
  > - Graceful maintenance: operator withdraws BGP session, traffic transparently shifts to remaining LBs with zero disruption
  > - Ungraceful failure: BGP keepalive timeout triggers router to terminate session; BFD could reduce delay but incompatible with L2 aggregation/VXLAN
  > - Direct Server Return (DSR) via Foo-Over-UDP encapsulation: return traffic bypasses LBs entirely — LBs only process inbound
  > - IPVS configured with Maglev scheduler at kernel level; stateless by design eliminates connection synchronization between LBs
- [Andromeda: Performance, Isolation, and Velocity at Scale (Google, NSDI'18)](https://www.usenix.org/conference/nsdi18/presentation/dalton) -- Google's production network virtualization stack

### Serverless & Compute

- [Cloud Computing Without Containers (Cloudflare)](https://blog.cloudflare.com/cloud-computing-without-containers/) -- V8 isolate-based serverless as a container alternative

  > **Key insights:**
  > - V8 Isolates replace containers/VMs as isolation boundary: each tenant runs in a lightweight V8 execution context (same sandbox as Chrome tabs), not a full process/container/VM
  > - Sub-millisecond cold starts (many under 1ms) vs hundreds of ms for containers or seconds for VMs; eliminates cold start as a meaningful concern
  > - Memory overhead ~1-5 MB per isolate vs ~35+ MB per container; enables thousands of tenants per process — critical for economic viability at 200+ edge PoPs
  > - Security model: V8's battle-tested sandbox (no cross-isolate memory access, no syscalls, CPU/memory caps) + process-level seccomp + separate isolate groups as defense in depth
  > - No filesystem, no network sockets, no native code: API surface restricted to Service Workers spec (fetch, crypto, streams, KV bindings) — eliminates path traversal, SSRF, native code exploit classes
  > - Anycast routing: code runs at nearest PoP (all 200+ locations simultaneously), no region selection; single-digit-ms latency to end users globally
  > - Per-request billing model enabled by near-zero isolate startup cost — fundamentally different economics vs per-container-hour
  > - Tradeoff: no long-lived connections or persistent in-memory state; must use external services (Durable Objects, Workers KV, R2) for stateful workloads
  > - WASM support extends model beyond JavaScript: Rust/C/C++/Go via WASM in same isolate sandbox with same cold-start properties
- [Eliminating Cold Starts 2: Shard and Conquer (Cloudflare)](https://blog.cloudflare.com/eliminating-cold-starts-2-shard-and-conquer/) -- Sharding strategy to eliminate serverless cold starts

  > **Key insights:**
  > - Problem: complex Workers with 10MB scripts now have cold starts longer than TLS handshakes (up to 400ms CPU time); direct optimization insufficient
  > - Solution: consistent hash ring maps script IDs to "home" shard servers; requests routed to the server most likely to have a warm instance
  > - Optimistic routing: requests sent without pre-approval; if shard server refuses, returns client's own "lazy capability" (Cap'n Proto RPC loopback reference) — stops sending bytes immediately
  > - Cap'n Proto distributed object model: context stacks (ownership overrides, resource limits, feature flags) serialize for cross-machine transmission; trace data consolidates via capabilities
  > - Results: 10× reduction in eviction rate globally; Enterprise warm request rate improved from 99.9% to 99.99%; cold starts dropped from 0.1% to 0.01%
  > - Only 4% of enterprise traffic actually sharded — power-law distribution means targeting low-traffic Workers (most likely to be evicted) yields disproportionate benefit
  > - Latency overhead sub-1ms for cross-server proxying vs typical cold start duration — net positive tradeoff
  > - Key insight: accepting minimal per-request IPC overhead eliminates cold starts entirely for tail-latency-sensitive workloads
- [R2 SQL: A Deep Dive into Our New Distributed Query Engine (Cloudflare)](https://blog.cloudflare.com/r2-sql-deep-dive/) -- Distributed SQL engine on top of R2 object storage

  > **Key insights:**
  > - Three-layer Iceberg metadata pruning: partition (manifest list) → file (manifest column stats) → row-group (Parquet footer stats) — eliminates data before any read
  > - Streaming pipeline: planner emits work units as soon as available; executor consumes concurrently — no "plan complete then execute" barrier
  > - ORDER BY-aware manifest ordering: planner walks files in user's sort order, enabling early termination when top-K heap's threshold exceeds remaining metadata high-water mark
  > - Row group as primary work unit: 1 multi-GB Parquet file = N parallel partitions, each with own CPU cache locality
  > - Built on DataFusion (Rust): vectorized execution, filter pushdown, row-group-level parallelization out of the box
  > - Columnar projection: only referenced columns transferred from R2 → massive reduction in network egress and decompression cost
  > - Arrow IPC over gRPC for worker→coordinator results; zero-copy on both ends inside the worker
  > - Serverless: runs on Workers + R2, no provisioned cluster; coordinator selected per query via internal API; Argo Smart Routing handles connectivity
  > - "Bite-sized pieces" model = power-of-two parallelism that adapts to query selectivity without explicit reshaping
- [R2 SQL Aggregations (Cloudflare)](https://blog.cloudflare.com/r2-sql-aggregations/) -- Adding GROUP BY/SUM to R2's distributed SQL engine

  > **Key insights:**
  > - Workers emit partial-aggregate states, not raw rows; "multiple pre-aggregates can be merged" enables horizontal scaling
  > - Scatter-gather works for simple aggregations (no HAVING/ORDER BY): coordinator receives small partial states, bounded memory regardless of input size
  > - High-cardinality GROUP BY (IPs, user IDs) breaks scatter-gather → triggers hash-based shuffle on GROUP BY columns; deterministic partitioning needs no central coordinator
  > - Synchronization barrier: workers buffer outbound shuffle data + await coordinator ACK before next stage — guarantees complete dataset per worker after shuffle
  > - Post-shuffle workers hold full per-group data → apply HAVING + local ORDER BY independently; coordinator only does final k-way merge
  > - LIMIT pushdown: coordinator merges streams until top-K found, then halts upstream; back-pressures workers to stop early
  > - Memory boundedness: pushing HAVING and sort down to workers prevents coordinator from becoming bottleneck even at PB scale
  > - Cardinality is the design dimension: low-card → scatter-gather (cheap), high-card → shuffle (correct); engine picks at plan time from stats
  > - Pattern reusable in any object-store SQL engine: Iceberg metadata + DataFusion + Arrow IPC shuffle = scalable analytics without long-lived cluster
- [The Principles of Extreme Fault Tolerance (PlanetScale)](https://planetscale.com/blog/the-principles-of-extreme-fault-tolerance) -- Design principles for highly fault-tolerant database infrastructure

  > **Key insights:**
  > - Three core principles: Isolation (physically/logically independent parts), Redundancy (replicated + isolated copies), Static Stability (last-known-good state on failure)
  > - Data plane (queries, storage) operates independently from control plane (management); control plane failures don't disrupt queries
  > - Each cluster: primary + minimum 2 replicas across 3 availability zones; synchronous replication (commit persists on replica before primary ACK)
  > - Weekly failover testing on every customer database as changes ship; ensures failover mechanisms remain practiced and reliable
  > - Progressive rollouts: changes ship gradually via feature flags and release channels; limits blast radius of operator errors
  > - Critical query path has minimal dependencies; external failures (Docker registry, control plane outages) don't impact active queries
  > - Automated failover handling: instance, zonal, and regional failures trigger failover with query buffering to minimize disruption
- [PlanetScale Postgres Operations Philosophy](https://planetscale.com/docs/postgres/operations-philosophy) -- Operational design principles for managed Postgres

  > **Key insights:**
  > - Three-node mandatory minimum (primary + 2 replicas) across AZs; no single-node deployments offered even at lowest tier — fault tolerance baseline non-negotiable
  > - Synchronous replication via Postgres `synchronous_commit = remote_apply` to at least one replica; commit fence waits for replica apply (not just receive) before client ACK
  > - 10-second target failover: orchestrator detects primary failure → promotes most-caught-up replica → updates routing → in-flight queries buffered
  > - Dual connection paths: PgBouncer transaction pooler for high concurrency + direct unpooled for prepared statements / advisory locks / `SET LOCAL`
  > - No CPU autoscaling: scaling triggers replica swap with larger instance — predictable cost, no thrash, but requires headroom planning
  > - Vacuum and autovacuum tuning intentionally conservative: prevents wraparound emergencies on long-running multi-TB tenants
  > - Backups: continuous WAL archiving to S3 + nightly base backups; PITR to any second within retention window
  > - Philosophical bias: prefer "boring, predictable" operations over "elastic, dynamic" — fewer moving parts = fewer failure modes
- [Aurora DSQL: Serverless, Scalable, Global OLTP (Marc Brooker, CMU)](https://db.cs.cmu.edu/events/pg-vs-world-aurora-dsql-marc-brooker/) -- Aurora DSQL architecture deep-dive

### Postmortems

- [Supabase Incident on February 12, 2026](https://supabase.com/blog/supabase-incident-on-february-12-2026) -- Supabase production incident postmortem

  > **Key insights:**
  > - Root cause: deployment inadvertently enabled AWS VPC Block Public Access in "block-bidirectional" mode regionally — disabled all internet gateways across 20+ subnets in us-east-2
  > - Total regional outage: all services (DB, Auth, APIs, Edge Functions, Storage, Realtime) down for 3h42m; VPC-peered customers unaffected
  > - 14-minute detection lag: outage started 21:12 UTC, first alert at 21:26 — blind spot let cascading failures propagate
  > - Investigation misdirected by elevated Management API errors → team chased AWS provider issue, not network; single CloudTrail `ModifyVpcBlockPublicAccessOptions` line "did not jump out"
  > - Pre-prod environment lacked us-east-2 → week of test deploys revealed nothing; environment parity gap is the structural fault
  > - Correlation breakthrough at 3h required matching deployment timestamp (21:12) with outage onset + cross-team infrastructure engagement
  > - Access control gap: monitoring service deployment had no guardrails preventing account/region-scoped AWS resource modifications
  > - Comms failures stacked: status page lagged, dashboard banners didn't appear, social channels silent for hours
  > - Forward fix: non-customer services moved to separate AWS accounts, blocklist for problematic resource types, external connectivity probes, full pre-prod parity across all regions
- [Post-mortem of Shai-Hulud Attack (PostHog)](https://posthog.com/blog/nov-24-shai-hulud-attack-post-mortem) -- PostHog production attack postmortem
- [Railway: Diagnosing System Failure with Logs, Metrics, Traces, and Alerts](https://www.infoq.com/news/2026/01/railway-diagnosing-failure/) -- Postmortem-driven approach to observability

### Language Adoption

- [WhatsApp Deploys Rust-Based Media Parser to Block Malware on 3B Devices](https://www.infoq.com/news/2026/02/whatsapp-rust-media-malware/) -- WhatsApp replacing C/C++ parsers with Rust at massive scale

  > **Key insights:**
  > - ~160K LOC of C++ media-parsing code replaced by ~90K LOC of Rust (~44% reduction); deployed to all 3B devices via WhatsApp client
  > - "Kaleidoscope" = Rust-based malware/threat-detection engine running alongside parser; flags malicious media before decode reaches OS codecs
  > - Memory-safety class of bugs (use-after-free, OOB read, double-free in image/video parsing) — historically the dominant exploit surface in messengers — eliminated by Rust ownership model at compile time
  > - Binary-size overhead measured at ~200 KB on Android — explicitly judged acceptable for the safety guarantee; APK budget engineering required to stay within tolerance
  > - Cross-platform: same Rust crate compiled for Android (NDK), iOS, Windows, macOS — reduces parser-divergence bugs across client platforms
  > - Differential fuzzing harness ran Rust + C++ parsers on same inputs to validate bitwise-identical output before cutover
  > - Pattern: pick the high-blast-radius security-critical layer (media parsing) as first Rust beachhead in a giant C++ codebase, not greenfield modules
  > - Confirms Microsoft/Google trend: 70% of CVEs are memory-safety; Rust-at-parser-boundary is the highest-leverage mitigation
- [Ladybird Adopts Rust](https://ladybird.org/posts/adopting-rust/) -- Ladybird browser project's strategy for incremental Rust adoption

  > **Key insights:**
  > - Phased coexistence, not rewrite: Rust modules live behind well-defined C++ interop boundaries; C++ stays primary language
  > - LibJS chosen as first target: lexer + parser + AST + bytecode generator — self-contained, huge test coverage (test262), low coupling
  > - Byte-for-byte compatibility required: 52,898 test262 + 12,461 Ladybird regression tests must produce identical output, zero perf regression
  > - Translated Rust deliberately non-idiomatic: preserves C++ register-allocation patterns so both compilers emit identical bytecode opcodes
  > - AI-assisted (Claude Code, Codex) but human-steered: "hundreds of small prompts" + adversarial review, not autonomous generation
  > - 25,000 lines ported in ~2 weeks vs estimated months — productivity gain comes from AI as smart translator + human as architect/reviewer
  > - Core team gatekeeps porting: contributors must coordinate before starting to prevent duplicate work and divergent design choices
  > - Avoids the "rewrite trap": each ported module proves itself via test parity before next is started; never a half-Rust/half-C++ broken state
  > - Pattern matches WhatsApp's Rust strategy: target security/perf-critical, self-contained modules first; don't try to convert the world
- [Banned C++ in Chromium](https://medium.com/@build_break_learn/modern-c-is-a-lie-chromium-treats-half-the-standard-library-as-a-bug-42a9aa60a427) -- Why Chromium bans large portions of the C++ standard library
- [We Trusted Rust With the 3 Components That Could Not Fail](https://medium.com/@Krishnajlathi/we-trusted-rust-with-the-3-components-that-could-not-fail-ad1554f41dda) -- Production Rust for mission-critical components

  > **Key insights:**
  > - Three components chosen for Rust: **parsing**, **routing**, **boundary** — selected not for language preference but because these were the parts "we could not afford to be wrong about"
  > - Under +38% request surge: other components saw CPU plateau and P99 jump from 210ms → 4.8s; Rust components maintained identical latency, unchanged memory, 0.00% error rate
  > - Key failure modes avoided: queue growth, allocator fragmentation, synchronized retry storms — all emerged in non-Rust components under pressure
  > - Core insight: "Correct" architecturally ≠ "safe" under stress; Rust's compile-time guarantees caught failure modes that testing couldn't
  > - Written alongside C++ differential fuzzing for parity validation before transition
- [Apache Iggy's Migration to Thread-per-Core Architecture Powered by io_uring](https://iggy.apache.org/blogs/2026/02/27/thread-per-core-io_uring/) -- Thread-per-core + io_uring migration for high-throughput messaging

  > **Key insights:**
  > - Tokio's work-stealing executor hit a ceiling: task migrations caused cache invalidations, regular file I/O blocked threads despite epoll readiness
  > - io_uring is completion-based (submit op, kernel drives to completion) vs epoll's readiness-based model; heavily batches syscalls reducing context switches
  > - Chose compio runtime over monoio/glommio for active maintenance and decoupled driver/executor architecture
  > - "Work stealing to work steering": one thread per CPU core, no shared state, reduced lock contention
  > - Pitfall: RefCell borrows across .await points cause runtime panics; solved with ECS-style component splitting (State, Storage)
  > - Hybrid consistency: shared strongly-consistent resources + sharded eventually-consistent ones via left-right concurrent data structure
  > - Results: P99 latency -60% (4.52ms to 1.82ms, 32 partitions), P9999 -57%; fsync mode: +18% throughput, -16% P95 latency
  > - Gap identified: POSIX APIs don't expose io_uring capabilities (request chaining, registered buffers); ecosystem lacks DST-friendly pluggable components

---

## Techniques

Algorithms, performance, OS internals, networking, compilers.

### CPU & Performance Optimization

- [Understanding CPU Microarchitecture to Increase Performance](https://www.infoq.com/presentations/microarchitecture-modern-cpu/) -- CPU pipelines, branch prediction, cache hierarchies, perf-aware code
- [Software Optimization Resources (Agner Fog)](https://www.agner.org/optimize/#manuals) -- Definitive manuals on C++ and assembly optimization, microarchitecture
- [Optimizing C++ (Agner Fog)](https://www.agner.org/optimize/optimizing_cpp.pdf) -- Comprehensive C++ performance optimization guide
- [Abseil Performance Hints](https://abseil.io/fast/hints.html) -- Google's Abseil library tips for high-performance C++
- [Optimizations Past Their Prime (Abseil)](https://abseil.io/fast/9) -- Which classic optimizations no longer help on modern hardware

  > **Key insights:**
  > - Runtime CPU feature dispatch is wasteful once an ISA extension is universal: checking for `popcnt` on every modern x86_64 burns cycles for an always-yes answer
  > - Inline asm blocks compiler optimization: hand-written `popcnt` asm prevented LLVM from fixing a known false-dependency bug — the "fast" path stayed slow
  > - `__builtin_popcount` overtook hand-tuned asm once compilers emit `popcnt` directly + constant-fold + inline aggressively
  > - Redundant null checks (`CHECK_EQ` re-checking `str_ != nullptr`) can't be eliminated by optimizer once the abstraction stack hides the invariant
  > - Wrapping `std::string*` in `CheckOpString` hid pointer relationships → optimizer lost the ability to reason about control flow
  > - Debug builds sometimes outperformed release: layers of dead optimization had become counterproductive overhead
  > - Idiomatic code ages better than clever code: clear portable C++ stays optimizer-friendly as hardware evolves; intrinsics rot
  > - General rule: an optimization "valuable in 2010" deserves re-benchmarking; the cost-benefit can flip silently as compilers + CPUs improve
  > - Counter-intuitive corollary: removing old optimizations is itself an optimization worth doing
- [How Michael Abrash Doubled Quake Framerate](https://fabiensanglard.net/quake_asm_optimizations/index.html) -- Classic assembly-level optimization from Quake development
- [I/O Is No Longer the Bottleneck](https://stoppels.ch/2022/11/27/io-is-no-longer-the-bottleneck.html) -- How NVMe SSDs shifted the bottleneck from I/O to CPU

  > **Key insights:**
  > - Sequential read: 1.6 GB/s cold cache, 12.8 GB/s warm cache on modern NVMe
  > - Hand-optimized AVX2 word-counting: only 1.45 GB/s (warm) = 11% of sequential disk speed
  > - Standard C `wc -w`: 245 MB/s (6.5x slower than disk); vectorized C: 330 MB/s (4.8x slower)
  > - Branch prediction in inner loops prevents compiler auto-vectorization; manual SIMD required
  > - Hash map cache misses create additional CPU bottlenecks beyond raw throughput
  > - Key takeaway: single-threaded CPU processing is now the real constraint, not storage I/O
  > - Implication: system design should optimize for computation efficiency, not just I/O patterns
- [Best Practice Guide: Modern Processors and Accelerators (PRACE)](https://prace-ri.eu/wp-content/uploads/Best-Practice-Guide-Modern-Processors-Accelerators.pdf) -- NUMA, cache hierarchies, vectorization, and HPC optimization
- [Sub-NUMA Clustering vs Hemisphere/Quadrant Modes](https://stackoverflow.com/questions/76127861/whats-the-difference-between-sub-numa-clustering-and-hemisphere-and-quadrant) -- Intel SNC and NUMA topology modes for memory-performance tuning
- [Performance and Benchmarking (Chapter 1)](https://github.com/djiangtw/performance-and-benchmarking-public/blob/main/manuscript/chapters/chapter01.md) -- Foundations of performance measurement: metrics, methodology, pitfalls
- [Tech Column: Cache, NoC, Performance Optimization](https://github.com/djiangtw/tech-column-public) -- Cache design, network-on-chip, hardware-software co-optimization
- [Perf Ninja: Low-Level Performance Analysis Course](https://github.com/dendibakh/perf-ninja) -- Hands-on CPU microarchitecture performance tuning course
- [Inside High-Frequency Trading Systems: The Race to Zero Latency](https://levelup.gitconnected.com/inside-high-frequency-trading-systems-the-race-to-zero-latency-faa638d0c180) -- Architecture and latency optimization patterns in HFT
- [I Made Zig Compute 33 Million Satellite Positions in 3 Seconds](https://atempleton.bearblog.dev/i-made-zig-compute-33-million-satellite-positions-in-3-seconds-no-gpu-required/) -- SIMD and cache-friendly optimization in Zig

  > **Key insights:**
  > - Zig's `@Vector(4, f64)` SIMD primitive is portable: LLVM backend picks AVX/NEON/etc. — no per-arch intrinsics in user code
  > - Branchless hot path uses `@select` masked-selection: compute both branches, pick per-lane — avoids branch-mispredict cost in tight propagation loop
  > - `comptime` precomputation bakes gravity/polynomial constants into the binary; no runtime init — gave scalar baseline 5.2M propagations/sec start
  > - Cache-tiling at 64 time-points per satellite batch keeps time data hot in L1/L2 across 13,000 sats; opposite of naïve sat-major iteration
  > - SoA layout: `ElementsV4` holds each orbital element as its own `@Vector(4, f64)` — "pre-splatting" eliminates broadcast ops inside hot loop
  > - Custom polynomial atan2 (LLVM has no vectorized atan2): ~1e-7 rad accuracy = ~10mm at LEO, well below SGP4's km-scale error budget
  > - Final perf: 11-13M propagations/sec native SIMD (2× scalar), 7M/sec via Python bindings, full 13,000-sat catalog in 3.3 s
  > - Lesson: algorithmic parallelism (lane organization, cache tiling, SoA) dominates raw hardware — same chip, 2× from layout alone
  > - Zig as systems language: comptime + native SIMD + no FFI overhead makes it competitive with hand-written C/Rust for numerics

### Concurrency & Parallelism

- [Is Parallel Programming Hard? (Paul McKenney's perfbook)](https://mirrors.edge.kernel.org/pub/linux/kernel/people/paulmck/perfbook/perfbook.2024.12.27a.pdf) -- Comprehensive reference: parallel programming, memory ordering, RCU, lock-free algorithms
- [The ABA Problem in Concurrency](https://www.baeldung.com/cs/aba-concurrency) -- ABA problem in lock-free data structures and solutions
- [Multi-Core By Default (Ryan Fleury)](https://www.rfleury.com/p/multi-core-by-default) -- Designing software for multi-core from the start
- [Memory Management Reference](https://www.memorymanagement.org/) -- Allocators, GC algorithms, and memory management techniques

### Hashing & Data Structures

- [Looking at Randomness and Performance for Hash Codes](https://vanilla-java.github.io/2018/08/15/Looking-at-randomness-and-performance-for-hash-codes.html) -- Empirical hash function quality and performance trade-offs
- [wyhash: The Fastest Quality Hash Function](https://github.com/wangyi-fudan/wyhash) -- Extremely fast, high-quality hash function for production
- [Sort Research in Rust](https://github.com/Voultapher/sort-research-rs/tree/main) -- Benchmarking sort algorithms (pdqsort, timsort, etc.) in Rust
- [Workshop on Filter Data Structures (SPAA 2023)](https://prashantpandey.github.io/workshop/) -- Bloom, cuckoo, quotient filters and modern filter structures
- [Undergraduate Upends a 40-Year-Old Data Science Conjecture](https://www.quantamagazine.org/undergraduate-upends-a-40-year-old-data-science-conjecture-20250210/) -- Breakthrough disproof of Kannan-Lovasz-Simonovits conjecture

### Linux Kernel & eBPF

- [Interactive Map of Linux Kernel](https://makelinux.github.io/kernel/map/) -- Visual map of Linux kernel subsystems
- [Linux Kernel Schedulers](https://documentation.ubuntu.com/real-time/latest/explanation/schedulers/) -- CFS, SCHED_FIFO, SCHED_DEADLINE overview
- [Sched: Rewrite MM CID Management (Thomas Gleixner)](https://lore.kernel.org/lkml/20251015164952.694882104@linutronix.de/) -- Kernel scheduler patch: 15% PostgreSQL improvement
- [Cache and TLB Flushing Under Linux](https://docs.kernel.org/core-api/cachetlb.html) -- Cache/TLB coherence APIs
- [Memory Allocation Guide (Linux Kernel)](https://docs.kernel.org/core-api/memory-allocation.html) -- Slab allocator, kmalloc, vmalloc, GFP flags
- [Announcing systing 1.0](https://josefbacik.github.io/kernel/systing/debugging/2026/02/23/systing-1.0.html) -- New Linux kernel tracing/debugging tool

  > **Key insights:**
  > - eBPF-based system tracer by Josef Bacik (btrfs maintainer); output writes directly to DuckDB Parquet for SQL post-analysis instead of bespoke trace formats
  > - Timeline view: per-task scheduling state (running/runnable/blocked) overlaid with stack traces at sched_switch + sched_wakeup events
  > - Stuck on networking issue → systing identified syscall-level blocker via kretprobe timing → cut 12 s tail latency to 2 s after fix
  > - MCP integration: Claude (or any LLM client) can query the DuckDB trace via SQL, ask "which threads were blocked longest and on what" — natural-language perf forensics
  > - Kretprobe-based regression detection: compares per-function latency distributions across runs; flags 99th-percentile shifts that average masks
  > - Designed to replace ad-hoc combinations of perf + bpftrace + flamegraph + custom scripts for everyday kernel-side debugging
  > - DuckDB choice deliberate: columnar Parquet trace files are durable, shareable, and analyzable offline without re-running workload
  > - Positions DuckDB-backed traces as a general pattern for systems observability — same idea seen in eBPF profiler ecosystems
- [AI Helped Uncover a 50-80x Improvement for Linux io_uring](https://www.phoronix.com/news/AI-50-80x-IO-uring) -- Major io_uring performance improvement
- [All My Favorite Tracing Tools: eBPF, QEMU, Perfetto](https://thume.ca/2023/12/02/tracing-methods/) -- Survey of tracing/profiling tools for systems performance
- [eBPF on Hard Mode](https://feyor.sh/blog/ebpf-on-hard-mode/) -- Advanced eBPF usage patterns and pitfalls

  > **Key insights:**
  > - Unprivileged eBPF: limited to 4096 instructions, no subprograms/loops/back edges; only socket filters and cgroup socket buffers
  > - Full capability requires CAP_BPF + CAP_NET_ADMIN + CAP_PERFMON
  > - BTF (BPF Type Format) required for advanced features: subprograms and callbacks need explicit type signatures
  > - Writing without libbpf/LLVM means manually constructing instruction arrays — "bytecode rawdogging"
  > - String matching via `strncmp` helper needs read-only maps with BPF_F_RDONLY_PROG flags and freezing
  > - KFunc calls use BTF ID-based invocation, requiring runtime extraction from /sys/kernel/btf/vmlinux
  > - Verifier transforms dead code into infinite loops (ja -1); ALU constants rewritten as Spectre mitigation
  > - Verifier output is essential debugging tool: logs reveal register states and instruction processing metrics
  > - Kernel version sensitivity: verifier gets smarter each release, creating compatibility risks for bytecode-level programs
- [eBPF Ring Buffer vs Perf Buffer](https://kubefront.net/system/ebpf/ring-buffer-vs-perf-buffer/) -- Comparing eBPF event output mechanisms
- [ePass: Verifier-Cooperative Runtime Enforcement for eBPF](https://ebpf.foundation/epass-verifier-cooperative-runtime-enforcement-for-ebpf/) -- Novel eBPF safety combining verifier and runtime enforcement
- [Profiling in Production: eBPF Continuous Profiling](https://medium.com/@yashbatra11111/profiling-in-production-without-killing-performance-ebpf-continuous-profiling-5a92a8610769) -- Always-on production profiling with minimal overhead
- [profile-bee: Rust-based eBPF CPU Profiler](https://github.com/zz85/profile-bee/) -- Lightweight eBPF profiler with stack unwinding
- [BPF Instruction Set Specification](https://docs.kernel.org/bpf/standardization/instruction-set.html) -- Formal eBPF ISA specification
- [Building eBPF/XDP L2 DSR Load Balancer from Scratch](https://labs.iximiuz.com/tutorials/xdp-dsr-layer2-lb-92b02f3e) -- Hands-on XDP/eBPF load balancer
- [Building eBPF/XDP IP-in-IP DSR Load Balancer](https://labs.iximiuz.com/tutorials/xdp-dsr-load-balancer-b701a95a) -- IP-in-IP encapsulation variant

### Networking

- [How NAT Traversal Works](https://tailscale.com/blog/how-nat-traversal-works/) -- STUN, TURN, ICE, and NAT hole-punching techniques

  > **Key insights:**
  > - Stateful firewalls permit inbound UDP only after matching outbound traffic; two peers must send packets simultaneously for hole-punching
  > - STUN: "what's my endpoint from your point of view?" reveals public IP:port mapping created by NATs
  > - NAT taxonomy: Endpoint-Independent Mapping (EIM, "easy", consistent ports) vs Endpoint-Dependent Mapping (EDM, "hard", varies by destination)
  > - Birthday paradox optimization for symmetric NATs: open multiple ports on one side, probe random ports on other — statistically faster than exhaustive scan
  > - Port mapping protocols (UPnP IGD, NAT-PMP, PCP) allow explicit port forwarding requests, "making one NAT vanish from the data path"
  > - Tailscale's DERP: simultaneous fallback relay and upgrade helper to peer-to-peer connections
  > - ICE core algorithm: "try everything at once, and pick the best thing that works"
  > - Hairpinning: NATs often fail to route between internal devices using external addresses; problematic with CGNAT
  > - IPv6 eliminates many issues but mixed deployments require NAT64, DNS64, CLAT compatibility layers
- [QUIC: A UDP-Based Multiplexed and Secure Transport (RFC 9000)](https://datatracker.ietf.org/doc/html/rfc9000) -- QUIC transport protocol specification (HTTP/3 foundation)
- [HyStart++: Modified Slow Start for TCP (RFC 9406)](https://www.rfc-editor.org/rfc/rfc9406.html) -- Improved TCP slow-start algorithm
- [Stream Control Transmission Protocol (RFC 9260)](https://www.rfc-editor.org/rfc/rfc9260.html) -- SCTP: multi-streaming, multi-homing transport
- [WebRTC for the Curious: Real-time Networking](https://webrtcforthecurious.com/docs/05-real-time-networking/) -- Jitter buffers, congestion control, real-time transport
- [Network Protocols, Sans I/O](https://sans-io.readthedocs.io/) -- Protocol state machines decoupled from I/O
- [Networking Protocol Sequence Diagrams](https://www.eventhelix.com/networking/) -- Visual sequence diagrams for TCP, IP, ARP, DHCP
- [TUN/TAP Interface Tutorial](https://backreference.org/2010/03/26/tuntap-interface-tutorial/index.html) -- Virtual network interfaces for tunneling
- [How Container Networking Works: Bridge Network from Scratch](https://labs.iximiuz.com/tutorials/container-networking-from-scratch) -- Linux namespaces, veth pairs, and bridges

### Containers & Virtualization

- [How Container Filesystem Works: Building a Docker-like Container](https://labs.iximiuz.com/tutorials/container-filesystem-from-scratch) -- Overlay filesystems and container image internals
- [FUSE - Filesystem in Userspace (Linux Kernel docs)](https://www.kernel.org/doc/html/latest/filesystems/fuse.html) -- Kernel-side FUSE architecture and request handling
- [virtio specification v1.2](https://docs.oasis-open.org/virtio/virtio/v1.2/virtio-v1.2.pdf) -- OASIS standard for para-virtualized I/O devices
- [gVisor: Sandboxed Container Runtime](https://gvisor.dev/docs/) -- Google's user-space kernel for container isolation
- [crosvm: Chrome OS Virtual Machine Monitor](https://crosvm.dev/book/introduction.html) -- Google's Rust-based VMM for Chrome OS / Android
- [Building the Virtualization Stack with rust-vmm](https://opensource.com/article/19/3/rust-virtual-machine) -- Reusable Rust crates for custom VMMs (Firecracker, Cloud Hypervisor)
- [How Terminals Work](https://how-terminals-work.vercel.app/) -- Terminal emulators, TTY subsystem, and PTY internals

### Compilers & Toolchain

- [LLVM Architecture (AOSA Book)](https://aosabook.org/en/v1/llvm.html) -- Chris Lattner on LLVM's modular compiler architecture

  > **Key insights:**
  > - Three-phase design: frontend (parsing/AST) -> optimizer (mid-level transforms) -> backend (codegen); enables N languages x M targets without N*M implementations
  > - LLVM IR is a "first-class language with well-defined semantics" in 3 forms: textual .ll, in-memory data structures, binary bitcode
  > - IR is fully self-contained (unlike GCC's GIMPLE): no reference to frontend/backend data structures; enables text-based pipelines and external tools
  > - Modular pass architecture: independent optimization passes (inlining, constant prop, etc.) can be mixed/reordered; PassManager resolves dependencies
  > - Library-based design: clients link only needed functionality; "collection of useful compiler technology" not a monolithic compiler
  > - Target Description Language (.td): declare registers/instructions/constraints once; tblgen auto-generates assemblers, disassemblers, instruction selectors
  > - Bitcode serialization enables link-time optimization (LTO) and install-time optimization across translation units
  > - Individual passes testable in isolation via IR load -> run pass -> verify output; BugPoint automates test case reduction
  > - Separation of concerns: frontend devs need only IR semantics; backend authors work independently; lowers contribution barriers
- [LLVM Documentation](https://llvm.org/docs/index.html) -- Official LLVM docs: IR, passes, backends, tooling
- [LLVM Inliner Pass Deep Dive](https://www.compilersutra.com/docs/llvm/llvm_pass_tracker/transformpass/llvm-inliner-pass-v1-deep-dive/) -- LLVM function inlining pass analysis
- [LLVM Machine Code Analyzer on Godbolt (Arm)](https://learn.arm.com/learning-paths/cross-platform/mca-godbolt/) -- Instruction scheduling and pipeline throughput analysis
- [How Compiler Explorer Works in 2025 (Matt Godbolt)](https://xania.org/202506/how-compiler-explorer-works) -- Architecture behind godbolt.org
- [Compiler Engineering in Practice -- Part 1](https://chisophugis.github.io/2025/12/08/compiler-engineering-in-practice-part-1-what-is-a-compiler.html) -- Practical compiler engineering series
- [CS 6120: Advanced Compilers (Cornell, Self-Guided)](https://www.cs.cornell.edu/courses/cs6120/2025fa/self-guided/) -- SSA, optimization passes, dataflow analysis
- [ACM India Winter School on Compiler Design](https://www.cse.iitm.ac.in/~krishna/acm-winter-school-2025/) -- IIT Madras compiler design materials
- [Clang Hardening Cheat Sheet - Ten Years Later](https://blog.quarkslab.com/clang-hardening-cheat-sheet-ten-years-later.html) -- Clang/LLVM compiler flags for binary hardening
- [Finding and Understanding Bugs in C Compilers (Csmith, PLDI'11)](http://www.cs.utah.edu/~regehr/papers/pldi11-preprint.pdf) -- Random C program generation for compiler testing
- [Test-Case Reduction for C Compiler Bugs (C-Reduce, PLDI'12)](http://www.cs.utah.edu/~regehr/papers/pldi12-preprint.pdf) -- Automated test case minimization
- [Reflections on Trusting Trust (Ken Thompson)](https://www.win.tue.nl/~aeb/linux/hh/thompson/trust.html) -- Classic on compiler trust chains

### Debuggers & Profiling

- [The GDB JIT Interface](https://bernsteinbear.com/blog/gdb-jit/) -- Registering JIT-compiled code with GDB for debugging
- [RAD Debugger (Epic Games)](https://github.com/EpicGamesExt/raddebugger/) -- Native graphical debugger, open source
- [Demystifying Debuggers (Ryan Fleury)](https://www.rfleury.com/p/posts-table-of-contents) -- How debuggers work at the OS/CPU level

### Distributed Systems Theory

- [Hedging: A Simple Tactic to Tame Tail Latency](https://blog.alexoglou.com/posts/hedging/) -- Request hedging patterns for P99 latency reduction

  > **Key insights:**
  > - Hedging sends duplicate requests to alternate backends after a timeout threshold (e.g., 20ms); use whichever responds first
  > - Requires idempotent operations to prevent side effects from duplicate execution
  > - Google BigTable: 96% reduction in tail latency with only 2% increase in total requests
  > - Google MapReduce: backup tasks reduced overall runtime by 44%
  > - Grafana Tempo: 45% reduction in tail latency
  > - Simulation (20K requests): P99 87.88ms to 19.13ms (-78%), P100 278.62ms to 19.94ms (-93%), mean 12.13ms to 9.71ms (-20%), load overhead only 6.8%
  > - Most effective when multiple backend instances exist and rare server slowdowns cause tail latency
  > - Threshold selection is critical: too aggressive wastes resources, too conservative misses the window
- [Keeping CALM: When Distributed Consistency is Easy](https://arxiv.org/pdf/1901.01930.pdf) -- CALM theorem: monotonic programs can be eventually consistent
- [Distributed Transactional Systems Cannot Be Fast](https://arxiv.org/pdf/1903.09106.pdf) -- Fundamental lower bounds on distributed transaction latency
- [Shinjuku: Preemptive Scheduling for Microsecond-scale Tail Latency (NSDI'19)](https://www.usenix.org/conference/nsdi19/presentation/kaffes) -- Microsecond-scale preemptive scheduling for datacenter RPCs
- [uCache: A Customizable Unikernel-based IO Cache (FAST'26)](https://www.usenix.org/conference/fast26/presentation/meignan-masson) -- Unikernel-based I/O caching layer
- [Cuttlefish: Coordination-free Distributed State Kernel](https://github.com/abokhalill/cuttlefish) -- Nanosecond-latency distributed state without coordination
- [Distributed System Algorithms Reference](https://github.com/pipethedev/distributed-system-algorithms/blob/main/ALGORITHMS.md) -- Curated distributed systems algorithms with explanations
- [On System Design (ACM)](https://dl.acm.org/doi/epdf/10.1145/1167515.1167513) -- Classic ACM paper on principles of system design

### Misc Techniques

- [Write Your Own Virtual Machine (LC-3)](https://www.jmeiners.com/lc3-vm/) -- Step-by-step guide to building an LC-3 VM
- [Writing an OS: Baby Steps](https://tutorialsbynick.com/writing-an-os-baby-steps/) -- Bare-metal OS development from bootloader to protected mode
- [FreeRTOS Context Switch Implementation](https://www.freertos.org/implementation/main.html) -- How FreeRTOS implements task context switching
- [UTF-8 Everywhere](https://utf8everywhere.org/) -- Technical argument for UTF-8 as the universal encoding
- [Full-Blown Cross-Assembler in a Bash Script](https://hackaday.com/2026/02/06/full-blown-cross-assembler-in-a-bash-script/) -- Multi-target cross-assembler entirely in Bash
- [Introduction to IA-32e Hardware Paging](https://www.triplefault.io/2017/07/introduction-to-ia-32e-hardware-paging.html) -- x86-64 page table internals
- [ELF Binaries on Linux: Understanding and Analysis](https://linux-audit.com/elf-binaries-on-linux-understanding-and-analysis/) -- ELF format internals
- [How to Write Shared Libraries (Ulrich Drepper)](https://www.akkadia.org/drepper/dsohowto.pdf) -- Definitive guide to ELF shared libraries, PLT/GOT, dynamic linking
- [Shared Libraries in Windows and Linux](https://www.youtube.com/watch?v=6TrJc06IekE) -- Comparing dynamic linking and symbol resolution across OSes
- [Dijkstra's in Disguise](https://blog.evjang.com/2018/08/dijkstras.html) -- How many algorithms reduce to shortest path problems

---

## Data Storage

Databases, storage engines, file formats, replication, caching.

### PostgreSQL

- [The Internals of PostgreSQL (interdb.jp)](https://www.interdb.jp/pg/) -- Free book: buffer manager, WAL, MVCC, executor, query processing

  > **Key insights (Ch.9 WAL):**
  > - XLOG records written to WAL buffer in memory, then flushed synchronously to WAL segment files on transaction commit
  > - LSN (Log Sequence Number) = location where record is written on the transaction log; unique identifier for each XLOG record
  > - Checkpoint writes a special XLOG record containing the REDO point = "location to write the XLOG record at the moment when checkpoint started"
  > - Full-page writes (FPW, default on): first modification after checkpoint writes header + entire page as "backup block" — torn page protection
  > - Recovery replays XLOG records sequentially from REDO point; record replayed only if record LSN > page LSN, otherwise skipped
  > - PostgreSQL XLOG = REDO log only; no UNDO log support (unlike Oracle/MySQL InnoDB)
  > - Backup blocks can restore pages corrupted during background writer operations (torn writes)
  > - Checkpoint processing and database recovery are tightly coupled and inseparable
- [Learning PostgreSQL Internals (Paul Ramsey)](https://blog.cleverelephant.ca/2022/10/postgresql-links.html) -- Curated list of PostgreSQL internals resources
- [PostgreSQL Hacking Workshop](https://github.com/pghacking/workshop) -- Hands-on PostgreSQL source code workshop
- [PostgreSQL Internals - Indexes, WAL, MVCC, Locks and Queries](https://gitlab.com/-/snippets/4918687) -- Concise reference on core Postgres internals
- [PostgreSQL Recovery Internals](https://www.cybertec-postgresql.com/en/postgresql-recovery-internals/) -- WAL replay, crash recovery, timeline handling
- [PostgreSQL High-Availability Architectures](https://www.cybertec-postgresql.com/en/postgresql-high-availability-architectures/) -- Streaming replication, Patroni, PgBouncer patterns
- [PostgreSQL Performance: Latency in Cloud and On Premise](https://www.cybertec-postgresql.com/en/postgresql-performance-latency-in-the-cloud-and-on-premise/) -- Benchmarking latency across deployment environments
- [Unlocking High-Performance PostgreSQL: Key Memory Optimizations](https://stormatics.tech/blogs/unlocking-high-performance-postgresql-key-memory-optimizations) -- shared_buffers, work_mem, OS page cache tuning

  > **Key insights:**
  > - PG never reads directly from disk to client: data page → shared_buffers → caller; the buffer cache is the central perf knob
  > - Default `shared_buffers = 128MB` is inadequate; production dedicated boxes want 20-25% of RAM, ceiling ~40% before OS page-cache competition hurts
  > - `work_mem` is per-operation not per-session: 5 parallel workers × work_mem = 5× allocation; the dominant OOM trigger when tuned naively
  > - `pg_stat_database` cache-hit-ratio + `EXPLAIN (ANALYZE, BUFFERS)` together pinpoint which queries spill — measure before tuning
  > - Small system (< 64 GB) work_mem formula: ≈ 0.25% of RAM (~3 MB / GB) — aggressive enough to suppress sort spills
  > - Large system (≥ 64 GB) safer formula: `max(162MB, 0.125% RAM + 80MB)` — prevents exponential growth under parallelism
  > - `shared_buffers` requires restart; `work_mem` can be set per session/role/transaction — fine-grained tuning without downtime
  > - Over-sizing `shared_buffers` competes with OS page cache and increases dirty-page flush volume per checkpoint — write spikes
  > - Tuning order: measure cache hit ratio → fix shared_buffers → measure per-query spills → tune work_mem at session/role level, never globally aggressive
- [Importance of Tuning Checkpoint in PostgreSQL](https://www.percona.com/blog/importance-of-tuning-checkpoint-in-postgresql/) -- Checkpoint tuning for write-heavy workloads

  > **Key insights:**
  > - Checkpoints guarantee heap + index files reflect all writes before that LSN — establish the REDO recovery point
  > - Full-page images (FPI) on first modification after checkpoint create predictable I/O spike — protects against torn pages but hurts steady-state perf
  > - Benchmark: 5-min → 60-min `checkpoint_timeout` cut WAL volume from 12 GB → 2 GB (6×) and FPI writes from 1.47M → 161K (9×)
  > - Production rule: `checkpoint_timeout` ≥ 30 min; default 5 min is far too aggressive for write-heavy workloads
  > - `max_wal_size` too small undoes timeout setting — triggers WAL-volume-driven checkpoints early, restoring the FPI cascade
  > - `checkpoint_completion_target = 0.9` spreads dirty-page writes across 90% of interval — eliminates synchronous I/O cliff at boundary
  > - Recovery-speed misconception: PG replays WAL at ≥64 MB/s; even hour-long checkpoints recover in minutes, not hours — long intervals are safe
  > - Bgwriter complements checkpointer: continuously trickles dirty pages so checkpoints have less to flush
  > - Trade-off: longer intervals = more WAL retained for recovery + larger replay window vs much lower steady-state write amplification
- [Upgrading 200GB Postgres Within 10 Minutes in Heroku](https://rosenfeld.page/articles/2025_11_16_upgrading_200_gb_postgres_within_10_minutes_in_heroku) -- Fast major-version PostgreSQL upgrades
- [Mastering Logical Replication in PostgreSQL](https://boringsql.com/guides/mastering-logical-replication/) -- Comprehensive logical replication guide
- [Listen to Database Changes through the Postgres WAL](https://peterullrich.com/listen-to-database-changes-through-the-postgres-wal) -- WAL-based change data capture
- [PostgreSQL Materialized Views](https://stormatics.tech/blogs/postgresql-materialized-views-when-caching-your-query-results-makes-sense) -- When and how to use materialized views
- [You Don't Need Elasticsearch: BM25 Is Now in Postgres](https://www.tigerdata.com/blog/you-dont-need-elasticsearch-bm25-is-now-in-postgres) -- Full-text search with BM25 ranking in Postgres
- [10 Elasticsearch Production Issues and How Postgres Avoids Them](https://www.tigerdata.com/blog/10-elasticsearch-production-issues-how-postgres-avoids-them) -- Elasticsearch pain points vs PostgreSQL alternatives
- [Postgres 18 Features I Will Actually Use in Production](https://medium.com/@maahisoft20/postgres-18-features-i-will-actually-use-in-production-dc0f7151f3ef) -- PostgreSQL 18 most impactful new features
- [PostgreSQL Developer Options: debug_io_direct](https://www.postgresql.org/docs/current/runtime-config-developer.html#GUC-DEBUG-IO-DIRECT) -- Direct I/O developer option bypassing OS page cache
- [PostgreSQL Inval Reliability for Inplace Updates](https://www.postgresql.org/message-id/flat/20250824233927.89.nmisch%40google.com) -- Cache invalidation correctness for inplace tuple updates
- [Scale PostgreSQL Horizontally with PgDog](https://pgdog.dev/) -- PostgreSQL proxy for horizontal sharding
- [Go + Postgres with sqlc: The Zero-ORM Stack](https://medium.com/@yashbatra11111/go-postgres-with-sqlc-the-zero-orm-stack-cloudflare-uses-for-99-99-uptime-d4beaddbfdf8) -- Type-safe SQL in Go as used at Cloudflare
- [Explain Plan Visualizer by Datadog](https://explain.datadoghq.com/) -- Interactive tool for visualizing PostgreSQL EXPLAIN output

### MySQL & InnoDB

- [The Basics of InnoDB Undo Logging and History System](https://blog.jcole.us/2014/04/16/the-basics-of-the-innodb-undo-logging-and-history-system/) -- InnoDB MVCC undo log chain and purge system
- [InnoDB Architecture (MySQL 8.1)](https://dev.mysql.com/doc/refman/8.1/en/innodb-architecture.html) -- Buffer pool, redo/undo, tablespaces, doublewrite

### Storage Engines & Key-Value Stores

- [Log-Structured Merge Trees (Interactive)](https://jidin.org/lsm/) -- Visual explanation of LSM tree internals
- [Build Your Own KV Storage Engine -- Deletes, Tombstones, Compaction](https://read.thecoder.cafe/p/build-your-own-kv-engine-4) -- Hands-on KV engine with LSM-style compaction
- [CockroachDB Pebble: Binary Fuse Filters](https://github.com/cockroachdb/pebble/pull/5700) -- Binary fuse filters (faster than Bloom) in CockroachDB's LSM engine

  > **Key insights:**
  > - Xor-based structure: fingerprints satisfy f[h1(k)] XOR f[h2(k)] XOR f[h3(k)] = k using 3 independent hash functions across consecutive segments
  > - Construction via hypergraph "peeling" algorithm: find positions with degree 1, solve iteratively until all keys processed
  > - ~24 bits per key during construction (12-24MB for typical L6 sstables with 500K-1M keys)
  > - Superior false positive rates: 8-bit binary fuse achieves ~1/256 FP vs 1/88 for traditional 10-bits-per-key Bloom
  > - Supports custom bitpacking: 4, 8, 12, or 16-bit fingerprint variants
  > - Query accesses 3 segments (potentially >1 cache line), but CPU parallelizes independent lookups; cold-cache only 1-2% slower than Bloom on M1
  > - Construction 2-3x slower than Bloom for short keys; gap reduces with longer keys (faster XXH3 hashing)
  > - Memory-conscious pooling: sync.Pool reuse for small/medium filters, limited concurrency for large, no reuse for very large
  > - PR adds full implementation without enabling anywhere yet; staged rollout planned
  > - TPCC benchmarks: Bloom queries = 0.2% CPU; binary fuse substitution estimated "about a wash" including construction overhead
- [bf-tree: Concurrent Larger-than-Memory Range Index (Microsoft Research)](https://github.com/microsoft/bf-tree) -- Modern concurrent B-tree variant in Rust
- [From Building Houses to Storage Engines (TidesDB)](https://tidesdb.com/articles/from-building-houses-to-storage-engines/) -- Lessons from building a storage engine from scratch
- [What Does a Database for SSDs Look Like? (Marc Brooker)](https://brooker.co.za/blog/2025/12/15/database-for-ssd.html) -- SSD-optimized database storage engine design

  > **Key insights:**
  > - Challenges WAL-centric durability: replication across machines provides superior durability; local WAL unnecessary
  > - SSD transfer sweet spot: 32kB — below wastes throughput (IOPS-limited), above doesn't improve (throughput-limited); random access now viable
  > - Large pages (1MB+) optimized for spinning disks create false sharing on SSDs with poor spatial locality
  > - Updated five-minute rule: cache pages expected to be accessed within ~30 seconds (not 1986's economics)
  > - "Commit transactions to a distributed log" across AZs rather than local system durability
  > - Cross-AZ latency only at commit boundaries; batch coordination to leverage modern datacenter bandwidth
  > - Use strong hardware clocks for consistent reads across replicas without coordination overhead
  > - Default to SNAPSHOT isolation (not serializable) to avoid per-write coordination
  > - Preserve core relational model, SQL, atomicity, strong consistency — the abstractions remain valuable
- [The Quest for One Million IOPS at LanceDB](https://lancedb.com/blog/one-million-iops) -- Storage I/O benchmarking and optimization
- [HelixDB: Graph-Vector Database in Rust](https://github.com/HelixDB/helix-db) -- Combined graph + vector database in Rust
- [I Built Google Bigtable in Go](https://jitesh117.github.io/blog/implementing-google-bigtable-in-golang/) -- Simplified Bigtable showing core SSTable/memtable concepts

### Apache Arrow & Parquet

- [Apache Arrow C++ Cookbook](https://arrow.apache.org/cookbook/cpp/) -- Practical Arrow array/table examples in C++
- [A Practical Dive Into Late Materialization in arrow-rs Parquet Reads](https://arrow.apache.org/blog/2025/12/11/parquet-late-materialization-deep-dive/) -- Late materialization to skip unnecessary I/O

  > **Key insights:**
  > - Late materialization: defer data column decoding until after predicates filter rows, minimizing I/O and CPU
  > - "LM-pipelined" strategy: sequentially evaluate predicates, build sparse row masks, then decode only surviving rows
  > - RowSelection abstraction: RLE for large skips, bitmasks for tiny gaps; adaptive switching based on avg run length (threshold: 32)
  > - RowSelection::and_then combines successive filters via linear-time zipper algorithm, no data copies
  > - Page pruning: skip entire Parquet pages when metadata confirms no selected rows, eliminating decompression
  > - Dual-layer caching (shared global + local pinned) prevents double-decoding when columns serve both filter and projection
  > - Zero-copy conversions for fixed-width types: decoded vectors handed directly to Arrow buffers
  > - Fuzz testing validates coordinate transformations between relative/absolute row offsets across batch boundaries
  > - Transforms Parquet reader into "mini query engine" with selective I/O efficiency
- [parquet-linter: A Better Parquet Is Parquet Itself](https://blog.xiangpeng.systems/posts/parquet-linter/) -- Validating and optimizing Parquet file layout
- [Hardwood: Minimal Dependency Parquet Implementation](https://github.com/hardwood-hq/hardwood) -- Clean Parquet implementation for learning

### Query Engines & OLAP

- [Building Index-Backed Query Plans in DataFusion](https://pierrezemb.fr/posts/datafusion-index-provider/) -- Adding index support to DataFusion's query planner
- [Optimizing SQL CASE Expression Evaluation (DataFusion)](https://datafusion.apache.org/blog/2026/02/02/datafusion_case/) -- CASE expression optimization
- [Optimizing Repartitions in DataFusion](https://datafusion.apache.org/blog/output/2025/12/15/avoid-consecutive-repartitions/) -- Eliminating redundant repartitions
- [Extending SQL in DataFusion: from ->> to TABLESAMPLE](https://datafusion.apache.org/blog/2026/01/12/extending-sql/) -- DataFusion SQL extensibility
- [Apache DataFusion Comet Overview](https://datafusion.apache.org/comet/about/index.html) -- Native vectorized Spark execution on DataFusion/Arrow
- [Efficient String Compression for Modern Database Systems (CedarDB)](https://cedardb.com/blog/string_compression/) -- String compression in analytical workloads

  > **Key insights:**
  > - Three-tier approach: Uncompressed, Single Value, Dictionary compression, plus FSST (Fast Static Symbol Table)
  > - FSST replaces frequently occurring substrings with fixed-size 1-byte tokens; up to 256 codes (255 reserved as escape)
  > - Symbol selection: greedy, based on frequency x symbol_size compression gain; symbol table fits in L1 cache (~1ns access)
  > - Two-phase: build symbol table from sampled data, then tokenize full dataset
  > - ClickBench: 20% total data reduction, 35% string-specific; TPC-H: 40% total, ~60% string reduction
  > - Cold runs: up to 40% speedup for I/O-bound queries; hot runs: up to 2.8x slowdown for decompression-heavy queries
  > - Penalty threshold: 40% compression bonus required to justify FSST over dictionary encoding alone
  > - Combined FSST + dictionary: efficient predicate evaluation on keys while achieving better compression than dictionaries alone
  > - Compressed data treated as immutable, eliminating costly dictionary reordering
- [How ClickHouse Makes Top-N Queries Faster with Granule-Level Data Skipping](https://clickhouse.com/blog/clickhouse-top-n-queries-granule-level-data-skipping) -- Granule-level skipping for Top-N acceleration

  > **Key insights:**
  > - Granule = smallest processing unit (~8192 rows); min/max metadata from data-skipping indexes used to eliminate granules before reading
  > - Static Top-N: skip granules upfront using metadata; Dynamic Top-N: threshold filtering as execution progresses
  > - Converts Top-N into metadata-driven pruning problem: compare current Top-N threshold against granule boundaries
  > - Static gains: 5x faster (0.044s to 0.009s), 610x less data (100M rows to 164K), I/O from 1.2GB to 4.95MB
  > - Dynamic gains: 10x faster (0.325s to 0.033s), 7.7% of data read, I/O from 9.42GB to 520MB
  > - 50-billion-row tables: Top-N in under 0.2 seconds
  > - Composable with streaming execution, read-in-order, and lazy materialization
  > - Especially powerful for object storage / disaggregated compute where avoiding I/O saves network bandwidth
- [Modern OLAP Systems](https://www.ssp.sh/brain/modern-olap-systems/) -- Survey of modern analytical database architectures
- [Jack of All Trades: Query Federation in Modern OLAP (FOSDEM 2026)](https://fosdem.org/2026/schedule/event/BVYJ3S-jack-of-all-trades-starrocks/) -- StarRocks on query federation
- [Time-series and Analytical Databases (QuestDB P99)](https://questdb.com/blog/2024/10/28/time-series-analytic-database-p99-andrei/) -- Time-series database internals and query optimization
- [QuestDB: Parallel ORDER BY with High-Cardinality GROUP BY](https://github.com/questdb/questdb/pull/6582) -- Parallelized Top-N for high-cardinality aggregations

### Distributed Databases & Replication

- [ScyllaDB Ring Architecture](https://opensource.docs.scylladb.com/stable/architecture/ringarchitecture/index.html) -- Consistent hashing ring, token ranges, data distribution
- [LeasGuard: Raft Leases Done Right](https://muratbuffalo.blogspot.com/2025/12/leaseguard-raft-leases-done-right.html) -- Correctness analysis of Raft lease-based reads

  > **Key insights:**
  > - Core idea: "the log is the lease" — committing a log entry implicitly grants/extends a lease until timeout; no separate lease-management messages
  > - Lame-duck failure mode of prior schemes: a leader that can't append entries can still send lease-extend pings, deadlocking writes; LeasGuard fixes by tying lease to write progress
  > - Decouples elections from leases: followers no longer refuse election votes based on stale leader's lease — faster recovery after crash
  > - Leverages Raft's Leader Completeness property: a newly elected leader's own log tells it when the predecessor's lease expired; minimal clock-sync requirement
  > - Deferred-commit optimization: new leader accepts and replicates writes immediately, but defers committing until prior lease expires — eliminates write-queueing pause during transition
  > - Inherited lease reads: both old and new leaders can serve consistent reads during transition by checking whether query results depend on "limbo" entries
  > - Local timer with bounded drift suffices for most ops; only inherited-lease reads require synchronized clocks with known error bound
  > - TLA+ specification verified Read-Your-Writes; the inherited-lease optimization itself emerged from the formal model
  > - Pattern: making the safety invariant (write progress) drive the liveness mechanism (lease) eliminates an entire class of split-brain bugs
- [pg_crdt: CRDTs in PostgreSQL (Supabase)](https://github.com/supabase/pg_crdt/blob/master/docs/automerge.md) -- Automerge-based CRDT extension for PostgreSQL
- [Gossip, Paxos, Microservices in Go, and CRDTs at SoundCloud](https://www.infoq.com/podcasts/bourgon-paxos-go-crdts/) -- Distributed systems primitives in production
- [Why Isn't "majority" the Default Read Concern in MongoDB?](https://dev.to/franckpachot/why-isnt-majority-the-default-read-concern-in-mongodb-2782/) -- MongoDB read concern tradeoffs and consistency

### Messaging & Streaming

- [Kafka Can Be So Much More](https://ramansharma.substack.com/p/kafka-can-be-so-much-more) -- Kafka beyond messaging: event store, streaming platform
- [RabbitMQ vs Kafka vs Pulsar](https://blog.bytebytego.com/p/ep203-rabbitmq-vs-kafka-vs-pulsar) -- Architecture comparison of message brokers
- [Tansu: Kafka-compatible Broker with S3/PostgreSQL/Iceberg Backends](https://github.com/tansu-io/tansu) -- Kafka-protocol broker backed by S3, PostgreSQL, SQLite, Iceberg

### Patterns & Architecture

- [Revisiting the Outbox Pattern (Gunnar Morling)](https://www.morling.dev/blog/revisiting-the-outbox-pattern/) -- Transactional outbox for reliable event publishing

  > **Key insights:**
  > - Core purpose: atomically update local DB and notify downstream services via Kafka without distributed transactions
  > - Polling-based approach: simple but problematic — DB load spikes, poor ordering when concurrent transactions involved
  > - Log-based CDC (superior): tail DB transaction log for outbox events in commit order; propagation within "two-digit milliseconds"
  > - PostgreSQL shortcut: pg_logical_emit_message() writes events directly to WAL without materializing an outbox table
  > - Log-based CDC preserves transactional ordering that polling cannot guarantee
  > - Idempotency: track monotonically increasing sequence values (DB LSNs) rather than UUIDs to detect/discard duplicates
  > - Backfill via watermark-based snapshotting (DBLog paper): chunked processing with deduplication for existing data
  > - Debezium: open-source CDC tool for outbox implementation; Quarkus provides CDI event abstractions
  > - Outbox > 2PC: service only needs its DB online, not also the message broker; better availability
  > - Pattern "deserves a very central spot in the toolbox"; DB overhead typically insignificant with log-based implementations
- [Building a Durable Execution Engine With SQLite](https://www.morling.dev/blog/building-durable-execution-engine-with-sqlite/) -- SQLite as durable execution foundation
- [Database-Backed Workflow Orchestration (QCon SF)](https://www.infoq.com/news/2025/11/database-backed-workflow/) -- Databases as workflow orchestration layer
- [How Is Data Stored? (Making Software)](https://www.makingsoftware.com/chapters/how-is-data-stored) -- Visual explainer of on-disk storage fundamentals
- [Why JSON Isn't a Problem for Databases Anymore](https://floedb.ai/blog/why-json-isnt-a-problem-for-databases-anymore) -- Columnar approaches to semi-structured JSON data

### Surveys & References

- [Readings in Database Systems, 5th Edition (Red Book)](http://www.redbook.io/) -- Bailis, Hellerstein, Stonebraker's curated database readings
- [Databases in 2025: A Year in Review (Andy Pavlo)](https://www.cs.cmu.edu/~pavlo/blog/2026/01/2025-databases-retrospective.html) -- Annual database industry trends

  > **Key insights:**
  > - PostgreSQL is now infrastructure, not differentiator: Databricks bought Neon ($1B), Snowflake bought CrunchyData ($250M), Microsoft launched HorizonDB — every cloud vendor sells managed PG
  > - Three serious distributed-PG efforts launched in 2025: Multigres (Vitess co-creator Sugu), Neki (PlanetScale), PgDog — first credible attack on PG horizontal-scaling gap since Citus/PG-XL
  > - Model Context Protocol became universal DB feature: every major DBMS shipped MCP support so LLMs can query without custom glue; security model still immature
  > - Vector DB hype cycle peaked and declined: VC dollars rotated to LLM companies; vector search reverted to "feature inside Postgres/Mongo" rather than standalone product category
  > - Five new columnar formats launched (Vortex, F3, FastLanes, Amudai, AnyBlox) but interop is broken: 94% of existing Parquet files use only 2013-era v1 features — legacy compat dominates innovation
  > - MongoDB sued FerretDB over patent + trademark infringement of "drop-in replacement" claim — first major DB API litigation since Oracle/Java
  > - Massive M&A: DataStax → IBM ($3B), Confluent → IBM (~$11B), Informatica → Salesforce ($8B), Fivetran + dbt merger
  > - Notable deaths: Fauna, PostgresML, Hydra, Voltron Data ($110M funded) — GPU-accelerated DBs keep failing commercially despite repeated attempts
  > - Pattern: commodity CPU + great optimizer beats specialized hardware; market consolidates around PG as the lingua franca
- [Are Database Researchers Making Correct Assumptions? (Murat Demirbas)](https://muratbuffalo.blogspot.com/2026/01/are-database-system-researchers-making.html) -- Questioning OLTP benchmarking assumptions

  > **Key insights:**
  > - Interactive transactions are rarer than literature assumes: 39% of apps have none; in apps that do, only 9.6% of workload involves interactivity — validates deterministic-DB assumption
  > - Strictly interactive (require mid-flight external input/side effect) is 0.5% — deterministic systems' expressivity loss touches almost nothing real
  > - Read/write-set inferability holds for 90% of apps: ≥58% of transactions have statically determinable sets — supports deterministic locking premise
  > - The 27% of transactions querying by secondary attribute (not PK) blocks static lock prediction; mostly simple single-statement cases though
  > - Corpus bias: study covers Django + TypeORM ORMs only — heavily skewed toward web apps, excludes most enterprise systems (SAP, Oracle EBS, etc.)
  > - DBA/analyst terminal transactions ignored: ad-hoc human-initiated queries are operationally critical but absent from any ORM corpus
  > - "Convertible to one-shot with minimal code change" claim lacks empirical engineering-cost validation
  > - Title overpromises: paper is really about deterministic DB research's narrow niche; classic MVCC/2PL systems never depended on these assumptions
  > - Pattern for the reader: benchmark realism matters more than benchmark count — every workload study inherits the bias of its corpus
- [Cloudspecs: Cloud Hardware Evolution](https://muratbuffalo.blogspot.com/2026/01/cloudspecs-cloud-hardware-evolution.html) -- How cloud hardware evolution impacts database design
- [The Fastest Database You've Never Heard Of](https://www.amplifypartners.com/blog-posts/the-fastest-database-youve-never-heard-of) -- High-performance database architecture profile
- [SIGMOD 2026 Accepted Papers](https://2026.sigmod.org/sigmod_papers.shtml) -- Full SIGMOD 2026 paper list
- [FOSDEM 2026 Databases Track](https://fosdem.org/2026/schedule/track/databases/) -- FOSDEM 2026 database talks
- [TigerBeetle Intro (presentation)](https://f.00f.net/presentations/1000x/tigerbeetle-intro.pdf) -- Deterministic high-throughput financial transaction database
- [Log-Structured File Systems (Rosenblum & Ousterhout)](https://web.stanford.edu/~ouster/cgi-bin/papers/lfs.pdf) -- Seminal LFS paper from Stanford
- [Databricks Lakebase: A New Era of Databases](https://www.databricks.com/blog/what-is-a-lakebase) -- Merging data lake and database workloads
- [SQL Server 2025 General Availability](https://techcommunity.microsoft.com/blog/sqlserver/sql-server-2025-is-now-generally-available/4470570) -- SQL Server 2025 new features

---

## Programming Languages

Rust, C/C++, Go, Zig, language internals, embedded, systems programming.

### Rust

- [Rust Language Cheat Sheet](https://cheats.rs/) -- Comprehensive syntax and concept reference
- [The Algebra of Loans in Rust](https://nadrieril.github.io/blog/2025/12/21/the-algebra-of-loans-in-rust.html) -- Formal algebraic analysis of the borrow checker

  > **Key insights:**
  > - A "loan" = borrow event tied to a memory place; restrictions persist both during and after the loan's lifetime
  > - Three-phase analysis: (1) ops on the reference itself, (2) on the borrowed place while loan active, (3) after loan expires
  > - Reference types form a partial order: &T allows reborrowing to shared; &own T permits moving out; pinning restricts both
  > - Most loan types (mut, own, pinned) prevent all concurrent access; only &T and &pin T permit parallel shared borrows
  > - Uninitialization as explicit state: &own T and &uninit T treat places as uninitialized after expiry
  > - Pinning creates persistent constraints beyond lifetime: prevents moves/deallocation without running Drop
  > - &uninit T and &own T enable bidirectional conversion (initialization promotes, moving out demotes)
  > - Three composable tables predict allowed operations based on reference type + loan state — a decision procedure for borrow-checker extensions
  > - Explores speculative extensions: async pinning, non-forgettable types, in-place initialization guarantees
- [Borrow Checking, Escape Analysis, and the Generational Hypothesis](https://words.steveklabnik.com/borrow-checking-escape-analysis-and-the-generational-hypothesis) -- Borrow checker and GC theory connections
- [How Rust Does Async Differently (and Why It Matters)](https://thenewstack.io/how-rust-does-async-differently-and-why-it-matters/) -- Zero-cost async model vs goroutines/green threads
- [Rust Experimental Coroutines RFC](https://rust-lang.github.io/rfcs/2033-experimental-coroutines.html) -- Stackless coroutines/generators, foundation for async/await
- [Rust impl vs dyn](https://impl.rs/snippets/rust-impl-vs-dyn/) -- When to use static vs dynamic dispatch
- [Don't Unwrap Options: Better Ways in Rust](https://corrode.dev/blog/rust-option-handling-best-practices/) -- Idiomatic Option/Result handling patterns

  > **Key insights:**
  > - Avoid unwrap() in production: defers error handling, causes runtime panics, "one unwrap attracts another" making codebase fragile
  > - Top recommendation: let-else syntax (Rust 1.65+) — `let Some(v) = f() else { return Err(...); };` clearly highlights the happy path
  > - ok_or/ok_or_else: convert Option to Result with descriptive error messages; use ok_or_else with closures to avoid expensive operations
  > - Match expressions: explicit pattern matching on Some(value)/None works reliably for all cases
  > - Consider changing return types: if absence = error condition, return Result instead of Option to enable natural ? operator
  > - Anti-pattern: using ? on Option in Result-returning functions fails; requires explicit ok_or() conversion
  > - anyhow crate: provides .context() method for applications, but unsuitable for libraries (error type matching limitations)
  > - Distinguish semantically: Option for expected value absence, Result for error conditions
- [Effectively Using Iterators In Rust](https://hermanradtke.com/2015/06/22/effectively-using-iterators-in-rust.html) -- Practical Rust iterator patterns
- [Writing Rust the Elixir Way](https://lunatic.solutions/blog/writing-rust-the-elixir-way-1.5-years-later/) -- Lunatic runtime: Erlang-style actors in Rust with WASM isolation
- [Emitting Safer Rust with C2Rust](https://immunant.com/blog/2023/03/lifting/) -- Automated C-to-Rust translation lifting passes
- [From Rust to Beyond: The C Galaxy](https://mnt.io/2018/09/11/from-rust-to-beyond-the-c-galaxy/) -- FFI between Rust and C
- [Rust bindgen: Bindings for Non-System Libraries](https://rust-lang.github.io/rust-bindgen/non-system-libraries.html) -- Generating Rust FFI bindings for C/C++ libraries
- [qstr: Cache-Efficient Stack-Allocated String Types](https://github.com/tindzk/qstr) -- Small-string optimization with stack allocation
- [compio: Thread-per-Core Runtime with io_uring/IOCP](https://github.com/compio-rs/compio) -- Cross-platform async runtime using io_uring on Linux
- [Warper: Rust-Powered React Virtualisation](https://www.infoq.com/news/2026/02/warper-rust-react/) -- Rust/WASM for high-performance list virtualization

### Rust Embedded & Kernel

- [Coding Guidelines for Rust in the Linux Kernel](https://docs.kernel.org/rust/coding-guidelines.html) -- Official kernel Rust coding style and safety abstractions
- [Rust Embedded: The Smallest no_std Program](https://docs.rust-embedded.org/embedonomicon/smallest-no-std.html) -- Minimal bare-metal Rust binary
- [Embedded Rust: Singletons Pattern](https://docs.rust-embedded.org/book/peripherals/singletons.html) -- Rust ownership for safe peripheral access
- [RTIC: Real-Time Interrupt-driven Concurrency](https://rtic.rs/2/book/en/) -- Zero-cost concurrent embedded Rust
- [Tock OS Design](https://tockos.org/documentation/design) -- Rust-based embedded OS with capability-based security
- [FreeRTOS-rust Crate](https://github.com/lobaro/FreeRTOS-rust) -- Rust bindings for FreeRTOS
- [Microsoft LiteBox: Rust-Based Sandboxing Library OS](https://www.phoronix.com/news/Microsoft-LiteBox) -- Microsoft's Rust library OS for lightweight sandboxing

### C & C++

- [C++ Core Guidelines](https://github.com/isocpp/CppCoreGuidelines/blob/master/CppCoreGuidelines.md) -- Stroustrup and Sutter's C++ best practices
- [Modern C++ Firmware: Proven Strategies for Tiny, Critical Systems](https://johnfarrier.com/modern-cpp-firmware-part-01-case-for-modern-cpp/) -- Modern C++ in resource-constrained embedded contexts
- [11 C Language Features I Ignored at First](https://medium.com/@arslanshoukatali/11-c-language-features-i-ignored-at-first-now-i-use-them-everywhere-373a10921958) -- Designated initializers, compound literals, _Generic
- [C++ DataFrame](https://github.com/hosseinmoein/DataFrame) -- Pandas-like DataFrame in C++ with continuous memory
- [The Case for Writing Network Drivers in High-Level Languages](https://arxiv.org/pdf/1909.06344.pdf) -- Writing Linux network drivers in Rust/Go

### Go

- [Go by Example](https://gobyexample.com/) -- Hands-on Go through annotated examples
- [Go Maps in Action](https://go.dev/blog/maps) -- Official Go blog on map internals
- [Understanding Escape Analysis in Go](https://www.freecodecamp.org/news/understanding-escape-analysis-in-go/) -- Stack vs heap allocation decisions

### Zig

- [Introduction to Zig (Book)](https://pedropark99.github.io/zig-book/) -- Comprehensive free online Zig book
- [Error Payloads in Zig](https://srcreigh.ca/posts/error-payloads-in-zig/) -- Zig's error handling model
- [Zig Can Come for Rust's Performance Crown](https://medium.com/@yashbatra11111/zig-can-come-for-rusts-performance-crown-and-it-might-win-10ca15bd6b0e) -- Performance comparison between Zig and Rust

### Language Internals & Runtimes

- [Internals of CPython](https://hackmd.io/s/ByMHBMjFe) -- CPython interpreter deep dive
- [Exploring CPython's Internals](https://devguide.python.org/exploring/) -- Official Python developer guide to CPython source
- [V8 TurboFan JIT](https://v8.dev/blog/turbofan-jit) -- V8 JavaScript engine's optimizing JIT compiler
- [The Path to Mojo 1.0](https://www.modular.com/blog/the-path-to-mojo-1-0) -- Mojo ownership model, lifetime semantics, systems-level features
- [GPU Puzzles in Mojo](https://puzzles.modular.com/) -- Interactive GPU programming exercises

### Systems Programming References

- [matklad's Links Collection](https://matklad.github.io/links.html) -- Curated by the rust-analyzer author: compilers, editors, Rust internals
- [mcyoung Posts](https://mcyoung.xyz/posts) -- Compilers, linkers, systems programming
- [Linux Kernel Development, 3rd Edition (Robert Love)](https://doc.lagout.org/operating%20system%20/linux/Linux%20Kernel%20Development%2C%203rd%20Edition.pdf) -- Essential Linux kernel programming reference
- [Advanced Programming in the UNIX Environment, 3rd Edition](https://zodml.org/sites/default/files/Advanced_Programming_in_the_UNIX_Environment%2C_3rd_20Edition.pdf) -- Stevens & Rago's classic UNIX systems programming
- [System Calls (Beej's Guide)](https://beej.us/guide/bgnet/html/multi/syscalls.html) -- Network programming system call reference
- [TUM Systems Programming Course (io_uring, eBPF, networking)](https://github.com/ls1-sys-prog-course-archive-SoSe25/docs) -- Linux systems programming materials
- [TUM Advanced Systems Programming Course](https://github.com/ls1-adv-sys-prog-course/docs) -- Kernel modules, device drivers, DPDK, RDMA
- [How to Create Jump Tables via Function Pointer Arrays](https://barrgroup.com/Embedded-Systems/How-To/C-Function-Pointers) -- Function pointer dispatch for embedded systems

---

## See Also

- [Database Systems Survey](@/notes/database/database_systems.md) — In-depth coverage of many systems referenced in the bookmarks (Neon, DuckDB, ClickHouse, TigerBeetle)
- [Kafka Internals](@/notes/distributed/kafka_internals.md) — Detailed treatment of Kafka architecture bookmarked in the Case Studies section
- [io_uring Internals](@/notes/os/io_uring_internals.md) — Deep dive into io_uring referenced across multiple bookmarked articles
- [Rust Low-Level Programming](@/notes/programming/rust_low_level.md) — Unsafe Rust patterns related to the Rust bookmarks in the Programming Languages section
