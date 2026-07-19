# Production-preview E2E

这组用例使用系统 Chrome 检查正式 `dist`，不会执行 `playwright install`，也不会下载浏览器。

默认前提：

- 后端已经在 `http://127.0.0.1:8000` 运行；
- 正式预览使用 `http://127.0.0.1:5173`；
- 真实大纲为 `F:\小说\实验\顶级\《人间种》.docx`；
- 数据库中至少已有一个项目。

运行：

```powershell
Set-Location 'F:\kelaode\Data\Agents\zhongji8633\wudi8633\nos_staging\frontend'
$env:PLAYWRIGHT_CHROME_PATH = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
npm run test:e2e
```

可选变量：

- `E2E_BASE_URL`：前端地址；
- `E2E_API_TARGET`：由测试启动 preview 时使用的后端代理地址；
- `E2E_OUTLINE_PATH`：真实 DOCX/TXT/MD 大纲；
- `E2E_SKIP_WEBSERVER=1`：只连接已经运行的 preview；
- `E2E_HEADED=1`：显示系统 Chrome 窗口。

测试只允许一个非 GET 请求：`POST /api/projects/outline/inspect`。它只解析上传文件并返回预检结果，不创建项目、不保存大纲、不调用模型。创作舱用例要求所有请求均为只读。

## 隔离数据库真实写回验收

下面的命令会自动启动一个使用临时 SQLite 数据库的后端和独立端口的正式前端预览，运行完毕后停止两个进程并删除临时数据库。隔离后端会显式清空所有模型地址、模型名和 API Key，并只在该测试进程中抑制异步章节 Worker 的派发；启动、暂停、恢复、停止仍执行真实 Continuous 服务与数据库状态迁移，因此既能验证持久化 API 合约，也不会请求外部模型。

```powershell
npm run test:e2e:isolated
```

这条用例会在浏览器中完成并回读：

- 从首页新建一次性项目，并真实上传含 500+ 来源节点的 Markdown 大纲；
- 通过 GET API 核对项目、大纲原文、索引覆盖和提示词已经持久化；
- 修改项目提示词，整页刷新后再次从后端读取；
- 启动、暂停、恢复、停止 24H 任务，并核对请求只命中 Continuous API，没有普通 WorkSession、Boss command 或手动 Pipeline 回退；
- 验证证据控制台跨接口页读取全部来源节点、分批渲染和唯一锚点搜索；
- 停止任务、删除一次性项目并确认 404。即使测试中途失败，隔离数据库也会由运行器清理。

可用 `E2E_PYTHON` 指定后端 Python，可用 `E2E_ISOLATED_BACKEND_PORT` 与 `E2E_ISOLATED_FRONTEND_PORT` 覆盖默认隔离端口。

每次运行都会把后端 stdout/stderr 连同 UTC 接收时间写入 `e2e-logs/backend-*.log`，即使失败也保留，便于核对状态竞争与 SQL 锁栈。仅在专门回归真实 Worker 生命周期时可设置 `E2E_ALLOW_WORKER=1`；该模式仍使用空模型配置和隔离数据库，但会允许后台 Worker 实际取得租约，因此不作为默认 UI 验收模式。

产物：

- `test-results/`：每个浏览器尺寸的截图、诊断 JSON、失败 trace/video；
- `playwright-report/`：可离线查看的 HTML 报告。
