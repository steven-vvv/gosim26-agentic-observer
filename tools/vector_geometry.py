"""NumPy geometry samples for many catalog tiles at one instant."""

from __future__ import annotations

import math

import numpy as np

FIELDS = (
    "altitude_deg", "azimuth_deg", "hour_angle_deg", "airmass",
    "moon_separation_deg", "lunar_quality_factor",
)


class BatchGeometry:
    def __init__(self, simulator) -> None:
        tiles = list(simulator.tiles.values())
        self.tiles = tiles
        self.index = {id(tile): i for i, tile in enumerate(tiles)}
        self.ra = np.array([tile.ra_deg for tile in tiles], dtype=np.float64)
        self.ra_radians = np.radians(self.ra)
        declination = np.radians([tile.dec_deg for tile in tiles])
        self.sin_declination = np.sin(declination)
        self.cos_declination = np.cos(declination)
        latitude = math.radians(float(simulator.calendar_config["site"]["latitude_deg"]))
        self.sin_latitude = math.sin(latitude)
        self.cos_latitude = math.cos(latitude)
        self.zenith_airmass = 1.0 / (1.0 + 0.50572 * 96.07995**-1.6364)

    def at(self, terms: tuple[float, float, float, float, float]) -> tuple[np.ndarray, ...]:
        sidereal, moon_ra, moon_dec, lunar_scale, decay_scale = terms
        hour_angle_deg = (sidereal - self.ra + 180.0) % 360.0 - 180.0
        hour_angle = np.radians(hour_angle_deg)
        sin_altitude = (
            self.sin_latitude * self.sin_declination
            + self.cos_latitude * self.cos_declination * np.cos(hour_angle)
        )
        altitude = np.arcsin(np.clip(sin_altitude, -1.0, 1.0))
        cos_altitude = np.maximum(1e-12, np.cos(altitude))
        sin_azimuth = -np.sin(hour_angle) * self.cos_declination / cos_altitude
        cos_azimuth = (
            self.sin_declination - np.sin(altitude) * self.sin_latitude
        ) / (cos_altitude * max(1e-12, self.cos_latitude))
        azimuth_deg = np.degrees(np.arctan2(sin_azimuth, cos_azimuth)) % 360.0
        altitude_deg = np.degrees(altitude)

        airmass = np.full(len(self.tiles), np.inf)
        visible = altitude_deg > 0.0
        zenith_deg = 90.0 - altitude_deg[visible]
        raw = 1.0 / (
            np.cos(np.radians(zenith_deg))
            + 0.50572 * np.power(96.07995 - zenith_deg, -1.6364)
        )
        airmass[visible] = raw / self.zenith_airmass

        moon_ra_radians = math.radians(moon_ra)
        moon_dec_radians = math.radians(moon_dec)
        cosine = (
            self.sin_declination * math.sin(moon_dec_radians)
            + self.cos_declination * math.cos(moon_dec_radians)
            * np.cos(self.ra_radians - moon_ra_radians)
        )
        moon_separation_deg = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
        lunar_quality = np.clip(
            1.0 - lunar_scale * np.exp(-moon_separation_deg / decay_scale), 0.0, 1.0
        )
        return altitude_deg, azimuth_deg, hour_angle_deg, airmass, moon_separation_deg, lunar_quality

    @staticmethod
    def sample(values: tuple[np.ndarray, ...], index: int) -> dict[str, float]:
        return {name: float(column[index]) for name, column in zip(FIELDS, values)}
