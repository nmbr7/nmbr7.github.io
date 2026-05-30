+++
title = "Calcite Internals"
[taxonomies]
tags = ["database", "query-planning", "query-optimization", "java", "apache", "calcite", "volcano", "cascades"]
+++


# Apache Calcite: Query Planning and Optimization Framework

**Key paper:** Begoli, E., Camacho-Rodríguez, J., Hyde, J., Mior, M., Lemire, D. "Apache Calcite: A Foundational Framework for Optimized Query Processing Over Heterogeneous Data Sources." SIGMOD 2018. doi:10.1145/3183713.3190662.

Apache Calcite is a Java query planning framework: SQL parser → AST → logical plan → physical plan, pluggable into any execution backend. Not an execution engine — it produces a `RelNode` tree that the backend executes. Used by Flink, Hive, Druid, Kylin, Storm, Samza, Calcite-Avatica (JDBC), Phoenix (HBase SQL), and many others.

---

## Architecture Pipeline

```
SQL string
    │
    ▼ SqlParser (JavaCC/Fmpp grammar)
SqlNode AST  (SqlCall, SqlIdentifier, SqlLiteral, ...)
    │
    ▼ SqlValidator (scope analysis, type inference, name resolution)
Validated SqlNode
    │
    ▼ SqlToRelConverter
RelNode tree (logical, Convention.NONE)
    │
    ├── HepPlanner (normalization rules: filter pushdown, project merge, decorrelation)
    │
    ▼ VolcanoPlanner (cost-based: join ordering, physical convention selection)
Physical RelNode tree (Convention.ENUMERABLE or adapter-specific)
    │
    ▼ Backend executes (or EnumerableRel.implement() → Linq4j/Janino Java code)
```

---

## SQL Parser

### JavaCC Grammar (core/src/main/codegen/templates/Parser.jj, generated via Fmpp)

The grammar is a Freemarker template (`Parser.jj.ftl`) processed by Fmpp into a JavaCC grammar file. JavaCC generates a recursive-descent parser from the grammar; no separate lexer/parser split — JavaCC interleaves token and grammar rules.

**Extension mechanism:** `config.fmpp` lets adapters inject additional tokens, keywords, and grammar productions into designated extension points (`SqlCreateFoo`, `SqlShowFoo`). Calcite uses this for `CREATE TABLE`, `CREATE VIEW`, `EXPLAIN`, `ALTER`, etc. Third-party dialects (Flink, Phoenix) extend the grammar this way, adding their syntax without forking the base grammar.

### SqlNode AST Hierarchy (org.apache.calcite.sql)

```
SqlNode
├── SqlCall               — any SQL construct with operands (select, join, function call, ...)
│   ├── SqlSelect         — SELECT: selectList, from, where, groupBy, having, orderBy, fetch, offset
│   ├── SqlJoin           — JOIN: left, right, joinType, conditionType, condition
│   ├── SqlBasicCall      — generic n-ary call: operator (SqlOperator) + operands[]
│   ├── SqlOrderBy        — wraps a query with ORDER BY + LIMIT
│   └── ... (SqlInsert, SqlUpdate, SqlDelete, SqlCreateTable, ...)
├── SqlIdentifier         — qualified name (parts[]: String[]; dot-separated)
├── SqlLiteral            — typed literal: value + SqlTypeName
├── SqlNodeList           — list of SqlNodes (e.g. SELECT list, GROUP BY list)
├── SqlDataTypeSpec       — inline type reference (for CAST etc.)
├── SqlIntervalQualifier  — INTERVAL unit (YEAR, MONTH, DAY, ...)
└── SqlDynamicParam       — ? placeholder (index: int)
```

**SqlOperator** is the operator attached to `SqlBasicCall`: `SqlBinaryOperator` (`+`, `-`, `AND`, `OR`, `=`), `SqlPrefixOperator` (`NOT`, unary `-`), `SqlFunction` (scalar functions), `SqlAggFunction` (`SUM`, `COUNT`, `MAX`), `SqlSpecialOperator` (`CASE`, `CAST`, `IN`). Each has `SqlSyntax` (BINARY, PREFIX, POSTFIX, FUNCTION, SPECIAL, FUNCTION_STAR) and `SqlKind` (AND, OR, EQUALS, LIKE, CASE, etc.).

### SqlValidator (org.apache.calcite.sql.validate.SqlValidatorImpl)

Pass over the AST that resolves names, infers types, validates semantics:

1. **Scope analysis**: builds nested `SqlValidatorScope` objects (SelectScope, JoinScope, AggregatingScope, etc.) mapping identifier names to `SqlValidatorNamespace` (the relational source: table, alias, subquery).
2. **Name resolution**: `SqlIdentifier` resolved against scope chain via `SqlValidatorScope.resolve()`.
3. **Type inference**: bottom-up; each `SqlNode.validate()` call propagates types upward. `SqlTypeFactory` (usually `JavaTypeFactoryImpl`) creates `RelDataType` instances.
4. **Implicit casting**: type coercion rules (`SqlTypeCoercionRule`) insert `CAST` nodes into the AST for implicit promotions (e.g., `INT + DOUBLE → DOUBLE`).
5. **Aggregate validation**: `AggregatingScope` checks that non-aggregated columns appear in GROUP BY.
6. **Output**: `SqlNode` tree with inferred `RelDataType` attached (accessible via `SqlValidator.getValidatedNodeType(node)`).

---

## RelNode Tree

### RelNode Base (org.apache.calcite.rel.RelNode)

