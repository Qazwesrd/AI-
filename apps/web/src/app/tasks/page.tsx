"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type TaskItem = {
  id: string;
  source: "media" | "image" | "analysis";
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
  result_available: boolean;
  progress?: number;
  completed_chunks?: number;
  total_chunks?: number;
  created_at: string | null;
  updated_at: string | null;
};

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
    const uncertainRetry = action === "retry" && task.status === "interrupted_unknown";
    const confirmation = uncertainRetry
      ? "请先登录图像服务商控制台核对该任务。只有确认服务商没有创建任务、没有产生费用时，才能重新排队。是否已核对并确认？"
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
                  {task.source === "image" && ["failed", "cancelled"].includes(task.status) && !task.type.startsWith("model3d_") && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "retry")}
                      className="rounded-lg border border-amber-300/25 px-3 py-2 text-xs text-amber-200 disabled:opacity-40">重新排队</button>
                  )}
                  {task.source === "image" && ["pending", "paused", "failed"].includes(task.status) && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "cancel")}
                      className="rounded-lg border border-red-300/25 px-3 py-2 text-xs text-red-200 disabled:opacity-40">取消</button>
                  )}
                  {task.source === "media" && ["video", "storyboard_video"].includes(task.type) && !["completed", "failed", "cancelled", "submission_unknown"].includes(task.status) && (
                    <button disabled={busyTask === task.id} onClick={() => void runAction(task, "refresh")}
                      className="rounded-lg border border-cyan-300/25 px-3 py-2 text-xs text-cyan-200 disabled:opacity-40">刷新状态</button>
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
