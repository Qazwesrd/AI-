"use client";

import { useEffect, useRef, useState } from "react";

const API = "http://127.0.0.1:8000";

declare global {
  interface Window {
    desktop?: {
      selectFile?: (options?: {
        filters?: { name: string; extensions: string[] }[];
      }) => Promise<string | null>;
    };
  }
}

type Platform = {
  id: string;
  name: string;
  creator_url: string;
  mode: string;
};

type Job = {
  id: number;
  platform: string;
  title: string;
  status: string;
  video_path: string;
  publish_url?: string;
  error?: string;
};

export default function PublishingPage() {
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [videoPath, setVideoPath] = useState("");
  const [coverPath, setCoverPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [coverUrl, setCoverUrl] = useState("");
  const [videoError, setVideoError] = useState("");
  const videoInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);

  async function loadData() {
    try {
      const [p, j] = await Promise.all([
        fetch(`${API}/api/publishing/platforms`),
        fetch(`${API}/api/publishing/jobs`),
      ]);

      if (!p.ok || !j.ok) {
        throw new Error("发布中心 API 尚未正常连接");
      }

      const pd = await p.json();
      const jd = await j.json();

      setPlatforms(pd.items || []);
      setJobs(jd.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取失败");
    }
  }

  useEffect(() => {
    void loadData();
    return () => {
      if (videoUrl.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
      if (coverUrl.startsWith("blob:")) URL.revokeObjectURL(coverUrl);
    };
  }, [videoUrl, coverUrl]);

  function togglePlatform(id: string) {
    setSelected(current =>
      current.includes(id)
        ? current.filter(item => item !== id)
        : [...current, id]
    );
  }

  async function chooseDesktopFile(type: "video" | "cover") {
    try {
      const filters = type === "video"
        ? [{ name: "视频文件", extensions: ["mp4", "mov", "mkv", "webm", "avi"] }]
        : [{ name: "图片文件", extensions: ["png", "jpg", "jpeg", "webp"] }];

      const path = await window.desktop?.selectFile?.({ filters });

      if (!path) return;

      if (type === "video") {
        setVideoPath(path);
        setVideoError("已选择本地视频。请注意：发布前仍需将视频上传到发布服务。");
        setVideoUrl("");
      } else {
        setCoverPath(path);
        setCoverUrl("");
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "文件选择失败");
    }
  }

  function chooseBrowserFile(type: "video" | "cover", file?: File) {
    if (!file) return;

    const url = URL.createObjectURL(file);

    if (type === "video") {
      setVideoPath("");
      setVideoUrl(url);
      setVideoError("");
    } else {
      setCoverPath("");
      setCoverUrl(url);
    }

    setMessage("浏览器预览文件仅供预览，尚未上传到服务器。");
  }

  async function createBatch() {
    if (!videoPath.trim()) {
      setMessage("请先选择视频文件并填写服务器可访问的视频路径");
      return;
    }

    if (selected.length === 0) {
      setMessage("请至少选择一个发布平台");
      return;
    }

    setLoading(true);
    setMessage("");

    try {
      const response = await fetch(`${API}/api/publishing/jobs/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selected.map(platform => ({
          platform,
          video_path: videoPath.trim(),
          cover_path: coverPath,
          title,
          description,
          tags: tags.split(/[,，、]/).map(x => x.trim()).filter(Boolean),
        }))),
      });

      const result = await response.json();

      if (!response.ok) throw new Error(result.detail || "创建发布任务失败");

      setMessage(`成功创建 ${result.created_count} 个发布任务`);
      await loadData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建失败");
    } finally {
      setLoading(false);
    }
  }

  async function prepareJob(id: number) {
    try {
      const response = await fetch(`${API}/api/publishing/jobs/${id}/prepare`, {
        method: "POST",
      });

      const result = await response.json();

      if (!response.ok) throw new Error(result.detail || "准备失败");

      window.open(result.creator_url, "_blank", "noopener,noreferrer");
      setMessage("已打开平台创作者页面，请完成视频发布。");
      await loadData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    }
  }

  const statusText: Record<string, string> = {
    pending: "等待发布",
    scheduled: "定时等待",
    ready: "素材已准备",
    published: "已发布",
    failed: "发布失败",
  };

  return (
    <main className="min-h-screen p-6 md:p-10 text-white">
      <div className="mx-auto max-w-7xl space-y-8">
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-sm text-purple-300 mb-2">AI 漫剧制作大师 / 多媒体中心</div>
            <h1 className="text-3xl md:text-4xl font-bold">多平台视频发布中心</h1>
            <p className="text-white/60 mt-3">统一管理视频、封面、发布文案和多平台任务。</p>
          </div>
          <a href="/publishing/accounts" className="rounded-xl border border-purple-400/40 px-5 py-3 text-purple-200 hover:bg-purple-500/10">
            账号管理
          </a>
        </header>

        {message && (
          <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4 text-sm break-words">
            {message}
          </div>
        )}

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">一、选择发布平台</h2>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {platforms.map(platform => (
              <button key={platform.id} type="button" onClick={() => togglePlatform(platform.id)}
                className={`rounded-xl border p-4 text-left transition ${selected.includes(platform.id) ? "border-purple-400 bg-purple-500/20" : "border-white/10 bg-white/5 hover:bg-white/10"}`}>
                <div className="font-medium">{platform.name}</div>
                <div className="text-xs text-white/40 mt-2">{selected.includes(platform.id) ? "已选择" : "点击选择"}</div>
              </button>
            ))}
          </div>
          <div className="text-sm text-purple-200">已选择 {selected.length} 个平台</div>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">二、视频和封面</h2>

          <div className="flex flex-wrap gap-3">
            <button type="button" onClick={() => void chooseDesktopFile("video")} className="rounded-xl bg-purple-600 px-5 py-3 hover:bg-purple-500">
              从电脑选择视频
            </button>
            <button type="button" onClick={() => videoInput.current?.click()} className="rounded-xl border border-white/20 px-5 py-3 hover:bg-white/10">
              浏览器选择视频预览
            </button>
            <input ref={videoInput} type="file" accept="video/*" className="hidden" onChange={e => chooseBrowserFile("video", e.target.files?.[0])}/>
          </div>

          {videoPath && <div className="text-sm text-purple-200 break-all">视频路径：{videoPath}</div>}
          {videoError && <div className="text-xs text-yellow-300">{videoError}</div>}
          {videoUrl && <video src={videoUrl} controls className="w-full max-h-[420px] rounded-xl bg-black"/>}

          <div className="border-t border-white/10 pt-5 space-y-3">
            <div className="font-medium">视频封面</div>
            <div className="flex flex-wrap gap-3">
              <button type="button" onClick={() => void chooseDesktopFile("cover")} className="rounded-xl border border-purple-400/40 px-5 py-3 hover:bg-purple-500/10">
                从电脑选择封面
              </button>
              <button type="button" onClick={() => coverInput.current?.click()} className="rounded-xl border border-white/20 px-5 py-3 hover:bg-white/10">
                浏览器选择图片预览
              </button>
              <input ref={coverInput} type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={e => chooseBrowserFile("cover", e.target.files?.[0])}/>
            </div>
            {coverPath && <div className="text-sm text-purple-200 break-all">封面路径：{coverPath}</div>}
            {coverUrl && <img src={coverUrl} alt="视频封面预览" className="max-h-64 rounded-xl object-contain"/>}
          </div>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">三、发布文案</h2>
          <label className="block space-y-2">
            <span className="text-sm text-white/70">标题</span>
            <input value={title} onChange={e => setTitle(e.target.value)} maxLength={200} placeholder="请输入视频标题" className="w-full rounded-xl border border-white/15 bg-black/40 p-3"/>
          </label>
          <label className="block space-y-2">
            <span className="text-sm text-white/70">简介</span>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={4} placeholder="请输入视频简介" className="w-full rounded-xl border border-white/15 bg-black/40 p-3"/>
          </label>
          <label className="block space-y-2">
            <span className="text-sm text-white/70">标签（逗号分隔）</span>
            <input value={tags} onChange={e => setTags(e.target.value)} placeholder="AI漫剧, 动漫, 短剧" className="w-full rounded-xl border border-white/15 bg-black/40 p-3"/>
          </label>
          <button type="button" disabled={loading} onClick={createBatch} className="rounded-xl bg-purple-600 hover:bg-purple-500 disabled:opacity-50 px-8 py-3 font-semibold">
            {loading ? "正在创建任务..." : "批量创建发布任务"}
          </button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <h2 className="text-xl font-semibold">四、发布队列</h2>
            <button type="button" onClick={() => void loadData()} className="rounded-lg border border-white/20 px-4 py-2 text-sm hover:bg-white/10">刷新队列</button>
          </div>
          {jobs.length === 0 ? (
            <div className="text-center text-white/40 py-12">暂无发布任务</div>
          ) : (
            <div className="space-y-3">
              {jobs.map(job => (
                <div key={job.id} className="rounded-xl border border-white/10 bg-white/5 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="font-medium">{platforms.find(p => p.id === job.platform)?.name || job.platform} · {job.title || "未命名视频"}</div>
                    <div className="text-xs text-white/40 mt-2 break-all">{job.video_path}</div>
                    <div className="text-sm text-purple-300 mt-2">{statusText[job.status] || job.status}</div>
                    {job.error && <div className="text-sm text-red-300 mt-2">{job.error}</div>}
                  </div>
                  {job.status !== "published" && (
                    <button type="button" onClick={() => void prepareJob(job.id)} className="shrink-0 rounded-lg bg-purple-600 px-4 py-2 text-sm hover:bg-purple-500">
                      前往平台发布
                    </button>
                  )}
                  {job.publish_url && <a href={job.publish_url} target="_blank" rel="noreferrer" className="text-sm text-purple-300 underline">查看发布视频</a>}
                </div>
              ))}
            </div>
          )}
        </section>

        <p className="text-xs text-white/40 leading-6">
          注意：本页面已支持本地文件选择和浏览器预览。正式创建任务要求视频文件位于 API 服务可访问的安全存储目录。当前尚未实现文件上传和平台自动发布授权。
        </p>
      </div>
    </main>
  );
}
