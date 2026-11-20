import datetime as dt
import email
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gazette  # noqa: E402


class FauxSMTP:
    envoyes = []

    def __init__(self, *a, **k):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        FauxSMTP.envoyes.append(msg)

    def quit(self):
        pass


class TestGazette(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.contenu = self.tmp / "contenu"
        for r in ("histoires", "traditions", "recettes", "enigmes", "phrases", "photos/2019", "photos/@eaDir"):
            (self.contenu / r).mkdir(parents=True)
        photo = gazette.EXEMPLES / "photos" / "Noël 2024" / "sapin.jpg"
        for i in range(30):
            for r in ("histoires", "traditions", "recettes"):
                (self.contenu / r / "{}.txt".format(i)).write_text("Titre {} {}\nCorps {}".format(r, i, i), encoding="utf-8")
            (self.contenu / "enigmes" / "{}.txt".format(i)).write_text(
                "Énigme {}\nQuestion {} ?\nRéponse : solution-{}".format(i, i, i), encoding="utf-8")
            shutil.copy(photo, self.contenu / "photos" / "2019" / "p{}.jpg".format(i))
        shutil.copy(photo, self.contenu / "photos" / "@eaDir" / "vignette.jpg")
        (self.contenu / "phrases" / "toutes.txt").write_text(
            "\n".join("Phrase numéro {}".format(i) for i in range(30)), encoding="utf-8")

        self.config = self.tmp / "config.ini"
        self.config.write_text(
            "[gazette]\ndossier_contenu = {}\nheure_envoi = 08:00\n"
            "[mail]\nexpediteur = g@example.com\nmot_de_passe = abcd efgh\n"
            "destinataires = a@example.com, b@example.com\n".format(self.contenu), encoding="utf-8")
        donnees = self.tmp / "donnees"
        self.patches = [
            mock.patch.object(gazette, "DONNEES", donnees),
            mock.patch.object(gazette, "FICHIER_ETAT", donnees / "etat.json"),
            mock.patch.object(gazette, "FICHIER_JOURNAL", donnees / "journal.txt"),
            mock.patch("smtplib.SMTP_SSL", FauxSMTP),
        ]
        for p in self.patches:
            p.start()
        FauxSMTP.envoyes = []
        self.cfg = gazette.Config(self.config)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def envoyer(self, jour, heure=8):
        return gazette.cmd_envoyer(self.cfg, dt.datetime(2026, 12, jour, heure, 5))

    def test_campagne_complete_sans_reutilisation(self):
        for j in range(1, 25):
            self.assertEqual(self.envoyer(j), 0)
        self.assertEqual(len(FauxSMTP.envoyes), 24)
        etat = gazette.charger_etat()
        self.assertEqual(len(etat["utilises"]), len(set(etat["utilises"])), "un élément a été réutilisé")
        self.assertEqual(len(etat["utilises"]), 24 * 6)
        self.assertFalse(any("@eaDir" in c for c in etat["utilises"]))
        self.assertTrue((gazette.DONNEES / "archives" / "2026" / "24.html").exists())

    def test_reponse_le_lendemain(self):
        self.envoyer(1)
        reponse = gazette.charger_etat()["reponse_en_attente"]["reponse"]
        self.envoyer(2)
        html = FauxSMTP.envoyes[1].get_body(("html",)).get_content()
        self.assertIn(reponse, html)
        self.assertIn("solution d&#x27;hier", html.lower().replace("'", "&#x27;"))
        premier = FauxSMTP.envoyes[0].get_body(("html",)).get_content()
        self.assertNotIn(reponse, premier, "la réponse ne doit pas être dans la Gazette du jour")

    def test_pas_de_doublon_dans_la_journee(self):
        self.envoyer(3)
        self.envoyer(3, heure=9)
        self.envoyer(3, heure=10)
        self.assertEqual(len(FauxSMTP.envoyes), 1)

    def test_pas_avant_l_heure_ni_hors_saison(self):
        self.envoyer(4, heure=7)
        gazette.cmd_envoyer(self.cfg, dt.datetime(2026, 12, 25, 9))
        gazette.cmd_envoyer(self.cfg, dt.datetime(2026, 11, 30, 9))
        self.assertEqual(FauxSMTP.envoyes, [])

    def test_echec_d_envoi_ne_consomme_rien(self):
        with mock.patch.object(FauxSMTP, "send_message", side_effect=OSError("réseau")), \
                mock.patch("time.sleep"):
            with self.assertRaises(RuntimeError):
                self.envoyer(5)
        self.assertEqual(gazette.charger_etat()["utilises"], [])

    def test_photo_integree(self):
        self.envoyer(6)
        msg = FauxSMTP.envoyes[0]
        images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
        self.assertEqual(len(images), 1)
        cid = images[0]["Content-ID"].strip("<>")
        self.assertIn("cid:" + cid, msg.get_body(("html",)).get_content())
        # Le message doit pouvoir être relu par un client mail
        email.message_from_bytes(msg.as_bytes())

    def test_rubrique_vide_ignoree(self):
        shutil.rmtree(self.contenu / "enigmes")
        self.assertEqual(self.envoyer(7), 0)
        html = FauxSMTP.envoyes[0].get_body(("html",)).get_content()
        self.assertNotIn("petite énigme", html)


class TestDossiersPhotos(unittest.TestCase):
    def test_plusieurs_dossiers(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            photo = gazette.EXEMPLES / "photos" / "Noël 2024" / "sapin.jpg"
            for rel in ("photo/Noël/a.jpg", "photo/Noël/2018/b.JPG", "photo/Vacances/c.png",
                        "photo/Vacances/@eaDir/c.png/SYNOPHOTO_THUMB_XL.jpg", "photo/Prive/d.jpg"):
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(photo, d / rel)
            (d / "photo/Vacances/video.mp4").write_bytes(b"x")
            c = d / "config.ini"
            c.write_text("[gazette]\ndossiers_photos =\n   {0}/photo/Noël\n#   {0}/photo/Prive\n"
                         "   {0}/photo/Vacances\n   {0}/photo/Noël/2018\n[mail]\n".format(d.as_posix()),
                         encoding="utf-8")
            cfg = gazette.Config(c)
            self.assertEqual(len(cfg.dossiers_photos), 3)
            cands = gazette.candidats_photos(d / "inutile", cfg.dossiers_photos)
            noms = sorted(x.chemin.name for x in cands)
            self.assertEqual(noms, ["a.jpg", "b.JPG", "c.png"])  # ni Prive, ni @eaDir, ni vidéo, ni doublon
            legendes = {x.chemin.name: x.legende for x in cands}
            self.assertEqual(legendes["b.JPG"], "Noël · 2018")
            self.assertEqual(legendes["c.png"], "Vacances")


class TestBrevo(unittest.TestCase):
    def test_starttls_et_identifiant(self):
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / "config.ini"
            c.write_text("[gazette]\n[mail]\nexpediteur = moi@gmail.com\nidentifiant = 12@smtp-brevo.com\n"
                         "mot_de_passe = cle\ndestinataires = a@b.c\nserveur = smtp-relay.brevo.com\nport = 587\n",
                         encoding="utf-8")
            cfg = gazette.Config(c)
            with mock.patch("smtplib.SMTP") as smtp:
                gazette.connexion_smtp(cfg)
            smtp.assert_called_once_with("smtp-relay.brevo.com", 587, timeout=60)
            smtp.return_value.starttls.assert_called_once()
            smtp.return_value.login.assert_called_once_with("12@smtp-brevo.com", "cle")


class TestLecture(unittest.TestCase):
    def test_analyse(self):
        self.assertEqual(gazette.analyser_fichier_texte("Titre\n\nLe corps"), ("Titre", "Le corps", None))
        self.assertEqual(gazette.analyser_fichier_texte("Une seule ligne"), (None, "Une seule ligne", None))
        t, c, r = gazette.analyser_fichier_texte("Q\nQui suis-je ?\nreponse: Moi\nEt toi", avec_reponse=True)
        self.assertEqual((t, c, r), ("Q", "Qui suis-je ?", "Moi\nEt toi"))

    def test_encodage_windows(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.txt"
            f.write_bytes("Bûche de Noël\nCrème".encode("cp1252"))
            self.assertEqual(gazette.lire_texte(f), "Bûche de Noël\nCrème")

    def test_html(self):
        h = gazette.texte_vers_html("Intro <b>\n\n- un\n- deux\n\n1. a\n2. b\n\nVoir https://ex.com/page.")
        self.assertIn("&lt;b&gt;", h)
        self.assertIn("<ul", h)
        self.assertIn("<ol", h)
        self.assertIn('href="https://ex.com/page"', h)

    def test_liste_sous_un_titre(self):
        h = gazette.texte_vers_html("Ingrédients :\n- 1 œuf\n- du sucre\n\n1. Battre\n2. Cuire")
        self.assertIn("<p", h)
        self.assertEqual(h.count("<li"), 4)
        self.assertIn("<ul", h)
        self.assertIn("<ol", h)
        self.assertNotIn("- 1 œuf", h)


if __name__ == "__main__":
    unittest.main()
