
## 安装

我们提供两种安装方式。推荐使用方式 2（使用 uv），安装速度更快、依赖管理更优。

### 方式一：使用 conda

1. 创建新的 conda 环境：

```bash
conda create -n open_manus python=3.12
conda activate open_manus
```

2. 克隆仓库：

```bash
git clone https://github.com/Cecil09312/OpenManus-gui.git
cd OpenManus-gui
```

3. 安装依赖：

```bash
pip install -r requirements.txt
```

### 方式二：使用 uv（推荐）

1. 安装 uv（一个快速的 Python 包安装器和解析器）：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. 克隆仓库：

```bash
git clone https://github.com/Cecil09312/OpenManus-gui.git
cd OpenManus-gui
```

3. 创建新的虚拟环境并激活：

```bash
uv venv --python 3.12
source .venv/bin/activate  # 在 Unix/macOS 上
# 或在 Windows 上：
# .venv\Scripts\activate
```

4. 安装依赖：

```bash
uv pip install -r requirements.txt
```

### 浏览器自动化工具（可选）
```bash
playwright install
```

## 配置

OpenManus-gui 需要为其使用的 LLM API 进行配置。请按照以下步骤完成配置：

1. 在 `config` 目录中创建 `config.toml` 文件（可以从示例文件复制）：

```bash
cp config/config.example.toml config/config.toml
```

2. 编辑 `config/config.toml`，添加你的 API Key 并自定义设置。完整的配置示例（含 LLM、浏览器、搜索、MCP、Daytona 等）：

```toml
# 全局 LLM 配置
[llm]
api_type = "openai"                          # DashScope 使用 OpenAI 兼容 API
model = "qwen-max"                           # DashScope 模型名称（支持 function calling）
# 可选模型：qwen-turbo, qwen-plus, qwen-max（都支持 function calling）
base_url = "your base url"
# 如果未设置，将从环境变量 DASHSCOPE_API_KEY 读取
api_key = "your api key"
max_tokens = 8192
temperature = 0.0

# 特定 LLM 模型的可选配置（视觉模型，用于浏览器视觉降级）
[llm.vision]
api_type = "openai"                          # DashScope 使用 OpenAI 兼容 API
model = "qwen-vl-plus"                       # DashScope 视觉模型：qwen-vl-plus
base_url = "your base url"
api_key = "your api key"
max_tokens = 8192
temperature = 0.0

# 浏览器配置
[browser]
headless = false                             # 是否无头模式运行（默认 false）
disable_security = true                      # 禁用浏览器安全特性（默认 true）
# 添加反检测参数，减少被网站封锁的可能性
extra_chromium_args = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

# 搜索引擎配置
[search]
engine = "baidu"                             # 主搜索引擎：Google / Baidu / DuckDuckGo / Bing
fallback_engines = ["duckduckgo", "google", "bing"]  # 主引擎失败后的降级顺序
retry_delay = 60                             # 引擎限流后重试间隔秒数
max_retries = 3                              # 全部失败后最大重试次数
lang = "zh"                                  # 搜索结果语言
country = "cn"                               # 搜索结果国家

# MCP (Model Context Protocol) 配置
[mcp]
server_reference = "app.mcp.server"         # 默认服务器模块引用

# GUI 服务器配置（app.py 使用）
[server]
host = "localhost"
port = 5172

# Runflow 多智能体可选配置
[runflow]
use_data_analysis_agent = false              # 数据分析智能体，默认禁用

# Daytona 沙箱配置
[daytona]
daytona_api_key = "your daytona api key"
#daytona_server_url = "https://app.daytona.io/api"
#daytona_target = "us"
```

## 快速开始

OpenManus-gui 提供两种使用方式：图形化 Web 界面（推荐）和命令行方式。

### 方式一：图形化 Web 界面（推荐）

启动 Web 服务，浏览器会自动打开图形界面：

```bash
python app.py
```

