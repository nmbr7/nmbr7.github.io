+++
title = "Arrow Postgresql Integration"
date = 2026-02-10
[taxonomies]
tags = ["arrow", "postgresql", "toast", "mvcc", "dictionary-encoding", "rust", "columnar"]
+++


# Advanced Arrow Format and Row-to-Columnar Conversion

**Date**: 2026-01-22
**Context**: Building pg_arrow, a Rust library to read PostgreSQL data files directly and convert to Apache Arrow format. Initial page header parsing is complete.

## Summary

Comprehensive survey of advanced Arrow concepts (schema metadata, dictionary encoding, extension types), PostgreSQL-specific challenges (TOAST, MVCC, HOT chains, alignment), performance optimization techniques, and analysis of how similar projects (DuckDB postgres_scanner, pg_analytics) solve these problems.

---

## Techniques Evaluated

### 1. Arrow Schema Metadata
- **Description**: Store PostgreSQL-specific metadata (type OIDs, typmod, relation info) in Arrow schema for round-trip fidelity
- **Pros**:
  - Preserves full PostgreSQL type information
  - Enables writing back to PostgreSQL
  - Self-documenting output
- **Cons**:
  - Non-standard metadata keys
  - Increased schema size
- **Verdict**: ✅ Chosen - Essential for PostgreSQL compatibility

**Implementation example**:
```rust
use arrow::datatypes::{Field, DataType, Schema};
use std::collections::HashMap;

let field = Field::new("id", DataType::Int32, false)
    .with_metadata(HashMap::from([
        ("pg:typoid".to_string(), "23".to_string()),
        ("pg:typmod".to_string(), "-1".to_string()),
        ("pg:attnum".to_string(), "1".to_string()),
    ]));

let schema = Schema::new(vec![field])
    .with_metadata(HashMap::from([
        ("pg:oid".to_string(), "16384".to_string()),
        ("pg:relname".to_string(), "users".to_string()),
    ]));
```

---

### 2. Dictionary Encoding
- **Description**: Store low-cardinality string columns as indices into a dictionary of unique values
- **Pros**:
  - 10-100x memory reduction for low-cardinality columns
  - Faster comparisons and grouping
  - Standard Arrow feature
- **Cons**:
  - Overhead for high-cardinality columns
  - Dictionary building cost
  - Cross-batch dictionary coordination
- **Best suited for**: Status fields, country codes, categories
- **Used by**: DuckDB (adaptive), ClickHouse, Parquet
- **Verdict**: ✅ Chosen - Use adaptive heuristic (dictionary if `unique_count/total < 0.1`)

**Implementation strategy**:
```rust
use std::collections::HashMap;

struct ColumnStats {
    unique_values: HashMap<String, usize>,
    total_count: usize,
}

impl ColumnStats {
    fn should_use_dictionary(&self) -> bool {
        let cardinality_ratio = self.unique_values.len() as f64 / self.total_count as f64;
        cardinality_ratio < 0.1 // Use dictionary if < 10% unique
    }
}
```

---

### 3. Extension Types for PostgreSQL-Specific Types
- **Description**: Use Arrow extension types to represent NUMERIC, UUID, INET, geometric types, etc.
- **Pros**:
  - Type-safe representation
  - Metadata preserved
  - Arrow ecosystem compatible
- **Cons**:
  - Consumers must understand extensions
  - Complex implementation per type
- **Key mappings**:
  - UUID → FixedSizeBinary(16) + extension
  - NUMERIC → Decimal128 (if precision ≤ 38) or Utf8 + extension
  - INET/CIDR → Utf8 or custom extension
  - JSON/JSONB → Utf8 (consider LargeUtf8 for JSONB)
- **Verdict**: ✅ Chosen - Required for type fidelity

**Example**:
```rust
// UUID extension type
let uuid_type = DataType::Extension(
    "postgresql.uuid".to_string(),
    Box::new(DataType::FixedSizeBinary(16)),
    None,
);

// NUMERIC with precision > 38 (Decimal128 limit)
let high_precision_numeric = DataType::Extension(
    "postgresql.numeric".to_string(),
    Box::new(DataType::Utf8),
    Some(serde_json::json!({
        "precision": 100,
        "scale": 20
    }).to_string()),
);
```

---

### 4. Memory Mapping vs Buffered I/O

| Aspect | mmap() | Buffered read() |
|--------|--------|-----------------|
| **Kernel caching** | ✅ Automatic | Manual |
| **Large files (>RAM)** | ✅ Supported | ✅ Supported |
| **Predictability** | ❌ Page faults | ✅ Controlled |
| **Safety** | ❌ SIGBUS risk | ✅ Error handling |
| **Live databases** | ❌ Unsafe | ⚠️ Possible with care |

