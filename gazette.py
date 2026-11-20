#!/usr/bin/env python3
"""La Gazette de Noël — une newsletter quotidienne du 1er au 24 décembre.

Le programme pioche au hasard une photo, une histoire, une tradition, une
recette, une phrase (et éventuellement une énigme) dans les dossiers de
contenu, compose un joli mail et l'envoie. Un élément déjà utilisé n'est
jamais repris.

Aucune dépendance : uniquement la bibliothèque standard de Python (>= 3.8),
pour tourner tel quel sur un Synology.

Commandes :
    python3 gazette.py envoyer     # à lancer chaque matin (Planificateur de tâches)
    python3 gazette.py verifier    # vérifie la config, Gmail et compte les contenus
    python3 gazette.py apercu      # génère apercu.html avec les contenus d'EXEMPLE
    python3 gazette.py test-mail   # envoie un mail de test avec les contenus d'EXEMPLE
"""

import argparse
import base64
import configparser
import datetime as dt
import html
import json
import mimetypes
import os
import random
import re
import smtplib
import ssl
import string
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path
from typing import Dict, List, Optional

ICI = Path(__file__).resolve().parent
DONNEES = ICI / "donnees"
FICHIER_ETAT = DONNEES / "etat.json"
FICHIER_JOURNAL = DONNEES / "journal.txt"
MODELE = ICI / "modele" / "gazette.html"
EXEMPLES = ICI / "exemples"

EXT_TEXTE = {".txt", ".md"}
EXT_PHOTO = {".jpg", ".jpeg", ".png", ".gif"}
TAILLE_MAX_PHOTO = 15 * 1024 * 1024  # au-delà, la photo est ignorée (limite Gmail : 25 Mo)

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Rubriques texte : (dossier, emoji, titre affiché)
RUBRIQUES = {
    "enigmes": ("🧩", "La petite énigme"),
    "histoires": ("✨", "L'histoire du jour"),
    "traditions": ("🌍", "Noël autour du monde"),
    "recettes": ("🍪", "La pause gourmande"),
    "phrases": ("💌", "Le petit mot du jour"),
}

COULEURS = {
    "fond": "#efe6d2",
    "papier": "#fffaf0",
    "vert": "#1f4d3a",
    "rouge": "#a4282c",
    "or": "#b8912f",
    "texte": "#3b2f25",
    "doux": "#7a6a58",
}


# --------------------------------------------------------------------------
# Journal (technique uniquement : jamais de contenu, pour préserver la surprise)
# --------------------------------------------------------------------------

def journal(message: str) -> None:
    ligne = "{}  {}".format(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message)
    print(ligne)
    try:
        DONNEES.mkdir(exist_ok=True)
        with open(FICHIER_JOURNAL, "a", encoding="utf-8") as f:
            f.write(ligne + "\n")
    except OSError:
        pass


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

class Config:
    def __init__(self, chemin: Path):
        if not chemin.exists():
            raise SystemExit(
                "Fichier de configuration introuvable : {}\n"
                "Copiez config.exemple.ini en config.ini et remplissez-le.".format(chemin)
            )
        cp = configparser.ConfigParser()
        cp.read(chemin, encoding="utf-8")
        g = cp["gazette"] if cp.has_section("gazette") else {}
        m = cp["mail"] if cp.has_section("mail") else {}

        self.titre = g.get("titre", "La Gazette de Noël")
        dossier = g.get("dossier_contenu", "").strip()
        self.dossier_contenu = Path(dossier) if dossier else ICI / "contenu"
        if not self.dossier_contenu.is_absolute():
            self.dossier_contenu = (ICI / self.dossier_contenu).resolve()
        heure = g.get("heure_envoi", "08:00").strip()
        h, _, mn = heure.partition(":")
        self.heure_envoi = dt.time(int(h), int(mn or 0))
        self.afficher_dossier_photo = g.get("afficher_dossier_photo", "oui").strip().lower() in (
            "oui", "yes", "true", "1")

        self.expediteur = m.get("expediteur", "").strip()
        self.nom_expediteur = m.get("nom_expediteur", self.titre).strip()
        # Identifiant de connexion SMTP (Brevo : « xxx@smtp-brevo.com ») ; par défaut l'expéditeur (Gmail)
        self.identifiant = m.get("identifiant", "").strip() or self.expediteur
        self.mot_de_passe = os.environ.get("GAZETTE_MOT_DE_PASSE") or m.get("mot_de_passe", "")
        self.mot_de_passe = self.mot_de_passe.replace(" ", "")
        self.destinataires = [a.strip() for a in re.split(r"[,;\s]+", m.get("destinataires", "")) if a.strip()]
        self.serveur = m.get("serveur", "smtp.gmail.com").strip()
        self.port = int(m.get("port", "465"))

    def verifier_mail(self) -> List[str]:
        erreurs = []
        if not self.expediteur:
            erreurs.append("[mail] expediteur est vide")
        if not self.mot_de_passe:
            erreurs.append("[mail] mot_de_passe est vide")
        if not self.destinataires:
            erreurs.append("[mail] destinataires est vide")
        return erreurs


