import streamlit as st
import folium
from streamlit_folium import st_folium
import requests
import pandas as pd
from pymongo import MongoClient
from datetime import datetime

# BEZPEČNÉ NAČTENÍ HESLA: Streamlit si ho vytáhne ze skrytého nastavení (Secrets)
MONGO_URI = st.secrets["MONGO_URI"]

@st.cache_resource
def get_mongo_client():
    # Využíváme standardní TLS konfiguraci bez externích certifikátů
    return MongoClient(
        MONGO_URI,
        tls=True,
        tlsAllowInvalidCertificates=False
    )

client = get_mongo_client()
db = client["flight_tracker"]       
collection = db["history"]         

def fetch_and_save_to_mongo():
    # Souřadnice oblasti (lamin, lamax, lomin, lomax)
    bbox = {'lamin': 50.0, 'lamax': 50.5, 'lomin': 15.5, 'lomax': 16.2}
    
    # OPRAVA: URL adresa upravená tak, aby params šly striktně na endpoint states/all
    url = "https://opensky-network.org/api/states/all"
    
    try:
        # Přidáno ošetření chyb přímo do requests.get
        res = requests.get(url, params=bbox, timeout=15)
        
        if res.status_code == 200:
            states = res.json().get("states", [])
            if not states:
                st.warning("🛩️ V zadané oblasti zrovna neletí žádná letadla.")
                return

            documents = []
            for f in states:
                doc = {
                    "timestamp": datetime.utcnow(),
                    "icao24": f[0] if f[0] else "UNKNOWN",
                    "callsign": f[1].strip() if f[1] else "UNKNOWN",
                    "origin_country": f[2] if f[2] else "UNKNOWN",
                    "longitude": f[5],
                    "latitude": f[6],
                    "altitude": f[7],
                    "velocity": f[9]
                }
                documents.append(doc)
            
            collection.insert_many(documents)
            st.success(f"⚡ Staženo a úspěšně uloženo {len(documents)} letadel do MongoDB Atlas!")
        
        elif res.status_code == 429:
            st.error("⚠️ OpenSky API hlásí přetížení (Rate Limit). Zkuste kliknout znovu za 1-2 minuty.")
        else:
            st.error(f"⚠️ OpenSky API vrátilo chybu s kódem: {res.status_code}")
            
    except requests.exceptions.Timeout:
        st.warning("⏳ Server OpenSky neodpověděl včas (Timeout). Pravděpodobně je přetížený, zkuste to prosím za chvíli znovu. Mapa níže zobrazuje historii.")
    except Exception as e:
        st.error(f"Chyba při komunikaci s API nebo DB: {e}")


# --- WEB rozhraní ---
st.set_page_config(layout="wide", page_title="Live Flight Tracker")
st.title("🛩️ Veřejný tracker letadel s historií")

if st.button("🔄 Aktualizovat a stáhnout nová data z OpenSky"):
    fetch_and_save_to_mongo()

# Načtení posledních 200 pozic z MongoDB pro zobrazení na mapě
raw_data = list(collection.find().sort("timestamp", -1).limit(200))

m = folium.Map(location=[50.2, 15.8], zoom_start=10)

if raw_data:
    df = pd.DataFrame(raw_data)
    
    for _, row in df.iterrows():
        if pd.notna(row['latitude']) and pd.notna(row['longitude']):
            cas_zaznamu = row['timestamp'].strftime('%H:%M:%S (%d.%m.)')
            popup_text = f"""
            <b>Let:</b> {row['callsign']}<br>
            <b>Čas:</b> {cas_zaznamu}<br>
            <b>Výška:</b> {row['altitude']} m<br>
            <b>Rychlost:</b> {int(row['velocity'] * 3.6) if row['velocity'] else 0} km/h
            """
            folium.Marker(
                location=[row['latitude'], row['longitude']],
                popup=folium.Popup(popup_text, max_width=250),
                icon=folium.Icon(color="blue", icon="plane", prefix="fa")
            ).add_to(m)

st_folium(m, width=1200, height=500)

if raw_data:
    st.subheader("📋 Historické záznamy z MongoDB")
    st.dataframe(df[['timestamp', 'callsign', 'origin_country', 'latitude', 'longitude', 'altitude']])
