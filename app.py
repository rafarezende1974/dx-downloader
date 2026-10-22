# DX Downloader - Baixador de vídeos e áudios (Windows)
# DX Labs - usa yt-dlp como motor (YouTube, TikTok, Instagram, Facebook, X, Vimeo e +1000 sites)
# GUI em Tkinter puro (sem dependências extras) + ffmpeg embutido no .exe para juntar vídeo/áudio e gerar MP3.
#
# v1.1.0 - correção do travamento ("não respondendo"):
#  - progresso do download LIMITADO (máx. ~3 atualizações/segundo) e a fila de eventos da tela
#    é esvaziada em lotes pequenos -> a thread principal nunca fica soterrada de atualizações.
#  - Colar reescrito: usa o mecanismo interno do Tk (<<Paste>>) e menu de botão direito;
#    a leitura direta da área de transferência virou só plano B, com proteção.
#  - Log em arquivo (%LOCALAPPDATA%\DXDownloader\dx-downloader.log) pra diagnóstico.

import os
import sys
import time
import ctypes
import queue
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_NAME = "DX Downloader"
APP_VERSION = "1.4.0"

BG = "#0B0E13"
BG2 = "#151A22"
FG = "#E8ECF1"
ACCENT = "#E63946"
ACCENT2 = "#2EC4B6"
MUTED = "#8A94A6"


def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    d = Path(base) / "DXDownloader"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        d = Path.home()
    return d


LOG_FILE = log_dir() / "dx-downloader.log"


def log_file(msg: str):
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
    except Exception:
        pass


CF_UNICODETEXT = 13


def read_clipboard_text_windows(timeout_s: float = 2.0) -> str | None:
    """Lê o texto da área de transferência DIRETO pela API do Windows (Win32),
    com tentativas limitadas — pensado pra rodar numa thread separada, NUNCA na tela.
    Retorna None se não houver texto ou se o clipboard estiver ocupado além do limite.
    Fora do Windows, retorna None (usado só nos testes)."""
    if sys.platform != "win32":
        return None
    # COM em modo STA nesta thread: sem isso, o clipboard entregue via OLE
    # (navegadores) pode voltar vazio quando lido fora da thread da janela.
    _com_ready = False
    try:
        hr = ctypes.windll.ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        _com_ready = hr in (0, 1)  # S_OK / S_FALSE
    except Exception:
        pass
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.restype = ctypes.c_bool
    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    deadline = time.monotonic() + timeout_s
    opened = False
    while time.monotonic() < deadline:
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.05)  # outro programa está com o clipboard aberto; tenta de novo sem travar nada
    if not opened:
        log_file("clipboard: OpenClipboard nao conseguiu dentro do limite")
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            result = ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(handle)
        return result
    finally:
        user32.CloseClipboard()
        if _com_ready:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


def read_clipboard_via_powershell(timeout_s: float = 6.0) -> str | None:
    """Lê o clipboard com o Get-Clipboard do PowerShell (o caminho .NET/STA que o
    próprio Windows usa — lê o clipboard OLE dos navegadores sem travar e sem voltar vazio)."""
    if sys.platform != "win32":
        return None
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"],
            capture_output=True, timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = r.stdout.decode("utf-8", errors="replace").rstrip("\r\n")
        log_file(f"powershell Get-Clipboard: rc={r.returncode} len={len(out)} "
                 f"stderr={r.stderr.decode('utf-8', errors='replace')[:200]!r}")
        return out or None
    except Exception as e:
        log_file(f"powershell Get-Clipboard falhou: {e}")
        return None


def read_clipboard_text() -> str | None:
    """Tenta os métodos em ordem e registra cada um no log: PowerShell, depois Win32."""
    txt = read_clipboard_via_powershell()
    if txt:
        log_file(f"clipboard ok via PowerShell ({len(txt)} caracteres)")
        return txt
    txt = read_clipboard_text_windows()
    if txt:
        log_file(f"clipboard ok via Win32 ({len(txt)} caracteres)")
        return txt
    log_file("clipboard: nenhum metodo retornou texto")
    return None


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
    return None


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


def has_ffprobe(ffmpeg_path: str | None) -> bool:
    """ffprobe fica ao lado do ffmpeg quando embutido; sem ele, nada de embutir capa/metadados."""
    if not ffmpeg_path:
        return False
    parent = Path(ffmpeg_path).parent
    return (parent / "ffprobe.exe").exists() or (parent / "ffprobe").exists()


