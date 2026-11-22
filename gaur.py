"""
Gaur Donostia — genera la agenda de Donostia y alrededores para hoy,
mañana y el próximo fin de semana.

Qué hace, en 4 pasos:
  1. Descarga los eventos de la API abierta de Kulturklik (Gobierno Vasco)
     para cada día que hace falta (hoy, mañana y viernes a domingo).
  2. Se queda con los de Donostia y los pueblos cercanos y los agrupa en
     tres vistas: "hoy", "manana" y "finde".
  3. Le pide a Claude una selección de 4 planes por vista, con un comentario breve.
  4. Rellena plantilla.html y guarda el resultado en index.html.

Uso:
  python gaur.py                     # tomando hoy como referencia
  python gaur.py 2026-11-22          # tomando otro día como "hoy"
  python gaur.py --sin-claude        # sin selección (no necesita clave de API)
  python gaur.py --picks picks.json  # usa una selección ya hecha (sin API)

picks.json es un objeto con una lista por vista:
  {"hoy": [...], "manana": [...], "finde": [...]}
(una lista suelta, como en la versión anterior, se entiende como "hoy").

Con cualquiera de las dos opciones sin API, el script guarda también
eventos.json (las tres vistas ya filtradas), útil para hacer la selección a mano.

Qué es "el finde": viernes, sábado y domingo. De lunes a viernes, el de esa
semana (el viernes incluye el propio día); en sábado o domingo, el siguiente.

Requisitos:
  pip install requests anthropic
  Variable de entorno ANTHROPIC_API_KEY con tu clave (console.anthropic.com).
"""

import json
import os
import sys
from datetime import date, datetime, timedelta
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

Esta es la lista de eventos de {cuando} (JSON):

{eventos}

Elige los 4 planes más interesantes. Busca variedad: no todo conciertos,
y si puedes, incluye al menos un plan gratis o barato. Evita los formatos
comerciales repetitivos (por ejemplo, conciertos "Candlelight" o tributos) salvo
que no haya nada mejor. Si la lista abarca varios días (campo "w"), reparte los
planes entre ellos y pon el día al principio de "m".

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

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DIAS_CORTOS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


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
        if pagina >= datos.get("totalPages", pagina):
            break
        pagina += 1
    print(f"   {dia}: {len(eventos)} eventos en Euskadi ({pagina} páginas)")
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
            "i": e.get("id", ""),
            "w": f"{DIAS_CORTOS[dia.weekday()]} {dia.day}",
            "f": hoy,
            "fin": solo_dia(e.get("endDate")) if varios_dias else "",
            "t": "" if varios_dias else (e.get("openingHoursEs") or "").strip()[:13],
            "n": (e.get("nameEs") or "Sin título").strip(),
            "c": CATEGORIAS.get(e.get("typeEs"), e.get("typeEs") or "Otros"),
            "l": lugar,
            "p": (e.get("priceEs") or "").strip(),
            "u": e.get("urlEventEs") or e.get("purchaseUrlEs") or "",
            "last": varios_dias and solo_dia(e.get("endDate")) == hoy,
        })
    return salida


def juntar(por_dia: list[list[dict]]) -> list[dict]:
    """Une los eventos de uno o varios días: primero los que tienen hora, día a día
    y por hora; al final las expos y actividades largas, sin repetir."""
    con_hora, largas, vistas = [], [], set()
    for eventos in por_dia:
        con_hora += sorted((e for e in eventos if e["t"]), key=lambda e: e["t"])
        for e in eventos:
            if not e["t"] and e["i"] not in vistas:
                vistas.add(e["i"])
                largas.append(e)
    return con_hora + largas


def fecha_bonita(dia: date) -> str:
    return f"{DIAS[dia.weekday()]}, {dia.day} de {MESES[dia.month - 1]} de {dia.year}"


def fecha_finde(vie: date, dom: date) -> str:
    sab = vie + timedelta(days=1)
    if vie.month == dom.month:
        return f"Viernes {vie.day}, sábado {sab.day} y domingo {dom.day} de {MESES[dom.month - 1]}"
    mes = lambda d: MESES[d.month - 1]
    if sab.month == dom.month:  # el viernes es el último día del mes
        return f"Viernes {vie.day} de {mes(vie)}, sábado {sab.day} y domingo {dom.day} de {mes(dom)}"
    return f"Viernes {vie.day}, sábado {sab.day} de {mes(sab)} y domingo {dom.day} de {mes(dom)}"


