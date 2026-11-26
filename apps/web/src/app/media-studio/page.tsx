"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type MediaTask = {
  id: number;
  provider: string;
  model: string;
  status: string;
  prompt: string;
  remote_task_id: string;
  result_path: string;
  error: string;
};
type LocalVaceTask = {
  id: number;
  status: string;
  prompt: string;
  stage: string;
  progress: number | null;
  error: string;
  download_url: string | null;
  created_at: string | null;
};

export default function MediaStudioPage() {
  const [tasks, setTasks] = useState<MediaTask[]>([]);
  const [message, setMessage] = useState("");
  const [localVacePrompt, setLocalVacePrompt] = useState("");
  const [localVaceTasks, setLocalVaceTasks] = useState<LocalVaceTask[]>([]);
  const [localVaceBusy, setLocalVaceBusy] = useState(false);
  const [localVaceMessage, setLocalVaceMessage] = useState("");

  async function loadTasks() {
    try {
      const response = await fetch(`${API}/api/media/video/tasks`);
      if (response.ok) setTasks(await response.json());
    } catch {}
  }

  async function loadLocalVaceTasks() {
    try {
      const response = await fetch(`${API}/api/media/video/local-vace/tasks`, { cache: "no-store" });
      if (response.ok) setLocalVaceTasks(await response.json());
    } catch {}
  }

  async function createLocalVace() {
    if (!localVacePrompt.trim()) {
      setLocalVaceMessage("请先填写视频画面和动作描述。");
      return;
    }
    setLocalVaceBusy(true);
    setLocalVaceMessage("正在将任务加入本地 VACE 队列……");
    try {
      const response = await fetch(`${API}/api/media/video/local-vace/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: localVacePrompt.trim(),
          width: 256, height: 256, frames: 5, steps: 4
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地 VACE 任务提交失败");
      setLocalVaceMessage(`本地任务 #${data.task_id} 已加入队列；不会调用云端或付费 API。`);
      await loadLocalVaceTasks();
    } catch (error) {
      setLocalVaceMessage(error instanceof Error ? error.message : "本地 VACE 任务提交失败");
    } finally {
      setLocalVaceBusy(false);
    }
  }

  async function cancelLocalVace(id: number) {
    if (!window.confirm("确定停止这个本地 VACE 生成任务吗？已生成的未完成临时文件会清理。")) return;
    try {
      const response = await fetch(`${API}/api/media/video/local-vace/tasks/${id}/cancel`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "取消任务失败");
      setLocalVaceMessage(data.message || "已发送取消请求");
      await loadLocalVaceTasks();
    } catch (error) {
      setLocalVaceMessage(error instanceof Error ? error.message : "取消任务失败");
    }
  }

  useEffect(() => {
    loadTasks();
    loadLocalVaceTasks();
    const timer = window.setInterval(loadLocalVaceTasks, 4000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <main className="min-h-screen p-8 text-white">
      <div className="mx-auto max-w-6xl space-y-8">
        <header>
          <h1 className="text-3xl font-bold">AI 视频与音频生产中心</h1>
          <p className="mt-2 text-white/60">视频生成、AI 配音、任务查询与媒体合成</p>
          <a href="/tasks" className="mt-3 inline-flex rounded-lg border border-purple-300/25 px-3 py-2 text-sm text-purple-200 hover:bg-purple-950/40">
            查看统一任务中心（含配音任务）
          </a>
          <a href="/episode-studio" className="ml-2 mt-3 inline-flex rounded-lg border border-cyan-300/30 px-3 py-2 text-sm text-cyan-200 hover:bg-cyan-950/40">
            分集成片检查与镜头替换
          </a>
        </header>

        {message && <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">{message}</div>}

        <section className="rounded-2xl border border-cyan-300/25 bg-slate-950/65 p-6">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-cyan-100">本地离线视频生成 · Wan 2.1 VACE</h2>
              <p className="mt-2 text-sm text-white/65">使用安装包内置模型和 sd-cli.exe，在本机生成；不调用云端、不下载模型、不产生 API 费用。</p>
              <p className="mt-1 text-xs text-amber-200/80">当前默认低负载预览：256×256、5 帧、4 步，CPU 卸载模式。Ryzen 7 4800U 可先按 CPU 路径运行，尚未完成该设备的实机性能认证。</p>
            </div>
            <span className="rounded-lg border border-cyan-300/25 px-3 py-1 text-xs text-cyan-100">完全本地</span>
          </div>
          <label className="mt-5 mb-2 block text-sm">视频画面与动作描述</label>
          <textarea value={localVacePrompt} onChange={e => setLocalVacePrompt(e.target.value)}
            rows={4} maxLength={2000}
            className="w-full rounded-lg border border-white/15 bg-black/30 p-3"
            placeholder="例如：一名国漫风格少年站在雨后的古城屋顶，披风随风摆动，镜头缓慢向前推进……" />
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button disabled={localVaceBusy || !localVacePrompt.trim()} onClick={createLocalVace}
              className="rounded-xl bg-cyan-700 px-5 py-3 font-semibold hover:bg-cyan-600 disabled:opacity-40">
              {localVaceBusy ? "正在提交…" : "生成本地视频"}
            </button>
            <span className="text-xs text-white/50">首次运行会占用较多内存和 CPU，生成时间取决于设备性能。</span>
          </div>
          {localVaceMessage && <p className="mt-3 text-sm text-cyan-100">{localVaceMessage}</p>}
          <div className="mt-6 space-y-3">
            <div className="font-semibold">本地 VACE 任务</div>
            {localVaceTasks.map(task => (
              <div key={task.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold">任务 #{task.id} · {task.status}</div>
                    <div className="mt-1 text-xs text-white/55">阶段：{task.stage || "等待执行"}</div>
                    {task.progress !== null && <div className="mt-2 h-2 w-56 max-w-full overflow-hidden rounded-full bg-white/10">
                      <div className="h-full rounded-full bg-cyan-400 transition-all" style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }} />
                    </div>}
                    {task.progress !== null && <div className="mt-1 text-xs text-white/45">{task.progress}%</div>}
                  </div>
                  <div className="flex gap-2">
                    {task.download_url && <a href={API + task.download_url} className="rounded-lg border border-green-300/25 px-3 py-2 text-xs text-green-200">下载成片</a>}
                    {!["completed", "failed", "cancelled", "interrupted"].includes(task.status) &&
                      <button onClick={() => cancelLocalVace(task.id)} className="rounded-lg border border-red-300/25 px-3 py-2 text-xs text-red-200">取消任务</button>}
                  </div>
                </div>
                <p className="mt-2 text-sm text-white/70">{task.prompt}</p>
                {task.error && <p className="mt-2 whitespace-pre-wrap text-xs text-red-300">{task.error}</p>}
              </div>
            ))}
            {localVaceTasks.length === 0 && <p className="text-sm text-white/45">暂无本地 VACE 任务。</p>}
          </div>
        </section>

        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-amber-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-3 text-xl font-semibold">云端 AI 视频生成</h2>
            <p className="text-sm text-white/70">此版本不启用第三方视频 API。离线包尚未完成可交付级的本地文本生成视频质量验收，因此不显示可付费提交入口。</p>
            <button disabled className="mt-4 rounded-xl bg-slate-700 px-6 py-3 font-semibold opacity-60">当前版本不可用</button>
          </div>
          <div className="rounded-2xl border border-amber-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-3 text-xl font-semibold">AI 配音</h2>
            <p className="text-sm text-white/70">当前离线资源包没有包含可用的本地 TTS 声音模型。为避免任何未经确认的费用，云端语音服务已关闭。</p>
            <button disabled className="mt-4 rounded-xl bg-slate-700 px-6 py-3 font-semibold opacity-60">等待本地 TTS 模型</button>
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-xl font-semibold">视频任务队列</h2>
            <button onClick={loadTasks} className="rounded-lg border border-white/20 px-4 py-2">
              刷新列表
            </button>
          </div>
          <div className="space-y-3">
            {tasks.map(task => (
              <div key={task.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold">任务 #{task.id} · {task.provider}</div>
                    <div className="mt-1 text-sm text-white/60">{task.model} · {task.status}</div>
                  </div>
                  <button disabled className="rounded-lg bg-slate-700 px-4 py-2 opacity-60">历史任务只读</button>
                </div>
                <p className="mt-3 text-sm text-white/70">{task.prompt}</p>
                {task.result_path && <p className="mt-2 break-all text-xs text-green-300">{task.result_path}</p>}
                {task.error && <p className="mt-2 text-sm text-red-300">{task.error}</p>}
              </div>
            ))}
            {tasks.length === 0 && <p className="text-white/50">暂无视频任务</p>}
          </div>
        </section>
      </div>
    </main>
  );
}
