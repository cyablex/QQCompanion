using System.Diagnostics;
using System.Windows;
using System.Windows.Threading;
using Point = System.Windows.Point;

namespace DafeiyuPet;

public sealed class PetScenes : IDisposable
{
    private readonly PetWindow owner;
    private readonly PetVisuals visuals;
    private readonly DispatcherTimer timer = new() { Interval = TimeSpan.FromMilliseconds(40) };
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private double last, until, reactionUntil, manualUntil;
    private string idle = "idle", backend = "";
    private string reactionPose = "head";
    private double previousLeft;
    private RiceWindow? bowl;
    private RiceModel? rice;
    private SceneStamp? stamp;
    private bool recordedCatch, disposed;
    private bool liftedWhilePressed;
    public RicePhase? RicePhase => rice?.Phase;
    public bool Feeding => rice is not null;
    public bool CanWander => !Feeding && !backend.StartsWith("正在") && idle == "idle" && clock.Elapsed.TotalSeconds >= manualUntil;
    private static string? PoseActivity(string pose) => pose switch {
        "read" => "翻翻手边的书 · 陪伴动作", "draw" => "随手涂几笔 · 陪伴动作",
        "stretch" => "伸个懒腰", "hum" => "晃晃身子", "look" => "看看四周",
        "yawn" => "打个哈欠", "greeting" => "提裙打个招呼", _ => null
    };
    private string? activity;
    public string? Activity { get => activity; private set { if (activity == value) return; activity = value; owner.UpdateActivity(); } }
    public PetScenes(PetWindow owner, PetVisuals visuals)
    {
        this.owner = owner; this.visuals = visuals;
        timer.Tick += (_, _) => Tick(); timer.Start();
    }
    public void Backend(string value) { backend = value; }
    public void React(string kind = "head") { reactionPose = !visuals.VideoReady && kind != "lift" && visuals.CurrentPose is "read" or "write" or "rest" ? visuals.CurrentPose : kind; liftedWhilePressed = kind == "lift" && owner.PetPressed; reactionUntil = clock.Elapsed.TotalSeconds + visuals.ReactionDuration(kind); visuals.React(kind, clock.Elapsed.TotalSeconds); }
    public void StartWalk() { idle = "idle"; manualUntil = 0; until = clock.Elapsed.TotalSeconds + 12; }
    public void Choose(string pose)
    {
        if (Feeding) return;
        owner.FreezePetMovement(); idle = pose;
        until = clock.Elapsed.TotalSeconds + (pose is "stretch" or "greeting" ? 10 : 25);
        manualUntil = until;
    }
    public void SpawnRice()
    {
        if (disposed || !owner.IsVisible) return;
        if (bowl is not null) { bowl.Activate(); return; }
        owner.FreezePetMovement(); stamp = owner.SceneIdentity; recordedCatch = false;
        bowl = new RiceWindow(owner);
        bowl.Width = Math.Clamp(80 * owner.PetZoom / .52, 50, 115); bowl.Height = bowl.Width * .88;
        var location = new Point(owner.Left + owner.Width + bowl.Width * .5 + 8, owner.Top + owner.StageHeight * .7);
        var area = SystemParameters.WorkArea;
        location.X = Math.Clamp(location.X, area.Left + bowl.Width * .5, area.Right - bowl.Width * .5);
        location.Y = Math.Clamp(location.Y, area.Top + bowl.Height * .5, area.Bottom - bowl.Height * .5);
        bowl.CenterAt(location); rice = new RiceModel(location) { EatingDuration = visuals.VideoReady ? 241d / 24 : 6.5 };
        bowl.PickedUp = () => rice?.BeginDrag();
        bowl.Dragged = point => rice?.MoveFood(point);
        bowl.Dropped = () => rice?.Release();
        bowl.Show(); owner.RecordScene("rice_placed", stamp);
    }
    public void CancelRice()
    {
        var window = bowl; bowl = null; rice?.Cancel(); rice = null; stamp = null;
        window?.Close(); Activity = null;
    }
    private void Tick()
    {
        if (disposed) return;
        double now = clock.Elapsed.TotalSeconds, dt = Math.Min(.1, Math.Max(0, now - last)); last = now;
        double movement = owner.Left - previousLeft; previousLeft = owner.Left;
        if (!owner.IsVisible || owner.WindowState == WindowState.Minimized) { visuals.Suspend(); return; }
        if (rice is not null && bowl is not null)
        {
            owner.FreezePetMovement();
            var position = rice.Step(new Point(owner.Left, owner.Top), owner.HandOffset, owner.MovementArea, dt,
                                    Math.Clamp(80 * owner.PetZoom / .52, 40, 110));
            owner.MoveTo(position);
            if (rice.Phase == DafeiyuPet.RicePhase.Carried || (rice.Phase == DafeiyuPet.RicePhase.Chasing && !rice.Held)) bowl.CenterAt(rice.Food);
            if (rice.Caught && !recordedCatch) { recordedCatch = true; owner.RecordScene("rice_caught", stamp); }
            if (rice.Phase == DafeiyuPet.RicePhase.Eating)
            {
                if (bowl.IsVisible) bowl.Hide();
                Activity = "抱着白米饭开吃"; visuals.Show("eat", now);
            }
            else if (rice.Phase == DafeiyuPet.RicePhase.Finished)
            {
                owner.RecordScene("rice_finished", stamp); CancelRice();
                reactionUntil = now + 2; owner.Note("白米饭吃完啦。🐟");
                visuals.React("head", now);
                reactionPose = "head";
            }
            else if (rice.Phase == DafeiyuPet.RicePhase.Chasing)
            {
                Activity = "发现白米饭，慢慢追过来";
                string direction = rice.Food.X < owner.Left + owner.HandOffset.X ? "left" : "right";
                visuals.Show("walk_" + direction + ((int)(now * 5) % 2), now, true);
            }
            else
            {
                Activity = rice.Phase == DafeiyuPet.RicePhase.Carried ? "抓住饭碗不放" : "饭摆好啦，拿起来逗逗她";
                visuals.Show(rice.Phase == DafeiyuPet.RicePhase.Carried ? "excited" : "idle", now);
            }
            return;
        }
        if (reactionPose == "lift" && liftedWhilePressed && !owner.PetPressed) { liftedWhilePressed = false; reactionUntil = 0; }
        if (now < reactionUntil || reactionPose == "lift" && liftedWhilePressed && owner.PetPressed) { Activity = null; visuals.Show(reactionPose, now); return; }
        if (backend == "正在写日记") { Activity = null; owner.FreezePetMovement(); visuals.Show("write", now); return; }
        if (Math.Abs(movement) > .05 && !owner.PetPressed && !owner.QuietMode) { Activity = "散步一下"; visuals.Show("walk_" + (movement < 0 ? "left" : "right") + ((int)(now * 5) % 2), now, true); return; }
        if (manualUntil > 0)
        {
            if (now < manualUntil || !visuals.AtVideoBoundary) { Activity = PoseActivity(idle); visuals.Show(idle == "draw" ? "write" : idle, now); return; }
            manualUntil = 0;
        }
        if (owner.QuietMode) { Activity = "安静休息"; visuals.Show("rest", now); return; }
        if (owner.PetPressed) { Activity = null; visuals.Show("excited", now); return; }
        if (backend.StartsWith("正在画画")) { Activity = null; visuals.Show("write", now); return; }
        if (backend.StartsWith("正在") && backend != "正在休息") { Activity = null; visuals.Show("idle", now); return; }
        if (now >= until && visuals.AtVideoBoundary)
        {
            idle = owner.DailyActivities ? (visuals.VideoReady ? new[] { "idle", "idle", "hum", "look", "read", "draw", "stretch", "yawn" }[Random.Shared.Next(8)] : new[] { "idle", "idle", "read", "draw", "stretch" }[Random.Shared.Next(5)]) : "idle";
            until = now + (idle == "stretch" ? 5 : Random.Shared.Next(18, 38));
            if (idle != "idle") owner.FreezePetMovement();
        }
        Activity = PoseActivity(idle);
        visuals.Show(idle == "draw" ? "write" : idle, now);
    }
    public void Dispose() { if (disposed) return; disposed = true; timer.Stop(); CancelRice(); visuals.Dispose(); }
}
