using System.Windows;
using Point = System.Windows.Point;

namespace DafeiyuPet;

public enum RicePhase { Placed, Chasing, Carried, Eating, Finished, Cancelled }

// Geometry and state are independent of mouse APIs, windows, or model calls.
public sealed class RiceModel
{
    public RicePhase Phase { get; private set; } = RicePhase.Placed;
    public Point Food { get; private set; }
    public bool Held { get; private set; }
    public bool Caught { get; private set; }
    public double EatingSeconds { get; private set; }
    public RiceModel(Point food) { Food = food; }
    public void MoveFood(Point food) { Food = food; }
    public void BeginDrag()
    {
        if (Phase is RicePhase.Eating or RicePhase.Finished or RicePhase.Cancelled) return;
        Held = true;
        if (Phase == RicePhase.Placed) Phase = RicePhase.Chasing;
    }
    public void Release()
    {
        Held = false;
        if (Phase == RicePhase.Carried) Phase = RicePhase.Eating;
    }
    public Point Step(Point pet, Vector hand, Rect allowed, double seconds, double speed = 80)
    {
        seconds = Math.Clamp(seconds, 0, .1);
        if (Phase == RicePhase.Chasing)
        {
            // A released bowl stays within reach of the pet's hands at screen edges.
            if (!Held) Food = Clamp(Food, new Rect(allowed.TopLeft + hand, allowed.Size));
            var target = Food - hand;
            var delta = target - pet;
            double distance = delta.Length;
            if (distance > 0) pet += delta * (Math.Min(distance, speed * seconds) / distance);
            pet = Clamp(pet, allowed);
            if ((Food - (pet + hand)).Length <= 18)
            {
                Caught = true;
                Phase = Held ? RicePhase.Carried : RicePhase.Eating;
            }
        }
        if (Phase == RicePhase.Carried)
        {
            pet = Clamp(Food - hand, allowed);
            Food = pet + hand;
        }
        if (Phase == RicePhase.Eating)
        {
            EatingSeconds += seconds;
            if (EatingSeconds >= 6.5) Phase = RicePhase.Finished;
        }
        return pet;
    }
    public void Cancel() { Held = false; Phase = RicePhase.Cancelled; }
    private static Point Clamp(Point point, Rect area) => new(
        Math.Clamp(point.X, area.Left, area.Right), Math.Clamp(point.Y, area.Top, area.Bottom));
}
