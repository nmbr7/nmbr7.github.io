+++
title = "WAL Incremental Conversion"
date = 2026-02-10
[taxonomies]
tags = ["wal", "cdc", "arrow", "postgresql", "streaming", "replication", "logical-decoding"]
+++


# WAL-Based Incremental Conversion for pg_arrow

**Date**: 2026-01-22
**Context**: Exploring how PostgreSQL's Write-Ahead Log (WAL) can enable consistent reads from running databases and incremental Arrow conversion.

## Summary

PostgreSQL's WAL provides a complete, ordered log of all database changes. By leveraging WAL, pg_arrow could:
1. Read from live databases with consistent snapshots
2. Perform incremental/delta conversions
3. Implement Change Data Capture (CDC) to Arrow
4. Enable point-in-time recovery and time-travel queries

This research evaluates two approaches: logical replication (recommended) and physical WAL parsing (advanced).

---

## Current Limitation

**pg_arrow Phase 1 design**: Requires stopped PostgreSQL
- Reading raw data files while PostgreSQL is running → inconsistent snapshots
- Buffer cache vs. disk state mismatches
- No visibility into in-flight transactions

**WAL solves this by providing**:
- Consistent timeline of all changes
- Transaction boundaries (BEGIN, COMMIT, ABORT)
- Ability to reconstruct database state at any LSN (Log Sequence Number)

---

## WAL Fundamentals

### Log Sequence Number (LSN)

```rust
/// PostgreSQL LSN: 64-bit position in WAL stream
/// Format: XXXXXXXX/YYYYYYYY (xlogid/xrecoff)
#[derive(Debug, Copy, Clone, PartialEq, Eq, PartialOrd, Ord)]
struct LSN(u64);

impl LSN {
    fn new(xlogid: u32, xrecoff: u32) -> Self {
        LSN(((xlogid as u64) << 32) | (xrecoff as u64))
    }

    fn xlogid(&self) -> u32 {
        (self.0 >> 32) as u32
    }

    fn xrecoff(&self) -> u32 {
        self.0 as u32
    }

    fn from_string(s: &str) -> Result<Self> {
        // Parse "16/B374D848" format
        let parts: Vec<&str> = s.split('/').collect();
        let xlogid = u32::from_str_radix(parts[0], 16)?;
        let xrecoff = u32::from_str_radix(parts[1], 16)?;
        Ok(LSN::new(xlogid, xrecoff))
    }
}
```

### WAL Segments

```
WAL files: pg_wal/000000010000000000000001 (16MB segments by default)
           └─ timeline ─┘└─ xlogid ──┘└─ segment ─┘

Each segment contains WAL records from LSN range
```

---

## Approach 1: Logical Replication (Recommended)

### Overview

Use PostgreSQL's **logical decoding** infrastructure instead of parsing physical WAL:
- PostgreSQL decodes WAL for you
- Returns structured change events
- Stable API across versions
- Filters by publication (tables/schemas)

### Architecture

```
PostgreSQL Database
  ├─ Replication Slot (tracks consumer position)
  ├─ Publication (defines what to replicate)
  └─ Logical Decoding Plugin (pgoutput, wal2json, etc.)
         ↓
    Change Stream (INSERT/UPDATE/DELETE)
         ↓
    pg_arrow Converter
         ↓
    Arrow RecordBatch (delta)
```

### Setup

```sql
-- 1. Enable logical replication (postgresql.conf)
-- wal_level = logical
-- max_replication_slots = 10

-- 2. Create replication slot
SELECT pg_create_logical_replication_slot('pg_arrow_slot', 'pgoutput');

-- 3. Create publication for tables to track
CREATE PUBLICATION pg_arrow_pub FOR ALL TABLES;
-- Or specific tables:
-- CREATE PUBLICATION pg_arrow_pub FOR TABLE users, orders;

-- 4. Stream changes
SELECT * FROM pg_logical_slot_get_changes(
    'pg_arrow_slot',
    NULL,  -- upto_lsn (NULL = all available)
    NULL,  -- upto_nchanges (NULL = no limit)
    'proto_version', '1',
    'publication_names', 'pg_arrow_pub'
);
```

### Implementation