```java
interface RelNode extends RelOptNode, Cloneable {
  RelDataType    getRowType();          // output row type
  RelTraitSet    getTraitSet();         // set of traits (convention, collation, distribution)
  List<RelNode>  getInputs();           // child relations
  RelNode        copy(RelTraitSet, List<RelNode>); // deep copy with new traits+inputs
  RelOptCost     computeSelfCost(RelOptPlanner, RelMetadataQuery);
  String         getDigest();           // canonical string for deduplication
  RelNode        accept(RelShuttle);    // visitor
  <T> T          accept(RexShuttle);   // expression visitor (rewrites RexNodes)
}
```

`getDigest()` is the hash key for `RelSet` deduplication in VolcanoPlanner's memo. Built from class name + row type + inputs' digests + operator-specific fields. Two `RelNode`s with the same digest are considered equivalent and merged.

`copy()` is called by the planner whenever it applies a transformation: produces a new `RelNode` with new trait set and/or new child nodes.

### Logical RelNodes (org.apache.calcite.rel.logical)

| Class | SQL construct | Key fields |
|---|---|---|
| `LogicalTableScan` | FROM table | `RelOptTable table` |
| `LogicalFilter` | WHERE / HAVING | `RexNode condition` |
| `LogicalProject` | SELECT exprs | `List<RexNode> projects, List<String> fieldNames` |
| `LogicalJoin` | JOIN | `JoinRelType joinType, RexNode condition` |
| `LogicalAggregate` | GROUP BY / aggregates | `ImmutableBitSet groupSet, List<AggCall> aggCalls` |
| `LogicalSort` | ORDER BY + LIMIT/OFFSET | `RelCollation collation, RexNode fetch, RexNode offset` |
| `LogicalUnion` | UNION [ALL] | `boolean all` |
| `LogicalIntersect` | INTERSECT | `boolean all` |
| `LogicalMinus` | EXCEPT | `boolean all` |
| `LogicalValues` | VALUES(...) | `ImmutableList<ImmutableList<RexLiteral>> tuples` |
| `LogicalTableFunctionScan` | TABLE(func()) | `RexNode call, Type elementType` |
| `LogicalCorrelate` | correlated subquery | `CorrelationId correlationId, ImmutableBitSet requiredColumns, JoinRelType joinType` |
| `LogicalExchange` | shuffle | `RelDistribution distribution` |
| `LogicalWindow` | window functions | `List<Window.Group> groups, List<RexLiteral> constants` |
| `LogicalMatch` | MATCH_RECOGNIZE | pattern matching fields |
| `LogicalTableModify` | INSERT/UPDATE/DELETE | `Operation operation, List<String> updateColumnList` |

### Abstract Core RelNodes (org.apache.calcite.rel.core)

Abstract superclasses used by both logical and physical nodes: `Aggregate`, `Filter`, `Join`, `Project`, `Sort`, `SetOp`, `TableScan`, `Calc` (combined filter+project). `Calc` contains a `RexProgram` — compact representation of project + filter as a list of `RexNode`s plus a condition; avoids separate `Filter` + `Project` nodes in the Enumerable backend.

### AggCall

```java
class AggCall {
  SqlAggFunction     aggregation;   // SUM, COUNT, MIN, MAX, etc.
  List<Integer>      argList;       // input column indices
  boolean            distinct;      // COUNT(DISTINCT ...)
  boolean            approximate;   // APPROX_COUNT_DISTINCT
  RelFieldCollation[] orderKeys;    // ORDER BY inside aggregate (array_agg, first_value)
  RexNode            filterArg;     // FILTER (WHERE ...) index
  String             name;          // output field name
}
```

### RelInput / RelWriter (Serialization)

`RelInput` and `RelWriter` provide JSON serialization for `RelNode` trees. Each `RelNode` calls `RelWriter.input(...)`, `.item(...)`, `.itemIf(...)` in its `explain()` method. `RelJsonWriter` implements `RelWriter` and writes a JSON array of nodes with `id`, `relOp`, and field map. `RelJsonReader` reconstructs from JSON. Used for plan caching and debugging.

---

## RexNode Expression Tree

`RexNode` is the expression AST within a single `RelNode`. Pure value expressions — no subrelation references except through `RexSubQuery`.

### Full RexNode Hierarchy (org.apache.calcite.rex)

| Class | Purpose |
|---|---|
| `RexCall` | Function/operator call: `SqlOperator op + List<RexNode> operands`. Covers arithmetic, comparison, logical, string ops, CASE, CAST, ARRAY, MAP |
| `RexInputRef` | Reference to input column by index: `int index, RelDataType type` |
| `RexLiteral` | Typed constant: `Comparable value, RelDataType type, SqlTypeName typeName` |
| `RexFieldAccess` | Field access on a struct: `RexNode expr, RelDataTypeField field` |
| `RexOver` | Window function: `SqlAggFunction op, List<RexNode> partitionKeys, List<RexFieldCollation> orderKeys, RexWindowBound lowerBound/upperBound, boolean distinct` |
| `RexCorrelVariable` | Outer reference in correlated subquery: `CorrelationId id, RelDataType type` |
| `RexSubQuery` | Scalar subquery / IN / EXISTS / UNIQUE: `SqlKind kind, RelNode rel, List<RexNode> operands` |
| `RexDynamicParam` | ? parameter: `int index` |
| `RexRangeRef` | Reference to a range of input fields (used internally during SqlToRelConverter) |
| `RexPatternFieldRef` | Reference to a field within a MATCH_RECOGNIZE pattern |
| `RexLocalRef` | Reference to another expression in a `RexProgram` (used inside Calc nodes) |

### RexBuilder (org.apache.calcite.rex.RexBuilder)

