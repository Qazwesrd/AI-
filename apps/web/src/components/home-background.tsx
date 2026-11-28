"use client";

import { useEffect, useRef, useState } from "react";

const STORAGE_KEY = "ai-comic-home-background-v1";
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const MAX_IMAGE_EDGE = 1920;

async function compressBackgroundImage(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  try {
    const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法处理图片，请更换图片后重试。");
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    // JPEG keeps the persisted data URL small enough for typical browser storage quotas.
    return canvas.toDataURL("image/jpeg", 0.78);
  } finally {
    bitmap.close();
  }
}

export default function HomeBackground() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [hasCustomBackground, setHasCustomBackground] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(STORAGE_KEY);
      if (saved?.startsWith("data:image/")) {
        document.documentElement.style.setProperty("--home-bg-image", `url("${saved}")`);
        setHasCustomBackground(true);
      }
    } catch {
      // Private browsing or storage restrictions: keep the default background.
    }
  }, []);

  async function chooseImage(file?: File) {
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      window.alert("请选择图片文件。");
      return;
    }
    if (file.size > MAX_UPLOAD_BYTES) {
      window.alert("原始背景图片不能超过 20MB。");
      return;
    }

    setIsProcessing(true);
    try {
      const dataUrl = await compressBackgroundImage(file);
      document.documentElement.style.setProperty("--home-bg-image", `url("${dataUrl}")`);
      setHasCustomBackground(true);
      try {
        window.localStorage.setItem(STORAGE_KEY, dataUrl);
      } catch {
        window.alert("背景图片已应用，但浏览器存储空间不足，刷新后可能需要重新选择。");
      }
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "图片处理失败，请尝试更换图片。");
    } finally {
      setIsProcessing(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function clearImage() {
    document.documentElement.style.removeProperty("--home-bg-image");
    try { window.localStorage.removeItem(STORAGE_KEY); } catch {}
    setHasCustomBackground(false);
  }

  return (
    <div className="home-background-controls">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="sr-only"
        aria-label="选择首页背景图片"
        disabled={isProcessing}
        onChange={(event) => void chooseImage(event.target.files?.[0])}
      />
      <button type="button" disabled={isProcessing} onClick={() => inputRef.current?.click()}>
        {isProcessing ? "正在处理图片…" : hasCustomBackground ? "更换背景图片" : "设置背景图片"}
      </button>
      {hasCustomBackground && (
        <button type="button" disabled={isProcessing} onClick={clearImage} aria-label="恢复默认背景">恢复默认</button>
      )}
    </div>
  );
}
