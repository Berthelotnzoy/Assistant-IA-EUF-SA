import os
# Désactive la redirection proxy pour les adresses locales
os.environ['NO_PROXY'] = '127.0.0.1,localhost'
os.environ['no_proxy'] = '127.0.0.1,localhost'

import unicodedata
import requests
from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, FollowupAction

# ===========================================================================
# CONFIGURATION GLPI
# ===========================================================================
GLPI_URL = "http://127.0.0.1/glpi/apirest.php"
GLPI_APP_TOKEN = "5eGbh8D5lggoCPj2bDrp1n1FTlUqI3emJHQa1W4N"
GLPI_USER_TOKEN = "6d4I3RGe0lupxjFjbAlPCD4fvweYDZGE7EujDC8q"

CATEGORY_MAPPING = {
    "impression": 2,
    "logiciel": 3,
    "reseau": 5,
    "materiel": 8,
    "defaut": 1  # ID de catégorie par défaut dans GLPI
}

CATEGORY_LABELS = {
    "reseau": "PROBLEME DE RÉSEAU",
    "impression": "PROBLEME D'IMPRESSION",
    "materiel": "PROBLEME MATÉRIEL",
    "logiciel": "PROBLEME LOGICIEL",
    "defaut": "SUPPORT GÉNÉRAL"
}

# ===========================================================================
# BASE DE CONNAISSANCES LOCALE
# ===========================================================================
SOLUTIONS_DATABASE = {
    "reseau": {
        "sol1": "Vérifiez que votre câble Ethernet est bien branché ou redémarrez votre module Wi-Fi.",
        "sol2": "Ouvrez l'invite de commande et tapez 'ipconfig /renew' pour réinitialiser votre adresse IP."
    },
    "impression": {
        "sol1": "Vérifiez que l'imprimante est allumée et qu'il n'y a pas de bourrage papier dans le bac.",
        "sol2": "Redémarrez le service 'Spouleur d'impression' sur votre ordinateur."
    },
    "materiel": {
        "sol1": "Vérifiez les branchements électriques et les câbles de connexion de votre équipement.",
        "sol2": "Redémarrez complètement l'appareil concerné."
    },
    "logiciel": {
        "sol1": "Fermez l'application via le Gestionnaire des tâches et relancez-la.",
        "sol2": "Redémarrez votre ordinateur et vérifiez si une mise à jour est en attente."
    },
    "defaut": {
        "sol1": "Redémarrez votre équipement/application et réessayez la manip.",
        "sol2": "Videz le cache de votre navigateur ou redémarrez complètement votre ordinateur."
    }
}


def supprimer_accents(texte: str) -> str:
    texte_normalise = unicodedata.normalize('NFD', texte)
    return "".join(c for c in texte_normalise if unicodedata.category(c) != 'Mn')


def categoriser_probleme(texte: str) -> str:
    texte_clean = supprimer_accents(texte.lower())
    
    if any(m in texte_clean for m in ["wifi", "internet", "reseau", "connexion"]):
        return "reseau"
    elif any(m in texte_clean for m in ["imprimer", "imprimante", "papier", "edition", "impression"]):
        return "impression"
    elif any(m in texte_clean for m in ["pc", "ecran", "souris", "clavier", "materiel", "ordinateur"]):
        return "materiel"
    elif any(m in texte_clean for m in ["logiciel", "application", "excel", "word", "bug", "app"]):
        return "logiciel"
        
    return "defaut"


# ===========================================================================
# ACTION : ACCUEIL ET RÉCUPÉRATION DU NOM
# ===========================================================================
class ActionGreetUser(Action):
    def name(self) -> Text:
        return "action_greet_user"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        user_name = tracker.get_slot("user_name")

        if not user_name:
            headers_init = {
                "App-Token": GLPI_APP_TOKEN,
                "Authorization": f"user_token {GLPI_USER_TOKEN}",
                "Content-Type": "application/json"
            }
            try:
                with requests.Session() as s:
                    s.trust_env = False  # Ignore les variables d'environnement de proxy du système
                    s.proxies = {"http": None, "https": None}
                    res_sess = s.get(f"{GLPI_URL}/initSession", headers=headers_init, timeout=20)
                    
                    res_sess = s.get(f"{GLPI_URL}/initSession", headers=headers_init, timeout=20)
                    if res_sess.status_code == 200:
                        session_token = res_sess.json().get("session_token")
                        headers_act = {
                            "App-Token": GLPI_APP_TOKEN,
                            "Session-Token": session_token,
                            "Content-Type": "application/json"
                        }
                        
                        res_user = s.get(f"{GLPI_URL}/getFullSession", headers=headers_act, timeout=20)
                        if res_user.status_code == 200:
                            sess_data = res_user.json().get("session", {})
                            user_name = sess_data.get("glpifirstname") or sess_data.get("glpiname")
                        
                        s.get(f"{GLPI_URL}/killSession", headers=headers_act, timeout=5)
            except Exception as e:
                print(f"[ERREUR RECUPERATION NOM USER] : {e}")
                user_name = None

        if user_name and user_name != "utilisateur":
            greeting_msg = f"Bonjour Mr./Mme {user_name}, je suis l'agent support d'Express Union Finance SA. En quoi puis-je vous aider aujourd'hui ?"
        else:
            greeting_msg = "Bonjour ! Je suis l'agent support d'Express Union Finance SA. En quoi puis-je vous aider aujourd'hui ?"

        dispatcher.utter_message(text=greeting_msg)
        return [SlotSet("user_name", user_name)]


