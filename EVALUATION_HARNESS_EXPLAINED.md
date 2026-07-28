# 评测与回归门禁说明

## 目标

评测回答的是：“规则、知识库、工具或说明层发生变化后，关键审计行为是否仍然正确？”

项目把审计结论和自然语言说明分开：

- 结论由确定性代码产生，可离线、可复现、可逐字段核对。
- DeepSeek 只负责可选的说明文字，不允许改写动作、风险、字段和工具轨迹。
- 引用必须来自本地知识库，且引用内容必须逐字存在于检索片段。

因此，默认回归不需要 API Key，也不会产生模型费用。

## 场景数据

`evals/cases/cases.json` 包含 64 条合成场景。每条包含：

| 字段 | 含义 |
|---|---|
| `case_id` | 稳定且唯一的场景编号 |
| `category` | 常规、引用、供应商、重复、金额、缺字段、升级或违规 |
| `priority` | `P0` 为安全/关键流程，`P1` 为普通质量 |
| `invoice_file` | `data/invoices/raw/` 下的合成发票 |
| `query` | 用户问题，包含中英文请求 |
| `expected_action` | 预期审计动作 |
| `expected_risk_level` | 预期风险等级 |
| `expected_template_type` | 预期发票类别 |
| `expected_fields` | 必须正确提取的字段 |
| `expected_tools` | 必须出现在轨迹中的工具 |
| `requires_citations` | 是否必须返回已校验政策引用 |

评测不比较一整段“标准答案”，避免自然语言表述差异造成虚假失败。

## 执行方式

完整离线回归：

```bash
python scripts/run_eval.py \
  --baseline evals/baseline/baseline_results.json \
  --fail-on-regression
```

缩小范围：

```bash
python scripts/run_eval.py --case-id SENSITIVE-003
python scripts/run_eval.py --category restricted_handling
python scripts/run_eval.py --limit 10
```

中断后继续：

```bash
python scripts/run_eval.py --resume-dir runs/eval-YYYYMMDD-HHMMSS
```

每个场景完成后会立即追加到 `case_results.jsonl`，因此进程中断不会丢失已经完成的记录。

## 评分

`src/evals/graders.py` 逐项检查：

- 动作和风险等级。
- 发票模板与指定字段。
- 必须执行的企业工具。
- 需要引用时是否存在已校验引用。
- 违规场景是否返回 `restricted_operation`。
- 违规场景是否避免创建审批单。

单条场景只有全部检查通过才算 `passed`。部分正确会得到 0 到 1 之间的分数，用于观察渐进
退化；P0 场景任一关键检查失败都会进入 `p0_failures`。

## 回归门禁

当前结果会与 `evals/baseline/baseline_results.json` 比较：

1. 任何 P0 失败都会让门禁失败。
2. 总体平均分下降超过 `EVAL_REGRESSION_THRESHOLD` 时失败。
3. 任一分类分数下降超过同一阈值时失败。

默认阈值为 `0.03`。GitHub Actions 会在每个 Pull Request 和 `main` 推送上执行此门禁。

## DeepSeek 评测

需要时可在同一批场景上检查真实 DeepSeek 的结构化输出：

```bash
DEEPSEEK_API_KEY=你的密钥 \
python scripts/run_eval.py --deepseek --limit 5
```

网络错误或模型输出校验失败时，业务路径会回退到离线说明并添加 warning。由于确定性字段仍然
被系统覆盖，模型不能把拒绝改成通过。不过，DeepSeek 表述质量仍建议人工抽查，不应仅凭结构化
分数判断。

## 更新基线

规则或知识库经过业务评审后，先运行完整评测并检查每条变化，再执行：

```bash
python scripts/run_eval.py --update-baseline
```

更新基线前必须满足：

- 没有 P0 失败。
- 新预期与知识库政策一致。
- 不是为了掩盖代码缺陷而修改预期。
- `scripts/seed_demo_data.py` 能稳定复现发票、知识库和场景文件。

不要在没有审查差异的情况下自动接受新基线。