Factory for constructing `RexNode`s. Always use `RexBuilder` — it enforces type consistency and interning:
```java
rexBuilder.makeCall(SqlStdOperatorTable.PLUS, a, b);
rexBuilder.makeInputRef(rowType, index);
rexBuilder.makeLiteral(42, intType, SqlTypeName.INTEGER);
rexBuilder.makeCast(targetType, expr);
rexBuilder.makeFieldAccess(expr, fieldName, caseSensitive);
rexBuilder.makeOver(type, aggOp, operands, partitionKeys, orderKeys,
                    lowerBound, upperBound, physical, allowPartial, distinct, ignoreNulls);
rexBuilder.makeCorrel(type, correlId);
```

### RexShuttle / RexVisitor

`RexShuttle` is a rewriting visitor (returns new `RexNode`). `visitCall()` by default recurses and reconstructs. Used for: `RexInputRef` remapping (after project pushdown), `RexCorrelVariable` substitution (decorrelation), literal folding.

`RexVisitor<R>` is a pure visitor (no rewriting). `RexVisitorImpl` provides default no-op implementations.

### RexUtil

`RexUtil.toCnf(rexBuilder, node)` — convert to conjunctive normal form (AND of ORs).
`toDnf` — disjunctive normal form.
`pullFactors` — factor AND out of OR.
`composeConjunction(rexBuilder, exprs)` — AND together a list.
`composeDisjunction(rexBuilder, exprs)` — OR together a list.
`swapTableReferences` — remap column indices.
`isLosslessCast` — whether a CAST loses information.

### RexSimplify (org.apache.calcite.rex.RexSimplify)

Folds and simplifies expressions:
- Constant folding: `1 + 2 → 3`, `TRUE AND x → x`, `FALSE AND x → FALSE`.
- Null handling: `x + NULL → NULL` (for null-propagating ops).
- Range simplification: `x > 3 AND x > 5 → x > 5`.
- Redundancy: `x = x → TRUE` (when x is not nullable).
- `simplify(rex, unknownAs)` — `unknownAs` controls NULL semantics (for filter context vs. other contexts).
- `SARG` (Search ARGument): IN-lists and OR-of-ranges are collapsed into a single `SARG` literal for efficient range evaluation.

### RexOver (Window Functions)

```java
class RexOver extends RexCall {
  SqlAggFunction  aggOp;     // SUM, ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, ...
  RexWindow       window;
}

class RexWindow {
  ImmutableList<RexNode>           partitionKeys;
  ImmutableList<RexFieldCollation> orderKeys;
  RexWindowBound                   lowerBound;  // UNBOUNDED PRECEDING / CURRENT ROW / N PRECEDING
  RexWindowBound                   upperBound;
  boolean                          isRows;      // ROWS vs RANGE mode
}
```

---

## Type System

### RelDataType and RelDataTypeFactory (org.apache.calcite.rel.type)

```
RelDataType
├── BasicSqlType       — scalar SQL types (int, varchar, decimal, ...)
├── ArraySqlType       — ARRAY<elementType>
├── MapSqlType         — MAP<keyType, valueType>
├── MultisetSqlType    — MULTISET<elementType>
└── RelRecordType      — ROW(fields)  — field = (name, type) pair
```

`RelDataTypeFactory` creates all types. `SqlTypeFactoryImpl` is the standard implementation; `JavaTypeFactoryImpl` extends it to also map Java classes to SQL types (for JDBC and Enumerable convention). `TypeSystem` (pluggable) sets coercion rules, max precision/scale for DECIMAL.

**Nullability:** every `RelDataType` carries `isNullable()`. Two types with same SqlTypeName but different nullability are distinct (`INT NOT NULL` ≠ `INT`). `RelDataTypeFactory.createTypeWithNullability(type, nullable)` wraps/unwraps. Validators infer nullability from: outer-join output columns (nullable), aggregate COUNT (NOT NULL), literal NULL (nullable).

### SqlTypeName Enum

```
BOOLEAN, TINYINT, SMALLINT, INTEGER, BIGINT, DECIMAL, FLOAT, REAL, DOUBLE,
DATE, TIME, TIME_WITH_LOCAL_TIME_ZONE, TIMESTAMP, TIMESTAMP_WITH_LOCAL_TIME_ZONE,
INTERVAL_YEAR, INTERVAL_YEAR_MONTH, INTERVAL_MONTH,
INTERVAL_DAY, ..., INTERVAL_SECOND,
CHAR, VARCHAR, BINARY, VARBINARY,
NULL, ANY, SYMBOL, MULTISET, ARRAY, MAP, DISTINCT, STRUCTURED, ROW, OTHER,
CURSOR, COLUMN_LIST, DYNAMIC_STAR, GEOMETRY, SARG
```

`SARG` — "Search ARGument" compact form of OR-of-ranges created by `RexSimplify` for IN-list optimization.

---

## Trait System

Every `RelNode` carries a `RelTraitSet` — an ordered list of `RelTrait` values, one per `RelTraitDef`. Traits encode physical properties that the execution plan must satisfy.

### ConventionTraitDef (org.apache.calcite.plan.ConventionTraitDef)

The "calling convention" — how data flows between operators:
- `Convention.NONE` — logical plan (no physical convention; planners start here)
- `EnumerableConvention.INSTANCE` — Java Iterable execution via Linq4j
- `BindableConvention.INSTANCE` — runtime-interpreted execution (for testing)
- Adapter-specific: `JdbcConvention`, `DruidConvention`, `ElasticsearchConvention`, etc.

Convention is the primary trait. The planner satisfies convention by applying converter rules that insert physical operators.

