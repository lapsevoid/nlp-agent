# 学术检索质量评测

正式套件 ID 为 `academic-routing-v1`，数据位于
`.jbeval/suites/academic-routing-v1/dataset.yaml`。

它复用统一评测运行器，并额外统计：

- `academic_search_recall`：应检索问题实际调用 `academic_search` 的比例；
- `academic_false_positive_rate`：无需工具的问题错误调用学术检索的比例；
- `citation_integrity_rate`：最终学术链接全部存在于本轮工具结果的比例。

先启动 Web、Monitor，再运行小样本：

```powershell
$env:NLP_AGENT_EVALUATION_USERNAME='evaluation-user'
$env:NLP_AGENT_EVALUATION_PASSWORD='<password>'
uv run python -m evaluation validate .jbeval/suites/academic-routing-v1/dataset.yaml
uv run python -m evaluation run academic-routing-v1 --live --limit 2
```

`--live` 会产生模型调用费用；数据集校验和单元测试不调用外部模型。
