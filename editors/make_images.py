"""生成仓里那几张图。

配色同一套 —— 深蓝底 + 白字, 亮色/暗色主题下都读得清。2 倍超采样再缩回,
免得字边发木。确定性: 没有随机数、没有时间戳, 重跑逐字节相同。

    python editors/make_images.py

产物:
    editors/loment-banner.png        README 顶部标题图
    editors/loment-pipeline.png      一个程序是怎么编出来的
    editors/loment-bootstrap.png     自举: 种子 -> stage1 -> stage2 -> stage3
    editors/loment-diagnostic.png    报错器渲染的一张说明卡 (真实输出)
    editors/loment-social.png        GitHub 社交预览卡 (1280x640)
    editors/loment-discord.png       Discord 服务器图标 (512x512, 按圆显示)

前四张进 README; 后两张不进 —— 它们上传到各自平台的设置里 (GitHub 的 Social preview,
Discord 的服务器设置), 留在仓里是为了**有源可重生成**。
"""
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageFilter

S = 2                                   # 超采样
BG_TOP = (13, 17, 40)
BG_BOT = (24, 31, 70)
PANEL = (26, 33, 72)
PANEL_EDGE = (58, 74, 140)
ACCENT = (108, 137, 253)
ACCENT_DK = (72, 100, 210)
WHITE = (255, 255, 255)
SOFT = (226, 232, 255)
DIM = (150, 164, 205)
GREEN = (126, 211, 154)
RED = (236, 126, 126)
CYAN = (120, 200, 226)
AMBER = (230, 190, 120)

UI = r"C:\Windows\Fonts\segoeui.ttf"
UIB = r"C:\Windows\Fonts\segoeuib.ttf"
MONO = r"C:\Windows\Fonts\consola.ttf"
MONOB = r"C:\Windows\Fonts\consolab.ttf"


def canvas(w, h):
    img = Image.new("RGB", (w * S, h * S), BG_TOP)
    d = ImageDraw.Draw(img)
    for y in range(h * S):
        t = y / (h * S - 1)
        d.line([(0, y), (w * S, y)],
               fill=tuple(round(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOT)))
    return img


def rc(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([box[0] * S, box[1] * S, box[2] * S, box[3] * S],
                        radius=r * S, fill=fill, outline=outline, width=width * S)


def txt(d, xy, s, font, fill, anchor="la"):
    d.text((xy[0] * S, xy[1] * S), s, font=font, fill=fill, anchor=anchor)


def font(path, size):
    return ImageFont.truetype(path, size * S)


def arrow(d, x1, x2, y, color=ACCENT, head=7):
    d.line([(x1 * S, y * S), ((x2 - head) * S, y * S)], fill=color, width=2 * S)
    d.polygon([((x2 - head * 1.9) * S, (y - head) * S), ((x2 - head * 1.9) * S, (y + head) * S),
               (x2 * S, y * S)], fill=color)


def save(img, w, h, path):
    img.resize((w, h), Image.LANCZOS).save(path, optimize=True)
    print("wrote", path)


# ---------------------------------------------------------------- 标题图

def banner():
    """标题图: 图标 + 三行白字 + 一层很淡的代码底纹。

    底纹只放**代码片段**, 不放任何"说法" (不写 no libc / 几个语法 之类) ——
    那些是会随 0.1.4 的计划变的断言, 钉进一张图里就等着过期。所以它只是装饰,
    压得很低 (离背景只差十几个色阶), 亮暗主题下都不抢字。
    """
    W, H = 1040, 280
    img = canvas(W, H).convert("RGBA")

    # 右边一栏代码, 左端渐隐 —— 看得见是代码, 但整栏都在文字区右侧
    code = [
        "module hello",
        "fn _start() {",
        '    let s: str = "hello\\n";',
        "    syscall4(1, 1, str_ptr(s) as u64);",
        "    syscall4(60, 0, 0, 0);",
        "}",
        "capability blk : disk[0..4]",
        "guard blk(i);",
        "pub extern fn c_add(a: i32, b: i32) -> i32;",
    ]
    tex = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    td = ImageDraw.Draw(tex)
    f_code = font(MONO, 14)
    y = 28
    for line in code:
        td.text((648 * S, y * S), line, font=f_code, fill=(104, 126, 212))
        y += 27

    mask = Image.new("L", (W * S, H * S), 0)
    md = ImageDraw.Draw(mask)
    for x in range(W * S):                      # 横向 ramp: 左边全透明, 右边全不透明
        t = min(1.0, max(0.0, (x / S - 596) / 150))
        md.line([(x, 0), (x, H * S)], fill=int(255 * t))
    tex.putalpha(mask)
    img = Image.alpha_composite(img, tex)

    glow = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        [(34 * S, 30 * S), (222 * S, 218 * S)], fill=(84, 115, 227, 74))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(23 * S)))

    icon = Image.open("editors/loment.ico")
    icon.size = (256, 256)
    icon = icon.convert("RGBA").resize((168 * S, 168 * S), Image.LANCZOS)
    img.paste(icon, (44 * S, 50 * S), icon)
    d = ImageDraw.Draw(img)

    # 文字块左侧那道竖条
    d.rounded_rectangle([242 * S, 64 * S, 247 * S, 202 * S], radius=2 * S, fill=ACCENT)

    X = 268
    txt(d, (X, 100), "Loment", font(UIB, 72), WHITE, anchor="lm")
    txt(d, (X + 2, 156), "Programming Language", font(UI, 28), (236, 240, 255), anchor="lm")
    txt(d, (X + 2, 191), "Program by Fujo", font(UI, 21), (186, 199, 238), anchor="lm")

    save(img.convert("RGB"), W, H, "editors/loment-banner.png")


