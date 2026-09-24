#!/usr/bin/env python3
"""Render revised comic boards PDF: visual panel grids, not a text script."""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("/workspace/comic_flow")
SAMPLE_DIR = Path("/workspace/comic_flow_pdf")
PDF_PATH = OUT_DIR / "DEVIL_REVISED_BOARDS.pdf"

# Letter @ 150 DPI (matches finished samples)
W, H = 1275, 1650
MARGIN = 48
GUTTER = 18
TITLE_H = 56
BORDER = 4
BG = (255, 255, 255)
INK = (20, 20, 20)
PANEL_BG = (252, 252, 250)
CAPTION_BG = (255, 255, 255)
MUTED = (90, 90, 90)

# --- page data: (title, panels list of short beat strings) ---
# Prefer 1-2 lines; truncate later in draw.
PAGES = {
    1: ("Microbangs + Gahgeous", ["FINISHED SAMPLE", "3 panels"]),
    2: ("Trophy + NWOL aerial", ["FINISHED SAMPLE", "3 panels"]),
    3: ("This Word + Bible hide", ["FINISHED SAMPLE", "4 panels"]),
    4: ("Clock in / Zohan", ["FINISHED SAMPLE", "4 panels"]),
    5: (
        "Bible phone / Gabi credit",
        [
            "Counter: Devil hides face behind hollowed This Word Bible -- fake-work setup",
            "Cutaway: MagSafe ring + iPhone inside Bible (Zohan on screen)",
            "Credit: \"I wish I could take credit, but... Gabi.\" + appointment-book aside",
            "Quiet: Devil's eyes + small Gabi inset -- \"Love her, but...\"",
        ],
    ),
    6: (
        "Spotty histories / lost job again",
        [
            "Arm/tattoo: Gabi \"Oh my devil, I lost my job again! But... new shop...\"",
            "Collage: looping Gabi faces / years 2011-2019 -- linked by spotty job histories",
            "Phone: Zohan still playing -- \"kinda sucks\"",
            "SFX: (((*sandlerian noises*)))",
        ],
    ),
    7: (
        "What would the Zohan do?",
        [
            "Wide mall corridor: customer appears; Devil startled",
            "Internal: \"WHAT WOULD THE ZOHAN DO?\" mid-lunge",
            "SITREP montage: Ruth's Chris shirt / bro-jacked / drawstring bag",
            "Punchline: mall walker asks bathroom",
        ],
    ),
    8: (
        "Mall walkers are vermin",
        [
            "Devil points: \"Thataway is the uh... food court.\" / walker \"Bless you sir!\"",
            "Caption cold: \"Mall walkers are vermin.\"",
            "Wide aerial/parking: car across Employee of Month / Moe's spots -- \"Big Mistake.\"",
        ],
    ),
    9: (
        "Gabi vs Stuart Little (salon A)",
        [
            "Meanwhile... messy nape close-up",
            "Gabi: neck looks like a \"rat's ass\" / who cut this?",
            "Extreme eyes: \"It. Shall. Be. Fixed.\"",
            "Multi-arm tool storm (snip/psht/zzzz) -- customer \"OW!\"",
        ],
    ),
    10: (
        "Tips start at 20%",
        [
            "Tuft of hair aloft: \"Say goodbye... to Stuart Little... The Rat.\"",
            "Reaction / \"RIP STEWIE LIL\" / \"Colby!\"",
            "Gabi loom: \"happy with your cut... Tips start at 20%.\"",
        ],
    ),
    11: (
        "Zohan talk turns bad",
        [
            "Two-shot: \"I finally watched it. Don't mess with the Zohan.\" / Gabi \"Huh?\"",
            "Gabi eyes ECU: \"Is she serious right now?\"",
            "Distressed face + caption spiral start",
            "Wide text storm: \"no one cares... I feel trapped...\" / scattered IT'S OK",
        ],
    ),
    12: (
        "Karmic debt / clock",
        [
            "Abstract: INFRACTION * TO THE DEVIL * YOUR KARMIC DEBT...",
            "Icon/symbol beat (star/cloak mark)",
            "Gabi flat: \"Please let's not talk about this here.\" / \"It's OK.\"",
            "Time-o-matic KA-CHUNK -- card \"Gabi Part-Time Max. 18H\"",
        ],
    ),
    13: (
        "Parking as revenue",
        [
            "\"Employee\" sign X'd out -- Moe's has no employees, under construction for years",
            "Diagram: mall doesn't make $ off commerce; walkers don't help",
            "Devil + ticket device + dying fund graph -- fines opt-in, commission store credit",
            "POV dash: printer Chunk-a / Infraction $127",
        ],
    ),
    14: (
        "Meet Arcade",
        [
            "Gust blows tickets -- \"Frick.\" / \"HEY!\"",
            "Masked figure + headphones; Ruth's Chris BG -- \"Nice to see you again... solid earlier.\"",
            "Full body: \"White Girl Star-tistics\" shirt; \"Sorry for being vulgar.\"",
            "Finger to forehead: \"Ronnie Dakota... call me Arcade.\" / \"Uhh... OK.\"",
            "Soft: \"Sorry, Ronnie. This was never meant to be.\" / name confusion",
        ],
    ),
    15: (
        "Guardian + Gabi appears",
        [
            "Devil points at pavement: \"You were in a Moe's spot...\"",
            "Arcade grin: \"Twice in one day! ... like my Guardian...\"",
            "Gabi enters big: \"DEVIL! What're you doing here?\"",
        ],
    ),
    16: (
        "Ten minutes / Colleen's card",
        [
            "Two-shot break: Gabi \"I will give you ten minutes.\"",
            "Reveal: \"I'm using Colleen's card too. Need the hours.\" / Devil \"I saw you clock out!\"",
            "Timer motif starts (10 -> ...)",
        ],
    ),
    17: (
        "Sandler oeuvre",
        [
            "Devil monologue: Sandler tackling something as big as the...",
            "Big lettering: \"...Devil!\"",
            "Course-correct: \"No! He covered that in Little Nicky!\"",
            "Gabi: the haircut movie; Devil admits Zohan sucked; mentioned it offhand",
        ],
    ),
    18: (
        "Herbal cigarettes",
        [
            "Gabi eyes ECU: \"she is full on bullshitting me\" / break almost up",
            "Bic FLICK! / \"Gabi! You smoke?\"",
            "\"Is that cannabis?\" / \"Dude, no one says weed.\"",
            "Herbal cigs for acting class; Devil: \"still think you should go to therapy!\"",
            "\"Colleen has to clock in.\" -- walk-off",
        ],
    ),
    19: (
        "Back to This Word / dead time",
        [
            "Phone/YouTube: Daniel 6 -- \"back once again, to the store\"",
            "Tired face + Zohan residual: \"I can't believe I watched that movie.\"",
            "Store cross / \"Only four more hours\" / \"Small comforts.\"",
            "Search: Tiffany \"New York\" Pollard classics fallback",
        ],
    ),
    20: (
        "Emily Clair, secret shopper?",
        [
            "Cheerleader-ish customer vs FOR SALE wall: \"Is this the Northwest Orlando This Word?\"",
            "Bible/book CLAP on counter -- \"What is with today?\"",
            "\"Modest but edgy couture!\" / \"Can I help you?\"",
            "\"Yearly secret shopper?\" -> Emily Clair -> \"Eclair?\" -> Eclair Capital owns this mall",
        ],
    ),
    21: (
        "Eclair Capital Holdings",
        [
            "Wide establish: ECLAIR CAPITAL HOLDINGS sign",
            "Emily at desk/headset: \"Yeah, they weren't buying it.\" / \"Kinda my fault tho.\"",
            "Phone nameplate Emily/Claire -- \"Did you give them your full name again?\"",
        ],
    ),
    22: (
        "The lying is implied",
        [
            "Emily ECU: \"I panicked!\" / \"You've trained for months.\" / \"Year and a half actually.\"",
            "Handset: \"What part of secret shopper do you not understand?\"",
            "Defiant: \"Don't make me feel shitty for being honest!\"",
            "Mouthpiece: \"The lying is implied.\" -> soft \"Love you, honey bunny.\"",
        ],
    ),
    23: (
        "Precarious / open mic cold open",
        [
            "Time-o-matic: Colleen Part-time KA-CHUNK",
            "Abstract/jagged: \"The times are precarious, the precarious are timed.\"",
            "Caption: awful poet at an open mic",
            "Echo on clock face: same line overlaid on Time-o-matic",
        ],
    ),
    24: (
        "TIME! / venue bones",
        [
            "Spiky poet at mic: \"Aw, jesus.\" / off-panel TIME!",
            "Devil behind desk: \"Nothing personal... I'm still on the clock.\" / busy day",
            "Wide room: debris, FRAGILE crate -- open mic in ex-Moe's / diner / urgent care",
            "\"Same decor.\" / \"I'll put you top of the list for next time.\"",
        ],
    ),
    25: (
        "Met Gabi here",
        [
            "Abstract scribble: \"This was where I'd met her. Gabi.\"",
            "Sign-up clipboard (Michael-sing, Gary-poetry...) -- she's not on it; thank the lord",
            "Wide: Sonic on stage + Gabi at Audience Table, enjoying",
            "Gabi praise: \"Rupi Kaur, Ralph Waldo Emerson, Emily Dixon!\"",
        ],
    ),
    26: (
        "Sonic & The Light",
        [
            "Sonic sheepish: \"thanks Gabi... Emil Dijon... Quebecois.\" Caption: we called him the Sonic",
            "Glowing bulb: THE LIGHT / 10-minute timer respected",
            "Caption: rarely ran over; never asked better spot; art never got better",
            "Clock + calendar: \"Moe's coming soon!\" -- \"Nice guy, miss him sometimes.\"",
        ],
    ),
    27: (
        "Not a dead mall",
        [
            "Facade/skull: \"I wouldn't call this a 'Dead Mall.'\"",
            "Drain life: cats, rats, hagfish -- \"Some things just live differently.\"",
            "Vines over \"Moe's Coming Soon!\" -- \"Surviving isn't flourishing.\"",
        ],
    ),
    28: (
        "Pringles guy",
        [
            "Young Devil at mic; Respect the Light sign",
            "Heckler beanie: BOO / YOU SUCK / \"Not this guy again\"",
            "Caption: dabbling in poetry; New Sincerity vs cringe",
            "Perfect insult: \"You look like the Pringles guy!\"",
            "Mic KLUNK on floor -- \"I never recovered.\"",
        ],
    ),
    29: (
        "Aztek / Fresca",
        [
            "Steering-wheel POV: sobbing -- \"I'm so fucking stupid...\"",
            "Exterior: 2005 Pontiac Aztek",
            "Trunk packed with FRESCA cases -- craigslist deal; this is where he cried",
        ],
    ),
    30: (
        "Gabi finds him",
        [
            "Fresca \"fortress of sweet deals\" / castle flags",
            "Glasses reflection FRESCA/FREYJA -- off-panel \"Are you okay?\"",
            "Through arch: \"All good!\" / whisper \"I was not all good.\"",
            "Two-shot: \"You're crying!\" / \"It's sweat.\" / \"It's not.\" / Orlando heat",
        ],
    ),
    31: (
        "Gabrielle / Paul Blart / Sonic?",
        [
            "Sweat ECU: \"Hell of an interrogation.\"",
            "\"I'm Gabrielle, by the way. But most say Gabi.\" / \"Nice to meet you.\"",
            "Kevin James / Paul Blart phone digression",
            "Interrupted at Bible: off-panel \"Hey! You're back!\" / \"Huh?\"",
            "Wide empty: no one there -- \"almost sounded like the old poet. Sonic...\"",
        ],
    ),
    32: (
        "Raccoon standoff",
        [
            "Raccoon face ECU",
            "Paws: TICKET $127 PAY OR ELSE!",
            "Devil/worker scissors/bike: \"Put that down! I need that money!\"",
            "Wide: raccoon on car hood, *twitch*, ticket held hostage",
        ],
    ),
    33: (
        "Last straw",
        [
            "Caption: \"I would never hurt him, or her.\"",
            "Eye ECU: \"Oh, little fucker. What are you turning me into?\"",
            "Chaos: KICK / LIFT / NO NO NO NO NO",
            "CHOMP KA-CHUNK -- Colleen ID / 3.0 Hours / $0",
            "Full-bleed text: WHY IS THIS THE LAST STRAW?",
        ],
    ),
    34: (
        "Dissociation / allergic to bullshit",
        [
            "Hollow eyes: \"trite portrayal\" of how it felt / dissociation",
            "Abstract impersonal shapes",
            "Mask face: \"Cruelty belongs to simple masks.\" / \"Simple as debt.\"",
            "Portrait: \"I am allergic to Bullshit. and bullshitting.\"",
        ],
    ),
    35: (
        "Mikayla's ultimatum (2013)",
        [
            "Caption: desperate for money and a calling (2013)",
            "Sister Mikayla impressed / \"so cool you want to hire me\" (irony)",
            "Ultimatum: any job + rent > $0 (therapist-instructed)",
            "\"Allocated towards non-revenue generating activities!\" / gifted kid beat",
        ],
    ),
    36: (
        "Asset flip / hate this haircut",
        [
            "Gate packages: Christian Rock / eccChristian -- relentless newness",
            "Price gun SHRIIIIP + logo soup (SPIRIT/THIS/NOW/BLINK-182...)",
            "Asset-flip gag: Evil Alien (lvl 1) -> Demon Squid (final boss)",
            "Controller POV: vaporize the demon squid -- \"Might as well close out your shift.\"",
            "Face ECU: \"I fucking hate this haircut.\"",
        ],
    ),
    37: (
        "Whale fall / Home",
        [
            "Wide seafloor: giant decomposing whale -- \"On the bottom of the sea floor... Somewhere...\"",
            "Rib close-up: pure bone naming comes later",
            "Worms on flesh: \"some... have an easy name for this carcass\"",
            "Smash cut Home. -- \"Ten minutes clone. The light. Thank god it's over.\"",
        ],
    ),
}


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FONT_TITLE = load_font(28, bold=True)
FONT_SUB = load_font(18, bold=False)
FONT_PANEL = load_font(17, bold=False)
FONT_PANEL_SM = load_font(15, bold=False)
FONT_NUM = load_font(14, bold=True)
FONT_COVER = load_font(42, bold=True)
FONT_COVER_SUB = load_font(24, bold=False)
FONT_COVER_SM = load_font(18, bold=False)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def layout_rects(n: int, page_i: int) -> list[tuple[int, int, int, int]]:
    """Return list of (x0,y0,x1,y1) for n panels. Vary by page_i so pages don't clone."""
    x0 = MARGIN
    x1 = W - MARGIN
    y0 = MARGIN + TITLE_H
    y1 = H - MARGIN
    aw = x1 - x0
    ah = y1 - y0
    g = GUTTER
    rects: list[tuple[int, int, int, int]] = []

    variant = page_i % 3  # rotate patterns within same count

    if n == 3:
        # two on top, one wide bottom (sample p1-2)
        mid_y = y0 + int(ah * 0.48)
        half = (aw - g) // 2
        rects = [
            (x0, y0, x0 + half, mid_y),
            (x0 + half + g, y0, x1, mid_y),
            (x0, mid_y + g, x1, y1),
        ]
        if variant == 1:
            # one tall left, two stacked right
            mid_x = x0 + int(aw * 0.55)
            mid_y2 = y0 + (ah - g) // 2
            rects = [
                (x0, y0, mid_x, y1),
                (mid_x + g, y0, x1, mid_y2),
                (mid_x + g, mid_y2 + g, x1, y1),
            ]
        elif variant == 2:
            # wide top, two bottom
            mid_y = y0 + int(ah * 0.52)
            half = (aw - g) // 2
            rects = [
                (x0, y0, x1, mid_y),
                (x0, mid_y + g, x0 + half, y1),
                (x0 + half + g, mid_y + g, x1, y1),
            ]
    elif n == 4:
        # 2x2 default
        mid_x = x0 + (aw - g) // 2
        mid_y = y0 + (ah - g) // 2
        if variant == 0:
            rects = [
                (x0, y0, mid_x, mid_y),
                (mid_x + g, y0, x1, mid_y),
                (x0, mid_y + g, mid_x, y1),
                (mid_x + g, mid_y + g, x1, y1),
            ]
        elif variant == 1:
            # wide top + 3 bottom strip? better: tall + 3 stack-ish: wide, then 2, then wide
            y_a = y0 + int(ah * 0.28)
            y_b = y0 + int(ah * 0.62)
            half = (aw - g) // 2
            rects = [
                (x0, y0, x1, y_a),
                (x0, y_a + g, half + x0, y_b),
                (x0 + half + g, y_a + g, x1, y_b),
                (x0, y_b + g, x1, y1),
            ]
        else:
            # 1 tall left + 3 stacked right
            mid_x = x0 + int(aw * 0.48)
            h3 = (ah - 2 * g) // 3
            rects = [
                (x0, y0, mid_x, y1),
                (mid_x + g, y0, x1, y0 + h3),
                (mid_x + g, y0 + h3 + g, x1, y0 + 2 * h3 + g),
                (mid_x + g, y0 + 2 * h3 + 2 * g, x1, y1),
            ]
    elif n == 5:
        if variant == 0:
            # 2 top + 2 mid + 1 wide bottom
            y_a = y0 + int(ah * 0.32)
            y_b = y0 + int(ah * 0.64)
            half = (aw - g) // 2
            rects = [
                (x0, y0, x0 + half, y_a),
                (x0 + half + g, y0, x1, y_a),
                (x0, y_a + g, x0 + half, y_b),
                (x0 + half + g, y_a + g, x1, y_b),
                (x0, y_b + g, x1, y1),
            ]
        elif variant == 1:
            # 3 top + 2 bottom
            y_mid = y0 + int(ah * 0.48)
            w3 = (aw - 2 * g) // 3
            half = (aw - g) // 2
            rects = [
                (x0, y0, x0 + w3, y_mid),
                (x0 + w3 + g, y0, x0 + 2 * w3 + g, y_mid),
                (x0 + 2 * w3 + 2 * g, y0, x1, y_mid),
                (x0, y_mid + g, x0 + half, y1),
                (x0 + half + g, y_mid + g, x1, y1),
            ]
        else:
            # wide top + 2x2 bottom
            y_mid = y0 + int(ah * 0.30)
            mid_x = x0 + (aw - g) // 2
            mid_y = y_mid + g + (y1 - y_mid - g - g) // 2
            # simplify: wide, then 2, then 2
            y_a = y0 + int(ah * 0.28)
            y_b = y0 + int(ah * 0.64)
            half = (aw - g) // 2
            rects = [
                (x0, y0, x1, y_a),
                (x0, y_a + g, x0 + half, y_b),
                (x0 + half + g, y_a + g, x1, y_b),
                (x0, y_b + g, x0 + half, y1),
                (x0 + half + g, y_b + g, x1, y1),
            ]
    elif n == 6:
        # 2x3
        cols = 2
        rows = 3
        cw = (aw - g) // cols
        rh = (ah - 2 * g) // rows
        for r in range(rows):
            for c in range(cols):
                px0 = x0 + c * (cw + g)
                py0 = y0 + r * (rh + g)
                px1 = px0 + cw if c < cols - 1 else x1
                py1 = py0 + rh if r < rows - 1 else y1
                rects.append((px0, py0, px1, py1))
        if variant == 1:
            # 3x2
            rects = []
            cols, rows = 3, 2
            cw = (aw - 2 * g) // cols
            rh = (ah - g) // rows
            for r in range(rows):
                for c in range(cols):
                    px0 = x0 + c * (cw + g)
                    py0 = y0 + r * (rh + g)
                    px1 = px0 + cw if c < cols - 1 else x1
                    py1 = py0 + rh if r < rows - 1 else y1
                    rects.append((px0, py0, px1, py1))
    else:
        # fallback grid
        cols = 2
        rows = (n + 1) // 2
        cw = (aw - g) // cols
        rh = (ah - (rows - 1) * g) // rows
        for i in range(n):
            r, c = divmod(i, cols)
            px0 = x0 + c * (cw + g)
            py0 = y0 + r * (rh + g)
            px1 = px0 + cw
            py1 = py0 + rh
            rects.append((px0, py0, px1, py1))
    return rects


