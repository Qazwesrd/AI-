"use client";

import { useEffect, useMemo, useState } from "react";

type ToolCategory = "text" | "image" | "storyboard" | "video";
type Tool = { id: string; name: string; category: ToolCategory; url: string; note: string; custom?: boolean };
type ResultFile = { id: number; filename: string; size: number; downloadUrl: string };
type Task = {
  id: string;
  title: string;
  category: ToolCategory;
  toolId: string;
  prompt: string;
  status: "prepared" | "opened" | "completed";
  resultText: string;
  resultUrl: string;
  createdAt: string;
};

const STORAGE_TOOLS = "ai-comic-studio:web-tools:v1";
const STORAGE_TASKS = "ai-comic-studio:web-tool-tasks:v1";

const presets: Tool[] = [
  { id: "chatgpt", name: "ChatGPT", category: "text", url: "https://chatgpt.com/", note: "剧本改编、对白润色、剧情分析" },
  { id: "deepseek", name: "DeepSeek", category: "text", url: "https://chat.deepseek.com/", note: "小说理解、长文本分析、剧本初稿" },
  { id: "gemini", name: "Google Gemini", category: "text", url: "https://gemini.google.com/", note: "文本分析与多模态辅助" },
  { id: "chatgpt-storyboard", name: "ChatGPT（分镜规划）", category: "storyboard", url: "https://chatgpt.com/", note: "镜头拆分、景别、机位、动作和时长规划" },
  { id: "deepseek-storyboard", name: "DeepSeek（分镜规划）", category: "storyboard", url: "https://chat.deepseek.com/", note: "剧本结构化与分镜表生成" },
  { id: "doubao", name: "豆包", category: "image", url: "https://www.doubao.com/", note: "人物、场景、道具图片生成；具体能力以账号页面为准" },
  { id: "jimeng", name: "即梦AI", category: "image", url: "https://jimeng.jianying.com/", note: "图片与创意视觉素材" },
  { id: "midjourney", name: "Midjourney", category: "image", url: "https://www.midjourney.com/", note: "风格化概念图与视觉探索" },
  { id: "kling", name: "可灵AI", category: "video", url: "https://klingai.com/", note: "图生视频、文生视频" },
  { id: "runway", name: "Runway", category: "video", url: "https://runwayml.com/", note: "视频生成与视觉处理" },
];

const categoryLabels: Record<ToolCategory, string> = {
  text: "剧本 / 文本",
  image: "图片 / 视觉素材",
  storyboard: "分镜 / 镜头",
  video: "视频生成",
};

const taskExamples: Record<ToolCategory, string> = {
  text: "请根据以下小说内容改编为适合漫剧的剧本，保留主线剧情、人物性格和关键事件，输出场景、人物、动作、对白及旁白。",
  image: "请根据以下设定生成可用于 AI 漫剧的视觉素材。保持人物身份、服装、发型、年龄感和画风一致；如为人物，请优先输出正面、侧面、背面三视图。",
  storyboard: "请把以下剧本拆解为可执行的分镜表。每个镜头给出镜头编号、景别、机位、画面内容、人物动作、对白/旁白、镜头运动和建议时长。",
  video: "请根据以下分镜描述生成视频片段。保持人物外观、服装、场景和镜头方向连续，画面比例与项目设置一致。",
};

