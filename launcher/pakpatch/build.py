"""Build launcher/assets/pakpatch.json from a player's own 1.26 DATA.PAK.

The launcher never ships game files. This script reads the original entries out of
DATA.PAK, applies our edits, and records only the difference:

  * LOGIN.LAYOUT / SERVERLIST.LAYOUT (BINLAYOUT): edited as text, recompiled with
    tl2-mikuro-mod-packer, then diffed against the original payload into splice ops.
    BINLAYOUT has block sizes and absolute offsets, so an edit cannot be done by
    string replacement at run time; the splice list carries the fixed-up bytes.
  * TRANSLATION.DAT (BINDAT): strings live in a table keyed by id with no offsets
    into it, so the launcher swaps whole strings. Only the (old, new) pairs are kept.
  * TAPTAPLOGINBUTTON.DDS: replaced outright by our own image (login_button.png, drawn
    by make_button.py), stored zlib-compressed.

SERVERLIST gets a centered "Server: <host>" line. Its text is a fixed-length
placeholder; the launcher overwrites those characters with the chosen host padded
with spaces, which keeps every size and offset in the layout valid.

Each file is keyed by its original size + crc32; the launcher skips any entry that
does not match exactly (another game version, or XD changed that file).

Usage:  python build.py [--pak DATA.PAK] [--packer tl2-mikuro-mod-packer.exe]
"""
from __future__ import annotations

import argparse
import base64
import difflib
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "assets" / "pakpatch.json"
DEFAULT_PAK = Path("E:/Program Files (x86)/Steam/steamapps/common/Torchlight II/PAKS/DATA.PAK")
DEFAULT_PACKER = Path("D:/WizardProject/TL2/tl2-mikuro-mod-packer-rs/target/release/tl2-mikuro-mod-packer.exe")

LOGIN = "MEDIA/UI/MENUS/MESSAGEBOXES/LOGIN.LAYOUT"
SERVERLIST = "MEDIA/UI/MENUS/MAINMENUS/SERVERLIST.LAYOUT"
TRANSLATIONS = ["MEDIA/TRANSLATIONS/MANDARIN/TRANSLATION.DAT", "MEDIA/TRANSLATIONS/TAIWANESE/TRANSLATION.DAT"]

# Placeholder for the server line. The launcher writes exactly this many UTF-16 units.
PLACEHOLDER_LEN = 96
PLACEHOLDER = ("@@MIKURO_SERVER_LINE@@" + "#" * PLACEHOLDER_LEN)[:PLACEHOLDER_LEN]

# old English layout text -> new English, and per-language translations of the new text.
NOTICE = [
    ("Dear players:",
     "No TapTap account needed.",
     {"MANDARIN": "无需 TapTap 账号。", "TAIWANESE": "無需 TapTap 帳號。"}),
    ("To provide long-term and stable operations, the game account system is upgraded to TapTap login "
     "starting today. You need to register or sign in with the new account system to continue playing online mode.",
     "The Mikuro launcher points this game at a community lobby. The button above opens the lobby's own "
     "sign-in page in your browser, which sends you straight back here.",
     {"MANDARIN": "本游戏已由 Mikuro 启动器指向社区大厅。点击上方按钮，浏览器会打开大厅自己的登录页，随后自动跳回游戏。",
      "TAIWANESE": "本遊戲已由 Mikuro 啟動器指向社群大廳。點擊上方按鈕，瀏覽器會開啟大廳自己的登入頁，隨後自動跳回遊戲。"}),
    ("The new account will reset the friend list and will not affect existing local save progress.",
     "To change your random name, open the launcher's Auth URL in the same browser.",
     {"MANDARIN": "想改掉随机名字，请用同一个浏览器打开启动器里的 Auth URL。",
      "TAIWANESE": "想改掉隨機名字，請用同一個瀏覽器開啟啟動器裡的 Auth URL。"}),
    ("Thank you for your understanding and support!",
     "Your local saves are not affected.",
     {"MANDARIN": "本地存档不受影响。", "TAIWANESE": "本機存檔不受影響。"}),
]