### RelCollationTraitDef (org.apache.calcite.rel.RelCollations)

Sort order. A `RelCollation` is an ordered list of `RelFieldCollation`:
```java
class RelFieldCollation {
  int            fieldIndex;    // column index
  Direction      direction;     // ASCENDING, DESCENDING, STRICTLY_ASCENDING, STRICTLY_DESCENDING, CLUSTERED
  NullDirection  nullDirection; // FIRST, LAST, UNSPECIFIED
}
```

`RelCollations.EMPTY` = unsorted. Collation is produced by `Sort` operators and consumed by `MergeJoin`, streaming aggregation, `TopN`.

### RelDistributionTraitDef (org.apache.calcite.rel.RelDistributions)

Data distribution across partitions:
- `SINGLETON` — single partition
- `BROADCAST_DISTRIBUTED` — full copy at each partition
- `HASH_DISTRIBUTED(keys)` — hash-partitioned on key columns
- `RANGE_DISTRIBUTED(keys)` — range-partitioned
- `RANDOM_DISTRIBUTED` — random (no specific guarantee)
- `ANY` — wildcard in matching

Used by Flink and distributed engines to plan exchanges (repartitioning operators).

### AbstractConverter and Enforcers

When a `RelSubset` must satisfy a trait it doesn't have, VolcanoPlanner inserts an `AbstractConverter`. Converter rules (`ConverterRule`) match `AbstractConverter` and replace it with a concrete operator (e.g., `Sort` to satisfy collation, `Exchange` to satisfy distribution, `ToEnumerableConverter` to satisfy `EnumerableConvention`).

```java
class SortToEnumerableSortRule extends ConverterRule {
  // matches: Sort with Convention.NONE
  // produces: EnumerableSort with EnumerableConvention
}
```

---

## VolcanoPlanner (Cost-Based, Volcano/Cascades Model)

`org.apache.calcite.plan.volcano.VolcanoPlanner`

Based on Graefe's Volcano optimizer (ICDE 1993) and Cascades (IEEE DE Bulletin 1995). Uses a **memo** of equivalence classes.

### Memo: RelSet and RelSubset

```
RelSet          — equivalence class of logically equivalent RelNodes
├── List<RelNode> rels              — all physical/logical alternatives found
├── List<RelSubset> subsets         — one per (RelSet, RelTraitSet) combination
└── RelNode best                    — cheapest physical plan found so far

RelSubset       — (RelSet, RelTraitSet) pair
├── RelSet set
├── RelTraitSet traitSet            — required traits for this subset
├── RelNode best                    — best plan satisfying these traits
├── RelOptCost bestCost
└── List<AbstractConverter> abstractConverters  — pending conversions
```

Each `RelNode` belongs to exactly one `RelSet`. When a new `RelNode` is registered (`VolcanoPlanner.register()`), Calcite computes its digest and merges into an existing `RelSet` if an equivalent already exists.

### Rule Application and RuleQueue

`VolcanoPlanner` maintains a `RuleQueue` (priority queue of `VolcanoRuleMatch` records). A `VolcanoRuleMatch` is a (rule, binding of RelNodes to operands) pair. Planner loop:

1. Dequeue highest-priority match.
2. Call `RelOptRule.onMatch(VolcanoRuleCall)`.
3. `VolcanoRuleCall.transformTo(newRel)` registers the new RelNode.
4. New RelNode triggers new rule matches via `VolcanoPlanner.fireRules()`.
5. Loop until queue empty or cost converges.

**Importance** drives priority: subsets closer to the root (more important to query result) get rules fired first. Importance of a subset = max importance of its `AbstractConverter`s and parent subsets. This guides rule firing toward improving the critical path.

### RelOptRule and Operand Patterns

```java
class RelOptRule {
  RelOptRuleOperand operand;        // pattern tree
  abstract void onMatch(RelOptRuleCall call);
}

class RelOptRuleOperand {
  Class<? extends RelNode> clazz;
  RelTrait                 trait;       // optional trait filter
  Predicate<RelNode>       predicate;
  List<RelOptRuleOperand>  children;
  // matching modes: UNORDERED, EXACT, PLUS, ANY
}
```

Pattern construction examples:
```java
// Filter on top of TableScan:
operand(LogicalFilter.class, Convention.NONE,
  operand(LogicalTableScan.class, none()))

// Project on top of anything:
operand(LogicalProject.class, Convention.NONE, any())

// Join with two inputs:
operand(LogicalJoin.class, Convention.NONE,
  operand(RelNode.class, any()),
  operand(RelNode.class, any()))
```

`VolcanoRuleCall.rel(0)` gets root matched node; `rel(1)` gets first child; indices follow pre-order of operand tree.

### Cost Model

```java
interface RelOptCost {
  double getRows();
  double getCpu();
  double getIo();
  boolean isInfinite();
  boolean isLe(RelOptCost other);   // ≤
  boolean isLt(RelOptCost other);   // <
  RelOptCost plus(RelOptCost other);
  RelOptCost multiplyBy(double factor);
  double divideBy(RelOptCost other);
}
```

Default `VolcanoCost` (triple of rows, cpu, io). `computeSelfCost()` returns operator's own cost excluding children. Total cost = selfCost + sum(childCosts). Best plan per subset = minimum total cost.

### Phase Control and Composition

`VolcanoPlanner.addRelTraitDef()` registers trait definitions. `addRule()` registers rules. Rule sets can be enabled/disabled per phase. Standard composition via `Programs.standard()`:
1. HepPlanner for subquery decorrelation.
2. HepPlanner for logical normalization (filter pushdown, project merge).
3. VolcanoPlanner for physical convention selection and join ordering.

