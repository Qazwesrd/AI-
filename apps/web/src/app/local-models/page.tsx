"use client";

import { useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type LicenseReview =
  | "unverified"
  | "checked_by_user"
  | "commercial_permission_confirmed"
  | "noncommercial_only"
  | "restricted";

type ModelItem = {
  name: string;
  format: string;
  size_bytes: number;
  license_review: LicenseReview;
  license_name: string;
  license_url?: string | null;
  notes: string;
  license_notice: string;
  compatibility: {
    status: string;
    label: string;
    file_size_bytes: number;
    ram_bytes?: number | null;
    vram_bytes?: number | null;
    note: string;
  };
};

type RuntimeModel = { name: string; size_bytes?: number | null };
type Runtime = { status: string; models: RuntimeModel[]; message: string };
type InstallerInfo = { engine_status: string; model: string; model_size_bytes: number; required_free_bytes: number; free_bytes: number | null; enough_space: boolean; installed: boolean | null; download_source: string; license: string; license_url: string; job: { status: string; stage: string; percent: number; completed_bytes: number; total_bytes: number; error?: string | null } };

type LocalImageRuntime = {
  status: string; message: string; checkpoint_count: number; checkpoints: string[];
  hardware: { ram_bytes: number | null; vram_bytes: number | null; gpu_level: string; reasons: string[]; note: string };
  limitations?: string;
};
type HardwareReadiness = {
  model: string; status: string; label: string; free_disk_bytes: number | null;
  ram_bytes: number | null; vram_bytes: number | null; logical_cores: number | null;
  operating_system: { status: string; message: string; supported?: boolean | null };
  reasons: string[]; blockers: string[]; note: string;
  ollama_running: boolean; ollama_model_installed: boolean;
};
type OllamaRuntimeStatus = { installed: boolean; status: string; freeBytes: number | null; requiredFreeBytes: number; enoughSpace: boolean; installDirectory: string };
type OllamaInstallProgress = { status: string; stage: string; percent?: number | null; receivedBytes?: number; totalBytes?: number; message: string };
type ComfyUIReadiness = {
  status: string; can_install_engine: boolean; hardware_profile: string;
  ram_bytes: number | null; vram_bytes: number | null; free_disk_bytes: number | null;
  engine_disk_reserve_bytes: number; optional_model_disk_reserve_bytes: number;
  recommended_total_reserve_bytes: number; blockers: string[]; warnings: string[];
  install_target: string; note: string;
};
type ComfyUIReleaseCatalog = {
  version: string; published_at: string; source: string;
  assets: { name: string; download_url: string; size_bytes: number; sha256: string | null; digest_available: boolean }[];
  download_started: boolean; installation_started: boolean; note: string;
};
declare global {
  interface Window {
    localAIInstallerDesktop?: {
      getStatus: () => Promise<OllamaRuntimeStatus>;
      install: (confirm: boolean) => Promise<{ status: string }>;
      cancelDownload: () => Promise<{ cancelled: boolean }>;
      onProgress: (callback: (progress: OllamaInstallProgress) => void) => () => void;
    };
  }
}

type Catalog = {
  items: ModelItem[];
  total: number;
  directory: string;
  network_used: boolean;
  downloaded: boolean;
  note: string;
};

const licenseLabels: Record<LicenseReview, string> = {
  unverified: "未核验",
  checked_by_user: "用户已人工查看",
  commercial_permission_confirmed: "用户确认允许商用",
  noncommercial_only: "仅限非商业用途",
  restricted: "限制使用 / 暂不可用",
};

function size(value?: number | null) {
  if (value === undefined || value === null) return "未知";
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024 ** 3).toFixed(2)} GB`;
}

export default function LocalModelsPage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [selected, setSelected] = useState("");
  const [licenseReview, setLicenseReview] = useState<LicenseReview>("unverified");
  const [licenseName, setLicenseName] = useState("");
  const [licenseUrl, setLicenseUrl] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [baseModel, setBaseModel] = useState("");
  const [newModelName, setNewModelName] = useState("comic-writer");
  const [modelRole, setModelRole] = useState("all_rounder");
  const [modelStyle, setModelStyle] = useState("国漫");
  const [modelInstructions, setModelInstructions] = useState("");
  const [creatingModel, setCreatingModel] = useState(false);
  const [installerInfo, setInstallerInfo] = useState<InstallerInfo | null>(null);
  const [installingBaseModel, setInstallingBaseModel] = useState(false);
  const [runtimeInstallStatus, setRuntimeInstallStatus] = useState<OllamaRuntimeStatus | null>(null);
  const [runtimeInstallProgress, setRuntimeInstallProgress] = useState<OllamaInstallProgress | null>(null);
  const [installingRuntime, setInstallingRuntime] = useState(false);
  const [hasDesktopInstaller, setHasDesktopInstaller] = useState(false);
  const [hardwareReadiness, setHardwareReadiness] = useState<HardwareReadiness | null>(null);
  const [checkingHardware, setCheckingHardware] = useState(false);
  const [imageRuntime, setImageRuntime] = useState<LocalImageRuntime | null>(null);
  const [checkingImageRuntime, setCheckingImageRuntime] = useState(false);
  const [comfyReadiness, setComfyReadiness] = useState<ComfyUIReadiness | null>(null);
  const [checkingComfyReadiness, setCheckingComfyReadiness] = useState(false);
  const [comfyReleaseCatalog, setComfyReleaseCatalog] = useState<ComfyUIReleaseCatalog | null>(null);
  const [loadingComfyReleaseCatalog, setLoadingComfyReleaseCatalog] = useState(false);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/local-models`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法读取本地模型库");
      setCatalog(result);
      setSelected((current) => current || result.items[0]?.name || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取模型库失败");
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshHardwareReadiness = useCallback(async () => {
    setCheckingHardware(true);
    try {
      const response = await fetch(`${API}/api/local-ai/hardware-readiness`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "硬件检测失败");
      setHardwareReadiness(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "硬件检测失败");
    } finally {
      setCheckingHardware(false);
    }
  }, []);

  const refreshImageRuntime = useCallback(async () => {
    setCheckingImageRuntime(true);
    try {
      const response = await fetch(`${API}/api/local-ai/image-runtime`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "本地图片引擎检测失败");
      setImageRuntime(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "本地图片引擎检测失败");
    } finally {
      setCheckingImageRuntime(false);
    }
  }, []);

  const refreshComfyReadiness = useCallback(async () => {
    setCheckingComfyReadiness(true);
    try {
      const response = await fetch(`${API}/api/local-ai/comfyui-installer/readiness`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "ComfyUI 安装前检测失败");
      setComfyReadiness(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "ComfyUI 安装前检测失败");
    } finally {
      setCheckingComfyReadiness(false);
    }
  }, []);

  const loadComfyReleaseCatalog = useCallback(async () => {
    if (!window.confirm("将连接 Comfy-Org 官方 GitHub，只读取最新 ComfyUI Windows 安装包的版本、文件大小和校验摘要，不会下载或安装文件。此操作会使用网络流量。是否继续？")) return;
    setLoadingComfyReleaseCatalog(true);
    setError("");
    try {
      const response = await fetch(`${API}/api/local-ai/comfyui-installer/release-catalog`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "读取官方 ComfyUI 版本信息失败");
      setComfyReleaseCatalog(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取官方 ComfyUI 版本信息失败");
    } finally {
      setLoadingComfyReleaseCatalog(false);
    }
  }, []);

  const refreshInstaller = useCallback(async () => {
    try {
      const response = await fetch(`${API}/api/local-ai/model-installer/info`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法读取模型安装状态");
      setInstallerInfo(result);
    } catch (err) { setError(err instanceof Error ? err.message : "模型安装状态读取失败"); }
  }, []);

  useEffect(() => { void refresh(); void refreshRuntime(); void refreshInstaller(); void refreshHardwareReadiness(); void refreshImageRuntime(); void refreshComfyReadiness(); }, [refresh, refreshInstaller, refreshHardwareReadiness, refreshImageRuntime, refreshComfyReadiness]);

  useEffect(() => {
    const desktop = window.localAIInstallerDesktop;
    if (!desktop) return;
    setHasDesktopInstaller(true);
    let mounted = true;
    void desktop.getStatus().then(result => { if (mounted) setRuntimeInstallStatus(result); }).catch(() => {});
    const unsubscribe = desktop.onProgress(progress => {
      if (!mounted) return;
      setRuntimeInstallProgress(progress);
      if (["success", "failed", "cancelled"].includes(progress.status)) {
        setInstallingRuntime(false);
        void desktop.getStatus().then(result => { if (mounted) setRuntimeInstallStatus(result); }).catch(() => {});
        void refreshRuntime();
        void refreshInstaller();
      }
    });
    return () => { mounted = false; unsubscribe(); };
  }, [refreshInstaller]);

  useEffect(() => {
    if (!installerInfo || !["downloading", "verifying"].includes(installerInfo.job.status)) return;
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`${API}/api/local-ai/model-installer/status`, { cache: "no-store" });
        const result = await response.json();
        if (response.ok) setInstallerInfo(current => current ? { ...current, job: result } : current);
        if (["success", "failed", "cancelled"].includes(result.status)) {
          window.clearInterval(timer); setInstallingBaseModel(false); void refreshRuntime(); void refreshInstaller();
        }
      } catch { /* transient polling errors are retried on the next tick */ }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [installerInfo?.job.status, refreshInstaller]);

  const refreshRuntime = async () => {
    try {
      const response = await fetch(`${API}/api/local-ai/runtime`, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "无法检测本地 Ollama");
      setRuntime(result);
      setBaseModel((current) => current || result.models?.[0]?.name || "");
    } catch (err) {
      setRuntime({ status: "error", models: [], message: err instanceof Error ? err.message : "本地引擎检测失败" });
    }
  };

  const installOllamaRuntime = async () => {
    const desktop = window.localAIInstallerDesktop;
    if (!desktop) {
      setError("自动安装本地运行环境需要 Windows 桌面安装版。请安装桌面版后再使用此功能。");
      return;
    }
    if (!window.confirm("即将从 Ollama 官方网站下载并安装本地运行环境。安装程序可能需要较大网络流量，安装至少需要约 4 GB 磁盘空间（建议预留 5 GiB）。之后还需单独下载约 2.5 GB 的 Qwen3 4B 模型，并为模型预留至少 6 GiB 空间。不会产生 API 费用，也不会连接云端模型。是否继续？")) return;
    setInstallingRuntime(true); setError(""); setMessage("");
    setRuntimeInstallProgress({ status: "starting", stage: "准备下载", percent: 0, message: "正在启动安装流程" });
    try {
      const result = await desktop.install(true);
      if (result.status === "already_installed") {
        setMessage("检测到 Ollama 已安装并运行。");
        setInstallingRuntime(false);
      } else {
        setMessage("已启动 Ollama 本地运行环境安装。");
      }
      const status = await desktop.getStatus();
      setRuntimeInstallStatus(status);
      await refreshRuntime();
      await refreshInstaller();
    } catch (err) {
      setInstallingRuntime(false);
      setError(err instanceof Error ? err.message : "Ollama 本地运行环境安装失败");
      const status = await desktop.getStatus().catch(() => null);
      if (status) setRuntimeInstallStatus(status);
    }
  };

  const cancelOllamaDownload = async () => {
    try {
      const result = await window.localAIInstallerDesktop?.cancelDownload();
      if (result?.cancelled) setMessage("已发送取消下载请求。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "取消下载失败");
    }
  };

  const startBaseModelInstall = async () => {
    if (!installerInfo?.enough_space || installerInfo.installed) return;
    if (!window.confirm("即将从 Ollama 官方模型库下载 Qwen3 4B（约 2.5 GB）。需要至少 6 GiB 可用磁盘空间，并会消耗网络流量；模型本身不收 API 费用。请先阅读模型许可证。是否继续？")) return;
    setInstallingBaseModel(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API}/api/local-ai/model-installer/start`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: "qwen3:4b", confirm_download: true }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "模型下载启动失败");
      setMessage(result.status === "already_installed" ? "Qwen3 4B 已安装。" : "已开始下载 Qwen3 4B。下载期间可以查看实时进度。");
      await refreshInstaller();
    } catch (err) { setInstallingBaseModel(false); setError(err instanceof Error ? err.message : "模型下载启动失败"); }
  };

  const cancelBaseModelInstall = async () => {
    try {
      const response = await fetch(`${API}/api/local-ai/model-installer/cancel`, { method: "POST" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "取消下载失败");
      setMessage("已发送取消请求；Ollama 支持后续继续下载。");
      await refreshInstaller();
    } catch (err) { setError(err instanceof Error ? err.message : "取消下载失败"); }
  };

  const createSpecializedModel = async () => {
    if (!baseModel || !newModelName.trim()) return;
    if (!window.confirm("将使用已安装的基础模型，在本机创建一个新的专用模型。不会下载模型或调用云端服务。继续吗？")) return;
    setCreatingModel(true); setError(""); setMessage("");
    try {
      const response = await fetch(`${API}/api/local-ai/models/create`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_model: baseModel, name: newModelName, role: modelRole, style: modelStyle, instructions: modelInstructions }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "专用模型创建失败");
      setMessage(`专用模型 ${result.model} 已在本机创建。刷新模型列表即可选择使用。`);
      setNewModelName("");
      await refreshRuntime();
    } catch (err) { setError(err instanceof Error ? err.message : "专用模型创建失败"); }
    finally { setCreatingModel(false); }
  };

  const chooseModel = (item: ModelItem) => {
    setSelected(item.name);
    setLicenseReview(item.license_review);
    setLicenseName(item.license_name || "");
    setLicenseUrl(item.license_url || "");
    setNotes(item.notes || "");
    setMessage("");
  };

  const saveReview = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch(`${API}/api/local-models/review`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filename: selected,
          license_review: licenseReview,
          license_name: licenseName,
          license_url: licenseUrl.trim() || null,
          notes,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "保存许可证备注失败");
      setMessage("模型资料已保存。许可证内容由你填写，软件没有替你核验授权。");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const selectedItem = catalog?.items.find((item) => item.name === selected);

  return (
    <main className="cosmic min-h-screen p-5 text-white md:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">本地 AI 模型管理</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">
              管理已经放入本机模型目录的文件，查看粗略硬件适配建议，并记录你自己核对过的许可证信息。
            </p>
            <div className="mt-3 flex flex-wrap gap-2"><a href="/local-creator" className="inline-flex rounded-lg bg-cyan-500 px-3 py-2 text-sm font-semibold text-slate-950">打开本地创作工作台</a><a href="/system-diagnostics" className="inline-flex rounded-lg border border-purple-300/30 px-3 py-2 text-sm">返回系统诊断中心</a></div>
          </div>
          <button disabled={busy} onClick={() => void refresh()} className="rounded-xl bg-purple-600 px-4 py-3 text-sm font-semibold disabled:opacity-40">
            {busy ? "处理中…" : "刷新模型库"}
          </button>
        </header>

        <section className="rounded-2xl border border-emerald-300/20 bg-emerald-400/5 p-4">
          <div className="font-semibold text-emerald-100">严格本地管理模式</div>
          <p className="mt-2 text-sm leading-6 text-white/65">
            本页面不会自动联网或下载。只有在你明确确认后，才会下载官方运行环境或模型文件；不会调用云端 API，也不会产生模型服务费。
            本地运行仍会使用电脑硬件和电力。许可证信息需要你自行查阅官方来源，软件不会把用户填写的状态当作法律核验结果。
          </p>
        </section>

        <section className="rounded-2xl border border-cyan-300/25 bg-slate-950/70 p-5">
          <h2 className="text-xl font-semibold">一键安装本地创作模型</h2>
          <p className="mt-2 text-sm leading-6 text-white/60">推荐文字模型：Qwen3 4B（Q4_K_M，约 2.5 GB）。用于小说理解、剧本、人物设定、剧情分析、分镜和图片提示词；它本身不生成图片、视频或配音。</p>
          {installerInfo ? (
            <div className="mt-4 space-y-3">
              <div className="grid gap-2 text-sm md:grid-cols-3">
                <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-white/45">模型大小</div><div className="mt-1 font-semibold">{size(installerInfo.model_size_bytes)}</div></div>
                <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-white/45">磁盘预留要求</div><div className="mt-1 font-semibold">{size(installerInfo.required_free_bytes)}</div></div>
                <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-white/45">当前可用空间</div><div className="mt-1 font-semibold">{size(installerInfo.free_bytes)}</div></div>
              </div>
              <div className="rounded-xl bg-white/[0.04] p-3 text-sm">
                <div>本地 Ollama：{installerInfo.engine_status === "ready" ? "已运行" : "未运行"}</div>
                <div className="mt-1">模型状态：{installerInfo.installed ? "已安装" : installerInfo.installed === false ? "尚未安装" : "暂时无法检测"}</div>
                <div className="mt-1 break-all text-xs text-white/45">模型目录：{installerInfo.model ? "Ollama 默认模型目录或 OLLAMA_MODELS 设置目录" : "未知"}</div>
                <div className="mt-2 text-xs text-white/50">许可证：{installerInfo.license} · <a className="text-cyan-200 underline" href={installerInfo.license_url} target="_blank" rel="noreferrer">查看模型许可证</a></div>
                <div className="mt-1 text-xs text-white/50">来源：<a className="text-cyan-200 underline" href={installerInfo.download_source} target="_blank" rel="noreferrer">Ollama 官方模型库</a></div>
              </div>
              {["downloading", "verifying"].includes(installerInfo.job.status) && (
                <div className="rounded-xl border border-cyan-300/20 p-3">
                  <div className="flex justify-between gap-3 text-sm"><span>{installerInfo.job.stage}</span><span>{installerInfo.job.percent}%</span></div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/10"><div className="h-full rounded-full bg-cyan-400 transition-all" style={{ width: `${installerInfo.job.percent}%` }} /></div>
                  <div className="mt-1 text-xs text-white/45">{size(installerInfo.job.completed_bytes)} / {size(installerInfo.job.total_bytes)}</div>
                  <button type="button" onClick={() => void cancelBaseModelInstall()} className="mt-3 rounded-lg border border-rose-300/30 px-3 py-2 text-sm text-rose-100">取消下载</button>
                </div>
              )}
              {installerInfo.job.status === "failed" && <p className="text-sm text-rose-200">{installerInfo.job.error || "下载失败，可重试。"}</p>}
              {installerInfo.job.status === "success" && <p className="text-sm text-emerald-200">模型下载并通过本机模型清单核验。可前往本地创作工作台测试。</p>}
              <button type="button" disabled={installerInfo.engine_status !== "ready" || !installerInfo.enough_space || installerInfo.installed === true || ["downloading", "verifying"].includes(installerInfo.job.status) || installingBaseModel} onClick={() => void startBaseModelInstall()} className="rounded-xl bg-cyan-500 px-5 py-3 font-semibold text-slate-950 disabled:opacity-40">
                {installerInfo.installed ? "模型已安装" : installingBaseModel ? "正在准备…" : "确认并下载 Qwen3 4B"}
              </button>
              {installerInfo.engine_status !== "ready" && <p className="text-xs text-amber-100/80">尚未检测到 Ollama。当前步骤只负责安装模型，Ollama 运行引擎的自动安装将在后续安装向导阶段接入；请勿从不明网站下载运行环境。</p>}
              {!installerInfo.enough_space && <p className="text-xs text-amber-100/80">可用空间不足，暂不能下载。至少需要 6 GiB 可用空间。</p>}
            </div>
          ) : <p className="mt-3 text-sm text-white/50">正在检查本地引擎和磁盘空间…</p>}
          <p className="mt-3 text-xs leading-5 text-white/45">下载需联网并消耗网络流量；不收取模型 API 费用。下载操作仅在你点击确认后开始。不会登录云端、不会调用云端模型，也不会自动购买或充值。首次下载完成后，模型可在本机离线运行。</p>
        </section>

        <section className="rounded-2xl border border-fuchsia-300/25 bg-slate-950/70 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold">本地图片生成引擎检测（ComfyUI）</h2>
              <p className="mt-1 text-sm leading-6 text-white/55">检测本机 ComfyUI 是否运行、已安装哪些 checkpoint，并评估显存适配情况。</p>
            </div>
            <button type="button" disabled={checkingImageRuntime} onClick={() => void refreshImageRuntime()} className="rounded-lg border border-fuchsia-300/30 px-3 py-2 text-sm disabled:opacity-40">{checkingImageRuntime ? "检测中…" : "重新检测图片引擎"}</button>
          </div>
          {imageRuntime ? (
            <>
              <div className="mt-4 grid gap-2 sm:grid-cols-3">
                <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">引擎状态</div><div className="mt-1 font-semibold">{imageRuntime.status === "ready" ? "ComfyUI 已连接" : imageRuntime.status === "not_running" ? "尚未安装或启动" : "检测异常"}</div></div>
                <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">已安装图片模型</div><div className="mt-1 font-semibold">{imageRuntime.checkpoint_count} 个 checkpoint</div></div>
                <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">显存适配初筛</div><div className="mt-1 font-semibold">{({unknown:"未知",limited:"显存紧张",basic:"基础运行",capable:"具备一定余量",strong:"较充足"} as Record<string,string>)[imageRuntime.hardware?.gpu_level] || "待检测"}</div></div>
              </div>
              <div className="mt-3 rounded-xl bg-white/[0.04] p-3 text-sm">
                <p>{imageRuntime.message}</p>
                {imageRuntime.checkpoints.length > 0 && <div className="mt-2 space-y-1 text-white/65">{imageRuntime.checkpoints.slice(0,12).map((name) => <p key={name} className="break-all">• {name}</p>)}</div>}
                {imageRuntime.hardware?.reasons?.map((reason,index) => <p key={index} className="mt-2 text-white/65">• {reason}</p>)}
              </div>
              <p className="mt-2 text-xs leading-5 text-amber-100/65">{imageRuntime.hardware?.note} {imageRuntime.limitations || "本阶段仅做本地引擎和模型检测，尚未提交图片生成任务。"}</p>
            </>
          ) : <p className="mt-3 text-sm text-white/55">正在检测本机图片引擎…</p>}
          <p className="mt-3 text-xs leading-5 text-white/45">本检测只访问本机 127.0.0.1:8188，不会联网下载模型、启动程序、提交生成任务或调用云端 API。ComfyUI 和图片模型需要另行安装；后续会增加经过确认的安装向导。</p>
        </section>

        <section className="rounded-2xl border border-purple-300/25 bg-slate-950/70 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold">Qwen3 4B 硬件适配检测</h2>
              <p className="mt-1 text-sm text-white/55">只读取系统信息和磁盘空间，不进行压力测试。</p>
            </div>
            <button type="button" disabled={checkingHardware} onClick={() => void refreshHardwareReadiness()} className="rounded-lg border border-purple-300/30 px-3 py-2 text-sm disabled:opacity-40">{checkingHardware ? "正在检测…" : "重新检测电脑"}</button>
          </div>
          {hardwareReadiness && <>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">适配结论</div><div className="mt-1 font-semibold">{hardwareReadiness.label}</div></div>
              <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">系统内存</div><div className="mt-1 font-semibold">{size(hardwareReadiness.ram_bytes)}</div></div>
              <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">模型磁盘空间</div><div className="mt-1 font-semibold">{size(hardwareReadiness.free_disk_bytes)}</div></div>
            </div>
            <div className="mt-3 space-y-1 text-sm text-white/65">{hardwareReadiness.blockers.map((item,index)=><p key={index}>• {item}</p>)}{hardwareReadiness.reasons.map((item,index)=><p key={index}>• {item}</p>)}</div>
            <p className="mt-2 text-xs text-white/45">{hardwareReadiness.note}</p>
          </>}
        </section>

        <section className="rounded-2xl border border-fuchsia-300/25 bg-slate-950/70 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h2 className="text-xl font-semibold">ComfyUI 安装准备评估</h2><p className="mt-1 text-sm leading-6 text-white/55">先检查电脑和磁盘条件。目前只做评估，不会下载或安装任何文件。</p></div>
            <button type="button" disabled={checkingComfyReadiness} onClick={() => void refreshComfyReadiness()} className="rounded-lg border border-fuchsia-300/30 px-3 py-2 text-sm disabled:opacity-40">{checkingComfyReadiness ? "检测中…" : "重新评估"}</button>
          </div>
          {comfyReadiness ? <>
            <div className="mt-3 grid gap-2 sm:grid-cols-3">
              <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">引擎空间预留</div><div className="mt-1 font-semibold">{size(comfyReadiness.engine_disk_reserve_bytes)}</div></div>
              <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">可选模型额外预留</div><div className="mt-1 font-semibold">{size(comfyReadiness.optional_model_disk_reserve_bytes)}</div></div>
              <div className="rounded-xl bg-white/[0.04] p-3"><div className="text-xs text-white/45">目标磁盘可用空间</div><div className="mt-1 font-semibold">{size(comfyReadiness.free_disk_bytes)}</div></div>
            </div>
            <div className="mt-3 rounded-xl bg-white/[0.04] p-3 text-sm">
              <p>初步结论：{comfyReadiness.status === "blocked" ? "暂不建议安装" : comfyReadiness.status === "needs_manual_check" ? "需要人工确认" : "可以进入后续安装方案评估"}</p>
              <p className="mt-1 text-white/60">内存 {size(comfyReadiness.ram_bytes)} · 显存 {size(comfyReadiness.vram_bytes)} · 显卡档位 {comfyReadiness.hardware_profile}</p>
              {comfyReadiness.blockers.map((item,index) => <p key={index} className="mt-2 text-rose-200">• {item}</p>)}
              {comfyReadiness.warnings.map((item,index) => <p key={index} className="mt-2 text-amber-100/75">• {item}</p>)}
              <p className="mt-2 break-all text-xs text-white/45">规划安装位置：{comfyReadiness.install_target}</p>
            </div>
            <p className="mt-2 text-xs leading-5 text-white/45">{comfyReadiness.note}</p>
          </> : <p className="mt-3 text-sm text-white/55">正在评估 ComfyUI 安装条件…</p>}
          <div className="mt-4 border-t border-white/10 pt-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div><div className="font-semibold">官方安装包版本与下载量</div><p className="mt-1 text-xs text-white/45">需要你手动确认后才会查询官方版本信息；查询不会下载文件。</p></div>
              <button type="button" disabled={loadingComfyReleaseCatalog} onClick={() => void loadComfyReleaseCatalog()} className="rounded-lg border border-cyan-300/30 px-3 py-2 text-sm disabled:opacity-40">{loadingComfyReleaseCatalog ? "正在读取官方信息…" : "查询官方版本和大小"}</button>
            </div>
            {comfyReleaseCatalog && <>
              <p className="mt-3 text-sm">最新官方版本：<strong>{comfyReleaseCatalog.version}</strong></p>
              <div className="mt-2 space-y-2">
                {comfyReleaseCatalog.assets.map(asset => <div key={asset.name} className="rounded-lg bg-white/[0.04] p-3 text-sm">
                  <div className="break-all font-medium">{asset.name}</div>
                  <div className="mt-1 text-white/60">下载大小：{size(asset.size_bytes)} · SHA-256：{asset.digest_available ? "官方元数据已提供" : "官方未提供"}</div>
                  {asset.sha256 && <div className="mt-1 break-all font-mono text-[11px] text-white/40">{asset.sha256}</div>}
                </div>)}
                {comfyReleaseCatalog.assets.length === 0 && <p className="text-sm text-amber-100/75">官方最新版本暂未提供可识别的 Windows 安装包资源。</p>}
              </div>
              <p className="mt-2 text-xs leading-5 text-white/45">{comfyReleaseCatalog.note}</p>
            </>}
          </div>
          <p className="mt-3 text-xs text-white/45">当前阶段不会自动下载、安装或启动引擎。实际安装前还会展示确定版本、官方来源、下载量、校验信息和目标磁盘要求，并再次征求确认。</p>
        </section>

        {error && <div className="rounded-xl border border-rose-400/30 bg-rose-500/10 p-4 text-rose-200">{error}</div>}
        {message && <div className="rounded-xl border border-emerald-400/30 bg-emerald-500/10 p-4 text-emerald-100">{message}</div>}



        <section className="rounded-2xl border border-cyan-300/25 bg-slate-950/70 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold">本地 AI 专用模型工坊</h2>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-white/60">从已安装的 Ollama 基础模型创建漫剧编剧、分镜导演、人物设定或图片提示词专用模型。只在本机执行，不下载基础模型。</p>
            </div>
            <button type="button" onClick={() => void refreshRuntime()} className="rounded-lg border border-cyan-200/30 px-3 py-2 text-sm">检测本地引擎</button>
          </div>
          <div className="mt-3 rounded-xl bg-white/[0.04] p-3 text-sm text-white/65">
            {runtime?.status === "ready" ? `Ollama 已连接 · 已安装 ${runtime.models.length} 个模型` : runtime?.message || "正在检测本机 Ollama…"}
          </div>
          {runtime?.models?.length ? (
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <label className="space-y-2 text-sm"><span className="text-white/70">基础模型（必须已安装）</span>
                <select value={baseModel} onChange={e => setBaseModel(e.target.value)} className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white">
                  {runtime.models.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
                </select>
              </label>
              <label className="space-y-2 text-sm"><span className="text-white/70">新模型名称</span>
                <input value={newModelName} onChange={e => setNewModelName(e.target.value)} maxLength={63} placeholder="例如 comic-writer" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
              </label>
              <label className="space-y-2 text-sm"><span className="text-white/70">专用方向</span>
                <select value={modelRole} onChange={e => setModelRole(e.target.value)} className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white">
                  <option value="all_rounder">全能漫剧助手</option><option value="screenwriter">漫剧编剧</option><option value="storyboard_director">分镜导演</option><option value="character_designer">人物设定设计师</option><option value="prompt_artist">图片提示词设计师</option>
                </select>
              </label>
              <label className="space-y-2 text-sm"><span className="text-white/70">默认画面风格</span>
                <select value={modelStyle} onChange={e => setModelStyle(e.target.value)} className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white">
                  <option>国漫</option><option>仿真人</option><option>3D漫</option><option>2D漫</option>
                </select>
              </label>
            </div>
          ) : <p className="mt-3 text-sm text-amber-100/80">当前没有检测到已安装的 Ollama 模型。请先在本机准备好基础模型；此处不会自动下载。</p>}
          <label className="mt-3 block space-y-2 text-sm"><span className="text-white/70">额外创作规则（可选）</span>
            <textarea value={modelInstructions} onChange={e => setModelInstructions(e.target.value)} maxLength={4000} rows={3} placeholder="例如：尊重原著，不擅自改变人物关系；每个镜头必须保持角色服装、发型和道具一致。" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
          </label>
          <button type="button" disabled={!runtime?.models?.length || !baseModel || !newModelName.trim() || creatingModel} onClick={() => void createSpecializedModel()} className="mt-4 rounded-xl bg-cyan-500 px-5 py-3 font-semibold text-slate-950 disabled:opacity-40">
            {creatingModel ? "正在本机创建专用模型…" : "创建本地专用模型"}
          </button>
          <p className="mt-2 text-xs leading-5 text-white/45">创建过程可能占用 CPU、内存和磁盘空间；复用基础模型但会生成额外模型配置/数据。不会覆盖同名模型，创建前会再次确认。模型能力取决于基础模型本身，不等同于从零训练。</p>
        </section>

        {catalog && (
          <>
            <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <h2 className="font-semibold">模型文件概览</h2>
                  <p className="mt-1 break-all text-xs text-white/45">{catalog.directory}</p>
                </div>
                <div className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/70">{catalog.total} 个文件</div>
              </div>
              <p className="mt-3 text-xs text-white/50">{catalog.note}</p>
            </section>

            <div className="grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
              <section className="space-y-3">
                <h2 className="font-semibold">已发现的模型文件</h2>
                {catalog.items.length === 0 ? (
                  <div className="rounded-2xl border border-white/10 bg-slate-950/60 p-6 text-sm leading-6 text-white/60">
                    暂时没有发现模型文件。将模型文件放入上方模型目录后，点击“刷新模型库”。请先确认模型来源、许可证、文件大小和电脑配置。
                  </div>
                ) : catalog.items.map((item) => (
                  <button
                    type="button"
                    key={item.name}
                    onClick={() => chooseModel(item)}
                    className={`w-full rounded-2xl border p-4 text-left transition ${selected === item.name ? "border-cyan-300/60 bg-cyan-400/10" : "border-white/10 bg-slate-950/60 hover:border-white/25"}`}
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="break-all font-semibold">{item.name}</div>
                        <div className="mt-1 text-xs text-white/45">{item.format.toUpperCase()} · {size(item.size_bytes)}</div>
                      </div>
                      <span className="rounded-full bg-white/10 px-3 py-1 text-xs text-white/70">{licenseLabels[item.license_review]}</span>
                    </div>
                    <div className="mt-3 text-sm text-cyan-100">{item.compatibility.label}</div>
                    <div className="mt-1 text-xs leading-5 text-white/45">{item.compatibility.note}</div>
                  </button>
                ))}
              </section>

              <section className="rounded-2xl border border-purple-300/20 bg-slate-950/65 p-5">
                <h2 className="font-semibold">许可证与使用记录</h2>
                {!selectedItem ? (
                  <p className="mt-4 text-sm text-white/50">请先从左侧选择一个模型文件。</p>
                ) : (
                  <form onSubmit={saveReview} className="mt-4 space-y-4">
                    <div className="rounded-xl bg-white/[0.04] p-3">
                      <div className="text-xs text-white/45">当前模型</div>
                      <div className="mt-1 break-all text-sm font-semibold">{selectedItem.name}</div>
                      <div className="mt-2 text-xs text-cyan-100">{selectedItem.compatibility.label}</div>
                      <div className="mt-1 text-xs text-white/45">内存：{size(selectedItem.compatibility.ram_bytes)} · 显存：{size(selectedItem.compatibility.vram_bytes)}</div>
                    </div>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">许可证核对状态</span>
                      <select value={licenseReview} onChange={(event) => setLicenseReview(event.target.value as LicenseReview)} className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white">
                        <option value="unverified">未核验</option>
                        <option value="checked_by_user">我已人工查看许可证</option>
                        <option value="commercial_permission_confirmed">我已确认允许商用</option>
                        <option value="noncommercial_only">仅限非商业用途</option>
                        <option value="restricted">限制使用 / 暂不可用</option>
                      </select>
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">许可证名称</span>
                      <input value={licenseName} onChange={(event) => setLicenseName(event.target.value)} maxLength={160} placeholder="例如：模型页面列出的许可证名称" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">官方许可证页面（可选）</span>
                      <input type="url" value={licenseUrl} onChange={(event) => setLicenseUrl(event.target.value)} placeholder="https://…" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
                    </label>

                    <label className="block space-y-2 text-sm">
                      <span className="text-white/70">个人核对备注</span>
                      <textarea value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={2000} rows={4} placeholder="记录商用限制、署名要求、再分发限制等…" className="w-full rounded-lg border border-white/15 bg-slate-900 px-3 py-3 text-white placeholder:text-white/30" />
                    </label>

                    <div className="rounded-xl border border-amber-300/20 bg-amber-400/5 p-3 text-xs leading-5 text-amber-100/80">
                      重要：这里的状态是你自己的记录，不代表软件已经验证许可证，也不构成法律意见。请以模型作者和官方发布页面的完整条款为准。
                    </div>
                    <button disabled={busy} type="submit" className="w-full rounded-xl bg-purple-600 px-4 py-3 text-sm font-semibold disabled:opacity-40">
                      {busy ? "正在保存…" : "保存许可证核对记录"}
                    </button>
                  </form>
                )}
              </section>
            </div>
          </>
        )}
      </div>
    </main>
  );
}
