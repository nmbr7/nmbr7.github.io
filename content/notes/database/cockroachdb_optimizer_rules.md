+++
title = "Cockroachdb Optimizer Rules"
date = 2026-05-20
[taxonomies]
tags = ["cockroachdb", "query-optimizer", "optgen", "transformation-rules", "cost-based"]
+++


# CockroachDB Optimizer Rules: Deep Research Note

## Context

CockroachDB's SQL optimizer (`pkg/sql/opt`) uses a declarative DSL called **Optgen** to define
transformation rules on query expression trees. These rules are written in `.opt` files and compiled
into Go code by the `optgen` tool. The system separates logical simplification (normalization) from
physical plan enumeration (exploration), enabling a cost-based optimizer that is both maintainable
and extensible.

This document catalogs every `.opt` file, lists every rule, and explains why each rule exists. It also covers the memo data structure, physical properties system, cost model, statistics pipeline, join ordering algorithm, plan caching, and DistSQL execution integration.

---

## Architecture Overview

```
SQL AST
  │
  ▼
optbuilder  →  builds initial expression tree
  │
  ▼
norm.Factory  →  applies NORM rules during construction (single canonical form)
  │
  ▼
MEMO  →  stores equivalence classes of expressions
  │
  ▼
xform.Explorer  →  applies XFORM rules (generate alternative plans)
  │
  ▼
xform.Coster  →  assigns cost to each alternative
  │
  ▼
Best plan selected
```

### Two Rule Categories

| | Normalization (norm) | Exploration (xform) |
|---|---|---|
| **When** | During expression construction | During optimization pass |
| **Goal** | Canonical, simplified form | Enumerate cost-relevant alternatives |
| **Cost-aware** | No | Yes (generates candidates for costing) |
| **Output** | One result per expression | Many alternatives per group |
| **Tag** | `Normalize` | `Explore` |

### Rule Syntax

```
# Comment explaining WHY
[RuleName, Tag, OptionalPriorityTag]
(MatchPattern $var:TypeOrWildcard ...)
=>
(ReplacementExpression ...)
```

- `$x:*` — bind anything to `$x`
- `$x:(Type)` — bind only if node is `Type`
- `& (Func $x)` — additional boolean guard
- `^ (Func $x)` — negation guard
- `HighPriority` / `LowPriority` — control rule ordering within a pass

---

## Operator Definitions (`ops/`)

These files define the expression tree node types — not rules per se, but the vocabulary that
rules operate on.

### `ops/relational.opt` — ~71 operators
Defines relational (table-returning) operators: `Scan`, `Select`, `Project`, `InnerJoin`,
`LeftJoin`, `RightJoin`, `FullJoin`, `SemiJoin`, `AntiJoin`, `IndexJoin`, `LookupJoin`,
`MergeJoin`, `ZigzagJoin`, `GroupBy`, `ScalarGroupBy`, `DistinctOn`, `UpsertDistinctOn`,
`EnsureUpsertDistinctOn`, `EnsureDistinctOn`, `Union`, `Intersect`, `Except`, `UnionAll`,
`IntersectAll`, `ExceptAll`, `Limit`, `Offset`, `Max1Row`, `ProjectSet`, `WindowExpr`,
`WithScan`, `Barrier`, `RecursiveCTE`, `Values`, `FakeRel`, etc.

**Why:** Every SQL plan node type must be registered here so optgen can generate typed match/replace
code for it.

### `ops/scalar.opt` — ~173 operators
Defines scalar (value-returning) expression operators: all comparison ops (`Eq`, `Ne`, `Lt`, `Gt`,
`Le`, `Ge`), arithmetic (`Plus`, `Minus`, `Mult`, `Div`, `Mod`), logical (`And`, `Or`, `Not`),
string (`Like`, `ILike`, `SimilarTo`, `RegMatch`, `Concat`), casts, functions, aggregates
(`Sum`, `Count`, `Avg`, `Min`, `Max`, `StringAgg`, etc.), subquery ops (`Exists`, `Any`, `All`),
`Case`/`When`/`Else`, `Tuple`, `Array`, `Coalesce`, `IfErr`, `AggFilter`, `AggDistinct`, etc.

**Why:** Scalar expressions compose into filters, projections, and join conditions; they each need
defined operator types for pattern matching.

### `ops/mutation.opt` — ~16 operators
`Insert`, `Update`, `Delete`, `Upsert`, `CreateTable`, `CreateIndex`, `AlterTable`, etc.

**Why:** DML operations have special optimization considerations (FK checks, uniqueness checks,
returning clauses).

### `ops/statement.opt` — ~39 operators
DDL and control-flow: `Explain`, `ExplainOpt`, `ShowTrace`, `Call`, `AlterRange`, etc.

### `ops/enforcer.opt` — 2 operators
`Sort`, `Distribution` — physical enforcers that impose ordering/distribution properties.

**Why:** When a plan requires sorted output and the child doesn't provide it, the optimizer
injects a `Sort` enforcer. Same for distribution in multi-region plans.

### `ops/cycle.opt` — 2 operators
`RecursiveCTE`, `CycleDetector` — operators for WITH RECURSIVE support.

---

## Normalization Rules (`norm/rules/`)

Applied eagerly during expression construction. Each fires at most once per expression node;
together they reduce the expression to a unique canonical form.

---

### `norm/rules/bool.opt` — 19 rules

**Purpose:** Simplify boolean logic expressions algebraically.

| Rule | Why Needed |
|---|---|
| `NormalizeNestedAnds` | Flatten `A AND (B AND C)` to left-deep tree so other rules can traverse conjuncts uniformly |
| `SimplifyTrueAnd` | `TRUE AND x` → `x`. Eliminates vacuous conjunct |
| `SimplifyAndTrue` | `x AND TRUE` → `x`. Symmetric |
| `SimplifyFalseAnd` | `FALSE AND x` → `FALSE`. Short-circuit |
| `SimplifyAndFalse` | `x AND FALSE` → `FALSE`. Symmetric |
| `SimplifyTrueOr` | `TRUE OR x` → `TRUE`. Short-circuit |
| `SimplifyOrTrue` | `x OR TRUE` → `TRUE`. Symmetric |
| `SimplifyFalseOr` | `FALSE OR x` → `x`. Eliminates vacuous disjunct |
| `SimplifyOrFalse` | `x OR FALSE` → `x`. Symmetric |
| `SimplifyNotTrue` | `NOT TRUE` → `FALSE` |
| `SimplifyNotFalse` | `NOT FALSE` → `TRUE` |
| `NegateComparison` | `NOT (a = b)` → `a <> b`. Pushes NOT inward to enable further simplification |
| `EliminateNot` | `NOT NOT x` → `x`. Double negation elimination |
| `SimplifyAnd` | Removes duplicate conjuncts in AND (deduplication) |
| `SimplifyOr` | Removes duplicate disjuncts in OR |
| `ExtractRedundantConjunct` | `(A AND B) OR (A AND C)` → `A AND (B OR C)`. Factors out common conjuncts to reduce filter complexity |
| `SimplifyAnds` | Flattens list-form And with True/False handling |
| `FlattenAnd` | Converts nested And tree to flat conjunction list |
| `FlattenOr` | Converts nested Or tree to flat disjunction list |

---

### `norm/rules/scalar.opt` — 32 rules

**Purpose:** Simplify scalar expressions: comparisons, coalesce, subqueries, type casts.

