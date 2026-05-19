+++
title = "Deterministic Simulation Testing"
date = 2026-02-11
[taxonomies]
tags = ["simulation-testing", "foundationdb", "fault-injection", "determinism", "tigerbeetle", "testing"]
+++


# Deterministic Simulation Testing (DST)

A comprehensive reference on DST: the technique of running distributed systems inside a fully controlled, deterministic simulation to find correctness bugs that are impossible to reproduce in traditional testing.

---

## 1. Foundations

### 1.1 What DST Is

DST runs an entire distributed system — multiple nodes, network, disk, clocks — inside a single-threaded, deterministic process. All sources of non-determinism (I/O, time, scheduling, random numbers) are replaced with controlled, seed-driven simulations. Given the same seed, the system executes the **exact same sequence of events** every time.

```
Traditional Testing:                    DST:
  Real nodes, real network               All nodes in one process
  Real clocks, real disk                 Simulated clock, simulated disk
  Non-deterministic scheduling           Deterministic scheduling (PRNG)
  Bugs may not reproduce                 Bugs always reproduce (same seed)
  Hours per scenario                     Milliseconds per scenario
```

Formally (Tornow, 2024): a system S with initial state σ and runtime R is deterministic if:

```
∀ σ ∈ Σ : | traces(S, σ, R) | = 1
```

That is, for every initial state, there exists exactly one possible trace of external events. DST achieves this by replacing the non-deterministic runtime R with a deterministic simulator R_sim seeded by a PRNG.

### 1.2 The Core Insight

All non-determinism in software comes from a small number of sources:

```
Source                  Solution in DST
─────────────────────   ──────────────────────────────
Thread scheduling       Single-threaded event loop
Wall clock time         Simulated clock (discrete advances)
Network I/O             Simulated network (in-process)
Disk I/O                Simulated filesystem (in-memory)
Random numbers          Seeded PRNG
OS signals/interrupts   Not simulated (or intercepted)
Hardware interrupts     Not simulated (or hypervisor)
```

If you control all of these, execution becomes a deterministic function of the seed. This enables:
- **Perfect reproducibility**: re-run any bug with the same seed
- **Time-travel debugging**: step through the exact event sequence
- **Accelerated time**: skip `sleep()` calls, compress hours into seconds
- **Exhaustive fault injection**: inject every combination of faults

### 1.3 Historical Origins

The idea traces to **hardware simulation**: chip designers have used deterministic simulation (Verilog/VHDL simulators) since the 1980s to verify circuits before fabrication. The FoundationDB team (2009-2013) applied this insight to distributed software.

Key intellectual lineage:
- **Lamport, "Time, Clocks, and the Ordering of Events"** (CACM, 1978): establishes that logical time can replace wall-clock time — the foundation of simulated clocks.
- **FLP impossibility** (Fischer, Lynch, Paterson, JACM, 1985): proves distributed consensus is impossible with even one faulty process — motivates why distributed systems are so hard to test.
- **Hardware simulation** (Verilog, VHDL, 1980s-): deterministic cycle-accurate simulation of digital circuits. The FoundationDB team explicitly drew on this analogy.
- **Discrete-event simulation** (Simula, 1967; ns-2/ns-3): network simulators that model packet-level behavior deterministically.

---

## 2. FoundationDB: The Origin Story

### 2.1 The Radical Decision

When FoundationDB Inc. (~2009) set out to build a distributed database, they didn't start by building a database. **They built a deterministic simulator first.** The database came second.

> "Before building the database itself, we built a deterministic database simulation framework that can simulate a network of interacting processes and a variety of disk, process, network, and request-level failures and recoveries, all within a single physical process."
> — Zhou et al., SIGMOD 2021

This decision was driven by the founders' (Dave Scherer, Will Wilson, et al.) conviction that distributed systems cannot be made correct through ad-hoc testing. The combinatorial explosion of failure modes (crashes × partitions × disk errors × clock skew × message reordering) makes manual testing inadequate.

### 2.2 The Flow Language

To make simulation possible, they built **Flow**, a custom programming language that compiles to C++11.

**What Flow is:**
- An **actor-based concurrency framework** implemented as a C++ preprocessor
- Compilation: `Flow source → Flow compiler → C++11 → g++/clang++ → binary`
- Every asynchronous operation is an **actor** decorated with the `ACTOR` macro
- Actors communicate via `Future<T>` / `Promise<T>` pairs
- `wait()` suspends an actor until a Future resolves (non-blocking, cooperative)

**Key constructs:**