# --------------------------------------------------------------------------
# État : éléments déjà utilisés, éditions envoyées, réponse en attente
# --------------------------------------------------------------------------

def charger_etat() -> dict:
    if FICHIER_ETAT.exists():
        with open(FICHIER_ETAT, encoding="utf-8") as f:
            etat = json.load(f)
    else:
        etat = {}
    etat.setdefault("utilises", [])
    etat.setdefault("envois", {})
    etat.setdefault("reponse_en_attente", None)
    return etat


def sauver_etat(etat: dict) -> None:
    DONNEES.mkdir(exist_ok=True)
    tmp = FICHIER_ETAT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FICHIER_ETAT)  # écriture atomique


# --------------------------------------------------------------------------
# Lecture des contenus
# --------------------------------------------------------------------------

def est_ignore(chemin: Path, racine: Path) -> bool:
    """Ignore les dossiers techniques (@eaDir de Synology, #recycle, fichiers cachés...)."""
    for partie in chemin.relative_to(racine).parts:
        if partie.startswith((".", "@", "#", "~")) or partie.lower() in ("thumbs.db", "desktop.ini"):
            return True
    return False


def lister_fichiers(dossier: Path, extensions: set) -> List[Path]:
    if not dossier.is_dir():
        return []
    fichiers = []
    for racine, dossiers, noms in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if not d.startswith((".", "@", "#")))
        for nom in sorted(noms):
            p = Path(racine) / nom
            if p.suffix.lower() in extensions and not est_ignore(p, dossier):
                fichiers.append(p)
    return fichiers


def lire_texte(chemin: Path) -> str:
    brut = chemin.read_bytes()
    for encodage in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return brut.decode(encodage).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return brut.decode("utf-8", errors="replace")


RE_REPONSE = re.compile(r"^\s*(r[ée]ponse|solution)\s*:?\s*", re.IGNORECASE)


class Element:
    """Un élément de contenu pioché dans un dossier."""

    def __init__(self, cle: str, titre: Optional[str], corps: str, reponse: Optional[str] = None,
                 chemin: Optional[Path] = None, legende: Optional[str] = None):
        self.cle = cle
        self.titre = titre
        self.corps = corps
        self.reponse = reponse
        self.chemin = chemin
        self.legende = legende


def analyser_fichier_texte(texte: str, avec_reponse: bool = False):
    """Première ligne = titre (s'il y a une suite), le reste = texte.
    Pour les énigmes, tout ce qui suit une ligne « Réponse : » est la réponse."""
    lignes = texte.strip().split("\n")
    reponse = None
    if avec_reponse:
        for i, ligne in enumerate(lignes):
            if RE_REPONSE.match(ligne):
                premiere = RE_REPONSE.sub("", ligne, count=1)
                reponse = "\n".join([premiere] + lignes[i + 1:]).strip() or None
                lignes = lignes[:i]
                break
    while lignes and not lignes[0].strip():
        lignes.pop(0)
    if not lignes:
        return None, "", reponse
    reste = "\n".join(lignes[1:]).strip()
    if reste:
        return lignes[0].strip(), reste, reponse
    return None, lignes[0].strip(), reponse


def candidats_texte(dossier_contenu: Path, rubrique: str) -> List[Element]:
    dossier = dossier_contenu / rubrique
    elements = []
    for f in lister_fichiers(dossier, EXT_TEXTE):
        rel = f.relative_to(dossier_contenu).as_posix()
        texte = lire_texte(f)
        if rubrique == "phrases":
            # Un fichier peut contenir une seule phrase ou une phrase par ligne.
            for ligne in texte.split("\n"):
                ligne = ligne.strip().lstrip("-•*").strip()
                if ligne:
                    elements.append(Element("{}#{}".format(rel, ligne), None, ligne))
            continue
        titre, corps, reponse = analyser_fichier_texte(texte, avec_reponse=(rubrique == "enigmes"))
        if corps:
            elements.append(Element(rel, titre, corps, reponse))
    return elements


