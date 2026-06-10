# Spec: 阶段 1.3 — bge-small-zh Embedding 接入 + 语义去重

## Background
当前 `raw_articles.embedding` / `events.centroid` 仍是 1024 维占位（bge-large 量级），但实际从未填值。
[clean.py](file:///Users/bytedance/liu/isYOU/content_engine/stages/clean.py) 仅做 SimHash 字面去重（汉明距离≤3），无法识别**语义相似但用词不同**的转载/复述（例如同一新闻被两家媒体改写）。
[cluster.py](file:///Users/bytedance/liu/isYOU/content_engine/stages/cluster.py) 仍用 Jaccard，与方案 §5.2 要求的「Embedding 余弦相似度」存在差距。

## Goal
1. 引入 **bge-small-zh-v1.5（512 维，本地推理）** 作为默认 Embedding provider；
2. schema 从 1024 维迁到 512 维；
3. 历史 75 条文章批量回填 embedding；
4. 在 clean 阶段（SimHash 通过后）追加**语义去重**（cos ≥ 0.92 判重，72h 时间窗）；
5. EmbeddingService 设计成 provider 接口，方便后续切远程 API。

> 阶段 2.3 的 cluster 替换为 cos≥0.86 仍属下一个 Spec 范围，**本 Spec 不做 cluster 替换**，仅打好地基（保证文章入库都有 embedding）。

## Scope (in / out)
**In Scope**
- 新增 `content_engine/services/embedding.py`：`EmbeddingProvider` 抽象 + `LocalBgeProvider` 本地实现 + 模块级 `embed_texts()` 工厂
- 新增 `content_engine/stages/embed.py`：单独阶段，对 `status=cleaned` 的 RawArticle 批量生成 embedding（成功后状态保持 cleaned，仅 embedding 列写入；不改变状态机；后续 classify 阶段照常推进）
- 新增 alembic 0006 迁移：将 `raw_articles.embedding` 与 `events.centroid` 从 `Vector(1024)` 改成 `Vector(512)`（先 drop 列再加列，因为现有列均为 NULL，无数据损失）
- 修改 `content_engine/models/schema.py`：`EMBEDDING_DIM = 512`；本地新增 `Embedding(dim=512)`
- 修改 `content_engine/stages/clean.py`：在 SimHash 通过后追加语义去重（在已 cleaned 的同窗文章中找 cos≥0.92，命中即 dropped）
  - 注意：clean 时 article 还没 embedding；先生成 embedding 再做 cos 比对（提前 embed 一条，避免双阶段）
- 配置：`Settings` 加 `embedding` 子模块（model_name / dim / device / batch_size + provider 切换）
- 单元测试：
  - `test_embedding_service.py`：mock provider 测 service 接口契约；本地 provider 跳过（避免 CI 下载模型）
  - `test_clean_semantic_dedup.py`：用 mock provider 直接喂向量，验证 cos≥0.92 命中走 dropped

**Out of Scope**
- 远程 OpenAI 兼容 Embedding API provider（用户已表示「后续切」，此 Spec 仅保留接口位）
- cluster.py 的 Jaccard → cos 替换（属阶段 2.3）
- HDBSCAN 离线复核（属阶段 2.4）
- 信源扩展到 40+（属阶段 1.6）

## Approach

### A. provider 接口（services/embedding.py）
```python
class EmbeddingProvider(Protocol):
    dim: int
    def encode(self, texts: list[str]) -> list[list[float]]: ...

class LocalBgeProvider:
    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 32):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name, device=device)
        self.dim = self._model.get_sentence_embedding_dimension()
        self._batch = batch_size
    def encode(self, texts): ...

# 工厂：根据 settings.embedding.provider 选 provider
def get_embedding_provider() -> EmbeddingProvider: ...
def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedding_provider().encode(texts)
```

### B. clean.py 改造
顺序：质量门槛 → SimHash → SimHash 命中即 dropped；否则 → 生成 embedding → 在已 cleaned 同窗内 cos 比对 → cos≥0.92 dropped；否则 cleaned。
- 历史 cleaned 文章可能没 embedding（旧数据）：跳过它们参与 cos 比对（向后兼容）
- clean 阶段失败（embed 异常）：留 cleaned 状态但 embedding 为 NULL，记 last_error=f"embed_failed: {e}"，不阻塞流水线

### C. 历史回填（独立小脚本，stages/embed.py 兼任）
- `python -m content_engine.stages.embed` 扫描 `embedding IS NULL AND status IN (cleaned/classified/clustered)`，批量回填
- 同样支持新文章入库后回填

### D. 迁移 0006
```python
def upgrade():
    op.drop_column("raw_articles", "embedding")
    op.drop_column("events", "centroid")
    op.add_column("raw_articles", sa.Column("embedding", Vector(512), nullable=True))
    op.add_column("events", sa.Column("centroid", Vector(512), nullable=True))

def downgrade():
    # 反向：drop 512 → 加 1024
    ...
```

### E. 依赖
- 新增 `sentence-transformers >= 2.7`（会带入 transformers/torch）；首次 `model = SentenceTransformer("BAAI/bge-small-zh-v1.5")` 会去 huggingface 拉权重 ~95MB。

## Acceptance Criteria
- alembic upgrade head 成功，raw_articles.embedding / events.centroid 为 Vector(512)
- `python -m content_engine.stages.embed` 给 75 条历史文章回填 embedding（embedding IS NULL 数为 0）
- pytest 全绿（≥ 27 + 新增）
- clean 阶段：mock 一条 cos=0.95 的近邻文章，新文章被 dropped；mock 一条 cos=0.5 的，仍 cleaned
- EmbeddingProvider 抽象：单测能用 mock provider 注入，不必真跑模型

## Open Questions
（无，关键决策已与用户确认：本地 provider + 一并做迁移/回填/语义去重）