# ===========================================================================
# ACTION 1 : GESTION DES ÉTAPES DE RÉSOLUTION
# ===========================================================================
class ActionHandleProblemStep(Action):
    def name(self) -> Text:
        return "action_handle_problem_step"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        step = tracker.get_slot("solution_step") or 0
        tried_solutions = tracker.get_slot("tried_solutions") or []
        
        last_user_msg = tracker.latest_message.get('text', '')
        saved_description = tracker.get_slot("user_problem_description")
        description = saved_description if saved_description else last_user_msg

        cat_key = categoriser_probleme(description)

        # 1. CAS HORS SUJET / ABSURDE (ex: "je veux de l'argent")
        if cat_key == "defaut":
            dispatcher.utter_message(
                text="Désolé, mais je ne suis pas en mesure de vous aider concernant ce sujet. "
                     "Mes compétences sont limitées à l'assistance informatique et au support technique."
            )
            return [
                SlotSet("user_problem_description", None),
                SlotSet("solution_step", None),
                SlotSet("tried_solutions", None)
            ]

        # 2. CAS CRÉATION DIRECTE DE TICKET
        # Parcourt les événements récents pour voir si utter_ask_ticket_description a été exécuté juste avant
        is_direct_ticket_request = False
        for event in reversed(tracker.events):
            if event.get("event") == "action" and event.get("name") == "utter_ask_ticket_description":
                is_direct_ticket_request = True
                break
            elif event.get("event") == "bot" and event.get("metadata", {}).get("utter_action") == "utter_ask_ticket_description":
                is_direct_ticket_request = True
                break
            # Si on remonte jusqu'à une autre action utilisateur importante, on arrête la recherche
            elif event.get("event") == "action" and event.get("name") in ["action_handle_problem_step", "action_greet_user"]:
                break

        if is_direct_ticket_request:
            dispatcher.utter_message(text="Très bien, je crée directement votre ticket d'assistance.")
            return [
                SlotSet("user_problem_description", description),
                FollowupAction("action_create_glpi_ticket")
            ]
        
        # 3. CAS DÉPANNAGE ÉTAPE PAR ÉTAPE (Flux normal)
        if step == 0:
            solution = SOLUTIONS_DATABASE.get(cat_key, {}).get("sol1", "Vérifiez les branchements.")
            tried_solutions.append(solution)

            dispatcher.utter_message(text=f"Voici une première vérification à effectuer :\n👉 {solution}")
            dispatcher.utter_message(text="Est-ce que cette solution a résolu votre problème ?")
            
            return [
                SlotSet("user_problem_description", description),
                SlotSet("solution_step", 1),
                SlotSet("tried_solutions", tried_solutions)
            ]

        elif step == 1:
            solution = SOLUTIONS_DATABASE.get(cat_key, {}).get("sol2", "Redémarrez le système.")
            tried_solutions.append(solution)

            dispatcher.utter_message(text=f"D'accord. Tentons une deuxième solution :\n👉 {solution}")
            dispatcher.utter_message(text="Est-ce que cette solution a résolu votre problème ?")

            return [
                SlotSet("solution_step", 2),
                SlotSet("tried_solutions", tried_solutions)
            ]

        else:
            return [FollowupAction("action_create_glpi_ticket")]


# ===========================================================================
# ACTION 2 : RÉINITIALISATION DU CONTEXTE
# ===========================================================================
class ActionResetSlots(Action):
    def name(self) -> Text:
        return "action_reset_slots"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        return [
            SlotSet("solution_step", 0),
            SlotSet("tried_solutions", []),
            SlotSet("user_problem_description", None)
        ]


