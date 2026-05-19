+++
title = "VFIO Internals"
date = 2026-03-23
[taxonomies]
tags = ["vfio", "iommu", "dma", "pci-passthrough", "dpdk", "spdk", "virtualization"]
+++


# VFIO Internals: Userspace Device Drivers with Hardware DMA Isolation

A comprehensive deep dive into Linux VFIO (Virtual Function I/O) — the kernel framework that enables safe, high-performance userspace device drivers by leveraging IOMMU hardware for DMA isolation. Covers IOMMU fundamentals, the VFIO API, PCI passthrough workflows, production usage in DPDK/SPDK/QEMU, the newer IOMMUFD interface, and kernel internals.

---

## Table of Contents
1. [What VFIO Is and Why It Exists](#1-what-vfio-is-and-why-it-exists)
2. [IOMMU Fundamentals](#2-iommu-fundamentals)
3. [VFIO Architecture](#3-vfio-architecture)
4. [PCI Device Passthrough Workflow](#4-pci-device-passthrough-workflow)
5. [VFIO in Practice: DPDK, SPDK, QEMU/KVM](#5-vfio-in-practice-dpdk-spdk-qemu-kvm)
6. [VFIO-user: Software-Defined Devices](#6-vfio-user-software-defined-devices)
7. [DMA and Memory Management](#7-dma-and-memory-management)
8. [Security Model](#8-security-model)
9. [Performance Considerations](#9-performance-considerations)
10. [Advanced Topics](#10-advanced-topics)
11. [Kernel Internals](#11-kernel-internals)
12. [Key References](#12-key-references)

---

## 1. What VFIO Is and Why It Exists

### The Problem

DMA-capable devices can read and write physical memory independently of the CPU. When you give a userspace process direct access to a device (for performance — bypassing the kernel driver), the device can DMA to **any** physical address, including kernel memory, other processes' memory, or IOMMU page tables themselves. This is a catastrophic security hole.

```
Without IOMMU:

  Userspace process          PCI Device
  ┌─────────────┐           ┌──────────┐
  │ programs     │──MMIO──► │ NIC/NVMe │
  │ device regs  │           │          │──DMA──► ANY physical address
  └─────────────┘           └──────────┘         (kernel, other procs, etc.)
                                                  ← UNSAFE
```

### The History: UIO → VFIO

**UIO (Userspace I/O)** — Linux 2.6.23, 2007:
- First attempt at userspace device drivers
- Maps device MMIO (BARs) into userspace via `mmap(/dev/uioN)`
- Delivers interrupts via `read()` on the fd
- **Fatal flaw**: No DMA isolation. The device can DMA anywhere. You must trust the hardware completely.
- No MSI/MSI-X support (only legacy INTx via a kernel-side interrupt handler)
- Still used for simple FPGA boards and industrial hardware where you trust the device

**VFIO (Virtual Function I/O)** — Linux 3.6, 2012, designed by Alex Williamson (Red Hat):
- Solves the DMA isolation problem by requiring an IOMMU
- Maps DMA through the IOMMU — device can **only** access pages explicitly mapped by the userspace process
- Full MSI/MSI-X support via eventfd (epoll-able)
- Proper PCI config space access
- Device reset capability
- Foundation for all modern userspace drivers: DPDK, SPDK, QEMU/KVM device passthrough

```
Comparison:

                  UIO                          VFIO
  IOMMU?          No                           Yes (required)
  DMA safety      None — device can DMA        Hardware-enforced — only
                  to any physical address       mapped pages accessible
  MSI/MSI-X       No (INTx only)               Yes, via eventfd
  Config space    No                            Yes (read/write)
  Device reset    No                            Yes
  Privilege       CAP_SYS_RAWIO                 Group file perms (can be
                                                unprivileged)
  Users           Legacy FPGA, industrial       DPDK, SPDK, QEMU/KVM,
                                                GPU passthrough
```

---

## 2. IOMMU Fundamentals

The IOMMU is the hardware that makes VFIO possible. It sits between PCI devices and physical memory, translating device DMA addresses the same way the CPU's MMU translates virtual addresses.

### What an IOMMU Does

```
  CPU                          IOMMU                      DRAM
  ┌─────┐                    ┌──────────┐               ┌──────┐
  │     │─── VA→PA (MMU) ──►│          │               │      │
  │ CPU │                    │  IOMMU   │               │ RAM  │
  │     │                    │          │               │      │
  └─────┘                    │ IOVA→PA  │               └──────┘
                             │ (page    │                  ▲
  ┌─────┐                    │  tables) │                  │
  │ NIC │─── DMA (IOVA) ──►│          │──── PA ──────────┘
  └─────┘                    └──────────┘
                                  │
                             Also handles:
                             - Interrupt remapping
                             - Device isolation
                             - PASID (SVA)
```

Three implementations, same concept:
- **Intel VT-d** (Virtualization Technology for Directed I/O) — the first, most widely deployed on servers
- **AMD-Vi** (AMD I/O Virtualization) — AMD's equivalent, architecturally very similar
- **ARM SMMU** (System Memory Management Unit) — SMMUv3 is the current version, used in ARM server SoCs (Ampere, Graviton)

### DMA Remapping (Address Translation)

The IOMMU maintains page tables (structurally similar to CPU page tables) that translate **IOVA** (I/O Virtual Address — what the device sees) to **PA** (Physical Address — actual DRAM):

```
  IOVA (device-side)                    PA (DRAM-side)
  ┌────────────────┐                   ┌────────────────┐
  │ 0x0000_0000    │──────────────────►│ 0x7F20_0000    │  (mapped by VFIO_MAP_DMA)
  │ 0x0000_1000    │──────────────────►│ 0x3A10_5000    │
  │ 0x0000_2000    │       ╳           │                │  (unmapped — device gets
  │ 0x0000_3000    │──────────────────►│ 0x1234_0000    │   fault or abort on access)
  └────────────────┘                   └────────────────┘
```

Page table structure (Intel VT-d, 4-level):
```
Root Table (per-bus)
  └─► Context Table (per-device function)
        └─► Level-4 Page Table (PML4-equivalent)
              └─► Level-3 → Level-2 → Level-1 → Physical Page
```

Supports 4KB, 2MB, and 1GB page sizes (same as CPU page tables). **Hugepages matter enormously** — see [Performance](#9-performance-considerations).

### IOMMU Groups

An **IOMMU group** is the smallest set of devices that the IOMMU can isolate from all other devices. It is the **security boundary**.

Why groups exist: PCIe devices behind the same bridge or switch can potentially communicate with each other via peer-to-peer DMA without going through the IOMMU. If device A and device B share a PCIe switch that lacks **ACS (Access Control Services)**, then isolating A from B is impossible in hardware — they're in the same IOMMU group.

```
  PCIe Root Complex
  ├── Port 0 (ACS capable)
  │   └── GPU                    ← IOMMU group 1 (alone)
  ├── Port 1 (ACS capable)
  │   └── NIC                    ← IOMMU group 2 (alone)
  └── Port 2 (NO ACS)
      ├── SATA controller        ← IOMMU group 3
      └── USB controller         ← IOMMU group 3 (same group — can't isolate)
```

**ACS (Access Control Services)**: PCIe capability that enforces routing through the root complex (and thus the IOMMU) for all transactions. When every bridge in the path has ACS, each device function gets its own IOMMU group. Server-grade hardware usually has ACS; consumer hardware often doesn't.

View IOMMU groups:
```bash
# List all IOMMU groups and their devices
for g in /sys/kernel/iommu_groups/*/devices/*; do
    echo "Group $(basename $(dirname $(dirname $g))): $(lspci -nns $(basename $g))"
done
```

### IOTLB (IOMMU TLB)

The IOMMU caches recent IOVA→PA translations in its TLB, called the **IOTLB**. On a miss, the IOMMU walks the page tables (in DRAM), adding latency to DMA transactions. The IOTLB is typically small (a few hundred to a few thousand entries), so hugepages dramatically reduce miss rate:

| Page size | Entries for 1 GB mapping | IOTLB pressure |
|-----------|--------------------------|----------------|
| 4 KB      | 262,144                  | Extreme        |
| 2 MB      | 512                      | Moderate       |
| 1 GB      | 1                        | Negligible     |

### Interrupt Remapping

The IOMMU also remaps interrupts (MSI/MSI-X). Without interrupt remapping, a malicious device could forge an MSI message to inject arbitrary interrupts into any CPU vector — effectively a hardware-level interrupt injection attack. The IOMMU's **Interrupt Remapping Table (IRT)** validates that each device can only signal interrupts it was assigned.

---

## 3. VFIO Architecture

VFIO uses a three-level hierarchy: **Container** → **Group** → **Device**.

```
  Userspace Process
  ┌──────────────────────────────────────────────────────┐
  │                                                      │
  │  container_fd = open("/dev/vfio/vfio")               │
  │       │                                              │
  │       │  VFIO_SET_IOMMU(TYPE1)                       │
  │       │  VFIO_MAP_DMA(iova, vaddr, size)             │
  │       │                                              │
  │       ├── group_fd = open("/dev/vfio/26")             │
  │       │       │                                      │
  │       │       ├── device_fd = VFIO_GROUP_GET_DEVICE_FD│
  │       │       │       │                              │
  │       │       │       ├── GET_REGION_INFO (BARs)     │
  │       │       │       ├── mmap() BAR regions         │
  │       │       │       ├── SET_IRQS (MSI-X→eventfd)   │
  │       │       │       └── DEVICE_RESET               │
  │       │       │                                      │
  │       │       └── (another device in same group)     │
  │       │                                              │
  │       └── (another group in same container)          │
  │                                                      │
  └──────────────────────────────────────────────────────┘

  Kernel
  ┌──────────────────────────────────────────────────────┐
  │  VFIO core                                           │
  │    ├── vfio_iommu_type1  ←── IOMMU page table mgmt  │
  │    ├── vfio_pci_core     ←── PCI device handling     │
  │    └── vfio-pci driver   ←── binds to PCI devices    │
  │                                                      │
  │  IOMMU subsystem                                     │
  │    ├── intel-iommu (VT-d)                            │
  │    ├── amd_iommu (AMD-Vi)                            │
  │    └── arm-smmu-v3                                   │
  └──────────────────────────────────────────────────────┘
```

### Container

The **container** (`/dev/vfio/vfio`) represents an IOMMU domain — a single IOVA address space. All devices in the same container share the same DMA mappings.

```c
int container = open("/dev/vfio/vfio", O_RDWR);

// Check API version
ioctl(container, VFIO_GET_API_VERSION);  // must return VFIO_API_VERSION

// Check IOMMU type support
ioctl(container, VFIO_CHECK_EXTENSION, VFIO_TYPE1_IOMMU);

// Set IOMMU type (after attaching at least one group)
ioctl(container, VFIO_SET_IOMMU, VFIO_TYPE1v2_IOMMU);
```

IOMMU backend types:
- `VFIO_TYPE1_IOMMU` / `VFIO_TYPE1v2_IOMMU` — standard x86/ARM (most common)
- `VFIO_SPAPR_TCE_IOMMU` — IBM POWER (pSeries)
- `VFIO_NOIOMMU_IOMMU` — no IOMMU, no isolation (see [No-IOMMU mode](#no-iommu-mode))

### Group

The **group** (`/dev/vfio/N`) corresponds to an IOMMU group. All devices in the group must be bound to VFIO (or have no driver) before any device in the group can be used — this enforces the IOMMU isolation guarantee.

```c
int group = open("/dev/vfio/26", O_RDWR);

// Check group is viable (all devices bound to vfio or no driver)
struct vfio_group_status status = { .argsz = sizeof(status) };
ioctl(group, VFIO_GROUP_GET_STATUS, &status);
// status.flags must include VFIO_GROUP_FLAGS_VIABLE

// Attach group to container
ioctl(group, VFIO_GROUP_SET_CONTAINER, &container);
```

### Device

Individual devices within a group, obtained by BDF (Bus:Device.Function) string:

```c
int device = ioctl(group, VFIO_GROUP_GET_DEVICE_FD, "0000:03:00.0");
```

#### Region Info (BARs)

PCI devices expose up to 6 BARs (Base Address Registers) plus config space, ROM, and VGA regions:

```c
struct vfio_region_info reg = {
    .argsz = sizeof(reg),
    .index = VFIO_PCI_BAR0_REGION_INDEX,  // 0-5 for BARs
};
ioctl(device, VFIO_DEVICE_GET_REGION_INFO, &reg);

// Map BAR into userspace (if MMAP flag set)
void *bar0 = mmap(NULL, reg.size, PROT_READ | PROT_WRITE,
                   MAP_SHARED, device, reg.offset);

// Now bar0[offset] directly reads/writes device registers
```

Region indices:
| Index | Region |
|-------|--------|
| 0-5   | PCI BAR 0-5 |
| 6     | PCI ROM |
| 7     | PCI config space |
| 8     | VGA (legacy I/O + framebuffer) |

Not all BARs are mmap-able. Config space and some BARs require `read()`/`write()` on the device fd at the region's offset. The kernel intercepts certain config space writes to maintain system integrity (e.g., command register, BARs themselves).

#### Interrupt Configuration

VFIO delivers interrupts via **eventfd** — file descriptors that integrate with `epoll`, `poll`, `select`, and `io_uring`.

```c
// Create eventfd for MSI-X vector 0
int evtfd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);

// Configure MSI-X interrupt
struct vfio_irq_set *irq_set;
size_t sz = sizeof(*irq_set) + sizeof(int32_t);
irq_set = malloc(sz);
irq_set->argsz = sz;
irq_set->flags = VFIO_IRQ_SET_DATA_EVENTFD | VFIO_IRQ_SET_ACTION_TRIGGER;
irq_set->index = VFIO_PCI_MSIX_IRQ_INDEX;
irq_set->start = 0;   // vector number
irq_set->count = 1;
*(int32_t *)irq_set->data = evtfd;  // eventfd

ioctl(device, VFIO_DEVICE_SET_IRQS, irq_set);

// Now epoll on evtfd to receive interrupts
```

Interrupt types:
| Index | Type | Notes |
|-------|------|-------|
| `VFIO_PCI_INTX_IRQ_INDEX` | INTx (legacy) | Level-triggered, shared, slow |
| `VFIO_PCI_MSI_IRQ_INDEX` | MSI | Message-signaled, 1-32 vectors |
| `VFIO_PCI_MSIX_IRQ_INDEX` | MSI-X | Message-signaled, up to 2048 vectors |
| `VFIO_PCI_ERR_IRQ_INDEX` | PCIe AER | Error reporting |
| `VFIO_PCI_REQ_IRQ_INDEX` | Device request | Kernel wants device back |

#### DMA Mapping

Set up on the **container** (shared by all devices in it):

```c
struct vfio_iommu_type1_dma_map dma_map = {
    .argsz = sizeof(dma_map),
    .flags = VFIO_DMA_MAP_FLAG_READ | VFIO_DMA_MAP_FLAG_WRITE,
    .vaddr = (uint64_t)hugepage_ptr,  // userspace virtual address
    .iova  = 0x100000,                 // device-visible address
    .size  = 2 * 1024 * 1024,         // 2 MB
};
ioctl(container, VFIO_IOMMU_MAP_DMA, &dma_map);
```

After this call:
1. The kernel **pins** the physical pages backing `hugepage_ptr` (they can't be swapped out)
2. The IOMMU page tables are programmed: device DMA to IOVA `0x100000` → physical page behind `hugepage_ptr`
3. The device can now DMA read/write to that memory region

#### Device Reset

```c
ioctl(device, VFIO_DEVICE_RESET);
```

Performs a PCI function-level reset (FLR), secondary bus reset, or PM reset — whatever the hardware supports. Essential for returning a device to a known state.

---

## 4. PCI Device Passthrough Workflow

### Step-by-Step

```bash
# 1. Identify the device
$ lspci -nn | grep -i ethernet
03:00.0 Ethernet controller [0200]: Intel Corporation ... [8086:1572]

# 2. Find its IOMMU group
$ readlink /sys/bus/pci/devices/0000:03:00.0/iommu_group
/sys/kernel/iommu_groups/26

# 3. Check what else is in the group
$ ls /sys/kernel/iommu_groups/26/devices/
0000:03:00.0   # just this device — good

# 4. Load vfio-pci module
$ modprobe vfio-pci

# 5. Unbind from kernel driver
$ echo 0000:03:00.0 > /sys/bus/pci/devices/0000:03:00.0/driver/unbind

# 6. Bind to vfio-pci (using vendor:device ID)
$ echo "8086 1572" > /sys/bus/pci/drivers/vfio-pci/new_id
# Or alternatively:
$ echo vfio-pci > /sys/bus/pci/devices/0000:03:00.0/driver_override
$ echo 0000:03:00.0 > /sys/bus/pci/drivers/vfio-pci/bind

# 7. Set permissions (for non-root access)
$ chown user:group /dev/vfio/26
$ chmod 0660 /dev/vfio/26
```

### Complete C Example

```c
#include <linux/vfio.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <fcntl.h>

int main() {
    // --- Container setup ---
    int container = open("/dev/vfio/vfio", O_RDWR);
    assert(ioctl(container, VFIO_GET_API_VERSION) == VFIO_API_VERSION);
    assert(ioctl(container, VFIO_CHECK_EXTENSION, VFIO_TYPE1_IOMMU));

    // --- Group setup ---
    int group = open("/dev/vfio/26", O_RDWR);
    struct vfio_group_status gstatus = { .argsz = sizeof(gstatus) };
    ioctl(group, VFIO_GROUP_GET_STATUS, &gstatus);
    assert(gstatus.flags & VFIO_GROUP_FLAGS_VIABLE);

    ioctl(group, VFIO_GROUP_SET_CONTAINER, &container);
    ioctl(container, VFIO_SET_IOMMU, VFIO_TYPE1v2_IOMMU);

    // --- DMA mapping (hugepage-backed) ---
    void *dma_mem = mmap(NULL, 2*1024*1024, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);

    struct vfio_iommu_type1_dma_map dma_map = {
        .argsz = sizeof(dma_map),
        .flags = VFIO_DMA_MAP_FLAG_READ | VFIO_DMA_MAP_FLAG_WRITE,
        .vaddr = (uint64_t)dma_mem,
        .iova  = 0x0,
        .size  = 2 * 1024 * 1024,
    };
    ioctl(container, VFIO_IOMMU_MAP_DMA, &dma_map);

    // --- Device setup ---
    int device = ioctl(group, VFIO_GROUP_GET_DEVICE_FD, "0000:03:00.0");

    // Query and map BAR0
    struct vfio_region_info reg = {
        .argsz = sizeof(reg),
        .index = VFIO_PCI_BAR0_REGION_INDEX,
    };
    ioctl(device, VFIO_DEVICE_GET_REGION_INFO, &reg);

    void *bar0 = mmap(NULL, reg.size, PROT_READ | PROT_WRITE,
                       MAP_SHARED, device, reg.offset);

    // --- MSI-X interrupt setup ---
    int irqfd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);

    char irq_buf[sizeof(struct vfio_irq_set) + sizeof(int32_t)];
    struct vfio_irq_set *irq = (void *)irq_buf;
    irq->argsz = sizeof(irq_buf);
    irq->flags = VFIO_IRQ_SET_DATA_EVENTFD | VFIO_IRQ_SET_ACTION_TRIGGER;
    irq->index = VFIO_PCI_MSIX_IRQ_INDEX;
    irq->start = 0;
    irq->count = 1;
    *(int32_t *)irq->data = irqfd;
    ioctl(device, VFIO_DEVICE_SET_IRQS, irq);

    // --- Now use the device ---
    // Write commands to bar0, set up DMA descriptors in dma_mem,
    // epoll on irqfd for completions.
    // Device DMAs to/from IOVA 0x0 which maps to dma_mem.

    // --- Cleanup ---
    ioctl(device, VFIO_DEVICE_RESET);
    munmap(bar0, reg.size);
    munmap(dma_mem, 2*1024*1024);
    close(device);
    close(group);
    close(container);
}
```

---

## 5. VFIO in Practice: DPDK, SPDK, QEMU/KVM

### DPDK (Data Plane Development Kit)

DPDK drives NICs entirely from userspace for line-rate packet processing.

```
  ┌─────────────────────────────────────────────┐
  │  DPDK Application (userspace)               │
  │                                             │
  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
  │  │ Worker 0 │  │ Worker 1 │  │ Worker 2 │  │  (pinned to cores)
  │  │ poll()   │  │ poll()   │  │ poll()   │  │
  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
  │       │              │              │        │
  │  ┌────▼──────────────▼──────────────▼────┐  │
  │  │        Poll-Mode Driver (PMD)          │  │  (no interrupts in fast path)
  │  │  mmap'd BAR → TX/RX descriptor rings   │  │
  │  │  DMA buffers in hugepage mempools       │  │
  │  └────────────────┬──────────────────────┘  │
  │                   │                          │
  └───────────────────┼──────────────────────────┘
                      │ VFIO (IOMMU)
  ┌───────────────────▼──────────────────────────┐
  │  NIC Hardware                                │
  │  TX/RX queues DMA to hugepage-backed buffers │
  └──────────────────────────────────────────────┘
```

Key DPDK-VFIO details:
- **Pre-maps all hugepages at startup** — one `VFIO_IOMMU_MAP_DMA` per hugepage, then zero DMA mapping overhead during operation
- **Poll-mode drivers (PMDs)** — worker threads busy-poll TX/RX descriptor rings via mmap'd BARs. No interrupts, no context switches, no kernel involvement in the fast path
- **IOVA modes**: `iova-as-va` (IOVA = process virtual address, simplest) or `iova-as-pa` (IOVA = physical address, for legacy/No-IOMMU mode)
- **Performance**: 30-40 Mpps (million packets per second) per core for 64-byte packets on modern NICs. Near line-rate for 100 GbE.
- Falls back to UIO (`igb_uio`) or `vfio-pci` with No-IOMMU mode when hardware IOMMU is unavailable

### SPDK (Storage Performance Development Kit)

SPDK drives NVMe SSDs entirely from userspace using the same VFIO pattern.

```c
// SPDK NVMe flow (simplified):
// 1. Bind NVMe controller to vfio-pci
// 2. Map controller BARs (admin queue doorbell registers)
// 3. Allocate submission/completion queue memory in hugepages
// 4. Map queue memory via VFIO_MAP_DMA
// 5. Program queues via BAR registers
// 6. Submit I/O: write NVMe commands to submission queue, ring doorbell
// 7. Poll completion queue (no interrupts)
```

- Achieves **millions of IOPS per core** — versus ~200K IOPS through the kernel NVMe driver
- Uses hugepage-backed memory pools for all I/O buffers
- Eliminates: syscall overhead, interrupt overhead, context switches, kernel memory copies, block layer overhead
- Used in production at Intel, Samsung, Tencent, Alibaba for high-performance storage

### QEMU/KVM (Device Passthrough)

QEMU uses VFIO to assign physical PCI devices directly to VMs with near-native performance.

```
  ┌──────────────────────────────────┐
  │  Guest VM                        │
  │  ┌──────────────────────────┐    │
  │  │ Guest driver (e.g. NIC   │    │
  │  │ driver, GPU driver)      │    │  Guest thinks it owns the device
  │  └──────────┬───────────────┘    │
  │             │ Guest MMIO/DMA     │
  └─────────────┼────────────────────┘
                │
  ┌─────────────▼────────────────────┐
  │  QEMU (userspace)               │
  │  vfio-pci device model           │
  │  - Maps guest GPA → IOVA         │
  │  - Forwards MSI-X to KVM irqfd   │
  └─────────────┬────────────────────┘
                │ VFIO ioctls
  ┌─────────────▼────────────────────┐
  │  Kernel (VFIO + KVM + IOMMU)     │
  │  - IOMMU maps IOVA → HPA         │
  │  - Posted interrupts: MSI-X      │
  │    delivered directly to vCPU     │
  │    without VM exit                │
  └─────────────┬────────────────────┘
                │
  ┌─────────────▼────────────────────┐
  │  Physical PCI Device             │
  │  DMAs directly to VM's memory    │
  └──────────────────────────────────┘
```

Key mechanisms:
- **Two-level IOMMU**: Guest programs "guest IOVA" → QEMU translates to "host IOVA" → IOMMU translates to HPA. With nested IOMMU support, this becomes hardware-accelerated.
- **Posted interrupts** (Intel VT-d): MSI-X from the device is delivered directly to the guest vCPU without causing a VM exit. Zero-cost interrupt delivery.
- **ATS (Address Translation Service)**: Device has its own TLB (ATC — Address Translation Cache) and can cache IOMMU translations, reducing IOTLB pressure.

Common use cases:
- **GPU passthrough**: Assign an NVIDIA/AMD GPU to a VM for ML training or gaming
- **NIC passthrough**: Wire-speed networking in VMs (SR-IOV VFs assigned via VFIO)
- **NVMe passthrough**: Direct SSD access for database VMs

### vfio-mdev (Mediated Devices)

When you want to **share** a device across multiple VMs (not assign the whole thing), mediated devices provide software-partitioned virtual functions:

```
                    Physical GPU
                    ┌──────────────────────┐
                    │                      │
  ┌──────────┐     │  ┌───────────────┐   │
  │ VM 1     │◄────┼──│ mdev instance │   │
  └──────────┘     │  │ (vGPU 1)      │   │
                    │  └───────────────┘   │
  ┌──────────┐     │  ┌───────────────┐   │
  │ VM 2     │◄────┼──│ mdev instance │   │
  └──────────┘     │  │ (vGPU 2)      │   │
                    │  └───────────────┘   │
                    └──────────────────────┘
```

- **NVIDIA vGPU**: A kernel module (proprietary) creates mdev instances that each present as a virtual GPU. Each VM gets a time-sliced or MIG-partitioned portion.
- **Intel GVT-g**: Open-source mediated passthrough for Intel integrated GPUs. Guest GPU commands are intercepted and scheduled.

**SR-IOV vs mdev**:
- **SR-IOV** (Single Root I/O Virtualization): Hardware creates multiple PCI Virtual Functions (VFs) from one Physical Function (PF). Each VF is a real PCI device, assigned via standard VFIO. No software overhead. NICs widely support this (Intel X710, Mellanox ConnectX).
- **mdev**: Software-mediated. The vendor kernel module intercepts and multiplexes access. More flexible (can present any device type) but higher overhead. Used when hardware doesn't support SR-IOV or when finer partitioning is needed (GPUs).

---

## 6. VFIO-user: Software-Defined Devices

**VFIO-user** (introduced ~2021) extends the VFIO protocol over Unix domain sockets, allowing device emulation in a **separate userspace process** rather than in the kernel.

```
  ┌────────────────────┐         Unix Socket        ┌────────────────────┐
  │  Client Process    │◄───────────────────────────►│  Server Process    │
  │  (e.g., QEMU)      │    vfio-user protocol       │  (device emulator) │
  │                    │    (VFIO ioctls as msgs)     │                    │
  │  Opens vfio-user   │                             │  Implements virtual│
  │  "device" via      │                             │  PCI device:       │
  │  socket path       │                             │  - BAR handling    │
  │                    │                             │  - DMA requests    │
  │                    │                             │  - Interrupt inject│
  └────────────────────┘                             └────────────────────┘
```

### Why VFIO-user Exists

1. **Disaggregated emulation**: Device emulation can run in a separate process (or even a separate host), isolated from the VMM. If the emulator crashes, the VMM survives.
2. **Security**: Emulator process can be sandboxed (seccomp, namespaces) independently.
3. **Reuse**: Same VFIO API on the client side — QEMU, SPDK, or any VFIO-aware application works with no changes.

### SPDK vfio-user NVMe Target

SPDK implements a vfio-user server that presents a virtual NVMe controller:

```
  VM (QEMU)                                    SPDK
  ┌──────────────┐                            ┌──────────────┐
  │ Guest NVMe   │                            │ NVMe target  │
  │ driver       │                            │ (vfio-user   │
  │              │◄── vfio-user socket ──────►│  transport)  │
  │ Sees a real  │                            │              │
  │ NVMe device  │                            │ Backed by    │
  │              │                            │ real NVMe,   │
  │              │                            │ malloc, etc. │
  └──────────────┘                            └──────────────┘
```

The guest runs its standard NVMe driver. SPDK handles I/O on the server side, backed by any SPDK bdev (real NVMe, Ceph RBD, null, malloc). This replaces QEMU's built-in NVMe emulation with SPDK's high-performance I/O path.

### libvfio-user

Open-source library (originally from Nutanix) for building vfio-user device emulators:

```c
// Server side (device emulator):
vfu_ctx_t *ctx = vfu_create_ctx(VFU_TRANS_SOCK, "/tmp/my-device.sock", ...);
vfu_pci_init(ctx, VFU_PCI_TYPE_CONVENTIONAL, ...);
vfu_setup_region(ctx, VFU_PCI_DEV_BAR0_REGION_IDX, 0x1000, bar0_access_cb, ...);
vfu_realize_ctx(ctx);
vfu_run_ctx(ctx);  // event loop
```

---

## 7. DMA and Memory Management

### Page Pinning

When you call `VFIO_IOMMU_MAP_DMA`, the kernel **pins** the physical pages backing the userspace buffer. Pinning means:
- Pages cannot be swapped out
- Pages cannot be migrated (by NUMA balancing, compaction, etc.)
- The physical address is stable for the lifetime of the mapping

This is necessary because the IOMMU page tables contain physical addresses. If the kernel moved a page, the IOMMU would point to stale memory.

**Cost**: Pinning calls `pin_user_pages()` for each page, which is expensive. For 1 GB of 4 KB pages, that's 262,144 `pin_user_pages()` calls. With 2 MB hugepages, it's 512 calls. With 1 GB hugepages, it's 1 call. This is another reason hugepages dominate VFIO workloads.

**RLIMIT_MEMLOCK**: Pinned memory counts against the process's locked memory limit. You must raise it:
```bash
ulimit -l unlimited
# Or in /etc/security/limits.conf:
# user  hard  memlock  unlimited
```

### Hugepages for DMA

Hugepages are essentially mandatory for serious VFIO usage. Benefits:
1. **Fewer pin operations** — 512x fewer for 2 MB, 262,144x fewer for 1 GB
2. **Fewer IOMMU page table entries** — smaller tables, faster walks
3. **Fewer IOTLB entries** — dramatically better IOTLB hit rate
4. **Fewer TLB entries** — CPU-side benefit when the process accesses DMA buffers

Setup:
```bash
# Reserve 1 GB hugepages at boot (grub/kernel cmdline)
hugepagesz=1G hugepages=8 default_hugepagesz=1G

# Or at runtime (2 MB hugepages)
echo 1024 > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages

# Mount hugetlbfs
mount -t hugetlbfs nodev /dev/hugepages

# Application allocates from hugetlbfs or MAP_HUGETLB
void *buf = mmap(NULL, SZ_2M, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);
```

### IOVA Allocation

The userspace application chooses IOVA values (the addresses the device will use for DMA). Strategies:

1. **Identity mapping (VA = IOVA)**: Set `iova = vaddr`. Simplest, used by DPDK in `iova-as-va` mode. Works well with IOMMU. Device sees the same addresses as the process.

2. **Physical mapping (PA = IOVA)**: Set `iova = physical_address`. Used in No-IOMMU mode or DPDK's `iova-as-pa` mode. Requires reading `/proc/self/pagemap` to get physical addresses.

3. **Custom allocator**: Maintain an IOVA allocator that hands out addresses from a reserved range. Used by QEMU (guest physical addresses as IOVAs).

### Scatter-Gather DMA

Most modern devices support scatter-gather lists (SGLs) — a chain of (address, length) pairs that describe a non-contiguous DMA transfer. Each entry points to a different IOVA region. The IOMMU translates each IOVA independently.

```
  Scatter-Gather List:
  ┌───────────────────┐
  │ IOVA=0x1000, 4KB  │──► physical page A
  │ IOVA=0x5000, 4KB  │──► physical page B  (non-contiguous)
  │ IOVA=0x9000, 8KB  │──► physical pages C,D
  └───────────────────┘
```

### No-IOMMU Mode

```bash
echo 1 > /sys/module/vfio/parameters/enable_unsafe_noiommu_mode
```

VFIO without an IOMMU. The device uses physical addresses directly (no translation). Provides the VFIO API (BAR access, interrupts) but **zero DMA isolation**. The device can DMA to any physical address.

When used:
- Embedded systems without IOMMU hardware
- VMs where the host IOMMU isn't exposed to guests
- Development/testing

The `/dev/vfio/noiommu-*` group files are created with restrictive permissions and require `CAP_SYS_RAWIO`.

---

## 8. Security Model

### Why VFIO Is Safer Than Alternatives

| Approach | DMA Isolation | Interrupt Isolation | Privilege Required |
|----------|--------------|--------------------|--------------------|
| `/dev/mem` + `mmap` | None | None | `CAP_SYS_RAWIO` |
| `iopl()`/`ioperm()` | None | None | `CAP_SYS_RAWIO` |
| UIO | None | None | `CAP_SYS_RAWIO` |
| VFIO (No-IOMMU) | None | Via eventfd | `CAP_SYS_RAWIO` |
| **VFIO (with IOMMU)** | **Hardware-enforced** | **Remapped** | **Group file perms** |

VFIO with IOMMU is the only approach that provides hardware-enforced DMA isolation. A device bound to VFIO can **only** DMA to pages that the userspace process explicitly mapped via `VFIO_IOMMU_MAP_DMA`. Any DMA to an unmapped IOVA causes an IOMMU fault (logged in dmesg, DMA aborted).

### IOMMU Group as Security Boundary

The IOMMU group is the atomic unit of isolation. If you grant a user access to one device in a group, they effectively have access to **all** devices in that group (since those devices can peer-to-peer DMA to each other without going through the IOMMU).

VFIO enforces this: a group is not "viable" (usable) until **every** device in it is either bound to vfio-pci or has no driver. You can't use a VFIO device while another device in the same group has a kernel driver — that would let the VFIO user's device DMA through to the kernel driver's memory.

### ACS and Group Granularity

On consumer hardware (especially multi-function devices and PCIe switches without ACS), IOMMU groups can be large — sometimes an entire PCIe root port and everything behind it. This makes individual device passthrough difficult.

The `pci=acs_override=downstream,multifunction` kernel parameter forces all devices into separate groups by pretending ACS exists everywhere. **This breaks the security guarantee** — devices can still peer-to-peer DMA, the kernel just doesn't know about it. Acceptable for home lab GPU passthrough; unacceptable in production multi-tenant environments.

### Capability Requirements

With proper group file permissions, VFIO can be used **without root** and without `CAP_SYS_RAWIO`:
```bash
# Allow user 'dpdk' to access IOMMU group 26
chown dpdk:dpdk /dev/vfio/26
chmod 0600 /dev/vfio/26
# Also need access to the container
chown dpdk:dpdk /dev/vfio/vfio
```

The only caveat is `RLIMIT_MEMLOCK` must be sufficient for pinned DMA memory.

---

## 9. Performance Considerations

### DMA Mapping Overhead

The dominant cost is **page pinning** during `VFIO_IOMMU_MAP_DMA`. Each pinned page requires:
1. `pin_user_pages()` — walk the process page tables, increment page refcount, possibly fault in pages
2. IOMMU page table update — write the IOVA→PA mapping
3. IOTLB invalidation — flush stale cached translations

**Strategy: Map everything upfront.** DPDK maps all hugepages at startup. SPDK maps its memory pools once. After initial setup, the fast path has zero DMA mapping overhead.

For workloads that dynamically map/unmap (like QEMU with ballooning), the overhead is measurable:
- 4 KB pages: ~1-5 µs per map/unmap (dominated by pin/unpin)
- 2 MB hugepages: ~1-5 µs per map/unmap (same — one pin operation)
- The difference is you need 512x fewer operations for the same amount of memory

### IOTLB Pressure

IOTLB misses add **100-300 ns** per DMA transaction (IOMMU page table walk in DRAM). For a NIC processing 40 Mpps, that's catastrophic if even a small fraction are IOTLB misses.

Mitigation hierarchy:
1. **1 GB hugepages** — 1 IOTLB entry covers 1 GB. Essentially eliminates IOTLB misses.
2. **2 MB hugepages** — good enough for most workloads
3. **Pre-touch all DMA memory** — ensure IOMMU page tables are populated before I/O starts
4. **Minimize the IOVA range** — keep DMA buffers in a compact IOVA region

### IOMMU Passthrough Mode

The kernel boot parameter `iommu=pt` (passthrough) disables IOMMU translation for **kernel drivers** (DMA goes directly to physical addresses) while keeping IOMMU active for **VFIO devices**. Best of both worlds:
- Kernel drivers: zero IOMMU overhead, bare-metal performance
- VFIO devices: full IOMMU isolation

```bash
# Kernel cmdline
intel_iommu=on iommu=pt
```

Without `iommu=pt`, the kernel's DMA API sets up IOMMU mappings for every kernel driver DMA operation, adding overhead to disk and network I/O even when VFIO isn't in use.

### Posted Interrupts (Intel VT-d)

For VM device passthrough, **posted interrupts** allow MSI-X interrupts from the device to be delivered directly to the guest vCPU without a VM exit:

```
Without posted interrupts:
  Device MSI-X → host CPU → VM exit → KVM → inject interrupt → VM enter
  (each interrupt = ~1-3 µs VM exit/enter overhead)

With posted interrupts:
  Device MSI-X → posted interrupt descriptor → delivered to vCPU
  (zero VM exits for interrupt delivery)
```

This is critical for high-throughput passthrough workloads (NVMe, high-speed NICs) where interrupt rates can reach hundreds of thousands per second.

### Typical Overhead

With proper setup (hugepages, pre-mapped DMA, `iommu=pt`):
- **DPDK**: <1% overhead vs. No-IOMMU mode
- **SPDK NVMe**: <1% overhead vs. bare-metal
- **GPU passthrough**: ~2-5% overhead vs. bare-metal (mostly from VM exit handling for non-DMA device accesses)

---

## 10. Advanced Topics

### Nested IOMMUs (Nested Virtualization)

When running a VM inside a VM, there are two levels of IOMMU translation:

```
  Device DMA address (guest IOVA)
    │
    ▼  Stage-1 translation (guest-controlled)
  Guest Physical Address (GPA)
    │
    ▼  Stage-2 translation (host-controlled)
  Host Physical Address (HPA)
```

- **Intel VT-d**: "Scalable mode" with first-level + second-level page tables
- **ARM SMMUv3**: Stage-1 + Stage-2 (same as CPU EL1/EL0 + EL2 translation)
- **IOMMUFD** natively supports nested translation (see below)

Without hardware nested translation, the hypervisor must shadow the guest's IOMMU page tables — expensive and complex.

### Dirty Page Tracking (Live Migration)

When live-migrating a VM with a passed-through device, you need to know which pages the device has written to (dirtied) so you can re-transfer them. Two approaches:

1. **Software dirty tracking**: Mark all DMA-mapped pages read-only in the IOMMU. On device write → IOMMU fault → kernel logs the dirty page → re-maps as writable. Functional but high overhead (fault per first write).

2. **Hardware dirty tracking**:
   - Intel VT-d **SSAD** (Scalable-mode Second-stage Access/Dirty bits): IOMMU sets dirty bits in page tables automatically, like CPU page tables. Kernel reads and clears them.
   - ARM SMMUv3 **HTTU** (Hardware Translation Table Update): Same concept.
   - Much faster — no faults, just periodic dirty bitmap reads.

VFIO exposes this via `VFIO_IOMMU_DIRTY_PAGES` ioctl. QEMU uses it during live migration's iterative copy phase.

### PASID and SVA (Shared Virtual Addressing)

**PASID (Process Address Space ID)** allows a device to tag DMA requests with a process identifier, so different processes can share a device while each seeing their own virtual address space through the IOMMU.

**SVA (Shared Virtual Addressing)** / **SVM (Shared Virtual Memory)**: The IOMMU uses the **CPU's page tables** for DMA translation. The device sees the same virtual addresses as the process — no separate IOVA mapping needed.

```
  Process (VA space)          IOMMU with PASID/SVA
  ┌────────────────┐         ┌────────────────────┐
  │ 0x7fff0000     │         │ Device DMA to       │
  │ (stack)        │         │ VA 0x7fff0000       │
  │                │         │ with PASID=42       │
  │ 0x400000       │◄────────│                     │
  │ (heap buffer)  │         │ → IOMMU walks CPU   │
  │                │         │   page table for    │
  └────────────────┘         │   process 42        │
        ▲                    │ → translates to PA  │
        │                    └────────────────────┘
   Same page tables
   shared between CPU
   and IOMMU
```

Use cases:
- **Intel DSA/IAA** (Data Streaming Accelerator / In-Memory Analytics Accelerator): Submit work items pointing to process virtual addresses. The accelerator uses PASID+SVA to access them.
- **CXL Type-2 devices**: Accelerators that share the host's virtual memory coherently.
- Eliminates the need for `VFIO_MAP_DMA` entirely — the device uses process virtual addresses directly.

### IOMMUFD: The New Interface

**IOMMUFD** (Linux 6.2, 2023) is a new kernel interface designed to replace VFIO's container and group model. It provides a device-centric, fine-grained IOMMU management API.

```
  VFIO (legacy)                    IOMMUFD (new)
  ┌────────────────────┐          ┌────────────────────┐
  │ Container          │          │ /dev/iommu          │
  │  └── Group         │          │  ├── IOAS           │
  │       └── Device   │          │  │   (I/O Address   │
  │                    │          │  │    Space)         │
  │ DMA mgmt tied to   │          │  ├── HWPT           │
  │ container, group    │          │  │   (HW Page Table)│
  │ model              │          │  └── Device         │
  └────────────────────┘          └────────────────────┘
```

Key improvements:
- **No group requirement**: Devices can be managed individually (though IOMMU group isolation is still enforced internally).
- **IOAS (I/O Address Space)**: A logical IOVA address space, decoupled from containers and groups. Multiple IOASes can exist. Devices can be moved between IOASes.
- **HWPT (Hardware Page Table)**: Explicit control over IOMMU page tables. Enables nested translation (guest-controlled stage-1, host-controlled stage-2) without shadowing.
- **First-class dirty tracking**: `IOMMU_HWPT_GET_DIRTY_BITMAP` — read dirty bits from hardware page tables.
- **First-class nested IOMMU**: Allocate a nested HWPT, give it to the guest, and the guest programs it directly. No more shadow page tables.

VFIO still works (and will for the foreseeable future), but new features are being developed against IOMMUFD. QEMU 8.x+ supports IOMMUFD as a backend.

### VFIO CDX (Compute Data Acceleration)

VFIO CDX (Linux 6.5+) extends VFIO beyond PCIe to support platform-specific accelerator buses found in SoCs and FPGAs (e.g., AMD/Xilinx Versal). These devices don't have standard PCI BARs or config space — VFIO CDX provides the same container/group/device model adapted to their bus architecture.

---

## 11. Kernel Internals

### Architecture Overview

```
  Userspace
  ─────────────────────────────────────────────────────
  Kernel
  ┌──────────────────────────────────────────────────┐
  │                   VFIO Core                       │
  │  (drivers/vfio/vfio_main.c)                       │
  │  - Device fd management                           │
  │  - Group lifecycle                                │
  │  - ioctl dispatch                                 │
  │                                                   │
  │  ┌─────────────────┐  ┌───────────────────────┐  │
  │  │ IOMMU Backend   │  │ Bus Driver            │  │
  │  │                 │  │                       │  │
  │  │ vfio_iommu_     │  │ vfio_pci_core.c       │  │
  │  │ type1.c         │  │ vfio_pci.c            │  │
  │  │                 │  │                       │  │
  │  │ - DMA map/unmap │  │ - BAR access          │  │
  │  │ - Pin/unpin     │  │ - Config space virt.  │  │
  │  │ - IOMMU domain  │  │ - Interrupt setup     │  │
  │  │   management    │  │ - Device reset        │  │
  │  └────────┬────────┘  └───────────┬───────────┘  │
  │           │                       │               │
  └───────────┼───────────────────────┼───────────────┘
              │                       │
  ┌───────────▼───────────────────────▼───────────────┐
  │              IOMMU Subsystem                       │
  │  (drivers/iommu/iommu.c)                           │
  │                                                    │
  │  ┌───────────┐ ┌───────────┐ ┌──────────────┐     │
  │  │intel-iommu│ │ amd_iommu │ │arm-smmu-v3   │     │
  │  │  (VT-d)   │ │  (AMD-Vi) │ │              │     │
  │  └─────┬─────┘ └─────┬─────┘ └──────┬───────┘     │
  └────────┼──────────────┼──────────────┼─────────────┘
           │              │              │
  ─────────▼──────────────▼──────────────▼──── Hardware
         Intel           AMD            ARM
         IOMMU           IOMMU          SMMU
```

### vfio_iommu_type1.c — The IOMMU Backend

This is the core of VFIO's DMA management. Key data structures and operations:

**DMA mapping storage**: A red-black tree (`struct rb_root`) keyed by IOVA, storing `struct vfio_dma` entries:

```c
struct vfio_dma {
    struct rb_node     node;       // RB-tree node
    dma_addr_t         iova;       // start IOVA
    unsigned long      vaddr;      // userspace virtual address
    size_t             size;       // mapping size
    int                prot;       // IOMMU_READ | IOMMU_WRITE
    // ... pfn tracking, lock_acct
};
```

**MAP_DMA flow**:
1. Validate IOVA range doesn't overlap existing mappings (RB-tree lookup)
2. `pin_user_pages()` — pin the userspace pages, get physical addresses
3. `iommu_map()` — program the IOMMU page tables (IOVA → PA)
4. Insert `vfio_dma` into the RB-tree
5. Account pinned memory against `RLIMIT_MEMLOCK`

**UNMAP_DMA flow**:
1. Find the mapping in the RB-tree
2. `iommu_unmap()` — remove IOMMU page table entries
3. `unpin_user_pages()` — unpin the physical pages
4. Remove from RB-tree, adjust locked memory accounting

### vfio_pci_core.c — PCI Device Handling

Handles the PCI-specific aspects of VFIO devices:

**BAR access**:
- BARs with the `VFIO_REGION_INFO_FLAG_MMAP` flag: `mmap()` → `remap_pfn_range()` maps the BAR's physical MMIO region directly into userspace. Reads and writes go directly to hardware.
- BARs without mmap (or config space): `read()`/`write()` on the device fd at `region.offset + bar_offset`. The kernel mediates these, which allows it to virtualize certain registers.

**Config space virtualization**: The kernel intercepts writes to sensitive config space registers:
- **Command register**: The kernel manages bus-master enable, memory/IO space enable
- **BAR registers**: Writes are intercepted to track BAR addresses
- **MSI/MSI-X capability**: Managed by the interrupt subsystem
- Other capabilities may be hidden or virtualized for safety

**Interrupt setup (MSI-X)**:
1. `pci_alloc_irq_vectors()` — allocate MSI-X vectors from the kernel's interrupt subsystem
2. For each vector, `request_irq()` with a VFIO handler that calls `eventfd_signal()` on the userspace-provided eventfd
3. When the device fires MSI-X → CPU interrupt → VFIO handler → `eventfd_signal()` → userspace epoll wakes up

```
  Device MSI-X → CPU vector → vfio_msihandler() → eventfd_signal() → epoll_wait() returns
```

**Device reset**: Tries, in order:
1. PCI FLR (Function Level Reset) — `pcie_flr()`
2. PCI PM reset (D3hot → D0 transition) — `pci_pm_reset()`
3. Secondary bus reset (resets everything behind the bridge) — `pci_reset_bus()`

### VFIO and DMA-API Relationship

The IOMMU subsystem in Linux serves two masters:
1. **DMA-API** (`dma_map_page()`, `dma_alloc_coherent()`): Used by kernel drivers. The IOMMU manages translations transparently.
2. **VFIO**: Userspace drivers manage IOMMU translations explicitly via ioctls.

When a device is bound to vfio-pci, it's removed from the DMA-API's domain and placed in a VFIO-managed IOMMU domain. VFIO creates its own `iommu_domain` and attaches the device to it, giving full control over the page tables to userspace (via the type1 backend).

With `iommu=pt`, kernel-driver devices use an identity-mapped domain (IOVA = PA, effectively no translation), while VFIO devices get their own isolated domains with explicit mappings.

---

## 12. Key References

### Kernel Documentation
- `Documentation/driver-api/vfio.rst` — VFIO kernel documentation
- `Documentation/driver-api/vfio-mediated-device.rst` — mdev framework
- `Documentation/userspace-api/iommufd.rst` — IOMMUFD userspace API
- `include/uapi/linux/vfio.h` — VFIO ioctl definitions and data structures

### Specifications
- Intel VT-d Specification (Revision 4.1) — DMA remapping, interrupt remapping, posted interrupts, PASID, scalable mode
- AMD I/O Virtualization Technology (IOMMU) Specification — AMD-Vi architecture
- ARM System Memory Management Unit Architecture Specification (SMMUv3) — ARM IOMMU
- PCI Express Base Specification — ACS, ATS, PASID, SR-IOV capabilities
- vfio-user protocol specification (https://github.com/nutanix/libvfio-user)

### Papers and Talks
- Williamson, "VFIO: A User's Perspective", KVM Forum 2012 — original VFIO design talk
- Ben-Yehuda et al., "The Turtles Project: Design and Implementation of Nested Virtualization", OSDI 2010 — nested IOMMU concepts
- Amit et al., "vIOMMU: Efficient IOMMU Emulation", USENIX ATC 2011 — IOMMU virtualization
- Markuze et al., "True IOMMU Protection from DMA Attacks: When Copy is Faster than Zero Copy", ASPLOS 2016 — IOMMU performance analysis
- Neugebauer et al., "Understanding PCIe Performance for End Host Networking", SIGCOMM 2018 — PCIe/DMA performance characterization
- Intel DPDK Programmer's Guide — VFIO usage for NIC PMDs
- SPDK Documentation — NVMe userspace driver and vfio-user target
- Tian et al., "A Full GPU Virtualization Solution with Mediated Pass-Through", USENIX ATC 2014 — Intel GVT-g / mdev architecture
- Patel et al., "IOMMUFD and the future of VFIO", Linux Plumbers Conference 2022 — IOMMUFD design rationale
- Oracle, "VFIO User (vfio-user) protocol", 2021 — vfio-user specification

### Source Code
- `drivers/vfio/` — VFIO core, iommu backends, pci driver
- `drivers/iommu/` — IOMMU subsystem (intel, amd, arm-smmu)
- `include/uapi/linux/vfio.h` — userspace API
- `include/uapi/linux/iommufd.h` — IOMMUFD userspace API
- DPDK `drivers/bus/pci/linux/pci_vfio.c` — DPDK's VFIO integration
- SPDK `lib/env_dpdk/pci.c` + `lib/vfio_user/` — SPDK's VFIO and vfio-user code
- QEMU `hw/vfio/` — QEMU's VFIO device passthrough implementation

---

## See Also

- [ISA Critical Instructions](@/notes/hardware/isa_critical_instructions.md) — Virtualization instructions (VMX, ARM EL2, RISC-V H-ext) and IOMMU-related ISA features
- [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) — VFIO ioctls and related kernel interfaces in the hardware access section
- [GPU/TPU Accelerator Design](@/notes/hardware/gpu_tpu_accelerator_design.md) — GPU passthrough via VFIO is a key use case for accelerator access from VMs
- [io_uring Internals](@/notes/os/io_uring_internals.md) — Complementary userspace I/O path; SPDK uses both VFIO and io_uring for NVMe access
