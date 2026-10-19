"""Convierte knowledge/guia_*.md a knowledge/html/*.html, lo que se sube al bucket.

Uso (desde la raiz del repo):

    .venv/bin/python knowledge/build.py

El <style> se copia del SLA (`html/sla_dxkare_v2.0.html`, que salio de pandoc)
para que todas las paginas del dataStore tengan la misma forma. El SLA no se
genera aqui: es un documento contractual que se mantiene aparte y se versiona
tal cual se subio.
"""
import html
import pathlib
import re

import markdown

RAIZ = pathlib.Path(__file__).parent
SALIDA = RAIZ / "html"
SLA = SALIDA / "sla_dxkare_v2.0.html"

style = re.search(r"<style>.*?</style>", SLA.read_text(encoding="utf-8"), re.S).group(0)
for md in sorted(RAIZ.glob("guia_*.md")):
    text = md.read_text(encoding="utf-8")
    # El <title> es lo que `buscar_documentacion` devuelve como `titulo`.
    title = re.search(r"^title:\s*(.+)$", text, re.M).group(1).strip()
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    content = markdown.markdown(body, extensions=["tables", "toc"])
    page = f"""<!DOCTYPE html>
<html lang="es" xml:lang="es">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=yes" />
  <title>{html.escape(title)}</title>
  {style}
</head>
<body>
{content}
</body>
</html>
"""
    (SALIDA / (md.stem + ".html")).write_text(page, encoding="utf-8")
    print(SALIDA / (md.stem + ".html"))
