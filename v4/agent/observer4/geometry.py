"""Vectorized public sky and fiber geometry, matching the pinned v4 engine."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

RAD = math.pi / 180.0
SIDEREAL_RATE = 360.98564736629 / 86400.0


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sidereal(timestamp: float, longitude: float) -> float:
    days = timestamp / 86400.0 + 2440587.5 - 2451545.0
    return (280.46061837 + 360.98564736629 * days + longitude) % 360.0


def wrap180(value):
    return (value + 180.0) % 360.0 - 180.0


def equatorial_vectors(ra, dec):
    ra, dec = np.asarray(ra) * RAD, np.asarray(dec) * RAD
    return np.stack((np.cos(dec) * np.cos(ra), np.cos(dec) * np.sin(ra), np.sin(dec)), axis=-1)


def horizontal_vectors(ra, dec, lst: float, latitude: float):
    ha = wrap180(lst - np.asarray(ra)) * RAD
    dec, lat = np.asarray(dec) * RAD, latitude * RAD
    sd, cd, sh, ch = np.sin(dec), np.cos(dec), np.sin(ha), np.cos(ha)
    return np.stack(
        (math.cos(lat) * sd - math.sin(lat) * cd * ch, -cd * sh, math.sin(lat) * sd + math.cos(lat) * cd * ch), axis=-1
    )


def altaz(vectors):
    vectors = np.asarray(vectors)
    return np.arcsin(np.clip(vectors[..., 2], -1, 1)) / RAD, np.arctan2(vectors[..., 1], vectors[..., 0]) / RAD % 360.0


class HorizonFactors:
    """horizontal_vectors for fixed targets with the hour-angle-free products formed once.

    The products of the declination and latitude terms are computed exactly as in
    horizontal_vectors and in the same order, and every operation is elementwise, so
    vectors() and altitude() equal horizontal_vectors and altaz bit for bit, also for a
    subset taken with take().
    """

    def __init__(self, ra, dec, latitude: float):
        dec, lat = np.asarray(dec) * RAD, latitude * RAD
        sd, cd = np.sin(dec), np.cos(dec)
        self.ra = np.asarray(ra)
        self.terms = (math.cos(lat) * sd, math.sin(lat) * cd, -cd, math.sin(lat) * sd, math.cos(lat) * cd)

    def take(self, indices) -> HorizonFactors:
        part = object.__new__(HorizonFactors)
        part.ra = self.ra[indices]
        part.terms = tuple(term[indices] for term in self.terms)
        return part

    def vectors(self, lst: float, ha=None):
        """horizontal_vectors(ra, dec, lst, latitude); ``ha`` may pass wrap180(lst - ra) in degrees."""
        ha = (wrap180(lst - self.ra) if ha is None else ha) * RAD
        x_sd, x_cd, y_cd, z_sd, z_cd = self.terms
        sh, ch = np.sin(ha), np.cos(ha)
        return np.stack((x_sd - x_cd * ch, y_cd * sh, z_sd + z_cd * ch), axis=-1)

    def altitude(self, lst: float):
        """altaz(horizontal_vectors(...))[0] without the azimuth terms."""
        ha = wrap180(lst - self.ra) * RAD
        return np.arcsin(np.clip(self.terms[3] + self.terms[4] * np.cos(ha), -1, 1)) / RAD


def to_equatorial(alt: float, az: float, lst: float, latitude: float) -> tuple[float, float]:
    a, z, lat = alt * RAD, az * RAD, latitude * RAD
    dec = math.asin(max(-1.0, min(1.0, math.sin(a) * math.sin(lat) + math.cos(a) * math.cos(lat) * math.cos(z))))
    cd = max(1e-12, math.cos(dec))
    sh = -math.sin(z) * math.cos(a) / cd
    ch = (math.sin(a) - math.sin(dec) * math.sin(lat)) / (cd * max(1e-12, math.cos(lat)))
    return (lst - math.atan2(sh, ch) / RAD) % 360.0, dec / RAD


def max_hour_angle(dec, latitude: float, minimum: float):
    dec, lat = np.asarray(dec) * RAD, latitude * RAD
    denominator = np.cos(dec) * math.cos(lat)
    numerator = math.sin(minimum * RAD) - np.sin(dec) * math.sin(lat)
    value = numerator / np.where(np.abs(denominator) < 1e-12, 1.0, denominator)
    hmax = np.arccos(np.clip(value, -1, 1)) / RAD
    return np.where(np.abs(denominator) < 1e-12, np.where(numerator <= 0, 180.0, 0.0), hmax)


def airmass(alt):
    alt = np.asarray(alt)
    zenith = 90.0 - np.clip(alt, 0.01, 90.0)
    raw = 1.0 / (np.cos(zenith * RAD) + 0.50572 * (96.07995 - zenith) ** -1.6364)
    return raw * (1.0 + 0.50572 * 96.07995**-1.6364)


def moon_equatorial(timestamp: float) -> tuple[float, float]:
    days = timestamp / 86400.0 + 2440587.5 - 2451545.0
    ml = ((218.316 + 13.176396 * days) % 360.0) * RAD
    anomaly = ((134.963 + 13.064993 * days) % 360.0) * RAD
    argument = ((93.272 + 13.229350 * days) % 360.0) * RAD
    lon, lat = ml + 6.289 * RAD * math.sin(anomaly), 5.128 * RAD * math.sin(argument)
    ob = (23.439 - 0.0000004 * days) * RAD
    x = math.cos(lon) * math.cos(lat)
    y = math.sin(lon) * math.cos(lat) * math.cos(ob) - math.sin(lat) * math.sin(ob)
    z = math.sin(lon) * math.cos(lat) * math.sin(ob) + math.sin(lat) * math.cos(ob)
    return math.atan2(y, x) / RAD % 360.0, math.asin(z) / RAD


def sun_equatorial(timestamp: float) -> tuple[float, float]:
    days = timestamp / 86400.0 + 2440587.5 - 2451545.0
    ml = (280.460 + 0.9856474 * days) % 360.0
    anomaly = ((357.528 + 0.9856003 * days) % 360.0) * RAD
    lon = (ml + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly)) * RAD
    ob = (23.439 - 0.0000004 * days) * RAD
    return math.atan2(math.cos(ob) * math.sin(lon), math.cos(lon)) / RAD % 360.0, math.asin(math.sin(ob) * math.sin(lon)) / RAD


def lunar_factors(ra, dec, timestamp: float, latitude: float, longitude: float, model: dict | None,
                  vectors=None):
    """Lunar sky factor of each target.

    ``vectors`` may pass equatorial_vectors(ra, dec) computed earlier (ra and dec are then
    unused): the same array enters the same matrix product.
    """
    shape = np.shape(ra) if vectors is None else np.shape(vectors)[:-1]
    if not model:
        return np.ones(shape)
    mra, mdec = moon_equatorial(timestamp)
    sra, sdec = sun_equatorial(timestamp)
    moon = equatorial_vectors(mra, mdec)
    sun = equatorial_vectors(sra, sdec)
    illumination = (1.0 - float(np.clip(moon @ sun, -1, 1))) / 2.0
    malt = float(altaz(horizontal_vectors(mra, mdec, sidereal(timestamp, longitude), latitude))[0])
    if malt <= 0:
        return np.ones(shape)
    separation = np.arccos(np.clip((equatorial_vectors(ra, dec) if vectors is None else vectors) @ moon, -1, 1)) / RAD
    penalty = float(model["maximum_penalty"]) * illumination * math.sin(malt * RAD) ** float(model["altitude_exponent"])
    return np.clip(1.0 - penalty * np.exp(-separation / float(model["angular_decay_scale_deg"])), 0, 1)


class FiberGrid:
    def __init__(self, instrument: dict):
        self.side = int(instrument["grid_side"])
        self.n = int(instrument["n_fibers"])
        if self.side < 1 or self.side * self.side != self.n:
            raise ValueError("invalid fiber grid")
        self.glass = math.sqrt(float(instrument["fiber_area_deg2"]))
        self.pitch = self.glass + float(instrument["gap_deg"])
        self.fov = self.side * self.pitch
        ids = np.arange(self.n)
        middle = (self.side - 1) / 2.0
        self.north = (ids // self.side - middle) * self.pitch
        self.east = (ids % self.side - middle) * self.pitch

    def centers_for_target(self, alt: float, az: float):
        # Invert the gnomonic projection with the required alt/az orientation.
        dn, de = self.north * RAD, self.east * RAD
        sine = math.sin(alt * RAD) * np.sqrt(1.0 + dn * dn + de * de) / np.sqrt(1.0 + dn * dn)
        ca = np.arcsin(np.clip(sine, -1, 1)) - np.arctan(dn)
        cz = az * RAD - np.arctan2(de, np.cos(ca) - dn * np.sin(ca))
        valid = (np.abs(sine) <= 1) & (ca >= 0) & (ca <= math.pi / 2)
        return ca[valid] / RAD, (cz[valid] / RAD) % 360.0

    def classify(self, vectors, center_alt, center_az):
        """Fibre, glass margin and tangent offsets of every (centre, target) pair.

        Depth, north and east each take their own matrix product, with the operand shapes of
        the plain formulation: BLAS may round a stacked product differently on some CPUs
        (the kernel depends on the matrix shape), which would move targets across a fibre
        edge. The other arithmetic is that formulation's elementwise sequence, in place and
        without repeated trigonometry.
        """
        ca, cz = np.asarray(center_alt) * RAD, np.asarray(center_az) * RAD
        sa, co_a, sz, co_z = np.sin(ca), np.cos(ca), np.sin(cz), np.cos(cz)
        center, north, east = (np.empty(ca.shape + (3,)) for _ in range(3))
        np.multiply(co_a, co_z, out=center[..., 0])
        np.multiply(co_a, sz, out=center[..., 1])
        center[..., 2] = sa
        np.multiply(-sa, co_z, out=north[..., 0])
        np.multiply(-sa, sz, out=north[..., 1])
        north[..., 2] = co_a
        east[..., 0] = -sz
        east[..., 1] = co_z
        east[..., 2] = 0.0
        target = np.asarray(vectors).T
        depth = center @ target
        positive = depth > 0
        divisor = np.where(positive, depth, 1.0)
        dn = north @ target
        dn /= divisor
        dn /= RAD
        de = east @ target
        de /= divisor
        de /= RAD
        side, half = self.side, self.side / 2
        cells = []
        for offset in (dn, de):
            cell = offset / self.pitch
            cell += half
            np.floor(cell, out=cell)
            np.maximum(cell, 0, out=cell)
            np.minimum(cell, side - 1, out=cell)
            cells.append(cell.astype(np.int32))
        fiber = cells[0] * side + cells[1]
        error_n = dn - self.north.take(fiber)
        np.abs(error_n, out=error_n)
        error_e = de - self.east.take(fiber)
        np.abs(error_e, out=error_e)
        np.maximum(error_n, error_e, out=error_n)
        margin = self.glass / 2 - error_n
        limit = self.fov / 2
        valid = positive
        valid &= np.abs(dn) <= limit
        valid &= np.abs(de) <= limit
        valid &= margin >= 0
        return np.where(valid, fiber, -1), margin, dn, de
