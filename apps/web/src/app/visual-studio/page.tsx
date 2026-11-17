"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Novel = { id: number; title?: string; name?: string };

type Asset = {
  id: number;
  category: string;
  name: string;
  description: string;
  appearance: string;
  clothing: string;
  personality: string;
  consistency_prompt: string;
  style: string;
  image_path: string;
  confirmed: boolean;
  data?: {
    model3d_task?: { task_id?: number; status?: string };
    model3d?: { path?: string; format?: string };
    generated_views?: Record<string, { path?: string }>;
  };
};

type Task = {
  id: number;
  task_type: string;
  prompt: string;
  status: string;
  error: string;
  result_path: string;
};

const categories = [
  { key: "characters", label: "人物角色" },
  { key: "scenes", label: "场景环境" },
  { key: "props", label: "普通道具" },
  { key: "weapons", label: "武器与刀具" },
  { key: "costumes", label: "服装与装备" },
  { key: "buildings", label: "建筑与地标" },
  { key: "special_items", label: "特殊物品" },
];

const styles = [
  "3D动漫电影风格",
  "真人电影风格",
  "2D动漫风格",
  "国风漫画风格",
  "水墨动画风格",
  "自定义参考图风格",
];

export default function VisualStudioPage() {
  const [category, setCategory] = useState("characters");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [novels, setNovels] = useState<Novel[]>([]);
  const [sourceNovelId, setSourceNovelId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [appearance, setAppearance] = useState("");
  const [clothing, setClothing] = useState("");
  const [personality, setPersonality] = useState("");
  const [consistency, setConsistency] = useState("");
  const [style, setStyle] = useState(styles[0]);
  const [prompt, setPrompt] = useState("");
  const [selectedAsset, setSelectedAsset] = useState<number | null>(null);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [confirm3dPaid, setConfirm3dPaid] = useState(false);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function loadNovels() {
    try {
      const response = await fetch(`${API}/api/story/novels`);
      if (response.ok) setNovels(await response.json());
    } catch {}
  }

  async function importAnalysisAssets() {
    if (!sourceNovelId) { setMessage("请先选择要同步的小说"); return; }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/assets/import-analysis/${sourceNovelId}`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "同步失败");
      setMessage(result.message + ` 新增 ${result.created} 项，更新 ${result.updated} 项，跳过 ${result.skipped} 项。`);
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "同步失败");
    } finally { setBusy(false); }
  }

  async function loadAssets() {
    try {
      const response = await fetch(`${API}/api/visual/assets?category=${category}`);
      if (response.ok) setAssets(await response.json());
    } catch {}
  }

  async function loadTasks() {
    try {
      const response = await fetch(`${API}/api/visual/tasks`);
      if (response.ok) setTasks(await response.json());
    } catch {}
  }

  useEffect(() => {
    loadAssets();
  }, [category]);

  useEffect(() => { loadNovels(); }, []);

  useEffect(() => {
    loadTasks();
  }, []);

  async function saveAsset() {
    if (!name.trim()) {
      setMessage("请先填写素材名称");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/assets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          category, name, description, appearance, clothing,
          personality, consistency_prompt: consistency, style, data: {},
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "保存失败");
      setMessage("素材设定已保存");
      setName("");
      setDescription("");
      setAppearance("");
      setClothing("");
      setPersonality("");
      setConsistency("");
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  function buildPrompt() {
    const selected = assets.find((item) => item.id === selectedAsset);
    const base = selected
      ? `${selected.name}。原文设定：${selected.description}。外观：${selected.appearance}。服装/装备：${selected.clothing}。性格/气质：${selected.personality}。必须保持的固定特征：${selected.consistency_prompt}。`
      : "";
    const viewGuide = category === "characters"
      ? "生成同一角色标准三视图设定稿：正面全身、严格90度侧面全身、背面全身，三个视角必须是同一张画布内同一人物、同一身高比例、同一服装、同一发型、同一装备；中性站姿，纯净背景，不添加文字水印，不得生成三个不同人物。"
      : category === "weapons"
      ? "生成武器/刀具专业三视图：正视图、侧视图、背视图，完整展示刃口、刀背、护手、握柄、刀鞘、连接结构和材质纹理；严格保持同一件武器的比例和结构，不得擅自添加不存在的零件。"
      : category === "props" || category === "special_items"
      ? "生成物品正面、侧面、背面三视图，完整展示轮廓、尺寸比例、材质、颜色、纹理、磨损和独特标记，三个视角必须是同一件物品。"
      : category === "costumes"
      ? "生成服装装备正面、侧面、背面三视图，清晰展示剪裁、层次、系带、护甲、配饰、材质和纹样；服装结构必须一致。"
      : category === "buildings"
      ? "生成建筑正立面、侧立面和整体轴测参考图，保持建筑结构、层数、屋顶、门窗、材质和比例一致。"
      : "生成电影级环境概念设定图，建立明确空间布局、地形、建筑位置、光照方向、天气、时间和可供人物行动的区域；保持可复用的场景地理结构。";
    const full = `${base}${prompt}。${viewGuide}视觉风格：${style}。高质量电影级3D资产设定，清晰锐利，真实材质细节，统一比例，专业角色/道具设计稿。`;
    setPrompt(full);
  }

  async function createTask() {
    if (!prompt.trim()) {
      setMessage("请填写图片生成提示词");
      return;
    }
    setBusy(true);
    try {
      const response = await fetch(`${API}/api/visual/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          asset_id: selectedAsset,
          provider: "doubao",
          task_type: category,
          prompt,
          size: "2K",
          confirm_paid: confirmPaid,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "创建任务失败");
      setMessage(`图片任务 #${result.task_id} 已加入队列`);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建任务失败");
    } finally {
      setBusy(false);
    }
  }

  async function runTask(id: number) {
    setBusy(true);
    try {
      const response = await fetch(
        `${API}/api/visual/tasks/${id}/run?confirm_paid=${confirmPaid}`,
        { method: "POST" }
      );
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "生成失败");
      setMessage(`图片任务 #${id} 已完成`);
      await loadTasks();
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "生成失败");
      await loadTasks();
    } finally {
      setBusy(false);
    }
  }


  async function generateProfessionalPrompt(assetId: number) {
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + assetId + "/prompt");
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "提示词生成失败");
      setPrompt(result.positive_prompt + "\n\n负面提示词：" + result.negative_prompt);
      setSelectedAsset(assetId);
      setMessage("已生成专业正向/负面提示词，可检查后再创建图片任务。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "提示词生成失败");
    } finally { setBusy(false); }
  }

  async function createViewBatch(assetId: number) {
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + assetId + "/views?provider=doubao", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "创建多视角任务失败");
      setMessage(result.message);
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建多视角任务失败");
    } finally { setBusy(false); }
  }

  async function confirmAsset(asset: Asset) {
    if (!window.confirm("确认“" + asset.name + "”的设定已审核无误？确认后，AI 分镜和三维生成将优先复用这份素材。")) return;
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + asset.id + "/confirm", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "确认素材失败");
      setMessage("素材“" + asset.name + "”已确认。");
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认素材失败");
    } finally { setBusy(false); }
  }

  async function generateEditable3D(asset: Asset) {
    if (!asset.confirmed) {
      setMessage("请先确认素材设定，再生成真实三维模型。");
      return;
    }
    if (!window.confirm("将把已生成的正面、侧面、背面参考图上传到 Tripo，并调用多视图转 3D 模型 API。该服务可能消耗付费额度，是否继续？")) return;
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + asset.id + "/model3d/tripo", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider: "tripo3d", model: "v3.1-20260211", texture: true, pbr: true, texture_quality: "detailed", geometry_quality: "standard", confirm_paid: confirm3dPaid }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "3D 模型任务提交失败");
      setMessage("已提交 Tripo 三维模型任务 #" + result.task_id + "。输入视角：" + (result.input_views || []).join("、") + "。稍后可点击查询并下载 GLB。");
      await loadAssets();
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "3D 模型生成失败");
    } finally { setBusy(false); }
  }

  async function refreshEditable3D(asset: Asset) {
    const taskId = asset.data?.model3d_task?.task_id;
    if (!taskId) return setMessage("该素材尚无 3D 模型任务。");
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/tasks/" + taskId + "/refresh-model3d", { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "查询 3D 模型失败");
      setMessage(result.status === "completed" ? "GLB 三维模型已下载到本地素材库。" : result.status === "failed" ? (result.error || "3D 任务失败") : "3D 模型仍在生成，当前进度：" + (result.progress ?? "未知") + "%");
      await loadAssets();
      await loadTasks();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "查询 3D 模型失败");
    } finally { setBusy(false); }
  }

  async function addEvolution(asset: Asset) {
    const chapter = window.prompt("变化发生的章节号：");
    if (!chapter || !/^[1-9]\d*$/.test(chapter)) return;
    const event = window.prompt("记录剧情事件/变化：");
    if (!event || !event.trim()) return;
    const changeType = window.prompt("变化类型（状态变化/损坏/升级/易主/外观变化）：", "状态变化") || "状态变化";
    const appearanceAfter = window.prompt("变化后的外观（可留空）：", "") || "";
    setBusy(true);
    try {
      const response = await fetch(API + "/api/visual/assets/" + asset.id + "/evolution", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chapter_number: Number(chapter), event, change_type: changeType, appearance_after: appearanceAfter }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "记录变化失败");
      setMessage("已记录第 " + chapter + " 章变化，共 " + result.history_count + " 条。");
      await loadAssets();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "记录变化失败");
    } finally { setBusy(false); }
  }

  async function uploadReference(file?: File) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    if (selectedAsset) form.append("asset_id", String(selectedAsset));
    try {
      const response = await fetch(`${API}/api/visual/references`, {
        method: "POST",
        body: form,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "上传失败");
      setMessage("参考图片已上传");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    }
  }

  const panel = "rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5 shadow-xl shadow-purple-950/20";
  const field = "w-full rounded-xl border border-purple-300/20 bg-slate-900/80 px-3 py-2.5 text-sm text-white outline-none focus:border-purple-400";
  const button = "rounded-xl border border-purple-300/30 bg-purple-600/80 px-4 py-2 text-sm font-medium text-white transition hover:bg-purple-500 disabled:opacity-50";

  return (
    <main className="min-h-screen space-y-6 p-6 text-white">
      <header>
        <a
          href="/"
          className="mb-5 inline-flex items-center gap-2 rounded-xl border border-purple-300/30 bg-purple-950/60 px-4 py-2.5 text-sm font-medium text-white transition hover:border-purple-300/70 hover:bg-purple-800/60"
        >← 返回主页</a>
        <div className="text-sm text-purple-300">AI COMIC STUDIO / VISUAL LAB</div>
        <h1 className="mt-2 text-3xl font-bold">AI视觉素材生产中心</h1>
        <p className="mt-2 text-sm text-slate-300">
          统一管理人物、场景、普通道具、武器刀具、服装、建筑和特殊物品，并为后续镜头生成提供统一视觉参考。
        </p>
      </header>

      {message && (
        <div className="rounded-xl border border-purple-300/20 bg-purple-950/50 p-3 text-sm text-purple-100">
          {message}
        </div>
      )}

      <section className="grid gap-3 md:grid-cols-3">
        {categories.map((item) => (
          <button
            key={item.key}
            onClick={() => setCategory(item.key)}
            className={`rounded-xl border p-4 text-left transition ${
              category === item.key
                ? "border-purple-400 bg-purple-700/40"
                : "border-purple-300/20 bg-slate-950/60 hover:bg-purple-950/40"
            }`}
          >
            <div className="text-lg font-semibold">{item.label}</div>
            <div className="mt-1 text-xs text-slate-400">
              {item.key === "characters" ? "人物身份、外貌、服装、三视图与角色一致性" :
               item.key === "scenes" ? "地点布局、环境、时间、天气、氛围与场景参考图" :
               item.key === "weapons" ? "刀身、刃口、刀背、护手、握柄、刀鞘与武器结构" :
               item.key === "costumes" ? "服装剪裁、层次、护甲、配饰、材质与纹样" :
               item.key === "buildings" ? "建筑立面、屋顶、门窗、结构、材质与比例" :
               item.key === "special_items" ? "法器、神器、信物、关键线索与独特物品" :
               "普通道具外观、材质、颜色、尺寸与三视图"}
            </div>
          </button>
        ))}
      </section>

      <section className="grid gap-6 xl:grid-cols-2">
        <div className={panel}>
          <h2 className="mb-4 text-xl font-semibold">建立视觉素材设定</h2>
          <div className="space-y-3">
            <input className={field} placeholder="素材名称" value={name} onChange={(e) => setName(e.target.value)} />
            <textarea className={field} rows={2} placeholder="基础描述 / 故事背景" value={description} onChange={(e) => setDescription(e.target.value)} />
            <textarea className={field} rows={3} placeholder="外貌、外形、颜色、体型、材质等" value={appearance} onChange={(e) => setAppearance(e.target.value)} />
            <textarea className={field} rows={2} placeholder="服装、装备或表面细节（场景和道具可填写对应特征）" value={clothing} onChange={(e) => setClothing(e.target.value)} />
            <textarea className={field} rows={2} placeholder="性格、气质或视觉氛围" value={personality} onChange={(e) => setPersonality(e.target.value)} />
            <textarea className={field} rows={3} placeholder="固定特征：发型、脸型、颜色、标志物等，供后续保持一致" value={consistency} onChange={(e) => setConsistency(e.target.value)} />
            <select className={field} value={style} onChange={(e) => setStyle(e.target.value)}>
              {styles.map((item) => <option key={item}>{item}</option>)}
            </select>
            <button className={button} disabled={busy} onClick={saveAsset}>保存素材设定</button>
          </div>
        </div>

        <div className={panel}>
          <h2 className="mb-4 text-xl font-semibold">AI图片生成</h2>
          <div className="space-y-3">
            <label className="block text-sm text-slate-300">关联素材</label>
            <select
              className={field}
              value={selectedAsset ?? ""}
              onChange={(e) => setSelectedAsset(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">不关联素材</option>
              {assets.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <textarea
              className={field}
              rows={7}
              placeholder="填写图片生成要求，例如：角色正面、侧面、背面三视图，纯色背景，全身站立，三个人物视角保持同一比例……"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <button className="rounded-xl border border-sky-300/30 bg-sky-700/60 px-4 py-2 text-sm text-white hover:bg-sky-600" onClick={buildPrompt}>
              根据素材设定组合提示词
            </button>
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input type="checkbox" checked={confirmPaid} onChange={(e) => setConfirmPaid(e.target.checked)} />
              我已了解本次图片生成可能产生费用，并确认执行
            </label>
            <div className="flex flex-wrap gap-2">
              <button className={button} disabled={busy} onClick={createTask}>创建图片任务</button>
              <label className="cursor-pointer rounded-xl border border-purple-300/30 px-4 py-2 text-sm hover:bg-purple-950/50">
                上传参考图片
                <input type="file" accept="image/*" className="hidden" onChange={(e) => uploadReference(e.target.files?.[0])} />
              </label>
            </div>
            <p className="text-xs leading-5 text-slate-400">
              图片生成只在你主动点击执行后调用。付费服务还需要 AI 管理中心开启付费授权并在本页确认。
            </p>
          </div>
        </div>
      </section>

      <section className={panel}>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl font-semibold">当前素材库</h2>
          <div className="flex flex-wrap gap-2">
            <select className="max-w-xs rounded-xl border border-purple-300/20 bg-slate-900 px-3 py-2 text-sm text-white" value={sourceNovelId} onChange={(e) => setSourceNovelId(e.target.value)}>
              <option value="">选择小说，同步 AI 分析资产</option>
              {novels.map((novel) => <option key={novel.id} value={novel.id}>{novel.title || novel.name || `小说 #${novel.id}`}</option>)}
            </select>
            <button className={button} disabled={busy || !sourceNovelId} onClick={importAnalysisAssets}>同步 AI 识别结果</button>
          </div>
          <button className={button} onClick={loadAssets}>刷新素材</button>
        </div>
        {assets.length === 0 ? (
          <p className="text-sm text-slate-400">当前分类还没有素材。</p>
        ) : (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {assets.map((item) => (
              <article key={item.id} className="rounded-xl border border-purple-300/15 bg-slate-900/70 p-4">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-semibold">{item.name}</h3>
                  <span className="text-xs text-slate-400">{item.confirmed ? "已确认" : "待确认"}</span>
                </div>
                <p className="mt-2 line-clamp-3 text-sm text-slate-400">{item.description || item.appearance}</p>
                <p className="mt-2 text-xs text-purple-300">{item.style}</p>
                {item.image_path && (
                  <p className="mt-2 break-all text-xs text-slate-500">图片已生成：{item.image_path}</p>
                )}
                <button
                  className="mt-3 rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs hover:bg-purple-900/50"
                  onClick={() => {
                    setSelectedAsset(item.id);
                    setName(item.name);
                    setDescription(item.description || "");
                    setAppearance(item.appearance || "");
                    setClothing(item.clothing || "");
                    setPersonality(item.personality || "");
                    setConsistency(item.consistency_prompt || "");
                    setPrompt("");
                  }}
                >
                  使用此素材生成图片
                </button>
                <div className="mt-3 flex flex-wrap gap-2">
                  {!item.confirmed && <button className="rounded-lg border border-amber-300/30 px-3 py-1.5 text-xs text-amber-200 hover:bg-amber-900/30" disabled={busy} onClick={() => confirmAsset(item)}>审核通过并确认素材</button>}
                  <button className="rounded-lg border border-sky-300/20 px-3 py-1.5 text-xs hover:bg-sky-900/40" disabled={busy} onClick={() => generateProfessionalPrompt(item.id)}>生成专业提示词</button>
                  {item.confirmed && (
                    <>
                      <label className="flex items-center gap-1 text-xs text-amber-200"><input type="checkbox" checked={confirm3dPaid} onChange={e => setConfirm3dPaid(e.target.checked)} />确认本次 3D 生成可能收费</label>
                      <button className="rounded-lg border border-emerald-300/30 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-900/30 disabled:opacity-50" disabled={busy} onClick={() => generateEditable3D(item)}>生成真实3D模型（Tripo）</button>
                    </>
                  )}
                  {item.data?.model3d_task?.task_id && <button className="rounded-lg border border-cyan-300/30 px-3 py-1.5 text-xs text-cyan-200 hover:bg-cyan-900/30" disabled={busy} onClick={() => refreshEditable3D(item)}>查询/下载3D模型</button>}
                  {item.data?.model3d?.path && <a className="rounded-lg border border-green-300/30 px-3 py-1.5 text-xs text-green-200 hover:bg-green-900/30" href={API + "/api/visual/models3d/" + encodeURIComponent(item.data.model3d.path.split(/[\\/]/).pop() || "")} target="_blank" rel="noreferrer">下载 GLB 模型</a>}
                  <button className="rounded-lg border border-purple-300/20 px-3 py-1.5 text-xs hover:bg-purple-900/50" disabled={busy} onClick={() => createViewBatch(item.id)}>创建多视角任务</button>
                  {(item.category === "props" || item.category === "weapons" || item.category === "special_items" || item.category === "costumes") && (
                    <button className="rounded-lg border border-amber-300/20 px-3 py-1.5 text-xs hover:bg-amber-900/30" disabled={busy} onClick={() => addEvolution(item)}>记录剧情变化</button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className={panel}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-xl font-semibold">图片生成任务队列</h2>
          <button className={button} onClick={loadTasks}>刷新任务</button>
        </div>
        <div className="space-y-3">
          {tasks.length === 0 && <p className="text-sm text-slate-400">还没有图片生成任务。</p>}
          {tasks.map((task) => (
            <div key={task.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-purple-300/15 bg-slate-900/70 p-3">
              <div className="min-w-0 flex-1">
                <div className="font-medium">任务 #{task.id} · {task.task_type}</div>
                <p className="mt-1 line-clamp-2 text-xs text-slate-400">{task.prompt}</p>
                {task.error && <p className="mt-1 text-xs text-red-300">{task.error}</p>}
              </div>
              <span className="text-xs text-purple-300">{task.status}</span>
              {task.status === "pending" && (
                <button className={button} disabled={busy} onClick={() => runTask(task.id)}>执行生成</button>
              )}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
