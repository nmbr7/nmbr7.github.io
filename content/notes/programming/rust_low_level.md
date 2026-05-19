+++
title = "Rust Low Level"
date = 2026-02-10
[taxonomies]
tags = ["rust", "unsafe", "ffi", "pin", "inline-assembly", "allocators", "memory-management"]
+++


Great question. You’re essentially asking:

> _“Besides `MaybeUninit`, what other Rust constructs exist specifically to let me do C-/systems-level programming safely (or at least deliberately unsafe)?”_

Below is a **curated map of Rust’s low-level toolbox**, grouped by _what problem they solve_, with explanations and when you should use them. These are the constructs people actually use when writing kernels, databases, allocators, runtimes, or FFI bindings (like PostgreSQL).

---

# 1️⃣ Memory & Initialization Control

### 🔹 `MaybeUninit<T>`

- Allocate without initializing
- Partial initialization
- FFI “out parameters”
- Avoiding unnecessary zeroing

Used for:

- Structs
- Arrays
- Buffers

---

### 🔹 `ManuallyDrop<T>`

```rust
use std::mem::ManuallyDrop;
```

- Prevents Rust from automatically running `Drop`
- Lets you control _when_ (or if) destruction happens

Used for:

- Custom allocators
- Unions
- FFI types owned by C
- Avoiding double-free

Example:

```rust
let x = ManuallyDrop::new(String::from("hi"));
// no destructor called automatically
```

---

### 🔹 `mem::forget`

- Leaks a value on purpose
- Never runs `Drop`

Used when:

- Ownership is transferred to C
- Process exit is imminent

---

# 2️⃣ Raw Pointers & Unsafe Access

### 🔹 `*const T` / `*mut T`

- Like C pointers
- No aliasing rules enforced by compiler
- No lifetime tracking

Used for:

- FFI
- Intrusive data structures
- Memory-mapped data
- Custom allocators

---

### 🔹 `addr_of!` / `addr_of_mut!`

```rust
use std::ptr::addr_of;
```

- Gets field addresses **without creating references**
- Avoids UB when struct is uninitialized or packed

Very important for:

- `repr(packed)`
- Manual layout inspection

---

### 🔹 `read_unaligned` / `write_unaligned`

- Access misaligned memory safely
- Generates correct instructions

Used when:

- Parsing disk/network formats
- Working with packed data

---

# 3️⃣ Layout & ABI Control

### 🔹 `#[repr(C)]`

- C-compatible layout
- Stable field order
- ABI-safe

Mandatory for:

- FFI
- Shared memory
- Disk formats (with care)

---

### 🔹 `#[repr(transparent)]`

- Guarantees same layout as inner field
- Used for newtypes

Example:

```rust
#[repr(transparent)]
struct Page(u8);
```

---

### 🔹 `#[repr(packed)]` / `#[repr(packed(N))]`

- Removes padding
- Allows misalignment

⚠️ Extremely dangerous:

- Field access may be UB
- Use only for byte parsing + unaligned reads

---

### 🔹 `offset_of!`

```rust
use std::mem::offset_of;
```

- Safe equivalent of C `offsetof`
- Requires `repr(C)`

---

# 4️⃣ Interior Mutability & Aliasing

### 🔹 `UnsafeCell<T>`

- The **only legal way** to mutate through shared references
- Foundation of `Cell`, `RefCell`, atomics

Used for:

- Lock-free structures
- Memory-mapped data
- Shared mutable state

---

### 🔹 `Cell<T>` / `RefCell<T>`

- Safe wrappers around `UnsafeCell`
- Runtime-checked mutability

Used when:

- You need mutation without `&mut`
- Performance isn’t ultra-critical

---

# 5️⃣ Unions & Bit Patterns

### 🔹 `union`

- Same memory interpreted in multiple ways
- No active field tracking

Used for:

- FFI
- Tagged values
- Low-level protocols

---

### 🔹 `std::mem::transmute`

⚠️ **The most dangerous tool**

- Reinterprets bits as another type
- No checks

Safer alternatives:

- `from_ne_bytes`
- `bytemuck`
- `ptr::cast`

---

# 6️⃣ Byte-Level APIs

### 🔹 `slice::from_raw_parts`

- View raw memory as a slice

Used for:

- Memory-mapped files
- C arrays
- DMA buffers

---

### 🔹 `copy_nonoverlapping`

- Like `memcpy`
- Faster, well-defined

---

### 🔹 `write_bytes`

- Like `memset`

---

# 7️⃣ Synchronization & Atomics

