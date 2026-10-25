"""Record the demo video's screen clips from a long-lived headless Chromium (one MP4 per storyboard shot).

    # once, in the background: a 1920x1080 headless browser that keeps the match open between shots
    uv run --with playwright python scripts/video/rec.py serve
    # look, act, record
    uv run --with playwright python scripts/video/rec.py snap [name]
    uv run --with playwright python scripts/video/rec.py do '[["goto", "#/split"], ["key", "n"]]'
    uv run --with playwright python scripts/video/rec.py shot 3a '[["key", "n"], ["wait", 8]]'

Frames come from Chrome's own screencast (JPEG q95, only when the page repaints, with timestamps) and are
encoded by ffmpeg into constant-30 fps H.264 (CRF 16), so text stays crisp and the timing matches what played.
Actions: goto <hash or url> · key <key> · type <text> · click <selector> · hover <selector> · move <x> <y> ·
scroll <dy> · wait <s> · js <expression>. Selectors are Playwright selectors (e.g. "text=Fact trail").
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "video" / "clips"
SNAPS = ROOT / "video" / "snaps"
PORT = 9333
SCALE = int(os.getenv("FULCRUM_VIDEO_SCALE", "2"))  # 2 = 4K frames: crisp push-ins, sharper 1080p export
SITE = os.getenv("FULCRUM_VIDEO_SITE", "http://localhost:5173/")  # the deployed overlay to record
FFMPEG = shutil.which("ffmpeg") or str(
    next((Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages").glob("Gyan.FFmpeg*/*/bin/ffmpeg.exe"))
)


# A visible mouse pointer (headless has none) with a soft ripple on click, drawn above everything.
CURSOR_JS = """
(() => {
  const add = () => {
    if (document.getElementById('__rec_cursor')) return;
    const c = document.createElement('div');
    c.id = '__rec_cursor';
    c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2 L4 19 L8.6 14.9 L11.6 21.6 ' +
      'L14.3 20.4 L11.4 13.9 L17.6 13.9 Z" fill="#fff" stroke="#0b0f17" stroke-width="1.4" ' +
      'stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position: 'fixed', left: '-40px', top: '-40px', zIndex: 2147483647,
      pointerEvents: 'none', filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.6))'});
    document.documentElement.appendChild(c);
    addEventListener('mousemove', e => { c.style.left = (e.clientX - 4) + 'px'; c.style.top = (e.clientY - 2) + 'px'; },
      true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div');
      Object.assign(r.style, {position: 'fixed', left: (e.clientX - 18) + 'px', top: (e.clientY - 18) + 'px',
        width: '36px', height: '36px', borderRadius: '50%', border: '2px solid #3de0ff', zIndex: 2147483646,
        pointerEvents: 'none', transition: 'transform .45s ease-out, opacity .45s ease-out'});
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.8)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 500);
    }, true);
  };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', add); else add();
})();
"""


def serve() -> None:
    """Own a 1920x1080 headless browser and run command batches posted to localhost:PORT (one at a time)."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        str(ROOT / "video" / ".profile"),
        headless=True,
        viewport={"width": 1920, "height": 1080},
        device_scale_factor=SCALE,
        args=["--hide-scrollbars", "--autoplay-policy=no-user-gesture-required"],
    )
    ctx.add_init_script(CURSOR_JS)  # headless Chrome draws no pointer
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto(SITE)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                msg = handle(pg, body)
                code = 200
            except Exception as e:  # report, keep serving
                msg, code = f"error: {e}", 500
            self.send_response(code)
            self.end_headers()
            self.wfile.write(msg.encode())

        def log_message(self, *args) -> None:
            pass

    print(f"recorder on :{PORT}", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


def handle(pg, body: dict) -> str:
    out: list[str] = []
    if body["cmd"] == "shot":
        out.append(record(pg, body["name"], body["actions"], out))
    else:
        run_actions(pg, body.get("actions", []), out)
    if body.get("snap"):
        SNAPS.mkdir(parents=True, exist_ok=True)
        path = SNAPS / f"{body['snap']}.png"
        pg.screenshot(path=str(path))
        out.append(str(path))
    return "\n".join(str(x) for x in out)


def run_actions(pg, actions: list[list], out: list[str]) -> None:
    for a in actions:
        op, *args = a
        if op == "goto":
            target = args[0]
            if target.startswith("#") or target.startswith("?"):
                base = pg.url.split("#")[0] if target.startswith("#") else SITE
                target = base + target
            pg.goto(target)
        elif op == "key":
            pg.keyboard.press(args[0])
        elif op == "type":
            pg.keyboard.type(args[0], delay=args[1] if len(args) > 1 else 45)
        elif op == "click":
            pg.locator(args[0]).first.click()
        elif op == "hover":
            pg.locator(args[0]).first.hover()
        elif op == "move":
            pg.mouse.move(args[0], args[1], steps=args[2] if len(args) > 2 else 25)
        elif op == "scroll":  # scroll <dy> [steps over ~0.6 s]: smooth when steps > 1
            steps = args[1] if len(args) > 1 else 1
            for _ in range(steps):
                pg.mouse.wheel(0, args[0] / steps)
                pg.wait_for_timeout(600 / steps)
        elif op == "wait":
            pg.wait_for_timeout(args[0] * 1000)
        elif op == "until":  # until <js condition> [timeout s]: e.g. a moment label appearing
            pg.wait_for_function(args[0], timeout=(args[1] if len(args) > 1 else 60) * 1000, polling=50)
        elif op == "js":
            out.append(json.dumps(pg.evaluate(args[0]), default=str)[:4000])
        else:
            raise ValueError(f"unknown action {op}")


def record(pg, name: str, actions: list[list], out_log: list[str]) -> str:
    """Run `actions` while capturing the screencast; returns the encoded MP4."""
    frames_dir = ROOT / "video" / ".frames" / name
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    cdp = pg.context.new_cdp_session(pg)
    frames: list[tuple[float, Path]] = []

    def on_frame(ev: dict) -> None:
        path = frames_dir / f"{len(frames):06d}.jpg"
        path.write_bytes(base64.b64decode(ev["data"]))
        frames.append((ev["metadata"]["timestamp"], path))
        cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})

    cdp.on("Page.screencastFrame", on_frame)
    size = {"maxWidth": 1920 * SCALE, "maxHeight": 1080 * SCALE}
    cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95, **size})
    pg.wait_for_timeout(300)
    run_actions(pg, actions, out_log)
    end = time.time()
    cdp.send("Page.stopScreencast")
    pg.wait_for_timeout(200)
    if not frames:
        raise SystemExit("no frames captured")
    # ffconcat with real durations: the screencast only sends a frame when the page repaints
    lines = ["ffconcat version 1.0"]
    stamps = [t for t, _ in frames] + [max(end, frames[-1][0] + 0.04)]
    for i, (_, path) in enumerate(frames):
        lines += [f"file '{path.as_posix()}'", f"duration {max(0.001, stamps[i + 1] - stamps[i]):.4f}"]
    lines.append(f"file '{frames[-1][1].as_posix()}'")
    concat = frames_dir / "list.ffconcat"
    concat.write_text("\n".join(lines))
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{name}.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
         "-vf", f"scale={1920 * SCALE}:{1080 * SCALE}:flags=lanczos,fps=30",
         "-c:v", "libx264", "-preset", "slow", "-crf", "16",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        check=True,
    )  # fmt: skip
    span = stamps[-1] - stamps[0]
    shutil.rmtree(frames_dir, ignore_errors=True)
    return f"{out.name}: {len(frames)} frames over {span:.1f} s ({len(frames) / max(span, 0.1):.0f} fps captured)"


def main() -> None:
    cmd, *rest = sys.argv[1:] or ["help"]
    if cmd == "serve":
        serve()
        return
    if cmd not in ("snap", "do", "shot"):
        print(__doc__)
        return
    import urllib.request

    rest = [Path(r[1:]).read_text(encoding="utf-8") if r.startswith("@") else r for r in rest]  # @file.json
    if cmd == "snap":
        body = {"cmd": "do", "snap": rest[0] if rest else "now"}
    elif cmd == "do":
        body = {"cmd": "do", "actions": json.loads(rest[0]), "snap": "now" if "--snap" in rest else None}
    else:
        body = {"cmd": "shot", "name": rest[0], "actions": json.loads(rest[1]) if len(rest) > 1 else [["wait", 5]]}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}", data=json.dumps(body).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            print(r.read().decode())
    except urllib.error.HTTPError as e:
        print(e.read().decode())
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