```rust
use postgres::{Client, NoTls};

struct LogicalReplicationReader {
    client: Client,
    slot_name: String,
    publication_name: String,
    last_lsn: Option<LSN>,
}

impl LogicalReplicationReader {
    fn new(conn_string: &str, slot_name: &str) -> Result<Self> {
        let client = Client::connect(conn_string, NoTls)?;

        // Create slot if not exists
        client.execute(
            "SELECT pg_create_logical_replication_slot($1, 'pgoutput')",
            &[&slot_name],
        )?;

        Ok(Self {
            client,
            slot_name: slot_name.to_string(),
            publication_name: "pg_arrow_pub".to_string(),
            last_lsn: None,
        })
    }

    fn fetch_changes(&mut self) -> Result<Vec<ChangeEvent>> {
        let rows = self.client.query(
            "SELECT lsn, xid, data FROM pg_logical_slot_get_changes($1, NULL, NULL, \
             'proto_version', '1', 'publication_names', $2)",
            &[&self.slot_name, &self.publication_name],
        )?;

        let mut events = Vec::new();
        for row in rows {
            let lsn: String = row.get(0);
            let xid: u32 = row.get(1);
            let data: &[u8] = row.get(2);

            // Parse pgoutput protocol message
            let event = self.parse_change_event(lsn, xid, data)?;
            events.push(event);
        }

        Ok(events)
    }

    fn parse_change_event(&self, lsn: String, xid: u32, data: &[u8]) -> Result<ChangeEvent> {
        // pgoutput protocol parsing
        // See: src/backend/replication/logical/proto.c
        match data[0] {
            b'B' => Ok(ChangeEvent::Begin { lsn: LSN::from_string(&lsn)?, xid }),
            b'C' => Ok(ChangeEvent::Commit { lsn: LSN::from_string(&lsn)?, xid }),
            b'I' => self.parse_insert(lsn, xid, &data[1..]),
            b'U' => self.parse_update(lsn, xid, &data[1..]),
            b'D' => self.parse_delete(lsn, xid, &data[1..]),
            _ => Err(anyhow!("Unknown message type: {}", data[0])),
        }
    }
}

#[derive(Debug)]
enum ChangeEvent {
    Begin { lsn: LSN, xid: u32 },
    Commit { lsn: LSN, xid: u32 },
    Insert { lsn: LSN, relation_id: u32, tuple: TupleData },
    Update { lsn: LSN, relation_id: u32, old_tuple: Option<TupleData>, new_tuple: TupleData },
    Delete { lsn: LSN, relation_id: u32, old_tuple: TupleData },
}
```

### Using wal2json Plugin (Alternative)

```sql
-- Create slot with wal2json plugin
SELECT pg_create_logical_replication_slot('pg_arrow_slot', 'wal2json');

-- Fetch changes as JSON
SELECT data FROM pg_logical_slot_get_changes('pg_arrow_slot', NULL, NULL);
```

**Example output**:
```json
{
  "change": [{
    "kind": "insert",
    "schema": "public",
    "table": "users",
    "columnnames": ["id", "name", "email"],
    "columntypes": ["integer", "text", "text"],
    "columnvalues": [1, "Alice", "alice@example.com"]
  }]
}
```

**Pros**: Easier parsing (JSON)
**Cons**: Less efficient than binary pgoutput

---

## Approach 2: Physical WAL Parsing (Advanced)

### Overview

Directly read and parse WAL segment files:
- Maximum performance (no PostgreSQL overhead)
- Works offline (from archived WAL)
- Full control over decoding

### WAL Record Structure

```rust
/// WAL record header (common to all record types)
#[repr(C)]
struct XLogRecord {
    xl_tot_len: u32,      // Total length including header
    xl_xid: u32,          // Transaction ID
    xl_prev: u64,         // LSN of previous record
    xl_info: u8,          // Record type + flags
    xl_rmid: u8,          // Resource manager ID
    xl_crc: u32,          // CRC of record
    // Variable-length data follows
}

const RM_HEAP_ID: u8 = 10;        // Heap operations
const RM_HEAP2_ID: u8 = 11;       // Heap2 operations (VACUUM, etc.)
const RM_BTREE_ID: u8 = 2;        // B-tree operations

// Heap operation types (xl_info & 0x0F)
const XLOG_HEAP_INSERT: u8 = 0x00;
const XLOG_HEAP_DELETE: u8 = 0x10;
const XLOG_HEAP_UPDATE: u8 = 0x20;
const XLOG_HEAP_HOT_UPDATE: u8 = 0x30;
```

