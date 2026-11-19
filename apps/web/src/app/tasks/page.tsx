"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type TaskItem = {
  id: string;
  source: "media" | "image" | "analysis" | "image3d_asset";
  source_id: number;
  type: string;
  title: string;
  status: string;
  status_group: string;
  provider: string;
  model: string;
  summary: string;
  context: string;
  error: string;
  failure_diagnostic?: {category:string|null;guidance:string;confidence:string};
  result_available: boolean;
  progress?: number;
  completed_chunks?: number;
  total_chunks?: number;
  created_at: string | null;
  updated_at: string | null;
};

type QueueItem = { id:number; source:string; task_id:number; priority:number; status:"waiting"|"ready_to_run"; queued_at:string|null; wait_seconds:number; reason:string };
type RuntimeInfo = { max_concurrency: number; active_count: number; available_slots: number; leases: Array<{source:string;task_id:number;age_seconds:number;started_at:string|null}>; stale_leases: Array<{source:string;task_id:number;age_seconds:number}>; queue?: {items:QueueItem[];total:number;dispatch_mode:string;note:string}; note?: string };

type TaskResponse = {
  items: TaskItem[];
  total: number;
  has_more: boolean;
  counts: Record<string, number>;
  by_source: Record<string, Record<string, number>>;
  note: string;
  generated_at: string;
};

const statusNames: Record<string, string> = {
  pending: "等待中", queued: "排队中", submitting: "正在提交",
  submitted: "已提交", processing: "处理中", running: "运行中",
  completed: "已完成", failed: "失败", cancelled: "已取消",
  canceled: "已取消", paused: "已暂停", partial: "部分完成",
  submission_unknown: "提交结果待核实", awaiting_video_url: "等待视频地址",
  download_failed: "下载失败·可仅重试下载",
  interrupted_unknown: "执行中断·待核实",
};

const groupStyles: Record<string, string> = {
  completed: "text-emerald-300 border-emerald-400/30 bg-emerald-950/30",
  failed: "text-red-300 border-red-400/30 bg-red-950/30",
  attention: "text-amber-200 border-amber-400/30 bg-amber-950/30",
  active: "text-cyan-200 border-cyan-400/30 bg-cyan-950/30",
  paused: "text-slate-300 border-slate-400/30 bg-slate-900/40",
  partial: "text-amber-200 border-amber-400/30 bg-amber-950/30",
};

function taskHref(task: TaskItem) {
  if (task.source === "analysis") return "/novels";
  if (task.source === "image") return "/visual-studio";
  if (task.type === "speech") return "/media-studio";
  return task.type === "video" ? "/video-editor" : "/storyboard";
}

function formatTime(value: string | null) {
  if (!value) return "时间未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString();
}

