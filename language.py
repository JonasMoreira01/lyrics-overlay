"""Deteccao simples do idioma da letra (EN / PT / ES), por palavras funcionais frequentes.

Sem dependencias. Palavras que existem em mais de um idioma ficam de fora das listas.
"""
import re

EN = frozenset("""
the and you your i i'm i'll i've i'd to of in is it its that my we with for on be are this but not all just
like don't can't when what will can now there they have was were so if or at from out up she he her his our
them would could been
""".split())

PT = frozenset("""
não nao eu você voce vocês uma um uns umas em com mais meu minha meus minhas teu tua nós vai sem já muito
aqui isso são foi tem então ainda sempre pra pro nem só também onde da das dos ela ele quando tudo estou
estava
""".split())

ES = frozenset("""
el los las una un pero yo tú mis ya muy qué cómo cuando donde porque estoy eres soy con del al sin aquí
también tengo tienes quiero siempre más
""".split())

TOKEN_RE = re.compile(r"[a-záàâãéêíóôõúçñü']+")


def detect_language(texts, min_hits=5):
    """'en', 'pt', 'es' ou None (pouco texto ou empate). texts = lista de linhas."""
    words = TOKEN_RE.findall(" ".join(texts).lower())
    scores = {"en": 0, "pt": 0, "es": 0}
    for w in words:
        if w in EN:
            scores["en"] += 1
        if w in PT:
            scores["pt"] += 1
        if w in ES:
            scores["es"] += 1
    (best, top), (_, second) = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:2]
    return best if top >= min_hits and top > second else None
