# Zotero PDF

[English](README.md) | [简体中文](README.zh-CN.md)

一个 Codex skill：把合法获取的论文 PDF 保存到现有 Zotero 10 条目，并依据实际掌握的证据追加标签和 collection 归属。配套的标准库 Python helper 会直接调用 Zotero Desktop 本地 API，在写入前核验书目信息，在附件写入后核验真实文件，并从 Zotero 回读分类结果。

`1.0.1` 新增了条目直接检索、附件回读核验、纯题录分类、更安全的授权管理、流式上传，以及找不到 PDF 时的明确降级路径。

## 处理范围

| 情况 | 行为 |
| --- | --- |
| 找到合法 PDF | 保存附件、核验 Zotero 中的实际文件，再依据全文分类。 |
| 已有 PDF | 核验现有附件，不重复写入。 |
| 只有 HTML 全文 | 依据 HTML 分类并标记 `html`；不把网页伪装成 PDF，也不创建占位附件。 |
| 只有可靠摘要与题录 | 保守分类并标记 `metadata`。 |
| 证据不足 | 报告 `not_found` 或 `insufficient_evidence`，不臆造“缺少 PDF”标签。 |
| 题录冲突或重复条目 | 在写入前停止，并报告冲突字段或候选 key。 |

## 功能

- 通过 DOI、精确标题和可选作者条件定位唯一的父条目。
- 读取题录证据、现有标签、collection 层级和子附件信息。
- 在确认文件头、大小、目标身份和重复状态后写入 PDF。
- 以流方式上传 PDF，并拒绝 4 GB 及以上的文件。
- 通过 Zotero 文件端点核验真实附件的 `%PDF-` 文件头、MD5 和可选字节数。
- 优先复用已有标签词表和 collection，而不是反复制造近义分类。
- 只追加标签与 collection，不覆盖条目原有分类。
- 使用 Zotero 版本条件写入，并在写入后回读确认。
- 正确处理单次与记住型授权，不把 API key 持久化。
- `401` 时最多重新授权一次，并按有界策略处理 `429`。

## 依赖

### 必需

- 支持 skill 的 Codex。
- **Zotero Desktop 10 或更高版本**。
- 已开启并可通过 `http://127.0.0.1:23119` 访问的 Zotero 本地 API。
- **Python 3.10 或更高版本**。
- 用于查找合法论文来源的联网与文档获取能力。

helper 只使用 Python 标准库，不需要第三方 Python 包。

### 可选

- Node.js/npm，仅在通过 `npx skills add` 安装时需要。
- `computer-use`，仅在 GUI 环境中需要启动或聚焦 Zotero 时使用。
- 独立的 `zotero` skill。本仓库自带条目检索和附件核验，不强制安装它。
- Zotero 浏览器 Connector。本流程不需要它。

首次本地**写入**必须由用户在 Zotero 交互窗口中授权，因此纯 headless 会话不能独立完成首次写入。只读检索与附件核验通常不需要授权。

## 安装

```bash
npx skills add CelianNew/zotero-pdf -g
```

只查看仓库包含的 skill，不安装：

```bash
npx skills add CelianNew/zotero-pdf --list
```

手动安装时，把仓库根目录放入对应 agent 的 skills 目录。包含 `SKILL.md` 的目录名必须为 `zotero-pdf`。

## Zotero 设置

1. 启动 Zotero Desktop 10 或更高版本。
2. 打开 **Settings → Advanced**。
3. 开启 **Allow other applications on this computer to communicate with Zotero**。
4. 在执行写入命令前，确认 `http://127.0.0.1:23119/api/` 有响应。

不要把本地 API 端口暴露给其他机器。Zotero 本地 API 的读取请求默认不需要认证。

## 使用

用明确论文和目标 collection 调用：

```text
使用 $zotero-pdf 为 DOI 10.xxxx/example 查找合法 PDF，
保存到匹配的 Zotero 条目，核验实际附件，
再优先使用我的现有标签和最精确的 collection 完成分类。
```

