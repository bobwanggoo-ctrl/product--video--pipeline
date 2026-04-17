#!/usr/bin/env bun
/**
 * Navigation AI - 生图/生视频/文本模型客户端
 * 支持：邮箱验证码登录、Token刷新、图片上传、查询模型分组、创建异步任务、轮询任务结果
 */

import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import { homedir } from "node:os";
import path from "node:path";
import process from "node:process";

// ─── 常量 ─────────────────────────────────────────────────────────────────────

const BASE_URL = "http://yswg.love:15091/api/admin";
const CONFIG_DIR = path.join(homedir(), ".baoyu-skills", "navigation-ai");
const TOKEN_FILE = path.join(CONFIG_DIR, "auth.json");
const CONFIG_FILE = path.join(CONFIG_DIR, "config.json");
const POLL_INTERVAL_MS = 3000;
const POLL_MAX_WAIT_MS = 900_000; // 15分钟（LLM 任务排队时间可能较长）

// ─── 类型定义 ─────────────────────────────────────────────────────────────────

interface AuthData {
  email: string;
  token: string;
  refreshToken: string;
  expireMinutes: number;
  savedAt: number; // timestamp ms
}

interface NavConfig {
  appId: string | null;
}

interface ApiResult<T> {
  status: string;
  message: string;
  data: T;
  requestId: string | null;
}

interface LoginTokenVo {
  token: string;
  refreshToken: string;
  expireMinutes: number;
  userId: number;
  email: string;
}

/**
 * status 实际为数字：0=PENDING 1=RUNNING 2=SUCCESS 3=FAILED 4=CANCELED
 */
interface TaskVo {
  id: string;
  status: number;
  failReason: string | null;
  responseJson: unknown;
  queuePosition: number | null;
  estimatedWaitSeconds: number | null;
  finishTime: string | null;
}

const TASK_STATUS = {
  PENDING: 0,
  RUNNING: 1,
  SUCCESS: 2,
  FAILED: 3,
  CANCELED: 4,
} as const;

const TASK_STATUS_LABEL: Record<number, string> = {
  0: "PENDING（排队中）",
  1: "RUNNING（处理中）",
  2: "SUCCESS",
  3: "FAILED",
  4: "CANCELED",
};

interface StreamChunk {
  done: boolean;
  chunk: string | null;
  conversationId: string | null;
  userMessageId: string | null;
  assistantMessageId: string | null;
  usage: unknown;
  error: string | null;
}

interface ModelGroupVo {
  id: number;
  groupName: string;
  groupCode: string;
  modelName: string;
  label: string;
  modelType: "TEXT" | "IMAGE" | "VIDEO" | null;
  points: number;
  enabled: boolean;
  remark: string | null;
  imageGenOptions: { imageCountEnabled: boolean; imageRadioEnabled: boolean } | null;
}

type GenTaskStatus = "SCHEDULED" | "GENERATING" | "SUCCESS" | "FAILED" | "CANCELLED";

interface AiGenTaskListVo {
  id: string;
  taskType: number | null;         // 1=图片 2=视频
  status: GenTaskStatus | number;  // 接口可能返回字符串枚举或数字
  requestTime: string | null;
  userName: string | null;
  inputText: string | null;
  outPutFile: string[] | null;
  aiToolName: string | null;
  groupId: number | null;
  imageRatio: string | null;
  generateSuccess: boolean | null;
  failReason: string | null;
  generateDuration: number | null;
  totalTokens: number | null;
  pics: number | null;
  isNightGenerate: boolean | null;
}

interface CreditAccountVo {
  currentCredit: number;
  totalObtained: number;
  totalConsumed: number;
  lastGrantTime: string | null;
  nextGrantTime: string | null;
}

interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

interface CliArgs {
  command: string | null;
  // auth
  email: string | null;
  code: string | null;
  // config
  appId: string | null;
  // generate / chat
  // 使用 string 保留原始值，避免 JS Number 对 19 位大整数的精度丢失
  groupId: string | null;
  endpointId: string | null;
  prompt: string | null;
  aspectRatio: string | null;
  imageFile: string | null;
  imageUrl: string | null;
  imageCount: number;
  // chat
  messages: ChatMessage[];
  systemPrompt: string | null;
  userMessage: string | null;
  // stream
  content: string | null;
  conversationId: string | null;
  imageUrls: string[];
  // task
  taskId: string | null;
  taskIds: string[];
  // list-groups
  keyword: string | null;
  modelType: "TEXT" | "IMAGE" | "VIDEO" | null;
  pageSize: number;
  // list-tasks
  taskStatus: GenTaskStatus | null;
  taskType: number | null;   // 1=图片 2=视频
  // extra
  params: Record<string, unknown>;
  jsonOutput: boolean;
  noWait: boolean;
  help: boolean;
}

// ─── 命令行解析 ───────────────────────────────────────────────────────────────

