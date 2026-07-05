# PRD.md - GoodNotes PDF 结构理解包

## Summary

`goodnotes-pdf-prep` 是一个本地 CLI，把 GoodNotes 导出的 PDF 转成适合多模态 AI 阅读的结构包。它不直接调用 AI，而是把大画布拆成可定位、可追踪、可复核的证据：全局图、局部高清 tiles、连续 agent tiles、线条候选、文字块和图结构。

目标不是一次性生成完美思维导图，而是让后续 agent 比直接上传 PDF 更稳定地复原内容和远距离关系。

## 用户流程

1. 从 GoodNotes 导出 PDF。
2. 运行：

```bash
goodnotes-prep SOURCE.pdf --out OUT --emit-recognition-tasks --emit-agent-tiles
```

3. 把整个 `OUT` 文件夹交给有多模态能力的 agent。
4. agent 根据 `prompt.md` 读取 `graph.json`、`overview.png`、`agent_tiles` 和 `tiles`。
5. 如果需要更强结构化，外部 agent 生成 `TEXT_BLOCKS.json`。
6. 运行：

```bash
goodnotes-prep attach-text OUT --text-blocks TEXT_BLOCKS.json
```

7. 使用更新后的 `graph.json` 和 `prompt.md` 生成 Markdown、Mermaid、OPML 或思维导图草稿。

## 功能要求

- 读取 PDF 页面尺寸，保留 PDF 全局坐标。
- 生成整页 overview，帮助 agent 先看整体布局。
- 生成局部高清 tiles，保留 bbox、像素尺寸、dpi 和 overlap。
- 生成 full-width `agent_tiles`，帮助 agent 连续阅读长箭头和大画布上下文。
- `agent_tiles` 使用 JPEG、相对路径、逐页切片、固定 overlap stride，最后一块允许短于目标高度。
- 从 PDF 文本层提取 text blocks；没有文本层时输出 warning，不中断。
- 从 editable PDF 提取 vector drawing paths。
- 标记 connector candidates 和 long-distance connectors。
- 支持 `attach-text` 回填外部 AI/VLM 识别的 text blocks。
- `attach-text` 支持从 detail `tile_XXXX` 或 `agent_tile_XXX` 的 tile-local bbox 换算到 PDF 全局坐标。
- 输出 `graph.json`，包含 nodes、edges、unresolved connectors 和 warnings。
- 输出 `graph.json.long_connectors`，逐条列出长距离 connector 的状态和视觉证据，避免多条长线被合并叙述。
- 生成 `prompt.md`，要求后续 agent 显式保留 graph edges 和长箭头关系。

## 非目标

- 不内置 AI/OCR API。
- 不做 Web UI。
- 不解析 `.goodnotes` 私有二进制格式。
- 不直接保证生成完美 XMind。
- 不把 edge candidates 声明为绝对正确关系。

## 验收标准

基础验收：

- CLI 能生成 `manifest.json`、`graph.json`、`prompt.md`、`overview.png`、`tiles/`。
- 使用 `--emit-agent-tiles` 时生成 `agent_tiles/*.jpg` 和 `agent_tiles/metadata.json`。
- `agent_tiles/metadata.json` 中每个 tile 记录 page index、PDF bbox、pixel size、dpi、overlap，图片路径为相对路径。
- 使用 `--emit-recognition-tasks` 时生成 recognition tasks 和 text block schema。
- 空文本层、空 drawings、超大页面只产生 warnings，不中断。
- `uv run pytest` 通过。

代表性大画布验收：

- `prompt.md` 明确要求 agent 先读取 `graph.json`。
- `graph.json` 中已有 edge 必须在最终 Markdown 中显式保留。
- `graph.json.long_connectors` 中的 ultra-long connector 必须被逐条检查；resolved edge 和 unresolved connector 不能写成同一条关系。
- `agent_tiles` 能让 agent 看到长箭头的连续视觉证据。
- 如果同一页里存在多条远距离连接线，输出必须把它们分别暴露为 graph evidence、visual evidence 或 required review point。
- 未解析的长线不能被丢弃；它们应保留在 `unresolved_connectors` 和 `long_connectors` 中等待文字块回填或人工确认。

## Roadmap

- 短期：提高 prompt 对长箭头和 graph edges 的约束力。
- 短期：支持 agent tile 识别结果回填为 global bbox。
- 中期：生成 long-arrow review report。
- 中期：输出 Mermaid / OPML 草稿。
- 远期：评估 `.goodnotes` 原生解析，但不污染当前 PDF 主流程。