### Top-Down Search Mode (Cascades)

`VolcanoPlanner.setTopDownOpt(true)` (added Calcite 1.27) switches to Cascades-style top-down search: starts from root, recurses into children only when needed for cost computation. Avoids exploring irrelevant subplans. Standard bottom-up search fires rules for all subplans regardless.

### Infinite Loop Prevention

`VolcanoPlanner` limits rule firings via `ruleCallStack` depth and `AbstractConverter` cycle detection. `RuleQueue` tracks `VolcanoRuleMatch` objects by (rule + bindings) and won't re-fire the same match if it produced the same result.

---

## HepPlanner (Heuristic Planner)

`org.apache.calcite.plan.hep.HepPlanner`

Applies rules in a fixed sequence without cost model. Used for normalization passes where rule order matters. Simpler and faster than VolcanoPlanner for normalization.

### HepProgram

```java
HepProgram program = HepProgram.builder()
  .addRuleInstance(FilterJoinRule.FILTER_ON_JOIN)
  .addRuleCollection(ImmutableList.of(
    ProjectMergeRule.INSTANCE,
    FilterMergeRule.INSTANCE))
  .addMatchLimit(20)
  .addMatchOrder(HepMatchOrder.BOTTOM_UP)
  .build();
```

`HepInstruction` types:
- `RuleInstance` — apply one specific rule
- `RuleCollection` — apply a set of rules (single pass or to fixpoint)
- `RuleClass` — apply all rules of a given class
- `MatchLimit` — cap total rule firings
- `MatchOrder` — traversal: `ARBITRARY`, `BOTTOM_UP` (leaves first), `TOP_DOWN` (root first), `DEPTH_FIRST`
- `SubProgram` — nest a sub-program (runs to fixpoint before continuing)
- `ConvergenceLoop` — repeat entire program until no more changes

HepPlanner keeps a directed graph (`HepRelGraph`) of the current plan; `HepRelVertex` wraps each `RelNode`. When a rule fires and produces a new node, the old node is replaced. No memo — single canonical plan at any point.

**HepPlanner vs VolcanoPlanner:**
- HepPlanner: normalization (filter pushdown, constant folding, decorrelation) where rule order matters, cost doesn't.
- VolcanoPlanner: join ordering, physical convention selection where exploring alternatives matters.
- Flink: HepPlanner for logical rewriting → VolcanoPlanner for physical planning.

---

## Rules Engine: Built-In Rule Families

### Filter Rules

| Rule | What it does |
|---|---|
| `FilterJoinRule` | Push filter predicates through a join; predicates referencing only one side become that side's filter; join predicates become join conditions |
| `FilterProjectTransposeRule` | Push filter below project when it references only simple input refs or deterministic project outputs |
| `FilterMergeRule` | Merge two adjacent `Filter` nodes into one AND |
| `FilterAggregateTransposeRule` | Push filter below aggregate when predicates reference only grouping columns |
| `FilterTableScanRule` | Push filter into `TableScan` as a `SubfieldFilter` (adapter-specific) |
| `FilterCorrelateRule` | Push filter below `Correlate` when safe |

### Project Rules

| Rule | What it does |
|---|---|
| `ProjectMergeRule` | Collapse two adjacent `Project` nodes by composing their expression lists |
| `ProjectFilterTransposeRule` | Move project below filter when filter's expressions are covered by project outputs |
| `ProjectRemoveRule` | Remove identity `Project` (all outputs = input refs in order) |
| `ProjectJoinTransposeRule` | Push project through join, reducing each side's row width |
| `ProjectToCalcRule` | Convert `Project` + optional `Filter` into `Calc` node (for Enumerable backend) |
| `ProjectSetOpTransposeRule` | Push project through `UNION`/`INTERSECT`/`EXCEPT` |

### Join Rules

| Rule | What it does |
|---|---|
| `JoinCommuteRule` | Swap left and right inputs (commutativity) |
| `JoinAssociateRule` | Reorder ((A ⋈ B) ⋈ C) → (A ⋈ (B ⋈ C)) (associativity) |
| `JoinPushThroughJoinRule` | Push one join through another (bushy plan exploration) |
| `JoinToMultiJoinRule` | Collect adjacent inner joins into `MultiJoin` for join reordering |
| `LoptOptimizeJoinRule` | Optimize `MultiJoin` using left-deep dynamic-programming algorithm |
| `MultiJoinOptimizeBushyRule` | Optimize `MultiJoin` allowing bushy plans (Selinger-style DP) |

### Aggregation Rules

| Rule | What it does |
|---|---|
| `AggregateProjectMergeRule` | Push `Aggregate` through `Project` when grouping key columns are simple refs |
| `AggregateJoinTransposeRule` | Push aggregate below join when agg + grouping key touch only one join side |
| `AggregateMergeRule` | Merge two adjacent `Aggregate` nodes |
| `AggregateRemoveRule` | Remove `Aggregate` when grouping set = unique key (no aggregation needed) |
| `AggregateExpandDistinctAggregatesRule` | Rewrite `COUNT(DISTINCT x)` into `GROUP BY` + `COUNT` subquery |
| `AggregateUnionTransposeRule` | Push `Aggregate` through `Union` (partial aggs + merge agg) |

### Subquery / Decorrelation Rules

