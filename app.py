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

# Běžné hlavičky prohlížeče, aby nás Cloudflare / API servery neblokovaly
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 FlightTracker/1.0"
}

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


def fetch_from_adsb_lol(radius_nm=35):
    """
    Rychlé komunitní ADS-B API bez přísných cloudových limitů a bez nutnosti registrace.
    Skvěle funguje i z prostředí Streamlit Cloud / AWS.
    """
    url = f"https://api.adsb.lol/v2/point/{HOME_LAT}/{HOME_LON}/{radius_nm}"
    res = requests.get(url, headers=HTTP_HEADERS, timeout=10)
    
    if res.status_code != 200:
        raise Exception(f"adsb.lol vrátilo HTTP kód {res.status_code}")
        
    data = res.json().get("ac", [])
    now = datetime.now(timezone.utc)
    documents = []

    for ac in data:
        lat = ac.get("lat")
        lon = ac.get("lon")
        if lat is None or lon is None:
            continue

        alt_baro = ac.get("alt_baro")
        is_ground = (alt_baro == "ground")
        alt_meters = round(alt_baro * 0.3048) if isinstance(alt_baro, (int, float)) else None

        gs_knots = ac.get("gs")
        vel_ms = round(gs_knots * 0.514444, 1) if isinstance(gs_knots, (int, float)) else None

        callsign = (ac.get("flight") or "").strip() or ac.get("r") or "UNKNOWN"

        doc = {
            "timestamp": now,
            "icao24": (ac.get("hex") or "UNKNOWN").lower(),
            "callsign": callsign,
            "origin_country": ac.get("r") or "ČR / Mezinárodní",
            "aircraft_type": ac.get("t") or "N/A",
            "longitude": lon,
            "latitude": lat,
            "altitude": alt_meters,
            "on_ground": is_ground,
            "velocity": vel_ms,
            "heading": ac.get("track"),
            "source": "adsb.lol"
        }
        documents.append(doc)

    return documents


def fetch_from_opensky():
    """
    Stažení dat z OpenSky Network (bounding box pro východní Čechy).
    """
    bbox = {'lamin': 50.0, 'lamax': 50.5, 'lomin': 15.5, 'lomax': 16.2}
    url = "https://opensky-network.org/api/states/all"
    
    # Zvýšený timeout na 20 sekund pro cloudová prostředí
    res = requests.get(url, params=bbox, headers=HTTP_HEADERS, timeout=20)
    
    if res.status_code == 429:
        raise Exception("OpenSky hlásí Rate Limit (429). Na sdílených cloudových IP bývá denní limit rychle vyčerpán.")
    elif res.status_code != 200:
        raise Exception(f"OpenSky vrátilo chybový kód: {res.status_code}")
        
    states = res.json().get("states", [])
    now = datetime.now(timezone.utc)
    documents = []

    for f in states:
        if f[5] is None or f[6] is None:
            continue

        doc = {
            "timestamp": now,
            "icao24": (f[0] or "UNKNOWN").lower(),
            "callsign": (f[1] or "UNKNOWN").strip(),
            "origin_country": f[2] or "UNKNOWN",
            "aircraft_type": "N/A",
            "longitude": f[5],
            "latitude": f[6],
            "altitude": f[7],
            "on_ground": f[8],
            "velocity": f[9],
            "heading": f[10],
            "source": "OpenSky"
        }
        documents.append(doc)

    return documents


