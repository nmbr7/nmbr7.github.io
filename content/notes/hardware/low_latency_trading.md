+++
title = "Low Latency Trading"
date = 2026-06-04
[taxonomies]
tags = ["hft", "low-latency", "trading", "kernel-bypass", "dpdk", "rdma", "fpga", "ptp", "lock-free", "networking", "hardware"]
+++


# Low-Latency Trading Systems (HFT / Ultra-Low-Latency)

Expert reference for the full hardware/OS/network/algorithm stack used to build ultra-low-latency electronic trading systems. The unifying engineering goal is to minimize **tick-to-trade** latency — the time from a market-data packet arriving on the wire to an order leaving on the wire — and to minimize its **tail** (p99/p999/max), because in a race only the winner gets the fill, and a single jitter spike can be a worst-case loss event.

This document is systems-internals-focused: it explains the *mechanisms* (why a technique removes latency, what hardware/kernel structure it bypasses) rather than trading strategy.

Existing related references:
- [Compute Interconnects](@/notes/hardware/interconnects.md) §9 RDMA verbs, §6 software stacks (DPDK/XDP), §11 tail latency pathology — networking substrate.
- [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) — branch prediction, store-to-load forwarding, prefetchers; the microarchitecture being tuned here.
- [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) §memory-ordering, §atomics, §RDTSC — fences and timestamp counters used below.
- [Cycle Counters and Energy](@/notes/hardware/cycle_counters_and_energy.md) — RDTSC/CNTVCT granularity, used for rdtsc-based timing.
- [Expert Linux Syscalls](@/notes/os/linux_expert_syscalls.md) — CPU isolation, NUMA, huge pages, AF_XDP, busy polling.
- [Data Structures for High-Performance Systems](@/notes/programming/data_structures.md) — SPSC queues, Disruptor, lock-free structures.
- [PCIe Internals](@/notes/hardware/pcie_internals.md) — DMA, MSI-X, the bus FPGA NICs sit on.
- [Database Latency Landscape](../reference/db_latency.pdf), [Unified Latency Megachart](../reference/general_latency.pdf) — cross-stack latency numbers.

---

## Table of Contents