def draw_panel(draw: ImageDraw.ImageDraw, rect, text: str, idx: int) -> None:
    x0, y0, x1, y1 = rect
    # outer black border (comic gutter look)
    draw.rectangle([x0, y0, x1, y1], outline=INK, width=BORDER, fill=PANEL_BG)
    # inner subtle inset line
    inset = 6
    draw.rectangle(
        [x0 + inset, y0 + inset, x1 - inset, y1 - inset],
        outline=(180, 180, 180),
        width=1,
    )
    # panel number badge
    badge = f"{idx}"
    bx0, by0 = x0 + 10, y0 + 8
    bb = draw.textbbox((0, 0), badge, font=FONT_NUM)
    pad = 4
    draw.rectangle(
        [bx0 - pad, by0 - 2, bx0 + (bb[2] - bb[0]) + pad, by0 + (bb[3] - bb[1]) + 2],
        fill=INK,
    )
    draw.text((bx0, by0), badge, font=FONT_NUM, fill=(255, 255, 255))

    # beat text centered-ish in panel
    max_w = (x1 - x0) - 28
    max_h = (y1 - y0) - 50
    font = FONT_PANEL if (x1 - x0) > 400 else FONT_PANEL_SM
    # truncate aggressively for readability
    t = text
    if len(t) > 160:
        t = t[:157] + "..."
    lines = wrap_text(draw, t, font, max_w)
    # limit lines by height
    line_h = font.size + 4
    max_lines = max(1, max_h // line_h)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        if len(lines[-1]) > 3:
            lines[-1] = lines[-1][:-3] + "..."

    total_h = len(lines) * line_h
    ty = y0 + ((y1 - y0) - total_h) // 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        tx = x0 + ((x1 - x0) - lw) // 2
        # soft caption plate behind text for readability
        pad_x, pad_y = 8, 3
        draw.rectangle(
            [tx - pad_x, ty - pad_y, tx + lw + pad_x, ty + line_h - 2],
            fill=(255, 255, 240),
            outline=(220, 220, 200),
        )
        draw.text((tx, ty), line, font=font, fill=INK)
        ty += line_h


def make_cover() -> Image.Image:
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # decorative outer frame
    d.rectangle([36, 36, W - 36, H - 36], outline=INK, width=6)
    d.rectangle([48, 48, W - 48, H - 48], outline=INK, width=2)

    title = "I am the Devil"
    sub = "Revised boards"
    note = "(3-6 panels/page)"
    remap = "Dense pencils 15 pages  ->  revised ~37 pages"
    detail = "Pages 1-4 = finished sample art\nPages 5-37 = open storyboard layouts\nfrom DEVIL_REVISED_PAGE_FLOW.md"

    def center_text(y, text, font, fill=INK):
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        d.text(((W - tw) // 2, y), text, font=font, fill=fill)

    center_text(420, title, FONT_COVER)
    center_text(490, sub, FONT_COVER_SUB)
    center_text(540, note, FONT_COVER_SM, MUTED)
    # divider
    d.line([W // 2 - 180, 600, W // 2 + 180, 600], fill=INK, width=2)
    center_text(640, remap, FONT_SUB)
    # multi-line detail
    y = 720
    for line in detail.split("\n"):
        center_text(y, line, FONT_SUB, MUTED)
        y += 36
    center_text(H - 120, "storyboard / thumbnail boards -- not a script", FONT_PANEL_SM, MUTED)
    return im


def make_sample_page(n: int) -> Image.Image:
    """Full-bleed finished sample with slim title bar."""
    src = SAMPLE_DIR / f"page-{n}.png"
    art = Image.open(src).convert("RGB")
    # fit to page leaving title strip
    canvas = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(canvas)
    title, panels = PAGES[n]
    n_panels = 3 if n <= 2 else 4
    header = f"Page {n} -- {title}  |  {n_panels} panels (FINISHED SAMPLE)"
    d.text((MARGIN, 18), header, font=FONT_TITLE, fill=INK)

    # place art below title
    top = MARGIN + TITLE_H - 10
    avail_h = H - top - MARGIN // 2
    avail_w = W - 2 * MARGIN
    # scale art to fit
    aw, ah = art.size
    scale = min(avail_w / aw, avail_h / ah)
    nw, nh = int(aw * scale), int(ah * scale)
    art = art.resize((nw, nh), Image.Resampling.LANCZOS)
    ox = (W - nw) // 2
    oy = top + (avail_h - nh) // 2
    canvas.paste(art, (ox, oy))
    # thin border around art
    d.rectangle([ox - 2, oy - 2, ox + nw + 1, oy + nh + 1], outline=INK, width=2)
    return canvas


def make_board_page(n: int) -> Image.Image:
    title, panels = PAGES[n]
    n_panels = len(panels)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    header = f"Page {n} -- {title}  |  {n_panels} panels"
    d.text((MARGIN, 18), header, font=FONT_TITLE, fill=INK)
    # underline
    d.line([MARGIN, TITLE_H - 8, W - MARGIN, TITLE_H - 8], fill=INK, width=2)

    rects = layout_rects(n_panels, n)
    for i, (rect, text) in enumerate(zip(rects, panels), start=1):
        draw_panel(d, rect, text, i)
    return im


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pages: list[Image.Image] = []

    print("cover...")
    pages.append(make_cover())

    for n in range(1, 38):
        print(f"page {n}...")
        if n <= 4:
            pages.append(make_sample_page(n))
        else:
            pages.append(make_board_page(n))

    # Save multipage PDF via Pillow
    print(f"writing {PDF_PATH}...")
    rgb_pages = [p.convert("RGB") for p in pages]
    rgb_pages[0].save(
        PDF_PATH,
        "PDF",
        resolution=150.0,
        save_all=True,
        append_images=rgb_pages[1:],
    )
    size = PDF_PATH.stat().st_size
    print(f"PDF pages={len(rgb_pages)} size={size}")

    # Previews
    def save_preview(im: Image.Image, path: Path) -> None:
        # JPEG preview ~half res for light files
        preview = im.copy()
        preview.thumbnail((900, 1200), Image.Resampling.LANCZOS)
        preview.convert("RGB").save(path, "JPEG", quality=85)
        print(f"preview {path}")

    save_preview(pages[0], OUT_DIR / "board_preview_cover.jpg")
    # page 14 Arcade = index 14 (cover=0, p1=1, ... p14=14)
    save_preview(pages[14], OUT_DIR / "board_preview_p14.jpg")
    save_preview(pages[37], OUT_DIR / "board_preview_p37.jpg")

    print("DONE")


if __name__ == "__main__":
    main()