def candidats_photos(dossier_contenu: Path) -> List[Element]:
    dossier = dossier_contenu / "photos"
    elements = []
    for f in lister_fichiers(dossier, EXT_PHOTO):
        try:
            if f.stat().st_size > TAILLE_MAX_PHOTO or f.stat().st_size == 0:
                continue
        except OSError:
            continue
        parent = f.parent.relative_to(dossier)
        legende = " · ".join(parent.parts) if parent.parts else None
        elements.append(Element(f.relative_to(dossier_contenu).as_posix(), None, "", chemin=f, legende=legende))
    return elements


def piocher(candidats: List[Element], utilises: set, rng: random.Random) -> Optional[Element]:
    libres = [c for c in candidats if c.cle not in utilises]
    return rng.choice(libres) if libres else None


# --------------------------------------------------------------------------
# Mise en forme
# --------------------------------------------------------------------------

RE_URL = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\]'\"])")


def texte_vers_html(texte: str) -> str:
    """Lignes vides = paragraphes ; lignes commençant par - ou • = liste ;
    les liens deviennent cliquables."""
    c = COULEURS
    morceaux = []
    for bloc in re.split(r"\n\s*\n", texte.strip()):
        lignes = [l.rstrip() for l in bloc.split("\n") if l.strip()]
        if not lignes:
            continue
        puces = [re.match(r"^\s*([-•*–]|\d+[.)])\s+", l) for l in lignes]
        if all(puces):
            ordonnee = all(re.match(r"^\s*\d+[.)]", l) for l in lignes)
            balise = "ol" if ordonnee else "ul"
            items = "".join(
                '<li style="margin:0 0 6px 0;">{}</li>'.format(lier(html.escape(l[m.end():])))
                for l, m in zip(lignes, puces))
            morceaux.append('<{0} style="margin:0 0 14px 0;padding-left:22px;">{1}</{0}>'.format(balise, items))
        else:
            contenu = "<br>".join(lier(html.escape(l.strip())) for l in lignes)
            morceaux.append('<p style="margin:0 0 14px 0;">{}</p>'.format(contenu))
    return "\n".join(morceaux).replace("<a ", '<a style="color:{};" '.format(c["rouge"]))


def lier(texte_echappe: str) -> str:
    return RE_URL.sub(r'<a href="\1">\1</a>', texte_echappe)


def date_fr(jour: dt.date) -> str:
    j = "1er" if jour.day == 1 else str(jour.day)
    return "{} {} décembre {}".format(JOURS[jour.weekday()].capitalize(), j, jour.year)


def compte_a_rebours(jour: dt.date) -> str:
    reste = 25 - jour.day
    if reste <= 1:
        return "C'est le réveillon ! Joyeux Noël 🎅"
    return "Plus que {} dodos avant Noël".format(reste)


def section_html(emoji: str, titre_rubrique: str, contenu: str) -> str:
    c = COULEURS
    return (
        '<tr><td style="padding:26px 30px 6px 30px;">'
        '<div style="font-family:Georgia,\'Times New Roman\',serif;font-size:13px;letter-spacing:2px;'
        'text-transform:uppercase;color:{rouge};border-bottom:1px solid {or_};padding-bottom:6px;'
        'margin-bottom:14px;">{emoji}&nbsp; {titre}</div>{contenu}</td></tr>'
    ).format(rouge=c["rouge"], or_=c["or"], emoji=emoji, titre=html.escape(titre_rubrique), contenu=contenu)


def titre_article(titre: Optional[str]) -> str:
    if not titre:
        return ""
    return ('<h2 style="font-family:Georgia,\'Times New Roman\',serif;font-size:22px;line-height:1.25;'
            'color:{};margin:0 0 12px 0;font-weight:bold;">{}</h2>').format(COULEURS["vert"], html.escape(titre))


