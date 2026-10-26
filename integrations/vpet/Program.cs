using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Controls.Primitives;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using LinePutScript;
using VPet_Simulator.Core;
using App = System.Windows.Application;
using Window = System.Windows.Window;
using Main = VPet_Simulator.Core.Main;
using Brushes = System.Windows.Media.Brushes;

namespace DafeiyuPet;

public static class Program
{
    [STAThread]
    public static void Main(string[] args)
    {
        var rootIndex = Array.IndexOf(args, "--root");
        if (rootIndex < 0 || rootIndex + 1 >= args.Length) return;
        var root = Path.GetFullPath(args[rootIndex + 1]);
        using var mutex = new Mutex(true, @"Local\DafeiyuPet_" + Convert.ToHexString(
            System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(root.ToLowerInvariant()))), out bool created);
        if (!created) return;
        try
        {
            var app = new App();
            app.DispatcherUnhandledException += (_, e) => Log(root, e.Exception);
            int portIndex = Array.IndexOf(args, "--port");
            int port = portIndex >= 0 && portIndex + 1 < args.Length ? int.Parse(args[portIndex + 1]) : 8766;
            app.Run(new PetWindow(root, port));
        }
        catch (Exception error) { Log(root, error); }
    }

    private static void Log(string root, Exception error)
    {
        Directory.CreateDirectory(Path.Combine(root, "logs"));
        File.AppendAllText(Path.Combine(root, "logs", "vpet-host.log"), DateTime.Now + " " + error + Environment.NewLine);
    }
}

public sealed class PetWindow : Window, IController
{
    private readonly string root;
    private readonly HttpClient client = new() { BaseAddress = new Uri("http://127.0.0.1:8766"), Timeout = TimeSpan.FromSeconds(175) };
    private readonly TextBlock bubble = new() { TextWrapping = TextWrapping.Wrap, FontSize = 13, Foreground = Brushes.DarkSlateBlue };
    private readonly TextBlock status = new() { Text = "正在连接大肥鱼…", FontSize = 11, Foreground = Brushes.SlateGray, TextAlignment = TextAlignment.Center };
    private readonly DispatcherTimer poll = new() { Interval = TimeSpan.FromSeconds(2) };
    private readonly DispatcherTimer bubbleTimer = new() { Interval = TimeSpan.FromSeconds(12) };
    private readonly DispatcherTimer positionTimer = new() { Interval = TimeSpan.FromMilliseconds(800) };
    private readonly DispatcherTimer wanderTimer = new() { Interval = TimeSpan.FromSeconds(75) };
    private readonly Popup bubblePopup;
    private readonly System.Windows.Forms.NotifyIcon tray;
    private readonly DesktopMedia media;
    private readonly HashSet<string> seen = new();
    private WindowPreferences preferences;
    private long? generation;
    private string? scope;
    public ChatWindow Chat { get; }
    private Main? pet;
    private bool loading, interacting, quitting, polling;
    private DateTime lastInteraction;
    private double zoom = .52;
    public bool RePositionActive { get; set; }
    public bool EnableFunction => false;
    public int InteractionCycle => 100;
    public int PressLength => 450;
    public double ZoomRatio => zoom;

