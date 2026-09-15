# 接入一个新的 Agent

这份文档记录的是**实际做过一遍之后**的流程。`pi` 是照它加进来的：一个 profile、一个
Dockerfile、一个入口脚本、一个日志解析器，共享代码一行没改，评测跑到 `decision: pass`。

先读第一节，因为工作量差别很大，把它当成一个数字会浪费一天。

## 工作量取决于这个 Agent 已经会做什么

| 这个 Agent 是… | 你要写 | 为什么 |
| --- | --- | --- |
| **成品 CLI** — `codex`、`pi`、`claude`、`aider` | profile + Dockerfile + 入口脚本 | 它已经有工具循环、文件编辑和 shell，你只是告诉框架怎么启动它、怎么把它接到我们的网关上 |
| **一次模型调用** — 自己写个脚本发 prompt、打印回复 | 上面这些 **加上工具循环** | 它能回答问题但碰不了文件；任何要求产出文件的任务都会失败 |
| **新协议** — JSON-RPC、ACP、socket 握手 | 上面这些 **加上一个 bridge** | 一次性的命令行表达不了握手过程，见 `docker/dsh-agent/acp-runner.mjs` |

`pi` 属于第一类，而且是这类里最省事的：它有 `--print`（跑完就退出）和 `--mode json`
（结构化输出）。

## 三层，各管一件事

```
profiles/agents/<id>.yaml    这个 Agent 是什么          （数据）
docker/<id>/Dockerfile       怎么把它装出来            （构建）
docker/<id>/entrypoint.sh    怎么把它接到我们的网关     （翻译）
```

**前两层是模板活。第三层才需要读那个 Agent 自己的文档。**

---

## 第一层：profile

`profiles/agents/pi.yaml` 是完整例子。字段分四组：

```yaml
id: pi
label: pi
image_repository: ai-native-pi-agent     # 镜像仓库
agent_version: 0.85.1                    # tag 由它派生，不能和镜像分叉

build:                                   # 怎么构建
  dockerfile: docker/pi-agent/Dockerfile
  version_arg: PI_VERSION                # 这个 Dockerfile 管版本参数叫什么

protocol: chat                           # 跟模型说什么协议
workdir: /workspace
trace:
  parser: pi                             # 怎么读它的日志
entrypoint: /usr/local/bin/ai-native-pi-entrypoint
writable_paths:                          # 容器根只读，写别处必须列出来
  - /opt/pi-home
```

几个容易搞错的点：

**tag 是派生的，不要写死。** `image_repository` + `agent_version` 拼出
`ai-native-pi-agent:0.85.1`。想跑第二个版本就改 `agent_version`，两个版本会并存而不是
互相覆盖。

**`protocol` 要跟实际相符。** 它要和 `AGENT_WIRE_APIS`（`src/ai_native_evals/providers.py`）
对得上。没登记的 adapter 会**退回 Codex 的协议列表**，那是猜测；猜错的话运行会在模型调用
那一步才失败。

**`writable_paths` 漏了会很像 Agent 的 bug。** 容器根文件系统是只读的。Agent 在
`$HOME` 或自己的配置目录下写东西，必须列出来，否则容器内以权限错误失败。

**`trace.parser` 不写也能跑。** 退化成 `generic`：仍然产生事件，只是识别不出类型。
已有 `codex`、`dsh-acp`、`pi`、`generic`。

**`build` 块可以整个不写。** 意思是"没东西要构建"——已经发布好的镜像，或者在本仓库外构建
的。这是正常状态，构建工具会如实报告。

---

## 第二层：Dockerfile

三步：装 CLI、建目录、指入口。

```dockerfile
ARG NODE_BASE_IMAGE=node:22-bookworm
FROM ${NODE_BASE_IMAGE}

ARG PI_VERSION=0.85.1
LABEL ai.native.agent="pi" \
      ai.native.agent.version="$PI_VERSION"

RUN npm install --global --no-fund --no-audit \
        @earendil-works/pi-coding-agent@${PI_VERSION} \
    && pi --version \
    && mkdir -p /opt/pi-home \
    && chown -R node:node /opt/pi-home

COPY docker/pi-agent/entrypoint.sh /usr/local/bin/ai-native-pi-entrypoint
RUN chmod 0755 /usr/local/bin/ai-native-pi-entrypoint

USER node
WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/ai-native-pi-entrypoint"]
```

