"""
Gaur Donostia — pestaña «Escapadas»: teatro, conciertos y danza en Bilbao,
Vitoria-Gasteiz, Pamplona/Iruña y Biarritz-Baiona para las próximas semanas.
Pensada para planear, no para hoy: se actualiza una vez por semana.

Qué hace:
  1. Descarga TODOS los eventos futuros de Kulturklik.
  2. Se queda con conciertos, teatro, danza y festivales de esas ciudades
     en las próximas 4 semanas (sin tributos ni Candlelight).
  3. Junta duplicados y deja un formato corto. Guarda escapadas_candidatos.json.

Extras: lo que Kulturklik no trae (sobre todo la Gare du Midi de Biarritz) se busca
en la web y se guarda en escapadas_extra.json, con el mismo formato y la URL de la
fuente. El script lo mezcla con lo de Kulturklik si cae dentro de las 4 semanas.

La selección «Merece el viaje» (unos 6 planes) la hace Claude en la tarea
semanal y se guarda, junto con la lista, en escapadas.json:
  python escapadas.py                         # solo candidatos
  python escapadas.py --picks esc_picks.json  # candidatos + selección → escapadas.json
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

API = "https://api.euskadi.eus/culture/events/v1.0/events/upcoming"
CARPETA = os.path.dirname(os.path.abspath(__file__))
ZONA = ZoneInfo("Europe/Madrid")
SEMANAS = 4

# Municipio de Kulturklik → nombre corto de la ciudad en la app.
CIUDADES = {
    "Bilbao": "Bilbao", "Barakaldo": "Bilbao",
    "Vitoria-Gasteiz": "Vitoria",
    "Pamplona/Iruña": "Pamplona", "Villava/Atarrabia": "Pamplona", "Barañáin": "Pamplona",
    "Biarritz": "Biarritz", "Bayonne": "Biarritz", "Anglet": "Biarritz",
}
TIPOS = {"Concierto": "Música", "Teatro": "Teatro", "Danza": "Danza", "Festival": "Festival"}
RUIDO = re.compile(r"^(programa|\(suspendido\)|suspendido)|fashion week", re.IGNORECASE)
TRIBUTO = re.compile(r"tribut|homenaje|tribute|candlelight|experience\b", re.IGNORECASE)


def limpiar_titulo(t: str) -> str:
    t = re.sub(r"^\s*entradas( concierto)?\s*", "", t or "", flags=re.IGNORECASE)
    t = re.sub(r"\s*\([^)]*\b(20\d\d|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
               r"octubre|noviembre|diciembre)\b[^)]*\)\s*$", "", t, flags=re.IGNORECASE)
    return t.strip(" -–:")


def descargar_todo() -> list[dict]:
    eventos, pagina = [], 1
    while True:
        r = requests.get(API, params={"_elements": 100, "_page": pagina}, timeout=30)
        r.raise_for_status()
        datos = r.json()
        eventos += datos.get("items", [])
        if pagina >= datos.get("totalPages", 1) or pagina >= 100:
            break
        pagina += 1
    print(f"1) {len(eventos)} eventos futuros en Kulturklik")
    return eventos


def candidatos(hoy, hasta) -> list[dict]:
    vistos, salida = set(), []
    for e in descargar_todo():
        ciudad = CIUDADES.get(e.get("municipalityEs"))
        tipo = TIPOS.get(e.get("typeEs"))
        if not ciudad or not tipo:
            continue
        inicio, fin = (e.get("startDate") or "")[:10], (e.get("endDate") or "")[:10]
        if not inicio or fin < hoy or inicio > hasta:
            continue
        titulo = limpiar_titulo(e.get("nameEs"))
        lugar = e.get("establishmentEs") or e.get("placeEs") or ""
        if TRIBUTO.search(titulo) or RUIDO.search(titulo):
            continue
        if fin and (date.fromisoformat(fin) - date.fromisoformat(inicio)).days > 31:
            continue  # temporadas y ciclos de meses: no son un plan concreto
        if tipo == "Festival" and not lugar:
            continue  # paraguas de festival sin sitio concreto
        clave = (titulo.lower(), inicio, ciudad)
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append({
            "fecha": max(inicio, hoy),  # si empezó antes, se muestra desde hoy
            "fin": fin if fin != inicio else "",
            "hora": (e.get("openingHoursEs") or "").strip()[:13],
            "titulo": titulo,
            "tipo": tipo,
            "lugar": lugar,
            "ciudad": ciudad,
            "precio": (e.get("priceEs") or "").strip(),
            "url": e.get("purchaseUrlEs") or e.get("sourceUrlEs") or e.get("urlEventEs") or "",
        })
    # Extras encontrados en la web (Gare du Midi, etc.)
    ruta_extra = os.path.join(CARPETA, "escapadas_extra.json")
    if os.path.exists(ruta_extra):
        with open(ruta_extra, encoding="utf-8") as f:
            extras = json.load(f)
        n = 0
        for x in extras:
            fin = x.get("fin") or x["fecha"]
            if fin < hoy or x["fecha"] > hasta:
                continue
            clave = (x["titulo"].lower(), x["fecha"], x["ciudad"])
            if clave in vistos:
                continue
            vistos.add(clave)
            salida.append({**x, "fecha": max(x["fecha"], hoy), "fuente": "web"})
            n += 1
        print(f"   + {n} extras de la web (escapadas_extra.json)")
    salida.sort(key=lambda x: (x["fecha"], x["hora"] or "99", x["titulo"]))
    return salida


def main():
    hoy_d = datetime.now(ZONA).date()
    hoy, hasta = hoy_d.isoformat(), (hoy_d + timedelta(weeks=SEMANAS)).isoformat()
    lista = candidatos(hoy, hasta)
    from collections import Counter
    print(f"2) {len(lista)} planes hasta {hasta}:", dict(Counter(x["ciudad"] for x in lista)),
          dict(Counter(x["tipo"] for x in lista)))
    with open(os.path.join(CARPETA, "escapadas_candidatos.json"), "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=1)
    print("3) Guardado escapadas_candidatos.json")

    if "--picks" in sys.argv:
        ruta = sys.argv[sys.argv.index("--picks") + 1]
        with open(ruta, encoding="utf-8") as f:
            picks = json.load(f)
        with open(os.path.join(CARPETA, "escapadas.json"), "w", encoding="utf-8") as f:
            json.dump({"actualizado": hoy_d.strftime("%d/%m/%Y"), "desde": hoy, "hasta": hasta,
                       "picks": picks, "eventos": lista}, f, ensure_ascii=False, indent=1)
        print(f"4) Guardado escapadas.json con {len(picks)} planes destacados")


if __name__ == "__main__":
    main()
