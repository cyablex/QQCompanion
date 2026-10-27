using System.IO;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using DrawingImage = System.Drawing.Image;

namespace DafeiyuPet;

// GDI+ composites GIF disposal frames; the WPF timer belongs to the preview.
public sealed class AnimatedPicture : IDisposable
{
    public System.Windows.Controls.Image View { get; } = new() { Stretch = System.Windows.Media.Stretch.Uniform, MaxHeight = 400 };
    private readonly MemoryStream stream;
    private readonly DrawingImage source;
    private readonly DispatcherTimer timer = new();
    private readonly int count;
    private readonly int[] delays;
    private int frame;
    private bool disposed;

    public AnimatedPicture(byte[] bytes)
    {
        stream = new MemoryStream(bytes);
        source = DrawingImage.FromStream(stream);
        var time = System.Drawing.Imaging.FrameDimension.Time;
        count = source.FrameDimensionsList.Contains(time.Guid) ? source.GetFrameCount(time) : 1;
        delays = Enumerable.Repeat(80, count).ToArray();
        if (count > 1 && source.PropertyIdList.Contains(0x5100))
        {
            var values = source.GetPropertyItem(0x5100)?.Value;
            if (values is not null)
                for (int i = 0; i < count && (i + 1) * 4 <= values.Length; i++)
                    delays[i] = Math.Max(40, BitConverter.ToInt32(values, i * 4) * 10);
        }
        Render();
        timer.Tick += (_, _) => { frame = (frame + 1) % count; Render(); };
        if (count > 1) timer.Start();
    }

    private void Render()
    {
        if (disposed) return;
        if (count > 1) source.SelectActiveFrame(System.Drawing.Imaging.FrameDimension.Time, frame);
        using var buffer = new MemoryStream();
        source.Save(buffer, System.Drawing.Imaging.ImageFormat.Png); buffer.Position = 0;
        var bitmap = new BitmapImage(); bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad;
        bitmap.StreamSource = buffer; bitmap.EndInit(); bitmap.Freeze(); View.Source = bitmap;
        timer.Interval = TimeSpan.FromMilliseconds(delays[frame]);
    }

    public void Dispose() { if (disposed) return; disposed = true; timer.Stop(); View.Source = null; source.Dispose(); stream.Dispose(); }
}
