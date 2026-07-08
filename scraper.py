# -*- coding: utf-8 -*-
"""
Robot de collecte des concours suisses romands.
Récupère les concours depuis Concours.ch, Radin.ch et Win4win,
puis écrit le résultat dans concours.json (lu par index.html).

Chaque source est isolée : si l'une casse, les autres continuent.
"""
import json
import re
import sys
from datetime import datetime, date

import requests
import feedparser
from bs4 import BeautifulSoup

ENTETES = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "fr-CH,fr;q=0.9",
}

MOIS_FR = {
    "janv": 1, "jan": 1, "janvier": 1,
    "fevr": 2, "fev": 2, "fevrier": 2,
    "mars": 3, "mar": 3,
    "avr": 4, "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7, "juillet": 7,
    "aout": 8, "aou": 8,
    "sept": 9, "sep": 9, "septembre": 9,
    "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11,
    "dec": 12, "decembre": 12,
}


def sans_accents(texte):
    return (texte.replace("é", "e").replace("è", "e").replace("ê", "e")
                 .replace("û", "u").replace("à", "a").replace("â", "a")
                 .replace("î", "i").replace("ô", "o").replace("ç", "c"))


def date_francaise_vers_iso(jour, mois_txt, annee):
    """Convertit '12', 'juil.', '2026' -> '2026-07-12'"""
    try:
        jour = int(str(jour).replace("er", ""))
        cle = sans_accents(mois_txt.lower().strip().rstrip("."))
        mois = MOIS_FR.get(cle) or MOIS_FR.get(cle[:4]) or MOIS_FR.get(cle[:3])
        if not mois:
            return None
        return "%04d-%02d-%02d" % (int(annee), mois, jour)
    except Exception:
        return None


def chercher_date_fin(texte):
    """Cherche 'se termine le 12 juil. 2026' ou 'jusqu'au 12 juillet 2026' dans un texte."""
    if not texte:
        return None
    motif = r"(?:se termine le|jusqu.au|dernier jour[:\s]*le?)\s+(1er|\d{1,2})\s+([A-Za-zéèêûàâîôç]+)\.?\s+(\d{4})"
    m = re.search(motif, texte, re.IGNORECASE)
    if m:
        return date_francaise_vers_iso(m.group(1), m.group(2), m.group(3))
    return None


# ---------------------------------------------------------------- Concours.ch
def source_concours_ch():
    """Scrape la liste publique https://www.concours.ch/concours/tous"""
    resultats = []
    r = requests.get("https://www.concours.ch/concours/tous", headers=ENTETES, timeout=30)
    r.raise_for_status()
    soupe = BeautifulSoup(r.text, "html.parser")
    for a in soupe.find_all("a", href=True):
        href = a["href"]
        # les fiches concours ont la forme /emetteur/slug-123456
        if not re.search(r"concours\.ch/[^/]+/[^/]+-\d+$", href):
            continue
        titre_attr = a.get("title", "") or ""
        titre = titre_attr.split(", Date de publication")[0].strip()
        texte_lien = a.get_text(" ", strip=True)
        if not titre:
            titre = texte_lien[:120]
        if not titre:
            continue
        date_fin = chercher_date_fin(texte_lien) or chercher_date_fin(titre_attr)
        resultats.append({
            "titre": titre[:140],
            "lot": "",
            "lien": href,
            "dateFin": date_fin,
            "source": "Concours.ch",
        })
    return resultats


# ------------------------------------------------------------------- Radin.ch
def source_radin():
    """Flux RSS de la catégorie concours de Radin.ch"""
    resultats = []
    flux = feedparser.parse("https://radin.ch/category/concours-suisse/feed/",
                            request_headers=ENTETES)
    for entree in flux.entries:
        titre = (entree.get("title") or "").strip()
        lien = (entree.get("link") or "").strip()
        if not titre or not lien:
            continue
        resume = re.sub(r"<[^>]+>", " ", entree.get("summary", "") or "")
        resultats.append({
            "titre": titre[:140],
            "lot": "",
            "lien": lien,
            "dateFin": chercher_date_fin(titre) or chercher_date_fin(resume),
            "source": "Radin.ch",
        })
    return resultats


# ------------------------------------------------------------------- Win4win
def source_win4win():
    """Flux RSS français de Win4win, filtré sur les concours."""
    resultats = []
    flux = feedparser.parse("https://win4win.ch/fr/feed/", request_headers=ENTETES)
    for entree in flux.entries:
        titre = (entree.get("title") or "").strip()
        lien = (entree.get("link") or "").strip()
        if not titre or not lien:
            continue
        categories = " ".join(t.get("term", "") for t in entree.get("tags", [])).lower()
        if "concours" not in lien.lower() and "concours" not in categories \
           and "wettbewerb" not in categories:
            continue
        resume = re.sub(r"<[^>]+>", " ", entree.get("summary", "") or "")
        resultats.append({
            "titre": titre[:140],
            "lot": "",
            "lien": lien,
            "dateFin": chercher_date_fin(titre) or chercher_date_fin(resume),
            "source": "Win4win",
        })
    return resultats


# ---------------------------------------------------------------------- main
def normaliser_lien(u):
    u = (u or "").lower().split("?")[0].rstrip("/")
    return u.replace("https://", "").replace("http://", "").replace("www.", "")


def main():
    sources = [
        ("Concours.ch", source_concours_ch),
        ("Radin.ch", source_radin),
        ("Win4win", source_win4win),
    ]
    tous, erreurs = [], []
    for nom, fonction in sources:
        try:
            trouves = fonction()
            print(f"[OK] {nom}: {len(trouves)} concours")
            tous.extend(trouves)
        except Exception as e:
            print(f"[ERREUR] {nom}: {e}", file=sys.stderr)
            erreurs.append(nom)

    # dédoublonnage par lien + suppression des concours déjà expirés
    aujourd_hui = date.today().isoformat()
    vus, uniques = set(), []
    for c in tous:
        cle = normaliser_lien(c["lien"])
        if not cle or cle in vus:
            continue
        if c.get("dateFin") and c["dateFin"] < aujourd_hui:
            continue
        vus.add(cle)
        uniques.append(c)

    donnees = {
        "maj": datetime.now().isoformat(timespec="minutes"),
        "erreurs": erreurs,
        "concours": uniques[:120],
    }
    with open("concours.json", "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=1)
    print(f"Total: {len(uniques)} concours écrits dans concours.json")

    # le robot ne doit jamais échouer complètement si au moins une source marche
    if len(uniques) == 0 and erreurs:
        sys.exit(1)


if __name__ == "__main__":
    main()