### Heap Insert Record

```rust
/// xl_heap_insert: WAL record for INSERT
#[repr(C)]
struct XlHeapInsert {
    offnum: u16,          // Offset number in page
    flags: u8,            // Flags
    // Followed by:
    // - HeapTupleHeaderData
    // - Tuple data
}

const XLH_INSERT_ALL_VISIBLE_CLEARED: u8 = 0x01;
const XLH_INSERT_LAST_IN_MULTI: u8 = 0x02;
const XLH_INSERT_IS_SPECULATIVE: u8 = 0x04;
const XLH_INSERT_CONTAINS_NEW_TUPLE: u8 = 0x08;

fn parse_heap_insert(record: &XLogRecord, data: &[u8]) -> Result<HeapInsertEvent> {
    let xl_info = record.xl_info & 0x0F;
    if xl_info != XLOG_HEAP_INSERT {
        return Err(anyhow!("Not an INSERT record"));
    }

    let insert = unsafe { &*(data.as_ptr() as *const XlHeapInsert) };
    let tuple_data = &data[size_of::<XlHeapInsert>()..];

    Ok(HeapInsertEvent {
        xid: record.xl_xid,
        offnum: insert.offnum,
        tuple: parse_heap_tuple_data(tuple_data)?,
    })
}
```

### Full-Page Images (FPI)

After a checkpoint, PostgreSQL writes full pages to WAL:

```rust
struct XLogPageHeader {
    xlp_magic: u16,       // 0xD097 for PG 9.3+
    xlp_info: u16,        // Flags
    xlp_tli: u32,         // Timeline ID
    xlp_pageaddr: u64,    // LSN of page start
}

const XLP_FIRST_IS_CONTRECORD: u16 = 0x0001;
const XLP_LONG_HEADER: u16 = 0x0002;
const XLP_BKP_BLOCK_1: u16 = 0x0004;  // Has backup block 1
const XLP_BKP_BLOCK_2: u16 = 0x0008;  // Has backup block 2

fn has_full_page_image(record: &XLogRecord) -> bool {
    (record.xl_info & 0x80) != 0  // XLR_BKP_BLOCK flag
}
```

### WAL Reader Implementation

```rust
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};

struct WalReader {
    file: File,
    current_lsn: LSN,
}

impl WalReader {
    fn open(segment_path: &str, start_lsn: LSN) -> Result<Self> {
        let file = File::open(segment_path)?;
        let mut reader = Self {
            file,
            current_lsn: start_lsn,
        };
        reader.seek_to_lsn(start_lsn)?;
        Ok(reader)
    }

    fn seek_to_lsn(&mut self, lsn: LSN) -> Result<()> {
        let segment_size = 16 * 1024 * 1024; // 16MB
        let offset = (lsn.0 % segment_size) as u64;
        self.file.seek(SeekFrom::Start(offset))?;
        Ok(())
    }

    fn read_next_record(&mut self) -> Result<Option<WalRecord>> {
        // 1. Read XLogRecord header
        let mut header_buf = [0u8; size_of::<XLogRecord>()];
        if self.file.read_exact(&mut header_buf).is_err() {
            return Ok(None); // End of segment
        }

        let header = unsafe { &*(header_buf.as_ptr() as *const XLogRecord) };

        // 2. Validate CRC
        // ... CRC validation code ...

        // 3. Read record data
        let data_len = header.xl_tot_len as usize - size_of::<XLogRecord>();
        let mut data = vec![0u8; data_len];
        self.file.read_exact(&mut data)?;

        // 4. Parse based on resource manager
        let record = match header.xl_rmid {
            RM_HEAP_ID => self.parse_heap_record(header, &data)?,
            RM_HEAP2_ID => self.parse_heap2_record(header, &data)?,
            _ => WalRecord::Other,
        };

        self.current_lsn = LSN(self.current_lsn.0 + header.xl_tot_len as u64);
        Ok(Some(record))
    }

    fn parse_heap_record(&self, header: &XLogRecord, data: &[u8]) -> Result<WalRecord> {
        match header.xl_info & 0x0F {
            XLOG_HEAP_INSERT => {
                let event = parse_heap_insert(header, data)?;
                Ok(WalRecord::Insert(event))
            }
            XLOG_HEAP_DELETE => {
                let event = parse_heap_delete(header, data)?;
                Ok(WalRecord::Delete(event))
            }
            XLOG_HEAP_UPDATE | XLOG_HEAP_HOT_UPDATE => {
                let event = parse_heap_update(header, data)?;
                Ok(WalRecord::Update(event))
            }
            _ => Ok(WalRecord::Other),
        }
    }
}

enum WalRecord {
    Insert(HeapInsertEvent),
    Update(HeapUpdateEvent),
    Delete(HeapDeleteEvent),
    Other,
}
```

