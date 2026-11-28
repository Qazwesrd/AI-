"use client";

import Link from "next/link";
import { useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Check = { status: string; message: string; detail?: string };

const guide = [
  { title: "单独安装 AI 资源包", body: "电脑版安装器与 AI 离线资源包是两个独立文件，资源包不会被塞进桌面安装器。先运行独立资源包安装脚本，选择空间充足的数据盘；安装脚本会校验 SHA-256，并只补充缺失引擎，不覆盖已有模型和用户数据。" },
  { title: "确认两部分已正确衔接", body: "先检测独立资源包清单和必要文件是否安装，再检测 Ollama、ComfyUI 图片/视频引擎是否运行。资源包安装完成不等于模型已经加载；桌面软件只读取独立资源目录，不会重新打包或自动下载模型。" },
  { title: "按类别放置模型", body: "图片模型通常放在 ComfyUI/models/checkpoints、loras、vae、upscale_models、controlnet、clip_vision、ipadapter 等对应目录；视频模型按当前节点要求放入 diffusion_models、checkpoints、vae、text_encoders 或 clip。请以资源包目录说明和节点要求为准，不要把所有文件混放。" },
  { title: "重新扫描并核对", body: "扫描后核对文件名、类别和目录。显示“已发现文件”只代表文件出现在目录中；显示“节点可见”只代表 ComfyUI 下拉列表能列出它们，都不等于模型已成功加载。" },
  { title: "小规模实际测试", body: "进入图片工作台或视频中心，先用低分辨率、短时长和单个镜头做小测试。只有实际工作流执行成功并生成可打开的文件，才算该模型配置完成。不要一开始就跑长视频或批量任务。" },
  { title: "开始快速创作", body: "模型测试成功后，回到首页的新手流程，从导入小说、剧情分析、人物场景素材、剧本分镜、视频合成到成片检查，逐步完成第一集。" },
];

export default function SetupWizardPage() {
  const [step, setStep] = useState(0);
  const [bundleCheck, setBundleCheck] = useState<Check | null>(null);
  const [imageCheck, setImageCheck] = useState<Check | null>(null);
  const [videoCheck, setVideoCheck] = useState<Check | null>(null);
  const [ollamaCheck, setOllamaCheck] = useState<Check | null>(null);
  const [localModels, setLocalModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [modelTest, setModelTest] = useState<Check | null>(null);
  const [imageModels, setImageModels] = useState<string[]>([]);
  const [selectedImageModel, setSelectedImageModel] = useState("");
  const [imageTest, setImageTest] = useState<Check | null>(null);
  const [imagePromptId, setImagePromptId] = useState("");
  const [testingImage, setTestingImage] = useState(false);
  const [busy, setBusy] = useState(false);
  const [testingModel, setTestingModel] = useState(false);

  async function scan() {
    setBusy(true);
    setBundleCheck(null);
    setImageCheck(null);
    setVideoCheck(null);
    setOllamaCheck(null);
    setLocalModels([]);
    setModelTest(null);
    setImageTest(null);
    setImagePromptId("");
    setImageModels([]);
    const checkOne = async (path: string): Promise<Check> => {
      try {
        const response = await fetch(`${API}${path}`, { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) return { status: "error", message: data.detail || `检测失败（HTTP ${response.status}）` };
        if (path.endsWith("image-runtime")) {
          const count = data.model_inventory?.total ?? data.checkpoint_count ?? 0;
          const checkpoints = Array.isArray(data.checkpoints) ? data.checkpoints.filter((name: unknown): name is string => typeof name === "string") : [];
          setImageModels(checkpoints);
          setSelectedImageModel(current => current && checkpoints.includes(current) ? current : (checkpoints[0] || ""));
          return { status: data.status || "unknown", message: data.message || "图片引擎检测完成", detail: `已发现 ${count} 个图片模型条目；可测试基础 checkpoint：${checkpoints.length} 个；硬件建议：${data.hardware?.gpu_level || "未知"}。发现条目不代表已实测加载。` };
        }
        const count = data.model_inventory_count ?? data.model_inventory?.length ?? 0;
        const folders = data.model_folders ? Object.entries(data.model_folders).map(([name, item]: [string, any]) => `${name}: ${item.count ?? 0}`).join("；") : "模型目录信息未返回";
        return { status: data.status || "unknown", message: data.message || "视频引擎检测完成", detail: `已发现 ${count} 个视频模型条目；目录：${folders}。生成就绪：${data.generation_ready ? "是" : "否"}。` };
      } catch (error) {
        return { status: "unavailable", message: error instanceof Error ? error.message : "无法连接本地 API" };
      }
    };
    const checkBundle = async (): Promise<Check> => {
      try {
        const response = await fetch(`${API}/api/local-ai/resource-bundle/status`, { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) return { status: "error", message: data.detail || `检测失败（HTTP ${response.status}）` };
        const missing = (data.missing_components || []).join("、");
        const root = data.data_directory ? `数据目录：${data.data_directory}` : "尚未配置数据目录";
        const detail = [root, missing ? `缺失组件：${missing}` : "必要组件目录齐全", data.manifest_error ? `清单错误：${data.manifest_error}` : ""].filter(Boolean).join("；");
        return { status: data.status || "unknown", message: data.message || "AI 资源包检测完成", detail };
      } catch (error) {
        return { status: "unavailable", message: error instanceof Error ? error.message : "无法连接本地 API" };
      }
    };
    const checkOllama = async (): Promise<Check> => {
      try {
        const response = await fetch(`${API}/api/local-ai/runtime`, { cache: "no-store" });
        const data = await response.json();
        if (!response.ok) return { status: "error", message: data.detail || `检测失败（HTTP ${response.status}）` };
        const models = Array.isArray(data.models) ? data.models.map((item: any) => item.name).filter((name: unknown): name is string => typeof name === "string") : [];
        setLocalModels(models);
        setSelectedModel(current => current && models.includes(current) ? current : (models[0] || ""));
        return { status: data.status || "unknown", message: data.message || "本地文字模型检测完成", detail: models.length ? `已安装模型：${models.join("、")}` : "Ollama 已响应，但没有发现已安装模型。" };
      } catch (error) {
        return { status: "unavailable", message: error instanceof Error ? error.message : "无法连接本地 API" };
      }
    };
    const [bundle, image, video, ollama] = await Promise.all([
      checkBundle(),
      checkOne("/api/local-ai/image-runtime"),
      checkOne("/api/local-ai/video-runtime"),
      checkOllama(),
    ]);
    setBundleCheck(bundle);
    setImageCheck(image);
    setVideoCheck(video);
    setOllamaCheck(ollama);
    setBusy(false);
  }

  const statusLabel = (check: Check | null) => !check ? "尚未检测" : check.status === "ready" || check.status === "available" ? "引擎已响应" : check.status === "not_running" || check.status === "unavailable" ? "尚未连接" : check.status;

  async function testSelectedModel() {
    if (!selectedModel) return;
    setTestingModel(true);
    setModelTest(null);
    try {
      const response = await fetch(`${API}/api/local-ai/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel }),
      });
      const data = await response.json();
      setModelTest(response.ok
        ? { status: "success", message: data.message || "本地模型推理测试成功", detail: data.response || "" }
        : { status: "error", message: data.detail || `测试失败（HTTP ${response.status}）` });
    } catch (error) {
      setModelTest({ status: "unavailable", message: error instanceof Error ? error.message : "无法连接本地 API" });
    } finally {
      setTestingModel(false);
    }
  }

  async function testSelectedImageModel() {
    if (!selectedImageModel) return;
    setTestingImage(true);
    setImageTest(null);
    try {
      const response = await fetch(`${API}/api/local-ai/images/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          checkpoint: selectedImageModel,
          prompt: "A single cinematic anime character portrait, centered composition, clean background, detailed face and costume",
          negative_prompt: "blurry, low quality, deformed, extra limbs, text, watermark",
          style: "3D漫", width: 512, height: 512, steps: 4, cfg: 6,
          confirm_local_generation: true
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `图片测试失败（HTTP ${response.status}）`);
      setImagePromptId(data.prompt_id);
      setImageTest({ status: "submitted", message: "本地图片任务已进入 ComfyUI 队列，尚未判定生成成功", detail: "任务编号：" + data.prompt_id + "；模型：" + data.checkpoint + "；种子：" + data.seed + "。点击“检查实际生成结果”读取任务状态；只有任务完成且返回图片文件，才计为推理通过。" });
    } catch (error) {
      setImagePromptId("");
      setImageTest({ status: "error", message: error instanceof Error ? error.message : "图片测试失败" });
    } finally {
      setTestingImage(false);
    }
  }

  async function checkImageGenerationResult() {
    if (!imagePromptId) return;
    setTestingImage(true);
    try {
      const response = await fetch(API + "/api/local-ai/images/tasks/" + encodeURIComponent(imagePromptId), { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "任务查询失败（HTTP " + response.status + "）");
      if (data.status === "failed") {
        setImageTest({ status: "error", message: "本地图片推理失败", detail: data.error || "ComfyUI 报告任务执行失败，请检查本机节点、模型和运行日志。" });
      } else if (data.status === "completed" && Array.isArray(data.images) && data.images.length > 0) {
        const files = data.images.map((item: any) => item.filename).filter((name: unknown): name is string => typeof name === "string");
        setImageTest({ status: "success", message: "本地图片推理成功，ComfyUI 已返回生成文件", detail: "任务编号：" + imagePromptId + "\n生成文件：" + (files.join("、") || "已返回图片输出") + "\n该结果证明本机工作流已完成并输出图片；尚未验证不同模型、分辨率和长时间稳定性。" });
      } else {
        setImageTest({ status: "submitted", message: "本地图片仍在排队或生成中", detail: "任务编号：" + imagePromptId + "；当前状态：" + (data.status || "queued_or_running") + "。请稍后再次检查。" });
      }
    } catch (error) {
      setImageTest({ status: "unavailable", message: error instanceof Error ? error.message : "无法查询本地图片任务" });
    } finally {
      setTestingImage(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#070512] px-4 py-8 text-white md:px-8">
      <div className="mx-auto max-w-5xl space-y-6">
        <header className="rounded-3xl border border-cyan-300/20 bg-slate-950/80 p-6 md:p-8">
          <p className="text-xs tracking-[.25em] text-cyan-300">BEGINNER SETUP ASSISTANT</p>
          <h1 className="mt-3 text-3xl font-black md:text-4xl">AI 资源包配置向导</h1>
          <p className="mt-3 max-w-3xl text-sm leading-7 text-slate-300">按顺序配置本地图片与视频模型。每一步都说明要做什么、如何判断结果，以及下一步去哪操作。全程优先本地免费模式，不会自动下载模型、调用云端 API 或产生付费请求。</p>
          <div className="mt-5 flex flex-wrap gap-2">
            <Link href="/local-models" className="rounded-xl border border-purple-300/30 px-4 py-2 text-sm">模型与引擎管理</Link>
            <Link href="/media-studio" className="rounded-xl border border-purple-300/30 px-4 py-2 text-sm">视频模型中心</Link>
            <Link href="/local-image-studio" className="rounded-xl border border-purple-300/30 px-4 py-2 text-sm">图片生成工作台</Link>
            <Link href="/help" className="rounded-xl border border-white/15 px-4 py-2 text-sm">完整说明书</Link>
          </div>
        </header>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/75 p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div><p className="text-xs text-purple-300">配置进度</p><h2 className="text-xl font-bold">第 {step + 1} / {guide.length} 步</h2></div>
            <span className="text-sm text-cyan-200">{Math.round((step + 1) / guide.length * 100)}%</span>
          </div>
          <div className="mb-5 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-cyan-400 transition-all" style={{ width: `${(step + 1) / guide.length * 100}%` }} /></div>
          <div className="flex flex-wrap gap-2">
            {guide.map((item, index) => <button key={item.title} onClick={() => setStep(index)} className={`rounded-lg px-3 py-2 text-xs ${step === index ? "bg-cyan-700 text-white" : index < step ? "bg-emerald-900/50 text-emerald-200" : "bg-white/5 text-slate-400"}`}>{index + 1}. {item.title}</button>)}
          </div>
          <article className="mt-5 rounded-xl border border-cyan-300/20 bg-cyan-950/20 p-5">
            <h3 className="text-xl font-bold text-cyan-100">{guide[step].title}</h3>
            <p className="mt-3 text-sm leading-7 text-slate-200">{guide[step].body}</p>
            {step === 1 && <div className="mt-4 space-y-3">
              <button onClick={scan} disabled={busy} className="rounded-xl bg-cyan-700 px-5 py-3 text-sm font-semibold disabled:opacity-50">{busy ? "正在检测本地引擎…" : "检测资源包与全部本地引擎"}</button>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {[["独立 AI 资源包", bundleCheck], ["文字模型引擎", ollamaCheck], ["图片引擎", imageCheck], ["视频引擎", videoCheck]].map(([name, check]) => <div key={name as string} className="rounded-lg border border-white/10 bg-black/20 p-3"><p className="font-semibold">{name as string}：{statusLabel(check as Check | null)}</p><p className="mt-1 text-xs leading-5 text-slate-300">{(check as Check | null)?.message || "点击上方按钮检测"}</p>{(check as Check | null)?.detail && <p className="mt-2 break-all text-xs leading-5 text-cyan-100/70">{(check as Check).detail}</p>}</div>)}
              </div>
              <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3">
                <p className="font-semibold">本地图片模型实际推理测试</p>
                <p className="mt-1 text-xs leading-5 text-slate-300">使用已安装的 checkpoint，在本机 ComfyUI 生成一张 512×512、4 步测试图。会占用本机资源；不会下载模型、调用云端或产生付费请求。</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <select value={selectedImageModel} onChange={event => { setSelectedImageModel(event.target.value); setImageTest(null); setImagePromptId(""); }} disabled={!imageModels.length || testingImage} className="min-w-0 flex-1 rounded-lg border border-white/15 bg-slate-900 px-3 py-2 text-sm">
                    {!imageModels.length && <option value="">未发现可测试的 checkpoint</option>}
                    {imageModels.map(model => <option key={model} value={model}>{model}</option>)}
                  </select>
                  <button onClick={testSelectedImageModel} disabled={!selectedImageModel || testingImage} className="rounded-lg bg-cyan-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">{testingImage ? "正在提交本地测试…" : "生成测试图片"}</button>
                  {imagePromptId && <button onClick={checkImageGenerationResult} disabled={testingImage} className="rounded-lg border border-cyan-400/40 px-4 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-40">{testingImage ? "正在查询…" : "检查实际生成结果"}</button>}
                </div>
                {imageTest && <div className="mt-3 rounded-lg border border-white/10 p-3 text-xs leading-6"><p className={imageTest.status === "success" ? "text-emerald-200" : imageTest.status === "submitted" ? "text-cyan-200" : "text-amber-200"}>{imageTest.message}</p>{imageTest.detail && <p className="mt-1 whitespace-pre-wrap text-slate-300">{imageTest.detail}</p>}</div>}
              </div>

              <div className="mt-4 rounded-lg border border-white/10 bg-black/20 p-3">
                <p className="font-semibold">本地文字模型实际推理测试</p>
                <p className="mt-1 text-xs leading-5 text-slate-300">只测试本机已安装模型，不会下载模型，也不会调用云端。</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <select value={selectedModel} onChange={event => { setSelectedModel(event.target.value); setModelTest(null); }} disabled={!localModels.length || testingModel} className="min-w-0 flex-1 rounded-lg border border-white/15 bg-slate-900 px-3 py-2 text-sm">
                    {!localModels.length && <option value="">未发现已安装模型</option>}
                    {localModels.map(model => <option key={model} value={model}>{model}</option>)}
                  </select>
                  <button onClick={testSelectedModel} disabled={!selectedModel || testingModel} className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-semibold disabled:opacity-40">{testingModel ? "正在本地推理…" : "测试所选模型"}</button>
                </div>
                {modelTest && <div className="mt-3 rounded-lg border border-white/10 p-3 text-xs leading-6"><p className={modelTest.status === "success" ? "text-emerald-200" : "text-amber-200"}>{modelTest.message}</p>{modelTest.detail && <p className="mt-1 whitespace-pre-wrap text-slate-300">{modelTest.detail}</p>}</div>}
              </div>
            </div>}
            {step === 2 && <div className="mt-4 rounded-lg border border-amber-300/20 bg-amber-950/20 p-3 text-xs leading-6 text-amber-100">重要：先备份现有模型目录。不同模型架构要求不同节点和子目录，不能只凭文件扩展名判断兼容。资源包里的模型说明优先于通用目录建议。</div>}
            {step === 3 && <div className="mt-4 flex flex-wrap gap-3"><Link href="/local-models" className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm">查看图片模型扫描</Link><Link href="/media-studio" className="rounded-lg border border-cyan-300/30 px-4 py-2 text-sm">查看视频模型扫描</Link></div>}
            {step === 4 && <div className="mt-4 grid gap-3 md:grid-cols-2"><Link href="/local-image-studio" className="rounded-lg border border-cyan-300/30 p-3 text-sm">图片：先生成一张低分辨率测试图 →</Link><Link href="/media-studio" className="rounded-lg border border-cyan-300/30 p-3 text-sm">视频：先测试单镜头、短时长工作流 →</Link></div>}
            {step === 5 && <Link href="/" className="mt-4 inline-flex rounded-lg bg-purple-700 px-4 py-3 text-sm font-semibold">返回首页，开始新手创作流程 →</Link>}
          </article>
          <div className="mt-5 flex justify-between">
            <button onClick={() => setStep(value => Math.max(0, value - 1))} disabled={step === 0} className="rounded-lg border border-white/15 px-4 py-2 text-sm disabled:opacity-30">上一步</button>
            <button onClick={() => setStep(value => Math.min(guide.length - 1, value + 1))} disabled={step === guide.length - 1} className="rounded-lg bg-purple-700 px-5 py-2 text-sm disabled:opacity-30">下一步</button>
          </div>
        </section>

        <section className="rounded-2xl border border-amber-300/20 bg-amber-950/15 p-5 text-sm leading-7 text-amber-100/80">
          <strong>识别结果说明：</strong>扫描到文件 ≠ 节点能识别 ≠ 模型能加载 ≠ 能成功生成。当前向导能检测本地引擎和模型清单，并指导你完成小规模实测；不会伪造“配置成功”状态，也不会自动改动、覆盖或删除你的模型文件。
        </section>
      </div>
    </main>
  );
}
