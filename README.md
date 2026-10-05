# 聚力盯盘系统 · 盘中超短顾问

**A 股日内盯盘的轻量自托管工具** — 开盘锁定三级支撑压力，三重确认触发低吸/减仓信号，量价与动量交叉验证，事件直接推 webhook。

> 🚀 **在线演示：<https://neurowiki.cn/stock/>** — 无需部署，打开即可体验完整界面与信号流。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![LightweightCharts](https://img.shields.io/badge/Charts-LightweightCharts-2962FF)](https://tradingview.github.io/lightweight-charts/)

> 🧭 界面只有一块：左侧自选与通知流，右侧 K 线 + 指标副图 + 行情综合。打开就知道自己在等什么。

---

## 它解决什么问题

日内做超短，最难的从来不是看指标，而是**等**。等价格走到位置、等量能给出确认、等动量拐头。人盯盘会在等待里走神，在临界点犹豫。

这套系统把「等」这件事机械化：

1. **开盘锁定三级价位** — S1/S2/S3 支撑、R1/R2/R3 压力，一天之内位置不变
2. **到位置才触发检查** — 价格没到位，系统不做任何判断
3. **三重确认才出信号** — 价位、量能、动量三项同时满足才喊，缺一项只记为「观望扫描」
4. **信号立刻推走** — 不依赖你盯着屏幕

---

## 核心机制

### 三级价位（开盘锁定）

```
        R3  ─── 突破则整组重置
        R2
        R1
   ───────── 当前价
        S1
        S2
        S3  ─── 跌破则整组重置
```

开盘后用前 40 根 K 线计算，按 0.3% 容差聚合成带状区。**只有突破 R3 或跌破 S3 才会整组重算**，避免盘中噪音反复扰动你的位置感。

### 三重确认

触发信号必须三项全过，缺一项降级为「观望扫描」：

| 维度 | 低吸标准 | 减仓标准 |
|------|---------|---------|
| **价位到位** | 触及支撑带 S1/S2/S3 | 触及压力带 R1/R2/R3 |
| **量能配合** | 缩量，量比 < 0.8（越低越好） | 冲高放量或长上影，量比 ≥ 1.2 或上影 ≥ 0.8% |
| **动量拐点** | RSI<30 拐头向上 + KDJ 的 J<10 拐头 + 长下影 ≥1% | RSI/J 超买或拐头向下 + 冲高回落上影 |

> 三个维度是**与**关系。少一项就只是「在看着」，不喊买卖。

### 事件流

系统产生 9 类事件，全部进通知流：

`situation` 局面更新 · `buy` / `sell` 信号 · `hold` 持有 · `hard_stop` 硬止损 · `hard_take` 硬止盈 · `swing` 摆动点 · `levels_reset` 价位重置 · `checklist` 检查清单

### 风控参数（可在 `config.yaml` 调）

硬止损 5% · 硬止盈 20% · ATR 止损倍数 1.2 · 默认盈亏比 2.0 · 通知冷却 180 秒

---

## 快速开始

```bash
git clone https://github.com/gwwudh/ashare-scalp-advisor.git
cd ashare-scalp-advisor

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m app.main                 # 启动，默认 http://127.0.0.1:8765
```

打开 <http://127.0.0.1:8765> 即可用。**开箱无需任何配置** —— 不配 AI 也能完整盯盘，只是解读文案由规则生成。

---

## 📖 使用说明

### 一、添加自选股

左侧输入框填代码（如 `600519`）→ 点「添加自选」。自选列表保存在 `watchlist.yaml`（该文件已被 gitignore，属你的个人数据）。

界面左栏有三块：自选管理、**自选局面**（每只票当前态势卡片）、**通知流**（事件实时滚动）。

### 二、看盘

右侧主区：

- **周期切换**：分时 / 1 分钟 / 5 分钟（默认）/ 日线
- **指标开关**：成交量、BOLL（主图叠加）、MACD、KDJ、RSI（副图分轨，时间轴联动）
- **自定义指标**：点「配置自定义」，支持 RSI 可调周期 / MACD 可调参数 / 双均线差值 EMA快−EMA慢
- **行情综合**：右侧小面板，实时量比、换手等

