using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using Button = System.Windows.Controls.Button;
using TextBox = System.Windows.Controls.TextBox;
using Brushes = System.Windows.Media.Brushes;

namespace DafeiyuPet;

public sealed class ChatWindow : Window
{
    private readonly PetWindow owner;
    private readonly TextBox history = new() { IsReadOnly = true, TextWrapping = TextWrapping.Wrap, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Background = Brushes.White, BorderThickness = new Thickness(0), Padding = new Thickness(12), FontSize = 14 };
    private readonly TextBox input = new() { MaxLength = 2000, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, Height = 72, Margin = new Thickness(0, 8, 0, 8), FontSize = 14 };
    private readonly TextBlock notice = new() { Text = "和 QQ 里的大肥鱼共用记忆，也可以在这里交代事情。", TextWrapping = TextWrapping.Wrap, Foreground = Brushes.SlateGray };
    private readonly StackPanel attachments = new();
    private readonly Button send = new() { Content = "发送", Padding = new Thickness(22, 5, 22, 5), HorizontalAlignment = System.Windows.HorizontalAlignment.Right };
    private string? pendingId, pendingText;
    private bool sending;
    public bool ShuttingDown { get; set; }

    public ChatWindow(PetWindow owner)
    {
        this.owner = owner;
        Title = "和大肥鱼说话"; Width = 450; Height = 580; MinWidth = 340; MinHeight = 400;
        Background = Brushes.WhiteSmoke;
        var panel = new DockPanel { Margin = new Thickness(14) };
        var bottom = new StackPanel();
        bottom.Children.Add(notice); bottom.Children.Add(input); bottom.Children.Add(send);
        DockPanel.SetDock(bottom, Dock.Bottom); panel.Children.Add(bottom);
        var media = new ScrollViewer { Content = attachments, MaxHeight = 160, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        DockPanel.SetDock(media, Dock.Bottom); panel.Children.Add(media);
        panel.Children.Add(history); Content = panel;
        send.Click += async (_, _) => await Send();
        input.PreviewKeyDown += async (_, e) => {
            if (e.Key == Key.Enter && !Keyboard.Modifiers.HasFlag(ModifierKeys.Shift)) { e.Handled = true; await Send(); }
        };
        Closing += (_, e) => { if (!ShuttingDown) { e.Cancel = true; Hide(); } };
        Activated += async (_, _) => { await RefreshHistory(); input.Focus(); };
    }

    private async Task Send()
    {
        var text = input.Text.Trim();
        if (sending || text.Length == 0) return;
        // Retrying the same uncertain HTTP submission keeps its event ID.
        if (pendingId is null || pendingText != text) { pendingId = Guid.NewGuid().ToString("N"); pendingText = text; }
        sending = true; send.IsEnabled = false; input.IsReadOnly = true;
        try
        {
            var result = await owner.Request("/chat", new { id = pendingId, text });
            input.Clear(); pendingId = pendingText = null;
            notice.Text = result.TryGetProperty("confirmation", out var confirmation) ? confirmation.GetString() : "已接收，回复会出现在这里。";
            await RefreshHistory();
        }
        catch (Exception error) { notice.Text = error.Message + " 可保留原文重试。"; }
        finally { sending = false; send.IsEnabled = true; input.IsReadOnly = false; input.Focus(); }
    }

    public async Task RefreshHistory()
    {
        if (!IsVisible || ShuttingDown) return;
        try
        {
            var data = await owner.Request("/history");
            var text = string.Join("\n\n", data.GetProperty("messages").EnumerateArray().Select(m =>
                (m.GetProperty("role").GetString() == "assistant" ? "大肥鱼：" : "你：") + m.GetProperty("content").GetString()));
            if (history.Text != text) { history.Text = text; history.ScrollToEnd(); }
        }
        catch (Exception) { notice.Text = "聊天记录暂时没有读到，稍后会重新连接。"; }
    }

    public void AddMedia(string label, Action play, Action? stop)
    {
        var row = new StackPanel { Orientation = System.Windows.Controls.Orientation.Horizontal, Margin = new Thickness(0, 3, 0, 3) };
        var button = new Button { Content = label, Padding = new Thickness(8, 4, 8, 4), MaxWidth = 320 };
        button.Click += (_, _) => play(); row.Children.Add(button);
        if (stop is not null)
        {
            var stopButton = new Button { Content = "停止", Margin = new Thickness(6, 0, 0, 0), Padding = new Thickness(8, 4, 8, 4) };
            stopButton.Click += (_, _) => stop(); row.Children.Add(stopButton);
        }
        attachments.Children.Add(row);
        if (attachments.Children.Count > 20) attachments.Children.RemoveAt(0);
    }

    public void ShowImage(byte[] bytes)
    {
        var bitmap = new BitmapImage();
        using var stream = new System.IO.MemoryStream(bytes);
        bitmap.BeginInit(); bitmap.CacheOption = BitmapCacheOption.OnLoad; bitmap.StreamSource = stream; bitmap.EndInit(); bitmap.Freeze();
        var image = new System.Windows.Controls.Image { Source = bitmap, Stretch = Stretch.Uniform, MaxHeight = 400 };
        new Window { Title = "大肥鱼发来的图片", Content = image, Width = 420, Height = 440, Owner = this, Background = Brushes.White }.Show();
    }

    public void ResetMemory()
    {
        history.Clear(); attachments.Children.Clear(); input.Clear(); pendingId = pendingText = null;
        foreach (Window window in OwnedWindows.Cast<Window>().ToArray()) window.Close();
        notice.Text = "对话记录已清理。";
    }
}
