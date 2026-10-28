using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using Image = System.Windows.Controls.Image;
using Point = System.Windows.Point;

namespace DafeiyuPet;

public sealed partial class PetVisuals : IDisposable
{
    public Grid View { get; } = new() { IsHitTestVisible = false };
    private readonly Image front = new() { Stretch = Stretch.Uniform };
    private readonly Image back = new() { Stretch = Stretch.Uniform, Opacity = 0 };
    private readonly PetAccents accents = new();
    private readonly Dictionary<string, BitmapSource> images = new();
    private readonly Dictionary<string, PoseClip> clips = new();
    private readonly TranslateTransform translate = new();
    private readonly RotateTransform rotate = new();
    private string current = "";
    private double started, reactionAt = -10;
    private PoseClip? transition;
    private double transitionAt, transitionEnd;
    private string reaction = "";
    public bool ReducedMotion { get; set; }
    public bool Ready => images.ContainsKey("idle");
    public int AnimatedPoses => clips.Count;
    public ImageSource? CurrentFrame => front.Source;
    public string CurrentPose => current;
    public BitmapSource? Portrait => images.TryGetValue("idle", out var value) ? new CroppedBitmap(value,
        new Int32Rect(value.PixelWidth * 14 / 100, value.PixelHeight * 7 / 100, value.PixelWidth * 72 / 100, value.PixelHeight * 49 / 100)) : null;
    public PetVisuals(string root)
    {
        var folder = Path.Combine(root, "vpet", "characters", "dafeiyu-v1");
        if (!PrepareVideo(folder))
        {
        foreach (string pose in new[] { "idle", "read", "draw", "stretch", "excited", "eat", "rest", "walk_left0", "walk_left1", "walk_right0", "walk_right1" })
        {
            string path = Path.Combine(folder, "poses", pose + ".png");
            if (File.Exists(path)) images[pose] = Load(path);
        }
        foreach (string pose in new[] { "read", "write", "eat", "reaction", "settle" })
        {
            string directory = Path.Combine(folder, "animations", pose), manifest = Path.Combine(directory, "clip.json");
            if (!File.Exists(manifest)) continue;
            using var document = JsonDocument.Parse(File.ReadAllText(manifest));
            var durations = document.RootElement.GetProperty("durations").EnumerateArray().Select(x => x.GetDouble() / 1000).ToArray();
            var frames = durations.Select((_, i) => Load(Path.Combine(directory, i.ToString("000") + ".png"))).ToArray();
            if (pose == "reaction" && frames.Length == 6)
            {
                clips["head"] = new(frames.Take(2).ToArray(), new[] { .55, .7 });
                clips["body"] = new(frames.Skip(2).Take(2).ToArray(), new[] { .28, .55 });
                clips["lift"] = new(frames.Skip(4).Take(2).ToArray(), new[] { .4, .4 });
                if (images.TryGetValue("idle", out var standing)) clips["idle"] = new(new[] { standing, frames[0], standing }, new[] { 3.2, .14, 1.7 });
            }
            else clips[pose] = new(frames, durations);
        }
        }
        View.Children.Add(back); View.Children.Add(front); View.Children.Add(accents);
        RenderOptions.SetBitmapScalingMode(front, BitmapScalingMode.HighQuality);
        RenderOptions.SetBitmapScalingMode(back, BitmapScalingMode.HighQuality);
        var transforms = new TransformGroup(); transforms.Children.Add(rotate); transforms.Children.Add(translate);
        View.RenderTransform = transforms; View.RenderTransformOrigin = new Point(.5, .85);
    }
    private static BitmapSource Load(string path)
    {
        var bitmap = new BitmapImage(); bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad;
        bitmap.CreateOptions = BitmapCreateOptions.IgnoreImageCache; bitmap.UriSource = new Uri(Path.GetFullPath(path)); bitmap.EndInit(); bitmap.Freeze();
        return bitmap;
    }
    public void React(string kind, double seconds) { reaction = kind; reactionAt = seconds; if (kind == "head" && VideoReady) { var choices = new[] { "head", "wave", "body" }.Where(value => value != headVideo).ToArray(); headVideo = choices[Random.Shared.Next(choices.Length)]; } }
    public void Show(string pose, double seconds, bool walking = false)
    {
        if (ShowVideo(pose, seconds)) return;
        bool sameWalk = current.StartsWith("walk_") && pose.StartsWith("walk_");
        bool changed = current != pose && !sameWalk;
        if (sameWalk) current = pose;
        if (changed)
        {
            bool sitting = pose is "read" or "write";
            bool wasSitting = current is "read" or "write";
            bool standing = pose is "idle" or "stretch" || pose.StartsWith("walk_");
            bool wasStanding = current is "idle" or "stretch" || current.StartsWith("walk_");
            transition = null;
            if (!ReducedMotion && clips.TryGetValue("settle", out var settling) && (sitting && wasStanding || standing && wasSitting))
            {
                transition = sitting ? settling : new PoseClip(settling.Frames.Reverse().ToArray(), settling.Durations.Reverse().ToArray());
                transitionAt = seconds; transitionEnd = seconds + transition.Durations.Sum();
            }
            current = pose; started = seconds;
        }
        BitmapSource? bitmap;
        if (!ReducedMotion && transition is not null && seconds < transitionEnd) bitmap = transition.At(seconds - transitionAt);
        else if (clips.TryGetValue(pose, out var clip)) bitmap = clip.At(ReducedMotion ? 0 : seconds - (transition is null ? started : transitionEnd));
        else if (!images.TryGetValue(pose, out bitmap)) images.TryGetValue(pose switch { "head" => "idle", "body" => "excited", "lift" => "excited", "write" => "draw", _ => "idle" }, out bitmap);
        if (bitmap is null) return;
        if (!ReferenceEquals(front.Source, bitmap))
        {
            back.Source = front.Source; front.Source = bitmap;
            double duration = ReducedMotion ? 0 : changed ? .22 : .07;
            back.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(back.Source is null ? 0 : 1, 0, TimeSpan.FromSeconds(duration)));
            front.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(duration == 0 ? 1 : 0, 1, TimeSpan.FromSeconds(duration)));
        }
        translate.Y = ReducedMotion ? 0 : walking ? -Math.Abs(Math.Sin(seconds * 9)) * 3 : Math.Sin(seconds * 1.5) * .65;
        rotate.Angle = ReducedMotion ? 0 : walking ? Math.Sin(seconds * 9) * 1.1 : pose == "body" ? Math.Sin((seconds - reactionAt) * 12) * Math.Max(0, 1.6 - (seconds - reactionAt)) : 0;
        accents.Update(ReducedMotion ? "" : reaction, seconds - reactionAt);
    }
}

