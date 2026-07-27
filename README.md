# mc-automation

面向 KLPBBS 与 MineBBS 的规则感知型 Minecraft 服务器宣传顶贴自动化。程序执行站点提供的签到、资源查询、官方顶贴道具和 KLPBBS 推广任务流程，不发送论坛回复或刷评分。

## 工作方式

- GitHub Actions 每小时检查所有启用站点，也支持 `workflow_dispatch` 手动运行。
- GitHub Actions 建立系统级 Cloudflare WARP 全隧道路由并检查 `warp=on`；WARP 建立或检查失败时终止任务。
- KLPBBS 只有排名大于阈值（默认 `8`）后才会使用官方顶贴道具。
- MineBBS 不使用排名作为门槛，两次成功顶贴默认间隔 16 小时。
- 每个站点每轮最多完成一次购买/使用事务；状态丢失时先保守地执行登录、签到和读取检查。
- 检测到登录限制、验证码、Cloudflare/WAF 挑战或未知页面时，该站点立即停止并报告 `manual_intervention`；连续三次挑战会暂停 24 小时。
- KLPBBS 使用独立的 `cloudscraper` 会话；其他站点继续使用普通 `requests` 会话，所有响应仍经过统一挑战检测。
- KLPBBS 推广任务参考 `klpAutomation`，每批最多用 20 个 worker 并行点击站点校验过的同源推广链接；代理失败时继续下一批，HTTP 成功不等于任务进度。每批后由主线程读取任务页 `#csc_1` 的实际百分比，直到完成领奖或代理池耗尽。只有推广点击绕过 WARP 路由选择，代理源下载、任务申请、状态检查和领奖仍走 WARP。
- MineBBS ESA 滑块可选使用 `nodriver` 读取滑块与轨道的 DOM 几何信息，并通过 CDP 鼠标事件拖到末端；不截图、不调用 AI。WDSJFWQ 图片验证码仍可独立启用 OpenAI-compatible 视觉模型。

## 配置

在仓库 `Settings -> Secrets and variables -> Actions` 中配置：

### Secrets

| 名称 | 用途 |
|---|---|
| `KLPBBS_USERNAME` / `KLPBBS_PASSWORD` | KLPBBS 登录凭据 |
| `KLPBBS_THREAD_ID` | KLPBBS 宣传帖 ID |
| `MINEBBS_USERNAME` / `MINEBBS_PASSWORD` | MineBBS 登录凭据；用户名必须与目标帖作者一致 |
| `MINEBBS_THREAD_ID` | MineBBS 宣传帖数字 ID、`slug.ID` 短名或完整帖子 URL；运行时统一提取数字 ID |
| `AI_SOLVER_ENDPOINT` | OpenAI-compatible endpoint，建议填 base URL 如 `https://api.example.com/v1` |
| `AI_SOLVER_API_KEY` | OpenAI-compatible API key |

### Variables

| 名称 | 默认值 | 用途 |
|---|---:|---|
| `KLPBBS_ENABLED` | `false` | 启用 KLPBBS 适配器 |
| `MINEBBS_ENABLED` | `false` | 启用 MineBBS 适配器 |
| `RANK_THRESHOLD` | `8` | KLPBBS 排名门槛；排名大于该值时尝试顶贴 |
| `KLPBBS_PROMOTION_ENABLED` | `false` | 启用 KLPBBS 官方推广任务 |
| `KLPBBS_PROMOTION_URL` | 空 | KLPBBS 账号的同源推广链接，例如 `https://klpbbs.com/?fromuid=123456`；启用推广时必填 |
| `KLPBBS_PROMOTION_VISIT_DELAY_SECONDS` | `0.5` | 有效代理批次后的间隔；全失败批次立即跳过，最低 `0.5` 秒 |
| `PAID_BUMP_COOLDOWN_SECONDS` | `3600` | KLPBBS 付费顶贴冷却 |
| `MINEBBS_BUMP_INTERVAL_HOURS` | `16` | MineBBS 顶贴间隔 |
| `MINEBBS_ESA_SLIDER_ENABLED` | `false` | 启用基于 `nodriver` DOM 几何的 MineBBS ESA 非 AI 滑块处理 |
| `MINEBBS_BROWSER_EXECUTABLE_PATH` | 空 | 可选 Chromium-family 浏览器路径；空值由 `nodriver` 自动查找，Windows 无 Chrome 时再尝试系统 Edge |
| `AI_SOLVER_ENABLED` | `false` | 启用 WDSJFWQ 图片验证码 AI 处理 |
| `AI_SOLVER_MODEL` | 空 | 视觉模型名称，例如 `gpt-4o-mini` 或兼容服务提供的模型名 |
| `AI_SOLVER_TIMEOUT_SECONDS` | `60` | 单次模型请求超时 |
| `AI_SOLVER_MAX_ATTEMPTS` | `1` | 模型请求最大尝试次数，最高 5 |
| `AI_SOLVER_WDSJFWQ_CAPTCHA_ENABLED` | 空 | 单独控制 WDSJFWQ 图片验证码；空值继承 `AI_SOLVER_ENABLED` |

