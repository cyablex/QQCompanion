using System.Windows;
using System.Windows.Input;
using System.Windows.Media;
using Window = System.Windows.Window;
using Point = System.Windows.Point;
using Brushes = System.Windows.Media.Brushes;
using Color = System.Windows.Media.Color;
using Pen = System.Windows.Media.Pen;
using Cursors = System.Windows.Input.Cursors;

namespace DafeiyuPet;

public sealed class RiceWindow : Window
{
    private bool dragging;
    private Point offset;
    public Action? PickedUp, Dropped;
    public Action<Point>? Dragged;
    public RiceWindow(Window owner)
    {
        Owner = owner; Title = "大肥鱼的白米饭"; WindowStyle = WindowStyle.None;
        AllowsTransparency = true; Background = Brushes.Transparent; ShowInTaskbar = false;
        ShowActivated = false; Topmost = true; ResizeMode = ResizeMode.NoResize;
        Width = 82; Height = 72; Content = new RiceDrawing(); Cursor = Cursors.Hand;
        MouseLeftButtonDown += (_, e) => { dragging = true; offset = e.GetPosition(this); CaptureMouse(); PickedUp?.Invoke(); e.Handled = true; };
        MouseMove += (_, e) => {
            if (!dragging) return;
            var source = PresentationSource.FromVisual(this);
            var screen = PointToScreen(e.GetPosition(this));
            var dip = source?.CompositionTarget?.TransformFromDevice.Transform(screen) ?? screen;
            Left = dip.X - offset.X; Top = dip.Y - offset.Y;
            Dragged?.Invoke(Center); e.Handled = true;
        };
        MouseLeftButtonUp += (_, e) => { EndDrag(); e.Handled = true; };
        LostMouseCapture += (_, _) => EndDrag();
    }
    public Point Center => new(Left + Width * .5, Top + Height * .5);
    public void CenterAt(Point point) { Left = point.X - Width * .5; Top = point.Y - Height * .5; }
    private void EndDrag() { if (!dragging) return; dragging = false; ReleaseMouseCapture(); Dropped?.Invoke(); }
}

public sealed class RiceDrawing : FrameworkElement
{
    protected override void OnRender(DrawingContext dc)
    {
        double sx = ActualWidth / 82, sy = ActualHeight / 72;
        dc.PushTransform(new ScaleTransform(sx, sy));
        var edge = new Pen(new SolidColorBrush(Color.FromRgb(83, 130, 159)), 1.6);
        dc.DrawEllipse(new SolidColorBrush(Color.FromArgb(25, 0, 0, 0)), null, new Point(41, 64), 28, 5);
        var bowl = new StreamGeometry();
        using (var p = bowl.Open()) { p.BeginFigure(new Point(9, 32), true, true); p.BezierTo(new Point(12, 70), new Point(70, 70), new Point(73, 32), true, false); }
        dc.DrawGeometry(Brushes.AliceBlue, edge, bowl);
        dc.DrawEllipse(Brushes.White, edge, new Point(41, 30), 32, 11);
        dc.DrawEllipse(Brushes.White, null, new Point(41, 23), 26, 14);
        var grain = new SolidColorBrush(Color.FromRgb(225, 231, 227));
        for (int row = 0; row < 4; row++)
            for (int column = 0; column < 8 - Math.Abs(row - 2); column++)
                dc.DrawEllipse(Brushes.White, new Pen(grain, .7), new Point(19 + column * 6 + (row % 2) * 2, 15 + row * 5), 3.5, 2);
        dc.DrawEllipse(new SolidColorBrush(Color.FromRgb(84, 149, 201)), null, new Point(41, 48), 9, 5);
        dc.DrawEllipse(Brushes.White, null, new Point(45, 47), 1, 1);
        dc.Pop();
    }
}
