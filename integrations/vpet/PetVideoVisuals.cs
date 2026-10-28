using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using Point = System.Windows.Point;

namespace DafeiyuPet;

public sealed partial class PetVisuals
{
    private readonly Dictionary<string, string> videos = new();
    private readonly Dictionary<string, int> videoFrames = new();
    private readonly HashSet<string> failedVideos = new();
    private PetVideoPlayer? video, standby;
    private BitmapSource? standbyFrame;
    private string standbyName = "", decoderPath = "";
    public int VideoFps { get; private set; } = 24;
    public double AnimationSpeed { get; private set; } = 1;
    public double RemainingVideo => video is null ? 0 : video.Duration - video.Position;
    private string videoKey = "", videoPose = "", videoFolder = "";
    private string headVideo = "head";
    private bool videoMirror;
    public bool VideoReady => video is not null;
    public long VideoFrames => video?.Frames ?? 0;
    public int? VideoDecoderPid => video?.DecoderPid;
    public string VideoPose => videoPose;
    public bool AtVideoBoundary => !VideoReady || ReducedMotion || video!.Position < .13 || video.Duration - video.Position < .13;
    public double PoseDuration(string pose) { string name = VideoName(pose); return VideoReady ? videoFrames[name] / (double)VideoFps : 2; }
    public double ReactionDuration(string kind) => PoseDuration(kind);
    public event Action<string>? VideoFailed;

