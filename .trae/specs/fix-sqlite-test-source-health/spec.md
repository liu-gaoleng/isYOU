# 修复 source_health 单测 SQLite 自增主键失败 Spec

## Why
阶段 1.2 新增的 `content_engine/tests/test_source_health.py` 三个用例在 SQLite in-memory 上全部失败，报 `NOT NULL constraint failed: sources.id`。根因是 ORM `IdMixin` 用 `BigInteger primary_key autoincrement=True`，SQLAlchemy 在 SQLite 方言下不会把它编译成 `INTEGER PRIMARY KEY AUTOINCREMENT`，导致 INSERT 时主键不自动填值。在 PG 上 `BIGSERIAL` 行为正确，因此线上不受影响，仅离线单测受阻。

## What Changes
- 在 `IdMixin` 的主键列上启用方言变体：PostgreSQL 仍为 `BigInteger`，SQLite 降级为 `Integer`（保持单测可在 in-memory 跑通，且不影响 PG 行为）。
- 不修改任何业务表结构、不修改 Alembic 迁移、不修改运行时代码。
- **不**回滚或修改测试期望，仅修复底层 ORM 兼容性。

## Impact
- Affected specs: 阶段 1.2 信源健康监控验收
- Affected code:
  - [content_engine/models/base.py](file:///Users/bytedance/liu/isYOU/content_engine/models/base.py)：`IdMixin` 主键定义
- Affected tests:
  - [content_engine/tests/test_source_health.py](file:///Users/bytedance/liu/isYOU/content_engine/tests/test_source_health.py)：3 个用例转绿

## ADDED Requirements

### Requirement: ORM 主键须在 SQLite in-memory 单测环境正确自增
The system SHALL ensure all ORM models with `IdMixin` insert records without explicit `id` value on both PostgreSQL and SQLite backends.

#### Scenario: 在 SQLite in-memory 上 INSERT 不传 id
- **WHEN** 单测使用 `create_engine("sqlite://")` 建表后，ORM `add()` 一条 `Source` 记录而不显式赋值 `id`
- **THEN** SQLAlchemy 应在 SQLite 上自动生成自增主键，`commit()` 不抛 `IntegrityError`，且 `s.refresh(obj)` 能拿到非空 `id`

#### Scenario: 在 PostgreSQL 上保持 BIGSERIAL 行为
- **WHEN** Alembic 迁移建表 + ORM `add()` 一条记录到 PG
- **THEN** 主键类型仍为 `bigint`，自增由 sequence 提供，与既有线上 78 条数据兼容；不需要任何数据迁移

## MODIFIED Requirements

### Requirement: IdMixin 主键定义
`IdMixin.id` 列定义须使用方言变体：默认 `BigInteger`、SQLite 上变体为 `Integer`，二者均启用 `autoincrement=True`。

```
id: Mapped[int] = mapped_column(
    BigInteger().with_variant(Integer, "sqlite"),
    primary_key=True,
    autoincrement=True,
)
```

## REMOVED Requirements
（无）
