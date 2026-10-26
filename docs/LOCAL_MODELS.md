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

V5.6 默认50步转换，常规成品为320 kbps MP3，同时保留44.1 kHz、24位立体声FLAC母带。曲库显示的成品空间包含母带，试听版本仍单独保留。模型质量与原始音源仍限制最终听感，不会因为导出无损格式就恢复已经缺失的信息。

`preserve` 保留原曲音高；`reference_octave` 只根据整曲音域统一选择是否升一个八度，不逐句降八度。需要调整调性时使用显式的半音移调。整曲升八度可能让原本的高音更高，想保留原唱唱法时选择保留原曲。转换入口直接使用本仓库的整合代码，模型仍从相邻 SeedVC 目录加载。

私人离线资源包会保存已经验证过的实际模型与解释器，并包含唱歌 venv 使用的基础环境。
资源恢复后仍应检查新显卡和驱动。新用户自行下载模型时，应保留上游版本和各自许可信息。
