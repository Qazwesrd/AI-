"use client";

import { useEffect, useRef, useState } from "react";

const API = "http://127.0.0.1:8000";

type Platform = {
  id: string;
  name: string;
  creator_url?: string;
  mode?: string;
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
  const [videoUrl, setVideoUrl] = useState("");
  const [coverUrl, setCoverUrl] = useState("");

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");

  const [message, setMessage] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [loading, setLoading] = useState(false);

  const videoInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);

  async function loadData() {
    try {
      const [platformResponse, jobResponse] = await Promise.all([
        fetch(`${API}/api/publishing/platforms`),
        fetch(`${API}/api/publishing/jobs`),
      ]);

      if (!platformResponse.ok || !jobResponse.ok) {
        throw new Error("发布中心接口连接失败");
      }

      const platformData = await platformResponse.json();
      const jobData = await jobResponse.json();

      setPlatforms(platformData.items || []);
      setJobs(jobData.items || []);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "读取数据失败");
    }
  }

  useEffect(() => {
    void loadData();
  }, []);

  useEffect(() => {
    return () => {
      if (videoUrl.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
    };
  }, [videoUrl]);

  useEffect(() => {
    return () => {
      if (coverUrl.startsWith("blob:")) URL.revokeObjectURL(coverUrl);
    };
  }, [coverUrl]);

  function togglePlatform(id: string) {
    setSelected(current =>
      current.includes(id)
        ? current.filter(item => item !== id)
        : [...current, id]
    );
  }

  function uploadFile(file: File, kind: "video" | "cover"): Promise<void> {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);
      form.append("kind", kind);

      const xhr = new XMLHttpRequest();

      xhr.open("POST", `${API}/api/publishing/uploads`);

      xhr.upload.onprogress = event => {
        if (event.lengthComputable) {
          setUploadProgress(Math.round(event.loaded / event.total * 100));
        }
      };

      xhr.onload = () => {
        let result: any;

        try {
          result = JSON.parse(xhr.responseText);
        } catch {
          reject(new Error("服务器返回的数据无法识别"));
          return;
        }

        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(result.detail || "文件上传失败"));
          return;
        }

        if (kind === "video") {
          setVideoPath(result.path);
        } else {
          setCoverPath(result.path);
        }

        resolve();
      };

      xhr.onerror = () => reject(new Error("上传连接中断"));
      xhr.onabort = () => reject(new Error("上传已取消"));
      xhr.send(form);
    });
  }

  async function selectAndUpload(
    event: React.ChangeEvent<HTMLInputElement>,
    kind: "video" | "cover"
  ) {
    const file = event.target.files?.[0];
    if (!file) return;

    setMessage("");
    setUploading(true);
    setUploadProgress(0);

    const preview = URL.createObjectURL(file);

    if (kind === "video") {
      setVideoPath("");
      setVideoUrl(preview);
    } else {
      setCoverPath("");
      setCoverUrl(preview);
    }

    try {
      await uploadFile(file, kind);
      setUploadProgress(100);
      setMessage(
        `${kind === "video" ? "视频" : "封面"}上传成功：${file.name}`
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function createBatch() {
    if (uploading) {
      setMessage("文件仍在上传，请等待上传完成");
      return;
    }

    if (!videoPath) {
      setMessage("请先选择并上传视频文件");
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
            video_path: videoPath,
            cover_path: coverPath || null,
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

      setMessage(`发布任务创建完成，共创建 ${result.created_count ?? "若干"} 个任务`);
      await loadData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "创建任务失败");
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

      if (!response.ok) {
        throw new Error(result.detail || "准备发布失败");
      }

      if (result.creator_url) {
        window.open(result.creator_url, "_blank", "noopener,noreferrer");
      }

      setMessage("已打开平台页面，请按照平台要求完成发布");
      await loadData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    }
  }

  const statusLabels: Record<string, string> = {
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
            <p className="text-sm text-purple-300 mb-2">多媒体中心</p>
            <h1 className="text-3xl md:text-4xl font-bold">多平台视频发布中心</h1>
            <p className="mt-3 text-white/60">视频上传、封面管理、发布文案和任务队列</p>
          </div>
          <a href="/publishing/accounts" className="rounded-xl border border-purple-400/40 px-5 py-3 hover:bg-purple-500/10">
            账号管理
          </a>
        </header>

        {message && (
          <div className="rounded-xl border border-purple-400/30 bg-purple-500/10 p-4 text-sm break-all">
            {message}
          </div>
        )}

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">1. 选择发布平台</h2>

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
                <div className="mt-2 text-xs text-white/40">
                  {selected.includes(platform.id) ? "已选择" : "点击选择"}
                </div>
              </button>
            ))}
          </div>

          <p className="text-sm text-purple-200">已选择 {selected.length} 个平台</p>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">2. 上传视频和封面</h2>

          <div className="space-y-3">
            <div className="font-medium">视频文件</div>

            <button
              type="button"
              onClick={() => videoInput.current?.click()}
              disabled={uploading}
              className="rounded-xl bg-purple-600 px-6 py-3 hover:bg-purple-500 disabled:opacity-50"
            >
              选择并上传视频
            </button>

            <input
              ref={videoInput}
              type="file"
              accept="video/mp4,video/quicktime,video/x-matroska,video/webm,video/x-msvideo"
              className="hidden"
              onChange={event => void selectAndUpload(event, "video")}
            />

            {videoPath && (
              <p className="text-xs text-green-300 break-all">视频已上传：{videoPath}</p>
            )}

            {videoUrl && (
              <video
                src={videoUrl}
                controls
                className="w-full max-h-[440px] rounded-xl bg-black"
              />
            )}
          </div>

          <div className="border-t border-white/10 pt-5 space-y-3">
            <div className="font-medium">视频封面</div>

            <button
              type="button"
              onClick={() => coverInput.current?.click()}
              disabled={uploading}
              className="rounded-xl border border-purple-400/40 px-6 py-3 hover:bg-purple-500/10 disabled:opacity-50"
            >
              选择并上传封面
            </button>

            <input
              ref={coverInput}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              className="hidden"
              onChange={event => void selectAndUpload(event, "cover")}
            />

            {coverPath && (
              <p className="text-xs text-green-300 break-all">封面已上传：{coverPath}</p>
            )}

            {coverUrl && (
              <img
                src={coverUrl}
                alt="封面预览"
                className="max-h-64 rounded-xl object-contain"
              />
            )}
          </div>

          {uploading && (
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span>正在上传文件</span>
                <span>{uploadProgress}%</span>
              </div>
              <div className="h-2 overflow-hidden rounded-full bg-white/10">
                <div
                  className="h-full rounded-full bg-purple-500 transition-all"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            </div>
          )}

          <p className="text-xs text-white/40">
            视频最大支持 2GB；封面最大支持 20MB。上传文件保存在本地 storage/publishing 目录。
          </p>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <h2 className="text-xl font-semibold">3. 发布文案</h2>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">视频标题</span>
            <input
              value={title}
              onChange={event => setTitle(event.target.value)}
              maxLength={200}
              placeholder="请输入视频标题"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">视频简介</span>
            <textarea
              value={description}
              onChange={event => setDescription(event.target.value)}
              rows={4}
              placeholder="请输入视频简介"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm text-white/70">标签</span>
            <input
              value={tags}
              onChange={event => setTags(event.target.value)}
              placeholder="AI漫剧, 动漫, 短剧"
              className="w-full rounded-xl border border-white/15 bg-black/40 p-3"
            />
          </label>

          <button
            type="button"
            disabled={loading || uploading}
            onClick={() => void createBatch()}
            className="rounded-xl bg-purple-600 px-8 py-3 font-semibold hover:bg-purple-500 disabled:opacity-50"
          >
            {loading ? "正在创建..." : "批量创建发布任务"}
          </button>
        </section>

        <section className="rounded-2xl border border-white/10 bg-black/30 p-6 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-xl font-semibold">4. 发布任务队列</h2>
            <button
              type="button"
              onClick={() => void loadData()}
              className="rounded-lg border border-white/20 px-4 py-2 text-sm hover:bg-white/10"
            >
              刷新队列
            </button>
          </div>

          {jobs.length === 0 ? (
            <div className="py-12 text-center text-white/40">暂无发布任务</div>
          ) : (
            <div className="space-y-3">
              {jobs.map(job => (
                <div
                  key={job.id}
                  className="rounded-xl border border-white/10 bg-white/5 p-4 flex flex-col md:flex-row md:items-center justify-between gap-4"
                >
                  <div className="min-w-0">
                    <div className="font-medium">
                      {platforms.find(item => item.id === job.platform)?.name || job.platform}
                      {" · "}
                      {job.title || "未命名视频"}
                    </div>
                    <div className="mt-2 text-xs text-white/40 break-all">{job.video_path}</div>
                    <div className="mt-2 text-sm text-purple-300">
                      {statusLabels[job.status] || job.status}
                    </div>
                    {job.error && <div className="mt-2 text-sm text-red-300">{job.error}</div>}
                  </div>

                  {job.status !== "published" && (
                    <button
                      type="button"
                      onClick={() => void prepareJob(job.id)}
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

        <div className="text-xs text-white/40 leading-6">
          当前完成的是本地文件上传与发布任务素材管理接口。各平台自动发布仍需分别接入官方 OAuth 授权和开放平台 API，不能仅凭创建任务就视为已发布。
        </div>
      </div>
    </main>
  );
}