# Window title of the login box and of its "connecting" box (the layout has it twice). The
# English title font (HUGE.TTF) has no star glyph; the Mandarin one (FZLBJW.TTF) does.
TITLE = ("TapTap Log In", "★MIKURO LOGIN★",
         {"MANDARIN": "★MIKURO LOGIN★", "TAIWANESE": "★MIKURO LOGIN★"})
TITLE_COUNT = 2

# The green TapTap button image, replaced by our own (make_button.py -> login_button.png).
# Same format as the original: 512x128 uncompressed A4R4G4B4, the button in the top-left 370x70.
BUTTON_PNG = HERE / "login_button.png"
BUTTON_DDS = ["MEDIA/UI/HUD/SCHEMES/TAPTAPLOGINBUTTON.DDS",
              "MEDIA/TRANSLATIONS/MANDARIN/UI/HUD/SCHEMES/TAPTAPLOGINBUTTON.DDS"]
DDS_W, DDS_H = 512, 128

# Inserted into SERVERLIST's TopBar right after AccountDetails ("Account: ..."), same row.
TOPBAR_ID = "-9069365514745537816"
SERVER_LINE = [
    "[BASEOBJECT]",
    "\t[PROPERTIES]",
    "\t\t<STRING>DESCRIPTOR:Text",
    "\t\t<STRING>NAME:MikuroServerLine",
    f"\t\t<INTEGER64>PARENTID:{TOPBAR_ID}",
    "\t\t<INTEGER64>ID:7310393047151420413",
    "\t\t<FLOAT>OFFSET Y:-12",
    "\t\t<FLOAT>WIDTH:520",
    "\t\t<FLOAT>HEIGHT:16",
    "\t\t<BOOL>CLIPPED:false",
    "\t\t<BOOL>MOUSE PASS THROUGH:true",
    "\t\t<STRING>VERTICAL:CENTER",
    "\t\t<STRING>HORIZONTAL:CENTER",
    f"\t\t<STRING>TEXT:{PLACEHOLDER}",
    "\t\t<STRING>FONT:Serif14",
    "\t\t<STRING>HORIZONTAL ALIGN:CENTER",
    "\t[/PROPERTIES]",
    "[/BASEOBJECT]",
]


