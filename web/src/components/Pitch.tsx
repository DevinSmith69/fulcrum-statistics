import { useEffect, useRef } from "react";
import { PITCH_L, PITCH_W, type PitchModel } from "../lib/pitchModel";
import type { Team } from "../types";

const MARGIN = 3; // metres of run-off drawn around the pitch
const SIGNAL = "#3de0ff";

interface Props {
  model: PitchModel;
  followedPlayerId?: string | null;
  className?: string;
}

/**
 * Top-down 2D pitch. Reads the PitchModel every animation frame and eases dots and ball towards their
 * targets, so the picture stays smooth even though events arrive in bursts.
 */
export function Pitch({ model, followedPlayerId, className = "" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const followRef = useRef(followedPlayerId);
  followRef.current = followedPlayerId;

  useEffect(() => {
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const pos = new Map<string, { x: number; y: number }>();
    const ball = { x: 52.5, y: 34 };
    const trail: { x: number; y: number }[] = [];
    let raf = 0;
    let last = performance.now();
    let version = -1;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const resize = () => {
      const r = canvas.getBoundingClientRect();
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = Math.max(1, Math.round(r.width * dpr));
      canvas.height = Math.max(1, Math.round(r.height * dpr));
    };
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    resize();

    const frame = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const W = canvas.width;
      const H = canvas.height;
      const scale = Math.min(W / (PITCH_L + 2 * MARGIN), H / (PITCH_W + 2 * MARGIN));
      const ox = (W - PITCH_L * scale) / 2;
      const oy = (H - PITCH_W * scale) / 2;
      const X = (x: number) => ox + x * scale;
      const Y = (y: number) => oy + y * scale;

      if (model.version !== version) {
        version = model.version;
        pos.clear();
      }

      ctx.clearRect(0, 0, W, H);
      drawPitch(ctx, X, Y, scale, model.home, model.away, model.period);

      // traces (passes, shots), fading over ~1.8 s
      for (const t of model.traces) {
        const age = (now - t.at) / 1800;
        if (age >= 1) continue;
        const a = 1 - age;
        ctx.save();
        ctx.lineCap = "round";
        if (t.kind === "shot" || t.kind === "goal") {
          ctx.strokeStyle = hexA(SIGNAL, 0.9 * a);
          ctx.lineWidth = (t.kind === "goal" ? 3 : 2) * (scale / 9);
          ctx.setLineDash([]);
        } else if (t.kind === "carry") {
          ctx.strokeStyle = `rgba(243,246,250,${0.18 * a})`;
          ctx.lineWidth = 1.5 * (scale / 9);
          ctx.setLineDash([2 * scale / 9, 4 * scale / 9]);
        } else {
          ctx.strokeStyle = t.ok ? `rgba(243,246,250,${0.42 * a})` : `rgba(123,134,154,${0.5 * a})`;
          ctx.lineWidth = 1.6 * (scale / 9);
          ctx.setLineDash(t.ok ? [] : [4 * scale / 9, 4 * scale / 9]);
        }
        ctx.beginPath();
        ctx.moveTo(X(t.x), Y(t.y));
        ctx.lineTo(X(t.ex), Y(t.ey));
        ctx.stroke();
        if (t.kind === "goal") {
          ctx.setLineDash([]);
          ctx.strokeStyle = hexA(SIGNAL, 0.8 * a);
          ctx.lineWidth = 2 * (scale / 9);
          ctx.beginPath();
          ctx.arc(X(t.ex), Y(t.ey), (2 + age * 14) * scale, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.restore();
      }

      // players
      const ease = reduced ? 1 : 1 - Math.exp(-dt * 3.2);
      const r = Math.max(5, 1.45 * scale);
      const dots = model.targets();
      for (const d of dots) {
        const p = pos.get(d.id) ?? { x: d.x, y: d.y };
        p.x += (d.x - p.x) * ease;
        p.y += (d.y - p.y) * ease;
        pos.set(d.id, p);
        const team = d.side === "home" ? model.home : model.away;
        const followed = followRef.current === d.id;
        ctx.beginPath();
        ctx.arc(X(p.x), Y(p.y), r, 0, Math.PI * 2);
        ctx.fillStyle = team?.primary_colour ?? "#888";
        ctx.fill();
        ctx.lineWidth = Math.max(1.2, r * 0.16);
        ctx.strokeStyle = team?.secondary_colour ?? "#fff";
        ctx.stroke();
        if (followed) {
          ctx.beginPath();
          ctx.arc(X(p.x), Y(p.y), r + Math.max(3, r * 0.45), 0, Math.PI * 2);
          ctx.strokeStyle = SIGNAL;
          ctx.lineWidth = Math.max(1.5, r * 0.22);
          ctx.stroke();
        }
        if (r >= 8) {
          ctx.fillStyle = team?.secondary_colour ?? "#fff";
          ctx.font = `600 ${Math.round(r * 1.05)}px "Barlow Condensed", sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(String(d.number), X(p.x), Y(p.y) + r * 0.05);
        }
        if (followed) {
          ctx.font = `600 ${Math.round(Math.max(11, r * 1.05))}px "Barlow", sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "bottom";
          ctx.fillStyle = SIGNAL;
          ctx.fillText(d.name, X(p.x), Y(p.y) - r - Math.max(5, r * 0.6));
        }
      }

      // ball with a short trail
      const be = reduced ? 1 : 1 - Math.exp(-dt * 7);
      ball.x += (model.ball[0] - ball.x) * be;
      ball.y += (model.ball[1] - ball.y) * be;
      trail.push({ x: ball.x, y: ball.y });
      if (trail.length > 10) trail.shift();
      trail.forEach((t, i) => {
        ctx.beginPath();
        ctx.arc(X(t.x), Y(t.y), Math.max(1.5, 0.45 * scale) * (i / trail.length), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(243,246,250,${0.18 * (i / trail.length)})`;
        ctx.fill();
      });
      ctx.save();
      ctx.shadowColor = "rgba(61,224,255,0.65)";
      ctx.shadowBlur = Math.max(6, scale * 0.9);
      ctx.beginPath();
      ctx.arc(X(ball.x), Y(ball.y), Math.max(3.5, 0.62 * scale), 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.restore();

      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [model]);

  return <canvas ref={canvasRef} className={`block h-full w-full ${className}`} aria-label="Live pitch view" role="img" />;
}

function hexA(hex: string, a: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

function drawPitch(
  ctx: CanvasRenderingContext2D,
  X: (x: number) => number,
  Y: (y: number) => number,
  s: number,
  home: Team | null,
  away: Team | null,
  period: number,
) {
  // surface with mowing stripes
  ctx.fillStyle = "#0c1915";
  ctx.fillRect(X(-MARGIN), Y(-MARGIN), (PITCH_L + 2 * MARGIN) * s, (PITCH_W + 2 * MARGIN) * s);
  for (let i = 0; i < 14; i++) {
    if (i % 2) continue;
    ctx.fillStyle = "rgba(201,227,216,0.022)";
    ctx.fillRect(X((i * PITCH_L) / 14), Y(0), (PITCH_L / 14) * s, PITCH_W * s);
  }
  ctx.strokeStyle = "rgba(201,227,216,0.30)";
  ctx.lineWidth = Math.max(1, s * 0.14);
  ctx.setLineDash([]);
  const rect = (x: number, y: number, w: number, h: number) => ctx.strokeRect(X(x), Y(y), w * s, h * s);
  rect(0, 0, PITCH_L, PITCH_W);
  ctx.beginPath();
  ctx.moveTo(X(52.5), Y(0));
  ctx.lineTo(X(52.5), Y(68));
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(X(52.5), Y(34), 9.15 * s, 0, Math.PI * 2);
  ctx.stroke();
  const spot = (x: number, y: number) => {
    ctx.beginPath();
    ctx.arc(X(x), Y(y), Math.max(1.5, 0.3 * s), 0, Math.PI * 2);
    ctx.fillStyle = "rgba(201,227,216,0.4)";
    ctx.fill();
  };
  spot(52.5, 34);
  for (const left of [true, false]) {
    const gx = left ? 0 : PITCH_L;
    const dir = left ? 1 : -1;
    rect(left ? 0 : PITCH_L - 16.5, 34 - 20.16, 16.5, 40.32);
    rect(left ? 0 : PITCH_L - 5.5, 34 - 9.16, 5.5, 18.32);
    spot(gx + dir * 11, 34);
    ctx.beginPath();
    const a = Math.acos(5.5 / 9.15);
    if (left) ctx.arc(X(11), Y(34), 9.15 * s, -a, a);
    else ctx.arc(X(PITCH_L - 11), Y(34), 9.15 * s, Math.PI - a, Math.PI + a);
    ctx.stroke();
    // goal
    ctx.strokeStyle = "rgba(201,227,216,0.55)";
    ctx.strokeRect(X(left ? -1.8 : PITCH_L), Y(34 - 3.66), 1.8 * s, 7.32 * s);
    ctx.strokeStyle = "rgba(201,227,216,0.30)";
  }
  // which way each side attacks: in period 1 home starts on the left, attacking towards x = 105
  const leftTeam = period % 2 === 1 ? home : away;
  const rightTeam = period % 2 === 1 ? away : home;
  ctx.font = `600 ${Math.max(10, Math.round(s * 1.35))}px "Barlow Condensed", sans-serif`;
  ctx.fillStyle = "rgba(201,227,216,0.22)";
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  if (leftTeam) ctx.fillText(`${leftTeam.short_name} →`, X(1.2), Y(1));
  ctx.textAlign = "right";
  if (rightTeam) ctx.fillText(`← ${rightTeam.short_name}`, X(PITCH_L - 1.2), Y(1));
}
