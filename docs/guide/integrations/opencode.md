---
title: OpenCode Agent Integration Guide
author: Hoyun123
date: 2026-07-05
tags:
  - integration
  - OpenCode-agent
  - coding-agent
  - agent
lang: en-US
---

# OpenCode Agent Integration Guide

[中文文档](../../zh/guide/integrations/opencode.md)

Run the [OpenCode coding agent](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
— a terminal-native AI coding agent — inside CubeSandbox MicroVMs. This guide
covers image build, key injection, egress control, and snapshot-based session
persistence, and pairs with the runnable
[`examples/opencode-integration`](https://github.com/TencentCloud/CubeSandbox/tree/master/examples/opencode-integration)
project.

## Integration Target and Version

| Component | Version |
|---|---|
| OpenCode CLI | Latest release (pin via `--build-arg OPENCODE_VERSION=x.y.z`) |
| Node.js | 24 (installed via NodeSource) |
| CubeSandbox base image | `ghcr.io/tencentcloud/cubesandbox-base:2026.16` |
| E2B SDK (host driver) | `e2b` (latest) |
| CubeSandbox platform | `>= 0.3.0` (pause/resume) / `>= 0.4.0` (CubeEgress credential vault) |

## Prerequisites

- A running CubeSandbox deployment; CubeAPI reachable at `http://<node>:3000`.
- `cubemastercli` on `$PATH`, connected to the cluster.
- Docker on the build workstation, plus a registry the Cube nodes can pull from.
- An LLM provider API key. This guide uses Moonshot as an example; any
  OpenAI-compatible endpoint works via `MOONSHOT_BASE_URL` / provider env vars.
- Python 3.10+ for the host driver scripts.

## Why Run OpenCode Inside a Sandbox

OpenCode is a terminal agent that edits files, runs commands, and installs
packages. Running it directly on a workstation blends the agent's blast radius
with your dev environment. Running it inside CubeSandbox gives you:

| Concern | CubeSandbox provides |
|---|---|
| **Isolation** | KVM MicroVM per session, dedicated guest kernel |
| **Reproducibility** | Every session boots from the same template snapshot |
| **Fast spin-up** | Sub-60 ms cold start, so N-parallel agents are cheap |
| **Long tasks** | `sandbox.pause()` snapshots VM + rootfs; resume later |
| **Key hygiene** | CubeEgress injects the auth header on the wire — the VM never sees the real key |
| **Egress audit** | Every request to the LLM API is recorded in the egress audit log |

## Integration Steps

### 1. Build the template image

The image stacks Node.js 24 and the OpenCode CLI on top of `cubesandbox-base`,
so envd is already listening on `:49983`.

```dockerfile
# examples/opencode-integration/Dockerfile
ARG CUBE_BASE_IMAGE=ghcr.io/tencentcloud/cubesandbox-base:2026.16
FROM ${CUBE_BASE_IMAGE}

ARG NODE_MAJOR=24

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git gnupg jq less procps python3 python3-pip ripgrep \
    && curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g opencode-ai@latest \
    && opencode --version \
    && npm cache clean --force \
    && rm -rf /root/.npm /var/lib/apt/lists/*

WORKDIR /workspace
EXPOSE 49983
```

Build and push:

```bash
docker build --platform linux/amd64 \
  -t opencode-cube:latest \
  /root/opencode-integration
```

![OpenCode running inside CubeSandbox](../../zh/guide/integrations/assets/image-0.png)

### 2. Register as a Cube template

```bash
cubemastercli tpl create-from-image \
  --image opencode-cube:latest \
  --writable-layer-size 2G \
  --expose-port 49983 \
  --probe 49983

cubemastercli tpl list
```

Reasons registration may fail:

```text
docker build creates opencode-cube:latest
    └── only this server can see it
            │
            ▼
    Cube platform tries to pull "opencode-cube:latest"
            │
            └── defaults to Docker Hub
            └── times out (network issue or does not exist)
            │
            ▼
    Need to push to a registry the Cube platform can reach
            │
            └── Tencent Cloud TCR (same region, fast internal network)
```

Once the job reaches `READY`, note the `template_id` — you pass it to every
`Sandbox.create()` call. A `4G` writable layer suits medium tasks; bump to `8G+`
if the agent installs large toolchains.

### 2.1 Register with Tencent Cloud Container Registry

```bash
# 1. Log in (provided)
docker login project1.tencentcloudcr.com --username <your-tcr-username> --password <your-tcr-password>

# 2. Tag
docker tag opencode-cube:latest project1.tencentcloudcr.com/hoyun_pj/opencode-cube:latest

# 3. Push
docker push project1.tencentcloudcr.com/hoyun_pj/opencode-cube:latest

# 4. Register as a Cube template
cubemastercli tpl create-from-image \
  --image project1.tencentcloudcr.com/hoyun_pj/opencode-cube:latest \
  --writable-layer-size 2G \
  --expose-port 49983 \
  --probe 49983

cubemastercli tpl list
```

### 3. Wire up the host driver

```bash
mkdir -p /root/opencode-integration
cd /root/opencode-integration
cat > .env << 'EOF'
# CubeSandbox API config
E2B_API_URL=http://127.0.0.1:3000
E2B_API_KEY=e2b_000000

# OpenCode template ID (from step 2)
CUBE_TEMPLATE_ID=tpl-581d9c926f9947acb5c46562

# LLM provider config (OpenCode supports multiple providers)
OPENCODE_PROVIDER=moonshot
OPENCODE_MODEL=kimi-latest

# API key (direct mode, for dev/test)
MOONSHOT_API_KEY=sk-xxx

# Optional: compatible gateway (e.g. DeepSeek)
# MOONSHOT_BASE_URL=https://api.moonshot.cn/v1
EOF

cat > requirements.txt << 'EOF'
e2b-code-interpreter
python-dotenv
EOF

pip install -r requirements.txt
```

| Variable | Where it flows | Notes |
|---|---|---|
| `E2B_API_URL` | Local process | CubeAPI address (`http://<node>:3000`) |
| `E2B_API_KEY` | Local process | Any non-empty string in local dev |
| `CUBE_TEMPLATE_ID` | `Sandbox.create(template=...)` | From step 2 |
| `OPENCODE_PROVIDER` / `OPENCODE_MODEL` | OpenCode CLI flags | Provider and model selection, e.g. `moonshot` / `kimi-latest` |
| `MOONSHOT_API_KEY` | `envs=...` (direct) or CubeEgress inject (vault) | Provider key |
| `MOONSHOT_BASE_URL` | Passed into the exec env | Moonshot or compatible gateway address |
| `OPENCODE_LLM_HOST` | `network_policy.py` | LLM host allowed under default-deny egress, e.g. `api.moonshot.cn` |

![Host driver env setup](../../zh/guide/integrations/assets/image-4.png)

### 4. Runtime configuration and API key injection

OpenCode is invoked headlessly: `--print` means it processes the prompt and exits
(no TUI, which would hang on the E2B exec channel), together with an explicit
provider/model and `--mode json` for a machine-readable JSONL event stream.
`--approve` is a boolean switch that trusts project-local files inside the
sandbox for this run; the prompt is passed as the trailing positional argument.
Two key-flow flavors share the same template:

**Direct flavor** — forward the key per command. `e2b`'s `commands.run(envs=...)`
puts the environment into the exec envelope, not into a persistent file inside
the VM, so the key lives only for the lifetime of that command. `pi` is the
default binary name exposed by the `opencode-ai` npm package; override via
`OPENCODE_CLI` or use `env_utils.opencode_cli()` if your installation differs:

```python
result = sandbox.commands.run(
    "cd /workspace && pi --print --mode json --provider moonshot "
    "--model kimi-latest --approve 'do something'",
    envs={"MOONSHOT_API_KEY": key},
    user="root",
    timeout=900,
)
```

**Vault flavor** — keep the key out of the VM entirely (see step 6).

The example scripts parse this JSONL and print a concise transcript by default
(assistant text, tool calls, and any failures); pass `--raw` (or set
`OPENCODE_STREAM_RAW=1`) to see the raw event stream.

### 5. Session persistence (pause / resume)

```bash
python resume_opencode.py
```

This mirrors the [snapshot / clone / rollback](../snapshot-rollback-clone.md)
engine at the SDK layer:

- `sandbox.pause()` snapshots the running VM (memory + rootfs) and frees compute.
- `Sandbox.connect(sandbox_id)` resumes with `/workspace`, OpenCode's state
directory (`/root/.pi/agent`), and every other file intact.

> **Lifecycle caveat:** manage the sandbox lifecycle with `try/finally`, not a
> `with Sandbox.create(...)` context manager. On `__exit__` the context manager
> kills the sandbox, which would undo the pause. The example creates the sandbox
> explicitly and only calls `sandbox.kill()` in `finally`.

```python
sandbox = Sandbox.create(template=template_id, timeout=1800)
try:
    run_turn(sandbox, prompt_1)          # writes /workspace/plan.md
    sandbox_id = sandbox.pause() or sandbox.sandbox_id
    sandbox = Sandbox.connect(sandbox_id)
    assert_state_survived(sandbox)       # /workspace + /root/.pi/agent intact
    run_turn(sandbox, prompt_2)          # continues the work
finally:
    sandbox.kill()
```

### 6. Network and egress policy (credential vault)

`network_policy.py` demonstrates the recommended pattern for shared clusters:
default-deny egress plus on-the-wire key injection.

```python
# Credential injection uses the native cubesandbox SDK (see security-proxy.md).
from cubesandbox import Sandbox, Rule, Match, Action, Inject

host = "api.moonshot.cn"
rules = [
    Rule(
        name="allow_moonshot_llm",
        match=Match(scheme="https", sni=host, host=host),
        action=Action(allow=True, audit="metadata", inject=[
            Inject(header="Authorization", secret=MOONSHOT_API_KEY, format="Bearer ${SECRET}"),
        ]),
    ),
]

sandbox = Sandbox.create(
    template=CUBE_TEMPLATE_ID,
    allow_internet_access=False,   # default-deny; the rule's host is auto-allowed
    network={"rules": rules},
)
```

Effect:

- `printenv MOONSHOT_API_KEY` inside the sandbox shows only a placeholder.
- Every request to the LLM host gets the `Authorization: Bearer ...` header
  attached on the wire.
- Anything else is dropped by CubeVS at L3/L4 (`allow_internet_access=False`)
  and never leaves the sandbox.
- Every allow / deny decision lands in the egress audit log.

Moonshot and other OpenAI-compatible providers use `Authorization: Bearer`. If a
provider does not accept a header-injected key, fall back to the direct flavor
(`envs=...`) — but never write the key into a persistent file inside the sandbox.

## Use Cases and Best Practices

- **Isolated development.** Run the coding agent inside the sandbox so its file
  edits and shell commands cannot touch the host.
- **Execute agent-generated code and collect results.** Have the agent write to
  `/workspace`, then read artifacts back via `sandbox.files` or `commands.run`.
- **Checkpoint / resume long tasks.** Use `pause()` + `connect()` to snapshot a
  long refactor and resume later, or fork multiple task variants off one snapshot.
- **Preinstall heavy dependencies** into the template rather than fetching them
  at runtime, especially under a default-deny egress policy.

## Key Code Snippets

### Headless OpenCode invocation

```python
from env_utils import build_opencode_env, opencode_cli

opencode_env = build_opencode_env()
cmd = (
    f"cd /workspace && {opencode_cli()} --print --mode json "
    "--provider moonshot --model kimi-latest "
    "--approve 'Inspect the project, run app.py, and summarize the result.'"
)
result = sandbox.commands.run(cmd, envs=opencode_env, user="root", timeout=900)
```

### Preflight version check

```python
from env_utils import opencode_cli

version = sandbox.commands.run(f"{opencode_cli()} --version", timeout=60)
```

## Caveats

- **Node.js version.** OpenCode needs a recent Node runtime; the base image ships
  an older apt Node, so always install via NodeSource (the Dockerfile does).
- **Agent state directory.** `/root/.pi/agent` holds OpenCode's session cache.
  Keep it empty in the image to avoid leaking sessions across tenants; it is
  created at build time but not populated with any credentials.
- **Direct-flavor key persistence.** With the direct flavor (`envs=`) the key is
  scoped to the exec call, but OpenCode may cache provider credentials under its
  state dir (`/root/.pi/agent/`), which survives `pause()` / `resume()`. For
  strict isolation prefer the vault flavor (`network_policy.py`), where the key
  never enters the VM.
- **CubeEgress CA (Node).** For the vault flavor the sandbox must trust the
  CubeEgress root CA, which the base image installs into the system bundle.
  OpenCode runs on Node.js, which ignores the system store, so `network_policy.py`
  also sets `NODE_EXTRA_CA_CERTS` (override via `OPENCODE_NODE_EXTRA_CA_CERTS`)
  — without it the vault path fails with `Connection error`.
- **Egress side-effects.** Tasks that `npm install` or fetch MCP tools need those
  hosts allowed or preinstalled into the template.
- **Interactive TTY features.** The OpenCode TUI is not available over the E2B
  protocol. Use headless `--print --mode json` and drive multi-turn conversations
  from the host script.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `pi: command not found` in preflight | Template not rebuilt after CLI change | Rebuild the image, re-register the template |
| Provider auth failure | Key not forwarded (direct) or missing inject rule (vault) | Pass `envs={...}` or fix the rule's `sni`/`host` |
| `403 Forbidden - CubeEgress` | Default-deny with no matching allow rule | Add the LLM host (and any extra hosts) to the rules |
| `Connection error` / TLS failure from OpenCode (vault) | OpenCode's Node runtime ignores the system CA store, so it won't trust the CubeEgress CA | The example sets `NODE_EXTRA_CA_CERTS`; override with `OPENCODE_NODE_EXTRA_CA_CERTS` if the CA lives elsewhere |
| Template creation stuck in `PULLING` | Registry unreachable from Cube nodes | Push to a registry the cluster can reach; supply auth if needed |
| Readiness probe timeout | Base image without envd | Ensure `FROM ghcr.io/tencentcloud/cubesandbox-base:2026.16` |
| `pause()` / `connect()` errors | Platform too old for snapshots | Upgrade the CubeSandbox platform |

## References

- Runnable example: [`examples/opencode-integration`](https://github.com/TencentCloud/CubeSandbox/tree/master/examples/opencode-integration)
- Bring Your Own Image: [`docs/guide/tutorials/bring-your-own-image.md`](../tutorials/bring-your-own-image.md)
- Template from image: [`docs/guide/tutorials/template-from-image.md`](../tutorials/template-from-image.md)
- Snapshot / Clone / Rollback: [`docs/guide/snapshot-rollback-clone.md`](../snapshot-rollback-clone.md)
- Credential vault + egress control: [`docs/guide/security-proxy.md`](../security-proxy.md)
- OpenCode coding agent: <https://www.npmjs.com/package/@earendil-works/pi-coding-agent>
