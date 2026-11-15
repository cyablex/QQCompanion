"""Connected whale silhouettes for the local sketch renderer."""

import math


MOODS = ("开心", "好奇", "专注", "平静", "低落", "困倦")
VIEWS = ("side", "angled", "front")
POSES = ("level", "up", "down", "curl")

# Coordinates in each drawing's own space. Each view places its face and fins
# on its own silhouette. The head is part of the body curve.
SIDE = [
    [240, 480], [180, 390], [90, 390], [50, 280], [40, 420], [110, 520], [160, 560],
    [90, 550], [35, 600], [40, 700], [120, 710], [200, 650], [250, 630],
    [340, 830], [780, 810], [910, 610], [990, 350], [760, 180], [520, 270],
    [360, 300], [260, 370], [240, 480],
]
ANGLED = [
    [330, 452], [260, 402], [184, 432], [127, 335], [125, 412], [166, 473], [205, 493],
    [157, 510], [126, 565], [154, 616], [224, 597], [263, 557], [317, 553],
    [330, 695], [487, 802], [662, 782], [854, 762], [938, 643], [899, 490],
    [855, 326], [702, 287], [560, 312], [435, 333], [396, 408], [330, 452],
]
FRONT = [
    [500, 337], [680, 337], [789, 441], [783, 585], [778, 732], [651, 808], [500, 800],
    [349, 808], [222, 732], [217, 585], [211, 441], [320, 337], [500, 337],
]
CURLED = [
    [353, 486], [420, 386], [579, 356], [705, 400], [831, 440], [885, 563], [825, 663],
    [757, 780], [566, 816], [420, 754], [310, 710], [278, 596], [353, 486],
]

CURLED_TAIL = [
    [413, 674], [276, 648], [231, 546], [264, 442], [291, 356], [344, 329], [380, 291],
    [329, 278], [296, 236], [321, 187], [368, 188], [405, 213], [427, 248],
    [452, 205], [503, 193], [549, 212], [533, 275], [464, 292], [433, 326],
    [384, 380], [353, 430], [378, 504], [385, 556], [418, 592], [475, 604],
]
FRONT_TAIL = [
    [455, 425], [465, 369], [453, 318], [419, 294], [370, 290], [320, 256], [313, 197],
    [390, 203], [454, 216], [500, 249], [545, 216], [610, 203], [687, 197],
    [680, 256], [630, 290], [581, 294], [547, 318], [535, 369], [545, 425],
]


def curve(points, fill=None, stroke=None, *, width=3, closed=False, shadow=False, fill2=None):
    return dict(type="bezier", points=points, closed=closed, fill=fill, fill2=fill2,
                stroke=stroke, shadow=shadow, width=width)


def face(eyes, mouth, mood, ink):
    parts = []
    for x, y, size in eyes:
        if mood in {"开心", "困倦", "平静"}:
            bow = -15 if mood == "开心" else 11
            points = [[x - size, y], [x - size / 2, y + bow], [x + size / 2, y + bow], [x + size, y]]
            parts.append(curve(points, stroke=ink, width=4))
        else:
            height = size if mood == "好奇" else size * .82
            parts.append(dict(type="ellipse", box=[x - size * .68, y - height, x + size * .68, y + height],
                              fill="#253c55", stroke=None, shadow=False))
            parts.append(dict(type="ellipse", box=[x - 6, y - height * .74, x + 3, y - height * .22],
                              fill="#ffffff", stroke=None, shadow=False))
    x, y = mouth
    if mood == "好奇":
        parts.append(dict(type="ellipse", box=[x - 12, y - 8, x + 12, y + 12], fill=None, stroke=ink, width=3))
    else:
        smile = {"开心": 34, "低落": -13, "专注": 3}.get(mood, 13)
        span = 36 if mood == "开心" else 25
        parts.append(curve([[x - span, y], [x - 14, y + smile], [x + 15, y + smile], [x + span, y]],
                           stroke=ink))
    return parts