---

## Use Cases Enabled

### 1. Consistent Snapshot from Live Database

```rust
/// Read database state as of specific LSN
struct ConsistentReader {
    base_reader: PageFileReader,
    wal_reader: LogicalReplicationReader,
    target_lsn: LSN,
}

impl ConsistentReader {
    fn read_table_at_lsn(&mut self, table_oid: u32, lsn: LSN) -> Result<Vec<RecordBatch>> {
        // 1. Get current database LSN
        let current_lsn = self.get_current_lsn()?;

        if lsn > current_lsn {
            return Err(anyhow!("Requested LSN is in the future"));
        }

        // 2. Read base pages
        let mut batches = self.base_reader.read_table(table_oid)?;

        // 3. Apply WAL changes from page LSN to target LSN
        let changes = self.wal_reader.get_changes_between(lsn, current_lsn)?;

        for change in changes {
            self.apply_change_to_batches(&mut batches, change)?;
        }

        Ok(batches)
    }

    fn get_current_lsn(&self) -> Result<LSN> {
        // SELECT pg_current_wal_lsn()
        unimplemented!()
    }
}
```

### 2. Incremental/Delta Conversion

```rust
struct IncrementalConverter {
    last_converted_lsn: LSN,
    wal_reader: LogicalReplicationReader,
}

impl IncrementalConverter {
    fn convert_delta(&mut self) -> Result<DeltaRecordBatch> {
        let current_lsn = self.get_current_lsn()?;

        // Get changes since last conversion
        let changes = self.wal_reader.get_changes_between(
            self.last_converted_lsn,
            current_lsn,
        )?;

        // Build Arrow batch with only changed rows
        let mut builder = DeltaRecordBatchBuilder::new();

        for change in changes {
            match change {
                ChangeEvent::Insert { tuple, .. } => {
                    builder.add_insert(tuple)?;
                }
                ChangeEvent::Update { new_tuple, .. } => {
                    builder.add_update(new_tuple)?;
                }
                ChangeEvent::Delete { old_tuple, .. } => {
                    builder.add_delete(old_tuple)?;
                }
                _ => {}
            }
        }

        self.last_converted_lsn = current_lsn;
        Ok(builder.finish()?)
    }
}

/// Arrow Delta format (inspired by Delta Lake)
struct DeltaRecordBatch {
    batch: RecordBatch,
    metadata: DeltaMetadata,
}

struct DeltaMetadata {
    base_lsn: LSN,
    target_lsn: LSN,
    operation_column: String,  // "_op": "I", "U", "D"
}
```

### 3. Change Data Capture (CDC) Stream

```rust
/// Stream changes as Arrow batches
struct CdcStreamer {
    wal_reader: LogicalReplicationReader,
    batch_builder: RecordBatchBuilder,
    batch_size: usize,
}

impl CdcStreamer {
    fn stream_changes(&mut self) -> impl Stream<Item = Result<RecordBatch>> {
        async_stream::stream! {
            loop {
                let changes = self.wal_reader.fetch_changes()?;

                for change in changes {
                    self.batch_builder.add_change(change)?;

                    if self.batch_builder.len() >= self.batch_size {
                        yield Ok(self.batch_builder.finish()?);
                        self.batch_builder.reset();
                    }
                }

                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }
    }
}

// Usage:
// let mut streamer = CdcStreamer::new(...);
// let mut stream = streamer.stream_changes();
// while let Some(batch) = stream.next().await {
//     write_to_arrow_file(batch?)?;
// }
```

### 4. Point-in-Time Recovery / Time Travel

