+++
title = "Agent Native Version Control"
date = 2026-05-20
[taxonomies]
tags = ["vcs", "git", "crdt", "patch-theory", "ai-agents", "version-control"]
+++


# Agent-Native Version Control: Beyond Git for AI-Driven Development

## Changelog

| Date       | Section added / updated |
|------------|------------------------|
| 2026-04-04 | Initial comprehensive deep dive: Git limitations for agent workflows, theoretical foundations (patch theory, OT, CRDTs, AST merge theory), alternative VCS systems (Pijul, Jujutsu, Sapling, Piper, Mega), CRDT collaboration systems (Automerge, Loro, Yjs), semantic merge tools (Difftastic, Mergiraf, Spork), database-backed version control (Prolly trees, event sourcing), agent-native VCS architecture synthesis, open problems |

---

## 1. Introduction & Motivation

Git was designed in 2005 by Linus Torvalds for a specific workflow: human developers working asynchronously on the Linux kernel, submitting patches via email, with a benevolent dictator merging them. Two decades later, the development landscape has fundamentally shifted. AI coding agents now generate 41% of new code in merged PRs (GitHub, 2025), and agentic coding frameworks deploy planning agents, coding agents, and reviewing agents that operate at machine speed.

The mismatch is structural:

```
Git's assumptions:                    Agent reality:
─────────────────                     ──────────────
Human typing speed (~50 WPM)          Machine speed (thousands of changes/hour)
One developer per working copy        100+ agents on same codebase simultaneously
Asynchronous review cycles (hours)    Continuous validation (milliseconds)
Branch-per-feature (tens of branches) Branch explosion (thousands of micro-branches)
Line-based diffs (textual)            Function-level changes (semantic)
Manual conflict resolution            Must auto-resolve or escalate programmatically
Commit messages for context           Need: agent ID, prompt, reasoning trace, validation
```

This document surveys the theoretical foundations, existing systems, and emerging architectures that could replace or augment git for agent-native development workflows.

---

## 2. Git's Fundamental Limitations for Agent Workflows

### 2.1 Snapshot Model vs Change Model

Git stores snapshots (trees of blobs), not changes. A commit is a pointer to a tree object, with parent pointers forming a DAG. Diffs are computed on demand by comparing trees. This design choice has consequences:

- **No patch commutation**: git cannot reorder commits without replaying diffs (rebase). Two independent changes to different functions in the same file create spurious conflicts because git sees overlapping line ranges, not independent semantic units.
- **Cherry-pick pollution**: cherry-picking from branch A to branch B creates a *new* commit with different SHA. Future merges between A and B may conflict on the already-cherry-picked content. Patch-based systems (Darcs, Pijul) handle this correctly by identity.
- **No structural sharing of changes**: if agent A and agent B both make the same rename refactoring independently, git sees two different diffs that conflict. A change-aware system would recognize them as identical operations.

### 2.2 Line-Based 3-Way Merge

Git's merge algorithm (recursive/ort) operates on text lines. It has no understanding of programming language syntax:

```
Base:       Agent A:          Agent B:
─────       ────────          ────────
fn foo() {  fn foo() {        fn foo() {
  x = 1;     x = 1;            x = 1;
  y = 2;     y = 3;  // ← A    z = 9;  // ← B adds line
  z = 3;     z = 3;            y = 2;
}           }                   z = 3;
                              }

Git: CONFLICT (both modified near line 3)
Reality: These changes are independent — A modifies y, B adds z before y
```

An AST-aware merge would see that A changed the assignment to `y` and B inserted a new statement — no conflict.

### 2.3 Working Copy Assumption

Git requires a working directory: checked-out files on a filesystem. Every `git checkout`, `git merge`, `git rebase` materializes files to disk. For agents:

- An agent modifying a single function doesn't need the other 10,000 files on disk
- Multiple agents cannot share a single working directory without coordination
- Each agent clone consumes disk I/O and storage proportional to the repo size (mitigated by sparse checkout, but still fundamentally limited)

Google's CitC and Meta's EdenFS solved this with virtual filesystems that materialize files on demand. The average Google developer workspace contains fewer than 10 files despite the monorepo containing 1 billion+.

### 2.4 Concurrency Model

Git uses filesystem-level locking for critical operations:

```
.git/index.lock          — prevents concurrent staging
.git/refs/heads/main.lock — prevents concurrent ref updates
.git/shallow.lock        — prevents concurrent fetch
```

Two agents running `git commit` simultaneously on the same repo will have one fail with `fatal: Unable to create '.git/index.lock': File exists`. Workarounds (worktrees, separate clones) add overhead and don't solve the fundamental serialization problem.

At Google's scale, Piper handles **800,000 requests/second** across 10 data centers using Paxos consensus on Spanner storage — a fundamentally different architecture than filesystem locks.

### 2.5 Branch/PR Model Mismatch

The branch → PR → review → merge cycle assumes:
- A human creates a branch (seconds)
- Works on it over hours/days
- Opens a PR for human review (hours)
- CI runs (minutes to hours)
- A human merges

With agents generating thousands of changes per hour, this model creates:
- **Branch explosion**: thousands of micro-branches polluting the ref namespace
- **Review bottleneck**: humans can't review at machine speed (the "hourglass effect" — AI collapses writing but review becomes the bottleneck)
- **CI overload**: each micro-change triggers full CI pipelines

### 2.6 No Native Provenance

Git tracks `Author`, `Committer`, `Date`, and a free-text `message`. For agent workflows, we need:
- Which agent (model, version, configuration)
- What prompt/instruction triggered the change
- What reasoning led to the specific implementation
- What validation was performed (tests passed, type-checked, etc.)
- Confidence level of the change
- Dependencies on other agents' changes

