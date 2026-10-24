import { API_URL } from "./config";
import { LANG_TAG, pickVoice } from "./speechQueue";

/** Which engine speaks: Azure AI Speech neural voice (via POST /api/speech) or the browser's speechSynthesis. */
export type SpeechEngine = "azure" | "browser" | "none";

/**
 * Plays one line at a time. Tries the API's neural voice first; a 503 (Azure Speech not configured) switches to
 * the browser's speechSynthesis for the rest of the session. Any other failure falls back for that line only.
 */
export class SpeechPlayer {
  private azure: "unknown" | "yes" | "no" = "unknown";
  private abort: AbortController | null = null;
  private audio: HTMLAudioElement | null = null;
  private done: (() => void) | null = null;
  /** Called when the browser refuses to play audio before a user gesture (autoplay policy). */
  onBlocked: (() => void) | null = null;

  /** The engine the next line will use. */
  get engine(): SpeechEngine {
    if (this.azure !== "no") return "azure";
    return typeof window !== "undefined" && "speechSynthesis" in window ? "browser" : "none";
  }

  /** Ask the API once whether neural voice is configured (saves a failed POST per session). */
  async probe(): Promise<SpeechEngine> {
    try {
      const r = await fetch(`${API_URL}/api/speech`);
      if (r.ok) this.azure = (await r.json()).enabled ? "yes" : "no";
      else this.azure = "no";
    } catch {
      this.azure = "no";
    }
    return this.engine;
  }

  /** Speak `text` in `lang`; resolves when it finishes or is stopped. Never rejects. */
  async speak(text: string, lang: string): Promise<void> {
    this.stop();
    if (this.azure !== "no") {
      const played = await this.speakAzure(text, lang);
      if (played !== "fallback") return;
    }
    await this.speakBrowser(text, lang);
  }

  /** Stop the current line immediately (pending fetch, audio element and browser speech). */
  stop(): void {
    this.abort?.abort();
    this.abort = null;
    if (this.audio) {
      this.audio.pause();
      this.audio = null;
    }
    if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
    const done = this.done;
    this.done = null;
    done?.();
  }

  private async speakAzure(text: string, lang: string): Promise<"played" | "stopped" | "fallback"> {
    const abort = new AbortController();
    this.abort = abort;
    let res: Response;
    try {
      res = await fetch(`${API_URL}/api/speech`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text, language: lang }),
        signal: abort.signal,
      });
    } catch {
      return abort.signal.aborted ? "stopped" : "fallback";
    }
    if (res.status === 503) this.azure = "no";
    if (!res.ok) return "fallback";
    this.azure = "yes";
    const url = URL.createObjectURL(await res.blob());
    if (abort.signal.aborted) {
      URL.revokeObjectURL(url);
      return "stopped";
    }
    const audio = new Audio(url);
    this.audio = audio;
    await new Promise<void>((resolve) => {
      this.done = resolve;
      audio.onended = () => resolve();
      audio.onerror = () => resolve();
      audio.play().catch((e: unknown) => {
        if ((e as Error)?.name === "NotAllowedError") this.onBlocked?.();
        resolve();
      });
    });
    if (this.audio === audio) this.audio = null;
    URL.revokeObjectURL(url);
    return "played";
  }

  private speakBrowser(text: string, lang: string): Promise<void> {
    if (typeof window === "undefined" || !("speechSynthesis" in window)) return Promise.resolve();
    const synth = window.speechSynthesis;
    const u = new SpeechSynthesisUtterance(text);
    u.lang = LANG_TAG[lang] ?? lang;
    u.voice = pickVoice(synth.getVoices(), lang);
    u.rate = 1.05;
    return new Promise<void>((resolve) => {
      // Some engines never fire `end`; don't let the queue hang on them.
      const guard = window.setTimeout(() => finish(), 4000 + text.split(/\s+/).length * 600);
      const finish = () => {
        window.clearTimeout(guard);
        if (this.done === finish) this.done = null;
        resolve();
      };
      this.done = finish;
      u.onend = finish;
      u.onerror = (e) => {
        if (e.error === "not-allowed") this.onBlocked?.();
        finish();
      };
      synth.speak(u);
    });
  }
}