**Verdict**: Use buffered I/O initially for safety; mmap as optimization for static files

---

### 5. MVCC Visibility Handling Options

#### Option A: Ignore visibility (unsafe snapshot)
- ❌ **Rejected** - Inconsistent results, uncommitted data

#### Option B: Hint-bit-only visibility
- **Description**: Use `HEAP_XMIN_COMMITTED`/`INVALID` bits, skip ambiguous tuples
- **Verdict**: 🔮 Consider Later - Useful for approximate reads

#### Option C: Full MVCC with CLOG
- **Description**: Implement complete visibility rules including CLOG consultation
- **Verdict**: 🔮 Consider Later - Needed for production correctness

#### Option D: Require stopped PostgreSQL
- **Description**: Only read from shut down databases
- **Pros**: No visibility concerns, all committed data is hinted
- **Cons**: Limited use cases
- **Verdict**: ✅ **Chosen for initial implementation**

**Visibility check code** (simplified):
```rust
const HEAP_XMIN_COMMITTED: u16 = 0x0100;
const HEAP_XMIN_INVALID: u16 = 0x0200;
const HEAP_XMAX_COMMITTED: u16 = 0x0400;
const HEAP_XMAX_INVALID: u16 = 0x0800;

fn is_tuple_visible_simple(t_infomask: u16) -> bool {
    // For stopped database with hint bits
    let xmin_committed = (t_infomask & HEAP_XMIN_COMMITTED) != 0;
    let xmin_invalid = (t_infomask & HEAP_XMIN_INVALID) != 0;
    let xmax_invalid = (t_infomask & HEAP_XMAX_INVALID) != 0;

    xmin_committed && !xmin_invalid && xmax_invalid
}
```

---

### 6. TOAST Handling Options

#### Option A: Skip TOAST columns
- ❌ **Rejected** - Data loss

#### Option B: Inline TOAST resolution
- **Description**: Resolve TOAST pointers during page scan
- **Pros**: Complete data, simple API
- **Cons**: Performance impact, requires TOAST table access
- **Verdict**: ✅ **Chosen** - Required for correctness

#### Option C: Lazy TOAST resolution
- **Description**: Return TOAST pointers, resolve on demand
- **Verdict**: 🔮 Consider Later - Optimization for selective queries

**TOAST detection**:
```rust
const VARTAG_EXTERNAL: u8 = 0x01;
const VARTAG_INDIRECT: u8 = 0x02;
const VARTAG_COMPRESSED: u8 = 0x03;

fn is_toast_pointer(varlena_data: &[u8]) -> bool {
    if varlena_data.len() < 1 {
        return false;
    }

    let tag = varlena_data[0];
    matches!(tag, VARTAG_EXTERNAL | VARTAG_INDIRECT)
}
```

---

### 7. RecordBatch Sizing Strategy

**Analysis**:
- Too small (<1024 rows): High overhead, poor vectorization
- Too large (>100K rows): Memory pressure, streaming delays
- Sweet spot: 8192-65536 rows OR 256KB-1MB data

**Verdict**: ✅ Adaptive sizing: `target_batch_bytes / avg_row_bytes`, clamped to [1024, 65536]

```rust
struct BatchSizer {
    target_bytes: usize, // 256KB default
    min_rows: usize,     // 1024
    max_rows: usize,     // 65536
}

impl BatchSizer {
    fn calculate_batch_size(&self, avg_row_bytes: usize) -> usize {
        if avg_row_bytes == 0 {
            return self.min_rows;
        }

        let target_rows = self.target_bytes / avg_row_bytes;
        target_rows.clamp(self.min_rows, self.max_rows)
    }
}
```

---

### 8. Parallel Processing Strategies

#### Page-level parallelism
- **Pros**: Good for many small pages
- **Cons**: Merge overhead, memory per thread
- **Verdict**: ✅ **Chosen** - Use rayon for page iteration

```rust
use rayon::prelude::*;

fn convert_pages_parallel(pages: Vec<Page>) -> Result<Vec<RecordBatch>> {
    pages
        .par_iter()
        .map(|page| convert_page_to_batch(page))
        .collect::<Result<Vec<_>>>()
}
```

#### Column-level parallelism
- **Verdict**: 🔮 Consider Later - Optimization for wide tables

---

### 9. Null Bitmap Conversion
- **Implementation note**: PostgreSQL and Arrow both use LSB-first bit packing with `1 = valid`
- **Verdict**: ✅ Direct conversion possible

