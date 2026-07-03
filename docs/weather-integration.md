# Weather plugin (`get_weather`)

## Overview

`get_weather` lets the robot answer weather questions by voice — both for the
device's current location and for any city the user names (e.g. *"thời tiết hôm
nay"*, *"thời tiết Hà Nội"*, *"Đà Nẵng có mưa không"*). It returns the current
conditions plus a 7-day forecast, which Gemini Live then narrates back to the
child in their language.

## Backend: Open-Meteo (keyless)

The plugin is backed by [Open-Meteo](https://open-meteo.com), which is **free
and requires no API key**. Two keyless JSON calls are made per lookup:

1. **Geocoding** — `https://geocoding-api.open-meteo.com/v1/search` resolves a
   place name → latitude/longitude (global coverage, including Vietnam).
2. **Forecast** — `https://api.open-meteo.com/v1/forecast` returns current
   conditions + a 7-day daily forecast.

There is **no secret to provision**. The robot-server only needs outbound HTTPS
to `*.open-meteo.com`. Results are cached (per location + language) to avoid
repeated calls.

> Migrated from the previous QWeather + HTML-scraping implementation, which
> required a paid-ish API key (empty by default → every call failed) and
> scraped a Chinese weather page. Open-Meteo removes the key dependency and the
> fragile scraping entirely.

## Configuration

The only optional setting is `default_location`, used when the user does not
name a city **and** the device IP cannot be geolocated:

```yaml
plugins:
  get_weather:
    default_location: "Ho Chi Minh City"
```

Enable the tool by listing it under the active intent's `functions` (already the
case in `config.yaml` under `Intent.function_call.functions`):

```yaml
Intent:
  function_call:
    functions:
      - get_weather
      # ...
```

## How it reaches the model

`get_weather` is registered via `@register_function("get_weather", …,
ToolType.SYSTEM_CTL)` (`plugins_func/functions/get_weather.py`) and is **not** in
`GoogleLiveProvider._LIVE_INCOMPATIBLE_TOOLS`, so it is exposed to Gemini Live
as a callable tool. When the model calls it, the server runs the lookup and
returns the report via `ActionResponse(Action.REQLLM, report, None)` so the
model speaks the answer.

Local weather is also injected into the system prompt when the active prompt
template (`agent-base-prompt.txt`) contains the `{{weather_info}}` placeholder
and the device IP geolocates to a city (`core/utils/prompt_manager.py`).
