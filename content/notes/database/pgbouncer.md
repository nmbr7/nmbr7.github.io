+++
title = "Pgbouncer"
date = 2026-05-20
[taxonomies]
tags = ["postgresql", "connection-pooling", "pgbouncer", "performance"]
+++


# PgBouncer: Connection Pooling for PostgreSQL -- Deep Dive

## Changelog

| Date       | Section added / updated |
|------------|------------------------|
| 2026-03-26 | Initial comprehensive write-up: architecture, pooling modes, connection lifecycle, configuration, performance, monitoring, deployment patterns, limitations, recent developments |

---

## 1. Architecture & Internals

### 1.1 Why Connection Pooling Matters

PostgreSQL forks a new OS process for every client connection. Each backend process consumes roughly 5-10 MB of resident memory (stack, shared buffer pointers, local catalog caches, work_mem allocations). At 500 connections that is 2.5-5 GB of memory purely for process overhead, even if those connections are idle. Worse, the process-per-connection model means that context switching overhead grows with connection count, and shared buffer contention (via LWLocks, especially ProcArrayLock and buffer partition locks) degrades throughput once you exceed a few hundred active backends. The PostgreSQL community has measured that peak throughput often occurs at 2-4x the number of CPU cores, and declines after that.

PgBouncer sits between clients and PostgreSQL, maintaining a much smaller pool of actual server connections that are shared across a larger number of client connections. A deployment might accept 10,000 client connections through PgBouncer but only ever open 100 connections to PostgreSQL.

### 1.2 Single-Threaded Event Loop (libevent)

PgBouncer is written in C and is single-threaded by design (until very recent multi-threading efforts; see Section 9). It uses **libevent** (specifically libevent2 in modern builds) as its event notification framework, which abstracts over OS-specific mechanisms:

- **epoll** on Linux
- **kqueue** on macOS/FreeBSD
- **select/poll** as fallback

The event loop is the heart of PgBouncer. It runs a tight loop that:

1. Calls `event_base_loop()` (or equivalent) to wait for I/O readiness on all registered file descriptors (client sockets, server sockets, the listening socket, DNS resolution sockets, admin console socket, pipes for signal handling).
2. When a file descriptor becomes readable or writable, the corresponding callback fires.
3. The callback reads/writes data, advances the state machine for that connection, and returns control to the event loop.

Because it is single-threaded, there is **zero lock contention** within PgBouncer itself. This is the source of its extreme efficiency -- there are no mutexes, no atomic operations on hot paths, no thread synchronization overhead. The trade-off is that PgBouncer can only use one CPU core. For most workloads, a single core running an event loop can handle tens of thousands of connections because the work per event is minimal (just copying packets between sockets).

### 1.3 Wire Protocol Handling

PgBouncer implements a substantial subset of the PostgreSQL v3 wire protocol (the protocol used since PostgreSQL 7.4). It does **not** implement the full protocol -- it only needs to understand enough to:

1. **Parse startup messages**: SSLRequest, GSSEncRequest, StartupMessage (with protocol version, user, database, options).
2. **Handle authentication exchanges**: Cleartext, MD5, SCRAM-SHA-256 (added later), certificate auth.
3. **Identify transaction boundaries**: It must detect when a transaction starts and ends to know when a server connection can be reassigned (in transaction pooling mode). It does this by tracking:
   - `ReadyForQuery` messages from the server, which include a transaction status byte: `I` (idle), `T` (in transaction), `E` (in failed transaction).
   - `Query` and `Parse` messages from the client (simple and extended query protocol).
4. **Forward packets opaquely**: For the most part, PgBouncer does not interpret the contents of Query, Parse, Bind, Execute, Describe, or data row messages. It forwards them as-is between client and server sockets, acting as a transparent proxy.

The key insight is that PgBouncer operates at the **packet level**, not the SQL level. It does not parse SQL. It reads the 1-byte message type tag and the 4-byte length prefix of each protocol message, and forwards the entire message. The only messages it deeply inspects are authentication-related messages, `ReadyForQuery` (for transaction boundary detection), and `ParameterStatus` messages (to track GUC settings like `client_encoding`, `DateStyle`, `TimeZone`, etc. that need to be replayed when reassigning connections).

### 1.4 Memory Model

PgBouncer is extremely memory-efficient:

- **Per client connection**: Approximately 2 KB of state (the `PgSocket` structure, read/write buffers). The packet buffer (`pkt_buf`) size is configurable (default 4096 bytes) and is the primary per-connection memory cost. For 10,000 client connections, that is roughly 40 MB of buffer memory.
- **Per server connection**: Similar overhead to client connections.
- **Slab allocator**: PgBouncer uses a custom slab allocator for `PgSocket` structures and related objects to avoid malloc fragmentation and keep allocation fast.
- **sbuf (SBuf)**: The streaming buffer abstraction. Each connection has an `SBuf` that manages non-blocking I/O. The sbuf implements scatter-gather-style buffering where data is read from one socket into a buffer and then written to the paired socket. When the buffer fills, back-pressure is applied by not reading from the source socket (removing it from the readable set in the event loop) until the destination socket drains.
- **pkt_buf**: Controls the maximum packet size that PgBouncer can handle in a single read. If a PostgreSQL protocol message exceeds `pkt_buf` size, PgBouncer can still handle it by streaming it in chunks, but certain messages that must be parsed in their entirety (like startup/auth) need to fit in the buffer.

### 1.5 Connection Multiplexing

The core multiplexing works as follows:

- Client connects to PgBouncer. PgBouncer accepts the TCP connection and creates a `PgSocket` in the `CL_LOGIN` state.
- After authentication, the client socket moves to `CL_ACTIVE` (if a server connection is available) or `CL_WAITING` (if none is available, and the client must wait in a queue).
- When a server connection is assigned, the client's `SBuf` and the server's `SBuf` are **linked**: data arriving on the client socket is forwarded to the server socket, and vice versa. This is the "fast path" -- PgBouncer is essentially doing `splice()` semantics at the userspace level.
- When the transaction completes (in transaction mode) or the client disconnects (in session mode), the server connection is **unlinked** from the client and returned to the pool.

### 1.6 DNS Resolution

PgBouncer performs asynchronous DNS resolution. It supports:
- Built-in DNS using `getaddrinfo_a` or its own resolver (c-ares integration in newer versions).
- `dns_max_ttl` controls caching duration.
- This matters for cloud environments where database endpoints may resolve to different IPs over time (e.g., RDS failover).

---

## 2. Pooling Modes

PgBouncer supports three pooling modes, configured per-database or globally via `pool_mode`.

### 2.1 Session Pooling (`pool_mode = session`)

**How it works**: A server connection is assigned to a client when the client connects and is released only when the client disconnects. This is the least aggressive pooling mode.

**What it provides**: Connection reuse between sequential client sessions. If client A disconnects and client B connects, client B can reuse the server connection that client A was using, avoiding the overhead of establishing a new backend process.