function printUsage(): void {
  console.log(`
Navigation AI Skill - 生图 / 生视频 / 文本模型客户端

Usage:
  bun main.ts <command> [options]

Commands:
  ── 认证 ──
  send-code          发送邮箱验证码
  login              邮箱验证码登录（自动保存 token）
  refresh            刷新 token
  check-auth         检查当前 token 是否有效
  logout             删除本地 token

  ── 配置 ──
  set-config         保存配置（appId 等）
  show-config        查看当前配置

  ── 模型 ──
  list-groups        分页查询模型分组（用于获取 groupId）
  list-tasks         查询我的任务历史（生成记录）
  credit             查看当前积分账户余额

  ── 生成 ──
  generate           创建图片/视频生成任务并轮询结果
  stream             流式文本对话（SSE，实时输出，服务端维护历史）
  chat               调用文本模型（异步任务版，单轮或多轮）
  upload             上传图片，返回 URL
  task               查询任务详情

Options:
  ── 认证 ──
  --email <email>           邮箱地址
  --code <code>             邮箱验证码

  ── 配置 ──
  --app-id <id>             appId（联系管理员获取，保存后无需每次传）

  ── generate / chat 公共 ──
  --group-id <id>           模型分组 ID（必选，与 endpoint-id 二选一）
  --endpoint-id <id>        直接指定接口 ID

  ── generate 专用 ──
  --prompt <text>           提示词
  --ar <ratio>              宽高比（如 1:1 / 16:9 / 9:16）
  --image-file <path>       本地图片（自动上传后使用）
  --image-url <url>         已有图片 URL
  --image-count <n>         生成数量（默认 1，最大 10）

  ── stream 专用 ──
  --content <text>          消息内容（与 --user 等价）
  --conversation-id <id>   对话 ID（续接上一轮，首次不传自动创建）
  --image-url-add <url>    附带图片 URL（可多次，仅当前轮有效）

  ── chat 专用 ──
  --system <text>           系统提示词（system role）
  --user <text>             用户消息（单轮快速调用）
  --message <role:text>     追加消息（可多次），如 --message user:你好

  ── list-groups ──
  --keyword <text>          关键字过滤（分组名/模型名）
  --page-size <n>           每页数量（默认 20）

  ── task ──
  --task-id <id>            任务 ID

  ── 通用 ──
  --param <key=value>       透传额外参数到 params（可多次）
  --json                    以 JSON 格式输出结果
  -h, --help                显示帮助

Examples:
  # 查询可用模型分组
  bun main.ts list-groups

  # 保存 appId（联系管理员获取）
  bun main.ts set-config --app-id 2014153035982319618

  # 文生图
  bun main.ts generate --group-id 5 --prompt "一只猫坐在月亮上" --ar 1:1

  # 图生图（去水印）
  bun main.ts generate --group-id 5 --image-file ./ref.jpg --prompt "去掉图中交叉线条的水印"

  # 文本模型（单轮）
  bun main.ts chat --group-id 13 --system "你是AI助手" --user "你好"

  # 文本模型（多轮）
  bun main.ts chat --group-id 13 --message system:你是AI助手 --message user:你好 --message assistant:你好！ --message user:继续

  # 上传图片
  bun main.ts upload --image-file ./photo.jpg
`);
}

function parseArgs(argv: string[]): CliArgs {
  const args: CliArgs = {
    command: null,
    email: null,
    code: null,
    appId: null,
    groupId: null,
    endpointId: null,
    prompt: null,
    aspectRatio: null,
    imageFile: null,
    imageUrl: null,
    imageCount: 1,
    messages: [],
    systemPrompt: null,
    userMessage: null,
    content: null,
    conversationId: null,
    imageUrls: [],
    taskId: null,
    taskIds: [],
    keyword: null,
    modelType: null,
    pageSize: 20,
    taskStatus: null,
    taskType: null,
    params: {},
    jsonOutput: false,
    noWait: false,
    help: false,
  };

  const positional: string[] = [];
  let i = 0;
  while (i < argv.length) {
    const arg = argv[i];
    switch (arg) {
      case "--email": args.email = argv[++i]; break;
      case "--code": args.code = argv[++i]; break;
      case "--app-id": args.appId = argv[++i]; break;
      case "--group-id": args.groupId = argv[++i]; break;
      case "--endpoint-id": args.endpointId = argv[++i]; break;
      case "--prompt": args.prompt = argv[++i]; break;
      case "--ar": args.aspectRatio = argv[++i]; break;
      case "--image-file": args.imageFile = argv[++i]; break;
      case "--image-url": args.imageUrl = argv[++i]; break;
      case "--image-count": args.imageCount = parseInt(argv[++i], 10); break;
      case "--system": args.systemPrompt = argv[++i]; break;
      case "--user": args.userMessage = argv[++i]; break;
      case "--content": args.content = argv[++i]; break;
      case "--conversation-id": args.conversationId = argv[++i]; break;
      case "--image-url-add": args.imageUrls.push(argv[++i]); break;
      case "--message": {
        const raw = argv[++i];
        const colonIdx = raw.indexOf(":");
        if (colonIdx > 0) {
          args.messages.push({
            role: raw.slice(0, colonIdx) as ChatMessage["role"],
            content: raw.slice(colonIdx + 1),
          });
        }
        break;
      }
      case "--task-id": args.taskIds.push(argv[++i]); break;
      case "--no-wait": args.noWait = true; break;
      case "--keyword": args.keyword = argv[++i]; break;
      case "--model-type": args.modelType = argv[++i].toUpperCase() as "TEXT" | "IMAGE" | "VIDEO"; break;
      case "--page-size": args.pageSize = parseInt(argv[++i], 10); break;
      case "--task-status": args.taskStatus = argv[++i].toUpperCase() as GenTaskStatus; break;
      case "--task-type": args.taskType = parseInt(argv[++i], 10); break;
      case "--json": args.jsonOutput = true; break;
      case "-h":
      case "--help": args.help = true; break;
      case "--param": {
        const pair = argv[++i];
        const eqIdx = pair.indexOf("=");
        if (eqIdx > 0) {
          args.params[pair.slice(0, eqIdx)] = pair.slice(eqIdx + 1);
        }
        break;
      }
      default:
        positional.push(arg);
    }
    i++;
  }

  if (positional.length > 0) {
    args.command = positional[0];
  }

  return args;
}