# ---------------------------------------------------------------- 管线图

def pipeline():
    W, H = 1400, 330
    img = canvas(W, H)
    d = ImageDraw.Draw(img)

    f_title = font(UIB, 25)
    f_name = font(MONOB, 19)
    f_sub = font(UI, 14)

    txt(d, (44, 40), "How a program is built", f_title, WHITE)

    bx, by, bw, bh, gap = 44, 108, 232, 108, 48
    boxes = [
        ("hello.lomt", ["Loment source", "any of the six", "surface syntaxes"]),
        ("loment-driver", ["load  ·  check", "emit  LLVM IR", "written in Loment"]),
        ("hello.ll", ["LLVM IR", "text, not bytes", "the only handover"]),
        ("loment-lomelf", ["the linker,", "also in Loment", "no external ld"]),
        ("hello", ["ELF  ·  PE  ·  bare", "sealed: one static file", "8 KB"]),
    ]
    for i, (name, subs) in enumerate(boxes):
        x = bx + i * (bw + gap)
        last = i == len(boxes) - 1
        rc(d, (x, by, x + bw, by + bh), 12,
           fill=(30, 40, 90) if last else PANEL,
           outline=ACCENT if last else PANEL_EDGE, width=2 if last else 1)
        txt(d, (x + bw / 2, by + 30), name, f_name, ACCENT if last else WHITE, anchor="mm")
        for k, s in enumerate(subs):
            if s:
                txt(d, (x + bw / 2, by + 58 + k * 19), s, f_sub, DIM, anchor="mm")
        if not last:
            arrow(d, x + bw + 10, x + bw + gap - 8, by + bh / 2)

    f_note = font(UI, 15)
    txt(d, (44, 258), "Sealed by default — no runtime, no libc, no undefined symbols.  choose hosted is the other path.",
        f_note, SOFT)
    txt(d, (44, 284), "That same compiler is what compiles the compiler.",
        f_note, DIM)
    save(img, W, H, "editors/loment-pipeline.png")


# ---------------------------------------------------------------- 自举图