    public PetWindow(string root, int port = 8766)
    {
        this.root = root;
        client.BaseAddress = new Uri($"http://127.0.0.1:{port}");
        preferences = LoadPreferences(); zoom = preferences.Zoom;
        Title = "大肥鱼桌宠";
        WindowStyle = WindowStyle.None;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        ResizeMode = ResizeMode.NoResize;
        ShowInTaskbar = false;
        Topmost = true;
        Width = Math.Max(280, 500 * zoom + 30); Height = 500 * zoom + 42;
        var area = SystemParameters.WorkArea;
        Left = preferences.Left ?? area.Right - Width - 30; Top = preferences.Top ?? area.Bottom - Height - 20;
        KeepOnScreen();
        var stack = new StackPanel();
        bubblePopup = new Popup { PlacementTarget = this, Placement = PlacementMode.Top, AllowsTransparency = true, StaysOpen = true,
            Child = new Border { Background = Brushes.White, CornerRadius = new CornerRadius(12), Padding = new Thickness(12),
                MaxWidth = 330, MaxHeight = 220, Child = new ScrollViewer { Content = bubble, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } } };
        bubble.MouseLeftButtonDown += (_, _) => OpenChat();
        var stage = new Grid { Height = 500 * zoom, Width = 500 * zoom };
        stack.Children.Add(stage);
        stack.Children.Add(new Border { Child = status, Background = Brushes.WhiteSmoke, CornerRadius = new CornerRadius(8), Padding = new Thickness(7, 4, 7, 4), Margin = new Thickness(20, 3, 20, 3) });
        Content = stack;
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", File.ReadAllText(Path.Combine(root, "config", "vpet-bridge.token")).Trim());
        Chat = new ChatWindow(this);
        media = new DesktopMedia(this, client, root) { Quiet = preferences.Quiet };
        tray = new System.Windows.Forms.NotifyIcon { Text = "大肥鱼桌宠", Icon = System.Drawing.SystemIcons.Application, Visible = true };
        var trayMenu = new System.Windows.Forms.ContextMenuStrip();
        trayMenu.Items.Add("显示 / 隐藏", null, (_, _) => ToggleVisible());
        trayMenu.Items.Add("和大肥鱼说话", null, (_, _) => OpenChat());
        trayMenu.Items.Add("安静陪伴 / 恢复", null, (_, _) => SetQuiet(!preferences.Quiet));
        trayMenu.Items.Add("退出桌宠", null, (_, _) => Quit());
        tray.ContextMenuStrip = trayMenu; tray.DoubleClick += (_, _) => { Show(); OpenChat(); };
        var menu = new ContextMenu();
        AddMenu(menu, "和大肥鱼说话", OpenChat);
        AddMenu(menu, "摸摸头", () => pet?.DisplayTouchHead());
        AddMenu(menu, "戳一下", () => pet?.DisplayTouchBody());
        AddMenu(menu, "散步一下", Walk);
        AddMenu(menu, "安静陪伴 / 恢复", () => SetQuiet(!preferences.Quiet));
        AddMenu(menu, "自动散步：开 / 关", () => { preferences.Wander = !preferences.Wander; SavePreferences(); ShowBubble(preferences.Wander ? "空闲时偶尔走走。" : "就在这里陪你。"); });
        AddMenu(menu, "停止播放", media.Stop);
        AddMenu(menu, "隐藏到托盘", ToggleVisible);
        menu.Items.Add(new Separator());
        AddMenu(menu, "打开共同经历", () => _ = OpenPage("creative"));
        AddMenu(menu, "打开日历", () => _ = OpenPage("calendar"));
        AddMenu(menu, "打开曲库", () => _ = OpenPage("songbook"));
        menu.Items.Add(new Separator());
        foreach (double size in new[] { .4, .52, .64 })
        {
            double ratio = size;
            AddMenu(menu, $"大小 {(int)(ratio * 500)}", () => { zoom = ratio; stage.Width = stage.Height = 500 * zoom; Width = Math.Max(280, stage.Width + 30); Height = stage.Height + 42; KeepOnScreen(); SavePreferences(); });
        }
        AddMenu(menu, "关于演示素材", () => ShowBubble("当前动作素材来自 VPet 原作者，角色外观尚未替换。大肥鱼的人设与记忆由原后台提供。"));
        AddMenu(menu, "退出桌宠", Quit);
        PreviewMouseRightButtonDown += (_, e) => { menu.PlacementTarget = this; menu.IsOpen = true; e.Handled = true; };
        poll.Tick += async (_, _) => await Refresh();
        bubbleTimer.Tick += (_, _) => { bubblePopup.IsOpen = false; bubbleTimer.Stop(); };
        positionTimer.Tick += (_, _) => { positionTimer.Stop(); SavePreferences(); };
        LocationChanged += (_, _) => { bubblePopup.IsOpen = false; positionTimer.Stop(); positionTimer.Start(); };
        wanderTimer.Tick += (_, _) => { if (preferences.Wander && !preferences.Quiet && IsVisible && !Chat.IsVisible && !interacting && DateTime.UtcNow - lastInteraction > TimeSpan.FromSeconds(40)) Walk(); };
        Loaded += async (_, _) => {
            try
            {
                loading = true;
                GraphCore.CachePath = Path.Combine(root, "cache", "vpet");
                var assets = Path.Combine(root, "vpet", "assets");
                var loader = new PetLoader(new LpsDocument(File.ReadAllText(Path.Combine(assets, "vup.lps"))), new DirectoryInfo(assets));
                var core = new GameCore { Controller = this, Save = new GameSave("大肥鱼"), Graph = loader.Graph(500, Dispatcher) };
                core.Graph.GraphConfig.Moves.RemoveAll(move => move.Graph != "walk.left" && move.Graph != "walk.right");
                pet = new Main(core) { NoFunctionMOD = IGameSave.ModeType.Nomal };
                core.Save.Mode = IGameSave.ModeType.Nomal;
                stage.Children.Add(pet);
                pet.Load_0_BaseConsole();
                pet.Load_2_TouchEvent();
                await pet.Load_2_WaitGraph();
                pet.EventTimer.Stop(); // Autonomous status/AI stays in the companion backend.
                pet.Load_3_BindingTimer();
                pet.Load_4_Start();
                pet.SetMoveMode(true, false, 1200);
                pet.Event_TouchHead += () => Dispatcher.InvokeAsync(() => _ = Interact("head"));
                pet.Event_TouchBody += () => Dispatcher.InvokeAsync(() => _ = Interact("body"));
                pet.GraphDisplayHandler += info => {
                    if (info.Type.ToString().StartsWith("Raised"))
                        Dispatcher.InvokeAsync(() => _ = Interact("lift"));
                };
                pet.ToolBar.Visibility = Visibility.Collapsed;
                loading = false;
                poll.Start(); wanderTimer.Start(); SetQuiet(preferences.Quiet); await Refresh();
            }
            catch (Exception ex) { status.Text = "桌宠加载失败，请查看日志"; File.AppendAllText(Path.Combine(root, "logs", "vpet-host.log"), ex + Environment.NewLine); }
        };
        Closed += (_, _) => { poll.Stop(); bubbleTimer.Stop(); positionTimer.Stop(); wanderTimer.Stop(); SavePreferences();
            bubblePopup.IsOpen = false; tray.Visible = false; tray.Dispose(); media.Dispose();
            Chat.ShuttingDown = true; Chat.Close(); pet?.Dispose(); client.Dispose(); if (!quitting) Disable(); };
    }