```cpp
// ACTOR: declares an asynchronous function (compiled to a state machine)
ACTOR Future<int> fetchAndAdd(Database db, Key key, int delta) {
    state int current = wait(db.get(key));     // suspend until get() completes
    wait(db.set(key, current + delta));        // suspend until set() completes
    return current + delta;
}

// state keyword: variable survives across wait() calls
// (local vars do NOT survive wait — Flow rewrites actors as classes)

// choose...when: wait on multiple futures
ACTOR void handleRequests(ServerInterface si) {
    state int count = 0;
    loop {
        choose {
            when(int x = waitNext(si.addCount.getFuture())) {
                count += x;
            }
            when(Promise<int> r = waitNext(si.getCount.getFuture())) {
                r.send(count);
            }
        }
    }
}

// PromiseStream<T> / FutureStream<T>: multiple async messages
// waitNext(): wait for next stream value
```

**Why Flow enables DST:**
- All I/O goes through abstract interfaces (`INetwork`, `IAsyncFile`)
- The Flow runtime scheduler is a **single-threaded event loop** with a priority queue
- In production: the event loop uses `epoll`/`kqueue` for real I/O (`Net2`)
- In simulation: the event loop is replaced with a time-stepped simulator (`Sim2`)
- Same binary, two runtimes:

```
Production mode:   App → INetwork → Net2 (real TCP/epoll)
                   App → IAsyncFile → AsyncFileNonDurable (real disk)

Simulation mode:   App → INetwork → Sim2 (simulated network)
                   App → IAsyncFile → SimAsyncFile (simulated disk)
```

### 2.3 The Simulator Architecture

```
┌─────────────────────────────────────────────────┐
│              Single OS Process                   │
│                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Process 1│  │ Process 2│  │ Process 3│ ...   │
│  │ (actors) │  │ (actors) │  │ (actors) │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
│       │              │              │            │
│  ┌────┴──────────────┴──────────────┴────┐      │
│  │         Simulated Network              │      │
│  │  (latency, reordering, drops,          │      │
│  │   partitions, connection failures)     │      │
│  └────────────────┬──────────────────────┘      │
│                   │                              │
│  ┌────────────────┴──────────────────────┐      │
│  │         Simulated Disk                 │      │
│  │  (latency, torn writes, corruption,    │      │
│  │   space exhaustion, power loss)        │      │
│  └────────────────┬──────────────────────┘      │
│                   │                              │
│  ┌────────────────┴──────────────────────┐      │
│  │         Simulated Clock                │      │
│  │  (discrete advances, clock skew)       │      │
│  └────────────────┬──────────────────────┘      │
│                   │                              │
│  ┌────────────────┴──────────────────────┐      │
│  │         Deterministic Scheduler        │      │
│  │  (seeded PRNG, priority queue)         │      │
│  └───────────────────────────────────────┘      │
└─────────────────────────────────────────────────┘
```

**What gets simulated:**
- Machine count, type, drive performance, storage capacity
- Network: latency, packet drops, reordering, partitions, connection failures
- Disk: latency, torn writes, corruption, space exhaustion
- Process: crashes, reboots, recovery
- Datacenter: full datacenter failures
- Clock: skew, drift, jumps

**Time compression**: simulations achieve ~10:1 real-to-simulated time ratio. A 1-hour test covers ~10 hours of simulated cluster operation.

**Scale**: FoundationDB claims roughly **one trillion CPU-hours** of cumulative simulation testing.

### 2.4 BUGGIFY: Cooperative Fault Injection

`BUGGIFY` is a macro that injects faults **only in simulation mode**. In production builds, `BUGGIFY` always evaluates to false.

```cpp
// In FoundationDB source code:
if (BUGGIFY) {
    // Only executes during simulation testing
    wait(delay(deterministicRandom()->random01() * 10));  // random delay
}

if (BUGGIFY) {
    throw io_error();  // simulate I/O failure
}

// BUGGIFY_WITH_PROB(p): fire with probability p
if (BUGGIFY_WITH_PROB(0.01)) {
    // 1% chance in simulation
    connection.close();
}
```

**Why BUGGIFY is powerful**: developers sprinkle `BUGGIFY` throughout the codebase at every point where something unusual could happen. This creates a cooperative fault injection system where the code itself knows its own failure modes. Over time, the codebase accumulates thousands of BUGGIFY sites covering edge cases that external fault injection could never target.

### 2.5 Swarm Testing

FoundationDB uses **swarm testing** (Chen et al., ISSTA 2012): instead of running every test with all features enabled, each test run randomly enables/disables a subset of features and BUGGIFY sites. This dramatically increases coverage by exploring different regions of the state space.

```
Run 1: BUGGIFY sites {A, C, F} enabled, features {X, Z} on
Run 2: BUGGIFY sites {B, D, E} enabled, features {Y} on
Run 3: BUGGIFY sites {A, B, F} enabled, features {X, Y, Z} on
...
```

**"Swizzle-clogging"**: a stress pattern that randomly stops and unclogs machine network connections to surface deep, rare bugs in connection management.

### 2.6 Will Wilson's Strange Loop Talk (2014)

The talk that introduced DST to the broader software community. Key claims:

