"""
Gaur Donostia — genera la agenda de hoy en Donostia y alrededores.

Qué hace, en 4 pasos:
  1. Descarga los eventos de hoy de la API abierta de Kulturklik (Gobierno Vasco).
  2. Se queda con los de Donostia y los pueblos cercanos.
  3. Le pide a Claude una selección de 4 planes con un comentario breve.
  4. Rellena plantilla.html y guarda el resultado en index.html.

Uso:
  python gaur.py                     # hoy
  python gaur.py 2026-11-22          # otro día
  python gaur.py --sin-claude        # sin selección (no necesita clave de API)
  python gaur.py --picks picks.json  # usa una selección ya hecha (sin API)

Con cualquiera de las dos opciones sin API, el script guarda también
eventos.json (la lista ya filtrada), útil para hacer la selección a mano.

Requisitos:
  pip install requests anthropic
  Variable de entorno ANTHROPIC_API_KEY con tu clave (console.anthropic.com).
"""

import json
import os
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

# ---------------------------------------------------------------------------
# CONFIGURACIÓN — cambia esto a tu gusto
# ---------------------------------------------------------------------------

# Municipios que se incluyen (tal como los escribe Kulturklik en "municipalityEs").
MUNICIPIOS = [
    "Donostia / San Sebastián",
    "Pasaia",
    "Errenteria",
    "Hernani",
    "Lasarte-Oria",
    "Astigarraga",
    "Usurbil",
    "Oiartzun",
    "Irun",
    "Hondarribia",
]

# Cómo se llama cada tipo de Kulturklik en la app (los demás se dejan tal cual).
CATEGORIAS = {
    "Concierto": "Música",
    "Teatro": "Teatro",
    "Danza": "Danza",
    "Bertsolarismo": "Bertsos",
    "Cine y audiovisuales": "Cine",
    "Exposición": "Expos",
    "Formación": "Taller",
    "Conferencia": "Charlas",
    "Fiestas": "Fiestas",
    "Feria": "Ferias",
    "Otro": "Otros",
}

MODELO_CLAUDE = "claude-sonnet-5-5"
ZONA = ZoneInfo("Europe/Madrid")  # "hoy" y la hora de actualización, en hora de Donostia

# ---------------------------------------------------------------------------
# EL PROMPT — aquí es donde le dices a Claude cómo tiene que "pensar"
# ---------------------------------------------------------------------------

PROMPT_SELECCION = """Eres el editor de "Gaur", una agenda cultural de Donostia
al estilo de la revista Zitty de Berlín: directa, con criterio y algo de chispa.

Hoy es {fecha}. Esta es la lista de eventos de hoy (JSON):

{eventos}

Elige los 4 planes más interesantes del día. Busca variedad: no todo conciertos,
y si puedes, incluye al menos un plan gratis o barato. Evita los formatos
comerciales repetitivos (por ejemplo, conciertos "Candlelight" o tributos) salvo
que no haya nada mejor.

Para cada plan escribe 2 frases que expliquen por qué merece la pena.
REGLA IMPORTANTE: no inventes datos. Usa solo lo que aparece en la lista
y lo que sepas con seguridad del artista. Si no conoces a un artista, habla del
tipo de plan, del lugar o del precio, no de su trayectoria.

Responde SOLO con un JSON válido, sin texto antes ni después, con este formato:
[
  {{"n": "título corto", "m": "hora · lugar · precio", "d": "tu comentario"}}
]
"""

# ---------------------------------------------------------------------------

API = "https://api.euskadi.eus/culture/events/v1.0/events/byDate/{y}/{m}/{d}"
CARPETA = os.path.dirname(os.path.abspath(__file__))


def descargar_eventos(dia: date) -> list[dict]:
    """Paso 1: descarga TODAS las páginas de eventos de ese día en Euskadi."""
    url = API.format(y=dia.year, m=f"{dia.month:02d}", d=f"{dia.day:02d}")
    eventos, pagina, vistos = [], 1, set()
    while True:
        r = requests.get(url, params={"_elements": 100, "_page": pagina}, timeout=30)
        r.raise_for_status()
        datos = r.json()
        items = datos.get("items", [])
        nuevos = [e for e in items if e.get("id") not in vistos]
        if not nuevos:  # página vacía o repetida: hemos terminado
            break
        vistos.update(e.get("id") for e in nuevos)
        eventos.extend(nuevos)
        print(f"  página {pagina}/{datos.get('totalPages', '?')}: {len(nuevos)} eventos")
        if pagina >= datos.get("totalPages", pagina):
            break
        pagina += 1
    return eventos


def solo_dia(iso: str) -> str:
    """'2026-11-21T00:00:00Z' -> '2026-11-21'"""
    return (iso or "")[:10]


