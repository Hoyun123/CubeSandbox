# OpenCode + CubeSandbox 示例

[English](README.md)

在 CubeSandbox MicroVM 内运行 [OpenCode coding agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
——一个终端原生的 AI 编码 Agent。Agent 在隔离、可复现的沙箱内编辑文件、执行命令、访问 LLM API。

本示例包含：

- `Dockerfile`：在 CubeSandbox 基础镜像上叠加 Node.js + OpenCode CLI（envd 已监听 `:49983`）。
- `run_opencode.py`：在 `/workspace` 内无交互地单次运行 OpenCode。
- `resume_opencode.py`：跨 pause/resume 两轮运行，验证 `/workspace` 与 OpenCode 状态目录在快照后仍然保留。
- `network_policy.py`：默认拒绝出网，CubeEgress 在链路上注入 API Key，密钥完全不进入 VM。
- `env_utils.py`、`.env.example`、`requirements.txt`。

## 目录结构

```
opencode-integration/
├── Dockerfile              # CubeSandbox 模板镜像（Node.js + OpenCode CLI）
├── .env.example            # 复制为 .env 后填写
├── .gitignore
├── requirements.txt        # 宿主驱动依赖（e2b、cubesandbox、python-dotenv）
├── env_utils.py            # .env 加载、provider 密钥、OpenCode 命令构造
├── _opencode_common.py     # 沙箱命令公共辅助（run/ensure/id）
├── run_opencode.py         # 单次 OpenCode 任务
├── resume_opencode.py      # pause / resume 会话持久化
├── network_policy.py       # 默认拒绝出网 + 链路上注入密钥
├── README.md               # 英文文档
└── README_zh.md            # 中文文档（本文）
```

## 前置条件

- 已部署 CubeSandbox，CubeAPI 可访问（`http://<node>:3000`）。
- `cubemastercli` 已在 `$PATH` 且已连通集群。
- 构建机装有 Docker，且 registry 能被 Cube 节点拉取。
- 一个 LLM provider 的 API Key（默认 Moonshot；任何 OpenAI 兼容端点均可）。
- Python 3.10+（宿主端驱动脚本）。

## 1. 构建模板镜像

```bash
docker build --platform linux/amd64 \
  -t <your-registry>/opencode-cube:latest \
  examples/opencode-integration
docker push <your-registry>/opencode-cube:latest
```

镜像会安装 `opencode-ai`，以及 `git`、`python3`、`ripgrep`、`jq`，并清理 apt/npm 缓存。
可通过 `--build-arg OPENCODE_VERSION=x.y.z` 固定 OpenCode 版本。

## 2. 注册为 Cube 模板

```bash
cubemastercli tpl create-from-image \
  --image <your-registry>/opencode-cube:latest \
  --writable-layer-size 4G \
  --expose-port 49983 \
  --probe       49983 \
  --probe-path  /health

cubemastercli tpl watch --job-id <job_id>
```

任务变为 `READY` 后记下 `template_id`。

## 3. 配置宿主端驱动

```bash
cd examples/opencode-integration
cp .env.example .env
# 填写 E2B_API_URL、CUBE_TEMPLATE_ID 和你的 provider key
pip install -r requirements.txt
```

| 变量 | 作用位置 | 说明 |
|---|---|---|
| `E2B_API_URL` | 本地进程 | CubeAPI 地址（`http://<node>:3000`） |
| `E2B_API_KEY` | 本地进程 | 本地开发填任意非空字符串 |
| `CUBE_TEMPLATE_ID` | `Sandbox.create(template=...)` | 来自第 2 步 |
| `OPENCODE_PROVIDER` / `OPENCODE_MODEL` | OpenCode CLI | 默认 `moonshot` / `kimi-latest` |
| `MOONSHOT_API_KEY` | `envs=...`（直连）或 CubeEgress 注入（vault） | provider 密钥 |
| `OPENCODE_LLM_HOST` | `network_policy.py` | 默认拒绝出网下放行的 LLM host |
| `OPENCODE_CLI` | OpenCode CLI | 若你的安装暴露的二进制名不是 `pi`，可覆盖 |

## 4. 单次运行（直连密钥方式）

```bash
python run_opencode.py --prompt "Create hello.py that prints 'Hello from CubeSandbox' and run it."
```

密钥通过 `sandbox.commands.run(..., envs=...)` 逐命令传入，因此只在该 exec 调用生命周期内存在——不会写入沙箱内的持久文件。

> **安全提示：** 直连方式保持出网开放，被攻陷的 Agent 可能外泄注入的密钥。共享/生产环境请使用第 6 步的保险柜方式：默认拒绝出网 + 链路上注入密钥。

三个脚本都使用 `--mode json` 运行 OpenCode，默认输出精简转写（助手文本、工具调用、失败项）。加 `--raw`（或设 `OPENCODE_STREAM_RAW=1`）可查看原始 JSONL 事件流。

## 5. 暂停 / 恢复（会话持久化）

```bash
python resume_opencode.py
```

第一轮让 OpenCode 写入 `/workspace/plan.md`，然后调用 `sandbox.pause()` 对 VM 打快照。脚本通过 `Sandbox.connect(sandbox_id)` 重新连接，验证 `/workspace/plan.md` 与 OpenCode 状态目录（`/root/.pi/agent`）仍然保留，再运行第二轮继续工作。沙箱生命周期通过 `try/finally` 手动管理（不用 context manager），避免 `__exit__` 提前 kill 掉沙箱导致 pause 失效。

## 6. 限制出网 + 密钥保险柜（推荐共享集群使用）

```bash
python network_policy.py
```

- 默认拒绝出网——只有 LLM host（`OPENCODE_LLM_HOST`）可达。
- CubeEgress 在链路上附加 `Authorization: Bearer` 头注入 provider key，沙箱内 `printenv` 永远看不到真实密钥，只能看到占位值。
- OpenCode 基于 Node.js，忽略系统 CA 库，因此脚本设置了 `NODE_EXTRA_CA_CERTS`，让 OpenCode 信任 CubeEgress 拦截 CA；否则 vault 路径会报 `Connection error`。若你的镜像把 CA 放在别处，可通过 `OPENCODE_NODE_EXTRA_CA_CERTS` 覆盖。
- 任何其他目的地都会返回 `403 Forbidden - CubeEgress`。

如果 Agent 需要访问额外 host（包仓库、MCP 服务器等），请添加对应放行规则，或预装进模板。

## 排错

| 现象 | 可能原因 | 处理 |
|---|---|---|
| preflight 报 `pi: command not found` | CLI 变更后未重建模板，或二进制名不同 | 重建镜像并重新注册模板，或设置 `OPENCODE_CLI` |
| provider 鉴权失败 | 密钥未传入（直连）或缺少 inject 规则（vault） | 传 `envs={...}` 或修正规则的 `sni`/`host` |
| `403 Forbidden - CubeEgress` | 默认拒绝且无匹配放行规则 | 把 LLM host（及所需其他 host）加入规则 |
| vault 下 OpenCode 报 `Connection error` / TLS 失败 | Node 运行时忽略系统 CA 库，不信任 CubeEgress CA | 示例已设 `NODE_EXTRA_CA_CERTS`；若 CA 在别处用 `OPENCODE_NODE_EXTRA_CA_CERTS` 覆盖 |
| 就绪探针超时 | 基础镜像缺少 envd | 确认 `FROM ghcr.io/tencentcloud/cubesandbox-base:...` |
| `pause()` / `connect()` 报错 | 平台版本过低不支持快照 | 升级 CubeSandbox 平台 |

## 参考

- 集成指南：[`docs/guide/integrations/opencode.md`](../../docs/guide/integrations/opencode.md)
- 快照 / 克隆 / 回滚：[`docs/guide/snapshot-rollback-clone.md`](../../docs/guide/snapshot-rollback-clone.md)
- 网络 / 出网策略示例：[`examples/network-policy`](../network-policy)
- OpenCode coding agent：<https://www.npmjs.com/package/@earendil-works/pi-coding-agent>
