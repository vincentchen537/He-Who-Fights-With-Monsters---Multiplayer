# Veilbound: a Pallimustus party adventure

The local prototype supports 2–8 players in a shared room, with character creation, essence selection, turn based d20/d10 combat, team loot votes, shared gold, and an optional rare item shop.

## Run locally

1. Open a terminal in this folder.
2. Run `python server.py` with Python 3 installed.
3. Open <https://he-who-fights-with-monsters-multiplayer-2.onrender.com/> in a browser.
4. Create a room. Teammates on this computer can join with the code. For another device on the same Wi-Fi, use this computer's local network IP address with `:8000`.

The local server keeps room state in memory. Rooms reset when the server stops. This prototype is for local testing and is not yet deployed to a public URL.

The source setting files are in `content/` and are read by the app from the project folder.