class Edition:
    def __init__(self, jour: dt.date):
        self.jour = jour
        self.numero = jour.day
        self.photo = None  # type: Optional[Element]
        self.rubriques = {}  # type: Dict[str, Element]
        self.reponse_veille = None  # type: Optional[dict]

    def cles_utilisees(self) -> List[str]:
        cles = [e.cle for e in self.rubriques.values()]
        if self.photo:
            cles.append(self.photo.cle)
        return cles


def composer(jour: dt.date, dossier_contenu: Path, utilises: set, reponse_veille: Optional[dict],
             rng: random.Random) -> Edition:
    ed = Edition(jour)
    ed.photo = piocher(candidats_photos(dossier_contenu), utilises, rng)
    for rubrique in RUBRIQUES:
        el = piocher(candidats_texte(dossier_contenu, rubrique), utilises, rng)
        if el:
            ed.rubriques[rubrique] = el
    ed.reponse_veille = reponse_veille
    return ed


def rendre_html(ed: Edition, titre_gazette: str, src_photo: Optional[str]) -> str:
    c = COULEURS
    sections = []

    if ed.photo and src_photo:
        legende = ""
        if ed.photo.legende:
            legende = ('<div style="font-family:Georgia,serif;font-style:italic;font-size:14px;color:{};'
                       'text-align:center;margin-top:10px;">📁 {}</div>').format(c["doux"], html.escape(ed.photo.legende))
        contenu = ('<img src="{}" alt="Le souvenir du jour" width="540" style="display:block;width:100%;'
                   'max-width:540px;height:auto;border:6px solid #ffffff;box-shadow:0 2px 6px rgba(0,0,0,.15);'
                   'margin:0 auto;">{}').format(src_photo, legende)
        sections.append(section_html("📸", "Le souvenir du jour", contenu))

    if ed.reponse_veille:
        r = ed.reponse_veille
        contenu = ('<div style="background:{fond};border-left:4px solid {or_};padding:12px 16px;margin:0 0 4px 0;">'
                   '<div style="font-size:13px;color:{doux};margin-bottom:6px;">La réponse à l\'énigme d\'hier{t}…</div>'
                   '{rep}</div>').format(
            fond=c["fond"], or_=c["or"], doux=c["doux"],
            t=" (« {} »)".format(html.escape(r["titre"])) if r.get("titre") else "",
            rep=texte_vers_html(r["reponse"]))
        sections.append(section_html("🔓", "La solution d'hier", contenu))

    derniere_reponse = None
    for rubrique, (emoji, nom) in RUBRIQUES.items():
        el = ed.rubriques.get(rubrique)
        if not el:
            continue
        if rubrique == "phrases":
            contenu = ('<p style="font-family:Georgia,serif;font-style:italic;font-size:20px;line-height:1.5;'
                       'color:{};text-align:center;margin:6px 10px 14px 10px;">« {} »</p>').format(
                c["vert"], html.escape(el.corps))
        else:
            contenu = titre_article(el.titre) + texte_vers_html(el.corps)
            if rubrique == "enigmes" and el.reponse:
                if ed.numero >= 24:
                    contenu += ('<p style="font-size:13px;color:{};font-style:italic;">'
                                'Pas de Gazette demain… la réponse est tout en bas !</p>').format(c["doux"])
                    derniere_reponse = el.reponse
                else:
                    contenu += ('<p style="font-size:13px;color:{};font-style:italic;">'
                                'Réponse dans la Gazette de demain !</p>').format(c["doux"])
        sections.append(section_html(emoji, nom, contenu))

    if ed.numero >= 24:
        conclusion = "Joyeux Noël à vous deux ! 🎄 Merci d'avoir lu la Gazette chaque matin."
    else:
        conclusion = "À demain matin pour la Gazette n°{} !".format(ed.numero + 1)

    solution_finale = ""
    if derniere_reponse:
        solution_finale = ('<tr><td style="padding:40px 30px 30px 30px;font-size:14px;color:{};">'
                           '<b>Réponse de la dernière énigme :</b>{}</td></tr>').format(
            c["doux"], texte_vers_html(derniere_reponse))

    modele = string.Template(MODELE.read_text(encoding="utf-8"))
    return modele.safe_substitute(
        titre_page=html.escape("{} — n°{}".format(titre_gazette, ed.numero)),
        titre=html.escape(titre_gazette),
        numero=ed.numero,
        date=html.escape(date_fr(ed.jour)),
        compte_a_rebours=html.escape(compte_a_rebours(ed.jour)),
        sections="\n".join(sections),
        conclusion=html.escape(conclusion),
        solution_finale=solution_finale,
        **{k: v for k, v in c.items()}
    )