账号、密码、Cookie、CSRF token、`AI_SOLVER_ENDPOINT` 和 `AI_SOLVER_API_KEY` 不得写入 Variables、工作流文件、状态缓存或日志。Actions workflow 从 Secrets 注入 endpoint 和 key，日志会把它们加入脱敏列表。

## 本地运行

CLI 会自动读取当前目录的 `.env`，系统环境变量优先于 `.env` 中的同名配置。可从
`.env.example` 复制一份本地配置；该文件已被 `.gitignore` 排除。

```powershell
python -m pip install -e ".[dev]"
python -m mc_automation.cli --dry-run
python -m pytest
```

需要 MineBBS ESA 非 AI 滑块处理时安装可选依赖，并确保系统已安装 Chrome 或 Chromium：

```powershell
python -m pip install -e ".[dev,browser]"
# 只有自动查找不到浏览器时才需要配置：
$env:MINEBBS_BROWSER_EXECUTABLE_PATH="C:\Program Files\Google\Chrome\Application\chrome.exe"
```

启用 WDSJFWQ 图片验证码模型：

```text
AI_SOLVER_ENABLED=true
AI_SOLVER_ENDPOINT=https://api.example.com/v1
AI_SOLVER_API_KEY=...
AI_SOLVER_MODEL=gpt-4o-mini
```

WDSJFWQ 会下载当前会话里的 `captcha.png`，用模型提示词要求只返回 `{"code":"...","confidence":...}`，随后随机生成 `PlayerNNNNNN` 用户名提交点赞表单。MineBBS ESA 只在显式启用 `MINEBBS_ESA_SLIDER_ENABLED` 后对 GET/HEAD 挑战启动 Chromium，读取 `#aliyunCaptcha-sliding-slider` 与轨道元素的边界，并生成从成功人工样本归纳的约 465 ms 单调轨迹：前段 1 px 试探、平滑加速、约 7-10 px 向上漂移，并在末端继续拖过轨道后释放。每个点通过 `nodriver` 的低层 CDP `Input.dispatchMouseEvent` 发送，并按绝对单调时钟调度，避免浏览器协议调用开销逐点累积。只有挑战标记消失且浏览器正常清理后才同步 Cookie 和 User-Agent，并只重试一次原 GET/HEAD。ESA 路径不会向模型发送截图或其他数据，挑战 POST 不会重放。轨迹单元测试只能验证输入契约；ESA 是否放行必须以实时验证响应和挑战页面消失为准。

## 挑战处理边界

通用模型只处理 WDSJFWQ 自定义图片验证码。模型输出不是严格 JSON、验证码不是 3-8 位字母数字时，不提交表单。MineBBS ESA 仅使用页面已渲染的 DOM 几何信息；滑块或轨道缺失、尺寸异常、拖动后页面仍显示挑战、登录限制或 Cloudflare/WAF 策略拒绝时，程序会失败关闭并要求人工介入，不伪造 token，也不自动解封或绕过限流。

## 安全与故障处理

适配器遇到未知 HTML、所有权不匹配、余额/库存/CSRF 不明确或站点拒绝时，不执行购买、使用或顶贴副作用。运行结果写入 Actions 日志和 Job Summary，但不会包含凭据、Cookie、响应正文或扩展密钥。

运行时还会输出逐步 JSONL 日志，覆盖配置、状态、各站点动作、HTTP、WDSJFWQ 验证码图片下载/AI 识别/表单提交/计数确认，以及 MineBBS ESA 浏览器/DOM/拖动/清理流程。URL 仅保留 scheme、host、port 和 path；日志只记录字段名、状态、数量及耗时，不记录表单值、验证码文本、图片 Base64、AI endpoint/key 或原始响应正文。

普通连接依赖 GitHub Actions 中的系统级 WARP 全隧道路由，不使用应用层 `HTTP_PROXY`/`HTTPS_PROXY`。KLPBBS 推广点击则使用独立的无凭据 Session，设置 `trust_env=False` 并显式传入动态代理；不携带登录 Cookie、CSRF token 或 Referer，不关闭 TLS 校验，也不跟随重定向。`KLPBBS_PROMOTION_URL` 必须与 `KLPBBS_BASE_URL` 同源。旧配置 `KLPBBS_PROMOTION_PROXY_TARGET_URL` 和 `KLPBBS_PROMOTION_TARGET_MARKER` 已删除。
