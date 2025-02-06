import os
from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
from io import BytesIO
from PIL import Image
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials

# -----------------------------
# Load API keys from environment variables
# -----------------------------
API_KEY = os.environ.get("LASTFM_API_KEY")  # Last.fm API key
BASE_URL = "https://ws.audioscrobbler.com/2.0/"

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

def authenticate_spotify():
    client_credentials_manager = SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    )
    return Spotify(client_credentials_manager=client_credentials_manager)

def fetch_album_art_from_spotify(track_name, artist_name):
    spotify = authenticate_spotify()
    try:
        results = spotify.search(q=f"track:{track_name} artist:{artist_name}", type="track", limit=1)
        if results["tracks"]["items"]:
            return results["tracks"]["items"][0]["album"]["images"][0]["url"]
        else:
            print(f"No match found on Spotify for {track_name} by {artist_name}.")
    except Exception as e:
        print(f"Error fetching album art from Spotify: {e}")
    return None

def get_spotify_track_info(track_name, artist_name):
    """Retrieve the track's external URL and preview URL from Spotify."""
    spotify = authenticate_spotify()
    try:
        results = spotify.search(q=f"track:{track_name} artist:{artist_name}", type="track", limit=1)
        if results["tracks"]["items"]:
            item = results["tracks"]["items"][0]
            return {
                "url": item["external_urls"]["spotify"],
                "preview_url": item.get("preview_url")
            }
    except Exception as e:
        print(f"Error fetching Spotify track info: {e}")
    return None

def fetch_top_tracks(username, period="12month", limit=10):
    params = {
        "method": "user.gettoptracks",
        "user": username,
        "api_key": API_KEY,
        "format": "json",
        "period": period,
        "limit": limit,
    }
    response = requests.get(BASE_URL, params=params)
    if response.status_code != 200:
        return None
    data = response.json()
    if "toptracks" in data and "track" in data["toptracks"]:
        tracks = []
        for track in data["toptracks"]["track"]:
            track_name = track["name"]
            artist_name = track["artist"]["name"]
            print(f"Fetching album art for {track_name} by {artist_name}.")
            # Try Last.fm image first
            image_url = next((img["#text"] for img in track["image"] if img["#text"]), None)
            if not image_url or "2a96cbd8b46e442fc41c2b86b821562f" in image_url:
                image_url = fetch_album_art_from_spotify(track_name, artist_name)
            if not image_url:
                continue
            tracks.append({
                "name": track_name,
                "artist": artist_name,
                "playcount": int(track["playcount"]),
                "url": track["url"],
                "image_url": image_url,
            })
        return tracks
    return None

def calculate_average_rgb(image_url):
    try:
        response = requests.get(image_url)
        if response.status_code == 200:
            image = Image.open(BytesIO(response.content)).convert("RGB")
            pixels = list(image.getdata())
            num_pixels = len(pixels)
            avg_r = sum(pixel[0] for pixel in pixels) // num_pixels
            avg_g = sum(pixel[1] for pixel in pixels) // num_pixels
            avg_b = sum(pixel[2] for pixel in pixels) // num_pixels
            print(f"Image URL: {image_url} -> Average RGB: ({avg_r}, {avg_g}, {avg_b})")
            return avg_r, avg_g, avg_b
    except Exception as e:
        print(f"Error processing image: {e}")
    return None

def find_closest_color(user_color, track_colors):
    def color_distance(c1, c2):
        return sum(abs(a - b) for a, b in zip(c1, c2))
    closest_track = None
    smallest_distance = float("inf")
    for track in track_colors:
        distance = color_distance(user_color, track["rgb"])
        print(f"Track: {track['name']} by {track['artist']} -> RGB: {track['rgb']} -> Distance: {distance}")
        if distance < smallest_distance or (distance == smallest_distance and track["playcount"] > (closest_track["playcount"] if closest_track else 0)):
            smallest_distance = distance
            closest_track = track
    return closest_track

# -----------------------------
# Global Cache for Track Data and Leaderboard
# -----------------------------
cached_query = {
    "username": None,
    "period": None,
    "limit": None,
    "tracks": None
}

leaderboard = []  # Each element: {"name": ..., "artist": ..., "image_url": ..., "username": ...}

def update_leaderboard(song):
    global leaderboard
    leaderboard.insert(0, song)
    if len(leaderboard) > 10:
        leaderboard.pop()

# -----------------------------
# Flask App with Rate Limiting
# -----------------------------
app = Flask(__name__)
limiter = Limiter(app, key_func=get_remote_address, default_limits=["10 per minute"])

class API:
    def fetch_and_match(self, username, period, limit, r, g, b):
        selected_color = (int(r), int(g), int(b))
        print(f"Received: username={username}, period={period}, limit={limit}, color={selected_color}")
        global cached_query
        if (cached_query["username"] == username and 
            cached_query["period"] == period and 
            cached_query["limit"] == limit and 
            cached_query["tracks"] is not None):
            print("Using cached tracks.")
            tracks = cached_query["tracks"]
        else:
            tracks = fetch_top_tracks(username, period, int(limit))
            cached_query = {
                "username": username,
                "period": period,
                "limit": limit,
                "tracks": tracks
            }
        if not tracks:
            return {"error": "failed to fetch tracks. check your username"}
        track_colors = []
        for track in tracks:
            rgb = calculate_average_rgb(track["image_url"])
            if rgb:
                track_colors.append({
                    "name": track["name"],
                    "artist": track["artist"],
                    "rgb": rgb,
                    "image_url": track["image_url"],
                    "playcount": track["playcount"],
                    "url": track["url"]
                })
        if not track_colors:
            return {"error": "failed to process album covers."}
        closest = find_closest_color(selected_color, track_colors)
        if closest:
            spotify_info = get_spotify_track_info(closest["name"], closest["artist"])
            update_leaderboard({
                "name": closest["name"],
                "artist": closest["artist"],
                "image_url": closest["image_url"],
                "username": username
            })
            return {
                "name": closest["name"],
                "artist": closest["artist"],
                "rgb": closest["rgb"],
                "image_url": closest["image_url"],
                "lastfm_url": closest["url"],
                "spotify_url": spotify_info["url"] if spotify_info else "not available",
                "preview_url": spotify_info["preview_url"] if spotify_info and spotify_info["preview_url"] else "",
                "leaderboard": leaderboard
            }
        else:
            return {"error": "no matching track found."}

@app.route("/api/fetch_and_match", methods=["POST"])
@limiter.limit("10 per minute")
def fetch_and_match_route():
    data = request.get_json()
    username = data.get("username")
    period = data.get("period", "12month")
    limit = data.get("limit", 25)
    r = data.get("r")
    g = data.get("g")
    b = data.get("b")
    result = API().fetch_and_match(username, period, limit, r, g, b)
    return jsonify(result)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
