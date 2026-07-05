# context.md - goodnotes-pdf-prep

更新时间：2026-07-05

## 背景

用户的问题不是普通多页 PDF 识别，而是 GoodNotes 单页或少页超大 whiteboard 画布。直接把导出的 PDF 给 AI 时，AI 往往会降采样或拆成局部图，导致：

- 手写细字看不清。
- 远距离线条、箭头和括号被断开。
- 相隔很远但由线连接的语义关系被漏掉或泛化。
- 最终 Markdown / mindmap 看似完整，但结构关系不可靠。

## 已做决策

1.0 目标是视觉证据包：

- 输出 overview。
- 输出带全局坐标的局部高清 tiles。
- 提取 PDF 文本层。
- 提取 vector drawing paths。
- 生成 connector 和 edge candidates。

2.0 目标是结构理解包：

- 生成 recognition tasks，让外部多模态 agent 识别 tile 文字。
- 支持 `attach-text` 回填 text blocks。
- 根据文字 bbox 和 connector 端点生成 `graph.json`。
- 在 `prompt.md` 中要求后续 agent 显式保留 graph edges。

本轮合并一个平行原型的有效经验：

- full-width overlapping tiles 对大画布连续阅读有帮助。
- `agent_tiles` 适合多模态 agent 直接浏览长箭头和上下文。
- 原有 `tiles` 仍更适合局部 OCR、bbox 和精读。
- 当前实现采用逐页切片，不做多页 stitching；多页 PDF 会在同一个 `agent_tiles/` 目录下得到唯一递增的 tile id。
- 切片采用固定 stride，最后一块自然变短，不为了满高而向上回填。

因此当前组合是：

- `overview.png`：全局布局。
- `graph.json`：结构边证据。
- `agent_tiles/*.jpg`：连续阅读大画布。
- `tiles/*.png`：局部高清确认。

坐标约定：

- `global_pdf_bbox` 使用 PDF page 坐标，单位是 points。
- AI 回填的 `bbox` 使用对应 tile 内的像素坐标。
- `attach-text` 支持 detail tile id 和 `agent_tile_XXX` id。
- `long_connectors` 中的 `connector_endpoints`、`connector_anchor_points`、`route_hint` 用于区分多条远距离线。

## 代表性验收观察

大画布白板通常会出现多条远距离关系线。验收时应特别关注：

- 从左侧或上方概念区连接到底部总结区的长线。
- 从中部问题区穿过空白区域连接到底部总结区的长线。
- 从右侧扩展说明区折返到底部总结区的长线。

这些线必须作为独立 visual evidence 保留，不能在最终 Markdown 中被合并成“整体连接到底部”。

2026-07-05 发现一个重要表征问题：

- 一类失败模式是：底层能提取到两条不同超长线，但输出层只强提示已解析 edge，未解析 long connector 缺少端点、route、颜色等证据。
- 原问题出在输出层：`graph.json` 只强提示 resolved edge，unresolved long connector 缺少端点、route、颜色等证据，`prompt.md` 也没有要求逐条 review。
- 修正后 `graph.json.long_connectors` 和 `prompt.md` 会把 ultra-long connectors 单独列为 review items，明确 resolved 与 unresolved 的区别。

但没有内置 AI/OCR，所以完整复原仍依赖外部多模态 agent 对 `agent_tiles` 和 `tiles` 的识别质量。

## 当前不足

- GoodNotes flattened PDF 经常没有可提取文本层。
- 自动文字识别不在本项目内完成。
- edge candidates 是候选，不声明 100% 正确。
- 线条语义仍需要 `graph.json`、视觉证据和 agent 共同判断。
- `.goodnotes` 原生文件暂不解析。

## 后续方向

- 加强 text block merge，对 `agent_tiles` 识别结果也支持坐标回填。
- 输出更明确的 long-arrow review report。
- 增加真实样本 fixture，覆盖三条长箭头。
- 未来可选支持 OPML / Mermaid / XMind 导出，但不作为当前主目标。