def definir_vistas(hoy: date) -> list[dict]:
    """Las tres pestañas de la app y los días que entran en cada una.
    Finde = viernes, sábado y domingo: de lunes a viernes, el de esa semana
    (el viernes incluye el propio día); en sábado o domingo, el siguiente."""
    manana = hoy + timedelta(days=1)
    vie = hoy + timedelta(days=(4 - hoy.weekday()) % 7)
    sab, dom = vie + timedelta(days=1), vie + timedelta(days=2)
    return [
        {"k": "hoy", "tab": "Hoy", "dias": [hoy], "fecha": fecha_bonita(hoy),
         "cuando": f"hoy, {fecha_bonita(hoy)}"},
        {"k": "manana", "tab": "Mañana", "dias": [manana], "fecha": fecha_bonita(manana),
         "cuando": f"mañana, {fecha_bonita(manana)}"},
        {"k": "finde", "tab": "Finde", "dias": [vie, sab, dom], "fecha": fecha_finde(vie, dom),
         "cuando": f"el fin de semana ({fecha_finde(vie, dom)})"},
    ]


def pedir_seleccion(eventos: list[dict], cuando: str) -> list[dict]:
    """Paso 3: Claude elige 4 planes y los comenta."""
    import anthropic  # se importa aquí para que --sin-claude funcione sin instalarlo

    candidatos = [{k: v for k, v in e.items() if k not in ("i", "u", "last")}
                  for e in eventos if e["t"]]  # solo eventos con hora
    prompt = PROMPT_SELECCION.format(
        cuando=cuando,
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


def generar_html(datos: dict, ruta_salida: str):
    """Paso 4: rellena la plantilla y guarda index.html."""
    with open(os.path.join(CARPETA, "plantilla.html"), encoding="utf-8") as f:
        html = f.read()
    datos_js = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")
    html = (html
            .replace("__DATA__", datos_js)
            .replace("__UPDATED__", datetime.now(ZONA).strftime("%d/%m/%Y %H:%M")))
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
    hoy = date.fromisoformat(args[0]) if args else datetime.now(ZONA).date()
    vistas = definir_vistas(hoy)

    print(f"1) Descargando eventos (hoy = {hoy}) ...")
    cache = {}
    for v in vistas:
        for d in v["dias"]:
            if d not in cache:
                cache[d] = limpiar(descargar_eventos(d), d)

    print("2) Eventos en Donostia y alrededores:")
    for v in vistas:
        v["eventos"] = juntar([cache[d] for d in v["dias"]])
        print(f"   {v['tab']:7} {v['fecha']}: {len(v['eventos'])}")

    picks = {}
    if ruta_picks:
        with open(ruta_picks, encoding="utf-8") as f:
            picks = json.load(f)
        if isinstance(picks, list):  # formato antiguo: solo hoy
            picks = {"hoy": picks}
        print(f"3) Selección leída de {ruta_picks}: "
              + ", ".join(f"{k} {len(p)}" for k, p in picks.items()))
    elif sin_claude:
        print("3) Sin selección de Claude (--sin-claude)")
    else:
        print("3) Pidiendo la selección a Claude ...")
        for v in vistas:
            try:
                picks[v["k"]] = pedir_seleccion(v["eventos"], v["cuando"])
                print(f"   {v['tab']}: " + " · ".join(p["n"] for p in picks[v["k"]]))
            except Exception as error:
                print(f"   {v['tab']}: no se pudo obtener la selección ({error}). Sigo sin ella.")

    if ruta_picks or sin_claude:
        with open(os.path.join(CARPETA, "eventos.json"), "w", encoding="utf-8") as f:
            json.dump({v["k"]: {"cuando": v["cuando"], "eventos": v["eventos"]} for v in vistas},
                      f, ensure_ascii=False, indent=1)

    datos = [{
        "k": v["k"], "tab": v["tab"], "fecha": v["fecha"],
        "varios": len(v["dias"]) > 1,
        "picks": picks.get(v["k"], []),
        "eventos": [{k: x for k, x in e.items() if k != "i"} for e in v["eventos"]],
    } for v in vistas]

    # Pestaña «Próximos»: la rellena cada semana la tarea de proximos.py.
    ruta_proximos = os.path.join(CARPETA, "proximos.json")
    if os.path.exists(ruta_proximos):
        with open(ruta_proximos, encoding="utf-8") as f:
            prox = json.load(f)
        hoy_txt = hoy.isoformat()
        vigentes = lambda lista: [e for e in lista if (e.get("fin") or e["fecha"]) >= hoy_txt]
        datos.append({
            "k": "proximos", "tab": "Próximos", "tipo": "proximos",
            "fecha": f"Conciertos y festivales hasta {prox.get('hasta_txt', '')}".strip(),
            "actualizado": prox.get("actualizado", ""),
            "para_ti": vigentes(prox.get("para_ti", [])),
            "grandes": vigentes(prox.get("grandes", [])),
            "picks": [], "eventos": [],
        })
        print(f"   Próximos: {len(datos[-1]['para_ti'])} para ti, {len(datos[-1]['grandes'])} grandes")

    salida = os.path.join(CARPETA, "index.html")
    generar_html(datos, salida)
    print(f"4) Listo: {salida}")


if __name__ == "__main__":
    main()