def fetch_and_save_data(preferred_source):
    documents = []
    source_used = preferred_source

    with st.spinner(f"📡 Stahuji letová data přes {preferred_source}..."):
        if preferred_source == "OpenSky Network":
            try:
                documents = fetch_from_opensky()
            except (requests.exceptions.Timeout, Exception) as e:
                st.warning(f"⚠️ OpenSky neodpovědělo včas nebo selhalo: {e}")
                st.info("🔄 Automaticky přepínám na záložní zdroj **adsb.lol** (komunitní ADS-B síť bez blokování cloudu)...")
                try:
                    documents = fetch_from_adsb_lol()
                    source_used = "adsb.lol (Záložní)"
                except Exception as ex2:
                    st.error(f"Chyba i na záložním zdroji: {ex2}")
                    return
        else:
            try:
                documents = fetch_from_adsb_lol()
            except Exception as e:
                st.error(f"Chyba při stahování z adsb.lol: {e}")
                return

    if documents:
        collection.insert_many(documents)
        st.success(f"⚡ Úspěšně staženo a uloženo **{len(documents)} letadel** (zdroj: {source_used})!")
    else:
        st.warning("🛩️ V zadaném okruhu zrovna nebylo detekováno žádné letadlo.")


# --- Uživatelské rozhraní ---
st.title("🛩️ Live Flight Tracker — Skalička & Hradec Králové")

col1, col2 = st.columns([1, 2])
with col1:
    source_option = st.radio(
        "Zvolte zdroj dat:",
        ["adsb.lol (Doporučeno — bez blokování cloudu)", "OpenSky Network"],
        index=0,
        help="Na Streamlit Cloud sdílí tisíce aplikací stejnou IP adresu AWS. OpenSky anonymní dotazy z AWS často zpomaluje nebo blokuje (Timeout). Služba adsb.lol funguje okamžitě."
    )
with col2:
    st.write("")
    st.write("")
    if st.button("🔄 Aktualizovat a stáhnout nová data", type="primary"):
        target_source = "OpenSky Network" if "OpenSky" in source_option else "adsb.lol"
        fetch_and_save_data(target_source)

# Načtení posledních záznamů z MongoDB
raw_data = list(collection.find().sort("timestamp", -1).limit(200))

# Vytvoření mapy se středem na Skaličce
m = folium.Map(location=[HOME_LAT, HOME_LON], zoom_start=11, control_scale=True)

# 1. Značka pro Skalička 41 (Domov)
folium.Marker(
    location=[HOME_LAT, HOME_LON],
    popup="🏠 <b>Skalička 41</b><br>500 03 Hradec Králové",
    tooltip="Skalička 41 (Domov)",
    icon=folium.Icon(color="red", icon="home", prefix="fa")
).add_to(m)

# Kružnice dosahu 10 km kolem domu
folium.Circle(
    location=[HOME_LAT, HOME_LON],
    radius=10000,
    color="crimson",
    weight=1.5,
    fill=True,
    fill_opacity=0.06,
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
            typ_letadla = row.get('aircraft_type', 'N/A')
            if pd.isna(typ_letadla) or not typ_letadla:
                typ_letadla = "N/A"

            popup_text = f"""
            <b>Let:</b> {row['callsign']} ({row['icao24'].upper()})<br>
            <b>Typ:</b> {typ_letadla}<br>
            <b>Registrace / Země:</b> {row['origin_country']}<br>
            <b>Čas:</b> {cas_zaznamu}<br>
            <b>Výška:</b> {vyska_str}<br>
            <b>Rychlost:</b> {rychlost_str}<br>
            <b>Kurz:</b> {smer_str}
            """

            icon_color = "gray" if row.get('on_ground') else "blue"
            folium.Marker(
                location=[row['latitude'], row['longitude']],
                popup=folium.Popup(popup_text, max_width=250),
                tooltip=f"{row['callsign']} | {typ_letadla} | {vyska_str}",
                icon=folium.Icon(color=icon_color, icon="plane", prefix="fa")
            ).add_to(m)

# Zobrazení mapy s responzivní šířkou a bez zbytečného reloadování při zoomu
st_folium(m, use_container_width=True, height=550, returned_objects=[])

if raw_data:
    st.subheader(f"📋 Poslední zachycená letadla ({len(df_latest)} unikátních v dosahu)")
    display_cols = [c for c in ['timestamp', 'callsign', 'aircraft_type', 'origin_country', 'latitude', 'longitude', 'altitude', 'velocity', 'source'] if c in df_latest.columns]
    st.dataframe(df_latest[display_cols], use_container_width=True)
