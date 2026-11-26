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
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [videoPromptMode, setVideoPromptMode] = useState("cinematic");
  const [duration, setDuration] = useState(10);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [tasks, setTasks] = useState<MediaTask[]>([]);
  const [speechText, setSpeechText] = useState("");
  const [speechModel, setSpeechModel] = useState("");
  const [voice, setVoice] = useState("alloy");
  const [forceRegenerate, setForceRegenerate] = useState(false);
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
          width: 256, height: 256, frames: 9, steps: 4
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

  async function createVideo() {
    if (!prompt.trim()) {
      setMessage("请填写视频内容描述；软件会自动组合镜头、风格和技术要求，不需要先单独生成提示词。");
      return;
    }
    const styleInstruction = videoPromptMode === "animation"
      ? "采用高质量动画电影视觉表现，动作自然流畅，角色造型稳定，色彩统一。"
      : videoPromptMode === "realistic"
      ? "采用真人电影写实视觉，真实光影、自然运动、可信材质和摄影机语言。"
      : "采用电影级镜头语言，强调构图、景深、光影、情绪节奏和连续性。";
    const effectivePrompt = `视频内容与动作：${prompt.trim()}。\n\n生成要求：严格围绕上述内容，不擅自改变人物身份、服装、场景和关键动作；镜头运动平滑，动作前后连贯，避免画面闪烁、肢体畸形、主体漂移和无关文字水印。\n视觉方案：${styleInstruction}\n时长：${duration} 秒。请输出完整、可执行的视频生成结果。`;
    setMessage("正在自动组合视频提示词并提交真实视频生成请求……");
    try {
      const response = await fetch(`${API}/api/media/video/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider, model, prompt: effectivePrompt, duration,
          confirm_paid: confirmPaid
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "视频任务提交失败");
      setMessage(`视频任务已提交，远程任务编号：${data.remote_task_id}`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "请求失败");
    }
  }

  async function refreshTask(id: number) {
    try {
      const response = await fetch(`${API}/api/media/video/tasks/${id}/refresh`, {
        method: "POST"
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "查询失败");
      setMessage(`任务状态：${data.status}`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查询失败");
    }
  }

  async function generateSpeech() {
    setMessage("正在调用真实语音生成 API……");
    try {
      const response = await fetch(`${API}/api/media/speech/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider, model: speechModel, text: speechText,
          voice, confirm_paid: confirmPaid, force_regenerate: forceRegenerate
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "配音失败");
      setMessage(`配音完成：任务 #${data.task_id} · ${data.file}${data.reused ? "（复用已有配音，未重复调用 AI）" : ""}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "配音失败");
    }
  }

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
              <p className="mt-1 text-xs text-amber-200/80">当前默认低负载预览：256×256、9 帧、4 步，CPU 卸载模式。Ryzen 7 4800U 可先按 CPU 路径运行，尚未完成该设备的实机性能认证。</p>
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
          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-5 text-xl font-semibold">真实 AI 视频生成</h2>
            <label className="mb-2 block text-sm">服务商 ID</label>
            <input value={provider} onChange={e => setProvider(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">视频模型名称</label>
            <input value={model} onChange={e => setModel(e.target.value)}
              placeholder="填写服务商提供的视频模型 ID"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">视频内容描述</label>
            <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
              rows={5} className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3"
              placeholder="描述人物动作、场景、镜头内容；软件会自动将描述组合成完整视频提示词。" />
            <label className="mb-2 block text-sm">提示词方案（自动应用）</label>
            <select value={videoPromptMode} onChange={e => setVideoPromptMode(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3">
              <option value="cinematic">电影级镜头语言（推荐）</option>
              <option value="realistic">真人电影写实</option>
              <option value="animation">动画电影风格</option>
            </select>
            <label className="mb-2 block text-sm">视频时长（秒）</label>
            <input type="number" min={1} max={600} value={duration}
              onChange={e => setDuration(Number(e.target.value))}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={confirmPaid}
                onChange={e => setConfirmPaid(e.target.checked)} />
              我已确认本次请求可能产生 API 费用
            </label>
            <button onClick={createVideo}
              className="rounded-xl bg-purple-600 px-6 py-3 font-semibold hover:bg-purple-500">
              提交视频生成
            </button>
          </div>

          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-5 text-xl font-semibold">AI 配音</h2>
            <label className="mb-2 block text-sm">服务商 ID</label>
            <input value={provider} onChange={e => setProvider(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">语音模型名称</label>
            <input value={speechModel} onChange={e => setSpeechModel(e.target.value)}
              placeholder="填写已开通的语音模型 ID"
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">声音 ID</label>
            <input value={voice} onChange={e => setVoice(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">配音文本</label>
            <textarea value={speechText} onChange={e => setSpeechText(e.target.value)}
              rows={5} className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3"
              placeholder="输入需要生成语音的台词或旁白" />
            <label className="mb-4 flex items-center gap-2 text-sm">
              <input type="checkbox" checked={forceRegenerate}
                onChange={e => setForceRegenerate(e.target.checked)} />
              忽略已有相同配音并重新生成（可能再次收费）
            </label>
            <button onClick={generateSpeech}
              className="rounded-xl bg-indigo-600 px-6 py-3 font-semibold hover:bg-indigo-500">
              生成配音
            </button>
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
                  <button onClick={() => refreshTask(task.id)}
                    className="rounded-lg bg-purple-700 px-4 py-2">查询任务</button>
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
