# 🎄 La Gazette de Noël

Chaque matin du 1er au 24 décembre, un joli mail arrive avec :

- 📸 **le souvenir du jour** : une photo de famille ;
- 🧩 **la petite énigme** (facultative), avec sa réponse dans la Gazette du lendemain ;
- ✨ **l'histoire du jour** ;
- 🌍 **Noël autour du monde** ;
- 🍪 **la pause gourmande** ;
- 💌 **le petit mot du jour**.

Chaque élément est pioché **au hasard** dans des dossiers que vous remplissez en vrac. Un élément déjà utilisé ne revient **jamais**. Il n'y a ni aperçu ni journal du contenu : la surprise reste entière.

Le programme n'a besoin que de Python 3.8 ou plus, sans rien installer d'autre. Il tourne donc tel quel sur un Synology (DSM 7).

---

## 1. Remplir les dossiers

```
Gazette/                  ← un dossier partagé sur le Synology
├── programme/            ← ce dépôt (gazette.py, modele/, …)
├── photos/               ← vos photos, sous-dossiers acceptés
├── histoires/            ← un fichier .txt par histoire
├── traditions/           ← un fichier .txt par tradition
├── recettes/             ← un fichier .txt par recette
├── phrases/              ← une phrase par ligne (un ou plusieurs .txt)
└── enigmes/              ← facultatif : vide, la rubrique disparaît
```

Prévoyez **au moins 24 éléments par rubrique**. S'il en manque un jour, la rubrique est simplement sautée ce jour-là.

### Les fichiers texte

Le nom du fichier n'a pas d'importance. **La première ligne sert de titre**, le reste est le texte. Vous pouvez faire un copier-coller brut depuis un site :

- une ligne vide crée un nouveau paragraphe ;
- les lignes qui commencent par `-` ou `1.` deviennent des listes (pratique pour les ingrédients) ;
- les liens `https://…` deviennent cliquables.

```
Le chat qui a sauvé Noël
En décembre 2019, une famille…

Source : https://…
```

**Les énigmes** : ajoutez une ligne `Réponse :`. Tout ce qui suit sera publié **le lendemain**.

```
Devinette du sapin
Je suis vert toute l'année et je brille en décembre. Qui suis-je ?
Réponse : Le sapin de Noël !
```

**Les phrases** : un seul fichier avec une phrase par ligne suffit.

### Les photos

Formats acceptés : JPG, PNG et GIF. Les fichiers de plus de 15 Mo sont ignorés. Le **nom du sous-dossier** s'affiche sous la photo, par exemple « 📁 Vacances 2019 ». Pour le masquer, mettez `afficher_dossier_photo = non` dans la configuration. Les dossiers techniques du Synology (`@eaDir`, `#recycle`) sont ignorés.

Des exemples de chaque format se trouvent dans [`exemples/`](exemples/).

---

## 2. Choisir le service d'envoi

### Option A : Brevo (recommandé, comme pour le Lego Tracker)

1. Sur https://app.brevo.com, menu en haut à droite → **Expéditeurs, domaines et IP dédiées** : vérifiez que l'adresse d'expédition y est validée.
2. Menu → **SMTP & API** → onglet **SMTP** : notez le **serveur** (`smtp-relay.brevo.com`), le **port** (587) et l'**identifiant** (du type `123abc@smtp-brevo.com`).
3. Cliquez sur **Générer une nouvelle clé SMTP**. C'est elle qui va dans `mot_de_passe`. Attention : la clé API du Lego Tracker ne fonctionne pas ici.

Le plan gratuit permet 300 mails par jour, très largement suffisant.

### Option B : Gmail

1. Créez un compte Gmail, par exemple `gazette.de.noel.xxx@gmail.com`.
2. Activez la **validation en deux étapes** : Compte Google → Sécurité.
3. Créez un **mot de passe d'application** à l'adresse https://myaccount.google.com/apppasswords (nom : « Gazette »). Google affiche 16 lettres : c'est ce mot de passe qui va dans la configuration, pas celui du compte.

Une précision : les Gazettes envoyées apparaîtront dans le dossier « Envoyés » de ce compte, à 8 h, en même temps que chez vous. N'allez simplement pas y jeter un œil avant 😉.

---

## Essai sur un PC Windows

