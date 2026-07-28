# 参与贡献

感谢你帮助完善发票智能审计助手。

## 本地开发

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff format .
ruff check .
pytest
python scripts/run_eval.py \
  --baseline evals/baseline/baseline_results.json \
  --fail-on-regression
```

## 提交要求

1. 不要提交真实发票、个人信息、企业内部策略或任何密钥。
2. 审计结论必须由可测试的确定性规则产生，模型只能优化说明文字。
3. 改动规则时同步修改知识库、测试用例和评测基线，并在 PR 中说明业务依据。
4. 新增文件格式时补充失败场景测试，不能静默丢弃内容。
5. 提交前运行 `ruff format .`、`ruff check .`、`pytest` 和离线回归。

## 更新评测基线

只有在逐项确认结果符合新规则后，才能运行：

```bash
python scripts/run_eval.py --update-baseline
```

基线不得包含 P0 失败。