核心命令顺序：

```text
resolve → inspect → attach → verify-attachment → classify
```

### 检索与检查

```bash
python scripts/zotero_pdf.py resolve \
  --doi "10.xxxx/example" \
  --author "Family, Given"

python scripts/zotero_pdf.py inspect \
  --item-key ITEM_KEY \
  --doi "10.xxxx/example"
```

`resolve` 只返回一个 item key；若存在多个完全匹配的条目，会列出 key 要求人工消歧。`inspect` 返回题录证据、当前标签与 collection、完整文库词表、collection 路径，以及 PDF 附件的 key 和哈希。

### 保存与核验附件

```bash
python scripts/zotero_pdf.py attach \
  --item-key ITEM_KEY \
  --pdf /path/paper.pdf \
  --source-url "https://..." \
  --doi "10.xxxx/example" \
  --title "Exact Zotero title"

python scripts/zotero_pdf.py verify-attachment \
  --item-key ITEM_KEY \
  --attachment-key ATTACHMENT_KEY \
  --expected-md5 MD5_FROM_ATTACH_RESULT
```

`attach` 至少需要 `--doi` 或 `--title`。若已有 PDF、文件头不是 PDF，或文件大小达到 4 GB，helper 会拒绝写入。

### 自动分类

```bash
python scripts/zotero_pdf.py classify \
  --item-key ITEM_KEY \
  --doi "10.xxxx/example" \
  --evidence-level full_text \
  --collection-name "目标 collection" \
  --tag "研究主题" \
  --tag "方法" \
  --tag "方法/具体参与方式" \
  --dry-run
```

必须通过 `--evidence-level` 声明 `full_text`、`html` 或 `metadata`。去掉 `--dry-run` 后正式写入；只有回读确认全部新增标签与 collection 后，helper 才返回 `"verified": true`。

## 授权

开始本地写入时，Zotero 可能显示 **Allow**、**Always Allow** 和 **Deny**。

- **Allow** 只授权一次写入；多阶段附件上传可能需要多次确认。
- **Always Allow** 生成当前 helper 进程内可复用的记忆型 key。
- **Deny** 会停止流程。
- Zotero 的 **Clear Write Authorizations** 可撤销记忆型授权。

API key 只存在于进程内存，不写入磁盘。Computer Use 可以定位或聚焦 Zotero，但授权决定必须由用户本人完成。

## 写入保证

helper 不会：

- 修改标题、作者、DOI 等书目字段。
- 创建重复父条目或替换已有 PDF。
- 自动创建 collection。
- 在附件或分类核验失败时虚报成功。

分类采用追加语义：保留现有标签和 collection，大小写不敏感地去除重复标签，使用 Zotero 并发版本头，并在报告成功前回读最终条目。

## 找不到 PDF 时

1. 若存在合法的出版商或仓储 HTML 全文，依据 HTML 分类并标记 `--evidence-level html`。
2. 若只有可靠题录与摘要，使用 `--evidence-level metadata` 添加保守标签。
3. 若证据不足以支持这些标签，不写入任何内容，报告 `not_found` 或 `insufficient_evidence`。

默认流程不会把 HTML 改名为 PDF、创建空附件或添加失败标签。缺失文献台账必须由用户明确开启。

## 排障

详细排障矩阵覆盖本地 API 启动、`401`、`403`、`429`、`412`、重复条目、题录冲突、损坏 PDF、找不到 PDF、多附件、上传中断和 headless 授权：

- [Skill 入口与分流规则。](SKILL.md)
- [详细排障参考。](references/troubleshooting.md)

## 兼容性与验证

- Windows 上持续测试 Python 3.10 与 3.12。
- 实现使用跨平台 Python 与 HTTP API，但其他操作系统尚未认证。
- 发布验证包括 Codex skill validator、CLI help 检查、附件与授权测试，以及 Skills CLI 发现。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