def bootstrap():
    """自举那四条证明 —— 按 `loment/bootstrap.sh` 打印的 1/4..4/4 排。"""
    W, H = 1400, 412
    img = canvas(W, H)
    d = ImageDraw.Draw(img)

    f_title = font(UIB, 25)
    f_step = font(MONOB, 16)
    f_flow = font(MONOB, 17)
    f_why = font(UI, 15)
    f_note = font(UI, 15)

    txt(d, (44, 40), "How the compiler is rebuilt from itself", f_title, WHITE)

    rows = [
        ("1/4", "seed  ->  stage1", "the genesis assembler, committed to the repo, builds a compiler", ACCENT),
        ("2/4", "stage1  compiles the compiler", "the result must equal the seed, byte for byte", GREEN),
        ("3/4", "stage2  ->  stage3", "and again — the chain reaches a fixed point", CYAN),
        ("4/4", "stage1  vs  stage2", "handed a foreign entry file, the two stages agree", AMBER),
    ]
    y = 116
    for step, flow, why, col in rows:
        rc(d, (44, y - 19, 106, y + 11), 8, fill=(28, 36, 80), outline=PANEL_EDGE)
        txt(d, (75, y - 4), step, f_step, col, anchor="mm")
        txt(d, (128, y - 5), flow, f_flow, WHITE, anchor="lm")
        txt(d, (560, y - 5), why, f_why, DIM, anchor="lm")
        y += 52

    txt(d, (44, y + 16),
        "All four come from one script — `sh loment/bootstrap.sh`. It needs POSIX sh on x86-64 Linux",
        f_note, SOFT)
    txt(d, (44, y + 40),
        "and nothing else: no Python, no interpreter, no C compiler.",
        f_note, DIM)
    save(img, W, H, "editors/loment-bootstrap.png")


# ---------------------------------------------------------------- 报错卡

def diagnostic():
    """`loment check` 的一张说明卡 —— **逐字原文**, 只按窗口宽度折行。

    折行是终端自己做的事, 所以续行**不加 `  | ` 前缀**, 也不删任何一条建议:
    这是截图, 不是摘要。
    """
    mono, monob = font(MONO, 15), font(MONOB, 15)
    W = 1120
    pad, top, lh, bottom = 30, 66, 25, 34
    bar = [("  | ", DIM, False)]

    def cont(s):
        return [("     ", DIM, False), (s, SOFT, False)]

    lines = [
        [("error[E002]: ", RED, True), ("undeclared name", WHITE, True)],
        [("  --> ", DIM, False), ("bad.lomt:4", CYAN, False)],
        [("  |", DIM, False)],
        [("4 | ", DIM, False), ("    return z;", SOFT, False)],
        [("  | ", DIM, False), ("    ", DIM, False), ("^^^^^^^^^", RED, False)],
        bar + [("message: ", WHITE, True), ("z", SOFT, False)],
        bar + [("what went wrong: ", WHITE, True),
               ("A name was used without being declared - a variable, a function, a type", SOFT, False)],
        cont("or a constant."),
        bar + [("why: ", WHITE, True),
               ("This is the one beginners hit most, and in Loment it is almost", SOFT, False)],
        cont("always the same thing: **there is no type inference** - `let x = 1;` is a"),
        cont("syntax error and you must write `let x: u32 = 1;`. Next comes defining after"),
        cont("use inside one module, and then a cross-module name that needs `pub` on the"),
        cont("declaration and `use` at the call site."),
        bar + [("how to fix:", WHITE, True)],
        [("  |   ", DIM, False),
         ("1. Add the type annotation: `let x: u32 = 1;` (a `let` with no type is", SOFT, False)],
        cont("really E019, a syntax error, but it is easy to read as this one)."),
        [("  |   ", DIM, False),
         ("2. Misspelt: fix the spelling. Cross-module: put `pub` on the declaration", SOFT, False)],
        cont('and `use <module>` or `use "./x.lomt"` at the call site.'),
        [("  |   ", DIM, False),
         ("3. Generic functions need their arguments to pin down `T`: `let a: u32 = 4;`", SOFT, False)],
        cont("then `pick(a, b)`. Passing integer literals straight in reports this code,"),
        cont("and that message is misleading."),
        [("  |   ", DIM, False),
         ('4. The name really does come from elsewhere: name its location with the path', SOFT, False)],
        cont('form `use "./util.lomt"`.'),
        bar + [("supported: ", GREEN, True),
               ("generics (when the call site determines T) / static trait dispatch /", SOFT, False)],
        cont("`pub` across modules / both `use` forms"),
        bar + [("not supported: ", RED, True),
               ("type inference / implicit globals / forward references inside one", SOFT, False)],
        cont("module / implicit numeric conversion"),
        [],
        [("1 errors: ", SOFT, False), ("E002 x1", RED, False)],
        [("more: ", DIM, False), ("loment explain E002", SOFT, False)],
    ]

    H = top + len(lines) * lh + bottom
    img = canvas(W, H).convert("RGB")
    d = ImageDraw.Draw(img)

    rc(d, (16, 16, W - 16, H - 16), 12, fill=(12, 16, 34), outline=PANEL_EDGE, width=2)
    d.line([(16 * S, 56 * S), ((W - 16) * S, 56 * S)], fill=PANEL_EDGE, width=S)
    for i, c in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        d.ellipse([(34 + i * 22) * S, 30 * S, (44 + i * 22) * S, 40 * S], fill=c)
    txt(d, (W / 2, 36), "loment check bad.lomt", font(MONO, 14), DIM, anchor="mm")

    y = top + 20
    for runs in lines:
        x = pad + 8
        for s, col, bold in runs:
            f = monob if bold else mono
            d.text((x * S, y * S), s, font=f, fill=col, anchor="la")
            x += d.textlength(s, font=f) / S
        y += lh

    save(img, W, H, "editors/loment-diagnostic.png")