**基础镜像由这个 Dockerfile 决定，不是框架统一给的。** 这一点踩过坑：曾经把
`NODE_BASE_IMAGE` 统一设成 gateway 镜像传给所有 Agent，结果 DSH 的 Dockerfile 在非特权
用户下跑 `npm install -g`，报 `EACCES`。Dockerfile 里 `ARG` 的默认值是它自己的事。

`node:22-bookworm` 自带 npm；`ai-native-codex-agent` 镜像**没有 npm**（它只从 Node 镜像
里拷了 `node` 和 `git`，Codex 是靠解包预下载的 tarball 装的）。要 `npm install` 就用前者。

**`LABEL` 里的版本比 tag 可信。** tag 是可以被重新指向的，label 跟着镜像走。

---

## 第三层：入口脚本（真正花时间的地方）

**每个 Agent 指定"去哪调模型"的方式都不一样。这一层的工作就是把我们的约定翻译成它自己的配置格式。**

框架传进容器的变量：

| 变量 | 内容 |
| --- | --- |
| `EVAL_TASK_PROMPT_FILE` | 任务提示词的**文件路径** |
| `EVAL_GATEWAY_URL` | 网关地址，如 `http://llm-gateway:8080/v1` |
| `EVAL_GATEWAY_API_KEY` | 网关的 key |
| `EVAL_MODEL` | 解析出的模型 id |
| `EVAL_REASONING_EFFORT` | 推理强度 |
| `EVAL_SYSTEM_PROMPT_FILE` | 系统提示词文件路径，可能不存在 |
| `EVAL_MCP_SERVERS_FILE` | MCP 配置路径 |

### 提示词必须当文件读

**永远不要把提示词内容放进 argv。** 提示词是 Markdown，含引号、反引号和 `$(...)`，
shell 会当成代码执行。这不是假设：曾经把提示词内联传参，一个表格的行丢了，Agent 把
缺失的名字当成了请求本身的歧义。

入口脚本里用 `cat "$prompt_file"` 读出来再交给 CLI。

### 翻译网关地址：pi 的教训

**这是最容易卡住的一步，`pi` 在这上面卡了很久，值得完整讲。**

第一版入口脚本设了 `OPENAI_BASE_URL`，结果每次调用**大约 20 毫秒就报
`Request timed out.`**。

20 毫秒不是超时，是**本地解析失败**。查下去发现：

- 那个变量在 pi 里是 **Azure 专用**的，不是通用开关；
- pi 从**自己的模型目录**决定请求发去哪；
- 当时 `models-store.json` 是 `{}`——它根本不认识我们的模型。

正解是在 pi 的配置目录里**把我们的网关声明成一个 provider**：

```json
{
  "providers": {
    "eval-gateway": {
      "baseUrl": "http://llm-gateway:8080/v1",
      "api": "openai-completions",
      "apiKey": "gateway",
      "models": [{ "id": "gpt-5.6-luna" }]
    }
  }
}
```

凭据单独写在 `auth.json`（放命令行里会出现在进程列表）。这两个文件写在
`$PI_CODING_AGENT_DIR` 下，而那个目录是 `writable_paths` 里的一项。

**通用的排查顺序：**

1. 先单独确认网络通不通：容器里 `curl` 一下 `$EVAL_GATEWAY_URL/models`。
   通了就说明问题不在网络。
2. 看**失败有多快**。几十毫秒 = 本地解析失败（找不到 provider / 模型 / 凭据）；
   几十秒 = 真的网络超时。
3. 去读那个 Agent 自己的配置文档，找"自定义 provider / 自定义端点"那一节。
   不要猜环境变量名。

### 结构化输出

有 `--mode json` 这类开关就用它。日志里一行一个 JSON 对象，解析器直接能吃。
纯文本输出要看它的措辞才能判断发生了什么，很脆。

### 长连接协议

DSH 讲 ACP，是一个长期存活的 JSON-RPC 会话，一次性命令行表达不了。这种情况下要写一个
runner 完成握手并驱动一个回合，见 `docker/dsh-agent/acp-runner.mjs`。

