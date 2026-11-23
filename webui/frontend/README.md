# AgentNodes Web UI

## 开发启动

需要 Python 3.10+、Node.js 22.12+ 和 npm。以下命令使用 PowerShell，在项目根目录执行：

```powershell
python -m agentnodes.interfaces.cli serve --frontend-dev --an-workspace .
```

Python 将启动后端和 Vite；访问 `http://127.0.0.1:5173/chat`，修改前端源码后由 Vite
热更新。后端默认监听 `127.0.0.1:8766`。按 Ctrl+C 停止本命令启动的进程。
可用 `--port` 和 `--frontend-port` 分别修改端口，Vite 自动代理到指定的后端端口；
端口占用会报错，不会自动切换。此模式仅绑定本机，不能与 `--api-only` 或后端
`--reload` 同时使用；它不提供 Python 后端热重载。

首次使用时需先安装后端和前端依赖；`npm ci` 会重建 `node_modules`，请确认没有其他
进程正在使用该目录：

```powershell
python -m pip install -e ".[webui-api,llm]"
Set-Location .\webui\frontend
npm ci
```

也可以分别启动后端和前端，在项目根目录运行 `python -m agentnodes.interfaces.cli serve
--an-workspace .`，然后在另一个终端运行：

```powershell
Set-Location .\webui\frontend
npm run dev
```

访问 Vite 显示的地址（默认 `http://localhost:5173`）。`/api` 默认代理到后端 8766 端口；
如果后端使用其他端口，在运行 `npm run dev` 的终端设置例如
`$env:API_TARGET = 'http://127.0.0.1:9000'`。

## 登录和操作

- 用户数据库为空时首次登录请求会创建 `admin` 账户。启动前可设置
  `AGENTNODES_ADMIN_PASSWORD` 为自己选择的密码；否则生成的初始密码保存在
  `~/.agentnodes/admin_password.txt`。环境变量不会更改已存在账户的密码。
- 注册默认关闭；仅在需要开放注册时，启动后端前设置
  `$env:AGENTNODES_API_ALLOW_REGISTRATION = '1'`。
- 登录成功后进入 Chat，创建或选择会话，在输入框发送消息；空闲时 `Enter` 发送，
  `Shift+Enter` 换行，生成过程中 `Enter` 或停止按钮可停止生成。
- 在设置中配置 LLM 后才能发起模型对话。请勿将 API 密钥提交到仓库。
- 侧栏可进入工作流编排；右侧面板可查看当前会话的目标、上下文和 Trace。
  部分历史 Study、Run、因子及策略界面依赖当前后端未挂载的业务接口，
  不应视为已完成的功能。

## 构建和验证

在 `webui/frontend` 目录执行 `npm run lint`、`npm test` 和 `npm run build`。
构建会生成并覆盖 `webui/static`，之后可通过后端的 8766 端口访问页面。
服务器默认仅绑定本机；如需对外开放，应先配置安全的认证和网络访问策略。
