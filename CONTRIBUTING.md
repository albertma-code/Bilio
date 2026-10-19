# 贡献指南

当前为 0.4.0 本机候选版，优先修复影响实际下载、账号隔离和文件安全的问题。支持范围与未验收项见 [README](README.md)。

## 开发约定

- 保持 Vue 界面、Rust 本机权限与 Python 下载引擎的边界，优先使用已有模式。
- 不提交真实 Cookie、令牌、账号、私有链接、任务数据库或下载媒体；测试使用合成内容和临时目录。
- 不把凭据传给前端或写入普通配置。钥匙串写入须有用户明确选择，失败不能回退明文。
- 不静默降低所选画质，不因同名文件存在就判定成功，不附带删除用户视频。
- UI 文案使用简洁中文，图标操作有名称，最小窗口下主要操作保持可达。
- 分开记录模拟、单元、本地媒体集成和真实站点验收，不用测试数量替代实际结果。

## 短回归

先按 README 安装依赖并构建 sidecar，再运行：

```bash
npm run test:sidecar
npm run build
cargo test --manifest-path src-tauri/Cargo.toml
.venv-stable/bin/python sidecar/tests/smoke_binary.py src-tauri/binaries/bilio-sidecar-aarch64-apple-darwin
```

最后一条适用于 Apple Silicon 已构建的 sidecar；其他架构不得据此宣称已支持。

`tests/ui/verify.js` 是接收 Playwright `page` 的异步回归函数，测试工具需调用该函数，且本地 Vite 运行在 `127.0.0.1:1421`。它使用模拟 IPC，不会真实登录或下载，不能当作会员权限验收。`tests/ui/preview.html` 为模拟任务设计预览。

真实站点脚本 `sidecar/acceptance.py`、`sidecar/acceptance_binary.py` 须显式指定经授权来源和输出目录，不加入默认 CI。运行前确认账号、内容、空间和流量；脚本可能下载完整视频，不默认读取真实历史库。打包验收脚本还假定测试来源具备对应会员画质，应先检查其格式要求。

长稳脚本为手动工具，不属于日常短回归；未明确要求时不要启动长时间测试。CI 结果以实际运行记录为准。

## 提交与反馈

提交前检查实际暂存文件和差异，避免加入凭据、构建产物或本机记录。截图使用合成任务，录制和日志检查账号、路径、标题及签名媒体 URL。

问题反馈请提供版本、系统/架构、复现步骤、期望与实际结果，以及可选的脱敏诊断。不需要 Cookie、密码、钥匙串导出或整份任务数据库。安全问题不要在公开 Issue 中附带敏感证据，见 [安全说明](SECURITY.md)。