### 🔹 `std::sync::atomic::*`

- Lock-free programming
- Memory ordering control

Used for:

- Shared memory
- Concurrent caches
- Low-latency systems

---

# 8️⃣ Language Features for Systems Code

### 🔹 `unsafe` blocks

- Explicitly mark where invariants are violated
- Compiler trusts you inside

---

### 🔹 `no_std`

- No heap
- No OS
- Kernel / embedded / bare metal

---

### 🔹 `extern "C"`

- ABI control
- Function calling conventions

---

# 9️⃣ Tooling for Low-Level Rust

### 🔹 `bindgen`

- Auto-generate Rust bindings from C

---

### 🔹 `bytemuck`

- Safe “plain old data” casting

---

### 🔹 `zerocopy`

- Zero-copy parsing of byte buffers

---

# 10️⃣ Mental model: Rust vs C

| Need                 | C       | Rust               |
| -------------------- | ------- | ------------------ |
| Uninitialized memory | Normal  | `MaybeUninit`      |
| Pointer arithmetic   | Default | `unsafe`           |
| Zero-init structs    | Trivial | Unsafe             |
| Layout guarantees    | Weak    | Explicit           |
| UB detection         | None    | Strong             |
| Control              | High    | High, but explicit |

---

## TL;DR

Rust **absolutely supports low-level programming**, but:

- Every dangerous capability is:
  - Explicit
  - Opt-in
  - Localized

- Invariants are your responsibility
- The compiler will trust you _only where you ask it to_

If you can write PostgreSQL or a kernel in C, you can do it in Rust — **with fewer silent footguns**.

---

Below is a **practical, opinionated cheat sheet** aimed at people doing **systems / database / kernel / FFI / storage-engine** work in Rust (exactly the PostgreSQL-style domain you’ve been exploring).

This is not beginner Rust. This is **“I need to control bytes, layout, lifetimes, and UB” Rust**.

---

# 🧨 Part 1: “LAST RESORT” CONSTRUCTS (Use Only When Nothing Else Works)

These are tools that **bypass Rust’s safety model almost entirely**.
Use them only when you _fully understand the invariants_.

---

## 🚨 `mem::transmute`

### What it does

Reinterprets bits of one type as another.

```rust
let x: u32 = unsafe { std::mem::transmute([0u8; 4]) };
```

### Why it’s dangerous

- No size check at runtime
- No alignment check
- No validity check
- Breaks aliasing rules
- Can create invalid values instantly

### When to use

⚠️ Almost never

### Preferred alternatives

- `from_ne_bytes`
- `ptr::cast`
- `bytemuck::{Pod, Zeroable}`
- `read_unaligned`

---

## 🚨 `#[repr(packed)]`

### What it does

Removes all padding from a struct.

```rust
#[repr(packed)]
struct Header {
    a: u8,
    b: u32, // misaligned
}
```

### Why it’s dangerous

- Accessing `b` via reference is **UB**
- Generates misaligned loads
- Compiler assumes alignment normally

### When to use

✔ Only when parsing **byte streams**
✔ Only with `read_unaligned`
✔ Never store pointers into packed fields

---

## 🚨 Raw pointer arithmetic (`ptr.add`, `ptr.offset`)

```rust
let p = base_ptr.add(offset);
```

### Why it’s dangerous

- No bounds checks
- No aliasing checks
- Easy to produce dangling pointers

### When to use

✔ Memory-mapped files
✔ Page layout parsing
✔ Custom allocators

---

## 🚨 `assume_init()`

```rust
let v = unsafe { maybe.assume_init() };
```

### Why it’s dangerous

- Compiler trusts you blindly
- UB if memory is not fully valid

### When to use

✔ After **proving** initialization
✔ FFI out-parameters
✔ Zeroed C structs

---

## 🚨 `UnsafeCell<T>`

### What it does

Allows mutation through shared references.

### Why it’s dangerous

- Breaks Rust aliasing rules
- Can violate data-race guarantees

### When to use

✔ Implementing atomics, locks, cells
✔ Memory-mapped mutable data

---

## 🚨 `union`

### What it does

Overlapping memory interpretations.

### Why it’s dangerous

- Rust doesn’t track active field
- Reading wrong field = UB

### When to use

✔ FFI
✔ Tagged representations
✔ C ABI compatibility

---

## 🚨 `mem::forget`

### What it does

Leaks memory intentionally.

### When to use

✔ Ownership handed to C
✔ Process lifetime objects

---

# 🧰 Part 2: RUST SYSTEMS PROGRAMMING CHEAT SHEET

