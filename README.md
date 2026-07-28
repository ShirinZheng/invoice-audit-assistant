# 发票智能审计助手

[![CI](https://github.com/ShirinZheng/invoice-audit-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/ShirinZheng/invoice-audit-assistant/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-1f5f46)](LICENSE)

一个本地优先、规则可复现、引用可核验的发票初审项目。它会从发票中提取字段，执行金额、
供应商、预算、重复报销和反舞弊检查，再从本地政策知识库返回对应依据。DeepSeek 是可选的
说明增强层，不能改变审计结论，也不能自行决定企业写操作。

> 项目内的 44 张发票和企业工具数据均为合成数据，不包含真实个人或企业信息。

## 现在可以做什么

- 直接打开中文 Web 界面，选择示例发票、粘贴 OCR 文本或上传文件。
- 默认离线审计，不需要 API Key，不会把发票发送给外部模型。
- 识别中英文 `字段名：值` 文本、文本型 PDF，以及 PNG/JPG/WebP/TIFF 图片 OCR。
- 返回结构化结论：`通过初审`、`补充资料`、`升级审批`、`拒绝报销` 或 `拒绝违规请求`。
- 对每个结论附上本地政策原文引用，并校验引用确实存在于对应文件。
- 可选使用 DeepSeek 生成更自然的中文或英文说明；确定性结果会覆盖模型输出。
- 提供 FastAPI、Swagger 文档、命令行、Docker、64 条离线回归和 GitHub CI。

## 5 分钟启动

需要 Python 3.10 或更高版本。

```bash
git clone https://github.com/ShirinZheng/invoice-audit-assistant.git
cd invoice-audit-assistant

python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

uvicorn src.api.app:app --reload
```

打开：

- Web 界面：<http://127.0.0.1:8000>
- Swagger API：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

Windows PowerShell 激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

### Docker 启动

Docker 镜像已包含中英文 Tesseract OCR：

```bash
cp .env.example .env
docker compose up --build
```

然后访问 <http://127.0.0.1:8000>。运行日志保存在本机 `runs/`，容器重建不会丢失。

## 可选：启用 DeepSeek

离线模式已经包含完整审计能力。只有在希望优化说明文字时才需要 DeepSeek：

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
AUDIT_MODE=offline
DEEPSEEK_API_KEY=你的新密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

Web 页面会在服务端检测到 Key 后开放“使用 DeepSeek 优化说明”开关。浏览器不会接收或保存
Key。若设置 `AUDIT_MODE=deepseek`，Python 调用的默认模式会变为 DeepSeek；Web 请求仍需
明确开启开关。

## 输入格式

确定性字段提取器面向 OCR 归一化后的 `字段名：值` 文本，例如：

```text
增值税普通发票
发票号码：INV-1001
开票日期：2026-07-28
销售方名称：杭州示例科技有限公司
销售方纳税人识别号：91330100TEST000001
项目编号：PRJ-1003
费用类别：vat_general
金额：1000.00
税额：60.00
价税合计：1060.00
币种：CNY
```

支持扩展名：

| 类型 | 扩展名 | 说明 |
|---|---|---|
| 文本 | `.txt` `.md` `.csv` `.json` | UTF-8 或 GB18030 |
| PDF | `.pdf` | 必须有文字层；纯扫描 PDF 请转图片 |
| 图片 | `.png` `.jpg` `.jpeg` `.webp` `.tif` `.tiff` | 通过本机 Tesseract OCR |

非 Docker 环境使用图片 OCR 时，需要先安装系统 Tesseract 及相应语言包。OCR 结果可能有误，
金额、税号和发票号码必须人工复核。

## 命令行

离线审计：

```bash
python scripts/run_assistant.py \
  --invoice data/invoices/raw/INV-0001.txt \
  --query "请审核这张发票是否可以报销"
```

使用 DeepSeek 优化说明：

```bash
python scripts/run_assistant.py \
  --invoice data/invoices/raw/INV-0001.txt \
  --query "请审核这张发票是否可以报销" \
  --deepseek
```

## HTTP API

### 文本审计

```bash
curl -X POST http://127.0.0.1:8000/api/v1/audits \
  -H "Content-Type: application/json" \
  -d '{
    "invoice_text": "MEAL INVOICE\nInvoice ID: INV-9001\nDate: 2026-07-28\nSeller: Demo Restaurant\nSeller Tax ID: TAX-001\nProject ID: PRJ-1003\nAmount: 100.00\nTax: 6.00\nTotal: 106.00",
    "query": "请审核这张发票",
    "use_deepseek": false
  }'
```

### 文件审计

```bash
curl -X POST http://127.0.0.1:8000/api/v1/audits/file \
  -F "file=@invoice.pdf" \
  -F "query=请审核这张发票" \
  -F "use_deepseek=false"
```

默认最大文件为 10 MB，可用 `MAX_UPLOAD_BYTES` 调整。API Key 只能配置在服务端，接口不接收
客户端传入的 Key。

## 审计流程

```text
文本 / PDF / 图片
        │
        ▼
文件解析或 OCR ──► 模板分类与字段提取
                         │
                         ▼
                   违规意图预检查
                    │          │
                 命中          未命中
                    │          ▼
                    │   供应商 / 预算 / 重复检查
                    │          │
                    └────┬─────┘
                         ▼
                   确定性政策决策
                         │
                 ┌───────┴────────┐
                 ▼                ▼
          本地政策 RAG       审批单 / 审计日志
                 │          （由规则决定）
                 └───────┬────────┘
                         ▼
                  离线说明或 DeepSeek
                         │
                         ▼
              覆盖决策字段 + 校验政策引用
```

关键边界：

1. 模型不判断通过或拒绝，结论来自 `src/policy/engine.py`。
2. 模型不能决定创建审批单或写审计日志，工具调用由规则触发。
3. 违规请求在企业工具调用前被拦截。
4. 模型引用只有在原文确实位于检索片段中时才会保留。
5. DeepSeek 不可用时会自动回退到离线说明，审计不会因此中断。

## 当前规则

- 必填字段：发票号码、销售方、开票日期、价税合计。
- `金额 + 税额` 与价税合计误差超过 0.01 时拒绝。
- 餐饮 / 差旅 / 办公用品单张限额分别为 500 / 5000 / 2000 元。
- 单张发票超过 10000 元需要升级审批。
- 黑名单供应商和重复发票拒绝；供应商审核中或预算不足时升级审批。
- 伪造、篡改、倒签、拆票绕过审批、个人费用报销、删除审计记录和重复报销请求会被拒绝。

这些是演示规则，不是中国税法或任一企业的正式制度。接入真实业务时，应把规则和
`data/knowledge_base/` 替换为经过财务、法务和合规评审的版本。

## 测试与评测

```bash
ruff format --check .
ruff check .
pytest

python scripts/run_eval.py \
  --baseline evals/baseline/baseline_results.json \
  --fail-on-regression
```

离线评测覆盖 64 个场景，包含常规审计、引用、黑名单供应商、预算、重复报销、金额不一致、
缺失字段和中英文违规请求。它完全不调用外部 API，因此适合每次提交都在 CI 中运行。

仅在需要评估 DeepSeek 的 JSON 稳定性和说明质量时运行：

```bash
python scripts/run_eval.py --deepseek --limit 5
```

基线的原理、P0 门禁和更新规范见
[EVALUATION_HARNESS_EXPLAINED.md](EVALUATION_HARNESS_EXPLAINED.md)。

## 项目结构

```text
.
├── .github/                 # CI 与 Dependabot
├── data/
│   ├── invoices/raw/        # 44 张合成发票
│   └── knowledge_base/      # 中英文政策知识库
├── evals/
│   ├── cases/               # 64 条评测场景
│   └── baseline/            # 已审核回归基线
├── scripts/                 # CLI、数据生成、索引检查、评测与安全扫描
├── src/
│   ├── api/                 # FastAPI
│   ├── assistant/           # 审计编排
│   ├── invoices/            # 文件读取、分类与字段提取
│   ├── policy/              # 确定性决策规则
│   ├── rag/                 # 安全的本地 TF-IDF 检索
│   ├── llm/                 # 可选 DeepSeek 适配
│   └── tools/               # 合成企业工具
├── tests/                   # 单元、API 与完整离线流程测试
├── web/                     # 无构建步骤的中文前端
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 安全与生产边界

- `.env`、运行轨迹和构建产物不会提交到 Git。
- 默认 LLM trace 只记录内容长度与 SHA-256，不落盘发票正文和完整提示词。
- RAG 不再加载可执行 pickle，而是启动时直接从 Markdown 安全构建。
- Web 接口有文件类型、请求长度、上传大小和路径格式校验。
- 当前企业工具是确定性模拟器，不能直接用于真实付款或报销审批。
- 公网部署前必须增加身份认证、租户隔离、最小权限、速率限制、加密存储和审计留存策略。

漏洞报告与敏感材料处理方式见 [SECURITY.md](SECURITY.md)。贡献规范见
[CONTRIBUTING.md](CONTRIBUTING.md)。

## License

[MIT](LICENSE) © 2026 ShirinZheng
