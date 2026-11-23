# Python 管理前端热更新启动方案

> 日期：2026-11-23；性质：实施设计。

## 现状与目标

- `serve` 默认由 [merged.py](../../src/agentnodes/interfaces/cli/commands/merged.py)
  调用 Web UI 服务；[server.py](../../src/agentnodes/interfaces/cli/commands/server.py)
  的 `--reload` 只重载 Python 后端，不启动 Vite。
- [vite.config.ts](../../webui/frontend/vite.config.ts) 提供前端热更新，默认将 `/api`
  代理到 `127.0.0.1:8766`。Python 服务静态产物不包含 Vite 热更新。
- 本次增加显式开发模式 `serve --frontend-dev`：Python 负责启动和监测后端及 Vite，
  浏览器访问 Vite 地址；前端变更由 Vite 热更新。普通 `serve` 保持不变。
  Node.js 和已安装的前端依赖仍是前置条件，不自动安装或修改依赖。

## 契约

1. 仅允许在本机地址启动开发模式；Vite 使用 `127.0.0.1`，默认 5173，可通过
   `--frontend-port` 指定。后端端口沿用 `--port`；`API_TARGET` 明确指向实际后端端口，
   避免自定义端口时代理到错误服务。Vite 使用严格端口，端口占用须报错而非静默换端口。
2. 复用 Python CLI 的现有工作区和可选目标数据库、hypotheses、静态目录参数。
   若与 `--api-only` 或后端 `--reload` 同时使用，明确拒绝；后者涉及重载子进程的
   跨平台生命周期，本次只承诺前端热更新，不改变后端运行语义。
3. 不经 shell，直接启动当前 Python 解释器和已安装的 Node/Vite；启动失败、缺少 Node、
   缺少 Vite 或任一进程提前退出均给出错误，不能输出成功状态。只有受本命令创建的
   子进程可被停止，不影响已有进程；退出时回收这些直接子进程。
4. 前端目录默认定位到源码仓库的 `webui/frontend`；缺失时明确提示，不假定所有
   Python 包安装方式都包含前端源码。
5. 用户可继续使用两个终端的既有开发方式；生产构建与静态文件输出行为不变。

## 验证与边界

- 在 CLI 可观察边界测试参数传递、端口/代理、缺依赖、子进程失败和退出清理，
  同时保留原有 `serve` 和 `--api-only` 行为。
- 实机验收须确认两个端口就绪、打开 Vite 页面、修改前端组件后浏览器更新，
  Ctrl+C 后本次进程退出；不触碰其他进程与数据库。受环境限制时明确记录未验收。
- 回滚只撤回本批新增 CLI 选项、启动管理及其测试/文档，不回退其他工作区改动。
- 不安装依赖、不重写现有前端、无需新增持久化目录，不实现后端代码热重载。

## 执行记录（2026-11-23）

- 新增 `serve --frontend-dev` 和 `--frontend-port`；普通 `serve` 与 `--api-only`
  保持原有分发。运行时使用当前 Python 及 PATH 中的 Node，Vite 直接使用本地依赖。
- CLI 启动、退出和错误路径采用子进程替身测试；server、parser、dispatch 与参数
  回归合计 74 项通过。分层检查上行违规为 0；修改文件 lint 通过（CLI 原有 I001
  在基线已存在，本批单独排除）。
- 无需启动 HTTP 服务的 Vite 转换检查：使用离线缓存的 Node 22.16.0 对实际
  `LoginPage.tsx` 执行开发转换，确认产物包含 `import.meta.hot` 与 React Fast Refresh
  runtime；这不能代替浏览器中真实文件变更和 WebSocket 更新的验收。
- 使用真实的短生命周期子进程验证模拟中断后的直接子进程回收；此测试不启动服务，
  也不能代替真实终端 Ctrl+C 与服务进程树的验收。
- 尚未完成真实浏览器热更新和进程退出验收：当前环境无法通过 CIM 枚举现有进程；
  默认 Node 为 20.18.1，低于前端 README 指定的 22.12+，但离线缓存中的 22.16.0
  可用。未启动或停止现有服务，不将替身测试或转换检查写成实机通过。
- 后续在允许安全查看现有进程、符合 Node 版本要求的环境中，按 §验证与边界完成
  服务就绪、源码修改触发浏览器热更新及 Ctrl+C 清理的实机验证。
