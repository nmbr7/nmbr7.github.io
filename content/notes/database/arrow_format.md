+++
title = "Arrow Format"
date = 2026-02-10
[taxonomies]
tags = ["columnar", "arrow", "ipc", "memory-layout", "zero-copy", "simd", "postgresql"]
+++


# Research Notes

## 2026-01-22 - Apache Arrow Format Deep Dive

### Context
Building pg_arrow - a PostgreSQL to Apache Arrow converter in Rust. This research establishes foundational knowledge of the Arrow format to guide implementation decisions for converting PostgreSQL heap tuples to Arrow columnar format.

### Summary
Apache Arrow is a language-independent columnar memory format designed for zero-copy data sharing, O(1) random access, and SIMD-friendly operations. Understanding its precise memory layout is critical for correct and efficient PostgreSQL data conversion.

---

## Arrow Format Fundamentals

### Design Principles
1. **Zero-Copy Data Sharing**: No serialization between processes/languages
2. **O(1) Random Access**: Any element accessible via offset calculation
3. **SIMD-Friendly Layout**: Aligned, contiguous buffers enable vectorized ops
4. **Language-Independent**: Same binary format across C++, Rust, Python, Java, Go
5. **Self-Describing**: Schema embedded in data

### Key Data Structures

#### RecordBatch
- Core unit: collection of equal-length arrays with shared schema
- Contains: schema, columns (ArrayData), num_rows

#### Array Buffers
Every Arrow array has 1+ buffers:
1. **Validity Buffer** (optional): LSB-packed bitmap for nulls
2. **Data Buffer(s)**: Actual values
3. **Offset Buffer** (variable-length types): Start positions

### Memory Alignment
- **Minimum**: 8-byte alignment for all buffers
- **Recommended**: 64-byte for SIMD (AVX-512, cache line)
- **Padding**: Zero-pad to maintain alignment

---

## Type-Specific Layouts

### Primitive Types (Fixed-Width)
```
Int32Array [1, 2, 3, NULL, 5]:
  Validity: [0b00010111]  (LSB: bits 0,1,2,4 valid; bit 3 null)
  Data:     [01 00 00 00][02 00 00 00][03 00 00 00][XX XX XX XX][05 00 00 00]
            (little-endian, 4 bytes each)
```

