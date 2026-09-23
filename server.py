"""Small local server for Pallimustus party adventure."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
import json, random, re, threading, time

ROOT = Path(__file__).parent.resolve()
ESSENCES = {}
raw = (ROOT / "content" / "dnd.txt").read_text(encoding="utf-8")
section = "Common"
for line in raw.splitlines():
    line = line.strip()
    if line in {"Common", "Uncommon", "Rare", "Epic", "Legendary"}:
        section = line
    elif line and "Essences and Types" not in line:
        for term in re.findall(r"[A-Za-z]+", line):
            ESSENCES[term.title()] = section

CAMPAIGN_ARCS = [
    {"settlement": "Copperwake", "signal": "its ward-lanterns are blinking in a pattern no guild engineer recognizes", "scheme": "charting the city's essence conduits", "companion": "Guild factor Sella Venn"},
    {"settlement": "Morrowfen", "signal": "merchant caravans report that their maps redraw themselves overnight", "scheme": "turning the trade roads into a ritual circuit", "companion": "field researcher Pell Ardin"},
    {"settlement": "Highmere Reach", "signal": "a temple bell rings beneath the earth with no one touching it", "scheme": "binding a stolen Awakening Stone beneath the old aqueduct", "companion": "wayfinder Tamsin Rook"},
    {"settlement": "Vellum Cross", "signal": "the local essence vault has begun answering questions in a stranger's voice", "scheme": "seeding false orders through the adventurer guild network", "companion": "quartermaster Oren Vale"},
]

LOCK = threading.RLock()
ROOMS = {}
CLOSED_ROOMS = set()
MAX_AGE = 60 * 60 * 5
RECOVERY_FILE = ROOT / "work" / "room-recovery.json"

def restore_room_snapshot():
    """Restore a one-time local room snapshot after a code update restart."""
    if not RECOVERY_FILE.exists():
        return
    try:
        snapshot = json.loads(RECOVERY_FILE.read_text(encoding="utf-8"))
        if isinstance(snapshot, dict) and snapshot.get("code"):
            snapshot["updated"] = time.time()
            ROOMS[snapshot["code"]] = snapshot
    finally:
        RECOVERY_FILE.unlink(missing_ok=True)

restore_room_snapshot()

def clean_rooms():
    now = time.time()
    for code in list(ROOMS):
        if now - ROOMS[code]["updated"] > MAX_AGE:
            del ROOMS[code]

def public(room):
    return {k: v for k, v in room.items() if k != "updated"}

def record_choice(room, key, amount=1):
    choices = room.setdefault("campaignChoices", {})
    choices[key] = choices.get(key, 0) + amount

def record_support(room, player):
    record_choice(room, "supportActions")
    choices = room.setdefault("campaignChoices", {})
    names = choices.setdefault("supporters", [])
    if player["name"] not in names:
        names.append(player["name"])

def prepare_story_decision(room):
    option_pool = [
        {"id": "scout", "title": "Study the breach", "desc": "Scout the enemy and reduce armor by 2 in the next boss battle."},
        {"id": "supplies", "title": "Gather guild supplies", "desc": "Ask the local guild for help and add 25 gold to the shared treasury."},
        {"id": "rest", "title": "Take a dangerous rest", "desc": "Everyone restores 20 HP and 20 mana. Each rest permanently strengthens every remaining boss by 10% HP, +1 attack, and +1 armor (stacks up to 3 times)."},
        {"id": "meditate", "title": "Attune to your essences", "desc": "The whole party restores 15 mana before the next encounter."},
        {"id": "ward", "title": "Raise a ward together", "desc": "Each hero gains a 5-point guard against the next boss's attacks."},
    ]
    room["storyDecision"] = {
        "question": random.choice(["How should the party prepare for the next breach?", "The guild offers several ways to help. Which path will you choose?", "Before moving on, decide what your party needs most."]),
        "options": random.sample(option_pool, 3),
        "votes": {}, "resolved": False, "winner": None, "result": "",
    }

def resolve_story_decision(room):
    decision = room.get("storyDecision")
    if not decision or decision.get("resolved") or len(decision.get("votes", {})) < len(room["players"]):
        return
    totals = {}
    for choice in decision["votes"].values():
        totals[choice] = totals.get(choice, 0) + 1
    highest = max(totals.values())
    winner = random.choice([choice for choice, count in totals.items() if count == highest])
    decision["winner"] = winner
    decision["resolved"] = True
    if winner == "scout":
        room["nextBossArmorReduction"] = room.get("nextBossArmorReduction", 0) + 2
        decision["result"] = "The party chose to study the breach. Every enemy in the next boss battle will have 2 less armor."
    elif winner == "supplies":
        room["sharedGold"] += 25
        decision["result"] = "The party gathered supplies from the local guild. The shared treasury gains 25 gold."
    elif winner == "rest":
        for player in room["players"]:
            player["hp"] = min(player["maxHp"], player["hp"] + 20)
            player["mana"] = min(100, player["mana"] + 20)
        room["restDifficulty"] = min(3, room.get("restDifficulty", 0) + 1)
        stacks = room["restDifficulty"]
        decision["result"] = f"The party rested and everyone restored up to 20 HP and 20 mana. The remaining bosses are now stronger: +{stacks * 10}% HP, +{stacks} attack, and +{stacks} armor. This danger stacks up to three times."
    elif winner == "meditate":
        for player in room["players"]:
            player["mana"] = min(100, player["mana"] + 15)
        decision["result"] = "The party attuned to its essences. Everyone restores up to 15 mana."
    elif winner == "ward":
        room["nextBossGuard"] = room.get("nextBossGuard", 0) + 5
        decision["result"] = "The party raised a shared ward. Each hero will absorb 5 damage from the next boss's attacks."
    choices = room.setdefault("campaignChoices", {})
    choices.setdefault("preparationDecisions", []).append(winner)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/essences":
            return self.send_json(200, ESSENCES)
        if path == "/api/room":
            q = __import__("urllib.parse").parse.parse_qs(urlparse(self.path).query)
            code = q.get("code", [""])[0].upper()
            with LOCK:
                clean_rooms()
                room = ROOMS.get(code)
                if not room:
                    if code in CLOSED_ROOMS:
                        return self.send_json(410, {"error": "The host left. This adventure has ended."})
                    return self.send_json(404, {"error": "Room not found. Check the code or create a new room."})
                room["updated"] = time.time()
                return self.send_json(200, public(room))
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        try: data = self.body()
        except Exception: return self.send_json(400, {"error": "Invalid request."})
        with LOCK:
            clean_rooms()
            if path == "/api/create":
                code = "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=5))
                host_id = str(random.getrandbits(64))
                campaign = random.choice(CAMPAIGN_ARCS)
                room = {"code": code, "host": host_id, "phase": "lobby", "round": 0,
                    "scene": f"The guild hall of Vitesse hums with guild notices and warded lanterns. {campaign['companion']} studies an urgent report from {campaign['settlement']}. A silver-haired adventurer named Jason Asano leans over the map. ‘New team? Pallimustus has a way of turning introductions into stories.’",
                    "players": [], "enemies": [], "log": [], "loot": [], "votes": {}, "sharedGold": 0, "sideQuest": False, "returnToStory": False,
                    "sideQuestCount": 0, "storyNext": "boss", "campaign": campaign,
                    "campaignChoices": {"sideQuestsAccepted": 0, "sideQuestsWon": 0, "sideQuestsSkipped": 0, "goldSpent": 0, "supportActions": 0, "supporters": [], "damageActions": 0},
                    "shop": [], "updated": time.time(), "turn": 0, "bossIndex": 0}
                ROOMS[code] = room
                return self.send_json(200, {"code": code, "playerId": host_id})
            code = str(data.get("code", "")).upper()
            room = ROOMS.get(code)
            if not room: return self.send_json(404, {"error": "Room not found."})
            room["updated"] = time.time()
            action = path.rsplit("/", 1)[-1]
            pid = str(data.get("playerId", ""))
            player = next((p for p in room["players"] if p["id"] == pid), None)
            if action == "join":
                if room["phase"] != "lobby": return self.send_json(400, {"error": "This journey has already begun. Join a new room instead."})
                name = str(data.get("name", "Adventurer"))[:24].strip() or "Adventurer"
                requested_id = str(data.get("playerId", ""))
                existing = next((p for p in room["players"] if requested_id and p["id"] == requested_id), None)
                if not requested_id:
                    matches = [p for p in room["players"] if p["name"].casefold() == name.casefold()]
                    existing = matches[0] if len(matches) == 1 else None
                if existing:
                    return self.send_json(200, {"playerId": existing["id"], "room": public(room)})
                if len(room["players"]) >= 8: return self.send_json(400, {"error": "This room is full (8 players)."})
                pid = room["host"] if requested_id == room["host"] and not any(p["id"] == room["host"] for p in room["players"]) else str(random.getrandbits(64))
                room["players"].append({"id": pid, "name": name, "description": "", "essences": [], "skills": [], "pendingAwakenings": 0, "hp": 0, "maxHp": 0, "mana": 100, "maxMana": 100, "rank": "Iron", "rankProgress": 0, "gold": 0, "atk": 10, "def": 0, "guard": 0, "potions": {"healing": 0, "mana": 0}, "items": [], "ready": False, "alive": True})
                return self.send_json(200, {"playerId": pid, "room": public(room)})
            if not player: return self.send_json(403, {"error": "Player session not found. Rejoin the room."})
            if action == "leave":
                was_host = pid == room["host"]
                leaving_name = player["name"]
                leaving_index = room["players"].index(player)
                room["players"].pop(leaving_index)
                if was_host and room["players"]:
                    room["host"] = room["players"][0]["id"]
                    room["log"].insert(0, f"{leaving_name} left the adventure. {room['players'][0]['name']} now leads the guild.")
                elif was_host:
                    del ROOMS[code]
                    CLOSED_ROOMS.add(code)
                    return self.send_json(200, {"closed": True})
                if room.get("phase") == "story":
                    room.get("storyDecision", {}).get("votes", {}).pop(pid, None)
                    resolve_story_decision(room)
                if room["phase"] == "battle" and leaving_index < room["turn"]:
                    room["turn"] -= 1
                room["votes"] = {
                    item_id: {voter: recipient for voter, recipient in votes.items()
                              if voter != pid and recipient != pid}
                    for item_id, votes in room.get("votes", {}).items()
                }
                if room["phase"] == "loot":
                    for item in room.get("loot", []):
                        if not item.get("taken"):
                            settle_votes(room, item["id"])
                if room["phase"] == "battle" and room["turn"] >= len(room["players"]):
                    enemy_turn(room)
                    skip_incapacitated_turns(room, rescue=True)
                    check_result(room)
                return self.send_json(200, {"closed": False, "room": public(room)})
            if action == "character":
                desc = str(data.get("description", ""))[:1600]
                selected = [x for x in data.get("essences", []) if x in ESSENCES][:3]
                if len(selected) != 3: return self.send_json(400, {"error": "Choose exactly three essences."})
                previous_max = player["maxHp"] or 1
                hp_ratio = player["hp"] / previous_max if player["ready"] else 1
                awakened = [s for s in player["skills"] if s.get("awakened")]
                player["description"] = desc
                player["essences"] = selected
                sturdy={"armor","iron","shield","bone","bear","turtle","might","flesh","cattle","pangolin"}
                fragile={"glass","cloth","bird","bat","mouse","spider","swift","eye"}
                player["maxHp"] = max(70, min(150, 90 + len(desc) // 30 + sum(12 if x.lower() in sturdy else -7 if x.lower() in fragile else 0 for x in selected)))
                player["hp"] = min(player["maxHp"], max(1, round(player["maxHp"] * hp_ratio)))
                player["skills"] = make_skills(desc, selected) + awakened
                player["def"] = sum(s.get("defense", 0) for s in player["skills"] if s["type"] == "passive") + sum(3 if item == "Ward-forged armor" else 5 if item == "Astral Ward Sigil" else 0 for item in player["items"])
                player["ready"] = True
            elif action == "awaken":
                if room["phase"] != "loot": return self.send_json(400, {"error": "Awakening stones can be used after the reward vote."})
                if player.get("pendingAwakenings", 0) < 1: return self.send_json(400, {"error": "You do not have an unused awakening stone."})
                cost = int(data.get("cost", 0))
                choices = {1: "Passive", 2: "Low", 4: "Medium", 6: "High", 8: "Extreme", 10: "Insane"}
                if cost not in choices: return self.send_json(400, {"error": "Choose one of the six mana tiers."})
                skill = make_awakened_skill(player, cost, choices[cost])
                player["skills"].append(skill)
                player["pendingAwakenings"] -= 1
                player["items"].append("Awakening stone")
                room["log"].insert(0, f"{player['name']} awakened {skill['name']} ({skill['tier']}, {skill['cost']}% mana).")
            elif action == "potion":
                if room["phase"] not in {"story", "dispatch", "loot", "shop"}:
                    return self.send_json(400, {"error": "Potions can be used outside battle between encounters."})
                kind = str(data.get("kind", ""))
                if kind not in {"healing", "mana"}: return self.send_json(400, {"error": "Choose a healing or mana potion."})
                if player.get("potions", {}).get(kind, 0) <= 0: return self.send_json(400, {"error": f"You have no {kind} potions."})
                target = find_player(room, str(data.get("target", "")))
                if not target: return self.send_json(400, {"error": "Choose a party member to receive the potion."})
                key, cap = ("hp", target["maxHp"]) if kind == "healing" else ("mana", 100)
                if target[key] >= cap: return self.send_json(400, {"error": f"{target['name']} already has full {'HP' if kind == 'healing' else 'mana'}."})
                player["potions"][kind] -= 1
                die = random.randint(1, 20); amount = roll_value(die)
                restored = min(cap - target[key], amount)
                target[key] += restored
                record_support(room, player)
                label = "HP" if kind == "healing" else "mana"
                room["log"].insert(0, f"{player['name']} used a {kind} potion on {target['name']}, restoring {restored} {label} at full effect (d20: {die}).")
            elif action == "start":
                if pid != room["host"]: return self.send_json(403, {"error": "Only the host can begin."})
                if len(room["players"]) < 2: return self.send_json(400, {"error": "Invite at least one teammate before beginning."})
                if not all(p["ready"] for p in room["players"]): return self.send_json(400, {"error": "Everyone needs to finish character creation first."})
                arc = room.get("campaign", random.choice(CAMPAIGN_ARCS))
                room["phase"] = "story"; room["scene"] = f"A guild liaison unrolls a map: three sealed breaches threaten the trade route. Near {arc['settlement']}, {arc['signal']}. Jason Asano studies the map while {arc['companion']} traces a route between the wards. ‘Before we move, learn how your companions fight. Out there, a good plan matters as much as a strong essence.’"; room["round"] = 0; room["storyNext"] = "boss"
                prepare_story_decision(room)
            elif action == "advance":
                if pid != room["host"]: return self.send_json(403, {"error": "Only the host can advance the journey."})
                if room["phase"] == "story":
                    if not room.get("storyDecision", {}).get("resolved"):
                        return self.send_json(400, {"error": "Everyone must vote on the party's preparation before continuing."})
                    if room.get("storyNext") == "dispatch":
                        room["phase"] = "dispatch"
                        arc = room.get("campaign", random.choice(CAMPAIGN_ARCS))
                        room["scene"] = f"At the guild board, a fresh contract bears the seal of {arc['settlement']}. The Builder cults are {arc['scheme']}; their scouts have been seen near an abandoned waystation. {arc['companion']} explains that the settlement needs help before the next breach opens. Jason glances from the contract to the map. ‘If we take the detour, we can protect those people and strengthen the party. If we press on, the cult loses less time. Your call.’"
                    else: launch_boss(room)
                elif room["phase"] == "dispatch":
                    record_choice(room, "sideQuestsSkipped")
                    launch_boss(room)
                elif room["phase"] == "loot": open_store_or_next(room)
                elif room["phase"] == "shop":
                    room["phase"] = "story"; room["scene"] = story_for(room, room["bossIndex"])
                    room["storyNext"] = "dispatch" if room["bossIndex"] in (1, 2) and room.get("sideQuestCount", 0) < room["bossIndex"] else "boss"
                    prepare_story_decision(room)
                elif room["phase"] == "victory": room["phase"] = "ended"
            elif action == "side":
                if pid != room["host"]: return self.send_json(403, {"error": "Only the host can begin a side quest."})
                if room["phase"] != "dispatch": return self.send_json(400, {"error": "Side quests are available from the guild board between encounters."})
                launch_sidequest(room)
            elif action == "story_vote":
                if room["phase"] != "story": return self.send_json(400, {"error": "Party preparation votes are only available during story scenes."})
                decision = room.get("storyDecision")
                if not decision or decision.get("resolved"): return self.send_json(400, {"error": "The party has already finalized this choice."})
                choice = str(data.get("choice", ""))
                if choice not in {option["id"] for option in decision["options"]}:
                    return self.send_json(400, {"error": "Choose one of the available preparations."})
                decision["votes"][pid] = choice
                resolve_story_decision(room)
            elif action == "act":
                if room["phase"] != "battle": return self.send_json(400, {"error": "It is not a battle turn."})
                if room["turn"] >= len(room["players"]): return self.send_json(400, {"error": "Waiting for the enemy turn."})
                current = room["players"][room["turn"]]
                if current["id"] != pid: return self.send_json(400, {"error": "Wait for your turn."})
                kind = data.get("kind"); target_id = str(data.get("target", "")); skill_idx = int(data.get("skill", -1))
                incapacitated = current["hp"] <= 0 or current["mana"] <= 0
                if incapacitated and kind not in ("skip", "potion_hp", "potion_mana"):
                    return self.send_json(400, {"error": "You are out of HP or mana. An ally must restore you before you can act."})
                if kind == "skip":
                    if current["hp"] > 0 and current["mana"] > 0: return self.send_json(400, {"error": "A standing hero must choose an action."})
                    msg = f"{player['name']} cannot act and passes this turn."
                elif kind == "skill":
                    if skill_idx < 0 or skill_idx >= len(player["skills"]): return self.send_json(400, {"error": "Choose a skill."})
                    skill = player["skills"][skill_idx]
                    if player["mana"] < skill["cost"]: return self.send_json(400, {"error": "Not enough mana for that skill."})
                    skill_type = skill["type"]
                    if skill_type == "hybrid":
                        skill_type = str(data.get("intent", ""))
                        if skill_type not in {"damage", "heal"}: return self.send_json(400, {"error": "Choose whether this spell deals damage or heals."})
                    player["mana"] -= skill["cost"]
                    die = random.randint(1, 20); val = skill_value(die, skill)
                    if skill_type == "passive":
                        target = find_player(room, target_id)
                        if not target: return self.send_json(400, {"error": "Choose an ally to support."})
                        target["guard"] += 5
                        record_support(room, player)
                        msg = f"{player['name']} set {target['name']} behind a {skill['name']} ward. It will soften the next enemy attack (d20: {die})."
                    elif skill_type == "heal":
                        target = find_player(room, target_id)
                        if not target: return self.send_json(400, {"error": "Choose an ally to heal."})
                        mode = "mana" if data.get("restore") == "mana" else "hp"
                        key, cap = ("mana", 100) if mode == "mana" else ("hp", target["maxHp"])
                        actual = min(cap - target[key], val)
                        target[key] += actual
                        record_support(room, player)
                        msg = f"{player['name']} used {skill['name']} on {target['name']} and restored {actual} {key.upper()} (d20: {die})."
                    elif skill_type == "mana":
                        target = find_player(room, target_id)
                        if not target: return self.send_json(400, {"error": "Choose an ally to restore."})
                        actual = min(100 - target["mana"], val)
                        target["mana"] += actual
                        record_support(room, player)
                        msg = f"{player['name']} restored {actual} mana to {target['name']} (d20: {die})."
                    else:
                        enemy = find_enemy(room, target_id)
                        if not enemy: return self.send_json(400, {"error": "Choose a living enemy."})
                        dmg = max(1, val + player["atk"] - enemy.get("armor", 0))
                        enemy["hp"] = max(0, enemy["hp"] - dmg)
                        record_choice(room, "damageActions")
                        msg = f"{player['name']} used {skill['name']} against {enemy['name']} for {dmg} damage (d20: {die})."
                elif kind in ("heal", "restore"):
                    skill = next((s for s in player["skills"] if s["type"] == kind), None)
                    if not skill: return self.send_json(400, {"error": "Your essences do not grant this kind of healing."})
                    if player["mana"] < skill["cost"]: return self.send_json(400, {"error": "Not enough mana."})
                    player["mana"] -= skill["cost"]; die = random.randint(1, 20); target = find_player(room, target_id) or player
                    gain = min((target["maxHp"] if kind == "heal" else 100) - (target["hp"] if kind == "heal" else target["mana"]), roll_value(die))
                    target["hp" if kind == "heal" else "mana"] += gain
                    record_support(room, player)
                    msg = f"{player['name']} restored {gain} {'HP' if kind == 'heal' else 'mana'} to {target['name']} (d20: {die})."
                elif kind in ("potion_hp", "potion_mana"):
                    ptype = "healing" if kind == "potion_hp" else "mana"
                    if player["potions"][ptype] <= 0: return self.send_json(400, {"error": "No potion of that kind."})
                    target = find_player(room, target_id)
                    if not target: return self.send_json(400, {"error": "Choose an ally to receive the potion."})
                    if incapacitated and target["id"] == player["id"]: return self.send_json(400, {"error": "An ally must use a turn to restore you."})
                    player["potions"][ptype] -= 1; die = random.randint(1,20); amt = max(1, round(roll_value(die) / 2))
                    key = "hp" if ptype == "healing" else "mana"; limit = target["maxHp"] if key == "hp" else 100
                    gain = min(limit - target[key], amt); target[key] += gain
                    record_support(room, player)
                    msg = f"{player['name']} used a {ptype} potion on {target['name']} for {gain} {key.upper()} (half effect; d20: {die})."
                else: return self.send_json(400, {"error": "Choose an action."})
                room["log"].insert(0, msg)
                room["turn"] += 1
                skip_incapacitated_turns(room)
                if room["turn"] >= len(room["players"]):
                    enemy_turn(room)
                    skip_incapacitated_turns(room, rescue=True)
                check_result(room)
            elif action == "vote":
                if room["phase"] != "loot": return self.send_json(400, {"error": "There is no loot vote now."})
                item_id = str(data.get("item", "")); recipient = str(data.get("recipient", ""))
                if not any(x["id"] == item_id and not x["taken"] for x in room["loot"]) or not find_player(room, recipient): return self.send_json(400, {"error": "Choose available loot and a teammate."})
                room["votes"].setdefault(item_id, {})[pid] = recipient
                settle_votes(room, item_id)
            elif action == "buy":
                if room["phase"] != "shop": return self.send_json(400, {"error": "The store is closed."})
                item_id = str(data.get("item", "")); item = next((x for x in room["shop"] if x["id"] == item_id), None)
                if not item or item["taken"]: return self.send_json(400, {"error": "That item is no longer available."})
                if room["sharedGold"] < item["price"]: return self.send_json(400, {"error": "The party cannot afford that."})
                room["sharedGold"] -= item["price"]; item["taken"] = True
                record_choice(room, "goldSpent", item["price"])
                if item["kind"] == "ward":
                    for hero in room["players"]: hero["def"] += 5
                    player["items"].append(item["name"])
                elif item["kind"] == "vitality":
                    player["hp"] = min(player["maxHp"], player["hp"] + 35)
                elif item["kind"] == "prism":
                    player["mana"] = min(100, player["mana"] + 30)
                room["log"].insert(0, f"{player['name']} spent {item['price']} shared gold on {item['name']}.")
            else: return self.send_json(404, {"error": "Unknown action."})
            return self.send_json(200, public(room))

MANA_TIERS = [(1, "Passive"), (2, "Low"), (4, "Medium"), (6, "High"), (8, "Extreme"), (10, "Insane")]
HEAL_ESSENCES = {"life", "renewal", "pure", "serene", "feast", "blood", "growth"}
MANA_ESSENCES = {"gathering", "knowledge", "star", "moon", "sun", "dimension", "space", "void", "magic", "rune"}

def skill_for(desc, essences, cost, tier, awakened=False, forced_kind=None):
    essences = essences or ["Magic"]
    text = desc.lower()
    can_heal = bool({x.lower() for x in essences} & HEAL_ESSENCES) or any(w in text for w in ("healer", "healing", "medic", "mend", "restore", "support"))
    can_restore_mana = bool({x.lower() for x in essences} & MANA_ESSENCES) or any(w in text for w in ("scholar", "wizard", "mage", "study", "magic", "arcane"))
    if cost == 1:
        kind = "passive"
    else:
        kinds = ["damage", "damage", "heal" if can_heal else "damage", "mana" if can_restore_mana else "damage"]
        kind = forced_kind or random.choice(kinds)
    suited = {"heal": [e for e in essences if e.lower() in HEAL_ESSENCES], "mana": [e for e in essences if e.lower() in MANA_ESSENCES]}.get(kind, [])
    essence = random.choice(suited or essences)
    lower = essence.lower()
    epithets = {"fire":"Emberbound", "water":"Tideglass", "ice":"Rimewoven", "cold":"Frostmarked", "air":"Skyborne", "wind":"Galeheart", "lightning":"Stormkissed", "earth":"Stoneward", "iron":"Ironroot", "armor":"Aegisforged", "shield":"Unbroken", "life":"Verdant", "renewal":"Dawnborn", "growth":"Wildbloom", "plant":"Briarbound", "light":"Sunlit", "dark":"Nightwoven", "shadow":"Veilborn", "death":"Gravebright", "blood":"Crimson", "knowledge":"Runescribed", "magic":"Spellwoven", "rune":"Glyphmarked", "song":"Resonant", "moon":"Moonlit", "sun":"Solar", "star":"Starfallen", "space":"Astral", "void":"Voidtouched", "dimension":"Riftwalking", "crystal":"Prismbright", "poison":"Venomkissed", "claw":"Wildheart", "wolf":"Packsworn", "bird":"Wingborne", "swift":"Fleetfoot"}
    epithet = epithets.get(lower, f"{essence}bound")
    if cost == 1:
        kind, name, effect = "passive", f"{epithet} Aegis", f"A quiet ward of {lower} settles over an ally, blunting the next enemy strike by 5."
    else:
        titles = {"damage": {2:"Needle",4:"Lance",6:"Tempest",8:"Nova",10:"Cataclysm"}, "heal": {2:"Mend",4:"Reprieve",6:"Renewal",8:"Sanctuary",10:"Phoenix Grace"}, "mana": {2:"Spark",4:"Current",6:"Deepwell",8:"Resonance",10:"Aetherfont"}}
        name = f"{epithet} {titles[kind][cost]}"
        motifs = {"fire":"emberglass", "water":"the deep tide", "ice":"blue rime", "cold":"winter's breath", "air":"the high wind", "wind":"a cutting gale", "lightning":"stormlight", "earth":"old mountain stone", "iron":"forged iron", "armor":"bright steel", "shield":"a steadfast bulwark", "life":"verdant light", "renewal":"the first light of dawn", "growth":"living vines", "plant":"thorn and blossom", "light":"sunfire", "dark":"the velvet dark", "shadow":"a veil of dusk", "death":"grave-cold mist", "blood":"a crimson pulse", "knowledge":"a constellation of runes", "magic":"raw spellfire", "rune":"a chain of glowing glyphs", "song":"a chord of resonance", "moon":"silver moonlight", "sun":"a shard of sunlight", "star":"falling starlight", "space":"the astral current", "void":"the hungry void", "dimension":"folded space", "crystal":"prismatic glass", "poison":"emerald venom", "claw":"wild spirit", "wolf":"the hunting pack", "bird":"a storm of feathers", "swift":"a streak of speed", "staff":"the staff's silver grain"}
        motif = motifs.get(lower, f"{lower} essence")
        damage_scenes = {2:f"A needle of {motif} slips through an enemy's guard.", 4:f"A spiraling lance of {motif} sweeps across the battlefield and hammers one foe.", 6:f"A gathering storm of {motif} crashes into the chosen enemy.", 8:f"A blazing nova of {motif} blooms at your foe's feet, then collapses in a thunderclap.", 10:f"A torrent of {motif} surges through the breach and engulfs your target."}
        heal_scenes = {2:f"A warm thread of {motif} closes a fresh wound with a shimmer.", 4:f"A ring of {motif} circles an ally, knitting their strength back together.", 6:f"A wave of {motif} washes over a companion and steadies their breath.", 8:f"A sheltering canopy of {motif} rises around an ally, mending body or spirit.", 10:f"A radiant surge of {motif} calls an ally back from the brink in a blaze of renewal."}
        mana_scenes = {2:f"A bright mote of {motif} sparks in an ally's palm.", 4:f"A flowing current of {motif} refills an ally's fading reserves.", 6:f"A deep well of {motif} opens beneath a companion, restoring their focus.", 8:f"A resonant halo of {motif} pours fresh power into an ally.", 10:f"An immense font of {motif} floods an ally with renewed magical force."}
        effect = {
            "damage": damage_scenes[cost],
            "heal": heal_scenes[cost],
            "mana": mana_scenes[cost],
        }[kind]
    if awakened:
        name = f"Awakened {name}"
        effect = f"Kindled by an awakening stone from {', '.join(essences)}, this art answers your character's nature. {effect}"
    return {"name": name, "desc": effect, "cost": cost, "tier": tier, "type": kind, "awakened": awakened}

def make_skills(desc, essences):
    skills = [skill_for(desc, essences, cost, tier) for cost, tier in MANA_TIERS]
    words = desc.lower()
    can_heal = bool({x.lower() for x in essences} & HEAL_ESSENCES) or any(w in words for w in ("healer", "healing", "medic", "mend", "restore", "support"))
    can_restore_mana = bool({x.lower() for x in essences} & MANA_ESSENCES) or any(w in words for w in ("scholar", "wizard", "mage", "study", "magic", "arcane"))
    if can_heal:
        skills[2] = skill_for(desc, essences, 4, "Medium", forced_kind="heal")
    if can_restore_mana:
        index = 3 if can_heal else 2
        cost, tier = MANA_TIERS[index]
        skills[index] = skill_for(desc, essences, cost, tier, forced_kind="mana")
    if not any(skill["type"] == "damage" for skill in skills):
        protected = {2} if can_heal else set()
        if can_restore_mana:
            protected.add(3 if can_heal else 2)
        index = next(i for i in range(len(MANA_TIERS) - 1, 0, -1) if i not in protected)
        cost, tier = MANA_TIERS[index]
        skills[index] = skill_for(desc, essences, cost, tier, forced_kind="damage")
    if can_heal:
        candidates = [i for i, skill in enumerate(skills) if skill["type"] in {"damage", "heal"}]
        if candidates:
            count = random.randint(1, min(3, len(candidates)))
            for index in random.sample(candidates, count):
                skills[index]["type"] = "hybrid"
    return skills

def make_awakened_skill(player, cost, tier):
    return skill_for(player.get("description", ""), player.get("essences", []), cost, tier, awakened=True)

def roll_value(d): return max(1, round(d * 1.25))
def skill_value(die, skill):
    value = roll_value(die)
    factor = {"Low": 0.8, "Medium": 1.0, "High": 1.25, "Extreme": 1.6, "Insane": 2.0}.get(skill.get("tier"), 1)
    return max(1, round(value * factor))
def find_player(room, pid): return next((p for p in room["players"] if p["id"] == pid), None)
def find_enemy(room, eid): return next((e for e in room["enemies"] if e["id"] == eid and e["hp"] > 0), None)
def story_for(r, i):
    arc = r.get("campaign", random.choice(CAMPAIGN_ARCS))
    choices = r.setdefault("campaignChoices", {})
    if i == 1:
        scenes = [
            f"The first breach collapses with a sound like a distant bell. Across {arc['settlement']}, the ward-lanterns flare back to life, and guild runners race along the rooftops carrying the news. {arc['companion']} reports that the cult's pattern is not a simple invasion: they were {arc['scheme']}. Jason studies the tracks left in the dust. ‘The next breach is already changing. We need to understand what the cult is building before we meet it.’",
            f"A rain of pale sparks drifts from the sealed breach and settles on the trade road. At {arc['settlement']}, merchants and essence-users gather around a map that now shows a new route through the hills. {arc['companion']} connects the marks to the cult's plan of {arc['scheme']}. Jason nods toward the second rift. ‘We bought this region a little time. The next clue is waiting on the other side.’",
        ]
        if choices.get("sideQuestsWon", 0):
            scenes.append(f"Word reaches the party that the people of {arc['settlement']} are safe because you answered the guild contract. They send a hand-drawn map of the cult's hidden supply path, turning your detour into a lead. Jason smiles. ‘Good deeds have a way of finding their way back to the party.’")
        elif choices.get("sideQuestsSkipped", 0):
            scenes.append(f"A runner from {arc['settlement']} catches up with the party: the residents evacuated before the cult scouts arrived, but their abandoned storehouse has fallen into enemy hands. The report reveals the cult's plan of {arc['scheme']}. Jason folds the note. ‘We couldn't be in two places at once. Now we know what we're walking into.’")
        return random.choice(scenes)
    if i == 2:
        scenes = [
            f"The second breach seals, and a silence settles over the road. The party's report exposes the wider design: the Builder cults were {arc['scheme']}, using the unrest around {arc['settlement']} to conceal their final ritual. {arc['companion']} marks the ritual's likely center beneath an old aqueduct. Jason looks toward the silver-lit horizon. ‘The last breach is the keystone. If it opens fully, every ward on this route could fail.’",
            f"At dusk, the party reaches the old aqueduct. Silver threads of magic hang in the air, connecting the last breach to distant city wards. {arc['companion']} recognizes the same signature seen near {arc['settlement']}: the cults were {arc['scheme']}. Jason lowers his voice. ‘The final fight won't just decide who owns this road. It could decide who controls the region's essence network.’",
        ]
        if choices.get("sideQuestsWon", 0):
            scenes.append(f"A message from {arc['settlement']} arrives with a warning from the people you protected: the cult has moved its ritual focus below the aqueduct. Their knowledge gives the party a safer route to the last breach. Jason passes the note around. ‘They trusted us with this. Let's make it count.’")
        elif choices.get("sideQuestsSkipped", 0):
            scenes.append(f"The guild reports that {arc['settlement']} survived by evacuating, but the cult took control of the local ward station. Its signal now feeds the last breach. Jason traces the new threat on the map. ‘The road is clear enough to reach the source, but we'll have to break through its defenses the hard way.’")
        return random.choice(scenes)
    return "Jason Asano taps the first breach on the map. ‘A team that watches one another survives. Decide who draws its attention before you cross the threshold.’"

BOSSES = [([
    [("Rift Hound",75,7), ("Veil Stalker",62,8)],
    [("Corrupted Scout",68,7), ("Ember Wisp",72,8)],
    [("Dungeon Brute",82,8), ("Ashwing Stalker",58,7)],
], "Iron"), ([
    [("Corrupted Guardian",105,10), ("Astral Leech",82,11)],
    [("Rift Captain",118,10), ("Veil Hexer",76,12)],
    [("Astral Devourer",120,11), ("Rift Hound Alpha",88,10)],
], "Bronze"), ([
    [("Rift Tyrant",155,13), ("Void Herald",125,14), ("Rift Spawn",65,9)],
    [("Astral Warlord",170,13), ("Voidbound Ravager",115,15)],
    [("Dimension Breaker",145,14), ("Star-Eater",125,15), ("Riftling Swarm",74,10)],
], "Silver")]
BOSS_REINFORCEMENTS = [
    [("Cult Initiate",42,7), ("Riftling",48,7)],
    [("Bound Acolyte",62,9), ("Ward-Eater",68,10)],
    [("Builder's Envoy",78,12), ("Voidbound Guard",84,13)],
]

def scaled_enemy_group(templates, count, party_size, rank, attack_bonus=0, hp_growth=0.13, armor_max=3):
    selected = random.sample(templates, min(count, len(templates)))
    while len(selected) < count:
        selected.append(random.choice(BOSS_REINFORCEMENTS[0]))
    average_hp = sum(enemy[1] for enemy in templates) / len(templates)
    total_hp = round(average_hp * count * (1 + hp_growth * (party_size - 2)))
    weight_total = sum(enemy[1] for enemy in selected)
    enemies = []
    for name, base_hp, base_attack in selected:
        hp = max(20, round(total_hp * base_hp / weight_total) + random.randint(-8, 8))
        enemies.append({"id":str(random.getrandbits(32)),"name":name,"rank":rank,"hp":hp,"maxHp":hp,
                        "attack":base_attack + attack_bonus,"armor":random.randint(0,armor_max)})
    return enemies

def launch_boss(r):
    idx = r["bossIndex"]
    variants, rank = BOSSES[idx]
    party_size = max(2, min(8, len(r["players"])))
    templates = random.choice(variants)
    count = min(4, 1 + (party_size - 1) // 2 + (1 if party_size < 7 and random.random() < 0.45 else 0))
    reinforcements = BOSS_REINFORCEMENTS[idx]
    if count > len(templates):
        templates = templates + random.sample(reinforcements, min(count - len(templates), len(reinforcements)))
    r["enemies"] = scaled_enemy_group(templates, count, party_size, rank,
                                       attack_bonus=max(0, (party_size - 2) // 3), hp_growth=0.13)
    armor_reduction = r.pop("nextBossArmorReduction", 0)
    if armor_reduction:
        for enemy in r["enemies"]:
            enemy["armor"] = max(0, enemy["armor"] - armor_reduction)
    opening_guard = r.pop("nextBossGuard", 0)
    if opening_guard:
        for hero in r["players"]:
            hero["guard"] += opening_guard
    rest_stacks = min(3, r.get("restDifficulty", 0))
    if rest_stacks:
        for enemy in r["enemies"]:
            enemy["hp"] = round(enemy["hp"] * (1 + 0.10 * rest_stacks))
            enemy["maxHp"] = enemy["hp"]
            enemy["attack"] += rest_stacks
            enemy["armor"] += rest_stacks
    r["phase"] = "battle"; r["turn"] = 0; r["round"] = 1; r["log"] = [f"Boss {idx+1} · {rank} rank: {', '.join(x['name'] for x in r['enemies'])} block the way." + (f" Rest empowerment: +{rest_stacks * 10}% HP, +{rest_stacks} attack, and +{rest_stacks} armor." if rest_stacks else "")]
    skip_incapacitated_turns(r, rescue=True)
    check_result(r)

def launch_sidequest(r):
    party_size = max(2, min(8, len(r["players"])))
    rank = BOSSES[min(r["bossIndex"], 2)][1]
    count = min(3, 1 + max(0, (party_size - 2) // 3))
    side_templates = [("Astral Stray",50 + r["bossIndex"] * 9,5 + min(r["bossIndex"],2)),
                      ("Riftbound Stalker",54 + r["bossIndex"] * 9,6 + min(r["bossIndex"],2)),
                      ("Corrupted Scout",48 + r["bossIndex"] * 9,5 + min(r["bossIndex"],2))]
    random.shuffle(side_templates)
    r["enemies"] = scaled_enemy_group(side_templates, count, party_size, rank,
                                       attack_bonus=max(0, (party_size - 2) // 3), hp_growth=0.12, armor_max=1)
    r["sideQuestCount"] = r.get("sideQuestCount", 0) + 1
    choices = r.setdefault("campaignChoices", {})
    choices["sideQuestsAccepted"] = choices.get("sideQuestsAccepted", 0) + 1
    r["sideQuest"] = True; r["phase"] = "battle"; r["turn"] = 0; r["round"] = 1
    r["log"] = ["Optional guild contract accepted. A side-boss blocks the route; victory will earn a small reward."]
    skip_incapacitated_turns(r, rescue=True)
    check_result(r)

def skip_incapacitated_turns(r, rescue=False):
    while r["turn"] < len(r["players"]):
        player = r["players"][r["turn"]]
        if player["hp"] > 0 and player["mana"] > 0:
            return
        r["log"].insert(0, f"{player['name']} is out of HP or mana and needs an ally to restore them.")
        r["turn"] += 1
    if rescue:
        for i, player in enumerate(r["players"]):
            potions = player.get("potions", {})
            if potions.get("healing", 0) or potions.get("mana", 0):
                r["turn"] = i
                return

def enemy_turn(r):
    for e in [x for x in r["enemies"] if x["hp"] > 0]:
        living = [p for p in r["players"] if p["hp"] > 0 and p["mana"] > 0]
        if not living: break
        die = random.randint(1,10); target = random.choice(living); dmg = max(1, die + e["attack"] - target["def"] - target.get("guard", 0)); target["guard"] = 0
        target["hp"] = max(0, target["hp"] - dmg)
        r["log"].insert(0, f"{e['name']} attacked {target['name']} for {dmg} damage (d10: {die}).")
    for p in r["players"]: p["guard"] = 0
    r["turn"] = 0; r["round"] += 1

def check_result(r):
    can_fight = any(p["hp"] > 0 and p["mana"] > 0 for p in r["players"])
    healing_potions = sum(p.get("potions", {}).get("healing", 0) for p in r["players"])
    mana_potions = sum(p.get("potions", {}).get("mana", 0) for p in r["players"])
    if not can_fight and healing_potions == 0 and mana_potions == 0:
        r["phase"] = "ended"
        r["scene"] = "No one ever heard from them again."
        r["defeatReason"] = "The party had no healing or mana potions left, and no one had both HP and mana remaining."
        r["log"].insert(0, "Defeat: the party had no healing or mana potions remaining, and no hero could continue the fight.")
        return
    if not any(e["hp"] > 0 for e in r["enemies"]):
        was_side_quest = r.get("sideQuest", False)
        if was_side_quest:
            r["sideQuest"] = False; r["returnToStory"] = True
            choices = r.setdefault("campaignChoices", {})
            choices["sideQuestsWon"] = choices.get("sideQuestsWon", 0) + 1
            ranks = ["Iron", "Bronze", "Silver", "Gold", "Diamond"]
            for hero in r["players"]:
                hero["rankProgress"] += 1
                old_rank = ranks.index(hero["rank"])
                if old_rank < len(ranks) - 1:
                    hero["rank"] = ranks[old_rank + 1]
                    hero["maxHp"] += 8; hero["hp"] += 8; hero["atk"] += 2; hero["def"] += 1
            r["log"].insert(0, "Side contract complete: the whole party gains a rank and strengthens together.")
        else:
            r["bossIndex"] += 1
        r["loot"] = make_loot(r)
        r["sharedGold"] += random.randint(15,30) if was_side_quest else random.randint(30,80) * r["bossIndex"]
        r["phase"] = "loot"; r["votes"]={}; r["scene"] = "The breach goes quiet. The team gathers what the monsters left behind."; r["turn"] = 0
        r["log"].insert(0,"Encounter cleared. Loot is ready to claim.")

def make_loot(r):
    i = r["bossIndex"]
    pool = [{"kind":"potion_hp","name":"Healing potion","desc":"Restores health; half effect in battle."}, {"kind":"potion_mana","name":"Mana potion","desc":"Restores mana; half effect in battle."}, {"kind":"armor","name":"Ward-forged armor","desc":"Raises defense by 3."}, {"kind":"weapon","name":"Essence-forged weapon","desc":"Raises attack by 4."}, {"kind":"awakening","name":"Awakening stone","desc":"Single-use stone unlocks another essence ability."}]
    random.shuffle(pool)
    # Guarantee each loot class across the journey, with extra random drops for variety.
    if r.get("returnToStory"):
        potions = [x for x in pool if x["kind"] in {"potion_hp", "potion_mana"}]
        chosen = [random.choice(potions)]
        extras = [x for x in pool if x not in chosen]
        chosen.extend(random.sample(extras, random.randint(0, min(1, len(extras)))))
    else:
        guaranteed = {1: {"potion_hp", "potion_mana"}, 2: {"armor", "weapon"}, 3: {"awakening"}}[i]
        chosen = [x for x in pool if x["kind"] in guaranteed]
        extras = [x for x in pool if x["kind"] not in guaranteed]
        chosen.extend(random.sample(extras, random.randint(0, min(2, len(extras)))))
    random.shuffle(chosen)
    out=[]
    for x in chosen:
        out.append({**x,"id":str(random.getrandbits(40)),"taken":False})
    return out

def settle_votes(r, item_id):
    item = next(x for x in r["loot"] if x["id"] == item_id)
    votes = r["votes"].get(item_id,{})
    # Everyone still in the room gets a vote; resolve when all have voted.
    if len(votes) < len(r["players"]): return
    counts={}
    for choice in votes.values(): counts[choice]=counts.get(choice,0)+1
    top=max(counts.values()); winners=[pid for pid,n in counts.items() if n==top]
    recipient=find_player(r,random.choice(winners)); apply_item(recipient,item); item["taken"]=True; item["winner"]=recipient["id"]
    tie = " (random tie-break)" if len(winners)>1 else ""
    r["log"].insert(0,f"The vote awarded {item['name']} to {recipient['name']}{tie}.")

def apply_item(p,item):
    if item["kind"]=="potion_hp": p["potions"]["healing"]+=1
    elif item["kind"]=="potion_mana": p["potions"]["mana"]+=1
    elif item["kind"]=="armor": p["def"]+=3; p["items"].append(item["name"])
    elif item["kind"]=="weapon": p["atk"]+=4; p["items"].append(item["name"])
    elif item["kind"]=="awakening":
        p["pendingAwakenings"] = p.get("pendingAwakenings", 0) + 1

def victory_epilogue(r):
    arc = r.get("campaign", random.choice(CAMPAIGN_ARCS))
    choices = r.get("campaignChoices", {})
    opening = random.choice([
        f"When the third breach collapses, the Builder cults' signal dies across Pallimustus. The last construct falls silent beneath the old aqueduct, and the sky above {arc['settlement']} clears for the first time in weeks.",
        f"The final seal closes with a wave of silver light. Far beyond the battlefield, ward-lanterns relight one by one, carrying the news from {arc['settlement']} along the trade road.",
        f"As the last Builder altar breaks, the three cults lose their hold on the region's essence network. The breachwind fades, revealing the hills and city lights around {arc['settlement']} intact beneath the night sky.",
    ])
    aftermath = []
    if choices.get("sideQuestsWon", 0):
        aftermath.append(f"Because the party answered the guild contract, the people of {arc['settlement']} survived the cult's advance. They turn the rescued waystation into a refuge and send the guild a map of safe roads through the region.")
    elif choices.get("sideQuestsSkipped", 0):
        aftermath.append(f"By pressing on, the party left the people of {arc['settlement']} to face the danger alone. The residents escaped before the cults arrived, but their ward station was lost. Rebuilding it becomes the guild's first task.")
    else:
        aftermath.append(f"With the cults driven out, {arc['settlement']} opens its gates to the returning guild teams. The town's merchants and essence-users begin restoring the damaged wards together.")
    if choices.get("goldSpent", 0):
        aftermath.append("The rare gear bought from the astral outfitter is put to work repairing the trade route, and the local merchants record the party's investment as the beginning of a new alliance.")
    else:
        aftermath.append("The party's unspent treasury stays in the guild vault, reserved for the next crew sent to protect the region.")
    supporters = choices.get("supporters", [])
    if supporters:
        remembered = supporters[:3]
        if len(remembered) == 1:
            names, verb = remembered[0], "is"
        elif len(remembered) == 2:
            names, verb = f"{remembered[0]} and {remembered[1]}", "are"
        else:
            names, verb = f"{', '.join(remembered[:-1])}, and {remembered[-1]}", "are"
        aftermath.append(f"In the guild's battle report, {names} {verb} remembered for keeping companions standing when the line began to break.")
    else:
        aftermath.append("The guild chroniclers describe a party that trusted each member to hold their own place in the line.")
    closing = random.choice([
        "Jason Asano watches the first repair crew raise a fresh ward-lantern. ‘You didn't just stop the cults. You gave people a future to build on.’ The companions gather around the party as the guild bell rings out across the road.",
        "Your guild companion folds the marked map and hands it to the next team. Jason smiles at the party. ‘That's how a region gets its roads, its wards, and its confidence back—one team choosing to stand together.’",
        "At dawn, the guild posts a new map with the three breaches crossed out. Jason raises a cup to the party. ‘Pallimustus is safer tonight because you made every hard choice together.’",
    ])
    return " ".join([opening, *aftermath, closing])

def open_store_or_next(r):
    if r.get("returnToStory"):
        r["returnToStory"] = False; r["phase"] = "story"
        arc = r.get("campaign", random.choice(CAMPAIGN_ARCS))
        r["scene"] = f"The guild records the party's side-contract success. Word from {arc['settlement']} confirms the residents are safe, and the team earns a rank for answering the call. {arc['companion']} shares their thanks. The next breach is still ahead."
        r["storyNext"] = "boss"
        prepare_story_decision(r)
        return
    if r["bossIndex"] >= 3:
        r["phase"]="victory"; r["scene"]=victory_epilogue(r); return
    if not r["shop"]:
        r["shop"]=[{"id":str(random.getrandbits(32)),"name":"Astral Ward Sigil","desc":"All heroes gain +5 defense for the next boss.","price":55,"taken":False,"kind":"ward"}, {"id":str(random.getrandbits(32)),"name":"Grand Vitality Draught","desc":"Restore 35 HP to one hero now.","price":45,"taken":False,"kind":"vitality"}, {"id":str(random.getrandbits(32)),"name":"Mana Prism","desc":"Restore 30 mana to one hero now.","price":40,"taken":False,"kind":"prism"}]
    r["phase"]="shop"; r["scene"]="A discreet astral outfitter opens a case of exceptionally rare goods. The party can spend its shared gold or save it for later."; r["shop"]=[x for x in r["shop"] if not x["taken"]]

if __name__ == "__main__":
    host, port = "0.0.0.0", 8000
    print(f"Pallimustus party game at http://localhost:{port}")
    print("For other devices on your Wi-Fi, use this computer's local network address and port 8000.")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