| Rule | Why Needed |
|---|---|
| `CommuteVar` | Canonicalize `const op var` → `var op const` so index constraint code only needs to handle one form |
| `CommuteConst` | Same but for const groups |
| `EliminateCoalesce` | `COALESCE(x)` (single arg) → `x` |
| `SimplifyCoalesce` | Removes NULL first args, folds constant non-NULL first arg |
| `EliminateIfErr` | `IFERROR(non-error-expr)` → `non-error-expr` when inner can't error |
| `EliminateCast` | `CAST(x AS T)` when x already has type T → `x` |
| `EliminateExistsProject` | `EXISTS(Project(x))` → `EXISTS(x)`. Projections don't affect existence |
| `EliminateExistsGroupBy` | `EXISTS(ScalarGroupBy(x))` → scalar true if non-empty (scalar agg always returns a row) |
| `SimplifyEquality` | Constant folding for trivially equal/unequal expressions |
| `NormalizeNotAnyToAllNot` | `NOT (a = ANY(x))` → `a <> ALL(x)`. Enables index usage |
| `NormalizeAnyEqAll` | `a = ALL(x)` when x has single value → `a = x[0]` |
| `EliminateAnyArray` | `a = ANY(ARRAY[c])` with single const → scalar equality |
| `FoldInNull` | `x IN (NULL, ...)` handling: IN with all NULLs → NULL |
| `NormalizeInConst` | Sort IN list, remove duplicates; enables better constraint generation |
| `EliminateExistsLimit` | `EXISTS(Limit(x, 0))` → `FALSE` |
| `InlineWith` | Inlines CTE when used exactly once and safe to do so |
| `FoldNullCast` | `CAST(NULL AS T)` → typed NULL constant |
| `EliminateUnnecessaryCast` | Removes no-op casts between compatible types |
| `SimplifyCase` | Removes WHEN branches that are always false, short-circuits to ELSE |
| `SimplifyCaseWhenNull` | `CASE WHEN NULL THEN x ELSE y END` → `y` |
| `NormalizeInToContains` | Rewrites IN to JSON/array containment when applicable |
| (+ more) | Various comparison normalization and null propagation rules |

---

### `norm/rules/comp.opt` — 26 rules

**Purpose:** Normalize comparison operators, especially for index constraint generation.

| Rule | Why Needed |
|---|---|
| `NormalizeCmpPlusConst` | `a + 1 = 5` → `a = 4`. Moves constants to RHS so index constraints work |
| `NormalizeCmpMinusConst` | `a - 1 < 5` → `a < 6`. Same reason |
| `NormalizeCmpConstMinus` | `1 - a < 5` → `a > -4`. Flips inequality direction |
| `NormalizeCmpDivConst` | `a / 2 = 4` → `a = 8`. Division to RHS |
| `NormalizeTupleEquality` | `(a, b) = (1, 2)` → `a = 1 AND b = 2`. Decomposes for per-column constraint extraction |
| `NormalizeTupleInConst` | `(a,b) IN ((1,2),(3,4))` → conjunctive form; enables constraint generation |
| `FoldCmpBetween` | `a BETWEEN 1 AND 5` → `a >= 1 AND a <= 5`. Desugar BETWEEN |
| `NormalizeGtToLt` | `5 > a` → `a < 5`. Ensures variable always on left |
| `NormalizeGeToLe` | Same for `>=`/`<=` |
| `EliminateNullEquality` | `x = NULL` → `FALSE` (since NULL ≠ NULL in SQL equality) |
| `NormalizeEqToIs` | `a IS DISTINCT FROM b` handling |
| `SimplifyComparison` | Constant folding for compile-time-evaluable comparisons |
| `FoldIsNull` | `IS NULL` on non-nullable expr → `FALSE`; important for NOT NULL constrained columns |
| `FoldIsNotNull` | `IS NOT NULL` on non-nullable → `TRUE` |
| `NormalizeInequality` | Various inequality normalizations |
| (+ more) | Type coercion normalizations, redundancy elimination |

**Key insight:** Most comp rules exist to transform expressions into forms that the constraint
solver (`pkg/sql/opt/constraint/`) can extract index constraints from. Without these, a filter like
`a + 1 = 5` would not generate a span `[/5 - /5]`.

---

### `norm/rules/fold_constants.opt` — 19 rules

**Purpose:** Evaluate constant expressions at optimization time (compile-time constant folding).

| Rule | Why Needed |
|---|---|
| `FoldNullUnary` | `op(NULL)` → `NULL` for ops that propagate nulls |
| `FoldNullBinary` | `NULL op x` or `x op NULL` → `NULL` |
| `FoldNullCompare` | `NULL = x` → `NULL` (not FALSE — SQL three-valued logic) |
| `FoldNullInCondition` | NULL in filter → treated as FALSE in boolean filter context |
| `FoldBinary` | `1 + 2` → `3`. Evaluates binary ops on literal constants |
| `FoldUnary` | `-3` → `-3` (literal). Evaluates unary ops on constants |
| `FoldComparison` | `1 < 2` → `TRUE`. Evaluates comparisons on literals |
| `FoldCast` | `CAST(1 AS FLOAT)` → `1.0` at opt time |
| `FoldFunction` | Pure functions on constants: `length('abc')` → `3` |
| `FoldIndirection` | `ARRAY[1,2,3][2]` → `2`. Array indexing on constant arrays |
| `FoldColumnAccess` | `.field` access on constant tuple |
| `FoldEqualsAnyNull` | `x = ANY(NULL)` → `NULL` |
| `FoldEqualsAnyScalar` | `x = ANY(single value)` → `x = value` |
| `FoldAnyNull` | `ANY(NULL)` → `NULL` |
| `FoldNullTupleInaccessible` | Tuple field access on NULL tuple → NULL |
| `FoldIn` | `x IN (1, 2, 3)` when x is constant → evaluate at opt time |
| (+ more) | Various specific constant-folding cases |

**Why critical:** Eliminates branches of the plan that can never execute, reduces predicate
complexity, enables downstream rules to fire.

---

### `norm/rules/select.opt` — 22 rules

**Purpose:** Push and simplify filters on `Select` nodes.

| Rule | Why Needed |
|---|---|
| `SimplifySelectFilters` *(HighPriority)* | Remove TRUE conjuncts, fold FALSE, flatten ANDs. Must run first |
| `ConsolidateSelectFilters` *(LowPriority)* | Merge `x >= 5 AND x <= 10` into Range op for better selectivity estimation |
| `DeduplicateSelectFilters` *(LowPriority)* | Remove exact duplicate filter predicates |
| `EliminateSelect` | `Select(input, [])` → `input`. Empty filter = no filtering |
| `MergeSelects` | `Select(Select(x, f1), f2)` → `Select(x, f1 AND f2)`. Merge nested selects |
| `PushSelectIntoProject` | `Select(Project(x, cols), filter)` → `Project(Select(x, filter), cols)` when filter only refs input cols. Enables pushing filter below projection |
| `RemoveNotNullCondition` | `col IS NOT NULL` on NOT NULL column → TRUE. Eliminates redundant null check |
| `InlineSelectProject` | Inlines Project columns into Select filter when safe |
| `PushFilterIntoJoinLeft` | Move filter referencing only left side of join to left input |
| `PushFilterIntoJoinRight` | Move filter referencing only right side of join to right input |
| `PushFilterIntoJoinLeftAndRight` | For filters that apply to both sides (e.g., joining col = col), push copies to both |
| `MapFilterIntoJoinLeft` | Use functional dependencies to map filter to left side |
| `MapFilterIntoJoinRight` | Same for right side |
| `PushSelectCondLeftIntoJoinLeftFilter` | Specialized push for complex join conditions |
| `PushSelectIntoGroupBy` | Push HAVING-equivalent filters below GroupBy when they only reference grouping cols |
| `PushSelectIntoValues` | Filter on constant VALUES rows — evaluate at opt time |
| `PushSelectIntoInlinableProject` | Push through project that can be inlined |
| `EliminateJoinUnderSelect` | Eliminate join made redundant by filter |
| `SimplifySelectFiltersWithComputedCol` | Derive filter implications from computed column definitions |
| `InlineConstVar` | `x = 5, x > 3` — substitute constant into other filters |
| `PushSelectIntoWindow` | Push partition-column filters into window operators |

