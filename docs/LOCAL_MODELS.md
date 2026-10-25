# 可选本地语音与唱歌

基础聊天使用配置的 API，本地模型按需准备，不会自动改变用户的模型路由或预算。

预期相邻目录：

```text
AI/
  QQCompanion/
  QQCompanionFrontendTools/python-env/
  GPTSoVITS/app/...
  GPTSoVITS/reference/yui/presets.json
  SeedVC/.venv/
```

GPT-SoVITS 的官方软件与模型按上游说明准备。本项目的 `integrations/gptsovits/launch.py` 是本地启动整合，当前适配 v5turbo 配置；模型路径与设备由其 `tts_local.yaml` 指定。
`presets.json` 的 `profiles` 需要 `id=everyday`、`ref_audio_path`、`prompt_text`、`prompt_lang`。参考音频由用户自己提供，公开仓库不分发角色音频。

Seed-VC 按上游准备 F0-conditioned 歌声转换与 Demucs 分离依赖，使用项目的 `integrations/seedvc/companion_svc.py` 整合入口。参考路径使用相邻目录；可显式传入 `--target`。
它转换已有歌声的音色，不能从文字凭空生成歌曲。需要 FFmpeg、可用音源与兼容 CUDA 的环境。

私人离线资源包会保存已经验证过的实际模型与解释器，并包含唱歌 venv 使用的基础环境。
资源恢复后仍应检查新显卡和驱动。新用户自行下载模型时，应保留上游版本和各自许可信息。