`SubQueryRemoveRule` rewrites scalar subqueries and `IN`/`EXISTS`/`NOT IN`/`NOT EXISTS` into joins + aggregations:
- Scalar subquery → `LogicalCorrelate` → `SINGLE_JOIN` (error if > 1 row)
- `IN (subquery)` → `LogicalCorrelate` → semi-join
- `EXISTS (subquery)` → `LogicalCorrelate` → semi-join
- `NOT IN` / `NOT EXISTS` → anti-join

**Decorrelation** (`RelDecorrelator.decorrelate(root)`): after `SubQueryRemoveRule` creates `LogicalCorrelate` nodes, `RelDecorrelator` rewrites them into standard joins:
1. Identify all `LogicalCorrelate` nodes.
2. For each: analyze what the inner side uses from the outer via `CorrelationId`.
3. Rewrite inner side to accept those columns as regular inputs (append to inner row type).
4. Replace `LogicalCorrelate` with `LogicalJoin` (correlated variables become join conditions).

This is Galindo-Legaria & Joshi's "Orthogonal Optimization of Subqueries and Aggregation" (SIGMOD 2001).

---

## Metadata Framework

`RelMetadataQuery` (`org.apache.calcite.rel.metadata.RelMetadataQuery`) is the API for querying statistics from a plan node. Accessed via `rel.getCluster().getMetadataQuery()`.

### Key Metadata Handlers

| Handler | Method | Returns |
|---|---|---|
| `RelMdRowCount` | `getRowCount(rel, mq)` | Estimated output row count |
| `RelMdSelectivity` | `getSelectivity(rel, mq, predicate)` | Fraction of rows matching predicate ∈ (0, 1] |
| `RelMdDistinctRowCount` | `getDistinctRowCount(rel, mq, groupKey, predicate)` | NDV for a set of columns |
| `RelMdColumnUniqueness` | `areColumnsUnique(rel, mq, cols, ignoreNulls)` | Whether cols form a unique key |
| `RelMdMaxRowCount` | `getMaxRowCount(rel, mq)` | Upper bound on row count |
| `RelMdMinRowCount` | `getMinRowCount(rel, mq)` | Lower bound |
| `RelMdSize` | `averageRowSize` / `averageColumnSizes` | Byte widths |
| `RelMdCollation` | `collations(rel, mq)` | Sort orders this operator preserves |
| `RelMdDistribution` | `distribution(rel, mq)` | Data distribution |
| `RelMdPredicates` | `getPredicates(rel, mq)` | Predicates known to hold on output |

### RelMetadataProvider and Handler Dispatch

`JaninoRelMetadataProvider` (the default) generates Java code at first use for each (RelNode class, metadata type) pair and compiles with Janino. Generated code dispatches to the correct `RelMdXxx` handler. Multiple providers chained via `ChainedRelMetadataProvider`.

Third-party statistics plug in by implementing a `RelMdRowCount`-style handler for their specific `TableScan` subclass.

### Row Count Propagation

- `LogicalTableScan`: asks `RelOptTable.getRowCount()` → from table statistics or user hint.
- `LogicalFilter`: `getRowCount(input) * getSelectivity(filter, predicate)`.
- `LogicalJoin` (inner): `getRowCount(left) * getRowCount(right) * getSelectivity(join, condition)`. Default selectivity for `=` on unique key → `1 / max(NDV(left.key), NDV(right.key))`; range → `0.5`; no stats → `0.25`.
- `LogicalAggregate`: `getDistinctRowCount(input, groupSet)`.
- `LogicalUnion ALL`: `sum(getRowCount(inputs))`. UNION: `0.5 * sum(...)`.
- `LogicalSort` with `fetch`: `min(getRowCount(input), fetch)`.

### RelMdUtil Default Selectivities

`RelMdUtil.guessSelectivity(predicate)` heuristics when statistics absent:
- `=` → 0.15
- `<` / `>` / `<=` / `>=` → 0.5
- `!=` → 0.9
- `IS NULL` → 0.1
- `IN (k items)` → `min(0.5, k * 0.15)`
- `LIKE '%x%'` → 0.1
- `LIKE 'x%'` → 0.2
- `AND` → product; `OR` → inclusion-exclusion

---

## Enumerable Convention and Code Generation

### EnumerableRel Interface (org.apache.calcite.adapter.enumerable)

```java
interface EnumerableRel extends RelNode {
  Result implement(EnumerableRelImplementor implementor, Prefer pref);
}

class Result {
  Expression block;      // Java block expression (Linq4j Expression tree)
  PhysType   physType;   // tuple format: ARRAY, CUSTOM, LIST, SCALAR
}
```

Each `Enumerable*` node generates a Linq4j `Expression` tree representing its execution logic. The root node's `implement()` is called; it recursively calls children's `implement()`, composing the full Java code tree.

### Linq4j (org.apache.calcite.linq4j)