然后在网页中输入你的想法，即可通过可视化界面查看智能体的思考、工具调用和执行结果（支持 SSE 实时事件流）。默认访问地址：`http://localhost:5172`（可在 `config.toml` 的 `[server]` 中修改）。

### 方式二：命令行

一行命令运行 OpenManus-gui：

```bash
python main.py
```

然后通过终端输入你的想法！

如需运行 MCP 工具版本，可以执行：
```bash
python run_mcp.py
```

如需运行不稳定的多智能体版本，也可以执行：

```bash
python run_flow.py
```

### 自定义添加多个智能体

目前，除了通用的 OpenManus 智能体外，我们还集成了 DataAnalysis 智能体，适用于数据分析和数据可视化任务。你可以在 `config.toml` 中将此智能体添加到 `run_flow`。

```toml
# run-flow 的可选配置
[runflow]
use_data_analysis_agent = true     # 默认禁用，改为 true 即可启用
```
此外，你还需要安装相关依赖，以确保智能体正常运行。
## 工作流程

以下流程图展示了 OpenManus-gui 智能体的核心执行流程：

```mermaid
flowchart TD
    Start([用户输入提示词]) --> Create[创建 Manus 智能体]
    Create --> InitMCP[初始化 MCP 服务器连接]
    InitMCP --> State1[智能体状态: IDLE → RUNNING]
    State1 --> Check1{current_step < max_steps\n且状态 ≠ FINISHED?}

    Check1 -->|否| Cleanup[清理资源:\n浏览器 / MCP / 沙箱]
    Check1 -->|是| Step[执行单个步骤 step]

    Step --> Think[think: LLM 思考决策\n构建工具调用请求]
    Think --> Check2{LLM 是否选择工具?}

    Check2 -->|否| Record[记录思考内容到记忆]
    Check2 -->|是| Act[act: 执行工具调用]

    Act --> ExecTools[执行工具]
    ExecTools --> Tools[可用工具集合:\nPythonExecute / BrowserUseTool\nStrReplaceEditor / AskHuman\nMCP 工具 / Terminate]

    Tools --> WriteMem[工具结果写入记忆]
    Record --> WriteMem

    WriteMem --> Check3{是否触发特殊工具\n如 Terminate?}
    Check3 -->|是| Finish[状态 → FINISHED]
    Check3 -->|否| Check4{是否卡住\nis_stuck?}

    Check4 -->|是| Stuck[添加策略变更提示\n改变下一步策略]
    Check4 -->|否| IncStep[current_step += 1]
    Finish --> IncStep
    Stuck --> IncStep

    IncStep --> Check1
    Cleanup --> End([返回执行结果])

    classDef startEnd fill:#e1f5e1,stroke:#2e7d32,stroke-width:2px
    classDef decision fill:#fff3e0,stroke:#ef6c00,stroke-width:2px
    classDef process fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    classDef special fill:#fce4ec,stroke:#c62828,stroke-width:2px

    class Start,End startEnd
    class Check1,Check2,Check3,Check4 decision
    class Create,InitMCP,State1,Step,Think,Act,ExecTools,Tools,WriteMem,Record,IncStep process
    class Finish,Stuck,Cleanup special
```

**工作流程的关键组成部分：**

- **ReAct 循环**：每个步骤遵循 `think()` → `act()` 模式，由 LLM 对当前状态进行推理并决定调用哪些工具，然后执行它们。
- **工具执行**：智能体可以并行调用多个内置工具（Python 执行、浏览器自动化、文件编辑）和远程 MCP 工具。
- **状态管理**：智能体在 `IDLE`、`RUNNING`、`FINISHED` 和 `ERROR` 状态之间转换，并具有可配置的 `max_steps` 限制（默认：30）。
- **卡住检测**：当检测到重复响应时，智能体会注入策略变更提示以跳出循环。
- **优雅关闭**：在完成或达到步骤上限时，智能体会清理浏览器会话、MCP 连接和沙箱资源。
