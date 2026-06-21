# B站视频下载

一个面向 macOS 桌面的 B站视频下载工具。项目目标是把已经验证可用的 `yt-dlp` 下载能力，包装成更易用、更好看的桌面应用。

## 当前状态

项目刚初始化，当前只完成了下载依赖迁移：

- `yt-dlp==2025.10.14`
- `imageio-ffmpeg==0.6.0`

后续计划使用轻量桌面方案构建前端界面，优先考虑 Tauri。

## 本地开发

创建虚拟环境：

```bash
python3 -m venv .venv
```

安装依赖：

```bash
.venv/bin/python -m pip install -r requirements.txt
```

验证 `yt-dlp`：

```bash
.venv/bin/yt-dlp --version
```

## 初步路线

- URL 输入与解析
- 视频/番剧/合集信息展示
- 分集选择
- 清晰度选择
- 下载队列与进度显示
- 下载目录、cookies、`yt-dlp`、ffmpeg 路径设置

## 安全与隐私

不要提交以下内容：

- B站 cookies
- 登录态导出的浏览器数据
- 私有下载链接
- 本机真实下载目录中的视频文件
- 含个人信息的日志

建议把下载文件放在本地 `downloads/` 或用户自定义目录中，仓库已默认忽略 `downloads/`。

## 许可证

许可证暂未选择。准备正式开源前，需要确认使用 MIT、Apache-2.0、GPL-3.0 或其他许可证。
