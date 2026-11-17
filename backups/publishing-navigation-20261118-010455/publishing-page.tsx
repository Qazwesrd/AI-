"use client";

import { useEffect, useState } from "react";

const API = "http://127.0.0.1:8000";

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
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  async function loadData() {
    try {
      const [p, j] = await Promise.all([
        fetch(`${API}/api/publishing/platforms`),
        fetch(`${API}/api/publishing/jobs`),
      ]);

      if (!p.ok || !j.ok) {
        throw new Error("发布中心接口尚未注册或服务未启动");
      }

      const platformData = await p.json();
      const jobData = await j.json();

      setPlatforms(platformData.items || []);
      setJobs(jobData.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取失败");
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  function togglePlatform(id: string) {
    setSelected(current =>
      current.includes(id)
        ? current.filter(item => item !== id)
        : [...current, id]
    );
  }

  async function createBatch() {
    if (!videoPath.trim()) {
      setMessage("请填写视频文件的完整路径");
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
        body: JSON.stringify(
          selected.map(platform => ({
            platform,
            video_path: videoPath.trim(),
            title,
            description,
            tags: tags.split(/[,，、]/).map(x => x.trim()).filter(Boolean),
          }))
        ),
      });

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.detail || "创建发布任务失败");
      }

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
      const response = await fetch(
        `${API}/api/publishing/jobs/${id}/prepare`,
        { method: "POST" }
      );

      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.detail || "准备失败");
      }

      window.open(result.creator_url, "_blank", "noopener,noreferrer");
      setMessage("已打开对应平台，请在平台页面完成发布");
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
        <header>
          <div className="text-sm text-purple-300 mb-2">
            AI 漫剧制作大师 / 多媒体中心
          </div>
          <h1 className="text-3xl md:text-4xl font-bold">
            多平台视频发布中心
          </h1>
          <p className="text-white/60 mt-3">
            统一准备视频、发布文案和多平台发布任务。
          </p>
        </header>

        {message && (
          <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4 text-sm">
            {message}
          </div>
        )}

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">第一步：选择发布平台</h2>

          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {platforms.map(platform => (
              <button
                key={platform.id}
                type="button"
                onClick={() => togglePlatform(platform.id)}
                className={`rounded-xl border p-4 text-left transition ${
                  selected.includes(platform.id)
                    ? "border-purple-400 bg-purple-500/20"
                    : "border-white/10 bg-white/5 hover:bg-white/10"
                }`}
              >
                <div className="font-medium">{platform.name}</div>
                <div className="text-xs text-white/40 mt-2">
                  {selected.includes(platform.id) ? "已选择" : "点击选择"}
                </div>
              </button>
            ))}
          </div>

          <div className="text-sm text-purple-200">
            已选择 {selected.length} 个平台
          </div>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">第二步：准备发布内容</h2>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">视频文件完整路径</span>
            <input
              value={videoPath}
              onChange={e => setVideoPath(e.target.value)}
              placeholder="例如：D:\AI漫剧制作大师\storage\videos\episode01.mp4"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3 outline-none focus:border-purple-400"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">视频标题</span>
            <input
              value={title}
              onChange={e => setTitle(e.target.value)}
              maxLength={200}
              placeholder="请输入视频标题"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3 outline-none focus:border-purple-400"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">视频简介</span>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={4}
              placeholder="请输入视频简介"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3 outline-none focus:border-purple-400"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">标签（使用逗号分隔）</span>
            <input
              value={tags}
              onChange={e => setTags(e.target.value)}
              placeholder="AI漫剧, 动漫, 短剧"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3 outline-none focus:border-purple-400"
            />
          </label>

          <button
            type="button"
            disabled={loading}
            onClick={createBatch}
            className="rounded-xl bg-purple-600 hover:bg-purple-500 disabled:opacity-50 px-8 py-3 font-semibold"
          >
            {loading ? "正在创建任务..." : "批量创建发布任务"}
          </button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <div className="flex items-center justify-between flex-wrap gap-3">
            <h2 className="text-xl font-semibold">第三步：发布队列</h2>
            <button
              type="button"
              onClick={loadData}
              className="rounded-lg border border-white/20 px-4 py-2 text-sm hover:bg-white/10"
            >
              刷新队列
            </button>
          </div>

          {jobs.length === 0 ? (
            <div className="text-center text-white/40 py-12">
              暂无发布任务
            </div>
          ) : (
            <div className="space-y-3">
              {jobs.map(job => (
                <div
                  key={job.id}
                  className="rounded-xl border border-white/10 bg-white/5 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4"
                >
                  <div className="min-w-0">
                    <div className="font-medium">
                      {platforms.find(p => p.id === job.platform)?.name || job.platform}
                      {" · "}
                      {job.title || "未命名视频"}
                    </div>
                    <div className="text-xs text-white/40 mt-2 break-all">
                      {job.video_path}
                    </div>
                    <div className="text-sm text-purple-300 mt-2">
                      {statusText[job.status] || job.status}
                    </div>
                    {job.error && (
                      <div className="text-sm text-red-300 mt-2">{job.error}</div>
                    )}
                  </div>

                  {job.status !== "published" && (
                    <button
                      type="button"
                      onClick={() => prepareJob(job.id)}
                      className="shrink-0 rounded-lg bg-purple-600 px-4 py-2 text-sm hover:bg-purple-500"
                    >
                      前往平台发布
                    </button>
                  )}

                  {job.publish_url && (
                    <a
                      href={job.publish_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm text-purple-300 underline"
                    >
                      查看发布视频
                    </a>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>

        <p className="text-xs text-white/40 leading-6">
          提示：平台自动发布需要各平台官方 API、开发者资质和用户授权。
          当前页面提供发布任务管理和官方页面辅助发布，不会冒充已经完成自动发布授权。
        </p>
      </div>
    </main>
  );
}