```rust
fn convert_null_bitmap(pg_bitmap: &[u8], natts: usize) -> Vec<u8> {
    // PostgreSQL null bitmap: 1 = null (inverted!)
    // Arrow validity bitmap: 1 = valid

    let num_bytes = (natts + 7) / 8;
    let mut arrow_bitmap = vec![0u8; num_bytes];

    for i in 0..natts {
        let pg_is_null = (pg_bitmap[i / 8] & (1 << (i % 8))) != 0;
        if !pg_is_null {
            arrow_bitmap[i / 8] |= 1 << (i % 8);
        }
    }

    arrow_bitmap
}
```

---

### 10. HOT Chain Following
- **Description**: Follow Heap-Only-Tuple chains to find visible tuple version
- **Verdict**: ✅ **Chosen** - Required for updated tables

```rust
const HEAP_HOT_UPDATED: u16 = 0x4000;
const HEAP_ONLY_TUPLE: u16 = 0x8000;

fn follow_hot_chain(page: &Page, initial_offset: u16) -> Option<u16> {
    let mut current_offset = initial_offset;

    loop {
        let tuple = page.get_tuple(current_offset)?;
        let t_infomask = tuple.t_infomask;

        if (t_infomask & HEAP_HOT_UPDATED) == 0 {
            // End of chain
            return Some(current_offset);
        }

        // Follow ctid to next version
        let (block, offset) = tuple.t_ctid;
        if block != page.block_number {
            // Chain left the page
            return Some(current_offset);
        }

        current_offset = offset;
    }
}
```

---

## PostgreSQL-Specific Implementation Details

### Tuple Header Structure

```rust
#[repr(C)]
struct HeapTupleHeaderData {
    t_xmin: u32,      // Creating transaction
    t_xmax: u32,      // Deleting transaction
    t_cid_xvac: u32,  // Command ID or vacuum XID
    t_ctid: ItemPointerData, // (block: u32, offset: u16)
    t_infomask2: u16, // Flags + natts
    t_infomask: u16,  // Flags
    t_hoff: u8,       // Offset to data
    // Null bitmap follows if HEAP_HASNULL set
}

// Key constants
const HEAP_HASNULL: u16 = 0x0001;
const HEAP_HASVARWIDTH: u16 = 0x0002;
const HEAP_HASEXTERNAL: u16 = 0x0004;  // Has TOAST pointer
const HEAP_XMIN_COMMITTED: u16 = 0x0100;
const HEAP_XMIN_INVALID: u16 = 0x0200;
const HEAP_XMAX_COMMITTED: u16 = 0x0400;
const HEAP_XMAX_INVALID: u16 = 0x0800;
const HEAP_HOT_UPDATED: u16 = 0x4000;
const HEAP_ONLY_TUPLE: u16 = 0x8000;
```

### Alignment Handling

```rust
fn align_offset(offset: usize, alignment: usize) -> usize {
    (offset + alignment - 1) & !(alignment - 1)
}

fn read_attribute_with_alignment(
    data: &[u8],
    mut offset: usize,
    attr: &PgAttribute,
) -> (usize, &[u8]) {
    // Apply alignment for non-varlena types
    if attr.attlen > 0 && attr.attalign > 1 {
        offset = align_offset(offset, attr.attalign as usize);
    }

    // Read the value
    let value = match attr.attlen {
        len if len > 0 => &data[offset..offset + len as usize],
        -1 => read_varlena(data, offset),
        -2 => read_cstring(data, offset),
        _ => panic!("Invalid attlen"),
    };

    let next_offset = offset + value.len();
    (next_offset, value)
}
```

### NUMERIC Conversion

PostgreSQL NUMERIC uses base-10000 digits:

```rust
const NBASE: i16 = 10000;

struct NumericVar {
    ndigits: i16,  // Number of base-10000 digits
    weight: i16,   // Position of first digit (10000^weight)
    sign: i16,     // 0x0000 = positive, 0x4000 = negative
    dscale: i16,   // Display scale (decimal places)
    digits: Vec<i16>, // Base-10000 digits
}

fn pg_numeric_to_arrow_decimal128(numeric_data: &[u8]) -> Result<i128> {
    let ndigits = read_i16(&numeric_data[0..2]);
    let weight = read_i16(&numeric_data[2..4]);
    let sign = read_i16(&numeric_data[4..6]);
    let dscale = read_i16(&numeric_data[6..8]);

    let mut result: i128 = 0;
    let digits = &numeric_data[8..];

    // Convert base-10000 to base-10
    for i in 0..ndigits {
        let digit = read_i16(&digits[(i * 2) as usize..((i + 1) * 2) as usize]) as i128;
        result = result * (NBASE as i128) + digit;
    }

    // Adjust for scale: multiply by 10^dscale
    let scale_factor = 10_i128.pow(dscale as u32);
    result *= scale_factor;

    // Apply sign
    if sign == 0x4000 {
        result = -result;
    }

    Ok(result)
}
```

---

## Key Papers & Resources

