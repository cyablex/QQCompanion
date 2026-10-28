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
    private PetVideoPlayer? video;
    private string videoKey = "", videoPose = "", videoFolder = "";
    private string headVideo = "head";
    private bool videoMirror;
    public bool VideoReady => video is not null;
    public long VideoFrames => video?.Frames ?? 0;
    public int? VideoDecoderPid => video?.DecoderPid;
    public string VideoPose => videoPose;
    public bool AtVideoBoundary => !VideoReady || ReducedMotion || video!.Position < .13 || video.Duration - video.Position < .13;
    public double ReactionDuration(string kind) => VideoReady ? 241d / 24 : 2;
    public event Action<string>? VideoFailed;

    private bool PrepareVideo(string folder)
    {
        videoFolder = Path.Combine(folder, "video");
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
            video = new PetVideoPlayer(executable, View.Dispatcher);
            video.Frame += (bitmap, first) => { if (first) PresentVideo(bitmap, videoMirror, true); };
            video.Failed += reason => {
                failedVideos.Add(videoPose); videoKey = "";
                if (images.TryGetValue(videoPose, out var poster)) PresentVideo(poster, videoMirror, false);
                VideoFailed?.Invoke(reason);
            };
            return true;
        }
        catch (Exception error) when (error is IOException or JsonException or KeyNotFoundException or InvalidOperationException)
        {
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
        string name = pose.StartsWith("walk_") ? "walk" : pose switch {
            "read" or "draw" => "write", "excited" => "body", "head" => headVideo, _ => pose
        };
        if (!videos.ContainsKey(name)) name = "idle";
        bool mirror = pose.StartsWith("walk_right");
        string key = name + (mirror ? ":right" : ":left");
        translate.Y = 0; rotate.Angle = 0;
        accents.Update(ReducedMotion ? "" : reaction, seconds - reactionAt);
        if (ReducedMotion || failedVideos.Contains(name))
        {
            video!.Stop(); videoKey = "";
            if (!ReferenceEquals(front.Source, images[name])) PresentVideo(images[name], mirror, false);
            return true;
        }
        if (videoKey != key)
        {
            videoKey = key; videoPose = name; videoMirror = mirror;
            video!.Play(videos[name], videoFrames[name]);
        }
        return true;
    }
    public void Suspend() { video?.Stop(); videoKey = ""; }
    public void Dispose() { video?.Dispose(); video = null; }
}