def is_cloudflare_error(msg: str) -> bool:
    return any(s in msg for s in ("impersonate", "Cloudflare", "HTTP Error 403"))


def build_ydl_opts(mode: str, quality_label: str, out_dir: str,
                   playlist: bool, cookies_browser: str | None,
                   embed_meta: bool, progress_hook, ffmpeg_path: str | None,
                   embed_ok: bool = True, impersonate: str | None = None) -> dict:
    """Monta as opções do yt-dlp conforme as escolhas da tela. mode: 'video' | 'mp3' | 'm4a'
    embed_ok=False pula capa/metadados (quando falta o ffprobe) em vez de falhar o download.
    impersonate='chrome' imita o navegador (exige curl-cffi) — usado na retentativa anti-Cloudflare."""
    opts: dict = {
        "outtmpl": str(Path(out_dir) / "%(title)s [%(id)s].%(ext)s"),
        "noplaylist": not playlist,
        "retries": 10,
        "fragment_retries": 10,
        "socket_timeout": 30,
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
    if impersonate:
        opts["impersonate"] = impersonate
    embed = embed_meta and embed_ok

    if mode == "video":
        h = QUALITY_HEIGHTS.get(quality_label)
        if h:
            fmt = f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"
        else:
            fmt = "bv*+ba/b"
        opts["format"] = fmt
        opts["merge_output_format"] = "mp4"
        if embed:
            opts["postprocessors"] = [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
            opts["writethumbnail"] = True
    elif mode == "mp3":
        opts["format"] = "ba/b"
        pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
        if embed:
            pp += [
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail", "already_have_thumbnail": False},
            ]
            opts["writethumbnail"] = True
        opts["postprocessors"] = pp
    elif mode == "m4a":
        opts["format"] = "ba[ext=m4a]/ba/b"
        pp = [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "192"}]
        if embed:
            pp += [{"key": "FFmpegMetadata", "add_metadata": True}]
        opts["postprocessors"] = pp
    return opts


class CancelledError(Exception):
    pass


# ------------------------- GUI -------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        log_file(f"=== app iniciou v{APP_VERSION} ===")
        self.title(f"{APP_NAME}  v{APP_VERSION} — DX Labs")
        self.geometry("780x660")
        self.minsize(680, 560)
        self.configure(bg=BG)

        self.events: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.last_dir: Path = default_download_dir()
        # limitador de progresso (anti-travamento)
        self._prog_last_pct = -10.0
        self._prog_last_ts = 0.0
        self._paste_busy = False

        self._build_style()
        self._build_ui()
        self.after(100, self._poll_events)

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
        style.configure("TRadiobutton", background=BG2, foreground=FG, font=("Segoe UI", 10))
        style.configure("TCheckbutton", background=BG2, foreground=FG, font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 9))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Bar.Horizontal.TProgressbar", troughcolor=BG2, background=ACCENT2, thickness=14)

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)

        ttk.Label(root, text="⬇  DX DOWNLOADER", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(root, text="Baixe vídeos e áudios do YouTube, TikTok, Instagram, Facebook, X, Vimeo e +1.000 sites.  •  DX Labs",
                  style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 10))

        card = ttk.Frame(root, style="Card.TFrame", padding=12)
        card.grid(row=2, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)

        ttk.Label(card, text="Cole um ou mais links (um por linha) — Ctrl+V ou botão direito → Colar:",
                  style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.url_text = tk.Text(card, height=5, bg="#0E1219", fg=FG, insertbackground=FG,
                                font=("Segoe UI", 10), relief="flat", wrap="word", undo=False)
        self.url_text.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        # menu de botão direito no campo de links
        self._url_menu = tk.Menu(self.url_text, tearoff=0)
        self._url_menu.add_command(label="Colar", command=self._paste)
        self._url_menu.add_command(label="Limpar tudo", command=lambda: self.url_text.delete("1.0", "end"))
        self.url_text.bind("<Button-3>", lambda e: self._url_menu.tk_popup(e.x_root, e.y_root))
        # Ctrl+V / Shift+Insert / <<Paste>> passam pelo NOSSO colar assíncrono (nunca o do Tk)
        self.url_text.bind("<Control-v>", self._on_paste_key)
        self.url_text.bind("<Control-V>", self._on_paste_key)
        self.url_text.bind("<Shift-Insert>", self._on_paste_key)
        self.url_text.bind("<<Paste>>", self._on_paste_key)

        btns = ttk.Frame(card, style="Card.TFrame")
        btns.grid(row=2, column=0, sticky="w", pady=(0, 8))
        ttk.Button(btns, text="Colar da área de transferência", command=self._paste).pack(side="left")
        ttk.Button(btns, text="Limpar", command=lambda: self.url_text.delete("1.0", "end")).pack(side="left", padx=(8, 0))

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

        dest_row = ttk.Frame(card, style="Card.TFrame")
        dest_row.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        dest_row.columnconfigure(1, weight=1)
        ttk.Label(dest_row, text="Salvar em:", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.dest_var = tk.StringVar(value=str(self.last_dir))
        ttk.Entry(dest_row, textvariable=self.dest_var).grid(row=0, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(dest_row, text="Escolher…", command=self._choose_dir).grid(row=0, column=2)

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

    # ---------- helpers ----------
    def _sync_quality(self):
        self.quality.configure(state="readonly" if self.mode.get() == "video" else "disabled")

    def _on_paste_key(self, _event=None):
        self._paste()
        return "break"  # impede o colar nativo do Tk (que travava a tela)

    def _paste(self):
        """Colar ASSÍNCRONO (v1.2): o clipboard é lido pela API do Windows numa thread
        separada, com limite de 2s. A tela nunca espera o clipboard — nada de congelar."""
        if self._paste_busy:
            return
        self._paste_busy = True
        self.status_var.set("Lendo a área de transferência…")
        log_file("colar acionado (thread separada)")
        threading.Thread(target=self._paste_worker, daemon=True).start()

    def _paste_worker(self):
        try:
            txt = read_clipboard_text()
            self.events.put(("paste_result", txt or ""))
        except Exception as e:
            log_file(f"erro lendo clipboard: {e}")
            self.events.put(("paste_result", ""))

    def _apply_paste(self, txt: str):
        self._paste_busy = False
        if not txt.strip():
            self.status_var.set("Não consegui ler o link copiado — copie de novo e tente outra vez.")
            self._log("⚠ Colar: a área de transferência voltou vazia/sem texto. Copie o link de novo e tente outra vez.")
            return
        cur = self.url_text.get("1.0", "end").strip()
        self.url_text.delete("1.0", "end")
        self.url_text.insert("1.0", (cur + "\n" + txt.strip()) if cur else txt.strip())
        self.url_text.focus_set()
        log_file("link colado na tela")

    def _choose_dir(self):
        d = filedialog.askdirectory(parent=self, initialdir=self.dest_var.get() or str(Path.home()))
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
        log_file(msg)
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
            messagebox.showwarning(APP_NAME, "Cole pelo menos um link válido (começando com https://).", parent=self)
            return
        out_dir = self.dest_var.get().strip()
        if not out_dir:
            messagebox.showwarning(APP_NAME, "Escolha a pasta onde salvar.", parent=self)
            return
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        self.last_dir = Path(out_dir)

        self.cancel_flag.clear()
        self._prog_last_pct = -10.0
        self._prog_last_ts = 0.0
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
            # LIMITADOR anti-travamento: no máximo ~3 atualizações/segundo ou a cada +1%
            now = time.monotonic()
            if pct - self._prog_last_pct >= 1.0 or now - self._prog_last_ts >= 0.33:
                self._prog_last_pct = pct
                self._prog_last_ts = now
                self.events.put(("progress", pct, d.get("filename", ""), d.get("_speed_str", ""), d.get("_eta_str", "")))
        elif status == "finished":
            self.events.put(("log", f"✔ Download concluído: {Path(d.get('filename','')).name} — processando (juntar/extrair)…"))
        elif status == "started":
            self.events.put(("log", f"▶ Processando: {Path(str(d.get('filename',''))).name}"))

    def _run_downloads(self, urls, mode, quality_label, out_dir, playlist, cookies_browser, embed_meta):
        try:
            import yt_dlp
            log_file(f"yt-dlp {yt_dlp.version.__version__}")
        except Exception as e:
            self.events.put(("error", f"yt-dlp não encontrado: {e}"))
            return

        ffmpeg_path = bundled_ffmpeg()
        ffprobe_ok = has_ffprobe(ffmpeg_path)
        log_file(f"ffmpeg: {ffmpeg_path or 'nao localizado'} | ffprobe: {'ok' if ffprobe_ok else 'ausente'}")
        if not ffmpeg_path:
            self.events.put(("log", "⚠ ffmpeg.exe não localizado ao lado do app — vídeos separados de áudio podem não juntar. Use o .exe oficial do build."))
        elif not ffprobe_ok:
            self.events.put(("log", "ℹ ffprobe não embutido nesta versão: downloads normais, mas sem embutir capa/metadados (corrigido no próximo build)."))

        ok, fail = 0, 0
        for i, url in enumerate(urls, 1):
            if self.cancel_flag.is_set():
                break
            self.events.put(("status", f"Baixando {i}/{len(urls)}: {url}"))
            success, err = self._attempt(yt_dlp, url, mode, quality_label, out_dir, playlist,
                                         cookies_browser, embed_meta, ffmpeg_path,
                                         embed_ok=ffprobe_ok, impersonate=None)
            if err == "__cancelled__":
                self.events.put(("log", "⏹ Cancelado pelo usuário."))
                break
            # Retentativa anti-Cloudflare: imita o Chrome (precisa do curl-cffi embutido)
            if not success and err and is_cloudflare_error(err):
                try:
                    import curl_cffi  # noqa: F401
                    can_impersonate = True
                except Exception:
                    can_impersonate = False
                if can_impersonate:
                    self.events.put(("log", "🛡 Site com proteção anti-robô (Cloudflare) — tentando de novo imitando o Chrome…"))
                    success, err2 = self._attempt(yt_dlp, url, mode, quality_label, out_dir, playlist,
                                                  cookies_browser, embed_meta, ffmpeg_path,
                                                  embed_ok=ffprobe_ok, impersonate="chrome")
                    if success or err2:
                        err = err2
                else:
                    self.events.put(("log", "ℹ Este site pede imitação de navegador (curl-cffi não embutido neste build). Atualize o workflow e gere o próximo build."))
            # Retentativa de segurança: se o erro citar ffprobe, tenta sem embutir
            if not success and err and "ffprobe" in err:
                self.events.put(("log", "↪ Tentando de novo sem embutir capa/metadados…"))
                success, err2 = self._attempt(yt_dlp, url, mode, quality_label, out_dir, playlist,
                                              cookies_browser, embed_meta, ffmpeg_path,
                                              embed_ok=False, impersonate=None)
                if success or err2:
                    err = err2
            if err == "__cancelled__":
                self.events.put(("log", "⏹ Cancelado pelo usuário."))
                break
            if success:
                ok += 1
                self.events.put(("log", f"✅ Pronto: {url}"))
            else:
                fail += 1
                hint = ""
                if "instagram.com" in url and any(s in (err or "") for s in ("login", "Login", "rate", "Rate")):
                    hint = "\n   Dica: no Instagram, selecione os cookies do navegador (Chrome/Edge) nas opções e tente de novo."
                self.events.put(("log", f"❌ Falhou: {url}\n   Motivo: {(err or '')[:300]}{hint}"))
        self.events.put(("done", ok, fail))

    def _attempt(self, yt_dlp, url, mode, quality_label, out_dir, playlist,
                 cookies_browser, embed_meta, ffmpeg_path, embed_ok, impersonate):
        """Uma tentativa de download. Retorna (sucesso, mensagem_de_erro)."""
        opts = build_ydl_opts(mode, quality_label, out_dir, playlist,
                              cookies_browser, embed_meta, self._progress_hook, ffmpeg_path,
                              embed_ok=embed_ok, impersonate=impersonate)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            return True, None
        except CancelledError:
            return False, "__cancelled__"
        except Exception as e:
            return False, str(e)

    # ---------- eventos da thread -> UI (em LOTES, pra nunca soterrar a tela) ----------
    def _poll_events(self):
        processed = 0
        try:
            while processed < 80:  # lote máximo por ciclo
                ev = self.events.get_nowait()
                processed += 1
                kind = ev[0]
                if kind == "paste_result":
                    self._apply_paste(ev[1])
                elif kind == "progress":
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
                    if ok and not fail:
                        self.progress["value"] = 100
                    self.status_var.set(f"Concluído: {ok} ok • {fail} falha(s). Arquivos em: {self.dest_var.get()}")
                    self._log(f"— Fim: {ok} concluído(s), {fail} falha(s) —")
                    self._finish_ui()
        except queue.Empty:
            pass
        # se ainda tem fila, volta logo; senão, ritmo normal
        self.after(50 if processed >= 80 else 100, self._poll_events)

    def _finish_ui(self):
        self.btn_download.configure(state="normal")
        self.btn_cancel.configure(state="disabled")


if __name__ == "__main__":
    App().mainloop()
