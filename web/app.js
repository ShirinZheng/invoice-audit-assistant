const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const actionLabels = {
  approve_initial_review: "通过初审",
  require_more_info: "补充资料",
  escalate_approval: "升级审批",
  reject: "拒绝报销",
  restricted_operation: "拒绝违规请求",
};

const riskLabels = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  critical: "严重风险",
};

const typeLabels = {
  vat_special: "增值税专票",
  vat_general: "增值税普票",
  travel: "差旅发票",
  meal: "餐饮发票",
  office_supplies: "办公用品",
  unknown: "未识别",
};

const toolLabels = {
  query_vendor_status: "核验供应商状态",
  check_project_budget: "检查项目预算",
  check_duplicate_invoice: "检查重复报销",
  create_approval_ticket: "创建升级审批单",
  write_audit_log: "写入审计日志",
};

const fieldLabels = {
  invoice_id: "发票号码",
  invoice_date: "开票日期",
  seller_name: "销售方",
  seller_tax_id: "销售方税号",
  buyer_name: "购买方",
  project_id: "项目编号",
  category: "费用类别",
  description: "项目说明",
  amount: "不含税金额",
  tax_amount: "税额",
  total_amount: "价税合计",
  currency: "币种",
  payment_method: "支付方式",
  notes: "备注",
};

let appConfig = null;

function setSource(source) {
  $$("[data-source-pane]").forEach((pane) => {
    pane.classList.toggle("hidden", pane.dataset.sourcePane !== source);
  });
}

function setBusy(busy) {
  $("#submit-button").disabled = busy;
  $("#submit-button").firstElementChild.textContent = busy ? "正在审计…" : "开始审计";
  $("#result-panel").classList.toggle("empty", !busy && $("#result-content").classList.contains("hidden"));
  $("#empty-state").classList.toggle("hidden", busy);
  $("#loading-state").classList.toggle("hidden", !busy);
  if (busy) {
    $("#result-content").classList.add("hidden");
    $("#result-panel").classList.remove("empty");
  }
}

function showError(message) {
  const box = $("#form-error");
  box.textContent = message;
  box.classList.toggle("hidden", !message);
}

function appendDefinition(list, key, value) {
  const wrapper = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = fieldLabels[key] || key;
  description.textContent = value == null ? "—" : String(value);
  description.title = description.textContent;
  wrapper.append(term, description);
  list.append(wrapper);
}

function renderResult(result) {
  $("#empty-state").classList.add("hidden");
  $("#loading-state").classList.add("hidden");
  $("#result-content").classList.remove("hidden");
  $("#result-panel").classList.remove("empty");

  const actionLabel = actionLabels[result.action] || result.action;
  $("#result-title").textContent = actionLabel;
  const badge = $("#decision-badge");
  badge.textContent = actionLabel;
  badge.className = `decision-badge ${result.action}`;

  $("#summary-id").textContent = result.invoice_id || "UNKNOWN";
  $("#summary-type").textContent = typeLabels[result.template_type] || result.template_type;
  $("#summary-risk").textContent = riskLabels[result.risk_level] || result.risk_level;
  $("#summary-mode").textContent = result.processing_mode === "deepseek" ? "DeepSeek" : "本地离线";
  $("#result-answer").textContent = result.answer || result.rationale || "暂无说明";

  const fields = Object.entries(result.extracted_fields || {});
  const fieldList = $("#field-list");
  fieldList.replaceChildren();
  fields.forEach(([key, value]) => appendDefinition(fieldList, key, value));
  $("#field-count").textContent = `${fields.length} 项`;

  const tools = result.tool_calls || [];
  const toolList = $("#tool-list");
  toolList.replaceChildren();
  tools.forEach((tool) => {
    const item = document.createElement("li");
    item.textContent = toolLabels[tool.tool_name] || tool.tool_name;
    toolList.append(item);
  });
  $("#tool-count").textContent = `${tools.length} 项`;

  const citations = result.citations || [];
  const citationList = $("#citation-list");
  citationList.replaceChildren();
  citations.forEach((citation) => {
    const item = document.createElement("div");
    item.className = "citation-item";
    const source = document.createElement("strong");
    source.textContent = `${citation.file} · ${citation.source_id}`;
    const quote = document.createElement("blockquote");
    quote.textContent = citation.quote;
    item.append(source, quote);
    citationList.append(item);
  });
  if (!citations.length) {
    const empty = document.createElement("div");
    empty.className = "citation-item";
    empty.textContent = "未检索到匹配的政策片段。";
    citationList.append(empty);
  }
  $("#citation-count").textContent = `${citations.length} 条`;

  const warnings = [...(result.warnings || []), ...(result.errors || [])];
  const warningList = $("#warning-list");
  warningList.replaceChildren();
  warnings.forEach((warning) => {
    const line = document.createElement("p");
    line.textContent = warning;
    warningList.append(line);
  });
  warningList.classList.toggle("hidden", warnings.length === 0);
}