```rust
/// Query database state at any historical LSN
struct TimeTravelQuery {
    converter: ConsistentReader,
}

impl TimeTravelQuery {
    fn query_at_timestamp(&self, timestamp: DateTime<Utc>) -> Result<Vec<RecordBatch>> {
        // 1. Map timestamp to LSN
        let lsn = self.timestamp_to_lsn(timestamp)?;

        // 2. Read state at that LSN
        self.converter.read_table_at_lsn(table_oid, lsn)
    }

    fn timestamp_to_lsn(&self, timestamp: DateTime<Utc>) -> Result<LSN> {
        // Query: SELECT pg_wal_lsn_from_time($1)
        // Or use archived WAL timestamp correlation
        unimplemented!()
    }
}
```

---

## Comparison: Logical Replication vs Physical WAL

| Aspect | Logical Replication | Physical WAL Parsing |
|--------|---------------------|----------------------|
| **Complexity** | ✅ Low (PostgreSQL does work) | ❌ High (manual parsing) |
| **Stability** | ✅ Stable API | ❌ Version-specific |
| **Performance** | ⚠️ Some overhead | ✅ Maximum performance |
| **Offline use** | ❌ Requires running PG | ✅ Works with archived WAL |
| **Filtering** | ✅ Publication-based | ⚠️ Manual filtering |
| **Error handling** | ✅ Better (PG validates) | ⚠️ Must handle corruption |
| **Learning curve** | ✅ Moderate | ❌ Steep |

---

## Implementation Roadmap

### Phase 1: Logical Replication Foundation (Recommended First)

**Milestone 1.1**: Basic logical decoding
- [ ] Create replication slot
- [ ] Fetch changes using pgoutput protocol
- [ ] Parse BEGIN/COMMIT/INSERT events
- [ ] Test with simple table

**Milestone 1.2**: Arrow conversion
- [ ] Convert INSERT events to Arrow RecordBatch
- [ ] Handle UPDATE events (as delete + insert)
- [ ] Handle DELETE events
- [ ] Test with basic types (int, text, bool)

**Milestone 1.3**: Production features
- [ ] LSN tracking and resumption
- [ ] Transaction batching (commit boundaries)
- [ ] Error handling and retry logic
- [ ] Performance benchmarking

### Phase 2: Advanced Features

**Milestone 2.1**: Incremental conversion
- [ ] Delta batch format design
- [ ] Merge strategy for deltas
- [ ] Compaction logic

**Milestone 2.2**: Snapshot consistency
- [ ] Combine base read + WAL replay
- [ ] Point-in-time snapshot API
- [ ] Test with concurrent writes

**Milestone 2.3**: Type support
- [ ] All PostgreSQL types
- [ ] TOAST value handling via logical decoding
- [ ] Array and composite types

### Phase 3: Physical WAL Parsing (Optional, Advanced)

**Milestone 3.1**: WAL segment reading
- [ ] Parse XLogRecord headers
- [ ] CRC validation
- [ ] Segment file navigation

**Milestone 3.2**: Heap record decoding
- [ ] HEAP_INSERT parsing
- [ ] HEAP_UPDATE parsing
- [ ] HEAP_DELETE parsing
- [ ] Full-page images

**Milestone 3.3**: Production hardening
- [ ] Multi-version PostgreSQL support
- [ ] Corruption detection and recovery
- [ ] Performance optimization

---

## Challenges & Mitigations

### Challenge 1: Transaction Boundaries

**Problem**: Changes arrive as individual operations, need to group by transaction

**Solution**:
```rust
struct TransactionBatcher {
    pending_txns: HashMap<u32, Vec<ChangeEvent>>,
}

impl TransactionBatcher {
    fn add_event(&mut self, event: ChangeEvent) {
        match event {
            ChangeEvent::Begin { xid, .. } => {
                self.pending_txns.insert(xid, Vec::new());
            }
            ChangeEvent::Commit { xid, .. } => {
                if let Some(changes) = self.pending_txns.remove(&xid) {
                    self.emit_batch(changes);
                }
            }
            _ => {
                // Add to pending transaction
                let xid = event.xid();
                self.pending_txns.entry(xid).or_default().push(event);
            }
        }
    }
}
```

### Challenge 2: Schema Changes

**Problem**: ALTER TABLE during replication

**Solution**:
```rust
// Track schema versions per LSN
struct SchemaRegistry {
    schemas: BTreeMap<LSN, Schema>,
}

// On DDL event, fetch new schema and cache
fn handle_ddl(&mut self, lsn: LSN, relation_id: u32) {
    let new_schema = self.fetch_schema_from_pg(relation_id)?;
    self.schemas.insert(lsn, new_schema);
}
```

### Challenge 3: Large Transactions

