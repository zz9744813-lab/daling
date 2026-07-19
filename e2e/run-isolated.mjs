import { spawn } from 'node:child_process'
import { createWriteStream } from 'node:fs'
import { mkdir, rm } from 'node:fs/promises'
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryDir = resolve(frontendDir, '..')
const backendDir = resolve(repositoryDir, 'backend')
const stateDir = resolve(frontendDir, '.e2e-state')
const backendPort = Number(process.env.E2E_ISOLATED_BACKEND_PORT || 18_080)
const frontendPort = Number(process.env.E2E_ISOLATED_FRONTEND_PORT || 15_173)
const backendOrigin = `http://127.0.0.1:${backendPort}`
const frontendOrigin = `http://127.0.0.1:${frontendPort}`
const dbPath = resolve(stateDir, 'novel-os-e2e.db')
const blobPath = resolve(stateDir, 'blobstore')
// Playwright deletes its outputDir at the beginning of a run, so backend logs
// live beside (not inside) test-results and survive both passing and failing runs.
const logDir = resolve(frontendDir, 'e2e-logs')
const runStamp = new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-')
const logPath = resolve(logDir, `backend-${runStamp}.log`)
const children = []
let logStream

function assertInsideStateDir(path) {
  const pathFromState = relative(stateDir, resolve(path))
  if (
    !isAbsolute(stateDir) ||
    pathFromState === '..' ||
    pathFromState.startsWith(`..${sep}`) ||
    isAbsolute(pathFromState)
  ) {
    throw new Error(`拒绝清理隔离目录之外的路径：${path}`)
  }
}

async function endpointExists(url) {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1_000) })
    return response.status > 0
  } catch {
    return false
  }
}

async function waitFor(url, child, label, timeoutMs = 60_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.exitCode != null) {
      throw new Error(`${label} 在就绪前退出，exitCode=${child.exitCode}`)
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_000) })
      if (response.ok) return
    } catch {
      // 服务仍在启动。
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250))
  }
  throw new Error(`${label} 未在 ${timeoutMs}ms 内就绪：${url}`)
}

function launch(label, command, args, options) {
  const child = spawn(command, args, {
    ...options,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  children.push(child)
  child.stdout.on('data', (chunk) => {
    process.stdout.write(`[${label}] ${chunk}`)
    logStream?.write(`[${new Date().toISOString()}] [${label}:stdout] ${chunk}`)
  })
  child.stderr.on('data', (chunk) => {
    process.stderr.write(`[${label}] ${chunk}`)
    logStream?.write(`[${new Date().toISOString()}] [${label}:stderr] ${chunk}`)
  })
  return child
}

async function terminate(child) {
  if (child.exitCode != null) return
  child.kill('SIGTERM')
  await Promise.race([
    new Promise((resolvePromise) => child.once('exit', resolvePromise)),
    new Promise((resolvePromise) => setTimeout(resolvePromise, 5_000)),
  ])
  if (child.exitCode == null) child.kill('SIGKILL')
}

async function cleanup() {
  await Promise.all([...children].reverse().map(terminate))
  assertInsideStateDir(dbPath)
  assertInsideStateDir(blobPath)
  await rm(stateDir, { recursive: true, force: true })
  if (logStream) {
    const stream = logStream
    logStream = undefined
    await new Promise((resolvePromise) => stream.end(resolvePromise))
  }
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => {
    void cleanup().finally(() => process.exit(130))
  })
}

let exitCode = 1
try {
  if (await endpointExists(`${backendOrigin}/health`)) {
    throw new Error(`隔离后端端口已被占用：${backendOrigin}`)
  }
  if (await endpointExists(frontendOrigin)) {
    throw new Error(`隔离前端端口已被占用：${frontendOrigin}`)
  }

  assertInsideStateDir(dbPath)
  assertInsideStateDir(blobPath)
  await rm(stateDir, { recursive: true, force: true })
  await mkdir(stateDir, { recursive: true })
  await mkdir(logDir, { recursive: true })
  logStream = createWriteStream(logPath, { flags: 'a', encoding: 'utf8' })
  logStream.write(`[${new Date().toISOString()}] isolated runner started\n`)
  process.stdout.write(`[isolated-runner] backend log: ${logPath}\n`)

  const python = process.env.E2E_PYTHON || (process.platform === 'win32' ? 'python.exe' : 'python3')
  const sqlitePath = dbPath.replaceAll('\\', '/')
  const allowWorker = process.env.E2E_ALLOW_WORKER === '1'
  const backend = launch(
    'isolated-backend',
    python,
    [
      '-m',
      'uvicorn',
      allowWorker ? 'app.main:app' : 'isolated_backend:app',
      '--app-dir',
      allowWorker ? backendDir : resolve(frontendDir, 'e2e'),
      '--host',
      '127.0.0.1',
      '--port',
      String(backendPort),
    ],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        APP_ENV: 'test',
        DATABASE_URL: `sqlite+aiosqlite:///${sqlitePath}`,
        OBJECT_STORAGE_LOCAL_DIR: blobPath,
        REDIS_URL: 'redis://127.0.0.1:1/15',
        DEFAULT_PROVIDER: 'openai_compatible',
        DEFAULT_MODEL: '',
        DEFAULT_BASE_URL: '',
        DEFAULT_API_KEY: '',
        OPENAI_COMPATIBLE_MODEL: '',
        OPENAI_COMPATIBLE_BASE_URL: '',
        OPENAI_COMPATIBLE_API_KEY: '',
        ANTHROPIC_MODEL: '',
        ANTHROPIC_BASE_URL: '',
        ANTHROPIC_API_KEY: '',
        ENABLE_ONLINE_LEARNING: 'false',
        ENABLE_REAL_PROVIDER_SMOKE: 'false',
      },
    },
  )
  await waitFor(`${backendOrigin}/health`, backend, '隔离后端')

  const vite = launch(
    'production-preview',
    process.execPath,
    [
      resolve(frontendDir, 'node_modules/vite/bin/vite.js'),
      'preview',
      '--host',
      '127.0.0.1',
      '--port',
      String(frontendPort),
      '--strictPort',
    ],
    {
      cwd: frontendDir,
      env: {
        ...process.env,
        VITE_API_TARGET: backendOrigin,
      },
    },
  )
  await waitFor(frontendOrigin, vite, '生产前端预览')

  const playwright = launch(
    'playwright',
    process.execPath,
    [
      resolve(frontendDir, 'node_modules/@playwright/test/cli.js'),
      'test',
      'e2e/ui-mutating.e2e.ts',
      '--project=desktop-system-chrome',
      '--project=mobile-390-system-chrome',
    ],
    {
      cwd: frontendDir,
      env: {
        ...process.env,
        E2E_BASE_URL: frontendOrigin,
        E2E_API_TARGET: backendOrigin,
        E2E_DB_PATH: dbPath,
        E2E_PYTHON: python,
        E2E_SKIP_WEBSERVER: '1',
        E2E_MUTATING: '1',
      },
    },
  )
  exitCode = await new Promise((resolvePromise, rejectPromise) => {
    playwright.once('error', rejectPromise)
    playwright.once('exit', (code) => resolvePromise(code ?? 1))
  })
} finally {
  await cleanup()
}

process.exitCode = exitCode
