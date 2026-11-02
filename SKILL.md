---
name: zotero-pdf
description: Find and attach lawful or open-access paper PDFs to existing Zotero 10 items, then add evidence-based tags and collection membership. 为 Zotero 文献查找并保存可合法获取的 PDF，写入后自动打标签和归类。优先匹配 DOI；只使用开放获取或用户有权访问的来源。
license: MIT
---

# Zotero 论文 PDF

本技能负责从可靠来源取得论文 PDF、保存为现有 Zotero 条目的本地附件，并依据题录与全文证据完成自动分类。Zotero 搜索和基础库管理使用 Zotero 技能；桌面启动、窗口定位和授权窗口处理使用 Computer Use。

下文中的 `<SKILL_DIR>` 指包含本 `SKILL.md` 的绝对目录；使用 helper 命令前先将其替换为本机实际路径。

## 写入流程

1. 用 Zotero 技能定位目标条目，记录条目 key、标题、作者、DOI、当前 tags 和 collections。优先用 DOI 唯一匹配；无 DOI 时至少用标题与作者交叉核验。
2. 查找可合法访问的 PDF。优先论文官方开放获取页面、作者或机构仓储、arXiv、PubMed Central 等可信来源。不得绕过付费墙、登录限制或访问控制。
3. 下载到临时位置，确认文件头为 `%PDF-`，并核对标题、作者、DOI 或稳定标识。元数据冲突或仅标题相似时停止写入。
4. 用 Zotero 技能检查桌面端和本地 API。Zotero 需运行且版本不低于 10，本地 API 需可用。需要启动或聚焦 Zotero 时，先阅读并遵循 `computer-use` 技能：
   - 使用 Computer Use 查找现有 Zotero 窗口；应用未运行时从已安装应用启动它。
   - 不通过 shell、`taskkill`、强制重启或伪造窗口句柄启动 Zotero。
   - 启动后重新读取 Zotero 窗口；不能把启动器、空白窗口或授权弹窗误当成可操作主窗口。
5. 附件写入只对已完成核验的父条目执行：

   ```powershell
   python "<SKILL_DIR>\scripts\zotero_pdf.py" attach `
     --item-key ITEM_KEY `
     --pdf "C:\path\paper.pdf" `
     --source-url "https://..." `
     --doi 10.xxxx/... `
     --title "Exact Zotero title"
   ```

   `--doi` 或 `--title` 至少提供一个。同一 helper 进程复用一个授权 key；不要把 API key 写入命令、日志、说明或临时文件。
6. Zotero 可能弹出“本地 API 授权”。用 Computer Use 查找并聚焦该窗口，然后让用户完成安全授权：
   - 不得自动点击“允许”“拒绝”或“始终允许”，也不得绕过系统、应用或凭据授权。
   - 优先请用户选择“始终允许”，避免每条文献重复授权；用户已明确完成授权后，直接继续，不重复询问。
   - 若 `attach` 超时，先检查 Zotero 是否仍显示授权窗口，并检查父条目是否会留下空附件。不要在授权未完成时循环重试。
   - 授权完成后最多重试一次；仍失败则报告具体写入阶段并保留临时 PDF。
7. 每条新增题录或 PDF 写入成功后，立即自动打标签和归类，不等待额外确认。先运行 `inspect`，读取该条目现有 `tags` 与 `collections`，并读取 `libraryTags` 和带层级路径的 `libraryCollections`；再从题录、摘要和必要的论文正文中归纳稳定主题：

   ```powershell
   python "<SKILL_DIR>\scripts\zotero_pdf.py" inspect `
     --item-key ITEM_KEY `
     --title "Exact Zotero title"
   ```
   - 优先复用已有标签；没有合适标签时才创建简洁的新标签。
   - 通常添加 3 到 6 个有证据支持的标签，覆盖研究主题、方法或应用，以及关键参与方式；不要添加作者、年份或未经支持的推断。
   - 用户明确给出目标 folder 时使用该 collection；否则选择现有 collection 中最精确的主题入口。不要仅因名称相似就扩大分类，也不要在未要求时新建 collection。
   - 添加 collection 和 tags 时保留条目原有归属与标签。
8. 执行自动分类。`--title` 或 `--doi` 仍用于防止写错条目；`--tag` 可重复：

   ```powershell
   python "<SKILL_DIR>\scripts\zotero_pdf.py" classify `
     --item-key ITEM_KEY `
     --title "Exact Zotero title" `
     --collection-name "目标 collection" `
     --tag "研究主题" `
     --tag "方法" `
     --tag "方法类别/具体参与方式"
   ```

   先运行同一命令并加 `--dry-run` 可检查待新增项。正式写入后 helper 会回读 tags 和 collection；只有 `verified: true` 才能报告成功。
9. 用 Zotero 技能的 `children ITEM_KEY --json` 获取附件 key，再用 `file-url ATTACHMENT_KEY` 确认正式文件存在、可读且文件头为 `%PDF-`。同时回读父条目 tags 与 collections。
10. 全部核验通过后删除本次下载的临时 PDF 和导入记录；保留 Zotero storage 中的正式附件。失败材料保留到问题定位完成，并明确报告失败阶段。

## 分类规则

标签应当可复用、粒度稳定，并能回答“这篇文献研究什么、怎样做、在哪个场景中做”。例如车辆轨迹与决策模型相关文献可使用：

- 主题：`车辆轨迹重构`
- 模型参与：`大语言模型`、`决策模型`
- 机制：`大语言模型参与方式/知识约束`、`大语言模型参与方式/证据诊断`
- 评价或用途：`可解释性`、`强控制`、`实验基线`

上面的标签是示例，不是固定词表。每次先查看当前文库已有标签，避免同义标签反复增殖。

## 边界

- 不修改论文题名、作者、DOI 等书目信息，不自动创建重复文献。
- 不覆盖已有 PDF；发现已有附件时先报告，由用户决定是否替换。
- 下载失败时可以尝试另一可信开放来源；没有合法可用 PDF 时如实报告。
- Zotero 10 本地 API 写入必须遵从 Zotero 授权。低于 Zotero 10、API 不可用或授权被拒绝时停止，不尝试绕过。
- Computer Use 用于启动、聚焦和观察 Zotero 窗口；安全与隐私授权必须由用户完成。

## 已验证流程

- Zotero 10 本地 API 的 `attach` 已完成真实附件创建与 MD5 回读验证。
- `inspect` 已从真实文库分页读取标签和带层级路径的 collection 词表。
- `classify` 已完成只追加标签与 collection、版本条件写入和写入后回读验证。
- 授权弹窗可能阻断后台 helper；需先用 Computer Use 定位窗口并完成用户授权。
