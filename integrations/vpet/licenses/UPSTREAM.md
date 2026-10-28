# Transparent-video implementation references

`PetVideoPlayer.cs` and `VideoDecoderJob.cs` adapt the streaming-decoder/job-object design from [Bai-Cute/big-blue-fish-desktop-pet](https://github.com/Bai-Cute/big-blue-fish-desktop-pet), specifically `CompanionVideoPlayer.cs` and `CompanionJob.cs`. Those upstream files are under Apache-2.0; the full license is retained in `Apache-2.0.txt`.

QQCompanion changes include per-playback process ownership, idempotent concurrent job disposal, a bounded frame pipeline, a shared cropped viewport, looping, and same-character still-frame fallback. The application-specific selection and integration code remains part of QQCompanion.

Optional animation resources are downloaded separately from [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet), commit `48642698ee73ecf2029ea85a2e8f76f562b710e5`, under its MIT license. Copyright (c) 2026 PC2005-cloud. The preparation tool retains the full license and source record beside the downloaded resources. The held/dragged clip is trimmed and losslessly re-encoded to loop its suspended section.

FFmpeg is an external executable, not copied into this source distribution. The preparation tool can reuse an existing installation or accept an explicit path. Its own license/build terms continue to apply.

`tools/prepare_pet_smooth.py` optionally processes those licensed animations with [nihui/rife-ncnn-vulkan](https://github.com/nihui/rife-ncnn-vulkan), release `20221029`, model `rife-v4.6`, through dual-matte interpolation and alpha reconstruction. The tool/model are not included or run during normal pet use. This is derivative frame interpolation, not original character animation. The external tool's MIT license is retained in `RIFE-MIT.txt` and copied into prepared packs.
