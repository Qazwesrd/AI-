const { app, BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");

const outputDir = path.resolve(process.argv[2] || path.join(__dirname, "../../docs/manual"));
const imageDir = path.join(outputDir, "images");
const manualHtml = path.join(outputDir, "manual.html");
const pdfPath = path.join(outputDir, "AI漫剧制作大师-使用说明书.pdf");
const pages = [
  ["/", "01-home.png"],
  ["/novels", "02-novels.png"],
  ["/ai-analysis", "03-analysis.png"],
  ["/visual-studio", "04-visual-studio.png"],
  ["/assets", "05-assets.png"],
  ["/asset-timeline", "06-asset-timeline.png"],
  ["/storyboard", "07-storyboard.png"],
  ["/ai-tools", "08-ai-tools.png"],
  ["/ai-collaboration", "09-ai-collaboration.png"],
  ["/video-editor", "10-video-editor.png"],
  ["/media-studio", "11-media-studio.png"],
  ["/tasks", "12-tasks.png"],
  ["/publishing", "13-publishing.png"],
  ["/ai-center", "14-ai-center.png"],
  ["/backup-center", "15-backup-center.png"],
  ["/system-diagnostics", "16-system-diagnostics.png"],
  ["/novel-publishing", "18-novel-publishing.png"],
  ["/revenue", "19-revenue.png"],
  ["/prompts/research", "20-prompt-research.png"],
  ["/help", "17-help.png"]
];

app.whenReady().then(async () => {
  fs.mkdirSync(imageDir, { recursive: true });
  const window = new BrowserWindow({
    width: 1440,
    height: 1000,
    show: true,
    backgroundColor: "#09091b",
    webPreferences: { contextIsolation: true, nodeIntegration: false }
  });
  try {
    for (const [route, filename] of pages) {
      const response = await window.loadURL("http://127.0.0.1:3000" + route);
      if (response && response.statusCode >= 500) {
        throw new Error("页面加载失败：" + route + " HTTP " + response.statusCode);
      }
      await new Promise(resolve => setTimeout(resolve, 900));
      await window.webContents.executeJavaScript("window.scrollTo(0, 0)");
      await new Promise(resolve => setTimeout(resolve, 200));
      const image = await window.webContents.capturePage();
      fs.writeFileSync(path.join(imageDir, filename), image.toPNG());
      process.stdout.write("Captured " + route + " -> " + filename + "\n");
    }
    if (!fs.existsSync(manualHtml)) throw new Error("说明书 HTML 文件不存在：" + manualHtml);
    await window.loadFile(manualHtml);
    await new Promise(resolve => setTimeout(resolve, 500));
    const pdf = await window.webContents.printToPDF({
      pageSize: "A4",
      printBackground: true,
      margins: { top: 0.35, bottom: 0.35, left: 0.35, right: 0.35 }
    });
    fs.writeFileSync(pdfPath, pdf);
    process.stdout.write("PDF manual created: " + pdfPath + "\n");
  } catch (error) {
    console.error(error);
    app.exit(1);
    return;
  }
  window.close();
  app.quit();
}).catch(error => {
  console.error(error);
  app.exit(1);
});
