+++
title = "Interconnects"
date = 2026-05-20
[taxonomies]
tags = ["interconnects", "pcie", "cxl", "nvlink", "ucie", "networking", "noc"]
+++


# Compute Interconnects: From On-Die to Datacenter-Scale

Master reference for the wires, fabrics, protocols, and software stacks that move data between transistors, chips, boards, racks, and datacenters. Covers the full electrical/optical/protocol stack from sub-nanosecond on-die NoCs to multi-millisecond WAN, with a focus on what matters for high-performance database, AI/ML, HPC, and storage systems in 2024-2026.

Existing related references:
- [PCIe Internals](@/notes/hardware/pcie_internals.md) — exhaustive PCIe coverage; this doc keeps PCIe to a single summary table and cross-links there.
- [Superscalar OoO CPU](@/notes/hardware/superscalar_ooo_cpu.md) §10 — on-die NoC and CXL high-level overview.
- [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) §10 — NVLink, UCIe, package interconnect basics.
- [Disaggregated Storage](@/notes/database/disaggregated_storage.md) — RDMA storage use cases.
- [VFIO Internals](@/notes/os/vfio_internals.md), [KVM Internals](@/notes/os/kvm_internals.md), [io_uring Internals](@/notes/os/io_uring_internals.md) — DMA, IOMMU, async I/O substrate.

---

## Table of Contents

