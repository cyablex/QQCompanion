using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Image = System.Windows.Controls.Image;
using Point = System.Windows.Point;

namespace DafeiyuPet;

public sealed class PetVisuals
{
    public Image View { get; } = new() { IsHitTestVisible = false, Stretch = Stretch.Uniform };
    private readonly Dictionary<string, BitmapSource> images = new();
    private readonly TranslateTransform translate = new();
    private readonly RotateTransform rotate = new();
    private string current = "";
    public bool Ready => images.ContainsKey("idle");
    public PetVisuals(string root)
    {
        var folder = Path.Combine(root, "vpet", "characters", "dafeiyu-v1", "poses");
        foreach (string pose in new[] { "idle", "read", "draw", "stretch", "excited", "eat", "rest", "walk_left0", "walk_left1", "walk_right0", "walk_right1" })
        {
            string path = Path.Combine(folder, pose + ".png");
            if (!File.Exists(path)) continue;
            var bitmap = new BitmapImage(); bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad;
            bitmap.CreateOptions = BitmapCreateOptions.IgnoreImageCache; bitmap.UriSource = new Uri(path); bitmap.EndInit(); bitmap.Freeze();
            images[pose] = bitmap;
        }
        var transforms = new TransformGroup(); transforms.Children.Add(rotate); transforms.Children.Add(translate);
        View.RenderTransform = transforms; View.RenderTransformOrigin = new Point(.5, .85);
        RenderOptions.SetBitmapScalingMode(View, BitmapScalingMode.HighQuality);
    }
    public void Show(string pose, double seconds, bool walking = false)
    {
        if (!images.TryGetValue(pose, out var bitmap) && !images.TryGetValue("idle", out bitmap)) return;
        if (pose != current) { View.Source = bitmap; current = pose; }
        translate.Y = walking ? -Math.Abs(Math.Sin(seconds * 9)) * 3 : Math.Sin(seconds * 1.5) * .65;
        rotate.Angle = walking ? Math.Sin(seconds * 9) * 1.1 : 0;
    }
}