def limpiar(eventos: list[dict], dia: date) -> list[dict]:
    """Paso 2: filtra por municipio y lo deja en el formato corto que usa la app."""
    hoy = dia.isoformat()
    salida = []
    for e in eventos:
        if e.get("municipalityEs") not in MUNICIPIOS:
            continue
        varios_dias = solo_dia(e.get("startDate")) != solo_dia(e.get("endDate"))
        lugar = e.get("establishmentEs") or e.get("placeEs") or ""
        if e.get("municipalityEs") != "Donostia / San Sebastián":
            lugar = f"{lugar} ({e['municipalityEs']})" if lugar else e["municipalityEs"]
        salida.append({
            "t": "" if varios_dias else (e.get("openingHoursEs") or "").strip()[:13],
            "n": (e.get("nameEs") or "Sin título").strip(),
            "c": CATEGORIAS.get(e.get("typeEs"), e.get("typeEs") or "Otros"),
            "l": lugar,
            "p": (e.get("priceEs") or "").strip(),
            "u": e.get("urlEventEs") or e.get("purchaseUrlEs") or "",
            "last": varios_dias and solo_dia(e.get("endDate")) == hoy,
        })
    # Ordena: primero lo que tiene hora (por hora), al final las expos.
    salida.sort(key=lambda x: (x["t"] == "", x["t"]))
    return salida


def pedir_seleccion(eventos: list[dict], fecha_txt: str) -> list[dict]:
    """Paso 3: Claude elige 4 planes y los comenta."""
    import anthropic  # se importa aquí para que --sin-claude funcione sin instalarlo

    candidatos = [e for e in eventos if e["t"]]  # solo eventos con hora
    prompt = PROMPT_SELECCION.format(
        fecha=fecha_txt,
        eventos=json.dumps(candidatos, ensure_ascii=False, indent=0),
    )
    cliente = anthropic.Anthropic()  # usa ANTHROPIC_API_KEY
    respuesta = cliente.messages.create(
        model=MODELO_CLAUDE,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = respuesta.content[0].text.strip()
    texto = texto[texto.find("["): texto.rfind("]") + 1]  # por si añade algo alrededor
    return json.loads(texto)


def fecha_bonita(dia: date) -> str:
    dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{dias[dia.weekday()]}, {dia.day} de {meses[dia.month - 1]} de {dia.year}"


def generar_html(eventos, picks, dia: date, ruta_salida: str):
    """Paso 4: rellena la plantilla y guarda index.html."""
    with open(os.path.join(CARPETA, "plantilla.html"), encoding="utf-8") as f:
        html = f.read()
    html = (html
            .replace("__EVENTS__", json.dumps(eventos, ensure_ascii=False))
            .replace("__PICKS__", json.dumps(picks, ensure_ascii=False))
            .replace("__DATE__", fecha_bonita(dia))
            .replace("__UPDATED__", datetime.now(ZONA).strftime("%d/%m/%Y %H:%M"))
            .replace("__COUNT__", str(len(eventos))))
    with open(ruta_salida, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    argv = sys.argv[1:]
    ruta_picks = None
    if "--picks" in argv:
        i = argv.index("--picks")
        ruta_picks = argv[i + 1]
        del argv[i:i + 2]
    sin_claude = "--sin-claude" in argv
    args = [a for a in argv if not a.startswith("--")]
    dia = date.fromisoformat(args[0]) if args else datetime.now(ZONA).date()

    print(f"1) Descargando eventos del {dia} ...")
    brutos = descargar_eventos(dia)
    print(f"   {len(brutos)} eventos en toda Euskadi")

    eventos = limpiar(brutos, dia)
    print(f"2) {len(eventos)} eventos en Donostia y alrededores")

    picks = []
    if ruta_picks:
        with open(ruta_picks, encoding="utf-8") as f:
            picks = json.load(f)
        print(f"3) Selección leída de {ruta_picks}: {len(picks)} planes")
    elif sin_claude:
        print("3) Sin selección de Claude (--sin-claude)")
    else:
        print("3) Pidiendo la selección a Claude ...")
        try:
            picks = pedir_seleccion(eventos, fecha_bonita(dia))
            for p in picks:
                print(f"   · {p['n']}")
        except Exception as error:
            print(f"   No se pudo obtener la selección ({error}). Sigo sin ella.")

    if ruta_picks or sin_claude:
        with open(os.path.join(CARPETA, "eventos.json"), "w", encoding="utf-8") as f:
            json.dump(eventos, f, ensure_ascii=False, indent=1)

    salida = os.path.join(CARPETA, "index.html")
    generar_html(eventos, picks, dia, salida)
    print(f"4) Listo: {salida}")


if __name__ == "__main__":
    main()
