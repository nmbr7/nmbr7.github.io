+++
title = "Filesystem Design"
date = 2026-02-10
[taxonomies]
tags = ["filesystem", "journaling", "cow", "ssd", "ext4", "btrfs", "crash-recovery"]
+++


# Expert-Level Filesystem Design: Complete Guide

**Date**: 2026-01-24  
**Purpose**: Comprehensive technical guide for building state-of-the-art filesystems  
**Scope**: From foundational concepts to cutting-edge innovations

---

## Table of Contents

1. [Core Filesystem Concepts](#core-filesystem-concepts)
2. [On-Disk Data Structures](#on-disk-data-structures)
3. [Block Allocation Strategies](#block-allocation-strategies)
4. [B-tree Variants and Indexing](#b-tree-variants-and-indexing)
5. [Journaling and Crash Consistency](#journaling-and-crash-consistency)
6. [Copy-on-Write (COW) Techniques](#copy-on-write-cow-techniques)
7. [Modern Filesystem Innovations](#modern-filesystem-innovations)
8. [Flash/SSD Optimizations](#flash-ssd-optimizations)
9. [Concurrency and Locking](#concurrency-and-locking)
10. [Caching Strategies](#caching-strategies)
11. [RAID and Redundancy](#raid-and-redundancy)
12. [Advanced Features](#advanced-features)
13. [Performance Optimization](#performance-optimization)
14. [Key Research Papers](#key-research-papers)
15. [Implementation Guide](#implementation-guide)

---

## Core Filesystem Concepts

### The File Abstraction

At its core, a filesystem provides an **abstraction layer** between logical file operations and physical storage blocks.

**Fundamental Abstractions**:
```
Logical View (User):          Physical View (Disk):
┌──────────────┐             ┌──────────────┐
│ /home/user/  │             │ Block 0      │ ← Superblock
│   file.txt   │────────────▶│ Block 1      │ ← Inode table
│              │             │ Block 2      │ ← Data blocks
│ 1024 bytes   │             │ Block 3      │ ← ...
└──────────────┘             └──────────────┘
```

### Inode Design

The **inode** (index node) is the fundamental metadata structure in Unix-like filesystems.

```c
// Classic Unix inode structure (conceptual)
struct inode {
    uint64_t i_ino;           // Inode number (unique identifier)
    uint16_t i_mode;          // File type + permissions (rwxrwxrwx)
    uint16_t i_nlink;         // Hard link count
    uint32_t i_uid;           // Owner user ID
    uint32_t i_gid;           // Owner group ID
    uint64_t i_size;          // File size in bytes
    uint64_t i_blocks;        // Number of 512-byte blocks allocated
    
    // Timestamps (nanosecond precision)
    struct timespec i_atime;  // Access time
    struct timespec i_mtime;  // Modification time
    struct timespec i_ctime;  // Change time (metadata)
    
    // Block pointers (ext2/3-style)
    uint32_t i_block[15];     // Direct + indirect block pointers
    //   [0-11]: Direct blocks (point to data)
    //   [12]:   Single indirect (points to block of pointers)
    //   [13]:   Double indirect
    //   [14]:   Triple indirect
    
    // Additional fields
    uint32_t i_generation;    // File version (for NFS)
    uint32_t i_flags;         // File flags (immutable, append-only, etc.)
    
    // Extended attributes pointer
    uint32_t i_file_acl;      // File ACL block
};
```

**Inode Addressing Schemes**:

1. **Direct Block Pointers** (fast for small files):
```
inode.i_block[0] → Data Block 100
inode.i_block[1] → Data Block 101
...
inode.i_block[11] → Data Block 111

For 4KB blocks: 12 × 4KB = 48KB addressable directly
```

2. **Indirect Block Pointers** (for larger files):
```
Single Indirect:
inode.i_block[12] → Block 500 (contains 1024 pointers)
                        ├─→ Data Block 600
                        ├─→ Data Block 601
                        └─→ ... (1024 blocks)

For 4KB blocks: 1024 × 4KB = 4MB additional

Double Indirect:
inode.i_block[13] → Block 1000
                        ├─→ Indirect Block 1100
                        │       ├─→ Data blocks...
                        ├─→ Indirect Block 1101
                        └─→ ... (1024 indirect blocks)

For 4KB blocks: 1024 × 1024 × 4KB = 4GB additional
```

**Problems with Classic Inode Design**:
- **Fragmentation**: Large files scattered across disk
- **Inefficient for huge files**: Triple indirection adds latency
- **Fixed block pointers**: Wastes space for small files

### Extent-Based Allocation (Modern Approach)

**Extent**: Contiguous range of blocks represented as `(start, length)`.

```c
struct extent {
    uint64_t e_block;    // Starting logical block number
    uint64_t e_start;    // Starting physical block number
    uint32_t e_len;      // Length in blocks
    uint32_t e_flags;    // Extent flags (unwritten, etc.)
};

// ext4 extent tree
struct ext4_extent_header {
    uint16_t eh_magic;       // Magic number (0xF30A)
    uint16_t eh_entries;     // Number of valid entries
    uint16_t eh_max;         // Max entries possible
    uint16_t eh_depth;       // Tree depth (0 = leaf)
    uint32_t eh_generation;  // Generation number
};

struct ext4_extent_node {
    struct ext4_extent_header header;
    union {
        struct ext4_extent extents[];      // If leaf node
        struct ext4_extent_idx indexes[]; // If internal node
    };
};
```

**Example File with Extents**:
```
File: 100MB, stored in 3 extents

Extent 1: logical 0-999,   physical 10000-10999  (1000 blocks = 4MB)
Extent 2: logical 1000-24999, physical 20000-43999 (24000 blocks = 96MB)
Extent 3: logical 25000-25099, physical 50000-50099 (100 blocks = 400KB)

Total: 3 extent entries vs 25,600 block pointers in classic scheme!
```

**Benefits**:
- **Reduced metadata**: 3 extents vs 25,600 block pointers
- **Sequential I/O**: Contiguous allocation improves performance
- **Large file support**: Efficiently handle multi-TB files

### Directory Structures

Directories map **names** to **inode numbers**.

#### Linear Directory (Simple, Slow)

```c
struct dir_entry {
    uint32_t inode;       // Inode number
    uint16_t rec_len;     // Record length
    uint8_t  name_len;    // Name length
    uint8_t  file_type;   // File type (regular, dir, symlink, etc.)
    char     name[];      // Filename (variable length)
};

// Directory stored as linear list of entries
// Lookup: O(n) - must scan all entries
```

**Problem**: Terrible for large directories (thousands of files).

#### Hash Table Directory (ext3 HTree)

```c
// ext3 HTree: Hash tree for directory indexing
struct dx_root {
    uint32_t dot_inode;           // "." entry
    uint16_t dot_rec_len;
    uint8_t  dot_name_len;
    uint8_t  dot_file_type;
    char     dot_name[4];         // ".\0\0\0"
    
    uint32_t dotdot_inode;        // ".." entry
    uint16_t dotdot_rec_len;
    // ...
    
    struct dx_root_info {
        uint32_t reserved_zero;
        uint8_t  hash_version;    // Hash algorithm (Legacy, Half MD4, Tea)
        uint8_t  info_length;
        uint8_t  indirect_levels; // Tree depth
        uint8_t  unused_flags;
    } info;
    
    struct dx_entry entries[];    // Hash → block mappings
};

struct dx_entry {
    uint32_t hash;     // Hash of filename
    uint32_t block;    // Block containing entries with this hash
};

// Lookup: O(1) average case
// Hash collision resolution: linear search within block
```

**Hash Function** (ext3 Half MD4):
```c
uint32_t dx_hash_half_md4(const char *name, int len) {
    uint32_t hash = 0x12345678;
    while (len > 0) {
        hash = md4_transform(hash, name);
        name += 16;
        len -= 16;
    }
    return hash;
}
```

#### B-tree Directory (XFS, Btrfs)

```c
// XFS directory B-tree
struct xfs_dir2_leaf_entry {
    uint32_t hashval;     // Hash of filename
    uint32_t address;     // Block address of data entry
};

// Allows:
// - Sorted iteration (for readdir)
// - Range queries
// - Efficient insert/delete
// - Scalability to millions of entries
```

**Comparison**:

| Directory Type | Lookup | Insert | Iterate | Max Entries |
|---------------|--------|--------|---------|-------------|
| Linear | O(n) | O(1) | O(n) | ~10,000 |
| Hash (HTree) | O(1) avg | O(1) avg | O(n) | ~10M |
| B-tree | O(log n) | O(log n) | O(n) | Unlimited |

### Extended Attributes (xattrs)

Store metadata beyond the standard inode fields.

```c
// Extended attribute structure
struct xattr {
    char name_space;     // user, system, security, trusted
    char *name;          // Attribute name
    void *value;         // Attribute value
    size_t value_len;    // Value length
};

// Examples:
// user.comment = "My vacation photos"
// security.selinux = "unconfined_u:object_r:user_home_t:s0"
// system.posix_acl_access = <ACL binary data>
```

**Storage Strategies**:
1. **Inline**: Small xattrs stored in inode itself (if space available)
2. **Dedicated block**: Large xattrs in separate block
3. **B-tree**: Many xattrs indexed in B-tree (XFS)

---

## On-Disk Data Structures

### Superblock

The **superblock** contains filesystem-wide metadata.

```c
struct superblock {
    // Filesystem identification
    uint32_t s_magic;            // Magic number (identifies FS type)
    uint32_t s_block_size;       // Block size (1024, 2048, 4096, etc.)
    uint64_t s_blocks_count;     // Total blocks in filesystem
    uint64_t s_free_blocks;      // Free blocks
    
    // Inode management
    uint32_t s_inodes_count;     // Total inodes
    uint32_t s_free_inodes;      // Free inodes
    uint32_t s_first_ino;        // First non-reserved inode
    uint16_t s_inode_size;       // Size of inode structure
    
    // Block group information (ext2/3/4)
    uint32_t s_blocks_per_group; // Blocks per block group
    uint32_t s_inodes_per_group; // Inodes per block group
    
    // Mount state
    uint16_t s_state;            // Clean/error state
    uint16_t s_errors;           // Error handling behavior
    uint32_t s_mtime;            // Last mount time
    uint32_t s_wtime;            // Last write time
    uint16_t s_mnt_count;        // Mount count since fsck
    uint16_t s_max_mnt_count;    // Max mounts before fsck
    
    // Journal information (ext3/4)
    uint32_t s_journal_inum;     // Journal inode number
    uint32_t s_journal_dev;      // Journal device
    
    // Features
    uint32_t s_feature_compat;   // Compatible features
    uint32_t s_feature_incompat; // Incompatible features
    uint32_t s_feature_ro_compat;// Read-only compatible features
    
    // UUID and volume information
    uint8_t  s_uuid[16];         // Filesystem UUID
    char     s_volume_name[16];  // Volume name
    char     s_last_mounted[64]; // Last mount point
    
    // ... many more fields
};
```

**Superblock Placement**:
- **Primary**: Block 0 or 1 (depending on FS)
- **Backups**: Scattered throughout disk (for recovery)
  - ext2/3/4: At start of each block group
  - XFS: No backups (uses log for recovery)

### Block Groups (ext2/3/4)

Divide disk into **block groups** for locality.

```
Disk Layout:
┌──────────┬──────────┬──────────┬──────────┐
│  Block   │  Block   │  Block   │  Block   │
│  Group 0 │  Group 1 │  Group 2 │  Group 3 │
└──────────┴──────────┴──────────┴──────────┘

Each Block Group:
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│  Superblock  │  Group Desc  │ Block Bitmap │ Inode Bitmap │ Inode Table  │
│  (backup)    │   Table      │              │              │              │
├──────────────┴──────────────┴──────────────┴──────────────┴──────────────┤
│                         Data Blocks                                       │
└───────────────────────────────────────────────────────────────────────────┘
```

```c
struct ext4_group_desc {
    uint32_t bg_block_bitmap_lo;      // Block bitmap block
    uint32_t bg_inode_bitmap_lo;      // Inode bitmap block
    uint32_t bg_inode_table_lo;       // Inode table block
    uint16_t bg_free_blocks_count_lo; // Free blocks count
    uint16_t bg_free_inodes_count_lo; // Free inodes count
    uint16_t bg_used_dirs_count_lo;   // Directories count
    uint16_t bg_flags;                // Flags (INODE_UNINIT, etc.)
    uint32_t bg_exclude_bitmap_lo;    // Snapshot exclude bitmap
    uint16_t bg_block_bitmap_csum_lo; // Block bitmap checksum
    uint16_t bg_inode_bitmap_csum_lo; // Inode bitmap checksum
    uint16_t bg_itable_unused_lo;     // Unused inodes count
    uint16_t bg_checksum;             // Group descriptor checksum
    
    // 64-bit support (if enabled)
    uint32_t bg_block_bitmap_hi;
    uint32_t bg_inode_bitmap_hi;
    uint32_t bg_inode_table_hi;
    // ...
};
```

**Why Block Groups?**
1. **Locality**: Keep related data close (directory + files in same group)
2. **Parallelism**: Multiple groups can be accessed independently
3. **Fault isolation**: Corruption in one group doesn't affect others

### Allocation Bitmaps vs B-trees

#### Bitmaps (ext2/3/4)

```c
// Block bitmap: 1 bit per block (0 = free, 1 = used)
// For 4KB blocks, each bitmap block tracks 32,768 blocks = 128MB

uint8_t block_bitmap[4096];  // 4KB = 32,768 bits

// Check if block is free
bool is_block_free(uint32_t block_num) {
    int byte = block_num / 8;
    int bit = block_num % 8;
    return !(block_bitmap[byte] & (1 << bit));
}

// Allocate block
void allocate_block(uint32_t block_num) {
    int byte = block_num / 8;
    int bit = block_num % 8;
    block_bitmap[byte] |= (1 << bit);
}
```

**Pros**:
- Simple, fast for small allocations
- Cache-friendly for sequential scans
- Constant space overhead

**Cons**:
- Linear scan to find free blocks (slow for fragmented disk)
- Doesn't scale well to huge filesystems (TB+)
- Must be in memory for fast allocation

#### B-tree Space Maps (XFS, Btrfs, ZFS)

```c
// XFS allocation B-tree
struct xfs_alloc_rec {
    uint32_t ar_startblock;  // Starting block number
    uint32_t ar_blockcount;  // Number of free blocks
};

// Two B-trees per allocation group:
// 1. Sorted by start block (for finding specific location)
// 2. Sorted by size (for best-fit allocation)

// Example tree (by size):
//           [Root: 1000 blocks @ 5000]
//          /                           \
//  [500 @ 1000]                [2000 @ 10000]
//     /      \                    /          \
// [100@500] [300@2000]      [1500@8000]  [800@12000]

// Allocate 350 blocks (best-fit):
// 1. Search size-tree for smallest extent >= 350
// 2. Found: 500 @ 1000
// 3. Split: allocate 350, leave 150 @ 1350
// 4. Update both trees
```

**Pros**:
- Scales to petabytes
- Fast best-fit allocation
- Efficient range queries
- Low memory overhead (only metadata in RAM)

**Cons**:
- More complex than bitmaps
- Slower for tiny allocations
- Requires careful balancing

### Journal/Log Structures

**Journal**: Write-ahead log for crash consistency.

```c
// ext4 journal block
struct journal_header {
    uint32_t h_magic;        // 0xC03B3998 (JBD2 magic)
    uint32_t h_blocktype;    // Block type (descriptor, commit, etc.)
    uint32_t h_sequence;     // Transaction sequence number
};

// Journal descriptor block
struct journal_block_tag {
    uint32_t t_blocknr;      // Filesystem block number
    uint32_t t_flags;        // Flags (escaped, same UUID, etc.)
};

// Transaction in journal:
// 1. Descriptor block: Lists all blocks in transaction
// 2. Data blocks: Actual data being written
// 3. Commit block: Marks transaction complete
```

**Journal Modes**:

1. **Journal (Full Data + Metadata)**:
```
Write order:
1. Write data + metadata to journal
2. Write commit block
3. Checkpoint: Write data + metadata to final location
4. Free journal space

Safest but slowest (double write)
```

2. **Ordered (Metadata Journaled, Data Ordered)**:
```
Write order:
1. Write data to final location
2. Wait for data write to complete
3. Write metadata to journal
4. Write commit block
5. Checkpoint: Write metadata to final location

Default for ext4, good balance
```

3. **Writeback (Metadata Journaled, Data Not Ordered)**:
```
Write order:
1. Write metadata to journal
2. Write commit block
3. Write data to final location (anytime)
4. Checkpoint: Write metadata to final location

Fastest but can expose stale data
```

---

## Block Allocation Strategies

### First-Fit, Best-Fit, Worst-Fit

```c
// Free block list
struct free_extent {
    uint64_t start;
    uint64_t length;
    struct free_extent *next;
};

// First-fit: Use first extent that fits
extent* first_fit(size_t requested, free_extent *list) {
    for (extent *e = list; e != NULL; e = e->next) {
        if (e->length >= requested) {
            return e;  // Found first fit
        }
    }
    return NULL;  // No fit found
}

// Best-fit: Use smallest extent that fits
extent* best_fit(size_t requested, free_extent *list) {
    extent *best = NULL;
    for (extent *e = list; e != NULL; e = e->next) {
        if (e->length >= requested) {
            if (best == NULL || e->length < best->length) {
                best = e;
            }
        }
    }
    return best;
}

// Worst-fit: Use largest extent
extent* worst_fit(size_t requested, free_extent *list) {
    extent *worst = NULL;
    for (extent *e = list; e != NULL; e = e->next) {
        if (e->length >= requested) {
            if (worst == NULL || e->length > worst->length) {
                worst = e;
            }
        }
    }
    return worst;
}
```

**Trade-offs**:
- **First-fit**: Fast, but fragments
- **Best-fit**: Reduces waste, but creates tiny fragments
- **Worst-fit**: Keeps large contiguous areas, but slower

### Buddy Allocator

Used in kernel memory allocation, applicable to filesystems.

```c
// Buddy system: Allocate power-of-2 sized blocks
// Split and merge blocks as needed

#define MAX_ORDER 10  // 2^10 = 1024 blocks max

struct buddy_allocator {
    struct list_head free_list[MAX_ORDER + 1];
    // free_list[0]: 2^0 = 1 block
    // free_list[1]: 2^1 = 2 blocks
    // ...
    // free_list[10]: 2^10 = 1024 blocks
};

void* buddy_alloc(struct buddy_allocator *buddy, size_t blocks) {
    int order = ceil_log2(blocks);  // Round up to power of 2
    
    // Find smallest available order
    for (int i = order; i <= MAX_ORDER; i++) {
        if (!list_empty(&buddy->free_list[i])) {
            void *block = list_first_entry(&buddy->free_list[i]);
            list_del(block);
            
            // Split larger blocks down to requested order
            while (i > order) {
                i--;
                void *buddy_block = block + (1 << i);
                list_add(&buddy->free_list[i], buddy_block);
            }
            
            return block;
        }
    }
    return NULL;  // Out of memory
}

void buddy_free(struct buddy_allocator *buddy, void *block, int order) {
    // Try to merge with buddy
    while (order < MAX_ORDER) {
        void *buddy_block = (void*)((uintptr_t)block ^ (1 << order));
        
        if (!is_free(buddy_block, order)) {
            break;  // Buddy not free, can't merge
        }
        
        // Merge with buddy
        list_del(buddy_block);
        if (buddy_block < block) {
            block = buddy_block;
        }
        order++;
    }
    
    list_add(&buddy->free_list[order], block);
}
```

**Benefits**:
- Fast allocation/deallocation: O(log n)
- Automatic coalescing of fragments
- Simple implementation

**Downsides**:
- Internal fragmentation (round up to power of 2)
- Not suitable for variable-size extents

### Delayed Allocation (Allocate-on-Flush)

**Concept**: Delay block allocation until page cache is flushed to disk.

```c
// Without delayed allocation:
write()  →  allocate blocks immediately  →  mark dirty  →  flush later

// With delayed allocation:
write()  →  mark dirty (no allocation)  →  flush  →  allocate optimally
```

**Benefits**:
1. **Better locality**: Allocate all blocks together
2. **Reduce fragmentation**: Know final size before allocating
3. **Fewer allocations**: Overwritten data never allocated

**Example** (ext4):
```c
// User writes 100KB in small chunks
write(fd, buf, 1024);   // Page marked dirty, no allocation
write(fd, buf, 1024);   // Another dirty page
// ... 100 writes ...

// Later, pdflush wakes up
// Allocate all 100 pages together as single extent
extent = allocate_extent(100 * PAGE_SIZE);  // One contiguous chunk!
```

**Risks**:
- **ENOSPC surprise**: Writes succeed, but allocation fails later
- **Complex error handling**: Must handle late failures

### Multi-Block Allocation

Allocate multiple blocks in one call for better contiguity.

```c
// ext4 multi-block allocator
struct ext4_allocation_request {
    struct inode *inode;
    ext4_lblk_t logical;     // Logical block number in file
    ext4_fsblk_t goal;       // Preferred physical block
    unsigned int len;        // Number of blocks requested
    unsigned int flags;
};

ext4_fsblk_t ext4_mb_new_blocks(handle_t *handle,
                                struct ext4_allocation_request *ar,
                                int *errp) {
    // Try to allocate 'ar->len' contiguous blocks near 'ar->goal'
    
    // 1. Check preallocation pool for this inode
    // 2. Try allocation near goal
    // 3. Fall back to best-fit in current block group
    // 4. Try other block groups if necessary
    
    return allocated_block;
}
```

### Preallocation

Reserve space in advance to reduce fragmentation.

```c
// fallocate() system call
int fallocate(int fd, int mode, off_t offset, off_t len);

// Example: Preallocate 1GB for database file
fallocate(fd, 0, 0, 1024*1024*1024);

// Filesystem allocates extents but doesn't zero data
// Marked as "unwritten" - reads return zeros until written
```

**Use Cases**:
- Databases (allocate tablespace)
- Video recording (ensure continuous write)
- Torrent clients (allocate full file size)

### Locality Optimization

**Goal**: Keep related data physically close.

#### Block Groups (ext4)

```c
// Allocation strategy:
// 1. New directory: Choose group with most free inodes
// 2. New file: Same group as parent directory
// 3. Data blocks: Same group as inode

uint32_t ext4_find_group_orlov(struct inode *parent) {
    // Find block group with:
    // - Most free inodes (for directories)
    // - Above average free blocks
    // - Low directory count (balance across groups)
    
    int best_group = -1;
    int max_score = 0;
    
    for (int i = 0; i < ngroups; i++) {
        int score = group_free_inodes[i] + 
                   group_free_blocks[i] / blocks_per_group -
                   group_dir_count[i];
        if (score > max_score) {
            max_score = score;
            best_group = i;
        }
    }
    return best_group;
}
```

#### Allocation Groups (XFS)

```c
// XFS: Multiple allocation groups, similar to ext4 block groups
// But more sophisticated allocation strategies

struct xfs_ag {
    xfs_agno_t ag_number;        // AG number
    xfs_agblock_t ag_length;     // AG size in blocks
    
    // Two B-trees for free space:
    struct xfs_btree *by_block;  // Sorted by start block
    struct xfs_btree *by_size;   // Sorted by size
    
    // Inode allocation B-tree
    struct xfs_btree *inode_btree;
};

// Allocation strategy:
// - Prefer AG with most free space
// - Keep files in same directory in same AG
// - Balance across AGs for parallel I/O
```


---

## B-tree Variants and Indexing

### Classic B-tree vs B+-tree

**B-tree**: Internal nodes contain keys and data
**B+-tree**: Only leaf nodes contain data, internal nodes only keys

```
B-tree:
           [30|60]
          /   |   \
    [10,20] [40,50] [70,80]
    
    Each node contains actual data records

B+-tree:
           [30|60]
          /   |   \
    [10,20,30] → [40,50,60] → [70,80,90]
                  ↑ linked list
    
    Internal nodes: only keys (for routing)
    Leaf nodes: keys + data, linked for sequential access
```

**Why B+-tree is Better for Filesystems**:
1. **Higher fanout**: Internal nodes are pure keys → more keys per node → shorter tree
2. **Sequential access**: Leaf nodes linked → efficient range scans
3. **Stable structure**: All data at same depth → predictable performance

```c
// B+-tree node structure
struct btree_node {
    bool is_leaf;
    int num_keys;
    uint64_t keys[BTREE_ORDER - 1];
    
    union {
        // Internal node: child pointers
        struct btree_node *children[BTREE_ORDER];
        
        // Leaf node: data pointers + next leaf
        struct {
            void *data[BTREE_ORDER - 1];
            struct btree_node *next_leaf;
        };
    };
};

// Search in B+-tree
void* btree_search(struct btree_node *root, uint64_t key) {
    struct btree_node *node = root;
    
    // Navigate to leaf
    while (!node->is_leaf) {
        int i;
        for (i = 0; i < node->num_keys && key >= node->keys[i]; i++);
        node = node->children[i];
    }
    
    // Search in leaf
    for (int i = 0; i < node->num_keys; i++) {
        if (node->keys[i] == key) {
            return node->data[i];
        }
    }
    return NULL;  // Not found
}
```

### Copy-on-Write B-trees (Btrfs)

**Problem with Traditional B-trees**: In-place updates are not crash-safe.

**Solution**: Never modify nodes in-place, always write to new location.

```c
// Traditional update (UNSAFE):
node->keys[5] = new_key;
write_block(node_block_num, node);  // Overwrites old data
// ↑ If crash happens here, tree is corrupted

// COW update (SAFE):
struct btree_node *new_node = clone_node(node);
new_node->keys[5] = new_key;
uint64_t new_block = allocate_block();
write_block(new_block, new_node);  // Write to NEW location
update_parent_pointer(parent, old_block, new_block);
// ↑ If crash happens, old tree is still intact
```

**COW B-tree Update**:
```
Original tree:
         [Root: block 1000]
        /                  \
  [Node A: 500]       [Node B: 600]
  /     |     \       /     |     \
[L1]  [L2]  [L3]  [L4]  [L5]  [L6]

Update L5:
1. Clone L5 → write to block 700
2. Clone Node B (points to new L5) → write to block 800
3. Clone Root (points to new Node B) → write to block 1100
4. Update superblock to point to new root (atomic)

New tree (old tree still exists!):
         [Root: block 1100]  ← NEW
        /                  \
  [Node A: 500] (same) [Node B: 800]  ← NEW
  /     |     \       /     |     \
[L1]  [L2]  [L3]  [L4]  [L5: 700] [L6]  ← NEW
```

**Btrfs COW B-tree Implementation**:

```c
struct btrfs_header {
    uint8_t fsid[16];           // Filesystem UUID
    uint64_t bytenr;            // Physical block number
    uint64_t flags;
    uint8_t chunk_tree_uuid[16];
    uint64_t generation;        // Transaction ID
    uint64_t owner;             // Tree ID
    uint32_t nritems;           // Number of items
    uint8_t level;              // Tree level (0 = leaf)
};

struct btrfs_key {
    uint64_t objectid;          // Object (inode, chunk, etc.)
    uint8_t type;               // Item type (inode, extent, etc.)
    uint64_t offset;            // Offset or secondary key
};

struct btrfs_item {
    struct btrfs_key key;
    uint32_t offset;            // Offset in node
    uint32_t size;              // Item size
};

struct btrfs_node {
    struct btrfs_header header;
    struct btrfs_key_ptr {
        struct btrfs_key key;
        uint64_t blockptr;      // Child block number
        uint64_t generation;    // Child's generation
    } ptrs[];
};

struct btrfs_leaf {
    struct btrfs_header header;
    struct btrfs_item items[];
    // Actual data at end of block (grows backward)
};
```

**Benefits of COW B-trees**:
1. **Crash safety**: Old tree intact until new one committed
2. **Snapshots**: Keep old root pointers = instant snapshots
3. **No journal needed**: COW itself provides atomicity

**Challenges**:
1. **Wandering trees**: Must update all ancestors (addressed later)
2. **Space amplification**: Old versions accumulate
3. **Fragmentation**: New blocks scattered across disk

### Log-Structured Merge Trees (LSM-trees)

**Used in**: LevelDB, RocksDB, Cassandra, HBase (not traditional filesystems, but relevant)

**Concept**: Write-optimized tree that batches updates in memory, then flushes to disk.

```
LSM-tree Structure:

Memory (C0):
┌─────────────────┐
│  MemTable       │ ← All writes go here first (sorted in RAM)
│  (sorted)       │
└─────────────────┘
        ↓ (when full)
        
Disk (C1, C2, ...):
┌─────────────────┐
│  Level 0        │ ← Immutable sorted runs (SSTables)
│  Run 1, Run 2...│   (small, overlapping keys)
└─────────────────┘
        ↓ (compaction)
┌─────────────────┐
│  Level 1        │ ← Larger sorted runs
│  Run 1, Run 2...│   (non-overlapping keys)
└─────────────────┘
        ↓ (compaction)
┌─────────────────┐
│  Level 2        │ ← Even larger runs
└─────────────────┘
```

**Operations**:

```c
// Write (fast - in memory)
void lsm_put(key, value) {
    memtable.insert(key, value);  // Sorted insert in RAM
    
    if (memtable.size() > THRESHOLD) {
        flush_memtable_to_disk();  // Write sorted run to Level 0
        memtable.clear();
    }
}

// Read (slower - may need to check multiple levels)
value lsm_get(key) {
    // 1. Check memtable
    if (memtable.contains(key)) {
        return memtable.get(key);
    }
    
    // 2. Check each level (newest to oldest)
    for (level in [0, 1, 2, ...]) {
        for (sstable in level.sstables) {
            if (sstable.might_contain(key)) {  // Bloom filter check
                value = sstable.get(key);
                if (value != NULL) {
                    return value;
                }
            }
        }
    }
    return NULL;  // Not found
}

// Compaction (background process)
void compact_level(level) {
    // Merge overlapping SSTables in level
    // Write merged result to next level
    // Delete old SSTables
    
    // Example: Level 0 → Level 1
    // L0: [1-10], [5-15], [12-20] (overlapping)
    // Compact to L1: [1-20] (merged, no overlap)
}
```

**Trade-offs**:
- **Writes**: Very fast (in-memory, sequential disk writes)
- **Reads**: Slower (must check multiple levels)
- **Space**: Amplification due to multiple copies during compaction
- **Compaction**: Background I/O load

**Not widely used in traditional filesystems**, but principles appear in:
- F2FS (flash-friendly filesystem)
- bcachefs (uses similar multi-level structure)

### Adaptive Radix Tree (ART)

Covered extensively in the HyPer/Umbra research, but here's filesystem context:

**Use in Filesystems**:
- **In-memory directory cache** (dcache in Linux)
- **Inode cache** (icache)
- **Extent lookup**

```c
// ART for directory entry cache
struct dentry_cache {
    struct art_tree *tree;  // ART root
};

struct dentry {
    char name[256];
    uint64_t inode_num;
    struct dentry *parent;
    // ...
};

// Lookup: O(k) where k = key length
struct dentry* dcache_lookup(const char *path) {
    return art_lookup(dcache.tree, path, strlen(path));
}

// Benefit: Faster than hash table for long keys
// No hash collisions, cache-friendly
```

### HTree (ext3/4 Directory Indexing)

**Problem**: Linear directory lookup is O(n).

**Solution**: Hash-based index tree.

```c
// HTree uses hash of filename to index into tree

// Root block contains:
struct dx_root {
    struct fake_dirent dot;         // "." entry
    struct fake_dirent dotdot;      // ".." entry
    struct dx_root_info {
        uint32_t reserved_zero;
        uint8_t hash_version;       // Hash algorithm
        uint8_t info_length;
        uint8_t indirect_levels;    // Tree depth
        uint8_t unused_flags;
    } info;
    struct dx_entry entries[0];     // Hash index
};

struct dx_entry {
    uint32_t hash;                  // Hash value
    uint32_t block;                 // Block containing entries
};

// Lookup algorithm:
struct dirent* htree_lookup(const char *name) {
    uint32_t hash = dx_hash(name);
    
    // Navigate tree using hash
    struct dx_entry *entry = dx_root.entries;
    int level = dx_root.info.indirect_levels;
    
    while (level > 0) {
        // Binary search in current level
        entry = binary_search(entry, hash);
        entry = read_block(entry->block);
        level--;
    }
    
    // Linear search in final block (hash collisions)
    struct dirent *de = read_block(entry->block);
    while (de) {
        if (strcmp(de->name, name) == 0) {
            return de;
        }
        de = next_dirent(de);
    }
    return NULL;
}
```

**Performance**:
- Lookup: O(1) average (hash) + O(m) for collisions
- Can handle millions of files per directory
- Default in ext4 for large directories

---

## Journaling and Crash Consistency

### Write-Ahead Logging (WAL)

**Fundamental Principle**: Log changes before applying them.

```c
// WAL protocol:
void wal_write(transaction *txn) {
    // 1. Write operation to log
    log_entry entry = {
        .txn_id = txn->id,
        .operation = txn->op,
        .data = txn->data,
    };
    append_log(entry);
    
    // 2. Force log to disk (fsync)
    fsync(log_fd);
    
    // 3. Apply operation to data structures
    apply_operation(txn->op, txn->data);
    
    // 4. Mark log entry as committed
    mark_committed(entry);
}

// Recovery after crash:
void wal_recover() {
    for (entry in log) {
        if (entry.committed) {
            // Already applied, skip
        } else {
            // Replay operation
            apply_operation(entry.operation, entry.data);
        }
    }
}
```

### ext4 Journal (JBD2)

**Journal Block Types**:

```c
#define JBD2_DESCRIPTOR_BLOCK   1   // Lists blocks in transaction
#define JBD2_COMMIT_BLOCK       2   // Marks transaction complete
#define JBD2_SUPERBLOCK_V1      3   // Journal superblock
#define JBD2_SUPERBLOCK_V2      4   // Journal superblock v2
#define JBD2_REVOKE_BLOCK       5   // Revoked blocks

struct journal_header_s {
    uint32_t h_magic;               // 0xC03B3998
    uint32_t h_blocktype;           // Block type (above)
    uint32_t h_sequence;            // Transaction sequence
};

// Descriptor block: describes transaction
struct journal_block_tag_s {
    uint32_t t_blocknr;             // FS block number
    uint32_t t_flags;               // Flags
    uint32_t t_blocknr_high;        // High 32 bits (64-bit)
    // ... checksum, UUID, etc.
};

// Commit block: transaction complete marker
struct commit_header {
    struct journal_header_s h;
    uint8_t h_chksum_type;          // Checksum algorithm
    uint8_t h_chksum_size;          // Checksum size
    uint32_t h_chksum[JBD2_CHECKSUM_BYTES];
    uint64_t h_commit_sec;          // Commit timestamp
    uint32_t h_commit_nsec;
};
```

**Transaction Example**:

```
Journal layout for transaction 1234:

Block 0: Descriptor
  - Transaction 1234
  - Contains blocks: [500, 501, 502] → [1000, 1001, 1002]

Block 1: Data (copy of block 500)
Block 2: Data (copy of block 501)  
Block 3: Data (copy of block 502)

Block 4: Commit
  - Transaction 1234 complete
  - Checksum: 0xABCD1234

Recovery:
- Found transaction 1234 with commit block → COMPLETE
- Replay: Copy blocks 1,2,3 to 500,501,502
- Transaction 1235 no commit block → INCOMPLETE
- Don't replay, discard
```

**Checkpoint Process**:

```c
void jbd2_checkpoint() {
    // Write journaled blocks to final location
    for (transaction in committed_transactions) {
        for (block in transaction.blocks) {
            write_block(block.fs_location, block.data);
        }
        // Mark transaction as checkpointed
        transaction.state = CHECKPOINTED;
    }
    
    // Reclaim journal space
    journal_head = find_oldest_uncommitted();
}
```

### Soft Updates (FreeBSD FFS)

**Alternative to journaling**: Carefully order writes to maintain consistency.

**Key Idea**: Ensure disk is always in valid state, even after crash.

**Ordering Rules**:
1. Never point to uninitialized structure
2. Never reuse resource before old references removed
3. Never reset old pointer before new structure initialized

```c
// Example: Create file
void create_file(dir, name, inode) {
    // WRONG order:
    // 1. Add dir entry → 2. Initialize inode
    // ↑ Crash between: dir points to garbage inode!
    
    // CORRECT order (soft updates):
    // 1. Initialize inode → 2. Add dir entry
    
    // Step 1: Write inode
    inode->mode = 0644;
    inode->uid = current_uid;
    // ... initialize ...
    write_inode(inode);
    
    // Step 2: Add directory entry (only after inode written)
    add_dir_entry(dir, name, inode->inum);
}

// Example: Delete file
void delete_file(dir, name, inode) {
    // CORRECT order:
    // 1. Remove dir entry → 2. Free inode → 3. Free data blocks
    
    // Step 1: Remove directory entry
    remove_dir_entry(dir, name);
    
    // Step 2: Decrement inode link count
    inode->nlink--;
    if (inode->nlink == 0) {
        // Step 3: Mark inode free
        mark_inode_free(inode);
        
        // Step 4: Free data blocks
        for (block in inode->blocks) {
            mark_block_free(block);
        }
    }
}
```

**Dependencies**:

```c
struct dependency {
    enum { INODE_DEP, DIRENTRY_DEP, BLOCK_DEP } type;
    void *old_value;
    void *new_value;
    struct dependency *next;
};

// Before writing block:
void check_dependencies(block) {
    for (dep in block->dependencies) {
        if (!dep->predecessor_written) {
            // Can't write yet, wait for dependency
            add_to_wait_queue(block);
            return;
        }
    }
    // All dependencies satisfied, safe to write
    write_block(block);
}
```

**Pros vs Journaling**:
- **No double-write overhead**: Write each block once
- **Better performance**: No journal I/O

**Cons**:
- **Complex**: Hard to implement correctly
- **Background cleanup**: Need fsck-like process to reclaim space
- **Not widely adopted**: Only FFS (FreeBSD)

### Log-Structured Filesystem (LFS)

**Paper**: Rosenblum & Ousterhout, 1992

**Key Idea**: Treat disk as circular log, always append.

```
Disk as Circular Log:
┌────────────────────────────────────────────────────────────┐
│ Segment 1 │ Segment 2 │ Segment 3 │ ... │ Segment N │     │
│ (old)     │           │ (current) │     │           │     │
└────────────────────────────────────────────────────────────┘
            ↑                         ↑
         Cleaned              Current write position
```

**Write Process**:

```c
void lfs_write(inode, data) {
    // 1. Buffer writes in memory
    segment_buffer.add(data);
    segment_buffer.add(inode);  // inode follows data
    
    // 2. When buffer full, write entire segment
    if (segment_buffer.full()) {
        segment_num = allocate_segment();
        write_segment(segment_num, segment_buffer);
        
        // 3. Update inode map (inode locations)
        inode_map[inode.inum] = segment_num;
        
        // 4. Clear buffer
        segment_buffer.clear();
    }
}

// Segment structure:
struct segment {
    struct segment_summary {
        int num_blocks;
        struct {
            uint64_t inode_num;
            uint32_t block_num;
        } blocks[num_blocks];
    } summary;
    
    char data[];  // Actual data blocks + inodes
};
```

**Garbage Collection**:

```c
void lfs_clean() {
    // Find segments with mostly dead data
    segment_num = find_low_utilization_segment();
    
    segment = read_segment(segment_num);
    
    // Copy live data to new segment
    for (block in segment) {
        if (is_live(block)) {  // Check via inode map
            write_to_new_segment(block);
        }
    }
    
    // Free old segment
    mark_segment_free(segment_num);
}

bool is_live(block, segment) {
    // Check if inode map points to this segment
    uint64_t current_location = inode_map[block.inode_num];
    return current_location == segment.num;
}
```

**Pros**:
- **Sequential writes**: Excellent for SSDs/flash
- **No seek time**: All writes clustered
- **Crash recovery**: Segments are atomic

**Cons**:
- **Garbage collection overhead**: Must reclaim dead segments
- **Random reads**: Inode map lookups add overhead
- **Write amplification**: Copying live data during GC

**Modern Incarnations**:
- **F2FS**: Flash-friendly filesystem (Android)
- **NILFS**: New Implementation of LFS (Linux)

---

## Copy-on-Write (COW) Techniques

### COW Fundamentals

**Core Principle**: Never modify data in place, always write to new location.

```c
// Traditional in-place update:
void update_block(uint64_t block_num, char *data) {
    write_block(block_num, data);  // Overwrites old data
    // ↑ Not crash-safe, old data lost
}

// COW update:
void cow_update_block(uint64_t old_block, char *data) {
    uint64_t new_block = allocate_block();
    write_block(new_block, data);  // Write to new location
    update_references(old_block, new_block);
    // ↑ Old data still intact until committed
}
```

### Snapshot Implementation

**Snapshot**: Point-in-time copy of filesystem.

**Without COW** (traditional):
```c
// Must copy entire filesystem
snapshot_create() {
    for (block in filesystem) {
        copy_block(block, snapshot_volume);  // Expensive!
    }
}
// Time: O(n), Space: O(n)
```

**With COW** (Btrfs, ZFS):
```c
// Just save root pointer!
snapshot_create() {
    snapshot.root = filesystem.root;  // Save pointer
    snapshot.generation = current_generation;
    current_generation++;
}
// Time: O(1), Space: O(shared changes)
```

**Snapshot Tree Example**:

```
Original filesystem (gen 1):
         [Root gen1: block 100]
        /                      \
  [Node A: 200]            [Node B: 300]
  /     |     \            /     |     \
[D1]  [D2]  [D3]        [D4]  [D5]  [D6]

Create snapshot → Save root pointer

Modify D5 (gen 2):
         [Root gen2: block 400]  ← NEW
        /                      \
  [Node A: 200] (shared)   [Node B': 500]  ← NEW
  /     |     \            /     |     \
[D1]  [D2]  [D3]        [D4]  [D5': 600] [D6]  ← NEW

Snapshot still sees:
         [Root gen1: block 100]
        /                      \
  [Node A: 200]            [Node B: 300]
  /     |     \            /     |     \
[D1]  [D2]  [D3]        [D4]  [D5: old] [D6]
```

### Reference Counting

**Problem**: When can we free old blocks?

**Solution 1: Reference Counting**

```c
struct block {
    char data[BLOCK_SIZE];
    uint32_t refcount;  // How many references
};

void cow_write(block_num) {
    block *old = read_block(block_num);
    
    if (old->refcount == 1) {
        // Only we reference it, can modify in-place
        modify_block(old);
    } else {
        // Shared, must COW
        block *new = allocate_block();
        memcpy(new->data, old->data, BLOCK_SIZE);
        modify_block(new);
        
        old->refcount--;  // Decrement old
        new->refcount = 1;
    }
}

void delete_snapshot(snapshot) {
    // Decrement refcounts for all blocks in snapshot
    walk_tree(snapshot.root, [](block) {
        block->refcount--;
        if (block->refcount == 0) {
            free_block(block);
        }
    });
}
```

**Problems with Refcounting**:
- **Cycle detection**: Reference cycles prevent freeing
- **Overhead**: Maintaining counts for billions of blocks
- **Atomicity**: Incrementing/decrementing must be atomic

### Garbage Collection (ZFS Approach)

**Solution 2: Mark-and-Sweep GC**

```c
// ZFS uses "livelist" - track which blocks are live

void zfs_destroy_snapshot(snapshot) {
    // Don't immediately free blocks
    // Instead, mark snapshot as deleted
    snapshot->deleted = true;
    
    // Background process:
    async_gc_snapshot(snapshot);
}

void async_gc_snapshot(snapshot) {
    // 1. Mark all blocks reachable from active snapshots
    bitmap live_blocks = allocate_bitmap();
    
    for (snap in active_snapshots) {
        walk_tree(snap.root, [&](block) {
            live_blocks.set(block->num);
        });
    }
    
    // 2. Walk deleted snapshot, free unmarked blocks
    walk_tree(snapshot.root, [&](block) {
        if (!live_blocks.is_set(block->num)) {
            free_block(block);  // Not referenced elsewhere
        }
    });
}
```

**Deferred Deletion**:
```c
// Btrfs approach: Queue blocks for later deletion
void btrfs_delete_snapshot(snapshot) {
    // Add to deletion queue
    deletion_queue.push(snapshot.root);
    
    // Background cleaner processes queue
    async_cleaner_thread();
}

void async_cleaner_thread() {
    while (true) {
        root = deletion_queue.pop();
        if (root == NULL) {
            sleep(60);
            continue;
        }
        
        // Walk tree, check if blocks are shared
        walk_tree(root, [](block) {
            if (block->refcount == 1) {
                free_block(block);  // Not shared
            } else {
                block->refcount--;   // Still shared
            }
        });
    }
}
```

### Space Maps (ZFS)

**Problem**: Need efficient way to track which blocks are allocated.

**ZFS Solution**: Log-structured space map.

```c
// Space map: Track allocations as log of operations
struct space_map_entry {
    enum { ALLOC, FREE } type;
    uint64_t offset;     // Block number
    uint64_t length;     // Number of blocks
};

// Space map is append-only log
struct space_map {
    uint64_t size;       // Size of space
    uint64_t alloc;      // Bytes allocated
    struct space_map_entry entries[];
};

// Example:
// ALLOC(100, 50)  ← Allocated blocks 100-149
// ALLOC(200, 30)  ← Allocated blocks 200-229
// FREE(120, 10)   ← Freed blocks 120-129

// Periodically condense:
// ALLOC(100, 20)  ← 100-119
// ALLOC(130, 20)  ← 130-149
// ALLOC(200, 30)  ← 200-229
```

**Benefits**:
- **Efficient**: Log-structured, sequential writes
- **Compact**: Only deltas, not full bitmap
- **Transactional**: Part of transaction group

### Wandering Trees Problem

**Problem**: COW tree updates propagate to root.

```
Update leaf block:
1. Clone leaf: A → A'
2. Clone parent: B → B' (update pointer A → A')
3. Clone grandparent: C → C' (update pointer B → B')
4. ... all the way to root

For deep trees, many blocks rewritten per update!
```

**Solution 1: Batching** (Btrfs)

```c
// Batch multiple updates in single transaction
void btrfs_transaction() {
    transaction *txn = begin_transaction();
    
    // Apply many operations
    for (op in pending_operations) {
        apply_operation(op, txn);  // Modify in-memory tree
    }
    
    // Commit: Walk tree once, COW all modified blocks
    commit_transaction(txn);  // Single tree walk
}
```

**Solution 2: Indirect Blocks** (ZFS)

```c
// ZFS uses "indirect blocks" to reduce COW overhead

struct blkptr {
    uint64_t blk_dva[3];      // Up to 3 copies (mirrors)
    uint64_t blk_prop;        // Properties
    uint64_t blk_pad[2];
    uint64_t blk_phys_birth;  // Transaction group born
    uint64_t blk_birth;       // Transaction group
    uint64_t blk_fill;        // Fill count
    zio_cksum_t blk_cksum;    // Checksum
};

// Indirect block contains array of blkptrs
// Only update changed blkptrs, not entire indirect block
```

**Solution 3: Shadow Paging** (BPFS)

```c
// Keep old version of tree during update
// Only atomically flip root pointer when done

// Double-buffered root:
struct filesystem {
    struct root *current_root;
    struct root *shadow_root;
    uint64_t generation;
};

void shadow_update() {
    // 1. Clone root to shadow
    shadow_root = clone_root(current_root);
    
    // 2. Apply updates to shadow
    apply_updates(shadow_root);
    
    // 3. Atomic flip
    atomic_exchange(&current_root, &shadow_root);
    generation++;
}
```


---

## Modern Filesystem Innovations

### Extent Trees

**Problem**: Block-mapped files waste metadata space for large files.

**Solution**: Store contiguous ranges (extents) instead of individual blocks.

```c
// Traditional block map (ext2/3):
uint32_t blocks[100000];  // 100K blocks = 400KB metadata

// Extent map (ext4):
struct extent extents[10];  // 10 extents = 160 bytes!
// extent[0]: blocks 0-19999 → physical 1000-20999
// extent[1]: blocks 20000-39999 → physical 30000-49999
// ...
```

**ext4 Extent Tree**:

```c
#define EXT4_EXT_MAGIC 0xf30a

struct ext4_extent {
    uint32_t ee_block;        // First logical block
    uint16_t ee_len;          // Number of blocks (max 32768)
    uint16_t ee_start_hi;     // High 16 bits of physical block
    uint32_t ee_start_lo;     // Low 32 bits of physical block
};

struct ext4_extent_idx {
    uint32_t ei_block;        // Index covers logical blocks >= ei_block
    uint32_t ei_leaf_lo;      // Low 32 bits of physical block
    uint16_t ei_leaf_hi;      // High 16 bits of physical block
    uint16_t ei_unused;
};

struct ext4_extent_header {
    uint16_t eh_magic;        // Magic number
    uint16_t eh_entries;      // Number of valid entries
    uint16_t eh_max;          // Capacity of store
    uint16_t eh_depth;        // Tree depth (0 = leaf)
    uint32_t eh_generation;   // Generation
};
```

**Extent Lookup**:

```c
// Find physical block for logical block
uint64_t ext4_ext_find_block(struct inode *inode, uint32_t logical) {
    struct ext4_extent_header *eh = ext4_ext_get_header(inode);
    
    // Navigate tree
    while (eh->eh_depth > 0) {
        // Binary search in index nodes
        struct ext4_extent_idx *ix = ext4_ext_binsearch_idx(eh, logical);
        eh = ext4_ext_read_block(ix->ei_leaf);
    }
    
    // Binary search in leaf node
    struct ext4_extent *ex = ext4_ext_binsearch(eh, logical);
    
    if (ex == NULL || logical < ex->ee_block || 
        logical >= ex->ee_block + ex->ee_len) {
        return 0;  // Hole in file
    }
    
    // Calculate physical block
    uint64_t physical = (ex->ee_start_hi << 32) | ex->ee_start_lo;
    return physical + (logical - ex->ee_block);
}
```

### Variable-Size Blocks

**Traditional**: Fixed block size (4KB typically)

**Problem**: Wastes space for small files, inefficient for huge files

**Solution**: Support multiple block sizes

```c
// ZFS recordsize property
zfs set recordsize=128K pool/dataset  // For large sequential I/O
zfs set recordsize=8K pool/database   // For small random I/O

// Block can be 512B to 16MB (power of 2)
// Small files: Use small blocks (less internal fragmentation)
// Large files: Use large blocks (less metadata overhead)
```

**Btrfs**: Similar with "nodesize" and data compression

```bash
mkfs.btrfs --nodesize 16K /dev/sda   # Metadata block size
# Data blocks can be variable size due to compression
```

### Transparent Compression

**Inline Compression**: Compress data before writing to disk.

```c
// Btrfs compression
struct compressed_extent {
    uint64_t logical_offset;
    uint64_t compressed_size;
    uint64_t uncompressed_size;
    uint8_t compression_type;  // LZO, ZLIB, ZSTD, LZ4
    char compressed_data[];
};

// Write path:
void btrfs_write_compressed(struct inode *inode, char *data, size_t len) {
    // 1. Compress data
    char *compressed = compress_data(data, len, ZSTD);
    size_t comp_len = get_compressed_size(compressed);
    
    // 2. If compression saves space, use it
    if (comp_len < len * 0.9) {  // At least 10% savings
        write_extent(inode, compressed, comp_len, COMPRESSED);
    } else {
        // Compression not worth it, store uncompressed
        write_extent(inode, data, len, UNCOMPRESSED);
    }
}

// Read path:
void* btrfs_read_compressed(struct extent *ext) {
    char *compressed = read_extent(ext->physical_block);
    
    if (ext->flags & COMPRESSED) {
        return decompress_data(compressed, ext->uncompressed_size);
    } else {
        return compressed;  // Already uncompressed
    }
}
```

**Compression Algorithms**:

| Algorithm | Ratio | Speed (comp) | Speed (decomp) | Use Case |
|-----------|-------|--------------|----------------|----------|
| LZ4 | 2-3x | 500 MB/s | 2000 MB/s | Fast, default |
| ZSTD (level 3) | 3-5x | 200 MB/s | 600 MB/s | Balanced |
| ZLIB (gzip) | 4-6x | 50 MB/s | 200 MB/s | Maximum ratio |
| LZO | 2-3x | 400 MB/s | 800 MB/s | Legacy |

**Compression Strategy**:

```c
// Compress in blocks (e.g., 128KB chunks)
// Allows random access without decompressing entire file

struct compressed_file {
    uint32_t block_size;         // Compression block size
    uint32_t num_blocks;
    struct comp_block {
        uint32_t compressed_size;
        uint32_t uncompressed_size;
        uint64_t physical_offset;
    } blocks[];
};

// Read offset 200KB from compressed file:
void* read_compressed_offset(struct compressed_file *cf, uint64_t offset) {
    // Find which compression block contains offset
    uint32_t block_idx = offset / cf->block_size;
    
    // Decompress only that block
    char *compressed = read_block(cf->blocks[block_idx].physical_offset);
    char *uncompressed = decompress(compressed, cf->blocks[block_idx]);
    
    // Return data at offset within block
    uint64_t block_offset = offset % cf->block_size;
    return uncompressed + block_offset;
}
```

### Inline Data

**Small File Optimization**: Store small files directly in inode.

```c
struct inode_inline {
    // Standard inode fields
    uint64_t i_ino;
    uint32_t i_mode;
    // ...
    
    // Inline data (use space normally for block pointers)
    char i_inline_data[60];  // e.g., in ext4 inode
};

// Decision logic:
void write_file(struct inode *inode, char *data, size_t len) {
    if (len <= INLINE_SIZE && !inode->has_extents) {
        // Store inline
        memcpy(inode->i_inline_data, data, len);
        inode->i_size = len;
        inode->i_flags |= INLINE_DATA;
    } else {
        // Use regular extents
        allocate_extents(inode, data, len);
    }
}
```

**Benefits**:
- No separate data block needed
- One I/O to read metadata + data
- Common: Small files, symlinks, directories

### Tail Packing (Reiser

FS)

**Problem**: Last block of file often partially used.

```
File: 12.5 KB, block size 4KB
Block 0: [████████████████] 4KB (full)
Block 1: [████████████████] 4KB (full)
Block 2: [████████████████] 4KB (full)
Block 3: [██████░░░░░░░░░░] 0.5KB (wasted 3.5KB!)
```

**Solution**: Pack multiple file tails into single block.

```c
// Tail packing block:
┌──────────────────────────────────┐
│ File A tail (500 bytes)          │
│ File B tail (1200 bytes)         │
│ File C tail (2000 bytes)         │
│ File D tail (300 bytes)          │
└──────────────────────────────────┘
  Total: 4000 bytes in 4KB block

// Metadata points to offset within shared block
struct file_tail {
    uint64_t block_num;
    uint32_t offset;
    uint32_t length;
};
```

**Trade-offs**:
- **Space savings**: Can save 30-50% for small files
- **Complexity**: Harder to manage, especially with COW
- **Performance**: Extra indirection on access

### Checksumming and Data Integrity

**Protect Against**:
- Disk corruption (bit rot)
- Silent data corruption
- Firmware bugs
- Cosmic rays (seriously!)

**ZFS Checksum Tree**:

```c
struct blkptr {
    uint64_t blk_dva[3];         // Physical locations
    // ...
    zio_cksum_t blk_cksum;       // Checksum of data
};

// Every block pointer contains checksum of data it points to

// Tree structure:
//         [Root: cksum(A,B)]
//        /                  \
//  [A: cksum(D,E)]    [B: cksum(F,G)]
//  /        \         /        \
// [D:data] [E:data] [F:data] [G:data]

// Verification:
void* zfs_read_block(struct blkptr *bp) {
    void *data = read_disk(bp->blk_dva[0]);
    
    // Verify checksum
    zio_cksum_t computed = checksum(data);
    if (!checksums_equal(computed, bp->blk_cksum)) {
        // Corruption detected!
        
        if (bp->blk_dva[1] != 0) {
            // Try mirror copy
            data = read_disk(bp->blk_dva[1]);
            computed = checksum(data);
            
            if (checksums_equal(computed, bp->blk_cksum)) {
                // Mirror is good, repair primary
                write_disk(bp->blk_dva[0], data);
                return data;
            }
        }
        
        // Unrecoverable error
        return NULL;
    }
    
    return data;
}
```

**Checksum Algorithms**:

| Algorithm | Speed | Collision Resistance | Use |
|-----------|-------|---------------------|-----|
| Fletcher2 | Fast | Weak | Legacy ZFS |
| Fletcher4 | Fast | Weak | ZFS default |
| SHA-256 | Slow | Strong | Deduplication |
| Blake3 | Very Fast | Strong | Modern (bcachefs) |

**Btrfs Checksum**:

```c
struct btrfs_csum_item {
    uint8_t csum[BTRFS_CSUM_SIZE];  // 32 bytes for SHA-256
};

// Checksums stored separately in checksum tree
// Indexed by (offset, length)

// Read verification:
void* btrfs_read_data(uint64_t offset, size_t len) {
    void *data = read_extent(offset, len);
    
    // Lookup checksum
    struct btrfs_csum_item *csum = csum_tree_lookup(offset, len);
    
    // Verify
    uint8_t computed[32];
    sha256(data, len, computed);
    
    if (memcmp(computed, csum->csum, 32) != 0) {
        // Corruption!
        handle_corruption(offset, len);
    }
    
    return data;
}
```

### Deduplication

**Block-Level Deduplication**: Identify identical blocks, store once.

```c
// Content-addressed storage
struct dedup_table {
    hash_map<checksum, block_location> dedup_map;
};

// Write with dedup:
void write_block_dedup(char *data, size_t len) {
    // 1. Compute strong hash
    uint8_t hash[32];
    sha256(data, len, hash);
    
    // 2. Check if already exists
    uint64_t existing_block = dedup_map.lookup(hash);
    
    if (existing_block) {
        // Found duplicate, just add reference
        increment_refcount(existing_block);
        update_extent_pointer(existing_block);
    } else {
        // New unique block, write it
        uint64_t new_block = allocate_block();
        write_block(new_block, data);
        dedup_map.insert(hash, new_block);
        set_refcount(new_block, 1);
    }
}
```

**Deduplication Strategies**:

1. **Inline Dedup** (ZFS):
```c
// Check dedup table on every write
// Slow, but saves space immediately
```

2. **Background Dedup** (Btrfs):
```c
// Scan filesystem periodically
// Find duplicates, consolidate

void background_dedup() {
    while (true) {
        // Build hash table of all blocks
        hash_map<checksum, vector<block_num>> blocks;
        
        for (block in filesystem) {
            checksum hash = sha256(block);
            blocks[hash].push_back(block.num);
        }
        
        // For each duplicate set
        for (auto& [hash, block_list] : blocks) {
            if (block_list.size() > 1) {
                // Keep first, redirect others
                uint64_t canonical = block_list[0];
                for (int i = 1; i < block_list.size(); i++) {
                    redirect_block(block_list[i], canonical);
                    free_block(block_list[i]);
                }
            }
        }
        
        sleep(3600);  // Run hourly
    }
}
```

**Trade-offs**:
- **Space savings**: Can be huge for VMs, backups (50%+)
- **RAM usage**: Dedup table can be gigantic
- **Performance**: Hash computation, table lookups
- **Fragmentation**: Breaking up sequential writes

**ZFS Dedup RAM Requirements**:
```
1TB data, 128KB blocks = 8M blocks
Dedup table entry: ~320 bytes
Total RAM: 8M × 320B = 2.5GB

Generally: 5GB RAM per 1TB deduplicated data
```

---

## Flash/SSD Optimizations

### Flash Translation Layer (FTL)

**Problem**: Flash can't update in place (must erase first).

**FTL**: Firmware layer that maps logical to physical blocks.

```
Logical Blocks (OS view):     Physical Flash Pages:
┌────┬────┬────┬────┐        ┌────┬────┬────┬────┐
│ 0  │ 1  │ 2  │ 3  │───────>│ 0  │ 1  │ 2  │ 3  │
└────┴────┴────┴────┘        └────┴────┴────┴────┘

Update logical block 1:
┌────┬────┬────┬────┐        ┌────┬────┬────┬────┐
│ 0  │ 1' │ 2  │ 3  │───────>│ 0  │ X  │ 2  │ 3  │
└────┴────┴────┴────┘        └────┴────┴────┴─1'─┘
                              Old page invalidated
                              New page written to free space
```

**Garbage Collection in FTL**:

```c
// FTL garbage collection
void ftl_gc() {
    // Find block with most invalid pages
    block = find_high_invalid_block();
    
    // Copy valid pages to new block
    for (page in block.pages) {
        if (page.valid) {
            new_location = allocate_page();
            copy_page(page, new_location);
            update_mapping(page.logical_addr, new_location);
        }
    }
    
    // Erase old block
    erase_block(block);
}
```

### Write Amplification

**Problem**: One logical write → multiple physical writes.

```
User writes 4KB:
1. FTL writes 4KB to flash → 4KB written
2. GC moves 124KB of valid pages → 124KB written
3. Total: 128KB written for 4KB user write
   Write amplification: 32x!
```

**Metrics**:
```
Write Amplification Factor (WAF) = 
    Total bytes written to flash / User data written

Good: < 2
Bad: > 10
Terrible: > 20
```

### TRIM/Discard Support

**TRIM**: Tell SSD which blocks are free.

```c
// When file deleted:
void delete_file(struct inode *inode) {
    // Free blocks in filesystem
    for (block in inode->extents) {
        mark_block_free(block);
        
        // Send TRIM command to SSD
        blkdev_issue_discard(block, length);
    }
}

// SSD now knows blocks are free
// Can erase proactively, reducing GC overhead
```

**Without TRIM**:
```
Filesystem frees block → SSD doesn't know
SSD GC still copies "deleted" data
Wasted I/O, higher write amplification
```

**With TRIM**:
```
Filesystem frees block → Sends TRIM → SSD marks invalid
SSD GC skips invalid blocks
Less data to copy, lower write amplification
```

### F2FS Design

**F2FS**: Flash-Friendly File System (Samsung, 2012)

**Key Ideas**:
1. **Log-structured**: Sequential writes (flash-friendly)
2. **Multi-head logging**: Separate logs for hot/cold data
3. **Section cleaning**: Align with flash erase blocks

**Layout**:
```
┌────────────────────────────────────────────────────────┐
│ Superblock │ Checkpoint │ Segment Info │ Node Area    │
│            │            │ Table        │ (metadata)   │
├────────────────────────────────────────────────────────┤
│                    Main Area                           │
│  ┌────────────┬────────────┬────────────┬────────────┐│
│  │ Hot Node   │ Warm Node  │ Cold Node  │ Hot Data   ││
│  │ Segment    │ Segment    │ Segment    │ Segment    ││
│  └────────────┴────────────┴────────────┴────────────┘│
│  ┌────────────┬────────────┬────────────┐             │
│  │ Warm Data  │ Cold Data  │ ...        │             │
│  │ Segment    │ Segment    │            │             │
│  └────────────┴────────────┴────────────┘             │
└────────────────────────────────────────────────────────┘
```

**Temperature-Based Separation**:

```c
enum data_temperature {
    HOT,    // Frequently updated (directories, inodes)
    WARM,   // Normal files
    COLD,   // Rarely updated (multimedia, archives)
};

// Separate logs for each temperature
struct f2fs_sb_info {
    struct curseg_info *curseg[NR_CURSEG_TYPE];
    // curseg[HOT_NODE], curseg[WARM_NODE], curseg[COLD_NODE]
    // curseg[HOT_DATA], curseg[WARM_DATA], curseg[COLD_DATA]
};

void f2fs_write_data(char *data, enum data_temperature temp) {
    struct curseg_info *curseg = select_curseg(temp);
    
    // Append to appropriate log
    write_to_curseg(curseg, data);
    
    if (curseg_full(curseg)) {
        allocate_new_segment(curseg);
    }
}
```

**Benefits of Separation**:
- Hot data segments recycled quickly (high invalid %)
- Cold data segments rarely need cleaning
- Reduces write amplification

**Cleaning (GC)**:

```c
void f2fs_gc() {
    // Find victim segment (highest invalid %)
    segment = select_victim_segment();
    
    if (segment.invalid_ratio < THRESHOLD) {
        return;  // Not worth cleaning yet
    }
    
    // Migrate valid blocks
    for (block in segment.blocks) {
        if (block.valid) {
            // Determine temperature
            temp = get_block_temperature(block);
            
            // Write to appropriate log
            new_location = write_to_curseg(temp, block.data);
            
            // Update node mapping
            update_node_pointer(block.logical, new_location);
        }
    }
    
    // Free segment
    mark_segment_free(segment);
}
```

### Zone/Stream-Aware Allocation

**Modern SSDs**: Support "streams" or "zones" for hint-based placement.

```c
// NVMe Streams
#define NR_STREAMS 8

enum stream_id {
    STREAM_METADATA = 0,
    STREAM_HOT_DATA = 1,
    STREAM_WARM_DATA = 2,
    STREAM_COLD_DATA = 3,
    // ...
};

void write_with_stream(char *data, enum stream_id stream) {
    struct nvme_write_cmd cmd = {
        .data = data,
        .stream_id = stream,  // Hint to SSD
    };
    
    nvme_submit_io(&cmd);
}

// SSD can use hint to:
// - Place related data together
// - Separate hot/cold data
// - Reduce internal fragmentation
```

**Zoned Namespaces (ZNS)**:

```c
// Zoned SSD: Disk divided into zones
// Each zone must be written sequentially

struct zone {
    uint64_t zone_start;
    uint64_t write_pointer;  // Current write position
    uint64_t zone_size;
    enum { EMPTY, OPEN, FULL } state;
};

// Write to zone:
void zone_write(struct zone *z, char *data, size_t len) {
    if (z->state != OPEN) {
        return -EINVAL;
    }
    
    // Must write at write pointer (sequential)
    write_disk(z->write_pointer, data, len);
    z->write_pointer += len;
    
    if (z->write_pointer >= z->zone_start + z->zone_size) {
        z->state = FULL;
    }
}

// Reset zone (erase):
void zone_reset(struct zone *z) {
    erase_zone(z);
    z->write_pointer = z->zone_start;
    z->state = EMPTY;
}
```

**Filesystems for ZNS**:
- **F2FS**: Can use zones directly (log-structured)
- **Btrfs**: Experimental ZNS support
- **ZoneFS**: Expose zones as files directly


---

## Concurrency and Locking

### The Big Kernel Lock (BKL) Problem

**Historical**: Early Linux used single global lock for all filesystem operations.

```c
// Old approach (2.4 kernel era):
void sys_read(int fd, char *buf, size_t len) {
    lock_kernel();  // Global lock!
    // ... perform read ...
    unlock_kernel();
}

// Only ONE filesystem operation at a time!
// Terrible on multi-core systems
```

### Fine-Grained Locking

**Modern Approach**: Lock only what's needed.

```c
// Per-inode locks
struct inode {
    struct rw_semaphore i_rwsem;  // Protects inode metadata
    spinlock_t i_lock;             // Protects inode fields
    // ...
};

// Read file:
ssize_t vfs_read(struct file *file, char *buf, size_t len) {
    struct inode *inode = file->f_inode;
    
    // Acquire read lock (multiple readers OK)
    down_read(&inode->i_rwsem);
    
    ssize_t ret = inode->i_fop->read(file, buf, len);
    
    up_read(&inode->i_rwsem);
    return ret;
}

// Write file:
ssize_t vfs_write(struct file *file, const char *buf, size_t len) {
    struct inode *inode = file->f_inode;
    
    // Acquire write lock (exclusive)
    down_write(&inode->i_rwsem);
    
    ssize_t ret = inode->i_fop->write(file, buf, len);
    
    up_write(&inode->i_rwsem);
    return ret;
}
```

**Lock Hierarchy** (to prevent deadlocks):

```
1. Superblock lock
2. Inode locks (parent before child)
3. Page locks
4. Buffer head locks

Rule: Always acquire in this order!
```

### Range Locks

**Problem**: Per-inode lock serializes all I/O to file.

**Solution**: Lock only the range being accessed.

```c
struct range_lock {
    uint64_t start;
    uint64_t end;
    enum { READ_LOCK, WRITE_LOCK } type;
    struct list_head list;
};

struct inode_range_locks {
    struct list_head range_locks;
    spinlock_t lock;  // Protects the list
};

// Acquire range lock:
void lock_range(struct inode *inode, uint64_t start, uint64_t end, int type) {
    struct range_lock *rl = kmalloc(sizeof(*rl));
    rl->start = start;
    rl->end = end;
    rl->type = type;
    
    spin_lock(&inode->range_locks.lock);
    
    // Check for conflicts
retry:
    list_for_each_entry(existing, &inode->range_locks.range_locks, list) {
        if (ranges_overlap(rl, existing)) {
            if (rl->type == WRITE_LOCK || existing->type == WRITE_LOCK) {
                // Conflict, must wait
                spin_unlock(&inode->range_locks.lock);
                wait_for_range(existing);
                spin_lock(&inode->range_locks.lock);
                goto retry;
            }
            // Both read locks, OK to proceed
        }
    }
    
    // No conflicts, add to list
    list_add(&inode->range_locks.range_locks, &rl->list);
    spin_unlock(&inode->range_locks.lock);
}

// Now multiple threads can write to different parts of same file!
```

**ZFS Range Locks**:

```c
// ZFS zvol (block device over ZFS)
struct zv_request {
    uint64_t zv_offset;
    uint64_t zv_size;
    enum { READ, WRITE } zv_type;
    struct rangelock *zv_rl;
};

void zvol_write(struct zv_request *req) {
    // Lock range
    req->zv_rl = zfs_range_lock(zv,
                                req->zv_offset,
                                req->zv_size,
                                WRITE_LOCK);
    
    // Perform write
    do_zvol_write(req);
    
    // Unlock range
    zfs_range_unlock(req->zv_rl);
}
```

### RCU (Read-Copy-Update)

**Problem**: Locks slow down read-heavy workloads.

**Solution**: Lock-free reads via RCU.

```c
// Dcache (directory entry cache) uses RCU
struct dentry {
    struct qstr d_name;           // Filename
    struct inode *d_inode;        // Inode
    struct dentry *d_parent;      // Parent directory
    struct list_head d_subdirs;   // Children
    // ...
};

// Lookup (no locks!):
struct dentry* d_lookup(struct dentry *parent, struct qstr *name) {
    rcu_read_lock();  // Disable preemption, no actual lock
    
    list_for_each_entry_rcu(dentry, &parent->d_subdirs, d_child) {
        if (dentry->d_name.hash == name->hash &&
            dentry->d_name.len == name->len &&
            memcmp(dentry->d_name.name, name->name, name->len) == 0) {
            
            rcu_read_unlock();
            return dentry;
        }
    }
    
    rcu_read_unlock();
    return NULL;
}

// Update (rare):
void d_add(struct dentry *parent, struct dentry *new) {
    spin_lock(&parent->d_lock);  // Brief lock for write
    
    list_add_rcu(&new->d_child, &parent->d_subdirs);
    
    spin_unlock(&parent->d_lock);
    
    // Old readers may still see old list
    // That's OK, they'll finish soon
}

// Delete:
void d_delete(struct dentry *dentry) {
    spin_lock(&dentry->d_parent->d_lock);
    
    list_del_rcu(&dentry->d_child);  // Remove from list
    
    spin_unlock(&dentry->d_parent->d_lock);
    
    // Can't free immediately! Readers might still access
    call_rcu(&dentry->d_rcu, dentry_free);  // Free after grace period
}
```

**RCU Grace Period**:
```
Reader 1: ────[RCU read]────────
Reader 2: ──────────[RCU read]──
Writer:   ──[update]──[wait]──[free]──
                      ↑        ↑
                      Grace period
                      All readers done
```

### Lock-Free Data Structures

**Lock-Free Stack** (for free block lists):

```c
struct lfstack_node {
    void *data;
    struct lfstack_node *next;
};

struct lfstack {
    struct lfstack_node *top;
};

// Push (lock-free):
void lfstack_push(struct lfstack *stack, void *data) {
    struct lfstack_node *node = malloc(sizeof(*node));
    node->data = data;
    
    do {
        node->next = stack->top;
    } while (!__sync_bool_compare_and_swap(&stack->top, node->next, node));
}

// Pop (lock-free):
void* lfstack_pop(struct lfstack *stack) {
    struct lfstack_node *old_top;
    struct lfstack_node *new_top;
    
    do {
        old_top = stack->top;
        if (old_top == NULL) {
            return NULL;  // Stack empty
        }
        new_top = old_top->next;
    } while (!__sync_bool_compare_and_swap(&stack->top, old_top, new_top));
    
    void *data = old_top->data;
    free(old_top);  // Careful: ABA problem!
    return data;
}
```

**ABA Problem**:
```
Thread 1: Reads top = A
Thread 2: Pops A, pops B, pushes A back
Thread 1: CAS succeeds (top still A), but A is different!
```

**Solution**: Use generation counters or hazard pointers.

### Scalability Techniques

**Per-CPU Data Structures**:

```c
// Free block allocation per-CPU
struct percpu_allocator {
    struct free_list *cpu_freelists[NR_CPUS];
};

void* allocate_block() {
    int cpu = smp_processor_id();
    
    // No lock needed! Each CPU has own list
    void *block = pop_from_freelist(cpu_freelists[cpu]);
    
    if (block == NULL) {
        // Local list empty, steal from another CPU
        block = steal_from_other_cpu();
    }
    
    return block;
}
```

**Parallel Bitmap Scanning**:

```c
// Scan bitmap in parallel
uint32_t find_free_block_parallel(uint8_t *bitmap, size_t size) {
    int nr_cpus = num_online_cpus();
    uint32_t chunk_size = size / nr_cpus;
    
    // Each CPU scans its chunk
    parallel_for(cpu = 0; cpu < nr_cpus; cpu++) {
        uint32_t start = cpu * chunk_size;
        uint32_t end = start + chunk_size;
        
        uint32_t block = scan_bitmap(bitmap, start, end);
        if (block != NOTFOUND) {
            return block;
        }
    }
    
    return NOTFOUND;
}
```

---

## Caching Strategies

### Page Cache

**Unified Page Cache**: Backs both file data and memory-mapped files.

```c
struct address_space {
    struct inode *host;             // File inode
    struct radix_tree_root page_tree; // Pages indexed by offset
    spinlock_t tree_lock;           // Protects radix tree
    unsigned long nrpages;          // Number of pages
    const struct address_space_operations *a_ops;
};

struct page {
    unsigned long flags;            // PG_locked, PG_dirty, etc.
    atomic_t _refcount;             // Reference count
    struct address_space *mapping;  // Back pointer
    pgoff_t index;                  // Offset in file
    // ...
};

// Read page:
struct page* find_get_page(struct address_space *mapping, pgoff_t offset) {
    rcu_read_lock();
    
    struct page *page = radix_tree_lookup(&mapping->page_tree, offset);
    
    if (page) {
        get_page(page);  // Increment refcount
    }
    
    rcu_read_unlock();
    return page;
}

// Add page:
void add_to_page_cache(struct page *page, struct address_space *mapping,
                       pgoff_t offset) {
    spin_lock(&mapping->tree_lock);
    
    radix_tree_insert(&mapping->page_tree, offset, page);
    mapping->nrpages++;
    page->mapping = mapping;
    page->index = offset;
    
    spin_unlock(&mapping->tree_lock);
}
```

### Write-Back vs Write-Through

**Write-Through**: Write to cache and disk immediately.

```c
ssize_t write_through(struct file *file, const char *buf, size_t len) {
    // 1. Update page cache
    update_page_cache(file, buf, len);
    
    // 2. Write to disk immediately
    write_to_disk(file, buf, len);  // Synchronous!
    
    return len;
}
```

**Write-Back** (default): Write to cache, flush later.

```c
ssize_t write_back(struct file *file, const char *buf, size_t len) {
    // 1. Update page cache
    struct page *page = update_page_cache(file, buf, len);
    
    // 2. Mark dirty (will be flushed later)
    set_page_dirty(page);
    
    return len;  // Return immediately!
}

// Flusher thread (pdflush/kswapd):
void flusher_thread() {
    while (true) {
        sleep(30);  // Every 30 seconds
        
        // Find dirty pages older than 30 seconds
        for_each_dirty_page(page) {
            if (page_age(page) > 30) {
                writeback_page(page);
            }
        }
    }
}
```

### Dirty Page Threshold

**Balance**: Too many dirty pages → crash loses lots of data.

```c
// sysctl controls:
unsigned long dirty_ratio = 20;          // Max 20% of RAM dirty
unsigned long dirty_background_ratio = 10; // Start background writeback

void balance_dirty_pages(struct address_space *mapping) {
    unsigned long nr_dirty = global_page_state(NR_FILE_DIRTY);
    unsigned long nr_memory = totalram_pages;
    
    if (nr_dirty * 100 / nr_memory > dirty_ratio) {
        // Threshold exceeded, block writer until clean
        writeback_inodes_sb(mapping->host->i_sb);
        wait_for_writeback();
    } else if (nr_dirty * 100 / nr_memory > dirty_background_ratio) {
        // Start background writeback
        wakeup_flusher_threads();
    }
}
```

### Read-Ahead

**Sequential Read Detection**:

```c
struct file_ra_state {
    pgoff_t start;          // First page of current window
    unsigned int size;      // Current window size
    unsigned int async_size; // Async readahead size
    pgoff_t prev_pos;       // Previous read position
};

void page_cache_sync_readahead(struct file *file, pgoff_t offset) {
    struct file_ra_state *ra = &file->f_ra;
    
    // Detect sequential pattern
    if (offset == ra->prev_pos + 1) {
        // Sequential! Increase readahead window
        ra->size = min(ra->size * 2, MAX_READAHEAD);
        
        // Start async readahead
        page_cache_async_readahead(file, offset, ra->size);
    } else {
        // Random access, reset window
        ra->size = MIN_READAHEAD;
    }
    
    ra->prev_pos = offset;
}

// Async readahead in background:
void page_cache_async_readahead(struct file *file, pgoff_t offset, int size) {
    for (int i = 0; i < size; i++) {
        struct page *page = alloc_page();
        add_to_page_cache(page, file->f_mapping, offset + i);
        submit_async_read(page);  // Non-blocking
    }
}
```

**Benefits**:
- Hides disk latency for sequential reads
- Can achieve line-rate throughput
- Adaptive based on access pattern

### Buffer Cache vs Page Cache

**Historical**: Separate caches for block device and files.

```
Old (Linux 2.2):
┌─────────────┐  ┌─────────────┐
│ Page Cache  │  │Buffer Cache │
│ (file data) │  │ (block I/O) │
└─────────────┘  └─────────────┘
       Duplication if file is block-aligned!
```

**Modern** (Linux 2.4+): Unified page cache.

```
New:
┌─────────────────────────┐
│    Unified Page Cache   │
│  (files + block device) │
└─────────────────────────┘
       No duplication!
```

### Direct I/O

**Bypass page cache** for applications with own caching (databases).

```c
int fd = open("/data/db.dat", O_DIRECT);  // Bypass page cache

// Direct I/O requirements:
// - Buffer aligned to 512 bytes
// - Size multiple of 512 bytes
// - Offset multiple of 512 bytes

char *buf = aligned_alloc(512, 4096);
pread(fd, buf, 4096, 0);  // Direct to disk, no caching
```

**Use Cases**:
- Databases (Oracle, PostgreSQL with direct I/O)
- Video editing (large sequential files)
- Backup tools

**Trade-offs**:
- **Faster** for large sequential I/O (no copy to cache)
- **Slower** for random I/O (no caching benefit)
- **More CPU**: Must handle alignment manually

---

## RAID and Redundancy

### RAID Levels

**RAID 0**: Striping (no redundancy)

```
Data: [A][B][C][D][E][F][G][H]

Disk 1: [A][C][E][G]
Disk 2: [B][D][F][H]

Capacity: N disks
Performance: N× read, N× write
Reliability: Any disk failure → total loss
```

**RAID 1**: Mirroring

```
Data: [A][B][C][D]

Disk 1: [A][B][C][D]
Disk 2: [A][B][C][D]

Capacity: N/2 disks
Performance: N× read, 1× write
Reliability: Can lose N-1 disks
```

**RAID 5**: Striping + Distributed Parity

```
Data: [A1][A2][A3]

Disk 1: [A1][B1][C1][Dp]  ← Parity for [A2][A3]
Disk 2: [A2][B2][Cp][D1]  ← Parity for [A1][A3]
Disk 3: [A3][Bp][C2][D2]  ← Parity for [A1][A2]
Disk 4: [Ap][B3][C3][D3]  ← Parity for [B1][B2][B3]

Parity: Ap = A1 XOR A2 XOR A3

Capacity: (N-1) disks
Rebuild: If disk 2 fails, A2 = A1 XOR A3 XOR Ap
```

**RAID 6**: Dual Parity

```
Like RAID 5 but two parity blocks
Can survive 2 disk failures

Capacity: (N-2) disks
```

### ZFS RAID-Z

**RAID-Z**: Like RAID 5 but variable stripe width.

```c
struct raidz_map {
    uint64_t rm_cols;         // Number of columns (disks)
    uint64_t rm_rows;         // Number of rows (stripes)
    uint64_t rm_firstdatacol; // First data column
    struct raidz_col {
        uint64_t rc_devidx;   // Device index
        uint64_t rc_offset;   // Offset on device
        uint64_t rc_size;     // Size of data
        void *rc_data;        // Data buffer
    } rm_col[1];
};

// Write to RAID-Z:
void vdev_raidz_write(vdev_t *vd, zio_t *zio) {
    // 1. Split data into columns
    raidz_map_t *rm = vdev_raidz_map_alloc(zio);
    
    // 2. Calculate parity
    vdev_raidz_generate_parity(rm);
    
    // 3. Write each column to disk
    for (int c = 0; c < rm->rm_cols; c++) {
        vdev_disk_write(rm->rm_col[c].rc_devidx,
                        rm->rm_col[c].rc_offset,
                        rm->rm_col[c].rc_data,
                        rm->rm_col[c].rc_size);
    }
}

// Parity calculation (SIMD-optimized):
void vdev_raidz_generate_parity(raidz_map_t *rm) {
    uint8_t *p = rm->rm_col[PARITY_COL].rc_data;
    memset(p, 0, rm->rm_col[PARITY_COL].rc_size);
    
    // XOR all data columns
    for (int c = rm->rm_firstdatacol; c < rm->rm_cols; c++) {
        uint8_t *d = rm->rm_col[c].rc_data;
        size_t size = rm->rm_col[c].rc_size;
        
        // SIMD XOR
        for (size_t i = 0; i < size; i += 32) {
            __m256i pv = _mm256_load_si256((__m256i*)(p + i));
            __m256i dv = _mm256_load_si256((__m256i*)(d + i));
            __m256i xv = _mm256_xor_si256(pv, dv);
            _mm256_store_si256((__m256i*)(p + i), xv);
        }
    }
}
```

**RAID-Z2**: Dual parity (Reed-Solomon codes)

```c
// Reed-Solomon parity
void vdev_raidz2_generate_parity(raidz_map_t *rm) {
    uint8_t *p = rm->rm_col[PARITY1].rc_data;
    uint8_t *q = rm->rm_col[PARITY2].rc_data;
    
    // P = D1 XOR D2 XOR D3 XOR ...
    // Q = g^0*D1 XOR g^1*D2 XOR g^2*D3 XOR ...
    //   (GF(256) arithmetic, g = generator)
    
    memset(p, 0, size);
    memset(q, 0, size);
    
    for (int c = rm->rm_firstdatacol; c < rm->rm_cols; c++) {
        uint8_t *d = rm->rm_col[c].rc_data;
        int exp = c - rm->rm_firstdatacol;
        
        for (size_t i = 0; i < size; i++) {
            p[i] ^= d[i];
            q[i] ^= gf_mul(d[i], gf_exp[exp]);  // Galois field multiply
        }
    }
}
```

### Scrubbing

**Detect Silent Corruption**: Periodically verify all data.

```c
void zfs_scrub(zpool_t *pool) {
    for_each_vdev(pool, vdev) {
        for_each_block(vdev, blkptr) {
            // Read block
            void *data = read_block(blkptr);
            
            // Verify checksum
            zio_checksum_t computed;
            checksum(data, &computed);
            
            if (!checksum_equal(computed, blkptr->checksum)) {
                // Corruption detected!
                
                if (has_redundancy(blkptr)) {
                    // Try to read from mirror/parity
                    void *good_data = read_redundant_copy(blkptr);
                    
                    if (checksum_verify(good_data, blkptr->checksum)) {
                        // Repair
                        write_block(blkptr, good_data);
                        log_repair(blkptr);
                    } else {
                        // Unrecoverable
                        log_error(blkptr);
                    }
                } else {
                    // No redundancy, data lost
                    log_data_loss(blkptr);
                }
            }
        }
    }
}
```

**Scrub Schedule**:
- **ZFS**: Monthly recommended
- **Btrfs**: Weekly to monthly
- **Impact**: Background I/O, can slow performance


### Self-Healing

**Automatic Repair** when redundancy available.

```c
void* zfs_read_with_heal(blkptr_t *bp) {
    void *data = read_block(bp->dva[0]);  // Read from primary
    
    if (!checksum_verify(data, bp->checksum)) {
        // Primary corrupted, try mirror
        for (int i = 1; i < bp->ndvas; i++) {
            data = read_block(bp->dva[i]);
            
            if (checksum_verify(data, bp->checksum)) {
                // Found good copy, repair primary
                write_block(bp->dva[0], data);
                log_repair(bp->dva[0]);
                return data;
            }
        }
        
        // All copies bad, unrecoverable
        return NULL;
    }
    
    return data;  // Primary was good
}
```

---

## Advanced Features

### Snapshots

**Read-Only Snapshots** (Btrfs):

```c
// Create snapshot
int btrfs_snapshot(struct btrfs_root *root, char *name) {
    struct btrfs_root *snap_root;
    
    // 1. Clone root node (COW)
    snap_root = btrfs_copy_root(root);
    
    // 2. Add to snapshot tree
    btrfs_add_snapshot(snap_root, name);
    
    // 3. Done! Old tree now frozen
    return 0;
}

// Snapshot is just a pointer to old root
// All shared blocks reference-counted
```

**Writable Snapshots** (ZFS clones):

```c
// Clone dataset (writable snapshot)
int zfs_clone(dataset_t *origin, char *clone_name) {
    // 1. Create new dataset
    dataset_t *clone = create_dataset(clone_name);
    
    // 2. Point to same data
    clone->ds_bp = origin->ds_bp;  // Share block pointer
    
    // 3. Mark as clone
    clone->ds_origin = origin;
    
    // 4. Writes to clone are COW'd
    //    Reads go to origin for unchanged blocks
    
    return 0;
}
```

### Reflinks (CoW File Copies)

**cp --reflink**: Instant file copy via COW.

```bash
# Traditional copy:
cp file1 file2  # Copies all data (slow, doubles space)

# Reflink copy:
cp --reflink=always file1 file2  # Instant! Shares blocks
```

```c
// XFS reflink implementation
int xfs_reflink(struct file *src, struct file *dst) {
    struct inode *src_inode = src->f_inode;
    struct inode *dst_inode = dst->f_inode;
    
    // Copy extent tree, increment refcounts
    for_each_extent(src_inode, extent) {
        // Add same extent to destination
        xfs_add_extent(dst_inode, extent);
        
        // Increment reference count
        extent->refcount++;
    }
    
    // Files now share data blocks
    // Writes are COW'd
}
```

**Benefits**:
- Instant large file copies
- Space-efficient backups
- VM cloning

### Send/Receive (Replication)

**ZFS Send**Stream**:

```c
// Send incremental snapshot stream
void zfs_send_incremental(dataset_t *ds, snapshot_t *from, snapshot_t *to,
                          int fd) {
    // Walk both snapshots, find differences
    diff_iterator_t *iter = create_diff_iterator(from, to);
    
    while (diff_next(iter, &change)) {
        switch (change.type) {
        case BLOCK_NEW:
            // New block, send it
            send_record(fd, BLOCK_NEW, change.block, change.data);
            break;
            
        case BLOCK_MODIFIED:
            // Block changed, send new version
            send_record(fd, BLOCK_MOD, change.block, change.new_data);
            break;
            
        case BLOCK_DELETED:
            // Block deleted
            send_record(fd, BLOCK_DEL, change.block);
            break;
        }
    }
    
    send_end(fd);
}

// Receive and reconstruct
void zfs_receive(int fd, dataset_t *ds) {
    while (true) {
        record_t rec = read_record(fd);
        
        switch (rec.type) {
        case BLOCK_NEW:
            write_block(ds, rec.block, rec.data);
            break;
            
        case BLOCK_MOD:
            update_block(ds, rec.block, rec.data);
            break;
            
        case BLOCK_DEL:
            delete_block(ds, rec.block);
            break;
            
        case END:
            return;  // Done
        }
    }
}
```

**Use Cases**:
- Replication to remote server
- Backup/restore
- Migration between systems

### Encryption at Rest

**Per-Dataset Encryption** (ZFS):

```c
struct dsl_crypto_params {
    enum zio_encrypt algorithm;  // AES-128-CCM, AES-256-GCM, etc.
    uint8_t wrapping_key[32];    // User's key
    uint8_t dek[32];             // Data Encryption Key (DEK)
    uint8_t mac[32];             // MAC of DEK
    uint64_t salt;
    uint64_t iters;              // PBKDF2 iterations
};

// Encrypt block
void zio_encrypt(zio_t *zio, dsl_crypto_key_t *key) {
    uint8_t iv[ZIO_DATA_IV_LEN];
    
    // Generate IV from block pointer (deterministic!)
    zio_crypt_gen_iv(iv, zio->io_bookmark);
    
    // Encrypt data
    crypto_encrypt(key->dck_key,    // DEK
                   iv,               // IV
                   zio->io_data,     // Plaintext
                   zio->io_size,     // Size
                   zio->io_abd);     // Ciphertext output
    
    // Update MAC
    crypto_mac_update(&zio->io_mac, zio->io_abd, zio->io_size);
}

// Key hierarchy:
// User Password
//    ↓ (PBKDF2)
// Wrapping Key
//    ↓ (AES-Unwrap)
// Data Encryption Key (DEK)
//    ↓ (AES-GCM)
// Encrypted Blocks
```

**Changing Password** (no re-encryption!):

```c
void zfs_change_password(dataset_t *ds, char *old_pass, char *new_pass) {
    // 1. Derive old wrapping key
    uint8_t old_wk[32];
    pbkdf2(old_pass, ds->salt, ds->iters, old_wk);
    
    // 2. Unwrap DEK
    uint8_t dek[32];
    aes_unwrap(old_wk, ds->wrapped_dek, dek);
    
    // 3. Derive new wrapping key
    uint8_t new_wk[32];
    pbkdf2(new_pass, ds->salt, ds->iters, new_wk);
    
    // 4. Wrap DEK with new key
    aes_wrap(new_wk, dek, ds->wrapped_dek);
    
    // Done! Data blocks unchanged
}
```

### Quotas and Resource Limits

**User Quotas**:

```c
struct quota {
    uid_t uid;
    uint64_t blocks_used;
    uint64_t blocks_soft_limit;  // Warning
    uint64_t blocks_hard_limit;  // Enforce
    uint64_t inodes_used;
    uint64_t inodes_limit;
};

// Check quota on write
int check_quota(struct inode *inode, size_t blocks) {
    struct quota *q = get_user_quota(inode->i_uid);
    
    if (q->blocks_used + blocks > q->blocks_hard_limit) {
        return -EDQUOT;  // Quota exceeded
    }
    
    if (q->blocks_used + blocks > q->blocks_soft_limit) {
        warn_user(inode->i_uid, "Approaching quota limit");
    }
    
    // Update quota
    q->blocks_used += blocks;
    return 0;
}
```

**Dataset Quotas** (ZFS):

```c
// Per-dataset limits
zfs set quota=100G pool/user1
zfs set refquota=50G pool/user1  // Exclude snapshots

// Reservation (guaranteed space)
zfs set reservation=20G pool/database
```

### Online Resize

**Grow Filesystem**:

```c
// ext4 online resize
int ext4_resize_fs(struct super_block *sb, ext4_fsblk_t n_blocks) {
    ext4_fsblk_t old_blocks = ext4_blocks_count(sb);
    
    if (n_blocks <= old_blocks) {
        return -EINVAL;  // Can only grow online
    }
    
    // 1. Add new block groups
    int new_groups = (n_blocks - old_blocks) / blocks_per_group;
    
    for (int i = 0; i < new_groups; i++) {
        ext4_add_new_group(sb);
    }
    
    // 2. Update superblock
    ext4_blocks_count_set(sb, n_blocks);
    ext4_free_blocks_count_add(sb, n_blocks - old_blocks);
    
    // 3. Done! Filesystem now larger
    return 0;
}
```

**Shrink Filesystem** (harder - must relocate data):

```c
int btrfs_shrink_device(struct btrfs_device *device, uint64_t new_size) {
    uint64_t old_size = device->total_bytes;
    
    // 1. Find all data beyond new_size
    for_each_extent_beyond(device, new_size, extent) {
        // 2. Relocate to space before new_size
        new_location = find_free_space(device, 0, new_size);
        
        if (new_location == 0) {
            return -ENOSPC;  // Can't shrink, not enough free space
        }
        
        relocate_extent(extent, new_location);
    }
    
    // 3. Update device size
    device->total_bytes = new_size;
    
    return 0;
}
```

---

## Performance Optimization

### Batching and Request Merging

**I/O Scheduler**: Merge and reorder requests.

```c
struct io_request {
    sector_t sector;
    size_t size;
    enum { READ, WRITE } type;
    struct list_head list;
};

// Merge adjacent requests
void merge_requests(struct list_head *queue) {
    struct io_request *req, *next;
    
    list_for_each_entry_safe(req, next, queue, list) {
        // Check if adjacent
        if (req->sector + req->size == next->sector &&
            req->type == next->type) {
            // Merge!
            req->size += next->size;
            list_del(&next->list);
            free(next);
        }
    }
}

// Example:
// Before: Read(0, 4KB), Read(4KB, 4KB), Read(8KB, 4KB)
// After:  Read(0, 12KB)
//         One I/O instead of three!
```

### Elevator Algorithms

**Deadline Scheduler**: Prevent starvation.

```c
struct deadline_data {
    struct rb_root sort_list;       // Sorted by sector
    struct list_head fifo_list[2];  // FIFO per type (read/write)
    unsigned long expires[2];       // Deadlines
};

struct io_request* deadline_dispatch(struct deadline_data *dd) {
    // Check if any request expired
    for (int i = 0; i < 2; i++) {
        if (!list_empty(&dd->fifo_list[i])) {
            struct io_request *req = list_first_entry(&dd->fifo_list[i]);
            
            if (time_after(jiffies, dd->expires[i])) {
                // Deadline expired, must serve this
                return req;
            }
        }
    }
    
    // No expired, serve by sector order (minimize seek)
    return rb_entry(rb_first(&dd->sort_list), struct io_request, rb_node);
}
```

### Metadata Clustering

**Keep Related Metadata Together**:

```c
// ext4: Inode table clustered with data blocks in same block group
// Benefit: Reading directory → inode → data requires minimal seeking

// XFS: Allocation groups with local inode B-trees
// Benefit: Directory operations localized to single AG
```

### Sequential Optimization

**Optimize for Sequential Access**:

```c
// Detect sequential write pattern
bool is_sequential(struct file *file, loff_t offset) {
    loff_t expected = file->f_pos;
    return (offset == expected) || (offset == expected + BLOCK_SIZE);
}

// Allocate contiguously for sequential writes
void allocate_sequential(struct inode *inode, size_t size) {
    if (is_sequential_write_pattern(inode)) {
        // Try to extend last extent
        struct extent *last = get_last_extent(inode);
        
        if (can_extend(last, size)) {
            extend_extent(last, size);
        } else {
            // Allocate large contiguous chunk
            allocate_extent(inode, size, CONTIGUOUS);
        }
    } else {
        // Random writes, use normal allocation
        allocate_extent(inode, size, 0);
    }
}
```

---

## Key Research Papers

### Foundational Papers

1. **"A Fast File System for UNIX"** (McKusick et al., 1984)
   - Introduced cylinder groups (locality)
   - Block sizes, fragments
   - Foundation of BSD FFS

2. **"The Design and Implementation of a Log-Structured File System"** (Rosenblum & Ousterhout, 1992)
   - LFS concept
   - Log-structured approach
   - Garbage collection challenges

3. **"Soft Updates: A Solution to the Metadata Update Problem in File Systems"** (Ganger & Patt, 1994)
   - Alternative to journaling
   - Ordered writes for consistency
   - No double-write overhead

4. **"Extent-like Performance from a UNIX File System"** (McVoy & Kleiman, 1991)
   - Extent-based allocation
   - Benefits over block-mapped files

5. **"Analysis and Evolution of Journaling File Systems"** (Prabhakaran et al., 2005)
   - Comprehensive study of ext3, ReiserFS, JFS
   - Journal performance analysis

### Modern Innovations

6. **"BTRFS: The Linux B-tree Filesystem"** (Rodeh, 2007)
   - COW B-trees
   - Snapshots and subvolumes
   - Integration with Linux

7. **"ZFS: The Last Word in File Systems"** (Bonwick & Moore, 2007)
   - End-to-end data integrity
   - RAID-Z
   - Checksum trees

8. **"F2FS: A New File System for Flash Storage"** (Lee et al., 2015)
   - Flash-aware design
   - Multi-head logging
   - Temperature-based data separation

9. **"NOVA: A Log-structured File System for Hybrid Volatile/Non-volatile Main Memories"** (Xu & Swanson, 2016)
   - Per-inode logs
   - NVMM optimization
   - Atomic operations via logging

10. **"SplitFS: Reducing Software Overhead in File Systems for Persistent Memory"** (Kadekodi et al., 2019)
    - Direct access for data
    - POSIX for metadata
    - Hybrid approach for PM

### Performance Studies

11. **"An Analysis of Linux Scalability to Many Cores"** (Boyd-Wickizer et al., 2010)
    - Identifies scalability bottlenecks
    - Lock contention in VFS
    - Proposed solutions

12. **"All File Systems Are Not Created Equal"** (Harter et al., 2011)
    - Comparative study of filesystems
    - Workload-specific performance
    - Trade-offs analysis

---

## Implementation Guide

### Step 1: Define Requirements

**Questions to Answer**:
- **Use case**: OLTP? OLAP? General purpose?
- **Media**: HDD? SSD? NVMe? Persistent memory?
- **Scale**: Gigabytes? Terabytes? Petabytes?
- **Features**: Snapshots? Encryption? Compression?
- **Reliability**: Single disk? RAID? Checksums?

### Step 2: Choose Core Design

**Decision Tree**:

```
Is data > RAM?
├─ Yes → Need buffer manager
│  ├─ Traditional (bitmaps)
│  └─ Modern (B-tree space maps)
└─ No → In-memory FS

Sequential or random access?
├─ Sequential → Log-structured
└─ Mixed → Traditional with optimization

Crash consistency how?
├─ Journaling (metadata or full)
├─ Soft Updates (careful ordering)
└─ COW (Btrfs/ZFS style)

Snapshots needed?
├─ Yes → COW required
└─ No → Either works
```

### Step 3: On-Disk Format

**Design Structures**:

```c
// Superblock
struct my_superblock {
    uint32_t magic;               // 0x4D594653 ("MYFS")
    uint32_t version;
    uint64_t block_size;
    uint64_t total_blocks;
    uint64_t free_blocks;
    uint64_t root_inode;
    // ...
};

// Inode
struct my_inode {
    uint64_t ino;
    uint16_t mode;
    uint32_t uid, gid;
    uint64_t size;
    struct timespec atime, mtime, ctime;
    
    // Extent tree root
    struct my_extent_header extent_header;
    struct my_extent extents[4];   // Or pointer to external tree
};

// Extent
struct my_extent {
    uint64_t logical_start;
    uint64_t physical_start;
    uint32_t length;
    uint32_t flags;
};

// Directory entry
struct my_dirent {
    uint64_t ino;
    uint16_t rec_len;
    uint8_t name_len;
    uint8_t file_type;
    char name[];                   // Variable length
};
```

### Step 4: In-Memory Structures

**VFS Integration** (Linux):

```c
// Superblock operations
struct super_operations my_sops = {
    .alloc_inode = my_alloc_inode,
    .destroy_inode = my_destroy_inode,
    .write_inode = my_write_inode,
    .evict_inode = my_evict_inode,
    .put_super = my_put_super,
    .statfs = my_statfs,
};

// Inode operations
struct inode_operations my_iops = {
    .create = my_create,
    .lookup = my_lookup,
    .link = my_link,
    .unlink = my_unlink,
    .mkdir = my_mkdir,
    .rmdir = my_rmdir,
    .rename = my_rename,
};

// File operations
struct file_operations my_fops = {
    .read_iter = my_read_iter,
    .write_iter = my_write_iter,
    .open = my_open,
    .release = my_release,
    .fsync = my_fsync,
    .fallocate = my_fallocate,
};

// Address space operations (page cache)
struct address_space_operations my_aops = {
    .readpage = my_readpage,
    .readpages = my_readpages,
    .writepage = my_writepage,
    .writepages = my_writepages,
    .write_begin = my_write_begin,
    .write_end = my_write_end,
};
```

### Step 5: Block Allocation

**Implement Allocator**:

```c
// Simple bitmap allocator
uint64_t allocate_blocks(struct my_sb_info *sbi, uint32_t count) {
    spin_lock(&sbi->s_lock);
    
    uint64_t start = bitmap_find_free_range(sbi->s_block_bitmap,
                                           sbi->s_total_blocks,
                                           count);
    
    if (start == NOTFOUND) {
        spin_unlock(&sbi->s_lock);
        return 0;  // ENOSPC
    }
    
    bitmap_set_range(sbi->s_block_bitmap, start, count);
    sbi->s_free_blocks -= count;
    
    spin_unlock(&sbi->s_lock);
    
    mark_buffer_dirty(sbi->s_bitmap_bh);
    return start;
}

void free_blocks(struct my_sb_info *sbi, uint64_t start, uint32_t count) {
    spin_lock(&sbi->s_lock);
    
    bitmap_clear_range(sbi->s_block_bitmap, start, count);
    sbi->s_free_blocks += count;
    
    spin_unlock(&sbi->s_lock);
    
    mark_buffer_dirty(sbi->s_bitmap_bh);
}
```

### Step 6: Crash Consistency

**Simple Metadata Journal**:

```c
struct my_journal {
    uint64_t j_head;       // Next write position
    uint64_t j_tail;       // Oldest uncommitted
    uint64_t j_commit_seq; // Last committed transaction
    struct buffer_head *j_bh;
};

// Start transaction
handle_t* my_journal_start(struct my_sb_info *sbi) {
    handle_t *handle = kmalloc(sizeof(*handle));
    handle->h_transaction = atomic_inc(&sbi->j_running_transaction);
    return handle;
}

// Add metadata block to transaction
void my_journal_get_write_access(handle_t *handle, struct buffer_head *bh) {
    // Copy original content to journal
    journal_write_metadata(handle->h_transaction, bh);
}

// Commit transaction
int my_journal_stop(handle_t *handle) {
    // Write commit record
    journal_write_commit(handle->h_transaction);
    
    // Wait for I/O
    wait_on_buffer(journal_bh);
    
    kfree(handle);
    return 0;
}
```

### Step 7: Testing

**Critical Test Cases**:

1. **Correctness**:
   - Create/delete files
   - Read/write with various sizes
   - Large files (> 4GB)
   - Many small files
   - Deep directory trees

2. **Crash Consistency**:
   - Power-fail testing (dm-flakey)
   - Check fsck after crashes
   - Verify data integrity

3. **Performance**:
   - `fio` benchmarks (sequential/random)
   - `dbench` (file server workload)
   - `filebench` (complex workloads)
   - Metadata-heavy tests

4. **Stress Testing**:
   - Fill filesystem to 100%
   - Many concurrent operations
   - Large file deletions
   - Fragmentation scenarios

**Example fio Test**:

```bash
# Sequential write
fio --name=seqwrite --rw=write --bs=1M --size=10G --numjobs=1

# Random write
fio --name=randwrite --rw=randwrite --bs=4K --size=10G --numjobs=4

# Mixed workload
fio --name=mixed --rw=randrw --rwmixread=70 --bs=4K --size=10G
```

### Step 8: Optimization

**Profile and Optimize**:

```bash
# Perf profiling
perf record -g -a -- fio workload.fio
perf report

# Look for:
# - Lock contention (spin_lock, mutex_lock)
# - Cache misses
# - Expensive functions
```

**Common Optimizations**:
1. Reduce lock scope
2. Use RCU for read-heavy paths
3. Batch operations
4. Pre-allocate structures
5. SIMD for checksums/compression

### Step 9: Documentation

**Essential Documentation**:
- **On-disk format specification**
- **Design rationale**
- **API documentation**
- **Tuning guide**
- **Recovery procedures**

### Step 10: Maintenance

**Long-term Considerations**:
- **Versioning**: Support old formats
- **Migration tools**: For upgrades
- **Monitoring**: Health checks, SMART data
- **Community**: Bug reports, patches

---

## Conclusion

Building a state-of-the-art filesystem requires mastering:

1. **Data Structures**: B-trees, extent trees, space maps
2. **Algorithms**: Allocation, journaling, garbage collection  
3. **Concurrency**: Fine-grained locking, RCU, lock-free structures
4. **Storage Media**: HDD, SSD, NVMe, persistent memory
5. **Reliability**: Checksums, redundancy, crash consistency
6. **Performance**: Caching, batching, SIMD, parallelism

**Key Trade-offs**:
- Reliability vs Performance
- Simplicity vs Features
- Space vs Speed
- Generality vs Specialization

**Recommended Learning Path**:
1. Study existing filesystems (ext4 source code)
2. Implement simple FS in FUSE (userspace)
3. Add features incrementally
4. Profile and optimize
5. Graduate to kernel module

**Resources**:
- Linux kernel source: `fs/` directory
- FUSE: Easy prototyping in userspace
- `xfstests`: Comprehensive FS test suite
- Papers: Read everything from SOSP, OSDI, FAST conferences

**Final Advice**:
Start simple, test thoroughly, optimize carefully. 
The best filesystem is one that doesn't lose your data!

---

*Document created: 2026-01-24*
*Total: 3,800+ lines of expert-level filesystem design knowledge*
*Ready for building production-quality filesystems!*

---

## See Also

- [WAL, Torn Pages, and Disk Reliability](@/notes/database/wal_torn_pages.md) — Database WAL durability depends on filesystem journaling and fsync semantics
- [io_uring Internals](@/notes/os/io_uring_internals.md) — Modern async I/O interface for filesystem operations
- [LSM Trees](@/notes/database/lsm_trees.md) — Storage engine design shaped by filesystem I/O characteristics and block allocation
- [Linux Expert Syscalls](@/notes/os/linux_expert_syscalls.md) — O_DIRECT, fallocate, reflink, and other storage syscalls that interact with filesystem internals

