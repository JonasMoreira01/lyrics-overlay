# Lyrics Overlay

Mostra no canto da tela a **letra sincronizada** da música que está tocando, com **tradução para português** embaixo de cada linha. Feito para quem quer melhorar o inglês escutando música enquanto trabalha.

- Lê a música atual de qualquer player que fale MPRIS: **Spotify**, **YouTube no Chrome/Chromium/Firefox** e outros.
- Letras sincronizadas vêm do [LRCLIB](https://lrclib.net) (grátis, sem chave de API). Nada é embutido neste repositório: as letras são buscadas na hora e ficam só no cache local (`~/.cache/lyrics-overlay`).
- Tradução EN→PT **offline** com [Argos Translate](https://github.com/argosopentech/argos-translate) (sem limite de uso). A letra é traduzida por estrofe, para dar contexto ao tradutor.
- **Modo estudo**: clique numa palavra da linha atual para ver IPA, tradução e definições, e salve no seu vocabulário (importável no Anki).
- Ícone na bandeja do sistema com todas as opções.
- A janela é transparente, fica sempre no topo e, fora do modo estudo, ignora cliques.

## Requisitos

- Linux com **X11** (testado no Linux Mint Cinnamon). Em Wayland o "sempre no topo" e o posicionamento podem não funcionar.
- Python 3.9+ e as bibliotecas do sistema (Ubuntu/Debian/Mint):

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 python3-venv
```

Opcional: `sudo apt install espeak-ng` habilita o botão **Ouvir** (pronúncia) no painel de palavra.

## Instalação

```bash
git clone https://github.com/JonasMoreira01/lyrics-overlay.git
cd lyrics-overlay
./install.sh
```

O instalador não usa `sudo`. Ele copia o app para `~/.local/share/lyrics-overlay`, cria um ambiente virtual próprio e adiciona **Lyrics Overlay** ao menu de aplicativos, então não precisa de terminal para abrir. Para atualizar, rode `./install.sh` de novo e reinicie o app.

| Opção | O que faz |
|---|---|
| `--lite` | Pula o tradutor offline (~200MB). A tradução cai no Google gratuito, que pode bloquear com erro 429. |
| `--autostart` | Inicia junto com a sessão. |

Para remover: `./uninstall.sh` (use `--purge` para apagar também cache e configuração). O vocabulário salvo **nunca** é apagado pelo script; fica em `~/.local/share/lyrics-overlay/vocabulary.tsv`.

## Uso

Abra **Lyrics Overlay** no menu de aplicativos ou rode `lyrics-overlay`. Um ícone aparece na bandeja; o menu tem:

| Item | O que faz |
|---|---|
| Mostrar letra | Oculta/exibe o overlay. |
| Traduzir para português | Liga/desliga a tradução. |
| Ocultar quando nada toca | Esconde o overlay 3 s depois que a música para. |
| **Estudo** | Modo estudo, pausa ao consultar, abrir vocabulário (abaixo). |
| **Aparência** | Tamanho (P/M/G), fundo (85/60/35%), canto da tela e monitor. |
| **Sincronia da letra** | Adianta/atrasa 0,5 s **só para a música atual** e zera o ajuste. |
| Sair | Encerra o app. |

Todas as escolhas ficam salvas em `~/.config/lyrics-overlay/config.json`.

### Modo estudo

1. Na bandeja: **Estudo → Modo estudo**. As palavras da linha atual viram links.
2. Clique numa palavra: aparece um painel com IPA, tradução e até duas definições em inglês (dados do [Datamuse](https://www.datamuse.com/api/), que usa o Wiktionary).
3. Por padrão a música **pausa** ao consultar e volta ao tocar quando você clica em **Fechar** (desligue em *Pausar a música ao consultar*).
4. **Salvar palavra** grava em `~/.local/share/lyrics-overlay/vocabulary.tsv`, junto com a linha de onde ela veio e a música. Palavras repetidas são ignoradas.

Com o modo estudo ligado a janela passa a receber cliques; desligue-o para voltar a ignorá-los.

**Importar no Anki** (2.1.54 ou mais novo): *Arquivo → Importar* e escolha o `vocabulary.tsv`. O cabeçalho do arquivo já configura separador (tab), HTML e tags. Em *Abrir lista de vocabulário* o arquivo abre no programa padrão do seu sistema para `.tsv`, que pode ser uma planilha.

### Linha de comando

| Opção | O que faz |
|---|---|
| `--offset 0.5` | Ajuste global de sincronia (soma ao ajuste por música). |
| `--monitor N` | Escolhe o monitor nesta execução (sobrepõe a configuração). |
| `--no-translate` | Inicia sem tradução, só nessa execução. |
| `--demo` | Modo demonstração: frases de exemplo, sem player. Bom para testar a aparência. |

## Limitações

- Só aparece letra quando o LRCLIB tem versão **sincronizada** daquela faixa.
- No YouTube, o título do vídeo é limpo (`(Official Video)`, `- Topic`…) e dividido em `Artista - Música`. Vídeos fora desse padrão, podcasts e covers podem não ter letra ou ter artista errado.
- O tradutor offline é mais literal que o Google; gírias podem sair estranhas. Se a tradução por estrofe não mantiver o número de linhas, o app traduz linha a linha.
- A consulta de palavras precisa de internet (Datamuse) na primeira vez; depois fica em cache. Palavras que o Datamuse não conhece mostram só a tradução.
- Não há áudio de pronúncia embutido: o IPA aparece sempre e o botão **Ouvir** só existe com `espeak-ng` instalado.

## Desenvolvimento

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install requests argostranslate
.venv/bin/python lyrics_overlay.py --demo
```

`study.py` (consulta de palavras e vocabulário) não depende de GTK e é o ponto de partida para testes.

## Licença

[MIT](LICENSE)