Java port of LINQ from C# (.NET). Provides:
- `Enumerable<T>` — lazy sequence (extends `Iterable<T>`)
- `Queryable<T>` — composable query (LINQ-style)
- `Expression` — Java AST nodes (Calcite's own AST, not java.lang.reflect): `BlockStatement`, `MethodCallExpression`, `LambdaExpression`, `NewExpression`, `BinaryExpression`, etc.
- `Expressions` factory: `Expressions.call(method, args)`, `Expressions.lambda(body, params)`, `Expressions.new_(clazz, args)`, etc.

The generated `Expression` tree is converted to Java source via `Expressions.toString()` (pretty-printer) then compiled by Janino.

Reference: Meijer, Beckman, Bierman, "LINQ: Reconciling Object, Relations and XML in the .NET Framework." SIGMOD Record 2006.

### Janino Runtime Compilation

`ClassBodyDeclaration` → Janino `SimpleCompiler` → `Class<?>` loaded into JVM. Calcite compiles the entire query plan into a single Java class per query. Queries cached in `CalciteMetaImpl.prepareStatementCache` by SQL text + param types.

### EnumerableHashJoin Code Generation

`EnumerableHashJoin.implement()` generates (pseudocode of generated Java):
```java
Enumerable<TLeft> left = leftInput;
Enumerable<TRight> right = rightInput;
Lookup<K, TRight> lookup = right.toLookup(rightKeySelector);

return left.join(
  lookup,
  leftKeySelector,
  resultSelector);   // emits (leftRow, rightRow) pairs
```

Selectors are lambda expressions generated from the join key expressions.

### EnumerableAggregate

Groups via `Grouping.ofGroupSets(...)` (uses `HashMap<K, Accumulator>`). Generated code: iterate input, extract group key, find or create accumulator, call `accumulator.add(row)`, at end iterate map calling `accumulator.result()`.

### EnumerableProject / EnumerableCalc

`EnumerableProject.implement()` generates a `select` (map) lambda over the input:
```java
return input.select(row -> new Object[] {
  row[fieldIndex0],
  row[fieldIndex0] + row[fieldIndex1],   // computed expression
});
```

`EnumerableCalc` (combined project+filter via `RexProgram`):
```java
return input
  .where(row -> condition(row))   // filter
  .select(row -> project(row));   // project
```

### EnumerableInterpreter

For operators without `EnumerableRel` implementations, `EnumerableInterpreter` wraps a `BindableRel` subtree with a `Compiler`/`Interpreter` (row-at-a-time execution of a simple bytecode-like interpreter). Used as fallback and for testing.

### EnumerableMergeJoin / EnumerableNestedLoopJoin

`EnumerableMergeJoin`: both inputs sorted on join keys; generates merge scan. `EnumerableNestedLoopJoin`: nested loops (for non-equi joins or small inputs).

---

## Adapter / Convention System

### Writing a Calcite Adapter

1. Implement `Schema` → `Table` → `RelOptTable` → register in `SchemaPlus`.
2. Implement `ScannableTable` (full scan), `FilterableTable` (scan + predicate), or `ProjectableFilterableTable` (scan + predicate + column pruning).
3. Optionally implement `TranslatableTable` to return a custom `RelNode` from `toRel()` — allows the adapter to produce a convention-specific scan node.
4. Register adapter-specific rules that convert `LogicalFilter`/`LogicalProject`/`LogicalAggregate` above the adapter's scan into a single combined adapter scan (pushdown).

### JdbcAdapter (org.apache.calcite.adapter.jdbc)

`JdbcSchema` wraps a JDBC `DataSource`. `JdbcTable` implements `TranslatableTable` → `JdbcTableScan`. `JdbcRules` convert logical nodes to `JdbcRel` nodes:
- `JdbcProjectRule`, `JdbcFilterRule`, `JdbcJoinRule`, `JdbcAggregateRule`, `JdbcSortRule`

**SQL generation:** `JdbcImplementor.visitRoot(root)` walks the `JdbcRel` tree. `RexToSqlNodeConverter` converts `RexNode` expressions back to `SqlNode` AST; `SqlDialect.unparse()` renders to SQL string. Dialects (`MySqlDialect`, `PostgresqlDialect`, `HiveSqlDialect`, etc.) override unparse for DB-specific syntax. Generated SQL sent to JDBC `DataSource` via `JdbcUtils.ObjectArrayStatementFactory`.

### Other Adapters

| Adapter | Key approach |
|---|---|
| `DruidAdapter` | Translates Filter+Project+Aggregate+Sort on a Druid table into Druid JSON query (GroupBy, TopN, Timeseries) |
| `ElasticsearchAdapter` | Translates Filter+Project+Aggregate into ES Query DSL JSON |
| `FileAdapter` | CSV/JSON/XLSX reading via `CsvTable`; schema inferred from headers |
| `CassandraAdapter` | Pushes Filter+Sort into CQL; no aggregation pushdown |
| `ArrowAdapter` | Reads Arrow files; produces `ArrowEnumerable` |

---

## SqlToRelConverter

`org.apache.calcite.sql2rel.SqlToRelConverter` converts the validated `SqlNode` AST to a logical `RelNode` tree.

Key conversions:
- `convertSelect(SqlSelect)` → `LogicalProject(LogicalFilter(from, where), selectList)`
- `convertFrom(SqlNode from)` → `TableScan` / `Join` / subquery RelNode
- `convertJoin(SqlJoin)` → `LogicalJoin` (condition becomes `RexCall`)
- `convertGroupBy(...)` → `LogicalAggregate`
- `convertOrderBy(...)` → `LogicalSort`
- `convertSubQuery(SqlCall)` → `RexSubQuery` (later converted by `SubQueryRemoveRule`)

**Correlated variables:** when a subquery references an outer query column, `SqlToRelConverter` creates a `CorrelationId` and wraps the subquery in `LogicalCorrelate`.

### RelBuilder (org.apache.calcite.tools.RelBuilder)

Higher-level fluent API for building `RelNode` trees programmatically:
```java
RelBuilder builder = RelBuilder.create(config);
RelNode plan = builder
  .scan("ORDERS")
  .filter(builder.call(SqlStdOperatorTable.EQUALS,
                       builder.field("STATUS"), builder.literal("OPEN")))
  .aggregate(builder.groupKey("CUSTOMER_ID"),
             builder.sum(false, "TOTAL", builder.field("AMOUNT")))
  .build();
```

---

## Materialized View Rewriting

### AbstractMaterializedViewRule (org.apache.calcite.rel.rules.materialize)

Two MV rewriting rule families:
1. `MaterializedViewProjectFilterRule` — Project+Filter queries.
2. `MaterializedViewAggregateRule` — Project+Filter+Aggregate queries.

**Matching algorithm** for `MaterializedViewAggregateRule.perform()`:
1. Extract query's table references, filter predicates, grouping columns, aggregate calls.
2. For each candidate MV:
   a. Check MV's tables ⊇ query's tables.
   b. Check MV's filter is implied by query's filter (or compensated with additional filter on MV scan).
   c. Check MV's grouping key ⊆ query's grouping key (query can re-aggregate over MV's pre-aggregated groups).
   d. Check MV provides all aggregate functions needed (or they can be computed from MV columns, e.g., `SUM` from a `SUM` MV column).
3. If matching: build new plan → `MV scan → Filter (compensation) → Project → Aggregate (re-aggregation if needed)`.

### Lattice and TileOptimizer

A `Lattice` (`org.apache.calcite.materialize.Lattice`) models a pre-aggregated star schema:
- Defines the join graph (fact table + dimension tables + join conditions).
- Each "tile" is a specific combination of (grouping set, aggregate functions).
- `TileOptimizer` selects which tiles to materialize based on query workload (lattice covering / benefit analysis).

`MaterializationService` manages materialization lifecycle. `CalciteSchema` attaches `Materialization` objects to schemas.

---

## Streaming and Flink Integration

### Flink SQL Architecture

Flink uses Calcite for all SQL. Maintains a fork of some Calcite internals but contributes heavily upstream.

```
Flink SQL string
    │
    ▼ FlinkSqlParserImpl (extends Calcite parser with Flink extensions)
FlinkSqlNode AST
    │
    ▼ FlinkSqlValidator → PlannerContext → FlinkRelBuilder
Logical RelNode (Convention.NONE)
    │
    ▼ HepPlanner (FlinkBatchPrograms.LOGICAL / FlinkStreamPrograms.LOGICAL)
    │  Rules: subquery decorrelation, filter pushdown, join reordering
    ▼ VolcanoPlanner (FlinkBatchPrograms.PHYSICAL / FlinkStreamPrograms.PHYSICAL)
    │  Rules: FlinkLogicalXxx → FlinkPhysicalXxx
    │         select ExecEdge shuffle types (HASH, BROADCAST, FORWARD, etc.)
    ▼ ExecNode graph → ExecNodePlan
    │
    ▼ ExecNode.translateToPlan() → DataStream transformations
Flink Job Graph
```

### Streaming-Specific RelNodes

Flink adds:
- `StreamPhysicalCalc`, `StreamPhysicalJoin`, `StreamPhysicalGroupAggregate`, `StreamPhysicalWindowAggregate`
- `StreamPhysicalTemporalJoin` — joins a stream with a versioned table
- `StreamPhysicalWindowTableFunction` — TUMBLE/HOP/CUMULATE/SESSION table-valued functions
- `StreamPhysicalSort` with `inputChangelogMode`
- `StreamPhysicalExchange` — inserts shuffle with `InputProperty.DistributionType`

### Temporal Table Joins (FOR SYSTEM_TIME AS OF)

```sql
SELECT * FROM orders o
JOIN rates FOR SYSTEM_TIME AS OF o.event_time AS r
  ON o.currency = r.currency
```

Calcite models this as `LogicalCorrelate` with a temporal `Snapshot` over the right side. Flink converts to `StreamPhysicalTemporalJoin` using a state backend lookup at the event time.

### Changelog Mode / Retraction

Streaming aggregation must handle retractions (row deletions from upstream changes). Flink's physical planner decides:
- `StreamPhysicalGroupAggregate` (with retraction input → emit retractions downstream)
- `StreamPhysicalWindowAggregate` (no retraction needed for closed windows)

Tracked via `InputChangelogMode` (INSERT_ONLY, ALL: INSERT+UPDATE_BEFORE+UPDATE_AFTER+DELETE) propagated through the physical plan.

---

## Key Papers and References

| Paper | Venue | Year |
|---|---|---|
| Begoli, Camacho-Rodríguez, Hyde, Mior, Lemire, "Apache Calcite: A Foundational Framework for Optimized Query Processing Over Heterogeneous Data Sources" | SIGMOD | 2018 |
| Graefe, "The Volcano Optimizer Generator: Extensibility and Efficient Search" | ICDE | 1993 |
| Graefe, McKenna, "The Cascades Framework for Query Optimization" | IEEE DE Bulletin | 1995 |
| Selinger, Astrahan, Chamberlin, Lorie, Price, "Access Path Selection in a Relational Database Management System" | SIGMOD | 1979 |
| Galindo-Legaria, Joshi, "Orthogonal Optimization of Subqueries and Aggregation" | SIGMOD | 2001 |
| Moerkotte, Neumann, "Analysis of Two Existing and One New Dynamic Programming Algorithm for the Generation of Optimal Bushy Join Trees Without Cross Products" | VLDB | 2006 |
| Neumann, Radke, "Adaptive Optimization of Very Large Join Queries" | SIGMOD | 2018 |
| Meijer, Beckman, Bierman, "LINQ: Reconciling Object, Relations and XML in the .NET Framework" | SIGMOD Record | 2006 |
| Begoli et al., "One SQL to Rule Them All" | CIDR | 2019 |

**Online sources:**
- Calcite documentation: https://calcite.apache.org/docs/
- Calcite source: https://github.com/apache/calcite
- Flink SQL architecture: https://nightlies.apache.org/flink/flink-docs-stable/docs/dev/table/sql/