def rendre_texte(ed: Edition, titre_gazette: str) -> str:
    morceaux = ["{} — n°{}".format(titre_gazette, ed.numero), date_fr(ed.jour), compte_a_rebours(ed.jour), ""]
    if ed.photo:
        morceaux.append("📸 Le souvenir du jour : (photo jointe)")
        morceaux.append("")
    if ed.reponse_veille:
        morceaux += ["🔓 La solution d'hier", ed.reponse_veille["reponse"], ""]
    for rubrique, (emoji, nom) in RUBRIQUES.items():
        el = ed.rubriques.get(rubrique)
        if el:
            morceaux.append("{} {}".format(emoji, nom))
            if el.titre:
                morceaux.append(el.titre.upper())
            morceaux += [el.corps, ""]
    morceaux.append("Joyeux Noël !" if ed.numero >= 24 else "À demain matin pour la Gazette n°{} !".format(ed.numero + 1))
    return "\n".join(morceaux)


def sujet(ed: Edition, titre_gazette: str) -> str:
    return "🎄 {} n°{} · {}".format(titre_gazette, ed.numero, date_fr(ed.jour))


# --------------------------------------------------------------------------
# Mail
# --------------------------------------------------------------------------

def construire_message(ed: Edition, cfg: Config, prefixe_sujet: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = prefixe_sujet + sujet(ed, cfg.titre)
    msg["From"] = formataddr((cfg.nom_expediteur, cfg.expediteur))
    msg["To"] = ", ".join(cfg.destinataires)
    # Identifiant stable : si un renvoi a lieu malgré tout, Gmail le regroupe.
    msg["Message-ID"] = "<gazette-{}@gazette-noel>".format(ed.jour.isoformat())
    msg.set_content(rendre_texte(ed, cfg.titre))

    cid = None
    if ed.photo:
        cid = make_msgid(domain="gazette-noel")
    msg.add_alternative(rendre_html(ed, cfg.titre, "cid:" + cid[1:-1] if cid else None), subtype="html")

    if ed.photo and cid:
        type_mime = mimetypes.guess_type(ed.photo.chemin.name)[0] or "image/jpeg"
        principal, secondaire = type_mime.split("/")
        partie_html = msg.get_payload()[1]
        partie_html.add_related(ed.photo.chemin.read_bytes(), maintype=principal, subtype=secondaire,
                                cid=cid, filename="souvenir" + ed.photo.chemin.suffix.lower())
    return msg


def connexion_smtp(cfg: Config) -> smtplib.SMTP:
    ctx = ssl.create_default_context()
    if cfg.port == 465:
        smtp = smtplib.SMTP_SSL(cfg.serveur, cfg.port, context=ctx, timeout=60)
    else:
        smtp = smtplib.SMTP(cfg.serveur, cfg.port, timeout=60)
        smtp.starttls(context=ctx)
    smtp.login(cfg.identifiant, cfg.mot_de_passe)
    return smtp


def envoyer_message(msg: EmailMessage, cfg: Config, tentatives: int = 3) -> None:
    derniere = None
    for i in range(1, tentatives + 1):
        try:
            smtp = connexion_smtp(cfg)
            try:
                smtp.send_message(msg)
            finally:
                try:
                    smtp.quit()
                except Exception:
                    pass
            return
        except smtplib.SMTPAuthenticationError:
            raise
        except (smtplib.SMTPException, OSError) as e:
            derniere = e
            journal("Échec d'envoi (tentative {}/{}) : {}".format(i, tentatives, type(e).__name__))
            if i < tentatives:
                time.sleep(30 * i)
    raise RuntimeError("Envoi impossible après {} tentatives : {}".format(tentatives, derniere))


def archiver(ed: Edition, cfg: Config) -> None:
    """Garde une copie de l'édition envoyée (après envoi uniquement : pas de spoiler)."""
    src = None
    if ed.photo:
        type_mime = mimetypes.guess_type(ed.photo.chemin.name)[0] or "image/jpeg"
        src = "data:{};base64,{}".format(type_mime, base64.b64encode(ed.photo.chemin.read_bytes()).decode())
    dossier = DONNEES / "archives" / str(ed.jour.year)
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "{:02d}.html".format(ed.numero)).write_text(rendre_html(ed, cfg.titre, src), encoding="utf-8")


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------