### Boolean Arrays
- Bit-packed: 1 bit per value (vs PostgreSQL's 1 byte)
- LSB ordering within each byte

### Variable-Length (Strings, Binary)
```
StringArray ["hello", "world", "!"]:
  Offsets (n+1 elements): [0, 5, 10, 11]
  Data: "helloworld!"
```
- **Critical**: Offsets array has n+1 elements for n strings
- **Large variants**: LargeUtf8/LargeBinary use Int64 offsets (> 2GB)

### Nested Types

#### List<T>
```
List<Int32> [[1,2], [3], [4,5,6]]:
  Offsets: [0, 2, 3, 6]
  Child Int32Array: [1, 2, 3, 4, 5, 6]
```

#### Struct
- Multiple child arrays with shared validity
- Child values undefined when parent struct is null

### Dictionary Encoding
```
Original: ["red", "blue", "red", "red", "blue"]
Encoded:
  Dictionary: ["red", "blue"]
  Indices:    [0, 1, 0, 0, 1]
```
- Excellent for low-cardinality string columns
- 10-100x space savings possible

---

## PostgreSQL to Arrow Type Mapping

| PostgreSQL | Arrow | Notes |
|------------|-------|-------|
| `smallint` | `Int16` | Direct |
| `integer` | `Int32` | Direct |
| `bigint` | `Int64` | Direct |
| `real` | `Float32` | IEEE 754 |
| `double precision` | `Float64` | IEEE 754 |
| `numeric(p,s)` | `Decimal128(p,s)` | Convert from base-10000 |
| `boolean` | `Boolean` | Pack 8:1 |
| `text/varchar` | `Utf8` | Validate/transcode UTF-8 |
| `bytea` | `Binary` | Direct |
| `date` | `Date32` | Add 10957 days (epoch diff) |
| `timestamp` | `Timestamp(Microsecond)` | Add epoch offset |
| `timestamptz` | `Timestamp(Microsecond, "UTC")` | Add epoch offset |
| `uuid` | `FixedSizeBinary(16)` | Or Extension type |
| `array[]` | `List<T>` | Recursive |
| `composite` | `Struct` | Map fields |

### Epoch Conversion Constants
```rust
const POSTGRES_EPOCH_DAYS: i32 = 10957;  // 1970-01-01 to 2000-01-01
const POSTGRES_EPOCH_MICROS: i64 = 946_684_800_000_000;
```

---

## IPC/File Format

### Streaming Format
```
Schema Message
RecordBatch Message 1
RecordBatch Message 2
...
End of Stream marker (0xFFFFFFFF 0x00000000)
```

### File Format (Feather v2)
```
ARROW1 (magic, 6 bytes)
Padding (2 bytes)
Schema Message
RecordBatch 1
RecordBatch 2
...
Footer (schema copy, batch locations, dictionary locations)
Footer Length (4 bytes)
ARROW1 (magic, 6 bytes)
```

### Message Structure
- Continuation marker (4B): 0xFFFFFFFF
- Metadata length (4B)
- Metadata (FlatBuffers)
- Padding to 8-byte boundary
- Message body (buffer data)

### Buffer Ordering
Depth-first pre-order traversal of schema fields.

---

## Performance Considerations

### Batch Size
- **Too small** (< 1K rows): Metadata overhead dominates
- **Too large** (> 1M rows): Memory pressure, cache thrashing
- **Optimal**: 10K-100K rows
- **Industry examples**: DuckDB uses 2048, DataFusion defaults to 8192

### Optimizations
1. **Buffer reuse**: Pool allocations between batches
2. **SIMD null counting**: Use popcnt for validity bitmaps
3. **Parallel page processing**: Pages are independent units
4. **Streaming for large tables**: Don't hold all data in memory

### Compression (IPC)
- LZ4_FRAME: Fast, moderate ratio
- ZSTD: Better ratio, still fast
- Per-buffer compression

---

## Common Pitfalls

### 1. Final Partial Batch
Always finalize remaining rows after loop.

### 2. Null Bitmap Bit Order
Arrow uses LSB ordering: `bitmap[i/8] |= 1 << (i%8)`

### 3. Offset Array Length
n+1 offsets for n elements (final offset = total length).

### 4. Epoch Mismatch
PostgreSQL: 2000-01-01, Arrow: 1970-01-01. Always convert.

### 5. Struct Null Semantics
Child values undefined when parent struct is null. Check parent first.

### 6. IPC Alignment
Pad all buffers to 8-byte boundary when writing.

### 7. String Size Overflow
Use LargeUtf8 for > 2GB total string data.

### 8. Dictionary Index Validation
Verify all indices are within dictionary bounds.

---

## Key Papers & Resources

### Foundational
- "Apache Arrow and the '10 Things I Hate About pandas'" - Wes McKinney, 2017
- "The Design and Implementation of Modern Column-Oriented Database Systems" - Abadi, Madden, Hachem, Foundations and Trends in Databases, 2013

### Arrow Specifications
- Columnar Format: https://arrow.apache.org/docs/format/Columnar.html
- IPC Format: https://arrow.apache.org/docs/format/IPC.html
- FlatBuffers schemas: format/Schema.fbs, Message.fbs, File.fbs

### PostgreSQL References
- Page Layout: https://www.postgresql.org/docs/current/storage-page-layout.html
- Heap Tuple: src/include/access/htup_details.h
- Numeric: src/backend/utils/adt/numeric.c
- TOAST: https://www.postgresql.org/docs/current/storage-toast.html

### Performance Research
- "Efficiently Compiling Efficient Query Plans for Modern Hardware" - Neumann, VLDB 2011
- "MonetDB/X100: Hyper-Pipelining Query Execution" - Boncz et al., CIDR 2005

### Related Projects
- arrow-rs: https://github.com/apache/arrow-rs
- DuckDB postgres scanner: https://github.com/duckdb/postgres_scanner
- pg_analytics: https://github.com/paradedb/paradedb/tree/main/pg_analytics

---

## Implementation Decision: Chosen Approach

### Architecture
```
PostgreSQL Pages -> Tuple Parser -> Type Converter -> Batch Builder -> IPC Writer
```

### Key Decisions
1. **Batch size**: Start with 8192 rows, tune based on benchmarks
2. **String handling**: Validate UTF-8, use LargeUtf8 if needed
3. **Dictionary encoding**: Auto-detect for string columns with < 10% cardinality
4. **Parallelism**: Page-level parallel processing
5. **Memory management**: Stream to IPC for tables > available memory

### Future Considerations
- [ ] TOAST table handling for large values
- [ ] Compression support (LZ4/ZSTD)
- [ ] Extension types for UUID, JSON, etc.
- [ ] Multi-version PostgreSQL compatibility testing
- [ ] Predicate pushdown for filtered reads

---

## See Also

- [Arrow PostgreSQL Integration](@/notes/database/arrow_postgresql_integration.md) — Covers pg_arrow architecture for converting PostgreSQL data to Arrow format
- [WAL-Based Incremental Conversion](@/notes/database/wal_incremental_conversion.md) — CDC pipeline that produces Arrow output from PostgreSQL WAL
- [Join Algorithms](@/notes/database/join_algorithms.md) — Vectorized join execution benefits directly from Arrow's columnar layout
- [Data Structures](@/notes/programming/data_structures.md) — Dictionary encoding, Bloom filters, and bitmap structures used within Arrow arrays
