"""
Gaur Donostia — pestaña «Próximos»: conciertos y festivales que vienen.

Este script hace la parte mecánica (la que no necesita criterio):
  1. Descarga TODOS los eventos futuros de Kulturklik.
  2. Se queda con conciertos y festivales de los próximos 6 meses en
     Gipuzkoa, Bizkaia, Álava y Pamplona/Iruña (con Villava y Barañáin).
  3. Limpia títulos ("Entradas ...", fechas entre paréntesis), junta
     duplicados, marca los tributos y le pone a cada uno el tamaño del recinto.
  4. Guarda candidatos.json.

La parte con criterio la hacen los agentes (ver la tarea semanal):
identificar artistas, medir la fama, cruzar con tus gustos, verificar
contra la fuente y escribir proximos.json, que gaur.py convierte en la
pestaña «Próximos».

Uso:
  python proximos.py              # 6 meses desde hoy
  python proximos.py --meses 12   # otro horizonte
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

API = "https://api.euskadi.eus/culture/events/v1.0/events/upcoming"
CARPETA = os.path.dirname(os.path.abspath(__file__))
ZONA = ZoneInfo("Europe/Madrid")

TIPOS = {"Concierto", "Festival"}
PROVINCIAS = {"20": "Gipuzkoa", "48": "Bizkaia", "1": "Álava"}  # entera
NAVARRA_MUNICIPIOS = {"Pamplona/Iruña", "Villava/Atarrabia", "Barañáin"}

# Tamaño del recinto: la señal de fama más barata y más fiable.
# "estadio" y "arena" casi siempre son artistas grandes.
RECINTOS = {
    "estadio": ["San Mamés", "Anoeta", "Reale Arena", "Mendizabala", "Kobetamendi"],
    "arena": ["BEC", "Bizkaia Arena", "Bilbao Arena", "Miribilla", "Illunbe",
              "Donostia Arena", "Navarra Arena", "Buesa Arena", "Fernando Buesa"],
    "auditorio": ["Kursaal", "Euskalduna", "Baluarte", "Victoria Eugenia",
                  "Arriaga", "Campos Elíseos", "Principal Antzokia", "Gayarre"],
    "sala": ["Dabadaba", "Kafe Antzokia", "Santana 27", "Totem", "Jimmy Jazz",
             "Helldorado", "Zentral", "Altxerri", "Kutxa Fundazioa Kluba", "Sala BBK"],
}

TRIBUTO = re.compile(r"tribut|homenaje|tribute|plays |experience\b|show by|candlelight",
                     re.IGNORECASE)


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
    print(f"1) {len(eventos)} eventos futuros en Kulturklik ({pagina} páginas)")
    return eventos


def en_zona(e: dict) -> bool:
    prov = str(e.get("provinceNoraCode"))
    return prov in PROVINCIAS or (prov == "31" and e.get("municipalityEs") in NAVARRA_MUNICIPIOS)


def tamano(lugar: str) -> str:
    for nivel, nombres in RECINTOS.items():
        if any(n.lower() in lugar.lower() for n in nombres):
            return nivel
    return "otro"


def limpiar_titulo(t: str) -> str:
    t = re.sub(r"^\s*entradas( concierto)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*\((\d{1,2}|[^)]*\b20\d\d\b|[^)]*(enero|febrero|marzo|abril|mayo|junio|julio|"
               r"agosto|septiembre|octubre|noviembre|diciembre)[^)]*)\)\s*$", "", t,
               flags=re.IGNORECASE)
    return t.strip(" -–:")


def main():
    meses = 6
    if "--meses" in sys.argv:
        meses = int(sys.argv[sys.argv.index("--meses") + 1])
    hoy = datetime.now(ZONA).date()
    limite = hoy + timedelta(days=round(meses * 30.5))

    brutos = descargar_todo()
    vistos, salida = {}, []
    for e in brutos:
        if e.get("typeEs") not in TIPOS or not en_zona(e):
            continue
        inicio = (e.get("startDate") or "")[:10]
        fin = (e.get("endDate") or "")[:10]
        if not inicio or fin < hoy.isoformat() or inicio > limite.isoformat():
            continue
        titulo = limpiar_titulo(e.get("nameEs") or "")
        lugar = e.get("establishmentEs") or e.get("placeEs") or ""
        clave = (titulo.lower(), inicio, e.get("municipalityEs"))
        if clave in vistos:  # el mismo concierto publicado dos veces
            continue
        vistos[clave] = True
        salida.append({
            "id": e.get("id"),
            "fecha": inicio,
            "fin": fin if fin != inicio else "",
            "hora": (e.get("openingHoursEs") or "").strip()[:13],
            "titulo": titulo,
            "tipo": e.get("typeEs"),
            "lugar": lugar,
            "ciudad": e.get("municipalityEs") or "",
            "zona": PROVINCIAS.get(str(e.get("provinceNoraCode")), "Pamplona"),
            "recinto": tamano(f"{lugar} {titulo}"),
            "precio": (e.get("priceEs") or "").strip(),
            "tributo": bool(TRIBUTO.search(titulo)),
            "url": e.get("purchaseUrlEs") or e.get("sourceUrlEs") or e.get("urlEventEs") or "",
            "fuente": e.get("sourceUrlEs") or e.get("urlEventEs") or "",
            "descripcion": re.sub(r"<[^>]+>", " ", e.get("descriptionEs") or "")[:300].strip(),
        })
    salida.sort(key=lambda x: (x["fecha"], x["titulo"]))

    from collections import Counter
    print(f"2) {len(salida)} conciertos y festivales hasta {limite} en la zona")
    print("   por recinto:", dict(Counter(x["recinto"] for x in salida)))
    print("   tributos marcados:", sum(x["tributo"] for x in salida))

    with open(os.path.join(CARPETA, "candidatos.json"), "w", encoding="utf-8") as f:
        json.dump({"generado": hoy.isoformat(), "hasta": limite.isoformat(), "eventos": salida},
                  f, ensure_ascii=False, indent=1)
    print("3) Guardado candidatos.json")


if __name__ == "__main__":
    main()
