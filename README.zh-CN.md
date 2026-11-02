# Zotero PDF

[English](README.md) | [简体中文](README.zh-CN.md)

一个 Codex skill：获取可合法访问的论文 PDF，保存到现有 Zotero 10 条目，并依据题录、摘要与必要的全文证据自动追加标签和 collection 归属。

helper 会在写入前核验目标条目，拒绝重复 PDF，保留原有书目信息与分类，并从 Zotero 回读每项分类变更后再报告成功。

## 功能

- 通过 DOI 或精确标题核验 Zotero 父条目。
- 支持论文官方开放获取版本、机构仓储版本或用户有权使用的本地 PDF。
- 拒绝非 PDF 文件和重复 PDF 附件。
- 优先复用现有标签词表与 collection 名称。
- 分类前读取完整文库标签词表和 collection 层级路径。
- 自动选择 3–6 个有证据支持的稳定标签，避免堆叠作者、年份等元数据。
- 只追加标签与 collection，不覆盖条目原有分类。
- 使用 `If-Unmodified-Since-Version` 防止并发覆盖，并在写入后回读验证。
- 需要时用 Computer Use 启动、聚焦或定位 Zotero；安全授权仍由用户本人完成。
- 不在发布包中保存临时文件、本地绝对路径或授权材料。

## 环境要求

- 支持 skill 的 Codex。
- Zotero Desktop 10 或更高版本。
- Zotero 本地 API 可通过 `http://127.0.0.1:23119` 访问。
- Python 3.10 或更高版本。
- 用于条目检索和附件核验的 `zotero` skill。
- 需要启动 Zotero 或定位授权窗口时使用 `computer-use` skill。

helper 不依赖第三方 Python 包。

## 安装

使用 Skills CLI 全局安装：

```bash
npx skills add CelianNew/zotero-pdf -g
```

也可以先查看仓库内包含的 skill，不执行安装：

```bash
npx skills add CelianNew/zotero-pdf --list
```

手动安装时，克隆仓库并将仓库根目录放入对应 agent 的 skills 目录。包含 `SKILL.md` 的目录名必须为 `zotero-pdf`。

## 使用

显式调用：

```text
使用 $zotero-pdf 为 DOI 10.xxxx/example 查找并核验开放获取 PDF，
保存到匹配的 Zotero 条目，并优先使用我的现有标签和最精确的 collection 完成分类。
```

skill 会先用配套 Zotero 能力定位精确的父条目，再核验 PDF 并调用：

```text
python scripts/zotero_pdf.py attach ...
python scripts/zotero_pdf.py inspect ...
python scripts/zotero_pdf.py classify ...
```

请在 skill 目录内运行 helper。完整参数见 `python scripts/zotero_pdf.py --help`。

### 保存附件

```bash
python scripts/zotero_pdf.py attach \
  --item-key ITEM_KEY \
  --pdf /path/paper.pdf \
  --source-url "https://..." \
  --doi "10.xxxx/..." \
  --title "Exact Zotero title"
```

`--doi` 与 `--title` 至少提供一个。

### 检查分类

```bash
python scripts/zotero_pdf.py inspect \
  --item-key ITEM_KEY \
  --title "Exact Zotero title"
```

结果包括条目当前标签和 collection、完整文库标签词表，以及 collection 的 key、名称、父级和层级路径。

### 自动分类

```bash
python scripts/zotero_pdf.py classify \
  --item-key ITEM_KEY \
  --title "Exact Zotero title" \
  --collection-name "目标 collection" \
  --tag "研究主题" \
  --tag "方法" \
  --tag "方法/具体参与方式"
```

加 `--dry-run` 可预览准确的待新增项。只有 Zotero 回读到新标签和 collection 后，正式写入才会返回 `"verified": true`。

## 写入保证

helper 不修改书目字段、不覆盖已有 PDF、不创建重复父条目，也不会擅自创建 collection。分类采用追加语义：

- 保留原有标签。
- 保留原 collection 归属。
- 大小写不敏感地去除重复标签。
- 写入时携带 Zotero 乐观并发版本头。
- 在报告成功前从 Zotero 回读最终状态。

## 授权与隐私

Zotero 可能请求本地写入授权。Computer Use 可以定位或聚焦授权窗口，但“允许”“始终允许”或“拒绝”必须由用户本人选择，skill 不得自动处理这些安全决定。

本地 API key 只在 helper 进程内存中复用，不得写入命令行、日志、说明或临时文件。

## 适用边界

本 skill 为现有 Zotero 条目保存 PDF 并整理分类。它不会绕过付费墙、代替用户登录出版商账户、伪造书目资料，也不会取代 Zotero 自身的文献管理能力。

## 验证

发布前会执行 Codex skill validator、Python 语法检查、Skills CLI 发现测试，以及标签合并、collection 解析、版本条件写入和写后回读的针对性测试。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
