"""Consulta de palavras (Datamuse) e lista de vocabulario (TSV importavel no Anki).

Sem dependencia de GTK, para ficar facil de testar.
"""
import html
import json
import os
import re
from pathlib import Path

import requests

DATAMUSE_URL = "https://api.datamuse.com/words"
WORD_RE = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
VALID_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)*")
POS_NAMES = {"n": "noun", "v": "verb", "adj": "adj", "adv": "adv", "u": ""}
# Diretivas do importador do Anki (2.1.54+): separador, HTML ligado, tags na 3a coluna.
VOCAB_HEADER = "#separator:tab\n#html:true\n#tags column:3\n"
TAG = "lyrics-overlay"


def data_dir():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "lyrics-overlay"


def vocab_path():
    return data_dir() / "vocabulary.tsv"


def normalize(token):
    """'Don’t' -> "don't"."""
    return token.replace("’", "'").lower().strip("'")


def lookup(word, cache_dir, headers=None):
    """IPA + ate 3 definicoes (Datamuse). Retorna dict, ou None se a palavra nao existe.

    Erros de rede levantam requests.RequestException (e nao entram no cache).
    """
    word = normalize(word)
    if not VALID_WORD_RE.fullmatch(word):
        return None
    cache = Path(cache_dir) / "dict" / f"{word}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    r = requests.get(DATAMUSE_URL, params={"sp": word, "md": "dr", "ipa": 1, "max": 1},
                     headers=headers, timeout=6)
    r.raise_for_status()
    data = r.json()
    result = None
    # 'sp' aceita aproximacoes: so vale se a palavra devolvida for exatamente a pedida.
    if data and data[0].get("word", "").lower() == word:
        entry = data[0]
        ipa = next((t.split(":", 1)[1] for t in entry.get("tags", []) if t.startswith("ipa_pron:")), "")
        defs = []
        for raw in entry.get("defs", [])[:3]:
            pos, _, text = raw.partition("\t")
            defs.append({"pos": POS_NAMES.get(pos.strip(), pos.strip()), "text": text.strip()})
        result = {"word": word, "ipa": ipa, "defs": defs}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result), encoding="utf-8")
    return result


def ensure_vocab_file(path=None):
    path = path or vocab_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(VOCAB_HEADER, encoding="utf-8")
    return path


def save_word(word, ipa, translation, definition, line, track, path=None):
    """Acrescenta a palavra ao TSV. Retorna False se ela ja estava na lista."""
    path = ensure_vocab_file(path)
    word = normalize(word)
    for row in path.read_text(encoding="utf-8").splitlines():
        if not row.startswith("#") and row.split("\t", 1)[0].lower() == word:
            return False

    def clean(value):
        return html.escape(" ".join(str(value).split()), quote=False)

    back = "<br>".join(part for part in (
        "/" + clean(ipa) + "/" if ipa else "",
        "<b>" + clean(translation) + "</b>" if translation else "",
        clean(definition) if definition else "",
        "<i>" + clean(line) + "</i> (" + clean(track) + ")" if line else "",
    ) if part)
    with path.open("a", encoding="utf-8") as f:
        f.write(f"{clean(word)}\t{back}\t{TAG}\n")
    return True
