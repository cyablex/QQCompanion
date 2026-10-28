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
using System.Windows.Media.Animation;
using System.Windows.Threading;
using LinePutScript;
using VPet_Simulator.Core;
using App = System.Windows.Application;
using Window = System.Windows.Window;
using Main = VPet_Simulator.Core.Main;
using Brushes = System.Windows.Media.Brushes;
using Button = System.Windows.Controls.Button;
using Point = System.Windows.Point;

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
    private readonly Grid stage;
    private readonly PetVisuals visuals;
    private readonly PetScenes scenes;
    private PeekWindow? peek;
    private Action? bubbleAction;
    private bool diarySnapshot;
    private string diaryJob = "", diaryDay = "";
    private readonly Queue<SceneEvent> sceneEvents = new();
    private bool recordingScenes, backendReady;
    private string backendActivity = "";
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
        Width = Math.Max(180, 500 * zoom + 30); Height = 500 * zoom + 42;
        var area = SystemParameters.WorkArea;
        Left = preferences.Left ?? area.Right - Width - 30; Top = preferences.Top ?? area.Bottom - Height - 20;
        KeepOnScreen();
        var stack = new StackPanel();
        bubblePopup = new Popup { PlacementTarget = this, Placement = PlacementMode.Top, AllowsTransparency = true, StaysOpen = true,
            Child = new Border { Background = Brushes.White, CornerRadius = new CornerRadius(12), Padding = new Thickness(12),
                MaxWidth = 330, MaxHeight = 220, Child = new ScrollViewer { Content = bubble, VerticalScrollBarVisibility = ScrollBarVisibility.Auto } } };
        bubble.MouseLeftButtonDown += (_, _) => { if (bubbleAction is not null) bubbleAction(); else OpenChat(); };
        stage = new Grid { Height = 500 * zoom, Width = 500 * zoom };
        visuals = new PetVisuals(root);
        visuals.VideoFailed += reason => { Directory.CreateDirectory(Path.Combine(root, "logs")); File.AppendAllText(Path.Combine(root, "logs", "vpet-video.log"), DateTime.Now + " " + reason + Environment.NewLine); ShowBubble("动画暂未能播放，已保留同一角色的静态画面。可以重新打开桌宠重试。"); };
        visuals.ReducedMotion = preferences.ReducedMotion || !SystemParameters.ClientAreaAnimation;
        scenes = new PetScenes(this, visuals);
        stack.Children.Add(stage);
        stack.Children.Add(new Border { Child = status, Background = Brushes.WhiteSmoke, CornerRadius = new CornerRadius(8), Padding = new Thickness(7, 4, 7, 4), Margin = new Thickness(20, 3, 20, 3) });
        Content = stack;
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", File.ReadAllText(Path.Combine(root, "config", "vpet-bridge.token")).Trim());
        Chat = new ChatWindow(this);
        media = new DesktopMedia(this, client, root) { Quiet = preferences.Quiet };
        tray = new System.Windows.Forms.NotifyIcon { Text = "大肥鱼桌宠", Icon = System.Drawing.SystemIcons.Application, Visible = true };
        var trayMenu = new System.Windows.Forms.ContextMenuStrip();
        trayMenu.Items.Add("潜水收起 / 恢复", null, (_, _) => ToggleVisible());
        trayMenu.Items.Add("和大肥鱼说话", null, (_, _) => OpenChat());
        trayMenu.Items.Add("安静陪伴 / 恢复", null, (_, _) => SetQuiet(!preferences.Quiet));
        trayMenu.Items.Add("退出桌宠", null, (_, _) => Quit());
        tray.ContextMenuStrip = trayMenu; tray.DoubleClick += (_, _) => { RestoreFromPeek(); OpenChat(); };
        var menu = new ContextMenu();
        AddMenu(menu, "和大肥鱼说话", OpenChat);
        AddMenu(menu, "摸摸头", () => pet?.DisplayTouchHead());
        AddMenu(menu, "戳一下", () => pet?.DisplayTouchBody());
        AddMenu(menu, "散步一下", Walk);
        AddMenu(menu, "翻翻手边的书", () => scenes.Choose("read"));
        AddMenu(menu, "随手涂几笔", () => scenes.Choose("draw"));
        AddMenu(menu, "伸个懒腰", () => scenes.Choose("stretch"));
        if (visuals.VideoReady) AddMenu(menu, "提裙打个招呼", () => scenes.Choose("greeting"));
        AddMenu(menu, "喂大白饭", scenes.SpawnRice);
        AddMenu(menu, "收起米饭", scenes.CancelRice);
        AddMenu(menu, "安静陪伴 / 恢复", () => SetQuiet(!preferences.Quiet));
        AddMenu(menu, "自动散步：开 / 关", () => { preferences.Wander = !preferences.Wander; SavePreferences(); ShowBubble(preferences.Wander ? "空闲时偶尔走走。" : "就在这里陪你。"); });
        AddMenu(menu, "日常小动作：开 / 关", () => { preferences.DailyActivities = !preferences.DailyActivities; SavePreferences(); });
        AddMenu(menu, "减少动效：开 / 关", () => { preferences.ReducedMotion = !preferences.ReducedMotion; visuals.ReducedMotion = preferences.ReducedMotion || !SystemParameters.ClientAreaAnimation; SavePreferences(); });
        AddMenu(menu, "停止播放", media.Stop);
        AddMenu(menu, "潜水收起", ToggleVisible);
        AddMenu(menu, "完全隐藏到托盘", HideCompletely);
        menu.Items.Add(new Separator());
        AddMenu(menu, "打开共同经历", () => _ = OpenPage("creative"));
        AddMenu(menu, "打开鲸鱼日记", OpenDiary);
        AddMenu(menu, "打开日历", () => _ = OpenPage("calendar"));
        AddMenu(menu, "打开曲库", () => _ = OpenPage("songbook"));
        menu.Items.Add(new Separator());
        var sizePanel = new StackPanel { Width = 225, Margin = new Thickness(8) };
        var sizeLabel = new TextBlock { Text = $"大小 {preferences.SizePercent:0}%（当前基准100%）" };
        var sizeSlider = new Slider { Minimum = 50, Maximum = 200, Value = preferences.SizePercent,
            SmallChange = 1, LargeChange = 10, TickFrequency = 25, IsSnapToTickEnabled = false, Margin = new Thickness(0, 8, 0, 5) };
        sizeSlider.ValueChanged += (_, _) => { sizeLabel.Text = $"大小 {sizeSlider.Value:0}%（50—200%）"; SetSizePercent(sizeSlider.Value); };
        var resetSize = new Button { Content = "恢复100%", Padding = new Thickness(6, 2, 6, 2) };
        resetSize.Click += (_, _) => sizeSlider.Value = 100;
        sizePanel.Children.Add(sizeLabel); sizePanel.Children.Add(sizeSlider); sizePanel.Children.Add(resetSize);
        menu.Items.Add(new MenuItem { Header = sizePanel, StaysOpenOnClick = true, Focusable = false });
        AddMenu(menu, "关于当前形象", () => ShowBubble(visuals.VideoReady ? "蓝发女仆透明视频形象，14段连续动作，24帧/秒。和QQ共用人设与任务；日常动作不写日记，语音口型待补充。" : File.Exists(Path.Combine(root, "vpet", "characters", "dafeiyu-v1", "character.json"))
            ? visuals.Ready ? "蓝发女仆高清形象；有读书、涂画、伸懒腰、休息和追饭动作。和 QQ 共用人设与记忆；日常姿态属于本地陪伴动作。" : "蓝发女仆基础动作，复用本机 QQ 表情；和 QQ 共用同一套人设与记忆。可准备本机高清姿态，语音口型待补充。"
            : "当前为 VPet 原作者演示素材；本机有女仆表情包时，可运行角色准备工具接入。"));
        AddMenu(menu, "退出桌宠", Quit);
        PreviewMouseRightButtonDown += (_, e) => { menu.PlacementTarget = this; menu.IsOpen = true; e.Handled = true; };
        poll.Tick += async (_, _) => await Refresh();
        bubbleTimer.Tick += (_, _) => { bubblePopup.IsOpen = false; bubbleTimer.Stop(); };
        positionTimer.Tick += (_, _) => { positionTimer.Stop(); SavePreferences(); };
        LocationChanged += (_, _) => { bubblePopup.IsOpen = false; positionTimer.Stop(); positionTimer.Start(); };
        wanderTimer.Tick += (_, _) => { if (preferences.Wander && !preferences.Quiet && scenes.CanWander && IsVisible && !Chat.IsVisible && !interacting && DateTime.UtcNow - lastInteraction > TimeSpan.FromSeconds(40)) Walk(); };
        Loaded += async (_, _) => {
            try
            {
                loading = true;
                GraphCore.CachePath = Path.Combine(root, "cache", "vpet");
                var custom = Path.Combine(root, "vpet", "characters", "dafeiyu-v1");
                var assets = File.Exists(Path.Combine(custom, "character.json")) ? custom : Path.Combine(root, "vpet", "assets");
                var model = assets == custom ? "pet.lps" : "vup.lps";
                var loader = new PetLoader(new LpsDocument(File.ReadAllText(Path.Combine(assets, model))), new DirectoryInfo(assets));
                var core = new GameCore { Controller = this, Save = new GameSave("大肥鱼"), Graph = loader.Graph(500, Dispatcher) };
                core.Graph.GraphConfig.Moves.RemoveAll(move => move.Graph != "walk.left" && move.Graph != "walk.right");
                pet = new Main(core) { NoFunctionMOD = IGameSave.ModeType.Nomal };
                core.Save.Mode = IGameSave.ModeType.Nomal;
                stage.Children.Add(pet);
                if (visuals.Ready) { pet.Opacity = 0; stage.Children.Add(visuals.View); }
                pet.Load_0_BaseConsole();
                pet.Load_2_TouchEvent();
                await pet.Load_2_WaitGraph();
                pet.EventTimer.Stop(); // Autonomous status/AI stays in the companion backend.
                pet.Load_3_BindingTimer();
                pet.Load_4_Start();
                pet.SetMoveMode(true, false, 1200);
                pet.Event_TouchHead += () => Dispatcher.InvokeAsync(() => { scenes.React("head"); _ = Interact("head"); });
                pet.Event_TouchBody += () => Dispatcher.InvokeAsync(() => { scenes.React("body"); _ = Interact("body"); });
                pet.GraphDisplayHandler += info => {
                    if (info.Type.ToString().StartsWith("Raised"))
                        Dispatcher.InvokeAsync(() => { scenes.CancelRice(); scenes.React("lift"); _ = Interact("lift"); });
                };
                pet.ToolBar.Visibility = Visibility.Collapsed;
                loading = false;
                poll.Start(); wanderTimer.Start(); SetQuiet(preferences.Quiet); await Refresh();
            }
            catch (Exception ex) { status.Text = "桌宠加载失败，请查看日志"; File.AppendAllText(Path.Combine(root, "logs", "vpet-host.log"), ex + Environment.NewLine); }
        };
        Closed += (_, _) => { poll.Stop(); bubbleTimer.Stop(); positionTimer.Stop(); wanderTimer.Stop(); SavePreferences();
            peek?.Close(); peek = null;
            bubblePopup.IsOpen = false; scenes.Dispose(); sceneEvents.Clear(); tray.Visible = false; tray.Dispose(); media.Dispose();
            Chat.ShuttingDown = true; Chat.Close(); pet?.Dispose(); client.Dispose(); if (!quitting) Disable(); };
    }

    private static void AddMenu(ContextMenu menu, string title, Action action)
    {
        var item = new MenuItem { Header = title };
        item.Click += (_, _) => action(); menu.Items.Add(item);
    }

    private void ShowBubble(string text)
    {
        bubbleAction = null;
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
            if (generation.HasValue && (generation != current || scope != currentScope)) { scenes.CancelRice(); sceneEvents.Clear(); media.Reset(); Chat.ResetMemory(); seen.Clear(); bubblePopup.IsOpen = false; diarySnapshot = false; diaryJob = diaryDay = ""; peek?.ClearNotice(); }
            generation = current;
            scope = currentScope;
            backendReady = data.GetProperty("ready").GetBoolean();
            string activity = data.GetProperty("activity").GetString() ?? "";
            scenes.Backend(activity);
            backendActivity = activity;
            UpdateActivity();
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
            await FlushScenes();
            CheckDiary(data);
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

    private async Task OpenPage(string page, string day = "")
    {
        try { if (day == "") await Request("/open", new { page }); else await Request("/open", new { page, day }); }
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
    public void OpenDiary() { peek?.ClearNotice(); _ = OpenPage("diary", diaryDay); }
    private void CheckDiary(JsonElement data)
    {
        if (!data.TryGetProperty("diary", out var diary)) return;
        bool ready = diary.TryGetProperty("phase", out var phase) && phase.GetString() == "ready";
        string job = ready && diary.TryGetProperty("job_id", out var id) ? id.GetString() ?? "" : "";
        if (!diarySnapshot) { diarySnapshot = true; diaryJob = job; return; }
        if (!ready || job == "" || job == diaryJob || !backendReady) return;
        diaryJob = job; diaryDay = diary.GetProperty("day").GetString() ?? "";
        string title = diary.TryGetProperty("title", out var value) ? value.GetString() ?? "今天的日记" : "今天的日记";
        if (IsVisible) { ShowBubble("日记写好啦：" + title + "。点这里翻开。"); bubbleAction = OpenDiary; }
        peek?.DiaryReady(title);
    }
    public void OpenChat() { if (quitting) return; Chat.Show(); Chat.Activate(); }
    private void Quit() { Disable(); quitting = true; Close(); }
    private void HideCompletely() { scenes.CancelRice(); FreezePetMovement(); visuals.Suspend(); Hide(); peek?.Hide(); Chat.Hide(); bubblePopup.IsOpen = false; media.Stop(); media.Quiet = true; }
    private void ToggleVisible()
    {
        if (!IsVisible) { RestoreFromPeek(); return; }
        HideCompletely();
        peek ??= new PeekWindow(this, visuals.Portrait);
        var area = SystemParameters.WorkArea;
        peek.Left = Math.Clamp(Left + Width / 2 - peek.Width / 2, area.Left, area.Right - peek.Width);
        peek.Top = Math.Clamp(Top + stage.Height - peek.Height, area.Top, area.Bottom - peek.Height);
        peek.Show();
        if (!visuals.ReducedMotion) peek.BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromSeconds(.2)));
    }
    public void RestoreFromPeek()
    {
        if (quitting) return;
        if (peek?.IsVisible is true) { Left = peek.Left + peek.Width / 2 - Width / 2; Top = peek.Top + peek.Height - stage.Height; peek.Hide(); }
        Show(); media.Quiet = preferences.Quiet; KeepOnScreen(); SavePreferences();
        if (!visuals.ReducedMotion) BeginAnimation(UIElement.OpacityProperty, new DoubleAnimation(0, 1, TimeSpan.FromSeconds(.18)));
    }
    private void Walk()
    {
        if (pet is null || preferences.Quiet || pet.isPress || scenes.Feeding) return;
        if (pet.DisplayType.Type == GraphInfo.GraphType.Default) { scenes.StartWalk(); pet.DisplayMove(); }
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
            item.NormalizeSize();
            if (item.Left.HasValue && !double.IsFinite(item.Left.Value)) item.Left = null;
            if (item.Top.HasValue && !double.IsFinite(item.Top.Value)) item.Top = null;
            return item;
        }
        catch (Exception ex) when (ex is IOException or JsonException) { var fallback = new WindowPreferences(); fallback.NormalizeSize(); return fallback; }
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

    public void SetSizePercent(double value)
    {
        double bottom = Top + stage.Height, center = Left + Width / 2;
        preferences.SizePercent = Math.Clamp(value, 50, 200);
        zoom = preferences.Zoom = preferences.BaseZoom!.Value * preferences.SizePercent / 100;
        stage.Width = stage.Height = 500 * zoom;
        Width = Math.Max(180, stage.Width + 30); Height = stage.Height + 42;
        Left = center - Width / 2; Top = bottom - stage.Height;
        KeepOnScreen(); SavePreferences();
    }
    public double PetZoom => zoom;
    public double StageHeight => stage.Height;
    public bool QuietMode => preferences.Quiet;
    public bool DailyActivities => preferences.DailyActivities;
    public bool PetPressed => pet?.isPress is true;
    public void UpdateActivity()
    {
        bool taskActive = backendActivity.StartsWith("正在") && backendActivity != "正在休息" || backendActivity.Contains("等待准备或发送");
        status.Text = backendReady ? media.Activity ?? (scenes.Feeding ? scenes.Activity : taskActive ? backendActivity : scenes.Activity ?? backendActivity) : "大肥鱼正在休息";
    }
    public Vector HandOffset => new((Width - stage.Width) / 2 + stage.Width * .52, stage.Height * .58);
    public Rect MovementArea => new(SystemParameters.WorkArea.Left, SystemParameters.WorkArea.Top,
        Math.Max(0, SystemParameters.WorkArea.Width - Width), Math.Max(0, SystemParameters.WorkArea.Height - Height));
    public void MoveTo(Point point) { Left = point.X; Top = point.Y; KeepOnScreen(); }
    public void FreezePetMovement() { pet?.MoveTimer.Stop(); }
    public SceneStamp? SceneIdentity => backendReady && generation.HasValue && scope is not null ? new(scope, generation.Value) : null;
    public void RecordScene(string kind, SceneStamp? identity)
    {
        if (quitting || identity is null) return;
        sceneEvents.Enqueue(new(Guid.NewGuid().ToString("N"), kind, identity.Value));
        _ = FlushScenes();
    }
    private async Task FlushScenes()
    {
        if (recordingScenes || !backendReady || quitting) return;
        recordingScenes = true;
        try
        {
            while (sceneEvents.Count > 0 && !quitting)
            {
                var item = sceneEvents.Peek();
                if (item.Stamp.Scope != scope || item.Stamp.Generation != generation) { sceneEvents.Dequeue(); continue; }
                await Request("/scene", new { id = item.Id, kind = item.Kind, scope = item.Stamp.Scope, generation = item.Stamp.Generation });
                if (sceneEvents.Count > 0 && sceneEvents.Peek().Id == item.Id) sceneEvents.Dequeue();
            }
        }
        catch (Exception) { /* The same event IDs retry after the next status refresh. */ }
        finally { recordingScenes = false; }
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
    public double? BaseZoom { get; set; }
    public double SizePercent { get; set; } = 100;
    public bool Quiet { get; set; }
    public bool Wander { get; set; } = true;
    public bool DailyActivities { get; set; } = true;
    public bool ReducedMotion { get; set; }
    public void NormalizeSize()
    {
        if (!double.IsFinite(Zoom) || Zoom <= 0) Zoom = .52;
        if (!BaseZoom.HasValue || !double.IsFinite(BaseZoom.Value) || BaseZoom <= 0)
        {
            BaseZoom = Math.Clamp(Zoom, .2, 1.04); SizePercent = 100;
        }
        if (!double.IsFinite(SizePercent)) SizePercent = 100;
        SizePercent = Math.Clamp(SizePercent, 50, 200);
        Zoom = BaseZoom.Value * SizePercent / 100;
    }
}

public readonly record struct SceneStamp(string Scope, long Generation);
public readonly record struct SceneEvent(string Id, string Kind, SceneStamp Stamp);