    private bool PrepareVideo(string folder)
    {
        videoFolder = Path.Combine(folder, File.Exists(Path.Combine(folder, "video-smooth", "pack.json")) ? "video-smooth" : "video");
        string manifest = Path.Combine(videoFolder, "pack.json"), decoder = Path.Combine(videoFolder, "decoder.json");
        if (!File.Exists(manifest) || !File.Exists(decoder)) return false;
        try
        {
            using var info = JsonDocument.Parse(File.ReadAllText(manifest));
            using var settings = JsonDocument.Parse(File.ReadAllText(decoder));
            string executable = settings.RootElement.GetProperty("ffmpeg").GetString() ?? "";
            executable = Path.GetFullPath(Path.Combine(folder, "..", "..", "..", executable));
            if (!File.Exists(executable)) throw new IOException("动画解码器不存在");
            foreach (string key in new[] { "idle", "hum", "look", "walk", "lift", "head", "body", "wave", "stretch", "write", "eat", "rest", "yawn", "greeting" })
            {
                var entry = info.RootElement.GetProperty("clips").GetProperty(key);
                string movie = entry.GetProperty("file").GetString() ?? "", poster = entry.GetProperty("poster").GetString() ?? "";
                if (Path.GetFileName(movie) != movie || Path.GetFileName(poster) != poster
                    || !File.Exists(Path.Combine(videoFolder, movie)) || !File.Exists(Path.Combine(videoFolder, poster)))
                    throw new IOException("动画文件不完整");
                videos[key] = Path.Combine(videoFolder, movie);
                videoFrames[key] = entry.GetProperty("frames").GetInt32();
                if (videoFrames[key] <= 0) throw new IOException("动画帧数无效");
                images[key] = Load(Path.Combine(videoFolder, poster));
            }
            VideoFps = info.RootElement.GetProperty("fps").GetInt32();
            if (VideoFps is not (24 or 48 or 60)) throw new IOException("不支持的动画帧率");
            AnimationSpeed = info.RootElement.TryGetProperty("speed", out var speed) ? speed.GetDouble() : 1;
            if (!double.IsFinite(AnimationSpeed) || AnimationSpeed < .8 || AnimationSpeed > 2) throw new IOException("无效的动画速度");
            decoderPath = executable;
            video = NewPlayer();
            return true;
        }
        catch (Exception error) when (error is IOException or JsonException or KeyNotFoundException or InvalidOperationException)
        {
            VideoFps = 24; AnimationSpeed = 1;
            images.Clear(); videos.Clear();
            string logs = Path.Combine(folder, "..", "..", "..", "logs");
            Directory.CreateDirectory(logs);
            File.AppendAllText(Path.Combine(logs, "vpet-video.log"), DateTime.Now + " " + error.Message + Environment.NewLine);
            return false;
        }
    }
    private void PresentVideo(BitmapSource bitmap, bool mirror, bool blend)
    {
        back.Source = front.Source; back.RenderTransform = front.RenderTransform;
        front.RenderTransformOrigin = new Point(.5, .5);
        back.RenderTransformOrigin = new Point(.5, .5);
        front.RenderTransform = mirror ? new ScaleTransform(-1, 1) : Transform.Identity;
        front.Source = bitmap;
        double duration = blend && !ReducedMotion ? .14 : 0;
        back.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(duration > 0 && back.Source is not null ? 1 : 0, 0, TimeSpan.FromSeconds(duration)));
        front.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(duration > 0 ? 0 : 1, 1, TimeSpan.FromSeconds(duration)));
    }
    private bool ShowVideo(string pose, double seconds)
    {
        if (!VideoReady) return false;
        current = pose;
        string name = VideoName(pose);
        bool mirror = pose.StartsWith("walk_right");
        string key = name + (mirror ? ":right" : ":left");
        translate.Y = 0; rotate.Angle = 0;
        accents.Update(ReducedMotion ? "" : reaction, seconds - reactionAt);
        if (ReducedMotion || failedVideos.Contains(name))
        {
            video!.Stop(); CancelPreload(); videoKey = "";
            if (!ReferenceEquals(front.Source, images[name])) PresentVideo(images[name], mirror, false);
            return true;
        }
        if (videoKey != key)
        {
            videoKey = key; videoPose = name; videoMirror = mirror;
            if (standbyName == name && standbyFrame is not null && standby is not null)
            {
                var old = video; video = standby; standby = null;
                var frame = standbyFrame; standbyFrame = null; standbyName = "";
                old?.Dispose(); PresentVideo(frame, mirror, true); video.Activate();
            }
            else
            {
                CancelPreload();
                PresentVideo(images[name], mirror, true);
                video!.Play(videos[name], videoFrames[name]);
            }
        }
        return true;
    }
    private string VideoName(string pose)
    {
        string name = pose.StartsWith("walk_") ? "walk" : pose switch {
            "read" or "draw" => "write", "excited" => "body", "head" => headVideo, _ => pose
        };
        return videos.ContainsKey(name) ? name : "idle";
    }
    private PetVideoPlayer NewPlayer()
    {
        var player = new PetVideoPlayer(decoderPath, View.Dispatcher, VideoFps);
        player.Frame += (bitmap, first) => {
            if (!first) return;
            if (ReferenceEquals(player, video)) PresentVideo(bitmap, videoMirror, false);
            else if (ReferenceEquals(player, standby)) standbyFrame = bitmap;
        };
        player.Failed += reason => {
            if (ReferenceEquals(player, standby)) { CancelPreload(); return; }
            if (!ReferenceEquals(player, video)) return;
            failedVideos.Add(videoPose); videoKey = ""; CancelPreload();
            if (images.TryGetValue(videoPose, out var poster)) PresentVideo(poster, videoMirror, false);
            VideoFailed?.Invoke(reason);
        };
        return player;
    }
    public void Preload(string pose)
    {
        if (!VideoReady || ReducedMotion) return;
        string name = VideoName(pose);
        if (name == videoPose || name == standbyName || failedVideos.Contains(name)) return;
        CancelPreload(); standbyName = name; standby = NewPlayer();
        standby.Prepare(videos[name], videoFrames[name]);
    }
    public void CancelPreload() { var old = standby; standby = null; standbyFrame = null; standbyName = ""; old?.Dispose(); }
    public void Suspend() { video?.Stop(); CancelPreload(); videoKey = ""; }
    public void Dispose() { video?.Dispose(); video = null; CancelPreload(); }
}
