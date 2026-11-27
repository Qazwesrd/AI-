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
  const [runtimeDiagnostics, setRuntimeDiagnostics] = useState<any>(null);
  const [diagnosticsLoading, setDiagnosticsLoading] = useState(false);
  const [videoRuntimeLoading, setVideoRuntimeLoading] = useState(false);
  const [workflowJson, setWorkflowJson] = useState("");
  const [workflowTemplateSpec, setWorkflowTemplateSpec] = useState("");
  const [workflowPromptId, setWorkflowPromptId] = useState("");
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowResult, setWorkflowResult] = useState<any>(null);
  const [templateCapabilities, setTemplateCapabilities] = useState<any>(null);
  const [wizardClassType, setWizardClassType] = useState("");
  const [wizardNodes, setWizardNodes] = useState<any[]>([]);


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


  async function loadRuntimeDiagnostics() {
    setDiagnosticsLoading(true);
    try {
      const response = await fetch(`${API}/api/local-ai/video-runtime/diagnostics`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "本地运行环境诊断失败");
      setRuntimeDiagnostics(data);
    } catch (error) {
      setRuntimeDiagnostics({ available: false, reason: error instanceof Error ? error.message : "无法读取本机运行环境", devices: [] });
    } finally { setDiagnosticsLoading(false); }
  }

  async function loadVideoTemplateCapabilities() {
    try {
      const response = await fetch(`${API}/api/local-ai/video-workflow/template-capabilities`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "节点能力扫描失败");
      setTemplateCapabilities(data);
    } catch (error) {
      setTemplateCapabilities({
        available: false, roles: {}, nodes: [],
        message: error instanceof Error ? error.message : "无法读取本机视频节点能力",
        template_generated: false, generation_ready: false,
      });
    }
  }

  async function loadTasks() {
    try {
      const response = await fetch(`${API}/api/media/video/tasks`);
      if (response.ok) setTasks(await response.json());
    } catch {}
  }

  useEffect(() => { loadTasks(); loadLocalVideoRuntime(); loadVideoTemplateCapabilities(); loadRuntimeDiagnostics(); }, []);

  useEffect(() => {
    let attempts = 0;
    const timer = window.setInterval(() => {
      attempts += 1;
      void loadLocalVideoRuntime();
      if (attempts >= 8) window.clearInterval(timer);
    }, 15000);
    return () => window.clearInterval(timer);
  }, []);




  function addWizardNode() {
    const discovered = templateCapabilities?.nodes?.find((node: any) => node.class_type === wizardClassType);
    if (!discovered) return;
    const defaults: Record<string, unknown> = {};
    for (const section of ["required", "optional"]) {
      for (const [name, schema] of Object.entries(discovered.input_schema?.[section] || {}) as [string, any][]) {
        if (schema.default !== undefined && schema.default !== null) defaults[name] = schema.default;
      }
    }
    setWizardNodes(current => [...current, { node_id: String(Date.now()) + String(current.length), class_type: wizardClassType, values: defaults, connections: {} }]);
    setWizardClassType("");
  }
  function updateWizardNode(nodeId: string, updater: (node: any) => any) {
    setWizardNodes(current => current.map(node => node.node_id === nodeId ? updater(node) : node));
  }
  function setWizardInputValue(nodeId: string, inputName: string, value: unknown) {
    updateWizardNode(nodeId, node => {
      const values = { ...node.values, [inputName]: value };
      const connections = { ...node.connections }; delete connections[inputName];
      return { ...node, values, connections };
    });
  }
  function setWizardInputConnection(nodeId: string, inputName: string, selection: string) {
    updateWizardNode(nodeId, node => {
      const values = { ...node.values }; delete values[inputName];
      const connections = { ...node.connections };
      if (selection) { const [sourceId, outputIndex] = selection.split(":"); connections[inputName] = { node_id: sourceId, output_index: Number(outputIndex) }; }
      else delete connections[inputName];
      return { ...node, values, connections };
    });
  }
  async function buildWizardWorkflow() {
    if (!wizardNodes.length) { setMessage("请先添加本机节点。"); return; }
    setWorkflowBusy(true); setWorkflowResult(null); setMessage("正在构建并预检本机工作流草稿……");
    try {
      const response = await fetch(`${API}/api/local-ai/video-workflow/build`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nodes: wizardNodes }),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = typeof data.detail === "object" ? [data.detail.message, ...(data.detail.errors || [])].filter(Boolean).join("；") : data.detail;
        throw new Error(detail || "向导构建失败");
      }
      setWorkflowJson(JSON.stringify(data.workflow, null, 2)); setWorkflowResult(data.validation || data);
      setMessage("向导已生成工作流草稿并通过本机结构预检；尚未提交生成。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "向导构建失败"); }
    finally { setWorkflowBusy(false); }
  }

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

  async function buildLocalVideoWorkflowTemplate() {
    let specification: { nodes: unknown[] };
    try {
      specification = JSON.parse(workflowTemplateSpec);
      if (!specification || !Array.isArray(specification.nodes) || specification.nodes.length === 0) {
        throw new Error("节点配置必须包含非空 nodes 数组");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "模板配置 JSON 格式错误");
      return;
    }
    setWorkflowBusy(true);
    setWorkflowResult(null);
    setMessage("正在根据你明确指定的本机节点、参数和连线构建模板……");
    try {
      const response = await fetch(`${API}/api/local-ai/video-workflow/build`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(specification),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = typeof data.detail === "object" ? [data.detail.message, ...(data.detail.errors || [])].filter(Boolean).join("；") : data.detail;
        throw new Error(detail || "本地模板构建失败");
      }
      setWorkflowJson(JSON.stringify(data.workflow, null, 2));
      setWorkflowResult(data.validation || data);
      setMessage("模板已构建并通过本机结构预检。请检查生成的 API JSON，再决定是否提交队列。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "本地模板构建失败");
    } finally { setWorkflowBusy(false); }
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
    setMessage(validateOnly ? "正在核对本机节点、模型选项和视频输出节点……" : "正在检查本机显存、运行设备和工作流参数……");
    try {
      let resourceAssessment: any = null;
      if (!validateOnly) {
        const preflightResponse = await fetch(`${API}/api/local-ai/video-workflow/preflight`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ workflow }),
        });
        const preflightData = await preflightResponse.json();
        if (!preflightResponse.ok) {
          const detail = typeof preflightData.detail === "object"
            ? [preflightData.detail.message, ...(preflightData.detail.errors || [])].filter(Boolean).join("；")
            : preflightData.detail;
          throw new Error(detail || "本地资源预检未通过");
        }
        resourceAssessment = preflightData.resource_assessment;
        setWorkflowResult(preflightData);
        const warnings = resourceAssessment?.warnings || [];
        const recommendations = resourceAssessment?.recommendations || [];
        if (warnings.length) {
          const promptText = [
            "本机资源预检发现以下风险：",
            ...warnings.map((item: string) => "• " + item),
            "",
            "建议：",
            ...recommendations.map((item: string) => "• " + item),
            "",
            "继续将只向本机 ComfyUI 提交任务，不会调用云端。仍要继续吗？",
          ].join("\n");
          if (!window.confirm(promptText)) {
            setMessage("已取消提交；预检结果已保留，未启动生成。");
            return;
          }
        }
      }
      const response = await fetch(`${API}/api/local-ai/video-workflow/${validateOnly ? "validate" : "submit"}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow }),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = typeof data.detail === "object" ? [data.detail.message, ...(data.detail.errors || [])].filter(Boolean).join("；") : data.detail;
        throw new Error(detail || "本地工作流请求失败");
      }
      setWorkflowResult({ ...data, ...(resourceAssessment ? { resource_assessment: resourceAssessment } : {}) });
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

          <section className="mt-4 rounded-xl border border-emerald-300/20 bg-emerald-950/15 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="font-semibold text-emerald-100">本机视频运行环境诊断</h2><p className="mt-1 text-sm text-white/60">只读取本机 ComfyUI 系统信息，不运行压力测试、不下载模型、不提交生成任务。</p></div>
              <button onClick={loadRuntimeDiagnostics} disabled={diagnosticsLoading} className="rounded-lg border border-emerald-300/30 px-3 py-2 text-sm disabled:opacity-50">{diagnosticsLoading ? "检测中…" : "重新诊断"}</button>
            </div>
            {runtimeDiagnostics && <div className="mt-3 space-y-2 text-sm">
              <p>{runtimeDiagnostics.available ? "本地引擎系统信息已读取" : "暂不可读取运行环境"} · {runtimeDiagnostics.reason || runtimeDiagnostics.assessment || ""}</p>
              {runtimeDiagnostics.system && <p className="text-xs text-white/55">系统：{runtimeDiagnostics.system.os || "未报告"} · ComfyUI：{runtimeDiagnostics.system.comfyui_version || "未报告"} · PyTorch：{runtimeDiagnostics.system.pytorch_version || "未报告"}</p>}
              {runtimeDiagnostics.devices?.length > 0 ? runtimeDiagnostics.devices.map((device: any, index: number) => <div key={device.name + index} className="rounded-lg border border-white/10 bg-black/20 p-3">
                <p className="font-medium text-emerald-100">{device.name}</p>
                <p className="mt-1 text-xs text-white/65">设备类型：{device.type} · 显存总量：{device.vram_total_mb === null ? "未报告" : device.vram_total_mb + " MB"} · 当前可用：{device.vram_free_mb === null ? "未报告" : device.vram_free_mb + " MB"}</p>
                {device.torch_vram_total_mb !== null && <p className="mt-1 text-xs text-white/45">PyTorch显存：{device.torch_vram_total_mb} MB · 可用：{device.torch_vram_free_mb ?? "未报告"} MB</p>}
              </div>) : <p className="text-xs text-amber-200/80">没有检测到 ComfyUI 报告的加速设备信息。不能据此断定硬件不支持，需结合驱动和实际模型测试。</p>}
              {runtimeDiagnostics.assessment && <p className="text-xs text-amber-100/70">{runtimeDiagnostics.assessment}</p>}
              <p className="text-xs text-white/40">设备被识别不等于模型可加载。下一步仍需使用小尺寸、低显存工作流进行真实执行验收。</p>
            </div>}
          </section>
          <section className="mt-4 rounded-xl border border-indigo-300/20 bg-indigo-950/15 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-indigo-100">本机视频工作流节点能力映射</h2>
                <p className="mt-1 text-sm text-white/60">{templateCapabilities?.message || "正在读取 ComfyUI 节点真实输入结构……"}</p>
              </div>
              <button onClick={loadVideoTemplateCapabilities} className="rounded-lg border border-indigo-300/30 px-3 py-2 text-sm">重新扫描节点能力</button>
            </div>
            {templateCapabilities?.available && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {Object.entries(templateCapabilities.roles || {}).map(([role, nodes]: [string, any]) => (
                  <div key={role} className="rounded-lg border border-white/10 bg-black/20 p-3">
                    <p className="text-sm font-medium text-indigo-100">{({
                      model_loader: "视频模型加载器", text_encoder: "文本编码器", text_encode: "提示词编码",
                      sampler: "视频采样器", vae_loader: "VAE 加载器", decode: "视频解码",
                      image_input: "图片输入", video_input: "视频输入", vace: "VACE 编辑节点",
                      video_output: "视频输出",
                    } as Record<string, string>)[role] || role}</p>
                    <p className="mt-1 break-all text-xs text-white/55">{(nodes as string[]).join("、")}</p>
                  </div>
                ))}
              </div>
            )}
            {templateCapabilities?.available && templateCapabilities?.template_assessment && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {([
                  ["wan_t2v_role_coverage", "Wan 文生视频节点角色检查"],
                  ["vace_role_coverage", "VACE 视频编辑节点角色检查"],
                ] as const).map(([key, label]) => {
                  const assessment = templateCapabilities.template_assessment[key];
                  return (
                    <div key={key} className="rounded-lg border border-white/10 bg-black/20 p-3">
                      <p className="text-sm font-medium text-indigo-100">{label}</p>
                      <p className="mt-1 text-xs text-white/60">
                        {assessment?.all_role_groups_found
                          ? "候选角色已齐全，仍需验证接口语义和实际工作流。"
                          : `缺少：${(assessment?.missing_roles || []).join("、") || "尚未识别"}`}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}
            {templateCapabilities?.available && (
              <div className="mt-3 rounded-lg border border-white/10 bg-black/20 p-3">
                <p className="text-sm font-medium text-indigo-100">
                  已发现 {templateCapabilities.connection_candidate_count || 0} 组同类型接口候选连接
                </p>
                <p className="mt-1 text-xs text-white/55">
                  以下只表示输入/输出类型相同，不代表语义上一定可连接；系统不会擅自写入未经验证的连线。
                </p>
                <div className="mt-2 max-h-48 space-y-2 overflow-auto">
                  {(templateCapabilities.connection_candidates || []).slice(0, 16).map((candidate: any, index: number) => (
                    <div key={index} className="text-xs text-white/70">
                      <span className="text-cyan-200">{candidate.target_class}.{candidate.input}</span>
                      <span> ← </span>
                      <span>{candidate.sources.map((source: any) => `${source.class_type}[${source.output_index}]`).join("、")}</span>
                      <span className="ml-1 text-white/40">({candidate.input_type})</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <p className="mt-3 text-xs text-amber-100/70">
              {templateCapabilities?.available
                ? "已读取节点输入/输出类型并列出候选连接；这仍不是完整可执行模板。下一阶段将按经过验证的 Wan 节点组合生成工作流，再进行严格预检。"
                : "未发现可确认的 Wan/VACE 专用节点时，不会用普通图片节点伪造视频工作流。"}
            </p>
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

          <section className="mt-4 rounded-xl border border-cyan-300/20 bg-black/20 p-4">
            <h3 className="font-semibold text-cyan-100">新手节点向导（自动读取本机节点）</h3>
            <p className="mt-1 text-xs leading-5 text-white/60">从已安装节点中逐个添加。参数、下拉选项和输出端口均来自本机 ComfyUI；连线必须由你明确选择，不会自动猜测。</p>
            {templateCapabilities?.available ? <>
              <div className="mt-3 flex flex-wrap gap-2">
                <select value={wizardClassType} onChange={event => setWizardClassType(event.target.value)} className="min-w-0 flex-1 rounded-lg border border-white/15 bg-slate-900 p-2 text-sm">
                  <option value="">选择本机节点类型</option>
                  {(templateCapabilities.nodes || []).map((node: any) => <option key={node.class_type} value={node.class_type}>{node.class_type} · {(node.roles || []).join("/")}</option>)}
                </select>
                <button onClick={addWizardNode} disabled={!wizardClassType || workflowBusy} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-sm disabled:opacity-40">添加节点</button>
              </div>
              {wizardNodes.map((node: any, nodeIndex: number) => {
                const discovered = templateCapabilities.nodes.find((item: any) => item.class_type === node.class_type);
                const schema = discovered?.input_schema || { required: {}, optional: {} };
                const inputs = { ...schema.required, ...schema.optional };
                return <div key={node.node_id} className="mt-3 rounded-lg border border-white/10 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="min-w-0"><p className="text-xs text-white/50">节点 {nodeIndex + 1} · ID {node.node_id}</p><p className="break-all text-sm font-medium text-cyan-100">{node.class_type}</p></div>
                    <button onClick={() => setWizardNodes(current => current.filter(item => item.node_id !== node.node_id))} className="shrink-0 rounded border border-red-300/20 px-2 py-1 text-xs text-red-200">移除</button>
                  </div>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {Object.entries(inputs).map(([inputName, field]: [string, any]) => {
                      const required = Object.prototype.hasOwnProperty.call(schema.required || {}, inputName);
                      const socket = field.type && !["COMBO", "STRING", "INT", "FLOAT", "BOOLEAN"].includes(field.type);
                      const choices = field.choices || [];
                      const outputOptions = wizardNodes.filter((source: any) => source.node_id !== node.node_id).flatMap((source: any) => {
                        const sourceNode = templateCapabilities.nodes.find((item: any) => item.class_type === source.class_type);
                        return (sourceNode?.output_schema || []).filter((output: any) => !socket || !field.type || ["*", "ANY", "ANY_TYPE"].includes(field.type) || output.type === field.type).map((output: any) => ({ value: source.node_id + ":" + output.index, label: source.class_type + " · " + output.name + " [" + output.type + "]" }));
                      });
                      const selectedConnection = node.connections?.[inputName];
                      const selectedValue = node.values?.[inputName];
                      return <div key={inputName} className="min-w-0 rounded border border-white/10 p-2">
                        <label className="block break-all text-xs text-white/80">{inputName} · {required ? "必填" : "可选"} · {field.type || "未知类型"}</label>
                        {socket ? <select value={selectedConnection ? selectedConnection.node_id + ":" + selectedConnection.output_index : ""} onChange={event => setWizardInputConnection(node.node_id, inputName, event.target.value)} className="mt-2 w-full rounded bg-slate-900 p-2 text-xs">
                          <option value="">选择来源节点输出（未连接）</option>{outputOptions.map((option: any) => <option key={option.value} value={option.value}>{option.label}</option>)}
                        </select> : choices.length ? <select value={selectedValue === undefined ? "" : String(selectedValue)} onChange={event => setWizardInputValue(node.node_id, inputName, event.target.value)} className="mt-2 w-full rounded bg-slate-900 p-2 text-xs">
                          <option value="">选择本机可用选项</option>{choices.map((choice: any) => <option key={String(choice)} value={String(choice)}>{String(choice)}</option>)}
                        </select> : field.type === "BOOLEAN" ? <select value={selectedValue === undefined ? "" : String(selectedValue)} onChange={event => setWizardInputValue(node.node_id, inputName, event.target.value === "true")} className="mt-2 w-full rounded bg-slate-900 p-2 text-xs"><option value="">请选择</option><option value="true">开启</option><option value="false">关闭</option></select> : <input type={["INT", "FLOAT"].includes(field.type) ? "number" : "text"} value={selectedValue === undefined ? "" : String(selectedValue)} min={field.min} max={field.max} step={field.type === "INT" ? 1 : "any"} onChange={event => setWizardInputValue(node.node_id, inputName, event.target.value === "" ? "" : (["INT", "FLOAT"].includes(field.type) ? Number(event.target.value) : event.target.value))} placeholder={field.default !== undefined ? "默认：" + field.default : "填写参数值"} className="mt-2 w-full rounded bg-slate-900 p-2 text-xs" />}
                        {field.default !== undefined && <p className="mt-1 text-[11px] text-white/40">本机默认值：{String(field.default)}</p>}
                        {socket && outputOptions.length === 0 && <p className="mt-1 text-[11px] text-amber-200/70">尚无类型匹配的来源输出。</p>}
                      </div>;
                    })}
                  </div>
                </div>;
              })}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button onClick={buildWizardWorkflow} disabled={workflowBusy || !wizardNodes.length} className="rounded-lg bg-cyan-700 px-4 py-2 text-sm disabled:opacity-40">生成工作流草稿并预检</button>
                <button onClick={() => setWizardNodes([])} disabled={workflowBusy || !wizardNodes.length} className="rounded-lg border border-white/15 px-3 py-2 text-sm disabled:opacity-40">清空节点</button>
                <span className="text-xs text-white/50">已添加 {wizardNodes.length} 个节点；尚未提交生成</span>
              </div>
            </> : <p className="mt-3 text-sm text-amber-200/80">尚未发现 Wan/VACE 专用节点。向导不会自动下载或安装节点。</p>}
          </section>
          <details className="mt-4 rounded-xl border border-white/10 bg-black/20 p-3">
            <summary className="cursor-pointer text-sm font-medium text-cyan-100">高级：按本机节点结构构建工作流草稿</summary>
            <p className="mt-2 text-xs leading-5 text-white/60">填写节点 class_type、必填参数，以及明确的输入连线。系统只按你指定的连接构建，不会根据相同接口类型擅自猜连线。可先扫描上方节点能力，再使用此功能。</p>
            <textarea value={workflowTemplateSpec} onChange={event => setWorkflowTemplateSpec(event.target.value)}
              rows={8} className="mt-3 w-full rounded-lg border border-white/15 bg-black/40 p-3 font-mono text-xs"
              placeholder={`{
  "nodes": [
    {"node_id":"1","class_type":"本机模型加载节点","values":{"模型输入":"从本机选项中选择"}},
    {"node_id":"2","class_type":"本机采样节点","values":{"步数":4},"connections":{"模型输入":{"node_id":"1","output_index":0}}},
    {"node_id":"3","class_type":"本机视频输出节点","values":{"帧率":24},"connections":{"图像帧输入":{"node_id":"2","output_index":0}}}
  ]
}`} />
            <button onClick={buildLocalVideoWorkflowTemplate} disabled={workflowBusy}
              className="mt-3 rounded-lg border border-cyan-300/30 px-4 py-2 text-sm text-cyan-100 disabled:opacity-50">
              按明确配置构建模板草稿
            </button>
          </details>
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
            {workflowResult.has_connected_generation_chain !== undefined && (
              <p className="mt-1 text-xs">
                生成链路：{workflowResult.has_connected_generation_chain ? "Wan 模型 → 采样/VACE → 视频输出，连通检查通过" : "未形成完整连通链路"}
              </p>
            )}
            {workflowResult.has_video_output_connection !== undefined && (
              <p className="mt-1 text-xs">视频输出连接：{workflowResult.has_video_output_connection ? "已连接" : "未确认连接"}</p>
            )}
            {workflowResult.videos?.length > 0 && <div className="mt-2 space-y-3">{workflowResult.videos.map((video: any, index: number) => <div key={index} className="space-y-1"><p className="break-all text-xs text-emerald-200">{video.filename} · {video.subfolder || "output"}</p>{workflowResult.completed && workflowPromptId && <video controls preload="metadata" className="max-h-[480px] w-full rounded-lg bg-black" src={`${API}/api/local-ai/video-workflow/${workflowPromptId}/preview?output_index=${index}`} />}</div>)}</div>}
            {workflowResult.errors?.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-amber-200">{workflowResult.errors.map((error: string, index: number) => <li key={index}>{error}</li>)}</ul>}
            <p className="mt-2 text-xs text-white/60">{workflowResult.message}</p>
          </div>}
          <p className="mt-3 text-xs leading-5 text-amber-100/70">说明：当前执行器使用你已经在 ComfyUI 中配置好的 API 工作流；需要 Wan/VACE 节点和模型已安装。预检通过不代表模型权重加载或生成一定成功。先用短时长、低分辨率工作流测试。生成结果保存在本机 ComfyUI 输出目录。</p>
        </section>
        <section className="grid gap-6 lg:grid-cols-2">
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