    private static void AddMenu(ContextMenu menu, string title, Action action)
    {
        var item = new MenuItem { Header = title };
        item.Click += (_, _) => action(); menu.Items.Add(item);
    }

    private void ShowBubble(string text)
    {
        bubble.Text = text; bubblePopup.IsOpen = IsVisible;
        bubbleTimer.Stop(); bubbleTimer.Start();
    }

    public async Task<JsonElement> Request(string path, object? data = null)
    {
        using var response = data is null ? await client.GetAsync(path) : await client.PostAsync(path,
            new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json"));
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
        if (!response.IsSuccessStatusCode || !json.RootElement.GetProperty("ok").GetBoolean())
            throw new InvalidOperationException(json.RootElement.TryGetProperty("message", out var message) ? message.GetString() : "后台暂时没有连接成功。");
        return json.RootElement.GetProperty("data").Clone();
    }

    private async Task Refresh()
    {
        if (polling || loading || quitting) return;
        polling = true;
        try
        {
            var data = await Request("/status");
            if (data.GetProperty("game_mode").GetBoolean() || !data.GetProperty("enabled").GetBoolean()) { quitting = true; Close(); return; }
            long current = data.GetProperty("generation").GetInt64();
            string currentScope = data.GetProperty("scope").GetString()!;
            if (generation.HasValue && (generation != current || scope != currentScope)) { media.Reset(); Chat.ResetMemory(); seen.Clear(); bubblePopup.IsOpen = false; }
            generation = current;
            scope = currentScope;
            status.Text = data.GetProperty("ready").GetBoolean() ? data.GetProperty("activity").GetString() : "大肥鱼正在休息";
            status.ToolTip = data.GetProperty("project").GetString() + "\n" + data.GetProperty("next_step").GetString();
            var messages = (await Request("/messages")).GetProperty("messages");
            var acknowledgements = new List<string>();
            foreach (var message in messages.EnumerateArray())
            {
                var id = message.GetProperty("id").GetString()!;
                if (seen.Add(id)) { var text = message.GetProperty("text").GetString(); if (!string.IsNullOrWhiteSpace(text)) ShowBubble(text); media.Add(message); }
                acknowledgements.Add(id);
            }
            if (acknowledgements.Count > 0) await Request("/ack", new { ids = acknowledgements });
            await Chat.RefreshHistory();
        }
        catch (Exception) { status.Text = "后台连接中，互动动画仍可使用"; }
        finally { polling = false; }
    }

    private async Task Interact(string kind)
    {
        if (interacting || DateTime.UtcNow - lastInteraction < TimeSpan.FromSeconds(2)) return;
        lastInteraction = DateTime.UtcNow; interacting = true;
        try
        {
            var data = await Request("/interact", new { id = Guid.NewGuid().ToString("N"), kind });
            var messages = data.GetProperty("messages").EnumerateArray().Select(x => x.GetString()).Where(x => !string.IsNullOrWhiteSpace(x));
            var text = string.Join("\n", messages);
            if (text.Length > 0) ShowBubble(text);
        }
        catch (Exception) { ShowBubble("刚才这次互动还没收到后台回应，可以稍后再碰我一下。 "); }
        finally { interacting = false; }
    }

    private async Task OpenPage(string page)
    {
        try { await Request("/open", new { page }); }
        catch (Exception) { ShowBubble("控制台暂时没打开，可以从原来的桌面入口进入。"); }
    }

    private void Disable()
    {
        try
        {
            var path = Path.Combine(root, "config", "vpet.json");
            var temp = path + "." + Guid.NewGuid().ToString("N") + ".new";
            File.WriteAllText(temp, "{\"enabled\":false}"); File.Move(temp, path, true);
        }
        catch (IOException) { }
    }

    public void Note(string text) { if (!quitting) ShowBubble(text); }
    public void OpenChat() { if (quitting) return; Chat.Show(); Chat.Activate(); }
    private void Quit() { Disable(); quitting = true; Close(); }
    private void ToggleVisible() { if (IsVisible) { Hide(); bubblePopup.IsOpen = false; media.Stop(); media.Quiet = true; } else { Show(); media.Quiet = preferences.Quiet; KeepOnScreen(); } }
    private void Walk()
    {
        if (pet is null || preferences.Quiet || pet.isPress) return;
        if (pet.DisplayType.Type == GraphInfo.GraphType.Default) pet.DisplayMove();
    }
    private void SetQuiet(bool value)
    {
        preferences.Quiet = value; media.Quiet = value;
        if (value) media.Stop();
        if (pet is not null)
        {
            pet.MoveTimer.Stop();
            if (value) pet.DisplayStopForce(() => pet.DisplaySleep(true));
            else { pet.State = Main.WorkingState.Nomal; pet.DisplayStopForce(pet.DisplayToNomal); }
        }
        SavePreferences();
    }
    private WindowPreferences LoadPreferences()
    {
        try
        {
            var item = JsonSerializer.Deserialize<WindowPreferences>(File.ReadAllText(Path.Combine(root, "data", "vpet-window.json"))) ?? new();
            if (item.Zoom is not (.4 or .52 or .64)) item.Zoom = .52;
            if (item.Left.HasValue && !double.IsFinite(item.Left.Value)) item.Left = null;
            if (item.Top.HasValue && !double.IsFinite(item.Top.Value)) item.Top = null;
            return item;
        }
        catch (Exception ex) when (ex is IOException or JsonException) { return new(); }
    }
    private void SavePreferences()
    {
        preferences.Left = Left; preferences.Top = Top; preferences.Zoom = zoom;
        try
        {
            var path = Path.Combine(root, "data", "vpet-window.json"); Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path + ".new", JsonSerializer.Serialize(preferences)); File.Move(path + ".new", path, true);
        }
        catch (IOException) { }
    }