设计上主图叠加类（BOLL）与副图类分开渲染，互不遮挡。

### 三、接通知（可选）

在 `config.yaml` 填 webhook：

```yaml
notify:
  cooldown_sec: 180
  webhook_url: "https://your-webhook-url"
  use_llm: true
```

发的是纯文本 JSON，飞书 / 企业微信 / 自建服务都能接。留空则只打日志和终端输出。

### 四、接 AI 文案（可选）

不配也能跑，配了可以让解读更自然。**仓库不内置任何服务商地址**，两种填法任选：

**方式 A · 环境变量（推荐，密钥不落盘）**

```bash
cp .env.example .env
# 编辑 .env
LLM_BASE_URL=https://your-openai-compatible-endpoint/v1
LLM_MODEL=your-model-name
LLM_API_KEY=your-key
```

**方式 B · 写进 config.yaml**

```yaml
llm:
  enabled: true
  base_url: "https://your-openai-compatible-endpoint/v1"
  model: "your-model-name"
  timeout_sec: 25
  # api_key 不要写这里，走 .env 的 LLM_API_KEY
```

环境变量优先级高于 `config.yaml`。支持 OpenAI / DeepSeek / 智谱 / 本地 vLLM / Ollama 等一切 OpenAI 兼容接口。

> ⚠️ **AI 只负责把规则结论说成人话，不参与买卖判断。** 信号全部由 `app/engine/` 下的确定性规则产生，不受模型波动影响。关掉 AI 只是文案变朴素，逻辑不变。

---

## 配置项速查

| 分组 | 关键项 | 默认 | 说明 |
|------|--------|------|------|
| `poll` | `quote_interval_sec` | 5 | 行情轮询间隔 |
| | `kline_interval_sec` | 30 | K 线拉取间隔 |
| | `situation_interval_sec` | 60 | 局面评估间隔 |
| `bars` | `primary` / `trigger` | 5m / 1m | 主周期与触发周期 |
| `levels` | `lookback` | 40 | 计算价位的回看根数 |
| | `zone_pct` | 0.003 | 价位带容差 |
| `risk` | `hard_stop_pct` | 5.0 | 硬止损 |
| | `hard_take_pct` | 20.0 | 硬止盈 |
| | `default_atr_mult_sl` | 1.2 | ATR 止损倍数 |
| `notify` | `cooldown_sec` | 180 | 同类通知冷却 |
| `server` | `host` / `port` | 127.0.0.1 / 8765 | 监听地址 |

---

## API

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/snapshot` | 全量快照（自选+局面+事件） |
| GET/POST/DELETE | `/api/watchlist` | 自选股增删查 |
| POST | `/api/watchlist/reload` | 重新加载自选 |
| GET | `/api/kline/{code}` | 指定标的历史 K 线 |
| POST | `/api/chat` | 向 AI 顾问提问（需已配置 LLM） |

另有 `GET /docs` 是 FastAPI 自动生成的交互式文档。

---

## 部署到服务器（`/stock` 子路径）

线上演示站就是这样部署的：FastAPI 跑在本地端口，nginx 反代到子路径。前端通过 `window.APP_BASE` 感知挂载点，改子路径只需改这一处。

```nginx
location /stock/ {
    proxy_pass http://127.0.0.1:8765/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
}
```

同时把 `index.html` 里的 `window.APP_BASE = ""` 改成 `"/stock"`。

---

## 技术栈

**后端** FastAPI · Pydantic v2 · httpx · PyYAML · APScheduler 式轮询（线程）

**前端** 原生 JavaScript（无框架、无构建）· LightweightCharts 4.2 · 自写指标计算（`static/indicators.js`）

**特点**：零前端构建步骤、零打包产物，改完 JS 刷新即生效。整个项目不到 20 个文件。

---

## 免责声明

本项目是**盯盘辅助工具**，不是投资建议系统。

- 三级价位与三重确认是基于历史 K 线的统计规则，**不预测未来**
- 所有信号需你自己判断，据此操作的盈亏由你自己承担
- A 股 T+1 制度下，日内超短有结构性限制，请充分理解再使用

---

## License

[MIT](LICENSE)