def step(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---- PAK access ---------------------------------------------------------------

def read_manifest(man: Path) -> dict[str, tuple[int, int, int]]:
    """path -> (crc, off, size) for every file record."""
    b = man.read_bytes()
    o = 0

    def u16() -> int:
        nonlocal o
        v = struct.unpack_from("<H", b, o)[0]; o += 2; return v

    def u32() -> int:
        nonlocal o
        v = struct.unpack_from("<I", b, o)[0]; o += 4; return v

    def ss() -> str:
        nonlocal o
        n = u16(); s = b[o:o + 2 * n].decode("utf-16-le"); o += 2 * n; return s

    u16(); u32(); ss(); u32()
    out = {}
    for _ in range(u32()):
        d = ss()
        for _ in range(u32()):
            crc = u32(); o += 1; name = ss(); off = u32(); size = u32(); o += 8
            if name:
                out[d + name] = (crc, off, size)
    return out


def read_entry(pak: Path, man: dict, path: str) -> bytes:
    crc, off, size = man[path]
    with open(pak, "rb") as f:
        f.seek(off)
        us, cs = struct.unpack("<II", f.read(8))
        raw = f.read(cs if cs else us)
    data = zlib.decompress(raw) if cs else raw
    if len(data) != size or zlib.crc32(data) != crc:
        sys.exit(f"{path}: payload does not match its manifest record")
    return data


# ---- layouts ------------------------------------------------------------------

def run(cmd: list) -> None:
    r = subprocess.run([str(c) for c in cmd], capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.exit(f"command failed: {cmd}\n{r.stdout}\n{r.stderr}")


def decompile(packer: Path, raw: bytes, tmp: Path, name: str) -> str:
    src = tmp / f"{name}.BINLAYOUT"
    dst = tmp / f"{name}.txt"
    src.write_bytes(raw)
    run([packer, "decompile-layout", src, dst])
    return dst.read_bytes().decode("utf-16")


def compile_text(packer: Path, text: str, tmp: Path, name: str, newline: str) -> bytes:
    src = tmp / f"{name}.LAYOUT"
    dst = tmp / f"{name}.out"
    src.write_bytes(b"\xff\xfe" + text.replace("\n", newline).encode("utf-16-le"))
    run([packer, "compile-layout", src, dst])
    return dst.read_bytes()


def replace_once(text: str, old: str, new: str) -> str:
    n = text.count(old)
    if n != 1:
        sys.exit(f"expected exactly one {old[:60]!r}, found {n}")
    return text.replace(old, new)


def edit_login(text: str) -> str:
    for old, new, _ in NOTICE:
        text = replace_once(text, f"<STRING>TEXT:{old}\n", f"<STRING>TEXT:{new}\n")
    old, new, _ = TITLE
    n = text.count(f"<STRING>TEXT:{old}\n")
    if n != TITLE_COUNT:
        sys.exit(f"title: expected {TITLE_COUNT} {old!r}, found {n}")
    return text.replace(f"<STRING>TEXT:{old}\n", f"<STRING>TEXT:{new}\n")


# ---- button image -------------------------------------------------------------

def button_dds() -> bytes:
    """login_button.png on a transparent 512x128 canvas, as an uncompressed A4R4G4B4 DDS
    laid out like the original (128-byte header, no mipmaps, pitch 1024)."""
    from PIL import Image

    png = Image.open(BUTTON_PNG).convert("RGBA")
    canvas = Image.new("RGBA", (DDS_W, DDS_H), (0, 0, 0, 0))
    canvas.paste(png, (0, 0))
    q = lambda v: (v * 15 + 127) // 255          # 8-bit -> 4-bit, rounded
    px = bytearray()
    for r, g, b, a in canvas.getdata():
        px += struct.pack("<H", (q(a) << 12) | (q(r) << 8) | (q(g) << 4) | q(b))
    # DDSD_CAPS|HEIGHT|WIDTH|PITCH|PIXELFORMAT; DDPF_ALPHAPIXELS|DDPF_RGB; DDSCAPS_TEXTURE
    hdr = struct.pack("<4sIIIIIII44x", b"DDS ", 124, 0x100F, DDS_H, DDS_W, DDS_W * 2, 0, 0)
    hdr += struct.pack("<IIIIIIII", 32, 0x41, 0, 16, 0x0F00, 0x00F0, 0x000F, 0xF000)
    hdr += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)
    assert len(hdr) == 128
    return hdr + bytes(px)


def edit_serverlist(text: str) -> str:
    lines = text.split("\n")
    at = [i for i, l in enumerate(lines) if l.strip() == "<STRING>NAME:AccountDetails"]
    if len(at) != 1:
        sys.exit(f"AccountDetails: expected one, found {len(at)}")
    i = at[0]
    while lines[i].strip() != "[BASEOBJECT]":
        i -= 1
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip("\t"))]
    j = i + 1
    while not (lines[j].strip() == "[/BASEOBJECT]" and lines[j].startswith(indent + "[")):
        j += 1
    block = [indent + l for l in SERVER_LINE]
    return "\n".join(lines[: j + 1] + block + lines[j + 1:])


def splice_ops(a: bytes, b: bytes) -> list[dict]:
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    ops = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        ops.append({"at": i1, "del": i2 - i1, "ins": base64.b64encode(b[j1:j2]).decode("ascii")})
    return ops


def apply_ops(a: bytes, ops: list[dict]) -> bytes:
    out, pos = bytearray(), 0
    for op in ops:
        out += a[pos:op["at"]]
        out += base64.b64decode(op["ins"])
        pos = op["at"] + op["del"]
    out += a[pos:]
    return bytes(out)


def build_layout(packer: Path, raw: bytes, tmp: Path, name: str, edit) -> tuple[bytes, list[dict]]:
    text = decompile(packer, raw, tmp, name)
    newline = "\r\n" if "\r\n" in text else "\n"
    text = text.replace("\r\n", "\n")
    if compile_text(packer, text, tmp, name + "_rt", newline) != raw:
        sys.exit(f"{name}: the packer does not round-trip the original; refusing to diff")
    new = compile_text(packer, edit(text), tmp, name, newline)
    ops = splice_ops(raw, new)
    if apply_ops(raw, ops) != new:
        sys.exit(f"{name}: splice ops do not reproduce the edited payload")
    return new, ops