    private void KeepOnScreen()
    {
        var area = SystemParameters.WorkArea;
        Left = Math.Clamp(Left, area.Left, Math.Max(area.Left, area.Right - Width));
        Top = Math.Clamp(Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
    }
    public void MoveWindows(double x, double y) { Dispatcher.Invoke(() => { Left += x * zoom; Top += y * zoom; KeepOnScreen(); }); }
    public double GetWindowsDistanceLeft() => Dispatcher.Invoke(() => Left - SystemParameters.WorkArea.Left);
    public double GetWindowsDistanceRight() => Dispatcher.Invoke(() => SystemParameters.WorkArea.Right - Left - Width);
    public double GetWindowsDistanceUp() => Dispatcher.Invoke(() => Top - SystemParameters.WorkArea.Top);
    public double GetWindowsDistanceDown() => Dispatcher.Invoke(() => SystemParameters.WorkArea.Bottom - Top - Height);
    public void ShowPanel() => _ = OpenPage("home");
    public void ResetPosition() => Dispatcher.Invoke(KeepOnScreen);
    public bool CheckPosition() => true;
}

public sealed class WindowPreferences
{
    public double? Left { get; set; }
    public double? Top { get; set; }
    public double Zoom { get; set; } = .52;
    public bool Quiet { get; set; }
    public bool Wander { get; set; } = true;
}