# ===========================================================================
# ACTION 3 : CRÉATION DU TICKET GLPI
# ===========================================================================
class ActionCreateGlpiTicket(Action):
    def name(self) -> Text:
        return "action_create_glpi_ticket"

    def reformuler_description(self, problem_desc: str, tried_solutions: List[str]) -> str:
        desc_clean = problem_desc.strip()
        phrases_a_retirer = ["que faire ?", "que faire", "aidez moi", "aidez-moi"]
        for phrase in phrases_a_retirer:
            if desc_clean.lower().endswith(phrase):
                desc_clean = desc_clean[:-len(phrase)].strip()

        contenu = (
            "========================================\n"
            "DEMANDE AUTOMATISÉE DE TICKETING ASSISTANT\n"
            "========================================\n\n"
            f"Description du problème :\n{desc_clean}\n\n"
        )

        if tried_solutions:
            contenu += "Solutions proposées par l'Assistant AI (testées sans succès) :\n"
            for sol in tried_solutions:
                sol_clean = sol.strip().lstrip("👉").strip()
                contenu += f"- {sol_clean}\n"
        else:
            contenu += "Aucune solution de niveau 1 n'a pu être appliquée (Demande directe de ticket).\n"

        contenu += (
            "\n----------------------------------------\n"
            "Ce ticket a été généré automatiquement par l'Assistant AI."
        )

        return contenu

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:

        problem_desc = tracker.get_slot("user_problem_description") or "Problème non spécifié"
        tried_solutions = tracker.get_slot("tried_solutions") or []
        cat_key = categoriser_probleme(problem_desc)

        label_categorie = CATEGORY_LABELS.get(cat_key, "SUPPORT GÉNÉRAL")
        resume_desc = problem_desc[:50] + ("..." if len(problem_desc) > 50 else "")
        titre_ticket = f"[Assistant AI] [{label_categorie}] : {resume_desc}"

        corps_ticket = self.reformuler_description(problem_desc, tried_solutions)
        glpi_category_id = CATEGORY_MAPPING.get(cat_key, 1)
        ticket_id = None

        headers_init = {
            "App-Token": GLPI_APP_TOKEN,
            "Authorization": f"user_token {GLPI_USER_TOKEN}",
            "Content-Type": "application/json"
        }

        try:
            with requests.Session() as s:
                s.proxies = {"http": None, "https": None}
                res_session = s.get(f"{GLPI_URL}/initSession", headers=headers_init, timeout=20)
                
                if res_session.status_code == 200:
                    session_token = res_session.json().get("session_token")
                    headers_action = {
                        "App-Token": GLPI_APP_TOKEN,
                        "Session-Token": session_token,
                        "Content-Type": "application/json"
                    }

                    user_id = None
                    try:
                        res_user = s.get(f"{GLPI_URL}/getFullSession", headers=headers_action, timeout=20)
                        if res_user.status_code == 200:
                            sess_info = res_user.json().get("session", {})
                            user_id = sess_info.get("glpiID") or sess_info.get("glpi_id")
                    except Exception:
                        pass

                    payload_input = {
                        "name": titre_ticket,
                        "content": corps_ticket,
                        "urgency": 3,
                        "type": 1,
                        "itilcategories_id": glpi_category_id
                    }

                    if user_id:
                        payload_input["_users_id_requester"] = user_id

                    payload = {"input": payload_input}

                    res_ticket = s.post(f"{GLPI_URL}/Ticket", json=payload, headers=headers_action, timeout=20)
                    
                    if res_ticket.status_code in [200, 201]:
                        res_json = res_ticket.json()
                        ticket_id = res_json.get("id") if isinstance(res_json, dict) else res_json[0].get("id")

                    s.get(f"{GLPI_URL}/killSession", headers=headers_action, timeout=5)

        except Exception as e:
            print(f"[ERREUR EXCEPTION GLPI] : {e}")

        if ticket_id:
            if tried_solutions:
                msg_intro = "Les solutions proposées n'ont pas permis de résoudre le problème."
            else:
                msg_intro = "Votre demande de ticket a bien été enregistrée."

            dispatcher.utter_message(
                text=f"{msg_intro}\n\n"
                     f"Un ticket d'assistance a été créé sur GLPI sous le N° **{ticket_id}**.\n"
                     f"Un technicien prendra en charge votre demande."
            )
        else:
            dispatcher.utter_message(
                text="Une erreur est survenue lors de la création directe dans GLPI, "
                     "mais votre demande a été transmise au support."
            )

        return [
            SlotSet("solution_step", 0),
            SlotSet("tried_solutions", []),
            SlotSet("user_problem_description", None)
        ]