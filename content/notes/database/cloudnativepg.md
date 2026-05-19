+++
title = "Cloudnativepg"
date = 2026-05-20
[taxonomies]
tags = ["postgresql", "kubernetes", "cnpg", "operator", "high-availability"]
+++


# CloudNativePG (CNPG) - Expert-Level Technical Deep Dive

## Changelog

| Date       | Section added / updated |
|------------|------------------------|
| 2026-03-26 | Initial comprehensive deep dive: Architecture, Operator Implementation, Instance Manager, CRDs, Lifecycle Management, Storage, HA, Backup & Recovery, Networking, Security, Monitoring, Design Decisions, CNPG-I Plugin Interface, Distributed Topology |

---

## 1. Architecture Overview

### 1.1 High-Level Design Philosophy

CloudNativePG is a Kubernetes operator for PostgreSQL that follows a radically different design philosophy from older PostgreSQL-on-Kubernetes solutions (Crunchy PGO, Zalando's postgres-operator with Patroni/Spilo, Stolon). Its three fundamental architectural principles are:

1. **No external failover management tools** -- no Patroni, no repmgr, no Stolon. The operator directly extends the Kubernetes controller and relies on the Kubernetes API server to hold the status of a PostgreSQL cluster.

2. **No StatefulSets** -- the operator implements its own custom pod controller, managing Pods and PVCs directly.

3. **Instance manager as PID 1** -- instead of the sidecar pattern, each PostgreSQL container runs a custom Go binary (`/controller/manager`) as PID 1, which in turn manages the `postmaster` process.

These three choices are deeply interrelated and represent a coherent design where the operator has maximum control over the PostgreSQL lifecycle, free from the constraints and abstractions of StatefulSet and external HA tools.

### 1.2 Shared-Nothing Architecture

CNPG mandates a shared-nothing deployment model:

- Each PostgreSQL instance runs in its own Pod with its own dedicated PVCs
- No shared storage between instances (no NFS, no shared block volumes)
- Replication uses PostgreSQL's native WAL shipping and streaming replication -- application-level replication, not storage-level replication
- Instances should reside on different Kubernetes worker nodes, ideally across different availability zones
- Storage-level replication (Ceph replicas, Longhorn replicas) is explicitly discouraged because PostgreSQL already handles replication; doubling it causes unnecessary write amplification

### 1.3 Primary-Standby Topology

Within a single Kubernetes cluster, CNPG manages:

- Exactly one primary instance (read-write)
- Zero or more hot standby replicas (read-only, via streaming replication)
- The number of replicas = `.spec.instances - 1`
- Automatic service updates during failover to redirect traffic seamlessly

### 1.4 Operator Deployment Model

The operator itself runs as a standard Kubernetes Deployment (typically a single replica, or multiple for HA of the operator process). It:

- Watches all Cluster, Backup, ScheduledBackup, Pooler, and related resources across configured namespaces
- Runs reconciliation loops using `controller-runtime` (the standard Go framework underlying kubebuilder)
- Exposes webhook endpoints on port 9443 (validating and mutating admission webhooks)
- Exposes Prometheus metrics on port 8080
- Communicates with instance managers via port 8000 on each Pod (TLS-secured, operator-authenticated)

---

## 2. Custom Resource Definitions (CRDs)

CNPG defines its CRDs under the API group `postgresql.cnpg.io/v1`:

### 2.1 Cluster

The primary CRD. Declaratively defines an entire PostgreSQL cluster:

- `.spec.instances` -- number of PostgreSQL instances (primary + replicas)
- `.spec.imageName` / `.spec.imageCatalogRef` -- container image with PostgreSQL
- `.spec.storage` -- PGDATA PVC specification
- `.spec.walStorage` -- optional separate WAL PVC
- `.spec.tablespaces` -- declarative tablespace volumes
- `.spec.postgresql` -- PostgreSQL configuration parameters, pg_hba.conf, synchronous replication
- `.spec.bootstrap` -- how to initialize the cluster (initdb, recovery from backup, pg_basebackup)
- `.spec.backup` -- backup configuration (volume snapshots, object store via plugins)
- `.spec.replica` -- replica cluster configuration for distributed topologies
- `.spec.monitoring` -- Prometheus metrics configuration
- `.spec.resources` -- CPU/memory requests and limits
- `.spec.affinity` -- node affinity, pod anti-affinity, topology constraints
- `.spec.plugins` -- CNPG-I plugin declarations

### 2.2 Backup

A one-shot request for a physical backup:

- `.spec.cluster` -- reference to the target Cluster
- `.spec.method` -- backup method (plugin-based or volumeSnapshot)
- `.spec.target` -- prefer standby (default) or primary
- Status tracks start/stop times, backup size, WAL position, success/failure

### 2.3 ScheduledBackup

Cron-scheduled backups:

- `.spec.schedule` -- six-field cron expression (includes seconds field, unlike Unix crontab)
- `.spec.cluster` -- target Cluster reference
- `.spec.method` -- backup method
- `.spec.immediate` -- take one immediately on creation
- `.spec.suspend` -- temporarily disable scheduling

### 2.4 Pooler

Connection pooling via PgBouncer:

- `.spec.cluster` -- target Cluster
- `.spec.type` -- `rw` (routes to primary) or `ro` (routes to replicas)
- `.spec.instances` -- number of PgBouncer pods
- `.spec.pgbouncer` -- PgBouncer configuration (poolMode, parameters)
- `.spec.template` -- full PodSpec customization (container must be named `pgbouncer`)

### 2.5 ClusterImageCatalog / ImageCatalog

Catalogs mapping PostgreSQL major versions to container images, enabling simplified version management and rolling upgrades.

---

## 3. Operator Implementation Details

### 3.1 Technology Stack

- **Language**: Go
- **Framework**: `controller-runtime` (the library underlying kubebuilder, but CNPG does not use kubebuilder scaffolding directly for all controllers)
- **CRD generation**: Standard controller-gen markers in Go types under `api/v1/`
- **Build**: Makefile + GoReleaser + Docker Bake (multi-platform builds)
- **CI**: GitHub Actions with golangci-lint, gosec, govulncheck, CodeQL, Snyk, Dockle
- **Image signing**: cosign with short-lived OIDC tokens, SBOM (SPDX), SLSA provenance attestations

### 3.2 Repository Structure

```
cloudnative-pg/
  api/v1/          -- CRD type definitions (Cluster, Backup, ScheduledBackup, Pooler, etc.)
  cmd/             -- Entry points (operator binary, instance manager binary -- same binary, different modes)
  internal/        -- Private packages: reconciliation logic, controllers, management
  pkg/             -- Public packages: specs, utilities, PgBouncer helpers, metrics
  config/          -- Kubernetes manifests, CRD YAMLs, RBAC, webhooks
  docs/            -- Documentation source (Markdown, built into the documentation site)
  tests/           -- Integration and E2E test suites
  hack/            -- Build scripts, development utilities
```

The operator and instance manager are compiled into the **same Go binary**. The binary's behavior is determined by how it is invoked:
- As the operator controller manager (running in the operator Deployment)
- As the instance manager (running as PID 1 inside each PostgreSQL Pod)

### 3.3 Reconciliation Architecture

The operator uses `controller-runtime`'s standard reconciliation pattern:

**Cluster Controller** -- the most complex reconciler. On each reconciliation:

1. Reads the current Cluster spec and status from the API server
2. Reads the current state of Pods, PVCs, Services, Secrets, ConfigMaps
3. Compares desired state (spec) with actual state (live resources)
4. Takes corrective actions:
   - Creates missing Pods (new instances)
   - Creates missing PVCs (storage provisioning)
   - Updates Services to point to the correct primary
   - Triggers rolling updates when image or configuration changes
   - Initiates failover when the primary is unhealthy
   - Initiates switchover when requested
   - Manages fencing annotations
   - Updates Cluster status with current topology, replication state, phase

**Backup Controller** -- watches Backup resources, coordinates with the instance manager (or CNPG-I plugin sidecar) to execute physical backups.

**ScheduledBackup Controller** -- evaluates cron schedules and creates Backup resources at the appropriate times.

**Pooler Controller** -- manages PgBouncer Deployments, Services, Secrets, and ConfigMaps for each Pooler resource.

The reconciliation queue uses `client-go/util/workqueue` with deduplication (same item not processed concurrently) and configurable `MaxConcurrentReconciles`.

### 3.4 Webhooks

CNPG implements both:

- **Validating admission webhooks** -- reject invalid Cluster specs (e.g., instances < 1, invalid storage config, conflicting options)
- **Mutating admission webhooks** -- set defaults, inject annotations, normalize configurations

Webhooks run on port 9443 with mandatory TLS, served by the operator Deployment.

---

## 4. The Instance Manager (The Signature Design Choice)

### 4.1 Why Not Sidecars?

Traditional operators (Crunchy PGO, Zalando) use sidecar containers alongside the PostgreSQL container to handle health checks, WAL archiving, metrics export, and failover coordination. CNPG rejected this pattern because:

- **Sidecar lifecycle is decoupled from the main container** -- a sidecar can restart independently, creating coordination challenges
- **Signal handling is fragmented** -- the Pod's PID 1 (typically `tini` or `dumb-init`) does not understand PostgreSQL's shutdown semantics
- **Resource overhead** -- multiple containers per Pod means more memory, more CPU, more complexity
- **Tight coupling needed** -- PostgreSQL lifecycle events (promotion, demotion, shutdown, WAL archiving) need to be atomically coordinated with Kubernetes state updates

### 4.2 PID 1 Behavior

The instance manager binary runs as PID 1 inside the PostgreSQL container. It:

1. **Starts PostgreSQL** -- launches the `postmaster` process as a child
2. **Manages the full lifecycle** -- handles initialization (initdb, pg_basebackup, recovery), configuration updates, role changes (primary <-> standby), and shutdown
3. **Handles signals** -- when kubelet sends SIGTERM (pod deletion, node drain), the instance manager executes a multi-stage graceful shutdown:
   - **Stage 1 (Smart Shutdown)**: Issues a CHECKPOINT, then requests smart shutdown (no new connections). Duration: up to `.spec.smartShutdownTimeout` (default 180s)
   - **Stage 2 (Fast Shutdown)**: If PostgreSQL is still up, requests fast shutdown (terminates existing connections)
   - **Stage 3 (WAL completion)**: Waits for WAL archiving/streaming to complete up to remaining `.spec.stopDelay` time, then forcibly terminates
4. **Serves health probes** -- implements the HTTP endpoints for liveness, readiness, and startup probes
5. **Exports Prometheus metrics** -- runs the metrics exporter on port 9187
6. **Manages certificates** -- watches TLS certificate secrets and reloads PostgreSQL when they change
7. **Coordinates with the operator** -- exposes a REST API on port 8000 for operator-to-instance communication (status reporting, backup triggering, fencing)
8. **Handles WAL archiving** -- coordinates with CNPG-I plugin sidecars (or the deprecated native Barman Cloud integration)

### 4.3 Probe Architecture

**Startup Probe**:
- Default mechanism: `pg_isready`
- Controlled by `.spec.startDelay` (default 3600s -- generous for large databases)
- `failureThreshold` auto-calculated as `startDelay / periodSeconds`
- Supports strategies: `pg_isready`, `query` (run an actual SQL query), `streaming` (verify replication lag)
- `maximumLag` option: replication lag threshold in bytes -- standby not considered ready until caught up

**Liveness Probe**:
- Ensures the instance manager and PostgreSQL are operating correctly
- Default timeout: 30 seconds total (3 failures x 10s period)
- **Primary Isolation Detection** (v1.27+): Reports failure when BOTH conditions hold:
  1. Cannot reach the Kubernetes API server
  2. Cannot reach any other instance via REST API
- This prevents split-brain: an isolated primary that cannot communicate with anything gets killed by its own liveness probe, triggering failover to a replica that can still communicate

**Readiness Probe**:
- Activates after startup probe succeeds
- Verifies PostgreSQL can accept connections
- Used by Services to route traffic -- a Pod removed from Service endpoints cannot receive application traffic
- Supports same strategies and `maximumLag` as startup probe

### 4.4 Switchover Shutdown Behavior

During a controlled switchover (as opposed to Pod deletion), the instance manager of the former primary:
1. Issues a CHECKPOINT
2. Initiates a **fast** shutdown (not smart -- switchover must be fast)
3. Waits up to `.spec.switchoverDelay` (default 3600s) for WAL archival to complete
4. The new primary is promoted only after the old primary's WAL receiver stops

### 4.5 Independence from the Operator

A critical resilience property: **the instance manager operates independently of the operator**. If the operator Pod dies or is being upgraded:

- PostgreSQL instances continue running normally
- WAL archiving continues
- Metrics export continues
- Liveness/readiness probes continue
- The only thing paused is reconciliation (no new Pods created, no failovers initiated, no rolling updates)

The operator is a control plane component; the instance manager is a data plane component. This separation ensures that operator downtime does not cause PostgreSQL downtime.

---

## 5. Why No StatefulSets?

This is one of CNPG's most controversial and most important design decisions. The rationale:

### 5.1 PVC Resizing

StatefulSet cannot resize PVCs. For a database, this is a showstopper -- you will inevitably need to expand storage. CNPG manages PVCs directly and can trigger online resizing (if the StorageClass supports it) or offline resizing (delete Pod, resize PVC, recreate Pod).

### 5.2 Role-Aware Update Ordering

StatefulSet updates Pods in reverse ordinal order (highest to lowest). It has no concept of PostgreSQL roles. CNPG's custom controller understands that:

- Replicas must be updated before the primary
- The primary must undergo a switchover (not just restart) to minimize downtime
- Rolling updates can use different strategies for different Pods

### 5.3 PVC Coherence

A PostgreSQL instance may have multiple PVCs (PGDATA + WAL + tablespaces). StatefulSet has no concept of PVC groups. If one PVC in a group becomes orphaned or corrupted, StatefulSet would blindly reattach it. CNPG classifies orphaned PVCs as unusable rather than silently reusing them, preventing data corruption.

### 5.4 Node Maintenance Flexibility

When a node goes down, CNPG supports three recovery strategies:
- **Clone to new PVCs** on a different node (fast recovery, requires re-syncing data)
- **Remount existing PVCs** on a different node (works with network-attached storage)
- **Wait for node recovery** (optimal for large databases with local SSDs where re-cloning would take hours)

StatefulSet offers only one behavior: wait for the Pod to be rescheduled, which may or may not work depending on storage topology.

### 5.5 Direct PVC Lifecycle Management

CNPG creates PVCs independently of Pods, using the configured StorageClass for dynamic provisioning. This means:
- PVCs survive Pod deletion (intentional -- data persists across instance restarts)
- PVCs can be resized independently
- PVC creation can be decoupled from Pod scheduling
- The operator can inspect PVC state and make intelligent decisions about reuse vs. recreation

---

## 6. Lifecycle Management

### 6.1 Cluster Bootstrapping

Three bootstrap methods:

**initdb** -- fresh cluster initialization:
- Runs `initdb` with configurable options (encoding, locale, data checksums)
- Creates the application database and user
- Applies initial PostgreSQL configuration
- Creates replicas via `pg_basebackup` from the new primary

**recovery** -- restore from backup:
- Bootstrap from a physical backup (object store via CNPG-I plugin or volume snapshot)
- Applies WAL replay for PITR to a target time, LSN, XID, or "immediate"
- The first instance becomes the new primary; replicas are cloned from it
- **Recovery is never in-place** -- it always creates a new Cluster resource

**pg_basebackup** -- clone from an existing PostgreSQL instance:
- Creates the primary via `pg_basebackup` from an external PostgreSQL server
- Useful for migrating from non-CNPG PostgreSQL installations

### 6.2 Rolling Updates (Minor Versions, Config Changes)

Triggered by changes to: `imageName`, extension images, image catalog entries, PostgreSQL parameters requiring restart, resource requests/limits, or operator upgrades.

**Sequence**:
1. Operator detects the drift between desired and actual Pod specs
2. Replicas are updated one at a time, starting from the highest serial number
3. Each replica Pod is deleted, then recreated with the new spec -- same PVCs, new container image
4. Identity is preserved (same PVC, same PostgreSQL data directory, same replication slot)
5. After all replicas are updated, the primary is handled based on `primaryUpdateStrategy`:
   - **`unsupervised` (default)**: Automatic -- switchover to the most aligned replica (which is now running the new version), then update the old primary
   - **`supervised`**: Pauses after replica updates; operator waits for manual `kubectl cnpg promote` or `kubectl cnpg restart`
6. Two methods for primary update:
   - **Restart method** (default): In-place pod restart
   - **Switchover method**: Promotes a replica first, then updates the old primary -- zero-downtime for writes (brief pause during switchover)

### 6.3 Major Version Upgrades

Three strategies:

**Offline In-Place with pg_upgrade (v1.26+)**:
- Triggered by changing `.spec.imageName` to a higher major version
- The operator shuts down all pods
- Runs a `pg_upgrade --link` job (hard links, fast)
- Replaces original data directories with upgraded versions
- Destroys replica PVCs and re-clones all replicas from the upgraded primary
- Limitations: same OS distribution required, extensions must be compatible, cluster offline during the entire process
- Previous image info stored in `.status.pgDataImageInfo` for potential rollback

**Blue/Green with Logical Replication (Online)**:
- Create a new Cluster with the target major version
- Set up PostgreSQL native logical replication from old to new
- Switchover at the application level when caught up
- Zero downtime but requires careful handling of DDL, sequences, large objects

**Blue/Green with Dump/Restore (Offline)**:
- pg_dump from old cluster, pg_restore into new cluster
- Simplest approach, longest downtime for large databases

### 6.4 Operator Upgrades

When the CNPG operator itself is upgraded:
- If the new operator version requires a different instance manager binary, rolling updates of all managed Pods are triggered
- **In-place instance manager updates** (configurable): if enabled, the operator can update the instance manager binary inside running Pods without restarting them, avoiding a full rolling update

---

## 7. Storage

### 7.1 PGDATA PVC

Every PostgreSQL instance gets one PVC for PGDATA. Configuration via:

- **Simple mode**: `.spec.storage.storageClass` + `.spec.storage.size`
- **Template mode**: `.spec.storage.pvcTemplate` with full PVC spec (access modes, volume mode, storage class, etc.)

The operator creates PVCs directly (not via StatefulSet volumeClaimTemplates). PVCs are named deterministically based on the instance serial number.

### 7.2 WAL Volume Separation

Optional but recommended for production. Configured via `.spec.walStorage`:

- Puts `pg_wal` on a dedicated PVC, separate from PGDATA
- Enables parallel I/O (sequential WAL writes + random data file I/O on separate devices)
- Prevents PGDATA disk exhaustion from blocking WAL writes
- Allows independent sizing and monitoring
- **Irreversible**: once added, WAL separation cannot be removed from a running cluster

### 7.3 Tablespace Volumes

Declarative tablespace support via `.spec.tablespaces`:

- Each tablespace gets a dedicated PVC
- Managed as part of the PVC group for each instance
- Enables tiered storage (fast SSD for hot tables, slower storage for archive tables)

### 7.4 Volume Expansion

- **Online expansion**: If StorageClass supports `allowVolumeExpansion`, just update the `.spec.storage.size` -- the operator patches all PVCs
- **Offline expansion**: For non-expandable classes, delete Pod+PVC (replicas first, then primary with switchover), operator recreates with new size
- **WAL volume expansion**: Same process, but both PGDATA and WAL PVCs may need handling

### 7.5 Volume Snapshots

For backup and recovery:

- Configured via `.spec.backup.volumeSnapshot`
- Requires CSI driver supporting `VolumeSnapshot`
- Separate `VolumeSnapshotClass` can be specified for PGDATA and WAL volumes via `walClassName`
- Snapshots are taken from standbys by default (minimize primary I/O impact)
- Copy-on-write snapshots provide near-instant recovery

### 7.6 Disk Exhaustion Handling

The instance manager detects when there is insufficient space to store the next WAL segment. Instead of allowing PostgreSQL to crash (which would trigger a failover), it proactively avoids the situation. Recovery: expand the PVC and update the Cluster resource.

---

## 8. High Availability

### 8.1 Streaming Replication

- CNPG creates a `streaming_replica` user with `REPLICATION` privilege immediately after cluster initialization
- Replication connections use TLS client certificate authentication (mutual TLS)
- When continuous backup is configured, replicas also use `restore_command` as a WAL fallback mechanism
- Replicas connect directly to the primary's Pod IP (not through Services)

### 8.2 Synchronous Replication

Two configuration approaches:

**Modern (recommended)**:
- `.spec.postgresql.synchronous.method`: `any` (quorum-based) or `first` (priority-based)
- `.spec.postgresql.synchronous.number`: count of synchronous standbys required for commit
- Operator auto-populates `synchronous_standby_names`
- Supports `standbyNamesPre` / `standbyNamesPost` for including external standbys

**Legacy (deprecated)**:
- `minSyncReplicas` / `maxSyncReplicas` -- auto-calculated quorum

**Data durability modes**:
- **Required** (default): Writes block if insufficient synchronous standbys. RPO=0 guaranteed, but reduced availability during disruptions.
- **Preferred**: Adjusts required synchronous count based on available standbys. Self-healing but risks data loss if all standbys fail.

### 8.3 Replication Slots

CNPG manages replication slots automatically:

**High Availability Slots**:
- Enabled by default (`.spec.replicationSlots.highAvailability.enabled: true`)
- Primary creates HA slots for each standby (prefixed `_cnpg_` by default)
- Standbys advance their own HA slots using `pg_replication_slot_advance()`
- This ensures WAL files are retained even after failover -- the new primary already has slots for all standbys
- Update interval: 30 seconds (configurable)

**User-Defined Slot Synchronization**:
- Custom replication slots created via SQL are synchronized to standbys
- `excludePatterns` allows regex-based filtering
- Logical decoding slot synchronization available (`synchronizeLogicalDecoding: true`)
- PostgreSQL 17+: operator manages `synchronized_standby_slots` natively
- PostgreSQL 16 and earlier: `pg_failover_slots` extension required

### 8.4 Failover

**Detection**: The reconciliation loop detects primary failure when the primary Pod's readiness probe fails. Failover begins after `.spec.failoverDelay` (default: 0 seconds -- immediate).

**Two-Phase Process**:
1. **Shutdown Phase**: `TargetPrimary` is set to pending. The primary Pod is forced to shut down, which stops WAL receivers on replicas (prevents timeline fork).
   - Fast shutdown with `.spec.switchoverDelay` timeout for WAL archival
   - If fast shutdown fails, immediate shutdown
2. **Promotion Phase**: Leader election selects the best replica. The selected replica is promoted. The former primary restarts, detects it is no longer primary, and becomes a replica.

**Replica Selection**: The operator selects the most up-to-date replica (based on received/replayed LSN). With quorum-based failover, it ensures the promoted replica has confirmed all synchronous commits.

**Quorum-Based Failover**: Uses the Dynamo `R + W > N` consistency model:
- R = number of promotable replicas (read quorum)
- W = write quorum (replicas acknowledging synchronous commits)
- N = total potentially synchronous replicas
- Failover proceeds only if `R + W > N`, guaranteeing at least one promotable replica has all committed data

**Post-Failover Recovery**: The former primary uses `pg_rewind` to synchronize with the new primary's timeline. This avoids a full re-clone, making recovery fast.

### 8.5 Switchover (Planned)

A controlled, zero-data-loss primary change:
1. Operator selects the most aligned replica (or a specified target)
2. Former primary issues CHECKPOINT, then fast shutdown
3. WAL receiver on the target replica catches up
4. Target replica is promoted
5. Former primary restarts as a standby
6. Services are updated to point to the new primary

### 8.6 Fencing

Fencing isolates instances by shutting down PostgreSQL while keeping the Pod running:

- Set annotation `cnpg.io/fencedInstances` to a JSON list of instance names, or `["*"]` for all
- Or use `kubectl cnpg fencing on/off`
- Fenced instances: PostgreSQL is stopped, Pod remains running but not Ready, configuration and certificate updates continue, only `cnpg_collector_fencing_on` metric is collected
- **Fencing a primary does NOT trigger automatic failover** -- this is intentional for debugging. The primary restarts when fencing is lifted.
- Useful for: debugging crashlooping instances, manual maintenance, investigating data issues

### 8.7 Primary Isolation Detection (v1.27+)

The liveness probe on the primary reports failure when BOTH conditions hold:
1. The instance manager cannot reach the Kubernetes API server
2. The instance manager cannot reach any other instance via REST API

This is a split-brain prevention mechanism. An isolated primary (network partitioned from both the API server and all replicas) will fail its liveness probe, get killed by kubelet, and the replicas (which can still communicate) will elect a new primary. Configurable via `.spec.probes.liveness.isolationCheck.enabled` (default: true).

---

## 9. Backup & Recovery

### 9.1 Architecture: Moving to CNPG-I Plugins

Starting with v1.26, backup/recovery is being progressively moved from native implementations to the CNPG-I plugin interface. The **Barman Cloud Plugin** (`barman-cloud.cloudnative-pg.io`) is the official community plugin.

The native Barman Cloud integration (built into the operator) is deprecated as of v1.26 in favor of the plugin-based approach.

### 9.2 Object Store Backups (via Barman Cloud Plugin)

- Physical base backups stored as tarballs in S3, GCS, Azure Blob, or any S3-compatible object store
- WAL archiving to the same object store is **mandatory** for object store backups
- Hot backups only (online, no downtime)
- Supports PITR to any target: time, LSN, XID, named restore point, or "immediate"
- Retention policies managed by the plugin
- No incremental/differential backup support (full backups only)
- Backups prefer standbys by default (operator selects the most synchronized standby)

### 9.3 Volume Snapshot Backups

- Leverages Kubernetes CSI VolumeSnapshot API
- Configured via `.spec.backup.volumeSnapshot`
- Can snapshot PGDATA and WAL volumes separately (with different VolumeSnapshotClasses)
- WAL archiving optional but recommended (needed for PITR)
- Supports both hot and cold backups
- Incremental/differential possible (depends on storage driver's copy-on-write capabilities)
- Near-instant recovery from snapshots
- No retention policy management (handled outside CNPG)

### 9.4 Scheduled Backups

ScheduledBackup CRD with six-field cron expressions (includes seconds):
- `"0 0 0 * * *"` = daily at midnight
- `immediate: true` = take one backup right now in addition to the schedule
- `suspend: true` = temporarily disable
- Creates Backup resources on schedule; the Backup controller handles execution

### 9.5 Recovery (Bootstrap)

Recovery always creates a **new** Cluster -- never in-place modification:

- Bootstrap a new Cluster from a Backup resource, object store, or volume snapshot
- PITR: specify `recoveryTarget` with `targetTime`, `targetLSN`, `targetXID`, `targetName`, or `targetImmediate`
- First instance recovers to the target, becomes the new primary
- Replicas are cloned from the recovered primary
- WAL archive is mandatory for PITR (volume snapshots alone give you the snapshot point, not arbitrary PITR)

### 9.6 WAL Archiving

- Foundation of continuous backup
- Coordinated by the instance manager, executed by CNPG-I plugin sidecars
- Archives every completed WAL segment to the object store
- Enables PITR from any base backup to any point with available WAL
- Without a base backup, WAL archive alone is useless for recovery

---

## 10. Networking & Services

### 10.1 Kubernetes Services

CNPG automatically creates and manages three Services per Cluster:

- **`<cluster>-rw`**: Points to the primary (read-write). Updated immediately during failover/switchover.
- **`<cluster>-ro`**: Points to standby replicas (read-only). Load balances across all healthy replicas.
- **`<cluster>-r`**: Points to all instances (any read -- primary + replicas).

Service selectors use labels managed by the operator. During failover, the operator updates labels on Pods so that the `-rw` Service immediately points to the new primary.

### 10.2 Connection Pooling (PgBouncer via Pooler CRD)

The Pooler CRD creates a managed PgBouncer deployment:

**Architecture**:
- Deployed as a Kubernetes Deployment (not managed by the Cluster controller)
- Each Pooler creates its own Service (named after the Pooler)
- Applications connect to the Pooler Service instead of the Cluster Service
- PgBouncer requires v1.19+ (for `auth_dbname` feature)

**Types**:
- `rw`: Routes through to the `<cluster>-rw` Service (primary)
- `ro`: Routes through to the `<cluster>-ro` Service (replicas)

**Authentication**:
- Operator creates a `cnpg_pooler_pgbouncer` user in PostgreSQL
- Creates `public.user_search(text)` function with `SECURITY DEFINER` for `auth_query`
- Issues TLS client certificates for the pooler authentication user
- PgBouncer authenticates to PostgreSQL via TLS client certificates

**TLS**:
- Fully integrated with CNPG's certificate infrastructure
- TLS on both client side (application -> PgBouncer) and server side (PgBouncer -> PostgreSQL)
- Reuses cluster CA certificates by default
- Custom certificates supported

**Configuration**:
- ~70 PgBouncer parameters exposed via `.spec.pgbouncer.parameters`
- Pool modes: session, transaction, statement
- Connection limits: `max_client_conn`, `default_pool_size`, `min_pool_size`, `reserve_pool_size`
- Operator does NOT validate parameter values -- user responsibility

**Scaling & HA**:
- Multiple PgBouncer pods distribute connections
- Pod anti-affinity rules for cross-node distribution
- `paused: true` invokes PgBouncer PAUSE command (graceful connection draining)

**Lifecycle**:
- Independent of Cluster lifecycle (deleting a Cluster does not delete its Poolers, and vice versa)
- Multiple Poolers can serve one Cluster (e.g., one per application)
- Operator upgrades trigger rolling updates of Pooler pods

**Monitoring**:
- PgBouncer metrics on port 9127 with `cnpg_pgbouncer_` prefix
- Exposes SHOW LISTS, SHOW POOLS, SHOW STATS data as Prometheus metrics

---

## 11. Distributed Topology (Replica Clusters)

### 11.1 Concept

A replica cluster is a separate Cluster resource that continuously replicates from another PostgreSQL source. Two use cases:

1. **Distributed Topology** (DR/HA across Kubernetes clusters): Multiple Cluster resources across different K8s clusters, one primary, rest are replicas. Supports controlled switchover.
2. **Standalone Replica Clusters** (read-only workloads): One-way replication, no switchover capability, promotion is irreversible.

### 11.2 Replication Methods for Cross-Cluster

- **Streaming Replication**: Direct TCP connection between clusters via `pg_basebackup` for bootstrap, then continuous WAL receiver
- **WAL Archive**: WAL files written to object store by primary, retrieved by replica via `restore_command`
- **Hybrid**: Both methods active; PostgreSQL dynamically switches between them

### 11.3 Controlled Switchover (Distributed Topology)

A two-step, manual process:

**Step 1 -- Demotion**:
- Change `.spec.replica.primary` on the primary cluster to point to the new primary
- Operator archives the WAL file containing the shutdown checkpoint as a `.partial` file
- Generates a `demotionToken` (base64-encoded JSON from `pg_controldata`) in cluster status
- Retrieve via `kubectl get cluster <name> -o jsonpath='{.status.demotionToken}'`

**Step 2 -- Promotion**:
- On the replica cluster, set `.spec.replica.primary` to itself and `.spec.replica.promotionToken` to the demotion token
- CloudNativePG waits for the replica to replay all WAL up to the specified LSN
- Upon reaching target LSN, switches timelines, archives history file
- Former primary begins replicating from the new primary

**Critical**: `primary` and `promotionToken` must be set simultaneously. Omitting `promotionToken` triggers an uncontrolled failover instead of a controlled switchover.

### 11.4 Cross-Cluster Failover Limitation

CloudNativePG **cannot perform cross-cluster automated failover**. This is a deliberate design decision -- cross-cluster failover requires human judgment about network partitions, data consistency, and application routing. The operator provides the primitives (demotion/promotion tokens, WAL synchronization) but leaves the decision to operators.

### 11.5 Delayed Replicas

`.spec.replica.minApplyDelay` maps to PostgreSQL's `recovery_min_apply_delay`. Use cases: protection against accidental DDL, time-travel queries, compliance buffers. Cannot be combined with `promotionToken`.

---

## 12. Security Model

### 12.1 TLS Certificate Management

**Auto-provisioned by default**:
- Operator generates a self-signed CA per cluster
- Issues server certificates for PostgreSQL instances
- Issues client certificates for the `streaming_replica` user (replication) and `cnpg_pooler_pgbouncer` user (connection pooling)
- Certificates stored in Kubernetes Secrets
- Instance manager watches secrets and triggers PostgreSQL configuration reload on certificate rotation
- TLS v1.3 required by default for streaming replication

**Bring Your Own Certificates**:
- Supply your own CA and server certificates via Secrets
- Integration with cert-manager supported

### 12.2 RBAC

**Operator RBAC** (`cnpg-manager` ServiceAccount):
- ClusterRole for reading Nodes and ClusterImageCatalog objects
- All other permissions can be namespace-scoped
- Watches and manages Pods, PVCs, Services, Secrets, ConfigMaps, Jobs in the target namespace(s)

**Instance Manager RBAC** (per-cluster ServiceAccount, named after the cluster):
- Can read only secrets related to its own cluster (streaming replication, app user, superuser, LDAP, CA certs, server cert, backup credentials, custom monitoring queries)
- Can update its own Cluster status
- Can manage Backup resources in its namespace

### 12.3 Pod Security

- Containers run as non-root `postgres` user
- Read-only root filesystem (no writable layer)
- `allowPrivilegeEscalation: false`
- All Linux capabilities dropped
- No privileged mode required
- Seccomp profile: `RuntimeDefault` by default
- Customizable via `.spec.podSecurityContext` and `.spec.securityContext`

### 12.4 User Management

- Passwords auto-generated and stored in Secrets
- PostgreSQL 14+: `scram-sha-256` password encryption (earlier: md5)
- `enableSuperuserAccess` disabled by default (postgres password set to NULL)
- Application user gets full ownership of the application database

### 12.5 Network Security

- Port 8000: Operator-to-instance communication (TLS + operator authentication)
- Port 5432: PostgreSQL (optional TLS + password auth)
- Port 9187: Instance metrics (optional TLS)
- Port 9443: Operator webhook server (TLS required)
- Network policies must permit operator access to port 8000

---

## 13. Monitoring

### 13.1 Instance Metrics (Port 9187)

Each PostgreSQL Pod exposes Prometheus metrics via the instance manager's built-in exporter.

**Built-in metrics** (`cnpg_collector_*` prefix):
- WAL file counts and disk usage
- Archive status (.ready/.done file counts)
- Synchronous replica configuration
- Node distribution
- Backup timestamps and recovery point info
- Replica mode and fencing status
- PostgreSQL version information

**Custom metrics**: Defined via ConfigMap or Secret, referenced in `.spec.monitoring.customQueriesConfigMap` / `customQueriesSecret`:
- SQL queries executed atomically with `pg_monitor` role
- `application_name` set to `cnpg_metrics_exporter`
- Support metric types: COUNTER, GAUGE, HISTOGRAM, DURATION, LABEL, MAPPEDMETRIC, DISCARD
- Database targeting: specific databases, multiple databases, or auto-discovery via shell patterns
- Version constraints via `runonserver` semantic versioning
- Primary-only execution flag
- 30-second output cache (configurable)
- Auto-reloading via `cnpg.io/reload` label on ConfigMaps/Secrets

**Default monitoring ConfigMap**: `cnpg-default-monitoring` installed by the operator. Can be disabled per-cluster (`.spec.monitoring.disableDefaultQueries: true`) or globally via operator configuration.

### 13.2 Operator Metrics (Port 8080)

The operator exposes kubebuilder/controller-runtime standard metrics:
- Reconciliation counts, durations, errors
- Work queue depth and latency
- Go runtime metrics
- Webhook request counts

### 13.3 PodMonitor Integration

Manual PodMonitor creation is now recommended (the `.spec.monitoring.enablePodMonitor` field is deprecated):

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PodMonitor
spec:
  selector:
    matchLabels:
      cnpg.io/cluster: <cluster-name>
  podMetricsEndpoints:
  - port: metrics
```

For TLS-enabled metrics, the PodMonitor must reference the cluster CA secret and use `serverName` matching `<cluster-name>-rw`.

---

## 14. CNPG-I Plugin Interface

### 14.1 Architecture

CNPG-I is a gRPC-based protocol that defines a standardized interface between the operator and external plugins. It enables extending CloudNativePG without modifying the core codebase.

### 14.2 Deployment Modes

**Sidecar Container**:
- Plugin exposes gRPC server via Unix domain socket
- Socket placed in shared directory at `PLUGIN_SOCKET_DIR` (default: `/plugin`)
- Lifecycle coupled to the operator Pod
- Simpler setup via shared `emptyDir` volume

**Standalone Deployment (recommended)**:
- Plugin runs as independent Kubernetes Deployment
- Exposes TCP gRPC endpoint behind a Service
- Decoupled lifecycle, independent scaling
- Requires mTLS (cert-manager recommended)
- Service must have label `cnpg.io/plugin: <plugin-name>` for discovery
- Annotation `cnpg.io/pluginPort: <port>` specifies the gRPC endpoint

Plugins are discovered **at operator startup only** (requires operator restart to pick up new plugins).

### 14.3 Capability Categories

Plugins can extend eight areas:
1. WAL Management (archiving, restore)
2. Backup and Recovery (physical base backups)
3. Logging and Auditing
4. Metrics Export
5. Authentication and Authorization
6. Extension Management
7. Instance Lifecycle Management
8. Configuration Management

### 14.4 Lifecycle Hooks

Plugins can hook into key points in a cluster's lifecycle:
- Pre/Post reconciliation hooks
- Backup execution
- WAL archive/restore commands
- Recovery orchestration
- Sub-resource reconciliation

### 14.5 The Barman Cloud Plugin

The official community plugin for object store integration:
- Runs as a sidecar container in PostgreSQL Pods (for WAL archiving and backup execution)
- Uses `barman-cloud-backup` for physical base backups
- Uses `barman-cloud-wal-archive` / `barman-cloud-wal-restore` for WAL management
- Supports S3, GCS, Azure Blob Storage
- Manages retention policies
- Replaces the deprecated native Barman Cloud integration

---

## 15. Key Design Decisions Summary

### Why Instance Manager Instead of Sidecar?

| Aspect | Sidecar Pattern | Instance Manager (PID 1) |
|--------|----------------|--------------------------|
| Signal handling | Fragmented across containers | Unified, PostgreSQL-aware |
| Lifecycle coupling | Loose -- sidecar can restart independently | Tight -- manager IS the container |
| Resource overhead | Extra container per concern | Single binary handles all concerns |
| Coordination | Requires IPC between containers | Direct function calls |
| Shutdown semantics | Generic SIGTERM to each container | Multi-stage PostgreSQL-aware shutdown |
| Resilience | Depends on sidecar orchestration | Independent of operator |

### Why No Patroni/Stolon?

- Patroni requires a DCS (Distributed Consensus Store) -- etcd, ZooKeeper, or Consul -- adding operational complexity
- Kubernetes already IS a distributed system with an etcd-backed API server
- CNPG uses the K8s API server directly as its source of truth for cluster state
- Eliminating the DCS dependency removes a failure domain and simplifies the architecture
- The operator's reconciliation loop + instance manager replaces the Patroni agent

### Why No StatefulSet?

See Section 5 above. In summary: PVC resizing, role-aware updates, PVC group coherence, and flexible node maintenance strategies all require a custom pod controller.

---

## 16. Comparison with Other Operators

| Feature | CloudNativePG | Crunchy PGO | Zalando postgres-operator |
|---------|--------------|-------------|--------------------------|
| HA mechanism | Native K8s reconciliation + instance manager | Patroni | Patroni |
| DCS requirement | None (K8s API server) | etcd (via Patroni) | etcd (via Patroni) |
| Pod management | Custom controller (direct Pods) | StatefulSet | StatefulSet |
| Sidecar pattern | No (instance manager as PID 1) | Yes | Yes |
| Connection pooling | PgBouncer (Pooler CRD) | PgBouncer (built-in) | Connection pooler operator |
| Backup tool | Barman Cloud (CNPG-I plugin) | pgBackRest | WAL-G / logical backup |
| Volume snapshots | Native support | Via pgBackRest | Limited |
| Major version upgrade | pg_upgrade in-place (v1.26+) | pg_upgrade via PGUpgrade CRD | Not natively supported |
| Cross-cluster DR | Replica clusters with controlled switchover | Distributed PostgreSQL via PGO | Not natively supported |
| Plugin architecture | CNPG-I (gRPC-based) | None | None |
| CNCF status | Sandbox project | None | None |

---

## 17. Sources

- [CloudNativePG Official Documentation](https://cloudnative-pg.io/docs/)
- [CloudNativePG FAQ](https://cloudnative-pg.io/docs/devel/faq)
- [CloudNativePG Architecture](https://cloudnative-pg.io/docs/devel/architecture)
- [Instance Manager Documentation](https://cloudnative-pg.io/docs/devel/instance_manager)
- [Custom Pod Controller](https://cloudnative-pg.io/docs/1.28/controller/)
- [Storage Documentation](https://cloudnative-pg.io/docs/1.28/storage/)
- [Replication Documentation](https://cloudnative-pg.io/docs/1.28/replication/)
- [Security Documentation](https://cloudnative-pg.io/docs/1.28/security/)
- [Monitoring Documentation](https://cloudnative-pg.io/docs/1.28/monitoring/)
- [Backup Documentation](https://cloudnative-pg.io/docs/1.28/backup/)
- [Rolling Updates](https://cloudnative-pg.io/docs/1.28/rolling_update/)
- [Failover Documentation](https://cloudnative-pg.io/docs/1.28/failover/)
- [Fencing Documentation](https://cloudnative-pg.io/docs/1.28/fencing/)
- [Connection Pooling](https://cloudnative-pg.io/docs/1.28/connection_pooling/)
- [PostgreSQL Upgrades](https://cloudnative-pg.io/docs/1.28/postgres_upgrades/)
- [Replica Clusters](https://cloudnative-pg.io/docs/1.28/replica_cluster/)
- [CNPG-I Plugin Interface](https://cloudnative-pg.io/docs/1.25/cnpg_i/)
- [CNPG-I GitHub Repository](https://github.com/cloudnative-pg/cnpg-i)
- [Barman Cloud Plugin Concepts](https://cloudnative-pg.io/plugin-barman-cloud/docs/next/concepts/)
- [CloudNativePG GitHub Repository](https://github.com/cloudnative-pg/cloudnative-pg)
- [Offline In-Place Major Upgrades Blog (EDB)](https://www.enterprisedb.com/blog/offline-place-major-upgrades-cloudnativepg)
- [Replication Slots Management Blog (EDB)](https://www.enterprisedb.com/blog/how-cloudnativepg-manages-replication-slots)
- [CNPG Recipe 17 - Major Upgrades (Gabriele Bartolini)](https://www.gabrielebartolini.it/articles/2025/04/cnpg-recipe-17-postgresql-in-place-major-upgrades/)
- [Comparing K8s Operators: CloudNativePG (Palark Blog)](https://blog.palark.com/cloudnativepg-and-other-kubernetes-operators-for-postgresql/)
