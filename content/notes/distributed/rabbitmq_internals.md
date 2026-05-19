+++
title = "Rabbitmq Internals"
date = 2026-02-10
[taxonomies]
tags = ["amqp", "quorum-queues", "raft", "flow-control", "erlang", "message-broker"]
+++


# RabbitMQ: Expert-Level Deep Dive

## Table of Contents

1. [Overview](#overview)
2. [Core Architecture](#core-architecture)
   - [Erlang Foundation](#1-erlang-foundation)
   - [AMQP Model Components](#2-amqp-model-components)
   - [Queue Internals](#3-queue-internals)
3. [Message Flow Deep Dive](#message-flow-deep-dive)
4. [Persistence & Durability](#persistence-durability)
5. [Clustering & High Availability](#clustering-high-availability)
   - [Cluster Architecture](#cluster-architecture)
   - [Classic Mirrored Queues (Deprecated)](#classic-mirrored-queues-deprecated)
   - [Quorum Queues (Raft-based)](#quorum-queues-raft-based)
6. [Streams (RabbitMQ 3.9+)](#streams-rabbitmq-3-9)
7. [Flow Control & Backpressure](#flow-control-backpressure)
8. [Dead Letter Exchanges & Message TTL](#dead-letter-exchanges-message-ttl)
9. [Protocol Support & Plugins](#protocol-support-plugins)
10. [Performance Tuning](#performance-tuning)
11. [Monitoring & Observability](#monitoring-observability)
12. [Security](#security)
13. [Operational Patterns](#operational-patterns)
14. [Comparison with Other Message Brokers](#comparison-with-other-message-brokers)
15. [Production Deployment Patterns](#production-deployment-patterns)
16. [Common Anti-Patterns](#common-anti-patterns)

---

## Overview

RabbitMQ is an open-source message broker implementing **AMQP 0-9-1** (Advanced Message Queuing Protocol) with extensions for other protocols. It's built on the **Erlang/OTP** platform, giving it exceptional concurrency, fault-tolerance, and distributed computing capabilities.

---

## Core Architecture

### 1. **Erlang Foundation**

```
┌─────────────────────────────────────────────────────────────┐
│                    Erlang VM (BEAM)                        │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│  │ Process │ │ Process │ │ Process │ │ Process │  ...     │
│  │ (Queue) │ │ (Conn)  │ │ (Chan)  │ │ (Mgmt)  │          │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘          │
│                                                            │
│  • Lightweight processes (~2KB each)                       │
│  • Preemptive scheduling                                   │
│  • No shared state (message passing)                       │
│  • Per-process garbage collection                          │
└─────────────────────────────────────────────────────────────┘
```

**Why Erlang matters:**

- Each queue, connection, and channel runs as an isolated Erlang process
- Millions of concurrent processes possible
- Process crashes don't affect other processes (supervision trees)
- Hot code reloading for zero-downtime upgrades

---

### 2. **AMQP Model Components**

```
Publisher                                              Consumer
    │                                                      ▲
    ▼                                                      │
┌───────┐    Routing Key     ┌─────────┐    Binding    ┌──────┐
│Message│──────────────────▶│ Exchange │───────────────▶│Queue │
└───────┘                    └─────────┘               └──────┘
                                  │
                            Exchange Type
                            determines routing
```

#### **Exchanges (Routing Engines)**

| Type                | Routing Logic                 | Use Case               |
| ------------------- | ----------------------------- | ---------------------- |
| **Direct**          | Exact routing key match       | Point-to-point, RPC    |
| **Fanout**          | Broadcast to all bound queues | Pub/Sub, broadcasts    |
| **Topic**           | Pattern matching (`*`, `#`)   | Selective multicasting |
| **Headers**         | Message header attributes     | Complex routing rules  |
| **Consistent Hash** | Hash-based distribution       | Load distribution      |

```python
# Topic exchange routing example
# Routing key: "stock.nyse.ibm"

"stock.*.*"     # Matches - single word wildcard
"stock.#"       # Matches - zero or more words
"stock.nyse.#"  # Matches
"*.*.ibm"       # Matches
"stock.nasdaq.*"# NO MATCH
```

---

### 3. **Queue Internals**

```
┌─────────────────────────────────────────────────────────────┐
│                     Queue Process                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Message Store                           │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐             │   │
│  │  │RAM Index│  │ Message │  │ Message │             │   │
│  │  │  (ETS)  │  │  Store  │  │  Index  │             │   │
│  │  └─────────┘  └─────────┘  └─────────┘             │   │
│  │       │            │            │                   │   │
│  │       ▼            ▼            ▼                   │   │
│  │  ┌─────────────────────────────────────────────┐   │   │
│  │  │        Persistent Storage (.rdq files)      │   │   │
│  │  └─────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  Queue States: running | idle | flow (backpressure)        │
└─────────────────────────────────────────────────────────────┘
```

#### **Message Lifecycle States**

```
                    ┌─────────┐
                    │ alpha   │ ← In RAM (index + content)
                    └────┬────┘
                         │ memory pressure
                         ▼
                    ┌─────────┐
                    │  beta   │ ← Index in RAM, content on disk
                    └────┬────┘
                         │ more pressure
                         ▼
                    ┌─────────┐
                    │  gamma  │ ← Index in RAM, content on disk
                    └────┬────┘     (pending acks)
                         │
                         ▼
                    ┌─────────┐
                    │  delta  │ ← Everything on disk
                    └─────────┘
```

---

## Message Flow Deep Dive

### **Publishing Path**

```
┌──────────────────────────────────────────────────────────────────┐
│                        PUBLISH FLOW                               │
└──────────────────────────────────────────────────────────────────┘

1. TCP Connection → Channel Multiplexing
   ┌─────────┐     ┌─────────────────────────────────┐
   │ Client  │────▶│ Connection (1 TCP socket)       │
   └─────────┘     │  ├── Channel 1 ─┐               │
                   │  ├── Channel 2  │ lightweight   │
                   │  └── Channel N ─┘ virtual conn  │
                   └─────────────────────────────────┘

2. Message arrives at Channel process
   • Frame parsing (AMQP frames: method, header, body)
   • Content validation

3. Exchange routing
   • Lookup bindings in Mnesia
   • Execute routing algorithm
   • Return list of destination queues

4. Queue enqueue (per destination)
   • Credit flow check (backpressure)
   • Message store write
   • If durable: fsync to disk
   • Index update

5. Publisher Confirms (if enabled)
   • basic.ack sent after persistence guarantee met
```

### **Consuming Path**

```
┌──────────────────────────────────────────────────────────────────┐
│                       CONSUME FLOW                                │
└──────────────────────────────────────────────────────────────────┘

Consumer Registration:
├── basic.consume → Queue process adds consumer
├── Prefetch count (QoS) set per channel/consumer
└── Consumer tag returned

Message Delivery:
┌───────────┐     ┌───────────┐     ┌───────────┐     ┌──────────┐
│   Queue   │────▶│  Channel  │────▶│   TCP     │────▶│ Consumer │
│  Process  │     │  Process  │     │  Socket   │     │          │
└───────────┘     └───────────┘     └───────────┘     └──────────┘
      │                                                      │
      │              Acknowledgment Path                     │
      │◀─────────────────────────────────────────────────────┘
      │
      ▼
 ┌─────────────────────────────────────────────────────────┐
 │ Ack Mode         │ Behavior                             │
 ├───────────────────┼─────────────────────────────────────┤
 │ auto-ack         │ Message removed on send              │
 │ manual ack       │ Wait for basic.ack (single/multiple)│
 │ reject/nack      │ Requeue or dead-letter              │
 └─────────────────────────────────────────────────────────┘
```

---

## Persistence & Durability

### **Storage Architecture**

```
$RABBITMQ_MNESIA_DIR/
├── rabbit@hostname/
│   ├── msg_stores/
│   │   └── vhosts/
│   │       └── <vhost-id>/
│   │           ├── msg_store_persistent/
│   │           │   ├── 0.rdq          # Message bodies
│   │           │   ├── 1.rdq
│   │           │   └── clean.dot      # Clean shutdown marker
│   │           └── msg_store_transient/
│   │               └── *.rdq
│   │
│   ├── queues/
│   │   └── <queue-id>/
│   │       ├── journal.jif           # Queue index journal
│   │       └── <segment>.idx         # Index segments
│   │
│   └── quorum/                       # Quorum queue Raft logs
│       └── <queue-name>/
│           └── *.wal

├── schema.DAT                        # Mnesia schema
├── DECISION_TAB.LOG                  # Mnesia decisions
└── *.DCD, *.DCL                     # Mnesia data
```

### **Durability Guarantees**

```
┌─────────────────────────────────────────────────────────────┐
│              DURABILITY REQUIREMENTS                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Full Durability = ALL of these:                           │
│                                                             │
│  1. Exchange: durable=true                                 │
│  2. Queue: durable=true                                    │
│  3. Message: delivery_mode=2 (persistent)                  │
│  4. Publisher confirms enabled                             │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Message persisted ≠ Message safe                    │   │
│  │ OS may cache writes; use publisher confirms for     │   │
│  │ guaranteed durability after fsync                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Clustering & High Availability

### **Cluster Architecture**

```
                    ┌─────────────────────────────────────┐
                    │          Erlang Distribution        │
                    │         (Full mesh topology)        │
                    └─────────────────────────────────────┘
                           │           │           │
              ┌────────────┴───┐ ┌─────┴─────┐ ┌───┴────────────┐
              │   Node A       │ │   Node B  │ │    Node C      │
              │ (disc node)    │ │(disc node)│ │  (ram node)    │
              ├────────────────┤ ├───────────┤ ├────────────────┤
              │                │ │           │ │                │
              │ ┌────────────┐ │ │┌─────────┐│ │ ┌────────────┐ │
              │ │ Q1 (owner) │ │ ││Q1 mirror││ │ │ Q2 (owner) │ │
              │ └────────────┘ │ │└─────────┘│ │ └────────────┘ │
              │ ┌────────────┐ │ │┌─────────┐│ │ ┌────────────┐ │
              │ │ Q3 mirror  │ │ ││Q2 mirror││ │ │ Q3 (owner) │ │
              │ └────────────┘ │ │└─────────┘│ │ └────────────┘ │
              │                │ │           │ │                │
              │ [Mnesia copy]  │ │[Mnesia]   │ │ [RAM only]     │
              └────────────────┘ └───────────┘ └────────────────┘

Replicated via Mnesia (distributed database):
├── Exchanges
├── Bindings
├── Users/Permissions
├── Vhosts
├── Policies
└── Runtime parameters

NOT replicated (local per node):
├── Message stores
└── Queue processes (unless mirrored/quorum)
```

### **Classic Mirrored Queues (Deprecated)**

```
┌─────────────────────────────────────────────────────────────┐
│              MIRRORED QUEUE ARCHITECTURE                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────────┐                                         │
│   │    Master    │ ← All reads/writes go here              │
│   │    Queue     │                                         │
│   └──────┬───────┘                                         │
│          │ GM (Guaranteed Multicast)                       │
│          │ protocol for replication                        │
│    ┌─────┴─────┬─────────────┐                            │
│    ▼           ▼             ▼                             │
│ ┌──────┐   ┌──────┐     ┌──────┐                          │
│ │Mirror│   │Mirror│     │Mirror│                          │
│ │  1   │   │  2   │     │  N   │                          │
│ └──────┘   └──────┘     └──────┘                          │
│                                                             │
│ Synchronization modes:                                      │
│ • automatic: sync on mirror join (may block)               │
│ • manual: require explicit sync command                    │
│                                                             │
│ ⚠️  DEPRECATED: Use Quorum Queues instead                  │
└─────────────────────────────────────────────────────────────┘
```

### **Quorum Queues (Raft-based)**

```
┌─────────────────────────────────────────────────────────────┐
│                 QUORUM QUEUE (RAFT)                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│         ┌──────────────────┐                               │
│         │     Leader       │ ◄── Handles all operations    │
│         │  (elected via    │                               │
│         │   Raft protocol) │                               │
│         └────────┬─────────┘                               │
│                  │                                          │
│    ┌─────────────┼─────────────┐                           │
│    │             │             │                            │
│    ▼             ▼             ▼                            │
│ ┌──────┐    ┌──────┐     ┌──────┐                         │
│ │Follw │    │Follw │     │Follw │                         │
│ │  1   │    │  2   │     │  N   │                         │
│ └──────┘    └──────┘     └──────┘                         │
│                                                             │
│ Write path:                                                 │
│ 1. Client → Leader                                         │
│ 2. Leader appends to WAL (Write-Ahead Log)                │
│ 3. Replicates to followers                                 │
│ 4. Waits for majority (quorum) ack                        │
│ 5. Commits entry, notifies client                          │
│                                                             │
│ Read path:                                                  │
│ 1. Client → Leader (reads only from leader)                │
│ 2. Leader returns from in-memory state                     │
│                                                             │
│ Leader election:                                            │
│ • Heartbeat timeout triggers election                      │
│ • Candidate requests votes from all members                │
│ • Majority vote wins → new leader                          │
│ • Term number prevents split-brain                         │
│                                                             │
│ Quorum = ⌊N/2⌋ + 1                                        │
│ 3 nodes → quorum of 2 (tolerates 1 failure)               │
│ 5 nodes → quorum of 3 (tolerates 2 failures)              │
│ 7 nodes → quorum of 4 (tolerates 3 failures)              │
└─────────────────────────────────────────────────────────────┘
```

#### **Quorum Queue Features**

```
┌─────────────────────────────────────────────────────────────┐
│           QUORUM vs CLASSIC QUEUE COMPARISON                 │
├──────────────────┬──────────────────┬───────────────────────┤
│    Feature       │   Classic Queue  │   Quorum Queue        │
├──────────────────┼──────────────────┼───────────────────────┤
│ Replication      │ GM protocol      │ Raft consensus        │
│ Data safety      │ Weak guarantees  │ Strong guarantees     │
│ Non-durable msgs │ Supported        │ Always durable        │
│ Exclusive queues │ Supported        │ Not supported         │
│ Message TTL      │ Supported        │ Supported (3.10+)     │
│ Queue TTL        │ Supported        │ Not supported         │
│ Dead lettering   │ Supported        │ Supported (at-least-  │
│                  │                  │ once semantics)       │
│ Priority         │ Supported        │ Not supported         │
│ Lazy mode        │ Supported        │ Always lazy-like      │
│ Poison msg       │ No handling      │ Delivery limit +      │
│ handling         │                  │ dead-letter           │
│ Memory usage     │ Variable         │ Predictable           │
│ Performance      │ Higher single-   │ Higher replication    │
│                  │ node throughput  │ throughput            │
│ Recommended      │ Legacy only      │ All new deployments   │
└──────────────────┴──────────────────┴───────────────────────┘
```

#### **Quorum Queue WAL (Write-Ahead Log)**

```
┌─────────────────────────────────────────────────────────────┐
│                    RAFT WAL STRUCTURE                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  WAL Segments (append-only):                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                    │
│  │ Segment 1│ │ Segment 2│ │ Segment 3│  ← Current         │
│  │ (closed) │ │ (closed) │ │ (active) │                    │
│  └──────────┘ └──────────┘ └──────────┘                    │
│                                                              │
│  Each entry:                                                │
│  ┌─────────┬──────┬──────┬───────────────────┐             │
│  │  Index  │ Term │ Type │    Payload         │             │
│  │  (u64)  │(u64) │(enum)│ (message/command)  │             │
│  └─────────┴──────┴──────┴───────────────────┘             │
│                                                              │
│  Snapshot:                                                   │
│  • Periodic compaction of WAL into snapshot                 │
│  • Old segments deleted after snapshot                      │
│  • Configurable via x-max-in-memory-length                 │
│                                                              │
│  Recovery:                                                   │
│  1. Load latest snapshot                                    │
│  2. Replay WAL entries after snapshot                       │
│  3. Rejoin cluster, catch up from leader                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Streams (RabbitMQ 3.9+)

### **Stream Architecture**

```
┌─────────────────────────────────────────────────────────────┐
│                 RABBITMQ STREAMS                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Append-only log (Kafka-like semantics):                    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ Offset: 0   1   2   3   4   5   6   7   8   ...    │    │
│  │        ┌───┬───┬───┬───┬───┬───┬───┬───┬───┐       │    │
│  │        │M0 │M1 │M2 │M3 │M4 │M5 │M6 │M7 │M8 │ ...  │    │
│  │        └───┴───┴───┴───┴───┴───┴───┴───┴───┘       │    │
│  │                  ▲           ▲           ▲           │    │
│  │                  │           │           │           │    │
│  │              Consumer A  Consumer B  Consumer C      │    │
│  │              (offset 2)  (offset 5)  (offset 8)     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Key differences from queues:                               │
│  • Messages NOT removed after consumption                   │
│  • Multiple consumers read independently via offsets        │
│  • Time-based or size-based retention policies              │
│  • Non-destructive consumption (re-read at any time)       │
│  • Massively higher throughput than queues                  │
└─────────────────────────────────────────────────────────────┘
```

### **Stream Internal Storage**

```
Segment-based storage on disk:

┌─────────────────────────────────────────────────────────────┐
│  Stream Directory: /var/lib/rabbitmq/stream/<vhost>/<name>/ │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ Segment 0    │  │ Segment 1    │  │ Segment 2    │      │
│  │ 00000000.log │  │ 00050000.log │  │ 00100000.log │      │
│  │ 00000000.idx │  │ 00050000.idx │  │ 00100000.idx │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│   (oldest)                              (newest/active)     │
│                                                              │
│  Segment structure:                                         │
│  ┌──────────────────────────────────────────────┐           │
│  │ Chunk Header │ Compressed Batch │ Chunk │ ... │           │
│  │              │ of messages      │       │     │           │
│  └──────────────────────────────────────────────┘           │
│                                                              │
│  Index: sparse offset → file position mapping               │
│                                                              │
│  Retention policies:                                        │
│  • x-max-length-bytes: total stream size limit              │
│  • x-max-age: time-based (e.g., "7D", "24h")              │
│  • x-stream-max-segment-size-bytes: per-segment size       │
└─────────────────────────────────────────────────────────────┘
```

### **Stream Protocol**

```
┌─────────────────────────────────────────────────────────────┐
│              STREAM PROTOCOL (Binary, port 5552)             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Dedicated binary protocol (NOT AMQP) optimized for:       │
│  • High throughput publishing                               │
│  • Efficient offset-based consumption                       │
│  • Sub-batch compression                                    │
│                                                              │
│  Frame format:                                              │
│  ┌──────────┬──────────┬──────────────────────────────┐     │
│  │ Size(u32)│ Key(u16) │ Version(u16) │ Payload       │     │
│  └──────────┴──────────┴──────────────────────────────┘     │
│                                                              │
│  Publisher flow:                                            │
│  1. DeclarePublisher (get publisher ID)                     │
│  2. Publish (batch of messages with publishing ID)          │
│  3. PublishConfirm / PublishError (async)                   │
│                                                              │
│  Consumer flow:                                             │
│  1. Subscribe (stream, offset spec, credit)                 │
│  2. Deliver (chunks of messages, pushed by server)          │
│  3. Credit (flow control, consumer grants more credit)      │
│                                                              │
│  Offset specifications:                                     │
│  • first / last / next                                      │
│  • offset(N) - specific numeric offset                      │
│  • timestamp(T) - messages from time T                      │
│                                                              │
│  Accessing via AMQP (interop):                              │
│  • Publish: normal AMQP basic.publish to stream queue       │
│  • Consume: x-stream-offset header in basic.consume        │
│  • Lower throughput than native stream protocol             │
└─────────────────────────────────────────────────────────────┘
```

### **Stream Replication**

```
Leader-Follower replication (similar to Raft but not Raft):

  ┌────────────────┐
  │  Leader         │ ← All writes go here
  │  (one per       │
  │   stream)       │
  └───────┬────────┘
          │ Replicate segments
    ┌─────┼─────────┐
    ▼     ▼         ▼
┌──────┐ ┌──────┐ ┌──────┐
│Replica│ │Replica│ │Replica│
│  1   │ │  2   │ │  N   │
└──────┘ └──────┘ └──────┘

• ISR (In-Sync Replicas) tracking
• Publisher confirms after all ISR members ack
• Leader election on failure
• Consumers can read from replicas (local reads)
```

---

## Flow Control & Backpressure

### **Credit Flow Mechanism**

```
┌─────────────────────────────────────────────────────────────┐
│                   CREDIT FLOW                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Erlang process-level flow control:                         │
│                                                              │
│  ┌──────────┐  credits  ┌──────────┐  credits  ┌────────┐  │
│  │Connection│──────────▶│ Channel  │──────────▶│ Queue  │  │
│  │ Process  │◀──────────│ Process  │◀──────────│Process │  │
│  └──────────┘  grant    └──────────┘  grant    └────────┘  │
│                                                              │
│  How it works:                                              │
│  1. Each sender starts with N credits (default: 200)       │
│  2. Each message sent costs 1 credit                       │
│  3. Receiver grants credits back after processing          │
│  4. When credits = 0 → sender BLOCKS                       │
│                                                              │
│  Chain effect:                                              │
│  Fast publisher → Channel blocks → Connection blocks →     │
│  TCP backpressure → Client library blocks application      │
│                                                              │
│  Visible in management UI as "flow" state on connections   │
└─────────────────────────────────────────────────────────────┘
```

### **Memory Alarms**

```
┌─────────────────────────────────────────────────────────────┐
│                  MEMORY ALARM SYSTEM                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Memory watermark (default: 0.4 = 40% of system RAM):      │
│                                                              │
│  RAM Usage:                                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │░░░░░░░░░░░░░░░░░░░░│▓▓▓▓▓▓▓▓▓│                    │   │
│  │      Normal         │ Paging   │    Alarm!          │   │
│  │                     │ to disk  │  (all publishers   │   │
│  │                     │          │   blocked)         │   │
│  └─────────────────────────────────────────────────────┘   │
│  0%                   40%        50%                  100% │
│                    watermark   paging                       │
│                                threshold                    │
│                                                              │
│  When alarm triggers:                                       │
│  1. ALL publishers blocked (even to non-full queues)       │
│  2. Consumers still allowed (to drain queues)              │
│  3. Queue processes start paging messages to disk          │
│  4. GC triggered on all queue processes                    │
│                                                              │
│  Configuration:                                             │
│  vm_memory_high_watermark.relative = 0.4                   │
│  vm_memory_high_watermark.absolute = 2GB                   │
│  vm_memory_high_watermark_paging_ratio = 0.5               │
│  (paging starts at 50% of watermark = 20% of RAM)         │
└─────────────────────────────────────────────────────────────┘
```

### **Disk Alarms**

```
┌─────────────────────────────────────────────────────────────┐
│                   DISK ALARM SYSTEM                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Free disk space threshold (default: 50MB):                 │
│                                                              │
│  When free space < threshold:                               │
│  1. ALL publishers blocked cluster-wide                    │
│  2. Persistent message writes may fail                     │
│  3. Node enters "disk alarm" state                         │
│                                                              │
│  Configuration:                                             │
│  disk_free_limit.relative = 1.0  (1x RAM size)            │
│  disk_free_limit.absolute = 2GB                            │
│                                                              │
│  Best practice: set to at least 1x the RAM size to allow   │
│  queue paging without hitting disk alarm                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Dead Letter Exchanges & Message TTL

### **Dead Letter Exchange (DLX)**

```
┌─────────────────────────────────────────────────────────────┐
│                 DEAD LETTER EXCHANGE                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Messages are dead-lettered when:                           │
│  • Consumer rejects with requeue=false (basic.reject/nack) │
│  • Message TTL expires                                      │
│  • Queue length limit exceeded (x-max-length)              │
│  • Delivery limit exceeded (quorum queues)                  │
│                                                              │
│                                                              │
│  ┌──────────┐  reject   ┌──────────┐  route   ┌──────────┐ │
│  │  Main    │──────────▶│   DLX    │─────────▶│  DLQ     │ │
│  │  Queue   │           │(exchange)│          │ (queue)  │ │
│  └──────────┘           └──────────┘          └──────────┘ │
│                                                              │
│  Dead-lettered message gets extra headers:                  │
│  x-death[]:                                                 │
│  ├── reason: rejected | expired | maxlen | delivery-limit  │
│  ├── queue: original queue name                             │
│  ├── exchange: original exchange                            │
│  ├── routing-keys: original routing keys                    │
│  ├── count: number of times dead-lettered                  │
│  └── time: timestamp                                        │
│                                                              │
│  Queue arguments:                                           │
│  x-dead-letter-exchange: "my-dlx"                          │
│  x-dead-letter-routing-key: "dead-letters"                 │
│  x-dead-letter-strategy: "at-most-once" | "at-least-once" │
│                                                              │
│  Retry pattern (with TTL):                                  │
│  ┌──────┐ reject ┌─────┐ TTL expires ┌──────┐              │
│  │ Main │───────▶│Retry│────────────▶│ Main │              │
│  │Queue │        │Queue│  (re-route) │Queue │              │
│  └──────┘        └─────┘             └──────┘              │
│              x-message-ttl: 30000 (30s retry delay)        │
└─────────────────────────────────────────────────────────────┘
```

### **TTL (Time-To-Live)**

```
┌─────────────────────────────────────────────────────────────┐
│                      TTL MECHANISMS                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Per-Message TTL:                                        │
│     • Set expiration property on each message               │
│     • Checked only at head of queue (lazy evaluation)      │
│     • Expired messages behind non-expired NOT removed      │
│                                                              │
│  2. Per-Queue TTL:                                          │
│     • x-message-ttl argument on queue declaration          │
│     • Applied uniformly to all messages                     │
│     • More efficient (can remove from head proactively)    │
│                                                              │
│  3. Queue TTL (Queue Expiry):                               │
│     • x-expires argument                                    │
│     • Queue auto-deleted after idle period                  │
│     • Useful for temporary/reply queues                     │
│                                                              │
│  Interaction with DLX:                                      │
│  • Expired messages → dead-lettered (if DLX configured)    │
│  • Otherwise silently dropped                               │
└─────────────────────────────────────────────────────────────┘
```

### **Priority Queues**

```
┌─────────────────────────────────────────────────────────────┐
│                   PRIORITY QUEUES                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Queue argument: x-max-priority: 10 (0-255, keep low)      │
│                                                              │
│  Internal structure (separate sub-queues per priority):     │
│                                                              │
│  Priority 10: ┌───┬───┐                                    │
│  Priority  5: ┌───┬───┬───┬───┐                            │
│  Priority  1: ┌───┬───┬───┬───┬───┬───┬───┐               │
│  Priority  0: ┌───┬───┬───┐                                │
│                                                              │
│  Consumption: highest priority messages delivered first     │
│                                                              │
│  Caveats:                                                   │
│  • Each priority level = separate internal queue            │
│  • More levels = more memory + CPU overhead                │
│  • Recommended: max 5-10 priority levels                   │
│  • NOT supported by quorum queues                          │
│  • Only effective when queue has backlog                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Protocol Support & Plugins

### **Multi-Protocol Support**

```
┌─────────────────────────────────────────────────────────────────┐
│              RABBITMQ PROTOCOL SUPPORT                           │
├──────────────┬──────────┬───────────────────────────────────────┤
│  Protocol    │  Port    │  Use Case                             │
├──────────────┼──────────┼───────────────────────────────────────┤
│ AMQP 0-9-1  │  5672    │ Primary protocol (native)             │
│ AMQP 1.0    │  5672    │ Interop with Azure, ActiveMQ          │
│ MQTT 3.1.1  │  1883    │ IoT devices, lightweight pub/sub      │
│ MQTT 5.0    │  1883    │ IoT with enhanced features            │
│ STOMP       │  61613   │ Simple text-based (WebSocket-friendly)│
│ Stream      │  5552    │ High-throughput stream consumption     │
│ AMQP 0-9-1  │  15672   │ Management HTTP API                   │
│  over WS    │          │                                       │
│ MQTT over WS│  15675   │ Browser-based MQTT                    │
│ STOMP/WS    │  15674   │ Browser-based STOMP                   │
│ Prometheus  │  15692   │ Metrics scraping                      │
└──────────────┴──────────┴───────────────────────────────────────┘
```

### **AMQP 0-9-1 Frame Structure**

```
┌─────────────────────────────────────────────────────────────┐
│                AMQP 0-9-1 FRAME FORMAT                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────┬─────────┬──────┬─────────────────┬───────────┐   │
│  │ Type │ Channel │ Size │    Payload       │ Frame-end │   │
│  │(1B)  │  (2B)   │(4B)  │   (variable)     │  (0xCE)   │   │
│  └──────┴─────────┴──────┴─────────────────┴───────────┘   │
│                                                              │
│  Frame types:                                               │
│  1 = Method frame    (commands: basic.publish, etc.)       │
│  2 = Header frame    (content properties)                  │
│  3 = Body frame      (message payload, up to frame_max)   │
│  8 = Heartbeat frame (keep-alive, empty payload)           │
│                                                              │
│  Message publishing = 3 frames:                             │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐             │
│  │  Method     │ │  Header    │ │   Body     │             │
│  │basic.publish│ │(properties)│ │ (payload)  │             │
│  └────────────┘ └────────────┘ └────────────┘             │
│                                                              │
│  Large messages split across multiple body frames:         │
│  frame_max default = 131072 (128KB)                        │
│                                                              │
│  Connection negotiation:                                    │
│  Client → AMQP0091 protocol header                         │
│  Server → Connection.Start                                 │
│  Client → Connection.Start-Ok (credentials)                │
│  Server → Connection.Tune (frame_max, channel_max, heart) │
│  Client → Connection.Tune-Ok                               │
│  Client → Connection.Open (vhost)                          │
│  Server → Connection.Open-Ok                               │
└─────────────────────────────────────────────────────────────┘
```

### **Plugin Architecture**

```
┌─────────────────────────────────────────────────────────────┐
│                 PLUGIN SYSTEM                                │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  RabbitMQ plugins are Erlang/OTP applications:              │
│                                                              │
│  Core plugins (bundled):                                    │
│  ├── rabbitmq_management       - Web UI + HTTP API         │
│  ├── rabbitmq_management_agent - Per-node metrics agent    │
│  ├── rabbitmq_prometheus       - Prometheus metrics        │
│  ├── rabbitmq_mqtt             - MQTT protocol support     │
│  ├── rabbitmq_stomp            - STOMP protocol support    │
│  ├── rabbitmq_web_stomp        - STOMP over WebSocket     │
│  ├── rabbitmq_web_mqtt         - MQTT over WebSocket      │
│  ├── rabbitmq_amqp1_0          - AMQP 1.0 support        │
│  ├── rabbitmq_stream           - Stream protocol          │
│  ├── rabbitmq_shovel           - Cross-broker msg move    │
│  ├── rabbitmq_federation       - Loose coupling           │
│  ├── rabbitmq_auth_backend_ldap - LDAP authentication    │
│  ├── rabbitmq_auth_backend_http - HTTP-based auth        │
│  ├── rabbitmq_consistent_hash_exchange - Hash exchange   │
│  ├── rabbitmq_delayed_message_exchange - Delayed msgs    │
│  └── rabbitmq_tracing          - Firehose tracing        │
│                                                              │
│  Enable: rabbitmq-plugins enable <plugin>                  │
│  Disable: rabbitmq-plugins disable <plugin>                │
│  List: rabbitmq-plugins list                               │
└─────────────────────────────────────────────────────────────┘
```

### **Federation & Shovel**

```
┌─────────────────────────────────────────────────────────────┐
│              FEDERATION vs SHOVEL                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  FEDERATION:                                                │
│  Loosely coupled, WAN-friendly replication                  │
│                                                              │
│  Datacenter A              Datacenter B                     │
│  ┌────────────────┐       ┌────────────────┐               │
│  │  ┌──────────┐  │  AMQP │  ┌──────────┐  │               │
│  │  │ Exchange │──┼───────┼─▶│ Exchange │  │               │
│  │  │ (upstream)│  │  link │  │(downstream)│  │               │
│  │  └──────────┘  │       │  └──────────┘  │               │
│  └────────────────┘       └────────────────┘               │
│                                                              │
│  • Messages flow upstream → downstream                     │
│  • Configurable via policies                                │
│  • Survives network partitions gracefully                  │
│  • Exchange federation + Queue federation                  │
│                                                              │
│  SHOVEL:                                                    │
│  Simple point-to-point message forwarding                  │
│                                                              │
│  ┌──────────────┐  consume+publish  ┌──────────────┐       │
│  │  Source       │─────────────────▶│ Destination  │       │
│  │  (queue)      │                   │ (exchange)   │       │
│  └──────────────┘                   └──────────────┘       │
│                                                              │
│  • Static or dynamic configuration                          │
│  • Can bridge different protocols/clusters                  │
│  • Reconnects automatically on failure                      │
│  • Can apply ack-mode: on-confirm | on-publish | no-ack   │
│                                                              │
│  When to use which:                                         │
│  • Federation: multi-datacenter, policy-driven, flexible   │
│  • Shovel: simple migration, cross-cluster forwarding      │
└─────────────────────────────────────────────────────────────┘
```

---

## Performance Tuning

### **Key Tuning Parameters**

```
┌─────────────────────────────────────────────────────────────┐
│             PERFORMANCE TUNING GUIDE                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  CONNECTION/CHANNEL TUNING:                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ • Reuse connections (1 per application thread pool) │   │
│  │ • Use channels for concurrency (1 per thread)       │   │
│  │ • heartbeat = 60 (detect dead connections)          │   │
│  │ • frame_max = 131072 (128KB, increase for large msg)│   │
│  │ • channel_max = 2047 (default, rarely needs change) │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  PREFETCH (QoS):                                            │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Prefetch = 0:   Unlimited (dangerous, can OOM)      │   │
│  │ Prefetch = 1:   Fair but slow (1 msg at a time)     │   │
│  │ Prefetch = 10-50: Good for most workloads           │   │
│  │ Prefetch = 100-300: High-throughput consumers       │   │
│  │                                                      │   │
│  │ Rule of thumb:                                       │   │
│  │ prefetch ≈ round_trip_time × processing_rate        │   │
│  │                                                      │   │
│  │ Per-consumer vs per-channel:                         │   │
│  │ basic.qos(prefetch, global=false) → per-consumer    │   │
│  │ basic.qos(prefetch, global=true)  → per-channel     │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  PUBLISHER TUNING:                                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ • Batch publisher confirms (don't wait per-message) │   │
│  │ • Use async confirms with confirm callback          │   │
│  │ • Use persistent messages only when needed          │   │
│  │ • Consider mandatory=false (faster, no returns)     │   │
│  │ • Batch publish: multiple messages per channel      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  QUEUE TUNING:                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ • Keep queues short (target: near 0)                │   │
│  │ • Lazy queues for unavoidable backlogs              │   │
│  │ • x-max-length / x-max-length-bytes for bounds      │   │
│  │ • Multiple queues > one huge queue (parallelism)    │   │
│  │ • Auto-delete for temporary queues                  │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### **Throughput Characteristics**

```
┌─────────────────────────────────────────────────────────────────┐
│          APPROXIMATE THROUGHPUT (single node, modern HW)        │
├──────────────────────┬──────────────┬──────────────────────────┤
│  Scenario            │  Messages/s  │  Notes                   │
├──────────────────────┼──────────────┼──────────────────────────┤
│ Transient, no-ack    │  50K-100K+   │ Fastest, no guarantees   │
│ Transient, manual ack│  30K-60K     │ Ack overhead             │
│ Persistent, confirms │  10K-30K     │ Disk fsync bottleneck    │
│ Quorum queue         │  10K-25K     │ Raft replication cost    │
│ Stream (publish)     │  100K-1M+    │ Append-only, batched     │
│ Stream (consume)     │  100K-1M+    │ Sequential read          │
│ Mirrored (3 nodes)   │  5K-15K      │ GM protocol overhead     │
├──────────────────────┼──────────────┼──────────────────────────┤
│ Message size impact:                                            │
│  1KB message         │ ~30K msg/s   │ ~30 MB/s                 │
│  10KB message        │ ~15K msg/s   │ ~150 MB/s                │
│  100KB message       │ ~3K msg/s    │ ~300 MB/s                │
│  1MB message         │ ~300 msg/s   │ ~300 MB/s (BW-limited)  │
└──────────────────────┴──────────────┴──────────────────────────┘
```

### **Erlang VM Tuning**

```erlang
%% Key BEAM VM settings (rabbitmq-env.conf / advanced.config)

%% Scheduler threads (default: number of CPU cores)
+S 8:8

%% Async thread pool for file I/O
+A 128  %% default: 128, increase for disk-heavy workloads

%% Process limit
+P 1048576  %% default: 1M, increase for very many queues/connections

%% Memory allocator tuning
+MBas aobf  %% Address order best fit
+MHas aobf
+MBlmbcs 512  %% Largest multiblock carrier size
+MHlmbcs 512

%% GC settings
RABBITMQ_SERVER_ERL_ARGS="+hms 233 +hmbs 233"
%% +hms: min heap size (words)
%% +hmbs: min binary heap size
```

---

## Monitoring & Observability

### **Key Metrics**

```
┌─────────────────────────────────────────────────────────────┐
│              CRITICAL METRICS TO MONITOR                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  QUEUE HEALTH:                                              │
│  ├── queue.messages_ready         - Ready for delivery     │
│  ├── queue.messages_unacked       - Delivered, awaiting ack│
│  ├── queue.message_bytes          - Total queue size       │
│  ├── queue.consumers              - Consumer count         │
│  └── queue.consumer_utilisation   - % time consumers busy  │
│                                                              │
│  NODE HEALTH:                                               │
│  ├── node.mem_used                - Erlang memory usage    │
│  ├── node.mem_limit               - Memory alarm threshold │
│  ├── node.disk_free               - Free disk space        │
│  ├── node.disk_free_limit         - Disk alarm threshold   │
│  ├── node.fd_used / fd_total      - File descriptors       │
│  ├── node.sockets_used / total    - TCP sockets            │
│  ├── node.proc_used / proc_total  - Erlang processes       │
│  └── node.run_queue               - Scheduler run queue    │
│                                                              │
│  CONNECTION/CHANNEL:                                        │
│  ├── connection.state             - running | flow | blocked│
│  ├── connection.channels          - Channels per connection│
│  ├── channel.prefetch_count       - QoS setting            │
│  ├── channel.messages_unacked     - In-flight messages     │
│  └── channel.consumer_count       - Consumers per channel  │
│                                                              │
│  THROUGHPUT:                                                │
│  ├── message_stats.publish_rate   - Messages published/s   │
│  ├── message_stats.deliver_rate   - Messages delivered/s   │
│  ├── message_stats.ack_rate       - Acknowledgments/s      │
│  └── message_stats.redeliver_rate - Redeliveries/s        │
│                                                              │
│  RED FLAGS:                                                 │
│  ├── ⚠ messages_ready growing     → Consumers too slow    │
│  ├── ⚠ messages_unacked growing   → Consumers stuck       │
│  ├── ⚠ connection state = flow    → Backpressure active   │
│  ├── ⚠ mem_used > 0.4 × mem_limit→ Approaching alarm     │
│  ├── ⚠ disk_free < 2 × mem_limit → Risk of disk alarm    │
│  └── ⚠ run_queue > 0 sustained   → CPU saturation        │
└─────────────────────────────────────────────────────────────┘
```

### **Prometheus + Grafana Setup**

```
┌─────────────────────────────────────────────────────────────┐
│           PROMETHEUS MONITORING STACK                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  RabbitMQ (port 15692)                                      │
│    │ /metrics (Prometheus endpoint)                         │
│    │ /metrics/per-object (detailed per-queue metrics)       │
│    │                                                        │
│    ▼                                                        │
│  ┌───────────┐     ┌───────────┐     ┌───────────┐         │
│  │Prometheus │────▶│ Grafana   │────▶│ Alerting  │         │
│  │ (scrape)  │     │(dashboards)│     │(PagerDuty)│         │
│  └───────────┘     └───────────┘     └───────────┘         │
│                                                              │
│  Prometheus config:                                         │
│  scrape_configs:                                            │
│    - job_name: rabbitmq                                     │
│      scrape_interval: 15s                                   │
│      static_configs:                                        │
│        - targets:                                           │
│          - rabbit1:15692                                    │
│          - rabbit2:15692                                    │
│          - rabbit3:15692                                    │
│                                                              │
│  Key Grafana dashboards (official):                         │
│  • RabbitMQ-Overview (ID: 10991)                           │
│  • RabbitMQ-Quorum-Queues-Raft (ID: 11340)               │
│  • RabbitMQ-Stream (ID: 14798)                            │
│                                                              │
│  Essential alert rules:                                     │
│  • rabbitmq_alarms_memory_used_watermark > 0               │
│  • rabbitmq_alarms_free_disk_space_watermark > 0           │
│  • rabbitmq_queue_messages_ready > 10000 for 5m            │
│  • rabbitmq_channel_messages_unacked > 5000 for 5m         │
│  • rabbitmq_connections == 0 for 1m                        │
│  • rabbitmq_node_disk_free < 5GB                           │
└─────────────────────────────────────────────────────────────┘
```

### **Management CLI (rabbitmqctl)**

```bash
# Node status
rabbitmqctl status
rabbitmqctl cluster_status
rabbitmqctl environment

# Queue inspection
rabbitmqctl list_queues name messages messages_ready messages_unacknowledged \
    consumers state type

# Connection/channel diagnostics
rabbitmqctl list_connections name state channels send_pend recv_cnt
rabbitmqctl list_channels name consumer_count messages_unacknowledged \
    prefetch_count

# Exchange and binding inspection
rabbitmqctl list_exchanges name type durable auto_delete
rabbitmqctl list_bindings source_name destination_name routing_key

# Health checks
rabbitmq-diagnostics check_running
rabbitmq-diagnostics check_local_alarms
rabbitmq-diagnostics check_port_connectivity
rabbitmq-diagnostics check_virtual_hosts
rabbitmq-diagnostics memory_breakdown

# Quorum queue diagnostics
rabbitmq-diagnostics check_if_node_is_quorum_critical
rabbitmq-queues quorum_status <queue-name>

# Observer (Erlang GUI debugger)
rabbitmq-diagnostics observer  # Requires X11
```

---

## Security

### **Authentication & Authorization**

```
┌─────────────────────────────────────────────────────────────┐
│                   SECURITY MODEL                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  AUTHENTICATION BACKENDS (stackable):                       │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ 1. Internal (Mnesia) ← default                      │   │
│  │ 2. LDAP (rabbitmq_auth_backend_ldap)                │   │
│  │ 3. HTTP (rabbitmq_auth_backend_http)                │   │
│  │ 4. OAuth 2.0 / JWT (rabbitmq_auth_backend_oauth2)   │   │
│  │ 5. Client certificate (x509 CN/SAN)                 │   │
│  │ 6. Cache wrapper (rabbitmq_auth_backend_cache)      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  AUTHORIZATION (per-vhost):                                 │
│  ┌──────────────┬─────────────────────────────────────┐   │
│  │  Permission   │  Operations                         │   │
│  ├──────────────┼─────────────────────────────────────┤   │
│  │  configure   │  queue.declare, exchange.declare,    │   │
│  │              │  queue.bind, exchange.bind           │   │
│  │  write       │  basic.publish                       │   │
│  │  read        │  basic.consume, queue.get,           │   │
│  │              │  queue.purge                          │   │
│  └──────────────┴─────────────────────────────────────┘   │
│                                                              │
│  Permission patterns (regex):                               │
│  rabbitmqctl set_permissions -p /prod user1 \              │
│    "^user1\\..*" "^user1\\..*" "^(user1\\.|shared)\\..*"   │
│   (configure)     (write)       (read)                      │
│                                                              │
│  VHOSTS (virtual hosts):                                    │
│  • Logical grouping / namespace isolation                  │
│  • Separate exchanges, queues, bindings, users             │
│  • No message routing between vhosts                       │
│  • Default vhost: "/"                                       │
└─────────────────────────────────────────────────────────────┘
```

### **TLS/SSL Configuration**

```
┌─────────────────────────────────────────────────────────────┐
│                    TLS SETUP                                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Configuration (rabbitmq.conf):                             │
│                                                              │
│  listeners.ssl.default = 5671                               │
│  ssl_options.cacertfile = /path/to/ca_certificate.pem      │
│  ssl_options.certfile   = /path/to/server_certificate.pem  │
│  ssl_options.keyfile    = /path/to/server_key.pem          │
│  ssl_options.verify     = verify_peer                      │
│  ssl_options.fail_if_no_peer_cert = true                   │
│  ssl_options.versions.1 = tlsv1.3                          │
│  ssl_options.versions.2 = tlsv1.2                          │
│                                                              │
│  Inter-node TLS (cluster encryption):                      │
│  RABBITMQ_CTL_ERL_ARGS="-proto_dist inet_tls"             │
│  ssl_options.depth = 2                                     │
│                                                              │
│  Management UI TLS:                                         │
│  management.ssl.port = 15671                               │
│  management.ssl.cacertfile = /path/to/ca.pem              │
│  management.ssl.certfile   = /path/to/cert.pem            │
│  management.ssl.keyfile    = /path/to/key.pem             │
│                                                              │
│  Certificate rotation:                                      │
│  • Erlang SSL supports runtime cert reloading              │
│  • rabbitmqctl eval 'ssl:clear_pem_cache().'              │
│  • No connection drop needed                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Operational Patterns

### **Network Partitions**

```
┌─────────────────────────────────────────────────────────────┐
│             NETWORK PARTITION HANDLING                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Partition scenario:                                        │
│  ┌────────────┐     ╳ NETWORK ╳     ┌────────────┐         │
│  │   Node A   │     ╳  SPLIT  ╳     │   Node B   │         │
│  │   Node C   │     ╳         ╳     │   Node D   │         │
│  └────────────┘                      └────────────┘         │
│  (Partition 1)                       (Partition 2)          │
│                                                              │
│  Partition handling modes:                                   │
│                                                              │
│  1. ignore (default):                                       │
│     • Both sides continue operating                        │
│     • Queue state DIVERGES (split-brain)                   │
│     • Manual intervention needed to reconcile              │
│     • ⚠ Data loss possible                                │
│                                                              │
│  2. pause_minority:                                         │
│     • Minority side pauses (stops serving clients)         │
│     • Majority side continues normally                     │
│     • Auto-recovers when partition heals                   │
│     • ✓ Recommended for most clusters                     │
│                                                              │
│  3. autoheal:                                               │
│     • Both sides continue during partition                 │
│     • On heal: losing side restarts                        │
│     • Winner determined by most connections/oldest         │
│     • ⚠ Brief downtime on loser during restart            │
│                                                              │
│  Quorum queues handle partitions via Raft:                  │
│  • Partition without leader → queue unavailable            │
│  • Partition with leader → continues for majority side     │
│  • No split-brain possible                                 │
│                                                              │
│  Config: cluster_partition_handling = pause_minority        │
└─────────────────────────────────────────────────────────────┘
```

### **Upgrade Strategies**

```
┌─────────────────────────────────────────────────────────────┐
│                UPGRADE STRATEGIES                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ROLLING UPGRADE (recommended):                             │
│  1. Check version compatibility matrix                     │
│  2. Drain node: rabbitmqctl stop_app                       │
│  3. Upgrade packages on that node                          │
│  4. Start: rabbitmqctl start_app                           │
│  5. Wait for sync, repeat for next node                    │
│                                                              │
│  Order:                                                     │
│  ┌─────┐  ┌─────┐  ┌─────┐                                │
│  │ N1  │  │ N2  │  │ N3  │                                │
│  │v3.12│  │v3.12│  │v3.12│  ← Start state                 │
│  └──┬──┘  └─────┘  └─────┘                                │
│     │ upgrade                                               │
│  ┌──▼──┐  ┌─────┐  ┌─────┐                                │
│  │ N1  │  │ N2  │  │ N3  │                                │
│  │v3.13│  │v3.12│  │v3.12│  ← Mixed version OK            │
│  └─────┘  └──┬──┘  └─────┘                                │
│              │ upgrade                                      │
│  ┌─────┐  ┌──▼──┐  ┌─────┐                                │
│  │ N1  │  │ N2  │  │ N3  │                                │
│  │v3.13│  │v3.13│  │v3.12│                                │
│  └─────┘  └─────┘  └──┬──┘                                │
│                        │ upgrade                            │
│  ┌─────┐  ┌─────┐  ┌──▼──┐                                │
│  │ N1  │  │ N2  │  │ N3  │                                │
│  │v3.13│  │v3.13│  │v3.13│  ← Complete                    │
│  └─────┘  └─────┘  └─────┘                                │
│                                                              │
│  Feature flags:                                             │
│  • New features gated behind feature flags                 │
│  • Enabled cluster-wide after all nodes upgraded           │
│  • rabbitmqctl list_feature_flags                          │
│  • rabbitmqctl enable_feature_flag <flag>                  │
│  • Some flags auto-enabled, some require manual enable     │
└─────────────────────────────────────────────────────────────┘
```

### **Disaster Recovery**

```
┌─────────────────────────────────────────────────────────────┐
│               DISASTER RECOVERY                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  BACKUP (definitions + messages):                           │
│                                                              │
│  1. Definitions (exchanges, queues, users, vhosts):        │
│     rabbitmqctl export_definitions /backup/defs.json       │
│     # or via HTTP API:                                     │
│     curl -u admin:pass http://rabbit:15672/api/definitions │
│     > defs.json                                             │
│                                                              │
│  2. Messages (requires file-level backup):                  │
│     • Stop node cleanly (rabbitmqctl stop_app)             │
│     • Copy $RABBITMQ_MNESIA_DIR                            │
│     • Start node                                            │
│     ⚠ Messages in non-durable queues lost on restart      │
│                                                              │
│  RESTORE:                                                   │
│  1. Fresh install + start                                  │
│  2. Import definitions:                                     │
│     rabbitmqctl import_definitions /backup/defs.json       │
│  3. For messages: restore Mnesia dir before start          │
│                                                              │
│  MULTI-DC PATTERNS:                                         │
│                                                              │
│  Active-Passive:                                            │
│  ┌──────────┐  Federation/  ┌──────────┐                   │
│  │  Active  │  Shovel      │ Passive  │                    │
│  │ DC-East  │─────────────▶│ DC-West  │                    │
│  └──────────┘               └──────────┘                   │
│  (all traffic)              (standby, consumers paused)    │
│                                                              │
│  Active-Active:                                             │
│  ┌──────────┐  Federation  ┌──────────┐                    │
│  │  Active  │◀────────────▶│  Active  │                    │
│  │ DC-East  │  (bidirect)  │ DC-West  │                    │
│  └──────────┘               └──────────┘                   │
│  (local publish/consume, federated exchanges)              │
│  ⚠ No message dedup — app must handle idempotency        │
└─────────────────────────────────────────────────────────────┘
```

---

## Comparison with Other Message Brokers

### **RabbitMQ vs Apache Kafka**

```
┌──────────────────────────────────────────────────────────────────┐
│              RABBITMQ vs KAFKA                                    │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Dimension       │  RabbitMQ        │  Apache Kafka             │
├──────────────────┼──────────────────┼───────────────────────────┤
│ Model            │ Message broker   │ Distributed log           │
│ Protocol         │ AMQP, MQTT, STOMP│ Kafka protocol (binary)   │
│ Routing          │ Exchange-based   │ Topic-partition           │
│                  │ (complex routing)│ (simple key-based)        │
│ Consumption      │ Push (server     │ Pull (consumer polls)     │
│                  │ pushes to        │                           │
│                  │ consumer)        │                           │
│ Message removal  │ On ack (delete)  │ Retention-based (keep)    │
│ Ordering         │ Per-queue FIFO   │ Per-partition FIFO        │
│ Replay           │ No (unless       │ Yes (offset-based)        │
│                  │ streams)         │                           │
│ Throughput       │ 10K-100K msg/s   │ 100K-1M+ msg/s           │
│ Latency          │ Sub-millisecond  │ Low milliseconds          │
│ Scaling          │ Vertical +       │ Horizontal (partitions)   │
│                  │ clustering       │                           │
│ Message size     │ Any (practical   │ Default 1MB max           │
│                  │ limit ~128MB)    │ (configurable)            │
│ Priority         │ Yes              │ No                        │
│ Dead lettering   │ Built-in DLX     │ Custom (topic redirect)   │
│ Delayed delivery │ Plugin           │ No native support         │
│ Request-reply    │ Built-in RPC     │ Possible but awkward      │
│ Transactions     │ Publisher confirm│ Exactly-once (idempotent) │
│ Language         │ Erlang           │ Java/Scala                │
│ Operations       │ Simple (single   │ Complex (ZK/KRaft,        │
│ complexity       │ binary)          │ brokers, schema registry) │
├──────────────────┴──────────────────┴───────────────────────────┤
│ Use RabbitMQ when:                                               │
│ • Complex routing needed (topic, header, fanout patterns)       │
│ • Low latency messaging (sub-ms)                                │
│ • Request-reply / RPC patterns                                  │
│ • Message-level features (priority, TTL, DLX)                   │
│ • Polyglot protocol needs (MQTT, STOMP, AMQP)                  │
│ • Task queues / work distribution                               │
│                                                                   │
│ Use Kafka when:                                                  │
│ • Event streaming / event sourcing                              │
│ • Very high throughput (millions msg/s)                          │
│ • Log aggregation / data pipeline                               │
│ • Message replay / reprocessing needed                          │
│ • Stream processing (Kafka Streams, ksqlDB)                     │
│ • Long-term message retention                                   │
└──────────────────────────────────────────────────────────────────┘
```

### **RabbitMQ vs Other Brokers**

```
┌──────────────────────────────────────────────────────────────────┐
│              BROKER COMPARISON MATRIX                             │
├───────────┬───────────┬──────────┬──────────┬──────────────────┤
│           │ RabbitMQ  │  Kafka   │  NATS    │  ActiveMQ        │
├───────────┼───────────┼──────────┼──────────┼──────────────────┤
│ Written in│ Erlang    │ Java     │ Go       │ Java             │
│ Protocol  │ AMQP+     │ Custom   │ Custom   │ JMS/AMQP/STOMP   │
│ Model     │ Broker    │ Log      │ At-most  │ Broker           │
│           │           │          │ -once*   │                  │
│ Persist   │ Yes       │ Yes      │ JetStream│ Yes              │
│ Cluster   │ Erlang    │ ZK/KRaft │ Built-in │ Network of       │
│           │ dist.     │          │ RAFT     │ brokers          │
│ Throughput│ Medium    │ Very High│ Very High│ Medium           │
│ Latency   │ Very Low  │ Low      │ Very Low │ Medium           │
│ Maturity  │ 2007      │ 2011     │ 2010     │ 2004             │
│ Community │ Large     │ Very     │ Growing  │ Declining        │
│           │           │ Large    │          │ (→Artemis)       │
│ Cloud     │ CloudAMQP │ Confluent│ Synadia  │ Amazon MQ        │
│ managed   │ Amazon MQ │ Aiven    │          │                  │
├───────────┴───────────┴──────────┴──────────┴──────────────────┤
│ * NATS core is at-most-once; JetStream adds at-least-once     │
│   and exactly-once semantics                                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Production Deployment Patterns

### **Cluster Sizing Guide**

```
┌─────────────────────────────────────────────────────────────┐
│              CLUSTER SIZING GUIDELINES                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  SMALL (< 10K msg/s):                                       │
│  • 3 nodes (quorum)                                        │
│  • 4 CPU cores / node                                      │
│  • 8 GB RAM / node                                         │
│  • SSD storage (100+ GB)                                   │
│                                                              │
│  MEDIUM (10K-50K msg/s):                                    │
│  • 3-5 nodes                                               │
│  • 8 CPU cores / node                                      │
│  • 16 GB RAM / node                                        │
│  • NVMe SSD (500+ GB)                                      │
│                                                              │
│  LARGE (50K-200K+ msg/s):                                   │
│  • 5-7 nodes                                               │
│  • 16+ CPU cores / node                                    │
│  • 32-64 GB RAM / node                                     │
│  • NVMe SSD (1+ TB)                                        │
│  • Dedicated network (10Gbps+)                             │
│  • Consider streams for highest throughput                 │
│                                                              │
│  RAM calculation:                                           │
│  Base: ~150MB (Erlang VM + RabbitMQ)                       │
│  + Per connection: ~100KB                                   │
│  + Per channel: ~50KB                                       │
│  + Per queue: ~20KB (empty classic), ~30KB (quorum)        │
│  + Messages in RAM: message_count × avg_msg_size           │
│  + Mnesia overhead: ~1MB per 1000 queues                   │
│  + Binary reference cache                                  │
│                                                              │
│  Example: 10K connections × 2 channels × 1K queues         │
│  = 150MB + 1000MB + 1000MB + 20MB + messages ≈ 2.2GB+     │
└─────────────────────────────────────────────────────────────┘
```

### **Production Configuration Template**

```ini
# rabbitmq.conf - production template

## Networking
listeners.tcp.default = 5672
listeners.ssl.default = 5671
management.tcp.port   = 15672

## TLS
ssl_options.cacertfile = /etc/rabbitmq/ssl/ca.pem
ssl_options.certfile   = /etc/rabbitmq/ssl/server.pem
ssl_options.keyfile    = /etc/rabbitmq/ssl/server-key.pem
ssl_options.verify     = verify_peer
ssl_options.fail_if_no_peer_cert = false

## Resource limits
vm_memory_high_watermark.relative = 0.4
vm_memory_high_watermark_paging_ratio = 0.5
disk_free_limit.relative = 1.5

## Clustering
cluster_partition_handling = pause_minority
cluster_formation.peer_discovery_backend = rabbit_peer_discovery_k8s
# or: rabbit_peer_discovery_consul, rabbit_peer_discovery_dns

## Queue defaults
default_queue_type = quorum

## Connections
tcp_listen_options.backlog = 4096
tcp_listen_options.nodelay = true
tcp_listen_options.linger.on = true
tcp_listen_options.linger.timeout = 0

## Limits
channel_max = 2047
heartbeat = 60
frame_max = 131072

## Logging
log.console = true
log.console.level = info
log.file.level = info

## Metrics
prometheus.return_per_object_metrics = true

## Quorum queue defaults
quorum_queue.x-max-in-memory-length = 0
# (0 = write all to disk immediately, saves RAM)
```

### **Kubernetes Deployment**

```
┌─────────────────────────────────────────────────────────────┐
│            KUBERNETES DEPLOYMENT PATTERN                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Official: RabbitMQ Cluster Operator                        │
│                                                              │
│  apiVersion: rabbitmq.com/v1beta1                           │
│  kind: RabbitmqCluster                                      │
│  metadata:                                                   │
│    name: production                                         │
│  spec:                                                       │
│    replicas: 3                                              │
│    resources:                                                │
│      requests:                                              │
│        cpu: 4                                               │
│        memory: 8Gi                                          │
│      limits:                                                │
│        cpu: 8                                               │
│        memory: 16Gi                                         │
│    persistence:                                             │
│      storageClassName: fast-ssd                             │
│      storage: 200Gi                                         │
│    rabbitmq:                                                │
│      additionalConfig: |                                    │
│        cluster_partition_handling = pause_minority           │
│        vm_memory_high_watermark.relative = 0.4              │
│        default_queue_type = quorum                          │
│                                                              │
│  Key considerations:                                        │
│  • Use StatefulSet (stable network identity)               │
│  • PersistentVolumeClaim per pod                           │
│  • Anti-affinity: spread across nodes/zones                │
│  • Headless Service for inter-node discovery               │
│  • LoadBalancer Service for client access                  │
│  • Resource requests = limits (avoid OOM kills)            │
│  • Pod Disruption Budget: maxUnavailable: 1                │
│  • Readiness probe: /api/health/checks/alarms              │
│  • Liveness probe: /api/health/checks/node-is-quorum-     │
│    critical (avoid killing quorum-critical nodes)          │
└─────────────────────────────────────────────────────────────┘
```

---

## Common Anti-Patterns

```
┌─────────────────────────────────────────────────────────────┐
│             ANTI-PATTERNS TO AVOID                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. UNBOUNDED QUEUES                                        │
│     ✗ No max-length, no TTL, no consumers                  │
│     ✓ Always set x-max-length or x-max-length-bytes        │
│     ✓ Configure DLX for overflow handling                   │
│                                                              │
│  2. TOO MANY QUEUES                                         │
│     ✗ 100K+ queues (one per user/session)                  │
│     ✓ Use consistent-hash exchange or topic routing         │
│     ✓ Quorum queues limited to ~thousands per cluster      │
│                                                              │
│  3. LARGE MESSAGES                                          │
│     ✗ Sending 10MB+ payloads through RabbitMQ              │
│     ✓ Store payload in S3/blob, send reference via MQ      │
│     ✓ Claim check pattern                                   │
│                                                              │
│  4. PREFETCH = 0 (UNLIMITED)                                │
│     ✗ Server sends all messages, consumer OOMs             │
│     ✓ Set prefetch to match processing capacity             │
│                                                              │
│  5. CONNECTION PER PUBLISH                                   │
│     ✗ Open → publish → close (TCP handshake overhead)      │
│     ✓ Long-lived connections, use channels for concurrency │
│                                                              │
│  6. NOT USING PUBLISHER CONFIRMS                            │
│     ✗ Fire-and-forget persistent messages                  │
│     ✓ Enable confirms, handle nacks with retry             │
│                                                              │
│  7. POLLING WITH basic.get                                  │
│     ✗ Repeated basic.get in a loop (1 msg per round-trip)  │
│     ✓ Use basic.consume with prefetch (server push)        │
│                                                              │
│  8. AUTO-ACK FOR IMPORTANT MESSAGES                         │
│     ✗ Messages lost if consumer crashes after receive      │
│     ✓ Manual ack after processing complete                  │
│                                                              │
│  9. CLASSIC MIRRORED QUEUES IN NEW DEPLOYMENTS              │
│     ✗ Deprecated, poor performance, split-brain issues     │
│     ✓ Use quorum queues (Raft-based, data-safe)            │
│                                                              │
│  10. IGNORING FLOW CONTROL                                  │
│      ✗ Not monitoring "flow" state on connections          │
│      ✓ Monitor, adjust publish rate, add consumers         │
└─────────────────────────────────────────────────────────────┘
```

---

## See Also

- [Distributed Consensus](@/notes/distributed/distributed_consensus.md) — Raft protocol underpinning RabbitMQ quorum queues
- [Kafka Internals](@/notes/distributed/kafka_internals.md) — Alternative messaging system compared in the broker comparison section
- [Filesystem Design](@/notes/os/filesystem_design.md) — Persistence mechanisms and fsync behavior relevant to RabbitMQ's durability guarantees
- [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) — Networking syscalls (MSG_ZEROCOPY, TCP tuning) and memory management relevant to broker performance
