+++
title = "Patroni HA"
date = 2026-05-20
[taxonomies]
tags = ["postgresql", "high-availability", "patroni", "failover", "replication", "dcs"]
+++


# Patroni: PostgreSQL High Availability Framework -- Deep Dive

## Changelog

| Date       | Section added / updated |
|------------|------------------------|
| 2026-03-26 | Initial comprehensive deep dive: Architecture, DCS Backends, Failover Mechanics, Replication, Configuration, REST API, Watchdog, Backup Integration, Kubernetes, Operational Patterns, Limitations, Comparison with Alternatives |

---

## 1. Architecture & Design Philosophy

### Core Principle: External Consensus, Not Embedded

Patroni's fundamental design decision is that PostgreSQL should **not** implement its own consensus protocol. Instead, Patroni delegates leader election and cluster state to an external **Distributed Configuration Store (DCS)** -- a system purpose-built for distributed consensus (etcd, ZooKeeper, Consul, or the Kubernetes API). This is philosophically distinct from approaches that embed Raft or Paxos directly into the database agent.

The reasoning is pragmatic: implementing a correct consensus protocol is extraordinarily difficult (as demonstrated by decades of academic work from Lamport, Ongaro/Ousterhout, etc.). By offloading this to a battle-tested external system, Patroni avoids re-inventing what etcd/ZooKeeper already solve correctly.

### Agent-Per-Node Model

Patroni runs as a **sidecar daemon** alongside each PostgreSQL instance. There is exactly one Patroni process per PostgreSQL node. The Patroni agent:

1. Manages the PostgreSQL process lifecycle (start, stop, promote, demote, restart, reload).
2. Periodically updates a **leader key** in the DCS with a TTL (time-to-live).
3. Exposes a **REST API** for health checks and administrative operations.
4. Monitors replication state (WAL position, timeline, lag).
5. Makes local decisions based on the global state stored in the DCS.

### The Leader Key -- Heart of the System

The leader key in the DCS is the single source of truth for "who is the current primary." The current leader must **renew** this key before its TTL expires. If the leader fails to renew (because the Patroni process crashed, the node is unreachable, or PostgreSQL is unhealthy), the key expires and other nodes can compete to acquire it. The node that successfully acquires the leader key via an atomic compare-and-swap (CAS) operation becomes the new leader and promotes its PostgreSQL instance to primary.