async function parseResponse(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    if (Array.isArray(detail)) {
      throw new Error(detail.map((item) => item.msg).join("；"));
    }
    throw new Error(detail || `请求失败（HTTP ${response.status}）`);
  }
  return data;
}

async function submitAudit(event) {
  event.preventDefault();
  showError("");
  const source = $('input[name="source"]:checked').value;
  const query = $("#audit-query").value.trim();
  const useDeepseek = $("#use-deepseek").checked;
  if (!query) {
    showError("请输入审计问题。");
    return;
  }

  let request;
  if (source === "demo") {
    const invoiceId = $("#demo-invoice").value;
    if (!invoiceId) {
      showError("请选择一张示例发票。");
      return;
    }
    request = fetch(`/api/v1/demo-invoices/${encodeURIComponent(invoiceId)}/audit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, use_deepseek: useDeepseek }),
    });
  } else if (source === "text") {
    const invoiceText = $("#invoice-text").value.trim();
    if (!invoiceText) {
      showError("请粘贴发票文本。");
      return;
    }
    request = fetch("/api/v1/audits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        invoice_text: invoiceText,
        query,
        use_deepseek: useDeepseek,
      }),
    });
  } else {
    const file = $("#invoice-file").files[0];
    if (!file) {
      showError("请选择要上传的发票文件。");
      return;
    }
    if (appConfig && file.size > appConfig.max_upload_bytes) {
      showError(`文件不能超过 ${Math.floor(appConfig.max_upload_bytes / 1024 / 1024)} MB。`);
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("query", query);
    formData.append("use_deepseek", String(useDeepseek));
    request = fetch("/api/v1/audits/file", { method: "POST", body: formData });
  }

  setBusy(true);
  try {
    renderResult(await parseResponse(await request));
  } catch (error) {
    showError(error.message || "审计失败，请稍后重试。");
    $("#empty-state").classList.remove("hidden");
    $("#result-panel").classList.add("empty");
  } finally {
    setBusy(false);
  }
}

async function loadApplication() {
  try {
    const [configResponse, invoicesResponse] = await Promise.all([
      fetch("/api/v1/config"),
      fetch("/api/v1/demo-invoices"),
    ]);
    appConfig = await parseResponse(configResponse);
    const invoices = await parseResponse(invoicesResponse);

    const select = $("#demo-invoice");
    select.replaceChildren();
    invoices.forEach((invoice) => {
      const option = document.createElement("option");
      option.value = invoice.invoice_id;
      const total = invoice.total_amount == null ? "金额缺失" : `¥${invoice.total_amount}`;
      option.textContent = `${invoice.invoice_id} · ${invoice.seller_name} · ${total}`;
      select.append(option);
    });

    const toggle = $("#use-deepseek");
    toggle.disabled = !appConfig.deepseek_configured;
    $("#deepseek-help").textContent = appConfig.deepseek_configured
      ? "已配置，可生成更自然的说明；审计结论仍由规则决定"
      : "未配置 API Key，当前使用免费离线模式";
    const status = $("#system-status");
    status.classList.add("online");
    status.lastElementChild.textContent = "本地服务正常";
  } catch (error) {
    $("#system-status").lastElementChild.textContent = "服务连接失败";
    showError(error.message || "初始化失败。");
  }
}

function resetForm() {
  $("#audit-form").reset();
  $('input[name="source"][value="demo"]').checked = true;
  setSource("demo");
  $("#audit-query").value = "请审核这张发票是否可以报销，并说明依据。";
  $("#file-label").textContent = "拖入文件，或点击选择";
  $("#result-content").classList.add("hidden");
  $("#loading-state").classList.add("hidden");
  $("#empty-state").classList.remove("hidden");
  $("#result-panel").classList.add("empty");
  showError("");
  if (appConfig) {
    $("#use-deepseek").disabled = !appConfig.deepseek_configured;
  }
}

$$('input[name="source"]').forEach((radio) => {
  radio.addEventListener("change", () => setSource(radio.value));
});

$("#invoice-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  $("#file-label").textContent = file ? file.name : "拖入文件，或点击选择";
});

const dropZone = $("#drop-zone");
["dragenter", "dragover"].forEach((name) => {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragover");
  });
});
["dragleave", "drop"].forEach((name) => {
  dropZone.addEventListener(name, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragover");
  });
});
dropZone.addEventListener("drop", (event) => {
  const files = event.dataTransfer.files;
  if (files.length) {
    $("#invoice-file").files = files;
    $("#file-label").textContent = files[0].name;
  }
});

$("#audit-form").addEventListener("submit", submitAudit);
$("#reset-button").addEventListener("click", resetForm);
loadApplication();
