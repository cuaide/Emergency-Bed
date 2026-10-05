"""기상청 동네예보 격자(DFS) 좌표 변환.

기상청 단기예보 API는 위경도가 아니라 5km 격자 번호(nx, ny)를 받는다.
Lambert Conformal Conic 투영을 쓰는 기상청 공식 변환식을 그대로 옮긴 것이다.
"""

from __future__ import annotations

import math
from typing import NamedTuple

# 기상청 격자 파라미터
RE = 6371.00877  # 지구 반경(km)
GRID = 5.0  # 격자 간격(km)
SLAT1 = 30.0  # 표준 위도 1
SLAT2 = 60.0  # 표준 위도 2
OLON = 126.0  # 기준점 경도
OLAT = 38.0  # 기준점 위도
XO = 43  # 기준점 X좌표(격자)
YO = 136  # 기준점 Y좌표(격자)

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
    """위경도를 기상청 격자 번호로 변환한다.

    >>> latlon_to_grid(37.5665, 126.9780)   # 서울시청
    Grid(nx=60, ny=127)
    """
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
    """격자 번호를 격자 중심의 위경도로 되돌린다 (디버깅/표시용)."""
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
