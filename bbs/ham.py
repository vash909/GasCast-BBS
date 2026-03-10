"""Ham radio helper services for GasCast."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


HF_BANDS = {
    "160m": (1.8, 2.0),
    "80m": (3.5, 3.8),
    "40m": (7.0, 7.2),
    "30m": (10.1, 10.15),
    "20m": (14.0, 14.35),
    "17m": (18.068, 18.168),
    "15m": (21.0, 21.45),
    "12m": (24.89, 24.99),
    "10m": (28.0, 29.7),
}

Q_CODES = {
    "QRM": "Interference from other stations.",
    "QRN": "Atmospheric noise / natural static.",
    "QRP": "I am transmitting at low power.",
    "QRO": "I am transmitting at high power.",
    "QTH": "My location is...",
    "QSL": "Reception confirmed / QSL card.",
    "QRZ": "Who is calling me?",
    "QSY": "Changing frequency.",
}


@dataclass
class PropagationForecast:
    band: str
    score: int
    condition: str
    recommendation: str


class HamService:
    """Simple propagation model usable offline.

    It estimates quality from UTC hour, month and selected band.
    """

    def propagation(self, band: str) -> PropagationForecast:
        band_key = band.lower()
        if band_key not in HF_BANDS:
            raise ValueError("Unsupported band")

        now = datetime.now(timezone.utc)
        utc_hour = now.hour
        month = now.month

        base = {
            "160m": 50,
            "80m": 58,
            "40m": 70,
            "30m": 68,
            "20m": 74,
            "17m": 72,
            "15m": 62,
            "12m": 55,
            "10m": 48,
        }[band_key]

        daytime_boost = 0
        if 6 <= utc_hour <= 18:
            if band_key in {"20m", "17m", "15m", "12m", "10m"}:
                daytime_boost = 12
            else:
                daytime_boost = -8
        else:
            if band_key in {"160m", "80m", "40m"}:
                daytime_boost = 14
            else:
                daytime_boost = -10

        seasonal = 6 if month in {3, 4, 5, 9, 10} else -2
        score = max(10, min(98, base + daytime_boost + seasonal))

        if score >= 80:
            condition = "Excellent"
            rec = "Long openings and DX paths are likely."
        elif score >= 65:
            condition = "Good"
            rec = "Stable traffic, good chance for national/international QSOs."
        elif score >= 50:
            condition = "Fair"
            rec = "QSOs possible with patience and an efficient antenna."
        else:
            condition = "Weak"
            rec = "Prefer NVIS or try an alternate band."

        return PropagationForecast(
            band=band_key,
            score=score,
            condition=condition,
            recommendation=rec,
        )

    def bands_table(self) -> list[str]:
        lines = []
        for band, (low, high) in HF_BANDS.items():
            lines.append(f"{band:>4} : {low:>6.3f} - {high:>6.3f} MHz")
        return lines

    def qcode(self, code: str) -> str | None:
        return Q_CODES.get(code.upper())

    def grayline_tip(self) -> str:
        now = datetime.now(timezone.utc)
        hour = now.hour
        if 4 <= hour <= 8:
            return "UTC sunrise window: try 40m and 20m for opening DX paths."
        if 16 <= hour <= 20:
            return "UTC sunset window: watch for long-path openings on 20m/17m."
        return "Outside the main grayline window: use 40m at night or 20m daytime."

    def solar_snapshot(self) -> list[str]:
        now = datetime.now(timezone.utc)
        pseudo_sfi = 110 + ((now.timetuple().tm_yday * 3) % 35)
        pseudo_k = 2 + (now.hour % 3)
        pseudo_a = 8 + (now.day % 15)
        return [
            f"UTC: {now.strftime('%Y-%m-%d %H:%M')}",
            f"Estimated SFI: {pseudo_sfi}",
            f"Estimated K-index: {pseudo_k}",
            f"Estimated A-index: {pseudo_a}",
            "Note: offline estimated values, not a replacement for official bulletins.",
        ]