# ---------------------------------------------------------------- 社交预览卡

def social():
    """GitHub 的社交预览图 (1280x640)。

    与其他几张分工不同: 这张**先被缩到拇指大**才被看到 (时间线、聊天卡片),
    所以构图按"缩略图可读"来: 居中、字少字大、负空间给足。元素都放**四角与边缘** ——
    各平台裁切比例不一样, 中间那点必须留得住。

    底是平的 (不铺渐变/纹理), 中间打一层极淡的光, 元素另算 —— 就是用户要的
    "空白背景带元素丰富"。同样**不放任何会过期的说法** (理由见 banner 那段)。

    **它不进 README**, 也不由仓库自己生效: GitHub 把社交预览图存在仓库**设置**里,
    没有 API (`gh repo edit` 没这个开关, REST 也没有)。改法只有两步:
    Settings -> General -> Social preview -> Edit -> Upload an image。
    所以这张图留在仓里, 是为了**有源可重生成**, 不是因为它在页面上出现。
    """
    W, H = 1280, 640
    img = Image.new("RGB", (W * S, H * S), BG_TOP).convert("RGBA")
    d = ImageDraw.Draw(img)

    for r, a in ((560, 30), (380, 26), (220, 22)):      # 中间一层极淡的光
        light = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
        ImageDraw.Draw(light).ellipse(
            [((W / 2 - r) * S, (300 - r * 0.62) * S),
             ((W / 2 + r) * S, (300 + r * 0.62) * S)], fill=(64, 88, 190, a))
        img = Image.alpha_composite(img, light.filter(ImageFilter.GaussianBlur(40 * S)))
    d = ImageDraw.Draw(img)
    for gx in range(40, W, 34):                          # 点阵
        for gy in range(34, H, 34):
            d.ellipse([gx * S, gy * S, (gx + 2) * S, (gy + 2) * S], fill=(30, 40, 84))

    # 四角的代码碎片 (装饰)。左右各自**对齐到同一条内边距线**, 免得撞上角括号
    # 或者被画布边缘切掉 —— 缩略图上最先露馅的就是被切了一半的字。
    frags = [                                            # (边, y, 文字, 字号)
        ("l", 32, "module hello", 16), ("r", 32, "fn _start() {", 16),
        ("l", 152, "guard blk(i);", 15), ("r", 156, "let x: u32 = 1;", 15),
        ("l", 448, 'use "./util.lomt"', 15), ("r", 452, "match k { .. }", 15),
        ("l", 552, "capability blk : disk[0..4]", 15), ("r", 556, "pub extern fn c_add(a: i32)", 15),
    ]
    for side, y, s, sz in frags:
        f = font(MONO, sz)
        if side == "l":
            d.text((88 * S, y * S), s, font=f, fill=(58, 74, 146), anchor="la")
        else:
            d.text(((W - 88) * S, y * S), s, font=f, fill=(58, 74, 146), anchor="ra")

    for cx, cy, sx, sy in ((46, 46, 1, 1), (W - 46, 46, -1, 1),
                           (46, H - 46, 1, -1), (W - 46, H - 46, -1, -1)):
        d.line([(cx * S, cy * S), ((cx + 30 * sx) * S, cy * S)], fill=(70, 92, 170), width=2 * S)
        d.line([(cx * S, cy * S), (cx * S, (cy + 30 * sy) * S)], fill=(70, 92, 170), width=2 * S)

    glow = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([(548 * S, 84 * S), (732 * S, 268 * S)],
                                 fill=(84, 115, 227, 96))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(30 * S)))
    icon = Image.open("editors/loment.ico")
    icon.size = (256, 256)
    icon = icon.convert("RGBA").resize((176 * S, 176 * S), Image.LANCZOS)
    img.paste(icon, (552 * S, 88 * S), icon)
    d = ImageDraw.Draw(img)

    txt(d, (W / 2, 344), "Loment", font(UIB, 104), WHITE, anchor="mm")
    d.rounded_rectangle([(W / 2 - 44) * S, 404 * S, (W / 2 + 44) * S, 407 * S],
                        radius=2 * S, fill=ACCENT)
    txt(d, (W / 2, 442), "Programming Language", font(UI, 33), (232, 238, 255), anchor="mm")
    txt(d, (W / 2, 486), "Program by Fujo", font(UI, 24), (176, 190, 228), anchor="mm")
    txt(d, (W / 2, 566), "github.com/FujoJTOP/loment", font(MONO, 22), (120, 138, 190), anchor="mm")

    save(img.convert("RGB"), W, H, "editors/loment-social.png")


