+++
title = "IO Uring Internals"
date = 2026-02-10
[taxonomies]
tags = ["io-uring", "async-io", "linux-kernel", "submission-queue", "zero-copy", "syscalls"]
+++


# Deep dive 2

## Table of Contents

1. [Introduction to io_uring](#1-introduction-to-io-uring)
2. [Architecture Overview](#architecture-overview)
3. [Core Data Structures](#core-data-structures)
4. [Ring Buffer Implementation](#4-ring-buffer-implementation)
5. [Submission Queue (SQ) Deep Dive](#5-submission-queue-sq-deep-dive)
6. [Completion Queue (CQ) Deep Dive](#6-completion-queue-cq-deep-dive)
7. [System Call Interface](#7-system-call-interface)
8. [Kernel-Side Implementation](#8-kernel-side-implementation)
9. [Performance Optimization Techniques](#9-performance-optimization-techniques)
10. [Advanced Features](#advanced-features)
11. [Memory Management](#11-memory-management)
12. [Practical Performance Analysis with perf](#12-practical-performance-analysis-with-perf)
13. [Benchmarking and Profiling](#13-benchmarking-and-profiling)
14. [Code Examples](#14-code-examples)
15. [Comparison with Traditional I/O](#15-comparison-with-traditional-i-o)

---

## 1. Introduction to io_uring

### 1.1 What is io_uring?

io_uring is a Linux kernel interface introduced in kernel 5.1 (2019) by Jens Axboe that provides high-performance asynchronous I/O operations.

```

┌─────────────────────────────────────────────────────────┐
│ Traditional I/O │
├─────────────────────────────────────────────────────────┤
│ User Space │ Syscall │ Kernel │ Syscall │ User│
│ (prepare) │ (enter) │ (work) │ (exit) │(get)│
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ io_uring │
├─────────────────────────────────────────────────────────┤
│ Shared Memory Ring Buffers (Zero/Minimal Syscalls) │
│ User ←→ Kernel communication via memory mapping │
└─────────────────────────────────────────────────────────┘

```

### 1.2 Key Benefits

| Feature          | Traditional I/O | io_uring     |
| ---------------- | --------------- | ------------ |
| Syscall overhead | Per operation   | Batched/Zero |
| Memory copies    | Multiple        | Minimized    |
| Context switches | Frequent        | Reduced      |
| Batching         | Limited         | Native       |
| Polling support  | No              | Yes          |

---

## 2. Architecture Overview

### 2.1 High-Level Architecture

```

┌────────────────────────────────────────────────────────────────┐
│ USER SPACE │
├────────────────────────────────────────────────────────────────┤
│ │
│ ┌─────────────────┐ ┌─────────────────┐ │
│ │ Application │ │ liburing │ │
│ │ │◄────────────►│ (Helper Lib) │ │
│ └────────┬────────┘ └────────┬────────┘ │
│ │ │ │
│ ▼ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Memory Mapped Regions │ │
│ │ ┌──────────────────┐ ┌──────────────────┐ │ │
│ │ │ Submission Ring │ │ Completion Ring │ │ │
│ │ │ (SQ Ring) │ │ (CQ Ring) │ │ │
│ │ └──────────────────┘ └──────────────────┘ │ │
│ │ ┌──────────────────┐ │ │
│ │ │ SQE Array │ │ │
│ │ └──────────────────┘ │ │
│ └─────────────────────────────────────────────────────┘ │
│ │ │
├──────────────────────────────┼──────────────────────────────────┤
│ KERNEL SPACE │
├──────────────────────────────┼──────────────────────────────────┤
│ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ io_uring Core │ │
│ │ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ │ │
│ │ │ SQ Thread │ │ IO Workers │ │ Poll Mech │ │ │
│ │ │ (SQPOLL) │ │ (async) │ │ (IOPOLL) │ │ │
│ │ └─────────────┘ └─────────────┘ └─────────────┘ │ │
│ └─────────────────────────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ VFS / Block Layer / Network │ │
│ └─────────────────────────────────────────────────────┘ │
│ │ │
│ ▼ │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Hardware │ │
│ └─────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────┘

```

### 2.2 Ring Buffer Concept

```

            Submission Queue (SQ)                 Completion Queue (CQ)
        ┌───────────────────────┐            ┌───────────────────────┐
        │                       │            │                       │

┌────┤ head (kernel owns) │ ┌────┤ head (user owns) │
│    │ │ │ │ │
│ ├───────────────────────┤ │ ├───────────────────────┤
│ │ SQE Index 0 │ │ │ CQE 0 │
│ ├───────────────────────┤ │ ├───────────────────────┤
│ │ SQE Index 1 │ │ │ CQE 1 │
│ ├───────────────────────┤ │ ├───────────────────────┤
│ │ SQE Index 2 │◄──────┼────│ CQE 2 │
│ ├───────────────────────┤ │ ├───────────────────────┤
│ │ ... │ │ │ ... │
│ ├───────────────────────┤ │ ├───────────────────────┤
│ │ SQE Index N │ │ │ CQE N │
│ │ │ │ │ │
│ ├───────────────────────┤ │ ├───────────────────────┤
└────┤ tail (user owns) │ └────┤ tail (kernel owns) │
│ │ │ │
└───────────────────────┘ └───────────────────────┘

        Producer: User Space                  Producer: Kernel
        Consumer: Kernel                      Consumer: User Space

```

---

## 3. Core Data Structures

### 3.1 Submission Queue Entry (SQE)

```c
/* From include/uapi/linux/io_uring.h */
struct io_uring_sqe {
    __u8    opcode;         /* Operation code (IORING_OP_*) */
    __u8    flags;          /* IOSQE_ flags */
    __u16   ioprio;         /* I/O priority */
    __s32   fd;             /* File descriptor */
    union {
        __u64   off;        /* Offset into file */
        __u64   addr2;
        struct {
            __u32   cmd_op;
            __u32   __pad1;
        };
    };
    union {
        __u64   addr;       /* Buffer address or pointer */
        __u64   splice_off_in;
    };
    __u32   len;            /* Buffer length or count */
    union {
        __kernel_rwf_t  rw_flags;
        __u32           fsync_flags;
        __u16           poll_events;
        __u32           poll32_events;
        __u32           sync_range_flags;
        __u32           msg_flags;
        __u32           timeout_flags;
        __u32           accept_flags;
        __u32           cancel_flags;
        __u32           open_flags;
        __u32           statx_flags;
        __u32           fadvise_advice;
        __u32           splice_flags;
        __u32           rename_flags;
        __u32           unlink_flags;
        __u32           hardlink_flags;
        __u32           xattr_flags;
        __u32           msg_ring_flags;
        __u32           uring_cmd_flags;
    };
    __u64   user_data;      /* User data (returned in CQE) */
    union {
        __u16   buf_index;  /* Index into fixed buffers */
        __u16   buf_group;  /* Buffer group ID */
    };
    __u16   personality;    /* Personality to use */
    union {
        __s32   splice_fd_in;
        __u32   file_index;
        struct {
            __u16   addr_len;
            __u16   __pad3[1];
        };
    };
    union {
        struct {
            __u64   addr3;
            __u64   __pad2[1];
        };
        __u8    cmd[0];
    };
};
```

### 3.2 Completion Queue Entry (CQE)

```c
struct io_uring_cqe {
    __u64   user_data;      /* Copied from SQE */
    __s32   res;            /* Result code */
    __u32   flags;          /* IORING_CQE_F_* flags */

    /* Extended CQE (if IORING_SETUP_CQE32) */
    __u64   big_cqe[];      /* Additional data */
};
```

### 3.3 Ring Offsets Structure

```c
struct io_sqring_offsets {
    __u32 head;             /* Offset of ring head */
    __u32 tail;             /* Offset of ring tail */
    __u32 ring_mask;        /* Ring size mask */
    __u32 ring_entries;     /* Number of entries */
    __u32 flags;            /* Ring flags */
    __u32 dropped;          /* Number of dropped entries */
    __u32 array;            /* SQE index array offset */
    __u32 resv1;
    __u64 resv2;
};

struct io_cqring_offsets {
    __u32 head;
    __u32 tail;
    __u32 ring_mask;
    __u32 ring_entries;
    __u32 overflow;
    __u32 cqes;             /* CQE array offset */
    __u32 flags;
    __u32 resv1;
    __u64 resv2;
};
```

### 3.4 Main io_uring Context (Kernel)

```c
/* From fs/io_uring.c (simplified) */
struct io_ring_ctx {
    /* Frequently accessed fields */
    struct {
        unsigned int        flags;
        unsigned int        ring_fd;
        unsigned int        sq_entries;
        unsigned int        cq_entries;

        struct io_rings     *rings;
        struct io_uring_sqe *sq_sqes;
    } ____cacheline_aligned_in_smp;

    /* Submission queue */
    struct {
        unsigned            cached_sq_head;
        unsigned            sq_mask;
        unsigned            sq_thread_idle;
        unsigned            cached_sq_dropped;
        struct io_sq_data   *sq_data;       /* SQPOLL data */
    } ____cacheline_aligned_in_smp;

    /* Completion queue */
    struct {
        unsigned            cached_cq_tail;
        unsigned            cq_mask;
        atomic_t            cq_timeouts;
        unsigned            cq_extra;
    } ____cacheline_aligned_in_smp;

    /* Fixed resources */
    struct io_mapped_ubuf   **user_bufs;    /* Registered buffers */
    unsigned int            nr_user_bufs;
    struct file             **user_files;    /* Registered files */
    unsigned int            nr_user_files;

    /* Task and credential tracking */
    struct task_struct      *submitter_task;
    const struct cred       *sq_creds;

    /* Work queues */
    struct io_wq            *io_wq;

    /* Memory mappings */
    struct page             **ring_pages;
    unsigned int            nr_ring_pages;

    /* ... additional fields ... */
};
```

---

## 4. Ring Buffer Implementation

### 4.1 Memory Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SQ Ring Memory Layout                            │
├─────────────────────────────────────────────────────────────────────┤
│  Offset 0: struct io_rings (shared ring metadata)                   │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  sq_head (u32) │ sq_tail (u32) │ sq_flags │ sq_dropped │ ...   ││
│  │  cq_head (u32) │ cq_tail (u32) │ cq_flags │ cq_overflow│ ...   ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  Offset sq_array: SQ Index Array                                    │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  idx[0] │ idx[1] │ idx[2] │ ... │ idx[entries-1]                ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  SQE Array (separate mmap region)                                   │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  SQE[0] │ SQE[1] │ SQE[2] │ ... │ SQE[entries-1]                ││
│  │  64B    │  64B   │  64B   │     │  64B                          ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     CQ Ring Memory Layout                            │
├─────────────────────────────────────────────────────────────────────┤
│  Offset cqes: CQE Array                                             │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │  CQE[0] │ CQE[1] │ CQE[2] │ ... │ CQE[entries-1]                ││
│  │  16B    │  16B   │  16B   │     │  16B (or 32B with CQE32)      ││
│  └─────────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Ring Index Management

```c
/* Producer-Consumer Protocol */

/* User submitting (SQ) - User is producer */
static inline void io_uring_submit(struct io_uring *ring)
{
    unsigned tail = *ring->sq.ktail;    /* Read current kernel tail */
    unsigned next = tail + 1;

    /* Check if ring is full */
    if (next - *ring->sq.khead > ring->sq.ring_entries)
        return; /* Ring full */

    /* Get SQE slot */
    unsigned index = tail & ring->sq.ring_mask;
    struct io_uring_sqe *sqe = &ring->sq.sqes[index];

    /* Fill SQE... */

    /* Memory barrier to ensure SQE is visible before updating tail */
    io_uring_smp_store_release(ring->sq.ktail, next);
}

/* Kernel consuming (SQ) - Kernel is consumer */
static int io_submit_sqes(struct io_ring_ctx *ctx, unsigned int nr)
{
    unsigned head = ctx->cached_sq_head;
    unsigned tail = READ_ONCE(ctx->rings->sq.tail);

    /* Memory barrier */
    smp_rmb();

    while (head != tail) {
        unsigned index = head & ctx->sq_mask;
        unsigned sqe_index = ctx->sq_array[index];
        struct io_uring_sqe *sqe = &ctx->sq_sqes[sqe_index];

        /* Process SQE... */

        head++;
    }

    WRITE_ONCE(ctx->rings->sq.head, head);
    return submitted;
}
```

### 4.3 Memory Barriers

```c
/* Critical memory ordering for lock-free communication */

/* Store-release: Ensures all prior stores are visible */
#define io_uring_smp_store_release(p, v)    \
    atomic_store_explicit((_Atomic typeof(*(p)) *)(p), (v), \
                         memory_order_release)

/* Load-acquire: Ensures all subsequent loads see stores */
#define io_uring_smp_load_acquire(p)    \
    atomic_load_explicit((_Atomic typeof(*(p)) *)(p), \
                        memory_order_acquire)

/* Example flow:
 *
 * User Space                    Kernel Space
 * -----------                   ------------
 * Fill SQE data
 * smp_store_release(tail++)
 *                              smp_load_acquire(tail)
 *                              Read SQE data
 *                              Process request
 *                              Fill CQE data
 *                              smp_store_release(cq_tail++)
 * smp_load_acquire(cq_tail)
 * Read CQE data
 */
```

---

## 5. Submission Queue (SQ) Deep Dive

### 5.1 SQE Operations (Opcodes)

```c
enum io_uring_op {
    IORING_OP_NOP,              /* No operation */
    IORING_OP_READV,            /* Vectored read */
    IORING_OP_WRITEV,           /* Vectored write */
    IORING_OP_FSYNC,            /* File sync */
    IORING_OP_READ_FIXED,       /* Read with fixed buffer */
    IORING_OP_WRITE_FIXED,      /* Write with fixed buffer */
    IORING_OP_POLL_ADD,         /* Add poll event */
    IORING_OP_POLL_REMOVE,      /* Remove poll event */
    IORING_OP_SYNC_FILE_RANGE,  /* Sync file range */
    IORING_OP_SENDMSG,          /* Send message */
    IORING_OP_RECVMSG,          /* Receive message */
    IORING_OP_TIMEOUT,          /* Timeout operation */
    IORING_OP_TIMEOUT_REMOVE,   /* Remove timeout */
    IORING_OP_ACCEPT,           /* Accept connection */
    IORING_OP_ASYNC_CANCEL,     /* Cancel async operation */
    IORING_OP_LINK_TIMEOUT,     /* Linked timeout */
    IORING_OP_CONNECT,          /* Connect socket */
    IORING_OP_FALLOCATE,        /* Allocate file space */
    IORING_OP_OPENAT,           /* Open file */
    IORING_OP_CLOSE,            /* Close file */
    IORING_OP_FILES_UPDATE,     /* Update registered files */
    IORING_OP_STATX,            /* Get file status */
    IORING_OP_READ,             /* Read */
    IORING_OP_WRITE,            /* Write */
    IORING_OP_FADVISE,          /* File advice */
    IORING_OP_MADVISE,          /* Memory advice */
    IORING_OP_SEND,             /* Send data */
    IORING_OP_RECV,             /* Receive data */
    IORING_OP_OPENAT2,          /* Open file (extended) */
    IORING_OP_EPOLL_CTL,        /* Epoll control */
    IORING_OP_SPLICE,           /* Splice data */
    IORING_OP_PROVIDE_BUFFERS,  /* Provide buffers */
    IORING_OP_REMOVE_BUFFERS,   /* Remove buffers */
    IORING_OP_TEE,              /* Tee data */
    IORING_OP_SHUTDOWN,         /* Shutdown socket */
    IORING_OP_RENAMEAT,         /* Rename file */
    IORING_OP_UNLINKAT,         /* Unlink file */
    IORING_OP_MKDIRAT,          /* Make directory */
    IORING_OP_SYMLINKAT,        /* Create symlink */
    IORING_OP_LINKAT,           /* Create hard link */
    IORING_OP_MSG_RING,         /* Message to another ring */
    IORING_OP_FSETXATTR,        /* Set extended attribute */
    IORING_OP_SETXATTR,         /* Set extended attribute */
    IORING_OP_FGETXATTR,        /* Get extended attribute */
    IORING_OP_GETXATTR,         /* Get extended attribute */
    IORING_OP_SOCKET,           /* Create socket */
    IORING_OP_URING_CMD,        /* io_uring command */
    /* ... more opcodes added in newer kernels ... */
    IORING_OP_LAST,
};
```

### 5.2 SQE Flags

```c
/* SQE flags (sqe->flags) */
#define IOSQE_FIXED_FILE        (1U << 0)  /* Use fixed file table */
#define IOSQE_IO_DRAIN          (1U << 1)  /* Issue after in-flight completes */
#define IOSQE_IO_LINK           (1U << 2)  /* Link with next SQE */
#define IOSQE_IO_HARDLINK       (1U << 3)  /* Hard link (ignore errors) */
#define IOSQE_ASYNC             (1U << 4)  /* Force async execution */
#define IOSQE_BUFFER_SELECT     (1U << 5)  /* Select buffer from pool */
#define IOSQE_CQE_SKIP_SUCCESS  (1U << 6)  /* Don't generate CQE on success */

/* Flag usage visualization */
/*
 * IOSQE_IO_LINK: Creates a chain of dependent operations
 *
 * SQE1 (LINK) -> SQE2 (LINK) -> SQE3
 *
 * If SQE1 fails, SQE2 and SQE3 are canceled
 *
 * IOSQE_IO_HARDLINK: Chain continues even on failure
 *
 * SQE1 (HARDLINK) -> SQE2 (HARDLINK) -> SQE3
 *
 * SQE2 executes even if SQE1 fails
 */
```

### 5.3 Submission Path in Kernel

```c
/* Simplified kernel submission path */

/* Entry point: io_uring_enter syscall */
SYSCALL_DEFINE6(io_uring_enter, unsigned int, fd, u32, to_submit,
                u32, min_complete, u32, flags, const void __user *, argp,
                size_t, argsz)
{
    struct io_ring_ctx *ctx;
    struct file *file;
    int ret;

    /* Get io_uring context from fd */
    file = fget(fd);
    ctx = file->private_data;

    /* Submit entries if requested */
    if (to_submit) {
        ret = io_submit_sqes(ctx, to_submit);
    }

    /* Wait for completions if requested */
    if (flags & IORING_ENTER_GETEVENTS) {
        ret = io_cqring_wait(ctx, min_complete, ...);
    }

    return ret;
}

/* Process submission queue */
static int io_submit_sqes(struct io_ring_ctx *ctx, unsigned int nr)
{
    struct io_submit_state state;
    int submitted = 0;

    io_submit_state_start(&state, ctx, nr);

    while (submitted < nr) {
        struct io_uring_sqe *sqe;
        struct io_kiocb *req;

        /* Get next SQE from ring */
        sqe = io_get_sqe(ctx);
        if (!sqe)
            break;

        /* Allocate request structure */
        req = io_alloc_req(ctx);

        /* Initialize request from SQE */
        io_init_req(ctx, req, sqe);

        /* Issue the request */
        io_queue_sqe(req);

        submitted++;
    }

    io_submit_state_end(&state, ctx);
    return submitted;
}
```

---

## 6. Completion Queue (CQ) Deep Dive

### 6.1 CQE Generation

```c
/* Kernel: Generate completion */
static void io_complete_rw(struct kiocb *kiocb, long res)
{
    struct io_kiocb *req = container_of(kiocb, struct io_kiocb, rw.kiocb);
    struct io_ring_ctx *ctx = req->ctx;

    /* Fill CQE */
    io_fill_cqe_req(ctx, req, res, 0);

    /* Free the request */
    io_put_req(req);
}

static void io_fill_cqe_req(struct io_ring_ctx *ctx,
                            struct io_kiocb *req,
                            s32 res, u32 cflags)
{
    struct io_uring_cqe *cqe;
    unsigned tail = ctx->cached_cq_tail;

    /* Check for overflow */
    if (tail - READ_ONCE(ctx->rings->cq.head) >= ctx->cq_entries) {
        /* Handle overflow - add to overflow list */
        io_cqring_event_overflow(ctx, req->cqe.user_data, res, cflags);
        return;
    }

    /* Get CQE slot */
    cqe = &ctx->cqes[tail & ctx->cq_mask];

    /* Fill CQE */
    WRITE_ONCE(cqe->user_data, req->cqe.user_data);
    WRITE_ONCE(cqe->res, res);
    WRITE_ONCE(cqe->flags, cflags);

    /* Memory barrier and update tail */
    smp_store_release(&ctx->rings->cq.tail, tail + 1);

    ctx->cached_cq_tail++;
}
```

### 6.2 CQE Flags

```c
/* CQE flags (cqe->flags) */
#define IORING_CQE_F_BUFFER     (1U << 0)  /* Buffer ID in upper 16 bits */
#define IORING_CQE_F_MORE       (1U << 1)  /* More CQEs for this request */
#define IORING_CQE_F_SOCK_NONEMPTY  (1U << 2)  /* Socket has more data */
#define IORING_CQE_F_NOTIF      (1U << 3)  /* Notification CQE */

/* Buffer ID extraction */
#define IORING_CQE_BUFFER_SHIFT 16
#define io_uring_cqe_get_data(cqe)  ((cqe)->user_data)
#define io_uring_cqe_get_flags(cqe) ((cqe)->flags)
```

### 6.3 Completion Handling (User Space)

```c
/* User space: Process completions */
static int process_completions(struct io_uring *ring)
{
    struct io_uring_cqe *cqe;
    unsigned head;
    int count = 0;

    /* Get current head */
    head = *ring->cq.khead;

    /* Memory barrier */
    read_barrier();

    /* Process all available CQEs */
    while (head != *ring->cq.ktail) {
        unsigned index = head & ring->cq.ring_mask;
        cqe = &ring->cq.cqes[index];

        /* Process this completion */
        handle_completion(cqe->user_data, cqe->res, cqe->flags);

        head++;
        count++;
    }

    /* Update head to mark CQEs as consumed */
    io_uring_smp_store_release(ring->cq.khead, head);

    return count;
}
```

---

## 7. System Call Interface

### 7.1 io_uring_setup

```c
/* Create new io_uring instance */
SYSCALL_DEFINE2(io_uring_setup, u32, entries,
                struct io_uring_params __user *, params)
{
    struct io_uring_params p;
    struct io_ring_ctx *ctx;
    int ret;

    /* Copy params from user space */
    if (copy_from_user(&p, params, sizeof(p)))
        return -EFAULT;

    /* Validate parameters */
    if (entries > IORING_MAX_ENTRIES)
        return -EINVAL;

    /* Round up to power of 2 */
    entries = roundup_pow_of_two(entries);
    p.sq_entries = entries;
    p.cq_entries = 2 * entries;  /* CQ is typically 2x SQ */

    /* Create context */
    ctx = io_ring_ctx_alloc(&p);
    if (!ctx)
        return -ENOMEM;

    /* Initialize rings */
    ret = io_allocate_scq_urings(ctx, &p);
    if (ret)
        goto err;

    /* Create file descriptor */
    ret = io_uring_get_fd(ctx);
    if (ret < 0)
        goto err;

    /* Copy offsets back to user */
    if (copy_to_user(params, &p, sizeof(p))) {
        ret = -EFAULT;
        goto err;
    }

    return ret;  /* Return fd */
}
```

### 7.2 io_uring_params Structure

```c
struct io_uring_params {
    __u32 sq_entries;           /* SQ ring size (out) */
    __u32 cq_entries;           /* CQ ring size (out) */
    __u32 flags;                /* Setup flags */
    __u32 sq_thread_cpu;        /* SQPOLL CPU affinity */
    __u32 sq_thread_idle;       /* SQPOLL idle timeout (ms) */
    __u32 features;             /* Kernel features (out) */
    __u32 wq_fd;               /* Workqueue sharing */
    __u32 resv[3];
    struct io_sqring_offsets sq_off;  /* SQ ring offsets (out) */
    struct io_cqring_offsets cq_off;  /* CQ ring offsets (out) */
};

/* Setup flags */
#define IORING_SETUP_IOPOLL     (1U << 0)  /* Busy-poll for I/O completions */
#define IORING_SETUP_SQPOLL     (1U << 1)  /* Kernel SQ polling thread */
#define IORING_SETUP_SQ_AFF     (1U << 2)  /* SQPOLL CPU affinity */
#define IORING_SETUP_CQSIZE     (1U << 3)  /* Custom CQ size */
#define IORING_SETUP_CLAMP      (1U << 4)  /* Clamp ring sizes */
#define IORING_SETUP_ATTACH_WQ  (1U << 5)  /* Share workqueue */
#define IORING_SETUP_R_DISABLED (1U << 6)  /* Ring starts disabled */
#define IORING_SETUP_SUBMIT_ALL (1U << 7)  /* Submit all on error */
#define IORING_SETUP_COOP_TASKRUN (1U << 8)  /* Cooperative task running */
#define IORING_SETUP_TASKRUN_FLAG (1U << 9)  /* Set flag for task run */
#define IORING_SETUP_SQE128     (1U << 10) /* 128-byte SQEs */
#define IORING_SETUP_CQE32      (1U << 11) /* 32-byte CQEs */
#define IORING_SETUP_SINGLE_ISSUER (1U << 12)  /* Single task submission */
#define IORING_SETUP_DEFER_TASKRUN (1U << 13)  /* Defer task running */
```

### 7.3 io_uring_enter

```c
SYSCALL_DEFINE6(io_uring_enter, unsigned int, fd, u32, to_submit,
                u32, min_complete, u32, flags, const void __user *, argp,
                size_t, argsz)
{
    struct io_ring_ctx *ctx;
    int submitted = 0;
    int ret = 0;

    /* Get context */
    ctx = io_ring_ctx_wait_and_acquire(fd);

    /* Handle different modes */
    if (ctx->flags & IORING_SETUP_SQPOLL) {
        /* SQPOLL mode: wake up kernel thread */
        if (flags & IORING_ENTER_SQ_WAKEUP)
            wake_up(&ctx->sq_data->wait);
    } else {
        /* Normal mode: submit from this context */
        if (to_submit) {
            mutex_lock(&ctx->uring_lock);
            submitted = io_submit_sqes(ctx, to_submit);
            mutex_unlock(&ctx->uring_lock);
        }
    }

    /* Wait for completions */
    if (flags & IORING_ENTER_GETEVENTS) {
        min_complete = min(min_complete, ctx->cq_entries);
        ret = io_cqring_wait(ctx, min_complete, ...);
    }

    return submitted ? submitted : ret;
}

/* Enter flags */
#define IORING_ENTER_GETEVENTS      (1U << 0)  /* Wait for completions */
#define IORING_ENTER_SQ_WAKEUP      (1U << 1)  /* Wake SQPOLL thread */
#define IORING_ENTER_SQ_WAIT        (1U << 2)  /* Wait for SQ space */
#define IORING_ENTER_EXT_ARG        (1U << 3)  /* Extended arguments */
#define IORING_ENTER_REGISTERED_RING (1U << 4) /* Use registered ring */
```

### 7.4 io_uring_register

```c
SYSCALL_DEFINE4(io_uring_register, unsigned int, fd, unsigned int, opcode,
                void __user *, arg, unsigned int, nr_args)
{
    struct io_ring_ctx *ctx;
    int ret;

    ctx = io_ring_ctx_wait_and_acquire(fd);

    switch (opcode) {
    case IORING_REGISTER_BUFFERS:
        ret = io_sqe_buffers_register(ctx, arg, nr_args);
        break;
    case IORING_UNREGISTER_BUFFERS:
        ret = io_sqe_buffers_unregister(ctx);
        break;
    case IORING_REGISTER_FILES:
        ret = io_sqe_files_register(ctx, arg, nr_args);
        break;
    case IORING_UNREGISTER_FILES:
        ret = io_sqe_files_unregister(ctx);
        break;
    case IORING_REGISTER_EVENTFD:
        ret = io_eventfd_register(ctx, arg);
        break;
    case IORING_REGISTER_PROBE:
        ret = io_probe(ctx, arg, nr_args);
        break;
    /* ... many more operations ... */
    }

    return ret;
}

/* Register opcodes */
enum {
    IORING_REGISTER_BUFFERS,
    IORING_UNREGISTER_BUFFERS,
    IORING_REGISTER_FILES,
    IORING_UNREGISTER_FILES,
    IORING_REGISTER_EVENTFD,
    IORING_UNREGISTER_EVENTFD,
    IORING_REGISTER_FILES_UPDATE,
    IORING_REGISTER_EVENTFD_ASYNC,
    IORING_REGISTER_PROBE,
    IORING_REGISTER_PERSONALITY,
    IORING_UNREGISTER_PERSONALITY,
    IORING_REGISTER_RESTRICTIONS,
    IORING_REGISTER_ENABLE_RINGS,
    IORING_REGISTER_FILES2,
    IORING_REGISTER_FILES_UPDATE2,
    IORING_REGISTER_BUFFERS2,
    IORING_REGISTER_BUFFERS_UPDATE,
    IORING_REGISTER_IOWQ_AFF,
    IORING_UNREGISTER_IOWQ_AFF,
    IORING_REGISTER_IOWQ_MAX_WORKERS,
    IORING_REGISTER_RING_FDS,
    IORING_UNREGISTER_RING_FDS,
    IORING_REGISTER_PBUF_RING,
    IORING_UNREGISTER_PBUF_RING,
    IORING_REGISTER_SYNC_CANCEL,
    IORING_REGISTER_FILE_ALLOC_RANGE,
    IORING_REGISTER_LAST,
};
```

---

## 8. Kernel-Side Implementation

### 8.1 Request Lifecycle

```
┌─────────────────────────────────────────────────────────────────────┐
│                    io_uring Request Lifecycle                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  User Space                                                         │
│  ──────────                                                         │
│  1. Prepare SQE ──────────────────────────────────────┐            │
│                                                        │            │
│  ┌────────────────────────────────────────────────────┼────────────┤
│  │ Kernel Space                                        ▼            │
│  │ ────────────                                                     │
│  │                                                                  │
│  │  2. io_submit_sqes()                                            │
│  │     ├── io_get_sqe()        ← Get SQE from ring                 │
│  │     ├── io_alloc_req()      ← Allocate io_kiocb                 │
│  │     └── io_init_req()       ← Initialize request                │
│  │                                                                  │
│  │  3. io_queue_sqe()                                              │
│  │     ├── io_issue_sqe()      ← Try inline execution              │
│  │     │   ├── Success: Complete inline                            │
│  │     │   └── -EAGAIN: Queue to io-wq                             │
│  │     │                                                            │
│  │     └── io_queue_async_work() ← Async execution                 │
│  │                                                                  │
│  │  4. I/O Operation                                               │
│  │     ├── vfs_read/write()                                        │
│  │     ├── sock_sendmsg/recvmsg()                                  │
│  │     └── Other ops...                                            │
│  │                                                                  │
│  │  5. io_complete_rw()                                            │
│  │     ├── io_fill_cqe_req()   ← Fill CQE                          │
│  │     └── io_put_req()        ← Free request                      │
│  │                                                                  │
│  └──────────────────────────────────────────────────────────────────┤
│                                                                      │
│  User Space                                                         │
│  ──────────                                                         │
│  6. Process CQE ◄────────────────────────────────────────          │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 8.2 io_kiocb Structure (Request)

```c
/* Main request structure (simplified) */
struct io_kiocb {
    union {
        struct file     *file;
        struct io_rw    rw;
        struct io_poll  poll;
        struct io_accept accept;
        struct io_sync  sync;
        struct io_cancel cancel;
        struct io_timeout timeout;
        struct io_connect connect;
        struct io_sr_msg sr_msg;
        struct io_open  open;
        struct io_close close;
        struct io_files_update files_update;
        struct io_fadvise fadvise;
        struct io_madvise madvise;
        struct io_epoll epoll;
        struct io_splice splice;
        struct io_provide_buf pbuf;
        struct io_statx statx;
        struct io_shutdown shutdown;
        struct io_rename rename;
        struct io_unlink unlink;
        struct io_mkdir mkdir;
        struct io_symlink symlink;
        struct io_hardlink hardlink;
        struct io_msg   msg;
        struct io_xattr xattr;
        struct io_socket sock;
        struct io_uring_cmd uring_cmd;
    };

    /* Common fields */
    struct io_ring_ctx  *ctx;
    struct task_struct  *task;
    struct io_cqe       cqe;

    u8                  opcode;
    u8                  iopoll_completed;
    u16                 buf_index;
    u32                 flags;

    u64                 user_data;

    struct io_kiocb     *link;
    struct list_head    link_list;

    struct io_wq_work   work;
};
```

### 8.3 SQPOLL Thread Implementation

```c
/* Kernel SQ polling thread */
static int io_sq_thread(void *data)
{
    struct io_sq_data *sqd = data;
    struct io_ring_ctx *ctx;
    unsigned long timeout = 0;
    bool needs_sched = true;

    while (!kthread_should_stop()) {
        bool cap_entries;
        int ret;

        /* Process all contexts attached to this sqd */
        list_for_each_entry(ctx, &sqd->ctx_list, sqd_list) {
            /* Check for new submissions */
            if (io_sqring_entries(ctx)) {
                /* Submit entries */
                ret = io_submit_sqes(ctx, ctx->sq_entries);
                timeout = jiffies + sqd->sq_thread_idle;
                needs_sched = false;
            }
        }

        /* Check if we should sleep */
        if (needs_sched) {
            if (time_after(jiffies, timeout)) {
                /* Idle timeout - set flag and wait */
                list_for_each_entry(ctx, &sqd->ctx_list, sqd_list)
                    io_ring_set_wakeup_flag(ctx);

                schedule();

                list_for_each_entry(ctx, &sqd->ctx_list, sqd_list)
                    io_ring_clear_wakeup_flag(ctx);
            } else {
                /* Brief sleep before checking again */
                cond_resched();
            }
        }

        needs_sched = true;
    }

    return 0;
}
```

### 8.4 io-wq (IO Work Queue)

```c
/* io_uring async work queue system */
struct io_wq {
    unsigned long       state;
    free_work_fn        *free_work;
    io_wq_work_fn       *do_work;

    struct io_wq_hash   *hash;

    atomic_t            worker_refs;
    struct completion   worker_done;

    struct hlist_node   cpuhp_node;

    struct task_struct  *task;

    struct io_wq_acct   acct[IO_WQ_ACCT_NR];
};

enum {
    IO_WQ_ACCT_BOUND,   /* Bounded workers */
    IO_WQ_ACCT_UNBOUND, /* Unbounded workers */
    IO_WQ_ACCT_NR,
};

/* Work queue handler */
static void io_wq_submit_work(struct io_wq_work *work)
{
    struct io_kiocb *req = container_of(work, struct io_kiocb, work);
    struct io_ring_ctx *ctx = req->ctx;

    /* Execute the operation */
    io_issue_sqe(req, IO_URING_F_NONBLOCK);
}
```

---

## 9. Performance Optimization Techniques

### 9.1 Zero-Copy I/O

```c
/* Register fixed buffers for zero-copy */
struct iovec iovecs[NUM_BUFFERS];
for (int i = 0; i < NUM_BUFFERS; i++) {
    iovecs[i].iov_base = aligned_alloc(4096, BUFFER_SIZE);
    iovecs[i].iov_len = BUFFER_SIZE;
}

/* Register with kernel */
io_uring_register_buffers(&ring, iovecs, NUM_BUFFERS);

/* Use fixed buffer in SQE */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_read_fixed(sqe, fd, buffer, len, offset, buf_index);

/*
 * Memory Layout with Fixed Buffers:
 *
 * ┌──────────────────────────────────────────────────┐
 * │                   User Space                      │
 * │  ┌────────────────────────────────────────────┐  │
 * │  │         Registered Buffer Array             │  │
 * │  │  buf[0] │ buf[1] │ buf[2] │ ... │ buf[n]   │  │
 * │  └────────────────────────────────────────────┘  │
 * │           │                                       │
 * │           │ Pages pinned in kernel                │
 * │           ▼                                       │
 * ├──────────────────────────────────────────────────┤
 * │                  Kernel Space                     │
 * │  ┌────────────────────────────────────────────┐  │
 * │  │      io_mapped_ubuf array (page tables)     │  │
 * │  │  Direct DMA possible - no copy needed       │  │
 * │  └────────────────────────────────────────────┘  │
 * └──────────────────────────────────────────────────┘
 */
```

### 9.2 Fixed File Descriptors

```c
/* Register files for faster access */
int fds[NUM_FILES];
for (int i = 0; i < NUM_FILES; i++) {
    fds[i] = open(filenames[i], O_RDWR | O_DIRECT);
}

/* Register with kernel */
io_uring_register_files(&ring, fds, NUM_FILES);

/* Use fixed file in SQE */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, file_index, buffer, len, offset);
sqe->flags |= IOSQE_FIXED_FILE;

/*
 * File Lookup Comparison:
 *
 * Normal:  fd → fdtable lookup → file* → operations
 *          (requires RCU, atomic operations)
 *
 * Fixed:   index → ctx->user_files[index] → operations
 *          (direct array access, no locking)
 */
```

### 9.3 Buffer Ring (Provided Buffers)

```c
/* Setup provided buffer ring */
struct io_uring_buf_ring *br;
int bgid = 1;  /* Buffer group ID */

/* Allocate buffer ring */
struct io_uring_buf_reg reg = {
    .ring_addr = (unsigned long)br,
    .ring_entries = NUM_BUFFERS,
    .bgid = bgid,
};
io_uring_register_buf_ring(&ring, &reg, 0);

/* Add buffers to ring */
io_uring_buf_ring_init(br);
for (int i = 0; i < NUM_BUFFERS; i++) {
    io_uring_buf_ring_add(br, buffers[i], BUFFER_SIZE, i,
                          io_uring_buf_ring_mask(NUM_BUFFERS), i);
}
io_uring_buf_ring_advance(br, NUM_BUFFERS);

/* Use buffer selection in SQE */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv(sqe, sockfd, NULL, BUFFER_SIZE, 0);
sqe->flags |= IOSQE_BUFFER_SELECT;
sqe->buf_group = bgid;

/* Get buffer ID from CQE */
int buf_id = cqe->flags >> IORING_CQE_BUFFER_SHIFT;
```

### 9.4 SQPOLL Mode

```c
/* Setup with SQPOLL */
struct io_uring_params params = {
    .flags = IORING_SETUP_SQPOLL,
    .sq_thread_cpu = 3,        /* Pin to CPU 3 */
    .sq_thread_idle = 1000,    /* 1 second idle timeout */
};

io_uring_queue_init_params(QUEUE_DEPTH, &ring, &params);

/* Submit without syscall */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, fd, buf, len, offset);
io_uring_sqe_set_data(sqe, user_data);

/* Just update tail - kernel thread will pick it up */
io_uring_submit(&ring);  /* No syscall if thread is active! */

/*
 * SQPOLL Thread State Machine:
 *
 * ┌─────────────┐     new work      ┌─────────────┐
 * │   IDLE      │ ─────────────────►│   RUNNING   │
 * │  (sleeping) │                   │ (processing)│
 * └─────────────┘                   └─────────────┘
 *       ▲                                 │
 *       │           sq_thread_idle        │
 *       │              timeout            │
 *       └─────────────────────────────────┘
 */
```

### 9.5 IOPOLL Mode

```c
/* Setup with IOPOLL for polling completions */
struct io_uring_params params = {
    .flags = IORING_SETUP_IOPOLL,
};

io_uring_queue_init_params(QUEUE_DEPTH, &ring, &params);

/* Submit I/O */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, fd, buf, len, offset);  /* Must use O_DIRECT */
io_uring_submit(&ring);

/* Poll for completion - no interrupt */
while (1) {
    ret = io_uring_enter(ring.ring_fd, 0, 1, IORING_ENTER_GETEVENTS, NULL);
    if (ret > 0) {
        /* Process completion */
        break;
    }
}

/*
 * IOPOLL vs Interrupt-Based:
 *
 * Interrupt-Based:
 *   Submit → ... → Hardware IRQ → Softirq → Complete
 *   (interrupt overhead, context switch)
 *
 * IOPOLL:
 *   Submit → Poll → Poll → Poll → Complete
 *   (CPU busy-wait, but no interrupt overhead)
 *   Best for: NVMe, high-IOPS scenarios
 */
```

### 9.6 Linked Requests (Chaining)

```c
/* Create linked operation chain */
struct io_uring_sqe *sqe;

/* First: read from file */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, src_fd, buf, len, 0);
sqe->flags |= IOSQE_IO_LINK;

/* Second: write to another file (only if read succeeds) */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_write(sqe, dst_fd, buf, len, 0);
sqe->flags |= IOSQE_IO_LINK;

/* Third: fsync (only if write succeeds) */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_fsync(sqe, dst_fd, IORING_FSYNC_DATASYNC);
/* No LINK flag - end of chain */

io_uring_submit(&ring);

/*
 * Link Chain Execution:
 *
 * IOSQE_IO_LINK:
 *   SQE1 ──success──► SQE2 ──success──► SQE3
 *     │                 │
 *     failure           failure
 *     │                 │
 *     ▼                 ▼
 *   Cancel SQE2,3     Cancel SQE3
 *
 * IOSQE_IO_HARDLINK:
 *   SQE1 ────────────► SQE2 ────────────► SQE3
 *   (always continues regardless of failure)
 */
```

### 9.7 Multishot Operations

```c
/* Multishot accept - one SQE, multiple completions */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_multishot_accept(sqe, listen_fd, NULL, NULL, 0);

io_uring_submit(&ring);

/* Process completions */
while (1) {
    struct io_uring_cqe *cqe;
    io_uring_wait_cqe(&ring, &cqe);

    if (cqe->res >= 0) {
        int client_fd = cqe->res;
        /* Handle new connection */
    }

    /* Check if more completions coming */
    if (!(cqe->flags & IORING_CQE_F_MORE)) {
        /* Multishot finished, need to rearm */
        break;
    }

    io_uring_cqe_seen(&ring, cqe);
}

/*
 * Multishot vs Single-shot:
 *
 * Single-shot:   Submit → Accept → CQE → Submit → Accept → CQE → ...
 *                (submit per accept)
 *
 * Multishot:     Submit → Accept → CQE
 *                       ↓
 *                       Accept → CQE
 *                       ↓
 *                       Accept → CQE
 *                       ↓
 *                       ...
 *                (one submit, many completions)
 */
```

---

## 10. Advanced Features

### 10.1 Request Cancellation

```c
/* Cancel specific request */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_cancel(sqe, user_data, 0);  /* Cancel by user_data */
io_uring_submit(&ring);

/* Cancel all requests for fd */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_cancel_fd(sqe, fd, 0);
io_uring_submit(&ring);

/* Kernel-side cancellation */
static int io_async_cancel(struct io_kiocb *req, unsigned int issue_flags)
{
    struct io_ring_ctx *ctx = req->ctx;
    struct io_cancel_data cd = {
        .ctx = ctx,
        .data = req->cancel.addr,
    };

    /* Search and cancel matching request */
    return io_try_cancel(req, &cd, issue_flags);
}
```

### 10.2 Timeout Operations

```c
/* Absolute timeout */
struct __kernel_timespec ts = { .tv_sec = 5, .tv_nsec = 0 };
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_timeout(sqe, &ts, 0, 0);

/* Linked timeout - timeout for linked operations */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, fd, buf, len, 0);
sqe->flags |= IOSQE_IO_LINK;

sqe = io_uring_get_sqe(&ring);
io_uring_prep_link_timeout(sqe, &ts, 0);

io_uring_submit(&ring);

/*
 * Timeout Types:
 *
 * IORING_TIMEOUT_ABS:     Absolute time
 * IORING_TIMEOUT_UPDATE:  Update existing timeout
 * IORING_TIMEOUT_BOOTTIME: Use CLOCK_BOOTTIME
 * IORING_TIMEOUT_REALTIME: Use CLOCK_REALTIME
 * IORING_TIMEOUT_ETIME_SUCCESS: Return success on timeout
 */
```

### 10.3 Socket Operations

```c
/* Full async socket lifecycle */

/* Create socket */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
io_uring_prep_socket(sqe, AF_INET, SOCK_STREAM, 0, 0);

/* Connect */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_connect(sqe, sockfd, addr, addrlen);

/* Send with zerocopy */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_send_zc(sqe, sockfd, buf, len, 0, 0);

/* Receive with multishot */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv_multishot(sqe, sockfd, NULL, 0, 0);
sqe->flags |= IOSQE_BUFFER_SELECT;
sqe->buf_group = bgid;

/* Shutdown */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_shutdown(sqe, sockfd, SHUT_RDWR);

/* Close */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_close(sqe, sockfd);
```

### 10.4 Direct Descriptor Operations

```c
/* Allocate file slot in fixed table */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_socket_direct_alloc(sqe, AF_INET, SOCK_STREAM, 0, 0);
/* Result: fixed file index */

/* Accept directly into fixed slot */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_accept_direct(sqe, listen_fd, NULL, NULL, 0,
                            IORING_FILE_INDEX_ALLOC);

/* Open file directly into fixed slot */
sqe = io_uring_get_sqe(&ring);
io_uring_prep_openat_direct(sqe, AT_FDCWD, path, flags, mode,
                            file_index);

/*
 * Benefits of Direct Descriptors:
 * - Skip fd allocation (no fdtable manipulation)
 * - Skip fd → file lookup on every operation
 * - Better cache locality
 */
```

### 10.5 Message Passing Between Rings

```c
/* Send message to another io_uring instance */
struct io_uring ring1, ring2;

/* In ring1: send message to ring2 */
struct io_uring_sqe *sqe = io_uring_get_sqe(&ring1);
io_uring_prep_msg_ring(sqe, ring2.ring_fd,
                       IORING_MSG_DATA,  /* message type */
                       result_value,      /* data to send */
                       user_data,         /* CQE user_data in ring2 */
                       0);

io_uring_submit(&ring1);

/* In ring2: receive CQE with the message */
struct io_uring_cqe *cqe;
io_uring_wait_cqe(&ring2, &cqe);
/* cqe->res contains result_value */
/* cqe->user_data contains user_data */
```

---

## 11. Memory Management

### 11.1 Ring Memory Allocation

```c
/* Kernel: Allocating ring memory */
static int io_allocate_scq_urings(struct io_ring_ctx *ctx,
                                   struct io_uring_params *p)
{
    struct io_rings *rings;
    size_t size, sq_array_offset;
    unsigned sq_entries = p->sq_entries;
    unsigned cq_entries = p->cq_entries;

    /* Calculate total size for rings + CQEs */
    size = struct_size(rings, cqes, cq_entries);

    /* Account for SQ array */
    sq_array_offset = size;
    size += array_size(sizeof(__u32), sq_entries);

    /* Allocate ring memory (page-aligned) */
    rings = io_mem_alloc(size);
    if (!rings)
        return -ENOMEM;

    ctx->rings = rings;
    ctx->sq_array = (u32 *)((char *)rings + sq_array_offset);

    rings->sq_ring_mask = sq_entries - 1;
    rings->cq_ring_mask = cq_entries - 1;
    rings->sq_ring_entries = sq_entries;
    rings->cq_ring_entries = cq_entries;

    /* Allocate SQE array (separate allocation for separate mmap) */
    ctx->sq_sqes = io_mem_alloc(sq_entries * sizeof(struct io_uring_sqe));
    if (!ctx->sq_sqes) {
        io_mem_free(rings);
        return -ENOMEM;
    }

    return 0;
}
```

### 11.2 Page Pinning for Registered Buffers

```c
/* Kernel: Pin user pages for fixed buffers */
static int io_sqe_buffer_register(struct io_ring_ctx *ctx,
                                   struct iovec *iov,
                                   struct io_mapped_ubuf **pimu,
                                   struct page **last_hpage)
{
    struct io_mapped_ubuf *imu;
    unsigned long start, end;
    struct page **pages;
    int nr_pages, ret;

    start = (unsigned long)iov->iov_base;
    end = (start + iov->iov_len + PAGE_SIZE - 1) >> PAGE_SHIFT;
    start >>= PAGE_SHIFT;
    nr_pages = end - start;

    /* Allocate page array */
    pages = kvmalloc_array(nr_pages, sizeof(struct page *), GFP_KERNEL);
    if (!pages)
        return -ENOMEM;

    /* Pin user pages in memory (prevents swap-out) */
    ret = pin_user_pages_fast(
        (unsigned long)iov->iov_base,
        nr_pages,
        FOLL_WRITE | FOLL_LONGTERM,
        pages
    );

    if (ret != nr_pages) {
        /* Partial pin - undo */
        if (ret > 0)
            unpin_user_pages(pages, ret);
        kvfree(pages);
        return -EFAULT;
    }

    /* Create kernel mapping structure */
    imu = kvmalloc(struct_size(imu, bvec, nr_pages), GFP_KERNEL);
    imu->ubuf = (unsigned long)iov->iov_base;
    imu->ubuf_end = imu->ubuf + iov->iov_len;
    imu->nr_bvecs = nr_pages;

    /* Setup bio_vec entries for DMA */
    for (int i = 0; i < nr_pages; i++) {
        imu->bvec[i].bv_page = pages[i];
        imu->bvec[i].bv_len = PAGE_SIZE;
        imu->bvec[i].bv_offset = 0;
    }

    /* Adjust first and last page for partial coverage */
    unsigned off = offset_in_page(iov->iov_base);
    imu->bvec[0].bv_offset = off;
    imu->bvec[0].bv_len = PAGE_SIZE - off;

    *pimu = imu;
    kvfree(pages);
    return 0;
}
```

### 11.3 Memory Mapping (mmap) Interface

```c
/*
 * io_uring exposes three mmap regions to user space:
 *
 * ┌──────────────────────────────────────────────────────────────────┐
 * │ Offset Constant           │ Hex Value    │ Content              │
 * ├──────────────────────────────────────────────────────────────────┤
 * │ IORING_OFF_SQ_RING        │ 0x00000000   │ SQ ring + SQ array   │
 * │ IORING_OFF_CQ_RING        │ 0x08000000   │ CQ ring + CQE array  │
 * │ IORING_OFF_SQES           │ 0x10000000   │ SQE array            │
 * └──────────────────────────────────────────────────────────────────┘
 *
 * Note: Since kernel 5.12, SQ ring and CQ ring share the same
 * mmap region (IORING_OFF_SQ_RING includes CQ data), so only
 * two mmap calls are needed.
 */

/* Kernel: mmap handler */
static int io_uring_mmap(struct file *file, struct vm_area_struct *vma)
{
    struct io_ring_ctx *ctx = file->private_data;
    size_t sz = vma->vm_end - vma->vm_start;
    unsigned long pfn;
    void *ptr;

    switch ((pgoff_t)vma->vm_pgoff) {
    case IORING_OFF_SQ_RING >> PAGE_SHIFT:
        ptr = ctx->rings;
        break;
    case IORING_OFF_SQES >> PAGE_SHIFT:
        ptr = ctx->sq_sqes;
        break;
    default:
        return -EINVAL;
    }

    /* Map kernel pages into user space */
    pfn = virt_to_phys(ptr) >> PAGE_SHIFT;
    return remap_pfn_range(vma, vma->vm_start, pfn, sz, vma->vm_page_prot);
}

/* User space: Mapping the rings */
/*
 * struct io_uring_params p;
 * int ring_fd = io_uring_setup(entries, &p);
 *
 * // Map SQ ring (includes CQ ring since 5.12)
 * void *sq_ptr = mmap(NULL,
 *     p.sq_off.array + p.sq_entries * sizeof(__u32),
 *     PROT_READ | PROT_WRITE,
 *     MAP_SHARED | MAP_POPULATE,
 *     ring_fd, IORING_OFF_SQ_RING);
 *
 * // Map SQE array
 * void *sqes = mmap(NULL,
 *     p.sq_entries * sizeof(struct io_uring_sqe),
 *     PROT_READ | PROT_WRITE,
 *     MAP_SHARED | MAP_POPULATE,
 *     ring_fd, IORING_OFF_SQES);
 */
```

### 11.4 Huge Page Support

```c
/*
 * io_uring can benefit from huge pages for:
 * 1. Ring buffers (reduces TLB misses for ring access)
 * 2. Registered buffers (better DMA performance)
 *
 * Memory Hierarchy Impact:
 *
 * 4KB pages:                    2MB huge pages:
 * ┌──────────┐                  ┌──────────────────────┐
 * │ 4KB page │ × 512 TLB       │ 2MB huge page        │ × 1 TLB
 * │ 4KB page │   entries        │                      │   entry
 * │ 4KB page │   = 2MB          │                      │   = 2MB
 * │ ...      │                  │                      │
 * │ 4KB page │                  │                      │
 * └──────────┘                  └──────────────────────┘
 * (many TLB misses)             (single TLB entry)
 */

/* Kernel: io_mem_alloc with huge page support (5.19+) */
static void *io_mem_alloc(size_t size)
{
    gfp_t gfp = GFP_KERNEL_ACCOUNT | __GFP_ZERO |
                 __GFP_NOWARN | __GFP_COMP;
    void *ptr;

    /* Try huge page allocation first for large rings */
    if (size >= PMD_SIZE) {
        ptr = (void *)__get_free_pages(gfp | __GFP_HUGETLB,
                                        get_order(size));
        if (ptr)
            return ptr;
    }

    /* Fall back to regular pages */
    return (void *)__get_free_pages(gfp, get_order(size));
}
```

### 11.5 Request Allocation and Caching

```c
/*
 * io_kiocb (request) allocation is performance-critical.
 * io_uring uses a per-ctx free list + slab cache.
 *
 * Allocation Strategy:
 * ┌─────────────────────────────────────────────────────────┐
 * │ 1. Check per-submit free list (batch-local cache)       │
 * │    └── Hit? Return immediately (fastest)                │
 * │                                                          │
 * │ 2. Check ctx->submit_state.free_list                    │
 * │    └── Hit? Return from free list                       │
 * │                                                          │
 * │ 3. Bulk allocate from slab (io_kiocb_cachep)            │
 * │    └── Allocate IO_REQ_ALLOC_BATCH (8) at once          │
 * │    └── Put extras on free list                          │
 * └─────────────────────────────────────────────────────────┘
 */

static struct io_kiocb *io_alloc_req(struct io_ring_ctx *ctx)
{
    struct io_submit_state *state = &ctx->submit_state;
    struct io_kiocb *req;

    /* Fast path: cached request available */
    if (!list_empty(&state->free_list)) {
        req = list_first_entry(&state->free_list,
                               struct io_kiocb, inflight_entry);
        list_del(&req->inflight_entry);
        state->free_reqs--;
        return req;
    }

    /* Slow path: bulk allocate */
    return io_alloc_req_bulk(ctx);
}

/* Freeing: Return to per-ctx cache instead of slab */
static void io_free_req(struct io_kiocb *req)
{
    struct io_ring_ctx *ctx = req->ctx;

    /* Return to free list for reuse */
    list_add(&req->inflight_entry, &ctx->submit_state.free_list);
    ctx->submit_state.free_reqs++;

    /* Periodically flush excess back to slab */
    if (ctx->submit_state.free_reqs > IO_REQ_CACHE_MAX)
        io_flush_cached_reqs(ctx);
}
```

### 11.6 IOMMU and DMA Considerations

```
┌─────────────────────────────────────────────────────────────────┐
│                DMA Path with Fixed Buffers                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  User Buffer ──pin──► Physical Pages ──map──► DMA Address        │
│                                                                  │
│  Without IOMMU:                                                  │
│  ┌──────────┐    1:1 mapping    ┌──────────┐                    │
│  │ Physical │ ─────────────────►│   DMA    │                    │
│  │ Address  │                   │ Address  │                    │
│  └──────────┘                   └──────────┘                    │
│                                                                  │
│  With IOMMU:                                                     │
│  ┌──────────┐    IOMMU table    ┌──────────┐                    │
│  │ Physical │ ──► ┌────────┐ ──►│   DMA    │                    │
│  │ Address  │     │ IOMMU  │    │ Address  │                    │
│  └──────────┘     └────────┘    └──────────┘                    │
│  (scattered)     (remapping)    (contiguous for device)          │
│                                                                  │
│  Fixed buffers advantage:                                        │
│  - Pages pinned at registration time (not per I/O)              │
│  - DMA mappings cached across operations                         │
│  - No per-I/O get_user_pages() + put_user_pages()              │
│  - No per-I/O dma_map_sg() + dma_unmap_sg()                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 12. Practical Performance Analysis with perf

### 12.1 io_uring Tracepoints

```bash
# List all io_uring tracepoints
perf list 'io_uring:*'

# Available tracepoints (kernel 5.10+):
#   io_uring:io_uring_complete     - Request completed
#   io_uring:io_uring_submit_sqe   - SQE submitted
#   io_uring:io_uring_queue_async_work - Work queued to io-wq
#   io_uring:io_uring_defer        - Request deferred
#   io_uring:io_uring_link         - Linked request
#   io_uring:io_uring_cqring_wait  - Waiting on CQ ring
#   io_uring:io_uring_fail_link    - Linked request failed
#   io_uring:io_uring_create       - Ring created
#   io_uring:io_uring_register     - Resource registered
#   io_uring:io_uring_task_add     - Task work added
#   io_uring:io_uring_task_run     - Task work executed
#   io_uring:io_uring_short_write  - Short write occurred
#   io_uring:io_uring_local_work_run - Local task work run
```

### 12.2 Basic perf Tracing

```bash
# Trace all io_uring events for a process
perf trace -e 'io_uring:*' -p <PID>

# Record io_uring events for later analysis
perf record -e 'io_uring:*' -p <PID> -- sleep 10
perf script

# Count io_uring events by type
perf stat -e 'io_uring:io_uring_submit_sqe' \
          -e 'io_uring:io_uring_complete' \
          -e 'io_uring:io_uring_queue_async_work' \
          -p <PID> -- sleep 10

# Sample output:
#   1,245,892  io_uring:io_uring_submit_sqe
#   1,245,890  io_uring:io_uring_complete
#       2,341  io_uring:io_uring_queue_async_work   (0.19% async)
```

### 12.3 Latency Analysis

```bash
# Measure submission-to-completion latency with BPF
# Using bpftrace (requires kernel 5.5+)
bpftrace -e '
tracepoint:io_uring:io_uring_submit_sqe {
    @start[args->req] = nsecs;
}

tracepoint:io_uring:io_uring_complete {
    if (@start[args->req]) {
        @latency_us = hist((nsecs - @start[args->req]) / 1000);
        delete(@start[args->req]);
    }
}

END {
    print(@latency_us);
}'

# Sample output:
# @latency_us:
# [1, 2)            1024 |@@@@@@                          |
# [2, 4)            5891 |@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@|
# [4, 8)            3201 |@@@@@@@@@@@@@@@@@               |
# [8, 16)            891 |@@@@                            |
# [16, 32)           234 |@                               |
# [32, 64)            45 |                                |
# [64, 128)           12 |                                |
```

### 12.4 Flame Graph Generation

```bash
# CPU flame graph for io_uring workload
perf record -F 99 -g -p <PID> -- sleep 30
perf script | stackcollapse-perf.pl | flamegraph.pl > io_uring_flame.svg

# Off-CPU flame graph (shows where io_uring blocks)
# Requires offcputime from BCC tools
offcputime-bpfcc -df -p <PID> 30 | flamegraph.pl \
    --color=io --title="Off-CPU Time" > io_uring_offcpu.svg

# io_uring-specific flame graph (filter to io_uring frames)
perf record -F 99 -g -p <PID> -- sleep 30
perf script | stackcollapse-perf.pl | \
    grep -E 'io_uring|io_submit|io_queue|io_issue|io_complete' | \
    flamegraph.pl > io_uring_only.svg
```

### 12.5 Syscall Overhead Measurement

```bash
# Compare syscall rates: traditional I/O vs io_uring
# Traditional I/O:
perf stat -e 'raw_syscalls:sys_enter' ./traditional_io_app -- sleep 5
# Expected: ~500,000+ syscalls for 500K I/O operations

# io_uring (normal mode):
perf stat -e 'raw_syscalls:sys_enter' ./io_uring_app -- sleep 5
# Expected: ~5,000 syscalls (batched submissions)

# io_uring (SQPOLL mode):
perf stat -e 'raw_syscalls:sys_enter' ./io_uring_sqpoll_app -- sleep 5
# Expected: ~50 syscalls (setup + occasional wakeup only)

# Detailed syscall breakdown
perf trace -s -p <PID> -- sleep 5
# Shows: io_uring_enter calls, their duration, and frequency
```

### 12.6 Cache and Memory Analysis

```bash
# Measure cache behavior of ring buffer access
perf stat -e 'cache-references,cache-misses,L1-dcache-loads,L1-dcache-load-misses' \
    -p <PID> -- sleep 10

# Memory access patterns
perf mem record -p <PID> -- sleep 10
perf mem report --sort=mem

# TLB pressure (relevant for huge page decision)
perf stat -e 'dTLB-loads,dTLB-load-misses,dTLB-stores,dTLB-store-misses' \
    -p <PID> -- sleep 10
```

### 12.7 BPF-based Deep Inspection

```c
/* BPF program to track io_uring queue depth over time */
/* Compile with: bpftool prog load io_uring_depth.bpf.o */

#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, u64);     /* ctx pointer */
    __type(value, u64);   /* current depth */
} depth_map SEC(".maps");

SEC("tp/io_uring/io_uring_submit_sqe")
int trace_submit(struct trace_event_raw_io_uring_submit_sqe *ctx)
{
    u64 key = ctx->ctx;
    u64 *depth = bpf_map_lookup_elem(&depth_map, &key);
    if (depth) {
        __sync_fetch_and_add(depth, 1);
    } else {
        u64 val = 1;
        bpf_map_update_elem(&depth_map, &key, &val, BPF_ANY);
    }
    return 0;
}

SEC("tp/io_uring/io_uring_complete")
int trace_complete(struct trace_event_raw_io_uring_complete *ctx)
{
    u64 key = ctx->ctx;
    u64 *depth = bpf_map_lookup_elem(&depth_map, &key);
    if (depth && *depth > 0) {
        __sync_fetch_and_sub(depth, 1);
    }
    return 0;
}
```

---

## 13. Benchmarking and Profiling

### 13.1 fio Benchmarking

```bash
# fio supports io_uring natively via --ioengine=io_uring

# Basic sequential read benchmark
fio --name=seq-read \
    --ioengine=io_uring \
    --iodepth=64 \
    --rw=read \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --numjobs=1 \
    --filename=/dev/nvme0n1

# Random read with fixed buffers and registered files
fio --name=rand-read-fixed \
    --ioengine=io_uring \
    --iodepth=128 \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --numjobs=4 \
    --fixedbufs=1 \
    --registerfiles=1 \
    --filename=/dev/nvme0n1

# SQPOLL mode benchmark
fio --name=sqpoll-test \
    --ioengine=io_uring \
    --iodepth=128 \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --sqthread_poll=1 \
    --sqthread_poll_cpu=3 \
    --fixedbufs=1 \
    --registerfiles=1 \
    --filename=/dev/nvme0n1

# IOPOLL mode (requires NVMe with polling support)
fio --name=hipri-test \
    --ioengine=io_uring \
    --iodepth=64 \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --hipri=1 \
    --size=1G \
    --filename=/dev/nvme0n1

# Compare io_uring vs libaio
fio --name=libaio-baseline \
    --ioengine=libaio \
    --iodepth=128 \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --filename=/dev/nvme0n1

fio --name=io_uring-compare \
    --ioengine=io_uring \
    --iodepth=128 \
    --rw=randread \
    --bs=4k \
    --direct=1 \
    --size=1G \
    --fixedbufs=1 \
    --registerfiles=1 \
    --filename=/dev/nvme0n1
```

### 13.2 Expected Benchmark Results

```
┌────────────────────────────────────────────────────────────────────┐
│ 4KB Random Read Benchmark (NVMe SSD, QD=128, 1 Thread)            │
├──────────────────┬──────────┬──────────┬──────────┬───────────────┤
│    Engine        │   IOPS   │  BW MB/s │ Lat(avg) │   CPU/IOP    │
├──────────────────┼──────────┼──────────┼──────────┼───────────────┤
│ sync read()      │   80K    │   312    │  12.5 us │   ~2000 ns   │
│ libaio           │  350K    │  1367    │   2.8 us │    ~500 ns   │
│ io_uring         │  400K    │  1562    │   2.5 us │    ~400 ns   │
│ io_uring+fixed   │  450K    │  1757    │   2.2 us │    ~300 ns   │
│ io_uring+SQPOLL  │  500K    │  1953    │   2.0 us │    ~200 ns*  │
│ io_uring+IOPOLL  │  550K    │  2148    │   1.8 us │    ~180 ns*  │
└──────────────────┴──────────┴──────────┴──────────┴───────────────┘
* Plus dedicated kernel thread CPU
```

```
┌────────────────────────────────────────────────────────────────────┐
│ Scaling with Queue Depth (io_uring + fixed bufs, NVMe)            │
├──────────────────┬──────────┬──────────┬──────────────────────────┤
│    QD            │   IOPS   │ Lat(avg) │  Lat(p99)               │
├──────────────────┼──────────┼──────────┼──────────────────────────┤
│    1             │   80K    │  12.5 us │   25 us                 │
│    4             │  250K    │  16.0 us │   35 us                 │
│    16            │  380K    │  42.0 us │   85 us                 │
│    64            │  430K    │ 148.0 us │  250 us                 │
│    128           │  450K    │ 284.0 us │  520 us                 │
│    256           │  455K    │ 562.0 us │  980 us                 │
└──────────────────┴──────────┴──────────┴──────────────────────────┘
Note: IOPS plateaus while latency grows at high QDs
```

### 13.3 Custom Microbenchmark

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define QUEUE_DEPTH  128
#define BLOCK_SIZE   4096
#define NUM_OPS      100000

static inline uint64_t gettime_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

int main(int argc, char *argv[])
{
    struct io_uring ring;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    void *buf;
    int fd, ret;

    if (argc < 2) {
        fprintf(stderr, "Usage: %s <device-or-file>\n", argv[0]);
        return 1;
    }

    fd = open(argv[1], O_RDONLY | O_DIRECT);
    if (fd < 0) { perror("open"); return 1; }

    posix_memalign(&buf, BLOCK_SIZE, BLOCK_SIZE);

    /* Setup io_uring */
    ret = io_uring_queue_init(QUEUE_DEPTH, &ring, 0);
    if (ret < 0) { fprintf(stderr, "init: %s\n", strerror(-ret)); return 1; }

    /* Register buffer and file */
    struct iovec iov = { .iov_base = buf, .iov_len = BLOCK_SIZE };
    io_uring_register_buffers(&ring, &iov, 1);
    io_uring_register_files(&ring, &fd, 1);

    /* Warmup */
    for (int i = 0; i < QUEUE_DEPTH; i++) {
        sqe = io_uring_get_sqe(&ring);
        io_uring_prep_read_fixed(sqe, 0, buf, BLOCK_SIZE, 0, 0);
        sqe->flags |= IOSQE_FIXED_FILE;
    }
    io_uring_submit_and_wait(&ring, QUEUE_DEPTH);
    for (int i = 0; i < QUEUE_DEPTH; i++) {
        io_uring_wait_cqe(&ring, &cqe);
        io_uring_cqe_seen(&ring, cqe);
    }

    /* Benchmark */
    uint64_t start = gettime_ns();
    unsigned inflight = 0;
    unsigned completed = 0;

    /* Fill pipeline */
    for (int i = 0; i < QUEUE_DEPTH && i < NUM_OPS; i++) {
        sqe = io_uring_get_sqe(&ring);
        io_uring_prep_read_fixed(sqe, 0, buf, BLOCK_SIZE,
                                  (rand() % 1000000) * BLOCK_SIZE, 0);
        sqe->flags |= IOSQE_FIXED_FILE;
        inflight++;
    }
    io_uring_submit(&ring);

    while (completed < NUM_OPS) {
        io_uring_wait_cqe(&ring, &cqe);

        if (cqe->res < 0) {
            fprintf(stderr, "I/O error: %s\n", strerror(-cqe->res));
        }

        io_uring_cqe_seen(&ring, cqe);
        completed++;
        inflight--;

        /* Refill */
        if (completed + inflight < NUM_OPS) {
            sqe = io_uring_get_sqe(&ring);
            io_uring_prep_read_fixed(sqe, 0, buf, BLOCK_SIZE,
                                      (rand() % 1000000) * BLOCK_SIZE, 0);
            sqe->flags |= IOSQE_FIXED_FILE;
            inflight++;
            io_uring_submit(&ring);
        }
    }

    uint64_t elapsed_ns = gettime_ns() - start;
    double elapsed_s = elapsed_ns / 1e9;
    double iops = NUM_OPS / elapsed_s;
    double bw_mb = (iops * BLOCK_SIZE) / (1024.0 * 1024.0);
    double lat_us = (elapsed_ns / (double)NUM_OPS) / 1000.0;

    printf("Results:\n");
    printf("  Operations:  %d\n", NUM_OPS);
    printf("  Elapsed:     %.2f s\n", elapsed_s);
    printf("  IOPS:        %.0f\n", iops);
    printf("  Bandwidth:   %.1f MB/s\n", bw_mb);
    printf("  Avg Latency: %.1f us\n", lat_us);

    io_uring_unregister_files(&ring);
    io_uring_unregister_buffers(&ring);
    io_uring_queue_exit(&ring);
    free(buf);
    close(fd);
    return 0;
}
```

### 13.4 Profiling Checklist

```
┌─────────────────────────────────────────────────────────────────────┐
│              io_uring Performance Profiling Checklist                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  1. Submission Efficiency                                           │
│     □ Measure submissions per io_uring_enter() syscall              │
│     □ Check batch size (target: >10 SQEs per submit)               │
│     □ Verify SQPOLL thread utilization if enabled                   │
│                                                                      │
│  2. Completion Processing                                           │
│     □ Check CQ overflow counter (should be 0)                      │
│     □ Measure completion processing latency                         │
│     □ Verify CQ reaping frequency                                   │
│                                                                      │
│  3. Async vs Inline Execution                                       │
│     □ Track io_uring:io_uring_queue_async_work events              │
│     □ High async rate = operations can't complete inline            │
│     □ For file I/O: check if page cache is warm                    │
│                                                                      │
│  4. Memory Efficiency                                               │
│     □ Fixed buffers registered? (avoid per-I/O pinning)            │
│     □ Fixed files registered? (avoid fdtable lookup)               │
│     □ Buffer alignment correct for O_DIRECT?                       │
│     □ TLB miss rate acceptable? (consider huge pages)              │
│                                                                      │
│  5. Queue Depth Tuning                                              │
│     □ Monitor actual queue utilization vs configured depth          │
│     □ SQ full events (dropped submissions)                         │
│     □ Balance between throughput and latency                        │
│                                                                      │
│  6. System-Level Checks                                             │
│     □ CPU frequency scaling disabled for benchmarks                │
│     □ IRQ affinity aligned with application CPUs                   │
│     □ NUMA locality verified                                        │
│     □ Kernel version supports required features                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 14. Code Examples

### 14.1 Async File Copy with io_uring

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define QUEUE_DEPTH 64
#define BLOCK_SIZE  (128 * 1024)  /* 128KB blocks */

struct copy_ctx {
    int src_fd;
    int dst_fd;
    void *buf;
    off_t offset;
    int buf_idx;
    enum { COPY_READ, COPY_WRITE } state;
};

int main(int argc, char *argv[])
{
    struct io_uring ring;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    int src_fd, dst_fd, ret;
    off_t file_size, offset = 0;
    unsigned inflight = 0;

    if (argc != 3) {
        fprintf(stderr, "Usage: %s <src> <dst>\n", argv[0]);
        return 1;
    }

    src_fd = open(argv[1], O_RDONLY);
    dst_fd = open(argv[2], O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (src_fd < 0 || dst_fd < 0) { perror("open"); return 1; }

    file_size = lseek(src_fd, 0, SEEK_END);
    lseek(src_fd, 0, SEEK_SET);

    io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

    /* Allocate copy contexts */
    struct copy_ctx *ctxs = calloc(QUEUE_DEPTH, sizeof(struct copy_ctx));
    for (int i = 0; i < QUEUE_DEPTH; i++) {
        ctxs[i].src_fd = src_fd;
        ctxs[i].dst_fd = dst_fd;
        ctxs[i].buf = aligned_alloc(4096, BLOCK_SIZE);
        ctxs[i].buf_idx = i;
    }

    /* Submit initial reads */
    for (int i = 0; i < QUEUE_DEPTH && offset < file_size; i++) {
        ctxs[i].offset = offset;
        ctxs[i].state = COPY_READ;

        sqe = io_uring_get_sqe(&ring);
        size_t to_read = (file_size - offset < BLOCK_SIZE) ?
                          file_size - offset : BLOCK_SIZE;
        io_uring_prep_read(sqe, src_fd, ctxs[i].buf, to_read, offset);
        io_uring_sqe_set_data(sqe, &ctxs[i]);

        offset += to_read;
        inflight++;
    }
    io_uring_submit(&ring);

    /* Process read/write pipeline */
    size_t total_copied = 0;
    while (inflight > 0) {
        ret = io_uring_wait_cqe(&ring, &cqe);
        if (ret < 0) { fprintf(stderr, "wait: %s\n", strerror(-ret)); break; }

        struct copy_ctx *ctx = io_uring_cqe_get_data(cqe);

        if (cqe->res < 0) {
            fprintf(stderr, "I/O error: %s\n", strerror(-cqe->res));
            io_uring_cqe_seen(&ring, cqe);
            inflight--;
            continue;
        }

        if (ctx->state == COPY_READ) {
            /* Read complete -> submit write */
            ctx->state = COPY_WRITE;
            sqe = io_uring_get_sqe(&ring);
            io_uring_prep_write(sqe, dst_fd, ctx->buf, cqe->res, ctx->offset);
            io_uring_sqe_set_data(sqe, ctx);
            io_uring_submit(&ring);
        } else {
            /* Write complete -> submit next read or finish */
            total_copied += cqe->res;
            inflight--;

            if (offset < file_size) {
                ctx->offset = offset;
                ctx->state = COPY_READ;

                sqe = io_uring_get_sqe(&ring);
                size_t to_read = (file_size - offset < BLOCK_SIZE) ?
                                  file_size - offset : BLOCK_SIZE;
                io_uring_prep_read(sqe, src_fd, ctx->buf, to_read, offset);
                io_uring_sqe_set_data(sqe, ctx);

                offset += to_read;
                inflight++;
                io_uring_submit(&ring);
            }
        }

        io_uring_cqe_seen(&ring, cqe);
    }

    printf("Copied %zu bytes\n", total_copied);

    for (int i = 0; i < QUEUE_DEPTH; i++) free(ctxs[i].buf);
    free(ctxs);
    io_uring_queue_exit(&ring);
    close(src_fd);
    close(dst_fd);
    return 0;
}
```

### 14.2 Multishot TCP Echo Server with Buffer Rings

```c
#include <liburing.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define QUEUE_DEPTH    256
#define NUM_BUFFERS    128
#define BUFFER_SIZE    4096
#define BUFFER_GROUP   1
#define MAX_CONNS      1024

enum {
    OP_ACCEPT,
    OP_RECV,
    OP_SEND,
};

struct conn_info {
    int fd;
    int op;
};

struct io_uring ring;
struct io_uring_buf_ring *buf_ring;
char *buffers[NUM_BUFFERS];

void setup_buffer_ring(void)
{
    struct io_uring_buf_reg reg = {};
    int ret;

    /* Allocate buffer ring */
    if (posix_memalign((void **)&buf_ring,
                        sysconf(_SC_PAGESIZE),
                        NUM_BUFFERS * sizeof(struct io_uring_buf) +
                        sizeof(struct io_uring_buf_ring))) {
        perror("posix_memalign");
        exit(1);
    }

    io_uring_buf_ring_init(buf_ring);

    reg.ring_addr = (unsigned long)buf_ring;
    reg.ring_entries = NUM_BUFFERS;
    reg.bgid = BUFFER_GROUP;

    ret = io_uring_register_buf_ring(&ring, &reg, 0);
    if (ret) {
        fprintf(stderr, "register_buf_ring: %s\n", strerror(-ret));
        exit(1);
    }

    /* Add buffers */
    for (int i = 0; i < NUM_BUFFERS; i++) {
        buffers[i] = malloc(BUFFER_SIZE);
        io_uring_buf_ring_add(buf_ring, buffers[i], BUFFER_SIZE, i,
                               io_uring_buf_ring_mask(NUM_BUFFERS), i);
    }
    io_uring_buf_ring_advance(buf_ring, NUM_BUFFERS);
}

void add_multishot_accept(int listen_fd)
{
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    struct conn_info *info = malloc(sizeof(*info));
    info->fd = listen_fd;
    info->op = OP_ACCEPT;

    io_uring_prep_multishot_accept(sqe, listen_fd, NULL, NULL, 0);
    io_uring_sqe_set_data(sqe, info);
}

void add_recv(int fd)
{
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    struct conn_info *info = malloc(sizeof(*info));
    info->fd = fd;
    info->op = OP_RECV;

    io_uring_prep_recv(sqe, fd, NULL, BUFFER_SIZE, 0);
    sqe->flags |= IOSQE_BUFFER_SELECT;
    sqe->buf_group = BUFFER_GROUP;
    io_uring_sqe_set_data(sqe, info);
}

void add_send(int fd, char *data, int len)
{
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    struct conn_info *info = malloc(sizeof(*info));
    info->fd = fd;
    info->op = OP_SEND;

    io_uring_prep_send(sqe, fd, data, len, 0);
    io_uring_sqe_set_data(sqe, info);
}

int main(void)
{
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_addr.s_addr = INADDR_ANY,
        .sin_port = htons(8080),
    };
    int listen_fd, opt = 1;

    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr));
    listen(listen_fd, SOMAXCONN);

    io_uring_queue_init(QUEUE_DEPTH, &ring, 0);
    setup_buffer_ring();

    add_multishot_accept(listen_fd);
    io_uring_submit(&ring);

    printf("Listening on :8080 (multishot + buffer rings)\n");

    while (1) {
        struct io_uring_cqe *cqe;
        io_uring_wait_cqe(&ring, &cqe);

        struct conn_info *info = io_uring_cqe_get_data(cqe);

        switch (info->op) {
        case OP_ACCEPT:
            if (cqe->res >= 0) {
                add_recv(cqe->res);
            }
            /* Multishot: don't free info, don't resubmit */
            if (!(cqe->flags & IORING_CQE_F_MORE)) {
                /* Multishot terminated, rearm */
                add_multishot_accept(info->fd);
                free(info);
            }
            break;

        case OP_RECV:
            if (cqe->res > 0 && (cqe->flags & IORING_CQE_F_BUFFER)) {
                int buf_id = cqe->flags >> IORING_CQE_BUFFER_SHIFT;
                int len = cqe->res;

                /* Echo back */
                add_send(info->fd, buffers[buf_id], len);

                /* Return buffer to ring */
                io_uring_buf_ring_add(buf_ring, buffers[buf_id],
                                       BUFFER_SIZE, buf_id,
                                       io_uring_buf_ring_mask(NUM_BUFFERS), 0);
                io_uring_buf_ring_advance(buf_ring, 1);
            } else {
                close(info->fd);
            }
            free(info);
            break;

        case OP_SEND:
            /* Send complete, read more */
            if (cqe->res >= 0) {
                add_recv(info->fd);
            } else {
                close(info->fd);
            }
            free(info);
            break;
        }

        io_uring_cqe_seen(&ring, cqe);
        io_uring_submit(&ring);
    }

    return 0;
}
```

### 14.3 Scatter-Gather I/O with Vectored Operations

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define QUEUE_DEPTH 32

/*
 * Scatter-Gather Pattern:
 *
 * ┌────────┐  ┌────────┐  ┌────────┐
 * │ Header │  │  Data  │  │ Footer │
 * │  buf   │  │  buf   │  │  buf   │
 * └───┬────┘  └───┬────┘  └───┬────┘
 *     │           │           │
 *     └─────┬─────┘───────────┘
 *           │
 *     ┌─────▼──────────────────┐
 *     │    Single writev()     │
 *     │    to io_uring         │
 *     └────────────────────────┘
 */

int main(void)
{
    struct io_uring ring;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    int fd;

    io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

    fd = open("output.bin", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { perror("open"); return 1; }

    /* Prepare scatter-gather buffers */
    char header[] = "MAGIC_HEADER_V2\0";
    char data[4096];
    memset(data, 0xAB, sizeof(data));
    char footer[] = "END_OF_RECORD\0";

    struct iovec iovs[3] = {
        { .iov_base = header, .iov_len = sizeof(header) },
        { .iov_base = data,   .iov_len = sizeof(data)   },
        { .iov_base = footer, .iov_len = sizeof(footer)  },
    };

    /* Submit vectored write */
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_writev(sqe, fd, iovs, 3, 0);
    sqe->user_data = 1;

    io_uring_submit(&ring);
    io_uring_wait_cqe(&ring, &cqe);

    if (cqe->res < 0) {
        fprintf(stderr, "writev failed: %s\n", strerror(-cqe->res));
    } else {
        printf("Wrote %d bytes (header + data + footer)\n", cqe->res);
    }

    io_uring_cqe_seen(&ring, cqe);

    /* Now scatter-read it back */
    char read_header[sizeof(header)] = {};
    char read_data[sizeof(data)] = {};
    char read_footer[sizeof(footer)] = {};

    struct iovec read_iovs[3] = {
        { .iov_base = read_header, .iov_len = sizeof(read_header) },
        { .iov_base = read_data,   .iov_len = sizeof(read_data)   },
        { .iov_base = read_footer, .iov_len = sizeof(read_footer)  },
    };

    close(fd);
    fd = open("output.bin", O_RDONLY);

    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_readv(sqe, fd, read_iovs, 3, 0);
    sqe->user_data = 2;

    io_uring_submit(&ring);
    io_uring_wait_cqe(&ring, &cqe);

    if (cqe->res > 0) {
        printf("Read %d bytes: header='%s', footer='%s'\n",
               cqe->res, read_header, read_footer);
    }

    io_uring_cqe_seen(&ring, cqe);
    io_uring_queue_exit(&ring);
    close(fd);
    return 0;
}
```

### 14.4 Event-Driven State Machine Pattern

```c
/*
 * Production-quality event loop pattern using io_uring.
 * Each connection follows a state machine:
 *
 * ┌─────────┐    ┌──────────┐    ┌───────────┐    ┌─────────┐
 * │ ACCEPT  │───►│  READ    │───►│  PROCESS  │───►│  WRITE  │
 * └─────────┘    └──────────┘    └───────────┘    └────┬────┘
 *                     ▲                                 │
 *                     └─────────────────────────────────┘
 *                              (keep-alive)
 */

#include <liburing.h>
#include <netinet/in.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define QUEUE_DEPTH 512
#define MAX_MSG_LEN 2048

typedef enum {
    CONN_STATE_ACCEPTING,
    CONN_STATE_READING,
    CONN_STATE_WRITING,
    CONN_STATE_CLOSING,
} conn_state_t;

typedef struct connection {
    int                 fd;
    conn_state_t        state;
    char                buf[MAX_MSG_LEN];
    int                 buf_len;
    struct connection   *next_free;     /* Free list linkage */
} connection_t;

/* Connection pool */
#define MAX_CONNECTIONS 4096
static connection_t conn_pool[MAX_CONNECTIONS];
static connection_t *free_list = NULL;

static void conn_pool_init(void)
{
    for (int i = 0; i < MAX_CONNECTIONS - 1; i++) {
        conn_pool[i].next_free = &conn_pool[i + 1];
        conn_pool[i].fd = -1;
    }
    conn_pool[MAX_CONNECTIONS - 1].next_free = NULL;
    free_list = &conn_pool[0];
}

static connection_t *conn_alloc(void)
{
    if (!free_list) return NULL;
    connection_t *c = free_list;
    free_list = c->next_free;
    c->next_free = NULL;
    return c;
}

static void conn_free(connection_t *c)
{
    if (c->fd >= 0) close(c->fd);
    c->fd = -1;
    c->next_free = free_list;
    free_list = c;
}

static void submit_accept(struct io_uring *ring, int listen_fd)
{
    connection_t *c = conn_alloc();
    if (!c) return;

    c->state = CONN_STATE_ACCEPTING;
    c->fd = listen_fd;

    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    io_uring_prep_accept(sqe, listen_fd, NULL, NULL, 0);
    io_uring_sqe_set_data(sqe, c);
}

static void submit_read(struct io_uring *ring, connection_t *c)
{
    c->state = CONN_STATE_READING;

    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    io_uring_prep_recv(sqe, c->fd, c->buf, MAX_MSG_LEN, 0);
    io_uring_sqe_set_data(sqe, c);
}

static void submit_write(struct io_uring *ring, connection_t *c)
{
    c->state = CONN_STATE_WRITING;

    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    io_uring_prep_send(sqe, c->fd, c->buf, c->buf_len, 0);
    io_uring_sqe_set_data(sqe, c);
}

static void process_request(connection_t *c, int bytes_read)
{
    /* Simple echo: just set the length for write-back */
    c->buf_len = bytes_read;
}

int main(void)
{
    struct io_uring ring;
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_addr.s_addr = INADDR_ANY,
        .sin_port = htons(8080),
    };
    int listen_fd, opt = 1;

    conn_pool_init();

    listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr));
    listen(listen_fd, SOMAXCONN);

    struct io_uring_params params = {
        .flags = IORING_SETUP_SINGLE_ISSUER |
                 IORING_SETUP_DEFER_TASKRUN,
    };
    io_uring_queue_init_params(QUEUE_DEPTH, &ring, &params);

    /* Pre-submit multiple accept requests */
    for (int i = 0; i < 32; i++) {
        submit_accept(&ring, listen_fd);
    }
    io_uring_submit(&ring);

    printf("Event-driven server on :8080\n");

    while (1) {
        struct io_uring_cqe *cqe;
        unsigned head;
        int count = 0;

        io_uring_submit_and_wait(&ring, 1);

        /* Process all available CQEs */
        io_uring_for_each_cqe(&ring, head, cqe) {
            connection_t *c = io_uring_cqe_get_data(cqe);
            count++;

            switch (c->state) {
            case CONN_STATE_ACCEPTING:
                if (cqe->res >= 0) {
                    /* New connection */
                    connection_t *new_conn = conn_alloc();
                    if (new_conn) {
                        new_conn->fd = cqe->res;
                        submit_read(&ring, new_conn);
                    } else {
                        close(cqe->res);
                    }
                }
                /* Resubmit accept */
                submit_accept(&ring, c->fd);
                conn_free(c);
                c = NULL;
                break;

            case CONN_STATE_READING:
                if (cqe->res <= 0) {
                    conn_free(c);
                } else {
                    process_request(c, cqe->res);
                    submit_write(&ring, c);
                }
                break;

            case CONN_STATE_WRITING:
                if (cqe->res < 0) {
                    conn_free(c);
                } else {
                    /* Keep-alive: read next request */
                    submit_read(&ring, c);
                }
                break;

            default:
                conn_free(c);
                break;
            }
        }

        io_uring_cq_advance(&ring, count);
    }

    return 0;
}
```

---

## 15. Comparison with Traditional I/O

### 15.1 Interface Comparison Matrix

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Linux I/O Interface Comparison                          │
├──────────────┬──────────┬──────────┬──────────┬──────────┬────────────────┤
│   Feature    │  sync    │  epoll   │  libaio  │ POSIX AIO│  io_uring     │
│              │ read/    │          │          │          │               │
│              │ write    │          │          │          │               │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Async I/O    │    No    │ Readiness│   Yes    │   Yes    │     Yes       │
│              │          │  only    │          │          │               │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Buffered I/O │   Yes    │   Yes    │    No    │   Yes*   │     Yes       │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Direct I/O   │   Yes    │   Yes    │   Yes    │   Yes    │     Yes       │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Network I/O  │   Yes    │   Yes    │    No    │    No    │     Yes       │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ File ops     │   Yes    │    No    │    No    │    No    │ Yes (open,    │
│ (open,close) │          │          │          │          │  close,stat)  │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Syscalls/op  │    1     │   1+     │  0-1     │    1     │    0-1        │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Batching     │    No    │ Limited  │   Yes    │    No    │     Yes       │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Zero-copy    │    No    │    No    │    No    │    No    │  Yes (fixed   │
│              │          │          │          │          │   bufs/send)  │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Kernel poll  │    No    │    No    │    No    │    No    │ Yes (SQPOLL)  │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Op chaining  │    No    │    No    │    No    │    No    │ Yes (linked)  │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Multishot    │    No    │    No    │    No    │    No    │     Yes       │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Complexity   │   Low    │  Medium  │  Medium  │   Low    │    High       │
├──────────────┼──────────┼──────────┼──────────┼──────────┼────────────────┤
│ Min kernel   │   Any    │  2.6     │  2.6     │   Any    │    5.1+       │
└──────────────┴──────────┴──────────┴──────────┴──────────┴────────────────┘
* POSIX AIO with glibc uses user-space threads, not true kernel async
```

### 15.2 Code Pattern Comparison

#### Traditional Blocking I/O (Thread-per-Connection)

```c
/* Thread-per-connection server */
void *handle_client(void *arg)
{
    int fd = *(int *)arg;
    char buf[4096];
    int n;

    while ((n = read(fd, buf, sizeof(buf))) > 0) {  /* BLOCKS */
        write(fd, buf, n);                            /* BLOCKS */
    }

    close(fd);
    return NULL;
}

/* Main: 1 syscall per operation, 1 thread per connection */
while (1) {
    int client = accept(listen_fd, ...);      /* BLOCKS */
    pthread_create(&tid, NULL, handle_client, &client);
}

/*
 * Cost model:
 *   - 1 accept() syscall per connection
 *   - 1 read() syscall per receive
 *   - 1 write() syscall per send
 *   - Thread stack: ~8MB per connection (default)
 *   - Context switch per blocking call
 *   - Scales: ~1K-10K connections before thrashing
 */
```

#### epoll Event Loop

```c
/* epoll-based event loop */
int epfd = epoll_create1(0);
struct epoll_event ev, events[MAX_EVENTS];

ev.events = EPOLLIN;
ev.data.fd = listen_fd;
epoll_ctl(epfd, EPOLL_CTL_ADD, listen_fd, &ev);

while (1) {
    int nfds = epoll_wait(epfd, events, MAX_EVENTS, -1);  /* 1 syscall */

    for (int i = 0; i < nfds; i++) {
        if (events[i].data.fd == listen_fd) {
            int client = accept(listen_fd, ...);           /* 1 syscall */
            /* Set non-blocking */
            fcntl(client, F_SETFL, O_NONBLOCK);
            ev.events = EPOLLIN | EPOLLET;
            ev.data.fd = client;
            epoll_ctl(epfd, EPOLL_CTL_ADD, client, &ev);  /* 1 syscall */
        } else {
            int n = read(events[i].data.fd, buf, ...);    /* 1 syscall */
            if (n > 0) {
                write(events[i].data.fd, buf, n);          /* 1 syscall */
            }
        }
    }
}

/*
 * Cost model:
 *   - 1 epoll_wait() per batch (amortized)
 *   - 1 syscall per accept, read, write
 *   - epoll_ctl for state changes
 *   - Single thread handles many connections
 *   - Scales: ~100K+ connections
 *   - But: still 1 syscall per I/O operation
 */
```

#### Linux AIO (libaio)

```c
/* libaio pattern */
io_context_t ctx = 0;
io_setup(QUEUE_DEPTH, &ctx);

struct iocb cb;
struct iocb *cbs[1] = { &cb };

io_prep_pread(&cb, fd, buf, len, offset);
cb.data = user_data;

io_submit(ctx, 1, cbs);                              /* 1 syscall */

struct io_event events[MAX_EVENTS];
int n = io_getevents(ctx, 1, MAX_EVENTS, events, &timeout); /* 1 syscall */

for (int i = 0; i < n; i++) {
    /* Process completion */
    void *data = events[i].data;
    long res = events[i].res;
}

/*
 * Cost model:
 *   - Batched submit (io_submit)
 *   - Batched reap (io_getevents)
 *   - ONLY supports O_DIRECT (no buffered I/O)
 *   - ONLY supports file I/O (no network)
 *   - No zero-copy, no fixed buffers
 *   - No SQPOLL, no linked ops
 */
```

#### io_uring

```c
/* io_uring pattern */
struct io_uring ring;
io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

/* Submit multiple operations without any syscall */
for (int i = 0; i < batch_size; i++) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf[i], len, offset[i]);
    sqe->user_data = i;
    /* No syscall! Just writing to shared memory */
}

/* Single syscall submits all + waits for completions */
io_uring_submit_and_wait(&ring, min_complete);

/* Reap completions - no syscall */
struct io_uring_cqe *cqe;
unsigned head;
io_uring_for_each_cqe(&ring, head, cqe) {
    /* Process completion */
    handle(cqe->user_data, cqe->res);
}
io_uring_cq_advance(&ring, count);

/*
 * Cost model:
 *   - 0-1 syscalls per batch (SQPOLL: 0)
 *   - Supports ALL I/O types (file, network, fs ops)
 *   - Fixed buffers: no per-I/O page pinning
 *   - Fixed files: no per-I/O fd lookup
 *   - Linked operations: atomic chains
 *   - Multishot: 1 submit, many completions
 *   - Scales: ~1M+ operations/sec on modern hardware
 */
```

### 15.3 Syscall Overhead Visualization

```
Operations: 1 million 4KB reads

Synchronous read():
┌──────────────────────────────────────────────────────────────┐
│ 1,000,000 × [user→kernel→read→kernel→user]                  │
│                                                              │
│ Syscalls:    1,000,000                                       │
│ Ctx switches: 1,000,000                                      │
│ CPU cycles:  ~2,000,000,000 (syscall overhead alone)         │
└──────────────────────────────────────────────────────────────┘

epoll + non-blocking read():
┌──────────────────────────────────────────────────────────────┐
│ ~10,000 × epoll_wait + 1,000,000 × read()                   │
│                                                              │
│ Syscalls:    ~1,010,000                                      │
│ Ctx switches: ~10,000                                        │
│ CPU cycles:  ~1,800,000,000                                  │
└──────────────────────────────────────────────────────────────┘

io_uring (batched, no SQPOLL):
┌──────────────────────────────────────────────────────────────┐
│ ~8,000 × io_uring_enter (submit 128 + wait)                  │
│                                                              │
│ Syscalls:    ~8,000                                          │
│ Ctx switches: ~8,000                                         │
│ CPU cycles:  ~200,000,000                                    │
└──────────────────────────────────────────────────────────────┘

io_uring (SQPOLL + fixed bufs + fixed files):
┌──────────────────────────────────────────────────────────────┐
│ ~50 × io_uring_enter (wakeup only)                           │
│                                                              │
│ Syscalls:    ~50                                             │
│ Ctx switches: ~50                                            │
│ CPU cycles:  ~50,000,000                                     │
│ (plus dedicated kernel thread CPU)                           │
└──────────────────────────────────────────────────────────────┘
```

### 15.4 When to Use Each Interface

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Decision Guide                                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Use SYNCHRONOUS I/O when:                                          │
│  ├── Simple CLI tools or scripts                                    │
│  ├── Low-concurrency applications                                   │
│  ├── Sequential I/O patterns                                        │
│  └── Maximum portability required                                   │
│                                                                      │
│  Use EPOLL when:                                                    │
│  ├── Many concurrent network connections (C10K+)                    │
│  ├── Event-driven servers (HTTP, WebSocket)                         │
│  ├── Portability across older kernels (2.6+)                        │
│  ├── Readiness notification is sufficient                           │
│  └── Established ecosystem (nginx, Node.js, etc.)                  │
│                                                                      │
│  Use LIBAIO when:                                                   │
│  ├── Direct I/O to block devices                                    │
│  ├── Database engines with O_DIRECT                                 │
│  ├── Legacy codebases already using libaio                          │
│  └── Kernel < 5.1 but need async file I/O                          │
│                                                                      │
│  Use IO_URING when:                                                 │
│  ├── Maximum I/O performance is critical                            │
│  ├── High-throughput storage (NVMe, Optane)                         │
│  ├── Mixed file + network workloads                                 │
│  ├── Syscall overhead is measurable bottleneck                      │
│  ├── Need linked/chained operations                                 │
│  ├── Need zero-copy or kernel-side polling                          │
│  ├── Building new high-performance infrastructure                   │
│  └── Kernel 5.1+ is guaranteed                                     │
│                                                                      │
│  Migration priority:                                                │
│  1. libaio → io_uring (direct replacement, superset)                │
│  2. thread-per-conn → io_uring (massive scaling improvement)        │
│  3. epoll → io_uring (incremental gains, higher complexity)         │
└─────────────────────────────────────────────────────────────────────┘
```

### 15.5 Real-World Adoption

```
┌─────────────────────────────────────────────────────────────────────┐
│                 io_uring Adoption in Major Projects                  │
├──────────────────┬──────────────────────────────────────────────────┤
│ Project          │ How io_uring is Used                             │
├──────────────────┼──────────────────────────────────────────────────┤
│ RocksDB          │ MultiRead for SST file reads (async)            │
│ ScyllaDB         │ Primary I/O engine (replaced libaio)            │
│ QEMU/KVM         │ Virtio-blk backend using io_uring               │
│ libvirt          │ Disk I/O for virtual machines                    │
│ PostgreSQL       │ AIO subsystem (16+ with io_uring)               │
│ Ceph             │ BlueStore async I/O backend                      │
│ fio              │ ioengine=io_uring (benchmarking standard)        │
│ Tokio (Rust)     │ tokio-uring crate for async runtime             │
│ Seastar (C++)    │ io_uring reactor (ScyllaDB, Redpanda)           │
│ io_uring_echo    │ Reference echo server (~10M msg/sec)            │
│ Photon (C++)     │ Coroutine-based io_uring integration            │
│ Tigerbeetle      │ Primary I/O engine for deterministic DB         │
│ Dragonfly        │ Redis-compatible using io_uring for net I/O     │
│ Glommio (Rust)   │ Thread-per-core framework built on io_uring     │
└──────────────────┴──────────────────────────────────────────────────┘
```

### 15.6 Latency Comparison Under Load

```
Request latency distribution at 200K IOPS (4KB random reads, NVMe):

sync read():       [Not achievable at this IOPS with single thread]

libaio (QD=128):
  p50:   280 us  ████████████████████
  p99:   890 us  ████████████████████████████████████████████████████████████
  p999: 2100 us  ████████████████████████████████████████████████████████████████████

io_uring (QD=128):
  p50:   250 us  ██████████████████
  p99:   650 us  █████████████████████████████████████████████████
  p999: 1200 us  ████████████████████████████████████████████████████████████████

io_uring + fixed bufs + files (QD=128):
  p50:   220 us  ████████████████
  p99:   480 us  ███████████████████████████████████████
  p999:  850 us  ██████████████████████████████████████████████████████████

io_uring + SQPOLL + fixed (QD=128):
  p50:   200 us  ██████████████
  p99:   420 us  █████████████████████████████████████
  p999:  720 us  ████████████████████████████████████████████████████████

Key insight: io_uring reduces tail latency significantly by
eliminating per-I/O syscall and memory management overhead.
```

---

# io_uring: A Deep Dive

## Table of Contents

1. [Introduction](#introduction)
2. [Architecture Overview](#architecture-overview)
3. [Core Data Structures](#core-data-structures)
4. [Performance Characteristics](#performance-characteristics)
5. [Reliability Features](#reliability-features)
6. [Security Concerns](#security-concerns)
7. [Kernel Implementation Details](#kernel-implementation-details)
8. [Practical Examples](#practical-examples)

---

## Introduction

**io_uring** is a Linux kernel interface for asynchronous I/O, introduced in kernel 5.1 (2019) by Jens Axboe. It was designed to address the limitations of existing async I/O mechanisms:

| Mechanism          | Limitations                                      |
| ------------------ | ------------------------------------------------ |
| POSIX AIO          | User-space threads, limited operations           |
| Linux AIO (libaio) | Only O_DIRECT, no buffered I/O, limited syscalls |
| epoll              | Still requires syscalls per I/O operation        |

**io_uring's Goal**: Achieve true asynchronous I/O with minimal syscall overhead through shared memory ring buffers.

---

## Architecture Overview

### High-Level Design

```

┌─────────────────────────────────────────────────────────────────┐
│ USER SPACE │
├─────────────────────────────────────────────────────────────────┤
│ │
│ ┌──────────────────┐ ┌──────────────────┐ │
│ │ Submission Queue │ │ Completion Queue │ │
│ │ (SQ) │ │ (CQ) │ │
│ │ │ │ │ │
│ │ ┌─────────────┐ │ │ ┌────────────┐ │ │
│ │ │ SQE │ SQE │ │ │ │ CQE │ CQE │ │ │
│ │ │ SQE │ SQE │ │ │ │ CQE │ CQE │ │ │
│ │ └─────────────┘ │ │ └────────────┘ │ │
│ └────────┬─────────┘ └────────▲─────────┘ │
│ │ │ │
├────────────┼──────────────────────────────────┼──────────────────┤
│ │ KERNEL SPACE │ │
│ ▼ │ │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ io_uring Core │ │
│ │ ┌──────────┐ ┌──────────┐ ┌──────────┐ │ │
│ │ │ SQ Thread│ │ Workers │ │Completion│ │ │
│ │ │ (SQPOLL) │ │ Pool │ │ Handler │ │ │
│ │ └──────────┘ └──────────┘ └──────────┘ │ │
│ └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘

```

### The Ring Buffer Concept

```

Submission Queue (SQ) Completion Queue (CQ)
Ring Buffer Ring Buffer

    ┌───────────┐                        ┌───────────┐
    │   Head    │◄── Kernel reads        │   Head    │◄── User reads
    ├───────────┤                        ├───────────┤
    │  Entry 0  │                        │  Entry 0  │
    ├───────────┤                        ├───────────┤
    │  Entry 1  │                        │  Entry 1  │
    ├───────────┤                        ├───────────┤
    │    ...    │                        │    ...    │
    ├───────────┤                        ├───────────┤
    │  Entry N  │                        │  Entry N  │
    ├───────────┤                        ├───────────┤
    │   Tail    │◄── User writes         │   Tail    │◄── Kernel writes
    └───────────┘                        └───────────┘

```

---

## Core Data Structures

### Submission Queue Entry (SQE)

```c
struct io_uring_sqe {
    __u8    opcode;         /* Operation code (read, write, etc.) */
    __u8    flags;          /* IOSQE_ flags */
    __u16   ioprio;         /* I/O priority */
    __s32   fd;             /* File descriptor */
    union {
        __u64   off;        /* Offset into file */
        __u64   addr2;      /* Secondary address */
    };
    union {
        __u64   addr;       /* Buffer address or pointer */
        __u64   splice_off_in;
    };
    __u32   len;            /* Buffer length or count */
    union {
        __kernel_rwf_t  rw_flags;
        __u32   fsync_flags;
        __u16   poll_events;
        __u32   sync_range_flags;
        __u32   msg_flags;
        __u32   timeout_flags;
        __u32   accept_flags;
        __u32   cancel_flags;
        __u32   open_flags;
        __u32   statx_flags;
        __u32   fadvise_advice;
        __u32   splice_flags;
    };
    __u64   user_data;      /* User data passed back in CQE */
    union {
        __u16   buf_index;  /* Index into fixed buffers */
        __u16   buf_group;  /* Buffer group ID */
    };
    __u16   personality;    /* Credentials personality */
    union {
        __s32   splice_fd_in;
        __u32   file_index;
    };
    __u64   __pad2[2];
};
```

### Completion Queue Entry (CQE)

```c
struct io_uring_cqe {
    __u64   user_data;      /* Matches sqe->user_data */
    __s32   res;            /* Result code (like syscall return) */
    __u32   flags;          /* IORING_CQE_F_ flags */

    /* Extended CQE (if IORING_SETUP_CQE32) */
    __u64   big_cqe[];      /* Additional 16 bytes */
};
```

### Ring Structure Layout

```c
// Memory layout of io_uring rings
struct io_rings {
    struct io_uring_sqe *sq_sqes;    // SQE array
    struct io_uring_cqe *cqes;       // CQE array

    // SQ ring pointers
    unsigned *sq_head;               // Kernel-updated
    unsigned *sq_tail;               // User-updated
    unsigned *sq_ring_mask;
    unsigned *sq_ring_entries;
    unsigned *sq_flags;
    unsigned *sq_dropped;
    unsigned *sq_array;              // Indirection array

    // CQ ring pointers
    unsigned *cq_head;               // User-updated
    unsigned *cq_tail;               // Kernel-updated
    unsigned *cq_ring_mask;
    unsigned *cq_ring_entries;
    unsigned *cq_overflow;
    struct io_uring_cqe *cqes;
};
```

---

## Performance Characteristics

### Syscall Overhead Comparison

```
Traditional I/O (per operation):
┌─────────────────────────────────────────────────────────────┐
│ User Space │ ──syscall──► │ Kernel │ ──syscall return──► │ │
│            │              │        │                      │ │
│ ~1000-2000 CPU cycles per syscall                         │ │
└─────────────────────────────────────────────────────────────┘

io_uring (batched):
┌─────────────────────────────────────────────────────────────┐
│ User Space: Write N SQEs to ring (no syscall)              │
│ Single io_uring_enter() or SQPOLL (zero syscalls)          │
│ Read N CQEs from ring (no syscall)                         │
│                                                            │
│ Amortized cost: ~100-200 cycles per operation              │
└─────────────────────────────────────────────────────────────┘
```

### Performance Features

#### 1. **Zero-Copy Registration**

```c
// Register fixed buffers - eliminates per-I/O mapping
struct iovec iovs[N_BUFFERS];
// ... initialize iovs ...

io_uring_register(ring_fd, IORING_REGISTER_BUFFERS, iovs, N_BUFFERS);

// Now use IORING_OP_READ_FIXED / IORING_OP_WRITE_FIXED
// Kernel keeps pages pinned, no per-I/O get_user_pages()
```

```
Without Fixed Buffers:          With Fixed Buffers:
┌──────────────────────┐        ┌──────────────────────┐
│ Each I/O:            │        │ Registration (once): │
│  - get_user_pages()  │        │  - get_user_pages()  │
│  - pin memory        │        │  - pin memory        │
│  - DMA mapping       │        │  - DMA mapping       │
│  - unpin             │        │                      │
│  - put_user_pages()  │        │ Each I/O:            │
│                      │        │  - Use pre-mapped    │
│ Cost: HIGH           │        │                      │
└──────────────────────┘        │ Cost: MINIMAL        │
                                └──────────────────────┘
```

#### 2. **Fixed File Descriptors**

```c
// Register files to avoid per-I/O fd lookup
int fds[N_FILES] = { fd1, fd2, fd3, ... };
io_uring_register(ring_fd, IORING_REGISTER_FILES, fds, N_FILES);

// Use IOSQE_FIXED_FILE flag with file_index instead of fd
sqe->flags |= IOSQE_FIXED_FILE;
sqe->fd = file_index;  // Index into registered array
```

#### 3. **SQPOLL Mode (Kernel Polling)**

```c
struct io_uring_params params = {
    .flags = IORING_SETUP_SQPOLL,
    .sq_thread_idle = 2000,  // ms before thread sleeps
    .sq_thread_cpu = 0,      // Pin to CPU 0
};

// Kernel thread polls SQ - zero syscalls for submission!
```

```
Without SQPOLL:                  With SQPOLL:
┌───────────────────────┐       ┌───────────────────────┐
│ User: Write SQE       │       │ User: Write SQE       │
│ User: io_uring_enter()│       │   (kernel thread      │
│ Kernel: Process SQE   │       │    picks it up)       │
│ Kernel: Return        │       │                       │
│                       │       │ Zero syscalls!        │
└───────────────────────┘       └───────────────────────┘
```

### Benchmark Comparison

```
Operation: 4KB Random Reads (NVMe SSD)
Threads: 1, Queue Depth: 32

┌────────────────┬────────────┬────────────┬────────────┐
│   Interface    │   IOPS     │  Latency   │ CPU Usage  │
├────────────────┼────────────┼────────────┼────────────┤
│ sync read()    │   80,000   │   400 µs   │   100%     │
│ libaio         │  350,000   │    90 µs   │    85%     │
│ io_uring       │  400,000   │    80 µs   │    70%     │
│ io_uring+fixed │  450,000   │    70 µs   │    55%     │
│ io_uring+SQPOLL│  500,000   │    65 µs   │    45%*    │
└────────────────┴────────────┴────────────┴────────────┘
* Plus dedicated kernel thread CPU usage
```

---

## Reliability Features

### 1. **Linked Operations**

```c
// Create a chain of dependent operations
// Op2 only executes if Op1 succeeds

sqe1 = io_uring_get_sqe(ring);
io_uring_prep_read(sqe1, fd, buf1, len, 0);
sqe1->flags |= IOSQE_IO_LINK;  // Link to next

sqe2 = io_uring_get_sqe(ring);
io_uring_prep_write(sqe2, fd, buf2, len, 0);
sqe2->flags |= IOSQE_IO_LINK;

sqe3 = io_uring_get_sqe(ring);
io_uring_prep_fsync(sqe3, fd, 0);
// Last in chain, no link flag
```

```
Linked Operations Flow:
┌─────────┐     ┌─────────┐     ┌─────────┐
│  READ   │────►│  WRITE  │────►│  FSYNC  │
│ (Link)  │     │ (Link)  │     │ (End)   │
└─────────┘     └─────────┘     └─────────┘
     │               │               │
     ▼               ▼               ▼
  Success ──► Execute Next    Execute Next
     │
  Failure ──► Cancel Chain (skip remaining)
```

### 2. **Timeout Operations**

```c
// Add timeout to any operation
struct __kernel_timespec ts = {
    .tv_sec = 5,
    .tv_nsec = 0,
};

// Timeout linked to previous operation
sqe = io_uring_get_sqe(ring);
io_uring_prep_timeout(sqe, &ts, 0, 0);
sqe->flags |= IOSQE_IO_LINK;

// Or standalone timeout
sqe = io_uring_get_sqe(ring);
io_uring_prep_link_timeout(sqe, &ts, 0);
```

### 3. **Cancellation**

```c
// Cancel a pending operation by user_data
sqe = io_uring_get_sqe(ring);
io_uring_prep_cancel(sqe, user_data_to_cancel, 0);

// Cancel all operations on a file descriptor
sqe = io_uring_get_sqe(ring);
io_uring_prep_cancel_fd(sqe, fd, IORING_ASYNC_CANCEL_FD);
```

### 4. **Ordered Execution (IOSQE_IO_DRAIN)**

```c
// Ensure all previous ops complete before this one
sqe = io_uring_get_sqe(ring);
io_uring_prep_write(sqe, fd, buf, len, off);
sqe->flags |= IOSQE_IO_DRAIN;

// This creates a barrier in the submission queue
```

```
Without DRAIN:               With DRAIN:
┌─────────────────────┐     ┌─────────────────────┐
│ Op1 ─┬─► Complete   │     │ Op1 ─┬─► Complete   │
│ Op2 ─┼─► Complete   │     │ Op2 ─┤              │
│ Op3 ─┼─► Complete   │     │      ▼              │
│ Op4 ─┘              │     │ Op3[DRAIN] ──►      │
│ (Any order)         │     │      │              │
└─────────────────────┘     │ Op4 ─┘ (after Op3)  │
                            └─────────────────────┘
```

### 5. **CQ Overflow Handling**

```c
// Check for overflow
if (*cq_overflow > 0) {
    // CQ was full, entries were dropped or backed up
    // Need to reap completions and/or increase CQ size
}

// Use IORING_SETUP_CQSIZE for custom CQ size
struct io_uring_params p = {
    .flags = IORING_SETUP_CQSIZE,
    .cq_entries = 4096,  // CQ can be larger than SQ
};
```

---

## Security Concerns

### 1. **Attack Surface Expansion**

```
io_uring adds significant kernel attack surface:

┌─────────────────────────────────────────────────────────┐
│                  io_uring Attack Surface                │
├─────────────────────────────────────────────────────────┤
│ • 70+ operation types (opcodes)                         │
│ • Complex state machine                                 │
│ • Shared memory with user space                         │
│ • Asynchronous completion handling                      │
│ • Multiple execution contexts (workers, SQPOLL)         │
│ • Reference counting complexity                         │
└─────────────────────────────────────────────────────────┘
```

### 2. **CVE History (Selected)**

| CVE            | Description                     | Severity |
| -------------- | ------------------------------- | -------- |
| CVE-2021-3491  | Buffer over-read in io_uring    | High     |
| CVE-2021-41073 | File table use-after-free       | Critical |
| CVE-2022-1043  | io_uring reference counting bug | High     |
| CVE-2022-29582 | Race condition in io_uring      | High     |
| CVE-2023-2598  | Out-of-bounds write             | Critical |

### 3. **Sandbox Bypass Concerns**

```c
// io_uring can bypass seccomp filters!
// Seccomp filters syscalls, but io_uring operations
// happen in kernel context after setup

/* Example: Blocked by seccomp */
open("/etc/passwd", O_RDONLY);  // BLOCKED

/* But io_uring openat might work */
io_uring_prep_openat(sqe, AT_FDCWD, "/etc/passwd", O_RDONLY, 0);
// May BYPASS seccomp depending on configuration!
```

**Mitigations:**

```c
// Disable io_uring via sysctl
sysctl -w kernel.io_uring_disabled=2  // Disabled for all

// Values:
// 0 = enabled for all
// 1 = disabled for unprivileged users
// 2 = disabled for all
```

### 4. **Container Security**

```
Docker/Kubernetes Considerations:

┌─────────────────────────────────────────────────────────┐
│ Container Runtimes blocking io_uring:                   │
│                                                         │
│ • Docker: Default seccomp profile blocks io_uring_*    │
│   since Docker 20.10.10                                │
│                                                         │
│ • Kubernetes: Depends on container runtime             │
│                                                         │
│ • Google: Disabled io_uring on production systems      │
│   (ChromeOS, Android, GKE)                             │
└─────────────────────────────────────────────────────────┘
```

---

## Kernel Implementation Details

### System Calls

```c
// Three main syscalls

// 1. Setup the io_uring instance
int io_uring_setup(u32 entries, struct io_uring_params *p);

// 2. Submit and/or wait for completions
int io_uring_enter(unsigned int fd, unsigned int to_submit,
                   unsigned int min_complete, unsigned int flags,
                   sigset_t *sig);

// 3. Register resources
int io_uring_register(unsigned int fd, unsigned int opcode,
                      void *arg, unsigned int nr_args);
```

### Kernel Data Structures

```c
// Main io_uring context (simplified)
struct io_ring_ctx {
    struct {
        // Submission handling
        struct io_rings         *rings;
        struct io_uring_sqe     *sq_sqes;
        unsigned                sq_entries;
        unsigned                sq_mask;

        // Submission state
        unsigned                cached_sq_head;
        unsigned                sq_dropped;
    } ____cacheline_aligned_in_smp;

    struct {
        // Completion handling
        unsigned                cq_entries;
        unsigned                cq_mask;

        // Completion state
        unsigned                cached_cq_tail;
        unsigned                cq_overflow;
    } ____cacheline_aligned_in_smp;

    // Worker management
    struct io_wq               *io_wq;

    // Fixed resources
    struct file               **file_table;
    unsigned                   nr_user_files;
    struct io_mapped_ubuf     *user_bufs;
    unsigned                   nr_user_bufs;

    // Polling
    struct io_sq_thread        *sq_thread;

    // Various lists and locks
    struct list_head           defer_list;
    struct list_head           timeout_list;
    spinlock_t                 completion_lock;

    // ... many more fields
};
```

### Request Processing Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    io_uring Request Flow                        │
└─────────────────────────────────────────────────────────────────┘

User Space:
   │
   ▼
┌──────────────────┐
│ 1. Write SQE to  │
│    submission    │
│    ring buffer   │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 2. Update sq_tail│
│    (memory       │
│    barrier)      │
└────────┬─────────┘
         │
         ▼ (syscall or SQPOLL)
═════════════════════════════════════════════════════
         │
Kernel:  ▼
┌──────────────────┐
│ 3. io_uring_enter│
│    or SQPOLL     │
│    thread wakes  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ 4. io_submit_sqes│
│    - Read SQEs   │
│    - Validate    │
│    - Create reqs │
└────────┬─────────┘
         │
         ▼
┌──────────────────────────────────────────────┐
│ 5. io_issue_sqe (per request)                │
│                                              │
│    ┌─────────────────┐                       │
│    │ Can complete    │──Yes──► Inline        │
│    │ inline?         │         completion    │
│    └────────┬────────┘              │        │
│             │ No                     │        │
│             ▼                        │        │
│    ┌─────────────────┐              │        │
│    │ Punt to         │              │        │
│    │ io-wq worker    │              │        │
│    └────────┬────────┘              │        │
│             │                        │        │
│             ▼                        ▼        │
│    ┌─────────────────────────────────────┐   │
│    │    Async completion via callback     │   │
│    └─────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│ 6. io_cqring_add │
│    - Write CQE   │
│    - Update tail │
│    - Wake waiter │
└────────┬─────────┘
         │
═════════▼═══════════════════════════════════════
         │
User:    ▼
┌──────────────────┐
│ 7. Read CQE from │
│    completion    │
│    ring          │
└──────────────────┘
```

### Worker Thread Pool (io-wq)

```c
// io_wq structure (simplified)
struct io_wq {
    struct io_wq_acct   acct[2];      // Bounded and unbounded
    struct task_struct  *manager;      // Manager thread
    struct hlist_head   wq_hash[];    // Work hash table

    // Per-NUMA node worker lists
    struct io_wqe      *wqes[];
};

// Worker types:
// - Bounded: Limited count, for blocking ops (file I/O)
// - Unbounded: Can grow, for non-blocking ops (network)
```

```
io-wq Worker Pool Architecture:

┌─────────────────────────────────────────────────────────────┐
│                        io-wq Manager                         │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                    Worker Pool                       │    │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐             │    │
│  │  │ Worker1 │  │ Worker2 │  │ Worker3 │  ...        │    │
│  │  └────┬────┘  └────┬────┘  └────┬────┘             │    │
│  │       │            │            │                   │    │
│  │       ▼            ▼            ▼                   │    │
│  │  ┌─────────────────────────────────────────────┐   │    │
│  │  │              Work Hash Table                 │   │    │
│  │  │  [hash(fd)] ──► work_item ──► work_item     │   │    │
│  │  └─────────────────────────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Worker scaling:                                             │
│  - Start with min workers                                    │
│  - Scale up under load                                       │
│  - Scale down after idle timeout                             │
└─────────────────────────────────────────────────────────────┘
```

### Memory Mapping

```c
// io_uring_setup returns offsets for mmap
struct io_uring_params params;
int ring_fd = io_uring_setup(entries, &params);

// Map the rings
void *sq_ptr = mmap(0, params.sq_off.array + params.sq_entries * sizeof(__u32),
                    PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE,
                    ring_fd, IORING_OFF_SQ_RING);

void *cq_ptr = mmap(0, params.cq_off.cqes + params.cq_entries * sizeof(struct io_uring_cqe),
                    PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE,
                    ring_fd, IORING_OFF_CQ_RING);

void *sqes = mmap(0, params.sq_entries * sizeof(struct io_uring_sqe),
                  PROT_READ | PROT_WRITE, MAP_SHARED | MAP_POPULATE,
                  ring_fd, IORING_OFF_SQES);
```

```
Memory Layout:

   mmap offset              Size                Content
┌─────────────────┬────────────────────┬─────────────────────┐
│ IORING_OFF_     │                    │                     │
│ SQ_RING (0)     │  sq_ring_size      │  SQ ring header +   │
│                 │                    │  sq_array indices   │
├─────────────────┼────────────────────┼─────────────────────┤
│ IORING_OFF_     │                    │                     │
│ CQ_RING         │  cq_ring_size      │  CQ ring header +   │
│ (0x8000000)     │                    │  CQE array          │
├─────────────────┼────────────────────┼─────────────────────┤
│ IORING_OFF_     │                    │                     │
│ SQES            │  sq_entries *      │  SQE array          │
│ (0x10000000)    │  sizeof(sqe)       │                     │
└─────────────────┴────────────────────┴─────────────────────┘
```

---

## Practical Examples

### Basic Read/Write

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>

int main() {
    struct io_uring ring;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    char buf[1024];
    int fd, ret;

    // Initialize io_uring with 8 entries
    ret = io_uring_queue_init(8, &ring, 0);
    if (ret < 0) {
        perror("io_uring_queue_init");
        return 1;
    }

    // Open file
    fd = open("test.txt", O_RDONLY);
    if (fd < 0) {
        perror("open");
        return 1;
    }

    // Get SQE and prepare read operation
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf, sizeof(buf), 0);
    sqe->user_data = 1;  // Identifier for this operation

    // Submit and wait
    ret = io_uring_submit(&ring);
    if (ret < 0) {
        perror("io_uring_submit");
        return 1;
    }

    // Wait for completion
    ret = io_uring_wait_cqe(&ring, &cqe);
    if (ret < 0) {
        perror("io_uring_wait_cqe");
        return 1;
    }

    // Check result
    if (cqe->res < 0) {
        fprintf(stderr, "Read failed: %s\n", strerror(-cqe->res));
    } else {
        printf("Read %d bytes\n", cqe->res);
    }

    // Mark CQE as seen
    io_uring_cqe_seen(&ring, cqe);

    // Cleanup
    close(fd);
    io_uring_queue_exit(&ring);
    return 0;
}
```

### High-Performance Server Pattern (Complete)

```c
#include <liburing.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <unistd.h>

#define QUEUE_DEPTH 256
#define READ_SZ     1024

enum {
    EVENT_TYPE_ACCEPT,
    EVENT_TYPE_READ,
    EVENT_TYPE_WRITE,
};

struct request {
    int event_type;
    int client_fd;
    struct iovec iov;
    char buf[READ_SZ];
};

struct io_uring ring;
int server_fd;

void add_accept_request(struct io_uring *ring, int server_fd,
                        struct sockaddr_in *client_addr,
                        socklen_t *client_addr_len) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    struct request *req = malloc(sizeof(*req));

    req->event_type = EVENT_TYPE_ACCEPT;

    io_uring_prep_accept(sqe, server_fd,
                         (struct sockaddr *)client_addr,
                         client_addr_len, 0);
    io_uring_sqe_set_data(sqe, req);
}

void add_read_request(struct io_uring *ring, int client_fd) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);
    struct request *req = malloc(sizeof(*req));

    req->event_type = EVENT_TYPE_READ;
    req->client_fd = client_fd;
    req->iov.iov_base = req->buf;
    req->iov.iov_len = READ_SZ;

    io_uring_prep_readv(sqe, client_fd, &req->iov, 1, 0);
    io_uring_sqe_set_data(sqe, req);
}

void add_write_request(struct io_uring *ring, struct request *req) {
    struct io_uring_sqe *sqe = io_uring_get_sqe(ring);

    req->event_type = EVENT_TYPE_WRITE;

    io_uring_prep_writev(sqe, req->client_fd, &req->iov, 1, 0);
    io_uring_sqe_set_data(sqe, req);
}

int setup_server(int port) {
    struct sockaddr_in addr;
    int fd, opt = 1;

    fd = socket(AF_INET, SOCK_STREAM, 0);
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR | SO_REUSEPORT, &opt, sizeof(opt));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(port);

    bind(fd, (struct sockaddr *)&addr, sizeof(addr));
    listen(fd, SOMAXCONN);

    return fd;
}

int main() {
    struct io_uring_cqe *cqe;
    struct sockaddr_in client_addr;
    socklen_t client_addr_len = sizeof(client_addr);

    // Initialize io_uring
    io_uring_queue_init(QUEUE_DEPTH, &ring, 0);

    // Setup server socket
    server_fd = setup_server(8080);
    printf("Server listening on port 8080\n");

    // Add initial accept request
    add_accept_request(&ring, server_fd, &client_addr, &client_addr_len);
    io_uring_submit(&ring);

    // Event loop
    while (1) {
        int ret = io_uring_wait_cqe(&ring, &cqe);
        if (ret < 0) {
            perror("io_uring_wait_cqe");
            break;
        }

        struct request *req = io_uring_cqe_get_data(cqe);

        switch (req->event_type) {
            case EVENT_TYPE_ACCEPT: {
                int client_fd = cqe->res;
                if (client_fd >= 0) {
                    // Add read request for new client
                    add_read_request(&ring, client_fd);

                    // Add another accept request
                    add_accept_request(&ring, server_fd,
                                      &client_addr, &client_addr_len);
                }
                free(req);
                break;
            }

            case EVENT_TYPE_READ: {
                int bytes_read = cqe->res;
                if (bytes_read <= 0) {
                    // Connection closed or error
                    close(req->client_fd);
                    free(req);
                } else {
                    // Echo back - reuse request for write
                    req->iov.iov_len = bytes_read;
                    add_write_request(&ring, req);
                }
                break;
            }

            case EVENT_TYPE_WRITE: {
                // Write complete, wait for more data
                add_read_request(&ring, req->client_fd);
                free(req);
                break;
            }
        }

        io_uring_cqe_seen(&ring, cqe);
        io_uring_submit(&ring);
    }

    io_uring_queue_exit(&ring);
    return 0;
}
```

### Batched I/O with Fixed Buffers

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define QUEUE_DEPTH  64
#define BLOCK_SIZE   4096
#define NUM_BUFFERS  32

int main(int argc, char *argv[]) {
    struct io_uring ring;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    struct iovec *iovecs;
    int fd, ret, i;
    off_t file_size, offset = 0;
    unsigned pending = 0;

    if (argc < 2) {
        fprintf(stderr, "Usage: %s <file>\n", argv[0]);
        return 1;
    }

    // Open file
    fd = open(argv[1], O_RDONLY | O_DIRECT);
    if (fd < 0) {
        perror("open");
        return 1;
    }

    // Get file size
    file_size = lseek(fd, 0, SEEK_END);
    lseek(fd, 0, SEEK_SET);

    // Initialize io_uring
    ret = io_uring_queue_init(QUEUE_DEPTH, &ring, 0);
    if (ret < 0) {
        fprintf(stderr, "io_uring_queue_init: %s\n", strerror(-ret));
        return 1;
    }

    // Allocate aligned buffers for O_DIRECT
    iovecs = calloc(NUM_BUFFERS, sizeof(struct iovec));
    for (i = 0; i < NUM_BUFFERS; i++) {
        if (posix_memalign(&iovecs[i].iov_base, BLOCK_SIZE, BLOCK_SIZE)) {
            perror("posix_memalign");
            return 1;
        }
        iovecs[i].iov_len = BLOCK_SIZE;
    }

    // Register fixed buffers
    ret = io_uring_register_buffers(&ring, iovecs, NUM_BUFFERS);
    if (ret < 0) {
        fprintf(stderr, "io_uring_register_buffers: %s\n", strerror(-ret));
        return 1;
    }

    // Register file descriptor
    ret = io_uring_register_files(&ring, &fd, 1);
    if (ret < 0) {
        fprintf(stderr, "io_uring_register_files: %s\n", strerror(-ret));
        return 1;
    }

    printf("Reading %ld bytes with fixed buffers...\n", file_size);

    int buf_idx = 0;
    size_t total_read = 0;

    // Submit initial batch
    while (offset < file_size && pending < NUM_BUFFERS) {
        sqe = io_uring_get_sqe(&ring);
        if (!sqe) break;

        // Use fixed buffer and fixed file
        io_uring_prep_read_fixed(sqe, 0,  // file index, not fd
                                  iovecs[buf_idx].iov_base,
                                  BLOCK_SIZE, offset, buf_idx);
        sqe->flags |= IOSQE_FIXED_FILE;
        sqe->user_data = buf_idx;

        offset += BLOCK_SIZE;
        buf_idx = (buf_idx + 1) % NUM_BUFFERS;
        pending++;
    }

    io_uring_submit(&ring);

    // Process completions and submit more
    while (pending > 0) {
        ret = io_uring_wait_cqe(&ring, &cqe);
        if (ret < 0) {
            fprintf(stderr, "io_uring_wait_cqe: %s\n", strerror(-ret));
            break;
        }

        if (cqe->res < 0) {
            fprintf(stderr, "Read error: %s\n", strerror(-cqe->res));
        } else if (cqe->res > 0) {
            total_read += cqe->res;

            // Process data in iovecs[cqe->user_data].iov_base
            // ...
        }

        pending--;

        // Submit more if needed
        if (offset < file_size) {
            sqe = io_uring_get_sqe(&ring);
            if (sqe) {
                int idx = cqe->user_data;  // Reuse completed buffer

                io_uring_prep_read_fixed(sqe, 0,
                                          iovecs[idx].iov_base,
                                          BLOCK_SIZE, offset, idx);
                sqe->flags |= IOSQE_FIXED_FILE;
                sqe->user_data = idx;

                offset += BLOCK_SIZE;
                pending++;
                io_uring_submit(&ring);
            }
        }

        io_uring_cqe_seen(&ring, cqe);
    }

    printf("Total read: %zu bytes\n", total_read);

    // Cleanup
    io_uring_unregister_files(&ring);
    io_uring_unregister_buffers(&ring);
    io_uring_queue_exit(&ring);

    for (i = 0; i < NUM_BUFFERS; i++) {
        free(iovecs[i].iov_base);
    }
    free(iovecs);
    close(fd);

    return 0;
}
```

### SQPOLL Mode Example

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>

#define QUEUE_DEPTH 128
#define BLOCK_SIZE  4096

int main() {
    struct io_uring ring;
    struct io_uring_params params;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    void *buf;
    int fd, ret;

    // Setup with SQPOLL
    memset(&params, 0, sizeof(params));
    params.flags = IORING_SETUP_SQPOLL;
    params.sq_thread_idle = 2000;  // 2 seconds idle before sleep

    ret = io_uring_queue_init_params(QUEUE_DEPTH, &ring, &params);
    if (ret < 0) {
        if (ret == -EPERM) {
            fprintf(stderr, "SQPOLL requires root or CAP_SYS_NICE\n");
        } else {
            fprintf(stderr, "io_uring_queue_init_params: %s\n", strerror(-ret));
        }
        return 1;
    }

    printf("SQPOLL thread started\n");

    // Allocate buffer
    buf = aligned_alloc(BLOCK_SIZE, BLOCK_SIZE);
    if (!buf) {
        perror("aligned_alloc");
        return 1;
    }

    // Open file
    fd = open("test_file", O_RDWR | O_CREAT | O_DIRECT, 0644);
    if (fd < 0) {
        perror("open");
        return 1;
    }

    // Register fd for SQPOLL (required for SQPOLL mode)
    ret = io_uring_register_files(&ring, &fd, 1);
    if (ret < 0) {
        fprintf(stderr, "io_uring_register_files: %s\n", strerror(-ret));
        return 1;
    }

    // Prepare write
    memset(buf, 'A', BLOCK_SIZE);

    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_write(sqe, 0, buf, BLOCK_SIZE, 0);  // fd index = 0
    sqe->flags |= IOSQE_FIXED_FILE;
    sqe->user_data = 1;

    // No io_uring_submit() needed! Just update the tail
    // The SQPOLL thread will pick it up
    io_uring_sqe_set_data(sqe, (void *)1);

    // We need to "kick" the ring to ensure submission
    // In practice, io_uring_submit() is still called but returns immediately
    io_uring_submit(&ring);

    // Check if SQPOLL thread needs waking
    // (it sleeps after sq_thread_idle milliseconds of inactivity)
    if (*ring.sq.kflags & IORING_SQ_NEED_WAKEUP) {
        printf("Waking SQPOLL thread\n");
        io_uring_enter(ring.ring_fd, 0, 0, IORING_ENTER_SQ_WAKEUP, NULL);
    }

    // Wait for completion
    ret = io_uring_wait_cqe(&ring, &cqe);
    if (ret < 0) {
        fprintf(stderr, "io_uring_wait_cqe: %s\n", strerror(-ret));
        return 1;
    }

    printf("Write completed: %d bytes\n", cqe->res);
    io_uring_cqe_seen(&ring, cqe);

    // Cleanup
    io_uring_unregister_files(&ring);
    io_uring_queue_exit(&ring);
    free(buf);
    close(fd);

    return 0;
}
```

### Linked Operations with Timeout

```c
#include <liburing.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main() {
    struct io_uring ring;
    struct io_uring_sqe *sqe;
    struct io_uring_cqe *cqe;
    char buf[4096];
    int fd, ret;
    struct __kernel_timespec ts;

    io_uring_queue_init(8, &ring, 0);

    fd = open("test.txt", O_RDWR | O_CREAT, 0644);

    // Operation 1: Write (linked)
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_write(sqe, fd, "Hello, World!\n", 14, 0);
    sqe->flags |= IOSQE_IO_LINK;
    sqe->user_data = 1;

    // Operation 2: Fsync (linked) - only runs if write succeeds
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_fsync(sqe, fd, IORING_FSYNC_DATASYNC);
    sqe->flags |= IOSQE_IO_LINK;
    sqe->user_data = 2;

    // Operation 3: Read (linked) - only runs if fsync succeeds
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, fd, buf, sizeof(buf), 0);
    sqe->flags |= IOSQE_IO_LINK;
    sqe->user_data = 3;

    // Operation 4: Timeout for the entire chain
    ts.tv_sec = 5;
    ts.tv_nsec = 0;
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_link_timeout(sqe, &ts, 0);
    sqe->user_data = 4;

    printf("Submitting linked operations...\n");
    ret = io_uring_submit(&ring);
    printf("Submitted %d operations\n", ret);

    // Collect all completions
    for (int i = 0; i < 4; i++) {
        ret = io_uring_wait_cqe(&ring, &cqe);
        if (ret < 0) {
            fprintf(stderr, "wait_cqe: %s\n", strerror(-ret));
            break;
        }

        printf("Operation %llu completed: ", (unsigned long long)cqe->user_data);

        if (cqe->res < 0) {
            if (cqe->res == -ECANCELED) {
                printf("CANCELED (previous op failed or timeout)\n");
            } else if (cqe->res == -ETIME) {
                printf("TIMEOUT\n");
            } else {
                printf("ERROR: %s\n", strerror(-cqe->res));
            }
        } else {
            printf("SUCCESS (res=%d)\n", cqe->res);
        }

        io_uring_cqe_seen(&ring, cqe);
    }

    // Print what we read
    printf("Buffer contents: %s", buf);

    io_uring_queue_exit(&ring);
    close(fd);

    return 0;
}
```

---

## Advanced Features

### 1. **Multishot Operations**

Multishot operations can generate multiple CQEs from a single SQE:

```c
// Multishot accept - keeps accepting connections
sqe = io_uring_get_sqe(&ring);
io_uring_prep_multishot_accept(sqe, server_fd, NULL, NULL, 0);

// Each accepted connection generates a CQE
// CQE_F_MORE flag indicates more completions coming

while (1) {
    io_uring_wait_cqe(&ring, &cqe);

    if (cqe->flags & IORING_CQE_F_MORE) {
        // More completions expected from this SQE
        int client_fd = cqe->res;
        handle_client(client_fd);
    } else {
        // Multishot terminated (error or explicit cancel)
        break;
    }

    io_uring_cqe_seen(&ring, cqe);
}
```

```
Single-shot vs Multishot:

Single-shot accept:              Multishot accept:
┌─────────────────────┐         ┌─────────────────────┐
│ SQE (accept)        │         │ SQE (multishot)     │
└─────────┬───────────┘         └─────────┬───────────┘
          │                               │
          ▼                               ├──► CQE (client 1)
┌─────────────────────┐                   │    [MORE flag set]
│ CQE (1 client)      │                   │
└─────────────────────┘                   ├──► CQE (client 2)
                                          │    [MORE flag set]
Need new SQE for                          │
next accept                               ├──► CQE (client 3)
                                          │    [MORE flag set]
                                          │
                                          └──► ... continues
```

### 2. **Provided Buffers (Buffer Selection)**

```c
// Register a pool of buffers
#define BUFFERS_COUNT 64
#define BUFFER_SIZE   4096
#define BUFFER_GROUP  1

char *bufs[BUFFERS_COUNT];
struct io_uring_buf_reg reg;
struct io_uring_buf_ring *br;

// Setup buffer ring
void *mapped = mmap(NULL, BUFFERS_COUNT * sizeof(struct io_uring_buf),
                    PROT_READ | PROT_WRITE,
                    MAP_ANONYMOUS | MAP_PRIVATE, -1, 0);

br = (struct io_uring_buf_ring *)mapped;
io_uring_buf_ring_init(br);

// Register the buffer ring
memset(&reg, 0, sizeof(reg));
reg.ring_addr = (unsigned long)br;
reg.ring_entries = BUFFERS_COUNT;
reg.bgid = BUFFER_GROUP;

io_uring_register_buf_ring(&ring, &reg, 0);

// Add buffers to the ring
for (int i = 0; i < BUFFERS_COUNT; i++) {
    bufs[i] = malloc(BUFFER_SIZE);
    io_uring_buf_ring_add(br, bufs[i], BUFFER_SIZE, i,
                          io_uring_buf_ring_mask(BUFFERS_COUNT), i);
}
io_uring_buf_ring_advance(br, BUFFERS_COUNT);

// Use in read operations - kernel selects buffer
sqe = io_uring_get_sqe(&ring);
io_uring_prep_recv(sqe, client_fd, NULL, BUFFER_SIZE, 0);
sqe->flags |= IOSQE_BUFFER_SELECT;
sqe->buf_group = BUFFER_GROUP;

// On completion, find which buffer was used
io_uring_wait_cqe(&ring, &cqe);
if (cqe->res > 0 && (cqe->flags & IORING_CQE_F_BUFFER)) {
    int buf_id = cqe->flags >> IORING_CQE_BUFFER_SHIFT;
    char *data = bufs[buf_id];
    int len = cqe->res;

    // Process data...

    // Return buffer to pool
    io_uring_buf_ring_add(br, bufs[buf_id], BUFFER_SIZE, buf_id,
                          io_uring_buf_ring_mask(BUFFERS_COUNT), 0);
    io_uring_buf_ring_advance(br, 1);
}
```

### 3. **Direct Descriptors (Kernel 5.19+)**

```c
// Allocate file descriptor directly in the fixed file table
struct io_uring_params params = {
    .flags = IORING_SETUP_NO_MMAP | IORING_SETUP_REGISTERED_FD_ONLY,
};

// Open directly into fixed table
sqe = io_uring_get_sqe(&ring);
io_uring_prep_openat_direct(sqe, AT_FDCWD, "file.txt", O_RDONLY, 0,
                            IORING_FILE_INDEX_ALLOC);

io_uring_submit(&ring);
io_uring_wait_cqe(&ring, &cqe);

// cqe->res contains the fixed file index
int file_index = cqe->res;

// Use directly without going through regular fd table
sqe = io_uring_get_sqe(&ring);
io_uring_prep_read(sqe, file_index, buf, len, 0);
sqe->flags |= IOSQE_FIXED_FILE;
```

### 4. **io_uring_cmd (Passthrough)**

```c
// Send arbitrary commands to drivers (NVMe, etc.)
struct nvme_user_io {
    __u8    opcode;
    __u8    flags;
    __u16   control;
    __u16   nblocks;
    __u16   rsvd;
    __u64   metadata;
    __u64   addr;
    __u64   slba;
    __u32   dsmgmt;
    __u32   reftag;
    __u16   apptag;
    __u16   appmask;
};

sqe = io_uring_get_sqe(&ring);
io_uring_prep_cmd(sqe, IORING_CMD_FIXED, nvme_fd, &cmd, sizeof(cmd));
```

---

## Supported Operations (Opcodes)

```
┌────────────────────────────────────────────────────────────────┐
│                    io_uring Operations                         │
├────────────────────────────────────────────────────────────────┤
│ File I/O:                                                      │
│   IORING_OP_READ, IORING_OP_WRITE                             │
│   IORING_OP_READV, IORING_OP_WRITEV                           │
│   IORING_OP_READ_FIXED, IORING_OP_WRITE_FIXED                 │
│   IORING_OP_FSYNC, IORING_OP_SYNC_FILE_RANGE                  │
│   IORING_OP_FALLOCATE, IORING_OP_FADVISE                      │
│   IORING_OP_MADVISE                                           │
├────────────────────────────────────────────────────────────────┤
│ File Management:                                               │
│   IORING_OP_OPENAT, IORING_OP_OPENAT2                         │
│   IORING_OP_CLOSE                                             │
│   IORING_OP_STATX                                             │
│   IORING_OP_RENAMEAT, IORING_OP_UNLINKAT                      │
│   IORING_OP_MKDIRAT, IORING_OP_SYMLINKAT, IORING_OP_LINKAT    │
├────────────────────────────────────────────────────────────────┤
│ Network:                                                       │
│   IORING_OP_ACCEPT, IORING_OP_CONNECT                         │
│   IORING_OP_RECV, IORING_OP_SEND                              │
│   IORING_OP_RECVMSG, IORING_OP_SENDMSG                        │
│   IORING_OP_SEND_ZC (zero-copy send)                          │
│   IORING_OP_SOCKET                                            │
│   IORING_OP_SHUTDOWN                                          │
├────────────────────────────────────────────────────────────────┤
│ Polling:                                                       │
│   IORING_OP_POLL_ADD, IORING_OP_POLL_REMOVE                   │
│   IORING_OP_EPOLL_CTL                                         │
├────────────────────────────────────────────────────────────────┤
│ Splice/Copy:                                                   │
│   IORING_OP_SPLICE, IORING_OP_TEE                             │
├────────────────────────────────────────────────────────────────┤
│ Control:                                                       │
│   IORING_OP_NOP                                               │
│   IORING_OP_TIMEOUT, IORING_OP_TIMEOUT_REMOVE                 │
│   IORING_OP_LINK_TIMEOUT                                      │
│   IORING_OP_ASYNC_CANCEL                                      │
│   IORING_OP_PROVIDE_BUFFERS, IORING_OP_REMOVE_BUFFERS         │
│   IORING_OP_MSG_RING                                          │
├────────────────────────────────────────────────────────────────┤
│ Extended (5.15+):                                              │
│   IORING_OP_URING_CMD (driver passthrough)                    │
│   IORING_OP_GETXATTR, IORING_OP_SETXATTR                      │
│   IORING_OP_FGETXATTR, IORING_OP_FSETXATTR                    │
└────────────────────────────────────────────────────────────────┘
```

---

## Performance Tuning Guidelines

### 1. **Queue Sizing**

```c
// Rule of thumb for queue sizes
// SQ entries: Match your expected concurrent operations
// CQ entries: Usually 2x SQ entries (CQ fills faster)

struct io_uring_params params = {
    .flags = IORING_SETUP_CQSIZE,
    .sq_entries = 256,    // Power of 2
    .cq_entries = 512,    // Can be larger than SQ
};

// For high-throughput scenarios
// SQ: 1024-4096
// CQ: 2048-8192
```

### 2. **Batching Strategy**

```c
// Bad: Submit one at a time
for (int i = 0; i < 1000; i++) {
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, ...);
    io_uring_submit(&ring);  // Syscall each iteration!
}

// Good: Batch submissions
for (int i = 0; i < 1000; i++) {
    sqe = io_uring_get_sqe(&ring);
    io_uring_prep_read(sqe, ...);
}
io_uring_submit(&ring);  // Single syscall!

// Better: Submit and reap together
io_uring_submit_and_wait(&ring, min_complete);
```

### 3. **Memory Layout Optimization**

```c
// Align buffers to page boundaries
// Helps with DMA and reduces memory copies
void *buf;
posix_memalign(&buf, 4096, buffer_size);

// For O_DIRECT, alignment is required
// Block size alignment (usually 512 or 4096)

// Use huge pages for large buffer pools
void *huge_buf = mmap(NULL, size, PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);
```

### 4. **NUMA Considerations**

```c
// For NUMA systems, pin SQPOLL thread to same node as application
struct io_uring_params params = {
    .flags = IORING_SETUP_SQPOLL | IORING_SETUP_SQ_AFF,
    .sq_thread_cpu = 4,        // Pin to CPU on same NUMA node
    .sq_thread_idle = 2000,
};

// Allocate buffers on local NUMA node
#include <numaif.h>

void *buf = mmap(NULL, size, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

// Bind to NUMA node 0
unsigned long nodemask = 1;
mbind(buf, size, MPOL_BIND, &nodemask, sizeof(nodemask) * 8, 0);

// Set io-wq worker affinity
cpu_set_t cpumask;
CPU_ZERO(&cpumask);
CPU_SET(0, &cpumask);  // Local NUMA CPUs
CPU_SET(1, &cpumask);
CPU_SET(2, &cpumask);
CPU_SET(3, &cpumask);

io_uring_register(ring_fd, IORING_REGISTER_IOWQ_AFF,
                  &cpumask, sizeof(cpumask));
```

```
NUMA-Aware io_uring Deployment:

┌───────────────────────────────────────────────────────────────┐
│  NUMA Node 0                   NUMA Node 1                    │
│  ┌─────────────────────┐      ┌─────────────────────┐        │
│  │ CPUs 0-3            │      │ CPUs 4-7            │        │
│  │ Local Memory        │      │ Local Memory        │        │
│  │                     │      │                     │        │
│  │ ┌─────────────────┐ │      │                     │        │
│  │ │ Application     │ │      │                     │        │
│  │ │ SQPOLL thread   │ │      │                     │        │
│  │ │ io-wq workers   │ │      │                     │        │
│  │ │ Ring buffers    │ │      │                     │        │
│  │ │ I/O buffers     │ │      │                     │        │
│  │ └─────────────────┘ │      │                     │        │
│  │                     │      │                     │        │
│  │ NVMe attached here  │      │                     │        │
│  └─────────────────────┘      └─────────────────────┘        │
│                                                               │
│  Best practice: Co-locate app, SQPOLL, workers, buffers,     │
│  and storage on the same NUMA node.                          │
└───────────────────────────────────────────────────────────────┘
```

---

## See Also

- [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) — io_uring in context alongside O_DIRECT, SQPOLL, registered buffers, and other expert-level interfaces
- [Filesystem Design](@/notes/os/filesystem_design.md) — Filesystem internals that io_uring I/O operations interact with
- [VFIO Internals](@/notes/os/vfio_internals.md) — Userspace I/O via VFIO/DPDK/SPDK complements io_uring for device-level bypass
- [Rust Low-Level Programming](@/notes/programming/rust_low_level.md) — Unsafe Rust patterns for building io_uring bindings and zero-copy I/O
