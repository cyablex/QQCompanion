using System.IO;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
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
            app.Run(new PetWindow(root));
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
    private readonly DispatcherTimer poll = new() { Interval = TimeSpan.FromSeconds(4) };
    private readonly DispatcherTimer bubbleTimer = new() { Interval = TimeSpan.FromSeconds(12) };
    private readonly Border bubbleBox;
    private Main? pet;
    private bool loading, interacting, quitting, polling;
    private DateTime lastInteraction;
    private double zoom = .52;
    public bool RePositionActive { get; set; }
    public bool EnableFunction => false;
    public int InteractionCycle => 100;
    public int PressLength => 450;
    public double ZoomRatio => zoom;

    public PetWindow(string root)
    {
        this.root = root;
        Title = "大肥鱼桌宠";
        WindowStyle = WindowStyle.None;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        ResizeMode = ResizeMode.NoResize;
        ShowInTaskbar = false;
        Topmost = true;
        Width = 310; Height = 400;
        var area = SystemParameters.WorkArea;
        Left = area.Right - Width - 30; Top = area.Bottom - Height - 20;
        var stack = new StackPanel();
        bubbleBox = new Border { Background = Brushes.White, CornerRadius = new CornerRadius(12), Padding = new Thickness(12),
            Margin = new Thickness(12, 0, 12, 4), Child = bubble, Visibility = Visibility.Collapsed };
        stack.Children.Add(bubbleBox);
        var stage = new Grid { Height = 500 * zoom, Width = 500 * zoom };
        stack.Children.Add(stage);
        stack.Children.Add(new Border { Child = status, Background = Brushes.WhiteSmoke, CornerRadius = new CornerRadius(8), Padding = new Thickness(7, 4, 7, 4), Margin = new Thickness(20, 3, 20, 3) });
        Content = stack;
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", File.ReadAllText(Path.Combine(root, "config", "vpet-bridge.token")).Trim());
        var menu = new ContextMenu();
        AddMenu(menu, "摸摸头", () => pet?.DisplayTouchHead());
        AddMenu(menu, "戳一下", () => pet?.DisplayTouchBody());
        menu.Items.Add(new Separator());
        AddMenu(menu, "打开共同经历", () => _ = OpenPage("creative"));
        AddMenu(menu, "打开日历", () => _ = OpenPage("calendar"));
        AddMenu(menu, "打开曲库", () => _ = OpenPage("songbook"));
        menu.Items.Add(new Separator());
        foreach (double size in new[] { .4, .52, .64 })
        {
            double ratio = size;
            AddMenu(menu, $"大小 {(int)(ratio * 500)}", () => { zoom = ratio; stage.Width = stage.Height = 500 * zoom; Width = Math.Max(280, stage.Width + 30); Height = stage.Height + 140; KeepOnScreen(); });
        }
        AddMenu(menu, "关于演示素材", () => ShowBubble("当前动作素材来自 VPet 原作者，角色外观尚未替换。大肥鱼的人设与记忆由原后台提供。"));
        AddMenu(menu, "退出桌宠", () => { Disable(); quitting = true; Close(); });
        PreviewMouseRightButtonDown += (_, e) => { menu.PlacementTarget = this; menu.IsOpen = true; e.Handled = true; };
        poll.Tick += async (_, _) => await Refresh();
        bubbleTimer.Tick += (_, _) => { bubbleBox.Visibility = Visibility.Collapsed; bubbleTimer.Stop(); };
        Loaded += async (_, _) => {
            try
            {
                loading = true;
                GraphCore.CachePath = Path.Combine(root, "cache", "vpet");
                var assets = Path.Combine(root, "vpet", "assets");
                var loader = new PetLoader(new LpsDocument(File.ReadAllText(Path.Combine(assets, "vup.lps"))), new DirectoryInfo(assets));
                var core = new GameCore { Controller = this, Save = new GameSave("大肥鱼"), Graph = loader.Graph(500, Dispatcher) };
                pet = new Main(core) { NoFunctionMOD = IGameSave.ModeType.Nomal };
                core.Save.Mode = IGameSave.ModeType.Nomal;
                stage.Children.Add(pet);
                pet.Load_0_BaseConsole();
                pet.Load_2_TouchEvent();
                await pet.Load_2_WaitGraph();
                pet.EventTimer.Stop(); // Autonomous status/AI stays in the companion backend.
                pet.Load_3_BindingTimer();
                pet.Load_4_Start();
                pet.Event_TouchHead += () => Dispatcher.InvokeAsync(() => _ = Interact("head"));
                pet.Event_TouchBody += () => Dispatcher.InvokeAsync(() => _ = Interact("body"));
                pet.GraphDisplayHandler += info => {
                    if (info.Type.ToString().StartsWith("Raised"))
                        Dispatcher.InvokeAsync(() => _ = Interact("lift"));
                };
                pet.ToolBar.Visibility = Visibility.Collapsed;
                loading = false;
                poll.Start(); await Refresh();
            }
            catch (Exception ex) { status.Text = "桌宠加载失败，请查看日志"; File.AppendAllText(Path.Combine(root, "logs", "vpet-host.log"), ex + Environment.NewLine); }
        };
        Closed += (_, _) => { poll.Stop(); bubbleTimer.Stop(); pet?.Dispose(); client.Dispose(); if (!quitting) Disable(); };
    }

    private static void AddMenu(ContextMenu menu, string title, Action action)
    {
        var item = new MenuItem { Header = title };
        item.Click += (_, _) => action(); menu.Items.Add(item);
    }

    private void ShowBubble(string text)
    {
        bubble.Text = text; bubbleBox.Visibility = Visibility.Visible;
        bubbleTimer.Stop(); bubbleTimer.Start();
    }

    private async Task<JsonElement> Request(string path, object? data = null)
    {
        using var response = data is null ? await client.GetAsync(path) : await client.PostAsync(path,
            new StringContent(JsonSerializer.Serialize(data), Encoding.UTF8, "application/json"));
        response.EnsureSuccessStatusCode();
        using var json = JsonDocument.Parse(await response.Content.ReadAsStringAsync());
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
            status.Text = data.GetProperty("ready").GetBoolean() ? data.GetProperty("activity").GetString() : "大肥鱼正在休息";
            status.ToolTip = data.GetProperty("project").GetString() + "\n" + data.GetProperty("next_step").GetString();
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
