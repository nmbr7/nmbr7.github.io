+++
title = "Distributed Consensus"
date = 2026-02-10
[taxonomies]
tags = ["paxos", "raft", "pbft", "byzantine", "state-machine-replication", "crdt", "distributed-systems"]
+++


# Distributed Consensus Algorithms

Comprehensive technical reference covering classical and modern consensus protocols, Byzantine fault tolerance, leaderless approaches, and production system implementations.

---

## Table of Contents

1. [Foundations](#foundations)
2. [Classical Algorithms](#2-classical-algorithms)
3. [Raft](#raft)
4. [Byzantine Fault Tolerance](#byzantine-fault-tolerance)
5. [Leaderless and Flexible Approaches](#5-leaderless-and-flexible-approaches)
6. [Production Systems](#production-systems)
7. [Performance Considerations](#7-performance-considerations)
8. [Key Papers](#8-key-papers)

---

## 1. Foundations

### 1.1 The Consensus Problem

The consensus problem requires a set of `n` processes, some of which may fail, to agree on a single value. Formally, a consensus protocol must satisfy:

- **Agreement**: No two correct processes decide differently.
- **Validity**: If a process decides value `v`, then `v` was proposed by some process.
- **Termination**: Every correct process eventually decides some value.

Agreement and validity together form **safety**. Termination is the **liveness** property. The central tension of distributed consensus is that you cannot always have both simultaneously.

### 1.2 FLP Impossibility

The most important impossibility result in distributed computing was established by Fischer, Lynch, and Paterson in 1985 (JACM):

> **Theorem (FLP)**: No deterministic protocol can solve consensus in an asynchronous system if even one process may crash.

The proof uses a *bivalence argument*: starting from an initial configuration where the decision is not yet determined (bivalent), any execution step can be shown to lead to another bivalent configuration, meaning the protocol can always be driven away from a decision.

**Practical implications**:
- Pure asynchronous consensus is impossible; all practical protocols must make timing assumptions.
- Solutions circumvent FLP by using one or more of:
  - **Partial synchrony**: Assume bounds on message delay hold *eventually* (Dwork, Lynch, Stockmeyer, JACM 1988).
  - **Randomization**: Probabilistic algorithms that terminate with probability 1 (Ben-Or, PODC 1983).
  - **Failure detectors**: Oracles that provide (possibly unreliable) information about failures (Chandra & Toueg, JACM 1996). The weakest failure detector sufficient for consensus is Omega (eventual leader).

### 1.3 CAP Theorem

Gilbert and Lynch (SIGACT News, 2002) formalized Brewer's conjecture:

> In a network subject to partitions, a distributed system can provide at most two of: **Consistency** (linearizability), **Availability** (every non-faulty node returns a response), **Partition tolerance**.

Since network partitions are inevitable in real systems, the practical trade-off is between **CP** (sacrifice availability during partitions) and **AP** (sacrifice consistency during partitions).

```
                  C (Consistency)
                 / \
                /   \
               /     \
              / CP    \
             /  systems\
            /___________\
           A ----------- P
        (Availability)   (Partition Tolerance)

        CP systems: ZooKeeper, etcd, Spanner, CockroachDB
        AP systems: Cassandra, DynamoDB, Riak
```

**Important nuance**: CAP is a spectrum, not a binary choice. The PACELC extension (Abadi, Computer, 2012) notes that even when the system is running **N**ormally (no **P**artition), there is a trade-off between **L**atency and **C**onsistency.

### 1.4 Safety vs. Liveness

Every correctness property of a distributed system can be decomposed into a safety property ("nothing bad happens") and a liveness property ("something good eventually happens") -- Alpern & Schneider, 1985.

For consensus:
- **Safety**: No two correct nodes commit different values for the same slot.
- **Liveness**: A value is eventually committed.

Most practical consensus protocols (Paxos, Raft, PBFT) guarantee **safety unconditionally** but guarantee **liveness only under partial synchrony** (when the network stabilizes and a correct leader exists). This is an explicit design choice: it is better to halt than to produce an incorrect result.

### 1.5 System Models

| Model | Timing Assumption | Failures Tolerated | Examples |
|-------|------------------|--------------------|---------|
| Synchronous | Known upper bound on message delay | Crash (f < n/2) | Academic models |
| Asynchronous | No timing assumptions at all | Impossible (FLP) | Theoretical baseline |
| Partially Synchronous | Bounds hold after unknown GST | Crash (f < n/2) | Paxos, Raft, VR |
| Byzantine Partially Synchronous | Bounds hold after unknown GST | Byzantine (f < n/3) | PBFT, HotStuff |

---

## 2. Classical Algorithms

### 2.1 Paxos

#### 2.1.1 Single-Decree Paxos

Published by Lamport in "The Part-Time Parliament" (TOCS, 1998; originally submitted 1990) and later clarified in "Paxos Made Simple" (SIGACT News, 2001).

Single-Decree Paxos decides on a single value. It has three roles (a single node can play multiple roles):

- **Proposer**: Proposes values.
- **Acceptor**: Votes on proposals, stores accepted values.
- **Learner**: Learns the decided value.

**Two-Phase Protocol**:

```
Phase 1: Prepare / Promise
==========================

Proposer                 Acceptors (majority quorum)
   |                         |
   |---Prepare(n)----------->|   Proposer picks ballot number n
   |                         |
   |<--Promise(n, v_max)-----|   Acceptor promises not to accept
   |                         |   ballots < n. Returns highest
   |                         |   accepted value (if any).

Phase 2: Accept / Accepted
==========================

Proposer                 Acceptors (majority quorum)
   |                         |
   |---Accept(n, v)--------->|   v = highest v_max from Phase 1,
   |                         |   or proposer's own value if none.
   |                         |
   |<--Accepted(n, v)--------|   Acceptor accepts if it hasn't
   |                         |   promised a higher ballot.

   Once a majority accepts: value v is CHOSEN.
```

**Key invariant**: If a value `v` has been chosen with ballot `n`, then every higher-numbered ballot that is accepted must also have value `v`. This is enforced by Phase 1: any proposer using a higher ballot will discover `v` from a majority of acceptors.

**Safety**: Guaranteed unconditionally, regardless of message delays, duplicates, or reordering.

**Liveness**: Not guaranteed if two proposers "duel" with alternating ballots (livelock). This motivates the use of a distinguished proposer (leader).

#### 2.1.2 Multi-Paxos

Lamport's original paper describes choosing a sequence of values (a log) by running separate instances of Paxos for each log slot. Multi-Paxos optimizes this:

1. A **stable leader** runs Phase 1 once for a range of slots.
2. Subsequent slots only require Phase 2 (Accept/Accepted), saving one round trip per entry.
3. The leader acts as the sole proposer, eliminating dueling.

```
Multi-Paxos Steady State (leader established):

Client       Leader        Followers
  |            |              |
  |--Request-->|              |
  |            |--Accept(n,v)--->|
  |            |<--Accepted------|
  |            |              |
  |<--Reply----|              |

  Single round-trip in steady state.
```

**Leader election**: Multi-Paxos does not prescribe a specific leader election mechanism. Implementations vary widely. Common approaches:
- Highest-ID node after Phase 1 succeeds becomes leader.
- Separate failure detector / timeout-based election.
- Piggybacking leader information on Prepare messages.

**Log gaps and holes**: Multi-Paxos allows concurrent proposers to claim different slots, which can leave gaps. These must be filled (typically with no-ops) before applying entries.

#### 2.1.3 Paxos Variants

| Variant | Key Idea | Reference |
|---------|----------|-----------|
| **Cheap Paxos** | Uses f+1 main replicas + f cheap witnesses that only activate during recovery | Lamport & Massa, DSN 2004 |
| **Fast Paxos** | Clients send directly to acceptors; 1 RTT in fast path but requires larger quorum (2f+1 out of 3f+1) | Lamport, Distributed Computing, 2006 |
| **Generalized Paxos** | Exploits commutativity of operations to avoid conflict resolution | Lamport, MSR-TR, 2005 |
| **Disk Paxos** | Uses shared disks as acceptors | Gafni & Lamport, Distributed Computing, 2003 |

### 2.2 Viewstamped Replication (VR)

Independently developed by Oki and Liskov (PODC, 1988), revisited by Liskov and Cowling (MIT-CSAIL-TR, 2012). VR is conceptually similar to Multi-Paxos but was developed earlier and uses different terminology.

**Core concepts**:
- A **view** identifies a configuration with a designated **primary** and a set of **backups**.
- Each event is stamped with a **viewstamp** = (view-number, op-number).
- The primary assigns order; backups replicate.

**Normal operation**:

```
Client       Primary         Backups (f of 2f+1)
  |            |                |
  |--Request-->|                |
  |            |--Prepare(v,n)->|   v=view, n=op-number
  |            |<--PrepareOK----|
  |            |  (wait for f)  |
  |            |--Commit(n)---->|
  |<--Reply----|                |
```

**View Change Protocol** (triggered when primary is suspected faulty):

1. Backup sends `StartViewChange(v+1)` to all replicas.
2. Upon receiving `f` StartViewChange messages, backup sends `DoViewChange(v+1, log, ...)` to the new primary.
3. New primary waits for `f+1` DoViewChange messages, selects the most up-to-date log, installs it, and sends `StartView(v+1, log)` to all backups.
4. Backups update their state and resume normal operation.

**Key property**: The new primary always has the most complete log because it gathers logs from a quorum during view change. This is equivalent to Paxos Phase 1.

**Comparison with Multi-Paxos**:
- VR prescribes a specific view change protocol; Multi-Paxos leaves leader election open.
- VR explicitly handles state transfer; Paxos focuses on abstract consensus instances.
- VR uses a fixed primary-backup model; Paxos is more flexible about roles.
- Both are equivalent in terms of the fundamental safety guarantees.

---

## 3. Raft

### 3.1 Overview

Designed by Ongaro and Ousterhout, published "In Search of an Understandable Consensus Algorithm" (USENIX ATC, 2014). Raft was explicitly designed for understandability while providing the same guarantees as Multi-Paxos.

**Key design decisions**:
1. **Strong leader**: All log entries flow from leader to followers. Simplifies reasoning.
2. **Leader election**: Randomized timeouts prevent split votes.
3. **Log matching**: If two logs contain an entry with the same index and term, all preceding entries are identical.
4. **Leader completeness**: If a log entry is committed in a given term, it will be present in the logs of all leaders for higher terms.

### 3.2 Server States

```
                  +-----------+
         timeout, |           | receives votes from
         start    |           | majority of servers
         election | Candidate |-------------------+
     +----------->|           |                   |
     |            +-----------+                   |
     |               |    ^                       v
     |   discovers   |    | timeout,          +---------+
     |   current     |    | new election      |         |
     |   leader or   +----+                   | Leader  |----+
     |   new term                             |         |    |
     |            +-----------+               +---------+    |
     +------------|           |<----- discovers server       |
                  | Follower  |        with higher term      |
     +----------->|           |<-----------------------------+
     |            +-----------+   steps down
     |                 |
     +-- on startup ---+
```

### 3.3 Leader Election

Each server maintains a monotonically increasing **term** number. When a follower's election timeout fires:

1. Increments term to `T+1`, transitions to Candidate.
2. Votes for itself.
3. Sends `RequestVote(term=T+1, lastLogIndex, lastLogTerm)` to all peers.
4. Wins if it receives votes from a majority.

**Election restriction**: A server grants its vote only if the candidate's log is at least as up-to-date as its own. "Up-to-date" means: higher last log term, or same last log term with equal or higher last log index.

This restriction ensures the **Leader Completeness Property**: a newly elected leader's log contains all committed entries. Unlike Paxos, the leader does not need to "catch up" after election.

**Randomized timeouts** (typically 150-300ms) break symmetry and prevent repeated split votes. The election timeout must be much larger than the broadcast time:

```
broadcastTime << electionTimeout << MTBF

broadcastTime: ~0.5-20ms (within datacenter)
electionTimeout: 150-300ms (configurable)
MTBF: months to years
```

### 3.4 Log Replication

```
Leader Log:    [1:x<-3] [1:y<-1] [2:x<-5] [3:z<-2] [3:w<-7]
                                            ^committed

Follower A:   [1:x<-3] [1:y<-1] [2:x<-5] [3:z<-2]
Follower B:   [1:x<-3] [1:y<-1] [2:x<-5]
Follower C:   [1:x<-3] [1:y<-1]           (lagging)

AppendEntries RPC carries:
  - term: leader's current term
  - prevLogIndex, prevLogTerm: consistency check
  - entries[]: new log entries
  - leaderCommit: leader's commit index
```

The **consistency check** in AppendEntries ensures the Log Matching Property: if `prevLogIndex` and `prevLogTerm` do not match the follower's log, the follower rejects the request and the leader decrements `nextIndex` and retries (or uses an optimization where the follower returns its conflicting term and earliest index for that term).

**Commit rule**: An entry from the current term is committed once a majority of servers have replicated it. Entries from previous terms are committed indirectly by committing a current-term entry at a higher index (this prevents the subtle "figure 8" problem described in the Raft paper).

### 3.5 Joint Consensus for Membership Changes

Changing cluster membership (adding/removing nodes) is dangerous because different servers may transition at different times, allowing two disjoint majorities.

Raft solves this with **joint consensus** (two-phase approach):

```
Phase 1: C_old -> C_{old,new}
===========================
Leader creates a configuration entry C_{old,new}
Decisions require majorities from BOTH C_old AND C_new

Phase 2: C_{old,new} -> C_new
===========================
Once C_{old,new} is committed, leader creates C_new entry
Decisions now require majority of C_new only

Timeline:
  C_old    |---C_{old,new}---|---C_new---|
           ^                 ^           ^
         proposed         committed   committed
```

**Single-server changes** (Ongaro's dissertation): If membership changes are restricted to adding or removing one server at a time, joint consensus is not needed because any majority of the old configuration overlaps with any majority of the new configuration.

### 3.6 Linearizable Reads

By default, a Raft leader could serve stale reads if it has been deposed without knowing. Two solutions:

**ReadIndex**:
1. Leader records current commit index as `readIndex`.
2. Leader confirms it is still leader by exchanging heartbeats with a majority.
3. Leader waits until its state machine advances past `readIndex`.
4. Leader executes the read and responds.

**LeaseRead** (optimization):
1. Leader maintains a lease based on heartbeat responses.
2. If the lease has not expired, the leader can serve reads without an extra round of heartbeats.
3. Requires bounded clock drift assumption (typically safe within a datacenter).

```
LeaseRead Timeline:

Leader heartbeat to majority at time T
Lease valid until: T + electionTimeout - clockDrift

   T                    T+lease
   |----lease valid-----|
   ^                    ^
   heartbeat ACK        must re-confirm or
   from majority        step down
```

**Follower reads** (etcd implementation): A follower asks the leader for the current commit index via ReadIndex, waits for its own state machine to catch up, then serves the read locally.

### 3.7 Raft vs. Multi-Paxos

| Aspect | Raft | Multi-Paxos |
|--------|------|-------------|
| **Leader election** | Randomized timeout, up-to-date log required | Not specified; any node can attempt |
| **Log completeness** | Leader always has all committed entries | Leader may have gaps; must fill via Phase 1 |
| **Log gaps** | Never occur; entries are contiguous | Gaps possible with concurrent proposers |
| **Commit rule** | Only current-term entries advance commit | Can commit entries from any term |
| **Understandability** | Explicit goal of the design | Notoriously difficult to implement correctly |
| **Flexibility** | More rigid (by design) | More flexible; many valid implementations |
| **Performance** | Comparable to Multi-Paxos in practice | Slightly more optimization headroom |
| **Formal verification** | TLA+ spec by Ongaro | TLA+ spec by Lamport |

Howard and Mortier ("Paxos vs Raft: Have We Reached Consensus on Distributed Consensus?", HotCloud, 2020) argue that Raft's restrictions (no log gaps, leader completeness) make it less flexible but not fundamentally different from Multi-Paxos. The core consensus mechanism is equivalent.

---

## 4. Byzantine Fault Tolerance

### 4.1 PBFT (Practical Byzantine Fault Tolerance)

Published by Castro and Liskov (OSDI, 1999). First BFT protocol practical enough for real systems. Tolerates up to `f` Byzantine faults with `n = 3f + 1` replicas.

**Why 3f+1?**: With `f` Byzantine nodes that can lie:
- Need `2f+1` honest responses to outvote `f` liars.
- But `f` honest nodes might be slow/unreachable.
- Total: `(2f+1) + f = 3f+1`.

**Three-Phase Protocol**:

```
Client    Primary(0)   Replica 1   Replica 2   Replica 3
  |          |            |            |            |
  |--Request->            |            |            |
  |          |            |            |            |
  |     PRE-PREPARE       |            |            |
  |          |---PP(v,n,d)------------>|            |
  |          |---PP(v,n,d)------------------------>|
  |          |---PP(v,n,d)->           |            |
  |          |            |            |            |
  |     PREPARE           |            |            |
  |          |<--P(v,n,d)-|            |            |
  |          |<--P(v,n,d)-------------|            |
  |          |<--P(v,n,d)-------------------------|
  |          |---P(v,n,d)------------>|            |
  |          |            |--P(v,n,d)->|            |
  |          |            |--P(v,n,d)------------->|
  |          |            |            |--P(v,n,d)->|
  |          |            |            |            |
  |     COMMIT (once prepared = 2f+1 matching prepares)
  |          |<--C(v,n)---|            |            |
  |          |<--C(v,n)---------------|            |
  |          |<--C(v,n)----------------------------|
  |          |---C(v,n)-->|            |            |
  |          |---C(v,n)-------------->|            |
  |          |---C(v,n)----------------------------|
  |          |            |            |            |
  |<---Reply-|            |            |            |
  |<---Reply--------------|            |            |
  |<---Reply--------------------------|            |
  |<---Reply--------------------------------------|
  |  (client waits for f+1 matching replies)       |

  v = view number
  n = sequence number
  d = digest of request
```

**Phase guarantees**:
- **Pre-Prepare**: Leader assigns sequence number. Non-binding.
- **Prepare**: Replica broadcasts agreement. Once a replica collects `2f` matching Prepare messages (plus its own), the request is **prepared**.
- **Commit**: Replica broadcasts readiness to execute. Once `2f+1` matching Commit messages are collected, the request is **committed** and can be executed.

**View Change**: When the primary is suspected (timeout on request progress):
1. Replica sends `ViewChange(v+1, ...)` with proof of all prepared requests.
2. New primary collects `2f` ViewChange messages.
3. New primary sends `NewView(v+1, ...)` with the set of operations that must be re-proposed.

**Complexity**: O(n^2) messages per consensus decision (all-to-all in Prepare and Commit phases). This limits scalability to ~20 nodes in practice.

### 4.2 HotStuff

Published by Yin, Malkhi, Reiter, Gueta, and Abraham (PODC, 2019). Key innovation: **linear message complexity** per view and **O(n) view changes**.

**Design goals**:
1. Linear communication complexity (O(n) messages per decision, vs O(n^2) for PBFT).
2. Responsive: commits at actual network speed, not worst-case timeouts.
3. Optimistic responsiveness: leader-driven progress after GST.

**Three-phase commit with threshold signatures**:

```
HotStuff Phases (Basic HotStuff):

Round 1: PREPARE
  Leader -> ALL: Propose(block, QC_high)
  ALL -> Leader: Vote_prepare (if extends from locked QC)
  Leader: Collect n-f votes -> form prepareQC

Round 2: PRE-COMMIT
  Leader -> ALL: prepareQC
  ALL -> Leader: Vote_precommit
  Leader: Collect n-f votes -> form precommitQC

Round 3: COMMIT
  Leader -> ALL: precommitQC
  ALL -> Leader: Vote_commit
  Leader: Collect n-f votes -> form commitQC

Round 4: DECIDE
  Leader -> ALL: commitQC
  ALL: Execute and reply to client

Message pattern (star topology through leader):

           Replica 1
           ^     |
          /       \
  Leader <-------> Replica 2
          \       /
           v     |
           Replica 3

  All communication goes through the leader.
  Threshold signatures aggregate n-f votes into one QC.
```

**Pipelined HotStuff**: Overlaps phases of consecutive blocks. Each block's QC serves double duty as a vote for the current block and a phase advancement for the previous block:

```
Pipelined HotStuff:

Block B1 proposed in view 1
Block B2 proposed in view 2 (B2.QC certifies B1 -> B1 is prepared)
Block B3 proposed in view 3 (B3.QC certifies B2 -> B1 is pre-committed)
Block B4 proposed in view 4 (B4.QC certifies B3 -> B1 is COMMITTED)

"Three-chain commit rule":
  B1 <- B2 <- B3 <- B4
  ^                  ^
  committed       latest

  B1 commits when a three-chain B2->B3->B4 extends from it
  with consecutive view numbers.
```

**View change in HotStuff** is trivially handled: replicas simply send their highest QC to the new leader. No complex state reconciliation. This is the major advantage over PBFT.

**LibraBFT / DiemBFT**: Facebook's (Meta's) Libra/Diem blockchain used a variant of pipelined HotStuff. DiemBFT v4 added further optimizations including two-chain commit rules and improved liveness mechanisms.

### 4.3 Tendermint / CometBFT

Published by Buchman, Kwon, and Milosevic ("The latest gossip on BFT consensus", arXiv, 2018). CometBFT is the actively maintained successor to Tendermint Core.

**Core mechanism**: Round-based BFT consensus with propose/prevote/precommit phases.

```
Tendermint Round Structure:

  Propose -> Prevote -> Precommit -> [Commit or next round]

  Proposer selection: deterministic round-robin (weighted by stake)

  If >2/3 precommit for a block: COMMIT (deterministic finality)
  If timeout expires: move to next round with new proposer
```

**Key properties**:
- **Deterministic finality**: Once committed, a block is final. No forks.
- **Accountability**: If a fork occurs, at least 1/3 of validators must have signed conflicting blocks, which is cryptographically provable.
- **Lock mechanism**: Once a validator prevotes for a block and sees >2/3 prevotes, it "locks" on that block and will not prevote for a different block in subsequent rounds (unless it sees a valid unlock proof).

**Comparison with HotStuff**:
| Aspect | Tendermint/CometBFT | HotStuff |
|--------|--------------------| ---------|
| Communication | O(n^2) gossip | O(n) star through leader |
| Commit latency | 2 rounds | 3 rounds (basic), pipelined |
| View change | Integrated (round transition) | Trivial (send highest QC) |
| Leader rotation | Every round | Configurable |
| Finality | Immediate | Immediate |
| Primary use | Cosmos ecosystem | Blockchain protocols |

---

## 5. Leaderless and Flexible Approaches

### 5.1 EPaxos (Egalitarian Paxos)

Published by Moraru, Andersen, and Kaminsky (SOSP, 2013). A leaderless protocol where any replica can propose, achieving:

- **Optimal commit latency**: 1 RTT in the common case (when there are no conflicts).
- **Uniform load distribution**: No single leader bottleneck.
- **Availability**: Tolerates `f` failures with `2f+1` replicas.

**Fast path** (no conflicts):

```
Any Replica (as command leader)     Other Replicas
      |                                  |
      |---PreAccept(cmd, seq, deps)----->|
      |<--PreAcceptOK(cmd, seq, deps)----|
      |                                  |
      (if all replies agree on seq, deps)
      |---Commit(cmd, seq, deps)-------->|

      1 round trip. Requires fast quorum:
      floor(N/2) + floor((floor(N/2)+1)/2)
      For N=5: fast quorum = 4 (of 5)
```

**Slow path** (conflicts detected -- dependencies or sequence numbers disagree):

```
Command Leader              Other Replicas
      |                          |
      |---PreAccept(cmd)-------->|
      |<--PreAcceptOK (disagree)-|
      |                          |
      (conflict detected)        |
      |---Accept(cmd, merged)--->|  Classic Paxos Phase 2
      |<--AcceptOK---------------|
      |---Commit(cmd)----------->|

      2 round trips.
```

**Dependency tracking**: Each command carries:
- A **sequence number** (for ordering).
- A **dependency set** (commands that must execute before this one).

At execution time, replicas build a dependency graph and execute commands in a topologically sorted order. Strongly connected components (cycles) are broken by sequence number.

```
Dependency Graph Example:

  cmd_A (seq=1, deps={})
    |
    v
  cmd_B (seq=2, deps={A})     cmd_C (seq=2, deps={A})
    |                            |
    +----------+-----------------+
               |
               v
             cmd_D (seq=3, deps={B, C})

Execution order: A -> {B, C} (either order) -> D
```

**Limitations**:
- Recovery is complex: must reconstruct dependency graphs.
- High conflict workloads degrade to slow path frequently.
- Dependency graph execution is more complex than a simple log.
- Implementation complexity is substantially higher than Raft.

Moraru et al. showed EPaxos outperforms Multi-Paxos significantly in WAN deployments with low-conflict workloads because any replica can act as leader for its local clients.

### 5.2 Flexible Paxos

Published by Howard, Malkhi, and Spiegelman (OPODIS, 2016). Key insight: Paxos only requires that Phase 1 quorums (Prepare) and Phase 2 quorums (Accept) **intersect**. They do not each need to be a majority.

**Classic Paxos requirement**:
- Phase 1 quorum: majority (> n/2)
- Phase 2 quorum: majority (> n/2)
- Intersection guaranteed because two majorities always overlap.

**Flexible Paxos relaxation**:
- Phase 1 quorum size: Q1
- Phase 2 quorum size: Q2
- Requirement: Q1 + Q2 > n (they must intersect)

**Practical implications**:

```
Example with n=5 replicas:

Classic:  Q1=3, Q2=3  (standard majority)
Option A: Q1=4, Q2=2  (fast writes, slower recovery)
Option B: Q1=5, Q2=1  (write to single node! recovery reads all)

Option A is useful when:
- Writes are frequent (Q2=2 means write to 2 of 5)
- Leader failures are rare (Q1=4 is only used during election)
```

This insight has been adopted in several systems. WPaxos (Ailijiang et al., 2017) uses flexible quorums for wide-area deployments, stealing objects to the nearest leader's zone.

### 5.3 CRDTs as an Alternative to Consensus

Conflict-free Replicated Data Types (Shapiro et al., SSS, 2011) provide **strong eventual consistency** without consensus:

| Aspect | Consensus (Paxos/Raft) | CRDTs |
|--------|----------------------|-------|
| Consistency | Linearizable (strong) | Strong eventual (weaker) |
| Availability | Sacrificed during partitions | Always available |
| Latency | At least 1 RTT to quorum | Local operation, async merge |
| Operations | Arbitrary (total order) | Must be commutative/monotonic |
| Conflict resolution | Prevention (total ordering) | Automatic (mathematical) |
| Use cases | State machine replication | Collaborative editing, counters, sets |

CRDTs are not a replacement for consensus but rather complement it. Many production systems use both: consensus for metadata and coordination, CRDTs for data that can tolerate eventual consistency (e.g., Redis CRDT for cross-datacenter counters, Riak for distributed sets).

---

## 6. Production Systems

### 6.1 etcd (Raft)

etcd is the key-value store backing Kubernetes. Uses a straightforward Raft implementation in Go.

**Key implementation details**:
- Single Raft group for the entire keyspace.
- WAL for durable log storage, boltdb (now bbolt) for state machine snapshots.
- Supports learner nodes (non-voting replicas for catching up).
- Implements PreVote extension to prevent disruptive re-elections from partitioned nodes.
- ReadIndex and LeaseRead for linearizable/serializable reads.
- Typical cluster size: 3 or 5 nodes.

**Limitations**: Single Raft group means etcd does not scale horizontally for write throughput. Recommended for metadata/coordination, not high-throughput data storage.

### 6.2 ZooKeeper (ZAB)

ZooKeeper Atomic Broadcast (Junqueira, Reed, and Serafini, DSN, 2011) is a crash-recovery atomic broadcast protocol purpose-built for ZooKeeper.

**ZAB phases**:
1. **Discovery**: Find the most up-to-date replica (highest zxid -- (epoch, counter)).
2. **Synchronization**: New leader syncs followers to its state.
3. **Broadcast**: Leader proposes, followers ACK, leader commits once a majority ACKs.

```
ZAB Broadcast Phase:

Client -> Leader: write(path, data)
Leader -> Followers: PROPOSAL(zxid, txn)
Followers -> Leader: ACK(zxid)
Leader (on majority ACKs): COMMIT(zxid)
Leader -> Followers: COMMIT(zxid)

zxid = (epoch << 32) | counter
  epoch increments on each new leader election
  counter increments per transaction within an epoch
```

**Differences from Paxos**:
- ZAB guarantees **FIFO ordering** of all proposals from a leader; Paxos slots can be filled out of order.
- ZAB recovery ensures the new leader has all committed AND all proposed-but-uncommitted transactions from the previous leader (prefix property).
- Epoch-based sequencing simplifies reasoning about leader transitions.

**Production characteristics**:
- Typical ensemble: 3 or 5 nodes.
- All writes go through the leader.
- Reads can be served by any node (session consistency) or by the leader (linearizable).
- Watches provide event notifications without polling.

### 6.3 CockroachDB (Multi-Raft)

CockroachDB uses a Multi-Raft architecture where each Range (~512MB of contiguous key-value data) is a separate Raft group.

**Architecture**:

```
CockroachDB Node 1         CockroachDB Node 2
+---------------------+    +---------------------+
| Range 1 (Leader)    |    | Range 1 (Follower)  |
| Range 2 (Follower)  |    | Range 2 (Leader)    |
| Range 3 (Follower)  |    | Range 3 (Follower)  |
| ...                 |    | ...                 |
| Raft Transport      |<-->| Raft Transport      |
| (coalesced per-node)|    | (coalesced per-node)|
+---------------------+    +---------------------+
```

**Key optimizations**:
- **Coalesced heartbeats**: Instead of per-Range heartbeats, a single heartbeat per node pair carries heartbeat information for all Ranges on that connection.
- **Batched Raft processing**: Multiple Ranges' Raft state machines are driven in a single event loop tick, with disk writes batched via Pebble WriteBatch.
- **Joint consensus**: CockroachDB implements joint consensus for Range rebalancing (adding/removing replicas) to maintain availability during region failures.
- **Non-voting replicas**: Used in multi-region deployments for serving follower reads without participating in quorum.
- **Parallel commits**: Pipelining transaction intents with consensus writes so that the 2PC commit and Raft replication overlap.
- **Delegated snapshots**: A Raft follower can send snapshots on behalf of the leader, reducing leader bandwidth consumption.

### 6.4 TiKV (Multi-Raft)

TiKV is the distributed key-value storage layer for TiDB. Written in Rust. Uses the `raft-rs` library (a port of etcd's Raft implementation to Rust).

**Architecture**:
- Data is split into **Regions** (~96MB by default), each a Raft group.
- PD (Placement Driver) manages metadata, schedules Region split/merge/rebalance.
- Each TiKV node runs many Raft groups concurrently.

```rust
// Simplified TiKV Raft processing loop (conceptual)
fn on_raft_tick(&mut self) {
    for region in &mut self.regions {
        // Drive the Raft state machine forward
        region.raft_group.tick();

        // Process any ready state
        if region.raft_group.has_ready() {
            let ready = region.raft_group.ready();

            // Persist entries and hard state to RocksDB (raft engine)
            self.raft_engine.append(&ready.entries);
            self.raft_engine.put_hard_state(&ready.hs);

            // Send messages to other nodes
            for msg in ready.messages {
                self.transport.send(msg);
            }

            // Apply committed entries to state machine (kv engine)
            for entry in ready.committed_entries {
                self.apply_to_kv_engine(entry);
            }

            region.raft_group.advance(ready);
        }
    }
}
```

**Key features**:
- **Region split/merge**: Regions auto-split when exceeding size threshold and merge when too small.
- **Leader transfer**: PD can transfer Raft leadership for load balancing.
- **Learner replicas**: Used during Region rebalancing to avoid reducing quorum size before the new replica catches up.
- **Prevote**: Prevents disruptive elections from partitioned nodes.
- **Joint consensus**: For safe membership changes.
- **Raft Engine**: Dedicated storage engine for Raft logs (migrated from RocksDB to a custom engine for better write performance).

### 6.5 Google Spanner (Paxos + TrueTime)

Described in Corbett et al. (OSDI, 2012). Uses Paxos for replication and TrueTime for globally consistent timestamps.

**Architecture**:

```
Universe
  |
  +-- Zone 1 (datacenter)
  |    +-- Spanserver 1
  |    |    +-- Tablet 1 -> Paxos Group -> {Leader, Follower, Follower}
  |    |    +-- Tablet 2 -> Paxos Group -> {Follower, Leader, Follower}
  |    +-- Spanserver 2
  |         +-- ...
  +-- Zone 2 (datacenter)
  |    +-- ...
  +-- Zone 3 (datacenter)
       +-- ...

Each tablet is replicated across zones via Paxos.
Leader handles writes; any replica can serve stale reads.
```

**TrueTime integration**:
- TrueTime API: `TT.now()` returns interval `[earliest, latest]` with bounded uncertainty (typically <1ms in 99th percentile).
- Uses GPS receivers and atomic clocks in every datacenter with distinct failure modes.
- **Commit wait**: After assigning timestamp `s` to a transaction, the leader waits until `TT.after(s)` is true before releasing the commit. This ensures external consistency (linearizability of transactions).

**Transaction protocol**: Two-phase commit (2PC) across Paxos groups, with Paxos replication within each participant group. The 2PC coordinator is itself Paxos-replicated for fault tolerance.

```
Cross-shard Transaction:

  Coordinator (Paxos Group A leader)
       |
       |--- Prepare --> Participant (Paxos Group B leader)
       |                      |
       |                      |--> Paxos replicate prepare
       |                      |<-- majority ACK
       |                      |
       |<-- Prepared ---------|
       |
       |--> Assign commit timestamp s
       |--> Wait until TT.after(s) (commit wait)
       |
       |--- Commit(s) --> Participant B leader
       |                      |
       |                      |--> Paxos replicate commit
       |                      |<-- majority ACK
       |                      |
       |<-- Committed --------|
       |
       |--> Paxos replicate commit locally
       |--> Respond to client
```

### 6.6 Kafka KRaft

KIP-500 introduced KRaft (Kafka Raft), replacing ZooKeeper for Kafka metadata management.

**Key design decisions**:
- **Event-sourced metadata**: All metadata (topics, partitions, brokers, ACLs) is stored as an event log managed by a Raft quorum of controller nodes.
- **Variant of Raft**: Not a textbook Raft implementation. Uses an event-based variant optimized for Kafka's specific needs.
- **Controller quorum**: Typically 3 or 5 dedicated controller nodes. One is the active controller (Raft leader); others are hot standbys.
- **Metadata propagation**: Brokers fetch metadata from the controller quorum, similar to Raft log replication but one-directional.

**Benefits over ZooKeeper**:
- Eliminates the operational complexity of running a separate ZooKeeper ensemble.
- Dramatically increases partition scalability (millions of partitions vs. hundreds of thousands with ZooKeeper).
- Faster controller failover (in-process Raft vs. external ZooKeeper session timeout).
- Single security model (Kafka-native authentication and authorization).

### 6.7 FoundationDB (Active Disk Paxos Variant)

Described in Zhou et al. (SIGMOD, 2021). FoundationDB uses a unique architecture with clear separation between control plane and data plane.

**Control plane (Paxos)**:
- **Coordinators** form a Paxos group that stores critical cluster metadata (the current configuration).
- Uses Active Disk Paxos, an extension of Disk Paxos (Gafni & Lamport, 2003).
- Coordinators elect a **ClusterController**, which in turn recruits processes for various roles.

**Data plane (not consensus-based)**:
- Does NOT use quorum-based replication for data. Instead, uses `f+1` replicas with **eager failure detection and reconfiguration**.
- Transaction processing uses OCC (Optimistic Concurrency Control) with MVCC.
- If any replica fails, the entire transaction system is reconfigured rather than relying on quorum masking.

```
Control Plane:
  Coordinators (Paxos) -> ClusterController -> recruits:
    - Sequencer (timestamp oracle)
    - Proxies (transaction processing)
    - Resolvers (conflict detection)
    - LogServers (WAL)
    - StorageServers (data)

Data Plane:
  Client -> Proxy -> Resolver -> LogServers (f+1 replicas)
                                     |
                                     v
                              StorageServers (async)
```

**Key insight**: By eschewing quorum replication for data and instead detecting failures quickly and reconfiguring, FoundationDB needs only `f+1` replicas (not `2f+1`) to tolerate `f` failures. The trade-off is brief unavailability during reconfiguration (typically <5 seconds).

---

## 7. Performance Considerations

### 7.1 Batching

Batching amortizes the cost of consensus over multiple client requests.

```
Without batching:
  Request 1 -> Propose -> Accept -> Commit (1 RTT per request)
  Request 2 -> Propose -> Accept -> Commit
  Request 3 -> Propose -> Accept -> Commit

With batching:
  {Request 1, 2, 3} -> Propose -> Accept -> Commit (1 RTT for 3 requests)
```

Santos and Schiper ("Optimizing Paxos with Batching and Pipelining", 2012) showed batching can improve throughput by 10x+ under high load. The trade-off is increased latency at low load (must wait for batch to fill or timeout).

**Implementation strategy**: Use an adaptive batch size -- batch aggressively under high load, immediately dispatch under low load:

```rust
// Adaptive batching pseudocode
struct BatchAccumulator {
    entries: Vec<LogEntry>,
    max_batch_size: usize,
    max_wait: Duration,
    last_flush: Instant,
}

impl BatchAccumulator {
    fn maybe_flush(&mut self) -> Option<Vec<LogEntry>> {
        let should_flush = self.entries.len() >= self.max_batch_size
            || self.last_flush.elapsed() >= self.max_wait
            || (self.entries.len() > 0 && self.no_pending_requests());

        if should_flush && !self.entries.is_empty() {
            self.last_flush = Instant::now();
            Some(std::mem::take(&mut self.entries))
        } else {
            None
        }
    }
}
```

### 7.2 Pipelining

Pipelining allows the leader to send multiple AppendEntries (or Accept) messages without waiting for the previous one to be acknowledged.

```
Without pipelining (sequential):
  Leader: Send(entry 1) -> Wait ACK -> Send(entry 2) -> Wait ACK -> ...
  Throughput limited by: 1 / RTT

With pipelining (window = 3):
  Leader: Send(entry 1) -> Send(entry 2) -> Send(entry 3) -> Wait ACK(1)...
  Throughput limited by: window / RTT
```

CockroachDB uses transaction pipelining to overlap Raft consensus with transaction processing: write intents are replicated via Raft in parallel, and the transaction only waits for all replications to complete at commit time.

### 7.3 Parallel Commits

CockroachDB's parallel commit protocol eliminates a round trip from the transaction commit path:

```
Traditional 2PC + Raft:
  Write intents (Raft replicated) -> Commit record (Raft replicated) -> Resolve intents
  3 synchronous Raft rounds

Parallel Commits:
  Write intents + Commit record (all Raft-replicated in parallel)
  -> Resolve intents (async)
  1 synchronous Raft round (all in parallel)
```

The commit record is written as a STAGING record that references all in-flight writes. The transaction is implicitly committed once all referenced writes are durably replicated.

### 7.4 PreVote

The PreVote extension (Ongaro's PhD thesis) prevents a partitioned node from disrupting the cluster upon reconnection.

**Problem**: A partitioned follower repeatedly increments its term. When it rejoins, its high term forces the current leader to step down, causing an unnecessary election.

**Solution**: Before starting a real election, a candidate sends a PreVote request. Other nodes respond based on whether they would vote for the candidate, but do NOT update their own term. Only if the PreVote succeeds does the candidate increment its term and run a real election.

```
Without PreVote:
  Partitioned node term: 5 -> 6 -> 7 -> 8 -> 9 ...
  Rejoins cluster (leader at term 5):
  -> Leader sees term 9, steps down
  -> Unnecessary election disruption

With PreVote:
  Partitioned node term stays at 5 (PreVote fails, term not incremented)
  Rejoins cluster:
  -> PreVote fails (leader is healthy, nodes reject)
  -> No disruption
```

### 7.5 Witness Replicas

A witness is a lightweight replica that participates in quorum voting but does not store the full state machine. It only stores the Raft log (or just metadata about committed entries).

**Benefits**:
- Reduces storage costs for the third (or fifth) replica.
- Can be placed in a third availability zone cheaply.
- Maintains fault tolerance without full data replication.

**Limitations**:
- Cannot serve reads.
- Cannot become leader (in most implementations).
- If a full replica fails, a witness cannot replace it; a new full replica must be provisioned.

### 7.6 Tail Latency

Consensus protocols are sensitive to tail latency because they must wait for the slowest node in a quorum.

| Technique | Description | Trade-off |
|-----------|-------------|-----------|
| **Hedged requests** | Send to all replicas, use first response | Increased network load |
| **Quorum leases** | Cache quorum decisions for reads | Stale reads during lease |
| **Flexible quorums** | Smaller write quorum (e.g., 2 of 5) | Larger read/recovery quorum |
| **Speculative execution** | Execute before commit, roll back if needed | Wasted work on rollback |
| **Adaptive timeouts** | Dynamically tune election/heartbeat timeouts | Complexity |

### 7.7 Multi-Raft Optimizations Summary

For systems running thousands of Raft groups per node (CockroachDB, TiKV):

```
Optimization             Impact
-------------------------------------------------------------------
Coalesced heartbeats     Reduce heartbeat traffic from O(groups)
                         to O(nodes) per node pair

Batched disk writes      Single fsync for multiple Raft groups'
                         log entries via WriteBatch

Shared transport         Multiplex Raft messages for all groups
                         over a single TCP connection per node pair

Leader balancing         Distribute Raft leadership across nodes
                         to spread write load

Raft log engine          Dedicated storage engine for Raft logs
                         (TiKV moved from RocksDB to custom engine)

Region merge             Combine small Raft groups to reduce per-
                         group overhead

Async apply              Decouple Raft commit from state machine
                         apply for higher throughput
```

---

## 8. Key Papers

### Foundations

1. **Fischer, Lynch, Paterson** - "Impossibility of Distributed Consensus with One Faulty Process", JACM, 1985.
2. **Dwork, Lynch, Stockmeyer** - "Consensus in the Presence of Partial Synchrony", JACM, 1988.
3. **Chandra, Toueg** - "Unreliable Failure Detectors for Reliable Distributed Systems", JACM, 1996.
4. **Gilbert, Lynch** - "Brewer's Conjecture and the Feasibility of Consistent, Available, Partition-Tolerant Web Services", SIGACT News, 2002.
5. **Abadi** - "Consistency Tradeoffs in Modern Distributed Database System Design", Computer (IEEE), 2012.
6. **Alpern, Schneider** - "Defining Liveness", Information Processing Letters, 1985.

### Paxos Family

7. **Lamport** - "The Part-Time Parliament", TOCS, 1998.
8. **Lamport** - "Paxos Made Simple", SIGACT News, 2001.
9. **Lamport** - "Fast Paxos", Distributed Computing, 2006.
10. **Lamport** - "Generalized Consensus and Paxos", MSR Technical Report, 2005.
11. **Lamport, Massa** - "Cheap Paxos", DSN, 2004.
12. **Gafni, Lamport** - "Disk Paxos", Distributed Computing, 2003.
13. **Van Renesse, Altinbuken** - "Paxos Made Moderately Complex", ACM Computing Surveys, 2015.
14. **Chandra, Griesemer, Redstone** - "Paxos Made Live: An Engineering Perspective", PODC, 2007.

### Viewstamped Replication

15. **Oki, Liskov** - "Viewstamped Replication: A New Primary Copy Method to Support Highly-Available Distributed Systems", PODC, 1988.
16. **Liskov, Cowling** - "Viewstamped Replication Revisited", MIT-CSAIL-TR-2012-021, 2012.

### Raft

17. **Ongaro, Ousterhout** - "In Search of an Understandable Consensus Algorithm", USENIX ATC, 2014.
18. **Ongaro** - "Consensus: Bridging Theory and Practice", PhD Thesis, Stanford, 2014.
19. **Howard, Mortier** - "Paxos vs Raft: Have We Reached Consensus on Distributed Consensus?", HotCloud, 2020.

### Byzantine Fault Tolerance

20. **Castro, Liskov** - "Practical Byzantine Fault Tolerance", OSDI, 1999.
21. **Castro, Liskov** - "Practical Byzantine Fault Tolerance and Proactive Recovery", TOCS, 2002.
22. **Yin, Malkhi, Reiter, Gueta, Abraham** - "HotStuff: BFT Consensus with Linearity and Responsiveness", PODC, 2019.
23. **Buchman, Kwon, Milosevic** - "The Latest Gossip on BFT Consensus", arXiv, 2018.
24. **Baudet et al.** - "State Machine Replication in the Libra Blockchain", The Libra Association, 2019.

### Leaderless and Flexible

25. **Moraru, Andersen, Kaminsky** - "There Is More Consensus in Egalitarian Parliaments", SOSP, 2013.
26. **Howard, Malkhi, Spiegelman** - "Flexible Paxos: Quorum Intersection Revisited", OPODIS, 2016.
27. **Ailijiang, Charapko, Demirbas, Mitra** - "WPaxos: Wide Area Network Flexible Consensus", IEEE TPDS, 2020.
28. **Shapiro, Preguica, Baquero, Zawirski** - "Conflict-free Replicated Data Types", SSS, 2011.

### Production Systems

29. **Corbett et al.** - "Spanner: Google's Globally-Distributed Database", OSDI, 2012.
30. **Bacon et al.** - "Spanner: Becoming a SQL System", SIGMOD, 2017.
31. **Hunt, Konar, Junqueira, Reed** - "ZooKeeper: Wait-free Coordination for Internet-scale Systems", USENIX ATC, 2010.
32. **Junqueira, Reed, Serafini** - "Zab: High-performance Broadcast for Primary-backup Systems", DSN, 2011.
33. **Taft et al.** - "CockroachDB: The Resilient Geo-Distributed SQL Database", SIGMOD, 2020.
34. **Huang et al.** - "TiDB: A Raft-based HTAP Database", VLDB, 2020.
35. **Zhou et al.** - "FoundationDB: A Distributed Unbundled Transactional Key Value Store", SIGMOD, 2021.
36. **Kreps, Narkhede, Rao** - "Kafka: A Distributed Messaging System for Log Processing", NetDB Workshop, 2011.

### Performance and Optimizations

37. **Santos, Schiper** - "Optimizing Paxos with Batching and Pipelining", Theoretical Computer Science, 2012.
38. **Mao, Junqueira, Marzullo** - "Mencius: Building Efficient Replicated State Machines for WANs", OSDI, 2008.
39. **Danezis, Kokoris-Kogias, Sonnino, Spiegelman** - "Narwhal and Tusk: A DAG-based Mempool and Efficient BFT Consensus", EuroSys, 2022.

---

## Appendix A: Fault Tolerance Requirements

```
Protocol         Model                Replicas   Tolerated   Commit
                                      Needed     Failures    Latency
---------------------------------------------------------------------------
Paxos            Crash, Partial Sync  2f+1       f crash     2 RTT (1 RTT steady)
Multi-Paxos      Crash, Partial Sync  2f+1       f crash     1 RTT (steady state)
Raft             Crash, Partial Sync  2f+1       f crash     1 RTT (steady state)
VR               Crash, Partial Sync  2f+1       f crash     1 RTT (steady state)
ZAB              Crash, Partial Sync  2f+1       f crash     1 RTT (steady state)
EPaxos           Crash, Partial Sync  2f+1       f crash     1 RTT (fast path)
Flexible Paxos   Crash, Partial Sync  varies     depends     1 RTT (steady state)
PBFT             Byzantine, P.Sync    3f+1       f Byzantine 2 RTT
HotStuff         Byzantine, P.Sync    3f+1       f Byzantine 3 RTT (basic)
Tendermint       Byzantine, P.Sync    3f+1       f Byzantine 2 RTT
FoundationDB     Crash (eager detect) f+1        f crash     1 RTT + reconfig risk
```

## Appendix B: Decision Tree

```
Start
  |
  |-- Need Byzantine fault tolerance?
  |     |
  |     +-- Yes: How many nodes?
  |     |     |
  |     |     +-- < 20: PBFT or Tendermint
  |     |     +-- > 20: HotStuff (linear complexity)
  |     |
  |     +-- No: Continue below
  |
  |-- Single datacenter or geo-distributed?
  |     |
  |     +-- Single DC:
  |     |     |
  |     |     +-- Need simplicity? -> Raft
  |     |     +-- Need max throughput? -> Multi-Paxos with batching
  |     |     +-- Metadata only? -> etcd (Raft) or ZooKeeper (ZAB)
  |     |
  |     +-- Geo-distributed:
  |           |
  |           +-- Low conflict workload? -> EPaxos (any-replica leader)
  |           +-- High conflict? -> Multi-Paxos/Raft with leader in hot region
  |           +-- Need global consistency? -> Spanner-style (Paxos + TrueTime)
  |           +-- Flexible quorum needs? -> WPaxos / Flexible Paxos
  |
  |-- Scale (data volume)?
        |
        +-- Small (metadata): Single Raft/Paxos group
        +-- Large (data): Multi-Raft (CockroachDB/TiKV pattern)
        +-- Very large (unbounded): FoundationDB (f+1 + reconfiguration)
```

---

## See Also

- [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) — How FoundationDB and TigerBeetle verify consensus correctness via simulation
- [Kafka Internals](@/notes/distributed/kafka_internals.md) — KRaft implements Raft for Kafka's metadata quorum
- [Disaggregated Storage](@/notes/database/disaggregated_storage.md) — Aurora, Spanner, PolarDB, and TiKV all use consensus protocols covered here
- [Database Systems Survey](@/notes/database/database_systems.md) — CockroachDB, TiDB, and TigerBeetle rely on consensus protocols for replication
