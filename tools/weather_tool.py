"""
Weather information tool using OpenWeatherMap API.
If no API key is configured, returns a helpful message.
"""

from __future__ import annotations

from langchain_core.tools import tool

from core.logger import get_logger

log = get_logger(__name__)


@tool
def get_weather(city: str) -> str:
    """
    Get current weather conditions for a city.
    Returns temperature, humidity, wind speed, and a description.
    Requires OPENWEATHERMAP_API_KEY to be set in .env.

    Args:
        city: City name (e.g. "London", "New York", "Tokyo").

    Returns:
        Weather information or a message if the API key is not configured.
    """
    from config import settings

    if not settings.openweathermap_api_key:
        return (
            "Weather tool is not configured. "
            "Please set OPENWEATHERMAP_API_KEY in your .env file. "
            f"You can get a free API key at https://openweathermap.org/api"
        )

    log.debug("Weather lookup for: %s", city)
    try:
        import httpx

        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": settings.openweathermap_api_key,
            "units": "metric",
        }
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, params=params)
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
        return f"Could not retrieve weather for '{city}': {exc}"
