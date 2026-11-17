"use client";

import { useRef, useState } from "react";

type Props = {
  onVideoSelected: (path: string) => void;
  onCoverSelected: (path: string) => void;
};

declare global {
  interface Window {
    desktop?: {
      selectFile?: (options?: {
        filters?: { name: string; extensions: string[] }[];
      }) => Promise<string | null>;
    };
  }
}

export default function PublishingFilePicker({
  onVideoSelected,
  onCoverSelected,
}: Props) {
  const videoInput = useRef<HTMLInputElement>(null);
  const coverInput = useRef<HTMLInputElement>(null);
  const [videoName, setVideoName] = useState("");
  const [coverName, setCoverName] = useState("");

  async function chooseDesktopFile(
    type: "video" | "cover",
  ) {
    try {
      const filters =
        type === "video"
          ? [{ name: "视频文件", extensions: ["mp4", "mov", "mkv", "webm", "avi"] }]
          : [{ name: "图片文件", extensions: ["png", "jpg", "jpeg", "webp"] }];

      const result = await window.desktop?.selectFile?.({ filters });

      if (!result) return;

      if (type === "video") {
        setVideoName(result.split(/[\\/]/).pop() || result);
        onVideoSelected(result);
      } else {
        setCoverName(result.split(/[\\/]/).pop() || result);
        onCoverSelected(result);
      }
    } catch {
      alert("桌面文件选择器暂不可用，请使用下方网页文件选择方式。");
    }
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
        <div className="font-medium">选择视频</div>
        <button
          type="button"
          onClick={() => void chooseDesktopFile("video")}
          className="rounded-lg bg-purple-600 px-5 py-3 hover:bg-purple-500"
        >
          从电脑选择视频
        </button>
        <input
          ref={videoInput}
          type="file"
          accept="video/*"
          className="hidden"
          onChange={event => {
            const file = event.target.files?.[0];
            if (!file) return;
            setVideoName(file.name);
            alert("浏览器选择的文件需要先上传到服务器，不能直接当作本地文件路径发布。");
          }}
        />
        {videoName && (
          <div className="text-sm text-purple-200 break-all">{videoName}</div>
        )}
      </div>

      <div className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-3">
        <div className="font-medium">选择封面</div>
        <button
          type="button"
          onClick={() => void chooseDesktopFile("cover")}
          className="rounded-lg border border-purple-400/40 px-5 py-3 hover:bg-purple-500/10"
        >
          从电脑选择封面
        </button>
        <input
          ref={coverInput}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={event => {
            const file = event.target.files?.[0];
            if (!file) return;
            setCoverName(file.name);
            alert("浏览器选择的封面需要先上传，不能直接当作服务器文件路径。");
          }}
        />
        {coverName && (
          <div className="text-sm text-purple-200 break-all">{coverName}</div>
        )}
      </div>
    </div>
  );
}
