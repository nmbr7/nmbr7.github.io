+++
title = "PCIE Internals"
date = 2026-03-27
[taxonomies]
tags = ["pcie", "pci", "bus", "interconnect", "dma", "virtualization", "drivers", "mmio", "tlp", "sr-iov"]
+++


# PCI Express Internals: Architecture, Protocols, and Implementation

A comprehensive implementation-level reference for PCI Express — covering architecture fundamentals, packet formats with bit-level detail, configuration space registers, capability structures, interrupt mechanisms, Linux kernel driver APIs, device emulation for VMMs, SR-IOV, passthrough, error handling, power management, and advanced topics (CXL, NVMe, P2P DMA, FLIT mode). Written for engineers implementing PCIe device emulation, writing PCIe drivers, or working with PCIe passthrough in virtualization.

---

## Table of Contents

1. [Evolution: ISA to PCI to PCIe](#1-evolution-isa-to-pci-to-pcie)
2. [PCIe Architecture Fundamentals](#2-pcie-architecture-fundamentals)
3. [Transaction Layer — TLPs](#3-transaction-layer-tlps)
4. [Data Link Layer — DLLPs](#4-data-link-layer-dllps)
5. [Physical Layer](#5-physical-layer)
6. [Configuration Space](#6-configuration-space)
7. [Base Address Registers (BARs)](#7-base-address-registers-bars)
8. [PCIe Capabilities](#8-pcie-capabilities)
9. [Interrupt Mechanisms](#9-interrupt-mechanisms)
10. [Enumeration and Resource Assignment](#10-enumeration-and-resource-assignment)
11. [PCIe in the Linux Kernel](#11-pcie-in-the-linux-kernel)
12. [PCIe Device Emulation for VMMs](#12-pcie-device-emulation-for-vmms)
13. [PCIe Passthrough and SR-IOV](#13-pcie-passthrough-and-sr-iov)
14. [Power Management](#14-power-management)
15. [Error Handling](#15-error-handling)
16. [Advanced Topics](#16-advanced-topics)
17. [Key References](#17-key-references)

---

## 1. Evolution: ISA to PCI to PCIe

### The Journey

```
ISA (1981)          PCI (1992)           PCI-X (1998)        PCIe 1.0 (2003)
 8/16-bit            32/64-bit            64-bit              Serial,
 parallel bus         parallel bus         parallel bus        point-to-point
 4.77-8.33 MHz       33/66 MHz            133 MHz             2.5 GT/s/lane
 ~8 MB/s              ~133/~533 MB/s       ~1064 MB/s          ~250 MB/s/lane
                                                               (x16 = 4 GB/s)
```

**ISA (Industry Standard Architecture), 1981**: Intel 8088/8086 bus. 8-bit data (later 16-bit in AT/EISA). Shared parallel bus with no auto-configuration — jumpers for IRQ and I/O port selection. No bus mastering. Maximum ~8 MB/s.

**PCI (Peripheral Component Interconnect), 1992**: Intel-designed replacement. 32-bit shared parallel bus at 33 MHz (132 MB/s), later 64-bit at 66 MHz (528 MB/s in PCI 2.1). Key innovation: **auto-configuration** via configuration space — BIOS/OS reads Vendor/Device IDs, allocates resources (BARs, IRQs) automatically. Supports bus mastering. Multiplexed address/data lines (AD[31:0] carry both address and data in different clock phases). Tree topology via PCI-to-PCI bridges.

**PCI-X, 1998**: Extended PCI to 133 MHz (1066 MB/s, 64-bit). Split transactions (separate request/response phases to free the bus). Still a shared parallel bus, so bandwidth shared among all devices.

**PCI Express 1.0, 2003**: The paradigm shift. Replaced the shared parallel bus with **serial point-to-point links** using differential signaling. Each direction has dedicated lanes — no arbitration, no bus contention. Packet-based protocol (transaction layer packets over a serial link). Software-compatible with PCI (same configuration space model), but electrically completely different.

### Why Serial Beats Parallel

The parallel bus problem at high frequencies:
- **Clock skew**: All 32/64 data lines must arrive within the same clock cycle. At 133 MHz (7.5 ns period), even trace length differences of a few mm cause bit errors.
- **Crosstalk**: Adjacent parallel traces couple electromagnetically, worsening at higher frequencies.
- **Stub loading**: Each device on a shared bus adds electrical load (stub), degrading signal quality.
- **Bus arbitration**: Only one master at a time — wasted bandwidth when devices wait.

PCIe solves all of these:
- Serial per-lane: only one differential pair per direction, no skew across data bits.
- Point-to-point: no bus sharing, no arbitration, full bandwidth per link.
- Embedded clock: 8b/10b or 128b/130b encoding carries clock in the data stream — no separate clock signal.
- Scalable: add lanes (x1, x2, x4, x8, x16, x32) for more bandwidth.

### PCIe Generations

```
Gen   Year   Data Rate    Encoding        BW/Lane       x16 BW        Signaling
─────────────────────────────────────────────────────────────────────────────────
1.0   2003   2.5 GT/s     8b/10b          250 MB/s      4 GB/s        NRZ
2.0   2007   5.0 GT/s     8b/10b          500 MB/s      8 GB/s        NRZ
3.0   2010   8.0 GT/s     128b/130b       ~985 MB/s     ~16 GB/s      NRZ
4.0   2017   16.0 GT/s    128b/130b       ~1969 MB/s    ~32 GB/s      NRZ
5.0   2019   32.0 GT/s    128b/130b       ~3938 MB/s    ~63 GB/s      NRZ
6.0   2022   64.0 GT/s    1b/1b (FLIT)    ~7877 MB/s    ~126 GB/s     PAM4
7.0   2025   128.0 GT/s   1b/1b (FLIT)    ~15754 MB/s   ~252 GB/s     PAM4
```

**8b/10b encoding** (Gen 1-2): Each 8-bit byte encoded as 10-bit symbol. Guarantees DC balance and sufficient transitions for clock recovery. 20% overhead (250 MB/s from 2.5 Gbit/s raw).

**128b/130b encoding** (Gen 3-5): 128 data bits + 2-bit sync header. Only ~1.5% overhead. Requires scrambling (LFSR-based) for DC balance instead of encoding overhead.

**PAM4 signaling** (Gen 6+): 4-level Pulse Amplitude Modulation. Each symbol carries 2 bits (vs NRZ's 1 bit). Doubles data rate at same baud rate, but requires Forward Error Correction (FEC) due to reduced noise margins between the 4 voltage levels. Gen 6 uses lightweight CRC-based FEC rather than heavy Reed-Solomon.

**FLIT mode** (Gen 6+): Fixed-Length Integrity-protected Transport. TLPs are packed into fixed 256-byte FLITs with integrated CRC. Eliminates per-TLP LCRC and ECRC overhead. FLITs can contain multiple small TLPs or fragments of large ones. Reduces protocol overhead from ~20% to ~4%.

---

## 2. PCIe Architecture Fundamentals

### Topology

PCIe uses a **tree topology** rooted at the Root Complex:

```
                         ┌─────────────┐
                         │     CPU     │
                         └──────┬──────┘
                                │
                         ┌──────┴──────┐
                         │ Root Complex│  ← Bridges CPU to PCIe
                         │  (RC)       │     fabric; may have
                         └──┬──────┬───┘     integrated endpoints
                            │      │
                    ┌───────┘      └───────┐
                    │                      │
              ┌─────┴─────┐         ┌─────┴─────┐
              │ Root Port │         │ Root Port │  ← Each RP is a
              │  (RP)     │         │  (RP)     │     virtual PCI-to-PCI
              └─────┬─────┘         └─────┬─────┘     bridge
                    │                      │
              ┌─────┴─────┐         ┌─────┴──────┐
              │  Switch   │         │  Endpoint  │  ← NVMe SSD,
              │           │         │  (EP)      │     GPU, NIC, etc.
              └──┬────┬───┘         └────────────┘
                 │    │
           ┌─────┘    └─────┐
     ┌─────┴─────┐   ┌─────┴─────┐
     │ Endpoint  │   │ Endpoint  │
     │ (EP)      │   │ (EP)      │
     └───────────┘   └───────────┘
```

**Root Complex (RC)**: Connects the processor and memory subsystem to the PCIe fabric. Generates configuration transactions. Contains one or more Root Ports. In x86 systems, typically part of the CPU die or chipset (PCH). The RC terminates the PCIe hierarchy — all TLPs addressed to system memory are consumed here.

**Root Port (RP)**: A virtual PCI-to-PCI bridge within the RC. Each RP represents one downstream PCIe link. Has a Type 1 (bridge) configuration header with primary/secondary/subordinate bus numbers and memory/I/O windows.

**Switch**: A PCIe switching fabric. Contains one **upstream port** (connects toward RC) and multiple **downstream ports** (connect toward endpoints). Internally, the switch has a virtual PCI bus connecting all ports. Each port appears as a virtual PCI-to-PCI bridge. Switches perform address-based routing of TLPs between ports using the address windows configured in their bridge registers.

**Endpoint (EP)**: A PCIe function that originates or terminates transactions. Has a Type 0 configuration header. Examples: NVMe controllers, NICs, GPUs, FPGA accelerators. Can have up to 8 functions per device (multi-function).

**Bridge (to PCI/PCI-X)**: Converts between PCIe and legacy PCI/PCI-X protocols. Increasingly rare.

### BDF Addressing

Every PCIe function is uniquely identified by a **Bus:Device:Function** (BDF) address:

```
┌─────────────────┬──────────────┬─────────────┐
│  Bus Number     │ Device Number│  Function   │
│  (8 bits)       │ (5 bits)     │  (3 bits)   │
│  0-255          │ 0-31         │  0-7        │
└─────────────────┴──────────────┴─────────────┘

Total: 16 bits → Requester ID / Completer ID in TLPs

Example: 03:1f.2 = Bus 3, Device 31, Function 2
         Requester ID = (3 << 8) | (31 << 3) | 2 = 0x03FA

PCI Segment Group (16 bits) extends this further:
  Segment:Bus:Device:Function → 0000:03:1f.2
  Allows up to 65536 segments × 256 buses = 16M bus segments
```

The BDF is assigned during enumeration. In PCIe, "Device" within a bus segment is typically 0 because each link connects exactly one device (unlike PCI where multiple devices share a bus). Multi-function devices use functions 0-7 on the same device number.

**ARI (Alternative Routing-ID Interpretation)**: For SR-IOV and multi-function devices that need more than 8 functions. ARI removes the device number field, giving all 8 bits (device + function) to the function number, allowing up to 256 functions per bus number. Requires ARI capability in both the device and the upstream port.

### Link Architecture

A PCIe **link** connects two ports and consists of 1 to 32 **lanes**:

```
         Port A                              Port B
    ┌──────────────┐                    ┌──────────────┐
    │ Lane 0 TX ───┼──── D+ D- ───────►│ Lane 0 RX    │
    │ Lane 0 RX ◄──┼──── D+ D- ────────┤ Lane 0 TX    │
    │              │                    │              │
    │ Lane 1 TX ───┼──── D+ D- ───────►│ Lane 1 RX    │
    │ Lane 1 RX ◄──┼──── D+ D- ────────┤ Lane 1 TX    │
    │              │                    │              │
    │    ...       │                    │    ...       │
    │              │                    │              │
    │ Lane N TX ───┼──── D+ D- ───────►│ Lane N RX    │
    │ Lane N RX ◄──┼──── D+ D- ────────┤ Lane N TX    │
    └──────────────┘                    └──────────────┘

    Each lane = 1 differential pair TX + 1 differential pair RX
    Full duplex — simultaneous transmit and receive
```

Common widths: x1, x2, x4, x8, x16 (x32 rare, only in some server interconnects).

Data is **striped** across lanes at the byte level. For a x4 link, byte 0 goes on lane 0, byte 1 on lane 1, byte 2 on lane 2, byte 3 on lane 3, byte 4 on lane 0, etc. This distributes the payload across all lanes for maximum bandwidth.

**Lane reversal**: During link training, the two ports may discover that lane 0 on one side connects to lane N on the other (PCB routing convenience). The hardware can reverse the lane numbering to compensate.

**Polarity inversion**: If D+ and D- of a differential pair are swapped, the receiver can invert the polarity. Detected and corrected during link training.

### PCIe Protocol Stack

```
┌─────────────────────────────────────────────┐
│            Software Layer                    │
│   (Device driver, OS, application)           │
├─────────────────────────────────────────────┤
│         Transaction Layer                    │
│   TLPs: MRd, MWr, CfgRd, CfgWr,           │
│   Cpl, CplD, Msg                            │
│   Flow control, ordering, virtual channels   │
├─────────────────────────────────────────────┤
│          Data Link Layer                     │
│   DLLPs: Ack/Nak, FC update, PM             │
│   Sequence numbers, CRC, retry              │
├─────────────────────────────────────────────┤
│           Physical Layer                     │
│   Electrical signaling, 8b/10b or           │
│   128b/130b encoding, LTSSM,               │
│   lane training, equalization               │
├─────────────────────────────────────────────┤
│         Electrical/Mechanical                │
│   Differential pairs, connectors,           │
│   impedance, voltage levels                 │
└─────────────────────────────────────────────┘
```

---

## 3. Transaction Layer — TLPs

The Transaction Layer is the heart of PCIe. All communication between devices occurs via **Transaction Layer Packets (TLPs)**.

### TLP Categories

TLPs are classified by their semantics:

```
Category       TLP Types              Requires Completion?   Ordering
─────────────────────────────────────────────────────────────────────
Posted         Memory Write (MWr)     No                     Strong
               Message (Msg/MsgD)     No                     Strong

Non-Posted     Memory Read (MRd)      Yes (CplD)             Strong
               I/O Read (IORd)        Yes (CplD)             Strong
               I/O Write (IOWr)       Yes (Cpl)              Strong
               Config Read (CfgRd)    Yes (CplD)             Strong
               Config Write (CfgWr)   Yes (Cpl)              Strong

Completion     Completion (Cpl)       N/A                    Relaxed*
               Completion w/ Data     N/A                    Relaxed*
               (CplD)
```

**Posted**: Fire-and-forget. The sender does not wait for acknowledgment from the receiver. Memory writes are posted for performance — the sender can continue immediately. Ordering guarantee: posted requests are delivered in order.

**Non-Posted**: Request-response pair. The sender must wait for a completion. Memory reads, I/O operations, and configuration operations are all non-posted. Each non-posted request is tracked by a Tag, and the requester maintains a pending request table.

**Completion**: Response to a non-posted request. Carries data (CplD) for reads, or just status (Cpl) for writes.

### TLP Header Format

Every TLP starts with a 3-DW (12-byte) or 4-DW (16-byte) header:

```
TLP Packet Structure:
┌─────────────────────────────────────────────────────────────┐
│ (Optional) TLP Prefix(es) — 4 bytes each, Gen 3+ only      │
├─────────────────────────────────────────────────────────────┤
│ Header: 3 DW (12 bytes) or 4 DW (16 bytes)                 │
├─────────────────────────────────────────────────────────────┤
│ Data Payload: 0 to 1024 DW (0 to 4096 bytes)              │
├─────────────────────────────────────────────────────────────┤
│ (Optional) ECRC: 4 bytes (end-to-end CRC)                  │
└─────────────────────────────────────────────────────────────┘

Note: The Data Link Layer adds a 2-byte sequence number prefix
and a 4-byte LCRC suffix (not part of the TLP itself).

Full packet on wire:
┌──────┬───────────────────────────────────┬──────┐
│ Seq# │ TLP (header + data + ECRC)        │ LCRC │
│ 2B   │                                   │ 4B   │
└──────┴───────────────────────────────────┴──────┘
```

#### DW0 — Common Header (First Doubleword)

```
Bit 31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16 15 14 13 12 11 10  9  8  7  6  5  4  3  2  1  0
    ├──┤  ├─────┤  ├──────────────┤  ├─┤  ├──┤  ├─┤  ├─┤  ├─┤  ├────────────────────────────────────┤
    Fmt    Type        R           TC   R  Attr  R  TH  TD  EP  Attr          Length
    [2]    [5]        [1]         [3] [1] [2]  [1][1] [1] [1] [1]            [10]

Fmt[1:0]:
  00 = 3 DW header, no data
  01 = 4 DW header, no data
  10 = 3 DW header, with data
  11 = 4 DW header, with data
  (For TLP Prefix: Fmt = 100)

Type[4:0] combined with Fmt:
  Fmt  Type    TLP Type
  ──── ─────   ──────────────────────────────
  00   00000   Memory Read Request (MRd) — 32-bit address
  01   00000   Memory Read Request (MRd) — 64-bit address
  00   00001   Memory Read Lock Request (MRdLk) — 32-bit (deprecated)
  01   00001   Memory Read Lock Request (MRdLk) — 64-bit (deprecated)
  10   00000   Memory Write Request (MWr) — 32-bit address
  11   00000   Memory Write Request (MWr) — 64-bit address
  00   00010   I/O Read Request (IORd)
  10   00010   I/O Write Request (IOWr)
  00   00100   Config Read Type 0 (CfgRd0)
  10   00100   Config Write Type 0 (CfgWr0)
  00   00101   Config Read Type 1 (CfgRd1)
  10   00101   Config Write Type 1 (CfgWr1)
  01   10000   Message Request (Msg) — routed by address
  01   10001   Message Request (Msg) — routed by ID
  01   10010   Message Request (Msg) — broadcast from RC
  01   10011   Message Request (Msg) — local, terminate at receiver
  01   10100   Message Request (Msg) — gathered, routed to RC
  01   10101   Message Request (Msg) — reserved
  11   10xxx   Message Request with Data (MsgD)
  00   01010   Completion (Cpl) — no data
  10   01010   Completion with Data (CplD)
  00   01011   Completion for Locked Memory Read (CplLk)
  10   01011   Completion for Locked Memory Read with Data (CplDLk)

TC[2:0]: Traffic Class (0-7). TC0 is default. Higher TCs map to
         higher-priority Virtual Channels if configured.

Attr[2]: IDO (ID-based Ordering) — Gen 2+. Allows completions to
         pass posted writes if they have different requester IDs.

Attr[1:0]:
  Bit 1 = Relaxed Ordering (RO). If set, this TLP may pass
          previously queued TLPs (relaxes strict ordering).
  Bit 0 = No Snoop (NS). If set, system hardware is not required
          to snoop CPU caches for this transaction.

TH: TLP Processing Hint (Gen 3+). If set, the last 4 bytes of
    the TLP (before ECRC) contain Steering Tag hints for
    cache allocation at the completer.

TD: TLP Digest (ECRC) present. If set, 4-byte ECRC appended.

EP: Poisoned. If set, the data payload is known to be corrupted.
    Receiver should accept the TLP but flag an error.

Length[9:0]: Data payload length in DWs (doublewords = 4 bytes).
  0 = reserved (except: Length=0 means 1024 DWs for some types).
  Valid range: 1-1024 DWs (4-4096 bytes).
  For requests without data (reads), this is the requested length.
  Must not exceed Max Payload Size (MPS) for writes or
  Max Read Request Size (MRRS) for reads.
```

#### Memory Request Header (3DW — 32-bit address)

```
DW0: [Fmt|Type|R|TC|R|Attr|R|TH|TD|EP|Attr|Length]  (as above)

DW1:
Bit 31                    16 15          8 7              0
┌─────────────────────────┬──────────────┬────────────────┐
│     Requester ID         │    Tag       │ Last BE|1st BE │
│   (Bus:Dev:Func)         │  (8 bits)    │ [3:0]  [3:0]  │
│     16 bits              │              │                │
└─────────────────────────┴──────────────┴────────────────┘

DW2:
Bit 31                                          2 1 0
┌────────────────────────────────────────────────┬───┐
│              Address [31:2]                     │ R │
│              (30 bits, DW-aligned)              │   │
└────────────────────────────────────────────────┴───┘
```

#### Memory Request Header (4DW — 64-bit address)

```
DW0: [Fmt|Type|R|TC|R|Attr|R|TH|TD|EP|Attr|Length]

DW1:
┌─────────────────────────┬──────────────┬────────────────┐
│     Requester ID         │    Tag       │ Last BE|1st BE │
└─────────────────────────┴──────────────┴────────────────┘

DW2:
┌────────────────────────────────────────────────────────┐
│              Address [63:32]  (upper 32 bits)           │
└────────────────────────────────────────────────────────┘

DW3:
┌────────────────────────────────────────────────┬───┐
│              Address [31:2]                     │ R │
└────────────────────────────────────────────────┴───┘
```

**Byte Enables**: 4-bit masks specifying which bytes within the first and last DW are valid.
- `1st DW BE[3:0]`: Byte enables for the first DW of data. Bit 0 = byte at lowest address.
- `Last DW BE[3:0]`: Byte enables for the last DW. All zeros if Length == 1 DW.

Example: Writing bytes at offset 0x1001-0x1002 (2 bytes starting at offset 1 within a DW):
- Address = 0x1000 (DW-aligned), Length = 1
- 1st DW BE = 0b0110 (bytes 1 and 2 valid, bytes 0 and 3 not)
- Last DW BE = 0b0000 (unused, Length=1)

**Tag field**: Uniquely identifies outstanding non-posted requests from a given requester. Standard: 8-bit tag (256 outstanding requests). Extended: 10-bit (1024 outstanding), requires Extended Tag capability. Phantom Functions can further expand the tag space by using unused function bits.

**Requester ID**: The BDF of the function that originated the request. Used to route completions back.

#### Completion Header

```
DW0: [Fmt=00/10|Type=01010|R|TC|R|Attr|R|TH|TD|EP|Attr|Length]

DW1:
Bit 31                    16 15    13 12        8 7              0
┌─────────────────────────┬─────────┬────────────┬────────────────┐
│     Completer ID         │  Cpl    │    Byte    │                │
│   (Bus:Dev:Func)         │ Status  │   Count    │    BCM         │
│     16 bits              │ [2:0]   │  [11:0]    │   [0]          │
└─────────────────────────┴─────────┴────────────┴────────────────┘

Completion Status:
  000 = Successful Completion (SC)
  001 = Unsupported Request (UR)
  010 = Config Request Retry Status (CRS) — device not ready
  100 = Completer Abort (CA)

Byte Count[11:0]: Remaining bytes to be transferred (for
  split completions, multiple CplD packets may be needed).

BCM: Byte Count Modified — set by PCI-X bridges.

DW2:
Bit 31                    16 15          8 7              0
┌─────────────────────────┬──────────────┬────────────────┐
│     Requester ID         │    Tag       │ R |Lower Addr  │
│   (original requester)   │  (8 bits)    │[7]  [6:0]     │
└─────────────────────────┴──────────────┴────────────────┘

Lower Address[6:0]: Lower bits of the byte address for
  the first enabled byte of data in this completion.
  Used by the requester to place data correctly.
```

#### Configuration Request Header

```
DW0: [Fmt=00/10|Type=00100(T0)/00101(T1)|...|Length=1]

DW1:
┌─────────────────────────┬──────────────┬────────────────┐
│     Requester ID         │    Tag       │ Last BE|1st BE │
└─────────────────────────┴──────────────┴────────────────┘

DW2:
Bit 31               24 23    16 15    11 10     8 7         2 1 0
┌───────────────────────┬────────┬───────┬────────┬──────────┬───┐
│     Reserved          │  Bus   │  Dev  │  Func  │ Ext Reg  │ R │
│                       │  [7:0] │ [4:0] │ [2:0]  │  [9:2]   │   │
└───────────────────────┴────────┴───────┴────────┴──────────┴───┘

Ext Reg#[9:2] + Reg#[7:2] (from Ext Reg# field):
  Combined 10-bit register number, addressing the full
  4096-byte configuration space (4096/4 = 1024 DW offsets).

  Standard config space: Ext Reg# = 0, Reg#[7:2] addresses
  offsets 0x00-0xFC (256 bytes).
  Extended config space: offsets 0x100-0xFFC.
```

**Type 0 vs Type 1 configuration requests**:
- **Type 0**: Targets the device on the current bus segment. Consumed by the endpoint.
- **Type 1**: Forwarded by bridges. When a bridge receives a Type 1 CfgRd/CfgWr, it checks if the target bus number matches its secondary bus. If so, it converts to Type 0 and forwards. If the target bus is behind a subordinate bridge, it forwards as Type 1.

#### Message TLP Header

```
DW0: [Fmt=01/11|Type=10rrr|...|Length]
     rrr = routing subfield (by address, by ID, broadcast, etc.)

DW1:
┌─────────────────────────┬──────────────┬────────────────┐
│     Requester ID         │    Tag       │  Message Code  │
│                          │              │    [7:0]       │
└─────────────────────────┴──────────────┴────────────────┘

DW2-DW3: Depends on message code and routing type.
  For INTx: message code = 0x20 (Assert_INTA) through 0x23 (Assert_INTD)
            or 0x24 (Deassert_INTA) through 0x27 (Deassert_INTD)
  For PME: message code = 0x18 (PME_Turn_Off), 0x19 (PME_TO_Ack)
  For Error: 0x30 (ERR_COR), 0x31 (ERR_NONFATAL), 0x33 (ERR_FATAL)
  For vendor-defined: specific vendor code in message code field
```

### TLP Processing Rules

When a PCIe port receives a TLP, it must decide whether to accept, forward, or reject it:

1. **Endpoint receives a Memory/IO TLP**: Compare the address against all enabled BARs. If the address falls within a BAR range, accept. Otherwise, send Unsupported Request (UR) completion (for non-posted) or silently discard (for posted writes — with error logging).

2. **Bridge/Switch receives a TLP**: Compare address against configured memory and I/O windows (Memory Base/Limit, I/O Base/Limit, Prefetchable Memory Base/Limit). If the address falls within a downstream window, forward downstream. If it falls outside all downstream windows, forward upstream (toward RC). Bridges also check secondary bus number range for config TLPs.

3. **Root Complex receives a TLP**: Memory addresses targeting system DRAM are consumed by the RC (DMA reads/writes). Completions are routed to the originating function by Requester ID. Messages (INTx, PME, error) are handled by the RC's interrupt and error logic.

### Poisoned TLPs

A poisoned TLP (EP bit set in DW0) indicates the data payload is corrupted. This can occur due to:
- Uncorrectable memory ECC error during DMA read
- Data integrity error detected at an intermediate point

The receiver must:
- Accept the TLP (don't reject it)
- Log an error (poisoned TLP received = uncorrectable non-fatal error)
- Use the corrupted data or discard it (implementation-defined)
- NOT generate a completion abort — the TLP was delivered

### Ordering Rules

PCIe ordering rules ensure producer-consumer correctness:

```
           Can B pass A?

A \ B        Posted    Non-Posted    Completion
─────────────────────────────────────────────
Posted        No(1)      Yes          Yes
Non-Posted    No(2)      No(3)        Yes
Completion    Yes        Yes          No(4)

(1) Posted requests are strongly ordered — P2 cannot pass P1.
    This ensures writes arrive in program order.
(2) Non-posted requests cannot pass posted requests.
    A read issued after a write must see the write's effect.
(3) Non-posted requests are ordered among themselves.
(4) Completions for the same request are ordered (multi-part
    completion for a large read). Completions for different
    requests may be reordered.

With Relaxed Ordering (RO) attribute set:
- Memory writes with RO set can pass other memory writes
- Memory reads with RO set can pass memory writes
- Useful for bulk DMA where ordering doesn't matter

With ID-based Ordering (IDO):
- Transactions with different Requester IDs can pass each other
- Useful for SR-IOV where VF traffic shouldn't block PF traffic
```

### Credit-Based Flow Control

PCIe uses a **credit-based flow control** mechanism to prevent buffer overflow. Each receiver advertises how many TLPs it can accept, measured in credit units.

```
Six credit types (tracked independently):
  1. Posted Header (PH)         — headers for memory writes, messages
  2. Posted Data (PD)           — data payloads for posted TLPs
  3. Non-Posted Header (NPH)    — headers for reads, config, I/O ops
  4. Non-Posted Data (NPD)      — data payloads for non-posted TLPs
  5. Completion Header (CplH)   — headers for completions
  6. Completion Data (CplD)     — data payloads for completions

Credit units:
  Header credit = 1 per TLP header (regardless of 3DW or 4DW)
  Data credit = 1 per 4 DW (16 bytes) of payload, rounded up
    Example: 64-byte payload = 64/16 = 4 data credits
    Example: 20-byte payload = ceil(20/16) = 2 data credits

Transmitter maintains:
  credits_consumed[type] — running counter of credits used

Receiver advertises:
  credits_limit[type]   — maximum credits available

Transmission rule:
  Can transmit only if: credits_consumed + cost <= credits_limit
  (using modular arithmetic with the counter width)

Flow:
  1. During link initialization, receiver sends InitFC1/InitFC2
     DLLPs advertising initial credit limits for all 6 types.
  2. Transmitter starts with credits_consumed = 0.
  3. Each TLP sent increments credits_consumed.
  4. Receiver processes TLPs, frees buffer space, sends
     UpdateFC DLLPs with new credits_limit values.
  5. Transmitter updates its credits_limit and can send more.

IMPORTANT: Endpoints MUST advertise infinite credits for
completion headers and completion data. This prevents deadlock
— a device that issued a read must always be able to accept
the completion, even if its buffers are full of other traffic.
Infinite credit = a credits_limit value that never stops the
transmitter.

Switches/bridges can have finite completion credits but must
have enough to handle all outstanding non-posted requests
that might generate completions.
```

### Max Payload Size (MPS) and Max Read Request Size (MRRS)

```
MPS (Max Payload Size):
  Configured in PCIe Device Control register (bits 7:5).
  Values: 128, 256, 512, 1024, 2048, 4096 bytes.
  Applies to the DATA PAYLOAD of any TLP (read completion data
  or write data). A TLP's data payload must not exceed MPS.

  Negotiation: Each device advertises its Max_Payload_Size_Supported
  in Device Capabilities. System software sets MPS to the minimum
  of all devices in the hierarchy (to ensure all switches and
  bridges can handle the payload).

  Common default: 128 bytes (conservative).
  NVMe SSDs: often support 512 or 256.
  GPUs: often support 256.

MRRS (Max Read Request Size):
  Configured in PCIe Device Control register (bits 14:12).
  Values: 128, 256, 512, 1024, 2048, 4096 bytes.
  Limits the size of a single memory read request.
  Large reads may be split into multiple completions.
  MRRS can be set independently per device (unlike MPS).
```

---

## 4. Data Link Layer — DLLPs

The Data Link Layer provides reliable delivery of TLPs between adjacent PCIe ports using sequence numbers, CRC, and a retry mechanism.

### DLLP Format

```
DLLP packet (always 8 bytes on wire):

┌──────────────┬────────────────────────────┬──────────┐
│  DLLP Type   │  Type-specific fields      │  CRC-16  │
│  (8 bits)    │  (24 bits)                 │ (16 bits)│
└──────────────┴────────────────────────────┴──────────┘
                                              ▲
                                     CRC covers DLLP Type
                                     + type-specific fields

DLLP Types:
  Type byte    Name                    Purpose
  ─────────    ────                    ───────
  0000_0000    Ack                     Acknowledge TLPs up to seq#
  0001_0000    Nak                     Request retransmission from seq#
  0010_0000    PM_Enter_L1             Power management
  0010_0001    PM_Enter_L23            Power management
  0010_0011    PM_Active_State_Req_L1  Power management
  0010_0100    PM_Request_Ack          Power management
  0011_0000    Vendor-specific         Implementation-defined
  0100_xxxx    InitFC1-P               Flow control init (Posted)
  0101_xxxx    InitFC1-NP              Flow control init (Non-Posted)
  0110_xxxx    InitFC1-Cpl             Flow control init (Completion)
  1100_xxxx    InitFC2-P               Flow control init phase 2
  1101_xxxx    InitFC2-NP              Flow control init phase 2
  1110_xxxx    InitFC2-Cpl             Flow control init phase 2
  1000_xxxx    UpdateFC-P              Flow control update (Posted)
  1001_xxxx    UpdateFC-NP             Flow control update (Non-Posted)
  1010_xxxx    UpdateFC-Cpl            Flow control update (Completion)
```

### Ack/Nak DLLP Format

```
Bit   7      4 3       0    23            12 11              0
┌─────────────┬───────────┬────────────────┬─────────────────┐
│  Ack (0x00) │  Reserved │   Reserved     │  AckNak_Seq#    │
│  Nak (0x10) │           │                │   [11:0]        │
└─────────────┴───────────┴────────────────┴─────────────────┘

AckNak_Seq#: The sequence number being acknowledged or nak'd.
  Ack: All TLPs with seq# <= AckNak_Seq# are acknowledged.
  Nak: Retransmit starting from TLP with seq# = AckNak_Seq#.
```

### Flow Control DLLP Format

```
Bit   7            0    23     20 19       8 7              0
┌────────────────────┬──────────┬───────────┬────────────────┐
│  FC Type (InitFC1/ │ HdrFC    │  HdrFC    │   DataFC       │
│  InitFC2/UpdateFC  │ [11:8]   │   [7:0]   │   [11:0]       │
│  + P/NP/Cpl)       │          │           │                │
└────────────────────┴──────────┴───────────┴────────────────┘

HdrFC[11:0]: Header credits (number of TLP headers receiver can buffer)
DataFC[11:0]: Data credits (in units of 4 DW = 16 bytes)

Infinite credits: HdrFC or DataFC = 0 means infinite for that type.
(InitFC values of 0 mean infinite; UpdateFC 0 means no change.)
```

### Ack/Nak Protocol and Retry

```
Transmitter                                    Receiver
    │                                              │
    │──── TLP (seq=0) ──────────────────────────►  │
    │──── TLP (seq=1) ──────────────────────────►  │
    │──── TLP (seq=2) ──────────────────────────►  │  CRC check
    │                                              │  passes
    │  ◄──────────────────────── Ack (seq=2) ──── │  (acks 0,1,2)
    │                                              │
    │──── TLP (seq=3) ──────────────────────────►  │  CRC FAILS
    │──── TLP (seq=4) ──────────────────────────►  │
    │                                              │
    │  ◄──────────────────────── Nak (seq=3) ──── │  (request retry)
    │                                              │
    │  [replay buffer: resend seq=3,4]             │
    │──── TLP (seq=3) ──────────────────────────►  │
    │──── TLP (seq=4) ──────────────────────────►  │
    │                                              │
    │  ◄──────────────────────── Ack (seq=4) ──── │

Replay buffer: Transmitter keeps all sent TLPs until acknowledged.
  Buffer depth determines max outstanding TLPs.

Replay timer: If no Ack received within timeout, retransmit.

Replay number rollover: After N consecutive replays without
  progress, the link is considered failed → link retraining.

LCRC (Link CRC): CRC-32 computed over the entire TLP (header +
  data + ECRC if present). Appended by transmitter Data Link Layer,
  checked by receiver Data Link Layer.

ECRC (End-to-end CRC): CRC-32 computed by the originator over
  the TLP header + data. Survives across switches. Optional.
  Checked by the final destination, not intermediate switches.
  Enabled via AER capability.
```

### Flow Control Initialization

```
Flow control initialization occurs during link training (after
Physical Layer link-up, before normal TLP traffic):

Phase 1 (InitFC1):
  Both ports exchange InitFC1 DLLPs for all three types
  (Posted, Non-Posted, Completion). Each InitFC1 carries the
  receiver's initial credit limits.

  A port sends InitFC1 continuously until it receives InitFC1
  from the other side for all three types.

Phase 2 (InitFC2):
  After receiving all InitFC1 DLLPs, switch to InitFC2.
  InitFC2 carries the same credit values (confirmation).

  After both sides complete InitFC2 exchange, the Data Link
  Layer enters DL_Active state and TLP traffic can begin.

After initialization:
  Credits are consumed by sending TLPs and replenished by
  receiving UpdateFC DLLPs from the receiver.

  UpdateFC DLLPs must be sent periodically (even if no credits
  freed) to prevent the transmitter from stalling due to
  counter rollover ambiguity.
```

---

## 5. Physical Layer

### Electrical Signaling

Each PCIe lane is a pair of differential signals (D+ and D-):

```
         TX                                RX
    ┌─────────┐                      ┌─────────┐
    │    D+ ──┼──── AC coupling ────►│ D+      │
    │    D- ──┼──── capacitor ──────►│ D-      │
    └─────────┘                      └─────────┘

Differential voltage swing (Gen 1-5):
  800-1200 mV peak-to-peak differential
  Common-mode voltage: 0 V (AC-coupled)

Impedance: 85 ohm differential, 42.5 ohm single-ended

AC coupling: Required on all lanes. Blocks DC component.
  Allows each side to set its own common-mode voltage.
  Capacitor value: typically 75-200 nF.
```

### LTSSM (Link Training and Status State Machine)

The LTSSM controls link initialization, speed/width negotiation, and power management:

```
                    ┌──────────┐
       Reset ──────►│ Detect   │◄─────────────────┐
                    │          │                   │
                    └────┬─────┘                   │
                         │ Receiver detected       │
                    ┌────▼─────┐                   │
                    │ Polling  │                   │
                    │          │  TS1/TS2 exchange │
                    └────┬─────┘                   │
                         │ Bit lock, symbol lock   │
                    ┌────▼─────────┐               │
                    │Configuration │               │
                    │              │ Lane/width     │
                    │              │ negotiation    │
                    └────┬─────────┘               │
                         │ Link# and Lane#         │
                         │ agreed                  │
                    ┌────▼─────┐                   │
              ┌────►│   L0     │───────────────────┤
              │     │ (Active) │ Link error or     │
              │     └──┬───┬──┘ timeout            │
              │        │   │                       │
              │   ┌────┘   └────┐                  │
              │   │             │                  │
         ┌────▼───▼──┐    ┌────▼─────┐            │
         │ Recovery  │    │   L0s    │            │
         │           │    │ (Standby)│            │
         └─────┬─────┘    └────┬─────┘            │
               │               │                  │
               │          Back to L0              │
               │                                  │
          ┌────▼─────┐                            │
          │   L1     │                            │
          │ (Low Pwr)│                            │
          └────┬─────┘                            │
               │                                  │
          ┌────▼─────┐                            │
          │   L2     │  ──── Power off ────►  ┌───┴────┐
          │ (Sleep)  │                        │  L3    │
          └──────────┘                        │ (Off)  │
                                              └────────┘

Key LTSSM states:

Detect: Port powers on, checks for receiver presence by
  measuring impedance on each lane. If a receiver termination
  (50 ohm to ground) is detected, proceed to Polling.

Polling: Exchange Training Sequences (TS1, TS2) to achieve:
  - Bit lock (PLL locks to incoming data frequency)
  - Symbol lock (8b/10b comma detection or 128b/130b sync header)
  - Lane polarity detection and correction

Configuration: Negotiate link width and assign lane numbers.
  Exchange TS1/TS2 with Link# and Lane# fields set.
  Both sides agree on the active link width.
  After agreement, transition to L0.

L0 (Active): Normal operating state. TLPs and DLLPs flow.
  This is where useful work happens.

Recovery: Entered when:
  - Speed change is needed (e.g., Gen 1 → Gen 3)
  - Equalization required (Gen 3+)
  - Link error recovery
  Exchanges TS1/TS2 to re-establish bit/symbol lock.
  After recovery, return to L0.

L0s: Low-power standby. Electrical idle on TX lanes.
  Fast exit (~1 us). Entered autonomously by hardware.
  No software involvement. Small power savings.

L1: Deeper low-power state. Both TX and RX quiesced.
  Longer exit latency (~2-32 us). Requires DLLP handshake
  to enter. Significant power savings.

L1.1/L1.2 (substates): Even deeper L1 power savings.
  L1.1: Reference clock can be turned off.
  L1.2: Common-mode voltage removed. Longest exit latency
  but maximum power savings.

L2: Very low power. Only auxiliary power maintained.
  Used for wake capability (PME#). Most of the link
  circuitry is powered down.

L3: Link completely off. No power. Full power-on reset
  required to return to Detect.
```

### Ordered Sets

Ordered sets are special symbols transmitted during link training and maintenance:

```
TS1 (Training Sequence 1):
  16 symbols in Gen 1-2, 32 symbols in Gen 3+.
  Contains: COM, Link#, Lane#, N_FTS (number of Fast
  Training Sequences), Data Rate supported, Training
  Control bits (hot reset, disable, loopback, etc.)

TS2 (Training Sequence 2):
  Same format as TS1 but indicates the receiver has
  successfully locked to TS1. Both sides sending TS2
  means agreement.

SKIP ordered set:
  Compensates for clock frequency differences between
  TX and RX (clock tolerance compensation).
  Sent periodically during L0 (every ~1180-1538 symbols).

EIEOS (Electrical Idle Exit Ordered Set):
  Signals transition from electrical idle back to active.
  Used when exiting L0s or L1.

SDS (Start of Data Stream):
  Marks the beginning of TLP/DLLP data after training
  (Gen 3+ only, replaces COM in data stream).

EDS (End of Data Stream):
  Marks transition from data stream to ordered set
  (Gen 3+ only).
```

### Equalization (Gen 3+)

At 8 GT/s and above, signal quality degrades due to channel loss (frequency-dependent attenuation). Equalization compensates:

```
Equalization phases (Gen 3-5):

Phase 0: Upstream port sends initial TX presets in TS1
  (from a table of 11 presets, P0-P10, each specifying
  pre-cursor, cursor, and post-cursor coefficients).

Phase 1: Downstream port evaluates presets, selects best.

Phase 2: Downstream port requests specific TX coefficients
  from upstream port via TS1 Equalization Control field.
  Upstream port adjusts its transmitter.

Phase 3: Upstream port requests specific TX coefficients
  from downstream port (reverse direction equalization).

TX equalization uses a 3-tap FIR (Finite Impulse Response) filter:
  Output = C(-1)*D(n-1) + C(0)*D(n) + C(+1)*D(n+1)

  C(-1) = pre-cursor coefficient (compensates pre-cursor ISI)
  C(0)  = cursor coefficient (main signal amplitude)
  C(+1) = post-cursor coefficient (compensates post-cursor ISI)

  The 11 presets define specific {C(-1), C(0), C(+1)} combinations
  optimized for different channel characteristics.
```

### Clock Architecture

```
Common clock (default for add-in cards):
  Both ports use a common 100 MHz reference clock
  distributed from the platform. ±300 ppm tolerance.
  Simplifies CDR (Clock Data Recovery).

SRIS (Separate Reference clock with Independent SSC):
  Each port has its own reference clock. SSC (Spread
  Spectrum Clocking, ±0.5% modulation) applied independently.
  Requires wider CDR bandwidth. Used in some embedded designs.

SRNS (Separate Reference clock with No SSC):
  Separate clocks, no spread spectrum. Tighter frequency
  tolerance required.

Data clock:
  PCIe embeds the clock in the data stream via encoding
  (8b/10b guarantees transitions, 128b/130b uses scrambling).
  The receiver's CDR PLL recovers the clock from the data.
```

---

## 6. Configuration Space

Every PCIe function has a **4096-byte configuration space**. The first 256 bytes are the standard PCI configuration space. Bytes 256-4095 are the **extended configuration space** (PCIe-only, accessed via ECAM).

### Access Mechanisms

```
Legacy PCI mechanism (I/O ports, first 256 bytes only):
  CONFIG_ADDRESS (0xCF8): Write the target BDF + register offset
  CONFIG_DATA    (0xCFC): Read/write the register value

  CONFIG_ADDRESS format:
  Bit 31    30:24    23:16   15:11    10:8     7:2      1:0
  ┌────┬──────────┬────────┬────────┬───────┬─────────┬─────┐
  │ En │ Reserved │  Bus   │  Dev   │ Func  │ Reg Ofs │  0  │
  │ [1]│  [7]     │  [8]   │  [5]   │  [3]  │  [6]    │ [2] │
  └────┴──────────┴────────┴────────┴───────┴─────────┴─────┘

  Example (C):
  uint32_t config_read32(uint8_t bus, uint8_t dev, uint8_t func,
                         uint8_t offset) {
      uint32_t addr = (1u << 31)          // enable
                    | ((uint32_t)bus << 16)
                    | ((uint32_t)dev << 11)
                    | ((uint32_t)func << 8)
                    | (offset & 0xFC);     // DW-aligned
      outl(0xCF8, addr);
      return inl(0xCFC);
  }

ECAM (Enhanced Configuration Access Mechanism):
  Memory-mapped access to full 4096-byte space.
  Base address provided by ACPI MCFG table.

  Physical address = ECAM_Base
                   + (Bus << 20)
                   + (Device << 15)
                   + (Function << 12)
                   + Register_Offset

  Each function gets a 4 KB page (4096 bytes = 2^12).
  Each bus gets 256 KB (32 devices * 8 functions * 4 KB).
  Max ECAM region = 256 MB (256 buses * 256 KB/bus).

  Example:
  // Map ECAM region
  volatile uint8_t *ecam = mmap_ecam_base();

  // Read 32 bits from Bus=3, Dev=0, Func=0, offset=0x10 (BAR0)
  uint32_t *reg = (uint32_t *)(ecam + (3 << 20) + (0 << 15)
                               + (0 << 12) + 0x10);
  uint32_t bar0 = *reg;
```

### Type 0 Header (Endpoint)

```
Offset  Bits 31:24     Bits 23:16     Bits 15:8      Bits 7:0
──────  ───────────    ───────────    ──────────     ─────────
0x00    Device ID [31:16]              Vendor ID [15:0]
0x04    Status [31:16]                 Command [15:0]
0x08    Class Code     Subclass       Prog IF        Revision ID
0x0C    BIST           Header Type    Lat Timer(0)   Cache Line Sz
0x10    BAR0 [31:0]
0x14    BAR1 [31:0]    (or upper 32 bits of BAR0 if 64-bit)
0x18    BAR2 [31:0]
0x1C    BAR3 [31:0]    (or upper 32 bits of BAR2 if 64-bit)
0x20    BAR4 [31:0]
0x24    BAR5 [31:0]    (or upper 32 bits of BAR4 if 64-bit)
0x28    CardBus CIS Pointer [31:0]    (legacy, usually 0)
0x2C    Subsystem Device ID [31:16]   Subsystem Vendor ID [15:0]
0x30    Expansion ROM Base Address [31:0]
0x34    Reserved [31:8]                Capabilities Pointer [7:0]
0x38    Reserved [31:0]
0x3C    Max Lat        Min Gnt        Interrupt Pin  Interrupt Line
```

**Command Register (0x04, 16 bits):**
```
Bit  Name                 Description
───  ────                 ───────────
 0   I/O Space Enable     Allow I/O BAR access
 1   Memory Space Enable  Allow Memory BAR access
 2   Bus Master Enable    Allow device to initiate DMA
 3   Special Cycles       (hardwired 0 in PCIe)
 4   MW Invalidate En     (hardwired 0 in PCIe)
 5   VGA Palette Snoop    (hardwired 0 in PCIe)
 6   Parity Error Resp    Enable parity error signaling
 7   IDSEL Stepping       (hardwired 0 in PCIe)
 8   SERR# Enable         Enable SERR# driver
 9   Fast B2B Enable      (hardwired 0 in PCIe)
10   Interrupt Disable    Disable INTx assertion
11:15 Reserved
```

**Status Register (0x06, 16 bits):**
```
Bit  Name                    Description
───  ────                    ───────────
 0   Immediate Readiness     (PCIe) Device ready immediately
 3   Interrupt Status        INTx asserted (read-only)
 4   Capabilities List      Always 1 for PCIe (has capabilities)
 5   66 MHz Capable          (hardwired 0 in PCIe)
 7   Fast B2B Capable        (hardwired 0 in PCIe)
 8   Master Data Parity Err  Set when Bus Master detects parity err
11   DEVSEL Timing           (hardwired 0 in PCIe)
12   Signaled Target Abort   Set when device sends UR completion
13   Received Target Abort   Set when device receives UR completion
14   Received Master Abort   Set when completion times out
15   Signaled System Error   Set when device sends ERR_FATAL/NONFATAL
```

**Header Type (0x0E):**
```
Bit 7: Multi-function device (if 1, scan all 8 functions)
Bit 6:0: Header layout type
  0x00 = Type 0 (Endpoint)
  0x01 = Type 1 (PCI-to-PCI Bridge)
  0x02 = Type 2 (CardBus Bridge) — obsolete
```

### Type 1 Header (Bridge / Root Port / Switch Port)

```
Offset  Bits 31:24     Bits 23:16     Bits 15:8      Bits 7:0
──────  ───────────    ───────────    ──────────     ─────────
0x00    Device ID [31:16]              Vendor ID [15:0]
0x04    Status [31:16]                 Command [15:0]
0x08    Class Code     Subclass       Prog IF        Revision ID
0x0C    BIST           Header Type    Lat Timer(0)   Cache Line Sz
0x10    BAR0 [31:0]
0x14    BAR1 [31:0]
0x18    Sec Lat Timer  Subordinate    Secondary      Primary
                       Bus Number     Bus Number     Bus Number
0x1C    Secondary Status [31:16]      I/O Limit      I/O Base
0x20    Memory Limit [31:16]          Memory Base [15:0]
0x24    Pref Mem Limit [31:16]        Pref Mem Base [15:0]
0x28    Prefetchable Base Upper 32 bits
0x2C    Prefetchable Limit Upper 32 bits
0x30    I/O Limit Upper 16    I/O Base Upper 16
0x34    Reserved [31:8]                Capabilities Pointer [7:0]
0x38    Expansion ROM Base Address [31:0]
0x3C    Bridge Ctrl [31:16]           Interrupt Pin  Interrupt Line
```

**Bus Number Registers (0x18):**
```
Primary Bus Number [7:0]:
  Bus number of the bus on the upstream (CPU-facing) side.

Secondary Bus Number [15:8]:
  Bus number of the bus immediately downstream of this bridge.
  The bridge decodes Type 1 config transactions targeting this
  bus number, converts them to Type 0, and forwards downstream.

Subordinate Bus Number [23:16]:
  Highest bus number of any bus downstream of this bridge.
  The bridge forwards Type 1 config transactions for bus numbers
  in [Secondary, Subordinate] range downstream.
```

**Memory Base/Limit (0x20):**
```
Bits 15:4 of Memory Base → bits [31:20] of start address
  (bits [19:0] are 0 → 1 MB alignment, 1 MB granularity)
Bits 15:4 of Memory Limit → bits [31:20] of end address
  (bits [19:0] are 0xFFFFF → inclusive upper bound)

The bridge forwards memory TLPs with addresses in
[Memory Base, Memory Limit + 0xFFFFF] downstream.
TLPs outside this range are forwarded upstream.

Example: Memory Base = 0xF000, Memory Limit = 0xF7F0
  Window = 0xF000_0000 to 0xF7F0_FFFF (128 MB window)
```

**Prefetchable Memory Base/Limit (0x24-0x2C):**
```
Same concept but supports 64-bit addresses:
  Lower 16 bits at offset 0x24 (base) and upper 16 bits of base
  at offset 0x28.
  Lower 16 bits at offset 0x24 (limit) and upper 16 bits of limit
  at offset 0x2C.

Prefetchable memory: Safe to prefetch (no side effects on read).
  Typically used for framebuffers, large DMA buffers.
Non-prefetchable: Reads may have side effects (MMIO registers).
  Use the standard Memory Base/Limit window.
```

**I/O Base/Limit (0x1C, 0x30):**
```
Bits 7:4 of I/O Base → bits [15:12] of start address
  (4 KB alignment, 4 KB granularity)
If bits 3:0 = 0x1, indicates 32-bit I/O addressing
  (upper 16 bits at offset 0x30).
If bits 3:0 = 0x0, indicates 16-bit I/O addressing.

PCIe endpoints rarely use I/O BARs (discouraged).
```

**Bridge Control Register (0x3E, 16 bits):**
```
Bit  Name                    Description
───  ────                    ───────────
 0   Parity Error Response   Forward parity errors
 1   SERR# Enable            Enable SERR# for secondary bus
 2   ISA Enable              Enable ISA I/O filtering
 3   VGA Enable              Forward VGA I/O and memory
 4   VGA 16-bit Decode       VGA alias decoding
 5   Master Abort Mode       (hardwired 0 in PCIe)
 6   Secondary Bus Reset     Assert reset on secondary bus
 8   Primary Discard Timer   (hardwired 0 in PCIe)
 9   Secondary Discard Timer (hardwired 0 in PCIe)
10   Discard Timer Status    (hardwired 0 in PCIe)
11   Discard Timer SERR#     (hardwired 0 in PCIe)
```

### Extended Configuration Space (0x100 - 0xFFF)

Accessible only via ECAM (not via legacy I/O port mechanism). Contains **Extended Capabilities** in a linked list similar to standard capabilities but with a different header format:

```
Offset 0x100+:

Extended Capability Header (4 bytes):
Bit 31:20    19:16       15:0
┌──────────┬───────────┬──────────────────┐
│Next Cap  │ Cap Ver   │ Cap ID           │
│Offset    │ [3:0]     │ [15:0]           │
│[11:0]    │           │                  │
└──────────┴───────────┴──────────────────┘

Next Cap Offset: Pointer to next extended capability (0 = end).
Cap Version: Version of this capability.
Extended Capability ID: 16-bit ID (vs 8-bit for standard caps).

Common Extended Capability IDs:
  0x0001 = AER (Advanced Error Reporting)
  0x0002 = Virtual Channel (VC)
  0x0003 = Device Serial Number
  0x0004 = Power Budgeting
  0x0005 = Root Complex Link Declaration
  0x000D = ACS (Access Control Services)
  0x000E = ARI (Alternative Routing-ID Interpretation)
  0x0010 = SR-IOV (Single Root I/O Virtualization)
  0x0015 = Resizable BAR
  0x0017 = TPH (TLP Processing Hints)
  0x0018 = LTR (Latency Tolerance Reporting)
  0x001E = L1 PM Substates
  0x001F = DPC (Downstream Port Containment)
  0x001D = Secondary PCI Express
  0x0023 = Designated Vendor-Specific
  0x0025 = Data Link Feature
  0x0027 = Physical Layer 16.0 GT/s
  0x0029 = Physical Layer 32.0 GT/s
  0x002B = DOE (Data Object Exchange)
  0x0030 = IDE (Integrity and Data Encryption)
  0x0031 = Physical Layer 64.0 GT/s
```

---

## 7. Base Address Registers (BARs)

BARs define the memory or I/O address ranges that a device's registers occupy. The host CPU accesses device registers by reading/writing to these addresses.

### BAR Format

```
Memory BAR (bit 0 = 0):
Bit 31                                4 3  2  1  0
┌────────────────────────────────────────┬──┬──┬──┐
│        Base Address [31:4]             │P │Ty│ 0│
│   (16-byte aligned minimum)           │  │pe│  │
└────────────────────────────────────────┴──┴──┴──┘

Bit 0: 0 = Memory Space
Bit 2:1 (Type):
  00 = 32-bit address, locatable anywhere in 32-bit space
  10 = 64-bit address, uses this BAR + next BAR for full address
  (01 and 11 are reserved)
Bit 3 (Prefetchable):
  0 = Non-prefetchable (MMIO with side effects, uncacheable)
  1 = Prefetchable (safe to read ahead, write-combinable)

I/O BAR (bit 0 = 1):
Bit 31                             2  1  0
┌──────────────────────────────────┬──┬──┐
│        Base Address [31:2]       │ R│ 1│
│   (4-byte aligned)               │  │  │
└──────────────────────────────────┴──┴──┘

Bit 0: 1 = I/O Space
Bit 1: Reserved
```

### BAR Sizing Algorithm

The firmware/OS determines BAR size by:

```
1. Save original BAR value:
   original = config_read32(bus, dev, func, BAR_OFFSET);

2. Write all 1s to the BAR:
   config_write32(bus, dev, func, BAR_OFFSET, 0xFFFFFFFF);

3. Read back the BAR:
   readback = config_read32(bus, dev, func, BAR_OFFSET);

4. Restore original value:
   config_write32(bus, dev, func, BAR_OFFSET, original);

5. Determine type and size:
   if (readback == 0) {
       // BAR is not implemented (device doesn't use this BAR)
       return;
   }

   if (readback & 1) {
       // I/O BAR
       mask = readback & 0xFFFFFFFC;  // clear type bits
       size = ~mask + 1;              // invert and add 1
       // size is the I/O address space required
   } else {
       // Memory BAR
       mask = readback & 0xFFFFFFF0;  // clear type/prefetch bits
       size = ~mask + 1;
       // size is the memory address space required

       if ((readback >> 1) & 3 == 2) {
           // 64-bit BAR: read next BAR too
           config_write32(bus, dev, func, BAR_OFFSET+4, 0xFFFFFFFF);
           upper = config_read32(bus, dev, func, BAR_OFFSET+4);
           config_write32(bus, dev, func, BAR_OFFSET+4, original_upper);
           // Combine for 64-bit size calculation
           uint64_t mask64 = ((uint64_t)upper << 32) | mask;
           uint64_t size64 = ~mask64 + 1;
       }
   }

Example: NVMe controller BAR0
  Write 0xFFFFFFFF → BAR0
  Read back: 0xFFFFC004
    Bit 0 = 0 → Memory BAR
    Bits 2:1 = 10 → 64-bit
    Bit 3 = 0 → Non-prefetchable
    Mask = 0xFFFFC000
    Size = ~0xFFFFC000 + 1 = 0x00004000 = 16 KB
  This is a 64-bit, non-prefetchable, 16 KB memory BAR.
  The device's NVMe registers (doorbell array, controller
  registers) are mapped into this 16 KB window.
```

### BAR Assignment

After sizing, firmware/OS assigns addresses:

```
For each device, for each BAR:
  1. Determine size via sizing algorithm
  2. Allocate a naturally-aligned region from the MMIO pool
     (address must be aligned to BAR size)
  3. Write the allocated base address into the BAR
  4. For bridges: ensure the bridge's memory window encompasses
     all downstream BAR assignments

Example BAR assignment for a hierarchy:
  Root Port (Bus 0, Memory Window: 0xF000_0000 - 0xF7FF_FFFF)
    └── NVMe SSD (Bus 1, Dev 0)
          BAR0 = 0xF000_0000 (16 KB, 64-bit, non-prefetchable)
    └── NIC (Bus 1, Dev 1)
          BAR0 = 0xF010_0000 (1 MB, 64-bit, non-prefetchable)
          BAR2 = 0xF020_0000 (64 KB, 64-bit, prefetchable)

  Root Port memory base/limit programmed to cover 0xF000_0000
  to 0xF02F_FFFF (or wider, aligned to 1 MB granularity).

For 64-bit BARs:
  BAR[n] holds lower 32 bits of address
  BAR[n+1] holds upper 32 bits
  This consumes two BAR slots (e.g., BAR0+BAR1, BAR2+BAR3)
  So a Type 0 header with 6 BAR slots can have at most 3
  64-bit BARs, or 6 32-bit BARs, or a mix.
```

### Prefetchable vs Non-Prefetchable

```
Non-prefetchable (MMIO registers):
  - Read side effects possible (reading a status register may
    clear it, reading a FIFO pops an entry)
  - Must be mapped uncacheable (UC) by the CPU
  - Writes are not combinable
  - Bridge forwards only to non-prefetchable memory window

Prefetchable (device memory, framebuffers):
  - Reads are idempotent (no side effects)
  - CPU can use Write-Combining (WC) or even cacheable mapping
  - Multiple writes to adjacent addresses can be merged
  - Bridge can use the prefetchable memory window
  - Improves DMA bandwidth for bulk data

Gotcha: If a device marks a BAR as prefetchable but the registers
  actually have side effects, reads will be corrupted. Conversely,
  marking a framebuffer as non-prefetchable wastes performance
  (no write combining).
```

---

## 8. PCIe Capabilities

PCIe capabilities extend the configuration space with optional features. They form a **linked list** starting from the Capabilities Pointer register (offset 0x34).

### Capability List Structure

```
Standard Capability Header (2 bytes):
Offset+0x00:
Bit 15:8         7:0
┌────────────┬───────────┐
│ Next Cap   │ Cap ID    │
│ Pointer    │           │
│ (offset)   │           │
└────────────┴───────────┘

Next Pointer: Offset of next capability in config space.
  0x00 = end of list.
  Must be DWORD-aligned (bits 1:0 are reserved/zero).

Cap ID: Identifies the capability type.

Standard Capability IDs:
  0x01 = PCI Power Management Interface (PMI)
  0x05 = MSI (Message Signaled Interrupts)
  0x10 = PCI Express Capability
  0x11 = MSI-X
  0x12 = SATA Data/Index Configuration
  0x13 = Advanced Features (AF)
  0x14 = Enhanced Allocation (EA)

Walking the capability list:
  uint8_t cap_ptr = config_read8(dev, 0x34) & 0xFC;
  while (cap_ptr != 0) {
      uint8_t cap_id = config_read8(dev, cap_ptr);
      uint8_t next = config_read8(dev, cap_ptr + 1) & 0xFC;
      printf("Capability 0x%02x at offset 0x%02x\n", cap_id, cap_ptr);
      if (cap_id == target_cap) {
          // Found it — read capability-specific registers
          break;
      }
      cap_ptr = next;
  }
```

### PCI Express Capability (ID 0x10)

The most important capability for any PCIe device. Always present.

```
Offset  Register                 Size
──────  ────────                 ────
+0x00   Cap ID (0x10) | Next Ptr 2B
+0x02   PCIe Capabilities Reg    2B
+0x04   Device Capabilities      4B
+0x08   Device Control           2B
+0x0A   Device Status            2B
+0x0C   Link Capabilities        4B
+0x10   Link Control             2B
+0x12   Link Status              2B
+0x14   Slot Capabilities        4B (Root/Switch Downstream ports only)
+0x18   Slot Control             2B
+0x1A   Slot Status              2B
+0x1C   Root Control             2B (Root Ports only)
+0x1E   Root Capabilities        2B
+0x20   Root Status              4B
+0x24   Device Capabilities 2    4B
+0x28   Device Control 2         2B
+0x2A   Device Status 2          2B
+0x2C   Link Capabilities 2      4B
+0x30   Link Control 2           2B
+0x32   Link Status 2            2B
+0x34   Slot Capabilities 2      4B
+0x38   Slot Control 2           2B
+0x3A   Slot Status 2            2B

PCIe Capabilities Register (+0x02):
  Bits 3:0:  Capability Version (current = 2)
  Bits 7:4:  Device/Port Type:
    0000 = PCI Express Endpoint
    0001 = Legacy PCI Express Endpoint
    0100 = Root Port
    0101 = Upstream Port of Switch
    0110 = Downstream Port of Switch
    1000 = PCIe-to-PCI/PCI-X Bridge
    1001 = Root Complex Integrated Endpoint
    1010 = Root Complex Event Collector
  Bit 8:     Slot Implemented
  Bits 13:9: Interrupt Message Number (for MSI/MSI-X)

Device Capabilities (+0x04):
  Bits 2:0:  Max_Payload_Size Supported (encoded: 0=128B, 1=256B,
             2=512B, 3=1024B, 4=2048B, 5=4096B)
  Bits 4:3:  Phantom Functions Supported
  Bit 5:     Extended Tag Field Supported (10-bit tags)
  Bits 8:6:  Endpoint L0s Acceptable Latency
  Bits 11:9: Endpoint L1 Acceptable Latency
  Bit 15:    Role-Based Error Reporting
  Bit 28:    FLR (Function Level Reset) Capable

Device Control (+0x08):
  Bit 0:     Correctable Error Reporting Enable
  Bit 1:     Non-Fatal Error Reporting Enable
  Bit 2:     Fatal Error Reporting Enable
  Bit 3:     Unsupported Request Reporting Enable
  Bit 4:     Enable Relaxed Ordering
  Bits 7:5:  Max_Payload_Size (must not exceed Device Cap)
  Bit 8:     Extended Tag Field Enable
  Bit 9:     Phantom Functions Enable
  Bit 10:    AUX Power PM Enable
  Bit 11:    Enable No Snoop
  Bits 14:12: Max_Read_Request_Size
  Bit 15:    Initiate FLR (Function Level Reset) — write-only

Device Status (+0x0A):
  Bit 0:     Correctable Error Detected
  Bit 1:     Non-Fatal Error Detected
  Bit 2:     Fatal Error Detected
  Bit 3:     Unsupported Request Detected
  Bit 4:     AUX Power Detected
  Bit 5:     Transactions Pending (device has outstanding non-posted)

Link Capabilities (+0x0C):
  Bits 3:0:   Max Link Speed (1=2.5GT/s, 2=5GT/s, 3=8GT/s,
              4=16GT/s, 5=32GT/s, 6=64GT/s)
  Bits 9:4:   Maximum Link Width (1,2,4,8,12,16,32)
  Bits 11:10: ASPM Support (bit0=L0s, bit1=L1)
  Bits 14:12: L0s Exit Latency (encoded, 64ns to >4us)
  Bits 17:15: L1 Exit Latency (encoded, 1us to >64us)
  Bit 18:     Clock Power Management
  Bit 19:     Surprise Down Error Reporting Capable
  Bit 20:     Data Link Layer Link Active Reporting Capable
  Bit 21:     Link Bandwidth Notification Capable
  Bit 22:     ASPM Optionality Compliance
  Bits 31:24: Port Number

Link Control (+0x10):
  Bits 1:0:   ASPM Control (00=disabled, 01=L0s, 10=L1, 11=L0s+L1)
  Bit 3:      Read Completion Boundary (0=64B, 1=128B)
  Bit 4:      Link Disable
  Bit 5:      Retrain Link (write 1 to initiate retraining)
  Bit 6:      Common Clock Configuration
  Bit 7:      Extended Synch
  Bit 8:      Enable Clock Power Management
  Bit 9:      Hardware Autonomous Width Disable
  Bit 10:     Link Bandwidth Management Interrupt Enable
  Bit 11:     Link Autonomous Bandwidth Interrupt Enable
  Bits 15:12: Reserved (Gen 3+: some equalization bits)

Link Status (+0x12):
  Bits 3:0:   Current Link Speed (same encoding as Cap)
  Bits 9:4:   Negotiated Link Width
  Bit 11:     Link Training (1 = training in progress)
  Bit 12:     Slot Clock Configuration
  Bit 13:     Data Link Layer Link Active (DL_Active)
  Bit 14:     Link Bandwidth Management Status
  Bit 15:     Link Autonomous Bandwidth Status
```

### Power Management Capability (ID 0x01)

```
Offset  Register              Size
──────  ────────              ────
+0x00   Cap ID (0x01) | Next  2B
+0x02   PMC (PM Capabilities) 2B
+0x04   PMCSR (PM Ctrl/Status)2B
+0x06   PMCSR_BSE Bridge Ext  1B
+0x07   Data                  1B

PMC (+0x02):
  Bits 2:0:  Version (3 for PCIe)
  Bit 3:     PME Clock (0 for PCIe)
  Bit 5:     DSI (Device Specific Init)
  Bits 8:6:  AUX Current (for D3cold PME)
  Bit 9:     D1 Support
  Bit 10:    D2 Support
  Bits 15:11: PME Support (which D-states can generate PME)

PMCSR (+0x04):
  Bits 1:0:  PowerState (00=D0, 01=D1, 10=D2, 11=D3hot)
  Bit 3:     No Soft Reset
  Bit 8:     PME Enable
  Bits 12:9: Data Select
  Bits 14:13: Data Scale
  Bit 15:    PME Status (write 1 to clear)

D-state power consumption:
  D0: Fully operational. All functions available.
  D1: Light sleep. Context preserved. Quick wake.
  D2: Deeper sleep. More context may be lost.
  D3hot: Deep sleep. Only config space accessible.
       Software can transition to D0 via PMCSR.
  D3cold: Power removed. Only aux power (for wake).
       Requires full power-on reset to return to D0.
```

### MSI Capability (ID 0x05)

See [Section 9: Interrupt Mechanisms](#9-interrupt-mechanisms) for detailed MSI/MSI-X coverage.

### AER — Advanced Error Reporting (Extended Cap ID 0x0001)

```
Offset    Register                          Size
──────    ────────                          ────
+0x00     Extended Cap Header               4B
+0x04     Uncorrectable Error Status        4B
+0x08     Uncorrectable Error Mask          4B
+0x0C     Uncorrectable Error Severity      4B
+0x10     Correctable Error Status          4B
+0x14     Correctable Error Mask            4B
+0x18     Advanced Error Capabilities/Ctrl  4B
+0x1C     Header Log (4 DW)                16B
+0x2C     Root Error Command                4B  (Root Ports only)
+0x30     Root Error Status                 4B  (Root Ports only)
+0x34     Error Source ID                   4B  (Root Ports only)
+0x38     TLP Prefix Log (4 DW)            16B (if supported)

Uncorrectable Error Status/Mask/Severity (+0x04/+0x08/+0x0C):
  Bit  Error
  ───  ─────
   4   Data Link Protocol Error
  12   Poisoned TLP Received
  13   Flow Control Protocol Error
  14   Completion Timeout
  15   Completer Abort
  16   Unexpected Completion
  17   Receiver Overflow
  18   Malformed TLP
  19   ECRC Error
  20   Unsupported Request Error
  21   ACS Violation
  22   Uncorrectable Internal Error
  24   AtomicOp Egress Blocked
  25   TLP Prefix Blocked Error
  26   Poisoned TLP Egress Blocked

  Severity: 0 = non-fatal, 1 = fatal
  Default fatal: Data Link Protocol Error, Flow Control Protocol
  Error, Receiver Overflow, Malformed TLP.
  Default non-fatal: all others.

Correctable Error Status/Mask (+0x10/+0x14):
  Bit  Error
  ───  ─────
   0   Receiver Error
   6   Bad TLP
   7   Bad DLLP
   8   Replay Number Rollover
  12   Replay Timer Timeout
  13   Advisory Non-Fatal Error
  14   Corrected Internal Error
  15   Header Log Overflow

Header Log (+0x1C):
  Captures the first 4 DW of the TLP that caused the error.
  Invaluable for debugging — you can see the Requester ID,
  address, type, and other fields of the offending TLP.
```

### SR-IOV Capability (Extended Cap ID 0x0010)

```
Offset    Register                     Size
──────    ────────                     ────
+0x00     Extended Cap Header          4B
+0x04     SR-IOV Capabilities          4B
+0x08     SR-IOV Control               2B
+0x0A     SR-IOV Status                2B
+0x0C     InitialVFs                   2B
+0x0E     TotalVFs                     2B
+0x10     NumVFs                       2B
+0x12     Function Dependency Link     1B
+0x14     First VF Offset              2B
+0x16     VF Stride                    2B
+0x18     Reserved                     2B
+0x1A     VF Device ID                 2B
+0x1C     Supported Page Sizes         4B
+0x20     System Page Size             4B
+0x24     VF BAR0                      4B
+0x28     VF BAR1                      4B
+0x2C     VF BAR2                      4B
+0x30     VF BAR3                      4B
+0x34     VF BAR4                      4B
+0x38     VF BAR5                      4B
+0x3C     VF Migration State Array Ofs 4B

SR-IOV Control (+0x08):
  Bit 0: VF Enable (set to create VFs)
  Bit 1: VF Migration Enable
  Bit 3: VF MSE (Memory Space Enable for all VFs)
  Bit 4: ARI Capable Hierarchy

NumVFs (+0x10):
  Number of VFs to create. Must be <= TotalVFs.
  Write this before setting VF Enable.

First VF Offset (+0x14):
  Routing ID offset from PF to first VF.
  VF0 RID = PF RID + First VF Offset.

VF Stride (+0x16):
  Routing ID stride between consecutive VFs.
  VF[n] RID = PF RID + First VF Offset + n * VF Stride.

VF BARs (+0x24-0x38):
  Define the BAR apertures for each VF.
  All VFs share the same BAR sizes (decoded from VF BARs).
  VF BAR addresses are spaced VF_BAR_size * NumVFs apart.
  The PF driver programs VF BARs during VF creation; each
  VF gets its own slice of the total VF BAR space.

Example: Intel E810 NIC
  TotalVFs = 256 per PF
  VF BAR0 = 16 KB per VF (NIC queue doorbell registers)
  VF BAR3 = 16 KB per VF (NIC queue memory)
  With 64 VFs enabled: 64 * 16 KB = 1 MB per BAR consumed.

  Each VF appears as a separate PCIe function with its own
  config space, BARs, and MSI-X capability. The VF has no
  SR-IOV capability itself — only the PF does.
```

### ACS — Access Control Services (Extended Cap ID 0x000D)

```
ACS is critical for secure SR-IOV and VFIO passthrough.

ACS Control bits:
  Bit 0: ACS Source Validation — reject TLPs from wrong source
  Bit 1: ACS Translation Blocking — prevent ATS translation bypass
  Bit 2: ACS P2P Request Redirect — redirect peer-to-peer requests
         through the IOMMU instead of direct switch forwarding
  Bit 3: ACS P2P Completion Redirect — same for completions
  Bit 4: ACS Upstream Forwarding — force upstream forwarding
  Bit 5: ACS P2P Egress Control — enable egress control vector
  Bit 6: ACS Direct Translated P2P

Why ACS matters for passthrough:
  Without ACS, two devices behind the same switch could DMA
  directly to each other via peer-to-peer, bypassing the IOMMU.
  A malicious VF assigned to one VM could DMA to a VF assigned
  to another VM through the switch, violating isolation.

  ACS forces all transactions upstream through the IOMMU,
  ensuring DMA isolation even for devices behind switches.

  VFIO checks for ACS support and configuration when determining
  IOMMU groups. Devices behind switches without ACS end up in
  the same IOMMU group, preventing independent assignment.
```

### Other Important Capabilities

```
ATS — Address Translation Services (Extended Cap ID 0x000F):
  Device-side TLB for IOMMU translations.
  Device caches IOVA→PA translations from the IOMMU.
  ATS-capable devices send Translation Requests upstream.
  Reduces IOMMU lookup overhead for frequently accessed pages.
  The IOMMU can invalidate device TLB entries.

PASID — Process Address Space ID (Extended Cap ID 0x001B):
  Allows a device to tag DMA requests with a PASID,
  identifying which process's address space to use.
  Enables Shared Virtual Addressing (SVA) — device DMAs
  using the same virtual addresses as the CPU process.
  Requires IOMMU support (Intel PASID, ARM Substream ID).

Resizable BAR (Extended Cap ID 0x0015):
  Allows dynamic BAR size changes. Used by GPUs to expose
  their full VRAM (e.g., 16 GB) instead of the default
  256 MB window. Requires OS and firmware support.
  NVIDIA/AMD GPUs use this for "ReBAR" / "Smart Access Memory".

LTR — Latency Tolerance Reporting (Extended Cap ID 0x0018):
  Device reports its latency tolerance to the platform.
  Enables aggressive power management — if all devices
  tolerate high latency, deeper sleep states can be used.

DPC — Downstream Port Containment (Extended Cap ID 0x001F):
  Automatically disables a PCIe link when a fatal error
  is detected on the downstream port. Prevents error
  propagation to the rest of the hierarchy. The OS can
  then attempt recovery or hot-removal.

PTM — Precision Time Measurement (Extended Cap ID 0x001F):
  Distributes precise time from the Root Complex to endpoints.
  Enables timestamp synchronization between devices.
  Used for audio/video synchronization, IEEE 1588 PTP.
```

---

## 9. Interrupt Mechanisms

### Legacy INTx Interrupts

```
PCIe preserves PCI's INTx interrupt model but replaces physical
interrupt wires with in-band messages.

Physical PCI: Four shared interrupt lines (INTA#-INTD#).
  Level-triggered, active-low. Multiple devices share lines.

PCIe: INTx messages carried as TLPs:
  Assert_INTA (message code 0x20)
  Assert_INTB (message code 0x21)
  Assert_INTC (message code 0x22)
  Assert_INTD (message code 0x23)
  Deassert_INTA (message code 0x24)
  Deassert_INTB (message code 0x25)
  Deassert_INTC (message code 0x26)
  Deassert_INTD (message code 0x27)

Behavior:
  1. Device asserts virtual interrupt by sending Assert_INTx
  2. Root Complex converts this to physical IOAPIC input or
     internal interrupt routing
  3. CPU receives interrupt, handler reads device status
  4. Handler clears interrupt source in device
  5. Device sends Deassert_INTx

Problems with INTx:
  - Shared interrupts: handler must check if this device
    caused the interrupt → overhead
  - Two messages per interrupt (assert + deassert) → overhead
  - Level-triggered semantics require polling
  - No per-device targeting — all go through IOAPIC routing

INTx Disable: Bit 10 of Command register (0x04).
  When set, device must not send INTx messages.
  Required when MSI or MSI-X is enabled.
```

### MSI (Message Signaled Interrupts)

```
MSI replaces INTx with memory writes. The device writes a
specific data value to a specific memory address, which the
interrupt controller (LAPIC) interprets as an interrupt.

MSI Capability (ID 0x05):

Without per-vector masking, 32-bit:
+0x00: [Next Ptr | Cap ID=0x05]          2B
+0x02: Message Control                    2B
+0x04: Message Address (lower 32)         4B
+0x08: Message Data                       2B

With 64-bit addressing:
+0x00: [Next Ptr | Cap ID=0x05]          2B
+0x02: Message Control                    2B
+0x04: Message Address (lower 32)         4B
+0x08: Message Address (upper 32)         4B
+0x0C: Message Data                       2B

With per-vector masking and 64-bit:
+0x00: [Next Ptr | Cap ID=0x05]          2B
+0x02: Message Control                    2B
+0x04: Message Address (lower 32)         4B
+0x08: Message Address (upper 32)         4B
+0x0C: Message Data                       2B
+0x10: Mask Bits                          4B
+0x14: Pending Bits                       4B

Message Control (+0x02):
  Bit 0:     MSI Enable
  Bits 3:1:  Multiple Message Capable (log2: 000=1, 001=2,
             010=4, 011=8, 100=16, 101=32 vectors)
  Bits 6:4:  Multiple Message Enable (same encoding, <=Capable)
  Bit 7:     64-bit Address Capable
  Bit 8:     Per-Vector Masking Capable

Message Address (x86 LAPIC format):
  Bits 31:20 = 0xFEE (fixed prefix for LAPIC)
  Bits 19:12 = Destination APIC ID
  Bit 3      = Redirection Hint (RH)
  Bit 2      = Destination Mode (DM: 0=physical, 1=logical)

  Example: Target APIC ID 0x02, physical mode:
    Address = 0xFEE02000

Message Data (x86 format):
  Bits 7:0  = Vector number (0-255)
  Bits 10:8 = Delivery Mode (000=Fixed, 001=LowPri, etc.)
  Bit 14    = Level (1=Assert for edge-triggered)
  Bit 15    = Trigger Mode (0=Edge)

  Example: Vector 0x41, fixed delivery, edge:
    Data = 0x0041

Multiple MSI vectors:
  When Multiple Message Enable > 0, the device uses the low
  bits of Message Data as a sub-vector selector.
  E.g., with 4 vectors enabled, vectors are Data & ~0x3 | {0,1,2,3}.
  All vectors share the same address (same target CPU).

MSI operation:
  1. OS writes Message Address and Message Data to capability
  2. OS sets MSI Enable in Message Control
  3. OS disables INTx (sets bit 10 in Command register)
  4. When device needs to signal interrupt:
     - Device issues a Memory Write TLP:
       Address = Message Address
       Data = Message Data (with vector bits set)
     - This write targets the LAPIC MMIO region
     - LAPIC delivers the interrupt to the CPU
  5. No ack needed — MSI is edge-triggered by write
```

### MSI-X (Extended Message Signaled Interrupts)

```
MSI-X provides per-vector configuration, more vectors, and
per-vector masking. Preferred over MSI for modern devices.

MSI-X Capability (ID 0x11):

+0x00: [Next Ptr | Cap ID=0x11]            2B
+0x02: Message Control                      2B
+0x04: Table Offset / Table BIR             4B
+0x08: PBA Offset / PBA BIR                 4B

Message Control (+0x02):
  Bits 10:0:  Table Size (N-1 encoded; max 2047 → 2048 vectors)
  Bit 14:     Function Mask (mask all vectors at once)
  Bit 15:     MSI-X Enable

Table Offset/BIR (+0x04):
  Bits 2:0:   Table BIR (BAR Indicator Register — which BAR
              contains the MSI-X table)
  Bits 31:3:  Table Offset (byte offset within the BAR,
              8-byte aligned)

PBA Offset/BIR (+0x08):
  Bits 2:0:   PBA BIR (which BAR contains the Pending Bit Array)
  Bits 31:3:  PBA Offset (byte offset within the BAR,
              8-byte aligned)

MSI-X Table Entry (16 bytes per vector, in BAR space):
Byte Offset   Field
───────────   ─────
  +0x00       Message Address Low (32 bits)
  +0x04       Message Address High (32 bits)
  +0x08       Message Data (32 bits)
  +0x0C       Vector Control (32 bits)
                Bit 0: Mask Bit (1=masked, 0=unmasked)
                Bits 31:1: Reserved

  Each entry is independently programmable:
    - Different address (different target CPU)
    - Different data (different vector number)
    - Independent mask

PBA (Pending Bit Array):
  One bit per vector. If the vector is masked and the device
  wants to signal it, the corresponding PBA bit is set.
  When the vector is unmasked, if PBA bit is set, the
  interrupt is delivered immediately.

  PBA is read-only from software's perspective.

MSI-X operation:
  1. OS reads Table Size from Message Control
  2. OS maps the BAR containing the MSI-X table
  3. For each vector:
     - Write Message Address (LAPIC target)
     - Write Message Data (vector number)
     - Clear Mask Bit in Vector Control
  4. Set MSI-X Enable in Message Control
  5. Disable INTx in Command register
  6. When device signals vector N:
     - Device reads table entry N
     - Issues Memory Write TLP with address/data from entry
     - If entry is masked, sets PBA bit N instead

Example MSI-X table for NVMe (4 queues + admin):
  Vector 0: Admin completion queue → CPU 0, vector 0x30
  Vector 1: I/O queue 1 completion → CPU 1, vector 0x31
  Vector 2: I/O queue 2 completion → CPU 2, vector 0x32
  Vector 3: I/O queue 3 completion → CPU 3, vector 0x33
  Vector 4: I/O queue 4 completion → CPU 4, vector 0x34

  Each I/O queue's completion interrupt goes directly to
  the CPU processing that queue — no interrupt routing overhead.
```

### Interrupt Delivery Path

```
Device → Root Complex → Interrupt Controller → CPU

For MSI/MSI-X:
  Device writes to LAPIC address → RC decodes the write
  as a local interrupt delivery (address in 0xFEExxxxx range)
  → LAPIC on target CPU receives the interrupt → CPU
  vectors to ISR.

For INTx:
  Device sends Assert_INTx message → RC routes to IOAPIC
  input → IOAPIC sends interrupt to target LAPIC →
  CPU vectors to ISR → ISR services device and clears
  interrupt → Device sends Deassert_INTx.

In virtualization (with interrupt remapping):
  VT-d Interrupt Remapping:
    Device write to LAPIC address intercepted by IOMMU.
    IOMMU looks up the Interrupt Remapping Table Entry (IRTE)
    using the interrupt index (from address/data fields).
    IRTE specifies destination VCPU and vector.
    IOMMU translates and delivers to the correct VCPU.

  Posted Interrupts (VT-d PI):
    IOMMU can post the interrupt directly to the VCPU's
    Posted Interrupt Descriptor (PID) in memory.
    If VCPU is running, interrupt delivered without VM exit.
    If VCPU is not running, notification event triggers
    the VMM to schedule the VCPU.
    Eliminates VM exit overhead for interrupt delivery.
```

---

## 10. Enumeration and Resource Assignment

### Bus Enumeration Algorithm

Firmware (BIOS/UEFI) or the OS enumerates PCIe devices using a **depth-first search (DFS)**:

```
Algorithm: enumerate(bus_number)
  bus_available = bus_number + 1
  for dev = 0 to 31:
    for func = 0 to 7:
      vendor_id = config_read16(bus_number, dev, func, 0x00)
      if vendor_id == 0xFFFF:
        if func == 0: break  // no device at this dev#
        continue             // no function at this func#

      header_type = config_read8(bus_number, dev, func, 0x0E)

      if (header_type & 0x7F) == 0x01:
        // This is a bridge — recurse
        // Set primary bus = bus_number
        config_write8(bus_number, dev, func, 0x18, bus_number)
        // Set secondary bus = bus_available
        config_write8(bus_number, dev, func, 0x19, bus_available)
        // Temporarily set subordinate to max
        config_write8(bus_number, dev, func, 0x1A, 0xFF)

        // Recurse into the secondary bus
        bus_available = enumerate(bus_available)

        // Now set subordinate to the actual max bus found
        config_write8(bus_number, dev, func, 0x1A, bus_available - 1)
      else:
        // Endpoint — size BARs, assign resources
        assign_bars(bus_number, dev, func)
        assign_interrupts(bus_number, dev, func)

      if func == 0 && !(header_type & 0x80):
        break  // Single-function device, skip func 1-7

  return bus_available

// Start enumeration from bus 0
enumerate(0)
```

### BAR Assignment Strategy

```
Firmware/OS assigns BARs after sizing:

1. Size all BARs for all devices
2. Sort BARs by size (largest first) — ensures alignment
3. Allocate from the MMIO address pool:
   - 32-bit BARs: allocate from below 4 GB
   - 64-bit BARs: can allocate from above 4 GB
   - I/O BARs: allocate from the I/O port range (limited!)
4. Program bridge windows to encompass all downstream BARs

MMIO pool typically:
  Below 4 GB: varies by platform, often 0xC000_0000-0xFEFF_FFFF
  Above 4 GB: large pool, platform-specific

Address alignment requirement:
  BAR address must be naturally aligned to its size.
  A 1 MB BAR must start at a 1 MB boundary.
  This is enforced by the BAR's hardwired low bits.

Linux kernel functions:
  pci_scan_bus()          — trigger enumeration
  pci_read_config_*()     — read config space
  pci_assign_resource()   — assign BAR addresses
  pci_enable_device()     — enable memory/IO access in Command reg
  pci_set_master()        — enable Bus Master in Command reg
```

### ACPI Tables for PCIe

```
MCFG (Memory-mapped Configuration Table):
  Provides ECAM base addresses for each PCI segment group.
  struct acpi_mcfg_allocation {
      uint64_t base_address;    // ECAM base physical address
      uint16_t segment_group;   // PCI segment group number
      uint8_t  start_bus;       // first bus number
      uint8_t  end_bus;         // last bus number
      uint32_t reserved;
  };

_DSM (Device Specific Method):
  ACPI method for device-specific operations.
  Used for PCIe features like hotplug, power management.

_OSC (Operating System Capabilities):
  Firmware and OS negotiate control of PCIe features:
  - Native PCIe hotplug control
  - SHPC (Standard Hot-Plug Controller)
  - PCIe native power management events
  - PCIe AER control
  - PCIe Capability Structure control

_HPP (Hot-Plug Parameters):
  Provides default PCIe settings for hot-plugged devices.
```

---

## 11. PCIe in the Linux Kernel

### Key Source Files

```
drivers/pci/
  ├── pci.c              — Core PCI functions
  ├── pci-driver.c       — Driver model (probe/remove)
  ├── probe.c            — Device enumeration
  ├── setup-bus.c        — Resource assignment
  ├── msi/               — MSI/MSI-X subsystem
  │   ├── msi.c
  │   └── irqdomain.c
  ├── pcie/              — PCIe-specific
  │   ├── portdrv.c      — Port service driver
  │   ├── aer.c          — Advanced Error Reporting
  │   ├── dpc.c          — Downstream Port Containment
  │   ├── pme.c          — Power Management Events
  │   └── aspm.c         — ASPM control
  ├── hotplug/           — Hotplug drivers
  │   ├── pciehp_core.c  — PCIe native hotplug
  │   └── acpiphp_core.c — ACPI-based hotplug
  ├── iov.c              — SR-IOV support
  ├── ecam.c             — ECAM config space access
  ├── access.c           — Config space read/write
  └── host/              — Host bridge drivers (per-platform)

include/linux/pci.h      — struct pci_dev, pci_driver, APIs
include/uapi/linux/pci_regs.h — All PCI register offset #defines
```

### Key Data Structures

```c
struct pci_dev {
    struct list_head bus_list;   // linked into pci_bus->devices
    struct pci_bus *bus;         // bus this device is on
    struct pci_bus *subordinate; // bus behind this bridge (if bridge)

    unsigned int devfn;          // encoded device + function
    unsigned short vendor;       // from config space
    unsigned short device;
    unsigned short subsystem_vendor;
    unsigned short subsystem_device;
    unsigned int class;          // class code (24 bits)
    u8 revision;

    u8 hdr_type;                 // 0=endpoint, 1=bridge
    u16 pcie_cap;                // offset of PCIe capability

    struct resource resource[PCI_NUM_RESOURCES]; // BAR resources
    // resource[0..5] = standard BARs
    // resource[6]    = expansion ROM
    // resource[7..N] = bridge windows, etc.

    unsigned int irq;            // IRQ number (legacy)
    bool msi_enabled;
    bool msix_enabled;

    struct pci_driver *driver;   // bound driver
    void *driver_data;           // private data for driver

    u8 pm_cap;                   // offset of PM capability
    unsigned int d3hot_delay;    // D3hot->D0 transition delay

    pci_power_t current_state;   // D0, D1, D2, D3hot, D3cold

    // SR-IOV
    struct pci_sriov *sriov;     // SR-IOV capability info
    u16 sriov_initial_vfs;

    // Error handling
    struct aer_stats *aer_stats;

    // DMA
    u64 dma_mask;                // DMA address mask
};

struct pci_driver {
    const char *name;
    const struct pci_device_id *id_table;  // match table

    int  (*probe)(struct pci_dev *dev, const struct pci_device_id *id);
    void (*remove)(struct pci_dev *dev);
    int  (*suspend)(struct pci_dev *dev, pm_message_t state);
    int  (*resume)(struct pci_dev *dev);
    void (*shutdown)(struct pci_dev *dev);

    int  (*sriov_configure)(struct pci_dev *dev, int num_vfs);

    const struct pci_error_handlers *err_handler;

    struct device_driver driver;  // embedded generic driver
};

struct pci_device_id {
    __u32 vendor, device;          // PCI_ANY_ID = match any
    __u32 subvendor, subdevice;
    __u32 class, class_mask;
    kernel_ulong_t driver_data;
};
```

### Writing a PCI Driver — Skeleton

```c
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/interrupt.h>

#define MY_VENDOR_ID  0x1234
#define MY_DEVICE_ID  0x5678

struct my_device {
    struct pci_dev *pdev;
    void __iomem *bar0;     // mapped BAR0
    void __iomem *bar2;     // mapped BAR2
    int irq_count;
};

static const struct pci_device_id my_pci_ids[] = {
    { PCI_DEVICE(MY_VENDOR_ID, MY_DEVICE_ID) },
    { 0, }  // terminator
};
MODULE_DEVICE_TABLE(pci, my_pci_ids);

static irqreturn_t my_irq_handler(int irq, void *data)
{
    struct my_device *mydev = data;
    u32 status = readl(mydev->bar0 + 0x08);  // read ISR register
    if (!(status & 0x1))
        return IRQ_NONE;  // not our interrupt

    writel(status, mydev->bar0 + 0x08);  // clear interrupt
    mydev->irq_count++;
    return IRQ_HANDLED;
}

static int my_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
    struct my_device *mydev;
    int err;

    // 1. Allocate driver private data
    mydev = kzalloc(sizeof(*mydev), GFP_KERNEL);
    if (!mydev)
        return -ENOMEM;
    mydev->pdev = pdev;
    pci_set_drvdata(pdev, mydev);

    // 2. Enable the PCI device (power on, enable config space)
    err = pci_enable_device(pdev);
    if (err)
        goto err_free;

    // 3. Request MMIO regions (prevents conflicts)
    err = pci_request_regions(pdev, "my_driver");
    if (err)
        goto err_disable;

    // 4. Map BAR0 into kernel virtual address space
    mydev->bar0 = pci_iomap(pdev, 0, 0);  // BAR 0, map entire BAR
    if (!mydev->bar0) {
        err = -ENOMEM;
        goto err_release;
    }

    // 5. Enable bus mastering (for DMA)
    pci_set_master(pdev);

    // 6. Set DMA mask
    err = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
    if (err) {
        err = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(32));
        if (err)
            goto err_unmap;
    }

    // 7. Allocate MSI-X vectors (or fall back to MSI)
    err = pci_alloc_irq_vectors(pdev, 1, 16,
                                PCI_IRQ_MSIX | PCI_IRQ_MSI);
    if (err < 0)
        goto err_unmap;

    // 8. Request IRQ for vector 0
    err = request_irq(pci_irq_vector(pdev, 0), my_irq_handler,
                      0, "my_driver", mydev);
    if (err)
        goto err_free_irq_vectors;

    // 9. Device-specific initialization
    writel(0x01, mydev->bar0 + 0x00);  // enable device
    readl(mydev->bar0 + 0x00);         // flush posted write

    dev_info(&pdev->dev, "device initialized\n");
    return 0;

err_free_irq_vectors:
    pci_free_irq_vectors(pdev);
err_unmap:
    pci_iounmap(pdev, mydev->bar0);
err_release:
    pci_release_regions(pdev);
err_disable:
    pci_disable_device(pdev);
err_free:
    kfree(mydev);
    return err;
}

static void my_remove(struct pci_dev *pdev)
{
    struct my_device *mydev = pci_get_drvdata(pdev);

    // 1. Disable device-level interrupts
    writel(0x00, mydev->bar0 + 0x04);  // disable interrupts
    readl(mydev->bar0 + 0x04);         // flush

    // 2. Free IRQ
    free_irq(pci_irq_vector(pdev, 0), mydev);

    // 3. Free MSI/MSI-X vectors
    pci_free_irq_vectors(pdev);

    // 4. Unmap BARs
    pci_iounmap(pdev, mydev->bar0);

    // 5. Release regions
    pci_release_regions(pdev);

    // 6. Disable PCI device
    pci_disable_device(pdev);

    // 7. Free private data
    kfree(mydev);
}

static struct pci_driver my_pci_driver = {
    .name     = "my_driver",
    .id_table = my_pci_ids,
    .probe    = my_probe,
    .remove   = my_remove,
};

module_pci_driver(my_pci_driver);
MODULE_LICENSE("GPL");
```

### DMA Mapping APIs

```c
// Coherent (consistent) DMA mapping — kernel manages cache coherency
// Use for long-lived structures (descriptor rings, command queues)
void *virt = dma_alloc_coherent(&pdev->dev, size, &dma_handle, GFP_KERNEL);
// virt: kernel virtual address
// dma_handle: DMA address the device should use
// CPU writes are immediately visible to device (no explicit flush)
dma_free_coherent(&pdev->dev, size, virt, dma_handle);

// Streaming DMA mapping — for transient buffers (data payloads)
// Must explicitly sync before/after device access
dma_addr_t dma = dma_map_single(&pdev->dev, cpu_addr, size,
                                DMA_TO_DEVICE);     // CPU → device
                             // DMA_FROM_DEVICE);   // device → CPU
                             // DMA_BIDIRECTIONAL);
if (dma_mapping_error(&pdev->dev, dma)) { /* handle error */ }

// After device finishes DMA (before CPU reads the buffer):
dma_sync_single_for_cpu(&pdev->dev, dma, size, DMA_FROM_DEVICE);

// Before giving buffer back to device (after CPU writes):
dma_sync_single_for_device(&pdev->dev, dma, size, DMA_TO_DEVICE);

dma_unmap_single(&pdev->dev, dma, size, direction);

// Scatter-gather DMA (for multiple non-contiguous buffers)
int nents = dma_map_sg(&pdev->dev, sglist, nents_orig, direction);
// nents may be less than nents_orig (IOMMU coalescing)
for_each_sg(sglist, sg, nents, i) {
    dma_addr = sg_dma_address(sg);
    dma_len  = sg_dma_len(sg);
    // Program device DMA descriptor with dma_addr, dma_len
}
dma_unmap_sg(&pdev->dev, sglist, nents_orig, direction);
```

### sysfs Interface

```
/sys/bus/pci/devices/0000:03:00.0/
  ├── vendor          — 0x8086 (Intel)
  ├── device          — 0x1572 (E810 NIC)
  ├── class           — 0x020000 (Network controller)
  ├── subsystem_vendor
  ├── subsystem_device
  ├── revision
  ├── config          — Binary access to 4096-byte config space
  ├── resource        — Text: BAR start, end, flags for each BAR
  ├── resource0       — Binary mmap of BAR0
  ├── resource0_wc    — BAR0 with write-combining (if prefetchable)
  ├── resource2       — Binary mmap of BAR2
  ├── rom             — Expansion ROM (write 1 to enable, then read)
  ├── irq             — IRQ number
  ├── local_cpus      — CPU affinity mask
  ├── local_cpulist   — CPU affinity list
  ├── numa_node       — NUMA node
  ├── enable          — Enable/disable device
  ├── remove          — Hot-remove device
  ├── rescan          — Rescan for new devices
  ├── driver/         — Symlink to bound driver
  ├── driver_override — Force specific driver binding
  ├── iommu_group/    — Symlink to IOMMU group
  ├── msi_irqs/       — Directory of assigned MSI/MSI-X IRQs
  ├── sriov_numvfs    — Set number of SR-IOV VFs
  ├── sriov_totalvfs  — Max VFs supported
  ├── sriov_vf_total_msix — Total MSI-X vectors for VFs
  └── power/          — Power management attributes

Interpreting lspci output:
  $ lspci -vvv -s 03:00.0
    03:00.0 Network controller: Intel Corporation ...
      Control: I/O- Mem+ BusMaster+ SpecCycle- ...
      Status: Cap+ 66MHz- ... <MAbort- >SERR- <PERR- ...
      Latency: 0
      Interrupt: pin A routed to IRQ 37
      Region 0: Memory at f0000000 (64-bit, non-prefetchable) [size=16M]
      Region 3: Memory at f1000000 (64-bit, prefetchable) [size=32K]
      Capabilities: [40] Power Management ...
      Capabilities: [50] MSI-X: Enable+ Count=129 Masked-
        Vector table: BAR=3 offset=00000000
        PBA: BAR=3 offset=00001000
      Capabilities: [60] Express Endpoint, MSI 00
        DevCap: MaxPayload 512 bytes, PhantFuncEn 0, ExtTag+ ...
        DevCtl: MaxPayload 256 bytes, MaxReadReq 512 bytes
        LnkCap: Speed 8GT/s, Width x8, ASPM L1
        LnkSta: Speed 8GT/s, Width x8
      Capabilities: [100] Advanced Error Reporting
      Capabilities: [140] SR-IOV: ...
        IOVCap: ...
        IOVCtl: Enable- ...
        NumVFs 0, TotalVFs 64, ...
        VF BAR0: Memory at f2000000 (64-bit, non-pref) [size=16K]

  $ setpci -s 03:00.0 COMMAND  # Read Command register
    0407
  $ setpci -s 03:00.0 COMMAND=0007  # Write Command register
```

### PCIe Error Recovery in Linux

```c
// Error recovery callbacks in struct pci_error_handlers:
static pci_ers_result_t my_error_detected(struct pci_dev *pdev,
                                           pci_channel_state_t state)
{
    struct my_device *mydev = pci_get_drvdata(pdev);

    if (state == pci_channel_io_perm_failure)
        return PCI_ERS_RESULT_DISCONNECT;

    // Stop all DMA and I/O
    my_stop_dma(mydev);

    // If device memory is still accessible:
    if (state == pci_channel_io_normal)
        return PCI_ERS_RESULT_CAN_RECOVER;

    // If device memory is frozen (reads return 0xFFFFFFFF):
    return PCI_ERS_RESULT_NEED_RESET;
}

static pci_ers_result_t my_slot_reset(struct pci_dev *pdev)
{
    struct my_device *mydev = pci_get_drvdata(pdev);

    // Re-enable the device after reset
    pci_restore_state(pdev);
    pci_enable_device(pdev);
    pci_set_master(pdev);

    // Re-initialize device hardware
    my_hw_init(mydev);

    return PCI_ERS_RESULT_RECOVERED;
}

static void my_resume(struct pci_dev *pdev)
{
    // Resume normal operations
    struct my_device *mydev = pci_get_drvdata(pdev);
    my_resume_io(mydev);
}

static const struct pci_error_handlers my_err_handlers = {
    .error_detected = my_error_detected,
    .slot_reset     = my_slot_reset,
    .resume         = my_resume,
};

// In pci_driver:
static struct pci_driver my_driver = {
    ...
    .err_handler = &my_err_handlers,
};
```

---

## 12. PCIe Device Emulation for VMMs

### Architecture Overview

When building a VMM (like QEMU, Cloud Hypervisor, Firecracker, or crosvm), you must emulate PCIe devices for the guest. This involves:

```
┌──────────────────────────────────────────────────────────┐
│  Guest VM                                                │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Guest OS                                         │  │
│  │  ├── PCI driver (e.g., virtio-net)                │  │
│  │  │    reads/writes config space and BARs           │  │
│  │  │    programs MSI-X vectors                       │  │
│  │  │    submits DMA descriptors                      │  │
│  │  └── PCI subsystem (enumeration, resource assign) │  │
│  └──────────┬───────────┬───────────────────────────┘  │
│             │           │                               │
│      Config access   MMIO access                        │
│      (port I/O or    (BAR reads/                        │
│       ECAM trap)      writes trap)                      │
│             │           │                               │
├─────────────┼───────────┼───────────────────────────────┤
│  VMM (Host userspace)   │                               │
│  ┌──────────▼───────────▼───────────────────────────┐  │
│  │  PCI Device Model                                │  │
│  │  ┌──────────────┐  ┌──────────────────────────┐  │  │
│  │  │ Config Space │  │ BAR MMIO Handlers        │  │  │
│  │  │ (4096 bytes) │  │ (device register logic)  │  │  │
│  │  └──────────────┘  └──────────────────────────┘  │  │
│  │  ┌──────────────┐  ┌──────────────────────────┐  │  │
│  │  │ MSI-X Table  │  │ Interrupt Injection      │  │  │
│  │  │ (in BAR)     │  │ (KVM irqfd / irqchip)    │  │  │
│  │  └──────────────┘  └──────────────────────────┘  │  │
│  └──────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### Emulating Configuration Space

```c
// Configuration space is a 4096-byte array per device.
// The VMM pre-populates standard fields and handles
// reads/writes with appropriate semantics.

struct emulated_pci_device {
    uint8_t config_space[4096];  // full config space

    // BAR state
    struct {
        uint64_t addr;           // current mapped address
        uint64_t size;           // BAR size
        uint8_t  type;           // memory/IO, 32/64-bit, prefetchable
        bool     is_upper;       // true if this is upper half of 64-bit
    } bars[6];

    // MSI-X state
    struct {
        bool enabled;
        uint16_t table_size;     // number of vectors
        struct msix_entry {
            uint64_t addr;
            uint32_t data;
            bool masked;
        } *table;
        uint64_t *pba;           // pending bit array
    } msix;

    // Capability list
    // ...
};

// Initialize standard config space fields:
void init_config_space(struct emulated_pci_device *dev) {
    // Vendor ID / Device ID
    *(uint16_t *)(dev->config_space + 0x00) = htole16(VENDOR_ID);
    *(uint16_t *)(dev->config_space + 0x02) = htole16(DEVICE_ID);

    // Command: start with everything disabled
    *(uint16_t *)(dev->config_space + 0x04) = 0x0000;

    // Status: capabilities list present
    *(uint16_t *)(dev->config_space + 0x06) = htole16(1 << 4);

    // Class code
    dev->config_space[0x09] = PROG_IF;
    dev->config_space[0x0A] = SUBCLASS;
    dev->config_space[0x0B] = CLASS_CODE;

    // Header type (endpoint, single-function)
    dev->config_space[0x0E] = 0x00;

    // Capabilities pointer (offset of first capability)
    dev->config_space[0x34] = FIRST_CAP_OFFSET;

    // Initialize BARs (write size mask, firmware will size them)
    // BAR0: 64-bit, non-prefetchable, 16 KB
    dev->bars[0].size = 16384;
    dev->bars[0].type = 0x04;  // 64-bit memory
    // BAR register initially shows the type bits, address = 0
    *(uint32_t *)(dev->config_space + 0x10) = htole32(0x04);
    *(uint32_t *)(dev->config_space + 0x14) = 0;
    dev->bars[1].is_upper = true;
}

// Handle config space read:
uint32_t config_read(struct emulated_pci_device *dev,
                     uint16_t offset, uint8_t size) {
    uint32_t val = 0;

    // Some registers have special read behavior
    switch (offset) {
    case 0x10: case 0x14: case 0x18: case 0x1C:
    case 0x20: case 0x24:
        // BAR read — return current address | type bits
        val = *(uint32_t *)(dev->config_space + offset);
        break;
    default:
        memcpy(&val, dev->config_space + offset, size);
        break;
    }
    return val;
}

// Handle config space write:
void config_write(struct emulated_pci_device *dev,
                  uint16_t offset, uint32_t val, uint8_t size) {
    switch (offset) {
    case 0x04: {  // Command register
        uint16_t cmd = (uint16_t)val;
        uint16_t old = *(uint16_t *)(dev->config_space + 0x04);

        // Only allow writable bits
        uint16_t writable = 0x0447;  // IO, Mem, BusMaster, INTx Disable, etc.
        cmd = (old & ~writable) | (cmd & writable);
        *(uint16_t *)(dev->config_space + 0x04) = cmd;

        // React to changes:
        if ((cmd & 0x02) && !(old & 0x02)) {
            // Memory space just enabled — register MMIO regions
            register_mmio_regions(dev);
        }
        if (!(cmd & 0x02) && (old & 0x02)) {
            // Memory space disabled — unregister MMIO regions
            unregister_mmio_regions(dev);
        }
        break;
    }
    case 0x10: case 0x14: case 0x18: case 0x1C:
    case 0x20: case 0x24: {
        // BAR write — handle sizing and address assignment
        int bar_idx = (offset - 0x10) / 4;
        handle_bar_write(dev, bar_idx, val);
        break;
    }
    // ... handle capability writes (MSI-X enable, etc.)
    default:
        memcpy(dev->config_space + offset, &val, size);
        break;
    }
}

// BAR write handling (critical for firmware interaction):
void handle_bar_write(struct emulated_pci_device *dev,
                      int idx, uint32_t val) {
    if (dev->bars[idx].is_upper) {
        // Upper 32 bits of 64-bit BAR
        if (val == 0xFFFFFFFF) {
            // Sizing: return upper size mask
            uint64_t mask = ~(dev->bars[idx-1].size - 1);
            *(uint32_t *)(dev->config_space + 0x10 + idx*4) =
                htole32((uint32_t)(mask >> 32));
        } else {
            dev->bars[idx-1].addr = (dev->bars[idx-1].addr & 0xFFFFFFFF)
                                  | ((uint64_t)val << 32);
            *(uint32_t *)(dev->config_space + 0x10 + idx*4) = htole32(val);
            // Re-register MMIO region at new address
            update_mmio_mapping(dev, idx-1);
        }
        return;
    }

    if (val == 0xFFFFFFFF) {
        // Sizing: return size mask with type bits
        uint64_t mask = ~(dev->bars[idx].size - 1);
        uint32_t type_bits = dev->bars[idx].type;
        *(uint32_t *)(dev->config_space + 0x10 + idx*4) =
            htole32((uint32_t)(mask & 0xFFFFFFF0) | type_bits);
    } else {
        // Address assignment
        dev->bars[idx].addr = (dev->bars[idx].addr & 0xFFFFFFFF00000000ULL)
                            | (val & 0xFFFFFFF0);
        *(uint32_t *)(dev->config_space + 0x10 + idx*4) =
            htole32((val & 0xFFFFFFF0) | dev->bars[idx].type);
        update_mmio_mapping(dev, idx);
    }
}
```

### Emulating MSI-X

```c
// MSI-X table lives in a BAR. When the guest reads/writes
// the MSI-X table region, the VMM traps the access.

void msix_table_write(struct emulated_pci_device *dev,
                      uint64_t offset, uint64_t val, uint8_t size) {
    int vector = offset / 16;    // 16 bytes per entry
    int field  = offset % 16;    // which field within entry

    if (vector >= dev->msix.table_size) return;

    struct msix_entry *entry = &dev->msix.table[vector];

    switch (field) {
    case 0x00:  // Message Address Low
        entry->addr = (entry->addr & 0xFFFFFFFF00000000ULL) | (val & 0xFFFFFFFC);
        break;
    case 0x04:  // Message Address High
        entry->addr = (entry->addr & 0x00000000FFFFFFFF) | ((uint64_t)val << 32);
        break;
    case 0x08:  // Message Data
        entry->data = (uint32_t)val;
        break;
    case 0x0C:  // Vector Control
        entry->masked = (val & 1);
        if (!entry->masked && (dev->msix.pba[vector / 64] & (1ULL << (vector % 64)))) {
            // Was pending and now unmasked — deliver interrupt
            dev->msix.pba[vector / 64] &= ~(1ULL << (vector % 64));
            inject_interrupt(dev, vector);
        }
        break;
    }

    // Update KVM irqfd routing if address/data changed
    if (field <= 0x08 && !entry->masked) {
        update_irq_routing(dev, vector, entry->addr, entry->data);
    }
}

// Interrupt injection into guest (via KVM):
void inject_interrupt(struct emulated_pci_device *dev, int vector) {
    struct msix_entry *entry = &dev->msix.table[vector];

    if (entry->masked) {
        // Set pending bit
        dev->msix.pba[vector / 64] |= (1ULL << (vector % 64));
        return;
    }

    // Method 1: Direct KVM ioctl (slow, causes VM exit)
    struct kvm_irq_level irq = {
        .irq = vector,
        .level = 1,
    };
    ioctl(kvm_fd, KVM_IRQ_LINE, &irq);

    // Method 2: irqfd (fast, no VM exit needed)
    // Pre-register: associate eventfd with MSI address/data
    struct kvm_irqfd irqfd = {
        .fd    = eventfd_create(0, EFD_NONBLOCK),
        .gsi   = gsi_number,  // global system interrupt
        .flags = 0,
    };
    ioctl(kvm_fd, KVM_IRQFD, &irqfd);
    // Now writing 1 to the eventfd triggers the interrupt

    // Method 3: MSI routing (KVM_SET_GSI_ROUTING)
    // Map GSI to MSI address/data, then use irqfd
    struct kvm_msi msi = {
        .address_lo = (uint32_t)entry->addr,
        .address_hi = (uint32_t)(entry->addr >> 32),
        .data       = entry->data,
    };
    ioctl(kvm_fd, KVM_SIGNAL_MSI, &msi);
}
```

### Virtio over PCI

Virtio-PCI is the most common transport for virtual devices. It uses PCIe capabilities to expose virtio configuration.

```
Virtio PCI Capability Structure (in PCI config space):

Each virtio-pci capability is a standard PCI capability with
Cap ID = 0x09 (Vendor-Specific) containing:

struct virtio_pci_cap {
    uint8_t cap_vndr;     // 0x09 (PCI_CAP_ID_VNDR)
    uint8_t cap_next;     // Offset of next capability
    uint8_t cap_len;      // Length of this capability
    uint8_t cfg_type;     // Type of virtio structure:
                          //   1 = VIRTIO_PCI_CAP_COMMON_CFG
                          //   2 = VIRTIO_PCI_CAP_NOTIFY_CFG
                          //   3 = VIRTIO_PCI_CAP_ISR_CFG
                          //   4 = VIRTIO_PCI_CAP_DEVICE_CFG
                          //   5 = VIRTIO_PCI_CAP_PCI_CFG
    uint8_t bar;          // BAR containing the structure
    uint8_t id;           // Multiple instances of same type
    uint8_t padding[2];
    uint32_t offset;      // Offset within BAR
    uint32_t length;      // Length of structure
};

Five capability types:

1. Common Configuration (cfg_type=1):
   Mapped in a BAR. Contains:
     - device_feature_select / device_feature  — feature negotiation
     - driver_feature_select / driver_feature
     - msix_config                             — config MSI-X vector
     - num_queues                              — number of virtqueues
     - device_status                           — status byte
     - config_generation
     - queue_select / queue_size / queue_msix_vector
     - queue_enable / queue_notify_off
     - queue_desc / queue_driver / queue_device — virtqueue addresses

2. Notification (cfg_type=2):
   Extended structure with:
     struct virtio_pci_notify_cap {
         struct virtio_pci_cap cap;
         uint32_t notify_off_multiplier;  // multiplier for queue offset
     };

   To notify queue Q:
     Write queue index to:
       BAR[cap.bar] + cap.offset + queue_notify_off * notify_off_multiplier

   The VMM traps this MMIO write using ioeventfd:
     KVM_IOEVENTFD maps a specific MMIO address to an eventfd.
     When guest writes to the doorbell address, KVM signals the
     eventfd without VM exit. The VMM backend thread reads the
     eventfd and processes the virtqueue.

3. ISR Status (cfg_type=3):
   Single byte register for legacy interrupt status.
   Bit 0: virtqueue interrupt
   Bit 1: device configuration change

4. Device Configuration (cfg_type=4):
   Device-type-specific configuration (e.g., MAC address for
   virtio-net, capacity for virtio-blk). Format varies by device.

5. PCI Configuration Access (cfg_type=5):
   Allows accessing the other structures via PCI config space
   reads/writes (for platforms that can't easily mmap BARs).

Modern Virtio (1.0+) vs Legacy:
  Modern: Uses PCI capabilities above. Guest discovers structures
          via capability list walking. Clean, extensible.
  Legacy: Fixed layout in BAR0:
          Offset 0x00-0x13: Common header
          Offset 0x14+: Device-specific config
          No capabilities. BAR0 I/O port space.
          Still supported for compatibility.
```

### Implementing a Minimal PCI Device in a VMM

```c
// Minimal example: a PCI device that exposes a 256-byte
// register space with a writable scratch register and
// an interrupt-on-write register.

struct minimal_pci_dev {
    uint8_t config[4096];
    uint8_t regs[256];           // device registers in BAR0
    int kvm_fd;
    int irqfd;                   // eventfd for MSI-X vector 0
    struct msix_entry msix[1];   // single MSI-X vector
};

// Step 1: Initialize config space
void minimal_init(struct minimal_pci_dev *dev) {
    memset(dev->config, 0, sizeof(dev->config));

    // Vendor=0x1AF4 (Red Hat), Device=0x1000
    put_le16(dev->config + 0x00, 0x1AF4);
    put_le16(dev->config + 0x02, 0x1000);

    // Status: capabilities list present
    put_le16(dev->config + 0x06, 0x0010);

    // Class: Unclassified (0xFF)
    dev->config[0x0B] = 0xFF;

    // Header type 0, single function
    dev->config[0x0E] = 0x00;

    // BAR0: 256 bytes, 32-bit, non-prefetchable
    // (size mask = ~(256-1) = 0xFFFFFF00)
    put_le32(dev->config + 0x10, 0x00);  // type = memory 32-bit

    // Capabilities pointer → offset 0x40
    dev->config[0x34] = 0x40;

    // MSI-X capability at offset 0x40
    dev->config[0x40] = 0x11;    // Cap ID = MSI-X
    dev->config[0x41] = 0x00;    // Next = end of list
    put_le16(dev->config + 0x42, 0x0000);  // 1 vector (N-1=0), disabled
    put_le32(dev->config + 0x44, 0x00000100 | 0);  // Table at BAR0+0x100
    put_le32(dev->config + 0x48, 0x00000180 | 0);  // PBA at BAR0+0x180
}

// Step 2: Register with KVM
// - Map ECAM region for config space access (or use port I/O trap)
// - Set up ioeventfd for BAR0 MMIO writes
// - Set up irqfd for MSI-X interrupt injection

// Step 3: Handle MMIO reads/writes to BAR0
uint32_t bar0_read(struct minimal_pci_dev *dev, uint64_t offset) {
    if (offset < 0x100) {
        // Device registers
        return get_le32(dev->regs + offset);
    } else if (offset < 0x110) {
        // MSI-X table (1 entry = 16 bytes)
        int field = (offset - 0x100) % 16;
        switch (field) {
        case 0: return (uint32_t)dev->msix[0].addr;
        case 4: return (uint32_t)(dev->msix[0].addr >> 32);
        case 8: return dev->msix[0].data;
        case 12: return dev->msix[0].masked ? 1 : 0;
        }
    }
    return 0xFFFFFFFF;
}

void bar0_write(struct minimal_pci_dev *dev, uint64_t offset,
                uint32_t val) {
    if (offset == 0x04) {
        // Interrupt trigger register: writing any value
        // causes MSI-X vector 0 to fire
        uint64_t one = 1;
        write(dev->irqfd, &one, sizeof(one));  // trigger irqfd
    } else if (offset < 0x100) {
        put_le32(dev->regs + offset, val);
    } else if (offset < 0x110) {
        // MSI-X table write — update routing
        msix_table_write(dev, offset - 0x100, val);
    }
}
```

---

## 13. PCIe Passthrough and SR-IOV

### VFIO-Based PCIe Passthrough

```
Passthrough gives a guest VM direct access to a physical PCIe
device. The guest's driver talks directly to hardware — no
emulation overhead for data path operations.

Architecture:
  ┌──────────────────────────────────┐
  │ Guest VM                         │
  │   Guest driver ←→ Physical NIC   │  ← Direct MMIO + DMA
  └────────┬─────────────────────────┘
           │ (guest physical → host physical mapping)
  ┌────────┴─────────────────────────┐
  │ IOMMU (VT-d / AMD-Vi)           │  ← DMA isolation
  │   Translates guest DMA addresses │
  │   to host physical addresses     │
  └──────────────────────────────────┘

Steps for passthrough:
  1. Unbind device from host driver:
     echo 0000:03:00.0 > /sys/bus/pci/devices/0000:03:00.0/driver/unbind

  2. Bind to vfio-pci:
     echo 8086 1572 > /sys/bus/pci/drivers/vfio-pci/new_id
     # or: echo vfio-pci > /sys/bus/pci/devices/0000:03:00.0/driver_override
     #     echo 0000:03:00.0 > /sys/bus/pci/drivers_probe

  3. Open VFIO container and group:
     container_fd = open("/dev/vfio/vfio", O_RDWR);
     group_fd = open("/dev/vfio/42", O_RDWR);  // IOMMU group
     ioctl(group_fd, VFIO_GROUP_SET_CONTAINER, &container_fd);
     ioctl(container_fd, VFIO_SET_IOMMU, VFIO_TYPE1v2_IOMMU);

  4. Get device fd:
     device_fd = ioctl(group_fd, VFIO_GROUP_GET_DEVICE_FD, "0000:03:00.0");

  5. Map guest memory for DMA:
     struct vfio_iommu_type1_dma_map dma_map = {
         .vaddr = (uint64_t)guest_ram,
         .iova  = 0,               // guest physical address = 0
         .size  = guest_ram_size,
         .flags = VFIO_DMA_MAP_FLAG_READ | VFIO_DMA_MAP_FLAG_WRITE,
     };
     ioctl(container_fd, VFIO_IOMMU_MAP_DMA, &dma_map);

  6. Access config space:
     struct vfio_region_info config_info = {
         .argsz = sizeof(config_info),
         .index = VFIO_PCI_CONFIG_REGION_INDEX,
     };
     ioctl(device_fd, VFIO_DEVICE_GET_REGION_INFO, &config_info);
     // Read config: pread(device_fd, buf, 4, config_info.offset + reg);
     // Write config: pwrite(device_fd, &val, 4, config_info.offset + reg);

  7. Map BARs into guest:
     struct vfio_region_info bar_info = {
         .argsz = sizeof(bar_info),
         .index = VFIO_PCI_BAR0_REGION_INDEX,
     };
     ioctl(device_fd, VFIO_DEVICE_GET_REGION_INFO, &bar_info);
     void *bar0 = mmap(NULL, bar_info.size,
                       PROT_READ | PROT_WRITE, MAP_SHARED,
                       device_fd, bar_info.offset);
     // Then map this into guest address space via KVM_SET_USER_MEMORY_REGION

  8. Set up interrupts:
     struct vfio_irq_info irq_info = {
         .argsz = sizeof(irq_info),
         .index = VFIO_PCI_MSIX_IRQ_INDEX,
     };
     ioctl(device_fd, VFIO_DEVICE_GET_IRQ_INFO, &irq_info);

     int efd = eventfd(0, EFD_NONBLOCK);
     struct vfio_irq_set *irq_set = alloc_irq_set(1);
     irq_set->flags = VFIO_IRQ_SET_DATA_EVENTFD | VFIO_IRQ_SET_ACTION_TRIGGER;
     irq_set->index = VFIO_PCI_MSIX_IRQ_INDEX;
     irq_set->start = 0;
     irq_set->count = 1;
     *(int *)irq_set->data = efd;
     ioctl(device_fd, VFIO_DEVICE_SET_IRQS, irq_set);

     // Then connect this eventfd to KVM for interrupt injection:
     struct kvm_irqfd kvm_irqfd = { .fd = efd, .gsi = guest_gsi };
     ioctl(kvm_fd, KVM_IRQFD, &kvm_irqfd);
```

### Config Space Virtualization for Passthrough

```
Even in passthrough, config space access must be virtualized
(trapped by the VMM), not passed through directly. Reasons:

1. BAR addresses: Guest sees guest physical addresses, but
   host BARs are at different host physical addresses.
   The VMM must translate.

2. Bus numbers: Guest has its own PCI bus numbering.
   Requester ID in config space doesn't match host.

3. Capabilities: Some capabilities (ASPM, power management)
   must be controlled by the host, not the guest.

4. Security: Unrestricted config write could let guest
   disable IOMMU checks, change BARs to overlap with
   other devices, etc.

Typical virtualization strategy:
  - Vendor ID, Device ID, Class Code: Pass through from device
  - Command register: Virtualize. Track guest's view. Apply
    safe bits to hardware. Never let guest disable IOMMU
    features.
  - BARs: Show guest the guest physical addresses. When guest
    writes BAR (sizing or assignment), handle in VMM. The
    actual hardware BARs remain at host-assigned addresses.
  - Capabilities: Selective pass-through. MSI-X capability
    is intercepted (VMM manages interrupt routing). PCIe
    capability link status can be passed through (read-only).
    Power management: intercept to prevent guest from
    power-managing the device (host controls this).
  - Extended capabilities: AER status can be passed through.
    SR-IOV capability is hidden from VF guests.
```

### SR-IOV Architecture

```
SR-IOV splits a single physical device (Physical Function, PF)
into multiple Virtual Functions (VFs), each assignable to a
different VM.

┌──────────────────────────────────────────────────────────┐
│ Physical NIC (e.g., Intel E810)                          │
│                                                          │
│   PF (Bus:Dev.0)                                         │
│   ├── Full device functionality                          │
│   ├── SR-IOV capability                                  │
│   ├── Controls VF creation/destruction                   │
│   └── Manages shared resources (link, firmware, etc.)    │
│                                                          │
│   VF0 (Bus:Dev+offset.0)          VF1 (Bus:Dev+offset+stride.0)
│   ├── Lightweight PCIe function    ├── Lightweight PCIe function
│   ├── Own config space             ├── Own config space
│   ├── Own BARs (queue regs)        ├── Own BARs (queue regs)
│   ├── Own MSI-X vectors            ├── Own MSI-X vectors
│   └── Assigned to VM1              └── Assigned to VM2
│                                                          │
│   ... up to TotalVFs ...                                 │
└──────────────────────────────────────────────────────────┘

VF creation (Linux):
  # Check total VFs supported
  cat /sys/bus/pci/devices/0000:03:00.0/sriov_totalvfs
  64

  # Create 4 VFs
  echo 4 > /sys/bus/pci/devices/0000:03:00.0/sriov_numvfs

  # VFs appear as new PCI devices:
  # 0000:03:01.0  (VF0)
  # 0000:03:01.1  (VF1)
  # 0000:03:02.0  (VF2)
  # 0000:03:02.1  (VF3)

  # Each VF can be individually bound to vfio-pci for passthrough

VF properties:
  - VFs have no SR-IOV capability (only PF does)
  - VFs have limited config space (no power management control)
  - VF BARs are slices of the PF's VF BAR aperture
  - VFs share the physical link but have isolated queues
  - VFs have own MSI-X vectors (interrupt isolation)
  - VFs appear to guest as regular PCIe endpoints

Key advantage over full device passthrough:
  - Multiple VMs can share one physical device
  - Near-native performance (no emulation on data path)
  - Hardware-enforced isolation between VFs

Production examples:
  Intel E810 (100G NIC): 256 VFs per PF, 4 PFs
  Mellanox ConnectX-6: 1024 VFs per PF
  NVMe SSDs (some): VF support for storage isolation
  Intel QAT: VFs for crypto offload to VMs
```

### SR-IOV in Linux Kernel

```c
// PF driver implements sriov_configure callback:
static int my_sriov_configure(struct pci_dev *pdev, int num_vfs)
{
    if (num_vfs == 0) {
        // Disable SR-IOV
        pci_disable_sriov(pdev);
        // Free VF resources
        my_free_vf_resources(pdev);
        return 0;
    }

    // Allocate per-VF resources (queues, etc.)
    int err = my_alloc_vf_resources(pdev, num_vfs);
    if (err)
        return err;

    // Enable SR-IOV — creates VF PCIe functions
    err = pci_enable_sriov(pdev, num_vfs);
    if (err) {
        my_free_vf_resources(pdev);
        return err;
    }

    return num_vfs;
}

static struct pci_driver my_pf_driver = {
    .name             = "my_nic_pf",
    .id_table         = my_pf_ids,
    .probe            = my_pf_probe,
    .remove           = my_pf_remove,
    .sriov_configure  = my_sriov_configure,
};

// VF driver is a separate, simpler driver:
static struct pci_driver my_vf_driver = {
    .name     = "my_nic_vf",
    .id_table = my_vf_ids,  // matches VF device ID
    .probe    = my_vf_probe,
    .remove   = my_vf_remove,
};
```

---

## 14. Power Management

### ASPM (Active State Power Management)

```
ASPM allows PCIe links to enter low-power states during idle
periods, without software involvement.

L0: Active. Full bandwidth. Normal operation.
    No power savings. No latency.

L0s: Standby. TX lanes go electrically idle.
    Entry: Autonomous (hardware detects idle period).
    Exit latency: ~1 us (fast).
    Power savings: Moderate (TX PLL can idle).
    Per-direction: Each direction can enter L0s independently.
    Caveat: Short idle periods → frequent transitions → overhead.

L1: Low power. Both TX and RX quiesced. PLL may be off.
    Entry: Both ends must agree (via DLLP handshake):
      1. Upstream port sends PM_Enter_L1 DLLP
      2. Downstream port sends PM_Request_Ack DLLP
      3. Both enter L1
    Exit latency: 2-32 us (configurable).
    Power savings: Significant.
    Software can enable/disable via Link Control register.

L1 Substates (PCIe 3.1+, L1 PM Substates capability):

  L1.0: Standard L1. PHY powered, reference clock on.

  L1.1: PCI-PM L1. Reference clock can be gated.
        Additional power savings from clock gating.
        Exit latency: add clock stabilization time.

  L1.2: CLKREQ#-based L1. Common-mode voltage removed.
        Maximum power savings. Only logic for wake detection
        is powered.
        Exit latency: longest (up to 64 us).
        Requires CLKREQ# signal (not available on all platforms).

ASPM configuration:
  Link Control register bits 1:0:
    00 = ASPM disabled
    01 = L0s enabled
    10 = L1 enabled
    11 = L0s and L1 enabled

  Linux kernel: /sys/module/pcie_aspm/parameters/policy
    default, performance, powersave, powersupersave

  Gotcha: ASPM can cause latency spikes. Some NVMe drivers
  disable ASPM for consistent performance:
    pci_disable_link_state(pdev, PCIE_LINK_STATE_L0S |
                                  PCIE_LINK_STATE_L1);
```

### D-States

```
D-state transitions:

  ┌──────┐  software   ┌──────┐  software   ┌──────┐
  │  D0  │────────────►│  D1  │────────────►│  D2  │
  │Active│◄────────────│Light │◄────────────│Deeper│
  └──┬───┘             └──────┘             └──────┘
     │                                         │
     │ software                    software    │
     ▼                                         ▼
  ┌──────┐                              ┌──────────┐
  │D3hot │◄─────────────────────────────│  D3cold  │
  │Config│  power removal               │  No power│
  │only  │──────────────────────────────►│  (aux    │
  └──────┘                              │  only)   │
                                        └──────────┘

Transition via PMCSR register (power management capability):
  Write PowerState bits [1:0] to desired D-state.
  D3hot → D0: Requires at least 10 ms delay (spec minimum).
  D3cold → D0: Full power-on reset (link retrain, enumeration).

Device context in each state:
  D0: All context preserved. Fully operational.
  D1: Most context preserved. Quick resume.
  D2: May lose some context. Moderate resume time.
  D3hot: Config space accessible. No MMIO/DMA. Internal
         state may be lost. Software must re-initialize.
  D3cold: Nothing preserved except aux-powered PME logic.
         Device must be fully re-initialized on power-on.

PME (Power Management Event):
  Device in D1/D2/D3 can signal PME to wake the system.
  In PCIe: PME is a message TLP sent upstream to the Root Port.
  Root Port has PME interrupt handling (via AER/PME service).
  Linux: pcie_pme driver handles PME messages.
```

---

## 15. Error Handling

### Error Classification

```
Correctable Errors:
  Automatically corrected by hardware (link layer retry).
  No data corruption. Counted for monitoring.

  Specific errors:
    - Receiver Error: 8b/10b or 128b/130b decode error
    - Bad TLP: CRC error on TLP (LCRC mismatch)
    - Bad DLLP: CRC error on DLLP
    - Replay Timer Timeout: Ack not received in time
    - Replay Number Rollover: Too many retries
    - Advisory Non-Fatal: Non-fatal error treated as correctable
    - Corrected Internal Error: Internal logic error corrected

Uncorrectable Non-Fatal Errors:
  Data may be corrupted but device can continue.
  Software intervention needed.

  Specific errors:
    - Poisoned TLP Received: Data payload known bad
    - Completion Timeout: No completion received for non-posted request
    - Completer Abort: Completer deliberately rejected request
    - Unexpected Completion: Received completion for no matching request
    - Unsupported Request: Request type or address not supported
    - ECRC Error: End-to-end CRC mismatch
    - ACS Violation: ACS check failed
    - Uncorrectable Internal Error: Internal logic error

Uncorrectable Fatal Errors:
  Link reliability compromised. Link must be reset.

  Specific errors:
    - Data Link Protocol Error: DLLP sequence error
    - Surprise Down Error: Link went down unexpectedly
    - Flow Control Protocol Error: Credit protocol violation
    - Receiver Overflow: Receiver buffer overflow
    - Malformed TLP: TLP structure invalid
```

### AER Error Flow

```
Error occurs at device:
  1. Device sets error bit in AER Uncorrectable/Correctable
     Error Status register.
  2. If error reporting is enabled in Device Control register:
     - Device sends ERR_COR, ERR_NONFATAL, or ERR_FATAL message
       upstream toward Root Port.
  3. Root Port receives error message:
     - Sets appropriate bit in Root Error Status register.
     - If interrupt enabled: triggers AER interrupt.
  4. Linux AER driver (aer.c):
     - Reads Root Error Status to identify error source.
     - Reads error device's AER registers (via config space).
     - Logs the Header Log (first 4 DW of offending TLP).
     - For fatal/non-fatal: initiates error recovery state machine.
     - Calls driver's err_handler callbacks.

DPC (Downstream Port Containment):
  Configured on Root Ports or Switch Downstream Ports.
  When fatal error detected on downstream link:
    1. DPC automatically disables the link (containment).
    2. Prevents error propagation to rest of hierarchy.
    3. Triggers DPC interrupt to software.
    4. Software can attempt recovery:
       - Clear DPC trigger status
       - Retrain link
       - Re-enumerate devices
       - Or hot-remove the failed device

  DPC is especially valuable for NVMe hotplug and
  surprise-removal scenarios.
```

### Linux AER Recovery State Machine

```
                    Error Detected
                         │
                    ┌────▼────────────────────┐
                    │ error_detected()         │
                    │ - Stop I/O               │
                    │ - Return: CAN_RECOVER,   │
                    │   NEED_RESET, or         │
                    │   DISCONNECT             │
                    └────┬────────────────────┘
                         │
            ┌────────────┼────────────┐
            ▼            ▼            ▼
       CAN_RECOVER   NEED_RESET   DISCONNECT
            │            │            │
       ┌────▼────┐  ┌────▼────┐      │
       │mmio_    │  │platform │      │
       │enabled()│  │link/slot│   Remove
       │- Probe  │  │reset    │   device
       │  device │  └────┬────┘
       └────┬────┘       │
            │       ┌────▼────┐
            │       │slot_    │
            │       │reset()  │
            │       │- Restore│
            │       │  config │
            │       │- Re-init│
            │       └────┬────┘
            │            │
            └──────┬─────┘
                   │
              ┌────▼────┐
              │resume() │
              │- Normal │
              │  ops    │
              └─────────┘
```

---

## 16. Advanced Topics

### CXL (Compute Express Link)

```
CXL is built on top of PCIe physical layer. Three sub-protocols:

CXL.io: Equivalent to PCIe TLP layer. Used for device discovery,
  configuration, MMIO access. Functionally identical to PCIe
  transactions. This is what the device uses during enumeration
  and initialization.

CXL.cache: Device-to-host cache coherency protocol. Allows the
  device to cache host memory with full coherency (snoop/invalidate).
  Enables accelerators to access host memory without software
  cache flush/invalidate. Uses separate header format from TLPs.

CXL.mem: Host-to-device memory access protocol. Allows the host
  CPU to access device-attached memory as if it were local DRAM
  (load/store, cacheable). Enables memory expanders, pooling.

CXL device types:
  Type 1: Accelerator (CXL.io + CXL.cache) — e.g., SmartNIC
  Type 2: Accelerator with memory (CXL.io + CXL.cache + CXL.mem)
          — e.g., GPU with device memory
  Type 3: Memory expander (CXL.io + CXL.mem) — e.g., CXL DRAM
          module, persistent memory

CXL versions:
  CXL 1.0/1.1: Based on PCIe Gen 5 PHY
  CXL 2.0: Memory pooling, switching, security
  CXL 3.0: Based on PCIe Gen 6 PHY (PAM4, 64 GT/s)
            Back-invalidation, peer-to-peer, fabric management

Relationship to PCIe:
  CXL uses the same electrical signaling, link training (LTSSM),
  and physical layer as PCIe. A CXL port negotiates CXL protocol
  during link training via modified TS1/TS2 ordered sets.
  If both sides support CXL, the link runs CXL protocols.
  If either side is PCIe-only, it falls back to standard PCIe.
  CXL devices enumerate as PCIe devices (same config space,
  BDF, ECAM) with additional CXL-specific capabilities.
```

### NVMe over PCIe

```
NVMe uses PCIe as its transport. Key architectural mapping:

NVMe BAR0 (Controller Registers):
  Offset 0x00: CAP (Controller Capabilities, 8B)
  Offset 0x08: VS (Version, 4B)
  Offset 0x0C: INTMS (Interrupt Mask Set, 4B)
  Offset 0x10: INTMC (Interrupt Mask Clear, 4B)
  Offset 0x14: CC (Controller Configuration, 4B)
  Offset 0x1C: CSTS (Controller Status, 4B)
  Offset 0x20: NSSR (NVM Subsystem Reset, 4B)
  Offset 0x24: AQA (Admin Queue Attributes, 4B)
  Offset 0x28: ASQ (Admin Submission Queue Base Addr, 8B)
  Offset 0x30: ACQ (Admin Completion Queue Base Addr, 8B)
  Offset 0x1000+: Doorbell registers (per-queue)

Submission Queue (SQ): Ring buffer in host memory. Host writes
  64-byte NVMe commands. Host writes SQ Tail Doorbell in BAR0
  to notify controller of new commands.

  Doorbell write = single 4-byte MMIO write = single PCIe
  Memory Write TLP. This is the "submission" cost.

Completion Queue (CQ): Ring buffer in host memory. Controller
  DMAs 16-byte completion entries. Controller triggers MSI-X
  interrupt to notify host.

  DMA write + interrupt = one PCIe Memory Write TLP (completion
  data) + one PCIe Memory Write TLP (MSI-X interrupt).

Performance-critical path:
  Submit: CPU writes doorbell → 1 posted Memory Write TLP (fast)
  Complete: Device DMAs completion → triggers MSI-X → CPU reads CQ
  Data: Device DMAs data to/from host memory → bulk Memory Write TLPs

NVMe and PCIe MPS/MRRS:
  NVMe data transfers are limited by MPS (for writes) and MRRS
  (for reads). Larger values mean fewer TLPs for the same data,
  reducing per-TLP overhead. Setting MPS=256 or 512 can improve
  throughput.
```

### PCIe Peer-to-Peer (P2P) DMA

```
P2P DMA allows one PCIe device to directly read/write another
PCIe device's memory (BARs), without going through system DRAM.

Use cases:
  - GPU Direct Storage: NVMe SSD → GPU VRAM (bypass CPU/DRAM)
  - GPUDirect RDMA: NIC → GPU VRAM (bypass CPU/DRAM)
  - FPGA ↔ GPU direct communication

Requirements:
  - Both devices must be behind the same Root Port (or the
    Root Complex must support P2P routing)
  - ACS must be configured to allow P2P (or disabled)
  - IOMMU must allow the P2P DMA mappings

How it works:
  Device A wants to DMA to Device B's BAR:
  1. Device A issues a Memory Write TLP with Device B's BAR address
  2. The switch (or Root Complex) routes the TLP directly to
     Device B based on address matching
  3. The data goes Device A → Switch → Device B (never touches DRAM)

Linux kernel P2P support (drivers/pci/p2pdma.c):
  pci_p2pdma_distance()    — check if P2P is feasible
  pci_alloc_p2pmem()       — allocate P2P-capable memory
  pci_p2pdma_map_sg()      — DMA-map for P2P transfer

Limitations:
  - Not all Root Complexes support P2P (some force all traffic
    through DRAM). Intel chipsets historically didn't support
    P2P across Root Ports.
  - Switch-based P2P works more reliably (both devices behind
    same PCIe switch).
  - IOMMU interaction: P2P requests may or may not go through
    the IOMMU depending on topology and ACS configuration.
```

### PCIe 6.0/7.0: FLIT Mode

```
FLIT (Flow control unIT) mode fundamentally changes the
Transaction and Data Link Layer for Gen 6+:

Traditional mode (Gen 1-5):
  Variable-length TLPs, each with its own LCRC.
  DLLPs separate from TLPs.
  Ack/Nak per TLP.

FLIT mode (Gen 6+):
  Fixed 256-byte FLITs. Every FLIT is exactly 256 bytes.

  FLIT structure:
  ┌──────────────────────────────────────────────────────┐
  │  236 bytes: TLP data                                 │
  │  (can contain multiple small TLPs, or fragments      │
  │   of large TLPs, packed tightly)                     │
  ├──────────────────────────────────────────────────────┤
  │  6 bytes: FLIT header (type, sequence#, credits)     │
  ├──────────────────────────────────────────────────────┤
  │  8 bytes: CRC (covers entire FLIT)                   │
  ├──────────────────────────────────────────────────────┤
  │  6 bytes: FEC (Forward Error Correction)             │
  └──────────────────────────────────────────────────────┘
  Total: 256 bytes

Benefits:
  - No per-TLP ECRC/LCRC overhead (single CRC per FLIT)
  - Small TLPs packed efficiently (no wasted link bandwidth)
  - FEC provides error correction without retransmission
    (corrects 1-bit errors, detects multi-bit)
  - Flow control credits embedded in every FLIT (no separate
    UpdateFC DLLPs needed)
  - Simplified hardware: fixed-size processing pipeline

Efficiency comparison:
  64-byte Memory Write TLP:
    Gen 5 (traditional): 64B data + 16B header + 4B ECRC + 4B LCRC
                        + 2B seq# = 90 bytes → 71% efficiency
    Gen 6 (FLIT):       Packed into 236B payload area with other TLPs
                        → ~92% efficiency (amortized FLIT overhead)

L0p power state (Gen 6+):
  New power state between L0 and L0s.
  Reduces link width dynamically (e.g., x8 → x2) while
  maintaining active state. No exit latency for remaining lanes.
  Provides power savings proportional to reduced width.
```

### Other Advanced Features

```
PCIe IDE (Integrity and Data Encryption):
  Link-level encryption for TLP payloads.
  AES-GCM-256 encryption + integrity protection.
  Protects against physical attacks on PCIe links
  (interposers, protocol analyzers that modify data).
  Negotiated via IDE capability and DOE/SPDM.

DOE (Data Object Exchange):
  Generic mechanism for exchanging data objects via config space.
  Used for CMA (Component Measurement and Authentication),
  SPDM (Security Protocol and Data Model), and IDE setup.
  Replaces vendor-specific mailbox protocols.

MCTP (Management Component Transport Protocol):
  Sideband management protocol. Can run over PCIe (using
  VDM = Vendor Defined Messages). Used for BMC communication
  with PCIe devices (firmware update, telemetry, etc.).

TPH (TLP Processing Hints):
  TLP carries Steering Tag hints that tell the completer
  (Root Complex / memory controller) where to place the data
  in the CPU cache hierarchy. Can direct DMA data to specific
  cache levels or bypass cache entirely.
  Useful for NIC receive buffers (place in L2 cache of the
  CPU that will process the packet).

Coherent Interconnects layered on PCIe:
  CXL: Cache/memory coherency (see above)
  CCIX: Cache Coherent Interconnect for Accelerators
        (ARM-ecosystem, being superseded by CXL)
  Gen-Z: Fabric-based memory-semantic interconnect (defunct,
         merged into CXL efforts)
  OpenCAPI: Open Coherent Accelerator Processor Interface
            (IBM Power, now folded into CXL)
  UCIe: Universal Chiplet Interconnect Express
        Chiplet-to-chiplet interconnect. Uses PCIe/CXL
        protocols at the die-to-die level. Physical layer
        designed for short-reach (in-package) signaling.
```

---

## 17. Key References

### Specifications

1. **PCI Express Base Specification**, PCI-SIG. Revision 6.1 (2024), 7.0 (2025). The authoritative source. Available to PCI-SIG members. Covers all layers, TLP formats, capabilities, LTSSM, etc. (~1500 pages for Rev 5.0).
2. **PCI Local Bus Specification**, PCI-SIG, Revision 3.0. Covers legacy PCI config space layout, BAR mechanism, capability list. Still relevant for understanding PCIe's software interface.
3. **Single Root I/O Virtualization (SR-IOV) Specification**, PCI-SIG, Revision 1.1 (2010). SR-IOV capability, VF lifecycle, BARs.
4. **VIRTIO Specification**, OASIS, Version 1.2+ (2022). Virtio-PCI transport chapter covers capability structures, notification, MSI-X usage.
5. **NVM Express Base Specification**, NVM Express Inc., Revision 2.0+ (2021). NVMe register layout in PCIe BAR0, doorbell mechanism, queue architecture.
6. **CXL Specification**, CXL Consortium, Revision 3.1 (2023). CXL.io/cache/mem protocols, device types, relationship to PCIe PHY.

### Books

7. **PCI Express System Architecture**, Ravi Budruk, Don Anderson, Tom Shanley, MindShare/Addison-Wesley, 2003. The classic comprehensive reference. Covers all three layers, LTSSM, flow control, ordering, config space in great detail.
8. **PCI Express Technology 3.0**, Mike Jackson, Ravi Budruk, MindShare Press, 2012. Updated for Gen 3, covers 128b/130b encoding, equalization, AER, extended capabilities.
9. **Linux Device Drivers, 3rd Edition**, Jonathan Corbet, Alessandro Rubini, Greg Kroah-Hartman, O'Reilly, 2005. Chapter 12 covers PCI driver basics (somewhat dated API but concepts remain).

### Linux Kernel Documentation

10. **kernel.org/doc/html/latest/PCI/pci.html** — Linux PCI driver API
11. **kernel.org/doc/html/latest/PCI/msi-howto.html** — MSI/MSI-X howto
12. **kernel.org/doc/html/latest/PCI/pci-error-recovery.html** — Error recovery
13. **kernel.org/doc/html/latest/PCI/pciebus-howto.html** — PCIe port bus driver
14. **kernel.org/doc/html/latest/PCI/sysfs-pci.html** — sysfs interface
15. **kernel.org/doc/html/latest/PCI/endpoint/** — PCI endpoint framework
16. **kernel.org/doc/html/latest/driver-api/vfio.html** — VFIO driver API
17. Source: **include/uapi/linux/pci_regs.h** — All register offset defines

### Online Resources

18. **OSDev Wiki: PCI** (wiki.osdev.org/PCI) — Configuration space layout, BAR decoding, MSI/MSI-X format, capability structures. Excellent implementation reference.
19. **OSDev Wiki: PCI Express** (wiki.osdev.org/PCI_Express) — ECAM access, extended config space.
20. **Xillybus PCIe Tutorials** (xillybus.com/tutorials/) — Excellent TLP format walkthrough, flow control explanation, practical examples.
21. **QEMU source: include/hw/pci/pci.h, hw/pci/**, **hw/virtio/** — Reference implementation for PCI device emulation.

### Papers and Talks

22. **"Understanding PCIe Performance for End Host Networking"**, Neugebauer et al., SIGCOMM 2018. Measures real PCIe performance bottlenecks: TLP overhead, credit stalls, ordering constraints. Essential reading for understanding PCIe performance in practice.
23. **"FPsPIN: An FPGA-based Open-Hardware Research Platform for Network-Attached Accelerators"**, Di Girolamo et al., 2023. PCIe endpoint implementation details.
24. **"Optimus Prime: Accelerating Data Transformation and Transfer for GPU-Native Analytics"**, Chrysogelos et al., PVLDB 2023. GPU Direct / P2P DMA architecture for database analytics.
25. **"Characterizing PCIe Congestion in an HPC Cluster"**, Li et al., SC 2020. PCIe congestion analysis in multi-GPU/multi-NIC configurations.

---

*Last updated: 2026-03-27*