Git trailers (`Signed-off-by`, `Co-Authored-By`) are an informal workaround, not a structured provenance system.

---

## 3. Theoretical Foundations

### 3.1 Patch Theory

#### Darcs (2003)

David Roundy's Darcs introduced the idea that version control should be based on **patches** (changes) rather than **snapshots** (states). The key operation is **commutation**: given sequential patches A then B, can we find equivalent patches B' then A' that produce the same result?

```
        A         B
  O ────────→ X ────────→ Y
  │                        │
  │ B'                     │ A'    (if A and B commute)
  ↓                        ↓
  Z ────────────────────→ Y
              A'
```

Formally: if `AB = B'A'`, patches A and B commute. A **conflict** occurs when commutation is undefined.

Darcs's critical flaw: the merge algorithm in Darcs 1.x was **exponential** in the number of conflicting patches. Certain real-world merge patterns caused Darcs to hang for hours. Darcs 2.0 (2008) introduced new "darcs-2" patch semantics to mitigate this, but the theoretical problem motivated Pijul's redesign.

**Key paper**: Roundy, "Darcs: Distributed Version Management in Haskell," 2005; Dagit, "Darcs Patch Theory," Tufts CS.

#### Pijul (2016)

Pijul, by Samuel Mimram and Cinzia di Giusto, places version control on rigorous category-theoretic foundations. The core insight: **merges are pushouts** in a category where files are objects and patches are arrows.

```
         p
    O ────────→ A       Alice's changes
    │           │
  q │           │ s     If Alice (p) and Bob (q) edit concurrently,
    ↓           ↓       the pushout P is the unique merge where
    B ────────→ P       pr = qs (the diagram commutes)
         r
```

The pushout P has a universal property: anything reachable from both A and B is reachable from P. This gives merge a precise mathematical meaning, unlike git's heuristic 3-way text merge.

