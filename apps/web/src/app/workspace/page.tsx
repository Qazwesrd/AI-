"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Novel = {
  id: number; title: string; description?: string; genre?: string; status?: string;
  chapter_count: number; word_count: number; updated_at?: string | null;
};
type Chapter = { id: number; chapter_number: number; title: string; word_count: number };
type AnalysisItem = { id: number; category: string; name: string; confirmed: boolean; data?: Record<string, unknown> };
type StoryProject = {
  id: number; novel_id: number; name: string; status: string; adaptation_style?: string;
  generation_blocked?: boolean; source_change_notice?: { status?: string } | null;
};
type Episode = { id: number; episode_number: number; title: string; status: string };
type Shot = { id: number; shot_number: number };
type Asset = { id: number; category: string; name: string; confirmed?: boolean };
type Task = {
  id: string; source: string; source_id: number; title: string; status: string;
  status_group?: string; summary?: string; updated_at?: string | null;
};
type VideoTask = { id: number; status: string; result_path?: string; error?: string };
type PublishingStats = { total?: number; pending?: number; ready?: number; published?: number; failed?: number };

async function api<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`, { cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败：${response.status}`);
  return data as T;
}

function countLabel(value: number | undefined, suffix = "") {
  return `${value ?? 0}${suffix}`;
}

function statusText(value?: string) {
  const labels: Record<string, string> = {
    completed: "已完成", ready: "已就绪", processing: "处理中", running: "运行中",
    pending: "等待中", queued: "排队中", failed: "失败", cancelled: "已取消",
    source_changed: "原文有变更，待复核", draft: "草稿", planned: "已规划",
  };
  return value ? labels[value] || value : "尚未开始";
}

