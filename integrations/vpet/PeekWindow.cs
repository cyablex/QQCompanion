using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using Window = System.Windows.Window;
using Point = System.Windows.Point;
using Image = System.Windows.Controls.Image;
using Brushes = System.Windows.Media.Brushes;

namespace DafeiyuPet;

public sealed class PeekWindow : Window
{
    private Point start, origin;
    private bool held, moved;
    private readonly TextBlock badge = new() { Text = "●", Foreground = Brushes.CornflowerBlue, HorizontalAlignment = System.Windows.HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top, Visibility = Visibility.Collapsed };
    public PeekWindow(PetWindow pet, ImageSource? portrait)
    {
        Title = "大肥鱼 · 潜水中"; Width = 84; Height = 75;
        WindowStyle = WindowStyle.None; AllowsTransparency = true; Background = Brushes.Transparent;
        ShowInTaskbar = false; ShowActivated = false; Topmost = true; ResizeMode = ResizeMode.NoResize;
        var grid = new Grid();
        grid.Children.Add(new Border { Background = new SolidColorBrush(System.Windows.Media.Color.FromArgb(232, 241, 248, 253)), CornerRadius = new CornerRadius(32,32,19,19), BorderBrush = Brushes.LightSteelBlue, BorderThickness = new Thickness(1), Margin = new Thickness(5, 10, 5, 0) });
        grid.Children.Add(new Image { Source = portrait, Stretch = Stretch.Uniform, Margin = new Thickness(7,0,7,13) });
        grid.Children.Add(new TextBlock { Text = "﹏﹏", Foreground = Brushes.SteelBlue, FontSize = 21, HorizontalAlignment = System.Windows.HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Bottom });
        grid.Children.Add(badge); Content = grid; Cursor = System.Windows.Input.Cursors.Hand;
        ToolTip = "大肥鱼在这里。点击叫她出来，拖动可以换位置。";
        MouseLeftButtonDown += (_, e) => { held = true; moved = false; start = e.GetPosition(this); origin = new Point(Left, Top); CaptureMouse(); e.Handled = true; };
        MouseMove += (_, e) => {
            if (!held) return;
            var point = PointToScreen(e.GetPosition(this));
            point = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformFromDevice.Transform(point) ?? point;
            var area = SystemParameters.WorkArea;
            Left = Math.Clamp(point.X-start.X, area.Left, area.Right-Width); Top = Math.Clamp(point.Y-start.Y, area.Top, area.Bottom-Height);
            moved |= (new Point(Left,Top)-origin).Length > 4;
        };
        MouseLeftButtonUp += (_, e) => { if (!held) return; held = false; ReleaseMouseCapture(); if (!moved) pet.RestoreFromPeek(); e.Handled = true; };
        LostMouseCapture += (_, _) => held = false;
        var menu = new ContextMenu();
        var restore = new MenuItem { Header = "叫大肥鱼出来" }; restore.Click += (_, _) => pet.RestoreFromPeek(); menu.Items.Add(restore);
        var diary = new MenuItem { Header = "打开鲸鱼日记" }; diary.Click += (_, _) => pet.OpenDiary(); menu.Items.Add(diary);
        ContextMenu = menu;
    }
    public void DiaryReady(string title) { badge.Visibility = Visibility.Visible; ToolTip = "日记写好啦：" + title + "。右键可以打开。"; }
    public void ClearNotice() { badge.Visibility = Visibility.Collapsed; ToolTip = "大肥鱼在这里。点击叫她出来。"; }
}