**Why this file matters:** Filter pushdown is the #1 optimization for reducing rows processed.
The further down the tree a filter is pushed, the fewer rows join and project operations see.

---

### `norm/rules/project.opt` — 12 rules

**Purpose:** Simplify and eliminate unnecessary projections.

| Rule | Why Needed |
|---|---|
| `EliminateProject` | `Project(x, cols)` when cols == output(x) → `x`. No-op projection removal |
| `MergeProjects` | `Project(Project(x, c1), c2)` → `Project(x, c2 computed from c1)`. Merge nested projections |
| `EliminateProjectNoopOrdering` | Remove Project that only exists to enforce ordering already satisfied |
| `SimplifyProjections` | Remove unused computed columns from projection list |
| `EliminateComputedCol` | Remove computed column if result is never referenced |
| `InlineProjectInProject` | Inline inner projection computations into outer when cols used exactly once |
| `NormalizeProjectExprs` | Normalize expressions in projections (reuse norm scalar rules) |
| `EliminateProjectCycles` | Handle projection cycles in recursive CTEs |
| `PushProjectIntoJoinLeft` | Move projection below join to reduce row width during join |
| `PushProjectIntoJoinRight` | Symmetric |
| `EliminateProjectUnderScan` | Remove project on scan when scan already projects exact cols |
| `SimplifyProjectSet` | Simplify SRF (set-returning function) projections |

---

### `norm/rules/join.opt` — 31 rules

**Purpose:** Normalize joins — eliminate, simplify, push filters, handle null-rejection.

| Rule | Why Needed |
|---|---|
| `EliminateJoinNoRows` | `Join(x, Values[0 rows])` → empty. Join with empty input = empty |
| `EliminateLeftJoinNoRows` | `LeftJoin(x, empty)` → `Project(x, ...)` with NULLs for right cols |
| `EliminateFullJoinNoRows` | Both sides empty → empty |
| `SimplifyJoinFilters` | Remove TRUE from join ON clause |
| `SimplifyJoinNotNullEquality` | Simplify null-safety predicates on NOT NULL columns |
| `EliminateSelfJoin` | `Join(x, x, x.pk = x.pk)` → `x` when provably the same rows |
| `SimplifyLeftJoinFilters` | Move LeftJoin ON condition to WHERE when it rejects NULLs (converts to InnerJoin) |
| `SimplifyFullJoinFilters` | Same for FullJoin |
| `ConvertAntiToLeftJoin` | AntiJoin → LeftJoin + filter, enables more join ordering options |
| `ConvertSemiToInnerJoin` | SemiJoin to InnerJoin + DistinctOn when safe |
| `DecorrelateJoin` | Remove correlated subquery by converting to join (normalization side) |
| `PushFilterIntoJoin` | Push outer Select filter into join ON clause |
| `SimplifyJoinFiltersOnKey` | When join is on full key, ON simplifications possible |
| `MapEqualityFilter` | Use equalities to substitute equivalent expressions in filters |
| `EliminateJoinWithKey` | Join on unique key where result provably not more rows → simplify |
| `PushJoinIntoIndexJoin` | Rewrite to IndexJoin form when beneficial |
| `EliminateCrossJoin` | `CrossJoin(x, Values[()])` → `x` |
| `NormalizeJoinAnyFilter` | `ANY` subqueries in join conditions → semi/anti joins |
| `NormalizeJoinExistsFilter` | `EXISTS` in join condition → SemiJoin |
| `NormalizeJoinNotExistsFilter` | `NOT EXISTS` → AntiJoin |
| `NormalizeJoinInFilter` | `col IN (subquery)` → SemiJoin |
| `NormalizeJoinNotInFilter` | `col NOT IN (subquery)` → AntiJoin |
| (+ more) | Null-rejection rewrites, filter mapping via functional deps |

---

### `norm/rules/decorrelate.opt` — 33 rules

**Purpose:** Remove correlated subqueries by converting them to joins.

Correlated subqueries (where inner query references outer query cols) are expensive — they must
re-execute for every outer row. Decorrelation converts them to equivalent joins that execute once.

| Rule | Why Needed |
|---|---|
| `DecorrelateSelect` | `Select(x, correlated-filter)` → `x JOIN subquery` |
| `DecorrelateProjectCorrelatedScan` | Pull correlated scan out of subquery into join |
| `DecorrelateGroupByCorrelatedScan` | Same for aggregation subqueries |
| `UnnestSelectInExists` | `EXISTS(Select(x, f))` → SemiJoin form |
| `UnnestProjectInExists` | `EXISTS(Project(x, cols))` → SemiJoin |
| `UnnestGroupByInExists` | Exists with aggregation |
| `UnnestSelectInAny` | `ANY(Select(x, f))` → SemiJoin |
| `UnnestScalarInExists` | Scalar subquery `= EXISTS(...)` handling |
| `InlineWith` | Inline a CTE used in correlated position |
| `HoistSelectExists` | Hoist correlated EXISTS out of Select filters |
| `HoistSelectAny` | Hoist correlated ANY |
| `HoistSelectSubquery` | Hoist scalar correlated subquery to outer join |
| `HoistProjectExists` | Hoist correlated EXISTS from Project |
| `HoistProjectAny` | Hoist correlated ANY from Project |
| `HoistProjectSubquery` | Hoist scalar subquery from Project |
| `HoistJoinSubquery` | Hoist from Join ON clause |
| `HoistValuesSubquery` | Hoist from VALUES expressions |
| `HoistGroupBySubquery` | Hoist from aggregation expressions |
| `HoistWhereOr` | Handle OR of correlated subqueries |
| (+ 14 more) | Additional hoisting and decorrelation patterns |

**Why critical:** Without decorrelation, queries like `SELECT * FROM t WHERE id IN (SELECT id FROM s WHERE s.col = t.col)` run in O(n) subquery executions. With decorrelation → SemiJoin, it runs in O(1) join.

---

### `norm/rules/groupby.opt` — 20 rules

**Purpose:** Simplify and eliminate aggregations.

