# Novel Agent OS：24 小时常驻部署

该部署把前端、后端和 Redis 作为独立容器运行，所有服务使用
`restart: unless-stopped`。后端收到停止信号后有 120 秒用于保存连续写作状态，重启后由
应用恢复未完成任务。

## 数据安全约定

- 默认使用现有 `backend/data/novel_os.db`，目录直接挂载到容器 `/app/data`。
- 上传的大纲和本地对象存储统一保存在 `backend/data/blobstore`。
- Redis 使用命名卷 `redisdata` 和 AOF 持久化。
- PostgreSQL 档使用既有卷名 `pgdata`，切换 Compose 文件不会删除旧卷。
- 停止脚本只执行 `stop`。不要执行 `docker compose down -v`，该命令会删除命名卷。
- Provider 密钥只放在 `backend/.env`；该文件以只读文件挂载，既不会进入镜像，也不会
  出现在容器环境变量列表中。

## 第一次启动（保留当前 SQLite 数据）

1. 安装 Docker Desktop，并启用 Docker Compose v2。
2. 在 Docker Desktop 设置中启用登录后自动启动；机器休眠期间任何本地服务都会暂停。
3. 检查 `backend/.env` 中的 Provider 地址、模型和密钥。不要把该文件提交到版本库。
4. 在 PowerShell 中运行：

```powershell
Set-Location 'F:\kelaode\Data\Agents\zhongji8633\wudi8633\nos_staging'
.\deploy\start-24h.ps1
```

脚本会先停止容器内后端并备份 SQLite，然后构建镜像、执行 Alembic 迁移并启动服务。
浏览器访问 `http://127.0.0.1:5173`。后端健康检查可访问
`http://127.0.0.1:8000/health`，经前端代理的检查地址是
`http://127.0.0.1:5173/backend-health`。

## Windows 原生常驻（无需 Docker）

没有安装 Docker 的 Windows 机器可直接使用原生常驻档。首次启动会先备份 SQLite、执行
迁移、构建正式前端包，然后注册当前用户的 `NovelAgentOS-24H` 计划任务。监督进程每 10 秒
检查前后端健康；连续异常会自动重启对应进程。计划任务采用“登录触发 + 每分钟幂等巡检”，
监督进程本身被终止后也会在一分钟内接管既有健康进程，并在下次登录后恢复。Provider 密钥
仍只由后端从 `backend/.env` 读取，不会写入任务参数或进程命令行。

```powershell
Set-Location 'F:\kelaode\Data\Agents\zhongji8633\wudi8633\nos_staging'

# 安装/更新并立即启动原生 24H 服务
.\deploy\start-native-24h.ps1

# 查看任务、进程和 HTTP 健康状态
.\deploy\status-native-24h.ps1

# 优雅停止并禁用登录自启；数据和任务配置均保留
.\deploy\stop-native-24h.ps1
```

原生档日志与运行状态位于 `deploy/runtime`；该目录不存储 Provider 密钥。机器休眠时本地程序
仍会暂停，恢复唤醒后计划任务和应用内恢复机制会接续未完成的连续写作。

## 常用运维命令

```powershell
# 查看容器健康状态和最近日志
.\deploy\status-24h.ps1

# 优雅停止；保留数据库、运行状态和所有卷
.\deploy\stop-24h.ps1

# 代码更新后重建；如果已经另行备份，可跳过本次快照
.\deploy\start-24h.ps1 -SkipBackup
```

Compose 日志启用了轮转，避免 24 小时运行时无限占用磁盘。默认仅监听本机回环地址。
Redis/PostgreSQL 位于不具备外网出口的内部数据网络；只有后端连接数据网络，前端无法
直接访问数据库。后端同时连接 Web 网络，因此仍可调用外部模型 Provider。
如需局域网访问，在项目根目录创建只含部署参数的 `.env`：

```dotenv
FRONTEND_BIND_ADDRESS=0.0.0.0
FRONTEND_PORT=5173
BACKEND_BIND_ADDRESS=127.0.0.1
BACKEND_PORT=8000
```

对公网开放时，应在前端之前增加带 TLS 和访问控制的反向代理，不要直接暴露后端端口。

## 可选 PostgreSQL 档

SQLite 档最稳妥地保留现有数据。需要 PostgreSQL 时，先完成数据迁移，再运行：

```powershell
.\deploy\start-24h.ps1 -WithPostgres
```

脚本会在 `deploy/secrets/postgres_password.txt` 生成随机密码，并用 Docker secret 同时提供给
PostgreSQL 和后端。该文件已被忽略规则排除。PostgreSQL 状态命令与停止命令也要带上
`-WithPostgres`：

```powershell
.\deploy\status-24h.ps1 -WithPostgres
.\deploy\stop-24h.ps1 -WithPostgres
```

注意：启用 PostgreSQL 不会自动把 SQLite 内容复制过去。确认迁移完成前，请继续使用默认档。
如果机器上已有旧版 `pgdata` 卷而密钥文件不存在，启动脚本会安全退出，不会用新密码覆盖或
误接旧库。此时应先把旧数据库的原密码写入 `deploy/secrets/postgres_password.txt`，或完成
受控的导出/导入后再切换。

## 健康与恢复

- Redis、后端和前端都有容器级健康检查；前端仅在后端健康后启动。
- 后端入口先运行 `alembic upgrade head`，迁移成功后才接受请求。
- API 容器使用 `init: true` 和 120 秒停止宽限期，便于持续写作任务完成停机持久化。
- SSE 与长时间生成请求关闭 Nginx 缓冲，读取超时为 24 小时。
- 如果容器反复重启，先运行 `status-24h.ps1`，检查迁移、Provider 或磁盘错误。