**What works**: Everything. Since the client has exclusive use of a server connection for its entire session, all PostgreSQL features work:
- Prepared statements (named and unnamed)
- SET / SET LOCAL for GUC variables
- LISTEN / NOTIFY
- Advisory locks (session-level)
- Temporary tables
- Cursors (WITH HOLD and without)
- DECLARE ... CURSOR
- Sequences with currval() state
- Connection-level state in extensions

**What breaks**: Nothing, from a feature perspective.

**Trade-offs**:
- Pros: Maximum compatibility. Zero risk of state leakage between sessions.
- Cons: Minimal pooling benefit. If clients hold connections open for long periods (as connection pools in application servers typically do), the pool provides little reduction in server connections. You get the N:N mapping where N clients need N server connections.
- Best suited for: Applications that cannot be modified to work with transaction pooling, or workloads where clients connect, do work, and disconnect quickly (short-lived connections like PHP-FPM, serverless functions).

### 2.2 Transaction Pooling (`pool_mode = transaction`)

**How it works**: A server connection is assigned to a client only for the duration of a single transaction. Between transactions (when the server sends `ReadyForQuery` with status `I`), the server connection is returned to the pool and can be used by another client. For autocommit queries (no explicit BEGIN), the connection is assigned for the duration of that single query.

**What it provides**: Dramatic reduction in required server connections. If 1,000 clients each spend 5% of their time in a transaction, you only need ~50 server connections instead of 1,000.

**What breaks in transaction mode -- the complete list**:

