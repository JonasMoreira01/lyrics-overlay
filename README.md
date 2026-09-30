# Lyrics Overlay

Mostra no canto inferior direito da tela a **letra sincronizada** da música que está tocando, com **tradução para português** embaixo de cada linha. Feito para quem quer melhorar o inglês escutando música enquanto trabalha.

- Lê a música atual de qualquer player que fale MPRIS: **Spotify**, **YouTube no Chrome/Chromium/Firefox** e outros.
- Letras sincronizadas vêm do [LRCLIB](https://lrclib.net) (grátis, sem chave de API). Nada é embutido neste repositório: as letras são buscadas na hora e ficam só no cache local (`~/.cache/lyrics-overlay`).
- Tradução EN→PT **offline** com [Argos Translate](https://github.com/argosopentech/argos-translate) (sem limite de uso).
- Ícone na bandeja do sistema com menu: mostrar/ocultar a letra, ligar/desligar a tradução e sair.
- A janela é transparente, fica sempre no topo e ignora cliques.

## Requisitos

- Linux com **X11** (testado no Linux Mint Cinnamon). Em Wayland o "sempre no topo" e o posicionamento podem não funcionar.
- Python 3.9+ e as bibliotecas do sistema (Ubuntu/Debian/Mint):

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 python3-venv
```

## Instalação

```bash
git clone https://github.com/JonasMoreira01/lyrics-overlay.git
cd lyrics-overlay
./install.sh
```

O instalador não usa `sudo`. Ele copia o app para `~/.local/share/lyrics-overlay`, cria um ambiente virtual próprio e adiciona **Lyrics Overlay** ao menu de aplicativos, então não precisa de terminal para abrir.

| Opção | O que faz |
|---|---|
| `--lite` | Pula o tradutor offline (~200MB). A tradução cai no Google gratuito, que pode bloquear com erro 429. |
| `--autostart` | Inicia junto com a sessão. |

Para remover: `./uninstall.sh` (use `--purge` para apagar também cache e configuração).

## Uso

Abra **Lyrics Overlay** no menu de aplicativos ou rode `lyrics-overlay`. Um ícone aparece na bandeja; clique nele para:

- **Mostrar letra**: oculta/exibe o overlay.
- **Traduzir para português**: liga/desliga a tradução (a escolha é lembrada).
- **Sair**.

Opções de linha de comando:

| Opção | O que faz |
|---|---|
| `--offset 0.5` | Adianta a letra em 0,5s (use valor negativo para atrasar). |
| `--monitor N` | Escolhe o monitor (padrão: primário). |
| `--no-translate` | Inicia sem tradução, só nessa execução. |

## Limitações

- Só aparece letra quando o LRCLIB tem versão **sincronizada** daquela faixa.
- No YouTube, o título do vídeo é limpo (`(Official Video)`, `- Topic`…) e dividido em `Artista - Música`. Vídeos fora desse padrão, podcasts e covers podem não ter letra ou ter artista errado.
- O tradutor offline é mais literal que o Google; gírias podem sair estranhas.

## Desenvolvimento

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install requests argostranslate
.venv/bin/python lyrics_overlay.py
```

## Licença

[MIT](LICENSE)