This is a **lease-based leadership** model. The TTL is typically 30 seconds by default, with a loop_wait (the interval between Patroni's main loop iterations) of 10 seconds and a retry_timeout of 10 seconds. The relationship between these is critical: the leader has `ttl` seconds to renew, and it attempts renewal every `loop_wait` seconds, so it gets roughly `ttl / loop_wait` attempts before the lease expires.

### State Machine

Each Patroni node operates as a state machine with the following major states:

- **running as leader**: PostgreSQL is primary, Patroni holds the leader key, periodic renewal.
- **running as replica**: PostgreSQL is in recovery (streaming replication or WAL replay), monitoring the leader.
- **starting**: PostgreSQL is starting up, Patroni is determining cluster state.
- **creating replica**: Running pg_basebackup or custom replica creation method.
- **stopped**: PostgreSQL is stopped (e.g., during maintenance).

The global cluster state is a function of all nodes' local states plus the DCS contents (leader key, member keys, cluster configuration, failover/switchover keys, sync standby list, history, etc.).

### Key Design Decisions and Their Implications

**No split-brain by construction**: Because the DCS provides linearizable writes with TTL-based fencing, at most one node can hold the leader key at any time. A node that cannot reach the DCS will **demote itself** (step down to replica or stop accepting writes) rather than risk split-brain.

**Pessimistic safety model**: When in doubt, Patroni shuts down the primary rather than risk two primaries. This is the correct trade-off for a database system where data integrity outweighs availability.

**PostgreSQL is the managed process**: Patroni does not patch or fork PostgreSQL. It manages a stock PostgreSQL installation through its standard interfaces (pg_ctl, SQL connections, recovery configuration, signal-based promote). This means Patroni works with any PostgreSQL version from 9.3+ (with varying feature support).

---

## 2. DCS Backends

### 2.1 etcd

**Protocol**: etcd v2 API (key-value with TTL) or etcd v3 API (leases + key-value). Patroni supports both, with v3 being strongly recommended for production.

**How Patroni uses it**: The leader key is stored at a path like `/service/{cluster_name}/leader`. Member registration is at `/service/{cluster_name}/members/{node_name}`. The cluster-wide dynamic configuration is at `/service/{cluster_name}/config`.

**Lease mechanism (v3)**: Patroni creates a lease with the configured TTL and attaches the leader key to that lease. Renewal is done via `LeaseKeepAlive`. If the Patroni process dies, the lease expires and the key is automatically deleted by etcd.

**Trade-offs**:
- Pros: Purpose-built for this use case. Raft-based consensus. Well-understood operational model. etcd is the de facto standard for Kubernetes service discovery, so many organizations already run it.
- Cons: Requires running a separate 3-node (minimum) etcd cluster, which is itself a HA system that needs monitoring and maintenance. etcd can be sensitive to disk I/O latency (Raft log fsync). Compaction and defragmentation need regular attention.
- Failure modes: If a majority of etcd nodes are lost, the etcd cluster becomes read-only (or unavailable), which triggers Patroni's DCS-unavailable behavior (see below).

### 2.2 Consul

**Protocol**: HTTP API with session-based locking and TTL.

**How Patroni uses it**: Consul sessions are created with a TTL and associated health checks. The leader key is acquired via Consul's `PUT` with `?acquire=session_id`. Consul's session invalidation (when the TTL expires or health check fails) releases the lock.

**Trade-offs**:
- Pros: Multi-datacenter support built in (though the leader lock is local to a single DC). Service mesh integration. Many organizations run Consul for service discovery already.
- Cons: Consul's consistency model for KV is `default` (not `consistent` by default), so Patroni must use `?consistent` reads to avoid stale reads. The session/lock model is less elegant than etcd leases for this use case. Consul's gossip protocol (Serf) is separate from its Raft consensus, which can cause confusion about failure detection semantics.
- Important nuance: Consul health checks can interact unexpectedly with Patroni's own health monitoring. If a Consul health check marks the node as critical, the session may be invalidated, causing an unintended leader key release.

### 2.3 ZooKeeper

**Protocol**: ZooKeeper protocol (TCP-based, using ephemeral nodes and watches).

**How Patroni uses it**: The leader key is an **ephemeral node** in ZooKeeper. When the Patroni process's ZooKeeper session expires (due to network partition or process death), ZooKeeper automatically deletes the ephemeral node. Other nodes receive a watch notification and can attempt to create the ephemeral node (leader election).

**Trade-offs**:
- Pros: Mature, battle-tested (LinkedIn, Apache Kafka historically). Ephemeral nodes are a natural fit for leader election. Session semantics are well-defined.
- Cons: JVM-based, which introduces operational complexity (JVM tuning, GC pauses). Session timeout is negotiated between client and server (typically 2x-20x the tick time), which adds a parameter to tune. ZooKeeper is increasingly being replaced by etcd in modern deployments. The ZAB protocol is less well-understood than Raft.
- Failure mode: ZooKeeper session expiration can happen due to long GC pauses on the ZooKeeper server, leading to unexpected leader key loss even when the PostgreSQL primary is perfectly healthy.

### 2.4 Kubernetes API (Endpoints/ConfigMaps)

**Protocol**: Kubernetes API server, using either Endpoints objects or ConfigMaps with resourceVersion-based optimistic concurrency control.

**How Patroni uses it**: Instead of a dedicated DCS, Patroni uses the Kubernetes API server as the coordination backend. The leader information is stored as an annotation on a Kubernetes Endpoints or ConfigMap object. Leader election uses Kubernetes' atomic update with `resourceVersion` (optimistic locking -- the update succeeds only if the resourceVersion matches, which is a CAS operation). TTL is enforced by Patroni itself (checking timestamps in annotations) rather than by the Kubernetes API.

**Trade-offs**:
- Pros: No additional infrastructure -- the Kubernetes API server is already present. Simplifies deployment in Kubernetes environments. No need to manage a separate etcd cluster (Kubernetes' own etcd is used transparently).
- Cons: The Kubernetes API server is not designed as a high-performance DCS. API server load is a concern in large clusters. Rate limiting and request throttling can delay leader renewal. The TTL enforcement is application-level (Patroni checks timestamps) rather than server-enforced (unlike etcd leases), which is slightly less robust. RBAC must be configured to grant Patroni pods access to the relevant API resources.
- Important: Kubernetes' own etcd cluster is separate from what Patroni would use if you ran etcd as a standalone DCS. When using the Kubernetes DCS backend, Patroni talks to the Kubernetes API server, which talks to Kubernetes' internal etcd.

### 2.5 DCS Outage Behavior and Fencing

This is one of the most important operational aspects of Patroni.

**When the DCS becomes unreachable**:

1. **The current leader** cannot renew its leader key. After the TTL expires, the leader key is gone (or will be gone once the DCS recovers).
2. **Patroni's behavior on the leader**: If the leader cannot reach the DCS, it must decide whether to continue serving writes or to demote itself. The default behavior is **demote** -- the primary will be demoted to a replica (or stopped) to prevent split-brain. This is the safe choice.
3. **Replicas**: Cannot see the leader key, so they know something is wrong, but they also cannot acquire the leader key (because the DCS is unreachable). No failover happens during a total DCS outage.
4. **Net effect**: During a complete DCS outage, the cluster has **no writable primary**. This is a deliberate trade-off: availability is sacrificed to prevent split-brain.

The `master_start_timeout` (now `primary_start_timeout`) parameter controls how long the primary waits before demoting itself when it cannot reach the DCS. Setting this to 0 means immediate demotion. Setting it higher gives the DCS time to recover, but increases the window during which a stale primary might accept writes while another node has been promoted (if the DCS was only partially unreachable).

**The watchdog integration** (Section 7) provides an additional fencing mechanism: even if the Patroni process is killed or hung, the hardware watchdog will reboot the machine, ensuring the old primary cannot continue serving writes.

---

## 3. Failover Mechanics

### 3.1 Automatic Failover

Automatic failover is triggered when the leader key expires in the DCS. The sequence is:

1. **Leader key expires**: The current primary's Patroni process failed to renew the key (crash, network partition, PostgreSQL health check failure, overloaded system, etc.).
2. **Replicas detect leader key absence**: Each replica's Patroni process, during its main loop iteration (every `loop_wait` seconds), checks the DCS and finds no leader key.
3. **Candidate evaluation**: Each eligible replica evaluates whether it should attempt to acquire the leader key. Eligibility depends on:
   - The node does not have `nofailover: true` tag.
   - The node's replication lag is within `maximum_lag_on_failover` (default: 1MB of WAL).
   - The node is in a healthy streaming state (or at least has a recent enough WAL position).
4. **Best candidate selection**: Patroni compares candidates based on:
   - **WAL position** (pg_last_wal_replay_lsn or pg_last_wal_receive_lsn): The replica closest to the primary's last known WAL position is preferred.
   - **Timeline**: Must be on the same or correct timeline.
   - **Priority**: The `failover_priority` tag (or legacy `candidate_priority`). Higher priority wins among replicas with equivalent WAL positions.
5. **Leader race**: The best candidate attempts an atomic CAS (compare-and-swap) on the leader key in the DCS. If multiple candidates attempt simultaneously, only one succeeds (guaranteed by the DCS's linearizable writes).
6. **Promote**: The winner runs `pg_ctl promote` (or `SELECT pg_promote()` on PG12+) on its local PostgreSQL instance.
7. **Old primary handling**: When the old primary comes back (if it was just a network partition), its Patroni process will see that another node holds the leader key. It will attempt to rejoin the cluster as a replica, potentially using `pg_rewind` to rewind to the point of divergence.

### 3.2 Manual Failover vs Switchover

**Switchover** (`patronictl switchover`): A planned, graceful leadership transfer.
1. The current leader is told to give up the leader key.
2. The leader first checkpoints, then demotes PostgreSQL (shuts down or transitions to recovery).
3. A specified candidate (or the best available) acquires the leader key and promotes.
4. The old primary restarts as a replica, connecting to the new primary.
5. Downtime is minimized because the handoff is coordinated.

**Manual failover** (`patronictl failover`): An administrator-initiated failover, typically when the current primary is already down or unhealthy. The administrator can specify the target node.

The DCS stores the failover/switchover request as a key, and the relevant Patroni nodes pick it up during their next main loop iteration.

### 3.3 pg_rewind for Rejoining Old Primaries

When the old primary comes back after a failover, its WAL timeline has diverged from the new primary. It has WAL records that the new primary does not (records generated between the last replicated position and the crash/partition). Two options exist:

**pg_rewind**: Reads the new primary's WAL (or a WAL archive) to identify which blocks were modified after the divergence point, then fetches those blocks from the new primary. This is fast -- it only copies the changed blocks, not the entire database.

Requirements for pg_rewind:
- `wal_log_hints = on` or checksums enabled (so that hint bit changes generate WAL records, allowing pg_rewind to identify all changed blocks).
- Access to the new primary (via a libpq connection or access to its data directory).
- The divergence point's WAL must be available (either on the new primary or in an archive).

**Full re-clone**: If pg_rewind fails (e.g., the divergence point WAL is no longer available, or the database is corrupted), Patroni falls back to a full pg_basebackup from the new primary. This is slow for large databases.

Patroni configuration for this:
```yaml
postgresql:
  use_pg_rewind: true
  parameters:
    wal_log_hints: "on"
```

### 3.4 Timeline Handling

PostgreSQL increments the timeline ID on every promote. Patroni tracks the cluster's timeline history in the DCS (`/service/{cluster_name}/history`). When evaluating failover candidates, Patroni ensures that the candidate is on the correct timeline. A replica that somehow diverged to a different timeline (e.g., due to a misconfigured WAL archive) will be rejected as a candidate.

After promotion, the new primary starts on a new timeline (e.g., timeline 2 if the old primary was on timeline 1). All replicas must follow the new timeline, which PostgreSQL handles automatically via timeline history files during streaming replication.

---

## 4. Replication Configuration

### 4.1 Asynchronous Replication (Default)

In the default mode, the primary streams WAL to replicas asynchronously. Transactions are committed on the primary without waiting for any replica to acknowledge receipt. This means:

- **Zero write latency overhead** from replication.
- **Potential data loss on failover**: If the primary crashes, any WAL that was written but not yet shipped to replicas is lost. Patroni mitigates this with `maximum_lag_on_failover`, ensuring that only replicas within a reasonable WAL distance of the primary can be promoted, but a non-zero gap can still exist.

### 4.2 Synchronous Replication

Patroni provides a managed synchronous replication mode that is significantly more sophisticated than raw PostgreSQL synchronous replication.

**`synchronous_mode: true`**: Patroni manages the `synchronous_standby_names` GUC dynamically. It picks the most up-to-date replica(s) and sets them as synchronous. Key behaviors:

- Patroni automatically updates `synchronous_standby_names` when replicas join or leave.
- If all synchronous replicas fail, the primary will **block writes** (because PostgreSQL's synchronous commit requires at least one sync standby to acknowledge). Patroni detects this and, if `synchronous_mode_strict` is **false** (the default when `synchronous_mode` is true), will demote the synchronous mode -- it removes `synchronous_standby_names` entirely, allowing the primary to continue accepting writes asynchronously. This prevents complete unavailability at the cost of temporary data loss risk.
- If `synchronous_mode: true`, Patroni will **only promote a synchronous standby** during automatic failover. This ensures zero data loss on failover (because the sync standby has confirmed receipt of all committed transactions). If no synchronous standby is available, no automatic failover occurs.

**`synchronous_mode_strict: true`**: The strict variant. If all synchronous replicas fail, the primary **does not** fall back to asynchronous mode. Writes will block until at least one synchronous replica returns. This guarantees that no committed transaction is ever lost (zero RPO), but at the cost of availability -- the primary effectively freezes if all sync replicas are down.

**`synchronous_node_count`**: Controls how many replicas must be synchronous. Default is 1. Setting it to N means Patroni will maintain N synchronous replicas (using PostgreSQL's `FIRST N (...)` or `ANY N (...)` syntax in `synchronous_standby_names`). Higher values increase durability guarantees but require more healthy replicas and increase commit latency.

### 4.3 Replication Slots

Patroni can manage **physical replication slots** automatically. When a replica connects, Patroni (on the primary) creates a replication slot for it. Benefits:

- The primary retains WAL segments until the replica has consumed them, preventing the "replica fell too far behind" problem.
- Avoids the need to set `wal_keep_size` (or the deprecated `wal_keep_segments`) to artificially high values.

Configuration:
```yaml
postgresql:
  use_slots: true  # default is true
```

**Risk**: If a replica is down for a long time, its replication slot will cause WAL accumulation on the primary, potentially filling the disk. Patroni mitigates this with `max_replication_slots` and by dropping slots for replicas that have been removed from the cluster.

### 4.4 Cascading Replication

Patroni supports cascading replication via the `replicatefrom` tag. A replica can be configured to stream from another replica rather than the primary:

```yaml
tags:
  replicatefrom: node2
```

This reduces the primary's connection and network overhead for large clusters. However, the cascading replica's lag is additive (its own lag plus the upstream replica's lag), and if the intermediate replica fails, the downstream replica loses its replication source. Patroni does not currently auto-reroute cascading replicas when the intermediate node fails.

---

## 5. Configuration Management

### 5.1 Bootstrap Process

When a Patroni cluster is started for the first time:

1. The **first node** to start checks the DCS for an existing cluster (leader key, member keys).
2. Finding no existing cluster, it runs `initdb` to create a new PostgreSQL data directory.
3. It then acquires the leader key in the DCS, becoming the primary.
4. It writes the cluster's dynamic configuration to the DCS.
5. **Subsequent nodes** find an existing cluster in the DCS. They do NOT run initdb. Instead, they create a replica using `pg_basebackup` (or a custom method) from the current primary.

The bootstrap section of `patroni.yml` controls the `initdb` parameters and the initial DCS-stored configuration:

```yaml
bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576  # 1MB
    postgresql:
      use_pg_rewind: true
      use_slots: true
      parameters:
        max_connections: 100
        max_wal_senders: 10
        wal_level: replica
        hot_standby: "on"
  initdb:
    - encoding: UTF8
    - data-checksums
```

**Critical point**: The `bootstrap.dcs` section is only applied on **initial cluster creation**. Once the cluster exists, the DCS-stored configuration is the authoritative source. Modifying `bootstrap.dcs` in `patroni.yml` on an existing cluster has **no effect**.

### 5.2 DCS-Stored (Dynamic) Configuration vs Local Configuration

Patroni has a two-level configuration model:

**DCS-stored (dynamic) configuration**: Stored in the DCS, shared by all nodes, and modifiable at runtime via `patronictl edit-config` or the REST API. This includes:
- `ttl`, `loop_wait`, `retry_timeout`, `maximum_lag_on_failover`
- `postgresql.parameters` (PostgreSQL GUCs applied cluster-wide)
- `postgresql.pg_hba` (pg_hba.conf entries applied cluster-wide)
- Replication settings (synchronous_mode, etc.)
- Standby cluster configuration

**Local configuration** (`patroni.yml`): Node-specific settings that cannot be shared:
- `name` (node name)
- `restapi` (listen address, port, authentication)
- `postgresql.connect_address`, `postgresql.data_dir`, `postgresql.bin_dir`
- `postgresql.listen` (local listen address)
- `etcd` / `consul` / `zookeeper` connection details
- `bootstrap` section (only used once)
- `tags` (nofailover, noloadbalance, etc.)

**Precedence**: DCS-stored parameters take precedence over local `patroni.yml` parameters for settings that can be in both places (like `postgresql.parameters`). This means you cannot override a DCS-level GUC with a local `patroni.yml` setting. To override, you must either change the DCS config or use `ALTER SYSTEM` (though ALTER SYSTEM can conflict with Patroni's management -- see Gotchas).

### 5.3 How Patroni Manages postgresql.conf and pg_hba.conf

Patroni does **not** directly edit `postgresql.conf`. Instead, it writes a file `patroni.dynamic.json` (in the data directory) and may use `ALTER SYSTEM` or custom configuration files. The exact mechanism depends on the version:

- Patroni generates a `postgresql.base.conf` and includes the original `postgresql.conf` contents.
- It writes a `pg_hba.conf` from the DCS-stored `postgresql.pg_hba` configuration.
- GUC changes that require a restart are handled by Patroni's pending restart logic (the REST API shows `pending_restart: true`, and `patronictl restart` triggers it).

### 5.4 REST API for Configuration

- `GET /config`: Returns the current DCS-stored configuration.
- `PATCH /config`: Applies partial updates to the DCS-stored configuration. This is what `patronictl edit-config` uses under the hood.
- `PUT /config`: Replaces the entire DCS-stored configuration.

Example: Changing a PostgreSQL parameter cluster-wide:
```
PATCH /config
{"postgresql": {"parameters": {"work_mem": "256MB"}}}
```

This updates the DCS, and all Patroni nodes pick up the change on their next loop iteration and reload PostgreSQL if the parameter is a runtime-reloadable GUC.

---

## 6. REST API & Monitoring

### 6.1 Endpoints Overview

Patroni exposes an HTTP REST API on each node (default port 8008). The endpoints serve dual purposes: administrative operations and health checks for load balancers.

**Health-check endpoints** (return HTTP 200 if the condition is true, 503 otherwise):

| Endpoint | 200 Condition | Typical Use |
|---|---|---|
| `/primary` or `/master` | Node is the current leader running as primary | Route read-write traffic |
| `/replica` | Node is a running replica (not the leader) | Route read-only traffic |
| `/leader` | Node holds the leader key in DCS | Same as /primary for normal clusters; differs for standby clusters |
| `/read-only` | Node is running (primary or replica) | Route read traffic to any node |
| `/read-write` | Node is the primary and can accept writes | Same as /primary in most cases |
| `/synchronous` | Node is a synchronous standby | Route reads that need strong consistency |
| `/asynchronous` | Node is an asynchronous standby | Route reads where lag is acceptable |
| `/health` | Node is running (PostgreSQL process is up) | General health check |
| `/read-only-sync` | Node is a synchronous standby or primary | Route consistent reads |

**Informational endpoints**:

| Endpoint | Returns |
|---|---|
| `GET /patroni` | Full node state JSON (role, timeline, WAL position, pending_restart, etc.) |
| `GET /cluster` | Full cluster state (all members, their roles, WAL positions, lag, DCS config) |
| `GET /config` | DCS-stored dynamic configuration |
| `GET /history` | Cluster failover history |

**Administrative endpoints**:

| Endpoint | Action |
|---|---|
| `POST /switchover` | Initiate switchover |
| `POST /failover` | Initiate failover |
| `POST /restart` | Restart PostgreSQL on this node |
| `POST /reload` | Reload Patroni configuration |
| `POST /reinitialize` | Wipe and re-clone the replica |
| `PATCH /config` | Update DCS-stored configuration |
| `PUT /config` | Replace DCS-stored configuration |

### 6.2 Load Balancer Integration

**HAProxy** is the most common load balancer used with Patroni. The pattern:

```
# In haproxy.cfg
listen postgres-primary
    bind *:5000
    option httpchk GET /primary
    http-check expect status 200
    default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
    server node1 10.0.0.1:5432 maxconn 100 check port 8008
    server node2 10.0.0.2:5432 maxconn 100 check port 8008
    server node3 10.0.0.3:5432 maxconn 100 check port 8008

listen postgres-replica
    bind *:5001
    balance roundrobin
    option httpchk GET /replica
    http-check expect status 200
    default-server inter 3s fall 3 rise 2 on-marked-down shutdown-sessions
    server node1 10.0.0.1:5432 maxconn 100 check port 8008
    server node2 10.0.0.2:5432 maxconn 100 check port 8008
    server node3 10.0.0.3:5432 maxconn 100 check port 8008
```

**Key HAProxy settings**:
- `inter 3s`: Health check interval. Must be less than Patroni's `loop_wait` to detect failovers quickly.
- `fall 3`: Mark as down after 3 failed checks. With `inter 3s`, this is 9 seconds of downtime before traffic is rerouted.
- `on-marked-down shutdown-sessions`: Immediately terminate existing connections to the old primary. This is critical for preventing writes to a demoted node.

**Alternatives to HAProxy**:
- **PgBouncer** (connection pooling, not routing) -- can sit between HAProxy and PostgreSQL.
- **vip-manager**: Manages a virtual IP that follows the primary. Simpler than HAProxy but less flexible (no read/write splitting).
- **Consul DNS / Consul Connect**: If using Consul as DCS, can route via DNS SRV records or Consul's service mesh.
- **Kubernetes Services**: In Kubernetes, Patroni can update Endpoints objects to route a Kubernetes Service to the current primary.

### 6.3 Monitoring Considerations

Patroni's REST API provides enough information to build comprehensive monitoring:

- **Replication lag**: Available in the `/cluster` endpoint, per-member.
- **Pending restart**: Flag that indicates PostgreSQL needs a restart for a GUC change to take effect.
- **Timeline**: Available per-member. A replica on a wrong timeline is a critical issue.
- **Node role**: primary, replica, standby_leader, etc.
- **DCS connectivity**: If a node cannot reach the DCS, it will report this in its state.

Common monitoring integrations: Prometheus (via patroni-exporter or direct REST API scraping), Nagios/Icinga checks, Grafana dashboards.

---

## 7. Watchdog Support

### 7.1 Why Watchdog Matters

The watchdog is a **fencing mechanism** that addresses a specific failure scenario:

1. The Patroni process on the primary dies (SIGKILL, OOM, bug) but PostgreSQL continues running.
2. The leader key TTL expires in the DCS.
3. A replica promotes to primary.
4. Now there are **two PostgreSQL primaries** -- the old one (still running, accepting writes) and the new one.

Without a watchdog, the old PostgreSQL primary will continue accepting writes until something stops it. Patroni (being dead) cannot stop it. This is a split-brain scenario.

The watchdog solution: Patroni periodically pings a Linux watchdog device (`/dev/watchdog`). If Patroni stops pinging (because it died), the watchdog triggers a **hard reboot** of the machine after a timeout, guaranteeing that the old primary is stopped.

### 7.2 Watchdog Timing

The watchdog timeout must be carefully coordinated with the leader key TTL:

- `watchdog_safety_margin`: Patroni ensures that the watchdog timeout is **less than** the leader key TTL minus some margin. This guarantees that the old primary's machine is rebooted **before** the leader key expires and a new primary can be promoted.
- Default safety margin: 5 seconds.
- Example: TTL=30s, watchdog timeout should be <= 25s. If the Patroni process dies at time T=0, the machine reboots by T=25, and the leader key expires at T=30. The new primary cannot be promoted until T=30, by which time the old machine is guaranteed to be rebooting.

### 7.3 Watchdog Modes

**`off`** (default): No watchdog integration. The split-brain window described above exists.

**`automatic`**: Patroni will try to use `/dev/watchdog` if available, but will not fail if it is not available. Good for development/testing.

**`required`**: Patroni will refuse to start as primary without a working watchdog device. This is the recommended production setting for environments where split-brain must be absolutely prevented.

### 7.4 softdog vs Hardware Watchdog

**softdog**: A Linux kernel module (`modprobe softdog`) that provides a software-based watchdog. It triggers a kernel panic and reboot. Adequate for most cases but can be defeated by a hard kernel hang (e.g., I/O deadlock preventing the panic).

**Hardware watchdog (IPMI/BMC)**: A physical watchdog timer on the server's baseboard management controller. If not reset in time, it power-cycles the machine at the hardware level. Cannot be defeated by any software failure. Preferred for critical production systems.

**Dell iDRAC, HP iLO, Supermicro IPMI** all provide hardware watchdog devices exposed as `/dev/watchdog` via kernel drivers (e.g., `ipmi_watchdog` module).

---

## 8. Backup & Restore Integration

### 8.1 Bootstrap from Backup

Patroni can create a new cluster (or a standby cluster) by restoring from a backup rather than running `initdb`. This is the `bootstrap.method` configuration:

```yaml
bootstrap:
  method: pgbackrest
  pgbackrest:
    command: pgbackrest --stanza=main --delta restore
    keep_existing_recovery_conf: true
    no_params: true
    recovery_conf:
      restore_command: pgbackrest --stanza=main archive-get %f %p
```

Supported backup tools:
- **pgBackRest**: Best-in-class for production. Supports delta restore, S3/GCS/Azure backends, parallel restore, and incremental backup.
- **WAL-E / WAL-G**: WAL-G is the actively maintained successor to WAL-E. Popular for cloud deployments (S3/GCS integration).
- **Barman**: By 2ndQuadrant/EDB. Full and incremental backup, WAL archiving.
- **Custom scripts**: Any executable that can restore a PostgreSQL data directory.

### 8.2 Creating Replicas from Backups

By default, Patroni creates replicas using `pg_basebackup` from the current primary. For large databases, this is slow and puts load on the primary. Alternative methods:

```yaml
postgresql:
  create_replica_methods:
    - pgbackrest
    - basebackup
  pgbackrest:
    command: pgbackrest --stanza=main --delta restore
    keep_data: true
    no_master: true  # does not need to connect to the primary
```

With this configuration, Patroni first tries to create a replica by restoring from a pgBackRest backup, which is fetched from the backup repository (S3, etc.) without touching the primary. If that fails, it falls back to `pg_basebackup`. The `no_master: true` flag means the method does not require a running primary, which is useful when bootstrapping a whole cluster from backups.

### 8.3 Standby Clusters

Patroni supports **standby clusters** -- an entire cluster that replicates from another Patroni cluster (or any PostgreSQL primary). The standby cluster has its own leader (the "standby_leader") which is in recovery, replicating from the remote primary. Replicas in the standby cluster replicate from the standby_leader.

```yaml
bootstrap:
  dcs:
    standby_cluster:
      host: primary-cluster-vip
      port: 5432
      primary_slot_name: standby_cluster_slot
      create_replica_methods:
        - basebackup
```

This enables multi-region DR setups where the standby cluster can be promoted to a full primary cluster if the primary region fails.

---

## 9. Kubernetes Deployment

### 9.1 Patroni on Kubernetes

Running Patroni in Kubernetes introduces several specific considerations:

**StatefulSets**: Patroni pods should be managed by a StatefulSet to get:
- Stable network identities (pod DNS names like `pg-0.pg-headless.namespace.svc.cluster.local`).
- Stable storage (PersistentVolumeClaims that survive pod restarts).
- Ordered deployment (pg-0 comes up before pg-1).

**DCS backend**: The Kubernetes DCS backend is natural here. Patroni stores leader information in Endpoints or ConfigMaps, using the Kubernetes API server as the coordination layer.

**Service routing**: Patroni can manage Kubernetes Endpoints directly. A Kubernetes Service of type ClusterIP points to the Endpoints object that Patroni updates to always point to the current primary pod. This gives applications a stable DNS name that always resolves to the primary.

### 9.2 The Spilo Image

**Spilo** is Zalando's Docker image that packages:
- PostgreSQL (multiple major versions)
- Patroni
- WAL-E / WAL-G for backup
- pg_cron, bg_mon, and other extensions
- Scripts for backup, clone, and lifecycle management

Spilo is the reference Docker image for running Patroni in Kubernetes and is used by Zalando's postgres-operator.

### 9.3 Kubernetes Operators

**Zalando's postgres-operator**: The original Patroni-based Kubernetes operator.
- Defines a `postgresql` Custom Resource Definition (CRD).
- Automatically creates StatefulSets, Services, PVCs, and ConfigMaps.
- Manages Patroni configuration, backup schedules (via CronJobs + WAL-G), and connection pooling (PgBouncer sidecar).
- Logical backup support.
- Team/database management via CRD annotations.

**Crunchy Data's PGO (PostgreSQL Operator)**: A more mature and feature-rich operator.
- Uses Patroni under the hood for HA.
- Supports pgBackRest for backup/restore (including S3, GCS, Azure).
- More sophisticated monitoring integration (pgMonitor, Prometheus).
- Supports connection pooling (PgBouncer), PostGIS, and many extensions.
- Declarative PostgreSQL management via CRDs.
- More active development and commercial support.

**CloudNativePG**: A newer operator that does NOT use Patroni. Instead, it implements its own HA logic using Kubernetes primitives. Mentioned for completeness, but it is architecturally different from Patroni-based solutions.

### 9.4 Kubernetes-Specific Challenges

- **Pod disruption budgets (PDBs)**: Essential to prevent Kubernetes from evicting the primary and a synchronous replica simultaneously during node drains.
- **Storage class selection**: `ReadWriteOnce` PVCs are standard, but the storage class's IOPS and throughput characteristics directly impact PostgreSQL performance. Network-attached storage (EBS, PD) adds latency compared to local SSDs.
- **Anti-affinity rules**: Patroni pods should be spread across failure domains (nodes, availability zones) using pod anti-affinity rules.
- **Resource limits**: PostgreSQL's `shared_buffers` and Patroni's memory usage must fit within the pod's memory limits. OOM kills are a common issue.
- **Graceful shutdown**: The pod's `terminationGracePeriodSeconds` must be long enough for PostgreSQL to complete a clean shutdown (which includes a checkpoint). For large databases with large `shared_buffers`, this can take minutes.

---

## 10. Operational Patterns

### 10.1 Planned Switchover

```
patronictl switchover --master node1 --candidate node2 --scheduled "2026-03-27T02:00:00"
```

Or immediate:
```
patronictl switchover
```

Steps:
1. Patroni verifies the candidate is healthy and caught up.
2. The primary checkpoints and demotes.
3. The candidate acquires the leader key and promotes.
4. The old primary restarts as a replica.
5. Typical downtime: 5-30 seconds depending on checkpoint duration and connection draining.

### 10.2 Maintenance Mode (Pause)

```
patronictl pause
```

When paused:
- Patroni continues to renew the leader key (keeping the current primary as primary).
- **No automatic failover** occurs. If the primary dies while paused, the cluster stays leaderless until unpaused or manual intervention.
- PostgreSQL configuration is not modified.
- Useful for: Manual PostgreSQL maintenance (major version upgrades, extension installations, manual replication changes), DCS maintenance, debugging.

Resume with:
```
patronictl resume
```

### 10.3 Reinitialize

```
patronictl reinitialize node3
```

Wipes the replica's data directory and re-creates it from the primary (via pg_basebackup or custom method). Used when a replica is corrupted or too far behind to catch up via streaming.

### 10.4 Scheduled Restarts

```
patronictl restart cluster_name --scheduled "2026-03-27T03:00:00"
```

Patroni restarts PostgreSQL on the specified node at the scheduled time. Useful for applying GUC changes that require a restart (e.g., `shared_buffers`, `max_connections`).

When restarting the primary, Patroni can optionally perform a switchover first to minimize downtime (restart the primary as a replica, then switch back).

### 10.5 Tags for Controlling Node Behavior

| Tag | Effect |
|---|---|
| `nofailover: true` | Node will never be promoted during automatic failover |
| `noloadbalance: true` | Node returns 503 from `/replica` endpoint (HAProxy removes it from read pool) |
| `clonefrom: true` | Node can be used as a source for pg_basebackup by other replicas (reduces primary load) |
| `nosync: true` | Node will never be selected as a synchronous standby |
| `replicatefrom: node_name` | Cascade replication: replicate from the named node instead of the primary |
| `failover_priority: N` | Priority for failover candidate selection (0 = never failover, higher = preferred) |

These tags are set in the local `patroni.yml` or via the REST API:
```
PATCH /config
{"tags": {"nofailover": true}}
```

### 10.6 Handling Network Partitions

Consider a 3-node cluster where node1 (primary) becomes network-partitioned from the DCS but can still reach PostgreSQL clients:

1. node1's Patroni cannot renew the leader key. The key expires.
2. node2 or node3 acquires the leader key and promotes.
3. Meanwhile, node1's PostgreSQL is still running as primary, potentially accepting writes from clients that can still reach it.
4. **With watchdog (required mode)**: node1's machine reboots before the leader key expires, ensuring no split-brain.
5. **Without watchdog**: There is a window where both node1 and the new primary accept writes. This is the most dangerous failure mode.
6. When node1 comes back (or Patroni reconnects to the DCS), Patroni sees it is no longer the leader and demotes PostgreSQL. pg_rewind is used to rejoin.

**Mitigation strategies**:
- Always use watchdog in production.
- Set `primary_start_timeout: 0` so the primary demotes immediately upon losing DCS connectivity.
- Use `pg_hba.conf` rules that require connections through the load balancer, so partitioned clients cannot reach the old primary directly.
- Use synchronous replication so that the old primary cannot commit transactions without acknowledgment from a replica (which is now promoting, so it will not acknowledge).

---

## 11. Known Limitations & Gotchas

### 11.1 DCS Latency Sensitivity

Patroni's leader renewal must complete within the TTL. If the DCS is slow (high network latency, disk I/O contention on etcd nodes, Kubernetes API server throttling), the renewal may be late, causing an unnecessary failover. This is a common production issue.

**Mitigation**: Run the DCS cluster on low-latency, dedicated infrastructure. Monitor etcd/Consul latency percentiles. Set TTL and loop_wait appropriately for your network characteristics.

### 11.2 Clock Skew

The Kubernetes DCS backend is particularly sensitive to clock skew because TTL enforcement is application-level (Patroni compares timestamps). If node clocks are significantly skewed, a node may believe the leader key has expired when it has not (or vice versa). NTP (or chrony) is essential.

etcd and ZooKeeper are less sensitive because they enforce TTLs/leases server-side, but clock skew can still cause confusing behavior in logs and monitoring.

### 11.3 Complete DCS Failure

If all DCS nodes are down simultaneously:
- The current primary **will demote** (unless `primary_start_timeout` is set very high, buying time for DCS recovery).
- No new primary can be elected.
- **The entire cluster becomes read-only (or fully unavailable).**
- This is by design -- correctness over availability. But it means your DCS is a hard dependency for writes.

**Mitigation**: Run the DCS as a HA cluster (3 or 5 nodes across availability zones). Monitor DCS health aggressively.

### 11.4 pg_rewind Failures

pg_rewind can fail when:
- The WAL from the divergence point is no longer available (archived WAL has been cleaned up, or `wal_keep_size` is too small).
- The data directory is corrupted.
- `wal_log_hints` was not enabled (so pg_rewind cannot determine which blocks were modified by hint bit writes).
- The new primary's timeline history is not available.

When pg_rewind fails, Patroni falls back to a full re-clone (pg_basebackup), which for a multi-TB database can take hours.

**Mitigation**: Always enable `wal_log_hints` or data checksums. Ensure sufficient WAL retention. Use WAL archiving so that historical WAL is always available.

### 11.5 Large Database Promote Times

`pg_ctl promote` on PostgreSQL is nearly instantaneous (it writes a promote trigger file, and the startup process handles it). However, the overall failover time includes:
- DCS leader key expiration (up to `ttl` seconds).
- Patroni loop iteration (up to `loop_wait` seconds).
- HAProxy health check detection (depends on `inter` and `fall` settings).
- Client reconnection time.

Total automatic failover time is typically **30-60 seconds** with default settings. This can be reduced by tuning TTL, loop_wait, and HAProxy settings, but shorter TTLs increase the risk of false failovers.

### 11.6 WAL Shipping Lag During Failover

Even with `maximum_lag_on_failover` set to 1MB, there is a window between the primary's last WAL flush and the point at which the replica consumed it. In asynchronous replication, this means **data loss is possible** during automatic failover. The amount of data loss is bounded by `maximum_lag_on_failover` but is typically much less (often a few KB of WAL, representing a few transactions).

With synchronous replication enabled and `synchronous_mode: true`, data loss during failover is zero (the promoted replica is guaranteed to have all committed transactions), but write latency is higher.

### 11.7 ALTER SYSTEM Conflicts

`ALTER SYSTEM` writes to `postgresql.auto.conf`, which Patroni may not be aware of. If you use `ALTER SYSTEM` to set a GUC that is also managed in Patroni's DCS configuration, the two may conflict. Patroni's DCS configuration takes precedence on reload, potentially overwriting the `ALTER SYSTEM` change.

**Best practice**: Manage all PostgreSQL GUCs through Patroni's DCS configuration (`patronictl edit-config` or `PATCH /config`), not through `ALTER SYSTEM`.

### 11.8 Connection Storm After Failover

When the primary changes, all client connections to the old primary are broken. Applications will attempt to reconnect simultaneously, creating a connection storm on the new primary. With connection pooling (PgBouncer), this is mitigated. Without it, the new primary may struggle under the sudden connection load.

---

## 12. Comparison with Alternatives

### 12.1 Patroni vs repmgr

**repmgr** (by 2ndQuadrant/EDB) is a replication management tool that predates Patroni.

| Aspect | Patroni | repmgr |
|---|---|---|
| **Consensus mechanism** | External DCS (etcd, Consul, ZK, K8s) | None -- repmgr uses a voting protocol among repmgr daemons, with a "witness" node to prevent split-brain |
| **Split-brain prevention** | Strong (DCS linearizability + TTL + optional watchdog) | Weaker (relies on SSH fencing, or external fencing scripts; voting can fail in asymmetric partitions) |
| **Configuration** | DCS-stored dynamic config, REST API | Local config files, repmgr.conf, managed via `repmgr` CLI |
| **Failover automation** | Built-in, always-on | Requires `repmgrd` daemon; auto-failover can be enabled/disabled |
| **Load balancer integration** | REST API with HTTP health checks (HAProxy, etc.) | No built-in HTTP endpoints; relies on VIP or external scripts |
| **Kubernetes support** | Native (Kubernetes DCS backend, operators) | Not designed for Kubernetes |
| **PostgreSQL management** | Full lifecycle (initdb, promote, demote, restart, pg_rewind) | Primarily replication topology management; lifecycle management is less complete |
| **Community activity** | Very active (Zalando, many contributors, wide adoption) | Less active since EDB acquisition; still maintained but fewer new features |

**Verdict**: Patroni is the clear winner for new deployments. repmgr is found in older installations and in environments where EDB is the vendor.

### 12.2 Patroni vs Stolon

**Stolon** (by Sorint.lab) is another PostgreSQL HA manager that uses an external DCS.

| Aspect | Patroni | Stolon |
|---|---|---|
| **Architecture** | Agent-per-node (single Patroni process per PostgreSQL) | Three components: sentinel (monitors), keeper (manages PostgreSQL), proxy (routes connections) |
| **DCS** | etcd, Consul, ZooKeeper, Kubernetes | etcd, Consul, Kubernetes |
| **Proxy** | External (HAProxy, etc.) | Built-in proxy component |
| **Configuration** | YAML + DCS-stored, REST API | stolonctl CLI, JSON-based cluster spec |
| **Maturity** | Older, more battle-tested, wider adoption | Less widely adopted; development has slowed significantly |
| **Kubernetes** | Multiple operators (Zalando, Crunchy) | Helm charts available, no dedicated operator |
| **Community** | Larger community, more contributors | Smaller community |

**Verdict**: Patroni has won the adoption battle. Stolon's multi-component architecture is more complex to operate, and its development has slowed. New deployments should choose Patroni.

### 12.3 Patroni vs pg_auto_failover (Citus/Microsoft)

**pg_auto_failover** (originally by Citus Data, now Microsoft) takes a different architectural approach.

| Aspect | Patroni | pg_auto_failover |
|---|---|---|
| **Consensus** | External DCS | No external DCS -- uses a "monitor" node (a PostgreSQL instance running the pg_auto_failover extension) |
| **Architecture** | DCS + Patroni agent per node | Monitor node (single PostgreSQL instance) + pg_autoctl agent per node |
| **Split-brain prevention** | DCS linearizability + TTL + watchdog | Monitor node is the arbiter; if the monitor is down, no failover occurs |
| **Monitor HA** | DCS is a HA cluster (3+ nodes) | Monitor is a single point of failure (can be made HA with its own replication, but adds complexity) |
| **Setup complexity** | Higher (need DCS cluster) | Lower (just need one monitor + your PostgreSQL nodes) |
| **Feature richness** | Very rich (synchronous mode management, cascading replication, standby clusters, tags, REST API, watchdog) | More focused; fewer operational controls |
| **Kubernetes** | Excellent (multiple operators) | Limited Kubernetes support |
| **Multi-node clusters** | Supports many replicas, cascading, complex topologies | Primarily designed for 2-node (primary + secondary) setups, though multi-node support has been added |

**Verdict**: pg_auto_failover is simpler to set up for small deployments (especially 2-node). Patroni is more capable for complex, multi-node production deployments. pg_auto_failover's single-monitor architecture is a design trade-off that simplifies operations but limits scalability and robustness compared to a proper DCS.

### 12.4 Patroni vs Cloud-Managed HA

Cloud providers offer managed PostgreSQL with built-in HA:
- **AWS RDS / Aurora**: Multi-AZ failover, ~30s for RDS, faster for Aurora (shared storage model).
- **Google Cloud SQL**: Regional HA with automatic failover.
- **Azure Database for PostgreSQL**: Zone-redundant HA.

| Aspect | Patroni (self-managed) | Cloud-Managed HA |
|---|---|---|
| **Operational burden** | You manage everything: DCS, Patroni, PostgreSQL, monitoring, backups | Fully managed |
| **Customization** | Full control over PostgreSQL version, extensions, configuration, replication topology | Limited to what the cloud provider offers |
| **Cost** | Lower compute cost (no managed service premium), higher ops cost | Higher compute cost, lower ops cost |
| **Failover time** | 30-60s typical (tunable) | 30-120s typical (not tunable) |
| **Data loss** | Configurable (async = possible loss, sync = zero loss) | Usually async (some data loss possible); Aurora has synchronous shared storage |
| **PostgreSQL version** | Any version you want | Limited to versions the provider supports (often lagging by months/years) |
| **Extensions** | Any extension | Limited set (no custom C extensions on most platforms) |
| **Multi-region** | Patroni standby clusters | Limited or expensive (Aurora Global Database, Cloud SQL cross-region replicas) |

**Verdict**: Cloud-managed HA is the right choice when you want minimal operational burden and can live with the provider's limitations. Patroni is the right choice when you need full control, specific PostgreSQL versions/extensions, multi-cloud/hybrid deployments, or when the managed service's limitations are unacceptable for your use case.

---

## 13. Key Papers & Resources

### Academic & Foundational

- **Ongaro & Ousterhout, "In Search of an Understandable Consensus Algorithm (Extended Version)", USENIX ATC 2014** -- The Raft paper. Understanding Raft is essential for understanding how etcd (and thus Patroni's DCS) works.
- **Lamport, "The Part-Time Parliament", ACM TOCS 1998** -- Paxos. Foundational for all consensus-based systems.
- **Chandra & Toueg, "Unreliable Failure Detectors for Reliable Distributed Systems", JACM 1996** -- The theory of failure detection that underpins why DCS-based leader election has the properties it does.
- **Gray & Reuter, "Transaction Processing: Concepts and Techniques", Morgan Kaufmann 1993** -- Chapter on replication and high availability provides the theoretical foundation.

### Patroni-Specific

- **Patroni official documentation**: https://patroni.readthedocs.io/ -- The primary reference.
- **Alexander Kukushkin (Zalando), "Patroni: PostgreSQL High Availability Made Easy"** -- Numerous conference talks (PGConf EU, PGConf US) explaining the design decisions.
- **Zalando's blog posts on Patroni**: Operational lessons from running Patroni at scale (thousands of PostgreSQL clusters).
- **"PostgreSQL HA with Patroni" by Zalando, GitHub**: https://github.com/patroni/patroni -- Source code and issue tracker.

### Related Systems

- **etcd design documentation**: https://etcd.io/docs/ -- Understanding etcd's lease mechanism is critical for understanding Patroni's leader election.
- **PostgreSQL documentation on Streaming Replication**: https://www.postgresql.org/docs/current/warm-standby.html -- The replication features that Patroni builds upon.
- **PostgreSQL documentation on pg_rewind**: https://www.postgresql.org/docs/current/app-pgrewind.html -- Essential for understanding how old primaries rejoin.

### Industry Practices

- **Crunchy Data's PGO documentation**: https://access.crunchydata.com/documentation/postgres-operator/ -- How a production-grade Kubernetes operator integrates Patroni.
- **GitLab's PostgreSQL HA architecture**: GitLab uses Patroni + Consul for their PostgreSQL HA, documented in their public handbook. A real-world case study of Patroni at scale.
- **Zalando's postgres-operator**: https://github.com/zalando/postgres-operator -- Reference Kubernetes operator for Patroni.

---

## 14. Summary Decision Framework

| Requirement | Recommended Approach |
|---|---|
| Small deployment (2-3 nodes), minimal ops | pg_auto_failover or cloud-managed |
| Medium deployment, full control needed | Patroni + etcd (3 nodes) + HAProxy |
| Kubernetes-native | Patroni + Kubernetes DCS + Crunchy PGO or Zalando operator |
| Zero data loss (RPO=0) | Patroni + synchronous_mode + synchronous_mode_strict |
| Fastest failover | Patroni + short TTL (15s) + watchdog + HAProxy with aggressive health checks |
| Multi-region DR | Patroni standby clusters |
| Existing Consul infrastructure | Patroni + Consul DCS backend |
| Maximum simplicity, no external dependencies | pg_auto_failover (but accept single-monitor limitation) |

---

## 15. Future Considerations

- **Patroni 4.x**: Ongoing development includes improved integration with PostgreSQL's built-in features as they evolve (e.g., native logical replication failover in PG17+).
- **Distributed PostgreSQL (Citus, YugabyteDB, CockroachDB)**: For use cases requiring horizontal scaling, the HA model shifts from primary-replica failover to distributed consensus among all nodes. Patroni is not relevant for these systems.
- **PostgreSQL built-in HA**: There have been recurring discussions in the PostgreSQL community about building HA features directly into PostgreSQL (e.g., integrated Raft). If this ever materializes, it could reduce the need for external tools like Patroni, but this is unlikely in the near term.
- **CloudNativePG**: The Kubernetes operator that implements HA without Patroni (using Kubernetes primitives directly) is gaining adoption and represents an alternative architectural philosophy for Kubernetes-only deployments.