// ─── 工具函数 ─────────────────────────────────────────────────────────────────

function log(msg: string): void {
  console.error(`[navigation-ai] ${msg}`);
}

function fail(msg: string, exitCode = 1): never {
  console.error(`[navigation-ai] ERROR: ${msg}`);
  process.exit(exitCode);
}

async function apiRequest<T>(
  method: string,
  endpoint: string,
  options: {
    token?: string;
    refreshToken?: string;
    body?: unknown;
    formData?: FormData;
  } = {}
): Promise<ApiResult<T>> {
  const url = `${BASE_URL}${endpoint}`;
  const headers: Record<string, string> = { Accept: "application/json" };

  if (options.token) headers["Authorization"] = `Bearer ${options.token}`;
  if (options.refreshToken) headers["Refresh-Token"] = options.refreshToken;

  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    // safeStringify：groupId/endpointId 以字符串存储，序列化时去掉引号还原为 JSON 数字字面量，防止大整数精度丢失
    body = JSON.stringify(options.body).replace(/"(groupId|endpointId)":"(\d+)"/g, '"$1":$2');
  }

  const resp = await fetch(url, { method, headers, body });
  const text = await resp.text();

  let json: ApiResult<T>;
  try {
    // 响应中 >=16 位大整数先转成字符串，防止 JS Number 精度丢失
    const safeText = text.replace(/:\s*(\d{16,})\b/g, ':"$1"');
    json = JSON.parse(safeText) as ApiResult<T>;
  } catch {
    throw new Error(`HTTP ${resp.status}: non-JSON response: ${text.slice(0, 200)}`);
  }

  if (!resp.ok || json.status !== "200") {
    throw new Error(`API error [${json.status}]: ${json.message}`);
  }

  return json;
}

// ─── 持久化：Token ────────────────────────────────────────────────────────────

async function loadAuth(): Promise<AuthData | null> {
  try {
    await access(TOKEN_FILE);
    return JSON.parse(await readFile(TOKEN_FILE, "utf-8")) as AuthData;
  } catch {
    return null;
  }
}

async function saveAuth(data: AuthData): Promise<void> {
  await mkdir(CONFIG_DIR, { recursive: true });
  await writeFile(TOKEN_FILE, JSON.stringify(data, null, 2), "utf-8");
}

async function deleteAuth(): Promise<void> {
  try {
    const { unlink } = await import("node:fs/promises");
    await unlink(TOKEN_FILE);
  } catch {
    // 文件不存在也没关系
  }
}

function isTokenExpiringSoon(auth: AuthData): boolean {
  const expireMs = auth.expireMinutes * 60 * 1000;
  return Date.now() - auth.savedAt > expireMs - 5 * 60 * 1000;
}

async function getValidToken(): Promise<{ token: string; auth: AuthData }> {
  const auth = await loadAuth();
  if (!auth) {
    fail("未登录，请先运行: bun main.ts send-code --email <your-email>  然后 login");
  }

  if (!isTokenExpiringSoon(auth)) {
    return { token: auth.token, auth };
  }

  log("token 即将过期，尝试刷新...");
  try {
    const result = await apiRequest<LoginTokenVo>("POST", "/web/auth/token-refresh", {
      token: auth.token,
      refreshToken: auth.refreshToken,
    });
    const newAuth: AuthData = {
      email: auth.email,
      token: result.data.token,
      refreshToken: result.data.refreshToken,
      expireMinutes: result.data.expireMinutes,
      savedAt: Date.now(),
    };
    await saveAuth(newAuth);
    log("token 刷新成功");
    return { token: newAuth.token, auth: newAuth };
  } catch (err) {
    log(`token 刷新失败: ${err}`);
    await deleteAuth();
    fail("token 已失效，请重新登录: bun main.ts send-code --email <your-email>");
  }
}

