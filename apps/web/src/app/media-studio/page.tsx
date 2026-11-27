"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type LocalVideoRuntime = {
  status: string;
  node_count: number;
  nodes: { node_type: string; model_choices: string[]; input_schema?: { required?: Record<string, { type?: string; choices?: string[]; default?: unknown; min?: number; max?: number }>; optional?: Record<string, { type?: string; choices?: string[]; default?: unknown; min?: number; max?: number }> } }[];
  model_inventory?: Array<{ name: string; category: string; source: string; selector_node: string; model_folder?: string; status: string; load_tested: boolean }>;
  model_inventory_count?: number;
  model_folders?: Record<string, { available: boolean; count: number; reason?: string }>;
  model_folder_scan_available?: boolean;
  vace_node_found: boolean;
  wan_video_nodes_found: boolean;
  workflow_node_set_complete: boolean;
  required_nodes: Record<string, boolean>;
  missing_required_nodes: string[];
  generation_ready: boolean;
  workflow_execution_verified: boolean;
  reason: string;
  message: string;
};

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

export default function MediaStudioPage() {
  const [provider, setProvider] = useState("");
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
  const [localVideoRuntime, setLocalVideoRuntime] = useState<LocalVideoRuntime | null>(null);
  const [videoRuntimeLoading, setVideoRuntimeLoading] = useState(false);
  const [workflowJson, setWorkflowJson] = useState("");
  const [workflowPromptId, setWorkflowPromptId] = useState("");
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowResult, setWorkflowResult] = useState<any>(null);

  async function loadLocalVideoRuntime() {
    setVideoRuntimeLoading(true);
    try {
      const response = await fetch(`${API}/api/local-ai/video-runtime`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地视频引擎检测失败");
      setLocalVideoRuntime(data);
    } catch (error) {
      setLocalVideoRuntime({
        status: "unavailable", node_count: 0, nodes: [],
        vace_node_found: false, wan_video_nodes_found: false,
        workflow_node_set_complete: false, required_nodes: {}, missing_required_nodes: [],
        generation_ready: false, workflow_execution_verified: false,
        reason: error instanceof Error ? error.message : "无法连接本地视频引擎",
        message: "未能完成本地 Wan/VACE 检测。",
      });
    } finally {
      setVideoRuntimeLoading(false);
    }
  }

  async function loadTasks() {
    try {
      const response = await fetch(`${API}/api/media/video/tasks`);
      if (response.ok) setTasks(await response.json());
    } catch {}
  }

  useEffect(() => { loadTasks(); loadLocalVideoRuntime(); }, []);

  useEffect(() => {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      void loadLocalVideoRuntime();
      if (attempts >= 8) window.clearInterval(timer);
    }, 15000);
    return () => window.clearInterval(timer);
  }, []);



  async function importWorkflowFile(file?: File) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".json") || file.size > 4 * 1024 * 1024) {
      setMessage("请选择不超过 4 MB 的 ComfyUI API 格式 JSON 工作流文件。");
      return;
    }
    try {
      const text = await file.text();
      JSON.parse(text);
      setWorkflowJson(text);
      setWorkflowResult(null);
      setMessage("工作流文件已载入编辑区；尚未提交生成。");
    } catch {
      setMessage("工作流文件不是有效 JSON。");
    }
  }

  async function runLocalVideoWorkflow(validateOnly = false) {
    let workflow: Record<string, unknown>;
    try {
      workflow = JSON.parse(workflowJson);
      if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) throw new Error("工作流根节点必须是 JSON 对象");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "工作流 JSON 格式错误");
      return;
    }
    setWorkflowBusy(true);
    setWorkflowResult(null);
    setMessage(validateOnly ? "正在核对本机节点、模型选项和视频输出节点……" : "正在向本机 ComfyUI 提交视频生成任务……");
    try {
      const response = await fetch(`${API}/api/local-ai/video-workflow/${validateOnly ? "validate" : "submit"}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow }),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = typeof data.detail === "object" ? [data.detail.message, ...(data.detail.errors || [])].filter(Boolean).join("；") : data.detail;
        throw new Error(detail || "本地工作流请求失败");
      }
      setWorkflowResult(data);
      if (!validateOnly && data.prompt_id) setWorkflowPromptId(data.prompt_id);
      setMessage(data.message || (validateOnly ? "本地工作流预检通过。" : "已提交本地生成任务。"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "本地工作流操作失败");
    } finally { setWorkflowBusy(false); }
  }

  async function refreshLocalWorkflow() {
    if (!workflowPromptId) return;
    setWorkflowBusy(true);
    try {
      const response = await fetch(`${API}/api/local-ai/video-workflow/${workflowPromptId}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "查询本地任务失败");
      setWorkflowResult(data);
      setMessage(data.message || "本地视频任务状态已刷新。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查询本地任务失败");
    } finally { setWorkflowBusy(false); }
  }

  async function createVideo() {
    if (!prompt.trim()) {
      setMessage("请填写视频内容描述；软件会自动组合镜头、风格和技术要求，不需要先单独生成提示词。");
      return;
    }
    if (!provider.trim() || !model.trim()) {
      setMessage("尚未配置外部视频服务商和模型。请使用上方本地 Wan/VACE 工作流导入功能；不会自动选择付费服务。");
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
          <p className="mt-2 rounded-lg border border-amber-300/20 bg-amber-950/20 p-3 text-sm text-amber-100/80">本地免费模式优先：已支持导入 ComfyUI API 格式 Wan/VACE 工作流、预检并提交本机队列；内置自动建图模板和自动化加载验收仍在完善。外部视频 API 只有在你手动填写服务商、模型并勾选费用确认后才会提交。</p>
          <section className="mt-4 rounded-xl border border-cyan-300/20 bg-cyan-950/15 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-cyan-100">本地 Wan 2.1 / VACE 引擎检测</h2>
                <p className="mt-1 text-sm text-white/65">
                  {videoRuntimeLoading ? "正在检查本机 ComfyUI 节点……" : localVideoRuntime?.message || "尚未检测"}
                </p>
              </div>
              <button onClick={loadLocalVideoRuntime} disabled={videoRuntimeLoading}
                className="rounded-lg border border-cyan-300/30 px-3 py-2 text-sm disabled:opacity-50">
                重新检测
              </button>
            </div>
            {localVideoRuntime && (
              <div className="mt-3 space-y-2 text-sm">
                <p>检测状态：{localVideoRuntime.status} · Wan 节点：{localVideoRuntime.node_count}</p>
                <p>VACE 节点：{localVideoRuntime.vace_node_found ? "已发现" : "未发现"} · Wan Video 节点：{localVideoRuntime.wan_video_nodes_found ? "已发现" : "未发现"}</p>
                <p>完整工作流节点集：{localVideoRuntime.workflow_node_set_complete ? "齐全" : "不齐全"}</p>
        <p>视频模型条目：{localVideoRuntime.model_inventory_count ?? localVideoRuntime.model_inventory?.length ?? 0} 个（包含节点选择器与本地模型目录扫描）</p>
        {localVideoRuntime.model_folders && (
          <div className="rounded-lg border border-white/10 bg-black/20 p-3">
            <p className="mb-2 text-cyan-100">本地视频模型目录检查</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {Object.entries(localVideoRuntime.model_folders).map(([folder, status]) => (
                <div key={folder} className="rounded border border-white/10 p-2">
                  <p className="break-all">{folder}</p>
                  <p className="mt-1 text-xs text-white/55">{status.available ? `已扫描 · ${status.count} 个文件` : status.reason || "未扫描"}</p>
                </div>
              ))}
            </div>
          </div>
        )}
        {localVideoRuntime.model_inventory && localVideoRuntime.model_inventory.length > 0 && (
          <div className="rounded-lg border border-white/10 bg-black/20 p-3">
            <p className="mb-2 font-medium text-cyan-100">已识别的视频模型清单</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {localVideoRuntime.model_inventory.slice(0,100).map((item,index) => (
                <div key={item.category + ":" + item.selector_node + ":" + item.name + ":" + index} className="rounded-lg border border-white/10 p-2">
                  <p className="break-all text-white/90">{item.name}</p>
                  <p className="mt-1 text-xs text-white/50">{item.category} · {item.source} · {item.selector_node || item.model_folder || "本地目录"} · {item.load_tested ? "已实测加载" : item.status === "file_listed" ? "已发现文件，未实测加载" : "节点可见，未实测加载"}</p>
                </div>
              ))}
            </div>
          </div>
        )}
                <p className="text-amber-200">{localVideoRuntime.reason}</p>
                {localVideoRuntime.missing_required_nodes.length > 0 && (
                  <p className="break-words text-white/60">缺少节点：{localVideoRuntime.missing_required_nodes.join("、")}</p>
                )}
                <p className="text-white/50">工作流实际生成验收：{localVideoRuntime.workflow_execution_verified ? "已通过" : "尚未通过"}。检测不会下载模型、安装节点或提交生成任务。</p>
                {localVideoRuntime.nodes.length > 0 && (
                  <div className="space-y-2">
                    <p className="break-words text-white/60">已发现节点：{localVideoRuntime.nodes.map(node => node.node_type).join("、")}</p>
                    {localVideoRuntime.nodes.some(node => Object.keys({ ...(node.input_schema?.required || {}), ...(node.input_schema?.optional || {}) }).length > 0) && (
                      <details className="rounded-lg border border-white/10 bg-black/20 p-3">
                        <summary className="cursor-pointer text-cyan-100">展开本机节点真实输入参数（以当前 ComfyUI 返回值为准）</summary>
                        <div className="mt-3 space-y-3">
                          {localVideoRuntime.nodes.filter(node => Object.keys(node.input_schema || {}).length > 0).map(node => (
                            <div key={node.node_type}>
                              <p className="font-medium text-cyan-100">{node.node_type}</p>
                              <div className="mt-1 grid gap-1 sm:grid-cols-2">
                                {Object.entries({ ...(node.input_schema?.required || {}), ...(node.input_schema?.optional || {}) }).map(([name, schema]) => (
                                  <div key={name} className="rounded border border-white/10 p-2 text-xs">
                                    <p className="break-all text-white/90">{name} {node.input_schema?.required && Object.prototype.hasOwnProperty.call(node.input_schema.required, name) ? "· 必填" : "· 可选"}</p>
                                    <p className="mt-1 break-all text-white/50">
                                      {schema.type || "类型未报告"}
                                      {schema.choices?.length ? ` · 选项：${schema.choices.join("、")}` : ""}
                                      {schema.default !== undefined ? ` · 默认：${String(schema.default)}` : ""}
                                      {schema.min !== undefined || schema.max !== undefined ? ` · 范围：${schema.min ?? "—"} 至 ${schema.max ?? "—"}` : ""}
                                    </p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </details>
                    )}
                    {localVideoRuntime.nodes.some(node => node.model_choices.length > 0) && (
                      <div className="rounded-lg border border-white/10 bg-black/20 p-3">
                        <p className="mb-2 text-white/70">ComfyUI 节点实际提供的模型选项（仅为下拉清单，不代表模型已完整加载或可成功推理）：</p>
                        {localVideoRuntime.nodes.filter(node => node.model_choices.length > 0).map(node => (
                          <div key={node.node_type} className="mb-2 last:mb-0">
                            <p className="text-cyan-100">{node.node_type}</p>
                            <p className="break-all text-xs text-white/55">{node.model_choices.join("、")}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </section>
          <a href="/tasks" className="mt-3 inline-flex rounded-lg border border-purple-300/25 px-3 py-2 text-sm text-purple-200 hover:bg-purple-950/40">
            查看统一任务中心（含配音任务）
          </a>
          <a href="/episode-studio" className="ml-2 mt-3 inline-flex rounded-lg border border-cyan-300/30 px-3 py-2 text-sm text-cyan-200 hover:bg-cyan-950/40">
            分集成片检查与镜头替换
          </a>
        </header>

        {message && <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4">{message}</div>}


        <section className="rounded-2xl border border-emerald-300/25 bg-emerald-950/20 p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-emerald-100">本地 Wan / VACE 工作流执行</h2>
              <p className="mt-1 text-sm text-white/60">导入 ComfyUI 导出的 API 格式工作流，在本机检查节点和模型后直接排队生成。不会调用云端，也不会自动下载模型。</p>
            </div>
            <span className="rounded-full border border-emerald-300/30 px-3 py-1 text-xs text-emerald-200">本地执行</span>
          </div>
          <label className="mt-4 block text-sm">导入 ComfyUI API 工作流 JSON（最大 4 MB）</label>
          <input type="file" accept=".json,application/json" onChange={event => void importWorkflowFile(event.target.files?.[0])} className="mt-2 block w-full text-sm text-white/70" />
          <label className="mt-4 block text-sm">工作流 JSON 内容</label>
          <textarea value={workflowJson} onChange={event => setWorkflowJson(event.target.value)} rows={8}
            placeholder="先在 ComfyUI 开启开发者模式，使用「Save (API Format)」导出 API 格式 JSON，再导入此处。普通网页保存的 workflow JSON 不是 API 格式。"
            className="mt-2 w-full rounded-lg border border-white/15 bg-black/40 p-3 font-mono text-xs" />
          <div className="mt-3 flex flex-wrap gap-3">
            <button disabled={workflowBusy || !workflowJson.trim()} onClick={() => void runLocalVideoWorkflow(true)} className="rounded-lg border border-emerald-300/40 px-4 py-2 text-sm disabled:opacity-40">只做本地预检</button>
            <button disabled={workflowBusy || !workflowJson.trim()} onClick={() => void runLocalVideoWorkflow(false)} className="rounded-lg bg-emerald-700 px-5 py-2 text-sm font-semibold disabled:opacity-40">预检并提交本地生成</button>
            <button disabled={workflowBusy || !workflowPromptId} onClick={() => void refreshLocalWorkflow()} className="rounded-lg border border-white/20 px-4 py-2 text-sm disabled:opacity-40">刷新任务状态</button>
          </div>
          {workflowResult && <div className="mt-4 rounded-lg border border-white/10 bg-black/25 p-3 text-sm">
            <p className="font-semibold text-emerald-100">本地工作流结果</p>
            {workflowResult.prompt_id && <p className="mt-1 break-all text-xs">ComfyUI 任务编号：{workflowResult.prompt_id}</p>}
            {workflowResult.node_count !== undefined && <p className="mt-1 text-xs">节点数：{workflowResult.node_count} · 预检：{workflowResult.valid ? "通过" : "未通过"}</p>}
            {workflowResult.videos?.length > 0 && <div className="mt-2 space-y-1">{workflowResult.videos.map((video: any, index: number) => <p key={index} className="break-all text-xs text-emerald-200">{video.filename} · {video.subfolder || "output"}</p>)}</div>}
            {workflowResult.errors?.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-amber-200">{workflowResult.errors.map((error: string, index: number) => <li key={index}>{error}</li>)}</ul>}
            <p className="mt-2 text-xs text-white/60">{workflowResult.message}</p>
          </div>}
          <p className="mt-3 text-xs leading-5 text-amber-100/70">说明：当前执行器使用你已经在 ComfyUI 中配置好的 API 工作流；需要 Wan/VACE 节点和模型已安装。预检通过不代表模型权重加载或生成一定成功。先用短时长、低分辨率工作流测试。生成结果保存在本机 ComfyUI 输出目录。</p>
        </section>
\n        <section className="grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-6">
            <h2 className="mb-5 text-xl font-semibold">真实 AI 视频生成</h2>
            <label className="mb-2 block text-sm">服务商 ID</label>
            <input value={provider} onChange={e => setProvider(e.target.value)}
              className="mb-4 w-full rounded-lg border border-white/15 bg-black/30 p-3" />
            <label className="mb-2 block text-sm">视频模型名称</label>
            <input value={model} onChange={e => setModel(e.target.value)}
              placeholder="仅在你明确选择外部服务时填写模型 ID"
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