| Rule | Why Needed |
|---|---|
| `EliminateDistinct` | `DistinctOn(x)` on a key column → `x` (already unique) |
| `EliminateGroupByProject` | GroupBy with empty agg list on key → identity |
| `EliminateGroupBySingleRow` | GroupBy on input with at most 1 row → ScalarGroupBy or eliminate |
| `EliminateGroupByConstCols` | Remove constant cols from GROUP BY (don't affect grouping) |
| `RemoveGroupByColumn` | Column in GROUP BY that's functionally determined by key → remove |
| `EliminateAggregation` | `COUNT(*)` on 1 row → 1; `MIN/MAX(const)` → const |
| `SimplifyCountRows` | `COUNT(*)` → `COUNT_ROWS()` which has an optimized path |
| `ConvertCountToCountRows` | `COUNT(not-null-col)` → `COUNT_ROWS()` since nulls can't occur |
| `EliminateDistinctAggregation` | `SUM(DISTINCT col)` where col is key → `SUM(col)` |
| `ReplaceMinWithLimit` | `MIN(col ORDER BY col)` → `Limit(Sort(x), 1)` — often faster via index |
| `ReplaceMaxWithLimit` | Same for MAX |
| `EliminateMaxMinGroupByKey` | `MAX(col) GROUP BY col` → `col` (trivially equal) |
| `FoldGroupByHaving` | Push simple HAVING filters into WHERE (before aggregation) |
| `SimplifyGroupByKey` | Remove redundant grouping columns using functional dependencies |
| (+ more) | Null handling in aggregates, functional dep simplification |

---

### `norm/rules/prune_cols.opt` — 23 rules

**Purpose:** Remove unused columns from every operator in the tree.

Every rule here follows the pattern: if the parent only uses a subset of child's output columns,
pass that requirement down so the child generates fewer columns. This reduces tuple width
throughout the plan.

| Rule | Why Needed |
|---|---|
| `PruneScanCols` | Scan only those table columns actually needed |
| `PruneSelectCols` | Propagate column pruning through Select |
| `PruneProjectCols` | Remove projection exprs whose output cols are unused |
| `PruneProjectSetCols` | SRF column pruning |
| `PruneInnerJoinLeftCols` | InnerJoin: only output cols actually needed from left |
| `PruneInnerJoinRightCols` | Same for right |
| `PruneLeftJoinRightCols` | LeftJoin: right side cols that aren't output → prune (keeping NULL-fill cols) |
| `PruneSemiJoinRightCols` | SemiJoin right side: only cols needed for join condition |
| `PruneAntiJoinRightCols` | AntiJoin right side |
| `PruneGroupByCols` | GroupBy: only keep needed grouping + aggregate result cols |
| `PruneDistinctOnCols` | DistinctOn column pruning |
| `PruneWindowCols` | Window function partition/order col pruning |
| `PruneUnionCols` | Set operation: only output needed cols from each side |
| `PruneLimitCols` | Limit operator column pruning |
| `PruneOffsetCols` | Offset operator column pruning |
| `PruneOrdinality` | Remove ordinality col if not needed |
| `PruneMutationCols` | INSERT/UPDATE/DELETE: only fetch cols actually used |
| `PruneWithScan` | With (CTE) scan column pruning |
| (+ more) | Various operator-specific prune rules |

**Why critical:** Reducing column count reduces tuple memory, network bandwidth (in distributed
plans), and enables further optimizations (narrower indexes can be used).

---

### `norm/rules/limit.opt` — 13 rules

| Rule | Why Needed |
|---|---|
| `EliminateLimitZero` | `LIMIT 0` → empty result set immediately |
| `EliminateLimitOneGroupBy` | `LIMIT 1` on input with key → may eliminate GroupBy |
| `FoldLimits` | `LIMIT(LIMIT(x, 5), 3)` → `LIMIT(x, 3)`. Inner limit always ≥ outer |
| `EliminateLimitWithMaxRows` | Remove LIMIT when input provably has fewer rows than limit |
| `PushLimitIntoProject` | Push LIMIT below Project to stop projecting unnecessary rows |
| `PushLimitIntoValues` | Evaluate LIMIT on constant VALUES at opt time |
| `PushLimitIntoScan` | Convert LIMIT to Scan's internal row limit (avoids reading extra rows from storage) |
| `PushLimitIntoOffset` | `LIMIT(OFFSET(x, o), l)` → add offset to scan limit |
| `EliminateOffsetZero` | `OFFSET 0` → identity |
| `EliminateLimitUnderMax1Row` | Max1Row already guarantees ≤ 1 row; LIMIT n ≥ 1 is redundant |
| `PushLimitIntoUnionAll` | Push LIMIT into each side of UNION ALL (can short-circuit) |
| (+ more) | Ordering preservation under limit |

---

### `norm/rules/ordering.opt` — 6 rules

| Rule | Why Needed |
|---|---|
| `SimplifyLimitOrdering` | Remove ORDER BY cols not needed given the required output ordering |
| `SimplifyGroupByOrdering` | Remove unnecessary ordering requirements from GroupBy |
| `SimplifyWindowOrdering` | Same for window functions |
| `SimplifyDistinctOnOrdering` | DistinctOn ordering simplification |
| `SimplifyExplainOrdering` | Remove redundant ordering from Explain |
| `NormalizeOrdering` | Canonicalize ordering expressions (consistent column references) |

---

### `norm/rules/inline.opt` — 9 rules

| Rule | Why Needed |
|---|---|
| `InlineProjectConstants` | Replace references to projected constant cols with the constant directly |
| `InlineSelectProject` | Inline projection into select filter (avoids extra Project node) |
| `InlineJoinConstantsLeft` | Constants from left input inlined into join ON condition |
| `InlineJoinConstantsRight` | Same for right |
| `InlineWith` | Inline CTE body when CTE referenced exactly once |
| `InlineWithScan` | When WithScan is provably single-use, inline |
| `InlineConstantEqualities` | `col = 5 AND col > 3` → `5 > 3 → TRUE` — substitute constant into other exprs |
| `DetectValuesContradiction` | Detect contradiction in VALUES rows, eliminate those rows |
| (+ more) | Expression inlining for constant propagation |

---

### `norm/rules/reject_nulls.opt` — 6 rules

| Rule | Why Needed |
|---|---|
| `RejectNullsLeftJoin` | Left join ON condition rejects NULLs from right → effectively InnerJoin |
| `RejectNullsRightJoin` | Symmetric |
| `RejectNullsFullJoin` | FullJoin → LeftJoin or InnerJoin based on null-rejecting WHERE predicates |
| `RejectNullsGroupBy` | Group by key where NULLs are filtered → simplify null handling |
| `RejectNullsProject` | Propagate null-rejection through projection |
| `MarkUnionDistinctNullsRejected` | UNION with NOT NULL context — mark appropriately |

**Why needed:** LEFT/FULL JOIN semantics require NULL-filling when there's no match, but if
the query then filters those NULLs away (e.g., `WHERE right.col IS NOT NULL`), the join is
effectively an INNER JOIN. Converting to INNER JOIN enables more join orderings.

---

### `norm/rules/set.opt` — 8 rules

| Rule | Why Needed |
|---|---|
| `EliminateUnionAllLeft` | `UNION ALL(empty, x)` → `x` |
| `EliminateUnionAllRight` | `UNION ALL(x, empty)` → `x` |
| `EliminateExceptAll` | `EXCEPT ALL(x, empty)` → `x` |
| `EliminateExceptAllRight` | `EXCEPT ALL(empty, x)` → empty |
| `EliminateIntersectAll` | `INTERSECT ALL(x, empty)` → empty |
| `EliminateUnion` | `UNION(x, x)` where x same expression → `x` |
| `EliminateDistinct` | DISTINCT on key col → not needed |
| `SimplifyUnionAllDistinct` | UNION into UNION ALL + DistinctOn when provably equivalent |

---

### `norm/rules/numeric.opt` — 9 rules

| Rule | Why Needed |
|---|---|
| `FoldPlusZero` | `x + 0` → `x` |
| `FoldZeroPlus` | `0 + x` → `x` |
| `FoldMinusZero` | `x - 0` → `x` |
| `FoldMultOne` | `x * 1` → `x` |
| `FoldOneMult` | `1 * x` → `x` |
| `FoldDivOne` | `x / 1` → `x` |
| `FoldNegNeg` | `--x` → `x` |
| `NormalizeUnaryMinus` | `-(-x)` → `x` |
| `FoldModOne` | `x % 1` → `0` |

---

### `norm/rules/mutation.opt` — 3 rules

| Rule | Why Needed |
|---|---|
| `SimplifyInsertOrUpdateFilters` | Simplify filters in upsert operations |
| `EliminateMutationFKChecks` | Remove FK checks for cols that didn't change in UPDATE |
| `EliminateMutationUniqueChecks` | Remove uniqueness checks for non-updated unique columns |

---

### `norm/rules/with.opt` — 3 rules

| Rule | Why Needed |
|---|---|
| `InlineWith` | Inline CTE when referenced once and safe (no side effects, not recursive) |
| `EliminateWith` | Remove unused CTE entirely |
| `HoistWithExpression` | Hoist With above operators to maximize sharing |

---

### `norm/rules/window.opt` — 5 rules

| Rule | Why Needed |
|---|---|
| `SimplifyWindowOrdering` | Remove redundant ordering cols from window function OVER clause |
| `EliminateWindowNoopProject` | Window function that produces same col as input → identity |
| `PushSelectIntoWindow` | Push partition-col filter below window computation |
| `ReduceWindowPartitionCols` | Remove redundant partition cols using functional deps |
| `ReduceWindowOrderingCols` | Same for ordering cols |

---

### `norm/rules/agg.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `ReplaceAggsWithConstant` | When grouping is on a key and aggregate input is constant, fold to constant |

---

### `norm/rules/barrier.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `EliminateBarrier` | Remove optimization barrier when no longer needed (barriers block certain rewrites) |

---

### `norm/rules/max1row.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `EliminateMax1Row` | When input provably has ≤ 1 row (from key/cardinality analysis), remove Max1Row wrapper |

---

### `norm/rules/project_set.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `EliminateProjectSet` | `ProjectSet` (SRF cross join) with no actual SRFs → plain projection |

---

### `norm/rules/cycle.opt` — 2 rules

| Rule | Why Needed |
|---|---|
| `SimplifyCycleDetector` | Remove cycle detector wrapper when it's provably not in a recursive CTE context |
| `EliminateCycleDetector` | Remove entirely when no recursive reference is possible |

---

## Exploration Rules (`xform/rules/`)

Applied by `xform.Explorer` during the optimization pass. Unlike norm rules, exploration rules
generate *additional* alternatives in the memo without replacing the original. The cost model
then selects the best one.

---

### `xform/rules/scan.opt` — 2 rules

| Rule | Why Needed |
|---|---|
| `GenerateIndexScans` | For each secondary index on a table, generate an alternative Scan using that index. Without this, only the primary index would be considered |
| `GeneratePartialIndexScans` | For each partial index whose predicate is implied by the query filter, generate a Scan. Partial indexes are smaller → faster scans |

---

### `xform/rules/select.opt` — 9 rules

| Rule | Why Needed |
|---|---|
| `GenerateConstrainedScans` | For each index, attempt to extract index constraints from the SELECT filter and generate constrained Scan variants. Core of index-accelerated queries |
| `GenerateInvertedIndexScans` | For each inverted index (JSON, array, FTS), generate inverted Scan. Needed for containment/overlap queries |
| `GeneratePartialIndexScans` | Index-specific partial scan generation for each eligible partial index |
| `GenerateZigzagJoins` | When multiple indexes each satisfy part of the filter, generate ZigzagJoin (intersect via index merge). Often faster than single-index scan + filter |
| `GenerateInvertedIndexZigzagJoins` | ZigzagJoin variant for inverted indexes |
| `GenerateSkipScanIndex` | Generate skip-scan (jumping over index groups) for multi-column indexes where leading col is unconstrained |
| `SplitScanIntoUnionScans` | Split single constrained scan into UNION ALL of multiple scans (for better parallelism on disjoint ranges) |
| `SplitDisjunctionIntoUnionScans` | OR predicates → UNION ALL of scans, each handling one disjunct |
| `GenerateLocalityOptimizedScan` | Multi-region: generate a scan that prefers local region replicas first |

---

### `xform/rules/join.opt` — 20 rules

| Rule | Why Needed |
|---|---|
| `ReorderJoins` | Generate all valid join orderings (using join order builder). The optimizer then picks the cheapest order based on cardinality estimates |
| `CommuteLeftJoin` | `LeftJoin(A, B)` → `RightJoin(B, A)`. Symmetric form opens more ordering options |
| `CommuteSemiJoin` | `SemiJoin(A, B)` → `InnerJoin(A, DistinctOn(B))` when safe. Opens reordering |
| `ConvertSemiToInnerJoin` | Alternative semi→inner conversion without distinct |
| `GenerateLookupJoins` | For each index on the right side of a join, generate LookupJoin variant. LookupJoin is often faster when right side is small/indexed |
| `GenerateLookupJoinsWithVirtualCols` | Same but involving virtual (computed) columns in the index |
| `GenerateInvertedJoins` | Generate join that uses inverted index for containment/proximity predicates |
| `GenerateMergeJoins` | When both sides can be sorted on join key, generate MergeJoin (avoids hash table, good for large sorted inputs) |
| `GenerateHashJoin` | Explicitly generate HashJoin alternative (usually the default, but explicit for memo) |
| `GenerateInvertedIndexJoins` | Inverted index join variant |
| `GenerateLocalityOptimizedAntiJoin` | Anti-join preferring local replicas in multi-region |
| `GenerateLocalityOptimizedLookupJoin` | Lookup join preferring local replicas |
| `GenerateLocalityOptimizedSemiJoin` | Semi-join with locality preference |
| `GenerateGeoInvertedJoins` | Geo-spatial inverted index joins |
| `SplitDisjunctionOfJoinTerms` | OR in join ON clause → UNION ALL of two joins, each handling one disjunct |
| `PairGeoInvertedJoins` | Pair geo inverted joins for compound geo predicates |
| (+ more) | Parametrized join variants, cross-join reductions |

---

### `xform/rules/groupby.opt` — 10 rules

| Rule | Why Needed |
|---|---|
| `GenerateStreamingGroupBy` | If input is ordered on grouping cols, generate streaming (sort-based) GroupBy instead of hash-based. Often faster as avoids hash table |
| `GenerateIndexScanGroupBy` | If an index provides the needed order for grouping, generate Scan + StreamingGroupBy |
| `GenerateDistinctOnStreamingGroupBy` | Streaming DistinctOn using sorted input |
| `GenerateStreamingDistinct` | Streaming DISTINCT on sorted input |
| `GenerateStreamingDistinctOnStreamingGroupBy` | Combined streaming distinct + groupby |
| `SplitGroupByOrdinality` | Split groupby by ordinality for ordered output |
| `GenerateEfficientMinMax` | `MIN(col)` / `MAX(col)` where col has an index → `Limit(IndexScan, 1)`. Avoids full scan |
| `GenerateEfficientGroupByMinMax` | `GROUP BY key, MIN(val)` → IndexScan with limit per group |
| `GenerateLocalityOptimizedGroupBy` | Multi-region: prefer local nodes for grouping |
| `GenerateLimitedGroupBy` | When GROUP BY followed by LIMIT, propagate limit into groupby |

---

### `xform/rules/limit.opt` — 12 rules

| Rule | Why Needed |
|---|---|
| `GenerateLimitedScans` | `LIMIT n` on a Scan → generate Scan with internal row limit (stops reading early) |
| `GenerateLimitedIndexScans` | Same for index scans |
| `PushLimitIntoLookupJoin` | Propagate limit into lookup join (stop after n results) |
| `PushLimitIntoUnionAll` | Push limit into each side of UNION ALL |
| `PushLimitIntoDistinctOn` | Limit + DistinctOn → limit within distinct computation |
| `GenerateOrderedTopK` | `ORDER BY + LIMIT` → TopK operator (heap-based, avoids full sort) |
| `GenerateStreamingTopK` | TopK when input partially ordered |
| `GenerateIndexScanTopK` | `ORDER BY col LIMIT n` → forward/reverse IndexScan with limit |
| `GenerateLimitedHashGroupBy` | LIMIT after GROUP BY → limit hash groupby output early |
| `GenerateLimitedStreamingGroupBy` | Same for streaming groupby |
| `GenerateLimitedDistinctOn` | LIMIT + DistinctOn combination |
| `GenerateLocalityOptimizedLimit` | Multi-region limit with local preference |

---

### `xform/rules/project.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `GenerateProjectedInvertedIndexScans` | When projection output requires an inverted index, generate the scan variant |

---

### `xform/rules/set.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `GenerateUnionAllExchange` | UNION ALL in distributed setting → add Exchange operators to distribute work, potentially parallelizing across nodes |

---

### `xform/rules/insert.opt` — 1 rule

| Rule | Why Needed |
|---|---|
| `GenerateFastPathInsert` | When INSERT satisfies fast-path conditions (no FK checks needed, simple values), generate optimized FastPathInsert that skips normal write pipeline overhead |

---

### `xform/rules/generic.opt` — 2 rules

| Rule | Why Needed |
|---|---|
| `GenerateParameterizedJoin` | For generic plans (placeholder values unknown at opt time), convert `Select(Scan, placeholder-filter)` to `InnerJoin(Values[placeholders], Scan, filter)`. Allows lookup join to be planned without knowing actual values |
| `ConvertParameterizedLookupJoinToPlaceholderScan` | Refine parameterized join into a placeholder-based scan for specific execution |

---

### `xform/rules/cycle.opt` — 2 rules

| Rule | Why Needed |
|---|---|
| `GenerateCycleDetectedPlan` | Generate a recursive CTE execution plan with cycle detection enabled |
| `GenerateCycleDetectedScan` | Variant that uses index scan for cycle detection in recursive CTEs |

---

## Summary Statistics

| Category | Files | Rules/Operators |
|---|---|---|
| Operator definitions (ops/) | 6 | ~303 operators |
| Normalization rules (norm/rules/) | 25 | ~371 rules |
| Exploration rules (xform/rules/) | 10 | ~60 rules |
| **Total** | **41** | **~734** |

## Key Design Principles

1. **Normalization reduces search space.** By canonicalizing expressions first, exploration rules
   see fewer distinct forms to enumerate. E.g., `NormalizeNestedAnds` ensures And trees are always
   left-deep — exploration rules only need to match one shape.

2. **Exploration generates alternatives, not replacements.** Exploration rules add new expressions
   to a memo *group* (equivalence class). The original expression stays. Cost model picks winner.

3. **Priority tags control rule ordering.** `HighPriority` rules (like `SimplifySelectFilters`)
   run before others in the same file. `LowPriority` rules run after, allowing higher-priority
   rules to create opportunities they can then exploit.

4. **Custom functions bridge DSL and Go.** Complex matching/construction that can't be expressed
   in Optgen syntax is implemented in `*_funcs.go` files and called from rules with `& (FuncName ...)`.

5. **Functional dependencies drive many rules.** The FD (functional dependency) system tracks
   which columns are determined by others. Many rules use FDs to prove that grouping columns
   are redundant, that joins can be simplified, etc.

6. **Index constraint extraction is the payoff of comp/select normalization.** Rules in `comp.opt`
   that rewrite `a + 1 = 5` → `a = 4` exist specifically so the constraint solver can extract
   the index span `[/4 - /4]` and turn a full scan into a point lookup.

7. **Multi-region rules are additive.** Rules like `GenerateLocalityOptimizedScan` add
   region-aware alternatives; if the query is not multi-region, these alternatives simply cost
   more and are never selected.

---

## Memo Data Structure

The memo (`pkg/sql/opt/memo/memo.go`) is the core data structure of the optimizer. It efficiently stores a forest of logically equivalent query plans by exploiting the fact that many sub-plans are shared across alternatives.

### Structure

```
Memo
  └── groups: []memoGroup
        └── memoGroup
              ├── relProps: *relationalProps   (shared by all exprs in group)
              ├── scalarProps: *scalarProps
              └── exprs: []memoExpr
                    └── memoExpr
                          ├── op: operator
                          ├── children: []groupID   (references to other groups)
                          ├── physProps: *physicalProps
                          └── private: interface{}  (operator-specific data)
```

**Group identity:** Each group is identified by a fingerprint = `(operator, child group IDs)`. Before inserting a new expression, the factory computes its fingerprint. If a group with that fingerprint already exists, the expression joins that group. If not, a new group is created.

**Equivalence invariant:** All memo-expressions in a group are logically equivalent — they produce the same rows (modulo ordering). Normalization rules fire at construction time (via `norm.Factory`) so non-normalized forms never enter the memo.

**Expression extraction (binding):** Transformation rules don't traverse raw Go data structures. Instead, they use a **binding** step: patterns are matched against the memo, extracting only the parts the rule needs. Pattern leaves match opaquely (entire subtree as a group reference); pattern trees recursively extract for manipulation.

### Exploration vs Normalization in the Memo

| Phase | When | Adds to memo? | Replaces? |
|---|---|---|---|
| Normalization | During construction | Yes (new group) | Replaces root (single canonical form) |
| Exploration | During optimization | Yes (new expr in existing group) | Never — original stays |

`norm.Factory.ConstructXxx()` builds normalized expressions and inserts them, firing norm rules eagerly. `xform.Explorer.exploreGroup()` iterates over groups, applies exploration rules, adds new alternatives to existing groups.

### Cascades-Style Search

CockroachDB uses **Cascades** (Graefe 1995) style optimization — interleaved exploration and costing — rather than Volcano's separate expansion and costing phases.

```
OptimizeGroup(group, requiredProps):
  exploreGroup(group)           // fire all applicable xform rules, add alternatives
  for each expr in group.exprs:
    cost = CostExpr(expr, requiredProps)
    track minimum cost expr
  return bestExpr

CostExpr(expr, requiredProps):
  for each child group:
    OptimizeGroup(child, derivedRequiredProps)  // recursive
  cost += operatorCost(expr, childCardinalities)
  if !childSatisfiesRequiredProps:
    inject enforcer, add enforcer cost
```

**Branch-and-bound pruning:** If an expression's lower-bound cost already exceeds the best known cost for the group, its subtree is pruned. This is critical for large join search spaces.

---

## Logical Properties

Logical properties are attached to **memo groups** (shared by all expressions in the group). Computed once per group when first constructed.

### Relational Logical Properties

Defined in `pkg/sql/opt/props/logical.go`:

| Property | Type | Meaning |
|---|---|---|
| `OutputCols` | `ColSet` (bitmap) | Columns produced by expression |
| `OuterCols` | `ColSet` | Columns referenced but not produced (free variables — indicates correlated subquery) |
| `NotNullCols` | `ColSet` | Columns provably never NULL |
| `Cardinality` | `CardinalitySet` | Min/max row estimate (exact where possible) |
| `FuncDeps` | `FuncDepSet` | Functional dependencies (see below) |
| `Stats` | `Statistics` | Row count, column stats, histograms |

### Scalar Logical Properties

| Property | Type | Meaning |
|---|---|---|
| `InputCols` | `ColSet` | Columns consumed |
| `OuterCols` | `ColSet` | Free variables (correlated) |
| `HasVolatileFunc` | `bool` | Contains `NOW()`, `random()` etc — blocks caching |
| `HasSubquery` | `bool` | Contains subquery — triggers decorrelation |

### Functional Dependencies (FD Set)

`pkg/sql/opt/props/func_dep.go`. The FD set tracks:

1. **Strict keys** — column set S is a key if no two rows have identical values on S. All cols must be NOT NULL. Derived from PRIMARY KEY, UNIQUE NOT NULL indexes.
2. **Lax keys (weak keys)** — uniqueness on non-NULL values only. From UNIQUE indexes with nullable columns.
3. **Determiners** — `{A} → {B}`: column A functionally determines B. From computed column definitions.
4. **Equivalency groups** — `{A = B = C}`: columns proven equal via join or filter. Enables predicate inference across joins.
5. **Constant columns** — `{A = const}`: column has been equated to a constant. From `col = 5` filters.

**How FDs drive rules:**
- `EliminateGroupBySingleRow`: if grouping columns contain a key → at most one group → GroupBy unnecessary.
- `SimplifyGroupByKey`: if a non-key column is functionally determined by other grouping cols → remove it.
- `MapFilterIntoJoinLeft/Right`: if `a.x = b.x` via join condition and `a.x` has FD to another col → predicate can be mapped.
- Decorrelation: outer column FDs help prove that hoisted subquery join is a key-join → distinct is not needed.

---

## Physical Properties System

Physical properties describe **how** data is presented, not **what** data — they live outside relational algebra.

### Tracked Physical Properties

| Property | Description | Enforcer |
|---|---|---|
| `Presentation` | Column ordering and naming in output | No-op reproject |
| `Ordering` | Row sort order (`ORDER BY`) | `Sort` operator |
| `Distribution` | Which KV ranges / regions own rows | `Distribution` enforcer |

### Required vs Provided

**Required properties** flow top-down: parent specifies what it needs from child. Originate from:
- `ORDER BY` → ordering requirement
- `DISTINCT` → key requirement (handled via norm rules, not physical props)
- Merge join → requires both inputs sorted on join keys
- Lookup join → requires left input sorted on prefix matching index

**Provided properties** flow bottom-up: each operator specifies what physical properties its output satisfies given its child's properties. E.g.:
- `IndexScan(idx)` provides ordering = `idx.columns` (ascending or descending).
- `HashJoin` provides no ordering (output is unordered).
- `MergeJoin` provides merged ordering on join key.
- `Sort(input, ordering)` provides the specified ordering.

**Mismatch → Enforcer injection:** When a required property is not provided by any alternative in a group, the optimizer introduces an enforcer:
```
Sort(child, ordering) -- satisfies ordering requirement
Distribution(child)   -- routes data to required gateway node
```
Enforcers have costs (Sort = O(n log n) I/O + CPU; Distribution = network transfer).

### Multi-Region Distribution Properties

For `REGIONAL BY ROW` tables, each row has a `crdb_region` hidden column. The optimizer:
1. Adds `Distribution` required property at query root (gateway region).
2. Generates `GenerateLocalityOptimizedScan` — scans local region first with `LIMIT`.
3. If local scan produces insufficient rows, a follow-up scan covers other regions.
4. `GenerateLocalityOptimizedAntiJoin`, `GenerateLocalityOptimizedLookupJoin` extend this to join operators.

This turns cross-region queries into cheap local-first queries for most workloads.

---

## Cost Model

`pkg/sql/opt/xform/coster.go`. Costs are dimensionless "time units" — relative, not absolute.

### Cost Components

```
Cost(expr) = SeqIOCost + RandIOCost + CPUCost + NetworkCost

SeqIOCost  = rows × scanFactor × seqIOCostFactor
RandIOCost = rows × randomIOCostFactor  (for random-access index lookups)
CPUCost    = rows × cpuCostFactor × perRowWork
NetworkCost = rows × rowWidth × networkCostFactor  (for distributed plans, 0 for local)
```

Default cost factors (normalized, not real seconds):
- `seqIOCostFactor = 1.0`
- `randomIOCostFactor = 4.0` (random I/O ≈ 4× sequential)
- `cpuCostFactor = 0.01` (CPU cheap vs I/O)
- `networkCostFactor = 1.0` (same as seq I/O by default)

### Per-Operator Cost Logic

| Operator | Dominant Cost Factor |
|---|---|
| `Scan` (full) | SeqIO × estimated row count |
| `IndexScan` (constrained) | RandIO × selectivity × rows |
| `LookupJoin` | RandIO × left_rows × matches_per_row |
| `HashJoin` | CPU × (left_rows + right_rows) + possible spill IO |
| `MergeJoin` | CPU × rows (requires pre-sorted inputs; amortizes Sort cost) |
| `Sort` | CPU × n log n (dominated by comparison cost) |
| `IndexJoin` | RandIO × rows (one random KV read per row) |
| `ZigzagJoin` | RandIO × (rows_in_index1 + rows_in_index2) × selectivity |

### Spill Cost

When hash join or sort input exceeds `work_mem` equivalent, the optimizer adds disk spill cost:
```
spillCost = (rows × rowWidth / diskBlockSize) × seqIOCostFactor × spillFactor
```

### Network Cost in Distributed Plans

For queries spanning multiple nodes, each data shuffle (hash join redistribution, aggregation scatter-gather) incurs:
```
networkCost = rowBytes × networkCostFactor × hops
```

Gateway node is cheapest (0 network); remote node data costs proportional to transfer size.

### Optimal Substructure

Cost model satisfies optimal substructure: `Cost(tree) = Cost(root) + Σ Cost(subtrees)`. This enables dynamic programming — optimize each group independently, then compose. Critical invariant for Cascades to work correctly.

---

## Statistics & Cardinality Estimation

`pkg/sql/opt/memo/statistics_builder.go` + `pkg/sql/stats/`.

### What Gets Collected

`CREATE STATISTICS` / auto-stats collects per table:
- **Row count**: exact count at scan time
- **Null count**: per column
- **Distinct count**: HyperLogLog (HLL-TailCut+ variant). Parallel-friendly: HLL sketches merge across nodes.
- **Histogram**: equi-depth histogram on first column of each index. Also collected for any explicitly requested column.

Histogram format: up to 200 buckets, each bucket stores `(upper_bound, row_count, distinct_count, null_count)`. Equi-depth = each bucket covers approximately equal number of rows.

### Multi-Column Statistics

By default collects multi-column stats on the **prefix columns of each index** (columns that appear together in WHERE clauses for that index). Purpose: estimate correlation between columns — e.g., `(country, city)` tuple cardinality is much lower than `distinct(country) × distinct(city)` if city is nested within country.

### Automatic Stats Refresh

`pkg/sql/stats/automatic_stats.go`:
- Trigger: probabilistic after each mutation. After a batch of mutations on table T: `P(refresh) = (rows_mutated / total_rows) / threshold` where threshold ≈ 0.20 (20% changed).
- Background job: global serialization, one stats collection at a time. If node fails, another node adopts.
- Throttling: adaptive sleep inserted every 10K rows scanned. Sleep duration scales with CPU utilization.
- Sampling: reservoir sampling for histograms — sample S rows from table scan, compute histogram from sample.

### Statistics Forecasting

When ≥3 historical stats collections exist and row counts fit a linear trend, CockroachDB generates **forecasted statistics** extrapolating to the current time. Used between refreshes when table is changing rapidly.

```
forecasted_row_count = last_row_count + slope × (now - last_collection_time)
forecasted_distinct_count[col] = scale proportionally
```

Forecasting prevents stale stats from causing catastrophic plan regressions during bulk loads.

### Selectivity Estimation

`statistics_builder.go` propagates histograms up the operator tree:

```
Select(input, filter):
  sel = selectivity(filter, input.stats)
  output.rowCount = input.rowCount × sel
  output.histogram[col] = input.histogram[col].ApplySelectivity(sel, filter.bounds)

Join(left, right, condition):
  if equijoin on col:
    sel = 1 / max(distinct(left.col), distinct(right.col))
  output.rowCount = left.rowCount × right.rowCount × sel
  // histogram intersection for equijoin columns
```

**Selectivity for range predicates:** computed by scanning histogram buckets that overlap the predicate range, summing their row counts.

**Null handling:** null count tracked separately. `IS NULL` selectivity = null_count / row_count. `IS NOT NULL` = 1 - (null_count / row_count).

**Distinct count propagation through joins:** for join output column from left, `distinct(col) = min(distinct(left.col), output.rowCount)`.

---

## Join Ordering

`pkg/sql/opt/xform/join_order_builder.go`. The `ReorderJoins` exploration rule delegates to this.

### Algorithm

CockroachDB uses a **DPhyp** (Dynamic Programming on Hypergraphs) variant — the same algorithm used by HyPer, Umbra, DuckDB. Enumerate all subsets of joined relations, find minimum-cost join tree for each subset.

```
DPhyp:
  for each subset S of relations (bottom-up, size 1 → n):
    for each valid partition (S1, S2) of S where S1 ∪ S2 = S:
      if there exists a join predicate between S1 and S2:
        cost = Cost(BestPlan(S1)) + Cost(BestPlan(S2)) + joinCost(S1, S2)
        BestPlan(S) = min(BestPlan(S), cost)
```

**Hyperedges:** join predicates that reference more than 2 tables (e.g., from OR conditions or transitive predicates) are represented as hyperedges. DPhyp handles these without enumerating invalid cross products.

### Cardinality Limit & Fallback

`reorderJoinsLimit` session variable (default 8). When join count > limit:
- **Greedy fallback**: iteratively pick the pair (left, right) with cheapest immediate join cost. O(n²) instead of O(2ⁿ).
- Greedy avoids cross products but may miss globally optimal orderings.

For `n ≤ 8`: full DPhyp, optimal for tree-structured queries and near-optimal for cyclic queries. DP table entries = O(2ⁿ), manageable for n ≤ 8.

### What DPhyp Considers

- All join types: inner, left, full, semi, anti (with correctness constraints on associativity/commutativity)
- All plan shapes: left-deep, right-deep, bushy
- Join implementations: hash join, merge join, lookup join (index), zigzag join
- Cross joins excluded unless no predicate exists (forced by schema)

### Cardinality Estimation for Join Ordering

Row count for join output = `left.rows × right.rows × joinSelectivity`. Join selectivity:
- Equijoin on indexed column: `1 / max(distinct(left.col), distinct(right.col))`
- Non-equijoin: default selectivity 0.333 (heuristic)
- FK join (foreign key relationship detected): selectivity = `1 / distinct(pk_side)` — usually very precise

---

## Plan Caching & Prepared Statements

`pkg/sql/plan_opt.go`, `pkg/sql/querycache/`.

### Five Planning Stages

```
1. Parse      → SQL string → AST
2. OptBuild   → AST → relational expression tree (semantic analysis, name resolution)
3. Normalize  → norm.Factory applies all norm rules → canonical memo
4. Explore    → xform.Optimizer applies xform rules + costs → best plan
5. ExecBuild  → optimized plan → execinfrapb.ProcessorSpec tree (DistSQL specs)
```

### What Gets Cached

| Protocol | Cached State | Reuse Condition |
|---|---|---|
| Simple query (same SQL string) | Final explored plan (stage 4 output) | Identical SQL string, same schema version |
| `PREPARE` once, execute N times | Normalized memo through stage 3 | Same schema; re-runs stages 4+5 per execution |
| Generic plans (v23.1+) | Full plan including exploration (stage 4) | Parameter-independent plan; checked after 5 custom executions |

### AssignPlaceholders

Between stages 3 and 4 for prepared statements: substitute `$1, $2, ...` with actual values. This triggers additional normalization (e.g., `FoldBinary` on `col + $1` with $1=5 → `col + 5 → a = 4` after comp rules). The explore stage then sees concrete values — better selectivity estimates, better plans.

**Generic plans** skip AssignPlaceholders — the plan is optimized with placeholder types but no values. The optimizer generates parameter-independent execution. Chosen when: plan shape doesn't change across values (e.g., simple point lookup) OR custom plan cost is consistently ≥ generic plan cost.

### Replanning Triggers

Cache invalidation occurs when:
- Schema change: column added/removed, index created/dropped, table renamed/dropped.
- Database context change (`SET database`).
- Session setting affecting planning (`SET optimizer_*`, `SET reorder_joins_limit`).
- Statistics refresh on a table used by the query (triggers replan in background via `sql.planner.replanQuery()`).

### Query Fingerprinting

Queries are fingerprinted by normalizing the SQL string: strip literal values, normalize whitespace, replace `$N` with `?`. Fingerprint → statement bundle in `system.statement_statistics`. Used for plan regression detection and `EXPLAIN BUNDLE`.

---

## DistSQL Execution Integration

`pkg/sql/distsql_physical_planner.go`, `pkg/sql/colflow/`.

### Pipeline: Optimizer → Execution

```
Optimizer best plan (memo tree)
  ↓ execbuilder.Builder.Build()
planNode tree (scanNode, filterNode, joinNode, ...)
  ↓ DistSQLPlanner.PlanAndRun()
PhysicalPlan (ProcessorSpec DAG)
  ↓ FlowSpec per node
  ↓ SetupFlowRequest RPC to each node
colflow.VectorizedFlow (operator DAG)
  ↓ BatchFlowCoordinator.Run()
results → client
```

### ProcessorSpec Types

`pkg/sql/execinfrapb/processors.proto`:

| Spec | Corresponds To | Notes |
|---|---|---|
| `TableReaderSpec` | `Scan` / `IndexScan` | Specifies key spans, index, column IDs |
| `FiltererSpec` | `Select` | Post-processor on TableReader |
| `HashJoinerSpec` | `HashJoin` | Left/right types, equality cols, join type |
| `MergeJoinerSpec` | `MergeJoin` | Ordering spec for both inputs |
| `SorterSpec` | `Sort` | Output ordering, mem limit |
| `AggregatorSpec` | `GroupBy` / `Agg` | Agg functions, grouping cols, partial vs final |
| `ProjectSetSpec` | SRF projection | Set-returning functions |
| `NoopCoreSpec` | Final merge at gateway | Used to collect distributed results |

### Physical Planning Decisions

`DistSQLPlanner.checkSupportForPlanNode()` decides: local execution vs distributed.

- **Local**: simple queries on single node, admin statements, DDL.
- **Distributed**: queries where table ranges span multiple nodes. Scan processors placed on nodes owning the data ranges (locality-aware placement).

For joins: if one side fits in memory and the other is distributed → **broadcast join** (copy small side to all nodes). Otherwise → **hash-shuffle join** (redistribute both sides by join key hash).

### Vectorized Execution Engine

`pkg/sql/colflow/`, `pkg/sql/colexec/`:

- All operators implement `colexecop.Operator` with `Next() coldata.Batch` returning columnar batches of 1024 rows.
- Columnar format: each column is a typed Go slice + validity bitmap (for NULLs). No row-oriented iteration.
- Cross-node data transport: `colrpc.Outbox` serializes batches as **Apache Arrow IPC** format, sends via gRPC stream. `colrpc.Inbox` deserializes.
- `execgen` tool generates type-specialized operator implementations for all type combinations at compile time — no runtime reflection.

### Explain Output

```sql
EXPLAIN (DISTSQL, VERBOSE) SELECT ...
```
Returns per-node processor graph. `EXPLAIN ANALYZE (DISTSQL)` adds runtime row counts, memory usage, and time per processor — essential for diagnosing plan quality issues.

---

## Apply Operator & Decorrelation Internals

The `decorrelate.opt` rules translate correlated subqueries into joins. The mechanism:

### Apply Operator

A correlated subquery is initially represented as an **Apply** operator:
```
Apply(left: R, right: S(outer_cols), join_type)
  -- right side references columns from left; must re-execute per left row
  -- O(|R|) subquery executions
```

### Decorrelation Algorithm (Unnesting)

Rules in `decorrelate.opt` transform Apply into regular Join by **hoisting** the correlated expression:

```
Step 1: Identify outer column references in right subtree
Step 2: For each correlated operator in right (Select, Project, GroupBy, etc.):
  - "Hoist" the correlated computation above the Apply
  - Replace inner correlation reference with a new column
Step 3: Apply becomes InnerJoin / SemiJoin / AntiJoin
  - Join condition = original correlation predicate
  - Right side is now a standalone expression (no outer column refs)
```

**Example:**
```sql
SELECT * FROM t WHERE id IN (SELECT id FROM s WHERE s.col = t.col)
-- Initial: Apply(t, Select(Scan(s), s.col = t.col), SemiJoin)
-- After DecorrelateSelect:
--   SemiJoin(t, Scan(s), t.col = s.col)
-- t.col = s.col is now a regular join predicate; executes once
```

**When decorrelation fails:** subquery references multiple levels of outer scope, or contains volatile functions, or uses complex aggregation patterns that can't be unnested. Fallback: apply operator with subquery cache (memo per distinct outer-col value).