// ─── 持久化：Config（appId 等）────────────────────────────────────────────────

async function loadConfig(): Promise<NavConfig> {
  try {
    await access(CONFIG_FILE);
    return JSON.parse(await readFile(CONFIG_FILE, "utf-8")) as NavConfig;
  } catch {
    return { appId: null };
  }
}

async function saveConfig(cfg: NavConfig): Promise<void> {
  await mkdir(CONFIG_DIR, { recursive: true });
  await writeFile(CONFIG_FILE, JSON.stringify(cfg, null, 2), "utf-8");
}

async function getAppId(argAppId: string | null): Promise<string | undefined> {
  if (argAppId) return argAppId;
  const cfg = await loadConfig();
  return cfg.appId ?? undefined;
}

// ─── 命令：认证 ───────────────────────────────────────────────────────────────

async function cmdSendCode(args: CliArgs): Promise<void> {
  if (!args.email) fail("请传入 --email <邮箱地址>");
  log(`发送验证码到: ${args.email}`);
  await apiRequest("POST", "/web/auth/email-codes", { body: { email: args.email } });
  console.log(`✓ 验证码已发送至 ${args.email}，请查收邮件`);
}

async function cmdLogin(args: CliArgs): Promise<void> {
  if (!args.email) fail("请传入 --email <邮箱地址>");
  if (!args.code) fail("请传入 --code <验证码>");

  const result = await apiRequest<LoginTokenVo>("POST", "/web/auth/email-sessions", {
    body: { email: args.email, code: args.code },
  });

  const auth: AuthData = {
    email: args.email,
    token: result.data.token,
    refreshToken: result.data.refreshToken,
    expireMinutes: result.data.expireMinutes,
    savedAt: Date.now(),
  };
  await saveAuth(auth);

  if (args.jsonOutput) {
    console.log(JSON.stringify({ success: true, email: auth.email, expireMinutes: auth.expireMinutes }));
  } else {
    console.log(`✓ 登录成功，token 已保存（有效期 ${auth.expireMinutes} 分钟）`);
  }
}

async function cmdRefresh(): Promise<void> {
  const auth = await loadAuth();
  if (!auth) fail("未登录，无法刷新");

  const result = await apiRequest<LoginTokenVo>("POST", "/web/auth/token-refresh", {
    token: auth.token,
    refreshToken: auth.refreshToken,
  });
  const newAuth: AuthData = {
    email: auth.email,
    token: result.data.token,
    refreshToken: result.data.refreshToken,
    expireMinutes: result.data.expireMinutes,
    savedAt: Date.now(),
  };
  await saveAuth(newAuth);
  console.log(`✓ token 刷新成功（新有效期 ${newAuth.expireMinutes} 分钟）`);
}

async function cmdCheckAuth(): Promise<void> {
  const auth = await loadAuth();
  if (!auth) {
    console.log("✗ 未登录");
    process.exit(1);
  }
  const elapsed = Math.floor((Date.now() - auth.savedAt) / 1000 / 60);
  const expiringSoon = isTokenExpiringSoon(auth);
  console.log(`✓ 已登录: ${auth.email}（已用 ${elapsed} 分钟，token ${expiringSoon ? "即将过期" : "有效"}）`);
}

async function cmdLogout(): Promise<void> {
  await deleteAuth();
  console.log("✓ 已退出登录，本地 token 已删除");
}

// ─── 命令：配置 ───────────────────────────────────────────────────────────────

async function cmdSetConfig(args: CliArgs): Promise<void> {
  const cfg = await loadConfig();
  if (args.appId) {
    cfg.appId = args.appId;
    log(`appId 已设置: ${args.appId}`);
  }
  await saveConfig(cfg);
  console.log("✓ 配置已保存:", JSON.stringify(cfg, null, 2));
}

async function cmdShowConfig(): Promise<void> {
  const cfg = await loadConfig();
  console.log("当前配置:");
  console.log(`  appId: ${cfg.appId ?? "（未配置，联系管理员获取）"}`);
  const auth = await loadAuth();
  console.log(`  登录状态: ${auth ? `已登录 (${auth.email})` : "未登录"}`);
}

// ─── 命令：查询模型分组 ──────────────────────────────────────────────────────