# ---------------------------------------------------------------- Discord 图标

def discord():
    """Discord 服务器图标 (512x512)。

    两条约束决定它长什么样, 都不是审美:

    * **显示成圆形** —— Discord 把图标按圆裁。所以**从一开始就按圆设计**, 而不是拿
      一张方的去让它切掉四角;
    * **实际尺寸极小** —— 成员列表里 32px 上下。所以**一个字母都不放**: 那个尺寸上
      字只会糊成一团。标记只在够大的地方放字 (banner / 社交卡)。

    **底是自己画的圆, 不是把现成图标裁成圆**: 那个图标是圆角**方**, 裁圆之后它的四条
    直边会在圆内留下接缝。所以圆另画, 只用它里面的那个 L —— 按"白"取出来, 放到圆心上。
    """
    W = H = 512
    PAD = 14                                     # 圆与画布之间留一点, 免得贴边
    box = [PAD * S, PAD * S, (W - PAD) * S, (H - PAD) * S]

    disc = Image.new("L", (W * S, H * S), 0)
    ImageDraw.Draw(disc).ellipse(box, fill=255)

    grad = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    for y in range(H * S):                        # 上浅下深, 与图标的蓝同族
        t = y / (H * S - 1)
        gd.line([(0, y), (W * S, y)],
                fill=tuple(round(a + (b - a) * t) for a, b in zip((124, 152, 255), (68, 96, 210)))
                + (255,))
    grad.putalpha(disc)
    img = Image.alpha_composite(Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0)), grad)

    icon = Image.open("editors/loment.ico")       # 只借它的 L, 不借它的底
    icon.size = (256, 256)
    # 阈值要高: 图标自己有一层很淡的内高光, 卡低了会在 L 的包围盒里留下几点杂点,
    # 而它们会跟着 L 一起放大。中值再滤一道, 把 1px 的孤立点清掉。
    white = icon.convert("RGBA").convert("L").point(lambda v: 255 if v > 250 else 0)
    white = white.filter(ImageFilter.MedianFilter(3))
    white = white.crop(white.getbbox())

    target = int(W * S * 0.50)                    # 字标占盘面的一半 —— 32px 上要读得出
    k = min(target / white.width, target / white.height)
    lw, lh = round(white.width * k), round(white.height * k)
    L = white.resize((lw, lh), Image.LANCZOS)
    img.paste(Image.new("RGBA", (lw, lh), (255, 255, 255, 255)),
              ((W * S - lw) // 2, (H * S - lh) // 2), L)

    # 一道细环: Discord 的底色跟着主题走 (深/浅/自定义), 环给徽章一条边界
    d = ImageDraw.Draw(img)
    d.ellipse([4 * S, 4 * S, (W - 4) * S, (H - 4) * S], outline=(158, 182, 255, 235),
              width=5 * S)

    save(img, W, H, "editors/loment-discord.png")


if __name__ == "__main__":
    banner()
    pipeline()
    bootstrap()
    diagnostic()
    social()
    discord()
