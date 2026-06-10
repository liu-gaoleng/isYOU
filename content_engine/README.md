# content_engine

「热读」AI 内容引擎（生产化版本）。

> 演化自 `pipeline_demo/`，目标与里程碑见 [../内容引擎实施计划.md](../内容引擎实施计划.md)，
> 技术方案权威依据见 [../内容管线方案.md](../内容管线方案.md)。

## 目录结构

```
content_engine/
├── stages/           # 6 个管线阶段：collect / clean / classify / cluster / summarize / score
├── models/           # SQLAlchemy 2.0 ORM（对应方案 §9.2 六张表）
├── config/           # pydantic-settings 配置加载层
├── migrations/       # Alembic 迁移脚本
├── tests/            # pytest 单元测试
└── README.md
```

> 注：`pyproject.toml` 在仓库根目录（`../pyproject.toml`），把整个 `content_engine/` 作为一个 package 安装。

## 本地起服务

```bash
# 1. 起 PostgreSQL(pgvector) + Redis（在仓库根目录）
docker compose up -d

# 2. 安装依赖（建议用 venv，在仓库根目录执行）
cd /Users/bytedance/liu/isYOU
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"

# 3. 拷贝并填写 .env
cp .env.example .env

# 4. 跑 Alembic 迁移（cwd 为 content_engine/）
cd content_engine
export DATABASE_URL="postgresql+psycopg://rd:rd@localhost:5432/redu"
alembic upgrade head
```

## 阶段进度

参见 [../内容引擎实施计划.md](../内容引擎实施计划.md)。当前在 **阶段 0：工程化骨架**。
