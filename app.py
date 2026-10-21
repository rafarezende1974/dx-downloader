# DX Downloader - Baixador de vídeos e áudios (Windows)
# DX Labs - usa yt-dlp como motor (YouTube, TikTok, Instagram, Facebook, X, Vimeo e +1000 sites)
# GUI em Tkinter puro (sem dependências extras) + ffmpeg embutido no .exe para juntar vídeo/áudio e gerar MP3.

import os
import sys
import queue
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "DX Downloader"
APP_VERSION = "1.0.0"

BG = "#0B0E13"
BG2 = "#151A22"
FG = "#E8ECF1"
ACCENT = "#E63946"   # vermelho (programa/ação principal)
ACCENT2 = "#2EC4B6"  # verde-água (sucesso/progresso)
MUTED = "#8A94A6"


def bundled_ffmpeg() -> str | None:
    """Localiza o ffmpeg.exe embutido pelo PyInstaller ou no PATH."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys._MEIPASS) / "ffmpeg.exe")  # type: ignore[attr-defined]
        candidates.append(Path(sys.executable).parent / "ffmpeg.exe")
    candidates.append(Path(__file__).parent / "ffmpeg.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    return None  # yt-dlp procura no PATH se ffmpeg_location for None


def default_download_dir() -> Path:
    d = Path.home() / "Downloads" / "DX Downloader"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = Path.home()
    return d


# ------------------------- lógica de download (testável sem GUI) -------------------------

QUALITY_HEIGHTS = {
    "Melhor disponível": None,
    "Até 1080p (Full HD)": 1080,
    "Até 720p (HD)": 720,
    "Até 480p": 480,
    "Até 360p": 360,
}


def build_ydl_opts(mode: str, quality_label: str, out_dir: str,
                   playlist: bool, cookies_browser: str | None,
                   embed_meta: bool, progress_hook, ffmpeg_path: str | None) -> dict:
    """Monta as opções do yt-dlp conforme as escolhas da tela.

    mode: 'video' | 'mp3' | 'm4a'
    """
    opts: dict = {
        "outtmpl": str(Path(out_dir) / "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": not playlist,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
        "windowsfilenames": True,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": True,
    }
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    if mode == "video":
        h = QUALITY_HEIGHTS.get(quality_label)
        if h:
            fmt = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
        else:
            fmt = "bv*+ba/b"
        opts["format"] = fmt
        opts["merge_output_format"] = "mp4"
        if embed_meta:
            opts["postprocessors"] = [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
            opts["writethumbnail"] = True
    elif mode == "mp3":
        opts["format"] = "ba/b"
        pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        if embed_meta:
            pp += [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
            opts["writethumbnail"] = True
        opts["postprocessors"] = pp
    elif mode == "m4a":
        opts["format"] = "ba[ext=m4a]/ba/b"
        pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192"}]
        if embed_meta:
            pp += [{"key": "FFmpegMetadata", "add_metadata": True}]
        opts["postprocessors"] = pp
    return opts


class CancelledError(Exception):
    pass


# ------------------------- GUI -------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION} — DX Labs")
        self.geometry("780x660")
        self.minsize(680, 560)
        self.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.last_dir: Path = default_download_dir()

        self._build_style()
        self._build_ui()
        self.after(100, self._poll_events)

    # ---------- estilo ----------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=BG2)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 17, "bold"))
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=BG2, foreground=FG, font=("Segoe UI", 10))
        style.configure("CardSub.TLabel", background=BG2, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TRadiobutton", background=BG2, foreground=FG, font=("Segoe UI", 10))
        style.configure("TCheckbutton", background=BG2, foreground=FG, font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Bar.Horizontal.TProgressbar", troughcolor=BG2, background=ACCENT2, thickness=14)

    # ---------- tela ----------
    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        ttk.Label(root, text="⬇  DX DOWNLOADER", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text="Baixe vídeos e áudios do YouTube, TikTok, Instagram, Facebook, X, Vimeo e +1.000 sites.  •  DX Labs",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 10))

        card = ttk.Frame(root, style="Card.TFrame", padding=12)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text="Cole um ou mais links (um por linha):", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.url_text = tk.Text(card, height=5, bg="#0E1219", fg=FG, insertbackground=FG,
                                font=("Segoe UI", 10), relief="flat", wrap="word")
        self.url_text.grid(row=1, column=0, sticky="ew", pady=(4, 8))

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Button(btns, text="Colar da área de transferência", command=self._paste).pack(side="left")
        ttk.Button(btns, text="Limpar", command=lambda: self.url_text.delete("1.0", "end")).pack(side="left", padx=(8, 0))

        # modo + qualidade
        opts_row = ttk.Frame(card, style="Card.TFrame")
        opts_row.grid(row=3, column=0, sticky="w")
        self.mode = tk.StringVar(value="video")
        ttk.Radiobutton(opts_row, text="Vídeo MP4", variable=self.mode, value="video",
                        command=self._sync_quality).pack(side="left")
        ttk.Radiobutton(opts_row, text="Áudio MP3", variable=self.mode, value="mp3",
                        command=self._sync_quality).pack(side="left", padx=(14, 0))
        ttk.Radiobutton(opts_row, text="Áudio M4A", variable=self.mode, value="m4a",
                        command=self._sync_quality).pack(side="left", padx=(14, 0))
        ttk.Label(opts_row, text="   Qualidade:", style="Card.TLabel").pack(side="left")
        self.quality = ttk.Combobox(opts_row, values=list(QUALITY_HEIGHTS.keys()), state="readonly", width=20)
        self.quality.set("Melhor disponível")
        self.quality.pack(side="left", padx=(4, 0))

        # pasta destino
        dest_row = ttk.Frame(card, style="Card.TFrame")
        dest_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        dest_row.columnconfigure(1, weight=1)
        ttk.Label(dest_row, text="Salvar em:", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.dest_var = tk.StringVar(value=str(self.last_dir))
        ttk.Entry(dest_row, textvariable=self.dest_var).grid(row=0, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(dest_row, text="Escolher…", command=self._choose_dir).grid(row=0, column=2)

        # opções extras
        extra = ttk.Frame(card, style="Card.TFrame")
        extra.grid(row=5, column=0, sticky="w", pady=(10, 0))
        self.playlist_var = tk.BooleanVar(value=False)
        self.meta_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(extra, text="Baixar playlist inteira", variable=self.playlist_var).pack(side="left")
        ttk.Checkbutton(extra, text="Embutir capa/metadados", variable=self.meta_var).pack(side="left", padx=(14, 0))
        ttk.Label(extra, text="   Cookies do navegador (p/ Instagram/TikTok com login):", style="Card.TLabel").pack(side="left")
        self.cookies_box = ttk.Combobox(extra, values=["Nenhum", "chrome", "edge", "firefox", "brave"],
                                        state="readonly", width=9)
        self.cookies_box.set("Nenhum")
        self.cookies_box.pack(side="left", padx=(4, 0))

        # ações + progresso (fora do card)
        actions = ttk.Frame(root, padding=(0, 12, 0, 0))
        actions.grid(row=4, column=0, sticky="ew")
        actions.columnconfigure(1, weight=1)
        self.btn_download = ttk.Button(actions, text="⬇  BAIXAR", style="Accent.TButton", command=self._start)
        self.btn_download.grid(row=0, column=0, sticky="w")
        self.btn_cancel = ttk.Button(actions, text="Cancelar", command=self._cancel, state="disabled")
        self.btn_cancel.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(actions, text="Abrir pasta", command=self._open_dir).grid(row=0, column=2, sticky="e")

        self.progress = ttk.Progressbar(root, style="Bar.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.grid(row=5, column=0, sticky="ew", pady=(10, 4))
        self.status_var = tk.StringVar(value="Pronto. Cole os links e clique em BAIXAR.")
        ttk.Label(root, textvariable=self.status_var, style="Sub.TLabel").grid(row=6, column=0, sticky="w")

        ttk.Label(root, text="Registro:", style="TLabel").grid(row=7, column=0, sticky="w", pady=(10, 2))
        self.log = tk.Text(root, height=9, bg="#0E1219", fg="#B9C2D0", insertbackground=FG,
                           font=("Consolas", 9), relief="flat", state="disabled", wrap="word")
        self.log.grid(row=8, column=0, sticky="nsew")
        root.rowconfigure(8, weight=1)

    # ---------- helpers de UI ----------
    def _sync_quality(self):
        self.quality.configure(state="readonly" if self.mode.get() == "video" else "disabled")

    def _paste(self):
        try:
            txt = self.clipboard_get()
        except Exception:
            txt = ""
        if txt.strip():
            cur = self.url_text.get("1.0", "end").strip()
            self.url_text.delete("1.0", "end")
            self.url_text.insert("1.0", (cur + "\n" + txt.strip()) if cur else txt.strip())

    def _choose_dir(self):
        d = filedialog.askdirectory(initialdir=self.dest_var.get() or str(Path.home()))
        if d:
            self.dest_var.set(d)

    def _open_dir(self):
        p = Path(self.dest_var.get())
        try:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(p)  # Windows
        except Exception:
            webbrowser.open(str(p))

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------- download ----------
    def _get_urls(self) -> list[str]:
        raw = self.url_text.get("1.0", "end")
        return [ln.strip() for ln in raw.splitlines() if ln.strip().startswith(("http://", "https://"))]

    def _start(self):
        urls = self._get_urls()
        if not urls:
            messagebox.showwarning(APP_NAME, "Cole pelo menos um link válido (começando com https://).")
            return
        out_dir = self.dest_var.get().strip()
        if not out_dir:
            messagebox.showwarning(APP_NAME, "Escolha a pasta onde salvar.")
            return
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        self.last_dir = Path(out_dir)

        self.cancel_flag.clear()
        self.btn_download.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.progress["value"] = 0

        cookies = None if self.cookies_box.get() == "Nenhum" else self.cookies_box.get()
        args = dict(
            urls=urls,
            mode=self.mode.get(),
            quality_label=self.quality.get(),
            out_dir=out_dir,
            playlist=bool(self.playlist_var.get()),
            cookies_browser=cookies,
            embed_meta=bool(self.meta_var.get()),
        )
        self.worker = threading.Thread(target=self._run_downloads, kwargs=args, daemon=True)
        self.worker.start()
        self._log(f"Iniciando: {len(urls)} link(s) • modo={args['mode']} • pasta={out_dir}")

    def _cancel(self):
        self.cancel_flag.set()
        self.status_var.set("Cancelando…")

    def _progress_hook(self, d: dict):
        if self.cancel_flag.is_set():
            raise CancelledError("Download cancelado pelo usuário.")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            pct = (done / total * 100) if total else 0
            self.events.put(("progress", pct, d.get("filename", ""), d.get("_speed_str", ""), d.get("_eta_str", "")))
        elif status == "finished":
            self.events.put(("log", f"✔ Download concluído: {Path(d.get('filename','')).name} — processando (juntar/extrair)…"))
        elif status == "started":
            self.events.put(("log", f"▶ Processando: {Path(str(d.get('filename',''))).name}"))

    def _run_downloads(self, urls, mode, quality_label, out_dir, playlist, cookies_browser, embed_meta):
        try:
            import yt_dlp  # importado aqui pra erro de instalação aparecer no log da tela
        except Exception as e:
            self.events.put(("error", f"yt-dlp não encontrado: {e}"))
            return

        ffmpeg_path = bundled_ffmpeg()
        if not ffmpeg_path and mode in ("video", "mp3", "m4a"):
            self.events.put(("log", "⚠ ffmpeg.exe não localizado ao lado do app — vídeos separados de áudio podem não juntar. Use o .exe oficial do build."))

        ok, fail = 0, 0
        for i, url in enumerate(urls, 1):
            if self.cancel_flag.is_set():
                break
            self.events.put(("status", f"Baixando {i}/{len(urls)}: {url}"))
            opts = build_ydl_opts(mode, quality_label, out_dir, playlist,
                                  cookies_browser, embed_meta, self._progress_hook, ffmpeg_path)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
                ok += 1
                self.events.put(("log", f"✅ Pronto: {url}"))
            except CancelledError:
                self.events.put(("log", "⏹ Cancelado pelo usuário."))
                break
            except Exception as e:
                fail += 1
                self.events.put(("log", f"❌ Falhou: {url}\n   Motivo: {str(e)[:300]}"))
        self.events.put(("done", ok, fail))

    # ---------- eventos da thread -> UI ----------
    def _poll_events(self):
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "progress":
                    _, pct, fname, speed, eta = ev
                    self.progress["value"] = pct
                    name = Path(fname).name if fname else ""
                    self.status_var.set(f"{pct:.0f}%  •  {speed}  •  falta {eta}  •  {name}")
                elif kind == "log":
                    self._log(ev[1])
                elif kind == "status":
                    self.status_var.set(ev[1])
                elif kind == "error":
                    self._log("❌ " + ev[1])
                    self.status_var.set("Erro — veja o registro.")
                    self._finish_ui()
                elif kind == "done":
                    _, ok, fail = ev
                    self.progress["value"] = 100 if ok and not fail else self.progress["value"]
                    self.status_var.set(f"Concluído: {ok} ok • {fail} falha(s). Arquivos em: {self.dest_var.get()}")
                    self._log(f"— Fim: {ok} concluído(s), {fail} falha(s) —")
                    self._finish_ui()
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _finish_ui(self):
        self.btn_download.configure(state="normal")
        self.btn_cancel.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