export default function TasksPage() {
  const [items, setItems] = useState<TaskItem[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [source, setSource] = useState("all");
  const [status, setStatus] = useState("all");
  const [keyword, setKeyword] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const [busyTask, setBusyTask] = useState<string | null>(null);
  const [runtime, setRuntime] = useState<RuntimeInfo | null>(null);
  const [runtimeLimit, setRuntimeLimit] = useState(2);
  const [runtimeLogs, setRuntimeLogs] = useState<Array<{id:number;source:string;task_id:number;event:string;message:string;duration_seconds:number|null;created_at:string|null}>>([]);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "100", offset: String(offset) });
      if (source !== "all") params.set("task_type", source);
      if (status !== "all") params.set("status", status);
      if (keyword.trim()) params.set("q", keyword.trim());
      const response = await fetch(`${API}/api/tasks?${params.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "任务列表加载失败");
      const result = data as TaskResponse;
      setItems(result.items || []);
      setCounts(result.counts || {});
      setTotal(result.total || 0);
      setHasMore(Boolean(result.has_more));
      setUpdatedAt(result.generated_at || "");
      setMessage(result.note || "");
      const [runtimeResponse, logsResponse] = await Promise.all([
        fetch(`${API}/api/tasks/runtime`, { cache: "no-store" }),
        fetch(`${API}/api/tasks/runtime/logs?limit=12`, { cache: "no-store" }),
      ]);
      if (runtimeResponse.ok) {
        const runtimeData = await runtimeResponse.json() as RuntimeInfo;
        setRuntime(runtimeData);
        setRuntimeLimit(runtimeData.max_concurrency);
      }
      if (logsResponse.ok) setRuntimeLogs(await logsResponse.json());
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "任务列表加载失败，请检查 API 服务");
    } finally {
      setLoading(false);
    }
  }, [source, status, keyword, offset]);

  useEffect(() => { void loadTasks(); }, [loadTasks]);

  const runAction = async (task: TaskItem, action: string) => {
    const actionNames: Record<string, string> = {
      pause: "暂停", resume: "恢复", retry: "重新排队", cancel: "取消", refresh: "刷新远程状态",
    };
    const uncertainRetry = action === "retry" && ["interrupted_unknown", "submission_unknown"].includes(task.status);
    const confirmation = uncertainRetry
      ? task.source === "media"
        ? "请先核对语音服务商控制台和用量记录。只有确认没有对应远程任务或待结算请求后，才能解除重试保护。是否已核对并确认？"
        : task.source === "analysis"
          ? "请先核对文本模型服务商的用量记录。只有确认对应片段没有被受理、没有产生待结算费用后，才能解除保护并手动续作。是否已核对并确认？"
          : "请先登录图像服务商控制台核对该任务。只有确认服务商没有创建任务、没有产生费用时，才能重新排队。是否已核对并确认？"
      : `确定要${actionNames[action]}此任务吗？${action === "retry" ? "重新排队不会自动调用 AI，也不会自动扣费。" : ""}`;
    if (["retry", "cancel"].includes(action) && !window.confirm(confirmation)) return;
    setBusyTask(task.id);
    try {
      const query = uncertainRetry ? "?confirm_no_remote_job=true" : "";
      const response = await fetch(`${API}/api/tasks/${task.source}/${task.source_id}/${action}${query}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "任务操作失败");
      setMessage(data.message || `任务已执行：${actionNames[action]}`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "任务操作失败");
    } finally {
      setBusyTask(null);
    }
  };

  const cards = [
    { key: "total", label: "全部任务", value: counts.total || 0 },
    { key: "active", label: "进行中", value: counts.active || 0 },
    { key: "completed", label: "已完成", value: counts.completed || 0 },
    { key: "failed", label: "失败", value: counts.failed || 0 },
    { key: "attention", label: "待核实", value: counts.attention || 0 },
  ];

  return (
    <main className="cosmic min-h-screen p-4 text-white md:p-8">
      <div className="black-hole" aria-hidden="true" />
      <div className="nebula nebula-one" />
      <div className="nebula nebula-two" />
      <div className="mx-auto max-w-7xl">
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm text-purple-300">AI WORKFLOW CONTROL</p>
            <h1 className="mt-1 text-3xl font-bold">统一任务中心</h1>
            <p className="mt-2 text-sm text-slate-300">集中查看小说解析、视觉素材、视频与配音任务的执行状态。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={async () => {
              if (!window.confirm("将扫描运行超过 60 分钟的视觉任务和仍处于提交中的媒体任务，并标记为待核实。不会自动重试或调用 AI。继续吗？")) return;
              setLoading(true);
              try {
                const response = await fetch(`${API}/api/tasks/reconcile-stale?minutes=60`, { method: "POST" });
                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || "扫描失败");
                setMessage(data.message || "中断任务扫描完成");
                await loadTasks();
              } catch (error) {
                setMessage(error instanceof Error ? error.message : "扫描失败");
              } finally {
                setLoading(false);
              }
            }} disabled={loading}
              className="rounded-xl border border-amber-300/30 bg-amber-950/30 px-4 py-2 text-sm text-amber-200 hover:bg-amber-900/40 disabled:opacity-50">
              扫描中断任务
            </button>
            <button onClick={() => void loadTasks()} disabled={loading}
              className="rounded-xl border border-purple-300/30 bg-purple-950/50 px-4 py-2 text-sm hover:bg-purple-900/60 disabled:opacity-50">
              {loading ? "刷新中…" : "↻ 刷新任务"}
            </button>
          </div>
        </div>


        <section className="mb-5 rounded-2xl border border-purple-300/15 bg-slate-950/50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="font-semibold">任务执行资源</h2><p className="mt-1 text-xs text-slate-400">跨图片、视频和小说解析共享并发名额；排队任务按优先级排序，名额空出后等待你手动执行。</p></div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-slate-300">全局并发上限</span>
              <select value={runtimeLimit} onChange={event => setRuntimeLimit(Number(event.target.value))} className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2">
                {[1,2,3,4,5,6,7,8].map(value => <option key={value} value={value}>{value}</option>)}
              </select>
              <button onClick={async () => {
                try {
                  const response = await fetch(API + "/api/tasks/runtime/concurrency", {
                    method: "PUT", headers: {"Content-Type":"application/json"},
                    body: JSON.stringify({max_concurrency: runtimeLimit}),
                  });
                  const data = await response.json();
                  if (!response.ok) throw new Error(data.detail || "保存并发设置失败");
                  setRuntime(data); setMessage("并发上限已保存");
                } catch (error) { setMessage(error instanceof Error ? error.message : "保存失败"); }
              }} className="rounded-lg border border-purple-300/30 px-3 py-2 text-xs text-purple-200">保存</button>
            </div>
          </div>
          <div className="mt-3 grid grid-cols-3 gap-3">
            <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">正在执行</p><p className="mt-1 text-xl">{runtime?.active_count ?? 0}</p></div>
            <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">可用并发位</p><p className="mt-1 text-xl">{runtime?.available_slots ?? runtimeLimit}</p></div>
            <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">待核实租约</p><p className="mt-1 text-xl">{runtime?.stale_leases?.length ?? 0}</p></div>
          </div>
          {runtime?.stale_leases?.length ? <div className="mt-3 rounded-xl border border-amber-300/20 bg-amber-950/20 p-3">
            <p className="mb-2 text-xs text-amber-200">发现超过30分钟的遗留执行租约。系统不会自动释放；只有核对服务商确认没有远程任务后，才可手动释放占用。</p>
            {runtime.stale_leases.map(item => <div key={item.source + ":" + item.task_id} className="flex flex-wrap items-center justify-between gap-2 border-t border-white/5 py-2 text-xs">
              <span>{item.source} #{item.task_id} · 已占用 {Math.floor(item.age_seconds / 60)} 分钟</span>
              {(item.source === "image" || item.source === "image3d_asset" || item.source === "analysis" || item.source === "media") ? <button onClick={async () => {
                if (!window.confirm((item.source === "media" ? "请先到对应媒体服务商控制台核对该任务。" : item.source === "analysis" ? "请先核对文本模型服务商的请求记录。" : item.source === "image3d_asset" ? "请先到 Tripo 3D 服务商控制台核对该素材是否已经创建远程任务。" : "请先到图像服务商控制台核对。") + "只有确认没有仍在运行的远程任务、不会发生重复扣费，才能释放本地并发占用。确认已核实？")) return;
                try {
                  const response = await fetch(API + "/api/tasks/runtime/leases/" + item.source + "/" + item.task_id + "/release?confirm_no_remote_job=true", {method:"POST"});
                  const data = await response.json();
                  if (!response.ok) throw new Error(data.detail || "释放失败");
                  setMessage(data.message || "租约已释放");
                  await loadTasks();
                } catch (error) { setMessage(error instanceof Error ? error.message : "释放失败"); }
              }} className="rounded-lg border border-amber-300/30 px-3 py-1.5 text-amber-200">核实后释放占用</button> : <span className="text-slate-500">暂不支持人工释放</span>}
            </div>)}
          </div> : null}
          {runtime?.queue?.total ? <div className="mt-4 border-t border-white/10 pt-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-sm font-medium">持久化等待队列（{runtime.queue.total}）</p>
              <span className="text-xs text-slate-500">优先级高的任务排在前面</span>
            </div>
            <div className="space-y-2">
              {runtime.queue.items.map((item, index) => <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-black/25 px-3 py-2 text-xs">
                <div className="min-w-0">
                  <p className="text-slate-200">#{index + 1} · {item.source === "analysis" ? "小说解析" : item.source === "image" ? "视觉图片" : item.source === "image3d_asset" ? "3D模型" : "视频/媒体"}任务 #{item.task_id}
                    <span className="ml-2 text-slate-500">优先级 {item.priority}</span>
                  </p>
                  <p className="mt-1 text-slate-500">{item.status === "ready_to_run" ? "并发名额已空出 · 等待手动执行" : "等待并发名额"} · 已等待 {Math.floor(item.wait_seconds / 60)} 分钟</p>
                  {item.source === "media" ? <p className="mt-1 text-amber-200/80">视频任务需先移出队列，再回分镜页重新提交该镜头；系统不会自动重复提交。</p> : null}
                </div>
                <div className="flex items-center gap-2">
                  <Link href={item.source === "analysis" ? "/novels" : item.source === "image" || item.source === "image3d_asset" ? "/visual-studio" : "/storyboard"} className="rounded-lg border border-purple-300/25 px-3 py-1.5 text-purple-200">打开模块</Link>
                  <button onClick={async () => {
                    if (!window.confirm("确定从等待队列移除此任务？不会调用 AI。")) return;
                    try {
                      const response = await fetch(API + "/api/tasks/queue/" + item.source + "/" + item.task_id, {method:"DELETE"});
                      const data = await response.json();
                      if (!response.ok) throw new Error(data.detail || "移除失败");
                      setMessage(data.message || "已移除队列");
                      await loadTasks();
                    } catch (error) { setMessage(error instanceof Error ? error.message : "移除失败"); }
                  }} className="rounded-lg border border-red-300/20 px-3 py-1.5 text-red-200">移出队列</button>
                </div>
              </div>)}
            </div>
            <p className="mt-2 text-xs text-amber-200/80">队列不会自动调用 AI。点击“打开模块”后，请检查任务设置和付费确认，再手动执行。</p>
          </div> : null}
          {runtimeLogs.length > 0 && <div className="mt-3 border-t border-white/10 pt-3">
            <p className="mb-2 text-xs text-slate-400">最近执行记录</p>
            <div className="space-y-1">{runtimeLogs.slice(0,6).map(log => <p key={log.id} className="text-xs text-slate-400">
              {log.source} #{log.task_id} · {log.event === "completed" ? "完成" : log.event === "failed" ? "失败" : "启动"} · {log.duration_seconds == null ? "执行中" : log.duration_seconds + " 秒"} · {log.message}
            </p>)}</div>
          </div>}
        </section>
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          {cards.map(card => (
            <div key={card.key} className="rounded-2xl border border-white/10 bg-slate-950/45 p-4 backdrop-blur">
              <p className="text-xs text-slate-400">{card.label}</p>
              <p className="mt-2 text-2xl font-semibold tabular-nums">{card.value}</p>
            </div>
          ))}
        </div>

        <div className="mb-5 grid gap-3 rounded-2xl border border-white/10 bg-slate-950/45 p-4 md:grid-cols-[1fr_180px_180px_auto]">
          <input value={keyword} onChange={event => { setKeyword(event.target.value); setOffset(0); }}
            onKeyDown={event => { if (event.key === "Enter") void loadTasks(); }}
            placeholder="搜索任务、模型、小说名称或错误信息"
            className="min-w-0 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-purple-300/50" />
          <select value={source} onChange={event => { setSource(event.target.value); setOffset(0); }}
            className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm">
            <option value="all">全部模块</option>
            <option value="analysis">小说解析</option>
            <option value="image">视觉生成</option>
            <option value="media">视频与配音</option>
          </select>
          <select value={status} onChange={event => { setStatus(event.target.value); setOffset(0); }}
            className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm">
            <option value="all">全部状态</option>
            <option value="active">进行中</option>
            <option value="completed">已完成</option>
            <option value="failed">失败</option>
            <option value="paused">已暂停</option>
            <option value="attention">需要核实</option>
            <option value="partial">部分完成</option>
          </select>
          <button onClick={() => void loadTasks()} className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-200 hover:bg-cyan-950/40">查询</button>
        </div>

        {message && <p className="mb-4 rounded-xl border border-white/10 bg-slate-950/40 p-3 text-xs text-slate-300">{message}</p>}

        <div className="space-y-3">
          {items.map(task => (
            <article key={task.id} className="rounded-2xl border border-white/10 bg-slate-950/55 p-4 backdrop-blur">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="font-semibold">{task.title}</h2>
                    <span className={`rounded-full border px-2 py-0.5 text-xs ${groupStyles[task.status_group] || "border-white/20 text-slate-300"}`}>
                      {statusNames[task.status] || task.status}
                    </span>
                    <span className="text-xs text-slate-500">#{task.source_id}</span>
                  </div>
                  <p className="mt-1 text-xs text-purple-200">{task.context || task.type} · {task.provider || "本地任务"}{task.model ? ` / ${task.model}` : ""}</p>
                  {task.summary && <p className="mt-2 whitespace-pre-wrap break-words text-sm text-slate-300">{task.summary}</p>}
                  {task.source === "analysis" && (
                    <div className="mt-3 max-w-xl">
                      <div className="mb-1 flex justify-between text-xs text-slate-400">
                        <span>解析批次进度</span><span>{task.completed_chunks}/{task.total_chunks} · {task.progress || 0}%</span>
                      </div>
                      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                        <div className="h-full rounded-full bg-purple-400" style={{ width: `${Math.max(0, Math.min(100, task.progress || 0))}%` }} />
                      </div>
                    </div>
                  )}
                  {task.status_group === "attention" && (
                    <p className="mt-2 text-xs text-amber-200">该任务状态不确定。请先核对服务商任务记录，避免重复提交或重复扣费。</p>
                  )}
                  {task.error && <p className="mt-2 whitespace-pre-wrap break-words rounded-lg bg-red-950/30 p-2 text-xs text-red-200">{task.error}</p>}
                  {task.failure_diagnostic?.category && task.failure_diagnostic.category !== "unknown" && <div className="mt-2 rounded-lg border border-amber-300/15 bg-amber-950/15 p-2 text-xs">
                    <p className="text-amber-200">失败原因初步分类：{({
                      quota_or_billing:"额度 / 计费", authentication:"授权 / 密钥", rate_limit:"请求频率限制",
                      timeout:"请求超时", network:"网络连接", invalid_request:"请求参数",
                    } as Record<string,string>)[task.failure_diagnostic.category] || task.failure_diagnostic.category} <span className="text-slate-500">（规则匹配，仅供排查）</span></p>
                    <p className="mt-1 text-slate-300">{task.failure_diagnostic.guidance}</p>
                  </div>}
                  <p className="mt-2 text-[11px] text-slate-500">最近更新：{formatTime(task.updated_at)}</p>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  {task.source === "image" && task.status === "pending" && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "pause")}
                      className="rounded-lg border border-white/15 px-3 py-2 text-xs disabled:opacity-40">暂停</button>
                  )}
                  {task.source === "image" && task.status === "paused" && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "resume")}
                      className="rounded-lg border border-cyan-300/25 px-3 py-2 text-xs text-cyan-200 disabled:opacity-40">恢复</button>
                  )}
                  {task.source === "image" && ["failed", "cancelled", "interrupted_unknown"].includes(task.status) && !task.type.startsWith("model3d_") && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "retry")}
                      className="rounded-lg border border-amber-300/25 px-3 py-2 text-xs text-amber-200 disabled:opacity-40">
                      {task.status === "interrupted_unknown" ? "核实后重新排队" : "重新排队"}
                    </button>
                  )}
                  {task.source === "image" && ["pending", "paused", "failed"].includes(task.status) && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "cancel")}
                      className="rounded-lg border border-red-300/25 px-3 py-2 text-xs text-red-200 disabled:opacity-40">取消</button>
                  )}
                  {task.source === "media" && ["video", "storyboard_video"].includes(task.type) && !["completed", "failed", "cancelled", "submission_unknown"].includes(task.status) && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "refresh")}
                      className="rounded-lg border border-cyan-300/25 px-3 py-2 text-xs text-cyan-200 disabled:opacity-40">刷新状态</button>
                  )}
                  {task.source === "media" && ["speech", "episode_speech"].includes(task.type) && ["submission_unknown", "interrupted_unknown"].includes(task.status) && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "retry")}
                      className="rounded-lg border border-amber-300/25 px-3 py-2 text-xs text-amber-200 disabled:opacity-40">核实后重新生成</button>
                  )}
                  {task.source === "analysis" && task.status === "interrupted_unknown" && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "retry")}
                      className="rounded-lg border border-amber-300/25 px-3 py-2 text-xs text-amber-200 disabled:opacity-40">核实后续作</button>
                  )}
                  <Link href={taskHref(task)} className="rounded-lg border border-purple-300/25 px-3 py-2 text-xs text-purple-200 hover:bg-purple-950/50">打开所属模块 ↗</Link>
                </div>
              </div>
            </article>
          ))}
          {items.length === 0 && (
            <div className="rounded-2xl border border-dashed border-white/15 bg-slate-950/30 p-10 text-center text-sm text-slate-400">
              {loading ? "正在读取任务…" : "没有符合条件的任务。"}
            </div>
          )}
        </div>
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/10 bg-slate-950/35 p-3">
          <span className="text-xs text-slate-400">
            当前显示 {total === 0 ? 0 : offset + 1}–{Math.min(offset + items.length, total)} 条，共 {total} 条匹配记录；每类最多读取最近 500 条
          </span>
          <div className="flex items-center gap-2">
            <button disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - 100))}
              className="rounded-lg border border-white/15 px-3 py-2 text-xs disabled:opacity-40">上一页</button>
            <span className="text-xs text-slate-400">第 {Math.floor(offset / 100) + 1} 页</span>
            <button disabled={!hasMore || loading} onClick={() => setOffset(value => value + 100)}
              className="rounded-lg border border-white/15 px-3 py-2 text-xs disabled:opacity-40">下一页</button>
          </div>
        </div>
        <p className="mt-3 text-center text-xs text-slate-500">{updatedAt ? `数据刷新时间：${formatTime(updatedAt)}` : ""}</p>
      </div>
    </main>
  );
}