1. Installez Python depuis https://www.python.org/downloads/ et **cochez « Add python.exe to PATH »** pendant l'installation.
2. Téléchargez ce dépôt depuis GitHub (bouton **Code → Download ZIP**) et décompressez-le, par exemple dans `Documents\gazette_noel`.
3. Dans ce dossier, copiez `config.exemple.ini` en `config.ini` et ouvrez-le avec le Bloc-notes. Mettez `dossier_contenu = exemples` et remplissez la partie `[mail]`.
4. Ouvrez un terminal dans le dossier : clic droit dans l'explorateur → **Ouvrir dans le Terminal**. Lancez ensuite :
   ```
   py gazette.py apercu
   py gazette.py verifier
   py gazette.py test-mail
   ```

Pour essayer avec **vos propres dossiers** sans rien gâcher, créez un petit dossier d'essai avec deux ou trois fichiers bidons, puis lancez `py gazette.py test-mail --contenu C:\chemin\vers\essai`.

---

## 3. Installer sur le Synology

1. **Créer le dossier partagé** : Panneau de configuration → Dossier partagé → Créer → `Gazette`.
2. **Copier le programme** : avec File Station, créez `Gazette/programme` et déposez-y le contenu de ce dépôt.
3. **Configurer** : dans `programme`, copiez `config.exemple.ini` en `config.ini` et remplissez-le (adresse Gmail, mot de passe d'application, vos deux adresses). Le chemin `dossier_contenu = /volume1/Gazette` est normalement le bon.
4. **Tester** : Panneau de configuration → Planificateur de tâches → Créer → Tâche planifiée → Script défini par l'utilisateur.
   - Général : nom « Gazette – test », utilisateur : **root**.
   - Planification : une seule exécution, date d'aujourd'hui.
   - Paramètres de tâche : cochez « Envoyer les détails d'exécution par e-mail » si vous le souhaitez, puis, dans « Script » :
     ```
     cd /volume1/Gazette/programme && python3 gazette.py verifier && python3 gazette.py test-mail
     ```
   - Faites un clic droit sur la tâche → **Exécuter**. Vous devez recevoir un mail « [TEST] » qui utilise **uniquement les contenus d'exemple**, sans rien gâcher. Le résultat de la vérification (nombre d'éléments par rubrique, connexion Gmail) est visible avec le bouton « Action » → « Afficher le résultat ».
5. **Programmer l'envoi quotidien** : créez une seconde tâche « Gazette de Noël ».
   - Utilisateur : root.
   - Planification : quotidienne, **première exécution 08:00**, répéter **toutes les heures**, dernière exécution **12:00**.
   - Script :
     ```
     cd /volume1/Gazette/programme && python3 gazette.py envoyer
     ```

   Le programme n'envoie qu'**une seule fois par jour**. Les exécutions de 9 h à 12 h ne servent qu'à rattraper une coupure du NAS ou d'Internet à 8 h. En dehors du 1er au 24 décembre, il ne fait rien : la tâche peut rester active toute l'année.

Si la commande `python3` est introuvable, installez « Python 3.9 » depuis le Centre de paquets et remplacez `python3` par `python3.9`.

---

## Commandes

| Commande | Rôle |
|---|---|
| `python3 gazette.py envoyer` | Envoie la Gazette du jour si elle n'est pas déjà partie. |
| `python3 gazette.py verifier` | Vérifie la configuration et la connexion Gmail, et **compte** les éléments disponibles par rubrique, sans les afficher. |
| `python3 gazette.py apercu` | Crée `apercu.html` avec les **exemples**, pour voir la mise en page. |
| `python3 gazette.py test-mail` | Envoie un mail de test avec les **exemples**. |

Option : `--date 2026-12-24` pour simuler un autre jour avec `apercu` ou `test-mail`.

## Fichiers générés (`programme/donnees/`)

- `etat.json` : les éléments déjà utilisés et les éditions envoyées. Supprimez-le pour tout remettre à zéro, par exemple l'année prochaine si vous voulez réutiliser les anciens contenus.
- `journal.txt` : un journal **technique** uniquement (« Gazette n°5 envoyée »).
- `archives/2026/05.html` : une copie de chaque Gazette, créée **au moment de l'envoi**, pour garder une trace de l'édition 2026.

## Personnaliser

- Couleurs et rubriques : en haut de `gazette.py` (`COULEURS`, `RUBRIQUES`).
- Mise en page : `modele/gazette.html`.
- Tests : `python3 -m unittest discover -s tests`.
