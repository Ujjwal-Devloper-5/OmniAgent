"""
Weather information tool using OpenWeatherMap API with Open-Meteo fallback.
If no API key is configured or API fails, uses keyless Open-Meteo fallback.
"""

from __future__ import annotations

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)


async def _open_meteo_weather(location: str) -> str | None:
    """
    Keyless weather fallback using Open-Meteo + Nominatim geocoding.
    Both APIs are completely free with no API key required.
    """
    import httpx
    
    timeout = httpx.Timeout(8.0)
    headers = {"User-Agent": "OmniAgent/2.0 weather lookup"}
    
    try:
        # Step 1: Geocode location name to lat/lon using Nominatim
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            geo = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": location, "format": "json", "limit": 1},
            )
            geo_data = geo.json()
        
        if not geo_data:
            return None
        
        lat = float(geo_data[0]["lat"])
        lon = float(geo_data[0]["lon"])
        display_name = geo_data[0].get("display_name", location)
        
        # Step 2: Fetch weather from Open-Meteo (no key!)
        async with httpx.AsyncClient(timeout=timeout) as client:
            weather = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "temperature_unit": "celsius",
                    "wind_speed_unit": "kmh",
                    "timezone": "auto",
                },
            )
            w = weather.json()
        
        current = w.get("current", {})
        temp = current.get("temperature_2m", "N/A")
        humidity = current.get("relative_humidity_2m", "N/A")
        wind = current.get("wind_speed_10m", "N/A")
        code = current.get("weather_code", 0)
        
        # WMO weather code to description
        WMO_CODES = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Foggy", 48: "Icy fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
            80: "Slight showers", 81: "Moderate showers", 82: "Violent showers",
            95: "Thunderstorm", 96: "Thunderstorm with hail",
        }
        condition = WMO_CODES.get(code, f"Code {code}")
        
        return (
            f"🌤️ **Weather for {display_name.split(',')[0]}**\n"
            f"  Condition:   {condition}\n"
            f"  Temperature: {temp}°C\n"
            f"  Humidity:    {humidity}%\n"
            f"  Wind:        {wind} km/h\n"
            f"  *(Source: Open-Meteo — live data)*"
        )
    except Exception as exc:
        log.debug("Open-Meteo fallback failed: %s", exc)
        return None


@tool
async def get_weather(city: str) -> str:
    """
    Get current weather conditions for a city.
    Returns temperature, humidity, wind speed, and a description.
    Tries OPENWEATHERMAP_API_KEY if configured, else falls back to free API.

    Args:
        city: City name (e.g. "London", "New York", "Tokyo").

    Returns:
        Weather information or a message if both APIs fail.
    """
    from config import settings
    import httpx

    # Attempt OpenWeatherMap if key exists
    if settings.openweathermap_api_key:
        log.debug("Weather lookup for: %s", city)
        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": city,
                "appid": settings.openweathermap_api_key,
                "units": "metric",
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            weather = data["weather"][0]["description"].capitalize()
            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            humidity = data["main"]["humidity"]
            wind_speed = data["wind"]["speed"]
            city_name = data["name"]
            country = data["sys"]["country"]

            return (
                f"Weather in {city_name}, {country}:\n"
                f"  Conditions : {weather}\n"
                f"  Temperature: {temp:.1f}\u00b0C (feels like {feels_like:.1f}\u00b0C)\n"
                f"  Humidity   : {humidity}%\n"
                f"  Wind Speed : {wind_speed} m/s"
            )
        except Exception as exc:
            log.error("Weather tool error for '%s': %s", city, exc)
            # Fall through to open_meteo fallback

    # Fallback to Open-Meteo
    log.debug("Using Open-Meteo fallback for: %s", city)
    fallback_result = await _open_meteo_weather(city)
    if fallback_result:
        return fallback_result

    return f"Could not retrieve weather for '{city}'. Both primary and fallback APIs failed."