## 🧱 Memory & Initialization

| Tool                  | Purpose                      |
| --------------------- | ---------------------------- |
| `MaybeUninit<T>`      | Uninitialized / partial init |
| `write_bytes`         | memset                       |
| `copy_nonoverlapping` | memcpy                       |
| `ManuallyDrop<T>`     | Manual destruction           |
| `mem::size_of<T>`     | Type size                    |
| `mem::align_of<T>`    | Alignment                    |

---

## 📐 Layout & ABI

| Attribute           | Use               |
| ------------------- | ----------------- |
| `repr(C)`           | FFI, disk formats |
| `repr(transparent)` | Newtype ABI       |
| `repr(align(N))`    | Force alignment   |
| `offset_of!`        | Field offsets     |
| `size_of_val`       | DST sizes         |

---

## 📍 Pointer & Addressing

| Tool                    | Use                     |
| ----------------------- | ----------------------- |
| `*const T`, `*mut T`    | C-style pointers        |
| `addr_of!`              | Get address without ref |
| `read_unaligned`        | Misaligned access       |
| `slice::from_raw_parts` | Memory → slice          |

---

## 🧠 Aliasing & Mutability

| Tool            | Use                    |
| --------------- | ---------------------- |
| `UnsafeCell<T>` | Interior mutability    |
| `Cell<T>`       | Copy types             |
| `RefCell<T>`    | Borrow-checked runtime |
| `Atomic*`       | Lock-free sync         |

---

## 🧪 Byte Interpretation

| Tool            | Use               |
| --------------- | ----------------- |
| `from_ne_bytes` | Integer parsing   |
| `bytemuck::Pod` | Safe casts        |
| `zerocopy`      | Zero-copy parsing |

---

## 🔗 FFI & C Interop

| Tool              | Use           |
| ----------------- | ------------- |
| `extern "C"`      | ABI control   |
| `c_int`, `c_void` | C types       |
| `bindgen`         | Auto bindings |
| `#[no_mangle]`    | Symbol names  |

---

## ⚙️ Performance & Control

| Feature            | Use               |
| ------------------ | ----------------- |
| `no_std`           | Kernel / embedded |
| `inline(always)`   | Hot paths         |
| `black_box`        | Benchmarking      |
| `cfg(target_arch)` | Platform control  |

---

## 🧭 SAFE PATTERNS (IMPORTANT)

### ✅ Unsafe core + safe wrapper

```rust
unsafe fn parse_page(ptr: *const u8) -> Page { ... }

pub fn page_from_bytes(bytes: &[u8]) -> Page {
    assert!(bytes.len() == PAGE_SIZE);
    unsafe { parse_page(bytes.as_ptr()) }
}
```

---

### ❌ What NOT to do

```rust
#[repr(packed)]
struct Bad {
    x: u32,
}

let r = &bad.x; // UB
```

---

## 🧠 Rule of Thumb Hierarchy

**Prefer (in order):**

1. Safe Rust
2. `repr(C)` + references
3. Raw pointers
4. `MaybeUninit`
5. `UnsafeCell`
6. `repr(packed)`
7. `transmute`

If you’re at #6 or #7, **stop and double-check**.

---

## 🧩 PostgreSQL-specific mapping (bonus)

| PostgreSQL C | Rust equivalent        |
| ------------ | ---------------------- |
| `struct`     | `#[repr(C)] struct`    |
| `offsetof`   | `offset_of!`           |
| `memset`     | `write_bytes`          |
| `char *`     | `*mut u8`              |
| `uint16`     | `u16`                  |
| Page buffer  | `&[u8]` or `*const u8` |

---

## 🧠 Final TL;DR

- Rust gives you **every low-level tool C has**
- Dangerous tools are:
  - Explicit
  - Localized
  - Auditable

- “Last resort” means:

  > _You are now the compiler_

---

## See Also

- [Arrow PostgreSQL Integration](@/notes/database/arrow_postgresql_integration.md) — pg_arrow project using these unsafe Rust patterns for direct PostgreSQL file access
- [io_uring Internals](@/notes/os/io_uring_internals.md) — Low-level I/O interface commonly wrapped with unsafe Rust bindings
- [Deterministic Simulation Testing](@/notes/distributed/deterministic_simulation_testing.md) — Turmoil, MadSim, and loom are Rust-based DST frameworks using unsafe internals
- [Data Structures](@/notes/programming/data_structures.md) — Lock-free and concurrent data structures requiring unsafe Rust for implementation
