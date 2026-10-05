""" KMA neighborhood forecast grid (DFS) coordinate conversion. """
 
from __future__ import annotations
 
import math
from typing import NamedTuple
 
# KMA grid parameters
RE = 6371.00877  # Earth radius (km)
GRID = 5.0  # Grid spacing (km)
SLAT1 = 30.0  # Standard latitude 1
SLAT2 = 60.0  # Standard latitude 2
OLON = 126.0  # Reference point longitude
OLAT = 38.0  # Reference point latitude
XO = 43  # Reference point X coordinate (grid)
YO = 136  # Reference point Y coordinate (grid)
 
_DEGRAD = math.pi / 180.0
 
 
class Grid(NamedTuple):
    nx: int
    ny: int
 
 
def _projection_constants() -> tuple[float, float, float, float]:
    re = RE / GRID
    slat1 = SLAT1 * _DEGRAD
    slat2 = SLAT2 * _DEGRAD
    olat = OLAT * _DEGRAD
 
    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = math.pow(sf, sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / math.pow(ro, sn)
    return re, sn, sf, ro
 
 
_RE, _SN, _SF, _RO = _projection_constants()
 
 
def latlon_to_grid(latitude: float, longitude: float) -> Grid:
    """Convert latitude/longitude to KMA grid indices. """
    ra = math.tan(math.pi * 0.25 + latitude * _DEGRAD * 0.5)
    ra = _RE * _SF / math.pow(ra, _SN)
 
    theta = longitude * _DEGRAD - OLON * _DEGRAD
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= _SN
 
    nx = int(math.floor(ra * math.sin(theta) + XO + 0.5))
    ny = int(math.floor(_RO - ra * math.cos(theta) + YO + 0.5))
    return Grid(nx, ny)
 
 
def grid_to_latlon(nx: int, ny: int) -> tuple[float, float]:
    """Convert grid indices back to the latitude/longitude of the grid center (for debugging/display)."""
    xn = nx - XO
    yn = _RO - ny + YO
    ra = math.sqrt(xn * xn + yn * yn)
    if _SN < 0.0:
        ra = -ra
 
    alat = math.pow((_RE * _SF / ra), (1.0 / _SN))
    alat = 2.0 * math.atan(alat) - math.pi * 0.5
 
    if abs(xn) <= 0.0:
        theta = 0.0
    elif abs(yn) <= 0.0:
        theta = math.pi * 0.5
        if xn < 0.0:
            theta = -theta
    else:
        theta = math.atan2(xn, yn)
 
    alon = theta / _SN + OLON * _DEGRAD
    return alat / _DEGRAD, alon / _DEGRAD
 