// Streaming decoder design adapted from Bai-Cute/big-blue-fish-desktop-pet,
// upstream contributors, Apache-2.0. License: licenses/Apache-2.0.txt.
using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace DafeiyuPet;

public sealed class PetVideoPlayer : IDisposable
{
    public const int Width = 360, Height = 360;
    public int Fps { get; }
    private readonly string executable;
    private readonly Dispatcher dispatcher;
    private VideoDecoderJob? job;
    private Process? process;
    private CancellationTokenSource? cancellation;
    private long generation;
    private bool disposed;
    private int loopFrames = 241;
    private TaskCompletionSource<bool>? activation;
    public long Frames { get; private set; }
    public double Position => Frames % loopFrames / (double)Fps;
    public double Duration => loopFrames / (double)Fps;
    public int? DecoderPid { get { try { var active = process; return active is { HasExited: false } ? active.Id : null; } catch (InvalidOperationException) { return null; } } }
    public event Action<BitmapSource, bool>? Frame;
    public event Action<string>? Failed;
    public PetVideoPlayer(string executable, Dispatcher dispatcher, int fps = 24) { this.executable = executable; this.dispatcher = dispatcher; Fps = fps; }

    public void Play(string file, int frames)
        => Start(file, frames, false);
    public void Prepare(string file, int frames)
        => Start(file, frames, true);
    public void Activate() => activation?.TrySetResult(true);
    private void Start(string file, int frames, bool hold)
    {
        Stop(); if (disposed) return;
        Frames = 0;
        loopFrames = frames;
        var id = generation;
        cancellation = new();
        var token = cancellation.Token;
        activation = hold ? new(TaskCreationOptions.RunContinuationsAsynchronously) : null;
        var gate = activation?.Task;
        var start = new ProcessStartInfo(executable) {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        foreach (string argument in new[] { "-v", "error", "-nostdin", "-threads", "1", "-stream_loop", "-1", "-c:v", "libvpx-vp9",
            "-i", file, "-vf", "crop=360:360:140:0", "-an", "-sn", "-f", "rawvideo", "-pix_fmt", "bgra", "pipe:1" }) start.ArgumentList.Add(argument);
        var decoder = new Process { StartInfo = start };
        var playbackJob = new VideoDecoderJob();
        job = playbackJob;
        try
        {
            decoder.Start(); process = decoder;
            if (!playbackJob.Attach(decoder))
            {
                // This process was never attached, so closing the job cannot stop it.
                try { decoder.Kill(); } catch (System.ComponentModel.Win32Exception) { }
                throw new IOException("无法绑定动画解码进程");
            }
            _ = Task.Run(() => Decode(decoder, playbackJob, id, token, gate));
        }
        catch (Exception error) { Stop(); decoder.Dispose(); Failed?.Invoke(error.GetType().Name); }
    }

    private async Task Decode(Process decoder, VideoDecoderJob playbackJob, long id, CancellationToken token, Task? gate)
    {
        using var timing = new VideoTimerResolution();
        var errors = decoder.StandardError.ReadToEndAsync(token);
        try
        {
            var pixels = new byte[Width * Height * 4];
            WriteableBitmap? bitmap = null;
            var clock = Stopwatch.StartNew();
            double next = 0;
            while (true)
            {
                double wait = next - clock.Elapsed.TotalSeconds;
                if (wait > 0) await Task.Delay(TimeSpan.FromSeconds(wait), token);
                int offset = 0;
                while (offset < pixels.Length)
                {
                    int read = await decoder.StandardOutput.BaseStream.ReadAsync(pixels.AsMemory(offset), token);
                    if (read == 0) throw new IOException("动画流提前结束");
                    offset += read;
                }
                for (int i = 3; i < pixels.Length; i += 4) if (pixels[i] < 8) pixels[i] = 0;
                // Only one frame is outstanding. Never build an unbounded UI queue.
                await dispatcher.InvokeAsync(() => {
                    if (id != generation || disposed) return;
                    bool first = bitmap is null;
                    bitmap ??= new WriteableBitmap(Width, Height, 96, 96, PixelFormats.Bgra32, null);
                    bitmap.WritePixels(new Int32Rect(0, 0, Width, Height), pixels, Width * 4, 0);
                    Frames++; Frame?.Invoke(bitmap, first);
                }, DispatcherPriority.Render, token);
                if (Frames == 1 && gate is not null) { await gate.WaitAsync(token); clock.Restart(); next = 0; }
                // A blocked desktop resumes smoothly instead of bursting old frames.
                next = Math.Max(next + 1d / Fps, clock.Elapsed.TotalSeconds);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception error)
        {
            if (!dispatcher.HasShutdownStarted)
                await dispatcher.InvokeAsync(() => { if (id == generation && !disposed) Failed?.Invoke(error.GetType().Name); });
        }
        finally
        {
            playbackJob.Dispose();
            if (ReferenceEquals(process, decoder)) process = null;
            decoder.Dispose();
            try { await errors; } catch (OperationCanceledException) { }
        }
    }
    public void Stop()
    {
        generation++;
        cancellation?.Cancel(); cancellation?.Dispose(); cancellation = null;
        process = null;
        var previous = job; job = null;
        // Closing the owned job atomically kills its decoder. It avoids Kill()
        // racing an already exiting Windows process during rapid pose changes.
        previous?.Dispose();
    }
    public void Dispose() { if (disposed) return; disposed = true; Stop(); }
}

// Request finer timer granularity only while this decoder is alive. Every
// acquisition is paired, including cancellation while a preloaded frame waits.
internal sealed class VideoTimerResolution : IDisposable
{
    [System.Runtime.InteropServices.DllImport("winmm.dll")] private static extern uint timeBeginPeriod(uint period);
    [System.Runtime.InteropServices.DllImport("winmm.dll")] private static extern uint timeEndPeriod(uint period);
    private bool active = timeBeginPeriod(1) == 0;
    public void Dispose() { if (active) { active = false; timeEndPeriod(1); } }
}