function makeId() {
  return `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function safeRead<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export default function AIToolsPage() {
  const [tools, setTools] = useState<Tool[]>(presets);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [category, setCategory] = useState<ToolCategory>("text");
  const [toolId, setToolId] = useState("chatgpt");
  const [title, setTitle] = useState("小说章节改编为漫剧剧本");
  const [sourceText, setSourceText] = useState("");
  const [customName, setCustomName] = useState("");
  const [customUrl, setCustomUrl] = useState("");
  const [customCategory, setCustomCategory] = useState<ToolCategory>("image");
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [resultText, setResultText] = useState("");
  const [resultUrl, setResultUrl] = useState("");
  const [notice, setNotice] = useState("");
  const [apiReady, setApiReady] = useState(false);
  const [taskFiles, setTaskFiles] = useState<Record<string, ResultFile[]>>({});
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

  useEffect(() => {
    const savedTools = safeRead<Tool[]>(STORAGE_TOOLS, presets);
    const savedTasks = safeRead<Task[]>(STORAGE_TASKS, []);
    setTools(savedTools);
    setTasks(savedTasks);
    if (savedTools.length && !savedTools.some((tool) => tool.id === "chatgpt")) {
      setToolId(savedTools[0].id);
    }
    void Promise.all([
      fetch(`${apiBase}/api/web-ai/tools`).then((response) => response.ok ? response.json() : null).catch(() => null),
      fetch(`${apiBase}/api/web-ai/tasks`).then((response) => response.ok ? response.json() : null).catch(() => null),
    ]).then(([toolData, taskData]) => {
      if (Array.isArray(toolData?.items) && toolData.items.length) {
        setTools((current) => {
          const merged = new Map(current.map((item) => [item.id, item]));
          for (const item of toolData.items) merged.set(item.id, { id: item.id, name: item.name, category: item.category, url: item.url, note: item.note || "", custom: Boolean(item.custom) });
          return [...merged.values()];
        });
      }
      if (Array.isArray(taskData?.items) && taskData.items.length) {
        setTasks(taskData.items);
        for (const item of taskData.items) {
          void fetch(apiBase + "/api/web-ai/tasks/" + encodeURIComponent(item.id) + "/files").then((response) => response.ok ? response.json() : null).then((data) => {
            if (Array.isArray(data?.items)) setTaskFiles((current) => ({ ...current, [item.id]: data.items }));
          }).catch(() => undefined);
        }
      }
      setApiReady(true);
    });
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_TOOLS, JSON.stringify(tools));
    } catch {
      setNotice("浏览器本地存储不可用，工具配置可能无法保留。");
    }
  }, [tools]);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_TASKS, JSON.stringify(tasks));
    } catch {
      setNotice("浏览器本地存储空间不足，任务记录可能无法保留。");
    }
  }, [tasks]);

  useEffect(() => {
    if (!apiReady) return;
    for (const tool of tools) {
      void fetch(`${apiBase}/api/web-ai/tools`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: tool.id, name: tool.name, category: tool.category, url: tool.url, note: tool.note, custom: Boolean(tool.custom), enabled: true }),
      }).catch(() => setNotice("本地 API 暂不可用，工具配置暂时仅保存在本机浏览器。"));
    }
  }, [tools, apiReady]);

  useEffect(() => {
    if (!apiReady) return;
    for (const task of tasks) {
      const payload = { id: task.id, title: task.title, category: task.category, toolId: task.toolId, status: task.status, prompt: task.prompt, sourceText: "", resultText: task.resultText, resultUrl: task.resultUrl };
      void fetch(`${apiBase}/api/web-ai/tasks`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      }).then(async (response) => {
        if (response.status === 409) await fetch(`${apiBase}/api/web-ai/tasks/${encodeURIComponent(task.id)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
        });
      }).catch(() => setNotice("本地 API 暂不可用，任务暂时仅保存在本机浏览器。"));
    }
  }, [tasks, apiReady]);

  const categoryTools = useMemo(
    () => tools.filter((tool) => tool.category === category),
    [tools, category],
  );
  const selectedTool = tools.find((tool) => tool.id === toolId && tool.category === category)
    ?? categoryTools[0];

  useEffect(() => {
    if (selectedTool && selectedTool.id !== toolId) setToolId(selectedTool.id);
  }, [selectedTool, toolId]);

  function buildPrompt() {
    return `${taskExamples[category]}\n\n【任务名称】\n${title.trim()}\n\n【项目资料 / 原文 / 设定】\n${sourceText.trim() || "请在此处补充小说原文、人物设定、场景设定或分镜要求。"}\n\n【输出要求】\n请输出可供 AI 漫剧制作大师整理归档的清晰内容；不要省略关键设定。若生成图片或视频，请按当前工具支持的方式提供可下载的原始文件。`;
  }

  async function copyText(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setNotice("任务提示词已复制。");
    } catch {
      setNotice("无法自动复制，请手动选中提示词复制。");
    }
  }

  function createTask() {
    if (!title.trim()) {
      setNotice("请先填写任务名称。");
      return;
    }
    if (!selectedTool) {
      setNotice("当前任务类型没有可用工具，请先添加一个工具。");
      return;
    }
    const task: Task = {
      id: makeId(),
      title: title.trim(),
      category,
      toolId: selectedTool.id,
      prompt: buildPrompt(),
      status: "prepared",
      resultText: "",
      resultUrl: "",
      createdAt: new Date().toISOString(),
    };
    setTasks((current) => [task, ...current]);
    setActiveTaskId(task.id);
    setResultText("");
    setResultUrl("");
    setNotice("任务已准备。点击“复制并打开网页”后，才会跳转到外部 AI 网站。");
  }

  async function openTask(task: Task) {
    const target = tools.find((tool) => tool.id === task.toolId);
    if (!target) {
      setNotice("找不到该任务对应的 AI 工具，请检查工具配置。");
      return;
    }
    try {
      const parsed = new URL(target.url);
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
        throw new Error("只允许 HTTP 或 HTTPS 网站地址");
      }
      const desktop = (window as Window & { desktopAPI?: { openExternal?: (url: string) => Promise<boolean> } }).desktopAPI;
      // Start navigation during the user gesture so normal-browser popup blockers do not suppress it.
      const opening = desktop?.openExternal
        ? desktop.openExternal(parsed.toString())
        : Promise.resolve(window.open(parsed.toString(), "_blank", "noopener,noreferrer"));
      await copyText(task.prompt);
      await opening;
      setTasks((current) => current.map((item) => item.id === task.id ? { ...item, status: "opened" } : item));
      setNotice(`已打开 ${target.name}。提示词已复制，请在网页中粘贴并完成生成；本软件不会读取或绕过该网站的登录与验证码。`);
    } catch {
      setNotice("打开网页失败，请检查工具网址或默认浏览器设置。");
    }
  }

  function addCustomTool() {
    if (!customName.trim() || !customUrl.trim()) {
      setNotice("请填写工具名称和网址。");
      return;
    }
    try {
      const parsed = new URL(customUrl.trim());
      if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
        setNotice("只允许 HTTP 或 HTTPS 网站地址。");
        return;
      }
      const tool: Tool = {
        id: `custom-${Date.now()}`,
        name: customName.trim(),
        category: customCategory,
        url: parsed.toString(),
        note: "用户自定义工具",
        custom: true,
      };
      setTools((current) => [...current, tool]);
      setCategory(customCategory);
      setToolId(tool.id);
      setCustomName("");
      setCustomUrl("");
      setNotice("自定义 AI 工具已添加到本机配置。");
    } catch {
      setNotice("网址格式不正确，请输入完整的网址，例如 https://example.com。");
    }
  }

  function saveResult(task: Task) {
    let normalizedResultUrl = "";
    if (resultUrl.trim()) {
      try {
        const parsed = new URL(resultUrl.trim());
        if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
          setNotice("结果链接只允许 HTTP 或 HTTPS 地址。");
          return;
        }
        normalizedResultUrl = parsed.toString();
      } catch {
        setNotice("结果链接格式不正确。");
        return;
      }
    }
    setTasks((current) => current.map((item) => item.id === task.id
      ? { ...item, status: "completed", resultText, resultUrl: normalizedResultUrl }
      : item));
    setActiveTaskId(null);
    setNotice("结果已保存到本机任务记录。图片和视频文件请先从 AI 网站下载，再到素材库导入。");
  }


  async function uploadResultFile(task: Task, file?: File) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    try {
      const response = await fetch(apiBase + "/api/web-ai/tasks/" + encodeURIComponent(task.id) + "/files", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "文件上传失败");
      setTaskFiles((current) => ({ ...current, [task.id]: [data, ...(current[task.id] || [])] }));
      setNotice("已将 " + file.name + " 保存到本机任务结果库。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "文件上传失败，请确认本地 API 正常运行。");
    }
  }

  function removeTask(taskId: string) {
    setTasks((current) => current.filter((task) => task.id !== taskId));
    if (activeTaskId === taskId) setActiveTaskId(null);
  }

  const activeTask = tasks.find((task) => task.id === activeTaskId);

  return (
    <main className="cosmic min-h-screen px-4 py-6 text-white md:px-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs tracking-[0.3em] text-purple-300">AI WEB TOOL ORCHESTRATION</p>
            <h1 className="mt-2 text-3xl font-black">AI 网页工具协同中心</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-purple-200">
              在本软件中准备任务，按需打开你已登录的 AI 网页，完成生成后把文字结果登记到任务，并将下载的图片、视频导入素材库。
            </p>
          </div>
          <a href="/" className="rounded-xl border border-purple-300/30 px-4 py-2 text-sm text-purple-100 hover:bg-purple-950/50">返回首页</a>
        </header>

        <section className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5 shadow-xl shadow-purple-950/20">
            <div className="mb-4 flex items-center justify-between gap-3">
              <h2 className="text-lg font-bold">① 准备 AI 任务</h2>
              <span className="rounded-full border border-emerald-300/20 bg-emerald-950/30 px-3 py-1 text-xs text-emerald-200">手动确认后才打开网页</span>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-sm text-purple-200">任务类型
                <select value={category} onChange={(event) => setCategory(event.target.value as ToolCategory)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white">
                  {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label className="text-sm text-purple-200">使用的 AI 工具
                <select value={selectedTool?.id ?? ""} onChange={(event) => setToolId(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white">
                  {categoryTools.map((tool) => <option key={tool.id} value={tool.id}>{tool.name}</option>)}
                </select>
              </label>
            </div>
            {selectedTool && <p className="mt-2 text-xs text-slate-400">{selectedTool.note} · {selectedTool.url}</p>}
            <label className="mt-4 block text-sm text-purple-200">任务名称
              <input value={title} onChange={(event) => setTitle(event.target.value)} className="mt-2 w-full rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-white outline-none focus:border-purple-300/60" placeholder="例如：第一章改编为第1集剧本" />
            </label>
            <label className="mt-4 block text-sm text-purple-200">小说原文 / 人物设定 / 分镜要求
              <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} rows={8} className="mt-2 w-full resize-y rounded-xl border border-white/10 bg-slate-900 px-3 py-3 text-sm leading-6 text-white outline-none focus:border-purple-300/60" placeholder="粘贴需要交给 AI 网页处理的内容。建议按章节或镜头分批提交。" />
            </label>
            <div className="mt-4 flex flex-wrap gap-3">
              <button onClick={createTask} className="rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 px-5 py-3 text-sm font-semibold shadow-lg shadow-purple-950/40 hover:brightness-110">生成任务提示词</button>
              <button onClick={() => void copyText(buildPrompt())} className="rounded-xl border border-purple-300/30 px-5 py-3 text-sm text-purple-100 hover:bg-purple-950/50">只复制提示词</button>
            </div>
          </div>

          <div className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
            <h2 className="text-lg font-bold">② 工具管理</h2>
            <p className="mt-2 text-xs leading-5 text-slate-400">内置常用入口，也可以添加你自己的 AI 网站。工具列表只保存在当前电脑的本地浏览器配置中。</p>
            <div className="mt-4 space-y-2">
              {tools.map((tool) => (
                <div key={tool.id} className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-black/20 px-3 py-2.5">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{tool.name} <span className="ml-2 text-xs text-purple-300">{categoryLabels[tool.category]}</span></p>
                    <p className="truncate text-xs text-slate-500">{tool.url}</p>
                  </div>
                  {tool.custom ? <button onClick={() => setTools((current) => current.filter((item) => item.id !== tool.id))} className="shrink-0 text-xs text-red-300 hover:text-red-200">删除</button> : <span className="shrink-0 text-xs text-slate-500">内置</span>}
                </div>
              ))}
            </div>
            <div className="mt-5 border-t border-white/10 pt-4">
              <h3 className="text-sm font-semibold">添加自定义工具</h3>
              <div className="mt-3 grid gap-2">
                <input value={customName} onChange={(event) => setCustomName(event.target.value)} placeholder="工具名称" className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm outline-none focus:border-purple-300/60" />
                <input value={customUrl} onChange={(event) => setCustomUrl(event.target.value)} placeholder="https://AI网站地址" className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm outline-none focus:border-purple-300/60" />
                <select value={customCategory} onChange={(event) => setCustomCategory(event.target.value as ToolCategory)} className="rounded-lg border border-white/10 bg-slate-900 px-3 py-2.5 text-sm">
                  {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
                <button onClick={addCustomTool} className="rounded-lg border border-purple-300/30 px-4 py-2.5 text-sm text-purple-100 hover:bg-purple-950/50">＋ 添加工具</button>
              </div>
            </div>
          </div>
        </section>

        <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-bold">③ 任务与结果归档</h2>
            <a href="/assets" className="rounded-lg border border-cyan-300/30 px-4 py-2 text-xs text-cyan-200 hover:bg-cyan-950/30">打开素材库导入文件 ↗</a>
          </div>
          {notice && <p className="mt-3 rounded-xl border border-purple-300/15 bg-purple-950/30 px-3 py-2 text-xs text-purple-100">{notice}</p>}
          <div className="mt-4 space-y-3">
            {tasks.map((task) => {
              const tool = tools.find((item) => item.id === task.toolId);
              return (
                <article key={task.id} className="rounded-xl border border-white/10 bg-black/20 p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="font-semibold">{task.title}</h3>
                      <p className="mt-1 text-xs text-slate-400">{categoryLabels[task.category]} · {tool?.name ?? "工具已删除"} · {new Date(task.createdAt).toLocaleString()}</p>
                      <p className="mt-2 text-xs text-slate-400">状态：{task.status === "prepared" ? "已准备" : task.status === "opened" ? "已打开网页 / 等待生成" : "结果已登记"}</p>
                      {task.resultUrl && <a href={task.resultUrl} target="_blank" rel="noreferrer" className="mt-2 block break-all text-xs text-cyan-300 underline">结果链接：{task.resultUrl}</a>}
                      {task.resultText && <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs text-slate-300">{task.resultText}</p>}
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button onClick={() => void openTask(task)} className="rounded-lg bg-violet-700 px-3 py-2 text-xs font-medium hover:bg-violet-600">复制并打开网页</button>
                      <button onClick={() => { setActiveTaskId(task.id); setResultText(task.resultText); setResultUrl(task.resultUrl); }} className="rounded-lg border border-emerald-300/30 px-3 py-2 text-xs text-emerald-200 hover:bg-emerald-950/30">登记生成结果</button>
                      <button onClick={() => removeTask(task.id)} className="rounded-lg border border-red-300/20 px-3 py-2 text-xs text-red-200 hover:bg-red-950/30">删除</button>
                      <label className="cursor-pointer rounded-lg border border-cyan-300/30 px-3 py-2 text-xs text-cyan-200 hover:bg-cyan-950/30">
                        导入生成文件
                        <input type="file" className="hidden" accept="image/*,video/*,audio/*,.txt,.md,.json,.srt,.vtt,.pdf,.docx,.csv" onChange={(event) => { void uploadResultFile(task, event.target.files?.[0]); event.currentTarget.value = ""; }} />
                      </label>
                    </div>
                  </div>
                  {(taskFiles[task.id] || []).length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {(taskFiles[task.id] || []).map((file) => (
                        <a key={file.id} href={apiBase + file.downloadUrl} className="rounded-lg border border-cyan-300/20 px-3 py-2 text-xs text-cyan-200 hover:bg-cyan-950/30" download>
                          {file.filename} · {(file.size / 1024 / 1024).toFixed(1)} MB
                        </a>
                      ))}
                    </div>
                  )}
                  {activeTaskId === task.id && (
                    <div className="mt-4 grid gap-3 border-t border-white/10 pt-4">
                      <label className="text-xs text-purple-200">生成结果文字 / 剧本 / 分镜内容
                        <textarea value={resultText} onChange={(event) => setResultText(event.target.value)} rows={5} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm text-white" placeholder="将网页生成的文字复制到这里。" />
                      </label>
                      <label className="text-xs text-purple-200">结果网页链接（可选）
                        <input value={resultUrl} onChange={(event) => setResultUrl(event.target.value)} className="mt-2 w-full rounded-lg border border-white/10 bg-slate-900 px-3 py-2 text-sm text-white" placeholder="https://..." />
                      </label>
                      <div className="flex flex-wrap gap-2">
                        <button onClick={() => saveResult(task)} className="rounded-lg bg-emerald-700 px-4 py-2 text-xs font-semibold hover:bg-emerald-600">保存结果</button>
                        <button onClick={() => setActiveTaskId(null)} className="rounded-lg border border-white/15 px-4 py-2 text-xs">取消</button>
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
            {tasks.length === 0 && <div className="rounded-xl border border-dashed border-white/15 p-8 text-center text-sm text-slate-400">还没有任务。先准备一个剧本、图片、分镜或视频生成任务。</div>}
          </div>
        </section>

        <section className="grid gap-3 md:grid-cols-3">
          {[
            ["网页由你控制", "本阶段通过系统默认浏览器打开 AI 网站，不读取网站密码、Cookie 或验证码。"],
            ["不自动触发付费", "打开网站不等于提交生成任务；复制提示词、提交生成和付费均由你确认。"],
            ["结果可追溯", "任务提示词、工具名称、结果文字和链接保存在本机浏览器；下载文件由你导入素材库。"],
          ].map(([heading, description]) => <div key={heading} className="rounded-xl border border-white/10 bg-slate-950/50 p-4"><h3 className="text-sm font-semibold text-purple-100">{heading}</h3><p className="mt-2 text-xs leading-5 text-slate-400">{description}</p></div>)}
        </section>
      </div>
    </main>
  );
}