1. FoundationDB's simulator found **every bug** that later appeared in production (before deployment).
2. When Kyle Kingsbury (Jepsen's creator) tested FoundationDB, he found nothing worth reporting — the simulator had already caught everything.
3. The simulator runs **millions of test scenarios** that would be impossible to reproduce in real infrastructure.
4. Determinism enables "time-travel debugging": given a failing seed, replay the exact event sequence, set breakpoints, and step through.

---

## 3. How DST Works: Technical Deep Dive

### 3.1 Controlling Non-Determinism

**Step 1: Seed a PRNG**

All randomness flows from a single seed. The seed determines:
- Network message delivery order and latency
- Fault injection timing and type
- Process scheduling order
- Workload generation

```rust
// Pseudocode
fn simulate(seed: u64, steps: usize) {
    let mut rng = StdRng::seed_from_u64(seed);
    let mut clock = SimulatedClock::new();
    let mut network = SimulatedNetwork::new(&mut rng);
    let mut disk = SimulatedDisk::new(&mut rng);
    let mut scheduler = DeterministicScheduler::new(&mut rng);

    let cluster = spawn_cluster(&scheduler, &network, &disk, &clock);

    for _ in 0..steps {
        scheduler.run_next_event(&mut clock);
        inject_faults(&mut rng, &mut network, &mut disk);
        check_invariants(&cluster);
    }
}
```

**Step 2: Replace real I/O with simulated I/O**

```
Real I/O path:        App → syscall → kernel → hardware
Simulated I/O path:   App → simulated interface → in-process event queue
```

The simulated network delivers messages via an in-memory priority queue ordered by simulated delivery time. The simulated disk writes to in-memory buffers with optional fault injection (torn writes, corruption).

**Step 3: Simulated clock**

Time does not advance with the wall clock. Instead, time advances discretely when:
- The scheduler has no ready events and jumps to the next scheduled event
- A `sleep()` call schedules a wake-up event at `now + duration`

This enables **time compression**: hours of simulated operation in seconds of real time.

**Step 4: Single-threaded execution**

All "nodes" run as coroutines/actors in a single thread. The scheduler controls which coroutine runs next using the seeded PRNG. This eliminates OS thread scheduling non-determinism entirely.

### 3.2 Fault Injection

A typical DST framework injects:

```
Category          Faults Injected
─────────────     ──────────────────────────────────────────
Network           Message delay, drop, reorder, duplicate
                  Connection reset, timeout
                  Network partition (asymmetric, symmetric)
                  Bandwidth throttling
Disk              Read/write latency spikes
                  Torn writes (partial page writes)
                  Bit corruption (bit rot)
                  Space exhaustion (ENOSPC)
                  I/O errors (EIO)
Process           Crash at any point (SIGKILL simulation)
                  Slow process (freeze for N seconds)
                  Reboot with recovery
Clock             Clock skew between nodes
                  Clock jumps (NTP correction)
                  Monotonic clock violations
Datacenter        Full DC failure (all nodes in a DC crash)
                  DC network isolation
```

### 3.3 Invariant Checking (Test Oracles)

DST is only useful if you can check that the system behaves correctly. Common invariant classes:

**Safety properties** (nothing bad happens):
- Linearizability: operations appear to execute atomically in some order
- No data loss: acknowledged writes must be readable after recovery
- No duplicate operations: idempotency guarantees hold
- Consistency: application-level invariants (e.g., account balances sum correctly)

**Liveness properties** (something good eventually happens):
- The system eventually makes progress after faults heal
- Pending operations eventually complete or fail
- Leader election eventually succeeds

### 3.4 Shrinking and Debugging

When a seed produces a failure:

1. **Replay**: re-run with the same seed → identical execution → identical failure
2. **Minimize**: try removing fault injection events to find the minimal reproducer
3. **Inspect**: add logging/breakpoints and re-run — the execution is identical
4. **Time-travel**: step forward and backward through the event log

This is qualitatively different from debugging non-deterministic systems where "just run it again" may never reproduce the bug.

---

## 4. Antithesis: DST for Everyone

### 4.1 The Company

Founded by **Will Wilson** (CEO, FoundationDB co-creator) and **Dave Scherer** (FoundationDB's former chief architect) in 2018. Launched publicly in 2024. Based in Virginia.

**Core thesis**: FoundationDB proved DST works, but required building a custom language (Flow) and simulator. Antithesis makes DST work for **any software** by operating at the hypervisor level.

### 4.2 The Determinator (Deterministic Hypervisor)

Instead of requiring applications to be rewritten for a simulator, Antithesis runs unmodified software inside a **deterministic hypervisor** built on top of FreeBSD's bhyve.

```
FoundationDB approach:           Antithesis approach:

  App (Flow actors)                App (any language)
      │                                │
  Sim2 (simulated runtime)            OS (Linux, unmodified)
      │                                │
  Host OS                          The Determinator (det. hypervisor)
                                       │
                                   Host OS
```

**How it achieves determinism:**
- Controls CPU instruction scheduling (deterministic virtual CPU)
- Intercepts and replays all I/O (disk, network, time)
- Controls thread scheduling at the hypervisor level
- All entropy (RDRAND, `/dev/urandom`) replaced with seeded PRNG

**Key advantage**: no code changes required. Package your app as Docker containers and Antithesis runs them as-is.

### 4.3 Software Explorer

Antithesis doesn't just replay seeds — it actively **explores** the state space using:
- **Code coverage instrumentation**: detects rarely-executed paths
- **Snapshot-on-interest**: when a rare branch is hit, snapshot state and explore alternative execution paths from that point
- **Branch exploration**: fork execution to try different scheduling decisions
- **Guided fuzzing**: coverage-guided mutation of fault schedules

### 4.4 Sometimes / Always / Never Assertions

Antithesis introduces a three-valued assertion model:

```
always(condition)     // Must be true in every execution (safety)
sometimes(condition)  // Must be true in at least one execution (coverage)
never(condition)      // Must be false in every execution (safety)
```

`sometimes` is the key innovation: it catches **coverage gaps**. If `sometimes(leader_elected_after_partition_heals)` never fires across millions of runs, your test workload isn't exercising that scenario — even though your code handles it.

### 4.5 Notable Users

| Company | What they test | Result |
|---------|---------------|--------|
| CockroachDB | Distributed SQL database | Found a "one-in-a-million" non-idempotent retry bug in Parallel Commits (25 hours of testing, thousands of scenarios) |
| WarpStream | Kafka-compatible streaming (full SaaS) | Found critical data loss bug with microsecond-window race in file-flushing logic (233 seconds vs. 10,000+ CI hours undetected) |
| TigerBeetle | Financial transaction database | Ongoing testing partnership |
| Ramp | Corporate card platform | Financial correctness testing |
| MongoDB | Document database | Correctness testing |
| Jepsen 0.3.10 | Runs Jepsen scenarios inside Antithesis | Deterministic replay of Jepsen tests |

---

## 5. Systems Using DST

### 5.1 FoundationDB (C++, Flow)

- **Approach**: custom language (Flow), actor-level simulation
- **Simulator**: Sim2, replaces Net2 (production runtime)
- **Testing scale**: ~1 trillion CPU-hours cumulative
- **Owner**: Apple (acquired 2015, open-sourced 2018)
- **BUGGIFY**: cooperative fault injection macros throughout codebase
- **Swarm testing**: randomized feature/fault configurations per run

### 5.2 TigerBeetle (Zig, VOPR)

TigerBeetle built DST from day one, inspired by FoundationDB.

**VOPR (Viewstamped Operation Replicator):**
- Named after WOPR from WarGames
- Simulates full cluster: multiple replicas + clients in a single process
- All I/O mocked: network simulator (faults, latency), storage simulator (sector corruption, torn writes)
- State machine checker using AEGIS-128L checksums validates replica consistency

**Scale:**
- 3.3 seconds of VOPR simulation ≈ 39 minutes of real-world testing time (~700x acceleration)
- 10 VOPR simulators run 24/7 on 1,000 CPU cores ("VOPR-1000")
- ~2 millennia of simulated runtime per day

**Key difference from FoundationDB:**
- Written in Zig (no custom language needed — Zig's explicit control flow and comptime provide sufficient abstraction)
- Tests both safety AND liveness (system must make progress, not just avoid errors)
- Single-threaded in production too — no simulation tax for threading

```
VOPR Architecture:
  ┌─────────────────────────────────────────┐
  │          Single Zig Process              │
  │                                          │
  │  ┌────────┐ ┌────────┐ ┌────────┐      │
  │  │Replica1│ │Replica2│ │Replica3│      │
  │  └───┬────┘ └───┬────┘ └───┬────┘      │
  │      │          │          │            │
  │  ┌───┴──────────┴──────────┴───┐        │
  │  │   Network Simulator          │        │
  │  │ (drops, delays, partitions,  │        │
  │  │  reordering, duplication)    │        │
  │  └─────────────┬───────────────┘        │
  │                │                         │
  │  ┌─────────────┴───────────────┐        │
  │  │   Storage Simulator          │        │
  │  │ (torn writes, corruption,    │        │
  │  │  latency, sector faults)     │        │
  │  └─────────────┬───────────────┘        │
  │                │                         │
  │  ┌─────────────┴───────────────┐        │
  │  │   State Checker              │        │
  │  │ (AEGIS-128L checksums,       │        │
  │  │  linearizability, liveness)  │        │
  │  └─────────────────────────────┘        │
  └─────────────────────────────────────────┘
```

### 5.3 Turmoil (Rust, Tokio network simulation)

An importable Rust crate from the Tokio project. Provides deterministic execution by running multiple concurrent hosts within a single thread.

```rust
use turmoil::Builder;

#[test]
fn test_partition_recovery() {
    let mut sim = Builder::new().build();

    sim.host("node-1", || async { /* node logic */ });
    sim.host("node-2", || async { /* node logic */ });
    sim.host("node-3", || async { /* node logic */ });

    // Partition node-1 from the others
    sim.partition("node-1", "node-2");
    sim.partition("node-1", "node-3");

    sim.run().unwrap();

    // Heal the partition
    sim.repair("node-1", "node-2");
    sim.repair("node-1", "node-3");

    sim.run().unwrap();
}
```

**Limitations:**
- Only simulates networking. Filesystem and other I/O are not simulated
- Requires using turmoil's TCP types instead of `tokio::net`
- Does not intercept `libc` calls — cannot control randomness or time at the OS level

### 5.4 MadSim (Rust, full async runtime replacement)

MadSim (Magical Deterministic Simulator) replaces the entire async runtime.

```
Production mode:  App → Tokio Runtime → OS
Simulation mode:  App → MadSim Runtime → Simulated {Network, Time, Random, FS}
```

**Key technique: libc interception.** Overloads `gettimeofday`, `clock_gettime`, `getrandom`, and `sysconf` at link time to control time and randomness at the OS level:

```rust
// MadSim overrides libc symbols at link time
#[no_mangle]
pub unsafe extern "C" fn clock_gettime(
    clk_id: libc::clockid_t,
    tp: *mut libc::timespec,
) -> libc::c_int {
    // Return simulated time instead of real time
    let sim_time = SIMULATOR.with(|s| s.borrow().current_time());
    (*tp).tv_sec = sim_time.as_secs() as i64;
    (*tp).tv_nsec = sim_time.subsec_nanos() as i64;
    0
}
```

**Features:**
- API-compatible with Tokio (change `Cargo.toml`, not code)
- Simulated implementations of tonic (gRPC), etcd-client, rust-rdkafka, aws-sdk-s3
- Multi-node simulation with separate network namespaces in a single process
- BUGGIFY-style fault injection

**Users:** RisingWave (distributed streaming SQL database) uses MadSim extensively, simulating full clusters with Kafka, S3, and etcd mocked.

### 5.5 S2 (Rust, mad-turmoil hybrid)

S2.dev (streaming storage) created `mad-turmoil`, combining MadSim's libc overrides with Turmoil's ergonomic API.

**Key techniques:**
- Override `getrandom`, `getentropy`, `CCRandomGenerateBytes` (macOS) for deterministic randomness
- Override `clock_gettime` using Turmoil's simulated clock
- **Meta-test for determinism**: re-run the same seed twice in CI, diff TRACE-level logs byte-by-byte. Any difference = determinism leak.
- Found 17 bugs (API nuances, protocol errors, deadlocks) before production

### 5.6 Sled (Rust, embedded database)

Tyler Neely's embedded database was an early Rust adopter of simulation-style testing.

**Approach:**
- Model components as state machines communicating via messages
- Deterministic "delivery times" from a seeded PRNG
- Priority queue processes messages in time order
- Combine with quickcheck (property-based testing) for model checking
- Failpoints at every I/O operation for crash testing

**Sled's testing stack (7 layers):**
1. quickcheck model testing on Tree, PageCache, Log
2. proptest model testing on PageTable
3. Linearizability testing
4. Deterministic concurrent model testing
5. Failpoints with model testing at every I/O
6. Crash testing
7. Fuzzing with libfuzzer

Tyler Neely's claim: "Simulators give implementations that Jepsen will not find bugs in."

### 5.7 Polar Signals (Rust, state machine approach)

Instead of using a deterministic runtime, Polar Signals modeled core components as **explicit state machines**:

```rust
enum NodeAction {
    SendMessage { to: NodeId, msg: Message },
    WriteToDisk { key: Key, value: Value },
    SetTimer { duration: Duration, callback: TimerId },
}

trait StateMachine {
    fn handle_event(&mut self, event: Event) -> Vec<NodeAction>;
}

// The simulator drives state machines directly:
fn simulate(seed: u64, steps: usize) {
    let mut rng = StdRng::seed_from_u64(seed);
    let mut nodes: Vec<Box<dyn StateMachine>> = create_cluster();
    let mut event_queue: BinaryHeap<TimedEvent> = BinaryHeap::new();

    for _ in 0..steps {
        let event = event_queue.pop().unwrap();
        let actions = nodes[event.target].handle_event(event.payload);
        for action in actions {
            schedule_action(&mut event_queue, action, &mut rng);
        }
    }
}
```

**Pro:** inherently deterministic — a pure function from (state, event) → (new_state, actions). No runtime hacks, no libc overrides.
**Con:** requires restructuring all code as state machines. Loses ability to test "real" async code paths.

### 5.8 Gosim (Go)

Released December 2024. Brings DST to Go through **source translation**:
- Rewrites Go source to replace concurrency primitives: `go foo()` → `gosimruntime.Go(foo)`
- Implements a subset of Linux syscalls to simulate disk and network
- Deterministic goroutine scheduling

**Status:** Experimental. Limited simulated APIs (files/directories work but not permissions; TCP but not UDP; IP addresses but not DNS).

### 5.9 Hermit (Meta, deterministic Linux container)

Meta's open-source tool for deterministic execution of arbitrary Linux x86_64 programs.

**How it works:**
- Uses the Reverie syscall interception framework
- Intercepts system calls and replaces responses to remove non-determinism
- Controls: thread scheduling, time (`gettimeofday`, `clock_gettime`), random (`getrandom`), PIDs, file descriptors

**Limitations:**
- Determinizes a single process, not a distributed cluster
- Cannot isolate from filesystem changes or external network
- Requires fixed filesystem base image (Docker) and disabled external networking
- **Maintenance mode** since ~2023 — no longer actively developed at Meta

### 5.10 WarpStream (Go, uses Antithesis)

WarpStream (Kafka-compatible streaming, acquired by Confluent) uses Antithesis to test their **entire SaaS platform** — not just the data plane but user signup, metadata management, billing.

**Results (6 wall-clock hours, 280 simulated application hours):**
- Data race in metrics library (found in 233 seconds vs. 10,000+ CI hours undetected)
- Critical data loss bug: network failure + race condition in file-flushing logic with a **microsecond-window** condition

### 5.11 CockroachDB (Go, uses Antithesis)

Found a "one-in-a-million" bug in the Parallel Commits protocol. An RPC failure during a write caused a non-idempotent retry, which raced with transaction recovery logic. Antithesis reproduced it in 25 hours of testing across thousands of scenarios.

The concept of **"demonic nondeterminism"** (Cockroach Labs, 2024): in distributed systems, non-determinism acts as if an adversary ("demon") is choosing the worst possible execution at every step. DST lets you systematically explore these worst-case executions.

---

## 6. Comparison with Other Testing Approaches

### 6.1 DST vs. Jepsen

```
                DST                              Jepsen
                ───                              ─────
Approach:       White-box simulation             Black-box fault injection
                (runs inside simulator)          (runs on real cluster)
Tests:          Real code, simulated env         Real code, real env
Faults:         Simulated (precise control)      Real (iptables, kill -9)
Determinism:    Yes (seed-based)                 No (real OS non-determinism)
Reproducibility:Perfect                          Approximate (retry)
Speed:          1000s of scenarios/sec           1-10 scenarios/hour
Setup:          High (must integrate simulator)  Medium (Docker + Jepsen)
Finds real I/O: No (except Antithesis)           Yes
```

**When to use Jepsen:** to validate that your system works in the real world with real OS, real network stacks, and real disk behavior. Jepsen is a **validation tool**. DST is a **development tool** that finds bugs before deployment.

**Convergence:** Jepsen 0.3.10 added support for running inside Antithesis, enabling deterministic replay of Jepsen scenarios.

### 6.2 DST vs. TLA+ / Model Checking

```
                DST                              TLA+ / Model Checking
                ───                              ────────────────────
Tests:          Implementation (real code)       Specification (model)
Language:       Production language               TLA+, P, Alloy
Exhaustive:     No (random sampling)             Yes (within state bound)
Finds impl bugs:Yes                              No (spec may be correct,
                                                     impl may diverge)
Finds design:   Sometimes                        Yes (finds spec bugs)
```

**Complementary:** TLA+ finds design bugs (the algorithm is wrong). DST finds implementation bugs (the code doesn't match the algorithm). Use both: TLA+ to verify the design, DST to verify the implementation. Amazon describes this dual approach (Newcombe et al., CACM 2015).

### 6.3 DST vs. Property-Based Testing / QuickCheck

```
                DST                              Property-Based Testing
                ───                              ──────────────────────
Scope:          Full system integration          Unit/component level
Inputs:         Fault schedules + workloads      Input data
Shrinking:      Seed-based (limited)             Automatic (QuickCheck)
Finds:          Distributed/concurrent bugs      Logic bugs, edge cases
```

Natural complements. Many DST frameworks use PBT internally. TigerBeetle's VOPR is essentially a property-based test at the system level.

### 6.4 DST vs. Chaos Engineering

```
                DST                              Chaos Engineering
                ───                              ─────────────────
Environment:    Simulated                        Production / staging
Determinism:    Yes                              No
Safety:         Zero risk (simulation)           Real risk
Scope:          Correctness testing              Operational resilience
Faults:         Precise, reproducible            Coarse, probabilistic
Used by:        Developers during development    SREs in production
```

Chaos engineering (Chaos Monkey, LitmusChaos, Gremlin) tests **operational resilience**: can the system survive real failures? DST tests **correctness**: does the system produce correct results under failures? Different questions.

### 6.5 DST vs. Formal Verification (Coq, Dafny, Verus)

```
                DST                              Formal Verification
                ───                              ────────────────────
Guarantee:      Statistical confidence           Mathematical proof
Cost:           High (simulator)                 Very high (proof effort)
Scope:          Full system                      Usually single component
Real code:      Yes                              Depends (Verus = Rust)
```

IronFleet (Dafny), Verdi (Coq), and Verus (Rust) have formally verified distributed systems, but proof effort is enormous and doesn't extend to third-party dependencies.

### 6.6 Summary Matrix

```
           Design  Impl   Ops    Repro-     Cost       Scope
           Bugs    Bugs   Bugs   ducible
─────────  ──────  ─────  ─────  ────────   ─────────  ──────────
DST        Some    Yes    No     Yes        Very High  Full system
Jepsen     No      Yes    Some   No         Medium     Full system
TLA+       Yes     No     No     N/A        High       Design only
PBT        No      Yes    No     Yes        Low        Component
Chaos      No      No     Yes    No         Low        Operations
Formal     Yes     Dep.   No     N/A        Very High  Component
```

---

## 7. Practical Considerations

### 7.1 When DST Is Worth the Investment

**Good fit:**
- Distributed databases (correctness is existential)
- Consensus protocol implementations (Raft, Paxos, VSR)
- Financial transaction systems (money must not be lost or duplicated)
- Storage engines (data durability is paramount)

**Poor fit:**
- Simple CRUD applications
- Prototypes or early-stage projects
- Systems with many external dependencies hard to simulate

**Rule of thumb:** if your system has >3 interacting failure modes (crashes × partitions × corruption × clock skew), DST becomes valuable because the combinatorial explosion makes manual testing inadequate.

### 7.2 What DST Can and Cannot Find

**Can find:**
- Protocol bugs (violated invariants under specific fault sequences)
- Crash recovery bugs (data lost after crash + restart)
- Network partition handling errors (split brain, stale reads)
- Logical race conditions in distributed state machines
- Liveness bugs (system hangs under specific conditions)
- Edge cases in timeout/retry/backoff logic

**Cannot find:**
- Performance bugs (simulation does not model real timing)
- Real hardware bugs (disk firmware, NIC offload, kernel bugs)
- Memory safety bugs (use-after-free, buffer overflow) — use sanitizers
- CPU-level data races (cache coherence) — use ThreadSanitizer
- Bugs in the simulator itself
- Bugs in code outside the simulation boundary

### 7.3 The Simulation Tax

Making code simulatable imposes costs:

**1. Abstraction layers:** all I/O must go through swappable interfaces.

```cpp
// Direct I/O (fast, not simulatable):
ssize_t n = write(fd, buf, len);

// Abstracted I/O (simulatable, slight overhead):
ssize_t n = g_io->write(fd, buf, len);
```

**2. Single-threaded constraint:** if your system relies on multi-threading for performance, the single-threaded simulation may not test the same code paths. FoundationDB and TigerBeetle solve this by being single-threaded in production too.

**3. No real concurrency testing:** CPU-level concurrency bugs (atomics, lock-free data structures) are invisible in single-threaded simulation. Use `loom` (Rust) or ThreadSanitizer separately.

**4. Simulation fidelity:** a simulated disk that doesn't model torn writes will miss torn-write bugs. The simulation is only as good as the fault model.

**5. Seed fragility:** reproducibility only works if the code doesn't change. A code change may cause a previously-failing seed to take a completely different path. Requires continuous re-simulation.

### 7.4 Architecture Patterns

```
Pattern 1: Interface-based swapping (FoundationDB)
  Production:  App → INetwork → Net2 (real TCP)
  Simulation:  App → INetwork → Sim2 (simulated)

Pattern 2: libc interception (MadSim)
  Production:  App → libc → kernel
  Simulation:  App → intercepted libc → MadSim runtime

Pattern 3: Runtime replacement (Turmoil, Gosim)
  Production:  App → tokio/Go runtime → OS
  Simulation:  App → turmoil/gosim runtime → simulated OS

Pattern 4: Hypervisor (Antithesis)
  Production:  App → OS → Hardware
  Simulation:  App → OS → Deterministic Hypervisor

Pattern 5: State machines (Polar Signals, Sled)
  Production:  Event loop drives state machines via real I/O
  Simulation:  Simulator drives state machines via simulated events
```

| Pattern | Language Support | Code Changes | Fidelity | Performance |
|---------|----------------|-------------|----------|-------------|
| Interface-based | Language-specific | High (custom lang) | Highest | Fastest |
| libc intercept | C/Rust (libc-based) | Low | High | Fast |
| Runtime replace | Runtime-specific | Medium | Medium | Fast |
| Hypervisor | Any | None | High | Slowest |
| State machine | Any | Very high (rewrite) | Highest | Fastest |

---

## 8. Key Papers, Talks, and Resources

### 8.1 Foundational Papers

1. **Lamport** - "Time, Clocks, and the Ordering of Events in a Distributed System", CACM, 1978. Logical time, foundational to removing wall-clock dependence.
2. **Fischer, Lynch, Paterson** - "Impossibility of Distributed Consensus with One Faulty Process", JACM, 1985. FLP impossibility — motivates why distributed systems need rigorous testing.
3. **Newcombe, Rath, Zhang, Metz, Kelley** - "How Amazon Web Services Uses Formal Methods", CACM, 2015. AWS's TLA+ and simulation approach.
4. **Chen, Groce, Zhang, Wong, Fern, Eide, Regehr** - "Taming Compiler Fuzzers", PLDI, 2013. Swarm testing — randomizing configurations for coverage.
5. **Alpern, Schneider** - "Defining Liveness", Information Processing Letters, 1985. Safety/liveness decomposition used by FDB test oracles.

### 8.2 FoundationDB Papers

6. **Zhou, Donaghy, Dowling, Dunning, Liao, Meyerovich, Shraer** - "FoundationDB: A Distributed Unbundled Transactional Key Value Store", SIGMOD, 2021. Best Industrial Paper. Definitive paper on FDB including simulation testing.

### 8.3 Key Talks and Articles

7. **Will Wilson** - "Testing Distributed Systems w/ Deterministic Simulation", Strange Loop, 2014. The talk that introduced DST to the broader community.
8. **Will Wilson** - SE Radio Episode 685, "Deterministic Simulation Testing", September 2025.
9. **TigerBeetle team** - "A Descent into the Vortex", blog, February 2025. VOPR internals deep dive.
10. **TigerBeetle team** - "Simulation Testing for Liveness", blog, July 2023. Liveness mode in VOPR.
11. **CockroachDB** - "Antithesis of a One-in-a-Million Bug: Taming Demonic Nondeterminism", blog, 2024.
12. **WarpStream** - "Deterministic Simulation Testing for Our Entire SaaS", blog, March 2024.
13. **Phil Eaton** - "What's the Big Deal About Deterministic Simulation Testing?", blog, August 2024.
14. **Dominik Tornow / Resonate** - "Deterministic Simulation Testing", journal, 2024. Formal treatment.
15. **Pierre Zemb** - "Diving into FoundationDB's Simulation Framework", blog, 2023.
16. **S2.dev** - "Deterministic Simulation Testing for Async Rust", blog, 2025. mad-turmoil hybrid.
17. **RisingWave** - "Deterministic Simulation: A New Era of Distributed System Testing" (Parts 1 & 2), blog, 2023.
18. **Polar Signals** - "Deterministic Simulation Testing in Rust: A Theater of State Machines", blog/FOSDEM 2026.
19. **Antithesis** - "So You Think You Want to Write a Deterministic Hypervisor?", blog, March 2024.
20. **Amplify Partners** - "A DST Primer for Unit Test Maxxers", blog, 2024.

### 8.4 Tools and Frameworks

| Tool | Language | Approach | Status |
|------|----------|----------|--------|
| FoundationDB Sim2 | C++ (Flow) | Actor-level simulation | Production (Apple) |
| TigerBeetle VOPR | Zig | Full-system simulation | Production |
| Antithesis | Any (hypervisor) | Deterministic hypervisor | Commercial |
| Turmoil | Rust | Tokio network simulation | Active OSS |
| MadSim | Rust | Full runtime replacement | Active OSS |
| mad-turmoil | Rust | MadSim + Turmoil hybrid | Active OSS |
| Gosim | Go | Source translation + runtime | Experimental |
| Hermit | Linux x86_64 | Syscall interception (Reverie) | Maintenance mode |
| loom | Rust | Thread interleaving exploration | Active OSS |
| Sled testing | Rust | State machine simulation | Integrated in sled |
| Coyote | C# | Binary rewriting + scheduler | Microsoft Research |

### 8.5 Curated Lists

- [ivanyu/awesome-deterministic-simulation-testing](https://github.com/ivanyu/awesome-deterministic-simulation-testing) — curated DST resources
- [databases.systems/posts/open-source-antithesis-p1](https://databases.systems/posts/open-source-antithesis-p1) — ecosystem analysis
- FoundationDB forums: "Systems using simulation testing" thread

---

## See Also

- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — Consensus algorithms that DST is designed to verify (Paxos, Raft, VR)
- [WAL, Torn Pages, and Disk Reliability](@/notes/database/wal_torn_pages.md) — Crash recovery correctness that simulation testing validates
- [Database Systems Survey](@/notes/database/database_systems.md) — TigerBeetle and FoundationDB, key DST adopters, are surveyed here
- [Rust Low-Level Programming](@/notes/programming/rust_low_level.md) — Turmoil, MadSim, and loom are Rust-based DST frameworks

---

*Last updated: 2026-02-11*