1. [The Latency Budget — Mental Model and Numbers](#1-the-latency-budget-mental-model-and-numbers)
2. [Network Stack: Kernel Bypass and Co-Location](#2-network-stack-kernel-bypass-and-co-location)
3. [OS / Kernel-Level Techniques](#3-os-kernel-level-techniques)
4. [CPU / Hardware Microarchitecture Optimizations](#4-cpu-hardware-microarchitecture-optimizations)
5. [Lock-Free Data Structures and the Disruptor](#5-lock-free-data-structures-and-the-disruptor)
6. [Order Book and Matching Engine Design](#6-order-book-and-matching-engine-design)
7. [Clock Synchronization and Timestamping](#7-clock-synchronization-and-timestamping)
8. [FPGA in HFT](#8-fpga-in-hft)
9. [Measurement and Profiling](#9-measurement-and-profiling)
10. [Industry Practice and Exchange Specifics](#10-industry-practice-and-exchange-specifics)
11. [State of the Art Research (2020–2025)](#11-state-of-the-art-research-2020-2025)
12. [Consolidated Latency Number Table](#12-consolidated-latency-number-table)
13. [Pitfalls and Decision Checklist](#13-pitfalls-and-decision-checklist)
14. [Key References](#14-key-references)
15. [Updates and Latest Data (2024–2025)](#15-updates-and-latest-data-2024-2025)
16. [Pre-Trade Risk, Regulation, and Compliance Tech](#16-pre-trade-risk-regulation-and-compliance-tech)
17. [Transatlantic and Long-Haul Microwave / Laser Networks](#17-transatlantic-and-long-haul-microwave-laser-networks)
18. [Order Flow Toxicity, Adverse Selection, and Alpha Decay](#18-order-flow-toxicity-adverse-selection-and-alpha-decay)
19. [Network Topology Inside the Colo Rack](#19-network-topology-inside-the-colo-rack)
20. [Memory-Mapped Kernel Interfaces](#20-memory-mapped-kernel-interfaces)
21. [Language and Runtime Choices](#21-language-and-runtime-choices)
22. [Custom ASICs in Trading](#22-custom-asics-in-trading)
23. [FIX Protocol Evolution: SBE and FAST](#23-fix-protocol-evolution-sbe-and-fast)

---

## 1. The Latency Budget — Mental Model and Numbers

### 1.1 Definitions (be precise, the field is loose with these)

- **Wire-to-wire** (a.k.a. wire latency): from the last bit of an inbound packet at the NIC's SFP/QSFP cage to the first bit of the outbound packet at the cage. Measured by an external passive optical tap + a capture device (Corvil, Endace, Arista 7130/MetaWatch, Exablaze/Cisco Nexus 3550). This is the only *honest* number because it includes everything inside the box.
- **Tick-to-trade (T2T)**: from a market-data update ("tick") to the corresponding order. Often used interchangeably with wire-to-wire when the order is causally produced by that tick.
- **Tick-to-order**: software-internal version, NIC-RX-timestamp to NIC-TX-timestamp, missing the PHY/SerDes/MAC time on either side (typically 200 ns–1 µs of hidden latency the marketing number omits).
- **Internal / application latency**: feed-handler-in to order-gateway-out, in user space. Smallest number, most gameable.
- **Round-trip / RTT to matching engine**: order sent → ack received, dominated by cable length + switch + exchange gateway.

```
        EXTERNAL TAP (wire-to-wire — the honest number)
   ┌──────────────────────────────────────────────────────────┐
   │  ┌── NIC RX ──┐                          ┌── NIC TX ──┐    │
   │  │ PHY/SerDes │  feed   strategy  order  │ MAC/PHY    │    │
in ─┼──┤ MAC        ├─ handler ─ logic ─ gw ──┤ SerDes     ├────┼─ out
   │  └────────────┘                          └────────────┘    │
   │      └────────── tick-to-order (SW) ──────────┘            │
   │   └──────────────── tick-to-trade ───────────────┘         │
   └──────────────────────────────────────────────────────────┘
```

### 1.2 Order-of-magnitude budget (2024–2025, top-tier setups)

| Path | Software (kernel-bypass) | FPGA-accelerated | Pure FPGA / ASIC |
|---|---|---|---|
| Wire-to-wire tick-to-trade | 800 ns – 5 µs | 100–300 ns | **<100 ns (≈30–90 ns)** |
| NIC RX → user space (kernel bypass) | 700 ns – 1 µs | — | — |
| NIC RX → user space (FPGA NIC, ef_vi-style) | ~250–700 ns | — | — |
| Feed-handler decode (ITCH/PITCH) in SW | 100–500 ns | 10–40 ns (parse-on-the-fly) | — |
| Order-book update (single level) | 20–100 ns | few ns | — |
| Strategy decision (simple) | 50–500 ns | 5–20 ns | — |
| Order encode + NIC TX (kernel bypass) | 300–800 ns | — | — |
| L2 switch (cut-through, Arista 7130/Metamako) | — | — | **~3–5 ns/hop** |
| L2 switch (store-and-forward, normal DC) | ~350 ns – 1 µs | — | — |
| Co-lo cross-connect (fiber, per meter) | ≈5 ns/m (≈4.9 ns/m in fiber) | — | — |

Anchors to memorize: light in fiber ≈ **5 ns/m** (n≈1.46, so ~204,000 km/s). A 100 m cross-connect = 500 ns each way. The fastest commercial cut-through L1 switches (Arista 7130 / former Metamako, Exablaze/Cisco Nexus 3550) are in the **~3–4 ns** range. The wire-to-wire floor for a *software* trade is set by NIC PHY + DMA, not by your code.

### 1.3 Why the tail dominates

Mean latency is nearly irrelevant. A strategy that wins races at 1 µs mean but has a 50 µs p999 caused by a page fault, a TLB miss storm, an SMI (System Management Interrupt), a context switch, or a GC pause will get adversely selected: it wins the easy fills and loses (gets filled on stale quotes) exactly when the market moves. The discipline is **determinism over throughput**: eliminate every source of variance (interrupts, page faults, NUMA crossings, cache misses, kernel scheduling) even at the cost of mean latency or CPU efficiency.

---

## 2. Network Stack: Kernel Bypass and Co-Location

### 2.1 Why the kernel network stack is unacceptable

A packet through the Linux network stack incurs: NIC IRQ → softirq (NAPI) → `sk_buff` allocation → protocol demux → socket buffer copy → `recvmsg` syscall (mode switch) → user copy. That is multiple cache-line-cold structures, at least one copy, an interrupt, and a syscall — typically **5–15 µs** and, worse, with a fat tail from softirq batching, IRQ coalescing, and scheduler wakeups. Kernel bypass deletes the entire path.

### 2.2 The kernel-bypass landscape

**Solarflare/AMD OpenOnload + ef_vi** (the HFT standard).
- *OpenOnload*: a `LD_PRELOAD` user-space TCP/IP stack that intercepts BSD socket calls. Zero application change, ~1.5–3 µs RX. Two modes: interrupt-driven and **spinning** (`EF_POLL_USEC`), where a thread busy-polls the NIC's virtual interface descriptor ring from user space — no IRQ, no syscall.
- *ef_vi*: the low-level layer-2 API under Onload. The application gets direct access to the NIC's RX/TX descriptor rings (VIs = Virtual Interfaces) and DMA buffers. You poll `ef_eventq_poll()` for completions. This is the genuine HFT data path — ~250 ns NIC-to-user is achievable. Hardware models: Solarflare/Xilinx X2522, X3522 (the X3522 has an on-NIC "Cut-Through PIO" / TCPDirect path; sub-1µs and the X3 family integrates FPGA logic).
- *TCPDirect*: a stripped, zero-copy TCP on top of ef_vi for the absolute minimum-latency TCP send.

**DPDK** (Data Plane Development Kit).
- Poll-mode drivers (PMDs) bind the NIC via VFIO/UIO, DMA directly into huge-page mempools, and busy-poll the rings. No kernel network stack at all — you bring your own TCP/IP (or none; many feeds are UDP multicast). Latency comparable to ef_vi for raw L2; you must implement protocol handling. Used widely; vendor-neutral. See [Interconnects §6](@/notes/hardware/interconnects.md) and [VFIO Internals](@/notes/os/vfio_internals.md).
- Key DPDK knobs: `rte_eth_rx_burst` batch size (smaller = lower latency, larger = higher throughput), `--lcores` pinning, mempool cache alignment, RX/TX descriptor ring sizes, disabling RX interrupts entirely.

**AF_XDP / XDP** (in-kernel eBPF fast path).
- A middle ground: an eBPF program at the driver's NAPI poll can `XDP_REDIRECT` frames into a user-space `AF_XDP` socket via a UMEM (shared DMA region), bypassing the rest of the stack. Higher latency than DPDK/ef_vi (still has the driver NAPI context) but no exotic NIC and stays in mainline kernel. See [Linux Syscalls §XDP/AF_XDP](@/notes/os/linux_expert_syscalls.md).

**RDMA / RoCEv2 / InfiniBand**.
- One-sided `RDMA_WRITE`/`READ` let a remote NIC place data into local memory with zero CPU and zero kernel involvement; the verbs queue-pair (QP) model exposes send/recv/completion queues directly to user space (kernel only does connection setup). Excellent for *internal* fabric (strategy ↔ risk ↔ OMS, market-data fan-out) where you control both ends. Less used for exchange connectivity (exchanges speak UDP multicast + TCP). RoCEv2 needs a lossless fabric (PFC/ECN/DCQCN) — see [Interconnects §9–10](@/notes/hardware/interconnects.md). Verbs deep dive (SEND/WRITE/READ/ATOMIC, RNR, DCT) is in that doc.

| Technique | Typical RX latency | NIC requirement | Protocol you implement | Mainline kernel |
|---|---|---|---|---|
| Linux sockets | 5–15 µs | any | none | yes |
| AF_XDP | 2–5 µs | XDP-capable driver | L4+ | yes |
| DPDK | 0.5–1 µs | DPDK PMD NIC | TCP/IP yourself | no (VFIO bind) |
| OpenOnload (spin) | 1–3 µs | Solarflare/X2/X3 | none (sockets) | partial |
| ef_vi / TCPDirect | 0.25–0.7 µs | Solarflare/X2/X3 | L2/L4 yourself | partial |
| FPGA NIC (parse on card) | tens of ns to user-visible | FPGA NIC | on-FPGA | n/a |

### 2.3 Feed handlers

A **feed handler** decodes the exchange's market-data protocol into the firm's internal book. Exchange protocols are binary, fixed-field, designed for fast parsing:
- **Nasdaq ITCH / TotalView-ITCH**: per-message, fixed-length, big-endian, sequenced over MoldUDP64 (UDP multicast with a sequence number + message-count header for gap detection). Message types: Add Order, Order Executed, Order Cancel, Order Delete, Trade.
- **Cboe/BATS PITCH**, **NYSE XDP / Pillar**, **CME MDP 3.0** (Simple Binary Encoding, SBE — FIX's binary format, fixed-offset fields, no parsing branches).
- Recovery: multicast feeds are lossy; you run **A/B line arbitration** (two redundant multicast feeds; take whichever packet of a given sequence number arrives first, dedup by sequence) and a TCP "retransmission" / snapshot channel to recover gaps.

Feed-handler engineering: parse with fixed offsets (no `if` on field presence), avoid allocation (pre-sized arenas/ring), branch-free where possible, and dispatch by message type with a jump table or `switch` the compiler turns into one. SBE/ITCH are deliberately designed so a parser is `memcpy` of fixed fields + endian swap.

### 2.4 Co-location, cross-connects, microwave

- **Co-location**: your servers in the exchange's data center (Nasdaq Carteret NJ, NYSE Mahwah NJ, CME Aurora IL, LSE/Equinix LD4 Slough, Equinix FR2 Frankfurt for Eurex/Xetra, NY4/NY5 Secaucus, Tokyo). The exchange sells rack space + a **cross-connect** (a fiber patch) to the matching engine handoff.
- **Latency equalization**: exchanges (post-Reg-NMS scrutiny) cut all co-lo cross-connects to **equal length** ("coiled fiber") so no cabinet is physically closer to the matching engine — Nasdaq, NYSE, and others do this. You cannot beat your neighbor by being in a closer rack; you beat them with the box.
- **Cross-connect** = ~5 ns/m. Inter-exchange links matter for cross-venue arbitrage: the Chicago↔New Jersey route is the famous one.
  - Fiber (Spread Networks, 2010): ~13.1 ms RTT Chicago↔NY.
  - **Microwave** (McKay Brothers, Jump/New Line, Vigilant): ~8.5 ms RTT — microwave travels at ~0.99c through air vs ~0.67c in glass, so a straighter, faster path beats fiber by ~4.5 ms despite weather sensitivity (rain fade). This is the canonical "speed of light is the competitor" example.
  - **Millimeter wave / shortwave** for some transatlantic and longer paths; **laser/FSO** in metro.
- **Exchange-provided cut-through switches**: between your NIC and the matching engine sits the exchange's gateway; the only thing in your control is the box and the cross-connect length (now equalized).

---

## 3. OS / Kernel-Level Techniques

The objective: give one thread one core, forever, with nothing else allowed to touch that core — no interrupts, no scheduler, no kernel housekeeping, no page faults.

### 3.1 CPU isolation

- **`isolcpus=`** (boot cmdline): removes listed CPUs from the kernel scheduler's general load balancing. Threads only run there if explicitly pinned (`sched_setaffinity`/`taskset`/`cpuset`). Considered semi-deprecated in favor of cpuset + `nohz_full`, but still widely used.
- **`nohz_full=`** (boot cmdline): **full dynticks** — on a CPU running exactly one runnable task, the kernel stops the periodic 1000 Hz scheduler tick (`CONFIG_NO_HZ_FULL`). The tick is the single biggest recurring jitter source on an otherwise idle isolated core; removing it eliminates a periodic ~few-µs interruption. Must be paired with `rcu_nocbs=` to offload RCU callback processing to housekeeping CPUs.
- **`rcu_nocbs=`** + **`rcu_nocb_poll`**: move RCU grace-period callback execution off the isolated cores onto dedicated "rcuo" kthreads on housekeeping CPUs, so an isolated core is never interrupted to run RCU callbacks.
- **cpusets / cgroup v2 cpuset controller**: confine all other processes and kernel threads to a "housekeeping" set, leaving the trading cores exclusively for trading threads.
- **"Core shielding"** (the general pattern): isolate cores + move all movable IRQs and kernel threads off them + pin trading threads onto them with `SCHED_FIFO`. The `cset shield` tool, or manual cpuset + IRQ affinity, accomplishes this.
- **Disable timer migration, watchdogs**: `nowatchdog`, `nmi_watchdog=0`, `kthread_cpus=`, move `workqueue` affinity (`/sys/devices/virtual/workqueue/cpumask`), disable the RT throttling (`/proc/sys/kernel/sched_rt_runtime_us = -1`) so a busy-polling `SCHED_FIFO` thread is never preempted to "give time back".

Representative boot cmdline (Intel, 2-socket, cores 2–19 isolated):
```
isolcpus=nohz_domain,managed_irq,2-19 nohz_full=2-19 rcu_nocbs=2-19 rcu_nocb_poll \
irqaffinity=0-1 nosoftlockup nowatchdog nmi_watchdog=0 \
intel_pstate=disable processor.max_cstate=1 intel_idle.max_cstate=0 idle=poll \
mce=off audit=0 selinux=0 transparent_hugepage=never default_hugepagesz=1G hugepagesz=1G hugepages=64 \
tsc=reliable clocksource=tsc skew_tick=1 pcie_aspm=off
```

### 3.2 Stopping the CPU from saving power (the C-state / P-state war)

Power management is a top latency-tail source: a core in a deep C-state (C3/C6) takes microseconds to wake; frequency scaling (P-states) means your first instructions after idle run slow.
- **`idle=poll`**: the idle loop busy-spins instead of entering C-states — the core never sleeps. Burns power and heat but gives the lowest wakeup latency. Alternatively `processor.max_cstate=1` / `intel_idle.max_cstate=0` to cap at C1.
- **Disable C-states per-core** at runtime via `/dev/cpu_dma_latency` (write a 32-bit `0` and hold the fd open → PM QoS forces CPUs to C0/low-latency).
- **Pin frequency**: `intel_pstate=disable` + `cpupower frequency-set -g performance`, or disable Turbo (`/sys/devices/system/cpu/intel_pstate/no_turbo`) for *determinism* (Turbo's frequency is non-deterministic and thermally throttled). HFT shops often disable Turbo and run a fixed all-core frequency for predictability, accepting a lower peak.
- **Disable SMT/Hyper-Threading**: a sibling hyperthread contends for the same execution ports, L1/L2, and store buffer, injecting jitter. Most ULL setups disable SMT in BIOS.
- **Disable SMIs where possible**: System Management Interrupts (BIOS/firmware, e.g., for thermal, USB legacy, ECC) are invisible to the OS and the worst tail spikes (tens of µs). Mitigate in BIOS (disable USB legacy, processor power management, "patrol scrub"). Detect with `turbostat` SMI count or a tight-loop RDTSC jitter histogram.

### 3.3 NUMA pinning

On a multi-socket box, accessing remote-socket memory crosses UPI/Infinity Fabric (~+50–100 ns, and contends). Rule: the trading thread, its memory, the NIC it polls, and its IRQs must all be on the **same NUMA node**.
- Bind the NIC's PCIe slot to a socket (check `/sys/class/net/<dev>/device/numa_node`), pin the polling thread to a core on that node, allocate buffers with `numa_alloc_onnode`/`mbind`/`set_mempolicy`, mount huge pages from that node's pool.
- `numactl --cpunodebind=0 --membind=0 ./trader`. `lstopo` to see the topology. See [Linux Syscalls §NUMA](@/notes/os/linux_expert_syscalls.md).

### 3.4 Huge pages

- A 4 KB-page working set blows out the TLB; a TLB miss is a page-table walk (up to 4 memory accesses, ~tens of ns) and is a tail source. **2 MB or 1 GB huge pages** map the hot working set with one or a few TLB entries.
- Prefer **explicit hugetlbfs / `MAP_HUGETLB` with 1 GB pages**, reserved at boot (`hugepagesz=1G hugepages=N`), *not* Transparent Huge Pages (THP). THP's background `khugepaged` compaction and the latency of fault-time promotion are themselves jitter sources — **disable THP** (`transparent_hugepage=never`). Pre-fault and `mlock` all pages at startup so there is never a minor/major fault on the hot path.

### 3.5 IRQ affinity and busy-poll vs interrupt

- Move every movable IRQ off the isolated cores (`/proc/irq/*/smp_affinity`, and disable `irqbalance` which would undo this). The NIC's own queues are handled in user space (kernel bypass) so its IRQs are moot, but the box still has timer, disk, USB, management IRQs.
- **Busy polling beats interrupts for ULL, unconditionally.** An interrupt costs the IRQ entry, the softirq, and a likely cache-cold wakeup — hundreds of ns to µs and high variance. A busy-poll loop (`while (ef_eventq_poll(...) == 0);`) has bounded, low-jitter latency at the cost of 100% CPU. Every ULL data path busy-polls. The kernel's own `SO_BUSY_POLL` / `napi_busy_poll` exists for socket users but ULL goes lower with ef_vi/DPDK.

### 3.6 Real-time Linux (PREEMPT_RT)

- `PREEMPT_RT` (now largely mainline as of Linux 6.12, 2024) makes nearly all kernel code preemptible (sleeping spinlocks via rtmutex, threaded IRQs, priority inheritance). It dramatically lowers **worst-case scheduling latency** (the metric `cyclictest` measures), turning multi-hundred-µs kernel-induced spikes into low-µs.
- Nuance for HFT: a *busy-polling SCHED_FIFO thread on an isolated nohz_full core* spends ~0% time in the kernel anyway, so PREEMPT_RT's benefit is mostly about the rest of the system and about control-plane threads. Many HFT shops use a tuned vanilla kernel + isolation rather than PREEMPT_RT, because RT's preemption machinery can add overhead to the kernel paths they do hit. Others use it for guaranteed bounded latency on control threads. Measure with `cyclictest -p99 -m -t -a` on the isolated cores.
- Thread scheduling: trading threads run `SCHED_FIFO` (or `SCHED_RR`) at high priority, never `SCHED_OTHER` (CFS/EEVDF), so they are never preempted by background work and have deterministic wakeup.

---

## 4. CPU / Hardware Microarchitecture Optimizations

The hot path lives in L1/L2 and the store buffer. Everything here is about not missing in cache and not stalling the pipeline. See [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) for the underlying machinery.

### 4.1 Cache-line alignment and false sharing

- A cache line is **64 bytes** (Intel/AMD; Apple M-series 128 B). False sharing = two threads writing two different variables that happen to share a line → the coherence protocol (MESI/MOESI) ping-pongs the line between cores, turning an L1 write into a cross-core miss (~tens of ns, high variance). Fix: pad/align hot per-thread or producer/consumer variables to their own line.
  ```cpp
  struct alignas(64) PaddedCounter { std::atomic<uint64_t> v; char pad[64 - sizeof(v)]; };
  ```
  C++17 `std::hardware_destructive_interference_size`; in a Disruptor the head and tail cursors are each on their own line.
- **Cache-line-aligned hot structs**: keep the order-book level, the sequence number, the message buffer aligned and ideally within one or two lines. Group fields touched together; split fields touched by different cores.

### 4.2 Avoiding cache misses on the hot path

- **Pre-touch / warm the cache**: periodically "exercise" the hot path with dummy data so the code (I-cache, branch predictors) and data structures (D-cache) stay warm. A path you take once a minute will be cold and slow exactly when the rare-but-important event fires; firms run a warming loop that replays representative work to keep the predictors trained and lines resident.
- **Prefetch**: `__builtin_prefetch` / `_mm_prefetch` the next order-book node or the next message before you need it. Effective when the access pattern is predictable a few iterations ahead.
- **Keep the working set tiny and contiguous** (arrays over pointer-chasing trees — see §6). Pointer chasing = serial dependent loads = each a potential miss.

### 4.3 Branch prediction

- A mispredict flushes the pipeline (~15–20 cycles on modern cores). On the hot path, the common case (no trade, no signal) should be the predicted path.
- `__builtin_expect` / C++20 `[[likely]]`/`[[unlikely]]` annotate the common branch so the compiler lays out the fall-through accordingly and the static predictor + layout favor it.
- **Branchless code**: replace data-dependent branches with arithmetic/`cmov`/masking so there is nothing to mispredict (e.g., `min`/`max` via `cmov`, conditional update via mask). Useful for the unpredictable branches (a branch that's 50/50 is a guaranteed ~50% mispredict). The TAGE predictor (see [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md)) handles correlated branches well, so only genuinely random branches need de-branching.

### 4.4 SIMD for order-book and parsing

- Order-book operations are usually scalar (one level update), but SIMD helps in batch contexts: scanning a price ladder for the best N levels, computing aggregate volume across levels, batch-parsing/validating multiple fixed-width messages, or computing checksums. AVX2/AVX-512 can update or compare 8–16 price/qty lanes at once.
- Caveat: **AVX-512 frequency licensing** — on some Intel generations, heavy AVX-512 use drops the core (and neighbors) to a lower frequency for a transition window, *raising* latency and adding non-determinism. ULL shops often avoid wide AVX-512 on the hot path for this reason, or pin frequency to remove the variability. Newer cores (Sapphire Rapids onward) have much reduced license penalties.

### 4.5 Memory ordering

- Lock-free code lives or dies on memory ordering. x86 is **TSO** (Total Store Order): loads are not reordered with loads, stores not with stores, but a store *can* be reordered after a later load (store-buffer forwarding) — the one fence you actually need on x86 is around store-then-load (`MFENCE`/`LOCK`-prefixed op). ARM is weakly ordered and needs explicit `dmb`/`ldar`/`stlr`. See [ISA Critical Instructions §memory-ordering](@/notes/hardware/isa_critical_instructions.md).
- In C++: `std::atomic` with `memory_order_acquire`/`release` for SPSC handoff (release on publish, acquire on consume) — no full fence needed on x86, the acquire/release map to plain loads/stores there. Use `relaxed` for counters that don't gate a handoff. Getting this right is what makes a wait-free SPSC ring correct and fast.

### 4.6 Hardware timestamps

- NICs with **PTP hardware clocks (PHC)** timestamp packets in hardware at the MAC (`SO_TIMESTAMPING` with `SOF_TIMESTAMPING_RX_HARDWARE`). This removes the software-timestamping jitter (the time between packet arrival and your `clock_gettime`) and is the basis for honest internal latency measurement and for PTP sync (§7). Solarflare/Xilinx, Mellanox/NVIDIA, and Exablaze NICs expose hardware RX/TX timestamps with ~ns resolution.

---

## 5. Lock-Free Data Structures and the Disruptor

Locks are forbidden on the hot path: a mutex can sleep (syscall, context switch), and priority inversion + scheduler involvement destroy determinism. The toolkit is wait-free/lock-free single-writer structures. See [Data Structures §lock-free](@/notes/programming/data_structures.md) for the broader catalog.

### 5.1 SPSC ring buffer (the workhorse)

- **Single-producer, single-consumer** bounded ring is the fundamental ULL queue: wait-free on both ends, no CAS needed — just a producer index and a consumer index, each written by exactly one thread, read by the other, with acquire/release ordering on publish/consume.
- Design rules: power-of-two capacity (mask instead of modulo); head and tail on **separate cache lines** (false-sharing kill, §4.1); a cached copy of the other side's index so you don't read the contended line every operation (read it only when you think you're full/empty); store the payload inline (avoid pointer indirection). This is the classic "Folly ProducerConsumerQueue" / "rigtorp SPSCQueue" design — sub-50 ns enqueue/dequeue.

### 5.2 The LMAX Disruptor

- Created at LMAX Exchange (Thompson, Farley, Barker, Gee, Stewart, 2011 — "Disruptor: High performance alternating exchange between threads") to process **6M orders/sec on a single thread**. A pre-allocated **ring buffer** of entries + a monotonically increasing **sequence** claimed by producers; consumers track their own sequence and process entries behind the producer cursor.
- Why it's fast: no allocation (entries reused → no GC, no allocator), no locks (sequences are CAS or single-writer), mechanical-sympathy cache layout (sequence counters padded to cache lines), batching (a consumer that falls behind catches up by processing a run of entries, amortizing overhead), and a clear dependency graph (consumers can be chained: e.g. journaling → replication → business logic, each a `SequenceBarrier`).
- Wait strategies trade latency vs CPU: `BusySpinWaitStrategy` (lowest latency, burns a core), `YieldingWaitStrategy`, `BlockingWaitStrategy` (lock+condvar, for non-hot consumers). HFT uses busy-spin.
- The Disruptor is the canonical pattern for the in-process pipeline: feed handler → book builder → strategy → risk → order gateway, each stage a consumer on a shared ring, all on isolated cores, all busy-spinning, zero locks, zero allocation steady-state.

### 5.3 Memory reclamation

- For the rare multi-producer or shared-read structures, you cannot `free` while a reader might be in. Use **epoch-based reclamation**, **hazard pointers**, or **RCU/QSBR** (quiescent-state-based) to defer reclamation. ULL designs mostly *avoid* this by using SPSC + single-writer ownership and arena/slab allocation that's never freed during the trading session (allocate everything at startup, reset between sessions).

### 5.4 No allocation, no exceptions, no syscalls on the hot path

- Pre-allocate all buffers, objects, and message arenas at startup; `mlock` them; never `malloc`/`new`/`free` while trading (the allocator can take locks, touch cold pages, fault). Use object pools / freelists.
- Avoid exceptions on the hot path (throw is expensive and unpredictable); use error codes/`expected`.
- No syscalls: no logging to disk synchronously (log to a lock-free ring consumed by a separate core that does the I/O — async logging), no `clock_gettime` if RDTSC suffices, no `malloc`.

---

## 6. Order Book and Matching Engine Design

The **limit order book (LOB)** maintains, per instrument, resting buy (bid) and sell (ask) orders organized by price level, each level a FIFO queue of orders (price-time priority). Operations: Add, Cancel, Modify (often Cancel+Add), Execute/Match. The design tension is between *update latency* (an HFT consumer rebuilding the book from a feed cares about per-message ns) and *match throughput* (an exchange matching engine cares about orders/sec with fairness).

### 6.1 Data-structure choices for price levels

| Structure | Best price | Add at level | Random price access | Memory | Notes |
|---|---|---|---|---|---|
| Sorted array of levels | O(1) (ends) | O(n) shift | O(log n) bsearch | compact | bad for sparse/wide price ranges |
| Balanced BST / RB-tree (`std::map`) | O(log n) | O(log n) | O(log n) | pointer-heavy, cache-cold | simple, common, *not* ULL-optimal |
| Skip list | O(log n) | O(log n) | O(log n) | pointer-heavy | concurrent variants exist |
| **Flat array indexed by price (ticks)** | **O(1)** | **O(1)** | **O(1)** | large if wide range | the ULL favorite |
| Array of levels + hashmap order-id→node | O(1) best, O(1) cancel | O(1) | — | moderate | most production HFT books |

- **Price-as-index array** ("calendar"/direct-mapped book): since prices are discrete ticks, map price → array slot directly. Best bid/ask tracked as two indices. Add/cancel/lookup are O(1) array ops, fully cache-friendly, branch-light. The cost is memory and that the addressable price range must be bounded (use a window around the touch, or a sparse two-level scheme). This is the dominant ULL design because every operation is a contiguous array write.
- **Order-ID index**: feeds reference orders by ID for cancel/execute (ITCH gives you an order reference number). Maintain a flat array or open-addressing hash map `order_id → {price_level, qty, position}` so a cancel is O(1) without searching the level. Pre-size it; use a dense `order_id`-keyed array if IDs are dense, else robin-hood/open-addressing hashing (no chaining, cache-friendly).
- Per-level order list: an intrusive doubly linked list of order nodes drawn from a pre-allocated pool (so cancel is O(1) unlink), or for pure top-of-book strategies, just aggregate quantity per level (don't track individual orders at all — much smaller and faster if you only need L2 depth, not L3 queue position).

### 6.2 L1 / L2 / L3 and what you actually need

- **L1 (top of book)**: best bid/ask + size. Many strategies only need this — keep it in two cache lines, update in a handful of ns.
- **L2 (depth by price)**: aggregate size per price level. Array-by-price.
- **L3 (full order-by-order / MBO — market by order)**: every individual order, needed for queue-position estimation (where am I in the FIFO? → fill-probability models). Requires the order-ID index and per-level lists. More expensive but valuable for passive strategies.

### 6.3 Matching engine (exchange side) considerations

- An exchange matching engine is **single-threaded per instrument** (or per shard) for determinism and fairness — the Disruptor pattern (LMAX) exists precisely for this. Sharding is by instrument/symbol across cores; cross-instrument operations (e.g., basket) are the hard part.
- Determinism and auditability: the matcher must produce a totally ordered, replayable event log (the journaling consumer in a Disruptor pipeline) for regulatory replay and recovery. Inputs are sequenced; the matcher is a pure state machine over that sequence.
- Price-time priority matching: incoming aggressive order walks the opposite side from best price, filling FIFO at each level until exhausted or no cross. Pro-rata matching (some futures/options markets) allocates fills proportional to size instead — different data-structure pressure.

### 6.4 Market impact (strategy-side modeling, brief)

- **Market impact** = how much your own order moves the price (temporary, recovers; permanent, doesn't). Models inform order *placement/sizing* not the book mechanics: the square-root law (impact ∝ √(order size / daily volume), Almgren et al.), Kyle's lambda (linear price impact per signed volume, Kyle 1985), and execution-scheduling (Almgren–Chriss 2000 optimal liquidation balancing impact vs timing risk). Relevant to ULL because the latency advantage is *used* to react before impact propagates and to avoid being the slow order that gets adversely selected.

### 6.5 Latency vs throughput tradeoff

- ULL consumer book: optimize per-update latency, accept lower max throughput (small batches, busy-poll, one symbol per core).
- Exchange matcher: optimize throughput + fairness + determinism, accept higher per-order latency (still single-digit µs at top exchanges). CME, Nasdaq, etc., publish matching-engine latencies in the low-µs to tens-of-µs range.

---

## 7. Clock Synchronization and Timestamping

You cannot measure or attribute latency you cannot timestamp consistently, and regulators (MiFID II RTS 25 in the EU, and exchange rules) mandate clock accuracy (MiFID II: HFT must be within **100 µs of UTC** with timestamp granularity ≤ 1 µs; some require ≤ 100 µs). Internally, sub-µs sync is needed to compare RX timestamps across machines.

### 7.1 PTP / IEEE 1588

- **Precision Time Protocol** synchronizes clocks over Ethernet to sub-µs (with hardware support, tens of ns). A Grandmaster (GPS-disciplined) sends `Sync` messages; the **two-step** protocol exchanges `Sync`/`Follow_Up`/`Delay_Req`/`Delay_Resp` to measure path delay and offset. Accuracy depends on:
  - **Hardware timestamping at the MAC (PHC)** — software PTP (timestamp in the kernel/userspace) has ms-level jitter; hardware PTP timestamps the packet on the wire (ns).
  - **Transparent clocks / boundary clocks** in switches: a transparent clock measures and corrects for its own queuing/residence time (writes it into the correctionField), so PTP accuracy survives the switch fabric. PTP-aware switches (Arista, Cisco) are required for ns-class sync.
  - PTP profiles: default (1588), the telecom profiles (G.8275.1/.2), and the **financial / enterprise profile**.
- Linux: `linuxptp` (`ptp4l` syncs the NIC PHC to the network; `phc2sys` syncs the system clock to the PHC). `ts2phc` disciplines the PHC from a 1PPS/GPS source.

### 7.2 GPS-disciplined oscillators (GPSDO) and the time source hierarchy

- The Grandmaster is disciplined by **GPS/GNSS** (a 1PPS pulse + time-of-day) feeding a high-stability oscillator (OCXO or **Rubidium**) that holds time during GPS outages (holdover). Vendors: Meinberg, Microchip/Microsemi (SyncServer), Orolia/Safran, Trimble. A rooftop GPS antenna → grandmaster → PTP → all servers.
- Drift: a free-running crystal drifts ~ppm (µs/s to ms/s); an OCXO ~ppb; Rubidium ~10⁻¹¹. The discipline loop (PLL) steers the local oscillator to the GPS reference, and the holdover spec tells you how long you stay within budget if GPS is lost.

### 7.3 Hardware vs software timestamps

- **Software** (`clock_gettime(CLOCK_REALTIME/MONOTONIC)`): cheap (vDSO, no syscall) but timestamps when *your code* runs, not when the packet arrived — includes scheduling/poll jitter. Fine for relative internal timing if you're consistent (use `CLOCK_MONOTONIC_RAW` to avoid NTP/PTP steering glitches).
- **Hardware** (NIC PHC via `SO_TIMESTAMPING`): timestamps the packet at the MAC. The gold standard for measuring wire-to-software latency and for cross-host comparison once all PHCs are PTP-synced.
- **RDTSC** for the finest *intra-process* timing (§9): not wall-clock, but a monotonic cycle counter ideal for measuring code-segment latency in cycles.

### 7.4 White Rabbit

- **White Rabbit** (CERN, for the LHC timing network; now IEEE 1588-2019 High Accuracy profile) extends PTP with **Synchronous Ethernet (SyncE)** for syndtonization (frequency lock via the physical layer) + precise phase measurement (DDMTD — digital dual-mixer time difference) to achieve **sub-nanosecond** (≈<1 ns, even ~tens of ps) synchronization over fiber. Adopted by some financial networks and time-distribution providers where sub-ns cross-site sync matters; overkill for most colo where PTP's tens-of-ns suffices, but the reference for the bleeding edge.

---

## 8. FPGA in HFT

FPGAs win because they remove the von Neumann bottleneck for the hot path: parse-and-decide happens in a **fixed-latency dataflow pipeline** clocked at the line, with no instruction fetch, no cache, no OS, and (critically) **deterministic, jitter-free** latency. The tradeoff is development cost (HDL/HLS), inflexibility, and limited on-chip resources.

### 8.1 What gets put on the FPGA

- **Market-data parsing on the FPGA**: the Ethernet MAC/PHY is on the FPGA NIC; the ITCH/PITCH/MDP parser is a streaming pipeline that decodes fields as bytes arrive (no store-then-parse). The book (or top-of-book) can be maintained in on-chip BRAM/URAM. Decode-to-book in **tens of ns**.
- **Trigger / order routing**: a "fast path" on the FPGA evaluates a simple condition (e.g., price crosses threshold, or a pre-computed quote needs cancelling) and emits a pre-formed order template (fill in price/qty/checksum) directly to TX — **tick-to-trade in 30–100 ns**, entirely on-chip, CPU never involved.
- **Risk checks / pre-trade checks** in hardware (fat-finger limits, max order size, throttle) — required by SEC Rule 15c3-5 ("market access rule"); doing them in the FPGA path keeps them on the fast path without a CPU round trip.
- **Hybrid model (most common)**: FPGA handles the latency-critical "reflex" (cancel-on-event, simple liquidity-taking) and feeds parsed data to the CPU for the complex/slow strategy. The CPU pre-loads order templates and parameters into the FPGA; the FPGA fires them when a hardware condition matches. This combines FPGA speed for the race with CPU flexibility for intelligence.

### 8.2 Hardware

- **FPGA NICs / SmartNICs**: AMD/Xilinx Alveo (U50/U250/U280, and the X3 "Varium"/Solarflare X3522 which fuses a Solarflare NIC with FPGA logic), Intel/Altera Stratix 10 and Agilex (and the former Arista/Exablaze ExaNIC X10/X25, NoviFlow, Algo-Logic, Enyx, NovaSparks appliances). NovaSparks ships a fully FPGA feed handler appliance.
- **Vendors of FPGA trading IP**: Enyx (nxFeed/nxAccess), Algo-Logic (Tick-to-Trade), NovaSparks (NovaTick/NovaLink), Exegy/Vela (hardware feed handlers). These sell pre-built FPGA feed handlers and order-entry pipelines so a firm doesn't write the MAC/parser from scratch.
- **Layer-1 switches** (Arista 7130 / former Metamako, Exablaze/Cisco Nexus 3550, xCelor): a crosspoint switch that connects ports electrically with **~3–5 ns** latency and can fan a feed out to many listeners (replication in hardware) or tap a link — the connective tissue of a colo rack.

### 8.3 Latency numbers

- FPGA tick-to-trade (full, wire-to-wire, parse → trigger → order out): **published vendor numbers ~30–100 ns**; in-house can be at the low end (some report sub-50 ns, even ~20–40 ns for the simplest cancel/quote).
- FPGA NIC to host (when you do bring data to the CPU): ~few hundred ns, lower than any software NIC path.
- The FPGA's killer feature is not just the low mean but the **near-zero jitter** — fixed pipeline depth means p50 ≈ p99 ≈ p999. Software at 1 µs mean might be 20 µs at p999; FPGA at 50 ns is 50 ns at p999.

### 8.4 Development reality

- HDL (Verilog/VHDL) or HLS (Vitis HLS, Catapult) for the dataflow; timing closure to hit the line-rate clock (322 MHz for 40G, etc.); on-chip resource budgeting (LUTs, BRAM, transceivers). Long build times (hours), hard debug (ILA/ChipScope), and the parser must handle every edge case the exchange spec allows. The economic justification is that for the most contested races, software simply cannot compete with a fixed 50 ns hardware pipeline.

---

## 9. Measurement and Profiling

"You can't optimize what you can't measure, and in HFT you must measure the tail, not the mean."

### 9.1 The honest measurement: external taps

- The only ground truth is an **optical tap** on the inbound and outbound fibers feeding a capture device that hardware-timestamps both directions (Corvil/Pico, Endace DAG, Arista 7130 MetaWatch/MetaMux, Exablaze). Wire-to-wire = TX-tap timestamp − RX-tap timestamp for a causally linked tick→order pair. This captures everything (PHY, your code, the works) and cannot be gamed.

### 9.2 RDTSC-based intra-process timing

- `RDTSC`/`RDTSCP` read the CPU's invariant TSC (constant rate regardless of P-state on modern cores — "invariant TSC", check `CPUID`). It's the lowest-overhead high-resolution timer (~tens of cycles). See [Cycle Counters and Energy](@/notes/hardware/cycle_counters_and_energy.md) and [ISA Critical Instructions §RDTSC](@/notes/hardware/isa_critical_instructions.md).
- Pitfalls: `RDTSC` is not a serializing instruction — the OoO core can move it. Use `RDTSCP` (waits for prior instructions to retire) or bracket with `LFENCE` (`LFENCE; RDTSC`) to fence loads, per Intel's guidance, when measuring a tight code segment. Across cores the TSC is synced on a single socket (and across sockets on modern platforms) but pin the measuring thread. Convert cycles→ns with the known invariant frequency (not the current turbo frequency).
- Pattern: timestamp at each pipeline stage (RX, decode, decide, encode, TX) into a per-event record in a lock-free ring; a separate core aggregates into HDR histograms offline — never compute statistics on the hot path.

### 9.3 Percentile latency and histograms

- Report **p50/p99/p999/p9999/max**, not mean ± stddev (latency is heavy-tailed and bimodal). Use **HdrHistogram** (Tene) — fixed-precision, allocation-free recording, accurate high percentiles — to record every event's latency cheaply and dump the distribution.
- **Coordinated omission** (Tene): naive benchmarking that pauses sending while the system is stalled hides the worst latencies; load generators and measurement must account for it (record intended send time, not actual). HdrHistogram has correction support. This is *the* classic ULL benchmarking error.

### 9.4 Hardware performance counters

- `perf stat` and the PMU (PEBS on Intel, IBS on AMD, ARM SPE) attribute stalls: cache misses (`L1-dcache-load-misses`, `LLC-load-misses`), TLB misses (`dTLB-load-misses`), branch mispredicts (`branch-misses`), frontend/backend stalls, and **`cpu/event=smi`** to catch SMIs. PEBS gives precise (low-skid) instruction attribution — find the exact load that misses on the hot path. See [Linux Syscalls §profiling](@/notes/os/linux_expert_syscalls.md) and [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md).
- Intel PT / LBR for control-flow traces of rare tail events. `perf c2c` to find false sharing (HITM = cross-core cache hit, the false-sharing signature).

### 9.5 Jitter analysis

- `cyclictest` (from rt-tests) measures scheduling/wakeup latency of an RT thread — the canonical way to validate your isolation setup before deploying (target single-digit-µs worst case on isolated cores).
- A standalone RDTSC jitter histogram: a tight `for` loop reading RDTSC and recording deltas; spikes reveal SMIs, C-state wakeups, or stray interrupts. Used to certify a core is truly "quiet."
- Track latency *online* in production (lightweight RDTSC + HdrHistogram per stage) and alert on p999 regressions — a creeping tail is often the first sign of a config drift (THP re-enabled, irqbalance restarted, a noisy neighbor process).

---

## 10. Industry Practice and Exchange Specifics

Drawn from public talks, papers, and engineering blogs (firms are secretive; this is what is publicly attributable).

### 10.1 Firms (public sources)

- **LMAX Exchange** — open-sourced the **Disruptor** (Thompson et al., 2011) and published extensively on "mechanical sympathy" (Martin Thompson's blog/talks). The reference for single-thread, lock-free, allocation-free JVM trading. Demonstrated 6M+ orders/sec single thread.
- **Jane Street** — builds its entire trading system in **OCaml**, publicly argued for a GC'd functional language in trading (correctness + expressiveness; they tune to avoid GC on hot paths and use the type system to prevent classes of bugs). Public tech talks/blog (Yaron Minsky et al.). Demonstrates the "correctness and developer velocity can beat raw ns for many strategies" thesis — not everything is an FPGA cancel race.
- **Citadel Securities, Virtu, Jump Trading, Hudson River Trading (HRT), Two Sigma, Tower, IMC, Optiver, DRW, Flow Traders** — market makers/prop firms; publicly known to use co-location, kernel bypass (Solarflare/Onload is near-ubiquitous), and FPGAs for the most latency-critical paths. Jump Trading and HRT are publicly associated with heavy FPGA and custom-network (microwave) investment; Jump backed the New Line/microwave Chicago-NJ route. Virtu's public filings emphasize technology and risk controls across thousands of instruments globally.
- **CME / exchanges** publish co-lo and matching-engine latency stats; vendors (Solarflare/AMD, NVIDIA, Arista, Cisco/Exablaze) publish ULL benchmarks. Conference circuits: STAC (Securities Technology Analysis Center) benchmarks, and talks at CppCon / Meeting C++ (ULL C++ patterns), and the "Mechanical Sympathy" community.

### 10.2 Exchange co-location and feeds

- **Nasdaq** — Carteret, NJ data center; **TotalView-ITCH** (full depth) and various direct feeds over **MoldUDP64** multicast; equalized colo cross-connects.
- **NYSE** — Mahwah, NJ; **Pillar** architecture, **XDP** integrated feed (binary), equalized colo.
- **Cboe / BATS** — **PITCH** multicast feed; Secaucus NY5.
- **CME** (futures) — Aurora, IL (CME Globex matching engine); **MDP 3.0** market data in **SBE** (Simple Binary Encoding, fixed-offset binary), iLink order entry. The Chicago↔NJ microwave race exists because equity/ETF prices in NJ and futures in Aurora must be arbitraged.
- **Eurex / Xetra** (Deutsche Börse) — Equinix FR2, Frankfurt; **LSE** — LD4, Slough. **TSE** (Tokyo, arrowhead matching engine).
- Common feed mechanics everywhere: **UDP multicast** for the data (A/B redundant lines for loss recovery), **TCP** for order entry and snapshot/recovery, sequence numbers for gap detection, and a binary fixed-field encoding designed for branch-light parsing.

### 10.3 Network connectivity stack (what's actually in the rack)

- NIC: Solarflare/AMD X2522/X3522 (Onload/ef_vi) or NVIDIA/Mellanox ConnectX (VMA/RDMA) or an FPGA NIC (Alveo/ExaNIC/Enyx).
- Switch: an Arista 7130 / Cisco Nexus 3550 (former Exablaze/Metamako) L1 or ultra-low-latency cut-through L2/L3 for fan-out and aggregation.
- Time: rooftop GPS → Meinberg/Microchip grandmaster → PTP (linuxptp) with PTP-aware switches → NIC PHC on every box.
- Tap + capture: optical taps → Corvil/Pico or Endace for wire-to-wire monitoring and compliance timestamping.

---

## 11. State of the Art Research (2020–2025)

HFT-specific peer-reviewed work is thinner than the underlying systems research (firms don't publish their edge), but the enabling-technology literature is rich.

### 11.1 Kernel bypass / networking

- **Cai, Marty et al., "eRPC: Datacenter RPCs can be General and Fast"** (NSDI 2019) — shows lossy-network RDMA/DPDK RPCs at <2 µs and millions of req/s without special hardware; the design principles (kernel bypass, busy poll, zero-copy, scalable connection state) are directly applicable. (Kalia, Kaminsky, Andersen.)
- **Snap** (Marty et al., SOSP 2019, Google) — userspace network framework, microkernel-style; relevant to ULL userspace networking architecture.
- **Demikernel** (Zhang et al., SOSP 2021) — a library OS / datapath OS for kernel-bypass devices (DPDK, RDMA), unifying the programming model for µs-scale I/O.
- **eBPF/XDP** body of work (Høiland-Jørgensen et al., "The eXpress Data Path", CoNEXT 2018) — in-kernel fast path; AF_XDP follow-ups.
- **io_uring as a networking datapath** (Axboe; mainline Linux 5.1+ for storage, networking maturing through 6.x): **SQPOLL** (kernel-side submission polling → no syscall/op), **registered/fixed buffers**, and **zero-copy receive** (`IORING_OP_RECV_ZC`, multishot receive) push io_uring into the kernel-bypass-adjacent space. 2024 production benchmarks put it at ~4.2 µs median — between sockets and AF_XDP — and it is now an active option for market-data ingest where ef_vi-class NICs are unavailable. Not as fast as DPDK/ef_vi, but mainline and async.
- **Junction / caladan / Shenango** (Ousterhout, Fried et al., NSDI 2019/2021) — µs-scale core allocation and low-tail-latency scheduling; addresses the busy-poll-vs-efficiency tradeoff with fast core reallocation. Relevant to the "burn a core busy-polling" cost.
- **Persistent body of µs-tail-latency systems work** — Barroso et al., "Attack of the Killer Microseconds" (CACM 2017) framed the problem; subsequent OSDI/NSDI/SOSP scheduling work (Shinjuku, Shenango, Caladan, Persephone, Concord) targets the µs-scale tail directly.

### 11.2 FPGA in finance

- A steady stream of FPGA-feed-handler and FPGA-order-book papers at **FPL, FCCM, FPGA (ACM/SIGDA), and Field-Programmable** venues through 2020–2024, e.g., FPGA full-order-book construction, low-latency ITCH/OUCH parsing, and FPGA-accelerated risk checks. Leber, Geib, Litz, "High Frequency Trading Acceleration using FPGAs" (FPL 2011) is the seminal one; later work refines to sub-100 ns pipelines and on-chip book maintenance.
- **2024 product/benchmark state of the art**: Exegy/AMD's STAC-T0 result of **13.9 ns** tick-to-trade network-I/O (off-the-shelf AMD Alveo UL3524, asynchronous critical path, jitter ~200 ps) is the public reference point — see §15.1. Recent IEEE/academic work (e.g., "FPGA for High-Frequency Trading," IEEE 2024) reports per-message ITCH parsing of **20–25 ns** with **100–150 ns total pipeline** on Virtex UltraScale+ with parallel decoder modules (8.3 M msg/s peak), illustrating the parse-on-the-fly + on-chip book design the vendor appliances productize.

### 11.3 Lock-free structures and concurrency

- The classic foundations remain canonical: **Michael & Scott** lock-free queue (PODC 1996), **Herlihy & Wing** linearizability (TOPLAS 1990), **Treiber** stack; reclamation: **hazard pointers** (Michael, TPDS 2004), **epoch-based reclamation** (Fraser, 2004), **RCU** (McKenney). Modern: relaxed/wait-free SPSC and MPMC ring designs, and the practitioner literature (rigtorp's SPSCQueue, Folly, the Disruptor) that the ULL field actually deploys. See [Data Structures](@/notes/programming/data_structures.md).

### 11.4 Time synchronization

- **Sundial** (Li et al., OSDI 2020, Google) — datacenter-scale sub-µs/ns time sync with fast failure recovery; pushes PTP-class accuracy at fleet scale.
- **Graham / Huygens** (Geng et al., NSDI 2018) — probe-based, ML-aided clock sync to ~ns in software; relevant where hardware PTP isn't everywhere.
- **White Rabbit** standardized into **IEEE 1588-2019** High Accuracy profile — sub-ns over fiber; the bleeding edge of cross-site sync.

---

## 12. Consolidated Latency Number Table

| Item | Latency | Note |
|---|---|---|
| Light in fiber | ~5 ns/m (4.9 ns/m) | n≈1.46; the hard floor for distance |
| Light in air (microwave) | ~3.3 ns/m (≈c) | why microwave beats fiber long-haul |
| L1 crosspoint switch (Arista 7130 Connect) | ~4 ns | per hop, full signal regen (2024 spec) |
| FPGA-based L2 forwarding (Arista 7130) | <100 ns | FPGA-switching tier (2024 spec) |
| Cut-through ULL L2/L3 | ~350 ns – 1 µs | store-and-forward is worse |
| DC switch (store-and-forward) | ~1–5 µs | + queuing tail |
| Cross-connect 100 m | ~500 ns each way | colo patch |
| Chicago↔NJ fiber RTT | ~13.1 ms | Spread Networks |
| Chicago↔NJ microwave RTT | ~7.98–8.05 ms | McKay 2024 roadmap (Aurora↔Carteret <8.00 ms, ↔Piscataway 7.98 ms); ~8.5 ms older |
| Linux socket recv | 5–15 µs | + fat tail |
| AF_XDP recv | 2–5 µs (~2.1 µs p50, 2024) | mainline fast path; 0 context switches |
| io_uring recv (SQPOLL + fixed bufs) | ~4.2 µs p50 (2024) | mainline, no special NIC; zero-copy RX (IORING_OP_RECV_ZC) maturing |
| DPDK / ef_vi recv to user | 0.25–1 µs (DPDK ~850 ns p50, 2024) | kernel bypass; 0 context switches |
| OpenOnload (spinning) | 1–3 µs | sockets API, no change |
| Feed decode (SW, ITCH/SBE) | 0.1–0.5 µs | fixed-offset parse |
| Order-book L2 update (array) | 20–100 ns | cache-resident |
| SPSC enqueue/dequeue | <50 ns (rigtorp ~133 ns RTT, 2024) | wait-free ring; beats boost/folly |
| RDTSC read | ~10–30 cycles | + LFENCE if fencing |
| Branch mispredict | ~15–20 cycles | pipeline flush |
| L1 hit / L2 / L3 / DRAM | ~4 / ~12 / ~40 / ~100+ ns | cache hierarchy |
| Remote NUMA penalty | +50–100 ns | UPI/IF hop |
| TLB miss (page walk) | tens of ns | why huge pages |
| Software tick-to-trade (good) | 0.8–5 µs wire-to-wire | kernel bypass |
| FPGA-accelerated T2T | 100–300 ns | hybrid |
| Pure-FPGA T2T | 30–100 ns (≈<50 ns best) | fixed pipeline, ~zero jitter |
| **FPGA T2T network-I/O record (STAC-T0)** | **13.9 ns** | Exegy/AMD UL3524, Jun 2024 (was 24.2 ns); jitter ~200 ps |
| FPGA transceiver latency (UL3524 GTF) | ~2.34 ns | vs ~16 ns older GTY; hardened MAC/PCS |
| Exchange matcher inbound (CME Globex) | ~52 µs median | router→match engine; variability ~39 µs |
| PTP HW-timestamp sync | tens of ns | with PTP-aware switches |
| White Rabbit sync | <1 ns | fiber, SyncE + DDMTD |
| SMI (System Mgmt Interrupt) | tens of µs | the worst tail spike; disable in BIOS |
| MiFID II clock accuracy req. | ≤100 µs of UTC | regulatory, HFT |

---

## 13. Pitfalls and Decision Checklist

**Top jitter sources (eliminate in this order):**
1. Page faults on the hot path → pre-fault + `mlock` + huge pages, no `malloc` while trading.
2. The scheduler tick and scheduler itself → `nohz_full` + `isolcpus` + `SCHED_FIFO` + cpuset.
3. C-state wakeups + frequency scaling → `idle=poll`/cap C-state, pin frequency, disable Turbo for determinism.
4. SMIs → BIOS tuning (disable USB legacy, PM); detect via `turbostat`/PMU SMI count.
5. Interrupts on trading cores → IRQ affinity + kill `irqbalance` + busy-poll the NIC.
6. False sharing → cache-line-align/pad hot shared variables; verify with `perf c2c`.
7. NUMA crossings → co-locate thread + memory + NIC + IRQs on one node.
8. SMT contention → disable Hyper-Threading.
9. THP background compaction → `transparent_hugepage=never`, use explicit hugetlb.
10. Allocator/locks/exceptions/logging-IO on the hot path → none of them; async everything off-path.

**Decision checklist:**
- *Do I need an FPGA?* Only if you're in a contested latency race where 50 ns beats 1 µs (cancel-on-event, simple liquidity-taking). For complex/slower strategies, tuned software wins on flexibility and cost.
- *ef_vi vs DPDK vs Onload?* Onload if you want zero code change and ~µs is fine; ef_vi/TCPDirect for the lowest software path and you'll write L2/L4; DPDK if you want vendor-neutral and control the whole stack; AF_XDP if you must stay mainline and can tolerate higher latency.
- *PREEMPT_RT or tuned vanilla?* If your hot thread busy-polls on an isolated nohz_full core it barely touches the kernel — tuned vanilla often suffices; use RT for bounded control-plane latency. Always validate with `cyclictest`.
- *Measure the tail, always.* p999/max with HdrHistogram, correct for coordinated omission, and verify with an external optical tap (the only ungameable number).

---

## 14. Key References

**Foundational / industry**
- Thompson, Farley, Barker, Gee, Stewart, "Disruptor: High performance alternating exchange between threads" / LMAX Disruptor technical paper, 2011.
- Barroso, Marty, Patterson, Ranganathan, "Attack of the Killer Microseconds," CACM 2017.
- Tene, "How NOT to Measure Latency" (HdrHistogram, coordinated omission), conference talk, ~2015.
- Kyle, "Continuous Auctions and Insider Trading," Econometrica 1985 (market impact / Kyle's lambda).
- Almgren, Chriss, "Optimal Execution of Portfolio Transactions," Journal of Risk 2000.

**Networking / kernel bypass**
- Kalia, Kaminsky, Andersen, "Datacenter RPCs can be General and Fast" (eRPC), NSDI 2019.
- Marty et al., "Snap: a Microkernel Approach to Host Networking," SOSP 2019.
- Zhang et al., "The Demikernel Datapath OS Architecture for Microsecond-scale Datacenter Systems," SOSP 2021.
- Høiland-Jørgensen et al., "The eXpress Data Path: Fast Programmable Packet Processing in the Operating System Kernel," CoNEXT 2018.
- Ousterhout, Fried, Behrens, Belay, Balakrishnan, "Shenango: Achieving High CPU Efficiency for Latency-sensitive Datacenter Workloads," NSDI 2019; Fried et al., "Caladan," OSDI 2020.

**FPGA**
- Leber, Geib, Litz, "High Frequency Trading Acceleration using FPGAs," FPL 2011.
- Body of FPL/FCCM/ACM-FPGA work on FPGA order-book construction and feed handling, 2011–2024.

**Concurrency / lock-free**
- Michael, Scott, "Simple, Fast, and Practical Non-Blocking and Blocking Concurrent Queue Algorithms," PODC 1996.
- Herlihy, Wing, "Linearizability: A Correctness Condition for Concurrent Objects," ACM TOPLAS 1990.
- Michael, "Hazard Pointers: Safe Memory Reclamation for Lock-Free Objects," IEEE TPDS 2004.
- Fraser, "Practical Lock-Freedom" (epoch-based reclamation), PhD thesis / Cambridge TR, 2004.

**Time synchronization**
- IEEE 1588-2008 / 1588-2019 (PTP; 2019 adds the White Rabbit High Accuracy profile).
- Li et al., "Sundial: Fault-tolerant Clock Synchronization for Datacenters," OSDI 2020.
- Geng et al., "Exploiting a Natural Network Effect for Scalable, Fine-grained Clock Synchronization" (Huygens), NSDI 2018.
- ESMA, MiFID II RTS 25 (clock synchronization / business clock accuracy), 2017.

**OS / RT**
- `PREEMPT_RT` (mainlined Linux 6.12, 2024); rt-tests `cyclictest`.
- Intel, "How to Benchmark Code Execution Times" (RDTSC/RDTSCP/LFENCE guidance), white paper.

---

## 15. Updates and Latest Data (2024–2025)

*Added 2026-06-04. New figures and developments gathered from vendor announcements, STAC reports, engineering blogs, and conference talks. Where a number here supersedes §1/§12, the table in §12 has been annotated.*

### 15.1 The new tick-to-trade record: 13.9 ns (STAC-T0, June 2024)

The headline number in the field moved. **Exegy + AMD set a STAC-T0 record of 13.9 ns actionable tick-to-trade latency** (announced 2024-06-27), down from the prior 24.2 ns record — a **49% reduction** — and, notably, achieved with an *off-the-shelf* solution (AMD Alveo **UL3524** card) running an **asynchronous** critical path rather than a bespoke build. Two things make this number significant beyond the headline:

- **Jitter collapsed to ~200 ps** — roughly 10× lower than previous STAC-T0 entries. At this scale the figure of merit is no longer mean latency but the picosecond-scale spread; the FPGA "p50≈p99≈p999" property (§8.3) now holds down into hundreds of picoseconds.
- The gain came almost entirely from the **network ingest path**, not the algorithm: the UL3524 hardens the **MAC/PCS into the transceiver** (GTF transceivers running at 1.2 GHz), cutting 10 GbE handling from 24.2 ns to 13.9 ns. The lesson echoes the doc's thesis — at the bleeding edge the wire/PHY/MAC is the budget, not "your code."

**AMD Alveo UL3524** (announced Sept 2023, shipping 2024): purpose-built fintech FPGA card, **custom 16 nm Virtex UltraScale+** (note: 16 nm, *not* 7 nm), **64 ultra-low-latency transceivers, 780K LUTs, 1,680 DSP slices**. AMD's transceiver benchmark claims **<3 ns transceiver latency** (GTF ~2.34 ns vs the older GTY ~16 ns), marketed as ~7× faster than prior FPGA transceiver tech. The follow-on **Alveo UL3422** targets the same ultra-low-latency segment as latency gains get "increasingly marginal" (AMD's Girish Malipeddi). STAC-T0 is defined as tick-to-trade *network-I/O* latency — receive an inbound trigger, emit the order — so these numbers are the network reflex, not a full strategy.

> Industry framing (Databento, 2024): "As of 2024, most trading firms that compete on this front are capable of tick-to-trade latencies in the **single to double-digit nanoseconds**." Software baseline with kernel bypass on PCIe-3 NICs + TCP checksum offload + OpenOnload sits at **just under 2 µs**.

### 15.2 NIC generation: AMD Solarflare X4 supersedes X3522

AMD announced the **Solarflare X4** low-latency Ethernet adapter (2025) as the successor to the X3522 in the capital-markets line, claiming **up to 40% lower latency than the previous AMD Solarflare generation**, with the same kernel-bypass stack (**Onload, TCPDirect, ef_vi**). The X3522 (Alveo X3 series, up to 4× 10/25G, FPGA-fused NIC) remains the current widely-deployed part. TCPDirect's documented best case remains **~20–30 ns** of *added* stack latency under ideal hardware (the classic 2017 "latency cut to 20 ns" claim, validated by an equity trading firm). Practically: ef_vi/TCPDirect is the software floor; the X4's 40% claim moves the software-path RX number down within the same architectural envelope (sub-µs, not into the ns regime — that requires the FPGA datapath of §8/§15.1).

### 15.3 Kernel bypass benchmarks refreshed — and io_uring enters the conversation

A 2024–2025 production benchmark (NordVarg, trading-system context, processing 2.4M pkt/s) gives a clean modern comparison of the four user-visible datapaths:

| Path | p50 (median) | Tail behavior | Max pkts/s before loss | Context switches/s |
|---|---|---|---|---|
| Traditional socket | 18.4 µs | up to ~890 µs max | ~1M | 2.1M |
| **io_uring** (SQPOLL, registered buffers) | **4.2 µs** | degrades by ~5M pkt/s | ~1M (degrading) | reduced |
| **AF_XDP / XDP** | **2.1 µs** | — | ~8M | 0 (busy-poll) |
| **DPDK** | **850 ns** | p95 ~2.1 µs, max ~48 µs | 10M+ | 0 (busy-poll) |

Key takeaways and corrections to §2:
- **io_uring is now a real kernel-bypass-*adjacent* option for networking**, not just storage. With **SQPOLL** a kernel thread polls the submission ring so there is **no syscall per operation** once set up, and **registered/fixed buffers** (`io_uring_register_buffers`) give zero-copy. It lands at ~4.2 µs median — between sockets and XDP — useful when you want mainline, no special NIC, and async semantics, but it does **not** reach DPDK/ef_vi territory. This is the main "new kernel-bypass technique" since the doc was written: io_uring's **zero-copy RX** (`IORING_OP_RECV_ZC`, multishot receive, landed in Linux 6.x) is being actively explored for market-data ingest where ef_vi-class NICs aren't available.
- The **ordering still holds: DPDK/ef_vi (sub-µs) > XDP (~2 µs) > io_uring (~4 µs) > sockets (~18 µs)**, and busy-polling paths (XDP, DPDK) zero out context switches — the determinism win, not just the mean.

### 15.4 Jane Street: OCaml at the ULL frontier (concrete public detail)

The doc's §10.1 noted Jane Street uses OCaml; the public record now has mechanism-level detail worth recording:

- **Lower-latency GC** ([blog.janestreet.com/building-a-lower-latency-gc](https://blog.janestreet.com/building-a-lower-latency-gc/)): they decoupled **major GC slices from minor collections** and added an **application-driven job that forces major slices during quiet times** ("for responsive systems, it's often better to push off work until after the busy times"). Combined with interruptible array/root scanning and circular-buffer work accounting, this **cut tail latency by ~3×** in a real production app. This is the GC-language analogue of the doc's "do work off the hot path" discipline — the deferral is scheduled into market lulls.
- **Multicore OCaml**: OCaml 5.0 (Dec 2022) shipped the first multicore runtime; Jane Street **switched to it only after ~2.5 years** of internal research/engineering — a data point on how conservatively a ULL shop adopts runtime changes.
- **Language extensions for performance** (their compiler fork): **modal types** (memory-safe stack allocation, data-race-freedom for multicore, effect tracking) and **unboxed types** (control over memory representation, letting structured data live in **cache- and prefetch-friendly tabular/columnar form**) — directly the §4.1–4.2 cache-layout concerns pushed into the type system.
- Talk **"Safe at Any Speed: Building a Performant, Safe, Maintainable Packet Processor"** (Sebastian Funk) — a single-core OCaml packet processor at **millions of messages/sec at line rate**, on bridging high-level abstraction to machine-level efficiency. The thesis remains: correctness + developer velocity can win for the large class of strategies that aren't the FPGA cancel race.

### 15.5 Optiver / industry C++ practice

**Optiver — David Gross, CppCon 2024, "When Nanoseconds Matter: Ultrafast Trading Systems in C++"**: public talk covering low-latency design principles, **concurrent (lock-free) data structures to reduce latency**, and algorithm/data-structure micro-optimization. Optiver's publicly stated model: **one FPGA per product**, multi-year FPGA roadmaps (not one-off builds), thousands of concurrent positions across CME/Eurex/Nasdaq. Confirms §10.1's "hybrid FPGA + tuned C++" picture and the §5 lock-free-on-the-hot-path discipline as current best practice, not legacy.

### 15.6 SPSC queue — current practitioner benchmark

`rigtorp::SPSCQueue` (the canonical wait-free SPSC ring, §5.1) on a Ryzen 9 3900X: **~133 ns round-trip latency**, **362,723 ops/ms throughput** — beating `boost::lockfree::spsc_queue` (222 ns / 209,877 ops/ms) and `folly::ProducerConsumerQueue` (147 ns / 148,818 ops/ms) on throughput. The implementation detail that buys it: head/tail indices **aligned and padded to the cache-line / false-sharing range**, plus cached copies of the opposite index — exactly the §4.1/§5.1 rules. (RTT ~133 ns ≈ ~66 ns one-way, consistent with the doc's "<50 ns enqueue/dequeue" once you separate the round-trip framing.)

### 15.7 Exchange / matching-engine latency, refreshed

- **CME Globex**: the matching engine remains in **CyrusOne Aurora I (Aurora, IL)** as of 2026. Published Globex performance-release figures: **median inbound (router→match engine) ~52 µs**, with inbound order-entry **variability ~39 µs** (p95 minus median) and outbound market-data dissemination **variability ~58 µs**. CME has announced migration of matching engines to **Google Cloud's dedicated private regions, targeted ~2028** — a structural change worth watching (co-lo economics, the Aurora↔NJ microwave race, and the meaning of "co-location" all shift if the match engine moves to GCP).
- **Nasdaq Fusion**: the platform migration (ISE options, fixed income via "Project Fusion") delivered **~10% latency improvement and tighter determinism** on migrated markets.
- These confirm §6.5 — exchange matchers live in the **tens-of-µs** range (CME ~52 µs median inbound), versus the **single/double-digit ns** of the FPGA *consumer* reflex. The asymmetry (exchange ~µs, fastest participant ~ns) is the whole game.

### 15.8 Microwave / wireless networks, refreshed (McKay Brothers)

McKay's published roadmap for the Aurora (Chicago) ↔ New Jersey microwave route:
- Aurora ↔ **Secaucus**: target **< 8.05 ms** RTT.
- Aurora ↔ **Carteret**: target **< 8.00 ms** RTT.
- Aurora ↔ **CTS NJ3 (Piscataway)**: **7.98 ms** RTT (new PoP).

This refines the doc's "~8.5 ms microwave RTT" (§2.4/§12) downward to **~7.98–8.05 ms** for current best routes — the microwave network has kept tightening toward the air-path geodesic. McKay also added PoPs at 350 E Cermak (Chicago) and Piscataway, and operates metro/transatlantic routes (Chicago–Europe, NJ–Toronto, London via Interxion) — the wireless map keeps expanding, not contracting.

### 15.9 Time sync — White Rabbit goes commercial for finance

White Rabbit (§7.4) has moved from "bleeding edge / CERN" to a **commercial financial timing product**: **Safran/Orolia "White Rabbit for Finance"** and the **White Rabbit Z16** grandmaster, plus offerings from Oscilloquartz and Timebeat. Measured performance: **sub-nanosecond accuracy over ~5 km fiber, precision below ~10 ps**, combining PTP + SyncE + hardware timestamping. It is described as "the gold standard for time distribution within electronic trading networks" for cross-site latency monitoring, back-testing, and data science — i.e., adoption has crossed from research into deployed finance infrastructure, well beyond MiFID II's modest ≤100 µs requirement (§7).

### 15.10 Switching — Arista 7130 numbers confirmed and refined

Current Arista 7130 (post-Metamako) figures: **L1 (Connect) port-to-port ~4 ns with full signal regeneration**, non-blocking, "virtually undetectable jitter," no buffering/queuing; **FPGA-based L2 forwarding < 100 ns**. This refines §8.2/§12's "~3–5 ns" L1 number to a concrete **~4 ns** for the 7130 Connect line, and adds the **<100 ns L2** datapoint for the FPGA-switching tier.

### 15.11 Net changes to the mental model

- **Tick-to-trade record is now 13.9 ns** (was effectively "~30–50 ns best" in §8.3/§12); the FPGA floor dropped by ~2–3× and **jitter is now a picosecond conversation**.
- **io_uring is a legitimate new datapath** (~4 µs) sitting between sockets and XDP, with zero-copy RX maturing in mainline Linux — relevant where ef_vi-class NICs aren't available.
- **Microwave RTT floor tightened** to ~7.98–8.05 ms Aurora↔NJ.
- **White Rabbit is now a shipping finance product**, not just a CERN curiosity.
- **Structural watch item**: CME's matching-engine move to GCP (~2028) could reshape co-location economics.

---

## 14b. Key References (2024–2025 additions)

- Exegy/AMD, "New STAC-T0 record: 13.9 ns tick-to-trade" (STAC-T0, 2024-06-27) — [exegy.com](https://www.exegy.com/exegy-amd-new-record/), [STAC report AMD240422](https://docs.stacresearch.com/news/AMD240422).
- AMD, "Alveo UL3524 — purpose-built FPGA for ultra-low-latency electronic trading" (Sept 2023) — [amd.com product page](https://www.amd.com/en/products/accelerators/alveo/ul3524.html); [STAC Summit slides, Oct 2023](https://docs.stacresearch.com/system/files/resource/files/STAC-Summit-19-October-2023-AMD.pdf).
- AMD, "New AMD Solarflare X4 Low-Latency Ethernet Adapter" (2025) — [amd.com blog](https://www.amd.com/en/blogs/2025/new-amd-solarflare-x4-low-latency-ethernet-adapter-set.html).
- Jane Street, "Building a lower-latency GC" — [blog.janestreet.com](https://blog.janestreet.com/building-a-lower-latency-gc/); "Safe at Any Speed" tech talk (S. Funk) — [janestreet.com/tech-talks/safe-at-any-speed](https://www.janestreet.com/tech-talks/safe-at-any-speed/).
- Gross, D., "When Nanoseconds Matter: Ultrafast Trading Systems in C++," CppCon 2024 (Optiver) — [optiver.com career hub](https://optiver.com/working-at-optiver/career-hub/designing-low-latency-cpp-systems/).
- Rigtorp, E., SPSCQueue benchmark — [github.com/rigtorp/SPSCQueue](https://github.com/rigtorp/SPSCQueue), "Optimizing a Ring Buffer for Throughput" — [rigtorp.se/ringbuffer](https://rigtorp.se/ringbuffer/).
- NordVarg, "Kernel Bypass Networking: DPDK, io_uring, and XDP Compared" (2024–2025) — [nordvarg.com](https://nordvarg.com/blog/kernel-bypass-networking).
- Databento, "What is tick-to-trade latency?" — [databento.com/microstructure/tick-to-trade](https://databento.com/microstructure/tick-to-trade); "Where is the CME matching engine located?" — [databento.com/blog/cme-colocation](https://databento.com/blog/cme-colocation).
- McKay Brothers, low-latency microwave roadmap (Aurora↔NJ < 8.0 ms) — [mckay-brothers.com](https://mckay-brothers.com/).
- Safran/Orolia, "White Rabbit for Finance" / White Rabbit Z16 — [safran-navigation-timing.com/white-rabbit-for-finance](https://safran-navigation-timing.com/white-rabbit-for-finance/).
- Arista, "Measuring the latency of a 4ns switch" (7130 Connect L1) — [arista.com solution brief](https://www.arista.com/assets/data/pdf/Latency-4ns-Switch-Solution-Brief.pdf).

---

## 16. Pre-Trade Risk, Regulation, and Compliance Tech

### 16.1 SEC Rule 15c3-5 — Pre-Trade Risk on the Hot Path

**What the rule is.** SEC Rule 15c3-5 ("Market Access Rule", adopted 2010, 17 CFR 240.15c3-5) requires any broker-dealer with market access to maintain pre-trade risk controls under its *direct and exclusive control*. This banned "naked" sponsored access (prop firm orders straight to exchange through a broker's MPID with no broker-side checks). Controls must be applied **pre-trade** to *all* orders.

**Legally required check categories:**

| Category | Requirement | Hot-path realization |
|---|---|---|
| Credit/capital thresholds | Block orders exceeding pre-set credit or capital limits | Aggregate notional/position accumulator per account |
| Fat-finger / erroneous order | Block orders that "appear erroneous" | Price-band (collar vs. reference price) + max order size + max notional |
| Order-rate / duplicate | Prevent runaway message floods | Token-bucket throttle (msgs/sec/symbol) + duplicate-order detection |
| Regulatory pre-conditions | Block restricted symbols; no order unless pre-order checks pass | Hard-block table lookup |
| Post-trade surveillance | Immediate execution reports to surveillance | Async, off hot path |

**FPGA implementation.** All required checks reduce to comparisons and table lookups against pre-loaded limits — ideal for combinational logic in an FPGA "bump-in-the-wire" gateway between strategy host and exchange:
- Fat-finger/price-band: comparator against NBBO snapshot register
- Max notional: fixed-point `qty × price` + comparator
- Position/credit limit: running accumulator in BRAM, updated on each fill, compare-before-send
- Message rate: token-bucket counter in registers
- Restricted symbol: hash/CAM lookup

These run in parallel as the order packet streams through, so added latency is **tens to ~300 ns** rather than the µs+ a software gateway adds.

**Vendor latency numbers:**
- **Magmio** FPGA pre-trade risk gateway: "as low as **200 ns**" — runs on Cisco Nexus SmartNICs (V9P-3, V5P, K3P-S/Q) and AMD Alveo UL3524/X3522PV
- **Algo-Logic** PTRC (Alveo U50/U200/U250, Cisco Nexus V5P): "well under one microsecond"; all 15c3-5 checks completing in tens of ns within the full pipeline
- **Enyx nxFramework**: framework for FPGA pre-trade risk gateways, SORs, and tick-to-trade engines

Software risk gateway: **single-digit to low-tens of µs**. FPGA inline: **~25–300 ns deterministically** (no tail latency). The decisive property is determinism: FPGA limits never spike.

**Enforcement cases:**

- **Knight Capital (SEC, Oct 2013, $12M fine).** Aug 1 2012: a defective deployment left obsolete "Power Peg" code active on one of eight servers. On market open it sent **>4M erroneous orders in ~45 min**, trading ~397M shares and accumulating ~$3.5B long / ~$3.15B short unwanted positions — a **~$460M loss** that nearly destroyed the firm. SEC found: no automated control linking account-level limits to firm-wide capital; financial controls "not capable of preventing" threshold breaches; reliance on manual human monitoring; **no kill switch**; deficient CEO certification. The canonical lesson: pre-trade controls must be automated, firm-wide, and capable of *blocking*, not just alerting.
- 15c3-5 remains a recurring FINRA examination priority (2021–2026 oversight reports), with common findings: limits set so wide they never trigger, inadequate duplicate-order controls.

### 16.2 Smart Order Routing (SOR) and Multi-Venue Latency

**Reg NMS obligations.** US equity liquidity is fragmented across ~16 exchanges + dozens of ATSs/dark pools. Two Reg NMS rules drive SOR:
- **Rule 611 (Order Protection / trade-through rule):** cannot execute at a price inferior to a *protected quotation* on another venue. Makes the **NBBO** the reference every router respects.
- **Best execution** (FINRA Rule 5310): seek most favorable terms across price, speed, fill probability, and fees.

To satisfy Rule 611 at scale, routers fan out **Intermarket Sweep Orders (ISOs)** — orders marked to take out multiple protected quotes simultaneously, shifting trade-through compliance onto the sender and enabling parallel execution.

**Latency implications of simultaneous multi-venue routing.** Venues sit in different data centers (NYSE Mahwah NJ, Nasdaq Carteret NJ, Cboe/BATS Secaucus NJ, etc.) with different fiber distances. Orders sent simultaneously arrive at different times — letting fast HFTs detect the order at the nearest venue and race to the others. **RBC THOR** deliberately staggers send times so slices arrive simultaneously at all venues, defeating the cross-venue race.

**IEX 350 µs speed bump:**
- IEX coils **38 miles of fiber**, imposing **~350 µs** one-way delay (≈700 µs round trip) on inbound orders and outbound messages
- Delay exceeds the time IEX needs to recompute the NBBO — so its engine "sees" away-market price changes before an arb order can act. Powers the **Crumbling Quote Indicator (CQI)**: a model over sequential away-exchange quote updates predicting an imminent NBBO move. CQI fires for **~2 ms**; during that window **D-Peg** and **D-Limit** orders re-price 1 MPV ($0.01) less aggressively.
- **Reg NMS ruling (2016):** SEC ruled the speed bump is a *de minimis* delay consistent with Rule 611 "immediate access" — IEX quotes **are** protected. Routing around it: fire an ISO to IEX and route the rest of the sweep immediately without waiting for IEX's delayed response.

**Venue selection under latency constraints.** A production SOR ranks venues by: displayed size at NBBO, historical fill/hit ratio, expected toxicity (adverse selection), fees vs. rebates (maker-taker), and RTT to each matching engine. Modern SORs add ML-based fill-probability and short-horizon price prediction. **FPGA SOR** (Enyx and others): venue selection + ISO generation in hundreds of ns.

### 16.3 MiFID II / RTS 25 and CAT — Compliance Timestamping

**EU — MiFID II RTS 25 (clock sync) + RTS 24 (order records):**
- Clock accuracy: business clocks must track UTC; for HFT max divergence = **100 µs** with timestamp **granularity ≤ 1 µs** (RTS 25 Table 2). Non-HFT members get looser bands.
- RTS 24 order records: venues retain order-book data with 28 fields per buy/sell decision, 35 fields per executed order. ESMA's 2024 RTS 22/24 review proposes moving from XML → **JSON**.
- Time source: NTP is insufficient at 100 µs/1 µs. Firms use **PTP (IEEE 1588v2)** disciplined by GPS/GNSS grandmaster clocks distributed via PTP-aware switches. Eurex piloted **White Rabbit** (~200 ps achievable) with FPGA-based switches for internal timestamping.

**US — CAT (SEC Rule 613):**
- Clock sync: member clocks within **50 ms of NIST UTC**
- Granularity: reportable events in **≥1 ms** increments; if a firm captures finer than ms (down to nanoseconds), it *must report at that finer granularity*. HFTs running ns clocks must surface ns timestamps to CAT.

| | MiFID II RTS 25 (HFT) | US CAT (Rule 613) |
|---|---|---|
| Clock tolerance vs UTC/NIST | **100 µs** | **50 ms** |
| Timestamp granularity | **≤1 µs** | **≥1 ms** (finer if captured) |
| Sync transport | PTP + GPS / White Rabbit | NTP/PTP + GPS |

MiFID II is ~500× tighter on clock tolerance than CAT.

**Async logging pattern — keeping compliance off the hot path:**
1. **Passive wire capture** (optical tap + hardware-timestamping NIC/probe): authoritative ingress/egress timestamps with **zero application overhead** — preferred source for RTS 25 "time of receipt/transmission"
2. **In-app events via cheap reads**: read hardware TSC in **<10 ns**, store raw count, defer UTC conversion and serialization to an off-path thread
3. **Deferred publication to lock-free ring**: hot path writes compact record (raw counter + IDs) to pre-allocated SPSC ring; separate consumer batches, converts, persists. Hot-path overhead: **<10 ns**, no file I/O, no syscalls, no allocation
4. **Post-hoc UTC interpolation**: convert TSC → UTC offline by interpolating between periodic PTP-clock observations and the free-running TSC

Net: decouples *measurement* (must be precise, on-wire) from *recording* (must be durable, can be async) — meets 1 µs granularity / 100 µs UTC without adding hot-path latency.

---

## 17. Transatlantic and Long-Haul Microwave / Laser Networks

### 17.1 The transatlantic latency problem

The EU↔US arb runs between **LD4** (Equinix Slough, near London) and **NY4** (Equinix Secaucus NJ), with the Chicago futures complex (CME, Aurora IL) as the western anchor. Theoretical great-circle minimum NY↔London ≈ **~37 ms round trip** (~18.6 ms one-way) at c-in-vacuum. Fiber is far slower: light travels ~31% slower in glass, and cables don't follow great-circle paths.

**Transatlantic fiber (baseline):**
- **Hibernia Express** (now GTT), purpose-built lowest-latency cable, in service Sept 2015: **<58.95 ms round trip** NY4↔LD4 (≈29.5 ms one-way). Corning EX2000 pure-silica-core fiber on the shortest surveyed great-circle path — ~5 ms faster than prior fastest cable, >20% better on the subsea segment.
- **McKay Brothers / Quincy Extreme Data** published one-way figures from Aurora IL:
  - Aurora → Slough (LD4): **34.619 ms**
  - Aurora → Frankfurt (FR2): **36.917 ms**
  - Aurora → Marseilles: **41.444 ms**

These combine terrestrial microwave (US + Europe) with transatlantic fiber — **microwave is impossible across the open ocean** (line-of-sight limited to ~50–100 km between relay towers).

### 17.2 Transatlantic RF / shortwave (the cutting edge)

The Atlantic crossing uses **HF/shortwave radio**, which bounces off the ionosphere and sea surface (skywave) to cover thousands of km in a few hops with no fiber path-length penalty:
- Reported figures: NY↔London **~20–22 ms one-way** via radio vs **~33 ms** via fastest fiber — roughly **50% faster**, approaching the speed-of-light minimum because air propagation beats glass
- Trade-offs: very **low bandwidth** (kbps-class — only enough for a compressed signal/trigger, not full market data); **weather/ionospheric instability**
- **Operators:** **Vigilant Global** (DRW's network arm), **McKay Brothers / New Line Networks** (McKay's JV with Jump/GTS lineage), and other prop firms. Vigilant and McKay have collaborated on shared masts (e.g., Richborough, England) for terrestrial legs. **IMC** took a stake in **Quincy Data** (McKay's market-data arm).
- Microwave land networks generically run **~40%+ faster than fiber** (light in air ≈ 1.5× speed in glass).

### 17.3 Metro laser / FSO (free-space optics)

- **Anova** built a **laser FSO + millimeter-wave RF hybrid** link across the ~35 miles between **NYSE Mahwah** and **Nasdaq Carteret** NJ data centers. Lasers on towers transmit up to ~20 km per hop using **adaptive auto-tracking gimbals** that tolerate up to 3° of tower twist/sway; falls back to mmWave RF during fog/rain.
- **Hollow-core fiber** (air-guiding): light travels ~**99.7% of c** vs ~68% in standard solid silica, cutting metro latency ~30% on the last few km where RF line-of-sight is hard in dense urban areas. Emerging weapon for Slough↔City of London and similar metro legs.

### 17.4 Transatlantic latency summary

| Path | One-way | Notes |
|---|---|---|
| Speed-of-light minimum | ~18.6 ms (~37 ms RT) | great-circle, vacuum |
| Hibernia Express fiber | ~29.5 ms (58.95 ms RT) | fastest dedicated cable, 2015 |
| Fastest transatlantic fiber | ~33 ms | cited pre-radio baseline |
| HF/shortwave radio | ~20–22 ms | ~50% faster, kbps bandwidth, weather-dependent |
| McKay QED Aurora→Slough | 34.619 ms | terrestrial microwave + transatlantic fiber |

---

## 18. Order Flow Toxicity, Adverse Selection, and Alpha Decay

### 18.1 The adverse selection problem

A passive market maker profits from the spread only if counterparties are uninformed. When the counterparty knows something the maker doesn't, the maker is "picked off": buys right before price drops, or sells right before it rises. This is the **winner's curse** of market making — you get filled precisely when you're on the wrong side. Systematically toxic flow drives makers to widen or withdraw quotes.

### 18.2 VPIN — Volume-Synchronized Probability of Informed Trading

**Source:** Easley, López de Prado, O'Hara. *"Flow Toxicity and Liquidity in a High-Frequency World."* Review of Financial Studies 25(5), 2012.

**Mechanism:**
1. **Volume bucketing.** Partition the trade tape into equal-volume buckets V. Volume-time expands during active periods and contracts when quiet — exactly when informed trading concentrates.
2. **Bulk Volume Classification (BVC).** Classify volume in each bucket as buy vs. sell probabilistically using the standardized price change over the bucket mapped through a Student-t CDF: `V_buy = V · t(ΔP/σ)`, `V_sell = V − V_buy`. Robust at high message rates where the tick rule degrades.
3. **Toxicity estimate.** Over a rolling window of n buckets:
   ```
   VPIN ≈ Σ|V_buy − V_sell| / (n · V)
   ```
   Values near 0 → balanced/benign flow; values approaching 1 → severe one-sided (toxic) flow.

**Flash Crash evidence (May 6, 2010):** VPIN on the S&P 500 E-mini rose steadily and reached extreme levels (≈0.8+) in the hours *before* the ~9% intraday plunge — consistent with toxicity driving liquidity providers to withdraw. Note: VPIN's predictive power is contested (Andersen & Bondarenko, 2014 argue it's largely an artifact of volume-volatility clustering) — present as a debated metric.

### 18.3 Fill-ratio and markout analytics

Firms monitor realized fill quality in near-real-time:
- **Fill ratio** = fills / (fills + cancels) or fills / quotes. Sudden change signals queue-position loss or selective adverse filling.
- **Markout / post-fill drift:** track mid-price movement at fixed horizons after a passive fill (+10 µs, +100 µs, +1 ms, +1 s). Persistent adverse markout = being adversely selected. The practitioner's direct PnL-attributed toxicity measure, finer-grained than VPIN.
- **Message-to-trade ratio baseline:** post-2009, cancel/execution ratios shifted from ~26:1 to ~32:1. Anomalies must be measured against this heavy-cancel baseline.

### 18.4 Quote stuffing

**Mechanism:** submit and immediately cancel an extraordinarily large number of orders — thousands to **>10,000 messages/second** — to saturate a shared market-data feed or matching-engine outbound bandwidth. Phantom orders can be in *any* symbol sharing a price feed, so a competitor watching a different name on the same multicast group is slowed without being directly targeted.

**Detection/defense:**
- Surveillance on abnormally high message-to-trade ratios and burst submit-then-cancel patterns; ML/anomaly models flag deviations from the cancel-heavy baseline
- A/B redundant feed paths (ITCH A/B lines), gap-fill/recovery channels, FPGA feed handlers that parse line-rate without buffering, consuming a less-contended feed (direct binary vs. consolidated SIP/CQS)

### 18.5 Alpha decay — quantifying the value of a microsecond

Two distinct decays:
- **Per-event decay (latency sensitivity):** within a race, value drops sharply over µs/ns. On CME, the top-of-queue participant can receive private fill confirmations **up to ~11 µs before** those trades print to the public tape — that window is pure informational edge for a faster actor.
- **Strategy-level decay (signal aging):** signal Sharpe/strength degrades roughly **5–10% per year** under normal conditions. Infrastructure latency disadvantage estimated to cut returns by **~5.6% (US)** and **~10% (Europe)** in competitive equities/FX (Maven Securities / Exegy).
- **Economic framing:** feed-handler build cost cited at **>$5.3M** initial, **$400K–$600K** per additional handler — the µs saved must be amortized against substantial capex, which is why firms quantify $/µs.

---

## 19. Network Topology Inside the Colo Rack

### 19.1 Physical wiring

A latency-critical colo deployment minimizes hops and conversions:
- **Trading server** with low-latency NIC/FPGA (Exablaze/Cisco Nexus SmartNIC, Solarflare/AMD X2/X3/X4, NVIDIA ConnectX, or FPGA card like Alveo UL3524)
- **Cross-connect** from rack to exchange matching-engine handoff (metered patch in meet-me room). Exchanges enforce **length-equalized cross-connects** (CME, Nasdaq, NYSE Mahwah, LSE) — every colo tenant gets the same fiber length. You cannot beat your neighbor by being in a closer rack; you beat them with the box.

### 19.2 Cable choices and latency

| Medium | Propagation | Added device latency | Reach | Use |
|---|---|---|---|---|
| **DAC (Direct Attach Copper), passive** | ~**5.2 ns/m** | none (no SerDes retimer) | ≤~3–7 m | NIC-to-NIC / NIC-to-switch within or between adjacent racks |
| **AOC (Active Optical Cable)** | ~5 ns/m | **~5–10 ns** E/O/E conversion per end | up to ~100 m+ | across-row, within-hall |
| **SMF patch + transceivers** | ~**5 ns/m** (~5 µs/km) | transceiver ~tens–hundreds of ns; full SFP+ link path ~**300 ns** | meters to km | pod-to-pod, room-to-room, exchange cross-connect |

Explicit physical-layer budget: connector insertion + transceiver/SerDes (dominant fixed cost) + cable propagation + patch-panel pass-throughs + PHY retiming on active cables.

### 19.3 Why direct-attach bypasses the switch

For the single most latency-critical link, firms run a **point-to-point DAC directly between two NICs** — eliminating the switch entirely. A cut-through 10/25G switch adds ~300 ns–1 µs plus a port pair of SerDes; removing it eliminates that budget line. Trade-off: no fan-out, no L2 features — used only where topology is fixed and µs matters more than flexibility. For ≤~7 m links, passive DAC also beats a two-transceiver fiber link because it has no optical conversion.

### 19.4 Spine-leaf vs. direct-attach

Spine-leaf (predictable hop count, scale-out, ECMP) is used for **non-hot paths** in trading deployments: management, market-data distribution to many consumers, backtest clusters. For the **tick-to-trade hot path**, firms reject general spine-leaf in favor of the flattest possible topology: direct-attach or a single cut-through switch. Every additional hop = an extra SerDes pair + switching latency.

### 19.5 Power and thermal

- Industry rack-density shift (2024–25, Omdia): 20–30 kW/rack now ~28% of capacity, 10–20 kW ~30%. HFT racks packed with FPGA accelerators + high-clock CPUs sit in the higher bands.
- **Rear-door heat exchangers (RDHx):** passive ~40 kW/rack; active (Motivair ChilledDoor) **~72–75 kW/rack**. Liquid-to-air at the rack door — simplest retrofit.
- **Direct liquid cooling (DLC / cold-plate):** roadmaps to **~250 kW/rack** for densest deployments; also gives tightest die temperatures → best throttle avoidance → most deterministic clocks.
- **AMD Alveo UL3524:** draws up to **180 W, passively cooled** (relies entirely on chassis airflow). Several per server + dual high-TDP CPUs → single 2U node can reach several hundred watts.

### 19.6 CPU thermal throttling as a latency-tail source

Intel Turbo Boost opportunistically raises frequency above base clock while power (PL1/PL2) and thermal (Tjmax) headroom exist. When a core hits a thermal or power limit, it throttles — frequency drops, and **worst-case execution time becomes a function of thermal state**, shattering the determinism assumption. A throttle event is a multi-µs-to-ms latency spike in the tail.

**Why HFT runs CPUs below max turbo:** a clock pinned to a **fixed, sustainable frequency with thermal headroom never produces a throttle spike** — predictability beats peak. The standard HFT CPU config: performance governor, C-states off (C0/C1 only), Turbo off or capped, SMT off, frequency fixed, NUMA-pinned, IRQs steered away. This is the inverse of consumer tuning: HFT trades absolute throughput for **tail-latency determinism**.

**Power-delivery stability:** a brownout or PDU transient can trigger CPU PROCHOT/PL excursions or VRM ripple → transient frequency dip → latency tail event, even with no observable downtime. Standard hardening: dual PSUs on independent A/B PDUs, N+1 or 2N UPS, per-outlet metering.

---

## 20. Memory-Mapped Kernel Interfaces

### 20.1 vDSO — virtual dynamic shared object

**Mechanism:** the kernel maps a small shared library (`linux-vdso.so.1`) into every process's address space, plus a read-only vvar page of kernel-maintained timekeeping data (TSC multiplier/shift, base time, clock mode). vDSO-accelerated calls execute **entirely in user space** — no `syscall` instruction, no ring transition, no register save, no TLB effects.

**Accelerated calls (x86-64):** `clock_gettime`, `gettimeofday`, `time`, `getcpu`, `clock_getres`. `clock_gettime` reads the TSC, applies the mul/shift from vvar, returns — no kernel entry.

**Latency:** vDSO `clock_gettime` ≈ **~10–30 ns** vs. **~100+ ns** for the equivalent real syscall. `CLOCK_*_COARSE` variants skip the TSC read and return jiffy-resolution (~1–4 ms) base — cheapest of all.

**Fallback trap:** if the kernel marks the TSC unreliable (`VDSO_CLOCKMODE_NONE` — after live VM migration, CPU hotplug, or TSC instability), vDSO silently falls back to a real syscall → ~10× slower. On EC2/virtualized hosts this is a known tail-latency source. **HFT requirement:** pin to a host with `tsc` + `nonstop_tsc` + `constant_tsc`, `clocksource=tsc` in boot params; avoid `hpet`/`acpi_pm` (hundreds of ns–µs).

For the finest intra-process timing, skip even the vDSO and read TSC directly via `rdtscp` — see §9.2.

### 20.2 MSG_ZEROCOPY / SO_ZEROCOPY (send-side)

**Source:** de Bruijn & Dumazet, netdev 2.1 (2017); kernel `Documentation/networking/msg_zerocopy.rst`.

**Mechanism:** instead of copying user buffer into kernel skb memory, the kernel **pins user pages** and references them directly in the skb. Application must not reuse the buffer until it receives a **completion notification** on the socket's error queue (`MSG_ERRQUEUE` via `recvmsg`), which carries a range of sequence IDs identifying completed sends.

**When it helps vs. hurts:**
- Copy cost is replaced by **page accounting + pinning + completion-notification** overhead. Only pays off for **large writes (~≥10 KB)**. For small messages (trading orders are tens to hundreds of bytes), bookkeeping cost **exceeds** the copy it avoids — net loss.
- If data has gone cold in cache by the time it's DMA'd, deferred zero-copy can be *more* expensive than an immediate copy. The kernel signals this via `SO_EE_CODE_ZEROCOPY_COPIED` on the completion — stop setting `MSG_ZEROCOPY` when you see that flag.
- **HFT verdict:** rarely useful on the hot order path. Relevant for bulk paths (snapshot recovery, market-data replay, log shipping).

### 20.3 io_uring zero-copy RX (`IORING_OP_RECV_ZC`)

**Source:** Begunkov & Wei, LPC 2023 / NetDevConf 2024; `Documentation/networking/iou-zcrx.rst`.

**Mechanism:** the NIC DMAs incoming payload directly into a pre-registered **userspace refill ring** of buffers. Requires NIC **header/data split** (headers → kernel for normal TCP processing; payload → user region) and flow steering to the right queue. Crucially, the **kernel still runs the TCP/IP stack** on headers — keeps vanilla TCP and socket semantics while removing the payload copy.

**Numbers (NetDevConf 2024):** ~**90.4 Gbps (+31.4% over epoll)** at 1500 B; ~**116.2 Gbps (+41.4%)** at 4096 B. Efficient even at smaller sizes, unlike send-side `MSG_ZEROCOPY`.

**Decision matrix:**
- Absolute lowest, most deterministic tick-to-trade → **full kernel bypass** (DPDK / OpenOnload / ef_vi / FPGA TCP offload)
- Kernel TCP + cut copies on high-rate RX → **io_uring ZC RX**
- Bulk send of large buffers → **MSG_ZEROCOPY**
- Timestamps on every hot-path event → **vDSO `clock_gettime`** (TSC verified), or read TSC directly via `rdtscp`

---

## 21. Language and Runtime Choices

### 21.1 Rust in HFT

**Rationale.** Rust targets the same niche as C++ for HFT: AOT-compiled native code, no GC, manual control over layout and memory — plus compile-time memory safety (ownership/borrow checking) that eliminates use-after-free, data races, and buffer overflows. The most valuable property for trading is **tail-latency determinism**: no GC means no stop-the-world pauses, so p99/p999 is tight and predictable.

"Zero-cost abstractions" means high-level constructs (iterators, `Option`/`Result`, trait objects when monomorphized, `async` futures) compile to the same machine code you'd write by hand. Async futures compile to state machines with no heap allocation for the future itself; Tokio I/O tasks consume ~200–400 bytes each.

**Concrete numbers:**
- A practitioner tick-to-trade prototype measured ~12 µs to process a quote, ~6 µs for a trade in Rust — targeting single-digit-µs with p999 ~4–5× median
- Order-execution microbenchmarks put hot-path ops at ~120 ns in Rust vs. ~110 ns in C++ — a few percent behind in lab conditions, at parity or ahead in real-world conditions where safety guarantees reduce bug classes that cause latency spikes
- Allocator swap (jemalloc/mimalloc as `#[global_allocator]`) commonly beats system allocator by **30–50%** on small-object tight-loop workloads

**Pain points and controls:**
- **Allocator on the hot path:** set `#[global_allocator]` to jemalloc/mimalloc, or — the real HFT answer — pre-allocate everything at startup and use object pools / arenas / ring buffers so the steady state does **zero allocation**
- **Panic unwinding overhead:** set `panic = "abort"` in the release profile. Eliminates landing-pad code, lets the optimizer assume no unwind — measured ~13% smaller binary, ~11% faster compile; runtime gains smaller but real on instruction-dense paths. Trading processes want `abort` anyway: a panic is a bug and should crash, not unwind through a half-updated order book.
- **Tokio tail latency:** Tokio's multi-threaded scheduler is built for I/O throughput, not deterministic latency. Tasks only yield at `.await`; a CPU-heavy section without yield points stalls a worker. Under load, maximum latencies in the **tens of milliseconds** (Tokio issue #2702: 26–29 ms tails on a TCP echo client under load). HFT hot paths **avoid Tokio entirely**: OS threads pinned to cores (`core_affinity`), busy-poll, lock-free SPSC/MPSC ring buffers. Tokio is fine for the cold path (config, control plane, slow-path networking).

**Low-level control surface:**
- **`#![no_std]` + custom allocators:** removes std runtime dependency for the hottest components; path to kernel-bypass or embedded/FPGA-adjacent code
- **`std::hint::spin_loop()`:** emits the `PAUSE` instruction (x86) in busy-wait loops — same as C++'s `_mm_pause()`. Reduces power and frees the pipeline without yielding the core. Exactly what a busy-polling network thread wants.
- **`core::sync::atomic`:** same C++11/C++20 memory model orderings (`Relaxed`, `Acquire`, `Release`, `AcqRel`, `SeqCst`) backed by the same LLVM atomics as `std::atomic` — atomic codegen is effectively identical. `Acquire`/`Release` maps 1:1 to C++ `memory_order_acquire/release`.

**Verdict:** Rust is a credible HFT hot-path language. C++ retains an edge only in ecosystem maturity (existing libraries, FPGA toolchains, decades of tuning) and a few percent in microbenchmarks. The hot path looks the same in both: no allocation, no GC, pinned threads, busy-poll, lock-free ring buffers, SBE-style binary codecs.

### 21.2 JVM (Java) in HFT

The JVM is used in production HFT by neutralizing the GC and JIT warmup.

**GC strategies:**
- **Azul Zing / C4 (Continuously Concurrent Compacting Collector):** concurrent, compacting, "pauseless" — GC work done concurrently with application, no stop-the-world compaction. **LMAX Exchange publicly reported up to 50% latency improvement** moving to Zing, plus "typically requires little or no GC tuning." Zing also includes ReadyNow! to pre-warm the JIT.
- **OpenJDK ZGC / Shenandoah:** concurrent collectors targeting **sub-1 ms pauses** regardless of heap size (ZGC production-ready in JDK 15). Brings much of Zing's benefit into open-source, narrowing Azul's commercial moat.

**Mechanical-sympathy ecosystem:**
- **LMAX Disruptor:** lock-free ring buffer, pre-allocated, cache-line-padded — millions of ops/sec with single-digit-µs hand-off, no GC churn
- **Chronicle Queue / Wire / Bytes:** off-heap, memory-mapped, zero-GC persisted messaging and serialization — keeps market data and order logs entirely out of the Java heap

**Primary source:** Brian Nigito's KCG systems (built in Java, acquired by Virtu in 2017) — a documented example of Java in serious electronic market-making. The pattern: write Java that "doesn't look like Java" — zero steady-state allocation, off-heap data, pinned threads, busy-spin. Java is used where developer velocity and a large codebase matter and low-double-digit-µs is acceptable; not where single-digit-µs or below is required.

### 21.3 Go — generally excluded from sub-µs HFT

Go's GC (concurrent, non-compacting mark-sweep with write barriers) targets sub-100-µs pauses — catastrophic when the latency budget is hundreds of nanoseconds. Write barriers add steady-state hot-path cost. The goroutine M:N scheduler introduces non-deterministic scheduling latency; CPU-bound goroutines can starve timers (golang/go #38860). Go appears in crypto and mid-frequency systems; essentially absent from sub-µs equities/futures HFT. The Discord Go→Rust migration is the canonical "hit Go's GC tail-latency wall" case study.

### 21.4 OCaml — Jane Street's bet

Jane Street runs its trading in **OCaml** (30M+ lines, 500+ OCaml programmers). OCaml gives ML-family type safety and a fast native compiler; its runtime GC is the latency hazard. Two threads of engineering:

- **Lower-latency GC** (with Damien Doligez): aging (objects survive several minor collections before promotion), incrementalized array/root scanning (interruptible, smaller worst-case pauses), decoupled major slices from minor collections, smoothed work accounting via a circular buffer, segmented free lists. Result: **~3× tail-latency reduction** in production, partly by forcing major GC slices during quiet times so collection never lands mid-burst.
- **OCaml 5.0 multicore (Dec 2022):** first multicore runtime, built on the **Domain** model — domain ≈ OS thread/core with its own minor heap (minor collections domain-local), shared major heap. Jane Street took **~2.5 years** to adopt runtime-5, hitting GC-pacing and resource-usage regressions from the rewritten runtime.
- **OxCaml** (Jane Street's open-source OCaml branch): adds **locality modes** for stack allocation without GC. Values at `local` mode live in the function's stack region and are freed on region exit — no heap, no GC pressure on hot paths. Modes are deep (apply through data structures); `global` is a sub-mode of `local` (stack data can point at heap, never the reverse); `exclave` keyword lets a stack-allocated value be returned into the caller's region. OxCaml also adds Rust-style data-race-free parallelism ("Oxidizing OCaml").

**Language decision summary:**

| Language | Target latency | Key mechanism | HFT fit |
|---|---|---|---|
| C++ | sub-100 ns | deterministic, zero GC, full control | dominant, all tiers |
| Rust | sub-100 ns | same as C++ + memory safety, no GC | growing, credible |
| Java (Zing/ZGC) | low double-digit µs | pauseless/sub-ms GC + Disruptor | viable for large codebases |
| OCaml (Jane St) | single-digit µs | native code + engineered GC + OxCaml stack alloc | Jane Street-specific bet |
| Go | ~10+ µs with effort | sub-100 µs GC target (still too slow) | excluded from sub-µs |

---

## 22. Custom ASICs in Trading

Almost no first-party disclosure of full custom ASICs (taped-out, fabricated silicon) for trading logic. Firms treat hardware as the deepest part of their moat. What's documented publicly is almost entirely **FPGA-based**.

**Why FPGAs dominate over ASICs:**
- **Reconfigurability:** exchange protocols (SBE schemas, gateway behavior) change; strategies change nightly. An FPGA bitstream re-flashes overnight; an ASIC is immutable once fabricated. This is the decisive factor.
- **NRE cost and time-to-market:** custom ASIC NRE (mask sets, verification, tape-out) runs into the millions and takes 12–24 months. FPGAs have zero per-unit fabrication cost and weeks-to-months iteration.
- **Latency is already "good enough":** Arista 7130 L1 switching at ~45 ns round-trip using FPGA logic; Exablaze's L1 switch at **2.4–4.6 ns port-to-port**. The marginal latency win from an ASIC is small relative to its rigidity.
- **Vendor data point:** Exablaze stated it "does not have the sales volume to do a custom ASIC for its Ethernet controller" and uses an FPGA as the main brain — a direct admission that even a dedicated trading-hardware vendor couldn't justify an ASIC.

**When an ASIC would make sense:** all three conditions: (1) extreme sustained **volume** to amortize multi-million-dollar NRE; (2) a **stable, frozen protocol/function** that won't change over the chip's life (e.g., a fixed layer-1 crosspoint, PHY, or feed-decode primitive — not strategy logic); (3) a **latency target below what an FPGA can hit**.

**Practical convergence: eASIC / eFPGA hybrid.** The industry converges on structured-ASIC or embedded-FPGA fabric: near-ASIC speed/power with a reconfigurable region for protocol changes. Arista's 7130 and Exablaze/Cisco's Nexus 3550 (founded by Zomojo, an HFT firm) represent the FPGA-in-trading-hardware endpoint.

---

## 23. FIX Protocol Evolution: SBE and FAST

### 23.1 Why ASCII FIX is unusable at HFT speeds

Classic FIX (tag=value, e.g. `35=D|55=AAPL|...`, SOH-delimited) forces: (1) **variable-length fields** with delimiters requiring byte-by-byte scanning — no field-offset jumping; (2) **string-to-number parsing** for every numeric field (prices, quantities) — cycles + branches; (3) tag/value lookup typically needs a hash map per message; (4) no fixed layout → no zero-copy, no SIMD-friendly access. FIX collapses under millions of market-data updates per second.

### 23.2 SBE — Simple Binary Encoding

**Authors:** Martin Thompson & Todd Montgomery, Real Logic / Aeron. FIX Trading Community standard; reference implementation at `aeron-io/simple-binary-encoding`.

**Mechanism:**
- **Schema-driven codegen:** XML schema defines messages, fixed-length root fields, types, and repeating groups. Generator emits Java/C++/C#/Go/Rust codecs AOT — no reflection, no runtime schema lookup. Rust generator emits 100%-safe, zero-dependency crates.
- **Fixed offsets, C-struct layout:** root fields at compile-time-known byte offsets. Decoding a price = a single aligned load at a constant offset. No presence map, no per-field tags on the wire.
- **Little-endian, native byte order:** direct loads on x86, no byte-swapping.
- **Zero-copy / allocation-free:** in-memory layout equals wire layout. Codecs are "flyweights" that read/write directly over the network buffer — totally allocation-free in all reference languages.
- **Streaming, no backtracking:** fields accessed in order. Variable-length fields (strings/blobs) placed at the **end**, after fixed fields and repeating groups — keeps the fixed part a constant-offset struct. Prefetcher- and cache-friendly.
- **Message header:** `block length` (size of fixed root block, enables forward-compatible skipping), `template ID`, `schema ID`, `version` — the basis for schema evolution.

**Performance:** ~16–25× the throughput of Google Protocol Buffers; typical market-data messages **encode/decode in ~25 ns vs ~1000 ns for protobuf** — tens of nanoseconds, allocation-free, tight latency distribution.

### 23.3 CME MDP 3.0 — the canonical SBE deployment

CME's **Market Data Platform 3.0** launched **Dec 7, 2014**, the first major SBE feed; CME drove the SBE design within the FIX community. A UDP packet carries a **binary packet header** (sequence number + sending timestamp), then one or more SBE messages, each with an SBE message header (block length, template ID, schema ID, version) followed by the fixed root block and repeating groups. CME also uses SBE for **iLink 3** order entry. CME's own materials confirm MDP 3.0 is **lower latency and less CPU-intensive than the prior FIX/FAST feed**. Other SBE feeds: MEMX (MEMO), B3 (Binary UMDF), Euronext (Optiq OEG).

### 23.4 FAST — FIX Adapted for Streaming

FAST (~2005, FIX Protocol Ltd) optimizes **bandwidth**, not latency. **Mechanism:**
- **Template-based with field operators:** `constant` (value in template, never sent), `copy` (omit if unchanged), `default`, `delta` (send only difference), `increment` (auto-incrementing). Highly redundant feeds compress dramatically.
- **Presence map (PMAP):** leading bitmap flagging which optional/operated fields are present; absent fields reconstructed from operator + prior state.
- **Bit-packed integers:** stop-bit-encoded variable-length integers.

**Cost:** decoding is **stateful** (must track previous values per field) and branchier than SBE's constant-offset loads. FAST trades CPU and latency for wire size. Shines on **bandwidth-constrained, high-redundancy feeds** (options markets like OPRA with enormous symbol counts) and over WAN links. As exchanges moved to 10/40/100 GbE in colo, bandwidth stopped being the binding constraint — **CME and others migrated FAST → SBE**.

**Rule of thumb:** SBE when bandwidth is not the bottleneck and minimum latency is the goal (modern colocated feeds); FAST when bandwidth is the bottleneck and CPU/latency can be traded for compression (legacy / options / WAN).

### 23.5 Protocol stack choices

- **Order-entry session layer:** QuickFIX / QuickFIX-J / FIX8 / OnixS — latency-tolerant, not on the hot path
- **SBE codecs:** Real Logic `simple-binary-encoding` generator; OnixS and exchange-provided handlers for CME MDP
- **FAST decoders:** QuickFAST, mFAST (OCI), `fastlib` (Rust) — for legacy/options feeds
- **Aeron** (Thompson/Montgomery, Real Logic): the transport pairing with SBE — reliable UDP unicast/multicast + shared-memory IPC, lock-free. **~18 µs latency on physical hardware (<100 µs in cloud), >1M msg/sec at µs latency.** SBE-over-Aeron is the de-facto modern open-source low-latency messaging stack.

---

## 14c. Key References (§16–23 additions)

**Regulatory / pre-trade risk:**
- SEC Final Rule 34-63241 (2010); 17 CFR 240.15c3-5; SEC Division of Trading & Markets 15c3-5 FAQs.
- WilmerHale, "Knight Capital Settles Rule 15c3-5 Violations with SEC" (2013); SEC order 34-70694.
- FINRA Market Access Rule examination priority reports (2021–2026).
- ESMA RTS 25 (Commission Delegated Reg 2017/574); ESMA RTS 22/24 review final report (2025).
- SEC Rule 613; FINRA Regulatory Notices 14-47, 17-09, 20-31, 20-41; FINRA CAT oversight reports (2025/2026).
- Magmio, Algo-Logic, Enyx product pages; Exegy/AMD STAC-T0 13.9 ns benchmark (2024).

**Smart order routing / IEX:**
- SEC Reg NMS Rule 611; FINRA Rule 5310.
- SEC Order approving IEX D-Limit, 34-89686 (2020); Federal Register 2020-19204.
- RBC THOR patents: US 9,280,791; 10,896,466; 12,154,173.
- Hu, SEC (2018), "Evidence from IEX Becoming an Exchange."

**Transatlantic networks:**
- Submarine Networks / Business Wire, "Hibernia Express under 58.95 ms" (2015).
- McKay Brothers / PR Newswire, "Transatlantic Latency Slashed for Quincy Extreme Data" (2016).
- A-Team Insight, "Secret Transatlantic Radio Links Create Game-Changing Advantage for Traders."
- Fibre Systems / Electro Optics, "Free-space optics to speed stock exchange" (Anova Mahwah↔Carteret).
- Laser Focus World, "Hollow-core fiber gives high-frequency traders an edge."

**Adverse selection / toxicity:**
- Easley, López de Prado, O'Hara, "Flow Toxicity and Liquidity in a High-Frequency World," RFS 25(5) (2012).
- Easley, Kiefer, O'Hara, Paperman, "Liquidity, Information, and Infrequently Traded Stocks," J. Finance (1996) — original PIN.
- Andersen, Bondarenko, "VPIN and the Flash Crash" critique (2014).
- Exegy, "How to Stop Alpha Decay" (2024–25).

**Languages / runtimes:**
- Azul Systems, "LMAX Exchange: Getting 50% improvement in latency with Azul's Zing JVM."
- Jane Street, "Building a lower-latency GC" — blog.janestreet.com.
- Jane Street, "The Saga of Multicore OCaml" tech talk.
- Jane Street, "Oxidizing OCaml: Locality" — blog.janestreet.com/oxidizing-ocaml-locality.
- Tarides, "Introducing Jane Street's OxCaml branch" (2025).
- Databento, "Rust vs C++ for low-latency" — databento.com/blog/rust-vs-cpp.
- golang/go issue #38860 (goroutine scheduler timer latency).
- Tokio issue #2702 (26–29 ms tail latency under load).

**ASIC / hardware:**
- EE Times, "eFPGA: Hidden Engine of Tomorrow's HFT Systems."
- arXiv 2110.05335, "From FPGAs to Obfuscated eASICs: Design and Security Trade-offs."
- Leber, Geib, Litz, "High Frequency Trading Acceleration using FPGAs," FPL 2011.

**SBE / FAST / protocols:**
- Thompson, M., "Simple Binary Encoding," Mechanical Sympathy blog (2014).
- Real Logic, `aeron-io/simple-binary-encoding` (GitHub).
- CME Group, MDP 3.0 SBE documentation (Atlassian wiki).
- FIX Trading Community, SBE and FAST standards — fixtrading.org.
- Aeron transport — aeron.io; ~18 µs hardware latency.

---

*Cross-references: networking substrate in [Interconnects](@/notes/hardware/interconnects.md); microarchitecture in [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md); fences/atomics/RDTSC in [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md); timing counters in [Cycle Counters and Energy](@/notes/hardware/cycle_counters_and_energy.md); isolation/NUMA/hugepages/XDP in [Expert Linux Syscalls](@/notes/os/linux_expert_syscalls.md); lock-free queues in [Data Structures](@/notes/programming/data_structures.md); latency numbers in [DB Latency](../reference/db_latency.pdf) and [General Latency](../reference/general_latency.pdf).*
