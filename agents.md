# agents.md - goodnotes-pdf-prep

## 项目目标

`goodnotes-pdf-prep` 把 GoodNotes 导出的 PDF 预处理成 AI-friendly structure package，让多模态 agent 更稳定地理解大画布、手写文字、远距离连线和结构关系。

主流程：

```bash
goodnotes-prep SOURCE.pdf --out OUT
```

常用增强流程：

```bash
goodnotes-prep SOURCE.pdf --out OUT --emit-recognition-tasks --emit-agent-tiles
goodnotes-prep attach-text OUT --text-blocks TEXT_BLOCKS.json
```

## 技术栈

- Python 3.11+
- uv
- PyMuPDF
- Pillow
- argparse
- pytest

不内置 OpenAI、Anthropic、Google、Gemini、Mistral 或其他 AI/OCR API。

## 输出结构

核心输出：

- `manifest.json`：总索引，包含页面尺寸、tiles、agent tiles、文字块、线条和候选边。
- `graph.json`：节点、候选边、未解析 connector 和 warnings。
- `prompt.md`：给多模态 agent 的固定读取说明。
- `pages/page_XXX/overview.png`：整页低清全局图。
- `pages/page_XXX/tiles/*.png`：局部高清重叠切片，用于精读文字和 bbox。
- `agent_tiles/*.jpg`：full-width 连续切片，用于读取长箭头和大画布上下文。
- `agent_tiles/metadata.json`：agent tiles 的 page、bbox、pixel size、dpi、overlap 元数据。
- `graph.json.long_connectors`：长距离 connector 的独立 review items，包含 resolved/unresolved 状态、端点、路线、颜色和长度证据。
- `recognition_tasks/page_XXX.json`：给外部 AI/VLM 的文字识别任务包。
- `text_blocks.schema.json`：`attach-text` 接受的文字块格式。

## 开发约束

- 以当前 `goodnotes-pdf-prep` 为主项目，不迁移成单文件原型脚本。
- 平行原型只作为参考：吸收 full-width overlapping tiles 思路，不复制旧项目结构。
- `agent_tiles` 是逐页 full-width JPEG 切片，不是多页拼接画布；最后一块允许短于 `--agent-tile-height`。
- `agent_tiles` 和 detail `tiles` 都可以作为 `attach-text` 的 `tile_id` 来源；精确 bbox 优先使用 detail `tiles`。
- 多条 ultra-long connector 必须作为独立证据保留；resolved edge 和 unresolved connector 不能合并成一条泛泛叙述。
- 所有输出图片路径写入 JSON 时必须是相对路径。
- 不解析 `.goodnotes` 私有格式；MVP 输入仍是 PDF。
- 不直接生成最终完美 XMind；当前目标是结构理解包。
- 不删除或清空旧输出目录；需要测试时使用新的临时目录。
- 输出目录必须为空或不存在，避免覆盖用户已有结果。

## 常用命令

```bash
uv run pytest
uv run goodnotes-prep SOURCE.pdf --out OUT --emit-recognition-tasks --emit-agent-tiles
uv run goodnotes-prep attach-text OUT --text-blocks TEXT_BLOCKS.json
```

Agent tile 参数默认值：

```bash
--agent-dpi 200 --agent-tile-height 2000 --agent-overlap 0.05 --agent-quality 90
```

## 文档规则

本项目现在已有文档。后续每次改变 CLI、输出结构、prompt 语义、graph 逻辑或用户流程时，都要同步更新：

- `agents.md`
- `context.md`
- `PRD.md`