def jour_de_gazette(jour: dt.date) -> bool:
    return jour.month == 12 and 1 <= jour.day <= 24


def cmd_envoyer(cfg: Config, maintenant: Optional[dt.datetime] = None, force: bool = False) -> int:
    maintenant = maintenant or dt.datetime.now()
    jour = maintenant.date()
    if not jour_de_gazette(jour):
        journal("Pas de Gazette aujourd'hui ({}).".format(jour.isoformat()))
        return 0
    if not force and maintenant.time() < cfg.heure_envoi:
        journal("Trop tôt : envoi prévu à {}.".format(cfg.heure_envoi.strftime("%H:%M")))
        return 0
    erreurs = cfg.verifier_mail()
    if erreurs:
        journal("Configuration incomplète : " + " ; ".join(erreurs))
        return 1

    etat = charger_etat()
    if jour.isoformat() in etat["envois"]:
        journal("Gazette n°{} déjà envoyée aujourd'hui.".format(jour.day))
        return 0

    ed = composer(jour, cfg.dossier_contenu, set(etat["utilises"]), etat.get("reponse_en_attente"), random.Random())
    if not ed.photo and not ed.rubriques:
        journal("ERREUR : aucun contenu disponible dans {}".format(cfg.dossier_contenu))
        return 1
    manquantes = [r for r in RUBRIQUES if r not in ed.rubriques and r != "enigmes"]
    if not ed.photo:
        manquantes.insert(0, "photos")
    if manquantes:
        journal("Attention : rubriques sans contenu disponible : " + ", ".join(manquantes))

    if ed.photo and not cfg.afficher_dossier_photo:
        ed.photo.legende = None
    msg = construire_message(ed, cfg)
    envoyer_message(msg, cfg)

    etat["utilises"].extend(ed.cles_utilisees())
    enigme = ed.rubriques.get("enigmes")
    etat["reponse_en_attente"] = (
        {"titre": enigme.titre, "reponse": enigme.reponse} if enigme and enigme.reponse and ed.numero < 24 else None)
    etat["envois"][jour.isoformat()] = {
        "numero": ed.numero,
        "heure": maintenant.strftime("%H:%M:%S"),
        "destinataires": len(cfg.destinataires),
    }
    sauver_etat(etat)
    try:
        archiver(ed, cfg)
    except OSError as e:
        journal("Archivage impossible : {}".format(e))
    journal("Gazette n°{} envoyée à {} destinataire(s).".format(ed.numero, len(cfg.destinataires)))
    return 0


def cmd_verifier(cfg: Config, tester_mail: bool = True) -> int:
    ok = True
    print("Dossier de contenu : {}".format(cfg.dossier_contenu))
    if not cfg.dossier_contenu.is_dir():
        print("  ✗ dossier introuvable !")
        ok = False
    etat = charger_etat()
    utilises = set(etat["utilises"])
    print("\nContenus disponibles (non encore utilisés) — les contenus eux-mêmes ne sont pas affichés :")
    rubriques = [("photos", candidats_photos(cfg.dossier_contenu))]
    rubriques += [(r, candidats_texte(cfg.dossier_contenu, r)) for r in RUBRIQUES]
    for nom, cands in rubriques:
        libres = len([c for c in cands if c.cle not in utilises])
        alerte = "" if libres >= 24 or nom == "enigmes" else "   ⚠ moins de 24"
        print("  {:<11} {:>4} disponible(s){}".format(nom, libres, alerte))
    print("\nÉditions déjà envoyées : {}".format(len(etat["envois"])))

    erreurs = cfg.verifier_mail()
    if erreurs:
        print("\n✗ " + "\n✗ ".join(erreurs))
        return 1
    print("\nExpéditeur : {}\nDestinataires : {}".format(cfg.expediteur, ", ".join(cfg.destinataires)))
    if tester_mail:
        try:
            connexion_smtp(cfg).quit()
            print("✓ Connexion à Gmail réussie")
        except Exception as e:
            print("✗ Connexion à Gmail impossible : {}".format(e))
            ok = False
    return 0 if ok else 1