---

## 第四层（可选）：日志解析器

**只有当这个 Agent 的日志格式没人认识时才写。不写就用 `generic`，出事件但识别不出类型。**

要加的话，在 `src/ai_native_evals/adapters/events.py` 里写一个
`normalize_<agent>_event`，然后在 `normalize_event_lines` 里按 parser 名分派。

**关键是要用真实日志，不要用猜的。** 跑一次真的，把原始日志打出来，照着写。

`pi` 这里又踩了两个坑，都是测试抓出来的：

**坑一：同一个事件类型装着不同的东西。** `message_start` / `message_end` 既承载用户的
提示词，也承载助手回复和工具结果。**要靠 `role` 区分，不能只看事件类型。** 第一版同时
写了类型映射和角色映射，字典先命中，角色分支成了死代码，所有用户消息都被归成了
未分类事件。

**坑二：失败的调用要以 `error` 上报。** pi 失败时的形态是一个带
`stopReason: "error"` 和 `errorMessage` 的 `message_end`，而且**没有内容**。
如果先按 role 归档，它会被记成 `agent_message`——一个根本没够到模型的运行，看起来像是
回答过了。

**坑三：事件类型有白名单。** `_NORMALIZED_TYPES` 里没有的类型会被**静默降级**为
`provider_event`。加新类型时要记得加进去，否则映射写对了也看不到效果。

**未知事件要能落地。** 解析器遇到不认识的类型应该产出 `provider_event` 并带上原始内容，
这样 Agent 升级加了新事件类型时，Process 阶段不会变成空的。

---

## 不用改的东西

```
tools/build-sandbox-images.ps1      构建工具从 profile 读，不认识任何具体 Agent（Windows）
tools/build-sandbox-images.py       同上，Linux/macOS 入口
src/ai_native_evals/runs/*.py       运行器只看 profile 的 entrypoint / environment
src/ai_native_evals_console/*.py    控制台是投影，读 registry
```

`tests/test_pi_agent.py` 里有几条测试专门锁住这一点——构建工具里出现某个 Agent 的名字
就会失败。`tests/test_agent_versions.py` 把这条规则同时套在两个入口上：只锁
`.ps1` 的话，Agent 名字可以从没上锁的 Python 入口加回去。

---

## 一条命令的检查流程

```powershell
# 1. profile 能解析吗
.\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src');
from pathlib import Path
from ai_native_evals.agents.profile import load_agent_profile_file
p = load_agent_profile_file(Path('profiles/agents/pi.yaml'))
print(p.image, p.trace_parser)"

# 2. 构建（不用改任何共享代码）
.\tools\eval.ps1 build -Agent pi

# 3. 静态检查：profile、镜像在不在
#    Environment 页面 → 静态检查

# 4. 真实测试：起容器、问一句话，证明入口脚本 / 凭据 / 模型链路真的通
#    Environment 页面 → 真实测试

# 5. 跑一个真任务
.\.venv\Scripts\ai-native-evals.exe run execute <task-id> --agent pi
```

**第 4 步不能跳过。** 静态检查全绿只说明文件齐备，说明不了 Agent 能启动。
第 5 步也不能省：**能回答问题**和**能完成任务**是两种能力，只有第二种会被评分。

---

## 加一个版本

不用写任何东西：

```powershell
.\tools\eval.ps1 build -Agent pi -Version 0.86.0
```

或者在 Environment 页面上从已有的版本里选。版本列表是从**已构建的镜像**读出来的，
不是文件里维护的一份清单——没人构建过的版本选不了，因为选它一定启动失败。

---

## 检查表

1. 判断属于三类里的哪一类，这决定后面所有事情。
2. `profiles/agents/<id>.yaml`
3. `docker/<id>/Dockerfile`，tag 是 `<image_repository>:<agent_version>`
4. `docker/<id>/entrypoint.sh`——**读提示词文件、翻译网关配置**
5. 要的话加日志解析器（先用 `generic` 跑一次看原始日志）
6. `.\tools\eval.ps1 build -Agent <id>`
7. Environment 页面：静态检查 → 真实测试
8. 跑一个真任务

第 4 步是唯一无法预测工作量的地方，因为每个 Agent 的配置格式都不一样。
