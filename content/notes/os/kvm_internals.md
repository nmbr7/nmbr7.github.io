+++
title = "KVM Internals"
date = 2026-03-27
[taxonomies]
tags = ["kvm", "virtualization", "hypervisor", "vmm", "vmx", "svm", "ept", "vfio", "virtio", "live-migration"]
+++


# KVM Internals: Building Hypervisors with the Kernel-based Virtual Machine

A comprehensive, implementation-focused reference for KVM (Kernel-based Virtual Machine). Covers everything needed to build a working VMM from scratch: the ioctl API, hardware virtualization mechanics (VT-x/AMD-V/ARM/RISC-V), memory virtualization, interrupt handling, I/O models, live migration, security features, and kernel internals. Includes complete code examples and architecture diagrams.

---

## Table of Contents

1. [Architecture & Core Concepts](#1-architecture-core-concepts)
2. [KVM API & ioctl Interface](#2-kvm-api-ioctl-interface)
3. [Hardware Virtualization Support](#3-hardware-virtualization-support)
4. [Memory Virtualization](#4-memory-virtualization)
5. [Interrupt Virtualization](#5-interrupt-virtualization)
6. [I/O Virtualization](#6-i-o-virtualization)
7. [vCPU Scheduling & Performance](#7-vcpu-scheduling-performance)
8. [Live Migration](#8-live-migration)
9. [Security & Confidential Computing](#9-security-confidential-computing)
10. [KVM Kernel Internals](#10-kvm-kernel-internals)
11. [Building a Minimal VMM](#11-building-a-minimal-vmm)
12. [Advanced Topics](#12-advanced-topics)
13. [Key References](#13-key-references)

---

## 1. Architecture & Core Concepts

### 1.1 What KVM Is

KVM is a Linux kernel module (`kvm.ko` plus architecture-specific modules like `kvm-intel.ko` or `kvm-amd.ko`) that turns the Linux kernel into a hypervisor. It was created by Avi Kivity at Qumranet in 2006 and merged into Linux 2.6.20 (February 2007). KVM leverages existing Linux infrastructure -- the scheduler, memory management, device drivers -- rather than reimplementing them. Each VM is a regular Linux process; each vCPU is a regular Linux thread.

```
KVM Architecture:

  ┌─────────────────────────────────────────────────────────────┐
  │                     User Space                              │
  │                                                             │
  │  ┌─────────┐  ┌───────────┐  ┌────────────┐  ┌──────────┐ │
  │  │  QEMU   │  │Firecracker│  │Cloud Hyper- │  │  crosvm  │ │
  │  │         │  │           │  │   visor     │  │          │ │
  │  │ (full   │  │(microVM,  │  │(rust-vmm   │  │(ChromeOS │ │
  │  │ device  │  │ minimal   │  │ based)     │  │ VMs)     │ │
  │  │ model)  │  │ devices)  │  │            │  │          │ │
  │  └────┬────┘  └─────┬─────┘  └─────┬──────┘  └────┬─────┘ │
  │       │             │              │              │        │
  │       │     ioctl(/dev/kvm)        │              │        │
  ├───────┼─────────────┼──────────────┼──────────────┼────────┤
  │       ▼             ▼              ▼              ▼        │
  │  ┌─────────────────────────────────────────────────────┐   │
  │  │                    KVM Module                        │   │
  │  │  ┌───────────┐  ┌───────────┐  ┌─────────────────┐ │   │
  │  │  │ VM Mgmt   │  │ vCPU Mgmt │  │ Memory Mgmt     │ │   │
  │  │  │           │  │           │  │ (EPT/NPT/       │ │   │
  │  │  │           │  │           │  │  Stage-2)       │ │   │
  │  │  └───────────┘  └───────────┘  └─────────────────┘ │   │
  │  │  ┌───────────┐  ┌───────────┐  ┌─────────────────┐ │   │
  │  │  │ IRQ Chip  │  │ Timer     │  │ I/O Handling    │ │   │
  │  │  │ (LAPIC,   │  │ (PIT/     │  │ (PIO/MMIO      │ │   │
  │  │  │  IOAPIC)  │  │  HPET)    │  │  intercept)    │ │   │
  │  │  └───────────┘  └───────────┘  └─────────────────┘ │   │
  │  └──────────────────────┬──────────────────────────────┘   │
  │                         │                                   │
  │              ┌──────────┴──────────┐                        │
  │              │  Hardware VT        │                        │
  │              │  (VMX/SVM/EL2/H)    │                        │
  │              └─────────────────────┘                        │
  │                     Kernel Space                            │
  └─────────────────────────────────────────────────────────────┘
```

### 1.2 Type 1 vs Type 2 Debate

KVM defies clean classification:

- **Type 1 (bare-metal)**: Xen, VMware ESXi, Hyper-V -- hypervisor runs directly on hardware
- **Type 2 (hosted)**: VMware Workstation, VirtualBox -- hypervisor runs on top of a host OS

KVM is technically **Type 2** (it is a kernel module loaded into Linux), but once loaded, the Linux kernel itself becomes the hypervisor, making it functionally **Type 1**. The host kernel runs in VMX root mode (ring 0) and guest code runs in VMX non-root mode. The Linux kernel serves as the hypervisor and also handles scheduling, memory management, and device drivers -- roles traditionally handled by either the hypervisor (Type 1) or the host OS (Type 2). The academic consensus is to call KVM a "Type 1.5" or simply note that the classification doesn't cleanly apply.

### 1.3 KVM vs Other VMMs

KVM is the hypervisor (the kernel component). A VMM (Virtual Machine Monitor) is the userspace component that uses KVM. The most important VMMs:

| VMM | Language | Use Case | Code Size | Key Features |
|-----|----------|----------|-----------|--------------|
| **QEMU** | C | General-purpose | ~2M LoC | Full device emulation, TCG fallback, migration, snapshots |
| **Firecracker** | Rust | Serverless (AWS Lambda/Fargate) | ~50K LoC | Minimal devices, <125ms boot, rate limiters, jailer |
| **Cloud Hypervisor** | Rust | Cloud workloads | ~100K LoC | rust-vmm based, VFIO, vhost-user, PVH boot |
| **crosvm** | Rust | ChromeOS | ~150K LoC | Sandboxed processes per device, GPU passthrough, Wayland |
| **STRATOVIRT** | Rust | Huawei Cloud | ~80K LoC | Lightweight/standard modes, hot-plugging |
| **libkrun** | Rust+C | Container-like VMs | ~30K LoC | Library form factor, macOS/Linux |
| **Kata Containers** | Go+Rust | Secure containers | Varies | OCI-compatible, uses QEMU/Firecracker/Cloud Hypervisor |

### 1.4 The VM/vCPU Lifecycle

```
                    ┌──────────────────────────────────────────────┐
                    │          VMM Process Lifecycle               │
                    │                                              │
 open(/dev/kvm) ──► │  1. KVM_GET_API_VERSION (verify == 12)      │
                    │  2. KVM_CHECK_EXTENSION (probe capabilities) │
                    │  3. KVM_CREATE_VM ──► returns vm_fd          │
                    │                                              │
 vm_fd ──────────► │  4. KVM_SET_TSS_ADDR (x86 specific)         │
                    │  5. KVM_SET_IDENTITY_MAP_ADDR (x86)          │
                    │  6. KVM_CREATE_IRQCHIP (in-kernel APIC)      │
                    │  7. KVM_CREATE_PIT2 (i8254 timer)            │
                    │  8. KVM_SET_USER_MEMORY_REGION (guest RAM)   │
                    │  9. KVM_CREATE_VCPU ──► returns vcpu_fd      │
                    │                                              │
 vcpu_fd ────────► │ 10. mmap(vcpu_fd) ──► kvm_run shared page   │
                    │ 11. KVM_SET_CPUID2 (CPUID leaves)            │
                    │ 12. KVM_SET_SREGS (CR0, CR3, CR4, segments)  │
                    │ 13. KVM_SET_REGS (RIP, RSP, RFLAGS)         │
                    │ 14. KVM_SET_MSRS (EFER, STAR, etc.)          │
                    │                                              │
 vCPU Run Loop ──► │ 15. loop {                                   │
                    │         KVM_RUN ──► blocks until VM exit     │
                    │         match kvm_run.exit_reason {          │
                    │             IO  => handle_pio()              │
                    │             MMIO => handle_mmio()            │
                    │             HLT => wait_or_idle()            │
                    │             SHUTDOWN => break                │
                    │             ...                              │
                    │         }                                    │
                    │     }                                        │
                    │                                              │
 Teardown ───────► │ 16. close(vcpu_fd)                           │
                    │ 17. close(vm_fd)                             │
                    │ 18. close(kvm_fd)                            │
                    └──────────────────────────────────────────────┘
```

---

## 2. KVM API & ioctl Interface

The KVM API is entirely ioctl-based, operating on three levels of file descriptors:

1. **System fd** (`/dev/kvm`) -- global operations
2. **VM fd** (from `KVM_CREATE_VM`) -- per-VM operations
3. **vCPU fd** (from `KVM_CREATE_VCPU`) -- per-vCPU operations

### 2.1 System ioctls (on /dev/kvm)

```c
#include <linux/kvm.h>
#include <sys/ioctl.h>

// Open the KVM device
int kvm_fd = open("/dev/kvm", O_RDWR | O_CLOEXEC);

// --- KVM_GET_API_VERSION ---
// Returns the KVM API version. MUST be 12 (stable since 2007).
// Any other value means incompatible.
int api_version = ioctl(kvm_fd, KVM_GET_API_VERSION, 0);
assert(api_version == 12);

// --- KVM_CHECK_EXTENSION ---
// Probe for optional capabilities. Returns 0 (unsupported) or positive value.
// Critical extensions to check:
int has_irqchip     = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_IRQCHIP);
int has_pit2        = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_PIT2);
int has_user_mem    = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_USER_MEMORY);
int has_set_tss     = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_SET_TSS_ADDR);
int has_ext_cpuid   = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_EXT_CPUID);
int has_irqfd       = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_IRQFD);
int has_ioeventfd   = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_IOEVENTFD);
int has_dirty_log   = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_DIRTY_LOG_RING);
int max_vcpus       = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_MAX_VCPUS);
int has_imm_exit    = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_IMMEDIATE_EXIT);
int has_tsc_ctrl    = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_TSC_CONTROL);
int has_tsc_deadline = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_TSC_DEADLINE_TIMER);
int has_split_irq   = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_SPLIT_IRQCHIP);
int has_readonly_mem = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_READONLY_MEM);
int has_multi_addr  = ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_MULTI_ADDRESS_SPACE);

// --- KVM_GET_VCPU_MMAP_SIZE ---
// Returns the size of the kvm_run struct mmap region for each vCPU.
// Must be called before mmapping vCPU fds.
int vcpu_mmap_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
// Typically one page (4096) or slightly more.

// --- KVM_CREATE_VM ---
// Creates a new VM. Returns a VM file descriptor.
// The argument is machine type (0 = default, non-zero for ARM/MIPS machine types).
int vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);

// --- KVM_GET_SUPPORTED_CPUID (x86 only) ---
// Get the host's CPUID leaves that KVM supports.
// Used to filter/configure the guest's CPUID.
struct kvm_cpuid2 *cpuid = calloc(1, sizeof(*cpuid) + 256 * sizeof(cpuid->entries[0]));
cpuid->nent = 256;
ioctl(kvm_fd, KVM_GET_SUPPORTED_CPUID, cpuid);
```

### 2.2 VM ioctls (on vm_fd)

```c
// --- KVM_SET_TSS_ADDR (x86 only) ---
// Set the address of the Task State Segment (TSS) in guest physical memory.
// Required for the in-kernel LAPIC. Must be in the first 4GB.
// KVM needs 3 pages at this address. Common choice: 0xFFFBD000.
ioctl(vm_fd, KVM_SET_TSS_ADDR, 0xFFFBD000);

// --- KVM_SET_IDENTITY_MAP_ADDR (x86 only) ---
// Set the address for KVM's internal identity-mapped page table.
// Required for real-mode emulation. Must not overlap with guest RAM.
// Common choice: 0xFFFBC000.
uint64_t identity_base = 0xFFFBC000;
ioctl(vm_fd, KVM_SET_IDENTITY_MAP_ADDR, &identity_base);

// --- KVM_CREATE_IRQCHIP ---
// Create in-kernel PIC (8259), IOAPIC, and LAPIC.
// Must be done BEFORE creating vCPUs.
ioctl(vm_fd, KVM_CREATE_IRQCHIP, 0);

// --- KVM_CREATE_PIT2 ---
// Create an in-kernel i8254 PIT (Programmable Interval Timer).
struct kvm_pit_config pit_config = { .flags = KVM_PIT_SPEAKER_DUMMY };
ioctl(vm_fd, KVM_CREATE_PIT2, &pit_config);

// --- KVM_SET_USER_MEMORY_REGION ---
// Map a region of the VMM process's virtual memory as guest physical memory.
// This is the fundamental mechanism for providing guest RAM.
struct kvm_userspace_memory_region region = {
    .slot = 0,                      // Memory slot ID (0-based)
    .flags = 0,                     // 0 or KVM_MEM_LOG_DIRTY_PAGES or KVM_MEM_READONLY
    .guest_phys_addr = 0x0,         // Guest physical address (GPA) start
    .memory_size = 256 * 1024 * 1024, // Size in bytes (256 MB)
    .userspace_addr = (uint64_t)mmap(NULL, 256 * 1024 * 1024,
                                     PROT_READ | PROT_WRITE,
                                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE,
                                     -1, 0),
};
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// --- KVM_CREATE_VCPU ---
// Create a virtual CPU. Returns a vCPU file descriptor.
// Argument is the vCPU ID (0, 1, 2, ...).
int vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);

// --- KVM_IRQFD ---
// Bind an eventfd to a guest IRQ line for direct injection.
// When the eventfd is signaled, KVM injects the interrupt without a VMM ioctl.
int efd = eventfd(0, EFD_CLOEXEC);
struct kvm_irqfd irqfd = {
    .fd = efd,
    .gsi = 5,           // Guest System Interrupt number
    .flags = 0,         // or KVM_IRQFD_FLAG_RESAMPLE for level-triggered
};
ioctl(vm_fd, KVM_IRQFD, &irqfd);

// For level-triggered IRQs with EOI notification:
int resample_efd = eventfd(0, EFD_CLOEXEC);
struct kvm_irqfd irqfd_level = {
    .fd = efd,
    .gsi = 10,
    .flags = KVM_IRQFD_FLAG_RESAMPLE,
    .resamplefd = resample_efd,   // KVM signals this when guest does EOI
};

// --- KVM_IOEVENTFD ---
// Bind an eventfd to a specific I/O port or MMIO address.
// When the guest writes to this address, KVM signals the eventfd
// WITHOUT causing a VM exit to the VMM. Used as virtio doorbell.
int io_efd = eventfd(0, EFD_CLOEXEC);
struct kvm_ioeventfd ioeventfd = {
    .datamatch = 0,     // Optional data value to match
    .addr = 0x500,      // PIO port or MMIO address
    .len = 4,           // Access width (1, 2, 4, or 8 bytes)
    .fd = io_efd,
    .flags = 0,         // KVM_IOEVENTFD_FLAG_PIO for port I/O
                        // KVM_IOEVENTFD_FLAG_DATAMATCH to filter by value
};
ioctl(vm_fd, KVM_IOEVENTFD, &ioeventfd);

// --- KVM_SET_GSI_ROUTING ---
// Configure IRQ routing table. Maps GSI numbers to interrupt controller pins.
struct kvm_irq_routing *routing;
int num_entries = 24;  // legacy ISA IRQs
size_t size = sizeof(*routing) + num_entries * sizeof(routing->entries[0]);
routing = calloc(1, size);
routing->nr = num_entries;

// Example: route GSI 0 to IOAPIC pin 2 (PIT timer on IOAPIC)
routing->entries[0].gsi = 0;
routing->entries[0].type = KVM_IRQ_ROUTING_IRQCHIP;
routing->entries[0].u.irqchip.irqchip = KVM_IRQCHIP_IOAPIC;
routing->entries[0].u.irqchip.pin = 2;

// Example: MSI routing
routing->entries[1].gsi = 24;  // first non-legacy GSI
routing->entries[1].type = KVM_IRQ_ROUTING_MSI;
routing->entries[1].u.msi.address_lo = 0xFEE00000;  // LAPIC base + dest
routing->entries[1].u.msi.address_hi = 0;
routing->entries[1].u.msi.data = 0x41;  // vector 0x41, edge-triggered

ioctl(vm_fd, KVM_SET_GSI_ROUTING, routing);

// --- KVM_GET_DIRTY_LOG ---
// Retrieve bitmap of pages dirtied by the guest since last call.
// Used for live migration.
struct kvm_dirty_log dirty_log = {
    .slot = 0,  // memory slot
};
// bitmap: 1 bit per page, must be pre-allocated
size_t bitmap_size = (region.memory_size / 4096 + 7) / 8;
dirty_log.dirty_bitmap = calloc(1, bitmap_size);
ioctl(vm_fd, KVM_GET_DIRTY_LOG, &dirty_log);

// --- KVM_CLEAR_DIRTY_LOG (newer, more efficient) ---
// Atomically get and clear dirty bits for a sub-range.
struct kvm_clear_dirty_log clear = {
    .slot = 0,
    .num_pages = 1024,
    .first_page = 0,
    .dirty_bitmap = calloc(1, 1024 / 8),
};
ioctl(vm_fd, KVM_CLEAR_DIRTY_LOG, &clear);

// --- KVM_ENABLE_CAP ---
// Enable a specific VM capability.
struct kvm_enable_cap cap = {
    .cap = KVM_CAP_SPLIT_IRQCHIP,
    .args[0] = 24,  // number of GSI routes for split irqchip
};
ioctl(vm_fd, KVM_ENABLE_CAP, &cap);
```

### 2.3 vCPU ioctls (on vcpu_fd)

```c
// --- mmap the kvm_run structure ---
// This shared memory region is how KVM communicates exit information to userspace.
struct kvm_run *run = mmap(NULL, vcpu_mmap_size,
                           PROT_READ | PROT_WRITE, MAP_SHARED,
                           vcpu_fd, 0);

// --- KVM_SET_CPUID2 (x86 only) ---
// Set the CPUID leaves the guest will see.
// Typically start from KVM_GET_SUPPORTED_CPUID and modify.
// Critical modifications:
//   - Set vendor string
//   - Hide hypervisor features you don't support
//   - Expose KVM paravirt CPUID (0x40000000, 0x40000001)
ioctl(vcpu_fd, KVM_SET_CPUID2, cpuid);

// --- KVM_GET_SREGS / KVM_SET_SREGS ---
// Get/set special registers: segment registers, CR0-CR4, EFER, IDT, GDT, etc.
struct kvm_sregs sregs;
ioctl(vcpu_fd, KVM_GET_SREGS, &sregs);

// For real mode (simplest setup):
sregs.cs.base = 0;
sregs.cs.selector = 0;
// For protected mode: set CS/DS/ES/SS/FS/GS with proper limits, types, and DPL
// For long mode: set CR0.PE, CR0.PG, CR4.PAE, EFER.LME, EFER.LMA, CR3 = page table

ioctl(vcpu_fd, KVM_SET_SREGS, &sregs);

// --- KVM_GET_REGS / KVM_SET_REGS ---
// Get/set general-purpose registers, RIP, RSP, RFLAGS.
struct kvm_regs regs = {
    .rip = 0x1000,           // Entry point
    .rsp = 0x0,              // Stack pointer
    .rflags = 0x2,           // Bit 1 must be set (reserved)
    // rax, rbx, rcx, rdx, rsi, rdi, rbp, r8-r15 all zero
};
ioctl(vcpu_fd, KVM_SET_REGS, &regs);

// --- KVM_SET_MSRS ---
// Set Model-Specific Registers. Critical MSRs:
struct {
    struct kvm_msrs header;
    struct kvm_msr_entry entries[10];
} msrs = {
    .header.nmsrs = 4,
    .entries = {
        { .index = MSR_IA32_EFER,     .data = 0 },  // EFER (LME, LMA, SCE)
        { .index = MSR_STAR,          .data = 0 },  // SYSCALL target
        { .index = MSR_LSTAR,         .data = 0 },  // 64-bit SYSCALL entry
        { .index = MSR_IA32_TSC,      .data = 0 },  // TSC value
    },
};
ioctl(vcpu_fd, KVM_SET_MSRS, &msrs);

// --- KVM_GET_MSRS ---
// Read MSRs. Specify which MSRs to read in the entries array.
// On return, data fields are filled in. Returns number of MSRs read.
msrs.header.nmsrs = 1;
msrs.entries[0].index = MSR_IA32_TSC;
int nmsrs = ioctl(vcpu_fd, KVM_GET_MSRS, &msrs);
// msrs.entries[0].data now contains TSC value

// --- KVM_GET_FPU / KVM_SET_FPU ---
struct kvm_fpu fpu;
ioctl(vcpu_fd, KVM_GET_FPU, &fpu);
// fpu.fpr[8][16] -- FP registers
// fpu.fcw -- FP control word
// fpu.xmm[16][16] -- SSE registers
// fpu.mxcsr -- MXCSR register
ioctl(vcpu_fd, KVM_SET_FPU, &fpu);

// --- KVM_GET_LAPIC / KVM_SET_LAPIC ---
// Get/set the LAPIC state (when in-kernel IRQCHIP is used).
struct kvm_lapic_state lapic;
ioctl(vcpu_fd, KVM_GET_LAPIC, &lapic);
// lapic.regs[KVM_APIC_REG_SIZE] -- raw APIC register page
ioctl(vcpu_fd, KVM_SET_LAPIC, &lapic);

// --- KVM_INTERRUPT ---
// Inject an external interrupt. Only when NOT using in-kernel IRQCHIP.
struct kvm_interrupt irq = { .irq = 0x30 };  // vector number
ioctl(vcpu_fd, KVM_INTERRUPT, &irq);

// --- KVM_SET_SIGNAL_MASK ---
// Set the signal mask for the vCPU thread while running guest code.
// Signals not in this mask can kick the vCPU out of guest mode.
struct kvm_signal_mask *sigmask = calloc(1, sizeof(*sigmask) + sizeof(sigset_t));
sigmask->len = sizeof(sigset_t);
sigset_t *set = (sigset_t *)sigmask->sigset;
sigemptyset(set);
sigaddset(set, SIGUSR1);  // Only block SIGUSR1 during KVM_RUN
ioctl(vcpu_fd, KVM_SET_SIGNAL_MASK, sigmask);

// --- KVM_GET_VCPU_EVENTS / KVM_SET_VCPU_EVENTS ---
// Get/set pending exceptions, interrupts, NMIs, SMIs.
// Critical for migration (must preserve in-flight events).
struct kvm_vcpu_events events;
ioctl(vcpu_fd, KVM_GET_VCPU_EVENTS, &events);
// events.exception.injected, events.exception.nr, events.exception.has_error_code
// events.interrupt.injected, events.interrupt.nr, events.interrupt.soft
// events.nmi.injected, events.nmi.pending, events.nmi.masked
// events.sipi_vector

// --- KVM_RUN ---
// Enter guest mode. Blocks until a VM exit occurs.
ioctl(vcpu_fd, KVM_RUN, 0);
// Exit reason is in run->exit_reason
```

### 2.4 The kvm_run Shared Memory Region

The `struct kvm_run` is the shared memory page between KVM and userspace. It contains:

```c
struct kvm_run {
    /* in: set by userspace */
    __u8  request_interrupt_window;  // ask KVM to exit when interrupt window opens
    __u8  immediate_exit;            // exit immediately (for signal handling race fix)
    __u8  padding1[6];

    /* out: set by KVM on exit */
    __u32 exit_reason;               // WHY we exited (see below)
    __u8  ready_for_interrupt_injection;  // can we inject an interrupt now?
    __u8  if_flag;                   // guest's IF flag (interrupts enabled?)
    __u16 flags;

    /* Architecture-specific state */
    __u64 cr8;                       // guest CR8 (TPR)
    __u64 apic_base;                 // guest APIC base MSR

    /* Exit-reason-specific data (union) */
    union {
        /* KVM_EXIT_IO */
        struct {
            __u8  direction;         // KVM_EXIT_IO_IN or KVM_EXIT_IO_OUT
            __u8  size;              // access width: 1, 2, or 4
            __u16 port;              // I/O port number
            __u32 count;             // number of accesses (for REP INS/OUTS)
            __u64 data_offset;       // offset into kvm_run page for data
        } io;

        /* KVM_EXIT_MMIO */
        struct {
            __u64 phys_addr;         // guest physical address
            __u8  data[8];           // data read/written
            __u32 len;               // access length
            __u8  is_write;          // 1 = write, 0 = read
        } mmio;

        /* KVM_EXIT_HYPERCALL */
        struct {
            __u64 nr;                // hypercall number
            __u64 args[6];           // arguments
            __u64 ret;               // return value (set by VMM)
            __u32 longmode;          // 1 = 64-bit mode
            __u32 pad;
        } hypercall;

        /* KVM_EXIT_INTERNAL_ERROR */
        struct {
            __u32 suberror;          // KVM_INTERNAL_ERROR_EMULATION,
                                     // KVM_INTERNAL_ERROR_SIMUL_EX,
                                     // KVM_INTERNAL_ERROR_DELIVERY_EV
            __u32 ndata;
            __u64 data[16];          // additional error data
        } internal;

        /* KVM_EXIT_SYSTEM_EVENT */
        struct {
            __u32 type;              // KVM_SYSTEM_EVENT_SHUTDOWN,
                                     // KVM_SYSTEM_EVENT_RESET,
                                     // KVM_SYSTEM_EVENT_CRASH,
                                     // KVM_SYSTEM_EVENT_WAKEUP,
                                     // KVM_SYSTEM_EVENT_SUSPEND,
                                     // KVM_SYSTEM_EVENT_SEV_TERM
            __u32 ndata;
            __u64 data[16];
        } system_event;

        /* KVM_EXIT_IOAPIC_EOI */
        struct {
            __u8 vector;             // which vector was EOI'd
        } eoi;

        /* KVM_EXIT_HYPERV */
        struct kvm_hyperv_exit hyperv;  // Hyper-V specific exits

        // ... other exit types
    };
};
```

### 2.5 Exit Reasons -- Complete Handler Guide

```c
while (1) {
    int ret = ioctl(vcpu_fd, KVM_RUN, 0);
    if (ret < 0 && errno != EINTR) {
        perror("KVM_RUN failed");
        break;
    }

    switch (run->exit_reason) {

    case KVM_EXIT_IO: {
        // Guest executed IN or OUT instruction on an I/O port not handled
        // by an in-kernel device or IOEVENTFD.
        //
        // Port I/O is the primary mechanism for legacy x86 device communication.
        // Common ports:
        //   0x3F8-0x3FF: COM1 (serial port)
        //   0x60, 0x64:  PS/2 keyboard controller
        //   0x20, 0x21:  PIC master
        //   0xA0, 0xA1:  PIC slave
        //   0xCF8, 0xCFC: PCI config space access
        //   0x40-0x43:   PIT (i8254)
        //   0x70, 0x71:  CMOS/RTC

        uint8_t *data = (uint8_t *)run + run->io.data_offset;

        if (run->io.direction == KVM_EXIT_IO_OUT) {
            // Guest is writing data to a port
            if (run->io.port == 0x3F8 && run->io.size == 1) {
                // COM1 output -- simplest "display" for a minimal VMM
                write(STDOUT_FILENO, data, 1);
            }
            // Handle other ports (PCI config writes, etc.)
        } else {
            // Guest is reading from a port (KVM_EXIT_IO_IN)
            // VMM must fill in the data buffer with the response
            memset(data, 0xFF, run->io.size);  // default: all 1s (nothing connected)
        }
        break;
    }

    case KVM_EXIT_MMIO: {
        // Guest accessed a memory-mapped I/O region not backed by RAM.
        // Any GPA not covered by KVM_SET_USER_MEMORY_REGION causes this exit.
        //
        // Common MMIO regions:
        //   0xFEE00000: LAPIC registers (if not using in-kernel IRQCHIP)
        //   0xFEC00000: IOAPIC registers
        //   PCIe BAR regions mapped by the guest OS
        //   Virtio MMIO device regions (at addresses you choose)

        if (run->mmio.is_write) {
            handle_mmio_write(run->mmio.phys_addr, run->mmio.data, run->mmio.len);
        } else {
            handle_mmio_read(run->mmio.phys_addr, run->mmio.data, run->mmio.len);
        }
        break;
    }

    case KVM_EXIT_HLT:
        // Guest executed HLT instruction (idle).
        // Options:
        //   1. Wait for an interrupt (poll or epoll on IRQFD eventfds)
        //   2. If no interrupts expected, the VM is halted -- exit the loop
        //   3. Use KVM's halt-polling (kernel-side busy-wait before sleeping)
        // If using a multi-vCPU VM, only this vCPU is halted; others continue.
        break;

    case KVM_EXIT_SHUTDOWN:
        // Guest executed triple fault or shutdown sequence.
        // This is usually a bug in guest code (bad IDT, bad GDT, etc.)
        // or a deliberate ACPI shutdown.
        fprintf(stderr, "VM shutdown (triple fault)\n");
        return;

    case KVM_EXIT_FAIL_ENTRY: {
        // KVM could not enter guest mode at all.
        // Common causes:
        //   - Invalid guest state (bad segment registers, bad CR0/CR4 combination)
        //   - VMCS/VMCB misconfiguration
        // run->fail_entry.hardware_entry_failure_reason contains
        // the hardware-specific error code (Intel VMCS exit reason field).
        fprintf(stderr, "KVM_EXIT_FAIL_ENTRY: reason=0x%llx\n",
                run->fail_entry.hardware_entry_failure_reason);
        return;
    }

    case KVM_EXIT_INTERNAL_ERROR: {
        // KVM internal error -- typically emulation failure.
        // suberror values:
        //   KVM_INTERNAL_ERROR_EMULATION (1): KVM could not emulate an instruction
        //   KVM_INTERNAL_ERROR_SIMUL_EX (2):  simultaneous exceptions
        //   KVM_INTERNAL_ERROR_DELIVERY_EV (3): error during event delivery
        //   KVM_INTERNAL_ERROR_UNEXPECTED_EXIT_REASON (4): hardware bug?
        fprintf(stderr, "KVM internal error: suberror=%d\n",
                run->internal.suberror);
        for (uint32_t i = 0; i < run->internal.ndata; i++)
            fprintf(stderr, "  data[%d] = 0x%llx\n", i, run->internal.data[i]);
        return;
    }

    case KVM_EXIT_SYSTEM_EVENT:
        // Guest requested a system-level event (e.g., ACPI shutdown/reset/crash).
        // type: KVM_SYSTEM_EVENT_SHUTDOWN, _RESET, _CRASH
        if (run->system_event.type == KVM_SYSTEM_EVENT_SHUTDOWN) {
            printf("Guest requested ACPI shutdown\n");
            return;
        } else if (run->system_event.type == KVM_SYSTEM_EVENT_RESET) {
            printf("Guest requested reset\n");
            // Reset VM state and re-enter
        }
        break;

    case KVM_EXIT_IOAPIC_EOI:
        // Guest wrote to the LAPIC EOI register for a level-triggered interrupt.
        // Only occurs with split IRQCHIP mode.
        // VMM should deassert the interrupt and optionally re-trigger if still pending.
        handle_eoi(run->eoi.vector);
        break;

    case KVM_EXIT_HYPERV:
        // Hyper-V specific exits (synthetic MSR access, hypercalls, synic).
        // Used when emulating Hyper-V enlightenments for Windows guests.
        break;

    case KVM_EXIT_DEBUG:
        // Guest hit a debug breakpoint/watchpoint set via KVM_SET_GUEST_DEBUG.
        break;

    case KVM_EXIT_X86_RDMSR:
    case KVM_EXIT_X86_WRMSR:
        // MSR access that KVM doesn't handle and user_msr filtering is enabled
        // (KVM_CAP_X86_USER_SPACE_MSR).
        break;

    default:
        fprintf(stderr, "Unhandled exit reason: %d\n", run->exit_reason);
        return;
    }
}
```

### 2.6 Important KVM Capabilities

Key `KVM_CHECK_EXTENSION` values and their meaning:

| Capability | Value | Meaning |
|-----------|-------|---------|
| `KVM_CAP_IRQCHIP` | 0 or 1 | In-kernel APIC/IOAPIC/PIC emulation |
| `KVM_CAP_PIT2` | 0 or 1 | In-kernel i8254 PIT |
| `KVM_CAP_USER_MEMORY` | 0 or 1 | KVM_SET_USER_MEMORY_REGION support |
| `KVM_CAP_SET_TSS_ADDR` | 0 or 1 | KVM_SET_TSS_ADDR support (x86) |
| `KVM_CAP_EXT_CPUID` | 0 or 1 | Extended CPUID (KVM_GET_SUPPORTED_CPUID) |
| `KVM_CAP_NR_VCPUS` | N | Recommended max vCPUs (soft limit) |
| `KVM_CAP_MAX_VCPUS` | N | Hard max vCPUs (up to 1024+) |
| `KVM_CAP_IRQFD` | 0 or 1 | IRQ injection via eventfd |
| `KVM_CAP_IOEVENTFD` | 0 or 1 | I/O event notification via eventfd |
| `KVM_CAP_SPLIT_IRQCHIP` | 0 or 1 | Userspace IOAPIC + in-kernel LAPIC |
| `KVM_CAP_IMMEDIATE_EXIT` | 0 or 1 | `kvm_run.immediate_exit` field support |
| `KVM_CAP_TSC_CONTROL` | 0 or 1 | TSC frequency scaling |
| `KVM_CAP_TSC_DEADLINE_TIMER` | 0 or 1 | TSC deadline LAPIC timer mode |
| `KVM_CAP_READONLY_MEM` | 0 or 1 | KVM_MEM_READONLY flag support |
| `KVM_CAP_DIRTY_LOG_RING` | N | Ring-based dirty page tracking (N = max entries) |
| `KVM_CAP_X86_USER_SPACE_MSR` | 0 or 1 | Forward unhandled MSR access to userspace |
| `KVM_CAP_SGX_ATTRIBUTE` | 0 or 1 | SGX virtualization support |
| `KVM_CAP_MULTI_ADDRESS_SPACE` | N | Number of separate address spaces (used by SEV) |

---

## 3. Hardware Virtualization Support

### 3.1 Intel VT-x (VMX)

Intel VT-x introduces two CPU modes:

```
                        ┌─────────────────────────────────┐
                        │     VMX Root Mode (Host)        │
                        │   Ring 0: Hypervisor/KVM        │
                        │   Ring 3: VMM (QEMU, etc.)      │
                        │                                 │
                        │  ┌──────────┐  ┌──────────┐    │
                        │  │ VMLAUNCH │  │VM Exit   │    │
                        │  │ VMRESUME │  │(automatic│    │
                        │  │          │  │ trap)    │    │
                        │  └────┬─────┘  └────▲─────┘    │
                        └───────┼─────────────┼──────────┘
                                │             │
                                ▼             │
                        ┌───────┴─────────────┴──────────┐
                        │    VMX Non-Root Mode (Guest)    │
                        │   Ring 0: Guest kernel          │
                        │   Ring 3: Guest applications    │
                        │                                 │
                        │   Has its OWN ring 0/3 but      │
                        │   certain instructions cause    │
                        │   automatic VM exits            │
                        └─────────────────────────────────┘
```

**VMCS (Virtual Machine Control Structure):**

The VMCS is a hardware-defined 4KB data structure that controls VMX operation. It contains:

```
VMCS Layout (Intel SDM Vol. 3, Chapter 24):

 ┌──────────────────────────────────────────────────────────┐
 │ VMCS Header                                              │
 │   Revision ID, abort indicator, VMCS state              │
 ├──────────────────────────────────────────────────────────┤
 │ Guest-State Area                                         │
 │   CR0, CR3, CR4, DR7                                    │
 │   RSP, RIP, RFLAGS                                      │
 │   CS/SS/DS/ES/FS/GS/LDTR/TR (sel, base, limit, access) │
 │   GDTR, IDTR (base, limit)                              │
 │   MSRs: IA32_DEBUGCTL, IA32_SYSENTER_*, IA32_EFER,     │
 │          IA32_PAT, IA32_PERF_GLOBAL_CTRL                │
 │   SMBASE, activity state, interruptibility state        │
 │   Pending debug exceptions, VMCS link pointer           │
 │   Preemption timer value, PDPTEs (if PAE), guest CET    │
 ├──────────────────────────────────────────────────────────┤
 │ Host-State Area                                          │
 │   CR0, CR3, CR4                                         │
 │   RSP, RIP (host entry point after VM exit)             │
 │   CS/SS/DS/ES/FS/GS/TR selectors, FS/GS/TR bases      │
 │   GDTR base, IDTR base                                  │
 │   MSRs: IA32_SYSENTER_*, IA32_EFER, IA32_PAT,         │
 │          IA32_PERF_GLOBAL_CTRL                          │
 ├──────────────────────────────────────────────────────────┤
 │ VM-Execution Control Fields                              │
 │   Pin-based controls:                                    │
 │     External interrupt exiting, NMI exiting,            │
 │     virtual NMIs, preemption timer, posted interrupts   │
 │   Processor-based controls (primary):                    │
 │     HLT exiting, INVLPG exiting, MWAIT exiting,        │
 │     RDPMC exiting, RDTSC exiting, CR3-load/store exit,  │
 │     MOV-DR exiting, I/O bitmap, MSR bitmap,             │
 │     use TPR shadow, activate secondary controls         │
 │   Processor-based controls (secondary):                  │
 │     Virtualize APIC, enable EPT, descriptor-table exit, │
 │     RDTSCP, virtualize x2APIC, enable VPID, WBINVD,    │
 │     unrestricted guest, APIC register virtualization,   │
 │     virtual interrupt delivery, PAUSE-loop exiting,     │
 │     RDRAND exiting, INVPCID, VMFUNC, ENCLS exiting,    │
 │     SPP (Sub-Page Permission), PT uses guest phys addr, │
 │     TSC scaling, WAITPKG exiting, ENCLV exiting         │
 │   Exception bitmap: 32-bit bitmap (1 = exit on this exception) │
 │   I/O bitmap addresses (A and B)                        │
 │   MSR bitmap address                                     │
 │   EPT pointer (EPTP)                                     │
 │   VPID                                                   │
 │   PLE gap, PLE window (pause-loop exiting)              │
 ├──────────────────────────────────────────────────────────┤
 │ VM-Exit Control Fields                                   │
 │   VM exit controls: save debug controls, host address-   │
 │   space size (64-bit host), load IA32_PERF/PAT/EFER,    │
 │   acknowledge interrupt on exit, save/clear IA32_BNDCFGS│
 │   VM exit MSR-store/load count and addresses            │
 ├──────────────────────────────────────────────────────────┤
 │ VM-Entry Control Fields                                  │
 │   VM entry controls: load debug controls, IA32 mode     │
 │   guest, entry to SMM, deactivate dual-monitor          │
 │   VM entry interruption-information field               │
 │   VM entry exception error code                         │
 │   VM entry instruction length                           │
 │   VM entry MSR-load count and address                   │
 ├──────────────────────────────────────────────────────────┤
 │ VM-Exit Information Fields (read-only)                   │
 │   Exit reason (basic + specific)                        │
 │   Exit qualification (instruction-specific details)     │
 │   VM exit interruption information/error code           │
 │   IDT-vectoring information/error code                  │
 │   VM exit instruction length/information                │
 │   Guest-linear/physical address                         │
 └──────────────────────────────────────────────────────────┘
```

**Key VMX instructions:**

| Instruction | Action |
|-------------|--------|
| `VMXON` | Enable VMX operation. Allocates a VMXON region. |
| `VMXOFF` | Disable VMX operation. |
| `VMCLEAR` | Initialize/clear a VMCS. Must be done before VMPTRLD. |
| `VMPTRLD` | Load a VMCS as the "current" VMCS for this logical processor. |
| `VMREAD` | Read a field from the current VMCS. |
| `VMWRITE` | Write a field to the current VMCS. |
| `VMLAUNCH` | Enter VMX non-root mode (first entry for this VMCS). |
| `VMRESUME` | Enter VMX non-root mode (subsequent entries). |
| `VMCALL` | Guest-to-host hypercall. Causes unconditional VM exit. |
| `INVEPT` | Invalidate EPT TLB entries. |
| `INVVPID` | Invalidate TLB entries tagged with a specific VPID. |

**What causes VM exits (configurable in VMCS controls):**

- Privileged instructions: CPUID (always), HLT (configurable), INVLPG, MOV CR, MOV DR, IN/OUT, RDMSR/WRMSR (configurable via MSR bitmap), LGDT/LIDT/LLDT/LTR, SGDT/SIDT/SLDT/STR
- Memory: EPT violation (unmapped GPA), EPT misconfiguration
- Interrupts: external interrupt, NMI (configurable)
- Special: triple fault, INIT, SIPI, VMCALL, preemption timer, MONITOR/MWAIT, PAUSE (configurable via PLE), RDTSC/RDTSCP (configurable)

**Unrestricted Guest Mode** (VT-x + EPT on Westmere and later): Allows running real-mode and unpaged protected-mode guest code without emulation. Before this, KVM had to emulate real mode in software. This is why `KVM_SET_TSS_ADDR` is required -- KVM uses it for real-mode emulation on older hardware.

### 3.2 AMD-V (SVM)

AMD-V (also called SVM -- Secure Virtual Machine) is architecturally similar to VT-x but uses different data structures:

```
AMD-V vs Intel VT-x Comparison:

Feature            Intel VT-x                AMD-V (SVM)
─────────────────────────────────────────────────────────────
Control struct     VMCS (4KB, hardware-      VMCB (4KB, normal
                   managed, VMREAD/          memory, direct
                   VMWRITE access)           load/store access)
Enter guest        VMLAUNCH / VMRESUME       VMRUN
Guest -> host      VM exit (automatic)       #VMEXIT (automatic)
Hypercall          VMCALL                    VMMCALL
Enable             VMXON                     set EFER.SVME
EPT equivalent     EPT (Extended Page        NPT (Nested Page
                   Tables), EPTP in VMCS     Tables), nCR3 in VMCB
TLB tagging        VPID                      ASID (Address Space ID)
Posted interrupts  Posted Interrupts         AVIC (AMD Virtual
                   (PI descriptor)           Interrupt Controller)
Nested virt        VMCS shadowing            Nested SVM (VMCB
                                             caching)
```

**VMCB (Virtual Machine Control Block):**

```c
// The VMCB is a 4KB page with two halves:
// Offset 0x000-0x3FF: Control area
// Offset 0x400-0xFFF: State save area (guest register state)

// Key control area fields:
struct vmcb_control {
    uint32_t intercept_cr;      // CR read/write intercepts (bitmap)
    uint32_t intercept_dr;      // DR read/write intercepts
    uint32_t intercept_exceptions; // exception intercepts (bitmap, like VMCS exception bitmap)
    uint64_t intercepts;        // miscellaneous intercepts (INTR, NMI, SMI, INIT,
                                // VINTR, CR0 selective, RDIDTR, RDGDTR, RDLDTR, RDTR,
                                // RDTSC, RDPMC, PUSHF, POPF, CPUID, RSM, IRET,
                                // INTn, INVD, PAUSE, HLT, INVLPG, INVLPGA,
                                // I/O bitmap, MSR bitmap, task switch, FERR freeze,
                                // shutdown, VMRUN, VMMCALL, VMLOAD, VMSAVE, STGI, CLGI,
                                // SKINIT, RDTSCP, ICEBP, WBINVD, MONITOR, MWAIT,
                                // XSETBV, RDPRU, EFER write after finish, CR0-15 write after)
    uint64_t iopm_base_pa;      // physical address of I/O permission bitmap (12KB, 3 pages)
    uint64_t msrpm_base_pa;     // physical address of MSR permission bitmap (8KB, 2 pages)
    uint64_t tsc_offset;        // added to RDTSC/RDTSCP results
    uint32_t guest_asid;        // ASID for TLB tagging (must be non-zero)
    uint8_t  tlb_ctl;           // TLB flush control on VMRUN
    uint8_t  v_intr;            // virtual interrupt control
    uint64_t exitcode;          // exit reason (set by hardware on #VMEXIT)
    uint64_t exitinfo1;         // additional exit info (qualification)
    uint64_t exitinfo2;         // additional exit info
    uint64_t n_cr3;             // Nested page table root (NPT CR3)
    uint64_t avic_backing_page; // AVIC backing page physical address
    // ... more fields
};
```

**Advantages of VMCB over VMCS:**
- VMCB is regular memory -- you can `memcpy` it, inspect it with `gdb`, no special instructions needed
- VMCS requires `VMREAD`/`VMWRITE` for every field access (more complex code)
- VMCB makes debugging easier and nested virtualization simpler

### 3.3 ARM Virtualization (EL2)

ARM's virtualization is built into the exception level hierarchy:

```
ARM Exception Levels with Virtualization:

  ┌──────────────────────────────────────────────────┐
  │ EL3: Secure Monitor (TrustZone)                  │
  │   - SMC handling, secure/non-secure world switch │
  ├──────────────────────────────────────────────────┤
  │ EL2: Hypervisor                                  │
  │   - Stage-2 page tables (IPA -> PA)              │
  │   - Trap/emulate sensitive instructions          │
  │   - HVC (Hypervisor Call) handling               │
  │   - VTTBR_EL2: guest stage-2 table base         │
  │   - HCR_EL2: hypervisor configuration register   │
  │   - VBAR_EL2: hypervisor exception vectors       │
  ├──────────────────────────────────────────────────┤
  │ EL1: Guest OS kernel                             │
  │   - Full kernel privileges (but trapped by EL2)  │
  │   - Stage-1 page tables (VA -> IPA)              │
  │   - System registers trapped as configured       │
  ├──────────────────────────────────────────────────┤
  │ EL0: Guest userspace                             │
  │   - Applications                                 │
  └──────────────────────────────────────────────────┘
```

**VHE (Virtualized Host Extensions) -- ARMv8.1:**

Without VHE, the host kernel runs at EL1 and the hypervisor runs at EL2. On every VM exit, KVM has to save/restore the host kernel's EL1 state. VHE allows the host kernel to run directly at EL2, eliminating this overhead:

```
Without VHE:                       With VHE (ARMv8.1+):

EL2: KVM "stub"                    EL2: Host kernel + KVM
      ▲                                  ▲
      │ HVC / trap                       │ trap (direct)
      │                                  │
EL1: Host kernel                    EL1: Guest kernel
      or Guest kernel               (only guests use EL1)
```

VHE is detected via `ID_AA64MMFR1_EL1.VH` and KVM on ARM always uses VHE when available (all modern ARMv8.1+ SoCs). The VM exit cost drops ~30-50% with VHE.

**Stage-2 Translation (ARM's EPT equivalent):**

```
Guest VA ──[TTBR0_EL1/TTBR1_EL1]──> IPA ──[VTTBR_EL2]──> PA

- Stage-1: Guest-controlled (VA -> IPA). Guest OS sets these page tables.
- Stage-2: Hypervisor-controlled (IPA -> PA). KVM sets these page tables.
- Hardware walks both stages automatically (2D page table walk).
- Permission intersection: the final permission is the most restrictive
  of Stage-1 and Stage-2.
```

KVM on ARM source: `arch/arm64/kvm/` -- key files:
- `hyp/` -- EL2 code that runs at hypervisor privilege
- `handle_exit.c` -- ARM VM exit handler dispatch
- `mmu.c` -- Stage-2 page table management
- `sys_regs.c` -- System register emulation/trapping

### 3.4 RISC-V H Extension

The RISC-V Hypervisor extension adds a two-level address translation similar to Intel EPT and ARM Stage-2:

```
RISC-V Privilege Modes with H Extension:

  M-mode (Machine) ─── firmware / SBI
       │
  HS-mode (Hypervisor-extended Supervisor) ─── host kernel + KVM
       │
  VS-mode (Virtual Supervisor) ─── guest kernel
       │
  VU-mode (Virtual User) ─── guest applications
```

Key CSRs:
- `hgatp` -- Guest address translation pointer (like EPTP / VTTBR_EL2)
- `hstatus` -- Hypervisor status register
- `htval` -- Trap value (faulting guest physical address)
- `htinst` -- Trapped instruction (helps emulation without fetching from guest memory)
- `hedeleg` / `hideleg` -- Exception/interrupt delegation from HS to VS mode
- `hvip` -- Hypervisor virtual interrupt pending (inject interrupts into guest)

Special instructions:
- `HLV.B/H/W/D` -- Hypervisor Load Virtual: load from guest address space while in HS-mode
- `HSV.B/H/W/D` -- Hypervisor Store Virtual: store to guest address space
- `HFENCE.VVMA` -- Flush guest TLB entries (like INVEPT/INVVPID)
- `HFENCE.GVMA` -- Flush Stage-2 translations

KVM on RISC-V source: `arch/riscv/kvm/`

### 3.5 EPT / NPT (Extended / Nested Page Tables)

EPT (Intel) and NPT (AMD) solve the same problem: eliminating shadow page tables by providing hardware-assisted two-dimensional page table walks.

```
Without EPT (shadow page tables):

  Guest VA ──[Guest PT]──> GPA ──[Shadow PT maintained by KVM]──> HPA

  Problems:
  - KVM must intercept ALL guest page table modifications
  - KVM must maintain a "shadow" page table that maps GVA->HPA
  - Every CR3 load, INVLPG, or page table write causes a VM exit
  - Extremely expensive for workloads with heavy page table activity

With EPT/NPT (hardware 2D walk):

  Guest VA ──[Guest PT]──> GPA ──[EPT/NPT, hardware-walked]──> HPA

  Benefits:
  - Guest can modify its own page tables WITHOUT VM exits
  - CR3 loads and INVLPG are handled in hardware
  - KVM only manages the EPT/NPT tables (GPA->HPA mapping)
  - Eliminates the entire shadow page table machinery

  Cost:
  - Page table walk is deeper: each level of guest PT requires
    a full EPT walk to translate the guest PT page's GPA to HPA
  - Worst case: 4-level guest PT * 4-level EPT = 24 memory accesses
    per TLB miss (vs 4 with flat page tables)
  - Mitigated by TLB caching and page walk caches
```

**EPT Page Table Structure:**

```
EPT is a standard 4-level page table (like x86-64 regular page tables):

PML4 (512 entries) -> PDPT (512) -> PD (512) -> PT (512) -> 4KB page
                                 -> 1GB huge page
                           -> 2MB huge page

EPT PTE format (64-bit):
 ┌──────────────────────────────────────────────────────────────────┐
 │ 63-52: ignored │ 51-12: HPA page frame │ 11-8: type │ 7: IGN │
 │ 6: dirty │ 5: accessed │ 4-3: EPT type │ 2: X │ 1: W │ 0: R │
 └──────────────────────────────────────────────────────────────────┘

 Bits 2:0 (R/W/X): read, write, execute permissions
 Bits 5:3: EPT memory type (UC=0, WC=1, WT=4, WP=5, WB=6)
 Bit 7: 1 = this is a large page (1GB at PDPT level, 2MB at PD level)
 Bit 8: accessed (if enabled via EPTP)
 Bit 9: dirty (if enabled via EPTP)
```

EPT violations (the EPT equivalent of page faults) cause VM exits with exit qualification bits indicating:
- Was it a read, write, or instruction fetch?
- Was it caused by an EPT paging-structure entry or a leaf entry?
- Was the GPA valid (in a memory slot) or not?

KVM handles EPT violations by:
1. Looking up the faulting GPA in memory slots
2. If found: installing an EPT mapping (GPA -> HPA from the slot's mmap'd memory)
3. If not found: forwarding as MMIO to userspace
4. If permissions mismatch: handling dirty/accessed tracking, or MMIO

### 3.6 VPID (Virtual Processor Identifier)

Without VPID, every VM entry/exit flushes the entire TLB. VPID tags TLB entries with a per-vCPU identifier so the CPU can distinguish between different VMs' translations:

```
TLB Entry with VPID:
  [VPID | GVA | GPA | HPA | permissions]

- VPID 0 = VMX root mode (host)
- VPID 1-65535 = guest vCPUs
- On VM switch, no TLB flush needed -- entries for other VPIDs are ignored
- INVVPID instruction for selective invalidation:
  - Individual address: flush one GVA for one VPID
  - Single context: flush all entries for one VPID
  - All contexts: flush all non-zero VPIDs
  - Single context retaining globals: flush all non-global entries for one VPID
```

KVM allocates a VPID per vCPU and never reuses VPIDs while a vCPU exists.

### 3.7 Posted Interrupts

Posted Interrupts allow an external interrupt to be delivered directly to a guest vCPU without causing a VM exit. This is critical for VFIO device passthrough performance.

```
Traditional Interrupt Flow (causes VM exit):

  Device ──IRQ──> Host LAPIC ──VMEXIT──> KVM ──inject──> Guest LAPIC ──> Guest ISR

Posted Interrupt Flow (no VM exit):

  Device ──IRQ──> Posted Interrupt Descriptor ──direct──> Guest LAPIC ──> Guest ISR
                        (in memory)                     (no VM exit!)

Posted Interrupt Descriptor (PID) layout:
 ┌──────────────────────────────────────────────────────────────┐
 │ Bits 255:0: Posted Interrupt Requests (PIR)                 │
 │   256-bit bitmap, one bit per interrupt vector              │
 │ Bit 256: Outstanding Notification (ON)                      │
 │   Set when a new interrupt is posted, cleared by hardware   │
 │ Bit 257: Suppress Notification (SN)                         │
 │   If set, don't send notification (vCPU is not running)     │
 │ Bits 271:258: reserved                                      │
 │ Bits 279:272: Notification Vector (NV)                      │
 │   Vector for the notification interrupt                     │
 │ Bits 287:280: reserved                                      │
 │ Bits 319:288: Notification Destination (NDST)               │
 │   APIC ID of the physical CPU running this vCPU             │
 └──────────────────────────────────────────────────────────────┘
```

Requirements: VT-x, APIC virtualization, process-posted interrupts in secondary execution controls.

---

## 4. Memory Virtualization

### 4.1 Memory Slot Architecture

KVM manages guest physical memory through "memory slots" -- regions that map a GPA range to a HVA (host virtual address) range:

```
KVM Memory Slot Model:

  Guest Physical Address Space            Host Virtual Address Space
  (what the guest sees)                   (VMM process memory)

  ┌────────────────────┐ 4GB              ┌────────────────────┐
  │    MMIO/ROM hole   │                  │                    │
  ├────────────────────┤ 3.5GB            │                    │
  │                    │                  │                    │
  │  Slot 1: High RAM  │ ───────────────> │  mmap region 2     │
  │                    │                  │                    │
  ├────────────────────┤ 3GB              ├────────────────────┤
  │    MMIO gap        │ (no slot)        │                    │
  │    (PCI BARs)      │                  │                    │
  ├────────────────────┤ 0xE0000000       │                    │
  │                    │                  │                    │
  │                    │                  │                    │
  │  Slot 0: Low RAM   │ ───────────────> │  mmap region 1     │
  │                    │                  │                    │
  │                    │                  │  (may be backed by │
  │                    │                  │   huge pages)      │
  ├────────────────────┤ 0x100000 (1MB)   ├────────────────────┤
  │  Slot 2: VGA ROM   │ ───────────────> │  ROM image         │
  ├────────────────────┤ 0xC0000          ├────────────────────┤
  │  Slot 3: BIOS ROM  │ ───────────────> │  BIOS image        │
  ├────────────────────┤ 0x0              ├────────────────────┤
  └────────────────────┘                  └────────────────────┘
```

**Memory slot management:**

```c
// Allocate guest RAM with huge page backing
void *guest_mem = mmap(NULL, guest_ram_size,
                       PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE,
                       -1, 0);

// Request huge pages (2MB) for performance
madvise(guest_mem, guest_ram_size, MADV_HUGEPAGE);

// For even better performance, use explicit hugetlbfs:
int hugefd = open("/dev/hugepages/guest-ram", O_CREAT | O_RDWR, 0600);
ftruncate(hugefd, guest_ram_size);
void *guest_mem = mmap(NULL, guest_ram_size, PROT_READ | PROT_WRITE,
                       MAP_SHARED | MAP_POPULATE, hugefd, 0);

// Register with KVM
struct kvm_userspace_memory_region region = {
    .slot = 0,
    .flags = 0,
    .guest_phys_addr = 0,
    .memory_size = guest_ram_size,
    .userspace_addr = (uint64_t)guest_mem,
};
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// To delete a slot: set memory_size to 0
region.memory_size = 0;
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// To update a slot (e.g., enable dirty logging):
region.memory_size = guest_ram_size;
region.flags = KVM_MEM_LOG_DIRTY_PAGES;
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// Read-only memory (for ROM):
struct kvm_userspace_memory_region rom_region = {
    .slot = 2,
    .flags = KVM_MEM_READONLY,
    .guest_phys_addr = 0xFFFC0000,  // 256KB below 4GB
    .memory_size = 256 * 1024,
    .userspace_addr = (uint64_t)bios_image,
};
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &rom_region);
```

### 4.2 GPA to HPA Translation Path

```
Full Translation Path (with EPT):

  Guest executes: MOV RAX, [0x1000]

  1. Guest VA (0x1000) is translated via guest's page tables:
     Guest CR3 -> PML4[idx] -> PDPT[idx] -> PD[idx] -> PT[idx] -> GPA
     But EACH of these page table pages is at a GPA, so each step
     requires an EPT walk:

  2. EPT walk for guest CR3's GPA:
     EPTP -> EPT-PML4 -> EPT-PDPT -> EPT-PD -> EPT-PT -> HPA of guest CR3

  3. Read guest PML4 entry from HPA, get GPA of PDPT page

  4. EPT walk for PDPT page's GPA:
     EPTP -> EPT-PML4 -> EPT-PDPT -> EPT-PD -> EPT-PT -> HPA of guest PDPT

  5. Read guest PDPT entry, get GPA of PD page

  6. EPT walk for PD page's GPA ... (repeat)

  7. Read guest PD entry, get GPA of PT page

  8. EPT walk for PT page's GPA ... (repeat)

  9. Read guest PT entry, get final GPA of the data page

  10. EPT walk for data page's GPA:
      EPTP -> EPT-PML4 -> EPT-PDPT -> EPT-PD -> EPT-PT -> HPA of data

  Total memory accesses on TLB miss (4-level guest + 4-level EPT):
    5 guest PT levels * 4 EPT levels + 4 EPT levels for final data = 24

  In practice, TLBs and page walk caches make this rare.
```

### 4.3 EPT Violation Handling in KVM

When the guest accesses a GPA that has no EPT mapping (or the wrong permissions), an EPT violation occurs:

```c
// In KVM kernel code (simplified from arch/x86/kvm/mmu/mmu.c):

static int kvm_mmu_page_fault(struct kvm_vcpu *vcpu, gpa_t gpa,
                              u64 error_code, void *insn, int insn_len)
{
    // 1. Check if GPA is in a memory slot
    struct kvm_memory_slot *slot = gfn_to_memslot(vcpu->kvm, gpa >> PAGE_SHIFT);

    if (!slot) {
        // No slot -> this is MMIO. Let userspace handle it.
        // KVM caches MMIO information in special "MMIO SPTEs" to avoid
        // repeated exits for the same MMIO GPA.
        return handle_mmio_page_fault(vcpu, gpa);
    }

    // 2. It's a real memory access. Map the page.
    // Get the HPA by looking up the HVA in the VMM process's page tables.
    kvm_pfn_t pfn = gfn_to_pfn(vcpu->kvm, gpa >> PAGE_SHIFT);

    // 3. Install the EPT mapping
    //    KVM tries to use huge pages (2MB, 1GB) when possible.
    //    It checks alignment and whether the entire huge page range
    //    is within the same memory slot with uniform attributes.
    int level = mapping_level(vcpu, gpa >> PAGE_SHIFT);  // 1=4KB, 2=2MB, 3=1GB

    // 4. Write the EPT PTE
    mmu_set_spte(vcpu, gpa, pfn, level, ...);

    // 5. Resume guest execution (no exit to userspace needed)
    return RET_PF_CONTINUE;
}
```

### 4.4 Dirty Page Tracking

KVM provides two mechanisms for tracking which guest pages have been modified:

**Bitmap-based (classic):**

```c
// Enable dirty logging on a memory slot
region.flags = KVM_MEM_LOG_DIRTY_PAGES;
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// KVM now write-protects all EPT entries for this slot.
// When the guest writes, an EPT violation occurs:
//   1. KVM marks the page as dirty in a bitmap
//   2. KVM removes write-protection from the EPT entry
//   3. Guest resumes without userspace exit

// Periodically collect dirty pages:
struct kvm_dirty_log log = { .slot = 0 };
log.dirty_bitmap = calloc(1, bitmap_size);  // 1 bit per 4KB page
ioctl(vm_fd, KVM_GET_DIRTY_LOG, &log);
// NOTE: KVM_GET_DIRTY_LOG clears the bitmap AND re-protects all EPT entries

// KVM_CLEAR_DIRTY_LOG (newer, more efficient):
// Allows clearing dirty bits for a subrange without re-protecting everything
struct kvm_clear_dirty_log clear = {
    .slot = 0,
    .first_page = 0,
    .num_pages = 1024,
    .dirty_bitmap = bitmap,
};
ioctl(vm_fd, KVM_CLEAR_DIRTY_LOG, &clear);
```

**Ring-based (KVM_CAP_DIRTY_LOG_RING, Linux 5.18+):**

```c
// More efficient: KVM pushes dirty page notifications into a ring buffer
// shared with userspace, avoiding the bitmap scan overhead.
struct kvm_enable_cap cap = {
    .cap = KVM_CAP_DIRTY_LOG_RING,
    .args[0] = ring_size,  // must be power of 2, >= 1024
};
ioctl(vm_fd, KVM_ENABLE_CAP, &cap);

// Ring entries:
struct kvm_dirty_gfn {
    __u32 flags;      // KVM_DIRTY_GFN_F_DIRTY or KVM_DIRTY_GFN_F_RESET
    __u32 slot;       // memory slot
    __u64 offset;     // page offset within slot
};

// mmap the ring per vCPU (after the kvm_run region):
struct kvm_dirty_gfn *ring = (struct kvm_dirty_gfn *)
    ((char *)run + vcpu_mmap_size);
```

### 4.5 Memory Ballooning

Virtio-balloon allows the host to reclaim memory from guests:

```
Balloon Inflation (host reclaims memory):

  1. Host sends "inflate" request via virtio-balloon device
  2. Guest driver allocates pages and reports their GPAs to host
  3. Host calls madvise(MADV_DONTNEED) on corresponding HVAs
  4. Host kernel reclaims the physical pages

Balloon Deflation (guest gets memory back):

  1. Host sends "deflate" request
  2. Guest driver frees the balloon pages
  3. On next access, page faults bring in new physical pages

  ┌─────────────────────────────────────────────┐
  │ Guest VM (sees 4GB RAM)                     │
  │                                             │
  │  ┌─────────────────────────────────────┐   │
  │  │ Usable RAM (3GB)                    │   │
  │  ├─────────────────────────────────────┤   │
  │  │ Balloon (1GB) ─── reported to host  │   │
  │  │ (allocated by guest, unused)        │   │
  │  └─────────────────────────────────────┘   │
  └─────────────────────────────────────────────┘

  Host physical memory:
  The 1GB balloon pages have been MADV_DONTNEED'd and reclaimed.
  Guest still thinks it has 4GB but 1GB is "held" by the balloon.
```

### 4.6 KSM (Kernel Same-page Merging)

KSM scans memory regions marked with `MADV_MERGEABLE` and CoW-merges identical pages:

```c
// Mark guest memory as mergeable
madvise(guest_mem, guest_ram_size, MADV_MERGEABLE);

// KSM kernel thread (ksmd) periodically:
// 1. Computes content hashes of marked pages
// 2. Finds identical pages (even across different VMs)
// 3. CoW-merges them (one physical page, multiple mappings)
// 4. If a guest later writes, a CoW fault gives it a private copy

// Tuning (via /sys/kernel/mm/ksm/):
//   pages_to_scan  -- pages scanned per sleep interval (default 100)
//   sleep_millisecs -- interval between scans (default 20)
//   merge_across_nodes -- merge across NUMA nodes (default 1)
```

Savings: 100 VMs running the same OS image can share ~30-50% of memory. The tradeoff is CPU cost of `ksmd` and CoW fault latency spikes.

### 4.7 userfaultfd for Post-Copy Migration

```c
// Register guest memory with userfaultfd
int uffd = syscall(__NR_userfaultfd, O_CLOEXEC | O_NONBLOCK);

struct uffdio_api api = { .api = UFFD_API };
ioctl(uffd, UFFDIO_API, &api);

struct uffdio_register reg = {
    .range = { .start = (uint64_t)guest_mem, .len = guest_ram_size },
    .mode = UFFDIO_REGISTER_MODE_MISSING,
};
ioctl(uffd, UFFDIO_REGISTER, &reg);

// When guest accesses an unmigrated page:
// 1. Page fault -> userfaultfd event -> VMM reads event from uffd
// 2. VMM requests the page from the source host over the network
// 3. VMM provides the page via UFFDIO_COPY
// 4. Guest resumes

struct uffdio_copy copy = {
    .dst = (uint64_t)faulting_addr,
    .src = (uint64_t)page_from_network,
    .len = 4096,
    .mode = 0,
};
ioctl(uffd, UFFDIO_COPY, &copy);
```

---

## 5. Interrupt Virtualization

### 5.1 Interrupt Controller Modes

KVM supports three IRQCHIP modes:

```
Mode 1: Full In-Kernel IRQCHIP (KVM_CREATE_IRQCHIP)
─────────────────────────────────────────────────────

  ┌──────────────────────────────┐
  │ KVM (kernel)                 │
  │ ┌──────┐ ┌──────┐ ┌──────┐  │
  │ │ PIC  │ │IOAPIC│ │ LAPIC│  │
  │ │(8259)│ │      │ │(per  │  │
  │ │      │ │      │ │ vCPU)│  │
  │ └──────┘ └──────┘ └──────┘  │
  └──────────────────────────────┘

  Pros: Fastest. All interrupt routing in kernel. No exits for EOI/ICR.
  Cons: Less flexible. Limited to what KVM implements.
  Used by: QEMU (default), early Firecracker.

Mode 2: Split IRQCHIP (KVM_CAP_SPLIT_IRQCHIP)
──────────────────────────────────────────────

  ┌──────────────────────────────┐
  │ Userspace (VMM)              │
  │ ┌──────┐ ┌──────┐           │
  │ │ PIC  │ │IOAPIC│           │
  │ │(8259)│ │      │           │
  │ └──────┘ └──────┘           │
  └──────────────────────────────┘
  ┌──────────────────────────────┐
  │ KVM (kernel)                 │
  │ ┌──────┐                    │
  │ │ LAPIC│ (in-kernel, fast)  │
  │ └──────┘                    │
  └──────────────────────────────┘

  Pros: LAPIC stays fast (no exits for timer/IPI). IOAPIC in userspace
        allows flexible routing and MSI emulation.
  Cons: Slightly more complex userspace code.
  Used by: Firecracker, Cloud Hypervisor, crosvm.

Mode 3: Userspace IRQCHIP (no KVM_CREATE_IRQCHIP)
─────────────────────────────────────────────────

  ┌──────────────────────────────┐
  │ Userspace (VMM)              │
  │ ┌──────┐ ┌──────┐ ┌──────┐  │
  │ │ PIC  │ │IOAPIC│ │ LAPIC│  │
  │ └──────┘ └──────┘ └──────┘  │
  └──────────────────────────────┘

  Pros: Maximum flexibility.
  Cons: Slowest. Every LAPIC access (timer tick, IPI, EOI) causes a VM exit.
  Used by: Nobody in production. Only for testing.
```

### 5.2 Interrupt Injection Flow

```
How an interrupt reaches the guest:

1. Device generates interrupt
   │
   ▼
2. Routed to KVM via IRQFD (eventfd) or ioctl(KVM_IRQ_LINE)
   │
   ▼
3. KVM GSI routing table ──> determines destination (IOAPIC pin, MSI, etc.)
   │
   ▼
4. IOAPIC (in-kernel or userspace) determines:
   - Which LAPIC(s) to deliver to (destination field)
   - What vector to use
   - Edge vs level triggered
   │
   ▼
5. LAPIC receives interrupt request
   - Checks priority against current TPR (Task Priority Register)
   - If higher priority: sets bit in IRR (Interrupt Request Register)
   - When guest IF=1 and no higher-priority interrupt pending:
     sets bit in ISR (In-Service Register), clears IRR bit
   │
   ▼
6. On next VM entry, KVM injects the interrupt:
   - Intel: writes interrupt info into VMCS VM-entry interruption-information field
   - AMD: writes event injection field in VMCB
   - ARM: sets appropriate HCR_EL2 bits (VI, VF) or uses GICv3/GICv4
   │
   ▼
7. Guest executes ISR (Interrupt Service Routine)
   │
   ▼
8. Guest writes EOI to LAPIC
   - If level-triggered: KVM notifies IOAPIC to re-evaluate
   - If using IRQFD with resamplefd: KVM signals the resample eventfd
```

### 5.3 IRQFD Mechanism

IRQFD connects an eventfd to KVM's interrupt injection path, enabling zero-copy interrupt delivery:

```
IRQFD Flow:

  ┌──────────────┐     eventfd_signal()     ┌──────────────────┐
  │ Device thread│ ──────────────────────── │ KVM (irqfd       │
  │ (in VMM)     │                          │  worker thread)  │
  │              │     write(efd, 1)        │                  │
  └──────────────┘ ◄──────────────────────  │ Inject IRQ into  │
   resamplefd          (level-triggered     │ guest LAPIC      │
   (EOI notification)   re-trigger)         └──────────────────┘

  Benefits:
  - No ioctl needed per interrupt injection
  - Works with epoll (VMM can multiplex I/O events and interrupt injection)
  - Compatible with VFIO (device interrupts -> eventfd -> KVM -> guest)
  - For level-triggered: resamplefd notifies VMM when guest EOIs

  Setup:
    int efd = eventfd(0, 0);
    struct kvm_irqfd irqfd = { .fd = efd, .gsi = 5 };
    ioctl(vm_fd, KVM_IRQFD, &irqfd);

  Inject interrupt:
    uint64_t val = 1;
    write(efd, &val, sizeof(val));
    // KVM immediately injects GSI 5 into the guest
```

### 5.4 MSI/MSI-X Injection

MSI (Message Signaled Interrupts) bypass the IOAPIC entirely. The device writes a specific value to a specific address, and the chipset routes it directly to a LAPIC:

```c
// MSI routing entry in KVM_SET_GSI_ROUTING:
struct kvm_irq_routing_entry msi_entry = {
    .gsi = 24,  // assigned GSI
    .type = KVM_IRQ_ROUTING_MSI,
    .u.msi = {
        // MSI address format (x86):
        //   Bits 31:20 = 0xFEE (fixed prefix)
        //   Bits 19:12 = destination APIC ID
        //   Bit 3 = RH (redirect hint)
        //   Bit 2 = DM (destination mode: 0=physical, 1=logical)
        .address_lo = 0xFEE00000 | (dest_apic_id << 12),
        .address_hi = 0,
        // MSI data format:
        //   Bits 7:0 = vector
        //   Bit 14 = level (0=deassert, 1=assert)
        //   Bit 15 = trigger mode (0=edge, 1=level)
        .data = vector | (1 << 14),  // edge-triggered, assert
    },
};
```

---

## 6. I/O Virtualization

### 6.1 I/O Trapping: PIO and MMIO

```
I/O Trap Path:

  Guest executes:
    OUT 0x3F8, AL   (PIO -- serial port write)
    or
    MOV [0xFEE00000], EAX  (MMIO -- LAPIC write)
         │
         ▼
  Hardware traps (VM exit):
    Intel: Exit reason = "I/O instruction" or "EPT violation"
    AMD: Exit code = IOIO or NPF
         │
         ▼
  KVM checks:
    1. Is there an IOEVENTFD for this address? → signal eventfd, resume (no exit)
    2. Is this an in-kernel device (LAPIC, IOAPIC, PIT)? → handle in kernel
    3. Otherwise → exit to userspace via kvm_run
         │
         ▼
  VMM handles exit:
    kvm_run.exit_reason = KVM_EXIT_IO or KVM_EXIT_MMIO
    VMM emulates the device, fills in response data
    VMM calls KVM_RUN to resume guest
```

### 6.2 IOEVENTFD

IOEVENTFD is the "doorbell" mechanism -- KVM signals an eventfd when the guest writes to a specific address, WITHOUT causing a VM exit to userspace:

```
Without IOEVENTFD:                   With IOEVENTFD:

Guest write to 0x500                  Guest write to 0x500
     │                                     │
     ▼                                     ▼
  VM exit (expensive)                   KVM signals eventfd
     │                                  (NO vm exit)
     ▼                                     │
  VMM handles in userspace                 ▼
  VMM calls KVM_RUN                     VMM device thread
  (context switches, TLB               wakes up via epoll
   pressure)                           (asynchronous)
```

This is used as the virtio notification mechanism. The guest writes to the doorbell address to notify the VMM that new virtio descriptors are available. The VMM device thread, blocked on epoll, wakes up and processes the virtqueue.

```c
// Set up IOEVENTFD for a virtio MMIO doorbell at 0xd0000050
int doorbell_fd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
struct kvm_ioeventfd ev = {
    .addr = 0xd0000050,            // QueueNotify register for virtio-mmio
    .len = 4,                      // 4-byte write
    .fd = doorbell_fd,
    .flags = 0,                    // MMIO (not PIO)
    // .flags = KVM_IOEVENTFD_FLAG_PIO  // for PIO
    // .flags |= KVM_IOEVENTFD_FLAG_DATAMATCH  // only trigger on specific value
    // .datamatch = 0x1234          // the value to match
};
ioctl(vm_fd, KVM_IOEVENTFD, &ev);

// VMM device thread:
struct epoll_event events[10];
int epfd = epoll_create1(0);
struct epoll_event ev_cfg = { .events = EPOLLIN, .data.fd = doorbell_fd };
epoll_ctl(epfd, EPOLL_CTL_ADD, doorbell_fd, &ev_cfg);

while (running) {
    int n = epoll_wait(epfd, events, 10, -1);
    for (int i = 0; i < n; i++) {
        uint64_t val;
        read(events[i].data.fd, &val, sizeof(val));
        process_virtqueue();
    }
}
```

### 6.3 Virtio Architecture

Virtio is the standard paravirtual I/O framework. The guest knows it is virtualized and cooperates with the VMM through shared memory virtqueues:

```
Virtio Architecture:

  ┌─────────────────────────────────────────────────────────────┐
  │ Guest                                                       │
  │  ┌──────────────┐                                          │
  │  │ virtio driver│ (e.g., virtio-net, virtio-blk)           │
  │  │   (guest OS) │                                          │
  │  └──────┬───────┘                                          │
  │         │ writes descriptors to vring                       │
  │         ▼                                                   │
  │  ┌──────────────────────────────────────────────────────┐   │
  │  │                  Virtqueue (vring)                    │   │
  │  │  ┌─────────────┐  ┌─────────────┐  ┌────────────┐   │   │
  │  │  │ Descriptor  │  │ Available   │  │ Used Ring  │   │   │
  │  │  │ Table       │  │ Ring        │  │            │   │   │
  │  │  │             │  │             │  │            │   │   │
  │  │  │ [addr,len,  │  │ [idx,       │  │ [idx,      │   │   │
  │  │  │  flags,next]│  │  ring[]]    │  │  ring[]]   │   │   │
  │  │  └─────────────┘  └─────────────┘  └────────────┘   │   │
  │  └──────────────────────────────────────────────────────┘   │
  │         │ notify (MMIO write or PIO)                        │
  └─────────┼───────────────────────────────────────────────────┘
            │ IOEVENTFD (no VM exit)
            ▼
  ┌─────────────────────────────────────────────────────────────┐
  │ VMM (userspace)                                             │
  │  ┌──────────────┐                                          │
  │  │ virtio device│ (e.g., TAP backend, file backend)        │
  │  │  (backend)   │                                          │
  │  └──────┬───────┘                                          │
  │         │ processes descriptors, puts them on Used Ring     │
  │         │ signals guest via IRQFD                          │
  └─────────┘                                                   │
  └─────────────────────────────────────────────────────────────┘
```

**Vring structure (from linux/virtio_ring.h):**

```c
// Descriptor: describes a buffer
struct vring_desc {
    __le64 addr;   // Guest physical address of buffer
    __le32 len;    // Length of buffer
    __le16 flags;  // VRING_DESC_F_NEXT (chained), _WRITE (device writes), _INDIRECT
    __le16 next;   // Index of next descriptor in chain
};

// Available ring: guest puts descriptor indices here for the device to consume
struct vring_avail {
    __le16 flags;  // VRING_AVAIL_F_NO_INTERRUPT (suppress notifications)
    __le16 idx;    // Where guest will put next entry (monotonically increasing)
    __le16 ring[]; // Array of descriptor indices
};

// Used ring: device puts completed descriptor indices here
struct vring_used {
    __le16 flags;  // VRING_USED_F_NO_NOTIFY (suppress notifications)
    __le16 idx;    // Where device will put next entry
    struct vring_used_elem ring[];
};

struct vring_used_elem {
    __le32 id;   // Index of head of descriptor chain
    __le32 len;  // Number of bytes written by device
};
```

**Virtio transport types:**

| Transport | How it works | Used by |
|-----------|-------------|---------|
| **virtio-pci** | Device appears as PCI device. Bars for config/notify/ISR. | QEMU, most VMMs |
| **virtio-mmio** | Device at a fixed MMIO address. Simpler, no PCI bus. | Firecracker, embedded |

### 6.4 vhost: In-Kernel Virtio Data Plane

vhost moves the virtio data plane into the kernel, eliminating userspace context switches for every packet/block I/O:

```
Without vhost:                      With vhost-net:

  Guest                               Guest
    │ virtqueue                          │ virtqueue
    ▼                                    ▼
  VM exit (IOEVENTFD)                 VM exit (IOEVENTFD)
    │                                    │
    ▼                                    ▼
  VMM (userspace)                     KVM (kernel)
    │ read from virtqueue                │
    │ process packet                     ▼
    │ write to TAP fd                 vhost-net kernel thread
    │ system call overhead              │ directly accesses vring
    ▼                                    │ (guest memory is mapped)
  Kernel (TAP driver)                   │ copies to/from TAP
    │                                    │ NO userspace involvement
    ▼                                    ▼
  Network stack                       Network stack

  Overhead: 2 context switches        Overhead: 0 context switches
  per packet (user->kernel->user)     on data path
```

**vhost variants:**

| Variant | Location | Use Case |
|---------|----------|----------|
| `vhost-net` | In kernel (`/dev/vhost-net`) | Network I/O via TAP |
| `vhost-scsi` | In kernel (`/dev/vhost-scsi`) | SCSI target for block I/O |
| `vhost-vsock` | In kernel (`/dev/vhost-vsock`) | Host-guest socket communication |
| `vhost-user` | Userspace process (socket-based) | DPDK, SPDK, custom backends |

**vhost-user** uses a Unix domain socket protocol between the VMM and a separate backend process. This allows high-performance backends like DPDK (for networking) or SPDK (for storage) to serve virtio devices without being part of the VMM process.

### 6.5 VFIO Device Passthrough

VFIO gives a guest direct access to a physical device with IOMMU protection. See the companion document [VFIO Internals](@/notes/os/vfio_internals.md) for the full VFIO API. Key integration points with KVM:

```
VFIO + KVM Device Passthrough:

  ┌───────────────┐
  │ Guest VM      │
  │  ┌──────────┐ │       Direct access (no VMM involvement)
  │  │ Device   │ │ ◄─────────────────────────────────────────┐
  │  │ Driver   │ │                                           │
  │  └──────────┘ │                                           │
  └───────────────┘                                           │
                                                              │
  ┌────────────────────────────────────────────────────────┐  │
  │ VMM (QEMU/Firecracker)                                 │  │
  │                                                        │  │
  │  1. Open VFIO group (/dev/vfio/N)                      │  │
  │  2. Get device fd (ioctl VFIO_GROUP_GET_DEVICE_FD)     │  │
  │  3. Map device BARs into guest address space           │  │
  │     (KVM_SET_USER_MEMORY_REGION for device MMIO)       │  │
  │  4. Map DMA: VFIO_IOMMU_MAP_DMA                       │──┘
  │     (GPA range -> HVA, so device DMA reaches guest RAM)│
  │  5. Configure interrupts: VFIO_DEVICE_SET_IRQS         │
  │     (device MSI-X -> eventfd -> KVM IRQFD -> guest)    │
  │  6. Set up posted interrupts (if supported)            │
  └────────────────────────────────────────────────────────┘
```

**Performance**: Device passthrough achieves near-native performance:
- Network: Line-rate with DPDK, <2us latency
- NVMe: Full IOPS (millions per second) with no hypervisor overhead
- GPU: Full GPU performance for ML/HPC workloads

---

## 7. vCPU Scheduling & Performance

### 7.1 vCPUs as Linux Threads

Each KVM vCPU is a regular `CLONE_VM` Linux thread in the VMM process. This means:

- CFS (Completely Fair Scheduler) schedules vCPUs alongside all other host threads
- vCPUs compete for CPU time with host processes, other VMs, kernel threads
- Standard Linux scheduling tools work: `taskset`, `cpuset`, `chrt`, `nice`, `cgroups`
- vCPU threads show up in `/proc/<pid>/task/<tid>/`

```
Process view of a VM with 4 vCPUs:

  QEMU PID 12345
  ├── Main thread (event loop, management)
  ├── vCPU 0 (tid 12346) ── KVM_RUN loop
  ├── vCPU 1 (tid 12347) ── KVM_RUN loop
  ├── vCPU 2 (tid 12348) ── KVM_RUN loop
  ├── vCPU 3 (tid 12349) ── KVM_RUN loop
  ├── I/O thread (block I/O)
  └── VNC/SPICE thread (display)
```

### 7.2 vCPU Pinning

Pinning vCPUs to physical CPUs eliminates scheduling jitter and improves cache locality:

```c
// Pin vCPU thread to physical CPU
cpu_set_t cpuset;
CPU_ZERO(&cpuset);
CPU_SET(physical_cpu, &cpuset);
pthread_setaffinity_np(vcpu_thread, sizeof(cpuset), &cpuset);

// QEMU command-line equivalent:
// -vcpu 4 -object iothread,id=iot0
// With libvirt:
// <vcpupin vcpu="0" cpuset="2"/>
// <vcpupin vcpu="1" cpuset="3"/>
// <emulatorpin cpuset="0-1"/>
```

**NUMA considerations:**
- Pin vCPUs to CPUs in the same NUMA node as the guest's backing memory
- Use `numactl` or `set_mempolicy()` to bind guest memory to the correct NUMA node
- Cross-NUMA memory access adds 50-100ns latency per access

### 7.3 Halt-Polling

When a vCPU executes HLT (idle), KVM has three options:

```
vCPU HLT handling:

  Guest executes HLT
       │
       ▼
  ┌────────────────────┐
  │ Halt-poll phase    │   KVM busy-waits for halt_poll_ns
  │ (kernel busy-wait) │   (default: 200,000 ns = 200us)
  │                    │
  │ Check: interrupt   │──── Yes ──► Resume guest immediately
  │        pending?    │             (near-zero latency)
  │                    │
  │ Timer expired?     │──── Yes ──► Fall through to sleep
  └────────────────────┘
       │
       ▼
  ┌────────────────────┐
  │ Scheduled out      │   Linux scheduler sleeps the vCPU thread
  │ (kernel sleep)     │   (context switch, cache cold on wake)
  │                    │
  │ Wake on:           │
  │  - Interrupt       │
  │  - Timer           │
  │  - KVM_RUN signal  │
  └────────────────────┘
```

Tuning: `/sys/module/kvm/parameters/halt_poll_ns`
- 0 = never poll (save CPU, higher latency)
- 200000 = default (200us busy-wait)
- Higher values = lower latency for interrupt-heavy workloads but wastes CPU

KVM also adapts the poll time dynamically: it doubles the poll window when polling succeeds (fast wakeup) and halves it when polling times out (wasted CPU).

### 7.4 Paravirtual Optimizations

**PV Spinlocks:**
When a vCPU is preempted while holding a spinlock, other vCPUs spin waiting for a lock that cannot be released until the holder is scheduled back. PV spinlocks solve this:

```
1. Guest kernel detects it's on KVM (CPUID leaf 0x40000001)
2. Guest uses KVM_HC_KICK_CPU hypercall to wake a specific vCPU
3. If a vCPU spins too long on a lock, it yields via HLT
4. When the lock holder releases, it uses KVM_HC_KICK_CPU to wake waiters

Without PV spinlocks: O(N^2) wasted cycles (N = overcommitted vCPUs)
With PV spinlocks: lock waiters sleep, holder wakes them on release
```

**PV TLB Flush:**
Remote TLB flush (e.g., `munmap` on a multi-threaded process) normally sends IPIs to all CPUs. In a VM, this causes VM exits on every target vCPU. PV TLB flush batches these:

```
1. Instead of per-vCPU IPI for TLB flush, guest sets a flag in shared memory
2. On next VM entry, KVM checks the flag and does the flush
3. Avoids N-1 VM exits for an N-vCPU TLB shootdown
```

**PV Steal Time:**
KVM reports to the guest how much CPU time was "stolen" (the vCPU was scheduled out):

```c
// KVM writes steal time info to a per-vCPU shared memory page
struct kvm_steal_time {
    __u64 steal;     // cumulative stolen time in nanoseconds
    __u32 version;   // incremented on update (seqlock-like)
    __u32 flags;     // KVM_VCPU_STATE_PREEMPTED
    __u8  preempted; // 1 if vCPU is currently preempted
    __u8  pad[3];
    __u32 pad2;
};

// Guest reads this to:
// - Account stolen time in CPU usage statistics (avoids 100% CPU lies)
// - Adjust timeout calculations
// - Decide whether to spin or yield on locks
```

### 7.5 TSC Handling

The TSC (Time Stamp Counter) is the highest-resolution time source on x86. Virtualizing it correctly is critical:

```
TSC Challenges in VMs:
1. Guest TSC must be monotonic even if vCPU migrates between physical CPUs
2. Guest TSC should not jump when vCPU is descheduled
3. Multiple VMs must have independent TSC values
4. Live migration: source and destination hosts may have different TSC frequencies

Solutions:
- TSC offsetting: VMCS/VMCB has a TSC_OFFSET field added to every RDTSC
  guest_tsc = host_tsc + tsc_offset
- TSC scaling (KVM_CAP_TSC_CONTROL): multiply host TSC by a factor
  guest_tsc = host_tsc * scale_factor + tsc_offset
  Allows live migration between hosts with different TSC frequencies
- KVM_SET_TSC_KHZ: set the guest's TSC frequency
- If TSC offsetting/scaling can't work: KVM traps RDTSC and emulates it
  (expensive, causes VM exit on every RDTSC)

KVM ioctl:
struct kvm_enable_cap cap = {
    .cap = KVM_CAP_TSC_CONTROL,
};
ioctl(vm_fd, KVM_ENABLE_CAP, &cap);

// Set per-vCPU TSC frequency
struct kvm_enable_cap tsc_khz = {
    .cap = KVM_CAP_TSC_CONTROL,
    .args[0] = 2400000,  // 2.4 GHz
};
// Or use KVM_SET_TSC_KHZ:
ioctl(vcpu_fd, KVM_SET_TSC_KHZ, 2400000);
```

**TSC Deadline Timer:**
The LAPIC timer in TSC-deadline mode fires when TSC reaches a programmed value. This is the most precise timer available and is used by modern guest kernels:

```c
// Check support:
ioctl(kvm_fd, KVM_CHECK_EXTENSION, KVM_CAP_TSC_DEADLINE_TIMER);

// KVM exposes this via CPUID (bit 24 of CPUID.1.ECX)
// and handles the timer internally using hrtimers
```

### 7.6 Performance Counters Virtualization (vPMU)

KVM can expose hardware performance counters to the guest:

```c
// Enable via CPUID filtering (expose PMU CPUID leaves)
// and MSR access (allow RDPMC, PERF_GLOBAL_CTRL, etc.)

// Performance impact: vPMU adds VM exits for PMU counter overflow interrupts
// Some clouds disable vPMU for security (side channels) and performance
```

---

## 8. Live Migration

### 8.1 Pre-Copy Migration

The default QEMU migration algorithm:

```
Pre-Copy Migration Timeline:

Source Host                           Destination Host
    │                                      │
    │  1. Setup phase                      │
    │     - Negotiate capabilities         │
    │     - Create destination VM shell    │
    │                                      │
    │  2. Bulk transfer (iteration 0)      │
    │     - Transfer ALL guest RAM ────────────> Receive and map
    │     - Enable dirty logging           │
    │                                      │
    │  3. Iterative phase                  │
    │     - Get dirty bitmap ──────────────────> Track progress
    │     - Transfer dirty pages ──────────────> Apply pages
    │     - Repeat until dirty rate < bandwidth
    │       or max iterations reached      │
    │                                      │
    │  4. Stop-and-copy (downtime starts)  │
    │     - Pause source VM                │
    │     - Transfer remaining dirty pages │
    │     - Transfer device state ─────────────> Apply state
    │     - Transfer vCPU state ───────────────> Apply state
    │                                      │
    │  5. Switchover                       │
    │     - Signal destination to start ───────> Resume VM
    │     - (downtime ends)                │
    │     - Source VM destroyed             │
    └──────────────────────────────────────┘

Typical metrics:
  - Total migration time: seconds to minutes (depends on RAM size and dirty rate)
  - Downtime: 10-100ms (optimized) to seconds (naive)
  - Bandwidth: limited by network (10Gbps = ~1GB/s)
```

### 8.2 Post-Copy Migration

Post-copy transfers the minimum state first (vCPU, device state) and demand-pages the rest:

```
Post-Copy Migration:

Source Host                           Destination Host
    │                                      │
    │  1. Pause source VM                  │
    │  2. Transfer vCPU + device state ────────> Apply state
    │  3. Register userfaultfd on ─────────────> Resume VM immediately
    │     guest memory                     │    (downtime: ~milliseconds)
    │                                      │
    │  4. Guest accesses unmigrated page   │
    │                                 ◄────────  userfaultfd event
    │     Send requested page ─────────────────> UFFDIO_COPY, resume
    │                                      │
    │  5. Background push remaining pages  │
    │     (proactively, while guest runs)  │
    │                                      │
    │  Tradeoff:                           │
    │  - Minimal downtime (ms)             │
    │  - But page faults during runtime    │
    │    cause latency spikes              │
    │  - Source must stay alive until all   │
    │    pages transferred                 │
    └──────────────────────────────────────┘
```

### 8.3 KVM Dirty Page Tracking APIs

```c
// Method 1: Bitmap (classic, KVM_CAP_DIRTY_LOG)
// Enable:
region.flags |= KVM_MEM_LOG_DIRTY_PAGES;
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);
// Collect:
ioctl(vm_fd, KVM_GET_DIRTY_LOG, &dirty_log);
// Each bit = one 4KB page. Bit set = page was written since last query.
// KVM re-write-protects all pages after KVM_GET_DIRTY_LOG.

// Method 2: Clear-dirty-log (KVM_CAP_MANUAL_DIRTY_LOG_PROTECT2)
// More granular: clear dirty bits for a subrange without re-protecting everything
// Enable:
struct kvm_enable_cap cap = { .cap = KVM_CAP_MANUAL_DIRTY_LOG_PROTECT2 };
cap.args[0] = KVM_DIRTY_LOG_MANUAL_PROTECT_ENABLE;
ioctl(vm_fd, KVM_ENABLE_CAP, &cap);
// Use KVM_CLEAR_DIRTY_LOG for subrange clearing

// Method 3: Dirty ring (KVM_CAP_DIRTY_LOG_RING, Linux 5.18+)
// Ring buffer per vCPU, KVM pushes dirty page events as they happen.
// Avoids scanning a large bitmap.
// Best for large VMs where bitmap scanning is expensive.
struct kvm_enable_cap cap = {
    .cap = KVM_CAP_DIRTY_LOG_RING,
    .args[0] = ring_size,  // must be power of 2
};
ioctl(vm_fd, KVM_ENABLE_CAP, &cap);

// Ring is at the end of the kvm_run mmap region:
// entries = (kvm_run_mmap + kvm_run_size) to (kvm_run_mmap + kvm_run_size + ring_size)
```

### 8.4 Device State Serialization

During migration, all device state must be captured and restored. Each device emulated by the VMM must implement save/load:

```
Device state for migration:

  vCPU state:
    - Registers (KVM_GET_REGS, KVM_GET_SREGS, KVM_GET_FPU)
    - MSRs (KVM_GET_MSRS)
    - CPUID (KVM_GET_CPUID2)
    - LAPIC (KVM_GET_LAPIC)
    - XCRS (KVM_GET_XCRS) -- extended control registers (XCR0 for AVX, etc.)
    - Debug registers (KVM_GET_DEBUGREGS)
    - vCPU events (KVM_GET_VCPU_EVENTS) -- pending exceptions/NMIs/SMIs
    - Nested state (KVM_GET_NESTED_STATE) -- if running nested VMs
    - TSC value

  In-kernel device state:
    - PIT (KVM_GET_PIT2)
    - IOAPIC (KVM_GET_IRQCHIP with KVM_IRQCHIP_IOAPIC)
    - PIC (KVM_GET_IRQCHIP with KVM_IRQCHIP_PIC_MASTER / _SLAVE)
    - Clock (KVM_GET_CLOCK)

  VMM-emulated device state:
    - Each device serializes its own state (QEMU uses VMStateDescription)
    - Serial ports, network cards, block devices, USB, etc.
    - Includes in-flight I/O, buffer contents, register values
```

### 8.5 Downtime Optimization Techniques

| Technique | How it Helps |
|-----------|-------------|
| **Auto-converge** | Throttle guest CPU to reduce dirty rate when migration isn't converging |
| **XBZRLE** (Xor-Based Zero Run-Length Encoding) | Compress dirty pages by XOR with previous version, RLE encode the diff |
| **Multifd** | Parallel migration streams across multiple TCP connections |
| **Postcopy** | Eliminate stop-and-copy phase entirely |
| **Dirty limit** | Cap the dirty page rate via KVM throttling |
| **Compression** | zlib/zstd compress pages before transfer |

---

## 9. Security & Confidential Computing

### 9.1 AMD SEV (Secure Encrypted Virtualization)

SEV encrypts guest VM memory with a per-VM AES key managed by the AMD Secure Processor (PSP/ASP), a dedicated ARM Cortex-A5 on the CPU die:

```
SEV Architecture:

  ┌────────────────────────────────────────────┐
  │ AMD CPU Die                                │
  │                                            │
  │  ┌───────────┐     ┌───────────────┐       │
  │  │ x86 Cores │     │ AMD Secure    │       │
  │  │           │     │ Processor     │       │
  │  │  ┌─────┐  │     │ (ARM Cortex   │       │
  │  │  │Guest│  │     │  A5, runs     │       │
  │  │  │ VM  │  │     │  firmware)    │       │
  │  │  └─────┘  │     │              │       │
  │  └───────────┘     │ Manages:     │       │
  │       │            │ - AES keys    │       │
  │       │            │ - Attestation │       │
  │       ▼            │ - Key derivn  │       │
  │  ┌───────────┐     └───────────────┘       │
  │  │ Memory    │                             │
  │  │ Controller│  AES-128 encryption inline  │
  │  │ (w/ AES)  │  (every cache line leaving  │
  │  │           │   to DRAM is encrypted)     │
  │  └───────────┘                             │
  └────────────────────────────────────────────┘
```

**SEV variants:**

| Variant | Protection | Protects Against |
|---------|-----------|-----------------|
| **SEV** (EPYC Naples) | Memory encryption with per-VM keys | Physical memory snooping, cold boot attacks |
| **SEV-ES** (EPYC Rome) | + Encrypted register state (VMCB is encrypted) | Hypervisor inspecting guest registers on VM exit |
| **SEV-SNP** (EPYC Milan) | + Integrity protection (Reverse Map Table), attestation | Hypervisor remapping guest memory, replay attacks, tampering |

```c
// SEV API (simplified):
// 1. Create SEV-enabled VM
int vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);

// 2. Enable SEV
struct kvm_sev_cmd cmd = {
    .id = KVM_SEV_INIT,  // or KVM_SEV_ES_INIT, KVM_SEV_SNP_INIT
    .sev_fd = open("/dev/sev", O_RDWR),
};
ioctl(vm_fd, KVM_MEMORY_ENCRYPT_OP, &cmd);

// 3. Launch start
struct kvm_sev_launch_start start = {
    .handle = 0,
    .policy = SEV_POLICY_ES | SEV_POLICY_NODBG,
};
cmd.id = KVM_SEV_LAUNCH_START;
cmd.data = (uint64_t)&start;
ioctl(vm_fd, KVM_MEMORY_ENCRYPT_OP, &cmd);

// 4. Launch update -- encrypt guest memory
struct kvm_sev_launch_update_data update = {
    .uaddr = (uint64_t)guest_mem,
    .len = guest_mem_size,
};
cmd.id = KVM_SEV_LAUNCH_UPDATE_DATA;
cmd.data = (uint64_t)&update;
ioctl(vm_fd, KVM_MEMORY_ENCRYPT_OP, &cmd);

// 5. Launch measure -- get measurement for attestation
struct kvm_sev_launch_measure measure = { ... };
cmd.id = KVM_SEV_LAUNCH_MEASURE;
ioctl(vm_fd, KVM_MEMORY_ENCRYPT_OP, &cmd);

// 6. Launch finish
cmd.id = KVM_SEV_LAUNCH_FINISH;
ioctl(vm_fd, KVM_MEMORY_ENCRYPT_OP, &cmd);
```

### 9.2 Intel TDX (Trust Domain Extensions)

TDX creates "Trust Domains" (TDs) that are protected from the VMM, other VMs, and even SMM:

```
TDX Architecture:

  ┌──────────────────────────────────────────────────┐
  │ Intel CPU                                        │
  │                                                  │
  │  TDX Module (runs in SEAM mode, signed by Intel) │
  │  ┌────────────────────────────────────────────┐  │
  │  │ Manages TD lifecycle, memory encryption,   │  │
  │  │ attestation, SEPT (Secure EPT)             │  │
  │  └────────────────────────────────────────────┘  │
  │       ▲                    ▲                     │
  │       │ SEAMCALL           │ TDCALL              │
  │       │                    │                     │
  │  ┌─────────┐         ┌─────────┐                │
  │  │ VMM/KVM │         │ TD Guest│                │
  │  │ (host)  │         │ (trust  │                │
  │  │         │ cannot  │ domain) │                │
  │  │         │ read TD │         │                │
  │  │         │ memory  │         │                │
  │  └─────────┘         └─────────┘                │
  └──────────────────────────────────────────────────┘

Key differences from SEV:
- TDX Module is Intel-signed firmware, not OS-level software
- Secure EPT (SEPT): page tables managed by TDX Module, not VMM
- VMM cannot remap TD memory (prevents Heckler/WeSee attacks)
- Remote attestation built into Intel's attestation infrastructure
```

### 9.3 ARM CCA (Confidential Compute Architecture)

ARM CCA introduces "Realms" -- confidential VMs protected by the Realm Management Monitor (RMM):

```
ARM CCA Architecture:

  EL3: Monitor (Root world)
   │
   ├── Normal world                  Realm world
   │   EL2: Hypervisor/KVM           EL2: RMM (Realm Mgmt Monitor)
   │   EL1: Host kernel              EL1: Realm guest OS
   │   EL0: Host apps                EL0: Realm apps
   │
   └── Secure world (TrustZone)
       EL1: Secure OS (OP-TEE)
       EL0: Trusted apps
```

### 9.4 Spectre/Meltdown Mitigations

KVM applies CPU vulnerability mitigations on every VM entry/exit:

| Vulnerability | Mitigation | KVM Impact |
|--------------|-----------|------------|
| **Spectre v1** (bounds check bypass) | LFENCE barriers in kernel | Minimal |
| **Spectre v2** (branch target injection) | IBRS/IBPB/retpoline | IBPB on vCPU switch (flush BTB) |
| **Meltdown** (rogue data cache load) | KPTI (kernel page table isolation) | PTI switches on every VM exit (CR3 swap) |
| **L1TF** (L1 Terminal Fault) | L1D flush on VM entry | `kvm-intel.vmentry_l1d_flush=always` (significant perf hit) |
| **MDS** (Microarch Data Sampling) | VERW instruction on VM entry | Clears CPU buffers |
| **STIBP** | Single Thread Indirect Branch Predictors | Per-thread BTB isolation |
| **MMIO Stale Data** | VERW + buffer overwrite | On VM entry when SMT enabled |
| **Retbleed** | Return-to-IBPB | IBPB on VM exit |
| **GDS** (Gather Data Sampling) | Microcode + VERW | On VM entry |

These mitigations collectively add ~5-20% overhead to VM entry/exit, depending on workload and CPU microarchitecture.

---

## 10. KVM Kernel Internals

### 10.1 Source Code Layout

```
linux/
├── virt/kvm/
│   ├── kvm_main.c          -- Core KVM: VM/vCPU lifecycle, ioctls, memory slots
│   ├── eventfd.c           -- IRQFD and IOEVENTFD implementation
│   ├── irqchip.c           -- IRQ routing logic
│   ├── coalesced_mmio.c    -- MMIO write coalescing (batch MMIO exits)
│   ├── async_pf.c          -- Async page fault handling
│   ├── vfio.c              -- KVM-VFIO integration (group/device tracking)
│   ├── binary_stats.c      -- KVM statistics export
│   └── pfncache.c          -- GPA-to-PFN caching
│
├── arch/x86/kvm/
│   ├── x86.c               -- x86-specific KVM core (registers, CPUID, MSRs)
│   ├── emulate.c            -- x86 instruction emulator (~7000 lines)
│   ├── cpuid.c              -- CPUID handling and filtering
│   ├── irq.c                -- x86 interrupt injection
│   ├── lapic.c              -- In-kernel LAPIC emulation (~3000 lines)
│   ├── i8259.c              -- In-kernel PIC (8259) emulation
│   ├── ioapic.c             -- In-kernel IOAPIC emulation
│   ├── i8254.c              -- In-kernel PIT (8254) emulation
│   ├── pmu.c                -- Virtual PMU
│   ├── hyperv.c             -- Hyper-V enlightenments
│   ├── xen.c                -- Xen enlightenments
│   ├── debugfs.c            -- Debugfs statistics
│   │
│   ├── vmx/                 -- Intel VT-x implementation
│   │   ├── vmx.c            -- VMX main: VMCS setup, VM enter/exit, event injection
│   │   ├── vmenter.S        -- Assembly: VMLAUNCH/VMRESUME entry code
│   │   ├── vmcs.h           -- VMCS field definitions
│   │   ├── capabilities.h   -- VMX capability detection
│   │   ├── nested.c         -- Nested VMX (L1/L2 virtualization)
│   │   ├── posted_intr.c    -- Posted interrupts
│   │   ├── pmu_intel.c      -- Intel PMU virtualization
│   │   └── sgx.c            -- SGX virtualization
│   │
│   ├── svm/                 -- AMD-V (SVM) implementation
│   │   ├── svm.c            -- SVM main: VMCB setup, VMRUN/VMEXIT
│   │   ├── vmenter.S        -- Assembly: VMRUN entry code
│   │   ├── nested.c         -- Nested SVM
│   │   ├── sev.c            -- SEV/SEV-ES/SEV-SNP
│   │   ├── avic.c           -- AMD Virtual Interrupt Controller
│   │   └── pmu_amd.c        -- AMD PMU virtualization
│   │
│   └── mmu/                 -- Memory management unit
│       ├── mmu.c            -- Core MMU: page fault handling, SPTEs
│       ├── tdp_mmu.c        -- Two-Dimensional Paging MMU (EPT/NPT direct)
│       ├── spte.h           -- Shadow/EPT page table entry manipulation
│       ├── page_track.c     -- Page write tracking (for dirty log)
│       └── mmio.c           -- MMIO SPTE handling
│
├── arch/arm64/kvm/
│   ├── arm.c               -- ARM KVM core
│   ├── handle_exit.c       -- ARM VM exit dispatch
│   ├── mmu.c               -- Stage-2 page tables
│   ├── sys_regs.c          -- System register emulation
│   ├── vgic/               -- Virtual GIC (interrupt controller)
│   └── hyp/                -- EL2 hypervisor code
│
└── arch/riscv/kvm/
    ├── main.c              -- RISC-V KVM core
    ├── vcpu_exit.c         -- Exit handling
    ├── mmu.c               -- G-stage page tables
    └── vcpu_sbi.c          -- SBI (Supervisor Binary Interface) emulation
```

### 10.2 The kvm_vcpu_run() Main Loop

```c
// Simplified from arch/x86/kvm/x86.c
int kvm_arch_vcpu_ioctl_run(struct kvm_vcpu *vcpu)
{
    struct kvm_run *kvm_run = vcpu->run;

    // Check for immediate exit request
    if (kvm_run->immediate_exit)
        return -EINTR;

    // Signal handling
    vcpu_load(vcpu);  // load vCPU state onto this physical CPU

    for (;;) {
        // Check for pending signals
        if (signal_pending(current)) {
            r = -EINTR;
            break;
        }

        // Check for pending requests (e.g., TLB flush, clock update)
        if (kvm_check_request(KVM_REQ_TLB_FLUSH, vcpu))
            kvm_vcpu_flush_tlb_guest(vcpu);
        if (kvm_check_request(KVM_REQ_CLOCK_UPDATE, vcpu))
            kvm_guest_time_update(vcpu);
        // ... many more request types

        // Inject pending events (interrupts, exceptions, NMIs)
        r = kvm_x86_ops.inject_pending_event(vcpu);
        if (r < 0)
            break;

        // --- THE CRITICAL SECTION ---
        // Prepare for VM entry
        preempt_disable();
        kvm_x86_ops.prepare_switch_to_guest(vcpu);

        // Actually enter guest mode (VMLAUNCH/VMRESUME or VMRUN)
        // This is the assembly code in vmenter.S
        r = kvm_x86_ops.vcpu_run(vcpu);

        // We're back from VM exit
        kvm_x86_ops.prepare_switch_to_host(vcpu);
        preempt_enable();

        // Handle the VM exit
        r = kvm_x86_ops.handle_exit(vcpu, KVM_ISA_VMX);

        if (r <= 0) {
            // Exit to userspace (r == 0) or error (r < 0)
            break;
        }
        // r > 0 means handle internally, re-enter guest
    }

    vcpu_put(vcpu);
    return r;
}
```

### 10.3 VMX Entry/Exit Assembly

```asm
; From arch/x86/kvm/vmx/vmenter.S (simplified)

; __vmx_vcpu_run(struct vcpu_vmx *vmx, unsigned long *regs, unsigned int flags)
SYM_FUNC_START(__vmx_vcpu_run)
    ; Save host callee-saved registers
    push rbp
    push r15
    push r14
    push r13
    push r12
    push rbx

    ; Load guest registers from the regs array
    mov rax, [rsi + VCPU_RAX]
    mov rbx, [rsi + VCPU_RBX]
    mov rcx, [rsi + VCPU_RCX]
    mov rdx, [rsi + VCPU_RDX]
    mov rbp, [rsi + VCPU_RBP]
    mov r8,  [rsi + VCPU_R8]
    ; ... r9-r15, rdi, rsi (rsi saved last since it holds the pointer)

    ; Enter guest mode
    test flags, VMX_RUN_VMRESUME
    jnz .Lvmresume

    vmlaunch                    ; First entry
    jmp .Lvmfail                ; vmlaunch failed (CF or ZF set)

.Lvmresume:
    vmresume                    ; Subsequent entries
    jmp .Lvmfail                ; vmresume failed

    ; --- VM EXIT LANDS HERE ---
    ; (hardware automatically restores host RSP, RIP from VMCS host-state area,
    ;  but general-purpose registers still contain guest values)

.Lvmexit:
    ; Save guest registers back to the regs array
    mov [rsi + VCPU_RAX], rax
    mov [rsi + VCPU_RBX], rbx
    ; ... all registers

    ; Restore host callee-saved registers
    pop rbx
    pop r12
    pop r13
    pop r14
    pop r15
    pop rbp

    ; Apply Spectre mitigations
    ; IBPB (Indirect Branch Prediction Barrier) if needed
    ; L1D flush if needed (MDS/L1TF)

    ret
SYM_FUNC_END(__vmx_vcpu_run)
```

### 10.4 VM Exit Handling Dispatch (Intel VMX)

```c
// From arch/x86/kvm/vmx/vmx.c
static int (*kvm_vmx_exit_handlers[])(struct kvm_vcpu *vcpu) = {
    [EXIT_REASON_EXCEPTION_NMI]         = handle_exception_nmi,
    [EXIT_REASON_EXTERNAL_INTERRUPT]    = handle_external_interrupt,
    [EXIT_REASON_TRIPLE_FAULT]          = handle_triple_fault,
    [EXIT_REASON_INIT_SIGNAL]           = handle_init,
    [EXIT_REASON_IO_INSTRUCTION]        = handle_io,
    [EXIT_REASON_CR_ACCESS]             = handle_cr,
    [EXIT_REASON_DR_ACCESS]             = handle_dr,
    [EXIT_REASON_CPUID]                 = handle_cpuid,
    [EXIT_REASON_MSR_READ]              = handle_rdmsr,
    [EXIT_REASON_MSR_WRITE]             = handle_wrmsr,
    [EXIT_REASON_INTERRUPT_WINDOW]      = handle_interrupt_window,
    [EXIT_REASON_HLT]                   = handle_halt,
    [EXIT_REASON_INVLPG]               = handle_invlpg,
    [EXIT_REASON_VMCALL]               = handle_vmcall,
    [EXIT_REASON_EPT_VIOLATION]        = handle_ept_violation,
    [EXIT_REASON_EPT_MISCONFIG]        = handle_ept_misconfig,
    [EXIT_REASON_PAUSE_INSTRUCTION]    = handle_pause,
    [EXIT_REASON_RDTSC]                = handle_rdtsc,
    [EXIT_REASON_RDTSCP]               = handle_rdtscp,
    [EXIT_REASON_PREEMPTION_TIMER]     = handle_preemption_timer,
    [EXIT_REASON_XSETBV]              = handle_xsetbv,
    [EXIT_REASON_APIC_ACCESS]          = handle_apic_access,
    [EXIT_REASON_APIC_WRITE]           = handle_apic_write,
    [EXIT_REASON_EOI_INDUCED]          = handle_apic_eoi_induced,
    // ... ~50 total handlers
};

static int __vmx_handle_exit(struct kvm_vcpu *vcpu, fastpath_t exit_fastpath)
{
    u32 exit_reason = vmx->exit_reason.full;
    u32 vectoring_info = vmx->idt_vectoring_info;

    // Fast path for common exits:
    if (exit_fastpath != EXIT_FASTPATH_NONE)
        return handle_fastpath_set_tscdeadline(vcpu, exit_fastpath);

    // The most common exit reasons (in order of frequency for typical workloads):
    // 1. EPT_VIOLATION -- guest accessed unmapped memory
    // 2. IO_INSTRUCTION -- guest did IN/OUT
    // 3. HLT -- guest is idle
    // 4. CPUID -- guest queried CPU features
    // 5. MSR_READ/WRITE -- guest accessed MSRs
    // 6. EXTERNAL_INTERRUPT -- host interrupt while in guest mode
    // 7. EPT_MISCONFIG -- EPT entry has invalid configuration
    // 8. PREEMPTION_TIMER -- VMX preemption timer fired

    return kvm_vmx_exit_handlers[exit_reason](vcpu);
}
```

### 10.5 Timer Emulation

KVM emulates several timers:

```
Timer Hierarchy in a KVM Guest:

  ┌────────────────────────────────────────────────────────────┐
  │ LAPIC Timer (per-vCPU)                                     │
  │   - Most commonly used by modern guest kernels             │
  │   - Three modes:                                           │
  │     1. One-shot: fire once after N ticks                   │
  │     2. Periodic: fire every N ticks                        │
  │     3. TSC-deadline: fire when TSC reaches value X         │
  │   - KVM uses host hrtimers to implement                   │
  │   - TSC-deadline mode is most precise (~1ns resolution)    │
  │   - KVM exposes via CPUID and handles in lapic.c           │
  ├────────────────────────────────────────────────────────────┤
  │ PIT (i8254, in-kernel)                                     │
  │   - Legacy timer, used during boot (BIOS)                 │
  │   - ~1.19318 MHz clock, channel 0 generates IRQ 0          │
  │   - KVM emulates in i8254.c, uses host hrtimers           │
  │   - Only needed for legacy boot; UEFI guests don't use it │
  ├────────────────────────────────────────────────────────────┤
  │ HPET (High Precision Event Timer)                          │
  │   - Optional, emulated in QEMU (not in-kernel KVM)        │
  │   - ~10 MHz or higher, MMIO-based (at 0xFED00000)         │
  │   - Used by some guest OSes as a fallback timer            │
  ├────────────────────────────────────────────────────────────┤
  │ RTC/CMOS (MC146818)                                        │
  │   - Emulated in QEMU (MMIO at port 0x70/0x71)             │
  │   - Provides date/time and periodic interrupts (IRQ 8)    │
  │   - 32.768 KHz crystal, 2Hz-8192Hz programmable           │
  └────────────────────────────────────────────────────────────┘
```

### 10.6 KVM Tracepoints

KVM provides extensive tracepoints for debugging and performance analysis:

```bash
# List all KVM tracepoints
ls /sys/kernel/debug/tracing/events/kvm/

# Key tracepoints:
# kvm_entry           -- VM entry (guest mode start)
# kvm_exit            -- VM exit (with reason)
# kvm_mmio            -- MMIO access
# kvm_pio             -- Port I/O access
# kvm_cr              -- Control register access
# kvm_msr             -- MSR read/write
# kvm_page_fault      -- EPT/NPT fault
# kvm_inj_virq        -- Virtual interrupt injection
# kvm_apic            -- APIC events
# kvm_halt_poll_ns    -- Halt-poll timing
# kvm_nested_vmexit   -- Nested VM exit

# Enable a tracepoint:
echo 1 > /sys/kernel/debug/tracing/events/kvm/kvm_exit/enable

# Read trace:
cat /sys/kernel/debug/tracing/trace

# Using perf:
perf stat -e 'kvm:kvm_exit' -a sleep 5     # count VM exits
perf record -e 'kvm:kvm_exit' -a sleep 5    # record VM exit events
perf script                                  # decode events

# Using trace-cmd:
trace-cmd record -e kvm -p function_graph sleep 5
trace-cmd report

# KVM statistics via debugfs:
cat /sys/kernel/debug/kvm/vm-*/vcpu-*/stats
# Or via binary stats interface (KVM_GET_STATS_FD, KVM_STATS_GET)
```

---

## 11. Building a Minimal VMM

### 11.1 Complete Real-Mode VMM in C

This is a complete, compilable program that creates a VM and runs x86 real-mode code:

```c
/* minimal_vmm.c -- A complete KVM-based VMM that runs real-mode code.
 *
 * The guest code writes "Hello, KVM!\n" to serial port 0x3F8
 * character by character, then halts.
 *
 * Compile: gcc -o minimal_vmm minimal_vmm.c
 * Run:     sudo ./minimal_vmm
 *
 * Requirements: /dev/kvm accessible, Intel VT-x or AMD-V enabled in BIOS.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/kvm.h>
#include <stdint.h>
#include <errno.h>

#define GUEST_MEM_SIZE (1 << 20)  /* 1 MB */

/* Guest code (x86 real mode assembly):
 *
 *   mov si, msg       ; point to message
 * loop:
 *   lodsb             ; load next byte into AL, increment SI
 *   or al, al         ; check for null terminator
 *   jz halt           ; if zero, done
 *   out 0x3F8, al     ; write character to COM1
 *   jmp loop
 * halt:
 *   hlt               ; halt CPU
 * msg:
 *   db "Hello, KVM!", 0x0A, 0x00
 */
const uint8_t guest_code[] = {
    0xBE, 0x10, 0x00,   /* mov si, 0x0010 (offset of msg from CS:0) */
    0xAC,               /* lodsb */
    0x08, 0xC0,         /* or al, al */
    0x74, 0x05,         /* jz halt (skip 5 bytes ahead) */
    0xE6, 0xF8,         /* out 0xF8, al -- but wait, 0x3F8 needs special handling */
    /* Actually, in real mode, OUT imm8 only handles ports 0-255.
       For port 0x3F8, we need: mov dx, 0x3F8; out dx, al */
};

/* Corrected guest code using DX for port addressing: */
const uint8_t guest_code_v2[] = {
    /* org 0x0000 (loaded at CS:IP = 0:0x1000, but code is position-independent) */
    0xBA, 0xF8, 0x03,         /* mov dx, 0x3F8         ; COM1 port */
    0xBE, 0x13, 0x10,         /* mov si, 0x1013        ; address of msg */
    /* loop: */
    0xAC,                     /* lodsb                  ; AL = [SI++] */
    0x08, 0xC0,               /* or al, al             ; test for null */
    0x74, 0x04,               /* jz halt               ; if zero, stop */
    0xEE,                     /* out dx, al            ; write to COM1 */
    0xEB, 0xF8,               /* jmp loop              ; next character */
    /* halt: */
    0xF4,                     /* hlt                   ; halt CPU */
    /* msg: (at offset 0x13 from start, so at address 0x1013) */
    'H','e','l','l','o',',',' ','K','V','M','!','\n', 0x00,
};

int main(void)
{
    int kvm_fd, vm_fd, vcpu_fd;
    int vcpu_mmap_size;
    struct kvm_run *run;
    void *guest_mem;
    struct kvm_sregs sregs;
    struct kvm_regs regs;

    /* 1. Open /dev/kvm */
    kvm_fd = open("/dev/kvm", O_RDWR | O_CLOEXEC);
    if (kvm_fd < 0) { perror("open /dev/kvm"); return 1; }

    /* 2. Check API version */
    int api = ioctl(kvm_fd, KVM_GET_API_VERSION, 0);
    if (api != 12) { fprintf(stderr, "KVM API version %d != 12\n", api); return 1; }

    /* 3. Create VM */
    vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);
    if (vm_fd < 0) { perror("KVM_CREATE_VM"); return 1; }

    /* 4. Set TSS address (required for in-kernel APIC on Intel) */
    if (ioctl(vm_fd, KVM_SET_TSS_ADDR, 0xFFFBD000) < 0) {
        perror("KVM_SET_TSS_ADDR");
        /* Non-fatal on AMD, but required on Intel */
    }

    /* 5. Allocate guest memory */
    guest_mem = mmap(NULL, GUEST_MEM_SIZE,
                     PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE,
                     -1, 0);
    if (guest_mem == MAP_FAILED) { perror("mmap guest memory"); return 1; }

    /* 6. Load guest code at address 0x1000 */
    memcpy((uint8_t *)guest_mem + 0x1000, guest_code_v2, sizeof(guest_code_v2));

    /* 7. Register guest memory with KVM */
    struct kvm_userspace_memory_region region = {
        .slot = 0,
        .flags = 0,
        .guest_phys_addr = 0,
        .memory_size = GUEST_MEM_SIZE,
        .userspace_addr = (uint64_t)guest_mem,
    };
    if (ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region) < 0) {
        perror("KVM_SET_USER_MEMORY_REGION"); return 1;
    }

    /* 8. Create vCPU */
    vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);
    if (vcpu_fd < 0) { perror("KVM_CREATE_VCPU"); return 1; }

    /* 9. mmap the kvm_run structure */
    vcpu_mmap_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
    run = mmap(NULL, vcpu_mmap_size, PROT_READ | PROT_WRITE,
               MAP_SHARED, vcpu_fd, 0);
    if (run == MAP_FAILED) { perror("mmap kvm_run"); return 1; }

    /* 10. Set up special registers (real mode) */
    if (ioctl(vcpu_fd, KVM_GET_SREGS, &sregs) < 0) {
        perror("KVM_GET_SREGS"); return 1;
    }
    /* Real mode: CS.base = 0, CS.selector = 0 */
    sregs.cs.base = 0;
    sregs.cs.selector = 0;
    if (ioctl(vcpu_fd, KVM_SET_SREGS, &sregs) < 0) {
        perror("KVM_SET_SREGS"); return 1;
    }

    /* 11. Set up general registers */
    memset(&regs, 0, sizeof(regs));
    regs.rip = 0x1000;    /* Start executing at 0x1000 */
    regs.rflags = 0x2;    /* Bit 1 must be set (reserved, always 1) */
    if (ioctl(vcpu_fd, KVM_SET_REGS, &regs) < 0) {
        perror("KVM_SET_REGS"); return 1;
    }

    /* 12. Run the vCPU */
    printf("Running guest...\n");
    for (;;) {
        int ret = ioctl(vcpu_fd, KVM_RUN, 0);
        if (ret < 0) {
            if (errno == EINTR) continue;  /* signal, retry */
            perror("KVM_RUN");
            break;
        }

        switch (run->exit_reason) {
        case KVM_EXIT_IO:
            if (run->io.direction == KVM_EXIT_IO_OUT &&
                run->io.port == 0x3F8 &&
                run->io.size == 1) {
                /* Guest wrote to COM1 -- print the character */
                uint8_t *data = (uint8_t *)run + run->io.data_offset;
                write(STDOUT_FILENO, data, 1);
            }
            break;

        case KVM_EXIT_HLT:
            printf("\nGuest halted.\n");
            goto done;

        case KVM_EXIT_SHUTDOWN:
            printf("Guest shutdown (triple fault).\n");
            goto done;

        case KVM_EXIT_FAIL_ENTRY:
            fprintf(stderr, "FAIL_ENTRY: reason=0x%llx\n",
                    (unsigned long long)run->fail_entry.hardware_entry_failure_reason);
            goto done;

        case KVM_EXIT_INTERNAL_ERROR:
            fprintf(stderr, "INTERNAL_ERROR: suberror=%d\n",
                    run->internal.suberror);
            goto done;

        default:
            fprintf(stderr, "Unexpected exit reason: %d\n", run->exit_reason);
            goto done;
        }
    }

done:
    /* 13. Cleanup */
    munmap(run, vcpu_mmap_size);
    close(vcpu_fd);
    munmap(guest_mem, GUEST_MEM_SIZE);
    close(vm_fd);
    close(kvm_fd);
    return 0;
}
```

### 11.2 Setting Up Protected Mode

To run 32-bit protected mode guest code:

```c
/* After KVM_GET_SREGS, modify sregs for protected mode: */

/* 1. Set up a GDT (Global Descriptor Table) in guest memory */
struct gdt_entry {
    uint16_t limit_low;
    uint16_t base_low;
    uint8_t  base_mid;
    uint8_t  access;
    uint8_t  flags_limit_high;
    uint8_t  base_high;
} __attribute__((packed));

/* Place GDT at physical address 0x0 */
struct gdt_entry *gdt = (struct gdt_entry *)guest_mem;

/* Entry 0: null descriptor (required) */
gdt[0] = (struct gdt_entry){0};

/* Entry 1: code segment (CS) -- base=0, limit=4GB, 32-bit, ring 0 */
gdt[1] = (struct gdt_entry){
    .limit_low = 0xFFFF,
    .base_low = 0, .base_mid = 0, .base_high = 0,
    .access = 0x9A,              /* present, ring 0, code, readable */
    .flags_limit_high = 0xCF,    /* 4KB granularity, 32-bit, limit 0xFFFFF */
};

/* Entry 2: data segment (DS/SS/ES) -- base=0, limit=4GB, ring 0 */
gdt[2] = (struct gdt_entry){
    .limit_low = 0xFFFF,
    .base_low = 0, .base_mid = 0, .base_high = 0,
    .access = 0x92,              /* present, ring 0, data, writable */
    .flags_limit_high = 0xCF,
};

/* 2. Configure SREGS for protected mode */
sregs.gdt.base = 0x0;      /* GDT base address */
sregs.gdt.limit = sizeof(struct gdt_entry) * 3 - 1;

/* Code segment: selector 0x08 (GDT entry 1) */
sregs.cs.selector = 0x08;
sregs.cs.base = 0;
sregs.cs.limit = 0xFFFFFFFF;
sregs.cs.type = 0x0B;       /* code, execute/read, accessed */
sregs.cs.present = 1;
sregs.cs.dpl = 0;
sregs.cs.db = 1;            /* 32-bit */
sregs.cs.s = 1;             /* code/data segment */
sregs.cs.l = 0;             /* not 64-bit */
sregs.cs.g = 1;             /* 4KB granularity */

/* Data segments: selector 0x10 (GDT entry 2) */
sregs.ds = sregs.es = sregs.ss = (struct kvm_segment){
    .selector = 0x10,
    .base = 0,
    .limit = 0xFFFFFFFF,
    .type = 0x03,            /* data, read/write, accessed */
    .present = 1,
    .dpl = 0,
    .db = 1,
    .s = 1,
    .g = 1,
};

/* Enable protected mode */
sregs.cr0 |= 1;             /* CR0.PE = 1 */

ioctl(vcpu_fd, KVM_SET_SREGS, &sregs);
```

### 11.3 Setting Up Long Mode (64-bit)

```c
/* 64-bit mode requires:
 * 1. CR0.PE = 1 (protected mode)
 * 2. CR0.PG = 1 (paging enabled)
 * 3. CR4.PAE = 1 (Physical Address Extension)
 * 4. EFER.LME = 1 (Long Mode Enable)
 * 5. EFER.LMA = 1 (Long Mode Active -- set by hardware when PG=1 and LME=1)
 * 6. CR3 points to a valid PML4 page table
 * 7. CS.L = 1 (64-bit code segment)
 */

/* Set up identity-mapped page tables in guest memory */
/* Place at physical address 0x2000 */
#define PML4_ADDR  0x2000
#define PDPT_ADDR  0x3000
#define PD_ADDR    0x4000

/* PML4[0] -> PDPT */
uint64_t *pml4 = (uint64_t *)((uint8_t *)guest_mem + PML4_ADDR);
pml4[0] = PDPT_ADDR | 0x03;  /* present, writable */

/* PDPT[0] -> PD */
uint64_t *pdpt = (uint64_t *)((uint8_t *)guest_mem + PDPT_ADDR);
pdpt[0] = PD_ADDR | 0x03;    /* present, writable */

/* PD: map first 1GB using 2MB pages (identity mapped) */
uint64_t *pd = (uint64_t *)((uint8_t *)guest_mem + PD_ADDR);
for (int i = 0; i < 512; i++) {
    pd[i] = (i * (2ULL << 20)) | 0x83;  /* present, writable, 2MB page */
}

/* Configure SREGS for 64-bit mode */
sregs.cr3 = PML4_ADDR;
sregs.cr4 |= (1 << 5);      /* CR4.PAE = 1 */
sregs.cr0 |= (1 << 0);      /* CR0.PE = 1 */
sregs.cr0 |= (1UL << 31);   /* CR0.PG = 1 */
sregs.efer |= (1 << 8);     /* EFER.LME = 1 */
sregs.efer |= (1 << 10);    /* EFER.LMA = 1 */

/* 64-bit code segment: L=1, D=0 */
sregs.cs.selector = 0x08;
sregs.cs.base = 0;
sregs.cs.limit = 0xFFFFFFFF;
sregs.cs.type = 0x0B;
sregs.cs.present = 1;
sregs.cs.dpl = 0;
sregs.cs.db = 0;             /* Must be 0 in long mode */
sregs.cs.s = 1;
sregs.cs.l = 1;              /* 64-bit mode */
sregs.cs.g = 1;

/* Also set EFER via KVM_SET_MSRS */
struct {
    struct kvm_msrs header;
    struct kvm_msr_entry entries[1];
} msrs = {
    .header.nmsrs = 1,
    .entries[0] = {
        .index = 0xC0000080,  /* MSR_IA32_EFER */
        .data = (1 << 8) | (1 << 10) | (1 << 0),  /* LME | LMA | SCE */
    },
};
ioctl(vcpu_fd, KVM_SET_MSRS, &msrs);
```

### 11.4 Production VMM Architecture

How real VMMs (Firecracker, crosvm, Cloud Hypervisor) are structured:

```
Production VMM Architecture (Firecracker):

  ┌──────────────────────────────────────────────────────────────────┐
  │ main()                                                          │
  │  ├── Parse command line / API config                            │
  │  ├── Open /dev/kvm                                              │
  │  ├── Create VM (KVM_CREATE_VM)                                  │
  │  ├── Configure VM:                                              │
  │  │    ├── Set TSS address                                       │
  │  │    ├── Create in-kernel IRQCHIP (split mode)                 │
  │  │    ├── Set up memory regions                                 │
  │  │    └── Create IRQFD/IOEVENTFD bindings                      │
  │  ├── Load kernel (bzImage/PE) into guest memory                 │
  │  │    ├── Parse kernel header (boot_params)                     │
  │  │    ├── Set up boot_params (zero page) at 0x7000              │
  │  │    ├── Load kernel at 0x100000 (1MB, default load address)   │
  │  │    ├── Load initrd after kernel                              │
  │  │    └── Set up kernel command line                            │
  │  ├── Create vCPUs:                                              │
  │  │    ├── KVM_CREATE_VCPU for each                              │
  │  │    ├── Set CPUID, SREGS, REGS, MSRs                         │
  │  │    ├── For BSP (vCPU 0): set RIP to kernel entry             │
  │  │    └── For APs: wait for INIT-SIPI-SIPI sequence            │
  │  ├── Create device manager:                                     │
  │  │    ├── Serial (UART 16550)                                   │
  │  │    ├── virtio-net (via MMIO transport)                       │
  │  │    ├── virtio-block                                          │
  │  │    ├── virtio-vsock                                          │
  │  │    └── virtio-balloon                                        │
  │  ├── Start vCPU threads:                                        │
  │  │    └── Each thread runs:                                     │
  │  │         loop {                                               │
  │  │             KVM_RUN                                          │
  │  │             handle_exit(run)                                 │
  │  │         }                                                    │
  │  └── Main thread: event loop (epoll)                            │
  │       ├── API socket (Unix domain socket for REST API)          │
  │       ├── IOEVENTFD events (virtio doorbells)                   │
  │       ├── IRQFD events (interrupt injection)                    │
  │       ├── Timer events (rate limiting)                          │
  │       └── Signal handling                                       │
  └──────────────────────────────────────────────────────────────────┘
```

**Linux Boot Protocol in a VMM (x86_64):**

```
Guest memory layout for Linux kernel boot:

  0x00000000 - 0x00000FFF : Real-mode IVT (not used in 64-bit direct boot)
  0x00007000 - 0x00007FFF : boot_params (zero page)
  0x00008000 - 0x0000FFFF : Kernel command line (null-terminated)
  0x00010000 - 0x0001FFFF : GDT, page tables (set up by VMM)
  0x00100000 - 0x???????? : Kernel (bzImage loaded here, 1MB+)
  0x???????? - 0x???????? : initrd (loaded after kernel)
  0x???????? - 0xBFFFFFFF : Free (guest RAM)
  0xC0000000 - 0xFEBFFFFF : PCI MMIO space (not backed by RAM)
  0xFEC00000 - 0xFEC00FFF : IOAPIC
  0xFEE00000 - 0xFEE00FFF : LAPIC
  0xFFFC0000 - 0xFFFFFFFF : BIOS ROM (if needed)
```

### 11.5 The rust-vmm Ecosystem

Production Rust VMMs use the `rust-vmm` crate ecosystem:

```
rust-vmm Crate Dependency Graph:

  ┌────────────────────────┐
  │  Your VMM              │
  │  (Firecracker, Cloud   │
  │   Hypervisor, crosvm)  │
  └────────┬───────────────┘
           │ uses
  ┌────────┴───────────────────────────────────────────────────┐
  │                                                            │
  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
  │  │ kvm-ioctls   │  │ kvm-bindings │  │ vmm-sys-util    │  │
  │  │              │  │              │  │                 │  │
  │  │ Safe Rust    │  │ Auto-gen'd   │  │ EventFd,        │  │
  │  │ wrappers     │  │ KVM structs  │  │ Terminal,       │  │
  │  │ for KVM      │  │ from kernel  │  │ TempFile,       │  │
  │  │ ioctls       │  │ headers      │  │ signal handling │  │
  │  └──────────────┘  └──────────────┘  └─────────────────┘  │
  │                                                            │
  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
  │  │ vm-memory    │  │ vm-virtio    │  │ vm-superio      │  │
  │  │              │  │              │  │                 │  │
  │  │ Guest mem    │  │ Virtqueue    │  │ Serial (16550)  │  │
  │  │ abstraction  │  │ impl,       │  │ i8042 keyboard  │  │
  │  │ (GuestMem,   │  │ descriptor  │  │ RTC (MC146818)  │  │
  │  │  MmapRegion) │  │ chain iter  │  │                 │  │
  │  └──────────────┘  └──────────────┘  └─────────────────┘  │
  │                                                            │
  │  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐  │
  │  │ vhost        │  │ linux-loader │  │ event-manager   │  │
  │  │              │  │              │  │                 │  │
  │  │ vhost-user   │  │ Load bzImage │  │ epoll-based     │  │
  │  │ protocol,    │  │ /PE/ELF into │  │ event loop      │  │
  │  │ vhost-kern   │  │ guest memory │  │ (MutEventSubsc  │  │
  │  │              │  │              │  │  riber trait)   │  │
  │  └──────────────┘  └──────────────┘  └─────────────────┘  │
  └────────────────────────────────────────────────────────────┘
```

**Example: Creating a VM with kvm-ioctls (Rust):**

```rust
use kvm_ioctls::{Kvm, VcpuExit};
use kvm_bindings::{kvm_userspace_memory_region, kvm_regs, KVM_MEM_LOG_DIRTY_PAGES};

fn main() {
    // 1. Open /dev/kvm
    let kvm = Kvm::new().expect("Failed to open /dev/kvm");

    // 2. Create VM
    let vm = kvm.create_vm().expect("Failed to create VM");

    // 3. Set TSS address (x86)
    vm.set_tss_address(0xFFFB_D000).expect("Failed to set TSS");

    // 4. Allocate and register guest memory
    let guest_mem = unsafe {
        libc::mmap(
            std::ptr::null_mut(),
            1 << 20,  // 1MB
            libc::PROT_READ | libc::PROT_WRITE,
            libc::MAP_PRIVATE | libc::MAP_ANONYMOUS,
            -1,
            0,
        )
    };

    let mem_region = kvm_userspace_memory_region {
        slot: 0,
        guest_phys_addr: 0,
        memory_size: 1 << 20,
        userspace_addr: guest_mem as u64,
        flags: 0,
    };
    unsafe { vm.set_user_memory_region(mem_region).unwrap() };

    // 5. Load guest code
    let code: &[u8] = &[0xBA, 0xF8, 0x03, /* mov dx, 0x3F8 */
                         0xB0, 0x41,       /* mov al, 'A' */
                         0xEE,             /* out dx, al */
                         0xF4];            /* hlt */
    unsafe {
        let dest = (guest_mem as *mut u8).add(0x1000);
        std::ptr::copy_nonoverlapping(code.as_ptr(), dest, code.len());
    }

    // 6. Create vCPU
    let vcpu = vm.create_vcpu(0).expect("Failed to create vCPU");

    // 7. Set registers
    let mut sregs = vcpu.get_sregs().unwrap();
    sregs.cs.base = 0;
    sregs.cs.selector = 0;
    vcpu.set_sregs(&sregs).unwrap();

    let regs = kvm_regs {
        rip: 0x1000,
        rflags: 0x2,
        ..Default::default()
    };
    vcpu.set_regs(&regs).unwrap();

    // 8. Run
    loop {
        match vcpu.run().expect("KVM_RUN failed") {
            VcpuExit::IoOut(port, data) => {
                if port == 0x3F8 {
                    print!("{}", data[0] as char);
                }
            }
            VcpuExit::Hlt => {
                println!("\nGuest halted");
                break;
            }
            exit => {
                eprintln!("Unexpected exit: {:?}", exit);
                break;
            }
        }
    }
}
```

---

## 12. Advanced Topics

### 12.1 Nested Virtualization

Nested virtualization allows running a hypervisor inside a VM (L1 guest runs L2 guests):

```
Nested Virtualization Layers:

  L0 (bare metal host)
  ├── KVM (actual hardware VMX/SVM)
  │
  └── L1 (guest VM running a hypervisor)
      ├── KVM / Hyper-V / VMware
      │
      └── L2 (guest-of-guest VM)
          └── Application code
```

**How KVM handles nested VMX (Intel):**

```
L1 executes VMLAUNCH to start L2:
  1. This causes a VM exit to L0 (VMLAUNCH traps)
  2. L0 KVM reads L1's VMCS12 (the VMCS that L1 prepared for L2)
  3. L0 merges VMCS12 with its own VMCS01 to create VMCS02:
     - VMCS02.guest_state = VMCS12.guest_state (L2's registers)
     - VMCS02.host_state = VMCS01.guest_state (L1's registers)
     - VMCS02.controls = merge of VMCS01 and VMCS12 controls
     - VMCS02.EPTP = composed EPT (L2 GPA -> L1 GPA -> L0 HPA)
  4. L0 does VMLAUNCH with VMCS02 (L2 runs on real hardware)

L2 triggers a VM exit:
  1. Hardware exits to L0 (the real host)
  2. L0 KVM checks: should this exit go to L1?
     - If L1 asked to intercept this (in VMCS12): reflect to L1
     - If not: L0 handles it internally
  3. To reflect to L1: load L1's register state, set L1's VMCS12
     exit reason, resume L1 (which thinks it's handling an L2 exit)
```

**VMCS shadowing** (hardware optimization, Haswell+):
- L0 maps a "shadow VMCS" that L1 can VMREAD/VMWRITE without VM exits
- Only VMLAUNCH/VMRESUME and certain field changes still exit to L0
- Dramatically reduces nested virtualization overhead

**Performance**: Nested virtualization typically adds 10-50% overhead depending on workload and hardware support.

### 12.2 vGPU Technologies

| Technology | Approach | Performance | Use Case |
|-----------|---------|-------------|----------|
| **NVIDIA vGPU** | Time-sliced GPU sharing (mediated passthrough) | ~90% of bare metal | Enterprise VDI, ML inference |
| **Intel GVT-g** | Mediated passthrough (vfio-mdev) | ~80% of bare metal | Client/embedded virtualization |
| **virtio-gpu** | Paravirtual, VMM renders using host GPU | Depends on impl | General display, 3D acceleration |
| **SR-IOV GPU** | Hardware VF partitioning | Near-native | Data center GPU sharing |

```
vGPU via vfio-mdev:

  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
  │ VM 1         │  │ VM 2         │  │ VM 3         │
  │ vGPU driver  │  │ vGPU driver  │  │ vGPU driver  │
  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
         │                 │                 │
  ┌──────┴─────────────────┴─────────────────┴───────┐
  │ vfio-mdev (mediates access)                      │
  │ /sys/class/mdev_bus/...                          │
  ├──────────────────────────────────────────────────┤
  │ Physical GPU Driver                              │
  │ (nvidia, i915)                                   │
  ├──────────────────────────────────────────────────┤
  │ Physical GPU (single device)                     │
  └──────────────────────────────────────────────────┘
```

### 12.3 QEMU TCG vs KVM

```
QEMU execution modes:

  TCG (Tiny Code Generator) -- Software emulation:
  - Translates guest instructions to host instructions at runtime
  - Can run guest code for ANY architecture on ANY host
  - ~10-100x slower than native
  - Used for cross-architecture emulation (ARM on x86, etc.)
  - Useful for development when KVM is unavailable

  KVM acceleration:
  - Guest code runs directly on hardware (VMX/SVM non-root mode)
  - Near-native speed (typically <5% overhead for compute)
  - Only works when guest arch == host arch (or compatible)
  - Privileged instructions trap to KVM for emulation

  QEMU can fall back from KVM to TCG for individual instructions
  that KVM cannot handle (rare with modern hardware support).
```

### 12.4 KVM Unit Tests

```bash
# The KVM unit test framework (kvm-unit-tests) tests KVM functionality
# by running small programs directly on KVM (no OS, no bootloader):
git clone https://gitlab.com/kvm-unit-tests/kvm-unit-tests.git
cd kvm-unit-tests
./configure
make
./run_tests.sh

# Tests cover:
# - APIC, IOAPIC, PIT, RTC emulation
# - EPT/NPT, VPID, posted interrupts
# - MSR handling, CPUID
# - Exception injection
# - Nested virtualization
# - TSC handling
# - SEV, TDX, CCA
# - ARM: GIC, timers, stage-2
# - RISC-V: SBI, timer, interrupts
```

### 12.5 eBPF Integration with KVM

```
eBPF can observe and even modify KVM behavior:

1. kfunc-based eBPF programs (Linux 6.x+):
   - Attach to KVM functions via fentry/fexit
   - Observe VM exits, interrupt injection, memory mapping
   - Example: count VM exits by reason per vCPU

2. BPF-based scheduler (sched_ext, Linux 6.12+):
   - Custom scheduling policies for vCPU threads
   - Pin vCPUs, implement gang scheduling, NUMA-aware placement
   - Example: co-schedule all vCPUs of a latency-sensitive VM

3. Tracing:
   - perf + eBPF programs attached to KVM tracepoints
   - Build custom KVM performance dashboards

4. KVM + eBPF in guest:
   - Guest can run eBPF programs normally
   - eBPF verifier works in guest kernel
   - No special KVM support needed
```

### 12.6 Common Pitfalls and Gotchas

**1. Forgetting KVM_SET_TSS_ADDR on Intel:**
Without this, the in-kernel IRQCHIP fails silently on Intel CPUs. AMD does not need it. Always check the return value -- it succeeds on AMD but is required on Intel.

**2. Invalid guest state (KVM_EXIT_FAIL_ENTRY):**
The most common cause is inconsistent segment registers or control register settings. The hardware_entry_failure_reason field in the kvm_run struct gives the VMCS error code. Common errors:
- Setting CR0.PG without CR0.PE (paging requires protected mode)
- Setting EFER.LMA without CR4.PAE
- CS.L=1 with CS.D=1 (long mode requires D=0)
- Mismatched segment register attributes (type, S, DPL)

**3. Memory region alignment:**
- `guest_phys_addr` must be page-aligned (4KB)
- `memory_size` must be page-aligned and non-zero (or zero to delete)
- `userspace_addr` must be page-aligned
- Overlapping slots are not allowed

**4. vCPU thread affinity:**
KVM_RUN must be called from the same thread that created the vCPU (via KVM_CREATE_VCPU). You cannot migrate a vCPU fd between threads.

**5. CPUID filtering:**
If you pass through the host's CPUID without filtering, the guest may try to use features that KVM doesn't support. Always start with KVM_GET_SUPPORTED_CPUID and filter based on what your VMM actually supports.

**6. Signal handling and KVM_RUN:**
If a signal arrives while the vCPU is in guest mode, KVM_RUN returns -1 with errno=EINTR. You must handle this (check for pending signals, then re-enter KVM_RUN). The `kvm_run.immediate_exit` field helps avoid race conditions: set it to 1 before KVM_RUN, and KVM will immediately return with -EINTR without entering guest mode. This is used to handle signals that arrived between checking for pending signals and calling KVM_RUN.

**7. Dirty log performance:**
Enabling dirty logging (KVM_MEM_LOG_DIRTY_PAGES) write-protects all EPT entries. This means the first access to every page after enabling dirty logging causes an EPT violation (VM exit). For large VMs (hundreds of GB), this initial storm of EPT violations can cause a significant pause. Use KVM_CLEAR_DIRTY_LOG with subranges to avoid re-protecting all pages at once.

**8. IRQCHIP must be created before vCPUs:**
KVM_CREATE_IRQCHIP must be called before KVM_CREATE_VCPU. If you create vCPUs first, the LAPIC for each vCPU won't be properly initialized.

**9. Memory slot limits:**
KVM has a maximum number of memory slots (typically 509 on x86, configurable via KVM_CAP_NR_MEMSLOTS). Running out of slots is a real problem for VMs with many PCI device BARs.

**10. Nested virtualization performance:**
L2 VM exits are extremely expensive (L2 -> L0 -> L1 -> L0 -> L2). Minimize them by ensuring L1's VMCS intercepts are minimal and that VPID/EPT are properly configured for L2.

---

## 13. Key References

### Core Documentation

1. **KVM API Documentation** -- `Documentation/virt/kvm/api.rst` in the Linux kernel source tree. The definitive reference for all KVM ioctls.
2. **Intel SDM Volume 3, Chapters 23-34** -- VMX (Virtual Machine Extensions) specification. Intel 64 and IA-32 Architectures Software Developer's Manual.
3. **AMD APM Volume 2, Chapter 15** -- SVM (Secure Virtual Machine) specification. AMD64 Architecture Programmer's Manual.
4. **ARM Architecture Reference Manual** -- Chapter D1 (AArch64 System Level Architecture), sections on EL2 and Stage-2 translation.
5. **RISC-V Privileged Specification** -- Chapter 8, Hypervisor Extension.

### Key Papers

6. Kivity, A. et al. "kvm: the Linux Virtual Machine Monitor." *Proceedings of the Linux Symposium*, 2007. -- The original KVM paper.
7. Adams, K. and Agesen, O. "A Comparison of Software and Hardware Techniques for x86 Virtualization." *ASPLOS*, 2006. -- Explains why hardware-assisted virtualization (VMX/SVM) eventually beat binary translation.
8. Ben-Yehuda, M. et al. "The Turtles Project: Design and Implementation of Nested Virtualization." *OSDI*, 2010. -- Foundational work on nested KVM.
9. Amit, N. and Wei, M. "The Design and Implementation of Hyperupcalls." *ATC*, 2018. -- Optimization for VM exit handling.
10. Agache, A. et al. "Firecracker: Lightweight Virtualization for Serverless Applications." *NSDI*, 2020. -- Firecracker design, including KVM usage and <125ms boot times.
11. Kaplan, D. "AMD Memory Encryption." AMD Whitepaper, 2016. -- SEV architecture.
12. Intel Corporation. "Intel Trust Domain Extensions (TDX) Module Specification." 2023.
13. Uhlig, R. et al. "Intel Virtualization Technology." *IEEE Computer*, 2005. -- Original VT-x design and motivation.
14. Abramson, D. et al. "Intel Virtualization Technology for Directed I/O." *Intel Technology Journal*, 2006. -- VT-d (IOMMU) for device passthrough.
15. Dall, C. and Nieh, J. "KVM/ARM: The Design and Implementation of the Linux ARM Hypervisor." *ASPLOS*, 2014. -- KVM on ARM architecture.
16. Gordon, A. et al. "ELI: Bare-Metal Performance for I/O Virtualization." *ASPLOS*, 2012. -- Exit-less interrupts for device passthrough.
17. Waldspurger, C. "Memory Resource Management in VMware ESX Server." *OSDI*, 2002. -- Ballooning, content-based page sharing (KSM predecessor), idle memory taxation.

### Practical Resources

18. **kvmtool** -- A minimal KVM VMM in ~15K lines of C. Excellent for learning: `https://github.com/kvmtool/kvmtool`
19. **rust-vmm** -- The Rust VMM crate ecosystem: `https://github.com/rust-vmm`
20. **kvm-unit-tests** -- Unit test framework for KVM: `https://gitlab.com/kvm-unit-tests/kvm-unit-tests`
21. **QEMU source** -- `https://github.com/qemu/qemu` -- the reference VMM implementation
22. **Firecracker source** -- `https://github.com/firecracker-microvm/firecracker`
23. **Cloud Hypervisor source** -- `https://github.com/cloud-hypervisor/cloud-hypervisor`
24. **crosvm source** -- `https://chromium.googlesource.com/crosvm/crosvm`
25. **Linux kernel KVM documentation** -- `https://www.kernel.org/doc/html/latest/virt/kvm/`

### Cross-references within this repository

- [VFIO Internals](@/notes/os/vfio_internals.md) -- IOMMU and device passthrough details complementing Section 6.5
- [Critical ISA Instructions](@/notes/hardware/isa_critical_instructions.md) -- VMX/SVM/EL2/H-ext instruction details (Section 13: Virtualization)
- [Expert-Level Linux Syscalls](@/notes/os/linux_expert_syscalls.md) -- KVM-related syscalls and kernel interfaces
- [io_uring Internals](@/notes/os/io_uring_internals.md) -- Async I/O relevant to VMM I/O backends