async function cmdListGroups(args: CliArgs): Promise<void> {
  const { token } = await getValidToken();

  interface PageResult { records: ModelGroupVo[]; total: number }
  const body: Record<string, unknown> = {
    current: 1,
    size: args.pageSize,
    enabled: true,
  };
  if (args.keyword) body["keyword"] = args.keyword;
  if (args.modelType) body["modelType"] = args.modelType;

  const result = await apiRequest<PageResult>("POST", "/web/ai-model-groups/search", { token, body });
  const { records, total } = result.data;

  if (args.jsonOutput) {
    console.log(JSON.stringify(result.data, null, 2));
    return;
  }

  const typeFilter = args.modelType ? `（仅 ${args.modelType}）` : "";
  console.log(`模型分组列表（共 ${total} 个${typeFilter}）:\n`);

  const TYPE_LABEL: Record<string, string> = { TEXT: "文本", IMAGE: "图片", VIDEO: "视频" };
  console.log(`${"ID".padEnd(8)} ${"类型".padEnd(6)} ${"分组名称".padEnd(20)} ${"模型名称".padEnd(28)} ${"标签".padEnd(16)} ${"积分"}`);
  console.log("─".repeat(90));
  for (const g of records) {
    const typeLabel = String(g.modelType ? (TYPE_LABEL[g.modelType] ?? g.modelType) : "-").padEnd(6);
    const label = (g.label ?? "-").padEnd(16);
    const points = String(g.points ?? 0);
    const imgOpts = g.imageGenOptions
      ? ` [图数:${g.imageGenOptions.imageCountEnabled ? "✓" : "✗"} 比例:${g.imageGenOptions.imageRadioEnabled ? "✓" : "✗"}]`
      : "";
    console.log(
      `${String(g.id).padEnd(8)} ${typeLabel} ${g.groupName.padEnd(20)} ${(g.modelName ?? "-").padEnd(28)} ${label} ${points}${imgOpts}`
    );
  }
  console.log("\n提示：");
  console.log("  文本模型 → bun main.ts stream --group-id <ID> --user <消息>");
  console.log("  图片模型 → bun main.ts generate --group-id <ID> --prompt <提示词>");
  console.log("  过滤：--model-type text|image|video");
}

// ─── 命令：上传图片 ───────────────────────────────────────────────────────────

async function cmdUpload(args: CliArgs): Promise<string> {
  if (!args.imageFile) fail("请传入 --image-file <本地图片路径>");
  const { token } = await getValidToken();

  const fileData = await readFile(args.imageFile);
  const filename = path.basename(args.imageFile);
  const formData = new FormData();
  formData.append("file", new Blob([fileData]), filename);

  log(`上传图片: ${filename}`);
  const result = await apiRequest<Record<string, string>>("POST", "/web/files/images", { token, formData });

  const url = (result.data.url ?? Object.values(result.data)[0]) as string;
  if (!url) fail("上传返回的 URL 为空");

  if (args.jsonOutput) {
    console.log(JSON.stringify({ url }));
  } else {
    console.log(`✓ 上传成功: ${url}`);
  }
  return url;
}

// ─── 命令：查询任务 ───────────────────────────────────────────────────────────

async function cmdGetTask(args: CliArgs): Promise<TaskVo> {
  if (args.taskIds.length === 0) fail("请传入 --task-id <任务ID>");
  const { token } = await getValidToken();

  const result = await apiRequest<TaskVo>("GET", `/web/ai/invoke/tasks/${args.taskIds[0]}`, { token });
  const t = result.data;

  if (args.jsonOutput) {
    console.log(JSON.stringify(t, null, 2));
  } else {
    const label = TASK_STATUS_LABEL[t.status] ?? String(t.status);
    console.log(`任务 ${t.id}: ${label}${t.failReason ? ` (${t.failReason})` : ""}`);
    if (t.status === TASK_STATUS.SUCCESS) {
      const urls = extractImageUrls(t.responseJson);
      const text = extractTextContent(t.responseJson);
      if (urls.length > 0) {
        urls.forEach((u, i) => console.log(`  [${i + 1}] ${u}`));
      } else if (text) {
        console.log(text);
      } else {
        console.log("响应:", JSON.stringify(t.responseJson, null, 2));
      }
    }
    if (t.queuePosition != null) {
      console.log(`队列位置: ${t.queuePosition}，预计等待: ${t.estimatedWaitSeconds}s`);
    }
  }
  return t;
}

// ─── 命令：生成图片/视频 ──────────────────────────────────────────────────────

