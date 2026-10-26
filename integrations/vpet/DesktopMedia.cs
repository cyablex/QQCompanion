using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Windows.Media;

namespace DafeiyuPet;

// Uses files produced by the existing voice/song/drawing services, no second model.
public sealed class DesktopMedia : IDisposable
{
    private readonly PetWindow owner;
    private readonly HttpClient client;
    private readonly string cache;
    private readonly MediaPlayer player = new();
    private readonly Queue<JsonElement> queue = new();
    private bool busy, disposed;
    private string? currentFile;
    private int epoch;
    public bool Quiet { get; set; }

    public DesktopMedia(PetWindow owner, HttpClient client, string root)
    {
        this.owner = owner; this.client = client; cache = Path.Combine(root, "cache", "vpet-playback");
        ClearFiles(); Directory.CreateDirectory(cache);
        player.MediaOpened += (_, _) => { player.Play(); owner.Note("正在播放；右键菜单可以停止。"); };
        player.MediaEnded += (_, _) => { CloseCurrent(); busy = false; _ = PlayNext(); };
        player.MediaFailed += (_, _) => { CloseCurrent(); busy = false; owner.Note("这份音频暂时没能播放，可以在聊天窗口重试。"); _ = PlayNext(); };
    }

    public void Add(JsonElement message)
    {
        var media = message.GetProperty("media");
        if (media.ValueKind == JsonValueKind.Null) return;
        var copy = message.Clone();
        var kind = media.GetProperty("kind").GetString();
        var audio = kind is "voice" or "song";
        if (!audio) owner.Note("发来一张图片或表情，点这里打开聊天窗口查看。");
        owner.Chat.AddMedia(audio ? (kind == "song" ? "播放歌曲 · " : "播放语音 · ") + message.GetProperty("text").GetString() : "查看图片 / 表情",
            () => { if (audio) { Stop(); queue.Enqueue(copy); _ = PlayNext(true); } else _ = ShowImage(copy); }, audio ? Stop : null);
        if (audio && !Quiet) { queue.Enqueue(copy); _ = PlayNext(); }
    }

    private async Task ShowImage(JsonElement message)
    {
        var current = epoch;
        try
        {
            var bytes = await client.GetByteArrayAsync(message.GetProperty("media").GetProperty("url").GetString());
            if (current != epoch || disposed) return;
            owner.OpenChat(); owner.Chat.ShowImage(bytes);
        }
        catch (Exception) { if (!disposed) owner.Note("这张图片已不可用，文字记录仍然保留。"); }
    }

    private async Task PlayNext(bool manual = false)
    {
        if (busy || disposed || queue.Count == 0 || (Quiet && !manual)) return;
        var message = queue.Dequeue(); busy = true; var current = epoch;
        var extension = message.GetProperty("media").GetProperty("kind").GetString() == "song" ? ".mp3" : ".wav";
        var path = Path.Combine(cache, message.GetProperty("id").GetString() + "-" + current + extension);
        try
        {
            using var response = await client.GetAsync(message.GetProperty("media").GetProperty("url").GetString(), HttpCompletionOption.ResponseHeadersRead);
            response.EnsureSuccessStatusCode();
            if (current != epoch || disposed) return;
            await using (var file = File.Create(path)) { await response.Content.CopyToAsync(file); }
            if (current != epoch || disposed) { File.Delete(path); return; }
            currentFile = path; player.Open(new Uri(path));
        }
        catch (Exception)
        {
            try { File.Delete(path); } catch (IOException) { }
            if (current != epoch || disposed) return;
            busy = false; owner.Note("附件暂时没有读到，可以在聊天窗口重试。"); _ = PlayNext();
        }
    }

    private void CloseCurrent()
    {
        player.Close();
        if (currentFile is not null) { try { File.Delete(currentFile); } catch (IOException) { } currentFile = null; }
    }
    public void Stop() { epoch++; queue.Clear(); CloseCurrent(); busy = false; }
    public void Reset() { Stop(); ClearFiles(); }
    private void ClearFiles()
    {
        if (!Directory.Exists(cache)) return;
        foreach (var file in Directory.EnumerateFiles(cache)) { try { File.Delete(file); } catch (IOException) { } }
    }
    public void Dispose() { disposed = true; Reset(); }
}
