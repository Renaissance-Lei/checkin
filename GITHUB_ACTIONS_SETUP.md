# GitHub Actions 部署说明

## 需要准备的 GitHub Secrets

在仓库 `Settings -> Secrets and variables -> Actions -> New repository secret` 中添加：

- `TG_API_ID`
- `TG_API_HASH`
- `TG_PHONE_NUMBER`
- `TG_SESSION_STRING`

## 本地生成 `TG_SESSION_STRING`

先在本地终端设置环境变量，再运行：

```powershell
$env:TG_API_ID="你的 API ID"
$env:TG_API_HASH="你的 API HASH"
$env:TG_PHONE_NUMBER="你的 Telegram 手机号"
python generate_session_string.py
```

脚本会打印出一长串字符串，把它完整复制到 GitHub Secret `TG_SESSION_STRING` 中。

## 工作流文件

工作流已经写在：

- `.github/workflows/checkin.yml`

默认配置是：

- 每天北京时间 00:05 自动执行一次
- 同时支持在 GitHub Actions 页面里手动点击运行

如果你想改执行时间，修改 `cron` 即可。

注意：

- GitHub Actions 的 `cron` 使用 UTC，不是北京时间
- 当前 `5 16 * * *` 对应北京时间次日 `00:05`

## 推送前建议

当前仓库已经加入 `.gitignore`，会忽略：

- `telegram_checkin.session`
- `checkin_state.json`
- `__pycache__`

这样可以避免把本地登录状态和缓存文件提交到 GitHub。