async function cmdGenerate(args: CliArgs): Promise<void> {
  if (!args.groupId && !args.endpointId) fail("请传入 --group-id <分组ID> 或 --endpoint-id <接口ID>");
  const { token } = await getValidToken();
  const appId = await getAppId(args.appId);

  // 若有本地文件，先上传
  let imageUrl = args.imageUrl;
  if (args.imageFile) imageUrl = await cmdUpload(args);

  // 构建 params（图片模型格式）
  const params: Record<string, unknown> = { ...args.params };
  if (args.prompt) params["prompt"] = args.prompt;
  if (imageUrl) params["image"] = [imageUrl];
  if (args.aspectRatio) params["aspect_ratio"] = args.aspectRatio;

  const body: Record<string, unknown> = { params };
  if (args.groupId) body["groupId"] = args.groupId;
  if (args.endpointId) body["endpointId"] = args.endpointId;
  if (args.imageCount > 1) body["imageCount"] = args.imageCount;
  if (appId) body["appId"] = appId;

  log(`创建生成任务 groupId=${args.groupId ?? "-"}`);
  const result = await apiRequest<number[]>("POST", "/web/ai/invoke/tasks", { token, body });
  const taskIds = result.data;
  if (!taskIds || taskIds.length === 0) fail("创建任务失败，未返回任务 ID");

  if (args.noWait) {
    if (args.jsonOutput) {
      console.log(JSON.stringify({ taskIds }));
    } else {
      taskIds.forEach(id => console.log(String(id)));
    }
    return;
  }

  console.log(`✓ 任务已创建: ${taskIds.join(", ")}\n轮询状态中...`);
  await printPollResults(taskIds.map(String), token, args.jsonOutput);
}

// ─── 命令：并发轮询多个任务 ───────────────────────────────────────────────────

async function cmdPoll(args: CliArgs): Promise<void> {
  if (args.taskIds.length === 0) fail("请传入至少一个 --task-id <任务ID>");
  const { token } = await getValidToken();
  log(`轮询 ${args.taskIds.length} 个任务...`);
  await printPollResults(args.taskIds.map(String), token, args.jsonOutput);
}

// ─── 命令：查询积分账户 ───────────────────────────────────────────────────────

async function cmdCredit(): Promise<void> {
  const { token } = await getValidToken();
  const result = await apiRequest<CreditAccountVo>("GET", "/web/credit/account", { token });
  const c = result.data;
  console.log(`积分账户:`);
  console.log(`  当前余额: ${c.currentCredit}`);
  console.log(`  累计获得: ${c.totalObtained}`);
  console.log(`  累计消费: ${c.totalConsumed}`);
  if (c.lastGrantTime) console.log(`  上次发放: ${c.lastGrantTime}`);
  if (c.nextGrantTime) console.log(`  下次发放: ${c.nextGrantTime}`);
}

// ─── 命令：查询任务历史 ───────────────────────────────────────────────────────

const GEN_TASK_TYPE_LABEL: Record<number, string> = { 1: "图片", 2: "视频" };
const GEN_STATUS_LABEL: Record<string | number, string> = {
  SCHEDULED: "待夜间执行", 0: "待夜间执行",
  GENERATING: "生成中",    1: "生成中",
  SUCCESS:    "成功",      2: "成功",
  FAILED:     "失败",      3: "失败",
  CANCELLED:  "已取消",    4: "已取消",
};

async function cmdListTasks(args: CliArgs): Promise<void> {
  const { token } = await getValidToken();

  const body: Record<string, unknown> = {
    current: 1,
    size: args.pageSize,
  };
  if (args.taskStatus) body["status"] = args.taskStatus;
  if (args.taskType) body["taskType"] = args.taskType;
  if (args.keyword) body["keyword"] = args.keyword;
  if (args.groupId) body["groupId"] = args.groupId;

  interface PageResult { records: AiGenTaskListVo[]; total: number }
  const result = await apiRequest<PageResult>("POST", "/web/ai-gen-tasks/search", { token, body });
  const { records, total } = result.data;

  if (args.jsonOutput) {
    console.log(JSON.stringify(result.data, null, 2));
    return;
  }

  console.log(`任务历史（共 ${total} 条）:\n`);
  console.log(`${"ID".padEnd(22)} ${"类型".padEnd(4)} ${"状态".padEnd(8)} ${"时间".padEnd(20)} ${"提示词"}`);
  console.log("─".repeat(90));
  for (const t of records) {
    const typeLabel = (GEN_TASK_TYPE_LABEL[t.taskType ?? 0] ?? "-").padEnd(4);
    const statusLabel = (GEN_STATUS_LABEL[t.status] ?? t.status).padEnd(8);
    const time = (t.requestTime ?? "-").padEnd(20);
    const input = (t.inputText ?? "-").slice(0, 30);
    console.log(`${String(t.id).padEnd(22)} ${typeLabel} ${statusLabel} ${time} ${input}`);
    if (t.status === "SUCCESS" && t.outPutFile && t.outPutFile.length > 0) {
      t.outPutFile.forEach((u, i) => console.log(`  ${"".padEnd(22)} 结果[${i + 1}]: ${u}`));
    }
    if (t.status === "FAILED" && t.failReason) {
      console.log(`  ${"".padEnd(22)} 失败原因: ${t.failReason}`);
    }
  }
  console.log("\n过滤选项: --task-status <状态> --task-type <1=图片|2=视频> --keyword <关键词>");
}

// ─── 命令：流式文本对话（SSE）────────────────────────────────────────────────