| Feature | Why it breaks | Workaround |
|---------|---------------|------------|
| **Prepared statements** (pre-1.21) | PREPARE creates server-side state on a specific backend. Next transaction may go to a different backend. | Use protocol-level prepared statements with `PQprepare()` instead of SQL PREPARE. Or use PgBouncer 1.21+ with `track_extra_parameters`. See Section 8. |
| **SET / SET LOCAL** | SET changes persist for the session on the server. Next transaction on a different server will have different settings. SET LOCAL is fine (scoped to transaction). | Use SET LOCAL within transactions. Or use PgBouncer's `server_reset_query` (default: DISCARD ALL) to clean up, but this adds overhead. |
| **LISTEN / NOTIFY** | LISTEN registers interest on a specific backend. If the client is moved to a different backend, it misses notifications. NOTIFY works but is fragile. | Do not use LISTEN through PgBouncer in transaction mode. Use a dedicated non-pooled connection for LISTEN. |
| **Advisory locks (session-level)** | `pg_advisory_lock()` is held on a backend. If the client moves, the lock is orphaned (held until that backend's session ends, which may be a long time). | Use `pg_advisory_xact_lock()` (transaction-scoped advisory locks) instead. |
| **Temporary tables** | Created on a backend, persist for the session. Next transaction may go to a different backend where the temp table does not exist. | Create and drop temp tables within the same transaction, or use `ON COMMIT DROP`. |
| **Cursors (WITH HOLD)** | A WITH HOLD cursor persists after the transaction that created it. It exists on a specific backend. | Only use cursors within a single transaction. |
| **DEALLOCATE / DISCARD** | These reference session-level state on a specific backend. | Avoid, or use `server_reset_query = DISCARD ALL`. |
| **Sequences (currval/lastval)** | `currval()` depends on `nextval()` having been called in the same session/backend. | Call `nextval()` and `currval()` in the same transaction. |
| **Large objects** | Large object descriptors are session-scoped. | Open and close large objects within the same transaction. |
| **SET ROLE / SET SESSION AUTHORIZATION** | Session-level identity changes. | Avoid or scope to transaction. |

**How PgBouncer handles the transition**: When the server sends `ReadyForQuery` with `I` status, PgBouncer:
1. Optionally runs `server_reset_query` (default: `DISCARD ALL`) on the server connection to clean up any session state. This adds a round-trip. Setting it to empty string skips this, improving performance but risking state leakage.
2. Returns the server connection to the pool's free list.
3. If there are clients waiting in the queue, the server connection is immediately assigned to the next waiting client.

**Trade-offs**:
- Pros: Massive connection reduction. The most commonly used mode in production.
- Cons: Application must be aware of the constraints. Session-level state is unreliable.
- Best suited for: Most web applications, microservices, and serverless workloads that use simple request-response patterns.

### 2.3 Statement Pooling (`pool_mode = statement`)

**How it works**: A server connection is assigned for a single SQL statement (a single `Query` message or a single extended query cycle). After each statement completes, the connection is returned to the pool. Multi-statement transactions are **not allowed** -- PgBouncer will reject BEGIN/COMMIT/ROLLBACK and any attempt to run multiple statements in a transaction.

**What it provides**: Even more aggressive multiplexing than transaction pooling.

**What breaks**: Everything that breaks in transaction mode, plus:
- **All transactions**: You cannot use BEGIN/COMMIT/ROLLBACK. Every statement runs in autocommit mode.
- **Multi-statement consistency**: No way to ensure two statements see the same snapshot.
- **Any form of state**: No state persists between statements at all.

**Trade-offs**:
- Pros: Maximum multiplexing efficiency.
- Cons: Extremely restrictive. Almost no real application can work with this mode.
- Best suited for: Very simple read-only workloads, monitoring queries, health checks. Rarely used in production.

### 2.4 Choosing a Pool Mode

The decision tree is straightforward:
1. If your application uses LISTEN/NOTIFY, session-level advisory locks, or cannot be modified: **session mode**.
2. If your application uses simple request-response patterns (each HTTP request = one transaction): **transaction mode**. This is the right choice for 90%+ of deployments.
3. If you only run stateless single-query reads: **statement mode** (rare).

---

## 3. Connection Lifecycle & State Machine

### 3.1 Client Connection States

The client connection progresses through these states (defined in PgBouncer source as `SocketState`):

```
CL_FREE        --> socket not in use (in free list)
CL_JUSTFREE    --> just disconnected, cleanup pending
CL_LOGIN       --> client connected, authentication in progress
CL_WAITING     --> authenticated, waiting for a server connection
CL_WAITING_LOGIN --> waiting for server connection to finish login
CL_ACTIVE      --> linked to a server connection, forwarding data
CL_CANCEL      --> processing a cancel request
```

**Detailed flow**:

1. **TCP Accept**: PgBouncer's listening socket becomes readable. `accept()` is called. A `PgSocket` is allocated from the slab allocator. State = `CL_LOGIN`.

2. **SSL Negotiation** (optional): If the client sends an `SSLRequest` message (8 bytes: length=8, code=80877103), PgBouncer either upgrades the connection to TLS (if configured with `client_tls_sslmode`, `client_tls_key_file`, `client_tls_cert_file`) or responds with `N` (no SSL).

3. **Startup Message Parsing**: The client sends a `StartupMessage` containing protocol version (3.0), and key-value pairs: `user`, `database`, `application_name`, `options`, etc. PgBouncer extracts the user and database name to determine which pool to use.

4. **Authentication**: PgBouncer authenticates the client. This is **independent** of how PgBouncer authenticates to the PostgreSQL server. The client-side auth method is determined by:
   - `auth_type` in pgbouncer.ini: `any`, `trust`, `plain`, `md5`, `scram-sha-256`, `cert`, `hba`, `pam`.
   - `auth_file` (a userlist.txt file with "user" "password" pairs, or "user" "" for auth_query delegation).
   - `auth_hba_file` (a pg_hba.conf-format file for `auth_type = hba`).
   - `auth_query` (a SQL query run against PostgreSQL to look up passwords dynamically, e.g., `SELECT usename, passwd FROM pg_shadow WHERE usename=$1`).

5. **Pool Lookup**: PgBouncer looks up or creates a pool keyed by `(database, user)`. Each pool maintains its own free list of server connections.

6. **Server Connection Assignment**:
   - If a free server connection exists in the pool: assign it to the client. Client state -> `CL_ACTIVE`.
   - If no free connection exists but pool has room to grow (current count < `pool_size`): initiate a new server connection (which itself goes through `SV_LOGIN` state). Client state -> `CL_WAITING_LOGIN`.
   - If pool is full: client enters the wait queue. Client state -> `CL_WAITING`. The client will block until a server connection is freed or a timeout (`client_login_timeout`, `query_wait_timeout`) fires.

7. **Data Forwarding (Active State)**: Client and server sbufs are linked. Data flows bidirectionally. PgBouncer monitors `ReadyForQuery` messages to track transaction state.

8. **Connection Release** (transaction/statement mode): When PgBouncer sees `ReadyForQuery(I)` from the server:
   - Run `server_reset_query` if configured.
   - Unlink client from server.
   - Return server to free list.
   - Client -> `CL_WAITING` (will be reassigned to a server when it sends the next query).

9. **Client Disconnect**: Client socket closes or errors. PgBouncer cleans up. If a server was linked, it is reset and returned to the pool (or closed if tainted).

### 3.2 Server Connection States

```
SV_FREE         --> not in use
SV_JUSTFREE     --> cleanup pending
SV_LOGIN        --> connecting to PostgreSQL, authentication in progress
SV_IDLE         --> in the pool's free list, ready for assignment
SV_ACTIVE       --> linked to a client, forwarding data
SV_USED         --> just unlinked from a client, reset query pending
SV_TESTED       --> test query in progress (server_check_query)
```

### 3.3 Authentication Detail

**Client-side authentication** (PgBouncer authenticates the client):
- **trust**: No authentication.
- **any**: Accept any password (just check the user exists).
- **plain**: Cleartext password match against `auth_file`.
- **md5**: MD5 challenge-response against `auth_file`.
- **scram-sha-256**: Full SCRAM-SHA-256 exchange. PgBouncer acts as the SCRAM server. Requires that `auth_file` contains SCRAM-verifier-format passwords (the SCRAM-SHA-256$iterations:salt:StoredKey:ServerKey format). Added in PgBouncer 1.14+.
- **cert**: TLS client certificate authentication. The CN (Common Name) of the client certificate must match the connecting username.
- **hba**: Use pg_hba.conf-format rules from `auth_hba_file` to determine the auth method per-connection (based on source IP, database, user).
- **pam**: PAM authentication (calls out to the system PAM stack).
- **auth_query**: Instead of storing passwords in `auth_file`, PgBouncer connects to PostgreSQL and runs a query to fetch the user's password hash. This allows PgBouncer to always have up-to-date credentials without restarting.

**Server-side authentication** (PgBouncer authenticates to PostgreSQL):
- PgBouncer uses the password from `auth_file` (or `auth_query` result) to authenticate to PostgreSQL.
- Supports md5, SCRAM-SHA-256, and cleartext methods on the server side.
- Important subtlety: if the server requires SCRAM and the `auth_file` only has md5 hashes, authentication fails. You need the actual plaintext password (or the SCRAM verifier, but PgBouncer cannot derive a SCRAM response from an md5 hash). This is a common deployment gotcha when migrating from `password_encryption = md5` to `scram-sha-256`.

### 3.4 Cancel Request Handling

PostgreSQL's cancel protocol is unusual: the client opens a **new TCP connection**, sends a `CancelRequest` message (16 bytes: length=16, code=80877102, PID, secret key), and immediately closes the connection. The server matches the PID and secret key to find the target backend and sends SIGINT to it.

PgBouncer must intercept this because:
1. The PID the client knows is PgBouncer's "virtual PID" (assigned when the client connected), not the real PostgreSQL backend PID.
2. PgBouncer must map the virtual PID back to the actual backend PID and secret key.
3. PgBouncer then opens a new connection to PostgreSQL and forwards a CancelRequest with the real PID and secret key.

This works but has a known limitation: if the client's transaction has already completed and the server connection has been reassigned to a different client (in transaction mode), the cancel may go to the wrong query on the wrong client. This is an inherent race condition in the cancel protocol combined with connection multiplexing. PgBouncer mitigates this by checking if the original client is still linked to the server connection, but the window exists.

---

## 4. Configuration Deep Dive

### 4.1 Pool Sizing Parameters

**`default_pool_size`** (default: 20)
- Number of server connections per (database, user) pool. This is the target number of connections PgBouncer will maintain to PostgreSQL for each unique database+user combination.
- If you have 5 databases and 3 users, you could have up to 15 pools, each with 20 connections = 300 server connections maximum.
- This is the single most important parameter to tune. Set it to roughly `(number of CPU cores on the PG server) * 2-4` for OLTP workloads.

**`min_pool_size`** (default: 0)
- Minimum number of server connections to keep open even when idle. PgBouncer will proactively open connections up to this number when a pool is created.
- Prevents the latency spike when the first queries arrive and connections must be established.
- Set to a small value (e.g., 5-10) to keep warm connections ready.

**`reserve_pool_size`** (default: 0)
- Additional connections allowed beyond `default_pool_size` when the pool has been exhausted for longer than `reserve_pool_timeout`.
- Acts as an overflow buffer for traffic spikes.

**`reserve_pool_timeout`** (default: 5 seconds)
- How long clients must wait in the queue before PgBouncer opens reserve pool connections.
- Prevents premature scaling of connections for brief spikes.

**`max_client_conn`** (default: 100)
- Maximum number of client connections PgBouncer will accept. This is the hard limit on concurrent clients.
- In production, often set to 5,000-50,000 depending on the workload.

**`max_db_connections`** (default: 0, unlimited)
- Maximum total server connections per database (across all users). This is a hard cap.
- Useful for preventing a single database from monopolizing all server connections.

**`max_user_connections`** (default: 0, unlimited)
- Maximum total server connections per user (across all databases).

### 4.2 Timeout Parameters

**`server_lifetime`** (default: 3600 seconds)
- How long a server connection can live before PgBouncer closes and reopens it. This is measured from when the connection was established.
- Purpose: Prevent issues from long-lived connections (connection-level caches growing stale, TCP connections going bad behind a load balancer, graceful rotation for maintenance).
- Setting this too low increases connection churn. Setting it too high means stale connections linger.

**`server_idle_timeout`** (default: 600 seconds)
- How long a server connection can sit idle in the pool before PgBouncer closes it.
- Helps reclaim connections during low-traffic periods.
- Works in conjunction with `min_pool_size` -- PgBouncer will not close connections below `min_pool_size` even if they exceed `server_idle_timeout`.

**`server_connect_timeout`** (default: 15 seconds)
- How long PgBouncer waits for a TCP connection to PostgreSQL to be established.
- Increase this if PostgreSQL is on a remote host with high latency.

**`server_login_retry`** (default: 15 seconds)
- If a server connection attempt fails, PgBouncer waits this long before retrying.
- During this period, client queries queue up (or fail if `query_wait_timeout` is reached).

**`query_timeout`** (default: 0, disabled)
- Maximum time a query can run before PgBouncer terminates the server connection. This is a **blunt instrument** -- it kills the entire connection, not just the query. Use PostgreSQL's `statement_timeout` instead when possible.

**`query_wait_timeout`** (default: 120 seconds)
- How long a client can wait in the queue for a server connection. If this timeout is reached, PgBouncer sends an error to the client: `ERROR: query_wait_timeout`.
- This is the primary safeguard against pool exhaustion. If clients are waiting longer than this, it means the pool is saturated.

**`client_idle_timeout`** (default: 0, disabled)
- Disconnect a client that has been idle for this long. Useful for cleaning up abandoned connections.
- In session mode, this is the only way to reclaim server connections from idle clients.

**`client_login_timeout`** (default: 60 seconds)
- Maximum time for a client to complete the authentication handshake.

**`idle_transaction_timeout`** (default: 0, disabled)
- Close server connections that have been idle in a transaction (status `T` or `E`) for too long. This catches the "idle in transaction" problem where a client starts a transaction and then goes silent, holding a server connection hostage.
- Equivalent to PostgreSQL 14+'s `idle_in_transaction_session_timeout`, but enforced by PgBouncer.

### 4.3 Connection Validation

**`server_check_query`** (default: `SELECT 1`)
- A simple query sent to a server connection before it is handed to a client, to verify the connection is still alive.
- Only used when `server_check_delay` has elapsed since the last activity on that connection.

**`server_check_delay`** (default: 30 seconds)
- How recently the server connection must have been used to skip the `server_check_query`. If the server connection was active within this many seconds, the check is skipped.
- Setting to 0 means check every time (adds latency). Setting very high means rarely check (risk of dead connections).

**`server_reset_query`** (default: `DISCARD ALL`)
- Query run on a server connection when it is returned to the pool (after being unlinked from a client in transaction/statement mode).
- `DISCARD ALL` clears: prepared statements, temporary tables, GUC changes, LISTEN registrations, advisory locks -- essentially all session state.
- **Performance impact**: Running `DISCARD ALL` adds a round-trip to PostgreSQL on every transaction completion. For high-throughput workloads, this can be significant (adding 0.1-0.5 ms per transaction).
- If your application is well-behaved and does not leak session state, you can set this to empty string (`server_reset_query =`) for better performance. Many production deployments do this.
- For PostgreSQL 9.3+, `DISCARD ALL` is the recommended default. For older versions, `RESET ALL; DEALLOCATE ALL;` was common.

**`server_reset_query_always`** (default: 0)
- If set to 1, run the reset query even in session mode (normally it is only run in transaction and statement modes).

### 4.4 Common Pitfalls

1. **Pool exhaustion without awareness**: If `default_pool_size` is 20 and you have 1,000 clients, all 1,000 clients share 20 server connections. If queries are slow (say 100ms each) and arrival rate exceeds 200 queries/second, clients will queue. Monitor `cl_waiting` in `SHOW POOLS`.

2. **Too many pools**: If you have 10 databases x 10 users, that is 100 pools x `default_pool_size` connections each. You might exceed PostgreSQL's `max_connections` without realizing it. Use `max_db_connections` and `max_user_connections` as safety caps.

3. **server_reset_query overhead**: Leaving `DISCARD ALL` as the default is safe but costs a round-trip. Profile whether it matters for your workload.

4. **query_wait_timeout too long or too short**: Too long means clients hang for minutes during outages (bad user experience). Too short means clients get errors during brief traffic spikes that the pool could handle with a bit of patience.

5. **server_lifetime interacting with connection storms**: If many server connections were created at the same time (e.g., during a cold start), they all hit `server_lifetime` at the same time, causing a "thundering herd" of reconnections. PgBouncer mitigates this with random jitter (up to `server_lifetime / 4` added as random offset since version 1.11).

6. **DNS caching**: If `dns_max_ttl` is too high and your PostgreSQL endpoint changes IP (e.g., AWS RDS failover), PgBouncer continues connecting to the old IP. Set `dns_max_ttl` to 30-60 seconds in cloud environments.

---

## 5. Performance Characteristics

### 5.1 Overhead Per Connection

- **Memory**: ~2 KB per client connection for the `PgSocket` structure, plus the `pkt_buf` size (default 4096 bytes). Total: ~6 KB per client connection. For 10,000 clients: ~60 MB.
- **CPU**: Negligible per idle connection (just a file descriptor in epoll/kqueue). Per active query, PgBouncer adds approximately 5-20 microseconds of processing time (buffer copy, state machine check, event loop overhead).
- **Latency overhead**: In well-configured deployments, PgBouncer adds 50-200 microseconds to query latency (one extra memory-to-memory copy in each direction, plus the event loop wakeup). On localhost (PgBouncer co-located with the application), this is typically 20-50 microseconds.

### 5.2 Throughput

PgBouncer on a single core can handle:
- **50,000-100,000 simple queries per second** (forwarding small queries and results between client and server on localhost).
- **10,000-30,000 transactions per second** for typical OLTP workloads with small result sets.

The bottleneck is rarely PgBouncer itself but rather:
- PostgreSQL backend performance
- Network latency between PgBouncer and PostgreSQL
- Serialization overhead for large result sets passing through PgBouncer's buffers

### 5.3 Comparison with Alternatives

**PgBouncer vs pgpool-II**:

| Aspect | PgBouncer | pgpool-II |
|--------|-----------|-----------|
| Architecture | Single-threaded, event-driven, C | Multi-process, fork-based |
| Memory per connection | ~6 KB | ~32 KB (a full child process for each client) |
| Connection pooling | Excellent (primary purpose) | Supported but heavier |
| Query parsing | None (packet-level proxy) | Full SQL parser (for load balancing, replication features) |
| Load balancing | No (not its job) | Yes (read/write splitting, query-level LB) |
| Replication features | No | Yes (streaming replication management, watchdog) |
| Failover | No (pair with HAProxy/Patroni) | Yes (built-in watchdog) |
| High availability | Run multiple instances behind LB | Built-in watchdog with VIP |
| Throughput | Higher (5-10x) for pure proxying | Lower due to process model overhead |
| Complexity | Simple, single config file | Complex, many features |
| Maturity for pooling | Gold standard | Adequate but not primary focus |

**Verdict**: Use PgBouncer for connection pooling. Use pgpool-II if you need its other features (read/write split, built-in failover) and connection pooling is secondary. Many deployments use PgBouncer for pooling + Patroni for failover.

**PgBouncer vs PostgreSQL built-in connection pooling**:

PostgreSQL does not have true built-in connection pooling as of PG 17. There have been various proposals:
- **connection_pool** patch (by Konstantin Knizhnik and others): Proposed for PG core, allows backends to be reused without forking a new process. Has not been committed as of PostgreSQL 17.
- **Per-backend connection caching** in PG 17: There was work on making backend startup cheaper (shared catalog caches, etc.), but not a pooler.

The PostgreSQL community has generally taken the stance that connection pooling belongs in middleware (PgBouncer) rather than in the core server, though this is an active area of discussion.

### 5.4 Benchmarking Notes

When benchmarking PgBouncer:
- Use `pgbench` with `-C` flag (connect/disconnect per transaction) to see the benefit of connection reuse.
- Without PgBouncer, `-C` mode adds ~5-15ms per transaction (fork + auth + SSL overhead).
- With PgBouncer, `-C` mode adds ~0.5-2ms per transaction (just TCP handshake to PgBouncer; the server connection is already established).
- For steady-state benchmarking (persistent connections), measure the latency overhead: typically 50-200 microseconds added per query round-trip.

---

## 6. Monitoring & Admin Console

### 6.1 Admin Console

PgBouncer exposes a virtual database called `pgbouncer` (configurable via `admin_users` and `stats_users`). You connect to it like a regular PostgreSQL database:

```
psql -p 6432 -U admin pgbouncer
```

This is not a real database. It is a command interface that responds to SHOW commands and a few control commands.

### 6.2 Key SHOW Commands

**SHOW POOLS** -- The most important monitoring view:
- `database`: Database name
- `user`: User name
- `cl_active`: Client connections currently linked to a server (actively running queries or in a transaction)
- `cl_waiting`: Client connections waiting for a server connection (QUEUE DEPTH -- this is the most critical metric)
- `cl_cancel_req`: Client connections with pending cancel requests
- `sv_active`: Server connections currently linked to a client
- `sv_idle`: Server connections idle in the pool (available for assignment)
- `sv_used`: Server connections running `server_reset_query`
- `sv_tested`: Server connections running `server_check_query`
- `sv_login`: Server connections in the process of connecting/authenticating to PostgreSQL
- `maxwait`: How long the oldest waiting client has been waiting (in seconds). **Alert if this exceeds a few seconds.**
- `maxwait_us`: Same, in microseconds
- `pool_mode`: The pool mode for this pool

**Critical alert thresholds**:
- `cl_waiting > 0` for sustained periods: pool is saturated
- `maxwait > 1s`: clients are experiencing significant queueing delay
- `sv_active = pool_size` and `cl_waiting > 0`: pool is fully utilized, need to either increase pool_size or optimize queries

**SHOW STATS** -- Aggregate statistics:
- `total_xact_count`: Total transactions processed
- `total_query_count`: Total queries processed
- `total_received`: Total bytes received from clients
- `total_sent`: Total bytes sent to clients
- `total_xact_time`: Total microseconds spent in transactions
- `total_query_time`: Total microseconds spent on queries
- `total_wait_time`: Total microseconds clients spent waiting for a server connection
- `avg_xact_count`: Average transactions per second (since last SHOW STATS)
- `avg_query_count`: Average queries per second
- `avg_xact_time`: Average transaction duration in microseconds
- `avg_query_time`: Average query duration in microseconds
- `avg_wait_time`: Average wait time in microseconds (should be near zero in a healthy system)

**SHOW STATS_TOTALS** -- Cumulative stats since PgBouncer start.

**SHOW STATS_AVERAGES** -- Running averages.

**SHOW CLIENTS** -- All client connections:
- `type`, `user`, `database`, `state` (active/waiting/used/idle), `addr`, `port`
- `local_addr`, `local_port`
- `connect_time`, `request_time`
- `wait`, `wait_us` (how long in current waiting state)
- `close_needed` (flagged for closure)
- `ptr`, `link` (internal pointers; `link` shows the paired server connection)

**SHOW SERVERS** -- All server connections:
- Similar fields to SHOW CLIENTS
- `remote_pid`: The actual PostgreSQL backend PID
- `tls`: TLS status

**SHOW DNS_HOSTS** -- DNS cache entries.

**SHOW DNS_ZONES** -- DNS zone information.

**SHOW CONFIG** -- Current configuration.

**SHOW DATABASES** -- Configured databases and their settings (pool_size, pool_mode, etc.).

**SHOW FDS** -- File descriptor information (useful for debugging fd limits).

**SHOW LISTS** -- Internal list lengths (free clients, free servers, used clients, etc.).

**SHOW MEM** -- Memory usage by internal slab allocators.

**SHOW SOCKETS** -- Low-level socket state.

**SHOW ACTIVE_SOCKETS** -- Only active (non-idle) sockets.

### 6.3 Control Commands

- **PAUSE [db]**: Stop assigning server connections. Clients that finish their current transaction will wait. All server connections become idle. Used for graceful maintenance (e.g., before restarting PostgreSQL).
- **RESUME [db]**: Undo PAUSE.
- **DISABLE db**: Reject new client connections to a database.
- **ENABLE db**: Undo DISABLE.
- **KILL db**: Close all client and server connections to a database immediately.
- **SUSPEND**: Flush all data and stop I/O. Used for online restart of PgBouncer itself (combined with `RESUME` after restart with `-R` flag).
- **SHUTDOWN**: Shut down PgBouncer.
- **RELOAD**: Reload config file (pgbouncer.ini). Does not drop connections.
- **RECONNECT [db]**: Close and reopen all server connections (gracefully, after current transactions finish). Useful after a failover.

### 6.4 Prometheus/Grafana Integration

PgBouncer does not natively export Prometheus metrics. Common approaches:
- **pgbouncer_exporter** (standalone Go binary): Connects to the admin console, runs SHOW commands, and exposes metrics in Prometheus format.
- **Built-in Prometheus exporter** was proposed but not yet merged as of early 2026.

Key metrics to graph:
- `cl_waiting` per pool (pool saturation)
- `maxwait` per pool (queue depth)
- `avg_wait_time` (user-facing latency from pooling)
- `avg_query_time` vs `avg_xact_time` (query performance)
- `total_xact_count` rate (throughput)
- `sv_active` / `sv_idle` ratio (pool utilization)

---

## 7. Deployment Patterns

### 7.1 Sidecar Pattern (Application-Local)

PgBouncer runs on the same host (or in the same pod, in Kubernetes) as each application instance.

**Pros**:
- Minimal latency between application and PgBouncer (localhost / Unix socket).
- Each application instance has its own PgBouncer, so a misbehaving application cannot exhaust the pool for others.
- Simpler TLS setup (no TLS needed between app and PgBouncer on localhost).
- Natural horizontal scaling: as application instances scale, PgBouncer instances scale with them.

**Cons**:
- Many PgBouncer instances, each with their own pool. If `default_pool_size = 20` and you have 50 application instances, that is 1,000 server connections to PostgreSQL. The pooling benefit is per-instance, not global.
- To address this, reduce `default_pool_size` per instance (e.g., 2-5 per instance).
- Operational burden: many instances to configure and monitor.

**Kubernetes specifics**: PgBouncer is often deployed as a sidecar container in the application pod. The application connects to `localhost:5432` (PgBouncer), and PgBouncer connects to the PostgreSQL service. Configuration is typically injected via ConfigMap or secrets.

### 7.2 Centralized Pattern (Dedicated Pooling Tier)

One or a few PgBouncer instances sit between all applications and PostgreSQL.

**Pros**:
- Global pool: all applications share a single pool of server connections. Maximum efficiency in connection usage.
- Single point of configuration and monitoring.
- Easier to enforce connection limits.

**Cons**:
- Single point of failure (mitigated by running 2+ instances behind a load balancer).
- Network hop between application and PgBouncer adds latency (~0.2-1ms on a local network).
- A single PgBouncer instance only uses one CPU core. Must run multiple instances for high-throughput workloads (see multi-instance section below).
- Noisy neighbor problem: one application's slow queries can exhaust the pool for all applications.

### 7.3 Multi-Tier Pooling

For very large deployments: application-local PgBouncer (sidecar) connecting to a centralized PgBouncer tier.

- Sidecar PgBouncer handles rapid connection churn from the application (especially for frameworks that open/close connections per request).
- Centralized PgBouncer provides global pool management and connection limiting.
- Configuration: sidecar pool_mode = session (connections to centralized PgBouncer are long-lived), centralized pool_mode = transaction.

This is an advanced pattern. The added complexity is only justified for deployments with hundreds of application instances and strict connection limits on PostgreSQL.

### 7.4 HAProxy + PgBouncer

Common pattern for high availability:

```
Application --> HAProxy --> PgBouncer instances (2+) --> PostgreSQL
```

HAProxy provides:
- Health checking of PgBouncer instances (TCP check or HTTP check via a custom health endpoint).
- Load distribution across multiple PgBouncer instances.
- Transparent failover if a PgBouncer instance dies.

Configuration considerations:
- HAProxy should use `balance leastconn` for PgBouncer backends.
- Set `timeout client` and `timeout server` appropriately (PgBouncer connections may be long-lived in session mode).
- TCP mode (`mode tcp`), not HTTP mode.
- Consider enabling the HAProxy PROXY protocol if you need PgBouncer to know the client's real IP (PgBouncer supports PROXY protocol since 1.17).

### 7.5 TLS Termination

PgBouncer supports TLS on both sides:

**Client-facing TLS** (`client_tls_*` parameters):
- `client_tls_sslmode`: `disable`, `allow`, `prefer`, `require`, `verify-ca`, `verify-full`
- `client_tls_key_file`, `client_tls_cert_file`, `client_tls_ca_file`
- `client_tls_protocols`: Minimum/maximum TLS protocol version
- `client_tls_ciphers`: Cipher suite specification
- `client_tls_dheparams`: DH parameter size

**Server-facing TLS** (`server_tls_*` parameters):
- Same parameters, controlling the connection from PgBouncer to PostgreSQL.

**Common patterns**:
- Terminate TLS at PgBouncer (client TLS enabled, server TLS disabled if PgBouncer is on the same host as PostgreSQL or on a trusted network).
- Pass-through TLS (not supported -- PgBouncer must terminate and re-establish TLS because it needs to read the protocol messages).
- Both sides encrypted (client TLS + server TLS) for zero-trust networks.

### 7.6 Running Multiple PgBouncer Instances

Since PgBouncer is single-threaded, scaling beyond one CPU core requires multiple instances:

**Option 1: Multiple instances on different ports**:
- Run PgBouncer on ports 6432, 6433, 6434, etc.
- Use HAProxy or application-level routing to distribute across them.

**Option 2: SO_REUSEPORT** (Linux 3.9+):
- Multiple PgBouncer instances bind to the same port.
- The kernel distributes incoming connections across them.
- Requires `so_reuseport = 1` in pgbouncer.ini (available since PgBouncer 1.12).
- This is the cleanest approach on Linux.

**Option 3: PgBouncer's built-in multi-threading** (see Section 9).

---

## 8. Known Limitations & Gotchas

### 8.1 Prepared Statement Problem (Pre-1.21)

This is historically the biggest pain point with PgBouncer in transaction mode.

**The problem**: In PostgreSQL, `PREPARE` creates a named prepared statement on a specific backend. The name is session-scoped. When the connection pool reassigns the client to a different backend, that backend has no knowledge of the prepared statement. The client gets `ERROR: prepared statement "xxx" does not exist`.

This also applies to the **extended query protocol** (which is what most client drivers use: libpq's PQprepare/PQexecPrepared, JDBC's PreparedStatement, etc.). In the extended query protocol, the client sends Parse (with a statement name), then Bind, then Execute. If the Parse was done in a previous transaction and the server connection has changed, the named statement is gone.

**The unnamed statement exception**: The extended query protocol allows an **unnamed** prepared statement (empty string as the name). This statement is implicitly re-prepared on each Parse message. Since most ORMs and drivers use the unnamed statement for simple queries, many applications "just work" in transaction mode. But drivers that cache named statements (like PgJDBC with `prepareThreshold`) will break.

**Workarounds (pre-1.21)**:
1. Disable statement caching in the client driver (e.g., PgJDBC: `prepareThreshold=0`).
2. Use the unnamed statement only (libpq: `PQexecParams` instead of `PQprepare` + `PQexecPrepared`).
3. Use `PREPARE` and `DEALLOCATE` within the same transaction.
4. Use session mode (defeats the purpose of pooling).

### 8.2 Prepared Statement Tracking (PgBouncer 1.21+)

PgBouncer 1.21 (released late 2023) introduced **prepared statement tracking** in transaction mode, controlled by `max_prepared_statements`.

**How it works**:
- PgBouncer intercepts `Parse` messages from clients that contain named statements.
- It maintains a per-client mapping of statement name -> query text + parameter types.
- When a server connection is assigned to a client, PgBouncer checks if the server has the needed prepared statements. If not, it transparently re-sends the `Parse` messages to the server before forwarding the client's `Bind`/`Execute`.
- When a server connection is released, PgBouncer does NOT `DEALLOCATE` the statements (this would add latency). Instead, it tracks which statements each server has.
- If the statement cache on a server is full (`max_prepared_statements` reached), PgBouncer evicts the least recently used statement by sending `DEALLOCATE` and then `PARSE` for the new one.

**Limitations of the prepared statement tracking**:
- Adds memory overhead: PgBouncer must store the query text for each prepared statement for each client.
- Adds latency on the first use of a statement with a new server connection (the transparent re-Parse).
- SQL-level `PREPARE` and `DEALLOCATE` are still not tracked -- only protocol-level Parse/Close messages. This means `PREPARE foo AS SELECT ...` in a SQL query is still not handled.
- The `max_prepared_statements` parameter controls the maximum number of prepared statements tracked per server connection (default: 0, meaning disabled). Set it to a value like 100 or 200 to enable.

### 8.3 ParameterStatus Tracking

When a server connection is assigned to a client, PgBouncer must ensure the server's GUC settings match what the client expects. PostgreSQL sends `ParameterStatus` messages during startup and whenever certain GUCs change (e.g., `client_encoding`, `DateStyle`, `integer_datetimes`, `server_version`, `TimeZone`, `standard_conforming_strings`, etc.).

PgBouncer tracks a set of parameters (controlled by `track_extra_parameters`) and when reassigning a server to a client, it issues `SET` commands to synchronize any parameters that differ.

**`track_extra_parameters`** (added in PgBouncer 1.17):
- A comma-separated list of additional GUC parameters to track and synchronize.
- By default, PgBouncer tracks the parameters that PostgreSQL automatically sends in `ParameterStatus` messages.
- If your application issues `SET search_path = ...` (a very common pattern), you need to add `search_path` to `track_extra_parameters` or it will be lost when the connection is reassigned.

### 8.4 Protocol-Level Limitations

- **COPY protocol**: PgBouncer supports COPY (both COPY IN and COPY OUT) by detecting the CopyInResponse / CopyOutResponse messages and switching to pass-through mode until CopyDone / CopyFail is received. However, the connection cannot be multiplexed during a COPY operation.

- **Streaming replication protocol**: Not supported. PgBouncer cannot proxy replication connections (the replication protocol is a different protocol mode initiated by `replication=true` in the startup message). PgBouncer will reject these connections.

- **Large results**: Results that exceed `pkt_buf` are handled by streaming (PgBouncer does not buffer the entire result set), but performance is best when packets fit in the buffer.

- **Extended query protocol edge cases**: The extended query protocol allows pipelining (sending multiple Parse/Bind/Execute/Sync messages without waiting for responses). PgBouncer supports this but must be careful about transaction boundary detection. The `Sync` message triggers `ReadyForQuery` from the server, which PgBouncer uses to detect transaction boundaries.

### 8.5 LISTEN/NOTIFY in Transaction Mode

This deserves special emphasis because it is a frequently encountered problem:

- `LISTEN channel` registers the current backend to receive notifications on that channel.
- In transaction mode, after the transaction containing `LISTEN` completes, the server connection is returned to the pool. The LISTEN registration is still active on that server, but it is no longer linked to the original client.
- If another client is assigned that server connection, notifications intended for the first client may be delivered to the second client (or to no one, if the server is idle).
- PgBouncer's `DISCARD ALL` reset query will execute `UNLISTEN *`, cleaning this up. But between the `LISTEN` and the `DISCARD ALL`, there is a window of incorrect behavior.

**The only safe approach**: Use a dedicated, non-pooled connection for LISTEN/NOTIFY. Most PostgreSQL drivers support specifying a separate connection string for notification listeners.

### 8.6 Application-Level Connection Pool Interaction

Many applications use their own connection pool (HikariCP in Java, SQLAlchemy's pool in Python, connection_pool in Rails, etc.). When these are used with PgBouncer, you have two levels of pooling:

- Application pool holds N persistent connections to PgBouncer.
- PgBouncer holds M connections to PostgreSQL.

**The gotcha**: Application-level pools typically hold connections open for the lifetime of the application process. In session mode, this means N application pool connections = N PgBouncer server connections, negating PgBouncer's benefit. In transaction mode, PgBouncer can multiplex, and the benefit is restored.

**Best practice**: When using PgBouncer in transaction mode, set the application-level pool's maximum size higher than you normally would (it is cheap to hold many connections to PgBouncer). The key limit is PgBouncer's `default_pool_size`, not the application pool size.

However, be careful not to set the application pool's minimum idle connections too high, as each idle connection still consumes a file descriptor on PgBouncer.

### 8.7 Authentication Gotchas

- **SCRAM-SHA-256 dual-auth problem**: PgBouncer must authenticate clients AND authenticate to PostgreSQL. For SCRAM, it needs the actual password (or SCRAM verifier for client-side, and plaintext password for server-side). If you use `auth_query` and the database returns SCRAM-hashed passwords, PgBouncer can use those hashes to authenticate clients (acting as SCRAM server with the stored verifier), but it CANNOT derive the plaintext password from the SCRAM verifier to authenticate to PostgreSQL. You must store plaintext passwords in `auth_file` for the server-side auth, or use `auth_query` in a way that PgBouncer has access to the original password.

- **auth_query bootstrap problem**: `auth_query` requires a connection to PostgreSQL to look up passwords. But to connect to PostgreSQL, PgBouncer needs a password. This is solved by having at least one user with credentials in `auth_file` that PgBouncer uses for the `auth_query` connections.

### 8.8 Other Gotchas

- **max_client_conn and file descriptor limits**: Each client connection consumes a file descriptor. Ensure the OS `ulimit -n` is set high enough. PgBouncer also needs fds for server connections, the admin console, DNS, and internal pipes. Rule of thumb: `ulimit -n` should be at least `max_client_conn * 2 + 100`.

- **Connection storms after PgBouncer restart**: When PgBouncer restarts, all client connections are dropped. All clients reconnect simultaneously, and PgBouncer must establish new server connections. This can overwhelm PostgreSQL if `max_client_conn` is large. Mitigate with:
  - Online restart (`SUSPEND` + restart with `-R` flag to inherit file descriptors).
  - `min_pool_size` to pre-warm the server connection pool.
  - Application-side retry with exponential backoff.

- **TCP keepalive tuning**: Long-idle connections may be killed by intermediate network equipment (firewalls, NAT, load balancers) that drop idle TCP connections. Configure `tcp_keepalive`, `tcp_keepidle`, `tcp_keepintvl`, `tcp_keepcnt` on PgBouncer, and ensure they are shorter than any intermediate timeout.

- **Unix socket vs TCP**: PgBouncer can listen on a Unix domain socket (`unix_socket_dir`), which reduces overhead for co-located applications (~30% lower latency than TCP loopback due to no TCP/IP stack overhead).

---

## 9. Recent Developments

### 9.1 Multi-Threaded PgBouncer

The single-threaded architecture, while elegant, is a scaling limitation. On modern servers with 64+ cores, a single PgBouncer instance can become a bottleneck for high-throughput workloads (100,000+ queries/second).

**PgBouncer 1.22+ (2024) introduced multi-threading support**:
- Controlled by the `thread_count` parameter (default: 1, preserving backward compatibility).
- Each thread runs its own event loop.
- New connections are distributed across threads by the accept thread using a round-robin or SO_REUSEPORT approach.
- Each thread has its own pool of server connections. This means server connections are not shared across threads, which simplifies synchronization but means each thread independently manages its own pool. The effective total pool size is `default_pool_size * thread_count` (roughly, with some nuance around per-thread vs global limits).
- Shared state (configuration, stats aggregation) is protected by lightweight synchronization.

**Implications**:
- For the majority of deployments (< 50,000 queries/second), `thread_count = 1` is still sufficient and simpler.
- For high-throughput deployments, `thread_count = N` (where N is a small number like 4-8) can eliminate the single-core bottleneck without needing multiple PgBouncer instances.
- This reduces the operational complexity of the "multiple instances on SO_REUSEPORT" pattern described in Section 7.6.

### 9.2 SCRAM Authentication Improvements

PgBouncer's SCRAM support has evolved through several versions:

- **1.14** (2020): Initial SCRAM-SHA-256 support. PgBouncer could authenticate to PostgreSQL using SCRAM, and could authenticate clients using SCRAM if the `auth_file` contained SCRAM verifiers.
- **1.17** (2022): Improved handling of SCRAM channel binding (tls-server-end-point). This was important for `scram-sha-256` with `sslmode=verify-full`.
- **1.21+**: Further SCRAM refinements, including proper handling of the SCRAM `AuthenticationSASLContinue` flow when used with `auth_query`.

The direction is toward full SCRAM parity with what PostgreSQL itself supports, including channel binding.

### 9.3 track_extra_parameters Expansion

Starting from PgBouncer 1.17, the `track_extra_parameters` feature allows tracking arbitrary GUC parameters across connection reassignment. This was further expanded in subsequent versions:

- Initially supported: `IntervalStyle`, `TimeZone`, `standard_conforming_strings`, etc. (the parameters PostgreSQL sends in `ParameterStatus`).
- 1.21+: Users can add any GUC to `track_extra_parameters`, and PgBouncer will track SET commands for those parameters and replay them when reassigning connections.
- This significantly reduces the cases where `server_reset_query = DISCARD ALL` is necessary, because the important parameters are explicitly tracked.

### 9.4 Peering / Routing (Experimental/Proposed)

There have been community discussions and experimental patches for:
- **PgBouncer peering**: Multiple PgBouncer instances sharing pool state, allowing a client connected to instance A to use a server connection from instance B's pool. This would solve the multi-instance pool fragmentation problem. Not merged as of early 2026.
- **Read/write routing**: Sending read-only queries to replicas and read-write queries to the primary, based on parsing the SQL or inspecting transaction flags. PgBouncer has historically avoided SQL parsing, so this is more likely to be implemented at the session/transaction level (e.g., "connect to database `mydb_ro` for read replicas") rather than automatic query-level splitting.

### 9.5 PROXY Protocol Support

PgBouncer 1.17+ supports the HAProxy PROXY protocol (v1 and v2) for receiving client IP information from upstream load balancers. This is important for:
- Logging the real client IP in PgBouncer logs and PostgreSQL logs.
- Applying pg_hba.conf rules based on the real client IP.

---

## 10. Summary Decision Matrix

| Scenario | Recommended Mode | Key Parameters |
|----------|-----------------|----------------|
| Web app with ORM, short transactions | Transaction | `default_pool_size` = 2-4x cores, `server_reset_query` = empty if app is well-behaved |
| Legacy app using LISTEN/NOTIFY | Session | Full session pooling; consider separate non-pooled connection for LISTEN |
| Serverless / Lambda functions | Transaction | High `max_client_conn`, low `default_pool_size`, `server_idle_timeout` = 60s |
| Microservices with HikariCP | Transaction | Sidecar pattern, `default_pool_size` = 2-5 per instance, `max_db_connections` as global cap |
| Read-only analytics queries | Transaction or Statement | Statement mode if truly single-query; transaction mode otherwise |
| High-throughput OLTP (100k+ qps) | Transaction | `thread_count` > 1 (1.22+), or multiple instances with SO_REUSEPORT |
| Multi-tenant SaaS | Transaction | `max_db_connections` and `max_user_connections` to enforce per-tenant limits |

---

## 11. Key References

### Official Documentation
- PgBouncer official docs: https://www.pgbouncer.org/config.html
- PgBouncer GitHub: https://github.com/pgbouncer/pgbouncer

### Key Blog Posts and Talks
- "Scaling Connections in Postgres" -- Crunchy Data blog series on PgBouncer tuning
- "PgBouncer and Prepared Statements" -- explains the 1.21+ prepared statement tracking in detail
- "How PgBouncer Works" -- Percona blog post with internal architecture overview
- Peter Eisentraut, "Connection Pooling in PostgreSQL" -- PGConf talk on the state of connection pooling in the ecosystem

### Related Projects
- **Odyssey** (Yandex): An alternative PostgreSQL connection pooler with multi-threaded architecture from the start. Written in C with custom coroutine implementation (machinarium). Supports transaction pooling, multi-threaded I/O. Less widely adopted than PgBouncer but architecturally interesting. https://github.com/yandex/odyssey
- **pgcat** (formerly pgbouncer-rr, now Supabase's fork): A Rust-based PostgreSQL proxy with connection pooling, sharding, and load balancing. Designed for multi-tenant SaaS. https://github.com/postgresml/pgcat
- **pgagroal**: A high-performance PostgreSQL connection pool with a focus on being fast and having low overhead. Written in C, supports multi-process model. https://github.com/agroal/pgagroal
- **Supavisor** (Supabase): An Elixir-based multi-tenant connection pooler built for cloud-native PostgreSQL deployments. Designed for very large numbers of tenants. https://github.com/supabase/supavisor

### Academic / Deep Technical
- "The Impact of Connection Pooling on Database Performance" -- various benchmarks available from PostgreSQL community wiki
- PostgreSQL wire protocol documentation: https://www.postgresql.org/docs/current/protocol.html (essential reading for understanding what PgBouncer is proxying)

---

## 12. Future Considerations

1. **PostgreSQL core pooling**: Watch the mailing list for progress on built-in connection pooling. If PostgreSQL ever ships a native pooler, PgBouncer's role may evolve or diminish.

2. **HTTP/3 and QUIC**: As QUIC adoption grows, there may be interest in a QUIC-based protocol for PostgreSQL connections, which would change the proxy landscape.

3. **eBPF-based proxying**: There have been experiments with using eBPF to do TCP proxy at the kernel level, which could reduce the userspace overhead of PgBouncer to near zero for the packet-forwarding hot path.

4. **PgBouncer peering**: If the peering feature matures, it would solve the pool fragmentation problem in multi-instance deployments.

5. **Better observability**: Native Prometheus metrics export, distributed tracing integration (correlating client query with server query through PgBouncer).