def edition_exemple(jour: dt.date, dossier: Path) -> Edition:
    rng = random.Random(jour.toordinal())
    veille = None
    deja = set()
    if jour.day > 1:
        precedente = composer(jour - dt.timedelta(days=1), dossier, set(), None, random.Random(jour.toordinal() - 1))
        deja = set(precedente.cles_utilisees())
        e = precedente.rubriques.get("enigmes")
        if e and e.reponse:
            veille = {"titre": e.titre, "reponse": e.reponse}
    ed = composer(jour, dossier, deja, veille, rng)
    if not ed.photo:  # peu d'exemples : on réautorise la photo d'hier
        ed.photo = piocher(candidats_photos(dossier), set(), rng)
    return ed


def cmd_apercu(cfg: Optional[Config], jour: dt.date, dossier: Path, sortie: Path) -> int:
    ed = edition_exemple(jour, dossier)
    src = None
    if ed.photo:
        type_mime = mimetypes.guess_type(ed.photo.chemin.name)[0] or "image/jpeg"
        src = "data:{};base64,{}".format(type_mime, base64.b64encode(ed.photo.chemin.read_bytes()).decode())
    titre = cfg.titre if cfg else "La Gazette de Noël"
    sortie.write_text(rendre_html(ed, titre, src), encoding="utf-8")
    print("Aperçu écrit dans {}".format(sortie))
    return 0


def cmd_test_mail(cfg: Config, jour: dt.date, dossier: Path) -> int:
    erreurs = cfg.verifier_mail()
    if erreurs:
        print("✗ " + "\n✗ ".join(erreurs))
        return 1
    ed = edition_exemple(jour, dossier)
    if ed.photo and not cfg.afficher_dossier_photo:
        ed.photo.legende = None
    msg = construire_message(ed, cfg, prefixe_sujet="[TEST] ")
    del msg["Message-ID"]
    msg["Message-ID"] = make_msgid(domain="gazette-noel")
    envoyer_message(msg, cfg, tentatives=1)
    print("Mail de test envoyé à {}".format(", ".join(cfg.destinataires)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    for flux in (sys.stdout, sys.stderr):  # console Windows : pas de plantage sur les emojis
        try:
            flux.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(description="La Gazette de Noël")
    p.add_argument("--config", default=str(ICI / "config.ini"), help="chemin du fichier de configuration")
    sp = p.add_subparsers(dest="commande")

    e = sp.add_parser("envoyer", help="envoie la Gazette du jour (si pas déjà fait)")
    e.add_argument("--force", action="store_true", help="ignore l'heure d'envoi")

    v = sp.add_parser("verifier", help="vérifie la configuration et compte les contenus")
    v.add_argument("--sans-mail", action="store_true", help="ne teste pas la connexion Gmail")

    for nom, aide in (("apercu", "génère apercu.html avec les contenus d'exemple"),
                      ("test-mail", "envoie un mail de test avec les contenus d'exemple")):
        s = sp.add_parser(nom, help=aide)
        s.add_argument("--date", help="date simulée, ex. 2026-12-05 (défaut : 5 décembre)")
        s.add_argument("--contenu", default=str(EXEMPLES),
                       help="dossier de contenu à utiliser (défaut : exemples/ — pour ne rien gâcher !)")
        if nom == "apercu":
            s.add_argument("--sortie", default=str(ICI / "apercu.html"))

    args = p.parse_args(argv)
    if not args.commande:
        p.print_help()
        return 0

    if args.commande in ("apercu", "test-mail"):
        jour = dt.date.fromisoformat(args.date) if args.date else dt.date(dt.date.today().year, 12, 5)
        if not jour_de_gazette(jour):
            print("La date doit être entre le 1er et le 24 décembre.")
            return 1
        if args.commande == "apercu":
            cfg = Config(Path(args.config)) if Path(args.config).exists() else None
            return cmd_apercu(cfg, jour, Path(args.contenu), Path(args.sortie))
        return cmd_test_mail(Config(Path(args.config)), jour, Path(args.contenu))

    cfg = Config(Path(args.config))
    if args.commande == "verifier":
        return cmd_verifier(cfg, tester_mail=not args.sans_mail)
    try:
        return cmd_envoyer(cfg, force=args.force)
    except Exception as ex:  # on journalise le type d'erreur, jamais le contenu
        journal("ERREUR : {}: {}".format(type(ex).__name__, ex))
        return 1


if __name__ == "__main__":
    sys.exit(main())