async function cmdStream(args: CliArgs): Promise<void> {
  if (!args.groupId && !args.endpointId) fail("请传入 --group-id <分组ID> 或 --endpoint-id <接口ID>");
  const text = args.content ?? args.userMessage;
  if (!text) fail("请传入 --content <消息内容> 或 --user <消息内容>");

  const { token } = await getValidToken();
  const appId = await getAppId(args.appId);

  const body: Record<string, unknown> = { content: text };
  if (args.groupId) body["groupId"] = args.groupId;
  if (args.endpointId) body["endpointId"] = args.endpointId;
  if (appId) body["appId"] = appId;
  if (args.conversationId) body["conversationId"] = args.conversationId;
  if (args.imageUrls.length > 0) body["imageUrls"] = args.imageUrls;
  if (Object.keys(args.params).length > 0) body["params"] = args.params;

  const url = `${BASE_URL}/web/ai/invoke/stream`;
  const resp = await fetch(url, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${token}`,
      "Content-Type": "application/json",
      "Accept": "text/event-stream",
    },
    body: JSON.stringify(body).replace(/"(groupId|endpointId)":"(\d+)"/g, '"$1":$2'),
  });

  if (!resp.ok) {
    const errText = await resp.text();
    fail(`HTTP ${resp.status}: ${errText.slice(0, 200)}`);
  }

  if (!resp.body) fail("响应体为空，服务端未返回流");

  let conversationId: string | null = null;
  let fullText = "";
  let buffer = "";

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();

  // 非 --json 模式直接实时输出到 stdout
  if (!args.jsonOutput) process.stdout.write("");

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE 可能有 "data: " 前缀，也可能是裸 NDJSON，逐行解析
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? ""; // 最后不完整行留在 buffer

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;

      // 去掉 SSE "data: " 前缀
      const jsonStr = trimmed.startsWith("data:") ? trimmed.slice(5).trim() : trimmed;
      if (!jsonStr || jsonStr === "[DONE]") continue;

      let chunk: StreamChunk;
      try {
        chunk = JSON.parse(jsonStr) as StreamChunk;
      } catch {
        // 忽略非 JSON 行（SSE 注释等）
        continue;
      }

      if (chunk.error) {
        process.stdout.write("\n");
        fail(`流式错误: ${chunk.error}`);
      }

      if (chunk.conversationId) conversationId = chunk.conversationId;

      if (chunk.chunk) {
        fullText += chunk.chunk;
        if (!args.jsonOutput) {
          process.stdout.write(chunk.chunk);
        }
      }

      if (chunk.done) break;
    }
  }

  if (!args.jsonOutput) {
    process.stdout.write("\n");
    if (conversationId) {
      console.error(`[navigation-ai] conversationId: ${conversationId}（多轮对话时通过 --conversation-id 传入）`);
    }
  } else {
    console.log(JSON.stringify({ conversationId, content: fullText }));
  }
}

// ─── 命令：文本模型对话（异步任务版）──────────────────────────────────────────

async function cmdChat(args: CliArgs): Promise<void> {
  if (!args.groupId && !args.endpointId) fail("请传入 --group-id <分组ID> 或 --endpoint-id <接口ID>");
  const { token } = await getValidToken();
  const appId = await getAppId(args.appId);

  // 构建 messages
  let messages: ChatMessage[] = [...args.messages];

  // 快捷方式：--system / --user 追加到 messages
  if (args.systemPrompt && !messages.some(m => m.role === "system")) {
    messages = [{ role: "system", content: args.systemPrompt }, ...messages];
  }
  if (args.userMessage) {
    messages.push({ role: "user", content: args.userMessage });
  }

  if (messages.length === 0) fail("请提供消息：--user <内容> 或 --message <role:内容>");

  const params: Record<string, unknown> = { messages, ...args.params };
  const body: Record<string, unknown> = { params };
  if (args.groupId) body["groupId"] = args.groupId;
  if (args.endpointId) body["endpointId"] = args.endpointId;
  if (appId) body["appId"] = appId;

  log(`发送文本对话 groupId=${args.groupId ?? "-"}，消息数: ${messages.length}`);
  const result = await apiRequest<number[]>("POST", "/web/ai/invoke/tasks", { token, body });
  const taskIds = result.data;
  if (!taskIds || taskIds.length === 0) fail("创建任务失败");

  console.log(`✓ 任务已创建: ${taskIds.join(", ")}\n等待回复...`);
  await printPollResults(taskIds.map(String), token, args.jsonOutput);
}

// ─── 辅助：轮询并打印结果 ─────────────────────────────────────────────────────

async function printPollResults(taskIds: string[], token: string, jsonOutput: boolean): Promise<void> {
  const results = await pollTasks(taskIds, token);
  for (const task of results) {
    if (task.status === TASK_STATUS.SUCCESS) {
      const urls = extractImageUrls(task.responseJson);
      const text = extractTextContent(task.responseJson);
      if (jsonOutput) {
        console.log(JSON.stringify({ taskId: task.id, status: "SUCCESS", urls, text, responseJson: task.responseJson }));
      } else if (urls.length > 0) {
        console.log(`\n✓ 任务 ${task.id} 完成，生成结果:`);
        urls.forEach((u, i) => console.log(`  [${i + 1}] ${u}`));
      } else if (text) {
        console.log(`\n✓ 任务 ${task.id} 完成\n${text}`);
      } else {
        console.log(`\n✓ 任务 ${task.id} 完成`);
        console.log(JSON.stringify(task.responseJson, null, 2));
      }
    } else {
      const label = TASK_STATUS_LABEL[task.status] ?? String(task.status);
      if (jsonOutput) {
        console.log(JSON.stringify({ taskId: task.id, status: label, failReason: task.failReason }));
      } else {
        console.log(`\n✗ 任务 ${task.id} ${label}: ${task.failReason ?? "未知原因"}`);
      }
    }
  }
}

/**
 * 从轮询终态判断（status 为数字）
 */
async function pollTasks(taskIds: string[], token: string): Promise<TaskVo[]> {
  const pending = new Set(taskIds);
  const completed = new Map<string, TaskVo>();
  const start = Date.now();
  const TERMINAL = new Set([TASK_STATUS.SUCCESS, TASK_STATUS.FAILED, TASK_STATUS.CANCELED]);

  while (pending.size > 0) {
    if (Date.now() - start > POLL_MAX_WAIT_MS) {
      log(`轮询超时（${POLL_MAX_WAIT_MS / 1000}s），剩余: ${[...pending].join(", ")}`);
      break;
    }

    await sleep(POLL_INTERVAL_MS);

    for (const id of [...pending]) {
      try {
        const result = await apiRequest<TaskVo>("GET", `/web/ai/invoke/tasks/${id}`, { token });
        const task = result.data;
        if (TERMINAL.has(task.status)) {
          pending.delete(id);
          completed.set(id, task);
          log(`任务 ${id}: ${TASK_STATUS_LABEL[task.status] ?? task.status}`);
        } else {
          const pos = task.queuePosition != null ? ` (队列: ${task.queuePosition})` : "";
          log(`任务 ${id}: ${TASK_STATUS_LABEL[task.status] ?? task.status}${pos}`);
        }
      } catch (err) {
        log(`查询任务 ${id} 失败: ${err}`);
      }
    }
  }

  for (const id of pending) {
    completed.set(id, {
      id, status: TASK_STATUS.FAILED, failReason: "轮询超时",
      responseJson: null, queuePosition: null, estimatedWaitSeconds: null, finishTime: null,
    });
  }

  return taskIds.map(id => completed.get(id)!);
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * 提取图片/视频 URL
 * 格式: responseJson.data[].url
 */
function extractImageUrls(responseJson: unknown): string[] {
  if (!responseJson || typeof responseJson !== "object") return [];
  const obj = responseJson as Record<string, unknown>;
  const data = obj["data"];
  if (!Array.isArray(data)) return [];
  return data
    .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
    .map(item => item["url"])
    .filter((u): u is string => typeof u === "string");
}

/**
 * 提取文本模型回复
 * 格式: responseJson.choices[0].message.content
 */
function extractTextContent(responseJson: unknown): string | null {
  if (!responseJson || typeof responseJson !== "object") return null;
  const obj = responseJson as Record<string, unknown>;
  const choices = obj["choices"];
  if (!Array.isArray(choices) || choices.length === 0) return null;
  const first = choices[0] as Record<string, unknown>;
  const message = first["message"] as Record<string, unknown> | undefined;
  if (!message) return null;
  const content = message["content"];
  return typeof content === "string" ? content : null;
}

// ─── 主入口 ───────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  const args = parseArgs(process.argv.slice(2));

  if (args.help || !args.command) {
    printUsage();
    process.exit(args.help ? 0 : 1);
  }

  try {
    switch (args.command) {
      case "send-code":   await cmdSendCode(args); break;
      case "login":       await cmdLogin(args); break;
      case "refresh":     await cmdRefresh(); break;
      case "check-auth":  await cmdCheckAuth(); break;
      case "logout":      await cmdLogout(); break;
      case "set-config":  await cmdSetConfig(args); break;
      case "show-config": await cmdShowConfig(); break;
      case "list-groups": await cmdListGroups(args); break;
      case "list-tasks":  await cmdListTasks(args); break;
      case "credit":      await cmdCredit(); break;
      case "upload":      await cmdUpload(args); break;
      case "task":        await cmdGetTask(args); break;
      case "generate":    await cmdGenerate(args); break;
      case "poll":        await cmdPoll(args); break;
      case "stream":      await cmdStream(args); break;
      case "chat":        await cmdChat(args); break;
      default: fail(`未知命令: ${args.command}，运行 --help 查看帮助`);
    }
  } catch (err) {
    fail(String(err));
  }
}

await main();