def whale_parts(item):
    """Draw the requested view. Curves and features share one coordinate space."""
    facing, mood = item.get("facing", "right"), item.get("mood", "平静")
    shape, pose = item.get("shape", "round"), item.get("pose", "level")
    view, fin = item.get("view", "side"), item.get("fin", "relaxed")
    if (facing not in ("left", "right") or mood not in MOODS or shape not in ("round", "slender")
            or pose not in POSES or view not in VIEWS or fin not in ("relaxed", "wave")):
        raise ValueError("Invalid whale pose or view")

    blue, lower = item.get("fill", "#75bdea"), item.get("fill2", "#469dce")
    ink = item.get("stroke", "#30465c")
    behind = []
    if view == "front":
        body = FRONT
        behind.append(curve(CURLED_TAIL if pose == "curl" else FRONT_TAIL, blue, ink, closed=True, fill2=lower))
        left_fin = [[306, 568], [239, 581], [182, 664], [168, 730], [234, 738], [308, 691], [335, 650]]
        right_fin = [[1000 - x, y] for x, y in left_fin]
        if fin == "wave":
            left_fin = [[316, 574], [239, 538], [182, 475], [167, 425], [136, 503], [174, 598], [291, 644]]
        behind.extend(curve(points, blue, ink, closed=True, fill2=lower) for points in (left_fin, right_fin))
        belly = [[277, 640], [387, 699], [613, 699], [723, 640], [646, 822], [354, 822], [277, 640]]
        chest_fin = None
        eyes, mouth = [(380, 550, 22), (620, 550, 22)], (500, 602)
        cheeks = [(333, 592), (667, 592)]
    elif pose == "curl":
        body = CURLED
        behind.append(curve(CURLED_TAIL, blue, ink, closed=True, fill2=lower))
        belly = [[368, 672], [461, 752], [707, 754], [800, 645], [740, 793], [479, 824], [368, 672]]
        chest_fin = [[486, 615], [480, 660], [509, 714], [559, 726], [600, 701], [590, 639], [567, 602]]
        eyes, mouth = ([(654, 538, 21), (770, 532, 15)], (723, 589)) if view == "angled" else ([(723, 533, 21)], (768, 590))
        cheeks = [(790, 565)]
    elif view == "angled":
        body = ANGLED
        belly = [[383, 638], [493, 749], [751, 740], [873, 610], [811, 797], [522, 837], [383, 638]]
        chest_fin = [[525, 608], [458, 637], [423, 704], [430, 745], [492, 752], [579, 692], [611, 626]]
        eyes, mouth = [(651, 504, 22), (810, 480, 17)], (752, 566)
        cheeks = [(612, 553), (833, 518)]
    else:
        body = SIDE
        belly = [[300, 620], [450, 780], [770, 760], [870, 590], [720, 680], [450, 690], [300, 620]]
        chest_fin = [[550, 590], [580, 660], [600, 740], [680, 745], [705, 680], [660, 610], [620, 575]]
        eyes, mouth = [(767, 457, 22)], (809, 532)
        cheeks = [(815, 494)]

    parts = behind + [curve(body, blue, ink, closed=True, shadow=True, fill2=lower),
                      curve(belly, "#e7f5fc", closed=True, fill2="#cce8f7")]
    if chest_fin:
        if fin == "wave":
            tip = chest_fin[0][0] - 100
            start, end = chest_fin[0], chest_fin[-1]
            chest_fin = [start, [tip + 40, 548], [tip - 26, 467], [tip, 424], [tip + 56, 443], [end[0] - 57, 490], end]
        parts.append(curve(chest_fin, blue, ink, closed=True, fill2=lower))
    parts.extend(dict(type="ellipse", box=[x - 23, y - 9, x + 23, y + 9], fill="#efaeba", stroke=None, shadow=False)
                 for x, y in cheeks)
    parts.extend(face(eyes, mouth, mood, ink))
    return parts


def place_whale(item, box):
    """Fit the full drawing in the scene box without stretching its proportions."""
    parts = whale_parts(item)
    angle = {"up": -.42, "down": .44}.get(item.get("pose"), 0)
    if item.get("view") == "front":
        angle *= .5
    cosine, sine = math.cos(angle), math.sin(angle)
    height = .82 if item.get("shape") == "slender" else 1
    direction = -1 if item.get("facing") == "left" else 1

    def turn(point):
        x, y = point[0] - 500, (point[1] - 500) * height
        return (direction * (x * cosine - y * sine), x * sine + y * cosine)

    limits = []
    for part in parts:
        if "points" in part:
            part["points"] = [turn(point) for point in part["points"]]
            limits.extend(part["points"])
        else:
            x1, y1, x2, y2 = part["box"]
            cx, cy = turn(((x1 + x2) / 2, (y1 + y2) / 2))
            dx, dy = (x2 - x1) / 2, (y2 - y1) * height / 2
            part["box"] = [cx - dx, cy - dy, cx + dx, cy + dy]
            limits.extend([(cx - dx, cy - dy), (cx + dx, cy + dy)])

    xs, ys = zip(*limits)
    left, top, right, bottom = box
    # Scene coordinates are normalized, but the rendered canvas is 960×640.
    scale = min((right - left) * .96 / (max(xs) - min(xs)), (bottom - top) * .64 / (max(ys) - min(ys)))
    cx, cy = (left + right) / 2, (top + bottom) / 2
    bx, by = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    def position(x, y):
        # Avoid floating-point roundoff at an exact canvas edge.
        return [min(right, max(left, cx + (x - bx) * scale / .96)),
                min(bottom, max(top, cy + (y - by) * scale / .64))]

    for part in parts:
        if "points" in part:
            part["points"] = [position(x, y) for x, y in part["points"]]
        else:
            x1, y1, x2, y2 = part["box"]
            part["box"] = position(x1, y1) + position(x2, y2)
    return parts