### Foundational
- "The Design and Implementation of Modern Column-Oriented Database Systems" - Abadi, Boncz, et al., Foundations and Trends in Databases 2013
- "MonetDB/X100: Hyper-Pipelining Query Execution" - Boncz et al., CIDR 2005
- "Apache Arrow and the Future of Data Processing" - Arrow PMC, 2022

### PostgreSQL Internals
- PostgreSQL Documentation: [Database Physical Storage](https://www.postgresql.org/docs/current/storage-page-layout.html)
- "Inside the PostgreSQL Tuple Header" - Bruce Momjian
- PostgreSQL source: `src/include/storage/bufpage.h`, `src/include/access/htup_details.h`

### Performance
- "Everything You Always Wanted to Know About Compiled and Vectorized Queries" - Kersten et al., VLDB 2018
- "Arrow Flight: A Fast, Large-Scale Data Transport" - SIGMOD 2019

### Related Projects
- [DuckDB postgres_scanner](https://github.com/duckdb/postgres_scanner)
- [pg_analytics (ParadeDB)](https://github.com/paradedb/paradedb)
- [pgrx (Rust PostgreSQL extensions)](https://github.com/pgcentralfoundation/pgrx)
- [arrow-rs](https://github.com/apache/arrow-rs)

---

## Architectural Decisions

### 1. Storage Access
**Decision**: Direct file reading (not protocol-based like DuckDB scanner)
- ✅ Enables offline/backup access
- ✅ No PostgreSQL server needed
- ❌ Must handle MVCC and TOAST ourselves

### 2. Initial Scope
**Decision**: Require PostgreSQL to be stopped
- ✅ Simplifies visibility handling
- ✅ All committed data will have hint bits set
- ❌ Limited to offline analysis

### 3. TOAST Handling
**Decision**: Implement full resolution
- ✅ Required for data completeness
- 🔮 Defer lazy resolution as optimization

### 4. Batch Sizing
**Decision**: Adaptive based on row width
- Target: 256KB batches
- Clamp rows: [1024, 65536]

### 5. Type Mapping
**Decision**: Use extension types for PostgreSQL-specific types
- Preserve type OIDs in metadata
- Fall back to string for unsupported precisions

---

## Implementation Roadmap

### Short-term (Next phases)
- [ ] Complete tuple header parsing
- [ ] Implement fixed-width type decoders (int, float, bool)
- [ ] Implement varlena parsing (strings)
- [ ] Build Arrow RecordBatch construction

### Medium-term
- [ ] TOAST pointer detection and resolution
- [ ] Array type support
- [ ] NUMERIC/Decimal conversion
- [ ] Parallel page scanning

### Long-term / Research needed
- [ ] Full MVCC implementation with CLOG
- [ ] Streaming reads from live databases
- [ ] Predicate pushdown optimization
- [ ] Arrow Flight service wrapper
- [ ] Delta/incremental conversion using WAL

### Performance optimizations to investigate
- [ ] SIMD for null bitmap processing
- [ ] io_uring for async page reads (Linux)
- [ ] Memory arena allocation for tuple parsing
- [ ] Vectorized type decoding

### PostgreSQL version compatibility
- [ ] Test page format differences across versions 12-17
- [ ] Handle version-specific TOAST compression (pglz vs lz4 in PG14+)
- [ ] Handle NUMERIC infinity values (PG14+)

---

## Comparison with Existing Projects

### DuckDB postgres_scanner vs pg_arrow

| Aspect | DuckDB Scanner | pg_arrow |
|--------|----------------|----------|
| **Access method** | Protocol (libpq) | Direct file |
| **PostgreSQL state** | Must be running | Can be stopped |
| **MVCC** | Delegated to PG | Must implement |
| **TOAST** | Delegated to PG | Must implement |
| **Performance** | Network overhead | Direct I/O |
| **Use cases** | Live queries | Backup analysis, forensics |
| **Complexity** | Lower | Higher |

### pg_analytics (ParadeDB) Approach
- Uses PostgreSQL extension interface (pgrx)
- Converts on-the-fly within PostgreSQL
- Has access to PostgreSQL internals
- Different use case than pg_arrow

---

## See Also

- [Arrow Format](@/notes/database/arrow_format.md) — Foundational Arrow memory layout this integration targets
- [WAL-Based Incremental Conversion](@/notes/database/wal_incremental_conversion.md) — Extends pg_arrow with live CDC capabilities using PostgreSQL WAL
- [HyPer/Umbra/CedarDB](@/notes/database/hyper_umbra_cedardb.md) — Columnar execution engines relevant to Arrow conversion performance
- [Rust Low-Level Programming](@/notes/programming/rust_low_level.md) — Unsafe Rust patterns used in pg_arrow's direct file access and FFI

---

*Last updated: 2026-01-22*