**Graggles**: When patches conflict (no clean file merge exists), Pijul represents the result as a **graggle** — a directed graph of lines (generalizing a file's linear order). A file is a special case of a graggle where the graph is a total order. Graggles enable **perfect merges**: every pair of patches has a unique perfect merge in graggle-space. Flattening a graggle back to a file is just another patch.

**Data structure**: The repository state is a **graph of byte chunks** where edges represent sequential ordering. This graph is a cache of applied patches — unlike Darcs, which must repeatedly verify patch compatibility.

**Complexity**: Pijul targets O(log h) for all operations, where h is history size — optimal since filesystem operations are also O(log h).

**Properties critical for agents**:
- Patches **commute**: concurrent agents' changes can be applied in any order with identical results (when non-conflicting)
- Merges are **associative**: `merge(merge(A,B), C) = merge(A, merge(B,C))` — git does NOT guarantee this
- Cherry-pick is **idempotent**: picking the same patch twice is a no-op, not a conflict

**Key paper**: Mimram & di Giusto, "A Categorical Theory of Patches," 2013, *Electronic Notes in Theoretical Computer Science*.

### 3.2 Operational Transformation (OT)

OT was introduced by Ellis and Gibbs (1989) for real-time collaborative editing. The core idea: when concurrent operations arrive, **transform** each operation against the others so they can be applied in any order and converge.

```
Client A: insert("x", pos=3)    Client B: delete(pos=1)
Server receives A first, then B:
  B' = transform(B, A) = delete(pos=1)  // pos unchanged since insert was after
Server receives B first, then A:
  A' = transform(A, B) = insert("x", pos=2)  // pos shifted left since delete was before
```

**Google Docs** uses OT with a central server as the canonical ordering authority. The server serializes all operations, resolving ordering ambiguity.

**Limitations for agent workflows**:
- **Central server required**: single point of coordination and potential bottleneck
- **Transformation explosion**: O(n²) worst case for n concurrent operations
- **Correctness is notoriously hard**: Sun et al. proved that many published OT algorithms have correctness bugs (the "TP2 puzzle"). Google invested years getting OT right for Docs.
- **Not composable**: OT doesn't naturally extend to structured data (trees, ASTs) without significant additional complexity

**Key papers**: Ellis & Gibbs, "Concurrency Control in Groupware Systems," SIGMOD 1989; Sun et al., "Operational Transformation in Real-Time Group Editors: Issues, Algorithms, and Achievements," CSCW 1998.

### 3.3 CRDTs (Conflict-free Replicated Data Types)

CRDTs, formalized by Shapiro et al. (2011), are data structures that can be replicated across nodes and updated independently, with all replicas guaranteed to converge to the same state — **without coordination**.

Two flavors:
- **State-based (CvRDT)**: merge by computing join (least upper bound) of states
- **Operation-based (CmRDT)**: broadcast operations, which must be commutative

#### Text CRDTs for Code Editing

**RGA (Replicated Growable Array)** — Roh et al. 2011: each character gets a unique, immutable ID (timestamp + node ID). Insertions reference the ID of the character they follow. Deletions mark characters as tombstones. Concurrent inserts at the same position are ordered by timestamp.

**Fugue** — used by Loro: designed to minimize **interleaving anomalies** where concurrent inserts at the same position produce garbled text. Fugue provides a tree-based ordering that keeps concurrent inserts from interleaving.

**Automerge** — Kleppmann et al.: JSON-level CRDT supporting maps, lists, text, and counters. Preserves full version history. The Rust-based Automerge 2.0 dramatically improved performance over the original JS implementation.

**Diamond Types** — Seph Gentle: cutting-edge text CRDT focused on raw performance and minimal memory footprint. Achieves near-native-string-editing speed by using a novel run-length-encoded representation of CRDT metadata.

**Tradeoffs for agent workflows**:

| Property | OT | CRDT |
|---|---|---|
| Central server | Required | Not required |
| Offline support | No | Yes |
| Metadata overhead | Low | High (tombstones, IDs) |
| Correctness proofs | Very hard (TP2) | Automatic (by construction) |
| Undo/redo | Complex | Complex (tombstone chains) |
| Suitable for agents | Limited (bottleneck) | Strong (decentralized) |

CRDTs are the stronger foundation for agent workflows because agents are inherently distributed — they don't share a single server connection like human collaborators in Google Docs.

**Key papers**: Shapiro et al., "Conflict-free Replicated Data Types," SSS 2011; Roh et al., "Replicated Abstract Data Types," JPDC 2011; Kleppmann & Beresford, "A Conflict-Free Replicated JSON Datatype," IEEE TPDS 2017; Gentle, "Diamond Types," 2023.

### 3.4 AST-Level Merge Theory

Line-based merging is fundamentally limited because lines are not meaningful units of code. The alternative: merge at the Abstract Syntax Tree (AST) level, where the units are language constructs (functions, statements, expressions).

#### 3DM Algorithm

Lindholm's 3DM (Three-Dimensional Merge) operates on ordered, labeled trees:

1. **Match** nodes between base, left, and right versions using tree matching
2. **Detect** changes: insertions, deletions, moves, updates
3. **Merge** by applying non-conflicting changes from both sides
4. **Report** conflicts only when both sides modify the same node

Used by Spork (Java) and adapted by Mergiraf (multi-language).

#### GumTree

Falleri et al. (2014) introduced GumTree, a practical AST differencing algorithm:

1. **Top-down phase**: match identical subtrees using hash-based comparison (fast)
2. **Bottom-up phase**: match remaining nodes by label/value similarity
3. **Output**: edit script of insert, delete, update, and **move** operations

Move detection is critical — git's line-based diff cannot detect that a function was moved from one file to another. GumTree can.

#### Semantic vs Syntactic Conflicts

AST-level merging distinguishes:
- **Syntactic conflict**: both sides modify the same AST node → always a conflict
- **Semantic conflict**: both sides modify different nodes but the combined effect is semantically invalid (e.g., agent A renames function `foo` → `bar`, agent B adds a call to `foo`) → requires program analysis beyond tree structure
- **False conflict** (in git): both sides modify nearby lines but different AST nodes → NOT a conflict at AST level

Research from Schesch et al. (2024) evaluated merge tools: Mergiraf achieves **42% fewer false negatives** than Spork, and both Rust-based generic tools (Mergiraf, LASTMERGE) match the performance of language-specific Java implementations.

**Key papers**: Lindholm, "A Three-way Merge for XML," DocEng 2004; Falleri et al., "Fine-grained and Accurate Source Code Differencing," ASE 2014; Schesch et al., "Evaluation of Version Control Merge Tools," ASE 2024; Cavalcanti et al., "LASTMERGE: A Language-Agnostic Structured Tool for Code Integration," 2025.

---

## 4. Alternative VCS Systems

### 4.1 Pijul

| Property | Value |
|---|---|
| Language | Rust |
| Model | Patch-based (category-theoretic pushouts) |
| Data structure | Graph of byte chunks (graggle cache) |
| Merge | Automatic via pushouts; conflicts produce graggles |
| Complexity | O(log h) per operation |
| Status | Usable but small ecosystem |

Pijul stores patches, not snapshots. A **branch is exactly a set of patches**. This means:

```
# In git, cherry-picking then merging creates conflicts:
git cherry-pick abc123    # creates new commit def456
git merge feature         # conflict on abc123's changes (already applied as def456)

# In Pijul, patches have identity:
pijul pull --patch XYZ    # patch XYZ is now in this channel
pijul pull origin          # XYZ already present, skip — no conflict
```

**Agent relevance**: if 50 agents each cherry-pick useful patches from a shared pool, Pijul's idempotent patch identity prevents the combinatorial explosion of conflicts that git would produce. The commutative merge means agents don't need to coordinate ordering of their changes.

**Limitation**: small community, limited tooling, no IDE integration, unclear scaling characteristics beyond small-to-medium repos.

### 4.2 Jujutsu (jj)

| Property | Value |
|---|---|
| Language | Rust |
| Creator | Martin von Zweigbergk (Google) |
| Model | Snapshot-based (like git) but with operation log |
| Backend | Git-compatible (uses git object storage) |
| Key innovation | First-class conflicts, operation log, working-copy-as-commit |
| Status | Active development, growing adoption, Google internal use |

#### First-Class Conflicts

In git, a conflict is a textual artifact (`<<<<<<<`, `=======`, `>>>>>>>`). In jj, conflicts are **objects in the data model**:

```
# Git: conflict markers in working directory, must resolve before committing
# jj: conflict is stored in the commit itself, can be committed and resolved later

$ jj log
@ qpvuntsm  (conflict) Merge feature-a and feature-b
│ ├─ Added authentication module
│ └─ Refactored user module (conflicts with auth changes)
```

Conflicts propagate through rebases — if commit C conflicts, and you rebase D on top of C, D inherits the conflict. When you eventually resolve C's conflict, the resolution automatically propagates to D. This is profoundly better for agents: an agent can commit conflicted state, and a separate conflict-resolution agent can resolve it later without blocking the original agent.

#### Operation Log

Every mutation to the repo (commit, rebase, merge, amend) is recorded in an append-only operation log:

```
$ jj op log
@ 2f4e3d Operation { id: "abc123", description: "commit", time: "..." }
│ Parent: def456
│ Tags: agent_id=coding-agent-7, prompt_hash=a1b2c3
○ def456 Operation { id: "def456", description: "rebase", time: "..." }
```

This enables:
- **Lock-free concurrency**: multiple agents can commit simultaneously; conflicting operations are detected and merged at the operation level, not the file level
- **Time travel**: `jj op restore <id>` reverts the entire repo to any past state
- **Audit trail**: every agent's every action is recorded

#### Working-Copy-as-Commit

In jj, the working copy IS a commit (the `@` commit). There's no staging area, no "dirty" working directory. Every `jj status` shows the diff between the working-copy commit and its parent. This simplifies agent workflows — an agent just makes changes and they're automatically part of a commit.

**Agent relevance**: jj is the closest existing system to an agent-native VCS. Its operation log provides the concurrency model, first-class conflicts provide programmatic conflict handling, and the git-compatible backend means existing tooling (CI, code review) continues to work.

### 4.3 Sapling (Meta)

| Property | Value |
|---|---|
| Language | Rust + Python |
| Creator | Meta (formerly Facebook) |
| Components | Sapling client, Mononoke server, EdenFS virtual FS |
| Model | Snapshot-based (Mercurial-derived) |
| Scale | 10s of millions of files/commits/branches |
| Key innovation | Virtual filesystem, stacked diffs, server-side scaling |

#### EdenFS: Virtual Filesystem

EdenFS is a FUSE filesystem that presents the entire repo but **only materializes files on access**:

```
# Traditional git clone:
$ git clone mega-repo     # downloads all files → 50GB, 10 minutes
$ ls src/auth/login.rs    # file already on disk

# Sapling + EdenFS:
$ sl clone mega-repo      # downloads metadata only → seconds
$ ls src/auth/login.rs    # EdenFS fetches this file on demand from Mononoke
                          # Only files actually read are fetched
```

For agents, this means:
- Clone is near-instant (metadata only)
- Agent touching 5 files in a 10M-file repo only fetches those 5 files
- Multiple agents can share the same EdenFS mount with different views

#### Stacked Diffs

Sapling uses **stacked diffs** instead of branches: a chain of dependent changes that can be reviewed and landed independently. This maps better to agent workflows where changes are incremental refinements.

**Limitation**: EdenFS is not yet supported outside Meta's infrastructure. Mononoke requires significant server-side infrastructure.

### 4.4 Google Piper / CitC

Google's internal system represents the extreme end of centralized version control at scale:

```
Scale:
├── 1 billion+ files in single repository
├── 35 million+ commits
├── 25,000+ developers
├── 800,000 requests/second peak
├── Distributed across 10 data centers
└── Built on Spanner (Paxos consensus)

CitC (Clients in the Cloud):
├── FUSE filesystem presenting full repo
├── Average workspace: <10 files (out of 1 billion)
├── Only modified files stored in workspace
├── No explicit "clone" or "checkout"
└── Trunk-based development (no long-lived branches)
```

**Trunk-based development** at Google means everyone works on HEAD. Changes are submitted directly to trunk after code review and automated testing. No feature branches, no merge hell.

**Agent relevance**: Piper/CitC is the gold standard for what an agent-native VCS should achieve:
- Virtual workspaces with near-zero overhead (agents get <10-file workspaces)
- Centralized truth source (no distributed consistency headaches)
- Trunk-based development (no branch proliferation)
- Scale to billions of operations

**Limitation**: proprietary, tightly coupled to Google infrastructure (Spanner, Borg, etc.).

### 4.5 Mega (Open-Source Piper)

Mega is an open-source Rust implementation of the Google Piper model, explicitly targeting the "AI agent era":

```
Architecture:
├── Mega Server: monorepo engine with Git protocol compatibility
├── Libra Client: Rust-based client with SQLite-backed local storage
├── Buck2 Integration: hermetic, reproducible builds
├── Git Compatibility: clone/pull/push any subfolder as standard git repo
└── Agent Attribution: agents are tracked, attributable contributors
```

Key design choices:
- **Monorepo-first**: single logical repository with subfolder-level access
- **Git protocol**: agents and humans use standard git commands
- **AI-native features**: full codebase context for dependency analysis, impact assessment, cross-project reasoning
- **Fine-grained access control**: agents can be scoped to specific directories

**Status**: early-stage, active development (2024-2026). The most explicit attempt to build a git-compatible VCS for the agent era.

---

## 5. CRDT and Real-Time Collaboration Systems

While VCS systems handle asynchronous, coarse-grained collaboration (commit-level), CRDT-based systems enable synchronous, fine-grained collaboration (character-level). For agents, the boundary blurs — agents operate fast enough that their collaboration resembles real-time editing more than traditional VCS workflows.

### 5.1 Automerge

```
Architecture:
├── Core: Rust implementation of JSON CRDT
├── Bindings: JavaScript (WASM), Python, Ruby, Elixir, Go
├── Sync: automerge-repo (peer-to-peer, WebSocket, MessageChannel)
├── Data Model: JSON-compatible (maps, lists, text, counters)
└── History: Full operation log preserved, time travel supported
```

Automerge models documents as a tree of CRDT types. Each operation (set field, insert into list, edit text) is recorded with a Lamport timestamp and actor ID. Replicas sync by exchanging operations and applying them in causal order.

**Strengths for agents**: decentralized sync, full history, no central server needed.

**Weakness**: **tombstone and metadata accumulation**. Deleted characters remain as tombstones forever. Long-running documents accumulate unbounded metadata. Automerge limits undo history to 100 operations to prevent unbounded growth. For a codebase with millions of edits, this is a serious scalability concern.

### 5.2 Loro

```
Architecture:
├── Core: Rust (high-performance)
├── Bindings: JavaScript (WASM), Swift
├── Data Types: List, Map (LWW), Tree, Text (Fugue)
├── History: Git-like (time travel, fork, undo/redo)
├── Encoding: Compact binary format, columnar storage
└── Performance: Optimized for memory, CPU, and loading speed
```

Loro is the most promising CRDT library for agent-native version control because of its **Tree CRDT**. Code projects are hierarchical (directories → files → functions → statements), and Loro's tree CRDT can represent this structure natively, using the algorithm from Martin et al., "A Highly-Available Move Operation for Replicated Trees" (2020).

```
// Loro Tree CRDT can represent code project structure:
project/
├── src/
│   ├── main.rs      ← Text CRDT for file contents
│   ├── lib.rs       ← Text CRDT
│   └── auth/
│       ├── mod.rs   ← Text CRDT
│       └── jwt.rs   ← Text CRDT
└── Cargo.toml       ← Text CRDT

// Agent A moves auth/ to a new crate — tree move operation
// Agent B edits auth/jwt.rs — text edit operation
// Loro: both operations merge correctly (move + edit commute)
// Git: move creates delete+add, edit conflicts with delete
```

**Version history**: Loro preserves full version history with `oplog_vv` (version vector) and `state_frontiers`. Documents can be forked (branching), and changes can be time-traveled. This provides git-like semantics at the CRDT level.

### 5.3 Yjs

The most widely deployed CRDT framework. Powers VS Code Live Share, Jupyter collaborative editing, and numerous web editors. Uses a list CRDT (YATA) for text, with efficient binary encoding that keeps memory usage low.

**Relevance**: proves that CRDT-based collaboration scales to millions of users in production. However, Yjs is focused on real-time editing, not version control — it doesn't natively provide branching, merging, or history querying at the VCS level.

### 5.4 OT vs CRDT Comparison

```
                      OT                          CRDT
                      ──                          ────
Server requirement    Central server required     No server needed
Offline support       No (needs server)           Yes (merge on reconnect)
Consistency           Server-ordered              Eventual (convergent)
Metadata overhead     Low (ops are ephemeral)     High (tombstones, IDs)
Undo/redo             Server-managed              Complex (effect ordering)
Correctness proofs    Very hard (TP2 puzzle)      By construction
Mature systems        Google Docs, SharePoint     Figma, VS Code Live Share
Agent suitability     Bottleneck at server        Natural fit (decentralized)
Scale limit           Server throughput           Metadata growth
```

For agent systems, CRDTs win because:
1. No central bottleneck — agents are distributed by nature
2. Offline-first — agents can batch changes and sync later
3. Provable convergence — no subtle correctness bugs from bad transformation functions
4. Natural for event sourcing — each CRDT operation is an event

---

## 6. Semantic Merge and AST-Level Tools

### 6.1 Difftastic

Tree-sitter based structural diff tool supporting 30+ languages:

```bash
$ difft old.rs new.rs
# Output shows:
# - Moved function highlighted as MOVE (not delete + add)
# - Reformatted code shown as unchanged (ignores whitespace/formatting)
# - Only semantic changes highlighted
```

Difftastic understands that `{x: 1, y: 2}` and `{y: 2, x: 1}` may be equivalent depending on language semantics. It shows **moves** as first-class operations, unlike git's line-based diff which shows delete at old location + insert at new location.

**Limitation**: diff only, no merge. Cannot be used as a git merge driver.

### 6.2 Mergiraf

Production-ready AST-based merge tool, usable as a **git merge driver**:

```
Architecture:
├── Parser: tree-sitter (language-agnostic)
├── Matching: GumTree classic algorithm (top-down + bottom-up)
├── Merge: 3DM-based with enhancements
├── Languages: Any language with tree-sitter grammar
├── Integration: Drop-in git merge driver
└── Implementation: Rust
```

Setup as git merge driver:

```gitconfig
[merge "mergiraf"]
    name = mergiraf
    driver = mergiraf merge --git %O %A %B -s %S -p %P
```

Mergiraf processes the three versions (base, ours, theirs), constructs ASTs via tree-sitter, computes matchings using GumTree, and performs structured three-way merge. Falls back to line-based merge for unparseable files.

**Results** (Schesch et al., ASE 2024): 42% fewer false negatives than Spork across a benchmark of real-world merge scenarios from open-source Java projects.

### 6.3 Spork

AST-based structured merge for Java (Larsen et al., IEEE TSE 2022):

```
Pipeline:
1. Parse base/left/right → Java ASTs (via Spoon)
2. Match nodes between trees (GumTree)
3. 3DM merge algorithm on matched trees
4. Print merged AST preserving original formatting
```

Spork's formatting preservation is notable — it maintains the original code style of both contributors, not the AST's canonical pretty-printing. This matters for human readability of agent-generated merges.

### 6.4 LASTMERGE

Language-agnostic structured merge (Cavalcanti et al., 2025). Rust implementation using tree-sitter, achieving comparable performance to language-specific tools like Spork and jDime. Demonstrates that tree-sitter grammars are sufficient for high-quality structured merging without language-specific logic.

**Agent relevance of AST merge tools**: an agent-native VCS would integrate AST-level merging as the default merge strategy, not an optional extension. When agents modify code at the function level, merges should operate at the function level too.

---

## 7. Database-Backed Version Control

### 7.1 Prolly Trees (Noms → Dolt)

Traditional git stores objects in pack files — a custom binary format optimized for git's specific access patterns. An alternative: use a proper database.

**Prolly trees** (probabilistic B-trees) combine properties of B-trees and Merkle trees:

```
                    ┌──────────────────┐
                    │   Root (hash=A)  │
                    └────────┬─────────┘
                   ┌─────────┴──────────┐
              ┌────┴────┐          ┌────┴────┐
              │ Internal│          │ Internal│
              │(hash=B) │          │(hash=C) │
              └────┬────┘          └────┬────┘
            ┌──────┴──────┐      ┌─────┴──────┐
         ┌──┴──┐      ┌──┴──┐ ┌──┴──┐     ┌──┴──┐
         │Leaf │      │Leaf │ │Leaf │     │Leaf │
         │k1:v1│      │k2:v2│ │k3:v3│     │k4:v4│
         └─────┘      └─────┘ └─────┘     └─────┘

Properties:
├── B-tree: O(log n) seek/insert/delete (fast queries)
├── Merkle: content-addressed (hash identifies content)
├── Probabilistic: chunk boundaries determined by content hash rolling
│   → Two trees with similar data share most chunks (structural sharing)
│   → Diff between versions is O(changes), not O(data)
└── Queryable: supports range scans, prefix queries, etc.
```

**Noms** (Attic Labs, 2016): pioneered prolly trees for version-controlled data storage. Open-source, content-addressed, with git-style branching and merging.

**Dolt** (DoltHub): built a full MySQL-compatible SQL database on top of prolly trees. Provides `DOLT_DIFF()`, `DOLT_LOG()`, `DOLT_BLAME()` as SQL functions.

```sql
-- Query code history as SQL (hypothetical agent-native VCS):
SELECT agent_id, change_type, function_name, timestamp
FROM code_history
WHERE file_path = 'src/auth/login.rs'
  AND timestamp > '2026-04-01'
ORDER BY timestamp DESC;

-- Find all functions modified by a specific agent:
SELECT DISTINCT function_name, file_path
FROM code_changes
WHERE agent_id = 'coding-agent-7'
  AND confidence < 0.8;  -- low-confidence changes for review
```

**Agent relevance**: a database-backed VCS enables rich queries over code history that git cannot support without external tooling. "Show me all low-confidence changes by agent-7 to authentication code in the last week" is a SQL query, not a complex pipeline of `git log | grep | awk`.

### 7.2 Event Sourcing Model for Code

Event sourcing (Fowler, 2005) stores all changes as an append-only log of events. The current state is a materialized view derived by replaying events.

Applied to code:

```
Event Log (append-only):
─────────────────────────
Event 1: CreateFile("src/main.rs", agent="init-agent")
Event 2: InsertFunction("src/main.rs", "fn main()", agent="scaffold-agent")
Event 3: AddParameter("src/main.rs", "main", "args: Vec<String>", agent="cli-agent")
Event 4: InsertStatement("src/main.rs", "main", "let config = parse(args);", agent="cli-agent")
Event 5: CreateFile("src/config.rs", agent="cli-agent")
Event 6: RenameFunction("src/main.rs", "main" → "run", agent="refactor-agent")
...

Materialized View (current code):
──────────────────────────────────
// src/main.rs
fn run(args: Vec<String>) {
    let config = parse(args);
    ...
}
```

Properties:
- **Complete audit trail**: every agent action is recorded as a typed event
- **Replay**: reconstruct any historical state by replaying events up to a timestamp
- **Branching**: fork the event stream; merge by interleaving compatible events
- **Undo**: reverse a specific agent's events without affecting others (unlike `git revert` which creates anti-commits)
- **Streaming**: agents subscribe to event streams for real-time awareness of other agents' changes

This model naturally produces the provenance data that git lacks — each event carries agent ID, intent, and context.

---

## 8. Designing an Agent-Native VCS: Architecture Synthesis

### 8.1 Requirements Matrix

```
┌─────────────────────┬──────────────────────┬──────────────────────────────┐
│ Requirement         │ Git                  │ Agent-Native Target          │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Concurrency         │ Lock on refs,        │ Lock-free (CRDT / op-log)   │
│                     │ index.lock           │                              │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Change granularity  │ File / line          │ AST node / function          │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Merge model         │ 3-way text merge     │ Semantic (AST + intent)      │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Working copy        │ Full checkout on disk │ Virtual / on-demand          │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Change model        │ Snapshots (trees)    │ Patches / operations         │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Provenance          │ Free-text message    │ Agent ID, prompt, reasoning, │
│                     │                      │ validation, confidence       │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Conflict resolution │ Manual (human)       │ Auto-resolve + escalation    │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ History query       │ git log (linear scan)│ SQL / graph queries          │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Commit speed        │ ~10ms (human-scale)  │ <1ms (machine-speed)         │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ API model           │ CLI-first            │ Programmatic SDK-first       │
├─────────────────────┼──────────────────────┼──────────────────────────────┤
│ Collaboration unit  │ Branch → PR → merge  │ Session (intent + changes +  │
│                     │                      │ reasoning + validation)      │
└─────────────────────┴──────────────────────┴──────────────────────────────┘
```

### 8.2 Proposed Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Agent SDK / API                          │
│         (Rust core, bindings: Python, TypeScript, Go)           │
├──────────┬──────────┬───────────┬──────────┬───────────────────┤
│ Session  │ Conflict │ Provenance│ Query    │ Notification      │
│ Manager  │ Resolver │ Tracker   │ Engine   │ Stream            │
├──────────┴──────────┴───────────┴──────────┴───────────────────┤
│                     Merge Engine                                │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────┐    │
│  │ Text     │  │ AST-Level    │  │ Semantic Conflict      │    │
│  │ (CRDT)   │  │ (tree-sitter │  │ Detection (type-check, │    │
│  │          │  │  + GumTree)  │  │  test, LSP)            │    │
│  └──────────┘  └──────────────┘  └───────────────────────┘    │
├────────────────────────────────────────────────────────────────┤
│                   Concurrency Layer                             │
│  ┌──────────────────┐  ┌──────────────────────────────┐       │
│  │ Operation Log    │  │ CRDT Sync                     │       │
│  │ (jj-style,      │  │ (Loro-based, peer-to-peer     │       │
│  │  append-only)   │  │  or server-mediated)           │       │
│  └──────────────────┘  └──────────────────────────────┘       │
├────────────────────────────────────────────────────────────────┤
│                    Storage Engine                               │
│  ┌──────────────────┐  ┌──────────────────────────────┐       │
│  │ Prolly Trees     │  │ Virtual Filesystem             │       │
│  │ (content-        │  │ (FUSE / in-memory views,       │       │
│  │  addressed,      │  │  CitC/EdenFS-inspired)         │       │
│  │  SQL-queryable)  │  │                                │       │
│  └──────────────────┘  └──────────────────────────────┘       │
├────────────────────────────────────────────────────────────────┤
│                   Git Compatibility Bridge                      │
│            (import/export, git protocol, CI/CD)                │
└────────────────────────────────────────────────────────────────┘
```

#### Layer 1: Storage Engine — Prolly Trees + Virtual FS

The storage layer uses **prolly trees** for content-addressable, diffable storage:

- Each file version is a leaf in the prolly tree
- File contents stored as byte chunks with content-defined chunking (CDC)
- Structural sharing: two versions differing by one function share all other chunks
- SQL-queryable: "SELECT * FROM changes WHERE agent='X' AND file LIKE 'src/auth/%'"
- Virtual filesystem layer presents on-demand file views to agents (like CitC)

#### Layer 2: Concurrency — Operation Log + CRDT Sync

Borrowing from jj's operation log and Loro's CRDT sync:

- Every mutation is an operation in an append-only log (like jj)
- Operations carry CRDT metadata for automatic merge (like Loro)
- Lock-free: multiple agents commit simultaneously; conflicts detected at sync time
- Causal ordering via vector clocks / Lamport timestamps

#### Layer 3: Merge Engine — Multi-Strategy

```
Change arrives → Parse both versions with tree-sitter
              → Compute AST diff (GumTree)
              → Classify changes:
                 ├── Independent (different AST subtrees) → auto-merge
                 ├── Syntactic conflict (same node modified) → try semantic resolution
                 │   ├── Both made identical change → deduplicate (idempotent)
                 │   ├── Changes are composable → compose
                 │   └── True conflict → escalate to conflict resolver agent
                 └── Semantic conflict (type error, test failure after merge)
                     → run type checker / tests
                     → escalate with diagnostic context
```

#### Layer 4: Agent SDK

```rust
// Pseudocode: agent-native VCS API
let repo = AgentRepo::connect("https://mega.example.com/monorepo");

// Create a session (not a branch)
let session = repo.create_session(SessionConfig {
    agent_id: "refactor-agent-v3",
    prompt_hash: "sha256:abc123...",
    intent: "Rename all instances of 'user_id' to 'account_id' in auth module",
    scope: PathGlob("src/auth/**"),  // agent only sees auth module
});

// Read file (virtual — fetched on demand)
let content = session.read("src/auth/login.rs")?;

// Parse and modify at AST level
let ast = session.parse_ast("src/auth/login.rs")?;
ast.rename_identifier("user_id", "account_id");

// Commit with provenance
session.commit(CommitConfig {
    confidence: 0.95,
    validation: vec![
        Validation::TypeCheck(passed),
        Validation::Tests(TestResult { passed: 47, failed: 0 }),
    ],
    reasoning: "Renamed user_id → account_id per naming convention RFC-42",
});

// Auto-merge with trunk (AST-level)
let merge_result = session.merge_to_trunk()?;
match merge_result {
    MergeResult::Clean => println!("Merged successfully"),
    MergeResult::AutoResolved(resolutions) => {
        println!("Auto-resolved {} conflicts", resolutions.len());
    }
    MergeResult::NeedsEscalation(conflicts) => {
        // Assign to conflict-resolution agent
        session.escalate(conflicts, "conflict-resolver-agent")?;
    }
}
```

### 8.3 Sessions Over Branches

Pedro Piñera's "sessions" concept (2026) replaces branches and PRs:

```
Branch/PR Model:                    Session Model:
─────────────────                   ──────────────
1. Create branch                    1. Open session
2. Make changes                     2. Make changes + record reasoning
3. Write commit messages            3. Intent auto-captured from prompt
4. Open PR (describe what/why)      4. Session contains everything:
5. CI runs                             ├── Code changes
6. Human reviews code                  ├── Agent's reasoning trace
7. Merge                               ├── Prompt that initiated work
                                       ├── Validation results
                                       ├── Confidence scores
                                       └── Dependencies on other sessions
                                    5. Auto-validate + auto-merge if clean
                                    6. Escalate to human only if needed
```

Sessions make **intent a first-class citizen**. A future agent analyzing the codebase can query: "Why was this function renamed?" and get the full reasoning trace, not just a commit message that says "rename function."

### 8.4 Prompt Requests Over Pull Requests

For open-source collaboration with agents, Piñera proposes **prompt requests**: instead of submitting code (PR), submit **intent** (prompt). The maintainer reviews the intent and runs it with their own agent, ensuring:

- Code is generated in the context of the full codebase
- No malicious code hidden in a large diff
- The intent is auditable and reproducible
- Maintainers validate *what should change*, not *how it changed*

---

## 9. Open Problems

### 9.1 AST-Level CRDTs for Arbitrary Languages

Tree-sitter provides parsers for 200+ languages, but each language has different semantic rules for what constitutes a conflict. Renaming a variable in Python (dynamic dispatch) has different implications than in Rust (static dispatch, borrow checker). A truly semantic merge needs language-specific conflict rules on top of generic AST merging.

### 9.2 Scaling CRDT Metadata

CRDTs accumulate metadata: tombstones for deletions, unique IDs for every character, vector clocks for causal ordering. For a codebase with millions of changes over years, this metadata can exceed the code itself. Garbage collection of CRDT metadata without breaking convergence guarantees is an active research area. Diamond Types shows promising approaches with run-length encoding of metadata.

### 9.3 Agent Intent Specification Language

How should an agent express "rename function foo to bar everywhere" in a way that:
- Is portable across languages?
- Can be verified (all references updated)?
- Can be replayed on a different version of the code?
- Composes with other intents?

This is essentially the problem of a **refactoring DSL** — an unsolved problem that becomes critical when agents produce thousands of intents per hour.

### 9.4 Semantic Correctness Validation

Auto-merging two AST-compatible changes doesn't guarantee the result is correct:

```
Agent A: adds null check before dereference
Agent B: adds logging that dereferences the pointer
Merged: logging happens before null check → crash
```

Detecting this requires at minimum type-checking the merged result, and ideally running tests. The validation loop (merge → type-check → test → fix → re-merge) needs to be fast enough for machine-speed iteration.

### 9.5 Git Ecosystem Bridge

The entire software development ecosystem assumes git: GitHub/GitLab, CI/CD (GitHub Actions, Jenkins), IDEs (VS Code, JetBrains), code review tools, deployment pipelines, package registries. Any agent-native VCS must either:

1. **Be git-compatible** (jj, Mega approach): use git as the backend/transport, add agent-native features on top
2. **Provide a git bridge**: bidirectional sync between the native format and git
3. **Replace the ecosystem**: build new CI, review, and deployment tools (highest effort, highest reward)

Option 1 (jj/Mega) is the pragmatic path. Option 3 is where the industry will eventually end up.

---

## 10. Key References

### Foundational Theory
1. Ellis & Gibbs, "Concurrency Control in Groupware Systems," SIGMOD 1989
2. Roundy, "Darcs: Distributed Version Management in Haskell," 2005
3. Shapiro et al., "Conflict-free Replicated Data Types," SSS 2011
4. Roh et al., "Replicated Abstract Data Types: Building Blocks for Collaborative Applications," JPDC 2011
5. Mimram & di Giusto, "A Categorical Theory of Patches," ENTCS 2013
6. Kleppmann & Beresford, "A Conflict-Free Replicated JSON Datatype," IEEE TPDS 2017
7. Sun et al., "Operational Transformation in Real-Time Group Editors," CSCW 1998
8. Martin et al., "A Highly-Available Move Operation for Replicated Trees," IEEE TPDS 2020

### AST Differencing and Merge
9. Falleri et al., "Fine-grained and Accurate Source Code Differencing," ASE 2014 (GumTree)
10. Lindholm, "A Three-way Merge for XML," DocEng 2004 (3DM algorithm)
11. Larsen et al., "Spork: Structured Merge for Java with Formatting Preservation," IEEE TSE 2022
12. Schesch et al., "Evaluation of Version Control Merge Tools," ASE 2024
13. Cavalcanti et al., "LASTMERGE: A Language-Agnostic Structured Tool for Code Integration," 2025
14. Apel et al., "Semistructured Merge: Rethinking Merge in Revision Control Systems," FSE 2011

### Systems
15. Potvin & Levenberg, "Why Google Stores Billions of Lines of Code in a Single Repository," CACM 2016 (Piper)
16. Dagit, "Darcs Patch Theory," Tufts CS (Darcs formal model)
17. Gentle, "Diamond Types: A High-Performance Text CRDT," 2023
18. Fowler, "Event Sourcing," martinfowler.com, 2005

### Tools and Implementations
19. **Pijul** — https://pijul.org/ — Patch-based VCS with category-theoretic foundations
20. **Jujutsu (jj)** — https://github.com/jj-vcs/jj — Git-compatible VCS with first-class conflicts and operation log
21. **Sapling** — https://sapling-scm.com/ — Meta's scalable VCS with EdenFS virtual filesystem
22. **Mega** — https://gitmega.dev/ — Open-source Piper implementation for the AI agent era
23. **Automerge** — https://automerge.org/ — JSON CRDT library (Rust + WASM)
24. **Loro** — https://loro.dev/ — High-performance CRDT with tree support (Rust)
25. **Yjs** — https://yjs.dev/ — Widely deployed CRDT for collaborative editing
26. **Difftastic** — https://github.com/Wilfred/difftastic — Structural diff using tree-sitter
27. **Mergiraf** — https://mergiraf.org/ — AST-based git merge driver (tree-sitter + GumTree)
28. **Dolt** — https://dolthub.com/ — SQL database with git-style version control (prolly trees)
29. **Tree-sitter** — https://tree-sitter.github.io/ — Universal parser generator for AST tooling

### Analysis and Commentary
30. Piñera, "Rethinking Version Control for an Agentic World," 2026
31. All Things Open, "What Version Control Looks Like When AI Agents Write the Code," 2026
32. DoltHub, "How to Chunk Your Database into a Merkle Tree," 2022 (prolly tree chunking)
33. Balintona, "Jujutsu (jj) VCS Workflows and the Convenience of its Operation Log," 2025
34. Anthropic, "2026 Agentic Coding Trends Report," 2026