public sealed record PoseClip(BitmapSource[] Frames, double[] Durations)
{
    public BitmapSource At(double seconds)
    {
        double position = Math.Max(0, seconds) % Durations.Sum();
        for (int i = 0; i < Frames.Length; i++) { if (position < Durations[i]) return Frames[i]; position -= Durations[i]; }
        return Frames[0];
    }
}

public sealed class PetAccents : FrameworkElement
{
    private string kind = "";
    private double elapsed;
    public void Update(string value, double seconds)
    {
        if ((seconds > 2 || value == "") && kind == "") return;
        kind = seconds is >= 0 and < 2 ? value : ""; elapsed = seconds; InvalidateVisual();
    }
    protected override void OnRender(DrawingContext dc)
    {
        if (kind == "") return;
        var ink = new SolidColorBrush(kind == "head" ? System.Windows.Media.Color.FromRgb(194, 130, 155) : System.Windows.Media.Color.FromRgb(196, 164, 98));
        dc.PushOpacity(Math.Max(0, 1 - elapsed / 2));
        for (int i = 0; i < 3; i++)
        {
            double x = ActualWidth * (.18 + i * .32), y = ActualHeight * (.25 - elapsed * .045) + (i % 2) * 12;
            if (kind == "head")
            {
                var heart = Geometry.Parse("M0,3 C-7,-5 -11,5 0,12 C11,5 7,-5 0,3 Z");
                dc.PushTransform(new TranslateTransform(x, y)); dc.DrawGeometry(ink, null, heart); dc.Pop();
            }
            else { var pen = new System.Windows.Media.Pen(ink, 1.5); dc.DrawLine(pen, new Point(x-4,y),new Point(x+4,y)); dc.DrawLine(pen,new Point(x,y-4),new Point(x,y+4)); }
        }
        dc.Pop();
    }
}
