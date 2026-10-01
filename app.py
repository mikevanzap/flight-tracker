import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
import pandas as pd
from pymongo import MongoClient
from datetime import datetime, timezone

# --- Nastavení stránky ---
st.set_page_config(layout="wide", page_title="Live Flight Tracker - Skalička")

# Souřadnice domova (Skalička 41, 500 03)
HOME_LAT = 50.2696
HOME_LON = 15.8577

# Ověření Secrets pro MongoDB
if "MONGO_URI" not in st.secrets:
    st.error("⚠️ Chybí konfigurace `MONGO_URI` v `.streamlit/secrets.toml`!")
    st.info("Pro lokální spuštění vytvořte soubor `.streamlit/secrets.toml` s obsahem: \n\n`MONGO_URI = \"mongodb+srv://...\"`")
    st.stop()

MONGO_URI = st.secrets["MONGO_URI"]

@st.cache_resource
def get_mongo_client():
    return MongoClient(
        MONGO_URI,
        tls=True,
        tlsAllowInvalidCertificates=False
    )

try:
    client = get_mongo_client()
    db = client["flight_tracker"]
    collection = db["history"]
except Exception as e:
    st.error(f"Chyba při připojení k MongoDB: {e}")
    st.stop()


def fetch_and_save_to_mongo():
    # Bounding box pokrývající okolí Hradce Králové a Skaličky
    bbox = {'lamin': 50.0, 'lamax': 50.5, 'lomin': 15.5, 'lomax': 16.2}
    url = "https://opensky-network.org/api/states/all"

    try:
        res = requests.get(url, params=bbox, timeout=10)

        if res.status_code == 200:
            states = res.json().get("states", [])
            if not states:
                st.warning("🛩️ V zadané oblasti zrovna neletí žádná letadla.")
                return

            documents = []
            now = datetime.now(timezone.utc)
            for f in states:
                # Přeskočit záznamy bez GPS souřadnic
                if f[5] is None or f[6] is None:
                    continue

                doc = {
                    "timestamp": now,
                    "icao24": f[0] if f[0] else "UNKNOWN",
                    "callsign": f[1].strip() if f[1] else "UNKNOWN",
                    "origin_country": f[2] if f[2] else "UNKNOWN",
                    "longitude": f[5],
                    "latitude": f[6],
                    "altitude": f[7],
                    "on_ground": f[8],
                    "velocity": f[9],
                    "heading": f[10]
                }
                documents.append(doc)

            if documents:
                collection.insert_many(documents)
                st.success(f"⚡ Staženo a úspěšně uloženo {len(documents)} letadel do MongoDB Atlas!")
            else:
                st.info("Byla zachycena letadla bez platné GPS polohy.")

        elif res.status_code == 429:
            st.error("⚠️ OpenSky API rate limit (příliš mnoho požadavků). Počkejte prosím 1-2 minuty.")
        else:
            st.error(f"⚠️ OpenSky API vrátilo chybu: {res.status_code}")

    except requests.exceptions.Timeout:
        st.warning("⏳ Server OpenSky neodpověděl včas (Timeout). Zkuste to za chvíli.")
    except Exception as e:
        st.error(f"Chyba při komunikaci s API nebo DB: {e}")


# --- Uživatelské rozhraní ---
st.title("🛩️ Flight Tracker — Skalička & Hradec Králové")

col1, col2 = st.columns([2, 1])
with col1:
    if st.button("🔄 Aktualizovat a stáhnout nová data z OpenSky", type="primary"):
        fetch_and_save_to_mongo()

# Načtení posledních záznamů z MongoDB
raw_data = list(collection.find().sort("timestamp", -1).limit(200))

# Vytvoření mapy se středem na Skaličce
m = folium.Map(location=[HOME_LAT, HOME_LON], zoom_start=11, control_scale=True)

# 1. Značka pro Skalička 41 (Domov)
folium.Marker(
    location=[HOME_LAT, HOME_LON],
    popup="🏠 <b>Skalička 41</b>",
    tooltip="Skalička 41",
    icon=folium.Icon(color="red", icon="home", prefix="fa")
).add_to(m)

# Kružnice dosahu 10 km kolem domu
folium.Circle(
    location=[HOME_LAT, HOME_LON],
    radius=10000,
    color="crimson",
    weight=1,
    fill=True,
    fill_opacity=0.05,
    popup="Okruh 10 km od domu"
).add_to(m)

if raw_data:
    df = pd.DataFrame(raw_data)

    # Vybereme pouze nejnovější záznam pro každé unikátní letadlo (aby se body nepřekrývaly)
    df_latest = df.drop_duplicates(subset=['icao24'], keep='first')

    for _, row in df_latest.iterrows():
        if pd.notna(row['latitude']) and pd.notna(row['longitude']):
            cas_zaznamu = row['timestamp'].strftime('%H:%M:%S (%d.%m.)')
            vyska_str = f"{int(row['altitude'])} m" if pd.notna(row['altitude']) else ("Na zemi" if row.get('on_ground') else "N/A")
            rychlost_str = f"{int(row['velocity'] * 3.6)} km/h" if pd.notna(row['velocity']) else "N/A"
            smer_str = f"{int(row['heading'])}°" if pd.notna(row.get('heading')) else "N/A"

            popup_text = f"""
            <b>Let:</b> {row['callsign']} ({row['icao24']})<br>
            <b>Země:</b> {row['origin_country']}<br>
            <b>Čas:</b> {cas_zaznamu}<br>
            <b>Výška:</b> {vyska_str}<br>
            <b>Rychlost:</b> {rychlost_str}<br>
            <b>Kurz:</b> {smer_str}
            """

            icon_color = "gray" if row.get('on_ground') else "blue"
            folium.Marker(
                location=[row['latitude'], row['longitude']],
                popup=folium.Popup(popup_text, max_width=250),
                tooltip=f"{row['callsign']} ({vyska_str})",
                icon=folium.Icon(color=icon_color, icon="plane", prefix="fa")
            ).add_to(m)

# Zobrazení mapy s responzivní šířkou a bez zbytečného reloadování při zoomu
st_folium(m, use_container_width=True, height=550, returned_objects=[])

if raw_data:
    st.subheader("📋 Poslední zachycená letadla")
    st.dataframe(
        df_latest[['timestamp', 'callsign', 'origin_country', 'latitude', 'longitude', 'altitude', 'velocity']],
        use_container_width=True
    )