1. [Mental Model and 7-Tier Latency Cheat Sheet](#1-mental-model-and-7-tier-latency-cheat-sheet)
2. [Tier 1 — On-Die / Chiplet](#2-tier-1-on-die-chiplet)
3. [Tier 2 — Board / Internal Server](#3-tier-2-board-internal-server)
4. [Tier 3 — Storage Fabrics](#4-tier-3-storage-fabrics)
5. [Tier 4 — Datacenter Network](#5-tier-4-datacenter-network)
6. [Tier 5 — HPC Fabrics](#6-tier-5-hpc-fabrics)
7. [Tier 6 — Software Stacks](#7-tier-6-software-stacks)
8. [Tier 7 — Optical / Future](#8-tier-7-optical-future)
9. [RDMA Semantics Deep Dive](#9-rdma-semantics-deep-dive)
10. [Lossless Fabric Tuning](#10-lossless-fabric-tuning)
11. [Tail Latency Pathology](#11-tail-latency-pathology)
12. [Topology-Aware Collective Scheduling](#12-topology-aware-collective-scheduling)
13. [Cache Coherence on Fabric](#13-cache-coherence-on-fabric)
14. [Bandwidth Math, Bisection BW, Oversubscription](#14-bandwidth-math-bisection-bw-oversubscription)
15. [Power and Cost at Scale](#15-power-and-cost-at-scale)
16. [Security](#16-security)
17. [Mental Models — Decision Framework](#17-mental-models-decision-framework)
18. [Practical Skills — Commands and Benchmarks](#18-practical-skills-commands-and-benchmarks)
19. [Further Reading](#19-further-reading)

---

## 1. Mental Model and 7-Tier Latency Cheat Sheet

The interconnect stack spans 9 orders of magnitude in latency and 4 orders of magnitude in bandwidth. Every order of magnitude up in latency forces a different protocol, encoding, error model, and software paradigm. Keep this picture in your head:

```
   ON-DIE        PACKAGE        BOARD          RACK          ROW       DC-WIDE       WAN
    ~1 ns        ~5-10 ns      ~50-200 ns    ~500ns-1µs   ~1-3 µs   ~5-20 µs     1-200 ms
   1 TB/s        2-4 TB/s      32-128 GB/s   25-400 GB/s  ...        ...         1-100 Gb/s
  cache-          coherent     coherent or   non-coherent  packet    packet      TCP/QUIC
  coherent       coherent      non-coherent  (RDMA verbs)  (RDMA/     (RDMA/
  (snoopy        (UCIe/IFOP/                              UDP/IB)    Ethernet)
   /MESI)        NVLink-C2C)
```

| Tier | Distance | Typical Latency | Typical BW (point-to-point) | Coherent? | Example tech | Software paradigm |
|---|---|---|---|---|---|---|
| 1. On-die | < 20 mm | 1-3 ns | 1-10 TB/s | yes | AXI/CHI, ring/mesh NoC | load/store |
| 1b. Chiplet (in-package) | 1-30 mm | 3-10 ns | 1-4 TB/s | yes (mostly) | UCIe, IFOP, NVLink-C2C, AIB, BoW | load/store, coherent DMA |
| 2. Board | 5-30 cm | 50-200 ns | 32-128 GB/s | yes (CXL) or no (PCIe) | PCIe gen5/6/7, CXL 2/3, NVLink, GMI3, xGMI | mmio, DMA, CXL.mem |
| 3. Rack (HPC interconnect, NVL72) | 1-3 m | 200 ns - 1 µs | 100-1800 GB/s | yes/no (NVLink Scale-Up coherent up to 72 GPUs) | NVLink+NVSwitch (NVL72), UALink, ICI, Slingshot | NCCL, MPI, verbs |
| 4. Row / ToR | 10 m | 1-3 µs | 25-400 Gb/s | no | InfiniBand HDR/NDR, RoCE v2, Slingshot 11 | verbs, libfabric, NCCL |
| 5. DC-wide | 100-500 m | 5-20 µs | 25-800 Gb/s | no | Ethernet 100/400/800G + dragonfly/Clos | gRPC, RDMA WRITE, NVMe-oF |
| 6. Cross-DC (metro) | 1-100 km | 0.1-2 ms | 10-1600 Gb/s | no | 400ZR, dark fiber, MACsec | async replication |
| 7. WAN | 100-15000 km | 5-200 ms | 1-100 Gb/s | no | submarine cables, QUIC/BBR | eventual consistency |

**Bandwidth scaling law (rough rule):** Per-pin SerDes signaling has roughly doubled every 3 years for two decades (NRZ 1 → 2 → 4 → 10 → 25 → 50 → 100 Gbaud PAM4 → 200 Gbaud PAM4). When per-pin SerDes hits practical limits (~224 Gbaud is the current frontier for electrical), the only way to scale bandwidth is more pins (UCIe Advanced has thousands of bumps per mm²) or optics (CPO).

**Latency floor:** Light in fiber travels ~5 ns/m (n ≈ 1.5). 100 m of fiber = 500 ns one-way, irreducible. 100 km of metro fiber = 0.5 ms one-way. Speed of light is the budget; software hops within a datacenter spend most of their time at NICs and switches, not on the wire.

**Coherent-vs-non-coherent boundary:** Cache coherence has historically lived inside the package (CPU socket, GPU complex). With CXL 2.0+ and NVLink generation 5, coherence now spans up to a tray (CXL.mem pooled across a 2-3 m chassis) and up to 72 GPUs (NVL72 NVLink fabric coherent loads/stores). Beyond that, the cost of snooping (or directory traffic) exceeds the value: latency, jitter, and tail propagation make it impractical. Software paradigm therefore shifts: snoopy/MESI for shared memory inside-the-rack; explicit RDMA verbs / message passing across the rack.

**Why interconnects matter for AI workloads:** A B200 GPU has ~10 TFLOPS FP64, ~2.25 PFLOPS FP8 dense (4.5 sparse), and 8 TB/s HBM3E. To keep it fed during LLM training (gradient AllReduce on tens of GB of optimizer state), the GPU must talk to its 71 NVL72 peers at 1.8 TB/s (NVLink5 bidir) and to remote racks at 400 Gb/s (ConnectX-7) or 800 Gb/s (ConnectX-8). The fabric, not the FLOPs, sets the ceiling for training throughput on models > 100B params.

---

## 2. Tier 1 — On-Die / Chiplet

### 2.1 AMBA Family (Arm)

The ARM AMBA family is the de-facto on-chip interconnect spec for ARM-based SoCs and is also widely licensed in non-ARM designs (FPGA fabrics, RISC-V SoCs, GPU chiplets). Six standards matter today, in increasing order of capability:

| Spec | Year | Use case | Coherence | Notes |
|---|---|---|---|---|
| **APB** | 1995 | Low-bandwidth peripherals (UART, GPIO) | no | Single 32-bit data, simple handshake |
| **AHB** | 1999 | Mid-range memory, ROM | no | Pipelined, multi-master, single-cycle |
| **AXI3** | 2003 | Mainstream high-bandwidth | no | Five independent channels |
| **AXI4** | 2010 | Mainstream | no | Up to 256 beat bursts, QoS signals |
| **AXI5** | 2017 | Mainstream + IO coherence | partial (ACE-Lite) | Atomic transactions, unique-ID interleave |
| **ACE / ACE-Lite** | 2011 | CPU cache coherence | yes (snoopy MOESI) | Adds Snoop channels AC/CR/CD |
| **CHI** | 2014 | Mesh/ring, server-class coherence | yes (directory or snoopy) | Packet-based, scales to hundreds of nodes |

**AXI4 channels (most-used variant on FPGA / DMA / accelerator):**

```
Master                                Slave
  │                                     │
  │ AW (write address)  ──────────────► │
  │ W  (write data, burst, last) ─────► │
  │ B  (write response, OKAY/SLVERR) ◄─ │
  │                                     │
  │ AR (read address) ────────────────► │
  │ R  (read data + last + RESP) ◄───── │
```

The five channels are independent (separate VALID/READY handshakes), allowing reads and writes to interleave at the bus master's whim. Out-of-order ID semantics let masters issue many in-flight transactions and match responses by AWID/RID/BID.

**CHI (Coherent Hub Interface):** CHI is what Arm-based server chips (Neoverse N1/N2/V1/V2/V3, Ampere Altra, AWS Graviton 3/4) use as the on-die fabric. Key differences from ACE:

- **Packet-based, not channel-based.** Requests and snoops fly as packets on a routed mesh, not on signal-channel-per-direction wires.
- **Three logical channels** (Request, Response, Snoop), each layered over a physical NoC (mesh, ring).
- **Directory-based or snoopy.** CHI-A is snoopy (broadcast); CHI-B/C/D/E add directory support, multi-chiplet fabrics, atomic transactions, persistent CMO (cache maintenance), trace, and Realm Management Extension (CCA).
- **Hierarchical coherent gateways.** CMN-700 (Neoverse mesh) scales to 256 cores per die and supports multi-die coherence via Coherent Mesh Gateways (CMG) and CCIX/CXL bridges.

**ACE-Lite:** A reduced form of ACE for non-cacheable masters (DMA engines, accelerators) that still need to participate in system-level coherence (snoop the CPU caches). Used heavily for GPU/NPU integration on mobile SoCs.

### 2.2 Intel UPI / Predecessors

**UPI (Ultra Path Interconnect)** is Intel's coherent inter-socket and inter-die interconnect, introduced with Skylake-SP (Xeon Scalable v1, 2017) and evolved through Sapphire Rapids, Emerald Rapids, Granite Rapids. Predecessor: QuickPath Interconnect (QPI, Nehalem 2008 through Broadwell).

| Generation | Released | Speed (GT/s) | Per-link BW (one direction) | Used in |
|---|---|---|---|---|
| QPI 1.0 | 2008 (Nehalem) | 6.4 | 12.8 GB/s | Xeon 5500-5600 |
| QPI 1.1 | 2011 (Sandy Bridge-EP) | 8.0 | 16 GB/s | Xeon E5 |
| QPI 1.2 | 2014 (Haswell-EP) | 9.6 | 19.2 GB/s | Xeon E5/E7 v3-v4 |
| **UPI 1.0** | 2017 (Skylake-SP) | 10.4 | 20.8 GB/s | Xeon SP gen1/2/3 |
| **UPI 2.0** | 2023 (Sapphire Rapids) | 16.0 | 32 GB/s | Xeon SP gen4/5 |
| **UPI 2.0 (GNR)** | 2024 (Granite Rapids) | 24.0 | 48 GB/s | Xeon 6 |

**MESIF protocol:** Intel's MESI extension adds a **Forward (F)** state. Exactly one cached copy of a shared line holds F state; that cache is responsible for responding to read requests, eliminating wasteful "all sockets respond simultaneously" traffic. The home agent maintains a directory; the F-state holder is the designated forwarder. Compare with AMD MOESI which uses an Owned (O) state to allow shared-dirty caching.

**HitMe cache:** UPI's directory-based coherence is augmented by a "HitMe" cache at the home agent — a small (~1 MB per channel) directory cache holding recently-snooped line metadata to skip the (slow) DDR directory bit lookup. Hit on HitMe = snoop only the relevant agent; miss = broadcast snoop (with directory consult to filter).

**Flit layer:** UPI uses 192-bit (24 byte) flits with 8 bytes of header/CRC and 16 bytes of payload. Three message classes (Request, Snoop, Response) share the link with credit-based flow control.

### 2.3 AMD Infinity Fabric

Infinity Fabric (IF) is AMD's umbrella term for its coherent on-package and inter-socket interconnect, spanning from Zen 1 (2017) through Zen 5 (2024) and the MI300 series. It is built on the **HyperTransport 3.x** electrical layer with a custom AMD-defined protocol layer.

| Variant | Scope | Generation | Lanes / link | Per-link BW | Notes |
|---|---|---|---|---|---|
| **IFOP** (On-Package) | CCD ↔ IOD | Zen 2/3: 16 GT/s, Zen 4: 32 GT/s, Zen 5: 36 GT/s | 32 lanes | 64 GB/s (Zen 4) | One read + one write link per CCD; IOD is the central crossbar |
| **IFIS** (Inter-Socket) | Socket ↔ Socket | xGMI gen3 (Zen 3): 18 GT/s, gen4 (Zen 4): 32 GT/s, gen5 (Zen 5): 32 GT/s | 16 lanes per link, 3-4 links per socket | 64 GB/s/link | Used in 2P EPYC; can re-purpose as PCIe lanes |
| **GMI / GMI3** (Global Memory Interconnect) | CCD ↔ IOD on EPYC | GMI3: 36 GT/s, narrow (Zen 4); GMI3-Wide (Zen 4 SP5 64-core+): 32 lanes 36 GT/s | up to 32 lanes | ~36-72 GB/s | Replaces IFOP in datacenter EPYC; coherent |
| **xGMI** | EPYC ↔ EPYC (2P), MI300 ↔ MI300 | Up to 32 GT/s (gen4), 36 GT/s (gen5) | 16-32 lanes | up to ~144 GB/s per pair | MI300X uses 7 xGMI links = ~896 GB/s per GPU |
| **NVLink-style on MI300** | MI300 cluster (8 GPUs) | xGMI | 7 links × 16 lanes | 896 GB/s aggregate per GPU | All-to-all in 8-GPU cube; basis of MI300X reference platform |

**Coherence:** MOESI base protocol. Each CCD has a private L3 (Zen 2/3: 16 MB; Zen 4: 32 MB; Zen 5: 32 MB; X3D: +64 MB). Cross-CCD lines are snooped through the IOD. The IOD also houses the memory controllers, PCIe root complex, and Infinity Fabric Switch.

**SCF (System Coherent Fabric):** The data fabric inside the IOD that routes between CCDs, memory controllers, PCIe, and IO. SCF clock (FCLK) is independent of memory clock (MCLK); UCLK = unified memory controller clock. Crossing FCLK/MCLK domains incurs a ~10 ns penalty per crossing. For best memory latency, `FCLK = MCLK = UCLK` (1:1:1 ratio); for high memory speed past 6000 MT/s on Zen 4, expect 2:1 desync.

### 2.4 NVLink-C2C

**NVLink-C2C** is NVIDIA's chip-to-chip variant of NVLink, used in:
- **Grace-Hopper** (Grace CPU ↔ H100/H200 GPU): 900 GB/s bidirectional, coherent
- **Grace-Blackwell GB200** (Grace CPU ↔ 2× B200 GPUs): 900 GB/s per CPU-GPU link
- Custom partner chips via NVIDIA's NVLink-C2C IP

It uses the same 100G/lane PAM4 electrical signaling as NVLink-5 but is optimized for short on-board / on-substrate distances. Critically: it is **fully cache-coherent**, which means GPU code can issue `LD/ST` against host LPDDR5X memory directly — no explicit cudaMemcpy needed. The CPU and GPU see a single 624 GB unified address space (Grace: 480 GB LPDDR5X + Hopper: 144 GB HBM3e on H200).

The trade-off: coherence at this bandwidth requires aggressive directory traffic; latency is ~250 ns CPU→GPU, vs ~80-100 ns same-socket DRAM. Use NVLink-C2C for working sets too large to fit in HBM (KV cache spillover, parameter offload), not for inner-loop bandwidth-bound kernels.

### 2.5 UCIe (Universal Chiplet Interconnect Express)

UCIe is the open standard for **die-to-die (D2D)** chiplet interconnect, ratified by an industry consortium (Intel, AMD, Arm, Google, Microsoft, Qualcomm, Samsung, TSMC, Meta, NVIDIA later) in March 2022. Goal: a PCIe-like ecosystem of interoperable chiplets, where any chiplet supplier can mix-and-match dies from any vendor.

| Spec | Released | PHY data rate | Bump pitch | Reach | Per-mm shore BW (Advanced) | Notes |
|---|---|---|---|---|---|---|
| **UCIe 1.0** | Mar 2022 | 4-32 GT/s | Std 110-100 µm, Adv 45 µm (later 25 µm) | <2 mm Adv, <25 mm Std | ~10-32 GB/s/mm Std, up to ~165 GB/s/mm Adv | PCIe + CXL protocol; retimer support |
| **UCIe 1.1** | Aug 2023 | 4-32 GT/s | Adv 45/25/36/55 µm | same | same | Automotive + manageability + raw streaming |
| **UCIe 2.0** | Aug 2024 | 4-32 GT/s | Adv adds 25/55 µm | same | same | 3D stacking, system architecture (D2D Manageability Architecture), Memory + Raw streaming for HBM-on-die |
| **UCIe 2.1** | Aug 2025 | up to **64 GT/s** | Adv | same | up to ~331 GB/s/mm | Doubles base data rate; refined for AI clusters |

**Layered architecture:**

```
+---------------------------------------------+
|  Protocol Layer (PCIe, CXL, Streaming Raw)  |
+---------------------------------------------+
|  D2D Adapter (CRC, retry, link state mgmt)  |
+---------------------------------------------+
|  PHY (sideband + main band, lane training)  |
+---------------------------------------------+
|  Bumps / Interposer                          |
+---------------------------------------------+
```

**Standard vs Advanced packaging:**
- **Standard Package:** Organic substrate (PCB-style routing, 110-100 µm bump pitch), reach up to 25 mm, requires retimers for longer distances. Cheaper, looser tolerances.
- **Advanced Package:** Silicon interposer or embedded bridge (CoWoS, EMIB, InFO), 45 µm pitch (UCIe 1.0), tightening to 25 µm (UCIe 2.0+), reach < 2 mm. Multi-thousand-bump shoreline, sub-pJ/bit energy.

**Protocol mapping:** UCIe transports PCIe and CXL natively (so a chiplet that speaks PCIe can plug into a UCIe link transparently), plus a "streaming raw" mode for custom protocols. CXL.io, .cache, and .mem all map cleanly.

**KP4 BoB:** The KP4 Bunch-of-Bumps test vehicle defined by Ayar Labs/Eliyan/others is a common reference physical layout used in early UCIe interop demos (2023-2024). It standardizes ~512-1024 bumps in a regular grid for D2D testing.

### 2.6 TileLink (RISC-V / SiFive)

TileLink is an **open chip-coherence protocol** developed at UC Berkeley (Asanović et al., 2014+) and the standard fabric on SiFive/Chipyard RISC-V cores (Rocket, BOOM, NaxRiscv). Three conformance tiers:

| Tier | Capability | Use case |
|---|---|---|
| **TL-UL** (UncachedLight) | Single in-flight transaction per channel, simple R/W | Low-bandwidth peripherals (UART, SPI) |
| **TL-UH** (UncachedHeavy) | Multiple outstanding, burst, atomics, hints | Memory controllers, DMA |
| **TL-C** (Coherent) | Full MOESI-like with probe/grant channels | L1↔L2↔LLC, multi-core |

TileLink has 5 channels (A, B, C, D, E) carrying Acquire/Probe/Release/Grant messages — analogous to AMBA CHI's REQ/SNP/RSP but with explicit probe channels for snooping. Open spec, used in OpenTitan, BOOM, and many academic chips.

### 2.7 BoW, AIB, OpenHBI (Pre-UCIe Chiplet Standards)

Before UCIe consolidated the chiplet market, several alternatives competed:

| Standard | Backer | Status | Notes |
|---|---|---|---|
| **BoW** (Bunch of Wires) | OCP / OIF / Marvell | Largely subsumed by UCIe but still used in some custom designs | Targeted simple parallel wires across 2 mm; data rates up to 16 GT/s |
| **AIB** (Advanced Interface Bus) | Intel | Open-sourced 2020, used in EMIB-based Intel chiplets (Sapphire Rapids HBM-tile interconnect, Ponte Vecchio); influenced UCIe Advanced | First widely-deployed advanced-pkg D2D; 1024 wires per "channel" |
| **OpenHBI** | OCP HBI workgroup | Largely overlapping with HBM PHY; targeted memory-class D2D | Defines link to HBM-like memory dies |
| **XSR** (Extra Short Reach) | OIF | Survives for high-end CPO/optical engine interfaces | 56-112 GT/s SerDes for <2 cm reach |

UCIe 1.0+ has consolidated the mainstream chiplet ecosystem; AIB, BoW, OpenHBI remain in legacy/specialized designs.

### 2.8 HBM3, HBM3E, HBM4 as On-Package Interconnect

HBM (High-Bandwidth Memory) is technically DRAM, but the JEDEC-defined HBM PHY also acts as an interconnect to the host die (CPU/GPU/accelerator). Pin count and per-pin rate matter as much as bandwidth.

| Standard | Released | Stacks (typical) | Bus width per stack | Per-pin rate | Per-stack BW | Notes |
|---|---|---|---|---|---|---|
| **HBM2** | 2016 | 4-8 high | 1024 bits | 2 Gbps | 256 GB/s | Used in V100, Vega 20 |
| **HBM2E** | 2020 | 8 high | 1024 bits | 3.6 Gbps | 460 GB/s | A100 (5 stacks = 2 TB/s) |
| **HBM3** | 2022 | 8-12 high | 1024 bits | 6.4 Gbps | **819 GB/s** | H100 (5 stacks active = 3.35 TB/s) |
| **HBM3E** | 2024 | 12 high | 1024 bits | 9.2 Gbps | **1.18 TB/s** | H200 (6 stacks = 4.8 TB/s), B200 (8 stacks = 8 TB/s) |
| **HBM4** | 2026 (sampling) | 12-16 high | **2048 bits** (doubled!) | 8 Gbps | **~2.0 TB/s** | MI400, Rubin generation; doubling bus width is the big change |
| **HBM4E** | ~2028 | 16 high | 2048 bits | 12 Gbps | ~3.0 TB/s | Proposed |

**The HBM4 bus-width doubling matters:** Per-pin signaling has hit thermal/electrical walls (the HBM3E 9.2 Gbps is already aggressive). HBM4 doubles the parallel bus width from 1024 to 2048 bits while keeping per-pin rate moderate (8 Gbps). This forces ~2× the bumps between memory stack and host die — driving demand for advanced packaging (CoWoS-S/L, Intel Foveros, Samsung X-Cube).

**Implication for accelerator design:** A B200 with 8 HBM3E stacks consumes ~8 × 1024 = 8192 wires just for the HBM interface (plus command/address). With HBM4 at 2× width, this becomes ~16384 wires. Combined with NVLink (18 links × ~100 wires) and UCIe to neighbor chiplets, shoreline (bumps per mm of die edge) becomes the critical scaling constraint, not transistor density. This drives the move to 3D stacking (logic die underneath HBM stack) where the interconnect goes vertical rather than horizontal.

---

## 3. Tier 2 — Board / Internal Server

### 3.1 PCI Express Summary

(For exhaustive PCIe coverage, see [pcie_internals.md](@/notes/hardware/pcie_internals.md).) Single-table reference:

| Gen | Year ratified | Encoding | Raw GT/s/lane | Useful GB/s/lane | x4 BW | x16 BW (one direction) | x16 bidir | Status |
|---|---|---|---|---|---|---|---|---|
| 1.0 | 2003 | 8b/10b | 2.5 | 0.250 | 1 GB/s | 4 GB/s | 8 GB/s | Legacy |
| 2.0 | 2007 | 8b/10b | 5.0 | 0.500 | 2 GB/s | 8 GB/s | 16 GB/s | Legacy |
| 3.0 | 2010 | 128b/130b | 8.0 | 0.985 | 3.94 GB/s | 15.75 GB/s | 31.5 GB/s | Mainstream LegacyServer |
| 4.0 | 2017 | 128b/130b | 16 | 1.97 | 7.88 GB/s | 31.5 GB/s | 63 GB/s | Mainstream |
| 5.0 | 2019 | 128b/130b | 32 | 3.94 | 15.75 GB/s | 63 GB/s | 126 GB/s | Datacenter standard 2024 |
| 6.0 | 2022 | PAM4 + FLIT 256B + FEC | 64 | 7.56 | 30.25 GB/s | 121 GB/s | 242 GB/s | Shipping 2025-2026 (Granite Rapids, Turin) |
| 7.0 | 2025 (released 2025) | PAM4 + FLIT 256B + FEC | 128 | 15.13 | 60.5 GB/s | 242 GB/s | 484 GB/s | Spec released; first silicon 2026-2027 |

**Key shift at PCIe 6.0:** PAM4 signaling (4 levels = 2 bits/symbol) replaces NRZ, and 64b/66b encoding is replaced by 256-byte FLIT mode with forward error correction (FEC). FEC adds ~2 ns of latency for the FLIT roundtrip but is essential at PAM4 SNR margins. CXL 3.0+ uses the same FLIT mode.

### 3.2 CXL — Compute Express Link

CXL is the cache-coherent, low-latency interconnect built on PCIe physical and link layers. The protocol layer multiplexes three sub-protocols:

| Sub-protocol | What it transports | Used by |
|---|---|---|
| **CXL.io** | PCIe TLPs (discovery, configuration, BAR, MMIO) | All CXL devices |
| **CXL.cache** | Coherent caching requests from device to host caches | Type 1, Type 2 |
| **CXL.mem** | Host-issued loads/stores to device memory | Type 2, Type 3 |

**Device types:**

| Type | Description | Protocols | Example |
|---|---|---|---|
| Type 1 | Accelerator with its own caches, no device-side memory | .io + .cache | SmartNICs, FPGA caches |
| Type 2 | Accelerator with caches AND attached memory; both protocols | .io + .cache + .mem | GPUs (future), AI inference accelerators |
| Type 3 | Memory expander (no caches, just bulk memory exposed via .mem) | .io + .mem | Samsung CMM, SK Hynix CMM, Micron CZ120, Astera Leo |

**Version timeline:**

| Version | Year | Topology | Pool | Switching | Notable additions |
|---|---|---|---|---|---|
| **CXL 1.0** | Mar 2019 | Direct attach 1 host - 1 device | no | no | First public release; runs on PCIe 5 PHY |
| **CXL 1.1** | Jun 2019 | Same | no | no | Compliance + small fixes; the first widely-implemented spec |
| **CXL 2.0** | Nov 2020 | Switched | yes (multi-LD) | single-level switch | Memory pooling across up to 16 hosts; CXL switching; persistence flush; IDE encryption |
| **CXL 3.0** | Aug 2022 | Fabric (multi-host, multi-switch) | yes | Multi-level switching | Doubles to 64 GT/s (PCIe 6 PHY, PAM4); 256B FLIT; peer-to-peer (P2P) device-to-device; GFAM (Global Fabric Attached Memory); HDM-DB (Device-managed back-invalidation); coherence over fabric |
| **CXL 3.1** | Nov 2023 | Same fabric | yes | + scale-out via PBR | Trusted Security Protocol (TSP) on top of TDISP; **Port-Based Routing (PBR)** for large fabrics; GFAM enhancements; Global Integrated Memory (GIM) attachment |
| **CXL 3.2** | Dec 2024 | Same | yes | optimized PBR | Optimized fabric management; CCI (CXL Compliance Inspector); post-quantum considerations in IDE; sysfsmanagement attestation enhancements |

**HDM-DB (Host-managed Device Memory — Device-managed coherence):** Critical concept in CXL 3.0+. In HDM-H (Host-managed coherence, CXL 2.0 default), the host CPU owns the coherence directory; every cache line in CXL.mem space is tracked by the host. This scales poorly past ~256 GB of pooled memory.

HDM-DB lets the **device** manage coherence — device caches a line, device tracks which hosts cached it, device issues **back-invalidations** (BI) to evict from host caches when needed. The host's only coherence obligation is to respond to BI messages. This decouples coherence directory size from host LLC size and is essential for fabric-attached memory >1 TB.

**Fabric mode (CXL 3.0+):** The biggest architectural shift. Up to 4096 nodes (hosts + devices) in a single coherent fabric. Multiple switching layers, port-based routing (so routes don't need full Tree-based hierarchical IDs), peer-to-peer DMA between devices through the fabric, and Global Fabric Attached Memory (GFAM) — pooled memory accessible from any host with sub-microsecond latency.

**GFAM:** Memory devices that sit in the fabric and serve any host as a shared memory pool. Imagine 8 TB of pooled DRAM accessible from 32 hosts; each host sees it as a transparent memory region. Use cases: large database buffer pools (shared across nodes), in-memory caches (Redis-like), AI checkpoint storage. Reference designs: Samsung Memory Expander Modules, Astera Leo Gen 2.

**IDE (Integrity and Data Encryption):** CXL line-level encryption (AES-GCM) on every flit. Configurable per virtual channel. Adds <3 ns latency in modern controllers.

**TDISP (TEE Device Interface Security Protocol):** PCIe spec adopted by CXL for attesting confidential devices. Lets a TEE (Intel TDX, AMD SEV-SNP) verify that a CXL device is genuine and operating in a trusted mode before mapping its memory. Required for confidential AI workloads on cloud.

**Vendors and parts:**

| Vendor | Product | Type | Capacity | Notes |
|---|---|---|---|---|
| Samsung | CMM-D (CXL Memory Module DRAM) | Type 3 | 128/256 GB | First mass-market CXL 2.0 module |
| SK Hynix | CMM-DDR5 / CMM-2LM | Type 3 | 96/256 GB | Used in Tier-2 hot data offload |
| Micron | CZ120 | Type 3 | 128 GB | E3.S form factor, PCIe 5 |
| Astera Labs | Leo | Type 3 + retimers | up to 2 TB per module | Leading independent CXL memory IC supplier (Aries retimers, Leo controllers, Scorpio fabric switches) |
| Marvell | Structera CXL-X | Type 3 | up to 240 GB+ | Disaggregated memory + cache acceleration |
| Microchip | SMC 2000 | Type 3 | 128/256/512 GB | High-end DDR5 controller |
| Panmnesia | CXL 3.1 switch | switch | 64 lanes | First CXL 3.1 switch demoed late 2024 |

### 3.3 NVLink — Generations and NVSwitch

NVLink is NVIDIA's proprietary high-bandwidth GPU-to-GPU (and now GPU-to-CPU via NVLink-C2C, plus GPU-to-NVSwitch) interconnect.

| Gen | GPU debut | Year | Per-link bidir BW | Links per GPU | Total per GPU (bidir) | Notes |
|---|---|---|---|---|---|---|
| 1 | P100 | 2016 | 40 GB/s | 4 | 160 GB/s | First NVLink |
| 2 | V100 | 2017 | 50 GB/s | 6 | 300 GB/s | NVSwitch 1.0 introduced in DGX-2 (16-GPU all-to-all) |
| 3 | A100 | 2020 | 50 GB/s | 12 | 600 GB/s | NVSwitch 2.0 |
| 4 | H100 | 2022 | 50 GB/s | **18** | **900 GB/s** | NVSwitch 3.0 with NVLink Sharp (NVLS); 50G PAM4 per lane |
| 5 | B200 / GB200 | 2024 | 100 GB/s | 18 | **1.8 TB/s** | 100G PAM4 per lane; NVLink Switch tray; NVL72 enables 72-GPU coherent domain |
| 6 (Rubin) | R100 (expected) | 2026-2027 | 200 GB/s | 18+ | ~3.6 TB/s | 200G per lane |

**NVSwitch generations:**

| Switch gen | GPU gen | Per-switch BW | Total switches per node/rack | Used in |
|---|---|---|---|---|
| NVSwitch 1.0 | V100 | 50 GB/s × 18 ports = 900 GB/s | 6 switches per HGX-2 | DGX-2 (16 GPUs all-to-all) |
| NVSwitch 2.0 | A100 | 1.6 TB/s aggregate | 6 per HGX-A100 | DGX A100 (8 GPUs) |
| NVSwitch 3.0 | H100 | 3.2 TB/s aggregate, with NVLS in-switch reduction | 4 per HGX-H100 (8 GPU); 9 trays for NVL72 | DGX H100, HGX H100/H200, NVL72 |
| NVSwitch 4.0 | B200/B300 | ~7.2 TB/s aggregate, supports 72-GPU fabric | 9 NVSwitch trays per NVL72 | GB200 NVL72, GB300 NVL72 |

**NVLink Sharp (NVLS):** In-switch reduction. Instead of every GPU sending data to a root and reducing serially, NVSwitch 3.0+ has dedicated reduction ALUs inside the switch silicon. AllReduce moves from O(N) message exchanges per GPU to O(log N) with the switch doing the math. For an N-GPU ring AllReduce on M bytes, the time model goes from `2(N-1)/N × M/B` (ring) to `M/B + α log N` (NVLS / tree). On 72-GPU NVL72 doing FP8 AllReduce on 64 GB of tensors, NVLS halves AllReduce time (and frees compute streams from waiting).

**NVL72 — the 72-GPU rack-scale architecture:**

```
NVL72 Rack (single coherent NVLink domain — 72 B200 GPUs, 130 TB/s aggregate):
+============================================================================+
| Spine (NVLink interconnect — copper backplane, ~5000 cables, water cooled) |
+============================================================================+
| NVSwitch tray 9                                                              |
| NVSwitch tray 8                                                              |
| NVSwitch tray 7                                                              |
| NVSwitch tray 6                                                              |
| NVSwitch tray 5                                                              |
| NVSwitch tray 4   ← 9 NVSwitch trays in middle of rack                       |
| NVSwitch tray 3                                                              |
| NVSwitch tray 2                                                              |
| NVSwitch tray 1                                                              |
+============================================================================+
| Compute tray 18  (4 B200 GPUs + 2 Grace CPUs)                                |
| Compute tray 17                                                              |
| Compute tray 16                                                              |
| Compute tray 15                                                              |
| Compute tray 14                                                              |
| ...                                                                          |
| Compute tray 1  (4 B200 + 2 Grace via NVLink-C2C; 18 trays × 4 GPU = 72 GPU) |
+============================================================================+
| Power shelf + management                                                    |
+============================================================================+
```

- **18 compute trays**, each with 2 Grace CPUs + 4 B200 GPUs (or 2 GB200 Superchips = 2 Grace + 4 B200)
- **9 NVSwitch trays** at the middle of the rack, providing 130 TB/s aggregate bisection
- All 72 GPUs in a single NVLink fabric: each GPU has 1.8 TB/s to every peer (1-hop)
- Compute-to-switch: copper backplane (called the "NVLink spine") with ~5000 individual NVLink cables totaling >2 miles
- Power: 120 kW peak per rack; liquid-cooled (cold plates on every GPU and CPU)

For inference (large-context LLM serving on 1T+ params), NVL72 enables tensor-parallel + pipeline-parallel mapping with NVLink-only communication — no IB/Ethernet step needed for in-rack tokens. For training, NVL72 acts as a fast "scale-up" domain; multiple NVL72 racks connect via 800G InfiniBand (NDR/XDR) into the "scale-out" cluster.

### 3.4 AMD UALink and MI300 xGMI Topology

**MI300X 8-GPU node**: Each MI300X has 7 xGMI links of 64 GB/s each, organized as a fully-connected 8-GPU graph (each pair has 1 direct xGMI). Reference HGX-MI300X compute board mirrors NVIDIA HGX. Per-GPU peer BW: 7 × 128 GB/s = **896 GB/s** (each link bidir). No external switch tier yet — limit is 8 GPUs in one domain.

**UALink (Ultra Accelerator Link):** Consortium launched 2024 by AMD, Broadcom, Cisco, Google, Intel, Meta, Microsoft, Hewlett Packard Enterprise — explicitly to create an open NVLink alternative. Spec 1.0 released Apr 2025. Targets:
- Scale to 1024 GPUs in one coherent fabric (vs 72 for NVL72)
- 200 Gb/s per lane
- Memory semantics (load/store) — not just message passing
- UALink switches will be ASICs from Broadcom, Astera, Cornelis, etc.

UALink uses Ethernet PHY (so re-use 200 Gbps SerDes IP), but the protocol layer is a custom coherent protocol (not Ethernet, not CXL, not NVLink). The first UALink chips are expected late 2026.

### 3.5 Google ICI — TPU Pod Interconnect

Google's TPUs use a custom **ICI (Inter-Chip Interconnect)** in a 3D torus topology, with optical reconfiguration in v4+.

| Gen | Topology | BW per chip | Total chips per pod | Notes |
|---|---|---|---|---|
| TPU v2 | 2D torus | ~600 GB/s aggregate | 256 | First public ICI |
| TPU v3 | 2D torus | ~900 GB/s | 1024 | Liquid-cooled |
| **TPU v4** | **3D torus, OCS-reconfigurable** | ~1200 GB/s | 4096 chips per pod | Optical Circuit Switch (Palomar/Apollo) enables runtime topology reshape per job; ISCA 2023 paper Jouppi et al. |
| TPU v5e | 2D torus | ~1200 GB/s | 256 (single pod) | Cost-optimized |
| TPU v5p | 3D torus + OCS | ~3600 GB/s aggregate | 8960 chips | Larger pod, similar topology to v4 |
| TPU v6 (Trillium) | 3D torus + OCS | ~1800 GB/s per chip | 256 per "cube" | Energy-optimized; matches H100 perf at 1/3 the power |
| TPU v6e / v6p (Ironwood, 2025) | Same | ~3600 GB/s? | 9216 (Ironwood pod) | Targets inference scaleout; FP8 + integer formats |

**3D torus topology** is preferred at TPU scale because it has constant per-chip wire count (6 neighbors) regardless of pod size, vs Clos which scales links per chip with the fanout. Bisection bandwidth scales as N^(2/3) (the cross-section of a 3D torus), but for the dominant collective patterns (AllReduce, AllGather on tensor-parallel groups) torus is a natural fit. **OCS reconfiguration**: An optical circuit switch lets the cube be reshaped per job — a 3D torus can be split into multiple 2D tori, or reorganized as 2×4×8 vs 4×4×4. This is critical when TPU pod must run many parallel jobs with different shapes.

### 3.6 AWS NeuronLink (Trainium)

AWS Trainium2 (2024) and Trainium3 (2025) use **NeuronLink-v3**, a proprietary interconnect for scaling 16-64 Trainium2 chips in a single "UltraServer". Per-chip aggregate NeuronLink BW: ~12 Tbps (1.5 TB/s). Topology: hypercube or modified Clos depending on UltraServer size. Designed to match Nvidia NVL72 economics for inference at scale, used in Project Rainier (Anthropic's training cluster) for Claude-family training.

---

## 4. Tier 3 — Storage Fabrics

Storage networking has converged on **NVMe-oF** (NVMe over Fabrics) as the modern standard, replacing earlier protocols.

### 4.1 NVMe-oF — NVMe over Fabrics

NVMe-oF lets a host issue NVMe commands over a network fabric instead of PCIe. The same NVMe submission/completion queue semantics, with fabric-specific transport.

| Transport | Wire | Latency overhead vs PCIe NVMe | Notes |
|---|---|---|---|
| **NVMe/RDMA (RoCE v2)** | UDP/IP over Ethernet w/ RDMA | +5-10 µs | Most common; uses verbs; needs lossless network (PFC) |
| **NVMe/RDMA (InfiniBand)** | IB transport | +3-5 µs | Lower latency than RoCE; IB clusters |
| **NVMe/FC** (FC-NVMe) | Fibre Channel | +20-50 µs | Drop-in replacement for SCSI/FC in enterprise SANs |
| **NVMe/TCP** | TCP/IP | +30-80 µs (CPU-bound) | Most portable, runs over any IP network; CPU-heavy without offload |

**Capsule semantics:** Every NVMe-oF command is wrapped in a "capsule" containing the NVMe command opcode/parameters plus inline or referenced data. For small commands and writes, the data is **inlined** with the capsule (single round-trip). For larger I/O, the data is fetched via RDMA READ from the host's buffer (for writes) or pushed via RDMA WRITE (for reads). The capsule contains:
- 64-byte NVMe Submission Queue Entry (SQE)
- Optional payload (inline data)
- For reads: SGL/PRP pointer to host buffer (RDMA registered MR)

**Queue mapping:** NVMe has admin + I/O queues. Over fabrics, each I/O queue is mapped to a single **QP (Queue Pair)** in RDMA or a single **TCP connection** in NVMe/TCP. Multi-queue NVMe-oF therefore uses many QPs; CPU pinning of queues to cores matters significantly for performance.

**NVMe/TCP optimizations 2024-2026:**
- **TCP-DDP/zero-copy:** Linux kernel 6.x supports TCP zero-copy receive (MSG_ZEROCOPY) for NVMe/TCP, eliminating the buffer copy from sk_buff to user space.
- **kTLS offload:** Encryption offloaded to NIC for secure NVMe/TCP.
- **iouring submission:** Hybrid NVMe/TCP via io_uring is the modern path — competitive with NVMe/RDMA on light loads.

### 4.2 Fibre Channel

Despite predictions of its demise, FC remains entrenched in enterprise SANs. Speeds: 8/16/32/64/128 GFC (Gigabit Fibre Channel). 64GFC = 64 Gbps per port (~6.4 GB/s after encoding).

**FCP** (Fibre Channel Protocol): The SCSI-over-FC mapping. Largely replaced by NVMe-oF/FC for new deployments.

**NPIV** (N_Port ID Virtualization): Lets multiple "virtual" FC ports share one physical HBA — essential for VM passthrough on FC SANs.

**Zoning:**
- **Soft zoning:** Name-server enforced; the FC switch's name service hides devices in other zones. Bypassable if attacker has hard-coded WWPNs.
- **Hard zoning:** ASIC-enforced; switch hardware drops frames that violate zone rules.

**FCoE** (Fibre Channel over Ethernet): Was supposed to be the unified-fabric answer (FC over lossless Ethernet, with DCB extensions). Largely deprecated in favor of iSCSI and NVMe-oF; few new FCoE deployments since ~2018.

### 4.3 iSCSI

iSCSI (SCSI over TCP) is the long-time low-end alternative to FC. Still in widespread use for general-purpose SANs on commodity Ethernet. Latency: ~100-500 µs (TCP + SCSI translation). Increasingly displaced by NVMe/TCP, which has lower latency and the same simplicity.

### 4.4 SAS / SATA (Drive-Local Fabrics)

These are direct-attach drive interfaces, not network fabrics, but worth a note:

| Standard | Generation | Per-drive BW | Latency | Use case |
|---|---|---|---|---|
| **SATA III** | 6 Gbps | 600 MB/s | µs | Boot SSDs, legacy HDDs |
| **SAS-4** | 22.5 Gbps | 2.25 GB/s | 10s of µs | Enterprise SAS HDDs |
| **NVMe (U.2/U.3/E1.S/E3.S)** | PCIe 4/5/6 | up to 16 GB/s (PCIe 5 x4) | <10 µs | All modern NVMe SSDs |

SAS/SATA traffic to a JBOD enclosure flows over SAS expanders (12 Gbps SAS-3 or 22.5 Gbps SAS-4). NVMe-oF + E1.S/E3.S enclosures (EDSFF form factors) are replacing SAS JBODs for high-density flash deployments.

---

## 5. Tier 4 — Datacenter Network

### 5.1 Ethernet Evolution

Ethernet has scaled from 10 Mbps in 1983 to 1.6 Tbps in 2025 — 5 orders of magnitude in 42 years. The driver since ~2010 has been SerDes per lane × lane count.

| Speed | First standard (IEEE) | Year | Lanes × per-lane | Modulation | FEC | Reach | Status 2026 |
|---|---|---|---|---|---|---|---|
| 1 GbE | 802.3z | 1998 | 1 × 1 Gbps | NRZ | none | SR/LR/CX | Legacy |
| 10 GbE | 802.3ae | 2002 | 1 × 10 Gbps | NRZ | none | SR/LR/ER | Legacy |
| 25 GbE | 802.3by | 2016 | 1 × 25 Gbps | NRZ | RS(528,514) opt. | SR/LR | Mainstream edge |
| 40 GbE | 802.3ba | 2010 | 4 × 10 Gbps | NRZ | none | SR4/LR4 | Largely deprecated |
| 50 GbE | 802.3cd | 2018 | 1 × 50 Gbps | PAM4 | RS(544,514) | SR/FR/LR | Server NIC |
| 100 GbE | 802.3bj/bm | 2014 | 4 × 25 Gbps (NRZ); later 2 × 50 Gbps (PAM4) | NRZ → PAM4 | KR4 / RS(544,514) | SR4/LR4/CR4 | Mainstream server NIC |
| 200 GbE | 802.3bs | 2017 | 4 × 50 Gbps PAM4 | PAM4 | RS(544,514) | SR4/DR4/FR4/LR4 | Common |
| 400 GbE | 802.3bs | 2017 | 8 × 50 Gbps PAM4 (early); 4 × 100 Gbps PAM4 (2022+) | PAM4 | RS(544,514) | SR4/DR4/FR4/LR4/ZR | Mainstream AI cluster spine 2023-2025 |
| **800 GbE** | 802.3df | **2024** | 8 × 100 Gbps PAM4 | PAM4 | RS(544,514) | SR8/DR8/FR8/2xFR4 + ZR | **Latest AI cluster spine** |
| **1.6 TbE** | 802.3dj (project) | **2026** | 8 × 200 Gbps PAM4 | PAM4 | RS(544,514) or "concatenated" | SR8/DR8 (3-5m DAC), VR8, CPO | **Next gen** — sampling 2025-2026 |
| 3.2 TbE | Future | 2028+ | 8 × 400 Gbps PAM6 or coherent | likely PAM6 or coherent | new | mostly optical | Roadmap |

**SerDes generations** (per-lane signaling):
- **NRZ 10 Gbaud** (10 Gbps NRZ) — through ~2014
- **NRZ 25 Gbaud** — through 2018; backbone of 100GbE 4×25
- **PAM4 50 Gbaud (50 Gbps)** — 2018-2022; 100GbE 2×50 and 400GbE 8×50
- **PAM4 100 Gbaud (100 Gbps)** — 2022-2025; 800GbE 8×100, ConnectX-7
- **PAM4 200 Gbaud (200 Gbps)** — 2024-2027; 1.6TbE 8×200, ConnectX-8 (200 Gbps per lane, 800 Gb/s port)

Above ~224 Gbaud, electrical SerDes hits SNR walls (PCB loss, connector reflections). The frontier above this is **co-packaged optics** (CPO) — putting the optical engines next to the switch ASIC so SerDes only traverses millimeters of substrate, not centimeters of PCB.

**FEC (Forward Error Correction):**
- **KR4**: Original 802.3 short-reach Reed-Solomon-like FEC for 100GbE backplanes.
- **RS(528,514)**: Reed-Solomon code, 528-symbol codeword with 514 data + 14 parity (BER from ~1e-5 to ~1e-15). Used in 25GbE (optional), 50GbE, 100GbE.
- **RS(544,514)**: Used in 100-800GbE. 544 symbols, 30 parity. Stronger code needed for PAM4's lower per-symbol SNR.
- **Concatenated FEC**: For 1.6TbE, an additional outer code may be layered on top of RS(544,514) for the most challenging PAM4 channels.

FEC adds latency — typically 100-200 ns of switching latency at 100 GbE PAM4. For latency-sensitive HPC, this is significant; for general DC traffic, it's invisible.

### 5.2 Data Center Bridging (DCB) Stack

For lossless Ethernet (required by RoCE v1/v2 and FCoE), IEEE 802.1 added four extensions, collectively called **DCB**:

| Standard | Name | What it does |
|---|---|---|
| **802.1Qbb** (PFC) | Priority Flow Control | Per-class pause: instead of pausing all traffic, pause only one of 8 traffic classes (CoS) |
| **802.1Qaz** (ETS) | Enhanced Transmission Selection | Bandwidth allocation: assign min/max % of link to each class group |
| **802.1Qau** (QCN) | Quantized Congestion Notification | Switch sends explicit congestion feedback to source (rarely used today, superseded by ECN) |
| **802.1Qaz** (DCBX) | DCB Exchange protocol | LLDP-based exchange of DCB capabilities/config between switch and endpoint |

**PFC** is the workhorse. A receiving switch port that fills its buffer sends a 16-bit `PAUSE` frame back to the sender's switch, listing which of 8 priorities should stop sending. The sender pauses that class for a quanta-encoded time. PFC pause storms (cyclic dependencies) are the bane of large lossless networks (see §11).

**ETS** assigns bandwidth groups: e.g., 60% for RDMA storage traffic, 30% for compute RDMA, 10% for management. Within a group, classes share bandwidth proportionally.

### 5.3 InfiniBand

InfiniBand (IB) is the gold standard for HPC/AI fabrics: lower latency, higher BW, and richer semantics than Ethernet. Maintained by the InfiniBand Trade Association (IBTA). Vendors: NVIDIA Mellanox is the dominant supplier (~80%+); Intel exited (Omni-Path) and Cornelis Networks now produces a competitor.

| Spec | Year | Per-lane signaling | x4 link BW (one direction) | Notes |
|---|---|---|---|---|
| SDR | 2003 | 2.5 Gbps NRZ | 8 Gbps (1 GB/s) | First gen |
| DDR | 2005 | 5 Gbps NRZ | 16 Gbps | |
| QDR | 2008 | 10 Gbps NRZ | 32 Gbps | |
| FDR | 2011 | 14 Gbps NRZ | 56 Gbps | First w/ 64b/66b encoding |
| EDR | 2014 | 25 Gbps NRZ | 100 Gbps | ConnectX-4 |
| HDR | 2017 | 50 Gbps PAM4 | 200 Gbps | ConnectX-6 / Quantum HDR switches |
| **NDR** | 2021 | 100 Gbps PAM4 | **400 Gbps** | ConnectX-7 / Quantum-2 switches; dominant in 2024 AI clusters |
| **XDR** | 2024 | 200 Gbps PAM4 | **800 Gbps** | ConnectX-8 / Quantum-X switches; AI scale 2025-2026 |
| GDR | Future | 400 Gbps PAM4 or coherent | 1.6 Tbps | Planned ~2027-2028 |

**IB queue pair types:**

| QP type | Reliable | Connected | Best for | Used by |
|---|---|---|---|---|
| **RC** (Reliable Connection) | yes | yes (1-to-1) | Bulk RDMA WRITE/READ, latency-sensitive | Most apps; MPI; NCCL; RDMA storage |
| **UC** (Unreliable Connection) | no (drops silently) | yes (1-to-1) | Streaming where loss is OK | Rare in modern code |
| **UD** (Unreliable Datagram) | no | no (1-to-many) | Multicast, discovery, low-msg-rate broadcasts | OpenSM SA queries, MPI bootstrap |
| **XRC** (Extended Reliable Connection) | yes | semi-connected; one QP serves many remote processes per node | Many-process MPI to reduce QP scaling | Mellanox MPI variants |
| **DCT** (Dynamically Connected Transport) | yes | dynamic (connect on demand) | Scale: 10000s of processes without 10000s of QPs | NVIDIA stack; UCX |

For an N-process MPI job using RC, each rank needs N-1 QPs — at N=10000 that's 100M QPs in the cluster, blowing through NIC QP-context memory. DCT solves this by reusing a small pool of QPs that get dynamically rewired to peers as messages arrive.

**Verbs basics (covered in §9):**
- **MR** (Memory Region): a registered, pinned, IOMMU-mapped region. Has `lkey` (local) and `rkey` (remote) used in RDMA WRITEs/READs.
- **CQ** (Completion Queue): receives completions when WRs finish.
- **WR** (Work Request): a single SEND/RECV/READ/WRITE/ATOMIC posted to a QP's send or receive queue.
- **Doorbells**: MMIO writes that tell the NIC "new work posted" — kernel-bypass.

**OpenSM (Open Subnet Manager):** Software that runs on one fabric-attached server and:
- Discovers all switches, routers, and end-nodes (HCAs)
- Assigns LIDs (16-bit local routing IDs)
- Computes routing tables (per-switch, per-destination)
- Configures partitions, QoS, SL-to-VL mappings
- Monitors fabric health

A typical NDR cluster has one or two OpenSM masters with hot-standby — if the master fails, standby takes over fabric management.

**Partitions (P_Keys):** IB equivalent of VLANs. A 16-bit P_Key tags every packet; switches/HCAs enforce isolation. Used in multi-tenant clusters where job A must not see job B's traffic.

**Topology:** IB clusters at scale almost always use **fat-tree** (Charles Leiserson, 1985) — a Clos network where bandwidth doubles toward the spine, giving full bisection bandwidth at any cut. Variants:
- **Full fat tree:** Every leaf has full BW upward — most expensive.
- **Tapered (2:1 oversubscribed):** Leaf has 2× downlinks vs uplinks. Common in cost-sensitive deployments.
- **Dragonfly+ / Dragonfly:** Used in some Mellanox/HPE Slingshot clusters for very large fabrics.

### 5.4 RoCE — RDMA over Converged Ethernet

RoCE brings IB verbs to Ethernet. Two versions:

| Version | Layer | Encapsulation | Routable? |
|---|---|---|---|
| **RoCEv1** | L2 only | Ethertype 0x8915 directly in Ethernet frame | No — single broadcast domain |
| **RoCEv2** | L3 | UDP/IP encap, UDP port **4791** | Yes — runs over any IP network |

RoCEv1 is dead — every modern deployment is RoCEv2.

**Requirements:**
- **Lossless fabric** (PFC enabled) — RoCE inherits IB's no-drop assumption. A packet drop forces a go-back-N retransmit, killing throughput.
- **DCQCN congestion control** (see §5.5) — without it, microbursts cause head-of-line blocking.
- **ECN** marking on switches (set CE bit at congestion).

**Tuning the DCB triangle:**
1. Configure PFC on the RDMA priority (typically priority 3).
2. Enable ECN with watermarks (Kmin ~10-15% buffer, Kmax ~80%) so most congestion is signaled via ECN before PFC fires.
3. Run DCQCN at the endpoint to react to ECN by rate-throttling.

When tuned right: ECN does 99% of the congestion management, PFC is a safety net for rare bursts.

**Soft-RoCE (rxe):** A pure-software RoCE implementation in the Linux kernel. Useful for development/test on hardware without RoCE NICs (any Ethernet NIC works). Performance is poor (verbs over UDP without offload), but the API surface matches real hardware.

### 5.5 Datacenter Congestion Control — A Deep Dive

Datacenter congestion control is its own subfield. The fundamental tension: low latency requires small buffers / short queues, while high throughput requires near-100% link utilization. Solving both at line rate, across thousands of concurrent flows, is hard.

**DCTCP** (Alizadeh, Greenberg, Maltz, Padhye, Patel, Prabhakar, Sengupta, Sridharan, *SIGCOMM 2010*): Uses ECN with **fractional marking**. The receiver computes a moving average `α = (1-g)α + g × F`, where F is the fraction of recent packets ECN-marked. Sender then reduces cwnd by `α/2` (vs TCP's 50% cut). Works on commodity Ethernet with ECN.

**DCQCN** (Zhu, Eran, Firestone, Guo, Lipshteyn, Liron, Padhye, Raindel, Yahia, Zhang, *SIGCOMM 2015*): The Microsoft Azure solution for RoCEv2.
- Receiver-side: when receiving an ECN-marked packet, sends a **CNP (Congestion Notification Packet)** to the sender.
- Sender adjusts a "target rate" and "current rate" based on CNP feedback.
- Parameters: `Kmin`, `Kmax` (switch ECN watermarks), `α` smoothing, fast/active recovery rules.
- Default settings are notoriously hard to tune; Microsoft's experience report (Guo et al., SIGCOMM 2016, "RDMA over Commodity Ethernet at Scale") documents painful real-world deployment.

**TIMELY** (Mittal, Lam, Dukkipati, Blem, Wassel, Ghobadi, Vahdat, Wang, Wetherall, Zats, *SIGCOMM 2015*): RTT-based, not ECN-based. Sender measures fine-grained RTT (NIC timestamps), and reduces rate when RTT exceeds a "target." Works without ECN-aware switches but requires precise NIC timestamps. Used at Google in pre-Swift era.

**HPCC** (Li, Miao, Liu, Zhou, Sridharan, Kumar, Bao, Zhou, Yang, Tewari, *SIGCOMM 2019*): Uses **In-band Network Telemetry (INT)** — switches embed per-hop queue depth + tx_bytes into packet headers. Sender computes precise per-hop utilization U and adjusts window. ~3× better tail latency than DCQCN. Used at Alibaba RDMA deployments.

**Swift** (Kumar, Dukkipati, Jouppi, Lam, Madhavan, Mittal, Mittal, Wassel, Wetherall, Wu, Yang, Zats, *SIGCOMM 2020*): Google's evolution of TIMELY. Decouples **fabric delay** (network RTT) from **endpoint delay** (NIC + host stack). Two-loop control: one for fabric congestion, one for endpoint congestion. Production protocol at Google for both TCP and RDMA-like traffic.

**PowerTCP** (Addanki, Apostolaki, Ghobadi, Schmid, Vanbever, *NSDI 2022*): Combines window (queue-based) and rate (delay-based) signals. Uses the *power* (queue × throughput) as the congestion signal. Especially good for short flows that don't get many RTT samples.

**EQDS** (Olteanu, Agache, Voinescu, Raiciu, *NSDI 2022*): **Receiver-driven** scheduling. Senders post intentions; receivers issue per-packet "credits" controlling who sends when. Eliminates congestion at the receiver side entirely; well-suited for AI training where receiver = parameter server. Adopted in NVIDIA's BlueField stack experiments.

**IRN** (Improved RoCE NIC; Mittal, Shpiner, Panda, Zahavi, Krishnamurthy, Ratnasamy, Shenker, *SIGCOMM 2018*): Replaces go-back-N with selective-ACK + bitmap retransmit for RoCEv2. Allows running RoCE on lossy fabric (no PFC needed), trading off some throughput for elimination of PFC pause storms.

**Annulus** (Stephens, Akella, Swift, *SIGCOMM 2019*): Per-flow scheduling at the host via fast NIC primitives; complement to switch-side CC.

### 5.6 iWARP

iWARP (Internet Wide Area RDMA Protocol) is RDMA layered over **TCP**, not UDP. Three protocol layers stack:
- **RDMAP** — RDMA verbs (above DDP)
- **DDP** (Direct Data Placement) — handles segmentation and reassembly into pre-registered buffers
- **MPA** (Marker PDU Aligned framing) — frames DDP segments and adds CRCs

Pros: Runs over **any** IP network. Works in WAN. No special PFC tuning.
Cons: TCP overhead (slower start, complex congestion control) limits throughput vs RoCE. NIC implementations are rare today; Chelsio is the main vendor. Largely a niche choice in 2026.

### 5.7 Ultra Ethernet Consortium (UEC) 1.0

UEC is a Linux Foundation project (launched July 2023) explicitly chartered to build a **lossy, packet-spraying, modern transport** for AI workloads that beats InfiniBand on scale-out cost while matching its latency. Spec 1.0 released June 2025; member companies include AMD, Arista, Broadcom, Cisco, Eviden, HPE, Intel, Meta, Microsoft, NVIDIA (joined late 2024).

Key innovations:

| Innovation | What it does | Vs traditional |
|---|---|---|
| **RUD / RUDI (Reliable Unordered Delivery)** | Transport delivers packets unordered to NIC; reorder in NIC hardware on receive | Vs RC's strict in-order |
| **Packet spraying** | Every packet of a flow takes a different path (per-packet ECMP) | Vs traditional 5-tuple-hashed ECMP which sticks one flow to one path |
| **Out-of-order delivery** | NIC + transport handle reorder; sender doesn't pace per path | Eliminates head-of-line blocking |
| **Ephemeral connections** | Connection state set up at first message, torn down after idle; no persistent QPs | vs RC's persistent QPs |
| **Modernized CC** | Built-in HPCC/Swift-style signaling | vs DCQCN tuning headaches |
| **libfabric provider** | Software accessed via OFI providers | Familiar APIs |

The goal is to use **commodity Ethernet switches** (which can ECMP per-packet via load balancing on packet hashes) to achieve near-100% utilization without the IB premium. AMD (Pensando NICs) and Broadcom (Tomahawk switches) are leading hardware deployment.

UEC is the industry's bet that the next generation of AI scale-out fabrics will run on Ethernet, not IB.

### 5.8 Topologies — ASCII Diagrams

**Two-tier Clos (Leaf-Spine) — typical DC topology:**

```
            ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐
  Spine     │ S1   │ │ S2   │ │ S3   │ │ S4   │
            └─┬─┬──┘ └─┬─┬──┘ └─┬─┬──┘ └─┬─┬──┘
              │ │      │ │      │ │      │ │
       ╔══════╪═╪══════╪═╪══════╪═╪══════╪═╪═════╗
       ║      │ │      │ │      │ │      │ │     ║   Each leaf
       ║    every leaf has 4 uplinks (1 to each spine) ║   ────────
       ╚══════╪═╪══════╪═╪══════╪═╪══════╪═╪═════╝   ─ 32-64 server
              │ │      │ │      │ │      │ │            downlinks
            ┌─┴─┴──┐ ┌─┴─┴──┐ ┌─┴─┴──┐ ┌─┴─┴──┐         ─ 4-16 spine
  Leaf      │ L1   │ │ L2   │ │ L3   │ │ L4   │           uplinks
            └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘
               │        │        │        │
            servers  servers   servers   servers
            (~32)    (~32)     (~32)     (~32)
```

**Three-tier fat-tree (Charles Leiserson 1985):**

```
                  Core (Super-spine)
                ┌──┬──┬──┬──┬──┐
                │  │  │  │  │  │
        ┌───────┴──┴──┴──┴──┴──┴───────┐
        │          full bisection      │
   ┌────┴────┐  ┌────┴────┐  ┌────┴────┐
   │ Spine 1 │  │ Spine 2 │  │ Spine 3 │
   └─┬─┬─┬─┬─┘  └─┬─┬─┬─┬─┘  └─┬─┬─┬─┬─┘
     │ │ │ │      │ │ │ │      │ │ │ │
   ┌─┴─┐         ┌─┴─┐         ┌─┴─┐
   │L1 │  ...    │L9 │  ...    │L17│  ...
   └─┬─┘         └─┬─┘         └─┬─┘
   servers       servers       servers
```

**Dragonfly+ (HPE Slingshot, Cray, IB Quantum-2 dragonfly mode):**

```
        ┌─────────── group 1 ──────────┐    ┌─────── group 2 ──────┐
        │  ┌───┐  ┌───┐  ┌───┐  ┌───┐  │    │  ┌───┐ ┌───┐ ┌───┐   │
        │  │S1 ├──┤S2 ├──┤S3 ├──┤S4 │  ◄════►  │S1 ├─┤S2 ├─┤S3 │.. │
        │  └─┬─┘  └─┬─┘  └─┬─┘  └─┬─┘  │    │  └─┬─┘ └─┬─┘ └─┬─┘   │
        │    │      │      │      │     │    │    │     │     │     │
        │  servers  servers ...           │    │  ...                 │
        │   (1 link to every other        │    │                      │
        │    switch in same group)        │    │                      │
        └─────────────────────────────────┘    └──────────────────────┘

Topology: 3-tier
  1. Within a group, switches fully meshed (1 hop)
  2. Between groups, fewer long links (typically 1-4 per pair of groups, called "global links")
  3. To reach a faraway server: src_sw → src_group_egress_sw → dst_group_ingress_sw → dst_sw  (3 hops max)
```

Dragonfly's advantage over fat-tree: ~30% fewer optical links for the same bisection. Disadvantage: requires **adaptive routing** — picking which global link to use based on congestion — to avoid traffic concentrating on a few global links. Cray's Cassini (Slingshot 11/12) and Mellanox Quantum-2 dragonfly mode both implement adaptive routing in switch silicon.

**Rail-optimized topology (critical for AI):**

```
    GPU 0       GPU 1       GPU 2       GPU 3       (per server)
   ┌──┴──┐    ┌──┴──┐    ┌──┴──┐    ┌──┴──┐
   │NIC0 │    │NIC1 │    │NIC2 │    │NIC3 │
   └──┬──┘    └──┬──┘    └──┬──┘    └──┬──┘
      │          │          │          │
   ┌──┴─────────────────────────────────┴──┐
   │   Rail 0:  Leaf "Rail-0" connects     │
   │   ALL servers' NIC0 to same leaf      │  ← rail-optimized leaf-spine
   │   Rail 1:  Leaf "Rail-1" connects all NIC1
   │   Rail 2:  Leaf "Rail-2" connects all NIC2
   │   Rail 3:  Leaf "Rail-3" connects all NIC3
   └────────────────────────────────────────┘
```

In rail-optimized topology, GPU-i on every server connects to the same leaf in "rail i". For AllReduce, where every GPU communicates only with the same rank on other servers (ring reduction stays within a rail), traffic never crosses rails — eliminating cross-rail congestion. NCCL with `NCCL_IB_HCA` set per-rail uses this naturally.

### 5.9 Other DC Network Topologies

- **HyperX** (Ahn et al., SC 2009): Generalized hypercube; trade-off between dragonfly and fat-tree.
- **3D Torus** (Tofu-D, BlueGene): Each switch connects to 6 neighbors. Used in supercomputers but rare in commodity DCs.
- **Jellyfish** (Singla et al., NSDI 2012): Random regular graph topology. Higher throughput than fat-tree for a given switch budget, but routing is harder. Academic mostly.
- **F10** (Liu et al., NSDI 2013): Fault-resilient symmetric fat-tree variant.

---

## 6. Tier 5 — HPC Fabrics

HPC fabrics target the largest supercomputers and AI training clusters where commodity Ethernet/IB still leave performance on the table. Today, three live ecosystems matter:

### 6.1 HPE Slingshot

**Slingshot** is the interconnect used in HPE Cray EX supercomputers (Frontier, El Capitan, Aurora's Slingshot variant). Based on Cassini NIC ASIC + Rosetta switch ASIC.

| Generation | Year | Per-port BW | Topology | Deployed in |
|---|---|---|---|---|
| **Slingshot 11** | 2022 | 200 Gbps Ethernet | dragonfly+ | Frontier (9408 nodes), Adastra, LUMI |
| **Slingshot 12** | 2024 | 400 Gbps Ethernet | dragonfly+ | El Capitan, Aurora (variants), next-gen Cray EX |

**Cassini NIC**: AMD-designed (HPE-acquired) RDMA-capable NIC with:
- Adaptive routing (per-packet)
- Selective congestion management (small-flow priority)
- HPC-specific extensions over Ethernet: source routing, in-network telemetry, on-NIC reductions for collectives (similar to SHARP)

**Slingshot adds HPC features to Ethernet**, including:
- Adaptive routing in the dragonfly to avoid hot-spot global links
- Fine-grained per-flow buffer credits
- Custom congestion control (not stock DCQCN)
- Ethernet compatibility: still speaks 200/400 GbE to commodity NICs (so a Slingshot cluster can also host generic ML pods)

Deployed at scale in Frontier (first exascale system, Oak Ridge, 9408 EPYC nodes × 4 MI250X each), El Capitan (LLNL, ~11000 MI300A nodes), and others.

### 6.2 Fujitsu Tofu Interconnect D (TofuD)

Fujitsu's TofuD is the proprietary interconnect of **Fugaku** (Riken supercomputer, 158k A64FX nodes, peak #1 in TOP500 from 2020-2022). 6D mesh/torus topology, no central switch.

Key features:
- **6D structure**: Each node has 10 links in a "TofuD unit" of 12 nodes (A64FX chips); units stack into a 6D mesh
- **28 Gbps per link**, 10 links per node → 280 Gbps per node aggregate
- **Virtual 2D/3D mapping**: Applications request a logical 2D or 3D subdomain; the OS maps onto the 6D physical topology to minimize hops
- **HW collectives**: AllReduce-style barrier + reduction primitives in switch silicon
- **Multi-rail in software**: MPI rank-to-link assignment optimizable per phase

TofuD's 6D structure means any pair of nodes is at most ~5-6 hops apart in a 158k-node system, vs ~3 in a fat-tree but with no expensive optical cabling between groups. A great fit for stencil computations (CFD, weather modeling) where neighbors-only communication dominates.

### 6.3 Cray Aries, Gemini (Legacy)

- **Aries** (Cray XC-series, 2013-2020): Dragonfly topology; first widely-deployed dragonfly. Used in Piz Daint, Cori, Theta, Trinity.
- **Gemini** (Cray XE/XK-series, 2010-2014): 3D torus. Used in Titan (Oak Ridge), Hopper (NERSC).

Both retired in current production (last large Gemini system: Blue Waters, decommissioned 2019). Slingshot replaced Aries.

### 6.4 Intel Omni-Path → Cornelis Networks CN5000

Intel acquired QLogic's Trad-PSM in 2012, evolved it into Omni-Path (OPA), but exited the business in 2019. The IP was acquired by **Cornelis Networks** (founded 2020 by Omni-Path veterans), which continues development as:

- **Omni-Path Express (CN5000)**: 400 Gbps per port, deployed in some DOE labs (LLNL, ANL) and HPC academic clusters.
- Features: PSM3 (Performance Scaled Messaging 3) software stack, libfabric integration, low-overhead RDMA.
- Niche but active in HPC; not a major AI play.

### 6.5 IBM BlueGene Tree + Torus (Historical)

The BlueGene family (L/P/Q, 2004-2018) at LLNL used a 3D (BG/L, BG/P) or 5D (BG/Q) **torus** for nearest-neighbor traffic, plus a separate **collective tree** for reductions/broadcasts and a **global interrupt/barrier** network. Three physical networks for three traffic patterns. This pattern (separate network per traffic class) was efficient but expensive — modern systems consolidate via virtual channels on a unified fabric. BlueGene retired ~2019 (Sequoia decommissioned).

### 6.6 Anton 2 / Anton 3 (DE Shaw Research)

The Anton series are ASIC-based molecular-dynamics machines built by D. E. Shaw Research. Each Anton 2 chip integrates 64 specialized processing tiles plus a **dedicated 3D-torus interconnect** that runs molecular-dynamics specific kernels (PME-like FFTs, bond/non-bond force computations) at low latency. Per-link BW: ~5 Gbps × 6 directions per chip. Total system: 512 chips in 3D torus.

Anton 3 (announced 2021, SC22 paper): 64 tiles per ASIC at 6 nm, faster torus links, simulates 100+ µs of MD per day on multi-million-atom systems — far beyond any GPU cluster for this specific workload. The lesson is that for a fixed compute pattern (MD), a custom ASIC + custom topology beats general-purpose hardware by 50-100×.

---

## 7. Tier 6 — Software Stacks

### 7.1 libibverbs (Verbs API)

The core RDMA API on Linux, originally Mellanox/QLogic, now in `rdma-core` (https://github.com/linux-rdma/rdma-core). Header: `<infiniband/verbs.h>`. Object lifecycle:

```c
struct ibv_context *ctx = ibv_open_device(dev);
struct ibv_pd *pd = ibv_alloc_pd(ctx);
struct ibv_mr *mr = ibv_reg_mr(pd, buf, len, IBV_ACCESS_LOCAL_WRITE |
                                              IBV_ACCESS_REMOTE_READ |
                                              IBV_ACCESS_REMOTE_WRITE);
struct ibv_cq *cq = ibv_create_cq(ctx, depth, NULL, NULL, 0);
struct ibv_qp_init_attr attr = {
    .send_cq = cq, .recv_cq = cq,
    .cap = { .max_send_wr = 64, .max_recv_wr = 64,
             .max_send_sge = 4, .max_recv_sge = 4 },
    .qp_type = IBV_QPT_RC,
};
struct ibv_qp *qp = ibv_create_qp(pd, &attr);

// transition: INIT → RTR (ready to receive) → RTS (ready to send)
ibv_modify_qp(qp, &qp_init_attr,
              IBV_QP_STATE | IBV_QP_PKEY_INDEX | IBV_QP_PORT | IBV_QP_ACCESS_FLAGS);
// ... (RTR / RTS transitions with remote_qpn, remote_lid)

// Post a send WR
struct ibv_send_wr swr = {
    .wr_id = 1,
    .opcode = IBV_WR_RDMA_WRITE,
    .send_flags = IBV_SEND_SIGNALED,
    .wr.rdma.remote_addr = remote_addr,
    .wr.rdma.rkey = remote_rkey,
    .sg_list = &sge, .num_sge = 1,
};
struct ibv_send_wr *bad;
ibv_post_send(qp, &swr, &bad);

// Poll completion
struct ibv_wc wc;
while (ibv_poll_cq(cq, 1, &wc) == 0) { /* spin */ }
if (wc.status != IBV_WC_SUCCESS) { /* handle error */ }
```

**Cost of MR registration:** `ibv_reg_mr()` pins pages, builds NIC translation tables, and (with strict IOMMU) sets IOTLB entries. For a 1 GB region, this can take 10s of milliseconds. Hot path apps register large MRs at startup and reuse them. **ODP (On-Demand Paging)** avoids the pinning by letting the NIC take a page fault (via PCIe ATS+PRI), translated by host kernel. Latency penalty: ~5-10 µs per fault. Use for sparse access patterns over very large regions.

### 7.2 rdma-core, librdmacm

**rdma-core**: Single package containing libibverbs, librdmacm, libmlx5, etc. The reference RDMA userspace library.

**librdmacm** (RDMA Connection Manager): higher-level helpers for setting up RC connections. Models on BSD sockets — `rdma_create_id()`, `rdma_resolve_addr()`, `rdma_connect()`, `rdma_accept()`. Translates IP addresses to QP numbers + RDMA-specific routing. Used by NVMe-oF kernel target, Lustre, GPFS, ceph rdma-msgr, etc.

### 7.3 UCX — Unified Communication X

UCX (https://openucx.org/) is the unified software stack chosen by both **NCCL** and most modern **MPI** implementations.

Layers:

```
┌─────────────────────────────────┐
│ Applications  (MPI, NCCL, ...)  │
└─────────────────────────────────┘
            │
┌─────────────────────────────────┐
│ UCP   protocol layer            │  ← per-message protocol selection (eager/rendezvous), tag matching
└─────────────────────────────────┘
            │
┌─────────────────────────────────┐
│ UCT   transport layer           │  ← per-transport (verbs/RC, verbs/UD, DC, RDMA-CM, CUDA, ROCm, TCP)
└─────────────────────────────────┘
            │
┌─────────────────────────────────┐
│ UCS   services (config, mempool)│
└─────────────────────────────────┘
```

UCX automatically picks the fastest transport per peer (RC for short distances, DC for many-peer, GPU-Direct for intra-node GPU-to-GPU). UCX is used by:
- **NCCL** (2.10+) — primary plugin
- **Open MPI** (4.x+) — via `mca pml ucx`
- **MVAPICH2-X**
- **HPC-X** (Mellanox stack)
- **Charm++**, **Legion**, **HPX**

### 7.4 libfabric / OFI Providers

**libfabric** (OFI = OpenFabrics Interfaces) is an alternative high-level RDMA API maintained by the OpenFabrics Alliance. Different abstraction from UCX — more API-focused, less protocol-driven.

Providers (transports):
- `verbs` — generic libibverbs
- `efa` — AWS Elastic Fabric Adapter (custom RDMA-like transport over AWS-specific NIC)
- `psm3` — Cornelis Omni-Path / Intel Performance Scaled Messaging
- `cxi` — Cray/HPE Slingshot (Cassini)
- `tcp` — sockets fallback
- `sockets` — UDP-based testing
- `shm` — shared-memory (intra-node)
- `opx` — Omni-Path Express
- `ucx` — libfabric over UCX (interop)

libfabric is preferred by **AWS**, **Cray/HPE**, **Intel** stacks. UCX dominates **NVIDIA** + **Mellanox** stacks. Both interoperate but choosing one is usually deterministic per-vendor.

### 7.5 MPI — Comparison Table

| Implementation | Vendor | Primary transport plugin | Strengths | Weaknesses |
|---|---|---|---|---|
| **Open MPI** | Open consortium | UCX, libfabric, BTL | Most portable; works everywhere | Tuning surface; defaults rarely optimal |
| **MPICH** | ANL | libfabric (CH4), ch3 (legacy) | Reference impl; many forks (MVAPICH, Intel) | Less rich collective lib than NCCL |
| **Intel MPI** | Intel | libfabric | Tight x86 + OPX integration | Less common on AMD/ARM |
| **MVAPICH2-X** | OSU/NSF | UCX + verbs | InfiniBand specialist; GPU-aware | Less Ethernet/cloud support |
| **HPC-X / NVIDIA Mellanox MPI** | NVIDIA | UCX, SHARP, NCCL | Top performance on IB + NVLink | Vendor-tied |
| **Cray MPI** (MPICH-derived) | HPE | OFI/cxi for Slingshot | Slingshot specialist | Tied to Cray EX |

**Collective algorithms (key MPI primitives):**

| Collective | Naive | Better | Used When |
|---|---|---|---|
| **Broadcast** | flat (root → all, N msgs) | binomial tree O(log N) | Always (default) |
| **AllReduce** | flat (gather + scatter) | recursive doubling, Rabenseifner (split big msgs) | depends on msg size |
| **AllGather** | flat ring | Bruck (log N steps, longer per step) | small msgs |
| **AllToAll** | spread / Bruck | Bruck for small msgs, pairwise exchange for large | always tuned |
| **Reduce_scatter** | recursive halving | Rabenseifner | medium-large msgs |

Modern MPI implementations include adaptive selection: choose algorithm per (msg size, ranks, topology). MPI-4 added **persistent collectives** (`MPI_Bcast_init` + `MPI_Start`) — re-using a pre-planned schedule for repeated collectives, eliminating planning overhead.

### 7.6 NCCL — NVIDIA Collective Communications Library

NCCL (https://github.com/NVIDIA/nccl) is the dominant GPU collective library for AI training. Key concepts:

- **AllReduce algorithm choice**:
  - **Ring**: All-Reduce in 2(N-1) steps, each GPU sends M/N data per step. Time: `2(N-1)/N × M/B`. Used for medium-to-large messages where bisection BW dominates.
  - **Tree**: Reduce up a binary tree (log N depth), broadcast down (log N depth). Total: 2 log N hops. Time: `2 log N × α + 2 M/B`. Used for small messages where latency α dominates.
  - **NVLS (NVLink Sharp)**: In-network reduction via NVSwitch ALUs. Time approximately `M / (2 × B)` — halves AllReduce bandwidth. Used for HGX-H100+ and NVL72 where NVSwitch 3+ is present.
  - **SHARP** (Mellanox InfiniBand): Switch-side reduction in IB switches. Same idea as NVLS but on IB. Used in HGX nodes + IB scale-out.

NCCL dynamically picks the algorithm per (message size, topology, ranks).

- **Channels**: Each AllReduce uses N parallel "channels" — independent ring/tree paths through the topology. More channels = more concurrent NVLink/IB flows = higher BW. Default 8-16; tuned via `NCCL_MIN_NCHANNELS` / `NCCL_MAX_NCHANNELS`.

- **Topology detection**: At init, NCCL probes the system topology (PCIe layout, NVLink topology, NIC binding) and builds a tree representation. `NCCL_TOPO_DUMP_FILE=topo.xml` writes the detected topology to inspect.

- **Critical environment variables:**

| Variable | Purpose |
|---|---|
| `NCCL_DEBUG=INFO` | Verbose logging including topology decisions |
| `NCCL_DEBUG_SUBSYS=ALL` | Per-subsystem logs (INIT, COLL, P2P, NET, GRAPH) |
| `NCCL_TOPO_DUMP_FILE=topo.xml` | Dump system topology XML |
| `NCCL_IB_HCA=mlx5_0,mlx5_1` | Restrict NCCL to specific IB HCAs (e.g., rail-aware) |
| `NCCL_IB_GID_INDEX=3` | RoCE v2 GID (RDMA over Ethernet — match VLAN/network) |
| `NCCL_NET_GDR_LEVEL=PHB` | Enable GPU-Direct RDMA threshold (PHB = same PCIe host bridge) |
| `NCCL_P2P_DISABLE=1` | Disable peer-to-peer NVLink (debug only) |
| `NCCL_COLLNET_ENABLE=1` | Enable SHARP (in-network reduction) |
| `NCCL_ALGO=Tree` / `Ring` / `NVLS` | Force algorithm selection |
| `NCCL_NCHANNELS_PER_PEER=N` | Channels per peer link |

**NCCL-tests** repo (https://github.com/NVIDIA/nccl-tests) provides standard benchmarks. Run `all_reduce_perf -b 8 -e 8G -f 2 -g 8` to test AllReduce bandwidth from 8 B to 8 GB across 8 GPUs.

### 7.7 RCCL (AMD) and Gloo

**RCCL** (https://github.com/ROCm/rccl): AMD's NCCL-compatible reimplementation for ROCm/MI series. API-compatible with NCCL but uses xGMI/Infinity Fabric + HIP RDMA primitives.

**Gloo** (Facebook/Meta): CPU and GPU collective library originally built for PyTorch when NCCL was less mature. Still used as a CPU-only fallback (e.g., parameter sharding) and on networks where NCCL doesn't work (older Ethernet, mixed-vendor). Slower than NCCL on GPU clusters.

### 7.8 DPDK and XDP

**DPDK** (Data Plane Development Kit): User-space, poll-mode driver framework. Bypasses the Linux kernel completely; the NIC is unbound from the kernel driver and bound to `vfio-pci`. DPDK PMD (poll mode driver) constantly polls the NIC RX rings from a dedicated core, eliminating interrupts. Achieves 30-40 Mpps (million packets per second) per core for 64-byte packets — orders of magnitude beyond kernel networking.

Use cases: software switches (OVS-DPDK), NFV, Click-style routing, 5G UPF, AI ingress.

**XDP** (eXpress Data Path): In-kernel, eBPF-based programmable packet processing. Hooks at the NIC driver before sk_buff allocation. Three modes:
- `XDP_DROP` / `XDP_PASS` / `XDP_REDIRECT` (to another NIC or userspace via AF_XDP)
- Can run at 100+ Mpps on modern NICs
- Used by Cilium, Cloudflare load balancer, Meta's Katran

**AF_XDP**: Userspace socket type that lets userspace receive packets via XDP_REDIRECT — combines kernel safety with userspace performance.

### 7.9 io_uring, SPDK (Storage Side)

(See [io_uring_internals.md](@/notes/os/io_uring_internals.md), [vfio_internals.md](@/notes/os/vfio_internals.md) for full coverage.)

- **io_uring**: Async I/O via SQ/CQ rings, optionally SQPOLL (kernel poller). For NVMe-oF clients, gives near-DPDK performance with mainline kernel.
- **SPDK** (Storage Performance Development Kit): User-space NVMe driver framework, the storage analog of DPDK. Used to build high-performance NVMe targets (vhost-user-blk, NVMe-oF target, blobfs).

### 7.10 GPU-Direct: RDMA, Storage, Magnum IO

**GPU-Direct RDMA (GDR)**: NIC writes directly into GPU HBM via PCIe peer-to-peer, no CPU/host-memory bounce. NIC must be on same PCIe root complex (PHB) as GPU; IOMMU must allow P2P (or be set to passthrough). NCCL uses this transparently for IB/RoCE transports.

**GPU-Direct Storage (GDS)**: NVMe-oF (or local NVMe) reads/writes go directly to GPU HBM. Path: NVMe → PCIe → GPU. Used heavily for large LLM checkpoint load/save (e.g., load Llama 3 70B weights from NFS into GPUs in seconds, not minutes).

**Magnum IO** (NVIDIA umbrella SDK): GDR + GDS + UCX optimizations + DALI (data loader). Used to design end-to-end I/O paths in DGX clusters.

### 7.11 NIXL — NVIDIA Inference Transfer Library (2024-2025)

**NIXL** (NVIDIA Inference Transfer Library, late 2024 / 2025) is NVIDIA's new abstraction layer for **disaggregated LLM inference** — moving KV-cache, model partitions, and intermediate activations between GPUs/nodes for inference systems like Dynamo, vLLM, TensorRT-LLM Serving.

Use case: in LLM serving, prefill (compute KV cache) and decode (autoregressive) have very different compute profiles. Disaggregated inference puts prefill on one pool of GPUs and decode on another, and ships KV-cache between them via NIXL. NIXL supports:
- Multiple transports (NVLink, RoCE, IB)
- Async fire-and-forget transfers
- Tensor partitioning / sharding semantics
- Integration with KV-cache prefix-sharing systems (Mooncake, vLLM)

Released open-source mid-2025; rapidly becoming the standard transport layer for inference disaggregation.

---

## 8. Tier 7 — Optical / Future

### 8.1 Silicon Photonics Fundamentals

Silicon photonics is **integrated photonic circuits** on silicon (or SiGe) substrates — light modulators, waveguides, and photodetectors all in CMOS. The two dominant modulator topologies:

**Mach-Zehnder Modulator (MZM):**
```
                   ┌─── arm 1 (active phase shift) ───┐
   Light in ─────┤                                    ├──── Light out (mod amp = sin²(Δφ/2))
                   └─── arm 2 (reference) ────────────┘
```
A Mach-Zehnder modulator splits the input light into two paths, applies a phase shift on one arm electrically (via thermo-optic or carrier-injection), and recombines. Output amplitude is `cos²(Δφ/2)` — full extinction is possible. Bandwidth: 50-100+ GHz on modern Si photonics. Power: ~mW per modulator. Used in commercial 400ZR/800ZR pluggables.

**Microring Modulator:**
```
   Light in ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ Light out
                            │
                          (ring resonator: small ring waveguide,
                           heater on top, voltage shifts ring resonance,
                           which absorbs / transmits the input)
```
A small (10-50 µm diameter) ring resonator coupled to a straight waveguide. When the ring is at resonance, it absorbs light at that wavelength (notch filter). Electrically tuning the resonance modulates input transmission. Much smaller and lower-power than MZM (~10s of µW per modulator), but narrow wavelength range (sensitive to temperature, requires active tuning). Used in dense WDM photonic chiplets (Ayar Labs, Lightmatter).

**WDM / DWDM (Wavelength Division Multiplexing):** Multiple optical signals at different wavelengths share one fiber. Per fiber:
- **CWDM** (Coarse): 8-18 wavelengths, 20 nm spacing
- **DWDM** (Dense): up to 96 wavelengths at 0.4-0.8 nm spacing (50/100 GHz grid)

A DWDM 80-channel link at 100 Gbps per wavelength = 8 Tbps per fiber. Long-haul WAN fibers commonly carry 10-40 Tbps via DWDM.

### 8.2 Co-Packaged Optics (CPO)

The premise: at 1.6+ Tbps per port, electrical SerDes can no longer reach across a PCB to a pluggable optical module (the QSFP-DD/OSFP cage). The solution is **co-packaged optics** — placing the optical engine (laser, modulator, photodetector) directly on the switch package, with millimeter-scale electrical hops only.

**Broadcom Tomahawk 5 / 6**:
- Tomahawk 5: 51.2 Tbps switch ASIC (2023), 64 × 800 GbE ports. Pluggable optics standard; CPO variant in development.
- Tomahawk 6: **102.4 Tbps** switch ASIC (2024-2025). 64 × 1.6 TbE ports. CPO variants announced for 2026.

**NVIDIA Quantum-X Photonics / Spectrum-X Photonics** (announced GTC 2025): NVIDIA's first co-packaged-optics switches. Quantum-X (InfiniBand variant) and Spectrum-X (Ethernet/UEC variant). Each has 144 × 800 Gbps ports = 115 Tbps. Massive reduction in optical-module cost and power (no separate pluggable transceivers).

**TSMC COUPE**: TSMC's Compact Universal Photonic Engine — a packaged photonic engine reference design (announced 2024) targeted at 1.6T+ switching. Available to ASIC partners.

### 8.3 400ZR / 800ZR — Coherent Pluggables

For metro/regional optical transport, **coherent** optics replaces direct-detection IM-DD.

| Standard | Year | Per-port rate | Modulation | Reach (unamplified) | Form factor |
|---|---|---|---|---|---|
| 100G-ZR | 2014 | 100 Gbps | DP-QPSK | 80 km | C Form Pluggable |
| 400ZR | 2020 | 400 Gbps | **DP-16QAM** | 120 km | QSFP-DD |
| 400ZR+ | 2021 | 400 Gbps | DP-16QAM with FEC enhancements | 500+ km | QSFP-DD |
| **800ZR** | 2024 | **800 Gbps** | DP-16QAM at higher baud or DP-64QAM | ~80-120 km | QSFP-DD800 / OSFP |
| 1.6T ZR | future | 1.6 Tbps | DP-64QAM or DP-256QAM probabilistically shaped | ~80-120 km | OSFP-XD |

**DSP-based coherent**: Modern coherent pluggables have an integrated DSP that performs:
- Dispersion compensation (chromatic + polarization-mode)
- Polarization tracking
- Phase noise compensation
- Soft-decision FEC (LDPC + outer staircase)

These DSPs are sophisticated ASICs (3-5 nm) consuming 10-20 W and contributing most of the pluggable's cost.

**Use cases:** 400ZR/800ZR replaces traditional "transponder + line card" architectures. Datacenter Interconnect (DCI) for metro DCs, cloud regions, hyperscale Edge nodes. Some vendors (Google, Microsoft, Meta) routinely use 400ZR for DCI between buildings within a metro region.

### 8.4 Optical Circuit Switching (OCS)

OCS = a switch that routes light entirely in the optical domain (no electrical conversion). Slow to reconfigure (1-100 ms) but **infinite bandwidth** through any single circuit while connected, and very low power per bit.

**Google Apollo / Apollo 2 / Sirius / Lightning:**

| Generation | Year | Switching tech | Latency | Use case |
|---|---|---|---|---|
| **Apollo** (Liu et al., SIGCOMM 2021) | 2018-2021 | MEMS mirrors | ~10 ms reconfig | Spine layer of Jupiter datacenter network |
| **Apollo 2** | 2022 | MEMS, larger radix | ~10 ms | Jupiter Rising (Poutievski et al., SIGCOMM 2022) |
| **Sirius** (Ballani et al., SIGCOMM 2020) | research | Tunable laser + AWG (passive optical) | sub-µs | Microsoft / academic prototype |
| **Lightning / Lightning-2** | 2024 announcements | OCS for AI training | µs-scale | Specialized for AI workloads |

**Why OCS in datacenters?** Bursty traffic patterns: 80% of bytes flow between 20% of node pairs. If you can dynamically configure circuit-switched links to the hot pairs, you save 4-8× in spine bandwidth vs always-on packet-switched bisection. Google's Jupiter uses OCS at the spine to dynamically reroute capacity to where it's needed.

**Google Apollo MEMS:** Tiny micromirror arrays (~256-radix per OCS) steer light from input port to output port. Reconfiguration takes ~10 ms (mirror settling time). Apollo-class OCS chassis are deployed in tens-of-petabit-per-second Google networks.

**Lightning** is a newer OCS class targeting AI training topology reshaping — letting the same physical cabling host both a fat-tree (for one training job) and a dragonfly (for another) by reconfiguring OCS.

### 8.5 Optical Chiplets / Photonic Fabrics

The cutting edge: instead of co-packaging optics with a single switch ASIC, the goal is **photonic chiplets** that any chiplet vendor can drop in.

**Lightmatter Passage:**
- An "active photonic interposer" — silicon substrate with both electrical chiplets (compute) and integrated photonic transceivers + waveguides on top.
- Lets you place 8-16 compute chiplets on a single interposer with photonic interconnect between them (sub-pJ/bit energy, multi-Tbps per chiplet pair).
- Targeted at AI training where chip-to-chip bandwidth bottlenecks scaling. Production samples mid-2024.

**Ayar Labs TeraPHY:**
- CMOS chiplet that does WDM laser + modulator + detector at 8 × 256 Gbps = 2 Tbps per chiplet
- UCIe-compatible electrical interface to the host die
- Demoed in Intel Sapphire Rapids systems and Cornelis Networks switches.

**Celestial AI Photonic Fabric:**
- Hierarchical photonic switch with explicit "Photonic Fabric" abstraction layer.
- Targets AI training/inference at hyperscale; partnership with AMD, Samsung announced 2024-2025.

These are all in early production. By 2027-2028, expect photonic chiplets to be a normal part of high-end AI accelerator packaging — much as HBM became normal in 2020.

---

## 9. RDMA Semantics Deep Dive

RDMA verbs are the lingua franca of high-performance networking. Understanding the precise semantics is critical for correctness and performance.

### 9.1 Two-Sided: SEND / RECV

Like BSD sockets — both ends post WRs.

```c
// Sender:
struct ibv_send_wr swr = { .opcode = IBV_WR_SEND, /* ... */ };
ibv_post_send(qp, &swr, NULL);

// Receiver — MUST have a RECV posted in advance:
struct ibv_recv_wr rwr = { .wr_id = 1, .sg_list = &sge, .num_sge = 1 };
ibv_post_recv(qp, &rwr, NULL);
```

If no RECV is posted when SEND arrives → **RNR NAK** (Receiver Not Ready), sender retries with backoff. Tune `min_rnr_timer` and `retry_cnt` in QP attrs.

Use for: message passing, control plane, RPC. Costs both ends a verb posting per message.

### 9.2 One-Sided: WRITE

Sender pushes data into receiver's pre-registered memory **without involving the receiver's CPU**.

```c
struct ibv_send_wr swr = {
    .opcode = IBV_WR_RDMA_WRITE,
    .wr.rdma.remote_addr = remote_buf_addr,
    .wr.rdma.rkey = remote_mr->rkey,
    .sg_list = &sge,
    .num_sge = 1,
    .send_flags = IBV_SEND_SIGNALED,
};
ibv_post_send(qp, &swr, NULL);
```

The receiver's CPU is unaware until it polls memory or receives an out-of-band signal. Use cases:
- Distributed shared memory
- KV-cache transfer in disaggregated inference
- Log streaming (receiver polls a tail counter)
- Cache eviction propagation

**WRITE_WITH_IMM** is a variant that also includes a 32-bit "immediate" delivered in a CQE on the receiver side — combining one-sided data placement with a notification.

### 9.3 One-Sided: READ

Sender pulls data from a remote registered memory region.

```c
struct ibv_send_wr swr = { .opcode = IBV_WR_RDMA_READ, /* ... */ };
ibv_post_send(qp, &swr, NULL);
```

Read latency is 2x WRITE (request flight + response flight), so for the same payload sizes WRITE is faster. But READ is sometimes the right semantic — "what is the current value at address X?"

### 9.4 ATOMIC: FetchAdd / CmpSwap

8-byte atomic operations on remote memory:

```c
// FetchAdd: atomically fetch *remote and add value
struct ibv_send_wr swr = {
    .opcode = IBV_WR_ATOMIC_FETCH_AND_ADD,
    .wr.atomic.remote_addr = addr,
    .wr.atomic.rkey = rkey,
    .wr.atomic.compare_add = value_to_add,
    // ...
};

// CmpSwap: atomically compare and swap *remote
swr.opcode = IBV_WR_ATOMIC_CMP_AND_SWP;
swr.wr.atomic.compare_add = expected;
swr.wr.atomic.swap = new_value;
```

**On most NICs, atomics are slow.** They take a separate path inside the NIC (vs the bulk WRITE/READ engine) and may serialize across the link. Throughput: ~1-10 M ops/s vs ~100 M ops/s for WRITEs. Used in lock-free distributed shared memory (FaRM, RAMCloud, LITE) carefully.

ConnectX-6/7 supports **enhanced atomics** with better throughput, but they're still not free.

### 9.5 Signaled vs Unsignaled Completions

When you post a SEND with `IBV_SEND_SIGNALED`, the NIC generates a CQE when the WR completes. The CQE consumes a CQ slot and a poll cycle.

For batching, you can post many WRs **unsignaled** (`send_flags = 0`) and only signal every Nth. The signaled CQE is a "synchronization point" — it confirms all prior unsignaled WRs also completed. Reduces CQ pressure by N×.

Modern apps signal every 16-64 WRs.

### 9.6 CQ Moderation

To reduce interrupt rate (or polling overhead), the NIC can batch CQEs:
- **CQ events** (interrupt-driven): NIC generates an interrupt only after N CQEs or T µs.
- **CQ polling**: App busy-polls; CQ moderation determines how often new CQEs are visible.

Tune via `ibv_modify_cq()` with `cq_count` (CQEs per moderation) and `cq_period` (max µs between).

### 9.7 Memory Region Cost

Each `ibv_reg_mr()`:
1. Pins all pages of the region (`get_user_pages()`)
2. Builds NIC translation tables (Memory Translation Table, MTT) — one entry per 4 KB page
3. Programs the IOMMU (if active)
4. Returns lkey + rkey (32-bit each)

For a 1 GB MR with 4 KB pages: 256K MTT entries; takes 10-50 ms to register. **At scale, never register in the hot path** — pre-register all working memory at init.

**ODP (On-Demand Paging):** Replaces pinning with page faults. Configure with `IBV_ACCESS_ON_DEMAND`. NIC issues PCIe ATS request to get translation; on TLB miss, the IOMMU walks the page table; on actual page fault, the NIC issues PRI (Page Request Interface) to the OS, which faults the page in. Fault latency: ~5-10 µs. Use for **sparse access** on large MRs (e.g., 1 TB sparse data); avoid for dense streaming.

### 9.8 RNR NAK and Retry Tuning

When the receiver doesn't have a posted RECV (or its RX buffer is exhausted), it sends an **RNR NAK**. The sender then waits `min_rnr_timer` (default 0 = 655 ms!) and retries. Default 7 retries.

Misconfigured RNR causes 4-second connection stalls. Always set `min_rnr_timer = 12` (640 µs) or so, `rnr_retry = 7` or `IBV_QP_INFINITE_RNR_RETRY` (poll forever).

Network-loss retries are governed by `retry_cnt` (default 7) and `timeout` (default 14 = ~67 ms). Tune lower (8 ms) for low-latency apps.

### 9.9 DCT (Dynamic Connected Transport) for Scale

Each RC QP holds full state (~ several KB of NIC SRAM per QP). With N peers, you need N-1 QPs per process. At 10,000 processes, that's 10⁸ QPs total in the cluster — blowing NIC QP-context memory and consuming hundreds of MB of host pages.

**DCT** keeps a small pool of "DC initiator" and "DC target" QPs on each NIC. When you want to send to a new peer, you don't allocate a new QP — you reuse an existing initiator QP, providing the target's DCT key + GID in the WR. The NIC dynamically re-targets the QP.

Trade-off: DCT has slightly higher per-message latency (additional state setup on first message to a new peer), but at scale it's the only way. UCX with the `dc` transport uses this by default.

---

## 10. Lossless Fabric Tuning

PFC + ECN tuning is dark magic. The fundamentals:

### 10.1 PFC Headroom

When a switch receives a PFC PAUSE from downstream, it must buffer all packets in flight on the wire + already-decoded-in-NIC packets, until the PAUSE clears. The minimum headroom buffer is:

```
   PFC_headroom = (max_packet_size) + (cable_RTT × link_rate / 8)
```

- For a 100m DAC link at 100 Gbps: `RTT ≈ 1 µs` → headroom ≈ 12.5 KB + 1500B MTU = ~14 KB.
- For a 200m fiber at 400 Gbps: `RTT ≈ 2 µs` → ~100 KB.

Per-port, per-priority. Hash this across 8 priorities × 64 ports × 400 Gbps: a modern switch needs ~50-200 MB of buffer just for PFC headroom.

### 10.2 ECN Watermarks (Kmin, Kmax)

Switch marks ECN-CE on packets when queue length > Kmin (probability ramps from 0 at Kmin to 100% at Kmax). DCQCN at the endpoint then throttles based on ECN.

Rule of thumb:
- `Kmin = 10-15%` of buffer
- `Kmax = 50-80%` of buffer

The relationship `(Kmax - Kmin) / link_rate` defines the ECN sensitivity. Smaller window → faster reaction, more transient throughput loss. Larger → slower reaction, queueing latency rises.

Microsoft DCQCN paper documents typical Azure settings: Kmin = 5 MB, Kmax = 100 MB on 100 Gbps Mellanox switches.

### 10.3 Buffer Architecture

- **Cut-through**: Switch starts forwarding a packet as soon as the header is parsed (typically 96-128 bytes in). Lower latency (~few hundred ns). Used by IB, modern Ethernet HPC switches.
- **Store-and-forward**: Switch buffers the entire packet, validates FCS, then forwards. Higher latency (depends on MTU). Used historically by some Ethernet for FCS check; modern switches usually do cut-through.

- **Shared buffer**: All ports / priorities share one big buffer pool, partitioned dynamically.
- **Dedicated buffer**: Each port / priority has a fixed slice.

Most modern switches (Broadcom Trident/Tomahawk, Cisco Silicon One, Mellanox Spectrum) use shared buffer with dynamic allocation; works well for bursty AI workloads.

### 10.4 PFC Pause Storms and Deadlock

**Pause storms:** Receiver pauses sender → sender's switch buffer fills → it pauses its own ingress → propagates back upstream. Single congested receiver can stall an entire pod.

**Deadlock:** Cyclic dependency where switch A pauses B, which is waiting on C, which is waiting on A. Real example: a fault on one server creates an unending PAUSE on its ingress; that PAUSE propagates back; the chain becomes a deadlocked cycle.

**Mitigations:**
- **PFC watchdog**: Detect a port that's been PAUSEd for too long (>200 ms typically), drop packets on that priority/port until it clears.
- **Reduce reliance on PFC**: Use IRN-style selective retransmit, or run the lossy fabric design (UEC).

The Guo et al. SIGCOMM 2016 "RDMA over Commodity Ethernet at Scale" paper from Microsoft is the canonical reference on PFC pain.

### 10.5 Victim Flows

When PFC fires for one congested flow, it pauses **the entire priority** — all flows in that priority class stop. Innocent flows get caught in the pause; they're "victim flows." Mitigations:
- Run latency-sensitive flows in a separate, less-congested priority
- Use multi-queue + per-flow scheduling (BlueField DPU)
- Move to lossy + IRN / UEC

---

## 11. Tail Latency Pathology

### 11.1 Incast — The Synchronized Many-to-One Pattern

Classic pattern: N senders simultaneously respond to one receiver's request (typical of MapReduce, distributed indexes, AllReduce). N senders × M bytes each → MN bytes arrive at the receiver's switch port near-instantaneously. The receiver's egress port buffer overflows. PFC fires; or worse, packets are dropped and we go to slow TCP timeouts (Linux RTO_min = 200 ms).

**Solutions:**
- **DCTCP/DCQCN**: ECN signaling spreads the burst over time.
- **Smaller request granularity**: Send only 64 KB chunks, not 1 MB.
- **Application-level rate limiting**: HDFS uses staggered requests.
- **Aggregator pattern**: Tree-reduce instead of flat-reduce.

### 11.2 Microbursts

Bursts of 10s-100s of packets arriving in < 1 ms — too short for ECN to react before buffer overflow. Caused by NIC GSO (Generic Segmentation Offload) batching, MPI message bursts, etc.

**Solutions:** Deep switch buffers, faster ECN reaction (lower Kmin), pacing at the sender NIC (Linux TSO + pacing, or NIC-level pacing in ConnectX-6+).

### 11.3 ECMP Hash Collisions

Traditional 5-tuple ECMP picks an output path by hashing (src_IP, dst_IP, src_port, dst_port, proto). If many flows happen to hash to the same uplink, you get **load imbalance** — half your spine links idle while others congest.

**Solutions:**
- **Adaptive routing** (Slingshot, IB Quantum-2): switch picks output based on actual link utilization
- **Per-packet ECMP / packet spraying** (UEC): every packet of a flow can take a different path; reorder at the endpoint
- **WCMP** (weighted ECMP) where multiple flows are explicitly distributed
- **Symmetric hashing** + multi-channel transport (UCX, NCCL channels): give each flow N "sub-flows" with different ports, spreading more uniformly

### 11.4 Head-of-Line Blocking

A single slow flow blocks others behind it in the same virtual channel / queue. IB uses **service levels** (SL) + **virtual lanes** (VL) to separate flows — each VL has its own buffer and PAUSE state. Up to 16 VLs per port. SL-to-VL mapping is configurable.

### 11.5 Pause Propagation

Already discussed in §10.4. Practical advice: monitor `mlx5_xdp_redirect_drop`, `tx_pause`, `rx_pause` counters via `ethtool -S`. Pause time > a few hundred ms per port = serious issue.

### 11.6 Solutions Summary

| Pathology | Adaptive routing | Packet spraying | Receiver-driven CC | DCQCN tuning |
|---|---|---|---|---|
| Incast | partial | yes | best | partial |
| Microbursts | yes | yes | partial | partial (slow) |
| ECMP collisions | best | best | n/a | n/a |
| HoL blocking | partial | yes | partial | n/a |
| Pause storms | n/a | n/a (no PFC) | best | n/a |

---

## 12. Topology-Aware Collective Scheduling

### 12.1 Rail-Optimized AllReduce

In a rail-optimized network (NIC i on every node connects to leaf-i), an AllReduce across N nodes uses the **same rank-i NIC on every node** for all communication. The traffic stays entirely within "rail i" — never crosses rails. Benefits:
- No cross-rail congestion
- ECMP hash collisions impossible (one path per rail)
- Failures in rail j don't impact rail i

NCCL detects rail topology via PCIe + IP/IB device info; set `NCCL_IB_HCA=mlx5_0,mlx5_1,...` per rail.

### 12.2 NCCL Channels and Hierarchical Reductions

For an AllReduce across 1024 nodes × 8 GPUs/node:
1. **Intra-node reduce**: 8 GPUs per node reduce locally over NVLink (fast)
2. **Inter-node ring/tree**: 1024 logical reducers across nodes via IB
3. **Intra-node broadcast**: Result distributed back to 8 GPUs via NVLink

This 2-level hierarchy uses NVLink's massive BW for the 8-way reduction (cheap) and saves IB bandwidth for the 1024-way step (expensive). NCCL does this automatically.

### 12.3 SHARP / NVLS — In-Network Reduction

**SHARP (Scalable Hierarchical Aggregation and Reduction Protocol)** is Mellanox/NVIDIA's switch-side reduction for InfiniBand. The IB switch contains reduction ALUs that combine packets from N children into one output (sum, max, min, AND, OR, etc.). Used during AllReduce, gradient aggregation.

Bandwidth model for ring AllReduce: `2(N-1)/N × M/B`. With SHARP/NVLS in-network reduction: `M/B + α log N`. For N=72 (NVL72), M=64 GB, B=900 GB/s (per-rank NVLink BW):
- Ring: 2 × 71/72 × 64 GB / 900 GB/s ≈ 140 ms
- NVLS: 64 GB / 900 GB/s + α log 72 ≈ 71 ms + tiny α

Halves AllReduce time. SHARP v3 and NVLS support multiple data types (FP16, BF16, FP32) and multiple ops; collectives are pre-compiled into switch flow tables.

---

## 13. Cache Coherence on Fabric

### 13.1 MESI / MOESI / MESIF Refresher

(Covered in [superscalar_ooo_cpu.md](@/notes/hardware/superscalar_ooo_cpu.md) §10.) Briefly:

- **MESI** (Modified, Exclusive, Shared, Invalid): Default x86 / Arm coherence.
- **MOESI** (adds Owned): AMD. The "Owned" state lets a dirty line be shared without writeback to memory — Owner is responsible for supplying data on miss.
- **MESIF** (adds Forward): Intel. Designates a single "Forward" copy that supplies on miss; eliminates redundant supplies.

### 13.2 Snoop Filter vs Directory

- **Snoop filter (small directory)**: A small inclusive cache at the LLC remembers which lines might be present in any L1/L2 in the system. Eliminates broadcast snoops to coherent agents that obviously don't have the line. Used on Intel/AMD CCDs.
- **Directory (full)**: Per cache line, store a bitmap of which agents have a copy. Scales to thousands of agents (used in IBM POWER, large NUMA, CXL fabric mode). Memory overhead: O(N_agents bits per line).

In CXL 3.0 fabric mode, the directory is **distributed** — each home agent (CXL switch tier) tracks the lines it owns. Snooping is targeted, not broadcast.

### 13.3 CXL.cache HDM-DB (Back-Invalidation)

In CXL 2.0 / HDM-H (host-managed coherence), the device's caches are tracked by the host. The host must allocate directory space proportional to total device cache.

In CXL 3.0 HDM-DB, the device tracks its own caches. When the device wants to write a line, it issues a **Back-Invalidation (BI)** to evict any cached copies on the host:

```
Host CPU                       CXL Type-2 Device
   │                                  │
   │  LD address X (.mem read) ──────►│  (line cached on host LLC)
   │                                  │
   │                            (device wants to write X)
   │ ◄────────────── BI: invalidate X │
   │ (drops host cached copy)         │
   │ ───────────── BI Ack ───────────►│
   │                                  │
   │                            (device writes X)
   │                                  │
   │  LD address X (.mem read) ──────►│  (returns new value)
```

This pattern works for any Type-2 device or fabric-attached coherent memory pool. Critical for CXL 3.0 disaggregated coherent memory pools (GFAM): the memory device tracks all host caches; hosts only react to BI messages.

---

## 14. Bandwidth Math, Bisection BW, Oversubscription

### 14.1 Bisection Bandwidth — Definition

**Bisection BW** of a network = minimum bandwidth across any cut that divides the network into two halves of equal size. It's the worst-case bandwidth for "half the nodes talk to the other half" patterns (which is what AllToAll, ring AllReduce on a partition, etc. require).

For a non-blocking Clos network with N leaves, S spines, and k leaf uplinks: bisection BW = N × k × link_rate / 2 (half of all uplinks cross any cut). A "full bisection" fat tree has total uplink BW = downlink BW at every tier.

### 14.2 Oversubscription Ratios

Most production datacenter networks are **oversubscribed**: leaf has fewer uplink BW than downlink BW. Common ratios:

| Ratio | Use case |
|---|---|
| 1:1 (full bisection) | AI training, HPC |
| 2:1 | High-end DC, latency-sensitive |
| 3:1 | Cost-balanced DC (typical) |
| 4:1 - 8:1 | Cost-optimized, web-tier traffic |

A 3:1 oversubscription means cross-rack traffic gets 1/3 the bandwidth of intra-rack. AllReduce hitting that boundary suffers; rack-locality of training jobs is critical.

### 14.3 AllReduce Time Models

For a ring AllReduce on N nodes with message size M and per-node BW B:
```
   T_ring = 2(N-1)/N × M/B + (2N-2) × α
```
where α is per-hop latency. For large M, dominated by the bandwidth term ≈ 2 × M/B.

For a tree AllReduce (log N depth):
```
   T_tree = 2 log_2(N) × (M/B + α)
```
Dominated by latency for large N; better for small messages.

For SHARP / NVLS in-network reduction:
```
   T_sharp = M/B + α × log_2(N)
```
Approximately **half** the ring time at large M. Latency tier dominates for small M.

### 14.4 Clos Network Formula

For a 3-tier Clos with k-port switches at every tier:
- Tier-1 (leaf): k servers down, k uplinks up
- Tier-2 (spine): k leaves down, k cores up
- Tier-3 (core): k spines down
- Total servers: k³ / 4 ... but design typically uses k/2 servers per leaf

A k=64 Clos supports k³/4 = 65536 servers at full bisection. Beyond this, you go to 5-tier (super-spine), or you go to dragonfly.

---

## 15. Power and Cost at Scale

### 15.1 Power Trends — W/Gbps

Per-Gbps power has dropped 100× over 25 years but is now plateauing. Approximate W/Gbps for switching:

| Year | Speed | W/Gbps (typical) |
|---|---|---|
| 2010 | 10 GbE | ~10 W |
| 2015 | 25 GbE | ~3 W |
| 2020 | 100 GbE | ~1 W |
| 2024 | 400 GbE | ~0.3-0.5 W |
| 2025 | 800 GbE | ~0.2-0.3 W |
| 2026 (projected) | 1.6 TbE | ~0.15-0.2 W (electrical), ~0.08-0.12 W (CPO) |

**The electrical-to-optical crossover**: Around 1.6 Tbps per port, the SerDes power required to drive electrical signals from switch ASIC across PCB to pluggable optics (a few dozen cm of board) becomes comparable to the optical engine power itself. Beyond that, CPO is cheaper and lower-power. This is why hyperscalers are aggressively pursuing CPO for 1.6T+.

### 15.2 Cost — $/Port and Cable Types

Approximate 2025 list pricing (often discounted 50%+ in volume):

| Optic / Cable | Reach | List $/port |
|---|---|---|
| 100G DAC (copper) | < 3 m | $80-150 |
| 100G AOC | 3-30 m | $300-500 |
| 100G SR4 (multi-mode) | 100 m | $400-700 |
| 100G LR4 (single-mode) | 10 km | $1000-2000 |
| 400G DAC | < 2 m | $300-500 |
| 400G AOC | 5-30 m | $800-1500 |
| 400G DR4 (single-mode) | 500 m | $1500-3000 |
| 400G FR4 / LR4 | 2-10 km | $3000-7000 |
| 400ZR | 80-120 km | $5000-10000 |
| 800G AOC | 5-30 m | $1500-3000 |
| 800G DR8 | 500 m | $3000-6000 |
| 800G ZR | 80-120 km | $10000-20000 |
| 1.6T DR8 | 500 m | $5000-12000 (early 2026 pricing) |

**DAC** = Direct Attach Cable (copper, < 3 m, cheapest, lowest latency).
**AOC** = Active Optical Cable (optics permanently embedded in cable, plug-and-play, but fixed length).
**SR / DR / FR / LR / ZR** = single-mode / multi-mode reach grades. SR = short reach (multi-mode, 100 m). DR = data center reach (single-mode, 500 m). FR = far reach (2 km). LR = long reach (10 km). ZR = ZR plug. ER = extended (40 km).

For a 1024-GPU cluster, optical interconnect can easily account for 15-30% of total system cost. CPO is projected to cut this in half by 2027.

### 15.3 CPO Necessity for >1.6T

At 1.6 TbE per port, PCB trace loss + connector reflections at 200 Gbaud PAM4 become severe enough that signal integrity requires either:
1. Very short PCB traces (< 10 cm)
2. Retimers (which consume power and add latency)
3. Co-packaged optics (eliminating PCB entirely beyond the package)

Hyperscalers (Google, Microsoft, Meta) have all committed to CPO for >800G/port deployments by 2027.

---

## 16. Security

### 16.1 MACsec (802.1AE)

**MACsec** is L2 hop-by-hop encryption — every Ethernet frame is encrypted on the wire and decrypted at the next hop. Uses AES-128-GCM or AES-256-GCM. Now standard at line rate on most enterprise/DC NICs and switches.

- **Negotiated via MKA** (MACsec Key Agreement, 802.1X) or static keys
- Latency: < 100 ns added per hop
- Throughput: line-rate on modern NICs (BlueField, ConnectX-7+)

Use cases: DCI links (inter-rack, inter-DC), regulated workloads, zero-trust networks. Microsoft requires MACsec on Azure DCI for FedRAMP.

### 16.2 IPsec

L3 encryption with ESP (Encapsulating Security Payload). Modern NICs (BlueField-3, AWS Nitro) offload IPsec in hardware at line rate. Used for cross-region VPCs, hybrid cloud, WAN.

### 16.3 InfiniBand P_Keys, Q_Keys

- **P_Key** (16-bit Partition Key): IB equivalent of VLAN. Switch checks P_Key on every packet; mismatched packets dropped. Configured by OpenSM.
- **Q_Key** (32-bit Queue Key): Per-QP access token for UD QPs. Sender includes Q_Key in WR; receiver QP verifies match. Used to gate UD multicast.

P_Keys provide coarse multi-tenant isolation but not cryptographic security (no encryption).

### 16.4 CXL IDE and TDISP

**CXL IDE (Integrity and Data Encryption):** Per-FLIT AES-GCM encryption on CXL links. Selectable per virtual channel. Adds ~3 ns latency. Mandatory for confidential CXL deployments.

**TDISP (TEE Device Interface Security Protocol):** PCIe spec adopted by CXL. Lets a confidential VM (Intel TDX, AMD SEV-SNP, ARM CCA Realm) cryptographically verify that a CXL device is:
1. Genuine (DICE-attested)
2. In a "trusted" state (firmware verified, no debug mode)
3. Owned exclusively by this TEE (not shared)

After TDISP attestation, the device's MMIO and DMA regions are protected by the IOMMU + memory encryption — invisible to host kernel. Required for cloud-confidential AI workloads (host operator cannot snoop tenant GPU/CXL traffic).

---

## 17. Mental Models — Decision Framework

### 17.1 Workload-by-Scale Decision Table

| Workload | Scale | Best Fabric | Why |
|---|---|---|---|
| AI training (8 GPUs) | < 1 server | NVLink (intra-server) | NVSwitch BW |
| AI training (32-72 GPUs) | < 1 rack | NVLink + NVSwitch (NVL72) | Single coherent domain |
| AI training (100-10k GPUs) | < 1 cluster | InfiniBand NDR/XDR or RoCE+UEC | Bandwidth + low tail |
| LLM inference (single node) | 1 server | NVLink + GDR over PCIe | KV cache locality |
| Disaggregated inference | 10-100s nodes | RoCE + NIXL | KV cache transfer |
| HPC (CFD, weather, MD) | 1k-100k nodes | Slingshot / IB / TofuD | Low-latency, dragonfly/torus |
| OLTP database | 10-100 nodes | RoCE or TCP/IP | Standard DC fabric is fine |
| OLAP / lakehouse | 100-10k nodes | RoCE or TCP/IP + NVMe-oF | Disk-IO-bound; RDMA storage |
| Memory pool (CXL) | 1 rack | CXL 3.x fabric | Coherent shared memory |
| Storage (NVMe-oF) | 10-1000 servers | RoCE or TCP/IP | Mature NVMe-oF |
| Distributed KV (FoundationDB, Aurora) | 100-1000 nodes | RoCE or TCP/IP | LSN-ordered, latency-tolerant |
| Web tier | 100-100k servers | Standard Ethernet + TCP/QUIC | Mature, cheapest |

### 17.2 Latency / BW / Cost Tradeoff Matrix

| Fabric | Latency | BW | Cost ($/port) | Lossless? | Vendor lock |
|---|---|---|---|---|---|
| PCIe 5/6 | 100-200 ns | 64-128 GB/s | included on motherboard | n/a | none |
| NVLink5/NVSwitch | 100-500 ns | 1.8 TB/s | embedded in GPU | yes | NVIDIA |
| UCIe (chiplet) | 5-10 ns | 1-4 TB/s | bump area | yes | open (consortium) |
| CXL 3.x | 100-300 ns | 64-128 GB/s | $500-2000 (cable) | yes | open (consortium) |
| InfiniBand NDR | 1-3 µs | 400 Gb/s | $2000-5000 | yes | NVIDIA (Mellanox) |
| RoCEv2 | 2-5 µs | 100-400 Gb/s | $1000-3000 | yes (with PFC) | open |
| UEC (Ultra Ethernet) | 2-5 µs | 100-1600 Gb/s | $1000-3000 | no (lossy, OK) | open (consortium) |
| Slingshot 12 | 2-5 µs | 400 Gb/s | $5000-10000 | partial | HPE |
| Standard Ethernet + TCP | 10-30 µs | 25-800 Gb/s | $200-2000 | no | open |

---

## 18. Practical Skills — Commands and Benchmarks

### 18.1 Topology Discovery

```bash
# PCIe tree
lspci -tvvv                 # Tree view of PCI bus
lspci -vv                   # Verbose per-device (BARs, capabilities, AER)
lspci -nn | grep -i mell    # Find Mellanox / NVIDIA NICs

# Hardware topology (NUMA + PCIe + cores)
lstopo                      # Graphical (PDF/PNG output)
lstopo --of console         # Text
hwloc-ls                    # Same as lstopo --of console
hwloc-distrib 8             # Suggest CPU set for 8-way parallelism

# NUMA placement
cat /sys/bus/pci/devices/0000:01:00.0/numa_node     # NIC's NUMA node
numactl -H                                           # NUMA topology
```

### 18.2 InfiniBand Inspection

```bash
ibstat                      # Per-HCA status (LID, state, port speeds)
ibv_devinfo -v              # Verbose verbs device info
ibportstate 1 1             # Port state for HCA 1, port 1
iblinkinfo                  # All links + remote endpoint
ibhosts                     # Discover all HCAs
ibroute                     # Per-switch routing table
saquery -t Node             # Subnet Admin query: list all nodes
```

### 18.3 RDMA Benchmarks (perftest)

```bash
# Server
ib_send_bw -d mlx5_0                    # Send bandwidth
ib_send_lat -d mlx5_0                   # Send latency
ib_write_bw -d mlx5_0 --report_gbits    # RDMA write bandwidth, Gbit/s
ib_write_lat -d mlx5_0                  # RDMA write latency
ib_read_lat -d mlx5_0                   # RDMA read latency
ib_atomic_lat -d mlx5_0                 # Atomic op latency

# Client (other side)
ib_write_bw -d mlx5_0 -q 4 -x 3 server_ip --report_gbits
#   -q 4 : 4 QPs (parallel)
#   -x 3 : GID index (RoCEv2)
#   -F   : skip CPU frequency check (recommended on shared nodes)
```

Expected on a tuned ConnectX-7 NDR (400 Gb/s):
- `ib_send_lat`: ~1.0-1.2 µs
- `ib_write_bw`: ~390-395 Gb/s
- `ib_read_lat`: ~1.5-2 µs (round-trip cost)

### 18.4 NIC Tuning

```bash
# Driver info
ethtool -i eth0                         # Driver, version, firmware

# Ring buffer sizes
ethtool -g eth0                         # Current + max ring sizes
ethtool -G eth0 rx 8192 tx 8192         # Set ring sizes

# Queue count
ethtool -l eth0                         # Current + max queues
ethtool -L eth0 combined 32             # Set 32 combined queues

# Coalesce (interrupt moderation)
ethtool -c eth0                         # Current
ethtool -C eth0 rx-usecs 16 tx-usecs 16 # Per 16 µs or N pkts

# Offloads
ethtool -k eth0                         # Current offloads
ethtool -K eth0 tx-checksumming on rx-checksumming on tso on lro on

# Mellanox-specific: DCB / RoCE
mlnx_qos -i eth0                        # Show DCB config
mlnx_qos -i eth0 --pfc 0,0,0,1,0,0,0,0  # PFC on priority 3 only
mlnx_qos -i eth0 --trust dscp           # Use DSCP for priority (vs PCP)

# Mellanox firmware tools
mst start                               # Bring up mstflint device tree
mlxconfig -d /dev/mst/mt4119_pciconf0 q # Query firmware config
mlxlink -d mlx5_0 -p 1                  # Port-level link info: speed, FEC, errors
mlxlink -d mlx5_0 -p 1 --rx_fec_active  # RX FEC mode
```

### 18.5 NCCL Diagnostics

```bash
# Run NCCL-tests
git clone https://github.com/NVIDIA/nccl-tests
cd nccl-tests && make MPI=1 CUDA_HOME=/usr/local/cuda
mpirun -np 8 ./build/all_reduce_perf -b 8 -e 8G -f 2 -g 1

# Debug logging
NCCL_DEBUG=INFO NCCL_DEBUG_SUBSYS=INIT,COLL,P2P,NET mpirun ...

# Topology dump
NCCL_TOPO_DUMP_FILE=/tmp/topo.xml mpirun ...

# Force specific transport
NCCL_NET_GDR_LEVEL=PHB mpirun ...       # GPU-Direct RDMA only same PHB
NCCL_IB_HCA=mlx5_0,mlx5_1 mpirun ...    # Use only these HCAs (rail-aware)
NCCL_IB_GID_INDEX=3 mpirun ...          # RoCEv2 GID
NCCL_P2P_DISABLE=1 mpirun ...           # Disable NVLink (debug)
NCCL_ALGO=Tree mpirun ...               # Force tree AllReduce
NCCL_COLLNET_ENABLE=1 mpirun ...        # Enable SHARP
```

### 18.6 CXL

```bash
cxl list                                # List CXL devices
cxl list -v                             # Verbose: capacity, partitions
cxl create-region -d decoder0.0 -m mem0 # Create memory region
cxl reconfigure-system                  # After topology change
daxctl list                             # DAX devices (CXL memory exposed as devdax)
daxctl reconfigure-device dax0.0 -m system-ram  # Make CXL mem into NUMA node
ndctl list                              # PMEM/NVDIMM (parallel structure)
```

### 18.7 PCIe Performance Counters

```bash
# Uncore PMU events (Intel; Sapphire Rapids+ has iio_*)
perf stat -e uncore_iio_0/event=0x83,umask=0x04/ ...   # IIO inbound bytes
perf stat -e uncore_iio_*/event=0x83/ ...              # All IIO devices

# Intel PCM (Performance Counter Monitor)
pcm                                      # Live CPU/memory/PCIe view
pcm-pcie                                 # Per-device PCIe BW

# AER errors
lspci -vv | grep -i -A 5 "Advanced Error"

# PCIe link speed/width (current vs max)
lspci -vv -s 0000:01:00.0 | grep -i "lnksta\|lnkcap"
```

### 18.8 Standard Benchmarks

| Benchmark | What it measures | Command |
|---|---|---|
| **OSU** | MPI point-to-point + collectives | `osu_latency`, `osu_bw`, `osu_allreduce` |
| **NCCL-tests** | NCCL GPU collectives | `all_reduce_perf`, `all_gather_perf` |
| **iperf3** | TCP/UDP bandwidth | `iperf3 -s` / `iperf3 -c server -P 16` |
| **netperf** | Latency + throughput | `netperf -H server -t TCP_RR` |
| **fio** | Storage + NVMe-oF IOPS | `fio --rw=randread --bs=4k --iodepth=64 ...` |
| **MLPerf Training/Inference** | End-to-end AI workload (NCCL component) | as per MLPerf rules |
| **HPCG** | HPC sparse | `xhpcg` |
| **HPL (LINPACK)** | HPC dense matrix | `xhpl` |

---

## 19. Further Reading

### 19.1 Datacenter Networking and RDMA

Citations grouped by topic. Conference codes: SIGCOMM = ACM SIGCOMM, NSDI = USENIX Networked Systems Design and Implementation, SOSP = ACM Symposium on Operating Systems Principles, OSDI = USENIX Operating Systems Design and Implementation.

- Alizadeh, Greenberg, Maltz, Padhye, Patel, Prabhakar, Sengupta, Sridharan. "Data Center TCP (DCTCP)." SIGCOMM 2010.
- Zhu, Eran, Firestone, Guo, Lipshteyn, Liron, Padhye, Raindel, Yahia, Zhang. "Congestion Control for Large-Scale RDMA Deployments" (DCQCN). SIGCOMM 2015.
- Mittal, Lam, Dukkipati, Blem, Wassel, Ghobadi, Vahdat, Wang, Wetherall, Zats. "TIMELY: RTT-based Congestion Control for the Datacenter." SIGCOMM 2015.
- Li, Miao, Liu, Zhou, Sridharan, Kumar, Bao, Zhou, Yang, Tewari. "HPCC: High Precision Congestion Control." SIGCOMM 2019.
- Kumar, Dukkipati, Jouppi, Lam, Madhavan, Mittal, Mittal, Wassel, Wetherall, Wu, Yang, Zats. "Swift: Delay is Simple and Effective for Congestion Control in the Datacenter." SIGCOMM 2020.
- Addanki, Apostolaki, Ghobadi, Schmid, Vanbever. "PowerTCP: Pushing the Performance Limits of Datacenter Networks." NSDI 2022.
- Olteanu, Agache, Voinescu, Raiciu. "An Edge-Queued Datagram Service for All Datacenter Traffic" (EQDS). NSDI 2022.
- Mittal, Shpiner, Panda, Zahavi, Krishnamurthy, Ratnasamy, Shenker. "Revisiting Network Support for RDMA" (IRN). SIGCOMM 2018.
- Stephens, Akella, Swift. "Loom: Flexible and Efficient NIC Packet Scheduling" / "Annulus." SIGCOMM 2019.
- Dragojević, Narayanan, Hodson, Castro. "FaRM: Fast Remote Memory." NSDI 2014.
- Guo, Wu, Deng, Liu, Haridas, Liu, Xu, Yu, Xiang, Wang, Yu, Zhang, Zhang, Padhye, Lipshteyn. "RDMA over Commodity Ethernet at Scale." SIGCOMM 2016.
- Singh, Ong, Agarwal, Anderson, Armistead, Bannon, Boving, Desai, Felderman, Germano, Kanagala, Provost, Simmons, Tanda, Wanderer, Hölzle, Stuart, Vahdat. "Jupiter Rising: A Decade of Clos Topologies and Centralized Control in Google's Datacenter Network." SIGCOMM 2015.
- Poutievski, Mashayekhi, Ong, Singhvi, Tariq, Tariq, Vahdat, Wanderer. "Jupiter Evolving: Transforming Google's Datacenter Network via Optical Circuit Switches and Software-Defined Networking." SIGCOMM 2022.
- Gibson, Hartl, Wlodarczyk, Vahdat, Mogul, Goldberg, Sjödin, Sosa, Yang, Singh. "Aquila: A Unified, Low-Latency Fabric for Datacenter Networks." NSDI 2022.
- Bansal, Khan, Goyal et al. "Meta's RoCE Networks: Building, Operating, and Lessons Learned." SIGCOMM 2023.
- Mellette, McGuinness, Roy, Forencich, Papen, Snoeren, Porter. "RotorNet: A Scalable, Low-Complexity, Optical Datacenter Network." SIGCOMM 2017.

### 19.2 HPC Fabrics and Optical Networks

- De Sensi, Di Girolamo, McMahon, Roweth, Hoefler. "An In-Depth Analysis of the Slingshot Interconnect." SC 2020.
- Ajima, Inoue, Hiramoto, Takagi, Shimizu. "The Tofu Interconnect D." 2018 (Fugaku).
- Alverson, Roweth, Kaplan. "The Gemini System Interconnect." Hot Interconnects 2010.
- Faanes, Bataineh, Roweth, Court, Froese, Alverson, Johnson, Kopnick, Higgins, Reinhard. "Cray Cascade: A Scalable HPC System Based on a Dragonfly Network" (Aries). SC 2012.
- Shaw, Adams, Azaria, Bank, Batson, Bell, Bergdorf, Bhatt, Butts, Correia, Dirks, Dror, Eastwood, Edwards, Even, Feldmann, Fenn, Fenton, Forte, Gagliardo, Gill, Gorlatova, Greskamp, Grossman, Gullingsrud, Hibbard, Ho, Ierardi, Iserovich, Klepeis, Kuskin, Larson, Layman, Lee, Lerer, Li, Lindorff-Larsen, Maragakis, Mraz, Murphy, Piana, Predescu, Priest, Rendleman, Rosenberg, Salmon, Schafer, Schwink, Shan, Shrayer, Sjostedt, Smith, Spengler, Stuart, Theobald, Towles, Wang, Young. "Anton 2: Raising the Bar for Performance and Programmability in a Special-Purpose Molecular Dynamics Supercomputer." SC 2014.
- Liu, Theogarajan, Pinheiro, Vahdat. "Apollo: A Sequencing-Based Approach to Reconfigurable Optical Networks." SIGCOMM 2021.
- Ballani, Costa, Behrendt, Cletheroe, Haller, Jozwik, Karinou, Lange, Shi, Thomsen, Williams. "Sirius: A Flat Datacenter Network with Nanosecond Optical Switching." SIGCOMM 2020.
- Mellette, Das, Guo, McGuinness, Snoeren, Porter, Papen. "Expanding Across Time to Deliver Bandwidth Efficiency and Low Latency" (Opera). NSDI 2020.
- Khani, Ghobadi, Alizadeh, Zhu, Glick, Bergman, Vahdat, Klenk, Ebrahimi. "SiP-ML: High-Bandwidth Optical Network Interconnects for Machine Learning Training." SIGCOMM 2021.

### 19.3 Standards and Specifications

- PCI-SIG. "PCI Express Base Specification Revision 7.0." 2025.
- Compute Express Link Consortium. "CXL 3.2 Specification." Dec 2024.
- UCIe Consortium. "UCIe 2.1 Specification." Aug 2025.
- Ultra Ethernet Consortium. "Ultra Ethernet Specification 1.0." Jun 2025.
- InfiniBand Trade Association. "InfiniBand Architecture Specification 1.7 (Volume 1)." 2023.
- IEEE 802.3df-2024. "Standard for Ethernet — 200/400/800 Gb/s Operation." 2024.
- IEEE 802.3dj (draft). "1.6 Tb/s Operation." Project, ratification 2026.
- IEEE 802.1Qbb. "Priority-based Flow Control." 2011.
- IEEE 802.1Qaz. "Enhanced Transmission Selection." 2011.
- ARM. "AMBA AXI and ACE Protocol Specification." Issue G, 2021.
- ARM. "AMBA CHI Architecture Specification." Issue F, 2023.
- NVMe Express. "NVMe over Fabrics Specification 1.1a." 2023.

### 19.4 Books

- Dally, Towles. "Principles and Practices of Interconnection Networks." Morgan Kaufmann, 2003.
- Hennessy, Patterson. "Computer Architecture: A Quantitative Approach" (6th ed.). Morgan Kaufmann, 2017.
- Duato, Yalamanchili, Ni. "Interconnection Networks: An Engineering Approach." Morgan Kaufmann, 2003.

### 19.5 Talks, Blog Posts, Vendor Materials

- Microsoft Azure RDMA team (Bansal et al.) blog series 2023-2024 on RoCE at scale: deployment lessons.
- NVIDIA GTC keynotes (2022-2025) for NVLink, NVSwitch, NVL72, Quantum-X Photonics architecture announcements.
- Google Cloud research blogs on Apollo, Sirius, Jupiter, Aquila, Lightning.
- Meta Engineering blog on AI cluster networking (Llama 2/3 training infra), RoCE deployment.
- HPE Cray Slingshot Architecture Whitepaper, Cassini NIC datasheet.
- OpenFabrics Alliance workshops (annual): UCX, libfabric, OFI provider updates.
- SNIA tutorials on NVMe-oF, persistent memory, CXL.

---

*Cross-references: [pcie_internals.md](@/notes/hardware/pcie_internals.md), [superscalar_ooo_cpu.md](@/notes/hardware/superscalar_ooo_cpu.md), [gpu_tpu_accelerator_design.md](@/notes/hardware/gpu_tpu_accelerator_design.md), [disaggregated_storage.md](@/notes/database/disaggregated_storage.md), [vfio_internals.md](@/notes/os/vfio_internals.md), [io_uring_internals.md](@/notes/os/io_uring_internals.md), [isa_critical_instructions.md](@/notes/hardware/isa_critical_instructions.md).*
