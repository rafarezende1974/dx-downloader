# DX Downloader (Windows) — DX Labs

App para Windows que baixa **vídeos e áudios** de sites como YouTube, TikTok, Instagram,
Facebook, X (Twitter), Vimeo, Twitch e +1.000 outros (motor: **yt-dlp** + **ffmpeg** embutido).

## O que a v1 faz
- Cola **um ou vários links** (um por linha) e baixa em sequência
- **Vídeo MP4** — melhor qualidade ou limitado a 1080p / 720p / 480p / 360p (junta vídeo+áudio automaticamente)
- **Áudio MP3** (192 kbps) ou **M4A**, com capa e metadados embutidos (opcional)
- **Playlist inteira** (opcional, desligado por padrão)
- **Cookies do navegador** (Chrome/Edge/Firefox/Brave) — ajuda em posts do Instagram/TikTok que exigem login
- Barra de progresso com velocidade e tempo restante, botão **Cancelar**, registro na tela
- Pasta padrão: `Downloads\DX Downloader` (pode trocar na tela)

## Como gerar o .exe
1. Suba este projeto para um repositório no GitHub.
2. O workflow **Build Windows EXE** (`.github/workflows/build-windows.yml`) roda sozinho a cada push na `main`
   (usa o runner `windows-latest`, baixa o ffmpeg, gera o ícone e compila com PyInstaller).
3. Baixe o artefato **dx-downloader-windows** na aba **Actions** → contém `DXDownloader.exe` (arquivo único, portátil).

> Se o workflow não puder ser criado pela integração, crie o arquivo manualmente pela interface web
> do GitHub com o mesmo conteúdo — cópia dele em `WORKFLOW-BUILD-WINDOWS.yml.txt` na raiz.

## Versões
- **v1.1.0** — correção do travamento ("não respondendo"): progresso limitado a ~3 atualizações/segundo,
  fila de eventos da tela processada em lotes, Colar via mecanismo interno do Tk + menu de botão direito,
  e log de diagnóstico em `%LOCALAPPDATA%\DXDownloader\dx-downloader.log`.
- **v1.0.0** — primeira versão.

## Uso
Use apenas para baixar conteúdo que você tem direito de baixar / para uso pessoal.

## Estrutura
- `app.py` — GUI (Tkinter) + lógica de download
- `generate_icon.py` — gera `assets/icon.ico` no build (ícone próprio: seta de download + bandeja)
- `requirements.txt` — yt-dlp + Pillow