**Problem**: Transaction with millions of changes overwhelms memory

**Solution**:
```rust
// Spill to disk if transaction exceeds threshold
const MAX_MEMORY_PER_TXN: usize = 100 * 1024 * 1024; // 100MB

if txn_size > MAX_MEMORY_PER_TXN {
    self.spill_to_disk(xid)?;
}
```

### Challenge 4: Replication Lag

**Problem**: Consumer falls behind, replication slot grows unbounded

**Solution**:
```sql
-- Monitor slot lag
SELECT slot_name,
       pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) AS lag_bytes
FROM pg_replication_slots;

-- Set max_slot_wal_keep_size (PG 13+)
ALTER SYSTEM SET max_slot_wal_keep_size = '10GB';
```

---

## Testing Strategy

### Unit Tests

```rust
#[cfg(test)]
mod tests {
    #[test]
    fn test_lsn_parsing() {
        let lsn = LSN::from_string("16/B374D848").unwrap();
        assert_eq!(lsn.xlogid(), 0x16);
        assert_eq!(lsn.xrecoff(), 0xB374D848);
    }

    #[test]
    fn test_change_event_parsing() {
        // Mock pgoutput INSERT message
        let data = b"I\x00\x00\x40\x00...";
        let event = parse_change_event(data).unwrap();
        assert!(matches!(event, ChangeEvent::Insert { .. }));
    }
}
```

### Integration Tests

```rust
#[test]
fn test_incremental_conversion() {
    // 1. Setup test database
    let mut conn = setup_test_db();

    // 2. Insert initial data
    conn.execute("INSERT INTO test_table VALUES (1, 'Alice')", &[])?;

    // 3. Take initial snapshot
    let snapshot1 = converter.convert()?;

    // 4. Insert more data
    conn.execute("INSERT INTO test_table VALUES (2, 'Bob')", &[])?;

    // 5. Get delta
    let delta = converter.convert_delta()?;

    // 6. Verify delta contains only new row
    assert_eq!(delta.num_rows(), 1);
}
```

---

## Resources

### PostgreSQL Documentation
- [Logical Decoding](https://www.postgresql.org/docs/current/logicaldecoding.html)
- [Write-Ahead Logging](https://www.postgresql.org/docs/current/wal-internals.html)
- [Replication Protocol](https://www.postgresql.org/docs/current/protocol-replication.html)

### Source Code References
- `src/backend/replication/logical/` - Logical decoding
- `src/backend/access/transam/xlog.c` - WAL writing
- `src/include/access/xlog_internal.h` - WAL structures

### Tools
- **pg_waldump**: WAL file inspector
- **wal2json**: Logical decoding to JSON
- **pgoutput**: Standard logical replication protocol

### Related Projects
- [Debezium PostgreSQL Connector](https://github.com/debezium/debezium) - Production CDC
- [pglogical](https://github.com/2ndQuadrant/pglogical) - Logical replication
- [bottledwater-pg](https://github.com/confluentinc/bottledwater-pg) - WAL to Kafka

### Papers
- "Logical Replication in PostgreSQL" - Petr Jelinek, 2017
- "The Design of POSTGRES Storage System" - Stonebraker, 1987

---

## Recommended Implementation Order

1. **Start with logical replication + pgoutput** ✅
   - Lowest risk, fastest time-to-value
   - Learn the domain before tackling physical WAL

2. **Build incremental conversion** ⏭️
   - Enables live database use case
   - Foundation for CDC

3. **Add physical WAL parsing** (if needed) 🔮
   - Only if performance/offline requirements demand it
   - Significant complexity increase

---

## See Also

- [WAL, Torn Pages, and Disk Reliability](@/notes/database/wal_torn_pages.md) — Deep dive into WAL internals, crash recovery, and durability guarantees
- [Arrow Format](@/notes/database/arrow_format.md) — Target format for the conversion pipeline
- [Arrow PostgreSQL Integration](@/notes/database/arrow_postgresql_integration.md) — Companion doc covering direct file-based PostgreSQL-to-Arrow conversion
- [Kafka Internals](@/notes/distributed/kafka_internals.md) — Log-structured storage and CDC patterns relevant to WAL-based streaming
- [Filesystem Design](@/notes/os/filesystem_design.md) — Journaling and crash consistency mechanisms underlying WAL behavior

---

*Last updated: 2026-01-22*