function Stage({ number, title, detail, done, href }: {
  number: string; title: string; detail: string; done: boolean; href: string;
}) {
  return (
    <a href={href} className="group flex gap-3 rounded-xl border border-purple-300/15 bg-slate-950/45 p-4 transition hover:border-purple-300/45 hover:bg-purple-950/35">
      <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold ${done ? "bg-emerald-500/20 text-emerald-200" : "bg-purple-500/15 text-purple-200"}`}>
        {done ? "✓" : number}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold text-white">{title}</span>
        <span className="mt-1 block text-xs leading-5 text-slate-400">{detail}</span>
      </span>
      <span className="self-center text-purple-300 transition group-hover:translate-x-1">→</span>
    </a>
  );
}

export default function WorkspacePage() {
  const [novels, setNovels] = useState<Novel[]>([]);
  const [selectedNovelId, setSelectedNovelId] = useState<number | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisItem[]>([]);
  const [projects, setProjects] = useState<StoryProject[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [shots, setShots] = useState<Shot[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [videoTasks, setVideoTasks] = useState<VideoTask[]>([]);
  const [publishing, setPublishing] = useState<PublishingStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState("");

  const selectedNovel = useMemo(
    () => novels.find((item) => item.id === selectedNovelId) || null,
    [novels, selectedNovelId]
  );
  const novelProjects = useMemo(
    () => projects.filter((item) => item.novel_id === selectedNovelId),
    [projects, selectedNovelId]
  );
  const selectedProject = useMemo(
    () => novelProjects.find((item) => item.id === selectedProjectId) || novelProjects[0] || null,
    [novelProjects, selectedProjectId]
  );
  const confirmedAnalysis = analysis.filter((item) => item.confirmed).length;
  const completedTasks = tasks.filter((item) => item.status_group === "completed" || item.status === "completed").length;
  const activeTasks = tasks.filter((item) => ["active", "attention", "partial"].includes(item.status_group || "")).length;
  const failedTasks = tasks.filter((item) => item.status_group === "failed" || item.status === "failed").length;
  const renderedVideos = videoTasks.filter((item) => Boolean(item.result_path) && item.status === "completed").length;

  const loadOverview = useCallback(async (preferredNovelId?: number | null) => {
    setLoading(true);
    setError("");
    try {
      const [novelResult, projectResult, taskResult, publishingResult] = await Promise.all([
        api<{ items: Novel[] }>("/api/novels"),
        api<StoryProject[]>("/api/story/projects"),
        api<{ items: Task[] }>("/api/tasks?limit=100&offset=0"),
        api<PublishingStats>("/api/publishing/stats"),
      ]);
      const list = novelResult.items || [];
      setNovels(list);
      setProjects(Array.isArray(projectResult) ? projectResult : []);
      setTasks(taskResult.items || []);
      setPublishing(publishingResult);
      const queryId = Number(new URLSearchParams(window.location.search).get("novelId"));
      const wanted = preferredNovelId ?? (Number.isFinite(queryId) && queryId > 0 ? queryId : null);
      const nextId = wanted && list.some((item) => item.id === wanted) ? wanted : (selectedNovelId && list.some((item) => item.id === selectedNovelId) ? selectedNovelId : list[0]?.id ?? null);
      setSelectedNovelId(nextId);
      setLastUpdated(new Date().toLocaleString());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "项目总览加载失败");
    } finally {
      setLoading(false);
    }
  }, [selectedNovelId]);

  const loadNovelDetails = useCallback(async (novelId: number, projectId?: number | null) => {
    setDetailLoading(true);
    setError("");
    try {
      const [chapterResult, analysisResult] = await Promise.all([
        api<{ items: Chapter[] }>(`/api/novels/${novelId}/chapters`),
        api<{ items: AnalysisItem[] }>(`/api/novels/${novelId}/analysis`),
      ]);
      setChapters(chapterResult.items || []);
      setAnalysis(analysisResult.items || []);
      const related = projects.filter((item) => item.novel_id === novelId);
      const activeProject = related.find((item) => item.id === projectId) || related[0] || null;
      setSelectedProjectId(activeProject?.id ?? null);
      if (activeProject) {
        const detail = await api<{ project?: StoryProject; episodes: Episode[] }>(`/api/story/projects/${activeProject.id}`);
        const episodeList = detail.episodes || [];
        setEpisodes(episodeList);
        const [assetList, shotLists, videoLists, taskResult] = await Promise.all([
          api<Asset[]>(`/api/story/assets?project_id=${activeProject.id}`),
          Promise.all(episodeList.map((episode) => api<Shot[]>(`/api/story/episodes/${episode.id}/shots`).catch(() => []))),
          Promise.all(episodeList.map((episode) => api<VideoTask[]>(`/api/media/video/episodes/${episode.id}/tasks`).catch(() => []))),
          api<{ items: Task[] }>(`/api/tasks?limit=100&offset=0&project_id=${activeProject.id}`),
        ]);
        setAssets(Array.isArray(assetList) ? assetList : []);
        setShots(shotLists.flat());
        setVideoTasks(videoLists.flat());
        setTasks(taskResult.items || []);
      } else {
        setEpisodes([]);
        setShots([]);
        setAssets([]);
        setVideoTasks([]);
        const taskResult = await api<{ items: Task[] }>("/api/tasks?limit=100&offset=0");
        setTasks(taskResult.items || []);
      }
      const url = new URL(window.location.href);
      url.searchParams.set("novelId", String(novelId));
      if (activeProject) url.searchParams.set("projectId", String(activeProject.id));
      else url.searchParams.delete("projectId");
      window.history.replaceState({}, "", url.toString());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "项目详情加载失败");
      setEpisodes([]);
      setShots([]);
      setAssets([]);
      setVideoTasks([]);
    } finally {
      setDetailLoading(false);
    }
  }, [projects]);

  useEffect(() => { void loadOverview(); }, [loadOverview]);
  useEffect(() => {
    if (selectedNovelId !== null && projects.length >= 0) {
      const projectId = Number(new URLSearchParams(window.location.search).get("projectId"));
      void loadNovelDetails(selectedNovelId, Number.isFinite(projectId) && projectId > 0 ? projectId : null);
    } else {
      setChapters([]);
      setAnalysis([]);
      setEpisodes([]);
      setShots([]);
      setAssets([]);
      setVideoTasks([]);
    }
  }, [selectedNovelId, projects, loadNovelDetails]);

  function chooseNovel(id: number) {
    setSelectedNovelId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("novelId", String(id));
    url.searchParams.delete("projectId");
    window.history.replaceState({}, "", url.toString());
  }

  function chooseProject(id: number) {
    setSelectedProjectId(id);
    const url = new URL(window.location.href);
    url.searchParams.set("projectId", String(id));
    window.history.replaceState({}, "", url.toString());
    if (selectedNovelId !== null) void loadNovelDetails(selectedNovelId, id);
  }

  if (loading && novels.length === 0) {
    return <main className="cosmic min-h-screen p-8 text-white"><p className="text-purple-200">正在加载项目工作台……</p></main>;
  }

  return (
    <main className="cosmic min-h-screen text-white">
      <div className="nebula nebula-one" /><div className="nebula nebula-two" />
      <div className="relative z-10 mx-auto max-w-7xl p-5 lg:p-8">
        <header className="mb-7 flex flex-wrap items-end justify-between gap-4">
          <div>
            <a href="/" className="text-sm text-purple-300 hover:text-white">← 返回首页</a>
            <h1 className="mt-3 text-3xl font-black">项目统一工作台</h1>
            <p className="mt-2 text-sm text-purple-200">一个页面查看小说、分析、素材、分镜、视频、任务与发布进度</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-purple-300/20 bg-black/20 px-3 py-2 text-xs text-purple-200">{lastUpdated ? `更新于 ${lastUpdated}` : "项目数据实时读取"}</span>
            <button onClick={() => void loadOverview(selectedNovelId)} disabled={loading} className="rounded-xl border border-purple-300/30 px-4 py-2 text-sm hover:bg-purple-900/40 disabled:opacity-50">↻ 刷新总览</button>
          </div>
        </header>

        {error && <div role="alert" className="mb-5 rounded-xl border border-amber-300/30 bg-amber-950/30 p-4 text-sm text-amber-100">{error}</div>}

        <section className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><h2 className="text-lg font-bold">选择小说项目</h2><p className="mt-1 text-xs text-purple-200/70">选择后自动汇总该小说关联的改编项目与制作数据</p></div>
              <a href="/novels" className="rounded-lg bg-purple-600 px-3 py-2 text-xs font-semibold hover:bg-purple-500">＋ 新建 / 导入小说</a>
            </div>
            {novels.length ? <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {novels.map((novel) => <button key={novel.id} onClick={() => chooseNovel(novel.id)} className={`rounded-xl border p-3 text-left transition ${selectedNovelId === novel.id ? "border-fuchsia-300 bg-purple-700/30" : "border-purple-300/15 bg-black/20 hover:bg-purple-900/30"}`}>
                <span className="block truncate font-semibold">{novel.title}</span><span className="mt-2 block text-xs text-purple-200/75">{novel.genre || "未分类"} · {novel.chapter_count} 章 · {novel.word_count.toLocaleString()} 字</span>
              </button>)}
            </div> : <p className="mt-5 rounded-xl bg-black/20 p-5 text-sm text-purple-200">还没有小说项目。先创建或导入小说，再开始漫剧制作。</p>}
          </div>
          <div className="rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
            <p className="text-xs text-purple-200">当前项目</p>
            <h2 className="mt-2 text-xl font-bold">{selectedNovel?.title || "尚未选择"}</h2>
            <p className="mt-2 text-xs text-slate-400">{selectedNovel ? `项目编号 #${selectedNovel.id} · ${selectedNovel.status || "进行中"}` : "创建小说后将在此显示"}</p>
            <div className="mt-5 grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">章节</p><p className="mt-1 text-2xl font-bold">{countLabel(selectedNovel?.chapter_count)}</p></div>
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">字数</p><p className="mt-1 text-2xl font-bold">{(selectedNovel?.word_count || 0).toLocaleString()}</p></div>
            </div>
          </div>
        </section>

        {selectedNovel && <>
          <section className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            {[
              ["章节", chapters.length, "小说内容"], ["分析条目", analysis.length, `已确认 ${confirmedAnalysis}`],
              ["视觉素材", assets.length, "当前改编项目"], ["分集", episodes.length, "剧本规划"],
              ["镜头", shots.length, "分镜数量"], ["成片", renderedVideos, `视频任务 ${videoTasks.length}`],
            ].map(([label, value, note]) => <div key={String(label)} className="rounded-xl border border-purple-300/15 bg-[#160d2acc] p-4"><p className="text-xs text-purple-200/75">{label}</p><p className="mt-2 text-2xl font-black tabular-nums">{Number(value).toLocaleString()}</p><p className="mt-1 text-[11px] text-slate-400">{note}</p></div>)}
          </section>

          <section className="mb-6 rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-bold">创作进度与下一步</h2><p className="mt-1 text-xs text-slate-400">进度根据当前数据库中实际存在的数据计算，不会虚报完成状态</p></div>{detailLoading && <span className="text-xs text-cyan-200">正在同步项目详情…</span>}</div>
            <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              <Stage number="01" title="小说导入与章节整理" detail={chapters.length ? `已读取 ${chapters.length} 章，可继续编辑正文` : "尚未导入章节，先导入小说正文"} done={chapters.length > 0} href={`/novels?novelId=${selectedNovel.id}`} />
              <Stage number="02" title="剧情分析与人工确认" detail={analysis.length ? `${analysis.length} 条分析，${confirmedAnalysis} 条已确认` : "尚无分析数据，先运行本地提取或经确认后调用 AI"} done={analysis.length > 0 && confirmedAnalysis > 0} href={`/ai-analysis?novelId=${selectedNovel.id}`} />
              <Stage number="03" title="人物、场景与道具素材" detail={assets.length ? `当前改编项目关联 ${assets.length} 项素材` : "尚未关联素材，可从分析结果同步或手动创建"} done={assets.length > 0} href={`/visual-studio?novelId=${selectedNovel.id}`} />
              <Stage number="04" title="剧本改编与分镜" detail={episodes.length ? `${episodes.length} 集 · ${shots.length} 个镜头` : "尚未创建改编项目或分集剧本"} done={episodes.length > 0 && shots.length > 0} href={`/storyboard?novelId=${selectedNovel.id}`} />
              <Stage number="05" title="视频、配音与合成" detail={videoTasks.length ? `${renderedVideos} 个视频任务已完成` : "尚无视频任务，进入分镜工作室生成"} done={renderedVideos > 0} href={`/media-studio?novelId=${selectedNovel.id}`} />
              <Stage number="06" title="成片质检与发布准备" detail={`发布任务 ${publishing?.total ?? 0} 个 · 已登记发布 ${publishing?.published ?? 0} 个`} done={(publishing?.published ?? 0) > 0} href="/publishing" />
            </div>
          </section>

          {novelProjects.length > 0 && <section className="mb-6 rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
            <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-bold">漫剧改编项目</h2><p className="mt-1 text-xs text-slate-400">可切换同一小说下的不同改编版本</p></div><a href={`/storyboard?novelId=${selectedNovel.id}`} className="text-xs text-purple-200 hover:text-white">进入分镜工作室 →</a></div>
            <div className="mt-3 flex flex-wrap gap-2">{novelProjects.map((project) => <button key={project.id} onClick={() => chooseProject(project.id)} className={`rounded-xl border px-3 py-2 text-left text-sm ${selectedProject?.id === project.id ? "border-fuchsia-300 bg-purple-700/30" : "border-purple-300/20 bg-black/20 hover:bg-purple-900/30"}`}><span className="font-semibold">{project.name}</span><span className="ml-2 text-xs text-slate-400">{statusText(project.status)}</span></button>)}</div>
            {selectedProject?.generation_blocked && <p className="mt-3 rounded-lg border border-amber-300/25 bg-amber-950/20 p-3 text-xs text-amber-100">原文或章节发生变更，该改编项目已暂停后续生成；请先复核旧分析与剧本，再继续制作。</p>}
          </section>}
        </>}

        <section className="mb-6 grid gap-4 lg:grid-cols-2">
          <div className="rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
            <h2 className="text-lg font-bold">统一任务状态</h2><p className="mt-1 text-xs text-slate-400">来自任务中心的真实任务记录</p>
            <div className="mt-4 grid grid-cols-3 gap-2">
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">已完成</p><p className="mt-1 text-xl font-bold text-emerald-200">{completedTasks}</p></div>
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">进行中 / 待核实</p><p className="mt-1 text-xl font-bold text-cyan-200">{activeTasks}</p></div>
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">失败</p><p className="mt-1 text-xl font-bold text-rose-200">{failedTasks}</p></div>
            </div>
            <div className="mt-4 space-y-2">{tasks.slice(0, 5).map((task) => <div key={task.id} className="flex items-center justify-between gap-3 rounded-lg bg-black/20 px-3 py-2 text-xs"><span className="min-w-0 truncate">{task.title || task.summary || `${task.source} #${task.source_id}`}</span><span className="shrink-0 text-purple-200">{statusText(task.status)}</span></div>)}{tasks.length === 0 && <p className="text-sm text-slate-400">暂无任务记录</p>}</div>
            <a href={selectedProject ? `/tasks?projectId=${selectedProject.id}` : "/tasks"} className="mt-4 inline-flex text-sm text-purple-200 hover:text-white">打开当前项目任务 →</a>
          </div>
          <div className="rounded-2xl border border-purple-300/20 bg-[#160d2acc] p-5">
            <h2 className="text-lg font-bold">制作与发布概况</h2><p className="mt-1 text-xs text-slate-400">不会自动创建发布任务或上传视频</p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">发布任务</p><p className="mt-1 text-2xl font-bold">{publishing?.total ?? 0}</p></div>
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">已登记发布</p><p className="mt-1 text-2xl font-bold text-emerald-200">{publishing?.published ?? 0}</p></div>
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">待处理</p><p className="mt-1 text-xl font-bold">{publishing?.pending ?? 0}</p></div>
              <div className="rounded-xl bg-black/25 p-3"><p className="text-xs text-slate-400">失败</p><p className="mt-1 text-xl font-bold text-rose-200">{publishing?.failed ?? 0}</p></div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2"><a href="/media-studio" className="rounded-lg border border-purple-300/25 px-3 py-2 text-xs hover:bg-purple-900/30">媒体制作</a><a href="/video-editor" className="rounded-lg border border-purple-300/25 px-3 py-2 text-xs hover:bg-purple-900/30">独立视频剪辑</a><a href="/publishing" className="rounded-lg bg-purple-600 px-3 py-2 text-xs font-semibold hover:bg-purple-500">发布中心</a></div>
          </div>
        </section>

        <footer className="rounded-xl border border-cyan-300/15 bg-cyan-950/15 p-4 text-xs leading-6 text-cyan-100/80">
          安全说明：本工作台只读取并汇总现有项目数据。不会因打开页面自动调用 AI、生成图片/视频、产生付费任务、自动充值或向平台发布内容。涉及外部服务或费用的操作，仍需在对应功能页由你主动发起并确认。
        </footer>
      </div>
    </main>
  );
}