# ---- translations -------------------------------------------------------------

def bindat_strings(data: bytes) -> list[str]:
    ver, count, _ = struct.unpack_from("<III", data, 0)
    if ver != 2:
        sys.exit(f"BINDAT version {ver}")
    o, out = 12, []
    for k in range(count):
        if k:
            o += 4
        n = struct.unpack_from("<H", data, o)[0]; o += 2
        out.append(data[o:o + 2 * n].decode("utf-16-le")); o += 2 * n
    return out


def translation_pairs(packer: Path, raw: bytes, tmp: Path, lang: str) -> list[list[str]]:
    src = tmp / f"{lang}.BINDAT"
    dst = tmp / f"{lang}.DAT"
    src.write_bytes(raw)
    run([packer, "decompile-dat", src, dst])
    text = dst.read_bytes().decode("utf-16").replace("\r\n", "\n")
    table = bindat_strings(raw)
    pairs = []
    for old_en, new_en, tr in NOTICE + [TITLE]:
        key = f"<STRING>ORIGINAL:{old_en}\n"
        if text.count(key) != 1:
            sys.exit(f"{lang}: expected one ORIGINAL {old_en[:40]!r}")
        after = text.split(key, 1)[1].lstrip("\t")
        if not after.startswith("<STRING>TRANSLATION:"):
            sys.exit(f"{lang}: no TRANSLATION after {old_en[:40]!r}")
        old_tr = after[len("<STRING>TRANSLATION:"):].split("\n", 1)[0]
        for s in (old_en, old_tr):
            uses = text.count(f":{s}\n")
            if table.count(s) != 1 or uses != 1:
                sys.exit(f"{lang}: {s[:40]!r} is shared ({table.count(s)} in table, {uses} uses)")
        pairs += [[old_en, new_en], [old_tr, tr[lang]]]
    return pairs


# ---- main ---------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pak", type=Path, default=Path(os.environ.get("TL2_DATA_PAK", DEFAULT_PAK)))
    ap.add_argument("--packer", type=Path, default=Path(os.environ.get("TL2_PACKER", DEFAULT_PACKER)))
    a = ap.parse_args()
    man_path = a.pak.with_name(a.pak.name + ".MAN")

    step(f"manifest {man_path}")
    man = read_manifest(man_path)
    files = []
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for path, name, edit, extra in [
            (LOGIN, "LOGIN", edit_login, {}),
            (SERVERLIST, "SERVERLIST", edit_serverlist, {"placeholder": PLACEHOLDER}),
        ]:
            step(f"{path}: decompile, edit, compile, diff")
            raw = read_entry(a.pak, man, path)
            new, ops = build_layout(a.packer, raw, tmp, name, edit)
            if extra.get("placeholder") and new.count(PLACEHOLDER.encode("utf-16-le")) != 1:
                sys.exit("placeholder not found exactly once in the compiled SERVERLIST")
            ins = sum(len(base64.b64decode(o["ins"])) for o in ops)
            step(f"  {len(raw)} -> {len(new)} bytes, {len(ops)} ops, {ins} bytes inserted")
            files.append({"path": path, "kind": "splice", "origSize": man[path][2], "origCrc": man[path][0],
                          "ops": ops, **extra})
        for path in TRANSLATIONS:
            lang = path.split("/")[2]
            step(f"{path}: collect string pairs")
            raw = read_entry(a.pak, man, path)
            pairs = translation_pairs(a.packer, raw, tmp, lang)
            files.append({"path": path, "kind": "strings", "origSize": man[path][2], "origCrc": man[path][0],
                          "replace": pairs})
        dds = button_dds()
        z = zlib.compress(dds, 9)
        for path in BUTTON_DDS:
            read_entry(a.pak, man, path)                       # checks the original is what we expect
            step(f"{path}: our button, {len(dds)} bytes, {len(z)} compressed")
            files.append({"path": path, "kind": "replace", "origSize": man[path][2], "origCrc": man[path][0],
                          "data": base64.b64encode(z).decode("ascii")})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"version": 1, "placeholderLength": PLACEHOLDER_LEN, "files": files}
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    step(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
